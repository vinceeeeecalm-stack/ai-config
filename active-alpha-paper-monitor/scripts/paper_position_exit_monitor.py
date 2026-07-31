#!/usr/bin/env python3
"""
Fast paper position exit monitor for active-alpha-paper-monitor.

Research-only. This script reviews existing open paper positions, simulates
exchange-like sell orders when exit rules trigger, and writes compact artifacts.
It never opens new positions and never calls private/live trading endpoints.
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ROOT.parent
sys.path.insert(0, str(SCRIPT_DIR))

from sunday_crypto_realistic_paper_loop import (  # noqa: E402
    DEFAULT_CONFIG,
    DEFAULT_LEDGER,
    backfill_legacy_entry_orders,
    build_historical_barrier_replays,
    build_info_signals,
    fetch_market,
    iso,
    load_config,
    load_ledger,
    local_dt,
    mark_ledger,
    monthly_double_progress,
    reconcile_provisional_monthly_baseline_after_historical_close,
    render_closed_trades_table,
    render_open_positions_table,
    render_paper_orders_table,
    render_recent_events_table,
    review_positions,
    run_lock,
    save_ledger,
    session_summary,
    utc_now,
    write_json,
    write_text,
)
from research_panel_bridge import active_research_panel_overlay  # noqa: E402
import paper_strategy_overlay as pso  # noqa: E402


def cap_exit_monitor_action(action):
    """Keep exit-monitor handoffs paper-only even with a valid research panel."""

    rank = {
        "block": 0,
        "no_deploy": 1,
        "risk_alert": 2,
        "watch": 3,
        "paper_only": 4,
        "hold": 4,
        "trim_review": 4,
        "conditional_action": 5,
        "execute_now": 6,
    }
    if action not in rank:
        return "watch"
    if rank[action] <= rank["paper_only"]:
        return "paper_only" if action in {"hold", "trim_review"} else action
    return "paper_only"


def parse_now(text):
    from sunday_crypto_realistic_paper_loop import parse_now as shared_parse_now

    return shared_parse_now(text)


def build_settings(cfg):
    loop_cfg = dict(cfg.get("sunday_crypto_realistic_paper_loop", {}))
    loop_cfg.update(cfg.get("daily_crypto_paper_auto_trader", {}))
    loop_cfg.update(cfg.get("paper_position_exit_monitor", {}))
    paper_cfg = cfg.get("paper_portfolio", {})
    exit_cfg = cfg.get("paper_exit_management", {})
    scale_cfg = cfg.get("paper_scale_in", {})
    return {
        "commission_bps": loop_cfg.get("commission_bps", 10),
        "base_slippage_bps": loop_cfg.get("base_slippage_bps", 8),
        "max_dynamic_impact_bps": loop_cfg.get("max_dynamic_impact_bps", 50),
        "min_quote_volume_usd": loop_cfg.get("min_quote_volume_usd", 5_000_000),
        "min_depth_1pct_usd": loop_cfg.get("min_depth_1pct_usd", 25_000),
        "min_depth_to_notional": loop_cfg.get("min_depth_to_notional", 5),
        "max_spread_bps": loop_cfg.get("max_spread_bps", 35),
        "max_cross_source_deviation_bps": loop_cfg.get("max_cross_source_deviation_bps", 200),
        "default_stop_pct": paper_cfg.get("default_stop_pct", -8),
        "default_take_profit_pct": paper_cfg.get("default_take_profit_pct", 20),
        "request_pause_seconds": loop_cfg.get("request_pause_seconds", 0.03),
        "dynamic_exit_enabled": exit_cfg.get("enabled", True),
        "historical_barrier_replay_enabled": exit_cfg.get("historical_barrier_replay_enabled", True),
        "historical_barrier_interval": exit_cfg.get("historical_barrier_interval", "5m"),
        "historical_barrier_request_timeout_seconds": exit_cfg.get("historical_barrier_request_timeout_seconds", 8),
        "historical_barrier_max_pages": exit_cfg.get("historical_barrier_max_pages", 100),
        "historical_barrier_extra_slippage_bps": exit_cfg.get("historical_barrier_extra_slippage_bps", 12),
        "profit_protection_trigger_pct": exit_cfg.get("profit_protection_trigger_pct", 6.0),
        "profit_trailing_giveback_pct": exit_cfg.get("profit_trailing_giveback_pct", 3.5),
        "profit_break_even_floor_pct": exit_cfg.get("profit_break_even_floor_pct", 0.6),
        "profit_protection_min_hold_minutes": exit_cfg.get("profit_protection_min_hold_minutes", 30),
        "time_decay_exit_enabled": exit_cfg.get("time_decay_exit_enabled", True),
        "time_decay_exit_hours": exit_cfg.get("time_decay_exit_hours", 96),
        "time_decay_exit_max_pnl_pct": exit_cfg.get("time_decay_exit_max_pnl_pct", 0.0),
        "time_decay_min_info_score": exit_cfg.get("time_decay_min_info_score", 25),
        "exploratory_capital_protection_enabled": exit_cfg.get("exploratory_capital_protection_enabled", True),
        "exploratory_capital_protection_min_hold_minutes": exit_cfg.get("exploratory_capital_protection_min_hold_minutes", 30),
        "exploratory_capital_protection_loss_cut_pct": exit_cfg.get("exploratory_capital_protection_loss_cut_pct", -3.25),
        "exploratory_capital_protection_max_favorable_pnl_pct": exit_cfg.get("exploratory_capital_protection_max_favorable_pnl_pct", 1.0),
        "scale_in_loss_cut_pct": scale_cfg.get("loss_cut_pct", -7.0),
        "target_sprint_loss_cut_pct": scale_cfg.get("target_sprint_loss_cut_pct", -5.5),
        "paper_strategy_overlay": pso.load_overlay(),
    }


def money(value):
    try:
        return f"${float(value):.6f}"
    except Exception:
        return "-"


def pct_text(value):
    try:
        return f"{float(value):+.4f}%"
    except Exception:
        return "-"


def render_report(run):
    target = run["monthly_double_progress"]
    lines = [
        f"# Paper Position Exit Monitor | {run['run_id']}",
        "",
        "No live orders were placed. This is paper-only simulated API exit monitoring.",
        "",
        "## Portfolio",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Cash | {money(run['ledger'].get('cash_usd'))} |",
        f"| Open value | {money(run['ledger'].get('open_value_usd'))} |",
        f"| Equity | {money(run['ledger'].get('equity_usd'))} |",
        f"| Net return | {pct_text(run['ledger'].get('net_return_pct'))} |",
        f"| Max drawdown | {pct_text(run['ledger'].get('max_drawdown_pct'))} |",
        "",
        "## Monthly Double Tracker",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Target model | `{target.get('target_model')}` |",
        f"| Month | `{target.get('month_id')}` |",
        f"| Baseline source | `{target.get('baseline_source')}` |",
        f"| Month-start equity | {money(target.get('month_start_equity_usd', target['initial_capital_usd']))} |",
        f"| Lifetime initial capital | {money(target.get('lifetime_initial_capital_usd', target['initial_capital_usd']))} |",
        f"| Target equity | {money(target['target_equity_usd'])} |",
        f"| Current equity | {money(target['current_equity_usd'])} |",
        f"| Gap to target | {money(target['gap_to_target_usd'])} |",
        f"| Progress | {target['progress_pct']:.4f}% |",
        f"| Current return | {target['current_return_pct']:+.4f}% |",
        "",
        "## Research Committee",
        "",
        f"- Research committee degraded: `{run.get('research_committee_degraded')}`",
        f"- Max allowed action: `{run.get('max_allowed_action')}`",
        f"- Missing reason: `{run.get('research_panel_missing_reason') or 'none'}`",
        f"- Research method: `{(run.get('research_panel') or {}).get('research_method') or 'missing'}`",
        f"- External roles: `{len((run.get('research_panel') or {}).get('external_roles_used') or [])}`",
        "",
        "## Reviewed Actions",
        "",
        "| Trade | Symbol | Status | Last/Exit | Reason | PnL |",
        "|---|---|---|---:|---|---:|",
    ]
    if run["reviewed_positions"]:
        for item in run["reviewed_positions"]:
            lines.append(
                f"| `{item.get('paper_trade_id')}` | `{item.get('symbol')}` | {item.get('status')} | "
                f"{item.get('last_price', item.get('exit_price', '-'))} | {item.get('exit_reason', '-')} | "
                f"{money(item.get('realized_pnl_usd', item.get('unrealized_net_pnl_usd')))} |"
            )
    else:
        lines.append("| none | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Current Holdings",
            "",
            *render_open_positions_table(run["ledger"]),
            "",
            "## Simulated API Orders",
            "",
            *render_paper_orders_table(run["ledger"]),
            "",
            "## Trade Tracking",
            "",
            *render_closed_trades_table(run["ledger"]),
            "",
            "## Recent Ledger Events",
            "",
            *render_recent_events_table(run["ledger"]),
            "",
            "## Data Status",
            "",
            f"- Data status: `{run['data_status']}`",
            f"- Open symbols reviewed: `{', '.join(run['open_symbols']) if run['open_symbols'] else 'none'}`",
            f"- Error count: `{len(run['errors'])}`",
            "",
        ]
    )
    return "\n".join(lines)


def compact_run_output(run):
    research_panel = run.get("research_panel") or {}
    external_validation = (
        research_panel.get("external_agent_validation")
        or run.get("external_agent_validation")
        or {}
    )
    research_method = (
        research_panel.get("research_method")
        or run.get("research_method")
        or "missing"
    )
    return {
        "run_id": run["run_id"],
        "live_orders_enabled": False,
        "private_api_used": False,
        "private_api_keys_used": False,
        "allow_real_orders": False,
        "dry_run": run.get("dry_run"),
        "requested_dry_run": run.get("requested_dry_run"),
        "offline_fixture": run.get("offline_fixture"),
        "offline_fixture_writes_allowed": run.get("offline_fixture_writes_allowed"),
        "offline_fixture_write_policy": run.get("offline_fixture_write_policy"),
        "data_status": run["data_status"],
        "open_symbols_reviewed": run["open_symbols"],
        "reviewed_positions": run["reviewed_positions"],
        "paper_orders_total": len(run["ledger"].get("paper_orders", [])),
        "paper_portfolio": {
            "cash_usd": run["ledger"].get("cash_usd"),
            "open_value_usd": run["ledger"].get("open_value_usd"),
            "equity_usd": run["ledger"].get("equity_usd"),
            "net_return_pct": run["ledger"].get("net_return_pct"),
            "max_drawdown_pct": run["ledger"].get("max_drawdown_pct"),
        },
        "monthly_double_progress": run["monthly_double_progress"],
        "outputs": run["outputs"],
        "research_panel_missing": run.get("research_panel_missing"),
        "research_committee_degraded": run.get("research_committee_degraded"),
        "research_panel_missing_reason": run.get("research_panel_missing_reason"),
        "max_allowed_action": run.get("max_allowed_action"),
        "research_method": research_method,
        "external_roles_used": research_panel.get("external_roles_used") or [],
        "external_agent_validation": {
            "valid": external_validation.get("valid"),
            "unique_known_role_count": external_validation.get("unique_known_role_count"),
            "successful_known_role_count": external_validation.get("successful_known_role_count"),
            "degraded_role_count": external_validation.get("degraded_role_count"),
            "missing_required_external_roles": external_validation.get("missing_required_external_roles"),
        },
    }


def run_monitor(args):
    now = parse_now(args.now)
    local = local_dt(now)
    effective_dry_run = bool(args.dry_run or (args.offline_fixture and not args.allow_offline_fixture_writes))
    cfg = load_config(args.config)
    settings = build_settings(cfg)
    paper_cfg = cfg.get("paper_portfolio", {})
    initial = float(paper_cfg.get("initial_capital_usd", 500))
    ledger_path = Path(args.ledger)
    run_id = f"{local.strftime('%Y%m%d-%H%M')}-paper-position-exit-monitor"

    with run_lock(enabled=not args.no_lock and not effective_dry_run):
        ledger = load_ledger(ledger_path, initial)
        backfill_legacy_entry_orders(ledger, now)
        open_symbols = sorted({p.get("symbol") for p in ledger.get("open_positions", []) if p.get("symbol")})
        market = {}
        info_signals = {"rows": [], "by_symbol": {}, "errors": []}
        errors = []
        if open_symbols:
            market, _, market_errors = fetch_market(open_symbols, settings, offline_fixture=args.offline_fixture)
            errors.extend(market_errors)
            if args.offline_fixture:
                info_signals = {"rows": [], "by_symbol": {}, "errors": []}
            else:
                info_signals = build_info_signals(open_symbols, market, settings)
                errors.extend(info_signals.get("errors", []))
            barrier_replays, barrier_errors = build_historical_barrier_replays(
                ledger,
                settings,
                now,
                offline_fixture=args.offline_fixture,
            )
            errors.extend(barrier_errors)
            reviewed = review_positions(
                ledger,
                market,
                settings,
                now,
                info_signals=info_signals,
                run_id=run_id,
                historical_barrier_replays=barrier_replays,
            )
        else:
            reviewed = []
        mark_ledger(ledger)
        reconcile_provisional_monthly_baseline_after_historical_close(ledger, reviewed, now)
        ledger.setdefault("events", []).append(
            {
                "event_type": "paper_exit_monitor_snapshot",
                "created_at": iso(now),
                "run_id": run_id,
                "equity_usd": ledger.get("equity_usd"),
                "cash_usd": ledger.get("cash_usd"),
                "open_value_usd": ledger.get("open_value_usd"),
                "net_return_pct": ledger.get("net_return_pct"),
            }
        )
        target = monthly_double_progress(ledger)
        summary = session_summary(ledger, now)
        data_status = "degraded" if errors else "verified"
        if args.offline_fixture and not args.allow_offline_fixture_writes:
            data_status = "offline_fixture_not_counted"
            errors.append(
                "offline_fixture_result_not_counted: fixture prices may be synthetic; ledger writes are blocked without --allow-offline-fixture-writes"
            )

        date = local.strftime("%Y-%m-%d")
        stamp = local.strftime("%Y%m%d-%H%M")
        report_path = ROOT / "reports" / f"{date}-paper-exit-monitor-{local.strftime('%H%M')}.md"
        experiment_path = ROOT / "experiments" / f"{stamp}-paper-position-exit-monitor.json"
        paper_path = ROOT / "paper_trades" / f"{date}-paper-exit-monitor-{local.strftime('%H%M')}.json"
        handoff_path = ROOT / "handoffs" / f"{date}-paper-exit-monitor-{local.strftime('%H%M')}-handoff.json"

        missing_research_reason = (
            "subagent research committee output was not supplied to this paper-position exit monitor; "
            "output is paper-only exit/review context and requires manual review before real money"
        )
        research_overlay = active_research_panel_overlay(
            args.external_agent_outputs_json,
            run_id,
            missing_research_reason,
        )
        capped_action = cap_exit_monitor_action(research_overlay.get("max_allowed_action"))
        research_overlay["max_allowed_action"] = capped_action
        if isinstance(research_overlay.get("research_panel"), dict):
            research_overlay["research_panel"]["max_allowed_action"] = capped_action
            research_overlay["research_panel"]["arbiter_decision"] = (
                str(research_overlay["research_panel"].get("arbiter_decision") or "")
                + " Paper position exit monitor caps the handoff at paper-only simulated exit review."
            ).strip()
        run = {
            "run_id": run_id,
            "created_at": iso(now),
            "local_time": local.isoformat(),
            "source_skill": "active-alpha-paper-monitor",
            "loop_kind": "paper_position_exit_monitor",
            "live_orders_enabled": False,
            "private_api_used": False,
            "private_api_keys_used": False,
            "allow_real_orders": False,
            "dry_run": effective_dry_run,
            "requested_dry_run": bool(args.dry_run),
            "offline_fixture": bool(args.offline_fixture),
            "offline_fixture_writes_allowed": bool(args.allow_offline_fixture_writes),
            "offline_fixture_write_policy": (
                "blocked_without_explicit_allow_flag"
                if args.offline_fixture and not args.allow_offline_fixture_writes
                else "not_applicable_or_explicitly_allowed"
            ),
            **research_overlay,
            "open_symbols": open_symbols,
            "data_status": data_status,
            "errors": errors,
            "reviewed_positions": reviewed,
            "info_signals": info_signals,
            "settings": settings,
            "ledger": ledger,
            "monthly_double_progress": target,
            "session_summary": summary,
            "outputs": {},
        }
        run["outputs"] = {
            "report": str(report_path.relative_to(WORKSPACE_ROOT)),
            "experiment": str(experiment_path.relative_to(WORKSPACE_ROOT)),
            "paper_trades": str(paper_path.relative_to(WORKSPACE_ROOT)),
            "handoff": str(handoff_path.relative_to(WORKSPACE_ROOT)),
            "paper_ledger": str(ledger_path.relative_to(WORKSPACE_ROOT)) if ledger_path.is_absolute() and WORKSPACE_ROOT in ledger_path.parents else str(ledger_path),
        }
        handoff = {
            "handoff_id": run_id,
            "created_at": iso(now),
            "source_skill": "active-alpha-paper-monitor",
            "target_skill": "manual-investment-strategy-operator",
            "candidate_type": "paper_position_exit_monitor",
            "loop_kind": "paper_position_exit_monitor",
            "live_orders_enabled": False,
            "monitor_recommendation": capped_action,
            **research_overlay,
            "reviewed_positions": reviewed,
            "paper_portfolio": {
                "cash_usd": ledger.get("cash_usd"),
                "open_value_usd": ledger.get("open_value_usd"),
                "equity_usd": ledger.get("equity_usd"),
                "net_return_pct": ledger.get("net_return_pct"),
                "max_drawdown_pct": ledger.get("max_drawdown_pct"),
            },
            "monthly_double_progress": target,
            "requires_manual_review_before_real_money": True,
        }
        paper_record = {
            "created_at": iso(now),
            "run_id": run_id,
            "live_orders_enabled": False,
            "reviewed_positions": reviewed,
            "monthly_double_progress": target,
            "simulated_api_orders": ledger.get("paper_orders", [])[-10:],
            "ledger_snapshot": ledger,
        }
        save_ledger(ledger_path, ledger, dry_run=effective_dry_run)
        write_json(experiment_path, run, dry_run=effective_dry_run)
        write_json(paper_path, paper_record, dry_run=effective_dry_run)
        write_json(handoff_path, handoff, dry_run=effective_dry_run)
        write_text(report_path, render_report(run), dry_run=effective_dry_run)
        print(json.dumps(compact_run_output(run) if args.compact_output else run, ensure_ascii=False, indent=2))
        return run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--now", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline-fixture", action="store_true")
    ap.add_argument("--allow-offline-fixture-writes", action="store_true")
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--compact-output", action="store_true", default=True)
    ap.add_argument("--external-agent-outputs-json", help="optional externally collected 6+ subagent output JSON")
    args = ap.parse_args()
    run_monitor(args)


if __name__ == "__main__":
    main()
