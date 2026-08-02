#!/usr/bin/env python3
"""Deterministic shadow contracts and stage-0 replay for the unified short-term engine.

This module intentionally does not alter the production Manual V3 decision.  It
provides version-bound contracts, historical replay, near-miss memory and profit
attribution primitives that can be promoted one rule at a time by later frozen
goals.  All outputs are read-only research evidence and live ordering remains
disabled.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "UnifiedShortTermOpportunityEngineV1"
FORMAL_ACTIONS = {"ENTER_NOW", "WAIT_FOR_ENTRY", "NO_TRADE"}
INTERNAL_STATES = {"TRACKING", "EARLY_WARNING", "ELIGIBLE", "REJECTED"}
BINDING_FIELDS = ("snapshot_id", "strategy_version", "config_digest", "source_digest")
MAX_HOLDING_MINUTES = 7 * 24 * 60
FORBIDDEN_KEYS = {
    "api_key",
    "apikey",
    "secret",
    "access_token",
    "refresh_token",
    "private_key",
    "password",
    "account_export",
}


class ShortTermContractError(ValueError):
    """Raised when a versioned short-term contract violates a hard invariant."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_time(value: Any, field: str) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ShortTermContractError(f"missing:{field}")
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ShortTermContractError(f"invalid_time:{field}") from exc
    if parsed.tzinfo is None:
        raise ShortTermContractError(f"timezone_required:{field}")
    return parsed.astimezone(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def as_number(value: Any, field: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ShortTermContractError(f"invalid_number:{field}")
    return float(value)


def require_text(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ShortTermContractError(f"missing:{field}")
    return value.strip()


def scan_forbidden_keys(value: Any, prefix: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_KEYS:
                raise ShortTermContractError(f"forbidden_field:{prefix}{key}")
            scan_forbidden_keys(child, f"{prefix}{key}.")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_forbidden_keys(child, f"{prefix}{index}.")


def validate_digest(value: Any, field: str) -> str:
    text = require_text({field: value}, field).lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ShortTermContractError(f"invalid_digest:{field}")
    return text


def binding(record: dict[str, Any]) -> dict[str, str]:
    result = {
        "snapshot_id": require_text(record, "snapshot_id"),
        "strategy_version": require_text(record, "strategy_version"),
        "config_digest": validate_digest(record.get("config_digest"), "config_digest"),
        "source_digest": validate_digest(record.get("source_digest"), "source_digest"),
    }
    return result


def assert_same_binding(*records: dict[str, Any]) -> dict[str, str]:
    if not records:
        raise ShortTermContractError("binding_records_required")
    first = binding(records[0])
    for record in records[1:]:
        current = binding(record)
        if current != first:
            mismatch = [field for field in BINDING_FIELDS if current[field] != first[field]]
            raise ShortTermContractError("binding_mismatch:" + ",".join(mismatch))
    return first


def validate_safety(record: dict[str, Any], *, formal_action_eligible: bool | None = None) -> None:
    if record.get("live_orders_enabled") is not False:
        raise ShortTermContractError("live_orders_must_be_false")
    if record.get("private_api_used") is not False:
        raise ShortTermContractError("private_api_used_must_be_false")
    if record.get("human_confirmation_required") is not True:
        raise ShortTermContractError("human_confirmation_required")
    if formal_action_eligible is not None and record.get("formal_action_eligible") is not formal_action_eligible:
        raise ShortTermContractError("formal_action_eligibility_mismatch")
    scan_forbidden_keys(record)


def validate_factor_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "ShortTermFactorSnapshotV1":
        raise ShortTermContractError("schema:ShortTermFactorSnapshotV1")
    require_text(record, "factor_snapshot_id")
    require_text(record, "candidate_id")
    require_text(record, "symbol")
    binding(record)
    parse_time(record.get("captured_at"), "captured_at")
    price = as_number(record.get("current_price"), "current_price")
    if price is None or price <= 0:
        raise ShortTermContractError("current_price_must_be_positive")
    rank = record.get("rank")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
        raise ShortTermContractError("rank_must_be_positive_integer")
    for group in ("spot", "structure", "derivatives", "catalyst", "liquidity", "data_quality"):
        if not isinstance(record.get(group), dict):
            raise ShortTermContractError(f"missing_group:{group}")
    validate_safety(record, formal_action_eligible=False)
    return record


def _num(mapping: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = mapping.get(key)
    if value is None:
        return default
    return float(as_number(value, key) or 0.0)


def directional_confirmation(
    factor: dict[str, Any],
    *,
    funding_zscore_limit: float = 2.5,
    funding_rate_abs_limit_pct: float = 0.10,
) -> dict[str, Any]:
    """Evaluate whether derivatives fuel is confirmed by non-OI evidence.

    The result is an internal factor status.  It never authorizes a formal
    action and deliberately fails when OI is the only positive observation.
    """

    validate_factor_snapshot(factor)
    spot = factor["spot"]
    structure = factor["structure"]
    derivatives = factor["derivatives"]
    liquidity = factor["liquidity"]

    oi_fuel = any(
        _num(derivatives, key) > 0
        for key in ("oi_change_1h_pct", "oi_change_4h_pct", "oi_change_24h_pct")
    )
    spot_buying = sum(
        [
            _num(spot, "relative_strength_pct") > 0,
            _num(spot, "taker_buy_ratio") >= 0.55,
            _num(spot, "cvd_delta") > 0,
            _num(spot, "orderbook_imbalance") > 0,
            _num(spot, "relative_volume") >= 1.2,
        ]
    ) >= 3
    structure_support = bool(structure.get("higher_lows") or structure.get("breakout_attempt"))
    perp_confirms = bool(
        derivatives.get("spot_perp_volume_confirmation") is True
        or _num(derivatives, "perp_taker_buy_ratio") >= 0.55
    )
    funding_extreme = bool(
        abs(_num(derivatives, "funding_zscore")) >= funding_zscore_limit
        or abs(_num(derivatives, "funding_rate_pct")) >= funding_rate_abs_limit_pct
    )
    spread_bps = _num(liquidity, "spread_bps", 999999.0)
    depth_bid = _num(liquidity, "depth_1pct_bid_usd")
    depth_ask = _num(liquidity, "depth_1pct_ask_usd")
    liquidity_ok = spread_bps <= 20.0 and min(depth_bid, depth_ask) > 0

    checks = {
        "oi_fuel": oi_fuel,
        "spot_buying": spot_buying,
        "structure_support": structure_support,
        "perp_confirms": perp_confirms,
        "funding_not_extreme": not funding_extreme,
        "liquidity_observed": liquidity_ok,
    }
    confirmed = all(checks.values())
    reasons = [key for key, passed in checks.items() if not passed]
    return {
        "schema_version": "ShortTermDirectionalConfirmationV1",
        "factor_snapshot_id": factor["factor_snapshot_id"],
        **binding(factor),
        "confirmed": confirmed,
        "checks": checks,
        "blockers": reasons,
        "oi_alone_can_authorize": False,
        "formal_action_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }


def validate_opportunity(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "UnifiedShortTermOpportunityV1":
        raise ShortTermContractError("schema:UnifiedShortTermOpportunityV1")
    require_text(record, "opportunity_id")
    require_text(record, "factor_snapshot_id")
    require_text(record, "symbol")
    binding(record)
    hold = record.get("estimated_holding_minutes")
    if isinstance(hold, bool) or not isinstance(hold, int) or not (1 <= hold <= MAX_HOLDING_MINUTES):
        raise ShortTermContractError("estimated_holding_minutes_out_of_range")
    as_number(record.get("conservative_net_profit_pct"), "conservative_net_profit_pct")
    drawdown = as_number(record.get("expected_max_drawdown_pct"), "expected_max_drawdown_pct")
    if drawdown is None or drawdown <= 0:
        raise ShortTermContractError("expected_max_drawdown_pct_must_be_positive_magnitude")
    probability = as_number(record.get("target_before_stop_lower_pct"), "target_before_stop_lower_pct")
    if probability is None or not 0 <= probability <= 100:
        raise ShortTermContractError("target_before_stop_lower_pct_out_of_range")
    as_number(record.get("reward_risk_ratio"), "reward_risk_ratio")
    hard_gates = record.get("hard_gates")
    required_gates = {"value", "catalyst", "realtime", "regression", "liquidity", "risk"}
    if not isinstance(hard_gates, dict) or set(hard_gates) != required_gates:
        raise ShortTermContractError("hard_gates_incomplete")
    if any(not isinstance(value, bool) for value in hard_gates.values()):
        raise ShortTermContractError("hard_gates_must_be_boolean")
    if record.get("entry_state") not in {"ENTER_NOW", "WAIT_FOR_ENTRY", "NO_TRADE"}:
        raise ShortTermContractError("invalid_entry_state")
    factor = record.get("factor_snapshot")
    if not isinstance(factor, dict):
        raise ShortTermContractError("factor_snapshot_required")
    validate_factor_snapshot(factor)
    assert_same_binding(record, factor)
    if factor["factor_snapshot_id"] != record["factor_snapshot_id"]:
        raise ShortTermContractError("factor_snapshot_id_mismatch")
    if factor["symbol"] != record["symbol"]:
        raise ShortTermContractError("factor_symbol_mismatch")
    validate_safety(record, formal_action_eligible=False)
    return record


def opportunity_rank_metrics(record: dict[str, Any]) -> dict[str, float | bool]:
    validate_opportunity(record)
    conservative = float(record["conservative_net_profit_pct"])
    drawdown = float(record["expected_max_drawdown_pct"])
    hold_hours = max(float(record["estimated_holding_minutes"]) / 60.0, 1.0 / 60.0)
    decay = float(record.get("information_decay_penalty_pct") or 0.0)
    overnight = float(record.get("overnight_risk_penalty_pct") or 0.0)
    monitoring = float(record.get("monitoring_burden_penalty_pct") or 0.0)
    net_after_time = conservative - decay - overnight - monitoring
    unit_time_risk = net_after_time / max(drawdown, 0.01) / math.sqrt(hold_hours)
    eligible = bool(
        all(record["hard_gates"].values())
        and directional_confirmation(record["factor_snapshot"])["confirmed"] is True
        and conservative > 0
        and float(record["reward_risk_ratio"]) >= 2.0
    )
    return {
        "eligible": eligible,
        "net_after_time_penalty_pct": round(net_after_time, 9),
        "unit_time_risk_net_profit": round(unit_time_risk, 12),
    }


def rank_opportunities(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        raise ShortTermContractError("opportunities_required")
    # A ranking round is meaningful only when every candidate is evaluated from
    # the same point-in-time snapshot and the same strategy/config/source lineage.
    assert_same_binding(*records)
    evaluated = []
    for record in records:
        metrics = opportunity_rank_metrics(record)
        evaluated.append({"record": record, "metrics": metrics})
    evaluated.sort(
        key=lambda item: (
            int(item["metrics"]["eligible"]),
            float(item["metrics"]["net_after_time_penalty_pct"]),
            float(item["metrics"]["unit_time_risk_net_profit"]),
            float(item["record"]["target_before_stop_lower_pct"]),
            float(item["record"]["reward_risk_ratio"]),
            float(item["record"].get("catalyst_time_certainty") or 0.0),
            float(item["record"].get("liquidity_exit_score") or 0.0),
            -int(item["record"]["estimated_holding_minutes"]),
            item["record"]["symbol"],
        ),
        reverse=True,
    )
    rows = []
    for index, item in enumerate(evaluated, start=1):
        record = item["record"]
        rows.append(
            {
                "rank": index,
                "opportunity_id": record["opportunity_id"],
                "symbol": record["symbol"],
                "estimated_holding_minutes": record["estimated_holding_minutes"],
                "conservative_net_profit_pct": record["conservative_net_profit_pct"],
                **item["metrics"],
            }
        )
    top = evaluated[0]
    shadow_state = "ELIGIBLE" if top["metrics"]["eligible"] else "REJECTED"
    decision = {
        "schema_version": "ShortTermDecisionV1",
        "decision_id": "shortterm-shadow-" + sha256_json(rows)[:20],
        **binding(top["record"]),
        "research_top1": top["record"]["symbol"],
        "current_action": "NO_TRADE",
        "shadow_research_state": shadow_state,
        "shadow_gate_state": "ELIGIBLE_NOW" if (
            top["metrics"]["eligible"] and top["record"]["entry_state"] == "ENTER_NOW"
        ) else "ELIGIBLE_WAIT" if top["metrics"]["eligible"] else "REJECTED",
        "ranked_candidates": rows,
        "production_rule_changed": False,
        "formal_action_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    validate_decision(decision)
    return decision


def validate_decision(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "ShortTermDecisionV1":
        raise ShortTermContractError("schema:ShortTermDecisionV1")
    require_text(record, "decision_id")
    require_text(record, "research_top1")
    binding(record)
    if record.get("current_action") not in FORMAL_ACTIONS:
        raise ShortTermContractError("invalid_formal_action")
    if record.get("shadow_research_state") not in INTERNAL_STATES:
        raise ShortTermContractError("invalid_shadow_research_state")
    if record.get("shadow_gate_state") not in {"ELIGIBLE_NOW", "ELIGIBLE_WAIT", "REJECTED"}:
        raise ShortTermContractError("invalid_shadow_gate_state")
    if record.get("production_rule_changed") is not False:
        raise ShortTermContractError("stage0_cannot_change_production_rule")
    rows = record.get("ranked_candidates")
    if not isinstance(rows, list) or not rows or rows[0].get("symbol") != record["research_top1"]:
        raise ShortTermContractError("unique_research_top1_mismatch")
    validate_safety(record, formal_action_eligible=False)
    return record


def build_near_miss_state(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    expires_at: str,
) -> dict[str, Any]:
    validate_factor_snapshot(current)
    captured = parse_time(current["captured_at"], "captured_at")
    expiry = parse_time(expires_at, "expires_at")
    if not captured < expiry <= captured + dt.timedelta(days=7):
        raise ShortTermContractError("near_miss_expiry_out_of_range")
    current_rank = int(current["rank"])
    if previous is None:
        if not 4 <= current_rank <= 20:
            raise ShortTermContractError("new_near_miss_rank_must_be_4_to_20")
        rank_velocity = 0.0
        volume_acceleration = 0.0
        oi_acceleration = 0.0
        strength_acceleration = 0.0
        prior_state_id = None
    else:
        validate_near_miss_state(previous)
        previous_time = parse_time(previous["observed_at"], "previous.observed_at")
        if captured <= previous_time:
            raise ShortTermContractError("near_miss_time_must_advance")
        if previous["symbol"] != current["symbol"]:
            raise ShortTermContractError("near_miss_symbol_mismatch")
        if binding(previous) != binding(current):
            # A new snapshot is expected, but strategy/config/source lineage must remain stable.
            for field in ("strategy_version", "config_digest", "source_digest"):
                if previous[field] != current[field]:
                    raise ShortTermContractError(f"near_miss_lineage_mismatch:{field}")
        hours = max((captured - previous_time).total_seconds() / 3600.0, 1.0 / 60.0)
        rank_velocity = (int(previous["current_rank"]) - current_rank) / hours
        volume_acceleration = (
            _num(current["spot"], "relative_volume") - float(previous.get("relative_volume") or 0.0)
        ) / hours
        oi_acceleration = (
            _num(current["derivatives"], "oi_change_1h_pct") - float(previous.get("oi_change_1h_pct") or 0.0)
        ) / hours
        strength_acceleration = (
            _num(current["spot"], "relative_strength_pct") - float(previous.get("relative_strength_pct") or 0.0)
        ) / hours
        prior_state_id = previous["near_miss_state_id"]
    positive_accelerations = sum(value > 0 for value in (volume_acceleration, oi_acceleration, strength_acceleration))
    internal_state = "EARLY_WARNING" if rank_velocity > 0 and positive_accelerations >= 2 else "TRACKING"
    result = {
        "schema_version": "NearMissCandidateStateV1",
        "near_miss_state_id": "near-miss-" + sha256_json(
            {"factor_snapshot_id": current["factor_snapshot_id"], "previous": prior_state_id}
        )[:20],
        "factor_snapshot_id": current["factor_snapshot_id"],
        "symbol": current["symbol"],
        **binding(current),
        "observed_at": current["captured_at"],
        "expires_at": expires_at,
        "current_rank": current_rank,
        "previous_state_id": prior_state_id,
        "rank_velocity_per_hour": round(rank_velocity, 9),
        "relative_volume": _num(current["spot"], "relative_volume"),
        "volume_acceleration_per_hour": round(volume_acceleration, 9),
        "oi_change_1h_pct": _num(current["derivatives"], "oi_change_1h_pct"),
        "oi_acceleration_per_hour": round(oi_acceleration, 9),
        "relative_strength_pct": _num(current["spot"], "relative_strength_pct"),
        "relative_strength_acceleration_per_hour": round(strength_acceleration, 9),
        "internal_state": internal_state,
        "formal_action_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    validate_near_miss_state(result)
    return result


def validate_near_miss_state(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "NearMissCandidateStateV1":
        raise ShortTermContractError("schema:NearMissCandidateStateV1")
    require_text(record, "near_miss_state_id")
    require_text(record, "factor_snapshot_id")
    require_text(record, "symbol")
    binding(record)
    observed = parse_time(record.get("observed_at"), "observed_at")
    expiry = parse_time(record.get("expires_at"), "expires_at")
    if not observed < expiry <= observed + dt.timedelta(days=7):
        raise ShortTermContractError("near_miss_expiry_out_of_range")
    if record.get("internal_state") not in {"TRACKING", "EARLY_WARNING"}:
        raise ShortTermContractError("invalid_near_miss_internal_state")
    validate_safety(record, formal_action_eligible=False)
    return record


def validate_lifecycle(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "ShortTermLifecycleV1":
        raise ShortTermContractError("schema:ShortTermLifecycleV1")
    require_text(record, "lifecycle_id")
    require_text(record, "decision_id")
    binding(record)
    opened = parse_time(record.get("opened_at"), "opened_at")
    expires = parse_time(record.get("expires_at"), "expires_at")
    next_check = parse_time(record.get("next_check_at"), "next_check_at")
    if not opened < next_check <= expires <= opened + dt.timedelta(days=7):
        raise ShortTermContractError("lifecycle_clock_out_of_range")
    if record.get("current_action") not in FORMAL_ACTIONS:
        raise ShortTermContractError("invalid_formal_action")
    if record.get("auto_order_forbidden") is not True:
        raise ShortTermContractError("auto_order_must_be_forbidden")
    validate_safety(record, formal_action_eligible=False)
    return record


def append_record(path: Path, record: dict[str, Any], id_field: str) -> str:
    record_id = require_text(record, id_field)
    scan_forbidden_keys(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ShortTermContractError(f"invalid_jsonl:{path}:{line_number}") from exc
            item_id = item.get(id_field)
            if isinstance(item_id, str):
                existing[item_id] = item
    if record_id in existing:
        if canonical_json(existing[record_id]) == canonical_json(record):
            return "NO_UPDATE"
        raise ShortTermContractError(f"append_only_collision:{record_id}")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(record) + "\n")
    return "APPENDED"


def validate_missed_review(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("schema_version") != "MissedOpportunityReviewV1":
        raise ShortTermContractError("schema:MissedOpportunityReviewV1")
    require_text(record, "missed_review_id")
    require_text(record, "candidate_id")
    require_text(record, "symbol")
    binding(record)
    cutoff = parse_time(record.get("decision_cutoff_at"), "decision_cutoff_at")
    reviewed = parse_time(record.get("reviewed_at"), "reviewed_at")
    if not cutoff < reviewed <= cutoff + dt.timedelta(days=7):
        raise ShortTermContractError("missed_review_time_out_of_range")
    if record.get("classification") not in {
        "data_missing",
        "ranking_error",
        "threshold_error",
        "scan_delay",
        "not_predictable",
    }:
        raise ShortTermContractError("invalid_missed_classification")
    if record.get("paper_roi_eligible") is not False or record.get("real_money_roi_eligible") is not False:
        raise ShortTermContractError("missed_review_cannot_enter_roi")
    validate_safety(record, formal_action_eligible=False)
    return record


def first_path_outcome(
    entry_price: float,
    path: list[dict[str, Any]],
    *,
    target_pct: float,
    stop_pct: float,
    cutoff: dt.datetime,
) -> dict[str, Any]:
    target_price = entry_price * (1.0 + target_pct / 100.0)
    stop_price = entry_price * (1.0 - stop_pct / 100.0)
    first_event = "unresolved"
    first_event_at = None
    highs: list[float] = []
    lows: list[float] = []
    prior_time = cutoff
    for index, point in enumerate(path):
        point_time = parse_time(point.get("as_of"), f"outcome_path[{index}].as_of")
        if point_time <= cutoff:
            raise ShortTermContractError("future_leakage:outcome_at_or_before_cutoff")
        if point_time <= prior_time:
            raise ShortTermContractError("outcome_path_not_strictly_increasing")
        if point_time > cutoff + dt.timedelta(days=7):
            raise ShortTermContractError("outcome_path_exceeds_seven_days")
        prior_time = point_time
        high = float(as_number(point.get("high"), f"outcome_path[{index}].high") or 0.0)
        low = float(as_number(point.get("low"), f"outcome_path[{index}].low") or 0.0)
        close = float(as_number(point.get("close"), f"outcome_path[{index}].close") or 0.0)
        if min(high, low, close) <= 0 or not low <= close <= high:
            raise ShortTermContractError("invalid_ohlc_path_point")
        highs.append(high)
        lows.append(low)
        if first_event == "unresolved":
            target_hit = high >= target_price
            stop_hit = low <= stop_price
            if stop_hit:
                # Conservative same-bar handling: stop first.
                first_event = "stop_first"
                first_event_at = iso(point_time)
            elif target_hit:
                first_event = "target_first"
                first_event_at = iso(point_time)
    mfe = (max(highs) / entry_price - 1.0) * 100.0 if highs else None
    mae = (min(lows) / entry_price - 1.0) * 100.0 if lows else None
    return {
        "target_pct": target_pct,
        "stop_pct": stop_pct,
        "first_event": first_event,
        "first_event_at": first_event_at,
        "mfe_pct": round(mfe, 6) if mfe is not None else None,
        "mae_pct": round(mae, 6) if mae is not None else None,
        "same_bar_stop_first": True,
    }


def _median(values: Iterable[float]) -> float | None:
    items = list(values)
    return round(float(statistics.median(items)), 6) if items else None


def baseline_replay(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != "UnifiedShortTermReplayInputV1":
        raise ShortTermContractError("schema:UnifiedShortTermReplayInputV1")
    if payload.get("production_rule_changed") is not False:
        raise ShortTermContractError("stage0_cannot_change_production_rule")
    thresholds = payload.get("thresholds")
    if not isinstance(thresholds, list) or [(x.get("target_pct"), x.get("stop_pct")) for x in thresholds] != [
        (5.0, 3.0),
        (8.0, 4.0),
        (10.0, 5.0),
    ]:
        raise ShortTermContractError("stage0_thresholds_must_be_5_3_8_4_10_5")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ShortTermContractError("candidates_must_be_list")
    factors = [item.get("factor_snapshot") for item in candidates]
    if factors:
        if any(not isinstance(item, dict) for item in factors):
            raise ShortTermContractError("factor_snapshots_must_be_objects")
        assert_same_binding(*factors)
    rows = []
    for index, candidate in enumerate(candidates):
        factor = candidate.get("factor_snapshot")
        if not isinstance(factor, dict):
            raise ShortTermContractError(f"missing_factor_snapshot:{index}")
        validate_factor_snapshot(factor)
        cutoff = parse_time(factor["captured_at"], "captured_at")
        start_at = parse_time(candidate.get("opportunity_started_at"), "opportunity_started_at")
        if start_at > cutoff:
            raise ShortTermContractError("opportunity_start_after_cutoff")
        start_price = float(as_number(candidate.get("opportunity_start_price"), "opportunity_start_price") or 0.0)
        if start_price <= 0:
            raise ShortTermContractError("opportunity_start_price_must_be_positive")
        baseline_action = candidate.get("baseline_action")
        if baseline_action not in FORMAL_ACTIONS:
            raise ShortTermContractError("invalid_baseline_action")
        path = candidate.get("outcome_path")
        if not isinstance(path, list):
            raise ShortTermContractError("outcome_path_must_be_list")
        outcomes = [
            first_path_outcome(
                float(factor["current_price"]),
                path,
                target_pct=float(threshold["target_pct"]),
                stop_pct=float(threshold["stop_pct"]),
                cutoff=cutoff,
            )
            for threshold in thresholds
        ]
        delay_minutes = (cutoff - start_at).total_seconds() / 60.0
        move_consumed = (float(factor["current_price"]) / start_price - 1.0) * 100.0
        rows.append(
            {
                "candidate_id": factor["candidate_id"],
                "symbol": factor["symbol"],
                "factor_snapshot_id": factor["factor_snapshot_id"],
                **binding(factor),
                "baseline_rank": int(candidate.get("baseline_rank", factor["rank"])),
                "baseline_research_top1": candidate.get("baseline_research_top1") is True,
                "baseline_action": baseline_action,
                "alert_delay_minutes": round(delay_minutes, 6),
                "move_consumed_before_detection_pct": round(move_consumed, 6),
                "outcomes": outcomes,
            }
        )
    summaries = []
    for threshold_index, threshold in enumerate(thresholds):
        mature = [row for row in rows if row["outcomes"][threshold_index]["first_event"] != "unresolved"]
        golden = [row for row in mature if row["outcomes"][threshold_index]["first_event"] == "target_first"]
        top20 = [row for row in golden if row["baseline_rank"] <= 20]
        top3 = [row for row in golden if row["baseline_rank"] <= 3]
        top1 = [row for row in golden if row["baseline_research_top1"]]
        formal = [row for row in golden if row["baseline_action"] in {"ENTER_NOW", "WAIT_FOR_ENTRY"}]
        false_alerts = [
            row
            for row in mature
            if row["baseline_rank"] <= 3 and row["outcomes"][threshold_index]["first_event"] == "stop_first"
        ]
        denominator = len(golden)
        summaries.append(
            {
                "target_pct": threshold["target_pct"],
                "stop_pct": threshold["stop_pct"],
                "mature_count": len(mature),
                "golden_opportunity_count": denominator,
                "top20_recognition_rate_pct": round(len(top20) / denominator * 100.0, 6) if denominator else None,
                "top3_recognition_rate_pct": round(len(top3) / denominator * 100.0, 6) if denominator else None,
                "top1_recognition_rate_pct": round(len(top1) / denominator * 100.0, 6) if denominator else None,
                "formal_plan_recognition_rate_pct": round(len(formal) / denominator * 100.0, 6) if denominator else None,
                "target_first_rate_pct": round(denominator / len(mature) * 100.0, 6) if mature else None,
                "median_move_consumed_before_detection_pct": _median(
                    row["move_consumed_before_detection_pct"] for row in golden
                ),
                "median_alert_delay_minutes": _median(row["alert_delay_minutes"] for row in golden),
                "median_mfe_pct": _median(row["outcomes"][threshold_index]["mfe_pct"] for row in mature),
                "median_mae_pct": _median(row["outcomes"][threshold_index]["mae_pct"] for row in mature),
                "false_alert_count": len(false_alerts),
                "status": "MEASURED" if mature else "DATA_INSUFFICIENT",
            }
        )
    return {
        "schema_version": "UnifiedShortTermBaselineReportV1",
        "engine_version": SCHEMA_VERSION,
        "generated_from_input_digest": sha256_json(payload),
        "production_rule_changed": False,
        "candidate_count": len(rows),
        "threshold_summaries": summaries,
        "candidate_results": rows,
        "ada_special_case_present": False,
        "formal_action_eligible": False,
        "paper_roi_eligible": False,
        "real_money_roi_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }


def runtime_readiness(observation_ledger: Path, outcome_ledger: Path | None = None) -> dict[str, Any]:
    observations = []
    if observation_ledger.exists():
        observations = [json.loads(line) for line in observation_ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    outcomes = []
    if outcome_ledger is not None and outcome_ledger.exists():
        outcomes = [json.loads(line) for line in outcome_ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    observations_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(observations):
        if item.get("schema_version") != "ObservationSampleV1":
            raise ShortTermContractError(f"invalid_observation_schema:{index}")
        observation_id = require_text(item, "observation_id")
        if observation_id in observations_by_id:
            raise ShortTermContractError(f"duplicate_observation_id:{observation_id}")
        binding(item)
        observed_at = parse_time(item.get("observed_at"), "observed_at")
        review_due_at = parse_time(item.get("review_due_at"), "review_due_at")
        if review_due_at <= observed_at:
            raise ShortTermContractError("review_due_at_must_follow_observed_at")
        observations_by_id[observation_id] = item
    reviewed_ids: set[str] = set()
    for index, item in enumerate(outcomes):
        if item.get("schema_version") != "ObservationOutcomeReviewV1":
            raise ShortTermContractError(f"invalid_outcome_schema:{index}")
        observation_id = require_text(item, "observation_id")
        if observation_id in reviewed_ids:
            raise ShortTermContractError(f"duplicate_outcome_observation_id:{observation_id}")
        observation = observations_by_id.get(observation_id)
        if observation is None:
            raise ShortTermContractError(f"orphan_outcome:{observation_id}")
        assert_same_binding(item, observation)
        for field in ("candidate_id", "symbol"):
            if item.get(field) != observation.get(field):
                raise ShortTermContractError(f"outcome_{field}_mismatch:{observation_id}")
        reviewed_at = parse_time(item.get("reviewed_at"), "reviewed_at")
        review_due_at = parse_time(observation.get("review_due_at"), "review_due_at")
        if reviewed_at != review_due_at:
            raise ShortTermContractError(f"outcome_not_bound_to_review_due_at:{observation_id}")
        reviewed_ids.add(observation_id)
    matured = [item for item in observations if item.get("observation_id") in reviewed_ids]
    symbols = sorted({str(item.get("symbol")) for item in observations if item.get("symbol")})
    return {
        "schema_version": "UnifiedShortTermStage0RuntimeStatusV1",
        "engine_version": SCHEMA_VERSION,
        "observation_count": len(observations),
        "review_count": len(outcomes),
        "matured_observation_count": len(matured),
        "symbol_count": len(symbols),
        "status": "MEASURED" if matured else "DATA_INSUFFICIENT",
        "unique_next_step": (
            "run_stage0_replay_on_matured_append_only_outcomes"
            if matured
            else "wait_for_existing_observations_to_reach_review_due_at_without_changing_rules"
        ),
        "production_rule_changed": False,
        "formal_action_eligible": False,
        "paper_roi_eligible": False,
        "real_money_roi_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }


def profit_attribution(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != "ShortTermProfitAttributionInputV1":
        raise ShortTermContractError("schema:ShortTermProfitAttributionInputV1")
    attribution_binding = binding(payload)
    opening = float(as_number(payload.get("opening_capital_usd"), "opening_capital_usd") or 0.0)
    if opening <= 0:
        raise ShortTermContractError("opening_capital_usd_must_be_positive")
    trades = payload.get("trades")
    if not isinstance(trades, list):
        raise ShortTermContractError("trades_must_be_list")
    versions = set()
    net_profit = 0.0
    holding_labels: dict[str, int] = {"intraday": 0, "overnight_to_3d": 0, "four_to_7d": 0}
    for index, trade in enumerate(trades):
        if trade.get("evidence_mode") != "live":
            raise ShortTermContractError(f"non_live_trade_in_real_roi:{index}")
        if trade.get("request_mode") not in {"unified_short_term", "tactical_1_7d"}:
            raise ShortTermContractError(f"wrong_request_mode:{index}")
        hold = trade.get("holding_minutes")
        if isinstance(hold, bool) or not isinstance(hold, int) or not (0 <= hold <= MAX_HOLDING_MINUTES):
            raise ShortTermContractError(f"holding_minutes_out_of_range:{index}")
        versions.add(require_text(trade, "strategy_version"))
        if trade["strategy_version"] != attribution_binding["strategy_version"]:
            raise ShortTermContractError(f"trade_strategy_binding_mismatch:{index}")
        if validate_digest(trade.get("config_digest"), "config_digest") != attribution_binding["config_digest"]:
            raise ShortTermContractError(f"trade_config_binding_mismatch:{index}")
        if validate_digest(trade.get("source_digest"), "source_digest") != attribution_binding["source_digest"]:
            raise ShortTermContractError(f"trade_source_binding_mismatch:{index}")
        net_profit += float(as_number(trade.get("net_pnl_usd"), f"trades[{index}].net_pnl_usd") or 0.0)
        if hold <= 24 * 60:
            holding_labels["intraday"] += 1
        elif hold <= 3 * 24 * 60:
            holding_labels["overnight_to_3d"] += 1
        else:
            holding_labels["four_to_7d"] += 1
    if len(versions) > 1:
        raise ShortTermContractError("strategy_versions_cannot_share_profit_denominator")
    result = {
        "schema_version": "ShortTermProfitAttributionV1",
        "attribution_id": "shortterm-profit-" + sha256_json(payload)[:20],
        **attribution_binding,
        "opening_capital_usd": round(opening, 6),
        "trade_count": len(trades),
        "strategy_version": attribution_binding["strategy_version"],
        "net_profit_usd": round(net_profit, 6),
        "money_weighted_net_roi_pct": round(net_profit / opening * 100.0, 6),
        "holding_time_labels": holding_labels,
        "single_tactical_pool": True,
        "paper_included": False,
        "observations_included": False,
        # Real receipts make profit measurable; they are not sufficient evidence
        # for BUSINESS_READY, which is governed by a separate 90-day result gate.
        "live_profit_measured": bool(trades),
        "business_ready_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    scan_forbidden_keys(result)
    return result


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ShortTermContractError(f"json_object_required:{path}")
    return value


def write_json(path: Path | None, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path is None:
        print(text, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def self_test() -> dict[str, Any]:
    digest_a = "a" * 64
    digest_b = "b" * 64
    factor = {
        "schema_version": "ShortTermFactorSnapshotV1",
        "factor_snapshot_id": "factor-self-test",
        "candidate_id": "TESTUSDT",
        "symbol": "TESTUSDT",
        "snapshot_id": "snapshot-self-test",
        "strategy_version": "shadow-v1",
        "config_digest": digest_a,
        "source_digest": digest_b,
        "captured_at": "2030-01-01T00:00:00Z",
        "current_price": 10.0,
        "rank": 4,
        "spot": {"relative_strength_pct": 1.0, "relative_volume": 2.0, "taker_buy_ratio": 0.6, "cvd_delta": 1.0, "orderbook_imbalance": 0.2},
        "structure": {"higher_lows": True, "breakout_attempt": True},
        "derivatives": {"oi_change_1h_pct": 2.0, "funding_zscore": 0.5, "funding_rate_pct": 0.01, "perp_taker_buy_ratio": 0.6, "spot_perp_volume_confirmation": True},
        "catalyst": {"status": "verified"},
        "liquidity": {"spread_bps": 2.0, "depth_1pct_bid_usd": 10000.0, "depth_1pct_ask_usd": 10000.0},
        "data_quality": {"status": "verified"},
        "formal_action_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    confirmation = directional_confirmation(factor)
    if confirmation["confirmed"] is not True or confirmation["oi_alone_can_authorize"] is not False:
        raise ShortTermContractError("self_test_directional_confirmation_failed")
    return {
        "schema_version": "UnifiedShortTermSelfTestV1",
        "status": "PASS",
        "engine_version": SCHEMA_VERSION,
        "checks": ["binding", "directional_confirmation", "oi_alone_forbidden"],
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")

    replay = sub.add_parser("baseline-replay")
    replay.add_argument("--input", required=True)
    replay.add_argument("--output")

    runtime = sub.add_parser("runtime-status")
    runtime.add_argument("--observation-ledger", required=True)
    runtime.add_argument("--outcome-ledger")
    runtime.add_argument("--output")

    ranking = sub.add_parser("shadow-rank")
    ranking.add_argument("--input", required=True)
    ranking.add_argument("--output")

    profit = sub.add_parser("profit-attribution")
    profit.add_argument("--input", required=True)
    profit.add_argument("--output")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.self_test:
        write_json(None, self_test())
        return
    if args.command == "baseline-replay":
        result = baseline_replay(load_json(Path(args.input)))
        write_json(Path(args.output) if args.output else None, result)
        return
    if args.command == "runtime-status":
        result = runtime_readiness(
            Path(args.observation_ledger),
            Path(args.outcome_ledger) if args.outcome_ledger else None,
        )
        write_json(Path(args.output) if args.output else None, result)
        return
    if args.command == "shadow-rank":
        payload = load_json(Path(args.input))
        opportunities = payload.get("opportunities")
        if not isinstance(opportunities, list):
            raise ShortTermContractError("opportunities_must_be_list")
        write_json(Path(args.output) if args.output else None, rank_opportunities(opportunities))
        return
    if args.command == "profit-attribution":
        write_json(
            Path(args.output) if args.output else None,
            profit_attribution(load_json(Path(args.input))),
        )
        return
    raise SystemExit("choose --self-test or a subcommand")


if __name__ == "__main__":
    main()
