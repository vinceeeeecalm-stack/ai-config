#!/usr/bin/env python3
"""
Impulse capture scanner.

Research-only scanner for short-window crypto impulses. It uses Binance public
market data to detect volume expansion, taker-buy pressure, spread/depth, and
cross-asset anchors. It never places live orders and never uses private APIs.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from risk_adjusted_path_quality import (
    CALCULATION_VERSION as RISK_PATH_CALCULATION_VERSION,
    apply_cross_sectional_adjustments,
    calculate_risk_adjusted_path,
)


ROOT = Path(__file__).resolve().parent.parent
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
LEVERAGED_TOKEN_MARKERS = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
DEFAULT_SYMBOLS = ["NIGHTUSDT", "SOLUSDT", "ADAUSDT", "ETHUSDT", "BTCUSDT"]
ANCHORS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
DEFAULT_RECOMMENDATION_HISTORY = ROOT.parent / "manual-investment-strategy-operator" / "recommendations" / "recommendation_history.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fetch_json(path: str, params: dict | None = None, timeout: int = 8):
    last_error = None
    for base in BINANCE_PUBLIC_BASES:
        url = f"{base}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        try:
            request = Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Codex research-only scanner)"},
            )
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
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
):
    daily_raw = fetch_json(
        "/api/v3/klines",
        {"symbol": symbol, "interval": "1d", "limit": 140},
        timeout=timeout,
    )
    four_hour_raw = fetch_json(
        "/api/v3/klines",
        {"symbol": symbol, "interval": "4h", "limit": 140},
        timeout=timeout,
    )
    path = calculate_risk_adjusted_path(
        risk_price_rows(daily_raw, captured_at),
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
        asset_4h_rows=risk_price_rows(four_hour_raw, captured_at),
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
    return path


def sum_window(rows, minutes):
    window = rows[-minutes:]
    qv = sum(row["quote_volume"] for row in window)
    tbq = sum(row["taker_buy_quote"] for row in window)
    trades = sum(row["trade_count"] for row in window)
    start = window[0]["open"] if window else None
    end = window[-1]["close"] if window else None
    return {
        "quote_volume": qv,
        "taker_buy_quote": tbq,
        "taker_buy_ratio": tbq / qv if qv > 0 else None,
        "trade_count": trades,
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
    changes = {}
    for symbol in ANCHORS:
        try:
            raw = fetch_json("/api/v3/klines", {"symbol": symbol, "interval": "1h", "limit": 5}, timeout=timeout)
            rows = summarize_klines(raw)
            changes[symbol] = pct_change(rows[0]["open"], rows[-1]["close"])
        except Exception:
            changes[symbol] = None
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
    return symbol[:-4] not in STABLE_BASES


def build_public_crypto_identity(payload):
    symbols = set()
    security_like: dict[str, list[dict]] = {}
    security_markers = (
        "backpack securities",
        "robinhood token",
        "tokenized stock",
        "tokenized equity",
        " xstock",
        " etf",
        " fund",
    )
    for item in payload:
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        symbols.add(symbol)
        identity_text = f"{item.get('id') or ''} {item.get('name') or ''}".lower()
        if any(marker in identity_text for marker in security_markers):
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


def discover_symbols(top_n, timeout):
    raw = fetch_json("/api/v3/ticker/24hr", timeout=timeout)
    try:
        crypto_identity = fetch_public_crypto_identity(timeout)
        confirmed_crypto_symbols = crypto_identity["symbols"]
        security_like_symbols = crypto_identity["security_like"]
        DISCOVERY_AUDIT["crypto_identity_status"] = "verified_public_crosscheck"
    except Exception as exc:  # noqa: BLE001
        confirmed_crypto_symbols = set()
        security_like_symbols = {}
        DISCOVERY_AUDIT["crypto_identity_status"] = f"degraded_unavailable:{exc}"
    items = []
    for row in raw:
        symbol = row.get("symbol", "")
        if not is_dynamic_scan_candidate(symbol):
            continue
        quote_volume = safe_float(row.get("quoteVolume"), 0) or 0
        trade_count = int(row.get("count") or 0)
        items.append((symbol, quote_volume, trade_count))
    items.sort(key=lambda item: (item[1], item[2]), reverse=True)
    DISCOVERY_AUDIT["ticker_rows_considered"] = len(items)
    selected = []
    for symbol, _, _ in items:
        if len(selected) >= top_n:
            break
        DISCOVERY_AUDIT["spot_eligibility_checked"] += 1
        try:
            exchange_info = fetch_json(
                "/api/v3/exchangeInfo",
                {"symbol": symbol},
                timeout=timeout,
            )
            definition = (exchange_info.get("symbols") or [None])[0]
            eligible = bool(
                definition
                and definition.get("status") == "TRADING"
                and definition.get("isSpotTradingAllowed") is not False
            )
        except Exception as exc:  # noqa: BLE001
            eligible = False
            DISCOVERY_AUDIT["spot_eligibility_rejected"].append(
                {"symbol": symbol, "reason": f"exchange_info_failed:{exc}"}
            )
        if eligible:
            base_asset = str(definition.get("baseAsset") or symbol[:-4]).upper()
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


def analyze_symbol(symbol, anchor, timeout, baseline_recommendation_id=None, captured_at=None):
    captured_at = captured_at or utc_now()
    raw = fetch_json("/api/v3/klines", {"symbol": symbol, "interval": "1m", "limit": 180}, timeout=timeout)
    rows = completed_rows(summarize_klines(raw), int(captured_at.timestamp() * 1000))
    if len(rows) < 30:
        return {"symbol": symbol, "data_quality": "missing", "error": "not_enough_klines"}
    five_bucket = latest_completed_five_minute_bucket(rows, int(captured_at.timestamp() * 1000))
    if len(five_bucket) != 5:
        return {"symbol": symbol, "data_quality": "missing", "error": "no_complete_5m_bucket"}
    rows = [row for row in rows if row["close_time"] <= five_bucket[-1]["close_time"]]
    depth = fetch_json("/api/v3/depth", {"symbol": symbol, "limit": 100}, timeout=timeout)
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
    metrics = {
        "symbol": symbol,
        "current_price": current,
        "return_1m_pct": one["return_pct"],
        "return_5m_pct": five["return_pct"],
        "return_15m_pct": fifteen["return_pct"],
        "quote_volume_1m": one["quote_volume"],
        "quote_volume_5m": five["quote_volume"],
        "quote_volume_15m": fifteen["quote_volume"],
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
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--output-dir", default=str(ROOT))
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
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    if args.dynamic_top > 0:
        try:
            symbols.extend(discover_symbols(args.dynamic_top, args.timeout))
        except (URLError, TimeoutError, OSError) as exc:
            print(f"dynamic discovery failed: {exc}", file=sys.stderr)
    symbols = list(dict.fromkeys(symbols))
    captured = utc_now()
    baseline_ids = load_baseline_ids(args.recommendation_history, captured)
    anchor_state_value, anchor_changes = anchor_state(args.timeout)
    anchor = {"state": anchor_state_value, "changes": {key: rounded(value) for key, value in anchor_changes.items()}}
    benchmark_daily_rows = []
    benchmark_4h_rows = []
    try:
        benchmark_daily_rows = risk_price_rows(
            fetch_json(
                "/api/v3/klines",
                {"symbol": "BTCUSDT", "interval": "1d", "limit": 140},
                timeout=args.timeout,
            ),
            captured,
        )
        benchmark_4h_rows = risk_price_rows(
            fetch_json(
                "/api/v3/klines",
                {"symbol": "BTCUSDT", "interval": "4h", "limit": 140},
                timeout=args.timeout,
            ),
            captured,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"risk benchmark fetch failed: {exc}", file=sys.stderr)
    signals = []
    errors = []
    for symbol in symbols:
        try:
            signal = analyze_symbol(
                symbol,
                anchor,
                args.timeout,
                baseline_ids.get(symbol),
                captured,
            )
            try:
                signal["risk_adjusted_path"] = fetch_risk_adjusted_path(
                    symbol,
                    timeout=args.timeout,
                    captured_at=captured,
                    benchmark_daily_rows=benchmark_daily_rows,
                    benchmark_4h_rows=benchmark_4h_rows,
                    risk_free_rate_pct=args.risk_free_rate_pct,
                    risk_free_evidence_id=args.risk_free_evidence_id,
                )
            except Exception as risk_exc:  # noqa: BLE001
                signal["risk_adjusted_path_error"] = str(risk_exc)
            signals.append(signal)
            time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001 - scanner should continue and record failures.
            errors.append({"symbol": symbol, "error": str(exc)})
    apply_cross_sectional_adjustments(
        signals,
        base_score_field="impulse_score_points",
    )
    signals.sort(key=lambda row: (row.get("impulse_score_points") or 0, row.get("volume_multiple_5m_vs_median") or 0), reverse=True)
    result = {
        "run_type": "impulse_capture_scan",
        "captured_at": captured.isoformat().replace("+00:00", "Z"),
        "live_orders_enabled": False,
        "private_api_used": False,
        "anchor": anchor,
        "signals": signals,
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
        "max_allowed_action": "conditional_action_before_manual_review",
    }
    if not args.no_write:
        report_path, experiment_path = write_outputs(result, args.output_dir)
        result["report_path"] = str(report_path)
        result["experiment_path"] = str(experiment_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
