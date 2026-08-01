#!/usr/bin/env python3
"""Three-segment probability calibration research for Polymarket.

This lab calibrates the market-implied probability only. It is not an
independent information model and can never satisfy the official + two
independent-source entry gate by itself.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Callable


EPS = 1e-6


def clip(value: float) -> float:
    return min(1 - EPS, max(EPS, value))


def logit(probability: float) -> float:
    p = clip(probability)
    return math.log(p / (1 - p))


def sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1 / (1 + z)
    z = math.exp(value)
    return z / (1 + z)


def solve_linear(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    augmented = [matrix[row][:] + [vector[row]] for row in range(n)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("singular matrix")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(n):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                augmented[row][index] - factor * augmented[column][index]
                for index in range(n + 1)
            ]
    return [augmented[row][-1] for row in range(n)]


def fit_logistic(
    features: list[list[float]], outcomes: list[int],
    prior: list[float], l2: float = 2.0, max_iter: int = 80,
) -> list[float]:
    coefficients = prior[:]
    width = len(prior)
    for _ in range(max_iter):
        gradient = [l2 * (coefficients[index] - prior[index]) for index in range(width)]
        hessian = [[0.0 for _ in range(width)] for _ in range(width)]
        for index in range(width):
            hessian[index][index] = l2
        for row, outcome in zip(features, outcomes):
            prediction = sigmoid(sum(value * coefficient for value, coefficient in zip(row, coefficients)))
            error = prediction - outcome
            weight = max(1e-9, prediction * (1 - prediction))
            for left in range(width):
                gradient[left] += error * row[left]
                for right in range(width):
                    hessian[left][right] += weight * row[left] * row[right]
        try:
            step = solve_linear(hessian, gradient)
        except ValueError:
            break
        coefficients = [coefficient - delta for coefficient, delta in zip(coefficients, step)]
        if max(abs(delta) for delta in step) < 1e-8:
            break
    return coefficients


def metrics(rows: list[dict[str, Any]], predictor: Callable[[dict[str, Any]], float]) -> dict[str, Any]:
    if not rows:
        return {"samples": 0, "brier_score": None, "log_loss": None, "calibration_error": None}
    brier = 0.0
    loss = 0.0
    calibration_bins: dict[int, list[tuple[float, int]]] = {}
    for row in rows:
        probability = clip(float(predictor(row)))
        outcome = int(row["actual_yes"])
        brier += (probability - outcome) ** 2
        loss -= outcome * math.log(probability) + (1 - outcome) * math.log(1 - probability)
        calibration_bins.setdefault(min(9, int(probability * 10)), []).append((probability, outcome))
    calibration_error = sum(
        len(items) / len(rows) * abs(sum(p for p, _ in items) / len(items) - sum(y for _, y in items) / len(items))
        for items in calibration_bins.values()
    )
    return {
        "samples": len(rows), "brier_score": brier / len(rows),
        "log_loss": loss / len(rows), "calibration_error": calibration_error,
    }


def fit_candidates(train: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outcomes = [int(row["actual_yes"]) for row in train]
    global_features = [[1.0, logit(float(row["market_probability"]))] for row in train]
    candidates: list[dict[str, Any]] = [
        {"name": "identity_market_probability", "family": "identity", "params": {}},
    ]
    for regularization in (0.5, 2.0, 10.0):
        coefficients = fit_logistic(global_features, outcomes, [0.0, 1.0], regularization)
        candidates.append({
            "name": f"platt_global_l2_{regularization:g}", "family": "platt_global",
            "params": {"intercept": coefficients[0], "market_logit_slope": coefficients[1], "l2": regularization},
        })
    domains = sorted({str(row["domain"]) for row in train})
    baseline_domain = domains[0] if domains else "other"
    encoded_domains = [domain for domain in domains if domain != baseline_domain]
    domain_features = [
        [1.0, logit(float(row["market_probability"]))] + [1.0 if row["domain"] == domain else 0.0 for domain in encoded_domains]
        for row in train
    ]
    if encoded_domains:
        for regularization in (2.0, 10.0, 30.0):
            coefficients = fit_logistic(domain_features, outcomes, [0.0, 1.0] + [0.0] * len(encoded_domains), regularization)
            candidates.append({
                "name": f"platt_domain_l2_{regularization:g}", "family": "platt_domain",
                "params": {
                    "intercept": coefficients[0], "market_logit_slope": coefficients[1],
                    "baseline_domain": baseline_domain,
                    "domain_intercepts": {domain: coefficients[index + 2] for index, domain in enumerate(encoded_domains)},
                    "l2": regularization,
                },
            })
    for strength in (5.0, 15.0, 40.0):
        bins = {}
        for bin_index in range(10):
            items = [row for row in train if min(9, int(float(row["market_probability"]) * 10)) == bin_index]
            center = (bin_index + 0.5) / 10
            wins = sum(int(row["actual_yes"]) for row in items)
            calibrated = (wins + strength * center) / (len(items) + strength)
            bins[str(bin_index)] = {"samples": len(items), "probability": calibrated}
        candidates.append({
            "name": f"empirical_bins_strength_{strength:g}", "family": "empirical_bins",
            "params": {"strength": strength, "bins": bins},
        })
    return candidates


def predict(candidate: dict[str, Any], row: dict[str, Any]) -> float:
    family = candidate["family"]
    params = candidate["params"]
    market_probability = float(row["market_probability"])
    if family == "identity":
        return market_probability
    if family == "platt_global":
        return sigmoid(params["intercept"] + params["market_logit_slope"] * logit(market_probability))
    if family == "platt_domain":
        domain_effect = params["domain_intercepts"].get(str(row["domain"]), 0.0)
        return sigmoid(params["intercept"] + params["market_logit_slope"] * logit(market_probability) + domain_effect)
    if family == "empirical_bins":
        bin_index = str(min(9, int(market_probability * 10)))
        return float(params["bins"][bin_index]["probability"])
    raise ValueError(f"unknown candidate family {family}")


def improvement(model: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    return {
        "brier": None if model["brier_score"] is None else market["brier_score"] - model["brier_score"],
        "log_loss": None if model["log_loss"] is None else market["log_loss"] - model["log_loss"],
        "calibration_error": None if model["calibration_error"] is None else market["calibration_error"] - model["calibration_error"],
    }


def run_lab(replay_payload: dict[str, Any], min_domain_samples: int = 30) -> dict[str, Any]:
    if replay_payload.get("snapshot_verification", {}).get("status") != "pass":
        return {"status": "blocked_snapshot_integrity", "paper_only": True, "live_orders_enabled": False, "private_api_used": False}
    rows = sorted(replay_payload.get("rows", []), key=lambda row: row["cutoff_at"])
    train_end = int(len(rows) * 0.50)
    validation_end = int(len(rows) * 0.75)
    train, validation, holdout = rows[:train_end], rows[train_end:validation_end], rows[validation_end:]
    candidates = fit_candidates(train)
    candidate_results = []
    for candidate in candidates:
        market_validation = metrics(validation, lambda row: float(row["market_probability"]))
        model_validation = metrics(validation, lambda row, candidate=candidate: predict(candidate, row))
        candidate_results.append({
            **candidate,
            "train_metrics": metrics(train, lambda row, candidate=candidate: predict(candidate, row)),
            "validation_metrics": model_validation,
            "validation_improvement_vs_market": improvement(model_validation, market_validation),
        })
    selected = min(
        candidate_results,
        key=lambda candidate: (candidate["validation_metrics"]["brier_score"], candidate["validation_metrics"]["log_loss"]),
    )
    market_scores = {
        "train": metrics(train, lambda row: float(row["market_probability"])),
        "validation": metrics(validation, lambda row: float(row["market_probability"])),
        "final_holdout": metrics(holdout, lambda row: float(row["market_probability"])),
    }
    selected_scores = {
        "train": metrics(train, lambda row: predict(selected, row)),
        "validation": metrics(validation, lambda row: predict(selected, row)),
        "final_holdout": metrics(holdout, lambda row: predict(selected, row)),
    }
    improvements = {segment: improvement(selected_scores[segment], market_scores[segment]) for segment in market_scores}
    brier_sustained = improvements["validation"]["brier"] > 0 and improvements["final_holdout"]["brier"] > 0
    logloss_sustained = improvements["validation"]["log_loss"] > 0 and improvements["final_holdout"]["log_loss"] > 0
    domain_counts = replay_payload.get("historical_domain_counts", {})
    enabled_domains = sorted(domain for domain, count in domain_counts.items() if count >= min_domain_samples)
    insufficient_domains = {domain: count for domain, count in domain_counts.items() if count < min_domain_samples}
    selected_predictions = {
        row["market_id"]: {
            "probability": predict(selected, row), "market_probability": row["market_probability"],
            "as_of": row["cutoff_at"], "model_version": f"pm-market-calibration-{selected['name']}-v1",
            "domain": row["domain"], "source_scope": "market_price_only_not_independent_event_alpha",
        }
        for row in rows
    }
    blockers = [
        "market_price_only_not_independent_event_information",
        "cannot_satisfy_official_plus_two_independent_sources_gate",
        "cannot_create_tradeable_edge_from_own_market_input_without_external_evidence",
        "top_volume_sample_selection_bias",
        "forward_paper_validation_missing",
    ]
    if insufficient_domains:
        blockers.append("some_domains_below_30_historical_samples")
    if not (brier_sustained or logloss_sustained):
        blockers.append("no_sustained_validation_and_holdout_score_improvement")
    return {
        "schema_version": "polymarket-probability-calibration-lab-v1",
        "status": "research_pass_market_calibration_only" if (brier_sustained or logloss_sustained) else "research_fail_no_sustained_improvement",
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        "model_scope": "market_probability_calibration_only",
        "independent_event_alpha": False,
        "split_contract": {"train_samples": len(train), "validation_samples": len(validation), "final_holdout_samples": len(holdout), "holdout_used_for_selection": False},
        "candidate_selection_metric": "validation_brier_then_validation_log_loss",
        "candidates": candidate_results,
        "selected_model": {key: selected[key] for key in ("name", "family", "params")},
        "market_scores": market_scores,
        "selected_model_scores": selected_scores,
        "improvement_vs_market": improvements,
        "market_score_improvement_sustained": brier_sustained or logloss_sustained,
        "brier_improvement_sustained": brier_sustained,
        "log_loss_improvement_sustained": logloss_sustained,
        "historical_domain_counts": domain_counts,
        "historically_enabled_domains": enabled_domains,
        "insufficient_history_domains": insufficient_domains,
        "recommended_max_action": "research_watch",
        "promotion_blockers": blockers,
        "predictions": selected_predictions,
    }


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def self_test() -> dict[str, Any]:
    rows = [
        {"market_probability": probability, "actual_yes": int(probability >= 0.5), "domain": "sports", "market_id": str(index), "cutoff_at": f"2026-01-{index + 1:02d}T00:00:00+00:00"}
        for index, probability in enumerate([0.1, 0.2, 0.3, 0.7, 0.8, 0.9] * 5)
    ]
    candidates = fit_candidates(rows[:15])
    assert any(candidate["family"] == "platt_global" for candidate in candidates)
    for candidate in candidates:
        assert all(0 < predict(candidate, row) < 1 for row in rows)
    return {"status": "pass", "tests": ["candidate_fit", "bounded_probabilities"], "live_orders_enabled": False, "private_api_used": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Polymarket probability calibration lab")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--historical-replay-json", required=True)
    run.add_argument("--min-domain-samples", type=int, default=30)
    run.add_argument("--output", required=True)
    sub.add_parser("self-test")
    args = parser.parse_args()
    if args.command == "self-test":
        payload = self_test()
    else:
        replay_payload = json.loads(Path(args.historical_replay_json).read_text(encoding="utf-8"))
        payload = run_lab(replay_payload, args.min_domain_samples)
        atomic_json(Path(args.output), payload)
    print(json.dumps(payload if args.command == "self-test" else {
        "status": payload["status"], "selected_model": payload.get("selected_model"),
        "improvement_vs_market": payload.get("improvement_vs_market"),
        "promotion_blockers": payload.get("promotion_blockers"),
        "paper_only": True, "live_orders_enabled": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
