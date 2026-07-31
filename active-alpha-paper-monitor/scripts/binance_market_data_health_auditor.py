#!/usr/bin/env python3
"""Audit public Binance spot market-data health for the paper trading loop.

This script is read-only:
- public spot market-data endpoints only
- no account endpoints
- no orders, withdrawals, margin, futures or perpetuals
- no API keys in output
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
REPORTS_DIR = ACTIVE_ROOT / "reports"
EXPERIMENTS_DIR = ACTIVE_ROOT / "experiments"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
BINANCE_BASE = "https://api.binance.com/api/v3"
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
DEFAULT_INTERVALS = ["1m", "5m", "15m", "1h", "4h"]
BINANCE_BASE_URLS = [
    "https://api.binance.com/api/v3",
    "https://data-api.binance.vision/api/v3",
    "https://api1.binance.com/api/v3",
    "https://api2.binance.com/api/v3",
    "https://api3.binance.com/api/v3",
]


def now_local() -> dt.datetime:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0)


def rel(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def pct_change(first: float | None, last: float | None) -> float | None:
    if first in (None, 0) or last is None:
        return None
    return (last / first - 1.0) * 100.0


def round_or_none(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


class BinancePublicFetchError(RuntimeError):
    def __init__(self, path: str, failures: list[dict[str, Any]]):
        self.path = path
        self.failures = failures
        first = failures[0] if failures else {}
        self.primary_error_type = first.get("error_type") or "unknown"
        super().__init__(f"{path} failed on all Binance public hosts: {self.primary_error_type}")


def normalize_base_urls(base_url: str | list[str]) -> list[str]:
    values = base_url if isinstance(base_url, list) else str(base_url).split(",")
    cleaned = [item.strip().rstrip("/") for item in values if str(item).strip()]
    return cleaned or BINANCE_BASE_URLS


def fetch_json(base_url: str, path: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> Any:
    query = urllib.parse.urlencode(params or {})
    url = f"{base_url}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers={"User-Agent": "active-alpha-paper-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_json_with_fallback(base_urls: list[str], path: str, params: dict[str, Any] | None = None, timeout: float = 10.0) -> tuple[Any, dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for base_url in base_urls:
        try:
            payload = fetch_json(base_url, path, params, timeout)
            return payload, {
                "base_url": base_url,
                "attempt_count": len(failures) + 1,
                "fallback_failures": failures,
            }
        except Exception as exc:
            failures.append(
                {
                    "base_url": base_url,
                    "path": path,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:240],
                }
            )
    raise BinancePublicFetchError(path, failures)


def parse_klines(rows: Any) -> list[dict[str, float]]:
    parsed: list[dict[str, float]] = []
    for row in rows or []:
        try:
            parsed.append(
                {
                    "open_time": float(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                    "quote_volume": float(row[7]),
                    "trade_count": float(row[8]),
                }
            )
        except (TypeError, ValueError, IndexError):
            continue
    return parsed


def summarize_klines(rows: list[dict[str, float]]) -> dict[str, Any]:
    if not rows:
        return {"status": "missing", "bar_count": 0}
    closes = [row["close"] for row in rows if row.get("close")]
    quote_volumes = [row.get("quote_volume", 0.0) for row in rows]
    returns = []
    for previous, current in zip(closes, closes[1:]):
        if previous:
            returns.append((current / previous - 1.0) * 100.0)
    max_drawdown = 0.0
    peak = closes[0] if closes else 0.0
    for close in closes:
        peak = max(peak, close)
        if peak:
            max_drawdown = min(max_drawdown, (close / peak - 1.0) * 100.0)
    return {
        "status": "ok",
        "bar_count": len(rows),
        "first_close": round_or_none(closes[0] if closes else None),
        "last_close": round_or_none(closes[-1] if closes else None),
        "change_pct": round_or_none(pct_change(closes[0] if closes else None, closes[-1] if closes else None), 4),
        "quote_volume_sum": round_or_none(sum(quote_volumes), 2),
        "realized_volatility_pct": round_or_none(statistics.pstdev(returns), 4) if len(returns) >= 2 else None,
        "max_drawdown_pct": round_or_none(max_drawdown, 4),
    }


def depth_within(mid: float, levels: list[list[str]], pct_width: float, side: str) -> float:
    if not mid:
        return 0.0
    total = 0.0
    if side == "bid":
        floor = mid * (1.0 - pct_width / 100.0)
        for price_text, qty_text in levels:
            price = as_float(price_text, 0.0) or 0.0
            qty = as_float(qty_text, 0.0) or 0.0
            if price < floor:
                break
            total += price * qty
    else:
        ceiling = mid * (1.0 + pct_width / 100.0)
        for price_text, qty_text in levels:
            price = as_float(price_text, 0.0) or 0.0
            qty = as_float(qty_text, 0.0) or 0.0
            if price > ceiling:
                break
            total += price * qty
    return total


def summarize_depth(depth: dict[str, Any], book: dict[str, Any] | None = None) -> dict[str, Any]:
    bids = depth.get("bids") if isinstance(depth.get("bids"), list) else []
    asks = depth.get("asks") if isinstance(depth.get("asks"), list) else []
    best_bid = as_float((book or {}).get("bidPrice")) if book else None
    best_ask = as_float((book or {}).get("askPrice")) if book else None
    if best_bid is None and bids:
        best_bid = as_float(bids[0][0])
    if best_ask is None and asks:
        best_ask = as_float(asks[0][0])
    mid = ((best_bid or 0.0) + (best_ask or 0.0)) / 2.0 if best_bid and best_ask else None
    bid_depth_20 = sum((as_float(price, 0.0) or 0.0) * (as_float(qty, 0.0) or 0.0) for price, qty in bids[:20])
    ask_depth_20 = sum((as_float(price, 0.0) or 0.0) * (as_float(qty, 0.0) or 0.0) for price, qty in asks[:20])
    imbalance = ((bid_depth_20 - ask_depth_20) / (bid_depth_20 + ask_depth_20)) if (bid_depth_20 + ask_depth_20) else None
    spread_bps = ((best_ask - best_bid) / mid * 10000.0) if best_bid and best_ask and mid else None
    return {
        "status": "ok" if bids and asks and best_bid and best_ask else "missing",
        "best_bid": round_or_none(best_bid),
        "best_ask": round_or_none(best_ask),
        "mid": round_or_none(mid),
        "spread_bps": round_or_none(spread_bps, 4),
        "bid_depth_usd_20": round_or_none(bid_depth_20, 2),
        "ask_depth_usd_20": round_or_none(ask_depth_20, 2),
        "depth_1pct_bid_usd": round_or_none(depth_within(mid or 0.0, bids, 1.0, "bid"), 2),
        "depth_1pct_ask_usd": round_or_none(depth_within(mid or 0.0, asks, 1.0, "ask"), 2),
        "order_book_imbalance_20": round_or_none(imbalance, 6),
        "last_update_id": depth.get("lastUpdateId"),
    }


def summarize_recent_trades(trades: Any) -> dict[str, Any]:
    if not isinstance(trades, list) or not trades:
        return {"status": "missing", "trade_count": 0}
    buy_quote = 0.0
    sell_quote = 0.0
    total_quote = 0.0
    for trade in trades:
        price = as_float(trade.get("p"), 0.0) or 0.0
        qty = as_float(trade.get("q"), 0.0) or 0.0
        quote = price * qty
        total_quote += quote
        if trade.get("m"):
            sell_quote += quote
        else:
            buy_quote += quote
    return {
        "status": "ok",
        "trade_count": len(trades),
        "quote_volume": round_or_none(total_quote, 2),
        "taker_buy_quote_ratio": round_or_none(buy_quote / total_quote, 6) if total_quote else None,
        "taker_sell_quote_ratio": round_or_none(sell_quote / total_quote, 6) if total_quote else None,
    }


def endpoint_status(name: str, fetcher, errors: list[dict[str, Any]]) -> tuple[str, Any, dict[str, Any]]:
    try:
        payload, meta = fetcher()
        return "ok", payload, meta
    except BinancePublicFetchError as exc:
        errors.append(
            {
                "endpoint": name,
                "status": "error",
                "error": f"{exc.primary_error_type}: all_public_hosts_failed",
                "path": exc.path,
                "failures": exc.failures,
            }
        )
        return "error", None, {"path": exc.path, "failures": exc.failures}
    except Exception as exc:  # noqa: BLE001 - report exact fetch failure without crashing the audit
        errors.append({"endpoint": name, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return "error", None, {}


def audit_symbol(symbol: str, intervals: list[str], base_urls: list[str], timeout: float, kline_limit: int) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    endpoint_meta: dict[str, Any] = {}
    ticker_status, ticker, ticker_meta = endpoint_status(
        "ticker_24hr",
        lambda: fetch_json_with_fallback(base_urls, "/ticker/24hr", {"symbol": symbol}, timeout),
        errors,
    )
    endpoint_meta["ticker_24hr"] = ticker_meta
    book_status, book, book_meta = endpoint_status(
        "book_ticker",
        lambda: fetch_json_with_fallback(base_urls, "/ticker/bookTicker", {"symbol": symbol}, timeout),
        errors,
    )
    endpoint_meta["book_ticker"] = book_meta
    depth_status, depth, depth_meta = endpoint_status(
        "depth",
        lambda: fetch_json_with_fallback(base_urls, "/depth", {"symbol": symbol, "limit": 100}, timeout),
        errors,
    )
    endpoint_meta["depth"] = depth_meta
    trades_status, trades, trades_meta = endpoint_status(
        "agg_trades",
        lambda: fetch_json_with_fallback(base_urls, "/aggTrades", {"symbol": symbol, "limit": 120}, timeout),
        errors,
    )
    endpoint_meta["agg_trades"] = trades_meta
    kline_summaries: dict[str, Any] = {}
    for interval in intervals:
        status, rows, kline_meta = endpoint_status(
            f"klines_{interval}",
            lambda interval=interval: fetch_json_with_fallback(base_urls, "/klines", {"symbol": symbol, "interval": interval, "limit": kline_limit}, timeout),
            errors,
        )
        endpoint_meta[f"klines_{interval}"] = kline_meta
        parsed = parse_klines(rows) if status == "ok" else []
        kline_summaries[interval] = summarize_klines(parsed)
    depth_summary = summarize_depth(depth or {}, book if isinstance(book, dict) else None)
    trade_summary = summarize_recent_trades(trades)
    missing_intervals = [interval for interval, item in kline_summaries.items() if item.get("status") != "ok"]
    quote_volume_24h = as_float((ticker or {}).get("quoteVolume"))
    price_change_24h = as_float((ticker or {}).get("priceChangePercent"))
    endpoint_map = {
        "ticker_24hr": ticker_status,
        "book_ticker": book_status,
        "depth": depth_status,
        "agg_trades": trades_status,
        **{f"klines_{interval}": kline_summaries[interval].get("status") for interval in intervals},
    }
    symbol_status = "pass"
    warnings: list[str] = []
    if any(value != "ok" for value in endpoint_map.values()):
        symbol_status = "blocked"
        warnings.append("required_endpoint_missing_or_error")
    spread_bps = as_float(depth_summary.get("spread_bps"))
    min_depth_1pct = min(depth_summary.get("depth_1pct_bid_usd") or 0.0, depth_summary.get("depth_1pct_ask_usd") or 0.0)
    if spread_bps is None or spread_bps > 35:
        symbol_status = "warning" if symbol_status == "pass" else symbol_status
        warnings.append("spread_missing_or_above_35bps")
    if min_depth_1pct < 25_000:
        symbol_status = "warning" if symbol_status == "pass" else symbol_status
        warnings.append("depth_1pct_below_25000_usd")
    return {
        "symbol": symbol,
        "status": symbol_status,
        "warnings": warnings,
        "endpoint_status": endpoint_map,
        "endpoint_sources": {
            endpoint: {
                "base_url": meta.get("base_url"),
                "attempt_count": meta.get("attempt_count"),
                "fallback_failure_count": len(meta.get("fallback_failures") or []),
            }
            for endpoint, meta in endpoint_meta.items()
            if isinstance(meta, dict)
        },
        "endpoint_fallback_failures": {
            endpoint: meta.get("fallback_failures")
            for endpoint, meta in endpoint_meta.items()
            if isinstance(meta, dict) and meta.get("fallback_failures")
        },
        "price": {
            "last_price": round_or_none(as_float((ticker or {}).get("lastPrice"))),
            "price_change_24h_pct": round_or_none(price_change_24h, 4),
            "quote_volume_24h_usd": round_or_none(quote_volume_24h, 2),
            "trade_count_24h": int(as_float((ticker or {}).get("count"), 0) or 0),
        },
        "depth": depth_summary,
        "recent_trades": trade_summary,
        "klines": kline_summaries,
        "missing_intervals": missing_intervals,
        "errors": errors,
    }


def status_from_symbols(symbols: list[dict[str, Any]]) -> dict[str, Any]:
    blocked = [item for item in symbols if item.get("status") == "blocked"]
    warning = [item for item in symbols if item.get("status") == "warning"]
    if blocked:
        status = "blocked"
    elif warning:
        status = "warning"
    else:
        status = "pass"
    endpoint_counts: dict[str, int] = {}
    for item in symbols:
        for endpoint, endpoint_status_value in (item.get("endpoint_status") or {}).items():
            endpoint_counts[f"{endpoint}:{endpoint_status_value}"] = endpoint_counts.get(f"{endpoint}:{endpoint_status_value}", 0) + 1
    return {
        "status": status,
        "blocked_symbol_count": len(blocked),
        "warning_symbol_count": len(warning),
        "passed_symbol_count": len([item for item in symbols if item.get("status") == "pass"]),
        "endpoint_counts": endpoint_counts,
    }


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# Binance Market Data Health Audit",
        "",
        f"- run_id: `{payload.get('run_id')}`",
        f"- created_at: `{payload.get('created_at')}`",
        f"- scope: `public_spot_market_data_read_only`",
        f"- status: `{summary.get('status')}`",
        f"- live_orders_enabled: `{payload.get('live_orders_enabled')}`",
        f"- private_api_used: `{payload.get('private_api_used')}`",
        f"- base_urls: `{', '.join(payload.get('base_urls') or [])}`",
        f"- symbols_checked: `{', '.join(payload.get('symbols_requested') or [])}`",
        f"- intervals_checked: `{', '.join(payload.get('intervals_requested') or [])}`",
        f"- passed/warning/blocked: `{summary.get('passed_symbol_count')}/{summary.get('warning_symbol_count')}/{summary.get('blocked_symbol_count')}`",
        "",
        "## Symbol Health",
        "",
        "| Symbol | Status | 24h % | 24h Quote Vol | Spread bps | 1% Depth Min | Taker Buy | Missing |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in payload.get("symbols") or []:
        depth = item.get("depth") or {}
        recent = item.get("recent_trades") or {}
        price = item.get("price") or {}
        min_depth = min(depth.get("depth_1pct_bid_usd") or 0.0, depth.get("depth_1pct_ask_usd") or 0.0)
        missing = ", ".join(item.get("missing_intervals") or [])
        lines.append(
            f"| `{item.get('symbol')}` | `{item.get('status')}` | `{price.get('price_change_24h_pct')}` | "
            f"`{price.get('quote_volume_24h_usd')}` | `{depth.get('spread_bps')}` | `{round_or_none(min_depth, 2)}` | "
            f"`{recent.get('taker_buy_quote_ratio')}` | `{missing or '-'}` |"
        )
    lines.extend(
        [
            "",
            "## Endpoint Counts",
            "",
        ]
    )
    for key, count in sorted((summary.get("endpoint_counts") or {}).items()):
        lines.append(f"- `{key}`: `{count}`")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- This audit never places orders and never calls private/account/withdraw/margin/futures/perpetual endpoints.",
            "- If status is `blocked`, downstream paper scans should degrade to report-only until market data recovers.",
            "",
        ]
    )
    return "\n".join(lines)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    created = now_local()
    run_id = f"{created.strftime('%Y%m%d-%H%M%S')}-binance-market-data-health"
    symbols_requested = [item.strip().upper() for item in str(args.symbols).split(",") if item.strip()]
    intervals_requested = [item.strip() for item in str(args.intervals).split(",") if item.strip()]
    base_urls = normalize_base_urls(args.base_url)
    symbol_results = [
        audit_symbol(symbol, intervals_requested, base_urls, args.timeout_seconds, args.kline_limit)
        for symbol in symbols_requested
    ]
    summary = status_from_symbols(symbol_results)
    date = created.strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"{date}-{created.strftime('%H%M%S')}-binance-market-data-health.md"
    experiment_path = EXPERIMENTS_DIR / f"{run_id}.json"
    payload = {
        "run_id": run_id,
        "created_at": created.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "script": "scripts/binance_market_data_health_auditor.py",
        "scope": "public_spot_market_data_read_only",
        "base_url": base_urls[0],
        "base_urls": base_urls,
        "symbols_requested": symbols_requested,
        "intervals_requested": intervals_requested,
        "kline_limit": args.kline_limit,
        "summary": summary,
        "symbols": symbol_results,
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "ledger_mutated": False,
        "outputs": {"report": rel(report_path), "experiment": rel(experiment_path)},
    }
    if not args.no_write:
        write_json(experiment_path, payload)
        write_text(report_path, render_report(payload))
    return payload


def compact(payload: dict[str, Any]) -> dict[str, Any]:
    symbols = payload.get("symbols") or []
    return {
        "status": (payload.get("summary") or {}).get("status"),
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "symbols_checked": [item.get("symbol") for item in symbols],
        "passed_symbol_count": (payload.get("summary") or {}).get("passed_symbol_count"),
        "warning_symbol_count": (payload.get("summary") or {}).get("warning_symbol_count"),
        "blocked_symbol_count": (payload.get("summary") or {}).get("blocked_symbol_count"),
        "endpoint_counts": (payload.get("summary") or {}).get("endpoint_counts"),
        "base_urls": payload.get("base_urls"),
        "endpoint_sources": {
            item.get("symbol"): item.get("endpoint_sources")
            for item in symbols
        },
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "allow_real_orders": payload.get("allow_real_orders"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "outputs": payload.get("outputs"),
    }


def self_test() -> dict[str, Any]:
    sample_klines = [
        [1, "100", "101", "99", "100", "10", 0, "1000", 10],
        [2, "100", "102", "98", "101", "12", 0, "1212", 12],
        [3, "101", "103", "100", "102", "11", 0, "1122", 11],
    ]
    kline_summary = summarize_klines(parse_klines(sample_klines))
    depth = summarize_depth(
        {
            "lastUpdateId": 1,
            "bids": [["99.9", "100"], ["99.5", "200"]],
            "asks": [["100.1", "100"], ["100.5", "200"]],
        },
        {"bidPrice": "99.9", "askPrice": "100.1"},
    )
    trades = summarize_recent_trades(
        [
            {"p": "100", "q": "1", "m": False},
            {"p": "101", "q": "2", "m": True},
        ]
    )
    good_symbol = {
        "status": "pass",
        "endpoint_status": {"ticker_24hr": "ok", "book_ticker": "ok", "depth": "ok", "agg_trades": "ok", "klines_1m": "ok"},
    }
    blocked_symbol = {
        "status": "blocked",
        "endpoint_status": {"ticker_24hr": "error", "book_ticker": "ok", "depth": "ok", "agg_trades": "ok", "klines_1m": "missing"},
    }
    status = status_from_symbols([good_symbol, blocked_symbol])
    original_urlopen = urllib.request.urlopen

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
        fallback_payload, fallback_meta = fetch_json_with_fallback(
            ["https://bad-host/api/v3", "https://good-host/api/v3"],
            "/ticker/24hr",
            {"symbol": "BTCUSDT"},
        )
    finally:
        urllib.request.urlopen = original_urlopen
    assert fallback_payload == {"ok": True}, fallback_payload
    assert fallback_meta["base_url"] == "https://good-host/api/v3", fallback_meta
    assert len(fallback_meta["fallback_failures"]) == 1, fallback_meta

    def failing_urlopen(req: Any, timeout: float = 0.0) -> FakeResponse:
        raise TimeoutError("self-test all hosts failed")

    urllib.request.urlopen = failing_urlopen
    try:
        try:
            fetch_json_with_fallback(["https://bad-a/api/v3", "https://bad-b/api/v3"], "/depth", {"symbol": "BTCUSDT"})
        except BinancePublicFetchError as exc:
            assert exc.path == "/depth", exc
            assert exc.primary_error_type == "TimeoutError", exc
            assert len(exc.failures) == 2, exc.failures
        else:
            raise AssertionError("expected BinancePublicFetchError")
    finally:
        urllib.request.urlopen = original_urlopen
    with tempfile.TemporaryDirectory(prefix="binance_market_data_health_", dir="/private/tmp") as tmp_text:
        tmp = Path(tmp_text)
        report = tmp / "report.md"
        payload = {
            "run_id": "self-test",
            "created_at": now_local().isoformat(),
            "summary": status,
            "symbols_requested": ["BTCUSDT"],
            "intervals_requested": ["1m"],
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "pass",
                    "price": {"price_change_24h_pct": 1.0, "quote_volume_24h_usd": 1000},
                    "depth": depth,
                    "recent_trades": trades,
                    "missing_intervals": [],
                }
            ],
            "live_orders_enabled": False,
            "private_api_used": False,
            "base_url": BINANCE_BASE,
            "base_urls": BINANCE_BASE_URLS,
        }
        write_text(report, render_report(payload))
        report_written = report.exists() and "Binance Market Data Health Audit" in report.read_text(encoding="utf-8")
    assert kline_summary["status"] == "ok", kline_summary
    assert depth["status"] == "ok" and depth["spread_bps"] is not None, depth
    assert trades["status"] == "ok" and trades["trade_count"] == 2, trades
    assert status["status"] == "blocked" and status["blocked_symbol_count"] == 1, status
    assert report_written
    return {
        "status": "ok",
        "kline_summary_verified": True,
        "depth_summary_verified": True,
        "recent_trade_summary_verified": True,
        "blocked_status_verified": True,
        "host_fallback_verified": True,
        "all_host_failure_records_verified": True,
        "report_render_verified": True,
        "uses_temporary_files_only": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--intervals", default=",".join(DEFAULT_INTERVALS))
    parser.add_argument("--base-url", default=",".join(BINANCE_BASE_URLS))
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--kline-limit", type=int, default=24)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return
    payload = build_payload(args)
    print(json.dumps(compact(payload) if args.compact_output else payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
