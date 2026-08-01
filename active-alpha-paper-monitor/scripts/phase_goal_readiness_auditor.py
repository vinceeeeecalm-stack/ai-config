#!/usr/bin/env python3
"""Audit active-alpha progress against the three-phase automation goal.

Read-only. This script does not fetch market data, mutate the paper ledger, or
place orders. It maps the user's long-running goal into explicit evidence
checks so Phase 1 engineering readiness is not confused with Phase 2/3 strategy
or live-trading proof.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import datetime as dt
import json
from pathlib import Path
from typing import Any

from kline_cache_storage import inspect_kline_cache_storage


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
LEDGER_PATH = ROOT / "paper_trades" / "paper_portfolio_ledger.json"
REPORTS_DIR = ROOT / "reports"
EXPERIMENTS_DIR = ROOT / "experiments"
PAPER_STRATEGY_OVERLAY_PATH = ROOT / "config" / "paper_strategy_auto_overlay.json"
AUTOMATION_RECOVERY_PLAN_PATH = EXPERIMENTS_DIR / "automation-recovery-plan.json"
LATEST_GOAL_AUDIT_JSON = EXPERIMENTS_DIR / "active-alpha-goal-completion-audit.json"
LATEST_GOAL_AUDIT_REPORT = REPORTS_DIR / "ACTIVE_ALPHA_GOAL_COMPLETION_AUDIT.md"
LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))

PROVEN_STATUSES = {"proven", "pass", "ok"}


def now_local() -> dt.datetime:
    return dt.datetime.now(LOCAL_TZ).replace(microsecond=0)


def read_json(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def latest(pattern: str, directory: Path = EXPERIMENTS_DIR) -> Path | None:
    if not directory.exists():
        return None
    files = list(directory.glob(pattern))
    return max(files, key=lambda item: item.stat().st_mtime) if files else None


REQUIRED_DYNAMIC_SCAN_FIELDS = (
    "market_regime",
    "market_atmosphere",
    "short_term_state",
    "sentiment_state",
    "pool_width_policy",
    "pool_shape_policy",
    "selected_symbols",
    "top_dynamic_candidates",
)

UNKNOWN_REGIME_MARKERS = {
    "unknown",
    "unknown_or_not_attached",
    "manual_or_cli_open_unknown",
    "unavailable",
    "none",
    "null",
}


def dynamic_scan_contract(dynamic: dict[str, Any] | None) -> dict[str, Any]:
    dynamic = dynamic if isinstance(dynamic, dict) else {}
    status = dynamic.get("status")
    missing = [
        field for field in REQUIRED_DYNAMIC_SCAN_FIELDS
        if dynamic.get(field) in (None, "", [], {})
    ]
    fallback_static = status == "fallback_static"
    valid = status == "ok" and not missing and not fallback_static
    return {
        "valid": valid,
        "status": status,
        "missing_fields": missing,
        "fallback_static": fallback_static,
        "market_regime": dynamic.get("market_regime"),
        "market_atmosphere": dynamic.get("market_atmosphere"),
        "short_term_state": dynamic.get("short_term_state"),
        "sentiment_state": dynamic.get("sentiment_state"),
        "effective_symbols": dynamic.get("selected_symbol_count") or len(dynamic.get("selected_symbols") or []),
        "pool_width_policy": dynamic.get("pool_width_policy"),
    }


def is_clean_validation_runner(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("status") not in {"ok", "completed", "success"}:
        return False
    if payload.get("live_orders_enabled") is True or payload.get("private_api_used") is True:
        return False
    if payload.get("safety_errors"):
        return False
    dynamic = payload.get("dynamic_scan_universe") or payload.get("dynamic_scan_pool") or {}
    contract = dynamic_scan_contract(dynamic)
    return bool(payload.get("dynamic_scan_pool_enabled") and contract["valid"])


def latest_matching_json(pattern: str, directory: Path = EXPERIMENTS_DIR, max_candidates: int = 10) -> tuple[Path | None, dict[str, Any] | None]:
    if not directory.exists():
        return None, None
    files = sorted(directory.glob(pattern), reverse=True)
    fallback_path = files[0] if files else None
    fallback_payload = read_json(fallback_path) if fallback_path else None
    for path in files[: max(1, int(max_candidates or 1))]:
        payload = read_json(path)
        if is_clean_validation_runner(payload):
            return path, payload
    return fallback_path, fallback_payload


def rel(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def status_for(ok: bool, partial: bool = False) -> str:
    if ok:
        return "proven"
    if partial:
        return "partial"
    return "missing"


def check(name: str, status: str, evidence: str, detail: Any = None, next_step: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "evidence": evidence,
        "detail": detail,
        "next_step": next_step,
    }


def completion_audit_summary(
    phase_statuses: dict[str, str],
    objective_rows: list[dict[str, Any]],
    phase_check_groups: dict[str, list[dict[str, Any]]],
    current_deployment_authorization: str | None,
) -> dict[str, Any]:
    """Return a strict, machine-readable completion verdict.

    A requirement is complete only when the current evidence explicitly proves
    it. `partial`, `blocked_by_design`, and `manual_review_required` remain open
    work rather than being treated as successful by omission.
    """
    objective_counts = Counter(str(item.get("status") or "missing") for item in objective_rows)
    phase_requirement_rows: list[dict[str, Any]] = []
    for phase_name, rows in phase_check_groups.items():
        for item in rows:
            phase_requirement_rows.append(
                {
                    "requirement": f"{phase_name}: {item.get('name') or 'unnamed'}",
                    "status": str(item.get("status") or "missing"),
                    "evidence": [item.get("evidence")] if item.get("evidence") else [],
                    "next_step": item.get("next_step") or "",
                }
            )
    phase_requirement_counts = Counter(item["status"] for item in phase_requirement_rows)
    objective_blockers = [
        item for item in objective_rows
        if str(item.get("status") or "missing") not in PROVEN_STATUSES
    ]
    phase_blockers = [
        item for item in phase_requirement_rows
        if item["status"] not in PROVEN_STATUSES
    ]
    all_phase_gates_proven = (
        phase_statuses.get("phase1_paper_execution_loop") == "proven"
        and phase_statuses.get("phase2_positive_expectancy_proof") == "proven"
        and phase_statuses.get("phase3_monthly_double_pressure_test") == "proven"
        and phase_statuses.get("phase4_real_auto_trading_candidate") == "ready_for_manual_review"
    )
    goal_complete = bool(all_phase_gates_proven and not objective_blockers and not phase_blockers)

    if goal_complete:
        overall_status = "goal_proven_complete"
        max_current_action = "manual_approval_required_before_any_live_candidate"
    elif phase_statuses.get("phase1_paper_execution_loop") != "proven":
        overall_status = "phase1_loop_not_currently_proven"
        max_current_action = "offline_audit_and_public_data_rebuild_only"
    elif phase_statuses.get("phase2_positive_expectancy_proof") != "proven":
        overall_status = "paper_loop_proven_strategy_edge_not_proven"
        max_current_action = "paper_only_minimum_quality_scout"
    elif phase_statuses.get("phase3_monthly_double_pressure_test") != "proven":
        overall_status = "positive_expectancy_proven_monthly_pressure_not_proven"
        max_current_action = "paper_only_monthly_pressure_test"
    else:
        overall_status = "live_candidate_controls_or_human_approval_incomplete"
        max_current_action = "testnet_readiness_research_only"

    if current_deployment_authorization == "blocked":
        max_current_action = (
            "public_data_scan_and_paper_shadow_only_no_new_positions"
            if phase_statuses.get("phase1_paper_execution_loop") == "proven"
            else "offline_audit_and_public_data_rebuild_only"
        )

    blocking_requirements = [
        {
            "requirement": item.get("requirement"),
            "status": item.get("status"),
            "evidence": item.get("evidence") or [],
            "next_step": item.get("next_step") or "",
        }
        for item in [*objective_blockers, *phase_blockers]
    ]
    return {
        "goal_complete": goal_complete,
        "overall_status": overall_status,
        "max_current_action": max_current_action,
        "current_deployment_authorization": current_deployment_authorization or "unknown",
        "all_phase_gates_proven": all_phase_gates_proven,
        "objective_requirement_count": len(objective_rows),
        "objective_status_counts": dict(sorted(objective_counts.items())),
        "phase_requirement_count": len(phase_requirement_rows),
        "phase_requirement_status_counts": dict(sorted(phase_requirement_counts.items())),
        "strict_blocker_count": len(blocking_requirements),
        "blocking_requirements": blocking_requirements,
    }


def run_self_test() -> dict[str, Any]:
    phase_groups = {
        "phase1": [check("loop", "proven", "fixture")],
        "phase2": [check("edge", "proven", "fixture")],
        "phase3": [check("pressure", "proven", "fixture")],
        "phase4": [check("controls", "proven", "fixture")],
    }
    phase_statuses = {
        "phase1_paper_execution_loop": "proven",
        "phase2_positive_expectancy_proof": "proven",
        "phase3_monthly_double_pressure_test": "proven",
        "phase4_real_auto_trading_candidate": "ready_for_manual_review",
    }
    complete = completion_audit_summary(
        phase_statuses,
        [coverage_item("fixture objective", "proven", ["fixture"])],
        phase_groups,
        "paper_authorized",
    )
    assert complete["goal_complete"] is True
    assert complete["strict_blocker_count"] == 0

    blocked = completion_audit_summary(
        {**phase_statuses, "phase1_paper_execution_loop": "partial"},
        [coverage_item("durable cache", "partial", ["fixture"], next_step="rebuild")],
        {**phase_groups, "phase1": [check("loop", "partial", "fixture", next_step="refresh")]},
        "blocked",
    )
    assert blocked["goal_complete"] is False
    assert blocked["overall_status"] == "phase1_loop_not_currently_proven"
    assert blocked["max_current_action"] == "offline_audit_and_public_data_rebuild_only"
    assert blocked["strict_blocker_count"] == 2
    proven_loop_blocked_deployment = completion_audit_summary(
        phase_statuses,
        [coverage_item("fixture objective", "proven", ["fixture"])],
        phase_groups,
        "blocked",
    )
    assert proven_loop_blocked_deployment["max_current_action"] == "public_data_scan_and_paper_shadow_only_no_new_positions"
    return {
        "status": "ok",
        "tests": [
            "strict_complete_path_verified",
            "partial_requirement_blocks_completion_verified",
            "deployment_block_forces_offline_only_verified",
            "proven_loop_deployment_block_keeps_shadow_scan_only_verified",
        ],
    }


def order_lifecycle(ledger: dict[str, Any]) -> dict[str, Any]:
    orders = [o for o in ledger.get("paper_orders") or [] if isinstance(o, dict)]
    safe_orders = [
        o
        for o in orders
        if str(o.get("status", "")).upper() == "FILLED"
        and str(o.get("side", "")).upper() in {"BUY", "SELL"}
        and o.get("average_fill_price") is not None
        and o.get("executed_quantity") is not None
        and o.get("commission_usd") is not None
        and o.get("live_orders_enabled") is False
        and o.get("private_api_used") is False
    ]
    sides = sorted({str(o.get("side", "")).upper() for o in safe_orders})
    return {
        "order_count": len(orders),
        "safe_filled_count": len(safe_orders),
        "sides": sides,
        "buy_sell_lifecycle": {"BUY", "SELL"}.issubset(set(sides)),
    }


def exit_reason_summary(ledger: dict[str, Any]) -> dict[str, Any]:
    closed = [t for t in ledger.get("closed_trades") or [] if isinstance(t, dict)]
    reasons: dict[str, int] = {}
    for trade in closed:
        reason = str(trade.get("exit_reason") or trade.get("outcome") or "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1
    profit_reasons = {
        "take_profit",
        "take_profit_hit",
        "hard_take_profit",
        "trailing_profit_protection",
        "profit_protection",
        "profit_target",
    }
    return {
        "closed_count": len(closed),
        "reasons": reasons,
        "has_stop_loss": bool(reasons.get("stop_loss")),
        "has_expiry": bool(reasons.get("max_holding_expiry") or reasons.get("expired")),
        "has_profit_exit": any(reason in reasons for reason in profit_reasons),
        "has_information_decay": bool(reasons.get("information_decay_exit")),
    }


def duplicate_open_symbol_summary(ledger: dict[str, Any]) -> dict[str, Any]:
    positions = [p for p in ledger.get("open_positions") or [] if isinstance(p, dict)]
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for pos in positions:
        symbol = str(pos.get("symbol") or "").upper()
        if not symbol:
            continue
        by_symbol.setdefault(symbol, []).append(pos)
    allowed_follow_on_modes = {"winner_scale_in_probe", "target_sprint_scale_in_probe"}
    duplicate_symbols: dict[str, Any] = {}
    unauthorized: list[dict[str, Any]] = []
    for symbol, rows in sorted(by_symbol.items()):
        if len(rows) <= 1:
            continue
        rows = sorted(rows, key=lambda item: str(item.get("opened_at") or ""))
        modes = [str(row.get("paper_entry_mode") or "unknown") for row in rows]
        duplicate_symbols[symbol] = {
            "count": len(rows),
            "modes": modes,
            "paper_trade_ids": [row.get("paper_trade_id") for row in rows],
        }
        for row in rows[1:]:
            mode = str(row.get("paper_entry_mode") or "unknown")
            if mode not in allowed_follow_on_modes:
                unauthorized.append(
                    {
                        "symbol": symbol,
                        "paper_trade_id": row.get("paper_trade_id"),
                        "paper_entry_mode": mode,
                        "opened_at": row.get("opened_at"),
                    }
                )
    return {
        "duplicate_symbol_count": len(duplicate_symbols),
        "duplicate_symbols": duplicate_symbols,
        "allowed_follow_on_modes": sorted(allowed_follow_on_modes),
        "unauthorized_duplicate_count": len(unauthorized),
        "unauthorized_duplicates": unauthorized,
    }


def parse_dt(value: Any) -> dt.datetime | None:
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
    return parsed


def artifact_age_hours(value: Any) -> float | None:
    parsed = parse_dt(value)
    if not parsed:
        return None
    current = now_local()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    age = (current.astimezone(dt.timezone.utc) - parsed.astimezone(dt.timezone.utc)).total_seconds() / 3600.0
    return round(max(0.0, age), 4)


def trade_text_field(trade: dict[str, Any], *names: str) -> str:
    search_dicts = [
        trade,
        trade.get("market_context"),
        trade.get("signal_context"),
        trade.get("dynamic_scan_context"),
        trade.get("dynamic_scan_universe"),
        trade.get("candidate"),
        trade.get("selection_context"),
        trade.get("entry_signal"),
    ]
    for row in search_dicts:
        if not isinstance(row, dict):
            continue
        for name in names:
            value = row.get(name)
            if value not in (None, "", [], {}):
                return str(value)
    return ""


def closed_trade_quality_summary(ledger: dict[str, Any]) -> dict[str, Any]:
    closed = [t for t in ledger.get("closed_trades") or [] if isinstance(t, dict)]
    pnl_rows = [as_float(trade.get("realized_pnl_usd"), 0.0) or 0.0 for trade in closed]
    total_realized = sum(pnl_rows)
    wins = [value for value in pnl_rows if value > 0]
    losses = [value for value in pnl_rows if value < 0]
    positive_sum = sum(wins)
    max_win = max(wins) if wins else 0.0
    max_loss = min(losses) if losses else 0.0
    largest_win_share = (max_win / positive_sum * 100.0) if positive_sum > 0 else None

    family_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"closed": 0, "wins": 0, "pnl": 0.0})
    entry_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"closed": 0, "wins": 0, "pnl": 0.0})
    symbol_counts: Counter[str] = Counter()
    regime_counts: Counter[str] = Counter()
    dates: list[dt.datetime] = []

    for trade, pnl in zip(closed, pnl_rows):
        family = str(trade.get("strategy_family") or "unknown")
        entry_mode = str(trade.get("paper_entry_mode") or "unknown")
        symbol = str(trade.get("symbol") or "unknown").upper()
        family_stats[family]["closed"] += 1
        family_stats[family]["wins"] += int(pnl > 0)
        family_stats[family]["pnl"] += pnl
        entry_stats[entry_mode]["closed"] += 1
        entry_stats[entry_mode]["wins"] += int(pnl > 0)
        entry_stats[entry_mode]["pnl"] += pnl
        symbol_counts[symbol] += 1
        regime = trade_text_field(
            trade,
            "market_regime",
            "market_atmosphere",
            "short_term_state",
            "regime",
            "market_state",
        )
        if regime and regime.strip().lower() not in UNKNOWN_REGIME_MARKERS:
            regime_counts[regime] += 1
        for key in ("closed_at", "opened_at"):
            parsed = parse_dt(trade.get(key))
            if parsed:
                dates.append(parsed)

    positive_families = [
        {
            "name": name,
            "closed": stats["closed"],
            "win_rate_pct": round(stats["wins"] / stats["closed"] * 100.0, 4) if stats["closed"] else None,
            "realized_pnl_usd": round(stats["pnl"], 6),
        }
        for name, stats in sorted(family_stats.items())
        if stats["closed"] >= 3 and stats["pnl"] > 0
    ]
    positive_entry_modes = [
        {
            "name": name,
            "closed": stats["closed"],
            "win_rate_pct": round(stats["wins"] / stats["closed"] * 100.0, 4) if stats["closed"] else None,
            "realized_pnl_usd": round(stats["pnl"], 6),
        }
        for name, stats in sorted(entry_stats.items())
        if stats["closed"] >= 3 and stats["pnl"] > 0
    ]
    span_days = None
    if dates:
        span_days = (max(dates) - min(dates)).total_seconds() / 86400.0

    return {
        "closed_count": len(closed),
        "total_realized_pnl_usd": round(total_realized, 6),
        "positive_pnl_sum_usd": round(positive_sum, 6),
        "max_winning_trade_usd": round(max_win, 6),
        "max_losing_trade_usd": round(max_loss, 6),
        "largest_winner_share_of_positive_pnl_pct": round(largest_win_share, 4) if largest_win_share is not None else None,
        "positive_strategy_family_count": len(positive_families),
        "positive_strategy_families": positive_families[:8],
        "positive_entry_mode_count": len(positive_entry_modes),
        "positive_entry_modes": positive_entry_modes[:8],
        "unique_strategy_family_count": len(family_stats),
        "unique_entry_mode_count": len(entry_stats),
        "unique_symbol_count": len(symbol_counts),
        "top_symbols": symbol_counts.most_common(8),
        "explicit_regime_count": len(regime_counts),
        "regime_counts": regime_counts.most_common(8),
        "evidence_span_days": round(span_days, 4) if span_days is not None else None,
    }


def next_best_actions(
    ledger: dict[str, Any],
    validation: dict[str, Any],
    phase1_gaps: list[dict[str, Any]],
    phase2_blockers: list[dict[str, Any]],
    phase3_blockers: list[dict[str, Any]],
    phase4_blockers: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Build operator actions from current evidence, not stale position names."""
    open_positions = [p for p in ledger.get("open_positions") or [] if isinstance(p, dict)]
    open_symbols = sorted({str(p.get("symbol") or "").upper() for p in open_positions if p.get("symbol")})
    sample_gaps = validation.get("sample_gaps") if isinstance(validation.get("sample_gaps"), dict) else {}
    closed_needed = sample_gaps.get("closed_paper_trades_needed")
    win_rate = as_float(validation.get("win_rate_pct"), 0.0) or 0.0
    realized_return = as_float(validation.get("realized_net_return_on_closed_notional_pct"), 0.0) or 0.0
    actions: list[str] = []

    if open_symbols:
        actions.append(
            "Review and close/expire open paper positions first: "
            + ", ".join(open_symbols)
            + ". Do not add new exposure from stale open-position state."
        )
    else:
        actions.append(
            "No open paper positions need exit review; the next loop should focus on controlled fresh paper samples only when gates pass."
        )

    if closed_needed:
        actions.append(
            f"Collect at least {closed_needed} more closed paper trades before treating Phase 2 sample size as sufficient."
        )
    else:
        actions.append(
            "Closed paper trade count meets the minimum sample threshold; focus on win rate, net return and regime diversity."
        )

    if win_rate < 55.0 or realized_return <= 0:
        actions.append(
            "Keep recovery gating active: current win rate/net return do not justify larger sizing or live-test promotion."
        )
    regime_blocker = blocker_by_name(phase2_blockers, "Market regime evidence recorded")
    if regime_blocker:
        detail = regime_blocker.get("detail") if isinstance(regime_blocker.get("detail"), dict) else {}
        explicit_count = detail.get("explicit_regime_count")
        coverage = detail.get("context_coverage_pct")
        missing_count = detail.get("context_missing_count")
        actions.append(
            "Stamp market_context_at_entry on every new paper trade before counting it toward Phase 2 regime proof; "
            f"current explicit_regime_count={explicit_count}, context_coverage_pct={coverage}, missing_context_trades={missing_count}."
        )
    diversity_blocker = blocker_by_name(phase2_blockers, "2-3 positive strategy classes proven")
    entry_blocker = blocker_by_name(phase2_blockers, "Entry mode diversity not concentrated")
    if diversity_blocker or entry_blocker:
        actions.append(
            "Prioritize independent quality-scout samples across different strategy families and entry modes; do not fill the Phase 2 sample gap with one repeated weak pattern."
        )
    if phase1_gaps:
        actions.append(
            "Repair Phase 1 evidence before treating the loop as fully closed: "
            + ", ".join(item.get("name", "unknown") for item in phase1_gaps[:3])
            + "."
        )
    if phase2_blockers:
        actions.append(
            "Resolve Phase 2 blockers before any Phase 3 promotion: "
            + ", ".join(item.get("name", "unknown") for item in phase2_blockers[:3])
            + "."
        )
    if phase3_blockers:
        actions.append(
            "Keep the monthly-double pressure target in paper mode until current-month target, sample quality and risk controls are proven; current Phase 3 blocker: "
            + str(phase3_blockers[0].get("name", "unknown"))
            + "."
        )
    if phase4_blockers:
        actions.append(
            "Keep Binance testnet/tiny-live work gated until Phase 2 and Phase 3 are both proven; current Phase 4 blocker: "
            + str(phase4_blockers[0].get("name", "unknown"))
            + "."
        )
    actions.append(
        "Maintain paper-only safety invariants: live_orders_enabled=false, private_api_used=false, no withdrawals, no margin/futures/perps."
    )
    return actions


