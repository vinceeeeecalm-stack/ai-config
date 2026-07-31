#!/usr/bin/env python3
"""Build a read-only Phase 3 monthly-double pressure action board.

This board turns the monthly-double pressure test into an operator-facing
paper-only action plan. It reads the paper ledger plus existing validation and
capital-allocation artifacts, then explains why the system should stay in
minimum scout mode or what evidence would justify a larger paper-only sprint.

It never fetches market data, mutates the ledger, edits strategy config, places
orders, or authorizes live trading.
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
REPORT_PATH = ACTIVE_ROOT / "reports" / "PHASE3_PRESSURE_ACTION_BOARD.md"
EXPERIMENT_PATH = ACTIVE_ROOT / "experiments" / "phase3-pressure-action-board.json"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def now_local() -> dt.datetime:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0)


def rel(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def read_json(path: Path | None, default: Any = None) -> Any:
    if not path or not path.exists():
        return default
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def latest(pattern: str) -> Path | None:
    files = sorted(ACTIVE_ROOT.glob(pattern))
    return files[-1] if files else None


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if parsed == parsed and abs(parsed) != float("inf") else default
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def latest_validation_payload(runner_payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("post_validation", "post_audit", "after_validation_sample_audit"):
        value = runner_payload.get(key)
        if isinstance(value, dict) and isinstance(value.get("stdout_json"), dict):
            return value["stdout_json"]
        if isinstance(value, dict) and "paper_sample_metrics" in value:
            return value
    return {}


def monthly_target_from_sources(ledger: dict[str, Any], validation: dict[str, Any], created: dt.datetime) -> dict[str, Any]:
    recovery = validation.get("validation_recovery_plan") if isinstance(validation.get("validation_recovery_plan"), dict) else {}
    target = recovery.get("monthly_target") if isinstance(recovery.get("monthly_target"), dict) else {}
    if target:
        return dict(target)
    month_id = created.strftime("%Y-%m")
    baselines = ledger.get("monthly_goal_baselines") if isinstance(ledger.get("monthly_goal_baselines"), dict) else {}
    baseline = baselines.get(month_id) if isinstance(baselines.get(month_id), dict) else {}
    month_start = as_float(baseline.get("month_start_equity_usd"), as_float(ledger.get("initial_capital_usd"), 500.0))
    equity = as_float(ledger.get("equity_usd"), as_float(ledger.get("cash_usd"), 0.0))
    target_equity = as_float(baseline.get("target_equity_usd"), month_start * 2.0)
    gap = max(0.0, target_equity - equity)
    return {
        "target_model": "monthly_compounding_double",
        "month_id": month_id,
        "baseline_source": "ledger_monthly_goal_baselines" if baseline else "ledger_initial_capital_fallback",
        "month_start_equity_usd": round(month_start, 6),
        "target_equity_usd": round(target_equity, 6),
        "current_equity_usd": round(equity, 6),
        "target_gap_usd": round(gap, 6),
        "progress_pct": round(max(0.0, (equity - month_start) / month_start * 100.0), 6) if month_start else None,
        "required_return_pct_from_current_equity": round((target_equity / equity - 1.0) * 100.0, 6) if equity > 0 else None,
    }


def trade_stats(ledger: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    paper = validation.get("paper_sample_metrics") if isinstance(validation.get("paper_sample_metrics"), dict) else {}
    closed = [item for item in ledger.get("closed_trades") or [] if isinstance(item, dict)]
    pnls = [as_float(item.get("realized_pnl_usd")) for item in closed]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    payoff = (avg_win / abs(avg_loss)) if avg_loss < 0 else None
    closed_count = as_int(paper.get("closed_count"), len(closed))
    win_rate = as_float(paper.get("win_rate_pct"), (len(wins) / closed_count * 100.0) if closed_count else 0.0)
    realized = as_float(paper.get("realized_pnl_usd"), sum(pnls))
    net_closed = as_float(paper.get("realized_net_return_on_closed_notional_pct"))
    return {
        "closed_count": closed_count,
        "closed_gap_to_phase2_min": max(0, 30 - closed_count),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 6),
        "realized_pnl_usd": round(realized, 6),
        "realized_net_return_on_closed_notional_pct": net_closed,
        "average_win_usd": round(avg_win, 6),
        "average_loss_usd": round(avg_loss, 6),
        "payoff_ratio": round(payoff, 6) if payoff is not None else None,
        "max_drawdown_pct": ledger.get("max_drawdown_pct"),
    }


def decide_pressure_posture(
    monthly: dict[str, Any],
    stats: dict[str, Any],
    capital_payload: dict[str, Any],
) -> dict[str, Any]:
    allocation = capital_payload.get("allocation_decision") if isinstance(capital_payload.get("allocation_decision"), dict) else {}
    blockers: list[str] = []
    if as_float(monthly.get("target_gap_usd")) > 0:
        blockers.append("monthly_target_gap_open")
    if stats.get("closed_gap_to_phase2_min", 0) > 0:
        blockers.append("closed_sample_gap")
    if as_float(stats.get("win_rate_pct")) < 55.0:
        blockers.append("win_rate_below_55")
    if as_float(stats.get("realized_pnl_usd")) <= 0:
        blockers.append("realized_pnl_negative")
    if as_float(stats.get("realized_net_return_on_closed_notional_pct")) <= 0:
        blockers.append("closed_net_return_negative")

    policy = allocation.get("policy") or "unknown"
    max_deploy = as_float(allocation.get("max_deployable_now_usd"))
    per_trade = as_float(allocation.get("per_trade_notional_usd"))
    max_positions = as_int(allocation.get("max_new_positions_now"))
    if blockers:
        posture = "quality_recovery_scout_only"
        allowed_action = "at_most_one_25_usd_paper_scout_if_all_gates_pass"
    elif policy == "scaled_paper_sampling_allowed":
        posture = "scaled_pressure_paper_allowed"
        allowed_action = "scaled_paper_only_sampling_still_no_live"
    else:
        posture = "minimum_scout_until_next_audit"
        allowed_action = "keep_minimum_scout"

    return {
        "posture": posture,
        "allowed_action": allowed_action,
        "blockers": blockers,
        "capital_policy": policy,
        "max_deployable_now_usd": max_deploy,
        "per_trade_notional_usd": per_trade,
        "max_new_positions_now": max_positions,
    }


def action_rows(posture: dict[str, Any], stats: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        {
            "priority": 1,
            "action": "collect_quality_scout_samples",
            "paper_only_limit": posture.get("allowed_action"),
            "success_condition": "new closed samples improve win rate and net PnL without increasing max drawdown",
        },
        {
            "priority": 2,
            "action": "do_not_scale_size_yet",
            "paper_only_limit": f"per_trade_notional_usd={posture.get('per_trade_notional_usd')}",
            "success_condition": "Phase 2 gates pass: >=30 closed, >=55% win rate, positive closed net return",
        },
        {
            "priority": 3,
            "action": "retire_or_cooldown_failed_paths",
            "paper_only_limit": "respect validation_recovery_plan and strategy_proposal_enforcement gates",
            "success_condition": "next samples avoid retired entry modes and improve attribution",
        },
    ]
    if stats.get("closed_gap_to_phase2_min", 0) > 0:
        rows.insert(
            0,
            {
                "priority": 0,
                "action": "close_sample_gap",
                "paper_only_limit": f"need_{stats.get('closed_gap_to_phase2_min')}_more_closed_trades_minimum",
                "success_condition": "reach at least 30 closed trades before claiming quality proof",
            },
        )
    return rows


def build_record() -> dict[str, Any]:
    created = now_local()
    ledger = read_json(LEDGER_PATH, {}) or {}
    runner_path = latest("experiments/*validation-progress-runner.json")
    capital_path = latest("experiments/*paper-capital-allocation-audit.json")
    phase_path = latest("experiments/*phase-goal-readiness-audit.json")
    runner_payload = read_json(runner_path, {}) or {}
    validation = latest_validation_payload(runner_payload)
    capital_payload = read_json(capital_path, {}) or {}
    monthly = monthly_target_from_sources(ledger, validation, created)
    stats = trade_stats(ledger, validation)
    posture = decide_pressure_posture(monthly, stats, capital_payload)
    phase_payload = read_json(phase_path, {}) or {}
    phase_status = phase_payload.get("phase_status") if isinstance(phase_payload.get("phase_status"), dict) else {}
    record = {
        "run_id": f"{created.strftime('%Y%m%d-%H%M%S')}-phase3-pressure-action-board",
        "created_at": created.isoformat(),
        "scope": "paper_only_phase3_monthly_pressure_action_board",
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "ledger_mutated": False,
        "config_mutated": False,
        "source_ledger": rel(LEDGER_PATH),
        "source_runner": rel(runner_path),
        "source_capital_allocation": rel(capital_path),
        "source_phase_readiness": rel(phase_path),
        "phase_status": {
            "phase2": phase_status.get("phase2_positive_expectancy_proof"),
            "phase3": phase_status.get("phase3_monthly_double_pressure_test"),
            "phase4": phase_status.get("phase4_real_auto_trading_candidate"),
        },
        "monthly_target": monthly,
        "trade_quality": stats,
        "pressure_posture": posture,
        "action_rows": action_rows(posture, stats),
        "operator_note": (
            "This is a pressure-test action board, not a forecast and not a live-trading instruction. "
            "It keeps Phase 3 paper-only until target equity, sample quality and risk controls are proven."
        ),
        "outputs": {"report": rel(REPORT_PATH), "experiment": rel(EXPERIMENT_PATH)},
    }
    return record


def render_report(record: dict[str, Any]) -> str:
    monthly = record.get("monthly_target") or {}
    stats = record.get("trade_quality") or {}
    posture = record.get("pressure_posture") or {}
    lines = [
        "# Phase 3 Pressure Action Board",
        "",
        f"- generated_at: `{record.get('created_at')}`",
        "- scope: `paper_only_phase3_monthly_pressure_action_board`",
        f"- live_orders_enabled: `{record.get('live_orders_enabled')}`",
        f"- private_api_used: `{record.get('private_api_used')}`",
        f"- ledger_mutated: `{record.get('ledger_mutated')}`",
        "",
        "## Monthly Pressure Target",
        "",
        f"- month: `{monthly.get('month_id')}`",
        f"- start_equity: `${monthly.get('month_start_equity_usd')}`",
        f"- target_equity: `${monthly.get('target_equity_usd')}`",
        f"- current_equity: `${monthly.get('current_equity_usd')}`",
        f"- target_gap: `${monthly.get('target_gap_usd')}`",
        f"- required_return_from_current: `{monthly.get('required_return_pct_from_current_equity')}%`",
        "",
        "## Trade Quality",
        "",
        f"- closed_count: `{stats.get('closed_count')}` gap_to_30 `{stats.get('closed_gap_to_phase2_min')}`",
        f"- win_rate_pct: `{stats.get('win_rate_pct')}`",
        f"- realized_pnl_usd: `${stats.get('realized_pnl_usd')}`",
        f"- closed_net_return_pct: `{stats.get('realized_net_return_on_closed_notional_pct')}`",
        f"- avg_win / avg_loss: `${stats.get('average_win_usd')}` / `${stats.get('average_loss_usd')}`",
        "",
        "## Pressure Posture",
        "",
        f"- posture: `{posture.get('posture')}`",
        f"- allowed_action: `{posture.get('allowed_action')}`",
        f"- capital_policy: `{posture.get('capital_policy')}`",
        f"- max_deployable_now_usd: `${posture.get('max_deployable_now_usd')}`",
        f"- per_trade_notional_usd: `${posture.get('per_trade_notional_usd')}`",
        f"- blockers: `{', '.join(posture.get('blockers') or []) or '-'}`",
        "",
        "| Priority | Paper Action | Limit | Success Condition |",
        "|---:|---|---|---|",
    ]
    for row in record.get("action_rows") or []:
        lines.append(
            f"| `{row.get('priority')}` | `{row.get('action')}` | "
            f"`{row.get('paper_only_limit')}` | {row.get('success_condition')} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This board cannot open paper trades by itself.",
            "- It cannot authorize real orders, private APIs, withdrawals, margin, futures or perpetuals.",
            "- A Phase 3 pass requires target equity plus closed-sample quality, not one lucky trade.",
            "",
        ]
    )
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="phase3_pressure_board_", dir="/private/tmp") as tmp_text:
        tmp = Path(tmp_text)
        ledger = {
            "initial_capital_usd": 500,
            "cash_usd": 475,
            "equity_usd": 475,
            "live_orders_enabled": False,
            "private_api_used": False,
            "closed_trades": [
                {"realized_pnl_usd": 1.0},
                {"realized_pnl_usd": -2.0},
            ],
            "monthly_goal_baselines": {"2026-06": {"month_start_equity_usd": 500}},
        }
        path = tmp / "ledger.json"
        write_json(path, ledger)
        loaded = read_json(path, {})
        monthly = monthly_target_from_sources(loaded, {}, dt.datetime(2026, 6, 27, tzinfo=LOCAL_TZ))
        stats = trade_stats(loaded, {})
        posture = decide_pressure_posture(monthly, stats, {"allocation_decision": {"policy": "minimum_quality_scout_only", "max_deployable_now_usd": 25, "per_trade_notional_usd": 25}})
        assert monthly["target_equity_usd"] == 1000, monthly
        assert stats["closed_gap_to_phase2_min"] == 28, stats
        assert posture["posture"] == "quality_recovery_scout_only", posture
    return {
        "status": "ok",
        "monthly_target_verified": True,
        "quality_gap_verified": True,
        "scout_only_posture_verified": True,
        "uses_temporary_files_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    record = build_record()
    if not args.no_write:
        write_json(EXPERIMENT_PATH, record)
        write_text(REPORT_PATH, render_report(record))
    if args.compact_output:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "run_id": record.get("run_id"),
                    "monthly_target": record.get("monthly_target"),
                    "trade_quality": record.get("trade_quality"),
                    "pressure_posture": record.get("pressure_posture"),
                    "outputs": record.get("outputs"),
                    "live_orders_enabled": record.get("live_orders_enabled"),
                    "private_api_used": record.get("private_api_used"),
                    "ledger_mutated": record.get("ledger_mutated"),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
