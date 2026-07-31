#!/usr/bin/env python3
"""Forward-only diagnostics for the frozen, failed stock-weekly V1 model.

Forecasts are captured only during the 24 hours before Monday 09:29 ET.  The
script is research-only: it cannot emit entry estimates or mutate the main
paper ledger, and a missed capture window is never backfilled.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "stock_weekly"
MODEL_VERSION = "pm-stock-weekly-empirical-v1-forward-diagnostic-v2"
MODEL_FROZEN_AT = "2026-07-11T15:53:03.073938+00:00"
MODEL_KEY = "empirical_252_raw"
FIRST_FORWARD_WEEK = "2026-07-13"
CAPTURE_WINDOW_HOURS = 24
EXECUTION_NOTIONAL_USD = 75.0
EXTRA_SLIPPAGE_BPS = 8.0
BOOK_ACTIVITY_STALE_SECONDS = 900.0
MAX_SPREAD_PER_SHARE = 0.02
MAX_PRICE_IMPACT_PER_SHARE = 0.01
DEFAULT_LEDGER = ROOT / "data/stock_weekly_research_forecast_ledger.json"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load("stock_shadow_core", ROOT / "scripts/polymarket_alpha.py")
public = load("stock_shadow_public", ROOT / "scripts/polymarket_public_data.py")
lab = load("stock_shadow_lab", ROOT / "scripts/polymarket_stock_weekly_lab.py")


def file_sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def new_ledger() -> dict[str, Any]:
    return {
        "schema_version": "stock-weekly-research-forecast-ledger-v1",
        "created_at": core.now_iso(), "updated_at": core.now_iso(),
        "open_forecasts": [], "resolved_forecasts": [], "events": [],
        "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }


def load_ledger(path: Path) -> dict[str, Any]:
    ledger = core.read_json(path) if path.exists() else new_ledger()
    unsafe = (
        ledger.get("paper_estimates_emitted") is not False
        or ledger.get("main_paper_ledger_mutated") is not False
        or ledger.get("live_orders_enabled") is not False
        or ledger.get("private_api_used") is not False
    )
    if unsafe:
        raise ValueError("unsafe stock-weekly shadow ledger")
    return ledger


def capture_state(now: datetime, cutoff: datetime) -> str:
    if now >= cutoff:
        return "capture_window_missed"
    if now < cutoff - timedelta(hours=CAPTURE_WINDOW_HOURS):
        return "capture_window_not_reached"
    return "eligible"


def gamma_yes_probability(market: dict[str, Any]) -> float | None:
    outcomes = public.parse_jsonish(market.get("outcomes"))
    prices = public.parse_jsonish(market.get("outcomePrices"))
    if len(outcomes) != len(prices):
        return None
    for index, outcome in enumerate(outcomes):
        if str(outcome).lower() == "yes":
            try:
                value = float(prices[index])
            except (TypeError, ValueError):
                return None
            return value if 0.0 < value < 1.0 else None
    return None


def yes_token(market: dict[str, Any]) -> str | None:
    return next((token for outcome, token in public.token_map(market).items() if outcome.lower() == "yes"), None)


def fetch_yes_books(markets: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    tokens = [token for market in markets if (token := yes_token(market))]
    if len(tokens) != len(markets) or len(set(tokens)) != len(tokens):
        raise ValueError("stock-weekly YES token mapping incomplete or duplicated")
    url = f"{public.CLOB_BASE}/books"
    payload = public.post_json(url, [{"token_id": token} for token in tokens])
    if not isinstance(payload, list):
        raise ValueError("stock-weekly batch order books response is not a list")
    books = {str(row.get("asset_id")): row for row in payload if isinstance(row, dict) and row.get("asset_id")}
    missing = sorted(set(tokens) - set(books))
    requests = [{"url": url, "method": "POST", "status": "ok", "requested": len(tokens), "received": len(books), "missing": missing}]
    return books, requests


def executable_market_snapshot(
    book: dict[str, Any], gamma_probability: float | None, observed_at: datetime,
) -> dict[str, Any] | None:
    bids, asks = core.levels(book, "bids"), core.levels(book, "asks")
    if not bids or not asks:
        return None
    best_bid, best_ask = bids[0][0], asks[0][0]
    if best_bid >= best_ask:
        return None
    try:
        timestamp_ms = int(book["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None
    age_seconds = observed_at.timestamp() - timestamp_ms / 1000.0
    if age_seconds < -60:
        return None
    execution = core.simulate_buy(book, EXECUTION_NOTIONAL_USD, EXTRA_SLIPPAGE_BPS)
    midpoint = (best_bid + best_ask) / 2.0
    spread = best_ask - best_bid
    execution_gate_passed = bool(
        execution.get("fillable")
        and spread <= MAX_SPREAD_PER_SHARE
        and float(execution.get("price_impact") or 0.0) <= MAX_PRICE_IMPACT_PER_SHARE
    )
    return {
        "market_probability": midpoint,
        "gamma_yes_probability": gamma_probability,
        "gamma_midpoint_divergence": abs(gamma_probability - midpoint) if gamma_probability is not None else None,
        "best_bid": best_bid, "best_ask": best_ask, "spread": spread,
        "book_timestamp_ms": timestamp_ms, "book_age_seconds": age_seconds,
        "book_activity_age_exceeds_15m": age_seconds > BOOK_ACTIVITY_STALE_SECONDS,
        "book_hash": book.get("hash"), "bid_levels": len(bids), "ask_levels": len(asks),
        "planned_execution_notional_usd": EXECUTION_NOTIONAL_USD,
        "execution_quote": execution, "execution_gate_passed": execution_gate_passed,
        "execution_gate_limits": {
            "max_spread_per_share": MAX_SPREAD_PER_SHARE,
            "max_price_impact_per_share": MAX_PRICE_IMPACT_PER_SHARE,
            "book_activity_stale_diagnostic_seconds": BOOK_ACTIVITY_STALE_SECONDS,
        },
    }


def active_events() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events, requests = [], []
    for symbol, series_id in lab.SERIES.items():
        url = f"{public.GAMMA_BASE}/events?{urlencode({'series_id': series_id, 'closed': 'false', 'limit': 20, 'order': 'endDate', 'ascending': 'true'})}"
        try:
            payload = public.get_json(url)
            if not isinstance(payload, list):
                raise ValueError("events response is not a list")
            requests.append({"url": url, "status": "ok", "rows": len(payload)})
            for event in payload:
                if isinstance(event, dict):
                    events.append({**event, "shadow_symbol": symbol})
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
    return events, requests


def settle(ledger: dict[str, Any]) -> dict[str, Any]:
    details, requests, settled = {}, [], []
    for event_id in sorted({str(row["event_id"]) for row in ledger.get("open_forecasts", [])}):
        url = f"{public.GAMMA_BASE}/events/{event_id}"
        try:
            details[event_id] = public.get_json(url)
            requests.append({"url": url, "status": "ok"})
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
    for row in list(ledger.get("open_forecasts", [])):
        event = details.get(str(row["event_id"])) or {}
        market = next((item for item in event.get("markets") or [] if str(item.get("id")) == str(row["market_id"])), None)
        actual = lab.resolved_yes(market) if market else None
        if actual is None:
            continue
        closed = {**row, "status": "resolved", "resolved_at": core.now_iso(), "actual_yes": actual}
        ledger["open_forecasts"].remove(row)
        ledger["resolved_forecasts"].append(closed)
        settled.append(row["forecast_id"])
    return {"settled": settled, "requests": requests}


def audit(ledger: dict[str, Any]) -> dict[str, Any]:
    resolved = [row for row in ledger.get("resolved_forecasts", []) if row.get("actual_yes") in {0, 1}]
    def score_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float | None, float | None]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(row["event_group"], []).append(row)
        scored = []
        for group, items in sorted(groups.items()):
            model = statistics.fmean((float(row["model_score"]) - row["actual_yes"]) ** 2 for row in items)
            market = statistics.fmean((float(row["market_probability_at_forecast"]) - row["actual_yes"]) ** 2 for row in items)
            scored.append({
                "event_group": group, "contracts": len(items), "model_brier": model,
                "market_brier": market, "market_minus_model_brier": market - model,
            })
        diffs = [row["market_minus_model_brier"] for row in scored]
        mean = statistics.fmean(diffs) if diffs else None
        lower = mean - 1.96 * statistics.stdev(diffs) / math.sqrt(len(diffs)) if len(diffs) >= 2 else None
        return scored, mean, lower
    scored, mean, lower = score_rows(resolved)
    executable = [row for row in resolved if row.get("execution_gate_passed") is True]
    executable_scored, executable_mean, executable_lower = score_rows(executable)
    promising = len(executable_scored) >= 30 and executable_lower is not None and executable_lower > 0
    return {
        "schema_version": "stock-weekly-shadow-audit-v1",
        "resolved_forecasts": len(resolved),
        "resolved_event_groups": len(scored), "group_rows": scored,
        "mean_market_minus_model_brier": mean, "paired_brier_95pct_lower": lower,
        "execution_eligible_forecasts": len(executable),
        "execution_eligible_event_groups": len(executable_scored),
        "execution_eligible_group_rows": executable_scored,
        "execution_eligible_mean_market_minus_model_brier": executable_mean,
        "execution_eligible_paired_brier_95pct_lower": executable_lower,
        "promotion_requires_execution_eligible_evidence": True,
        "research_evidence_status": "promising_requires_new_model_and_manual_review" if promising else "insufficient_or_nonpositive_fresh_oos_evidence",
        "paper_promotion_automatic": False,
    }


def validate_frozen_model() -> dict[str, Any]:
    evidence = core.read_json(ROOT / "experiments/current-stock-weekly-walk-forward.json")
    required = {
        "selected_model": MODEL_KEY,
        "final_holdout_inspected_once": True,
        "final_holdout_reuse_for_model_selection_allowed": False,
        "next_generation_requires_post_2026_07_11_forward_weeks": True,
        "paper_estimates_allowed": False,
        "research_promotion_candidate": False,
    }
    if any(evidence.get(key) != value for key, value in required.items()):
        raise ValueError("stock-weekly frozen-failure contract mismatch")
    return evidence


def collect(ledger_path: Path, output: Path, ohlc_dir: Path, observed_at: datetime | None = None) -> dict[str, Any]:
    protected = [
        ROOT / "data/paper_ledger.json",
        ROOT / "experiments/current-crypto-barrier-estimates.json",
        ROOT / "experiments/current-stock-weekly-walk-forward.json",
    ]
    before = {str(path.relative_to(ROOT)): file_sha(path) for path in protected}
    validate_frozen_model()
    ledger = load_ledger(ledger_path)
    settlement = settle(ledger)
    events, event_requests = active_events()
    now = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    freeze = core.parse_iso(MODEL_FROZEN_AT)
    assert freeze is not None
    existing = {(row["event_group"], row["model_version"]) for row in ledger["open_forecasts"] + ledger["resolved_forecasts"]}
    eligible_events, excluded = [], []
    for event in events:
        markets = event.get("markets") or []
        parsed = [lab.parse_contract(str(row.get("question") or "")) for row in markets]
        parsed_valid = [row for row in parsed if row]
        if not markets or len(parsed_valid) != len(markets):
            excluded.append({"event_id": str(event.get("id")), "reason": "contract_ladder_unparsed"})
            continue
        weeks = {row["week_start"] for row in parsed_valid}
        symbols = {row["symbol"] for row in parsed_valid}
        if len(weeks) != 1 or symbols != {event["shadow_symbol"]}:
            excluded.append({"event_id": str(event.get("id")), "reason": "event_group_inconsistent"})
            continue
        week = next(iter(weeks)); group = f"{event['shadow_symbol']}:{week}"
        cutoff = lab.cutoff_for_week(week)
        timing = {
            "cutoff_at": cutoff.isoformat(),
            "capture_window_start_at": (cutoff - timedelta(hours=CAPTURE_WINDOW_HOURS)).isoformat(),
            "capture_window_end_at": cutoff.isoformat(),
        }
        if week < FIRST_FORWARD_WEEK or cutoff <= freeze:
            excluded.append({"event_id": str(event.get("id")), "reason": "cutoff_before_forward_boundary", **timing})
            continue
        if (group, MODEL_VERSION) in existing:
            excluded.append({"event_id": str(event.get("id")), "reason": "forecast_already_recorded", **timing})
            continue
        state = capture_state(now, cutoff)
        if state != "eligible":
            excluded.append({"event_id": str(event.get("id")), "reason": state, **timing})
            continue
        if len(markets) < 8 or {row["direction"] for row in parsed_valid} != {"high", "low"}:
            excluded.append({"event_id": str(event.get("id")), "reason": "event_group_market_coverage_incomplete"})
            continue
        eligible_events.append((event, parsed_valid, week, group, cutoff))

    opened, source_requests = [], []
    if eligible_events:
        manifest = lab.fetch_ohlc(ohlc_dir)
        if manifest.get("data_status") != "ok":
            raise ValueError("stock-weekly shadow OHLC refresh degraded")
        ohlc = core.read_json(ohlc_dir / "ohlc.json")
        source_requests = core.read_json(ohlc_dir / "request-log.json")
        all_eligible_markets = [market for event, *_ in eligible_events for market in event.get("markets") or []]
        books, book_requests = fetch_yes_books(all_eligible_markets)
        source_requests.extend(book_requests)
        for event, parsed, week, group, cutoff in eligible_events:
            predictions = []
            incomplete_contracts = 0
            for market, contract in zip(event.get("markets") or [], parsed):
                prediction = lab.empirical_probability(ohlc.get(contract["symbol"], []), week, contract["direction"], contract["threshold"], 252, False)
                token = yes_token(market)
                market_snapshot = executable_market_snapshot(
                    books.get(str(token)) or {}, gamma_yes_probability(market), now,
                )
                if prediction is None or market_snapshot is None:
                    incomplete_contracts += 1
                    continue
                predictions.append((market, contract, prediction, token, market_snapshot))
            if len(predictions) < 8 or {row[1]["direction"] for row in predictions} != {"high", "low"}:
                excluded.append({"event_id": str(event.get("id")), "reason": "model_or_fresh_two_sided_clob_snapshot_incomplete"})
                continue
            captured_at = now.isoformat()
            for market, contract, prediction, token, market_snapshot in predictions:
                forecast_id = core.stable_id("pm-stock-weekly-shadow", str(event.get("id")), str(market.get("id")), MODEL_VERSION)
                ledger["open_forecasts"].append({
                    "forecast_id": forecast_id, "created_at": core.now_iso(), "captured_at": captured_at,
                    "status": "open", "research_only": True, "domain": DOMAIN,
                    "event_id": str(event.get("id")), "event_group": group,
                    "market_id": str(market.get("id")), "condition_id": str(market.get("conditionId") or ""),
                    "question": market.get("question"), **contract, "cutoff_at": cutoff.isoformat(),
                    "model_version": MODEL_VERSION, "frozen_variant": MODEL_KEY,
                    "model_score": prediction["probability"], "model_confidence_low": prediction["confidence_low"],
                    "model_confidence_high": prediction["confidence_high"], "model_samples": prediction["samples"],
                    "spot_at_forecast": prediction["spot_at_cutoff"],
                    "yes_token_id": token,
                    "market_probability_at_forecast": market_snapshot["market_probability"],
                    "market_snapshot_source": "official_clob_two_sided_midpoint",
                    "market_snapshot_documentation": "https://docs.polymarket.com/trading/orderbook",
                    "gamma_yes_probability_at_forecast": market_snapshot["gamma_yes_probability"],
                    "gamma_midpoint_divergence": market_snapshot["gamma_midpoint_divergence"],
                    "best_bid_at_forecast": market_snapshot["best_bid"],
                    "best_ask_at_forecast": market_snapshot["best_ask"],
                    "spread_at_forecast": market_snapshot["spread"],
                    "book_timestamp_ms": market_snapshot["book_timestamp_ms"],
                    "book_age_seconds": market_snapshot["book_age_seconds"],
                    "book_activity_age_exceeds_15m": market_snapshot["book_activity_age_exceeds_15m"],
                    "book_hash": market_snapshot["book_hash"],
                    "bid_levels": market_snapshot["bid_levels"], "ask_levels": market_snapshot["ask_levels"],
                    "planned_execution_notional_usd": market_snapshot["planned_execution_notional_usd"],
                    "execution_quote": market_snapshot["execution_quote"],
                    "execution_gate_passed": market_snapshot["execution_gate_passed"],
                    "execution_gate_limits": market_snapshot["execution_gate_limits"],
                    "model_source": "split_adjusted_yahoo_daily_ohlc_before_target_week",
                    "model_frozen_at": MODEL_FROZEN_AT, "final_holdout_reused": False,
                    "not_a_true_probability": True, "not_eligible_for_paper_entry": True,
                    "single_external_ohlc_vendor_only": True, "paper_only": True,
                    "live_orders_enabled": False, "private_api_used": False,
                })
                opened.append(forecast_id)
            if incomplete_contracts:
                excluded.append({"event_id": str(event.get("id")), "reason": "partial_contract_snapshot_excluded", "contract_count": incomplete_contracts})
            existing.add((group, MODEL_VERSION))

    ledger["latest_audit"] = audit(ledger)
    ledger["updated_at"] = core.now_iso()
    ledger["events"].append({"at": core.now_iso(), "type": "stock_weekly_shadow_cycle", "opened": opened, "settled": settlement["settled"], "excluded": excluded})
    precommit = {str(path.relative_to(ROOT)): file_sha(path) for path in protected}
    if before != precommit:
        raise RuntimeError("stock-weekly shadow observed protected artifact mutation")
    core.write_json(ledger_path, ledger)
    payload = {
        "schema_version": "stock-weekly-shadow-cycle-v2", "created_at": core.now_iso(),
        "model_version": MODEL_VERSION, "model_frozen_at": MODEL_FROZEN_AT,
        "capture_window_hours": CAPTURE_WINDOW_HOURS, "opened": opened,
        "strict_pre_event_capture": True, "retroactive_backfill_allowed": False,
        "settled": settlement["settled"], "excluded": excluded,
        "open_forecasts": len(ledger["open_forecasts"]),
        "resolved_forecasts": len(ledger["resolved_forecasts"]), "audit": ledger["latest_audit"],
        "request_count": len(event_requests) + len(settlement["requests"]) + len(source_requests),
        "market_benchmark": "official_clob_two_sided_midpoint",
        "execution_notional_usd": EXECUTION_NOTIONAL_USD,
        "protected_artifacts_unchanged_through_precommit": True, "final_holdout_reused": False,
        "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(output, payload)
    return payload


def self_test() -> dict[str, Any]:
    cutoff = datetime(2026, 7, 13, 13, 29, tzinfo=timezone.utc)
    assert capture_state(cutoff - timedelta(hours=25), cutoff) == "capture_window_not_reached"
    assert capture_state(cutoff - timedelta(hours=12), cutoff) == "eligible"
    assert capture_state(cutoff, cutoff) == "capture_window_missed"
    market = {"outcomes": '["Yes", "No"]', "outcomePrices": '["0.42", "0.58"]'}
    assert gamma_yes_probability(market) == 0.42
    observed = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    book = {
        "timestamp": str(int(observed.timestamp() * 1000)), "hash": "book-hash",
        "bids": [{"price": "0.40", "size": "1000"}],
        "asks": [{"price": "0.42", "size": "1000"}],
    }
    snapshot = executable_market_snapshot(book, 0.415, observed)
    assert snapshot and abs(snapshot["market_probability"] - 0.41) < 1e-12
    assert snapshot["execution_gate_passed"] is True
    old_book = {**book, "timestamp": str(int((observed - timedelta(hours=2)).timestamp() * 1000))}
    old_snapshot = executable_market_snapshot(old_book, 0.415, observed)
    assert old_snapshot and old_snapshot["book_activity_age_exceeds_15m"] is True
    ledger = new_ledger()
    assert ledger["paper_estimates_emitted"] is False and ledger["main_paper_ledger_mutated"] is False
    assert DOMAIN == "stock_weekly"
    return {"status": "pass", "tests": ["strict_capture_window", "clob_midpoint_and_execution", "separate_research_ledger", "domain_provenance"], "live_orders_enabled": False, "private_api_used": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--output", default=str(ROOT / "experiments/current-stock-weekly-shadow-cycle.json"))
    parser.add_argument("--ohlc-dir", default=str(ROOT / "cache/current_stock_weekly_shadow_ohlc"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    payload = self_test() if args.self_test else collect(Path(args.ledger), Path(args.output), Path(args.ohlc_dir))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
