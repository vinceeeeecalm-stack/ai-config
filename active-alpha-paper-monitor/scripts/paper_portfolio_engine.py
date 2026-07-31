#!/usr/bin/env python3
"""
Paper portfolio engine for active-alpha-paper-monitor.

Research-only. No private keys. No live orders. It opens and reviews virtual
spot positions using public Binance prices, then records cash, open positions,
closed trades, and mark-to-market equity in a local JSON ledger.
"""

import argparse
import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_LEDGER = "active-alpha-paper-monitor/paper_trades/paper_portfolio_ledger.json"
UA = "Mozilla/5.0 (compatible; active-alpha-paper-monitor/paper-portfolio; research-only)"
CHINA_TZ = timezone(timedelta(hours=8))


def utc_now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value):
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def parse_holding_window(text):
    text = str(text).strip().lower()
    if text.endswith("d"):
        return timedelta(days=float(text[:-1]))
    if text.endswith("h"):
        return timedelta(hours=float(text[:-1]))
    raise ValueError("max holding window must end with d or h, e.g. 7d or 72h")


def fetch_binance_price(symbol):
    params = urllib.parse.urlencode({"symbol": symbol.upper()})
    url = f"https://api.binance.com/api/v3/ticker/price?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return float(data["price"])


def load_ledger(path, initial_capital):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text())
    now = iso(utc_now())
    return {
        "ledger_version": "paper-portfolio-v1",
        "created_at": now,
        "updated_at": now,
        "live_orders_enabled": False,
        "initial_capital_usd": float(initial_capital),
        "cash_usd": float(initial_capital),
        "open_positions": [],
        "closed_trades": [],
        "events": [],
    }


def save_ledger(path, ledger):
    ledger["updated_at"] = iso(utc_now())
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")


def money(value):
    try:
        return f"${float(value):.6f}"
    except Exception:
        return "-"


def pct_text(value):
    try:
        return f"{float(value):+.4f}%"
    except Exception:
        return "-"


def reports_dir_for_ledger(path):
    ledger_path = Path(path)
    if ledger_path.parent.name == "paper_trades":
        return ledger_path.parent.parent / "reports"
    return Path("active-alpha-paper-monitor/reports")


def paper_trades_dir_for_ledger(path):
    ledger_path = Path(path)
    if ledger_path.parent.name == "paper_trades":
        return ledger_path.parent
    return Path("active-alpha-paper-monitor/paper_trades")


