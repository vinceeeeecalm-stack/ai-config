#!/usr/bin/env python3
"""Stable shadow runner for target-sprint sandbox review.

Runs from /private/tmp so it is not affected by workspace files becoming
compressed,dataless. It reviews only the independent target-sprint sandbox
ledger, fetches Binance public spot market data, updates sandbox-only open or
closed paper positions, and writes readable reports plus JSON experiments.

No real orders. No private API. No main ledger mutation.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PRIMARY_ROOT = Path("/Users/vincentpan/Documents/investing/active-alpha-paper-monitor")
SHADOW_ROOT = Path("/private/tmp/active-alpha-paper-monitor-shadow/active-alpha-paper-monitor")
LEDGER_REL = Path("paper_trades/target_sprint_sandbox_ledger.json")
LEARNING_REL = Path("paper_trades/target_sprint_sandbox_learning_state.json")
BINANCE_BASE = "https://data-api.binance.vision"
LIVE_ORDERS_ENABLED = False
PRIVATE_API_USED = False
ALLOW_REAL_ORDERS = False
MAIN_LEDGER_MUTATED = False


class FileWriteTimeout(RuntimeError):
    pass


def _alarm_handler(signum: int, frame: Any) -> None:  # noqa: ARG001
    raise FileWriteTimeout("file write timed out")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    return now_utc().astimezone(timezone(timedelta(hours=8)))


def run_id() -> str:
    return f"{now_local().strftime('%Y%m%d-%H%M%S')}-stable-target-sprint-sandbox-review"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def http_json(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 8) -> Any:
    query = f"?{urlencode(params)}" if params else ""
    url = f"{BINANCE_BASE}{path}{query}"
    req = Request(url, headers={"User-Agent": "active-alpha-paper-monitor/stable-sandbox-review"})
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{url}: {type(exc).__name__}: {exc}") from exc


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def best_effort_write_json(path: Path, payload: Dict[str, Any], timeout_seconds: int = 4) -> Optional[str]:
    old_handler = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(timeout_seconds)
        write_json(path, payload)
        signal.alarm(0)
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{path}: {type(exc).__name__}: {exc}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def best_effort_write_text(path: Path, text: str, timeout_seconds: int = 4) -> Optional[str]:
    old_handler = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(timeout_seconds)
        write_text(path, text)
        signal.alarm(0)
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{path}: {type(exc).__name__}: {exc}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def write_dual_json(rel: Path, payload: Dict[str, Any]) -> None:
    write_json(SHADOW_ROOT / rel, payload)
    best_effort_write_json(PRIMARY_ROOT / rel, payload)


def load_ledger() -> Tuple[Dict[str, Any], Path]:
    shadow = SHADOW_ROOT / LEDGER_REL
    primary = PRIMARY_ROOT / LEDGER_REL
    if shadow.exists():
        return read_json(shadow), shadow
    return read_json(primary), primary


def depth_within(mid: float, levels: List[List[str]], side: str, pct_width: float = 1.0) -> float:
    if mid <= 0:
        return 0.0
    total = 0.0
    for level in levels:
        if len(level) < 2:
            continue
        price = as_float(level[0])
        qty = as_float(level[1])
        if side == "bid" and price >= mid * (1.0 - pct_width / 100.0):
            total += price * qty
        elif side == "ask" and price <= mid * (1.0 + pct_width / 100.0):
            total += price * qty
    return total


def market_snapshot(symbol: str) -> Dict[str, Any]:
    book = http_json("/api/v3/ticker/bookTicker", {"symbol": symbol})
    ticker = http_json("/api/v3/ticker/24hr", {"symbol": symbol})
    depth = http_json("/api/v3/depth", {"symbol": symbol, "limit": 100})
    bid = as_float(book.get("bidPrice"))
    ask = as_float(book.get("askPrice"))
    mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else as_float(ticker.get("lastPrice"))
    return {
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": as_float(ticker.get("lastPrice"), mid),
        "price_change_24h_pct": as_float(ticker.get("priceChangePercent")),
        "quote_volume_24h_usd": as_float(ticker.get("quoteVolume")),
        "spread_bps": round((ask - bid) / mid * 10_000.0, 6) if mid > 0 and ask >= bid else None,
        "bid_depth_1pct_usd": round(depth_within(mid, depth.get("bids") or [], "bid"), 6),
        "ask_depth_1pct_usd": round(depth_within(mid, depth.get("asks") or [], "ask"), 6),
        "source": "binance_public_market_data",
    }


def kline_momentum_snapshot(symbol: str, interval: str = "15m", limit: int = 12) -> Dict[str, Any]:
    """Small public-data momentum panel used only for paper exit timing."""
    try:
        rows = http_json("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    except Exception as exc:  # noqa: BLE001
        return {
            "symbol": symbol,
            "interval": interval,
            "status": "market_error",
            "error": f"{type(exc).__name__}: {exc}",
        }

    candles: List[Dict[str, float]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, list) or len(row) < 8:
            continue
        candles.append(
            {
                "open": as_float(row[1]),
                "high": as_float(row[2]),
                "low": as_float(row[3]),
                "close": as_float(row[4]),
                "quote_volume": as_float(row[7]),
            }
        )

    if len(candles) < 4:
        return {"symbol": symbol, "interval": interval, "status": "insufficient_klines", "bars": len(candles)}

    closes = [item["close"] for item in candles if item["close"] > 0]
    if len(closes) < 4:
        return {"symbol": symbol, "interval": interval, "status": "invalid_klines", "bars": len(candles)}

    def ret_from(index_from_end: int) -> Optional[float]:
        if len(closes) <= index_from_end or closes[-index_from_end] <= 0:
            return None
        return (closes[-1] / closes[-index_from_end] - 1.0) * 100.0

    recent = candles[-4:]
    older_volumes = [item["quote_volume"] for item in candles[:-1] if item["quote_volume"] > 0]
    avg_volume = sum(older_volumes) / len(older_volumes) if older_volumes else 0.0
    volume_ratio = candles[-1]["quote_volume"] / avg_volume if avg_volume > 0 else None
    red_bars_4 = sum(1 for item in recent if item["close"] < item["open"])
    lower_closes_3 = all(closes[-i] < closes[-i - 1] for i in range(1, min(3, len(closes) - 1) + 1))
    return {
        "symbol": symbol,
        "interval": interval,
        "status": "verified",
        "bars": len(candles),
        "ret_15m_pct": round(ret_from(2) or 0.0, 6),
        "ret_45m_pct": round(ret_from(4) or 0.0, 6),
        "ret_90m_pct": round(ret_from(7) or 0.0, 6) if len(closes) >= 7 else None,
        "red_bars_4": red_bars_4,
        "lower_closes_3": lower_closes_3,
        "latest_quote_volume_usd": round(candles[-1]["quote_volume"], 6),
        "latest_volume_ratio": round(volume_ratio, 6) if volume_ratio is not None else None,
        "source": "binance_public_klines",
    }


def position_age_minutes(position: Dict[str, Any]) -> Optional[float]:
    opened_at = parse_iso(position.get("opened_at"))
    if not opened_at:
        return None
    return max(0.0, (now_utc() - opened_at).total_seconds() / 60.0)


def dynamic_exit_reason(
    position: Dict[str, Any],
    pnl_pct: float,
    highest: float,
    market: Dict[str, Any],
    momentum: Dict[str, Any],
) -> Optional[str]:
    """Target-sprint paper exits that recycle weak capital without touching real funds."""
    age = position_age_minutes(position)
    if age is None:
        return None

    ret_45m = as_float(momentum.get("ret_45m_pct"))
    ret_90m = as_float(momentum.get("ret_90m_pct"))
    red_bars_4 = int(as_float(momentum.get("red_bars_4")))
    lower_closes_3 = bool(momentum.get("lower_closes_3"))
    spread_bps = as_float(market.get("spread_bps"), 999.0)
    bid_depth = as_float(market.get("bid_depth_1pct_usd"))
    change_24h = as_float(market.get("price_change_24h_pct"))

    if highest >= 2.0 and pnl_pct <= max(0.3, highest - 1.2):
        return "sandbox_trailing_profit_protection"

    if age >= 30 and pnl_pct <= -1.2 and ret_45m <= -1.0 and red_bars_4 >= 2:
        return "sandbox_fast_adverse_momentum_recycle"

    if age >= 60 and pnl_pct <= -0.85 and highest < 0.75 and ret_45m <= -0.35 and red_bars_4 >= 2:
        return "sandbox_capital_recycle_weakness"

    if age >= 75 and pnl_pct <= -0.55 and highest < 0.5 and change_24h <= -3.0 and (ret_45m <= 0 or lower_closes_3):
        return "sandbox_capital_recycle_broad_weakness"

    if age >= 120 and pnl_pct <= -0.35 and highest < 0.5 and ret_90m <= -0.4:
        return "sandbox_slow_bleed_recycle"

    if age >= 180 and pnl_pct <= 0.15 and highest < 0.6 and ret_90m <= 0:
        return "sandbox_stagnation_recycle"

    if age >= 45 and pnl_pct <= 0 and (spread_bps > 25 or bid_depth < 25_000):
        return "sandbox_liquidity_degraded_recycle"

    return None


def sell_quote(market: Dict[str, Any], quantity: float, commission_bps: float = 10.0, slippage_bps: float = 8.0) -> Dict[str, Any]:
    execution_price = as_float(market.get("bid")) * (1.0 - slippage_bps / 10_000.0)
    gross = max(0.0, execution_price * quantity)
    commission = gross * commission_bps / 10_000.0
    net = max(0.0, gross - commission)
    return {
        "execution_price": round(execution_price, 12),
        "gross_quote_usd": round(gross, 6),
        "commission_usd": round(commission, 6),
        "net_quote_after_commission_usd": round(net, 6),
        "commission_bps": commission_bps,
        "slippage_bps": slippage_bps,
    }


def fallback_open_mark(position: Dict[str, Any]) -> float:
    """Conservative value when public market data is temporarily unavailable."""
    current_value = as_float(position.get("current_value_usd"), default=-1.0)
    if current_value >= 0:
        return current_value
    notional = as_float(position.get("notional_usd"))
    prior_pnl = as_float(position.get("unrealized_pnl_usd"), default=0.0)
    if notional > 0:
        return max(0.0, notional + prior_pnl)
    entry_price = as_float(position.get("entry_price"))
    quantity = as_float(position.get("quantity"))
    return max(0.0, entry_price * quantity)


def recompute_max_drawdown_pct(ledger: Dict[str, Any], initial: float) -> float:
    peak = max(initial, 0.0)
    max_dd = 0.0
    for event in ledger.get("events") or []:
        if event.get("excluded_from_drawdown"):
            continue
        equity = as_float(event.get("equity_usd"), default=0.0)
        if equity <= 0:
            continue
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, (equity / peak - 1.0) * 100.0)
    return round(max_dd, 6)


def paper_order_id(symbol: str, index: int) -> str:
    return f"stable-tsbox-{now_local().strftime('%Y%m%d-%H%M%S')}-sell-{symbol}-{index:04d}"


def review_ledger(ledger: Dict[str, Any], rid: str) -> Dict[str, Any]:
    reviewed: List[Dict[str, Any]] = []
    closed_actions: List[Dict[str, Any]] = []
    remaining: List[Dict[str, Any]] = []
    open_value = 0.0

    for position in ledger.get("open_positions") or []:
        symbol = position.get("symbol")
        if not symbol:
            continue
        try:
            market = market_snapshot(symbol)
        except Exception as exc:  # noqa: BLE001
            mark = fallback_open_mark(position)
            notional = as_float(position.get("notional_usd"))
            pnl = mark - notional
            pnl_pct = pnl / notional * 100.0 if notional > 0 else 0.0
            reviewed.append(
                {
                    "symbol": symbol,
                    "status": "market_error",
                    "reason": "market_data_unavailable_fallback_mark",
                    "error": f"{type(exc).__name__}: {exc}",
                    "age_minutes": round(position_age_minutes(position) or 0.0, 3),
                    "net_pnl_usd": round(pnl, 6),
                    "net_pnl_pct": round(pnl_pct, 6),
                    "highest_unrealized_pnl_pct": position.get("highest_unrealized_pnl_pct"),
                    "stop_pct": position.get("stop_pct"),
                    "take_profit_pct": position.get("take_profit_pct"),
                    "expires_at": position.get("expires_at"),
                    "data_quality_status": "degraded",
                }
            )
            open_value += mark
            remaining.append(position)
            continue

        quote = sell_quote(market, as_float(position.get("quantity")))
        mark = as_float(quote.get("net_quote_after_commission_usd"))
        notional = as_float(position.get("notional_usd"))
        pnl = mark - notional
        pnl_pct = pnl / notional * 100.0 if notional > 0 else 0.0
        highest = max(as_float(position.get("highest_unrealized_pnl_pct")), pnl_pct)
        expires_at = parse_iso(position.get("expires_at"))
        momentum = kline_momentum_snapshot(symbol)
        reason = None
        if pnl_pct <= as_float(position.get("stop_pct"), -3.5):
            reason = "sandbox_stop_loss"
        elif pnl_pct >= as_float(position.get("take_profit_pct"), 6.0):
            reason = "sandbox_take_profit"
        elif expires_at and now_utc() >= expires_at:
            reason = "sandbox_expiry"
        else:
            reason = dynamic_exit_reason(position, pnl_pct, highest, market, momentum)

        position["last_reviewed_at"] = iso(now_utc())
        position["last_market"] = market
        position["last_momentum"] = momentum
        position["highest_unrealized_pnl_pct"] = round(highest, 6)
        position["unrealized_pnl_usd"] = round(pnl, 6)
        position["unrealized_pnl_pct"] = round(pnl_pct, 6)

        row = {
            "symbol": symbol,
            "status": "closed" if reason else "open",
            "reason": reason,
            "age_minutes": round(position_age_minutes(position) or 0.0, 3),
            "net_pnl_usd": round(pnl, 6),
            "net_pnl_pct": round(pnl_pct, 6),
            "highest_unrealized_pnl_pct": round(highest, 6),
            "stop_pct": position.get("stop_pct"),
            "take_profit_pct": position.get("take_profit_pct"),
            "expires_at": position.get("expires_at"),
            "market": market,
            "momentum": momentum,
        }
        reviewed.append(row)

        if reason:
            closed = dict(position)
            closed.update(
                {
                    "status": "closed",
                    "outcome": "hit" if pnl > 0 else "failed",
                    "closed_at": iso(now_utc()),
                    "exit_reason": reason,
                    "exit_execution": quote,
                    "exit_price": quote["execution_price"],
                    "realized_net_proceeds_usd": round(mark, 6),
                    "realized_pnl_usd": round(pnl, 6),
                    "realized_pnl_pct": round(pnl_pct, 6),
                    "live_orders_enabled": False,
                    "private_api_used": False,
                    "main_ledger_mutated": False,
                }
            )
            ledger.setdefault("closed_trades", []).append(closed)
            ledger["cash_usd"] = round(as_float(ledger.get("cash_usd")) + mark, 6)
            order = {
                "order_id": paper_order_id(symbol, len(ledger.get("paper_orders") or []) + 1),
                "paper_trade_id": position.get("paper_trade_id"),
                "symbol": symbol,
                "side": "sell",
                "status": "FILLED",
                "created_at": iso(now_utc()),
                "run_id": rid,
                "execution": quote,
                "live_orders_enabled": False,
                "private_api_used": False,
                "main_ledger_mutated": False,
            }
            ledger.setdefault("paper_orders", []).append(order)
            closed_actions.append({"position": closed, "order": order})
        else:
            remaining.append(position)
            open_value += mark

    ledger["open_positions"] = remaining
    ledger["open_value_usd"] = round(open_value, 6)
    ledger["equity_usd"] = round(as_float(ledger.get("cash_usd")) + open_value, 6)
    initial = as_float(ledger.get("initial_capital_usd"), 500.0)
    ledger["net_return_pct"] = round((as_float(ledger.get("equity_usd")) / initial - 1.0) * 100.0, 6) if initial > 0 else 0.0
    ledger["realized_pnl_usd"] = round(sum(as_float(item.get("realized_pnl_usd")) for item in ledger.get("closed_trades") or []), 6)
    ledger["updated_at"] = iso(now_utc())
    ledger.setdefault("events", []).append(
        {
            "event_type": "stable_target_sprint_sandbox_review",
            "created_at": iso(now_utc()),
            "run_id": rid,
            "equity_usd": ledger["equity_usd"],
            "cash_usd": ledger["cash_usd"],
            "open_count": len(remaining),
            "closed_actions": len(closed_actions),
            "live_orders_enabled": False,
            "private_api_used": False,
            "main_ledger_mutated": False,
        }
    )
    ledger["max_drawdown_pct"] = recompute_max_drawdown_pct(ledger, initial)
    return {"reviewed": reviewed, "closed_actions": closed_actions}


def monthly_target(ledger: Dict[str, Any]) -> Dict[str, Any]:
    month = now_local().strftime("%Y-%m")
    baseline = (ledger.get("monthly_goal_baselines") or {}).get(month) or {}
    target = as_float(baseline.get("target_equity_usd"), 1000.0)
    equity = as_float(ledger.get("equity_usd"))
    return {
        "month_id": month,
        "current_equity_usd": round(equity, 6),
        "target_equity_usd": round(target, 6),
        "gap_to_target_usd": round(max(0.0, target - equity), 6),
        "progress_to_target_pct": round(equity / target * 100.0, 6) if target > 0 else None,
    }


def build_learning_state(ledger: Dict[str, Any], rid: str) -> Dict[str, Any]:
    by_symbol: Dict[str, Dict[str, Any]] = {}
    by_mode: Dict[str, Dict[str, Any]] = {}

    def update_bucket(bucket: Dict[str, Dict[str, Any]], key: str, trade: Dict[str, Any]) -> None:
        item = bucket.setdefault(
            key,
            {
                "closed_count": 0,
                "win_count": 0,
                "loss_count": 0,
                "realized_pnl_usd": 0.0,
                "realized_return_on_notional_pct": 0.0,
                "stop_loss_count": 0,
                "latest_closed_at": None,
                "cooldown": False,
                "cooldown_reason": None,
            },
        )
        pnl = as_float(trade.get("realized_pnl_usd"))
        notional = as_float(trade.get("notional_usd"))
        item["closed_count"] += 1
        item["win_count"] += 1 if pnl > 0 else 0
        item["loss_count"] += 1 if pnl <= 0 else 0
        item["realized_pnl_usd"] = round(as_float(item.get("realized_pnl_usd")) + pnl, 6)
        if notional > 0:
            total_notional = as_float(item.get("_notional_sum")) + notional
            item["_notional_sum"] = total_notional
            item["realized_return_on_notional_pct"] = round(item["realized_pnl_usd"] / total_notional * 100.0, 6)
        if trade.get("exit_reason") == "sandbox_stop_loss":
            item["stop_loss_count"] += 1
        item["latest_closed_at"] = trade.get("closed_at") or item.get("latest_closed_at")
        if item["realized_pnl_usd"] < 0 or item["stop_loss_count"] > 0:
            item["cooldown"] = True
            item["cooldown_reason"] = "negative_forward_sandbox_evidence"

    for trade in ledger.get("closed_trades") or []:
        if not isinstance(trade, dict):
            continue
        symbol = str(trade.get("symbol") or "UNKNOWN")
        mode = str(trade.get("paper_entry_mode") or "UNKNOWN")
        update_bucket(by_symbol, symbol, trade)
        update_bucket(by_mode, mode, trade)

    for bucket in (by_symbol, by_mode):
        for item in bucket.values():
            item.pop("_notional_sum", None)
            count = as_float(item.get("closed_count"))
            item["win_rate_pct"] = round(as_float(item.get("win_count")) / count * 100.0, 6) if count > 0 else None

    state = {
        "run_id": rid,
        "created_at": iso(now_utc()),
        "source": "stable_target_sprint_sandbox_review",
        "live_orders_enabled": False,
        "private_api_used": False,
        "main_ledger_mutated": False,
        "summary": {
            "closed_count": len(ledger.get("closed_trades") or []),
            "open_count": len(ledger.get("open_positions") or []),
            "realized_pnl_usd": round(sum(as_float(item.get("realized_pnl_usd")) for item in ledger.get("closed_trades") or []), 6),
            "cooldown_symbols": [symbol for symbol, item in by_symbol.items() if item.get("cooldown")],
            "cooldown_modes": [mode for mode, item in by_mode.items() if item.get("cooldown")],
        },
        "by_symbol": by_symbol,
        "by_entry_mode": by_mode,
        "policy": {
            "cooldown_rule": "Any symbol or entry mode with negative forward sandbox PnL or stop-loss evidence should not be promoted to main ledger.",
            "scope": "sandbox_only_forward_evidence",
        },
    }
    write_dual_json(LEARNING_REL, state)
    return state


def render_report(payload: Dict[str, Any]) -> str:
    ledger = payload["ledger_summary"]
    learning = payload.get("learning_state", {})
    lines = [
        f"# Stable Target Sprint Sandbox Review - {payload['created_at_local']}",
        "",
        "## 结论",
        f"- 本轮复盘 `{len(payload['reviewed_positions'])}` 个 sandbox open 仓，平仓 `{len(payload['closed_actions'])}` 个。",
        f"- Sandbox 当前权益 `${ledger['equity_usd']}`，现金 `${ledger['cash_usd']}`，open `{ledger['open_count']}`，closed `{ledger['closed_count']}`。",
        f"- 目标缺口 `${ledger['monthly_target']['gap_to_target_usd']}`；当前仍不能证明完成复利目标。",
        "",
        "## 仓位复盘",
    ]
    for row in payload["reviewed_positions"]:
        lines.append(
            f"- `{row.get('symbol')}` `{row.get('status')}` PnL `{row.get('net_pnl_pct')}%` / `${row.get('net_pnl_usd')}` "
            f"age `{row.get('age_minutes')}m` stop `{row.get('stop_pct')}%` take `{row.get('take_profit_pct')}%` reason `{row.get('reason')}`"
        )
    if not payload["reviewed_positions"]:
        lines.append("- 无 open 仓。")
    lines.extend(
        [
            "",
        "## 安全边界",
            "- `live_orders_enabled=false`，`private_api_used=false`，`allow_real_orders=false`，`main_ledger_mutated=false`。",
            "- 只使用 Binance public market data；未调用账户、真实订单、提现、margin、futures、perpetual 或签名私有 API。",
            f"- Ledger: `{payload['ledger_path']}`",
            "",
            "## 学习状态",
            f"- Cooldown symbols: `{', '.join((learning.get('summary') or {}).get('cooldown_symbols') or []) or 'none'}`",
            f"- Cooldown modes: `{', '.join((learning.get('summary') or {}).get('cooldown_modes') or []) or 'none'}`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Stable target-sprint sandbox review")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    rid = run_id()
    created = now_utc()
    ledger, source_path = load_ledger()
    review = review_ledger(ledger, rid)
    write_warnings: List[str] = []
    write_json(SHADOW_ROOT / LEDGER_REL, ledger)
    warning = best_effort_write_json(PRIMARY_ROOT / LEDGER_REL, ledger)
    if warning:
        write_warnings.append(warning)
    learning = build_learning_state(ledger, rid)

    summary = {
        "cash_usd": round(as_float(ledger.get("cash_usd")), 6),
        "equity_usd": round(as_float(ledger.get("equity_usd")), 6),
        "open_value_usd": round(as_float(ledger.get("open_value_usd")), 6),
        "open_count": len(ledger.get("open_positions") or []),
        "closed_count": len(ledger.get("closed_trades") or []),
        "paper_order_count": len(ledger.get("paper_orders") or []),
        "realized_pnl_usd": round(as_float(ledger.get("realized_pnl_usd")), 6),
        "net_return_pct": ledger.get("net_return_pct"),
        "max_drawdown_pct": ledger.get("max_drawdown_pct"),
        "monthly_target": monthly_target(ledger),
    }
    payload = {
        "run_id": rid,
        "created_at": iso(created),
        "created_at_local": now_local().isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "mode": "stable_target_sprint_sandbox_review",
        "live_orders_enabled": LIVE_ORDERS_ENABLED,
        "private_api_used": PRIVATE_API_USED,
        "allow_real_orders": ALLOW_REAL_ORDERS,
        "main_ledger_mutated": MAIN_LEDGER_MUTATED,
        "ledger_path": str(PRIMARY_ROOT / LEDGER_REL),
        "ledger_shadow_path": str(SHADOW_ROOT / LEDGER_REL),
        "ledger_source_path": str(source_path),
        "ledger_summary": summary,
        "reviewed_positions": review["reviewed"],
        "closed_actions": review["closed_actions"],
        "learning_state": learning,
        "write_warnings": write_warnings,
    }
    stamp = now_local().strftime("%Y-%m-%d-%H%M")
    experiment_rel = Path("experiments") / f"{rid}.json"
    report_rel = Path("reports") / f"{stamp}-stable-target-sprint-sandbox-review.md"
    payload["outputs"] = {
        "experiment": str(PRIMARY_ROOT / experiment_rel),
        "report": str(PRIMARY_ROOT / report_rel),
        "shadow_experiment": str(SHADOW_ROOT / experiment_rel),
        "shadow_report": str(SHADOW_ROOT / report_rel),
    }
    report = render_report(payload)
    write_json(SHADOW_ROOT / experiment_rel, payload)
    write_text(SHADOW_ROOT / report_rel, report)
    for warning in [
        best_effort_write_json(PRIMARY_ROOT / experiment_rel, payload),
        best_effort_write_text(PRIMARY_ROOT / report_rel, report),
    ]:
        if warning:
            payload.setdefault("write_warnings", []).append(warning)
    if payload.get("write_warnings"):
        write_json(SHADOW_ROOT / experiment_rel, payload)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
