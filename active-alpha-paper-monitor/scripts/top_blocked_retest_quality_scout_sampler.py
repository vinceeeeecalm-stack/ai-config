#!/usr/bin/env python3
"""Open tiny paper scouts from top-blocked retest candidates.

Paper-only safety layer:
- reads the latest top_blocked_candidate_retest_lab experiment
- re-checks current public Binance spot price/depth before action
- opens at most one $25 paper scout when strict retest + current trigger + liquidity pass
- never calls private APIs, never places live orders, never mutates strategy config
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
EXPERIMENTS_DIR = ACTIVE_ROOT / "experiments"
REPORTS_DIR = ACTIVE_ROOT / "reports"
LEDGER_PATH = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
CHINA_TZ = dt.timezone(dt.timedelta(hours=8))
UA = "Mozilla/5.0 (compatible; active-alpha-paper-monitor/top-blocked-quality-scout; paper-only)"
BINANCE_PUBLIC_BASE_URLS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]

sys.path.insert(0, str(SCRIPT_DIR))
import paper_portfolio_engine as ppe  # noqa: E402
import paper_strategy_overlay as pso  # noqa: E402
import paper_testnet_risk_control_auditor as ptrc  # noqa: E402


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def now_local() -> dt.datetime:
    return now_utc().astimezone(CHINA_TZ)


def stamp(value: dt.datetime) -> str:
    return value.strftime("%Y%m%d-%H%M%S")


def rel(path: Path | None) -> str | None:
    if not path:
        return None
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path | None, default: Any = None) -> Any:
    if not path or not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def latest(pattern: str) -> Path | None:
    files = sorted(EXPERIMENTS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
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
        "source": source_context.get("source") or item.get("market_context_source") or "top_blocked_retest_item_or_unknown",
    }


def market_context_missing_fields(context: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in ("market_regime", "market_atmosphere", "short_term_state", "sentiment_state"):
        value = str((context or {}).get(field) or "").strip().lower()
        if value in UNKNOWN_MARKET_CONTEXT_VALUES:
            missing.append(field)
    return missing


def get_url_json(path: str, params: dict[str, Any], base_urls: list[str] | None = None) -> Any:
    query = urllib.parse.urlencode(params)
    failures: list[dict[str, str]] = []
    for base_url in base_urls or BINANCE_PUBLIC_BASE_URLS:
        url = f"{base_url}{path}?{query}"
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {
                    "base_url": base_url,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[-240:],
                }
            )
    raise RuntimeError(json.dumps({"path": path, "failures": failures}, ensure_ascii=False))


def failed_market_snapshot(symbol: str, endpoint: str, error: Exception) -> dict[str, Any]:
    return {
        "status": "failed",
        "symbol": symbol,
        "failed_endpoint": endpoint,
        "error": str(error)[-300:],
        "last_price": None,
        "bid": None,
        "ask": None,
        "mid": None,
        "spread_bps": 9999.0,
        "bid_depth_usd_20": 0.0,
        "ask_depth_usd_20": 0.0,
        "book_depth_min_usd_20": 0.0,
        "quote_volume_24h_usd": 0.0,
        "price_change_pct_24h": None,
    }


def public_market_snapshot(symbol: str, depth_limit: int = 20) -> dict[str, Any]:
    try:
        ticker = get_url_json("/api/v3/ticker/24hr", {"symbol": symbol})
    except Exception as exc:  # noqa: BLE001
        return failed_market_snapshot(symbol, "ticker_24hr", exc)
    try:
        depth = get_url_json("/api/v3/depth", {"symbol": symbol, "limit": depth_limit})
    except Exception as exc:  # noqa: BLE001
        return failed_market_snapshot(symbol, "depth", exc)
    bid = safe_float((depth.get("bids") or [[None]])[0][0], 0.0) or 0.0
    ask = safe_float((depth.get("asks") or [[None]])[0][0], 0.0) or 0.0
    mid = (bid + ask) / 2.0 if bid and ask else safe_float(ticker.get("lastPrice"), 0.0) or 0.0
    bid_depth = sum((safe_float(price, 0.0) or 0.0) * (safe_float(qty, 0.0) or 0.0) for price, qty in depth.get("bids", []))
    ask_depth = sum((safe_float(price, 0.0) or 0.0) * (safe_float(qty, 0.0) or 0.0) for price, qty in depth.get("asks", []))
    spread_bps = ((ask - bid) / mid * 10000.0) if mid else 9999.0
    return {
        "status": "ok",
        "symbol": symbol,
        "last_price": safe_float(ticker.get("lastPrice"), mid) or mid,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_bps": round(spread_bps, 6),
        "bid_depth_usd_20": round(bid_depth, 6),
        "ask_depth_usd_20": round(ask_depth, 6),
        "book_depth_min_usd_20": round(min(bid_depth, ask_depth), 6),
        "quote_volume_24h_usd": safe_float(ticker.get("quoteVolume"), 0.0),
        "price_change_pct_24h": safe_float(ticker.get("priceChangePercent"), 0.0),
    }


def candidate_rows(payload: dict[str, Any], max_candidates: int) -> list[dict[str, Any]]:
    rows = payload.get("results") if isinstance(payload.get("results"), list) else []
    eligible = [
        item for item in rows
        if isinstance(item, dict)
        and item.get("decision") == "candidate_for_min_quality_scout_after_current_signal"
    ]
    eligible.sort(
        key=lambda item: (
            -float(((item.get("best_variant") or {}).get("oos") or {}).get("net_return_pct") or 0.0),
            -float(((item.get("best_variant") or {}).get("oos") or {}).get("win_rate_pct") or 0.0),
        )
    )
    return eligible[:max(1, max_candidates)]


def validate_candidate(item: dict[str, Any], snapshot: dict[str, Any], ledger: dict[str, Any], args: argparse.Namespace) -> list[str]:
    reasons: list[str] = []
    symbol = str(item.get("symbol") or "").upper()
    setup = item.get("current_setup") or {}
    best = item.get("best_variant") or {}
    oos = best.get("oos") or {}
    breakout_level = safe_float(setup.get("breakout_level"))
    current_price = safe_float(snapshot.get("last_price"))
    if snapshot.get("status") != "ok":
        endpoint = snapshot.get("failed_endpoint") or "unknown_endpoint"
        reasons.append(f"binance_public_fetch_failed:{endpoint}")
    if not symbol:
        reasons.append("missing_symbol")
    if item.get("decision") != "candidate_for_min_quality_scout_after_current_signal":
        reasons.append("not_quality_scout_review_candidate")
    if safe_float(oos.get("trade_count"), 0.0) < args.min_oos_trades:
        reasons.append("oos_trade_count_below_gate")
    if safe_float(oos.get("win_rate_pct"), 0.0) < args.min_oos_win_rate_pct:
        reasons.append("oos_win_rate_below_gate")
    if safe_float(oos.get("net_return_pct"), 0.0) < args.min_oos_net_return_pct:
        reasons.append("oos_net_return_below_gate")
    if safe_float(oos.get("max_drawdown_pct"), 0.0) < -abs(args.max_oos_drawdown_pct):
        reasons.append("oos_drawdown_too_deep")
    missing_context = market_context_missing_fields(market_context_from_item(item))
    if missing_context:
        reasons.append(f"market_context_incomplete:{','.join(missing_context)}")
    if not breakout_level or not current_price:
        reasons.append("missing_current_price_or_breakout_level")
    elif current_price < breakout_level:
        distance_pct = pct_from_prices(breakout_level, current_price)
        reasons.append(f"current_trigger_not_confirmed_distance_{distance_pct:.4f}%")
    spread_bps = safe_float(snapshot.get("spread_bps"), 9999.0) or 9999.0
    depth = safe_float(snapshot.get("book_depth_min_usd_20"), 0.0) or 0.0
    quote_volume = safe_float(snapshot.get("quote_volume_24h_usd"), 0.0) or 0.0
    if spread_bps > args.max_spread_bps:
        reasons.append(f"spread_too_wide_{spread_bps:.4f}bps")
    if depth < args.min_depth_usd:
        reasons.append(f"depth_too_thin_{depth:.2f}")
    if quote_volume < args.min_quote_volume_usd:
        reasons.append(f"quote_volume_too_low_{quote_volume:.2f}")
    if any(str(pos.get("symbol") or "").upper() == symbol for pos in ledger.get("open_positions", [])):
        reasons.append("duplicate_open_symbol")
    if len(ledger.get("open_positions", [])) >= args.max_open_positions:
        reasons.append("open_position_limit_reached")
    if float(ledger.get("cash_usd", 0.0)) < args.notional_usd:
        reasons.append("insufficient_paper_cash")
    return reasons


def build_position(item: dict[str, Any], snapshot: dict[str, Any], args: argparse.Namespace, source_run_id: str, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    symbol = str(item["symbol"]).upper()
    mark_price = safe_float(snapshot.get("ask"), snapshot.get("last_price")) or safe_float(snapshot.get("last_price"), 0.0) or 0.0
    spread_bps = safe_float(snapshot.get("spread_bps"), 0.0) or 0.0
    commission_bps = float(args.commission_bps)
    slippage_bps = float(args.slippage_bps)
    fill_price = mark_price * (1.0 + slippage_bps / 10000.0)
    notional = float(args.notional_usd)
    quantity = notional / fill_price if fill_price else 0.0
    entry_commission = notional * commission_bps / 10000.0
    stop_pct = -abs(float(args.stop_pct))
    take_pct = abs(float(args.take_profit_pct))
    stop_price = fill_price * (1.0 + stop_pct / 100.0)
    take_price = fill_price * (1.0 + take_pct / 100.0)
    opened_at = now_utc()
    expires_at = opened_at + dt.timedelta(hours=float(args.max_holding_hours))
    seq = opened_at.strftime("%Y%m%d-%H%M%S")
    trade_id = f"paper-{seq}-blocked-retest-scout-{symbol}"
    order_id = f"paper-order-{seq}-blocked-retest-scout-{symbol}-BUY"
    best = item.get("best_variant") or {}
    oos = best.get("oos") or {}
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
        "stop_pct": round(stop_pct, 6),
        "take_profit_pct": round(take_pct, 6),
        "stop_price": round(stop_price, 12),
        "take_profit_price": round(take_price, 12),
        "max_holding_window": f"{args.max_holding_hours}h",
        "paper_entry_mode": "top_blocked_retest_quality_scout",
        "strategy_family": item.get("strategy_family") or "top_blocked_retest_scout",
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
        "entry_execution": {
            "status": "verified_public_market_data",
            "side": "buy",
            "raw_last_price": snapshot.get("last_price"),
            "bid": snapshot.get("bid"),
            "ask": snapshot.get("ask"),
            "fill_price": round(fill_price, 12),
            "commission_usd": round(entry_commission, 6),
            "commission_bps": commission_bps,
            "slippage_bps": slippage_bps,
            "spread_bps": spread_bps,
            "book_depth_min_usd_20": snapshot.get("book_depth_min_usd_20"),
            "quote_volume_24h_usd": snapshot.get("quote_volume_24h_usd"),
            "source": "top_blocked_retest_quality_scout_sampler",
        },
        "top_blocked_retest_context": {
            "best_variant": best.get("variant"),
            "oos_trade_count": oos.get("trade_count"),
            "oos_win_rate_pct": oos.get("win_rate_pct"),
            "oos_net_return_pct": oos.get("net_return_pct"),
            "oos_max_drawdown_pct": oos.get("max_drawdown_pct"),
            "source_block_reason": item.get("source_block_reason"),
            "breakout_level": (item.get("current_setup") or {}).get("breakout_level"),
        },
        "risk_state": {
            "profit_protection_armed": False,
            "highest_unrealized_pnl_pct": 0.0,
            "trailing_floor_pct": None,
            "distance_to_trailing_floor_pct": None,
            "information_pressure_score": oos.get("win_rate_pct"),
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
        "reason": "top_blocked_retest_quality_scout",
        "requested_notional_usd": round(notional, 6),
        "time_in_force": "IOC",
        "simulated_status": "PAPER_FILLED",
        "simulated_api": True,
        "filled_quantity": round(quantity, 12),
        "fill_price": round(fill_price, 12),
        "spread_bps": spread_bps,
        "slippage_bps": slippage_bps,
        "depth_1pct_usd": snapshot.get("book_depth_min_usd_20"),
        "quote_status": "top_blocked_retest_verified_public_market_data",
        "order_reason": "top_blocked_retest_quality_scout",
        "live_orders_enabled": False,
        "private_api_used": False,
        "notes": "paper-only scout from top blocked retest lab; no private or live endpoint called",
    }
    return position, order


def build_record(args: argparse.Namespace) -> dict[str, Any]:
    created = now_local()
    run_id = f"{stamp(created)}-top-blocked-retest-quality-scout-sampler"
    source_path = args.retest_json if args.retest_json else latest("*top-blocked-candidate-retest-lab.json")
    source = read_json(source_path, {}) if source_path else {}
    ledger = ppe.load_ledger(str(args.ledger), args.capital)
    overlay_path = getattr(args, "overlay_path", pso.OVERLAY_PATH)
    overlay = pso.load_overlay(overlay_path)
    paper_risk_control = ptrc.evaluate_from_paths(ledger, now=created)
    requested_notional = args.notional_usd
    paper_risk_notional_gate = ptrc.requested_notional_gate(paper_risk_control, requested_notional)
    if paper_risk_notional_gate.get("allow"):
        args.notional_usd = float(paper_risk_notional_gate["effective_notional_usd"])
    candidates = candidate_rows(source if isinstance(source, dict) else {}, args.max_candidates)
    record: dict[str, Any] = {
        "run_id": run_id,
        "created_at": created.isoformat(),
        "scope": "paper_only_top_blocked_retest_quality_scout_sampler",
        "status": "ok",
        "live_orders_enabled": False,
        "private_api_used": False,
        "strategy_version": pso.strategy_version(overlay),
        "paper_strategy_overlay": {
            "path": rel(overlay_path),
            "updated_at": overlay.get("updated_at"),
            "auto_learning_enabled": overlay.get("auto_learning_enabled"),
        },
        "ledger_mutated": False,
        "source_retest": rel(source_path),
        "source_retest_run_id": source.get("run_id") if isinstance(source, dict) else None,
        "candidate_count": len(candidates),
        "notional_usd": args.notional_usd,
        "requested_notional_usd": requested_notional,
        "paper_testnet_risk_control": paper_risk_control,
        "paper_risk_notional_gate": paper_risk_notional_gate,
        "decisions": [],
        "opened_positions": [],
        "opened_count": 0,
        "blocked_count": 0,
    }
    if ledger.get("live_orders_enabled") is not False or ledger.get("private_api_used") is not False:
        record["status"] = "blocked_ledger_safety_flags"
        record["blocked_count"] = len(candidates)
        record["decisions"] = [
            {"symbol": item.get("symbol"), "decision": "blocked", "reasons": ["ledger_safety_flags_not_false"]}
            for item in candidates
        ]
        return record
    opened = False
    price_cache: dict[str, float] = {}
    for item in candidates:
        symbol = str(item.get("symbol") or "").upper()
        snapshot = public_market_snapshot(symbol) if not args.offline_snapshot else args.offline_snapshot.get(symbol, {})
        reasons = validate_candidate(item, snapshot, ledger, args)
        if not paper_risk_notional_gate.get("allow"):
            reasons.append(str(paper_risk_notional_gate.get("reason")))
        overlay_allowed, overlay_decision = pso.combined_overlay_gate(
            entry_mode="top_blocked_retest_quality_scout",
            strategy_family=str(item.get("strategy_family") or "top_blocked_retest_scout"),
            overlay=overlay,
        )
        if not overlay_allowed:
            reasons.append("paper_strategy_overlay_block")
        decision = "blocked"
        if not opened and not reasons:
            position, order = build_position(item, snapshot, args, record.get("source_retest_run_id") or "unknown-retest", run_id)
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
                        "source": "top_blocked_retest_quality_scout_sampler",
                        "notes": "virtual order only; no live order placed",
                        "live_orders_enabled": False,
                        "private_api_used": False,
                    }
                )
                price_cache[symbol] = safe_float(snapshot.get("last_price"), position["entry_price"]) or position["entry_price"]
                ppe.mark_to_market(ledger, price_cache)
                ppe.save_ledger(str(args.ledger), ledger)
                record["ledger_mutated"] = True
            decision = "opened_paper_quality_scout" if not args.dry_run else "would_open_paper_quality_scout"
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
                "market_snapshot_status": snapshot.get("status"),
                "market_snapshot_error": snapshot.get("error"),
                "last_price": snapshot.get("last_price"),
                "breakout_level": (item.get("current_setup") or {}).get("breakout_level"),
                "spread_bps": snapshot.get("spread_bps"),
                "depth": snapshot.get("book_depth_min_usd_20"),
                "quote_volume_24h_usd": snapshot.get("quote_volume_24h_usd"),
                "best_variant": (item.get("best_variant") or {}).get("variant"),
                "oos": (item.get("best_variant") or {}).get("oos") or {},
            }
        )
    record["cash_usd_after"] = ledger.get("cash_usd")
    record["open_positions_after"] = len(ledger.get("open_positions", []))
    record["closed_trades_after"] = len(ledger.get("closed_trades", []))
    if args.dry_run:
        record["status"] = "dry_run"
    return record


def render_markdown(record: dict[str, Any]) -> str:
    lines = [
        f"# Top Blocked Retest Quality Scout Sampler | {record['run_id']}",
        "",
        "- scope: `paper_only_top_blocked_retest_quality_scout_sampler`",
        "- live_orders_enabled: `false`",
        "- private_api_used: `false`",
        f"- ledger_mutated: `{str(record.get('ledger_mutated')).lower()}`",
        f"- source_retest: `{record.get('source_retest') or '-'}`",
        "",
        "## Summary",
        "",
        f"- status: `{record.get('status')}`",
        f"- candidate_count: `{record.get('candidate_count')}`",
        f"- opened_count: `{record.get('opened_count')}`",
        f"- blocked_count: `{record.get('blocked_count')}`",
        f"- cash_usd_after: `{record.get('cash_usd_after')}`",
        f"- open_positions_after: `{record.get('open_positions_after')}`",
        "",
        "## Decisions",
        "",
        "| Symbol | Decision | Reasons | Price | Breakout | Spread | Depth | OOS Win % | OOS Net % | Variant |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in record.get("decisions") or []:
        oos = item.get("oos") or {}
        lines.append(
            f"| `{item.get('symbol')}` | `{item.get('decision')}` | "
            f"{', '.join(item.get('reasons') or []) or '-'} | "
            f"`{item.get('last_price')}` | `{item.get('breakout_level')}` | "
            f"`{item.get('spread_bps')}` | `{item.get('depth')}` | "
            f"`{oos.get('win_rate_pct')}` | `{oos.get('net_return_pct')}` | "
            f"`{item.get('best_variant')}` |"
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
    lines.extend(
        [
            "",
            "## Rule",
            "",
            "A passing retest is still not enough. This sampler requires current public Binance price to confirm the stored breakout level plus spread, depth, volume, cash, duplicate-symbol and safety gates.",
        ]
    )
    return "\n".join(lines) + "\n"


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
    write_json(path, payload)


def retest_fixture(path: Path, symbol: str = "TESTUSDT", breakout: float = 100.0) -> None:
    payload = {
        "run_id": "self-test-top-blocked-retest",
        "created_at": now_local().isoformat(),
        "scope": "paper_only_top_blocked_candidate_retest",
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
        "candidates_seen": 1,
        "quality_scout_review_count": 1,
        "results": [
            {
                "symbol": symbol,
                "interval": "15m",
                "strategy_family": "momentum",
                "source_block_reason": "self_test_block",
                "market_regime": "risk_on_momentum",
                "market_atmosphere": "broad_risk_appetite",
                "short_term_state": "neutral",
                "sentiment_state": "positive_catalyst_cluster",
                "market_context_source": "self_test_retest_lab",
                "market_context": {
                    "market_regime": "risk_on_momentum",
                    "market_atmosphere": "broad_risk_appetite",
                    "short_term_state": "neutral",
                    "sentiment_state": "positive_catalyst_cluster",
                    "source": "self_test_retest_lab",
                },
                "decision": "candidate_for_min_quality_scout_after_current_signal",
                "current_setup": {"status": "ok", "breakout_level": breakout},
                "best_variant": {
                    "variant": "strict_breakout_retest",
                    "oos": {
                        "trade_count": 4,
                        "win_rate_pct": 75.0,
                        "net_return_pct": 12.0,
                        "max_drawdown_pct": -4.0,
                    },
                },
            }
        ],
    }
    write_json(path, payload)


def self_test_args(
    ledger: Path,
    retest: Path,
    snapshot: dict[str, dict[str, Any]],
    overlay_path: Path,
) -> argparse.Namespace:
    return argparse.Namespace(
        ledger=ledger,
        retest_json=retest,
        capital=100.0,
        notional_usd=25.0,
        max_candidates=3,
        max_open_positions=3,
        min_oos_trades=3,
        min_oos_win_rate_pct=55.0,
        min_oos_net_return_pct=2.0,
        max_oos_drawdown_pct=15.0,
        max_spread_bps=12.0,
        min_depth_usd=15000.0,
        min_quote_volume_usd=100000.0,
        commission_bps=10.0,
        slippage_bps=8.0,
        stop_pct=-4.0,
        take_profit_pct=8.0,
        max_holding_hours=48.0,
        dry_run=False,
        offline_snapshot=snapshot,
        overlay_path=overlay_path,
    )


def run_self_test() -> dict[str, Any]:
    good_snapshot = {
        "TESTUSDT": {
            "status": "ok",
            "last_price": 101.0,
            "bid": 100.98,
            "ask": 101.02,
            "spread_bps": 3.96,
            "book_depth_min_usd_20": 50000.0,
            "quote_volume_24h_usd": 2000000.0,
        }
    }
    untriggered_snapshot = {
        "TESTUSDT": {
            **good_snapshot["TESTUSDT"],
            "last_price": 99.0,
            "bid": 98.98,
            "ask": 99.02,
        }
    }
    failed_snapshot = {
        "TESTUSDT": failed_market_snapshot(
            "TESTUSDT",
            "ticker_24hr",
            RuntimeError("self-test-dns-failure"),
        )
    }

    class FakeResponse:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    original_urlopen = urllib.request.urlopen

    def fallback_urlopen(req: Any, timeout: float = 0.0) -> FakeResponse:
        url = getattr(req, "full_url", str(req))
        if "bad-host" in url:
            raise OSError("self-test-primary-host-failed")
        return FakeResponse({"ok": True, "url": url})

    urllib.request.urlopen = fallback_urlopen
    try:
        fallback_payload = get_url_json(
            "/api/v3/ticker/24hr",
            {"symbol": "TESTUSDT"},
            base_urls=["https://bad-host", "https://good-host"],
        )
        assert fallback_payload["ok"] is True and "good-host" in fallback_payload["url"], fallback_payload
    finally:
        urllib.request.urlopen = original_urlopen

    with tempfile.TemporaryDirectory(prefix="top-blocked-quality-scout-") as tmp:
        root = Path(tmp)
        ledger = root / "ledger.json"
        retest = root / "retest.json"
        overlay_path = root / "paper-strategy-overlay.json"
        pso.save_overlay(pso.default_overlay(), overlay_path)
        seed_ledger(ledger)
        retest_fixture(retest)
        first = build_record(self_test_args(ledger, retest, good_snapshot, overlay_path))
        ledger_after_first = read_json(ledger, {})
        assert first["opened_count"] == 1, first
        assert first["ledger_mutated"] is True, first
        assert len(ledger_after_first.get("open_positions", [])) == 1, ledger_after_first
        assert ledger_after_first["open_positions"][0]["live_orders_enabled"] is False
        assert ledger_after_first["open_positions"][0]["market_regime"] == "risk_on_momentum"
        assert ledger_after_first["open_positions"][0]["market_context_at_entry"]["source"] == "self_test_retest_lab"
        second = build_record(self_test_args(ledger, retest, good_snapshot, overlay_path))
        assert second["opened_count"] == 0, second
        assert any("duplicate_open_symbol" in item.get("reasons", []) for item in second["decisions"]), second

        untriggered_ledger = root / "untriggered-ledger.json"
        seed_ledger(untriggered_ledger)
        untriggered = build_record(self_test_args(untriggered_ledger, retest, untriggered_snapshot, overlay_path))
        assert untriggered["opened_count"] == 0, untriggered
        assert any("current_trigger_not_confirmed" in ",".join(item.get("reasons", [])) for item in untriggered["decisions"]), untriggered

        network_ledger = root / "network-ledger.json"
        seed_ledger(network_ledger)
        network_failed = build_record(self_test_args(network_ledger, retest, failed_snapshot, overlay_path))
        assert network_failed["opened_count"] == 0, network_failed
        assert network_failed["ledger_mutated"] is False, network_failed
        assert any("binance_public_fetch_failed" in ",".join(item.get("reasons", [])) for item in network_failed["decisions"]), network_failed
        assert len(read_json(network_ledger, {}).get("open_positions", [])) == 0

        missing_context_retest = root / "missing-context-retest.json"
        retest_fixture(missing_context_retest, symbol="NOCONTEXTUSDT")
        missing_payload = read_json(missing_context_retest, {})
        row = missing_payload["results"][0]
        for key in ("market_regime", "market_atmosphere", "short_term_state", "sentiment_state", "market_context", "market_context_source"):
            row.pop(key, None)
        write_json(missing_context_retest, missing_payload)
        missing_context_ledger = root / "missing-context-ledger.json"
        seed_ledger(missing_context_ledger)
        missing_context = build_record(
            self_test_args(
                missing_context_ledger,
                missing_context_retest,
                {"NOCONTEXTUSDT": {**good_snapshot["TESTUSDT"], "symbol": "NOCONTEXTUSDT"}},
                overlay_path,
            )
        )
        assert missing_context["opened_count"] == 0, missing_context
        assert any(
            any(str(reason).startswith("market_context_incomplete:") for reason in item.get("reasons", []))
            for item in missing_context["decisions"]
        ), missing_context
        assert len(read_json(missing_context_ledger, {}).get("open_positions", [])) == 0

        unsafe_ledger = root / "unsafe-ledger.json"
        seed_ledger(unsafe_ledger, live_orders_enabled=True)
        unsafe = build_record(self_test_args(unsafe_ledger, retest, good_snapshot, overlay_path))
        assert unsafe["status"] == "blocked_ledger_safety_flags", unsafe
        assert len(read_json(unsafe_ledger, {}).get("open_positions", [])) == 0
    return {
        "status": "ok",
        "cases": [
            "binance_public_fetch_falls_back_to_second_host",
            "triggered_candidate_opens_one_tiny_paper_scout",
            "triggered_candidate_preserves_market_context",
            "missing_market_context_blocks_new_scout",
            "duplicate_symbol_blocks_second_open",
            "unconfirmed_breakout_blocks_open",
            "binance_public_fetch_failure_blocks_without_crash",
            "ledger_safety_flags_block_mutation",
        ],
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Open tiny paper scouts from top-blocked retest lab candidates.")
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    parser.add_argument("--retest-json", type=Path)
    parser.add_argument("--report-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--experiment-dir", type=Path, default=EXPERIMENTS_DIR)
    parser.add_argument("--capital", type=float, default=500.0)
    parser.add_argument("--notional-usd", type=float, default=25.0)
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--max-open-positions", type=int, default=3)
    parser.add_argument("--min-oos-trades", type=int, default=3)
    parser.add_argument("--min-oos-win-rate-pct", type=float, default=55.0)
    parser.add_argument("--min-oos-net-return-pct", type=float, default=2.0)
    parser.add_argument("--max-oos-drawdown-pct", type=float, default=15.0)
    parser.add_argument("--max-spread-bps", type=float, default=12.0)
    parser.add_argument("--min-depth-usd", type=float, default=15000.0)
    parser.add_argument("--min-quote-volume-usd", type=float, default=100000.0)
    parser.add_argument("--commission-bps", type=float, default=10.0)
    parser.add_argument("--slippage-bps", type=float, default=8.0)
    parser.add_argument("--stop-pct", type=float, default=-4.0)
    parser.add_argument("--take-profit-pct", type=float, default=8.0)
    parser.add_argument("--max-holding-hours", type=float, default=48.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overlay-path", type=Path, default=pso.OVERLAY_PATH)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    parser.set_defaults(offline_snapshot=None)
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(run_self_test(), ensure_ascii=False, indent=2))
        return 0
    record = build_record(args)
    out_stamp = record["run_id"].removesuffix("-top-blocked-retest-quality-scout-sampler")
    report_path = args.report_dir / f"{now_local().strftime('%Y-%m-%d')}-top-blocked-retest-quality-scout-sampler-{out_stamp}.md"
    experiment_path = args.experiment_dir / f"{out_stamp}-top-blocked-retest-quality-scout-sampler.json"
    record["outputs"] = {"report": rel(report_path), "experiment": rel(experiment_path)}
    write_json(experiment_path, record)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown(record), encoding="utf-8")
    if args.compact_output:
        print(json.dumps({
            "status": record.get("status"),
            "run_id": record.get("run_id"),
            "candidate_count": record.get("candidate_count"),
            "opened_count": record.get("opened_count"),
            "blocked_count": record.get("blocked_count"),
            "ledger_mutated": record.get("ledger_mutated"),
            "live_orders_enabled": False,
            "private_api_used": False,
            "outputs": record.get("outputs"),
        }, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
