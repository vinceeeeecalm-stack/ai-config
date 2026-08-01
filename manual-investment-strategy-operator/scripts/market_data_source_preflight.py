#!/usr/bin/env python3
"""Preflight market-data sources for manual dispatch reports.

The goal is not to collect a full report dataset. This script answers a more
basic question before a manual report is trusted: which market-data channels
are reachable now, which optional API keys are present, and which rail should
be treated as degraded.

Secrets are never printed. Environment variables are reported as booleans only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from typing import Any


TIMEOUT = 8
UA = "codex-investing-skill/market-data-preflight"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def fetch_json(url: str, headers: dict[str, str] | None = None, timeout: int = TIMEOUT) -> Any:
    req_headers = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str, headers: dict[str, str] | None = None, timeout: int = TIMEOUT) -> str:
    req_headers = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8")


def sanitize_error(exc: Exception) -> str:
    text = str(exc)
    for key_name in [
        "COINMARKETCAP_API_KEY",
        "TWELVEDATA_API_KEY",
        "ALPHA_VANTAGE_API_KEY",
        "FINNHUB_API_KEY",
        "BINANCE_API_KEY",
        "X_BEARER_TOKEN",
        "NEYNAR_API_KEY",
    ]:
        value = os.environ.get(key_name)
        if value:
            text = text.replace(value, "<redacted>")
    return text[:300]


def load_env_file(path: str | None) -> list[str]:
    """Load KEY=VALUE lines without logging values.

    Existing process environment values win. This lets a user keep secrets in
    an ignored .env.local file while still allowing CI/shell exports to override
    them.
    """

    if not path:
        return []
    loaded: list[str] = []
    candidate = os.path.expanduser(path)
    if not os.path.exists(candidate):
        return loaded
    with open(candidate, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key or not value:
                continue
            if key not in os.environ:
                os.environ[key] = value
            loaded.append(key)
    return loaded


def check_public_json(name: str, category: str, url: str, validator=None) -> dict[str, Any]:
    try:
        payload = fetch_json(url)
        ok = validator(payload) if validator else payload is not None
        return {
            "name": name,
            "category": category,
            "status": "ok" if ok else "degraded",
            "uses_private_api_key": False,
            "provider": name,
            "checked_at": utc_now(),
            "sample_fields": list(payload.keys())[:8] if isinstance(payload, dict) else type(payload).__name__,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "category": category,
            "status": "failed",
            "uses_private_api_key": False,
            "provider": name,
            "checked_at": utc_now(),
            "error": sanitize_error(exc),
        }


def check_public_text(name: str, category: str, url: str, validator=None) -> dict[str, Any]:
    try:
        payload = fetch_text(url)
        ok = validator(payload) if validator else bool(payload)
        return {
            "name": name,
            "category": category,
            "status": "ok" if ok else "degraded",
            "uses_private_api_key": False,
            "provider": name,
            "checked_at": utc_now(),
            "sample_fields": f"text_length={len(payload)}",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "category": category,
            "status": "failed",
            "uses_private_api_key": False,
            "provider": name,
            "checked_at": utc_now(),
            "error": sanitize_error(exc),
        }


def check_keyed_json(
    name: str,
    category: str,
    env_name: str,
    url_builder,
    header_builder=None,
    validator=None,
) -> dict[str, Any]:
    key_present = bool(os.environ.get(env_name))
    if not key_present:
        return {
            "name": name,
            "category": category,
            "status": "missing_key",
            "uses_private_api_key": False,
            "env_var_present": False,
            "env_var_name": env_name,
            "provider": name,
            "checked_at": utc_now(),
            "error": f"{env_name} not set",
        }
    try:
        key = os.environ[env_name]
        payload = fetch_json(url_builder(key), headers=header_builder(key) if header_builder else None)
        ok = validator(payload) if validator else payload is not None
        return {
            "name": name,
            "category": category,
            "status": "ok" if ok else "degraded",
            "uses_private_api_key": True,
            "env_var_present": True,
            "env_var_name": env_name,
            "provider": name,
            "checked_at": utc_now(),
            "sample_fields": list(payload.keys())[:8] if isinstance(payload, dict) else type(payload).__name__,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "category": category,
            "status": "failed",
            "uses_private_api_key": True,
            "env_var_present": True,
            "env_var_name": env_name,
            "provider": name,
            "checked_at": utc_now(),
            "error": sanitize_error(exc),
        }


def bls_cpi_has_rows(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    series_list = ((payload.get("Results") or {}).get("series") or [])
    for series in series_list:
        for row in series.get("data") or []:
            if str(row.get("period") or "").startswith("M") and row.get("value") not in (None, ""):
                return True
    return False


def build_preflight(args: argparse.Namespace, env_file_loaded_keys: list[str] | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    checks.append(check_public_json(
        "Binance spot ticker",
        "crypto_price_liquidity",
        "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT",
        lambda payload: bool(payload.get("lastPrice")) if isinstance(payload, dict) else False,
    ))
    checks.append(check_public_json(
        "CoinGecko ping",
        "crypto_cross_check",
        "https://api.coingecko.com/api/v3/ping",
        lambda payload: "gecko_says" in payload if isinstance(payload, dict) else False,
    ))
    checks.append(check_public_json(
        "DeFiLlama chains",
        "onchain_defi",
        "https://api.llama.fi/v2/chains",
        lambda payload: isinstance(payload, list) and bool(payload),
    ))
    checks.append(check_public_json(
        "Fear and Greed",
        "crypto_sentiment",
        "https://api.alternative.me/fng/?limit=1",
        lambda payload: bool(payload.get("data")) if isinstance(payload, dict) else False,
    ))
    checks.append(check_public_json(
        "Yahoo SPY chart",
        "us_equity_market",
        "https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=5d&interval=1d",
        lambda payload: bool(((payload.get("chart") or {}).get("result") or [])) if isinstance(payload, dict) else False,
    ))
    treasury_month = dt.datetime.now(dt.timezone.utc).strftime("%Y%m")
    treasury_url = (
        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?"
        + urllib.parse.urlencode({
            "data": "daily_treasury_yield_curve",
            "field_tdr_date_value_month": treasury_month,
        })
    )
    checks.append(check_public_text(
        "U.S. Treasury yield curve",
        "macro_official",
        treasury_url,
        lambda payload: ("BC_10YEAR" in payload or "d:BC_10YEAR" in payload)
        and ("BC_2YEAR" in payload or "d:BC_2YEAR" in payload),
    ))
    checks.append(check_public_json(
        "BLS CPI",
        "macro_official",
        "https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0?startyear=2025&endyear=2026",
        bls_cpi_has_rows,
    ))

    checks.append(check_keyed_json(
        "CoinMarketCap quotes",
        "crypto_cross_check",
        "COINMARKETCAP_API_KEY",
        lambda key: "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?symbol=BTC,ETH,SOL,ADA",
        lambda key: {"X-CMC_PRO_API_KEY": key},
        lambda payload: bool(payload.get("data")) if isinstance(payload, dict) else False,
    ))
    checks.append(check_keyed_json(
        "TwelveData quote",
        "us_equity_market",
        "TWELVEDATA_API_KEY",
        lambda key: "https://api.twelvedata.com/quote?" + urllib.parse.urlencode({"symbol": "SPY", "apikey": key}),
        None,
        lambda payload: bool(payload.get("close") or payload.get("price")) if isinstance(payload, dict) else False,
    ))
    checks.append(check_keyed_json(
        "Alpha Vantage global quote",
        "macro_and_us_equity_fallback",
        "ALPHA_VANTAGE_API_KEY",
        lambda key: "https://www.alphavantage.co/query?" + urllib.parse.urlencode({"function": "GLOBAL_QUOTE", "symbol": "QQQ", "apikey": key}),
        None,
        lambda payload: bool(payload.get("Global Quote")) if isinstance(payload, dict) else False,
    ))
    checks.append(check_keyed_json(
        "Finnhub quote",
        "us_equity_market",
        "FINNHUB_API_KEY",
        lambda key: "https://finnhub.io/api/v1/quote?" + urllib.parse.urlencode({"symbol": "SOXL", "token": key}),
        None,
        lambda payload: "c" in payload if isinstance(payload, dict) else False,
    ))

    by_category: dict[str, dict[str, Any]] = {}
    for item in checks:
        cat = item["category"]
        bucket = by_category.setdefault(cat, {"ok": 0, "failed": 0, "missing_key": 0, "degraded": 0, "sources": []})
        status = item.get("status")
        bucket[status if status in bucket else "degraded"] += 1
        bucket["sources"].append(item["name"])

    critical_categories = [
        "crypto_price_liquidity",
        "crypto_cross_check",
        "onchain_defi",
        "crypto_sentiment",
        "us_equity_market",
        "macro_official",
    ]
    missing_critical = [
        category
        for category in critical_categories
        if (by_category.get(category) or {}).get("ok", 0) <= 0
    ]

    api_env_presence = {
        "COINMARKETCAP_API_KEY": bool(os.environ.get("COINMARKETCAP_API_KEY")),
        "TWELVEDATA_API_KEY": bool(os.environ.get("TWELVEDATA_API_KEY")),
        "ALPHA_VANTAGE_API_KEY": bool(os.environ.get("ALPHA_VANTAGE_API_KEY")),
        "FINNHUB_API_KEY": bool(os.environ.get("FINNHUB_API_KEY")),
        "BINANCE_API_KEY": bool(os.environ.get("BINANCE_API_KEY")),
        "X_BEARER_TOKEN": bool(os.environ.get("X_BEARER_TOKEN")),
        "NEYNAR_API_KEY": bool(os.environ.get("NEYNAR_API_KEY")),
    }

    status = "verified" if not missing_critical else "degraded"
    return {
        "generated_at": utc_now(),
        "snapshot_type": "market_data_source_preflight",
        "data_quality": status,
        "live_orders_enabled": False,
        "private_api_keys_logged": False,
        "env_file_loaded": bool(env_file_loaded_keys),
        "env_file_keys_loaded": sorted(env_file_loaded_keys or []),
        "api_env_presence": api_env_presence,
        "checks": checks,
        "category_summary": by_category,
        "critical_categories": critical_categories,
        "missing_critical_categories": missing_critical,
        "recommendation": (
            "fresh_market_ready"
            if status == "verified"
            else "run_with_degraded_market_intelligence_or_retry_with_network_and_env_keys"
        ),
        "notes": [
            "This preflight never authorizes trading.",
            "missing_key only means the optional provider cannot be used; public fallbacks may still be enough.",
            "failed public endpoints usually indicate network/DNS/rate-limit problems and should degrade manual reports.",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Market Data Source Preflight",
        "",
        f"- Generated at: `{payload.get('generated_at')}`",
        f"- Data quality: `{payload.get('data_quality')}`",
        f"- Missing critical categories: `{', '.join(payload.get('missing_critical_categories') or []) or 'none'}`",
        "",
        "| Source | Category | Status | Key Present | Error |",
        "|---|---|---:|---:|---|",
    ]
    for item in payload.get("checks") or []:
        lines.append(
            f"| {item.get('name')} | {item.get('category')} | `{item.get('status')}` | "
            f"`{item.get('env_var_present', item.get('uses_private_api_key'))}` | {item.get('error', '')} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight manual market data sources without logging secrets")
    parser.add_argument("--env-file", help="Optional ignored local KEY=VALUE file such as .env.local; values are never logged")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()
    env_file_loaded_keys = load_env_file(args.env_file)
    payload = build_preflight(args, env_file_loaded_keys=env_file_loaded_keys)
    if args.format == "markdown":
        print(render_markdown(payload), end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
