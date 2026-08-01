#!/usr/bin/env python3
"""
Build a public Binance kline cache for walk-forward research.

This script is research-only:
- public market-data endpoints only
- no account endpoints
- no live orders
- no private API key logging

It discovers liquid/high-movement USDT spot pairs, downloads recent klines,
and writes files in the format consumed by weekly_goal_strategy_lab.py.
"""

import argparse
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from kline_cache_storage import (
    build_kline_cache_manifest,
    inspect_kline_cache_storage,
    self_test as storage_self_test,
)


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
EXPERIMENTS_DIR = ACTIVE_ROOT / "experiments"
BINANCE_PUBLIC_HOSTS = [
    "https://api.binance.com",
    "https://data-api.binance.vision",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]
REQUEST_FALLBACK_EVENTS = []

DEFAULT_SEED_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "ADAUSDT",
    "LINKUSDT",
    "NEARUSDT",
    "SUIUSDT",
    "INJUSDT",
    "FETUSDT",
    "RENDERUSDT",
    "TRXUSDT",
    "PEPEUSDT",
    "WLDUSDT",
]

STABLE_OR_FIAT_BASES = {
    "USDT",
    "USDC",
    "FDUSD",
    "TUSD",
    "BUSD",
    "DAI",
    "USDP",
    "USD1",
    "EUR",
    "EURI",
    "TRY",
    "BRL",
    "GBP",
    "AUD",
    "JPY",
    "ZAR",
    "UAH",
    "RUB",
}

LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")

INTERVAL_TARGET_BARS = {
    "1m": 1440,
    "5m": 2016,
    "15m": 2016,
    "30m": 3000,
    "1h": 2160,
    "2h": 2500,
    "4h": 1440,
    "1d": 900,
}

REQUIRED_OPERATIONAL_INTERVALS = ["1m", "5m", "15m", "1h", "4h"]
DEFAULT_INTERVALS = REQUIRED_OPERATIONAL_INTERVALS + ["1d"]
INTERVAL_ROLES = {
    "1m": "exit_replay_and_impulse_refinement",
    "5m": "exit_replay_and_microstructure_confirmation",
    "15m": "short_tactical_signal",
    "1h": "intraday_signal_and_regime",
    "4h": "swing_signal_and_regime",
    "1d": "optional_long_horizon_research",
}
DEFAULT_MAX_KLINE_REQUESTS = 525


class RequestBudgetExceeded(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def request_json(path, params=None, timeout=20):
    query = urllib.parse.urlencode(params or {})
    headers = {
        "User-Agent": "codex-active-alpha-paper-monitor/1.0",
    }
    # Public endpoints do not need this. If present, only pass it as an HTTP
    # header and never echo it into output artifacts.
    api_key = os.environ.get("BINANCE_API_KEY")
    if api_key:
        headers["X-MBX-APIKEY"] = api_key
    failures = []
    for host in BINANCE_PUBLIC_HOSTS:
        url = host + path + (("?" + query) if query else "")
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if failures:
                REQUEST_FALLBACK_EVENTS.append(
                    {
                        "path": path,
                        "base_url": host,
                        "attempt_count": len(failures) + 1,
                        "fallback_failure_count": len(failures),
                        "failures": failures[:3],
                    }
                )
            return payload
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {
                    "base_url": host,
                    "path": path,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:180],
                }
            )
    REQUEST_FALLBACK_EVENTS.append(
        {
            "path": path,
            "status": "failed_all_hosts",
            "attempt_count": len(failures),
            "fallback_failure_count": len(failures),
            "failures": failures[:5],
        }
    )
    raise RuntimeError(
        "Binance public kline-cache request failed on all hosts: "
        + "; ".join(f"{item['base_url']}:{item['error_type']}" for item in failures)
    )


