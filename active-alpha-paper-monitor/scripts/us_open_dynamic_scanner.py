#!/usr/bin/env python3
"""
US open dynamic scanner.

Research/handoff only. It fetches public Yahoo screener/chart/search data,
builds Top 1-3 US equity candidates, and writes a handoff plus Markdown report
for manual-investment-strategy-operator. It never places orders.
"""

import argparse
import json
import math
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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


def yahoo_screener(scr_id, count=DEFAULT_COUNT):
    query = urllib.parse.urlencode({"scrIds": scr_id, "count": count})
    url = f"https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?{query}"
    body = fetch_json(url)
    result = body.get("finance", {}).get("result") or []
    quotes = []
    for section in result:
        quotes.extend(section.get("quotes") or [])
    return quotes


def yahoo_chart(symbol, range_="2mo", interval="1d"):
    query = urllib.parse.urlencode({"range": range_, "interval": interval})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{query}"
    body = fetch_json(url)
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
        score += min(market_value / 100.0, 50.0)
        score += min(float(holding.get("position_pressure_score_points") or 0.0) / 5.0, 20.0)
        score += min(float(holding.get("short_term_score_points") or 0.0) / 5.0, 20.0)
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
            }
        )

    deployable = None
    ranked = sorted(
        [item for item in us_equity if item["liquid_quantity"] > 0 and not item["is_protected_long_term"]],
        key=lambda item: (item["selection_score_points"], item["market_value"]),
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


def build_candidate(metrics, sources, rank, current_symbol, current_metrics):
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
    readiness = 45
    if metrics.get("vol_ratio") and metrics["vol_ratio"] >= 1.5:
        readiness += 12
    if metrics.get("1d_pct") and metrics["1d_pct"] > 8:
        readiness += 8
    if metrics.get("atr20_pct") and metrics["atr20_pct"] >= 5:
        readiness += 8
    if metrics.get("last_volume", 0) >= 1_000_000:
        readiness += 10
    if sources:
        readiness += 5
    readiness = min(readiness, 82)

    probability = min(68, max(42, int(readiness - 16)))
    if metrics.get("1d_pct", 0) > 20:
        probability -= 5
    if rank > 1:
        probability -= rank - 1

    current_label = current_symbol or "current_tactical_position"
    pre_research_grade = "watch" if probability < 80 or readiness < 80 else "small_probe_review"
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
    return {
        "candidate_symbol": metrics["symbol"],
        "symbol": metrics["symbol"],
        "candidate_type": "us_open_scan",
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
        "target_time_window": "1-5 trading days, max 10 trading days",
        "stop_loss": stop_loss,
        "latest_exit_date": (datetime.now(timezone.utc) + timedelta(days=10)).date().isoformat(),
        "forecast_probability_pct": int(probability),
        "execution_readiness_score": int(readiness),
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
            "manual skill must re-check double-80 and cash relay before any real action."
        ),
        "data_quality_status": "verified_yahoo_public_chart",
        "metrics": metrics,
        "risk_adjusted_path": metrics.get("risk_adjusted_path"),
        "news": [],
        "requires_manual_review": True,
        "live_orders_enabled": False,
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
        "| Rank | Symbol | Price | Entry | Target | Stop | Sharpe 20/60 | IR60 | MDD60 | Path | Adj | Prob | Readiness | Action |",
        "|---:|---|---:|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|",
        ]
    )
    for idx, c in enumerate(payload["candidates"], start=1):
        risk = c.get("risk_adjusted_path") or {}
        windows = risk.get("windows") or {}
        daily20 = windows.get("daily_20") or {}
        daily60 = windows.get("daily_60") or {}
        lines.append(
            f"| {idx} | {c['candidate_symbol']} | {c['price']:.2f} | {c['entry_zone']} | "
            f"{c['target_price']:.2f} | {c['stop_loss']:.2f} | "
            f"{daily20.get('sharpe')}/{daily60.get('sharpe')} | {daily60.get('information_ratio')} | "
            f"{daily60.get('max_drawdown_pct')}% | {risk.get('persistence_label')} | "
            f"{risk.get('ranking_adjustment_points')} | {c['forecast_probability_pct']} | "
            f"{c['execution_readiness_score']} | {c['monitor_recommendation']} |"
        )
    if not payload["candidates"]:
        lines.append("| - | - | - | - | - | - | - | - | - | - | - | - | - | no_deploy |")
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
    args = ap.parse_args()

    global REQUEST_TIMEOUT
    REQUEST_TIMEOUT = max(1, args.request_timeout)

    tactical = current_tactical_snapshot(args.current_tactical_symbol)
    current_symbol = tactical.get("current_tactical_position") or ""
    seen = {}
    screener_ids = [s.strip() for s in args.screeners.split(",") if s.strip()]
    errors = []
    failed_screeners = set()
    for scr_id in screener_ids:
        try:
            for quote in yahoo_screener(scr_id, args.count):
                symbol = quote.get("symbol")
                if not symbol:
                    continue
                if current_symbol and symbol.upper() == current_symbol.upper():
                    continue
                bucket = seen.setdefault(symbol, {"quote": quote, "sources": set()})
                bucket["sources"].add(scr_id)
                if len(seen) >= max(args.max_symbols, args.top):
                    break
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": scr_id, "error": str(exc)})
            failed_screeners.add(scr_id)
        if len(seen) >= max(args.max_symbols, args.top):
            break

    benchmark_rows = {}
    for benchmark_symbol in ("SPY", "QQQ", "SMH"):
        try:
            benchmark_rows[benchmark_symbol] = yahoo_chart(
                benchmark_symbol, range_="1y", interval="1d"
            )
        except Exception as exc:  # noqa: BLE001
            benchmark_rows[benchmark_symbol] = []
            errors.append({"source": f"benchmark:{benchmark_symbol}", "error": str(exc)})

    risk_free_rate_pct = args.risk_free_rate_pct
    risk_free_evidence_id = args.risk_free_evidence_id
    if risk_free_rate_pct is None:
        try:
            risk_free_rows = [
                row
                for row in yahoo_chart("^IRX", range_="5d", interval="1d")
                if row.get("is_closed") is not False
            ]
            risk_free_rate_pct = risk_free_rows[-1]["close"]
            risk_free_evidence_id = (
                risk_free_evidence_id
                or f"yahoo-irx-3m-tbill:{risk_free_rows[-1]['ts']}"
            )
        except Exception as exc:  # noqa: BLE001
            risk_free_rate_pct = 0.0
            errors.append({"source": "risk_free:^IRX", "error": str(exc)})

    current_metrics = None
    if current_symbol:
        try:
            current_metrics = compute_metrics(
                current_symbol.upper(),
                {"shortName": current_symbol.upper()},
                benchmark_rows=benchmark_rows.get("SPY") or [],
                benchmark_symbol="SPY",
                risk_free_rate_pct=risk_free_rate_pct,
                risk_free_evidence_id=risk_free_evidence_id,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": "current_tactical_symbol", "error": str(exc)})

    scored = []
    for symbol, bucket in seen.items():
        try:
            selected_benchmark = benchmark_symbol_for(bucket["quote"], bucket["sources"])
            metrics = compute_metrics(
                symbol,
                bucket["quote"],
                benchmark_rows=benchmark_rows.get(selected_benchmark) or [],
                benchmark_symbol=selected_benchmark,
                risk_free_rate_pct=risk_free_rate_pct,
                risk_free_evidence_id=risk_free_evidence_id,
                sources=bucket["sources"],
            )
            if not metrics or metrics.get("quote_type") not in (None, "EQUITY", "ETF"):
                continue
            score = score_candidate(metrics, bucket["sources"], current_metrics=current_metrics)
            scored.append((score, metrics, bucket["sources"]))
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": symbol, "error": str(exc)})

    scored.sort(key=lambda item: item[0], reverse=True)
    all_candidates = []
    for rank, (score, metrics, sources) in enumerate(scored, start=1):
        candidate = build_candidate(metrics, sources, rank, current_symbol.upper(), current_metrics)
        candidate["open_scan_score_points"] = score
        all_candidates.append(candidate)
    apply_cross_sectional_adjustments(
        all_candidates,
        base_score_field="open_scan_score_points",
    )
    all_candidates.sort(
        key=lambda item: item.get("open_scan_score_points") or 0,
        reverse=True,
    )
    candidates = []
    for rank, candidate in enumerate(all_candidates[: args.top], start=1):
        candidate["candidate_cash_relay_priority"] = rank
        try:
            candidate["news"] = yahoo_news(candidate["symbol"], 3)
        except Exception as exc:  # noqa: BLE001
            candidate["news_error"] = str(exc)
        candidates.append(candidate)

    now = datetime.now(timezone.utc)
    local_now = now.astimezone(CN_TZ)
    day = local_now.date().isoformat()
    core_source_failed = bool(failed_screeners)
    degraded_no_actionable_trade = core_source_failed or (not seen) or (not candidates)
    scan_status = "degraded" if degraded_no_actionable_trade else ("partial" if errors else "ok")
    payload = {
        "handoff_id": f"{day.replace('-', '')}-us-open-scan-001",
        "created_at": now.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "target_skill": "manual-investment-strategy-operator",
        "candidate_type": "us_open_scan",
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
        "private_api_keys_used": False,
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
        "candidates": candidates,
        "tactical_rotation_relay": {
            "state": "holding_tactical_position",
            "source_position_to_sell": tactical.get("deployable_tactical_position"),
            "protected_long_term_holdings_respected": True,
            "cash_wait_policy": "same day preferred, otherwise 1-2 trading days max after manual review",
        },
        "decision_rule": (
            "degraded_no_actionable_trade when core public screener sources fail; otherwise handoff only and manual "
            "skill must re-check current tactical position, cash relay, double-80 gate, and human confirmation"
        ),
    }
    payload.update(
        active_research_panel_overlay(
            args.external_agent_outputs_json,
            payload["handoff_id"],
            "subagent research committee is not invoked inside this local US-open scanner; output is watch-only until manual research committee review",
        )
    )

    if args.json_only:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

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


if __name__ == "__main__":
    main()
