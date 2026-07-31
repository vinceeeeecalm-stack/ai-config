#!/usr/bin/env python3
"""Build an actionable paper-only watchlist from strategy recovery candidates.

Read-only. This script fetches public Binance spot market data for the latest
strategy-recovery queue and turns research-watch candidates into conditional
paper-scout plans with explicit trigger, stop, take-profit and failure rules.
It never mutates the paper ledger and never uses private APIs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import statistics
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
REPORTS_DIR = ROOT / "reports"
EXPERIMENTS_DIR = ROOT / "experiments"
BINANCE_BASE_URLS = [
    "https://data-api.binance.vision/api/v3",
    "https://api.binance.com/api/v3",
    "https://api1.binance.com/api/v3",
    "https://api2.binance.com/api/v3",
    "https://api3.binance.com/api/v3",
]
LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))
MARKET_CONTEXT_FIELDS = (
    "market_regime",
    "market_atmosphere",
    "short_term_state",
    "sentiment_state",
)


class BinancePublicFetchError(RuntimeError):
    def __init__(self, path: str, failures: list[dict[str, Any]]):
        self.path = path
        self.failures = failures
        first = failures[0] if failures else {}
        self.primary_error_type = first.get("error_type") or "unknown"
        super().__init__(f"{path} failed on all public Binance hosts: {self.primary_error_type}")


def now_local() -> dt.datetime:
    return dt.datetime.now(LOCAL_TZ).replace(microsecond=0)


def rel(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def read_json(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def latest(pattern: str, directory: Path | None = None) -> Path | None:
    directory = directory or EXPERIMENTS_DIR
    files = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    return files[0] if files else None


def recent_files(pattern: str, directory: Path | None = None, limit: int = 80) -> list[Path]:
    directory = directory or EXPERIMENTS_DIR
    return sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]


def unknown_market_context(source: str = "unknown_or_not_attached") -> dict[str, Any]:
    return {
        "market_regime": "unknown_or_not_attached",
        "market_atmosphere": "unknown_or_not_attached",
        "short_term_state": "unknown_or_not_attached",
        "sentiment_state": "unknown_or_not_attached",
        "pool_width_policy": None,
        "pool_shape_policy": None,
        "dynamic_scan_status": "unknown_or_not_attached",
        "dynamic_scan_reason": None,
        "ticker_meta_status": None,
        "social_handoff_status": None,
        "social_handoff_age_hours": None,
        "data_layer_degraded": True,
        "selected_symbol_count": 0,
        "selected_symbols_sample": [],
        "source": source,
    }


def market_context_from_runner(payload: dict[str, Any] | None, source: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return unknown_market_context(source)
    dynamic = payload.get("dynamic_scan_universe") or payload.get("dynamic_scan_pool") or {}
    if not isinstance(dynamic, dict):
        dynamic = {}
    selected = dynamic.get("selected_symbols") or []
    if not isinstance(selected, list):
        selected = []
    social = dynamic.get("social_handoff_state") if isinstance(dynamic.get("social_handoff_state"), dict) else {}
    ticker_meta = dynamic.get("ticker_meta") if isinstance(dynamic.get("ticker_meta"), dict) else {}
    dynamic_status = dynamic.get("status") or "unknown_or_not_attached"
    context = unknown_market_context(source)
    context.update(
        {
            "market_regime": dynamic.get("market_regime") or dynamic.get("regime") or context["market_regime"],
            "market_atmosphere": dynamic.get("market_atmosphere") or context["market_atmosphere"],
            "short_term_state": dynamic.get("short_term_state") or context["short_term_state"],
            "sentiment_state": dynamic.get("sentiment_state") or context["sentiment_state"],
            "pool_width_policy": dynamic.get("pool_width_policy"),
            "pool_shape_policy": dynamic.get("pool_shape_policy"),
            "dynamic_scan_status": dynamic_status,
            "dynamic_scan_reason": dynamic.get("reason") or dynamic.get("why_pool_changed_from_static_baseline"),
            "ticker_meta_status": ticker_meta.get("status"),
            "social_handoff_status": social.get("status"),
            "social_handoff_age_hours": social.get("age_hours"),
            "data_layer_degraded": dynamic_status in {"fallback_static", "fallback_static_survival", "degraded", "failed"},
            "selected_symbol_count": dynamic.get("selected_symbol_count") or len(selected),
            "selected_symbols_sample": selected[:12],
        }
    )
    return context


def latest_dynamic_market_context() -> tuple[dict[str, Any], Path | None]:
    fallback_path: Path | None = None
    fallback_context: dict[str, Any] | None = None
    for path in recent_files("*validation-progress-runner.json"):
        payload = read_json(path)
        context = market_context_from_runner(payload, rel(path))
        if fallback_path is None:
            fallback_path = path
            fallback_context = context
        no_entry = payload.get("no_entry_summary") if isinstance(payload, dict) else {}
        blocked = no_entry.get("top_blocked_candidates") if isinstance(no_entry, dict) else []
        if (
            context.get("dynamic_scan_status") == "ok"
            and context.get("market_regime") != "unknown_or_not_attached"
            and context.get("market_atmosphere") != "unknown_or_not_attached"
            and context.get("short_term_state") != "unknown_or_not_attached"
            and context.get("sentiment_state") != "unknown_or_not_attached"
            and isinstance(blocked, list)
            and blocked
        ):
            context["source_selection_status"] = "candidate_bearing_dynamic_runner_selected"
            return context, path
    if fallback_context is not None:
        fallback_context["source_selection_status"] = "fallback_latest_runner_no_candidate_dynamic_runner_found"
        return fallback_context, fallback_path
    return unknown_market_context("missing_validation_progress_runner"), None


def candidate_market_context(candidate: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    source_context = candidate.get("market_context") if isinstance(candidate.get("market_context"), dict) else {}
    context = {**fallback, **source_context}
    for key in MARKET_CONTEXT_FIELDS:
        context[key] = (
            source_context.get(key)
            or candidate.get(key)
            or fallback.get(key)
            or "unknown_or_not_attached"
        )
    context["source"] = (
        source_context.get("source")
        or candidate.get("market_context_source")
        or fallback.get("source")
        or "recovery_watchlist_fallback_or_unknown"
    )
    return context


def fetch_json(
    path: str,
    params: dict[str, Any] | None = None,
    timeout: float = 8.0,
    base_urls: list[str] | None = None,
    curl_fallback: bool = True,
) -> Any:
    query = urllib.parse.urlencode(params or {})
    failures: list[dict[str, Any]] = []
    for base_url in base_urls or BINANCE_BASE_URLS:
        url = f"{base_url}{path}" + (f"?{query}" if query else "")
        req = urllib.request.Request(url, headers={"User-Agent": "active-alpha-paper-monitor/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            failures.append(
                {
                    "base_url": base_url,
                    "path": path,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:240],
                }
            )
        if curl_fallback:
            try:
                result = subprocess.run(
                    ["/usr/bin/curl", "-sS", "--max-time", str(max(1.0, timeout)), url],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return json.loads(result.stdout)
            except Exception as exc:
                failures.append(
                    {
                        "base_url": base_url,
                        "path": path,
                        "transport": "curl_fallback",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:240],
                    }
                )
    raise BinancePublicFetchError(path, failures)


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def queue_from_optimizer(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("eligible_retest_queue", "top_retest_queue", "retest_queue"):
        queue = payload.get(key)
        if isinstance(queue, list):
            return [item for item in queue if isinstance(item, dict)]
    summary = payload.get("strategy_recovery_optimizer")
    if isinstance(summary, dict):
        queue = summary.get("top_retest_queue") or summary.get("eligible_retest_queue")
        if isinstance(queue, list):
            return [item for item in queue if isinstance(item, dict)]
    return []


def kline_rows(symbol: str, interval: str, limit: int) -> list[dict[str, float]]:
    rows = fetch_json("/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    parsed = []
    for row in rows or []:
        parsed.append(
            {
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "quote_volume": float(row[7]),
                "trade_count": float(row[8]),
            }
        )
    return parsed


def depth_summary(symbol: str) -> dict[str, Any]:
    depth = fetch_json("/depth", {"symbol": symbol, "limit": 20})
    bids = depth.get("bids") or []
    asks = depth.get("asks") or []
    best_bid = as_float(bids[0][0]) if bids else None
    best_ask = as_float(asks[0][0]) if asks else None
    mid = ((best_bid or 0.0) + (best_ask or 0.0)) / 2.0 if best_bid and best_ask else None
    spread_bps = ((best_ask - best_bid) / mid * 10000.0) if best_bid and best_ask and mid else None
    bid_depth_usd = sum((as_float(price, 0.0) or 0.0) * (as_float(qty, 0.0) or 0.0) for price, qty in bids)
    ask_depth_usd = sum((as_float(price, 0.0) or 0.0) * (as_float(qty, 0.0) or 0.0) for price, qty in asks)
    imbalance = ((bid_depth_usd - ask_depth_usd) / (bid_depth_usd + ask_depth_usd)) if (bid_depth_usd + ask_depth_usd) else None
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": round(spread_bps, 4) if spread_bps is not None else None,
        "bid_depth_usd_20": round(bid_depth_usd, 2),
        "ask_depth_usd_20": round(ask_depth_usd, 2),
        "order_book_imbalance_20": round(imbalance, 6) if imbalance is not None else None,
        "book_update_id": depth.get("lastUpdateId"),
    }


def recent_trade_summary(symbol: str, limit: int = 120) -> dict[str, Any]:
    trades = fetch_json("/aggTrades", {"symbol": symbol, "limit": limit})
    buy_quote = 0.0
    sell_quote = 0.0
    total_quote = 0.0
    total_qty = 0.0
    for trade in trades or []:
        price = as_float(trade.get("p"), 0.0) or 0.0
        qty = as_float(trade.get("q"), 0.0) or 0.0
        quote = price * qty
        total_quote += quote
        total_qty += qty
        # Binance aggTrades `m=true` means buyer is maker, so taker was seller.
        if trade.get("m") is True:
            sell_quote += quote
        else:
            buy_quote += quote
    buy_ratio = (buy_quote / total_quote) if total_quote else None
    return {
        "recent_trade_count": len(trades or []),
        "recent_trade_quote_volume_usd": round(total_quote, 2),
        "recent_trade_base_volume": round(total_qty, 8),
        "recent_taker_buy_quote_ratio": round(buy_ratio, 6) if buy_ratio is not None else None,
        "recent_taker_buy_quote_usd": round(buy_quote, 2),
        "recent_taker_sell_quote_usd": round(sell_quote, 2),
    }


def realized_volatility_pct(closes: list[float]) -> float | None:
    if len(closes) < 3:
        return None
    returns = []
    for prev, cur in zip(closes, closes[1:]):
        if prev:
            returns.append((cur / prev) - 1.0)
    if len(returns) < 2:
        return None
    return statistics.pstdev(returns) * math.sqrt(len(returns)) * 100.0


def max_drawdown_pct(closes: list[float]) -> float | None:
    if not closes:
        return None
    peak = closes[0]
    max_dd = 0.0
    for close in closes:
        peak = max(peak, close)
        if peak:
            max_dd = min(max_dd, (close / peak - 1.0) * 100.0)
    return max_dd


def interval_limit(interval: str) -> int:
    if interval.endswith("m"):
        return 96
    if interval.endswith("h"):
        return 72
    return 45


def build_plan(candidate: dict[str, Any], fallback_market_context: dict[str, Any] | None = None) -> dict[str, Any]:
    symbol = str(candidate.get("symbol") or "").upper()
    interval = str(candidate.get("interval") or "4h")
    market_context = candidate_market_context(candidate, fallback_market_context or unknown_market_context())
    item: dict[str, Any] = {
        "symbol": symbol,
        "interval": interval,
        "strategy_family": candidate.get("strategy_family"),
        "paper_entry_mode": candidate.get("paper_entry_mode"),
        "stage": candidate.get("stage") or "research_watch",
        "current_signal": bool(candidate.get("current_signal")),
        "source_score": candidate.get("score"),
        "recommended_max_action": "watch",
        "market_regime": market_context["market_regime"],
        "market_atmosphere": market_context["market_atmosphere"],
        "short_term_state": market_context["short_term_state"],
        "sentiment_state": market_context["sentiment_state"],
        "market_context_source": market_context.get("source"),
        "market_context": market_context,
        "dynamic_scan_status": market_context.get("dynamic_scan_status"),
        "dynamic_scan_reason": market_context.get("dynamic_scan_reason"),
        "data_layer_degraded": market_context.get("data_layer_degraded"),
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
    }
    if not symbol:
        item["data_quality_status"] = "missing_symbol"
        item["block_reason"] = "missing_symbol"
        return item
    try:
        ticker = fetch_json("/ticker/24hr", {"symbol": symbol})
        depth = depth_summary(symbol)
        rows = kline_rows(symbol, interval, interval_limit(interval))
        trades = recent_trade_summary(symbol)
    except BinancePublicFetchError as exc:
        item["data_quality_status"] = "missing"
        item["block_reason"] = f"binance_public_fetch_failed:{exc.path}:{exc.primary_error_type}"
        item["binance_public_fetch_failures"] = exc.failures
        item["binance_public_host_attempt_count"] = len(exc.failures)
        return item
    except Exception as exc:
        item["data_quality_status"] = "missing"
        item["block_reason"] = f"binance_public_fetch_failed:unexpected:{type(exc).__name__}"
        return item

    closes = [row["close"] for row in rows]
    highs = [row["high"] for row in rows[:-1] or rows]
    lows = [row["low"] for row in rows[:-1] or rows]
    quote_volumes = [row["quote_volume"] for row in rows]
    last = rows[-1] if rows else {}
    current_price = as_float(ticker.get("lastPrice"), last.get("close"))
    quote_volume_24h = as_float(ticker.get("quoteVolume"), 0.0) or 0.0
    change_24h_pct = as_float(ticker.get("priceChangePercent"), 0.0) or 0.0
    recent_high = max(highs[-20:]) if highs else current_price
    recent_low = min(lows[-20:]) if lows else current_price
    median_quote_volume = statistics.median(quote_volumes[:-1] or quote_volumes or [1.0])
    volume_ratio = (last.get("quote_volume", 0.0) / median_quote_volume) if median_quote_volume else None
    spread_bps = as_float(depth.get("spread_bps"))
    min_book_depth = min(depth.get("bid_depth_usd_20") or 0.0, depth.get("ask_depth_usd_20") or 0.0)
    order_book_imbalance = as_float(depth.get("order_book_imbalance_20"))
    taker_buy_ratio = as_float(trades.get("recent_taker_buy_quote_ratio"))
    volatility_pct = realized_volatility_pct(closes[-24:])
    drawdown_pct = max_drawdown_pct(closes[-24:])

    entry_trigger = (recent_high or current_price or 0.0) * 1.002
    pullback_trigger = (current_price or 0.0) * 0.992
    stop_loss = max((recent_low or 0.0) * 0.995, (entry_trigger or 0.0) * 0.94)
    take_profit_1 = entry_trigger * 1.06
    take_profit_2 = entry_trigger * 1.12
    liquidity_ok = quote_volume_24h >= 2_000_000 and (spread_bps is not None and spread_bps <= 12.0) and min_book_depth >= 15_000
    breakout_ready = current_price is not None and recent_high is not None and current_price >= recent_high * 0.995
    volume_ready = volume_ratio is not None and volume_ratio >= 1.35
    microstructure_ok = (
        (taker_buy_ratio is None or taker_buy_ratio >= 0.48)
        and (order_book_imbalance is None or order_book_imbalance >= -0.2)
    )
    current_signal = bool(candidate.get("current_signal"))

    if current_signal and liquidity_ok and microstructure_ok and (breakout_ready or volume_ready):
        recommended = "paper_scout_allowed_if_trigger_confirms"
        confidence = 62 + (3 if (taker_buy_ratio or 0) >= 0.56 else 0)
    elif liquidity_ok and microstructure_ok:
        recommended = "conditional_watch_until_breakout"
        confidence = 48 if not current_signal else 55
    elif liquidity_ok and not microstructure_ok:
        recommended = "watch_microstructure_blocked"
        confidence = 38
    else:
        recommended = "watch_liquidity_blocked"
        confidence = 35

    item.update(
        {
            "data_quality_status": "verified" if liquidity_ok else "verified_but_blocked",
            "recommended_max_action": recommended,
            "direction": "long_spot_paper_only_after_trigger",
            "current_price": round(current_price, 8) if current_price is not None else None,
            "change_24h_pct": round(change_24h_pct, 4),
            "quote_volume_24h_usd": round(quote_volume_24h, 2),
            "spread_bps": spread_bps,
            "book_depth_min_usd_20": round(min_book_depth, 2),
            "order_book_imbalance_20": round(order_book_imbalance, 6) if order_book_imbalance is not None else None,
            "recent_trade_count": trades.get("recent_trade_count"),
            "recent_trade_quote_volume_usd": trades.get("recent_trade_quote_volume_usd"),
            "recent_taker_buy_quote_ratio": taker_buy_ratio,
            "realized_volatility_pct": round(volatility_pct, 4) if volatility_pct is not None else None,
            "max_drawdown_pct": round(drawdown_pct, 4) if drawdown_pct is not None else None,
            "volume_ratio_vs_recent_median": round(volume_ratio, 4) if volume_ratio is not None else None,
            "recent_high": round(recent_high, 8) if recent_high is not None else None,
            "recent_low": round(recent_low, 8) if recent_low is not None else None,
            "entry_zone": {
                "breakout_confirm_above": round(entry_trigger, 8),
                "or_pullback_reclaim_above": round(pullback_trigger, 8),
                "requires": [
                    "current_signal=true or fresh breakout confirmation",
                    "volume_ratio_vs_recent_median >= 1.35",
                    "spread_bps <= 12",
                    "book_depth_min_usd_20 >= 15000",
                    "recent_taker_buy_quote_ratio >= 0.48",
                    "order_book_imbalance_20 >= -0.20",
                ],
            },
            "stop_loss": round(stop_loss, 8),
            "take_profit": [round(take_profit_1, 8), round(take_profit_2, 8)],
            "time_window": "next 1-3 candles on strategy interval; max paper holding 72h unless exit rule fires",
            "forecast_probability_pct": confidence,
            "failure_conditions": [
                "price rejects breakout level for 2 candles",
                "volume_ratio_vs_recent_median drops below 1.0",
                "spread_bps rises above 18 or order book depth thins below 10000 USD",
                "recent_taker_buy_quote_ratio falls below 0.45",
                "order_book_imbalance_20 falls below -0.30",
                "BTC/ETH/SOL/BNB anchors flip risk-off before entry",
            ],
            "block_reason": (
                None
                if recommended == "paper_scout_allowed_if_trigger_confirms"
                else (
                    "microstructure_gate_failed"
                    if recommended == "watch_microstructure_blocked"
                    else "liquidity_or_spread_depth_gate_failed"
                    if recommended == "watch_liquidity_blocked"
                    else "awaiting_current_signal_or_breakout"
                )
            ),
        }
    )
    return item


def build_record(max_candidates: int) -> dict[str, Any]:
    created = now_local()
    source_path = latest("*strategy-recovery-optimizer.json")
    source = read_json(source_path) or {}
    market_context, market_context_path = latest_dynamic_market_context()
    queue = queue_from_optimizer(source)[:max_candidates]
    plans = [build_plan(candidate, market_context) for candidate in queue]
    actionable = [
        item for item in plans
        if item.get("recommended_max_action") == "paper_scout_allowed_if_trigger_confirms"
    ]
    return {
        "run_id": f"{created.strftime('%Y%m%d-%H%M%S')}-recovery-watchlist-monitor",
        "created_at": created.isoformat(),
        "scope": "paper_only_recovery_watchlist",
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
        "source_strategy_recovery_optimizer": rel(source_path),
        "source_dynamic_market_context": rel(market_context_path),
        "dynamic_market_context": market_context,
        "queue_count": len(queue),
        "watchlist": plans,
        "actionable_paper_scout_count": len(actionable),
        "operator_note": "These are conditional paper-only trigger plans. They do not open paper positions or authorize live trading.",
    }


def render_markdown(record: dict[str, Any]) -> str:
    lines = [
        f"# Recovery Watchlist Monitor | {record['run_id']}",
        "",
        "- scope: `paper_only_recovery_watchlist`",
        "- live_orders_enabled: `false`",
        "- private_api_used: `false`",
        "- ledger_mutated: `false`",
        f"- source_strategy_recovery_optimizer: `{record.get('source_strategy_recovery_optimizer') or '-'}`",
        "",
        "## Summary",
        "",
        f"- queue_count: `{record.get('queue_count')}`",
        f"- actionable_paper_scout_count: `{record.get('actionable_paper_scout_count')}`",
        "",
        "## Watchlist",
        "",
        "| Symbol | Action | Price | 24h % | Spread | Depth | Buy ratio | Imbalance | Vol | Max DD | Trigger | Stop | Probability |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in record.get("watchlist") or []:
        entry = item.get("entry_zone") or {}
        take = item.get("take_profit") or []
        lines.append(
            f"| `{item.get('symbol')}` | `{item.get('recommended_max_action')}` | "
            f"`{item.get('current_price')}` | `{item.get('change_24h_pct')}` | "
            f"`{item.get('spread_bps')}` | `{item.get('book_depth_min_usd_20')}` | "
            f"`{item.get('recent_taker_buy_quote_ratio')}` | `{item.get('order_book_imbalance_20')}` | "
            f"`{item.get('realized_volatility_pct')}` | `{item.get('max_drawdown_pct')}` | "
            f"`{entry.get('breakout_confirm_above')}` | `{item.get('stop_loss')}` | "
            f"`{item.get('forecast_probability_pct')}` |"
        )
    if not record.get("watchlist"):
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | - |")
    lines.extend(["", "## Trigger Rules", ""])
    for item in record.get("watchlist") or []:
        entry = item.get("entry_zone") or {}
        lines.append(f"### {item.get('symbol')}")
        lines.append(f"- direction: `{item.get('direction')}`")
        lines.append(f"- time_window: `{item.get('time_window')}`")
        lines.append("- entry_requires:")
        for rule in entry.get("requires") or []:
            lines.append(f"  - {rule}")
        lines.append("- failure_conditions:")
        for rule in item.get("failure_conditions") or []:
            lines.append(f"  - {rule}")
        lines.append("")
    return "\n".join(lines)


def run_self_test() -> dict[str, Any]:
    payload = {
        "dynamic_scan_universe": {
            "status": "fallback_static",
            "reason": "binance_all_ticker_unavailable",
            "ticker_meta": {"status": "failed"},
            "social_handoff_state": {"status": "fresh", "age_hours": 2.5},
            "selected_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        }
    }
    context = market_context_from_runner(payload, "self-test-runner.json")
    assert context["market_regime"] == "unknown_or_not_attached", context
    assert context["dynamic_scan_status"] == "fallback_static", context
    assert context["dynamic_scan_reason"] == "binance_all_ticker_unavailable", context
    assert context["ticker_meta_status"] == "failed", context
    assert context["social_handoff_status"] == "fresh", context
    assert context["data_layer_degraded"] is True, context
    candidate = {
        "symbol": "TESTUSDT",
        "interval": "4h",
        "market_context": {"market_regime": "risk_on_momentum", "source": "candidate_context"},
    }
    merged = candidate_market_context(candidate, context)
    assert merged["market_regime"] == "risk_on_momentum", merged
    assert merged["dynamic_scan_status"] == "fallback_static", merged
    assert merged["source"] == "candidate_context", merged

    with tempfile.TemporaryDirectory(prefix="recovery-watchlist-monitor-") as tmp:
        fixture = Path(tmp) / "runner.json"
        fixture.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        loaded = read_json(fixture)
        assert market_context_from_runner(loaded, str(fixture))["selected_symbol_count"] == 3

        candidate_runner = Path(tmp) / "20260627-100000-validation-progress-runner.json"
        maintenance_runner = Path(tmp) / "20260627-110000-validation-progress-runner.json"
        candidate_runner.write_text(
            json.dumps(
                {
                    "dynamic_scan_universe": {
                        "status": "ok",
                        "market_regime": "risk_on_momentum",
                        "market_atmosphere": "broad_risk_appetite",
                        "short_term_state": "neutral",
                        "sentiment_state": "positive_catalyst_cluster",
                        "selected_symbols": ["BTCUSDT", "ZECUSDT"],
                    },
                    "no_entry_summary": {"top_blocked_candidates": [{"symbol": "ZECUSDT"}]},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        maintenance_runner.write_text(
            json.dumps(
                {
                    "dynamic_scan_universe": {"status": "disabled", "selected_symbols": []},
                    "no_entry_summary": {"top_blocked_candidates": []},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.utime(candidate_runner, (1000, 1000))
        os.utime(maintenance_runner, (2000, 2000))
        original_experiment_dir = globals()["EXPERIMENTS_DIR"]
        try:
            globals()["EXPERIMENTS_DIR"] = Path(tmp)
            selected_context, selected_path = latest_dynamic_market_context()
            assert selected_path == candidate_runner, (selected_context, selected_path)
            assert selected_context["source_selection_status"] == "candidate_bearing_dynamic_runner_selected"
            assert selected_context["dynamic_scan_status"] == "ok"
        finally:
            globals()["EXPERIMENTS_DIR"] = original_experiment_dir

    original_urlopen = urllib.request.urlopen
    original_subprocess_run = subprocess.run

    class FakeResponse:
        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def read(self) -> bytes:
            return self.body

    def fallback_urlopen(req: Any, timeout: float = 0.0) -> FakeResponse:
        url = getattr(req, "full_url", str(req))
        if "bad-host" in url:
            raise TimeoutError("self-test timeout")
        return FakeResponse(b'{"ok": true}')

    urllib.request.urlopen = fallback_urlopen
    try:
        assert fetch_json("/ticker/24hr", {"symbol": "TESTUSDT"}, base_urls=["https://bad-host/api/v3", "https://good-host/api/v3"]) == {"ok": True}
    finally:
        urllib.request.urlopen = original_urlopen

    def failing_urlopen(req: Any, timeout: float = 0.0) -> FakeResponse:
        raise TimeoutError("self-test all hosts failed")

    urllib.request.urlopen = failing_urlopen
    try:
        try:
            fetch_json(
                "/depth",
                {"symbol": "TESTUSDT"},
                base_urls=["https://bad-a/api/v3", "https://bad-b/api/v3"],
                curl_fallback=False,
            )
        except BinancePublicFetchError as exc:
            assert exc.path == "/depth", exc
            assert exc.primary_error_type == "TimeoutError", exc
            assert len(exc.failures) == 2, exc.failures
        else:
            raise AssertionError("expected BinancePublicFetchError")
    finally:
        urllib.request.urlopen = original_urlopen

    def dns_fail_urlopen(req: Any, timeout: float = 0.0) -> FakeResponse:
        raise OSError("self-test urllib dns failed")

    class FakeCompletedProcess:
        def __init__(self) -> None:
            self.stdout = '{"ok": true, "transport": "curl"}'

    def fake_curl_run(cmd: list[str], check: bool, capture_output: bool, text: bool) -> FakeCompletedProcess:
        assert "/usr/bin/curl" in cmd[0], cmd
        assert any("data-api.binance.vision" in item for item in cmd), cmd
        return FakeCompletedProcess()

    urllib.request.urlopen = dns_fail_urlopen
    subprocess.run = fake_curl_run
    try:
        curl_payload = fetch_json("/ticker/24hr", {"symbol": "TESTUSDT"}, base_urls=["https://data-api.binance.vision/api/v3"])
        assert curl_payload == {"ok": True, "transport": "curl"}, curl_payload
    finally:
        urllib.request.urlopen = original_urlopen
        subprocess.run = original_subprocess_run

    return {
        "status": "ok",
        "cases": [
            "fallback_static_context_preserves_degradation_reason",
            "candidate_context_overrides_regime_but_keeps_runner_degradation",
            "latest_dynamic_market_context_prefers_candidate_bearing_runner",
            "binance_public_fetch_falls_back_to_second_host",
            "binance_public_fetch_uses_curl_when_urllib_dns_fails",
            "binance_public_fetch_error_records_all_hosts",
        ],
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a paper-only recovery watchlist from strategy recovery candidates.")
    parser.add_argument("--max-candidates", type=int, default=8)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(run_self_test(), ensure_ascii=False, indent=2))
        return 0
    record = build_record(max(1, args.max_candidates))
    stamp = record["run_id"].removesuffix("-recovery-watchlist-monitor")
    report_path = REPORTS_DIR / f"{now_local().strftime('%Y-%m-%d')}-recovery-watchlist-{stamp}.md"
    experiment_path = EXPERIMENTS_DIR / f"{stamp}-recovery-watchlist-monitor.json"
    record["outputs"] = {"report": rel(report_path), "experiment": rel(experiment_path)}
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    experiment_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_markdown(record), encoding="utf-8")
    if args.compact_output:
        print(json.dumps({
            "status": "ok",
            "run_id": record["run_id"],
            "queue_count": record["queue_count"],
            "actionable_paper_scout_count": record["actionable_paper_scout_count"],
            "watch_symbols": [item.get("symbol") for item in record.get("watchlist") or []],
            "live_orders_enabled": False,
            "private_api_used": False,
            "outputs": record["outputs"],
        }, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
