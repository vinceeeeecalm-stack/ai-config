#!/usr/bin/env python3
"""Write a fixed latest-status dashboard for the active alpha automation.

This script is intentionally read-only with respect to trading state. It reads
local reports, experiments and the paper ledger, then writes one human-facing
Markdown dashboard so the operator does not have to browse hundreds of artifacts.
It never fetches market data, places orders, or calls private APIs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from kline_cache_storage import inspect_kline_cache_storage


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
LEDGER_PATH = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
AUTOMATION_PATH = Path("/Users/vincentpan/.codex/automations/active-alpha-hourly-crypto-paper-loop/automation.toml")
AUTOMATION_ROOT = Path("/Users/vincentpan/.codex/automations")
LATEST_DASHBOARD = ACTIVE_ROOT / "reports" / "LATEST_ACTIVE_ALPHA_STATUS.md"
ARTIFACT_INDEX_JSON = ACTIVE_ROOT / "experiments" / "automation-artifact-housekeeper-latest.json"
STRATEGY_BACKLOG_JSON = ACTIVE_ROOT / "experiments" / "strategy-iteration-backlog.json"
STRATEGY_PROPOSAL_DECISION_JSON = ACTIVE_ROOT / "experiments" / "strategy-proposal-decision-board.json"
STRATEGY_PROPOSAL_ENFORCEMENT_JSON = ACTIVE_ROOT / "experiments" / "strategy-proposal-enforcement-audit.json"
PAPER_STRATEGY_OVERLAY_JSON = ACTIVE_ROOT / "config" / "paper_strategy_auto_overlay.json"
PHASE2_QUALITY_JSON = ACTIVE_ROOT / "experiments" / "phase2-quality-recovery-action-board.json"
PHASE2_QUALITY_GATE_JSON = ACTIVE_ROOT / "experiments" / "phase2-quality-gate-enforcement-audit.json"
PHASE3_PRESSURE_JSON = ACTIVE_ROOT / "experiments" / "phase3-pressure-action-board.json"
KLINE_RESEARCH_REPRO_JSON = ACTIVE_ROOT / "experiments" / "kline-research-reproducibility-audit.json"
AUTOMATION_RECOVERY_PLAN_JSON = ACTIVE_ROOT / "experiments" / "automation-recovery-plan.json"
GOAL_COMPLETION_AUDIT_JSON = ACTIVE_ROOT / "experiments" / "active-alpha-goal-completion-audit.json"
PAPER_TESTNET_RISK_CONTROL_JSON = ACTIVE_ROOT / "experiments" / "paper-testnet-risk-control-audit.json"
CONFIG_PATH = ACTIVE_ROOT / "config" / "active_alpha_monitor_config.json"


def now_local() -> dt.datetime:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0)


def artifact_age_hours(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    current_utc = now_local().astimezone(dt.timezone.utc)
    age = (current_utc - parsed.astimezone(dt.timezone.utc)).total_seconds() / 3600.0
    return round(max(0.0, age), 4)


def freshness_status(value: Any, max_age_hours: float) -> str:
    age = artifact_age_hours(value)
    if age is None:
        return "missing"
    return "fresh" if age <= max_age_hours else "stale"


def effective_artifact_status(status: Any, created_at: Any, max_age_hours: float = 6.0) -> str:
    return str(status or "missing") if freshness_status(created_at, max_age_hours) == "fresh" else "stale_not_actionable"


def rel(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def latest_file(pattern: str) -> Path | None:
    paths = list(ACTIVE_ROOT.glob(pattern))
    if not paths:
        return None
    return max(paths, key=lambda item: item.stat().st_mtime)


def workspace_path(path_text: str | None) -> Path | None:
    if not path_text or path_text == "-":
        return None
    path = Path(path_text)
    if path.is_absolute():
        return path
    return WORKSPACE_ROOT / path


def parse_automation() -> dict[str, Any]:
    automation_files = list(AUTOMATION_ROOT.glob("*/automation.toml")) if AUTOMATION_ROOT.exists() else []
    if not AUTOMATION_PATH.exists():
        return {
            "status": "missing",
            "path": str(AUTOMATION_PATH),
            "local_automation_toml_count": len(automation_files),
        }
    text = AUTOMATION_PATH.read_text(encoding="utf-8")
    fields: dict[str, Any] = {
        "path": str(AUTOMATION_PATH),
        "local_automation_toml_count": len(automation_files),
    }
    for key in ("id", "kind", "name", "status", "rrule", "model", "reasoning_effort", "execution_environment"):
        marker = f'{key} = "'
        start = text.find(marker)
        if start == -1:
            continue
        start += len(marker)
        end = text.find('"', start)
        if end != -1:
            fields[key] = text[start:end]
    fields["prompt_length"] = len(fields.get("prompt") or "")
    return fields


def ledger_summary(ledger: dict[str, Any]) -> dict[str, Any]:
    open_positions = ledger.get("open_positions") if isinstance(ledger.get("open_positions"), list) else []
    closed_trades = ledger.get("closed_trades") if isinstance(ledger.get("closed_trades"), list) else []
    open_value = float(ledger.get("open_value_usd") or 0.0)
    cash = float(ledger.get("cash_usd") or 0.0)
    equity = float(ledger.get("equity_usd") or cash + open_value)
    wins = 0
    resolved = 0
    realized_pnl = 0.0
    for trade in closed_trades:
        if trade.get("status") != "closed":
            continue
        resolved += 1
        realized_pnl += float(trade.get("realized_pnl_usd") or 0.0)
        if trade.get("outcome") == "hit" or float(trade.get("realized_pnl_usd") or 0.0) > 0:
            wins += 1
    return {
        "updated_at": ledger.get("updated_at"),
        "initial_capital_usd": ledger.get("initial_capital_usd"),
        "cash_usd": cash,
        "open_value_usd": open_value,
        "equity_usd": equity,
        "net_return_pct": ledger.get("net_return_pct"),
        "max_drawdown_pct": ledger.get("max_drawdown_pct"),
        "open_count": len(open_positions),
        "closed_count": resolved,
        "win_rate_pct": round((wins / resolved) * 100, 2) if resolved else None,
        "realized_pnl_usd": round(realized_pnl, 6),
        "open_symbols": [item.get("symbol") for item in open_positions if item.get("symbol")],
        "live_orders_enabled": bool(ledger.get("live_orders_enabled", False)),
        "private_api_used": bool(ledger.get("private_api_used", False)),
    }


def runner_summary(payload: dict[str, Any]) -> dict[str, Any]:
    post = (
        payload.get("post_validation")
        or payload.get("post_audit")
        or payload.get("after_validation_sample_audit")
        or {}
    )
    if isinstance(post, dict) and "stdout_json" in post:
        post = post.get("stdout_json") or {}
    recovery = payload.get("validation_recovery_plan") or post.get("validation_recovery_plan") or {}
    proposed_changes = recovery.get("proposed_changes") if isinstance(recovery, dict) else []
    if not isinstance(proposed_changes, list):
        proposed_changes = []
    market = (
        payload.get("dynamic_scan_universe")
        or payload.get("dynamic_scan_pool")
        or payload.get("market_scan")
        or {}
    )
    no_entry = payload.get("no_entry_summary") or {}
    selected_symbols = market.get("selected_symbols") or []
    if not selected_symbols and payload.get("effective_symbols"):
        selected_symbols = [item.strip() for item in str(payload.get("effective_symbols")).split(",") if item.strip()]
    created_at = payload.get("created_at")
    return {
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": artifact_age_hours(created_at),
        "freshness_status": freshness_status(created_at, 6.0),
        "status": payload.get("status"),
        "report": (payload.get("outputs") or {}).get("report"),
        "auto_current_signal_refresh_enabled": payload.get("auto_current_signal_refresh_enabled"),
        "auto_current_signal_refresh_required": payload.get("auto_current_signal_refresh_required"),
        "auto_current_signal_kline_prefetch_enabled": payload.get("auto_current_signal_kline_prefetch_enabled"),
        "current_signal_refresh_state": payload.get("current_signal_refresh_state") or {},
        "dynamic_pool_status": market.get("status"),
        "market_regime": market.get("market_regime") or market.get("regime"),
        "market_atmosphere": market.get("market_atmosphere"),
        "short_term_state": market.get("short_term_state"),
        "sentiment_state": market.get("sentiment_state"),
        "selected_symbols": selected_symbols,
        "why_pool_changed": market.get("why_pool_changed_from_static_baseline"),
        "no_entry_status": no_entry.get("status"),
        "no_entry_reason_counts": no_entry.get("reason_counts") or {},
        "top_blocked_candidates": [
            {
                "symbol": item.get("symbol"),
                "stage": item.get("stage"),
                "interval": item.get("interval"),
                "strategy_family": item.get("strategy_family"),
                "entry_mode": item.get("entry_mode_estimate"),
                "selection_score": item.get("selection_score"),
                "oos_win_rate_pct": item.get("oos_win_rate_pct"),
                "oos_net_return_pct": item.get("oos_net_return_pct"),
                "primary_block_reason": item.get("primary_block_reason"),
                "validation_recovery_match": item.get("validation_recovery_match"),
            }
            for item in (no_entry.get("top_blocked_candidates") or [])[:6]
            if isinstance(item, dict)
        ],
        "proposed_change_count": len(proposed_changes),
        "top_proposed_changes": [
            {
                "id": item.get("proposed_change_id"),
                "type": item.get("change_type"),
                "title": item.get("title"),
                "status": item.get("status"),
            }
            for item in proposed_changes[:4]
            if isinstance(item, dict)
        ],
        "phase_status": ((post.get("phase_goal") or {}).get("phase_2") or {}).get("status"),
    }


def sample_growth_action_board(
    runner: dict[str, Any],
    phase: dict[str, Any],
    capital_allocation: dict[str, Any],
    market_context: dict[str, Any],
    blocked_retest_sampler: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reason_counts = runner.get("no_entry_reason_counts") or {}
    blocked_rows = runner.get("top_blocked_candidates") or []
    phase2_blockers = phase.get("phase2_top_blockers") or []
    closed_gap = None
    for item in phase2_blockers:
        if item.get("name") == "30-50 closed paper trades":
            detail = item.get("detail") or {}
            if isinstance(detail, dict):
                try:
                    closed_gap = max(0, int(detail.get("minimum") or 30) - int(detail.get("closed_count") or 0))
                except (TypeError, ValueError):
                    closed_gap = None
    policy = capital_allocation.get("policy") or "unknown"
    max_deploy = capital_allocation.get("max_deployable_now_usd")
    per_trade = capital_allocation.get("per_trade_notional_usd")
    authorized_candidate_notional = min(float(max_deploy or 0.0), float(per_trade or 0.0))
    status = "collect_quality_samples_when_gates_pass"
    if runner.get("no_entry_status") == "no_new_entry":
        status = "scan_active_but_no_paper_entry"
    if phase.get("phase2") == "proven":
        status = "phase2_proven_ready_for_next_gate_review"
    action_rows = []
    for item in blocked_rows[:5]:
        reason = str(item.get("primary_block_reason") or "")
        symbol = item.get("symbol") or "-"
        if reason.startswith("validation_recovery_plan_block"):
            next_step = "send_to_retest_or_wait_for_independent_quality_scout"
        elif reason == "not_entry_eligible":
            next_step = "watch_until_trigger_entry_stop_take_profit_contract_confirms"
        elif "liquidity" in reason or "spread" in reason:
            next_step = "watch_only_until_spread_depth_and_recent_trades_improve"
        elif "recent_loss" in reason:
            next_step = "cooldown_until_loss_penalty_expires_or_retest_passes"
        else:
            next_step = "keep_watch_and_collect_more_forward_evidence"
        action_rows.append(
            {
                "symbol": symbol,
                "block": reason or "-",
                "next_step": next_step,
                "max_paper_notional_usd": authorized_candidate_notional if next_step.startswith("watch") is False else 0,
            }
        )
    notes = [
        "Do not increase size until Phase 2 has enough closed trades, positive net return and improved win rate.",
        "New samples should come from independent quality scouts or retest-confirmed setups, not retired weak paths.",
    ]
    if market_context.get("status") == "warn":
        notes.append("Future paper entries must stamp market context; legacy missing context still blocks Phase 2 regime proof.")
    if reason_counts:
        notes.append("Current no-entry reasons: " + ", ".join(f"{key}={value}" for key, value in reason_counts.items()))
    trigger_watch = []
    for item in ((blocked_retest_sampler or {}).get("top_decisions") or [])[:5]:
        reasons = item.get("reasons") or []
        distance = None
        for reason in reasons:
            text = str(reason)
            if text.startswith("current_trigger_not_confirmed_distance_"):
                distance = text.removeprefix("current_trigger_not_confirmed_distance_")
                break
        trigger_watch.append(
            {
                "symbol": item.get("symbol"),
                "decision": item.get("decision"),
                "last_price": item.get("last_price"),
                "trigger_price": item.get("breakout_level"),
                "distance_to_trigger": distance,
                "spread_bps": item.get("spread_bps"),
                "depth_usd": item.get("depth"),
                "oos_win_rate_pct": item.get("oos_win_rate_pct"),
                "oos_net_return_pct": item.get("oos_net_return_pct"),
                "reasons": reasons[:3],
            }
        )
    return {
        "status": status,
        "closed_trade_gap_to_phase2_min": closed_gap,
        "capital_policy": policy,
        "max_deployable_now_usd": max_deploy,
        "per_trade_notional_usd": per_trade,
        "sample_action": "max_one_min_quality_scout" if max_deploy else "watch_only",
        "reason_counts": reason_counts,
        "candidate_actions": action_rows,
        "quality_scout_trigger_watch": trigger_watch,
        "notes": notes,
    }


def latest_phase_summary(payload: dict[str, Any]) -> dict[str, Any]:
    phase_status = payload.get("phase_status") if isinstance(payload.get("phase_status"), dict) else {}
    phase1_checks = payload.get("phase1_checks") if isinstance(payload.get("phase1_checks"), list) else []
    phase2_checks = payload.get("phase2_checks") if isinstance(payload.get("phase2_checks"), list) else []
    phase3_checks = payload.get("phase3_checks") if isinstance(payload.get("phase3_checks"), list) else []
    phase4_checks = payload.get("phase4_checks") if isinstance(payload.get("phase4_checks"), list) else []
    layer_rows = payload.get("layer_status") if isinstance(payload.get("layer_status"), list) else []
    objective_rows = payload.get("objective_coverage") if isinstance(payload.get("objective_coverage"), list) else []
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    completion = payload.get("completion_audit") if isinstance(payload.get("completion_audit"), dict) else {}
    phase1_gaps = summary.get("phase1_missing_or_partial")
    if not isinstance(phase1_gaps, list):
        phase1_gaps = [item for item in phase1_checks if item.get("status") not in {"proven", "pass", "ok"}]
    phase2_blockers = [item for item in phase2_checks if item.get("status") not in {"proven", "pass", "ok"}]
    phase3_blockers = [item for item in phase3_checks if item.get("status") not in {"proven", "pass", "ok"}]
    phase4_blockers = [item for item in phase4_checks if item.get("status") not in {"proven", "pass", "ok"}]
    return {
        "run_id": payload.get("run_id"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "goal_complete": payload.get("goal_complete", completion.get("goal_complete")),
        "overall_status": payload.get("overall_status") or completion.get("overall_status"),
        "max_current_action": payload.get("max_current_action") or completion.get("max_current_action"),
        "current_deployment_authorization": completion.get("current_deployment_authorization"),
        "strict_blocker_count": completion.get("strict_blocker_count"),
        "objective_status_counts": completion.get("objective_status_counts") or {},
        "phase_requirement_status_counts": completion.get("phase_requirement_status_counts") or {},
        "strict_top_blockers": [
            {
                "requirement": item.get("requirement"),
                "status": item.get("status"),
                "next_step": item.get("next_step"),
            }
            for item in (completion.get("blocking_requirements") or [])[:8]
            if isinstance(item, dict)
        ],
        "phase1": phase_status.get("phase1_paper_execution_loop"),
        "phase2": phase_status.get("phase2_positive_expectancy_proof"),
        "phase3": phase_status.get("phase3_monthly_double_pressure_test")
        or phase_status.get("phase3_testnet_or_tiny_live_readiness"),
        "phase4": phase_status.get("phase4_real_auto_trading_candidate"),
        "phase1_gap_count": len(phase1_gaps),
        "phase2_blocker_count": payload.get("phase2_blocker_count", len(phase2_blockers)),
        "phase3_blocker_count": payload.get("phase3_blocker_count", len(phase3_blockers)),
        "phase4_blocker_count": payload.get("phase4_blocker_count", len(phase4_blockers)),
        "layer_status": [
            {
                "layer": item.get("layer"),
                "status": item.get("status"),
                "evidence": item.get("evidence") or [],
                "next_step": item.get("next_step"),
            }
            for item in layer_rows
            if isinstance(item, dict)
        ],
        "objective_coverage": [
            {
                "requirement": item.get("requirement"),
                "status": item.get("status"),
                "detail": item.get("detail") or {},
                "next_step": item.get("next_step"),
            }
            for item in objective_rows
            if isinstance(item, dict)
        ],
        "objective_gap_count": len(
            [
                item
                for item in objective_rows
                if isinstance(item, dict)
                and item.get("status") not in {"proven", "pass", "ok", "blocked_by_design"}
            ]
        ),
        "objective_top_gaps": [
            {
                "requirement": item.get("requirement"),
                "status": item.get("status"),
                "detail": item.get("detail") or {},
                "next_step": item.get("next_step"),
            }
            for item in objective_rows
            if isinstance(item, dict)
            and item.get("status") not in {"proven", "pass", "ok", "blocked_by_design"}
        ][:6],
        "phase1_top_gaps": [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "detail": item.get("detail"),
                "next_step": item.get("next_step"),
            }
            for item in phase1_gaps[:4]
            if isinstance(item, dict)
        ],
        "phase2_top_blockers": [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "detail": item.get("detail"),
                "next_step": item.get("next_step"),
            }
            for item in phase2_blockers[:8]
            if isinstance(item, dict)
        ],
        "phase3_top_blockers": [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "next_step": item.get("next_step"),
            }
            for item in phase3_blockers[:3]
            if isinstance(item, dict)
        ],
        "phase4_top_blockers": [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "next_step": item.get("next_step"),
            }
            for item in phase4_blockers[:5]
            if isinstance(item, dict)
        ],
        "next_best_actions": summary.get("next_best_actions") or [],
        "report": (payload.get("outputs") or {}).get("latest_report")
        or (payload.get("outputs") or {}).get("report"),
    }


def phase3_pressure_summary(payload: dict[str, Any]) -> dict[str, Any]:
    monthly = payload.get("monthly_target") if isinstance(payload.get("monthly_target"), dict) else {}
    quality = payload.get("trade_quality") if isinstance(payload.get("trade_quality"), dict) else {}
    posture = payload.get("pressure_posture") if isinstance(payload.get("pressure_posture"), dict) else {}
    rows = payload.get("action_rows") if isinstance(payload.get("action_rows"), list) else []
    return {
        "created_at": payload.get("created_at"),
        "run_id": payload.get("run_id"),
        "phase3_status": (payload.get("phase_status") or {}).get("phase3") if isinstance(payload.get("phase_status"), dict) else None,
        "month_id": monthly.get("month_id"),
        "target_equity_usd": monthly.get("target_equity_usd"),
        "current_equity_usd": monthly.get("current_equity_usd"),
        "target_gap_usd": monthly.get("target_gap_usd"),
        "required_return_pct_from_current_equity": monthly.get("required_return_pct_from_current_equity"),
        "closed_count": quality.get("closed_count"),
        "closed_gap_to_phase2_min": quality.get("closed_gap_to_phase2_min"),
        "win_rate_pct": quality.get("win_rate_pct"),
        "realized_pnl_usd": quality.get("realized_pnl_usd"),
        "closed_net_return_pct": quality.get("realized_net_return_on_closed_notional_pct"),
        "posture": posture.get("posture"),
        "allowed_action": posture.get("allowed_action"),
        "blockers": posture.get("blockers") or [],
        "capital_policy": posture.get("capital_policy"),
        "max_deployable_now_usd": posture.get("max_deployable_now_usd"),
        "per_trade_notional_usd": posture.get("per_trade_notional_usd"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "report": (payload.get("outputs") or {}).get("report"),
        "top_actions": [
            {
                "priority": item.get("priority"),
                "action": item.get("action"),
                "limit": item.get("paper_only_limit"),
                "success_condition": item.get("success_condition"),
            }
            for item in rows[:4]
            if isinstance(item, dict)
        ],
    }


def phase2_quality_summary(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("phase2_metrics") if isinstance(payload.get("phase2_metrics"), dict) else {}
    gate = payload.get("quality_gate") if isinstance(payload.get("quality_gate"), dict) else {}
    rows = payload.get("action_rows") if isinstance(payload.get("action_rows"), list) else []
    win55 = metrics.get("win_path_55pct") if isinstance(metrics.get("win_path_55pct"), dict) else {}
    win60 = metrics.get("win_path_60pct") if isinstance(metrics.get("win_path_60pct"), dict) else {}
    return {
        "created_at": payload.get("created_at"),
        "run_id": payload.get("run_id"),
        "closed_count": metrics.get("closed_count"),
        "closed_gap_to_min_30": metrics.get("closed_gap_to_min_30"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "realized_pnl_usd": metrics.get("realized_pnl_usd"),
        "closed_net_return_pct": metrics.get("realized_net_return_on_closed_notional_pct"),
        "next_8_all_win_rate_pct": metrics.get("next_8_all_win_rate_pct"),
        "next_8_required_avg_pnl_to_breakeven_usd": metrics.get("next_8_required_avg_pnl_to_breakeven_usd"),
        "win_path_55pct": win55,
        "win_path_60pct": win60,
        "phase2_status": gate.get("phase2_status"),
        "quality_state": gate.get("current_quality_state"),
        "allowed_new_sample_posture": gate.get("allowed_new_sample_posture"),
        "blocked_entry_mode_count": len(payload.get("blocked_entry_modes") or []),
        "blocked_strategy_family_count": len(payload.get("blocked_strategy_families") or []),
        "blocked_entry_modes": [
            {
                "name": item.get("name"),
                "trades": item.get("trade_count"),
                "win_rate_pct": item.get("win_rate_pct"),
                "net_pnl_usd": item.get("net_pnl_usd"),
                "action": item.get("action"),
            }
            for item in (payload.get("blocked_entry_modes") or [])[:5]
            if isinstance(item, dict)
        ],
        "blocked_strategy_families": [
            {
                "name": item.get("name"),
                "trades": item.get("trade_count"),
                "win_rate_pct": item.get("win_rate_pct"),
                "net_pnl_usd": item.get("net_pnl_usd"),
                "action": item.get("action"),
            }
            for item in (payload.get("blocked_strategy_families") or [])[:5]
            if isinstance(item, dict)
        ],
        "top_actions": [
            {
                "priority": item.get("priority"),
                "action": item.get("action"),
                "limit": item.get("paper_only_limit"),
                "success_condition": item.get("success_condition"),
            }
            for item in rows[:5]
            if isinstance(item, dict)
        ],
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "report": (payload.get("outputs") or {}).get("report"),
    }


def latest_impulse_summary(payload: dict[str, Any]) -> dict[str, Any]:
    signals = payload.get("signals") if isinstance(payload.get("signals"), list) else []
    captured_at = payload.get("captured_at")
    top = []
    for item in signals[:6]:
        top.append(
            {
                "symbol": item.get("symbol"),
                "stage": item.get("stage"),
                "action": item.get("recommended_max_action"),
                "score": item.get("impulse_score_points"),
                "volume_5m": item.get("volume_multiple_5m_vs_median"),
                "buy_ratio_5m": item.get("taker_buy_ratio_5m"),
                "spread_bps": item.get("spread_bps"),
            }
        )
    return {
        "captured_at": captured_at,
        "age_hours": artifact_age_hours(captured_at),
        "freshness_status": freshness_status(captured_at, 6.0),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "anchor_state": (payload.get("anchor") or {}).get("state") if isinstance(payload.get("anchor"), dict) else None,
        "signal_count": len(signals),
        "top_signals": top,
        "report": payload.get("report_path"),
        "experiment": payload.get("experiment_path"),
    }


def latest_compounding_summary(payload: dict[str, Any]) -> dict[str, Any]:
    verdict = payload.get("verdict") if isinstance(payload.get("verdict"), dict) else {}
    return {
        "run_id": payload.get("run_id"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "overall_status": verdict.get("overall_status"),
        "max_allowed_action": verdict.get("max_allowed_action"),
        "repeatable_compounding_path_proven": verdict.get("repeatable_compounding_path_proven"),
        "monthly_target_complete_now": verdict.get("monthly_target_complete_now"),
        "blockers": verdict.get("blockers") or [],
        "report": (payload.get("outputs") or {}).get("report"),
    }


def phase2_quality_gate_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    entry_rows = payload.get("entry_mode_rows") if isinstance(payload.get("entry_mode_rows"), list) else []
    family_rows = payload.get("strategy_family_rows") if isinstance(payload.get("strategy_family_rows"), list) else []
    return {
        "created_at": payload.get("created_at"),
        "run_id": payload.get("run_id"),
        "status": payload.get("status"),
        "checked_count": summary.get("checked_count"),
        "enforced_count": summary.get("enforced_count"),
        "current_blocking_count": summary.get("current_blocking_count"),
        "missing_from_recovery_plan_count": summary.get("missing_from_recovery_plan_count"),
        "entry_mode_rows": [
            {
                "name": item.get("name"),
                "status": item.get("enforcement_status"),
                "hits": len(item.get("current_block_hits") or []),
            }
            for item in entry_rows[:5]
            if isinstance(item, dict)
        ],
        "strategy_family_rows": [
            {
                "name": item.get("name"),
                "status": item.get("enforcement_status"),
                "hits": len(item.get("current_block_hits") or []),
            }
            for item in family_rows[:5]
            if isinstance(item, dict)
        ],
        "report": (payload.get("outputs") or {}).get("report"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "ledger_mutated": payload.get("ledger_mutated"),
    }


def current_signal_artifact_summary(path: Path | None, payload: dict[str, Any]) -> dict[str, Any]:
    top = payload.get("top_candidates") if isinstance(payload.get("top_candidates"), list) else []
    generated_at = payload.get("generated_at")
    deduplicated_top: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    for item in top:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        deduplicated_top.append(item)
        if len(deduplicated_top) >= 5:
            break
    return {
        "path": rel(path),
        "generated_at": generated_at,
        "age_hours": artifact_age_hours(generated_at),
        "freshness_status": freshness_status(generated_at, 6.0),
        "frames_loaded": payload.get("frames_loaded"),
        "strategies_scanned": payload.get("strategies_scanned"),
        "current_signal_strategies": payload.get("current_signal_strategies"),
        "candidate_count": payload.get("candidate_count"),
        "deduplicated_candidate_count": payload.get("deduplicated_candidate_count"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_keys_used": payload.get("private_api_keys_used"),
        "top_symbols": [item.get("symbol") for item in deduplicated_top],
        "top_actions": [item.get("recommended_max_action") for item in deduplicated_top],
    }


def current_signal_retest_summary(path: Path | None, payload: dict[str, Any]) -> dict[str, Any]:
    opened = payload.get("opened_positions") if isinstance(payload.get("opened_positions"), list) else []
    skipped = payload.get("skipped_candidates") if isinstance(payload.get("skipped_candidates"), list) else []
    candidate = opened[0] if opened and isinstance(opened[0], dict) else skipped[0] if skipped and isinstance(skipped[0], dict) else {}
    details = candidate.get("strategy_candidate") if isinstance(candidate.get("strategy_candidate"), dict) else candidate
    strategy = details.get("strategy") if isinstance(details.get("strategy"), dict) else {}
    return {
        "path": rel(path),
        "report": (payload.get("outputs") or {}).get("report") if isinstance(payload.get("outputs"), dict) else None,
        "created_at": payload.get("created_at"),
        "status": payload.get("status") or "missing",
        "dry_run": payload.get("dry_run"),
        "dry_run_artifact_persisted": payload.get("dry_run_artifact_persisted"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "candidate_count": len(opened),
        "skipped_count": len(skipped),
        "block_reason": candidate.get("reason"),
        "robustness_gate": candidate.get("current_signal_robustness_gate"),
        "symbol": candidate.get("symbol") or details.get("symbol"),
        "interval": candidate.get("interval") or details.get("interval"),
        "sample_mode": candidate.get("sample_mode") or candidate.get("paper_entry_mode") or details.get("sample_mode"),
        "planned_notional_usd": candidate.get("planned_notional_usd") or details.get("planned_notional_usd") or payload.get("effective_notional_usd"),
        "stop_pct": details.get("planned_stop_pct") if details.get("planned_stop_pct") is not None else strategy.get("stop_pct"),
        "take_pct": details.get("planned_take_profit_pct") if details.get("planned_take_profit_pct") is not None else strategy.get("take_pct"),
        "max_holding": details.get("planned_max_holding"),
        "train_trade_count": details.get("train_trade_count"),
        "train_win_rate_pct": details.get("train_win_rate_pct"),
        "train_net_return_pct": details.get("train_net_return_pct"),
        "validation_trade_count": details.get("validation_trade_count"),
        "validation_win_rate_pct": details.get("validation_win_rate_pct"),
        "validation_net_return_pct": details.get("validation_net_return_pct"),
        "holdout_trade_count": details.get("oos_trade_count"),
        "holdout_win_rate_pct": details.get("oos_win_rate_pct"),
        "holdout_net_return_pct": details.get("oos_net_return_pct"),
        "probability_note": "small historical holdout sample; not a calibrated forecast probability",
    }


def artifact_inventory_summary(payload: dict[str, Any]) -> dict[str, Any]:
    automation = payload.get("automation") if isinstance(payload.get("automation"), dict) else {}
    directories = payload.get("directories") if isinstance(payload.get("directories"), dict) else {}
    reports = directories.get("reports") if isinstance(directories.get("reports"), dict) else {}
    experiments = directories.get("experiments") if isinstance(directories.get("experiments"), dict) else {}
    handoffs = directories.get("handoffs") if isinstance(directories.get("handoffs"), dict) else {}
    paper_trades = directories.get("paper_trades") if isinstance(directories.get("paper_trades"), dict) else {}
    return {
        "generated_at": payload.get("generated_at"),
        "automation_count": automation.get("count"),
        "active_automation_count": automation.get("active_count"),
        "consolidated": automation.get("consolidated"),
        "reports_count": reports.get("file_count"),
        "experiments_count": experiments.get("file_count"),
        "handoffs_count": handoffs.get("file_count"),
        "paper_trades_count": paper_trades.get("file_count"),
        "policy": (payload.get("operator_policy") or {}).get("default_cleanup_mode"),
    }


def strategy_backlog_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    items = payload.get("items") if isinstance(payload.get("items"), dict) else {}
    active = [
        item for item in items.values()
        if isinstance(item, dict) and item.get("currently_present")
    ]
    return {
        "generated_at": payload.get("generated_at"),
        "total_backlog_items": summary.get("total_backlog_items", len(items)),
        "active_proposed_changes": summary.get("active_proposed_changes", len(active)),
        "pending_human_review": summary.get("pending_human_review"),
        "top_items": [
            {
                "id": item.get("proposed_change_id"),
                "type": item.get("change_type"),
                "status": item.get("operator_status"),
                "seen_count": item.get("seen_count"),
                "source": item.get("source"),
                "title": item.get("title"),
            }
            for item in active[:4]
        ],
    }


def paper_auto_learning_summary(overlay: dict[str, Any], evolver: dict[str, Any]) -> dict[str, Any]:
    change_log = overlay.get("change_log") if isinstance(overlay.get("change_log"), list) else []
    status_counts: dict[str, int] = {}
    for item in change_log:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    summary = evolver.get("summary") if isinstance(evolver.get("summary"), dict) else {}
    rollback_checks = evolver.get("rollback_checks") if isinstance(evolver.get("rollback_checks"), list) else []
    return {
        "strategy_version": overlay.get("strategy_version") or summary.get("strategy_version"),
        "overlay_updated_at": overlay.get("updated_at"),
        "auto_learning_enabled": overlay.get("auto_learning_enabled"),
        "auto_apply_scope": overlay.get("auto_apply_scope"),
        "change_log_count": len(change_log),
        "change_status_counts": status_counts,
        "entry_mode_rule_count": len(overlay.get("entry_mode_rules") or {}),
        "strategy_family_rule_count": len(overlay.get("strategy_family_rules") or {}),
        "interval_rule_count": len(overlay.get("interval_rules") or {}),
        "latest_evolver_run_id": evolver.get("run_id"),
        "latest_evolver_created_at": evolver.get("created_at"),
        "considered_changes": summary.get("considered_changes"),
        "applied_or_ab_testing": summary.get("applied_or_ab_testing"),
        "newly_applied_or_ab_testing": summary.get("newly_applied_or_ab_testing"),
        "reverted": summary.get("reverted"),
        "awaiting_forward_samples": summary.get("awaiting_forward_samples"),
        "deduped_change_log_removed": summary.get("deduped_change_log_removed"),
        "strategy_version_changed": summary.get("strategy_version_changed"),
        "rollback_watch": [
            {
                "change_id": item.get("change_id"),
                "status_after": item.get("status_after"),
                "sample_count": ((item.get("forward_sample") or {}).get("sample_count")),
                "win_rate_pct": ((item.get("forward_sample") or {}).get("win_rate_pct")),
                "net_pnl_usd": ((item.get("forward_sample") or {}).get("net_pnl_usd")),
            }
            for item in rollback_checks[:4]
            if isinstance(item, dict)
        ],
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
    }


def strategy_proposal_decision_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = payload.get("top_decisions") if isinstance(payload.get("top_decisions"), list) else []
    return {
        "created_at": payload.get("created_at"),
        "status": payload.get("status") or "missing",
        "proposal_count": summary.get("proposal_count"),
        "top_priority_id": summary.get("top_priority_id"),
        "top_priority_score": summary.get("top_priority_score"),
        "paper_ab_test_candidate_count": summary.get("paper_ab_test_candidate_count"),
        "protective_gate_candidate_count": summary.get("protective_gate_candidate_count"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "config_mutated": payload.get("config_mutated"),
        "report": (payload.get("outputs") or {}).get("report"),
        "top_decisions": [
            {
                "id": row.get("proposed_change_id"),
                "type": row.get("change_type"),
                "decision": row.get("decision"),
                "priority_score": row.get("priority_score"),
                "loss_at_risk_usd": (row.get("impact") or {}).get("estimated_loss_at_risk_usd"),
                "minimum_new_closed_samples": row.get("minimum_new_closed_samples"),
                "title": row.get("title"),
            }
            for row in rows[:5]
            if isinstance(row, dict)
        ],
    }


def strategy_proposal_enforcement_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = payload.get("top_rows") if isinstance(payload.get("top_rows"), list) else []
    return {
        "created_at": payload.get("created_at"),
        "status": payload.get("status") or "missing",
        "decision_count": summary.get("decision_count"),
        "enforced_count": summary.get("enforced_count"),
        "pending_test_count": summary.get("pending_test_count"),
        "blocking_count": summary.get("blocking_count"),
        "recovery_plan_status": summary.get("recovery_plan_status"),
        "new_sample_policy": summary.get("new_sample_policy"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "config_mutated": payload.get("config_mutated"),
        "report": (payload.get("outputs") or {}).get("report"),
        "top_rows": [
            {
                "id": row.get("proposed_change_id"),
                "type": row.get("change_type"),
                "decision": row.get("decision"),
                "enforcement_status": row.get("enforcement_status"),
                "blocking": row.get("blocking"),
                "evidence": row.get("evidence") if isinstance(row.get("evidence"), dict) else {},
            }
            for row in rows[:6]
            if isinstance(row, dict)
        ],
    }


def recovery_watchlist_summary(payload: dict[str, Any]) -> dict[str, Any]:
    watchlist = payload.get("watchlist") if isinstance(payload.get("watchlist"), list) else []
    dynamic_context = payload.get("dynamic_market_context") if isinstance(payload.get("dynamic_market_context"), dict) else {}
    created_at = payload.get("created_at")
    artifact_dynamic_status = dynamic_context.get("dynamic_scan_status")
    freshness = freshness_status(created_at, 6.0)
    return {
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": artifact_age_hours(created_at),
        "freshness_status": freshness,
        "queue_count": payload.get("queue_count", len(watchlist)),
        "artifact_actionable_paper_scout_count": payload.get("actionable_paper_scout_count"),
        "actionable_paper_scout_count": payload.get("actionable_paper_scout_count") if freshness == "fresh" else 0,
        "source_strategy_recovery_optimizer": payload.get("source_strategy_recovery_optimizer"),
        "source_dynamic_market_context": payload.get("source_dynamic_market_context"),
        "artifact_dynamic_scan_status": artifact_dynamic_status,
        "dynamic_scan_status": effective_artifact_status(artifact_dynamic_status, created_at),
        "dynamic_scan_reason": dynamic_context.get("dynamic_scan_reason"),
        "ticker_meta_status": dynamic_context.get("ticker_meta_status"),
        "social_handoff_status": dynamic_context.get("social_handoff_status"),
        "data_layer_degraded": dynamic_context.get("data_layer_degraded"),
        "top_items": [
            {
                "symbol": item.get("symbol"),
                "action": item.get("recommended_max_action"),
                "block_reason": item.get("block_reason"),
                "dynamic_scan_status": item.get("dynamic_scan_status"),
                "data_layer_degraded": item.get("data_layer_degraded"),
                "price": item.get("current_price"),
                "spread_bps": item.get("spread_bps"),
                "depth": item.get("book_depth_min_usd_20"),
                "buy_ratio": item.get("recent_taker_buy_quote_ratio"),
                "imbalance": item.get("order_book_imbalance_20"),
                "volatility": item.get("realized_volatility_pct"),
                "trigger": (item.get("entry_zone") or {}).get("breakout_confirm_above"),
                "stop": item.get("stop_loss"),
                "probability": item.get("forecast_probability_pct"),
            }
            for item in watchlist[:5]
            if isinstance(item, dict)
        ],
    }


def recovery_sampler_summary(payload: dict[str, Any]) -> dict[str, Any]:
    decisions = payload.get("decisions") if isinstance(payload.get("decisions"), list) else []
    created_at = payload.get("created_at")
    return {
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": artifact_age_hours(created_at),
        "freshness_status": freshness_status(created_at, 6.0),
        "artifact_status": payload.get("status"),
        "status": effective_artifact_status(payload.get("status"), created_at),
        "opened_count": payload.get("opened_count"),
        "blocked_count": payload.get("blocked_count"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "source_watchlist": payload.get("source_watchlist"),
        "top_decisions": [
            {
                "symbol": item.get("symbol"),
                "decision": item.get("decision"),
                "reasons": item.get("reasons") or [],
                "price": item.get("current_price"),
                "trigger": item.get("trigger"),
                "spread_bps": item.get("spread_bps"),
                "probability": item.get("forecast_probability_pct"),
            }
            for item in decisions[:5]
            if isinstance(item, dict)
        ],
    }


def blocked_retest_summary(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    ordered_results = sorted(
        [item for item in results if isinstance(item, dict)],
        key=lambda item: (
            item.get("decision") != "candidate_for_min_quality_scout_after_current_signal",
            -float(((item.get("best_variant") or {}).get("oos") or {}).get("net_return_pct") or 0.0),
        ),
    )
    created_at = payload.get("created_at")
    return {
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": artifact_age_hours(created_at),
        "freshness_status": freshness_status(created_at, 6.0),
        "artifact_status": payload.get("status"),
        "status": effective_artifact_status(payload.get("status"), created_at),
        "source_runner": payload.get("source_runner"),
        "candidates_seen": payload.get("candidates_seen", len(results)),
        "quality_scout_review_count": payload.get("quality_scout_review_count"),
        "keep_blocked_count": payload.get("keep_blocked_count"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "top_results": [
            {
                "symbol": item.get("symbol"),
                "interval": item.get("interval"),
                "best_variant": (item.get("best_variant") or {}).get("variant"),
                "oos_trade_count": ((item.get("best_variant") or {}).get("oos") or {}).get("trade_count"),
                "oos_win_rate_pct": ((item.get("best_variant") or {}).get("oos") or {}).get("win_rate_pct"),
                "oos_net_return_pct": ((item.get("best_variant") or {}).get("oos") or {}).get("net_return_pct"),
                "distance_to_breakout_pct": (item.get("current_setup") or {}).get("distance_to_breakout_pct"),
                "decision": item.get("decision"),
                "recommended_next_action": item.get("recommended_next_action"),
            }
            for item in ordered_results[:6]
        ],
    }


def blocked_retest_sampler_summary(payload: dict[str, Any]) -> dict[str, Any]:
    decisions = payload.get("decisions") if isinstance(payload.get("decisions"), list) else []
    created_at = payload.get("created_at")
    return {
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": artifact_age_hours(created_at),
        "freshness_status": freshness_status(created_at, 6.0),
        "artifact_status": payload.get("status"),
        "status": effective_artifact_status(payload.get("status"), created_at),
        "source_retest": payload.get("source_retest"),
        "candidate_count": payload.get("candidate_count", len(decisions)),
        "opened_count": payload.get("opened_count"),
        "blocked_count": payload.get("blocked_count"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "top_decisions": [
            {
                "symbol": item.get("symbol"),
                "decision": item.get("decision"),
                "reasons": item.get("reasons") or [],
                "last_price": item.get("last_price"),
                "breakout_level": item.get("breakout_level"),
                "spread_bps": item.get("spread_bps"),
                "depth": item.get("depth"),
                "oos_win_rate_pct": (item.get("oos") or {}).get("win_rate_pct"),
                "oos_net_return_pct": (item.get("oos") or {}).get("net_return_pct"),
                "best_variant": item.get("best_variant"),
            }
            for item in decisions[:5]
            if isinstance(item, dict)
        ],
    }


def trade_attribution_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "summary": summary,
        "top_failure_categories": (payload.get("top_failure_categories") or [])[:6],
        "worst_entry_modes": (payload.get("worst_entry_modes") or [])[:5],
        "proposed_focus": payload.get("proposed_focus") or [],
        "proposed_change_count": len(payload.get("proposed_changes") or []),
        "top_proposed_changes": (payload.get("proposed_changes") or [])[:4],
        "ledger_mutated": payload.get("ledger_mutated"),
    }


def capital_allocation_summary(payload: dict[str, Any]) -> dict[str, Any]:
    decision = payload.get("allocation_decision") if isinstance(payload.get("allocation_decision"), dict) else {}
    portfolio = payload.get("portfolio") if isinstance(payload.get("portfolio"), dict) else {}
    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
    phase2 = payload.get("phase2_quality_state") if isinstance(payload.get("phase2_quality_state"), dict) else {}
    backlog = payload.get("strategy_backlog") if isinstance(payload.get("strategy_backlog"), dict) else {}
    return {
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "policy": decision.get("policy"),
        "current_deployment_authorization": decision.get("current_deployment_authorization"),
        "authorization_blockers": decision.get("authorization_blockers") or [],
        "research_max_new_positions": decision.get("research_max_new_positions"),
        "research_per_trade_notional_usd": decision.get("research_per_trade_notional_usd"),
        "research_max_deployable_usd": decision.get("research_max_deployable_usd"),
        "max_new_positions_now": decision.get("max_new_positions_now"),
        "per_trade_notional_usd": decision.get("per_trade_notional_usd"),
        "max_deployable_now_usd": decision.get("max_deployable_now_usd"),
        "reserve_cash_usd": decision.get("reserve_cash_usd"),
        "idle_or_waiting_cash_usd": decision.get("idle_or_waiting_cash_usd"),
        "target_cash_ratio_pct": decision.get("target_cash_ratio_pct"),
        "decision_reasons": decision.get("decision_reasons") or [],
        "current_instruction": decision.get("current_instruction"),
        "cash_ratio_pct": portfolio.get("cash_ratio_pct"),
        "phase2_status": phase2.get("status"),
        "closed_count": quality.get("closed_count"),
        "win_rate_pct": quality.get("win_rate_pct"),
        "realized_pnl_usd": quality.get("realized_pnl_usd"),
        "active_proposed_changes": backlog.get("active_proposed_changes"),
        "ledger_mutated": payload.get("ledger_mutated"),
    }


def paper_testnet_risk_control_summary(payload: dict[str, Any]) -> dict[str, Any]:
    risk = payload.get("risk_control") if isinstance(payload.get("risk_control"), dict) else {}
    metrics = risk.get("metrics") if isinstance(risk.get("metrics"), dict) else {}
    rollback = risk.get("rollback_evidence") if isinstance(risk.get("rollback_evidence"), dict) else {}
    return {
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "status": risk.get("status"),
        "control_contract_status": risk.get("control_contract_status"),
        "risk_decision": risk.get("risk_decision"),
        "size_multiplier": risk.get("recommended_new_entry_size_multiplier"),
        "max_new_entry_notional_usd": risk.get("max_new_entry_notional_usd"),
        "triggers": risk.get("triggers") or [],
        "contract_errors": risk.get("contract_errors") or [],
        "safety_errors": risk.get("safety_errors") or [],
        "integrity_errors": risk.get("integrity_errors") or [],
        "daily_realized_pnl_usd": metrics.get("daily_realized_pnl_usd"),
        "daily_realized_pnl_pct": metrics.get("daily_realized_pnl_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "consecutive_loss_streak": metrics.get("consecutive_loss_streak"),
        "open_exposure_pct": metrics.get("open_exposure_pct"),
        "rollback_status": rollback.get("status"),
        "rollback_check_count": rollback.get("rollback_check_count"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "report": (payload.get("outputs") or {}).get("latest_report")
        or (payload.get("outputs") or {}).get("report"),
    }


def ledger_integrity_summary(payload: dict[str, Any]) -> dict[str, Any]:
    audit = payload.get("audit") if isinstance(payload.get("audit"), dict) else {}
    repairs = payload.get("repairs_applied") if isinstance(payload.get("repairs_applied"), list) else []
    return {
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "status": audit.get("status"),
        "blocked_count": audit.get("blocked_count"),
        "warning_count": audit.get("warning_count"),
        "order_count": audit.get("order_count"),
        "open_position_count": audit.get("open_position_count"),
        "closed_trade_count": audit.get("closed_trade_count"),
        "repairs_applied": len(repairs),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "report": (payload.get("outputs") or {}).get("report"),
    }


def binance_market_data_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), list) else []
    endpoint_sources = {
        item.get("symbol"): item.get("endpoint_sources") or {}
        for item in symbols
        if isinstance(item, dict)
    }
    fallback_failure_count = sum(
        int((source or {}).get("fallback_failure_count") or 0)
        for symbol_sources in endpoint_sources.values()
        for source in symbol_sources.values()
        if isinstance(source, dict)
    )
    created_at = payload.get("created_at")
    return {
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": artifact_age_hours(created_at),
        "freshness_status": freshness_status(created_at, 6.0),
        "status": summary.get("status"),
        "passed_symbol_count": summary.get("passed_symbol_count"),
        "warning_symbol_count": summary.get("warning_symbol_count"),
        "blocked_symbol_count": summary.get("blocked_symbol_count"),
        "endpoint_counts": summary.get("endpoint_counts") or {},
        "base_urls": payload.get("base_urls") or ([payload.get("base_url")] if payload.get("base_url") else []),
        "primary_base_url": (payload.get("base_urls") or [payload.get("base_url") or "-"])[0],
        "endpoint_fallback_failure_count": fallback_failure_count,
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "allow_real_orders": payload.get("allow_real_orders"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "report": (payload.get("outputs") or {}).get("report"),
        "top_symbols": [
            {
                "symbol": item.get("symbol"),
                "status": item.get("status"),
                "price_change_24h_pct": (item.get("price") or {}).get("price_change_24h_pct"),
                "quote_volume_24h_usd": (item.get("price") or {}).get("quote_volume_24h_usd"),
                "spread_bps": (item.get("depth") or {}).get("spread_bps"),
                "depth_1pct_min_usd": min(
                    (item.get("depth") or {}).get("depth_1pct_bid_usd") or 0.0,
                    (item.get("depth") or {}).get("depth_1pct_ask_usd") or 0.0,
                ),
                "taker_buy_quote_ratio": (item.get("recent_trades") or {}).get("taker_buy_quote_ratio"),
                "missing_intervals": item.get("missing_intervals") or [],
            }
            for item in symbols[:6]
            if isinstance(item, dict)
        ],
    }


def binance_kline_cache_summary(payload: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    files_written = payload.get("files_written") if isinstance(payload.get("files_written"), list) else []
    discovered = (
        payload.get("top_discovery_ranked")
        or payload.get("top_discovered_symbols")
        or payload.get("top_symbols")
        or []
    )
    if not isinstance(discovered, list):
        discovered = []
    selected_symbols = payload.get("selected_symbols") if isinstance(payload.get("selected_symbols"), list) else []
    requested_intervals = payload.get("requested_intervals") if isinstance(payload.get("requested_intervals"), list) else []
    intervals_from_files = sorted(
        {
            item.get("interval")
            for item in files_written
            if isinstance(item, dict) and item.get("interval")
        }
    )
    completed_at = payload.get("completed_at") or payload.get("generated_at")
    storage = inspect_kline_cache_storage(payload)
    manifest = storage.get("manifest") if isinstance(storage.get("manifest"), dict) else {}
    artifact_status = payload.get("status") or "missing"
    effective_status = artifact_status if storage.get("replay_available") else "storage_missing_refresh_required"
    runner_config = config.get("validation_progress_runner") if isinstance(config, dict) else {}
    builder_contract = (
        runner_config.get("dynamic_kline_cache_builder")
        if isinstance(runner_config, dict) and isinstance(runner_config.get("dynamic_kline_cache_builder"), dict)
        else {}
    )
    return {
        "run_id": payload.get("run_id"),
        "generated_at": payload.get("generated_at"),
        "completed_at": payload.get("completed_at"),
        "age_hours": artifact_age_hours(completed_at),
        "freshness_status": freshness_status(completed_at, 6.0),
        "status": effective_status,
        "artifact_status": artifact_status,
        "cache_builder_version": payload.get("cache_builder_version"),
        "cache_dir": storage.get("cache_dir"),
        "cache_dir_exists": storage.get("cache_dir_exists"),
        "storage_status": storage.get("storage_status"),
        "declared_file_count": storage.get("declared_file_count"),
        "actual_file_count": storage.get("actual_file_count"),
        "missing_declared_file_count": storage.get("missing_declared_file_count"),
        "replay_available": storage.get("replay_available"),
        "refresh_required": storage.get("refresh_required"),
        "manifest_status": manifest.get("manifest_status"),
        "manifest_valid": manifest.get("manifest_valid"),
        "manifest_path": manifest.get("manifest_path"),
        "verified_file_count": manifest.get("verified_file_count"),
        "hash_mismatch_count": manifest.get("hash_mismatch_count"),
        "schema_invalid_count": manifest.get("schema_invalid_count"),
        "untracked_data_file_count": manifest.get("untracked_data_file_count"),
        "requested_top_symbols": payload.get("requested_top_symbols"),
        "selected_symbol_count": len(selected_symbols),
        "selected_symbols": selected_symbols,
        "requested_intervals": requested_intervals or intervals_from_files,
        "configured_default_intervals": str(builder_contract.get("default_intervals") or "").split(",")
        if builder_contract.get("default_intervals")
        else [],
        "configured_required_operational_intervals": builder_contract.get("required_operational_intervals") or [],
        "configured_optional_research_intervals": builder_contract.get("optional_research_intervals") or [],
        "configured_request_budget": builder_contract.get("default_max_kline_requests"),
        "configured_coverage_action": builder_contract.get("coverage_action"),
        "file_count": payload.get("file_count") if payload.get("file_count") is not None else len(files_written),
        "failure_count": payload.get("failure_count"),
        "fallback_event_count": payload.get("fallback_event_count"),
        "symbol_selection_note": payload.get("symbol_selection_note"),
        "binance_public_market_data_only": payload.get("binance_public_market_data_only"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_keys_logged": payload.get("private_api_keys_logged"),
        "experiment": (payload.get("outputs") or {}).get("experiment"),
        "top_discovered_symbols": [
            {
                "symbol": item.get("symbol"),
                "score": item.get("score"),
                "price_change_pct_24h": item.get("price_change_pct_24h"),
                "quote_volume_24h": item.get("quote_volume_24h"),
                "trade_count_24h": item.get("trade_count_24h"),
            }
            for item in discovered[:6]
            if isinstance(item, dict)
        ],
    }


def kline_research_reproducibility_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
    return {
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "status": payload.get("status") or "missing",
        "artifact_count": summary.get("artifact_count"),
        "reproducible_count": summary.get("reproducible_count"),
        "nonreproducible_count": summary.get("nonreproducible_count"),
        "promotion_allowed_count": summary.get("promotion_allowed_count"),
        "current_artifact": summary.get("current_artifact"),
        "current_artifact_reproducible": summary.get("current_artifact_reproducible"),
        "historical_nonreproducible_count": summary.get("historical_nonreproducible_count"),
        "max_allowed_action": summary.get("max_allowed_action"),
        "fresh_cache_rebuild_required": summary.get("fresh_cache_rebuild_required"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "report": (payload.get("outputs") or {}).get("report"),
        "top_artifacts": [
            {
                "artifact": item.get("artifact"),
                "declared_frames": item.get("declared_frames_loaded"),
                "actual_files": item.get("actual_cache_json_file_count"),
                "verified_files": item.get("verified_cache_file_count"),
                "manifest_ok": item.get("cache_manifest_integrity_ok"),
                "protocol_ok": item.get("protocol_ok"),
                "execution_model_ok": item.get("execution_model_ok"),
                "status": item.get("reproducibility_status"),
                "promotion_allowed": item.get("promotion_allowed"),
            }
            for item in artifacts[:5]
            if isinstance(item, dict)
        ],
    }


def paper_signal_contract_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    signals = payload.get("signals") if isinstance(payload.get("signals"), list) else []
    return {
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "status": payload.get("status"),
        "signal_count": summary.get("signal_count"),
        "complete_count": summary.get("complete_count"),
        "partial_count": summary.get("partial_count"),
        "incomplete_count": summary.get("incomplete_count"),
        "unsafe_count": summary.get("unsafe_count"),
        "missing_field_counts": summary.get("missing_field_counts") or {},
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "allow_real_orders": payload.get("allow_real_orders"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "report": (payload.get("outputs") or {}).get("report"),
        "top_signals": [
            {
                "symbol": item.get("symbol"),
                "type": item.get("signal_type"),
                "status": item.get("status"),
                "action": item.get("action"),
                "confidence": item.get("confidence"),
                "missing": item.get("missing_fields") or [],
            }
            for item in signals[:8]
            if isinstance(item, dict)
        ],
    }


def paper_market_context_summary(payload: dict[str, Any]) -> dict[str, Any]:
    all_summary = payload.get("all_summary") if isinstance(payload.get("all_summary"), dict) else {}
    return {
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "status": payload.get("status"),
        "trade_count": all_summary.get("trade_count"),
        "complete_count": all_summary.get("complete_count"),
        "partial_count": all_summary.get("partial_count"),
        "missing_count": all_summary.get("missing_count"),
        "coverage_pct": all_summary.get("coverage_pct"),
        "explicit_regime_count": all_summary.get("explicit_regime_count"),
        "regime_counts": all_summary.get("regime_counts") or [],
        "top_gaps": all_summary.get("top_gaps") or [],
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "allow_real_orders": payload.get("allow_real_orders"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "report": (payload.get("outputs") or {}).get("report"),
    }


def preflight_summary(payload: dict[str, Any]) -> dict[str, Any]:
    kline_cache = payload.get("kline_cache") if isinstance(payload.get("kline_cache"), dict) else {}
    return {
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "safety_status": (payload.get("safety") or {}).get("status"),
        "readiness_status": (payload.get("readiness") or {}).get("status"),
        "can_resume_hourly_new_sampling": (payload.get("readiness") or {}).get("can_resume_hourly_new_sampling"),
        "self_test_status": (payload.get("self_test") or {}).get("status"),
        "recovery_watchlist_monitor_self_test_status": (payload.get("recovery_watchlist_monitor_self_test") or {}).get("status"),
        "recovery_sampler_self_test_status": (payload.get("recovery_sampler_self_test") or {}).get("status"),
        "blocked_retest_sampler_self_test_status": (payload.get("blocked_retest_sampler_self_test") or {}).get("status"),
        "capital_allocation_self_test_status": (payload.get("capital_allocation_self_test") or {}).get("status"),
        "ledger_integrity_self_test_status": (payload.get("ledger_integrity_self_test") or {}).get("status"),
        "market_context_self_test_status": (payload.get("market_context_self_test") or {}).get("status"),
        "binance_market_data_health_self_test_status": (payload.get("binance_market_data_health_self_test") or {}).get("status"),
        "binance_kline_cache_builder_self_test_status": (payload.get("binance_kline_cache_builder_self_test") or {}).get("status"),
        "weekly_goal_strategy_lab_self_test_status": (payload.get("weekly_goal_strategy_lab_self_test") or {}).get("status"),
        "kline_research_reproducibility_self_test_status": (payload.get("kline_research_reproducibility_self_test") or {}).get("status"),
        "current_signal_probe_self_test_status": (payload.get("current_signal_probe_self_test") or {}).get("status"),
        "strategy_recovery_optimizer_self_test_status": (payload.get("strategy_recovery_optimizer_self_test") or {}).get("status"),
        "pipeline_freshness_self_test_status": (payload.get("pipeline_freshness_self_test") or {}).get("status"),
        "automation_recovery_planner_self_test_status": (payload.get("automation_recovery_planner_self_test") or {}).get("status"),
        "goal_completion_auditor_self_test_status": (payload.get("goal_completion_auditor_self_test") or {}).get("status"),
        "paper_testnet_risk_control_self_test_status": (payload.get("paper_testnet_risk_control_self_test") or {}).get("status"),
        "automation_recovery_plan_status": (((payload.get("automation_recovery_plan") or {}).get("stdout_json") or {}).get("status")),
        "automation_recovery_requires_approval": (((payload.get("automation_recovery_plan") or {}).get("stdout_json") or {}).get("requires_explicit_user_approval")),
        "kline_cache_status": kline_cache.get("status"),
        "kline_cache_storage_status": kline_cache.get("storage_status"),
        "kline_cache_refresh_required": kline_cache.get("refresh_required"),
        "safety_invariant_audit_status": (payload.get("safety_invariant_audit") or {}).get("status"),
        "validation_audit_status": (payload.get("validation_audit") or {}).get("status"),
    }


def automation_recovery_summary(payload: dict[str, Any]) -> dict[str, Any]:
    inventory = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {}
    contract = payload.get("proposed_contract") if isinstance(payload.get("proposed_contract"), dict) else {}
    return {
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "status": payload.get("status") or "missing",
        "reason": payload.get("reason"),
        "automation_count": inventory.get("count"),
        "expected_id_count": inventory.get("expected_id_count"),
        "safe_expected_id_count": inventory.get("safe_expected_id_count"),
        "requires_explicit_user_approval": payload.get("requires_explicit_user_approval"),
        "max_allowed_action": payload.get("max_allowed_action"),
        "automation_mutated": payload.get("automation_mutated"),
        "proposed_id": contract.get("id"),
        "proposed_initial_status": contract.get("initial_status"),
        "proposed_schedule": contract.get("schedule"),
        "prompt_sha256": contract.get("prompt_sha256"),
        "next_action": payload.get("next_action"),
    }


def pipeline_freshness_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    latest_runner = payload.get("latest_runner") if isinstance(payload.get("latest_runner"), dict) else {}
    latest_candidate_runner = (
        payload.get("latest_candidate_runner") if isinstance(payload.get("latest_candidate_runner"), dict) else {}
    )
    rows = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    return {
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "status": payload.get("status") or "missing",
        "artifact_sync_status": payload.get("artifact_sync_status"),
        "current_market_readiness_status": payload.get("current_market_readiness_status"),
        "stale_count": summary.get("stale_count"),
        "missing_count": summary.get("missing_count"),
        "synced_or_present_count": summary.get("synced_or_present_count"),
        "latest_runner_artifact": latest_runner.get("artifact"),
        "latest_runner_dynamic_scan_status": latest_runner.get("dynamic_scan_status"),
        "latest_runner_age_hours": latest_runner.get("age_hours"),
        "latest_runner_freshness_status": latest_runner.get("freshness_status"),
        "latest_candidate_runner_artifact": latest_candidate_runner.get("artifact"),
        "latest_candidate_runner_status": latest_candidate_runner.get("status"),
        "latest_candidate_runner_candidate_count": latest_candidate_runner.get("candidate_count"),
        "next_actions": payload.get("next_actions") or [],
        "rows": rows,
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "ledger_mutated": payload.get("ledger_mutated"),
    }


def pipeline_repair_summary(payload: dict[str, Any]) -> dict[str, Any]:
    initial = payload.get("initial_freshness") if isinstance(payload.get("initial_freshness"), dict) else {}
    final = payload.get("final_freshness") if isinstance(payload.get("final_freshness"), dict) else {}
    children = payload.get("children") if isinstance(payload.get("children"), list) else []
    created_at = payload.get("created_at")
    return {
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": artifact_age_hours(created_at),
        "freshness_status": freshness_status(created_at, 6.0),
        "artifact_status": payload.get("status") or "missing",
        "status": effective_artifact_status(payload.get("status"), created_at),
        "initial_status": initial.get("status"),
        "initial_summary": initial.get("summary"),
        "final_status": final.get("status"),
        "final_summary": final.get("summary"),
        "repair_plan_count": len(payload.get("repair_plan") or []),
        "child_count": len(children),
        "children": children,
        "ledger_mutated": payload.get("ledger_mutated"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
    }


def build_inline_strategy_decision_payload(fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        from strategy_proposal_decision_auditor import build_record as build_strategy_decision_record

        payload = build_strategy_decision_record()
        return payload if isinstance(payload, dict) else fallback
    except Exception:
        return fallback


def build_inline_strategy_enforcement_payload(fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        from strategy_proposal_enforcement_auditor import build_record as build_strategy_enforcement_record

        payload = build_strategy_enforcement_record()
        return payload if isinstance(payload, dict) else fallback
    except Exception:
        return fallback


def normalize_artifact_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        return rel(path)
    if text.startswith("active-alpha-paper-monitor/"):
        return text
    if text.startswith("experiments/") or text.startswith("reports/") or text.startswith("handoffs/"):
        return f"active-alpha-paper-monitor/{text}"
    return text


def pipeline_source_status(expected: Path | None, actual_ref: Any) -> tuple[str, str, str]:
    expected_rel = rel(expected) if expected else ""
    actual = normalize_artifact_ref(actual_ref)
    if not expected_rel:
        return "missing_expected_source", expected_rel, actual
    if not actual:
        return "missing_source_ref", expected_rel, actual
    if actual == expected_rel:
        return "synced", expected_rel, actual
    return "stale_source", expected_rel, actual


def has_top_blocked_candidates(payload: dict[str, Any]) -> bool:
    no_entry = payload.get("no_entry_summary") if isinstance(payload.get("no_entry_summary"), dict) else {}
    candidates = no_entry.get("top_blocked_candidates") if isinstance(no_entry, dict) else []
    return bool([item for item in (candidates or []) if isinstance(item, dict)])


def runner_is_safe_paper_source(payload: dict[str, Any]) -> bool:
    return not (
        payload.get("live_orders_enabled") is True
        or payload.get("private_api_used") is True
        or payload.get("allow_real_orders") is True
        or bool(payload.get("safety_errors"))
    )


def latest_candidate_runner_for_top_blocked(scan_limit: int = 80) -> tuple[Path | None, dict[str, Any]]:
    paths = sorted(
        ACTIVE_ROOT.glob("experiments/*validation-progress-runner.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    latest_any = paths[0] if paths else None
    for path in paths[: max(1, scan_limit)]:
        payload = read_json(path, {})
        if isinstance(payload, dict) and runner_is_safe_paper_source(payload) and has_top_blocked_candidates(payload):
            no_entry = payload.get("no_entry_summary") or {}
            return path, {
                "status": "candidate_bearing_runner_selected",
                "artifact": rel(path),
                "candidate_count": len(no_entry.get("top_blocked_candidates") or []),
                "latest_any_runner": rel(latest_any),
            }
    return latest_any, {
        "status": "fallback_no_candidate_runner",
        "artifact": rel(latest_any),
        "candidate_count": 0,
        "latest_any_runner": rel(latest_any),
    }


def pipeline_row(
    name: str,
    artifact_path: Path | None,
    payload: dict[str, Any],
    source_field: str | None,
    expected_source: Path | None,
    upstream_status: str | None = None,
) -> dict[str, Any]:
    if not artifact_path:
        return {
            "name": name,
            "status": "missing",
            "artifact": "",
            "source_field": source_field,
            "expected_source": rel(expected_source) if expected_source else "",
            "actual_source": "",
            "next_action": f"run_{name}",
        }
    if source_field:
        status, expected_rel, actual = pipeline_source_status(expected_source, payload.get(source_field))
    else:
        status, expected_rel, actual = "present", "", ""
    if upstream_status and upstream_status not in {"synced", "present"}:
        status = "upstream_stale"
    next_action = "none"
    if status in {"missing", "missing_source_ref", "stale_source", "upstream_stale"}:
        next_action = f"rerun_{name}_after_latest_runner"
    return {
        "name": name,
        "status": status,
        "artifact": rel(artifact_path),
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "source_field": source_field,
        "expected_source": expected_rel,
        "actual_source": actual,
        "next_action": next_action,
    }


def inline_pipeline_freshness(
    runner_exp: Path | None,
    runner_payload: dict[str, Any],
    recovery_watchlist_exp: Path | None,
    recovery_watchlist_payload: dict[str, Any],
    recovery_sampler_exp: Path | None,
    recovery_sampler_payload: dict[str, Any],
    blocked_retest_exp: Path | None,
    blocked_retest_payload: dict[str, Any],
    blocked_retest_sampler_exp: Path | None,
    blocked_retest_sampler_payload: dict[str, Any],
    capital_allocation_exp: Path | None,
    capital_allocation_payload: dict[str, Any],
) -> dict[str, Any]:
    top_blocked_runner_exp, top_blocked_runner_meta = latest_candidate_runner_for_top_blocked()
    watchlist = pipeline_row(
        "recovery_watchlist_monitor",
        recovery_watchlist_exp,
        recovery_watchlist_payload,
        "source_dynamic_market_context",
        runner_exp,
    )
    recovery_sampler = pipeline_row(
        "recovery_watchlist_paper_sampler",
        recovery_sampler_exp,
        recovery_sampler_payload,
        "source_watchlist",
        recovery_watchlist_exp,
        upstream_status=watchlist["status"],
    )
    retest = pipeline_row(
        "top_blocked_candidate_retest_lab",
        blocked_retest_exp,
        blocked_retest_payload,
        "source_runner",
        top_blocked_runner_exp,
    )
    retest_sampler = pipeline_row(
        "top_blocked_retest_quality_scout_sampler",
        blocked_retest_sampler_exp,
        blocked_retest_sampler_payload,
        "source_retest",
        blocked_retest_exp,
        upstream_status=retest["status"],
    )
    capital = pipeline_row(
        "paper_capital_allocation_auditor",
        capital_allocation_exp,
        capital_allocation_payload,
        None,
        None,
    )
    rows = [watchlist, recovery_sampler, retest, retest_sampler, capital]
    stale = [row for row in rows if row["status"] in {"stale_source", "upstream_stale"}]
    missing = [row for row in rows if row["status"].startswith("missing")]
    artifact_sync_status = "pass" if not stale and not missing else "warn"
    status = artifact_sync_status
    runner_created_at = runner_payload.get("created_at") or runner_payload.get("generated_at") or runner_payload.get("completed_at")
    runner_age_hours = artifact_age_hours(runner_created_at)
    runner_freshness = freshness_status(runner_created_at, 6.0)
    current_market_readiness_status = "fresh" if runner_freshness == "fresh" else "stale_not_actionable"
    next_actions = [f"{row['name']}: {row['next_action']}" for row in [*stale, *missing]]
    if not runner_exp:
        status = "blocked"
        current_market_readiness_status = "missing_not_actionable"
        next_actions.insert(0, "Run validation_progress_runner before relying on downstream paper reports.")
    elif runner_freshness != "fresh":
        status = "warn"
        next_actions.insert(0, "Refresh validation_progress_runner with current public market data before any new paper entry or current-signal claim.")
    if not next_actions:
        next_actions = ["All tracked downstream paper artifacts are synced or present."]
    return {
        "run_id": "inline-dashboard-pipeline-freshness",
        "created_at": now_local().isoformat(),
        "status": status,
        "artifact_sync_status": artifact_sync_status,
        "current_market_readiness_status": current_market_readiness_status,
        "summary": {
            "check_count": len(rows),
            "stale_count": len(stale),
            "missing_count": len(missing),
            "synced_or_present_count": len(rows) - len(stale) - len(missing),
            "runner_stale_count": 0 if runner_freshness == "fresh" else 1,
        },
        "latest_runner": {
            "artifact": rel(runner_exp),
            "run_id": runner_payload.get("run_id"),
            "created_at": runner_created_at,
            "age_hours": runner_age_hours,
            "freshness_status": runner_freshness,
            "dynamic_scan_status": (runner_payload.get("dynamic_scan_universe") or {}).get("status"),
        },
        "latest_candidate_runner": top_blocked_runner_meta,
        "checks": rows,
        "next_actions": next_actions,
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
    }


def render_dashboard(payload: dict[str, Any]) -> str:
    ledger = payload["ledger"]
    automation = payload["automation"]
    automation_recovery = payload["automation_recovery"]
    runner = payload["latest_runner"]
    phase = payload["latest_phase"]
    paper_testnet_risk_control = payload["paper_testnet_risk_control"]
    phase2_quality = payload["phase2_quality"]
    phase2_quality_gate = payload["phase2_quality_gate"]
    phase3_pressure = payload["phase3_pressure"]
    impulse = payload["latest_impulse"]
    compounding = payload["latest_compounding"]
    current_signal_artifact = payload["latest_current_signal_artifact"]
    current_signal_retest = payload["latest_current_signal_retest"]
    artifact_inventory = payload["artifact_inventory"]
    strategy_backlog = payload["strategy_backlog"]
    paper_auto_learning = payload["paper_auto_learning"]
    strategy_decision = payload["strategy_proposal_decision"]
    strategy_enforcement = payload["strategy_proposal_enforcement"]
    recovery_watchlist = payload["recovery_watchlist"]
    recovery_sampler = payload["recovery_sampler"]
    blocked_retest = payload["blocked_retest"]
    blocked_retest_sampler = payload["blocked_retest_sampler"]
    trade_attribution = payload["trade_attribution"]
    capital_allocation = payload["capital_allocation"]
    ledger_integrity = payload["ledger_integrity"]
    binance_health = payload["binance_market_data_health"]
    binance_kline_cache = payload["binance_kline_cache"]
    kline_research_repro = payload["kline_research_reproducibility"]
    signal_contract = payload["paper_signal_contract"]
    market_context = payload["paper_market_context"]
    pipeline_freshness = payload["pipeline_freshness"]
    pipeline_repair = payload["pipeline_repair"]
    preflight = payload["latest_preflight"]
    latest = payload["latest_files"]
    sample_growth = sample_growth_action_board(runner, phase, capital_allocation, market_context, blocked_retest_sampler)
    if automation.get("status") == "PAUSED" and preflight.get("readiness_status") == "ready_but_paused":
        sample_growth["status"] = "paused_ready_for_user_confirmation"
        sample_growth["sample_action"] = "await_user_confirmation_before_hourly_new_sampling"
    elif preflight.get("readiness_status") == "not_ready" or automation.get("status") in {"missing", "MISSING", None}:
        sample_growth["status"] = "not_ready_operational_authorization_blocked"
        sample_growth["sample_action"] = "restore_single_safe_automation_and_fresh_data_before_sampling"
    open_symbols = ", ".join(ledger.get("open_symbols") or []) or "-"
    selected_symbols = ", ".join((runner.get("selected_symbols") or [])[:24]) or "-"
    reason_counts = runner.get("no_entry_reason_counts") or {}
    current_signal_state = runner.get("current_signal_refresh_state") or {}
    if int(current_signal_artifact.get("frames_loaded") or 0) <= 0:
        latest_current_signal_state = "latest_artifact_empty_or_missing"
    elif current_signal_artifact.get("freshness_status") != "fresh":
        latest_current_signal_state = "latest_artifact_stale_not_actionable"
    else:
        latest_current_signal_state = "latest_artifact_usable_and_fresh"
    impulse_rows = impulse.get("top_signals") or []
    top_impulse_symbols = ", ".join(
        str(item.get("symbol"))
        for item in impulse_rows[:5]
        if item.get("symbol")
    ) or "-"
    preferred_proposed_rows = strategy_backlog.get("top_items") or runner.get("top_proposed_changes") or []
    proposed_change_source = "strategy_backlog" if strategy_backlog.get("top_items") else "latest_runner"
    proposed_change_ids = ", ".join(
        str(item.get("id"))
        for item in preferred_proposed_rows
        if item.get("id")
    ) or "-"
    current_operator_state = "watch_only"
    if ledger.get("open_count"):
        current_operator_state = "review_open_paper_positions"
    elif runner.get("no_entry_status") == "no_new_entry":
        current_operator_state = "no_new_entry_waiting_for_valid_signal"
    if compounding.get("overall_status") == "api_paper_flow_available_with_pressure_watch_but_target_not_proven":
        current_operator_state = "paper_flow_ready_but_strategy_edge_unproven"
    if automation.get("status") == "PAUSED" and preflight.get("readiness_status") == "ready_but_paused":
        current_operator_state = "paused_ready_for_user_confirmation"
    elif preflight.get("readiness_status") == "not_ready" or automation.get("status") in {"missing", "MISSING", None}:
        current_operator_state = "not_ready_operational_authorization_blocked"
    current_operator_action = "do_not_place_real_orders; keep paper-only scan active"
    next_loop_expectation = "next hourly run should refresh preflight, runner, sample audit, compounding audit and this dashboard"
    if current_operator_state == "paused_ready_for_user_confirmation":
        current_operator_action = "keep hourly automation paused; wait for explicit user confirmation before new paper sampling"
        next_loop_expectation = "after explicit resume confirmation, run preflight then one hourly paper-only validation cycle"
    elif current_operator_state == "not_ready_operational_authorization_blocked":
        current_operator_action = "do not sample; restore the single safe automation definition and rebuild fresh durable market data only after explicit user approval"
        next_loop_expectation = "preflight must return safe and current deploy authorization must be nonzero before any paper entry scan"
    repair_child_rows = [
        f"| `{child.get('label')}` | `{child.get('status')}` | "
        f"`{(child.get('stdout_json') or {}).get('opened_count', '-')}` | "
        f"`{(child.get('stdout_json') or {}).get('blocked_count', '-')}` |"
        for child in (pipeline_repair.get("children") or [])[:4]
    ] or ["| - | - | - | - |"]

    lines = [
        "# Active Alpha Automation Latest Status",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        "- scope: `paper_only`",
        "- live_orders_enabled: `false`",
        "- private_api_used: `false`",
        "- real_orders: `blocked`",
        "",
        "## Automation Registry",
        "",
        f"- id: `{automation.get('id') or 'missing'}`",
        f"- status: `{automation.get('status') or 'unknown'}`",
        f"- rrule: `{automation.get('rrule') or 'unknown'}`",
        f"- workspace: `{automation.get('execution_environment') or 'unknown'}`",
        f"- local_automation_toml_count: `{automation.get('local_automation_toml_count')}`",
        f"- artifact_index_consolidated: `{artifact_inventory.get('consolidated')}`",
        f"- latest_preflight_safety: `{preflight.get('safety_status') or 'missing'}`",
        f"- recovery_watchlist_monitor_self_test: `{preflight.get('recovery_watchlist_monitor_self_test_status') or 'missing'}`",
        f"- recovery_sampler_self_test: `{preflight.get('recovery_sampler_self_test_status') or 'missing'}`",
        f"- blocked_retest_sampler_self_test: `{preflight.get('blocked_retest_sampler_self_test_status') or 'missing'}`",
        f"- capital_allocation_self_test: `{preflight.get('capital_allocation_self_test_status') or 'missing'}`",
        f"- ledger_integrity_self_test: `{preflight.get('ledger_integrity_self_test_status') or 'missing'}`",
        f"- market_context_self_test: `{preflight.get('market_context_self_test_status') or 'missing'}`",
        f"- binance_market_data_health_self_test: `{preflight.get('binance_market_data_health_self_test_status') or 'missing'}`",
        f"- binance_kline_cache_builder_self_test: `{preflight.get('binance_kline_cache_builder_self_test_status') or 'missing'}`",
        f"- weekly_goal_strategy_lab_self_test: `{preflight.get('weekly_goal_strategy_lab_self_test_status') or 'missing'}`",
        f"- kline_research_reproducibility_self_test: `{preflight.get('kline_research_reproducibility_self_test_status') or 'missing'}`",
        f"- current_signal_probe_self_test: `{preflight.get('current_signal_probe_self_test_status') or 'missing'}`",
        f"- strategy_recovery_optimizer_self_test: `{preflight.get('strategy_recovery_optimizer_self_test_status') or 'missing'}`",
        f"- pipeline_freshness_self_test: `{preflight.get('pipeline_freshness_self_test_status') or 'missing'}`",
        f"- automation_recovery_planner_self_test: `{preflight.get('automation_recovery_planner_self_test_status') or 'missing'}`",
        f"- goal_completion_auditor_self_test: `{preflight.get('goal_completion_auditor_self_test_status') or 'missing'}`",
        f"- paper_testnet_risk_control_self_test: `{preflight.get('paper_testnet_risk_control_self_test_status') or 'missing'}`",
        f"- automation_recovery_plan_status: `{preflight.get('automation_recovery_plan_status') or automation_recovery.get('status')}`",
        f"- can_resume_hourly_new_sampling: `{preflight.get('can_resume_hourly_new_sampling')}`",
        f"- kline_cache_preflight: `{preflight.get('kline_cache_status')}` storage `{preflight.get('kline_cache_storage_status')}` refresh_required `{preflight.get('kline_cache_refresh_required')}`",
        "",
        "## Automation Recovery Plan",
        "",
        f"- status: `{automation_recovery.get('status')}`",
        f"- reason: `{automation_recovery.get('reason')}`",
        f"- automation_count: `{automation_recovery.get('automation_count')}`",
        f"- expected_id_count: `{automation_recovery.get('expected_id_count')}`",
        f"- requires_explicit_user_approval: `{automation_recovery.get('requires_explicit_user_approval')}`",
        f"- max_allowed_action: `{automation_recovery.get('max_allowed_action')}`",
        f"- automation_mutated: `{automation_recovery.get('automation_mutated')}`",
        f"- proposed_id: `{automation_recovery.get('proposed_id')}`",
        f"- proposed_initial_status: `{automation_recovery.get('proposed_initial_status')}`",
        f"- proposed_schedule: `{automation_recovery.get('proposed_schedule')}`",
        f"- prompt_sha256: `{automation_recovery.get('prompt_sha256')}`",
        f"- next_action: {automation_recovery.get('next_action') or '-'}",
        f"- report: `active-alpha-paper-monitor/reports/AUTOMATION_RECOVERY_PLAN.md`",
        "",
        "## Pipeline Freshness",
        "",
        f"- status: `{pipeline_freshness.get('status')}`",
        f"- artifact_sync_status: `{pipeline_freshness.get('artifact_sync_status')}`",
        f"- current_market_readiness_status: `{pipeline_freshness.get('current_market_readiness_status')}`",
        f"- latest_runner_freshness: `{pipeline_freshness.get('latest_runner_freshness_status')}` age_hours `{pipeline_freshness.get('latest_runner_age_hours')}`",
        f"- latest_runner_dynamic_scan_status: `{pipeline_freshness.get('latest_runner_dynamic_scan_status')}`",
        f"- top_blocked_candidate_runner: `{pipeline_freshness.get('latest_candidate_runner_artifact') or '-'}`",
        f"- top_blocked_candidate_runner_status: `{pipeline_freshness.get('latest_candidate_runner_status') or '-'}`",
        f"- top_blocked_candidate_count: `{pipeline_freshness.get('latest_candidate_runner_candidate_count')}`",
        f"- stale_count: `{pipeline_freshness.get('stale_count')}`",
        f"- missing_count: `{pipeline_freshness.get('missing_count')}`",
        f"- live_orders_enabled: `{pipeline_freshness.get('live_orders_enabled')}`",
        f"- private_api_used: `{pipeline_freshness.get('private_api_used')}`",
        f"- ledger_mutated: `{pipeline_freshness.get('ledger_mutated')}`",
        "",
        "| Artifact | Status | Next Action |",
        "|---|---|---|",
        *[
            f"| `{row.get('name')}` | `{row.get('status')}` | `{row.get('next_action')}` |"
            for row in (pipeline_freshness.get("rows") or [])[:6]
        ],
        "",
        "### Pipeline Next Actions",
        "",
        *[f"- {item}" for item in (pipeline_freshness.get("next_actions") or ["No pipeline freshness summary available."])[:5]],
        "",
        "### Pipeline Freshness Repair",
        "",
        f"- status: `{pipeline_repair.get('status')}`",
        f"- artifact_status: `{pipeline_repair.get('artifact_status')}`",
        f"- freshness_status: `{pipeline_repair.get('freshness_status')}` age_hours `{pipeline_repair.get('age_hours')}`",
        f"- initial_status: `{pipeline_repair.get('initial_status')}`",
        f"- final_status: `{pipeline_repair.get('final_status')}`",
        f"- repair_plan_count: `{pipeline_repair.get('repair_plan_count')}`",
        f"- child_count: `{pipeline_repair.get('child_count')}`",
        f"- ledger_mutated: `{pipeline_repair.get('ledger_mutated')}`",
        f"- live_orders_enabled: `{pipeline_repair.get('live_orders_enabled')}`",
        f"- private_api_used: `{pipeline_repair.get('private_api_used')}`",
        "",
        "| Child | Status | Opened | Blocked |",
        "|---|---|---:|---:|",
        *repair_child_rows,
        "",
        "## Paper Portfolio",
        "",
        f"- ledger_updated_at: `{ledger.get('updated_at')}`",
        f"- equity_usd: `${ledger.get('equity_usd'):.6f}`",
        f"- cash_usd: `${ledger.get('cash_usd'):.6f}`",
        f"- open_value_usd: `${ledger.get('open_value_usd'):.6f}`",
        f"- net_return_pct: `{ledger.get('net_return_pct')}`",
        f"- max_drawdown_pct: `{ledger.get('max_drawdown_pct')}`",
        f"- open_positions: `{ledger.get('open_count')}` ({open_symbols})",
        f"- closed_trades: `{ledger.get('closed_count')}`",
        f"- win_rate_pct: `{ledger.get('win_rate_pct')}`",
        f"- realized_pnl_usd: `${ledger.get('realized_pnl_usd')}`",
        "",
        "## Paper Capital Allocation",
        "",
        f"- generated_at: `{capital_allocation.get('created_at') or 'missing'}`",
        f"- policy: `{capital_allocation.get('policy') or 'missing'}`",
        f"- current_deployment_authorization: `{capital_allocation.get('current_deployment_authorization') or 'missing'}`",
        f"- authorization_blockers: `{capital_allocation.get('authorization_blockers')}`",
        f"- research_max_new_positions: `{capital_allocation.get('research_max_new_positions')}`",
        f"- research_per_trade_notional_usd: `${capital_allocation.get('research_per_trade_notional_usd')}`",
        f"- research_max_deployable_usd: `${capital_allocation.get('research_max_deployable_usd')}`",
        f"- max_new_positions_now: `{capital_allocation.get('max_new_positions_now')}`",
        f"- per_trade_notional_usd: `${capital_allocation.get('per_trade_notional_usd')}`",
        f"- max_deployable_now_usd: `${capital_allocation.get('max_deployable_now_usd')}`",
        f"- reserve_cash_usd: `${capital_allocation.get('reserve_cash_usd')}`",
        f"- idle_or_waiting_cash_usd: `${capital_allocation.get('idle_or_waiting_cash_usd')}`",
        f"- cash_ratio_pct: `{capital_allocation.get('cash_ratio_pct')}`",
        f"- target_cash_ratio_pct: `{capital_allocation.get('target_cash_ratio_pct')}`",
        f"- phase2_status: `{capital_allocation.get('phase2_status')}` closed `{capital_allocation.get('closed_count')}` win `{capital_allocation.get('win_rate_pct')}` pnl `${capital_allocation.get('realized_pnl_usd')}`",
        f"- active_proposed_changes: `{capital_allocation.get('active_proposed_changes')}`",
        f"- ledger_mutated: `{capital_allocation.get('ledger_mutated')}`",
        f"- instruction: {capital_allocation.get('current_instruction') or '-'}",
        "",
        "### Allocation Reasons",
        "",
        *[f"- `{reason}`" for reason in (capital_allocation.get("decision_reasons") or ["missing_capital_allocation_audit"])],
        "",
        "## Paper Ledger Integrity",
        "",
        f"- generated_at: `{ledger_integrity.get('created_at') or 'missing'}`",
        f"- status: `{ledger_integrity.get('status') or 'missing'}`",
        f"- paper_orders: `{ledger_integrity.get('order_count')}`",
        f"- open_positions: `{ledger_integrity.get('open_position_count')}`",
        f"- closed_trades: `{ledger_integrity.get('closed_trade_count')}`",
        f"- warning_count: `{ledger_integrity.get('warning_count')}`",
        f"- blocked_count: `{ledger_integrity.get('blocked_count')}`",
        f"- repairs_applied: `{ledger_integrity.get('repairs_applied')}`",
        f"- live_orders_enabled: `{ledger_integrity.get('live_orders_enabled')}`",
        f"- private_api_used: `{ledger_integrity.get('private_api_used')}`",
        "",
        "## Binance Market Data Health",
        "",
        f"- generated_at: `{binance_health.get('created_at') or 'missing'}`",
        f"- status: `{binance_health.get('status') or 'missing'}`",
        f"- freshness_status: `{binance_health.get('freshness_status')}` age_hours `{binance_health.get('age_hours')}`",
        f"- primary_base_url: `{binance_health.get('primary_base_url') or '-'}`",
        f"- base_url_count: `{len(binance_health.get('base_urls') or [])}`",
        f"- endpoint_fallback_failure_count: `{binance_health.get('endpoint_fallback_failure_count')}`",
        f"- passed/warning/blocked symbols: `{binance_health.get('passed_symbol_count')}/{binance_health.get('warning_symbol_count')}/{binance_health.get('blocked_symbol_count')}`",
        f"- live_orders_enabled: `{binance_health.get('live_orders_enabled')}`",
        f"- private_api_used: `{binance_health.get('private_api_used')}`",
        f"- allow_real_orders: `{binance_health.get('allow_real_orders')}`",
        f"- ledger_mutated: `{binance_health.get('ledger_mutated')}`",
        "",
        "| Symbol | Status | 24h % | 24h Quote Vol | Spread bps | 1% Depth Min | Taker Buy | Missing Intervals |",
        "|---|---|---:|---:|---:|---:|---:|---|",
        *[
            f"| `{item.get('symbol')}` | `{item.get('status')}` | `{item.get('price_change_24h_pct')}` | "
            f"`{item.get('quote_volume_24h_usd')}` | `{item.get('spread_bps')}` | `{item.get('depth_1pct_min_usd')}` | "
            f"`{item.get('taker_buy_quote_ratio')}` | `{', '.join(item.get('missing_intervals') or []) or '-'}` |"
            for item in (binance_health.get("top_symbols") or [])
        ],
        "",
        "## Binance Kline Cache",
        "",
        f"- run_id: `{binance_kline_cache.get('run_id') or 'missing'}`",
        f"- completed_at: `{binance_kline_cache.get('completed_at') or binance_kline_cache.get('generated_at') or 'missing'}`",
        f"- status: `{binance_kline_cache.get('status') or 'missing'}`",
        f"- artifact_status: `{binance_kline_cache.get('artifact_status') or 'missing'}`",
        f"- freshness_status: `{binance_kline_cache.get('freshness_status')}` age_hours `{binance_kline_cache.get('age_hours')}`",
        f"- storage_status: `{binance_kline_cache.get('storage_status')}` cache_dir_exists `{binance_kline_cache.get('cache_dir_exists')}` replay_available `{binance_kline_cache.get('replay_available')}` refresh_required `{binance_kline_cache.get('refresh_required')}`",
        f"- manifest_status: `{binance_kline_cache.get('manifest_status')}` valid `{binance_kline_cache.get('manifest_valid')}` verified_files `{binance_kline_cache.get('verified_file_count')}` hash_mismatches `{binance_kline_cache.get('hash_mismatch_count')}` schema_invalid `{binance_kline_cache.get('schema_invalid_count')}` untracked `{binance_kline_cache.get('untracked_data_file_count')}`",
        f"- manifest_path: `{binance_kline_cache.get('manifest_path') or '-'}`",
        f"- selected_symbol_count: `{binance_kline_cache.get('selected_symbol_count')}`",
        f"- selected_symbols: `{', '.join(binance_kline_cache.get('selected_symbols') or []) or '-'}`",
        f"- requested_intervals: `{', '.join(binance_kline_cache.get('requested_intervals') or []) or '-'}`",
        f"- current_config_default_intervals: `{', '.join(binance_kline_cache.get('configured_default_intervals') or []) or '-'}`",
        f"- current_config_required_operational_intervals: `{', '.join(binance_kline_cache.get('configured_required_operational_intervals') or []) or '-'}`",
        f"- current_config_optional_research_intervals: `{', '.join(binance_kline_cache.get('configured_optional_research_intervals') or []) or '-'}`",
        f"- current_config_request_budget: `{binance_kline_cache.get('configured_request_budget')}`",
        f"- current_config_coverage_action: `{binance_kline_cache.get('configured_coverage_action') or '-'}`",
        f"- file_count_declared: `{binance_kline_cache.get('declared_file_count')}`",
        f"- file_count_actual: `{binance_kline_cache.get('actual_file_count')}`",
        f"- missing_declared_file_count: `{binance_kline_cache.get('missing_declared_file_count')}`",
        f"- failure_count: `{binance_kline_cache.get('failure_count')}`",
        f"- fallback_event_count: `{binance_kline_cache.get('fallback_event_count')}`",
        f"- cache_dir: `{binance_kline_cache.get('cache_dir') or '-'}`",
        f"- binance_public_market_data_only: `{binance_kline_cache.get('binance_public_market_data_only')}`",
        f"- live_orders_enabled: `{binance_kline_cache.get('live_orders_enabled')}`",
        f"- private_api_keys_logged: `{binance_kline_cache.get('private_api_keys_logged')}`",
        f"- note: {binance_kline_cache.get('symbol_selection_note') or 'Kline cache evidence missing.'}",
        "",
        "| Top Discovery Symbol | Score | 24h % | 24h Quote Vol | Trades 24h |",
        "|---|---:|---:|---:|---:|",
        *[
            f"| `{item.get('symbol')}` | `{item.get('score')}` | `{item.get('price_change_pct_24h')}` | "
            f"`{item.get('quote_volume_24h')}` | `{item.get('trade_count_24h')}` |"
            for item in (binance_kline_cache.get("top_discovered_symbols") or [])
        ],
        "",
        "## Kline Research Reproducibility",
        "",
        f"- status: `{kline_research_repro.get('status')}`",
        f"- reproducible/nonreproducible: `{kline_research_repro.get('reproducible_count')}/{kline_research_repro.get('nonreproducible_count')}`",
        f"- promotion_allowed_count: `{kline_research_repro.get('promotion_allowed_count')}`",
        f"- current_artifact: `{kline_research_repro.get('current_artifact')}`",
        f"- current_artifact_reproducible: `{kline_research_repro.get('current_artifact_reproducible')}`",
        f"- historical_nonreproducible_count: `{kline_research_repro.get('historical_nonreproducible_count')}`",
        f"- max_allowed_action: `{kline_research_repro.get('max_allowed_action')}`",
        f"- fresh_cache_rebuild_required: `{kline_research_repro.get('fresh_cache_rebuild_required')}`",
        f"- report: `{kline_research_repro.get('report') or 'active-alpha-paper-monitor/reports/KLINE_RESEARCH_REPRODUCIBILITY_AUDIT.md'}`",
        "",
        "| Research Artifact | Declared Frames | Actual/Verified | Manifest | Protocol | Execution | Status | Promotion |",
        "|---|---:|---:|---|---|---|---|---|",
        *[
            f"| `{item.get('artifact')}` | `{item.get('declared_frames')}` | `{item.get('actual_files')}/{item.get('verified_files')}` | "
            f"`{item.get('manifest_ok')}` | `{item.get('protocol_ok')}` | `{item.get('execution_model_ok')}` | "
            f"`{item.get('status')}` | `{item.get('promotion_allowed')}` |"
            for item in (kline_research_repro.get("top_artifacts") or [])
        ],
        *([] if kline_research_repro.get("top_artifacts") else ["| - | - | - | `False` | `False` | `False` | `missing` | `False` |"]),
        "",
        "## Latest Runner Signal",
        "",
        f"- signal_source: `{runner.get('signal_source') or 'latest_runner'}`",
        f"- source_artifact: `{runner.get('source_artifact') or '-'}`",
        f"- latest_maintenance_runner_artifact: `{runner.get('latest_maintenance_runner_artifact') or '-'}`",
        f"- run_id: `{runner.get('run_id') or 'unknown'}`",
        f"- source_created_at: `{runner.get('created_at') or 'missing'}`",
        f"- source_freshness: `{runner.get('freshness_status')}` age_hours `{runner.get('age_hours')}`",
        f"- status: `{runner.get('status') or 'unknown'}`",
        f"- dynamic_pool_status: `{runner.get('dynamic_pool_status') or 'unknown'}`",
        f"- market_regime: `{runner.get('market_regime') or 'unknown'}`",
        f"- market_atmosphere: `{runner.get('market_atmosphere') or 'unknown'}`",
        f"- short_term_state: `{runner.get('short_term_state') or 'unknown'}`",
        f"- sentiment_state: `{runner.get('sentiment_state') or 'unknown'}`",
        f"- pre_run_current_signal_evidence: `{current_signal_state.get('status') or 'unknown'}` age_hours `{current_signal_state.get('age_hours')}` refresh_required `{runner.get('auto_current_signal_refresh_required')}` kline_prefetch `{runner.get('auto_current_signal_kline_prefetch_enabled')}`",
        f"- latest_current_signal_artifact: frames `{current_signal_artifact.get('frames_loaded')}` strategies `{current_signal_artifact.get('strategies_scanned')}` candidates `{current_signal_artifact.get('deduplicated_candidate_count')}`",
        f"- why_pool_changed: `{runner.get('why_pool_changed') or 'unknown'}`",
        f"- selected_symbols: `{selected_symbols}`",
        f"- no_entry_status: `{runner.get('no_entry_status') or 'unknown'}`",
        f"- no_entry_reason_counts: `{reason_counts}`",
        f"- proposed_change_count: `{runner.get('proposed_change_count')}`",
        f"- strategy_backlog_active: `{strategy_backlog.get('active_proposed_changes')}` pending_review `{strategy_backlog.get('pending_human_review')}`",
        f"- proposed_change_display_source: `{proposed_change_source}`",
        "",
        "## Paper Auto Learning Loop",
        "",
        f"- strategy_version: `{paper_auto_learning.get('strategy_version') or 'missing'}`",
        f"- overlay_updated_at: `{paper_auto_learning.get('overlay_updated_at') or 'missing'}`",
        f"- auto_learning_enabled: `{paper_auto_learning.get('auto_learning_enabled')}`",
        f"- auto_apply_scope: `{paper_auto_learning.get('auto_apply_scope')}`",
        f"- latest_evolver_run_id: `{paper_auto_learning.get('latest_evolver_run_id') or 'missing'}`",
        f"- latest_evolver_created_at: `{paper_auto_learning.get('latest_evolver_created_at') or 'missing'}`",
        f"- considered_changes: `{paper_auto_learning.get('considered_changes')}`",
        f"- applied_or_ab_testing: `{paper_auto_learning.get('applied_or_ab_testing')}`",
        f"- newly_applied_or_ab_testing: `{paper_auto_learning.get('newly_applied_or_ab_testing')}`",
        f"- reverted: `{paper_auto_learning.get('reverted')}`",
        f"- awaiting_forward_samples: `{paper_auto_learning.get('awaiting_forward_samples')}`",
        f"- deduped_change_log_removed: `{paper_auto_learning.get('deduped_change_log_removed')}`",
        f"- strategy_version_changed: `{paper_auto_learning.get('strategy_version_changed')}`",
        f"- change_status_counts: `{paper_auto_learning.get('change_status_counts')}`",
        f"- entry/family/interval_rules: `{paper_auto_learning.get('entry_mode_rule_count')}/{paper_auto_learning.get('strategy_family_rule_count')}/{paper_auto_learning.get('interval_rule_count')}`",
        f"- live_orders_enabled: `{paper_auto_learning.get('live_orders_enabled')}`",
        f"- private_api_used: `{paper_auto_learning.get('private_api_used')}`",
        "",
        "| Change | Status | Samples | Win % | Net PnL |",
        "|---|---|---:|---:|---:|",
        *[
            f"| `{item.get('change_id')}` | `{item.get('status_after')}` | `{item.get('sample_count')}` | "
            f"`{item.get('win_rate_pct')}` | `${item.get('net_pnl_usd')}` |"
            for item in (paper_auto_learning.get("rollback_watch") or [])
        ],
        *([] if paper_auto_learning.get("rollback_watch") else ["| - | - | - | - | - |"]),
        "",
        "## Sample Growth Action Board",
        "",
        f"- status: `{sample_growth.get('status')}`",
        f"- sample_action: `{sample_growth.get('sample_action')}`",
        f"- closed_trade_gap_to_phase2_min: `{sample_growth.get('closed_trade_gap_to_phase2_min')}`",
        f"- capital_policy: `{sample_growth.get('capital_policy')}`",
        f"- max_deployable_now_usd: `${sample_growth.get('max_deployable_now_usd')}`",
        f"- per_trade_notional_usd: `${sample_growth.get('per_trade_notional_usd')}`",
        f"- reason_counts: `{sample_growth.get('reason_counts')}`",
        "- rule: `only paper-only quality scouts may use deployable paper cash; blocked candidates are not orders`",
        "",
        "| Symbol | Current Block | Next Evidence Step | Max Paper Notional |",
        "|---|---|---|---:|",
        *[
            f"| `{item.get('symbol')}` | `{item.get('block')}` | `{item.get('next_step')}` | "
            f"`${item.get('max_paper_notional_usd')}` |"
            for item in (sample_growth.get("candidate_actions") or [])
        ],
        *([] if sample_growth.get("candidate_actions") else ["| - | - | keep_scanning_until_valid_signal | $0 |"]),
        "",
        "### Quality Scout Trigger Watch",
        "",
        "- rule: `retest-passing candidates still wait for current trigger, spread/depth and safety gates`",
        "",
        "| Symbol | Decision | Last Price | Trigger | Distance | Spread bps | Depth USD | OOS Win % | OOS Net % | Reasons |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
        *[
            f"| `{item.get('symbol')}` | `{item.get('decision')}` | `{item.get('last_price')}` | "
            f"`{item.get('trigger_price')}` | `{item.get('distance_to_trigger')}` | `{item.get('spread_bps')}` | "
            f"`{item.get('depth_usd')}` | `{item.get('oos_win_rate_pct')}` | `{item.get('oos_net_return_pct')}` | "
            f"`{', '.join(str(reason) for reason in (item.get('reasons') or [])) or '-'}` |"
            for item in (sample_growth.get("quality_scout_trigger_watch") or [])
        ],
        *([] if sample_growth.get("quality_scout_trigger_watch") else ["| - | - | - | - | - | - | - | - | - | - |"]),
        "",
        "### Sample Growth Notes",
        "",
        *[f"- {item}" for item in (sample_growth.get("notes") or [])],
        "",
        "## Paper Signal Contract",
        "",
        f"- generated_at: `{signal_contract.get('created_at') or 'missing'}`",
        f"- status: `{signal_contract.get('status') or 'missing'}`",
        f"- complete/partial/incomplete: `{signal_contract.get('complete_count')}/{signal_contract.get('partial_count')}/{signal_contract.get('incomplete_count')}`",
        f"- unsafe_count: `{signal_contract.get('unsafe_count')}`",
        f"- missing_field_counts: `{signal_contract.get('missing_field_counts')}`",
        f"- live_orders_enabled: `{signal_contract.get('live_orders_enabled')}`",
        f"- private_api_used: `{signal_contract.get('private_api_used')}`",
        "",
        "| Symbol | Type | Status | Action | Confidence | Missing |",
        "|---|---|---|---|---:|---|",
        *[
            f"| `{item.get('symbol')}` | `{item.get('type')}` | `{item.get('status')}` | "
            f"`{item.get('action')}` | `{item.get('confidence')}` | `{', '.join(item.get('missing') or []) or '-'}` |"
            for item in (signal_contract.get("top_signals") or [])
        ],
        "",
        "## Paper Market Context Coverage",
        "",
        f"- generated_at: `{market_context.get('created_at') or 'missing'}`",
        f"- status: `{market_context.get('status') or 'missing'}`",
        f"- trade_count: `{market_context.get('trade_count')}`",
        f"- complete/partial/missing: `{market_context.get('complete_count')}/{market_context.get('partial_count')}/{market_context.get('missing_count')}`",
        f"- coverage_pct: `{market_context.get('coverage_pct')}`",
        f"- explicit_regime_count: `{market_context.get('explicit_regime_count')}`",
        f"- live_orders_enabled: `{market_context.get('live_orders_enabled')}`",
        f"- private_api_used: `{market_context.get('private_api_used')}`",
        "",
        "| Trade | Symbol | Status | Missing | Regime |",
        "|---|---|---|---|---|",
        *[
            f"| `{item.get('paper_trade_id')}` | `{item.get('symbol')}` | `{item.get('status')}` | "
            f"`{', '.join(item.get('missing_or_unknown_fields') or []) or '-'}` | `{item.get('market_regime')}` |"
            for item in (market_context.get("top_gaps") or [])[:8]
        ],
        "",
        "## Proposed Strategy Changes",
        "",
        "| ID | Type | Status | Title |",
        "|---|---|---|---|",
    ]
    proposed_rows = preferred_proposed_rows
    if proposed_rows:
        for item in proposed_rows:
            lines.append(
                f"| `{item.get('id') or '-'}` | `{item.get('type') or '-'}` | "
                f"`{item.get('status') or '-'}` | {item.get('title') or '-'} |"
            )
    else:
        lines.append("| - | - | - | - |")
    lines.append("")
    lines.extend(
        [
            "## Strategy Backlog",
            "",
            f"- backlog_generated_at: `{strategy_backlog.get('generated_at') or 'missing'}`",
            f"- total_backlog_items: `{strategy_backlog.get('total_backlog_items')}`",
            f"- active_proposed_changes: `{strategy_backlog.get('active_proposed_changes')}`",
            f"- pending_human_review: `{strategy_backlog.get('pending_human_review')}`",
            "- backlog_report: `active-alpha-paper-monitor/reports/STRATEGY_ITERATION_BACKLOG.md`",
            "",
        "| ID | Source | Type | Status | Seen | Title |",
        "|---|---|---|---|---:|---|",
        ]
    )
    backlog_rows = strategy_backlog.get("top_items") or []
    if backlog_rows:
        for item in backlog_rows:
            lines.append(
                f"| `{item.get('id') or '-'}` | `{item.get('source') or '-'}` | "
                f"`{item.get('type') or '-'}` | `{item.get('status') or '-'}` | "
                f"`{item.get('seen_count') or 0}` | {item.get('title') or '-'} |"
            )
    else:
        lines.append("| - | - | - | - | - | - |")
    lines.append("")

    lines.extend(
        [
            "## Strategy Proposal Decision Board",
            "",
            f"- generated_at: `{strategy_decision.get('created_at') or 'missing'}`",
            f"- status: `{strategy_decision.get('status')}`",
            f"- proposal_count: `{strategy_decision.get('proposal_count')}`",
            f"- top_priority_id: `{strategy_decision.get('top_priority_id')}`",
            f"- paper_ab_test_candidates: `{strategy_decision.get('paper_ab_test_candidate_count')}`",
            f"- protective_gate_candidates: `{strategy_decision.get('protective_gate_candidate_count')}`",
            f"- config_mutated: `{strategy_decision.get('config_mutated')}`",
            f"- ledger_mutated: `{strategy_decision.get('ledger_mutated')}`",
            f"- decision_report: `{strategy_decision.get('report') or 'active-alpha-paper-monitor/reports/STRATEGY_PROPOSAL_DECISION_BOARD.md'}`",
            "",
            "| Priority | ID | Decision | Loss at Risk | Min Samples |",
            "|---:|---|---|---:|---:|",
        ]
    )
    decision_rows = strategy_decision.get("top_decisions") or []
    if decision_rows:
        for item in decision_rows:
            lines.append(
                f"| `{item.get('priority_score')}` | `{item.get('id') or '-'}` | "
                f"`{item.get('decision') or '-'}` | `${item.get('loss_at_risk_usd')}` | "
                f"`{item.get('minimum_new_closed_samples') if item.get('minimum_new_closed_samples') is not None else '-'}` |"
            )
    else:
        lines.append("| - | - | - | - | - |")
    lines.append("")

    lines.extend(
        [
            "## Strategy Proposal Enforcement Audit",
            "",
            f"- generated_at: `{strategy_enforcement.get('created_at') or 'missing'}`",
            f"- status: `{strategy_enforcement.get('status')}`",
            f"- decisions: `{strategy_enforcement.get('decision_count')}`",
            f"- enforced: `{strategy_enforcement.get('enforced_count')}`",
            f"- pending_tests: `{strategy_enforcement.get('pending_test_count')}`",
            f"- blocking: `{strategy_enforcement.get('blocking_count')}`",
            f"- recovery_plan_status: `{strategy_enforcement.get('recovery_plan_status')}`",
            f"- new_sample_policy: `{strategy_enforcement.get('new_sample_policy')}`",
            f"- config_mutated: `{strategy_enforcement.get('config_mutated')}`",
            f"- ledger_mutated: `{strategy_enforcement.get('ledger_mutated')}`",
            f"- enforcement_report: `{strategy_enforcement.get('report') or 'active-alpha-paper-monitor/reports/STRATEGY_PROPOSAL_ENFORCEMENT_AUDIT.md'}`",
            "",
            "| ID | Decision | Enforcement | Blocking | Evidence |",
            "|---|---|---|---:|---|",
        ]
    )
    enforcement_rows = strategy_enforcement.get("top_rows") or []
    if enforcement_rows:
        for item in enforcement_rows:
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            evidence_text = ", ".join(
                str(value)
                for value in (
                    evidence.get("current_block_hits")
                    or evidence.get("matched_gate_names")
                    or [evidence.get("policy")]
                    or []
                )
                if value
            )
            lines.append(
                f"| `{item.get('id') or '-'}` | `{item.get('decision') or '-'}` | "
                f"`{item.get('enforcement_status') or '-'}` | `{item.get('blocking')}` | "
                f"{evidence_text or '-'} |"
            )
    else:
        lines.append("| - | - | - | - | - |")
    lines.append("")

    lines.extend(
        [
            "## Recovery Watchlist",
            "",
            f"- generated_at: `{recovery_watchlist.get('created_at') or 'missing'}`",
            f"- freshness_status: `{recovery_watchlist.get('freshness_status')}` age_hours `{recovery_watchlist.get('age_hours')}`",
            f"- queue_count: `{recovery_watchlist.get('queue_count')}`",
            f"- actionable_paper_scout_count: `{recovery_watchlist.get('actionable_paper_scout_count')}`",
            f"- source_strategy_recovery_optimizer: `{recovery_watchlist.get('source_strategy_recovery_optimizer') or '-'}`",
            f"- source_dynamic_market_context: `{recovery_watchlist.get('source_dynamic_market_context') or '-'}`",
            f"- dynamic_scan_status: `{recovery_watchlist.get('dynamic_scan_status') or '-'}`",
            f"- dynamic_scan_reason: `{recovery_watchlist.get('dynamic_scan_reason') or '-'}`",
            f"- ticker_meta_status: `{recovery_watchlist.get('ticker_meta_status') or '-'}`",
            f"- social_handoff_status: `{recovery_watchlist.get('social_handoff_status') or '-'}`",
            f"- data_layer_degraded: `{str(recovery_watchlist.get('data_layer_degraded')).lower()}`",
            "- rule: `watchlist items are conditional paper-only triggers, not open orders`",
            "",
            "| Symbol | Action | Data | Block Reason | Price | Spread bps | Depth USD | Buy Ratio | Imbalance | Vol % | Trigger | Stop | Probability |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    recovery_rows = recovery_watchlist.get("top_items") or []
    if recovery_rows:
        for item in recovery_rows:
            lines.append(
                f"| `{item.get('symbol') or '-'}` | `{item.get('action') or '-'}` | "
                f"`{item.get('dynamic_scan_status') or '-'}`/`degraded={str(item.get('data_layer_degraded')).lower()}` | "
                f"`{item.get('block_reason') or '-'}` | "
                f"`{item.get('price')}` | `{item.get('spread_bps')}` | `{item.get('depth')}` | "
                f"`{item.get('buy_ratio')}` | `{item.get('imbalance')}` | `{item.get('volatility')}` | "
                f"`{item.get('trigger')}` | `{item.get('stop')}` | `{item.get('probability')}` |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | - |")
    lines.append("")

    blocked_rows = runner.get("top_blocked_candidates") or []
    lines.extend(
        [
            "## Top Blocked Candidates",
            "",
            "- rule: `paper-only diagnosis; blocked candidates are not orders and do not authorize live trading`",
            "",
            "| Symbol | Stage | Interval | Family | Entry Mode | Score | OOS Win % | OOS Net % | Block Reason | Match |",
            "|---|---|---|---|---|---:|---:|---:|---|---|",
        ]
    )
    if blocked_rows:
        for item in blocked_rows:
            lines.append(
                f"| `{item.get('symbol') or '-'}` | `{item.get('stage') or '-'}` | "
                f"`{item.get('interval') or '-'}` | `{item.get('strategy_family') or '-'}` | "
                f"`{item.get('entry_mode') or '-'}` | `{item.get('selection_score')}` | "
                f"`{item.get('oos_win_rate_pct')}` | `{item.get('oos_net_return_pct')}` | "
                f"`{item.get('primary_block_reason') or '-'}` | `{item.get('validation_recovery_match') or '-'}` |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - | - | - |")
    lines.append("")

    lines.extend(
        [
            "## Recovery Paper Sampler",
            "",
            f"- generated_at: `{recovery_sampler.get('created_at') or 'missing'}`",
            f"- freshness_status: `{recovery_sampler.get('freshness_status')}` age_hours `{recovery_sampler.get('age_hours')}`",
            f"- artifact_status: `{recovery_sampler.get('artifact_status') or 'missing'}`",
            f"- status: `{recovery_sampler.get('status') or 'missing'}`",
            f"- opened_count: `{recovery_sampler.get('opened_count')}`",
            f"- blocked_count: `{recovery_sampler.get('blocked_count')}`",
            f"- ledger_mutated: `{recovery_sampler.get('ledger_mutated')}`",
            f"- source_watchlist: `{recovery_sampler.get('source_watchlist') or '-'}`",
            "- rule: `opens at most one tiny paper scout only when every trigger/liquidity/safety gate passes`",
            "",
            "| Symbol | Decision | Reasons | Price | Trigger | Spread bps | Probability |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    sampler_rows = recovery_sampler.get("top_decisions") or []
    if sampler_rows:
        for item in sampler_rows:
            lines.append(
                f"| `{item.get('symbol') or '-'}` | `{item.get('decision') or '-'}` | "
                f"{', '.join(item.get('reasons') or []) or '-'} | "
                f"`{item.get('price')}` | `{item.get('trigger')}` | "
                f"`{item.get('spread_bps')}` | `{item.get('probability')}` |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - |")
    lines.append("")

    retest_rows = blocked_retest.get("top_results") or []
    lines.extend(
        [
            "## Top Blocked Retest Lab",
            "",
            f"- generated_at: `{blocked_retest.get('created_at') or 'missing'}`",
            f"- freshness_status: `{blocked_retest.get('freshness_status')}` age_hours `{blocked_retest.get('age_hours')}`",
            f"- artifact_status: `{blocked_retest.get('artifact_status') or 'missing'}`",
            f"- status: `{blocked_retest.get('status') or 'missing'}`",
            f"- candidates_seen: `{blocked_retest.get('candidates_seen')}`",
            f"- quality_scout_review_count: `{blocked_retest.get('quality_scout_review_count')}`",
            f"- keep_blocked_count: `{blocked_retest.get('keep_blocked_count')}`",
            f"- ledger_mutated: `{blocked_retest.get('ledger_mutated')}`",
            f"- source_runner: `{blocked_retest.get('source_runner') or '-'}`",
            "- rule: `research-only retest; does not open paper positions or change strategy config`",
            "",
            "| Symbol | Interval | Best Variant | OOS Trades | OOS Win % | OOS Net % | Distance to Breakout % | Decision | Next Action |",
            "|---|---|---|---:|---:|---:|---:|---|---|",
        ]
    )
    if retest_rows:
        for item in retest_rows:
            lines.append(
                f"| `{item.get('symbol') or '-'}` | `{item.get('interval') or '-'}` | "
                f"`{item.get('best_variant') or '-'}` | `{item.get('oos_trade_count')}` | "
                f"`{item.get('oos_win_rate_pct')}` | `{item.get('oos_net_return_pct')}` | "
                f"`{item.get('distance_to_breakout_pct')}` | `{item.get('decision') or '-'}` | "
                f"`{item.get('recommended_next_action') or '-'}` |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - | - |")
    lines.append("")

    scout_rows = blocked_retest_sampler.get("top_decisions") or []
    lines.extend(
        [
            "## Top Blocked Retest Quality Scout Sampler",
            "",
            f"- generated_at: `{blocked_retest_sampler.get('created_at') or 'missing'}`",
            f"- freshness_status: `{blocked_retest_sampler.get('freshness_status')}` age_hours `{blocked_retest_sampler.get('age_hours')}`",
            f"- artifact_status: `{blocked_retest_sampler.get('artifact_status') or 'missing'}`",
            f"- status: `{blocked_retest_sampler.get('status') or 'missing'}`",
            f"- candidate_count: `{blocked_retest_sampler.get('candidate_count')}`",
            f"- opened_count: `{blocked_retest_sampler.get('opened_count')}`",
            f"- blocked_count: `{blocked_retest_sampler.get('blocked_count')}`",
            f"- ledger_mutated: `{blocked_retest_sampler.get('ledger_mutated')}`",
            f"- source_retest: `{blocked_retest_sampler.get('source_retest') or '-'}`",
            "- rule: `opens at most one $25 paper scout only after current Binance price confirms trigger plus liquidity/cash/safety gates`",
            "",
            "| Symbol | Decision | Reasons | Price | Breakout | Spread bps | Depth USD | OOS Win % | OOS Net % | Variant |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    if scout_rows:
        for item in scout_rows:
            lines.append(
                f"| `{item.get('symbol') or '-'}` | `{item.get('decision') or '-'}` | "
                f"{', '.join(item.get('reasons') or []) or '-'} | "
                f"`{item.get('last_price')}` | `{item.get('breakout_level')}` | "
                f"`{item.get('spread_bps')}` | `{item.get('depth')}` | "
                f"`{item.get('oos_win_rate_pct')}` | `{item.get('oos_net_return_pct')}` | "
                f"`{item.get('best_variant') or '-'}` |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - | - | - |")
    lines.append("")

    attribution_summary = trade_attribution.get("summary") or {}
    lines.extend(
        [
            "## Paper Trade Attribution",
            "",
            f"- generated_at: `{trade_attribution.get('created_at') or 'missing'}`",
            f"- closed_trades_in_window: `{attribution_summary.get('closed_trades_in_window')}`",
            f"- win_rate_pct: `{attribution_summary.get('win_rate_pct')}`",
            f"- net_pnl_usd: `{attribution_summary.get('net_pnl_usd')}`",
            f"- total_friction_usd: `{attribution_summary.get('total_friction_usd')}`",
            f"- missed_profit_protection_count: `{attribution_summary.get('missed_profit_protection_count')}`",
            f"- avg_mfe_capture_ratio_pct: `{attribution_summary.get('avg_mfe_capture_ratio_pct')}`",
            f"- entry/exit_timing_issue_count: `{attribution_summary.get('entry_timing_issue_count')}/{attribution_summary.get('exit_timing_issue_count')}`",
            f"- market_regime_missing/misread_count: `{attribution_summary.get('market_regime_context_missing_count')}/{attribution_summary.get('market_regime_misread_count')}`",
            f"- symbol_selection_negative_cluster_count: `{attribution_summary.get('symbol_selection_negative_cluster_count')}`",
            f"- execution_cost_visible_count: `{attribution_summary.get('execution_cost_visible_count')}`",
            f"- structured_proposed_changes: `{trade_attribution.get('proposed_change_count')}`",
            f"- ledger_mutated: `{trade_attribution.get('ledger_mutated')}`",
            "- rule: `read-only attribution; focus items are review hints, not active config changes`",
            "",
            "| Category | Count | Net PnL USD |",
            "|---|---:|---:|",
        ]
    )
    for item in trade_attribution.get("top_failure_categories") or []:
        lines.append(f"| `{item.get('category')}` | `{item.get('count')}` | `{item.get('net_pnl_usd')}` |")
    if not trade_attribution.get("top_failure_categories"):
        lines.append("| - | - | - |")
    lines.extend(["", "### Worst Entry Modes", "", "| Entry Mode | Trades | Win % | Net PnL |", "|---|---:|---:|---:|"])
    for item in trade_attribution.get("worst_entry_modes") or []:
        lines.append(
            f"| `{item.get('paper_entry_mode')}` | `{item.get('trade_count')}` | `{item.get('win_rate_pct')}` | `{item.get('net_pnl_usd')}` |"
        )
    if not trade_attribution.get("worst_entry_modes"):
        lines.append("| - | - | - | - |")
    lines.extend(["", "### Paper-Only Focus", ""])
    for item in trade_attribution.get("proposed_focus") or ["no_new_focus_generated"]:
        lines.append(f"- `{item}`")
    lines.append("")

    lines.extend(
        [
            "## Paper/Testnet Risk Control",
            "",
            f"- status: `{paper_testnet_risk_control.get('status') or 'missing'}`",
            f"- control_contract_status: `{paper_testnet_risk_control.get('control_contract_status') or 'missing'}`",
            f"- risk_decision: `{paper_testnet_risk_control.get('risk_decision') or 'missing'}`",
            f"- size_multiplier: `{paper_testnet_risk_control.get('size_multiplier')}`",
            f"- max_new_entry_notional_usd: `${paper_testnet_risk_control.get('max_new_entry_notional_usd')}`",
            f"- daily_realized_pnl_usd: `${paper_testnet_risk_control.get('daily_realized_pnl_usd')}`",
            f"- daily_realized_pnl_pct: `{paper_testnet_risk_control.get('daily_realized_pnl_pct')}`",
            f"- max_drawdown_pct: `{paper_testnet_risk_control.get('max_drawdown_pct')}`",
            f"- consecutive_loss_streak: `{paper_testnet_risk_control.get('consecutive_loss_streak')}`",
            f"- rollback_status: `{paper_testnet_risk_control.get('rollback_status') or 'missing'}`",
            f"- triggers: `{paper_testnet_risk_control.get('triggers') or []}`",
            f"- report: `{paper_testnet_risk_control.get('report') or '-'}`",
            "- boundary: `paper/testnet only; does not override operational authorization or permit live trading`",
            "",
        ]
    )

    lines.extend(
        [
            "## Active Goal Completion",
            "",
            f"- goal_complete: `{phase.get('goal_complete')}`",
            f"- overall_status: `{phase.get('overall_status') or 'missing'}`",
            f"- max_current_action: `{phase.get('max_current_action') or 'missing'}`",
            f"- current_deployment_authorization: `{phase.get('current_deployment_authorization') or 'unknown'}`",
            f"- strict_blocker_count: `{phase.get('strict_blocker_count')}`",
            f"- objective_status_counts: `{phase.get('objective_status_counts') or {}}`",
            f"- phase_requirement_status_counts: `{phase.get('phase_requirement_status_counts') or {}}`",
            f"- detailed_report: `{phase.get('report') or '-'}`",
            "",
            "| Top Blocking Requirement | Status | Next |",
            "|---|---|---|",
            *[
                f"| {item.get('requirement') or '-'} | `{item.get('status') or 'missing'}` | {item.get('next_step') or '-'} |"
                for item in (phase.get("strict_top_blockers") or [])
            ],
            *([] if phase.get("strict_top_blockers") else ["| - | `proven` | - |"]),
            "",
        ]
    )

    lines.extend(
        [
        "## Phase Gates",
        "",
        f"- phase1_paper_execution_loop: `{phase.get('phase1') or 'unknown'}`",
        f"- phase2_positive_expectancy_proof: `{phase.get('phase2') or 'unknown'}`",
        f"- phase3_monthly_double_pressure_test: `{phase.get('phase3') or 'unknown'}`",
        f"- phase4_real_auto_trading_candidate: `{phase.get('phase4') or 'unknown'}`",
        f"- phase1_gap_count: `{phase.get('phase1_gap_count')}`",
        f"- phase2_blocker_count: `{phase.get('phase2_blocker_count')}`",
        f"- phase3_blocker_count: `{phase.get('phase3_blocker_count')}`",
        f"- phase4_blocker_count: `{phase.get('phase4_blocker_count')}`",
        "",
        "### Layer Evidence Matrix",
        "",
        "| Layer | Status | Next |",
        "|---|---|---|",
        *[
            f"| `{item.get('layer') or '-'}` | `{item.get('status') or '-'}` | {item.get('next_step') or '-'} |"
            for item in (phase.get("layer_status") or [])
        ],
        *([] if phase.get("layer_status") else ["| - | - | - |"]),
        "",
        "### Objective Coverage Gaps",
        "",
        f"- objective_gap_count: `{phase.get('objective_gap_count')}`",
        "",
        "| Requirement | Status | Detail | Next |",
        "|---|---|---|---|",
        *[
            (
                f"| {item.get('requirement') or '-'} | `{item.get('status') or '-'}` | "
                f"{', '.join(f'{key}={value}' for key, value in list((item.get('detail') or {}).items())[:3]) or '-'} | "
                f"{item.get('next_step') or '-'} |"
            )
            for item in (phase.get("objective_top_gaps") or [])
        ],
        *([] if phase.get("objective_top_gaps") else ["| - | proven | - | - |"]),
        "",
        "### Phase 1 Gaps",
        "",
        "| Check | Status | Detail |",
        "|---|---|---|",
        ]
    )
    phase1_rows = phase.get("phase1_top_gaps") or []
    if phase1_rows:
        for item in phase1_rows:
            detail = item.get("detail")
            if isinstance(detail, dict):
                compact_detail = ", ".join(f"{key}={value}" for key, value in list(detail.items())[:3])
            else:
                compact_detail = str(detail or "-")
            lines.append(
                f"| {item.get('name') or '-'} | `{item.get('status') or '-'}` | {compact_detail} |"
            )
    else:
        lines.append("| - | proven | - |")
    lines.extend(
        [
        "",
        "### Phase 2 Top Blockers",
        "",
        "| Blocker | Status | Detail |",
        "|---|---|---|",
        ]
    )
    phase2_rows = phase.get("phase2_top_blockers") or []
    if phase2_rows:
        for item in phase2_rows:
            detail = item.get("detail")
            if isinstance(detail, dict):
                compact_detail = ", ".join(f"{key}={value}" for key, value in list(detail.items())[:3])
            else:
                compact_detail = str(detail or "-")
            lines.append(
                f"| {item.get('name') or '-'} | `{item.get('status') or '-'}` | {compact_detail} |"
            )
    else:
        lines.append("| - | - | - |")
    lines.extend(
        [
        "",
        "### Phase 2 Quality Recovery Board",
        "",
        f"- generated_at: `{phase2_quality.get('created_at') or 'missing'}`",
        f"- quality_state: `{phase2_quality.get('quality_state') or 'missing'}`",
        f"- allowed_new_sample_posture: `{phase2_quality.get('allowed_new_sample_posture') or 'missing'}`",
        f"- closed_count: `{phase2_quality.get('closed_count')}` gap_to_30 `{phase2_quality.get('closed_gap_to_min_30')}`",
        f"- wins/losses: `{phase2_quality.get('wins')}/{phase2_quality.get('losses')}` win_rate `{phase2_quality.get('win_rate_pct')}`",
        f"- realized_pnl: `${phase2_quality.get('realized_pnl_usd')}` closed_net_return `{phase2_quality.get('closed_net_return_pct')}`",
        f"- next_8_all_win_rate: `{phase2_quality.get('next_8_all_win_rate_pct')}`",
        f"- next_8_required_avg_pnl_to_breakeven: `${phase2_quality.get('next_8_required_avg_pnl_to_breakeven_usd')}`",
        f"- win_path_55pct: `{phase2_quality.get('win_path_55pct')}`",
        f"- win_path_60pct: `{phase2_quality.get('win_path_60pct')}`",
        f"- blocked_entry_modes: `{phase2_quality.get('blocked_entry_mode_count')}` blocked_strategy_families `{phase2_quality.get('blocked_strategy_family_count')}`",
        f"- recovery_report: `{phase2_quality.get('report') or 'active-alpha-paper-monitor/reports/PHASE2_QUALITY_RECOVERY_ACTION_BOARD.md'}`",
        "",
        "| Entry Mode | Trades | Win % | Net PnL | Action |",
        "|---|---:|---:|---:|---|",
        *[
            f"| `{item.get('name')}` | `{item.get('trades')}` | `{item.get('win_rate_pct')}` | `{item.get('net_pnl_usd')}` | `{item.get('action')}` |"
            for item in (phase2_quality.get("blocked_entry_modes") or [])[:5]
        ],
        *([] if phase2_quality.get("blocked_entry_modes") else ["| - | - | - | - | - |"]),
        "",
        "| Priority | Recovery Action | Limit | Success Condition |",
        "|---:|---|---|---|",
        *[
            f"| `{item.get('priority')}` | `{item.get('action')}` | `{item.get('limit')}` | {item.get('success_condition')} |"
            for item in (phase2_quality.get("top_actions") or [])[:5]
        ],
        *([] if phase2_quality.get("top_actions") else ["| - | - | - | - |"]),
        "",
        "### Phase 2 Quality Gate Enforcement",
        "",
        f"- generated_at: `{phase2_quality_gate.get('created_at') or 'missing'}`",
        f"- status: `{phase2_quality_gate.get('status') or 'missing'}`",
        f"- checked/enforced/current_blocking/missing: `{phase2_quality_gate.get('checked_count')}` / `{phase2_quality_gate.get('enforced_count')}` / `{phase2_quality_gate.get('current_blocking_count')}` / `{phase2_quality_gate.get('missing_from_recovery_plan_count')}`",
        f"- report: `{phase2_quality_gate.get('report') or 'active-alpha-paper-monitor/reports/PHASE2_QUALITY_GATE_ENFORCEMENT_AUDIT.md'}`",
        f"- safety_flags: live `{phase2_quality_gate.get('live_orders_enabled')}` private `{phase2_quality_gate.get('private_api_used')}` ledger_mutated `{phase2_quality_gate.get('ledger_mutated')}`",
        "",
        "| Entry Mode | Enforcement Status | Current Block Hits |",
        "|---|---|---:|",
        *[
            f"| `{item.get('name')}` | `{item.get('status')}` | `{item.get('hits')}` |"
            for item in (phase2_quality_gate.get("entry_mode_rows") or [])[:5]
        ],
        *([] if phase2_quality_gate.get("entry_mode_rows") else ["| - | - | - |"]),
        "",
        "| Strategy Family | Enforcement Status | Current Block Hits |",
        "|---|---|---:|",
        *[
            f"| `{item.get('name')}` | `{item.get('status')}` | `{item.get('hits')}` |"
            for item in (phase2_quality_gate.get("strategy_family_rows") or [])[:5]
        ],
        *([] if phase2_quality_gate.get("strategy_family_rows") else ["| - | - | - |"]),
        ]
    )
    lines.extend(
        [
        "",
        "### Phase 4 Live Candidate Blockers",
        "",
        "| Blocker | Status | Next |",
        "|---|---|---|",
        ]
    )
    phase4_rows = phase.get("phase4_top_blockers") or []
    if phase4_rows:
        for item in phase4_rows:
            lines.append(
                f"| {item.get('name') or '-'} | `{item.get('status') or '-'}` | {item.get('next_step') or '-'} |"
            )
    else:
        lines.append("| - | - | - |")
    lines.extend(
        [
        "",
        "### Next Best Actions",
        "",
        ]
    )
    next_actions = phase.get("next_best_actions") or []
    if next_actions:
        for item in next_actions[:5]:
            lines.append(f"- {item}")
    else:
        lines.append("- No phase action summary available; rerun `phase_goal_readiness_auditor.py`.")
    lines.extend(
        [
        "",
        "## Phase 3 Pressure Action Board",
        "",
        f"- generated_at: `{phase3_pressure.get('created_at') or 'missing'}`",
        f"- month: `{phase3_pressure.get('month_id') or '-'}`",
        f"- current_vs_target: `${phase3_pressure.get('current_equity_usd')}` / `${phase3_pressure.get('target_equity_usd')}`",
        f"- target_gap_usd: `${phase3_pressure.get('target_gap_usd')}`",
        f"- required_return_from_current: `{phase3_pressure.get('required_return_pct_from_current_equity')}%`",
        f"- closed_count: `{phase3_pressure.get('closed_count')}` gap_to_30 `{phase3_pressure.get('closed_gap_to_phase2_min')}`",
        f"- win_rate_pct: `{phase3_pressure.get('win_rate_pct')}` realized_pnl `${phase3_pressure.get('realized_pnl_usd')}` closed_net_return `{phase3_pressure.get('closed_net_return_pct')}`",
        f"- posture: `{phase3_pressure.get('posture') or 'missing'}`",
        f"- allowed_action: `{phase3_pressure.get('allowed_action') or 'missing'}`",
        f"- capital_policy: `{phase3_pressure.get('capital_policy') or 'missing'}` max_deploy `${phase3_pressure.get('max_deployable_now_usd')}` per_trade `${phase3_pressure.get('per_trade_notional_usd')}`",
        f"- blockers: `{', '.join(phase3_pressure.get('blockers') or []) or '-'}`",
        f"- pressure_report: `{phase3_pressure.get('report') or 'active-alpha-paper-monitor/reports/PHASE3_PRESSURE_ACTION_BOARD.md'}`",
        "",
        "| Priority | Paper Action | Limit | Success Condition |",
        "|---:|---|---|---|",
        *[
            f"| `{item.get('priority')}` | `{item.get('action')}` | `{item.get('limit')}` | {item.get('success_condition')} |"
            for item in (phase3_pressure.get("top_actions") or [])
        ],
        *([] if phase3_pressure.get("top_actions") else ["| - | - | - | - |"]),
        "",
        "## Impulse Capture",
        "",
        f"- captured_at: `{impulse.get('captured_at') or 'unknown'}`",
        f"- freshness_status: `{impulse.get('freshness_status')}` age_hours `{impulse.get('age_hours')}`",
        f"- anchor_state: `{impulse.get('anchor_state') or 'unknown'}`",
        f"- signal_count: `{impulse.get('signal_count')}`",
        "",
        "| Symbol | Stage | Action | Score | 5m Vol x | 5m Buy Ratio | Spread bps |",
        "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    if impulse_rows:
        for item in impulse_rows:
            lines.append(
                f"| `{item.get('symbol')}` | `{item.get('stage')}` | `{item.get('action')}` | "
                f"`{item.get('score')}` | `{item.get('volume_5m')}` | `{item.get('buy_ratio_5m')}` | `{item.get('spread_bps')}` |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - |")

    lines.extend(
        [
        "",
        "## Compounding Proof",
        "",
        f"- overall_status: `{compounding.get('overall_status') or 'unknown'}`",
        f"- max_allowed_action: `{compounding.get('max_allowed_action') or 'unknown'}`",
        f"- monthly_target_complete_now: `{compounding.get('monthly_target_complete_now')}`",
        f"- repeatable_compounding_path_proven: `{compounding.get('repeatable_compounding_path_proven')}`",
        f"- blockers: `{compounding.get('blockers') or []}`",
        "",
        "## Current Operator Read",
        "",
        f"- state: `{current_operator_state}`",
        f"- current_action: `{current_operator_action}`",
        f"- why_no_trade_or_no_scale: `{reason_counts or compounding.get('blockers') or 'none'}`",
        f"- current_signal_research_state: `{latest_current_signal_state}`",
        f"- current_signal_freshness: `{current_signal_artifact.get('freshness_status')}` age_hours `{current_signal_artifact.get('age_hours')}` generated_at `{current_signal_artifact.get('generated_at')}`",
        f"- {'current_signal_top_symbols' if latest_current_signal_state == 'latest_artifact_usable_and_fresh' else 'historical_cached_signal_symbols'}: `{', '.join(current_signal_artifact.get('top_symbols') or []) or '-'}`",
        f"- research_reproducibility: `{kline_research_repro.get('status')}` promotion_allowed `{kline_research_repro.get('promotion_allowed_count')}` max_action `{kline_research_repro.get('max_allowed_action')}`",
        f"- proposed_strategy_changes: `{proposed_change_ids}`",
        "",
        "### Current Three-Segment Paper Retest Ticket",
        "",
        f"- status: `{current_signal_retest.get('status')}` dry_run `{current_signal_retest.get('dry_run')}` persisted `{current_signal_retest.get('dry_run_artifact_persisted')}` ledger_mutated `{current_signal_retest.get('ledger_mutated')}`",
        f"- candidate: `{current_signal_retest.get('symbol') or '-'}` `{current_signal_retest.get('interval') or '-'}` mode `{current_signal_retest.get('sample_mode') or '-'}` planned `${current_signal_retest.get('planned_notional_usd') or 0}`",
        f"- block_reason: `{current_signal_retest.get('block_reason') or '-'}` robustness `{current_signal_retest.get('robustness_gate') or {}}`",
        f"- risk_plan: stop `{current_signal_retest.get('stop_pct')}` take `{current_signal_retest.get('take_pct')}` max_holding `{current_signal_retest.get('max_holding') or '-'}`",
        f"- train: `{current_signal_retest.get('train_trade_count')}` trades, win `{current_signal_retest.get('train_win_rate_pct')}`%, net `{current_signal_retest.get('train_net_return_pct')}`%",
        f"- validation: `{current_signal_retest.get('validation_trade_count')}` trades, win `{current_signal_retest.get('validation_win_rate_pct')}`%, net `{current_signal_retest.get('validation_net_return_pct')}`%",
        f"- final_holdout: `{current_signal_retest.get('holdout_trade_count')}` trades, win `{current_signal_retest.get('holdout_win_rate_pct')}`%, net `{current_signal_retest.get('holdout_net_return_pct')}`%",
        f"- probability_note: `{current_signal_retest.get('probability_note')}`",
        f"- execution_state: `artifact_only_no_position_opened_when_dry_run_or_automation_missing`",
        f"- strategy_backlog_report: `active-alpha-paper-monitor/reports/STRATEGY_ITERATION_BACKLOG.md`",
        f"- {'current_watch_symbols_from_impulse' if impulse.get('freshness_status') == 'fresh' else 'historical_cached_impulse_symbols'}: `{top_impulse_symbols}` freshness `{impulse.get('freshness_status')}` age_hours `{impulse.get('age_hours')}`",
        f"- next_loop_expectation: `{next_loop_expectation}`",
        f"- dashboard_contract: `this file is the single latest operator input; reports/experiments are historical audit artifacts`",
        f"- artifact_cleanup_mode: `{artifact_inventory.get('policy') or 'index_only_no_delete_no_move'}`",
        f"- artifact_counts: `reports={artifact_inventory.get('reports_count')}, experiments={artifact_inventory.get('experiments_count')}, handoffs={artifact_inventory.get('handoffs_count')}, paper_trades={artifact_inventory.get('paper_trades_count')}`",
        "",
        "## Latest Artifacts",
        "",
        f"- latest_runner_experiment: `{rel(latest.get('runner_experiment'))}`",
        f"- latest_runner_report: `{runner.get('report') or rel(latest.get('runner_report'))}`",
        f"- latest_fast_report: `{rel(latest.get('fast_report'))}`",
        f"- latest_preflight_report: `{rel(latest.get('preflight_report'))}`",
        f"- latest_phase_report: `{phase.get('report') or rel(latest.get('phase_report'))}`",
        f"- latest_phase2_quality_report: `{phase2_quality.get('report') or rel(latest.get('phase2_quality_report'))}`",
        f"- latest_phase2_quality_gate_report: `{phase2_quality_gate.get('report') or rel(latest.get('phase2_quality_gate_report'))}`",
        f"- latest_impulse_report: `{impulse.get('report') or rel(latest.get('impulse_report'))}`",
        f"- latest_compounding_report: `{compounding.get('report') or rel(latest.get('compounding_report'))}`",
        f"- latest_current_signal_artifact: `{current_signal_artifact.get('path')}`",
        f"- latest_current_signal_retest_report: `{current_signal_retest.get('report') or rel(latest.get('current_signal_retest_report'))}`",
        f"- latest_current_signal_robustness_report: `{rel(latest.get('current_signal_robustness_report'))}`",
        f"- latest_recovery_watchlist_report: `{rel(latest.get('recovery_watchlist_report'))}`",
        f"- latest_recovery_sampler_report: `{rel(latest.get('recovery_sampler_report'))}`",
        f"- latest_blocked_retest_report: `{rel(latest.get('blocked_retest_report'))}`",
        f"- latest_blocked_retest_sampler_report: `{rel(latest.get('blocked_retest_sampler_report'))}`",
        f"- latest_trade_attribution_report: `{rel(latest.get('trade_attribution_report'))}`",
        f"- latest_capital_allocation_report: `{rel(latest.get('capital_allocation_report'))}`",
        f"- latest_ledger_integrity_report: `{ledger_integrity.get('report') or rel(latest.get('ledger_integrity_report'))}`",
        f"- latest_binance_market_data_health_report: `{binance_health.get('report') or rel(latest.get('binance_market_data_health_report'))}`",
        f"- latest_binance_kline_cache_experiment: `{binance_kline_cache.get('experiment') or rel(latest.get('binance_kline_cache_experiment'))}`",
        f"- kline_research_reproducibility_report: `{kline_research_repro.get('report') or 'active-alpha-paper-monitor/reports/KLINE_RESEARCH_REPRODUCIBILITY_AUDIT.md'}`",
        f"- latest_paper_signal_contract_report: `{signal_contract.get('report') or rel(latest.get('paper_signal_contract_report'))}`",
        f"- latest_handoff: `{rel(latest.get('handoff'))}`",
        f"- artifact_index: `active-alpha-paper-monitor/reports/AUTOMATION_ARTIFACT_INDEX.md`",
        "",
        "## Operator View",
        "",
        f"- 自动化设计最多允许一个小时级 paper 入口；当前状态是 `{automation.get('status')}`，历史 reports/experiments 是审计证据，不是新增定时任务。",
        "- 日常先看本文件；需要细节时再打开 latest_runner_report 或 latest_runner_experiment。",
        "- 若本文件长时间未更新，优先检查 automation status、runner lock、preflight decision 和网络数据源。",
        "",
        ]
    )
    return "\n".join(lines)


def build_payload() -> dict[str, Any]:
    ledger = read_json(LEDGER_PATH, {})
    config_payload = read_json(CONFIG_PATH, {})
    runner_exp = latest_file("experiments/*validation-progress-runner.json")
    phase_exp = GOAL_COMPLETION_AUDIT_JSON if GOAL_COMPLETION_AUDIT_JSON.exists() else latest_file("experiments/*phase-goal-readiness-audit.json")
    phase3_pressure_exp = latest_file("experiments/*phase3-pressure-action-board.json")
    impulse_exp = latest_file("experiments/*impulse-capture.json")
    compounding_exp = latest_file("experiments/*binance-api-compounding-flow-audit.json")
    current_signal_exp = latest_file("experiments/*current-signal-probe.json")
    current_signal_retest_exp = latest_file("experiments/*current-signal-near-miss-sampler.json")
    recovery_watchlist_exp = latest_file("experiments/*recovery-watchlist-monitor.json")
    recovery_sampler_exp = latest_file("experiments/*recovery-watchlist-paper-sampler.json")
    blocked_retest_exp = latest_file("experiments/*top-blocked-candidate-retest-lab.json")
    blocked_retest_sampler_exp = latest_file("experiments/*top-blocked-retest-quality-scout-sampler.json")
    trade_attribution_exp = latest_file("experiments/*paper-trade-attribution-report.json")
    capital_allocation_exp = latest_file("experiments/*paper-capital-allocation-audit.json")
    ledger_integrity_exp = latest_file("experiments/*paper-ledger-integrity-audit.json")
    binance_market_data_exp = latest_file("experiments/*binance-market-data-health.json")
    binance_kline_cache_exp = latest_file("experiments/*binance-kline-cache-builder.json")
    paper_signal_contract_exp = latest_file("experiments/*paper-signal-contract-audit.json")
    paper_market_context_exp = latest_file("experiments/*paper-market-context-audit.json")
    preflight_exp = latest_file("experiments/*resume-preflight.json")
    pipeline_freshness_exp = latest_file("experiments/*pipeline-freshness-audit.json")
    pipeline_repair_exp = latest_file("experiments/*pipeline-freshness-repair-runner.json")
    paper_auto_evolver_exp = latest_file("experiments/*paper-strategy-auto-evolver.json")
    paper_testnet_risk_control_payload = read_json(PAPER_TESTNET_RISK_CONTROL_JSON, {})
    artifact_index_payload = read_json(ARTIFACT_INDEX_JSON, {})
    automation_recovery_payload = read_json(AUTOMATION_RECOVERY_PLAN_JSON, {})
    kline_research_repro_payload = read_json(KLINE_RESEARCH_REPRO_JSON, {})
    strategy_backlog_payload = read_json(STRATEGY_BACKLOG_JSON, {})
    paper_strategy_overlay_payload = read_json(PAPER_STRATEGY_OVERLAY_JSON, {})
    strategy_decision_payload = build_inline_strategy_decision_payload(read_json(STRATEGY_PROPOSAL_DECISION_JSON, {}))
    strategy_enforcement_payload = build_inline_strategy_enforcement_payload(
        read_json(STRATEGY_PROPOSAL_ENFORCEMENT_JSON, {})
    )
    runner_report = latest_file("reports/*validation-progress-runner*.md")
    runner_payload = read_json(runner_exp, {}) if runner_exp else {}
    phase_payload = read_json(phase_exp, {}) if phase_exp else {}
    phase2_quality_payload = read_json(PHASE2_QUALITY_JSON, {})
    phase2_quality_gate_payload = read_json(PHASE2_QUALITY_GATE_JSON, {})
    phase3_pressure_payload = read_json(phase3_pressure_exp, {}) if phase3_pressure_exp else {}
    impulse_payload = read_json(impulse_exp, {}) if impulse_exp else {}
    compounding_payload = read_json(compounding_exp, {}) if compounding_exp else {}
    current_signal_payload = read_json(current_signal_exp, {}) if current_signal_exp else {}
    current_signal_retest_payload = read_json(current_signal_retest_exp, {}) if current_signal_retest_exp else {}
    recovery_watchlist_payload = read_json(recovery_watchlist_exp, {}) if recovery_watchlist_exp else {}
    recovery_sampler_payload = read_json(recovery_sampler_exp, {}) if recovery_sampler_exp else {}
    blocked_retest_payload = read_json(blocked_retest_exp, {}) if blocked_retest_exp else {}
    blocked_retest_sampler_payload = read_json(blocked_retest_sampler_exp, {}) if blocked_retest_sampler_exp else {}
    trade_attribution_payload = read_json(trade_attribution_exp, {}) if trade_attribution_exp else {}
    capital_allocation_payload = read_json(capital_allocation_exp, {}) if capital_allocation_exp else {}
    ledger_integrity_payload = read_json(ledger_integrity_exp, {}) if ledger_integrity_exp else {}
    binance_market_data_payload = read_json(binance_market_data_exp, {}) if binance_market_data_exp else {}
    binance_kline_cache_payload = read_json(binance_kline_cache_exp, {}) if binance_kline_cache_exp else {}
    paper_signal_contract_payload = read_json(paper_signal_contract_exp, {}) if paper_signal_contract_exp else {}
    paper_market_context_payload = read_json(paper_market_context_exp, {}) if paper_market_context_exp else {}
    preflight_payload = read_json(preflight_exp, {}) if preflight_exp else {}
    pipeline_freshness_payload = inline_pipeline_freshness(
        runner_exp,
        runner_payload if isinstance(runner_payload, dict) else {},
        recovery_watchlist_exp,
        recovery_watchlist_payload if isinstance(recovery_watchlist_payload, dict) else {},
        recovery_sampler_exp,
        recovery_sampler_payload if isinstance(recovery_sampler_payload, dict) else {},
        blocked_retest_exp,
        blocked_retest_payload if isinstance(blocked_retest_payload, dict) else {},
        blocked_retest_sampler_exp,
        blocked_retest_sampler_payload if isinstance(blocked_retest_sampler_payload, dict) else {},
        capital_allocation_exp,
        capital_allocation_payload if isinstance(capital_allocation_payload, dict) else {},
    )
    pipeline_repair_payload = read_json(pipeline_repair_exp, {}) if pipeline_repair_exp else {}
    paper_auto_evolver_payload = read_json(paper_auto_evolver_exp, {}) if paper_auto_evolver_exp else {}
    runner_signal_payload = runner_payload if isinstance(runner_payload, dict) else {}
    runner_signal_path = runner_exp
    runner_signal_source = "latest_runner"
    latest_candidate_runner = (
        pipeline_freshness_payload.get("latest_candidate_runner")
        if isinstance(pipeline_freshness_payload.get("latest_candidate_runner"), dict)
        else {}
    )
    candidate_count = latest_candidate_runner.get("candidate_count") or 0
    candidate_path = workspace_path(latest_candidate_runner.get("artifact"))
    if candidate_count and candidate_path and candidate_path.exists():
        candidate_payload = read_json(candidate_path, {})
        if isinstance(candidate_payload, dict):
            runner_signal_payload = candidate_payload
            runner_signal_path = candidate_path
            runner_signal_source = "latest_candidate_runner"
    latest_runner_summary = runner_summary(runner_signal_payload)
    latest_runner_summary["signal_source"] = runner_signal_source
    latest_runner_summary["source_artifact"] = rel(runner_signal_path)
    latest_runner_summary["latest_maintenance_runner_artifact"] = rel(runner_exp)
    return {
        "generated_at": now_local().isoformat(),
        "ledger": ledger_summary(ledger if isinstance(ledger, dict) else {}),
        "automation": parse_automation(),
        "automation_recovery": automation_recovery_summary(
            automation_recovery_payload if isinstance(automation_recovery_payload, dict) else {}
        ),
        "latest_runner": latest_runner_summary,
        "latest_phase": latest_phase_summary(phase_payload if isinstance(phase_payload, dict) else {}),
        "paper_testnet_risk_control": paper_testnet_risk_control_summary(
            paper_testnet_risk_control_payload if isinstance(paper_testnet_risk_control_payload, dict) else {}
        ),
        "phase2_quality": phase2_quality_summary(
            phase2_quality_payload if isinstance(phase2_quality_payload, dict) else {}
        ),
        "phase2_quality_gate": phase2_quality_gate_summary(
            phase2_quality_gate_payload if isinstance(phase2_quality_gate_payload, dict) else {}
        ),
        "phase3_pressure": phase3_pressure_summary(
            phase3_pressure_payload if isinstance(phase3_pressure_payload, dict) else {}
        ),
        "latest_impulse": latest_impulse_summary(impulse_payload if isinstance(impulse_payload, dict) else {}),
        "latest_compounding": latest_compounding_summary(compounding_payload if isinstance(compounding_payload, dict) else {}),
        "latest_current_signal_artifact": current_signal_artifact_summary(
            current_signal_exp,
            current_signal_payload if isinstance(current_signal_payload, dict) else {},
        ),
        "latest_current_signal_retest": current_signal_retest_summary(
            current_signal_retest_exp,
            current_signal_retest_payload if isinstance(current_signal_retest_payload, dict) else {},
        ),
        "artifact_inventory": artifact_inventory_summary(
            artifact_index_payload if isinstance(artifact_index_payload, dict) else {}
        ),
        "strategy_backlog": strategy_backlog_summary(
            strategy_backlog_payload if isinstance(strategy_backlog_payload, dict) else {}
        ),
        "paper_auto_learning": paper_auto_learning_summary(
            paper_strategy_overlay_payload if isinstance(paper_strategy_overlay_payload, dict) else {},
            paper_auto_evolver_payload if isinstance(paper_auto_evolver_payload, dict) else {},
        ),
        "strategy_proposal_decision": strategy_proposal_decision_summary(
            strategy_decision_payload if isinstance(strategy_decision_payload, dict) else {}
        ),
        "strategy_proposal_enforcement": strategy_proposal_enforcement_summary(
            strategy_enforcement_payload if isinstance(strategy_enforcement_payload, dict) else {}
        ),
        "recovery_watchlist": recovery_watchlist_summary(
            recovery_watchlist_payload if isinstance(recovery_watchlist_payload, dict) else {}
        ),
        "recovery_sampler": recovery_sampler_summary(
            recovery_sampler_payload if isinstance(recovery_sampler_payload, dict) else {}
        ),
        "blocked_retest": blocked_retest_summary(
            blocked_retest_payload if isinstance(blocked_retest_payload, dict) else {}
        ),
        "blocked_retest_sampler": blocked_retest_sampler_summary(
            blocked_retest_sampler_payload if isinstance(blocked_retest_sampler_payload, dict) else {}
        ),
        "trade_attribution": trade_attribution_summary(
            trade_attribution_payload if isinstance(trade_attribution_payload, dict) else {}
        ),
        "capital_allocation": capital_allocation_summary(
            capital_allocation_payload if isinstance(capital_allocation_payload, dict) else {}
        ),
        "ledger_integrity": ledger_integrity_summary(
            ledger_integrity_payload if isinstance(ledger_integrity_payload, dict) else {}
        ),
        "binance_market_data_health": binance_market_data_summary(
            binance_market_data_payload if isinstance(binance_market_data_payload, dict) else {}
        ),
        "binance_kline_cache": binance_kline_cache_summary(
            binance_kline_cache_payload if isinstance(binance_kline_cache_payload, dict) else {},
            config_payload if isinstance(config_payload, dict) else {},
        ),
        "kline_research_reproducibility": kline_research_reproducibility_summary(
            kline_research_repro_payload if isinstance(kline_research_repro_payload, dict) else {}
        ),
        "paper_signal_contract": paper_signal_contract_summary(
            paper_signal_contract_payload if isinstance(paper_signal_contract_payload, dict) else {}
        ),
        "paper_market_context": paper_market_context_summary(
            paper_market_context_payload if isinstance(paper_market_context_payload, dict) else {}
        ),
        "latest_preflight": preflight_summary(
            preflight_payload if isinstance(preflight_payload, dict) else {}
        ),
        "pipeline_freshness": pipeline_freshness_summary(
            pipeline_freshness_payload if isinstance(pipeline_freshness_payload, dict) else {}
        ),
        "pipeline_repair": pipeline_repair_summary(
            pipeline_repair_payload if isinstance(pipeline_repair_payload, dict) else {}
        ),
        "latest_files": {
            "runner_experiment": runner_exp,
            "runner_report": runner_report,
            "fast_report": latest_file("reports/*fast-crypto-paper*.md"),
            "preflight_report": latest_file("reports/*resume-preflight*.md"),
            "preflight_experiment": preflight_exp,
            "pipeline_freshness_experiment": pipeline_freshness_exp,
            "pipeline_freshness_report": latest_file("reports/*pipeline-freshness-audit*.md"),
            "pipeline_repair_experiment": pipeline_repair_exp,
            "pipeline_repair_report": latest_file("reports/*pipeline-freshness-repair*.md"),
            "phase_report": latest_file("reports/*phase-goal-readiness*.md"),
            "phase2_quality_experiment": PHASE2_QUALITY_JSON if PHASE2_QUALITY_JSON.exists() else None,
            "phase2_quality_report": latest_file("reports/PHASE2_QUALITY_RECOVERY_ACTION_BOARD.md"),
            "phase2_quality_gate_experiment": PHASE2_QUALITY_GATE_JSON if PHASE2_QUALITY_GATE_JSON.exists() else None,
            "phase2_quality_gate_report": latest_file("reports/PHASE2_QUALITY_GATE_ENFORCEMENT_AUDIT.md"),
            "phase3_pressure_experiment": phase3_pressure_exp,
            "phase3_pressure_report": latest_file("reports/*phase3-pressure-action-board*.md"),
            "impulse_report": latest_file("reports/*impulse-capture*.md"),
            "compounding_report": latest_file("reports/*binance-api-compounding-flow-audit*.md"),
            "current_signal_experiment": current_signal_exp,
            "current_signal_retest_experiment": current_signal_retest_exp,
            "current_signal_retest_report": latest_file("reports/*current-signal-near-miss-sampler*.md"),
            "current_signal_robustness_report": ACTIVE_ROOT / "reports" / "CURRENT_SIGNAL_ROBUSTNESS_AUDIT.md",
            "recovery_watchlist_experiment": recovery_watchlist_exp,
            "recovery_watchlist_report": latest_file("reports/*recovery-watchlist*.md"),
            "recovery_sampler_experiment": recovery_sampler_exp,
            "recovery_sampler_report": latest_file("reports/*recovery-watchlist-paper-sampler*.md"),
            "blocked_retest_experiment": blocked_retest_exp,
            "blocked_retest_report": latest_file("reports/*top-blocked-candidate-retest*.md"),
            "blocked_retest_sampler_experiment": blocked_retest_sampler_exp,
            "blocked_retest_sampler_report": latest_file("reports/*top-blocked-retest-quality-scout-sampler*.md"),
            "trade_attribution_experiment": trade_attribution_exp,
            "trade_attribution_report": latest_file("reports/*paper-trade-attribution*.md"),
            "capital_allocation_experiment": capital_allocation_exp,
            "capital_allocation_report": latest_file("reports/*paper-capital-allocation*.md"),
            "ledger_integrity_experiment": ledger_integrity_exp,
            "ledger_integrity_report": latest_file("reports/*paper-ledger-integrity-audit*.md"),
            "binance_market_data_health_experiment": binance_market_data_exp,
            "binance_market_data_health_report": latest_file("reports/*binance-market-data-health*.md"),
            "binance_kline_cache_experiment": binance_kline_cache_exp,
            "paper_signal_contract_experiment": paper_signal_contract_exp,
            "paper_signal_contract_report": latest_file("reports/*paper-signal-contract-audit*.md"),
            "paper_market_context_experiment": paper_market_context_exp,
            "paper_market_context_report": latest_file("reports/*paper-market-context-audit*.md"),
            "strategy_proposal_enforcement_report": latest_file("reports/STRATEGY_PROPOSAL_ENFORCEMENT_AUDIT.md"),
            "handoff": latest_file("handoffs/*"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Write the latest active-alpha automation dashboard.")
    parser.add_argument("--output", default=str(LATEST_DASHBOARD))
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()

    payload = build_payload()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_dashboard(payload), encoding="utf-8")
    if args.compact_output:
        print(json.dumps({"status": "ok", "dashboard": rel(output), "generated_at": payload["generated_at"]}, ensure_ascii=False))
    else:
        print(f"Wrote {rel(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
