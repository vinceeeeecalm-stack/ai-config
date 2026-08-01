#!/usr/bin/env python3
"""Evaluate the pre-entry downside and capital-risk gate for a US candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "manual_strategy_config.json"


def read_json(path_text: str) -> dict:
    if path_text == "-":
        return json.load(sys.stdin)
    with Path(path_text).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def evaluate(candidate: dict, config: dict) -> dict:
    gate = config["pre_entry_downside_and_capital_risk_gate"]
    required = gate["required_candidate_fields"]
    missing = [field for field in required if candidate.get(field) in (None, "", [])]
    failed: list[str] = []
    warnings: list[str] = []

    target_first = candidate.get("target_before_stop_probability_pct")
    stop_first = candidate.get("stop_before_target_probability_pct")
    reward_risk = candidate.get("reward_risk_ratio")
    days_to_event = candidate.get("days_to_binary_event")
    event_underwritten = bool(candidate.get("event_trade_underwritten", False))
    price_20d = candidate.get("price_change_20d_pct")
    price_60d = candidate.get("price_change_60d_pct")
    distance_high = candidate.get("distance_from_52w_high_pct")
    share_growth = candidate.get("share_count_yoy_pct")
    financing_flags = candidate.get("recent_financing_flags") or []
    funding_gap = candidate.get("capital_funding_gap_status")
    stock_comp_pct = candidate.get("stock_comp_pct_of_revenue")
    news_failures = int(candidate.get("positive_news_failure_count", 0) or 0)
    thresholds = gate["thresholds"]

    if target_first is not None and stop_first is not None and stop_first >= target_first:
        failed.append("stop_before_target_probability_not_lower_than_target_before_stop")
    if reward_risk is not None:
        if reward_risk < thresholds["minimum_reward_risk_no_deploy"]:
            failed.append("reward_risk_below_no_deploy_threshold")
        elif reward_risk < thresholds["minimum_reward_risk_full_size"]:
            warnings.append("reward_risk_allows_small_probe_only")
    if days_to_event is not None and days_to_event <= thresholds["binary_event_full_size_block_days"] and not event_underwritten:
        warnings.append("binary_event_inside_five_days_not_underwritten")

    extended = (
        price_20d is not None
        and price_60d is not None
        and distance_high is not None
        and (price_20d >= thresholds["extended_20d_return_pct"]
             or price_60d >= thresholds["extended_60d_return_pct"])
        and distance_high >= -thresholds["near_52w_high_pct"]
    )
    if extended:
        warnings.append("extended_chase_risk")

    severe_capital_flags: list[str] = []
    if share_growth is not None and share_growth > thresholds["severe_share_count_growth_yoy_pct"]:
        severe_capital_flags.append("share_count_growth")
    if financing_flags:
        severe_capital_flags.append("recent_financing_or_dilution")
    if funding_gap in {"uncovered", "material", "unknown_material"}:
        severe_capital_flags.append("capital_funding_gap")
    if stock_comp_pct is not None and stock_comp_pct > thresholds["severe_stock_comp_pct_of_revenue"]:
        severe_capital_flags.append("high_stock_compensation")
    if len(severe_capital_flags) >= thresholds["severe_capital_flags_to_block"]:
        failed.append("multiple_severe_capital_risk_flags")
    elif severe_capital_flags:
        warnings.append("one_severe_capital_risk_flag")

    if news_failures >= thresholds["positive_news_failures_to_block_add"]:
        failed.append("repeated_positive_news_failure")
    elif news_failures == 1:
        warnings.append("single_positive_news_failure")

    if missing:
        max_action = "watch"
        decision = "incomplete_downside_evidence"
    elif failed:
        max_action = "no_deploy"
        decision = "blocked"
    elif warnings:
        max_action = "small_probe_review"
        decision = "conditional_only"
    else:
        max_action = "continue_to_target_achievement_gate"
        decision = "passed"

    return {
        "symbol": candidate.get("symbol"),
        "gate": "pre_entry_downside_and_capital_risk_gate",
        "decision": decision,
        "max_allowed_action": max_action,
        "missing_fields": missing,
        "failed_gates": failed,
        "warnings": warnings,
        "severe_capital_flags": severe_capital_flags,
        "human_confirmation_required": True,
        "live_order_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Candidate JSON path or - for stdin")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output")
    args = parser.parse_args()

    candidate = read_json(args.input)
    config = read_json(args.config)
    result = evaluate(candidate, config)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