def latest_fast_loop_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    scan = payload.get("scan_summary") or payload.get("scan") or {}
    kline = payload.get("kline_cache_audit") or {}
    market_errors = payload.get("errors") or []
    optional_warnings = payload.get("optional_data_warnings") or []
    opened = payload.get("new_paper_trades") or payload.get("opened_positions") or []
    created_at = payload.get("created_at") or payload.get("generated_at") or payload.get("completed_at")
    age_hours = artifact_age_hours(created_at)
    return {
        "status": payload.get("status") or ("ok" if payload.get("run_id") else "unknown"),
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": age_hours,
        "fresh": age_hours is not None and age_hours <= 6.0,
        "data_status": payload.get("data_status"),
        "live_orders_enabled": payload.get("live_orders_enabled") is True,
        "private_api_used": payload.get("private_api_used") is True,
        "kline_status": kline.get("status"),
        "kline_verified_count": kline.get("verified_count"),
        "frames_loaded": scan.get("frames_loaded"),
        "strategies_scanned": scan.get("strategies_scanned"),
        "current_signal_strategies": scan.get("current_signal_strategies"),
        "candidate_count": scan.get("candidate_count"),
        "deduplicated_candidate_count": scan.get("deduplicated_candidate_count"),
        "new_paper_trade_count": len(opened),
        "reviewed_position_count": len(payload.get("reviewed_positions") or []),
        "market_error_count": len(market_errors),
        "market_error_names": [str(item.get("name") or "unknown") for item in market_errors[:8] if isinstance(item, dict)],
        "optional_warning_count": len(optional_warnings),
        "optional_warning_names": [str(item.get("name") or "unknown") for item in optional_warnings[:8] if isinstance(item, dict)],
    }


