#!/usr/bin/env python3
"""Build a Phase 2 quality recovery board for the active-alpha paper loop.

Read-only. This script turns the Phase 2 blockers into concrete paper-only
sample-quality actions. It never fetches market data, mutates the paper ledger,
changes strategy config, or opens/closes paper/live positions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
LEDGER_PATH = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
REPORT_PATH = ACTIVE_ROOT / "reports" / "PHASE2_QUALITY_RECOVERY_ACTION_BOARD.md"
EXPERIMENT_PATH = ACTIVE_ROOT / "experiments" / "phase2-quality-recovery-action-board.json"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def now_local() -> dt.datetime:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0)


def rel(path: Path | None) -> str:
    if path is None:
        return "-"
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


def latest(pattern: str) -> Path | None:
    files = list((ACTIVE_ROOT / "experiments").glob(pattern))
    return max(files, key=lambda item: item.stat().st_mtime) if files else None


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def closed_trades(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in ledger.get("closed_trades") or []
        if isinstance(item, dict) and item.get("status") == "closed"
    ]


def group_quality(trades: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[str(trade.get(field) or "UNKNOWN")].append(trade)
    rows: list[dict[str, Any]] = []
    for name, items in groups.items():
        pnls = [as_float(item.get("realized_pnl_usd")) for item in items]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl <= 0]
        rows.append(
            {
                "name": name,
                "trade_count": len(items),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate_pct": round(len(wins) / len(items) * 100.0, 4) if items else 0.0,
                "net_pnl_usd": round(sum(pnls), 6),
                "avg_pnl_usd": round(statistics.mean(pnls), 6) if pnls else 0.0,
                "action": classify_group_action(len(items), len(wins), sum(pnls)),
            }
        )
    return sorted(rows, key=lambda item: (item["net_pnl_usd"], item["win_rate_pct"], -item["trade_count"]))


def classify_group_action(count: int, wins: int, pnl: float) -> str:
    if count >= 3 and pnl < 0 and wins / max(1, count) < 0.55:
        return "retire_or_cooldown_from_new_samples"
    if count >= 2 and pnl < 0:
        return "smallest_paper_only_after_retest"
    if count >= 3 and pnl > 0 and wins / max(1, count) >= 0.55:
        return "eligible_for_quality_scout_review"
    return "insufficient_sample_watch_only"


def win_path(current_closed: int, current_wins: int, threshold: float, max_total: int = 120) -> dict[str, Any]:
    for target_total in range(current_closed + 1, max_total + 1):
        additional = target_total - current_closed
        required_total_wins = math.ceil(threshold * target_total)
        required_new_wins = required_total_wins - current_wins
        if required_new_wins <= additional:
            return {
                "threshold_pct": round(threshold * 100.0, 2),
                "target_total_closed": target_total,
                "additional_trades": additional,
                "required_new_wins": max(0, required_new_wins),
                "allowed_new_losses": additional - max(0, required_new_wins),
                "required_new_win_rate_pct": round(max(0, required_new_wins) / additional * 100.0, 4),
            }
    return {
        "threshold_pct": round(threshold * 100.0, 2),
        "target_total_closed": None,
        "additional_trades": None,
        "required_new_wins": None,
        "allowed_new_losses": None,
        "required_new_win_rate_pct": None,
    }


def build_payload() -> dict[str, Any]:
    created = now_local()
    run_id = f"{created.strftime('%Y%m%d-%H%M%S')}-phase2-quality-recovery-board"
    ledger = read_json(LEDGER_PATH, {})
    trades = closed_trades(ledger if isinstance(ledger, dict) else {})
    pnls = [as_float(trade.get("realized_pnl_usd")) for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl <= 0]
    closed_count = len(trades)
    win_count = len(wins)
    loss_count = len(losses)
    realized_pnl = round(sum(pnls), 6)
    closed_notional = sum(as_float(trade.get("notional_usd")) for trade in trades)
    net_return = round(realized_pnl / closed_notional * 100.0, 6) if closed_notional else None
    avg_win = round(statistics.mean(wins), 6) if wins else 0.0
    avg_loss = round(statistics.mean(losses), 6) if losses else 0.0
    current_win_rate = round(win_count / closed_count * 100.0, 6) if closed_count else 0.0
    next_8_all_win_rate = round((win_count + 8) / max(1, closed_count + 8) * 100.0, 6)
    break_even_needed = max(0.0, -realized_pnl)
    next_8_required_avg_pnl = round(break_even_needed / 8.0, 6) if break_even_needed else 0.0
    phase_path = latest("*phase-goal-readiness-audit.json")
    attribution_path = latest("*paper-trade-attribution-report.json")
    backlog_path = ACTIVE_ROOT / "experiments" / "strategy-iteration-backlog.json"
    capital_path = latest("*paper-capital-allocation-audit.json")
    phase_payload = read_json(phase_path, {})
    attribution_payload = read_json(attribution_path, {})
    backlog_payload = read_json(backlog_path, {})
    capital_payload = read_json(capital_path, {})
    entry_rows = group_quality(trades, "paper_entry_mode")
    family_rows = group_quality(trades, "strategy_family")
    blocked_entry_modes = [
        row for row in entry_rows if row["action"] in {"retire_or_cooldown_from_new_samples", "smallest_paper_only_after_retest"}
    ][:6]
    blocked_families = [
        row for row in family_rows if row["action"] in {"retire_or_cooldown_from_new_samples", "smallest_paper_only_after_retest"}
    ][:6]
    phase2_blockers = []
    if isinstance(phase_payload.get("summary"), dict):
        phase2_blockers = phase_payload["summary"].get("phase2_blockers") or []
    attribution_summary = attribution_payload.get("summary") if isinstance(attribution_payload.get("summary"), dict) else {}
    proposed_changes = attribution_payload.get("proposed_changes") if isinstance(attribution_payload.get("proposed_changes"), list) else []
    backlog_summary = backlog_payload.get("summary") if isinstance(backlog_payload.get("summary"), dict) else {}
    capital_decision = capital_payload.get("allocation_decision") if isinstance(capital_payload.get("allocation_decision"), dict) else {}
    quality_gate = {
        "phase2_status": (phase_payload.get("phase_status") or {}).get("phase2_positive_expectancy_proof"),
        "current_quality_state": "loss_recovery_required",
        "allowed_new_sample_posture": "one_25_usd_quality_scout_only_after_all_gates",
        "block_larger_sizing": True,
        "block_live_or_testnet_promotion": True,
        "required_context_on_new_trade": "market_context_at_entry",
        "required_diversity": "new samples should diversify strategy_family and paper_entry_mode",
    }
    action_rows = [
        {
            "priority": 1,
            "action": "do_not_expand_size_until_positive_expectancy",
            "why": "current closed paper win rate and net PnL are below Phase 2 thresholds",
            "paper_only_limit": "$25 max per new quality scout; max one new position when all gates pass",
            "success_condition": "next closed samples lift net PnL toward break-even without increasing max drawdown",
        },
        {
            "priority": 2,
            "action": "enforce_profit_protection_tightening",
            "why": f"missed profit protection count {attribution_summary.get('missed_profit_protection_count')}",
            "paper_only_limit": "proposal only; validate through future paper exits",
            "success_condition": "fewer trades give back +2% floating profit into realized loss",
        },
        {
            "priority": 3,
            "action": "cooldown_negative_entry_modes_and_families",
            "why": "multiple entry modes/families have negative realized PnL",
            "paper_only_limit": "blocked from larger sizing; retest only at smallest scout after trigger and liquidity gates",
            "success_condition": "subgroup forward samples turn net positive after fees/slippage",
        },
        {
            "priority": 4,
            "action": "stamp_market_context_at_entry",
            "why": "Phase 2 regime proof currently has missing context on historical trades",
            "paper_only_limit": "no new sample counts toward regime proof unless context fields are present",
            "success_condition": "new closed trades cover multiple explicit market regimes",
        },
        {
            "priority": 5,
            "action": "collect_independent_quality_scout_samples",
            "why": "Phase 2 requires diversified positive evidence, not repeated weak patterns",
            "paper_only_limit": "one quality scout at a time until win rate and net PnL recover",
            "success_condition": "at least two strategy families and two entry modes show positive realized PnL",
        },
    ]
    payload = {
        "run_id": run_id,
        "created_at": created.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "scope": "phase2_quality_recovery_read_only",
        "phase2_metrics": {
            "closed_count": closed_count,
            "closed_gap_to_min_30": max(0, 30 - closed_count),
            "wins": win_count,
            "losses": loss_count,
            "win_rate_pct": current_win_rate,
            "realized_pnl_usd": realized_pnl,
            "realized_net_return_on_closed_notional_pct": net_return,
            "average_win_usd": avg_win,
            "average_loss_usd": avg_loss,
            "next_8_all_win_rate_pct": next_8_all_win_rate,
            "next_8_required_avg_pnl_to_breakeven_usd": next_8_required_avg_pnl,
            "win_path_55pct": win_path(closed_count, win_count, 0.55),
            "win_path_60pct": win_path(closed_count, win_count, 0.60),
        },
        "quality_gate": quality_gate,
        "phase2_blockers": [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "detail": item.get("detail"),
                "next_step": item.get("next_step"),
            }
            for item in phase2_blockers
            if isinstance(item, dict)
        ],
        "entry_mode_quality": entry_rows,
        "strategy_family_quality": family_rows,
        "blocked_entry_modes": blocked_entry_modes,
        "blocked_strategy_families": blocked_families,
        "attribution_proposed_change_count": len(proposed_changes),
        "backlog_active_proposed_changes": backlog_summary.get("active_proposed_changes"),
        "capital_policy": capital_decision.get("policy"),
        "max_deployable_now_usd": capital_decision.get("max_deployable_now_usd"),
        "per_trade_notional_usd": capital_decision.get("per_trade_notional_usd"),
        "action_rows": action_rows,
        "sources": {
            "ledger": rel(LEDGER_PATH),
            "phase_goal_readiness": rel(phase_path),
            "paper_trade_attribution": rel(attribution_path),
            "strategy_iteration_backlog": rel(backlog_path),
            "capital_allocation": rel(capital_path),
        },
        "outputs": {"report": rel(REPORT_PATH), "experiment": rel(EXPERIMENT_PATH)},
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "ledger_mutated": False,
        "strategy_config_mutated": False,
    }
    return payload


def render(payload: dict[str, Any]) -> str:
    metrics = payload["phase2_metrics"]
    gate = payload["quality_gate"]
    lines = [
        "# Phase 2 Quality Recovery Action Board",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- created_at: `{payload['created_at']}`",
        "- scope: `phase2_quality_recovery_read_only`",
        f"- live_orders_enabled: `{payload['live_orders_enabled']}`",
        f"- private_api_used: `{payload['private_api_used']}`",
        f"- ledger_mutated: `{payload['ledger_mutated']}`",
        "",
        "## Current Math",
        "",
        f"- closed_count: `{metrics['closed_count']}` gap_to_30 `{metrics['closed_gap_to_min_30']}`",
        f"- wins/losses: `{metrics['wins']}/{metrics['losses']}`",
        f"- win_rate_pct: `{metrics['win_rate_pct']}`",
        f"- realized_pnl_usd: `${metrics['realized_pnl_usd']}`",
        f"- closed_net_return_pct: `{metrics['realized_net_return_on_closed_notional_pct']}`",
        f"- next_8_all_win_rate_pct: `{metrics['next_8_all_win_rate_pct']}`",
        f"- next_8_required_avg_pnl_to_breakeven_usd: `${metrics['next_8_required_avg_pnl_to_breakeven_usd']}`",
        "",
        "## Win-Rate Recovery Path",
        "",
        "| Threshold | Target Total Closed | Additional Trades | Required New Wins | Allowed New Losses | Required New Win % |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for key in ("win_path_55pct", "win_path_60pct"):
        item = metrics[key]
        lines.append(
            f"| `{item['threshold_pct']}%` | `{item['target_total_closed']}` | `{item['additional_trades']}` | "
            f"`{item['required_new_wins']}` | `{item['allowed_new_losses']}` | `{item['required_new_win_rate_pct']}` |"
        )
    lines.extend(
        [
            "",
            "## Quality Gate",
            "",
            f"- phase2_status: `{gate['phase2_status']}`",
            f"- current_quality_state: `{gate['current_quality_state']}`",
            f"- allowed_new_sample_posture: `{gate['allowed_new_sample_posture']}`",
            f"- required_context_on_new_trade: `{gate['required_context_on_new_trade']}`",
            f"- required_diversity: `{gate['required_diversity']}`",
            "",
            "## Blocked / Cooldown Entry Modes",
            "",
            "| Entry Mode | Trades | Win % | Net PnL | Action |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for item in payload["blocked_entry_modes"]:
        lines.append(
            f"| `{item['name']}` | `{item['trade_count']}` | `{item['win_rate_pct']}` | `{item['net_pnl_usd']}` | `{item['action']}` |"
        )
    if not payload["blocked_entry_modes"]:
        lines.append("| - | - | - | - | - |")
    lines.extend(["", "## Blocked / Cooldown Strategy Families", "", "| Family | Trades | Win % | Net PnL | Action |", "|---|---:|---:|---:|---|"])
    for item in payload["blocked_strategy_families"]:
        lines.append(
            f"| `{item['name']}` | `{item['trade_count']}` | `{item['win_rate_pct']}` | `{item['net_pnl_usd']}` | `{item['action']}` |"
        )
    if not payload["blocked_strategy_families"]:
        lines.append("| - | - | - | - | - |")
    lines.extend(["", "## Actions", "", "| Priority | Action | Limit | Success Condition |", "|---:|---|---|---|"])
    for item in payload["action_rows"]:
        lines.append(
            f"| `{item['priority']}` | `{item['action']}` | `{item['paper_only_limit']}` | {item['success_condition']} |"
        )
    lines.extend(["", "## Sources", ""])
    for key, value in payload["sources"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    return "\n".join(lines)


def write_outputs(payload: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPERIMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render(payload), encoding="utf-8")
    EXPERIMENT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def self_test() -> dict[str, Any]:
    sample = {
        "closed_trades": [
            {
                "status": "closed",
                "paper_entry_mode": "weak",
                "strategy_family": "mom",
                "realized_pnl_usd": -2,
                "notional_usd": 25,
            },
            {
                "status": "closed",
                "paper_entry_mode": "weak",
                "strategy_family": "mom",
                "realized_pnl_usd": -1,
                "notional_usd": 25,
            },
            {
                "status": "closed",
                "paper_entry_mode": "strong",
                "strategy_family": "rev",
                "realized_pnl_usd": 2,
                "notional_usd": 25,
            },
        ],
        "live_orders_enabled": False,
        "private_api_used": False,
    }
    rows = group_quality(closed_trades(sample), "paper_entry_mode")
    weak = [row for row in rows if row["name"] == "weak"][0]
    path = win_path(8, 4, 0.55)
    with tempfile.TemporaryDirectory(prefix="phase2_quality_recovery_", dir="/private/tmp") as tmp_text:
        tmp = Path(tmp_text)
        report = tmp / "report.md"
        payload = {
            "run_id": "self-test",
            "created_at": now_local().isoformat(),
            "phase2_metrics": {
                "closed_count": 3,
                "closed_gap_to_min_30": 27,
                "wins": 1,
                "losses": 2,
                "win_rate_pct": 33.3333,
                "realized_pnl_usd": -1,
                "realized_net_return_on_closed_notional_pct": -1.3333,
                "average_win_usd": 2,
                "average_loss_usd": -1.5,
                "next_8_all_win_rate_pct": 81.8181,
                "next_8_required_avg_pnl_to_breakeven_usd": 0.125,
                "win_path_55pct": win_path(3, 1, 0.55),
                "win_path_60pct": win_path(3, 1, 0.60),
            },
            "quality_gate": {
                "phase2_status": "not_proven",
                "current_quality_state": "loss_recovery_required",
                "allowed_new_sample_posture": "one_25_usd_quality_scout_only_after_all_gates",
                "required_context_on_new_trade": "market_context_at_entry",
                "required_diversity": "new samples should diversify strategy_family and paper_entry_mode",
            },
            "blocked_entry_modes": [weak],
            "blocked_strategy_families": [],
            "action_rows": [],
            "sources": {},
            "live_orders_enabled": False,
            "private_api_used": False,
            "ledger_mutated": False,
        }
        report.write_text(render(payload), encoding="utf-8")
        rendered = report.exists() and "Phase 2 Quality Recovery Action Board" in report.read_text(encoding="utf-8")
    assert weak["action"] == "smallest_paper_only_after_retest", weak
    assert path["required_new_wins"] >= 0, path
    assert rendered
    return {
        "status": "ok",
        "group_quality_verified": True,
        "win_path_verified": True,
        "render_verified": True,
        "uses_temporary_files_only": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Write Phase 2 quality recovery action board.")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    payload = build_payload()
    write_outputs(payload)
    if args.compact_output:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "run_id": payload["run_id"],
                    "phase2_metrics": payload["phase2_metrics"],
                    "quality_gate": payload["quality_gate"],
                    "blocked_entry_mode_count": len(payload["blocked_entry_modes"]),
                    "blocked_strategy_family_count": len(payload["blocked_strategy_families"]),
                    "outputs": payload["outputs"],
                    "live_orders_enabled": payload["live_orders_enabled"],
                    "private_api_used": payload["private_api_used"],
                    "ledger_mutated": payload["ledger_mutated"],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
