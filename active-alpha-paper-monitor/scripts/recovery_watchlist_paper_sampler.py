#!/usr/bin/env python3
"""Convert recovery watchlist trigger plans into tiny paper scout positions.

Paper-only safety layer:
- reads the latest recovery_watchlist_monitor experiment
- never calls private APIs
- opens at most one tiny paper scout when every trigger/liquidity gate passes
- otherwise writes a readable blocked report so the operator sees why no action happened
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS_DIR = ROOT / "experiments"
REPORTS_DIR = ROOT / "reports"
LEDGER_PATH = ROOT / "paper_trades" / "paper_portfolio_ledger.json"
CHINA_TZ = timezone(timedelta(hours=8))

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper_portfolio_engine as ppe  # noqa: E402
import paper_strategy_overlay as pso  # noqa: E402
import paper_testnet_risk_control_auditor as ptrc  # noqa: E402


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    return now_utc().astimezone(CHINA_TZ)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        return None


def rel(path: Path | None) -> str | None:
    if not path:
        return None
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def latest(pattern: str, directory: Path = EXPERIMENTS_DIR) -> Path | None:
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def read_json(path: Path | None, default: Any = None) -> Any:
    if not path or not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return default
        return value
    except Exception:
        return default


def pct_from_prices(target: float, entry: float) -> float:
    if entry <= 0:
        return 0.0
    return (target / entry - 1.0) * 100.0


UNKNOWN_MARKET_CONTEXT_VALUES = {
    "",
    "unknown",
    "unknown_or_not_attached",
    "manual_or_cli_open_unknown",
    "unavailable",
    "none",
    "null",
}


def market_context_from_item(item: dict[str, Any]) -> dict[str, Any]:
    source_context = item.get("market_context") if isinstance(item.get("market_context"), dict) else {}
    return {
        **source_context,
        "market_regime": source_context.get("market_regime") or item.get("market_regime") or item.get("dynamic_market_regime") or "unknown_or_not_attached",
        "market_atmosphere": source_context.get("market_atmosphere") or item.get("market_atmosphere") or "unknown_or_not_attached",
        "short_term_state": source_context.get("short_term_state") or item.get("short_term_state") or "unknown_or_not_attached",
        "sentiment_state": source_context.get("sentiment_state") or item.get("sentiment_state") or "unknown_or_not_attached",
        "source": source_context.get("source") or item.get("market_context_source") or "recovery_watchlist_item_or_unknown",
    }


def market_context_missing_fields(context: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in ("market_regime", "market_atmosphere", "short_term_state", "sentiment_state"):
        value = str((context or {}).get(field) or "").strip().lower()
        if value in UNKNOWN_MARKET_CONTEXT_VALUES:
            missing.append(field)
    return missing


def validate_item(item: dict[str, Any], args: argparse.Namespace, ledger: dict[str, Any], source_created_at: datetime | None) -> list[str]:
    reasons: list[str] = []
    symbol = str(item.get("symbol") or "").upper()
    if item.get("recommended_max_action") != "paper_scout_allowed_if_trigger_confirms":
        reasons.append(str(item.get("block_reason") or "watchlist_not_actionable"))
    if not symbol:
        reasons.append("missing_symbol")
    if source_created_at is None:
        reasons.append("missing_watchlist_timestamp")
    else:
        age_minutes = (now_utc() - source_created_at).total_seconds() / 60.0
        if age_minutes > args.max_stale_minutes:
            reasons.append(f"watchlist_stale_{age_minutes:.1f}m")
    current_price = as_float(item.get("current_price"))
    trigger = as_float((item.get("entry_zone") or {}).get("breakout_confirm_above"))
    stop_loss = as_float(item.get("stop_loss"))
    take_profit = as_float((item.get("take_profit") or [None])[0])
    if current_price is None or current_price <= 0:
        reasons.append("missing_current_price")
    if trigger is None or trigger <= 0:
        reasons.append("missing_entry_trigger")
    if current_price is not None and trigger is not None and current_price < trigger:
        reasons.append("trigger_not_confirmed")
    if stop_loss is None or current_price is None or stop_loss >= current_price:
        reasons.append("invalid_stop_loss")
    if take_profit is None or current_price is None or take_profit <= current_price:
        reasons.append("invalid_take_profit")
    spread_bps = as_float(item.get("spread_bps"), 9999.0) or 9999.0
    depth = as_float(item.get("book_depth_min_usd_20"), 0.0) or 0.0
    buy_ratio = as_float(item.get("recent_taker_buy_quote_ratio"))
    imbalance = as_float(item.get("order_book_imbalance_20"))
    if spread_bps > args.max_spread_bps:
        reasons.append(f"spread_too_wide_{spread_bps:.4f}bps")
    if depth < args.min_depth_usd:
        reasons.append(f"depth_too_thin_{depth:.2f}")
    if buy_ratio is not None and buy_ratio < args.min_taker_buy_ratio:
        reasons.append(f"taker_buy_ratio_weak_{buy_ratio:.4f}")
    if imbalance is not None and imbalance < args.min_order_book_imbalance:
        reasons.append(f"book_imbalance_weak_{imbalance:.4f}")
    missing_context = market_context_missing_fields(market_context_from_item(item))
    if missing_context:
        reasons.append(f"market_context_incomplete:{','.join(missing_context)}")
    if any(pos.get("symbol") == symbol for pos in ledger.get("open_positions", [])):
        reasons.append("duplicate_open_symbol")
    if len(ledger.get("open_positions", [])) >= args.max_open_positions:
        reasons.append("open_position_limit_reached")
    if float(ledger.get("cash_usd", 0.0)) < args.notional_usd:
        reasons.append("insufficient_paper_cash")
    return reasons


def build_position(item: dict[str, Any], args: argparse.Namespace, source_run_id: str, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    symbol = str(item["symbol"]).upper()
    raw_price = as_float(item.get("current_price"), 0.0) or 0.0
    spread_bps = as_float(item.get("spread_bps"), 0.0) or 0.0
    slippage_bps = float(args.slippage_bps)
    commission_bps = float(args.commission_bps)
    fill_price = raw_price * (1.0 + (slippage_bps + max(spread_bps, 0.0) / 2.0) / 10000.0)
    notional = float(args.notional_usd)
    quantity = notional / fill_price
    entry_commission = notional * commission_bps / 10000.0
    stop_price = as_float(item.get("stop_loss"), fill_price * 0.94) or fill_price * 0.94
    take_profit_price = as_float((item.get("take_profit") or [fill_price * 1.06])[0], fill_price * 1.06) or fill_price * 1.06
    opened_at = now_utc()
    expires_at = opened_at + timedelta(hours=float(args.max_holding_hours))
    trade_id = f"paper-{opened_at.strftime('%Y%m%d-%H%M%S')}-recovery-{symbol}"
    order_id = f"paper-order-{opened_at.strftime('%Y%m%d-%H%M%S')}-recovery-{symbol}-BUY"
    entry_execution = {
        "status": "verified",
        "side": "buy",
        "raw_price": round(raw_price, 12),
        "fill_price": round(fill_price, 12),
        "commission_usd": round(entry_commission, 6),
        "commission_bps": commission_bps,
        "slippage_bps": slippage_bps,
        "spread_bps": spread_bps,
        "depth_20_min_usd": item.get("book_depth_min_usd_20"),
        "quote_volume_24h_usd": item.get("quote_volume_24h_usd"),
        "recent_taker_buy_quote_ratio": item.get("recent_taker_buy_quote_ratio"),
        "order_book_imbalance_20": item.get("order_book_imbalance_20"),
        "source": "recovery_watchlist_paper_sampler",
    }
    market_context = market_context_from_item(item)
    position = {
        "paper_trade_id": trade_id,
        "symbol": symbol,
        "side": "long_spot_paper",
        "status": "open",
        "outcome": "pending",
        "opened_at": ppe.iso(opened_at),
        "expires_at": ppe.iso(expires_at),
        "entry_price": round(fill_price, 12),
        "quantity": round(quantity, 12),
        "notional_usd": round(notional, 6),
        "stop_pct": round(pct_from_prices(stop_price, fill_price), 6),
        "take_profit_pct": round(pct_from_prices(take_profit_price, fill_price), 6),
        "stop_price": round(stop_price, 12),
        "take_profit_price": round(take_profit_price, 12),
        "max_holding_window": f"{args.max_holding_hours}h",
        "paper_entry_mode": "recovery_watchlist_paper_scout",
        "strategy_family": "recovery_watchlist_breakout_scout",
        "signal_source": source_run_id,
        "sampler_run_id": run_id,
        "market_regime": market_context["market_regime"],
        "market_atmosphere": market_context["market_atmosphere"],
        "short_term_state": market_context["short_term_state"],
        "sentiment_state": market_context["sentiment_state"],
        "market_context_at_entry": market_context,
        "realistic_execution_enabled": True,
        "commission_bps": commission_bps,
        "slippage_bps": slippage_bps,
        "spread_bps": spread_bps,
        "entry_commission_usd": round(entry_commission, 6),
        "entry_execution": entry_execution,
        "recovery_watchlist_trigger": {
            "recommended_max_action": item.get("recommended_max_action"),
            "forecast_probability_pct": item.get("forecast_probability_pct"),
            "entry_trigger": (item.get("entry_zone") or {}).get("breakout_confirm_above"),
            "volume_ratio_vs_recent_median": item.get("volume_ratio_vs_recent_median"),
            "time_window": item.get("time_window"),
            "failure_conditions": item.get("failure_conditions") or [],
        },
        "risk_state": {
            "profit_protection_armed": False,
            "highest_unrealized_pnl_pct": 0.0,
            "trailing_floor_pct": None,
            "distance_to_trailing_floor_pct": None,
            "information_pressure_score": item.get("forecast_probability_pct"),
            "information_decay_threshold_hours": float(args.max_holding_hours),
            "age_hours": 0.0,
            "pnl_basis": "net_after_entry_and_exit_friction",
        },
        "live_orders_enabled": False,
        "private_api_used": False,
    }
    order = {
        "paper_order_id": order_id,
        "paper_trade_id": trade_id,
        "symbol": symbol,
        "side": "BUY",
        "type": "MARKET",
        "status": "FILLED",
        "created_at": ppe.iso(opened_at),
        "average_fill_price": round(fill_price, 12),
        "executed_quantity": round(quantity, 12),
        "commission_usd": round(entry_commission, 6),
        "commission_bps": commission_bps,
        "reason": "recovery_watchlist_paper_scout",
        "requested_notional_usd": round(notional, 6),
        "time_in_force": "IOC",
        "simulated_status": "PAPER_FILLED",
        "simulated_api": True,
        "filled_quantity": round(quantity, 12),
        "fill_price": round(fill_price, 12),
        "spread_bps": spread_bps,
        "slippage_bps": slippage_bps,
        "depth_1pct_usd": item.get("book_depth_min_usd_20"),
        "quote_status": "recovery_watchlist_verified_public_market_data",
        "order_reason": "recovery_watchlist_paper_scout",
        "live_orders_enabled": False,
        "private_api_used": False,
        "notes": "paper-only scout opened from recovery watchlist; no private or live endpoint called",
    }
    return position, order


def render_markdown(record: dict[str, Any]) -> str:
    lines = [
        f"# Recovery Watchlist Paper Sampler | {record['run_id']}",
        "",
        "- scope: `paper_only_recovery_watchlist_sampler`",
        "- live_orders_enabled: `false`",
        "- private_api_used: `false`",
        f"- ledger_mutated: `{str(record.get('ledger_mutated')).lower()}`",
        f"- source_watchlist: `{record.get('source_watchlist') or '-'}`",
        "",
        "## Summary",
        "",
        f"- status: `{record.get('status')}`",
        f"- opened_count: `{record.get('opened_count')}`",
        f"- blocked_count: `{record.get('blocked_count')}`",
        f"- cash_usd_after: `{record.get('cash_usd_after')}`",
        f"- open_positions_after: `{record.get('open_positions_after')}`",
        "",
        "## Decisions",
        "",
        "| Symbol | Decision | Reasons | Price | Trigger | Spread | Depth | Buy Ratio | Imbalance | Probability |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in record.get("decisions") or []:
        lines.append(
            f"| `{item.get('symbol')}` | `{item.get('decision')}` | "
            f"{', '.join(item.get('reasons') or []) or '-'} | "
            f"`{item.get('current_price')}` | `{item.get('trigger')}` | "
            f"`{item.get('spread_bps')}` | `{item.get('book_depth_min_usd_20') or item.get('depth')}` | "
            f"`{item.get('recent_taker_buy_quote_ratio')}` | `{item.get('order_book_imbalance_20')}` | "
            f"`{item.get('forecast_probability_pct')}` |"
        )
    if not record.get("decisions"):
        lines.append("| - | - | - | - | - | - | - | - | - | - |")
    if record.get("opened_positions"):
        lines.extend(["", "## Opened Paper Scouts", ""])
        for pos in record["opened_positions"]:
            lines.append(
                f"- `{pos.get('paper_trade_id')}` `{pos.get('symbol')}` "
                f"notional `${pos.get('notional_usd')}`, entry `{pos.get('entry_price')}`, "
                f"stop `{pos.get('stop_price')}`, take_profit `{pos.get('take_profit_price')}`"
            )
    return "\n".join(lines) + "\n"


def build_record(args: argparse.Namespace) -> dict[str, Any]:
    run_id = f"{now_local().strftime('%Y%m%d-%H%M%S')}-recovery-watchlist-paper-sampler"
    source_path = args.watchlist_json if getattr(args, "watchlist_json", None) else latest("*recovery-watchlist-monitor.json")
    source = read_json(source_path, {}) if source_path else {}
    ledger = ppe.load_ledger(str(args.ledger), args.capital)
    overlay = pso.load_overlay()
    paper_risk_control = ptrc.evaluate_from_paths(ledger, now=now_local())
    requested_notional = args.notional_usd
    paper_risk_notional_gate = ptrc.requested_notional_gate(paper_risk_control, requested_notional)
    if paper_risk_notional_gate.get("allow"):
        args.notional_usd = float(paper_risk_notional_gate["effective_notional_usd"])
    source_created = parse_dt(source.get("created_at")) if isinstance(source, dict) else None
    watchlist = source.get("watchlist") if isinstance(source.get("watchlist"), list) else []
    record: dict[str, Any] = {
        "run_id": run_id,
        "created_at": now_local().isoformat(),
        "scope": "paper_only_recovery_watchlist_sampler",
        "status": "ok",
        "live_orders_enabled": False,
        "private_api_used": False,
        "strategy_version": pso.strategy_version(overlay),
        "paper_strategy_overlay": {
            "path": rel(pso.OVERLAY_PATH),
            "updated_at": overlay.get("updated_at"),
            "auto_learning_enabled": overlay.get("auto_learning_enabled"),
        },
        "ledger_mutated": False,
        "source_watchlist": rel(source_path),
        "source_watchlist_run_id": source.get("run_id") if isinstance(source, dict) else None,
        "notional_usd": args.notional_usd,
        "requested_notional_usd": requested_notional,
        "paper_testnet_risk_control": paper_risk_control,
        "paper_risk_notional_gate": paper_risk_notional_gate,
        "decisions": [],
        "opened_positions": [],
        "blocked_count": 0,
        "opened_count": 0,
    }
    if ledger.get("live_orders_enabled") is not False or ledger.get("private_api_used") is not False:
        record["status"] = "blocked_ledger_safety_flags"
        record["blocked_count"] = len(watchlist)
        record["decisions"] = [
            {
                "symbol": item.get("symbol"),
                "decision": "blocked",
                "reasons": ["ledger_safety_flags_not_false"],
            }
            for item in watchlist
            if isinstance(item, dict)
        ]
        return record

    opened = False
    price_cache: dict[str, float] = {}
    for item in watchlist:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        reasons = validate_item(item, args, ledger, source_created)
        if not paper_risk_notional_gate.get("allow"):
            reasons.append(str(paper_risk_notional_gate.get("reason")))
        overlay_allowed, overlay_decision = pso.combined_overlay_gate(
            entry_mode="recovery_watchlist_paper_scout",
            strategy_family="recovery_watchlist_breakout_scout",
            overlay=overlay,
        )
        if not overlay_allowed:
            reasons.append("paper_strategy_overlay_block")
        decision = "blocked"
        if not opened and not reasons:
            position, order = build_position(item, args, record.get("source_watchlist_run_id") or "unknown-watchlist", run_id)
            pso.apply_overlay_to_position(position, overlay, overlay_decision)
            position["paper_testnet_risk_control"] = paper_risk_control
            position["paper_risk_notional_gate"] = paper_risk_notional_gate
            if not args.dry_run:
                ledger["cash_usd"] = round(float(ledger.get("cash_usd", 0.0)) - float(position["notional_usd"]), 6)
                ledger.setdefault("open_positions", []).append(position)
                ledger.setdefault("paper_orders", []).append(order)
                ledger.setdefault("events", []).append(
                    {
                        "event_type": "paper_open",
                        "created_at": ppe.iso(now_utc()),
                        "paper_trade_id": position["paper_trade_id"],
                        "symbol": symbol,
                        "entry_price": position["entry_price"],
                        "notional_usd": position["notional_usd"],
                        "source": "recovery_watchlist_paper_sampler",
                        "notes": "virtual order only; no live order placed",
                        "live_orders_enabled": False,
                        "private_api_used": False,
                    }
                )
                price_cache[symbol] = as_float(item.get("current_price"), position["entry_price"]) or position["entry_price"]
                ppe.mark_to_market(ledger, price_cache)
                ppe.save_ledger(str(args.ledger), ledger)
                record["ledger_mutated"] = True
            decision = "opened_paper_scout" if not args.dry_run else "would_open_paper_scout"
            record["opened_positions"].append(position)
            record["opened_count"] += 1
            opened = True
        else:
            record["blocked_count"] += 1
        record["decisions"].append(
            {
                "symbol": symbol,
                "decision": decision,
                "reasons": reasons,
                "paper_strategy_overlay_decision": overlay_decision,
                "current_price": item.get("current_price"),
                "trigger": (item.get("entry_zone") or {}).get("breakout_confirm_above"),
                "spread_bps": item.get("spread_bps"),
                "depth": item.get("book_depth_min_usd_20"),
                "book_depth_min_usd_20": item.get("book_depth_min_usd_20"),
                "recent_taker_buy_quote_ratio": item.get("recent_taker_buy_quote_ratio"),
                "order_book_imbalance_20": item.get("order_book_imbalance_20"),
                "quote_volume_24h_usd": item.get("quote_volume_24h_usd"),
                "data_quality_status": item.get("data_quality_status"),
                "forecast_probability_pct": item.get("forecast_probability_pct"),
            }
        )
    record["cash_usd_after"] = ledger.get("cash_usd")
    record["open_positions_after"] = len(ledger.get("open_positions", []))
    record["closed_trades_after"] = len(ledger.get("closed_trades", []))
    if args.dry_run:
        record["status"] = "dry_run"
    return record


def seed_ledger(path: Path, cash: float = 100.0, live_orders_enabled: bool = False) -> None:
    now = ppe.iso(now_utc())
    payload = {
        "ledger_version": "paper-portfolio-v1-self-test",
        "created_at": now,
        "updated_at": now,
        "live_orders_enabled": live_orders_enabled,
        "private_api_used": False,
        "initial_capital_usd": cash,
        "cash_usd": cash,
        "equity_usd": cash,
        "open_value_usd": 0.0,
        "open_positions": [],
        "closed_trades": [],
        "paper_orders": [],
        "events": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_watchlist(path: Path, items: list[dict[str, Any]]) -> None:
    payload = {
        "run_id": "self-test-recovery-watchlist-monitor",
        "created_at": now_local().isoformat(),
        "scope": "paper_only_recovery_watchlist",
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
        "watchlist": items,
        "queue_count": len(items),
        "actionable_paper_scout_count": len(
            [item for item in items if item.get("recommended_max_action") == "paper_scout_allowed_if_trigger_confirms"]
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def self_test_args(ledger: Path, watchlist: Path) -> argparse.Namespace:
    return argparse.Namespace(
        ledger=ledger,
        watchlist_json=watchlist,
        capital=100.0,
        notional_usd=25.0,
        max_open_positions=3,
        max_stale_minutes=90.0,
        max_spread_bps=12.0,
        min_depth_usd=15000.0,
        min_taker_buy_ratio=0.48,
        min_order_book_imbalance=-0.20,
        commission_bps=10.0,
        slippage_bps=8.0,
        max_holding_hours=72.0,
        dry_run=False,
    )


def run_self_test() -> dict[str, Any]:
    actionable = {
        "symbol": "TESTUSDT",
        "recommended_max_action": "paper_scout_allowed_if_trigger_confirms",
        "market_regime": "risk_on_momentum",
        "market_atmosphere": "broad_risk_appetite",
        "short_term_state": "neutral",
        "sentiment_state": "positive_catalyst_cluster",
        "market_context_source": "self_test_recovery_watchlist",
        "market_context": {
            "market_regime": "risk_on_momentum",
            "market_atmosphere": "broad_risk_appetite",
            "short_term_state": "neutral",
            "sentiment_state": "positive_catalyst_cluster",
            "pool_width_policy": "expanded_high_beta",
            "dynamic_scan_status": "risk_on_momentum_scan",
            "dynamic_scan_reason": "self_test_dynamic_pool",
            "data_layer_degraded": False,
            "selected_symbols_sample": ["TESTUSDT", "BLOCKUSDT"],
            "source": "self_test_recovery_watchlist",
        },
        "current_price": 101.0,
        "spread_bps": 2.0,
        "book_depth_min_usd_20": 50000.0,
        "recent_taker_buy_quote_ratio": 0.62,
        "order_book_imbalance_20": 0.18,
        "forecast_probability_pct": 64,
        "volume_ratio_vs_recent_median": 1.8,
        "entry_zone": {"breakout_confirm_above": 100.0},
        "stop_loss": 96.0,
        "take_profit": [108.0, 114.0],
        "time_window": "self-test",
        "failure_conditions": ["self-test failure condition"],
    }
    blocked = {
        **actionable,
        "symbol": "BLOCKUSDT",
        "recommended_max_action": "conditional_watch_until_breakout",
        "current_price": 90.0,
        "entry_zone": {"breakout_confirm_above": 100.0},
        "spread_bps": 25.0,
        "block_reason": "self_test_blocked",
    }
    with tempfile.TemporaryDirectory(prefix="recovery-watchlist-sampler-") as tmp:
        tmp_path = Path(tmp)
        ledger_path = tmp_path / "paper_trades" / "ledger.json"
        watchlist_path = tmp_path / "experiments" / "watchlist.json"
        seed_ledger(ledger_path)
        write_watchlist(watchlist_path, [actionable, blocked])

        first = build_record(self_test_args(ledger_path, watchlist_path))
        ledger_after_first = read_json(ledger_path, {})
        assert first["opened_count"] == 1, first
        assert first["blocked_count"] == 1, first
        assert first["ledger_mutated"] is True, first
        assert len(ledger_after_first.get("open_positions", [])) == 1, ledger_after_first
        assert len(ledger_after_first.get("paper_orders", [])) == 1, ledger_after_first
        assert abs(float(ledger_after_first["cash_usd"]) - 75.0) < 0.01, ledger_after_first
        pos = ledger_after_first["open_positions"][0]
        order = ledger_after_first["paper_orders"][0]
        assert pos["live_orders_enabled"] is False and pos["private_api_used"] is False, pos
        assert order["live_orders_enabled"] is False and order["private_api_used"] is False, order
        assert pos["market_regime"] == "risk_on_momentum", pos
        assert pos["market_context_at_entry"]["source"] == "self_test_recovery_watchlist", pos
        assert pos["market_context_at_entry"]["pool_width_policy"] == "expanded_high_beta", pos
        assert pos["market_context_at_entry"]["dynamic_scan_status"] == "risk_on_momentum_scan", pos
        assert pos["market_context_at_entry"]["data_layer_degraded"] is False, pos

        second = build_record(self_test_args(ledger_path, watchlist_path))
        ledger_after_second = read_json(ledger_path, {})
        assert second["opened_count"] == 0, second
        assert any("duplicate_open_symbol" in item.get("reasons", []) for item in second["decisions"]), second
        assert len(ledger_after_second.get("open_positions", [])) == 1, ledger_after_second

        unsafe_ledger = tmp_path / "paper_trades" / "unsafe-ledger.json"
        seed_ledger(unsafe_ledger, live_orders_enabled=True)
        unsafe = build_record(self_test_args(unsafe_ledger, watchlist_path))
        assert unsafe["status"] == "blocked_ledger_safety_flags", unsafe
        assert len(read_json(unsafe_ledger, {}).get("open_positions", [])) == 0, unsafe

        missing_context_item = {
            key: value
            for key, value in actionable.items()
            if key not in {"market_regime", "market_atmosphere", "short_term_state", "sentiment_state", "market_context", "market_context_source"}
        }
        missing_context_item["symbol"] = "NOCONTEXTUSDT"
        missing_context_ledger = tmp_path / "paper_trades" / "missing-context-ledger.json"
        missing_context_watchlist = tmp_path / "experiments" / "missing-context-watchlist.json"
        seed_ledger(missing_context_ledger)
        write_watchlist(missing_context_watchlist, [missing_context_item])
        missing_context = build_record(self_test_args(missing_context_ledger, missing_context_watchlist))
        assert missing_context["opened_count"] == 0, missing_context
        assert any(
            any(str(reason).startswith("market_context_incomplete:") for reason in item.get("reasons", []))
            for item in missing_context["decisions"]
        ), missing_context
        assert len(read_json(missing_context_ledger, {}).get("open_positions", [])) == 0, missing_context

    return {
        "status": "ok",
        "cases": [
            "actionable_opens_one_tiny_paper_scout",
            "actionable_preserves_market_context_at_entry",
            "missing_market_context_blocks_new_scout",
            "duplicate_symbol_blocks_second_open",
            "ledger_safety_flags_block_mutation",
        ],
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Open tiny paper scouts from actionable recovery watchlist triggers.")
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    parser.add_argument("--watchlist-json", type=Path, help="Use a specific recovery watchlist experiment instead of the latest one.")
    parser.add_argument("--report-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--experiment-dir", type=Path, default=EXPERIMENTS_DIR)
    parser.add_argument("--capital", type=float, default=500.0)
    parser.add_argument("--notional-usd", type=float, default=25.0)
    parser.add_argument("--max-open-positions", type=int, default=3)
    parser.add_argument("--max-stale-minutes", type=float, default=90.0)
    parser.add_argument("--max-spread-bps", type=float, default=12.0)
    parser.add_argument("--min-depth-usd", type=float, default=15000.0)
    parser.add_argument("--min-taker-buy-ratio", type=float, default=0.48)
    parser.add_argument("--min-order-book-imbalance", type=float, default=-0.20)
    parser.add_argument("--commission-bps", type=float, default=10.0)
    parser.add_argument("--slippage-bps", type=float, default=8.0)
    parser.add_argument("--max-holding-hours", type=float, default=72.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        result = run_self_test()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    record = build_record(args)
    stamp = record["run_id"].removesuffix("-recovery-watchlist-paper-sampler")
    report_path = args.report_dir / f"{now_local().strftime('%Y-%m-%d')}-recovery-watchlist-paper-sampler-{stamp}.md"
    experiment_path = args.experiment_dir / f"{stamp}-recovery-watchlist-paper-sampler.json"
    record["outputs"] = {"report": rel(report_path), "experiment": rel(experiment_path)}
    args.report_dir.mkdir(parents=True, exist_ok=True)
    args.experiment_dir.mkdir(parents=True, exist_ok=True)
    experiment_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_markdown(record), encoding="utf-8")
    if args.compact_output:
        print(json.dumps({
            "status": record.get("status"),
            "run_id": record["run_id"],
            "opened_count": record.get("opened_count"),
            "blocked_count": record.get("blocked_count"),
            "ledger_mutated": record.get("ledger_mutated"),
            "live_orders_enabled": False,
            "private_api_used": False,
            "outputs": record["outputs"],
        }, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
