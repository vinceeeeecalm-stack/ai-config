#!/usr/bin/env python3
"""Frozen V1 research evaluation for Pyth equity daily-direction events."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load("finance_model_core", ROOT / "scripts/polymarket_alpha.py")
PROTOCOL = ROOT / "experiments/finance-daily-research-protocol-v1.json"


def event_features(markets: list[dict[str, Any]], ohlc: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for market in markets:
        history = sorted(ohlc.get(market["symbol"]) or [], key=lambda row: row["date"])
        prior = [row for row in history if row["date"] < market["settlement_date"]]
        if len(prior) < 21 or any(float(row["close"]) <= 0 for row in prior[-21:]):
            continue
        closes = [float(row["close"]) for row in prior]
        returns = [math.log(closes[index] / closes[index - 1]) for index in range(len(closes) - 20, len(closes))]
        rows.append({
            "event_id": market["event_id"], "symbol": market["symbol"],
            "settlement_date": market["settlement_date"], "outcome": int(market["winning_outcome"] == "Up"),
            "lag_return_1": math.log(closes[-1] / closes[-2]),
            "lag_return_5": math.log(closes[-1] / closes[-6]),
            "lag_return_20": math.log(closes[-1] / closes[-21]),
            "realized_vol_20": statistics.stdev(returns),
            "last_feature_date": prior[-1]["date"], "post_event_feature_used": False,
        })
    return rows


def split_dates(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    dates = sorted({row["settlement_date"] for row in rows})
    development_end = int(len(dates) * 0.60)
    validation_end = int(len(dates) * 0.80)
    return {
        "development": set(dates[:development_end]),
        "validation": set(dates[development_end:validation_end]),
        "historical_diagnostic_holdout": set(dates[validation_end:]),
    }


def design(rows: list[dict[str, Any]], development: list[dict[str, Any]]):
    keys = ["lag_return_1", "lag_return_5", "lag_return_20", "realized_vol_20"]
    means = {key: statistics.mean(float(row[key]) for row in development) for key in keys}
    scales = {key: statistics.pstdev(float(row[key]) for row in development) or 1.0 for key in keys}
    symbols = sorted({row["symbol"] for row in development})

    def matrix(values):
        result = []
        for row in values:
            numeric = [(float(row[key]) - means[key]) / scales[key] for key in keys]
            indicators = [float(row["symbol"] == symbol) for symbol in symbols[1:]]
            result.append([1.0, *numeric, *indicators])
        return np.asarray(result, dtype=float)

    return matrix, {"numeric_features": keys, "means": means, "scales": scales, "reference_symbol": symbols[0], "indicator_symbols": symbols[1:]}


def fit_logistic(x: np.ndarray, y: np.ndarray, c_value: float) -> np.ndarray:
    weights = np.zeros(x.shape[1], dtype=float)
    penalty = np.zeros(x.shape[1], dtype=float); penalty[1:] = 1.0 / c_value
    for _ in range(100):
        probability = 1.0 / (1.0 + np.exp(-np.clip(x @ weights, -30, 30)))
        gradient = x.T @ (probability - y) + penalty * weights
        variance = np.maximum(probability * (1.0 - probability), 1e-8)
        hessian = x.T @ (x * variance[:, None]) + np.diag(penalty + 1e-8)
        step = np.linalg.solve(hessian, gradient)
        weights -= step
        if float(np.max(np.abs(step))) < 1e-9:
            break
    return weights


def predict(x: np.ndarray, weights: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x @ weights, -30, 30)))


def lower_95_by_date(rows: list[dict[str, Any]], differences: list[float]) -> float | None:
    grouped: dict[str, list[float]] = {}
    for row, value in zip(rows, differences):
        grouped.setdefault(row["settlement_date"], []).append(value)
    values = [statistics.mean(group) for group in grouped.values()]
    if len(values) < 2:
        return None
    return statistics.mean(values) - 1.96 * statistics.stdev(values) / math.sqrt(len(values))


def score(rows: list[dict[str, Any]], model: list[float], market: list[float]) -> dict[str, Any]:
    y = [float(row["outcome"]) for row in rows]
    eps = 1e-9
    model_brier = [(p - actual) ** 2 for p, actual in zip(model, y)]
    market_brier = [(p - actual) ** 2 for p, actual in zip(market, y)]
    model_log = [-(actual * math.log(max(eps, min(1 - eps, p))) + (1 - actual) * math.log(max(eps, min(1 - eps, 1 - p)))) for p, actual in zip(model, y)]
    market_log = [-(actual * math.log(max(eps, min(1 - eps, p))) + (1 - actual) * math.log(max(eps, min(1 - eps, 1 - p)))) for p, actual in zip(market, y)]
    brier_diff = [base - candidate for base, candidate in zip(market_brier, model_brier)]
    log_diff = [base - candidate for base, candidate in zip(market_log, model_log)]
    return {
        "events": len(rows), "distinct_dates": len({row["settlement_date"] for row in rows}),
        "model_brier": statistics.mean(model_brier), "market_brier": statistics.mean(market_brier),
        "brier_improvement": statistics.mean(brier_diff), "brier_improvement_lower_95_date_clustered": lower_95_by_date(rows, brier_diff),
        "model_log_loss": statistics.mean(model_log), "market_log_loss": statistics.mean(market_log),
        "log_loss_improvement": statistics.mean(log_diff), "log_loss_improvement_lower_95_date_clustered": lower_95_by_date(rows, log_diff),
    }


def evaluate(price_dir: Path, ohlc_dir: Path, output: Path, report: Path) -> dict[str, Any]:
    protocol = core.read_json(PROTOCOL)
    if protocol.get("status") != "protocol_frozen_inputs_not_acquired_model_not_fitted":
        raise ValueError("unexpected finance protocol state")
    price_manifest = core.read_json(price_dir / "manifest.json"); ohlc_manifest = core.read_json(ohlc_dir / "manifest.json")
    if price_manifest.get("data_status") != "ok" or ohlc_manifest.get("data_status") != "ok":
        raise ValueError("finance input manifest is degraded")
    markets = core.read_json(price_dir / "eligible-markets.json"); observations = core.read_json(price_dir / "cutoff-observations.json"); ohlc = core.read_json(ohlc_dir / "ohlc.json")
    rows = event_features(markets, ohlc); splits = split_dates(rows); development = [row for row in rows if row["settlement_date"] in splits["development"]]
    matrix, preprocessing = design(rows, development); by_id = {row["event_id"]: row for row in rows}; row_index = {row["event_id"]: index for index, row in enumerate(rows)}
    x_development = matrix(development); y_development = np.asarray([row["outcome"] for row in development], dtype=float)
    candidates = []
    for c_value in (0.01, 0.1, 1.0, 10.0):
        weights = fit_logistic(x_development, y_development, c_value); probability = predict(x_development, weights)
        candidates.append({"c": c_value, "development_brier": float(np.mean((probability - y_development) ** 2)), "weights": weights.tolist()})
    selected = min(candidates, key=lambda row: (row["development_brier"], row["c"])); weights = np.asarray(selected["weights"]); all_probability = predict(matrix(rows), weights)
    observation_map = {(row["event_id"], row["cutoff_label"]): row for row in observations if row["status"] == "complete"}
    metrics = []
    for segment, dates in splits.items():
        for cutoff in ("T-24h", "T-60m"):
            segment_rows, model_probability, market_probability = [], [], []
            for row in rows:
                observation = observation_map.get((row["event_id"], cutoff))
                if row["settlement_date"] not in dates or not observation:
                    continue
                segment_rows.append(row); model_probability.append(float(all_probability[row_index[row["event_id"]]])); market_probability.append(float(observation["normalized_market_probabilities"]["Up"]))
            metrics.append({"segment": segment, "cutoff": cutoff, **score(segment_rows, model_probability, market_probability)})
    validation = [row for row in metrics if row["segment"] == "validation"]
    historical_signal = any((row["brier_improvement_lower_95_date_clustered"] or -1) > 0 or (row["log_loss_improvement_lower_95_date_clustered"] or -1) > 0 for row in validation)
    payload = {
        "schema_version": "polymarket-finance-daily-model-v1", "created_at": core.now_iso(), "model_version": "pm-finance-daily-l2-logistic-v1",
        "protocol_created_at": protocol["created_at"], "scope": protocol["contract_scope"], "rows": len(rows),
        "split": {name: {"distinct_dates": len(dates), "events": sum(row["settlement_date"] in dates for row in rows)} for name, dates in splits.items()},
        "same_date_symbols_never_cross_segments": True, "preprocessing_fit_on_development_only": True,
        "candidate_results": candidates, "selected_c": selected["c"], "preprocessing": preprocessing, "metrics": metrics,
        "historical_validation_signal": historical_signal, "historical_diagnostic_holdout_is_pristine": False,
        "fresh_final_oos_required": True, "minimum_fresh_oos_dates": 30,
        "promotion_status": "blocked_fresh_forward_oos_and_execution_evidence_missing",
        "model_outputs_are_true_probabilities": False, "paper_estimates_allowed": False, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(output, payload)
    lines = ["# Polymarket Finance Daily Model V1", "", f"- Selected C (development only): `{selected['c']}`", f"- Events / dates: {len(rows)} / {len({row['settlement_date'] for row in rows})}", f"- Promotion: `{payload['promotion_status']}`", "", "| Segment | Cutoff | Events | Dates | Model Brier | Market Brier | Improvement | 95% lower |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in metrics:
        lines.append(f"| {row['segment']} | {row['cutoff']} | {row['events']} | {row['distinct_dates']} | {row['model_brier']:.5f} | {row['market_brier']:.5f} | {row['brier_improvement']:.5f} | {row['brier_improvement_lower_95_date_clustered']:.5f} |")
    lines.extend(["", "The historical diagnostic holdout is not pristine promotion evidence. No paper estimates are emitted; at least 30 post-freeze OOS settlement dates and forward executable books are still required.", ""])
    report.write_text("\n".join(lines), encoding="utf-8")
    return payload


def self_test() -> dict[str, Any]:
    rows = [{"settlement_date": f"2026-01-{day:02d}", "symbol": symbol} for day in range(1, 11) for symbol in ("A", "B")]
    splits = split_dates(rows)
    assert all(sum(date in values for values in splits.values()) == 1 for date in {row["settlement_date"] for row in rows})
    lower = lower_95_by_date([{"settlement_date": "a"}, {"settlement_date": "a"}, {"settlement_date": "b"}], [1.0, -1.0, 1.0])
    assert lower is not None
    return {"status": "pass", "tests": ["date_group_split", "date_clustered_interval"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--price-dir", default=str(ROOT / "cache/current_finance_daily_cutoff_prices")); parser.add_argument("--ohlc-dir", default=str(ROOT / "cache/current_finance_daily_ohlc"))
    parser.add_argument("--output", default=str(ROOT / "experiments/current-finance-daily-model-v1.json")); parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_FINANCE_DAILY_MODEL.md")); args = parser.parse_args()
    payload = self_test() if args.self_test else evaluate(Path(args.price_dir), Path(args.ohlc_dir), Path(args.output), Path(args.report))
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
