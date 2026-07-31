#!/usr/bin/env python3
"""Build a goal execution dashboard from the latest manual report context.

The dashboard is a control surface, not a trading engine. It summarizes whether
the current workflow is actually moving toward the user's 5y/10y 10x objective,
where the operating bottlenecks are, and what must be validated next.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
DEFAULT_RECOMMENDATION_LEDGER = MANUAL_ROOT / "recommendations" / "recommendation_history.json"
TMP_ROOT = Path("/private/tmp")
VALIDATION_AUDITOR = ROOT / "active-alpha-paper-monitor" / "scripts" / "validation_sample_auditor.py"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def latest_context_path() -> Path | None:
    candidates = [path for path in TMP_ROOT.glob("*daily-context-final.json") if path.is_file()]
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


def fresh_validation_sample_audit(thresholds: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Recompute validation samples so the dashboard does not depend on stale report context."""
    if not VALIDATION_AUDITOR.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("validation_sample_auditor_for_dashboard", VALIDATION_AUDITOR)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        audit = module.build_audit(ROOT, thresholds=thresholds or {})
        audit["dashboard_source"] = "fresh_recomputed_by_goal_execution_dashboard"
        return audit
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "dashboard_source": "fresh_recompute_failed",
            "error": str(exc),
            "validation_sample_plan": {
                "status": "insufficient_evidence",
                "max_allowed_action": "paper_only",
                "failed_gates": ["validation_sample_dashboard_refresh_failed"],
                "sample_gaps": {},
                "next_validation_queue": [],
            },
        }


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


def parse_review_days(time_window: Any, default_days: int = 30) -> int:
    if not time_window:
        return default_days
    text = str(time_window).lower()
    numbers = [int(match) for match in re.findall(r"\d+", text)]
    if not numbers:
        return default_days
    days = max(numbers)
    if "trading" in text:
        days = int(round(days * 1.4))
    return max(days, 1)


def recommendation_due_at(record: dict[str, Any]) -> dt.datetime | None:
    for field in ["latest_exit_or_review_date", "entry_deadline"]:
        parsed = parse_time(record.get(field))
        if parsed is not None:
            return parsed
    generated = parse_time(record.get("generated_at"))
    if generated is None:
        return None
    return generated + dt.timedelta(days=parse_review_days(record.get("time_window")))


def probability_bucket(record: dict[str, Any]) -> str:
    probability = as_float(record.get("forecast_probability_pct"))
    if probability is None:
        return "missing_probability"
    if probability >= 80:
        return "80_plus"
    if probability >= 60:
        return "60_to_79"
    if probability >= 0:
        return "below_60"
    return "out_of_range"


def pct_value(value: Any) -> float | None:
    numeric = as_float(value)
    if numeric is None:
        return None
    return numeric * 100.0 if abs(numeric) <= 5 else numeric


def action_level_rank(level: str | None) -> int:
    order = {
        "block": 0,
        "no_deploy": 1,
        "watch": 2,
        "paper_only": 3,
        "conditional_action": 4,
        "hold_or_conditional_action": 4,
        "conditional_action_or_watch": 4,
        "execute_now": 5,
    }
    return order.get(str(level or "").strip(), 2)


def strictest_action(*levels: str | None) -> str:
    known = [str(level) for level in levels if level]
    if not known:
        return "watch"
    return min(known, key=action_level_rank)


