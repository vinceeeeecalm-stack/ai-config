#!/usr/bin/env python3
"""Audit portfolio cost-basis coverage without inventing missing lots.

This script is read-only. It distinguishes fully costed holdings, partial
known-lot holdings, and holdings whose full acquisition/tax lots are still
missing. The goal is to prevent long-term PnL and 10x path reports from
silently treating screenshot quantities as verified cost basis.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = ROOT / "unified-longterm-alpha-investor" / "config" / "portfolio_ledger.json"


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


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def audit_holding(item: dict[str, Any], material_value_usd: float) -> dict[str, Any]:
    symbol = item.get("symbol")
    quantity = as_float(item.get("quantity"))
    market_value = as_float(item.get("market_value"), 0.0) or 0.0
    avg_cost = as_float(item.get("avg_cost"))
    lots = item.get("lots") or []
    known_lots = [lot for lot in lots if as_float(lot.get("cost_basis")) is not None]
    missing_lots = [lot for lot in lots if as_float(lot.get("cost_basis")) is None]
    known_lot_cost = sum(as_float(lot.get("cost_basis"), 0.0) or 0.0 for lot in known_lots)
    known_lot_quantity = sum(as_float(lot.get("quantity"), 0.0) or 0.0 for lot in known_lots)
    known_quantity_pct = None
    if quantity and quantity > 0:
        known_quantity_pct = round(known_lot_quantity / quantity * 100.0, 2)

    if avg_cost is not None:
        status = "full_avg_cost_available"
        max_pnl_status = "cost_aware_pnl_allowed"
    elif known_lots:
        status = "partial_known_lots"
        max_pnl_status = "partial_pnl_only"
    elif market_value <= material_value_usd or item.get("symbol") == "UNALLOCATED_EARN_BALANCE":
        status = "not_material_or_not_applicable"
        max_pnl_status = "not_material"
    else:
        status = "missing_cost_basis"
        max_pnl_status = "block_full_pnl"

    return {
        "holding_id": item.get("holding_id"),
        "symbol": symbol,
        "asset_class": item.get("asset_class"),
        "market_value_usd": round(market_value, 6),
        "quantity": quantity,
        "avg_cost": avg_cost,
        "cost_basis_status": item.get("cost_basis_status") or status,
        "audit_status": status,
        "max_pnl_status": max_pnl_status,
        "known_lot_count": len(known_lots),
        "missing_lot_count": len(missing_lots),
        "known_lot_quantity": round(known_lot_quantity, 8),
        "known_quantity_pct": known_quantity_pct,
        "known_lot_cost_basis_usd": round(known_lot_cost, 6),
        "notes": item.get("cost_basis_notes"),
    }


def build_audit(ledger_path: Path = DEFAULT_LEDGER, material_value_usd: float = 1.0) -> dict[str, Any]:
    ledger = load_json(ledger_path)
    rows = [audit_holding(item, material_value_usd) for item in ledger.get("holdings") or []]
    fully_known = [row for row in rows if row["audit_status"] == "full_avg_cost_available"]
    partial = [row for row in rows if row["audit_status"] == "partial_known_lots"]
    missing_material = [
        row
        for row in rows
        if row["audit_status"] == "missing_cost_basis" and row["market_value_usd"] > material_value_usd
    ]
    not_material = [row for row in rows if row["audit_status"] == "not_material_or_not_applicable"]
    known_cost = sum(row["known_lot_cost_basis_usd"] for row in rows)
    status = "verified_full" if not partial and not missing_material else "partial"
    max_allowed_pnl_claim = "full_cost_aware" if status == "verified_full" else "partial_cost_aware_only"
    return {
        "generated_at": utc_now(),
        "status": status,
        "audit_version": "cost-basis-reconciliation-audit-v1",
        "ledger_path": str(ledger_path),
        "ledger_as_of": ledger.get("as_of"),
        "ledger_policy": ledger.get("ledger_pricing_policy"),
        "reconciliation": ledger.get("cost_basis_reconciliation") or {},
        "summary": {
            "holdings_count": len(rows),
            "full_avg_cost_count": len(fully_known),
            "partial_known_lot_count": len(partial),
            "missing_material_count": len(missing_material),
            "not_material_or_not_applicable_count": len(not_material),
            "known_lot_cost_basis_usd": round(known_cost, 6),
            "known_or_partial_symbols": [row["symbol"] for row in fully_known + partial],
            "partial_known_lot_symbols": [row["symbol"] for row in partial],
            "missing_material_symbols": [row["symbol"] for row in missing_material],
            "max_allowed_pnl_claim": max_allowed_pnl_claim,
        },
        "holdings": rows,
        "failed_gates": [
            "missing_material_cost_basis"
        ] if missing_material else [],
        "next_actions": [
            "Import Coinbase/lcETH acquisition, conversion, reward, fee and redemption lots.",
            "Import complete ADA and SOL account trade exports for the pre-2026-05-25 balances and 2026-05-25_to_2026-05-28 residual fills.",
            "Keep avg_cost null for partial holdings until full lot history is available; use known_lot_cost_basis_usd only for partial reconciliation.",
        ] if missing_material or partial else [],
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        return str(value if value is not None else "").replace("\n", " ")

    output = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    output.extend("| " + " | ".join(cell(item) for item in row) + " |" for row in rows)
    return "\n".join(output)


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    rows = [
        [
            row.get("symbol"),
            row.get("audit_status"),
            row.get("market_value_usd"),
            row.get("avg_cost"),
            row.get("known_lot_count"),
            row.get("missing_lot_count"),
            row.get("known_quantity_pct"),
            row.get("max_pnl_status"),
        ]
        for row in payload.get("holdings") or []
    ]
    return "\n".join([
        "# Cost Basis Reconciliation Audit",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- Max allowed PnL claim: `{summary.get('max_allowed_pnl_claim')}`",
        f"- Full avg-cost holdings: `{summary.get('full_avg_cost_count')}`",
        f"- Partial known-lot holdings: `{summary.get('partial_known_lot_count')}`",
        f"- Missing material holdings: `{', '.join(summary.get('missing_material_symbols') or []) or 'none'}`",
        "",
        markdown_table(
            ["Symbol", "Audit", "Market Value", "Avg Cost", "Known Lots", "Missing Lots", "Known Qty %", "Max PnL"],
            rows,
        ),
        "",
        "## Next Actions",
        "",
        "\n".join(f"- {item}" for item in payload.get("next_actions") or ["No cost-basis action required."]),
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit cost-basis coverage in the portfolio ledger")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--material-value-usd", type=float, default=1.0)
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    payload = build_audit(Path(args.ledger), material_value_usd=args.material_value_usd)
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
