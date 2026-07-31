#!/usr/bin/env python3
"""Build a no-secret portfolio comparison snapshot.

Uses public Binance endpoints for crypto and Yahoo chart data for US equities.
Holding quantities come from the user's current screenshot-derived ledger.
Historical comparison assumes quantities are constant unless the ledger is
updated with dated trades.
"""

import json
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from v3_portfolio_state import resolve_portfolio_state_from_files


TIMEOUT = 15
ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = ROOT / "unified-longterm-alpha-investor" / "config" / "portfolio_ledger.json"
OVERRIDES_PATH = ROOT / "manual-investment-strategy-operator" / "config" / "current_position_overrides.json"
USER_CONFIRMED_STATES_PATH = (
    ROOT
    / "manual-investment-strategy-operator"
    / "runtime"
    / "user_confirmed_account_states.jsonl"
)

UNALLOCATED_EARN_FALLBACK_USD = 0.0

WINDOW_DAYS = {"current": 0, "1d": 1, "7d": 7, "30d": 30}

CRYPTO_OVERRIDE_BUCKETS = {
    "ADA": "ADA staking satellite",
    "SOL": "SOL growth",
    "NIGHT": "Tactical crypto",
    "ENA": "Tactical crypto",
    "BTC": "BTC liquidity anchor",
    "USDT": "Stablecoin/cash",
}

US_EQUITY_OVERRIDE_BUCKETS = {
    "CRCL": "US equity long-term protected",
    "APLD": "US equity tactical",
    "COIN": "US equity crypto beta",
    "SOXL": "US equity tactical leveraged ETF",
}


