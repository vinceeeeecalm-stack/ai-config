#!/usr/bin/env python3
"""Append-only evidence lanes for tactical 1–7 day research and Paper trades.

Observation samples measure what the scanner saw, including rejected setups.
Trade samples measure only completed, reproducible Paper fills.  Neither lane
can authorize a formal action or contribute to live profit attribution.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class EvidenceContractError(ValueError):
    """Raised when a record would weaken evidence or lane isolation."""


OBSERVATION_FIELDS = {
    "schema_version", "observation_id", "candidate_id", "setup_id", "symbol",
    "asset_class", "request_mode", "strategy_family", "strategy_version",
    "snapshot_id", "config_digest", "source_digest", "observed_at", "price",
    "price_as_of", "rank", "accepted", "rejection_reasons", "entry_trigger_state",
    "next_check_at", "review_due_at", "evidence_ids", "historical_summary",
    "formal_action_eligible", "paper_entry_eligible", "paper_live_separated",
    "live_orders_enabled", "private_api_used", "diagnostic_thresholds",
}
TRADE_FIELDS = {
    "schema_version", "trade_sample_id", "observation_id", "candidate_id", "symbol",
    "asset_class", "request_mode", "evidence_mode", "strategy_version", "snapshot_id",
    "config_digest", "source_digest", "entry_receipt", "exit_receipt", "outcome",
    "mfe_pct", "mae_pct", "holding_hours", "net_pnl_usd", "net_roi_pct",
    "formal_action_eligible", "real_money_roi_eligible", "business_ready_eligible",
    "paper_live_separated", "live_orders_enabled", "private_api_used",
}
PAPER_RECEIPT_FIELDS = {
    "schema_version", "receipt_id", "baseline_id", "trade_id", "symbol",
    "request_mode", "evidence_mode", "strategy_version", "snapshot_id", "event",
    "quantity", "price", "fee_usd", "slippage_usd", "executed_at",
    "live_orders_enabled",
}
OUTCOME_REVIEW_FIELDS = {
    "schema_version", "review_id", "observation_id", "candidate_id", "symbol",
    "asset_class", "request_mode", "strategy_version", "snapshot_id",
    "config_digest", "source_digest", "observed_at", "review_due_at",
    "reviewed_at", "window_start_at", "window_end_at", "window_status",
    "bar_interval", "closed_bar_count", "start_price", "end_price",
    "mfe_pct", "mae_pct", "target_return_pct", "stop_loss_pct",
    "target_first_at", "stop_first_at", "first_trigger", "path_ambiguous",
    "counterfactual_outcome", "original_accepted", "original_rejection_reasons",
    "blockers", "evidence_ids", "formal_action_eligible",
    "real_money_roi_eligible", "business_ready_eligible",
    "paper_live_separated", "live_orders_enabled", "private_api_used",
}


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise EvidenceContractError("timestamp must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceContractError(f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise EvidenceContractError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _require(record: dict[str, Any], fields: list[str]) -> None:
    missing = [field for field in fields if field not in record]
    if missing:
        raise EvidenceContractError("missing required fields: " + ", ".join(missing))


def _reject_unknown(record: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(record) - allowed)
    if unknown:
        raise EvidenceContractError(f"{label} contains non-allowlisted fields: {', '.join(unknown)}")


def _require_digest(record: dict[str, Any], field: str) -> None:
    value = record.get(field)
    if not isinstance(value, str) or len(value) != 64:
        raise EvidenceContractError(f"{field} must be a 64-hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise EvidenceContractError(f"{field} must be a 64-hex digest") from exc


def _require_false(record: dict[str, Any], fields: list[str]) -> None:
    for field in fields:
        if record.get(field) is not False:
            raise EvidenceContractError(f"{field} must be false")


def validate_observation(record: dict[str, Any]) -> dict[str, Any]:
    _reject_unknown(record, OBSERVATION_FIELDS, "observation")
    _require(record, [
        "schema_version", "observation_id", "candidate_id", "setup_id", "symbol",
        "asset_class", "request_mode", "strategy_family", "strategy_version",
        "snapshot_id", "config_digest", "source_digest", "observed_at", "price",
        "price_as_of", "rank", "accepted", "rejection_reasons", "entry_trigger_state",
        "next_check_at", "review_due_at", "evidence_ids", "formal_action_eligible",
        "paper_entry_eligible", "paper_live_separated", "live_orders_enabled",
        "private_api_used",
    ])
    if record["schema_version"] != "ObservationSampleV1":
        raise EvidenceContractError("observation schema must be ObservationSampleV1")
    if record["asset_class"] != "crypto" or record["request_mode"] != "tactical_1_7d":
        raise EvidenceContractError("observation must be crypto tactical_1_7d")
    if not isinstance(record["accepted"], bool):
        raise EvidenceContractError("accepted must be boolean")
    if not isinstance(record["rejection_reasons"], list):
        raise EvidenceContractError("rejection_reasons must be an array")
    if not record["accepted"] and not record["rejection_reasons"]:
        raise EvidenceContractError("rejected observation needs a reason")
    if record["paper_live_separated"] is not True:
        raise EvidenceContractError("paper_live_separated must be true")
    _require_false(record, ["formal_action_eligible", "live_orders_enabled", "private_api_used"])
    if record["paper_entry_eligible"] not in {True, False}:
        raise EvidenceContractError("paper_entry_eligible must be boolean")
    if record["paper_entry_eligible"] and not record["accepted"]:
        raise EvidenceContractError("rejected observation cannot be Paper-entry eligible")
    if not isinstance(record["evidence_ids"], list) or not record["evidence_ids"]:
        raise EvidenceContractError("observation needs evidence_ids")
    if not isinstance(record["rank"], int) or record["rank"] < 1:
        raise EvidenceContractError("rank must be a positive integer")
    price = float(record["price"])
    if not math.isfinite(price) or price <= 0:
        raise EvidenceContractError("price must be positive and finite")
    for field in ["observed_at", "price_as_of", "next_check_at", "review_due_at"]:
        _parse_time(record[field])
    if _parse_time(record["next_check_at"]) < _parse_time(record["observed_at"]):
        raise EvidenceContractError("next_check_at cannot precede observed_at")
    if _parse_time(record["review_due_at"]) < _parse_time(record["next_check_at"]):
        raise EvidenceContractError("review_due_at cannot precede next_check_at")
    _require_digest(record, "config_digest")
    _require_digest(record, "source_digest")
    thresholds = record.get("diagnostic_thresholds")
    if thresholds is not None:
        if not isinstance(thresholds, dict):
            raise EvidenceContractError("diagnostic_thresholds must be an object")
        _require(thresholds, [
            "target_return_pct", "stop_loss_pct", "horizon_hours",
            "source", "same_bar_stop_first",
        ])
        for field in ["target_return_pct", "stop_loss_pct", "horizon_hours"]:
            value = thresholds[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EvidenceContractError(f"diagnostic_thresholds {field} must be numeric")
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise EvidenceContractError(f"diagnostic_thresholds {field} must be positive")
        if float(thresholds["horizon_hours"]) > 7 * 24:
            raise EvidenceContractError("diagnostic horizon cannot exceed 7 days")
        if thresholds["same_bar_stop_first"] is not True:
            raise EvidenceContractError("diagnostic thresholds must use same-bar stop-first")
    return record


def validate_outcome_review(
    record: dict[str, Any],
    observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if observation is None:
        raise EvidenceContractError("outcome review requires an existing observation")
    _reject_unknown(record, OUTCOME_REVIEW_FIELDS, "observation outcome review")
    _require(record, sorted(OUTCOME_REVIEW_FIELDS))
    if record["schema_version"] != "ObservationOutcomeReviewV1":
        raise EvidenceContractError("outcome review schema must be ObservationOutcomeReviewV1")
    if record["asset_class"] != "crypto" or record["request_mode"] != "tactical_1_7d":
        raise EvidenceContractError("outcome review must be crypto tactical_1_7d")
    if record["window_status"] not in {"COMPLETE", "PATH_ONLY", "DATA_BLOCKED"}:
        raise EvidenceContractError("unsupported observation window_status")
    if record["first_trigger"] not in {
        "TARGET", "STOP", "NONE", "AMBIGUOUS_STOP_FIRST", "UNCLASSIFIED", "DATA_BLOCKED",
    }:
        raise EvidenceContractError("unsupported first_trigger")
    if record["counterfactual_outcome"] not in {
        "MISSED_UPSIDE", "PROTECTED_DOWNSIDE", "OBSERVED_UPSIDE",
        "OBSERVED_DOWNSIDE", "NEUTRAL", "UNCLASSIFIED_LEGACY", "DATA_BLOCKED",
    }:
        raise EvidenceContractError("unsupported counterfactual_outcome")
    if record["bar_interval"] != "15m":
        raise EvidenceContractError("observation review bar_interval must be 15m")
    if not isinstance(record["closed_bar_count"], int) or record["closed_bar_count"] < 0:
        raise EvidenceContractError("closed_bar_count must be a non-negative integer")
    if not isinstance(record["path_ambiguous"], bool):
        raise EvidenceContractError("path_ambiguous must be boolean")
    if not isinstance(record["original_accepted"], bool):
        raise EvidenceContractError("original_accepted must be boolean")
    if not isinstance(record["original_rejection_reasons"], list):
        raise EvidenceContractError("original_rejection_reasons must be an array")
    if not isinstance(record["blockers"], list):
        raise EvidenceContractError("blockers must be an array")
    if not isinstance(record["evidence_ids"], list) or not record["evidence_ids"]:
        raise EvidenceContractError("outcome review needs evidence_ids")
    if record["paper_live_separated"] is not True:
        raise EvidenceContractError("outcome review must stay separate from Paper/live")
    _require_false(record, [
        "formal_action_eligible", "real_money_roi_eligible", "business_ready_eligible",
        "live_orders_enabled", "private_api_used",
    ])
    _require_digest(record, "config_digest")
    _require_digest(record, "source_digest")
    for field in ["observed_at", "review_due_at", "reviewed_at"]:
        _parse_time(record[field])
    if _parse_time(record["reviewed_at"]) != _parse_time(record["review_due_at"]):
        raise EvidenceContractError("reviewed_at must bind the frozen review_due_at")
    if record["window_status"] == "DATA_BLOCKED":
        if record["closed_bar_count"] != 0 or not record["blockers"]:
            raise EvidenceContractError("DATA_BLOCKED review needs zero bars and blockers")
        if any(record[field] is not None for field in [
            "window_start_at", "window_end_at", "end_price", "mfe_pct", "mae_pct",
            "target_first_at", "stop_first_at",
        ]):
            raise EvidenceContractError("DATA_BLOCKED review cannot contain path results")
    else:
        if record["closed_bar_count"] <= 0:
            raise EvidenceContractError("completed path review needs closed bars")
        for field in ["window_start_at", "window_end_at"]:
            _parse_time(record[field])
        for field in ["start_price", "end_price", "mfe_pct", "mae_pct"]:
            value = record[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EvidenceContractError(f"{field} must be numeric")
            if not math.isfinite(float(value)):
                raise EvidenceContractError(f"{field} must be finite")
        if float(record["start_price"]) <= 0 or float(record["end_price"]) <= 0:
            raise EvidenceContractError("review prices must be positive")
        if float(record["mfe_pct"]) < 0 or float(record["mae_pct"]) > 0:
            raise EvidenceContractError("MFE must be non-negative and MAE non-positive")
    if record["window_status"] == "PATH_ONLY":
        if record["target_return_pct"] is not None or record["stop_loss_pct"] is not None:
            raise EvidenceContractError("legacy PATH_ONLY cannot invent diagnostic thresholds")
        if record["first_trigger"] != "UNCLASSIFIED":
            raise EvidenceContractError("PATH_ONLY first_trigger must be UNCLASSIFIED")
    validate_observation(observation)
    for field in [
        "observation_id", "candidate_id", "symbol", "strategy_version", "snapshot_id",
        "config_digest", "source_digest", "observed_at", "review_due_at",
    ]:
        if record[field] != observation[field]:
            raise EvidenceContractError(f"outcome review/observation mismatch: {field}")
    if record["original_accepted"] != observation["accepted"]:
        raise EvidenceContractError("outcome review accepted state mismatch")
    if record["original_rejection_reasons"] != observation["rejection_reasons"]:
        raise EvidenceContractError("outcome review rejection reasons mismatch")
    return record


def _validate_paper_receipt(receipt: dict[str, Any], expected_event: str) -> None:
    _reject_unknown(receipt, PAPER_RECEIPT_FIELDS, "Paper receipt")
    _require(receipt, [
        "schema_version", "receipt_id", "baseline_id", "trade_id", "symbol",
        "request_mode", "evidence_mode", "strategy_version", "snapshot_id", "event",
        "quantity", "price", "fee_usd", "slippage_usd", "executed_at",
        "live_orders_enabled",
    ])
    if receipt["schema_version"] != "PaperExecutionReceiptV1":
        raise EvidenceContractError("trade sample receipts must be PaperExecutionReceiptV1")
    if receipt["request_mode"] != "tactical_1_7d" or receipt["evidence_mode"] != "paper":
        raise EvidenceContractError("receipt must be isolated tactical_1_7d Paper evidence")
    if receipt["event"] != expected_event:
        raise EvidenceContractError(f"expected {expected_event} receipt")
    if receipt["live_orders_enabled"] is not False:
        raise EvidenceContractError("Paper receipt cannot enable live orders")
    for field in ["quantity", "price"]:
        value = float(receipt[field])
        if not math.isfinite(value) or value <= 0:
            raise EvidenceContractError(f"receipt {field} must be positive and finite")
    for field in ["fee_usd", "slippage_usd"]:
        value = float(receipt[field])
        if not math.isfinite(value) or value < 0:
            raise EvidenceContractError(f"receipt {field} must be non-negative and finite")
    _parse_time(receipt["executed_at"])


def validate_trade_sample(record: dict[str, Any], observation: dict[str, Any] | None = None) -> dict[str, Any]:
    _reject_unknown(record, TRADE_FIELDS, "trade sample")
    _require(record, [
        "schema_version", "trade_sample_id", "observation_id", "candidate_id", "symbol",
        "asset_class", "request_mode", "evidence_mode", "strategy_version", "snapshot_id",
        "config_digest", "source_digest", "entry_receipt", "exit_receipt", "outcome",
        "mfe_pct", "mae_pct", "holding_hours", "net_pnl_usd", "net_roi_pct",
        "formal_action_eligible", "real_money_roi_eligible", "business_ready_eligible",
        "paper_live_separated", "live_orders_enabled", "private_api_used",
    ])
    if record["schema_version"] != "TradeSampleV1":
        raise EvidenceContractError("trade schema must be TradeSampleV1")
    if record["asset_class"] != "crypto" or record["request_mode"] != "tactical_1_7d":
        raise EvidenceContractError("trade sample must be crypto tactical_1_7d")
    if record["evidence_mode"] != "paper" or record["paper_live_separated"] is not True:
        raise EvidenceContractError("trade sample must stay in the Paper lane")
    if record["outcome"] not in {"hit", "failed", "flat", "time_exit"}:
        raise EvidenceContractError("unsupported Paper trade outcome")
    for field in ["mfe_pct", "mae_pct", "holding_hours", "net_pnl_usd", "net_roi_pct"]:
        if not math.isfinite(float(record[field])):
            raise EvidenceContractError(f"{field} must be finite")
    _require_false(record, [
        "formal_action_eligible", "real_money_roi_eligible", "business_ready_eligible",
        "live_orders_enabled", "private_api_used",
    ])
    _require_digest(record, "config_digest")
    _require_digest(record, "source_digest")
    entry, exit_receipt = record["entry_receipt"], record["exit_receipt"]
    _validate_paper_receipt(entry, "entry")
    _validate_paper_receipt(exit_receipt, "exit")
    for field in ["baseline_id", "trade_id", "symbol", "strategy_version", "snapshot_id"]:
        if entry[field] != exit_receipt[field]:
            raise EvidenceContractError(f"entry/exit receipt mismatch: {field}")
    for field in ["symbol", "strategy_version", "snapshot_id"]:
        if record[field] != entry[field]:
            raise EvidenceContractError(f"trade sample/receipt mismatch: {field}")
    if abs(float(entry["quantity"]) - float(exit_receipt["quantity"])) > 1e-12:
        raise EvidenceContractError("closed trade sample requires equal entry and exit quantity")
    opened, closed = _parse_time(entry["executed_at"]), _parse_time(exit_receipt["executed_at"])
    holding_hours = (closed - opened).total_seconds() / 3600
    if holding_hours <= 0 or holding_hours > 7 * 24:
        raise EvidenceContractError("trade holding time must be within 1–7 days")
    quantity = float(entry["quantity"])
    invested = quantity * float(entry["price"])
    costs = sum(float(item[field]) for item in [entry, exit_receipt] for field in ["fee_usd", "slippage_usd"])
    expected_pnl = quantity * (float(exit_receipt["price"]) - float(entry["price"])) - costs
    expected_roi = expected_pnl / invested * 100
    if abs(float(record["holding_hours"]) - holding_hours) > 1e-6:
        raise EvidenceContractError("holding_hours does not match receipts")
    if abs(float(record["net_pnl_usd"]) - expected_pnl) > 1e-6:
        raise EvidenceContractError("net_pnl_usd does not match receipts")
    if abs(float(record["net_roi_pct"]) - expected_roi) > 1e-6:
        raise EvidenceContractError("net_roi_pct does not match receipts")
    if observation is not None:
        validate_observation(observation)
        if not observation["accepted"] or not observation["paper_entry_eligible"]:
            raise EvidenceContractError("TradeSample requires an accepted Paper-entry-eligible observation")
        for field in ["observation_id", "candidate_id", "symbol", "strategy_version", "snapshot_id", "config_digest", "source_digest"]:
            observed_value = observation[field]
            sample_value = record[field]
            if observed_value != sample_value:
                raise EvidenceContractError(f"trade sample/observation mismatch: {field}")
    return record


def append_record(path: Path, record: dict[str, Any], *, id_field: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                existing = json.loads(line)
                if existing.get(id_field) != record[id_field]:
                    continue
                if _canonical_digest(existing) == _canonical_digest(record):
                    return "NO_UPDATE"
                raise EvidenceContractError(f"append-only id collision: {record[id_field]}")
        with path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            out.flush()
            os.fsync(out.fileno())
    return "APPENDED"


def append_observation(path: Path, record: dict[str, Any]) -> str:
    return append_record(path, validate_observation(record), id_field="observation_id")


def append_outcome_review(
    path: Path,
    record: dict[str, Any],
    *,
    observation: dict[str, Any],
) -> str:
    return append_record(
        path,
        validate_outcome_review(record, observation),
        id_field="review_id",
    )


def build_scanner_observation(
    signal: dict[str, Any],
    *,
    rank: int,
    snapshot_id: str,
    strategy_version: str,
    config_digest: str,
    source_digest: str,
    observed_at: str,
    committee_degraded: bool,
) -> dict[str, Any]:
    """Normalize one frozen scanner candidate into the observation lane."""

    observed = _parse_time(observed_at)
    history = signal.get("historical_comparison") or {}
    diagnostic_horizon_hours = min(
        7 * 24,
        max(24, int(history.get("horizon_bars") or 5) * 24),
    )
    reasons: list[str] = []
    conservative_ev = history.get("conservative_expected_value_pct")
    if conservative_ev is None or float(conservative_ev) <= 0:
        reasons.append("positive_conservative_ev_gate_failed")
    if signal.get("stage") not in {"breakout_confirmed", "confirmed_impulse"}:
        reasons.append("current_impulse_not_confirmed")
    if not signal.get("breakout_close"):
        reasons.append("closed_breakout_not_confirmed")
    if signal.get("official_event_status") not in {"verified", "confirmed_1_7d"}:
        reasons.append("verified_1_7d_catalyst_missing")
    if committee_degraded:
        reasons.append("research_committee_degraded")
    reasons = sorted(set(reasons))
    accepted = not reasons
    evidence_ids = list(history.get("evidence_ids") or [])
    evidence_ids.extend((signal.get("risk_adjusted_path") or {}).get("evidence_ids") or [])
    evidence_ids.append(f"public-spot-snapshot:{snapshot_id}:{signal.get('symbol')}")
    evidence_ids = sorted(set(str(item) for item in evidence_ids if item))
    identity = {
        "snapshot_id": snapshot_id,
        "symbol": signal.get("symbol"),
        "setup_id": signal.get("impulse_id") or f"rank-{rank}",
        "strategy_version": strategy_version,
    }
    return validate_observation({
        "schema_version": "ObservationSampleV1",
        "observation_id": f"obs-{_canonical_digest(identity)[:20]}",
        "candidate_id": str(signal.get("symbol")),
        "setup_id": str(identity["setup_id"]),
        "symbol": str(signal.get("symbol")),
        "asset_class": "crypto",
        "request_mode": "tactical_1_7d",
        "strategy_family": "impulse_capture",
        "strategy_version": strategy_version,
        "snapshot_id": snapshot_id,
        "config_digest": config_digest,
        "source_digest": source_digest,
        "observed_at": observed.isoformat().replace("+00:00", "Z"),
        "price": float(signal["current_price"]),
        "price_as_of": observed.isoformat().replace("+00:00", "Z"),
        "rank": rank,
        "accepted": accepted,
        "rejection_reasons": reasons,
        "entry_trigger_state": "triggered" if signal.get("breakout_close") else "not_triggered",
        "next_check_at": (observed + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "review_due_at": (
            observed + timedelta(hours=diagnostic_horizon_hours)
        ).isoformat().replace("+00:00", "Z"),
        "evidence_ids": evidence_ids,
        "historical_summary": {
            key: history.get(key)
            for key in [
                "sample_size", "out_of_sample_win_rate_interval_pct",
                "conservative_expected_value_pct", "expected_return_pct",
                "profit_factor", "max_drawdown_pct", "walk_forward_positive_windows",
                "walk_forward_total_windows",
            ]
        },
        "diagnostic_thresholds": {
            "target_return_pct": float(history.get("target_return_pct") or 10.0),
            "stop_loss_pct": float(history.get("stop_loss_pct") or 5.0),
            "horizon_hours": diagnostic_horizon_hours,
            "source": "frozen_scanner_historical_contract",
            "same_bar_stop_first": True,
        },
        "formal_action_eligible": False,
        "paper_entry_eligible": accepted,
        "paper_live_separated": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    })


def _iso_from_millis(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def _data_blocked_review(
    observation: dict[str, Any],
    *,
    blocker: str,
) -> dict[str, Any]:
    identity = {
        "observation_id": observation["observation_id"],
        "review_due_at": observation["review_due_at"],
    }
    return validate_outcome_review({
        "schema_version": "ObservationOutcomeReviewV1",
        "review_id": f"review-{_canonical_digest(identity)[:20]}",
        **{
            field: observation[field]
            for field in [
                "observation_id", "candidate_id", "symbol", "strategy_version", "snapshot_id",
                "config_digest", "source_digest", "observed_at", "review_due_at",
            ]
        },
        "asset_class": "crypto",
        "request_mode": "tactical_1_7d",
        "reviewed_at": observation["review_due_at"],
        "window_start_at": None,
        "window_end_at": None,
        "window_status": "DATA_BLOCKED",
        "bar_interval": "15m",
        "closed_bar_count": 0,
        "start_price": float(observation["price"]),
        "end_price": None,
        "mfe_pct": None,
        "mae_pct": None,
        "target_return_pct": None,
        "stop_loss_pct": None,
        "target_first_at": None,
        "stop_first_at": None,
        "first_trigger": "DATA_BLOCKED",
        "path_ambiguous": False,
        "counterfactual_outcome": "DATA_BLOCKED",
        "original_accepted": observation["accepted"],
        "original_rejection_reasons": observation["rejection_reasons"],
        "blockers": [blocker],
        "evidence_ids": [f"review-blocked:{observation['observation_id']}"],
        "formal_action_eligible": False,
        "real_money_roi_eligible": False,
        "business_ready_eligible": False,
        "paper_live_separated": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }, observation)


def build_observation_outcome_review(
    observation: dict[str, Any],
    bars: list[dict[str, Any]],
    *,
    as_of: str,
    bar_interval: str = "15m",
) -> dict[str, Any]:
    """Build one final, no-hindsight review from closed post-observation bars."""

    validate_observation(observation)
    cutoff = _parse_time(as_of)
    due = _parse_time(observation["review_due_at"])
    observed = _parse_time(observation["observed_at"])
    if cutoff < due:
        raise EvidenceContractError("observation is not due for final review")
    if bar_interval != "15m":
        raise EvidenceContractError("only frozen 15m observation review is supported")
    observed_ms = int(observed.timestamp() * 1000)
    due_ms = int(due.timestamp() * 1000)
    usable: list[dict[str, Any]] = []
    for raw in bars:
        if not isinstance(raw, dict):
            continue
        try:
            open_time = int(raw["open_time"])
            close_time = int(raw["close_time"])
            high = float(raw["high"])
            low = float(raw["low"])
            close = float(raw["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if open_time < observed_ms or close_time > due_ms or close_time > int(cutoff.timestamp() * 1000):
            continue
        if close_time <= open_time or not all(math.isfinite(value) and value > 0 for value in [high, low, close]):
            continue
        if high < low:
            continue
        usable.append({
            "open_time": open_time,
            "close_time": close_time,
            "high": high,
            "low": low,
            "close": close,
        })
    usable.sort(key=lambda item: (item["open_time"], item["close_time"]))
    if not usable:
        return _data_blocked_review(observation, blocker="closed_post_observation_bars_missing")
    for left, right in zip(usable, usable[1:]):
        if right["open_time"] <= left["open_time"]:
            raise EvidenceContractError("observation review bars must be unique and chronological")

    start = float(observation["price"])
    maximum = max(item["high"] for item in usable)
    minimum = min(item["low"] for item in usable)
    mfe = max(0.0, (maximum / start - 1) * 100)
    mae = min(0.0, (minimum / start - 1) * 100)
    thresholds = observation.get("diagnostic_thresholds")
    target_pct = float(thresholds["target_return_pct"]) if thresholds else None
    stop_pct = float(thresholds["stop_loss_pct"]) if thresholds else None
    target_first_at = None
    stop_first_at = None
    first_trigger = "UNCLASSIFIED" if thresholds is None else "NONE"
    ambiguous = False
    if thresholds is not None:
        target_price = start * (1 + target_pct / 100)
        stop_price = start * (1 - stop_pct / 100)
        for bar in usable:
            target_hit = bar["high"] >= target_price
            stop_hit = bar["low"] <= stop_price
            when = _iso_from_millis(bar["close_time"])
            if target_hit and target_first_at is None:
                target_first_at = when
            if stop_hit and stop_first_at is None:
                stop_first_at = when
            if target_hit and stop_hit:
                first_trigger = "AMBIGUOUS_STOP_FIRST"
                ambiguous = True
                break
            if stop_hit:
                first_trigger = "STOP"
                break
            if target_hit:
                first_trigger = "TARGET"
                break
    if thresholds is None:
        counterfactual = "UNCLASSIFIED_LEGACY"
        window_status = "PATH_ONLY"
        blockers = ["diagnostic_thresholds_missing_legacy"]
    elif first_trigger in {"STOP", "AMBIGUOUS_STOP_FIRST"}:
        counterfactual = "OBSERVED_DOWNSIDE" if observation["accepted"] else "PROTECTED_DOWNSIDE"
        window_status = "COMPLETE"
        blockers = []
    elif first_trigger == "TARGET":
        counterfactual = "OBSERVED_UPSIDE" if observation["accepted"] else "MISSED_UPSIDE"
        window_status = "COMPLETE"
        blockers = []
    else:
        counterfactual = "NEUTRAL"
        window_status = "COMPLETE"
        blockers = []
    identity = {
        "observation_id": observation["observation_id"],
        "review_due_at": observation["review_due_at"],
    }
    return validate_outcome_review({
        "schema_version": "ObservationOutcomeReviewV1",
        "review_id": f"review-{_canonical_digest(identity)[:20]}",
        **{
            field: observation[field]
            for field in [
                "observation_id", "candidate_id", "symbol", "strategy_version", "snapshot_id",
                "config_digest", "source_digest", "observed_at", "review_due_at",
            ]
        },
        "asset_class": "crypto",
        "request_mode": "tactical_1_7d",
        "reviewed_at": observation["review_due_at"],
        "window_start_at": _iso_from_millis(usable[0]["open_time"]),
        "window_end_at": _iso_from_millis(usable[-1]["close_time"]),
        "window_status": window_status,
        "bar_interval": bar_interval,
        "closed_bar_count": len(usable),
        "start_price": round(start, 12),
        "end_price": round(usable[-1]["close"], 12),
        "mfe_pct": round(mfe, 6),
        "mae_pct": round(mae, 6),
        "target_return_pct": target_pct,
        "stop_loss_pct": stop_pct,
        "target_first_at": target_first_at,
        "stop_first_at": stop_first_at,
        "first_trigger": first_trigger,
        "path_ambiguous": ambiguous,
        "counterfactual_outcome": counterfactual,
        "original_accepted": observation["accepted"],
        "original_rejection_reasons": observation["rejection_reasons"],
        "blockers": blockers,
        "evidence_ids": [
            f"binance-spot-closed-15m:{observation['symbol']}:{observation['observed_at']}:{observation['review_due_at']}"
        ],
        "formal_action_eligible": False,
        "real_money_roi_eligible": False,
        "business_ready_eligible": False,
        "paper_live_separated": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }, observation)


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def review_due_observations(
    *,
    observation_ledger: Path,
    outcome_ledger: Path,
    as_of: str,
    bar_provider,
    max_workers: int = 4,
    write: bool = True,
) -> dict[str, Any]:
    """Review every final-due observation without letting one source failure fan out."""

    cutoff = _parse_time(as_of)
    observations = [validate_observation(item) for item in load_records(observation_ledger)]
    observation_by_id = {item["observation_id"]: item for item in observations}
    existing_reviews = [
        validate_outcome_review(item, observation_by_id.get(item.get("observation_id")))
        for item in load_records(outcome_ledger)
    ]
    existing_ids = {item["review_id"] for item in existing_reviews}
    due: list[dict[str, Any]] = []
    already_reviewed = 0
    for observation in sorted(observations, key=lambda item: (item["review_due_at"], item["observation_id"])):
        if _parse_time(observation["review_due_at"]) > cutoff:
            continue
        identity = {
            "observation_id": observation["observation_id"],
            "review_due_at": observation["review_due_at"],
        }
        review_id = f"review-{_canonical_digest(identity)[:20]}"
        if review_id in existing_ids:
            already_reviewed += 1
            continue
        due.append(observation)

    def prepare(observation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            bars = bar_provider(observation, as_of)
            review = build_observation_outcome_review(observation, bars, as_of=as_of)
        except Exception:  # noqa: BLE001 - source errors become candidate-local evidence blockers
            review = _data_blocked_review(observation, blocker="market_data_source_failed")
        return observation, review

    prepared: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if due:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(int(max_workers), len(due)))
        ) as executor:
            prepared = list(executor.map(prepare, due))
    results: list[dict[str, Any]] = []
    appended = no_update = blocked = 0
    for observation, review in prepared:
        append_status = "DRY_RUN"
        if review["window_status"] == "DATA_BLOCKED":
            append_status = "NOT_APPENDED_RETRYABLE"
            blocked += 1
        elif write:
            append_status = append_outcome_review(
                outcome_ledger,
                review,
                observation=observation,
            )
            if append_status == "APPENDED":
                appended += 1
            elif append_status == "NO_UPDATE":
                no_update += 1
        results.append({
            "review_id": review["review_id"],
            "observation_id": review["observation_id"],
            "symbol": review["symbol"],
            "window_status": review["window_status"],
            "counterfactual_outcome": review["counterfactual_outcome"],
            "append_status": append_status,
            "blockers": review["blockers"],
        })
    return {
        "schema_version": "ObservationOutcomeReviewRunV1",
        "as_of": cutoff.isoformat().replace("+00:00", "Z"),
        "observation_count": len(observations),
        "due_unreviewed_count": len(due),
        "already_reviewed_due_count": already_reviewed,
        "appended_count": appended,
        "no_update_count": no_update,
        "data_blocked_count": blocked,
        "remaining_retryable_count": blocked,
        "results": results,
        "formal_action_eligible": False,
        "real_money_roi_eligible": False,
        "paper_live_separated": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def append_trade_sample(
    path: Path,
    record: dict[str, Any],
    *,
    observation_ledger: Path,
) -> str:
    observations = {
        item["observation_id"]: validate_observation(item)
        for item in load_records(observation_ledger)
    }
    observation = observations.get(record.get("observation_id"))
    if observation is None:
        raise EvidenceContractError("trade sample requires an existing observation")
    return append_record(
        path,
        validate_trade_sample(record, observation),
        id_field="trade_sample_id",
    )


def summarize(
    observation_ledger: Path,
    trade_ledger: Path,
    outcome_ledger: Path | None = None,
) -> dict[str, Any]:
    observations = [validate_observation(item) for item in load_records(observation_ledger)]
    observation_by_id = {item["observation_id"]: item for item in observations}
    trades = [
        validate_trade_sample(item, observation_by_id.get(item.get("observation_id")))
        for item in load_records(trade_ledger)
    ]
    reviews = [
        validate_outcome_review(item, observation_by_id.get(item.get("observation_id")))
        for item in load_records(outcome_ledger)
    ] if outcome_ledger is not None else []
    return {
        "schema_version": "TacticalEvidenceLedgerSummaryV1",
        "request_mode": "tactical_1_7d",
        "observation_count": len(observations),
        "accepted_observation_count": sum(1 for item in observations if item["accepted"]),
        "rejected_observation_count": sum(1 for item in observations if not item["accepted"]),
        "paper_trade_sample_count": len(trades),
        "live_trade_sample_count": 0,
        "real_money_roi_sample_count": 0,
        "observation_outcome_review_count": len(reviews),
        "missed_upside_observation_count": sum(
            item["counterfactual_outcome"] == "MISSED_UPSIDE" for item in reviews
        ),
        "protected_downside_observation_count": sum(
            item["counterfactual_outcome"] == "PROTECTED_DOWNSIDE" for item in reviews
        ),
        "path_only_legacy_review_count": sum(
            item["window_status"] == "PATH_ONLY" for item in reviews
        ),
        "observation_review_enters_paper_or_live_roi": False,
        "paper_live_separated": True,
        "live_orders_enabled": False,
        "symbols": sorted({item["symbol"] for item in observations}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Append-only tactical observation/Paper evidence ledger")
    parser.add_argument("--observation-ledger", required=True)
    parser.add_argument("--trade-ledger", required=True)
    parser.add_argument("--outcome-ledger")
    parser.add_argument("--append-observation-json")
    parser.add_argument("--append-trade-json")
    parser.add_argument("--append-outcome-json")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    observation_path, trade_path = Path(args.observation_ledger), Path(args.trade_ledger)
    outcome_path = Path(args.outcome_ledger) if args.outcome_ledger else None
    if args.append_observation_json:
        record = json.loads(Path(args.append_observation_json).read_text(encoding="utf-8"))
        print(json.dumps({"status": append_observation(observation_path, record)}, sort_keys=True))
        return 0
    if args.append_trade_json:
        record = json.loads(Path(args.append_trade_json).read_text(encoding="utf-8"))
        print(json.dumps({"status": append_trade_sample(trade_path, record, observation_ledger=observation_path)}, sort_keys=True))
        return 0
    if args.append_outcome_json:
        if outcome_path is None:
            parser.error("--append-outcome-json requires --outcome-ledger")
        record = json.loads(Path(args.append_outcome_json).read_text(encoding="utf-8"))
        observations = {
            item["observation_id"]: validate_observation(item)
            for item in load_records(observation_path)
        }
        observation = observations.get(record.get("observation_id"))
        if observation is None:
            raise EvidenceContractError("outcome review requires an existing observation")
        print(json.dumps({
            "status": append_outcome_review(outcome_path, record, observation=observation)
        }, sort_keys=True))
        return 0
    if args.summary:
        print(json.dumps(
            summarize(observation_path, trade_path, outcome_path),
            ensure_ascii=False,
            sort_keys=True,
        ))
        return 0
    parser.error("choose an append operation or --summary")


if __name__ == "__main__":
    raise SystemExit(main())
