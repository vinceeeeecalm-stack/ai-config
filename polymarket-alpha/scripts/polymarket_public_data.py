#!/usr/bin/env python3
"""Public, read-only Polymarket market-data snapshots.

Only official Gamma and CLOB public endpoints are permitted. This module has no
authentication, signing, wallet, order, cancellation, bridge, or withdrawal
code. Network failures produce a structured degraded artifact, never a trade.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from http.client import HTTPException, IncompleteRead
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
ALLOWED_HOST_PREFIXES = (GAMMA_BASE + "/", CLOB_BASE + "/")
FORBIDDEN_PATH_PARTS = (
    "/order", "/orders", "/cancel", "/balance", "/allowance", "/withdraw",
    "/bridge", "/relayer", "/auth", "/api-key", "/heartbeat",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def parse_jsonish(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value) if value else []
        return parsed if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def assert_public_url(url: str) -> None:
    if not url.startswith(ALLOWED_HOST_PREFIXES):
        raise ValueError(f"non-official or non-public host blocked: {url}")
    path = url.split("?", 1)[0].lower()
    if any(part in path for part in FORBIDDEN_PATH_PARTS):
        raise ValueError(f"trading/private endpoint blocked: {url}")


def get_json(url: str, timeout: float = 12.0, retries: int = 2) -> Any:
    assert_public_url(url)
    errors: list[str] = []
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers={"Accept": "application/json", "User-Agent": "polymarket-paper-research/1.0"})
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError, HTTPException, IncompleteRead) as exc:
            errors.append(f"attempt_{attempt + 1}:{type(exc).__name__}:{exc}")
            if attempt < retries:
                time.sleep(min(2 ** attempt, 2))
    raise RuntimeError("; ".join(errors))


def post_json(url: str, payload: Any, timeout: float = 20.0, retries: int = 2) -> Any:
    assert_public_url(url)
    encoded = json.dumps(payload).encode("utf-8")
    errors: list[str] = []
    for attempt in range(retries + 1):
        try:
            request = Request(
                url, data=encoded, method="POST",
                headers={"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "polymarket-paper-research/1.0"},
            )
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError, HTTPException, IncompleteRead) as exc:
            errors.append(f"attempt_{attempt + 1}:{type(exc).__name__}:{exc}")
            if attempt < retries:
                time.sleep(min(2 ** attempt, 2))
    raise RuntimeError("; ".join(errors))


def market_page_url(closed: bool, limit: int, offset: int) -> str:
    params: dict[str, Any] = {
        "closed": str(closed).lower(), "limit": limit, "offset": offset,
        "order": "volumeNum", "ascending": "false",
    }
    if not closed:
        params["active"] = "true"
    return f"{GAMMA_BASE}/markets?{urlencode(params)}"


def fetch_markets_keyset(
    closed: bool, hard_cap: int = 10000, page_size: int = 100,
    fetch: Callable[[str], Any] = get_json,
    max_pages: int = 50, wall_clock_seconds: float = 90.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    started = time.monotonic()
    terminal_page_seen = False
    while len(rows) < hard_cap:
        if len(requests) >= max_pages:
            requests.append({"status": "failed", "error": "keyset_page_budget_exhausted", "max_pages": max_pages})
            break
        if time.monotonic() - started >= wall_clock_seconds:
            requests.append({"status": "failed", "error": "keyset_wall_clock_budget_exhausted", "wall_clock_seconds": wall_clock_seconds})
            break
        params: dict[str, Any] = {
            "closed": str(closed).lower(), "limit": min(page_size, hard_cap - len(rows)),
            "order": "volume_num", "ascending": "false",
        }
        if cursor:
            params["after_cursor"] = cursor
        url = f"{GAMMA_BASE}/markets/keyset?{urlencode(params)}"
        try:
            payload = fetch(url)
            page = payload.get("markets", []) if isinstance(payload, dict) else []
            next_cursor = payload.get("next_cursor") if isinstance(payload, dict) else None
            if not isinstance(page, list):
                raise ValueError("keyset markets response is not a list")
            requests.append({"url": url, "status": "ok", "rows": len(page), "next_cursor_present": bool(next_cursor)})
            rows.extend(item for item in page if isinstance(item, dict))
            if not next_cursor or not page:
                terminal_page_seen = True
                break
            if str(next_cursor) in seen_cursors:
                requests.append({"url": url, "status": "failed", "error": "repeated_next_cursor"})
                break
            seen_cursors.add(str(next_cursor))
            cursor = str(next_cursor)
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
            break
    if len(rows) >= hard_cap and not terminal_page_seen:
        requests.append({"status": "failed", "error": "keyset_hard_cap_reached_full_market_not_proven", "hard_cap": hard_cap})
    return rows[:hard_cap], requests


def sampling_to_gamma_shape(row: dict[str, Any]) -> dict[str, Any]:
    tokens = row.get("tokens") if isinstance(row.get("tokens"), list) else []
    outcomes = [str(token.get("outcome")) for token in tokens if isinstance(token, dict)]
    prices = [token.get("price") for token in tokens if isinstance(token, dict)]
    token_ids = [str(token.get("token_id")) for token in tokens if isinstance(token, dict)]
    condition_id = str(row.get("condition_id") or "")
    return {
        "id": condition_id, "conditionId": condition_id,
        "question": row.get("question"), "description": row.get("description"),
        "slug": row.get("market_slug"), "endDate": row.get("end_date_iso"),
        "active": bool(row.get("active")), "closed": bool(row.get("closed")),
        "archived": bool(row.get("archived")), "acceptingOrders": bool(row.get("accepting_orders")),
        "enableOrderBook": bool(row.get("enable_order_book")),
        "outcomes": json.dumps(outcomes), "outcomePrices": json.dumps(prices),
        "clobTokenIds": json.dumps(token_ids), "tags": row.get("tags") or [],
        "makerBaseFee": row.get("maker_base_fee"), "takerBaseFee": row.get("taker_base_fee"),
        "feesEnabled": bool(row.get("taker_base_fee")),
        "negRisk": bool(row.get("neg_risk")), "negRiskMarketID": row.get("neg_risk_market_id"),
        "_discovery_source": "clob.sampling-markets", "_sampling_raw": row,
    }


def sampling_identity_contract(row: dict[str, Any]) -> dict[str, Any]:
    """Fields that must agree when the sampling cursor repeats a condition.

    Token prices may legitimately move while a multi-page snapshot is being
    collected, so they are intentionally excluded. Identity, tradability,
    expiry and token mapping conflicts remain fatal.
    """
    return {
        "question": row.get("question"), "slug": row.get("slug"),
        "end_date": row.get("endDate"), "active": row.get("active"),
        "closed": row.get("closed"), "accepting_orders": row.get("acceptingOrders"),
        "enable_order_book": row.get("enableOrderBook"),
        "tokens": sorted(token_map(row).items()),
        "neg_risk_market_id": (row.get("_sampling_raw") or {}).get("neg_risk_market_id"),
    }


def fetch_sampling_markets(
    hard_cap: int = 20000, fetch: Callable[[str], Any] = get_json,
    max_pages: int = 30, wall_clock_seconds: float = 90.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    cursor: str | None = None
    seen: set[str] = set()
    terminal = False
    started = time.monotonic()
    while len(rows) < hard_cap:
        if len(requests) >= max_pages:
            requests.append({"status": "failed", "error": "sampling_page_budget_exhausted", "max_pages": max_pages})
            break
        if time.monotonic() - started >= wall_clock_seconds:
            requests.append({"status": "failed", "error": "sampling_wall_clock_budget_exhausted", "wall_clock_seconds": wall_clock_seconds})
            break
        url = f"{CLOB_BASE}/sampling-markets"
        if cursor:
            url += f"?{urlencode({'next_cursor': cursor})}"
        try:
            payload = fetch(url)
            page = payload.get("data", []) if isinstance(payload, dict) else []
            next_cursor = payload.get("next_cursor") if isinstance(payload, dict) else None
            if not isinstance(page, list):
                raise ValueError("sampling markets response is not a list")
            requests.append({
                "url": url, "status": "ok", "rows": len(page),
                "next_cursor": next_cursor, "terminal_cursor": next_cursor == "LTE=",
            })
            rows.extend(sampling_to_gamma_shape(item) for item in page if isinstance(item, dict))
            if next_cursor == "LTE=" or not next_cursor or not page:
                terminal = True
                break
            if str(next_cursor) in seen:
                requests.append({"url": url, "status": "failed", "error": "repeated_next_cursor"})
                break
            seen.add(str(next_cursor)); cursor = str(next_cursor)
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
            break
    if len(rows) >= hard_cap and not terminal:
        requests.append({"status": "failed", "error": "sampling_hard_cap_reached_full_market_not_proven", "hard_cap": hard_cap})
    tradeable = [
        row for row in rows
        if row.get("active") is True and row.get("closed") is not True
        and row.get("acceptingOrders") is True and row.get("enableOrderBook") is True
    ]
    unique: dict[str, dict[str, Any]] = {}
    duplicate_rows = 0
    missing_condition_ids = 0
    conflicting_conditions: list[str] = []
    for row in tradeable:
        key = str(row.get("conditionId") or row.get("id") or "")
        if not key:
            missing_condition_ids += 1
            continue
        prior = unique.get(key)
        if prior is not None:
            duplicate_rows += 1
            if sampling_identity_contract(prior) != sampling_identity_contract(row):
                conflicting_conditions.append(key)
            continue
        unique[key] = row
    if duplicate_rows:
        requests.append({
            "status": "observed", "observation": "duplicate_sampling_rows_deduplicated",
            "duplicate_rows": duplicate_rows, "rows": len(tradeable), "unique": len(unique),
        })
    if missing_condition_ids:
        requests.append({
            "status": "failed", "error": "missing_sampling_condition_id",
            "missing_rows": missing_condition_ids, "rows": len(tradeable), "unique": len(unique),
        })
    if conflicting_conditions:
        requests.append({
            "status": "failed", "error": "conflicting_duplicate_sampling_condition",
            "conflicting_count": len(set(conflicting_conditions)),
            "condition_ids": sorted(set(conflicting_conditions))[:20],
        })
    return list(unique.values()), requests


def fetch_markets(
    closed: bool, max_markets: int, page_size: int,
    fetch: Callable[[str], Any] = get_json,
    keyset_max_pages: int = 50, keyset_wall_clock_seconds: float = 90.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if max_markets <= 0:
        return fetch_markets_keyset(closed, 10000, page_size, fetch, keyset_max_pages, keyset_wall_clock_seconds)
    unlimited = False
    target = max_markets
    rows: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < target:
        limit = min(page_size, target - len(rows))
        url = market_page_url(closed, limit, offset)
        try:
            payload = fetch(url)
            page = payload.get("markets", []) if isinstance(payload, dict) else payload
            if not isinstance(page, list):
                raise ValueError("markets response is not a list")
            requests.append({"url": url, "status": "ok", "rows": len(page)})
            rows.extend(item for item in page if isinstance(item, dict))
            if len(page) < limit:
                break
            offset += len(page)
        except Exception as exc:  # structured degradation is intentional here
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
            break
    return rows[:max_markets], requests


def fetch_gamma_details_by_condition(
    condition_ids: list[str], fetch: Callable[[str], Any] = get_json, chunk_size: int = 50,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    details: dict[str, dict[str, Any]] = {}
    requests: list[dict[str, Any]] = []
    unique = list(dict.fromkeys(item for item in condition_ids if item))
    for index in range(0, len(unique), chunk_size):
        chunk = unique[index:index + chunk_size]
        params = [("condition_ids", item) for item in chunk] + [("limit", len(chunk))]
        url = f"{GAMMA_BASE}/markets?{urlencode(params)}"
        try:
            payload = fetch(url)
            page = payload if isinstance(payload, list) else []
            for market in page:
                if isinstance(market, dict) and market.get("conditionId"):
                    details[str(market["conditionId"])] = market
            requests.append({"url": url, "status": "ok", "requested": len(chunk), "rows": len(page)})
            if any(item not in details for item in chunk):
                requests.append({"url": url, "status": "failed", "error": "gamma_condition_detail_missing", "missing": [item for item in chunk if item not in details]})
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
    return details, requests


def market_identity_keys(market: dict[str, Any]) -> set[str]:
    return {str(value) for value in (market.get("id"), market.get("conditionId"), market.get("condition_id")) if value}


def token_map(market: dict[str, Any]) -> dict[str, str]:
    if isinstance(market.get("tokens"), list):
        return {
            str(token.get("outcome")): str(token.get("token_id"))
            for token in market["tokens"] if isinstance(token, dict) and token.get("outcome") is not None and token.get("token_id")
        }
    outcomes = parse_jsonish(market.get("outcomes"))
    tokens = parse_jsonish(market.get("clobTokenIds"))
    return {str(outcome): str(tokens[index]) for index, outcome in enumerate(outcomes) if index < len(tokens)}


def infer_resolution(market: dict[str, Any]) -> dict[str, Any]:
    outcomes = parse_jsonish(market.get("outcomes"))
    prices = parse_jsonish(market.get("outcomePrices"))
    if not market.get("closed") or len(outcomes) != len(prices) or not outcomes:
        return {"status": "unresolved_or_incomplete", "winning_outcome": None}
    numeric: list[float] = []
    try:
        numeric = [float(value) for value in prices]
    except (TypeError, ValueError):
        return {"status": "invalid_outcome_prices", "winning_outcome": None}
    winners = [index for index, value in enumerate(numeric) if value >= 0.999]
    losers = [index for index, value in enumerate(numeric) if value <= 0.001]
    if len(winners) == 1 and len(losers) == len(numeric) - 1:
        return {
            "status": "resolved_from_terminal_prices", "winning_outcome": str(outcomes[winners[0]]),
            "winning_token_id": token_map(market).get(str(outcomes[winners[0]])),
        }
    return {"status": "resolution_not_proven", "winning_outcome": None}


def fetch_market_aux(
    market: dict[str, Any], include_book: bool, include_history: bool,
    fetch: Callable[[str], Any] = get_json,
) -> dict[str, Any]:
    condition_id = str(market.get("conditionId") or market.get("condition_id") or "")
    tokens = token_map(market)
    yes_token = next((token for outcome, token in tokens.items() if outcome.lower() == "yes"), "")
    result: dict[str, Any] = {"condition_id": condition_id, "yes_token_id": yes_token, "books": {}, "errors": []}
    if condition_id:
        url = f"{CLOB_BASE}/clob-markets/{condition_id}"
        try:
            result["clob_market_info"] = fetch(url)
        except Exception as exc:
            result["errors"].append({"endpoint": "clob_market_info", "error": f"{type(exc).__name__}:{exc}"})
    if include_book:
        for outcome, token in tokens.items():
            url = f"{CLOB_BASE}/book?{urlencode({'token_id': token})}"
            try:
                result["books"][token] = fetch(url)
                if outcome.lower() == "yes":
                    result["book"] = result["books"][token]
            except Exception as exc:
                result["errors"].append({"endpoint": "book", "outcome": outcome, "token_id": token, "error": f"{type(exc).__name__}:{exc}"})
    if include_history and yes_token:
        url = f"{CLOB_BASE}/prices-history?{urlencode({'market': yes_token, 'interval': 'max', 'fidelity': 60})}"
        try:
            result["price_history"] = fetch(url)
        except Exception as exc:
            result["errors"].append({"endpoint": "prices_history", "error": f"{type(exc).__name__}:{exc}"})
    return result


def fetch_batch_price_histories(
    markets: list[dict[str, Any]],
    post: Callable[[str, Any], Any] = post_json,
    chunk_size: int = 20,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tokens = []
    for market in markets:
        mapping = token_map(market)
        token = next((value for outcome, value in mapping.items() if outcome.lower() == "yes"), "")
        if token and token not in tokens:
            tokens.append(token)
    histories: dict[str, Any] = {}
    requests: list[dict[str, Any]] = []
    url = f"{CLOB_BASE}/batch-prices-history"
    for index in range(0, len(tokens), chunk_size):
        chunk = tokens[index:index + chunk_size]
        body = {"markets": chunk, "interval": "max", "fidelity": 1440}
        try:
            payload = post(url, body)
            batch = payload.get("history", {}) if isinstance(payload, dict) else {}
            if not isinstance(batch, dict):
                raise ValueError("batch history response is not a mapping")
            for token in chunk:
                histories[token] = {"history": batch.get(token, [])}
            requests.append({"url": url, "status": "ok", "token_count": len(chunk), "history_count": len(batch)})
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "token_count": len(chunk), "error": f"{type(exc).__name__}:{exc}"})
    return histories, requests


def fee_contract(market: dict[str, Any], aux: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(market.get("feesEnabled"))
    gamma_schedule = market.get("feeSchedule") if isinstance(market.get("feeSchedule"), dict) else {}
    clob_info = aux.get("clob_market_info") if isinstance(aux.get("clob_market_info"), dict) else {}
    clob_schedule = clob_info.get("fd") if isinstance(clob_info.get("fd"), dict) else {}
    rate = clob_schedule.get("r", gamma_schedule.get("rate", gamma_schedule.get("r")))
    exponent = clob_schedule.get("e", gamma_schedule.get("exponent", gamma_schedule.get("e")))
    try:
        rate = float(rate) if rate is not None else None
    except (TypeError, ValueError):
        rate = None
    return {
        "fees_enabled": enabled,
        "fee_rate": rate,
        "fee_exponent": exponent,
        "taker_only": clob_schedule.get("to", gamma_schedule.get("takerOnly")),
        "source": "clob_market_info.fd" if clob_schedule else ("gamma.feeSchedule" if gamma_schedule else "missing"),
        "status": "ok" if (not enabled or rate is not None) else "missing_enabled_fee_schedule",
        "formula": "shares * fee_rate * price * (1-price)",
    }


def normalized_row(market: dict[str, Any], aux: dict[str, Any]) -> dict[str, Any]:
    resolution = infer_resolution(market)
    events = market.get("events") if isinstance(market.get("events"), list) else []
    event_id = str(events[0].get("id")) if events and isinstance(events[0], dict) and events[0].get("id") is not None else None
    return {
        "market_id": str(market.get("id") or ""),
        "condition_id": market.get("conditionId"),
        "event_id": event_id,
        "question": market.get("question"),
        "slug": market.get("slug"),
        "category": market.get("category"),
        "end_date": market.get("endDate"),
        "closed_time": market.get("closedTime"),
        "active": bool(market.get("active")),
        "closed": bool(market.get("closed")),
        "accepting_orders": bool(market.get("acceptingOrders")),
        "enable_order_book": bool(market.get("enableOrderBook")),
        "resolution_source": market.get("resolutionSource"),
        "uma_resolution_status": market.get("umaResolutionStatus"),
        "outcomes": parse_jsonish(market.get("outcomes")),
        "outcome_prices": parse_jsonish(market.get("outcomePrices")),
        "tokens": token_map(market),
        "resolution": resolution,
        "fee_contract": fee_contract(market, aux),
        "book_status": "ok" if isinstance(aux.get("book"), dict) else "missing_or_not_requested",
        "price_history_points": len((aux.get("price_history") or {}).get("history", [])) if isinstance(aux.get("price_history"), dict) else 0,
        "aux_errors": aux.get("errors", []),
    }


def file_evidence(path: Path, root: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"path": str(path.relative_to(root)), "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def write_snapshot(
    output_dir: Path, markets: list[dict[str, Any]], requests: list[dict[str, Any]],
    aux_by_market: dict[str, Any], mode: str, history_expected: int = 0, books_expected: int = 0,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    enriched_markets = []
    for market in markets:
        key = str(market.get("id") or market.get("conditionId") or "")
        enriched = dict(market)
        enriched["fee_contract"] = fee_contract(market, aux_by_market.get(key, {}))
        enriched_markets.append(enriched)
    normalized = [normalized_row(market, aux_by_market.get(str(market.get("id") or market.get("conditionId") or ""), {})) for market in enriched_markets]
    books = {
        str(token): book for row in aux_by_market.values()
        for token, book in (row.get("books") or {}).items() if isinstance(book, dict)
    }
    histories = {row.get("yes_token_id"): row.get("price_history") for row in aux_by_market.values() if row.get("yes_token_id") and isinstance(row.get("price_history"), dict)}
    clob_market_info = {market_id: row.get("clob_market_info") for market_id, row in aux_by_market.items() if isinstance(row.get("clob_market_info"), dict)}
    artifacts = {
        "markets.json": enriched_markets,
        "normalized-markets.json": normalized,
        "books.json": books,
        "price-history.json": histories,
        "clob-market-info.json": clob_market_info,
        "request-log.json": requests,
    }
    for name, payload in artifacts.items():
        atomic_json(output_dir / name, payload)
    resolved_count = sum(1 for row in normalized if row["resolution"]["winning_outcome"] is not None)
    aux_ids = set(aux_by_market)
    fee_missing = sum(1 for row in normalized if row["market_id"] in aux_ids and row["fee_contract"]["status"] != "ok")
    aux_error_count = sum(len(row.get("aux_errors", [])) for row in normalized)
    failed_request_count = sum(1 for row in requests if row.get("status") == "failed")
    duplicate_market_row_count = sum(int(row.get("duplicate_rows") or 0) for row in requests if row.get("observation") == "duplicate_sampling_rows_deduplicated")
    missing_condition_id_count = sum(int(row.get("missing_rows") or 0) for row in requests if row.get("error") == "missing_sampling_condition_id")
    conflicting_duplicate_condition_count = sum(int(row.get("conflicting_count") or 0) for row in requests if row.get("error") == "conflicting_duplicate_sampling_condition")
    price_history_missing_count = max(0, history_expected - len(histories))
    book_missing_count = max(0, books_expected - len(books))
    manifest = {
        "schema_version": "polymarket-public-snapshot-v1",
        "created_at": now_iso(), "mode": mode,
        "official_sources": [GAMMA_BASE, CLOB_BASE],
        "public_read_only": True, "paper_only": True,
        "live_orders_enabled": False, "private_api_used": False,
        "markets_fetched": len(enriched_markets), "resolved_markets_proven": resolved_count,
        "discovery_source": "clob.sampling-markets" if any(row.get("_discovery_source") == "clob.sampling-markets" for row in enriched_markets) else "gamma.markets",
        "terminal_cursor_proven": any(row.get("terminal_cursor") is True and row.get("status") == "ok" for row in requests),
        "books_fetched": len(books), "price_histories_fetched": len(histories),
        "books_expected": books_expected, "book_missing_count": book_missing_count,
        "price_histories_expected": history_expected, "price_history_missing_count": price_history_missing_count,
        "fee_contract_missing_count": fee_missing, "aux_error_count": aux_error_count,
        "duplicate_market_row_count": duplicate_market_row_count,
        "missing_condition_id_count": missing_condition_id_count,
        "conflicting_duplicate_condition_count": conflicting_duplicate_condition_count,
        "failed_request_count": failed_request_count,
        "data_status": "ok" if markets and aux_error_count == 0 and failed_request_count == 0 and fee_missing == 0 and price_history_missing_count == 0 and book_missing_count == 0 else "degraded",
        "files": [file_evidence(output_dir / name, output_dir) for name in artifacts],
    }
    atomic_json(output_dir / "snapshot-manifest.json", manifest)
    return manifest


def verify_manifest(snapshot_dir: Path) -> dict[str, Any]:
    manifest_path = snapshot_dir / "snapshot-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures = []
    for row in manifest.get("files", []):
        relative = Path(row.get("path", ""))
        if relative.is_absolute() or ".." in relative.parts:
            failures.append(f"unsafe_path:{relative}")
            continue
        path = snapshot_dir / relative
        if not path.exists():
            failures.append(f"missing:{relative}")
            continue
        current = file_evidence(path, snapshot_dir)
        if current["size_bytes"] != row.get("size_bytes"):
            failures.append(f"size_mismatch:{relative}")
        if current["sha256"] != row.get("sha256"):
            failures.append(f"sha256_mismatch:{relative}")
    return {
        "status": "pass" if not failures else "blocked",
        "snapshot_dir": str(snapshot_dir), "failures": failures,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }


def build_snapshot(
    mode: str, max_markets: int, page_size: int, aux_limit: int,
    include_books: bool, include_history: bool, output_dir: Path,
    fetch: Callable[[str], Any] = get_json,
    aux_market_ids: set[str] | None = None,
    keyset_max_pages: int = 50, keyset_wall_clock_seconds: float = 90.0,
) -> dict[str, Any]:
    closed = mode == "history"
    if mode == "live" and max_markets <= 0:
        markets, requests = fetch_sampling_markets(
            20000, fetch, keyset_max_pages, keyset_wall_clock_seconds,
        )
    else:
        markets, requests = fetch_markets(
            closed, max_markets, page_size, fetch,
            keyset_max_pages=keyset_max_pages, keyset_wall_clock_seconds=keyset_wall_clock_seconds,
        )
    aux: dict[str, Any] = {}
    selected_indices = list(range(min(aux_limit, len(markets))))
    if aux_market_ids:
        selected_indices.extend(
            index for index, market in enumerate(markets)
            if market_identity_keys(market) & aux_market_ids and index not in selected_indices
        )
    if mode == "live" and max_markets <= 0:
        conditions = [str(markets[index].get("conditionId") or "") for index in selected_indices]
        gamma_details, detail_requests = fetch_gamma_details_by_condition(conditions, fetch)
        requests.extend(detail_requests)
        for index in selected_indices:
            sampling = markets[index]
            condition_id = str(sampling.get("conditionId") or "")
            detail = gamma_details.get(condition_id)
            if detail:
                markets[index] = {
                    **detail, "_discovery_source": "clob.sampling-markets+gamma.condition-detail",
                    "_sampling_raw": sampling.get("_sampling_raw"),
                }
    selected_aux = [markets[index] for index in selected_indices]
    selected_ids = {str(market.get("id") or market.get("conditionId") or "") for market in selected_aux}
    for market in selected_aux:
        market_id = str(market.get("id") or market.get("conditionId") or "")
        aux[market_id] = fetch_market_aux(market, include_books and not closed, include_history and not closed, fetch)
    if closed and include_history:
        histories, history_requests = fetch_batch_price_histories(markets)
        requests.extend(history_requests)
        for market in markets:
            market_id = str(market.get("id") or "")
            mapping = token_map(market)
            yes_token = next((value for outcome, value in mapping.items() if outcome.lower() == "yes"), "")
            row = aux.setdefault(market_id, {"condition_id": market.get("conditionId"), "yes_token_id": yes_token, "errors": []})
            if yes_token in histories:
                row["price_history"] = histories[yes_token]
    history_expected = sum(1 for market in markets if any(outcome.lower() == "yes" for outcome in token_map(market))) if include_history else 0
    books_expected = sum(len(token_map(market)) for market in selected_aux) if include_books and not closed else 0
    return write_snapshot(output_dir, markets, requests, aux, mode, history_expected, books_expected)


def self_test() -> dict[str, Any]:
    markets = [
        {"id": "1", "conditionId": "c1", "question": "Will X?", "active": True, "closed": False, "acceptingOrders": True, "enableOrderBook": True, "outcomes": '["Yes","No"]', "outcomePrices": '["0.7","0.3"]', "clobTokenIds": '["y1","n1"]', "feesEnabled": True, "feeSchedule": {"rate": 0.04}},
        {"id": "2", "conditionId": "c2", "question": "Did Y?", "active": False, "closed": True, "outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]', "clobTokenIds": '["y2","n2"]', "feesEnabled": False},
    ]

    def fixture_fetch(url: str) -> Any:
        if "/markets?" in url:
            return markets
        if "/clob-markets/c1" in url:
            return {"fd": {"r": 0.04, "e": 1, "to": True}}
        if "/book?" in url:
            token = url.split("token_id=")[-1]
            return {"asset_id": token, "bids": [{"price": "0.69", "size": "100"}], "asks": [{"price": "0.70", "size": "100"}]}
        if "/prices-history?" in url:
            return {"history": [{"t": 1, "p": 0.5}]}
        if "/clob-markets/c2" in url:
            return {"fd": {"r": 0, "e": 1, "to": True}}
        raise RuntimeError(f"unexpected fixture URL {url}")

    fetched, log = fetch_markets(False, 2, 2, fixture_fetch)
    assert len(fetched) == 2 and log[0]["status"] == "ok"
    keyset_calls = []
    def keyset_fixture(url: str) -> Any:
        keyset_calls.append(url)
        if "after_cursor=" not in url:
            return {"markets": [markets[0]], "next_cursor": "cursor-1"}
        return {"markets": [markets[1]]}
    keyset_rows, keyset_log = fetch_markets_keyset(False, 10, 1, keyset_fixture)
    assert len(keyset_rows) == 2 and len(keyset_log) == 2 and "after_cursor=cursor-1" in keyset_calls[1]
    aux = fetch_market_aux(markets[0], True, True, fixture_fetch)
    assert aux["book"]["asset_id"] == "y1" and set(aux["books"]) == {"y1", "n1"} and fee_contract(markets[0], aux)["fee_rate"] == 0.04
    assert infer_resolution(markets[1])["winning_outcome"] == "Yes"
    histories, history_log = fetch_batch_price_histories(
        markets,
        lambda url, payload: {"history": {token: [{"t": 1, "p": 0.5}] for token in payload["markets"]}},
    )
    assert len(histories) == 2 and history_log[0]["status"] == "ok"
    try:
        assert_public_url("https://clob.polymarket.com/order")
        raise AssertionError("order endpoint was not blocked")
    except ValueError:
        pass
    return {
        "status": "pass",
        "tests": ["offset_pagination", "keyset_pagination", "book", "price_history", "batch_price_history", "fee_contract", "resolution", "private_endpoint_block"],
        "public_read_only": True, "live_orders_enabled": False, "private_api_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Polymarket official public-data snapshotter")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("discover-live", "discover-history"):
        command = sub.add_parser(name)
        command.add_argument("--max-markets", type=int, default=100 if name == "discover-live" else 500)
        command.add_argument("--page-size", type=int, default=100)
        command.add_argument("--aux-limit", type=int, default=100 if name == "discover-live" else 500)
        command.add_argument("--include-books", action="store_true")
        command.add_argument("--include-price-history", action="store_true")
        command.add_argument("--output-dir", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--snapshot-dir", required=True)
    sub.add_parser("self-test")
    args = parser.parse_args()
    if args.command == "self-test":
        payload = self_test()
    elif args.command == "verify":
        payload = verify_manifest(Path(args.snapshot_dir))
    else:
        payload = build_snapshot(
            "live" if args.command == "discover-live" else "history",
            args.max_markets, args.page_size, args.aux_limit,
            args.include_books, args.include_price_history, Path(args.output_dir),
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status", "pass") != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
