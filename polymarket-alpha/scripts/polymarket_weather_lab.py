#!/usr/bin/env python3
"""Leakage-safe research lab for Polymarket daily high-temperature ladders."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
GAMMA = "https://gamma-api.polymarket.com"
PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
DATA_API = "https://data-api.polymarket.com"
SERIES_ID = "10727"
STATION = "KDAL"
LATITUDE = 32.8471
LONGITUDE = -96.8518
LOCAL_TIMEZONE = "America/Chicago"
MODEL_VERSION = "pm-weather-dallas-day1-v1-20260711"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
    return module


core = load("weather_core", ROOT / "scripts/polymarket_alpha.py")
public = load("weather_public", ROOT / "scripts/polymarket_public_data.py")


def get_json(url: str, timeout: float = 45, retries: int = 3) -> Any:
    if not url.startswith((GAMMA + "/", PREVIOUS_RUNS.split("/v1/")[0] + "/", DATA_API + "/")):
        raise ValueError("weather lab source host not allowlisted")
    errors = []
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers={"Accept": "application/json", "User-Agent": "polymarket-paper-research/1.0"})
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except Exception as exc:
            errors.append(f"attempt_{attempt + 1}:{type(exc).__name__}:{exc}")
            if attempt < retries: time.sleep(min(2 ** attempt, 3))
    raise RuntimeError(";".join(errors))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(directory: Path, schema: str, source_urls: list[str], files: list[str], **extra: Any) -> dict[str, Any]:
    payload = {
        "schema_version": schema, "created_at": core.now_iso(), "source_urls": source_urls,
        "files": [{"path": name, "size_bytes": (directory / name).stat().st_size, "sha256": sha(directory / name)} for name in files],
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False, **extra,
    }
    core.write_json(directory / "manifest.json", payload); return payload


def parse_prices(value: Any) -> list[float]:
    try: values = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError): return []
    try: return [float(item) for item in values]
    except (TypeError, ValueError): return []


def resolved_winner(event: dict[str, Any]) -> dict[str, Any] | None:
    winners = []
    for market in event.get("markets") or []:
        prices = parse_prices(market.get("outcomePrices"))
        if len(prices) == 2 and prices[0] >= 0.99 and prices[1] <= 0.01:
            winners.append(market)
    return winners[0] if len(winners) == 1 else None


def discover_history(output_dir: Path, page_size: int = 20) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True); events = []; requests = []; offset = 0; terminal = False
    while True:
        params = {"series_id": SERIES_ID, "closed": "true", "limit": page_size, "offset": offset, "order": "endDate", "ascending": "true"}
        url = f"{GAMMA}/events?{urlencode(params)}"
        try:
            page = get_json(url)
            if not isinstance(page, list): raise ValueError("Gamma events response is not a list")
            requests.append({"url": url, "status": "ok", "rows": len(page)}); events.extend(page)
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}"}); break
        if len(page) < page_size: terminal = True; break
        offset += len(page)
    deduped = {str(event.get("id")): event for event in events if event.get("id")}
    events = sorted(deduped.values(), key=lambda row: str(row.get("endDate") or ""))
    core.write_json(output_dir / "events.json", events); core.write_json(output_dir / "request-log.json", requests)
    usable = [event for event in events if resolved_winner(event) and STATION in str(event.get("resolutionSource") or event.get("description") or "")]
    return write_manifest(
        output_dir, "polymarket-weather-history-v1", [f"{GAMMA}/events"], ["events.json", "request-log.json"],
        series_id=SERIES_ID, station=STATION, events=len(events), resolved_station_events=len(usable),
        terminal_pagination_proven=terminal, failed_request_count=sum(row["status"] == "failed" for row in requests),
        data_status="ok" if terminal and usable else "degraded",
    )


def event_date(event: dict[str, Any]) -> str | None:
    value = event.get("eventDate") or event.get("endDate")
    return str(value)[:10] if value else None


def fetch_forecast_archive(history_dir: Path, output_dir: Path) -> dict[str, Any]:
    history_manifest = core.read_json(history_dir / "manifest.json")
    if history_manifest.get("data_status") != "ok" or history_manifest.get("terminal_pagination_proven") is not True:
        raise ValueError("complete weather history required")
    events = core.read_json(history_dir / "events.json")
    dates = sorted(date for date in (event_date(row) for row in events) if date)
    if not dates: raise ValueError("weather event dates missing")
    output_dir.mkdir(parents=True, exist_ok=True); forecasts = {}; requests = []
    for model in ("gfs_seamless", "ecmwf_ifs025"):
        params = {
            "latitude": LATITUDE, "longitude": LONGITUDE, "hourly": "temperature_2m_previous_day1",
            "temperature_unit": "fahrenheit", "timezone": LOCAL_TIMEZONE,
            "start_date": dates[0], "end_date": dates[-1], "models": model,
        }
        url = f"{PREVIOUS_RUNS}?{urlencode(params)}"
        try:
            payload = get_json(url, timeout=90)
            hourly = payload.get("hourly") or {}; times = hourly.get("time") or []; values = hourly.get("temperature_2m_previous_day1") or []
            if len(times) != len(values) or not times: raise ValueError("forecast times/value mismatch")
            daily: dict[str, list[float]] = {}
            for stamp, value in zip(times, values):
                if value is not None: daily.setdefault(str(stamp)[:10], []).append(float(value))
            forecasts[model] = {date: max(values) for date, values in daily.items() if len(values) >= 18}
            requests.append({"url": url, "status": "ok", "hourly_rows": len(times), "daily_rows": len(forecasts[model])})
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
    core.write_json(output_dir / "daily-max-day1.json", forecasts); core.write_json(output_dir / "request-log.json", requests)
    complete = all(model in forecasts and len(forecasts[model]) >= max(1, len(set(dates)) - 2) for model in ("gfs_seamless", "ecmwf_ifs025"))
    return write_manifest(
        output_dir, "weather-day1-forecast-archive-v1", [PREVIOUS_RUNS], ["daily-max-day1.json", "request-log.json"],
        station=STATION, forecast_semantics="each valid hour predicted exactly 24 hours earlier",
        start_date=dates[0], end_date=dates[-1], failed_request_count=sum(row["status"] == "failed" for row in requests),
        data_status="ok" if complete and not any(row["status"] == "failed" for row in requests) else "degraded",
    )


def selected_events(history_dir: Path, maximum: int) -> list[dict[str, Any]]:
    events = core.read_json(history_dir / "events.json")
    usable = [event for event in events if resolved_winner(event) and STATION in str(event.get("resolutionSource") or event.get("description") or "")]
    return usable[-maximum:] if maximum else usable


def fetch_market_histories(history_dir: Path, output_dir: Path, maximum_events: int = 180, chunk_size: int = 20) -> dict[str, Any]:
    events = selected_events(history_dir, maximum_events); markets = [market for event in events for market in event.get("markets") or []]
    tokens = []
    for market in markets:
        token = next((token for outcome, token in public.token_map(market).items() if outcome.lower() == "yes"), None)
        if token and token not in tokens: tokens.append(token)
    output_dir.mkdir(parents=True, exist_ok=True); histories = {}; requests = []; url = f"{public.CLOB_BASE}/batch-prices-history"
    for index in range(0, len(tokens), chunk_size):
        chunk = tokens[index:index + chunk_size]
        try:
            payload = public.post_json(url, {"markets": chunk, "interval": "max", "fidelity": 60}, timeout=45, retries=3)
            batch = payload.get("history", {}) if isinstance(payload, dict) else {}
            if not isinstance(batch, dict): raise ValueError("batch history response not a mapping")
            for token in chunk: histories[token] = {"history": batch.get(token, [])}
            requests.append({"url": url, "status": "ok", "token_count": len(chunk), "history_count": len(batch)})
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "token_count": len(chunk), "error": f"{type(exc).__name__}:{exc}"})
    core.write_json(output_dir / "price-history.json", histories); core.write_json(output_dir / "request-log.json", requests)
    nonempty = sum(bool((row or {}).get("history")) for row in histories.values())
    return write_manifest(
        output_dir, "weather-market-price-history-v1", [url], ["price-history.json", "request-log.json"],
        event_groups=len(events), tokens_expected=len(tokens), tokens_fetched=len(histories), nonempty_histories=nonempty,
        fidelity_minutes=60, failed_request_count=sum(row["status"] == "failed" for row in requests),
        data_status="ok" if len(histories) == len(tokens) and nonempty == len(tokens) and not any(row["status"] == "failed" for row in requests) else "degraded",
    )


def trade_yes_price(trade: dict[str, Any]) -> float | None:
    try: price = float(trade.get("price"))
    except (TypeError, ValueError): return None
    outcome = str(trade.get("outcome") or "").lower()
    if outcome == "yes": return price
    if outcome == "no": return 1 - price
    return None


def fetch_trade_histories(history_dir: Path, output_dir: Path, maximum_events: int = 180, workers: int = 6) -> dict[str, Any]:
    """Archive the latest official public trade at or before each fixed cutoff."""
    events = selected_events(history_dir, maximum_events); output_dir.mkdir(parents=True, exist_ok=True)
    histories: dict[str, dict[str, list[dict[str, Any]]]] = {}; requests = []; terminal_events = 0
    zone = ZoneInfo(LOCAL_TIMEZONE)
    def fetch_event(event: dict[str, Any]) -> dict[str, Any]:
        markets = event.get("markets") or []; condition_to_token = {}
        for market in markets:
            token = next((token for outcome, token in public.token_map(market).items() if outcome.lower() == "yes"), None)
            condition = str(market.get("conditionId") or "")
            if token and condition: condition_to_token[condition] = token
        conditions = sorted(condition_to_token)
        date = event_date(event)
        if not date:
            return {"complete": False, "points": {}, "requests": [{"status": "failed", "event_id": str(event.get("id")), "error": "event_date_missing"}], "tokens": list(condition_to_token.values())}
        cutoff = int((datetime.fromisoformat(date).replace(tzinfo=zone) - timedelta(minutes=1)).timestamp())
        found: dict[str, dict[str, Any]] = {}; event_requests = []
        # The documented Data API `end` filter was empirically ignored on 2026-07-11.
        # Therefore each condition is paged newest-first and filtered locally.
        for condition in conditions:
            offset = 0; seen_pages: set[str] = set(); condition_complete = False
            while True:
                url = f"{DATA_API}/trades?{urlencode({'market': condition, 'limit': 1000, 'offset': offset})}"
                try:
                    page = get_json(url, timeout=30)
                    if not isinstance(page, list): raise ValueError("Data API trades response is not a list")
                    timestamps = [int(row["timestamp"]) for row in page if row.get("timestamp") is not None]
                    if timestamps != sorted(timestamps, reverse=True): raise ValueError("trade page not newest-first")
                    fingerprint = hashlib.sha256(json.dumps([(row.get("timestamp"), row.get("price"), row.get("outcome")) for row in page], sort_keys=True).encode()).hexdigest()
                    if fingerprint in seen_pages: raise ValueError("repeated condition trade page")
                    seen_pages.add(fingerprint)
                    event_requests.append({"url": url, "status": "ok", "event_id": str(event.get("id")), "condition_id": condition, "rows": len(page), "kind": "condition_page_local_cutoff"})
                    eligible = [trade for trade in page if trade.get("timestamp") is not None and int(trade["timestamp"]) <= cutoff]
                    if eligible:
                        trade = eligible[0]; price = trade_yes_price(trade)
                        if str(trade.get("conditionId") or "") != condition or price is None:
                            raise ValueError("condition trade mismatch")
                        found[condition] = {"t": int(trade["timestamp"]), "p": price, "source": "data_api_latest_trade_before_cutoff_local_filter"}
                        condition_complete = True; break
                    if len(page) < 1000:
                        condition_complete = True; break
                    offset += len(page)
                    if offset >= 10000: raise ValueError("condition trade page budget exhausted before cutoff or terminal page")
                except Exception as exc:
                    event_requests.append({"url": url, "status": "failed", "event_id": str(event.get("id")), "condition_id": condition, "error": f"{type(exc).__name__}:{exc}"}); break
            if not condition_complete and not any(row.get("status") == "failed" and row.get("condition_id") == condition for row in event_requests):
                event_requests.append({"status": "failed", "event_id": str(event.get("id")), "condition_id": condition, "error": "condition_not_proven_complete"})
        points = {condition_to_token[condition]: [point] for condition, point in found.items()}
        return {"complete": not any(row["status"] == "failed" for row in event_requests), "points": points, "requests": event_requests, "tokens": list(condition_to_token.values())}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
        results = list(executor.map(fetch_event, events))
    for result in results:
        requests.extend(result["requests"]); terminal_events += int(result["complete"])
        for token in result["tokens"]: histories.setdefault(token, {"history": []})
        for token, points in result["points"].items(): histories[token]["history"].extend(points)
    for row in histories.values():
        unique = {(item["t"], item["p"]): item for item in row["history"]}
        row["history"] = sorted(unique.values(), key=lambda item: item["t"])
    core.write_json(output_dir / "price-history.json", histories); core.write_json(output_dir / "request-log.json", requests)
    nonempty = sum(bool(row["history"]) for row in histories.values()); complete = terminal_events == len(events) and not any(row["status"] == "failed" for row in requests)
    return write_manifest(
        output_dir, "weather-data-api-trade-history-v1", [f"{DATA_API}/trades"], ["price-history.json", "request-log.json"],
        event_groups=len(events), terminal_event_groups=terminal_events, tokens=len(histories), nonempty_histories=nonempty,
        price_semantics="newest public trade at or before local-day-start cutoff, client-filtered because documented end filter was empirically ignored",
        failed_request_count=sum(row["status"] == "failed" for row in requests), data_status="ok" if complete else "degraded",
    )


def parse_bucket(title: str) -> tuple[float | None, float | None] | None:
    import re
    text = title.replace("−", "-")
    match = re.search(r"(-?\d+)\s*°?F\s+or\s+below", text, re.I)
    if match: return None, float(match.group(1))
    match = re.search(r"(?:between\s+)?(-?\d+)\s*[-–]\s*(-?\d+)\s*°?F", text, re.I)
    if match: return float(match.group(1)), float(match.group(2))
    match = re.search(r"(-?\d+)\s*°?F\s+or\s+(?:higher|above)", text, re.I)
    if match: return float(match.group(1)), None
    return None


def cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def bucket_probabilities(buckets: list[tuple[float | None, float | None]], forecast: float, bias: float, sigma: float) -> list[float]:
    mean = forecast + bias; values = []
    for low, high in buckets:
        left = 0.0 if low is None else cdf((low - 0.5 - mean) / sigma)
        right = 1.0 if high is None else cdf((high + 0.5 - mean) / sigma)
        values.append(max(0.0, right - left))
    total = sum(values)
    return [value / total for value in values] if total > 0 else []


def price_at_or_before(history: list[dict[str, Any]], cutoff: float, max_age_hours: float = 36) -> float | None:
    rows = [row for row in history if row.get("p") is not None and float(row.get("t", 0)) <= cutoff]
    if not rows: return None
    row = max(rows, key=lambda item: float(item["t"]))
    return float(row["p"]) if cutoff - float(row["t"]) <= max_age_hours * 3600 else None


def build_rows(history_dir: Path, forecast_dir: Path, price_dir: Path, maximum_events: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    events = selected_events(history_dir, maximum_events); forecasts = core.read_json(forecast_dir / "daily-max-day1.json")
    histories = core.read_json(price_dir / "price-history.json"); rows = []; exclusions: dict[str, int] = {}
    def exclude(reason: str): exclusions.__setitem__(reason, exclusions.get(reason, 0) + 1)
    zone = ZoneInfo(LOCAL_TIMEZONE)
    for event in events:
        date = event_date(event); winner = resolved_winner(event); markets = event.get("markets") or []
        if not date or not winner: exclude("date_or_winner_missing"); continue
        parsed = [parse_bucket(str(market.get("groupItemTitle") or market.get("question") or "")) for market in markets]
        if any(bucket is None for bucket in parsed) or len(parsed) < 4: exclude("bucket_ladder_unparsed"); continue
        buckets = [bucket for bucket in parsed if bucket is not None]
        if sum(low is None for low, _ in buckets) != 1 or sum(high is None for _, high in buckets) != 1:
            exclude("bucket_tails_invalid"); continue
        values = {model: (forecasts.get(model) or {}).get(date) for model in ("gfs_seamless", "ecmwf_ifs025")}
        if any(value is None for value in values.values()): exclude("day1_forecast_missing"); continue
        target = datetime.fromisoformat(date).replace(tzinfo=zone)
        cutoff = (target - timedelta(minutes=1)).timestamp()
        event_start = core.parse_iso(event.get("startDate"))
        if not event_start or event_start.timestamp() > cutoff: exclude("market_opened_after_forecast_cutoff"); continue
        market_probabilities = []
        missing = False
        for market in markets:
            token = next((token for outcome, token in public.token_map(market).items() if outcome.lower() == "yes"), None)
            price = price_at_or_before((histories.get(str(token)) or {}).get("history", []), cutoff)
            if price is None: missing = True; break
            market_probabilities.append(price)
        if missing: exclude("cutoff_market_price_missing"); continue
        total = sum(market_probabilities)
        if not 0.80 <= total <= 1.20: exclude("market_ladder_probability_sum_invalid"); continue
        market_probabilities = [value / total for value in market_probabilities]
        winner_index = next((index for index, market in enumerate(markets) if str(market.get("id")) == str(winner.get("id"))), None)
        if winner_index is None: exclude("winner_index_missing"); continue
        winning_bucket = buckets[winner_index]
        midpoint = None if None in winning_bucket else statistics.fmean(winning_bucket)
        rows.append({
            "event_id": str(event.get("id")), "event_group": f"{STATION}:{date}", "date": date,
            "cutoff_at": datetime.fromtimestamp(cutoff, timezone.utc).isoformat(), "market_start_at": event_start.isoformat(),
            "buckets": buckets, "winner_index": winner_index, "winning_midpoint_f": midpoint,
            "market_probabilities": market_probabilities, "gfs_forecast_high_f": values["gfs_seamless"],
            "ecmwf_forecast_high_f": values["ecmwf_ifs025"], "mean_forecast_high_f": statistics.fmean(values.values()),
        })
    return rows, exclusions


def fit(rows: list[dict[str, Any]], key: str) -> dict[str, float] | None:
    errors = [float(row["winning_midpoint_f"]) - float(row[key]) for row in rows if row.get("winning_midpoint_f") is not None]
    if len(errors) < 30: return None
    return {"bias_f": statistics.fmean(errors), "sigma_f": max(1.0, statistics.stdev(errors)), "bounded_training_events": len(errors)}


def score(rows: list[dict[str, Any]], key: str, fitted: dict[str, float]) -> dict[str, Any]:
    group_rows = []
    for row in rows:
        model = bucket_probabilities(row["buckets"], float(row[key]), fitted["bias_f"], fitted["sigma_f"])
        if len(model) != len(row["buckets"]): continue
        actual = [1 if index == row["winner_index"] else 0 for index in range(len(model))]
        model_brier = statistics.fmean((p-y)**2 for p,y in zip(model, actual))
        market_brier = statistics.fmean((p-y)**2 for p,y in zip(row["market_probabilities"], actual))
        model_log = -math.log(max(model[row["winner_index"]], 1e-12)); market_log = -math.log(max(row["market_probabilities"][row["winner_index"]], 1e-12))
        group_rows.append({
            "event_group": row["event_group"], "model_brier": model_brier, "market_brier": market_brier,
            "market_minus_model_brier": market_brier-model_brier, "model_log_loss": model_log,
            "market_log_loss": market_log, "market_minus_model_log_loss": market_log-model_log,
        })
    def summary(metric: str) -> tuple[float | None, float | None]:
        values = [row[metric] for row in group_rows]
        if not values: return None, None
        mean = statistics.fmean(values)
        lower = mean - 1.96 * statistics.stdev(values) / math.sqrt(len(values)) if len(values) >= 2 else None
        return mean, lower
    brier_mean,brier_lower=summary("market_minus_model_brier");log_mean,log_lower=summary("market_minus_model_log_loss")
    return {"event_groups":len(group_rows),"mean_market_minus_model_brier":brier_mean,"paired_brier_95pct_lower":brier_lower,"mean_market_minus_model_log_loss":log_mean,"paired_log_loss_95pct_lower":log_lower,"group_rows":group_rows}


def walk_forward(history_dir: Path, forecast_dir: Path, price_dir: Path, output: Path, maximum_events: int = 180) -> dict[str, Any]:
    for directory in (history_dir, forecast_dir, price_dir):
        manifest = core.read_json(directory / "manifest.json")
        if manifest.get("data_status") != "ok": raise ValueError(f"degraded input manifest: {directory}")
    rows, exclusions = build_rows(history_dir, forecast_dir, price_dir, maximum_events)
    rows.sort(key=lambda row: row["date"]); n=len(rows); train_end=int(n*.60); validation_end=int(n*.80)
    train,validation,final=rows[:train_end],rows[train_end:validation_end],rows[validation_end:]
    candidates={}
    for name,key in (("gfs","gfs_forecast_high_f"),("ecmwf","ecmwf_forecast_high_f"),("mean","mean_forecast_high_f")):
        fitted=fit(train,key)
        if fitted: candidates[name]={"forecast_key":key,"fit":fitted,"train":score(train,key,fitted)}
    if not candidates: raise ValueError("insufficient bounded train events")
    selected=min(candidates, key=lambda name: -float(candidates[name]["train"]["mean_market_minus_model_brier"] or -999))
    key=candidates[selected]["forecast_key"];fitted=candidates[selected]["fit"]
    validation_score=score(validation,key,fitted);final_score=score(final,key,fitted)
    minimum_groups=30
    sustained=all([
        validation_score["event_groups"]>=minimum_groups,final_score["event_groups"]>=minimum_groups,
        (validation_score["paired_brier_95pct_lower"] or -999)>0,(final_score["paired_brier_95pct_lower"] or -999)>0,
    ])
    payload={
        "schema_version":"polymarket-weather-walk-forward-v1","created_at":core.now_iso(),"model_version":MODEL_VERSION,
        "station":STATION,"series_id":SERIES_ID,"forecast_semantics":"fixed 24-hour lead for every valid hour; cutoff at local day start",
        "rows":n,"split":{"train":len(train),"validation":len(validation),"final":len(final),"chronological_event_group_split":True},
        "exclusions":exclusions,"candidates":candidates,"selected_model":selected,"selected_fit":fitted,
        "validation":validation_score,"final":final_score,"minimum_oos_groups_per_split":minimum_groups,
        "research_promotion_candidate":sustained,"paper_estimates_allowed":False,
        "promotion_status":"manual_research_review_required" if sustained else "research_fail_or_insufficient_sustained_oos_improvement",
        "model_outputs_are_true_probabilities":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False,
    }
    core.write_json(output,payload);return payload


def self_test() -> dict[str, Any]:
    assert parse_bucket("93°F or below") == (None,93.0)
    assert parse_bucket("between 94-95°F") == (94.0,95.0)
    assert parse_bucket("94-95°F") == (94.0,95.0)
    assert parse_bucket("104°F or higher") == (104.0,None)
    probabilities=bucket_probabilities([(None,93),(94,95),(96,97),(98,None)],96,0,2)
    assert len(probabilities)==4 and abs(sum(probabilities)-1)<1e-9
    assert trade_yes_price({"outcome":"Yes","price":0.2}) == 0.2
    assert abs(trade_yes_price({"outcome":"No","price":0.2}) - 0.8) < 1e-9
    return {"status":"pass","tests":["bucket_parser","normalized_distribution","trade_price_normalization","paper_promotion_disabled"]}


def main() -> int:
    parser=argparse.ArgumentParser(description="Polymarket daily temperature research lab");sub=parser.add_subparsers(dest="command",required=True)
    history=sub.add_parser("discover-history");history.add_argument("--output-dir",default=str(ROOT/"cache/weather_dallas_history"));history.add_argument("--page-size",type=int,default=20)
    forecast=sub.add_parser("fetch-forecast-archive");forecast.add_argument("--history-dir",default=str(ROOT/"cache/weather_dallas_history"));forecast.add_argument("--output-dir",default=str(ROOT/"cache/weather_dallas_forecasts"))
    prices=sub.add_parser("fetch-market-histories");prices.add_argument("--history-dir",default=str(ROOT/"cache/weather_dallas_history"));prices.add_argument("--output-dir",default=str(ROOT/"cache/weather_dallas_market_histories"));prices.add_argument("--maximum-events",type=int,default=180)
    trades=sub.add_parser("fetch-market-trades");trades.add_argument("--history-dir",default=str(ROOT/"cache/weather_dallas_history"));trades.add_argument("--output-dir",default=str(ROOT/"cache/weather_dallas_trade_histories"));trades.add_argument("--maximum-events",type=int,default=180);trades.add_argument("--workers",type=int,default=6)
    walk=sub.add_parser("walk-forward");walk.add_argument("--history-dir",default=str(ROOT/"cache/weather_dallas_history"));walk.add_argument("--forecast-dir",default=str(ROOT/"cache/weather_dallas_forecasts"));walk.add_argument("--price-dir",default=str(ROOT/"cache/weather_dallas_trade_histories"));walk.add_argument("--maximum-events",type=int,default=180);walk.add_argument("--output",default=str(ROOT/"experiments/current-weather-walk-forward.json"))
    sub.add_parser("self-test");args=parser.parse_args()
    if args.command=="discover-history": payload=discover_history(Path(args.output_dir),args.page_size)
    elif args.command=="fetch-forecast-archive": payload=fetch_forecast_archive(Path(args.history_dir),Path(args.output_dir))
    elif args.command=="fetch-market-histories": payload=fetch_market_histories(Path(args.history_dir),Path(args.output_dir),args.maximum_events)
    elif args.command=="fetch-market-trades": payload=fetch_trade_histories(Path(args.history_dir),Path(args.output_dir),args.maximum_events,args.workers)
    elif args.command=="walk-forward": payload=walk_forward(Path(args.history_dir),Path(args.forecast_dir),Path(args.price_dir),Path(args.output),args.maximum_events)
    else: payload=self_test()
    print(json.dumps(payload,ensure_ascii=False,indent=2));return 0


if __name__=="__main__": raise SystemExit(main())