def recommendation_ledger_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "missing",
            "path": str(path),
            "total_recommendations": 0,
            "pending_count": 0,
            "resolved_count": 0,
            "superseded_count": 0,
            "outcome_reviews": 0,
            "proposed_changes": 0,
        }
    payload = load_json(path)
    recommendations = payload.get("recommendations") or []
    now = dt.datetime.now(dt.timezone.utc)
    pending_records = [item for item in recommendations if item.get("outcome_status") == "pending"]
    pending = len(pending_records)
    resolved = sum(1 for item in recommendations if item.get("outcome_status") in {"hit", "failed"})
    superseded = sum(1 for item in recommendations if item.get("outcome_status") == "superseded")
    pending_with_due = []
    for item in pending_records:
        due = recommendation_due_at(item)
        bucket = probability_bucket(item)
        pending_with_due.append({
            "recommendation_id": item.get("recommendation_id"),
            "symbol": item.get("symbol"),
            "action": item.get("action"),
            "capital_sleeve": item.get("capital_sleeve"),
            "probability_bucket": bucket,
            "forecast_probability_pct": item.get("forecast_probability_pct"),
            "due_at": due.isoformat() if due else None,
            "days_until_due": (due - now).days if due else None,
            "latest_exit_or_review_date": item.get("latest_exit_or_review_date"),
            "entry_deadline": item.get("entry_deadline"),
            "time_window": item.get("time_window"),
        })
    pending_with_due.sort(key=lambda item: (item.get("days_until_due") is None, item.get("days_until_due") or 10**9))
    reviewable_now = [item for item in pending_with_due if item.get("days_until_due") is not None and item["days_until_due"] <= 0]
    upcoming_30d = [item for item in pending_with_due if item.get("days_until_due") is not None and 0 < item["days_until_due"] <= 30]
    probability_pending_counts: dict[str, int] = {}
    for item in pending_with_due:
        bucket = str(item.get("probability_bucket"))
        probability_pending_counts[bucket] = probability_pending_counts.get(bucket, 0) + 1
    return {
        "status": "ok",
        "path": str(path),
        "total_recommendations": len(recommendations),
        "pending_count": pending,
        "resolved_count": resolved,
        "superseded_count": superseded,
        "outcome_reviews": len(payload.get("outcome_reviews") or []),
        "proposed_changes": len(payload.get("proposed_changes") or []),
        "reviewable_now_count": len(reviewable_now),
        "upcoming_review_30d_count": len(upcoming_30d),
        "probability_pending_counts": probability_pending_counts,
        "next_review_candidates": pending_with_due[:12],
    }


def goal_path_status(required: dict[str, Any]) -> dict[str, Any]:
    five = required.get("5") or {}
    ten = required.get("10") or {}
    five_current = pct_value(five.get("current_principal_required_annual_rate"))
    ten_current = pct_value(ten.get("current_principal_required_annual_rate"))
    five_cumulative = pct_value(five.get("cumulative_capital_required_annual_rate"))
    ten_cumulative = pct_value(ten.get("cumulative_capital_required_annual_rate"))
    return {
        "five_year_current_principal_required_annual_pct": five_current,
        "five_year_cumulative_capital_required_annual_pct": five_cumulative,
        "ten_year_current_principal_required_annual_pct": ten_current,
        "ten_year_cumulative_capital_required_annual_pct": ten_cumulative,
        "five_year_status": "extreme_required_return" if (five_current or 0) >= 40 else "aggressive",
        "ten_year_status": "requires_growth_engine" if (ten_current or 0) >= 12 else "base_path_possible",
        "interpretation": (
            "5y 10x needs aggressive accelerator exposure and cannot be treated as a conservative DCA plan; "
            "10y current-principal 10x is less extreme but still needs sustained growth plus strict survival controls."
        ),
    }


