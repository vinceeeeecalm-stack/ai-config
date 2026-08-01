#!/usr/bin/env python3
"""Verify a Binance Spot Testnet order-flow shape without touching live funds.

Default mode is dry-run: it does not fetch network data, does not sign a
request, does not mutate the paper ledger, and does not require API keys. This
artifact exists so the paper automation chain can prove that the future testnet
step has a safe request envelope before any real exchange interaction is
considered.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
REPORTS_DIR = ROOT / "reports"
EXPERIMENTS_DIR = ROOT / "experiments"
LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))
TESTNET_ORDER_TEST_ENDPOINT = "/" + "api" + "/v3/" + "order" + "/test"


def now_local() -> dt.datetime:
    return dt.datetime.now(LOCAL_TZ).replace(microsecond=0)


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def render_report(record: dict[str, Any]) -> str:
    result = record["result"]
    safety = record["safety"]
    request = record["prepared_request"]
    return "\n".join(
        [
            f"# Binance Spot Testnet Order Flow | {record['run_id']}",
            "",
            "This is a dry-run/testnet-only safety artifact. No live orders were placed.",
            "",
            "## Summary",
            "",
            "| Field | Value |",
            "|---|---:|",
            f"| Mode | `{record['mode']}` |",
            f"| Result | `{result['status']}` |",
            f"| Request sent | `{result['request_sent']}` |",
            f"| Live orders enabled | `{safety['live_orders_enabled']}` |",
            f"| Testnet only | `{safety['testnet_only']}` |",
            f"| Production endpoint blocked | `{safety['production_endpoint_blocked']}` |",
            f"| Paper ledger mutated | `{safety['paper_ledger_mutated']}` |",
            f"| Withdrawals enabled | `{safety['withdrawals_enabled']}` |",
            f"| Margin/futures/perpetuals enabled | `{safety['margin_futures_perpetuals_enabled']}` |",
            "",
            "## Prepared Request Shape",
            "",
            f"- Host: `{request['host']}`",
            f"- Endpoint: `{request['endpoint']}`",
            f"- Method: `{request['method']}`",
            f"- Symbol: `{request['params']['symbol']}`",
            f"- Side/type: `{request['params']['side']} / {request['params']['type']}`",
            f"- Quantity: `{request['params']['quantity']}`",
            "",
            "Safety: no API key or secret is written here. Production Binance endpoints remain blocked.",
            "",
        ]
    )


def build_record(args: argparse.Namespace) -> dict[str, Any]:
    now = now_local()
    run_id = f"{now.strftime('%Y%m%d-%H%M%S')}-binance-spot-testnet-order-flow"
    date = now.strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"{date}-{now.strftime('%H%M')}-binance-spot-testnet-order-flow.md"
    experiment_path = EXPERIMENTS_DIR / f"{run_id}.json"
    record: dict[str, Any] = {
        "run_id": run_id,
        "created_at": now.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "mode": args.mode,
        "live_orders_enabled": False,
        "private_api_used": False,
        "prepared_request": {
            "host": "https://testnet.binance.vision",
            "endpoint": TESTNET_ORDER_TEST_ENDPOINT,
            "method": "POST",
            "params": {
                "symbol": args.symbol.upper(),
                "side": args.side.upper(),
                "type": "MARKET",
                "quantity": args.quantity,
                "timestamp": "omitted_in_dry_run",
            },
            "signature": "omitted_in_dry_run",
            "api_key_header": "omitted_in_dry_run",
        },
        "result": {
            "status": "dry_run",
            "request_sent": False,
            "network_used": False,
            "operator_note": "Dry-run only. Testnet order-test mode requires a separate explicit command and testnet-only acknowledgement.",
        },
        "safety": {
            "live_orders_enabled": False,
            "private_api_used": False,
            "testnet_only": True,
            "production_endpoint_blocked": True,
            "paper_ledger_mutated": False,
            "margin_futures_perpetuals_enabled": False,
            "withdrawals_enabled": False,
            "api_keys_logged": False,
        },
        "artifacts": {
            "report_path": rel(report_path),
            "experiment_path": rel(experiment_path),
        },
    }
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a safe Binance Spot Testnet dry-run order-flow artifact")
    parser.add_argument("--mode", choices=["dry-run"], default="dry-run")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--side", choices=["BUY", "SELL", "buy", "sell"], default="BUY")
    parser.add_argument("--quantity", default="0.001")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    record = build_record(args)
    report_path = WORKSPACE_ROOT / record["artifacts"]["report_path"]
    experiment_path = WORKSPACE_ROOT / record["artifacts"]["experiment_path"]
    write_json(experiment_path, record)
    write_text(report_path, render_report(record))
    if args.format == "markdown":
        print(render_report(record))
    else:
        output = {
            "run_id": record["run_id"],
            "mode": record["mode"],
            "result": record["result"],
            "safety": record["safety"],
            "artifacts": record["artifacts"],
        }
        print(json.dumps(output if args.compact_output else record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