def render_positions_table(positions):
    lines = [
        "| Trade | Symbol | Mode | Entry | Last | Notional | PnL | PnL % | Stop | Take Profit | Expiry |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    if not positions:
        lines.append("| none | - | - | - | - | - | - | - | - | - | - |")
        return lines
    for pos in positions:
        lines.append(
            "| "
            f"`{pos.get('paper_trade_id')}` | `{pos.get('symbol')}` | "
            f"{pos.get('paper_entry_mode', pos.get('strategy_family', '-'))} | "
            f"{pos.get('entry_price', '-')} | {pos.get('last_price', '-')} | {money(pos.get('notional_usd'))} | "
            f"{money(pos.get('unrealized_pnl_usd'))} | {pct_text(pos.get('unrealized_pnl_pct'))} | "
            f"{pos.get('stop_price', '-')} | {pos.get('take_profit_price', '-')} | {pos.get('expires_at', '-')} |"
        )
    return lines


def render_closed_table(trades):
    lines = [
        "| Trade | Symbol | Outcome | Exit Reason | Entry | Exit | Realized PnL | PnL % | Closed At |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    if not trades:
        lines.append("| none | - | - | - | - | - | - | - | - |")
        return lines
    for trade in trades[-8:]:
        lines.append(
            "| "
            f"`{trade.get('paper_trade_id')}` | `{trade.get('symbol')}` | {trade.get('outcome', '-')} | "
            f"{trade.get('exit_reason', '-')} | {trade.get('entry_price', '-')} | {trade.get('exit_price', '-')} | "
            f"{money(trade.get('realized_pnl_usd'))} | {pct_text(trade.get('realized_pnl_pct'))} | {trade.get('closed_at', '-')} |"
        )
    return lines


def render_review_report(ledger, reviewed, now):
    lines = [
        f"# Paper Portfolio Review | {now.astimezone(CHINA_TZ).strftime('%Y-%m-%d %H:%M')}",
        "",
        "No live orders were placed. This review only updates the paper ledger.",
        "",
        "## Portfolio",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Cash | {money(ledger.get('cash_usd'))} |",
        f"| Open value | {money(ledger.get('open_value_usd'))} |",
        f"| Equity | {money(ledger.get('equity_usd'))} |",
        f"| Net return | {pct_text(ledger.get('net_return_pct'))} |",
        f"| Max drawdown | {pct_text(ledger.get('max_drawdown_pct'))} |",
        "",
        "## Current Holdings",
        "",
        *render_positions_table(ledger.get("open_positions", [])),
        "",
        "## Reviewed Actions",
        "",
        "| Trade | Status | Last/Exit | Reason |",
        "|---|---|---:|---|",
    ]
    if reviewed:
        for item in reviewed:
            lines.append(
                f"| `{item.get('paper_trade_id')}` | {item.get('status', '-')} | "
                f"{item.get('last_price', item.get('exit_price', '-'))} | {item.get('exit_reason', '-')} |"
            )
    else:
        lines.append("| none | - | - | - |")
    lines.extend(
        [
            "",
            "## Trade Tracking",
            "",
            *render_closed_table(ledger.get("closed_trades", [])),
            "",
            "## Monitored Symbols",
            "",
            "| Symbol | Trade | Stop | Take Profit | Expiry | Status |",
            "|---|---|---:|---:|---|---|",
        ]
    )
    if ledger.get("open_positions"):
        for pos in ledger["open_positions"]:
            lines.append(
                f"| `{pos.get('symbol')}` | `{pos.get('paper_trade_id')}` | {pos.get('stop_price', '-')} | "
                f"{pos.get('take_profit_price', '-')} | {pos.get('expires_at', '-')} | {pos.get('status', '-')} |"
            )
    else:
        lines.append("| none | - | - | - | - | - |")
    lines.append("")
    return "\n".join(lines)


def write_review_report(path, ledger, reviewed, now):
    reports_dir = reports_dir_for_ledger(path)
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{now.astimezone(CHINA_TZ).date().isoformat()}-paper-portfolio-review.md"
    report_path.write_text(render_review_report(ledger, reviewed, now))
    return report_path


def write_review_json(path, ledger, reviewed, now):
    paper_trades_dir = paper_trades_dir_for_ledger(path)
    paper_trades_dir.mkdir(parents=True, exist_ok=True)
    review_path = paper_trades_dir / f"{now.astimezone(CHINA_TZ).date().isoformat()}-paper-review.json"
    payload = {
        "reviewed_at": iso(now),
        "live_orders_enabled": False,
        "reviewed": reviewed,
        "cash_usd": ledger.get("cash_usd"),
        "open_value_usd": ledger.get("open_value_usd"),
        "equity_usd": ledger.get("equity_usd"),
        "net_return_pct": ledger.get("net_return_pct"),
        "open_positions": ledger.get("open_positions", []),
        "closed_trades": ledger.get("closed_trades", []),
    }
    review_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return review_path


def mark_to_market(ledger, price_by_symbol):
    open_value = 0.0
    for pos in ledger["open_positions"]:
        price = price_by_symbol.get(pos["symbol"])
        if price is None:
            price = pos.get("last_price", pos["entry_price"])
        quantity = pos["quantity"]
        gross_pnl = (price - pos["entry_price"]) * quantity
        if pos.get("realistic_execution_enabled"):
            entry_commission = float(pos.get("entry_commission_usd", 0.0))
            commission_bps = float(pos.get("commission_bps", 0.0))
            exit_commission = price * quantity * commission_bps / 10000.0
            net_pnl = gross_pnl - entry_commission - exit_commission
        else:
            exit_commission = 0.0
            net_pnl = gross_pnl
        pos["last_price"] = round(price, 12)
        pos["unrealized_gross_pnl_usd"] = round(gross_pnl, 6)
        pos["unrealized_exit_commission_usd"] = round(exit_commission, 6)
        pos["unrealized_net_pnl_usd"] = round(net_pnl, 6)
        pos["unrealized_pnl_usd"] = round(net_pnl, 6)
        pos["unrealized_pnl_pct"] = round((net_pnl / pos["notional_usd"]) * 100.0 if pos.get("notional_usd") else 0.0, 4)
        pos["max_unrealized_pnl_usd"] = max(float(pos.get("max_unrealized_pnl_usd", pos["unrealized_pnl_usd"])), pos["unrealized_pnl_usd"])
        pos["min_unrealized_pnl_usd"] = min(float(pos.get("min_unrealized_pnl_usd", pos["unrealized_pnl_usd"])), pos["unrealized_pnl_usd"])
        open_value += price * quantity
    ledger["open_value_usd"] = round(open_value, 6)
    ledger["equity_usd"] = round(ledger["cash_usd"] + open_value, 6)
    ledger["net_return_pct"] = round((ledger["equity_usd"] / ledger["initial_capital_usd"] - 1.0) * 100.0, 4)
    ledger["max_drawdown_pct"] = round(
        min(float(ledger.get("max_drawdown_pct", ledger["net_return_pct"])), ledger["net_return_pct"]),
        4,
    )


def open_position(args):
    ledger = load_ledger(args.ledger, args.capital)
    if any(pos["paper_trade_id"] == args.trade_id for pos in ledger["open_positions"]):
        raise SystemExit(f"paper_trade_id already open: {args.trade_id}")
    if any(pos["paper_trade_id"] == args.trade_id for pos in ledger["closed_trades"]):
        raise SystemExit(f"paper_trade_id already closed: {args.trade_id}")

    entry_price = args.entry_price if args.entry_price is not None else fetch_binance_price(args.symbol)
    notional = float(args.notional)
    if notional <= 0:
        raise SystemExit("notional must be positive")
    if ledger["cash_usd"] + 1e-9 < notional:
        raise SystemExit(f"insufficient paper cash: cash={ledger['cash_usd']} notional={notional}")

    opened_at = utc_now()
    expires_at = opened_at + parse_holding_window(args.max_holding)
    quantity = notional / entry_price
    stop_price = entry_price * (1.0 + args.stop_pct / 100.0)
    take_profit_price = entry_price * (1.0 + args.take_profit_pct / 100.0)
    market_context = {
        "market_regime": "manual_or_cli_open_unknown",
        "market_atmosphere": "manual_or_cli_open_unknown",
        "short_term_state": "manual_or_cli_open_unknown",
        "sentiment_state": "manual_or_cli_open_unknown",
        "source": "paper_portfolio_engine_cli",
    }
    position = {
        "paper_trade_id": args.trade_id,
        "symbol": args.symbol.upper(),
        "side": "long_spot_paper",
        "strategy_family": args.strategy_family,
        "signal_source": args.signal_source,
        "market_regime": market_context["market_regime"],
        "market_atmosphere": market_context["market_atmosphere"],
        "short_term_state": market_context["short_term_state"],
        "sentiment_state": market_context["sentiment_state"],
        "market_context_at_entry": market_context,
        "opened_at": iso(opened_at),
        "expires_at": iso(expires_at),
        "entry_price": round(entry_price, 12),
        "quantity": round(quantity, 12),
        "notional_usd": round(notional, 6),
        "stop_pct": float(args.stop_pct),
        "take_profit_pct": float(args.take_profit_pct),
        "stop_price": round(stop_price, 12),
        "take_profit_price": round(take_profit_price, 12),
        "max_holding_window": args.max_holding,
        "status": "open",
        "outcome": "pending",
        "live_orders_enabled": False,
    }
    ledger["cash_usd"] = round(ledger["cash_usd"] - notional, 6)
    ledger["open_positions"].append(position)
    ledger["events"].append(
        {
            "event_type": "paper_open",
            "created_at": iso(opened_at),
            "paper_trade_id": args.trade_id,
            "symbol": args.symbol.upper(),
            "entry_price": position["entry_price"],
            "notional_usd": position["notional_usd"],
            "notes": "virtual order only; no live order placed",
        }
    )
    mark_to_market(ledger, {args.symbol.upper(): entry_price})
    save_ledger(args.ledger, ledger)
    print(json.dumps({"status": "opened", "position": position, "ledger": ledger}, ensure_ascii=False, indent=2))


def review_positions(args):
    ledger = load_ledger(args.ledger, args.capital)
    now = utc_now()
    still_open = []
    price_cache = {}
    reviewed = []
    for pos in ledger["open_positions"]:
        symbol = pos["symbol"]
        if symbol not in price_cache:
            price_cache[symbol] = fetch_binance_price(symbol)
        price = price_cache[symbol]
        exit_reason = None
        if price <= pos["stop_price"]:
            exit_reason = "stop"
        elif price >= pos["take_profit_price"]:
            exit_reason = "take_profit"
        elif now >= parse_iso(pos["expires_at"]):
            exit_reason = "time_expired"

        if exit_reason is None:
            reviewed.append({"paper_trade_id": pos["paper_trade_id"], "status": "open", "last_price": price})
            still_open.append(pos)
            continue

        proceeds = pos["quantity"] * price
        if pos.get("realistic_execution_enabled"):
            commission_bps = float(pos.get("commission_bps", 0.0))
            exit_commission = proceeds * commission_bps / 10000.0
            net_proceeds = proceeds - exit_commission
            pnl_usd = net_proceeds - pos["notional_usd"]
            pnl_pct = (pnl_usd / pos["notional_usd"]) * 100.0 if pos["notional_usd"] else 0.0
        else:
            exit_commission = 0.0
            net_proceeds = proceeds
            pnl_usd = proceeds - pos["notional_usd"]
            pnl_pct = (price / pos["entry_price"] - 1.0) * 100.0
        closed = dict(pos)
        rounded_pnl = round(pnl_usd, 6)
        closed.update(
            {
                "status": "closed",
                "outcome": "hit" if pnl_usd > 0 else "failed",
                "last_price": round(price, 12),
                "unrealized_gross_pnl_usd": round(proceeds - pos["notional_usd"], 6),
                "unrealized_exit_commission_usd": round(exit_commission, 6),
                "unrealized_net_pnl_usd": rounded_pnl,
                "unrealized_pnl_usd": rounded_pnl,
                "unrealized_pnl_pct": round(pnl_pct, 4),
                "max_unrealized_pnl_usd": max(
                    float(pos.get("max_unrealized_pnl_usd", rounded_pnl)),
                    rounded_pnl,
                ),
                "min_unrealized_pnl_usd": min(
                    float(pos.get("min_unrealized_pnl_usd", rounded_pnl)),
                    rounded_pnl,
                ),
                "closed_at": iso(now),
                "exit_price": round(price, 12),
                "exit_reason": exit_reason,
                "exit_commission_usd": round(exit_commission, 6),
                "realized_gross_proceeds_usd": round(proceeds, 6),
                "realized_net_proceeds_usd": round(net_proceeds, 6),
                "realized_pnl_usd": round(pnl_usd, 6),
                "realized_pnl_pct": round(pnl_pct, 4),
            }
        )
        ledger["cash_usd"] = round(ledger["cash_usd"] + net_proceeds, 6)
        ledger["closed_trades"].append(closed)
        ledger["events"].append(
            {
                "event_type": "paper_close",
                "created_at": iso(now),
                "paper_trade_id": pos["paper_trade_id"],
                "symbol": symbol,
                "exit_reason": exit_reason,
                "exit_price": round(price, 12),
                "realized_pnl_usd": round(pnl_usd, 6),
                "realized_pnl_pct": round(pnl_pct, 4),
            }
        )
        reviewed.append({"paper_trade_id": pos["paper_trade_id"], "status": "closed", "exit_reason": exit_reason})

    ledger["open_positions"] = still_open
    mark_to_market(ledger, price_cache)
    save_ledger(args.ledger, ledger)
    report_path = write_review_report(args.ledger, ledger, reviewed, now)
    review_path = write_review_json(args.ledger, ledger, reviewed, now)
    print(
        json.dumps(
            {
                "status": "reviewed",
                "reviewed": reviewed,
                "ledger": ledger,
                "report_path": str(report_path),
                "review_path": str(review_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def summary(args):
    ledger = load_ledger(args.ledger, args.capital)
    price_cache = {}
    for pos in ledger["open_positions"]:
        try:
            price_cache[pos["symbol"]] = fetch_binance_price(pos["symbol"])
        except Exception:
            price_cache[pos["symbol"]] = pos.get("last_price", pos["entry_price"])
    mark_to_market(ledger, price_cache)
    save_ledger(args.ledger, ledger)
    print(json.dumps(ledger, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["open", "review", "summary"])
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--capital", type=float, default=500.0)
    ap.add_argument("--trade-id", default="")
    ap.add_argument("--symbol", default="")
    ap.add_argument("--strategy-family", default="")
    ap.add_argument("--signal-source", default="")
    ap.add_argument("--notional", type=float, default=0.0)
    ap.add_argument("--entry-price", type=float)
    ap.add_argument("--stop-pct", type=float, default=-8.0)
    ap.add_argument("--take-profit-pct", type=float, default=20.0)
    ap.add_argument("--max-holding", default="7d")
    args = ap.parse_args()

    if args.command == "open":
        missing = [name for name in ["trade_id", "symbol", "strategy_family"] if not getattr(args, name)]
        if missing:
            raise SystemExit(f"missing required args for open: {', '.join(missing)}")
        open_position(args)
    elif args.command == "review":
        review_positions(args)
    else:
        summary(args)


if __name__ == "__main__":
    main()
