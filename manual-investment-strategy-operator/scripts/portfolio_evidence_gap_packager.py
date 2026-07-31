#!/usr/bin/env python3
"""Package missing portfolio evidence into an actionable import checklist.

This script is read-only by default. It does not mutate the portfolio ledger,
recommendation history, or any broker/exchange data. Its job is to turn recurring
degraded report inputs into a concrete evidence backlog: which lots, staking
terms, cash rails, and broker buying-power fields are missing, what they block,
and which CSV columns should be imported before the next full cost-aware report.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

from cost_basis_reconciliation_audit import build_audit


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
DEFAULT_LEDGER = ROOT / "unified-longterm-alpha-investor" / "config" / "portfolio_ledger.json"
DEFAULT_OVERRIDES = MANUAL_ROOT / "config" / "current_position_overrides.json"

CRYPTO_LOT_COLUMNS = [
    "asset",
    "account_or_wallet",
    "source_platform",
    "order_or_tx_id",
    "executed_at",
    "side",
    "quantity",
    "fill_price_usd",
    "gross_amount_usd",
    "fee_amount",
    "fee_asset",
    "net_cost_basis_usd",
    "source_file",
    "data_quality_status",
    "notes",
]

LCETH_STAKING_COLUMNS = [
    "provider",
    "transaction_or_reward_id",
    "executed_at",
    "action",
    "lceth_quantity",
    "underlying_eth_quantity",
    "receipt_conversion_ratio",
    "gross_amount_usd",
    "fee_amount_usd",
    "reward_quantity",
    "reward_asset",
    "redeemable_underlying_quantity",
    "estimated_unstake_delay_days",
    "instant_unstake_fee_pct",
    "source_file",
    "data_quality_status",
    "notes",
]

US_EQUITY_CASH_RAIL_COLUMNS = [
    "as_of",
    "broker",
    "settled_cash_usd",
    "buying_power_usd",
    "unsettled_cash_usd",
    "pending_orders_usd",
    "margin_enabled",
    "shorting_enabled",
    "options_enabled",
    "source_file",
    "data_quality_status",
    "notes",
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
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


def round_or_none(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def find_holding(ledger: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    return next((item for item in ledger.get("holdings") or [] if item.get("symbol") == symbol), None)


def missing_quantity_from_cost_row(row: dict[str, Any]) -> float | None:
    quantity = as_float(row.get("quantity"))
    known = as_float(row.get("known_lot_quantity"), 0.0)
    if quantity is None or known is None:
        return None
    return max(0.0, quantity - known)


def build_cost_basis_gaps(cost_audit: dict[str, Any], ledger: dict[str, Any]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for row in cost_audit.get("holdings") or []:
        symbol = row.get("symbol")
        audit_status = row.get("audit_status")
        if audit_status not in {"missing_cost_basis", "partial_known_lots"}:
            continue
        holding = find_holding(ledger, str(symbol))
        impact = "block_full_pnl" if audit_status == "missing_cost_basis" else "partial_pnl_only"
        priority = "P0" if symbol == "lcETH" else "P1"
        missing_qty = missing_quantity_from_cost_row(row)
        gaps.append(
            {
                "gap_id": f"cost_basis_{symbol}",
                "category": "cost_basis",
                "symbol": symbol,
                "priority": priority,
                "audit_status": audit_status,
                "impact": impact,
                "market_value_usd": row.get("market_value_usd"),
                "total_quantity": row.get("quantity"),
                "known_quantity": row.get("known_lot_quantity"),
                "missing_quantity_estimate": round_or_none(missing_qty, 8),
                "known_quantity_pct": row.get("known_quantity_pct"),
                "known_lot_cost_basis_usd": row.get("known_lot_cost_basis_usd"),
                "missing_fields": [
                    "complete acquisition lots",
                    "fill timestamps",
                    "fill prices",
                    "fees and fee asset",
                    "source account/export file",
                ],
                "accepted_evidence": (
                    [
                        "Coinbase transaction or tax export for lcETH/LsETH/ETH staking receipt",
                        "Coinbase staking rewards export",
                        "Coinbase redemption/conversion details if available",
                    ]
                    if symbol == "lcETH"
                    else [
                        "Binance account trade export",
                        "Ledger staking reward export or wallet transaction history",
                        "Exchange deposit/withdrawal history if balances were transferred",
                    ]
                ),
                "ledger_notes": (holding or {}).get("cost_basis_notes"),
                "max_allowed_until_resolved": "conditional_action_only_for_cost_aware_rotation",
                "what_changes_after_resolution": "Full cost-aware PnL and tax-lot-aware rotation can be shown for this asset.",
            }
        )
    return gaps


def build_lceth_staking_gap(ledger: dict[str, Any]) -> dict[str, Any] | None:
    lceth = find_holding(ledger, "lcETH")
    if not lceth:
        return None
    staking = lceth.get("staking") or {}
    missing: list[str] = []
    for field in [
        "receipt_conversion_ratio",
        "redeemable_underlying_quantity",
        "unlock_available_at",
    ]:
        if staking.get(field) in (None, "", "unknown"):
            missing.append(field)
    if staking.get("staking_unlock_delay_days") in (None, "", "standard_unstake_varies_by_protocol_or_instant_unstake_if_eligible_with_fee"):
        missing.append("account_specific_unstake_delay")
    if not missing:
        return None
    return {
        "gap_id": "lceth_staking_redemption_terms",
        "category": "staking_redemption",
        "symbol": "lcETH",
        "priority": "P0",
        "impact": "blocks_precise_lceth_valuation_and_liquidity_sizing",
        "current_proxy_value_usd": ((lceth.get("api_valuation") or {}).get("api_market_value")),
        "account_mark_value_usd": staking.get("account_mark_value"),
        "missing_fields": missing,
        "accepted_evidence": [
            "Coinbase account staking position page or export with lcETH receipt terms",
            "Unstake/redeem preview showing conversion ratio, fees, and estimated delay",
            "Coinbase rewards export for reward lots",
        ],
        "max_allowed_until_resolved": "hold_or_smaller_size_only; no new ETH/lcETH DCA while concentrated",
        "what_changes_after_resolution": "lcETH can be valued with account-specific receipt economics instead of degraded ETH spot proxy.",
    }


def build_cash_rail_gaps(ledger: dict[str, Any], overrides: dict[str, Any]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    rails = ledger.get("cash_rails") or {}
    us_rail = rails.get("us_equity_rail") or {}
    crypto_rail = rails.get("crypto_rail") or {}
    override_cash = (overrides.get("cash_rails") or {}).get("us_equity_cash_usd")

    us_status = us_rail.get("data_quality_status") or us_rail.get("cash_status")
    if us_status != "verified":
        gaps.append(
            {
                "gap_id": "us_equity_cash_rail_verification",
                "category": "cash_rail",
                "rail": "us_equity_rail",
                "priority": "P0",
                "impact": "blocks_real_sizing_for_us_tactical_rotations",
                "ledger_cash_status": us_rail.get("cash_status"),
                "ledger_cash_usd": us_rail.get("cash_usd"),
                "user_stated_cash_override_usd": override_cash,
                "data_quality_status": "user_stated_degraded" if override_cash is not None else "missing",
                "missing_fields": [
                    "settled cash",
                    "buying power",
                    "unsettled cash",
                    "pending orders",
                    "margin/options/shorting disabled confirmation",
                    "export or screenshot timestamp",
                ],
                "accepted_evidence": [
                    "Broker account balances export",
                    "Broker cash/buying power screenshot with timestamp",
                    "Trade confirmation for recently sold tactical shares",
                ],
                "max_allowed_until_resolved": "conditional_action_or_watch",
                "what_changes_after_resolution": "US tactical relay can size entries from verified broker buying power instead of user-stated cash.",
            }
        )

    crypto_cash = as_float(crypto_rail.get("cash_or_stablecoin_value"), 0.0) or 0.0
    normal_min = as_float(crypto_rail.get("normal_cash_min_value"), 0.0) or 0.0
    if crypto_cash < normal_min:
        gaps.append(
            {
                "gap_id": "crypto_cash_floor_below_policy",
                "category": "cash_rail",
                "rail": "crypto_rail",
                "priority": "P1",
                "impact": "new_dca_requires_confirmed_new_cash_or_reduced_cash_floor_by_human_choice",
                "cash_or_stablecoin_value_usd": round(crypto_cash, 6),
                "normal_cash_min_value_usd": round(normal_min, 2),
                "cash_gap_usd": round(normal_min - crypto_cash, 2),
                "data_quality_status": crypto_rail.get("data_quality_status"),
                "missing_fields": [
                    "whether new monthly DCA cash has arrived in crypto rail",
                    "whether existing limit orders are still open or filled",
                ],
                "accepted_evidence": [
                    "Exchange/wallet USDT/USDC cash balance screenshot or export",
                    "Open order list and recent filled-order export",
                ],
                "max_allowed_until_resolved": "conditional_dca_only",
                "what_changes_after_resolution": "DCA sizing can distinguish available stablecoins from future planned deposits.",
            }
        )
    return gaps


def build_templates_metadata() -> dict[str, Any]:
    return {
        "crypto_lot_import_template": {
            "file": "crypto_lot_import_template.csv",
            "columns": CRYPTO_LOT_COLUMNS,
            "use_for": ["ADA", "SOL", "NIGHT", "USDT", "ETH dust", "other exchange lots"],
        },
        "lceth_staking_lot_import_template": {
            "file": "lceth_staking_lot_import_template.csv",
            "columns": LCETH_STAKING_COLUMNS,
            "use_for": ["lcETH acquisition", "staking rewards", "conversion/redeem previews"],
        },
        "us_equity_cash_rail_template": {
            "file": "us_equity_cash_rail_template.csv",
            "columns": US_EQUITY_CASH_RAIL_COLUMNS,
            "use_for": ["broker settled cash", "buying power", "pending orders", "permission flags"],
        },
    }


def write_templates(directory: Path) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    templates = {
        "crypto_lot_import_template.csv": CRYPTO_LOT_COLUMNS,
        "lceth_staking_lot_import_template.csv": LCETH_STAKING_COLUMNS,
        "us_equity_cash_rail_template.csv": US_EQUITY_CASH_RAIL_COLUMNS,
    }
    written: dict[str, str] = {}
    for filename, columns in templates.items():
        path = directory / filename
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
        written[filename] = str(path)
    return written


def build_package(
    ledger_path: Path = DEFAULT_LEDGER,
    overrides_path: Path = DEFAULT_OVERRIDES,
    material_value_usd: float = 1.0,
    templates_dir: Path | None = None,
) -> dict[str, Any]:
    ledger = load_json_if_exists(ledger_path)
    overrides = load_json_if_exists(overrides_path)
    cost_audit = build_audit(ledger_path, material_value_usd=material_value_usd)
    cost_gaps = build_cost_basis_gaps(cost_audit, ledger)
    staking_gap = build_lceth_staking_gap(ledger)
    cash_gaps = build_cash_rail_gaps(ledger, overrides)
    evidence_gaps = cost_gaps + ([staking_gap] if staking_gap else []) + cash_gaps
    blocking_gaps = [
        gap
        for gap in evidence_gaps
        if gap.get("priority") == "P0"
        or "block" in str(gap.get("impact"))
        or "blocks" in str(gap.get("impact"))
    ]

    written_templates: dict[str, str] = {}
    if templates_dir:
        written_templates = write_templates(templates_dir)

    status = "open_gaps" if evidence_gaps else "complete"
    max_allowed_effect = "conditional_action_or_watch" if blocking_gaps else "conditional_action"
    return {
        "generated_at": utc_now(),
        "package_version": "portfolio-evidence-gap-packager-v1",
        "status": status,
        "ledger_path": str(ledger_path),
        "overrides_path": str(overrides_path),
        "ledger_as_of": ledger.get("as_of"),
        "source_policy": ledger.get("ledger_pricing_policy"),
        "cost_basis_audit_summary": cost_audit.get("summary"),
        "readiness": {
            "full_cost_aware_pnl_allowed": (cost_audit.get("summary") or {}).get("max_allowed_pnl_claim") == "full_cost_aware",
            "us_equity_cash_verified": not any(gap.get("gap_id") == "us_equity_cash_rail_verification" for gap in evidence_gaps),
            "lceth_account_specific_redemption_verified": staking_gap is None,
            "crypto_cash_floor_met": not any(gap.get("gap_id") == "crypto_cash_floor_below_policy" for gap in evidence_gaps),
            "max_allowed_effect": max_allowed_effect,
            "human_confirmation_required_before_ledger_mutation": True,
        },
        "summary": {
            "evidence_gap_count": len(evidence_gaps),
            "blocking_gap_count": len(blocking_gaps),
            "p0_gap_ids": [gap.get("gap_id") for gap in evidence_gaps if gap.get("priority") == "P0"],
            "p1_gap_ids": [gap.get("gap_id") for gap in evidence_gaps if gap.get("priority") == "P1"],
        },
        "evidence_gaps": evidence_gaps,
        "import_templates": build_templates_metadata(),
        "written_templates": written_templates,
        "rerun_after_import": [
            "python3 manual-investment-strategy-operator/scripts/cost_basis_reconciliation_audit.py --format json",
            "python3 manual-investment-strategy-operator/scripts/portfolio_evidence_gap_packager.py --format json",
            "python3 manual-investment-strategy-operator/scripts/portfolio_comparison_snapshot.py",
            "python3 manual-investment-strategy-operator/scripts/build_daily_report_context.py --portfolio-snapshot-json <snapshot.json>",
        ],
        "mutation_policy": "This package is read-only. Ledger edits require a reviewed import step and human confirmation.",
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return ""
        return str(value).replace("\n", " ")

    output = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    output.extend("| " + " | ".join(cell(item) for item in row) + " |" for row in rows)
    return "\n".join(output)


def render_markdown(payload: dict[str, Any]) -> str:
    readiness = payload.get("readiness") or {}
    rows = [
        [
            gap.get("gap_id"),
            gap.get("priority"),
            gap.get("category"),
            gap.get("symbol") or gap.get("rail"),
            gap.get("impact"),
            ", ".join(gap.get("missing_fields") or []),
        ]
        for gap in payload.get("evidence_gaps") or []
    ]
    templates = payload.get("import_templates") or {}
    return "\n".join(
        [
            "# Portfolio Evidence Gap Package",
            "",
            f"- Status: `{payload.get('status')}`",
            f"- Evidence gaps: `{(payload.get('summary') or {}).get('evidence_gap_count')}`",
            f"- Blocking gaps: `{(payload.get('summary') or {}).get('blocking_gap_count')}`",
            f"- Max allowed effect: `{readiness.get('max_allowed_effect')}`",
            f"- Full cost-aware PnL allowed: `{readiness.get('full_cost_aware_pnl_allowed')}`",
            f"- US equity cash verified: `{readiness.get('us_equity_cash_verified')}`",
            f"- lcETH account-specific redemption verified: `{readiness.get('lceth_account_specific_redemption_verified')}`",
            "",
            "## Evidence Gaps",
            "",
            markdown_table(
                ["Gap", "Priority", "Category", "Asset/Rail", "Impact", "Missing Fields"],
                rows,
            )
            if rows
            else "No open evidence gaps.",
            "",
            "## Import Templates",
            "",
            "\n".join(
                f"- `{meta.get('file')}`: {', '.join(meta.get('columns') or [])}"
                for meta in templates.values()
            ),
            "",
            "## Rerun After Import",
            "",
            "\n".join(f"- `{cmd}`" for cmd in payload.get("rerun_after_import") or []),
            "",
            f"Mutation policy: {payload.get('mutation_policy')}",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Package portfolio evidence gaps into an import checklist")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--overrides", default=str(DEFAULT_OVERRIDES))
    parser.add_argument("--material-value-usd", type=float, default=1.0)
    parser.add_argument("--templates-dir", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    payload = build_package(
        ledger_path=Path(args.ledger),
        overrides_path=Path(args.overrides),
        material_value_usd=args.material_value_usd,
        templates_dir=Path(args.templates_dir) if args.templates_dir else None,
    )
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
