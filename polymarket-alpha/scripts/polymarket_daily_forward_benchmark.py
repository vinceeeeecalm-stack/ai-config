#!/usr/bin/env python3
"""Score independent forward daily forecasts against the contemporaneous market baseline."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "data/daily_candidate_observation_ledger.json"
DEFAULT_OUTPUT = ROOT / "experiments/current-daily-forward-benchmark.json"
DEFAULT_REPORT = ROOT / "reports/CURRENT_DAILY_FORWARD_BENCHMARK.md"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module


core = load("daily_forward_benchmark_core", ROOT / "scripts/polymarket_alpha.py")


def percentile(values: list[float], q: float) -> float | None:
    if not values: return None
    ordered = sorted(values); position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    if low == high: return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def bootstrap_mean_interval(values: list[float], iterations: int = 10000, seed: int = 20260712) -> dict[str, float | None]:
    if len(values) < 2:
        return {"lower_95": None, "upper_95": None}
    rng = random.Random(seed); n = len(values)
    samples = [mean(values[rng.randrange(n)] for _ in range(n)) for _ in range(iterations)]
    return {"lower_95": percentile(samples, .025), "upper_95": percentile(samples, .975)}


def earliest(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return min(rows, key=lambda row: str(row.get("observed_at") or "")) if rows else None


def calibration_bins(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bins: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        p = float(row["model_yes_probability"]); bins[min(9, int(p * 10))].append(row)
    output = []
    for index in sorted(bins):
        group = bins[index]
        output.append({"probability_bin": f"{index / 10:.1f}-{(index + 1) / 10:.1f}",
                       "independent_market_count": len(group),
                       "mean_forecast_probability": mean(float(row["model_yes_probability"]) for row in group),
                       "observed_yes_rate": mean(float(row["outcome_yes"]) for row in group)})
    return output


def summarize_model(rows: list[dict[str, Any]], observation_count: int, iterations: int) -> dict[str, Any]:
    brier_improvements = [float(row["brier_improvement_vs_market"]) for row in rows]
    log_improvements = [float(row["log_loss_improvement_vs_market"]) for row in rows]
    brier_ci = bootstrap_mean_interval(brier_improvements, iterations)
    log_ci = bootstrap_mean_interval(log_improvements, iterations, seed=20260713)
    count = len(rows)
    return {
        "model_version": rows[0]["model_version"], "independent_market_count": count,
        "repeated_observation_count": observation_count,
        "mean_model_brier": mean(float(row["model_brier"]) for row in rows),
        "mean_market_brier": mean(float(row["market_baseline_brier"]) for row in rows),
        "mean_brier_improvement_vs_market": mean(brier_improvements),
        "brier_improvement_lower_95": brier_ci["lower_95"], "brier_improvement_upper_95": brier_ci["upper_95"],
        "mean_model_log_loss": mean(float(row["model_log_loss"]) for row in rows),
        "mean_market_log_loss": mean(float(row["market_baseline_log_loss"]) for row in rows),
        "mean_log_loss_improvement_vs_market": mean(log_improvements),
        "log_loss_improvement_lower_95": log_ci["lower_95"], "log_loss_improvement_upper_95": log_ci["upper_95"],
        "calibration_bins": calibration_bins(rows),
        "minimum_independent_samples_met": count >= 30,
        "positive_brier_lower_bound": bool(brier_ci["lower_95"] is not None and brier_ci["lower_95"] > 0),
        "positive_log_loss_lower_bound": bool(log_ci["lower_95"] is not None and log_ci["lower_95"] > 0),
        "forward_promotion_evidence_passed": bool(count >= 30 and brier_ci["lower_95"] is not None
                                                   and log_ci["lower_95"] is not None
                                                   and brier_ci["lower_95"] > 0 and log_ci["lower_95"] > 0),
    }


def build(ledger: dict[str, Any], iterations: int = 10000) -> dict[str, Any]:
    scans = {row["event_id"]: row for row in ledger.get("events", []) if row.get("event_type") == "scan_observation"}
    resolutions = [row for row in ledger.get("events", []) if row.get("event_type") == "resolution_observation"]
    market_rows, model_rows = [], []
    model_observation_counts: dict[str, int] = defaultdict(int)
    for resolution in resolutions:
        condition = str(resolution.get("condition_id") or "")
        winner = str(resolution.get("winning_outcome") or "").upper()
        outcome_yes = 1.0 if winner == "YES" else 0.0 if winner == "NO" else None
        market_score = earliest(resolution.get("market_baseline_forward_scores") or [])
        if market_score and outcome_yes is not None:
            scan = scans.get(market_score.get("scan_event_id"), {})
            market_rows.append({**market_score, "condition_id": condition, "market_id": resolution.get("market_id"),
                                "market_family": scan.get("market_family"), "outcome_yes": outcome_yes})
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for score in resolution.get("approved_model_forward_scores") or []:
            grouped[str(score.get("model_version") or "")].append(score)
        for version, scores in grouped.items():
            if not version or outcome_yes is None: continue
            model_observation_counts[version] += len(scores)
            score = earliest(scores)
            scan = scans.get(score.get("scan_event_id"), {})
            model_rows.append({**score, "condition_id": condition, "market_id": resolution.get("market_id"),
                               "market_family": scan.get("market_family"), "outcome_yes": outcome_yes})
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in model_rows: by_model[row["model_version"]].append(row)
    summaries = [summarize_model(rows, model_observation_counts[version], iterations) for version, rows in sorted(by_model.items())]
    market_summary = {
        "independent_market_count": len(market_rows),
        "repeated_observation_count": sum(len(row.get("market_baseline_forward_scores") or []) for row in resolutions),
        "mean_brier": mean(float(row["market_baseline_brier"]) for row in market_rows) if market_rows else None,
        "mean_log_loss": mean(float(row["market_baseline_log_loss"]) for row in market_rows) if market_rows else None,
        "consensus_accuracy": mean(float(bool(row["market_consensus_hit"])) for row in market_rows) if market_rows else None,
    }
    return {"schema_version": "polymarket-daily-forward-benchmark-v1", "created_at": core.now_iso(), "status": "ok",
            "canonical_observation_policy": "earliest_1_24h_observation_per_condition_and_model",
            "independence_unit": "condition_id", "hourly_snapshots_do_not_increase_independent_sample_count": True,
            "resolved_condition_count": len({row["condition_id"] for row in market_rows}),
            "market_baseline": market_summary, "approved_model_summaries": summaries,
            "approved_model_independent_forecast_count": len(model_rows),
            "forward_promotion_model_versions": [row["model_version"] for row in summaries if row["forward_promotion_evidence_passed"]],
            "paper_only": True, "counts_as_paper_trade": False, "live_orders_enabled": False, "private_api_used": False}


def fmt(value: Any) -> str:
    return "—" if value is None else f"{float(value):.5f}"


def markdown(payload: dict[str, Any]) -> str:
    market = payload["market_baseline"]
    lines = ["# Daily Forward Probability Benchmark", "", f"- Status: `{payload['status']}`",
             f"- Resolved independent conditions: {payload['resolved_condition_count']}",
             f"- Market observations / independent samples: {market['repeated_observation_count']} / {market['independent_market_count']}",
             f"- Market Brier / Log Loss / consensus accuracy: {fmt(market['mean_brier'])} / {fmt(market['mean_log_loss'])} / {fmt(market['consensus_accuracy'])}",
             "- Independence unit: `condition_id`; repeated hourly snapshots never inflate sample count.", "",
             "| Model | Independent | Observations | Model/market Brier | Brier Δ lower 95% | Model/market Log Loss | Log Δ lower 95% | Promote |",
             "|---|---:|---:|---:|---:|---:|---:|---|"]
    for row in payload["approved_model_summaries"]:
        lines.append(f"| {row['model_version']} | {row['independent_market_count']} | {row['repeated_observation_count']} | {fmt(row['mean_model_brier'])}/{fmt(row['mean_market_brier'])} | {fmt(row['brier_improvement_lower_95'])} | {fmt(row['mean_model_log_loss'])}/{fmt(row['mean_market_log_loss'])} | {fmt(row['log_loss_improvement_lower_95'])} | {row['forward_promotion_evidence_passed']} |")
    if not payload["approved_model_summaries"]:
        lines.extend(["", "No approved independent model forecast has resolved. Market-consensus rows remain a benchmark only, not an Alpha signal."])
    lines.extend(["", "This artifact is forward probability evidence, not a paper trade ledger and not real-money authorization.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT)); parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--bootstrap-iterations", type=int, default=10000); args = parser.parse_args()
    payload = build(core.read_json(Path(args.ledger)), args.bootstrap_iterations)
    core.write_json(Path(args.output), payload); Path(args.report).write_text(markdown(payload), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "resolved_condition_count": payload["resolved_condition_count"],
                      "approved_model_independent_forecast_count": payload["approved_model_independent_forecast_count"],
                      "forward_promotion_model_versions": payload["forward_promotion_model_versions"]}, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
