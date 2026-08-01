#!/usr/bin/env python3
"""Leakage-safe research lab for Polymarket weekly US-equity hit-price markets.

The lab uses only public Gamma/CLOB data plus split-adjusted Yahoo daily OHLC.
It never emits main-ledger estimates and never calls a private or order API.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import statistics
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
EASTERN = ZoneInfo("America/New_York")
YAHOO = "https://query1.finance.yahoo.com"
SERIES = {
    "AAPL": "11374", "MSFT": "11375", "AMZN": "11376", "GOOGL": "11377",
    "META": "11378", "TSLA": "11379", "NVDA": "11380", "NFLX": "11381",
    "PLTR": "11382", "OPEN": "11383", "RKLB": "11384", "ABNB": "11385",
    "COIN": "11386", "HOOD": "11387", "SPY": "11389", "EWY": "11390",
    "MU": "11811",
}
LOOKBACKS = (63, 126, 252)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load_module("stock_weekly_core", ROOT / "scripts" / "polymarket_alpha.py")
public = load_module("stock_weekly_public", ROOT / "scripts" / "polymarket_public_data.py")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(directory: Path, schema: str, sources: list[str], files: list[str], **fields: Any) -> dict[str, Any]:
    payload = {
        "schema_version": schema, "created_at": now_iso(), "sources": sources,
        "files": [{"path": name, "bytes": (directory / name).stat().st_size, "sha256": sha256(directory / name)} for name in files],
        "paper_only": True, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False, **fields,
    }
    core.write_json(directory / "manifest.json", payload)
    return payload


def parse_contract(question: str) -> dict[str, Any] | None:
    match = re.search(
        r"Will .+? \(([A-Z.]+)\) hit \((HIGH|LOW)\) \$([\d,]+(?:\.\d+)?) Week of (.+?)\??$",
        question, re.I,
    )
    if not match:
        return None
    try:
        week = datetime.strptime(match.group(4), "%B %d %Y").date()
    except ValueError:
        return None
    return {"symbol": match.group(1).upper(), "direction": match.group(2).lower(), "threshold": float(match.group(3).replace(",", "")), "week_start": week.isoformat()}


def resolved_yes(market: dict[str, Any]) -> int | None:
    inferred = public.infer_resolution(market)
    winner = str(inferred.get("winning_outcome") or "").lower()
    if winner == "yes":
        return 1
    if winner == "no":
        return 0
    return None


def discover_history(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    events, requests = [], []
    for symbol, series_id in SERIES.items():
        params = {"series_id": series_id, "closed": "true", "limit": 100, "offset": 0, "order": "endDate", "ascending": "true"}
        url = f"{public.GAMMA_BASE}/events?{urlencode(params)}"
        try:
            page = public.get_json(url)
            if not isinstance(page, list):
                raise ValueError("events response is not a list")
            requests.append({"url": url, "status": "ok", "rows": len(page), "terminal": len(page) < 100})
            for event in page:
                markets = event.get("markets") if isinstance(event, dict) else None
                parsed = [parse_contract(str(row.get("question") or "")) for row in (markets or [])]
                if not markets or any(item is None or item["symbol"] != symbol for item in parsed):
                    continue
                if any(resolved_yes(row) is None for row in markets):
                    continue
                events.append({**event, "lab_symbol": symbol, "lab_series_id": series_id})
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
    events.sort(key=lambda row: (str(row.get("endDate")), row["lab_symbol"]))
    core.write_json(output_dir / "events.json", events)
    core.write_json(output_dir / "request-log.json", requests)
    event_groups = {f"{row['lab_symbol']}:{parse_contract(row['markets'][0]['question'])['week_start']}" for row in events}
    complete = len(requests) == len(SERIES) and all(row.get("status") == "ok" and row.get("terminal") for row in requests)
    return write_manifest(
        output_dir, "stock-weekly-gamma-history-v1", [f"{public.GAMMA_BASE}/events"], ["events.json", "request-log.json"],
        symbols=len(SERIES), event_groups=len(event_groups), markets=sum(len(row.get("markets") or []) for row in events),
        failed_request_count=sum(row.get("status") == "failed" for row in requests), data_status="ok" if complete else "degraded",
    )


def fetch_market_prices(history_dir: Path, output_dir: Path, chunk_size: int = 20) -> dict[str, Any]:
    source_manifest = core.read_json(history_dir / "manifest.json")
    if source_manifest.get("data_status") != "ok":
        raise ValueError("history manifest degraded")
    events = core.read_json(history_dir / "events.json")
    output_dir.mkdir(parents=True, exist_ok=True)
    target_tokens = []
    for event in events:
        for market in event.get("markets") or []:
            token = next((value for outcome, value in public.token_map(market).items() if outcome.lower() == "yes"), None)
            if token and token not in target_tokens:
                target_tokens.append(token)
    histories = core.read_json(output_dir / "price-history.json") if (output_dir / "price-history.json").exists() else {}
    requests = core.read_json(output_dir / "request-log.json") if (output_dir / "request-log.json").exists() else []
    for token, record in histories.items():
        if record.get("history") and record.get("fidelity_minutes") is None:
            record["fidelity_minutes"] = 60
    for fidelity in (60, 1440):
        missing = [token for token in target_tokens if not (histories.get(token) or {}).get("history")]
        for index in range(0, len(missing), chunk_size):
            chunk = missing[index:index + chunk_size]
            body = {"markets": chunk, "interval": "max", "fidelity": fidelity}
            try:
                payload = public.post_json(f"{public.CLOB_BASE}/batch-prices-history", body)
                batch = payload.get("history", {}) if isinstance(payload, dict) else {}
                for token in chunk:
                    rows = batch.get(token, []) if isinstance(batch, dict) else []
                    unique = {(int(row["t"]), float(row["p"])): {"t": int(row["t"]), "p": float(row["p"])} for row in rows if row.get("t") is not None and row.get("p") is not None}
                    histories[token] = {"history": sorted(unique.values(), key=lambda row: row["t"]), "fidelity_minutes": fidelity}
                requests.append({"status": "ok", "fidelity_minutes": fidelity, "token_count": len(chunk), "nonempty": sum(bool((histories.get(token) or {}).get("history")) for token in chunk)})
            except Exception as exc:
                requests.append({"status": "failed", "fidelity_minutes": fidelity, "token_count": len(chunk), "error": f"{type(exc).__name__}:{exc}"})
            core.write_json(output_dir / "price-history.json", histories)
            core.write_json(output_dir / "request-log.json", requests)
    nonempty = sum(bool((histories.get(token) or {}).get("history")) for token in target_tokens)
    coverage = nonempty / len(target_tokens) if target_tokens else 0.0
    return write_manifest(
        output_dir, "stock-weekly-clob-history-v1", [f"{public.CLOB_BASE}/batch-prices-history"], ["price-history.json", "request-log.json"],
        fidelity_minutes_preference=[60, 1440], fidelity_fallback_reason="older tokens can return empty at 60 while retaining daily max history",
        fidelity_counts={str(value): sum((histories.get(token) or {}).get("fidelity_minutes") == value and bool((histories.get(token) or {}).get("history")) for token in target_tokens) for value in (60, 1440)},
        target_tokens=len(target_tokens), nonempty_histories=nonempty,
        missing_tokens=[token for token in target_tokens if not (histories.get(token) or {}).get("history")],
        history_coverage_pct=coverage * 100,
        contract_exclusion_policy="never impute missing market history; event groups require at least 8 priced contracts and both HIGH/LOW directions",
        data_status="ok" if coverage >= 0.95 else "degraded",
    )


def yahoo_json(url: str) -> Any:
    if not url.startswith(YAHOO + "/"):
        raise ValueError("non-Yahoo URL blocked")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 polymarket-alpha-paper-research/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def fetch_ohlc(output_dir: Path, start: str = "2024-01-01") -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    start_ts = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    end_ts = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp())
    all_rows, requests = {}, []
    for symbol in SERIES:
        params = {"period1": start_ts, "period2": end_ts, "interval": "1d", "events": "history", "includeAdjustedClose": "true"}
        url = f"{YAHOO}/v8/finance/chart/{symbol}?{urlencode(params)}"
        try:
            payload = yahoo_json(url)
            result = payload["chart"]["result"][0]
            quote = result["indicators"]["quote"][0]
            adjusted = (result["indicators"].get("adjclose") or [{}])[0].get("adjclose") or quote["close"]
            rows = []
            for index, stamp in enumerate(result["timestamp"]):
                values = {key: quote[key][index] for key in ("open", "high", "low", "close")}
                if any(value is None for value in values.values()) or adjusted[index] is None:
                    continue
                factor = float(adjusted[index]) / float(values["close"])
                rows.append({
                    "date": datetime.fromtimestamp(stamp, EASTERN).date().isoformat(), "timestamp": int(stamp),
                    **{key: float(value) * factor for key, value in values.items()}, "adjustment_factor": factor,
                })
            all_rows[symbol] = rows
            requests.append({"symbol": symbol, "url": url, "status": "ok", "rows": len(rows)})
        except Exception as exc:
            requests.append({"symbol": symbol, "url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
    core.write_json(output_dir / "ohlc.json", all_rows)
    core.write_json(output_dir / "request-log.json", requests)
    complete = len(all_rows) == len(SERIES) and all(len(rows) >= 300 for rows in all_rows.values())
    return write_manifest(
        output_dir, "stock-weekly-yahoo-ohlc-v1", [YAHOO], ["ohlc.json", "request-log.json"],
        symbols=len(all_rows), start_date=start, split_adjusted_ohlc=True,
        row_counts={symbol: len(rows) for symbol, rows in all_rows.items()},
        failed_request_count=sum(row["status"] == "failed" for row in requests), data_status="ok" if complete else "degraded",
    )


def cutoff_for_week(week_start: str) -> datetime:
    day = date.fromisoformat(week_start)
    return datetime(day.year, day.month, day.day, 9, 29, tzinfo=EASTERN).astimezone(timezone.utc)


def price_at_cutoff(history: list[dict[str, Any]], cutoff: datetime, market_start: datetime) -> float | None:
    rows = [row for row in history if market_start.timestamp() <= float(row.get("t", 0)) <= cutoff.timestamp()]
    if not rows:
        return None
    return float(max(rows, key=lambda row: float(row["t"]))["p"])


def daily_vol(rows: list[dict[str, Any]], end_index: int, window: int = 20) -> float | None:
    start = max(1, end_index - window + 1)
    values = [math.log(float(rows[index]["close"]) / float(rows[index - 1]["close"])) for index in range(start, end_index + 1) if rows[index - 1]["close"] > 0]
    return statistics.stdev(values) if len(values) >= 10 else None


def empirical_path_distribution(
    rows: list[dict[str, Any]], week_start: str, direction: str, lookback: int, volatility_scaled: bool,
) -> dict[str, Any] | None:
    target_date = date.fromisoformat(week_start)
    cutoff_index = next((index for index, row in enumerate(rows) if date.fromisoformat(row["date"]) >= target_date), len(rows))
    if cutoff_index < 30:
        return None
    spot = float(rows[cutoff_index - 1]["close"])
    current_vol = daily_vol(rows, cutoff_index - 1)
    start_index = max(20, cutoff_index - lookback - 5)
    samples = []
    for base_index in range(start_index, cutoff_index - 5):
        base = float(rows[base_index]["close"])
        future = rows[base_index + 1:base_index + 6]
        if base <= 0 or len(future) < 5:
            continue
        scale = 1.0
        if volatility_scaled:
            past_vol = daily_vol(rows, base_index)
            if current_vol is None or past_vol is None or past_vol <= 0:
                continue
            scale = min(2.0, max(0.5, current_vol / past_vol))
        if direction == "high":
            path_ratio = math.exp(math.log(max(float(day["high"]) for day in future) / base) * scale)
            samples.append(path_ratio)
        else:
            path_ratio = math.exp(math.log(min(float(day["low"]) for day in future) / base) * scale)
            samples.append(path_ratio)
    if len(samples) < 40:
        return None
    return {"path_ratios": samples, "spot_at_cutoff": spot}


def probability_from_distribution(distribution: dict[str, Any], direction: str, threshold: float) -> dict[str, Any]:
    samples = distribution["path_ratios"]
    spot = float(distribution["spot_at_cutoff"])
    target_ratio = threshold / spot
    hits = sum(value >= target_ratio for value in samples) if direction == "high" else sum(value <= target_ratio for value in samples)
    probability = (hits + 0.5) / (len(samples) + 1.0)
    standard_error = math.sqrt(probability * (1 - probability) / (len(samples) + 1.0))
    return {
        "probability": min(0.995, max(0.005, probability)),
        "confidence_low": max(0.0, probability - 1.96 * standard_error),
        "confidence_high": min(1.0, probability + 1.96 * standard_error),
        "samples": len(samples), "hits": hits, "spot_at_cutoff": spot,
    }


def empirical_probability(
    rows: list[dict[str, Any]], week_start: str, direction: str, threshold: float,
    lookback: int, volatility_scaled: bool,
) -> dict[str, Any] | None:
    distribution = empirical_path_distribution(rows, week_start, direction, lookback, volatility_scaled)
    return probability_from_distribution(distribution, direction, threshold) if distribution else None


def build_rows(history_dir: Path, price_dir: Path, ohlc_dir: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    events = core.read_json(history_dir / "events.json")
    histories = core.read_json(price_dir / "price-history.json")
    ohlc = core.read_json(ohlc_dir / "ohlc.json")
    rows, exclusions, distribution_cache = [], {}, {}
    def exclude(reason: str):
        exclusions[reason] = exclusions.get(reason, 0) + 1
    for event in events:
        symbol = event["lab_symbol"]
        for market in event.get("markets") or []:
            parsed = parse_contract(str(market.get("question") or ""))
            actual = resolved_yes(market)
            if not parsed or actual is None:
                exclude("parse_or_resolution_missing"); continue
            cutoff = cutoff_for_week(parsed["week_start"])
            market_start = core.parse_iso(market.get("startDate") or event.get("startDate"))
            if not market_start or market_start >= cutoff:
                exclude("market_open_after_cutoff"); continue
            token = next((value for outcome, value in public.token_map(market).items() if outcome.lower() == "yes"), None)
            market_probability = price_at_cutoff((histories.get(str(token)) or {}).get("history", []), cutoff, market_start)
            if market_probability is None:
                exclude("cutoff_price_missing"); continue
            predictions = {}
            for lookback in LOOKBACKS:
                for scaled in (False, True):
                    key = f"empirical_{lookback}_{'vol_scaled' if scaled else 'raw'}"
                    cache_key = (symbol, parsed["week_start"], parsed["direction"], lookback, scaled)
                    if cache_key not in distribution_cache:
                        distribution_cache[cache_key] = empirical_path_distribution(ohlc.get(symbol, []), parsed["week_start"], parsed["direction"], lookback, scaled)
                    distribution = distribution_cache[cache_key]
                    prediction = probability_from_distribution(distribution, parsed["direction"], parsed["threshold"]) if distribution else None
                    if prediction:
                        predictions[key] = prediction
            if len(predictions) != len(LOOKBACKS) * 2:
                exclude("model_history_insufficient"); continue
            rows.append({
                "event_id": str(event.get("id")), "market_id": str(market.get("id")),
                "event_group": f"{symbol}:{parsed['week_start']}", "week_start": parsed["week_start"],
                "symbol": symbol, "direction": parsed["direction"], "threshold": parsed["threshold"],
                "cutoff_at": cutoff.isoformat(), "market_start_at": market_start.isoformat(),
                "market_probability": market_probability, "actual": actual, "predictions": predictions,
            })
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["event_group"], []).append(row)
    valid_groups = {
        group for group, values in grouped.items()
        if len(values) >= 8 and {row["direction"] for row in values} == {"high", "low"}
    }
    for group, values in grouped.items():
        if group not in valid_groups:
            exclude("event_group_market_coverage_incomplete")
    return [row for row in rows if row["event_group"] in valid_groups], exclusions


def log_loss(probability: float, actual: int) -> float:
    value = min(1 - 1e-9, max(1e-9, probability))
    return -(actual * math.log(value) + (1 - actual) * math.log(1 - value))


def score(rows: list[dict[str, Any]], model_key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, float]]] = {}
    for row in rows:
        model = float(row["predictions"][model_key]["probability"]); market = float(row["market_probability"]); actual = int(row["actual"])
        groups.setdefault(row["event_group"], []).append({
            "market_minus_model_brier": (market - actual) ** 2 - (model - actual) ** 2,
            "market_minus_model_log_loss": log_loss(market, actual) - log_loss(model, actual),
        })
    group_rows = []
    for group, values in sorted(groups.items()):
        group_rows.append({
            "event_group": group, "contracts": len(values),
            "market_minus_model_brier": statistics.fmean(row["market_minus_model_brier"] for row in values),
            "market_minus_model_log_loss": statistics.fmean(row["market_minus_model_log_loss"] for row in values),
        })
    def summary(key: str) -> tuple[float | None, float | None]:
        values = [row[key] for row in group_rows]
        if not values:
            return None, None
        mean = statistics.fmean(values)
        lower = mean - 1.96 * statistics.stdev(values) / math.sqrt(len(values)) if len(values) >= 2 else None
        return mean, lower
    brier, brier_low = summary("market_minus_model_brier"); log, log_low = summary("market_minus_model_log_loss")
    return {
        "event_groups": len(group_rows), "contracts": sum(row["contracts"] for row in group_rows),
        "mean_market_minus_model_brier": brier, "paired_brier_95pct_lower": brier_low,
        "mean_market_minus_model_log_loss": log, "paired_log_loss_95pct_lower": log_low,
        "group_rows": group_rows,
    }


def chronological_split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, list[str]]]:
    weeks = sorted({row["week_start"] for row in rows})
    train_end = max(1, int(len(weeks) * 0.60)); validation_end = max(train_end + 1, int(len(weeks) * 0.80))
    parts = {"train": weeks[:train_end], "validation": weeks[train_end:validation_end], "final": weeks[validation_end:]}
    return tuple([[row for row in rows if row["week_start"] in parts[name]] for name in ("train", "validation", "final")] + [parts])  # type: ignore[return-value]


def markdown(payload: dict[str, Any]) -> str:
    selected = payload["selected_model"]
    lines = [
        "# Polymarket Stock Weekly Hit-Price Research", "",
        f"- Status: `{payload['status']}`", f"- Model version: `{payload['model_version']}`",
        f"- Rows: {payload['rows']}", f"- Event groups: {payload['event_groups']}",
        f"- Weeks: {payload['weeks']}", f"- Selected train-only variant: `{selected}`",
        f"- Validation Brier improvement: {payload['validation']['mean_market_minus_model_brier']}",
        f"- Validation paired 95% lower: {payload['validation']['paired_brier_95pct_lower']}",
        f"- Final Brier improvement: {payload['final']['mean_market_minus_model_brier']}",
        f"- Final paired 95% lower: {payload['final']['paired_brier_95pct_lower']}", "",
        "Positive values mean the independent OHLC model beat the contemporaneous Polymarket cutoff price.",
        "Selection used train weeks only. Validation and final weeks were not used for model selection.",
        "This artifact is research-only and emits no main-ledger probability estimates.", "",
        "## Promotion", "", f"- Paper estimates allowed: `{str(payload['paper_estimates_allowed']).lower()}`",
    ]
    for blocker in payload["promotion_blockers"]:
        lines.append(f"- Blocker: `{blocker}`")
    return "\n".join(lines) + "\n"


def walk_forward(history_dir: Path, price_dir: Path, ohlc_dir: Path, output: Path, report: Path) -> dict[str, Any]:
    for directory in (history_dir, price_dir, ohlc_dir):
        if core.read_json(directory / "manifest.json").get("data_status") != "ok":
            raise ValueError(f"degraded input manifest: {directory}")
    rows, exclusions = build_rows(history_dir, price_dir, ohlc_dir)
    train, validation, final, split = chronological_split(rows)
    model_keys = sorted(next(iter(rows))["predictions"]) if rows else []
    if not model_keys or len(split["validation"]) < 2 or len(split["final"]) < 2:
        raise ValueError("insufficient chronological evidence")
    train_scores = {key: score(train, key) for key in model_keys}
    selected = max(model_keys, key=lambda key: float(train_scores[key]["mean_market_minus_model_brier"] or -999))
    validation_score, final_score = score(validation, selected), score(final, selected)
    minimum_groups = 30
    sustained = all([
        validation_score["event_groups"] >= minimum_groups, final_score["event_groups"] >= minimum_groups,
        (validation_score["paired_brier_95pct_lower"] or -999) > 0,
        (final_score["paired_brier_95pct_lower"] or -999) > 0,
    ])
    blockers = []
    if validation_score["event_groups"] < minimum_groups or final_score["event_groups"] < minimum_groups:
        blockers.append("minimum_30_event_groups_per_oos_split_not_met")
    if (validation_score["paired_brier_95pct_lower"] or -999) <= 0:
        blockers.append("validation_paired_brier_lower_not_positive")
    if (final_score["paired_brier_95pct_lower"] or -999) <= 0:
        blockers.append("final_paired_brier_lower_not_positive")
    blockers.extend(["single_external_ohlc_vendor_only", "manual_model_approval_required"])
    payload = {
        "schema_version": "polymarket-stock-weekly-walk-forward-v1", "created_at": now_iso(),
        "model_version": "pm-stock-weekly-empirical-v1", "status": "passed_oos_research" if sustained else "failed_oos_research",
        "research_promotion_candidate": sustained, "sustained_improvement": sustained,
        "promotion_status": "research_pass_pending_multisource_and_manual_review" if sustained else "research_fail_no_sustained_oos_improvement",
        "model_outputs_are_true_probabilities": False,
        "paper_only": True, "paper_estimates_allowed": False, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False,
        "rows": len(rows), "event_groups": len({row["event_group"] for row in rows}), "weeks": len({row["week_start"] for row in rows}),
        "split": {name: {"weeks": values, "rows": sum(row["week_start"] in values for row in rows), "event_groups": len({row["event_group"] for row in rows if row["week_start"] in values})} for name, values in split.items()},
        "chronological_week_split": True, "contract_correlation_clustered_by_symbol_week": True,
        "cutoff_semantics": "latest CLOB price before Monday 09:30 America/New_York; model uses only prior daily OHLC",
        "selected_model": selected, "train_candidates": train_scores,
        "validation": validation_score, "final": final_score, "exclusions": exclusions,
        "final_holdout_inspected_once": True, "final_holdout_reuse_for_model_selection_allowed": False,
        "next_generation_requires_post_2026_07_11_forward_weeks": True,
        "promotion_blockers": blockers, "paper_estimates_allowed_if_research_passed": False,
        "source_manifests": {name: sha256(path / "manifest.json") for name, path in {"gamma": history_dir, "clob": price_dir, "ohlc": ohlc_dir}.items()},
    }
    core.write_json(output, payload)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(markdown(payload), encoding="utf-8")
    return payload


def self_test() -> dict[str, Any]:
    parsed = parse_contract("Will Apple (AAPL) hit (HIGH) $320 Week of July 13 2026?")
    assert parsed == {"symbol": "AAPL", "direction": "high", "threshold": 320.0, "week_start": "2026-07-13"}
    rows = []
    start = date(2024, 1, 1)
    price = 100.0
    for index in range(400):
        day = start + timedelta(days=index)
        if day.weekday() >= 5:
            continue
        price *= 1.0005
        rows.append({"date": day.isoformat(), "open": price, "high": price * 1.02, "low": price * 0.98, "close": price})
    near = empirical_probability(rows, "2025-06-02", "high", rows[-20]["close"] * 1.01, 126, False)
    far = empirical_probability(rows, "2025-06-02", "high", rows[-20]["close"] * 1.20, 126, False)
    assert near and far and near["probability"] > far["probability"]
    return {"status": "pass", "tests": ["contract_parser", "threshold_probability_monotonic"], "live_orders_enabled": False, "private_api_used": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover-history"); discover.add_argument("--output-dir", default=str(ROOT / "cache/stock_weekly_history"))
    prices = sub.add_parser("fetch-market-prices"); prices.add_argument("--history-dir", default=str(ROOT / "cache/stock_weekly_history")); prices.add_argument("--output-dir", default=str(ROOT / "cache/stock_weekly_cutoff_prices"))
    ohlc = sub.add_parser("fetch-ohlc"); ohlc.add_argument("--output-dir", default=str(ROOT / "cache/stock_weekly_ohlc")); ohlc.add_argument("--start", default="2024-01-01")
    walk = sub.add_parser("walk-forward"); walk.add_argument("--history-dir", default=str(ROOT / "cache/stock_weekly_history")); walk.add_argument("--price-dir", default=str(ROOT / "cache/stock_weekly_cutoff_prices")); walk.add_argument("--ohlc-dir", default=str(ROOT / "cache/stock_weekly_ohlc")); walk.add_argument("--output", default=str(ROOT / "experiments/current-stock-weekly-walk-forward.json")); walk.add_argument("--report", default=str(ROOT / "reports/CURRENT_STOCK_WEEKLY_RESEARCH.md"))
    sub.add_parser("self-test")
    args = parser.parse_args()
    if args.command == "discover-history": payload = discover_history(Path(args.output_dir))
    elif args.command == "fetch-market-prices": payload = fetch_market_prices(Path(args.history_dir), Path(args.output_dir))
    elif args.command == "fetch-ohlc": payload = fetch_ohlc(Path(args.output_dir), args.start)
    elif args.command == "walk-forward": payload = walk_forward(Path(args.history_dir), Path(args.price_dir), Path(args.ohlc_dir), Path(args.output), Path(args.report))
    else: payload = self_test()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
