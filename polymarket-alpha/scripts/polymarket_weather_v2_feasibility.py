#!/usr/bin/env python3
"""Development-only feasibility gate for a market-calibrated Weather V2."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GAMMAS = [0.5, 0.65, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5]
WEATHER_WEIGHTS = [0.0, 0.05, 0.1, 0.2, 0.35, 0.5]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load("weather_v2_core", ROOT / "scripts/polymarket_alpha.py")
weather = load("weather_v2_lab", ROOT / "scripts/polymarket_weather_lab.py")


def pooled_probabilities(row: dict[str, Any], fit: dict[str, float], gamma: float, weather_weight: float) -> list[float]:
    model = weather.bucket_probabilities(row["buckets"], float(row["mean_forecast_high_f"]), fit["bias_f"], fit["sigma_f"])
    market = row["market_probabilities"]
    logs = [gamma * math.log(max(float(p), 1e-12)) + weather_weight * math.log(max(float(q), 1e-12)) for p, q in zip(market, model)]
    peak = max(logs); values = [math.exp(value - peak) for value in logs]; total = sum(values)
    return [value / total for value in values]


def summarize(values: list[float]) -> dict[str, float | None]:
    mean = statistics.fmean(values) if values else None
    lower = mean - 1.96 * statistics.stdev(values) / math.sqrt(len(values)) if len(values) >= 2 and mean is not None else None
    return {"mean": mean, "paired_95pct_lower": lower}


def score(rows: list[dict[str, Any]], fit: dict[str, float], gamma: float, weather_weight: float) -> dict[str, Any]:
    brier, log_loss = [], []
    for row in rows:
        candidate = pooled_probabilities(row, fit, gamma, weather_weight); market = row["market_probabilities"]; winner = int(row["winner_index"])
        actual = [int(index == winner) for index in range(len(candidate))]
        market_brier = statistics.fmean((float(p) - y) ** 2 for p, y in zip(market, actual))
        candidate_brier = statistics.fmean((float(p) - y) ** 2 for p, y in zip(candidate, actual))
        brier.append(market_brier - candidate_brier)
        log_loss.append(-math.log(max(float(market[winner]), 1e-12)) + math.log(max(float(candidate[winner]), 1e-12)))
    return {"event_groups": len(rows), "market_minus_candidate_brier": summarize(brier), "market_minus_candidate_log_loss": summarize(log_loss)}


def select_development_only(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return max(candidates, key=lambda row: float(row["development"]["market_minus_candidate_brier"]["mean"] or -999))


def evaluate(history_dir: Path, forecast_dir: Path, price_dir: Path, maximum_events: int = 180) -> dict[str, Any]:
    rows, exclusions = weather.build_rows(history_dir, forecast_dir, price_dir, maximum_events)
    rows.sort(key=lambda row: row["date"]); count = len(rows); train_end = int(count * .60); validation_end = int(count * .80)
    development, validation, final = rows[:train_end], rows[train_end:validation_end], rows[validation_end:]
    fit = weather.fit(development, "mean_forecast_high_f")
    if fit is None:
        raise ValueError("weather V2 development fit unavailable")
    candidates = []
    for gamma in GAMMAS:
        for weather_weight in WEATHER_WEIGHTS:
            candidates.append({"gamma": gamma, "weather_weight": weather_weight, "development": score(development, fit, gamma, weather_weight)})
    selected = select_development_only(candidates)
    diagnostics = {segment: score(values, fit, selected["gamma"], selected["weather_weight"]) for segment, values in (("validation", validation), ("final", final))}
    dev = selected["development"]
    development_gate = bool(
        (dev["market_minus_candidate_brier"]["paired_95pct_lower"] or -999) > 0
        and (dev["market_minus_candidate_log_loss"]["paired_95pct_lower"] or -999) > 0
    )
    return {
        "schema_version": "polymarket-weather-v2-feasibility-v1", "created_at": core.now_iso(),
        "candidate_family": "market_temperature_calibration_plus_weather_log_opinion_pool",
        "station": weather.STATION, "rows": count,
        "split": {"development": len(development), "historical_validation_diagnostic": len(validation), "historical_final_diagnostic": len(final)},
        "exclusions": exclusions, "development_fit": fit, "candidate_grid": {"gammas": GAMMAS, "weather_weights": WEATHER_WEIGHTS},
        "selection_scope": "development_only", "selected": selected,
        "historical_diagnostics": diagnostics,
        "historical_diagnostics_were_already_inspected_and_are_not_fresh_oos": True,
        "development_gate_met": development_gate,
        "decision": "freeze_prospective_v2_forward_challenger" if development_gate else "reject_v2_family_before_forward_freeze",
        "v2_protocol_frozen": development_gate, "forward_challenger_allowed": development_gate,
        "paper_estimates_allowed": False, "paper_estimates_emitted": False, "model_outputs_are_true_probabilities": False,
        "main_paper_ledger_mutated": False, "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }


def markdown(payload: dict[str, Any]) -> str:
    selected = payload["selected"]; dev = selected["development"]; lines = [
        "# Weather V2 Feasibility Gate", "",
        f"- Decision: `{payload['decision']}`",
        f"- Development-only selected gamma / weather weight: {selected['gamma']} / {selected['weather_weight']}",
        f"- Development Brier improvement / 95% lower: {dev['market_minus_candidate_brier']['mean']:.6f} / {dev['market_minus_candidate_brier']['paired_95pct_lower']:.6f}",
        f"- Development log-loss improvement / 95% lower: {dev['market_minus_candidate_log_loss']['mean']:.6f} / {dev['market_minus_candidate_log_loss']['paired_95pct_lower']:.6f}",
        "", "| Segment | Groups | Brier improvement | Brier lower | Log improvement | Log lower |", "|---|---:|---:|---:|---:|---:|",
    ]
    for name, row in payload["historical_diagnostics"].items():
        b = row["market_minus_candidate_brier"]; l = row["market_minus_candidate_log_loss"]
        lines.append(f"| {name} (non-fresh diagnostic) | {row['event_groups']} | {b['mean']:.6f} | {b['paired_95pct_lower']:.6f} | {l['mean']:.6f} | {l['paired_95pct_lower']:.6f} |")
    lines.extend(["", "The already-inspected validation/final segments cannot select or promote this family. Because the development confidence bounds already fail, no V2 protocol or forward challenger is created.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-dir", default=str(ROOT / "cache/weather_dallas_history"))
    parser.add_argument("--forecast-dir", default=str(ROOT / "cache/weather_dallas_forecasts"))
    parser.add_argument("--price-dir", default=str(ROOT / "cache/weather_dallas_trade_histories"))
    parser.add_argument("--maximum-events", type=int, default=180)
    parser.add_argument("--output", default=str(ROOT / "experiments/current-weather-v2-feasibility.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_WEATHER_V2_FEASIBILITY.md"))
    args = parser.parse_args(); payload = evaluate(Path(args.history_dir), Path(args.forecast_dir), Path(args.price_dir), args.maximum_events)
    core.write_json(Path(args.output), payload); Path(args.report).write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
