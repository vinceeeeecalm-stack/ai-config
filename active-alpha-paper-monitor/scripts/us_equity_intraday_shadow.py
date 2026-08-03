#!/usr/bin/env python3
"""Deterministic Stage-0 US equity premarket/intraday shadow engine.

Alpaca is invoked by the Codex connector, not by this process.  This module
accepts only a secret-safe evidence handoff, classifies the feed honestly, and
builds a complete shadow funnel.  It cannot change production actions or place
orders.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCHEMA = "USEquityIntradayExplosiveEngineV1"
STRATEGY_VERSION = "us-equity-intraday-explosive-opportunity-v1-stage0-shadow"
FORMAL_ACTIONS = {"ENTER_NOW", "WAIT_FOR_ENTRY", "NO_TRADE"}
DATA_GRADES = {"CONSOLIDATED_REALTIME", "IEX_CROSS_VERIFIED", "IEX_ONLY", "STALE_OR_CONFLICTED"}
BINDING_FIELDS = (
    "snapshot_id", "strategy_version", "config_digest", "source_digest",
    "market_session", "price_as_of", "decision_valid_until",
)
FORBIDDEN_KEYS = {
    "api_key", "apikey", "secret", "token", "access_token", "refresh_token",
    "private_key", "password", "account", "account_id", "order", "credential",
    "connection_metadata", "authorization",
}
ALLOWED_EXCHANGES = {"NASDAQ", "NYSE", "NYSEARCA", "NYSEAMERICAN", "AMEX"}
ALLOWED_ASSET_CLASSES = {"COMMON_STOCK", "ETF"}
NY = ZoneInfo("America/New_York")
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "us_equity_intraday_shadow_v1.json"
_ADAPTER_AUTHORITY = object()


class USEquityShadowError(ValueError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_config(value: dict[str, Any] | None = None) -> dict[str, Any]:
    authoritative = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    if value is not None and (not isinstance(value, dict) or canonical(value) != canonical(authoritative)):
        raise USEquityShadowError("config_digest_not_authoritative")
    config = authoritative
    if config.get("schema_version") != "USEquityIntradayShadowConfigV1":
        raise USEquityShadowError("invalid_shadow_config_schema")
    for field, expected in (
        ("production_rule_changed", False), ("formal_action_eligible", False),
        ("live_orders_enabled", False), ("private_api_used", False),
        ("human_confirmation_required", True),
    ):
        if config.get(field) is not expected:
            raise USEquityShadowError(f"unsafe_shadow_config:{field}")
    if float(config.get("minimum_price_usd", 0)) < 5.0:
        raise USEquityShadowError("minimum_price_cannot_be_loosened")
    if float(config.get("actual_risk_cap_pct", 999)) > 0.5:
        raise USEquityShadowError("risk_cap_cannot_exceed_0_5")
    return config


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _time(value: Any, field: str) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise USEquityShadowError(f"missing:{field}")
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise USEquityShadowError(f"invalid_time:{field}") from exc
    if parsed.tzinfo is None:
        raise USEquityShadowError(f"timezone_required:{field}")
    return parsed.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any, field: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise USEquityShadowError(f"invalid_number:{field}")
    return float(value)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise USEquityShadowError(f"missing:{field}")
    return value.strip()


def scan_forbidden(value: Any, prefix: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_KEYS:
                raise USEquityShadowError(f"forbidden_field:{prefix}{key}")
            scan_forbidden(child, f"{prefix}{key}.")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_forbidden(child, f"{prefix}{index}.")


def market_session(as_of: dt.datetime) -> str:
    local = as_of.astimezone(NY)
    minute = local.hour * 60 + local.minute
    if 240 <= minute <= 569:
        return "PREMARKET"
    if 570 <= minute <= 955:
        return "REGULAR"
    return "CLOSED"


def _get(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _bar(raw: dict[str, Any] | None, field: str) -> dict[str, Any] | None:
    if not raw:
        return None
    ts = _get(raw, "timestamp", "t", "time")
    result = {
        "timestamp": _iso(_time(ts, f"{field}.timestamp")),
        "open": _number(_get(raw, "open", "o"), f"{field}.open"),
        "high": _number(_get(raw, "high", "h"), f"{field}.high"),
        "low": _number(_get(raw, "low", "l"), f"{field}.low"),
        "close": _number(_get(raw, "close", "c"), f"{field}.close"),
        "volume": _number(_get(raw, "volume", "v"), f"{field}.volume"),
        "vwap": _number(_get(raw, "vwap", "vw"), f"{field}.vwap", allow_none=True),
        "trade_count": _number(_get(raw, "trade_count", "n"), f"{field}.trade_count", allow_none=True),
    }
    if min(result["open"], result["high"], result["low"], result["close"]) <= 0:
        raise USEquityShadowError(f"nonpositive_bar:{field}")
    if result["low"] > result["high"] or not result["low"] <= result["close"] <= result["high"]:
        raise USEquityShadowError(f"invalid_ohlc:{field}")
    return result


def _bars(raw: Any, field: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise USEquityShadowError(f"invalid_list:{field}")
    result = [_bar(item, f"{field}[{index}]") for index, item in enumerate(raw)]
    return [item for item in result if item]


def _closed_bars(raw: Any, field: str, *, received_at: dt.datetime, interval_minutes: int) -> list[dict[str, Any]]:
    result = []
    for item in _bars(raw, field):
        opened = _time(item["timestamp"], f"{field}.timestamp")
        if opened + dt.timedelta(minutes=interval_minutes) <= received_at:
            result.append(item)
    return result


def _decode_plugin_result(value: Any, field: str) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("structuredContent"), dict):
        if value.get("isError") is True:
            raise USEquityShadowError(f"plugin_call_failed:{field}")
        value = value["structuredContent"].get("result")
    elif isinstance(value, dict) and isinstance(value.get("content"), list):
        text_blocks = [item.get("text") for item in value["content"] if isinstance(item, dict) and item.get("type") == "text"]
        if text_blocks:
            value = text_blocks[0]
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise USEquityShadowError(f"invalid_plugin_json:{field}") from exc
    if not isinstance(value, dict):
        raise USEquityShadowError(f"invalid_plugin_result:{field}")
    scan_forbidden(value)
    return value


def adapt_alpaca_plugin_evidence(
    snapshot_result: dict[str, Any] | str,
    bars_1m_result: dict[str, Any] | str,
    bars_5m_result: dict[str, Any] | str,
    *, symbol: str, received_at: str, second_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract one symbol from the connector's structured result and whitelist it."""
    target = _text(symbol, "symbol").upper()
    snapshot_outer = _decode_plugin_result(snapshot_result, "snapshot_result")
    one_minute_outer = _decode_plugin_result(bars_1m_result, "bars_1m_result")
    five_minute_outer = _decode_plugin_result(bars_5m_result, "bars_5m_result")
    snapshots = snapshot_outer.get("snapshots")
    if isinstance(snapshots, dict):
        snapshot = snapshots.get(target)
    elif isinstance(snapshot_outer.get(target), dict):
        snapshot = snapshot_outer.get(target)
    else:
        snapshot = snapshot_outer
    if not isinstance(snapshot, dict):
        raise USEquityShadowError("plugin_snapshot_symbol_missing")
    bars1 = one_minute_outer.get("bars", {}).get(target) if isinstance(one_minute_outer.get("bars"), dict) else one_minute_outer.get(target, one_minute_outer.get("bars_1m"))
    bars5 = five_minute_outer.get("bars", {}).get(target) if isinstance(five_minute_outer.get("bars"), dict) else five_minute_outer.get(target, five_minute_outer.get("bars_5m"))
    request = snapshot_outer.get("request") if isinstance(snapshot_outer.get("request"), dict) else {}
    feed = str(request.get("feed") or "iex").lower()
    merged = dict(snapshot)
    merged["bars_1m"] = bars1 or []
    merged["bars_5m"] = bars5 or []
    verified_second = _adapt_second_source(second_source, target) if second_source is not None else None
    if feed == "sip":
        raise USEquityShadowError("sip_not_available_in_stage0")
    return sanitize_alpaca_snapshot(
        merged, symbol=target, feed=feed, received_at=received_at,
        second_source=verified_second,
        _authority_token=_ADAPTER_AUTHORITY,
    )


