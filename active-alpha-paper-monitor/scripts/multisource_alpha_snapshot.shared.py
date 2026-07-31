#!/usr/bin/env python3
"""
Public-data multi-source alpha snapshot for V2.18 research.

No private API keys are used. No orders are placed. The script collects public
sentiment, spot momentum, derivatives crowding, trending-search, DeFi liquidity
and lightweight Reddit headline context, then emits a JSON snapshot for reports.
"""

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,LINKUSDT,SUIUSDT,NEARUSDT,DOGEUSDT,ADAUSDT,BNBUSDT"
UA = "Mozilla/5.0 (compatible; unified-longterm-alpha-investor/2.18; research-only)"
COINGECKO_IDS = {
    "BTCUSDT": "bitcoin",
    "ETHUSDT": "ethereum",
    "SOLUSDT": "solana",
    "ADAUSDT": "cardano",
    "NEARUSDT": "near",
    "LINKUSDT": "chainlink",
    "SUIUSDT": "sui",
    "INJUSDT": "injective-protocol",
    "FETUSDT": "fetch-ai",
    "WLDUSDT": "worldcoin-wld",
    "PEPEUSDT": "pepe",
    "XRPUSDT": "ripple",
    "DOGEUSDT": "dogecoin",
    "BNBUSDT": "binancecoin",
}


def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def safe_fetch(name, url):
    try:
        return {"name": name, "status": "ok", "url": url, "data": get_json(url)}
    except Exception as exc:
        return {"name": name, "status": "error", "url": url, "error": str(exc)}


def fetch_fear_greed(limit=10):
    return safe_fetch("alternative_me_fear_greed", f"https://api.alternative.me/fng/?limit={limit}&format=json")


def fetch_binance_spot(symbols):
    out = {}
    for symbol in symbols:
        url = "https://api.binance.com/api/v3/ticker/24hr?" + urllib.parse.urlencode({"symbol": symbol})
        item = safe_fetch(f"binance_spot_24hr_{symbol}", url)
        out[symbol] = item
        time.sleep(0.05)
    return out


def fetch_binance_klines(symbols, interval="1d", limit=31):
    out = {}
    for symbol in symbols:
        url = "https://api.binance.com/api/v3/klines?" + urllib.parse.urlencode(
            {"symbol": symbol, "interval": interval, "limit": limit}
        )
        item = safe_fetch(f"binance_klines_{interval}_{symbol}", url)
        out[symbol] = item
        time.sleep(0.05)
    return out


def fetch_binance_depth(symbols, limit=100):
    out = {}
    for symbol in symbols:
        url = "https://api.binance.com/api/v3/depth?" + urllib.parse.urlencode({"symbol": symbol, "limit": limit})
        item = safe_fetch(f"binance_depth_{symbol}", url)
        out[symbol] = item
        time.sleep(0.05)
    return out


def fetch_coingecko_markets(symbols):
    ids = [COINGECKO_IDS[s] for s in symbols if s in COINGECKO_IDS]
    if not ids:
        return {"name": "coingecko_markets", "status": "error", "url": None, "error": "no_supported_ids"}
    params = {
        "vs_currency": "usd",
        "ids": ",".join(sorted(set(ids))),
        "price_change_percentage": "24h,7d,30d",
        "per_page": 250,
        "page": 1,
    }
    url = "https://api.coingecko.com/api/v3/coins/markets?" + urllib.parse.urlencode(params)
    item = safe_fetch("coingecko_markets", url)
    if item["status"] == "ok":
        by_id = {row.get("id"): row for row in item.get("data") or []}
        item["by_symbol"] = {symbol: by_id.get(COINGECKO_IDS[symbol]) for symbol in symbols if symbol in COINGECKO_IDS}
    return item


def fetch_binance_derivatives(symbols):
    out = {}
    for symbol in symbols:
        premium_url = "https://fapi.binance.com/fapi/v1/premiumIndex?" + urllib.parse.urlencode({"symbol": symbol})
        oi_url = "https://fapi.binance.com/fapi/v1/openInterest?" + urllib.parse.urlencode({"symbol": symbol})
        out[symbol] = {
            "premium_index": safe_fetch(f"binance_futures_premium_{symbol}", premium_url),
            "open_interest": safe_fetch(f"binance_futures_open_interest_{symbol}", oi_url),
        }
        time.sleep(0.05)
    return out


def fetch_coingecko_trending():
    return safe_fetch("coingecko_trending", "https://api.coingecko.com/api/v3/search/trending")


def fetch_defillama_chains():
    return safe_fetch("defillama_chains", "https://api.llama.fi/v2/chains")


