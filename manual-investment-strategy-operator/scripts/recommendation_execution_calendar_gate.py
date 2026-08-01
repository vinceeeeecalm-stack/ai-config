#!/usr/bin/env python3
"""Validate formal recommendation execution-calendar fields.

This is a research/report gate. It never places orders or changes a ledger.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "config" / "recommendation_execution_calendar_schema.json"
BASELINE_FIELDS = [
    "recommendation_id", "symbol", "generated_at", "decision_price", "price_as_of", "entry_window_start",
    "entry_window_end", "entry_trigger", "current_direct_decision",
    "decision_price_ceiling", "decision_valid_until", "current_state_vector",
    "historical_cycle_conditioning", "macro_event_conditioning",
    "derivatives_and_flow", "buy_now_vs_wait", "target_1_price_or_scenario",
    "target_2_price_or_scenario", "stop_or_invalid", "forecast_probability_pct",
    "execution_action", "observation_action",
]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_time(value: Any) -> dt.datetime | None:
    if value in (None, ""):
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = dt.datetime.combine(dt.date.fromisoformat(text), dt.time.min)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def is_nonempty(value: Any) -> bool:
    return value not in (None, "", [], {})


def baseline_snapshot_sha256(record: dict[str, Any]) -> str:
    payload = {field: record.get(field) for field in BASELINE_FIELDS}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def leveraged_etf_underlying_gate(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("leveraged_etf") is not True:
        return {
            "status": "not_applicable",
            "entry_allowed": True,
            "required_current_direct_decision": None,
            "reason_codes": [],
        }
    if record.get("underlying_symbol") and record.get("underlying_confirmed") is True:
        return {
            "status": "pass",
            "entry_allowed": True,
            "required_current_direct_decision": None,
            "reason_codes": [],
        }
    return {
        "status": "blocked",
        "entry_allowed": False,
        "required_current_direct_decision": "do_not_enter_now",
        "reason_codes": ["underlying_confirmation_missing"],
    }


def validate_holding_fields(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected = record.get("expected_holding_days")
    maximum = record.get("max_holding_days")
    expected_keys = {"min_calendar_days", "base_calendar_days", "min_trading_days", "base_trading_days"}
    maximum_keys = {"calendar_days", "trading_days"}
    if not isinstance(expected, dict) or not expected_keys.issubset(expected):
        errors.append("expected_holding_days must contain min/base calendar and trading days")
    if not isinstance(maximum, dict) or not maximum_keys.issubset(maximum):
        errors.append("max_holding_days must contain calendar_days and trading_days")
    for payload, keys, label in ((expected, expected_keys, "expected_holding_days"), (maximum, maximum_keys, "max_holding_days")):
        if not isinstance(payload, dict):
            continue
        for key in keys:
            value = payload.get(key)
            if not isinstance(value, (int, float)) or value < 0:
                errors.append(f"{label}.{key} must be non-negative numeric")
    return errors


def validate_signal_continuity(record: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    execution_action = record.get("execution_action")
    observation_action = record.get("observation_action")
    if execution_action not in schema["execution_action_values"]:
        errors.append("execution_action is not an allowed value")
    if observation_action not in schema["observation_action_values"]:
        errors.append("observation_action is not an allowed value")
    if record.get("action_allowed") != execution_action:
        errors.append("action_allowed must equal execution_action")
    if record.get("baseline_frozen") is not True:
        errors.append("baseline_frozen must be true")
    if record.get("historical_baseline_mutation_forbidden") is not True:
        errors.append("historical_baseline_mutation_forbidden must be true")
    if record.get("baseline_snapshot_sha256") != baseline_snapshot_sha256(record):
        errors.append("baseline_snapshot_sha256 does not match frozen baseline fields")
    if record.get("live_orders_enabled") is not False:
        errors.append("live_orders_enabled must be false")
    if record.get("private_api_used") is not False:
        errors.append("private_api_used must be false")
    if record.get("human_confirmation_required") is not True:
        errors.append("human_confirmation_required must be true")

    monitoring_required = record.get("impulse_monitoring_required")
    if not isinstance(monitoring_required, bool):
        errors.append("impulse_monitoring_required must be boolean")
    deadline = record.get("impulse_check_deadline")
    sentinel = schema["impulse_check_deadline_sentinel"]
    if monitoring_required is True and parse_time(deadline) is None:
        errors.append("active impulse monitoring requires an ISO impulse_check_deadline")
    if monitoring_required is True and observation_action == "no_observation":
        errors.append("active impulse monitoring requires a non-empty observation action")
    if monitoring_required is False and deadline != sentinel:
        errors.append("inactive impulse monitoring must use the documented deadline sentinel")

    matrix = record.get("candidate_coverage_matrix")
    if not isinstance(matrix, dict):
        errors.append("candidate_coverage_matrix must be an object")
        return errors
    required_roles = schema["candidate_coverage_required_roles"]
    missing_roles = [role for role in required_roles if role not in matrix]
    if missing_roles:
        errors.append(f"candidate_coverage_matrix missing roles: {','.join(missing_roles)}")
    coverage_degraded = False
    for role in required_roles:
        item = matrix.get(role)
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if status not in schema["coverage_status_values"]:
            errors.append(f"candidate_coverage_matrix.{role}.status is invalid")
            continue
        checked_at = parse_time(item.get("checked_at"))
        if checked_at is None:
            errors.append(f"candidate_coverage_matrix.{role}.checked_at must be ISO")
        else:
            generated_at = parse_time(record.get("generated_at"))
            if generated_at and checked_at > generated_at + dt.timedelta(minutes=5):
                errors.append(f"candidate_coverage_matrix.{role}.checked_at cannot be in the future")
            if generated_at and (generated_at - checked_at).total_seconds() > 24 * 3600:
                errors.append(f"candidate_coverage_matrix.{role}.checked_at is older than 24 hours")
        sources = item.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"candidate_coverage_matrix.{role}.sources must be a non-empty list")
        elif status == "verified_current" and any("missing" in str(source).lower() for source in sources):
            errors.append(f"candidate_coverage_matrix.{role}.verified_current cannot cite a missing-source sentinel")
        symbols = item.get("symbols")
        record_symbol = str(record.get("symbol") or record.get("symbol_or_pair") or "").upper()
        normalized_symbols = {str(value).upper() for value in symbols} if isinstance(symbols, list) else set()
        if not record_symbol or record_symbol not in normalized_symbols:
            errors.append(f"candidate_coverage_matrix.{role}.symbols must include the candidate symbol")
        if status in {"degraded", "missing", "not_applicable"}:
            coverage_degraded = True
    if coverage_degraded and execution_action not in {"no_deploy", "paper_only", "watch"}:
        errors.append("degraded candidate coverage caps execution_action at no_deploy/paper_only/watch")
    return errors


def validate_intraday_scalp(record: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    if record.get("request_mode") != "intraday_scalp":
        return []
    errors: list[str] = []
    required = schema.get("intraday_scalp_required_fields") or []
    for field in required:
        if not is_nonempty(record.get(field)) and record.get(field) is not False:
            errors.append(f"intraday_scalp.{field} is required")
    age = record.get("data_age_minutes")
    if not isinstance(age, (int, float)) or age < 0 or age > 1:
        errors.append("intraday_scalp quote age must be at most 60 seconds")
    generated = parse_time(record.get("generated_at"))
    valid_until = parse_time(record.get("decision_valid_until"))
    latest_close = parse_time(record.get("latest_close_at"))
    if generated and valid_until and (valid_until - generated).total_seconds() > 300:
        errors.append("intraday_scalp decision validity must be at most 5 minutes")
    if generated and latest_close:
        if latest_close <= generated:
            errors.append("intraday_scalp latest close must be after generation")
        if latest_close.astimezone(generated.tzinfo).date() != generated.date():
            errors.append("intraday_scalp must close the same day")
    if record.get("overnight_allowed") is not False:
        errors.append("intraday_scalp overnight_allowed must be false")
    if record.get("binary_event_inside_window") is not False:
        errors.append("intraday_scalp binary event inside the window is forbidden")
    return errors


def validate_record(record: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in schema["required_fields"] if not is_nonempty(record.get(field))]
    errors: list[str] = []
    for field in ["generated_at", "price_as_of", "entry_window_start", "entry_window_end", "decision_valid_until", "latest_exit_or_review_date"]:
        if field not in missing and parse_time(record.get(field)) is None:
            errors.append(f"{field} must be an ISO date or datetime")
    generated_at = parse_time(record.get("generated_at"))
    price_as_of = parse_time(record.get("price_as_of"))
    if generated_at and price_as_of:
        computed_age = (generated_at - price_as_of).total_seconds() / 60.0
        if computed_age < -5:
            errors.append("price_as_of cannot be in the future relative to generated_at")
        data_age = record.get("data_age_minutes")
        if isinstance(data_age, (int, float)):
            if data_age < 0:
                errors.append("data_age_minutes must be non-negative")
            elif abs(data_age - max(0.0, computed_age)) > 5:
                errors.append("data_age_minutes is inconsistent with generated_at and price_as_of")
        elif record.get("data_freshness_status") not in {"degraded", "stale", "missing"}:
            errors.append("non-degraded data requires numeric data_age_minutes")
    event_exit = record.get("event_exit_date")
    if event_exit not in (None, "", schema.get("event_exit_date_sentinel")) and parse_time(event_exit) is None:
        errors.append("event_exit_date must be ISO or the documented no-event sentinel")
    start = parse_time(record.get("entry_window_start"))
    end = parse_time(record.get("entry_window_end"))
    if start and end and end < start:
        errors.append("entry_window_end precedes entry_window_start")
    if record.get("allowed_session") not in schema["allowed_session_values"]:
        errors.append("allowed_session is not an allowed value")
    if record.get("data_freshness_status") not in schema["freshness_status_values"]:
        errors.append("data_freshness_status is not an allowed value")
    if record.get("no_entry_if_not_triggered") is not True:
        errors.append("no_entry_if_not_triggered must be true")
    direct_decision = record.get("current_direct_decision")
    if direct_decision not in schema["current_direct_decision_values"]:
        errors.append("current_direct_decision is not an allowed value")
    underlying_gate = leveraged_etf_underlying_gate(record)
    if underlying_gate["status"] == "blocked":
        if direct_decision != "do_not_enter_now":
            errors.append(
                "leveraged ETF missing underlying confirmation requires "
                "current_direct_decision=do_not_enter_now"
            )
        reason_codes = record.get("decision_reason_codes")
        if not isinstance(reason_codes, list) or (
            "underlying_confirmation_missing" not in reason_codes
        ):
            errors.append(
                "leveraged ETF missing underlying confirmation requires "
                "underlying_confirmation_missing reason code"
            )
        if record.get("execution_action") in {"execute_now", "conditional_action"}:
            errors.append(
                "leveraged ETF missing underlying confirmation forbids entry action"
            )
    decision_price = record.get("decision_price")
    if not isinstance(decision_price, (int, float)) or isinstance(decision_price, bool) or decision_price <= 0:
        errors.append("decision_price must be a positive numeric frozen price")
    if record.get("current_state_already_evaluated") is not True:
        errors.append("current_state_already_evaluated must be true")
    if record.get("primary_action_is_future_trigger") is not False:
        errors.append("primary_action_is_future_trigger must be false")
    if direct_decision in {"enter_now", "small_entry_now"}:
        ceiling = record.get("decision_price_ceiling")
        if not isinstance(ceiling, (int, float)) or ceiling <= 0:
            errors.append("current entry decision requires a positive numeric decision_price_ceiling")
    for field in (
        "current_state_vector",
        "historical_cycle_conditioning",
        "macro_event_conditioning",
        "derivatives_and_flow",
        "buy_now_vs_wait",
    ):
        if not isinstance(record.get(field), dict):
            errors.append(f"{field} must be an object")
    if record.get("auto_relay_forbidden") is not schema["auto_relay_forbidden_required"]:
        errors.append("auto_relay_forbidden must be true")
    if record.get("post_exit_state") != schema["post_exit_state_required"]:
        errors.append("post_exit_state must be cash_pending_manual_reallocation")
    deployable = record.get("current_deployable_cash_usd")
    if not isinstance(deployable, (int, float)) or deployable < 0:
        errors.append("current_deployable_cash_usd must be non-negative numeric")
    if record.get("action_allowed") in {"execute_now", "conditional_action"} and not (isinstance(deployable, (int, float)) and deployable > 0):
        errors.append("execute_now/conditional_action requires positive settled deployable cash; use observation_action for cash-free alerts")
    if record.get("data_freshness_status") in {"degraded", "stale", "missing"} and record.get("execution_action") in {"execute_now", "conditional_action"}:
        errors.append("degraded/stale/missing data caps execution_action below conditional_action")
    errors.extend(validate_holding_fields(record))
    errors.extend(validate_signal_continuity(record, schema))
    errors.extend(validate_intraday_scalp(record, schema))
    valid = not missing and not errors
    return {
        "recommendation_id": record.get("recommendation_id"),
        "symbol": record.get("symbol") or record.get("symbol_or_pair"),
        "valid": valid,
        "missing_fields": missing,
        "errors": errors,
        "calendar_gate_record_action": record.get("action_allowed"),
        "max_allowed_action": (
            "conditional_action" if valid and record.get("action_allowed") == "execute_now"
            else record.get("action_allowed") if valid
            else schema["missing_field_max_action"]
        ),
        "live_execution_authorized": False,
        "downstream_gates_required": ["research", "probability", "portfolio_cash", "risk", "human_confirmation"],
    }


def run_self_test(schema: dict[str, Any]) -> dict[str, Any]:
    valid = {field: "x" for field in schema["required_fields"]}
    valid.update({
        "symbol": "NIGHTUSDT",
        "generated_at": "2026-07-21T08:30:00+08:00",
        "decision_price": 0.01842,
        "price_as_of": "2026-07-21T04:00:00+08:00",
        "data_age_minutes": 270,
        "data_freshness_status": "verified_recent_close",
        "entry_window_start": "2026-07-21",
        "entry_window_end": "2026-08-06",
        "allowed_session": "us_regular_session_only",
        "no_entry_if_not_triggered": True,
        "current_direct_decision": "do_not_enter_now",
        "current_state_already_evaluated": True,
        "primary_action_is_future_trigger": False,
        "decision_price_ceiling": "not_applicable_no_entry",
        "decision_valid_until": "2026-07-21T23:30:00+08:00",
        "current_state_vector": {
            "returns_pct": {"1d": -2, "5d": 1, "20d": 4, "60d": -5, "90d": -9},
            "realized_volatility": {"20d_annualized_pct": 70},
            "volume_ratio_vs_20d": 0.9,
            "risk_regime": "neutral_to_caution",
        },
        "historical_cycle_conditioning": {
            "sample_size": 24,
            "analog_selection_rule_frozen": "trend+volatility+volume+drawdown+macro",
            "target_first_pct": 38,
            "stop_first_pct": 46,
        },
        "macro_event_conditioning": {
            "event_within_10_trading_days": True,
            "event_type": "FOMC",
            "official_event_time": "2026-07-30T02:00:00+08:00",
            "market_expected_outcome": "hold",
            "sample_size": 12,
        },
        "derivatives_and_flow": {
            "status": "verified",
            "volume_and_liquidity": {"spread_bps": 2.0},
        },
        "buy_now_vs_wait": {
            "probability_weighted_preference": "wait",
            "missed_upside_probability_pct": 38,
        },
        "expected_holding_days": {"min_calendar_days": 4, "base_calendar_days": 21, "min_trading_days": 3, "base_trading_days": 15},
        "max_holding_days": {"calendar_days": 91, "trading_days": 63},
        "event_exit_date": "2026-08-08T03:55:00+08:00",
        "latest_exit_or_review_date": "2026-10-20",
        "post_exit_state": "cash_pending_manual_reallocation",
        "auto_relay_forbidden": True,
        "current_deployable_cash_usd": 0,
        "action_allowed": "no_deploy",
        "execution_action": "no_deploy",
        "observation_action": "watch",
        "observation_trigger": "5m closed-bar breakout with volume and taker-buy confirmation; otherwise remain watch-only",
        "impulse_monitoring_required": True,
        "impulse_check_deadline": "2026-07-21T23:30:00+08:00",
        "candidate_coverage_matrix": {
            role: {
                "status": "verified_current",
                "checked_at": "2026-07-21T08:25:00+08:00",
                "sources": [f"fixture_{role}_source"],
                "symbols": ["NIGHTUSDT"],
            }
            for role in schema["candidate_coverage_required_roles"]
        },
        "baseline_frozen": True,
        "historical_baseline_mutation_forbidden": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    })
    valid["baseline_snapshot_sha256"] = baseline_snapshot_sha256(valid)
    good = validate_record(valid, schema)
    invalid = dict(valid)
    invalid.pop("entry_window_end")
    invalid["auto_relay_forbidden"] = False
    bad = validate_record(invalid, schema)
    disconnected = dict(valid)
    disconnected["execution_action"] = "conditional_action"
    disconnected["action_allowed"] = "conditional_action"
    disconnected["candidate_coverage_matrix"] = dict(valid["candidate_coverage_matrix"])
    disconnected["candidate_coverage_matrix"]["official_event"] = {
        "status": "missing",
        "checked_at": "2026-07-21T08:25:00+08:00",
        "sources": ["official_event_search_failed"],
        "symbols": ["NIGHTUSDT"],
    }
    disconnected["baseline_snapshot_sha256"] = baseline_snapshot_sha256(disconnected)
    coverage_bad = validate_record(disconnected, schema)
    tampered = dict(valid)
    tampered["price_as_of"] = "2026-07-21T05:00:00+08:00"
    tampered_bad = validate_record(tampered, schema)
    unsafe_live = dict(valid)
    unsafe_live["live_orders_enabled"] = True
    unsafe_live_bad = validate_record(unsafe_live, schema)
    future_price = dict(valid)
    future_price["price_as_of"] = "2026-07-21T09:00:00+08:00"
    future_price["data_age_minutes"] = -30
    future_price["baseline_snapshot_sha256"] = baseline_snapshot_sha256(future_price)
    future_price_bad = validate_record(future_price, schema)
    future_trigger_primary = dict(valid)
    future_trigger_primary["primary_action_is_future_trigger"] = True
    future_trigger_primary["baseline_snapshot_sha256"] = baseline_snapshot_sha256(future_trigger_primary)
    future_trigger_primary_bad = validate_record(future_trigger_primary, schema)
    passed = (
        good["valid"] is True
        and bad["valid"] is False
        and bad["max_allowed_action"] == "no_deploy"
        and coverage_bad["valid"] is False
        and tampered_bad["valid"] is False
        and unsafe_live_bad["valid"] is False
        and future_price_bad["valid"] is False
        and future_trigger_primary_bad["valid"] is False
    )
    return {
        "status": "ok" if passed else "failed",
        "valid_fixture": good,
        "invalid_fixture": bad,
        "coverage_disconnect_fixture": coverage_bad,
        "historical_mutation_fixture": tampered_bad,
        "unsafe_live_fixture": unsafe_live_bad,
        "future_price_fixture": future_price_bad,
        "future_trigger_primary_fixture": future_trigger_primary_bad,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--records", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()
    schema = load_json(args.schema)
    if args.self_test:
        payload = run_self_test(schema)
    else:
        if not args.records:
            parser.error("--records is required unless --self-test is used")
        raw = load_json(args.records)
        records = raw if isinstance(raw, list) else raw.get("recommendations") or raw.get("records") or [raw]
        results = [validate_record(item, schema) for item in records if isinstance(item, dict)]
        payload = {
            "status": "passed" if results and all(item["valid"] for item in results) else "blocked",
            "record_count": len(results),
            "valid_count": sum(1 for item in results if item["valid"]),
            "results": results,
            "live_orders_enabled": False,
        }
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"# Execution Calendar Gate\n\n- status: `{payload.get('status')}`")
    return 0 if payload.get("status") in {"ok", "passed"} else 1


if __name__ == "__main__":
    sys.exit(main())
