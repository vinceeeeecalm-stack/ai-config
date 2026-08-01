#!/usr/bin/env python3
"""Read-only paper capital allocation audit.

This script explains how much of the Binance-only paper cash can be deployed
right now, and why. It does not fetch market data, mutate the paper ledger,
open positions, update strategy config, or authorize live trading.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
LEDGER_PATH = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
BACKLOG_JSON = ACTIVE_ROOT / "experiments" / "strategy-iteration-backlog.json"
AUTOMATION_PATH = Path("/Users/vincentpan/.codex/automations/active-alpha-hourly-crypto-paper-loop/automation.toml")
REPORTS_DIR = ACTIVE_ROOT / "reports"
EXPERIMENTS_DIR = ACTIVE_ROOT / "experiments"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")

import sys

sys.path.insert(0, str(SCRIPT_DIR))
from validation_sample_auditor import build_audit  # noqa: E402
from kline_cache_storage import inspect_kline_cache_storage  # noqa: E402
from pipeline_freshness_auditor import artifact_age_hours, build_record as build_pipeline_freshness  # noqa: E402


def now_local() -> dt.datetime:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0)


def rel(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path | None, default: Any = None) -> Any:
    if not path or not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def latest_file(pattern: str) -> Path | None:
    paths = list(ACTIVE_ROOT.glob(pattern))
    if not paths:
        return None
    return max(paths, key=lambda item: item.stat().st_mtime)


def automation_status(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    text = path.read_text(encoding="utf-8")
    marker = 'status = "'
    start = text.find(marker)
    if start < 0:
        return "UNKNOWN"
    start += len(marker)
    end = text.find('"', start)
    return text[start:end].strip().upper() if end >= 0 else "UNKNOWN"


def operational_authorization_snapshot(automation_path: Path = AUTOMATION_PATH) -> dict[str, Any]:
    pipeline = build_pipeline_freshness()
    kline_path = latest_file("experiments/*binance-kline-cache-builder.json")
    kline_payload = read_json(kline_path, {}) if kline_path else {}
    storage = inspect_kline_cache_storage(kline_payload if isinstance(kline_payload, dict) else {})
    completed_at = (
        kline_payload.get("completed_at")
        or kline_payload.get("generated_at")
        or kline_payload.get("created_at")
    ) if isinstance(kline_payload, dict) else None
    kline_age_hours = artifact_age_hours(completed_at)
    kline_fresh = kline_age_hours is not None and kline_age_hours <= 6.0
    current_automation_status = automation_status(automation_path)
    blockers: list[str] = []
    if current_automation_status != "ACTIVE":
        blockers.append(f"automation_not_active:{current_automation_status.lower()}")
    if pipeline.get("current_market_readiness_status") != "fresh":
        blockers.append("pipeline_market_data_not_fresh")
    if storage.get("replay_available") is not True:
        blockers.append("durable_kline_cache_not_replayable")
    if not kline_fresh:
        blockers.append("durable_kline_cache_not_fresh")
    return {
        "authorization_status": "authorized" if not blockers else "blocked",
        "blockers": blockers,
        "automation": {
            "path": str(automation_path),
            "status": current_automation_status,
        },
        "pipeline": {
            "status": pipeline.get("status"),
            "artifact_sync_status": pipeline.get("artifact_sync_status"),
            "current_market_readiness_status": pipeline.get("current_market_readiness_status"),
            "latest_runner": pipeline.get("latest_runner") or {},
        },
        "kline_cache": {
            "artifact": rel(kline_path),
            "completed_at": completed_at,
            "age_hours": kline_age_hours,
            "fresh": kline_fresh,
            **storage,
        },
    }


def normalize_operational_state(
    payload: dict[str, Any] | None,
    automation_path: Path = AUTOMATION_PATH,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        return operational_authorization_snapshot(automation_path)
    if payload.get("authorization_status") in {"authorized", "blocked"}:
        return payload

    # Accept resume_preflight output directly. This keeps the CLI composable and
    # prevents a blocked deployment from appearing with an empty reason list.
    if isinstance(payload.get("readiness"), dict) or isinstance(payload.get("safety"), dict):
        readiness = payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {}
        safety = payload.get("safety") if isinstance(payload.get("safety"), dict) else {}
        kline = payload.get("kline_cache") if isinstance(payload.get("kline_cache"), dict) else {}
        blockers: list[str] = []
        for item in safety.get("errors") or []:
            blockers.append(f"preflight_safety:{item}")
        readiness_status = str(readiness.get("status") or "unknown")
        if readiness.get("can_resume_hourly_new_sampling") is not True:
            blockers.append(f"preflight_readiness:{readiness_status}")
        nested_automation = payload.get("automation") if isinstance(payload.get("automation"), dict) else {}
        automation_status_value = str(
            payload.get("automation_status") or nested_automation.get("status") or "unknown"
        ).lower()
        if automation_status_value != "active":
            blockers.append(f"automation_not_active:{automation_status_value}")
        if kline.get("replay_available") is not True:
            blockers.append("durable_kline_cache_not_replayable")
        if kline.get("freshness_status") != "fresh" or kline.get("current_signal_usable") is not True:
            blockers.append("durable_kline_cache_not_fresh")
        for item in readiness.get("resume_requirements") or []:
            marker = f"preflight_requirement:{item}"
            if marker not in blockers:
                blockers.append(marker)
        blockers = list(dict.fromkeys(blockers))
        return {
            "authorization_status": "authorized" if not blockers else "blocked",
            "blockers": blockers,
            "source": "resume_preflight",
            "preflight_run_id": payload.get("run_id"),
            "automation": {
                "status": automation_status_value.upper(),
                "contract_status": payload.get("automation_contract") or (nested_automation.get("contract") or {}).get("status"),
            },
            "pipeline": {
                "current_market_readiness_status": "unknown_from_preflight",
            },
            "kline_cache": kline,
        }
    return {
        "authorization_status": "blocked",
        "blockers": ["unrecognized_operational_authorization_payload"],
        "source": "unrecognized_payload",
    }


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if parsed == parsed and abs(parsed) != float("inf") else default
    except Exception:
        return default


def active_backlog_items(backlog: dict[str, Any]) -> list[dict[str, Any]]:
    items = backlog.get("items") if isinstance(backlog.get("items"), dict) else {}
    return [
        item
        for item in items.values()
        if isinstance(item, dict) and item.get("currently_present")
    ]


def sampler_summary() -> dict[str, Any]:
    recovery_path = latest_file("experiments/*recovery-watchlist-paper-sampler.json")
    blocked_path = latest_file("experiments/*top-blocked-retest-quality-scout-sampler.json")
    recovery = read_json(recovery_path, {}) if recovery_path else {}
    blocked = read_json(blocked_path, {}) if blocked_path else {}
    return {
        "recovery_sampler": {
            "path": rel(recovery_path),
            "status": recovery.get("status") if isinstance(recovery, dict) else None,
            "opened_count": recovery.get("opened_count") if isinstance(recovery, dict) else None,
            "blocked_count": recovery.get("blocked_count") if isinstance(recovery, dict) else None,
            "decisions": (recovery.get("decisions") or [])[:5] if isinstance(recovery, dict) else [],
        },
        "blocked_retest_sampler": {
            "path": rel(blocked_path),
            "status": blocked.get("status") if isinstance(blocked, dict) else None,
            "opened_count": blocked.get("opened_count") if isinstance(blocked, dict) else None,
            "blocked_count": blocked.get("blocked_count") if isinstance(blocked, dict) else None,
            "decisions": (blocked.get("decisions") or [])[:5] if isinstance(blocked, dict) else [],
        },
    }


def quality_snapshot(audit: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    paper = audit.get("paper_sample_metrics") if isinstance(audit.get("paper_sample_metrics"), dict) else {}
    portfolio = audit.get("current_portfolio_metrics") if isinstance(audit.get("current_portfolio_metrics"), dict) else {}
    closed = ledger.get("closed_trades") if isinstance(ledger.get("closed_trades"), list) else []
    wins = [item for item in closed if as_float(item.get("realized_pnl_usd")) > 0]
    realized = sum(as_float(item.get("realized_pnl_usd")) for item in closed)
    closed_count = int(paper.get("closed_count") or len(closed))
    win_rate = as_float(paper.get("win_rate_pct"), (len(wins) / closed_count * 100.0) if closed_count else 0.0)
    return {
        "closed_count": closed_count,
        "win_rate_pct": round(win_rate, 6),
        "realized_pnl_usd": round(as_float(paper.get("realized_pnl_usd"), realized), 6),
        "realized_net_return_on_closed_notional_pct": paper.get("realized_net_return_on_closed_notional_pct"),
        "net_return_pct": portfolio.get("net_return_pct", ledger.get("net_return_pct")),
        "max_drawdown_pct": portfolio.get("max_drawdown_pct", ledger.get("max_drawdown_pct")),
    }


def phase2_quality_state(quality: dict[str, Any]) -> dict[str, Any]:
    closed = int(quality.get("closed_count") or 0)
    win_rate = as_float(quality.get("win_rate_pct"))
    pnl = as_float(quality.get("realized_pnl_usd"))
    drawdown = as_float(quality.get("max_drawdown_pct"))
    checks = [
        {"name": "closed_count_30_to_50", "pass": closed >= 30, "value": closed, "required": ">=30"},
        {"name": "win_rate_55_to_60", "pass": win_rate >= 55.0, "value": win_rate, "required": ">=55"},
        {"name": "realized_pnl_positive", "pass": pnl > 0.0, "value": pnl, "required": ">0"},
        {"name": "drawdown_within_15", "pass": drawdown >= -15.0, "value": drawdown, "required": ">=-15"},
    ]
    return {
        "status": "passed" if all(item["pass"] for item in checks) else "not_passed",
        "checks": checks,
        "failed_checks": [item["name"] for item in checks if not item["pass"]],
    }


def build_allocation_record(
    *,
    ledger: dict[str, Any],
    audit: dict[str, Any],
    backlog: dict[str, Any],
    args: argparse.Namespace,
    operational_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created = now_local()
    cash = as_float(ledger.get("cash_usd"))
    open_value = as_float(ledger.get("open_value_usd"))
    equity = as_float(ledger.get("equity_usd"), cash + open_value)
    open_positions = ledger.get("open_positions") if isinstance(ledger.get("open_positions"), list) else []
    cash_ratio = (cash / equity * 100.0) if equity > 0 else 0.0
    quality = quality_snapshot(audit, ledger)
    phase2 = phase2_quality_state(quality)
    capacity = audit.get("validation_capacity_state") if isinstance(audit.get("validation_capacity_state"), dict) else {}
    monthly = (
        (audit.get("validation_recovery_plan") or {}).get("monthly_target")
        if isinstance(audit.get("validation_recovery_plan"), dict)
        else {}
    )
    active_items = active_backlog_items(backlog)
    active_sources = (backlog.get("summary") or {}).get("active_sources") if isinstance(backlog.get("summary"), dict) else {}
    sampler = sampler_summary()
    operational = operational_state if isinstance(operational_state, dict) else {
        "authorization_status": "blocked",
        "blockers": ["operational_authorization_state_missing"],
    }

    safety_reasons: list[str] = []
    if ledger.get("live_orders_enabled") is not False:
        safety_reasons.append("ledger_live_orders_flag_not_false")
    if ledger.get("private_api_used") is not False:
        safety_reasons.append("ledger_private_api_flag_not_false")
    if equity <= 0:
        safety_reasons.append("equity_unavailable_or_nonpositive")

    quality_reasons = list(phase2["failed_checks"])
    if len(active_items) >= 3:
        quality_reasons.append("active_strategy_backlog_requires_review")
    if capacity.get("sample_action") in {"pause_new_samples", "hold_new_samples_temporarily"}:
        quality_reasons.append(f"validation_capacity_{capacity.get('sample_action')}")

    scout_budget = float(args.min_scout_notional_usd)
    reserve = max(float(args.min_cash_reserve_usd), equity * float(args.reserve_pct_of_equity) / 100.0)
    available_after_reserve = max(0.0, cash - reserve)
    open_slots = max(0, int(args.max_open_positions) - len(open_positions))

    if safety_reasons:
        policy = "block_all_paper_deployment"
        max_new_positions = 0
        per_trade_notional = 0.0
        deployable_now = 0.0
        target_cash_ratio = 100.0
    elif phase2["status"] != "passed":
        policy = "minimum_quality_scout_only"
        max_new_positions = min(1, open_slots)
        per_trade_notional = scout_budget if available_after_reserve >= scout_budget and open_slots > 0 else 0.0
        deployable_now = per_trade_notional
        target_cash_ratio = max(85.0, 100.0 - (deployable_now / equity * 100.0 if equity > 0 else 0.0))
    else:
        policy = "scaled_paper_sampling_allowed"
        max_new_positions = min(3, open_slots)
        per_trade_notional = min(
            float(args.scaled_trade_notional_usd),
            max(0.0, equity * float(args.max_single_position_pct_of_equity) / 100.0),
            available_after_reserve,
        )
        deployable_now = min(available_after_reserve, per_trade_notional * max_new_positions)
        target_cash_ratio = max(40.0, 100.0 - (deployable_now / equity * 100.0 if equity > 0 else 0.0))

    if per_trade_notional < scout_budget and not safety_reasons:
        quality_reasons.append("insufficient_cash_after_reserve_for_min_scout")

    research_max_new_positions = max_new_positions
    research_per_trade_notional = per_trade_notional
    research_deployable = deployable_now
    research_target_cash_ratio = target_cash_ratio
    operational_blockers = [str(item) for item in (operational.get("blockers") or [])]
    current_authorized = not safety_reasons and operational.get("authorization_status") == "authorized"
    if not current_authorized:
        max_new_positions = 0
        deployable_now = 0.0
        target_cash_ratio = 100.0

    idle_cash = max(0.0, cash - deployable_now)
    decision_reasons = [*safety_reasons, *quality_reasons, *operational_blockers]
    if not decision_reasons:
        decision_reasons.append("phase2_quality_gates_passed_scaled_paper_allowed")

    return {
        "run_id": f"{created.strftime('%Y%m%d-%H%M%S')}-paper-capital-allocation-audit",
        "created_at": created.isoformat(),
        "scope": "paper_only_capital_allocation_audit",
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
        "source_ledger": rel(args.ledger),
        "source_backlog": rel(args.backlog),
        "portfolio": {
            "cash_usd": round(cash, 6),
            "open_value_usd": round(open_value, 6),
            "equity_usd": round(equity, 6),
            "cash_ratio_pct": round(cash_ratio, 6),
            "open_positions": len(open_positions),
            "open_slots": open_slots,
        },
        "quality": quality,
        "phase2_quality_state": phase2,
        "monthly_target": monthly or {},
        "strategy_backlog": {
            "active_proposed_changes": len(active_items),
            "active_sources": active_sources or {},
            "top_items": [
                {
                    "id": item.get("proposed_change_id"),
                    "source": item.get("source"),
                    "type": item.get("change_type"),
                    "title": item.get("title"),
                    "status": item.get("operator_status"),
                }
                for item in active_items[:6]
            ],
        },
        "validation_capacity_state": {
            "sample_action": capacity.get("sample_action"),
            "recommended_runner_mode": capacity.get("recommended_runner_mode"),
            "reason": capacity.get("reason"),
        },
        "latest_sampler_state": sampler,
        "operational_authorization": operational,
        "allocation_decision": {
            "policy": policy,
            "current_deployment_authorization": "authorized" if current_authorized else "blocked",
            "authorization_blockers": [*safety_reasons, *operational_blockers],
            "research_max_new_positions": research_max_new_positions,
            "research_per_trade_notional_usd": round(research_per_trade_notional, 6),
            "research_max_deployable_usd": round(research_deployable, 6),
            "research_target_cash_ratio_pct": round(research_target_cash_ratio, 6),
            "max_new_positions_now": max_new_positions,
            "per_trade_notional_usd": round(per_trade_notional, 6),
            "max_deployable_now_usd": round(deployable_now, 6),
            "reserve_cash_usd": round(reserve, 6),
            "idle_or_waiting_cash_usd": round(idle_cash, 6),
            "target_cash_ratio_pct": round(target_cash_ratio, 6),
            "decision_reasons": decision_reasons,
            "current_instruction": (
                f"Current paper deployment is $0. Conditional research capacity is ${round(research_deployable, 6)} after operational blockers clear: {', '.join([*safety_reasons, *operational_blockers])}."
                if not current_authorized
                else
                "Only allow one $25 paper scout if all current trigger, liquidity, recovery, capacity and safety gates pass."
                if policy == "minimum_quality_scout_only"
                else "Block all paper deployment until safety flags and ledger integrity are fixed."
                if policy == "block_all_paper_deployment"
                else "Scaled paper sampling may proceed, still paper-only and gated by liquidity/recovery/capacity."
            ),
        },
        "operator_note": "Read-only paper capital allocation. It explains paper cash usage and never opens trades, changes config, or permits live orders.",
    }


def render_report(record: dict[str, Any]) -> str:
    portfolio = record.get("portfolio") or {}
    decision = record.get("allocation_decision") or {}
    quality = record.get("quality") or {}
    phase2 = record.get("phase2_quality_state") or {}
    backlog = record.get("strategy_backlog") or {}
    lines = [
        f"# Paper Capital Allocation Audit | {record.get('run_id')}",
        "",
        "- scope: `paper_only_capital_allocation_audit`",
        "- live_orders_enabled: `false`",
        "- private_api_used: `false`",
        "- ledger_mutated: `false`",
        "",
        "## Portfolio Cash State",
        "",
        f"- equity_usd: `${portfolio.get('equity_usd')}`",
        f"- cash_usd: `${portfolio.get('cash_usd')}`",
        f"- cash_ratio_pct: `{portfolio.get('cash_ratio_pct')}`",
        f"- open_positions: `{portfolio.get('open_positions')}`",
        f"- open_slots: `{portfolio.get('open_slots')}`",
        "",
        "## Allocation Decision",
        "",
        f"- policy: `{decision.get('policy')}`",
        f"- current_deployment_authorization: `{decision.get('current_deployment_authorization')}`",
        f"- authorization_blockers: `{decision.get('authorization_blockers')}`",
        f"- research_max_new_positions: `{decision.get('research_max_new_positions')}`",
        f"- research_per_trade_notional_usd: `${decision.get('research_per_trade_notional_usd')}`",
        f"- research_max_deployable_usd: `${decision.get('research_max_deployable_usd')}`",
        f"- max_new_positions_now: `{decision.get('max_new_positions_now')}`",
        f"- per_trade_notional_usd: `${decision.get('per_trade_notional_usd')}`",
        f"- max_deployable_now_usd: `${decision.get('max_deployable_now_usd')}`",
        f"- reserve_cash_usd: `${decision.get('reserve_cash_usd')}`",
        f"- idle_or_waiting_cash_usd: `${decision.get('idle_or_waiting_cash_usd')}`",
        f"- target_cash_ratio_pct: `{decision.get('target_cash_ratio_pct')}`",
        f"- current_instruction: {decision.get('current_instruction')}",
        "",
        "## Why",
        "",
    ]
    for reason in decision.get("decision_reasons") or []:
        lines.append(f"- `{reason}`")
    lines.extend(
        [
            "",
            "## Phase 2 Quality",
            "",
            f"- status: `{phase2.get('status')}`",
            f"- closed_count: `{quality.get('closed_count')}`",
            f"- win_rate_pct: `{quality.get('win_rate_pct')}`",
            f"- realized_pnl_usd: `${quality.get('realized_pnl_usd')}`",
            f"- max_drawdown_pct: `{quality.get('max_drawdown_pct')}`",
            "",
            "| Check | Pass | Value | Required |",
            "|---|---|---:|---|",
        ]
    )
    for item in phase2.get("checks") or []:
        lines.append(
            f"| `{item.get('name')}` | `{item.get('pass')}` | `{item.get('value')}` | `{item.get('required')}` |"
        )
    lines.extend(
        [
            "",
            "## Strategy Backlog Pressure",
            "",
            f"- active_proposed_changes: `{backlog.get('active_proposed_changes')}`",
            f"- active_sources: `{backlog.get('active_sources')}`",
            "",
            "| ID | Source | Type | Status | Title |",
            "|---|---|---|---|---|",
        ]
    )
    for item in backlog.get("top_items") or []:
        lines.append(
            f"| `{item.get('id')}` | `{item.get('source')}` | `{item.get('type')}` | `{item.get('status')}` | {item.get('title')} |"
        )
    if not backlog.get("top_items"):
        lines.append("| - | - | - | - | - |")
    lines.append("")
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="paper-capital-allocation-") as tmp:
        root = Path(tmp)
        ledger_path = root / "ledger.json"
        backlog_path = root / "backlog.json"
        ledger = {
            "cash_usd": 475.0,
            "equity_usd": 475.0,
            "open_value_usd": 0.0,
            "net_return_pct": -5.0,
            "max_drawdown_pct": -6.0,
            "live_orders_enabled": False,
            "private_api_used": False,
            "open_positions": [],
            "closed_trades": [{"realized_pnl_usd": -1.0} for _ in range(20)],
        }
        backlog = {
            "items": {
                "pc-test": {
                    "proposed_change_id": "pc-test",
                    "currently_present": True,
                    "source": "paper_trade_attribution_report",
                    "change_type": "tighten_profit_protection",
                    "operator_status": "proposed_pending_human_review",
                }
            },
            "summary": {"active_sources": {"paper_trade_attribution_report": 1}},
        }
        write_json(ledger_path, ledger)
        write_json(backlog_path, backlog)
        audit = {
            "paper_sample_metrics": {"closed_count": 20, "win_rate_pct": 35.0, "realized_pnl_usd": -20.0},
            "current_portfolio_metrics": {"net_return_pct": -5.0, "max_drawdown_pct": -6.0},
            "validation_capacity_state": {"sample_action": "paper_only_small_samples_allowed"},
            "validation_recovery_plan": {"monthly_target": {"target_gap_usd": 525.0}},
        }
        args = argparse.Namespace(
            ledger=ledger_path,
            backlog=backlog_path,
            min_scout_notional_usd=25.0,
            scaled_trade_notional_usd=75.0,
            min_cash_reserve_usd=50.0,
            reserve_pct_of_equity=10.0,
            max_open_positions=3,
            max_single_position_pct_of_equity=30.0,
        )
        fresh_active = {
            "authorization_status": "authorized",
            "blockers": [],
            "automation": {"status": "ACTIVE"},
            "pipeline": {"current_market_readiness_status": "fresh"},
            "kline_cache": {"fresh": True, "replay_available": True},
        }
        weak = build_allocation_record(
            ledger=ledger,
            audit=audit,
            backlog=backlog,
            args=args,
            operational_state=fresh_active,
        )
        assert weak["allocation_decision"]["policy"] == "minimum_quality_scout_only", weak
        assert weak["allocation_decision"]["per_trade_notional_usd"] == 25.0, weak
        assert weak["allocation_decision"]["max_deployable_now_usd"] == 25.0, weak

        paused = build_allocation_record(
            ledger=ledger,
            audit=audit,
            backlog=backlog,
            args=args,
            operational_state={
                **fresh_active,
                "authorization_status": "blocked",
                "blockers": ["automation_not_active:paused"],
            },
        )
        assert paused["allocation_decision"]["research_max_deployable_usd"] == 25.0, paused
        assert paused["allocation_decision"]["max_deployable_now_usd"] == 0.0, paused
        assert paused["allocation_decision"]["current_deployment_authorization"] == "blocked", paused

        stale = build_allocation_record(
            ledger=ledger,
            audit=audit,
            backlog=backlog,
            args=args,
            operational_state={
                **fresh_active,
                "authorization_status": "blocked",
                "blockers": ["pipeline_market_data_not_fresh"],
            },
        )
        assert stale["allocation_decision"]["max_deployable_now_usd"] == 0.0, stale

        missing_cache = build_allocation_record(
            ledger=ledger,
            audit=audit,
            backlog=backlog,
            args=args,
            operational_state={
                **fresh_active,
                "authorization_status": "blocked",
                "blockers": ["durable_kline_cache_not_replayable"],
            },
        )
        assert missing_cache["allocation_decision"]["max_deployable_now_usd"] == 0.0, missing_cache
        raw_preflight = normalize_operational_state(
            {
                "run_id": "unit-preflight",
                "safety": {"status": "blocked", "errors": ["automation_contract:automation_file_missing"]},
                "readiness": {
                    "status": "not_ready",
                    "can_resume_hourly_new_sampling": False,
                    "resume_requirements": ["rebuild_durable_kline_cache_before_current_signal_or_new_paper_entry"],
                },
                "automation_status": "missing",
                "automation_contract": "blocked",
                "kline_cache": {
                    "freshness_status": "stale",
                    "replay_available": False,
                    "current_signal_usable": False,
                },
            },
            root / "missing-automation.toml",
        )
        assert raw_preflight["authorization_status"] == "blocked", raw_preflight
        assert "automation_not_active:missing" in raw_preflight["blockers"], raw_preflight
        assert "durable_kline_cache_not_replayable" in raw_preflight["blockers"], raw_preflight
        unsafe = dict(ledger)
        unsafe["live_orders_enabled"] = True
        blocked = build_allocation_record(
            ledger=unsafe,
            audit=audit,
            backlog=backlog,
            args=args,
            operational_state=fresh_active,
        )
        assert blocked["allocation_decision"]["policy"] == "block_all_paper_deployment", blocked
        strong_audit = {
            "paper_sample_metrics": {"closed_count": 40, "win_rate_pct": 60.0, "realized_pnl_usd": 30.0},
            "current_portfolio_metrics": {"net_return_pct": 6.0, "max_drawdown_pct": -8.0},
            "validation_capacity_state": {"sample_action": "paper_only_small_samples_allowed"},
            "validation_recovery_plan": {},
        }
        strong = build_allocation_record(
            ledger=ledger,
            audit=strong_audit,
            backlog={"items": {}},
            args=args,
            operational_state=fresh_active,
        )
        assert strong["allocation_decision"]["policy"] == "scaled_paper_sampling_allowed", strong
        assert strong["allocation_decision"]["current_deployment_authorization"] == "authorized", strong
        assert strong["allocation_decision"]["max_deployable_now_usd"] > 0, strong
    return {
        "status": "ok",
        "fresh_active_authorization_verified": True,
        "paused_deployment_zero_verified": True,
        "stale_market_deployment_zero_verified": True,
        "missing_cache_deployment_zero_verified": True,
        "raw_preflight_blockers_normalized_verified": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Write read-only paper capital allocation audit.")
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    parser.add_argument("--backlog", type=Path, default=BACKLOG_JSON)
    parser.add_argument("--report-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--experiment-dir", type=Path, default=EXPERIMENTS_DIR)
    parser.add_argument("--min-scout-notional-usd", type=float, default=25.0)
    parser.add_argument("--scaled-trade-notional-usd", type=float, default=75.0)
    parser.add_argument("--min-cash-reserve-usd", type=float, default=50.0)
    parser.add_argument("--reserve-pct-of-equity", type=float, default=10.0)
    parser.add_argument("--max-open-positions", type=int, default=3)
    parser.add_argument("--max-single-position-pct-of-equity", type=float, default=30.0)
    parser.add_argument("--automation", type=Path, default=AUTOMATION_PATH)
    parser.add_argument("--operational-state-json", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0

    ledger = read_json(args.ledger, {})
    backlog = read_json(args.backlog, {})
    audit = build_audit(root=WORKSPACE_ROOT)
    raw_operational_state = (
        read_json(args.operational_state_json, {})
        if args.operational_state_json
        else operational_authorization_snapshot(args.automation)
    )
    operational_state = normalize_operational_state(
        raw_operational_state if isinstance(raw_operational_state, dict) else {},
        args.automation,
    )
    record = build_allocation_record(
        ledger=ledger if isinstance(ledger, dict) else {},
        audit=audit if isinstance(audit, dict) else {},
        backlog=backlog if isinstance(backlog, dict) else {},
        args=args,
        operational_state=operational_state if isinstance(operational_state, dict) else {},
    )
    out_stamp = record["run_id"].removesuffix("-paper-capital-allocation-audit")
    report_path = args.report_dir / f"{now_local().strftime('%Y-%m-%d')}-paper-capital-allocation-{out_stamp}.md"
    experiment_path = args.experiment_dir / f"{out_stamp}-paper-capital-allocation-audit.json"
    record["outputs"] = {"report": rel(report_path), "experiment": rel(experiment_path)}
    write_json(experiment_path, record)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(record), encoding="utf-8")
    result = {
        "status": "ok",
        "run_id": record["run_id"],
        "policy": (record.get("allocation_decision") or {}).get("policy"),
        "current_deployment_authorization": (record.get("allocation_decision") or {}).get("current_deployment_authorization"),
        "research_max_deployable_usd": (record.get("allocation_decision") or {}).get("research_max_deployable_usd"),
        "max_deployable_now_usd": (record.get("allocation_decision") or {}).get("max_deployable_now_usd"),
        "per_trade_notional_usd": (record.get("allocation_decision") or {}).get("per_trade_notional_usd"),
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
        "outputs": record["outputs"],
    }
    if args.compact_output:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(render_report(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
