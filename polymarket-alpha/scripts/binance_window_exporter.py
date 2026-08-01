#!/usr/bin/env python3
"""Read-only exporter from the canonical Binance paper ledger to 30-day windows."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINANCE_LEDGER = ROOT.parent / "active-alpha-paper-monitor" / "paper_trades" / "paper_portfolio_ledger.json"


def parse_iso(value: Any):
    from datetime import datetime, timezone
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def friction_complete(trade: dict[str, Any]) -> bool:
    required = {"commission_usd", "commission_bps", "slippage_bps", "spread_bps", "depth_1pct_usd", "fill_price"}
    return all(
        isinstance(trade.get(side), dict)
        and trade[side].get("status") in {"verified", "conservative_estimate"}
        and required.issubset(trade[side])
        and all(trade[side].get(key) is not None for key in required)
        for side in ("entry_execution", "exit_execution")
    )


def export_windows(ledger: dict[str, Any], source_sha256: str) -> dict[str, Any]:
    start = parse_iso(ledger.get("created_at"))
    observed_until = parse_iso(ledger.get("updated_at"))
    initial = float(ledger.get("initial_capital_usd", 0))
    trades = ledger.get("closed_trades", [])
    safety_ok = ledger.get("live_orders_enabled") is False and ledger.get("private_api_used") is False
    windows = []
    if start and observed_until and initial > 0:
        index = 0
        while start + timedelta(days=30 * (index + 1)) <= observed_until:
            window_start = start + timedelta(days=30 * index)
            window_end = window_start + timedelta(days=30)
            included, boundary = [], []
            for trade in trades:
                opened, closed = parse_iso(trade.get("opened_at")), parse_iso(trade.get("closed_at"))
                if not opened or not closed:
                    continue
                overlaps = opened < window_end and closed >= window_start
                contained = window_start <= opened and closed <= window_end
                if contained:
                    included.append(trade)
                elif overlaps:
                    boundary.append(trade.get("paper_trade_id"))
            ids = [str(row.get("paper_trade_id")) for row in included]
            duplicate_ids = sorted({item for item in ids if ids.count(item) > 1})
            friction_ok = all(friction_complete(row) for row in included)
            failures = []
            if not safety_ok:
                failures.append("unsafe_binance_ledger_flags")
            if boundary:
                failures.append("unmarked_boundary_position")
            if duplicate_ids:
                failures.append("duplicate_trade_ids")
            if not friction_ok:
                failures.append("friction_fields_incomplete")
            pnl = sum(float(row.get("realized_pnl_usd", 0)) for row in included)
            windows.append({
                "window_id": f"binance-{window_start.date()}-{window_end.date()}",
                "start_at": window_start.isoformat(), "end_at": window_end.isoformat(),
                "initial_equity_usd": initial, "capital_protocol": "reset_normalized_to_initial_capital_each_window",
                "closed_trades": len(included), "net_pnl_usd": pnl, "net_roi_pct": pnl / initial * 100,
                "data_status": "complete" if not failures else "incomplete",
                "friction_complete": friction_ok, "failed_gates": failures,
                "boundary_trade_ids": boundary, "duplicate_trade_ids": duplicate_ids,
            })
            index += 1
    return {
        "schema_version": "binance-paper-30d-window-export-v1",
        "source_ledger_created_at": ledger.get("created_at"),
        "source_ledger_observed_until": ledger.get("updated_at"),
        "source_ledger_sha256": source_sha256,
        "initial_equity_usd": initial,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        "comparison_status": "same_window_source_ready" if windows else "awaiting_first_complete_30d_window",
        "windows": windows,
    }


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export canonical Binance paper ledger into strict 30-day windows")
    parser.add_argument("--ledger", default=str(DEFAULT_BINANCE_LEDGER))
    parser.add_argument("--output", default=str(ROOT / "experiments" / "current-binance-30d-windows.json"))
    args = parser.parse_args()
    source = Path(args.ledger)
    raw = source.read_bytes()
    ledger = json.loads(raw)
    payload = export_windows(ledger, hashlib.sha256(raw).hexdigest())
    payload["source_ledger_path"] = str(source.resolve())
    atomic_json(Path(args.output), payload)
    print(json.dumps({
        "comparison_status": payload["comparison_status"], "complete_windows": len(payload["windows"]),
        "observed_until": payload["source_ledger_observed_until"], "output": args.output,
        "live_orders_enabled": False, "private_api_used": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
