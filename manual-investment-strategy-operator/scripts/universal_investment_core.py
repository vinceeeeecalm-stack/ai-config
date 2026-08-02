#!/usr/bin/env python3
"""Deterministic cross-asset investment analysis and manual live-decision kernel.

Regression evidence (walk-forward/Paper) is input evidence only.  The public action
is always ENTER_NOW, WAIT_FOR_ENTRY, or NO_TRADE and never authorizes an order.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable


UTC = dt.timezone.utc
ACTION_VALUES = {"ENTER_NOW", "WAIT_FOR_ENTRY", "NO_TRADE"}
ACCOUNT_EXECUTION_VALUES = {"CASH_READY", "NO_DEPLOY_CASH", "SETTLEMENT_BLOCKED"}
LIVE_PROFIT_VALUES = {"UNMEASURED", "EXPERIMENT_RUNNING", "NOT_ON_TRACK", "GOAL_HIT", "FAILED"}
LONG_PATH_VALUES = {"DATA_DEGRADED", "BASELINE_DEFINED", "ON_5Y_PATH", "ON_10Y_PATH", "NOT_ON_TRACK"}
BINDING_FIELDS = ("snapshot_id", "strategy_version", "config_digest", "source_digest")
PRODUCTION_INPUT_SCHEMA = "UniversalInvestmentRunInputV1"
PRODUCTION_OUTPUT_SCHEMA = "UniversalInvestmentRunResultV1"


class InvestmentContractError(ValueError):
    pass


def parse_time(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvestmentContractError(f"invalid_iso_time:{value}") from exc
    if parsed.tzinfo is None:
        raise InvestmentContractError(f"timezone_required:{value}")
    return parsed.astimezone(UTC)


def digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require(data: dict[str, Any], fields: Iterable[str], context: str) -> None:
    missing = [field for field in fields if data.get(field) is None]
    if missing:
        raise InvestmentContractError(f"{context}:missing:{','.join(missing)}")


def _positive(data: dict[str, Any], fields: Iterable[str], context: str, *, allow_zero: bool = False) -> None:
    for field in fields:
        value = data.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvestmentContractError(f"{context}:{field}:number_required")
        if value < 0 if allow_zero else value <= 0:
            raise InvestmentContractError(f"{context}:{field}:positive_required")


def _validate_binding(data: dict[str, Any], context: str) -> None:
    _require(data, BINDING_FIELDS, context)
    for field in ("config_digest", "source_digest"):
        value = data[field]
        if not isinstance(value, str) or len(value) != 64:
            raise InvestmentContractError(f"{context}:{field}:sha256_required")


def validate_universal_case(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") != "UniversalInvestmentCaseV1":
        raise InvestmentContractError("UniversalInvestmentCaseV1_required")
    _require(data, [
        "case_id", "asset_class", "horizon", "symbol", "as_of", "current_price",
        "fair_value", "catalyst", "downside", "fundamentals", "plan",
        "regression_evidence_id", "evidence_ids", "liquidity_status",
        "data_quality_status", "risk_gate_pass", "live_orders_enabled",
    ], "case")
    _validate_binding(data, "case")
    if data["asset_class"] not in {"crypto", "us_equity"}:
        raise InvestmentContractError("case:asset_class_unsupported")
    if data["horizon"] not in {"tactical_1_7d", "longterm"}:
        raise InvestmentContractError("case:horizon_unsupported")
    if data["live_orders_enabled"] is not False:
        raise InvestmentContractError("case:live_orders_must_be_false")
    parse_time(data["as_of"])
    _positive(data, ["current_price"], "case")
    fair = data["fair_value"]
    if not isinstance(fair, dict):
        raise InvestmentContractError("case:fair_value_object_required")
    _require(fair, ["low", "base", "high", "method", "uncertainty"], "fair_value")
    _positive(fair, ["low", "base", "high"], "fair_value")
    if not fair["low"] <= fair["base"] <= fair["high"]:
        raise InvestmentContractError("fair_value:interval_not_ordered")
    catalyst = data["catalyst"]
    _require(catalyst, ["summary", "verified", "realization_by", "time_certainty"], "catalyst")
    parse_time(catalyst["realization_by"])
    downside = data["downside"]
    _require(downside, ["expected_drawdown_pct", "invalidation_conditions"], "downside")
    _positive(downside, ["expected_drawdown_pct"], "downside")
    if not isinstance(downside["invalidation_conditions"], list) or not downside["invalidation_conditions"]:
        raise InvestmentContractError("downside:invalidation_conditions_required")
    fundamentals = data["fundamentals"]
    if data["asset_class"] == "crypto":
        _require(fundamentals, [
            "adoption", "real_fees", "value_capture", "supply_dilution", "staking_net_yield",
            "liquidity", "security", "regulation",
        ], "crypto_fundamentals")
    else:
        _require(fundamentals, [
            "revenue", "cash_flow", "earnings_quality", "balance_sheet", "valuation",
            "moat", "company_catalyst", "industry_catalyst",
        ], "equity_fundamentals")
    plan = data["plan"]
    if data["horizon"] == "tactical_1_7d":
        _require(plan, [
            "entry_low", "entry_high", "entry_trigger", "valid_until", "target_1",
            "target_2", "stop_price", "latest_exit_at", "max_allowed_loss_pct",
        ], "tactical_plan")
        _positive(plan, ["entry_low", "entry_high", "target_1", "target_2", "stop_price", "max_allowed_loss_pct"], "tactical_plan")
        if plan["entry_low"] > plan["entry_high"] or plan["target_2"] <= plan["target_1"]:
            raise InvestmentContractError("tactical_plan:levels_not_ordered")
        if parse_time(plan["latest_exit_at"]) <= parse_time(data["as_of"]):
            raise InvestmentContractError("tactical_plan:latest_exit_not_future")
        if parse_time(plan["latest_exit_at"]) > parse_time(data["as_of"]) + dt.timedelta(days=7):
            raise InvestmentContractError("tactical_plan:outside_1_7d")
        parse_time(plan["valid_until"])
    else:
        _require(plan, ["buy_low", "buy_high", "tranches", "wait_condition", "review_at", "thesis_invalidation"], "longterm_plan")
        _positive(plan, ["buy_low", "buy_high"], "longterm_plan")
        if plan["buy_low"] > plan["buy_high"]:
            raise InvestmentContractError("longterm_plan:buy_range_not_ordered")
        if any(field in plan for field in ("stop_price", "target_1", "target_2", "latest_exit_at")):
            raise InvestmentContractError("longterm_plan:tactical_exit_fields_forbidden")
        if not isinstance(plan["tranches"], list) or not plan["tranches"]:
            raise InvestmentContractError("longterm_plan:tranches_required")
        parse_time(plan["review_at"])
    if not isinstance(data["evidence_ids"], list) or not data["evidence_ids"]:
        raise InvestmentContractError("case:evidence_ids_required")
    return data


def validate_regression_evidence(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") != "RegressionEvidenceV1":
        raise InvestmentContractError("RegressionEvidenceV1_required")
    _require(data, [
        "regression_id", "symbol", "horizon", "mode", "as_of", "sample_size",
        "conservative_ev_pct", "max_drawdown_pct", "holdout_pass", "no_lookahead",
        "fees_included", "paper_live_separated", "formal_action_eligible",
    ], "regression")
    _validate_binding(data, "regression")
    if data["mode"] not in {"historical_walkforward", "forward_paper"}:
        raise InvestmentContractError("regression:mode_unsupported")
    if data["formal_action_eligible"] is not False:
        raise InvestmentContractError("regression:cannot_be_formal_action")
    if data["paper_live_separated"] is not True:
        raise InvestmentContractError("regression:paper_live_separation_required")
    if isinstance(data["sample_size"], bool) or not isinstance(data["sample_size"], int) or data["sample_size"] < 0:
        raise InvestmentContractError("regression:sample_size_invalid")
    parse_time(data["as_of"])
    return data


def binding_tuple(data: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(data[field]) for field in BINDING_FIELDS)


def _regression_gate(case: dict[str, Any], regression: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if binding_tuple(case) != binding_tuple(regression):
        reasons.append("regression_binding_mismatch")
    if case["symbol"] != regression["symbol"] or case["horizon"] != regression["horizon"]:
        reasons.append("regression_case_mismatch")
    if case["regression_evidence_id"] != regression["regression_id"]:
        reasons.append("regression_id_mismatch")
    if regression["sample_size"] < 10:
        reasons.append("regression_sample_insufficient")
    if regression["conservative_ev_pct"] <= 0:
        reasons.append("non_positive_conservative_ev")
    if regression["holdout_pass"] is not True or regression["no_lookahead"] is not True:
        reasons.append("regression_integrity_failed")
    if regression["fees_included"] is not True or regression["paper_live_separated"] is not True:
        reasons.append("regression_cost_or_mode_invalid")
    return reasons


def _research_gate(case: dict[str, Any], regression: dict[str, Any]) -> list[str]:
    reasons = _regression_gate(case, regression)
    discount = case["fair_value"]["base"] / case["current_price"] - 1
    if discount <= 0:
        reasons.append("not_below_base_fair_value")
    if case["catalyst"]["verified"] is not True:
        reasons.append("catalyst_not_verified")
    if case["liquidity_status"] != "verified":
        reasons.append("liquidity_not_verified")
    if case["data_quality_status"] != "verified":
        reasons.append("data_quality_not_verified")
    if case["risk_gate_pass"] is not True:
        reasons.append("risk_gate_failed")
    if case.get("source_failures"):
        reasons.append("candidate_source_failure")
    return reasons


def rank_investment_cases(
    cases: list[dict[str, Any]], regressions: list[dict[str, Any]]
) -> dict[str, Any]:
    if not cases:
        raise InvestmentContractError("at_least_one_case_required")
    regression_by_id = {}
    for item in regressions:
        validate_regression_evidence(item)
        regression_by_id[item["regression_id"]] = item
    ranked: list[dict[str, Any]] = []
    shared_binding = None
    for case in cases:
        validate_universal_case(case)
        if shared_binding is None:
            shared_binding = binding_tuple(case)
        elif binding_tuple(case) != shared_binding:
            raise InvestmentContractError("candidate_binding_mismatch")
        regression = regression_by_id.get(case["regression_evidence_id"])
        if regression is None:
            raise InvestmentContractError(f"regression_not_found:{case['symbol']}")
        if binding_tuple(case) != binding_tuple(regression):
            raise InvestmentContractError(f"regression_binding_mismatch:{case['symbol']}")
        if case["symbol"] != regression["symbol"] or case["horizon"] != regression["horizon"]:
            raise InvestmentContractError(f"regression_case_mismatch:{case['symbol']}")
        reasons = _research_gate(case, regression)
        discount_pct = (case["fair_value"]["base"] / case["current_price"] - 1) * 100
        drawdown = float(case["downside"]["expected_drawdown_pct"])
        ev = float(regression["conservative_ev_pct"])
        score = discount_pct + ev + (ev / drawdown) * 10 + float(case["catalyst"]["time_certainty"]) * 5
        ranked.append({
            "case_id": case["case_id"],
            "symbol": case["symbol"],
            "eligible": not reasons,
            "hard_gate_failures": reasons,
            "value_discount_pct": round(discount_pct, 6),
            "conservative_ev_pct": ev,
            "expected_drawdown_pct": drawdown,
            "score": round(score, 9),
        })
    ranked.sort(key=lambda item: (int(item["eligible"]), item["score"], item["symbol"]), reverse=True)
    result = {
        "schema_version": "UniversalCandidateRankingV1",
        **dict(zip(BINDING_FIELDS, shared_binding or ())),
        "ranking_id": f"universal-rank-{digest(ranked + list(shared_binding or ()))[:16]}",
        "ranked_candidates": ranked,
        "research_top1": ranked[0]["symbol"],
    }
    return result


def _validate_live_evidence(live: dict[str, Any], case: dict[str, Any], now: dt.datetime) -> list[str]:
    reasons: list[str] = []
    _validate_binding(live, "live_evidence")
    if binding_tuple(live) != binding_tuple(case):
        reasons.append("live_binding_mismatch")
    if live.get("symbol") != case["symbol"]:
        reasons.append("live_symbol_mismatch")
    try:
        certified_age = (now - parse_time(live.get("certified_at"))).total_seconds()
        if certified_age < 0 or certified_age > 60:
            reasons.append("live_certification_stale")
    except InvestmentContractError:
        reasons.append("live_certification_time_invalid")
    quotes = live.get("quotes") or []
    public = [quote for quote in quotes if quote.get("public") is True and quote.get("source")]
    prices: list[float] = []
    sources: set[str] = set()
    for quote in public:
        sources.add(str(quote["source"]))
        price = quote.get("price")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
            reasons.append("invalid_live_price")
            continue
        prices.append(float(price))
        age = (now - parse_time(quote.get("as_of"))).total_seconds()
        if age < 0 or age > 60:
            reasons.append("stale_live_price")
        latency = quote.get("latency_seconds")
        if isinstance(latency, bool) or not isinstance(latency, (int, float)) or latency < 0 or latency > 12:
            reasons.append("live_source_timeout")
    if len(sources) < 2 or len(prices) < 2:
        reasons.append("two_public_sources_required")
    if len(prices) >= 2 and (max(prices) / min(prices) - 1) * 100 > 1:
        reasons.append("live_price_conflict")
    scan_seconds = live.get("scan_duration_seconds")
    if isinstance(scan_seconds, bool) or not isinstance(scan_seconds, (int, float)) or scan_seconds < 0 or scan_seconds > 120:
        reasons.append("scan_timeout")
    current_plan = live.get("current_plan")
    if live.get("current_plan_digest") != digest(current_plan):
        reasons.append("current_plan_digest_mismatch")
    if not isinstance(current_plan, dict):
        reasons.append("current_plan_required")
    else:
        if any(current_plan.get(field) != case.get(field) for field in BINDING_FIELDS):
            reasons.append("current_plan_binding_mismatch")
        if current_plan.get("symbol") != case["symbol"]:
            reasons.append("current_plan_symbol_mismatch")
        try:
            plan_age = (now - parse_time(current_plan.get("generated_at"))).total_seconds()
            if plan_age < 0 or plan_age > 60:
                reasons.append("current_plan_stale")
        except InvestmentContractError:
            reasons.append("current_plan_generation_time_invalid")
    return list(dict.fromkeys(reasons))


def build_live_decision(
    *,
    cases: list[dict[str, Any]],
    regressions: list[dict[str, Any]],
    live_evidence_by_symbol: dict[str, dict[str, Any]],
    account: dict[str, Any],
    decided_at: str,
) -> dict[str, Any]:
    ranking = rank_investment_cases(cases, regressions)
    top_symbol = ranking["research_top1"]
    top_case = next(case for case in cases if case["symbol"] == top_symbol)
    top_rank = ranking["ranked_candidates"][0]
    now = parse_time(decided_at)
    reasons = list(top_rank["hard_gate_failures"])
    live = live_evidence_by_symbol.get(top_symbol)
    if live is None:
        reasons.append("live_evidence_missing")
    else:
        reasons.extend(_validate_live_evidence(live, top_case, now))
    authority = account.get("authority")
    if top_case["horizon"] == "longterm" and authority != "user_confirmed":
        reasons.append("longterm_data_degraded")

    prices = [float(item["price"]) for item in (live or {}).get("quotes", []) if isinstance(item.get("price"), (int, float)) and not isinstance(item.get("price"), bool) and item.get("price") > 0]
    decision_price = round(sum(prices) / len(prices), 12) if prices else None
    plan = copy.deepcopy((live or {}).get("current_plan"))
    action = "NO_TRADE"
    card = None
    if not reasons and decision_price is not None and isinstance(plan, dict):
        if top_case["horizon"] == "tactical_1_7d":
            required = ["entry_low", "entry_high", "entry_trigger", "valid_until", "target_1", "target_2", "stop_price", "latest_exit_at", "max_allowed_loss_pct"]
            if any(plan.get(field) is None for field in required):
                reasons.append("rebuilt_tactical_plan_incomplete")
            elif any(isinstance(plan[field], bool) or not isinstance(plan[field], (int, float)) or plan[field] <= 0 for field in ("entry_low", "entry_high", "target_1", "target_2", "stop_price", "max_allowed_loss_pct")):
                reasons.append("rebuilt_tactical_plan_levels_invalid")
            elif plan["entry_low"] > plan["entry_high"]:
                reasons.append("rebuilt_tactical_plan_levels_invalid")
            elif parse_time(plan["valid_until"]) <= now:
                reasons.append("rebuilt_tactical_plan_expired")
            elif plan["target_1"] <= decision_price or plan["target_2"] <= plan["target_1"] or plan["stop_price"] >= min(plan["entry_low"], decision_price):
                reasons.append("rebuilt_tactical_plan_levels_invalid")
            elif not (now < parse_time(plan["latest_exit_at"]) <= now + dt.timedelta(days=7)):
                reasons.append("rebuilt_tactical_exit_invalid")
            else:
                in_entry_range = plan["entry_low"] <= decision_price <= plan["entry_high"]
                action = (
                    "ENTER_NOW"
                    if in_entry_range and top_case.get("current_signal_complete", True) is True
                    else "WAIT_FOR_ENTRY"
                )
                card = {key: plan[key] for key in required}
        else:
            required = ["buy_low", "buy_high", "tranches", "wait_condition", "review_at", "thesis_invalidation"]
            if any(plan.get(field) is None for field in required):
                reasons.append("rebuilt_longterm_plan_incomplete")
            elif any(field in plan for field in ("stop_price", "target_1", "target_2", "latest_exit_at")):
                reasons.append("tactical_fields_forbidden_for_longterm")
            elif any(isinstance(plan[field], bool) or not isinstance(plan[field], (int, float)) or plan[field] <= 0 for field in ("buy_low", "buy_high")) or plan["buy_low"] > plan["buy_high"]:
                reasons.append("rebuilt_longterm_plan_levels_invalid")
            elif parse_time(plan["review_at"]) <= now:
                reasons.append("rebuilt_longterm_review_not_future")
            else:
                action = "ENTER_NOW" if plan["buy_low"] <= decision_price <= plan["buy_high"] else "WAIT_FOR_ENTRY"
                card = {key: plan[key] for key in required}
    if reasons:
        action, card = "NO_TRADE", None

    cash = account.get("deployable_cash")
    if isinstance(cash, bool) or not isinstance(cash, (int, float)) or cash <= 0:
        account_execution, executable_amount = "NO_DEPLOY_CASH", 0.0
    elif account.get("settled") is not True:
        account_execution, executable_amount = "SETTLEMENT_BLOCKED", 0.0
    else:
        account_execution = "CASH_READY"
        executable_amount = round(min(float(cash), float(account.get("max_manual_amount", cash))), 2)
    decision = {
        "schema_version": "LiveInvestmentDecisionV1",
        "decision_id": f"live-decision-{digest([ranking, live, account, decided_at, action])[:16]}",
        **{field: ranking[field] for field in BINDING_FIELDS},
        "decided_at": decided_at,
        "research_top1": top_symbol,
        "ranking_id": ranking["ranking_id"],
        "current_action": action,
        "no_trade_reasons": list(dict.fromkeys(reasons)),
        "decision_price": decision_price,
        "price_as_of": (live or {}).get("certified_at"),
        "decision_card": card,
        "account_execution": account_execution,
        "executable_amount": executable_amount if action != "NO_TRADE" else 0.0,
        "regression_evidence_ids": [top_case["regression_evidence_id"]],
        "paper_action_exposed": False,
        "live_orders_enabled": False,
        "ranking": ranking,
    }
    return validate_live_decision(decision)


def validate_live_decision(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") != "LiveInvestmentDecisionV1":
        raise InvestmentContractError("LiveInvestmentDecisionV1_required")
    _validate_binding(data, "live_decision")
    _require(data, ["decision_id", "decided_at", "research_top1", "ranking_id", "current_action", "no_trade_reasons", "account_execution", "executable_amount", "regression_evidence_ids", "paper_action_exposed", "live_orders_enabled"], "live_decision")
    if data["current_action"] not in ACTION_VALUES:
        raise InvestmentContractError("live_decision:formal_action_invalid")
    if data["account_execution"] not in ACCOUNT_EXECUTION_VALUES:
        raise InvestmentContractError("live_decision:account_execution_invalid")
    if data["live_orders_enabled"] is not False or data["paper_action_exposed"] is not False:
        raise InvestmentContractError("live_decision:paper_or_live_order_boundary_violated")
    if data["current_action"] == "NO_TRADE" and data.get("decision_card") is not None:
        raise InvestmentContractError("live_decision:no_trade_cannot_have_trade_card")
    if data["current_action"] == "WAIT_FOR_ENTRY" and not isinstance(data.get("decision_card"), dict):
        raise InvestmentContractError("live_decision:wait_requires_complete_card")
    if data["account_execution"] != "CASH_READY" and data["executable_amount"] != 0:
        raise InvestmentContractError("live_decision:blocked_account_amount_must_be_zero")
    parse_time(data["decided_at"])
    return data


def build_investment_outcome_status(
    *,
    delivery_status: str,
    decision: dict[str, Any],
    live_profit_status: str,
    long_path_status: str,
    regression_status: str,
    as_of: str,
) -> dict[str, Any]:
    validate_live_decision(decision)
    if live_profit_status not in LIVE_PROFIT_VALUES:
        raise InvestmentContractError("outcome:live_profit_status_invalid")
    if long_path_status not in LONG_PATH_VALUES:
        raise InvestmentContractError("outcome:long_path_status_invalid")
    blockers: list[dict[str, Any]] = []

    def add(priority: int, axis: str, code: str, message: str, next_step: str, blocking: bool = True) -> None:
        blockers.append({"priority": priority, "axis": axis, "code": code, "message": message, "next_step": next_step, "blocking": blocking})

    if delivery_status != "RUNTIME_VERIFIED":
        add(1, "delivery", "runtime_not_verified", f"系统交付为 {delivery_status}", "完成同摘要运行验证")
    if decision["current_action"] == "NO_TRADE":
        add(2, "market", "no_trade", "当前没有可执行真钱交易计划", "刷新同一时点证据并重新运行硬门")
    if decision["account_execution"] != "CASH_READY":
        add(3, "account", decision["account_execution"].lower(), f"账户执行为 {decision['account_execution']}", "仅在准备真钱执行时确认可用资金和结算", blocking=False)
    if live_profit_status != "GOAL_HIT":
        add(4, "live_profit", "live_profit_unproven", f"真钱利润为 {live_profit_status}", "积累真实成交、退出和时间序列证据")
    if long_path_status == "DATA_DEGRADED":
        add(5, "long_path", "longterm_data_degraded", "长期账户仅有旧估算", "完整确认持仓、现金、成本和质押后建立正式基线")
    blockers.sort(key=lambda item: (item["priority"], item["axis"], item["code"]))
    primary = next((item for item in blockers if item["blocking"]), None)
    business_ready = delivery_status == "RUNTIME_VERIFIED" and live_profit_status == "GOAL_HIT" and long_path_status != "DATA_DEGRADED"
    status = {
        "schema_version": "InvestmentOutcomeStatusV2",
        "as_of": as_of,
        "delivery_status": delivery_status,
        "account_execution": decision["account_execution"],
        "live_profit_status": live_profit_status,
        "long_path_status": long_path_status,
        "regression_status": {"internal_only": True, "status": regression_status, "can_promote_live_profit": False},
        "business_outcome_status": "BUSINESS_READY" if business_ready else "EVIDENCE_PENDING",
        "all_blockers": blockers,
        "unique_blocker": primary["message"] if primary else "无",
        "unique_next_step": primary["next_step"] if primary else "维持当前规则并继续监控",
        "v2_compat": {
            "schema_version": "BusinessOutcomeStatusV2",
            "delivery_status": delivery_status,
            "execution_readiness": "MANUAL_LIVE_READY" if decision["account_execution"] == "CASH_READY" else "NO_DEPLOY",
            "short_profit_status": {"paper": regression_status, "live": live_profit_status},
            "long_path_status": {"status": long_path_status},
            "business_outcome_status": "BUSINESS_READY" if business_ready else "EVIDENCE_PENDING",
        },
        "live_orders_enabled": False,
    }
    return validate_outcome_status(status)


def validate_outcome_status(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") != "InvestmentOutcomeStatusV2":
        raise InvestmentContractError("InvestmentOutcomeStatusV2_required")
    _require(data, ["as_of", "delivery_status", "account_execution", "live_profit_status", "long_path_status", "regression_status", "business_outcome_status", "all_blockers", "unique_blocker", "unique_next_step", "v2_compat", "live_orders_enabled"], "outcome")
    if data["account_execution"] not in ACCOUNT_EXECUTION_VALUES or data["live_profit_status"] not in LIVE_PROFIT_VALUES or data["long_path_status"] not in LONG_PATH_VALUES:
        raise InvestmentContractError("outcome:axis_value_invalid")
    if data["regression_status"].get("internal_only") is not True or data["regression_status"].get("can_promote_live_profit") is not False:
        raise InvestmentContractError("outcome:regression_must_remain_internal")
    if data["live_orders_enabled"] is not False:
        raise InvestmentContractError("outcome:live_orders_must_be_false")
    parse_time(data["as_of"])
    return data


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _binding_object(data: dict[str, Any]) -> dict[str, str]:
    return {field: str(data.get(field) or "") for field in BINDING_FIELDS}


def _account_execution(account: dict[str, Any]) -> tuple[str, float]:
    cash = account.get("deployable_cash")
    if isinstance(cash, bool) or not isinstance(cash, (int, float)) or cash <= 0:
        return "NO_DEPLOY_CASH", 0.0
    if account.get("settled") is not True:
        return "SETTLEMENT_BLOCKED", 0.0
    amount = min(float(cash), float(account.get("max_manual_amount", cash)))
    return "CASH_READY", round(amount, 2)


def _scanner_signal(scanner: dict[str, Any], symbol: str) -> dict[str, Any]:
    return next(
        (
            item
            for item in scanner.get("signals", [])
            if isinstance(item, dict) and item.get("symbol") == symbol
        ),
        {},
    )


def _scanner_price(scanner: dict[str, Any], symbol: str, signal: dict[str, Any]) -> float | None:
    price = _finite_number(signal.get("mid")) or _finite_number(signal.get("current_price"))
    if price is not None and price > 0:
        return price
    for item in scanner.get("discovery_top3", []):
        if isinstance(item, dict) and item.get("symbol") == symbol:
            price = _finite_number(item.get("current_price"))
            return price if price is not None and price > 0 else None
    return None


def _plan_reward_risk(plan: dict[str, Any] | None, current_price: float) -> float | None:
    if not isinstance(plan, dict):
        return None
    target = _finite_number(plan.get("target_1"))
    stop = _finite_number(plan.get("stop_price"))
    if target is None or stop is None or not (stop < current_price < target):
        return None
    risk = current_price - stop
    return (target - current_price) / risk if risk > 0 else None


def _research_binding_failures(
    research: dict[str, Any], binding: dict[str, str]
) -> list[str]:
    supplied = research.get("binding")
    if not isinstance(supplied, dict):
        return ["research_binding_missing"]
    return [
        f"research_binding_mismatch:{field}"
        for field in BINDING_FIELDS
        if str(supplied.get(field) or "") != binding[field]
    ]


def _tactical_research_record(
    *,
    scanner: dict[str, Any],
    row: dict[str, Any],
    research: dict[str, Any],
    binding: dict[str, str],
    decided_at: dt.datetime,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    symbol = str(row.get("symbol") or "")
    if not symbol:
        raise InvestmentContractError("scanner_history:symbol_required")
    signal = _scanner_signal(scanner, symbol)
    history = signal.get("historical_comparison") or {}
    current_price = _scanner_price(scanner, symbol, signal)
    if current_price is None:
        raise InvestmentContractError(f"scanner_candidate:current_price_required:{symbol}")

    failures = _research_binding_failures(research, binding)
    evidence_ids = list(
        dict.fromkeys(
            [
                *[str(item) for item in row.get("evidence_ids", []) if item],
                *[str(item) for item in research.get("evidence_ids", []) if item],
            ]
        )
    )
    if not evidence_ids:
        failures.append("candidate_evidence_missing")

    conservative_ev = _finite_number(row.get("conservative_expected_value_pct"))
    expected_return = _finite_number(row.get("expected_return_pct"))
    max_drawdown = _finite_number(row.get("max_drawdown_pct"))
    drawdown_abs = abs(max_drawdown) if max_drawdown is not None else None
    profit_factor = _finite_number(row.get("profit_factor"))
    sample_size = row.get("sample_size")
    walk_positive = row.get("walk_forward_positive_windows")
    walk_total = row.get("walk_forward_total_windows")
    reward_risk = _finite_number(row.get("reward_risk_ratio"))
    if conservative_ev is None or conservative_ev <= 0:
        failures.append("non_positive_conservative_ev")
    if isinstance(sample_size, bool) or not isinstance(sample_size, int) or sample_size < 30:
        failures.append("regression_sample_below_30")
    if history.get("untouched_holdout") is not True:
        failures.append("untouched_holdout_missing")
    if history.get("lookahead_free") is not True:
        failures.append("lookahead_free_evidence_missing")
    if _finite_number(history.get("friction_pct")) is None:
        failures.append("fees_and_slippage_evidence_missing")
    if profit_factor is None or profit_factor <= 1:
        failures.append("profit_factor_not_above_one")
    if (
        isinstance(walk_positive, bool)
        or not isinstance(walk_positive, int)
        or isinstance(walk_total, bool)
        or not isinstance(walk_total, int)
        or walk_total < 3
        or walk_positive < 3
    ):
        failures.append("walk_forward_stability_failed")
    if drawdown_abs is None or drawdown_abs > 15:
        failures.append("max_drawdown_above_15pct")
    if reward_risk is None or reward_risk < 2:
        failures.append("historical_reward_risk_below_two")

    fair = research.get("fair_value")
    discount_pct = None
    if not isinstance(fair, dict):
        failures.append("fair_value_missing")
    else:
        base = _finite_number(fair.get("base"))
        low = _finite_number(fair.get("low"))
        high = _finite_number(fair.get("high"))
        if (
            low is None
            or base is None
            or high is None
            or not (0 < low <= base <= high)
        ):
            failures.append("fair_value_interval_invalid")
        else:
            discount_pct = (base / current_price - 1) * 100
            if discount_pct <= 0:
                failures.append("not_below_base_fair_value")

    catalyst = research.get("catalyst")
    catalyst_certainty = 0.0
    if not isinstance(catalyst, dict) or catalyst.get("verified") is not True:
        failures.append("verified_1_7d_catalyst_missing")
    else:
        catalyst_certainty = _finite_number(catalyst.get("time_certainty")) or 0.0
        try:
            realization = parse_time(catalyst.get("realization_by"))
            if not (decided_at < realization <= decided_at + dt.timedelta(days=7)):
                failures.append("catalyst_outside_1_7d")
        except InvestmentContractError:
            failures.append("catalyst_time_invalid")

    if research.get("liquidity_status") != "verified":
        failures.append("liquidity_not_verified")
    if research.get("data_quality_status") != "verified":
        failures.append("data_quality_not_verified")
    if research.get("risk_gate_pass") is not True:
        failures.append("risk_gate_failed")
    if research.get("source_failures"):
        failures.append("candidate_source_failure")

    plan = research.get("plan")
    plan_reward_risk = _plan_reward_risk(plan, current_price)
    if not isinstance(plan, dict):
        failures.append("current_tactical_plan_missing")
    elif plan_reward_risk is None or plan_reward_risk < 2:
        failures.append("current_plan_reward_risk_below_two")
    if isinstance(plan, dict):
        max_loss = _finite_number(plan.get("max_allowed_loss_pct"))
        if max_loss is None or max_loss <= 0 or max_loss > 0.5:
            failures.append("max_allowed_loss_above_policy")

    signal_as_of = research.get("signal_as_of")
    try:
        signal_age = (decided_at - parse_time(signal_as_of)).total_seconds()
        if signal_age < 0 or signal_age > 60:
            failures.append("current_signal_stale")
    except InvestmentContractError:
        failures.append("current_signal_time_invalid")

    if not isinstance(research.get("fundamentals"), dict):
        failures.append("crypto_fundamentals_missing")
    if not isinstance(research.get("downside"), dict):
        failures.append("downside_case_missing")

    case = None
    regression = None
    live = research.get("live_evidence") if isinstance(research.get("live_evidence"), dict) else None
    regression_id = f"reg-live-{digest([binding, symbol, row])[:16]}"
    if not failures:
        case = {
            "schema_version": "UniversalInvestmentCaseV1",
            "case_id": f"case-live-{digest([binding, symbol, research])[:16]}",
            **binding,
            "asset_class": "crypto",
            "horizon": "tactical_1_7d",
            "symbol": symbol,
            "as_of": signal_as_of,
            "current_price": current_price,
            "fair_value": copy.deepcopy(fair),
            "catalyst": copy.deepcopy(catalyst),
            "downside": copy.deepcopy(research["downside"]),
            "fundamentals": copy.deepcopy(research["fundamentals"]),
            "plan": copy.deepcopy(plan),
            "regression_evidence_id": regression_id,
            "evidence_ids": evidence_ids,
            "liquidity_status": research["liquidity_status"],
            "data_quality_status": research["data_quality_status"],
            "risk_gate_pass": research["risk_gate_pass"],
            "source_failures": list(research.get("source_failures") or []),
            "current_signal_complete": research.get("current_signal_complete") is True,
            "live_orders_enabled": False,
        }
        regression = {
            "schema_version": "RegressionEvidenceV1",
            "regression_id": regression_id,
            **binding,
            "symbol": symbol,
            "horizon": "tactical_1_7d",
            "mode": "historical_walkforward",
            "as_of": signal_as_of,
            "sample_size": sample_size,
            "conservative_ev_pct": conservative_ev,
            "max_drawdown_pct": max_drawdown,
            "holdout_pass": history.get("untouched_holdout") is True,
            "no_lookahead": history.get("lookahead_free") is True,
            "fees_included": _finite_number(history.get("friction_pct")) is not None,
            "paper_live_separated": True,
            "formal_action_eligible": False,
        }
        try:
            validate_universal_case(case)
            validate_regression_evidence(regression)
        except InvestmentContractError as exc:
            failures.append(f"production_case_contract_invalid:{exc}")
            case = None
            regression = None

    ev_to_drawdown = (
        conservative_ev / drawdown_abs
        if conservative_ev is not None and drawdown_abs not in {None, 0}
        else None
    )
    record = {
        "symbol": symbol,
        "scanner_rank": row.get("rank"),
        "current_price": current_price,
        "price_as_of": signal_as_of or scanner.get("captured_at"),
        "setup_quality_score": _finite_number(row.get("setup_quality_score")),
        "conservative_ev_pct": conservative_ev,
        "expected_return_pct": expected_return,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown,
        "ev_to_drawdown": round(ev_to_drawdown, 9) if ev_to_drawdown is not None else None,
        "sample_size": sample_size,
        "walk_forward_positive_windows": walk_positive,
        "walk_forward_total_windows": walk_total,
        "value_discount_pct": round(discount_pct, 6) if discount_pct is not None else None,
        "catalyst_time_certainty": catalyst_certainty,
        "current_signal_complete": research.get("current_signal_complete") is True,
        "liquidity_status": research.get("liquidity_status") or "missing",
        "data_quality_status": research.get("data_quality_status") or "missing",
        "eligible": not failures,
        "hard_gate_failures": list(dict.fromkeys(failures)),
        "evidence_ids": evidence_ids,
    }
    return record, case, regression, live


def build_tactical_research_ranking(
    *,
    scanner: dict[str, Any],
    research_by_symbol: dict[str, Any],
    decided_at: str,
) -> tuple[dict[str, Any], dict[str, tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]]]:
    if scanner.get("request_mode") != "tactical_1_7d":
        raise InvestmentContractError("production:scanner_request_mode_must_be_tactical_1_7d")
    if scanner.get("live_orders_enabled") is not False or scanner.get("private_api_used") is not False:
        raise InvestmentContractError("production:scanner_safety_boundary_violated")
    _validate_binding(scanner, "production_scanner")
    parse_time(scanner.get("captured_at"))
    history = scanner.get("ranked_historical_comparison") or {}
    rows = history.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise InvestmentContractError("production:scanner_ranked_history_required")
    if not isinstance(research_by_symbol, dict):
        raise InvestmentContractError("production:research_by_symbol_object_required")
    binding = _binding_object(scanner)
    now = parse_time(decided_at)
    records: list[dict[str, Any]] = []
    prepared: dict[str, tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]] = {}
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise InvestmentContractError("production:scanner_history_row_object_required")
        symbol = str(raw.get("symbol") or "")
        if symbol in seen:
            raise InvestmentContractError(f"production:duplicate_candidate:{symbol}")
        seen.add(symbol)
        research = research_by_symbol.get(symbol)
        if not isinstance(research, dict):
            research = {}
        record, case, regression, live = _tactical_research_record(
            scanner=scanner,
            row=raw,
            research=research,
            binding=binding,
            decided_at=now,
        )
        records.append(record)
        prepared[symbol] = (case, regression, live)

    def ranking_key(item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            int(item["eligible"]),
            item["conservative_ev_pct"] if item["conservative_ev_pct"] is not None else -1e12,
            item["ev_to_drawdown"] if item["ev_to_drawdown"] is not None else -1e12,
            item["value_discount_pct"] if item["value_discount_pct"] is not None else -1e12,
            item["catalyst_time_certainty"],
            item["sample_size"] if isinstance(item["sample_size"], int) else -1,
            item["setup_quality_score"] if item["setup_quality_score"] is not None else -1e12,
            item["symbol"],
        )

    records.sort(key=ranking_key, reverse=True)
    for index, item in enumerate(records, start=1):
        item["rank"] = index
    ranking = {
        "schema_version": "TacticalResearchRankingV1",
        **binding,
        "ranking_id": f"tactical-live-rank-{digest([binding, records])[:16]}",
        "ranked_at": decided_at,
        "ranking_order": [
            "hard_gate_eligibility",
            "conservative_net_ev",
            "ev_to_drawdown",
            "value_discount",
            "catalyst_time_certainty",
            "sample_quality",
            "setup_quality_tiebreaker",
        ],
        "ranked_candidates": records,
        "research_top1": records[0]["symbol"],
        "qualified_alternative_symbols": [
            item["symbol"] for item in records[1:3] if item["eligible"]
        ],
    }
    return ranking, prepared


def _no_trade_live_decision(
    *,
    ranking: dict[str, Any],
    account: dict[str, Any],
    decided_at: str,
    reasons: list[str],
) -> dict[str, Any]:
    top = ranking["ranked_candidates"][0]
    account_status, _ = _account_execution(account)
    decision = {
        "schema_version": "LiveInvestmentDecisionV1",
        "decision_id": f"live-decision-{digest([ranking, account, decided_at, reasons])[:16]}",
        **{field: ranking[field] for field in BINDING_FIELDS},
        "decided_at": decided_at,
        "research_top1": ranking["research_top1"],
        "ranking_id": ranking["ranking_id"],
        "current_action": "NO_TRADE",
        "no_trade_reasons": list(dict.fromkeys(reasons)),
        "decision_price": top.get("current_price"),
        "price_as_of": top.get("price_as_of"),
        "decision_card": None,
        "account_execution": account_status,
        "executable_amount": 0.0,
        "regression_evidence_ids": [],
        "paper_action_exposed": False,
        "live_orders_enabled": False,
        "ranking": ranking,
    }
    return validate_live_decision(decision)


def run_production_input(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema_version") != PRODUCTION_INPUT_SCHEMA:
        raise InvestmentContractError(f"{PRODUCTION_INPUT_SCHEMA}_required")
    _require(
        data,
        [
            "decided_at",
            "scanner_result",
            "research_by_symbol",
            "account",
            "delivery_status",
            "live_profit_status",
            "long_path_status",
            "regression_status",
        ],
        "production_input",
    )
    parse_time(data["decided_at"])
    if not isinstance(data["account"], dict):
        raise InvestmentContractError("production_input:account_object_required")
    ranking, prepared = build_tactical_research_ranking(
        scanner=data["scanner_result"],
        research_by_symbol=data["research_by_symbol"],
        decided_at=data["decided_at"],
    )
    top = ranking["ranked_candidates"][0]
    case, regression, live = prepared[ranking["research_top1"]]
    if not top["eligible"] or case is None or regression is None:
        decision = _no_trade_live_decision(
            ranking=ranking,
            account=data["account"],
            decided_at=data["decided_at"],
            reasons=top["hard_gate_failures"] or ["qualified_case_missing"],
        )
    else:
        try:
            decision = build_live_decision(
                cases=[case],
                regressions=[regression],
                live_evidence_by_symbol={case["symbol"]: live} if live else {},
                account=data["account"],
                decided_at=data["decided_at"],
            )
            decision["ranking_id"] = ranking["ranking_id"]
            decision["research_top1"] = ranking["research_top1"]
            decision["ranking"] = ranking
            decision["decision_id"] = f"live-decision-{digest([ranking, live, data['account'], data['decided_at'], decision['current_action']])[:16]}"
            validate_live_decision(decision)
        except InvestmentContractError as exc:
            decision = _no_trade_live_decision(
                ranking=ranking,
                account=data["account"],
                decided_at=data["decided_at"],
                reasons=[f"live_evidence_contract_invalid:{exc}"],
            )

    status = build_investment_outcome_status(
        delivery_status=data["delivery_status"],
        decision=decision,
        live_profit_status=data["live_profit_status"],
        long_path_status=data["long_path_status"],
        regression_status=data["regression_status"],
        as_of=data["decided_at"],
    )
    return {
        "schema_version": PRODUCTION_OUTPUT_SCHEMA,
        "generated_at": data["decided_at"],
        "input_digest": digest(data),
        **{field: ranking[field] for field in BINDING_FIELDS},
        "research_ranking": ranking,
        "decision": decision,
        "status": status,
        "paper_live_separated": True,
        "human_confirmation_required": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def _atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _fixture_binding(name: str) -> dict[str, str]:
    return {
        "snapshot_id": f"shadow-{name}-snapshot",
        "strategy_version": "universal-investment-core-v3",
        "config_digest": digest({"fixture": name, "type": "config"}),
        "source_digest": digest({"fixture": name, "type": "source"}),
    }


def _shadow_case(name: str, symbol: str, asset_class: str, horizon: str, *, price: float, fair: float, entry: tuple[float, float], source_failure: bool = False) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    binding = _fixture_binding(name)
    as_of = "2030-01-01T00:00:00Z"
    fundamentals = ({
        "adoption": "measured", "real_fees": "positive", "value_capture": "verified", "supply_dilution": "bounded",
        "staking_net_yield": "measured", "liquidity": "verified", "security": "reviewed", "regulation": "scenario_reviewed",
    } if asset_class == "crypto" else {
        "revenue": "measured", "cash_flow": "measured", "earnings_quality": "reviewed", "balance_sheet": "reviewed",
        "valuation": "range", "moat": "reviewed", "company_catalyst": "verified", "industry_catalyst": "verified",
    })
    plan = ({
        "entry_low": entry[0], "entry_high": entry[1], "entry_trigger": "same-snapshot trigger",
        "valid_until": "2030-01-01T00:05:00Z", "target_1": price * 1.1, "target_2": price * 1.2,
        "stop_price": entry[0] * 0.95, "latest_exit_at": "2030-01-07T00:00:00Z", "max_allowed_loss_pct": 0.5,
    } if horizon == "tactical_1_7d" else {
        "buy_low": entry[0], "buy_high": entry[1], "tranches": [40, 30, 30], "wait_condition": "enter valuation range",
        "review_at": "2030-04-01T00:00:00Z", "thesis_invalidation": ["value capture structurally fails"],
    })
    case = {
        "schema_version": "UniversalInvestmentCaseV1", "case_id": f"case-{name}", **binding,
        "asset_class": asset_class, "horizon": horizon, "symbol": symbol, "as_of": as_of, "current_price": price,
        "fair_value": {"low": fair * 0.9, "base": fair, "high": fair * 1.1, "method": "shadow multi-factor", "uncertainty": "high"},
        "catalyst": {"summary": "shadow verified catalyst", "verified": True, "realization_by": "2030-01-07T00:00:00Z", "time_certainty": 0.8},
        "downside": {"expected_drawdown_pct": 5.0, "invalidation_conditions": ["catalyst invalidated"]},
        "fundamentals": fundamentals, "plan": plan, "regression_evidence_id": f"reg-{name}", "evidence_ids": [f"evidence-{name}"],
        "liquidity_status": "verified", "data_quality_status": "verified", "risk_gate_pass": True,
        "source_failures": ["shadow source failure"] if source_failure else [], "live_orders_enabled": False,
    }
    regression = {
        "schema_version": "RegressionEvidenceV1", "regression_id": f"reg-{name}", **binding, "symbol": symbol, "horizon": horizon,
        "mode": "historical_walkforward", "as_of": as_of, "sample_size": 40, "conservative_ev_pct": 6.0,
        "max_drawdown_pct": -10.0, "holdout_pass": True, "no_lookahead": True, "fees_included": True,
        "paper_live_separated": True, "formal_action_eligible": False,
    }
    live_plan = {
        **copy.deepcopy(plan),
        **binding,
        "symbol": symbol,
        "generated_at": "2030-01-01T00:00:30Z",
    }
    live = {
        **binding, "symbol": symbol, "certified_at": "2030-01-01T00:00:30Z", "scan_duration_seconds": 20,
        "quotes": [
            {"source": "public-a", "public": True, "price": price, "as_of": "2030-01-01T00:00:20Z", "latency_seconds": 2},
            {"source": "public-b", "public": True, "price": price * 1.002, "as_of": "2030-01-01T00:00:25Z", "latency_seconds": 3},
        ],
        "current_plan": live_plan, "current_plan_digest": digest(live_plan),
    }
    return case, regression, live


def run_shadow_suite(output_dir: Path | None = None) -> dict[str, Any]:
    tasks: dict[str, dict[str, Any]] = {}
    fixtures = [
        ("crypto-enter", "BTC", "crypto", "tactical_1_7d", 100.0, 130.0, (99.0, 101.0), "user_confirmed"),
        ("crypto-no-trade", "ETH", "crypto", "tactical_1_7d", 100.0, 125.0, (99.0, 101.0), "user_confirmed"),
        ("equity-wait", "APLD", "us_equity", "tactical_1_7d", 100.0, 140.0, (90.0, 95.0), "user_confirmed"),
        ("long-data-degraded", "COIN", "us_equity", "longterm", 100.0, 180.0, (90.0, 110.0), "legacy_estimate"),
    ]
    for name, symbol, asset, horizon, price, fair, entry, authority in fixtures:
        case, regression, live = _shadow_case(name, symbol, asset, horizon, price=price, fair=fair, entry=entry)
        if name == "crypto-no-trade":
            live["quotes"][0]["as_of"] = "2029-12-31T23:00:00Z"
        account = {"authority": authority, "deployable_cash": 0.0, "settled": True, "max_manual_amount": 0.0}
        decision = build_live_decision(cases=[case], regressions=[regression], live_evidence_by_symbol={symbol: live}, account=account, decided_at="2030-01-01T00:00:40Z")
        long_status = "DATA_DEGRADED" if authority == "legacy_estimate" else "BASELINE_DEFINED"
        status = build_investment_outcome_status(delivery_status="RUNTIME_VERIFIED", decision=decision, live_profit_status="UNMEASURED", long_path_status=long_status, regression_status="PASS", as_of="2030-01-01T00:00:40Z")
        tasks[name] = {"case": case, "regression": regression, "decision": decision, "status": status}

    recovery_case, recovery_reg, recovery_live = _shadow_case("fault-recovery", "SOL", "crypto", "tactical_1_7d", price=100, fair=135, entry=(99, 101))
    stale_live = copy.deepcopy(recovery_live)
    stale_live["quotes"][0]["as_of"] = "2029-12-31T23:00:00Z"
    stale = build_live_decision(cases=[recovery_case], regressions=[recovery_reg], live_evidence_by_symbol={"SOL": stale_live}, account={"authority": "user_confirmed", "deployable_cash": 0, "settled": True}, decided_at="2030-01-01T00:00:40Z")
    recovered = build_live_decision(cases=[recovery_case], regressions=[recovery_reg], live_evidence_by_symbol={"SOL": recovery_live}, account={"authority": "user_confirmed", "deployable_cash": 0, "settled": True}, decided_at="2030-01-01T00:00:40Z")
    recovery = {"stale_action": stale["current_action"], "recovered_action": recovered["current_action"], "stale_decision_id": stale["decision_id"], "recovered_decision_id": recovered["decision_id"]}
    package = {
        "schema_version": "UniversalInvestmentShadowSuiteV1",
        "generated_at": "2030-01-01T00:00:40Z",
        "tasks": tasks,
        "fault_recovery": recovery,
        "checks": {
            "crypto_enter": tasks["crypto-enter"]["decision"]["current_action"] == "ENTER_NOW",
            "crypto_no_trade": tasks["crypto-no-trade"]["decision"]["current_action"] == "NO_TRADE",
            "equity_wait": tasks["equity-wait"]["decision"]["current_action"] == "WAIT_FOR_ENTRY",
            "long_data_degraded": tasks["long-data-degraded"]["status"]["long_path_status"] == "DATA_DEGRADED",
            "fault_recovery": recovery == {**recovery, "stale_action": "NO_TRADE", "recovered_action": "ENTER_NOW"},
            "paper_never_user_action": all(task["decision"]["current_action"] in ACTION_VALUES for task in tasks.values()),
            "no_live_orders": all(task["decision"]["live_orders_enabled"] is False for task in tasks.values()),
        },
    }
    package["passed"] = all(package["checks"].values())
    if output_dir is not None:
        _atomic_write(output_dir / "universal_investment_shadow_suite_v1.json", package)
    return package


def main() -> int:
    parser = argparse.ArgumentParser(description="Universal investment analysis kernel")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--shadow-suite")
    parser.add_argument("--input", help="UniversalInvestmentRunInputV1 JSON path, or - for stdin")
    parser.add_argument("--output", help="Optional output path for UniversalInvestmentRunResultV1")
    args = parser.parse_args()
    if args.input:
        payload = json.load(sys.stdin) if args.input == "-" else json.loads(
            Path(args.input).expanduser().resolve().read_text(encoding="utf-8")
        )
        package = run_production_input(payload)
        if args.output:
            _atomic_write(Path(args.output).expanduser().resolve(), package)
        print(json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.self_test or args.shadow_suite:
        output = Path(args.shadow_suite).expanduser().resolve() if args.shadow_suite else None
        package = run_shadow_suite(output)
        print(json.dumps({"passed": package["passed"], "checks": package["checks"]}, ensure_ascii=False, sort_keys=True))
        return 0 if package["passed"] else 1
    parser.error("use --input, --self-test or --shadow-suite")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