def fetch_reddit_context(keywords):
    query = " OR ".join(k.replace("USDT", "") for k in keywords)
    params = {
        "q": query,
        "restrict_sr": "false",
        "sort": "new",
        "t": "day",
        "limit": 25,
    }
    url = "https://www.reddit.com/search.json?" + urllib.parse.urlencode(params)
    item = safe_fetch("reddit_new_headlines_24h", url)
    headlines = []
    counts = {k.replace("USDT", ""): 0 for k in keywords}
    if item["status"] == "ok":
        children = item["data"].get("data", {}).get("children", [])
        for child in children:
            data = child.get("data", {})
            title = data.get("title", "")
            subreddit = data.get("subreddit", "")
            created_utc = data.get("created_utc")
            headlines.append({"subreddit": subreddit, "title": title[:240], "created_utc": created_utc})
            title_upper = title.upper()
            for key in counts:
                if key.upper() in title_upper:
                    counts[key] += 1
    item["headline_sample"] = headlines[:15]
    item["keyword_counts"] = counts
    return item


def as_float(obj, key, default=0.0):
    try:
        return float(obj.get(key, default))
    except Exception:
        return default


def kline_performance(item):
    if item.get("status") != "ok":
        return {"status": "missing", "reason": item.get("error")}
    rows = item.get("data") or []
    if len(rows) < 2:
        return {"status": "missing", "reason": "not_enough_kline_rows"}
    closes = []
    for row in rows:
        try:
            closes.append(float(row[4]))
        except Exception:
            continue
    if len(closes) < 2 or closes[-1] <= 0:
        return {"status": "missing", "reason": "invalid_closes"}

    def pct_from(days):
        if len(closes) <= days:
            return None
        base = closes[-1 - days]
        if base <= 0:
            return None
        return round((closes[-1] / base - 1.0) * 100.0, 4)

    return {
        "status": "ok",
        "last_close": closes[-1],
        "7d_pct": pct_from(7),
        "30d_pct": pct_from(30),
    }


def orderbook_liquidity(item, notionals=(25, 100, 500, 1000)):
    if item.get("status") != "ok":
        return {"status": "missing", "reason": item.get("error")}
    data = item.get("data") or {}
    bids = data.get("bids") or []
    asks = data.get("asks") or []
    if not bids or not asks:
        return {"status": "missing", "reason": "empty_book"}
    try:
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
    except Exception:
        return {"status": "missing", "reason": "invalid_top_of_book"}
    mid = (best_bid + best_ask) / 2.0
    spread_bps = ((best_ask - best_bid) / mid * 10000.0) if mid > 0 else None

    def depth_within(side, pct):
        total = 0.0
        limit_price = mid * (1.0 - pct / 100.0) if side == "bid" else mid * (1.0 + pct / 100.0)
        rows = bids if side == "bid" else asks
        for price_raw, qty_raw in rows:
            try:
                price = float(price_raw)
                qty = float(qty_raw)
            except Exception:
                continue
            if side == "bid" and price < limit_price:
                break
            if side == "ask" and price > limit_price:
                break
            total += price * qty
        return round(total, 2)

    def simulate_buy_slippage(notional):
        remaining = float(notional)
        spent = 0.0
        qty = 0.0
        for price_raw, qty_raw in asks:
            try:
                price = float(price_raw)
                level_qty = float(qty_raw)
            except Exception:
                continue
            level_notional = price * level_qty
            take = min(remaining, level_notional)
            qty += take / price
            spent += take
            remaining -= take
            if remaining <= 1e-9:
                break
        if remaining > 1e-6 or qty <= 0 or mid <= 0:
            return {"notional_usd": notional, "status": "insufficient_depth"}
        avg_price = spent / qty
        return {
            "notional_usd": notional,
            "status": "ok",
            "avg_fill_price": round(avg_price, 12),
            "slippage_bps_vs_mid": round((avg_price / mid - 1.0) * 10000.0, 4),
        }

    return {
        "status": "ok",
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "bid_ask_spread_bps": round(spread_bps, 4) if spread_bps is not None else None,
        "bid_depth_1pct_usd": depth_within("bid", 1),
        "ask_depth_1pct_usd": depth_within("ask", 1),
        "bid_depth_2pct_usd": depth_within("bid", 2),
        "ask_depth_2pct_usd": depth_within("ask", 2),
        "buy_slippage": [simulate_buy_slippage(n) for n in notionals],
    }


