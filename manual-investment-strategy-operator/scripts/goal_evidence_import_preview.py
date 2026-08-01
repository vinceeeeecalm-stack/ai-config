#!/usr/bin/env python3
"""Build a read-only preview for validated goal-evidence CSV imports.

The preview sits between validation and any future human-confirmed ledger edit.
It does not mutate the portfolio ledger, write recommendations, place orders, or
move funds. It only turns valid CSV evidence into a proposed update package that
the user can inspect before any manual import is considered.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
UNIFIED_ROOT = ROOT / "unified-longterm-alpha-investor"
DEFAULT_TEMPLATES_DIR = MANUAL_ROOT / "import_templates"
DEFAULT_LEDGER_PATH = UNIFIED_ROOT / "config" / "portfolio_ledger.json"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = load_module(SCRIPT_DIR / "goal_evidence_import_validator.py", "goal_evidence_import_validator_for_preview")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def compact_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")


def load_json(path: Path | str | None) -> Any:
    if not path:
        return None
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {str(key): (value or "").strip() for key, value in row.items()}
            for row in reader
            if any((value or "").strip() for value in row.values())
        ]


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def bool_value(value: Any) -> bool | None:
    text = str(value or "").strip().lower()
    if text in {"true", "yes", "y", "1", "enabled"}:
        return True
    if text in {"false", "no", "n", "0", "disabled"}:
        return False
    return None


def money(value: Any) -> str:
    number = as_float(value)
    if number is None:
        return "-"
    return f"${number:,.2f}"


def compute_net_cost(row: dict[str, str]) -> float | None:
    net_cost = as_float(row.get("net_cost_basis_usd"))
    if net_cost is not None:
        return net_cost
    gross = as_float(row.get("gross_amount_usd"))
    if gross is not None:
        return gross
    quantity = as_float(row.get("quantity"))
    fill = as_float(row.get("fill_price_usd"))
    if quantity is None or fill is None:
        return None
    fee = as_float(row.get("fee_amount"), 0.0) or 0.0
    fee_asset = (row.get("fee_asset") or "").upper()
    estimated_fee_usd = fee if fee_asset in {"USD", "USDT", "USDC"} else 0.0
    return quantity * fill + estimated_fee_usd


def current_holdings_by_symbol(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for holding in ledger.get("holdings") or []:
        symbol = str(holding.get("symbol") or "").upper()
        if symbol:
            result[symbol] = holding
    return result


def build_crypto_lot_preview(rows: list[dict[str, str]], holdings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    proposed_lots: list[dict[str, Any]] = []
    by_asset: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, start=2):
        asset = row.get("asset", "").upper()
        side = row.get("side", "").lower()
        quantity = as_float(row.get("quantity"), 0.0) or 0.0
        net_cost = compute_net_cost(row)
        signed_quantity = -quantity if side in {"sell", "transfer_out"} else quantity
        lot = {
            "source_row": index,
            "lot_id": f"{asset}-{row.get('order_or_tx_id') or row.get('executed_at') or index}",
            "asset": asset,
            "side": side,
            "executed_at": row.get("executed_at"),
            "quantity": quantity,
            "signed_quantity": signed_quantity,
            "fill_price_usd": as_float(row.get("fill_price_usd")),
            "gross_amount_usd": as_float(row.get("gross_amount_usd")),
            "fee_amount": as_float(row.get("fee_amount")),
            "fee_asset": row.get("fee_asset"),
            "net_cost_basis_usd": net_cost,
            "source_platform": row.get("source_platform"),
            "account_or_wallet": row.get("account_or_wallet"),
            "source_file": row.get("source_file"),
            "data_quality_status": row.get("data_quality_status"),
            "notes": row.get("notes"),
            "human_review_required": True,
        }
        proposed_lots.append(lot)
        summary = by_asset.setdefault(asset, {
            "asset": asset,
            "current_ledger_quantity": (holdings.get(asset) or {}).get("quantity"),
            "current_cost_basis_status": (holdings.get(asset) or {}).get("cost_basis_status"),
            "rows": 0,
            "signed_quantity_delta": 0.0,
            "known_cost_basis_delta_usd": 0.0,
            "candidate_gap_closure": f"cost_basis_{asset}",
        })
        summary["rows"] += 1
        summary["signed_quantity_delta"] += signed_quantity
        if net_cost is not None and side not in {"sell", "transfer_out"}:
            summary["known_cost_basis_delta_usd"] += net_cost
    return {
        "template_type": "crypto_lot_import",
        "proposed_lot_count": len(proposed_lots),
        "asset_summaries": list(by_asset.values()),
        "proposed_lots": proposed_lots,
        "ledger_mutation_preview": "append cost lots after human confirmation; do not change current prices or create trades",
    }


def build_lceth_preview(rows: list[dict[str, str]], holdings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    lceth = holdings.get("LCETH") or holdings.get("LSETH") or {}
    records: list[dict[str, Any]] = []
    latest_terms: dict[str, Any] = {}
    latest_terms_time: dt.datetime | None = None
    for index, row in enumerate(rows, start=2):
        executed_at = row.get("executed_at")
        parsed_time = parse_time(executed_at)
        record = {
            "source_row": index,
            "provider": row.get("provider"),
            "transaction_or_reward_id": row.get("transaction_or_reward_id"),
            "executed_at": executed_at,
            "action": row.get("action"),
            "lceth_quantity": as_float(row.get("lceth_quantity")),
            "underlying_eth_quantity": as_float(row.get("underlying_eth_quantity")),
            "receipt_conversion_ratio": as_float(row.get("receipt_conversion_ratio")),
            "gross_amount_usd": as_float(row.get("gross_amount_usd")),
            "fee_amount_usd": as_float(row.get("fee_amount_usd")),
            "reward_quantity": as_float(row.get("reward_quantity")),
            "reward_asset": row.get("reward_asset"),
            "redeemable_underlying_quantity": as_float(row.get("redeemable_underlying_quantity")),
            "estimated_unstake_delay_days": as_float(row.get("estimated_unstake_delay_days")),
            "instant_unstake_fee_pct": as_float(row.get("instant_unstake_fee_pct")),
            "source_file": row.get("source_file"),
            "data_quality_status": row.get("data_quality_status"),
            "notes": row.get("notes"),
            "human_review_required": True,
        }
        records.append(record)
        has_terms = (
            record["receipt_conversion_ratio"] is not None
            or record["redeemable_underlying_quantity"] is not None
            or record["estimated_unstake_delay_days"] is not None
            or record["instant_unstake_fee_pct"] is not None
        )
        if has_terms and (latest_terms_time is None or (parsed_time and parsed_time >= latest_terms_time)):
            latest_terms_time = parsed_time or latest_terms_time
            latest_terms = {
                "receipt_conversion_ratio": record["receipt_conversion_ratio"],
                "redeemable_underlying_quantity": record["redeemable_underlying_quantity"],
                "estimated_unstake_delay_days": record["estimated_unstake_delay_days"],
                "instant_unstake_fee_pct": record["instant_unstake_fee_pct"],
                "source_row": index,
                "source_file": row.get("source_file"),
            }
    return {
        "template_type": "lceth_staking_lot_import",
        "current_ledger_quantity": lceth.get("quantity"),
        "current_staking_status": ((lceth.get("staking") or {}).get("lock_status")),
        "current_receipt_conversion_ratio": ((lceth.get("staking") or {}).get("receipt_conversion_ratio")),
        "proposed_record_count": len(records),
        "proposed_records": records,
        "proposed_latest_redemption_terms": latest_terms,
        "candidate_gap_closures": [
            closure
            for closure in ["cost_basis_lcETH", "lceth_staking_redemption_terms"]
            if records and (closure != "lceth_staking_redemption_terms" or latest_terms)
        ],
        "ledger_mutation_preview": "append lcETH cost/reward records and update staking redemption fields after human confirmation",
    }


def build_us_cash_preview(rows: list[dict[str, str]]) -> dict[str, Any]:
    parsed_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        parsed_rows.append({
            "source_row": index,
            "as_of": row.get("as_of"),
            "broker": row.get("broker"),
            "settled_cash_usd": as_float(row.get("settled_cash_usd")),
            "buying_power_usd": as_float(row.get("buying_power_usd")),
            "unsettled_cash_usd": as_float(row.get("unsettled_cash_usd")),
            "pending_orders_usd": as_float(row.get("pending_orders_usd")),
            "margin_enabled": bool_value(row.get("margin_enabled")),
            "shorting_enabled": bool_value(row.get("shorting_enabled")),
            "options_enabled": bool_value(row.get("options_enabled")),
            "source_file": row.get("source_file"),
            "data_quality_status": row.get("data_quality_status"),
            "notes": row.get("notes"),
            "human_review_required": True,
        })
    latest = max(parsed_rows, key=lambda item: parse_time(item.get("as_of")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc), default=None)
    return {
        "template_type": "us_equity_cash_rail",
        "proposed_record_count": len(parsed_rows),
        "proposed_records": parsed_rows,
        "proposed_latest_cash_rail_update": latest or {},
        "candidate_gap_closures": ["us_equity_cash_rail_verification"] if latest else [],
        "ledger_mutation_preview": "update us_equity_rail cash fields after human confirmation; keep margin/short/options disabled",
    }


def build_preview(
    *,
    crypto_lots: Path,
    lceth_staking: Path,
    us_cash_rail: Path,
    ledger_path: Path,
) -> dict[str, Any]:
    validation = VALIDATOR.build_validation(crypto_lots, lceth_staking, us_cash_rail)
    if validation.get("status") == "invalid":
        return {
            "generated_at": utc_now(),
            "package_version": "goal-evidence-import-preview-v1",
            "status": "blocked_invalid_validation",
            "read_only": True,
            "mutates_ledger": False,
            "live_orders_enabled": False,
            "human_confirmation_required": True,
            "validation": validation,
            "preview": {},
            "next_steps": [
                "Fix validation errors first.",
                "Re-run goal_evidence_import_validator.py until status is valid or empty_templates.",
                "No ledger preview is generated for invalid evidence.",
            ],
        }

    ledger = load_json(ledger_path) if ledger_path.exists() else {}
    holdings = current_holdings_by_symbol(ledger if isinstance(ledger, dict) else {})
    crypto_rows = read_csv_rows(crypto_lots)
    lceth_rows = read_csv_rows(lceth_staking)
    us_cash_rows = read_csv_rows(us_cash_rail)
    preview = {
        "ledger_path": str(ledger_path),
        "ledger_as_of": (ledger or {}).get("as_of") if isinstance(ledger, dict) else None,
        "crypto_lot_import": build_crypto_lot_preview(crypto_rows, holdings),
        "lceth_staking_lot_import": build_lceth_preview(lceth_rows, holdings),
        "us_equity_cash_rail": build_us_cash_preview(us_cash_rows),
    }
    proposed_closures = set()
    for section in preview.values():
        if not isinstance(section, dict):
            continue
        proposed_closures.update(section.get("candidate_gap_closures", []))
        for item in section.get("asset_summaries") or []:
            if isinstance(item, dict) and item.get("candidate_gap_closure"):
                proposed_closures.add(item["candidate_gap_closure"])
    proposed_closures_sorted = sorted(proposed_closures)
    proposed_change_count = (
        preview["crypto_lot_import"]["proposed_lot_count"]
        + preview["lceth_staking_lot_import"]["proposed_record_count"]
        + preview["us_equity_cash_rail"]["proposed_record_count"]
    )
    return {
        "generated_at": utc_now(),
        "package_version": "goal-evidence-import-preview-v1",
        "status": "empty_preview" if proposed_change_count == 0 else "ready_for_human_review",
        "read_only": True,
        "mutates_ledger": False,
        "live_orders_enabled": False,
        "human_confirmation_required": True,
        "validation": validation,
        "summary": {
            "proposed_change_count": proposed_change_count,
            "proposed_gap_closures": proposed_closures_sorted,
            "can_close_gaps_without_human_review": False,
            "can_authorize_trade": False,
            "ledger_update_allowed_by_this_script": False,
        },
        "preview": preview,
        "human_review_checklist": [
            "Confirm every source_file points to a broker/exchange/wallet export or timestamped screenshot.",
            "Confirm crypto lot sides, quantities, fees, and timestamps match the source documents.",
            "Confirm lcETH redemption terms are from Coinbase/provider account pages, not public estimates.",
            "Confirm US equity margin, shorting, and options permissions are false.",
            "After review, use a separate human-confirmed import step; this preview does not edit the ledger.",
        ],
        "plain_term_notes": {
            "import_preview": "导入预览，只告诉你如果人工确认后会补哪些字段，不自动写账本。",
            "proposed_gap_closure": "候选缺口关闭项，表示证据可能足够补齐某个数据缺口，但仍需人工确认。",
            "mutates_ledger": "是否修改账本。本脚本永远是 false。",
        },
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return ""
        return str(value).replace("\n", " ").replace("|", "\\|")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(cell(part) for part in row) + " |" for row in rows)
    return "\n".join(lines)


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    validation_summary = (payload.get("validation") or {}).get("summary") or {}
    preview = payload.get("preview") or {}
    crypto = preview.get("crypto_lot_import") or {}
    lceth = preview.get("lceth_staking_lot_import") or {}
    us_cash = preview.get("us_equity_cash_rail") or {}
    asset_rows = []
    for item in crypto.get("asset_summaries") or []:
        asset_rows.append([
            item.get("asset"),
            item.get("rows"),
            item.get("current_ledger_quantity"),
            item.get("signed_quantity_delta"),
            money(item.get("known_cost_basis_delta_usd")),
            item.get("candidate_gap_closure"),
        ])
    latest_cash = us_cash.get("proposed_latest_cash_rail_update") or {}
    return "\n".join([
        "# Goal Evidence Import Preview",
        "",
        "本预览只读：不导入账本、不下单、不移动资金。它只是把通过验证的 CSV 转成可人工复核的 proposed updates。",
        "",
        "## Summary",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["status", payload.get("status")],
                ["validation_status", (payload.get("validation") or {}).get("status")],
                ["validation_rows", validation_summary.get("row_count")],
                ["proposed_change_count", summary.get("proposed_change_count")],
                ["proposed_gap_closures", ", ".join(summary.get("proposed_gap_closures") or [])],
                ["mutates_ledger", payload.get("mutates_ledger")],
                ["live_orders_enabled", payload.get("live_orders_enabled")],
                ["human_confirmation_required", payload.get("human_confirmation_required")],
            ],
        ),
        "",
        "## Crypto Lot Preview",
        "",
        markdown_table(
            ["Asset", "Rows", "Ledger Qty", "Qty Delta", "Known Cost Delta", "Candidate Closure"],
            asset_rows,
        ) if asset_rows else "No crypto lot rows.",
        "",
        "## lcETH Preview",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["record_count", lceth.get("proposed_record_count")],
                ["current_ledger_quantity", lceth.get("current_ledger_quantity")],
                ["current_receipt_conversion_ratio", lceth.get("current_receipt_conversion_ratio")],
                ["proposed_latest_redemption_terms", json.dumps(lceth.get("proposed_latest_redemption_terms") or {}, ensure_ascii=False)],
            ],
        ),
        "",
        "## US Equity Cash Rail Preview",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["record_count", us_cash.get("proposed_record_count")],
                ["broker", latest_cash.get("broker")],
                ["as_of", latest_cash.get("as_of")],
                ["settled_cash_usd", money(latest_cash.get("settled_cash_usd"))],
                ["buying_power_usd", money(latest_cash.get("buying_power_usd"))],
                ["margin_enabled", latest_cash.get("margin_enabled")],
                ["shorting_enabled", latest_cash.get("shorting_enabled")],
                ["options_enabled", latest_cash.get("options_enabled")],
            ],
        ),
        "",
        "## Human Review Checklist",
        "",
        "\n".join(f"- {item}" for item in payload.get("human_review_checklist") or payload.get("next_steps") or []),
        "",
        "## Term Notes",
        "",
        "\n".join(f"- `{key}`: {value}" for key, value in (payload.get("plain_term_notes") or {}).items()),
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a read-only preview for validated goal-evidence CSV imports")
    parser.add_argument("--templates-dir", default=str(DEFAULT_TEMPLATES_DIR))
    parser.add_argument("--crypto-lots", default="")
    parser.add_argument("--lceth-staking", default="")
    parser.add_argument("--us-cash-rail", default="")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    templates_dir = Path(args.templates_dir)
    default_name = f"goal_evidence_import_preview_{compact_now()}"
    output_json = Path(args.output_json) if args.output_json else MANUAL_ROOT / "experiments" / f"{default_name}.json"
    output_md = Path(args.output_md) if args.output_md else MANUAL_ROOT / "reports" / f"{default_name}.md"
    payload = build_preview(
        crypto_lots=Path(args.crypto_lots) if args.crypto_lots else templates_dir / "crypto_lot_import_template.csv",
        lceth_staking=Path(args.lceth_staking) if args.lceth_staking else templates_dir / "lceth_staking_lot_import_template.csv",
        us_cash_rail=Path(args.us_cash_rail) if args.us_cash_rail else templates_dir / "us_equity_cash_rail_template.csv",
        ledger_path=Path(args.ledger),
    )
    write_json(output_json, payload)
    write_text(output_md, render_markdown(payload))
    payload["output_json"] = str(output_json)
    payload["output_md"] = str(output_md)
    if args.format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") in {"ready_for_human_review", "empty_preview"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
