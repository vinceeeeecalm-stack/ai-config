#!/usr/bin/env python3
"""Bridge a real tactical scanner Top1 into Manual's production decision kernel.

Slow valuation/catalyst research is kept separate from the final market
certification.  The dossier can be rebound only when the final fresh scanner
keeps the same Top1.  This module never places an order.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import universal_investment_core as core  # noqa: E402


UTC = dt.timezone.utc
ROLE_IDS = (
    "valuation_fundamentals",
    "official_catalyst",
    "market_liquidity",
    "risk_challenge",
)
PRE_RESEARCH_GATES = {
    "non_positive_conservative_ev",
    "regression_sample_below_30",
    "untouched_holdout_missing",
    "lookahead_free_evidence_missing",
    "fees_and_slippage_evidence_missing",
    "profit_factor_not_above_one",
    "walk_forward_stability_failed",
    "max_drawdown_above_15pct",
    "historical_reward_risk_below_two",
}
ALLOWED_VALUATION_METHODS = {
    "network_fee_capture",
    "protocol_revenue_value_capture",
    "adoption_value_capture_scenario",
    "monetary_premium_scenario",
    "crypto_sum_of_parts",
}
SOURCE_TYPES = {"official", "independent_public", "onchain_public", "market_public"}
EVIDENCE_CATEGORIES = {"valuation", "catalyst", "fundamentals", "risk", "market"}
FUNDAMENTAL_FIELDS = (
    "adoption",
    "real_fees",
    "value_capture",
    "supply_dilution",
    "staking_net_yield",
    "liquidity",
    "security",
    "regulation",
)


class TacticalResearchContractError(ValueError):
    pass


def parse_time(value: Any) -> dt.datetime:
    try:
        return core.parse_time(str(value))
    except core.InvestmentContractError as exc:
        raise TacticalResearchContractError(str(exc)) from exc


def finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def require(data: dict[str, Any], fields: list[str] | tuple[str, ...], context: str) -> None:
    missing = [field for field in fields if data.get(field) is None]
    if missing:
        raise TacticalResearchContractError(f"{context}:missing:{','.join(missing)}")


def binding(scanner: dict[str, Any]) -> dict[str, str]:
    result = {field: str(scanner.get(field) or "") for field in core.BINDING_FIELDS}
    if any(not result[field] for field in core.BINDING_FIELDS):
        raise TacticalResearchContractError("scanner:binding_missing")
    for field in ("config_digest", "source_digest"):
        if len(result[field]) != 64:
            raise TacticalResearchContractError(f"scanner:{field}:sha256_required")
    return result


def scanner_signal(scanner: dict[str, Any], symbol: str) -> dict[str, Any]:
    signal = next(
        (
            item
            for item in scanner.get("signals", [])
            if isinstance(item, dict) and item.get("symbol") == symbol
        ),
        None,
    )
    if signal is None:
        raise TacticalResearchContractError(f"scanner:signal_missing:{symbol}")
    return signal


def scanner_price(scanner: dict[str, Any], symbol: str) -> float:
    signal = scanner_signal(scanner, symbol)
    price = finite(signal.get("mid")) or finite(signal.get("current_price"))
    if price is None or price <= 0:
        raise TacticalResearchContractError(f"scanner:price_missing:{symbol}")
    return price


def empty_core_run(
    scanner: dict[str, Any],
    account: dict[str, Any],
    *,
    decided_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    when = decided_at or str(scanner.get("captured_at"))
    payload = {
        "schema_version": core.PRODUCTION_INPUT_SCHEMA,
        "decided_at": when,
        "scanner_result": scanner,
        "research_by_symbol": {},
        "account": account,
        "delivery_status": "RUNTIME_VERIFIED",
        "live_profit_status": "UNMEASURED",
        "long_path_status": "DATA_DEGRADED",
        "regression_status": "COLLECTING_SAMPLES",
    }
    return payload, core.run_production_input(payload)


def build_research_request(scanner: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    _, preliminary = empty_core_run(scanner, account)
    ranking = preliminary["research_ranking"]
    top = ranking["ranked_candidates"][0]
    pre_failures = [
        reason for reason in top.get("hard_gate_failures", []) if reason in PRE_RESEARCH_GATES
    ]
    bind = binding(scanner)
    symbol = ranking["research_top1"]
    request_body = {
        "binding": bind,
        "symbol": symbol,
        "scanner_captured_at": scanner["captured_at"],
        "pre_research_gate_failures": pre_failures,
    }
    roles = [
        {
            "role_id": "valuation_fundamentals",
            "required": [
                "non-price-path crypto fair-value interval and method",
                "adoption, real fees, value capture, supply dilution, staking, liquidity, security and regulation",
                "field-level public evidence IDs",
            ],
        },
        {
            "role_id": "official_catalyst",
            "required": [
                "at least one official public source",
                "an exact future realization_by inside 1-7 days, or an explicit BLOCKED result",
                "why the event can transmit into token value and what invalidates it",
            ],
        },
        {
            "role_id": "market_liquidity",
            "required": [
                "final post-research fresh scanner",
                "two public prices not older than 60 seconds and within 1 percent",
                "spread, 1 percent depth, current signal and scan duration evidence",
            ],
        },
        {
            "role_id": "risk_challenge",
            "required": [
                "downside case, invalidation and supply/security challenge",
                "target-before-stop and historical hard-gate challenge",
                "maximum account loss no greater than 0.5 percent",
            ],
        },
    ]
    return {
        "schema_version": "TacticalResearchRequestV1",
        "request_id": f"tactical-research-{core.digest(request_body)[:20]}",
        **bind,
        "symbol": symbol,
        "scanner_captured_at": scanner["captured_at"],
        "provisional_ranking_id": ranking["ranking_id"],
        "pre_research_gate_failures": pre_failures,
        "deep_research_recommended": not pre_failures,
        "research_roles": roles,
        "final_market_certification_after_slow_research": True,
        "top1_change_requires_new_request": True,
        "fair_value_from_price_path_forbidden": True,
        "formal_action_eligible": False,
        "human_confirmation_required": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def validate_research_request(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("schema_version") != "TacticalResearchRequestV1":
        raise TacticalResearchContractError("TacticalResearchRequestV1_required")
    require(
        request,
        (
            "request_id", "snapshot_id", "strategy_version", "config_digest", "source_digest",
            "symbol", "scanner_captured_at", "pre_research_gate_failures", "research_roles",
            "deep_research_recommended", "formal_action_eligible", "human_confirmation_required",
            "live_orders_enabled", "private_api_used",
        ),
        "research_request",
    )
    body = {
        "binding": {field: request[field] for field in core.BINDING_FIELDS},
        "symbol": request["symbol"],
        "scanner_captured_at": request["scanner_captured_at"],
        "pre_research_gate_failures": request["pre_research_gate_failures"],
    }
    expected_id = f"tactical-research-{core.digest(body)[:20]}"
    if request["request_id"] != expected_id:
        raise TacticalResearchContractError("research_request:id_digest_mismatch")
    parse_time(request["scanner_captured_at"])
    if request["formal_action_eligible"] is not False:
        raise TacticalResearchContractError("research_request:formal_action_forbidden")
    if request["human_confirmation_required"] is not True:
        raise TacticalResearchContractError("research_request:human_confirmation_required")
    if request["live_orders_enabled"] is not False or request["private_api_used"] is not False:
        raise TacticalResearchContractError("research_request:safety_boundary_violated")
    return request


def _validate_evidence_items(dossier: dict[str, Any], prepared_at: dt.datetime) -> tuple[dict[str, dict[str, Any]], list[str]]:
    items = dossier.get("evidence_items")
    if not isinstance(items, list) or not items:
        raise TacticalResearchContractError("dossier:evidence_items_required")
    by_id: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise TacticalResearchContractError(f"dossier:evidence_items[{index}]:object_required")
        require(
            item,
            ("evidence_id", "category", "source_type", "url_or_provider", "as_of", "status", "public", "summary"),
            f"dossier:evidence_items[{index}]",
        )
        evidence_id = str(item["evidence_id"])
        if evidence_id in by_id:
            raise TacticalResearchContractError(f"dossier:duplicate_evidence_id:{evidence_id}")
        if item["category"] not in EVIDENCE_CATEGORIES:
            raise TacticalResearchContractError(f"dossier:evidence_category_invalid:{evidence_id}")
        if item["source_type"] not in SOURCE_TYPES:
            raise TacticalResearchContractError(f"dossier:source_type_invalid:{evidence_id}")
        if item["status"] not in {"verified", "blocked", "missing", "disputed", "stale"}:
            raise TacticalResearchContractError(f"dossier:evidence_status_invalid:{evidence_id}")
        if item["public"] is not True:
            blockers.append(f"non_public_evidence:{evidence_id}")
        as_of = parse_time(item["as_of"])
        if as_of > prepared_at:
            blockers.append(f"future_evidence:{evidence_id}")
        if item["status"] != "verified":
            blockers.append(f"evidence_not_verified:{evidence_id}:{item['status']}")
        by_id[evidence_id] = item
    return by_id, blockers


def _field_evidence(
    dossier: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    field: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    mapping = dossier.get("field_evidence")
    if not isinstance(mapping, dict) or not isinstance(mapping.get(field), list) or not mapping[field]:
        return [], [f"field_evidence_missing:{field}"]
    resolved: list[dict[str, Any]] = []
    blockers: list[str] = []
    for evidence_id in mapping[field]:
        item = by_id.get(str(evidence_id))
        if item is None:
            blockers.append(f"field_evidence_unknown:{field}:{evidence_id}")
            continue
        resolved.append(item)
        if item["status"] != "verified" or item["public"] is not True:
            blockers.append(f"field_evidence_unusable:{field}:{evidence_id}")
    return resolved, blockers


def validate_dossier(
    dossier: dict[str, Any],
    provenance_request: dict[str, Any],
    final_request: dict[str, Any],
    final_scanner: dict[str, Any],
    decided_at: dt.datetime,
) -> list[str]:
    if dossier.get("schema_version") != "TacticalResearchDossierV1":
        raise TacticalResearchContractError("TacticalResearchDossierV1_required")
    require(
        dossier,
        (
            "dossier_id", "request_id", "provisional_snapshot_id", "symbol", "prepared_at", "valid_until",
            "roles", "evidence_items", "field_evidence", "valuation_method_class", "fair_value", "catalyst",
            "downside", "fundamentals", "risk_challenge", "source_failures", "live_orders_enabled", "private_api_used",
        ),
        "dossier",
    )
    blockers: list[str] = []
    validate_research_request(provenance_request)
    validate_research_request(final_request)
    if dossier["request_id"] != provenance_request["request_id"]:
        blockers.append("dossier_request_mismatch")
    if dossier["provisional_snapshot_id"] != provenance_request["snapshot_id"]:
        blockers.append("dossier_provisional_snapshot_mismatch")
    if dossier["symbol"] != provenance_request["symbol"]:
        blockers.append("dossier_symbol_mismatch")
    if dossier["symbol"] != final_request["symbol"]:
        blockers.append("final_top1_changed")
    if dossier["live_orders_enabled"] is not False or dossier["private_api_used"] is not False:
        blockers.append("dossier_safety_boundary_violated")

    prepared_at = parse_time(dossier["prepared_at"])
    valid_until = parse_time(dossier["valid_until"])
    if prepared_at > decided_at:
        blockers.append("dossier_prepared_in_future")
    if valid_until <= decided_at:
        blockers.append("dossier_expired")
    if valid_until > prepared_at + dt.timedelta(hours=24):
        blockers.append("dossier_validity_above_24h")

    roles = dossier["roles"]
    if not isinstance(roles, list):
        raise TacticalResearchContractError("dossier:roles_array_required")
    role_map: dict[str, dict[str, Any]] = {}
    for item in roles:
        if not isinstance(item, dict):
            raise TacticalResearchContractError("dossier:role_object_required")
        require(item, ("role_id", "status", "evidence_ids", "blockers"), "dossier:role")
        if item["role_id"] not in ROLE_IDS:
            raise TacticalResearchContractError(f"dossier:unknown_role:{item['role_id']}")
        if item["role_id"] in role_map:
            raise TacticalResearchContractError(f"dossier:duplicate_role:{item['role_id']}")
        if item["status"] not in {"PASS", "BLOCKED"}:
            raise TacticalResearchContractError(f"dossier:role_status_invalid:{item['role_id']}")
        if not isinstance(item["evidence_ids"], list) or not item["evidence_ids"]:
            raise TacticalResearchContractError(f"dossier:role_evidence_required:{item['role_id']}")
        if not isinstance(item["blockers"], list):
            raise TacticalResearchContractError(f"dossier:role_blockers_array_required:{item['role_id']}")
        if item["status"] == "BLOCKED" and not item["blockers"]:
            raise TacticalResearchContractError(f"dossier:blocked_role_reason_required:{item['role_id']}")
        role_map[item["role_id"]] = item
    for role_id in ROLE_IDS:
        if role_id not in role_map:
            blockers.append(f"research_role_missing:{role_id}")
        elif role_map[role_id]["status"] != "PASS":
            blockers.append(f"research_role_blocked:{role_id}")

    by_id, evidence_blockers = _validate_evidence_items(dossier, prepared_at)
    blockers.extend(evidence_blockers)
    for role_id, role in role_map.items():
        for evidence_id in role["evidence_ids"]:
            if str(evidence_id) not in by_id:
                blockers.append(f"role_evidence_unknown:{role_id}:{evidence_id}")
    resolved: dict[str, list[dict[str, Any]]] = {}
    for field in ("fair_value", "catalyst", "fundamentals", "downside", "risk_challenge"):
        resolved[field], field_blockers = _field_evidence(dossier, by_id, field)
        blockers.extend(field_blockers)

    method = dossier["valuation_method_class"]
    if method not in ALLOWED_VALUATION_METHODS:
        blockers.append("valuation_method_forbidden")
    if any(item["category"] == "market" for item in resolved["fair_value"]):
        blockers.append("market_path_cannot_prove_fair_value")
    if len(resolved["fair_value"]) < 2:
        blockers.append("fair_value_needs_two_public_evidence_items")
    fair = dossier["fair_value"]
    if not isinstance(fair, dict):
        raise TacticalResearchContractError("dossier:fair_value_object_required")
    require(fair, ("low", "base", "high", "method", "uncertainty", "assumptions"), "dossier:fair_value")
    low, base, high = finite(fair["low"]), finite(fair["base"]), finite(fair["high"])
    if low is None or base is None or high is None or not (0 < low <= base <= high):
        blockers.append("fair_value_interval_invalid")
    if not isinstance(fair["assumptions"], list) or not fair["assumptions"]:
        blockers.append("fair_value_assumptions_missing")
    method_text = str(fair["method"]).lower()
    if any(token in method_text for token in ("technical", "resistance", "momentum", "impulse", "price path", "historical return")):
        blockers.append("fair_value_method_uses_forbidden_price_path")

    catalyst = dossier["catalyst"]
    if not isinstance(catalyst, dict):
        raise TacticalResearchContractError("dossier:catalyst_object_required")
    require(catalyst, ("summary", "verified", "realization_by", "time_certainty", "invalidation"), "dossier:catalyst")
    if catalyst["verified"] is not True:
        blockers.append("verified_1_7d_catalyst_missing")
    else:
        realization = parse_time(catalyst["realization_by"])
        if not (decided_at < realization <= decided_at + dt.timedelta(days=7)):
            blockers.append("catalyst_outside_1_7d")
        if not any(item["source_type"] == "official" for item in resolved["catalyst"]):
            blockers.append("official_catalyst_source_missing")

    downside = dossier["downside"]
    if not isinstance(downside, dict):
        raise TacticalResearchContractError("dossier:downside_object_required")
    require(downside, ("expected_drawdown_pct", "invalidation_conditions"), "dossier:downside")
    if finite(downside["expected_drawdown_pct"]) is None or downside["expected_drawdown_pct"] <= 0:
        blockers.append("downside_drawdown_invalid")
    if not isinstance(downside["invalidation_conditions"], list) or not downside["invalidation_conditions"]:
        blockers.append("downside_invalidation_missing")

    fundamentals = dossier["fundamentals"]
    if not isinstance(fundamentals, dict):
        raise TacticalResearchContractError("dossier:fundamentals_object_required")
    for field in FUNDAMENTAL_FIELDS:
        item = fundamentals.get(field)
        if not isinstance(item, dict):
            blockers.append(f"fundamental_missing:{field}")
            continue
        if item.get("status") != "verified" or not item.get("summary") or not item.get("evidence_ids"):
            blockers.append(f"fundamental_not_verified:{field}")
            continue
        for evidence_id in item["evidence_ids"]:
            evidence_item = by_id.get(str(evidence_id))
            if evidence_item is None:
                blockers.append(f"fundamental_evidence_unknown:{field}:{evidence_id}")
            elif evidence_item["status"] != "verified" or evidence_item["category"] not in {"fundamentals", "valuation"}:
                blockers.append(f"fundamental_evidence_unusable:{field}:{evidence_id}")

    challenge = dossier["risk_challenge"]
    if not isinstance(challenge, dict):
        raise TacticalResearchContractError("dossier:risk_challenge_object_required")
    require(challenge, ("status", "critical_blockers", "approved_max_loss_pct"), "dossier:risk_challenge")
    if challenge["status"] != "PASS" or challenge["critical_blockers"]:
        blockers.append("risk_challenge_failed")
    max_loss = finite(challenge["approved_max_loss_pct"])
    if max_loss is None or not (0 < max_loss <= 0.5):
        blockers.append("approved_max_loss_above_policy")
    if not isinstance(dossier["source_failures"], list):
        raise TacticalResearchContractError("dossier:source_failures_array_required")
    for index, failure in enumerate(dossier["source_failures"]):
        if not isinstance(failure, dict):
            raise TacticalResearchContractError(f"dossier:source_failures[{index}]:object_required")
        require(failure, ("source", "field", "blocking", "reason"), f"dossier:source_failures[{index}]")
        if not isinstance(failure["blocking"], bool):
            raise TacticalResearchContractError(f"dossier:source_failures[{index}]:blocking_boolean_required")
        if failure["blocking"]:
            blockers.append(f"candidate_source_failure:{failure['field']}:{failure['source']}")
    return list(dict.fromkeys(blockers))


def validate_market_certification(
    certification: dict[str, Any],
    scanner: dict[str, Any],
    symbol: str,
) -> tuple[list[str], float, dt.datetime]:
    if certification.get("schema_version") != "TacticalMarketCertificationV1":
        raise TacticalResearchContractError("TacticalMarketCertificationV1_required")
    require(
        certification,
        (
            "certification_id", "binding", "symbol", "certified_at", "scan_started_at", "scan_duration_seconds",
            "scanner_signal_digest", "current_signal_complete", "quotes", "liquidity_status", "data_quality_status",
            "risk_gate_pass", "human_confirmation_required", "live_orders_enabled", "private_api_used",
        ),
        "market_certification",
    )
    blockers: list[str] = []
    bind = binding(scanner)
    if certification["binding"] != bind:
        blockers.append("market_certification_binding_mismatch")
    if certification["symbol"] != symbol:
        blockers.append("market_certification_symbol_mismatch")
    signal = scanner_signal(scanner, symbol)
    if certification["scanner_signal_digest"] != core.digest(signal):
        blockers.append("market_certification_signal_digest_mismatch")
    certified_at = parse_time(certification["certified_at"])
    scan_started = parse_time(certification["scan_started_at"])
    scanner_captured = parse_time(scanner["captured_at"])
    scan_duration = finite(certification["scan_duration_seconds"])
    if scan_duration is None or not (0 <= scan_duration <= 120):
        blockers.append("market_scan_timeout")
    if scan_started > certified_at:
        blockers.append("market_scan_time_invalid")
    if abs((certified_at - scanner_captured).total_seconds()) > 60:
        blockers.append("final_scanner_not_fresh_at_certification")

    quotes = certification["quotes"]
    if not isinstance(quotes, list):
        raise TacticalResearchContractError("market_certification:quotes_array_required")
    prices: list[float] = []
    sources: set[str] = set()
    for index, quote in enumerate(quotes):
        if not isinstance(quote, dict):
            raise TacticalResearchContractError(f"market_certification:quotes[{index}]:object_required")
        require(quote, ("evidence_id", "source", "public", "price", "as_of", "latency_seconds"), f"market_certification:quotes[{index}]")
        if quote["public"] is not True:
            blockers.append(f"quote_not_public:{index}")
        price = finite(quote["price"])
        if price is None or price <= 0:
            blockers.append(f"quote_price_invalid:{index}")
            continue
        quote_time = parse_time(quote["as_of"])
        age = (certified_at - quote_time).total_seconds()
        if age < 0 or age > 60:
            blockers.append(f"quote_stale:{index}")
        latency = finite(quote["latency_seconds"])
        if latency is None or latency < 0 or latency > 12:
            blockers.append(f"quote_timeout:{index}")
        prices.append(price)
        sources.add(str(quote["source"]))
    if len(prices) < 2 or len(sources) < 2:
        blockers.append("two_public_sources_required")
    if len(prices) >= 2 and (max(prices) / min(prices) - 1) * 100 > 1:
        blockers.append("live_price_conflict")
    decision_price = sum(prices) / len(prices) if prices else scanner_price(scanner, symbol)
    scanner_mid = scanner_price(scanner, symbol)
    if abs(decision_price / scanner_mid - 1) * 100 > 1:
        blockers.append("certified_price_scanner_conflict")
    if certification["liquidity_status"] != "verified":
        blockers.append("liquidity_not_verified")
    if certification["data_quality_status"] != "verified":
        blockers.append("data_quality_not_verified")
    if certification["risk_gate_pass"] is not True:
        blockers.append("market_risk_gate_failed")
    if certification["human_confirmation_required"] is not True:
        blockers.append("human_confirmation_required")
    if certification["live_orders_enabled"] is not False or certification["private_api_used"] is not False:
        blockers.append("market_certification_safety_boundary_violated")
    if certification["current_signal_complete"] is True:
        if signal.get("stage") not in {"trigger", "post_trigger_validation"} or signal.get("breakout_close") is not True:
            blockers.append("complete_signal_not_supported_by_scanner")
    spread = finite(signal.get("spread_bps"))
    bid_depth = finite(signal.get("depth_1pct_bid_usd"))
    ask_depth = finite(signal.get("depth_1pct_ask_usd"))
    if spread is None or spread > 20:
        blockers.append("scanner_spread_above_trigger_limit")
    if bid_depth is None or ask_depth is None or min(bid_depth, ask_depth) < 25000:
        blockers.append("scanner_depth_below_25000")
    return list(dict.fromkeys(blockers)), decision_price, certified_at


def _history_row(scanner: dict[str, Any], symbol: str) -> dict[str, Any]:
    rows = (scanner.get("ranked_historical_comparison") or {}).get("rows") or []
    row = next((item for item in rows if isinstance(item, dict) and item.get("symbol") == symbol), None)
    if row is None:
        raise TacticalResearchContractError(f"scanner:history_row_missing:{symbol}")
    return row


def _build_plan(
    scanner: dict[str, Any],
    symbol: str,
    decision_price: float,
    certified_at: dt.datetime,
    dossier: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    signal = scanner_signal(scanner, symbol)
    history = signal.get("historical_comparison") or _history_row(scanner, symbol)
    target_pct = finite(history.get("target_return_pct"))
    stop_pct = finite(history.get("stop_loss_pct"))
    horizon_bars = history.get("horizon_bars")
    blockers: list[str] = []
    if target_pct is None or stop_pct is None or target_pct / stop_pct < 2:
        return None, ["historical_target_stop_contract_invalid"]
    if isinstance(horizon_bars, bool) or not isinstance(horizon_bars, int) or not (1 <= horizon_bars <= 7):
        return None, ["historical_horizon_invalid"]
    current_signal_complete = dossier["market_certification"]["current_signal_complete"] is True
    if current_signal_complete:
        entry_low = decision_price * 0.997
        entry_high = decision_price * 1.003
        trigger = "fresh certified trigger remains above the frozen breakout level"
    else:
        breakout = finite(signal.get("breakout_level")) or finite(signal.get("resistance"))
        if breakout is None:
            return None, ["wait_entry_breakout_missing"]
        entry_low = max(breakout, decision_price * 1.001)
        entry_high = entry_low * 1.003
        trigger = "closed 5m price confirms above the frozen breakout with volume and taker-buy support"
    frozen_scanner_price = scanner_price(scanner, symbol)
    stop_price = min(frozen_scanner_price, decision_price, entry_low) * (1 - stop_pct / 100)
    target_reference = max(decision_price, entry_low)
    target_1 = max(
        target_reference * (1 + target_pct / 100),
        decision_price + 2 * (decision_price - stop_price),
    )
    target_2 = max(
        target_reference * (1 + target_pct * 1.5 / 100),
        target_1 + (target_1 - decision_price) * 0.5,
    )
    fair_base = finite(dossier["fair_value"].get("base"))
    if fair_base is None or fair_base < target_1:
        blockers.append("fair_value_does_not_support_target_1")
    max_loss = finite(dossier["risk_challenge"].get("approved_max_loss_pct"))
    plan = {
        "entry_low": round(entry_low, 12),
        "entry_high": round(entry_high, 12),
        "entry_trigger": trigger,
        "valid_until": (certified_at + dt.timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "target_1": round(target_1, 12),
        "target_2": round(target_2, 12),
        "stop_price": round(stop_price, 12),
        "latest_exit_at": (certified_at + dt.timedelta(days=horizon_bars)).isoformat().replace("+00:00", "Z"),
        "max_allowed_loss_pct": max_loss,
    }
    return plan, blockers


def build_research_payload(
    scanner: dict[str, Any],
    request: dict[str, Any],
    dossier: dict[str, Any],
    certification: dict[str, Any],
    decision_price: float,
    certified_at: dt.datetime,
) -> tuple[dict[str, Any] | None, list[str]]:
    working = copy.deepcopy(dossier)
    working["market_certification"] = certification
    plan, blockers = _build_plan(scanner, request["symbol"], decision_price, certified_at, working)
    if blockers or plan is None:
        return None, blockers
    bind = binding(scanner)
    current_plan = {
        **copy.deepcopy(plan),
        **bind,
        "symbol": request["symbol"],
        "generated_at": certification["certified_at"],
    }
    evidence_ids = list(
        dict.fromkeys(
            [
                *[str(item["evidence_id"]) for item in dossier["evidence_items"]],
                *[str(item["evidence_id"]) for item in certification["quotes"]],
                f"scanner:{scanner['snapshot_id']}:{request['symbol']}",
            ]
        )
    )
    live_quotes = [
        {
            "source": item["source"],
            "public": item["public"],
            "price": item["price"],
            "as_of": item["as_of"],
            "latency_seconds": item["latency_seconds"],
        }
        for item in certification["quotes"]
    ]
    research = {
        "binding": bind,
        "signal_as_of": certification["certified_at"],
        "current_signal_complete": certification["current_signal_complete"],
        "fair_value": copy.deepcopy(dossier["fair_value"]),
        "catalyst": {
            key: copy.deepcopy(dossier["catalyst"][key])
            for key in ("summary", "verified", "realization_by", "time_certainty")
        },
        "downside": copy.deepcopy(dossier["downside"]),
        "fundamentals": {
            field: copy.deepcopy(dossier["fundamentals"][field]) for field in FUNDAMENTAL_FIELDS
        },
        "plan": plan,
        "liquidity_status": certification["liquidity_status"],
        "data_quality_status": certification["data_quality_status"],
        "risk_gate_pass": certification["risk_gate_pass"],
        "source_failures": [
            f"{item['field']}:{item['source']}:{item['reason']}"
            for item in dossier["source_failures"]
            if item["blocking"] is True
        ],
        "evidence_ids": evidence_ids,
        "live_evidence": {
            **bind,
            "symbol": request["symbol"],
            "certified_at": certification["certified_at"],
            "scan_duration_seconds": certification["scan_duration_seconds"],
            "quotes": live_quotes,
            "current_plan": current_plan,
            "current_plan_digest": core.digest(current_plan),
        },
    }
    return research, []


def safe_zero_account() -> dict[str, Any]:
    return {
        "authority": "current_coverage_zero_cash",
        "deployable_cash": 0.0,
        "settled": True,
        "max_manual_amount": 0.0,
    }


def run_handoff(
    *,
    scanner: dict[str, Any],
    account: dict[str, Any] | None = None,
    dossier: dict[str, Any] | None = None,
    market_certification: dict[str, Any] | None = None,
    provisional_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    account = copy.deepcopy(account or safe_zero_account())
    request = build_research_request(scanner, account)
    provenance_request = copy.deepcopy(provisional_request or request)
    validate_research_request(provenance_request)
    empty_input, empty_result = empty_core_run(scanner, account)
    status = "REQUEST_READY"
    blockers: list[str] = []
    production_input = empty_input
    manual_result = empty_result
    research_populated = False

    if request["pre_research_gate_failures"]:
        status = "PRE_RESEARCH_GATES_FAILED"
        blockers.extend(request["pre_research_gate_failures"])
    elif dossier is None:
        status = "EVIDENCE_PENDING"
        blockers.append("tactical_research_dossier_missing")
    else:
        decided_at = parse_time(
            (market_certification or {}).get("certified_at")
            or dossier.get("prepared_at")
            or scanner.get("captured_at")
        )
        blockers.extend(
            validate_dossier(
                dossier,
                provenance_request,
                request,
                scanner,
                decided_at,
            )
        )
        if "final_top1_changed" in blockers or "dossier_symbol_mismatch" in blockers:
            status = "TOP1_CHANGED"
        elif blockers:
            status = "EVIDENCE_BLOCKED"
        elif market_certification is None:
            status = "MARKET_CERTIFICATION_PENDING"
            blockers.append("final_market_certification_missing")
        else:
            certification_blockers, decision_price, certified_at = validate_market_certification(
                market_certification,
                scanner,
                request["symbol"],
            )
            blockers.extend(certification_blockers)
            if blockers:
                status = "CERTIFICATION_BLOCKED"
            else:
                research, build_blockers = build_research_payload(
                    scanner,
                    request,
                    dossier,
                    market_certification,
                    decision_price,
                    certified_at,
                )
                blockers.extend(build_blockers)
                if research is None or blockers:
                    status = "EVIDENCE_BLOCKED"
                else:
                    production_input = {
                        "schema_version": core.PRODUCTION_INPUT_SCHEMA,
                        "decided_at": market_certification["certified_at"],
                        "scanner_result": scanner,
                        "research_by_symbol": {request["symbol"]: research},
                        "account": account,
                        "delivery_status": "RUNTIME_VERIFIED",
                        "live_profit_status": "UNMEASURED",
                        "long_path_status": "DATA_DEGRADED",
                        "regression_status": "COLLECTING_SAMPLES",
                    }
                    manual_result = core.run_production_input(production_input)
                    research_populated = True
                    status = (
                        "ARBITRATED_ACTION"
                        if manual_result["decision"]["current_action"] in {"ENTER_NOW", "WAIT_FOR_ENTRY"}
                        else "ARBITRATED_NO_TRADE"
                    )

    if status != "ARBITRATED_ACTION" and manual_result["decision"]["current_action"] != "NO_TRADE":
        raise TacticalResearchContractError("non_action_handoff_must_be_no_trade")
    return {
        "schema_version": "TacticalResearchHandoffResultV1",
        "generated_at": production_input["decided_at"],
        **binding(scanner),
        "research_request": request,
        "provisional_research_request_id": provenance_request["request_id"],
        "handoff_status": status,
        "blockers": list(dict.fromkeys(blockers)),
        "research_by_symbol_populated": research_populated,
        "production_input": production_input,
        "manual_result": manual_result,
        "research_top1": manual_result["decision"]["research_top1"],
        "current_action": manual_result["decision"]["current_action"],
        "decision_card": manual_result["decision"]["decision_card"],
        "account_execution": manual_result["decision"]["account_execution"],
        "live_profit_status": manual_result["status"]["live_profit_status"],
        "paper_live_separated": True,
        "formal_action_eligible": status == "ARBITRATED_ACTION",
        "human_confirmation_required": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def load_json(path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TacticalResearchContractError(f"json_object_required:{path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and certify a tactical Top1 research handoff")
    parser.add_argument("--scanner", required=True)
    parser.add_argument("--research-request", help="Original request used to produce a slow dossier")
    parser.add_argument("--dossier")
    parser.add_argument("--market-certification")
    parser.add_argument("--account")
    parser.add_argument("--output")
    args = parser.parse_args()
    scanner = load_json(args.scanner)
    assert scanner is not None
    result = run_handoff(
        scanner=scanner,
        dossier=load_json(args.dossier),
        market_certification=load_json(args.market_certification),
        provisional_request=load_json(args.research_request),
        account=load_json(args.account),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
