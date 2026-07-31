#!/usr/bin/env python3
"""Read-only paper compounding target audit.

This script summarizes the current paper ledger against the monthly
compounding-double target. It does not fetch market data, mutate the ledger, or
place orders.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
LEDGER_PATH = ROOT / "paper_trades" / "paper_portfolio_ledger.json"
REPORTS_DIR = ROOT / "reports"
EXPERIMENTS_DIR = ROOT / "experiments"
LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))


def now_local() -> dt.datetime:
    return dt.datetime.now(LOCAL_TZ).replace(microsecond=0)


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def read_ledger() -> dict[str, Any] | None:
    try:
        payload = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def monthly_target(ledger: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    month_id = now.strftime("%Y-%m")
    baselines = ledger.get("monthly_goal_baselines") if isinstance(ledger.get("monthly_goal_baselines"), dict) else {}
    baseline = baselines.get(month_id) if isinstance(baselines.get(month_id), dict) else {}
    equity = as_float(ledger.get("equity_usd"), 0.0) or 0.0
    month_start = as_float(baseline.get("month_start_equity_usd"), as_float(ledger.get("initial_capital_usd"), 500.0))
    target = as_float(baseline.get("target_equity_usd"), (month_start or 0.0) * 2.0)
    gap = (target or 0.0) - equity
    return {
        "target_model": "monthly_compounding_double",
        "month_id": month_id,
        "baseline_source": "ledger_monthly_goal_baselines" if baseline else "ledger_initial_capital_fallback",
        "month_start_equity_usd": round(month_start or 0.0, 6),
        "target_equity_usd": round(target or 0.0, 6),
        "current_equity_usd": round(equity, 6),
        "gap_to_target_usd": round(gap, 6),
        "progress_pct": round(((equity - (month_start or 0.0)) / (month_start or 1.0)) * 100.0, 6) if month_start else None,
        "required_return_from_current_pct": round((gap / equity) * 100.0, 6) if equity else None,
        "target_complete": bool(equity >= (target or float("inf"))),
    }


def closed_trade_stats(ledger: dict[str, Any]) -> dict[str, Any]:
    closed = [item for item in ledger.get("closed_trades") or [] if isinstance(item, dict)]
    wins = [item for item in closed if (as_float(item.get("realized_pnl_usd"), 0.0) or 0.0) > 0]
    losses = [item for item in closed if (as_float(item.get("realized_pnl_usd"), 0.0) or 0.0) <= 0]
    realized = sum(as_float(item.get("realized_pnl_usd"), 0.0) or 0.0 for item in closed)
    return {
        "closed_count": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round((len(wins) / len(closed)) * 100.0, 6) if closed else None,
        "realized_pnl_usd": round(realized, 6),
    }


def capability(ledger: dict[str, Any]) -> dict[str, Any]:
    orders = [item for item in ledger.get("paper_orders") or [] if isinstance(item, dict)]
    safe_filled = [
        item
        for item in orders
        if str(item.get("status", "")).upper() == "FILLED"
        and str(item.get("side", "")).upper() in {"BUY", "SELL"}
        and item.get("average_fill_price") is not None
        and item.get("executed_quantity") is not None
        and item.get("live_orders_enabled") is False
        and item.get("private_api_used") is False
    ]
    sides = {str(item.get("side", "")).upper() for item in safe_filled}
    return {
        "simulated_order_ledger_present": bool(safe_filled),
        "simulated_compounding_enabled": True,
        "binance_public_market_data_flow": "paper_ledger_mark_to_market_and_fast_loop_artifacts",
        "binance_live_order_flow": "disabled",
        "safe_filled_order_count": len(safe_filled),
        "buy_sell_lifecycle_present": {"BUY", "SELL"}.issubset(sides),
    }


def render_report(record: dict[str, Any]) -> str:
    target = record["monthly_target"]
    closed = record["closed_trade_stats"]
    cap = record["capability"]
    return "\n".join(
        [
            f"# Paper Compounding Target Auditor | {record['run_id']}",
            "",
            "This is a read-only paper ledger audit. It does not place orders or fetch market data.",
            "",
            "## Monthly Target",
            "",
            f"- Current equity: `${target['current_equity_usd']}`",
            f"- Target equity: `${target['target_equity_usd']}`",
            f"- Gap: `${target['gap_to_target_usd']}`",
            f"- Progress: `{target['progress_pct']}%`",
            f"- Required return from current equity: `{target['required_return_from_current_pct']}%`",
            f"- Target complete: `{target['target_complete']}`",
            "",
            "## Closed Paper Trades",
            "",
            f"- Closed count: `{closed['closed_count']}`",
            f"- Wins / losses: `{closed['wins']} / {closed['losses']}`",
            f"- Win rate: `{closed['win_rate_pct']}%`",
            f"- Realized PnL: `${closed['realized_pnl_usd']}`",
            "",
            "## Capability",
            "",
            f"- Simulated order ledger present: `{cap['simulated_order_ledger_present']}`",
            f"- BUY/SELL lifecycle present: `{cap['buy_sell_lifecycle_present']}`",
            f"- Live order flow: `{cap['binance_live_order_flow']}`",
            "",
        ]
    )


def build_record() -> dict[str, Any]:
    now = now_local()
    run_id = f"{now.strftime('%Y%m%d-%H%M%S')}-paper-compounding-target-auditor"
    ledger = read_ledger()
    if ledger is None:
        target = {
            "target_complete": False,
            "target_equity_usd": None,
            "current_equity_usd": None,
            "gap_to_target_usd": None,
            "progress_pct": None,
            "required_return_from_current_pct": None,
        }
        closed = {"closed_count": 0, "wins": 0, "losses": 0, "win_rate_pct": None, "realized_pnl_usd": 0.0}
        cap = {"simulated_order_ledger_present": False, "simulated_compounding_enabled": False, "binance_public_market_data_flow": "missing_ledger", "binance_live_order_flow": "disabled"}
        verdict = "ledger_missing_or_unreadable"
    else:
        target = monthly_target(ledger, now)
        closed = closed_trade_stats(ledger)
        cap = capability(ledger)
        verdict = "target_complete" if target.get("target_complete") else "target_not_complete"
    date = now.strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"{date}-{now.strftime('%H%M')}-paper-compounding-target-auditor.md"
    experiment_path = EXPERIMENTS_DIR / f"{run_id}.json"
    return {
        "run_id": run_id,
        "created_at": now.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
        "ledger_path": rel(LEDGER_PATH),
        "monthly_target": target,
        "closed_trade_stats": closed,
        "capability": cap,
        "verdict": verdict,
        "outputs": {
            "report": rel(report_path),
            "experiment": rel(experiment_path),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit the paper ledger against the monthly compounding target")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    record = build_record()
    write_json(WORKSPACE_ROOT / record["outputs"]["experiment"], record)
    write_text(WORKSPACE_ROOT / record["outputs"]["report"], render_report(record))
    if args.format == "markdown":
        print(render_report(record))
    else:
        compact = {
            "run_id": record["run_id"],
            "live_orders_enabled": False,
            "private_api_used": False,
            "monthly_target": record["monthly_target"],
            "closed_trade_stats": record["closed_trade_stats"],
            "capability": record["capability"],
            "verdict": record["verdict"],
            "outputs": record["outputs"],
        }
        print(json.dumps(compact if args.compact_output else record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