def _adapt_second_source(value: Any, symbol: str) -> dict[str, Any]:
    if not (
        isinstance(value, dict)
        and value.get("isError") is not True
        and isinstance(value.get("structuredContent"), dict)
        and isinstance(value["structuredContent"].get("result"), str)
    ):
        raise USEquityShadowError("second_source_requires_connector_envelope")
    body = _decode_plugin_result(value, "second_source")
    if body.get("tool") != "public_price_quote" or str(body.get("symbol") or "").upper() != symbol:
        raise USEquityShadowError("second_source_connector_identity_invalid")
    if body.get("source") not in {"Nasdaq public quote", "NYSE public quote", "Stooq public quote", "Yahoo Finance public quote"}:
        raise USEquityShadowError("second_source_provider_not_allowed")
    return {"source": body["source"], "price": body.get("price"), "as_of": body.get("as_of")}


def sanitize_alpaca_snapshot(
    raw: dict[str, Any], *, symbol: str, feed: str, received_at: str,
    second_source: dict[str, Any] | None = None,
    _authority_token: object | None = None,
) -> dict[str, Any]:
    """Convert an Alpaca connector result to the secret-safe whitelist contract."""
    scan_forbidden(raw)
    scan_forbidden(second_source or {})
    received = _time(received_at, "received_at")
    feed_norm = _text(feed, "feed").lower()
    if feed_norm not in {"iex", "sip"}:
        raise USEquityShadowError("unsupported_feed")
    if feed_norm == "sip":
        raise USEquityShadowError("sip_not_available_in_stage0")
    if second_source is not None and _authority_token is not _ADAPTER_AUTHORITY:
        raise USEquityShadowError("second_source_requires_connector_envelope")
    trade_raw = _get(raw, "latest_trade", "latestTrade", "trade") or {}
    quote_raw = _get(raw, "latest_quote", "latestQuote", "quote") or {}
    trade_price = _number(_get(trade_raw, "price", "p"), "latest_trade.price")
    trade_time = _iso(_time(_get(trade_raw, "timestamp", "t"), "latest_trade.timestamp"))
    bid = _number(_get(quote_raw, "bid_price", "bp"), "latest_quote.bid_price")
    ask = _number(_get(quote_raw, "ask_price", "ap"), "latest_quote.ask_price")
    if min(trade_price, bid, ask) <= 0 or ask < bid:
        raise USEquityShadowError("invalid_quote")
    quote_time = _iso(_time(_get(quote_raw, "timestamp", "t"), "latest_quote.timestamp"))
    second = None
    if second_source:
        second = {
            "source": _text(second_source.get("source"), "second_source.source"),
            "price": _number(second_source.get("price"), "second_source.price"),
            "as_of": _iso(_time(second_source.get("as_of"), "second_source.as_of")),
        }
    output = {
        "schema_version": "AlpacaMarketSnapshotV1",
        "symbol": _text(symbol, "symbol").upper(),
        "feed": feed_norm.upper(),
        "received_at": _iso(received),
        "latest_trade": {"price": trade_price, "timestamp": trade_time},
        "latest_quote": {
            "bid_price": bid,
            "ask_price": ask,
            "bid_size": _number(_get(quote_raw, "bid_size", "bs"), "latest_quote.bid_size", allow_none=True),
            "ask_size": _number(_get(quote_raw, "ask_size", "as"), "latest_quote.ask_size", allow_none=True),
            "timestamp": quote_time,
        },
        "minute_bar": _bar(_get(raw, "minute_bar", "minuteBar"), "minute_bar"),
        "daily_bar": _bar(_get(raw, "daily_bar", "dailyBar"), "daily_bar"),
        "previous_daily_bar": _bar(_get(raw, "previous_daily_bar", "prevDailyBar"), "previous_daily_bar"),
        "bars_1m": _closed_bars(_get(raw, "bars_1m", "bars1m"), "bars_1m", received_at=received, interval_minutes=1),
        "bars_5m": _closed_bars(_get(raw, "bars_5m", "bars5m"), "bars_5m", received_at=received, interval_minutes=5),
        "second_source": second,
        "source_provenance": ["Public Equity Investing plugin", f"Alpaca {feed_norm.upper()}"],
        "raw_payload_persisted": False,
        "credential_material_persisted": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    output["data_grade"] = classify_data_grade(output, received)
    output["snapshot_digest"] = digest({k: output[k] for k in output if k != "snapshot_digest"})
    validate_alpaca_snapshot(output)
    return output


def classify_data_grade(snapshot: dict[str, Any], as_of: dt.datetime) -> str:
    trade_at = _time(snapshot["latest_trade"]["timestamp"], "latest_trade.timestamp")
    quote_at = _time(snapshot["latest_quote"]["timestamp"], "latest_quote.timestamp")
    trade_age = (as_of - trade_at).total_seconds()
    quote_age = (as_of - quote_at).total_seconds()
    if min(trade_age, quote_age) < -5 or max(trade_age, quote_age) > 60:
        return "STALE_OR_CONFLICTED"
    second = snapshot.get("second_source")
    cross_ok = False
    if second:
        second_at = _time(second["as_of"], "second_source.as_of")
        primary = float(snapshot["latest_trade"]["price"])
        difference = abs(float(second["price"]) / primary - 1.0) * 100.0
        second_age = (as_of - second_at).total_seconds()
        if second_age < -5 or second_age > 60 or difference > 1.0:
            return "STALE_OR_CONFLICTED"
        cross_ok = True
    if snapshot.get("feed") == "SIP":
        return "CONSOLIDATED_REALTIME"
    return "IEX_CROSS_VERIFIED" if cross_ok else "IEX_ONLY"


def validate_alpaca_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "AlpacaMarketSnapshotV1":
        raise USEquityShadowError("schema:AlpacaMarketSnapshotV1")
    if record.get("data_grade") not in DATA_GRADES:
        raise USEquityShadowError("invalid_data_grade")
    if record.get("feed") not in {"IEX", "SIP"}:
        raise USEquityShadowError("invalid_feed")
    if record.get("raw_payload_persisted") is not False or record.get("credential_material_persisted") is not False:
        raise USEquityShadowError("unsafe_persistence")
    if record.get("live_orders_enabled") is not False or record.get("private_api_used") is not False:
        raise USEquityShadowError("execution_boundary")
    scan_forbidden(record)
    return record


def _weighted_vwap(bars: list[dict[str, Any]]) -> float | None:
    total_volume = sum(float(item["volume"]) for item in bars)
    if total_volume <= 0:
        return None
    return sum((float(item.get("vwap") or item["close"])) * float(item["volume"]) for item in bars) / total_volume


def _trade_acceleration(bars: list[dict[str, Any]]) -> float | None:
    counts = [float(item["trade_count"]) for item in bars if item.get("trade_count") is not None]
    if len(counts) < 4:
        return None
    half = len(counts) // 2
    before = sum(counts[:half]) / max(half, 1)
    after = sum(counts[half:]) / max(len(counts) - half, 1)
    return after / before if before > 0 else None


def _opening_range(bars: list[dict[str, Any]], count: int) -> dict[str, float] | None:
    if len(bars) < count:
        return None
    subset = bars[:count]
    return {"high": max(float(item["high"]) for item in subset), "low": min(float(item["low"]) for item in subset)}


def _current_session_bars(bars: list[dict[str, Any]], *, as_of: dt.datetime, session: str, interval_minutes: int) -> list[dict[str, Any]]:
    local_as_of = as_of.astimezone(NY)
    start_minute = 240 if session == "PREMARKET" else 570
    output = []
    previous_opened = None
    seen = set()
    for item in bars:
        opened = _time(item["timestamp"], "bar.timestamp")
        if opened in seen or (previous_opened is not None and opened <= previous_opened):
            raise USEquityShadowError("bar_sequence_not_strictly_increasing")
        if opened.minute % interval_minutes != 0 or opened.second != 0 or opened.microsecond != 0:
            raise USEquityShadowError("bar_interval_alignment_invalid")
        seen.add(opened)
        previous_opened = opened
        local_open = opened.astimezone(NY)
        minute = local_open.hour * 60 + local_open.minute
        if local_open.date() != local_as_of.date() or minute < start_minute:
            continue
        if opened + dt.timedelta(minutes=interval_minutes) <= as_of:
            output.append(item)
    return output


def build_factor_snapshot(candidate: dict[str, Any], market: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    validate_alpaca_snapshot(market)
    symbol = _text(candidate.get("symbol"), "candidate.symbol").upper()
    if symbol != market["symbol"]:
        raise USEquityShadowError("symbol_binding_mismatch")
    session = common["market_session"]
    as_of = _time(common["price_as_of"], "price_as_of")
    bars = _current_session_bars(market["bars_1m"], as_of=as_of, session=session, interval_minutes=1)
    bars_5m = _current_session_bars(market["bars_5m"], as_of=as_of, session=session, interval_minutes=5)
    session_volume = sum(float(item["volume"]) for item in bars)
    session_dollar_volume = sum(float(item["volume"]) * float(item.get("vwap") or item["close"]) for item in bars)
    baseline = _number(candidate.get("same_time_volume_baseline"), "same_time_volume_baseline", allow_none=True)
    relative_volume = session_volume / baseline if baseline and baseline > 0 else None
    bid = float(market["latest_quote"]["bid_price"])
    ask = float(market["latest_quote"]["ask_price"])
    mid = (bid + ask) / 2.0
    result = {
        "schema_version": "USEquityPremarketFactorSnapshotV1" if session == "PREMARKET" else "USEquityIntradayFactorSnapshotV1",
        **common,
        "symbol": symbol,
        "data_grade": market["data_grade"],
        "current_price": float(market["latest_trade"]["price"]),
        "spread_bps": (ask - bid) / mid * 10000.0,
        "session_volume": session_volume,
        "session_dollar_volume": session_dollar_volume,
        "relative_volume": relative_volume,
        "trade_count_acceleration": _trade_acceleration(bars),
        "session_vwap": _weighted_vwap(bars),
        "opening_range_5m": _opening_range(bars, 5),
        "opening_range_15m": _opening_range(bars, 15),
        "closed_1m_count": len(bars),
        "closed_5m_count": len(bars_5m),
        "sector": _text(candidate.get("sector") or "UNKNOWN", "candidate.sector"),
        "sector_relative_strength_pct": float(_number(candidate.get("sector_change_pct", 0.0), "sector_change_pct") or 0.0) - float(_number(candidate.get("benchmark_change_pct", 0.0), "benchmark_change_pct") or 0.0),
        "symbol_relative_strength_pct": float(_number(candidate.get("symbol_change_pct", 0.0), "symbol_change_pct") or 0.0) - float(_number(candidate.get("benchmark_change_pct", 0.0), "benchmark_change_pct") or 0.0),
        "breakout_confirmed": candidate.get("breakout_confirmed") is True,
        "first_retest_confirmed": candidate.get("first_retest_confirmed") is True,
        "higher_lows": candidate.get("higher_lows") is True,
        "catalyst": candidate.get("catalyst") if isinstance(candidate.get("catalyst"), dict) else {},
        "valuation": candidate.get("valuation") if isinstance(candidate.get("valuation"), dict) else {},
        "capital_risk": candidate.get("capital_risk") if isinstance(candidate.get("capital_risk"), dict) else {},
        "regression": candidate.get("regression") if isinstance(candidate.get("regression"), dict) else {},
        "formal_action_eligible": False,
        "production_rule_changed": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    validate_factor_snapshot(result)
    return result


def validate_binding(record: dict[str, Any]) -> None:
    for field in BINDING_FIELDS:
        _text(record.get(field), field)
    if record["market_session"] not in {"PREMARKET", "REGULAR"}:
        raise USEquityShadowError("invalid_market_session")
    _time(record["price_as_of"], "price_as_of")
    _time(record["decision_valid_until"], "decision_valid_until")


def validate_universe_audit(record: Any, *, as_of: dt.datetime, candidate_count: int) -> list[str]:
    blockers = []
    if not isinstance(record, dict) or record.get("schema_version") != "USEquityDynamicUniverseAuditV1":
        return ["dynamic_full_market_universe_unverified"]
    exchanges = {str(item).upper() for item in (record.get("exchanges") or [])}
    if not {"NASDAQ", "NYSE"}.issubset(exchanges) or not ({"AMEX", "NYSEAMERICAN"} & exchanges):
        blockers.append("required_exchange_coverage_missing")
    if int(record.get("active_asset_count") or 0) < 1000:
        blockers.append("active_asset_universe_too_small")
    if int(record.get("screened_candidate_count") or 0) < candidate_count:
        blockers.append("screened_candidate_count_inconsistent")
    methods = {str(item) for item in (record.get("discovery_methods") or [])}
    required_methods = {"unusual_volume", "day_gainers", "sector_rotation", "news_catalyst"}
    if not required_methods.issubset(methods):
        blockers.append("dynamic_discovery_method_coverage_missing")
    try:
        completed = _time(record.get("completed_at"), "universe_audit.completed_at")
        age = (as_of - completed).total_seconds()
        if age < -5 or age > 120:
            blockers.append("dynamic_universe_audit_stale_or_future")
    except USEquityShadowError:
        blockers.append("dynamic_universe_audit_time_invalid")
    if record.get("static_symbol_whitelist_used") is not False:
        blockers.append("static_symbol_whitelist_forbidden")
    return sorted(set(blockers))


def validate_factor_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    expected = "USEquityPremarketFactorSnapshotV1" if record.get("market_session") == "PREMARKET" else "USEquityIntradayFactorSnapshotV1"
    if record.get("schema_version") != expected:
        raise USEquityShadowError(f"schema:{expected}")
    validate_binding(record)
    if record.get("formal_action_eligible") is not False or record.get("production_rule_changed") is not False:
        raise USEquityShadowError("stage0_isolation")
    scan_forbidden(record)
    return record


def _candidate_identity_blockers(candidate: dict[str, Any], price: float, config: dict[str, Any]) -> list[str]:
    blockers = []
    exchange = str(candidate.get("exchange") or "").upper()
    asset_class = str(candidate.get("asset_class") or "").upper()
    if exchange not in set(config["allowed_exchanges"]) or candidate.get("otc") is True:
        blockers.append("exchange_or_otc_ineligible")
    if asset_class not in set(config["allowed_asset_classes"]):
        blockers.append("asset_class_ineligible")
    if candidate.get("identity_verified") is not True or candidate.get("tradable") is not True:
        blockers.append("identity_or_tradability_unverified")
    if candidate.get("halted") is True or candidate.get("delisting_risk") is True:
        blockers.append("halt_or_delisting_risk")
    if price < float(config["minimum_price_usd"]):
        blockers.append("price_below_5_usd")
    median_dollar = _number(candidate.get("median_dollar_volume_20d"), "median_dollar_volume_20d", allow_none=True)
    if median_dollar is None or median_dollar < float(config["minimum_median_dollar_volume_20d"]):
        blockers.append("median_dollar_volume_below_floor")
    return blockers


def _research_candidate_eligible_without_market(candidate: dict[str, Any], config: dict[str, Any]) -> bool:
    exchange = str(candidate.get("exchange") or "").upper()
    asset_class = str(candidate.get("asset_class") or "").upper()
    if exchange not in set(config["allowed_exchanges"]) or candidate.get("otc") is True:
        return False
    if asset_class not in set(config["allowed_asset_classes"]):
        return False
    if candidate.get("identity_verified") is not True or candidate.get("tradable") is not True:
        return False
    if candidate.get("halted") is True or candidate.get("delisting_risk") is True:
        return False
    price_hint = candidate.get("discovery_price", candidate.get("price"))
    if price_hint is not None and float(_number(price_hint, "discovery_price") or 0.0) < float(config["minimum_price_usd"]):
        return False
    median_dollar = candidate.get("median_dollar_volume_20d")
    if median_dollar is not None and float(_number(median_dollar, "median_dollar_volume_20d") or 0.0) < float(config["minimum_median_dollar_volume_20d"]):
        return False
    return True


def evaluate_candidate(candidate: dict[str, Any], factor: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    price = float(factor["current_price"])
    session = factor["market_session"]
    blockers = _candidate_identity_blockers(candidate, price, config)
    waiting = []
    grade = factor["data_grade"]
    if grade == "STALE_OR_CONFLICTED":
        blockers.append("stale_or_conflicted_market_data")
    elif grade == "IEX_ONLY":
        waiting.append("second_realtime_source_required")
    max_spread = float(config["maximum_spread_bps"][session])
    if factor["spread_bps"] > max_spread:
        blockers.append("spread_too_wide")
    minimum_session_dollar = float(config["minimum_session_dollar_volume"][session])
    if factor["session_dollar_volume"] < minimum_session_dollar:
        blockers.append("session_dollar_volume_insufficient")
    min_rvol = float(config["minimum_relative_volume"][session])
    if factor["relative_volume"] is None or factor["relative_volume"] < min_rvol:
        blockers.append("relative_volume_insufficient")
    if factor["trade_count_acceleration"] is None or factor["trade_count_acceleration"] < float(config["minimum_trade_count_acceleration"]):
        blockers.append("trade_count_not_accelerating")
    if factor["closed_1m_count"] < 15 or factor["closed_5m_count"] < 3:
        blockers.append("closed_1m_5m_evidence_incomplete")
    if factor["symbol_relative_strength_pct"] <= 0 or factor["sector_relative_strength_pct"] <= 0:
        blockers.append("relative_strength_not_confirmed")
    catalyst = factor["catalyst"]
    if catalyst.get("verified") is not True or not catalyst.get("official_source") or not catalyst.get("realization_by"):
        blockers.append("official_catalyst_unverified")
    else:
        try:
            decision_at = _time(factor["price_as_of"], "price_as_of")
            published_at = _time(catalyst.get("published_at"), "catalyst.published_at")
            realization_by = _time(catalyst.get("realization_by"), "catalyst.realization_by")
            if published_at > decision_at + dt.timedelta(seconds=5):
                blockers.append("catalyst_published_in_future")
            if not decision_at < realization_by <= decision_at + dt.timedelta(days=7):
                blockers.append("catalyst_realization_window_invalid")
        except USEquityShadowError:
            blockers.append("catalyst_time_evidence_invalid")
    valuation = factor["valuation"]
    fair_low = _number(valuation.get("fair_value_low"), "fair_value_low", allow_none=True)
    fair_high = _number(valuation.get("fair_value_high"), "fair_value_high", allow_none=True)
    if fair_low is None or fair_high is None or fair_low <= 0 or fair_high < fair_low or price > fair_high:
        blockers.append("valuation_support_failed")
    capital = factor["capital_risk"]
    if any(capital.get(key) is True for key in ("severe_dilution", "binary_event", "halt_risk", "financing_imminent")):
        blockers.append("capital_or_binary_risk")
    regression = factor["regression"]
    sample_size = int(regression.get("sample_size") or 0)
    conservative_ev = _number(regression.get("conservative_ev_net_pct"), "conservative_ev_net_pct", allow_none=True)
    target_first_lb = _number(regression.get("target_first_probability_lower"), "target_first_probability_lower", allow_none=True)
    reward_risk = _number(regression.get("reward_risk"), "reward_risk", allow_none=True)
    if sample_size < 10 or conservative_ev is None or conservative_ev <= 0:
        blockers.append("regression_ev_gate_failed")
    if target_first_lb is None or target_first_lb <= 0.5:
        blockers.append("target_first_probability_gate_failed")
    if reward_risk is None or reward_risk < float(config["minimum_reward_risk"]):
        blockers.append("reward_risk_below_2")
    vwap = factor["session_vwap"]
    if vwap is None:
        waiting.append("closed_bar_vwap_required")
    elif session == "REGULAR" and price < vwap:
        waiting.append("price_below_vwap")
    if session == "PREMARKET":
        if not (factor["higher_lows"] or factor["breakout_confirmed"]):
            waiting.append("premarket_structure_confirmation_required")
    elif not (factor["breakout_confirmed"] or factor["first_retest_confirmed"]):
        waiting.append("breakout_or_first_retest_required")
    expected_return = _number(candidate.get("expected_return_pct"), "expected_return_pct", allow_none=True) or 0.0
    expected_drawdown = abs(_number(candidate.get("expected_drawdown_pct"), "expected_drawdown_pct", allow_none=True) or 999.0)
    fees = _number(candidate.get("fees_slippage_pct"), "fees_slippage_pct", allow_none=True) or 0.0
    net_profit = expected_return - fees
    score = (
        net_profit * 10.0 + (conservative_ev or -100.0) * 8.0 + (target_first_lb or 0.0) * 20.0
        + min(float(factor["relative_volume"] or 0.0), 10.0) * 2.0
        + min(float(factor["trade_count_acceleration"] or 0.0), 5.0) * 2.0
        + factor["symbol_relative_strength_pct"] + factor["sector_relative_strength_pct"]
        - expected_drawdown * 2.0 - len(blockers) * 50.0 - len(waiting) * 5.0
    )
    return {
        "schema_version": "USEquityExplosiveOpportunityV1",
        **{field: factor[field] for field in BINDING_FIELDS},
        "symbol": factor["symbol"],
        "data_grade": grade,
        "factor_schema": factor["schema_version"],
        "score": round(score, 9),
        "expected_return_pct": expected_return,
        "expected_drawdown_pct": expected_drawdown,
        "fees_slippage_pct": fees,
        "conservative_net_profit_pct": net_profit,
        "target_10pct_feasible": expected_return >= 10.0,
        "hard_blockers": sorted(set(blockers)),
        "waiting_conditions": sorted(set(waiting)),
        "formal_action_eligible": False,
        "production_rule_changed": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }


def _trade_card(candidate: dict[str, Any], factor: dict[str, Any], action: str, account_cash: float, config: dict[str, Any]) -> dict[str, Any]:
    price = float(factor["current_price"])
    plan = candidate.get("plan") if isinstance(candidate.get("plan"), dict) else {}
    entry_low = _number(plan.get("entry_low"), "entry_low", allow_none=True) or price * 0.995
    entry_high = _number(plan.get("entry_high"), "entry_high", allow_none=True) or price * 1.005
    target_1 = _number(plan.get("target_1"), "target_1", allow_none=True) or price * 1.06
    target_2 = _number(plan.get("target_2"), "target_2", allow_none=True) or price * 1.10
    stop = _number(plan.get("stop"), "stop", allow_none=True) or price * 0.97
    if not (0 < stop < entry_low <= entry_high < target_1 <= target_2):
        raise USEquityShadowError("invalid_trade_plan_prices")
    max_loss = entry_high - stop
    stress = {}
    for risk_pct in config["risk_stress_pct"]:
        risk_budget = max(account_cash, 0.0) * risk_pct / 100.0
        shares = math.floor(risk_budget / max_loss) if max_loss > 0 else 0
        stress[str(risk_pct)] = {"risk_budget_usd": round(risk_budget, 2), "max_shares": shares, "notional_usd": round(shares * entry_high, 2)}
    return {
        "current_price": price,
        "price_source": f"Alpaca {factor['data_grade']}",
        "price_as_of": factor["price_as_of"],
        "data_grade": factor["data_grade"],
        "current_action": action,
        "entry_range": [round(entry_low, 4), round(entry_high, 4)],
        "trigger": plan.get("trigger") or ("breakout_or_first_valid_retest" if factor["market_session"] == "REGULAR" else "premarket_liquidity_and_price_confirmation"),
        "valid_until": factor["decision_valid_until"],
        "target_1": round(target_1, 4),
        "target_2": round(target_2, 4),
        "stop": round(stop, 4),
        "latest_exit_at": plan.get("latest_exit_at") or factor["decision_valid_until"],
        "invalidation": plan.get("invalidation") or "price_structure_or_catalyst_invalidated",
        "risk_tier": "SMALL" if factor["data_grade"] == "IEX_CROSS_VERIFIED" else "STANDARD",
        "actual_governed_risk_cap_pct": float(config["actual_risk_cap_pct"]),
        "risk_stress": stress,
        "account_execution": "CASH_READY" if account_cash > 0 else "NO_DEPLOY_CASH",
        "executable_amount_usd": 0.0,
        "human_confirmation_required": True,
    }


def build_decision(top: dict[str, Any], candidate: dict[str, Any], factor: dict[str, Any], account_cash: float, config: dict[str, Any]) -> dict[str, Any]:
    if top["hard_blockers"]:
        action = "NO_TRADE"
    elif top["waiting_conditions"]:
        action = "WAIT_FOR_ENTRY"
    else:
        action = "ENTER_NOW"
    card = None if action == "NO_TRADE" else _trade_card(candidate, factor, action, account_cash, config)
    result = {
        "schema_version": "USEquityIntradayDecisionV1",
        **{field: top[field] for field in BINDING_FIELDS},
        "research_top1": top["symbol"],
        "current_action": action,
        "decision_card": card,
        "no_trade_reasons": top["hard_blockers"] if action == "NO_TRADE" else [],
        "waiting_conditions": top["waiting_conditions"] if action == "WAIT_FOR_ENTRY" else [],
        "risk_tier": (card or {}).get("risk_tier"),
        "account_execution": (card or {}).get("account_execution", "NO_DEPLOY_CASH" if account_cash <= 0 else "CASH_READY"),
        "executable_amount_usd": 0.0,
        "shadow_action_only": True,
        "formal_action_eligible": False,
        "production_rule_changed": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    validate_decision(result)
    return result


def build_no_trade_decision(common: dict[str, Any], symbol: str, reasons: list[str], account_cash: float) -> dict[str, Any]:
    result = {
        "schema_version": "USEquityIntradayDecisionV1", **common,
        "research_top1": symbol, "current_action": "NO_TRADE", "decision_card": None,
        "no_trade_reasons": sorted(set(reasons)), "waiting_conditions": [],
        "risk_tier": None,
        "account_execution": "NO_DEPLOY_CASH" if account_cash <= 0 else "CASH_READY",
        "executable_amount_usd": 0.0, "shadow_action_only": True,
        "formal_action_eligible": False, "production_rule_changed": False,
        "live_orders_enabled": False, "private_api_used": False,
        "human_confirmation_required": True,
    }
    return validate_decision(result)


def validate_decision(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "USEquityIntradayDecisionV1":
        raise USEquityShadowError("schema:USEquityIntradayDecisionV1")
    validate_binding(record)
    if record.get("current_action") not in FORMAL_ACTIONS:
        raise USEquityShadowError("invalid_action")
    if record["current_action"] == "NO_TRADE" and record.get("decision_card") is not None:
        raise USEquityShadowError("no_trade_card_must_be_null")
    if record["current_action"] != "NO_TRADE" and not isinstance(record.get("decision_card"), dict):
        raise USEquityShadowError("action_card_required")
    if record.get("formal_action_eligible") is not False or record.get("shadow_action_only") is not True:
        raise USEquityShadowError("stage0_action_boundary")
    return record


def build_common(payload: dict[str, Any], as_of: dt.datetime, config: dict[str, Any]) -> dict[str, Any]:
    session = market_session(as_of)
    if session not in {"PREMARKET", "REGULAR"}:
        raise USEquityShadowError("outside_supported_market_window")
    sources = payload.get("source_manifest") if isinstance(payload.get("source_manifest"), list) else []
    valid_until = as_of + dt.timedelta(minutes=int(config["decision_valid_minutes"]))
    return {
        "snapshot_id": _text(payload.get("snapshot_id"), "snapshot_id"),
        "strategy_version": STRATEGY_VERSION,
        "config_digest": digest(config),
        "source_digest": digest(sources),
        "market_session": session,
        "price_as_of": _iso(as_of),
        "decision_valid_until": _iso(valid_until),
    }


def run_shadow(payload: dict[str, Any], *, as_of: str, account_cash: float = 0.0) -> dict[str, Any]:
    scan_forbidden(payload)
    now = _time(as_of, "as_of")
    config = load_config(payload.get("config"))
    common = build_common(payload, now, config)
    candidates = payload.get("candidates")
    markets = payload.get("alpaca_snapshots")
    if not isinstance(candidates, list) or not isinstance(markets, dict):
        raise USEquityShadowError("candidates_and_alpaca_snapshots_required")
    universe_blockers = validate_universe_audit(payload.get("universe_audit"), as_of=now, candidate_count=len(candidates))
    evaluated = []
    factors = {}
    candidate_by_symbol = {}
    local_failures = []
    for candidate in candidates:
        symbol = str(candidate.get("symbol") or "").upper()
        if not symbol:
            continue
        candidate_by_symbol[symbol] = candidate
        market = markets.get(symbol)
        if not isinstance(market, dict):
            local_failures.append({"symbol": symbol, "reason": "alpaca_snapshot_missing"})
            continue
        try:
            factor = build_factor_snapshot(candidate, market, common)
            identity_blockers = _candidate_identity_blockers(candidate, float(factor["current_price"]), config)
            if identity_blockers:
                local_failures.append({"symbol": symbol, "reason": "identity_hard_gate", "blockers": identity_blockers})
                continue
            factors[symbol] = factor
            opportunity = evaluate_candidate(candidate, factor, config)
            opportunity["hard_blockers"] = sorted(set(opportunity["hard_blockers"] + universe_blockers))
            evaluated.append(opportunity)
        except USEquityShadowError as exc:
            local_failures.append({"symbol": symbol, "reason": str(exc)})
    if not evaluated:
        research_pool = []
        for candidate in candidates:
            symbol = str(candidate.get("symbol") or "").upper()
            if not symbol or not _research_candidate_eligible_without_market(candidate, config):
                continue
            research_pool.append(candidate)
        research_pool.sort(key=lambda item: (int(item.get("discovery_rank") or 999999), -float(item.get("discovery_score") or 0.0), str(item.get("symbol"))))
        fallback_symbol = str(research_pool[0].get("symbol")).upper() if research_pool else None
        no_trade_reasons = sorted(set(universe_blockers + ["no_valid_candidate_evidence"] + [item["reason"] for item in local_failures]))
        fallback_decision = build_no_trade_decision(common, fallback_symbol, no_trade_reasons, account_cash) if fallback_symbol else None
        return {
            "schema_version": SCHEMA,
            **common,
            "top20": [], "top3": [], "research_top1": None,
            "decision": fallback_decision,
            "research_top1": fallback_symbol,
            "all_blockers": no_trade_reasons,
            "candidate_failures": local_failures,
            "production_rule_changed": False, "formal_action_eligible": False,
            "live_orders_enabled": False, "private_api_used": False,
            "human_confirmation_required": True,
            "lifecycle": lifecycle_from_decision(fallback_decision) if fallback_decision else None,
        }
    ranked = sorted(evaluated, key=lambda item: (bool(item["hard_blockers"]), bool(item["waiting_conditions"]), -item["score"], item["symbol"]))
    top20 = ranked[:20]
    top3 = top20[:3]
    top = top3[0]
    decision = build_decision(top, candidate_by_symbol[top["symbol"]], factors[top["symbol"]], account_cash, config)
    near_miss = []
    prior = payload.get("prior_candidate_states") if isinstance(payload.get("prior_candidate_states"), dict) else {}
    for index, item in enumerate(top20[3:], start=4):
        previous = prior.get(item["symbol"], {})
        previous_rank = int(previous.get("rank") or index)
        previous_score = float(previous.get("score") or item["score"])
        near_miss.append({
            "schema_version": "NearMissCandidateStateV1",
            **{field: item[field] for field in BINDING_FIELDS},
            "symbol": item["symbol"], "rank": index, "score": item["score"],
            "rank_acceleration": previous_rank - index,
            "score_acceleration": round(item["score"] - previous_score, 9),
            "expires_at": _iso(now + dt.timedelta(minutes=30)),
            "formal_action_eligible": False, "production_rule_changed": False,
        })
    long_alerts = []
    for symbol in ("CRCL",):
        if symbol in candidate_by_symbol:
            long_alerts.append({
                "symbol": symbol,
                "long_term_value_alert": True,
                "current_valuation_state": candidate_by_symbol[symbol].get("long_term_valuation_state", "RESEARCH_ONLY"),
                "thesis_status": candidate_by_symbol[symbol].get("long_term_thesis_status", "DATA_DEGRADED"),
                "add_or_wait_zone": candidate_by_symbol[symbol].get("long_term_add_or_wait_zone"),
                "next_review_at": candidate_by_symbol[symbol].get("long_term_next_review_at"),
                "excluded_from_intraday_funding_source": True,
            })
    result = {
        "schema_version": SCHEMA,
        **common,
        "top20": top20, "top3": top3,
        "research_top1": top["symbol"], "decision": decision,
        "factor_snapshots": factors,
        "near_miss_candidates": near_miss,
        "long_term_value_alerts": long_alerts,
        "candidate_failures": local_failures,
        "universe_audit": payload.get("universe_audit"),
        "universe_blockers": universe_blockers,
        "all_blockers": sorted(set(decision.get("no_trade_reasons", []) + decision.get("waiting_conditions", []))),
        "production_rule_changed": False, "formal_action_eligible": False,
        "paper_roi_eligible": False, "real_money_roi_eligible": False,
        "business_ready_eligible": False,
        "live_orders_enabled": False, "private_api_used": False,
        "human_confirmation_required": True,
    }
    result["lifecycle"] = lifecycle_from_decision(decision)
    return result


def lifecycle_from_decision(decision: dict[str, Any]) -> dict[str, Any]:
    validate_decision(decision)
    opened = _time(decision["price_as_of"], "price_as_of")
    return {
        "schema_version": "USEquityLifecycleV1",
        **{field: decision[field] for field in BINDING_FIELDS},
        "lifecycle_id": digest({"decision": decision["research_top1"], "snapshot": decision["snapshot_id"]}),
        "symbol": decision["research_top1"], "current_action": decision["current_action"],
        "next_check_at": _iso(opened + dt.timedelta(minutes=5)),
        "latest_exit_at": (decision.get("decision_card") or {}).get("latest_exit_at"),
        "auto_order_forbidden": True,
        "formal_action_eligible": False, "production_rule_changed": False,
        "live_orders_enabled": False, "private_api_used": False,
        "human_confirmation_required": True,
    }


def validate_missed_opportunity(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "USEquityMissedOpportunityV1":
        raise USEquityShadowError("schema:USEquityMissedOpportunityV1")
    validate_binding(record)
    if record.get("classification") not in {"data_missing", "ranking_error", "threshold_error", "scan_delay", "not_predictable"}:
        raise USEquityShadowError("invalid_missed_classification")
    if record.get("paper_roi_eligible") is not False or record.get("real_money_roi_eligible") is not False:
        raise USEquityShadowError("missed_opportunity_roi_isolation")
    return record


def validate_profit_attribution(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "USEquityProfitAttributionV1":
        raise USEquityShadowError("schema:USEquityProfitAttributionV1")
    if record.get("evidence_mode") != "live" or record.get("user_confirmed_receipts") is not True:
        raise USEquityShadowError("shadow_or_paper_cannot_enter_live_roi")
    if record.get("funds_weighted") is not True:
        raise USEquityShadowError("funds_weighting_required")
    return record


def append_jsonl(path: Path, record: dict[str, Any], id_field: str) -> str:
    scan_forbidden(record)
    record_id = _text(record.get(id_field), id_field)
    existing = {}
    if path.exists():
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise USEquityShadowError(f"invalid_jsonl:{line_no}") from exc
            existing[item.get(id_field)] = item
    if record_id in existing:
        if canonical(existing[record_id]) == canonical(record):
            return "NO_UPDATE"
        raise USEquityShadowError(f"append_only_collision:{record_id}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical(record) + "\n")
    return "APPENDED"


def _fixture_raw(as_of: dt.datetime, price: float = 100.0, spread: float = 0.1) -> dict[str, Any]:
    bars = []
    for index in range(20):
        value = price * (0.985 + index * 0.001)
        bars.append({
            "t": _iso(as_of - dt.timedelta(minutes=20 - index)),
            "o": value * 0.999, "h": value * 1.002, "l": value * 0.998,
            "c": value, "v": 10_000, "vw": value * 0.9995,
            "n": 100 if index < 10 else 220,
        })
    previous = dict(bars[-1])
    previous.update({"o": price * 0.955, "h": price * 0.97, "l": price * 0.95, "c": price * 0.96})
    return {
        "latestTrade": {"p": price, "t": _iso(as_of - dt.timedelta(seconds=5))},
        "latestQuote": {"bp": price - spread / 2, "ap": price + spread / 2, "bs": 500, "as": 600, "t": _iso(as_of - dt.timedelta(seconds=4))},
        "minuteBar": bars[-1], "dailyBar": bars[-1], "prevDailyBar": previous,
        "bars1m": bars, "bars5m": bars[::5],
    }


def _fixture_candidate(symbol: str = "IREN") -> dict[str, Any]:
    return {
        "symbol": symbol, "exchange": "NASDAQ", "asset_class": "COMMON_STOCK",
        "identity_verified": True, "tradable": True, "otc": False,
        "halted": False, "delisting_risk": False,
        "median_dollar_volume_20d": 200_000_000, "same_time_volume_baseline": 50_000,
        "discovery_price": 100.0, "discovery_rank": 1, "discovery_score": 80.0,
        "sector": "AI_INFRASTRUCTURE", "sector_change_pct": 3.0,
        "benchmark_change_pct": 0.4, "symbol_change_pct": 6.0,
        "breakout_confirmed": True, "first_retest_confirmed": True, "higher_lows": True,
        "catalyst": {"verified": True, "official_source": "issuer_ir", "published_at": "2026-08-03T11:00:00Z", "realization_by": "2026-08-03T20:00:00Z"},
        "valuation": {"fair_value_low": 105.0, "fair_value_high": 135.0, "method": "scenario_cash_flow"},
        "capital_risk": {"severe_dilution": False, "binary_event": False, "halt_risk": False, "financing_imminent": False},
        "regression": {"sample_size": 35, "conservative_ev_net_pct": 1.4, "target_first_probability_lower": 0.58, "reward_risk": 2.5},
        "expected_return_pct": 10.0, "expected_drawdown_pct": 3.5, "fees_slippage_pct": 0.2,
        "plan": {"entry_low": 99.8, "entry_high": 100.2, "target_1": 106.0, "target_2": 110.0, "stop": 97.0, "latest_exit_at": "2026-08-03T19:55:00Z"},
    }


def _fixture_payload(as_of: dt.datetime, candidates: list[dict[str, Any]], markets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "snapshot_id": f"forward-{as_of.strftime('%H%M')}",
        "config": load_config(),
        "source_manifest": ["alpaca_iex", "independent_public_quote", "issuer_ir"],
        "candidates": candidates, "alpaca_snapshots": markets,
        "universe_audit": {
            "schema_version": "USEquityDynamicUniverseAuditV1",
            "exchanges": ["NASDAQ", "NYSE", "NYSEAMERICAN", "NYSEARCA"],
            "active_asset_count": 6000,
            "screened_candidate_count": max(len(candidates), 40),
            "discovery_methods": ["unusual_volume", "day_gainers", "sector_rotation", "news_catalyst"],
            "completed_at": _iso(as_of - dt.timedelta(seconds=15)),
            "static_symbol_whitelist_used": False,
        },
    }


def run_forward_suite() -> dict[str, Any]:
    pre = dt.datetime(2026, 8, 3, 12, 0, tzinfo=dt.timezone.utc)
    regular = dt.datetime(2026, 8, 3, 15, 0, tzinfo=dt.timezone.utc)

    def snap(symbol: str, when: dt.datetime, *, second: bool = True, spread: float = 0.1) -> dict[str, Any]:
        cross = {"source": "independent_public_quote", "price": 100.1, "as_of": _iso(when - dt.timedelta(seconds=6))} if second else None
        return sanitize_alpaca_snapshot(_fixture_raw(when, spread=spread), symbol=symbol, feed="iex", received_at=_iso(when), second_source=cross, _authority_token=_ADAPTER_AUTHORITY)

    weak = _fixture_candidate()
    weak["sector_change_pct"] = -1.0
    tasks = [
        ("premarket_enter", pre, [_fixture_candidate()], {"IREN": snap("IREN", pre)}, "ENTER_NOW", "IREN"),
        ("premarket_wait", pre, [_fixture_candidate()], {"IREN": snap("IREN", pre, second=False)}, "WAIT_FOR_ENTRY", "IREN"),
        ("intraday_enter", regular, [_fixture_candidate()], {"IREN": snap("IREN", regular)}, "ENTER_NOW", "IREN"),
        ("intraday_no_trade", regular, [weak], {"IREN": snap("IREN", regular)}, "NO_TRADE", "IREN"),
        ("iex_abnormal_spread", regular, [_fixture_candidate()], {"IREN": snap("IREN", regular, spread=2.0)}, "NO_TRADE", "IREN"),
        ("source_failure_recovery", regular, [_fixture_candidate("MISSING"), _fixture_candidate()], {"IREN": snap("IREN", regular)}, "ENTER_NOW", "IREN"),
    ]
    results = []
    for name, when, candidates, markets, expected_action, expected_top1 in tasks:
        result = run_shadow(_fixture_payload(when, candidates, markets), as_of=_iso(when))
        actual_action = (result.get("decision") or {}).get("current_action")
        passed = actual_action == expected_action and result.get("research_top1") == expected_top1
        results.append({
            "task": name, "status": "PASS" if passed else "FAIL",
            "expected_action": expected_action, "actual_action": actual_action,
            "research_top1": result.get("research_top1"),
            "formal_action_eligible": result.get("formal_action_eligible"),
            "production_rule_changed": result.get("production_rule_changed"),
        })
    return {
        "schema_version": "USEquityIntradayForwardSuiteV1",
        "status": "PASS" if all(item["status"] == "PASS" for item in results) else "FAIL",
        "tasks": results,
        "paper_roi_eligible": False, "real_money_roi_eligible": False,
        "business_ready_eligible": False, "live_orders_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--as-of")
    parser.add_argument("--account-cash", type=float, default=0.0)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--forward-suite", action="store_true")
    args = parser.parse_args()
    if args.forward_suite:
        result = run_forward_suite()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "PASS" else 1
    if args.self_test:
        result = run_forward_suite()
        print(json.dumps({"status": "ok" if result["status"] == "PASS" else "failed", "schema_version": SCHEMA, "forward_suite": result, "production_rule_changed": False, "live_orders_enabled": False}, sort_keys=True))
        return 0 if result["status"] == "PASS" else 1
    if not args.input or not args.output or not args.as_of:
        parser.error("--input, --output and --as-of are required")
    result = run_shadow(json.loads(args.input.read_text(encoding="utf-8")), as_of=args.as_of, account_cash=args.account_cash)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "research_top1": result.get("research_top1"), "action": (result.get("decision") or {}).get("current_action")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
