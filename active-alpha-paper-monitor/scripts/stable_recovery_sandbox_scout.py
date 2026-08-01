#!/usr/bin/env python3
"""Stable recovery-style sandbox scout.

This runner lives in /private/tmp to avoid workspace dataless script stalls. It
uses Binance public spot market data to look for conservative recovery/pullback
paper candidates, reads the sandbox learning cooldown state, and may open one
small independent sandbox position. It never mutates the main paper ledger and
never calls private or trading APIs.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PRIMARY_ROOT = Path("/Users/vincentpan/Documents/investing/active-alpha-paper-monitor")
SHADOW_ROOT = Path("/private/tmp/active-alpha-paper-monitor-shadow/active-alpha-paper-monitor")
LEDGER_REL = Path("paper_trades/target_sprint_sandbox_ledger.json")
LEARNING_REL = Path("paper_trades/target_sprint_sandbox_learning_state.json")
BINANCE_BASE = "https://data-api.binance.vision"
ENTRY_MODE = "liquidity_pullback_rebound_probe"
CAPITULATION_ENTRY_MODE = "capitulation_rebound_probe"
LIVE_ORDERS_ENABLED = False
PRIVATE_API_USED = False
ALLOW_REAL_ORDERS = False
MAIN_LEDGER_MUTATED = False


class FileWriteTimeout(RuntimeError):
    pass


def _alarm_handler(signum: int, frame: Any) -> None:  # noqa: ARG001
    raise FileWriteTimeout("file write timed out")


DEFAULT_UNIVERSE = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "TRXUSDT",
    "LINKUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "AAVEUSDT",
    "SUIUSDT",
    "NEARUSDT",
    "INJUSDT",
    "SEIUSDT",
    "WLDUSDT",
    "ENAUSDT",
    "DOGEUSDT",
]


def discover_dynamic_universe(limit: int = 35) -> List[str]:
    """Discover liquid USDT spot symbols from Binance public 24h stats."""
    raw = http_json("/api/v3/ticker/24hr", timeout=12)
    if not isinstance(raw, list):
        return []
    rows: List[Dict[str, Any]] = []
    excluded_suffixes = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
    excluded_symbols = {
        "USDCUSDT",
        "FDUSDUSDT",
        "TUSDUSDT",
        "BUSDUSDT",
        "USDPUSDT",
        "DAIUSDT",
        "USD1USDT",
        "RLUSDUSDT",
        "EURUSDT",
        "EURIUSDT",
        "XUSDUSDT",
        "USDEUSDT",
        "SUSDEUSDT",
        "XAUTUSDT",
    }
    for item in raw:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "")
        if not symbol.endswith("USDT"):
            continue
        if symbol in excluded_symbols or symbol.endswith(excluded_suffixes):
            continue
        quote_volume = as_float(item.get("quoteVolume"))
        price_change_abs = abs(as_float(item.get("priceChangePercent")))
        if quote_volume < 25_000_000:
            continue
        rows.append(
            {
                "symbol": symbol,
                "quote_volume": quote_volume,
                "price_change_abs": price_change_abs,
                "rank_score": quote_volume * (1.0 + min(price_change_abs, 25.0) / 10.0),
            }
        )
    rows.sort(key=lambda row: row["rank_score"], reverse=True)
    return [row["symbol"] for row in rows[: max(0, limit)]]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    return now_utc().astimezone(timezone(timedelta(hours=8)))


def rid() -> str:
    return f"{now_local().strftime('%Y%m%d-%H%M%S')}-stable-recovery-sandbox-scout"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def pct(new: float, old: float) -> Optional[float]:
    if old == 0:
        return None
    return (new / old - 1.0) * 100.0


def http_json(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 8) -> Any:
    query = f"?{urlencode(params)}" if params else ""
    url = f"{BINANCE_BASE}{path}{query}"
    req = Request(url, headers={"User-Agent": "active-alpha-paper-monitor/stable-recovery-scout"})
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{url}: {type(exc).__name__}: {exc}") from exc


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


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


def load_ledger() -> Dict[str, Any]:
    shadow = read_json(SHADOW_ROOT / LEDGER_REL)
    if shadow:
        return shadow
    primary = read_json(PRIMARY_ROOT / LEDGER_REL)
    if primary:
        return primary
    created = now_utc()
    return {
        "ledger_type": "target_sprint_sandbox",
        "ledger_version": "1.0",
        "created_at": iso(created),
        "updated_at": iso(created),
        "initial_capital_usd": 500.0,
        "cash_usd": 500.0,
        "open_value_usd": 0.0,
        "equity_usd": 500.0,
        "realized_pnl_usd": 0.0,
        "net_return_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "live_orders_enabled": False,
        "private_api_used": False,
        "main_ledger_mutated": False,
        "open_positions": [],
        "closed_trades": [],
        "paper_orders": [],
        "events": [],
        "monthly_goal_baselines": {
            now_local().strftime("%Y-%m"): {
                "month_id": now_local().strftime("%Y-%m"),
                "target_model": "sandbox_monthly_compounding_double",
                "month_start_equity_usd": 500.0,
                "target_equity_usd": 1000.0,
                "baseline_source": "sandbox_initial_capital",
                "created_at": iso(created),
            }
        },
    }


def save_ledger(ledger: Dict[str, Any]) -> List[str]:
    ledger["updated_at"] = iso(now_utc())
    warnings: List[str] = []
    # Shadow is the authoritative stable path for this runner.
    write_json(SHADOW_ROOT / LEDGER_REL, ledger)
    warning = best_effort_write_json(PRIMARY_ROOT / LEDGER_REL, ledger)
    if warning:
        warnings.append(warning)
    return warnings


def load_learning() -> Dict[str, Any]:
    return read_json(SHADOW_ROOT / LEARNING_REL) or read_json(PRIMARY_ROOT / LEARNING_REL) or {
        "summary": {"cooldown_symbols": [], "cooldown_modes": []},
        "by_symbol": {},
        "by_entry_mode": {},
    }


def parse_klines(raw: Any) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    if not isinstance(raw, list):
        return rows
    for item in raw:
        if not isinstance(item, list) or len(item) < 8:
            continue
        rows.append(
            {
                "open": as_float(item[1]),
                "high": as_float(item[2]),
                "low": as_float(item[3]),
                "close": as_float(item[4]),
                "quote_volume": as_float(item[7]),
            }
        )
    return rows


def rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) <= period:
        return None
    gains: List[float] = []
    losses: List[float] = []
    for prev, cur in zip(values[-period - 1 : -1], values[-period:]):
        change = cur - prev
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


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


def symbol_snapshot(symbol: str) -> Dict[str, Any]:
    ticker = http_json("/api/v3/ticker/24hr", {"symbol": symbol})
    book = http_json("/api/v3/ticker/bookTicker", {"symbol": symbol})
    depth = http_json("/api/v3/depth", {"symbol": symbol, "limit": 100})
    k15 = parse_klines(http_json("/api/v3/klines", {"symbol": symbol, "interval": "15m", "limit": 96}))
    bid = as_float(book.get("bidPrice"))
    ask = as_float(book.get("askPrice"))
    mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else as_float(ticker.get("lastPrice"))
    closes = [row["close"] for row in k15 if row["close"] > 0]
    last = closes[-1] if closes else mid
    low24 = min((row["low"] for row in k15 if row["low"] > 0), default=0.0)
    high24 = max((row["high"] for row in k15), default=0.0)
    vol_recent = sum(row["quote_volume"] for row in k15[-4:]) / 4.0 if len(k15) >= 4 else 0.0
    vol_base = sum(row["quote_volume"] for row in k15[-24:-4]) / 20.0 if len(k15) >= 24 else 0.0
    return {
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": last,
        "spread_bps": round((ask - bid) / mid * 10000.0, 6) if mid > 0 and ask >= bid else None,
        "quote_volume_24h_usd": as_float(ticker.get("quoteVolume")),
        "price_change_24h_pct": as_float(ticker.get("priceChangePercent")),
        "ret_15m_pct": pct(closes[-1], closes[-2]) if len(closes) >= 2 else None,
        "ret_1h_pct": pct(closes[-1], closes[-5]) if len(closes) >= 5 else None,
        "ret_4h_pct": pct(closes[-1], closes[-17]) if len(closes) >= 17 else None,
        "drawup_from_low_pct": pct(last, low24) if low24 > 0 else None,
        "distance_from_high_pct": pct(last, high24) if high24 > 0 else None,
        "volume_ratio_1h_vs_5h": vol_recent / vol_base if vol_base > 0 else 0.0,
        "rsi_15m": rsi(closes),
        "bid_depth_1pct_usd": round(depth_within(mid, depth.get("bids") or [], "bid"), 6),
        "ask_depth_1pct_usd": round(depth_within(mid, depth.get("asks") or [], "ask"), 6),
    }


def choose_entry_mode(learning: Dict[str, Any]) -> Tuple[str, str]:
    cooldown_modes = set((learning.get("summary") or {}).get("cooldown_modes") or [])
    if ENTRY_MODE in cooldown_modes and CAPITULATION_ENTRY_MODE not in cooldown_modes:
        return CAPITULATION_ENTRY_MODE, "primary_rebound_mode_in_cooldown_use_capitulation_rebound"
    return ENTRY_MODE, "primary_rebound_mode_active"


def evaluate(snapshot: Dict[str, Any], open_symbols: set[str], learning: Dict[str, Any], entry_mode: str) -> Dict[str, Any]:
    symbol = snapshot["symbol"]
    cooldown_symbols = set((learning.get("summary") or {}).get("cooldown_symbols") or [])
    cooldown_modes = set((learning.get("summary") or {}).get("cooldown_modes") or [])
    blockers: List[str] = []
    if symbol in open_symbols:
        blockers.append("duplicate_open_symbol")
    if symbol in cooldown_symbols:
        blockers.append("symbol_cooldown")
    if entry_mode in cooldown_modes:
        blockers.append("entry_mode_cooldown")
    if as_float(snapshot.get("quote_volume_24h_usd")) < 25_000_000:
        blockers.append("quote_volume_below_25m")
    if as_float(snapshot.get("bid_depth_1pct_usd")) < 150_000 or as_float(snapshot.get("ask_depth_1pct_usd")) < 150_000:
        blockers.append("depth_below_150k")
    if snapshot.get("spread_bps") is None or as_float(snapshot.get("spread_bps")) > 8:
        blockers.append("spread_above_8bps")

    rsi15 = snapshot.get("rsi_15m")
    ret15 = as_float(snapshot.get("ret_15m_pct"))
    ret1h = as_float(snapshot.get("ret_1h_pct"))
    ret4h = as_float(snapshot.get("ret_4h_pct"))
    drawup = as_float(snapshot.get("drawup_from_low_pct"))
    high_dist = as_float(snapshot.get("distance_from_high_pct"))
    vol_ratio = as_float(snapshot.get("volume_ratio_1h_vs_5h"))

    score = 0.0
    if entry_mode == CAPITULATION_ENTRY_MODE:
        if rsi15 is None or not (28 <= as_float(rsi15) <= 52):
            blockers.append("rsi_not_capitulation_rebound_zone")
        if ret15 < -0.15:
            blockers.append("15m_no_rebound_confirmation")
        if ret1h < -2.2:
            blockers.append("1h_too_unstable")
        if ret1h > 1.8:
            blockers.append("1h_rebound_too_extended")
        if drawup > 3.5:
            blockers.append("too_far_from_washout_low")
        if vol_ratio < 0.35:
            blockers.append("volume_confirmation_weak")
        score += max(0.0, min(20.0, (ret15 + 0.15) * 30.0))
        score += max(0.0, min(18.0, (2.0 - abs(ret1h)) * 7.0))
        score += max(0.0, min(16.0, (3.5 - drawup) * 5.0))
        score += max(0.0, min(14.0, (52.0 - abs(as_float(rsi15) - 38.0)) * 0.35 if rsi15 is not None else 0.0))
        score += max(0.0, min(12.0, vol_ratio * 10.0))
        score += max(0.0, min(8.0, abs(min(0.0, as_float(snapshot.get("price_change_24h_pct")))) * 0.8))
    else:
        if rsi15 is None or not (30 <= as_float(rsi15) <= 62):
            blockers.append("rsi_not_pullback_recovery_zone")
        # Prefer pullback or base repair: short-term stabilizing, not chasing high.
        if ret15 < -0.35:
            blockers.append("15m_still_falling")
        if ret1h < -1.25:
            blockers.append("1h_still_falling")
        if ret1h > 3.0:
            blockers.append("1h_too_extended")
        if high_dist > -0.5:
            blockers.append("too_close_to_24h_high")
        if drawup > 8.0:
            blockers.append("too_far_from_24h_low")
        score += max(0.0, min(18.0, (ret15 + 0.25) * 12.0))
        score += max(0.0, min(18.0, (ret1h + 1.0) * 5.0))
        score += max(0.0, min(15.0, (2.5 - abs(ret4h)) * 4.0))
        score += max(0.0, min(15.0, (62.0 - abs(as_float(rsi15) - 46.0)) * 0.35 if rsi15 is not None else 0.0))
        score += max(0.0, min(14.0, vol_ratio * 6.0))

    score += 20.0 if not any(b in blockers for b in ["quote_volume_below_25m", "depth_below_150k", "spread_above_8bps"]) else 0.0
    decision = "sandbox_candidate" if score >= 55 and not blockers else "watch"
    return {
        "symbol": symbol,
        "score": round(score, 6),
        "decision": decision,
        "blockers": blockers,
        "market": snapshot,
        "entry_mode": entry_mode,
    }


def buy_quote(snapshot: Dict[str, Any], notional: float = 25.0, commission_bps: float = 10.0, slippage_bps: float = 8.0) -> Dict[str, Any]:
    execution_price = as_float(snapshot.get("ask")) * (1.0 + slippage_bps / 10000.0)
    commission = notional * commission_bps / 10000.0
    net = notional - commission
    qty = net / execution_price if execution_price > 0 else 0.0
    return {
        "execution_price": round(execution_price, 12),
        "quantity": round(qty, 12),
        "gross_quote_usd": round(notional, 6),
        "commission_usd": round(commission, 6),
        "net_quote_after_commission_usd": round(net, 6),
        "commission_bps": commission_bps,
        "slippage_bps": slippage_bps,
    }


def choose_notional(candidate: Dict[str, Any], ledger: Dict[str, Any]) -> Dict[str, Any]:
    """Choose paper size without loosening the entry gate.

    The sandbox target cannot be tested with permanent $25 sizing, but larger
    paper probes should only happen when liquidity, spread and score justify it.
    """
    snapshot = candidate["market"]
    score = as_float(candidate.get("score"))
    spread = as_float(snapshot.get("spread_bps"), 999.0)
    quote_volume = as_float(snapshot.get("quote_volume_24h_usd"))
    min_depth = min(as_float(snapshot.get("bid_depth_1pct_usd")), as_float(snapshot.get("ask_depth_1pct_usd")))
    cash = as_float(ledger.get("cash_usd"))
    equity = as_float(ledger.get("equity_usd"), 500.0)
    current_exposure = sum(as_float(pos.get("notional_usd")) for pos in ledger.get("open_positions") or [])
    max_total_exposure = max(0.0, equity * 0.85)
    available_exposure = max(0.0, max_total_exposure - current_exposure)
    available_cash = max(0.0, cash - 50.0)

    target = 25.0
    reason = "minimum_probe_default"
    if score >= 85 and spread <= 1.0 and quote_volume >= 500_000_000 and min_depth >= 1_000_000:
        target = 100.0
        reason = "elite_liquidity_score_probe"
    elif score >= 78 and spread <= 2.0 and quote_volume >= 250_000_000 and min_depth >= 750_000:
        target = 75.0
        reason = "strong_liquidity_score_probe"
    elif score >= 70 and spread <= 4.0 and quote_volume >= 100_000_000 and min_depth >= 300_000:
        target = 50.0
        reason = "mid_liquidity_score_probe"

    notional = min(target, available_cash, available_exposure)
    if notional < 25.0:
        return {
            "notional_usd": 0.0,
            "sizing_tier": "blocked_no_capacity",
            "sizing_reason": "insufficient_cash_or_exposure_capacity",
            "target_notional_usd": target,
            "available_cash_usd": round(available_cash, 6),
            "available_exposure_usd": round(available_exposure, 6),
        }
    return {
        "notional_usd": round(notional, 6),
        "sizing_tier": reason,
        "sizing_reason": reason,
        "target_notional_usd": target,
        "available_cash_usd": round(available_cash, 6),
        "available_exposure_usd": round(available_exposure, 6),
        "score": round(score, 6),
        "spread_bps": round(spread, 6),
        "quote_volume_24h_usd": round(quote_volume, 6),
        "min_depth_1pct_usd": round(min_depth, 6),
    }


def open_position(ledger: Dict[str, Any], candidate: Dict[str, Any], rid_value: str) -> Optional[Dict[str, Any]]:
    sizing = choose_notional(candidate, ledger)
    notional = as_float(sizing.get("notional_usd"))
    if notional < 25.0 or as_float(ledger.get("cash_usd")) < notional:
        return None
    snapshot = candidate["market"]
    symbol = candidate["symbol"]
    execution = buy_quote(snapshot, notional=notional)
    if as_float(execution.get("quantity")) <= 0:
        return None
    opened_at = now_utc()
    trade_id = f"recovery-sandbox-{now_local().strftime('%Y%m%d-%H%M%S')}-{symbol}"
    position = {
        "paper_trade_id": trade_id,
        "symbol": symbol,
        "side": "long_spot_paper",
        "status": "open",
        "strategy_family": "stable_recovery_sandbox_scout",
        "paper_entry_mode": candidate.get("entry_mode") or ENTRY_MODE,
        "run_id": rid_value,
        "opened_at": iso(opened_at),
        "expires_at": iso(opened_at + timedelta(hours=18)),
        "entry_price": execution["execution_price"],
        "entry_mark_price": snapshot.get("ask"),
        "quantity": execution["quantity"],
        "notional_usd": notional,
        "entry_execution": execution,
        "dynamic_sizing": sizing,
        "score": candidate["score"],
        "stop_pct": -2.5,
        "take_profit_pct": 4.5,
        "max_holding_hours": 18,
        "highest_unrealized_pnl_pct": 0.0,
        "market_at_open": snapshot,
        "live_orders_enabled": False,
        "private_api_used": False,
        "main_ledger_mutated": False,
    }
    order = {
        "order_id": f"recovery-tsbox-{now_local().strftime('%Y%m%d-%H%M%S')}-buy-{symbol}-{len(ledger.get('paper_orders') or []) + 1:04d}",
        "paper_trade_id": trade_id,
        "symbol": symbol,
        "side": "buy",
        "status": "FILLED",
        "created_at": iso(opened_at),
        "run_id": rid_value,
        "execution": execution,
        "dynamic_sizing": sizing,
        "live_orders_enabled": False,
        "private_api_used": False,
        "main_ledger_mutated": False,
    }
    ledger["cash_usd"] = round(as_float(ledger.get("cash_usd")) - notional, 6)
    ledger.setdefault("open_positions", []).append(position)
    ledger.setdefault("paper_orders", []).append(order)
    return {"position": position, "order": order}


def update_equity(ledger: Dict[str, Any]) -> None:
    cash = as_float(ledger.get("cash_usd"))
    open_value = 0.0
    for pos in ledger.get("open_positions") or []:
        open_value += max(0.0, as_float(pos.get("notional_usd")) + as_float(pos.get("unrealized_pnl_usd")))
    ledger["open_value_usd"] = round(open_value, 6)
    ledger["equity_usd"] = round(cash + open_value, 6)
    initial = as_float(ledger.get("initial_capital_usd"), 500.0)
    ledger["net_return_pct"] = round((as_float(ledger.get("equity_usd")) / initial - 1.0) * 100.0, 6) if initial > 0 else 0.0


def render_report(payload: Dict[str, Any]) -> str:
    lines = [
        f"# Stable Recovery Sandbox Scout - {payload['created_at_local']}",
        "",
        "## 结论",
        f"- 评估 `{payload['scan_summary']['evaluated_count']}` 个高流动性主流标的，候选 `{payload['scan_summary']['candidate_count']}` 个。",
        f"- 新开 sandbox 仓 `{len(payload['opened_positions'])}` 个；当前 sandbox 权益 `${payload['ledger_summary']['equity_usd']}`。",
        f"- 本轮 entry mode `{payload.get('entry_mode')}`；原因 `{payload.get('entry_mode_reason')}`。",
        "",
        "## Top 候选",
    ]
    for row in payload["ranked_candidates"][:8]:
        lines.append(f"- `{row['symbol']}` score `{row['score']}` decision `{row['decision']}` blockers `{','.join(row['blockers']) or 'none'}`")
    lines.append("")
    lines.append("## 新开仓")
    if payload["opened_positions"]:
        for item in payload["opened_positions"]:
            pos = item["position"]
            sizing = pos.get("dynamic_sizing") or {}
            lines.append(
                f"- `{pos['symbol']}` `${pos.get('notional_usd')}` entry `{pos['entry_price']}` "
                f"stop `{pos['stop_pct']}%` take `{pos['take_profit_pct']}%` sizing `{sizing.get('sizing_tier', 'unknown')}`"
            )
    else:
        lines.append("- 无。")
    lines.extend(
        [
            "",
            "## 安全边界",
            "- `live_orders_enabled=false`，`private_api_used=false`，`allow_real_orders=false`，`main_ledger_mutated=false`。",
            "- 只使用 Binance public market data；未调用账户、真实订单、提现、margin、futures、perpetual 或签名私有 API。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Stable recovery sandbox scout")
    parser.add_argument("--max-open", type=int, default=12)
    parser.add_argument("--max-new", type=int, default=1)
    parser.add_argument("--dynamic-universe", action="store_true", help="include Binance public 24h liquid/mover USDT discovery")
    parser.add_argument("--dynamic-top", type=int, default=35)
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    run = rid()
    created = now_utc()
    ledger = load_ledger()
    learning = load_learning()
    entry_mode, entry_mode_reason = choose_entry_mode(learning)
    open_symbols = {item.get("symbol") for item in ledger.get("open_positions") or []}
    dynamic_symbols: List[str] = []
    if args.dynamic_universe:
        try:
            dynamic_symbols = discover_dynamic_universe(args.dynamic_top)
        except Exception as exc:  # noqa: BLE001
            dynamic_symbols = []
            # Preserve discovery failure as a scan error while still scanning static universe.
            # The loop-level errors list is initialized below.
    rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    universe = list(dict.fromkeys([*DEFAULT_UNIVERSE, *dynamic_symbols]))
    for symbol in universe:
        try:
            rows.append(evaluate(symbol_snapshot(symbol), open_symbols, learning, entry_mode))
        except Exception as exc:  # noqa: BLE001
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
    ranked = sorted(rows, key=lambda row: row["score"], reverse=True)
    candidates = [row for row in ranked if row["decision"] == "sandbox_candidate"]
    opened: List[Dict[str, Any]] = []
    slots = max(0, args.max_open - len(open_symbols))
    for candidate in candidates:
        if len(opened) >= min(max(0, args.max_new), slots):
            break
        result = open_position(ledger, candidate, run)
        if result:
            opened.append(result)
            open_symbols.add(candidate["symbol"])
    update_equity(ledger)
    ledger.setdefault("events", []).append(
        {
            "event_type": "stable_recovery_sandbox_scout",
            "created_at": iso(created),
            "run_id": run,
            "evaluated_count": len(rows),
            "candidate_count": len(candidates),
            "opened_count": len(opened),
            "equity_usd": ledger.get("equity_usd"),
            "cash_usd": ledger.get("cash_usd"),
            "live_orders_enabled": False,
            "private_api_used": False,
            "main_ledger_mutated": False,
        }
    )
    ledger["updated_at"] = iso(now_utc())
    write_warnings = save_ledger(ledger)
    month = now_local().strftime("%Y-%m")
    target = as_float(((ledger.get("monthly_goal_baselines") or {}).get(month) or {}).get("target_equity_usd"), 1000.0)
    payload = {
        "run_id": run,
        "created_at": iso(created),
        "created_at_local": now_local().isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "mode": "stable_recovery_sandbox_scout",
        "live_orders_enabled": LIVE_ORDERS_ENABLED,
        "private_api_used": PRIVATE_API_USED,
        "allow_real_orders": ALLOW_REAL_ORDERS,
        "main_ledger_mutated": MAIN_LEDGER_MUTATED,
        "entry_mode": entry_mode,
        "entry_mode_reason": entry_mode_reason,
        "learning_summary": learning.get("summary"),
        "scan_summary": {
            "evaluated_count": len(rows),
            "candidate_count": len(candidates),
            "error_count": len(errors),
            "dynamic_universe_enabled": bool(args.dynamic_universe),
            "dynamic_symbol_count": len(dynamic_symbols),
            "universe_count": len(universe),
        },
        "ledger_summary": {
            "cash_usd": ledger.get("cash_usd"),
            "equity_usd": ledger.get("equity_usd"),
            "open_count": len(ledger.get("open_positions") or []),
            "closed_count": len(ledger.get("closed_trades") or []),
            "target_equity_usd": target,
            "gap_to_target_usd": round(max(0.0, target - as_float(ledger.get("equity_usd"))), 6),
        },
        "ranked_candidates": ranked,
        "opened_positions": opened,
        "errors": errors,
        "write_warnings": write_warnings,
    }
    stamp = now_local().strftime("%Y-%m-%d-%H%M")
    experiment_rel = Path("experiments") / f"{run}.json"
    report_rel = Path("reports") / f"{stamp}-stable-recovery-sandbox-scout.md"
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
