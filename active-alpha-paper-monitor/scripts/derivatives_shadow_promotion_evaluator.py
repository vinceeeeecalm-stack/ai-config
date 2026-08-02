#!/usr/bin/env python3
"""Pre-registered go/no-go evidence gate for derivatives shadow outcomes.

This evaluator is read-only and cannot promote a production rule.  Its strongest
result only permits a later, separately governed promotion review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "derivatives_shadow_promotion_gate_v1.json"
SAFETY_FLAGS = {
    "production_rule_changed": False,
    "formal_action_eligible": False,
    "paper_roi_eligible": False,
    "real_money_roi_eligible": False,
    "business_ready_eligible": False,
    "live_orders_enabled": False,
    "private_api_used": False,
    "human_confirmation_required": True,
}
EXPECTED_PRIMARY = "target_5_stop_3"
EXPECTED_DIAGNOSTICS = ["target_8_stop_4", "target_10_stop_5"]
EXPECTED_EXPERIMENT = ["CONFIRMED_LONG"]
EXPECTED_CONTROL = ["OI_ONLY_CONFLICT", "CROWDED_CONFLICT", "NO_FUEL"]
FROZEN_CONFIG_VALUES = {
    "gate_version": "derivatives-shadow-promotion-gate-v1",
    "reviewer_version": "derivatives-shadow-outcome-review-v1",
    "review_config_digest": "c42e46023e248a9053637f3b4543b29cc2ad3096c3f5952df6ebd0a4eb5a395e",
    "primary_threshold_id": EXPECTED_PRIMARY,
    "diagnostic_threshold_ids": EXPECTED_DIAGNOSTICS,
    "experiment_directional_states": EXPECTED_EXPERIMENT,
    "control_directional_states": EXPECTED_CONTROL,
    "minimum_complete_reviews": 40,
    "minimum_unique_snapshots": 2,
    "minimum_experiment_samples": 10,
    "minimum_control_samples": 20,
    "minimum_rank_4_20_experiment_samples": 5,
    "minimum_rank_4_20_control_samples": 10,
    "minimum_experiment_target_first_rate_pct": 55.0,
    "minimum_overall_uplift_percentage_points": 10.0,
    "minimum_rank_4_20_uplift_percentage_points": 10.0,
    "maximum_experiment_median_mae_magnitude_pct": 5.0,
    "confidence_interval": "wilson_95",
    "earliest_production_promotion_at": "2026-08-16T04:30:20Z",
    "eligible_result": "PROMOTION_REVIEW_ELIGIBLE",
    "reject_result": "REJECT_PHASE1",
    "insufficient_result": "MORE_EVIDENCE_REQUIRED",
}


class PromotionGateError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PromotionGateError(f"json_object_required:{path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PromotionGateError(f"invalid_jsonl:{path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise PromotionGateError(f"jsonl_object_required:{path}:{line_number}")
        records.append(value)
    return records


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PromotionGateError(f"missing_time:{field}")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise PromotionGateError(f"invalid_time:{field}") from exc
    if parsed.tzinfo is None:
        raise PromotionGateError(f"timezone_required:{field}")
    return parsed.astimezone(timezone.utc)


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("schema_version") != "DerivativesShadowPromotionGateConfigV1":
        raise PromotionGateError("invalid_config_schema")
    for field, expected in FROZEN_CONFIG_VALUES.items():
        if config.get(field) != expected:
            raise PromotionGateError(f"frozen_config_changed:{field}")
    integer_fields = [
        "minimum_complete_reviews",
        "minimum_unique_snapshots",
        "minimum_experiment_samples",
        "minimum_control_samples",
        "minimum_rank_4_20_experiment_samples",
        "minimum_rank_4_20_control_samples",
    ]
    for field in integer_fields:
        value = config.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise PromotionGateError(f"invalid_positive_integer:{field}")
    for field, expected in SAFETY_FLAGS.items():
        if config.get(field) is not expected:
            raise PromotionGateError(f"unsafe_config:{field}")
    return config


def wilson_interval(successes: int, total: int) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, centre - spread) * 100.0, min(1.0, centre + spread) * 100.0


def _validate_review(record: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "DerivativesShadowOutcomeReviewV1":
        raise PromotionGateError("invalid_review_schema")
    if record.get("window_status") != "COMPLETE":
        raise PromotionGateError("incomplete_review_not_allowed")
    for field in ("review_id", "observation_id", "symbol", "snapshot_id", "strategy_version", "config_digest", "source_digest"):
        if not isinstance(record.get(field), str) or not record[field]:
            raise PromotionGateError(f"missing_review_field:{field}")
    if record.get("reviewer_version") != config["reviewer_version"]:
        raise PromotionGateError("reviewer_version_mismatch")
    if record.get("review_config_digest") != config["review_config_digest"]:
        raise PromotionGateError("review_config_digest_mismatch")
    if record.get("rank_band") not in {"TOP3", "RANK_4_20"}:
        raise PromotionGateError("invalid_rank_band")
    if record.get("directional_state_at_capture") not in set(EXPECTED_EXPERIMENT + EXPECTED_CONTROL):
        raise PromotionGateError("unregistered_directional_state")
    outcomes = record.get("path_outcomes")
    expected_ids = [EXPECTED_PRIMARY, *EXPECTED_DIAGNOSTICS]
    if not isinstance(outcomes, list) or [row.get("threshold_id") for row in outcomes] != expected_ids:
        raise PromotionGateError("path_outcomes_mismatch")
    for outcome in outcomes:
        if outcome.get("first_trigger") not in {"TARGET", "STOP", "NONE", "AMBIGUOUS_STOP_FIRST"}:
            raise PromotionGateError("invalid_first_trigger")
    for field in ("mfe_pct", "mae_pct", "end_return_pct"):
        value = record.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise PromotionGateError(f"invalid_metric:{field}")
    observed = parse_time(record.get("observed_at"), "observed_at")
    due = parse_time(record.get("review_due_at"), "review_due_at")
    reviewed = parse_time(record.get("reviewed_at"), "reviewed_at")
    generated = parse_time(record.get("generated_at"), "generated_at")
    window_start = parse_time(record.get("window_start_at"), "window_start_at")
    window_end = parse_time(record.get("window_end_at"), "window_end_at")
    evaluated_now = datetime.now(timezone.utc)
    for field, timestamp in (
        ("observed_at", observed),
        ("review_due_at", due),
        ("reviewed_at", reviewed),
        ("generated_at", generated),
        ("window_start_at", window_start),
        ("window_end_at", window_end),
    ):
        if timestamp > evaluated_now:
            raise PromotionGateError(f"absolute_future_time:{field}")
    if due - observed != timedelta(days=7):
        raise PromotionGateError("review_window_must_be_exactly_seven_days")
    if reviewed != due:
        raise PromotionGateError("reviewed_at_must_equal_review_due_at")
    if generated < due:
        raise PromotionGateError("review_generated_before_due")
    if window_start < observed or window_start >= observed + timedelta(minutes=15):
        raise PromotionGateError("window_start_outside_first_closed_bar")
    if window_end > due or due - window_end >= timedelta(minutes=15):
        raise PromotionGateError("window_end_outside_last_closed_bar")
    closed_bar_count = record.get("closed_bar_count")
    if isinstance(closed_bar_count, bool) or not isinstance(closed_bar_count, int) or closed_bar_count < 671:
        raise PromotionGateError("closed_bar_count_incomplete")
    for field, expected in SAFETY_FLAGS.items():
        if record.get(field) is not expected:
            raise PromotionGateError(f"unsafe_review:{field}")
    return record


def _metrics(records: list[dict[str, Any]], threshold_id: str) -> dict[str, Any]:
    triggers = []
    for record in records:
        outcome = next(row for row in record["path_outcomes"] if row["threshold_id"] == threshold_id)
        triggers.append(outcome["first_trigger"])
    targets = sum(value == "TARGET" for value in triggers)
    adverse = sum(value in {"STOP", "AMBIGUOUS_STOP_FIRST"} for value in triggers)
    lower, upper = wilson_interval(targets, len(records))
    return {
        "sample_count": len(records),
        "target_first_count": targets,
        "adverse_first_count": adverse,
        "unresolved_count": sum(value == "NONE" for value in triggers),
        "target_first_rate_pct": round(targets / len(records) * 100.0, 6) if records else None,
        "wilson_95_lower_pct": round(lower, 6) if lower is not None else None,
        "wilson_95_upper_pct": round(upper, 6) if upper is not None else None,
        "median_mfe_pct": round(float(statistics.median(float(row["mfe_pct"]) for row in records)), 6) if records else None,
        "median_mae_pct": round(float(statistics.median(float(row["mae_pct"]) for row in records)), 6) if records else None,
        "median_end_return_pct": round(float(statistics.median(float(row["end_return_pct"]) for row in records)), 6) if records else None,
    }


def evaluate(records: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    config = validate_config(config)
    seen = set()
    seen_observations = set()
    complete = []
    for record in records:
        _validate_review(record, config)
        if record["review_id"] in seen:
            raise PromotionGateError(f"duplicate_review_id:{record['review_id']}")
        if record["observation_id"] in seen_observations:
            raise PromotionGateError(f"duplicate_observation_id:{record['observation_id']}")
        seen.add(record["review_id"])
        seen_observations.add(record["observation_id"])
        complete.append(record)
    lineage_fields = ("strategy_version", "config_digest", "source_digest", "reviewer_version", "review_config_digest")
    lineage_values = {
        field: sorted({record[field] for record in complete})
        for field in lineage_fields
    }
    lineage_blockers = [
        f"cross_review_lineage_mismatch:{field}"
        for field, values in lineage_values.items()
        if len(values) > 1
    ]
    # Multiple source or contract lineages are valid historical records but not
    # one comparable experiment.  Keep the records, refuse to pool their
    # outcomes, and return an explicit evidence blocker instead of crashing the
    # bounded settlement automation.
    analysis_records = complete if not lineage_blockers else []

    experiment_states = set(config["experiment_directional_states"])
    control_states = set(config["control_directional_states"])
    experiment = [row for row in analysis_records if row["directional_state_at_capture"] in experiment_states]
    control = [row for row in analysis_records if row["directional_state_at_capture"] in control_states]
    experiment_early = [row for row in experiment if row["rank_band"] == "RANK_4_20"]
    control_early = [row for row in control if row["rank_band"] == "RANK_4_20"]
    snapshots = {row["snapshot_id"] for row in complete}

    sample_checks = {
        "complete_reviews": len(complete) >= config["minimum_complete_reviews"],
        "unique_snapshots": len(snapshots) >= config["minimum_unique_snapshots"],
        "experiment_samples": len(experiment) >= config["minimum_experiment_samples"],
        "control_samples": len(control) >= config["minimum_control_samples"],
        "rank_4_20_experiment_samples": len(experiment_early) >= config["minimum_rank_4_20_experiment_samples"],
        "rank_4_20_control_samples": len(control_early) >= config["minimum_rank_4_20_control_samples"],
    }
    blockers = [*lineage_blockers, *[name for name, passed in sample_checks.items() if not passed]]

    primary = {
        "all_experiment": _metrics(experiment, config["primary_threshold_id"]),
        "all_control": _metrics(control, config["primary_threshold_id"]),
        "rank_4_20_experiment": _metrics(experiment_early, config["primary_threshold_id"]),
        "rank_4_20_control": _metrics(control_early, config["primary_threshold_id"]),
    }
    diagnostics = {
        threshold_id: {
            "all_experiment": _metrics(experiment, threshold_id),
            "all_control": _metrics(control, threshold_id),
            "rank_4_20_experiment": _metrics(experiment_early, threshold_id),
            "rank_4_20_control": _metrics(control_early, threshold_id),
        }
        for threshold_id in config["diagnostic_threshold_ids"]
    }

    result = config["insufficient_result"]
    decision_checks: dict[str, bool | None] = {
        "experiment_target_first_floor": None,
        "overall_uplift": None,
        "rank_4_20_uplift": None,
        "experiment_median_mae": None,
    }
    overall_uplift = early_uplift = None
    if not blockers:
        experiment_rate = float(primary["all_experiment"]["target_first_rate_pct"])
        control_rate = float(primary["all_control"]["target_first_rate_pct"])
        early_experiment_rate = float(primary["rank_4_20_experiment"]["target_first_rate_pct"])
        early_control_rate = float(primary["rank_4_20_control"]["target_first_rate_pct"])
        overall_uplift = experiment_rate - control_rate
        early_uplift = early_experiment_rate - early_control_rate
        decision_checks = {
            "experiment_target_first_floor": experiment_rate >= config["minimum_experiment_target_first_rate_pct"],
            "overall_uplift": overall_uplift >= config["minimum_overall_uplift_percentage_points"],
            "rank_4_20_uplift": early_uplift >= config["minimum_rank_4_20_uplift_percentage_points"],
            "experiment_median_mae": abs(float(primary["all_experiment"]["median_mae_pct"])) <= config["maximum_experiment_median_mae_magnitude_pct"],
        }
        result = config["eligible_result"] if all(decision_checks.values()) else config["reject_result"]

    return {
        "schema_version": "DerivativesShadowPromotionEvaluationV1",
        "evaluation_id": "derivatives-promotion-" + sha256_json({"reviews": sorted(seen), "config": sha256_json(config)})[:20],
        "gate_version": config["gate_version"],
        "gate_config_digest": sha256_json(config),
        "primary_threshold_id": config["primary_threshold_id"],
        "diagnostic_threshold_ids": config["diagnostic_threshold_ids"],
        "complete_review_count": len(complete),
        "unique_snapshot_count": len(snapshots),
        "evidence_pool_valid": not lineage_blockers,
        "lineage_values": lineage_values,
        "sample_checks": sample_checks,
        "all_blockers": blockers,
        "primary_metrics": primary,
        "diagnostic_metrics": diagnostics,
        "overall_uplift_percentage_points": round(overall_uplift, 6) if overall_uplift is not None else None,
        "rank_4_20_uplift_percentage_points": round(early_uplift, 6) if early_uplift is not None else None,
        "decision_checks": decision_checks,
        "decision": result,
        "decision_effect": "SEPARATE_GOVERNED_REVIEW_ONLY" if result == config["eligible_result"] else "NO_PRODUCTION_CHANGE",
        "earliest_production_promotion_at": config["earliest_production_promotion_at"],
        **SAFETY_FLAGS,
    }


def self_test() -> dict[str, Any]:
    config = validate_config(load_json(DEFAULT_CONFIG))
    result = evaluate([], config)
    if result["decision"] != "MORE_EVIDENCE_REQUIRED" or not result["all_blockers"]:
        raise PromotionGateError("self_test_insufficient_gate_failed")
    return {
        "schema_version": "DerivativesShadowPromotionGateSelfTestV1",
        "status": "PASS",
        "gate_config_digest": sha256_json(config),
        "empty_ledger_decision": result["decision"],
        **SAFETY_FLAGS,
    }


def write_output(payload: dict[str, Any], path: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path is None:
        print(text, end="")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--outcome-ledger")
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        write_output(self_test(), Path(args.output) if args.output else None)
        return
    if not args.outcome_ledger:
        raise SystemExit("--outcome-ledger is required unless --self-test is used")
    result = evaluate(load_jsonl(Path(args.outcome_ledger)), load_json(Path(args.config)))
    write_output(result, Path(args.output) if args.output else None)


if __name__ == "__main__":
    main()