def load_json_if_exists(path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def ledger_price_fallback(holding, quantity):
    price = holding.get("current_price")
    if price is not None:
        return float(price)
    market_value = holding.get("market_value")
    if market_value is not None and quantity:
        return float(market_value) / float(quantity)
    return None


def build_holdings_from_ledger():
    ledger = load_json_if_exists(LEDGER_PATH)
    overrides = load_json_if_exists(OVERRIDES_PATH)
    holding_overrides = overrides.get("holdings", {})
    cash_overrides = overrides.get("cash_rails", {})
    portfolio_state = resolve_portfolio_state_from_files(
        overrides_path=OVERRIDES_PATH,
        legacy_ledger_path=LEDGER_PATH,
        user_confirmed_states_path=USER_CONFIRMED_STATES_PATH,
    )

    crypto = {}
    equity = {}
    lceth_quantity = 0.0
    eth_dust_quantity = 0.0
    eth_like_market_value = 0.0
    unallocated_earn_usd = UNALLOCATED_EARN_FALLBACK_USD
    unallocated_earn_status = "missing"

    for holding in ledger.get("holdings", []):
        symbol = holding.get("symbol")
        if symbol == "UNALLOCATED_EARN_BALANCE":
            unallocated_earn_usd = float(holding.get("market_value") or UNALLOCATED_EARN_FALLBACK_USD)
            unallocated_earn_status = holding.get("data_quality_status") or holding.get("status") or "ledger_value"
            continue
        quantity = holding.get("quantity")
        if quantity is None:
            continue
        override = holding_overrides.get(symbol, {})
        quantity = float(
            portfolio_state.get(f"holdings.{symbol}.quantity", quantity)
        )
        account_rail = holding.get("account_rail")
        bucket = holding.get("bucket") or "unknown"

        if symbol == "lcETH":
            lceth_quantity += quantity
            eth_like_market_value += float(holding.get("market_value") or 0.0)
            continue
        if symbol == "ETH":
            eth_dust_quantity += quantity
            eth_like_market_value += float(holding.get("market_value") or 0.0)
            continue
        if account_rail == "crypto_rail" and symbol in CRYPTO_OVERRIDE_BUCKETS:
            crypto[symbol] = {
                "display": symbol,
                "quantity": quantity,
                    "rail": "crypto",
                    "bucket": {
                    "ADA": "ADA staking satellite",
                    "SOL": "SOL growth",
                    "NIGHT": "Tactical crypto",
                    "ENA": "Tactical crypto",
                    "BTC": "BTC liquidity anchor",
                    "USDT": "Stablecoin/cash",
                    }.get(symbol, bucket),
                    "ledger_price_fallback": ledger_price_fallback(holding, quantity),
                }
        if account_rail == "us_equity_rail" and symbol in US_EQUITY_OVERRIDE_BUCKETS:
            equity[symbol] = {
                "display": symbol,
                    "quantity": quantity,
                    "rail": "us_equity",
                    "bucket": "US equity",
                    "ledger_price_fallback": ledger_price_fallback(holding, quantity),
                }

    # Screenshot/user-stated overrides are the freshest position layer. They
    # must be able to add holdings that are not yet present in the older
    # portfolio ledger, otherwise new tactical positions such as ENA/APLD get
    # silently dropped from later skill runs.
    for symbol, override in holding_overrides.items():
        quantity = override.get("quantity")
        if quantity is None:
            continue
        quantity = float(
            portfolio_state.get(f"holdings.{symbol}.quantity", quantity)
        )
        if symbol in {"lcETH", "ETH"}:
            if symbol == "lcETH":
                lceth_quantity = quantity
            else:
                eth_dust_quantity = quantity
            continue
        if symbol in CRYPTO_OVERRIDE_BUCKETS and symbol not in crypto:
            crypto[symbol] = {
                "display": symbol,
                "quantity": quantity,
                "rail": "crypto",
                "bucket": CRYPTO_OVERRIDE_BUCKETS[symbol],
                "ledger_price_fallback": None,
            }
        if symbol in US_EQUITY_OVERRIDE_BUCKETS and symbol not in equity:
            equity[symbol] = {
                "display": symbol,
                "quantity": quantity,
                "rail": "us_equity",
                "bucket": US_EQUITY_OVERRIDE_BUCKETS[symbol],
                "ledger_price_fallback": None,
            }

    eth_like_quantity = lceth_quantity + eth_dust_quantity
    crypto["ETH"] = {
        "display": "lcETH/ETH proxy",
        "quantity": eth_like_quantity,
        "rail": "crypto",
        "bucket": "ETH/lcETH core",
        "ledger_price_fallback": eth_like_market_value / eth_like_quantity if eth_like_quantity else None,
    }

    us_equity_cash = float(portfolio_state.get("cash.us_equity.USD", 0.0))
    return (
        crypto,
        equity,
        us_equity_cash,
        overrides,
        unallocated_earn_usd,
        unallocated_earn_status,
        portfolio_state,
    )


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "codex-investing-skill/1.3"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def binance_klines(symbol, interval="1d", limit=45):
    query = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": limit})
    rows = fetch_json(f"https://api.binance.com/api/v3/klines?{query}")
    return [
        {
            "open_time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "close_time": int(row[6]),
        }
        for row in rows
    ]


def binance_ticker(symbol):
    query = urllib.parse.urlencode({"symbol": symbol})
    body = fetch_json(f"https://api.binance.com/api/v3/ticker/24hr?{query}")
    return {
        "price": float(body["lastPrice"]),
        "price_change_pct_24h": float(body["priceChangePercent"]),
        "quote_volume_24h": float(body["quoteVolume"]),
    }


def yahoo_chart(symbol, rng="2mo", interval="1d"):
    query = urllib.parse.urlencode({"range": rng, "interval": interval, "includePrePost": "false"})
    body = fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}")
    result = body.get("chart", {}).get("result", [{}])[0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    rows = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        rows.append({"time": int(ts), "close": float(close)})
    return {
        "regular_market_price": meta.get("regularMarketPrice"),
        "previous_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName"),
        "rows": rows,
    }


