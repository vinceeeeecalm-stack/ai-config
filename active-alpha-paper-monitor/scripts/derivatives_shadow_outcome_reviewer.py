#!/usr/bin/env python3
"""Settle due derivatives-shadow observations with closed, no-lookahead spot bars."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from impulse_capture_scanner import fetch_json, summarize_klines


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "derivatives_shadow_outcome_review_v1.json"
EXPECTED_THRESHOLDS = [
    ("target_5_stop_3", 5.0, 3.0),
    ("target_8_stop_4", 8.0, 4.0),
    ("target_10_stop_5", 10.0, 5.0),
]
SAFETY_FLAGS = {
    "production_rule_changed": False,
    "formal_action_eligible": False,
    "paper_roi_eligible": False,
    "real_money_roi_eligible": False,
    "business_ready_eligible": False,
    "live_orders_enabled": False,
    "private_api_used": False,
    "human_confirmation_required": True,
}


class ShadowOutcomeError(ValueError):
    """Raised when frozen shadow-outcome evidence is invalid."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ShadowOutcomeError(f"missing:{field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ShadowOutcomeError(f"invalid_time:{field}") from exc
    if parsed.tzinfo is None:
        raise ShadowOutcomeError(f"timezone_required:{field}")
    return parsed.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ShadowOutcomeError(f"json_object_required:{path}")
    return payload


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ShadowOutcomeError(f"invalid_jsonl:{path}:{line_number}") from exc
        if not isinstance(item, dict):
            raise ShadowOutcomeError(f"jsonl_object_required:{path}:{line_number}")
        records.append(item)
    return records


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("schema_version") != "DerivativesShadowOutcomeReviewConfigV1":
        raise ShadowOutcomeError("schema:DerivativesShadowOutcomeReviewConfigV1")
    if config.get("reviewer_version") != "derivatives-shadow-outcome-review-v1":
        raise ShadowOutcomeError("reviewer_version_mismatch")
    if config.get("bar_interval") != "15m" or config.get("bar_interval_seconds") != 900:
        raise ShadowOutcomeError("only_closed_15m_review_supported")
    timeout = config.get("maximum_source_timeout_seconds")
    workers = config.get("maximum_workers")
    if not isinstance(timeout, (int, float)) or not 0 < float(timeout) <= 12:
        raise ShadowOutcomeError("source_timeout_out_of_range")
    if not isinstance(workers, int) or not 1 <= workers <= 8:
        raise ShadowOutcomeError("maximum_workers_out_of_range")
    rows = config.get("threshold_pairs")
    if not isinstance(rows, list):
        raise ShadowOutcomeError("threshold_pairs_required")
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            raise ShadowOutcomeError("threshold_pair_object_required")
        normalized.append((row.get("id"), float(row.get("target_return_pct")), float(row.get("stop_loss_pct"))))
    if normalized != EXPECTED_THRESHOLDS:
        raise ShadowOutcomeError("frozen_threshold_pairs_mismatch")
    if config.get("same_bar_stop_first") is not True:
        raise ShadowOutcomeError("same_bar_stop_first_required")
    for field, expected in SAFETY_FLAGS.items():
        if config.get(field) is not expected:
            raise ShadowOutcomeError(f"unsafe_config:{field}")
    return config


def validate_observation(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "DerivativesShadowObservationV1":
        raise ShadowOutcomeError("schema:DerivativesShadowObservationV1")
    for field in (
        "observation_id", "symbol", "snapshot_id", "strategy_version",
        "config_digest", "source_digest", "observed_at", "review_due_at",
        "directional_state", "production_signal_stage",
    ):
        if record.get(field) in {None, ""}:
            raise ShadowOutcomeError(f"missing:{field}")
    observed = parse_time(record["observed_at"], "observed_at")
    due = parse_time(record["review_due_at"], "review_due_at")
    if due != observed + timedelta(days=7) or record.get("evaluation_window_hours") != 168:
        raise ShadowOutcomeError("observation_review_clock_must_be_seven_days")
    for field in ("config_digest", "source_digest"):
        value = record[field]
        if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ShadowOutcomeError(f"invalid_digest:{field}")
    spot = record.get("spot_factor")
    if not isinstance(spot, dict):
        raise ShadowOutcomeError("spot_factor_required")
    price = spot.get("current_price")
    if not isinstance(price, (int, float)) or not math.isfinite(float(price)) or float(price) <= 0:
        raise ShadowOutcomeError("invalid_observation_price")
    if record.get("directional_state") not in {
        "CONFIRMED_LONG", "CROWDED_CONFLICT", "OI_ONLY_CONFLICT", "DATA_INSUFFICIENT", "NO_FUEL"
    }:
        raise ShadowOutcomeError("invalid_directional_state")
    for field, expected in {
        "production_rule_changed": False,
        "formal_action_eligible": False,
        "paper_roi_eligible": False,
        "real_money_roi_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }.items():
        if record.get(field) is not expected:
            raise ShadowOutcomeError(f"unsafe_observation:{field}")
    return record


def legacy_unreviewable(record: dict[str, Any]) -> dict[str, Any] | None:
    """Classify early append-only rows without inventing a review clock."""

    if record.get("schema_version") != "DerivativesShadowObservationV1":
        return None
    if record.get("review_due_at") and record.get("evaluation_window_hours") == 168:
        return None
    for field in ("observation_id", "symbol", "observed_at"):
        if record.get(field) in {None, ""}:
            raise ShadowOutcomeError(f"legacy_missing_identity:{field}")
    parse_time(record["observed_at"], "observed_at")
    return {
        "observation_id": record["observation_id"],
        "symbol": record["symbol"],
        "status": "LEGACY_UNREVIEWABLE",
        "reason": "frozen_review_clock_missing",
        "review_due_at_backfilled": False,
        "market_data_requested": False,
    }


def rank_band(rank: Any) -> str:
    if not isinstance(rank, int) or rank < 1 or rank > 20:
        raise ShadowOutcomeError("discovery_rank_out_of_range")
    return "TOP3" if rank <= 3 else "RANK_4_20"


def normalize_bars(
    raw_bars: list[Any],
    *,
    observed: datetime,
    due: datetime,
    cutoff: datetime,
    interval_seconds: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    observed_ms = int(observed.timestamp() * 1000)
    due_ms = int(due.timestamp() * 1000)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    usable: list[dict[str, Any]] = []
    for raw in raw_bars:
        if not isinstance(raw, dict):
            continue
        try:
            bar = {
                "open_time": int(raw["open_time"]),
                "close_time": int(raw["close_time"]),
                "high": float(raw["high"]),
                "low": float(raw["low"]),
                "close": float(raw["close"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
        if bar["open_time"] < observed_ms or bar["close_time"] > due_ms or bar["close_time"] > cutoff_ms:
            continue
        if bar["close_time"] <= bar["open_time"]:
            continue
        values = [bar["high"], bar["low"], bar["close"]]
        if not all(math.isfinite(value) and value > 0 for value in values) or bar["high"] < bar["low"]:
            continue
        usable.append(bar)
    usable.sort(key=lambda row: (row["open_time"], row["close_time"]))
    blockers: list[str] = []
    if not usable:
        return [], ["closed_post_observation_bars_missing"]
    interval_ms = interval_seconds * 1000
    if usable[0]["open_time"] - observed_ms >= interval_ms:
        blockers.append("window_start_coverage_missing")
    if due_ms - usable[-1]["close_time"] >= interval_ms:
        blockers.append("window_end_coverage_missing")
    for left, right in zip(usable, usable[1:]):
        if right["open_time"] <= left["open_time"]:
            blockers.append("bars_not_unique_chronological")
            break
        if right["open_time"] - left["open_time"] != interval_ms:
            blockers.append("bar_gap_detected")
            break
    return usable, sorted(set(blockers))


def path_outcome(start_price: float, bars: list[dict[str, Any]], pair: dict[str, Any]) -> dict[str, Any]:
    target_pct = float(pair["target_return_pct"])
    stop_pct = float(pair["stop_loss_pct"])
    target_price = start_price * (1 + target_pct / 100.0)
    stop_price = start_price * (1 - stop_pct / 100.0)
    target_index = stop_index = None
    for index, bar in enumerate(bars):
        if target_index is None and bar["high"] >= target_price:
            target_index = index
        if stop_index is None and bar["low"] <= stop_price:
            stop_index = index
        if target_index is not None and stop_index is not None:
            break
    if target_index is None and stop_index is None:
        first = "NONE"
    elif target_index is not None and stop_index is not None and target_index == stop_index:
        first = "AMBIGUOUS_STOP_FIRST"
    elif stop_index is not None and (target_index is None or stop_index < target_index):
        first = "STOP"
    else:
        first = "TARGET"
    return {
        "threshold_id": pair["id"],
        "target_return_pct": target_pct,
        "stop_loss_pct": stop_pct,
        "target_price": round(target_price, 12),
        "stop_price": round(stop_price, 12),
        "target_first_at": iso(datetime.fromtimestamp(bars[target_index]["close_time"] / 1000, timezone.utc)) if target_index is not None else None,
        "stop_first_at": iso(datetime.fromtimestamp(bars[stop_index]["close_time"] / 1000, timezone.utc)) if stop_index is not None else None,
        "first_trigger": first,
        "stop_first_conservative": True,
    }


def review_id(observation: dict[str, Any], review_config_digest: str) -> str:
    return "derivatives-review-" + sha256_json({
        "observation_id": observation["observation_id"],
        "review_due_at": observation["review_due_at"],
        "review_config_digest": review_config_digest,
    })[:20]


def blocked_review(observation: dict[str, Any], config: dict[str, Any], blocker: str, generated_at: str) -> dict[str, Any]:
    return {
        "schema_version": "DerivativesShadowOutcomeReviewV1",
        "review_id": review_id(observation, sha256_json(config)),
        "observation_id": observation["observation_id"],
        "symbol": observation["symbol"],
        "window_status": "DATA_BLOCKED",
        "blockers": [blocker],
        "generated_at": generated_at,
        "path_outcomes": [],
        **SAFETY_FLAGS,
    }


def build_review(
    observation: dict[str, Any],
    raw_bars: list[Any],
    *,
    as_of: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    observation = validate_observation(observation)
    config = validate_config(config)
    cutoff = parse_time(as_of, "as_of")
    due = parse_time(observation["review_due_at"], "review_due_at")
    observed = parse_time(observation["observed_at"], "observed_at")
    if cutoff < due:
        raise ShadowOutcomeError("observation_not_due")
    bars, blockers = normalize_bars(
        raw_bars,
        observed=observed,
        due=due,
        cutoff=cutoff,
        interval_seconds=config["bar_interval_seconds"],
    )
    if blockers:
        return blocked_review(observation, config, blockers[0], as_of)
    start_price = float(observation["spot_factor"]["current_price"])
    review_config_digest = sha256_json(config)
    maximum = max(bar["high"] for bar in bars)
    minimum = min(bar["low"] for bar in bars)
    end_price = bars[-1]["close"]
    record = {
        "schema_version": "DerivativesShadowOutcomeReviewV1",
        "review_id": review_id(observation, review_config_digest),
        "observation_id": observation["observation_id"],
        "derivatives_snapshot_id": observation["derivatives_snapshot_id"],
        "symbol": observation["symbol"],
        "discovery_rank": observation["discovery_rank"],
        "rank_band": rank_band(observation["discovery_rank"]),
        "snapshot_id": observation["snapshot_id"],
        "strategy_version": observation["strategy_version"],
        "config_digest": observation["config_digest"],
        "source_digest": observation["source_digest"],
        "reviewer_version": config["reviewer_version"],
        "review_config_digest": review_config_digest,
        "observed_at": observation["observed_at"],
        "review_due_at": observation["review_due_at"],
        "reviewed_at": observation["review_due_at"],
        "generated_at": as_of,
        "window_status": "COMPLETE",
        "bar_interval": config["bar_interval"],
        "closed_bar_count": len(bars),
        "window_start_at": iso(datetime.fromtimestamp(bars[0]["open_time"] / 1000, timezone.utc)),
        "window_end_at": iso(datetime.fromtimestamp(bars[-1]["close_time"] / 1000, timezone.utc)),
        "start_price": round(start_price, 12),
        "end_price": round(end_price, 12),
        "end_return_pct": round((end_price / start_price - 1) * 100.0, 6),
        "mfe_pct": round(max(0.0, (maximum / start_price - 1) * 100.0), 6),
        "mae_pct": round(min(0.0, (minimum / start_price - 1) * 100.0), 6),
        "directional_state_at_capture": observation["directional_state"],
        "directional_blockers_at_capture": observation.get("directional_blockers") or [],
        "production_signal_stage_at_capture": observation["production_signal_stage"],
        "production_impulse_score_at_capture": observation.get("production_impulse_score_points"),
        "path_outcomes": [path_outcome(start_price, bars, pair) for pair in config["threshold_pairs"]],
        "blockers": [],
        "evidence_ids": [
            f"binance-spot-closed-15m:{observation['symbol']}:{observation['observed_at']}:{observation['review_due_at']}"
        ],
        **SAFETY_FLAGS,
    }
    validate_review(record, observation, config)
    return record


def validate_review(record: dict[str, Any], observation: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "DerivativesShadowOutcomeReviewV1":
        raise ShadowOutcomeError("schema:DerivativesShadowOutcomeReviewV1")
    if record.get("window_status") != "COMPLETE":
        raise ShadowOutcomeError("only_complete_reviews_are_persistable")
    for field in ("observation_id", "symbol", "snapshot_id", "strategy_version", "config_digest", "source_digest"):
        if record.get(field) != observation.get(field):
            raise ShadowOutcomeError(f"review_binding_mismatch:{field}")
    if record.get("review_due_at") != observation.get("review_due_at") or record.get("reviewed_at") != observation.get("review_due_at"):
        raise ShadowOutcomeError("review_due_binding_mismatch")
    if record.get("review_config_digest") != sha256_json(validate_config(config)):
        raise ShadowOutcomeError("review_config_digest_mismatch")
    outcomes = record.get("path_outcomes")
    if not isinstance(outcomes, list) or [row.get("threshold_id") for row in outcomes] != [row[0] for row in EXPECTED_THRESHOLDS]:
        raise ShadowOutcomeError("path_outcomes_mismatch")
    if any(row.get("first_trigger") not in {"TARGET", "STOP", "NONE", "AMBIGUOUS_STOP_FIRST"} for row in outcomes):
        raise ShadowOutcomeError("invalid_first_trigger")
    for field, expected in SAFETY_FLAGS.items():
        if record.get(field) is not expected:
            raise ShadowOutcomeError(f"unsafe_review:{field}")
    return record


def append_review(path: Path, record: dict[str, Any], observation: dict[str, Any], config: dict[str, Any]) -> str:
    validate_review(record, observation, config)
    existing = {item.get("review_id"): item for item in load_records(path)}
    current = existing.get(record["review_id"])
    if current is not None:
        if canonical_json(current) != canonical_json(record):
            raise ShadowOutcomeError(f"append_only_collision:{record['review_id']}")
        return "NO_UPDATE"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(record) + "\n")
    return "APPENDED"


def summarize_cohorts(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    cohorts: dict[str, dict[str, Any]] = {}
    for review in reviews:
        if review.get("window_status") != "COMPLETE":
            continue
        keys = [
            f"directional_state:{review['directional_state_at_capture']}",
            f"production_stage:{review['production_signal_stage_at_capture']}",
            f"rank_band:{review['rank_band']}",
        ]
        for key in keys:
            cohort = cohorts.setdefault(key, {
                "sample_count": 0,
                "average_end_return_pct": 0.0,
                "threshold_outcomes": {
                    threshold_id: {"TARGET": 0, "STOP": 0, "NONE": 0, "AMBIGUOUS_STOP_FIRST": 0}
                    for threshold_id, _, _ in EXPECTED_THRESHOLDS
                },
            })
            cohort["sample_count"] += 1
            cohort["average_end_return_pct"] += review["end_return_pct"]
            for outcome in review["path_outcomes"]:
                cohort["threshold_outcomes"][outcome["threshold_id"]][outcome["first_trigger"]] += 1
    for cohort in cohorts.values():
        cohort["average_end_return_pct"] = round(cohort["average_end_return_pct"] / cohort["sample_count"], 6)
    return dict(sorted(cohorts.items()))


def review_due_observations(
    *,
    observation_ledger: Path,
    outcome_ledger: Path,
    as_of: str,
    config: dict[str, Any],
    bar_provider: Callable[[dict[str, Any], str], list[Any]],
    max_workers: int,
    write: bool,
) -> dict[str, Any]:
    config = validate_config(config)
    cutoff = parse_time(as_of, "as_of")
    raw_observations = load_records(observation_ledger)
    observations: list[dict[str, Any]] = []
    legacy_rows: list[dict[str, Any]] = []
    for item in raw_observations:
        legacy = legacy_unreviewable(item)
        if legacy is not None:
            legacy_rows.append(legacy)
            continue
        observations.append(validate_observation(item))
    observations_by_id = {item["observation_id"]: item for item in observations}
    existing_reviews = load_records(outcome_ledger)
    for record in existing_reviews:
        observation = observations_by_id.get(record.get("observation_id"))
        if observation is None:
            raise ShadowOutcomeError("outcome_without_observation")
        validate_review(record, observation, config)
    existing_ids = {item["review_id"] for item in existing_reviews}
    due = []
    not_due_count = already_reviewed_count = 0
    review_config_digest = sha256_json(config)
    for observation in sorted(observations, key=lambda item: (item["review_due_at"], item["observation_id"])):
        if parse_time(observation["review_due_at"], "review_due_at") > cutoff:
            not_due_count += 1
            continue
        if review_id(observation, review_config_digest) in existing_ids:
            already_reviewed_count += 1
            continue
        due.append(observation)

    def prepare(observation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            bars = bar_provider(observation, as_of)
        except Exception:  # source failures remain local and retryable
            review = blocked_review(observation, config, "market_data_source_failed", as_of)
        else:
            review = build_review(observation, bars, as_of=as_of, config=config)
        return observation, review

    prepared: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if due:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(due), config["maximum_workers"]))) as executor:
            prepared = list(executor.map(prepare, due))
    results = []
    appended = no_update = blocked = 0
    new_complete: list[dict[str, Any]] = []
    for observation, review in prepared:
        append_status = "DRY_RUN"
        if review["window_status"] == "DATA_BLOCKED":
            append_status = "NOT_APPENDED_RETRYABLE"
            blocked += 1
        else:
            new_complete.append(review)
            if write:
                append_status = append_review(outcome_ledger, review, observation, config)
                appended += append_status == "APPENDED"
                no_update += append_status == "NO_UPDATE"
        results.append({
            "review_id": review["review_id"],
            "observation_id": review["observation_id"],
            "symbol": review["symbol"],
            "window_status": review["window_status"],
            "append_status": append_status,
            "blockers": review["blockers"],
        })
    all_complete = [*existing_reviews, *[row for row in new_complete if row["review_id"] not in existing_ids]]
    return {
        "schema_version": "DerivativesShadowOutcomeReviewRunV1",
        "reviewer_version": config["reviewer_version"],
        "review_config_digest": review_config_digest,
        "as_of": iso(cutoff),
        "observation_count": len(raw_observations),
        "reviewable_observation_count": len(observations),
        "legacy_unreviewable_count": len(legacy_rows),
        "legacy_unreviewable": legacy_rows,
        "due_unreviewed_count": len(due),
        "not_due_count": not_due_count,
        "already_reviewed_due_count": already_reviewed_count,
        "appended_count": appended,
        "no_update_count": no_update,
        "data_blocked_count": blocked,
        "remaining_retryable_count": blocked,
        "results": results,
        "cohort_summary": summarize_cohorts(all_complete),
        **SAFETY_FLAGS,
    }


def live_bar_provider(timeout: float) -> Callable[[dict[str, Any], str], list[dict[str, Any]]]:
    bounded_timeout = min(max(float(timeout), 0.25), 12.0)

    def provide(observation: dict[str, Any], as_of: str) -> list[dict[str, Any]]:
        start = parse_time(observation["observed_at"], "observed_at")
        due = parse_time(observation["review_due_at"], "review_due_at")
        cutoff = min(due, parse_time(as_of, "as_of"))
        raw = fetch_json(
            "/api/v3/klines",
            {
                "symbol": observation["symbol"],
                "interval": "15m",
                "startTime": int(start.timestamp() * 1000),
                "endTime": int(cutoff.timestamp() * 1000),
                "limit": 1000,
            },
            timeout=bounded_timeout,
            max_bases=2,
            transport="curl",
        )
        return summarize_klines(raw)

    return provide


def fixture_bar_provider(directory: Path) -> Callable[[dict[str, Any], str], list[Any]]:
    def provide(observation: dict[str, Any], _as_of: str) -> list[Any]:
        path = directory / f"{observation['symbol']}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ShadowOutcomeError("fixture_bars_must_be_array")
        return summarize_klines(payload) if payload and isinstance(payload[0], list) else payload

    return provide


def self_test() -> dict[str, Any]:
    config = validate_config(load_json(DEFAULT_CONFIG))
    observed = datetime(2030, 1, 1, tzinfo=timezone.utc)
    observation = {
        "schema_version": "DerivativesShadowObservationV1",
        "observation_id": "derivatives-observation-self-test",
        "derivatives_snapshot_id": "derivatives-self-test",
        "symbol": "TESTUSDT",
        "discovery_rank": 4,
        "snapshot_id": "snapshot-self-test",
        "strategy_version": "unified-shortterm-derivatives-shadow-v2",
        "config_digest": "a" * 64,
        "source_digest": "b" * 64,
        "observed_at": iso(observed),
        "captured_at": iso(observed),
        "review_due_at": iso(observed + timedelta(days=7)),
        "evaluation_window_hours": 168,
        "production_signal_stage": "early_watch",
        "production_impulse_score_points": 40.0,
        "spot_factor": {"current_price": 100.0},
        "directional_state": "OI_ONLY_CONFLICT",
        "directional_blockers": ["spot_buying_confirmed"],
        "production_rule_changed": False,
        "formal_action_eligible": False,
        "paper_roi_eligible": False,
        "real_money_roi_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    bars = []
    for index in range(7 * 24 * 4):
        start = observed + timedelta(minutes=15 * index)
        high = 106.0 if index == 100 else 101.0
        low = 95.5 if index == 200 else 99.0
        bars.append({
            "open_time": int(start.timestamp() * 1000),
            "close_time": int((start + timedelta(minutes=15) - timedelta(milliseconds=1)).timestamp() * 1000),
            "high": high,
            "low": low,
            "close": 101.0,
        })
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        observation_path = root / "observations.jsonl"
        outcome_path = root / "outcomes.jsonl"
        observation_path.write_text(canonical_json(observation) + "\n", encoding="utf-8")
        result = review_due_observations(
            observation_ledger=observation_path,
            outcome_ledger=outcome_path,
            as_of=observation["review_due_at"],
            config=config,
            bar_provider=lambda _observation, _as_of: bars,
            max_workers=1,
            write=True,
        )
        review = load_records(outcome_path)[0]
    triggers = [row["first_trigger"] for row in review["path_outcomes"]]
    if result["appended_count"] != 1 or triggers != ["TARGET", "STOP", "NONE"]:
        raise ShadowOutcomeError("self_test_path_outcome_failed")
    if any(review[field] is not expected for field, expected in SAFETY_FLAGS.items()):
        raise ShadowOutcomeError("self_test_safety_failed")
    return {
        "schema_version": "DerivativesShadowOutcomeSelfTestV1",
        "status": "PASS",
        "checks": ["three_frozen_paths", "append_only", "binding", "roi_and_action_isolation"],
        **SAFETY_FLAGS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--observation-ledger")
    parser.add_argument("--outcome-ledger")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--as-of")
    parser.add_argument("--bars-dir")
    parser.add_argument("--timeout", type=float, default=4.0)
    parser.add_argument("--max-workers", type=int)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.self_test:
        result = self_test()
    else:
        if not args.observation_ledger or not args.outcome_ledger:
            raise SystemExit("--observation-ledger and --outcome-ledger are required")
        config = validate_config(load_json(Path(args.config).expanduser().resolve()))
        as_of = args.as_of or iso(datetime.now(timezone.utc))
        provider = fixture_bar_provider(Path(args.bars_dir).expanduser().resolve()) if args.bars_dir else live_bar_provider(args.timeout)
        result = review_due_observations(
            observation_ledger=Path(args.observation_ledger).expanduser().resolve(),
            outcome_ledger=Path(args.outcome_ledger).expanduser().resolve(),
            as_of=as_of,
            config=config,
            bar_provider=provider,
            max_workers=args.max_workers or config["maximum_workers"],
            write=not args.no_write,
        )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(output)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
