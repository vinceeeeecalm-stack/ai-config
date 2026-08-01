#!/usr/bin/env python3
"""Build a public-source macro regime snapshot for manual reports.

This script fetches market-risk proxies and selected official macro series
without storing secrets. Optional API keys must be supplied through environment
variables and are never written to the output.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any


TIMEOUT = 18
YAHOO_SYMBOLS = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "SOXX": "SOXX",
    "SMH": "SMH",
    "VIX": "^VIX",
    "DXY": "DX-Y.NYB",
    "10Y_yahoo_proxy": "^TNX",
}
BINANCE_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def fetch_json(url: str, method: str = "GET", body: bytes | None = None, headers: dict[str, str] | None = None) -> Any:
    req_headers = {"User-Agent": "codex-investing-skill/1.0"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "codex-investing-skill/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].upper()


def pct_change(current: float | None, past: float | None) -> float | None:
    if current is None or past in (None, 0):
        return None
    return (current / past - 1.0) * 100.0


def parse_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def close_at_or_before(rows: list[dict[str, Any]], target_ts: int) -> float | None:
    candidates = [row for row in rows if row.get("time", 0) <= target_ts and row.get("close") is not None]
    if not candidates:
        return None
    return float(candidates[-1]["close"])


def summarize_yahoo(symbol: str, label: str, now_ts: int) -> dict[str, Any]:
    encoded = urllib.parse.quote(symbol, safe="")
    query = urllib.parse.urlencode({"range": "3mo", "interval": "1d", "includePrePost": "false"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?{query}"
    body = fetch_json(url)
    result = (body.get("chart", {}).get("result") or [{}])[0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    rows = []
    for idx, (ts, close) in enumerate(zip(timestamps, closes)):
        if close is None:
            continue
        volume = volumes[idx] if idx < len(volumes) else None
        rows.append({"time": int(ts), "close": float(close), "volume": volume})
    current = meta.get("regularMarketPrice") or (rows[-1]["close"] if rows else None)
    point = {
        "symbol": symbol,
        "source": "Yahoo Finance public chart",
        "data_quality": "verified" if current is not None else "missing",
        "price": float(current) if current is not None else None,
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName"),
        "timestamp": meta.get("regularMarketTime") or (rows[-1]["time"] if rows else None),
        "change_1d_pct": pct_change(float(current), close_at_or_before(rows, now_ts - 86400)) if current else None,
        "change_5d_pct": pct_change(float(current), close_at_or_before(rows, now_ts - 5 * 86400)) if current else None,
        "change_20d_pct": pct_change(float(current), close_at_or_before(rows, now_ts - 20 * 86400)) if current else None,
        "latest_volume": rows[-1].get("volume") if rows else None,
    }
    if label == "10Y_yahoo_proxy" and point["price"] is not None:
        point["yield_pct_estimate"] = point["price"] / 10.0 if point["price"] > 20 else point["price"]
        point["note"] = "Yahoo ^TNX convention can vary by feed; official Treasury curve is preferred when available."
    return point


def summarize_binance(symbol: str, label: str) -> dict[str, Any]:
    ticker_url = f"https://api.binance.com/api/v3/ticker/24hr?{urllib.parse.urlencode({'symbol': symbol})}"
    body = fetch_json(ticker_url)
    return {
        "symbol": symbol,
        "source": "Binance public 24hr ticker",
        "data_quality": "verified",
        "price": float(body["lastPrice"]),
        "change_24h_pct": float(body["priceChangePercent"]),
        "quote_volume_24h": float(body["quoteVolume"]),
        "timestamp": utc_now(),
    }


def fetch_treasury_yield_curve(as_of: dt.date) -> dict[str, Any]:
    months = [as_of, as_of - dt.timedelta(days=31)]
    errors: list[str] = []
    for month_date in months:
        month = month_date.strftime("%Y%m")
        query = urllib.parse.urlencode({
            "data": "daily_treasury_yield_curve",
            "field_tdr_date_value_month": month,
        })
        url = f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?{query}"
        try:
            xml_text = fetch_text(url)
            root = ET.fromstring(xml_text)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{month}: {exc}")
            continue
        rows: list[dict[str, Any]] = []
        for properties in root.iter():
            if local_name(properties.tag) != "PROPERTIES":
                continue
            row: dict[str, Any] = {}
            for child in list(properties):
                key = local_name(child.tag)
                text = child.text
                if text is None:
                    continue
                row[key] = text
            if row:
                rows.append(row)
        if not rows:
            continue
        rows = sorted(rows, key=lambda item: item.get("NEW_DATE", ""))
        latest = rows[-1]

        def yield_value(key: str) -> float | None:
            value = latest.get(key)
            if value in (None, ""):
                return None
            try:
                return float(value)
            except ValueError:
                return None

        three_month = yield_value("BC_3MONTH")
        two_year = yield_value("BC_2YEAR")
        ten_year = yield_value("BC_10YEAR")
        return {
            "source": "U.S. Treasury daily treasury yield curve XML",
            "source_url": url,
            "data_quality": "verified" if three_month is not None and two_year is not None and ten_year is not None else "degraded",
            "date": latest.get("NEW_DATE"),
            "yield_3m_pct": three_month,
            "yield_2y_pct": two_year,
            "yield_10y_pct": ten_year,
            "yield_10y_minus_2y_bps": round((ten_year - two_year) * 100.0, 2) if ten_year is not None and two_year is not None else None,
        }
    return {
        "source": "U.S. Treasury daily treasury yield curve XML",
        "data_quality": "missing",
        "errors": errors,
    }


def parse_bls_cpi_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    series = (payload.get("Results", {}).get("series") or [{}])[0]
    rows = series.get("data") or []
    if not rows:
        return None
    parsed = []
    for row in rows:
        period = row.get("period")
        if not period or not period.startswith("M"):
            continue
        value = parse_float(row.get("value"))
        if value is None:
            continue
        parsed.append({
            "year": int(row.get("year")),
            "period": period,
            "period_name": row.get("periodName"),
            "value": value,
            "latest": row.get("latest") == "true",
        })
    parsed = sorted(parsed, key=lambda item: (item["year"], item["period"]))
    latest = parsed[-1]
    prior_month = parsed[-2] if len(parsed) >= 2 else None
    same_month_prior_year = next(
        (
            item for item in parsed
            if item["year"] == latest["year"] - 1 and item["period"] == latest["period"]
        ),
        None,
    )
    return {
        "source": "BLS public API",
        "series": "CUSR0000SA0",
        "data_quality": "verified",
        "latest_period": f"{latest['year']}-{latest['period']}",
        "latest_value": latest["value"],
        "mom_pct": pct_change(latest["value"], prior_month["value"]) if prior_month else None,
        "yoy_pct": pct_change(latest["value"], same_month_prior_year["value"]) if same_month_prior_year else None,
        "last_updated_proxy": utc_now(),
    }


def fetch_bls_cpi(as_of: dt.date) -> dict[str, Any]:
    start_year = str(as_of.year - 2)
    end_year = str(as_of.year)
    attempts: list[dict[str, Any]] = []
    post_body = json.dumps({
        "seriesid": ["CUSR0000SA0"],
        "startyear": start_year,
        "endyear": end_year,
    }).encode("utf-8")
    post_url = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    get_url = (
        "https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0?"
        + urllib.parse.urlencode({"startyear": start_year, "endyear": end_year})
    )

    for method, url, body, headers in [
        ("POST", post_url, post_body, {"Content-Type": "application/json"}),
        ("GET", get_url, None, None),
    ]:
        try:
            payload = fetch_json(url, method=method, body=body, headers=headers)
            parsed = parse_bls_cpi_payload(payload) if isinstance(payload, dict) else None
            attempts.append({
                "method": method,
                "status": payload.get("status") if isinstance(payload, dict) else type(payload).__name__,
                "has_rows": parsed is not None,
            })
            if parsed:
                parsed["retrieval_method"] = method
                return parsed
        except Exception as exc:  # noqa: BLE001
            attempts.append({"method": method, "error": str(exc)[:240]})

    return {
        "source": "BLS public API",
        "series": "CUSR0000SA0",
        "data_quality": "missing",
        "attempts": attempts,
    }


def fetch_alpha_vantage_macro(api_key: str | None) -> dict[str, Any]:
    if not api_key:
        return {
            "private_api_key_used": False,
            "data_quality": "missing",
            "reason": "ALPHA_VANTAGE_API_KEY not set; using public fallback sources.",
        }
    base = "https://www.alphavantage.co/query"
    outputs: dict[str, Any] = {"private_api_key_used": True, "data_quality": "degraded"}
    endpoints = {
        "federal_funds_rate": {"function": "FEDERAL_FUNDS_RATE", "interval": "monthly"},
        "treasury_yield_10y": {"function": "TREASURY_YIELD", "interval": "monthly", "maturity": "10year"},
    }
    ok = 0
    for name, params in endpoints.items():
        query = dict(params)
        query["apikey"] = api_key
        try:
            body = fetch_json(f"{base}?{urllib.parse.urlencode(query)}")
            rows = body.get("data") or []
            latest = rows[0] if rows else None
            outputs[name] = {
                "source": "Alpha Vantage economic indicator",
                "data_quality": "verified" if latest else "missing",
                "latest": latest,
            }
            if latest:
                ok += 1
        except Exception as exc:  # noqa: BLE001
            outputs[name] = {"source": "Alpha Vantage economic indicator", "data_quality": "missing", "error": str(exc)}
    outputs["data_quality"] = "verified" if ok == len(endpoints) else ("degraded" if ok else "missing")
    return outputs


def normalize(value: float | None, low: float, high: float, invert: bool = False) -> float | None:
    if value is None:
        return None
    if math.isclose(high, low):
        return None
    score = (value - low) / (high - low)
    score = max(0.0, min(1.0, score))
    if invert:
        score = 1.0 - score
    return score * 100.0


def classify_regime(indicators: dict[str, Any], treasury: dict[str, Any], cpi: dict[str, Any]) -> dict[str, Any]:
    vix = indicators.get("VIX", {}).get("price")
    dxy_20d = indicators.get("DXY", {}).get("change_20d_pct")
    qqq_20d = indicators.get("QQQ", {}).get("change_20d_pct")
    soxx_20d = indicators.get("SOXX", {}).get("change_20d_pct")
    spy_20d = indicators.get("SPY", {}).get("change_20d_pct")
    ten_y = treasury.get("yield_10y_pct") or indicators.get("10Y_yahoo_proxy", {}).get("yield_pct_estimate")
    curve = treasury.get("yield_10y_minus_2y_bps")
    cpi_yoy = cpi.get("yoy_pct")
    btc_24h = indicators.get("BTC", {}).get("change_24h_pct")
    eth_24h = indicators.get("ETH", {}).get("change_24h_pct")

    components: list[dict[str, Any]] = []

    def add(name: str, value: float | None, score: float | None, note: str) -> None:
        components.append({"name": name, "value": value, "score_points": round(score, 2) if score is not None else None, "note": note})

    add("VIX risk pressure", vix, normalize(vix, 35, 12, invert=False), "Lower VIX is risk-on; high VIX slows tactical deployment.")
    add("DXY 20d pressure", dxy_20d, normalize(dxy_20d, 4, -4, invert=False), "Falling DXY supports risk assets; rising DXY reduces DCA aggression.")
    add("QQQ 20d trend", qqq_20d, normalize(qqq_20d, -8, 8), "Nasdaq trend proxy for risk appetite.")
    add("SOXX 20d trend", soxx_20d, normalize(soxx_20d, -12, 12), "Semiconductor trend proxy for SOXL/tactical beta.")
    add("SPY 20d trend", spy_20d, normalize(spy_20d, -6, 6), "Broad equity trend proxy.")
    add("10Y yield pressure", ten_y, normalize(ten_y, 5.2, 3.2, invert=False), "Lower long rates support duration/crypto beta.")
    add("10Y-2Y curve", curve, normalize(curve, -80, 80), "Less inverted/positive curve improves macro breadth.")
    add("CPI YoY pressure", cpi_yoy, normalize(cpi_yoy, 6.0, 2.0, invert=False), "Lower inflation supports easier policy expectations.")
    add("BTC 24h trend", btc_24h, normalize(btc_24h, -6, 6), "BTC trend proxy for crypto risk appetite.")
    add("ETH 24h trend", eth_24h, normalize(eth_24h, -6, 6), "ETH trend proxy for crypto beta.")

    valid_scores = [item["score_points"] for item in components if item["score_points"] is not None]
    score = sum(valid_scores) / len(valid_scores) if valid_scores else 50.0
    if score >= 70:
        classification = "risk_on"
        dca_pace = "normal"
        us_tactical_risk_posture = "allow_watch_or_conditional_after_candidate_gates"
    elif score >= 55:
        classification = "neutral_to_constructive"
        dca_pace = "normal"
        us_tactical_risk_posture = "conditional_only"
    elif score >= 40:
        classification = "neutral_to_caution"
        dca_pace = "split_more"
        us_tactical_risk_posture = "reduced_size_or_wait_for_pullback"
    else:
        classification = "risk_off"
        dca_pace = "wait_for_pullback"
        us_tactical_risk_posture = "watch_or_trim_only"

    return {
        "classification": classification,
        "risk_score_points": round(score, 2),
        "dca_pace": dca_pace,
        "us_tactical_risk_posture": us_tactical_risk_posture,
        "macro_affects_dca_pace_only": True,
        "components": components,
        "missing_component_count": len([item for item in components if item["score_points"] is None]),
    }


def build_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    generated_at = utc_now()
    now_ts = int(dt.datetime.now(dt.timezone.utc).timestamp())
    as_of_date = dt.datetime.now(dt.timezone.utc).date()
    indicators: dict[str, Any] = {}
    errors: list[dict[str, str]] = []

    for label, symbol in YAHOO_SYMBOLS.items():
        try:
            indicators[label] = summarize_yahoo(symbol, label, now_ts)
        except Exception as exc:  # noqa: BLE001
            indicators[label] = {"symbol": symbol, "source": "Yahoo Finance public chart", "data_quality": "missing", "error": str(exc)}
            errors.append({"source": f"Yahoo:{symbol}", "error": str(exc)})

    for label, symbol in BINANCE_SYMBOLS.items():
        try:
            indicators[label] = summarize_binance(symbol, label)
        except Exception as exc:  # noqa: BLE001
            indicators[label] = {"symbol": symbol, "source": "Binance public 24hr ticker", "data_quality": "missing", "error": str(exc)}
            errors.append({"source": f"Binance:{symbol}", "error": str(exc)})

    try:
        treasury = fetch_treasury_yield_curve(as_of_date)
    except Exception as exc:  # noqa: BLE001
        treasury = {"source": "U.S. Treasury daily treasury yield curve XML", "data_quality": "missing", "error": str(exc)}
        errors.append({"source": "U.S. Treasury", "error": str(exc)})

    try:
        cpi = fetch_bls_cpi(as_of_date)
    except Exception as exc:  # noqa: BLE001
        cpi = {"source": "BLS public API", "series": "CUSR0000SA0", "data_quality": "missing", "error": str(exc)}
        errors.append({"source": "BLS CPI", "error": str(exc)})

    alpha = fetch_alpha_vantage_macro(os.environ.get(args.alpha_vantage_env))
    regime = classify_regime(indicators, treasury, cpi)

    fund_flows = {
        "data_quality": "missing",
        "source": args.fund_flow_source or "not_supplied",
        "note": "ETF/fund flow source is not fetched by this script; supply an external fund-flow JSON or cite CoinShares/ETF sources in the report.",
    }

    missing = []
    if alpha.get("data_quality") == "missing":
        missing.append("Fed funds / rate expectation proxy from Alpha Vantage or CME FedWatch")
    if treasury.get("data_quality") == "missing":
        missing.append("official 10Y/2Y Treasury curve")
    if cpi.get("data_quality") == "missing":
        missing.append("latest CPI from BLS")
    missing.append("PCE and digital asset ETF/fund flows unless provided separately")
    if errors:
        missing.append("one or more market proxies failed; see source_errors")

    data_quality = "verified"
    if missing or regime.get("missing_component_count", 0) > 0:
        data_quality = "degraded" if len(missing) < 4 else "missing_or_degraded"

    return {
        "generated_at": generated_at,
        "private_api_keys_used": bool(alpha.get("private_api_key_used")),
        "live_orders_enabled": False,
        "snapshot_type": "macro_regime",
        "data_quality": data_quality,
        "macro_regime": regime,
        "indicators": indicators,
        "treasury_yield_curve": treasury,
        "cpi": cpi,
        "alpha_vantage_macro": {
            key: value for key, value in alpha.items()
            if key != "private_api_key_used"
        },
        "fund_flows": fund_flows,
        "missing_data": missing,
        "source_errors": errors,
        "downgrade_implications": [
            {
                "category": "macro_regime",
                "status": data_quality,
                "impact": "dca pace only",
                "reason": "Macro data can slow or split DCA and tactical sizing, but cannot trigger a single-asset strong buy by itself.",
            },
            {
                "category": "fund_flows",
                "status": fund_flows["data_quality"],
                "impact": "conditional only",
                "reason": "ETF/fund-flow confirmation is missing; do not upgrade crypto DCA based on flows.",
            },
        ],
    }


def render_markdown(snapshot: dict[str, Any]) -> str:
    regime = snapshot.get("macro_regime", {})
    lines = [
        "### Macro Regime Snapshot",
        f"- Regime: `{regime.get('classification')}` / score `{regime.get('risk_score_points')}`",
        f"- DCA pace: `{regime.get('dca_pace')}`; US tactical posture: `{regime.get('us_tactical_risk_posture')}`",
        f"- Data quality: `{snapshot.get('data_quality')}`; generated at `{snapshot.get('generated_at')}`",
    ]
    for key in ["VIX", "DXY", "QQQ", "SOXX", "SPY", "BTC", "ETH"]:
        item = snapshot.get("indicators", {}).get(key, {})
        if item:
            change = item.get("change_20d_pct", item.get("change_24h_pct"))
            lines.append(f"- {key}: `{item.get('price')}` change `{change}` quality `{item.get('data_quality')}`")
    treasury = snapshot.get("treasury_yield_curve", {})
    lines.append(f"- Treasury: 3M `{treasury.get('yield_3m_pct')}`, 10Y `{treasury.get('yield_10y_pct')}`, 2Y `{treasury.get('yield_2y_pct')}`, curve `{treasury.get('yield_10y_minus_2y_bps')}bps`, quality `{treasury.get('data_quality')}`")
    cpi = snapshot.get("cpi", {})
    lines.append(f"- CPI: YoY `{cpi.get('yoy_pct')}`, MoM `{cpi.get('mom_pct')}`, period `{cpi.get('latest_period')}`, quality `{cpi.get('data_quality')}`")
    if snapshot.get("missing_data"):
        lines.append("- Missing/degraded: " + "; ".join(snapshot["missing_data"]))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build macro regime snapshot from public sources")
    parser.add_argument("--alpha-vantage-env", default="ALPHA_VANTAGE_API_KEY", help="Environment variable name for optional Alpha Vantage key")
    parser.add_argument("--fund-flow-source", help="Optional text label for externally supplied fund-flow source")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    snapshot = build_snapshot(args)
    if args.format == "markdown":
        sys.stdout.write(render_markdown(snapshot))
    else:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