def close_at_or_before(rows, target_ts):
    candidates = [row for row in rows if row.get("time", 0) <= target_ts]
    if not candidates:
        return None
    return candidates[-1]["close"]


def kline_close_at_or_before(rows, target_ms):
    candidates = [row for row in rows if row.get("close_time", 0) <= target_ms]
    if not candidates:
        return None
    return candidates[-1]["close"]


def pct_change(current, past):
    if past in (None, 0) or current is None:
        return None
    return (current / past - 1.0) * 100.0


def fallback_series(price_value):
    return {window: price_value for window in WINDOW_DAYS}


def source_error_message(error):
    return f"{type(error).__name__}: {str(error)[:180]}"


def main():
    (
        crypto_holdings,
        equity_holdings,
        us_equity_cash_usd,
        overrides,
        unallocated_earn_usd,
        unallocated_earn_status,
        portfolio_state,
    ) = build_holdings_from_ledger()
    now = int(time.time())
    now_ms = now * 1000
    generated_at = datetime.now(timezone.utc).isoformat()
    prices = {}
    source_errors = []
    holdings = []

    for symbol, meta in crypto_holdings.items():
        if symbol == "USDT":
            series = {window: 1.0 for window in WINDOW_DAYS}
            data_quality = "verified_stablecoin_assumption"
            market = {}
        else:
            pair = f"{symbol}USDT"
            try:
                market = binance_ticker(pair)
                klines = binance_klines(pair)
                series = {"current": market["price"]}
                for window, days in WINDOW_DAYS.items():
                    if window == "current":
                        continue
                    series[window] = kline_close_at_or_before(klines, now_ms - days * 86400 * 1000)
                data_quality = "verified_binance_public"
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError, OSError) as error:
                fallback_price = meta.get("ledger_price_fallback")
                series = fallback_series(fallback_price)
                market = {
                    "source_error": source_error_message(error),
                    "fallback": "ledger_current_price_or_market_value",
                }
                data_quality = "stale_ledger_price_fallback_after_binance_error" if fallback_price is not None else "missing_after_binance_error"
                source_errors.append({"symbol": symbol, "source": "binance_public", "error": source_error_message(error)})
        prices[symbol] = series
        quantity = meta["quantity"]
        values = {window: (price * quantity if price is not None else None) for window, price in series.items()}
        holdings.append({
            "symbol": symbol,
            "display": meta["display"],
            "rail": meta["rail"],
            "bucket": meta["bucket"],
            "quantity": quantity,
            "prices": series,
            "values": values,
            "market": market,
            "data_quality": data_quality,
        })

    for symbol, meta in equity_holdings.items():
        try:
            chart = yahoo_chart(symbol)
            rows = chart["rows"]
            current = chart.get("regular_market_price") or (rows[-1]["close"] if rows else None)
            series = {"current": current}
            for window, days in WINDOW_DAYS.items():
                if window == "current":
                    continue
                series[window] = close_at_or_before(rows, now - days * 86400)
            market = {
                "exchange": chart.get("exchange"),
                "currency": chart.get("currency"),
            }
            data_quality = "verified_yahoo_public_chart"
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError, OSError, IndexError) as error:
            fallback_price = meta.get("ledger_price_fallback")
            series = fallback_series(fallback_price)
            market = {
                "source_error": source_error_message(error),
                "fallback": "ledger_current_price_or_market_value",
            }
            data_quality = "stale_ledger_price_fallback_after_yahoo_error" if fallback_price is not None else "missing_after_yahoo_error"
            source_errors.append({"symbol": symbol, "source": "yahoo_chart", "error": source_error_message(error)})
        quantity = meta["quantity"]
        values = {window: (price * quantity if price is not None else None) for window, price in series.items()}
        holdings.append({
            "symbol": symbol,
            "display": meta["display"],
            "rail": meta["rail"],
            "bucket": meta["bucket"],
            "quantity": quantity,
            "prices": series,
            "values": values,
            "market": market,
            "data_quality": data_quality,
        })

    usd_equity_series = {window: 1.0 for window in WINDOW_DAYS}
    holdings.append({
        "symbol": "USD_US_EQUITY",
        "display": "US equity cash from SOXL sale",
        "rail": "us_equity",
        "bucket": "US equity cash",
        "quantity": us_equity_cash_usd,
        "prices": usd_equity_series,
        "values": {window: us_equity_cash_usd for window in WINDOW_DAYS},
        "market": {},
        "data_quality": "user_stated_recent_sale_cash_assumption" if us_equity_cash_usd else "missing",
    })

    windows = list(WINDOW_DAYS)
    totals = {}
    for window in windows:
        crypto = sum((h["values"].get(window) or 0.0) for h in holdings if h["rail"] == "crypto")
        us_equity = sum((h["values"].get(window) or 0.0) for h in holdings if h["rail"] == "us_equity")
        unallocated = unallocated_earn_usd
        totals[window] = {
            "crypto_value": crypto + unallocated,
            "us_equity_value": us_equity,
            "unallocated_earn": unallocated,
            "total_value": crypto + us_equity + unallocated,
            "crypto_cash_or_stablecoin": next(h["values"][window] for h in holdings if h["symbol"] == "USDT"),
            "us_equity_cash": next(h["values"][window] for h in holdings if h["symbol"] == "USD_US_EQUITY"),
            "cash_or_stablecoin": (
                next(h["values"][window] for h in holdings if h["symbol"] == "USDT")
                + next(h["values"][window] for h in holdings if h["symbol"] == "USD_US_EQUITY")
            ),
        }

    for window in windows:
        base = totals[window]["total_value"]
        for h in holdings:
            value = h["values"].get(window)
            h.setdefault("weights", {})[window] = value / base * 100.0 if value is not None and base else None

    output = {
        "generated_at": generated_at,
        "private_api_keys_used": False,
        "live_orders_enabled": False,
        "ledger_path": str(LEDGER_PATH),
        "position_overrides_path": str(OVERRIDES_PATH) if OVERRIDES_PATH.exists() else None,
        "position_overrides_as_of": overrides.get("as_of"),
        "portfolio_state_v2": portfolio_state.to_dict(),
        "valuation_policy": "current and historical values assume constant quantities from the screenshot-derived ledger; historical prices use public Binance/Yahoo data",
        "windows": WINDOW_DAYS,
        "holdings": holdings,
        "totals": totals,
        "comparisons": {},
        "data_quality_summary": {
            "crypto": "verified_binance_public except USDT stablecoin assumption; per-asset fallback if source error",
            "us_equity": "verified_yahoo_public_chart; per-asset fallback if source error",
            "lceth": "degraded_eth_spot_proxy_for_lceth",
            "unallocated_earn": unallocated_earn_status,
            "us_equity_cash": "user_stated_recent_sale_cash_assumption" if us_equity_cash_usd else "missing",
            "position_quantity": "PortfolioStateV2_resolved_with_field_provenance",
        },
        "source_errors": source_errors,
    }

    current_total = totals["current"]["total_value"]
    for window in ["1d", "7d", "30d"]:
        past_total = totals[window]["total_value"]
        output["comparisons"][window] = {
            "total_change_usd": current_total - past_total,
            "total_change_pct": pct_change(current_total, past_total),
            "crypto_change_usd": totals["current"]["crypto_value"] - totals[window]["crypto_value"],
            "crypto_change_pct": pct_change(totals["current"]["crypto_value"], totals[window]["crypto_value"]),
            "us_equity_change_usd": totals["current"]["us_equity_value"] - totals[window]["us_equity_value"],
            "us_equity_change_pct": pct_change(totals["current"]["us_equity_value"], totals[window]["us_equity_value"]),
        }
    json.dump(output, sys.stdout, indent=2, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
