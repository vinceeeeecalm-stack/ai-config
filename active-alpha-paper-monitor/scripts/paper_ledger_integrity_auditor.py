#!/usr/bin/env python3
"""Audit paper ledger integrity and optionally repair legacy paper metadata.

This script is paper-only. It never calls private APIs and never places orders.
By default it is read-only. The repair mode only backfills simulated metadata for
legacy paper positions/orders that were already recorded in the local paper ledger.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
LEDGER_PATH = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
REPORT_DIR = ACTIVE_ROOT / "reports"
EXPERIMENT_DIR = ACTIVE_ROOT / "experiments"
CONFIG_PATH = ACTIVE_ROOT / "config" / "active_alpha_monitor_config.json"


REQUIRED_ORDER_FIELDS = [
    "paper_order_id",
    "paper_trade_id",
    "created_at",
    "symbol",
    "side",
    "status",
    "requested_notional_usd",
    "live_orders_enabled",
    "private_api_used",
]

API_STYLE_ORDER_FIELDS = [
    "type",
    "time_in_force",
    "simulated_status",
    "simulated_api",
    "average_fill_price",
    "commission_usd",
    "commission_bps",
    "slippage_bps",
    "quote_status",
    "order_reason",
]

REALISTIC_POSITION_FIELDS = [
    "entry_commission_usd",
    "commission_bps",
    "entry_slippage_bps",
    "realistic_execution_enabled",
    "execution_model",
]

MARKET_CONTEXT_POSITION_FIELDS = [
    "market_regime",
    "market_atmosphere",
    "short_term_state",
    "sentiment_state",
    "market_context_at_entry",
]


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def local_now() -> dt.datetime:
    return utc_now().astimezone(dt.timezone(dt.timedelta(hours=8)))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execution_cost_settings(config: dict[str, Any]) -> dict[str, float]:
    for key in ("fast_crypto_paper_auto_trader", "daily_crypto_paper_auto_trader", "sunday_crypto_realistic_paper_loop"):
        section = config.get(key) or {}
        if section:
            return {
                "commission_bps": float(section.get("commission_bps", 10.0) or 10.0),
                "slippage_bps": float(section.get("base_slippage_bps", 8.0) or 8.0),
            }
    return {"commission_bps": 10.0, "slippage_bps": 8.0}


def order_price(order: dict[str, Any]) -> float | None:
    for key in ("average_fill_price", "fill_price", "execution_price"):
        value = order.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def order_quantity(order: dict[str, Any]) -> float | None:
    for key in ("executed_quantity", "filled_quantity"):
        value = order.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def position_by_trade_id(ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    positions: dict[str, dict[str, Any]] = {}
    for collection in ("open_positions", "closed_trades"):
        for pos in ledger.get(collection) or []:
            trade_id = pos.get("paper_trade_id")
            if trade_id:
                positions[str(trade_id)] = pos
    return positions


def inferred_order_price(order: dict[str, Any], pos: dict[str, Any] | None) -> float:
    existing = order_price(order)
    if existing is not None:
        return existing
    side = str(order.get("side") or "").upper()
    if pos:
        key = "exit_price" if side == "SELL" else "entry_price"
        return safe_float(pos.get(key), 0.0)
    return 0.0


def inferred_order_quantity(order: dict[str, Any], pos: dict[str, Any] | None) -> float:
    existing = order_quantity(order)
    if existing is not None:
        return existing
    if pos:
        return safe_float(pos.get("quantity"), 0.0)
    return 0.0


def inferred_order_notional(order: dict[str, Any], pos: dict[str, Any] | None) -> float:
    existing = order.get("requested_notional_usd")
    if isinstance(existing, (int, float)):
        return float(existing)
    side = str(order.get("side") or "").upper()
    if pos and side == "BUY":
        notional = safe_float(pos.get("notional_usd"), 0.0)
        if notional > 0:
            return notional
    price = inferred_order_price(order, pos)
    quantity = inferred_order_quantity(order, pos)
    gross = price * quantity
    if gross > 0:
        return round(gross, 6)
    if pos:
        return safe_float(pos.get("notional_usd"), 0.0)
    return 0.0


def inferred_commission_bps(order: dict[str, Any], notional: float, fallback_bps: float) -> float:
    commission = safe_float(order.get("commission_usd"), 0.0)
    if commission > 0 and notional > 0:
        return round(commission / notional * 10000.0, 6)
    return fallback_bps


def backfill_order_metadata(
    order: dict[str, Any],
    pos: dict[str, Any] | None,
    settings: dict[str, float],
    quote_reason: str,
) -> list[str]:
    missing = [field for field in [*REQUIRED_ORDER_FIELDS, *API_STYLE_ORDER_FIELDS] if field not in order]
    if not missing:
        order["live_orders_enabled"] = False
        order["private_api_used"] = False
        return []

    notional = inferred_order_notional(order, pos)
    price = inferred_order_price(order, pos)
    quantity = inferred_order_quantity(order, pos)
    commission_bps = inferred_commission_bps(order, notional, settings["commission_bps"])
    commission = safe_float(order.get("commission_usd"), round(notional * commission_bps / 10000.0, 6))

    order.setdefault("requested_notional_usd", notional)
    order.setdefault("type", "MARKET")
    order.setdefault("time_in_force", "IOC")
    order.setdefault("status", "FILLED")
    order.setdefault("simulated_status", "PAPER_FILLED")
    order.setdefault("simulated_api", True)
    order.setdefault("executed_quantity", quantity)
    order.setdefault("filled_quantity", quantity)
    order.setdefault("average_fill_price", price)
    order.setdefault("fill_price", price)
    order.setdefault("commission_usd", commission)
    order.setdefault("commission_bps", commission_bps)
    order.setdefault("spread_bps", None)
    order.setdefault("slippage_bps", settings["slippage_bps"])
    order.setdefault("depth_1pct_usd", None)
    order.setdefault("quote_status", "legacy_backfilled_without_order_book")
    order.setdefault("quote_reasons", [quote_reason])
    order.setdefault("order_reason", order.get("reason") or "legacy_backfilled_paper_order")
    order.setdefault("notes", "legacy paper metadata backfilled; no private or live endpoint called")
    order["live_orders_enabled"] = False
    order["private_api_used"] = False
    return missing


def audit_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    positions = position_by_trade_id(ledger)
    orders = ledger.get("paper_orders") or []
    findings: list[dict[str, Any]] = []
    order_ids: set[str] = set()
    duplicate_order_ids: list[str] = []

    if ledger.get("live_orders_enabled") is True:
        findings.append({"severity": "blocked", "rule": "ledger_live_orders_enabled_true"})
    if ledger.get("private_api_used") is True:
        findings.append({"severity": "blocked", "rule": "ledger_private_api_used_true"})

    for order in orders:
        order_id = str(order.get("paper_order_id") or "")
        if order_id in order_ids:
            duplicate_order_ids.append(order_id)
        if order_id:
            order_ids.add(order_id)
        for field in REQUIRED_ORDER_FIELDS:
            if field not in order:
                findings.append(
                    {
                        "severity": "warning",
                        "rule": "order_missing_required_field",
                        "paper_order_id": order_id,
                        "field": field,
                    }
                )
        for field in API_STYLE_ORDER_FIELDS:
            if field not in order:
                findings.append(
                    {
                        "severity": "warning",
                        "rule": "order_missing_api_style_field",
                        "paper_order_id": order_id,
                        "paper_trade_id": order.get("paper_trade_id"),
                        "field": field,
                    }
                )
        if order.get("live_orders_enabled") is True:
            findings.append({"severity": "blocked", "rule": "order_live_orders_enabled_true", "paper_order_id": order_id})
        if order.get("private_api_used") is True:
            findings.append({"severity": "blocked", "rule": "order_private_api_used_true", "paper_order_id": order_id})
        trade_id = order.get("paper_trade_id")
        if trade_id and str(trade_id) not in positions:
            findings.append(
                {
                    "severity": "warning",
                    "rule": "order_trade_id_not_found_in_positions",
                    "paper_order_id": order_id,
                    "paper_trade_id": trade_id,
                }
            )

    for pos in ledger.get("open_positions") or []:
        trade_id = str(pos.get("paper_trade_id") or "")
        if pos.get("entry_order_id") and str(pos.get("entry_order_id")) not in order_ids:
            findings.append(
                {
                    "severity": "warning",
                    "rule": "position_entry_order_id_not_found",
                    "paper_trade_id": trade_id,
                    "entry_order_id": pos.get("entry_order_id"),
                }
            )
        for field in REALISTIC_POSITION_FIELDS:
            if field not in pos:
                findings.append(
                    {
                        "severity": "warning",
                        "rule": "open_position_missing_realistic_execution_field",
                        "paper_trade_id": trade_id,
                        "symbol": pos.get("symbol"),
                        "field": field,
                    }
                )
        for field in MARKET_CONTEXT_POSITION_FIELDS:
            if field not in pos:
                findings.append(
                    {
                        "severity": "warning",
                        "rule": "open_position_missing_market_context_field",
                        "paper_trade_id": trade_id,
                        "symbol": pos.get("symbol"),
                        "field": field,
                    }
                )
        if pos.get("live_orders_enabled") is True:
            findings.append({"severity": "blocked", "rule": "position_live_orders_enabled_true", "paper_trade_id": trade_id})
        if pos.get("private_api_used") is True:
            findings.append({"severity": "blocked", "rule": "position_private_api_used_true", "paper_trade_id": trade_id})

    if duplicate_order_ids:
        for order_id in duplicate_order_ids:
            findings.append({"severity": "warning", "rule": "duplicate_paper_order_id", "paper_order_id": order_id})

    blocked_count = sum(1 for item in findings if item.get("severity") == "blocked")
    warning_count = sum(1 for item in findings if item.get("severity") == "warning")
    return {
        "status": "blocked" if blocked_count else ("warning" if warning_count else "pass"),
        "blocked_count": blocked_count,
        "warning_count": warning_count,
        "order_count": len(orders),
        "open_position_count": len(ledger.get("open_positions") or []),
        "closed_trade_count": len(ledger.get("closed_trades") or []),
        "findings": findings,
    }


def repair_legacy_open_orders(ledger: dict[str, Any], config: dict[str, Any], now: dt.datetime) -> list[dict[str, Any]]:
    settings = execution_cost_settings(config)
    orders = ledger.get("paper_orders") or []
    orders_by_trade_id = {str(order.get("paper_trade_id")): order for order in orders if order.get("paper_trade_id")}
    repairs: list[dict[str, Any]] = []

    for pos in ledger.get("open_positions") or []:
        trade_id = str(pos.get("paper_trade_id") or "")
        if not trade_id:
            continue
        order = orders_by_trade_id.get(trade_id)
        if not order:
            continue
        missing_order_fields = [field for field in API_STYLE_ORDER_FIELDS if field not in order]
        missing_position_fields = [field for field in REALISTIC_POSITION_FIELDS if field not in pos]
        if not missing_order_fields and not missing_position_fields:
            continue

        commission_bps = float(pos.get("commission_bps") or settings["commission_bps"])
        slippage_bps = float(pos.get("entry_slippage_bps") or settings["slippage_bps"])
        notional = float(pos.get("notional_usd") or order.get("requested_notional_usd") or 0.0)
        commission = float(pos.get("entry_commission_usd") or round(notional * commission_bps / 10000.0, 6))
        price = float(pos.get("entry_price") or order_price(order) or 0.0)
        quantity = float(pos.get("quantity") or order_quantity(order) or 0.0)

        pos.setdefault("entry_commission_usd", commission)
        pos.setdefault("commission_bps", commission_bps)
        pos.setdefault("entry_slippage_bps", slippage_bps)
        pos.setdefault("entry_spread_bps", None)
        pos.setdefault("entry_depth_1pct_usd", None)
        pos.setdefault("entry_quote_status", "legacy_backfilled_without_order_book")
        pos.setdefault("entry_quote_reasons", ["legacy_near_miss_order_metadata_backfill"])
        pos.setdefault("realistic_execution_enabled", True)
        pos.setdefault(
            "execution_model",
            {
                "source": "legacy_backfill",
                "uses_commission": True,
                "uses_slippage": True,
                "uses_order_book_spread": False,
                "commission_bps": commission_bps,
                "slippage_bps": slippage_bps,
                "operator_note": "Backfilled legacy paper order metadata; no live or private API was used.",
            },
        )

        order.setdefault("type", "MARKET")
        order.setdefault("time_in_force", "IOC")
        order.setdefault("status", "FILLED")
        order.setdefault("simulated_status", "PAPER_FILLED")
        order.setdefault("simulated_api", True)
        order.setdefault("executed_quantity", quantity)
        order.setdefault("filled_quantity", quantity)
        order.setdefault("average_fill_price", price)
        order.setdefault("fill_price", price)
        order.setdefault("commission_usd", commission)
        order.setdefault("commission_bps", commission_bps)
        order.setdefault("spread_bps", None)
        order.setdefault("slippage_bps", slippage_bps)
        order.setdefault("depth_1pct_usd", None)
        order.setdefault("quote_status", "legacy_backfilled_without_order_book")
        order.setdefault("quote_reasons", ["legacy_near_miss_order_metadata_backfill"])
        order.setdefault("order_reason", order.get("reason") or "legacy_backfilled_paper_entry")
        order.setdefault("notes", "legacy paper metadata backfilled; no private or live endpoint called")
        order["live_orders_enabled"] = False
        order["private_api_used"] = False

        repairs.append(
            {
                "paper_trade_id": trade_id,
                "paper_order_id": order.get("paper_order_id"),
                "symbol": pos.get("symbol"),
                "missing_order_fields": missing_order_fields,
                "missing_position_fields": missing_position_fields,
            }
        )

    if repairs:
        ledger.setdefault("events", []).append(
            {
                "event_type": "ledger_integrity_backfill",
                "created_at": now.isoformat(),
                "repair_count": len(repairs),
                "live_orders_enabled": False,
                "private_api_used": False,
                "notes": "Backfilled simulated metadata for legacy open paper orders only.",
            }
        )
        ledger["updated_at"] = now.isoformat()
    return repairs


def repair_legacy_orders(ledger: dict[str, Any], config: dict[str, Any], now: dt.datetime) -> list[dict[str, Any]]:
    settings = execution_cost_settings(config)
    positions = position_by_trade_id(ledger)
    repairs: list[dict[str, Any]] = []

    for order in ledger.get("paper_orders") or []:
        trade_id = str(order.get("paper_trade_id") or "")
        pos = positions.get(trade_id)
        missing_fields = backfill_order_metadata(
            order,
            pos,
            settings,
            "legacy_full_order_metadata_backfill",
        )
        if missing_fields:
            repairs.append(
                {
                    "paper_trade_id": trade_id,
                    "paper_order_id": order.get("paper_order_id"),
                    "symbol": order.get("symbol") or (pos or {}).get("symbol"),
                    "side": order.get("side"),
                    "missing_order_fields": missing_fields,
                }
            )

    if repairs:
        ledger.setdefault("events", []).append(
            {
                "event_type": "ledger_integrity_backfill",
                "created_at": now.isoformat(),
                "repair_count": len(repairs),
                "repair_scope": "legacy_paper_orders",
                "live_orders_enabled": False,
                "private_api_used": False,
                "notes": "Backfilled simulated metadata for legacy paper orders only; no PnL, fill, or real account state was changed.",
            }
        )
        ledger["updated_at"] = now.isoformat()
    return repairs


def render_report(payload: dict[str, Any]) -> str:
    audit = payload["audit"]
    lines = [
        f"# Paper Ledger Integrity Audit | {payload['run_id']}",
        "",
        "Paper-only ledger integrity check. No live orders or private trading APIs were used.",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Status | `{audit['status']}` |",
        f"| Repairs applied | `{len(payload.get('repairs_applied') or [])}` |",
        f"| Orders | `{audit['order_count']}` |",
        f"| Open positions | `{audit['open_position_count']}` |",
        f"| Closed trades | `{audit['closed_trade_count']}` |",
        f"| Warnings | `{audit['warning_count']}` |",
        f"| Blocked findings | `{audit['blocked_count']}` |",
        f"| live_orders_enabled | `{payload['live_orders_enabled']}` |",
        "",
    ]
    if payload.get("repairs_applied"):
        lines.extend(["## Repairs Applied", "", "| Trade | Order | Symbol | Missing Order Fields | Missing Position Fields |", "|---|---|---|---|---|"])
        for item in payload["repairs_applied"]:
            lines.append(
                f"| `{item.get('paper_trade_id')}` | `{item.get('paper_order_id')}` | `{item.get('symbol')}` | "
                f"`{', '.join(item.get('missing_order_fields') or []) or '-'}` | "
                f"`{', '.join(item.get('missing_position_fields') or []) or '-'}` |"
            )
        lines.append("")
    lines.extend(["## Findings", "", "| Severity | Rule | Trade | Order | Field |", "|---|---|---|---|---|"])
    for item in audit["findings"][:80]:
        lines.append(
            f"| `{item.get('severity')}` | `{item.get('rule')}` | `{item.get('paper_trade_id', '-')}` | "
            f"`{item.get('paper_order_id', '-')}` | `{item.get('field', '-')}` |"
        )
    if not audit["findings"]:
        lines.append("| pass | none | - | - | - |")
    lines.append("")
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    now = utc_now()
    with tempfile.TemporaryDirectory(prefix="paper-ledger-integrity-") as tmp:
        tmp_path = Path(tmp)
        good_ledger = {
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "live_orders_enabled": False,
            "private_api_used": False,
            "cash_usd": 100.0,
            "equity_usd": 100.0,
            "open_positions": [],
            "closed_trades": [
                {
                    "paper_trade_id": "paper-good-1",
                    "symbol": "GOODUSDT",
                    "status": "closed",
                    "entry_price": 10.0,
                    "exit_price": 10.5,
                    "quantity": 2.0,
                    "notional_usd": 20.0,
                    "realized_pnl_usd": 0.9,
                }
            ],
            "paper_orders": [
                {
                    "paper_order_id": "pord-good-buy",
                    "paper_trade_id": "paper-good-1",
                    "created_at": now.isoformat(),
                    "symbol": "GOODUSDT",
                    "side": "BUY",
                    "status": "FILLED",
                    "requested_notional_usd": 20.0,
                    "live_orders_enabled": False,
                    "private_api_used": False,
                    "type": "MARKET",
                    "time_in_force": "IOC",
                    "simulated_status": "PAPER_FILLED",
                    "simulated_api": True,
                    "average_fill_price": 10.0,
                    "commission_usd": 0.02,
                    "commission_bps": 10.0,
                    "slippage_bps": 8.0,
                    "quote_status": "verified",
                    "order_reason": "self_test",
                }
            ],
        }
        good = audit_ledger(good_ledger)
        assert good["status"] == "pass", good

        good_open_ledger = {
            "live_orders_enabled": False,
            "private_api_used": False,
            "open_positions": [
                {
                    "paper_trade_id": "paper-open-good-1",
                    "symbol": "GOODUSDT",
                    "entry_order_id": "pord-open-good-buy",
                    "entry_commission_usd": 0.025,
                    "commission_bps": 10.0,
                    "entry_slippage_bps": 8.0,
                    "realistic_execution_enabled": True,
                    "execution_model": {"type": "realistic_spot_paper"},
                    "market_regime": "risk_on_momentum",
                    "market_atmosphere": "broad_risk_appetite",
                    "short_term_state": "neutral",
                    "sentiment_state": "positive_catalyst_cluster",
                    "market_context_at_entry": {
                        "market_regime": "risk_on_momentum",
                        "market_atmosphere": "broad_risk_appetite",
                        "short_term_state": "neutral",
                        "sentiment_state": "positive_catalyst_cluster",
                    },
                }
            ],
            "closed_trades": [],
            "paper_orders": [
                {
                    "paper_order_id": "pord-open-good-buy",
                    "paper_trade_id": "paper-open-good-1",
                    "created_at": now.isoformat(),
                    "symbol": "GOODUSDT",
                    "side": "BUY",
                    "status": "FILLED",
                    "requested_notional_usd": 25.0,
                    "live_orders_enabled": False,
                    "private_api_used": False,
                    "type": "MARKET",
                    "time_in_force": "IOC",
                    "simulated_status": "PAPER_FILLED",
                    "simulated_api": True,
                    "average_fill_price": 10.0,
                    "commission_usd": 0.025,
                    "commission_bps": 10.0,
                    "slippage_bps": 8.0,
                    "quote_status": "verified",
                    "order_reason": "self_test",
                }
            ],
        }
        good_open = audit_ledger(good_open_ledger)
        assert good_open["status"] == "pass", good_open

        warning_ledger = {
            "live_orders_enabled": False,
            "private_api_used": False,
            "open_positions": [],
            "closed_trades": [],
            "paper_orders": [{"paper_order_id": "pord-legacy", "paper_trade_id": "missing-trade"}],
        }
        warning = audit_ledger(warning_ledger)
        assert warning["status"] == "warning" and warning["warning_count"] > 0, warning

        missing_context_ledger = dict(good_open_ledger)
        missing_context_ledger["open_positions"] = [dict(good_open_ledger["open_positions"][0])]
        missing_context_ledger["open_positions"][0].pop("market_context_at_entry", None)
        missing_context = audit_ledger(missing_context_ledger)
        assert any(
            item.get("rule") == "open_position_missing_market_context_field"
            and item.get("field") == "market_context_at_entry"
            for item in missing_context["findings"]
        ), missing_context

        blocked_ledger = dict(good_ledger)
        blocked_ledger["live_orders_enabled"] = True
        blocked = audit_ledger(blocked_ledger)
        assert blocked["status"] == "blocked" and blocked["blocked_count"] > 0, blocked

        legacy_order = {"paper_order_id": "pord-legacy", "paper_trade_id": "paper-good-1", "side": "BUY"}
        missing = backfill_order_metadata(
            legacy_order,
            good_ledger["closed_trades"][0],
            {"commission_bps": 10.0, "slippage_bps": 8.0},
            "self_test_backfill",
        )
        assert missing and legacy_order["live_orders_enabled"] is False and legacy_order["simulated_api"] is True, legacy_order
        write_json(tmp_path / "good.json", good_ledger)
    return {
        "status": "ok",
        "good_status": good["status"],
        "good_open_context_status": good_open["status"],
        "missing_context_status": missing_context["status"],
        "warning_status": warning["status"],
        "blocked_status": blocked["status"],
        "backfill_verified": True,
        "uses_temporary_files_only": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit paper ledger integrity")
    parser.add_argument("--ledger", default=str(LEDGER_PATH))
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--repair-legacy-open-orders", action="store_true")
    parser.add_argument("--repair-legacy-orders", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0

    now = utc_now()
    local = local_now()
    run_id = f"{local.strftime('%Y%m%d-%H%M%S')}-paper-ledger-integrity-audit"
    ledger_path = Path(args.ledger)
    ledger = read_json(ledger_path)
    config = read_json(CONFIG_PATH) if CONFIG_PATH.exists() else {}
    repairs: list[dict[str, Any]] = []
    if args.repair_legacy_open_orders:
        repairs = repair_legacy_open_orders(ledger, config, now)
        if repairs:
            write_json(ledger_path, ledger)
    if args.repair_legacy_orders:
        order_repairs = repair_legacy_orders(ledger, config, now)
        repairs.extend(order_repairs)
        if order_repairs:
            write_json(ledger_path, ledger)
    audit = audit_ledger(ledger)
    payload = {
        "run_id": run_id,
        "created_at": now.isoformat(),
        "ledger_path": str(ledger_path),
        "repair_legacy_open_orders": bool(args.repair_legacy_open_orders),
        "repair_legacy_orders": bool(args.repair_legacy_orders),
        "repairs_applied": repairs,
        "audit": audit,
        "live_orders_enabled": False,
        "private_api_used": False,
        "outputs": {},
    }
    stamp = local.strftime("%Y-%m-%d-%H%M%S")
    report_path = REPORT_DIR / f"{stamp}-paper-ledger-integrity-audit.md"
    experiment_path = EXPERIMENT_DIR / f"{run_id}.json"
    payload["outputs"] = {
        "report": str(report_path.relative_to(ACTIVE_ROOT.parent)),
        "experiment": str(experiment_path.relative_to(ACTIVE_ROOT.parent)),
    }
    write_json(experiment_path, payload)
    report = render_report(payload)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    if args.format == "markdown":
        print(report)
    elif args.compact_output:
        print(
            json.dumps(
                {
                    "status": audit["status"],
                    "run_id": run_id,
                    "blocked_count": audit["blocked_count"],
                    "warning_count": audit["warning_count"],
                    "order_count": audit["order_count"],
                    "open_position_count": audit["open_position_count"],
                    "closed_trade_count": audit["closed_trade_count"],
                    "live_orders_enabled": False,
                    "private_api_used": False,
                    "repairs_applied": len(repairs),
                    "outputs": payload["outputs"],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if audit["status"] != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