def latest_runner_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    delta = payload.get("progress_delta") or {}
    dynamic = payload.get("dynamic_scan_universe") or {}
    shadow = payload.get("recovery_shadow_scan") if isinstance(payload.get("recovery_shadow_scan"), dict) else {}
    if not shadow:
        for child in payload.get("child_runs") or []:
            if not isinstance(child, dict) or child.get("label") != "fast_crypto_recovery_shadow_scan":
                continue
            candidate = child.get("stdout_json")
            if isinstance(candidate, dict):
                shadow = candidate
                break
    shadow_scan_summary = shadow.get("scan_summary") if isinstance(shadow.get("scan_summary"), dict) else {}
    dynamic_contract = dynamic_scan_contract(dynamic)
    created_at = payload.get("created_at") or payload.get("generated_at") or payload.get("completed_at")
    age_hours = artifact_age_hours(created_at)
    return {
        "status": payload.get("status"),
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": age_hours,
        "fresh": age_hours is not None and age_hours <= 6.0,
        "live_orders_enabled": payload.get("live_orders_enabled") is True,
        "dynamic_scan_pool_enabled": payload.get("dynamic_scan_pool_enabled") is True,
        "dynamic_scan_contract_valid": dynamic_contract["valid"],
        "dynamic_scan_missing_fields": dynamic_contract["missing_fields"],
        "dynamic_scan_status": dynamic_contract["status"],
        "dynamic_scan_fallback_static": dynamic_contract["fallback_static"],
        "dynamic_scan_short_term_state": dynamic_contract["short_term_state"],
        "dynamic_scan_sentiment_state": dynamic_contract["sentiment_state"],
        "dynamic_scan_pool_width_policy": dynamic_contract["pool_width_policy"],
        "market_regime": dynamic.get("market_regime"),
        "market_atmosphere": dynamic.get("market_atmosphere"),
        "effective_symbols": dynamic_contract["effective_symbols"] or payload.get("effective_symbols"),
        "open_count_delta": delta.get("open_count_delta"),
        "paper_order_count_delta": delta.get("paper_order_count_delta"),
        "max_allowed_action_after": delta.get("max_allowed_action_after"),
        "child_failures": payload.get("child_failures") or [],
        "safety_errors": payload.get("safety_errors") or [],
        "shadow_scan_status": shadow.get("status"),
        "shadow_scan_dry_run": shadow.get("dry_run"),
        "shadow_scan_data_status": shadow.get("data_status"),
        "shadow_frames_loaded": shadow.get("frames_loaded") or shadow_scan_summary.get("frames_loaded"),
        "shadow_strategies_scanned": shadow.get("strategies_scanned") or shadow_scan_summary.get("strategies_scanned"),
        "shadow_candidate_count": shadow.get("deduplicated_candidate_count") or shadow_scan_summary.get("deduplicated_candidate_count"),
    }


def latest_kline_builder_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    files = payload.get("files_written") or []
    failures = payload.get("failures") or []
    selected = payload.get("selected_symbols") or []
    storage = inspect_kline_cache_storage(payload)
    completed_at = payload.get("completed_at") or payload.get("generated_at")
    age_hours = artifact_age_hours(completed_at)
    return {
        "status": payload.get("status") or ("ok" if files else "missing"),
        "live_orders_enabled": payload.get("live_orders_enabled") is True,
        "public_market_data_only": payload.get("binance_public_market_data_only") is True,
        "cache_dir": payload.get("cache_dir"),
        "completed_at": completed_at,
        "age_hours": age_hours,
        "fresh": age_hours is not None and age_hours <= 6.0,
        "requested_intervals": payload.get("requested_intervals") or [],
        "selected_symbol_count": len(selected),
        "file_count": payload.get("file_count") if payload.get("file_count") is not None else len(files),
        "failure_count": payload.get("failure_count") if payload.get("failure_count") is not None else len(failures),
        "storage_status": storage.get("storage_status"),
        "cache_dir_exists": storage.get("cache_dir_exists"),
        "declared_file_count": storage.get("declared_file_count"),
        "actual_file_count": storage.get("actual_file_count"),
        "actual_intervals": storage.get("actual_intervals") or [],
        "missing_required_intervals": storage.get("missing_required_intervals") or [],
        "replay_available": storage.get("replay_available"),
        "refresh_required": storage.get("refresh_required"),
    }


def latest_binance_health_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    endpoint_counts = summary.get("endpoint_counts") if isinstance(summary.get("endpoint_counts"), dict) else {}
    ok_endpoint_count = sum(int(value or 0) for key, value in endpoint_counts.items() if str(key).endswith(":ok"))
    failed_endpoint_count = sum(
        int(value or 0)
        for key, value in endpoint_counts.items()
        if not str(key).endswith(":ok")
    )
    created_at = payload.get("created_at")
    age_hours = artifact_age_hours(created_at)
    return {
        "status": summary.get("status") or payload.get("status") or "unknown",
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": age_hours,
        "fresh": age_hours is not None and age_hours <= 6.0,
        "passed_symbol_count": summary.get("passed_symbol_count"),
        "warning_symbol_count": summary.get("warning_symbol_count"),
        "blocked_symbol_count": summary.get("blocked_symbol_count"),
        "ok_endpoint_count": ok_endpoint_count,
        "failed_endpoint_count": failed_endpoint_count,
        "endpoint_counts": endpoint_counts,
        "live_orders_enabled": payload.get("live_orders_enabled") is True,
        "private_api_used": payload.get("private_api_used") is True,
        "allow_real_orders": payload.get("allow_real_orders") is True,
        "ledger_mutated": payload.get("ledger_mutated") is True,
    }


def latest_signal_contract_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "status": payload.get("status") or summary.get("status") or "unknown",
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "signal_count": summary.get("signal_count"),
        "complete_count": summary.get("complete_count"),
        "partial_count": summary.get("partial_count"),
        "incomplete_count": summary.get("incomplete_count"),
        "unsafe_count": summary.get("unsafe_count"),
        "missing_field_counts": summary.get("missing_field_counts") or {},
        "live_orders_enabled": payload.get("live_orders_enabled") is True,
        "private_api_used": payload.get("private_api_used") is True,
        "allow_real_orders": payload.get("allow_real_orders") is True,
        "ledger_mutated": payload.get("ledger_mutated") is True,
    }


def latest_ledger_integrity_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    audit = payload.get("audit") if isinstance(payload.get("audit"), dict) else {}
    return {
        "status": audit.get("status") or payload.get("status") or "unknown",
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "order_count": audit.get("order_count"),
        "open_position_count": audit.get("open_position_count"),
        "closed_trade_count": audit.get("closed_trade_count"),
        "warning_count": audit.get("warning_count"),
        "blocked_count": audit.get("blocked_count"),
        "live_orders_enabled": payload.get("live_orders_enabled") is True,
        "private_api_used": payload.get("private_api_used") is True,
        "ledger_mutated": payload.get("ledger_mutated") is True,
        "repairs_applied": len(payload.get("repairs_applied") or []),
    }


def validation_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    portfolio = payload.get("current_portfolio_metrics") or {}
    paper = payload.get("paper_sample_metrics") or {}
    plan = payload.get("validation_sample_plan") or {}
    recovery = payload.get("validation_recovery_plan") or {}
    return {
        "status": payload.get("status"),
        "equity_usd": portfolio.get("equity_usd"),
        "net_return_pct": portfolio.get("net_return_pct"),
        "max_drawdown_pct": portfolio.get("max_drawdown_pct"),
        "open_count": paper.get("open_count"),
        "closed_count": paper.get("closed_count"),
        "win_rate_pct": paper.get("win_rate_pct"),
        "realized_net_return_on_closed_notional_pct": paper.get("realized_net_return_on_closed_notional_pct"),
        "failed_gates": plan.get("failed_gates") or [],
        "sample_gaps": plan.get("sample_gaps") or {},
        "recovery_status": recovery.get("status"),
        "monthly_target": recovery.get("monthly_target") or {},
    }


