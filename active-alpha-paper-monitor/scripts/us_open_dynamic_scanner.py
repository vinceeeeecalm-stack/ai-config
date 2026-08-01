#!/usr/bin/env python3
"""
US open dynamic scanner.

Research/handoff only. It fetches public Yahoo screener/chart/search data,
builds Top 1-3 US equity candidates, and writes a handoff plus Markdown report
for manual-investment-strategy-operator. It never places orders.
"""

import argparse
import concurrent.futures
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fast_candidate_funnel import (
    DiscoveryCandidateV1,
    committee_requirement,
    phase_budget_status,
    rank_discovery_candidates,
    historical_path_comparison,
    historical_rank_key,
    ranked_historical_comparison,
)
from research_panel_bridge import active_research_panel_overlay
from risk_adjusted_path_quality import (
    CALCULATION_VERSION as RISK_PATH_CALCULATION_VERSION,
    apply_cross_sectional_adjustments,
    calculate_risk_adjusted_path,
)


USER_AGENT = "Mozilla/5.0 (Codex active-alpha-paper-monitor; research only)"
CN_TZ = timezone(timedelta(hours=8))
DEFAULT_SCREENERS = [
    "day_gainers",
    "most_actives",
    "growth_technology_stocks",
    "aggressive_small_caps",
]
DEFAULT_COUNT = 25
DEFAULT_REQUEST_TIMEOUT = 4
DEFAULT_MAX_SYMBOLS = 8
DISCOVERY_SCREENER_TIMEOUT_SECONDS = 3.5
DISCOVERY_SPARK_TIMEOUT_SECONDS = 3.5
DISCOVERY_FALLBACK_TIMEOUT_SECONDS = 3.0
DISCOVERY_FALLBACK_MAX_SYMBOLS = 8
PROTECTED_LONG_TERM_HOLDINGS = {"CRCL"}
LEDGER_CANDIDATE_PATHS = [
    Path(__file__).resolve().parents[2] / "unified-longterm-alpha-investor" / "config" / "portfolio_ledger.json",
    Path(__file__).resolve().parents[1] / "paper_trades" / "paper_portfolio_ledger.json",
]
POSITION_OVERRIDES_PATH = (
    Path(__file__).resolve().parents[2]
    / "manual-investment-strategy-operator"
    / "config"
    / "current_position_overrides.json"
)


REQUEST_TIMEOUT = DEFAULT_REQUEST_TIMEOUT
NEW_YORK_TZ = ZoneInfo("America/New_York")


def fetch_json(url, timeout=None):
    timeout = REQUEST_TIMEOUT if timeout is None else timeout
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def numeric(value, default=None):
    """Return a finite numeric value from Yahoo's raw or wrapped fields."""

    if isinstance(value, dict):
        value = value.get("raw")
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def iso_from_epoch(value):
    parsed = numeric(value)
    if parsed is None:
        return None
    try:
        return datetime.fromtimestamp(parsed, tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def yahoo_screener(scr_id, count=DEFAULT_COUNT, timeout=None):
    query = urllib.parse.urlencode({"scrIds": scr_id, "count": count})
    url = f"https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?{query}"
    body = fetch_json(url, timeout=timeout)
    result = body.get("finance", {}).get("result") or []
    quotes = []
    for section in result:
        quotes.extend(section.get("quotes") or [])
    return quotes


def yahoo_spark(symbols, range_="5d", interval="1d", timeout=None):
    """Fetch a daily return vector for many symbols in one public request."""

    symbols = [str(symbol).upper() for symbol in symbols if str(symbol).strip()]
    if not symbols:
        return {}
    query = urllib.parse.urlencode(
        {
            "symbols": ",".join(symbols),
            "range": range_,
            "interval": interval,
        }
    )
    url = f"https://query1.finance.yahoo.com/v7/finance/spark?{query}"
    body = fetch_json(url, timeout=timeout)
    output = {}
    for item in (body.get("spark", {}).get("result") or []):
        symbol = str(item.get("symbol") or "").upper()
        response = (item.get("response") or [None])[0]
        if not symbol or not response:
            continue
        quote = ((response.get("indicators") or {}).get("quote") or [{}])[0]
        closes = [numeric(value) for value in (quote.get("close") or [])]
        closes = [value for value in closes if value is not None and value > 0]
        meta = response.get("meta") or {}
        output[symbol] = {
            "closes": closes,
            "meta": meta,
            "price_as_of": iso_from_epoch(meta.get("regularMarketTime")),
        }
    return output


def yahoo_spark_batched(symbols, batch_size=8, timeout=None):
    symbols = [str(symbol).upper() for symbol in symbols if str(symbol).strip()]
    batches = [symbols[index : index + max(1, batch_size)] for index in range(0, len(symbols), max(1, batch_size))]
    output = {}
    errors = []

    def fetch_batch(batch):
        try:
            return batch, yahoo_spark(batch, timeout=timeout), None
        except Exception as exc:  # noqa: BLE001
            return batch, {}, str(exc)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(len(batches), 8))
    ) as executor:
        for batch, values, error in executor.map(fetch_batch, batches):
            output.update(values)
            if error:
                errors.append(
                    {
                        "source": "batch_5d_spark",
                        "symbols": batch,
                        "error": error,
                    }
                )
    return output, errors


def yahoo_chart(symbol, range_="2mo", interval="1d", timeout=None):
    query = urllib.parse.urlencode({"range": range_, "interval": interval})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{query}"
    body = fetch_json(url, timeout=timeout)
    result = (body.get("chart", {}).get("result") or [None])[0]
    if not result:
        return []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    adjusted = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or []
    timestamps = result.get("timestamp") or []
    now_ny = datetime.now(timezone.utc).astimezone(NEW_YORK_TZ)
    rows = []
    for idx, ts in enumerate(timestamps):
        close = (quote.get("close") or [None])[idx]
        high = (quote.get("high") or [None])[idx]
        low = (quote.get("low") or [None])[idx]
        volume = (quote.get("volume") or [None])[idx]
        if close is None or high is None or low is None:
            continue
        adjusted_close = adjusted[idx] if idx < len(adjusted) and adjusted[idx] is not None else close
        bar_date = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(NEW_YORK_TZ).date()
        is_closed = bar_date < now_ny.date() or (
            bar_date == now_ny.date()
            and (now_ny.hour, now_ny.minute) >= (16, 15)
        )
        rows.append(
            {
                "ts": ts,
                "close": float(close),
                "adjusted_close": float(adjusted_close),
                "high": float(high),
                "low": float(low),
                "volume": float(volume or 0),
                "is_closed": is_closed,
            }
        )
    return rows


def yahoo_news(symbol, count=3):
    query = urllib.parse.urlencode({"q": symbol, "newsCount": count, "quotesCount": 0})
    url = f"https://query1.finance.yahoo.com/v1/finance/search?{query}"
    body = fetch_json(url)
    return [
        {"title": item.get("title"), "publisher": item.get("publisher"), "link": item.get("link")}
        for item in body.get("news", [])[:count]
    ]


def pct(now, then):
    if then in (None, 0) or now is None:
        return None
    return (now / then - 1.0) * 100.0


def atr_pct(rows, n=20):
    if len(rows) < 2:
        return None
    recent = rows[-n:]
    trs = []
    prev_close = rows[-len(recent) - 1]["close"] if len(rows) > len(recent) else None
    for row in recent:
        high_low = row["high"] - row["low"]
        if prev_close is None:
            tr = high_low
        else:
            tr = max(high_low, abs(row["high"] - prev_close), abs(row["low"] - prev_close))
        trs.append(tr)
        prev_close = row["close"]
    close = recent[-1]["close"]
    return (sum(trs) / len(trs) / close) * 100.0 if close else None


