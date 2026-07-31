#!/usr/bin/env python3
"""Frozen-model, research-only forward forecasts; never feeds paper entries."""
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
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
MODEL_VERSION = "pm-crypto-barrier-v2-empirical-frozen-20260711"
DEFAULT_LEDGER = ROOT / "data" / "research_forecast_ledger.json"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
    return module


core = load("shadow_core", ROOT / "scripts" / "polymarket_alpha.py")
public = load("shadow_public", ROOT / "scripts" / "polymarket_public_data.py")
lab = load("shadow_barrier_lab", ROOT / "scripts" / "polymarket_crypto_barrier_lab.py")


SOURCE_URLS = {
    "BTCUSDT": {
        "binance": "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
        "coinbase": "https://api.coinbase.com/v2/prices/BTC-USD/spot",
        "kraken": "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
    },
    "ETHUSDT": {
        "binance": "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT",
        "coinbase": "https://api.coinbase.com/v2/prices/ETH-USD/spot",
        "kraken": "https://api.kraken.com/0/public/Ticker?pair=ETHUSD",
    },
    "XRPUSDT": {
        "binance": "https://api.binance.com/api/v3/ticker/price?symbol=XRPUSDT",
        "coinbase": "https://api.coinbase.com/v2/prices/XRP-USD/spot",
        "kraken": "https://api.kraken.com/0/public/Ticker?pair=XRPUSD",
    },
}


def get_json(url: str, timeout: float = 30) -> Any:
    allowed = ("https://api.binance.com/", "https://api.coinbase.com/", "https://api.kraken.com/")
    if not url.startswith(allowed): raise ValueError("shadow source host not allowlisted")
    with urlopen(Request(url, headers={"Accept": "application/json", "User-Agent": "polymarket-paper-research/1.0"}), timeout=timeout) as response:
        return json.loads(response.read().decode())


