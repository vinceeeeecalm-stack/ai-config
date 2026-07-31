#!/usr/bin/env python3
"""Leakage-safe historical scoring against Polymarket's own price baseline."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "scripts" / "polymarket_alpha.py"
DATA_PATH = ROOT / "scripts" / "polymarket_public_data.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load_module("polymarket_alpha_core", CORE_PATH)
public_data = load_module("polymarket_public_data_core", DATA_PATH)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def score(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    valid = [row for row in rows if row.get(key) is not None]
    if not valid:
        return {"samples": 0, "brier_score": None, "log_loss": None}
    brier = 0.0
    log_loss = 0.0
    for row in valid:
        p = min(1 - 1e-12, max(1e-12, float(row[key])))
        y = int(row["actual_yes"])
        brier += (p - y) ** 2
        log_loss -= y * math.log(p) + (1 - y) * math.log(1 - p)
    return {"samples": len(valid), "brier_score": brier / len(valid), "log_loss": log_loss / len(valid)}


def market_price_at_or_before(history: dict[str, Any], cutoff: datetime) -> tuple[float | None, int | None]:
    points = history.get("history", []) if isinstance(history, dict) else []
    cutoff_ts = int(cutoff.timestamp())
    eligible = []
    for point in points:
        try:
            timestamp = int(float(point["t"]))
            price = float(point["p"])
        except (KeyError, TypeError, ValueError):
            continue
        if timestamp <= cutoff_ts and 0 <= price <= 1:
            eligible.append((timestamp, price))
    if not eligible:
        return None, None
    timestamp, price = max(eligible, key=lambda row: row[0])
    return price, timestamp


def replay(
    snapshot_dir: Path, predictions: dict[str, Any] | None = None,
    horizon_hours: float = 24.0,
) -> dict[str, Any]:
    verification = public_data.verify_manifest(snapshot_dir)
    if verification["status"] != "pass":
        return {
            "schema_version": "polymarket-historical-replay-v1", "created_at": now_iso(),
            "status": "blocked_snapshot_integrity", "snapshot_verification": verification,
            "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        }
    markets = json.loads((snapshot_dir / "markets.json").read_text(encoding="utf-8"))
    histories = json.loads((snapshot_dir / "price-history.json").read_text(encoding="utf-8"))
    predictions = predictions or {}
    rows = []
    exclusions: dict[str, int] = {}
    for market in markets:
        resolution = public_data.infer_resolution(market)
        if resolution.get("winning_outcome") is None:
            exclusions["resolution_unproven"] = exclusions.get("resolution_unproven", 0) + 1
            continue
        # Use the pre-declared market end date first. Resolution/closedTime can be
        # after the outcome is already public and would introduce look-ahead.
        end_time = parse_time(market.get("endDate")) or parse_time(market.get("closedTime"))
        if end_time is None:
            exclusions["end_time_missing"] = exclusions.get("end_time_missing", 0) + 1
            continue
        cutoff = end_time - timedelta(hours=horizon_hours)
        mapping = public_data.token_map(market)
        yes_token = next((token for outcome, token in mapping.items() if outcome.lower() == "yes"), "")
        market_probability, market_timestamp = market_price_at_or_before(histories.get(yes_token, {}), cutoff)
        if market_probability is None:
            exclusions["pre_cutoff_market_price_missing"] = exclusions.get("pre_cutoff_market_price_missing", 0) + 1
            continue
        market_id = str(market.get("id") or "")
        prediction = predictions.get(market_id) if isinstance(predictions.get(market_id), dict) else None
        model_probability = None
        model_version = None
        model_status = "missing"
        if prediction:
            prediction_as_of = parse_time(prediction.get("as_of"))
            candidate_probability = core.as_float(prediction.get("probability"))
            if prediction_as_of is None:
                model_status = "as_of_missing"
            elif prediction_as_of > cutoff:
                model_status = "lookahead_blocked"
            elif candidate_probability is None or not 0 <= candidate_probability <= 1:
                model_status = "probability_invalid"
            elif not prediction.get("model_version"):
                model_status = "model_version_missing"
            else:
                model_probability = candidate_probability
                model_version = prediction["model_version"]
                model_status = "ok"
        rows.append({
            "market_id": market_id, "question": market.get("question"),
            "domain": core.route_domain(market), "cutoff_at": cutoff.isoformat(),
            "market_price_timestamp": market_timestamp, "market_probability": market_probability,
            "model_probability": model_probability, "model_version": model_version,
            "model_status": model_status,
            "actual_yes": 1 if str(resolution["winning_outcome"]).lower() == "yes" else 0,
        })
    rows.sort(key=lambda row: row["cutoff_at"])
    train_end = int(len(rows) * 0.50)
    validation_end = int(len(rows) * 0.75)
    segments = {
        "train": rows[:train_end],
        "validation": rows[train_end:validation_end],
        "final_holdout": rows[validation_end:],
    }
    segment_scores = {}
    improvement_passes = []
    for name, segment_rows in segments.items():
        market_score = score(segment_rows, "market_probability")
        model_score = score(segment_rows, "model_probability")
        improvement = None
        if market_score["brier_score"] is not None and model_score["brier_score"] is not None:
            improvement = market_score["brier_score"] - model_score["brier_score"]
        segment_scores[name] = {"market": market_score, "model": model_score, "brier_improvement": improvement}
        if name in {"validation", "final_holdout"}:
            improvement_passes.append(improvement is not None and improvement > 0)
    domain_counts: dict[str, int] = {}
    for row in rows:
        domain_counts[row["domain"]] = domain_counts.get(row["domain"], 0) + 1
    model_ok_count = sum(1 for row in rows if row["model_status"] == "ok")
    status = "research_replay_complete" if model_ok_count == len(rows) and all(improvement_passes) else "market_baseline_complete_model_evidence_incomplete"
    return {
        "schema_version": "polymarket-historical-replay-v1", "created_at": now_iso(),
        "status": status, "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        "snapshot_dir": str(snapshot_dir), "snapshot_verification": verification,
        "forecast_horizon_hours": horizon_hours,
        "settled_markets_scanned": len(markets), "replay_eligible_markets": len(rows),
        "model_prediction_count": model_ok_count, "exclusions": exclusions,
        "historical_domain_counts": domain_counts,
        "split_contract": {"train_pct": 50, "validation_pct": 25, "final_holdout_pct": 25, "holdout_used_for_selection": False},
        "segment_scores": segment_scores,
        "market_score_improvement_sustained": bool(improvement_passes) and all(improvement_passes),
        "promotion_blockers": [] if status == "research_replay_complete" else ["calibrated_versioned_model_predictions_missing_or_not_better_in_validation_and_holdout"],
        "rows": rows,
    }


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def self_test() -> dict[str, Any]:
    cutoff = datetime(2026, 1, 2, tzinfo=timezone.utc)
    history = {"history": [{"t": int((cutoff - timedelta(hours=1)).timestamp()), "p": 0.7}, {"t": int((cutoff + timedelta(hours=1)).timestamp()), "p": 0.9}]}
    price, timestamp = market_price_at_or_before(history, cutoff)
    assert price == 0.7 and timestamp < int(cutoff.timestamp())
    scores = score([{"p": 0.8, "actual_yes": 1}, {"p": 0.2, "actual_yes": 0}], "p")
    assert abs(scores["brier_score"] - 0.04) < 1e-9
    return {"status": "pass", "tests": ["no_future_price", "brier_score"], "live_orders_enabled": False, "private_api_used": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Polymarket leakage-safe historical replay")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--snapshot-dir", required=True)
    run.add_argument("--predictions-json")
    run.add_argument("--forecast-horizon-hours", type=float, default=24.0)
    run.add_argument("--output", required=True)
    sub.add_parser("self-test")
    args = parser.parse_args()
    if args.command == "self-test":
        payload = self_test()
    else:
        predictions = json.loads(Path(args.predictions_json).read_text(encoding="utf-8")) if args.predictions_json else None
        payload = replay(Path(args.snapshot_dir), predictions, args.forecast_horizon_hours)
        atomic_json(Path(args.output), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not str(payload.get("status", "")).startswith("blocked") else 2


if __name__ == "__main__":
    raise SystemExit(main())
