#!/usr/bin/env python3
"""Research-only Binance price-barrier family discovery and parsing."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
    return module


core = load("crypto_barrier_core", ROOT / "scripts" / "polymarket_alpha.py")
public = load("crypto_barrier_public", ROOT / "scripts" / "polymarket_public_data.py")


MODEL_FAMILY = "binance-spot-first-passage-barrier-v1-research"
DEFAULT_TAGS = ["235", "39"]  # Official Gamma Bitcoin and Ethereum tags.
BINANCE_PUBLIC_BASE = "https://api.binance.com"


def money_value(text: str) -> float | None:
    match = re.search(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmM]?)", text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    suffix = match.group(2).lower()
    return value * ({"": 1, "k": 1_000, "m": 1_000_000}[suffix])


def parse_barrier_market(market: dict[str, Any]) -> dict[str, Any] | None:
    question = str(market.get("question") or "")
    description = str(market.get("description") or "")
    source_text = f"{question}\n{description}"
    pair_match = re.search(r"\b(BTC|ETH|XRP|SOL|BNB)/USDT\b", source_text, re.I)
    if not pair_match:
        name_match = re.search(r"\b(Bitcoin|Ethereum|XRP|Solana|BNB)\b", question, re.I)
        symbol_map = {"bitcoin": "BTC", "ethereum": "ETH", "xrp": "XRP", "solana": "SOL", "bnb": "BNB"}
        symbol = symbol_map.get(name_match.group(1).lower()) if name_match else None
    else:
        symbol = pair_match.group(1).upper()
    threshold = money_value(question)
    lower = bool(re.search(r"\b(dip|dips|below|lower|fall|falls|drop|drops)\b", question, re.I))
    upper = bool(re.search(r"\b(hit|hits|reach|reaches|above|higher|rise|rises)\b", question, re.I))
    # First-passage markets must explicitly settle on any Binance candle, not a single close/time print.
    first_passage = bool(re.search(r"any\s+Binance\s+1[ -]?minute\s+candle", description, re.I))
    source_pair = bool(re.search(r"Binance", description, re.I))
    sampling_raw = market.get("_sampling_raw") if isinstance(market.get("_sampling_raw"), dict) else {}
    start_at = market.get("startDate") or market.get("start_date") or market.get("createdAt") or sampling_raw.get("accepting_order_timestamp")
    end_at = market.get("endDate") or market.get("end_date")
    if not symbol or threshold is None or lower == upper or not first_passage or not source_pair or not start_at or not end_at:
        return None
    return {
        "market_id": str(market.get("id") or market.get("conditionId") or ""),
        "condition_id": market.get("conditionId") or market.get("condition_id"),
        "question": question, "symbol": f"{symbol}USDT", "direction": "lower" if lower else "upper",
        "barrier_price": threshold, "start_at": start_at, "end_at": end_at,
        "closed": bool(market.get("closed")), "outcomes": public.parse_jsonish(market.get("outcomes")),
        "outcome_prices": public.parse_jsonish(market.get("outcomePrices")),
        "resolution": public.infer_resolution(market),
        "model_family": MODEL_FAMILY,
    }


def fetch_tagged_history(tags: list[str], max_pages_per_tag: int, wall_seconds: float) -> tuple[list[dict], list[dict]]:
    rows: dict[str, dict] = {}; requests = []; started = datetime.now(timezone.utc).timestamp()
    for tag in tags:
        cursor = None; seen = set(); terminal = False
        for page_index in range(max_pages_per_tag):
            if datetime.now(timezone.utc).timestamp() - started >= wall_seconds:
                requests.append({"status": "failed", "error": "barrier_history_wall_clock_budget_exhausted", "wall_seconds": wall_seconds})
                return list(rows.values()), requests
            params = {"closed": "true", "limit": 100, "tag_id": tag}
            if cursor: params["after_cursor"] = cursor
            url = f"{public.GAMMA_BASE}/markets/keyset?{urlencode(params)}"
            try:
                payload = public.get_json(url); page = payload.get("markets", []); next_cursor = payload.get("next_cursor")
                requests.append({"url": url, "status": "ok", "tag_id": tag, "rows": len(page), "terminal": not bool(next_cursor)})
                for market in page:
                    key = str(market.get("id") or market.get("conditionId") or "")
                    if key: rows[key] = market
                if not next_cursor or not page:
                    terminal = True; break
                if str(next_cursor) in seen:
                    requests.append({"url": url, "status": "failed", "error": "repeated_next_cursor", "tag_id": tag}); break
                seen.add(str(next_cursor)); cursor = str(next_cursor)
            except Exception as exc:
                requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}", "tag_id": tag}); break
        if not terminal:
            requests.append({"status": "failed", "error": "tag_terminal_cursor_not_proven", "tag_id": tag, "max_pages": max_pages_per_tag})
    return list(rows.values()), requests


def next_month(value: str) -> str:
    year, month = map(int, value.split("-"))
    return f"{year + (month == 12):04d}-{1 if month == 12 else month + 1:02d}"


def month_sequence(start_month: str, end_month: str) -> list[str]:
    values = []; current = start_month
    while current <= end_month:
        values.append(current); current = next_month(current)
    return values


def fetch_sliced_barrier_history(
    tags: list[str], start_month: str, end_month: str,
    max_pages_per_slice: int, wall_seconds: float,
) -> tuple[list[dict], list[dict]]:
    matches: dict[str, dict] = {}; requests = []; started = datetime.now(timezone.utc).timestamp()
    for month in month_sequence(start_month, end_month):
        month_after = next_month(month)
        for tag in tags:
            cursor = None; seen = set(); terminal = False
            for page_index in range(max_pages_per_slice):
                if datetime.now(timezone.utc).timestamp() - started >= wall_seconds:
                    requests.append({"status": "failed", "error": "sliced_history_wall_clock_budget_exhausted", "wall_seconds": wall_seconds})
                    return list(matches.values()), requests
                params = {
                    "closed": "true", "limit": 100, "tag_id": tag,
                    "end_date_min": f"{month}-01T00:00:00Z",
                    "end_date_max": f"{month_after}-01T00:00:00Z",
                }
                if cursor: params["after_cursor"] = cursor
                url = f"{public.GAMMA_BASE}/markets/keyset?{urlencode(params)}"
                try:
                    payload = public.get_json(url); page = payload.get("markets", []); next_cursor = payload.get("next_cursor")
                    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                    parsed_count = 0
                    for market in page:
                        if parse_barrier_market(market) is not None:
                            key = str(market.get("id") or market.get("conditionId") or "")
                            if key: matches[key] = market; parsed_count += 1
                    requests.append({
                        "url": url, "status": "ok", "tag_id": tag, "month": month,
                        "rows": len(page), "parsed_barriers": parsed_count,
                        "response_sha256": hashlib.sha256(canonical).hexdigest(), "terminal": not bool(next_cursor),
                    })
                    if not next_cursor or not page:
                        terminal = True; break
                    if str(next_cursor) in seen:
                        requests.append({"url": url, "status": "failed", "error": "repeated_next_cursor", "tag_id": tag, "month": month}); break
                    seen.add(str(next_cursor)); cursor = str(next_cursor)
                except Exception as exc:
                    requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}", "tag_id": tag, "month": month}); break
            if not terminal:
                requests.append({"status": "failed", "error": "slice_terminal_cursor_not_proven", "tag_id": tag, "month": month})
    return list(matches.values()), requests


def fetch_event_sliced_barrier_history(
    tags: list[str], start_month: str, end_month: str,
    max_pages_per_slice: int, wall_seconds: float, page_size: int = 100,
) -> tuple[list[dict], list[dict]]:
    matches: dict[str, dict] = {}; requests = []; started = datetime.now(timezone.utc).timestamp()
    for month in month_sequence(start_month, end_month):
        after = next_month(month)
        for tag in tags:
            terminal = False
            for page_index in range(max_pages_per_slice):
                if datetime.now(timezone.utc).timestamp() - started >= wall_seconds:
                    requests.append({"status": "failed", "error": "event_sliced_wall_clock_budget_exhausted", "wall_seconds": wall_seconds})
                    return list(matches.values()), requests
                params = {
                    "closed": "true", "tag_id": tag,
                    "end_date_min": f"{month}-01T00:00:00Z", "end_date_max": f"{after}-01T00:00:00Z",
                    "limit": page_size, "offset": page_index * page_size,
                }
                url = f"{public.GAMMA_BASE}/events?{urlencode(params)}"
                try:
                    payload = public.get_json(url); events = payload if isinstance(payload, list) else []
                    nested = [market for event in events for market in (event.get("markets") or []) if isinstance(market, dict)]
                    parsed_count = 0
                    for market in nested:
                        if parse_barrier_market(market) is not None:
                            key = str(market.get("id") or market.get("conditionId") or "")
                            if key: matches[key] = market; parsed_count += 1
                    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                    terminal = len(events) < page_size
                    requests.append({
                        "url": url, "status": "ok", "tag_id": tag, "month": month,
                        "events": len(events), "rows": len(nested), "parsed_barriers": parsed_count,
                        "response_sha256": hashlib.sha256(canonical).hexdigest(), "terminal": terminal,
                    })
                    if terminal: break
                except Exception as exc:
                    requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}", "tag_id": tag, "month": month}); break
            if not terminal:
                requests.append({"status": "failed", "error": "event_slice_terminal_not_proven", "tag_id": tag, "month": month})
    return list(matches.values()), requests


def write_artifacts(output_dir: Path, markets: list[dict], requests: list[dict]) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    parsed = [row for market in markets if (row := parse_barrier_market(market)) is not None]
    files = {"markets.json": markets, "request-log.json": requests, "parsed-barriers.json": parsed}
    evidence = []
    for name, payload in files.items():
        target = output_dir / name; core.write_json(target, payload); raw = target.read_bytes()
        evidence.append({"path": name, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    resolved = [row for row in parsed if row["resolution"].get("winning_outcome") is not None]
    manifest = {
        "schema_version": "polymarket-crypto-barrier-history-v1", "created_at": core.now_iso(),
        "model_family": MODEL_FAMILY, "raw_markets": len(markets), "parsed_barriers": len(parsed),
        "resolved_barriers": len(resolved), "symbol_counts": dict(__import__("collections").Counter(row["symbol"] for row in resolved)),
        "failed_request_count": sum(row.get("status") == "failed" for row in requests),
        "terminal_cursors_proven": bool(requests) and not any(row.get("status") == "failed" for row in requests),
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False, "files": evidence,
    }
    core.write_json(output_dir / "manifest.json", manifest)
    return manifest


def complete_sliced_months(requests: list[dict[str, Any]], tags: list[str] = DEFAULT_TAGS) -> list[str]:
    terminal: dict[str, set[str]] = {}; failed_months = set()
    for row in requests:
        month, tag = row.get("month"), str(row.get("tag_id")) if row.get("tag_id") is not None else None
        if row.get("status") == "ok" and row.get("terminal") is True and month and tag:
            terminal.setdefault(month, set()).add(tag)
        if row.get("status") == "failed" and month:
            failed_months.add(month)
    required = set(tags)
    return sorted(month for month, found in terminal.items() if found >= required and month not in failed_months)


def get_public_json(url: str, timeout: float = 30.0) -> Any:
    if not url.startswith(BINANCE_PUBLIC_BASE + "/api/v3/klines?"):
        raise ValueError("only Binance public klines are allowed")
    with urlopen(Request(url, headers={"Accept": "application/json", "User-Agent": "polymarket-paper-research/1.0"}), timeout=timeout) as response:
        return json.loads(response.read().decode())


def fetch_binance_klines(symbol: str, start_ms: int, end_ms: int) -> tuple[list[list[Any]], list[dict]]:
    rows = []; requests = []; cursor = start_ms
    while cursor < end_ms:
        url = f"{BINANCE_PUBLIC_BASE}/api/v3/klines?{urlencode({'symbol': symbol, 'interval': '1h', 'startTime': cursor, 'endTime': end_ms - 1, 'limit': 1000})}"
        try:
            page = get_public_json(url)
            if not isinstance(page, list): raise ValueError("Binance kline response is not a list")
            requests.append({"url": url, "status": "ok", "rows": len(page)})
            if not page: break
            rows.extend(page)
            next_cursor = int(page[-1][0]) + 3_600_000
            if next_cursor <= cursor: raise ValueError("non-advancing Binance kline cursor")
            cursor = next_cursor
            if len(page) < 1000: break
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}"}); break
    unique = {int(row[0]): row for row in rows if isinstance(row, list) and len(row) >= 6}
    return [unique[key] for key in sorted(unique)], requests


def build_binance_cache(history_dir: Path, output_dir: Path) -> dict:
    requests = json.loads((history_dir / "request-log.json").read_text(encoding="utf-8"))
    parsed = json.loads((history_dir / "parsed-barriers.json").read_text(encoding="utf-8"))
    complete_months = complete_sliced_months(requests)
    eligible = [row for row in parsed if str(row.get("end_at"))[:7] in complete_months and core.parse_iso(row.get("start_at")) and core.parse_iso(row.get("end_at")) and core.parse_iso(row["start_at"]) < core.parse_iso(row["end_at"])]
    if not eligible: raise ValueError("no terminal-complete barrier slices")
    start = min(core.parse_iso(row["start_at"]) for row in eligible) - timedelta(days=370)
    end = max(core.parse_iso(row["end_at"]) for row in eligible)
    output_dir.mkdir(parents=True, exist_ok=True); files = []; all_requests = []
    for symbol in sorted({row["symbol"] for row in eligible}):
        klines, symbol_requests = fetch_binance_klines(symbol, int(start.timestamp() * 1000), int(end.timestamp() * 1000))
        all_requests.extend(symbol_requests); target = output_dir / f"{symbol}_1h.json"; core.write_json(target, klines); raw = target.read_bytes()
        files.append({"path": target.name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw), "rows": len(klines), "first_open_ms": klines[0][0] if klines else None, "last_open_ms": klines[-1][0] if klines else None})
    core.write_json(output_dir / "request-log.json", all_requests)
    manifest = {
        "schema_version": "polymarket-barrier-binance-kline-cache-v1", "created_at": core.now_iso(),
        "source": BINANCE_PUBLIC_BASE, "interval": "1h", "complete_slice_months": complete_months,
        "eligible_contracts": len(eligible), "event_group_count": len({(row['symbol'], row['end_at']) for row in eligible}),
        "start_at": start.isoformat(), "end_at": end.isoformat(), "files": files,
        "failed_request_count": sum(row.get("status") == "failed" for row in all_requests),
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(output_dir / "manifest.json", manifest); return manifest


def build_polymarket_history_cache(history_dir: Path, output_dir: Path) -> dict:
    requests = json.loads((history_dir / "request-log.json").read_text(encoding="utf-8"))
    markets = json.loads((history_dir / "markets.json").read_text(encoding="utf-8"))
    complete_months = complete_sliced_months(requests)
    eligible = [market for market in markets if str(market.get("endDate"))[:7] in complete_months and parse_barrier_market(market) is not None]
    histories, history_requests = public.fetch_batch_price_histories(eligible)
    output_dir.mkdir(parents=True, exist_ok=True)
    core.write_json(output_dir / "price-history.json", histories)
    core.write_json(output_dir / "request-log.json", history_requests)
    history_raw = (output_dir / "price-history.json").read_bytes()
    request_raw = (output_dir / "request-log.json").read_bytes()
    manifest = {
        "schema_version": "polymarket-barrier-price-history-cache-v1", "created_at": core.now_iso(),
        "source": f"{public.CLOB_BASE}/batch-prices-history", "eligible_contracts": len(eligible),
        "tokens_expected": len({token for market in eligible for outcome, token in public.token_map(market).items() if outcome.lower() == "yes"}),
        "tokens_fetched": len(histories), "failed_request_count": sum(row.get("status") == "failed" for row in history_requests),
        "complete_slice_months": complete_months,
        "files": [
            {"path": "price-history.json", "size_bytes": len(history_raw), "sha256": hashlib.sha256(history_raw).hexdigest()},
            {"path": "request-log.json", "size_bytes": len(request_raw), "sha256": hashlib.sha256(request_raw).hexdigest()},
        ],
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(output_dir / "manifest.json", manifest); return manifest


def first_passage_probability(spot: float, barrier: float, sigma_hourly: float, horizon_hours: float, direction: str, volatility_scale: float = 1.0) -> float:
    if spot <= 0 or barrier <= 0 or sigma_hourly <= 0 or horizon_hours <= 0:
        return 0.0
    if direction == "upper" and spot >= barrier: return 1.0
    if direction == "lower" and spot <= barrier: return 1.0
    distance = abs(math.log(barrier / spot))
    denominator = sigma_hourly * volatility_scale * math.sqrt(horizon_hours)
    z = distance / denominator
    tail = 0.5 * math.erfc(z / math.sqrt(2))
    return min(1.0, max(0.0, 2 * tail))


def sample_std(values: list[float]) -> float | None:
    if len(values) < 2: return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def price_at_or_before(history: list[dict], cutoff_s: float, max_age_hours: float = 48) -> float | None:
    eligible = [row for row in history if float(row.get("t", 0)) <= cutoff_s and row.get("p") is not None]
    if not eligible: return None
    row = max(eligible, key=lambda item: float(item["t"]))
    if cutoff_s - float(row["t"]) > max_age_hours * 3600: return None
    return float(row["p"])


def empirical_path_extrema(prior_bars: list[list[Any]], horizon_hours: int, cutoff: datetime) -> tuple[list[float], list[float]]:
    trailing_start = int((cutoff - timedelta(days=365)).timestamp() * 1000)
    bars = [bar for bar in prior_bars if int(bar[0]) >= trailing_start]
    upper_ratios = []; lower_ratios = []
    # Daily-spaced historical starts reduce overlapping-path pseudo-replication.
    for index in range(0, len(bars) - horizon_hours, 24):
        start_price = float(bars[index][4])
        window = bars[index + 1:index + 1 + horizon_hours]
        if start_price <= 0 or len(window) < horizon_hours: continue
        upper_ratios.append(max(float(bar[2]) for bar in window) / start_price)
        lower_ratios.append(min(float(bar[3]) for bar in window) / start_price)
    return upper_ratios, lower_ratios


def empirical_barrier_probability(spot: float, barrier: float, direction: str, upper_ratios: list[float], lower_ratios: list[float]) -> float | None:
    ratios = upper_ratios if direction == "upper" else lower_ratios
    if len(ratios) < 180: return None
    target = barrier / spot
    hits = sum(value >= target for value in ratios) if direction == "upper" else sum(value <= target for value in ratios)
    return (hits + 1) / (len(ratios) + 2)


def build_walk_forward_rows(history_dir: Path, kline_dir: Path, market_history_dir: Path) -> tuple[list[dict], dict[str, int]]:
    source_requests = json.loads((history_dir / "request-log.json").read_text(encoding="utf-8"))
    complete_months = complete_sliced_months(source_requests)
    markets = json.loads((history_dir / "markets.json").read_text(encoding="utf-8"))
    histories = json.loads((market_history_dir / "price-history.json").read_text(encoding="utf-8"))
    bars_by_symbol = {
        symbol: json.loads((kline_dir / f"{symbol}_1h.json").read_text(encoding="utf-8"))
        for symbol in ("BTCUSDT", "ETHUSDT")
    }
    rows = []; exclusions: dict[str, int] = {}; extrema_cache: dict[tuple[str, str, int], tuple[list[float], list[float]]] = {}
    def exclude(reason: str): exclusions[reason] = exclusions.get(reason, 0) + 1
    for market in markets:
        parsed = parse_barrier_market(market)
        if not parsed or str(parsed["end_at"])[:7] not in complete_months:
            continue
        start, end = core.parse_iso(parsed["start_at"]), core.parse_iso(parsed["end_at"])
        outcome_name = parsed["resolution"].get("winning_outcome")
        if not start or not end or start >= end or outcome_name not in {"Yes", "No"}:
            exclude("invalid_time_or_resolution"); continue
        token = next((value for outcome, value in public.token_map(market).items() if outcome.lower() == "yes"), None)
        history = (histories.get(str(token)) or {}).get("history", [])
        bars = bars_by_symbol.get(parsed["symbol"], [])
        for horizon_days in (1, 3, 7, 14, 21, 28):
            cutoff = end - timedelta(days=horizon_days)
            if cutoff <= start:
                exclude("cutoff_before_market_start"); continue
            cutoff_ms = int(cutoff.timestamp() * 1000); start_ms = int(start.timestamp() * 1000)
            prior = [bar for bar in bars if int(bar[6]) <= cutoff_ms]
            event_prior = [bar for bar in prior if int(bar[0]) >= start_ms]
            if not prior:
                exclude("binance_cutoff_missing"); continue
            already_hit = any(
                (parsed["direction"] == "upper" and float(bar[2]) >= parsed["barrier_price"])
                or (parsed["direction"] == "lower" and float(bar[3]) <= parsed["barrier_price"])
                for bar in event_prior
            )
            if already_hit:
                exclude("barrier_already_hit_before_cutoff"); continue
            trailing_start_ms = int((cutoff - timedelta(days=30)).timestamp() * 1000)
            trailing = [bar for bar in prior if int(bar[0]) >= trailing_start_ms]
            if len(trailing) < 24 * 14:
                exclude("insufficient_trailing_volatility"); continue
            closes = [float(bar[4]) for bar in trailing]
            returns = [math.log(right / left) for left, right in zip(closes, closes[1:]) if left > 0 and right > 0]
            sigma = sample_std(returns)
            market_probability = price_at_or_before(history, cutoff.timestamp())
            extrema_key = (parsed["symbol"], cutoff.isoformat(), horizon_days * 24)
            if extrema_key not in extrema_cache:
                extrema_cache[extrema_key] = empirical_path_extrema(prior, horizon_days * 24, cutoff)
            empirical_probability = empirical_barrier_probability(closes[-1], parsed["barrier_price"], parsed["direction"], *extrema_cache[extrema_key])
            if sigma is None or market_probability is None or empirical_probability is None:
                exclude("volatility_market_price_or_empirical_history_missing"); continue
            rows.append({
                **parsed, "sample_id": f"{parsed['market_id']}:{horizon_days}d",
                "event_group": f"{parsed['symbol']}:{parsed['end_at']}", "cutoff_at": cutoff.isoformat(),
                "horizon_hours": horizon_days * 24, "spot_at_cutoff": closes[-1], "sigma_hourly_30d": sigma,
                "market_probability": market_probability, "empirical_probability": empirical_probability,
                "empirical_path_count": len(extrema_cache[extrema_key][0]), "actual_yes": 1 if outcome_name == "Yes" else 0,
            })
    return rows, exclusions


def score(rows: list[dict], probability_key: str) -> dict:
    if not rows: return {"samples": 0, "event_groups": 0, "brier": None, "log_loss": None}
    groups: dict[str, list[tuple[float, int]]] = {}
    for row in rows:
        p = min(1 - 1e-9, max(1e-9, float(row[probability_key]))); y = int(row["actual_yes"])
        groups.setdefault(row["event_group"], []).append((p, y))
    group_brier = [sum((p-y)**2 for p,y in values)/len(values) for values in groups.values()]
    group_log = [-sum(y*math.log(p)+(1-y)*math.log(1-p) for p,y in values)/len(values) for values in groups.values()]
    return {"samples": len(rows), "event_groups": len(groups), "brier": sum(group_brier)/len(group_brier), "log_loss": sum(group_log)/len(group_log)}


def paired_score_improvement(rows: list[dict]) -> dict:
    groups: dict[str, list[tuple[float, float, int]]] = {}
    for row in rows:
        model = min(1-1e-9, max(1e-9, float(row["model_probability"])))
        market = min(1-1e-9, max(1e-9, float(row["market_probability"])))
        groups.setdefault(row["event_group"], []).append((model, market, int(row["actual_yes"])))
    output = {}
    for metric in ("brier", "log_loss"):
        differences = []
        for values in groups.values():
            if metric == "brier":
                model_loss = sum((model-y)**2 for model,_,y in values)/len(values)
                market_loss = sum((market-y)**2 for _,market,y in values)/len(values)
            else:
                model_loss = -sum(y*math.log(model)+(1-y)*math.log(1-model) for model,_,y in values)/len(values)
                market_loss = -sum(y*math.log(market)+(1-y)*math.log(1-market) for _,market,y in values)/len(values)
            differences.append(market_loss-model_loss)
        mean = sum(differences)/len(differences) if differences else None
        sd = sample_std(differences)
        lower = mean - 1.96 * sd / math.sqrt(len(differences)) if mean is not None and sd is not None else None
        output[metric] = {"mean_improvement": mean, "paired_95pct_lower": lower, "event_groups": len(differences)}
    return output


def run_walk_forward(history_dir: Path, kline_dir: Path, market_history_dir: Path) -> dict:
    rows, exclusions = build_walk_forward_rows(history_dir, kline_dir, market_history_dir)
    groups = sorted({row["event_group"] for row in rows}, key=lambda group: min(row["cutoff_at"] for row in rows if row["event_group"] == group))
    train_end = max(1, int(len(groups) * 0.60)); validation_end = max(train_end + 1, int(len(groups) * 0.80))
    segments = {"train": set(groups[:train_end]), "validation": set(groups[train_end:validation_end]), "holdout": set(groups[validation_end:])}
    scales = [0.50, 0.75, 1.0, 1.25, 1.50, 2.0]
    empirical_weights = [0.0, 0.25, 0.50, 0.75, 1.0]
    train_results = []
    for scale in scales:
        for empirical_weight in empirical_weights:
            candidate = []
            for row in rows:
                if row["event_group"] in segments["train"]:
                    brownian = first_passage_probability(row["spot_at_cutoff"], row["barrier_price"], row["sigma_hourly_30d"], row["horizon_hours"], row["direction"], scale)
                    probability = empirical_weight * row["empirical_probability"] + (1-empirical_weight) * brownian
                    candidate.append({**row, "model_probability": probability})
            train_results.append({"volatility_scale": scale, "empirical_weight": empirical_weight, **score(candidate, "model_probability")})
    selected = min(train_results, key=lambda row: (row["brier"], row["log_loss"]))
    evaluated = []
    for row in rows:
        segment = next(name for name, members in segments.items() if row["event_group"] in members)
        brownian = first_passage_probability(row["spot_at_cutoff"], row["barrier_price"], row["sigma_hourly_30d"], row["horizon_hours"], row["direction"], selected["volatility_scale"])
        probability = selected["empirical_weight"] * row["empirical_probability"] + (1-selected["empirical_weight"]) * brownian
        evaluated.append({**row, "segment": segment, "brownian_probability": brownian, "model_probability": probability})
    metrics = {}
    for segment in segments:
        subset = [row for row in evaluated if row["segment"] == segment]
        model_score = score(subset, "model_probability"); market_score = score(subset, "market_probability")
        paired = paired_score_improvement(subset)
        metrics[segment] = {"model": model_score, "market": market_score, "paired_improvement": paired}
    brier_sustained = all((metrics[segment]["paired_improvement"]["brier"]["paired_95pct_lower"] or -1) > 0 for segment in ("validation", "holdout"))
    log_sustained = all((metrics[segment]["paired_improvement"]["log_loss"]["paired_95pct_lower"] or -1) > 0 for segment in ("validation", "holdout"))
    performance_candidate = (brier_sustained or log_sustained) and metrics["holdout"]["model"]["event_groups"] >= 10
    # V2 empirical paths were introduced after the V1 GBM holdout was inspected.
    # This reused segment is diagnostic only; promotion requires a new later-time holdout.
    final_holdout_unseen = False
    promotion = performance_candidate and final_holdout_unseen
    return {
        "schema_version": "polymarket-crypto-barrier-walk-forward-v1", "created_at": core.now_iso(),
        "model_family": MODEL_FAMILY, "selected_volatility_scale": selected["volatility_scale"],
        "selected_empirical_weight": selected["empirical_weight"],
        "split_contract": "chronological 60/20/20 by symbol+end_at event group; threshold ladders never cross segments",
        "total_samples": len(evaluated), "total_event_groups": len(groups), "exclusions": exclusions,
        "train_scale_search": train_results, "segment_metrics": metrics,
        "sustained_improvement": {"brier": brier_sustained, "log_loss": log_sustained},
        "research_iteration": "v2_empirical_path_after_v1_gbm_holdout_review",
        "holdout_status": "diagnostic_reused_holdout_requires_new_later_time_final_oos",
        "final_holdout_unseen": final_holdout_unseen,
        "performance_candidate_before_holdout_governance": performance_candidate,
        "promotion_status": "oos_pass" if promotion else "research_fail_no_sustained_improvement_and_fresh_holdout_missing",
        "independent_event_alpha": promotion, "paper_estimates_allowed": promotion,
        "rows": evaluated, "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }


def walk_forward_markdown(payload: dict) -> str:
    lines = [
        "# Crypto Barrier Walk-Forward Status", "",
        f"- Model family: `{payload['model_family']}`", f"- Promotion: `{payload['promotion_status']}`",
        f"- Samples: {payload['total_samples']}", f"- Independent event groups: {payload['total_event_groups']}",
        f"- Selected volatility scale: {payload['selected_volatility_scale']}",
        f"- Selected empirical-path weight: {payload['selected_empirical_weight']}",
        f"- Sustained Brier improvement: `{str(payload['sustained_improvement']['brier']).lower()}`",
        f"- Sustained Log Loss improvement: `{str(payload['sustained_improvement']['log_loss']).lower()}`", "",
        f"- Holdout governance: `{payload['holdout_status']}`", "",
        "| Segment | Groups | Model Brier | Market Brier | Paired Brier 95% lower | Model Log Loss | Market Log Loss | Paired Log 95% lower |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for segment, row in payload["segment_metrics"].items():
        lines.append(
            f"| {segment} | {row['model']['event_groups']} | {row['model']['brier']:.6f} | {row['market']['brier']:.6f} | "
            f"{row['paired_improvement']['brier']['paired_95pct_lower']:.6f} | {row['model']['log_loss']:.6f} | {row['market']['log_loss']:.6f} | "
            f"{row['paired_improvement']['log_loss']['paired_95pct_lower']:.6f} |"
        )
    lines.extend(["", "Threshold ladders sharing the same symbol and expiry remain in one split group.", "No paper estimate is emitted unless one metric has a positive paired 95% lower bound in both validation and final holdout.", ""])
    return "\n".join(lines)


def research_backlog(payload: dict) -> dict:
    return {
        "schema_version": "polymarket-crypto-barrier-research-backlog-v1", "created_at": core.now_iso(),
        "model_family": payload["model_family"], "status": "research_only_no_estimates",
        "frozen_v2_spec": {
            "volatility_scale": payload["selected_volatility_scale"],
            "empirical_path_weight": payload["selected_empirical_weight"],
            "empirical_lookback_days": 365, "empirical_start_stride_hours": 24,
            "forecast_horizons_days": [1, 3, 7, 14, 21, 28],
            "group_key": "symbol+end_at", "parameter_changes_before_new_final_oos": "forbidden",
        },
        "required_next_evidence": [
            {"task": "collect_new_later_time_final_oos", "period": "2026-01 through 2026-06", "minimum_event_groups": 10, "must_be_unseen_before_frozen_v2_evaluation": True},
            {"task": "verify_all_month_tag_terminal_cursors", "sources": ["Gamma Bitcoin tag 235", "Gamma Ethereum tag 39"]},
            {"task": "fetch_manifested_binance_1h_klines_and_polymarket_cutoff_prices", "private_api": False},
            {"task": "run_frozen_v2_once", "pass_rule": "paired 95% lower improvement > 0 for Brier or Log Loss versus market"},
            {"task": "only_after_oos_pass_add_live_source_fusion", "sources": ["Binance official", "Coinbase independent", "Kraken independent"]},
        ],
        "current_blockers": ["validation_not_sustained", "prior_holdout_reused_after_model_revision", "fresh_final_oos_missing", "live_cross_source_contract_not_yet_enabled"],
        "max_allowed_action": "offline_research_and_cash", "paper_estimates_allowed": False,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }


def self_test() -> dict:
    fixture = {
        "id": "1", "conditionId": "c", "question": "Will Bitcoin dip to $60,000 in July?",
        "description": 'Resolves Yes if any Binance 1 minute candle for BTC/USDT reaches the level.',
        "startDate": "2026-07-01T04:00:00Z", "endDate": "2026-08-01T04:00:00Z",
        "closed": True, "outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]',
        "clobTokenIds": '["y","n"]',
    }
    parsed = parse_barrier_market(fixture); assert parsed and parsed["symbol"] == "BTCUSDT" and parsed["direction"] == "lower" and parsed["barrier_price"] == 60000
    close_fixture = {**fixture, "question": "Bitcoin above $60,000 on July 31?", "description": "Resolves using the Binance BTC/USDT close at 5PM ET."}
    assert parse_barrier_market(close_fixture) is None
    return {"status": "pass", "tests": ["first_passage_parse", "single_close_rejected"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Research-only Polymarket crypto barrier lab")
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover-history"); discover.add_argument("--tags", nargs="+", default=DEFAULT_TAGS)
    discover.add_argument("--max-pages-per-tag", type=int, default=20); discover.add_argument("--wall-seconds", type=float, default=180)
    discover.add_argument("--output-dir", default=str(ROOT / "cache" / "crypto_barrier_history"))
    sliced = sub.add_parser("discover-history-sliced"); sliced.add_argument("--tags", nargs="+", default=DEFAULT_TAGS)
    sliced.add_argument("--start-month", default="2024-06"); sliced.add_argument("--end-month", default="2026-06")
    sliced.add_argument("--max-pages-per-slice", type=int, default=30); sliced.add_argument("--wall-seconds", type=float, default=300)
    sliced.add_argument("--output-dir", default=str(ROOT / "cache" / "crypto_barrier_history_sliced"))
    event_sliced = sub.add_parser("discover-history-events-sliced"); event_sliced.add_argument("--tags", nargs="+", default=DEFAULT_TAGS)
    event_sliced.add_argument("--start-month", required=True); event_sliced.add_argument("--end-month", required=True)
    event_sliced.add_argument("--max-pages-per-slice", type=int, default=30); event_sliced.add_argument("--wall-seconds", type=float, default=300)
    event_sliced.add_argument("--output-dir", required=True)
    klines = sub.add_parser("fetch-binance-klines"); klines.add_argument("--history-dir", default=str(ROOT / "cache" / "crypto_barrier_history_sliced"))
    klines.add_argument("--output-dir", default=str(ROOT / "cache" / "crypto_barrier_binance_klines"))
    histories = sub.add_parser("fetch-polymarket-histories"); histories.add_argument("--history-dir", default=str(ROOT / "cache" / "crypto_barrier_history_sliced"))
    histories.add_argument("--output-dir", default=str(ROOT / "cache" / "crypto_barrier_market_histories"))
    walk = sub.add_parser("walk-forward"); walk.add_argument("--history-dir", default=str(ROOT / "cache" / "crypto_barrier_history_sliced"))
    walk.add_argument("--kline-dir", default=str(ROOT / "cache" / "crypto_barrier_binance_klines")); walk.add_argument("--market-history-dir", default=str(ROOT / "cache" / "crypto_barrier_market_histories"))
    walk.add_argument("--output", default=str(ROOT / "experiments" / "current-crypto-barrier-walk-forward.json"))
    walk.add_argument("--report", default=str(ROOT / "reports" / "CURRENT_CRYPTO_BARRIER_RESEARCH.md"))
    walk.add_argument("--backlog", default=str(ROOT / "experiments" / "current-crypto-barrier-research-backlog.json"))
    sub.add_parser("self-test")
    args = parser.parse_args()
    if args.command == "self-test": payload = self_test()
    elif args.command == "discover-history":
        markets, requests = fetch_tagged_history(args.tags, args.max_pages_per_tag, args.wall_seconds)
        payload = write_artifacts(Path(args.output_dir), markets, requests)
    elif args.command == "discover-history-sliced":
        markets, requests = fetch_sliced_barrier_history(args.tags, args.start_month, args.end_month, args.max_pages_per_slice, args.wall_seconds)
        payload = write_artifacts(Path(args.output_dir), markets, requests)
    elif args.command == "discover-history-events-sliced":
        markets, requests = fetch_event_sliced_barrier_history(args.tags, args.start_month, args.end_month, args.max_pages_per_slice, args.wall_seconds)
        payload = write_artifacts(Path(args.output_dir), markets, requests)
    elif args.command == "fetch-binance-klines":
        payload = build_binance_cache(Path(args.history_dir), Path(args.output_dir))
    elif args.command == "fetch-polymarket-histories":
        payload = build_polymarket_history_cache(Path(args.history_dir), Path(args.output_dir))
    else:
        payload = run_walk_forward(Path(args.history_dir), Path(args.kline_dir), Path(args.market_history_dir)); core.write_json(args.output, payload)
        target = Path(args.report); target.parent.mkdir(parents=True, exist_ok=True); temp = target.with_suffix(target.suffix + ".tmp"); temp.write_text(walk_forward_markdown(payload), encoding="utf-8"); temp.replace(target)
        core.write_json(args.backlog, research_backlog(payload))
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
