#!/usr/bin/env python3
"""
Impulse capture scanner.

Research-only scanner for short-window crypto impulses. It uses Binance public
market data to detect volume expansion, taker-buy pressure, spread/depth, and
cross-asset anchors. It never places live orders and never uses private APIs.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import math
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fast_candidate_funnel import (
    DiscoveryCandidateV1,
    committee_requirement,
    phase_budget_status,
    rank_discovery_candidates,
    historical_path_comparison,
    historical_rank_key,
    ranked_historical_comparison,
)

from risk_adjusted_path_quality import (
    CALCULATION_VERSION as RISK_PATH_CALCULATION_VERSION,
    apply_cross_sectional_adjustments,
    calculate_risk_adjusted_path,
)
from tactical_evidence_ledger import append_observation, build_scanner_observation


ROOT = Path(__file__).resolve().parent.parent
TACTICAL_STRATEGY_VERSION = "impulse-capture-tactical-v4"
BINANCE_PUBLIC_BASES = (
    "https://data-api.binance.vision",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api.binance.com",
)
BINANCE_ENDPOINT_AUDIT = {
    base: {"success_count": 0, "failure_count": 0, "last_error": None}
    for base in BINANCE_PUBLIC_BASES
}
DISCOVERY_AUDIT = {
    "ticker_rows_considered": 0,
    "spot_eligibility_checked": 0,
    "spot_eligibility_rejected": [],
    "crypto_identity_source": "CoinGecko public coin list",
    "crypto_identity_status": "not_checked",
    "crypto_identity_rejected": [],
}
STABLE_BASES = {
    "USDC",
    "FDUSD",
    "TUSD",
    "USDP",
    "USDD",
    "USDE",
    "USDS",
    "USDG",
    "BUSD",
    "USD1",
    "RLUSD",
    "EURI",
    "USTC",
    "PYUSD",
    "DAI",
    "XUSD",
    "SUSDE",
    "BFUSD",
    "JUSDT",
    "USUAL",
    "AEUR",
    "EUR",
    "TRY",
    "BRL",
    "AUD",
    "GBP",
}
LOW_BETA_COMMODITY_BASES = {"PAXG", "XAUT"}
LEVERAGED_TOKEN_MARKERS = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
SECURITY_IDENTITY_MARKERS = (
    "backpack securities",
    "bstocks tokenized stock",
    "ondo tokenized",
    "tokenized stock",
    "tokenized equity",
    "tokenized etf",
    " xstock",
)
KNOWN_CRYPTO_SUFFIX_BASES = {"BNB", "TON"}
DEFAULT_SYMBOLS = ["NIGHTUSDT", "SOLUSDT", "ADAUSDT", "ETHUSDT", "BTCUSDT"]
ANCHORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
DEFAULT_RECOMMENDATION_HISTORY = ROOT.parent / "manual-investment-strategy-operator" / "recommendations" / "recommendation_history.json"
DEFAULT_TOP_CANDIDATES = 3
DEFAULT_MAX_WORKERS = 12
IMPULSE_STAGE_PRIORITY = {
    "trigger": 5,
    "pre_breakout": 4,
    "early_watch": 3,
    "no_current_impulse": 2,
    "fakeout_or_exhaustion": 1,
    "data_missing": 0,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fetch_json(
    path: str,
    params: dict | None = None,
    timeout: float = 8,
    *,
    max_bases: int | None = None,
):
    last_error = None
    bases = BINANCE_PUBLIC_BASES[:max_bases] if max_bases else BINANCE_PUBLIC_BASES
    for base in bases:
        url = f"{base}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Codex research-only scanner)",
                    "Accept-Encoding": "gzip",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                raw_body = response.read()
                if str(response.headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw_body = gzip.decompress(raw_body)
                payload = json.loads(raw_body.decode("utf-8"))
            BINANCE_ENDPOINT_AUDIT[base]["success_count"] += 1
            return payload
        except Exception as exc:  # noqa: BLE001 - try the next official public endpoint.
            last_error = exc
            BINANCE_ENDPOINT_AUDIT[base]["failure_count"] += 1
            BINANCE_ENDPOINT_AUDIT[base]["last_error"] = str(exc)
    raise last_error or RuntimeError("All Binance public endpoints failed")


def safe_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pct_change(start, end):
    if not start:
        return None
    return (end / start - 1.0) * 100.0


def rounded(value, digits=4):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    try:
        if math.isnan(float(value)) or math.isinf(float(value)):
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return value


def signal_ranking_key(row):
    """Keep current impulse state authoritative over slower path-quality bonuses."""

    return (
        IMPULSE_STAGE_PRIORITY.get(str(row.get("stage") or ""), -1),
        *historical_rank_key(row, setup_score_field="impulse_score_points"),
        row.get("volume_multiple_5m_vs_median") or 0,
    )


def summarize_klines(raw):
    rows = []
    for item in raw:
        rows.append(
            {
                "open_time": int(item[0]),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "base_volume": float(item[5]),
                "close_time": int(item[6]),
                "quote_volume": float(item[7]),
                "trade_count": int(item[8]),
                "taker_buy_base": float(item[9]),
                "taker_buy_quote": float(item[10]),
            }
        )
    return rows


def risk_price_rows(raw, captured_at):
    captured_ms = int(captured_at.timestamp() * 1000)
    return [
        {
            "ts": int(item[0]) // 1000,
            "close": float(item[4]),
            "high": float(item[2]),
            "low": float(item[3]),
            "is_closed": int(item[6]) <= captured_ms,
        }
        for item in raw
        if item[4] is not None
    ]


def fetch_risk_adjusted_path(
    symbol,
    *,
    timeout,
    captured_at,
    benchmark_daily_rows,
    benchmark_4h_rows,
    risk_free_rate_pct,
    risk_free_evidence_id,
    request_mode,
):
    daily_raw = fetch_json(
        "/api/v3/klines",
        {"symbol": symbol, "interval": "1d", "limit": 500},
        timeout=timeout,
    )
    four_hour_raw = fetch_json(
        "/api/v3/klines",
        {"symbol": symbol, "interval": "4h", "limit": 500},
        timeout=timeout,
    )
    daily_rows = risk_price_rows(daily_raw, captured_at)
    four_hour_rows = risk_price_rows(four_hour_raw, captured_at)
    path = calculate_risk_adjusted_path(
        daily_rows,
        benchmark_daily_rows,
        asset_class="crypto",
        lane="trend_continuation",
        benchmark="BTCUSDT",
        risk_free_annual_pct=risk_free_rate_pct,
        risk_free_evidence_id=risk_free_evidence_id,
        evidence_ids=(
            f"binance-spot-closed-daily:{symbol}",
            f"binance-spot-closed-4h:{symbol}",
            "binance-spot-closed-daily:BTCUSDT",
            "binance-spot-closed-4h:BTCUSDT",
        ),
        asset_4h_rows=four_hour_rows,
        benchmark_4h_rows=benchmark_4h_rows,
        cutoff_at=captured_at,
    )
    daily_20 = (path.get("windows") or {}).get("daily_20") or {}
    four_hour_42 = (path.get("windows") or {}).get("crypto_4h_42") or {}
    if (
        safe_float(daily_20.get("total_return_pct"), 0) < 0
        and safe_float(four_hour_42.get("total_return_pct"), 0) > 0
    ):
        path["lane"] = "value_repair"
        path["value_catalyst_gate_status"] = "pending"
        path["ranking_adjustment_points"] = 0.0
        path["flags"] = sorted(
            set(
                (path.get("flags") or [])
                + [
                    "value_repair_ranking_blocked_until_value_catalyst_gate_passes",
                    "crypto_4h_improvement_inside_negative_20d_path",
                ]
            )
        )
    comparison_rows = four_hour_rows if request_mode == "intraday_scalp" else daily_rows
    path["historical_comparison"] = historical_path_comparison(
        comparison_rows,
        target_return_pct=3.0 if request_mode == "intraday_scalp" else 10.0,
        stop_loss_pct=1.5 if request_mode == "intraday_scalp" else 5.0,
        horizon_bars=2 if request_mode == "intraday_scalp" else 5,
        friction_pct=0.2,
        evidence_id=f"binance-spot-closed-daily-500:{symbol}",
    )
    return path


def sum_window(rows, minutes):
    window = rows[-minutes:]
    qv = sum(row["quote_volume"] for row in window)
    bv = sum(row["base_volume"] for row in window)
    tbq = sum(row["taker_buy_quote"] for row in window)
    trades = sum(row["trade_count"] for row in window)
    start = window[0]["open"] if window else None
    end = window[-1]["close"] if window else None
    return {
        "quote_volume": qv,
        "taker_buy_quote": tbq,
        "taker_buy_ratio": tbq / qv if qv > 0 else None,
        "trade_count": trades,
        "vwap": qv / bv if bv > 0 else None,
        "return_pct": pct_change(start, end) if start and end else None,
    }


def median_or_none(values):
    clean = [v for v in values if v is not None and v >= 0]
    if not clean:
        return None
    return statistics.median(clean)


def avg_or_none(values):
    clean = [v for v in values if v is not None and v >= 0]
    if not clean:
        return None
    return sum(clean) / len(clean)


def multiple(value, baseline):
    if baseline is None or baseline <= 0:
        return None
    return value / baseline


def orderbook_metrics(depth):
    bids = [(float(price), float(qty)) for price, qty in depth.get("bids", [])]
    asks = [(float(price), float(qty)) for price, qty in depth.get("asks", [])]
    if not bids or not asks:
        return {"data_quality": "missing"}
    best_bid, best_ask = bids[0][0], asks[0][0]
    mid = (best_bid + best_ask) / 2
    spread_bps = (best_ask / best_bid - 1) * 10000 if best_bid else None

    def depth_usd(side, pct):
        if side == "bid":
            floor = mid * (1 - pct)
            levels = [price * qty for price, qty in bids if price >= floor]
        else:
            ceiling = mid * (1 + pct)
            levels = [price * qty for price, qty in asks if price <= ceiling]
        return sum(levels)

    bid_1 = depth_usd("bid", 0.01)
    ask_1 = depth_usd("ask", 0.01)
    bid_2 = depth_usd("bid", 0.02)
    ask_2 = depth_usd("ask", 0.02)
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": spread_bps,
        "depth_1pct_bid_usd": bid_1,
        "depth_1pct_ask_usd": ask_1,
        "depth_2pct_bid_usd": bid_2,
        "depth_2pct_ask_usd": ask_2,
        "depth_1pct_imbalance": (bid_1 - ask_1) / (bid_1 + ask_1) if bid_1 + ask_1 > 0 else None,
        "data_quality": "verified",
    }


def support_resistance(rows, exclude_recent=5):
    prior = rows[:-exclude_recent] if len(rows) > exclude_recent else []
    sample = prior[-60:]
    if not sample:
        return None, None, False
    support = min(row["low"] for row in sample)
    resistance = max(row["high"] for row in sample)
    current = rows[-1]["close"]
    breakout_close = current > resistance
    return support, resistance, breakout_close


def completed_rows(rows, now_ms):
    return [row for row in rows if row["close_time"] <= now_ms]


def latest_completed_five_minute_bucket(rows, now_ms):
    bucket_end = (now_ms // 300_000) * 300_000 - 1
    bucket_start = bucket_end - 299_999
    bucket = [row for row in rows if bucket_start <= row["open_time"] and row["close_time"] <= bucket_end]
    return bucket if len(bucket) == 5 else []


def current_utc_hour_opening_range(rows, captured_at):
    hour_start = captured_at.replace(minute=0, second=0, microsecond=0)
    start_ms = int(hour_start.timestamp() * 1000)
    end_ms = start_ms + 15 * 60_000
    opening = [
        row
        for row in rows
        if start_ms <= row["open_time"] < end_ms
        and row["close_time"] <= int(captured_at.timestamp() * 1000)
    ]
    if not opening:
        opening = rows[-30:-15]
    if not opening:
        return None, None
    return max(row["high"] for row in opening), min(row["low"] for row in opening)


def load_baseline_ids(path, captured_at):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    candidates = {}
    for record in raw.get("recommendations", []):
        if (
            not isinstance(record, dict)
            or record.get("baseline_frozen") is not True
            or record.get("historical_baseline_mutation_forbidden") is not True
            or not record.get("baseline_snapshot_sha256")
        ):
            continue
        symbol = str(record.get("symbol") or "").upper()
        try:
            generated = datetime.fromisoformat(str(record.get("generated_at")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        if generated.astimezone(timezone.utc) > captured_at:
            continue
        previous = candidates.get(symbol)
        if previous is None or generated > previous[0]:
            candidates[symbol] = (generated, record.get("recommendation_id"))
    return {symbol: value[1] for symbol, value in candidates.items()}


def higher_lows(rows):
    if len(rows) < 12:
        return False
    recent = [row["low"] for row in rows[-5:]]
    previous = [row["low"] for row in rows[-10:-5]]
    return min(recent) > min(previous) and recent[-1] >= recent[0]


def classify(metrics):
    vol5 = metrics.get("volume_multiple_5m_vs_median") or 0
    vol1 = metrics.get("volume_multiple_1m_vs_median") or 0
    buy_ratio = metrics.get("taker_buy_ratio_5m") or 0
    spread = metrics.get("spread_bps")
    breakout = metrics.get("breakout_close")
    h_lows = metrics.get("higher_lows")
    anchor = metrics.get("anchor_state")

    if spread is None:
        return "data_missing", "watch", 0, ["missing_spread"]

    reasons = []
    score = 0
    if vol5 >= 12 or vol1 >= 20:
        score += 35
        reasons.append("extreme_short_window_volume")
    elif vol5 >= 8:
        score += 25
        reasons.append("strong_5m_volume")
    elif vol5 >= 3 or vol1 >= 8:
        score += 15
        reasons.append("early_volume_expansion")

    if buy_ratio >= 0.65:
        score += 25
        reasons.append("strong_taker_buy_pressure")
    elif buy_ratio >= 0.62:
        score += 18
        reasons.append("positive_taker_buy_pressure")
    elif buy_ratio >= 0.58:
        score += 10
        reasons.append("early_taker_buy_pressure")
    elif buy_ratio < 0.45 and (vol5 >= 3 or vol1 >= 8):
        return "fakeout_or_exhaustion", "risk_alert", score, ["sell_dominated_volume"]

    if spread <= 20:
        score += 12
        reasons.append("tight_spread")
    elif spread <= 25:
        score += 8
        reasons.append("acceptable_spread")
    else:
        reasons.append("wide_spread")

    if h_lows:
        score += 10
        reasons.append("higher_lows")
    if breakout:
        score += 15
        reasons.append("breakout_close")
    if anchor in {"supportive", "mixed_supportive"}:
        score += 8
        reasons.append("cross_asset_anchor_supportive")
    elif anchor == "risk_off":
        score -= 12
        reasons.append("risk_off_anchor")

    # A confirmed 5m breakout with >=8x volume and >=65% taker buys is already
    # a meaningful observation trigger. It remains research-only; requiring
    # 12x here previously hid fast NIGHT-like breakouts from the alert layer.
    if breakout and (vol5 >= 8 or vol1 >= 20) and buy_ratio >= 0.65 and spread <= 20:
        return "trigger", "conditional_action", score, reasons
    if vol5 >= 8 and buy_ratio >= 0.62 and h_lows and spread <= 25:
        return "pre_breakout", "paper_only", score, reasons
    if (vol5 >= 3 or vol1 >= 8) and buy_ratio >= 0.58 and spread <= 25:
        return "early_watch", "watch", score, reasons
    return "no_current_impulse", "watch", score, reasons


def anchor_state(timeout):
    def fetch_anchor(symbol):
        try:
            raw = fetch_json(
                "/api/v3/klines",
                {"symbol": symbol, "interval": "1h", "limit": 5},
                timeout=min(timeout, 2.0),
                max_bases=2,
            )
            rows = summarize_klines(raw)
            return symbol, pct_change(rows[0]["open"], rows[-1]["close"])
        except Exception:
            return symbol, None

    changes = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ANCHORS)) as executor:
        for symbol, value in executor.map(fetch_anchor, ANCHORS):
            changes[symbol] = value
    clean = [v for v in changes.values() if v is not None]
    if len(clean) < 2:
        state = "missing"
    elif sum(1 for v in clean if v > 0) >= 3:
        state = "supportive"
    elif sum(1 for v in clean if v < -1.0) >= 2:
        state = "risk_off"
    elif sum(1 for v in clean if v > 0) >= 2:
        state = "mixed_supportive"
    else:
        state = "mixed"
    return state, changes


def is_dynamic_scan_candidate(symbol):
    if not symbol.endswith("USDT") or symbol.endswith(LEVERAGED_TOKEN_MARKERS):
        return False
    return symbol[:-4] not in (STABLE_BASES | LOW_BETA_COMMODITY_BASES)


def build_public_crypto_identity(payload):
    symbols = set()
    security_like: dict[str, list[dict]] = {}
    for item in payload:
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        symbols.add(symbol)
        identity_text = f"{item.get('id') or ''} {item.get('name') or ''}".lower()
        if any(marker in identity_text for marker in SECURITY_IDENTITY_MARKERS):
            security_like.setdefault(symbol, []).append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                }
            )
    return {
        "symbols": symbols,
        "security_like": security_like,
    }


def fetch_public_crypto_identity(timeout):
    request = Request(
        "https://api.coingecko.com/api/v3/coins/list?include_platform=false",
        headers={"User-Agent": "Mozilla/5.0 (Codex research-only scanner)"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return build_public_crypto_identity(payload)


def fetch_public_identity_search(symbol, timeout):
    request = Request(
        f"https://api.coingecko.com/api/v3/search?{urlencode({'query': symbol})}",
        headers={"User-Agent": "Mozilla/5.0 (Codex research-only scanner)"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    exact = [
        item
        for item in payload.get("coins") or []
        if str(item.get("symbol") or "").upper() == symbol.upper()
    ]
    security_matches = [
        item
        for item in exact
        if any(
            marker in f"{item.get('id') or ''} {item.get('name') or ''}".lower()
            for marker in SECURITY_IDENTITY_MARKERS
        )
    ]
    non_security_matches = [item for item in exact if item not in security_matches]
    return {
        "symbol": symbol.upper(),
        "security_matches": security_matches,
        "non_security_matches": non_security_matches,
        "is_security_or_ambiguous": bool(security_matches) or not bool(non_security_matches),
    }


def discover_symbols(top_n, timeout, *, identity_crosscheck=False):
    """Select liquid active spot pairs without blocking the fast path on a large identity download.

    The CoinGecko full coin list is useful secondary identity evidence, but its
    payload can stream for far longer than the socket timeout. Discovery uses
    Binance's active-spot contract plus deterministic stable/leveraged-token
    exclusions. The optional public identity cross-check belongs after Top3.
    """

    raw = fetch_json(
        "/api/v3/ticker/24hr",
        {"type": "MINI"},
        timeout=min(timeout, 2.0),
        max_bases=2,
    )
    preselected_rows = []
    for row in raw:
        symbol = str(row.get("symbol") or "")
        if not is_dynamic_scan_candidate(symbol):
            continue
        quote_volume = safe_float(row.get("quoteVolume"), 0) or 0
        preselected_rows.append((symbol, quote_volume))
    preselected_rows.sort(key=lambda item: item[1], reverse=True)
    preselected = [
        symbol
        for symbol, _ in preselected_rows[: max(top_n * 2, top_n + 20)]
    ]
    exchange_rows = (
        fetch_json(
            "/api/v3/exchangeInfo",
            {"symbols": json.dumps(preselected, separators=(",", ":"))},
            timeout=min(timeout, 2.0),
            max_bases=2,
        ).get("symbols")
        or []
    )
    eligible_spot = {
        str(item.get("symbol") or ""): item
        for item in exchange_rows
        if item.get("status") == "TRADING" and item.get("isSpotTradingAllowed") is not False
    }
    suspicious_bases = sorted(
        {
            str(item.get("baseAsset") or "").upper()
            for item in exchange_rows
            if str(item.get("baseAsset") or "").upper().endswith(("B", "ON"))
            and str(item.get("baseAsset") or "").upper() not in KNOWN_CRYPTO_SUFFIX_BASES
        }
    )
    # Binance's tokenized bStocks use an underlying ticker plus a B suffix
    # (for example MUB/SOXLB/EWYB). Fast discovery rejects that convention
    # unless the base is a known native crypto. Any disputed symbol can be
    # re-admitted only after the slower public identity check in validation.
    security_like_searches = {
        base: [
            {
                "symbol": base,
                "reason": "tokenized_security_suffix_requires_post_top3_crypto_proof",
            }
        ]
        for base in suspicious_bases
    }
    confirmed_crypto_symbols = set()
    security_like_symbols = {}
    if identity_crosscheck:
        try:
            crypto_identity = fetch_public_crypto_identity(timeout)
            confirmed_crypto_symbols = crypto_identity["symbols"]
            security_like_symbols = crypto_identity["security_like"]
            DISCOVERY_AUDIT["crypto_identity_status"] = "verified_public_crosscheck"
        except Exception as exc:  # noqa: BLE001
            DISCOVERY_AUDIT["crypto_identity_status"] = f"degraded_unavailable:{exc}"
    else:
        DISCOVERY_AUDIT["crypto_identity_status"] = (
            "tokenized_security_suffix_excluded; full_crosscheck_deferred_until_after_top3"
        )
    items = [(symbol, quote_volume, 0) for symbol, quote_volume in preselected_rows]
    DISCOVERY_AUDIT["ticker_rows_considered"] = len(items)
    selected = []
    for symbol, _, _ in items:
        if len(selected) >= top_n:
            break
        DISCOVERY_AUDIT["spot_eligibility_checked"] += 1
        definition = eligible_spot.get(symbol)
        eligible = definition is not None
        if eligible:
            base_asset = str(definition.get("baseAsset") or symbol[:-4]).upper()
            if base_asset in security_like_searches:
                DISCOVERY_AUDIT["crypto_identity_rejected"].append(
                    {
                        "symbol": symbol,
                        "reason": "security_or_tokenized_equity_identity_not_crypto_candidate",
                        "identity_matches": security_like_searches[base_asset][:5],
                    }
                )
                continue
            if base_asset in security_like_symbols:
                DISCOVERY_AUDIT["crypto_identity_rejected"].append(
                    {
                        "symbol": symbol,
                        "reason": "security_or_tokenized_equity_identity_not_crypto_candidate",
                        "identity_matches": security_like_symbols[base_asset][:5],
                    }
                )
                continue
            if confirmed_crypto_symbols and base_asset not in confirmed_crypto_symbols:
                DISCOVERY_AUDIT["crypto_identity_rejected"].append(
                    {
                        "symbol": symbol,
                        "reason": "not_confirmed_in_public_crypto_asset_identity_list",
                    }
                )
                continue
            selected.append(symbol)
        elif not any(
            item.get("symbol") == symbol
            for item in DISCOVERY_AUDIT["spot_eligibility_rejected"]
        ):
            DISCOVERY_AUDIT["spot_eligibility_rejected"].append(
                {"symbol": symbol, "reason": "not_confirmed_active_spot_pair"}
            )
    return selected


def top_of_book_metrics(book):
    if not isinstance(book, dict):
        return {"data_quality": "missing"}
    best_bid = safe_float(book.get("bidPrice"))
    best_ask = safe_float(book.get("askPrice"))
    bid_qty = safe_float(book.get("bidQty"), 0.0) or 0.0
    ask_qty = safe_float(book.get("askQty"), 0.0) or 0.0
    if not best_bid or not best_ask:
        return {"data_quality": "missing"}
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": (best_bid + best_ask) / 2,
        "spread_bps": (best_ask / best_bid - 1) * 10_000,
        "top_bid_depth_usd": best_bid * bid_qty,
        "top_ask_depth_usd": best_ask * ask_qty,
        "data_quality": "verified_top_of_book",
    }


def analyze_symbol(
    symbol,
    anchor,
    timeout,
    baseline_recommendation_id=None,
    captured_at=None,
    book_snapshot=None,
):
    captured_at = captured_at or utc_now()
    # Ninety rows are sufficient for a 60-minute discovery baseline plus the
    # latest completed 5-minute bucket. Longer history belongs to Top3
    # validation and materially hurts the 30-symbol discovery latency budget.
    raw = fetch_json(
        "/api/v3/klines",
        {"symbol": symbol, "interval": "1m", "limit": 90},
        timeout=min(timeout, 2.0),
        max_bases=2,
    )
    rows = completed_rows(summarize_klines(raw), int(captured_at.timestamp() * 1000))
    if len(rows) < 30:
        return {"symbol": symbol, "data_quality": "missing", "error": "not_enough_klines"}
    five_bucket = latest_completed_five_minute_bucket(rows, int(captured_at.timestamp() * 1000))
    if len(five_bucket) != 5:
        return {"symbol": symbol, "data_quality": "missing", "error": "no_complete_5m_bucket"}
    rows = [row for row in rows if row["close_time"] <= five_bucket[-1]["close_time"]]
    book = top_of_book_metrics(book_snapshot)
    if book.get("data_quality") == "missing":
        depth = fetch_json(
            "/api/v3/depth",
            {"symbol": symbol, "limit": 100},
            timeout=min(timeout, 2.0),
            max_bases=2,
        )
        book = orderbook_metrics(depth)
    current = rows[-1]["close"]
    one = sum_window(rows, 1)
    five = sum_window(five_bucket, 5)
    fifteen = sum_window(rows, 15)
    baseline_rows = rows[-125:-5] if len(rows) >= 130 else rows[:-5]
    baseline_1m_qv = [row["quote_volume"] for row in baseline_rows]
    baseline_1m_trades = [row["trade_count"] for row in baseline_rows]
    median_1m_qv = median_or_none(baseline_1m_qv)
    avg_1m_qv = avg_or_none(baseline_1m_qv)
    median_1m_trades = median_or_none(baseline_1m_trades)
    support, resistance, breakout = support_resistance(rows, exclude_recent=5)
    opening_range_high, opening_range_low = current_utc_hour_opening_range(
        rows,
        captured_at,
    )
    metrics = {
        "symbol": symbol,
        "current_price": current,
        "return_1m_pct": one["return_pct"],
        "return_5m_pct": five["return_pct"],
        "return_15m_pct": fifteen["return_pct"],
        "quote_volume_1m": one["quote_volume"],
        "quote_volume_5m": five["quote_volume"],
        "quote_volume_15m": fifteen["quote_volume"],
        "trade_count_15m": fifteen["trade_count"],
        "vwap_15m": fifteen["vwap"],
        "closed_1m_as_of": datetime.fromtimestamp(
            rows[-1]["close_time"] / 1000,
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z"),
        "closed_5m_as_of": datetime.fromtimestamp(
            five_bucket[-1]["close_time"] / 1000,
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z"),
        "opening_range_definition": "first_15_completed_minutes_of_current_utc_hour_or_prior_15m_fallback",
        "opening_range_high": opening_range_high,
        "opening_range_low": opening_range_low,
        "taker_buy_ratio_1m": one["taker_buy_ratio"],
        "taker_buy_ratio_5m": five["taker_buy_ratio"],
        "taker_buy_ratio_15m": fifteen["taker_buy_ratio"],
        "volume_multiple_1m_vs_median": multiple(one["quote_volume"], median_1m_qv),
        "volume_multiple_1m_vs_average": multiple(one["quote_volume"], avg_1m_qv),
        "volume_multiple_5m_vs_median": multiple(five["quote_volume"], (median_1m_qv or 0) * 5),
        "trade_count_multiple_1m_vs_median": multiple(one["trade_count"], median_1m_trades),
        "support": support,
        "resistance": resistance,
        "breakout_close": breakout,
        "higher_lows": higher_lows(rows),
        "anchor_state": anchor["state"],
        "anchor_changes_pct": anchor["changes"],
        "data_quality": "verified",
    }
    metrics.update(book)
    stage, action, score, reasons = classify(metrics)
    metrics.update(
        {
            "impulse_id": f"{datetime.fromtimestamp(rows[-1]['close_time'] / 1000, tz=timezone.utc).strftime('%Y%m%d%H%M')}-{symbol}",
            "stage": stage,
            "recommended_max_action": action,
            "execution_action": "no_deploy",
            "observation_action": action,
            "observation_trigger": "closed 5m breakout above declared resistance with volume/taker-buy/spread confirmation",
            "baseline_recommendation_id": baseline_recommendation_id,
            "candidate_event_coverage_status": "market_only_degraded",
            "official_event_status": "not_checked_by_market_scanner",
            "requires_event_relay": stage not in {"data_missing", "no_current_impulse"},
            "triggered_at": (
                datetime.fromtimestamp(rows[-1]["close_time"] / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
                if stage == "trigger"
                else None
            ),
            "historical_baseline_mutation_allowed": False,
            "impulse_score_points": max(0, min(100, score)),
            "why_now": reasons,
            "support_zone": [support * 0.995, support * 1.005] if support else None,
            "breakout_level": resistance,
            "invalidation_level": support * 0.995 if support else None,
            "what_would_invalidate": [
                "price_loses_support_or_breakout_level",
                "taker_buy_ratio_5m_below_0.45_on_high_volume",
                "spread_or_depth_becomes_untradable",
                "btc_eth_anchor_turns_risk_off",
            ],
        }
    )
    return {key: rounded(value) for key, value in metrics.items()}


def build_crypto_intraday_market_plan(signal, captured_at):
    current_price = safe_float(signal.get("mid"), safe_float(signal.get("current_price")))
    resistance = safe_float(signal.get("resistance"), current_price)
    opening_high = safe_float(signal.get("opening_range_high"), resistance)
    opening_low = safe_float(signal.get("opening_range_low"), safe_float(signal.get("support")))
    support = safe_float(signal.get("support"), opening_low)
    trigger = max(value for value in (current_price, resistance, opening_high) if value is not None)
    stop_candidates = [value for value in (support, opening_low) if value is not None and value < trigger]
    stop = min(stop_candidates) * 0.995 if stop_candidates else trigger * 0.98
    unit_risk = trigger - stop
    latest_close = captured_at.replace(hour=23, minute=55, second=0, microsecond=0)
    plan = {
        "schema_version": "intraday-market-plan-v1",
        "quote_as_of": captured_at.isoformat().replace("+00:00", "Z"),
        "closed_1m_as_of": signal.get("closed_1m_as_of"),
        "closed_5m_as_of": signal.get("closed_5m_as_of"),
        "decision_valid_until": (
            captured_at + timedelta(minutes=5)
        ).isoformat().replace("+00:00", "Z"),
        "allowed_session": "crypto_24x7_closed_bar_only",
        "vwap": signal.get("vwap_15m"),
        "opening_range_definition": signal.get("opening_range_definition"),
        "opening_range_high": opening_high,
        "opening_range_low": opening_low,
        "relative_volume": signal.get("volume_multiple_5m_vs_median"),
        "spread_bps": signal.get("spread_bps"),
        "depth_bid_usd": signal.get("depth_1pct_bid_usd") or signal.get("top_bid_depth_usd"),
        "depth_ask_usd": signal.get("depth_1pct_ask_usd") or signal.get("top_ask_depth_usd"),
        "market_anchor": signal.get("anchor_state"),
        "entry_trigger_price": rounded(trigger),
        "entry_trigger": "closed 1m above trigger with 5m relative-volume and taker-buy confirmation",
        "cancel_if": "price loses VWAP/opening-range low, spread exceeds 20 bps, or BTC/ETH anchor turns risk_off",
        "price_stop": rounded(stop),
        "target_1_price": rounded(trigger + 2 * unit_risk),
        "target_2_price": rounded(trigger + 3 * unit_risk),
        "latest_close_at": latest_close.isoformat().replace("+00:00", "Z"),
        "overnight_allowed": False,
        "binary_event_inside_window": False,
        "max_loss_budget": None,
        "max_account_risk_pct": None,
        "planned_position_size": None,
        "position_size_formula": "verified max_loss_budget / abs(entry_trigger_price - price_stop)",
        "account_fields_status": "pending_manual_account_state",
        "realtime_signal_complete": signal.get("stage") in {"trigger", "pre_breakout"},
        "live_orders_enabled": False,
    }
    return plan


def build_top1_decision_card(
    signal,
    *,
    captured_at,
    request_mode,
    intraday_market_plan=None,
):
    """Finish the Top1 research card even when missing evidence blocks entry.

    A complete card is not the same as an entry approval.  The card must make
    the current decision, execution state, and remaining evidence gaps explicit
    so a missing committee or account snapshot cannot leave the funnel pending.
    """

    committee = committee_requirement(
        phase="validation",
        request_mode=request_mode,
    )
    missing_evidence = [
        "mode_appropriate_committee_outputs_missing",
        "official_event_or_protocol_fundamental_review_missing",
        "manual_account_state_missing",
        "calibrated_target_before_stop_probability_missing",
    ]
    return {
        "schema_version": "top1-decision-card-v1",
        "completed_at": captured_at.isoformat().replace("+00:00", "Z"),
        "completion_status": "complete_with_explicit_blockers",
        "best_candidate": signal.get("symbol"),
        "research_decision": "watch",
        "current_direct_decision": "do_not_enter_now",
        "account_state": {
            "status": "pending_manual_account_state",
            "deployable_cash": None,
        },
        "execution_decision": "no_deploy_evidence",
        "executable_amount": 0.0,
        "selection_reason": (
            "highest post-validation risk-adjusted setup quality among discovery Top3"
        ),
        "setup_quality_score": signal.get("impulse_score_points"),
        "signal_stage": signal.get("stage"),
        "committee_requirement": committee,
        "committee_status": "degraded_missing_required_outputs",
        "fundamental_event_review_status": "missing",
        "probability_status": "not_estimated_without_valid_sample",
        "missing_evidence": missing_evidence,
        "market_plan": intraday_market_plan,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def to_discovery_candidate(signal, captured_at):
    reasons = tuple(str(item) for item in (signal.get("why_now") or ()))
    hard_rejections = []
    if not str(signal.get("data_quality") or "").startswith("verified"):
        hard_rejections.append(str(signal.get("error") or "market_data_incomplete"))
    if signal.get("spread_bps") is None:
        hard_rejections.append("spread_missing")
    score = safe_float(signal.get("impulse_score_points"), 0.0) or 0.0
    return DiscoveryCandidateV1(
        symbol=str(signal.get("symbol") or ""),
        asset_class="crypto",
        market_time=captured_at.isoformat().replace("+00:00", "Z"),
        current_price=safe_float(signal.get("current_price"), 0.0) or 0.0,
        turnover=safe_float(signal.get("quote_volume_15m"), 0.0) or 0.0,
        relative_volume=safe_float(signal.get("volume_multiple_5m_vs_median")),
        trade_count=int(signal.get("trade_count_15m") or 0),
        vwap=safe_float(signal.get("vwap_15m")),
        spread_bps=safe_float(signal.get("spread_bps")),
        depth_bid_usd=safe_float(
            signal.get("depth_1pct_bid_usd"),
            safe_float(signal.get("top_bid_depth_usd")),
        ),
        depth_ask_usd=safe_float(
            signal.get("depth_1pct_ask_usd"),
            safe_float(signal.get("top_ask_depth_usd")),
        ),
        anchor_state={
            "market": signal.get("anchor_state"),
            "BTC": (signal.get("anchor_changes_pct") or {}).get("BTCUSDT"),
            "ETH": (signal.get("anchor_changes_pct") or {}).get("ETHUSDT"),
        },
        confirmed_catalyst_present=None,
        discovery_score=max(0.0, min(100.0, score)),
        ranking_reason=reasons or ("liquidity_and_short_window_market_activity",),
        hard_rejection_reasons=tuple(hard_rejections),
        return_1m_pct=safe_float(signal.get("return_1m_pct")),
        return_5m_pct=safe_float(signal.get("return_5m_pct")),
        return_15m_pct=safe_float(signal.get("return_15m_pct")),
        data_sources=("Binance public spot klines", "Binance public spot top-of-book"),
        data_quality_status=str(signal.get("data_quality") or "missing"),
    )


def write_outputs(result, output_dir):
    output_dir = Path(output_dir)
    reports = output_dir / "reports"
    experiments = output_dir / "experiments"
    reports.mkdir(parents=True, exist_ok=True)
    experiments.mkdir(parents=True, exist_ok=True)
    now = datetime.fromisoformat(result["captured_at"].replace("Z", "+00:00"))
    stamp = now.strftime("%Y%m%d-%H%M%S")
    report_path = reports / f"{now.strftime('%Y-%m-%d')}-impulse-capture-{now.strftime('%H%M%S')}.md"
    experiment_path = experiments / f"{stamp}-impulse-capture.json"
    experiment_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# Impulse Capture Report {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Research-only. No live orders, no private API, manual confirmation required.",
        "",
        f"- Anchor state: `{result['anchor']['state']}`",
        f"- Symbols scanned: {len(result['signals'])}",
        "",
        "| Symbol | Stage | Action ceiling | Price | 5m ret | 5m vol x median | Buy ratio 5m | Spread bps | Sharpe 20/60 | IR60 | MDD60 | Path | Adj | Score |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for row in result["signals"]:
        risk = row.get("risk_adjusted_path") or {}
        windows = risk.get("windows") or {}
        daily20 = windows.get("daily_20") or {}
        daily60 = windows.get("daily_60") or {}
        lines.append(
            "| {symbol} | {stage} | {action} | {price} | {ret5} | {vol5} | {buy5} | {spread} | "
            "{sharpe20}/{sharpe60} | {ir60} | {mdd60}% | {path} | {adjustment} | {score} |".format(
                symbol=row.get("symbol"),
                stage=row.get("stage"),
                action=row.get("recommended_max_action"),
                price=row.get("current_price"),
                ret5=row.get("return_5m_pct"),
                vol5=row.get("volume_multiple_5m_vs_median"),
                buy5=row.get("taker_buy_ratio_5m"),
                spread=row.get("spread_bps"),
                sharpe20=daily20.get("sharpe"),
                sharpe60=daily60.get("sharpe"),
                ir60=daily60.get("information_ratio"),
                mdd60=daily60.get("max_drawdown_pct"),
                path=risk.get("persistence_label"),
                adjustment=risk.get("ranking_adjustment_points"),
                score=row.get("impulse_score_points"),
            )
        )
    lines.extend(
        [
            "",
            "Terminology:",
            "- taker buy ratio: share of volume that looks like urgent buyers taking offers.",
            "- depth: executable liquidity near the current price; thin depth can amplify moves.",
            "- action ceiling: the maximum action this scanner can recommend before manual review.",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path, experiment_path


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Research-only impulse capture scanner.")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma-separated Binance spot symbols.")
    parser.add_argument("--dynamic-top", type=int, default=0, help="Add top N USDT spot symbols by quote volume.")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_CANDIDATES)
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS)
    parser.add_argument(
        "--request-mode",
        choices=("intraday_scalp", "tactical_1_7d"),
        default="intraday_scalp",
    )
    parser.add_argument("--discovery-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--output-dir", default=str(ROOT))
    parser.add_argument(
        "--observation-ledger",
        help="Append-only tactical_1_7d ObservationSampleV1 ledger; defaults under output-dir/runtime",
    )
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--recommendation-history", default=str(DEFAULT_RECOMMENDATION_HISTORY))
    parser.add_argument("--risk-free-rate-pct", type=float, default=0.0)
    parser.add_argument("--risk-free-evidence-id")
    return parser.parse_args(argv)


def run_self_test():
    night_like = {
        "volume_multiple_5m_vs_median": 9.04,
        "volume_multiple_1m_vs_median": 4.0,
        "taker_buy_ratio_5m": 0.6656,
        "spread_bps": 12.0,
        "breakout_close": True,
        "higher_lows": False,
        "anchor_state": "mixed",
    }
    fakeout = dict(night_like, taker_buy_ratio_5m=0.42)
    fixture_rows = [
        {"open_time": minute * 60_000, "close_time": (minute + 1) * 60_000 - 1}
        for minute in range(10)
    ]
    complete_bucket = latest_completed_five_minute_bucket(fixture_rows, 10 * 60_000)
    incomplete_bucket = latest_completed_five_minute_bucket(fixture_rows, 9 * 60_000 + 30_000)
    trigger_stage, trigger_action, _, _ = classify(night_like)
    fakeout_stage, fakeout_action, _, _ = classify(fakeout)
    identity_fixture = build_public_crypto_identity(
        [
            {"id": "aave", "symbol": "aave", "name": "Aave"},
            {
                "id": "direxion-daily-semiconductor-bull-3x-etf-backpack-securities",
                "symbol": "soxl",
                "name": "Direxion Daily Semiconductor Bull 3X ETF (Backpack Securities)",
            },
        ]
    )
    performance_fixture = [
        DiscoveryCandidateV1(
            symbol=f"ASSET{index}USDT",
            asset_class="crypto",
            market_time="2026-08-01T00:00:00Z",
            current_price=1.0 + index,
            turnover=1_000_000 + index,
            relative_volume=1.0 + index / 10,
            trade_count=10_000 + index,
            vwap=1.0 + index,
            spread_bps=5.0,
            depth_bid_usd=100_000,
            depth_ask_usd=100_000,
            anchor_state={"market": "supportive", "BTC": 0.2, "ETH": 0.3},
            confirmed_catalyst_present=False,
            discovery_score=float(index),
            ranking_reason=("fixture_momentum",),
            return_1m_pct=0.1,
            return_5m_pct=0.2,
            return_15m_pct=0.3,
        )
        for index in range(30)
    ]
    fixture_started = time.monotonic()
    ranked_fixture = rank_discovery_candidates(performance_fixture, top_n=3)
    fixture_elapsed = time.monotonic() - fixture_started
    checks = {
        "night_like_9x_breakout_is_visible": trigger_stage == "trigger",
        "night_like_alert_ceiling_is_conditional": trigger_action == "conditional_action",
        "sell_dominated_volume_is_risk_alert": fakeout_stage == "fakeout_or_exhaustion" and fakeout_action == "risk_alert",
        "only_complete_5m_bucket_is_used": (
            len(complete_bucket) == 5
            and len(incomplete_bucket) == 5
            and complete_bucket[-1]["close_time"] < 10 * 60_000
            and incomplete_bucket[-1]["close_time"] < 5 * 60_000
        ),
        "stablecoin_pairs_are_excluded_from_dynamic_discovery": all(
            not is_dynamic_scan_candidate(symbol)
            for symbol in ("RLUSDUSDT", "USDEUSDT", "PYUSDUSDT", "EURUSDT")
        ),
        "low_beta_gold_tokens_are_excluded_from_fast_alpha": all(
            not is_dynamic_scan_candidate(symbol)
            for symbol in ("PAXGUSDT", "XAUTUSDT")
        ),
        "leveraged_tokens_are_excluded_from_dynamic_discovery": all(
            not is_dynamic_scan_candidate(symbol)
            for symbol in ("BTCUPUSDT", "ETHDOWNUSDT", "SOLBULLUSDT", "ADABEARUSDT")
        ),
        "ordinary_spot_pairs_remain_eligible": all(
            is_dynamic_scan_candidate(symbol)
            for symbol in ("BTCUSDT", "AAVEUSDT", "NIGHTUSDT")
        ),
        "tokenized_security_identity_is_separated_from_crypto": (
            "SOXL" in identity_fixture["security_like"]
            and "AAVE" not in identity_fixture["security_like"]
        ),
        "thirty_candidate_top3_is_under_15_seconds": (
            len(ranked_fixture["top_candidates"]) == 3
            and fixture_elapsed <= 15.0
        ),
        "discovery_has_no_committee": (
            ranked_fixture["committee_tier"]["required_role_count"] == 0
        ),
        "intraday_uses_two_role_committee_after_discovery": (
            committee_requirement(
                phase="validation", request_mode="intraday_scalp"
            )["required_role_count"]
            == 2
        ),
    }
    return {
        "status": "ok" if all(checks.values()) else "failed",
        "checks": checks,
        "live_orders_enabled": False,
        "historical_baseline_mutation_allowed": False,
    }


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    if args.self_test:
        result = run_self_test()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ok" else 1
    run_started = time.monotonic()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    bootstrap_errors = []
    book_by_symbol = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        anchor_future = executor.submit(anchor_state, args.timeout)
        discovery_future = (
            executor.submit(discover_symbols, args.dynamic_top, args.timeout)
            if args.dynamic_top > 0
            else None
        )
        if discovery_future is not None:
            try:
                symbols.extend(discovery_future.result())
            except Exception as exc:  # noqa: BLE001
                bootstrap_errors.append({"source": "dynamic_discovery", "error": str(exc)})
        try:
            anchor_state_value, anchor_changes = anchor_future.result()
        except Exception as exc:  # noqa: BLE001
            bootstrap_errors.append({"source": "anchor", "error": str(exc)})
            anchor_state_value, anchor_changes = "missing", {}
    symbols = list(dict.fromkeys(symbols))
    try:
        raw_books = fetch_json(
            "/api/v3/ticker/bookTicker",
            {"symbols": json.dumps(symbols, separators=(",", ":"))},
            timeout=min(args.timeout, 2.0),
            max_bases=2,
        )
        if isinstance(raw_books, dict):
            raw_books = [raw_books]
        book_by_symbol = {
            str(item.get("symbol") or ""): item
            for item in raw_books
            if isinstance(item, dict) and item.get("symbol")
        }
    except Exception as exc:  # noqa: BLE001
        bootstrap_errors.append({"source": "selected_top_of_book", "error": str(exc)})
    captured = utc_now()
    baseline_ids = load_baseline_ids(args.recommendation_history, captured)
    anchor = {"state": anchor_state_value, "changes": {key: rounded(value) for key, value in anchor_changes.items()}}
    signals = []
    errors = list(bootstrap_errors)

    def scan_symbol(symbol):
        try:
            return analyze_symbol(
                symbol,
                anchor,
                args.timeout,
                baseline_ids.get(symbol),
                captured,
                book_by_symbol.get(symbol),
            )
        except Exception as exc:  # noqa: BLE001 - scanner should continue and record failures.
            return {"symbol": symbol, "_scan_error": str(exc)}

    workers = max(1, min(args.max_workers, len(symbols) or 1))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    futures = {executor.submit(scan_symbol, symbol): symbol for symbol in symbols}
    discovery_deadline = run_started + 14.5
    try:
        remaining = max(0.05, discovery_deadline - time.monotonic())
        for future in concurrent.futures.as_completed(futures, timeout=remaining):
            signal = future.result()
            if signal.get("_scan_error"):
                errors.append({"symbol": signal["symbol"], "error": signal["_scan_error"]})
            else:
                signals.append(signal)
    except concurrent.futures.TimeoutError:
        unfinished = [
            symbol for future, symbol in futures.items() if not future.done()
        ]
        errors.append(
            {
                "source": "discovery_deadline",
                "error": "partial_results_used_at_14_5_second_deadline",
                "unfinished_symbols": unfinished,
            }
        )
    finally:
        for future in futures:
            if not future.done():
                future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    discovery = rank_discovery_candidates(
        (to_discovery_candidate(signal, captured) for signal in signals),
        top_n=max(1, args.top),
    )
    discovery_elapsed = time.monotonic() - run_started
    discovery["elapsed_seconds"] = round(discovery_elapsed, 6)
    discovery["within_budget"] = discovery_elapsed <= discovery["budget_seconds"]
    top_symbols = [item["symbol"] for item in discovery["top_candidates"]]
    if args.discovery_only:
        print(json.dumps(discovery, ensure_ascii=False, indent=2))
        return 0

    validation_started = time.monotonic()
    benchmark_daily_rows = []
    benchmark_4h_rows = []
    try:
        benchmark_daily_rows = risk_price_rows(
            fetch_json(
                "/api/v3/klines",
                {"symbol": "BTCUSDT", "interval": "1d", "limit": 500},
                timeout=args.timeout,
            ),
            captured,
        )
        benchmark_4h_rows = risk_price_rows(
            fetch_json(
                "/api/v3/klines",
                {"symbol": "BTCUSDT", "interval": "4h", "limit": 500},
                timeout=args.timeout,
            ),
            captured,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append({"symbol": "BTCUSDT", "error": f"risk_benchmark:{exc}"})

    signal_by_symbol = {str(item.get("symbol")): item for item in signals}
    def enrich_top3_depth(symbol):
        try:
            return symbol, orderbook_metrics(
                fetch_json(
                    "/api/v3/depth",
                    {"symbol": symbol, "limit": 100},
                    timeout=args.timeout,
                )
            ), None
        except Exception as exc:  # noqa: BLE001
            return symbol, None, str(exc)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(len(top_symbols), 3))
    ) as executor:
        for symbol, depth_metrics, depth_error in executor.map(
            enrich_top3_depth,
            top_symbols,
        ):
            signal = signal_by_symbol.get(symbol)
            if signal is None:
                continue
            if depth_metrics:
                signal.update({key: rounded(value) for key, value in depth_metrics.items()})
            elif depth_error:
                signal["full_depth_error"] = depth_error
    for symbol in top_symbols:
        signal = signal_by_symbol.get(symbol)
        if signal is None:
            continue
        try:
            signal["risk_adjusted_path"] = fetch_risk_adjusted_path(
                symbol,
                timeout=args.timeout,
                captured_at=captured,
                benchmark_daily_rows=benchmark_daily_rows,
                benchmark_4h_rows=benchmark_4h_rows,
                risk_free_rate_pct=args.risk_free_rate_pct,
                risk_free_evidence_id=args.risk_free_evidence_id,
                request_mode=args.request_mode,
            )
            signal["historical_comparison"] = (
                signal["risk_adjusted_path"].get("historical_comparison") or {}
            )
        except Exception as risk_exc:  # noqa: BLE001
            signal["risk_adjusted_path_error"] = str(risk_exc)
    validation_timing = phase_budget_status(validation_started, "validation")
    top_signals = [signal_by_symbol[symbol] for symbol in top_symbols if symbol in signal_by_symbol]
    apply_cross_sectional_adjustments(
        top_signals,
        base_score_field="impulse_score_points",
    )
    top_signals.sort(key=signal_ranking_key, reverse=True)
    validated_top1_signal = top_signals[0] if top_signals else None
    validated_top1 = None
    intraday_market_plan = None
    top1_decision_card = None
    if validated_top1_signal is not None:
        validated_top1 = {
            "symbol": validated_top1_signal.get("symbol"),
            "setup_quality_score": validated_top1_signal.get("impulse_score_points"),
            "stage": validated_top1_signal.get("stage"),
            "reward_risk_status": "pending_manual_target_stop_probability_event",
            "research_decision": "watch",
            "current_direct_decision": "do_not_enter_now",
            "selection_reason": "highest post-validation risk-adjusted setup quality among discovery Top3",
            "live_orders_enabled": False,
        }
        if args.request_mode == "intraday_scalp":
            intraday_market_plan = build_crypto_intraday_market_plan(
                validated_top1_signal,
                captured,
            )
        top1_decision_card = build_top1_decision_card(
            validated_top1_signal,
            captured_at=captured,
            request_mode=args.request_mode,
            intraday_market_plan=intraday_market_plan,
        )
    top1_card_timing = phase_budget_status(run_started, "deep_research")
    top1_card_timing["phase"] = "top1_card"
    signals.sort(key=signal_ranking_key, reverse=True)
    config_digest = hashlib.sha256(json.dumps({
        "request_mode": args.request_mode,
        "dynamic_top": args.dynamic_top,
        "top": args.top,
        "symbols": args.symbols,
        "risk_free_rate_pct": args.risk_free_rate_pct,
        "historical_target_pct": 10.0 if args.request_mode == "tactical_1_7d" else 3.0,
        "historical_stop_pct": 5.0 if args.request_mode == "tactical_1_7d" else 1.5,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    ledger_source = Path(__file__).with_name("tactical_evidence_ledger.py")
    source_digest = hashlib.sha256(
        Path(__file__).read_bytes() + ledger_source.read_bytes()
    ).hexdigest()
    snapshot_id = "snapshot-" + hashlib.sha256(json.dumps({
        "captured_at": captured.isoformat(),
        "request_mode": args.request_mode,
        "candidates": [
            {"symbol": item.get("symbol"), "price": item.get("current_price")}
            for item in top_signals[:3]
        ],
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]
    result = {
        "run_type": "impulse_capture_scan",
        "request_mode": args.request_mode,
        "snapshot_id": snapshot_id,
        "strategy_version": TACTICAL_STRATEGY_VERSION,
        "config_digest": config_digest,
        "source_digest": source_digest,
        "captured_at": captured.isoformat().replace("+00:00", "Z"),
        "live_orders_enabled": False,
        "private_api_used": False,
        "anchor": anchor,
        "signals": signals,
        "discovery_top3": discovery["top_candidates"],
        "discovery_blocked_candidates": discovery["blocked_candidates"],
        "deep_risk_candidate_symbols": top_symbols,
        "validated_top1": validated_top1,
        "ranked_historical_comparison": ranked_historical_comparison(
            [
                {
                    **item,
                    "setup_quality_score": item.get("impulse_score_points"),
                }
                for item in top_signals[:3]
            ]
        ),
        "deep_research_candidate_symbol": (
            validated_top1.get("symbol") if validated_top1 else None
        ),
        "deep_research_status": (
            "complete_top1_decision_card_with_explicit_evidence_gaps"
            if top1_decision_card
            else "no_validated_top1"
        ),
        "top1_decision_card": top1_decision_card,
        "intraday_market_plan": intraday_market_plan,
        "stage_timings": {
            "discovery": {
                "elapsed_seconds": discovery["elapsed_seconds"],
                "budget_seconds": discovery["budget_seconds"],
                "within_budget": discovery["within_budget"],
            },
            "validation": validation_timing,
            "top1_card": top1_card_timing,
            "end_to_end": phase_budget_status(run_started, "end_to_end"),
        },
        "committee_requirement": committee_requirement(
            phase="validation",
            request_mode=args.request_mode,
        ),
        "research_committee_degraded": True,
        "research_panel_missing": True,
        "errors": errors,
        "data_sources": ["Binance public spot klines", "Binance public spot order book"],
        "public_endpoint_audit": BINANCE_ENDPOINT_AUDIT,
        "dynamic_discovery_audit": DISCOVERY_AUDIT,
        "risk_adjusted_path_policy": {
            "calculation_version": RISK_PATH_CALCULATION_VERSION,
            "promotion_status": "research_only_paper_only",
            "risk_free_rate_pct": args.risk_free_rate_pct,
            "risk_free_evidence_id": args.risk_free_evidence_id,
            "hard_sharpe_gt_3_gate": False,
            "live_gate_effect": "none_until_promotion",
        },
        "max_allowed_action": "watch",
    }
    if args.request_mode == "tactical_1_7d":
        result["observation_samples"] = [
            build_scanner_observation(
                signal,
                rank=rank,
                snapshot_id=snapshot_id,
                strategy_version=TACTICAL_STRATEGY_VERSION,
                config_digest=config_digest,
                source_digest=source_digest,
                observed_at=result["captured_at"],
                committee_degraded=result["research_committee_degraded"],
            )
            for rank, signal in enumerate(top_signals[:3], start=1)
        ]
        if not args.no_write:
            observation_ledger = Path(args.observation_ledger) if args.observation_ledger else (
                Path(args.output_dir) / "runtime" / "tactical_1_7d_observations.jsonl"
            )
            result["observation_ledger"] = str(observation_ledger.resolve())
            result["observation_append_results"] = [
                {
                    "observation_id": item["observation_id"],
                    "status": append_observation(observation_ledger, item),
                }
                for item in result["observation_samples"]
            ]
    if not args.no_write:
        report_path, experiment_path = write_outputs(result, args.output_dir)
        result["report_path"] = str(report_path)
        result["experiment_path"] = str(experiment_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