def fetch_spot_sources(symbol: str) -> dict[str, Any]:
    urls = SOURCE_URLS.get(symbol)
    if not urls: return {"status": "unsupported_symbol", "prices": {}, "sources": []}
    prices = {}; sources = []; captured = core.now_iso()
    for name, url in urls.items():
        try:
            payload = get_json(url)
            if name == "binance": price = float(payload["price"])
            elif name == "coinbase": price = float(payload["data"]["amount"])
            else:
                ticker = next(iter(payload["result"].values())); price = float(ticker["c"][0])
            prices[name] = price
            sources.append({"name": name, "url": url, "captured_at": captured, "status": "ok", "price_usd": price})
        except Exception as exc:
            sources.append({"name": name, "url": url, "captured_at": captured, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
    values = sorted(prices.values()); median = values[len(values)//2] if values else None
    dispersion = (max(values)-min(values))/median if len(values) == 3 and median else None
    return {
        "status": "verified" if len(values) == 3 and dispersion is not None and dispersion <= 0.01 else "degraded",
        "prices": prices, "median_price_usd": median, "relative_dispersion": dispersion, "sources": sources,
    }


def new_ledger() -> dict[str, Any]:
    return {
        "schema_version": "polymarket-research-forecast-ledger-v1", "created_at": core.now_iso(), "updated_at": core.now_iso(),
        "open_forecasts": [], "resolved_forecasts": [], "events": [],
        "main_paper_ledger_mutated": False, "paper_estimates_emitted": False,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }


def load_ledger(path: Path) -> dict[str, Any]:
    ledger = core.read_json(path) if path.exists() else new_ledger()
    if ledger.get("live_orders_enabled") is not False or ledger.get("private_api_used") is not False or ledger.get("paper_estimates_emitted") is not False:
        raise ValueError("unsafe research forecast ledger")
    return ledger


def file_sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def settle_open_forecasts(ledger: dict[str, Any]) -> dict[str, Any]:
    conditions = [str(row.get("condition_id") or "") for row in ledger.get("open_forecasts", [])]
    details, requests = public.fetch_gamma_details_by_condition(conditions) if conditions else ({}, [])
    settled = []
    for forecast in list(ledger.get("open_forecasts", [])):
        detail = details.get(str(forecast.get("condition_id")))
        resolution = public.infer_resolution(detail or {})
        outcome = resolution.get("winning_outcome")
        if outcome not in {"Yes", "No"}: continue
        closed = {**forecast, "status": "resolved", "resolved_at": core.now_iso(), "actual_yes": 1 if outcome == "Yes" else 0, "resolution_evidence": resolution}
        ledger["open_forecasts"].remove(forecast); ledger["resolved_forecasts"].append(closed); settled.append(forecast["forecast_id"])
    return {"settled": settled, "gamma_requests": requests}


def audit_shadow_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    """Score only resolved forecasts, treating each event group as one OOS unit."""
    resolved = [
        row for row in ledger.get("resolved_forecasts", [])
        if row.get("actual_yes") in {0, 1}
        and isinstance(row.get("model_score"), (int, float))
        and isinstance(row.get("market_probability_at_forecast"), (int, float))
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in resolved:
        grouped.setdefault(str(row.get("event_group") or row.get("forecast_id")), []).append(row)
    group_rows = []
    for group, rows in sorted(grouped.items()):
        model_brier = statistics.fmean((float(row["model_score"]) - int(row["actual_yes"])) ** 2 for row in rows)
        market_brier = statistics.fmean((float(row["market_probability_at_forecast"]) - int(row["actual_yes"])) ** 2 for row in rows)
        group_rows.append({
            "event_group": group, "forecast_count": len(rows), "model_brier": model_brier,
            "market_brier": market_brier, "market_minus_model_brier": market_brier - model_brier,
        })
    improvements = [row["market_minus_model_brier"] for row in group_rows]
    mean_improvement = statistics.fmean(improvements) if improvements else None
    if len(improvements) >= 2:
        standard_error = statistics.stdev(improvements) / math.sqrt(len(improvements))
        lower_95 = mean_improvement - 1.96 * standard_error
    else:
        lower_95 = None
    enough_fresh_groups = len(group_rows) >= 10
    return {
        "schema_version": "polymarket-shadow-forecast-audit-v1",
        "resolved_forecasts_scored": len(resolved), "resolved_event_groups": len(group_rows),
        "group_rows": group_rows, "mean_market_minus_model_brier": mean_improvement,
        "paired_improvement_95pct_lower": lower_95, "minimum_fresh_groups": 10,
        "fresh_group_threshold_met": enough_fresh_groups,
        "research_evidence_status": (
            "promising_requires_manual_research_review" if enough_fresh_groups and lower_95 is not None and lower_95 > 0
            else "insufficient_or_nonpositive_fresh_oos_evidence"
        ),
        "paper_promotion_automatic": False,
    }


def collect(snapshot_dir: Path, ledger_path: Path, kline_dir: Path) -> dict[str, Any]:
    protected_paths = [ROOT / "data/paper_ledger.json", ROOT / "experiments/current-crypto-barrier-estimates.json"]
    protected_before = {str(path.relative_to(ROOT)): file_sha256(path) for path in protected_paths}
    verification = public.verify_manifest(snapshot_dir)
    manifest = core.read_json(snapshot_dir / "snapshot-manifest.json")
    if (verification["status"] != "pass" or manifest.get("terminal_cursor_proven") is not True
            or int(manifest.get("markets_fetched", 0)) <= 0 or int(manifest.get("failed_request_count", 0)) != 0):
        raise ValueError("shadow forecast requires complete verified live market discovery")
    walk_forward = core.read_json(ROOT / "experiments/current-crypto-barrier-walk-forward.json")
    estimate_status = core.read_json(ROOT / "experiments/current-crypto-barrier-estimate-status.json")
    if estimate_status.get("paper_estimates_allowed") is not False or walk_forward.get("selected_empirical_weight") != 1.0:
        raise ValueError("frozen V2 research contract mismatch")
    markets = core.read_json(snapshot_dir / "markets.json"); now = datetime.now(timezone.utc)
    parsed = []
    for market in markets:
        row = lab.parse_barrier_market(market)
        end = core.parse_iso(row.get("end_at")) if row else None
        hours = (end-now).total_seconds()/3600 if end else None
        if row and row["symbol"] in SOURCE_URLS and hours is not None and 1 <= hours <= 30*24:
            parsed.append((market, row, hours))
    symbols = sorted({row[1]["symbol"] for row in parsed})
    spot_evidence = {symbol: fetch_spot_sources(symbol) for symbol in symbols}
    start = now - timedelta(days=370); kline_dir.mkdir(parents=True, exist_ok=True); kline_manifest = []
    bars_by_symbol = {}
    for symbol in symbols:
        bars, requests = lab.fetch_binance_klines(symbol, int(start.timestamp()*1000), int(now.timestamp()*1000))
        bars_by_symbol[symbol] = bars; target = kline_dir / f"{symbol}_1h.json"; core.write_json(target, bars); raw = target.read_bytes()
        kline_manifest.append({"symbol": symbol, "rows": len(bars), "sha256": hashlib.sha256(raw).hexdigest(), "failed_requests": sum(item.get("status")=="failed" for item in requests)})
    conditions = [str(row[1]["condition_id"] or "") for row in parsed]
    gamma_details, gamma_requests = public.fetch_gamma_details_by_condition(conditions)
    ledger = load_ledger(ledger_path); settlement = settle_open_forecasts(ledger)
    existing = {(row["condition_id"], row["model_version"]) for row in ledger["open_forecasts"] + ledger["resolved_forecasts"]}
    opened = []; excluded = []
    for market, row, hours in parsed:
        condition = str(row.get("condition_id") or "")
        if (condition, MODEL_VERSION) in existing:
            excluded.append({"condition_id": condition, "reason": "forecast_already_recorded"}); continue
        source = spot_evidence[row["symbol"]]
        if source["status"] != "verified":
            excluded.append({"condition_id": condition, "reason": "three_source_spot_not_verified"}); continue
        detail = gamma_details.get(condition)
        if not detail or not detail.get("description") or "Binance" not in detail.get("description", ""):
            excluded.append({"condition_id": condition, "reason": "gamma_rule_detail_missing_or_conflict"}); continue
        bars = bars_by_symbol[row["symbol"]]; start_at = core.parse_iso(row["start_at"]); prior_event = [bar for bar in bars if start_at and int(bar[0]) >= int(start_at.timestamp()*1000)]
        already_hit = any((row["direction"]=="upper" and float(bar[2])>=row["barrier_price"]) or (row["direction"]=="lower" and float(bar[3])<=row["barrier_price"]) for bar in prior_event)
        if already_hit:
            excluded.append({"condition_id": condition, "reason": "barrier_already_hit_before_forecast"}); continue
        horizon = max(1, int(math.ceil(hours))); upper, lower = lab.empirical_path_extrema(bars, horizon, now)
        score = lab.empirical_barrier_probability(source["median_price_usd"], row["barrier_price"], row["direction"], upper, lower)
        if score is None:
            excluded.append({"condition_id": condition, "reason": "empirical_history_insufficient"}); continue
        n = len(upper); margin = 1.96*math.sqrt(score*(1-score)/max(1,n))
        forecast_id = core.stable_id("pm-shadow", condition, MODEL_VERSION)
        market_probability = float(row["outcome_prices"][0]) if row.get("outcome_prices") else None
        if market_probability is None or not 0 <= market_probability <= 1:
            excluded.append({"condition_id": condition, "reason": "market_probability_missing"}); continue
        forecast = {
            "forecast_id": forecast_id, "created_at": core.now_iso(), "status": "open", "research_only": True,
            "market_id": str(detail.get("id") or row["market_id"]), "condition_id": condition,
            "question": row["question"], "symbol": row["symbol"], "direction": row["direction"], "barrier_price": row["barrier_price"],
            "event_group": f"{row['symbol']}:{row['end_at']}", "end_at": row["end_at"], "horizon_hours": horizon,
            "model_version": MODEL_VERSION, "model_score": score,
            "market_probability_at_forecast": market_probability,
            "market_probability_source": "verified_polymarket_public_snapshot",
            "snapshot_manifest_sha256": hashlib.sha256((snapshot_dir / "snapshot-manifest.json").read_bytes()).hexdigest(),
            "research_interval_low": max(0,score-margin), "research_interval_high": min(1,score+margin),
            "empirical_path_count": n, "spot_evidence": source,
            "rules_review": {"status": "machine_parsed_research_only", "gamma_market_id": detail.get("id"), "description_sha256": hashlib.sha256(detail["description"].encode()).hexdigest()},
            "not_a_true_probability": True, "not_eligible_for_paper_entry": True,
            "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        }
        ledger["open_forecasts"].append(forecast); opened.append(forecast_id)
    ledger["updated_at"] = core.now_iso(); ledger["events"].append({"at": core.now_iso(), "type": "shadow_forecast_cycle", "opened": opened, "settled": settlement["settled"], "excluded_count": len(excluded)})
    ledger["latest_audit"] = audit_shadow_ledger(ledger)
    core.write_json(ledger_path, ledger)
    kline_payload = {"schema_version": "shadow-kline-manifest-v1", "created_at": core.now_iso(), "files": kline_manifest, "paper_only": True, "private_api_used": False}
    core.write_json(kline_dir / "manifest.json", kline_payload)
    protected_after = {str(path.relative_to(ROOT)): file_sha256(path) for path in protected_paths}
    if protected_before != protected_after:
        raise RuntimeError("shadow cycle observed a protected main-paper artifact mutation")
    return {
        "schema_version": "polymarket-shadow-forecast-cycle-v1", "created_at": core.now_iso(),
        "model_version": MODEL_VERSION, "markets_parsed": len(parsed), "opened": opened, "settled": settlement["settled"],
        "forecast_semantics": "current_time_before_resolution_and_before_barrier_hit",
        "retroactive_backfill_allowed": False,
        "source_snapshot_data_status": manifest.get("data_status"),
        "source_snapshot_aux_error_count": manifest.get("aux_error_count"),
        "research_discovery_gate_passed": True,
        "excluded": excluded, "open_forecasts": len(ledger["open_forecasts"]), "resolved_forecasts": len(ledger["resolved_forecasts"]),
        "spot_evidence": spot_evidence, "gamma_request_failures": sum(item.get("status")=="failed" for item in gamma_requests),
        "audit": ledger["latest_audit"],
        "protected_artifact_sha256_before": protected_before,
        "protected_artifact_sha256_after": protected_after,
        "protected_artifacts_unchanged": True,
        "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }


def self_test() -> dict[str, Any]:
    ledger = new_ledger(); assert ledger["paper_estimates_emitted"] is False and ledger["main_paper_ledger_mutated"] is False
    ledger["resolved_forecasts"] = [
        {"forecast_id": "a", "event_group": "g1", "model_score": 0.8, "market_probability_at_forecast": 0.6, "actual_yes": 1},
        {"forecast_id": "b", "event_group": "g1", "model_score": 0.2, "market_probability_at_forecast": 0.4, "actual_yes": 0},
    ]
    audit = audit_shadow_ledger(ledger)
    assert audit["resolved_event_groups"] == 1 and audit["mean_market_minus_model_brier"] > 0
    return {"status": "pass", "tests": ["separate_research_ledger", "no_paper_estimates", "group_aware_resolved_only_scoring"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect frozen-model shadow forecasts")
    parser.add_argument("--snapshot-dir", default=str(ROOT / "cache/current_validation_snapshot")); parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--kline-dir", default=str(ROOT / "cache/current_shadow_klines")); parser.add_argument("--output", default=str(ROOT / "experiments/current-shadow-forecast-cycle.json"))
    parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    payload = self_test() if args.self_test else collect(Path(args.snapshot_dir), Path(args.ledger), Path(args.kline_dir))
    if not args.self_test: core.write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
