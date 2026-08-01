#!/usr/bin/env python3
"""Manage manual investment recommendation history.

This script stores recommendation records and outcome reviews for the
manual-investment-strategy-operator skill. It does not place trades.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "recommendations" / "recommendation_history.json"

SCHEMA_VERSION = "manual-recommendation-history-v1"
RECOMMENDATION_V2_SCHEMA_VERSION = "recommendation-v2"
OUTCOME_REVIEW_V2_SCHEMA_VERSION = "outcome-review-v2"

V2_REQUEST_MODES = {
    "longterm_dca",
    "tactical_1_7d",
    "event_trade_1_3w",
    "existing_position_review",
    "daily_dual_window",
}

V2_REQUIRED_FIELDS = {
    "recommendation_id",
    "request_mode",
    "evidence_snapshot_id",
    "generated_at",
    "research_decision",
    "current_direct_decision",
    "execution_decision",
    "decision_price",
    "price_as_of",
    "deployable_cash",
    "cash_source",
    "execution_blockers",
    "data_quality_status",
    "human_confirmation_required",
    "live_orders_enabled",
    "private_api_used",
    "decision_valid_until",
    "review_due_at",
    "observation_status",
    "execution_status",
    "outcome_status",
}

OBSERVATION_STATUSES = {
    "pending",
    "triggered",
    "not_triggered",
    "expired",
    "invalidated",
    "not_applicable",
}

EXECUTION_STATUSES = {
    "not_executed",
    "planned",
    "blocked",
    "partially_executed",
    "executed",
    "cancelled",
    "not_applicable",
}

STRUCTURED_DUE_FIELDS = (
    "review_due_at",
    "latest_exit_or_review_at",
    "latest_exit_or_review_date",
    "entry_deadline",
)

OUTCOME_STATUSES = {
    "pending",
    "hit",
    "failed",
    "not_triggered",
    "expired",
    "invalidated",
    "superseded",
}

REQUIRED_FIELDS = {
    "recommendation_id",
    "run_id",
    "strategy_version",
    "config_hash",
    "generated_at",
    "asset_class",
    "symbol",
    "bucket",
    "action",
    "direction",
    "time_window",
    "data_quality_status",
    "validation_status",
    "promotion_status",
    "risk_decision",
    "outcome_status",
}

TACTICAL_OR_REAL_ACTIONS = {
    "buy",
    "add",
    "trim",
    "sell",
    "execute_now",
    "conditional_action",
}

DCA_ACTIONS = {"dca_plan"}

TACTICAL_REQUIRED_FIELDS = {
    "forecast_probability_pct",
    "execution_readiness_score",
    "target_before_stop_probability_pct",
    "stop_before_target_probability_pct",
    "expected_mae_pct_5d_10d_20d",
    "stress_gap_pct",
    "reward_risk_ratio",
    "binary_event_calendar",
    "financing_and_dilution_snapshot",
    "capital_funding_gap_status",
    "contract_quality_snapshot",
    "valuation_expectation_risk",
    "position_size_from_stress_loss",
    "entry_range",
    "entry_deadline",
    "target_range",
    "target_time_window",
    "stop_or_invalid",
    "forecast_invalid_if",
    "latest_exit_or_review_date",
    "position_size_plan",
    "capital_sleeve",
    "cash_rail_source",
    "action_source",
    "price_as_of",
    "price_source",
    "data_age_minutes",
    "data_freshness_status",
    "entry_window_start",
    "entry_window_end",
    "allowed_session",
    "entry_trigger",
    "no_entry_if_not_triggered",
    "expected_holding_days",
    "max_holding_days",
    "target_1_price_or_scenario",
    "target_1_evaluation_window",
    "target_2_price_or_scenario",
    "target_2_evaluation_window",
    "time_stop",
    "event_exit_date",
    "event_handling_plan",
    "post_exit_state",
    "auto_relay_forbidden",
    "current_deployable_cash_usd",
    "settlement_constraint",
    "action_allowed",
    "execution_action",
    "observation_action",
    "observation_trigger",
    "impulse_monitoring_required",
    "impulse_check_deadline",
    "candidate_coverage_matrix",
    "baseline_frozen",
    "historical_baseline_mutation_forbidden",
    "baseline_snapshot_sha256",
    "live_orders_enabled",
    "private_api_used",
    "human_confirmation_required",
}

DCA_REQUIRED_FIELDS = {
    "entry_range",
    "secondary_entry",
    "optimal_entry",
    "entry_deadline",
    "target_range",
    "target_time_window",
    "latest_exit_or_review_date",
    "stop_or_invalid",
    "forecast_invalid_if",
    "position_size_plan",
    "planned_amount_usd_low",
    "planned_amount_usd_high",
    "planned_near_amount_usd",
    "planned_pullback_add_usd",
    "capital_sleeve",
    "cash_rail_source",
    "action_source",
    "long_horizon_timing_decision",
    "long_term_low_value_zone_status",
    "time_in_market_bias_score",
    "wait_requires_specific_pullback_trigger",
    "cash_idle_drag_comment",
    "price_as_of",
    "price_source",
    "data_age_minutes",
    "data_freshness_status",
    "entry_window_start",
    "entry_window_end",
    "allowed_session",
    "entry_trigger",
    "no_entry_if_not_triggered",
    "expected_holding_days",
    "max_holding_days",
    "target_1_price_or_scenario",
    "target_1_evaluation_window",
    "target_2_price_or_scenario",
    "target_2_evaluation_window",
    "time_stop",
    "event_exit_date",
    "event_handling_plan",
    "post_exit_state",
    "auto_relay_forbidden",
    "current_deployable_cash_usd",
    "settlement_constraint",
    "action_allowed",
    "execution_action",
    "observation_action",
    "observation_trigger",
    "impulse_monitoring_required",
    "impulse_check_deadline",
    "candidate_coverage_matrix",
    "baseline_frozen",
    "historical_baseline_mutation_forbidden",
    "baseline_snapshot_sha256",
    "live_orders_enabled",
    "private_api_used",
    "human_confirmation_required",
}

ISO_DATE_FIELDS = {
    "entry_deadline",
    "latest_exit_or_review_date",
    "entry_window_start",
    "entry_window_end",
    "price_as_of",
}

NUMERIC_AMOUNT_FIELDS = {
    "planned_amount_usd_low",
    "planned_amount_usd_high",
    "planned_near_amount_usd",
    "planned_pullback_add_usd",
}

TACTICAL_NUMERIC_FIELDS = {
    "current_quantity",
    "current_market_value_usd",
}

PROBABILITY_BUCKETS = [
    ("80_plus", 80, 100),
    ("60_to_79", 60, 79.999),
    ("below_60", 0, 59.999),
]

RESOLVED_FOR_HIT_RATE = {"hit", "failed"}

SUPERSEDE_EQUIVALENCE_FIELDS = (
    "asset_class",
    "symbol",
    "bucket",
    "capital_sleeve",
    "action_source",
)

SENSITIVE_EXACT_KEYS = {
    "api_key",
    "apikey",
    "api_token",
    "access_token",
    "refresh_token",
    "auth_token",
    "bearer",
    "bearer_token",
    "secret",
    "client_secret",
    "password",
    "passwd",
}

SENSITIVE_SINGLE_SEGMENTS = {"secret", "password", "passwd", "bearer"}
SENSITIVE_TOKEN_PREFIXES = {"api", "access", "refresh", "auth", "bearer"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_time(text: str | None) -> dt.datetime | None:
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        value = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _strip_calendar_dates(text: str) -> str:
    """Remove calendar dates before interpreting legacy duration text.

    V1 stored both free-form durations and ISO dates in ``time_window``.
    Treating every number as a day count made a 2026 date become a 2026-day
    review window. Only explicit duration expressions may affect the fallback.
    """

    without_timestamps = re.sub(
        r"\b\d{4}-\d{1,2}-\d{1,2}(?:[T ][0-9:.+\-Z]+)?\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\b\d{4}/\d{1,2}/\d{1,2}\b", " ", without_timestamps)


def parse_review_days(
    time_window: str | None,
    default_days: int = 30,
    *,
    require_explicit_duration: bool = False,
) -> int | None:
    """Parse a V1 free-form duration without interpreting calendar years.

    This function is only a compatibility fallback. Recommendation V2 never
    derives its due date from ``time_window``.
    """

    if not time_window:
        return None if require_explicit_duration else default_days
    text = _strip_calendar_dates(str(time_window)).lower()
    pattern = re.compile(
        r"(?P<low>\d+)"
        r"(?:\s*(?:-|–|—|to|through|至|到)\s*(?P<high>\d+))?"
        r"\s*(?P<unit>"
        r"trading\s+days?|calendar\s+days?|days?|weeks?|months?|years?|"
        r"个?交易日|个?自然日|天|周|个?月|年"
        r")\b",
        flags=re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return None if require_explicit_duration else default_days

    durations: list[int] = []
    for match in matches:
        value = int(match.group("high") or match.group("low"))
        unit = match.group("unit").lower()
        if "trading" in unit or "交易日" in unit:
            value = int(round(value * 1.4))
        elif "week" in unit or unit == "周":
            value *= 7
        elif "month" in unit or "月" in unit:
            value *= 30
        elif "year" in unit or unit == "年":
            value *= 365
        durations.append(max(value, 1))
    return max(durations)


def is_v2_record(record: dict[str, Any]) -> bool:
    return (
        record.get("schema_version") == RECOMMENDATION_V2_SCHEMA_VERSION
        or record.get("record_version") == RECOMMENDATION_V2_SCHEMA_VERSION
    )


def due_at(record: dict[str, Any]) -> dt.datetime | None:
    for field in STRUCTURED_DUE_FIELDS:
        if field not in record or record.get(field) in (None, ""):
            continue
        # An explicitly supplied structured deadline is authoritative. A
        # malformed value must remain visible as invalid rather than silently
        # falling through to a later or free-form date.
        return parse_time(str(record.get(field)))

    if is_v2_record(record):
        return None

    generated = parse_time(record.get("generated_at"))
    if generated is None:
        return None
    review_days = parse_review_days(record.get("time_window"))
    if review_days is None:
        return None
    return generated + dt.timedelta(days=review_days)


def default_ledger() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": None,
        "recommendations": [],
        "outcome_reviews": [],
        "proposed_changes": [],
    }


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return default_ledger()
    ledger = load_json(path)
    if not isinstance(ledger, dict):
        raise ValueError(f"Ledger must be a JSON object: {path}")
    for key, value in default_ledger().items():
        ledger.setdefault(key, value)
    if ledger["schema_version"] != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema_version {ledger['schema_version']!r}; "
            f"expected {SCHEMA_VERSION!r}"
        )
    if not isinstance(ledger["recommendations"], list):
        raise ValueError("ledger.recommendations must be a list")
    if not isinstance(ledger["outcome_reviews"], list):
        raise ValueError("ledger.outcome_reviews must be a list")
    if not isinstance(ledger["proposed_changes"], list):
        raise ValueError("ledger.proposed_changes must be a list")
    return ledger


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    ledger["updated_at"] = utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)
        f.write("\n")
        tmp = Path(f.name)
    tmp.replace(path)


def find_sensitive_keys(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_path = f"{prefix}.{key}" if prefix else str(key)
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
            # Split camelCase before applying segmented credential checks.
            camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
            segments = [
                item.lower()
                for item in re.split(r"[^A-Za-z0-9]+", camel_split)
                if item
            ]
            adjacent_pairs = {
                (segments[index], segments[index + 1])
                for index in range(max(len(segments) - 1, 0))
            }
            contains_credential_pair = any(
                prefix_part in SENSITIVE_TOKEN_PREFIXES and suffix == "token"
                for prefix_part, suffix in adjacent_pairs
            ) or ("api", "key") in adjacent_pairs
            if (
                normalized in SENSITIVE_EXACT_KEYS
                or any(segment in SENSITIVE_SINGLE_SEGMENTS for segment in segments)
                or contains_credential_pair
            ):
                found.append(key_path)
            found.extend(find_sensitive_keys(child, key_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(find_sensitive_keys(child, f"{prefix}[{index}]"))
    return found


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    if is_v2_record(normalized):
        return normalized
    normalized.setdefault("generated_at", utc_now())
    normalized.setdefault("outcome_status", "pending")
    return normalized


def _validate_with_external_v2_contract(record: dict[str, Any]) -> None:
    """Delegate to the shared contract validator when it is available.

    The local validator deliberately enforces the stable history core. A
    higher-level decision engine may add mode-specific checks without making
    recommendation history depend on that module's presence.
    """

    try:
        import v3_decision_contracts  # type: ignore
    except ImportError:
        return

    validator = getattr(
        v3_decision_contracts, "validate_recommendation_v2_payload", None
    )
    if not callable(validator):
        return
    canonical = dict(record)
    if "schema_version" not in canonical and canonical.get("record_version"):
        canonical["schema_version"] = canonical["record_version"]
    result = validator(canonical)
    if result is None or result is True:
        return
    if result is False:
        raise ValueError("Recommendation V2 failed the shared investment contract")
    if isinstance(result, dict):
        errors = result.get("errors", [])
        status = result.get("status")
        if errors or status in {"fail", "failed", "blocked", "invalid"}:
            raise ValueError(
                "Recommendation V2 failed the shared investment contract: "
                f"{errors or status}"
            )


def validate_v2_record(record: dict[str, Any]) -> None:
    """Validate the append-only Recommendation V2 history contract."""

    if not isinstance(record, dict):
        raise ValueError("Recommendation V2 record must be a JSON object")

    schema_version = record.get("schema_version", record.get("record_version"))
    if schema_version != RECOMMENDATION_V2_SCHEMA_VERSION:
        raise ValueError(
            "Recommendation V2 requires schema_version='recommendation-v2' "
            "(record_version is accepted as a compatibility alias)"
        )

    sensitive = find_sensitive_keys(record)
    if sensitive:
        raise ValueError(f"Sensitive fields are not allowed in recommendation records: {sensitive}")

    missing = sorted(V2_REQUIRED_FIELDS - set(record))
    if missing:
        raise ValueError(f"Missing required Recommendation V2 fields: {missing}")

    request_mode = record.get("request_mode")
    if request_mode not in V2_REQUEST_MODES:
        raise ValueError(
            f"Invalid request_mode {request_mode!r}; expected one of {sorted(V2_REQUEST_MODES)}"
        )

    for field in ("generated_at", "decision_valid_until", "review_due_at"):
        if parse_time(str(record.get(field))) is None:
            raise ValueError(f"{field} must be an ISO date or datetime for Recommendation V2")

    if not str(record.get("evidence_snapshot_id") or "").strip():
        raise ValueError("evidence_snapshot_id must be non-empty for Recommendation V2")
    if not str(record.get("recommendation_id") or "").strip():
        raise ValueError("recommendation_id must be non-empty for Recommendation V2")

    if record.get("observation_status") not in OBSERVATION_STATUSES:
        raise ValueError(
            f"Invalid observation_status {record.get('observation_status')!r}; "
            f"expected one of {sorted(OBSERVATION_STATUSES)}"
        )
    if record.get("execution_status") not in EXECUTION_STATUSES:
        raise ValueError(
            f"Invalid execution_status {record.get('execution_status')!r}; "
            f"expected one of {sorted(EXECUTION_STATUSES)}"
        )
    if record.get("outcome_status") not in OUTCOME_STATUSES:
        raise ValueError(
            f"Invalid outcome_status {record.get('outcome_status')!r}; "
            f"expected one of {sorted(OUTCOME_STATUSES)}"
        )

    if "deployable_cash" in record:
        deployable = record.get("deployable_cash")
        if not isinstance(deployable, (int, float)) or isinstance(deployable, bool) or deployable < 0:
            raise ValueError("deployable_cash must be non-negative numeric")
        if deployable == 0 and str(record.get("execution_decision")).lower() in {
            "buy",
            "enter_now",
            "execute_now",
            "small_entry_now",
            "manual_execute_candidate",
        }:
            raise ValueError("execution_decision cannot deploy when deployable_cash is zero")

    if due_at(record) is None:
        raise ValueError("Recommendation V2 requires a parseable structured review_due_at")

    _validate_with_external_v2_contract(record)


def validate_execution_calendar_fields(record: dict[str, Any]) -> None:
    if record.get("no_entry_if_not_triggered") is not True:
        raise ValueError("no_entry_if_not_triggered must be true")
    if record.get("auto_relay_forbidden") is not True:
        raise ValueError("auto_relay_forbidden must be true")
    if record.get("post_exit_state") != "cash_pending_manual_reallocation":
        raise ValueError("post_exit_state must be cash_pending_manual_reallocation")
    deployable = record.get("current_deployable_cash_usd")
    if not isinstance(deployable, (int, float)) or deployable < 0:
        raise ValueError("current_deployable_cash_usd must be non-negative numeric")
    expected = record.get("expected_holding_days")
    maximum = record.get("max_holding_days")
    if not isinstance(expected, dict) or not {
        "min_calendar_days", "base_calendar_days", "min_trading_days", "base_trading_days"
    }.issubset(expected):
        raise ValueError("expected_holding_days must contain min/base calendar and trading days")
    if not isinstance(maximum, dict) or not {"calendar_days", "trading_days"}.issubset(maximum):
        raise ValueError("max_holding_days must contain calendar_days and trading_days")
    if record.get("action_allowed") == "execute_now" and deployable <= 0:
        raise ValueError("execute_now requires positive settled current_deployable_cash_usd")


def validate_record(record: dict[str, Any]) -> None:
    if not isinstance(record, dict):
        raise ValueError("Recommendation record must be a JSON object")

    if is_v2_record(record):
        validate_v2_record(record)
        return

    sensitive = find_sensitive_keys(record)
    if sensitive:
        raise ValueError(f"Sensitive fields are not allowed in recommendation records: {sensitive}")

    missing = sorted(REQUIRED_FIELDS - set(record))
    if missing:
        raise ValueError(f"Missing required recommendation fields: {missing}")

    if record["outcome_status"] not in OUTCOME_STATUSES:
        raise ValueError(
            f"Invalid outcome_status {record['outcome_status']!r}; "
            f"expected one of {sorted(OUTCOME_STATUSES)}"
        )

    action = str(record.get("action", ""))
    sleeve = str(record.get("capital_sleeve", ""))
    requires_dca_fields = action in DCA_ACTIONS or sleeve in {"crypto_dca_sleeve", "crypto_tail_convexity_sleeve"}
    if requires_dca_fields:
        missing_dca = sorted(DCA_REQUIRED_FIELDS - set(record))
        if missing_dca:
            raise ValueError(f"DCA recommendations require fields: {missing_dca}")

        for field in ISO_DATE_FIELDS:
            if parse_time(str(record.get(field))) is None:
                raise ValueError(f"{field} must be an ISO date or datetime for DCA recommendations")
        for field in NUMERIC_AMOUNT_FIELDS:
            value = record.get(field)
            if not isinstance(value, (int, float)):
                raise ValueError(f"{field} must be numeric for DCA recommendations")
            if value < 0:
                raise ValueError(f"{field} must be non-negative for DCA recommendations")
        validate_execution_calendar_fields(record)

    requires_tactical_fields = action in TACTICAL_OR_REAL_ACTIONS or sleeve == "tactical_alpha_sleeve"
    if requires_tactical_fields:
        missing_tactical = sorted(TACTICAL_REQUIRED_FIELDS - set(record))
        if missing_tactical:
            raise ValueError(
                "Tactical or real-money recommendations require fields: "
                f"{missing_tactical}"
            )

        probability = record.get("forecast_probability_pct")
        readiness = record.get("execution_readiness_score")
        if not isinstance(probability, (int, float)):
            raise ValueError("forecast_probability_pct must be numeric for tactical recommendations")
        if not isinstance(readiness, (int, float)):
            raise ValueError("execution_readiness_score must be numeric for tactical recommendations")
        for field in ISO_DATE_FIELDS:
            if parse_time(str(record.get(field))) is None:
                raise ValueError(f"{field} must be an ISO date or datetime for tactical recommendations")
        for field in TACTICAL_NUMERIC_FIELDS:
            if field in record and not isinstance(record.get(field), (int, float)):
                raise ValueError(f"{field} must be numeric for tactical recommendations")
        validate_execution_calendar_fields(record)


def recommendation_index(ledger: dict[str, Any]) -> dict[str, int]:
    return {
        str(record["recommendation_id"]): index
        for index, record in enumerate(ledger["recommendations"])
        if isinstance(record, dict) and "recommendation_id" in record
    }


def supersede_key(record: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(record.get(field) or "") for field in SUPERSEDE_EQUIVALENCE_FIELDS)


def supersede_pending_equivalents(
    ledger: dict[str, Any],
    new_record: dict[str, Any],
    reason: str,
) -> list[str]:
    if new_record.get("outcome_status", "pending") != "pending":
        return []
    key = supersede_key(new_record)
    if not any(key):
        return []

    superseded_ids: list[str] = []
    now = utc_now()
    new_id = str(new_record.get("recommendation_id"))
    for existing in ledger["recommendations"]:
        if not isinstance(existing, dict):
            continue
        if existing.get("outcome_status") != "pending":
            continue
        existing_id = str(existing.get("recommendation_id"))
        if existing_id == new_id:
            continue
        if supersede_key(existing) != key:
            continue
        existing["outcome_status"] = "superseded"
        existing["superseded_at"] = now
        existing["superseded_by_recommendation_id"] = new_id
        existing["superseded_by_run_id"] = new_record.get("run_id")
        existing["supersede_reason"] = reason
        superseded_ids.append(existing_id)
        ledger["outcome_reviews"].append({
            "review_id": f"supersede-{existing_id}-{new_id}",
            "recommendation_id": existing_id,
            "reviewed_at": now,
            "outcome_status": "superseded",
            "actual_return_pct": None,
            "actual_notes": reason,
            "attribution": ["newer_equivalent_recommendation"],
            "what_should_change": "Use the newer recommendation as the active plan; do not count superseded records as hit/failed calibration evidence.",
            "proposed_change_id": None,
            "superseded_by_recommendation_id": new_id,
            "superseded_by_run_id": new_record.get("run_id"),
        })
    return superseded_ids


def coerce_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and "recommendations" in value:
        value = value["recommendations"]
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return value
    raise ValueError("Record file must contain a recommendation object, list, or recommendations wrapper")


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        load_ledger(path)
        print(f"Ledger already exists and is valid: {path}")
        return 0
    ledger = default_ledger()
    save_ledger(path, ledger)
    print(f"Initialized recommendation ledger: {path}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    path = Path(args.path)
    ledger = load_ledger(path)
    records = [normalize_record(record) for record in coerce_records(load_json(Path(args.record_file)))]
    indexes = recommendation_index(ledger)

    for record in records:
        validate_record(record)
        recommendation_id = str(record["recommendation_id"])
        if recommendation_id in indexes and not args.replace:
            raise ValueError(f"Duplicate recommendation_id: {recommendation_id}")

    superseded_total: list[str] = []
    for record in records:
        if args.supersede_open_equivalent:
            superseded_total.extend(
                supersede_pending_equivalents(
                    ledger,
                    record,
                    args.supersede_reason
                    or "Superseded by a newer equivalent recommendation from the latest manual report run.",
                )
            )
        recommendation_id = str(record["recommendation_id"])
        if recommendation_id in indexes:
            ledger["recommendations"][indexes[recommendation_id]] = record
        else:
            ledger["recommendations"].append(record)
            indexes[recommendation_id] = len(ledger["recommendations"]) - 1

    save_ledger(path, ledger)
    suffix = f"; superseded {len(superseded_total)} prior pending equivalent(s)" if superseded_total else ""
    print(f"Added {len(records)} recommendation record(s) to {path}{suffix}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    path = Path(args.path)
    ledger = load_ledger(path)
    indexes = recommendation_index(ledger)
    recommendation_id = args.recommendation_id
    if recommendation_id not in indexes:
        raise ValueError(f"Unknown recommendation_id: {recommendation_id}")

    if args.outcome_status not in OUTCOME_STATUSES - {"pending"}:
        raise ValueError("Review outcome_status must be hit/failed/not_triggered/expired/invalidated/superseded")

    record = ledger["recommendations"][indexes[recommendation_id]]
    record["outcome_status"] = args.outcome_status
    if is_v2_record(record):
        observation_status = getattr(args, "observation_status", None)
        execution_status = getattr(args, "execution_status", None)
        if observation_status is not None:
            if observation_status not in OBSERVATION_STATUSES:
                raise ValueError(
                    f"Invalid observation_status; expected one of {sorted(OBSERVATION_STATUSES)}"
                )
            record["observation_status"] = observation_status
        if execution_status is not None:
            if execution_status not in EXECUTION_STATUSES:
                raise ValueError(
                    f"Invalid execution_status; expected one of {sorted(EXECUTION_STATUSES)}"
                )
            record["execution_status"] = execution_status
    review = {
        "review_id": f"review-{recommendation_id}-{utc_now()}",
        "recommendation_id": recommendation_id,
        "reviewed_at": args.reviewed_at or utc_now(),
        "outcome_status": args.outcome_status,
        "actual_return_pct": args.actual_return_pct,
        "actual_notes": args.actual_notes,
        "attribution": [item.strip() for item in (args.attribution or "").split(",") if item.strip()],
        "what_should_change": args.what_should_change,
        "proposed_change_id": args.proposed_change_id,
    }
    if is_v2_record(record):
        review.update({
            "schema_version": OUTCOME_REVIEW_V2_SCHEMA_VERSION,
            "observation_status": record["observation_status"],
            "execution_status": record["execution_status"],
        })
    ledger["outcome_reviews"].append(review)
    save_ledger(path, ledger)
    print(f"Reviewed {recommendation_id}: {args.outcome_status}")
    return 0


def build_summary(ledger: dict[str, Any]) -> dict[str, Any]:
    recommendations = ledger["recommendations"]
    status_counts = Counter(record.get("outcome_status", "missing") for record in recommendations)
    asset_counts = Counter(record.get("asset_class", "missing") for record in recommendations)
    sleeve_counts = Counter(record.get("capital_sleeve", record.get("bucket", "missing")) for record in recommendations)
    action_counts = Counter(record.get("action", "missing") for record in recommendations)
    by_asset_status: dict[str, Counter[str]] = defaultdict(Counter)
    for record in recommendations:
        by_asset_status[str(record.get("asset_class", "missing"))][str(record.get("outcome_status", "missing"))] += 1

    resolved = status_counts["hit"] + status_counts["failed"]
    hit_rate = (status_counts["hit"] / resolved * 100) if resolved else None

    return {
        "schema_version": ledger["schema_version"],
        "updated_at": ledger["updated_at"],
        "total_recommendations": len(recommendations),
        "status_counts": dict(status_counts),
        "asset_class_counts": dict(asset_counts),
        "capital_sleeve_counts": dict(sleeve_counts),
        "action_counts": dict(action_counts),
        "hit_rate_pct_resolved_only": hit_rate,
        "outcome_reviews": len(ledger["outcome_reviews"]),
        "proposed_changes": len(ledger["proposed_changes"]),
        "by_asset_status": {asset: dict(counts) for asset, counts in by_asset_status.items()},
    }


def build_due_review(ledger: dict[str, Any], as_of: dt.datetime) -> list[dict[str, Any]]:
    due = []
    for record in ledger["recommendations"]:
        if record.get("outcome_status") != "pending":
            continue
        due_time = due_at(record)
        if due_time is None:
            due.append({
                "recommendation_id": record.get("recommendation_id"),
                "symbol": record.get("symbol"),
                "asset_class": record.get("asset_class"),
                "status": "generated_at_unparseable",
                "due_at": None,
                "days_overdue": None,
                "time_window": record.get("time_window"),
                "action": record.get("action"),
            })
            continue
        if due_time <= as_of:
            overdue = as_of - due_time
            due.append({
                "recommendation_id": record.get("recommendation_id"),
                "symbol": record.get("symbol"),
                "asset_class": record.get("asset_class"),
                "status": "due",
                "due_at": due_time.isoformat(),
                "days_overdue": overdue.days,
                "time_window": record.get("time_window"),
                "action": record.get("action"),
            })
    return due


def probability_bucket(probability: Any) -> str:
    if not isinstance(probability, (int, float)):
        return "missing_probability"
    for name, low, high in PROBABILITY_BUCKETS:
        if low <= float(probability) <= high:
            return name
    return "out_of_range"


def build_calibration(ledger: dict[str, Any]) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    for bucket_name, _, _ in PROBABILITY_BUCKETS:
        buckets[bucket_name] = {
            "count": 0,
            "resolved_count": 0,
            "hit": 0,
            "failed": 0,
            "other_status_counts": {},
            "hit_rate_pct": None,
            "brier_score": None,
        }
    buckets["missing_probability"] = {
        "count": 0,
        "resolved_count": 0,
        "hit": 0,
        "failed": 0,
        "other_status_counts": {},
        "hit_rate_pct": None,
        "brier_score": None,
    }
    buckets["out_of_range"] = {
        "count": 0,
        "resolved_count": 0,
        "hit": 0,
        "failed": 0,
        "other_status_counts": {},
        "hit_rate_pct": None,
        "brier_score": None,
    }

    brier_values: dict[str, list[float]] = defaultdict(list)
    for record in ledger["recommendations"]:
        bucket = probability_bucket(record.get("forecast_probability_pct"))
        item = buckets.setdefault(bucket, {
            "count": 0,
            "resolved_count": 0,
            "hit": 0,
            "failed": 0,
            "other_status_counts": {},
            "hit_rate_pct": None,
            "brier_score": None,
        })
        item["count"] += 1
        status = record.get("outcome_status")
        if status in RESOLVED_FOR_HIT_RATE:
            item["resolved_count"] += 1
            item[status] += 1
            probability = record.get("forecast_probability_pct")
            if isinstance(probability, (int, float)):
                actual = 1.0 if status == "hit" else 0.0
                forecast = float(probability) / 100.0
                brier_values[bucket].append((forecast - actual) ** 2)
        else:
            other = item["other_status_counts"]
            other[status or "missing"] = other.get(status or "missing", 0) + 1

    for bucket, item in buckets.items():
        resolved = item["hit"] + item["failed"]
        if resolved:
            item["hit_rate_pct"] = item["hit"] / resolved * 100.0
        if brier_values[bucket]:
            item["brier_score"] = sum(brier_values[bucket]) / len(brier_values[bucket])

    return {
        "buckets": buckets,
        "resolved_statuses_for_hit_rate": sorted(RESOLVED_FOR_HIT_RATE),
        "note": "Brier score uses hit=1 and failed=0; pending/not_triggered/expired/invalidated are excluded from Brier.",
    }


def build_export_panel(ledger: dict[str, Any], as_of: dt.datetime) -> dict[str, Any]:
    return {
        "generated_at": utc_now(),
        "summary": build_summary(ledger),
        "due_review": build_due_review(ledger, as_of),
        "calibration": build_calibration(ledger),
        "pending_proposed_changes": [
            item for item in ledger.get("proposed_changes", [])
            if item.get("approval_status") == "pending"
        ],
    }


def print_markdown_summary(summary: dict[str, Any]) -> None:
    print("# Recommendation History Summary")
    print()
    print(f"- total_recommendations: {summary['total_recommendations']}")
    print(f"- outcome_reviews: {summary['outcome_reviews']}")
    print(f"- proposed_changes: {summary['proposed_changes']}")
    hit_rate = summary["hit_rate_pct_resolved_only"]
    print(f"- hit_rate_pct_resolved_only: {hit_rate:.2f}" if hit_rate is not None else "- hit_rate_pct_resolved_only: n/a")
    print()
    print("## Status Counts")
    for key, value in sorted(summary["status_counts"].items()):
        print(f"- {key}: {value}")
    print()
    print("## Asset Class Counts")
    for key, value in sorted(summary["asset_class_counts"].items()):
        print(f"- {key}: {value}")


def cmd_summary(args: argparse.Namespace) -> int:
    ledger = load_ledger(Path(args.path))
    summary = build_summary(ledger)
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print_markdown_summary(summary)
    return 0


def cmd_due_review(args: argparse.Namespace) -> int:
    ledger = load_ledger(Path(args.path))
    as_of = parse_time(args.as_of) if args.as_of else dt.datetime.now(dt.timezone.utc)
    if as_of is None:
        raise ValueError("--as-of must be ISO-8601")
    due = build_due_review(ledger, as_of)
    if args.format == "json":
        print(json.dumps({"as_of": as_of.isoformat(), "due_review": due}, ensure_ascii=False, indent=2))
    else:
        print("# Due Recommendation Review")
        print()
        print(f"- as_of: `{as_of.isoformat()}`")
        print(f"- due_count: `{len(due)}`")
        print()
        print("| Recommendation | Symbol | Action | Due At | Days Overdue |")
        print("|---|---|---|---|---:|")
        for item in due:
            print(
                f"| `{item.get('recommendation_id')}` | {item.get('symbol')} | {item.get('action')} | "
                f"{item.get('due_at')} | {item.get('days_overdue')} |"
            )
    return 0


def cmd_calibration(args: argparse.Namespace) -> int:
    ledger = load_ledger(Path(args.path))
    calibration = build_calibration(ledger)
    if args.format == "json":
        print(json.dumps(calibration, ensure_ascii=False, indent=2))
    else:
        print("# Probability Calibration")
        print()
        print("| Bucket | Count | Resolved | Hit | Failed | Hit Rate | Brier |")
        print("|---|---:|---:|---:|---:|---:|---:|")
        for bucket, item in calibration["buckets"].items():
            hit_rate = item["hit_rate_pct"]
            brier = item["brier_score"]
            hit_rate_text = f"{hit_rate:.2f}" if hit_rate is not None else "n/a"
            brier_text = f"{brier:.4f}" if brier is not None else "n/a"
            print(
                f"| {bucket} | {item['count']} | {item['resolved_count']} | {item['hit']} | {item['failed']} | "
                f"{hit_rate_text} | {brier_text} |"
            )
    return 0


def cmd_export_panel(args: argparse.Namespace) -> int:
    ledger = load_ledger(Path(args.path))
    as_of = parse_time(args.as_of) if args.as_of else dt.datetime.now(dt.timezone.utc)
    if as_of is None:
        raise ValueError("--as-of must be ISO-8601")
    panel = build_export_panel(ledger, as_of)
    if args.format == "json":
        print(json.dumps(panel, ensure_ascii=False, indent=2))
    else:
        print("# Recommendation History Panel")
        print()
        summary = panel["summary"]
        print(f"- total_recommendations: `{summary['total_recommendations']}`")
        print(f"- pending_due_review: `{len(panel['due_review'])}`")
        print(f"- pending_proposed_changes: `{len(panel['pending_proposed_changes'])}`")
        print()
        print("## Calibration")
        for bucket, item in panel["calibration"]["buckets"].items():
            print(f"- {bucket}: count={item['count']}, resolved={item['resolved_count']}, hit_rate={item['hit_rate_pct']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage manual recommendation history")
    parser.add_argument("--path", default=str(DEFAULT_LEDGER), help="Path to recommendation_history.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create or validate a recommendation ledger")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing ledger")
    init_parser.set_defaults(func=cmd_init)

    add_parser = subparsers.add_parser("add", help="Append recommendation record(s)")
    add_parser.add_argument("--record-file", required=True, help="JSON file containing one record or a list")
    add_parser.add_argument("--replace", action="store_true", help="Replace existing recommendation_id")
    add_parser.add_argument(
        "--supersede-open-equivalent",
        action="store_true",
        help="Mark older pending recommendations with the same asset/sleeve/action_source as superseded",
    )
    add_parser.add_argument(
        "--supersede-reason",
        help="Reason stored on superseded records when --supersede-open-equivalent is used",
    )
    add_parser.set_defaults(func=cmd_add)

    review_parser = subparsers.add_parser("review", help="Record an outcome review")
    review_parser.add_argument("--recommendation-id", required=True)
    review_parser.add_argument("--outcome-status", required=True)
    review_parser.add_argument("--reviewed-at")
    review_parser.add_argument(
        "--observation-status",
        choices=sorted(OBSERVATION_STATUSES),
        help="V2 observation lifecycle state; kept separate from execution and outcome",
    )
    review_parser.add_argument(
        "--execution-status",
        choices=sorted(EXECUTION_STATUSES),
        help="V2 execution lifecycle state; kept separate from observation and outcome",
    )
    review_parser.add_argument("--actual-return-pct", type=float)
    review_parser.add_argument("--actual-notes")
    review_parser.add_argument("--attribution")
    review_parser.add_argument("--what-should-change")
    review_parser.add_argument("--proposed-change-id")
    review_parser.set_defaults(func=cmd_review)

    summary_parser = subparsers.add_parser("summary", help="Summarize recommendation outcomes")
    summary_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    summary_parser.set_defaults(func=cmd_summary)

    due_parser = subparsers.add_parser("due-review", help="List pending recommendations whose review window has elapsed")
    due_parser.add_argument("--as-of", help="ISO-8601 timestamp; defaults to now")
    due_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    due_parser.set_defaults(func=cmd_due_review)

    calibration_parser = subparsers.add_parser("calibration", help="Summarize probability calibration by bucket")
    calibration_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    calibration_parser.set_defaults(func=cmd_calibration)

    panel_parser = subparsers.add_parser("export-panel", help="Export full recommendation history panel for reports")
    panel_parser.add_argument("--as-of", help="ISO-8601 timestamp; defaults to now")
    panel_parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    panel_parser.set_defaults(func=cmd_export_panel)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 - CLI should print clear validation errors.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
