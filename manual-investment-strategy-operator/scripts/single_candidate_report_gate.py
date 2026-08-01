#!/usr/bin/env python3
"""Validate a primary tactical candidate plus up to two qualified alternatives."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ALLOWED_VERDICTS = {
    "small_entry_allowed",
    "wait_for_entry",
    "manage_existing_only",
    "no_qualified_trade",
}
ALLOWED_EVIDENCE_TYPES = {"FACT", "DERIVED", "JUDGMENT"}
ALLOWED_PROBABILITY_TYPES = {
    "historical_path",
    "event_estimate",
    "scenario_weight",
    "judgment_only",
    "unavailable",
}
ALLOWED_CURRENT_DECISIONS = {"enter_now", "small_entry_now", "do_not_enter_now"}
ALLOWED_MODES = {"single_best_candidate", "ranked_best_candidate"}


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _require(mapping: dict[str, Any], fields: list[str], prefix: str, errors: list[str]) -> None:
    for field in fields:
        if not _present(mapping.get(field)):
            errors.append(f"missing:{prefix}{field}")


def _parse_iso(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        errors.append(f"invalid_datetime:{field}")
        return
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"invalid_datetime:{field}")


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    mode = payload.get("mode")
    if mode not in ALLOWED_MODES:
        errors.append("invalid:mode")
    if "candidates" in payload:
        errors.append("invalid:multiple_candidates_not_allowed")

    candidate = payload.get("candidate")
    if not isinstance(candidate, dict):
        return {"status": "fail", "errors": ["missing:candidate"], "warnings": []}

    _require(
        candidate,
        [
            "symbol",
            "asset_class",
            "plain_language_description",
            "verdict",
            "current_price",
            "price_as_of",
            "price_source",
            "why_now",
            "evidence_chain",
            "entry_plan",
            "exit_plan",
            "holding_period",
            "scenarios",
            "probability_provenance",
            "invalidation",
            "funding",
            "best_candidate",
            "research_decision",
            "current_direct_decision",
            "account_state",
            "execution_decision",
            "executable_amount",
            "reward_risk_ratio",
            "realtime_signal_complete",
        ],
        "candidate.",
        errors,
    )

    if candidate.get("verdict") not in ALLOWED_VERDICTS:
        errors.append("invalid:candidate.verdict")
    direct_decision = candidate.get("current_direct_decision")
    if direct_decision not in ALLOWED_CURRENT_DECISIONS:
        errors.append("invalid:candidate.current_direct_decision")
    if candidate.get("best_candidate") != candidate.get("symbol"):
        errors.append("invalid:candidate.best_candidate")
    _parse_iso(candidate.get("price_as_of"), "candidate.price_as_of", errors)

    evidence = candidate.get("evidence_chain", [])
    if not isinstance(evidence, list) or not evidence:
        errors.append("invalid:candidate.evidence_chain")
    else:
        seen: set[str] = set()
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"invalid:evidence_chain[{index}]")
                continue
            kind = item.get("type")
            if kind not in ALLOWED_EVIDENCE_TYPES:
                errors.append(f"invalid:evidence_chain[{index}].type")
            else:
                seen.add(kind)
            _require(item, ["statement", "basis", "as_of"], f"evidence_chain[{index}].", errors)
        missing_types = ALLOWED_EVIDENCE_TYPES - seen
        if missing_types:
            errors.append("missing:evidence_types:" + ",".join(sorted(missing_types)))

    entry = candidate.get("entry_plan", {})
    if isinstance(entry, dict):
        _require(
            entry,
            ["window_start", "window_end", "allowed_session", "primary_zone", "trigger", "deadline", "do_not_buy_if"],
            "entry_plan.",
            errors,
        )
        _parse_iso(entry.get("window_start"), "entry_plan.window_start", errors)
        _parse_iso(entry.get("window_end"), "entry_plan.window_end", errors)
    else:
        errors.append("invalid:candidate.entry_plan")

    exit_plan = candidate.get("exit_plan", {})
    if isinstance(exit_plan, dict):
        _require(
            exit_plan,
            ["target_1", "target_2", "price_stop", "time_stop", "latest_exit_or_review"],
            "exit_plan.",
            errors,
        )
        _parse_iso(exit_plan.get("latest_exit_or_review"), "exit_plan.latest_exit_or_review", errors)
    else:
        errors.append("invalid:candidate.exit_plan")

    scenarios = candidate.get("scenarios", [])
    if not isinstance(scenarios, list) or len(scenarios) != 3:
        errors.append("invalid:scenarios_require_bull_base_bear")
    else:
        labels = {item.get("case") for item in scenarios if isinstance(item, dict)}
        if labels != {"bull", "base", "bear"}:
            errors.append("invalid:scenario_labels")
        total = 0.0
        for index, item in enumerate(scenarios):
            if not isinstance(item, dict):
                errors.append(f"invalid:scenarios[{index}]")
                continue
            _require(item, ["probability_pct", "price_path", "window", "drivers"], f"scenarios[{index}].", errors)
            try:
                total += float(item.get("probability_pct", 0))
            except (TypeError, ValueError):
                errors.append(f"invalid:scenarios[{index}].probability_pct")
        if abs(total - 100.0) > 0.01:
            errors.append(f"invalid:scenario_probability_sum:{total}")

    provenance = candidate.get("probability_provenance", {})
    if isinstance(provenance, dict):
        _require(
            provenance,
            [
                "probability_type",
                "method",
                "sample_size",
                "base_rate",
                "adjustments",
                "limitations",
                "calibration_status",
                "conservative_expected_value_pct",
            ],
            "probability_provenance.",
            errors,
        )
        probability_type = provenance.get("probability_type")
        if probability_type not in ALLOWED_PROBABILITY_TYPES:
            errors.append("invalid:probability_provenance.probability_type")
        sample_size = provenance.get("sample_size")
        calibration = provenance.get("calibration_status")
        if isinstance(sample_size, (int, float)) and sample_size < 10:
            if direct_decision != "do_not_enter_now":
                errors.append("invalid:judgment_only_cannot_allow_entry")
        elif isinstance(sample_size, (int, float)) and sample_size < 30:
            interval = provenance.get("probability_range_pct")
            if calibration != "wide_interval" or not isinstance(interval, list) or len(interval) != 2:
                errors.append("invalid:n_10_29_requires_wide_interval")
            if direct_decision == "enter_now":
                errors.append("invalid:wide_interval_max_small_entry_now")
            warnings.append("low_sample_size_use_wide_probability_range")
        elif isinstance(sample_size, (int, float)):
            if direct_decision == "enter_now" and (
                calibration != "calibrated"
                or provenance.get("untouched_holdout") is not True
                or provenance.get("lookahead_free") is not True
            ):
                errors.append("invalid:enter_now_requires_calibrated_untouched_holdout")
        if direct_decision in {"enter_now", "small_entry_now"}:
            ev = provenance.get("conservative_expected_value_pct")
            if not isinstance(ev, (int, float)) or ev <= 0:
                errors.append("invalid:entry_requires_positive_conservative_ev")
            if not isinstance(candidate.get("reward_risk_ratio"), (int, float)) or candidate["reward_risk_ratio"] < 2:
                errors.append("invalid:entry_requires_reward_risk_gte_2")
            if candidate.get("realtime_signal_complete") is not True:
                errors.append("invalid:entry_requires_complete_realtime_signal")
    else:
        errors.append("invalid:candidate.probability_provenance")

    funding = candidate.get("funding", {})
    if isinstance(funding, dict):
        _require(funding, ["deployable_cash", "cash_source", "position_size", "settlement_constraint"], "funding.", errors)
        cash = funding.get("deployable_cash")
        expected_amount = cash if candidate.get("execution_decision") == "manual_execute_candidate" else 0
        if candidate.get("executable_amount") != expected_amount:
            errors.append("invalid:executable_amount_mismatch")
        if (not isinstance(cash, (int, float)) or cash <= 0) and candidate.get("execution_decision") != "no_deploy_cash":
            errors.append("invalid:zero_cash_requires_no_deploy_cash")
    else:
        errors.append("invalid:candidate.funding")

    ranking_context = payload.get("ranking_context")
    if mode == "ranked_best_candidate":
        if not isinstance(ranking_context, dict):
            errors.append("missing:ranking_context")
            ranking_context = {}
        else:
            _require(
                ranking_context,
                [
                    "evidence_snapshot_id",
                    "strategy_version",
                    "generated_at",
                    "ranking_method",
                    "short_term_business_goal",
                    "historical_validation_first",
                    "deterministic_input_hash",
                    "primary_rank_reason",
                ],
                "ranking_context.",
                errors,
            )
            _parse_iso(
                ranking_context.get("generated_at"),
                "ranking_context.generated_at",
                errors,
            )
            if ranking_context.get("historical_validation_first") is not True:
                errors.append("invalid:ranking_context.historical_validation_first")
            if ranking_context.get("short_term_business_goal") != (
                "tactical_sleeve_monthly_roi_100pct_attack_goal"
            ):
                errors.append("invalid:ranking_context.short_term_business_goal")

    runners_up = payload.get("runners_up", [])
    if not isinstance(runners_up, list) or len(runners_up) > 2:
        errors.append("invalid:runners_up_max_two")
        runners_up = []
    if mode == "ranked_best_candidate":
        seen_symbols = {str(candidate.get("symbol") or "")}
        snapshot_id = (
            ranking_context.get("evidence_snapshot_id")
            if isinstance(ranking_context, dict)
            else None
        )
        for index, alternative in enumerate(runners_up):
            prefix = f"runners_up[{index}]."
            if not isinstance(alternative, dict):
                errors.append(f"invalid:runners_up[{index}]")
                continue
            _require(
                alternative,
                [
                    "rank",
                    "symbol",
                    "asset_class",
                    "sample_size",
                    "historical_win_rate_pct",
                    "win_rate_interval_pct",
                    "conservative_expected_value_pct",
                    "expected_return_pct",
                    "max_drawdown_pct",
                    "profit_factor",
                    "reward_risk_ratio",
                    "liquidity_status",
                    "current_direct_decision",
                    "decision_valid_until",
                    "evidence_snapshot_id",
                    "why_ranked_lower",
                ],
                prefix,
                errors,
            )
            symbol = str(alternative.get("symbol") or "")
            if symbol in seen_symbols:
                errors.append(f"invalid:{prefix}duplicate_symbol")
            seen_symbols.add(symbol)
            if alternative.get("rank") != index + 2:
                errors.append(f"invalid:{prefix}rank")
            if alternative.get("evidence_snapshot_id") != snapshot_id:
                errors.append(f"invalid:{prefix}snapshot_mismatch")
            _parse_iso(
                alternative.get("decision_valid_until"),
                f"{prefix}decision_valid_until",
                errors,
            )
            sample_size = alternative.get("sample_size")
            if not isinstance(sample_size, int) or isinstance(sample_size, bool) or sample_size < 30:
                errors.append(f"invalid:{prefix}sample_size_min_30")
            win_rate = alternative.get("historical_win_rate_pct")
            if not isinstance(win_rate, (int, float)) or isinstance(win_rate, bool) or not 0 <= win_rate <= 100:
                errors.append(f"invalid:{prefix}historical_win_rate_pct")
            interval = alternative.get("win_rate_interval_pct")
            if (
                not isinstance(interval, list)
                or len(interval) != 2
                or not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in interval)
                or interval[0] >= interval[1]
            ):
                errors.append(f"invalid:{prefix}win_rate_interval_pct")
            conservative_ev = alternative.get("conservative_expected_value_pct")
            if not isinstance(conservative_ev, (int, float)) or conservative_ev <= 0:
                errors.append(f"invalid:{prefix}positive_conservative_ev_required")
            reward_risk = alternative.get("reward_risk_ratio")
            if not isinstance(reward_risk, (int, float)) or reward_risk < 2:
                errors.append(f"invalid:{prefix}reward_risk_gte_2_required")
            profit_factor = alternative.get("profit_factor")
            if not isinstance(profit_factor, (int, float)) or profit_factor <= 1:
                errors.append(f"invalid:{prefix}profit_factor_gt_1_required")
            if alternative.get("current_direct_decision") not in ALLOWED_CURRENT_DECISIONS:
                errors.append(f"invalid:{prefix}current_direct_decision")
            reasons = alternative.get("why_ranked_lower")
            if not isinstance(reasons, list) or not reasons:
                errors.append(f"invalid:{prefix}why_ranked_lower")

    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
        "allowed_max_action": candidate.get("verdict") if not errors else "no_qualified_trade",
        "primary_symbol": candidate.get("symbol") if not errors else None,
        "qualified_alternative_count": len(runners_up) if not errors else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = validate(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