def build_backlog(
    context: dict[str, Any],
    goal: dict[str, Any],
    dca: dict[str, Any],
    us_tactical: dict[str, Any],
    validation_plan: dict[str, Any],
    rec_ledger: dict[str, Any],
    paper_calendar: dict[str, Any] | None = None,
    learning_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    backlog: list[dict[str, Any]] = []
    fresh_market = context.get("fresh_market_intelligence_snapshot") or {}
    if not fresh_market:
        backlog.append({
            "priority": "P0",
            "area": "manual market intelligence",
            "task": "Generate the next manual report with a Fresh Market Intelligence Panel.",
            "reason": "The dashboard cannot prove this report refreshed market, sentiment, macro, crypto, and US equity data.",
            "max_action": "conditional_action_or_watch",
        })
    elif fresh_market.get("market_intelligence_degraded") is True:
        backlog.append({
            "priority": "P0",
            "area": "manual market intelligence",
            "task": "Run the next manual report with fresh market/sentiment sources instead of cache-only or skipped scanners.",
            "reason": ", ".join(fresh_market.get("degraded_reasons") or ["market_intelligence_degraded"]),
            "max_action": "conditional_action_or_watch",
        })
    sample_gaps = validation_plan.get("sample_gaps") or {}
    if sample_gaps.get("closed_paper_trades_needed", 0):
        paper_calendar = paper_calendar or {}
        next_reviews = paper_calendar.get("next_open_position_reviews") or []
        next_review_note = ""
        if next_reviews:
            next_review = next_reviews[0]
            next_review_note = (
                f" Nearest paper review: {next_review.get('symbol')} "
                f"{next_review.get('paper_trade_id')} expires {next_review.get('expires_at')}."
            )
        backlog.append({
            "priority": "P0",
            "area": "active-alpha validation",
            "task": (
                f"Continue paper validation until at least {sample_gaps.get('closed_paper_trades_needed')} "
                f"more closed paper trades are resolved.{next_review_note}"
            ),
            "reason": "Short-term alpha cannot be promoted while closed paper sample is below gate.",
            "max_action": validation_plan.get("max_allowed_action") or "paper_only",
        })
    if sample_gaps.get("calibration_resolved_needed", 0):
        learning_state = learning_state or {}
        next_reviews = rec_ledger.get("next_review_candidates") or []
        next_review_note = ""
        if next_reviews:
            next_review = next_reviews[0]
            next_review_note = (
                f" Next review candidate: {next_review.get('recommendation_id')} "
                f"due {next_review.get('due_at')}."
            )
        backlog.append({
            "priority": "P0",
            "area": "learning loop",
            "task": (
                f"Resolve {sample_gaps.get('calibration_resolved_needed')} recommendation outcomes "
                f"to calibrate forecast probabilities.{next_review_note}"
            ),
            "reason": (
                "The system may start from 60%-79% learning samples, but it cannot claim true 80% "
                f"target probability without resolved calibration. Current stage={learning_state.get('learning_stage')}."
            ),
            "max_action": "paper_only",
        })
    if validation_plan.get("failed_gates"):
        backlog.append({
            "priority": "P0",
            "area": "promotion gate",
            "task": "Keep tactical execute_now blocked until validation failed gates clear.",
            "reason": ", ".join(validation_plan.get("failed_gates") or []),
            "max_action": validation_plan.get("max_allowed_action") or "paper_only",
        })
    if dca.get("primary_pair"):
        backlog.append({
            "priority": "P1",
            "area": "crypto DCA",
            "task": f"Use {dca.get('primary_pair')} as primary monthly DCA candidate and {dca.get('secondary_pair')} as secondary only after cash rail and data quality are verified.",
            "reason": "Goal gap requires accelerator exposure; ETH/BTC are not default new DCA in the current policy.",
            "max_action": "conditional_action",
        })
    cash_drag = as_float(us_tactical.get("tactical_cash_drag_pct"), 0.0) or 0.0
    if cash_drag >= 30:
        backlog.append({
            "priority": "P1",
            "area": "us tactical sleeve",
            "task": "Reduce idle tactical cash drag only when a two-step relay setup passes data, trend, and double-80 gates.",
            "reason": f"Tactical cash drag is {cash_drag:.2f}%, but current evidence max action remains below execute_now.",
            "max_action": us_tactical.get("max_allowed_action") or "conditional_action",
        })
    if (goal.get("five_year_current_principal_required_annual_pct") or 0) >= 40:
        backlog.append({
            "priority": "P1",
            "area": "goal construction",
            "task": "Keep new capital biased toward verified accelerator or high-convexity candidates; do not add drag assets unless risk regime requires survival mode.",
            "reason": "5y 10x path requires roughly 40%+ annualized return under current assumptions.",
            "max_action": "conditional_action",
        })
    if rec_ledger.get("pending_count", 0) >= 50:
        backlog.append({
            "priority": "P2",
            "area": "recommendation hygiene",
            "task": "Review and close or supersede stale pending recommendations during monthly review.",
            "reason": f"{rec_ledger.get('pending_count')} recommendations remain pending; unresolved records weaken learning quality.",
            "max_action": "review_only",
        })
    return backlog


def progressive_learning_state(validation_audit: dict[str, Any], rec_ledger: dict[str, Any]) -> dict[str, Any]:
    """Summarize the 60% -> 80% learning ramp as a first-class objective state."""
    paper = validation_audit.get("paper_sample_metrics") or {}
    rec = validation_audit.get("recommendation_sample_metrics") or {}
    thresholds = validation_audit.get("thresholds") or {}
    sample_plan = validation_audit.get("validation_sample_plan") or {}
    sample_gaps = sample_plan.get("sample_gaps") or {}
    resolved = int(rec.get("resolved_count") or 0)
    closed_paper = int(paper.get("closed_count") or 0)
    paper_win_rate = as_float(paper.get("win_rate_pct"))
    recommendation_hit_rate = as_float(rec.get("hit_rate_pct"))

    if resolved < 10:
        stage = "cold_start"
        next_stage = "early_calibration"
        max_learning_action = "watch / paper_only / conditional_action"
    elif resolved < 20:
        stage = "early_calibration"
        next_stage = "usable_calibration"
        max_learning_action = "paper_only / conditional_action / small_probe_review"
    elif resolved < 50:
        stage = "usable_calibration"
        next_stage = "validated_ramp"
        max_learning_action = "conditional_action / proposed_changes_after_human_review"
    else:
        stage = "validated_ramp"
        next_stage = "80pct_candidate_review"
        max_learning_action = "execute_now_candidate_only_if_double80_and_all_gates_pass"

    floor_met = paper_win_rate is not None and paper_win_rate >= 60.0
    return {
        "status": "active",
        "purpose": "progressively improve forecast calibration from reviewable 60%+ samples toward validated 80%+ probabilities",
        "learning_stage": stage,
        "next_stage": next_stage,
        "learning_floor_pct": 60,
        "validated_probability_target_pct": 80,
        "paper_closed_count": closed_paper,
        "paper_win_rate_pct": paper_win_rate,
        "paper_learning_floor_met": floor_met,
        "recommendation_resolved_count": resolved,
        "recommendation_hit_rate_pct": recommendation_hit_rate,
        "recommendation_outcome_reviews": rec.get("outcome_reviews"),
        "closed_paper_trades_needed": sample_gaps.get("closed_paper_trades_needed"),
        "calibration_resolved_needed": sample_gaps.get("calibration_resolved_needed"),
        "max_learning_action": max_learning_action,
        "execute_now_still_blocked_until": [
            "true target probability >=80",
            "execution readiness >=80",
            "strategy promotion evidence passes",
            "fresh data and risk gates pass",
            "human confirmation exists",
        ],
        "operator_note": (
            "This is a learning mechanism, not a promise that the next trade or this month will hit the target. "
            "The dashboard should improve by closing samples, reviewing misses, and updating proposed changes."
        ),
    }


def paper_review_calendar_summary(validation_audit: dict[str, Any]) -> dict[str, Any]:
    paper = validation_audit.get("paper_sample_metrics") or {}
    capacity = validation_audit.get("validation_capacity_state") or {}
    summary = paper.get("open_position_exit_summary") or {}
    return {
        "status": "ok" if summary else "missing",
        "open_position_exit_summary": summary,
        "next_open_position_reviews": (paper.get("open_exit_calendar") or [])[:5],
        "sample_action": capacity.get("sample_action"),
        "recommended_runner_mode": capacity.get("recommended_runner_mode"),
        "next_runner_hint": capacity.get("next_runner_hint"),
        "capacity_reason": capacity.get("reason"),
        "operator_note": (
            "Paper review calendar only tells the system when evidence can become reviewable; "
            "it is not a live sell/buy instruction."
        ),
    }


def build_dashboard(context: dict[str, Any], recommendation_ledger: Path = DEFAULT_RECOMMENDATION_LEDGER) -> dict[str, Any]:
    portfolio = context.get("portfolio_snapshot") or {}
    projection = context.get("goal_path_projection") or {}
    required = projection.get("required") or {}
    dca = (((context.get("asset_goal_contribution_panel") or {}).get("summary") or {}).get("dca_guidance") or {})
    us_tactical = (((context.get("us_tactical_performance_panel") or {}).get("summary") or {}))
    readiness = context.get("report_readiness") or {}
    fresh_market = context.get("fresh_market_intelligence_snapshot") or {}
    research = context.get("research_panel") or {}
    promotion = dict(context.get("strategy_promotion_evidence_panel") or {})
    fresh_validation = fresh_validation_sample_audit((promotion.get("thresholds") or {}))
    if fresh_validation:
        promotion["validation_sample_audit"] = fresh_validation
    validation_audit = promotion.get("validation_sample_audit") or {}
    validation_plan = validation_audit.get("validation_sample_plan") or {}
    rec_ledger = recommendation_ledger_summary(recommendation_ledger)
    learning_state = progressive_learning_state(validation_audit, rec_ledger)
    paper_calendar = paper_review_calendar_summary(validation_audit)
    goal = goal_path_status(required)
    tactical_max_allowed = strictest_action(
        promotion.get("max_real_action_from_evidence"),
        validation_plan.get("max_allowed_action"),
    )
    dashboard = {
        "generated_at": utc_now(),
        "status": "ok",
        "dashboard_version": "goal-execution-dashboard-v2-progressive-learning",
        "portfolio_value_usd": portfolio.get("total_value"),
        "monthly_dca_usd": projection.get("monthly_dca") or dca.get("monthly_dca_usd"),
        "goal_path": goal,
        "crypto_dca_operating_plan": {
            "primary_pair": dca.get("primary_pair"),
            "secondary_pair": dca.get("secondary_pair"),
            "satellite_pair": dca.get("satellite_pair"),
            "suggested_ranges": dca.get("suggested_ranges") or [],
            "avoid_or_zero_new_dca": dca.get("avoid_or_zero_new_dca") or [],
            "max_allowed_action": "conditional_action",
        },
        "us_tactical_operating_state": {
            "sleeve_id": us_tactical.get("sleeve_id"),
            "data_quality": us_tactical.get("data_quality"),
            "current_tactical_value_usd": us_tactical.get("current_tactical_value_usd"),
            "tactical_cash_drag_pct": us_tactical.get("tactical_cash_drag_pct"),
            "monthly_progress_status": us_tactical.get("monthly_progress_status"),
            "monthly_gap_usd": us_tactical.get("monthly_gap_usd"),
            "monthly_gap_pct_of_current": us_tactical.get("monthly_gap_pct_of_current"),
            "required_daily_return_to_monthly_target_pct": us_tactical.get("required_daily_return_to_monthly_target_pct"),
            "protected_symbols_excluded": us_tactical.get("protected_symbols_excluded") or [],
            "max_allowed_action": us_tactical.get("max_allowed_action"),
        },
        "manual_dispatch_market_intelligence": {
            "snapshot_id": fresh_market.get("snapshot_id"),
            "captured_at": fresh_market.get("captured_at"),
            "manual_dispatch_only": fresh_market.get("manual_dispatch_only"),
            "background_loop_started": fresh_market.get("background_loop_started"),
            "live_orders_enabled": fresh_market.get("live_orders_enabled"),
            "market_intelligence_degraded": fresh_market.get("market_intelligence_degraded"),
            "degraded_reasons": fresh_market.get("degraded_reasons") or [],
            "sources_attempted": fresh_market.get("sources_attempted") or [],
            "sources_succeeded": fresh_market.get("sources_succeeded") or [],
            "downgrade_effect": fresh_market.get("downgrade_effect"),
            "max_allowed_action_if_degraded": "conditional_action_or_watch",
        },
        "evidence_and_gate_state": {
            "research_committee_degraded": research.get("research_committee_degraded"),
            "market_intelligence_degraded": fresh_market.get("market_intelligence_degraded"),
            "execute_now_allowed": readiness.get("execute_now_allowed"),
            "report_max_allowed_action": readiness.get("max_allowed_action"),
            "short_term_tactical_max_allowed_action": tactical_max_allowed,
            "crypto_dca_max_allowed_action": "conditional_action" if dca.get("primary_pair") else "watch",
            "readiness_failed_gates": readiness.get("failed_gates") or [],
            "strategy_promotion_max_action": promotion.get("max_real_action_from_evidence"),
            "strategy_promotion_failed_gates": promotion.get("tactical_failed_gates") or [],
            "validation_sample_status": validation_plan.get("status"),
            "validation_sample_failed_gates": validation_plan.get("failed_gates") or [],
            "validation_sample_gaps": validation_plan.get("sample_gaps") or {},
            "recommendation_ledger": rec_ledger,
            "progressive_learning_stage": learning_state.get("learning_stage"),
            "progressive_learning_floor_pct": learning_state.get("learning_floor_pct"),
            "progressive_validated_probability_target_pct": learning_state.get("validated_probability_target_pct"),
        },
        "progressive_learning_state": learning_state,
        "paper_validation_review_calendar": paper_calendar,
    }
    dashboard["execution_backlog"] = build_backlog(
        context,
        goal,
        dca,
        us_tactical,
        validation_plan,
        rec_ledger,
        paper_calendar,
        learning_state,
    )
    return dashboard


def money(value: Any) -> str:
    numeric = as_float(value)
    if numeric is None:
        return "`n/a`"
    return f"`${numeric:,.2f}`"


def pct(value: Any) -> str:
    numeric = as_float(value)
    if numeric is None:
        return "`n/a`"
    return f"`{numeric:.2f}%`"


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join("" if item is None else str(item) for item in row) + " |")
    return "\n".join(lines)


