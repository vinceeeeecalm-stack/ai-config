#!/usr/bin/env python3
"""Validate historical-cycle, event-conditioning and current-decision fields.

This gate is read-only. It never places orders or mutates a portfolio ledger.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ALLOWED_DECISIONS = {"enter_now", "small_entry_now", "do_not_enter_now"}
ENTRY_DECISIONS = {"enter_now", "small_entry_now"}
REQUIRED_TOP_LEVEL = {
    "symbol",
    "asset_class",
    "generated_at",
    "price_as_of",
    "current_price",
    "current_direct_decision",
    "current_state_already_evaluated",
    "primary_action_is_future_trigger",
    "decision_price_ceiling",
    "decision_valid_until",
    "current_state_vector",
    "historical_cycle_conditioning",
    "macro_event_conditioning",
    "derivatives_and_flow",
    "buy_now_vs_wait",
    "probability_provenance",
    "current_deployable_cash_usd",
    "execution_action",
}
REQUIRED_LOOKBACKS = {"1d", "5d", "20d", "60d", "90d"}
REQUIRED_HISTORICAL = {
    "data_start",
    "data_end",
    "analog_selection_rule_frozen",
    "feature_vector",
    "sample_size",
    "target_first_count",
    "stop_first_count",
    "unresolved_count",
    "target_first_pct",
    "stop_first_pct",
    "median_forward_return_pct",
    "median_mfe_pct",
    "median_mae_pct",
    "pullback_before_target_pct",
    "missed_upside_if_wait_pct",
    "net_expectancy_after_friction_pct",
    "profit_factor",
    "max_drawdown_pct",
    "walk_forward_positive_windows",
    "walk_forward_total_windows",
    "untouched_holdout",
    "lookahead_free",
    "simulation_evidence_label",
    "limitations",
}
REQUIRED_EVENT = {
    "event_within_10_trading_days",
    "event_type",
    "official_event_time",
    "market_expected_outcome",
    "consecutive_same_decision_count",
    "pre_event_regime",
    "analog_filter_frozen",
    "sample_size",
    "event_windows",
    "target_stop_path",
    "limitations",
}
REQUIRED_BUY_WAIT = {
    "buy_now_expected_path",
    "wait_expected_discount_pct",
    "wait_fill_probability_pct",
    "missed_upside_probability_pct",
    "time_in_market_cost_pct",
    "event_gap_risk_pct",
    "probability_weighted_preference",
}


def present(value: Any) -> bool:
    return value not in (None, "", [], {})


def parse_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def missing_fields(payload: dict[str, Any], required: set[str], prefix: str) -> list[str]:
    return [f"missing:{prefix}{field}" for field in sorted(required) if not present(payload.get(field))]


def validate_percentage(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, (int, float)) or not 0 <= float(value) <= 100:
        errors.append(f"invalid_percentage:{field}")


def validate_record(record: dict[str, Any]) -> dict[str, Any]:
    errors = missing_fields(record, REQUIRED_TOP_LEVEL, "")
    warnings: list[str] = []

    generated_at = parse_time(record.get("generated_at"))
    price_as_of = parse_time(record.get("price_as_of"))
    valid_until = parse_time(record.get("decision_valid_until"))
    if generated_at is None:
        errors.append("invalid_datetime:generated_at")
    if price_as_of is None:
        errors.append("invalid_datetime:price_as_of")
    if valid_until is None:
        errors.append("invalid_datetime:decision_valid_until")
    if generated_at and price_as_of and price_as_of > generated_at + dt.timedelta(minutes=5):
        errors.append("price_as_of_after_generated_at")
    if generated_at and valid_until and valid_until <= generated_at:
        errors.append("decision_window_already_expired_at_generation")

    decision = record.get("current_direct_decision")
    if decision not in ALLOWED_DECISIONS:
        errors.append("invalid:current_direct_decision")
    if record.get("current_state_already_evaluated") is not True:
        errors.append("current_state_already_evaluated_must_be_true")
    if record.get("primary_action_is_future_trigger") is not False:
        errors.append("primary_action_is_future_trigger_must_be_false")
    if decision in ENTRY_DECISIONS:
        ceiling = record.get("decision_price_ceiling")
        if not isinstance(ceiling, (int, float)) or ceiling <= 0:
            errors.append("entry_decision_requires_numeric_decision_price_ceiling")

    state = record.get("current_state_vector")
    if not isinstance(state, dict):
        errors.append("invalid:current_state_vector")
    else:
        returns = state.get("returns_pct")
        if not isinstance(returns, dict):
            errors.append("invalid:current_state_vector.returns_pct")
        else:
            absent = REQUIRED_LOOKBACKS - set(returns)
            if absent:
                errors.append("missing_lookbacks:" + ",".join(sorted(absent)))
        for field in ("realized_volatility", "volume_ratio_vs_20d", "risk_regime"):
            if not present(state.get(field)):
                errors.append(f"missing:current_state_vector.{field}")

    historical = record.get("historical_cycle_conditioning")
    sample_size = 0
    if not isinstance(historical, dict):
        errors.append("invalid:historical_cycle_conditioning")
    else:
        errors.extend(missing_fields(historical, REQUIRED_HISTORICAL, "historical_cycle_conditioning."))
        sample_size = historical.get("sample_size", 0)
        counts = [
            historical.get("target_first_count"),
            historical.get("stop_first_count"),
            historical.get("unresolved_count"),
        ]
        if not isinstance(sample_size, int) or sample_size < 0:
            errors.append("invalid:historical_cycle_conditioning.sample_size")
            sample_size = 0
        if not all(isinstance(value, int) and value >= 0 for value in counts):
            errors.append("invalid:historical_cycle_conditioning.path_counts")
        elif sum(counts) != sample_size:
            errors.append("historical_path_counts_do_not_sum_to_sample_size")
        validate_percentage(historical.get("target_first_pct"), "target_first_pct", errors)
        validate_percentage(historical.get("stop_first_pct"), "stop_first_pct", errors)
        if isinstance(sample_size, int) and sample_size < 30:
            warnings.append("historical_sample_below_30_requires_wide_probability_range")
        if isinstance(sample_size, int) and sample_size < 10 and decision in ENTRY_DECISIONS:
            errors.append("historical_sample_below_10_blocks_current_entry")
        if 10 <= sample_size < 30 and decision == "enter_now":
            errors.append("historical_sample_10_29_max_small_entry_now")
        net_expectancy = historical.get("net_expectancy_after_friction_pct")
        profit_factor = historical.get("profit_factor")
        if decision in ENTRY_DECISIONS:
            if not isinstance(net_expectancy, (int, float)) or net_expectancy <= 0:
                errors.append("historical_entry_requires_positive_net_expectancy")
            if not isinstance(profit_factor, (int, float)) or profit_factor <= 1:
                errors.append("historical_entry_requires_profit_factor_above_1")
        positive_windows = historical.get("walk_forward_positive_windows")
        total_windows = historical.get("walk_forward_total_windows")
        if (
            not isinstance(positive_windows, int)
            or not isinstance(total_windows, int)
            or total_windows < 0
            or positive_windows < 0
            or positive_windows > total_windows
            or (sample_size > 0 and total_windows == 0)
        ):
            errors.append("historical_walk_forward_window_counts_invalid")
        if sample_size >= 30 and decision in ENTRY_DECISIONS and (
            historical.get("untouched_holdout") is not True
            or historical.get("lookahead_free") is not True
        ):
            errors.append("historical_calibrated_entry_requires_untouched_holdout_no_lookahead")
        if historical.get("simulation_evidence_label") not in {
            "historical_simulation",
            "walk_forward_out_of_sample",
            "unavailable",
        }:
            errors.append("historical_simulation_evidence_label_required")

    event = record.get("macro_event_conditioning")
    if not isinstance(event, dict):
        errors.append("invalid:macro_event_conditioning")
    elif event.get("event_within_10_trading_days") is True:
        errors.extend(missing_fields(event, REQUIRED_EVENT, "macro_event_conditioning."))
        if parse_time(event.get("official_event_time")) is None:
            errors.append("invalid_datetime:macro_event_conditioning.official_event_time")
        event_sample = event.get("sample_size")
        if not isinstance(event_sample, int) or event_sample < 0:
            errors.append("invalid:macro_event_conditioning.sample_size")
        elif event_sample < 10 and decision in ENTRY_DECISIONS:
            errors.append("event_sample_below_10_blocks_current_entry")
        elif event_sample < 30 and decision == "enter_now":
            errors.append("event_sample_10_29_max_small_entry_now")
    elif event.get("event_within_10_trading_days") is not False:
        errors.append("macro_event_conditioning.event_within_10_trading_days_must_be_boolean")

    derivatives = record.get("derivatives_and_flow")
    if not isinstance(derivatives, dict):
        errors.append("invalid:derivatives_and_flow")
    else:
        status = derivatives.get("status")
        if status not in {"verified", "degraded", "missing", "not_applicable"}:
            errors.append("invalid:derivatives_and_flow.status")
        if status in {"degraded", "missing"} and decision in ENTRY_DECISIONS:
            errors.append("missing_or_degraded_derivatives_blocks_current_entry")
        if not present(derivatives.get("volume_and_liquidity")):
            errors.append("missing:derivatives_and_flow.volume_and_liquidity")

    buy_wait = record.get("buy_now_vs_wait")
    if not isinstance(buy_wait, dict):
        errors.append("invalid:buy_now_vs_wait")
    else:
        errors.extend(missing_fields(buy_wait, REQUIRED_BUY_WAIT, "buy_now_vs_wait."))
        if buy_wait.get("probability_weighted_preference") not in {"buy_now", "wait", "indifferent"}:
            errors.append("invalid:buy_now_vs_wait.probability_weighted_preference")
        if decision in ENTRY_DECISIONS and buy_wait.get("probability_weighted_preference") != "buy_now":
            errors.append("entry_decision_conflicts_with_buy_now_vs_wait_result")
        if decision == "do_not_enter_now" and buy_wait.get("probability_weighted_preference") == "buy_now":
            warnings.append("do_not_enter_now_despite_buy_now_preference_requires_explicit_risk_or_cash_independent_block")

    provenance = record.get("probability_provenance")
    if not isinstance(provenance, dict):
        errors.append("invalid:probability_provenance")
    else:
        for field in ("probability_type", "base_rate", "adjustments", "limitations"):
            if not present(provenance.get(field)):
                errors.append(f"missing:probability_provenance.{field}")
        lower = provenance.get("range_low_pct")
        upper = provenance.get("range_high_pct")
        if sample_size < 20:
            if not all(isinstance(value, (int, float)) for value in (lower, upper)):
                errors.append("low_sample_requires_probability_range")
            elif upper - lower < 15:
                errors.append("low_sample_probability_range_must_be_at_least_15pp")

    cash = record.get("current_deployable_cash_usd")
    if not isinstance(cash, (int, float)) or cash < 0:
        errors.append("invalid:current_deployable_cash_usd")
    if cash == 0 and record.get("execution_action") in {"execute_now", "conditional_action"}:
        errors.append("zero_cash_requires_no_deploy_execution_action")

    return {
        "symbol": record.get("symbol"),
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
        "current_direct_decision": decision,
        "execution_action": record.get("execution_action"),
        "live_execution_authorized": False,
    }


def self_test() -> dict[str, Any]:
    fixture = {
        "symbol": "SOLUSDT",
        "asset_class": "crypto",
        "generated_at": "2026-07-24T10:00:00+08:00",
        "price_as_of": "2026-07-24T09:59:00+08:00",
        "current_price": 75.8,
        "current_direct_decision": "do_not_enter_now",
        "current_state_already_evaluated": True,
        "primary_action_is_future_trigger": False,
        "decision_price_ceiling": "not_applicable_no_entry",
        "decision_valid_until": "2026-07-24T23:30:00+08:00",
        "current_state_vector": {
            "returns_pct": {"1d": -3, "5d": 1, "20d": 2, "60d": -8, "90d": -12},
            "realized_volatility": {"20d_annualized_pct": 75},
            "volume_ratio_vs_20d": 0.88,
            "risk_regime": "neutral_to_caution",
        },
        "historical_cycle_conditioning": {
            "data_start": "2021-01-01",
            "data_end": "2026-07-23",
            "analog_selection_rule_frozen": "trend+volatility+volume+drawdown+macro",
            "feature_vector": {"trend": "mixed", "volume": "below_median"},
            "sample_size": 26,
            "target_first_count": 9,
            "stop_first_count": 12,
            "unresolved_count": 5,
            "target_first_pct": 34.6,
            "stop_first_pct": 46.2,
            "median_forward_return_pct": 0.9,
            "median_mfe_pct": 3.3,
            "median_mae_pct": -5.1,
            "pullback_before_target_pct": 65.4,
            "missed_upside_if_wait_pct": 34.6,
            "net_expectancy_after_friction_pct": 0.4,
            "profit_factor": 1.08,
            "max_drawdown_pct": -12.0,
            "walk_forward_positive_windows": 3,
            "walk_forward_total_windows": 5,
            "untouched_holdout": False,
            "lookahead_free": True,
            "simulation_evidence_label": "walk_forward_out_of_sample",
            "limitations": ["FOMC-conditioned sample handled separately"],
        },
        "macro_event_conditioning": {
            "event_within_10_trading_days": True,
            "event_type": "FOMC",
            "official_event_time": "2026-07-30T02:00:00+08:00",
            "market_expected_outcome": "hold",
            "consecutive_same_decision_count": 2,
            "pre_event_regime": {"DXY": "firm", "VIX": "elevated"},
            "analog_filter_frozen": "consecutive holds + similar VIX/DXY bucket",
            "sample_size": 12,
            "event_windows": ["-5d", "-1d", "+1d", "+3d", "+5d", "+10d"],
            "target_stop_path": {"target_first_pct": 42, "stop_first_pct": 50},
            "limitations": ["small conditional sample"],
        },
        "derivatives_and_flow": {
            "status": "verified",
            "volume_and_liquidity": {"spread_bps": 1.3, "volume_ratio": 0.88},
            "funding": -0.000002,
            "open_interest_usd": 351000000,
        },
        "buy_now_vs_wait": {
            "buy_now_expected_path": "negative asymmetry before FOMC",
            "wait_expected_discount_pct": 3.0,
            "wait_fill_probability_pct": 65,
            "missed_upside_probability_pct": 35,
            "time_in_market_cost_pct": 0.09,
            "event_gap_risk_pct": 7.0,
            "probability_weighted_preference": "wait",
        },
        "probability_provenance": {
            "probability_type": "historical_path_plus_event_conditioning",
            "base_rate": "34.6% target-first",
            "adjustments": ["no upward qualitative adjustment"],
            "limitations": ["event sample is small"],
            "range_low_pct": 30,
            "range_high_pct": 45,
        },
        "current_deployable_cash_usd": 0,
        "execution_action": "no_deploy",
    }
    good = validate_record(fixture)
    bad = dict(fixture)
    bad["current_direct_decision"] = "small_entry_now"
    bad["primary_action_is_future_trigger"] = True
    bad["historical_cycle_conditioning"] = dict(fixture["historical_cycle_conditioning"])
    bad["historical_cycle_conditioning"]["sample_size"] = 8
    bad["historical_cycle_conditioning"]["target_first_count"] = 3
    bad["historical_cycle_conditioning"]["stop_first_count"] = 4
    bad["historical_cycle_conditioning"]["unresolved_count"] = 1
    bad["buy_now_vs_wait"] = dict(fixture["buy_now_vs_wait"])
    bad["buy_now_vs_wait"]["probability_weighted_preference"] = "wait"
    invalid = validate_record(bad)
    mid_sample = dict(fixture)
    mid_sample["current_direct_decision"] = "small_entry_now"
    mid_sample["current_deployable_cash_usd"] = 100
    mid_sample["execution_action"] = "paper_only"
    mid_sample["decision_price_ceiling"] = 76.0
    mid_sample["buy_now_vs_wait"] = dict(fixture["buy_now_vs_wait"])
    mid_sample["buy_now_vs_wait"]["probability_weighted_preference"] = "buy_now"
    mid_sample["historical_cycle_conditioning"] = dict(fixture["historical_cycle_conditioning"])
    mid_sample["historical_cycle_conditioning"]["sample_size"] = 15
    mid_sample["historical_cycle_conditioning"]["target_first_count"] = 6
    mid_sample["historical_cycle_conditioning"]["stop_first_count"] = 7
    mid_sample["historical_cycle_conditioning"]["unresolved_count"] = 2
    mid_sample["macro_event_conditioning"] = dict(fixture["macro_event_conditioning"])
    mid_sample["macro_event_conditioning"]["sample_size"] = 15
    mid_sample_result = validate_record(mid_sample)
    return {
        "status": (
            "ok"
            if good["status"] == "pass"
            and invalid["status"] == "fail"
            and mid_sample_result["status"] == "pass"
            else "failed"
        ),
        "valid_fixture": good,
        "invalid_fixture": invalid,
        "mid_sample_wide_interval_fixture": mid_sample_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        result: Any = self_test()
    else:
        if not args.input:
            parser.error("--input is required unless --self-test is used")
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        records = raw if isinstance(raw, list) else raw.get("recommendations") or [raw]
        results = [validate_record(item) for item in records if isinstance(item, dict)]
        result = {
            "status": "pass" if results and all(item["status"] == "pass" for item in results) else "fail",
            "results": results,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"ok", "pass"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