def authoritative_ledger_validation_summary(
    ledger: dict[str, Any],
    fallback: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Recompute portfolio proof metrics from the current ledger.

    Runner post-audits remain useful scan evidence, but they can be older than
    a later exit reconciliation. Phase 2/3 portfolio metrics must therefore
    come from the ledger that owns the paper orders and closed trades.
    """

    out = dict(fallback or {})
    closed = [trade for trade in ledger.get("closed_trades") or [] if isinstance(trade, dict)]
    wins = 0
    realized_pnl = 0.0
    closed_notional = 0.0
    for trade in closed:
        pnl = as_float(trade.get("realized_pnl_usd"), 0.0) or 0.0
        realized_pnl += pnl
        closed_notional += as_float(trade.get("notional_usd"), 0.0) or 0.0
        if trade.get("outcome") == "hit" or pnl > 0:
            wins += 1
    closed_count = len(closed)
    win_rate = wins / closed_count * 100.0 if closed_count else 0.0
    realized_return = realized_pnl / closed_notional * 100.0 if closed_notional else 0.0
    current = now or now_local()
    month_id = current.astimezone(LOCAL_TZ).strftime("%Y-%m")
    baselines = ledger.get("monthly_goal_baselines") if isinstance(ledger.get("monthly_goal_baselines"), dict) else {}
    baseline_record = baselines.get(month_id) if isinstance(baselines.get(month_id), dict) else {}
    current_equity = as_float(ledger.get("equity_usd"), as_float(ledger.get("cash_usd"), 0.0)) or 0.0
    month_start_equity = as_float(
        baseline_record.get("month_start_equity_usd"),
        current_equity,
    ) or current_equity
    target_equity = as_float(
        baseline_record.get("target_equity_usd"),
        month_start_equity * 2.0,
    ) or month_start_equity * 2.0
    target_gap = max(0.0, target_equity - current_equity)
    current_return_pct = (
        (current_equity / month_start_equity - 1.0) * 100.0
        if month_start_equity
        else 0.0
    )
    sample_gaps = dict(out.get("sample_gaps") or {})
    sample_gaps["closed_paper_trades_needed"] = max(0, 30 - closed_count)
    out.update(
        {
            "status": "ok",
            "source": "paper_ledger_authoritative",
            "equity_usd": current_equity,
            "net_return_pct": as_float(ledger.get("net_return_pct"), 0.0) or 0.0,
            "max_drawdown_pct": as_float(ledger.get("max_drawdown_pct"), 0.0) or 0.0,
            "open_count": len(ledger.get("open_positions") or []),
            "closed_count": closed_count,
            "win_rate_pct": win_rate,
            "realized_pnl_usd": realized_pnl,
            "closed_notional_usd": closed_notional,
            "realized_net_return_on_closed_notional_pct": realized_return,
            "sample_gaps": sample_gaps,
            "monthly_target": {
                "target_model": baseline_record.get("target_model") or "monthly_compounding_double",
                "month_id": month_id,
                "baseline_source": baseline_record.get("baseline_source") or "ledger_equity_fallback",
                "month_start_equity_usd": month_start_equity,
                "target_equity_usd": target_equity,
                "current_equity_usd": current_equity,
                "target_gap_usd": target_gap,
                "progress_pct": max(0.0, current_return_pct),
                "current_return_pct": current_return_pct,
                "required_return_pct_from_current_equity": (
                    target_gap / current_equity * 100.0 if current_equity else None
                ),
            },
        }
    )
    return out


def market_context_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {"status": "missing"}
    all_summary = payload.get("all_summary") if isinstance(payload.get("all_summary"), dict) else {}
    return {
        "status": payload.get("status") or "unknown",
        "run_id": payload.get("run_id"),
        "trade_count": all_summary.get("trade_count"),
        "complete_count": all_summary.get("complete_count"),
        "partial_count": all_summary.get("partial_count"),
        "missing_count": all_summary.get("missing_count"),
        "coverage_pct": all_summary.get("coverage_pct"),
        "explicit_regime_count": all_summary.get("explicit_regime_count"),
        "regime_counts": all_summary.get("regime_counts") or [],
        "live_orders_enabled": payload.get("live_orders_enabled") is True,
        "private_api_used": payload.get("private_api_used") is True,
        "ledger_mutated": payload.get("ledger_mutated") is True,
    }


def paper_auto_learning_summary(overlay: dict[str, Any] | None, evolver: dict[str, Any] | None) -> dict[str, Any]:
    overlay = overlay if isinstance(overlay, dict) else {}
    evolver = evolver if isinstance(evolver, dict) else {}
    summary = evolver.get("summary") if isinstance(evolver.get("summary"), dict) else {}
    change_log = overlay.get("change_log") if isinstance(overlay.get("change_log"), list) else []
    change_ids = [
        str(item.get("change_id") or "").strip()
        for item in change_log
        if isinstance(item, dict) and str(item.get("change_id") or "").strip()
    ]
    duplicate_change_ids = sorted(
        change_id for change_id, count in Counter(change_ids).items() if count > 1
    )
    rollback_checks = evolver.get("rollback_checks") if isinstance(evolver.get("rollback_checks"), list) else []
    awaiting = [
        item for item in rollback_checks
        if isinstance(item, dict) and ((item.get("forward_sample") or {}).get("status") == "awaiting_forward_samples")
    ]
    ready = [
        item for item in rollback_checks
        if isinstance(item, dict) and ((item.get("forward_sample") or {}).get("status") == "ready")
    ]
    reverted = [
        item for item in rollback_checks
        if isinstance(item, dict) and item.get("status_after") == "paper_reverted"
    ]
    safe_flags = (
        overlay.get("live_orders_enabled") is False
        and overlay.get("private_api_used") is False
        and overlay.get("allow_real_orders") is False
        and evolver.get("live_orders_enabled") is False
        and evolver.get("private_api_used") is False
        and evolver.get("allow_real_orders") is False
    )
    applied_or_ab = summary.get("applied_or_ab_testing")
    if applied_or_ab is None:
        applied_or_ab = len([
            item for item in change_log
            if isinstance(item, dict) and item.get("status") in {"paper_applied", "paper_ab_testing"}
        ])
    return {
        "status": evolver.get("status") or "missing",
        "run_id": evolver.get("run_id"),
        "strategy_version": overlay.get("strategy_version") or summary.get("strategy_version"),
        "auto_learning_enabled": overlay.get("auto_learning_enabled") is True,
        "auto_apply_scope": overlay.get("auto_apply_scope"),
        "safe_flags": safe_flags,
        "change_log_count": len(change_log),
        "unique_change_id_count": len(set(change_ids)),
        "duplicate_change_ids": duplicate_change_ids,
        "applied_or_ab_testing": applied_or_ab,
        "newly_applied_or_ab_testing": summary.get("newly_applied_or_ab_testing"),
        "reverted": summary.get("reverted") if summary.get("reverted") is not None else len(reverted),
        "rollback_check_count": len(rollback_checks),
        "awaiting_forward_samples": summary.get("awaiting_forward_samples") if summary.get("awaiting_forward_samples") is not None else len(awaiting),
        "ready_forward_samples": len(ready),
        "deduped_change_log_removed": summary.get("deduped_change_log_removed"),
        "strategy_version_changed": summary.get("strategy_version_changed"),
        "entry_mode_rule_count": len(overlay.get("entry_mode_rules") or {}),
        "strategy_family_rule_count": len(overlay.get("strategy_family_rules") or {}),
        "interval_rule_count": len(overlay.get("interval_rules") or {}),
        "live_orders_enabled": overlay.get("live_orders_enabled") is True or evolver.get("live_orders_enabled") is True,
        "private_api_used": overlay.get("private_api_used") is True or evolver.get("private_api_used") is True,
    }


def coverage_item(
    requirement: str,
    status: str,
    evidence: list[str],
    detail: dict[str, Any] | None = None,
    next_step: str = "",
) -> dict[str, Any]:
    return {
        "requirement": requirement,
        "status": status,
        "evidence": [item for item in evidence if item],
        "detail": detail or {},
        "next_step": next_step,
    }


def blocker_by_name(blockers: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for item in blockers:
        if item.get("name") == name:
            return item
    return None


def build_record() -> dict[str, Any]:
    created = now_local()
    ledger = read_json(LEDGER_PATH) or {}
    fast_path = latest("*fast-crypto-paper-auto-trader.json")
    runner_path, runner_payload = latest_matching_json("*validation-progress-runner.json")
    flow_path = latest("*binance-api-compounding-flow-audit.json")
    kline_builder_path = latest("*binance-kline-cache-builder.json")
    kline_repro_path = EXPERIMENTS_DIR / "kline-research-reproducibility-audit.json"
    binance_health_path = latest("*binance-market-data-health.json")
    signal_contract_path = latest("*paper-signal-contract-audit.json")
    ledger_integrity_path = latest("*paper-ledger-integrity-audit.json")
    market_context_path = latest("*paper-market-context-audit.json")
    attribution_path = latest("*paper-trade-attribution-report.json")
    capital_allocation_path = latest("*paper-capital-allocation-audit.json")
    paper_auto_evolver_path = latest("*paper-strategy-auto-evolver.json")
    strategy_backlog_path = EXPERIMENTS_DIR / "strategy-iteration-backlog.json"
    strategy_enforcement_path = EXPERIMENTS_DIR / "strategy-proposal-enforcement-audit.json"
    preflight_path = latest("*resume-preflight.json")
    risk_control_path = EXPERIMENTS_DIR / "paper-testnet-risk-control-audit.json"
    validation_path = None
    validation_payload = None
    # validation_sample_auditor usually prints stdout, not a persisted file; use
    # the latest runner embedded post-audit when available.
    if runner_payload:
        validation_payload = (runner_payload.get("post_validation") or {}).get("stdout_json")
        validation_path = runner_path
    if not isinstance(validation_payload, dict):
        validation_payload = {}
    fast = latest_fast_loop_summary(read_json(fast_path))
    runner = latest_runner_summary(runner_payload)
    kline_builder = latest_kline_builder_summary(read_json(kline_builder_path))
    kline_repro_payload = read_json(kline_repro_path) or {}
    kline_repro_summary = (
        kline_repro_payload.get("summary")
        if isinstance(kline_repro_payload.get("summary"), dict)
        else {}
    )
    binance_health = latest_binance_health_summary(read_json(binance_health_path))
    signal_contract = latest_signal_contract_summary(read_json(signal_contract_path))
    ledger_integrity = latest_ledger_integrity_summary(read_json(ledger_integrity_path))
    market_context = market_context_summary(read_json(market_context_path))
    attribution_payload = read_json(attribution_path) or {}
    capital_payload = read_json(capital_allocation_path) or {}
    paper_auto_evolver_payload = read_json(paper_auto_evolver_path) or {}
    paper_strategy_overlay_payload = read_json(PAPER_STRATEGY_OVERLAY_PATH) or {}
    strategy_backlog_payload = read_json(strategy_backlog_path) or {}
    strategy_enforcement_payload = read_json(strategy_enforcement_path) or {}
    preflight_payload = read_json(preflight_path) or {}
    automation_recovery_payload = read_json(AUTOMATION_RECOVERY_PLAN_PATH) or {}
    risk_control_payload = read_json(risk_control_path) or {}
    risk_control = risk_control_payload.get("risk_control") if isinstance(risk_control_payload.get("risk_control"), dict) else {}
    validation = authoritative_ledger_validation_summary(
        ledger,
        validation_summary(validation_payload),
        now=created,
    )
    validation_path = LEDGER_PATH
    flow = read_json(flow_path) or {}
    lifecycle = order_lifecycle(ledger)
    exits = exit_reason_summary(ledger)
    trade_quality = closed_trade_quality_summary(ledger)
    fast_core_market_usable = (
        fast.get("status") == "ok"
        and fast.get("fresh") is True
        and fast.get("live_orders_enabled") is False
        and fast.get("private_api_used") is False
        and (fast.get("reviewed_position_count") or 0) > 0
        and (fast.get("deduplicated_candidate_count") or 0) > 0
        and fast.get("kline_status") == "verified"
        and (fast.get("frames_loaded") or 0) > 0
        and (fast.get("strategies_scanned") or 0) > 0
    )
    fast_minor_symbol_degradation_only = (
        fast.get("data_status") == "degraded"
        and fast_core_market_usable
        and (fast.get("market_error_count") or 0) <= 1
    )
    binance_health_proves_market_data = (
        binance_health.get("status") == "pass"
        and binance_health.get("fresh") is True
        and (binance_health.get("passed_symbol_count") or 0) > 0
        and (binance_health.get("blocked_symbol_count") or 0) == 0
        and (binance_health.get("failed_endpoint_count") or 0) == 0
        and binance_health.get("live_orders_enabled") is False
        and binance_health.get("private_api_used") is False
        and binance_health.get("allow_real_orders") is False
        and binance_health.get("ledger_mutated") is False
    )

    checks_phase1 = [
        check(
            "Binance public ticker/order-book/depth usable",
            status_for(
                (
                    fast.get("data_status") in {"verified", "verified_with_optional_source_warnings"}
                    and fast.get("fresh") is True
                )
                or fast_minor_symbol_degradation_only
                or binance_health_proves_market_data,
                partial=(
                    fast_core_market_usable
                    or binance_health.get("status") in {"pass", "warn", "partial"}
                ),
            ),
            rel(fast_path),
            {
                "status": fast.get("status"),
                "data_status": fast.get("data_status"),
                "fast_age_hours": fast.get("age_hours"),
                "fast_fresh": fast.get("fresh"),
                "binance_health_status": binance_health.get("status"),
                "binance_health_age_hours": binance_health.get("age_hours"),
                "binance_health_fresh": binance_health.get("fresh"),
                "binance_health_passed_symbol_count": binance_health.get("passed_symbol_count"),
                "binance_health_failed_endpoint_count": binance_health.get("failed_endpoint_count"),
                "minor_symbol_degradation_only": fast_minor_symbol_degradation_only,
                "reviewed_position_count": fast.get("reviewed_position_count"),
                "deduplicated_candidate_count": fast.get("deduplicated_candidate_count"),
                "kline_status": fast.get("kline_status"),
                "frames_loaded": fast.get("frames_loaded"),
                "strategies_scanned": fast.get("strategies_scanned"),
                "market_error_count": fast.get("market_error_count"),
                "market_error_names": fast.get("market_error_names"),
                "optional_warning_count": fast.get("optional_warning_count"),
                "optional_warning_names": fast.get("optional_warning_names"),
            },
            "If degradation is more than a single non-critical symbol/source timeout, keep the degraded reason visible and avoid new paper samples from incomplete data.",
        ),
        check(
            "Binance Kline cache verified",
            status_for(
                (
                    fast.get("kline_status") == "verified"
                    and fast.get("fresh") is True
                    and (fast.get("frames_loaded") or 0) > 0
                    and (fast.get("strategies_scanned") or 0) > 0
                )
                or (
                    kline_builder.get("status") == "ok"
                    and kline_builder.get("fresh") is True
                    and (kline_builder.get("actual_file_count") or 0) >= 10
                    and kline_builder.get("replay_available") is True
                    and kline_builder.get("public_market_data_only") is True
                ),
                partial=(
                    (fast.get("kline_status") in {"verified", "stale"} and (fast.get("frames_loaded") or 0) > 0)
                    or (kline_builder.get("declared_file_count") or 0) >= 10
                ),
            ),
            f"{rel(fast_path)}, {rel(kline_builder_path)}",
            {
                "fast_kline_status": fast.get("kline_status"),
                "fast_age_hours": fast.get("age_hours"),
                "fast_fresh": fast.get("fresh"),
                "fast_kline_verified_count": fast.get("kline_verified_count"),
                "frames_loaded": fast.get("frames_loaded"),
                "strategies_scanned": fast.get("strategies_scanned"),
                "builder_status": kline_builder.get("status"),
                "builder_file_count": kline_builder.get("file_count"),
                "builder_declared_file_count": kline_builder.get("declared_file_count"),
                "builder_actual_file_count": kline_builder.get("actual_file_count"),
                "builder_storage_status": kline_builder.get("storage_status"),
                "builder_replay_available": kline_builder.get("replay_available"),
                "builder_age_hours": kline_builder.get("age_hours"),
                "builder_fresh": kline_builder.get("fresh"),
                "builder_failure_count": kline_builder.get("failure_count"),
                "builder_cache_dir": kline_builder.get("cache_dir"),
            },
            "If fast cache is stale, rely only on freshly built dynamic cache or rerun a smaller symbol set before sampling.",
        ),
        check(
            "Binance market data health audit passed",
            status_for(
                binance_health.get("status") == "pass"
                and binance_health.get("fresh") is True
                and (binance_health.get("passed_symbol_count") or 0) > 0
                and (binance_health.get("blocked_symbol_count") or 0) == 0
                and (binance_health.get("failed_endpoint_count") or 0) == 0
                and binance_health.get("live_orders_enabled") is False
                and binance_health.get("private_api_used") is False
                and binance_health.get("allow_real_orders") is False
                and binance_health.get("ledger_mutated") is False,
                partial=binance_health.get("status") in {"pass", "warn", "partial"},
            ),
            rel(binance_health_path),
            binance_health,
            "If this is missing or blocked, degrade the loop to report-only before any new paper entry scan.",
        ),
        check(
            "Paper ledger maintained",
            status_for(bool(ledger) and ledger.get("live_orders_enabled") is False),
            rel(LEDGER_PATH),
            {
                "cash_usd": ledger.get("cash_usd"),
                "equity_usd": ledger.get("equity_usd"),
                "open_positions": len(ledger.get("open_positions") or []),
                "closed_trades": len(ledger.get("closed_trades") or []),
                "paper_orders": len(ledger.get("paper_orders") or []),
            },
        ),
        check(
            "Paper ledger integrity audit passed",
            status_for(
                ledger_integrity.get("status") == "pass"
                and (ledger_integrity.get("order_count") or 0) > 0
                and (ledger_integrity.get("blocked_count") or 0) == 0
                and (ledger_integrity.get("warning_count") or 0) == 0
                and ledger_integrity.get("live_orders_enabled") is False
                and ledger_integrity.get("private_api_used") is False,
                partial=ledger_integrity.get("status") in {"pass", "warn", "partial"},
            ),
            rel(ledger_integrity_path),
            ledger_integrity,
            "Repair only local paper metadata when explicitly requested; never use a broken ledger for new paper exposure.",
        ),
        check(
            "Dynamic candidate scan executed",
            status_for(
                runner.get("dynamic_scan_pool_enabled")
                and runner.get("dynamic_scan_contract_valid")
                and runner.get("fresh") is True
                and (
                    (fast.get("fresh") is True and (fast.get("strategies_scanned") or 0) > 0)
                    or (
                        runner.get("shadow_scan_status") == "ok"
                        and runner.get("shadow_scan_dry_run") is True
                        and (runner.get("shadow_frames_loaded") or 0) > 0
                        and (runner.get("shadow_strategies_scanned") or 0) > 0
                    )
                ),
                partial=(
                    runner.get("dynamic_scan_pool_enabled")
                    and (
                        (fast.get("strategies_scanned") or 0) > 0
                        or (runner.get("shadow_strategies_scanned") or 0) > 0
                    )
                ),
            ),
            rel(runner_path),
            {
                "dynamic_scan_status": runner.get("dynamic_scan_status"),
                "runner_created_at": runner.get("created_at"),
                "runner_age_hours": runner.get("age_hours"),
                "runner_fresh": runner.get("fresh"),
                "fast_fresh": fast.get("fresh"),
                "dynamic_scan_contract_valid": runner.get("dynamic_scan_contract_valid"),
                "missing_dynamic_scan_fields": runner.get("dynamic_scan_missing_fields"),
                "fallback_static": runner.get("dynamic_scan_fallback_static"),
                "market_regime": runner.get("market_regime"),
                "market_atmosphere": runner.get("market_atmosphere"),
                "short_term_state": runner.get("dynamic_scan_short_term_state"),
                "sentiment_state": runner.get("dynamic_scan_sentiment_state"),
                "pool_width_policy": runner.get("dynamic_scan_pool_width_policy"),
                "effective_symbols": runner.get("effective_symbols"),
                "candidate_count": fast.get("candidate_count"),
                "deduplicated_candidate_count": fast.get("deduplicated_candidate_count"),
                "shadow_scan_status": runner.get("shadow_scan_status"),
                "shadow_scan_dry_run": runner.get("shadow_scan_dry_run"),
                "shadow_scan_data_status": runner.get("shadow_scan_data_status"),
                "shadow_frames_loaded": runner.get("shadow_frames_loaded"),
                "shadow_strategies_scanned": runner.get("shadow_strategies_scanned"),
                "shadow_candidate_count": runner.get("shadow_candidate_count"),
            },
            "Every live-market run must rebuild the pool from market atmosphere and fresh/non-stale sentiment before K-line strategy scanning; static symbols are only a degraded fallback.",
        ),
        check(
            "Paper signal contract completeness audit passed",
            status_for(
                signal_contract.get("status") == "pass"
                and (signal_contract.get("signal_count") or 0) > 0
                and (signal_contract.get("incomplete_count") or 0) == 0
                and (signal_contract.get("unsafe_count") or 0) == 0
                and signal_contract.get("live_orders_enabled") is False
                and signal_contract.get("private_api_used") is False
                and signal_contract.get("allow_real_orders") is False
                and signal_contract.get("ledger_mutated") is False,
                partial=signal_contract.get("status") in {"pass", "warn", "partial"},
            ),
            rel(signal_contract_path),
            signal_contract,
            "Incomplete executable contracts must stay watch/report-only until entry, stop, target, time window, confidence and failure conditions are present.",
        ),
        check(
            "Paper buy lifecycle simulated",
            status_for((lifecycle.get("safe_filled_count") or 0) > 0 and "BUY" in lifecycle.get("sides", [])),
            rel(LEDGER_PATH),
            lifecycle,
        ),
        check(
            "Paper sell lifecycle simulated",
            status_for(lifecycle.get("buy_sell_lifecycle")),
            rel(LEDGER_PATH),
            lifecycle,
        ),
        check(
            "Stop-loss exit sample exists",
            status_for(exits.get("has_stop_loss")),
            rel(LEDGER_PATH),
            exits,
        ),
        check(
            "Take-profit or trailing-profit exit sample exists",
            status_for(exits.get("has_profit_exit"), partial=bool((ledger.get("open_positions") or []))),
            rel(LEDGER_PATH),
            exits,
            "Need an actual closed paper trade via take_profit/trailing_profit_protection; current open trades only prove target levels exist.",
        ),
        check(
            "Max-holding expiry exit sample exists",
            status_for(exits.get("has_expiry")),
            rel(LEDGER_PATH),
            exits,
        ),
        check(
            "Per-run reports and trade records generated",
            status_for(bool(fast_path) and bool(runner_path)),
            f"{rel(REPORTS_DIR)}/ and {rel(EXPERIMENTS_DIR)}/",
            {"fast_experiment": rel(fast_path), "runner_experiment": rel(runner_path)},
        ),
        check(
            "Safety flags remain paper-only",
            status_for(
                ledger.get("live_orders_enabled") is False
                and ledger.get("private_api_used") is False
                and fast.get("live_orders_enabled") is False
                and fast.get("private_api_used") is False
                and not runner.get("safety_errors")
            ),
            f"{rel(LEDGER_PATH)}, {rel(fast_path)}, {rel(runner_path)}",
            {
                "ledger_live_orders_enabled": ledger.get("live_orders_enabled"),
                "ledger_private_api_used": ledger.get("private_api_used"),
                "fast_live_orders_enabled": fast.get("live_orders_enabled"),
                "fast_private_api_used": fast.get("private_api_used"),
                "runner_safety_errors": runner.get("safety_errors"),
            },
        ),
    ]

    min_closed = 30
    closed_count = int(validation.get("closed_count") or 0)
    win_rate = as_float(validation.get("win_rate_pct"), 0.0) or 0.0
    net_closed = as_float(validation.get("realized_net_return_on_closed_notional_pct"), 0.0) or 0.0
    max_dd = as_float(validation.get("max_drawdown_pct"), 0.0) or 0.0
    checks_phase2 = [
        check("30-50 closed paper trades", status_for(closed_count >= min_closed), rel(validation_path), {"closed_count": closed_count, "minimum": min_closed}),
        check("Win rate >= 55%", status_for(win_rate >= 55.0 and closed_count >= min_closed), rel(validation_path), {"win_rate_pct": win_rate, "closed_count": closed_count}),
        check("Closed net return after costs > 0", status_for(net_closed > 0 and closed_count >= min_closed), rel(validation_path), {"realized_net_return_on_closed_notional_pct": net_closed}),
        check("Max drawdown within 10-15%", status_for(max_dd >= -15.0), rel(validation_path), {"max_drawdown_pct": max_dd}),
        check(
            "Walk-forward research raw Klines reproducible",
            status_for(
                str(kline_repro_payload.get("status") or "").startswith("pass")
                and (kline_repro_summary.get("reproducible_count") or 0) > 0
                and (kline_repro_summary.get("promotion_allowed_count") or 0) > 0,
                partial=(kline_repro_summary.get("artifact_count") or 0) > 0,
            ),
            rel(kline_repro_path),
            {
                "status": kline_repro_payload.get("status"),
                "artifact_count": kline_repro_summary.get("artifact_count"),
                "reproducible_count": kline_repro_summary.get("reproducible_count"),
                "nonreproducible_count": kline_repro_summary.get("nonreproducible_count"),
                "promotion_allowed_count": kline_repro_summary.get("promotion_allowed_count"),
                "max_allowed_action": kline_repro_summary.get("max_allowed_action"),
            },
            "Rebuild durable Binance Klines and rerun walk-forward research before any historical high-return candidate can support Phase 2.",
        ),
        check(
            "2-3 positive strategy classes proven",
            status_for(
                (trade_quality.get("positive_strategy_family_count") or 0) >= 2
                and closed_count >= min_closed
                and net_closed > 0
            ),
            rel(LEDGER_PATH),
            {
                "positive_strategy_family_count": trade_quality.get("positive_strategy_family_count"),
                "positive_strategy_families": trade_quality.get("positive_strategy_families"),
                "unique_strategy_family_count": trade_quality.get("unique_strategy_family_count"),
            },
            "Need at least two strategy families with 3+ closed trades each and positive realized PnL before calling Phase 2 diversified.",
        ),
        check(
            "Entry mode diversity not concentrated",
            status_for(
                (trade_quality.get("positive_entry_mode_count") or 0) >= 2
                and closed_count >= min_closed
                and net_closed > 0
            ),
            rel(LEDGER_PATH),
            {
                "positive_entry_mode_count": trade_quality.get("positive_entry_mode_count"),
                "positive_entry_modes": trade_quality.get("positive_entry_modes"),
                "unique_entry_mode_count": trade_quality.get("unique_entry_mode_count"),
            },
            "Need more than one positive entry mode; otherwise the system may be overfit to one paper entry style.",
        ),
        check(
            "Market regime evidence recorded",
            status_for(
                (trade_quality.get("explicit_regime_count") or 0) >= 3
                and closed_count >= min_closed,
                partial=(trade_quality.get("explicit_regime_count") or 0) > 0,
            ),
            rel(market_context_path) or rel(LEDGER_PATH),
            {
                "explicit_regime_count": trade_quality.get("explicit_regime_count"),
                "regime_counts": trade_quality.get("regime_counts"),
                "context_coverage_pct": market_context.get("coverage_pct"),
                "context_complete_count": market_context.get("complete_count"),
                "context_missing_count": market_context.get("missing_count"),
            },
            "Future paper trades should stamp market_regime/market_atmosphere at entry so Phase 2 can prove uptrend, chop, pullback and selloff coverage.",
        ),
        check(
            "No single winning trade dominates proof",
            status_for(
                closed_count >= min_closed
                and net_closed > 0
                and trade_quality.get("largest_winner_share_of_positive_pnl_pct") is not None
                and trade_quality["largest_winner_share_of_positive_pnl_pct"] <= 40.0
            ),
            rel(LEDGER_PATH),
            {
                "total_realized_pnl_usd": trade_quality.get("total_realized_pnl_usd"),
                "positive_pnl_sum_usd": trade_quality.get("positive_pnl_sum_usd"),
                "max_winning_trade_usd": trade_quality.get("max_winning_trade_usd"),
                "largest_winner_share_of_positive_pnl_pct": trade_quality.get("largest_winner_share_of_positive_pnl_pct"),
            },
            "Phase 2 should not pass if most positive evidence comes from one lucky outlier.",
        ),
        check(
            "2-4 week continuous run",
            status_for((trade_quality.get("evidence_span_days") or 0.0) >= 14.0),
            rel(LEDGER_PATH),
            {
                "evidence_span_days": trade_quality.get("evidence_span_days"),
                "minimum_days": 14.0,
            },
            "Continue hourly paper collection until evidence spans at least two weeks of live-market conditions.",
        ),
    ]

    flow_verdict = (flow.get("verdict") or {}) if isinstance(flow, dict) else {}
    duplicate_open_symbols = duplicate_open_symbol_summary(ledger)
    monthly_target = validation.get("monthly_target") if isinstance(validation.get("monthly_target"), dict) else {}
    current_equity = as_float(monthly_target.get("current_equity_usd"), as_float(ledger.get("equity_usd"), 0.0)) or 0.0
    target_equity = as_float(monthly_target.get("target_equity_usd"), 0.0) or 0.0
    month_start_equity = as_float(monthly_target.get("month_start_equity_usd"), as_float(ledger.get("initial_capital_usd"), 500.0)) or 0.0
    target_gap = as_float(monthly_target.get("target_gap_usd"), max(0.0, target_equity - current_equity)) or 0.0
    progress_pct = as_float(monthly_target.get("progress_pct"), 0.0) or 0.0
    capital_decision = capital_payload.get("allocation_decision") if isinstance(capital_payload.get("allocation_decision"), dict) else {}
    capital_authorization = capital_decision.get("current_deployment_authorization")
    capital_gate_safe = bool(capital_decision.get("policy")) and capital_authorization in {"authorized", "blocked"} and (
        capital_authorization != "blocked" or as_float(capital_decision.get("max_deployable_now_usd"), 0.0) == 0.0
    )
    checks_phase3 = [
        check(
            "Monthly compounding double target tracked",
            status_for(
                monthly_target.get("target_model") == "monthly_compounding_double"
                and monthly_target.get("month_id")
                and month_start_equity > 0
                and target_equity >= month_start_equity * 2.0
            ),
            rel(validation_path),
            monthly_target,
            "Keep using the ledger monthly baseline; do not reset the target after losses or mid-month pauses.",
        ),
        check(
            "Tactical paper pool remains isolated",
            status_for(
                as_float(ledger.get("initial_capital_usd"), 0.0) <= 500.0
                and ledger.get("live_orders_enabled") is False
                and ledger.get("private_api_used") is False
            ),
            rel(LEDGER_PATH),
            {
                "initial_capital_usd": ledger.get("initial_capital_usd"),
                "cash_usd": ledger.get("cash_usd"),
                "equity_usd": ledger.get("equity_usd"),
                "live_orders_enabled": ledger.get("live_orders_enabled"),
                "private_api_used": ledger.get("private_api_used"),
            },
            "Keep the pressure test separate from long-term holdings and real funds.",
        ),
        check(
            "Current month target equity reached",
            status_for(current_equity >= target_equity and target_equity > 0),
            rel(validation_path),
            {
                "month_start_equity_usd": month_start_equity,
                "target_equity_usd": target_equity,
                "current_equity_usd": current_equity,
                "target_gap_usd": target_gap,
                "progress_pct": progress_pct,
            },
            "Current paper equity must reach the monthly double target before Phase 3 is proven.",
        ),
        check(
            "Monthly pressure sample quality positive",
            status_for(closed_count >= min_closed and win_rate >= 55.0 and net_closed > 0),
            rel(validation_path),
            {
                "closed_count": closed_count,
                "minimum": min_closed,
                "win_rate_pct": win_rate,
                "realized_net_return_on_closed_notional_pct": net_closed,
            },
            "The pressure target cannot pass on a lucky mark-to-market move; closed paper trades must show positive net quality.",
        ),
        check(
            "Pressure risk controls respected",
            status_for(
                max_dd >= -15.0
                and duplicate_open_symbols.get("unauthorized_duplicate_count") == 0
                and risk_control.get("status") == "pass"
                and risk_control.get("control_contract_status") == "proven_offline"
                and (capital_decision.get("policy") in {"minimum_quality_scout_only", "quality_gated_deployment", "phase2_quality_scout_only"} or bool(capital_decision))
            ),
            f"{rel(LEDGER_PATH)}, {rel(capital_allocation_path)}, {rel(risk_control_path)}",
            {
                "max_drawdown_pct": max_dd,
                "unauthorized_duplicate_count": duplicate_open_symbols.get("unauthorized_duplicate_count"),
                "capital_policy": capital_decision.get("policy"),
                "max_new_positions_now": capital_decision.get("max_new_positions_now"),
                "per_trade_notional_usd": capital_decision.get("per_trade_notional_usd"),
                "risk_control_status": risk_control.get("status"),
                "risk_decision": risk_control.get("risk_decision"),
                "daily_realized_pnl_pct": (risk_control.get("metrics") or {}).get("daily_realized_pnl_pct"),
                "consecutive_loss_streak": (risk_control.get("metrics") or {}).get("consecutive_loss_streak"),
            },
            "Keep per-trade loss bounded and avoid all-in/repeated-symbol exposure during pressure testing.",
        ),
        check(
            "No single winner dominates monthly proof",
            status_for(
                current_equity >= target_equity
                and trade_quality.get("largest_winner_share_of_positive_pnl_pct") is not None
                and trade_quality["largest_winner_share_of_positive_pnl_pct"] <= 40.0
            ),
            rel(LEDGER_PATH),
            {
                "largest_winner_share_of_positive_pnl_pct": trade_quality.get("largest_winner_share_of_positive_pnl_pct"),
                "max_winning_trade_usd": trade_quality.get("max_winning_trade_usd"),
                "positive_pnl_sum_usd": trade_quality.get("positive_pnl_sum_usd"),
            },
            "A monthly-double pressure pass must not rely on one outlier trade.",
        ),
    ]

    def phase_status(checks: list[dict[str, Any]]) -> str:
        statuses = [item["status"] for item in checks]
        if all(status == "proven" for status in statuses):
            return "proven"
        if any(status == "proven" for status in statuses) or any(status == "partial" for status in statuses):
            return "partial"
        return "missing"

    phase1_status = phase_status(checks_phase1)
    phase2_status = "proven" if all(item["status"] == "proven" for item in checks_phase2) else "not_proven"
    phase3_status = "proven" if all(item["status"] == "proven" for item in checks_phase3) else "not_proven"
    checks_phase4 = [
        check(
            "Phase 2 positive expectancy proven",
            status_for(phase2_status == "proven"),
            rel(validation_path),
            {"phase2_status": phase2_status},
            "Do not promote any strategy to live/API candidate until Phase 2 has sufficient closed samples, positive net return, win rate and regime diversity.",
        ),
        check(
            "Phase 3 monthly-double pressure test proven",
            status_for(phase3_status == "proven"),
            rel(validation_path),
            {"phase3_status": phase3_status},
            "Do not consider live/API promotion until the high-risk tactical pool proves a monthly pressure result under paper constraints.",
        ),
        check("Binance Spot testnet or dry-run verified", status_for(flow_verdict.get("binance_testnet_or_dry_run_verified") is True), rel(flow_path), flow_verdict),
        check(
            "No unauthorized duplicate open symbols",
            status_for(duplicate_open_symbols.get("unauthorized_duplicate_count") == 0),
            rel(LEDGER_PATH),
            duplicate_open_symbols,
            "Existing duplicate paper positions must close/expire or be explicitly invalidated before Phase 4 readiness.",
        ),
        check(
            "API permission minimization documented",
            status_for(
                flow.get("live_orders_enabled") is False
                and flow.get("private_api_used") is False
                and flow.get("allow_real_orders") is not True,
                partial=bool(flow_path),
            ),
            rel(flow_path),
            {
                "live_orders_enabled": flow.get("live_orders_enabled"),
                "private_api_used": flow.get("private_api_used"),
                "allow_real_orders": flow.get("allow_real_orders"),
                "withdrawal_allowed": False,
            },
            "Future real route must use least-privilege spot-only keys with withdrawal disabled; current audit remains paper/testnet only.",
        ),
        check(
            "Kill switch / max loss / duplicate prevention proven",
            status_for(
                risk_control.get("status") == "pass"
                and risk_control.get("control_contract_status") == "proven_offline"
                and risk_control.get("phase4_control_evidence_ready") is True
                and risk_control.get("live_orders_enabled") is False
                and risk_control.get("private_api_used") is False
            ),
            rel(risk_control_path),
            {
                "control_contract_status": risk_control.get("control_contract_status"),
                "risk_decision": risk_control.get("risk_decision"),
                "phase4_control_evidence_ready": risk_control.get("phase4_control_evidence_ready"),
                "triggers": risk_control.get("triggers") or [],
                "idempotency": risk_control.get("idempotency") or {},
            },
            "Keep these controls paper/testnet-only; Phase 2, Phase 3 and human approval still block live promotion.",
        ),
        check(
            "Manual approval for api_ready_candidate",
            "missing",
            rel(strategy_enforcement_path),
            {"required_status": "human_confirmed_api_ready_candidate", "current_status": "not_requested"},
            "Human approval is mandatory after Phase 2 and Phase 3 pass; no automatic promotion from paper evidence.",
        ),
        check(
            "Paper/testnet rollback and audit log procedure proven",
            status_for(
                ((risk_control.get("rollback_evidence") or {}).get("status") == "proven_offline")
                and ((risk_control.get("rollback_evidence") or {}).get("complete") is True)
                and risk_control.get("allow_real_orders") is False
            ),
            rel(risk_control_path),
            risk_control.get("rollback_evidence") or {},
            "Run the same contract against Binance Spot testnet after Phase 2 and Phase 3 pass; live promotion still requires human approval.",
        ),
    ]
    phase1_gaps = [item for item in checks_phase1 if item["status"] != "proven"]
    phase2_blockers = [item for item in checks_phase2 if item["status"] != "proven"]
    phase3_blockers = [item for item in checks_phase3 if item["status"] != "proven"]
    phase4_blockers = [item for item in checks_phase4 if item["status"] != "proven"]
    attribution_summary = attribution_payload.get("summary") if isinstance(attribution_payload.get("summary"), dict) else {}
    attribution_closed_count = (
        attribution_summary.get("closed_trade_count")
        or attribution_summary.get("closed_trades_in_window")
        or attribution_summary.get("closed_trades_total")
        or 0
    )
    capital_decision = capital_payload.get("allocation_decision") if isinstance(capital_payload.get("allocation_decision"), dict) else {}
    backlog_summary = strategy_backlog_payload.get("summary") if isinstance(strategy_backlog_payload.get("summary"), dict) else {}
    enforcement_summary = strategy_enforcement_payload.get("summary") if isinstance(strategy_enforcement_payload.get("summary"), dict) else {}
    paper_auto_learning = paper_auto_learning_summary(paper_strategy_overlay_payload, paper_auto_evolver_payload)
    paper_auto_learning_safe = (
        paper_auto_learning.get("status") == "ok"
        and paper_auto_learning.get("auto_learning_enabled") is True
        and paper_auto_learning.get("auto_apply_scope") == "paper_only"
        and paper_auto_learning.get("safe_flags") is True
        and not paper_auto_learning.get("duplicate_change_ids")
        and bool(paper_auto_learning.get("strategy_version"))
        and (paper_auto_learning.get("applied_or_ab_testing") or 0) > 0
        and (paper_auto_learning.get("rollback_check_count") or 0) > 0
    )
    layer_status = [
        {
            "layer": "binance_data_layer",
            "status": "proven" if not [item for item in checks_phase1[:3] if item["status"] != "proven"] else "partial",
            "evidence": [rel(binance_health_path), rel(kline_builder_path)],
            "next_step": "Keep public multi-host Binance health and Kline cache checks fresh before entry scans.",
        },
        {
            "layer": "signal_scan_layer",
            "status": "proven" if (
                runner.get("dynamic_scan_contract_valid")
                and runner.get("fresh") is True
                and fast.get("fresh") is True
                and signal_contract.get("status") == "pass"
            ) else "partial",
            "evidence": [rel(runner_path), rel(signal_contract_path)],
            "next_step": "Continue dynamic pool scans; blocked candidates must remain diagnosis, not orders.",
        },
        {
            "layer": "paper_execution_layer",
            "status": "proven" if lifecycle.get("buy_sell_lifecycle") and ledger_integrity.get("status") == "pass" else "partial",
            "evidence": [rel(LEDGER_PATH), rel(ledger_integrity_path)],
            "next_step": "Only open new paper positions when recovery, liquidity, trigger and capacity gates pass.",
        },
        {
            "layer": "review_attribution_layer",
            "status": "proven" if attribution_closed_count or attribution_payload.get("proposed_changes") else "partial",
            "evidence": [rel(attribution_path)],
            "next_step": "Keep assigning failure categories after every closed paper trade.",
        },
        {
            "layer": "strategy_iteration_layer",
            "status": "proven" if (
                strategy_enforcement_payload.get("status") == "pass"
                and (backlog_summary.get("active_proposed_changes") or 0) >= 0
                and paper_auto_learning_safe
            ) else "partial",
            "evidence": [rel(strategy_backlog_path), rel(strategy_enforcement_path), rel(paper_auto_evolver_path), rel(PAPER_STRATEGY_OVERLAY_PATH)],
            "next_step": "Paper-only auto changes are active; collect enough forward samples to retain, promote as paper candidates, or revert them.",
        },
        {
            "layer": "capital_allocation_layer",
            "status": "proven" if capital_gate_safe else "partial",
            "evidence": [rel(capital_allocation_path)],
            "next_step": "Keep conservative sizing while Phase 2 remains not proven.",
        },
        {
            "layer": "real_trading_candidate_layer",
            "status": "blocked",
            "evidence": [rel(flow_path)],
            "next_step": "Requires Phase 2 proven, Phase 3 proven, least-privilege API, kill switch and human approval.",
        },
    ]
    endpoint_counts = binance_health.get("endpoint_counts") if isinstance(binance_health.get("endpoint_counts"), dict) else {}
    required_kline_intervals = ["1m", "5m", "15m", "1h", "4h"]
    kline_ok = {
        interval: int(endpoint_counts.get(f"klines_{interval}:ok") or 0)
        for interval in required_kline_intervals
    }
    historical_kline_endpoint_coverage = all(count > 0 for count in kline_ok.values())
    cached_intervals = {str(item) for item in (kline_builder.get("actual_intervals") or [])}
    durable_interval_coverage = set(required_kline_intervals).issubset(cached_intervals)
    current_kline_replay_usable = (
        binance_health.get("fresh") is True
        and kline_builder.get("fresh") is True
        and kline_builder.get("replay_available") is True
        and (kline_builder.get("actual_file_count") or 0) > 0
        and durable_interval_coverage
    )
    ticker_ok = int(endpoint_counts.get("ticker_24hr:ok") or 0)
    book_ok = int(endpoint_counts.get("book_ticker:ok") or 0)
    depth_ok = int(endpoint_counts.get("depth:ok") or 0)
    trades_ok = int(endpoint_counts.get("agg_trades:ok") or 0)
    backlog_active_count = (
        backlog_summary.get("active_proposed_changes")
        if isinstance(backlog_summary.get("active_proposed_changes"), int)
        else len(strategy_backlog_payload.get("items") or strategy_backlog_payload.get("proposed_changes") or [])
    )
    preflight_safety = preflight_payload.get("safety") if isinstance(preflight_payload.get("safety"), dict) else {}
    preflight_readiness = preflight_payload.get("readiness") if isinstance(preflight_payload.get("readiness"), dict) else {}
    preflight_automation = preflight_payload.get("automation") if isinstance(preflight_payload.get("automation"), dict) else {}
    automation_contract = preflight_automation.get("contract") if isinstance(preflight_automation.get("contract"), dict) else {}
    operational_entry_proven = (
        automation_contract.get("status") in {"pass", "ok"}
        and preflight_automation.get("status") in {"ACTIVE", "PAUSED"}
        and preflight_safety.get("status") in {"pass", "ok"}
    )
    operational_entry_partial = bool(preflight_payload or automation_recovery_payload)
    objective_coverage = [
        coverage_item(
            "Single operational trigger / scheduler contract",
            status_for(operational_entry_proven, partial=operational_entry_partial),
            [rel(preflight_path), rel(AUTOMATION_RECOVERY_PLAN_PATH)],
            {
                "automation_status": preflight_automation.get("status"),
                "automation_contract_status": automation_contract.get("status"),
                "preflight_safety_status": preflight_safety.get("status"),
                "preflight_readiness_status": preflight_readiness.get("status"),
                "recovery_plan_status": automation_recovery_payload.get("status"),
                "requires_explicit_user_approval": automation_recovery_payload.get("requires_explicit_user_approval"),
                "automation_mutated": automation_recovery_payload.get("automation_mutated"),
            },
            "A missing scheduler keeps automatic hourly sampling disabled. Restore exactly one PAUSED paper-only entry only after explicit user approval, then rerun preflight.",
        ),
        coverage_item(
            "Binance spot ticker",
            status_for(
                ticker_ok > 0 and binance_health.get("fresh") is True,
                partial=ticker_ok > 0,
            ),
            [rel(binance_health_path)],
            {
                "historical_ticker_24hr_ok_count": ticker_ok,
                "symbols": binance_health.get("passed_symbol_count"),
                "source_age_hours": binance_health.get("age_hours"),
                "source_fresh": binance_health.get("fresh"),
            },
            "Keep ticker health fresh before scans; if ticker fails, degrade to report-only.",
        ),
        coverage_item(
            "1m/5m/15m/1h/4h Kline coverage",
            status_for(
                current_kline_replay_usable,
                partial=(
                    any(count > 0 for count in kline_ok.values())
                    or (kline_builder.get("declared_file_count") or 0) > 0
                ),
            ),
            [rel(binance_health_path), rel(kline_builder_path)],
            {
                "historical_endpoint_ok_by_interval": kline_ok,
                "historical_endpoint_coverage_complete": historical_kline_endpoint_coverage,
                "durable_interval_coverage_complete": durable_interval_coverage,
                "cached_intervals": sorted(cached_intervals),
                "binance_health_fresh": binance_health.get("fresh"),
                "builder_declared_file_count": kline_builder.get("declared_file_count"),
                "builder_actual_file_count": kline_builder.get("actual_file_count"),
                "builder_storage_status": kline_builder.get("storage_status"),
                "builder_fresh": kline_builder.get("fresh"),
                "replay_available": kline_builder.get("replay_available"),
                "current_kline_replay_usable": current_kline_replay_usable,
            },
            (
                "Keep the verified manifest and five-interval cache fresh before each current-signal scan."
                if current_kline_replay_usable
                else "Historical endpoint coverage exists, but durable physical Klines must be rebuilt and refreshed before short-cycle research or entry signals are trusted."
            ),
        ),
        coverage_item(
            "Order book depth and bid/ask spread",
            status_for(
                book_ok > 0 and depth_ok > 0 and binance_health.get("fresh") is True,
                partial=book_ok > 0 or depth_ok > 0,
            ),
            [rel(binance_health_path)],
            {
                "historical_book_ticker_ok_count": book_ok,
                "historical_depth_ok_count": depth_ok,
                "source_age_hours": binance_health.get("age_hours"),
                "source_fresh": binance_health.get("fresh"),
            },
            "Depth/spread gates must block paper entries when liquidity is thin or spread is too wide.",
        ),
        coverage_item(
            "Recent trades / taker-flow evidence",
            status_for(
                trades_ok > 0 and binance_health.get("fresh") is True,
                partial=trades_ok > 0,
            ),
            [rel(binance_health_path)],
            {
                "historical_agg_trades_ok_count": trades_ok,
                "source_age_hours": binance_health.get("age_hours"),
                "source_fresh": binance_health.get("fresh"),
            },
            "Recent trade imbalance is required for impulse and scout confirmation.",
        ),
        coverage_item(
            "Dynamic scan pool from market mood and sentiment",
            status_for(
                runner.get("dynamic_scan_pool_enabled")
                and runner.get("dynamic_scan_contract_valid")
                and runner.get("fresh") is True
                and (runner.get("effective_symbols") or 0) > 0,
                partial=runner.get("dynamic_scan_pool_enabled"),
            ),
            [rel(runner_path)],
            {
                "dynamic_scan_status": runner.get("dynamic_scan_status"),
                "runner_age_hours": runner.get("age_hours"),
                "runner_fresh": runner.get("fresh"),
                "effective_symbols": runner.get("effective_symbols"),
                "market_regime": runner.get("market_regime"),
                "market_atmosphere": runner.get("market_atmosphere"),
                "short_term_state": runner.get("dynamic_scan_short_term_state"),
                "sentiment_state": runner.get("dynamic_scan_sentiment_state"),
                "missing_fields": runner.get("dynamic_scan_missing_fields"),
            },
            "Scan pool should expand/contract from current market atmosphere instead of staying static.",
        ),
        coverage_item(
            "Paper signal schema completeness",
            status_for(
                signal_contract.get("status") == "pass"
                and (signal_contract.get("signal_count") or 0) > 0
                and (signal_contract.get("incomplete_count") or 0) == 0
                and (signal_contract.get("unsafe_count") or 0) == 0,
                partial=(signal_contract.get("signal_count") or 0) > 0,
            ),
            [rel(signal_contract_path)],
            {
                "signal_count": signal_contract.get("signal_count"),
                "complete_count": signal_contract.get("complete_count"),
                "incomplete_count": signal_contract.get("incomplete_count"),
                "unsafe_count": signal_contract.get("unsafe_count"),
            },
            "This proves field completeness only. A signal still needs fresh market data, a current trigger, liquidity and risk gates before it can become a paper entry.",
        ),
        coverage_item(
            "Paper simulated order lifecycle",
            status_for(lifecycle.get("buy_sell_lifecycle") and ledger_integrity.get("status") == "pass"),
            [rel(LEDGER_PATH), rel(ledger_integrity_path)],
            {
                "paper_orders": lifecycle.get("order_count"),
                "safe_filled_orders": lifecycle.get("safe_filled_count"),
                "sides": lifecycle.get("sides"),
                "ledger_integrity_status": ledger_integrity.get("status"),
            },
            "Continue recording fill price, quantity, fees, slippage, spread and safety flags for every paper order.",
        ),
        coverage_item(
            "Automatic paper exits: stop / profit / expiry",
            status_for(
                exits.get("has_stop_loss") and exits.get("has_profit_exit") and exits.get("has_expiry"),
                partial=exits.get("has_stop_loss") or exits.get("has_profit_exit") or exits.get("has_expiry"),
            ),
            [rel(LEDGER_PATH)],
            {
                "has_stop_loss": exits.get("has_stop_loss"),
                "has_profit_exit": exits.get("has_profit_exit"),
                "has_expiry": exits.get("has_expiry"),
                "exit_reasons": exits.get("reasons"),
            },
            "Need closed examples across stop loss, take/trailing profit and expiry to prove the full exit loop.",
        ),
        coverage_item(
            "Trade attribution and learning loop",
            status_for(
                (attribution_closed_count or 0) > 0
                and len(attribution_payload.get("proposed_changes") or []) > 0,
                partial=(attribution_closed_count or 0) > 0,
            ),
            [rel(attribution_path)],
            {
                "closed_trade_count": attribution_closed_count,
                "structured_proposed_changes": len(attribution_payload.get("proposed_changes") or []),
            },
            "Every closed trade should feed attribution and proposed changes without automatically modifying live strategy.",
        ),
        coverage_item(
            "Strategy iteration backlog and enforcement",
            status_for(
                strategy_enforcement_payload.get("status") == "pass"
                and backlog_active_count is not None,
                partial=bool(strategy_backlog_payload),
            ),
            [rel(strategy_backlog_path), rel(strategy_enforcement_path)],
            {
                "backlog_active_count": backlog_active_count,
                "enforcement_status": strategy_enforcement_payload.get("status"),
                "enforced_count": enforcement_summary.get("enforced_count"),
                "pending_test_count": enforcement_summary.get("pending_test_count"),
            },
            "Changes remain proposed/paper-only until forward evidence validates them.",
        ),
        coverage_item(
            "Paper-only auto strategy evolver and overlay",
            status_for(
                paper_auto_learning_safe,
                partial=paper_auto_learning.get("status") == "ok" or bool(paper_strategy_overlay_payload),
            ),
            [rel(paper_auto_evolver_path), rel(PAPER_STRATEGY_OVERLAY_PATH)],
            {
                "strategy_version": paper_auto_learning.get("strategy_version"),
                "auto_learning_enabled": paper_auto_learning.get("auto_learning_enabled"),
                "auto_apply_scope": paper_auto_learning.get("auto_apply_scope"),
                "safe_flags": paper_auto_learning.get("safe_flags"),
                "change_log_count": paper_auto_learning.get("change_log_count"),
                "unique_change_id_count": paper_auto_learning.get("unique_change_id_count"),
                "duplicate_change_ids": paper_auto_learning.get("duplicate_change_ids"),
                "applied_or_ab_testing": paper_auto_learning.get("applied_or_ab_testing"),
                "newly_applied_or_ab_testing": paper_auto_learning.get("newly_applied_or_ab_testing"),
                "rollback_check_count": paper_auto_learning.get("rollback_check_count"),
                "awaiting_forward_samples": paper_auto_learning.get("awaiting_forward_samples"),
                "ready_forward_samples": paper_auto_learning.get("ready_forward_samples"),
                "reverted": paper_auto_learning.get("reverted"),
                "entry_family_interval_rules": [
                    paper_auto_learning.get("entry_mode_rule_count"),
                    paper_auto_learning.get("strategy_family_rule_count"),
                    paper_auto_learning.get("interval_rule_count"),
                ],
            },
            "New paper trades must consume overlay rules and then produce forward samples for retain/revert decisions.",
        ),
        coverage_item(
            "Paper capital allocation gate",
            status_for(capital_gate_safe, partial=bool(capital_decision.get("policy"))),
            [rel(capital_allocation_path)],
            {
                "policy": capital_decision.get("policy"),
                "current_deployment_authorization": capital_authorization,
                "authorization_blockers": capital_decision.get("authorization_blockers") or [],
                "research_max_deployable_usd": capital_decision.get("research_max_deployable_usd"),
                "research_per_trade_notional_usd": capital_decision.get("research_per_trade_notional_usd"),
                "max_deployable_now_usd": capital_decision.get("max_deployable_now_usd"),
                "per_trade_notional_usd": capital_decision.get("per_trade_notional_usd"),
                "max_new_positions_now": capital_decision.get("max_new_positions_now"),
            },
            "Sizing should remain conservative until Phase 2 expectancy and Phase 3 pressure evidence improve.",
        ),
        coverage_item(
            "Paper/testnet kill switch, loss limits, idempotency and rollback",
            status_for(
                risk_control.get("status") == "pass"
                and risk_control.get("control_contract_status") == "proven_offline"
                and ((risk_control.get("rollback_evidence") or {}).get("complete") is True)
                and not (risk_control.get("idempotency") or {}).get("duplicate_paper_trade_ids")
                and not (risk_control.get("idempotency") or {}).get("duplicate_paper_order_ids")
            ),
            [rel(risk_control_path)],
            {
                "risk_decision": risk_control.get("risk_decision"),
                "daily_realized_pnl_pct": (risk_control.get("metrics") or {}).get("daily_realized_pnl_pct"),
                "max_drawdown_pct": (risk_control.get("metrics") or {}).get("max_drawdown_pct"),
                "consecutive_loss_streak": (risk_control.get("metrics") or {}).get("consecutive_loss_streak"),
                "rollback_status": (risk_control.get("rollback_evidence") or {}).get("status"),
                "max_allowed_action": risk_control.get("max_allowed_action"),
            },
            "This proves the paper/testnet control contract only. It cannot bypass Phase 2/3 or human approval for live trading.",
        ),
        coverage_item(
            "Phase promotion gates",
            "blocked_by_design" if phase2_status != "proven" or phase3_status != "proven" else "manual_review_required",
            [rel(validation_path), rel(flow_path), rel(strategy_enforcement_path)],
            {
                "phase2": phase2_status,
                "phase3": phase3_status,
                "phase4": "not_ready" if phase4_blockers else "ready_for_manual_review",
            },
            "Do not move to testnet/tiny live until Phase 2 and Phase 3 are proven and human approval is recorded.",
        ),
    ]
    phase_statuses = {
        "phase1_paper_execution_loop": phase1_status,
        "phase2_positive_expectancy_proof": phase2_status,
        "phase3_monthly_double_pressure_test": phase3_status,
        "phase4_real_auto_trading_candidate": "not_ready" if phase4_blockers else "ready_for_manual_review",
    }
    completion_audit = completion_audit_summary(
        phase_statuses,
        objective_coverage,
        {
            "phase1": checks_phase1,
            "phase2": checks_phase2,
            "phase3": checks_phase3,
            "phase4": checks_phase4,
        },
        capital_authorization,
    )
    record = {
        "run_id": f"{created.strftime('%Y%m%d-%H%M%S')}-phase-goal-readiness-audit",
        "created_at": created.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
        "sources": {
            "ledger": rel(LEDGER_PATH),
            "latest_fast_loop": rel(fast_path),
            "latest_validation_runner": rel(runner_path),
            "latest_kline_builder": rel(kline_builder_path),
            "kline_research_reproducibility": rel(kline_repro_path),
            "latest_binance_market_data_health": rel(binance_health_path),
            "latest_paper_signal_contract": rel(signal_contract_path),
            "latest_paper_ledger_integrity": rel(ledger_integrity_path),
            "latest_paper_market_context": rel(market_context_path),
            "latest_trade_attribution": rel(attribution_path),
            "latest_capital_allocation": rel(capital_allocation_path),
            "latest_paper_strategy_auto_evolver": rel(paper_auto_evolver_path),
            "paper_strategy_auto_overlay": rel(PAPER_STRATEGY_OVERLAY_PATH),
            "strategy_backlog": rel(strategy_backlog_path),
            "strategy_proposal_enforcement": rel(strategy_enforcement_path),
            "latest_flow_audit": rel(flow_path),
            "validation_evidence": rel(validation_path),
            "latest_resume_preflight": rel(preflight_path),
            "automation_recovery_plan": rel(AUTOMATION_RECOVERY_PLAN_PATH),
            "paper_testnet_risk_control": rel(risk_control_path),
        },
        "phase_status": phase_statuses,
        "completion_audit": completion_audit,
        "goal_complete": completion_audit["goal_complete"],
        "overall_status": completion_audit["overall_status"],
        "max_current_action": completion_audit["max_current_action"],
        "layer_status": layer_status,
        "objective_coverage": objective_coverage,
        "phase1_checks": checks_phase1,
        "phase2_checks": checks_phase2,
        "phase3_checks": checks_phase3,
        "phase4_checks": checks_phase4,
        "summary": {
            "paper_loop_operational": phase_status(checks_phase1) in {"proven", "partial"},
            "phase1_missing_or_partial": phase1_gaps,
            "phase2_blockers": phase2_blockers,
            "phase3_blockers": phase3_blockers,
            "phase4_blockers": phase4_blockers,
            "next_best_actions": next_best_actions(ledger, validation, phase1_gaps, phase2_blockers, phase3_blockers, phase4_blockers),
            "market_context_summary": market_context,
            "paper_auto_learning_summary": paper_auto_learning,
        },
    }
    return record


def render_markdown(record: dict[str, Any]) -> str:
    completion = record.get("completion_audit") if isinstance(record.get("completion_audit"), dict) else {}
    lines = [
        f"# Active Alpha Goal Completion Audit | {record['run_id']}",
        "",
        "Read-only audit. No live orders, private APIs, or ledger mutation.",
        "",
        "## Strict Completion Verdict",
        "",
        f"- goal_complete: `{completion.get('goal_complete')}`",
        f"- overall_status: `{completion.get('overall_status')}`",
        f"- max_current_action: `{completion.get('max_current_action')}`",
        f"- current_deployment_authorization: `{completion.get('current_deployment_authorization')}`",
        f"- strict_blocker_count: `{completion.get('strict_blocker_count')}`",
        f"- objective_status_counts: `{completion.get('objective_status_counts') or {}}`",
        f"- phase_requirement_status_counts: `{completion.get('phase_requirement_status_counts') or {}}`",
        "",
        "### Top Strict Blockers",
        "",
        "| Requirement | Status | Next |",
        "|---|---|---|",
    ]
    blocker_rows = completion.get("blocking_requirements") if isinstance(completion.get("blocking_requirements"), list) else []
    for item in blocker_rows[:12]:
        lines.append(
            f"| {item.get('requirement') or '-'} | `{item.get('status') or 'missing'}` | {item.get('next_step') or '-'} |"
        )
    if not blocker_rows:
        lines.append("| - | `proven` | - |")
    lines.extend([
        "",
        "## Phase Status",
        "",
        "| Phase | Status |",
        "|---|---|",
    ])
    for key, value in record["phase_status"].items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(["", "## Layer Evidence Matrix", "", "| Layer | Status | Evidence | Next |", "|---|---|---|---|"])
    for item in record.get("layer_status") or []:
        evidence = ", ".join(f"`{value}`" for value in item.get("evidence") or [] if value) or "-"
        lines.append(
            f"| `{item.get('layer')}` | `{item.get('status')}` | {evidence} | {item.get('next_step') or '-'} |"
        )
    lines.extend(["", "## Objective Coverage Matrix", "", "| Requirement | Status | Evidence | Next |", "|---|---|---|---|"])
    for item in record.get("objective_coverage") or []:
        evidence = ", ".join(f"`{value}`" for value in item.get("evidence") or [] if value) or "-"
        lines.append(
            f"| {item.get('requirement')} | `{item.get('status')}` | {evidence} | {item.get('next_step') or '-'} |"
        )
    for section_key, title in (
        ("phase1_checks", "Phase 1 Paper Loop"),
        ("phase2_checks", "Phase 2 Strategy Proof"),
        ("phase3_checks", "Phase 3 Monthly-Double Pressure Test"),
        ("phase4_checks", "Phase 4 Real Auto Trading Candidate"),
    ):
        lines.extend(["", f"## {title}", "", "| Check | Status | Evidence | Next |", "|---|---|---|---|"])
        for item in record[section_key]:
            lines.append(
                f"| {item['name']} | `{item['status']}` | `{item['evidence']}` | {item.get('next_step') or '-'} |"
            )
    lines.extend(["", "## Sources", ""])
    for key, value in record["sources"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    return "\n".join(lines)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit active-alpha readiness against the three-phase goal")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(run_self_test(), ensure_ascii=False, indent=2))
        return 0
    record = build_record()
    stamp = record["run_id"].removesuffix("-phase-goal-readiness-audit")
    report_path = REPORTS_DIR / f"{now_local().strftime('%Y-%m-%d')}-phase-goal-readiness-{stamp}.md"
    experiment_path = EXPERIMENTS_DIR / f"{stamp}-phase-goal-readiness-audit.json"
    record["outputs"] = {
        "report": rel(report_path),
        "experiment": rel(experiment_path),
        "latest_report": rel(LATEST_GOAL_AUDIT_REPORT),
        "latest_experiment": rel(LATEST_GOAL_AUDIT_JSON),
    }
    if not args.dry_run:
        write_json(experiment_path, record)
        write_json(LATEST_GOAL_AUDIT_JSON, record)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        markdown = render_markdown(record)
        report_path.write_text(markdown, encoding="utf-8")
        LATEST_GOAL_AUDIT_REPORT.write_text(markdown, encoding="utf-8")
    if args.format == "markdown":
        print(render_markdown(record))
    else:
        payload = {
            "run_id": record["run_id"],
            "live_orders_enabled": False,
            "private_api_used": False,
            "goal_complete": record["goal_complete"],
            "overall_status": record["overall_status"],
            "max_current_action": record["max_current_action"],
            "strict_blocker_count": record["completion_audit"]["strict_blocker_count"],
            "completion_audit": record["completion_audit"],
            "phase_status": record["phase_status"],
            "phase1_missing_or_partial": [
                {"name": item["name"], "status": item["status"], "next_step": item.get("next_step")}
                for item in record["summary"]["phase1_missing_or_partial"]
            ],
            "phase2_blocker_count": len(record["summary"]["phase2_blockers"]),
            "phase3_blocker_count": len(record["summary"]["phase3_blockers"]),
            "phase4_blocker_count": len(record["summary"]["phase4_blockers"]),
            "layer_status": record.get("layer_status") or [],
            "objective_coverage": record.get("objective_coverage") or [],
            "outputs": record["outputs"],
        } if args.compact_output else record
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