def build_market_quality(symbols, spot, klines, depth, coingecko):
    cg_by_symbol = coingecko.get("by_symbol") if coingecko.get("status") == "ok" else {}
    rows = {}
    for symbol in symbols:
        spot_data = (spot.get(symbol, {}).get("data") if spot.get(symbol, {}).get("status") == "ok" else {}) or {}
        kperf = kline_performance(klines.get(symbol, {}))
        ob = orderbook_liquidity(depth.get(symbol, {}))
        cg = (cg_by_symbol or {}).get(symbol) or {}
        binance_price = as_float(spot_data, "lastPrice", None)
        cg_price = cg.get("current_price")
        price_diff_bps = None
        try:
            if binance_price and cg_price:
                price_diff_bps = round((float(binance_price) / float(cg_price) - 1.0) * 10000.0, 4)
        except Exception:
            price_diff_bps = None
        rows[symbol] = {
            "symbol": symbol,
            "status": "verified" if binance_price and ob.get("status") == "ok" else "degraded",
            "binance_price": binance_price,
            "coingecko_price": cg_price,
            "binance_vs_coingecko_diff_bps": price_diff_bps,
            "binance_24h_pct": as_float(spot_data, "priceChangePercent", None),
            "binance_24h_quote_volume_usd": as_float(spot_data, "quoteVolume", None),
            "binance_kline_7d_pct": kperf.get("7d_pct"),
            "binance_kline_30d_pct": kperf.get("30d_pct"),
            "coingecko_7d_pct": cg.get("price_change_percentage_7d_in_currency"),
            "coingecko_30d_pct": cg.get("price_change_percentage_30d_in_currency"),
            "liquidity": ob,
            "missing_or_degraded": [
                item
                for item in [
                    "coingecko_cross_check_missing" if not cg else None,
                    "binance_kline_missing" if kperf.get("status") != "ok" else None,
                    "binance_orderbook_missing" if ob.get("status") != "ok" else None,
                ]
                if item
            ],
        }
    return rows


def build_signal_scores(symbols, spot, derivatives, reddit_counts):
    rows = []
    for symbol in symbols:
        spot_item = spot.get(symbol, {})
        spot_data = spot_item.get("data") if spot_item.get("status") == "ok" else {}
        prem = derivatives.get(symbol, {}).get("premium_index", {})
        prem_data = prem.get("data") if prem.get("status") == "ok" else {}
        oi = derivatives.get(symbol, {}).get("open_interest", {})
        oi_data = oi.get("data") if oi.get("status") == "ok" else {}

        pct_24h = as_float(spot_data, "priceChangePercent")
        quote_volume = as_float(spot_data, "quoteVolume")
        funding = as_float(prem_data, "lastFundingRate")
        mark_price = as_float(prem_data, "markPrice")
        open_interest = as_float(oi_data, "openInterest")
        key = symbol.replace("USDT", "")
        reddit_hits = reddit_counts.get(key, 0)

        trend_points = 0
        if pct_24h > 5:
            trend_points += 25
        elif pct_24h > 2:
            trend_points += 15
        elif pct_24h > 0:
            trend_points += 5
        elif pct_24h < -5:
            trend_points -= 15

        liquidity_points = 20 if quote_volume >= 250_000_000 else 12 if quote_volume >= 50_000_000 else 4
        crowding_penalty = 0
        if funding > 0.0003:
            crowding_penalty = -15
        elif funding > 0.0001:
            crowding_penalty = -6
        elif funding < -0.0001:
            crowding_penalty = 8
        social_points = min(reddit_hits * 5, 15)
        score = trend_points + liquidity_points + crowding_penalty + social_points

        rows.append(
            {
                "symbol": symbol,
                "price_change_24h_pct": round(pct_24h, 2),
                "quote_volume_24h_usd": round(quote_volume, 2),
                "last_funding_rate": funding,
                "mark_price": mark_price,
                "open_interest_contracts": open_interest,
                "reddit_keyword_hits_24h": reddit_hits,
                "multi_source_alpha_score_points": score,
                "interpretation": "watch" if score >= 30 else "paper_only" if score >= 15 else "no_deploy",
            }
        )
    rows.sort(key=lambda x: x["multi_source_alpha_score_points"], reverse=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    args = ap.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    fng = fetch_fear_greed()
    spot = fetch_binance_spot(symbols)
    klines = fetch_binance_klines(symbols)
    depth = fetch_binance_depth(symbols)
    coingecko_markets = fetch_coingecko_markets(symbols)
    derivatives = fetch_binance_derivatives(symbols)
    trending = fetch_coingecko_trending()
    defillama = fetch_defillama_chains()
    reddit = fetch_reddit_context(symbols)
    reddit_counts = reddit.get("keyword_counts", {})

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "private_api_keys_used": False,
        "live_orders_enabled": False,
        "sources": {
            "fear_greed": fng,
            "binance_spot_24hr": spot,
            "binance_klines_1d": klines,
            "binance_orderbook_depth": depth,
            "coingecko_markets": coingecko_markets,
            "binance_derivatives": derivatives,
            "coingecko_trending": trending,
            "defillama_chains": defillama,
            "reddit_context": reddit,
        },
        "market_quality": build_market_quality(symbols, spot, klines, depth, coingecko_markets),
        "asset_scores": build_signal_scores(symbols, spot, derivatives, reddit_counts),
        "decision_rule": "multi_source_alpha_score is an input only; it cannot override failed walk-forward gates or paper requirements",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