def avg(items):
    items = [x for x in items if x is not None]
    return sum(items) / len(items) if items else None


def load_portfolio_ledger():
    for path in LEDGER_CANDIDATE_PATHS:
        try:
            if path.is_file():
                body = json.loads(path.read_text(encoding="utf-8"))
                return path, body
        except Exception:  # noqa: BLE001
            continue
    return None, {}


def load_position_overrides():
    try:
        if POSITION_OVERRIDES_PATH.is_file():
            return json.loads(POSITION_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {}


def current_tactical_snapshot(cli_symbol=""):
    if cli_symbol:
        return {
            "current_tactical_position": cli_symbol.upper(),
            "deployable_tactical_position": cli_symbol.upper(),
            "selection_source": "cli_override",
            "selection_reason": "Current tactical position provided explicitly.",
            "protected_long_term_holdings": sorted(PROTECTED_LONG_TERM_HOLDINGS),
            "us_equity_holdings": [],
            "ledger_path": None,
        }

    ledger_path, ledger = load_portfolio_ledger()
    overrides = load_position_overrides()
    holding_overrides = overrides.get("holdings") or {}
    cash_overrides = overrides.get("cash_rails") or {}
    holdings = ledger.get("holdings") or []
    us_equity = []
    for holding in holdings:
        if holding.get("account_rail") != "us_equity_rail":
            continue
        symbol = (holding.get("symbol") or "").upper()
        if not symbol:
            continue
        override = holding_overrides.get(symbol) or holding_overrides.get(symbol.upper()) or {}
        quantity = float(override.get("quantity", holding.get("quantity") or 0.0))
        default_liquid_quantity = quantity if override.get("quantity") is not None else (holding.get("liquid_quantity") or quantity)
        liquid_quantity = float(override.get("liquid_quantity", default_liquid_quantity))
        current_price = holding.get("current_price")
        market_value = float(override.get("market_value") or 0.0)
        if not market_value:
            market_value = float(current_price or 0.0) * quantity
        bucket = (holding.get("bucket") or "").lower()
        strategy_horizon = (holding.get("strategy_horizon") or "").lower()
        is_protected = symbol in PROTECTED_LONG_TERM_HOLDINGS
        is_tactical = "tactical" in bucket or strategy_horizon == "daily" or bool(holding.get("leveraged_etf"))
        score = 0.0
        if is_tactical:
            score += 100.0
        # Historical account prices and previously inferred tactical scores are
        # not current-market authority. They remain visible for audit, but the
        # market comparison below refreshes the selected symbol independently.
        score += min(liquid_quantity, 50.0)
        if is_protected:
            score -= 1000.0
        us_equity.append(
            {
                "symbol": symbol,
                "name": holding.get("name") or symbol,
                "market_value": round(market_value, 2),
                "quantity": quantity,
                "liquid_quantity": liquid_quantity,
                "bucket": holding.get("bucket"),
                "strategy_horizon": holding.get("strategy_horizon"),
                "current_price": current_price,
                "avg_cost": holding.get("avg_cost"),
                "position_pressure_score_points": holding.get("position_pressure_score_points"),
                "short_term_score_points": holding.get("short_term_score_points"),
                "risk_decision": holding.get("risk_decision"),
                "is_protected_long_term": is_protected,
                "is_tactical_candidate": is_tactical,
                "selection_score_points": round(score, 2),
                "override_applied": bool(override),
                "historical_account_price_used_for_current_ranking": False,
            }
        )

    deployable = None
    ranked = sorted(
        [item for item in us_equity if item["liquid_quantity"] > 0 and not item["is_protected_long_term"]],
        key=lambda item: (
            item["is_tactical_candidate"],
            item["liquid_quantity"],
            item["symbol"],
        ),
        reverse=True,
    )
    if ranked:
        deployable = ranked[0]

    if deployable:
        if deployable["is_tactical_candidate"]:
            reason = "Selected from local ledger plus current overrides as the highest-priority liquid US tactical position while protecting CRCL."
        else:
            reason = "No explicit tactical bucket found; selected the strongest liquid non-protected US equity as fallback."
    else:
        reason = "No liquid non-protected US equity holding was found in the local ledger."

    return {
        "current_tactical_position": deployable["symbol"] if deployable else None,
        "deployable_tactical_position": deployable["symbol"] if deployable else None,
        "selection_source": "local_portfolio_ledger_with_overrides" if ledger_path else "no_local_ledger",
        "selection_reason": reason,
        "protected_long_term_holdings": sorted(PROTECTED_LONG_TERM_HOLDINGS),
        "us_equity_holdings": us_equity,
        "ledger_path": str(ledger_path) if ledger_path else None,
        "position_overrides_path": str(POSITION_OVERRIDES_PATH) if POSITION_OVERRIDES_PATH.is_file() else None,
        "us_equity_cash_usd": float(cash_overrides.get("us_equity_cash_usd") or 0.0),
    }


def benchmark_symbol_for(quote, sources):
    name = str(quote.get("shortName") or quote.get("longName") or "").lower()
    sector = str(quote.get("sector") or "").lower()
    if "semiconductor" in name or "semiconductor" in sector:
        return "SMH"
    if "growth_technology_stocks" in sources or sector in {
        "technology",
        "communication services",
    }:
        return "QQQ"
    return "SPY"


def compute_metrics(
    symbol,
    quote,
    *,
    rows=None,
    benchmark_rows=None,
    benchmark_symbol="SPY",
    risk_free_rate_pct=0.0,
    risk_free_evidence_id=None,
    sources=(),
    include_risk_adjusted_path=True,
):
    rows = rows if rows is not None else yahoo_chart(symbol, range_="1y")
    rows = [row for row in rows if row.get("is_closed") is not False]
    if len(rows) < 22:
        return None
    close = rows[-1]["close"]
    quoted_price = quote.get("regularMarketPrice")
    try:
        quoted_price = float(quoted_price)
    except (TypeError, ValueError):
        quoted_price = None
    current_price = quoted_price if quoted_price and quoted_price > 0 else close
    quote_time = quote.get("regularMarketTime")
    try:
        price_as_of = datetime.fromtimestamp(
            float(quote_time), tz=timezone.utc
        ).isoformat()
    except (TypeError, ValueError, OSError):
        price_as_of = datetime.fromtimestamp(
            float(rows[-1]["ts"]), tz=timezone.utc
        ).isoformat()
    last_volume = rows[-1]["volume"]
    avg20_volume = avg([row["volume"] for row in rows[-20:]]) or 0
    prev_close = rows[-2]["close"]
    one_day = pct(close, prev_close)
    five_day = pct(close, rows[-6]["close"] if len(rows) >= 6 else None)
    twenty_day = pct(close, rows[-21]["close"] if len(rows) >= 21 else None)
    lane = (
        "value_repair"
        if (twenty_day or 0) < 0 and (five_day or 0) > 0 and (one_day or 0) > 0
        else "trend_continuation"
    )
    value_catalyst_gate_status = "pending" if lane == "value_repair" else "not_applicable"
    risk_path = None
    if include_risk_adjusted_path:
        risk_path = calculate_risk_adjusted_path(
            rows,
            benchmark_rows or [],
            asset_class="us_equity",
            lane=lane,
            benchmark=benchmark_symbol,
            risk_free_annual_pct=risk_free_rate_pct,
            risk_free_evidence_id=risk_free_evidence_id,
            evidence_ids=(
                f"yahoo-adjusted-close:{symbol}",
                f"yahoo-adjusted-close:{benchmark_symbol}",
                *(sources or ()),
            ),
            value_catalyst_gate_status=value_catalyst_gate_status,
            cutoff_at=datetime.now(timezone.utc),
        )
    metrics = {
        "symbol": symbol,
        "short_name": quote.get("shortName") or quote.get("longName") or symbol,
        "price": current_price,
        "closed_reference_price": close,
        "current_gap_pct": pct(current_price, close),
        "price_source": (
            "Yahoo screener regularMarketPrice"
            if quoted_price and quoted_price > 0
            else "Yahoo closed daily chart fallback"
        ),
        "price_as_of": price_as_of,
        "market_cap": quote.get("marketCap"),
        "quote_type": quote.get("quoteType"),
        "exchange": quote.get("exchange"),
        "currency": quote.get("currency"),
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "bid_size": quote.get("bidSize"),
        "ask_size": quote.get("askSize"),
        "1d_pct": one_day,
        "5d_pct": five_day,
        "20d_pct": twenty_day,
        "60d_pct": pct(close, rows[-61]["close"] if len(rows) >= 61 else None),
        "atr20_pct": atr_pct(rows, 20),
        "last_volume": last_volume,
        "avg20_volume": avg20_volume,
        "vol_ratio": (last_volume / avg20_volume) if avg20_volume else None,
        "support": min(row["low"] for row in rows[-10:]),
        "resistance": max(row["high"] for row in rows[-10:]),
        "risk_adjusted_path": risk_path,
        "risk_adjusted_lane": lane,
        "value_catalyst_gate_status": value_catalyst_gate_status,
    }
    return metrics


def attach_risk_adjusted_path(
    metrics,
    rows,
    *,
    benchmark_rows,
    benchmark_symbol,
    risk_free_rate_pct,
    risk_free_evidence_id,
    sources=(),
):
    metrics["risk_adjusted_path"] = calculate_risk_adjusted_path(
        [row for row in rows if row.get("is_closed") is not False],
        benchmark_rows or [],
        asset_class="us_equity",
        lane=metrics["risk_adjusted_lane"],
        benchmark=benchmark_symbol,
        risk_free_annual_pct=risk_free_rate_pct,
        risk_free_evidence_id=risk_free_evidence_id,
        evidence_ids=(
            f"yahoo-adjusted-close:{metrics['symbol']}",
            f"yahoo-adjusted-close:{benchmark_symbol}",
            *(sources or ()),
        ),
        value_catalyst_gate_status=metrics["value_catalyst_gate_status"],
        cutoff_at=datetime.now(timezone.utc),
    )
    return metrics


def preliminary_quote_score(quote, sources):
    price = numeric(quote.get("regularMarketPrice"), 0.0) or 0.0
    one_day = numeric(quote.get("regularMarketChangePercent"), 0.0) or 0.0
    volume = numeric(quote.get("regularMarketVolume"), 0.0) or 0.0
    average_volume = (
        numeric(quote.get("averageDailyVolume10Day"))
        or numeric(quote.get("averageDailyVolume3Month"))
        or 0.0
    )
    relative_volume = volume / average_volume if average_volume > 0 else 0.0
    turnover = price * volume
    score = min(max(one_day, -15.0), 35.0) * 1.2
    score += min(relative_volume, 8.0) * 8.0
    score += min(max(math.log10(max(turnover, 1.0)) - 5.0, 0.0), 5.0) * 3.0
    score += 6.0 if "most_actives" in sources else 0.0
    score += 3.0 if 2.0 <= price <= 80.0 else 0.0
    return round(max(0.0, min(100.0, score)), 4)


def discovery_metrics_from_quote(symbol, quote, sources, spark_item):
    """Build probability-free discovery metrics from batched public data."""

    spark_item = spark_item or {}
    meta = spark_item.get("meta") or {}
    closes = spark_item.get("closes") or []
    price = (
        numeric(quote.get("regularMarketPrice"))
        or numeric(meta.get("regularMarketPrice"))
        or (closes[-1] if closes else None)
    )
    price_as_of = (
        iso_from_epoch(quote.get("regularMarketTime"))
        or spark_item.get("price_as_of")
    )
    volume = (
        numeric(quote.get("regularMarketVolume"))
        or numeric(meta.get("regularMarketVolume"))
        or 0.0
    )
    average_volume = (
        numeric(quote.get("averageDailyVolume10Day"))
        or numeric(quote.get("averageDailyVolume3Month"))
        or 0.0
    )
    relative_volume = volume / average_volume if average_volume > 0 else None
    bid = numeric(quote.get("bid"))
    ask = numeric(quote.get("ask"))
    spread_bps = None
    if bid is not None and ask is not None and ask >= bid and (ask + bid) > 0:
        spread_bps = (ask - bid) / ((ask + bid) / 2.0) * 10_000.0
    one_day = pct(closes[-1], closes[-2]) if len(closes) >= 2 else None
    five_day = pct(closes[-1], closes[0]) if len(closes) >= 2 else None
    return {
        "symbol": symbol,
        "short_name": quote.get("shortName") or quote.get("longName") or symbol,
        "price": price,
        "price_as_of": price_as_of,
        "quote_type": quote.get("quoteType") or meta.get("instrumentType"),
        "last_volume": volume,
        "vol_ratio": relative_volume,
        "turnover": (price or 0.0) * volume,
        "spread_bps": spread_bps,
        "1d_pct": one_day,
        "5d_pct": five_day,
        "data_quality_status": (
            "verified_yahoo_public_batch"
            if price and price_as_of and one_day is not None and five_day is not None
            else "degraded_yahoo_public_batch"
        ),
        "sources": tuple(sorted(sources)),
    }


def score_discovery_metrics(metrics):
    one_day = metrics.get("1d_pct") or 0.0
    five_day = metrics.get("5d_pct") or 0.0
    relative_volume = metrics.get("vol_ratio") or 0.0
    turnover = metrics.get("turnover") or 0.0
    score = min(max(one_day, -15.0), 35.0) * 1.0
    score += min(max(five_day, -30.0), 60.0) * 0.45
    score += min(relative_volume, 8.0) * 8.0
    score += min(max(math.log10(max(turnover, 1.0)) - 5.0, 0.0), 5.0) * 3.0
    score += 6.0 if "most_actives" in metrics.get("sources", ()) else 0.0
    return round(max(0.0, min(100.0, score)), 4)


def score_candidate(metrics, sources, current_metrics=None):
    one = metrics.get("1d_pct") or 0
    five = metrics.get("5d_pct") or 0
    twenty = metrics.get("20d_pct") or 0
    atr = metrics.get("atr20_pct") or 0
    vol_ratio = metrics.get("vol_ratio") or 0
    price = metrics.get("price") or 0
    current_five = (current_metrics or {}).get("5d_pct") or 0
    relative = five - current_five

    score = 0
    score += min(max(one, -10), 30) * 0.8
    score += min(max(five, -20), 40) * 0.5
    score += min(max(twenty, -30), 80) * 0.2
    score += min(atr, 15) * 2.0
    score += min(vol_ratio, 5) * 7.0
    score += min(max(relative, -20), 40) * 0.5
    score += 7 if "most_actives" in sources else 0
    score += 5 if price and price < 60 else 0
    return round(score, 2)


def build_candidate(
    metrics,
    sources,
    rank,
    current_symbol,
    current_metrics,
    request_mode="tactical_1_7d",
):
    price = metrics["price"]
    current_gap_pct = metrics.get("current_gap_pct") or 0.0
    gap_repricing_guard = current_gap_pct >= 8.0
    atr = metrics.get("atr20_pct") or 7.0
    support = metrics.get("support") or price * 0.92
    resistance = metrics.get("resistance") or price * 1.08
    pullback_entry = max(support, price * (1 - min(atr, 12) / 200))
    breakout_entry = resistance * 1.01
    target = max(resistance * 1.08, price * (1 + min(max(atr, 5), 12) / 100 * 1.4))
    atr_stop = price * (1 - min(max(atr, 6), 12) / 100)
    support_stop = support * 0.98
    stop = max(support_stop, atr_stop)
    setup_quality = 45
    if metrics.get("vol_ratio") and metrics["vol_ratio"] >= 1.5:
        setup_quality += 12
    if metrics.get("1d_pct") and metrics["1d_pct"] > 8:
        setup_quality += 8
    if metrics.get("atr20_pct") and metrics["atr20_pct"] >= 5:
        setup_quality += 8
    if metrics.get("last_volume", 0) >= 1_000_000:
        setup_quality += 10
    if sources:
        setup_quality += 5
    setup_quality = min(setup_quality, 100)

    current_label = current_symbol or "current_tactical_position"
    pre_research_grade = "watch"
    if gap_repricing_guard:
        pre_research_grade = "watch"
        entry_zone = (
            f"no entry: current quote is {current_gap_pct:.2f}% above the last "
            "closed reference; catalyst repricing and reward/risk review required"
        )
        target_price = None
        stop_loss = None
    else:
        entry_zone = (
            f"{pullback_entry:.2f}-{price:.2f} pullback/hold, or breakout "
            f"above {breakout_entry:.2f}"
        )
        target_price = round(target, 2)
        stop_loss = round(stop, 2)
    reward_risk_ratio = None
    if target_price is not None and stop_loss is not None and price > stop_loss:
        reward_risk_ratio = round((target_price - price) / (price - stop_loss), 4)
    intraday_session_date = datetime.now(timezone.utc).astimezone(NEW_YORK_TZ).date()
    try:
        intraday_session_date = datetime.fromisoformat(
            str(metrics.get("price_as_of") or "").replace("Z", "+00:00")
        ).astimezone(NEW_YORK_TZ).date()
    except (TypeError, ValueError):
        pass
    return {
        "candidate_symbol": metrics["symbol"],
        "symbol": metrics["symbol"],
        "candidate_type": "us_open_scan",
        "request_mode": request_mode,
        "asset_class": "us_equity",
        "rail_candidate": "us_equity_rail",
        "sources": sorted(sources),
        "short_name": metrics["short_name"],
        "price": round(price, 4),
        "closed_reference_price": round(metrics["closed_reference_price"], 4),
        "current_gap_pct": metrics.get("current_gap_pct"),
        "gap_repricing_guard": gap_repricing_guard,
        "price_source": metrics.get("price_source"),
        "price_as_of": metrics.get("price_as_of"),
        "entry_zone": entry_zone,
        "target_price": target_price,
        "target_time_window": (
            "same US regular session; no overnight"
            if request_mode == "intraday_scalp"
            else "1-5 trading days, max 10 trading days"
        ),
        "stop_loss": stop_loss,
        "latest_exit_date": (
            intraday_session_date.isoformat()
            if request_mode == "intraday_scalp"
            else (datetime.now(timezone.utc) + timedelta(days=10)).date().isoformat()
        ),
        "setup_quality_score": int(setup_quality),
        "reward_risk_ratio": reward_risk_ratio,
        "why_better_than_current_tactical_position": (
            f"Requires confirmation versus {current_label}; ranked by open-window momentum, volatility, volume, and catalyst/news availability."
        ),
        "candidate_cash_relay_priority": rank,
        "pre_research_candidate_grade": pre_research_grade,
        "monitor_recommendation": "watch",
        "research_panel_missing_for_candidate": True,
        "candidate_action_downgrade_reason": (
            "Fresh quote gap requires catalyst repricing and reward/risk review; "
            "no entry, target or stop is valid from the closed-bar path."
            if gap_repricing_guard
            else "US-open scanner does not run the mandatory Research Committee; "
            "Manual must estimate probability from historical samples and apply the conservative-EV, risk and cash gates."
        ),
        "data_quality_status": "verified_yahoo_public_chart",
        "metrics": metrics,
        "risk_adjusted_path": metrics.get("risk_adjusted_path"),
        "news": [],
        "requires_manual_review": True,
        "live_orders_enabled": False,
    }


def to_discovery_candidate(metrics, sources, score):
    reasons = [
        f"one_day_return={round(metrics.get('1d_pct') or 0.0, 3)}pct",
        f"five_day_return={round(metrics.get('5d_pct') or 0.0, 3)}pct",
        f"relative_volume={round(metrics.get('vol_ratio') or 0.0, 3)}x",
    ]
    hard_rejections = []
    if metrics.get("quote_type") not in (None, "EQUITY", "ETF"):
        hard_rejections.append("unsupported_quote_type")
    if not metrics.get("price") or not metrics.get("price_as_of"):
        hard_rejections.append("current_price_or_timestamp_missing")
    if metrics.get("1d_pct") is None or metrics.get("5d_pct") is None:
        hard_rejections.append("daily_return_vector_missing")
    return DiscoveryCandidateV1(
        symbol=metrics["symbol"],
        asset_class="us_equity",
        market_time=metrics.get("price_as_of") or datetime.now(timezone.utc).isoformat(),
        current_price=float(metrics.get("price") or 0.0),
        turnover=float(
            metrics.get("turnover")
            or (float(metrics.get("last_volume") or 0.0) * float(metrics.get("price") or 0.0))
        ),
        relative_volume=metrics.get("vol_ratio"),
        trade_count=None,
        vwap=None,
        spread_bps=metrics.get("spread_bps"),
        depth_bid_usd=None,
        depth_ask_usd=None,
        anchor_state={
            "market": metrics.get("risk_adjusted_path", {}).get("benchmark")
            if metrics.get("risk_adjusted_path")
            else "benchmark_checked_after_top3",
            "BTC": None,
            "ETH": None,
        },
        confirmed_catalyst_present=None,
        discovery_score=max(0.0, min(100.0, float(score))),
        ranking_reason=tuple(reasons),
        hard_rejection_reasons=tuple(hard_rejections),
        return_1d_pct=metrics.get("1d_pct"),
        return_5d_pct=metrics.get("5d_pct"),
        data_sources=tuple(sorted(str(item) for item in sources)),
        data_quality_status=str(
            metrics.get("data_quality_status") or "verified_yahoo_public_chart"
        ),
    )


def report_number(value, digits=2):
    if value is None:
        return "unavailable"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "unavailable"


def build_us_intraday_precheck(candidate, generated_at):
    generated = generated_at.astimezone(timezone.utc)
    price_as_of = None
    try:
        price_as_of = datetime.fromisoformat(
            str((candidate or {}).get("price_as_of") or "").replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    quote_age = (
        (generated - price_as_of).total_seconds()
        if price_as_of is not None
        else None
    )
    local = generated.astimezone(NEW_YORK_TZ)
    session_date = (
        price_as_of.astimezone(NEW_YORK_TZ).date()
        if price_as_of is not None
        else local.date()
    )
    latest_close = datetime.combine(
        session_date,
        datetime.min.time(),
        tzinfo=NEW_YORK_TZ,
    ).replace(hour=15, minute=55)
    blockers = ["closed_1m_bar_missing", "closed_5m_bar_missing", "vwap_opening_range_missing"]
    if quote_age is None or not 0 <= quote_age <= 60:
        blockers.append("quote_age_over_60_seconds")
    if latest_close <= local:
        blockers.append("us_regular_session_close_window_passed")
    return {
        "schema_version": "intraday-market-precheck-v1",
        "status": "blocked",
        "quote_as_of": price_as_of.isoformat() if price_as_of else None,
        "quote_age_seconds": round(quote_age, 3) if quote_age is not None else None,
        "decision_valid_until": (generated + timedelta(minutes=5)).isoformat(),
        "allowed_session": "us_regular_session_only",
        "entry_trigger": "blocked until a <=60-second quote plus closed 1m/5m, VWAP, opening-range, spread and depth evidence are present",
        "cancel_if": "any freshness, liquidity, opening-range, underlying, event, or same-day-exit gate fails",
        "price_stop": None,
        "target_1_price": None,
        "target_2_price": None,
        "latest_close_at": latest_close.isoformat(),
        "overnight_allowed": False,
        "blockers": blockers,
        "live_orders_enabled": False,
    }


def market_session_context(generated_at, price_as_of):
    generated = generated_at.astimezone(timezone.utc)
    local = generated.astimezone(NEW_YORK_TZ)
    quote_time = None
    try:
        quote_time = datetime.fromisoformat(str(price_as_of).replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except (TypeError, ValueError):
        pass
    quote_age = (
        (generated - quote_time).total_seconds() if quote_time is not None else None
    )
    minutes = local.hour * 60 + local.minute
    regular_window = local.weekday() < 5 and 570 <= minutes < 960
    live_quote = quote_age is not None and 0 <= quote_age <= 60
    return {
        "state": "open_live" if regular_window and live_quote else "closed_or_stale_last_session",
        "generated_at": generated.isoformat(),
        "new_york_time": local.isoformat(),
        "price_cutoff": quote_time.isoformat() if quote_time else None,
        "quote_age_seconds": round(quote_age, 3) if quote_age is not None else None,
        "regular_session_window": regular_window,
        "live_quote_within_60_seconds": live_quote,
        "may_authorize_intraday_entry": False,
    }


def build_us_top1_decision_card(
    candidate,
    *,
    completed_at,
    request_mode,
    market_plan,
    deployable_cash,
):
    committee = committee_requirement(phase="validation", request_mode=request_mode)
    cash = max(0.0, numeric(deployable_cash, 0.0) or 0.0)
    missing_evidence = [
        "mode_appropriate_committee_outputs_missing",
        "official_sec_filing_and_financing_review_missing",
        "latest_earnings_guidance_and_consensus_review_missing",
        "candidate_specific_historical_target_before_stop_sample_missing",
        "derivatives_and_event_gap_underwriting_missing",
    ]
    for blocker in (market_plan or {}).get("blockers") or []:
        if blocker not in missing_evidence:
            missing_evidence.append(blocker)
    return {
        "schema_version": "top1-decision-card-v1",
        "completed_at": completed_at.astimezone(timezone.utc).isoformat(),
        "completion_status": "complete_with_explicit_blockers",
        "best_candidate": (candidate or {}).get("symbol"),
        "research_decision": "watch",
        "current_direct_decision": "do_not_enter_now",
        "account_state": {
            "status": "current_override_or_zero_baseline",
            "deployable_cash": cash,
        },
        "execution_decision": "no_deploy_cash" if cash == 0 else "no_deploy_evidence",
        "executable_amount": 0.0,
        "selection_reason": "highest post-validation risk-adjusted setup quality among discovery Top3",
        "setup_quality_score": (candidate or {}).get("setup_quality_score"),
        "committee_requirement": committee,
        "committee_status": "degraded_missing_required_outputs",
        "financial_statement_review_status": "missing",
        "earnings_expectation_review_status": "missing",
        "historical_conditioning_status": "missing",
        "probability_status": "not_estimated_without_valid_sample",
        "missing_evidence": missing_evidence,
        "market_plan": market_plan,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def _p95(values):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def run_self_test(iterations=30, candidate_count=25):
    phase_samples = {"discovery": [], "validation": [], "top1_card": [], "end_to_end": []}
    last_card = None
    for _ in range(max(1, iterations)):
        started = time.monotonic()
        candidates = [
            DiscoveryCandidateV1(
                symbol=f"US{index:02d}",
                asset_class="us_equity",
                market_time="2026-07-31T20:00:01+00:00",
                current_price=10.0 + index,
                turnover=10_000_000.0 + index,
                relative_volume=1.0 + index / 10.0,
                trade_count=None,
                vwap=None,
                spread_bps=2.0,
                depth_bid_usd=None,
                depth_ask_usd=None,
                anchor_state={"market": "pending_top3_validation", "BTC": None, "ETH": None},
                confirmed_catalyst_present=None,
                discovery_score=float(index),
                ranking_reason=("deterministic_us_fixture",),
                return_1d_pct=1.0 + index / 10.0,
                return_5d_pct=2.0 + index / 10.0,
                data_sources=("deterministic_fixture",),
            )
            for index in range(max(3, candidate_count))
        ]
        discovery = rank_discovery_candidates(candidates, top_n=3)
        discovery_elapsed = time.monotonic() - started
        validation_started = time.monotonic()
        top3 = discovery["top_candidates"]
        top1 = {
            "symbol": top3[0]["symbol"],
            "setup_quality_score": 80,
        }
        validation_elapsed = time.monotonic() - validation_started
        last_card = build_us_top1_decision_card(
            top1,
            completed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            request_mode="intraday_scalp",
            market_plan={
                "status": "blocked",
                "blockers": ["deterministic_fixture_no_live_quote"],
                "overnight_allowed": False,
                "live_orders_enabled": False,
            },
            deployable_cash=0,
        )
        end_elapsed = time.monotonic() - started
        phase_samples["discovery"].append(discovery_elapsed)
        phase_samples["validation"].append(validation_elapsed)
        phase_samples["top1_card"].append(end_elapsed)
        phase_samples["end_to_end"].append(end_elapsed)
    phase_p95 = {name: round(_p95(values), 6) for name, values in phase_samples.items()}
    budgets = {
        "discovery": 15.0,
        "validation": 45.0,
        "top1_card": 120.0,
        "end_to_end": 180.0,
    }
    checks = {
        "all_phase_p95_within_budget": all(phase_p95[name] <= budgets[name] for name in budgets),
        "top1_card_complete": bool(last_card)
        and last_card.get("completion_status") == "complete_with_explicit_blockers",
        "current_decision_explicit": bool(last_card)
        and last_card.get("current_direct_decision") == "do_not_enter_now",
        "account_execution_separated": bool(last_card)
        and last_card.get("execution_decision") == "no_deploy_cash"
        and last_card.get("executable_amount") == 0.0,
        "live_orders_disabled": bool(last_card)
        and last_card.get("live_orders_enabled") is False,
    }
    return {
        "schema_version": "us-open-fast-funnel-performance-v1",
        "status": "ok" if all(checks.values()) else "failed",
        "iterations": max(1, iterations),
        "candidate_count": max(3, candidate_count),
        "phase_p95_seconds": phase_p95,
        "phase_budget_seconds": budgets,
        "checks": checks,
        "top1_decision_card": last_card,
        "live_orders_enabled": False,
    }


def audit_runtime_contract(payload, require_open_session=False):
    errors = []
    if len(payload.get("discovery_top3") or []) < 1:
        errors.append("discovery_top3_missing")
    if not payload.get("validated_top1"):
        errors.append("validated_top1_missing")
    card = payload.get("top1_decision_card") or {}
    if card.get("completion_status") != "complete_with_explicit_blockers":
        errors.append("complete_top1_decision_card_missing")
    if card.get("current_direct_decision") not in {
        "enter_now",
        "small_entry_now",
        "do_not_enter_now",
    }:
        errors.append("current_direct_decision_missing")
    for phase in ("discovery", "validation", "top1_card", "end_to_end"):
        timing = (payload.get("stage_timings") or {}).get(phase) or {}
        if timing.get("within_budget") is not True:
            errors.append(f"{phase}_budget_failed")
    if payload.get("live_orders_enabled") is not False:
        errors.append("live_orders_must_remain_disabled")
    if payload.get("private_api_used") is not False:
        errors.append("private_api_must_remain_unused")
    if require_open_session and (payload.get("market_session") or {}).get("state") != "open_live":
        errors.append("open_live_session_required")
    return {
        "passed": not errors,
        "require_open_session": require_open_session,
        "errors": errors,
    }


def write_report(path, payload):
    scan_status = payload.get("scan_status") or "ok"
    lines = [
        "# US Open Dynamic Scanner",
        "",
        f"- Generated at: `{payload['created_at']}`",
        f"- Scan status: `{scan_status}`",
        f"- Current tactical position: `{payload.get('current_tactical_position') or 'unknown'}`",
        f"- Deployable tactical position: `{payload.get('deployable_tactical_position') or 'none'}`",
        f"- Tactical selection: {payload.get('current_tactical_position_reason') or 'not available'}",
        f"- Protected long-term holdings: `{', '.join(payload.get('protected_long_term_holdings') or ['none'])}`",
        f"- US equity cash override: `{payload.get('us_equity_cash_usd')}`",
        f"- Live orders enabled: `{payload['live_orders_enabled']}`",
        f"- Research committee degraded: `{payload.get('research_committee_degraded')}`",
        f"- Max allowed action: `{payload.get('max_allowed_action')}`",
        f"- Research panel missing reason: `{payload.get('research_panel_missing_reason') or 'none'}`",
        "",
    ]

    relay = payload.get("tactical_rotation_relay") or {}
    if relay:
        lines.extend(
            [
                "## Tactical Rotation Relay",
                "",
                f"- State: `{relay.get('state')}`",
                f"- Source position: `{relay.get('source_position_to_sell') or 'none'}`",
                f"- Protected holdings respected: `{relay.get('protected_long_term_holdings_respected')}`",
                f"- Cash wait policy: `{relay.get('cash_wait_policy')}`",
                "",
            ]
        )

    holdings = payload.get("current_us_equity_holdings") or []
    if holdings:
        lines.extend(
            [
                "## Current US Equity Holdings",
                "",
                "| Symbol | Value | Qty | Bucket | Protected | Tactical Candidate |",
                "|---|---:|---:|---|---|---|",
            ]
        )
        for holding in holdings:
            lines.append(
                f"| {holding['symbol']} | {holding['market_value']:.2f} | {holding['quantity']:.2f} | "
                f"{holding.get('bucket') or 'unknown'} | {holding['is_protected_long_term']} | {holding['is_tactical_candidate']} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Dynamic Candidates",
            "",
        ]
    )
    if payload.get("degraded_no_actionable_trade"):
        lines.extend(
            [
                "Degraded scan note: public data quality was insufficient for an actionable tactical handoff.",
                "",
            ]
        )
    lines.extend(
        [
        "| Rank | Symbol | Price | Entry | Target | Stop | Sharpe 20/60 | IR60 | MDD60 | Path | Adj | Discovery | Setup | R/R | Action |",
        "|---:|---|---:|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---|",
        ]
    )
    for idx, c in enumerate(payload["candidates"], start=1):
        risk = c.get("risk_adjusted_path") or {}
        windows = risk.get("windows") or {}
        daily20 = windows.get("daily_20") or {}
        daily60 = windows.get("daily_60") or {}
        lines.append(
            f"| {idx} | {c['candidate_symbol']} | {c['price']:.2f} | {c['entry_zone']} | "
            f"{report_number(c.get('target_price'))} | {report_number(c.get('stop_loss'))} | "
            f"{daily20.get('sharpe')}/{daily60.get('sharpe')} | {daily60.get('information_ratio')} | "
            f"{daily60.get('max_drawdown_pct')}% | {risk.get('persistence_label')} | "
            f"{risk.get('ranking_adjustment_points')} | {c.get('open_scan_score_points')} | "
            f"{c.get('setup_quality_score')} | {report_number(c.get('reward_risk_ratio'))} | {c['monitor_recommendation']} |"
        )
    if not payload["candidates"]:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | - | - | no_deploy |")
    if payload.get("errors"):
        lines.extend(["", "## Errors", ""])
        for error in payload["errors"]:
            lines.append(f"- `{error.get('source')}`: {error.get('error')}")
    lines.extend(
        [
            "",
            "This scanner only produces handoff candidates. Manual confirmation is required before any real trade.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def scan_health(screener_ids, failed_screeners, seen, candidates):
    total_core_failure = bool(screener_ids) and len(failed_screeners) == len(screener_ids)
    degraded = total_core_failure or (not seen) or (not candidates)
    return {
        "total_core_failure": total_core_failure,
        "degraded_no_actionable_trade": degraded,
        "scan_status": "degraded" if degraded else ("partial" if failed_screeners else "ok"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--screeners", default=",".join(DEFAULT_SCREENERS))
    ap.add_argument("--count", type=int, default=DEFAULT_COUNT)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--max-symbols", type=int, default=DEFAULT_MAX_SYMBOLS)
    ap.add_argument("--request-timeout", type=int, default=DEFAULT_REQUEST_TIMEOUT)
    ap.add_argument("--current-tactical-symbol", default="")
    ap.add_argument("--risk-free-rate-pct", type=float)
    ap.add_argument("--risk-free-evidence-id")
    ap.add_argument("--output-root", default="active-alpha-paper-monitor")
    ap.add_argument("--external-agent-outputs-json", help="optional externally collected 6+ subagent output JSON")
    ap.add_argument("--json-only", action="store_true", help="print JSON only and do not write files")
    ap.add_argument("--discovery-only", action="store_true", help="return Top3 before validation/deep research")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--iterations", type=int, default=30)
    ap.add_argument("--candidate-count", type=int, default=25)
    ap.add_argument("--assert-runtime-contract", action="store_true")
    ap.add_argument("--require-open-session", action="store_true")
    ap.add_argument(
        "--request-mode",
        choices=("intraday_scalp", "tactical_1_7d"),
        default="tactical_1_7d",
    )
    args = ap.parse_args()

    if args.self_test:
        result = run_self_test(args.iterations, args.candidate_count)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ok" else 1

    run_started = time.monotonic()
    global REQUEST_TIMEOUT
    REQUEST_TIMEOUT = max(1, args.request_timeout)

    seen = {}
    screener_ids = [s.strip() for s in args.screeners.split(",") if s.strip()]
    errors = []
    failed_screeners = set()

    def fetch_screener(scr_id):
        try:
            return scr_id, yahoo_screener(
                scr_id,
                args.count,
                timeout=min(DISCOVERY_SCREENER_TIMEOUT_SECONDS, REQUEST_TIMEOUT),
            ), None
        except Exception as exc:  # noqa: BLE001
            return scr_id, [], str(exc)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(len(screener_ids), 8))
    ) as executor:
        for scr_id, quotes, error in executor.map(fetch_screener, screener_ids):
            if error:
                errors.append({"source": scr_id, "error": error})
                failed_screeners.add(scr_id)
                continue
            for quote in quotes:
                symbol = quote.get("symbol")
                if not symbol:
                    continue
                quote_type = str(quote.get("quoteType") or "").upper()
                if quote_type and quote_type not in {"EQUITY", "ETF"}:
                    continue
                bucket = seen.setdefault(symbol, {"quote": quote, "sources": set()})
                bucket["sources"].add(scr_id)

    selected_universe_items = sorted(
        seen.items(),
        key=lambda item: preliminary_quote_score(
            item[1]["quote"], item[1]["sources"]
        ),
        reverse=True,
    )[: max(args.max_symbols, args.top)]
    selected_universe = dict(selected_universe_items)
    spark_by_symbol = {}
    if selected_universe:
        try:
            spark_by_symbol, spark_errors = yahoo_spark_batched(
                selected_universe.keys(),
                batch_size=8,
                timeout=min(DISCOVERY_SPARK_TIMEOUT_SECONDS, REQUEST_TIMEOUT),
            )
            errors.extend(spark_errors)
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": "batch_5d_spark", "error": str(exc)})

    missing_spark_symbols = [
        symbol for symbol in selected_universe if symbol not in spark_by_symbol
    ]
    remaining_discovery_seconds = max(0.0, 14.0 - (time.monotonic() - run_started))
    if missing_spark_symbols and remaining_discovery_seconds >= 1.0:
        fallback_timeout = max(
            1.0,
            min(
                DISCOVERY_FALLBACK_TIMEOUT_SECONDS,
                remaining_discovery_seconds - 0.25,
            ),
        )
        fallback_symbols = missing_spark_symbols[
            : max(DISCOVERY_FALLBACK_MAX_SYMBOLS, args.top * 2)
        ]

        def fetch_fallback_vector(symbol):
            try:
                rows = yahoo_chart(
                    symbol,
                    range_="5d",
                    interval="1d",
                    timeout=fallback_timeout,
                )
                closes = [
                    numeric(row.get("close"))
                    for row in rows
                    if row.get("is_closed") is not False
                ]
                closes = [value for value in closes if value is not None and value > 0]
                bucket = selected_universe[symbol]
                quote = bucket["quote"]
                return symbol, {
                    "closes": closes,
                    "meta": {
                        "instrumentType": quote.get("quoteType"),
                        "regularMarketPrice": quote.get("regularMarketPrice"),
                        "regularMarketTime": quote.get("regularMarketTime"),
                    },
                    "price_as_of": iso_from_epoch(quote.get("regularMarketTime")),
                }, None
            except Exception as exc:  # noqa: BLE001
                return symbol, {}, str(exc)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, len(fallback_symbols))
        ) as executor:
            for symbol, value, error in executor.map(
                fetch_fallback_vector, fallback_symbols
            ):
                if value.get("closes"):
                    spark_by_symbol[symbol] = value
                if error:
                    errors.append(
                        {
                            "source": f"fallback_5d_chart:{symbol}",
                            "error": error,
                        }
                    )

    lightweight_metrics = []
    for symbol, bucket in selected_universe.items():
        metrics = discovery_metrics_from_quote(
            symbol,
            bucket["quote"],
            bucket["sources"],
            spark_by_symbol.get(symbol),
        )
        lightweight_metrics.append(
            (score_discovery_metrics(metrics), metrics, bucket["sources"])
        )
    discovery = rank_discovery_candidates(
        (
            to_discovery_candidate(metrics, sources, score)
            for score, metrics, sources in lightweight_metrics
        ),
        top_n=max(1, args.top),
    )
    discovery_elapsed = time.monotonic() - run_started
    discovery["elapsed_seconds"] = round(discovery_elapsed, 6)
    discovery["within_budget"] = discovery_elapsed <= discovery["budget_seconds"]
    if args.discovery_only:
        print(json.dumps(discovery, ensure_ascii=False, indent=2))
        return

    validation_started = time.monotonic()
    selected_symbols = [item["symbol"] for item in discovery["top_candidates"]]
    tactical = current_tactical_snapshot(args.current_tactical_symbol)
    current_symbol = tactical.get("current_tactical_position") or ""

    fetch_specs = [("benchmark", symbol) for symbol in ("SPY", "QQQ", "SMH")]
    fetch_specs.extend(("candidate", symbol) for symbol in selected_symbols)
    if current_symbol and current_symbol not in selected_symbols:
        fetch_specs.append(("current", current_symbol.upper()))
    if args.risk_free_rate_pct is None:
        fetch_specs.append(("risk_free", "^IRX"))

    def fetch_validation_chart(spec):
        kind, symbol = spec
        try:
            return kind, symbol, yahoo_chart(
                symbol,
                range_="5d" if kind == "risk_free" else "5y",
                interval="1d",
            ), None
        except Exception as exc:  # noqa: BLE001
            return kind, symbol, [], str(exc)

    validation_rows = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(len(fetch_specs), 12))
    ) as executor:
        for kind, symbol, rows, error in executor.map(fetch_validation_chart, fetch_specs):
            validation_rows[(kind, symbol)] = rows
            if error:
                errors.append({"source": f"{kind}:{symbol}", "error": error})

    benchmark_rows = {
        symbol: validation_rows.get(("benchmark", symbol), [])
        for symbol in ("SPY", "QQQ", "SMH")
    }
    risk_free_rate_pct = args.risk_free_rate_pct
    risk_free_evidence_id = args.risk_free_evidence_id
    if risk_free_rate_pct is None:
        risk_free_rows = [
            row
            for row in validation_rows.get(("risk_free", "^IRX"), [])
            if row.get("is_closed") is not False
        ]
        if risk_free_rows:
            risk_free_rate_pct = risk_free_rows[-1]["close"]
            risk_free_evidence_id = (
                risk_free_evidence_id
                or f"yahoo-irx-3m-tbill:{risk_free_rows[-1]['ts']}"
            )
        else:
            risk_free_rate_pct = 0.0
            errors.append(
                {"source": "risk_free:^IRX", "error": "closed_risk_free_bar_missing"}
            )

    current_metrics = None
    if current_symbol:
        current_rows = validation_rows.get(("current", current_symbol.upper()))
        if current_rows is None:
            current_rows = validation_rows.get(("candidate", current_symbol.upper()), [])
        try:
            current_metrics = compute_metrics(
                current_symbol.upper(),
                {"shortName": current_symbol.upper(), "quoteType": "EQUITY"},
                rows=current_rows or [],
                benchmark_rows=benchmark_rows.get("SPY") or [],
                benchmark_symbol="SPY",
                risk_free_rate_pct=risk_free_rate_pct,
                risk_free_evidence_id=risk_free_evidence_id,
                include_risk_adjusted_path=False,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": "current_tactical_symbol", "error": str(exc)})

    all_candidates = []
    for rank, symbol in enumerate(selected_symbols, start=1):
        bucket = selected_universe[symbol]
        sources = bucket["sources"]
        selected_benchmark = benchmark_symbol_for(bucket["quote"], sources)
        rows = validation_rows.get(("candidate", symbol), [])
        try:
            metrics = compute_metrics(
                symbol,
                bucket["quote"],
                rows=rows,
                benchmark_rows=benchmark_rows.get(selected_benchmark) or [],
                benchmark_symbol=selected_benchmark,
                risk_free_rate_pct=risk_free_rate_pct,
                risk_free_evidence_id=risk_free_evidence_id,
                sources=sources,
                include_risk_adjusted_path=False,
            )
        except Exception as exc:  # noqa: BLE001
            metrics = None
            errors.append({"source": symbol, "error": str(exc)})
        if not metrics or metrics.get("quote_type") not in (None, "EQUITY", "ETF"):
            continue
        score = score_candidate(metrics, sources, current_metrics=current_metrics)
        attach_risk_adjusted_path(
            metrics,
            rows,
            benchmark_rows=benchmark_rows.get(selected_benchmark) or [],
            benchmark_symbol=selected_benchmark,
            risk_free_rate_pct=risk_free_rate_pct,
            risk_free_evidence_id=risk_free_evidence_id,
            sources=sources,
        )
        candidate = build_candidate(
            metrics,
            sources,
            rank,
            current_symbol.upper(),
            current_metrics,
            args.request_mode,
        )
        current_price = numeric(candidate.get("price"))
        target_price = numeric(candidate.get("target_price"))
        stop_price = numeric(candidate.get("stop_loss"))
        target_return_pct = (
            (target_price / current_price - 1.0) * 100.0
            if current_price and target_price and target_price > current_price
            else 10.0
        )
        stop_loss_pct = (
            (1.0 - stop_price / current_price) * 100.0
            if current_price and stop_price and 0 < stop_price < current_price
            else 5.0
        )
        candidate["historical_comparison"] = historical_path_comparison(
            rows,
            target_return_pct=target_return_pct,
            stop_loss_pct=stop_loss_pct,
            horizon_bars=1 if args.request_mode == "intraday_scalp" else 5,
            evidence_id=f"yahoo-closed-5y:{symbol}",
        )
        candidate["open_scan_score_points"] = score
        all_candidates.append(candidate)
    apply_cross_sectional_adjustments(
        all_candidates,
        base_score_field="open_scan_score_points",
    )
    all_candidates.sort(
        key=lambda item: historical_rank_key(
            item,
            setup_score_field="open_scan_score_points",
        ),
        reverse=True,
    )
    candidates = []
    for rank, candidate in enumerate(all_candidates[: args.top], start=1):
        candidate["candidate_cash_relay_priority"] = rank
        if rank == 1:
            try:
                candidate["news"] = yahoo_news(candidate["symbol"], 3)
            except Exception as exc:  # noqa: BLE001
                candidate["news_error"] = str(exc)
        candidates.append(candidate)
    validation_timing = phase_budget_status(validation_started, "validation")
    validated_top1_candidate = candidates[0] if candidates else None
    validated_top1 = None
    if validated_top1_candidate is not None:
        validated_top1 = {
            "symbol": validated_top1_candidate.get("symbol"),
            "setup_quality_score": validated_top1_candidate.get("setup_quality_score"),
            "gap_repricing_guard": validated_top1_candidate.get("gap_repricing_guard"),
            "reward_risk_ratio": validated_top1_candidate.get("reward_risk_ratio"),
            "research_decision": "watch",
            "current_direct_decision": "do_not_enter_now",
            "selection_reason": "highest post-validation risk-adjusted setup quality among discovery Top3",
            "live_orders_enabled": False,
        }

    now = datetime.now(timezone.utc)
    local_now = now.astimezone(CN_TZ)
    day = local_now.date().isoformat()
    intraday_market_plan = (
        build_us_intraday_precheck(validated_top1_candidate, now)
        if args.request_mode == "intraday_scalp"
        else None
    )
    top1_decision_card = None
    if validated_top1_candidate is not None:
        top1_decision_card = build_us_top1_decision_card(
            validated_top1_candidate,
            completed_at=now,
            request_mode=args.request_mode,
            market_plan=intraday_market_plan,
            deployable_cash=tactical.get("us_equity_cash_usd"),
        )
    top1_card_timing = phase_budget_status(run_started, "deep_research")
    top1_card_timing["phase"] = "top1_card"
    market_session = market_session_context(
        now,
        (validated_top1_candidate or {}).get("price_as_of"),
    )
    health = scan_health(screener_ids, failed_screeners, seen, candidates)
    core_source_failed = health["total_core_failure"]
    degraded_no_actionable_trade = health["degraded_no_actionable_trade"]
    scan_status = "partial" if errors and not degraded_no_actionable_trade else health["scan_status"]
    payload = {
        "handoff_id": f"{day.replace('-', '')}-us-open-scan-001",
        "created_at": now.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "target_skill": "manual-investment-strategy-operator",
        "candidate_type": "us_open_scan",
        "request_mode": args.request_mode,
        "scan_status": scan_status,
        "degraded_no_actionable_trade": degraded_no_actionable_trade,
        "current_tactical_position": tactical.get("current_tactical_position"),
        "deployable_tactical_position": tactical.get("deployable_tactical_position"),
        "current_tactical_position_reason": tactical.get("selection_reason"),
        "current_tactical_position_source": tactical.get("selection_source"),
        "current_us_equity_holdings": tactical.get("us_equity_holdings") or [],
        "protected_long_term_holdings": tactical.get("protected_long_term_holdings") or [],
        "portfolio_ledger_path": tactical.get("ledger_path"),
        "position_overrides_path": tactical.get("position_overrides_path"),
        "us_equity_cash_usd": tactical.get("us_equity_cash_usd"),
        "schedule_window": "weekdays 22:00-24:00 Asia/Shanghai",
        "market_session": market_session,
        "market_cutoff": market_session.get("price_cutoff"),
        "private_api_keys_used": False,
        "private_api_used": False,
        "live_orders_enabled": False,
        "research_panel_missing": True,
        "research_panel_missing_reason": "subagent research committee is not invoked inside this local US-open scanner; output is watch-only until manual research committee review",
        "research_committee_degraded": True,
        "max_allowed_action": "watch",
        "requires_manual_review": True,
        "data_quality_status": "verified_yahoo_public_when_available",
        "risk_adjusted_path_policy": {
            "calculation_version": RISK_PATH_CALCULATION_VERSION,
            "promotion_status": "research_only_paper_only",
            "risk_free_rate_pct": risk_free_rate_pct,
            "risk_free_evidence_id": risk_free_evidence_id,
            "hard_sharpe_gt_3_gate": False,
            "live_gate_effect": "none_until_promotion",
        },
        "errors": errors[:20],
        "failed_core_sources": sorted(failed_screeners),
        "scan_health": health,
        "discovery_top3": discovery["top_candidates"],
        "discovery_blocked_candidates": discovery["blocked_candidates"],
        "deep_risk_candidate_symbols": selected_symbols,
        "validated_top1": validated_top1,
        "ranked_historical_comparison": ranked_historical_comparison(candidates),
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
        "runner_up_rejections": [
            {
                "symbol": item.get("symbol"),
                "reason": item.get("candidate_action_downgrade_reason")
                or "lower_post_validation_rank",
            }
            for item in candidates[1:3]
        ],
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
        "candidates": candidates,
        "tactical_rotation_relay": {
            "state": "holding_tactical_position",
            "source_position_to_sell": tactical.get("deployable_tactical_position"),
            "protected_long_term_holdings_respected": True,
            "cash_wait_policy": "same day preferred, otherwise 1-2 trading days max after manual review",
        },
        "decision_rule": (
            "A total core-source failure is degraded; one failed source preserves the available pool. "
            "Discovery uses no forecast probability, cash, committee, or execution permission. Manual must "
            "apply sample-tiered probability, conservative expected value, risk, cash, and human confirmation."
        ),
    }
    payload.update(
        active_research_panel_overlay(
            args.external_agent_outputs_json,
            payload["handoff_id"],
            "subagent research committee is not invoked inside this local US-open scanner; output is watch-only until manual research committee review",
            payload["committee_requirement"],
        )
    )
    payload["runtime_contract_audit"] = audit_runtime_contract(
        payload,
        require_open_session=args.require_open_session,
    )

    if args.json_only:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if (not args.assert_runtime_contract or payload["runtime_contract_audit"]["passed"]) else 1

    root = Path(args.output_root)
    handoff_dir = root / "handoffs"
    report_dir = root / "reports"
    experiment_dir = root / "experiments"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    experiment_dir.mkdir(parents=True, exist_ok=True)
    handoff_path = handoff_dir / f"{day}-us-open-handoff.json"
    report_path = report_dir / f"{day}-us-open-monitor.md"
    experiment_path = experiment_dir / f"{day.replace('-', '')}-us-open-dynamic-scanner.json"
    handoff_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    experiment_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(report_path, payload)
    print(json.dumps({"handoff": str(handoff_path), "report": str(report_path), "experiment": str(experiment_path), "candidate_count": len(candidates)}, indent=2))
    return 0 if (not args.assert_runtime_contract or payload["runtime_contract_audit"]["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main() or 0)
