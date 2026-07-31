#!/usr/bin/env python3
"""Audit a generated manual investment report against core skill invariants.

This does not judge investment quality. It verifies that a report is wired to
the goal-oriented workflow: 10x target math, current data snapshots, Research
Committee output, two-step action plans, and recommendation-history writes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
DEFAULT_LEDGER = MANUAL_ROOT / "recommendations" / "recommendation_history.json"


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_optional_json(path: str | None) -> Any:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.exists():
        return None
    return load_json(candidate)


def nested_get(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def nearly_equal(left: Any, right: Any, tolerance: float = 0.01) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def parse_timestamp(value: Any) -> dt.datetime | None:
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


def timestamp_age_minutes(now_value: Any, source_value: Any) -> float | None:
    now_ts = parse_timestamp(now_value)
    source_ts = parse_timestamp(source_value)
    if not now_ts or not source_ts:
        return None
    return abs((now_ts - source_ts).total_seconds()) / 60.0


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


def audit(report_path: Path, context_path: Path, ledger_path: Path, run_id: str | None = None) -> dict[str, Any]:
    report_text = report_path.read_text(encoding="utf-8")
    context = load_json(context_path)
    ledger = load_json(ledger_path) if ledger_path.exists() else {"recommendations": []}
    resolved_run_id = extract_run_id(report_text, context, run_id)
    research = context.get("research_panel") or {}
    validation = research.get("external_agent_validation") or {}
    readiness = context.get("report_readiness") or {}
    fresh_market = context.get("fresh_market_intelligence_snapshot") or {}
    promotion = context.get("strategy_promotion_evidence_panel") or {}
    write_status = context.get("recommendation_history_write") or {}
    recommendation_panel = context.get("recommendation_history_summary") or {}
    recommendation_calibration = recommendation_panel.get("calibration") or {}
    calibration_buckets = recommendation_calibration.get("buckets") or {}
    calibration_resolved_count = sum(int((item or {}).get("resolved_count") or 0) for item in calibration_buckets.values())
    due_review = recommendation_panel.get("due_review") or []
    source_files = context.get("source_files") or {}
    portfolio_source = load_optional_json(source_files.get("portfolio_snapshot_json"))
    source_current = nested_get(portfolio_source or {}, "totals", "current") or {}
    source_quality = (portfolio_source or {}).get("data_quality_summary") or {}
    source_errors = (portfolio_source or {}).get("source_errors") or []
    portfolio_summary = context.get("portfolio_snapshot") or {}
    crypto_summary = nested_get(context, "crypto_market_panel", "summary") or {}
    us_scanner_summary = nested_get(context, "us_open_dynamic_scanner_panel", "summary") or {}
    macro_summary = nested_get(context, "macro_regime_panel", "summary") or {}
    price_scenario_summary = nested_get(context, "long_term_price_scenario_panel", "summary") or {}
    price_scenario_assets = price_scenario_summary.get("assets") or []
    asset_goal_summary = nested_get(context, "asset_goal_contribution_panel", "summary") or {}
    dca_guidance = asset_goal_summary.get("dca_guidance") or {}
    dca_timing_decisions = dca_guidance.get("long_horizon_timing_decisions") or []
    dca_timing_symbols = {item.get("symbol") for item in dca_timing_decisions}
    cost_basis_panel = context.get("cost_basis_reconciliation_panel") or {}
    cost_basis_summary = (cost_basis_panel.get("summary") or {}).get("summary") or {}
    report_generated_at = context.get("generated_at")
    freshness_inputs = {
        "portfolio": (portfolio_source or {}).get("generated_at"),
        "crypto": crypto_summary.get("generated_at"),
        "us_scanner": us_scanner_summary.get("created_at") or us_scanner_summary.get("generated_at"),
        "macro": macro_summary.get("generated_at"),
    }
    freshness_ages = {
        name: timestamp_age_minutes(report_generated_at, timestamp)
        for name, timestamp in freshness_inputs.items()
    }
    research_outputs = research.get("agent_outputs") or []
    research_quality = [item.get("data_quality") for item in research_outputs if item.get("data_quality")]
    degraded_research_count = sum(1 for item in research_quality if item == "degraded")
    records = [
        item
        for item in ledger.get("recommendations", [])
        if resolved_run_id and item.get("run_id") == resolved_run_id
    ]
    record_symbols = {item.get("symbol") for item in records}
    tactical_records = [
        item
        for item in records
        if item.get("bucket") == "us_tactical_alpha_sleeve"
        or item.get("capital_sleeve") == "tactical_alpha_sleeve"
    ]
    dca_structured_required = {
        "entry_range",
        "secondary_entry",
        "optimal_entry",
        "entry_deadline",
        "target_range",
        "target_time_window",
        "latest_exit_or_review_date",
        "position_size_plan",
        "action_source",
        "long_term_price_scenario",
        "planned_amount_usd_low",
        "planned_amount_usd_high",
        "planned_near_amount_usd",
        "planned_pullback_add_usd",
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
    tactical_structured_required = dca_structured_required | {
        "forecast_probability_pct",
        "execution_readiness_score",
        "trim_or_partial_exit",
        "full_exit_or_invalidation",
        "stop_or_invalid",
        "forecast_invalid_if",
        "current_quantity",
        "current_market_value_usd",
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
    tactical_structured_required = tactical_structured_required - {
        "planned_amount_usd_low",
        "planned_amount_usd_high",
        "planned_near_amount_usd",
        "planned_pullback_add_usd",
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
    }

    def record_for(symbol: str) -> dict[str, Any]:
        return next((item for item in records if item.get("symbol") == symbol), {})

    def missing_fields(record: dict[str, Any], required: set[str]) -> list[str]:
        return sorted(key for key in required if record.get(key) in (None, ""))

    sol_missing = missing_fields(record_for("SOLUSDT"), dca_structured_required)
    ada_missing = missing_fields(record_for("ADAUSDT"), dca_structured_required)
    tactical_missing = missing_fields(tactical_records[0], tactical_structured_required) if tactical_records else sorted(tactical_structured_required)

    required_sections = [
        "## 1. 一页结论",
        "## 1A. Fresh Market Intelligence Panel",
        "## 1B. Full-Market Deep Analysis Panel",
        "## 1C. 被动调度与本轮收口边界",
        "## 2. 持仓总览",
        "## 3. 成本覆盖与收益口径",
        "## 4. 目标差距与资产贡献",
        "## 5. 目标执行总控台",
        "## 6. 策略库与晋级状态",
        "## 7. 旧结论挑战与 Research Committee",
        "## 8A. 长期趋势矩阵",
        "## 8A-1. Long-Term Price Scenario Panel",
        "## 8B. Crypto Key Person Intelligence Panel",
        "## 8C. Asset Micro Thesis Matrix / Staking Compounding Model",
        "## 9. Crypto DCA 方向",
        "## 9A. Unified Candidate Deep Dive",
        "## 9B. Technical Execution Window",
        "## 10. 美股战术池与接力",
        "## 11. 缺失数据 / 降级 / 最终动作",
    ]
    checks: list[dict[str, Any]] = [
        check("report_file_exists", report_path.exists(), str(report_path)),
        check("context_file_exists", context_path.exists(), str(context_path)),
        check("run_id_resolved", bool(resolved_run_id), str(resolved_run_id)),
        check(
            "required_sections_present",
            all(section in report_text for section in required_sections),
            "; ".join(section for section in required_sections if section in report_text),
        ),
        check("goal_10x_math_present", "5年 当前本金10x" in report_text and "10年 当前本金10x" in report_text, "goal rows in report"),
        check(
            "long_term_price_scenario_panel_present",
            "Long-Term Price Scenario Panel" in report_text
            and "5y base" in report_text
            and "10x适配" in report_text
            and {"ETH", "SOL", "ADA", "NIGHT"}.issubset({item.get("symbol") for item in price_scenario_assets}),
            json.dumps({
                "asset_count": len(price_scenario_assets),
                "symbols": [item.get("symbol") for item in price_scenario_assets],
                "status": (context.get("long_term_price_scenario_panel") or {}).get("status"),
            }, ensure_ascii=False),
        ),
        check(
            "price_scenario_degradation_captured",
            (context.get("long_term_price_scenario_panel") or {}).get("status") != "degraded"
            or any(item.get("category") == "long_term_price_scenario" for item in context.get("missing_data_downgrade_panel") or []),
            json.dumps({
                "status": (context.get("long_term_price_scenario_panel") or {}).get("status"),
                "missing": price_scenario_summary.get("missing_data"),
            }, ensure_ascii=False)[:1000],
        ),
        check("strategy_library_panel_present", "goal_weighted_dca" in report_text and "trend_rotation_relay" in report_text, "strategy families in report"),
        check(
            "cost_basis_reconciliation_panel_present",
            bool(cost_basis_panel) and "成本覆盖与收益口径" in report_text and "partial_cost_aware_only" in report_text,
            json.dumps({
                "status": cost_basis_panel.get("status"),
                "max_allowed_pnl_claim": cost_basis_summary.get("max_allowed_pnl_claim"),
                "partial_symbols": cost_basis_summary.get("partial_known_lot_symbols"),
                "missing_material": cost_basis_summary.get("missing_material_symbols"),
            }, ensure_ascii=False),
        ),
        check("crypto_snapshot_bound", bool((context.get("crypto_market_panel") or {}).get("summary")), str((context.get("source_files") or {}).get("crypto_snapshot_json"))),
        check("us_scanner_bound", bool((context.get("us_open_dynamic_scanner_panel") or {}).get("summary")), str((context.get("source_files") or {}).get("us_scanner_json"))),
        check(
            "portfolio_source_snapshot_loadable",
            isinstance(portfolio_source, dict),
            str(source_files.get("portfolio_snapshot_json")),
        ),
        check(
            "portfolio_unallocated_earn_not_disputed_or_nonzero",
            source_current.get("unallocated_earn") in (0, 0.0, None)
            and "disputed" not in str(source_quality.get("unallocated_earn", "")).lower(),
            f"unallocated={source_current.get('unallocated_earn')}; quality={source_quality.get('unallocated_earn')}",
        ),
        check(
            "portfolio_total_matches_source_snapshot",
            nearly_equal(portfolio_summary.get("total_value"), source_current.get("total_value")),
            f"context={portfolio_summary.get('total_value')}; source={source_current.get('total_value')}",
        ),
        check(
            "portfolio_public_source_errors_clear",
            not source_errors,
            json.dumps(source_errors[:5], ensure_ascii=False),
            severity="warning",
        ),
        check(
            "core_input_freshness_under_120_minutes",
            bool(freshness_ages)
            and all(age is not None and age <= 120 for age in freshness_ages.values()),
            "; ".join(f"{name}={age:.1f}m" if age is not None else f"{name}=missing" for name, age in freshness_ages.items()),
            severity="warning",
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
            "fresh_market_intelligence_sources_recorded",
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
            "fresh_market_degraded_blocks_execute_now",
            fresh_market.get("market_intelligence_degraded") is not True
            or readiness.get("execute_now_allowed") is False,
            f"market_intelligence_degraded={fresh_market.get('market_intelligence_degraded')}; execute_now={readiness.get('execute_now_allowed')}; reasons={fresh_market.get('degraded_reasons')}",
        ),
        check(
            "full_market_deep_analysis_panel_present",
            "Full-Market Deep Analysis Panel" in report_text
            and "月度战术收益目标" in report_text
            and "5-10年长期目标" in report_text
            and "现金通道" in report_text
            and bool(fresh_market.get("objective_strategy_mapping")),
            json.dumps({
                "objective_strategy_mapping": fresh_market.get("objective_strategy_mapping"),
                "execute_now_allowed": readiness.get("execute_now_allowed"),
            }, ensure_ascii=False)[:1000],
        ),
        check(
            "research_committee_gate_safely_resolved",
            research.get("research_committee_degraded") is False
            or (
                readiness.get("execute_now_allowed") is False
                and (
                    "research_committee_degraded" in report_text
                    or "Research Committee" in report_text
                    or "降级" in report_text
                )
            ),
            f"degraded={research.get('research_committee_degraded')}; execute_now_allowed={readiness.get('execute_now_allowed')}",
        ),
        check(
            "research_roles_6_plus",
            research.get("research_committee_degraded") is True
            or (
                research.get("research_method") == "external_subagent_outputs"
                and validation.get("valid") is True
                and int(validation.get("unique_known_role_count") or 0) >= 6
                and int(validation.get("successful_known_role_count") or 0) >= 6
            ),
            f"method={research.get('research_method')}; degraded={research.get('research_committee_degraded')}; validation={validation}",
        ),
        check(
            "local_research_runner_is_safely_degraded",
            research.get("research_method") == "external_subagent_outputs"
            or (
                research.get("research_committee_degraded") is True
                and readiness.get("execute_now_allowed") is False
            ),
            f"method={research.get('research_method')}; degraded={research.get('research_committee_degraded')}; execute_now={readiness.get('execute_now_allowed')}",
        ),
        check(
            "research_agents_not_all_degraded",
            bool(research_quality) and degraded_research_count < len(research_quality),
            f"{degraded_research_count}/{len(research_quality)} degraded",
            severity="warning",
        ),
        check("two_step_dca_present", "两步 DCA 执行表" in report_text and "近价小仓" in report_text and "更优回调主仓" in report_text, "DCA ladder text"),
        check(
            "long_horizon_dca_timing_panel_present",
            "长期低位 / 质押机会成本判断" in report_text
            and "required_pullback_to_wait" in report_text
            and "可前置" in report_text
            and "近期待投入上限" in report_text
            and "现金闲置拖累" in report_text
            and {"SOL", "ADA"}.issubset(dca_timing_symbols)
            and all(
                item.get("decision")
                and item.get("required_pullback_to_wait_pct") is not None
                and item.get("front_load_extra_months") is not None
                and item.get("front_load_amount_usd") is not None
                and item.get("near_term_total_budget_usd") is not None
                and (item.get("long_term_low_value_zone_status") or item.get("long_term_value_zone_status"))
                and item.get("cash_idle_drag_comment")
                for item in dca_timing_decisions
                if item.get("symbol") in {"SOL", "ADA"}
            ),
            json.dumps({
                "symbols": sorted(str(item) for item in dca_timing_symbols if item),
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
            }, ensure_ascii=False)[:1000],
        ),
        check("two_step_us_tactical_present", "当前战术仓两步接力计划" in report_text and "次优入场" in report_text and "最优入场" in report_text, "US tactical ladder text"),
        check(
            "action_builders_are_context_dynamic",
            "context_dynamic_dca_builder" in report_text and "context_dynamic_us_tactical_builder" in report_text,
            "dynamic action builder markers",
        ),
        check(
            "recommendation_learning_panel_present",
            "Recommendation History 学习闭环 / 概率校准" in report_text
            and "probability_bucket" in report_text
            and "Progressive Learning Confidence Gate" in report_text
            and "learning_stage" in report_text
            and "60_to_79_learning_samples" in report_text,
            "learning loop and progressive confidence panel text",
        ),
        check(
            "calibration_unavailable_blocks_execute_now",
            calibration_resolved_count > 0 or readiness.get("execute_now_allowed") is False,
            f"resolved={calibration_resolved_count}; execute_now_allowed={readiness.get('execute_now_allowed')}",
        ),
        check(
            "due_review_visible_when_present",
            not due_review or "到期未复盘建议" in report_text,
            f"due_count={len(due_review)}",
        ),
        check("final_action_table_present", "本轮默认最终操作清单（可执行摘要）" in report_text, "final action table"),
        check(
            "execute_now_readiness_gate_evaluated",
            bool(readiness.get("execute_now_gate_version")),
            str(readiness.get("execute_now_gate_version")),
        ),
        check(
            "execute_now_only_when_all_gates_pass",
            readiness.get("execute_now_allowed") is not True
            or (
                research.get("research_committee_degraded") is False
                and validation.get("valid") is True
                and promotion.get("tactical_promotion_gate_passed") is True
                and int(readiness.get("execute_now_qualified_candidate_count") or 0) > 0
                and not readiness.get("failed_gates")
            ),
            json.dumps({
                "execute_now_allowed": readiness.get("execute_now_allowed"),
                "failed_gates": readiness.get("failed_gates"),
                "qualified_candidates": readiness.get("execute_now_qualified_candidate_count"),
                "promotion_passed": promotion.get("tactical_promotion_gate_passed"),
                "research_degraded": research.get("research_committee_degraded"),
            }, ensure_ascii=False),
        ),
        check("recommendation_history_write_ok", write_status.get("status") == "ok", json.dumps(write_status, ensure_ascii=False)),
        check("ledger_records_for_run", len(records) >= 5, f"{len(records)} records for {resolved_run_id}"),
        check(
            "ledger_has_core_crypto_and_dynamic_tactical",
            {"SOLUSDT", "ADAUSDT"}.issubset(record_symbols) and bool(tactical_records),
            f"symbols={','.join(sorted(str(item) for item in record_symbols))}; tactical={[item.get('symbol') for item in tactical_records]}",
        ),
        check(
            "ledger_dca_records_structured_for_review",
            not sol_missing and not ada_missing,
            f"SOL missing={sol_missing}; ADA missing={ada_missing}",
        ),
        check(
            "ledger_tactical_record_structured_for_review",
            bool(tactical_records) and not tactical_missing,
            f"tactical missing={tactical_missing}",
        ),
    ]
    errors = [item for item in checks if not item["passed"] and item["severity"] == "error"]
    warnings = [item for item in checks if not item["passed"] and item["severity"] == "warning"]
    return {
        "status": "ok" if not errors else "failed",
        "report_path": str(report_path),
        "context_path": str(context_path),
        "ledger_path": str(ledger_path),
        "run_id": resolved_run_id,
        "passed": len(checks) - len(errors) - len(warnings),
        "failed": len(errors),
        "warnings": len(warnings),
        "checks": checks,
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Manual Report Integrity Audit",
        "",
        f"- status: `{result['status']}`",
        f"- run_id: `{result.get('run_id')}`",
        f"- passed: `{result['passed']}`",
        f"- failed: `{result['failed']}`",
        "",
        "| Check | Status | Evidence |",
        "|---|---|---|",
    ]
    for item in result["checks"]:
        status = "PASS" if item["passed"] else "FAIL"
        lines.append(f"| `{item['name']}` | {status} | {item['evidence']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a generated manual investment report")
    parser.add_argument("--report-md", required=True)
    parser.add_argument("--context-json", required=True)
    parser.add_argument("--ledger-json", default=str(DEFAULT_LEDGER))
    parser.add_argument("--run-id")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    result = audit(Path(args.report_md), Path(args.context_json), Path(args.ledger_json), args.run_id)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(render_markdown(result))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
