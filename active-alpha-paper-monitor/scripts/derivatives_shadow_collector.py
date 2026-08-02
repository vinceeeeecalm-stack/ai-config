#!/usr/bin/env python3
"""Collect public derivatives context for every opt-in discovery candidate.

The output is shadow evidence only.  It never changes the production ranking,
never emits a formal user action, and never calls private or order endpoints.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "DerivativesShadowCollectorV2"
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "derivatives_shadow_v2.json"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"
FORBIDDEN_KEYS = {"api_key", "apikey", "secret", "token", "password", "private_key", "account"}


class DerivativesShadowError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_time(value: Any, field: str) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise DerivativesShadowError(f"missing:{field}")
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise DerivativesShadowError(f"invalid_time:{field}") from exc
    if parsed.tzinfo is None:
        raise DerivativesShadowError(f"timezone_required:{field}")
    return parsed.astimezone(dt.timezone.utc)


def number(value: Any, field: str, *, allow_none: bool = True) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise DerivativesShadowError(f"invalid_number:{field}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DerivativesShadowError(f"invalid_number:{field}") from exc
    if not math.isfinite(result):
        raise DerivativesShadowError(f"invalid_number:{field}")
    return result


def text(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DerivativesShadowError(f"missing:{field}")
    return value.strip()


def digest(value: Any, field: str) -> str:
    result = text({field: value}, field).lower()
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise DerivativesShadowError(f"invalid_digest:{field}")
    return result


def scan_forbidden(value: Any, prefix: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().strip()
            if normalized in FORBIDDEN_KEYS:
                raise DerivativesShadowError(f"forbidden_field:{prefix}{key}")
            scan_forbidden(child, f"{prefix}{key}.")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_forbidden(child, f"{prefix}{index}.")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DerivativesShadowError(f"json_object_required:{path}")
    return value


def write_json(path: Path | None, value: dict[str, Any]) -> None:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path is None:
        print(rendered, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("schema_version") != "DerivativesShadowConfigV1":
        raise DerivativesShadowError("schema:DerivativesShadowConfigV1")
    text(config, "strategy_version")
    for field in ("maximum_candidates", "per_source_timeout_seconds", "whole_round_timeout_seconds"):
        value = config.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise DerivativesShadowError(f"invalid_positive_integer:{field}")
    if not 1 <= config["maximum_candidates"] <= 20:
        raise DerivativesShadowError("maximum_candidates_out_of_range")
    if config["per_source_timeout_seconds"] > 12:
        raise DerivativesShadowError("per_source_timeout_exceeds_twelve_seconds")
    if config["whole_round_timeout_seconds"] > 120:
        raise DerivativesShadowError("whole_round_timeout_exceeds_120_seconds")
    for field, expected in (
        ("production_rule_changed", False),
        ("formal_action_eligible", False),
        ("paper_roi_eligible", False),
        ("real_money_roi_eligible", False),
        ("live_orders_enabled", False),
        ("private_api_used", False),
        ("human_confirmation_required", True),
    ):
        if config.get(field) is not expected:
            raise DerivativesShadowError(f"unsafe_config:{field}")
    scan_forbidden(config)
    return config


def validate_discovery(discovery: dict[str, Any], maximum_candidates: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if discovery.get("schema_version") != "fast-candidate-funnel-v1" or discovery.get("phase") != "discovery":
        raise DerivativesShadowError("discovery_contract_required")
    if discovery.get("derivatives_shadow_handoff_enabled") is not True:
        raise DerivativesShadowError("derivatives_shadow_handoff_required")
    candidates = discovery.get("top_candidates")
    handoffs = discovery.get("derivatives_shadow_handoff")
    if not isinstance(candidates, list) or not isinstance(handoffs, list) or not candidates:
        raise DerivativesShadowError("candidate_and_handoff_lists_required")
    if len(candidates) > maximum_candidates:
        raise DerivativesShadowError("candidate_count_exceeds_config")
    candidate_symbols = [text(item, "symbol") for item in candidates]
    handoff_symbols = [text(item, "symbol") for item in handoffs]
    if candidate_symbols != handoff_symbols or len(set(candidate_symbols)) != len(candidate_symbols):
        raise DerivativesShadowError("candidate_handoff_symbol_order_mismatch")
    captured_times = {text(item, "captured_at") for item in handoffs}
    if len(captured_times) != 1:
        raise DerivativesShadowError("mixed_handoff_capture_times")
    parse_time(next(iter(captured_times)), "captured_at")
    for index, item in enumerate(handoffs, start=1):
        if item.get("schema_version") != "DerivativesShadowSpotHandoffV1":
            raise DerivativesShadowError(f"invalid_handoff_schema:{index}")
        if item.get("discovery_rank") != index:
            raise DerivativesShadowError(f"handoff_rank_mismatch:{index}")
        if item.get("formal_action_eligible") is not False or item.get("production_ranking_input") is not False:
            raise DerivativesShadowError(f"handoff_action_leakage:{index}")
    return candidates, handoffs


def fetch_public_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "CodexDerivativesShadow/2.0"})
    with urllib.request.urlopen(request, timeout=max(0.1, timeout)) as response:
        value = json.loads(response.read().decode("utf-8"))
    if isinstance(value, dict) and isinstance(value.get("code"), int) and value["code"] < 0:
        raise DerivativesShadowError(f"public_source_error:{value['code']}")
    return value


def public_url(path: str, params: dict[str, Any] | None = None) -> str:
    query = urllib.parse.urlencode(params or {})
    return BINANCE_FUTURES_BASE + path + ("?" + query if query else "")


def list_by_symbol(value: Any) -> dict[str, dict[str, Any]]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise DerivativesShadowError("public_global_payload_must_be_list")
    return {str(item.get("symbol") or ""): item for item in value if isinstance(item, dict) and item.get("symbol")}


def resolve_derivatives_symbol(spot_symbol: str, available_symbols: set[str]) -> str | None:
    """Map an eligible spot pair to the public perpetual contract identity.

    Binance uses quantity multipliers for some low-unit-price contracts (for
    example PEPEUSDT -> 1000PEPEUSDT).  The mapping is accepted only when the
    exact derived contract is present in the current public contract snapshot.
    """

    if spot_symbol in available_symbols:
        return spot_symbol
    thousand = "1000" + spot_symbol
    return thousand if thousand in available_symbols else None


def fetch_real_sources(symbols: list[str], config: dict[str, Any]) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    deadline = started + config["whole_round_timeout_seconds"]
    timeout = float(config["per_source_timeout_seconds"])
    global_errors: list[str] = []
    premium_by_symbol: dict[str, dict[str, Any]] = {}
    ticker_by_symbol: dict[str, dict[str, Any]] = {}
    try:
        premium_by_symbol = list_by_symbol(fetch_public_json(public_url("/fapi/v1/premiumIndex"), timeout))
    except Exception as exc:  # noqa: BLE001 - source failure is recorded, never hidden.
        global_errors.append("premium_index:" + type(exc).__name__)
    try:
        ticker_by_symbol = list_by_symbol(fetch_public_json(public_url("/fapi/v1/ticker/24hr"), timeout))
    except Exception as exc:  # noqa: BLE001
        global_errors.append("ticker_24h:" + type(exc).__name__)

    def fetch_symbol(symbol: str) -> tuple[str, dict[str, Any]]:
        available_symbols = set(premium_by_symbol) | set(ticker_by_symbol)
        derivatives_symbol = resolve_derivatives_symbol(symbol, available_symbols)
        if derivatives_symbol is None:
            return symbol, {
                "derivatives_symbol": None,
                "errors": ["no_matching_public_perpetual_contract"],
                "global_errors": list(global_errors),
            }
        remaining = max(0.1, min(timeout, deadline - time.monotonic()))
        result: dict[str, Any] = {
            "derivatives_symbol": derivatives_symbol,
            "premium_index": premium_by_symbol.get(derivatives_symbol),
            "ticker_24h": ticker_by_symbol.get(derivatives_symbol),
            "global_errors": list(global_errors),
        }
        errors = []
        try:
            result["oi_history"] = fetch_public_json(
                public_url(
                    "/futures/data/openInterestHist",
                    {
                        "symbol": derivatives_symbol,
                        "period": config["oi_history_period"],
                        "limit": config["oi_history_limit"],
                    },
                ),
                remaining,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append("oi_history:" + type(exc).__name__)
        remaining = max(0.1, min(timeout, deadline - time.monotonic()))
        try:
            result["taker_history"] = fetch_public_json(
                public_url(
                    "/futures/data/takerlongshortRatio",
                    {
                        "symbol": derivatives_symbol,
                        "period": config["taker_history_period"],
                        "limit": config["taker_history_limit"],
                    },
                ),
                remaining,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append("taker_history:" + type(exc).__name__)
        result["errors"] = errors
        return symbol, result

    results: dict[str, Any] = {}
    workers = max(1, min(8, len(symbols)))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    futures = {executor.submit(fetch_symbol, symbol): symbol for symbol in symbols}
    try:
        remaining = max(0.1, deadline - time.monotonic())
        for future in concurrent.futures.as_completed(futures, timeout=remaining):
            symbol, result = future.result()
            results[symbol] = result
    except concurrent.futures.TimeoutError:
        pass
    finally:
        for future, symbol in futures.items():
            if not future.done():
                future.cancel()
                results[symbol] = {"errors": ["whole_round_timeout"], "global_errors": list(global_errors)}
        executor.shutdown(wait=False, cancel_futures=True)
    for symbol in symbols:
        results.setdefault(symbol, {"errors": ["missing_worker_result"], "global_errors": list(global_errors)})
    return results, time.monotonic() - started


def oi_change(history: Any, horizon_seconds: int) -> float | None:
    if not isinstance(history, list):
        return None
    rows = []
    for item in history:
        if not isinstance(item, dict):
            continue
        timestamp = number(item.get("timestamp"), "oi.timestamp")
        value = number(item.get("sumOpenInterestValue", item.get("sumOpenInterest")), "oi.value")
        if timestamp is not None and value is not None and value > 0:
            rows.append((timestamp / 1000.0, value))
    rows.sort()
    if len(rows) < 2:
        return None
    latest_time, latest_value = rows[-1]
    target = latest_time - horizon_seconds
    eligible = [row for row in rows[:-1] if row[0] <= target + 300]
    if not eligible:
        return None
    anchor_time, anchor_value = max(eligible, key=lambda row: row[0])
    if anchor_time > target + 300 or anchor_value <= 0:
        return None
    return round((latest_value / anchor_value - 1.0) * 100.0, 9)


def aggregate_taker_ratio(history: Any) -> float | None:
    if not isinstance(history, list):
        return None
    buy = 0.0
    sell = 0.0
    valid = 0
    for item in history:
        if not isinstance(item, dict):
            continue
        buy_value = number(item.get("buyVol"), "taker.buyVol")
        sell_value = number(item.get("sellVol"), "taker.sellVol")
        if buy_value is None or sell_value is None or buy_value < 0 or sell_value < 0:
            continue
        buy += buy_value
        sell += sell_value
        valid += 1
    return round(buy / (buy + sell), 9) if valid and buy + sell > 0 else None


def directional_state(spot: dict[str, Any], derivatives: dict[str, Any], config: dict[str, Any]) -> tuple[str, list[str]]:
    oi_values = [derivatives.get(key) for key in ("oi_change_1h_pct", "oi_change_4h_pct", "oi_change_24h_pct")]
    oi_fuel = any(value is not None and value > 0 for value in oi_values)
    spot_checks = [
        (number(spot.get("spot_taker_buy_ratio_5m"), "spot_taker_buy_ratio_5m") or 0) >= config["spot_taker_buy_minimum"],
        (number(spot.get("relative_volume_5m"), "relative_volume_5m") or 0) >= config["relative_volume_minimum"],
        (number(spot.get("return_5m_pct"), "return_5m_pct") or 0) > 0,
        (number(spot.get("orderbook_imbalance"), "orderbook_imbalance") or 0) > 0,
    ]
    spot_buying = sum(spot_checks) >= 3
    structure = spot.get("higher_lows") is True or spot.get("breakout_close") is True
    perp_buying = (derivatives.get("perp_taker_buy_ratio") or 0) >= config["perp_taker_buy_minimum"]
    funding = derivatives.get("funding_rate_pct")
    funding_not_extreme = funding is not None and abs(funding) < config["funding_extreme_abs_pct"]
    liquidity = (
        (number(spot.get("spread_bps"), "spread_bps") or 999999) <= config["spread_bps_maximum"]
        and (number(spot.get("depth_bid_usd"), "depth_bid_usd") or 0) > 0
        and (number(spot.get("depth_ask_usd"), "depth_ask_usd") or 0) > 0
    )
    checks = {
        "oi_fuel": oi_fuel,
        "spot_buying": spot_buying,
        "structure_support": structure,
        "perp_buying": perp_buying,
        "funding_not_extreme": funding_not_extreme,
        "liquidity": liquidity,
    }
    blockers = [key for key, passed in checks.items() if not passed]
    if all(checks.values()):
        return "CONFIRMED_LONG", blockers
    if oi_fuel and not funding_not_extreme:
        return "CROWDED_CONFLICT", blockers
    if oi_fuel:
        return "OI_ONLY_CONFLICT", blockers
    if all(value is None for value in oi_values):
        return "DATA_INSUFFICIENT", blockers
    return "NO_FUEL", blockers


def build_record(
    handoff: dict[str, Any],
    source: dict[str, Any],
    *,
    binding: dict[str, str],
    config: dict[str, Any],
) -> dict[str, Any]:
    premium = source.get("premium_index") if isinstance(source.get("premium_index"), dict) else {}
    ticker = source.get("ticker_24h") if isinstance(source.get("ticker_24h"), dict) else {}
    funding_raw = number(premium.get("lastFundingRate"), "lastFundingRate")
    mark = number(premium.get("markPrice"), "markPrice")
    index_price = number(premium.get("indexPrice"), "indexPrice")
    derivatives = {
        "oi_change_1h_pct": oi_change(source.get("oi_history"), 3600),
        "oi_change_4h_pct": oi_change(source.get("oi_history"), 4 * 3600),
        "oi_change_24h_pct": oi_change(source.get("oi_history"), 24 * 3600),
        "funding_rate_pct": round(funding_raw * 100.0, 9) if funding_raw is not None else None,
        "perpetual_basis_pct": round((mark / index_price - 1.0) * 100.0, 9) if mark and index_price else None,
        "perp_taker_buy_ratio": aggregate_taker_ratio(source.get("taker_history")),
        "perp_quote_volume_24h_usd": number(ticker.get("quoteVolume"), "quoteVolume"),
    }
    available = sum(value is not None for value in derivatives.values())
    errors = sorted(set(str(item) for item in [*(source.get("global_errors") or []), *(source.get("errors") or [])]))
    if available == 0 and not errors:
        errors.append("no_derivatives_fields_available")
    source_status = "COMPLETE" if available == len(derivatives) and not errors else "PARTIAL" if available else "DEGRADED"
    state, blockers = directional_state(handoff, derivatives, config)
    captured_at = parse_time(handoff["captured_at"], "captured_at")
    record = {
        "schema_version": "ShortTermDerivativesShadowSnapshotV1",
        "derivatives_snapshot_id": "derivatives-" + sha256_json({"symbol": handoff["symbol"], **binding})[:20],
        "symbol": handoff["symbol"],
        "derivatives_symbol": source.get("derivatives_symbol") or handoff["symbol"],
        "discovery_rank": handoff["discovery_rank"],
        **binding,
        "captured_at": handoff["captured_at"],
        "review_due_at": (captured_at + dt.timedelta(days=7)).isoformat().replace("+00:00", "Z"),
        "evaluation_window_hours": 168,
        "production_signal_stage": handoff.get("production_signal_stage"),
        "production_impulse_score_points": handoff.get("production_impulse_score_points"),
        "spot_factor": {key: handoff.get(key) for key in (
            "current_price", "return_1m_pct", "return_5m_pct", "return_15m_pct",
            "relative_volume_5m", "spot_taker_buy_ratio_5m", "orderbook_imbalance",
            "higher_lows", "breakout_close", "spread_bps", "depth_bid_usd", "depth_ask_usd",
        )},
        "derivatives_factor": derivatives,
        "directional_state": state,
        "directional_blockers": blockers,
        "oi_alone_can_authorize": False,
        "source_status": source_status,
        "source_errors": errors,
        "public_sources": ["Binance Futures public premiumIndex", "openInterestHist", "takerlongshortRatio", "ticker/24hr"],
        "production_rule_changed": False,
        "formal_action_eligible": False,
        "paper_roi_eligible": False,
        "real_money_roi_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    validate_record(record)
    return record


def validate_record(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "ShortTermDerivativesShadowSnapshotV1":
        raise DerivativesShadowError("schema:ShortTermDerivativesShadowSnapshotV1")
    for field in ("derivatives_snapshot_id", "symbol", "snapshot_id", "strategy_version"):
        text(record, field)
    digest(record.get("config_digest"), "config_digest")
    digest(record.get("source_digest"), "source_digest")
    parse_time(record.get("captured_at"), "captured_at")
    captured_at = parse_time(record.get("captured_at"), "captured_at")
    review_due_at = parse_time(record.get("review_due_at"), "review_due_at")
    if review_due_at != captured_at + dt.timedelta(days=7) or record.get("evaluation_window_hours") != 168:
        raise DerivativesShadowError("shadow_review_clock_must_be_seven_days")
    if record.get("directional_state") not in {"CONFIRMED_LONG", "CROWDED_CONFLICT", "OI_ONLY_CONFLICT", "DATA_INSUFFICIENT", "NO_FUEL"}:
        raise DerivativesShadowError("invalid_directional_state")
    if record.get("oi_alone_can_authorize") is not False:
        raise DerivativesShadowError("oi_alone_authorization_forbidden")
    for field, expected in (
        ("production_rule_changed", False), ("formal_action_eligible", False),
        ("paper_roi_eligible", False), ("real_money_roi_eligible", False),
        ("live_orders_enabled", False), ("private_api_used", False),
        ("human_confirmation_required", True),
    ):
        if record.get(field) is not expected:
            raise DerivativesShadowError(f"unsafe_record:{field}")
    scan_forbidden(record)
    return record


def run_shadow(discovery: dict[str, Any], config: dict[str, Any], sources: dict[str, Any] | None = None) -> dict[str, Any]:
    validate_config(config)
    candidates, handoffs = validate_discovery(discovery, config["maximum_candidates"])
    symbols = [item["symbol"] for item in candidates]
    started = time.monotonic()
    if sources is None:
        sources, elapsed = fetch_real_sources(symbols, config)
        evidence_mode = "public_runtime"
    else:
        elapsed = time.monotonic() - started
        evidence_mode = "deterministic_fixture"
    if not isinstance(sources, dict):
        raise DerivativesShadowError("sources_must_be_object")
    snapshot_id = "derivatives-shadow-" + sha256_json(
        {"symbols": symbols, "captured_at": handoffs[0]["captured_at"], "discovery_digest": sha256_json(discovery)}
    )[:20]
    binding = {
        "snapshot_id": snapshot_id,
        "strategy_version": config["strategy_version"],
        "config_digest": sha256_json(config),
        "source_digest": sha256_json({symbol: sources.get(symbol) for symbol in symbols}),
    }
    records = [
        build_record(handoff, sources.get(handoff["symbol"]) if isinstance(sources.get(handoff["symbol"]), dict) else {"errors": ["candidate_source_missing"]}, binding=binding, config=config)
        for handoff in handoffs
    ]
    status_counts = {status: sum(record["source_status"] == status for record in records) for status in ("COMPLETE", "PARTIAL", "DEGRADED")}
    state_counts = {state: sum(record["directional_state"] == state for record in records) for state in ("CONFIRMED_LONG", "CROWDED_CONFLICT", "OI_ONLY_CONFLICT", "DATA_INSUFFICIENT", "NO_FUEL")}
    lead_candidates = [
        record["symbol"]
        for record in records
        if record["directional_state"] == "CONFIRMED_LONG"
        and record.get("production_signal_stage") not in {"trigger", "pre_breakout"}
    ]
    production_stage_counts: dict[str, int] = {}
    for record in records:
        stage = str(record.get("production_signal_stage") or "missing")
        production_stage_counts[stage] = production_stage_counts.get(stage, 0) + 1
    oi_fuel_count = sum(
        record["directional_state"] in {"CONFIRMED_LONG", "CROWDED_CONFLICT", "OI_ONLY_CONFLICT"}
        for record in records
    )
    result = {
        "schema_version": "DerivativesShadowRunV1",
        "engine_version": SCHEMA_VERSION,
        **binding,
        "captured_at": handoffs[0]["captured_at"],
        "candidate_count": len(records),
        "candidate_symbols": symbols,
        "source_status_counts": status_counts,
        "complete_coverage_rate_pct": round(status_counts["COMPLETE"] / len(records) * 100.0, 6),
        "directional_state_counts": state_counts,
        "oi_fuel_candidate_count": oi_fuel_count,
        "oi_only_conflict_rate_pct": round(state_counts["OI_ONLY_CONFLICT"] / len(records) * 100.0, 6),
        "production_signal_stage_counts": production_stage_counts,
        "derivatives_lead_candidate_count": len(lead_candidates),
        "derivatives_lead_candidates": lead_candidates,
        "records": records,
        "elapsed_seconds": round(elapsed, 6),
        "within_wall_clock_budget": elapsed <= config["whole_round_timeout_seconds"],
        "evidence_mode": evidence_mode,
        "run_status": "DATA_DEGRADED" if status_counts["COMPLETE"] + status_counts["PARTIAL"] == 0 else "SHADOW_EVIDENCE_COLLECTED",
        "unique_next_step": "accumulate_shadow_outcomes_without_changing_production_rule",
        "production_rule_changed": False,
        "formal_action_eligible": False,
        "paper_roi_eligible": False,
        "real_money_roi_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    scan_forbidden(result)
    return result


def append_observations(path: Path, run: dict[str, Any]) -> dict[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DerivativesShadowError(f"invalid_jsonl:{line_number}") from exc
            observation_id = text(item, "observation_id")
            existing[observation_id] = item
    appended = 0
    no_update = 0
    for record in run["records"]:
        observation = {
            "schema_version": "DerivativesShadowObservationV1",
            "observation_id": "derivatives-observation-" + sha256_json(
                {"derivatives_snapshot_id": record["derivatives_snapshot_id"], "symbol": record["symbol"]}
            )[:20],
            "observed_at": record["captured_at"],
            **{key: value for key, value in record.items() if key != "schema_version"},
        }
        observation_id = observation["observation_id"]
        if observation_id in existing:
            if canonical_json(existing[observation_id]) != canonical_json(observation):
                raise DerivativesShadowError(f"append_only_collision:{observation_id}")
            no_update += 1
            continue
        with path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(observation) + "\n")
        existing[observation_id] = observation
        appended += 1
    return {"APPENDED": appended, "NO_UPDATE": no_update}


def self_test() -> dict[str, Any]:
    config = validate_config(load_json(DEFAULT_CONFIG))
    handoff = {
        "schema_version": "DerivativesShadowSpotHandoffV1", "symbol": "TESTUSDT", "discovery_rank": 1,
        "captured_at": "2030-01-01T00:00:00Z", "current_price": 10.0, "return_1m_pct": 0.2,
        "return_5m_pct": 1.0, "return_15m_pct": 1.2, "relative_volume_5m": 2.0,
        "spot_taker_buy_ratio_5m": 0.62, "orderbook_imbalance": 0.2, "higher_lows": True,
        "breakout_close": False, "spread_bps": 2.0, "depth_bid_usd": 10000.0, "depth_ask_usd": 10000.0,
        "data_quality": "verified", "formal_action_eligible": False, "production_ranking_input": False,
        "live_orders_enabled": False, "private_api_used": False, "human_confirmation_required": True,
    }
    discovery = {
        "schema_version": "fast-candidate-funnel-v1", "phase": "discovery",
        "top_candidates": [{"symbol": "TESTUSDT"}], "derivatives_shadow_handoff": [handoff],
        "derivatives_shadow_handoff_enabled": True,
    }
    now_ms = 1893456000000
    history = [{"timestamp": now_ms - (288 - index) * 300000, "sumOpenInterestValue": 1000 + index} for index in range(289)]
    source = {"TESTUSDT": {"premium_index": {"lastFundingRate": "0.0001", "markPrice": "10.01", "indexPrice": "10"}, "ticker_24h": {"quoteVolume": "1000000"}, "oi_history": history, "taker_history": [{"buyVol": "60", "sellVol": "40"}]}}
    result = run_shadow(discovery, config, source)
    if result["records"][0]["directional_state"] != "CONFIRMED_LONG":
        raise DerivativesShadowError("self_test_confirmation_failed")
    return {"schema_version": "DerivativesShadowSelfTestV1", "status": "PASS", "checks": ["binding", "all_candidate", "oi_not_alone", "production_isolation"], "live_orders_enabled": False, "private_api_used": False}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--input")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--fixture-sources")
    parser.add_argument("--output")
    parser.add_argument("--ledger")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.self_test:
        write_json(None, self_test())
        return
    if not args.input:
        raise SystemExit("--input is required unless --self-test is used")
    discovery = load_json(Path(args.input))
    config = load_json(Path(args.config))
    sources = load_json(Path(args.fixture_sources)) if args.fixture_sources else None
    if sources is not None:
        sources = sources.get("derivatives_by_symbol", sources)
    result = run_shadow(discovery, config, sources)
    if args.ledger:
        result["append_result"] = append_observations(Path(args.ledger), result)
    write_json(Path(args.output) if args.output else None, result)


if __name__ == "__main__":
    main()
