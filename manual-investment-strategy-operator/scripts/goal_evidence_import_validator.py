#!/usr/bin/env python3
"""Validate user-filled evidence CSVs before any ledger import.

This is intentionally read-only. It checks whether the evidence templates have
enough well-formed rows to close portfolio evidence blockers after human review.
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
DEFAULT_TEMPLATES_DIR = MANUAL_ROOT / "import_templates"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PORTFOLIO_PACKAGER = load_module(SCRIPT_DIR / "portfolio_evidence_gap_packager.py", "portfolio_evidence_gap_packager_for_import_validator")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_time(value: Any) -> bool:
    if value in (None, ""):
        return False
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt.datetime.fromisoformat(text)
        return True
    except ValueError:
        return False


def bool_value(value: Any) -> bool | None:
    text = str(value or "").strip().lower()
    if text in {"true", "yes", "y", "1", "enabled"}:
        return True
    if text in {"false", "no", "n", "0", "disabled"}:
        return False
    return None


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]], str | None]:
    if not path.exists():
        return [], [], "file_missing"
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        rows = [
            {str(key): (value or "").strip() for key, value in row.items()}
            for row in reader
            if any((value or "").strip() for value in row.values())
        ]
    return headers, rows, None


def missing_columns(headers: list[str], required: list[str]) -> list[str]:
    return [column for column in required if column not in headers]


def extra_columns(headers: list[str], required: list[str]) -> list[str]:
    return [column for column in headers if column not in required]


def add_issue(issues: list[dict[str, Any]], row_number: int | None, field: str, message: str, severity: str = "error") -> None:
    issues.append({
        "row_number": row_number,
        "field": field,
        "message": message,
        "severity": severity,
    })


def validate_crypto_lots(path: Path) -> dict[str, Any]:
    required = PORTFOLIO_PACKAGER.CRYPTO_LOT_COLUMNS
    headers, rows, error = read_csv(path)
    issues: list[dict[str, Any]] = []
    for column in missing_columns(headers, required):
        add_issue(issues, None, column, "missing required column")
    for column in extra_columns(headers, required):
        add_issue(issues, None, column, "extra column ignored", severity="warning")
    valid_assets: set[str] = set()
    accepted_sides = {"buy", "sell", "transfer_in", "transfer_out", "reward", "airdrop", "staking_reward"}
    for index, row in enumerate(rows, start=2):
        asset = row.get("asset", "").upper()
        if not asset:
            add_issue(issues, index, "asset", "asset is required")
        else:
            valid_assets.add(asset)
        if not parse_time(row.get("executed_at")):
            add_issue(issues, index, "executed_at", "executed_at must be ISO-like timestamp")
        side = row.get("side", "").lower()
        if side not in accepted_sides:
            add_issue(issues, index, "side", f"side should be one of {sorted(accepted_sides)}")
        quantity = as_float(row.get("quantity"))
        if quantity is None or quantity <= 0:
            add_issue(issues, index, "quantity", "quantity must be positive")
        fill_price = as_float(row.get("fill_price_usd"))
        gross = as_float(row.get("gross_amount_usd"))
        net_cost = as_float(row.get("net_cost_basis_usd"))
        if fill_price is None and gross is None and net_cost is None:
            add_issue(issues, index, "fill_price_usd", "provide fill_price_usd, gross_amount_usd, or net_cost_basis_usd")
        if not row.get("source_file"):
            add_issue(issues, index, "source_file", "source_file is required for audit trail")
        if row.get("data_quality_status") not in {"verified", "broker_export", "exchange_export", "wallet_export", "manual_reviewed"}:
            add_issue(issues, index, "data_quality_status", "data_quality_status should be verified/export/manual_reviewed")
    error_count = sum(1 for item in issues if item["severity"] == "error")
    return {
        "template_type": "crypto_lot_import",
        "path": str(path),
        "status": "file_missing" if error else "empty_template" if not rows else "valid" if error_count == 0 else "invalid",
        "row_count": len(rows),
        "valid_assets": sorted(valid_assets),
        "candidate_gap_closures": [
            f"cost_basis_{asset}" for asset in sorted(valid_assets) if asset in {"ADA", "SOL", "NIGHT", "ETH", "USDT"}
        ],
        "issues": issues,
    }


def validate_lceth(path: Path) -> dict[str, Any]:
    required = PORTFOLIO_PACKAGER.LCETH_STAKING_COLUMNS
    headers, rows, error = read_csv(path)
    issues: list[dict[str, Any]] = []
    for column in missing_columns(headers, required):
        add_issue(issues, None, column, "missing required column")
    for column in extra_columns(headers, required):
        add_issue(issues, None, column, "extra column ignored", severity="warning")
    has_cost_or_quantity = False
    has_redemption_terms = False
    for index, row in enumerate(rows, start=2):
        if not row.get("provider"):
            add_issue(issues, index, "provider", "provider is required")
        if not parse_time(row.get("executed_at")):
            add_issue(issues, index, "executed_at", "executed_at must be ISO-like timestamp")
        lceth_qty = as_float(row.get("lceth_quantity"))
        underlying = as_float(row.get("underlying_eth_quantity"))
        reward_qty = as_float(row.get("reward_quantity"))
        if not any(value is not None and value > 0 for value in [lceth_qty, underlying, reward_qty]):
            add_issue(issues, index, "lceth_quantity", "provide lcETH, underlying ETH, or reward quantity")
        else:
            has_cost_or_quantity = True
        conversion = as_float(row.get("receipt_conversion_ratio"))
        redeemable = as_float(row.get("redeemable_underlying_quantity"))
        unstake_delay = as_float(row.get("estimated_unstake_delay_days"))
        if conversion is not None and conversion > 0 and redeemable is not None and redeemable >= 0 and unstake_delay is not None and unstake_delay >= 0:
            has_redemption_terms = True
        if not row.get("source_file"):
            add_issue(issues, index, "source_file", "source_file is required for audit trail")
        if row.get("data_quality_status") not in {"verified", "coinbase_export", "provider_export", "manual_reviewed"}:
            add_issue(issues, index, "data_quality_status", "data_quality_status should be verified/provider_export/manual_reviewed")
    error_count = sum(1 for item in issues if item["severity"] == "error")
    closures = []
    if has_cost_or_quantity:
        closures.append("cost_basis_lcETH")
    if has_redemption_terms:
        closures.append("lceth_staking_redemption_terms")
    return {
        "template_type": "lceth_staking_lot_import",
        "path": str(path),
        "status": "file_missing" if error else "empty_template" if not rows else "valid" if error_count == 0 else "invalid",
        "row_count": len(rows),
        "has_cost_or_quantity": has_cost_or_quantity,
        "has_redemption_terms": has_redemption_terms,
        "candidate_gap_closures": closures,
        "issues": issues,
    }


def validate_us_cash(path: Path) -> dict[str, Any]:
    required = PORTFOLIO_PACKAGER.US_EQUITY_CASH_RAIL_COLUMNS
    headers, rows, error = read_csv(path)
    issues: list[dict[str, Any]] = []
    for column in missing_columns(headers, required):
        add_issue(issues, None, column, "missing required column")
    for column in extra_columns(headers, required):
        add_issue(issues, None, column, "extra column ignored", severity="warning")
    verified_rows = 0
    unsafe_permission_rows = 0
    for index, row in enumerate(rows, start=2):
        if not parse_time(row.get("as_of")):
            add_issue(issues, index, "as_of", "as_of must be ISO-like timestamp")
        if not row.get("broker"):
            add_issue(issues, index, "broker", "broker is required")
        settled = as_float(row.get("settled_cash_usd"))
        buying_power = as_float(row.get("buying_power_usd"))
        if settled is None or settled < 0:
            add_issue(issues, index, "settled_cash_usd", "settled_cash_usd must be non-negative")
        if buying_power is None or buying_power < 0:
            add_issue(issues, index, "buying_power_usd", "buying_power_usd must be non-negative")
        permissions = {
            "margin_enabled": bool_value(row.get("margin_enabled")),
            "shorting_enabled": bool_value(row.get("shorting_enabled")),
            "options_enabled": bool_value(row.get("options_enabled")),
        }
        for field, value in permissions.items():
            if value is None:
                add_issue(issues, index, field, "permission flag must be true/false")
            elif value is True:
                unsafe_permission_rows += 1
                add_issue(issues, index, field, "scope forbids margin/short/options for this strategy")
        if not row.get("source_file"):
            add_issue(issues, index, "source_file", "source_file is required for audit trail")
        if row.get("data_quality_status") in {"verified", "broker_export", "broker_screenshot", "manual_reviewed"}:
            verified_rows += 1
        else:
            add_issue(issues, index, "data_quality_status", "data_quality_status should be verified/broker_export/broker_screenshot/manual_reviewed")
    error_count = sum(1 for item in issues if item["severity"] == "error")
    return {
        "template_type": "us_equity_cash_rail",
        "path": str(path),
        "status": "file_missing" if error else "empty_template" if not rows else "valid" if error_count == 0 else "invalid",
        "row_count": len(rows),
        "verified_rows": verified_rows,
        "unsafe_permission_rows": unsafe_permission_rows,
        "candidate_gap_closures": ["us_equity_cash_rail_verification"] if verified_rows and not unsafe_permission_rows and error_count == 0 else [],
        "issues": issues,
    }


def build_validation(crypto_lots: Path, lceth_staking: Path, us_cash_rail: Path) -> dict[str, Any]:
    reports = [
        validate_crypto_lots(crypto_lots),
        validate_lceth(lceth_staking),
        validate_us_cash(us_cash_rail),
    ]
    all_closures = sorted({closure for report in reports for closure in report.get("candidate_gap_closures") or []})
    error_count = sum(1 for report in reports for issue in report.get("issues") or [] if issue.get("severity") == "error")
    warning_count = sum(1 for report in reports for issue in report.get("issues") or [] if issue.get("severity") == "warning")
    row_count = sum(int(report.get("row_count") or 0) for report in reports)
    return {
        "generated_at": utc_now(),
        "package_version": "goal-evidence-import-validator-v1",
        "read_only": True,
        "mutates_ledger": False,
        "live_orders_enabled": False,
        "human_confirmation_required_before_import": True,
        "status": "empty_templates" if row_count == 0 else "valid" if error_count == 0 else "invalid",
        "summary": {
            "row_count": row_count,
            "error_count": error_count,
            "warning_count": warning_count,
            "candidate_gap_closures": all_closures,
            "ready_for_human_import_review": row_count > 0 and error_count == 0,
        },
        "template_reports": reports,
        "next_steps": [
            "Fill only rows backed by broker/exchange/wallet exports or timestamped screenshots.",
            "Re-run this validator until status=valid.",
            "Human review is still required before any ledger import or strategy upgrade.",
        ],
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
    template_rows = []
    issue_rows = []
    for report in payload.get("template_reports") or []:
        template_rows.append([
            report.get("template_type"),
            report.get("status"),
            report.get("row_count"),
            ", ".join(report.get("candidate_gap_closures") or []),
        ])
        for issue in report.get("issues") or []:
            issue_rows.append([
                report.get("template_type"),
                issue.get("row_number"),
                issue.get("field"),
                issue.get("severity"),
                issue.get("message"),
            ])
    return "\n".join([
        "# Goal Evidence Import Validation",
        "",
        "本验证只读，不导入账本、不下单、不移动资金。",
        "",
        "## Summary",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["status", payload.get("status")],
                ["row_count", summary.get("row_count")],
                ["error_count", summary.get("error_count")],
                ["warning_count", summary.get("warning_count")],
                ["candidate_gap_closures", ", ".join(summary.get("candidate_gap_closures") or [])],
                ["ready_for_human_import_review", summary.get("ready_for_human_import_review")],
            ],
        ),
        "",
        "## Template Reports",
        "",
        markdown_table(["Template", "Status", "Rows", "Candidate Closures"], template_rows),
        "",
        "## Issues",
        "",
        markdown_table(["Template", "Row", "Field", "Severity", "Message"], issue_rows) if issue_rows else "No issues.",
        "",
        "## Next Steps",
        "",
        "\n".join(f"- {step}" for step in payload.get("next_steps") or []),
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate goal evidence CSV templates before import")
    parser.add_argument("--templates-dir", default=str(DEFAULT_TEMPLATES_DIR))
    parser.add_argument("--crypto-lots", default="")
    parser.add_argument("--lceth-staking", default="")
    parser.add_argument("--us-cash-rail", default="")
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    templates_dir = Path(args.templates_dir)
    payload = build_validation(
        crypto_lots=Path(args.crypto_lots) if args.crypto_lots else templates_dir / "crypto_lot_import_template.csv",
        lceth_staking=Path(args.lceth_staking) if args.lceth_staking else templates_dir / "lceth_staking_lot_import_template.csv",
        us_cash_rail=Path(args.us_cash_rail) if args.us_cash_rail else templates_dir / "us_equity_cash_rail_template.csv",
    )
    if args.output_json:
        write_json(Path(args.output_json), payload)
    if args.output_md:
        write_text(Path(args.output_md), render_markdown(payload))
    if args.format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") in {"valid", "empty_templates"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