def is_spot_usdt_symbol(info):
    if info.get("status") != "TRADING":
        return False
    if info.get("quoteAsset") != "USDT":
        return False
    if not info.get("isSpotTradingAllowed", False):
        return False
    base = info.get("baseAsset", "")
    if base in STABLE_OR_FIAT_BASES:
        return False
    if base.endswith(LEVERAGED_SUFFIXES):
        return False
    if any(tag in info.get("symbol", "") for tag in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
        return False
    return True


def safe_float(value, default=0.0):
    try:
        f = float(value)
        if math.isfinite(f):
            return f
    except Exception:
        pass
    return default


def split_intervals(value):
    intervals = []
    for raw in str(value or "").split(","):
        interval = raw.strip()
        if interval and interval not in intervals:
            intervals.append(interval)
    return intervals


def interval_target_bars(interval, min_bars, target_override=0):
    return max(int(min_bars), int(target_override or INTERVAL_TARGET_BARS.get(interval, 2000)))


def planned_request_count(symbol_count, intervals, min_bars, target_override=0):
    per_symbol = sum(
        math.ceil(interval_target_bars(interval, min_bars, target_override) / 1000)
        for interval in intervals
    )
    return {
        "per_symbol": per_symbol,
        "total": max(0, int(symbol_count)) * per_symbol,
    }


def evaluate_interval_coverage(files_written, selected_symbols, required_intervals, min_bars):
    observed = {}
    for item in files_written:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        interval = str(item.get("interval") or "")
        if symbol and interval:
            observed[(symbol, interval)] = int(item.get("bars") or 0)
    missing_pairs = []
    short_pairs = []
    complete_symbols = []
    for symbol in selected_symbols:
        symbol_complete = True
        for interval in required_intervals:
            bars = observed.get((symbol, interval))
            if bars is None:
                missing_pairs.append({"symbol": symbol, "interval": interval})
                symbol_complete = False
            elif bars < min_bars:
                short_pairs.append({"symbol": symbol, "interval": interval, "bars": bars})
                symbol_complete = False
        if symbol_complete:
            complete_symbols.append(symbol)
    return {
        "status": "complete" if selected_symbols and not missing_pairs and not short_pairs else "incomplete",
        "required_intervals": list(required_intervals),
        "selected_symbol_count": len(selected_symbols),
        "complete_symbol_count": len(complete_symbols),
        "complete_symbols": complete_symbols,
        "missing_pair_count": len(missing_pairs),
        "missing_pairs": missing_pairs,
        "short_pair_count": len(short_pairs),
        "short_pairs": short_pairs,
        "min_bars": int(min_bars),
    }


def discover_symbols(top_symbols, seed_symbols):
    exchange_info = request_json("/api/v3/exchangeInfo")
    ticker_24h = request_json("/api/v3/ticker/24hr")
    symbol_info = {
        row["symbol"]: row
        for row in exchange_info.get("symbols", [])
        if is_spot_usdt_symbol(row)
    }
    tickers = [row for row in ticker_24h if row.get("symbol") in symbol_info]
    max_quote = max((safe_float(row.get("quoteVolume")) for row in tickers), default=1.0)
    max_count = max((safe_float(row.get("count")) for row in tickers), default=1.0)
    ranked = []
    for row in tickers:
        pct_abs = abs(safe_float(row.get("priceChangePercent")))
        high = safe_float(row.get("highPrice"))
        low = safe_float(row.get("lowPrice"))
        range_pct = ((high / low - 1.0) * 100.0) if low > 0 else 0.0
        quote_volume = safe_float(row.get("quoteVolume"))
        count = safe_float(row.get("count"))
        # Score favors tradability first, then recent movement. This is only
        # candidate discovery; weekly_goal_strategy_lab still does the real
        # historical split test.
        liquidity_score = min(1.0, quote_volume / max_quote)
        activity_score = min(1.0, count / max_count)
        movement_score = min(3.0, (pct_abs + range_pct) / 20.0)
        score = liquidity_score * 45.0 + activity_score * 20.0 + movement_score * 35.0
        ranked.append(
            {
                "symbol": row["symbol"],
                "base_asset": symbol_info[row["symbol"]].get("baseAsset"),
                "score": round(score, 4),
                "price_change_pct_24h": safe_float(row.get("priceChangePercent")),
                "range_pct_24h": round(range_pct, 4),
                "quote_volume_24h": quote_volume,
                "trade_count_24h": int(count),
                "last_price": safe_float(row.get("lastPrice")),
            }
        )
    ranked.sort(key=lambda row: row["score"], reverse=True)
    selected = []
    for symbol in seed_symbols:
        if symbol in symbol_info and symbol not in selected:
            selected.append(symbol)
    for row in ranked:
        if len(selected) >= top_symbols:
            break
        if row["symbol"] not in selected:
            selected.append(row["symbol"])
    return selected, ranked, symbol_info


def fetch_klines(symbol, interval, target_bars, sleep_sec=0.08, request_state=None, max_requests=None):
    rows = []
    end_time = None
    limit = 1000
    while len(rows) < target_bars:
        if request_state is not None and max_requests is not None:
            if int(request_state.get("used") or 0) >= int(max_requests):
                raise RequestBudgetExceeded(
                    f"Kline request budget exhausted at {request_state.get('used')}/{max_requests}"
                )
            request_state["used"] = int(request_state.get("used") or 0) + 1
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if end_time is not None:
            params["endTime"] = end_time
        batch = request_json("/api/v3/klines", params=params, timeout=25)
        if not batch:
            break
        converted = [
            {
                "t": int(k[0]),
                "o": float(k[1]),
                "h": float(k[2]),
                "l": float(k[3]),
                "c": float(k[4]),
                "v": float(k[5]),
                "ct": int(k[6]),
                "qv": float(k[7]),
                "n": int(k[8]),
            }
            for k in batch
        ]
        rows = converted + rows
        first_open = converted[0]["t"]
        end_time = first_open - 1
        if len(batch) < limit:
            break
        time.sleep(sleep_sec)
    deduped = {row["t"]: row for row in rows}
    sorted_rows = [deduped[t] for t in sorted(deduped)][-target_bars:]
    return sorted_rows


def write_cache(cache_dir, symbol, interval, rows):
    if not rows:
        return None
    start = rows[0]["t"]
    end = rows[-1]["t"]
    path = cache_dir / f"{symbol}_{interval}_{start}_{end}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path


def self_test():
    global REQUEST_FALLBACK_EVENTS
    original_urlopen = urllib.request.urlopen
    REQUEST_FALLBACK_EVENTS = []
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen_second_host_ok(req, timeout=20):
        url = req.full_url
        calls.append(url)
        if url.startswith("https://api.binance.com"):
            raise TimeoutError("unit primary host timeout")
        return FakeResponse([["unit"]])

    urllib.request.urlopen = fake_urlopen_second_host_ok
    try:
        payload = request_json("/api/v3/klines", {"symbol": "GOODUSDT"}, timeout=1)
    finally:
        urllib.request.urlopen = original_urlopen
    assert payload == [["unit"]], payload
    assert len(calls) == 2 and calls[1].startswith("https://data-api.binance.vision"), calls
    assert REQUEST_FALLBACK_EVENTS and REQUEST_FALLBACK_EVENTS[0]["fallback_failure_count"] == 1, REQUEST_FALLBACK_EVENTS

    urllib.request.urlopen = lambda req, timeout=20: (_ for _ in ()).throw(TimeoutError("unit all hosts down"))
    try:
        try:
            request_json("/api/v3/ticker/24hr", timeout=1)
            all_failed = False
        except RuntimeError as exc:
            all_failed = "failed on all hosts" in str(exc)
    finally:
        urllib.request.urlopen = original_urlopen
    assert all_failed, "request_json should surface all-host failure"
    assert split_intervals("1m,5m,1m,15m") == ["1m", "5m", "15m"]
    request_plan = planned_request_count(2, DEFAULT_INTERVALS, 300)
    assert request_plan["per_symbol"] == 14, request_plan
    assert request_plan["total"] == 28, request_plan
    complete_rows = [
        {"symbol": "BTCUSDT", "interval": interval, "bars": INTERVAL_TARGET_BARS[interval]}
        for interval in REQUIRED_OPERATIONAL_INTERVALS
    ]
    complete = evaluate_interval_coverage(
        complete_rows,
        ["BTCUSDT"],
        REQUIRED_OPERATIONAL_INTERVALS,
        300,
    )
    assert complete["status"] == "complete" and complete["complete_symbol_count"] == 1, complete
    short = evaluate_interval_coverage(
        [{**row, "bars": 120} if row["interval"] == "1m" else row for row in complete_rows],
        ["BTCUSDT"],
        REQUIRED_OPERATIONAL_INTERVALS,
        300,
    )
    assert short["status"] == "incomplete" and short["short_pair_count"] == 1, short
    missing = evaluate_interval_coverage(
        complete_rows[:-1],
        ["BTCUSDT"],
        REQUIRED_OPERATIONAL_INTERVALS,
        300,
    )
    assert missing["status"] == "incomplete" and missing["missing_pair_count"] == 1, missing
    storage_result = storage_self_test()
    return {
        "status": "ok",
        "host_fallback_verified": True,
        "all_host_failure_verified": True,
        "storage_presence_verified": storage_result.get("status") == "ok",
        "required_operational_intervals": REQUIRED_OPERATIONAL_INTERVALS,
        "tiered_request_plan_verified": True,
        "complete_interval_coverage_verified": True,
        "short_replay_degradation_verified": True,
        "missing_interval_degradation_verified": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "uses_temporary_files_only": False,
        "caller_controls_cache_dir": True,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", required="--self-test" not in sys.argv)
    ap.add_argument("--top-symbols", type=int, default=35)
    ap.add_argument("--seed-symbols", default=",".join(DEFAULT_SEED_SYMBOLS))
    ap.add_argument("--intervals", default=",".join(DEFAULT_INTERVALS))
    ap.add_argument(
        "--coverage-profile",
        choices=["operational", "requested_only"],
        default="operational",
        help="Operational requires 1m/5m/15m/1h/4h coverage; requested_only audits only requested intervals.",
    )
    ap.add_argument("--min-bars", type=int, default=300)
    ap.add_argument(
        "--target-bars",
        type=int,
        default=0,
        help="Override per-interval target bars. Use a smaller value for quick shadow-scan prefetch.",
    )
    ap.add_argument("--sleep-sec", type=float, default=0.08)
    ap.add_argument(
        "--max-kline-requests",
        type=int,
        default=DEFAULT_MAX_KLINE_REQUESTS,
        help="Hard public Kline request budget. Candidate symbols are trimmed before fetch when needed.",
    )
    ap.add_argument("--format", choices=["json", "markdown"], default="json")
    ap.add_argument("--no-summary-write", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    seed_symbols = [s.strip().upper() for s in args.seed_symbols.split(",") if s.strip()]
    intervals = split_intervals(args.intervals)
    required_intervals = REQUIRED_OPERATIONAL_INTERVALS if args.coverage_profile == "operational" else intervals
    summary = {
        "run_id": utc_now().strftime("%Y%m%d-%H%M%S-binance-kline-cache-builder"),
        "generated_at": utc_now().isoformat(),
        "cache_builder_version": "binance-kline-cache-builder-v4-durable-manifest",
        "private_api_keys_logged": False,
        "live_orders_enabled": False,
        "binance_public_market_data_only": True,
        "binance_public_hosts": BINANCE_PUBLIC_HOSTS,
        "cache_dir": str(cache_dir),
        "requested_top_symbols": args.top_symbols,
        "requested_intervals": intervals,
        "interval_target_bars": {
            interval: interval_target_bars(interval, args.min_bars, args.target_bars)
            for interval in intervals
        },
        "interval_roles": {interval: INTERVAL_ROLES.get(interval, "additional_research") for interval in intervals},
        "coverage_profile": args.coverage_profile,
        "required_intervals": required_intervals,
        "request_budget": {
            "max_kline_requests": max(0, int(args.max_kline_requests)),
            "used_kline_requests": 0,
            "planned_before_symbol_trim": 0,
            "planned_after_symbol_trim": 0,
            "trimmed_symbol_count": 0,
            "trimmed_symbols": [],
        },
        "selected_symbols": [],
        "symbol_selection_note": "High liquidity plus recent 24h movement; this is discovery only, not a buy signal.",
        "files_written": [],
        "failures": [],
    }

    discovery_failed = False
    try:
        selected, ranked, symbol_info = discover_symbols(args.top_symbols, seed_symbols)
    except Exception as exc:  # noqa: BLE001 - structured degradation is better than empty stdout.
        discovery_failed = True
        selected = seed_symbols[: max(1, args.top_symbols)]
        ranked = []
        symbol_info = {symbol: {"symbol": symbol, "fallback_seed": True} for symbol in selected}
        summary["failures"].append(
            {
                "stage": "symbol_discovery",
                "reason": type(exc).__name__,
                "message": str(exc)[:500],
                "fallback": "direct_seed_symbols",
            }
        )
        summary["symbol_selection_note"] = (
            "Symbol discovery failed; directly attempted requested seed symbols. "
            "This is still public market-data only and not a buy signal."
        )
    summary["selected_symbols"] = selected
    summary["top_discovery_ranked"] = ranked[: max(args.top_symbols, 10)]
    missing_seeds = [s for s in seed_symbols if s not in symbol_info]
    if missing_seeds:
        summary["missing_seed_symbols"] = missing_seeds

    before_trim = planned_request_count(len(selected), intervals, args.min_bars, args.target_bars)
    summary["request_budget"]["planned_before_symbol_trim"] = before_trim["total"]
    max_requests = max(0, int(args.max_kline_requests))
    per_symbol_requests = before_trim["per_symbol"]
    if per_symbol_requests > 0 and before_trim["total"] > max_requests:
        allowed_symbols = max_requests // per_symbol_requests
        trimmed_symbols = selected[allowed_symbols:]
        selected = selected[:allowed_symbols]
        summary["selected_symbols"] = selected
        summary["request_budget"]["trimmed_symbol_count"] = len(trimmed_symbols)
        summary["request_budget"]["trimmed_symbols"] = trimmed_symbols
        summary["failures"].append(
            {
                "stage": "request_budget_precheck",
                "reason": "symbol_universe_trimmed_to_request_budget",
                "planned_requests": before_trim["total"],
                "max_kline_requests": max_requests,
                "trimmed_symbol_count": len(trimmed_symbols),
            }
        )
    after_trim = planned_request_count(len(selected), intervals, args.min_bars, args.target_bars)
    summary["request_budget"]["planned_after_symbol_trim"] = after_trim["total"]
    request_state = {"used": 0}

    for symbol in selected:
        for interval in intervals:
            try:
                target = interval_target_bars(interval, args.min_bars, args.target_bars)
                rows = fetch_klines(
                    symbol,
                    interval,
                    target,
                    sleep_sec=args.sleep_sec,
                    request_state=request_state,
                    max_requests=max_requests,
                )
                if len(rows) < args.min_bars:
                    summary["failures"].append(
                        {
                            "symbol": symbol,
                            "interval": interval,
                            "reason": "too_few_bars",
                            "bars": len(rows),
                        }
                    )
                    continue
                path = write_cache(cache_dir, symbol, interval, rows)
                summary["files_written"].append(
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "bars": len(rows),
                        "path": str(path),
                        "window": [
                            datetime.fromtimestamp(rows[0]["t"] / 1000, timezone.utc).isoformat(),
                            datetime.fromtimestamp(rows[-1]["t"] / 1000, timezone.utc).isoformat(),
                        ],
                    }
                )
            except Exception as exc:
                summary["failures"].append(
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "reason": type(exc).__name__,
                        "message": str(exc)[:240],
                    }
                )
    summary["request_budget"]["used_kline_requests"] = int(request_state["used"])
    summary["file_count"] = len(summary["files_written"])
    summary["failure_count"] = len(summary["failures"])
    summary["fallback_event_count"] = len(REQUEST_FALLBACK_EVENTS)
    summary["fallback_events"] = REQUEST_FALLBACK_EVENTS[:20]
    coverage = evaluate_interval_coverage(
        summary["files_written"],
        selected,
        required_intervals,
        args.min_bars,
    )
    summary["interval_contract"] = {
        "profile": args.coverage_profile,
        "required_operational_intervals": REQUIRED_OPERATIONAL_INTERVALS,
        "required_intervals": required_intervals,
        "optional_research_intervals": ["1d"],
        "requested_intervals": intervals,
        "coverage": coverage,
    }
    request_budget_exhausted = any(
        item.get("reason") == "RequestBudgetExceeded" for item in summary["failures"]
    )
    if request_budget_exhausted:
        summary["status"] = "degraded_request_budget_exhausted"
    elif coverage["status"] != "complete":
        summary["status"] = "degraded_interval_coverage"
    elif summary["file_count"] > 0:
        summary["status"] = "ok_direct_seed_fallback" if discovery_failed else "ok"
    elif discovery_failed:
        summary["status"] = "degraded_discovery_failed"
    else:
        summary["status"] = "degraded_no_files_written"
    summary["completed_at"] = utc_now().isoformat()
    if summary["file_count"] > 0:
        summary["cache_manifest"] = build_kline_cache_manifest(
            cache_dir,
            required_intervals=required_intervals,
            selected_symbols=selected,
            generated_at=summary["completed_at"],
        )
    else:
        summary["cache_manifest"] = {
            "manifest_version": "kline-cache-manifest-v1",
            "manifest_path": str(cache_dir / "kline-cache-manifest.json"),
            "file_count": 0,
            "valid_file_count": 0,
            "invalid_file_count": 0,
            "status": "not_written_no_kline_files",
        }
    summary["storage"] = inspect_kline_cache_storage(summary)
    if not args.no_summary_write:
        EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
        summary_path = EXPERIMENTS_DIR / f"{summary['run_id']}.json"
        summary["outputs"] = {
            "experiment": str(summary_path.relative_to(ACTIVE_ROOT.parent)),
            "cache_dir": str(cache_dir),
        }
        summary["storage"] = inspect_kline_cache_storage(summary)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.format == "markdown":
        print(f"# Binance Kline Cache Builder\n")
        print(f"- cache_dir: `{summary['cache_dir']}`")
        print(f"- selected_symbols: {', '.join(selected)}")
        print(f"- files_written: {summary['file_count']}")
        print(f"- failures: {summary['failure_count']}")
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