def render_markdown(payload: dict[str, Any]) -> str:
    goal = payload.get("goal_path") or {}
    dca = payload.get("crypto_dca_operating_plan") or {}
    us = payload.get("us_tactical_operating_state") or {}
    fresh = payload.get("manual_dispatch_market_intelligence") or {}
    evidence = payload.get("evidence_and_gate_state") or {}
    learning = payload.get("progressive_learning_state") or {}
    paper_calendar = payload.get("paper_validation_review_calendar") or {}
    open_summary = paper_calendar.get("open_position_exit_summary") or {}
    gap = evidence.get("validation_sample_gaps") or {}
    rec_ledger = evidence.get("recommendation_ledger") or {}
    goal_rows = [
        ["portfolio_value", money(payload.get("portfolio_value_usd"))],
        ["monthly_dca", money(payload.get("monthly_dca_usd"))],
        ["5y_current_required_annual", pct(goal.get("five_year_current_principal_required_annual_pct"))],
        ["10y_current_required_annual", pct(goal.get("ten_year_current_principal_required_annual_pct"))],
        ["report_max_action", f"`{evidence.get('report_max_allowed_action')}`"],
        ["tactical_max_action", f"`{evidence.get('short_term_tactical_max_allowed_action')}`"],
    ]
    dca_rows = [
        ["primary_pair", f"`{dca.get('primary_pair')}`"],
        ["secondary_pair", f"`{dca.get('secondary_pair')}`"],
        ["satellite_pair", f"`{dca.get('satellite_pair')}`"],
        ["max_action", f"`{dca.get('max_allowed_action')}`"],
    ]
    us_rows = [
        ["sleeve", f"`{us.get('sleeve_id')}`"],
        ["current_value", money(us.get("current_tactical_value_usd"))],
        ["cash_drag", pct(us.get("tactical_cash_drag_pct"))],
        ["monthly_status", f"`{us.get('monthly_progress_status')}`"],
        ["monthly_gap", money(us.get("monthly_gap_usd"))],
        ["required_daily_to_monthly_target", pct(us.get("required_daily_return_to_monthly_target_pct"))],
    ]
    evidence_rows = [
        ["market_intelligence_degraded", f"`{evidence.get('market_intelligence_degraded')}`"],
        ["research_committee_degraded", f"`{evidence.get('research_committee_degraded')}`"],
        ["execute_now_allowed", f"`{evidence.get('execute_now_allowed')}`"],
        ["validation_status", f"`{evidence.get('validation_sample_status')}`"],
        ["closed_paper_trades_needed", f"`{gap.get('closed_paper_trades_needed')}`"],
        ["calibration_resolved_needed", f"`{gap.get('calibration_resolved_needed')}`"],
        ["walkforward_target_pass_needed", f"`{gap.get('walkforward_target_research_pass_needed')}`"],
        ["progressive_learning_stage", f"`{learning.get('learning_stage')}`"],
        ["learning_floor_to_target", f"`{learning.get('learning_floor_pct')}% -> {learning.get('validated_probability_target_pct')}%`"],
        ["recommendation_reviewable_now", f"`{rec_ledger.get('reviewable_now_count')}`"],
        ["recommendation_upcoming_30d", f"`{rec_ledger.get('upcoming_review_30d_count')}`"],
    ]
    learning_rows = [
        ["stage", f"`{learning.get('learning_stage')}`"],
        ["next_stage", f"`{learning.get('next_stage')}`"],
        ["paper_closed", f"`{learning.get('paper_closed_count')}`"],
        ["paper_win_rate", pct(learning.get("paper_win_rate_pct"))],
        ["paper_60pct_floor_met", f"`{learning.get('paper_learning_floor_met')}`"],
        ["recommendation_resolved", f"`{learning.get('recommendation_resolved_count')}`"],
        ["recommendation_hit_rate", pct(learning.get("recommendation_hit_rate_pct"))],
        ["max_learning_action", f"`{learning.get('max_learning_action')}`"],
    ]
    paper_review_rows = [
        [
            item.get("paper_trade_id"),
            item.get("symbol"),
            item.get("expires_at"),
            item.get("hours_to_expiry"),
            pct(item.get("unrealized_pnl_pct")),
        ]
        for item in (paper_calendar.get("next_open_position_reviews") or [])[:5]
    ]
    paper_summary_rows = [
        ["open_count", f"`{open_summary.get('open_count')}`"],
        ["expiring_24h", f"`{open_summary.get('expiring_24h_count')}`"],
        ["expiring_72h", f"`{open_summary.get('expiring_72h_count')}`"],
        ["nearest_expiry_at", f"`{open_summary.get('nearest_expiry_at')}`"],
        ["sample_action", f"`{paper_calendar.get('sample_action')}`"],
        ["recommended_runner_mode", f"`{paper_calendar.get('recommended_runner_mode')}`"],
    ]
    fresh_rows = [
        ["snapshot_id", f"`{fresh.get('snapshot_id')}`"],
        ["captured_at", f"`{fresh.get('captured_at')}`"],
        ["manual_dispatch_only", f"`{fresh.get('manual_dispatch_only')}`"],
        ["background_loop_started", f"`{fresh.get('background_loop_started')}`"],
        ["live_orders_enabled", f"`{fresh.get('live_orders_enabled')}`"],
        ["market_intelligence_degraded", f"`{fresh.get('market_intelligence_degraded')}`"],
        ["downgrade_effect", f"`{fresh.get('downgrade_effect')}`"],
        ["sources_attempted", f"`{len(fresh.get('sources_attempted') or [])}`"],
        ["sources_succeeded", f"`{len(fresh.get('sources_succeeded') or [])}`"],
        ["degraded_reasons", "; ".join(fresh.get("degraded_reasons") or [])],
    ]
    review_rows = [
        [
            item.get("recommendation_id"),
            item.get("symbol"),
            item.get("action"),
            item.get("probability_bucket"),
            item.get("due_at"),
            item.get("days_until_due"),
        ]
        for item in (rec_ledger.get("next_review_candidates") or [])[:8]
    ]
    backlog_rows = [
        [item.get("priority"), item.get("area"), item.get("task"), item.get("max_action")]
        for item in payload.get("execution_backlog") or []
    ]
    return "\n".join([
        "# Goal Execution Dashboard",
        "",
        markdown_table(["Metric", "Value"], goal_rows),
        "",
        "## Crypto DCA",
        "",
        markdown_table(["Field", "Value"], dca_rows),
        "",
        "## US Tactical",
        "",
        markdown_table(["Field", "Value"], us_rows),
        "",
        "## Evidence Gates",
        "",
        markdown_table(["Gate", "State"], evidence_rows),
        "",
        "## Progressive Learning Loop",
        "",
        "这个面板回答的是“系统有没有在学习”，不是“这次收益目标是否已经完成”。60% 左右的样本可以先进入 watch/paper，等复盘样本足够后再校准到 80%+。",
        "",
        markdown_table(["Field", "Value"], learning_rows),
        "",
        "## Paper Review Calendar",
        "",
        "这里显示哪些模拟仓快到复盘窗口；它只帮助积累证据，不是实盘买卖指令。",
        "",
        markdown_table(["Field", "Value"], paper_summary_rows),
        "",
        markdown_table(
            ["Paper Trade", "Symbol", "Expires At", "Hours", "Unrealized PnL"],
            paper_review_rows or [["-", "-", "-", "-", "-"]],
        ),
        "",
        "## Fresh Market Intelligence",
        "",
        "这个面板只说明本次手动报告是否重新取数；它不代表后台自动扫描，也不代表自动下单。",
        "",
        markdown_table(["Field", "Value"], fresh_rows),
        "",
        "## Recommendation Calibration Queue",
        "",
        markdown_table(
            ["Recommendation", "Symbol", "Action", "Probability Bucket", "Due At", "Days"],
            review_rows or [["-", "-", "-", "-", "-", "-"]],
        ),
        "",
        "## Execution Backlog",
        "",
        markdown_table(["Priority", "Area", "Task", "Max Action"], backlog_rows or [["-", "-", "No backlog generated", "-"]]),
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Build 5y/10y 10x goal execution dashboard")
    parser.add_argument("--context-json", default="", help="Manual daily context JSON. Defaults to latest /private/tmp/*daily-context-final.json")
    parser.add_argument("--recommendation-ledger", default=str(DEFAULT_RECOMMENDATION_LEDGER))
    parser.add_argument("--output", default="", help="Optional JSON output path")
    parser.add_argument("--markdown-output", default="", help="Optional Markdown output path")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    context_path = Path(args.context_json) if args.context_json else latest_context_path()
    if context_path is None:
        parser.error("No context JSON supplied and no /private/tmp/*daily-context-final.json found")
    context = load_json(context_path)
    payload = build_dashboard(context, Path(args.recommendation_ledger))
    payload["context_json"] = str(context_path)
    if args.output:
        write_json(Path(args.output), payload)
    if args.markdown_output:
        Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_output).write_text(render_markdown(payload), encoding="utf-8")
    if args.format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
