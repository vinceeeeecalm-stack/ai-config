#!/usr/bin/env python3
"""Audit a report against the user's top-level investment objective.

This is stricter than the report integrity audit. It verifies that the report
is not merely well-formed, but tied back to the actual operating objective:
5y/10y 10x goal math, $1k crypto DCA, US tactical 50%+ target tracking,
separate cash rails, dynamic candidate/relay logic, and a reviewable learning
ledger. It does not claim the return target is achievable or guaranteed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
DEFAULT_LEDGER = MANUAL_ROOT / "recommendations" / "recommendation_history.json"
DEFAULT_CONFIG = MANUAL_ROOT / "config" / "manual_strategy_config.json"
DEFAULT_PAPER_LEDGER = ROOT / "active-alpha-paper-monitor" / "paper_trades" / "paper_portfolio_ledger.json"


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def nested_get(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def check(name: str, passed: bool, evidence: str, severity: str = "error") -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "severity": severity,
        "evidence": evidence,
    }


def extract_run_id(report_text: str, context: dict[str, Any], explicit: str | None) -> str | None:
    if explicit:
        return explicit
    research = context.get("research_panel") or {}
    if research.get("run_id"):
        return research.get("run_id")
    match = re.search(r"\| run_id \| `([^`]+)` \|", report_text)
    return match.group(1) if match else None


def record_missing(record: dict[str, Any], fields: set[str]) -> list[str]:
    return sorted(field for field in fields if record.get(field) in (None, ""))


def records_for_run(ledger: dict[str, Any], run_id: str | None) -> list[dict[str, Any]]:
    if not run_id:
        return []
    return [
        item
        for item in ledger.get("recommendations", [])
        if isinstance(item, dict) and item.get("run_id") == run_id
    ]


def paper_ledger_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "missing",
            "path": str(path),
            "open_count": 0,
            "closed_count": 0,
            "total_count": 0,
            "hit_count": 0,
            "failed_count": 0,
            "win_rate_pct": None,
            "net_return_pct": None,
            "max_drawdown_pct": None,
            "live_orders_enabled": None,
        }
    ledger = load_json(path)
    open_positions = ledger.get("open_positions") or []
    closed_trades = ledger.get("closed_trades") or []
    hit_count = sum(1 for trade in closed_trades if trade.get("outcome") in {"hit", "take_profit", "profit"})
    failed_count = sum(1 for trade in closed_trades if trade.get("outcome") in {"failed", "stop", "expired", "invalidated"})
    resolved = hit_count + failed_count
    realized_pnl = sum(as_float(trade.get("realized_pnl_usd"), 0.0) or 0.0 for trade in closed_trades)
    return {
        "status": "ok",
        "path": str(path),
        "open_count": len(open_positions),
        "closed_count": len(closed_trades),
        "total_count": len(open_positions) + len(closed_trades),
        "hit_count": hit_count,
        "failed_count": failed_count,
        "resolved_count": resolved,
        "win_rate_pct": (hit_count / resolved * 100.0) if resolved else None,
        "realized_pnl_usd": realized_pnl,
        "cash_usd": ledger.get("cash_usd"),
        "open_value_usd": ledger.get("open_value_usd"),
        "equity_usd": ledger.get("equity_usd"),
        "net_return_pct": ledger.get("net_return_pct"),
        "max_drawdown_pct": ledger.get("max_drawdown_pct"),
        "initial_capital_usd": ledger.get("initial_capital_usd"),
        "live_orders_enabled": ledger.get("live_orders_enabled"),
        "updated_at": ledger.get("updated_at"),
    }


def audit(
    report_path: Path,
    context_path: Path,
    ledger_path: Path,
    config_path: Path,
    paper_ledger_path: Path,
    run_id: str | None = None,
    expected_monthly_dca: float = 1000.0,
) -> dict[str, Any]:
    report_text = report_path.read_text(encoding="utf-8")
    context = load_json(context_path)
    ledger = load_json(ledger_path) if ledger_path.exists() else {"recommendations": []}
    config = load_json(config_path) if config_path.exists() else {}
    resolved_run_id = extract_run_id(report_text, context, run_id)
    records = records_for_run(ledger, resolved_run_id)

    portfolio = context.get("portfolio_snapshot") or {}
    projection = context.get("goal_path_projection") or {}
    required = projection.get("required") or {}
    asset_goal = nested_get(context, "asset_goal_contribution_panel", "summary") or {}
    price_scenario = nested_get(context, "long_term_price_scenario_panel", "summary") or {}
    price_scenario_assets = price_scenario.get("assets") or []
    dca_guidance = asset_goal.get("dca_guidance") or {}
    dca_timing_decisions = dca_guidance.get("long_horizon_timing_decisions") or []
    dca_timing_symbols = {item.get("symbol") for item in dca_timing_decisions if isinstance(item, dict)}
    macro = nested_get(context, "macro_regime_panel", "summary") or {}
    crypto = nested_get(context, "crypto_market_panel", "summary") or {}
    us_tactical = nested_get(context, "us_tactical_performance_panel", "summary") or {}
    us_scanner = nested_get(context, "us_open_dynamic_scanner_panel", "summary") or {}
    fresh_market = context.get("fresh_market_intelligence_snapshot") or {}
    research = context.get("research_panel") or {}
    research_validation = research.get("external_agent_validation") or {}
    readiness = context.get("report_readiness") or {}
    rec_write = context.get("recommendation_history_write") or {}
    rec_panel = context.get("recommendation_history_summary") or {}
    review_drafts = context.get("recommendation_outcome_review_drafts") or {}
    promotion_panel = context.get("strategy_promotion_evidence_panel") or {}
    strategy_iteration = context.get("strategy_iteration_backlog_panel") or {}
    config_cash_rails = config.get("cash_rails") or {}
    config_fresh_gate = config.get("manual_dispatch_market_sentiment_refresh_gate") or {}
    config_full_market_gate = config.get("full_market_deep_analysis_objective_gate") or {}
    config_progressive_learning = config.get("progressive_learning_confidence_policy") or {}
    config_traceability = config.get("objective_traceability_gate") or {}
    config_price_scenario = config.get("long_term_price_scenario_panel") or {}
    paper_metrics = paper_ledger_metrics(paper_ledger_path)

    dca_records = [
        item for item in records
        if item.get("capital_sleeve") in {"crypto_dca_sleeve", "crypto_tail_convexity_sleeve"}
    ]
    tactical_records = [
        item for item in records
        if item.get("capital_sleeve") == "tactical_alpha_sleeve"
        or item.get("bucket") == "us_tactical_alpha_sleeve"
    ]
    dca_required = {
        "entry_range",
        "secondary_entry",
        "optimal_entry",
        "entry_deadline",
        "target_range",
        "target_time_window",
        "latest_exit_or_review_date",
        "position_size_plan",
        "planned_amount_usd_low",
        "planned_amount_usd_high",
        "planned_near_amount_usd",
        "planned_pullback_add_usd",
        "cash_rail_source",
        "action_source",
        "long_term_price_scenario",
        "long_horizon_timing_decision",
        "dca_timing_decision",
        "staking_wait_cost_usd",
        "required_pullback_to_wait_pct",
        "front_load_extra_months",
        "front_load_amount_usd",
        "near_term_total_budget_usd",
        "long_term_low_value_zone_status",
        "cash_idle_drag_comment",
        "price_as_of",
        "price_source",
        "data_age_minutes",
        "data_freshness_status",
        "entry_window_start",
        "entry_window_end",
        "allowed_session",
        "entry_trigger",
        "no_entry_if_not_triggered",
        "expected_holding_days",
        "max_holding_days",
        "target_1_price_or_scenario",
        "target_1_evaluation_window",
        "target_2_price_or_scenario",
        "target_2_evaluation_window",
        "time_stop",
        "event_exit_date",
        "event_handling_plan",
        "post_exit_state",
        "auto_relay_forbidden",
        "current_deployable_cash_usd",
        "settlement_constraint",
        "action_allowed",
        "execution_action",
        "observation_action",
        "observation_trigger",
        "impulse_monitoring_required",
        "impulse_check_deadline",
        "candidate_coverage_matrix",
        "baseline_frozen",
        "historical_baseline_mutation_forbidden",
        "baseline_snapshot_sha256",
        "live_orders_enabled",
        "private_api_used",
        "human_confirmation_required",
    }
    tactical_required = {
        "entry_range",
        "secondary_entry",
        "optimal_entry",
        "entry_deadline",
        "target_range",
        "target_time_window",
        "trim_or_partial_exit",
        "full_exit_or_invalidation",
        "latest_exit_or_review_date",
        "position_size_plan",
        "forecast_probability_pct",
        "execution_readiness_score",
        "current_quantity",
        "current_market_value_usd",
        "cash_rail_source",
        "action_source",
        "price_as_of",
        "price_source",
        "data_age_minutes",
        "data_freshness_status",
        "entry_window_start",
        "entry_window_end",
        "allowed_session",
        "entry_trigger",
        "no_entry_if_not_triggered",
        "expected_holding_days",
        "max_holding_days",
        "target_1_price_or_scenario",
        "target_1_evaluation_window",
        "target_2_price_or_scenario",
        "target_2_evaluation_window",
        "time_stop",
        "event_exit_date",
        "event_handling_plan",
        "post_exit_state",
        "auto_relay_forbidden",
        "current_deployable_cash_usd",
        "settlement_constraint",
        "action_allowed",
    }
    dca_missing = {
        item.get("recommendation_id"): record_missing(item, dca_required)
        for item in dca_records
    }
    tactical_missing = {
        item.get("recommendation_id"): record_missing(item, tactical_required)
        for item in tactical_records
    }
    dca_amounts_numeric = all(
        isinstance(item.get(field), (int, float))
        and item.get(field) >= 0
        for item in dca_records
        for field in [
            "planned_amount_usd_low",
            "planned_amount_usd_high",
            "planned_near_amount_usd",
            "planned_pullback_add_usd",
            "staking_wait_cost_usd",
            "required_pullback_to_wait_pct",
            "front_load_extra_months",
            "front_load_amount_usd",
            "near_term_total_budget_usd",
        ]
    )
    record_review_dates_valid = all(
        parse_time(item.get("entry_deadline")) is not None
        and parse_time(item.get("latest_exit_or_review_date")) is not None
        for item in dca_records + tactical_records
    )
    calibration = rec_panel.get("calibration") or {}
    calibration_resolved = sum(
        int((item or {}).get("resolved_count") or 0)
        for item in (calibration.get("buckets") or {}).values()
    )
    recommendation_outcome_reviews = int((rec_panel.get("summary") or {}).get("outcome_reviews") or 0)
    research_roles = {
        item.get("agent_id")
        for item in research.get("agent_outputs") or []
        if isinstance(item, dict) and item.get("agent_id")
    }
    protected_excluded = set(us_tactical.get("protected_symbols_excluded") or [])
    annual_5 = nested_get(required, "5", "current_principal_required_annual_rate")
    annual_10 = nested_get(required, "10", "current_principal_required_annual_rate")
    total_value = as_float(portfolio.get("total_value"), 0.0)
    monthly_dca = as_float(projection.get("monthly_dca"), None)
    guidance_dca = as_float(dca_guidance.get("monthly_dca_usd"), None)
    crypto_assets = crypto.get("assets") or []
    missing_panel = context.get("missing_data_downgrade_panel") or []

    checks: list[dict[str, Any]] = [
        check("objective_report_file_exists", report_path.exists(), str(report_path)),
        check("objective_context_file_exists", context_path.exists(), str(context_path)),
        check("objective_run_id_resolved", bool(resolved_run_id), str(resolved_run_id)),
        check(
            "current_holdings_bound_to_goal",
            total_value > 0 and bool(portfolio.get("top_holdings")),
            f"total_value={total_value}; top_holdings={len(portfolio.get('top_holdings') or [])}",
        ),
        check(
            "five_and_ten_year_10x_math_present",
            annual_5 is not None and annual_10 is not None
            and "5年 当前本金10x" in report_text
            and "10年 当前本金10x" in report_text,
            f"5y={annual_5}; 10y={annual_10}",
        ),
        check(
            "monthly_dca_expected_amount_bound",
            monthly_dca == expected_monthly_dca and guidance_dca == expected_monthly_dca,
            f"projection={monthly_dca}; guidance={guidance_dca}; expected={expected_monthly_dca}",
        ),
        check(
            "crypto_dca_dynamic_two_step_present",
            "两步 DCA 执行表" in report_text
            and "context_dynamic_dca_builder" in report_text
            and bool(dca_records),
            f"dca_records={len(dca_records)}",
        ),
        check(
            "dca_recommendations_are_reviewable",
            bool(dca_records)
            and all(not missing for missing in dca_missing.values())
            and dca_amounts_numeric
            and record_review_dates_valid,
            json.dumps({
                "missing": dca_missing,
                "front_load_records": [
                    {
                        "symbol": item.get("symbol"),
                        "dca_timing_decision": item.get("dca_timing_decision"),
                        "front_load_extra_months": item.get("front_load_extra_months"),
                        "front_load_amount_usd": item.get("front_load_amount_usd"),
                        "near_term_total_budget_usd": item.get("near_term_total_budget_usd"),
                    }
                    for item in dca_records
                    if item.get("symbol") in {"SOLUSDT", "ADAUSDT", "NIGHTUSDT"}
                ],
            }, ensure_ascii=False)[:1000],
        ),
        check(
            "staking_compounding_in_goal_model",
            any((asset.get("staking_compounding_summary") or {}).get("apy_used_pct") is not None for asset in asset_goal.get("assets") or []),
            "staking_compounding_summary present on asset_goal assets",
        ),
        check(
            "long_horizon_dca_timing_covers_staking_opportunity_cost",
            "长期低位 / 质押机会成本判断" in report_text
            and "required_pullback_to_wait" in report_text
            and "可前置" in report_text
            and "近期待投入上限" in report_text
            and "现金闲置拖累" in report_text
            and {"SOL", "ADA"}.issubset(dca_timing_symbols)
            and all(
                item.get("decision") in {"accelerated_dca", "near_price_entry", "limit_order_wait", "hold_stablecoin_until_trigger"}
                and item.get("required_pullback_to_wait_pct") is not None
                and item.get("planned_wait_days") is not None
                and item.get("front_load_extra_months") is not None
                and item.get("front_load_amount_usd") is not None
                and item.get("near_term_total_budget_usd") is not None
                and (item.get("long_term_low_value_zone_status") or item.get("long_term_value_zone_status"))
                and item.get("cash_idle_drag_comment")
                for item in dca_timing_decisions
                if item.get("symbol") in {"SOL", "ADA"}
            )
            and all(item.get("long_horizon_timing_decision") for item in dca_records if item.get("symbol") in {"SOLUSDT", "ADAUSDT"}),
            json.dumps({
                "timing_symbols": sorted(str(item) for item in dca_timing_symbols if item),
                "decisions": [
                    {
                        "symbol": item.get("symbol"),
                        "decision": item.get("decision"),
                        "required_pullback_to_wait_pct": item.get("required_pullback_to_wait_pct"),
                        "front_load_extra_months": item.get("front_load_extra_months"),
                        "front_load_amount_usd": item.get("front_load_amount_usd"),
                        "near_term_total_budget_usd": item.get("near_term_total_budget_usd"),
                        "long_term_low_value_zone_status": item.get("long_term_low_value_zone_status") or item.get("long_term_value_zone_status"),
                        "cash_idle_drag_comment": item.get("cash_idle_drag_comment"),
                    }
                    for item in dca_timing_decisions
                ],
                "record_symbols_with_timing": [
                    item.get("symbol")
                    for item in dca_records
                    if item.get("long_horizon_timing_decision")
                ],
            }, ensure_ascii=False)[:1000],
        ),
        check(
            "long_term_price_scenario_gate_configured",
            config_price_scenario.get("enabled") is True
            and config_price_scenario.get("required_for_long_term_dca") is True
            and "references/LONG_TERM_PRICE_SCENARIO_POLICY.md" in str(config_price_scenario.get("policy"))
            and "long_term_price_scenario_gate" in (config.get("required_workflow_order") or []),
            json.dumps({
                "enabled": config_price_scenario.get("enabled"),
                "required_for_long_term_dca": config_price_scenario.get("required_for_long_term_dca"),
                "policy": config_price_scenario.get("policy"),
                "workflow_contains_gate": "long_term_price_scenario_gate" in (config.get("required_workflow_order") or []),
            }, ensure_ascii=False),
        ),
        check(
            "long_term_price_scenario_panel_present",
            "Long-Term Price Scenario Panel" in report_text
            and {"ETH", "SOL", "ADA", "NIGHT"}.issubset({item.get("symbol") for item in price_scenario_assets})
            and all(item.get("five_year_base_price_range") for item in price_scenario_assets if item.get("symbol") in {"ETH", "SOL", "ADA", "NIGHT"})
            and all(item.get("ten_year_base_price_range") for item in price_scenario_assets if item.get("symbol") in {"ETH", "SOL", "ADA", "NIGHT"}),
            json.dumps({
                "status": (context.get("long_term_price_scenario_panel") or {}).get("status"),
                "symbols": [item.get("symbol") for item in price_scenario_assets],
            }, ensure_ascii=False),
        ),
        check(
            "price_scenario_maps_goal_to_dca_implication",
            any(item.get("symbol") == "SOL" and item.get("ten_x_goal_fit") == "strong_engine" and item.get("dca_implication") == "increase" for item in price_scenario_assets)
            and any(item.get("symbol") == "ETH" and "drag_for_new_dca" in str(item.get("ten_x_goal_fit")) and "no_new_dca" in str(item.get("dca_implication")) for item in price_scenario_assets),
            json.dumps([
                {
                    "symbol": item.get("symbol"),
                    "fit": item.get("ten_x_goal_fit"),
                    "dca": item.get("dca_implication"),
                }
                for item in price_scenario_assets
            ], ensure_ascii=False)[:1000],
        ),
        check(
            "crypto_data_multisource_panel_present",
            len(crypto_assets) >= 4 and bool(crypto.get("generated_at")),
            f"assets={len(crypto_assets)}; generated_at={crypto.get('generated_at')}",
        ),
        check(
            "macro_regime_present_for_dca_pace",
            bool(macro.get("macro_regime")) and "宏观只调整 DCA 节奏" in report_text,
            f"generated_at={macro.get('generated_at')}",
        ),
        check(
            "cash_rails_are_separate_and_visible",
            config_cash_rails.get("separate_crypto_and_us_equity_rails") is True
            and config_cash_rails.get("cross_rail_transfer_requires_manual_confirmation") is True
            and ("Crypto" in report_text and "美股" in report_text),
            json.dumps(config_cash_rails, ensure_ascii=False),
        ),
        check(
            "manual_dispatch_market_intelligence_gate_configured",
            config_fresh_gate.get("enabled") is True
            and config_fresh_gate.get("required") is True
            and config_fresh_gate.get("fresh_market_intelligence_snapshot_required") is True
            and config_fresh_gate.get("decision_rules", {}).get("missing_fresh_refresh_blocks_execute_now") is True,
            json.dumps({
                "enabled": config_fresh_gate.get("enabled"),
                "required": config_fresh_gate.get("required"),
                "snapshot_required": config_fresh_gate.get("fresh_market_intelligence_snapshot_required"),
                "missing_blocks_execute_now": config_fresh_gate.get("decision_rules", {}).get("missing_fresh_refresh_blocks_execute_now"),
            }, ensure_ascii=False),
        ),
        check(
            "fresh_market_intelligence_panel_present",
            "Fresh Market Intelligence Panel" in report_text
            and bool(fresh_market.get("snapshot_id"))
            and fresh_market.get("manual_dispatch_only") is True
            and fresh_market.get("background_loop_started") is False
            and fresh_market.get("live_orders_enabled") is False,
            json.dumps({
                "snapshot_id": fresh_market.get("snapshot_id"),
                "manual_dispatch_only": fresh_market.get("manual_dispatch_only"),
                "background_loop_started": fresh_market.get("background_loop_started"),
                "live_orders_enabled": fresh_market.get("live_orders_enabled"),
            }, ensure_ascii=False),
        ),
        check(
            "fresh_market_sources_attempted_for_both_rails",
            bool(fresh_market.get("sources_attempted"))
            and any("crypto" in str(item).lower() for item in fresh_market.get("sources_attempted") or [])
            and any(("us_open" in str(item).lower() or "us_equity" in str(item).lower()) for item in fresh_market.get("sources_attempted") or []),
            json.dumps({
                "attempted": fresh_market.get("sources_attempted"),
                "succeeded": fresh_market.get("sources_succeeded"),
                "failed": fresh_market.get("missing_or_failed_sources"),
            }, ensure_ascii=False)[:800],
        ),
        check(
            "fresh_market_internal_degradation_captured",
            not fresh_market.get("internal_degraded_sources")
            or fresh_market.get("market_intelligence_degraded") is True,
            json.dumps({
                "internal_degraded_sources": fresh_market.get("internal_degraded_sources"),
                "market_intelligence_degraded": fresh_market.get("market_intelligence_degraded"),
                "degraded_reasons": fresh_market.get("degraded_reasons"),
            }, ensure_ascii=False)[:1000],
        ),
        check(
            "fresh_market_degradation_blocks_execute_now",
            fresh_market.get("market_intelligence_degraded") is not True
            or readiness.get("execute_now_allowed") is False,
            f"market_intelligence_degraded={fresh_market.get('market_intelligence_degraded')}; execute_now={readiness.get('execute_now_allowed')}; reasons={fresh_market.get('degraded_reasons')}",
        ),
        check(
            "full_market_deep_analysis_objective_gate_configured",
            config_full_market_gate.get("enabled") is True
            and config_full_market_gate.get("required") is True
            and config_full_market_gate.get("degraded_behavior", {}).get("missing_goal_mapping_blocks_execute_now") is True,
            json.dumps({
                "enabled": config_full_market_gate.get("enabled"),
                "required": config_full_market_gate.get("required"),
                "missing_goal_mapping_blocks_execute_now": config_full_market_gate.get("degraded_behavior", {}).get("missing_goal_mapping_blocks_execute_now"),
            }, ensure_ascii=False),
        ),
        check(
            "full_market_deep_analysis_panel_maps_to_objectives",
            "Full-Market Deep Analysis Panel" in report_text
            and "月度战术收益目标" in report_text
            and "5-10年长期目标" in report_text
            and "现金通道" in report_text
            and bool(fresh_market.get("objective_strategy_mapping")),
            json.dumps(fresh_market.get("objective_strategy_mapping"), ensure_ascii=False)[:1000],
        ),
        check(
            "objective_traceability_gate_configured",
            config_traceability.get("enabled") is True
            and config_traceability.get("required") is True
            and "references/OBJECTIVE_TO_MECHANISM_TRACEABILITY.md" in str(config_traceability.get("reference"))
            and len(config_traceability.get("required_objectives") or []) >= 8,
            json.dumps({
                "enabled": config_traceability.get("enabled"),
                "required": config_traceability.get("required"),
                "reference": config_traceability.get("reference"),
                "required_objectives": config_traceability.get("required_objectives"),
            }, ensure_ascii=False)[:1000],
        ),
        check(
            "objective_traceability_panel_present",
            "目标到机制映射" in report_text
            and "5年/10年 10x" in report_text
            and "$1,000/月 crypto DCA" in report_text
            and "美股月/季 50%+ 战术目标" in report_text,
            "report includes objective traceability panel linking user goals to mechanisms",
        ),
        check(
            "us_tactical_50pct_target_tracked",
            as_float(us_tactical.get("target_monthly_return_pct"), 0.0) >= 50
            and as_float(us_tactical.get("target_quarterly_return_pct"), 0.0) >= 50
            and (
                as_float(us_tactical.get("current_tactical_value_usd"), 0.0) > 0
                or us_tactical.get("status") in {"baseline_missing", "baseline_rebuild_required"}
            ),
            f"monthly={us_tactical.get('target_monthly_return_pct')}; quarterly={us_tactical.get('target_quarterly_return_pct')}; value={us_tactical.get('current_tactical_value_usd')}; status={us_tactical.get('status')}",
        ),
        check(
            "protected_long_term_holdings_excluded_from_tactical",
            "CRCL" in protected_excluded and "CRCL" in report_text,
            f"protected_excluded={sorted(protected_excluded)}",
        ),
        check(
            "us_tactical_dynamic_relay_reviewable",
            bool(tactical_records)
            and all(not missing for missing in tactical_missing.values())
            and all(item.get("action_source") == "context_dynamic_us_tactical_builder" for item in tactical_records),
            json.dumps(tactical_missing, ensure_ascii=False)[:800],
        ),
        check(
            "active_alpha_scanner_bound",
            bool(us_scanner) and bool(us_scanner.get("created_at") or us_scanner.get("generated_at")),
            f"scan_status={us_scanner.get('scan_status')}; created_at={us_scanner.get('created_at')}",
        ),
        check(
            "research_committee_6plus_or_safely_degraded",
            (
                research.get("research_method") == "external_subagent_outputs"
                and research_validation.get("valid") is True
                and int(research_validation.get("unique_known_role_count") or 0) >= 6
                and int(research_validation.get("successful_known_role_count") or 0) >= 6
            )
            or (research.get("research_committee_degraded") is True and readiness.get("execute_now_allowed") is False),
            f"roles={len(research_roles)}; method={research.get('research_method')}; validation={research_validation}; degraded={research.get('research_committee_degraded')}; execute_now={readiness.get('execute_now_allowed')}",
        ),
        check(
            "research_committee_data_quality_warning",
            not all(item.get("data_quality") == "degraded" for item in research.get("agent_outputs") or []),
            "all research roles degraded; keep actions below execute_now",
            severity="warning",
        ),
        check(
            "recommendation_history_written_for_learning_loop",
            rec_write.get("status") == "ok" and len(records) >= 5,
            f"status={rec_write.get('status')}; records={len(records)}",
        ),
        check(
            "progressive_learning_confidence_panel_present",
            config_progressive_learning.get("enabled") is True
            and config_progressive_learning.get("required") is True
            and "Progressive Learning Confidence Gate" in report_text
            and "60_to_79_learning_samples" in report_text
            and "80_plus_strong_candidates" in report_text,
            json.dumps({
                "enabled": config_progressive_learning.get("enabled"),
                "required": config_progressive_learning.get("required"),
                "milestones": config_progressive_learning.get("learning_milestones"),
            }, ensure_ascii=False)[:800],
        ),
        check(
            "recommendation_outcome_reviewer_present",
            review_drafts.get("status", "ok") != "failed"
            and review_drafts.get("pending_count") is not None
            and review_drafts.get("upcoming_count") is not None,
            json.dumps({k: review_drafts.get(k) for k in ["status", "pending_count", "due_or_reviewable_count", "draft_review_count", "upcoming_count", "error"]}, ensure_ascii=False),
        ),
        check(
            "strategy_promotion_evidence_gate_present",
            promotion_panel.get("status") == "ok"
            and promotion_panel.get("max_real_action_from_evidence") in {"paper_only", "conditional_action"}
            and isinstance(promotion_panel.get("strategies"), list),
            json.dumps({k: promotion_panel.get(k) for k in ["status", "tactical_promotion_gate_passed", "max_real_action_from_evidence", "tactical_failed_gates"]}, ensure_ascii=False),
        ),
        check(
            "strategy_iteration_backlog_present",
            strategy_iteration.get("status") == "ok"
            and len(strategy_iteration.get("candidate_strategies") or []) >= 6
            and "下一轮策略迭代候选" in report_text,
            json.dumps({
                "status": strategy_iteration.get("status"),
                "candidate_count": len(strategy_iteration.get("candidate_strategies") or []),
                "max_allowed_action": (strategy_iteration.get("summary") or {}).get("max_allowed_action"),
            }, ensure_ascii=False),
        ),
        check(
            "strategy_promotion_blocks_live_when_evidence_insufficient",
            promotion_panel.get("tactical_promotion_gate_passed") is True
            or readiness.get("execute_now_allowed") is False,
            f"promotion_passed={promotion_panel.get('tactical_promotion_gate_passed')}; max_action={promotion_panel.get('max_real_action_from_evidence')}; execute_now={readiness.get('execute_now_allowed')}",
        ),
        check(
            "active_alpha_paper_ledger_bound",
            paper_metrics.get("status") == "ok"
            and paper_metrics.get("live_orders_enabled") is False
            and as_float(paper_metrics.get("initial_capital_usd"), 0.0) > 0,
            json.dumps(paper_metrics, ensure_ascii=False)[:800],
        ),
        check(
            "paper_evidence_blocks_execute_now_until_sample_sufficient",
            (
                int(paper_metrics.get("closed_count") or 0) >= 20
                and paper_metrics.get("win_rate_pct") is not None
                and as_float(paper_metrics.get("net_return_pct"), -999.0) > 0
            )
            or readiness.get("execute_now_allowed") is False,
            f"closed={paper_metrics.get('closed_count')}; win_rate={paper_metrics.get('win_rate_pct')}; net_return={paper_metrics.get('net_return_pct')}; execute_now={readiness.get('execute_now_allowed')}",
        ),
        check(
            "paper_sample_size_warning",
            int(paper_metrics.get("closed_count") or 0) >= 20,
            f"closed={paper_metrics.get('closed_count')}; open={paper_metrics.get('open_count')}; net_return={paper_metrics.get('net_return_pct')}",
            severity="warning",
        ),
        check(
            "recommendation_outcome_sample_warning",
            recommendation_outcome_reviews > 0 and calibration_resolved > 0,
            f"outcome_reviews={recommendation_outcome_reviews}; calibration_resolved={calibration_resolved}",
            severity="warning",
        ),
        check(
            "calibration_blocks_execute_now_until_resolved",
            calibration_resolved > 0 or readiness.get("execute_now_allowed") is False,
            f"resolved={calibration_resolved}; execute_now={readiness.get('execute_now_allowed')}",
        ),
        check(
            "double_80_enforced_for_tactical_records",
            readiness.get("execute_now_allowed") is False
            or all(
                as_float(item.get("forecast_probability_pct"), 0.0) >= 80
                and as_float(item.get("execution_readiness_score"), 0.0) >= 80
                for item in tactical_records
            ),
            f"execute_now={readiness.get('execute_now_allowed')}",
        ),
        check(
            "missing_data_downgrade_panel_present",
            bool(missing_panel) and "缺失数据" in report_text,
            f"items={len(missing_panel)}",
        ),
        check(
            "final_action_summary_present",
            "本轮默认最终操作清单" in report_text and "最终动作" in report_text,
            "final actionable table visible",
        ),
        check(
            "objective_not_claimed_as_guarantee",
            "保证最终五年十倍" not in report_text and "保证收益" not in report_text,
            "report should frame goal as target/risk-managed plan, not guaranteed return",
            severity="warning",
        ),
    ]
    errors = [item for item in checks if not item["passed"] and item["severity"] == "error"]
    warnings = [item for item in checks if not item["passed"] and item["severity"] == "warning"]
    return {
        "status": "ok" if not errors else "failed",
        "report_path": str(report_path),
        "context_path": str(context_path),
        "ledger_path": str(ledger_path),
        "config_path": str(config_path),
        "paper_ledger_path": str(paper_ledger_path),
        "paper_ledger_metrics": paper_metrics,
        "run_id": resolved_run_id,
        "expected_monthly_dca": expected_monthly_dca,
        "passed": len(checks) - len(errors) - len(warnings),
        "failed": len(errors),
        "warnings": len(warnings),
        "checks": checks,
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Objective Coverage Audit",
        "",
        f"- status: `{result['status']}`",
        f"- run_id: `{result.get('run_id')}`",
        f"- passed: `{result['passed']}`",
        f"- failed: `{result['failed']}`",
        f"- warnings: `{result['warnings']}`",
        "",
        "| Check | Status | Severity | Evidence |",
        "|---|---|---|---|",
    ]
    for item in result["checks"]:
        status = "PASS" if item["passed"] else "FAIL"
        lines.append(f"| `{item['name']}` | {status} | `{item['severity']}` | {item['evidence']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit report coverage of the top-level goal")
    parser.add_argument("--report-md", required=True)
    parser.add_argument("--context-json", required=True)
    parser.add_argument("--ledger-json", default=str(DEFAULT_LEDGER))
    parser.add_argument("--config-json", default=str(DEFAULT_CONFIG))
    parser.add_argument("--paper-ledger-json", default=str(DEFAULT_PAPER_LEDGER))
    parser.add_argument("--run-id")
    parser.add_argument("--expected-monthly-dca", type=float, default=1000.0)
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    result = audit(
        Path(args.report_md),
        Path(args.context_json),
        Path(args.ledger_json),
        Path(args.config_json),
        Path(args.paper_ledger_json),
        args.run_id,
        args.expected_monthly_dca,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(result), end="")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
