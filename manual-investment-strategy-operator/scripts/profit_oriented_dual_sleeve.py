#!/usr/bin/env python3
"""Deterministic, no-trade profit governance for the short/long investment sleeves."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable


UTC = dt.timezone.utc
SCHEMAS = {
    "ProfitObjectiveV1",
    "ValuationCaseV1",
    "CandidateRankingV1",
    "TradePlanV1",
    "MinimalExecutionReceiptV1",
    "ProfitAttributionV1",
    "LongTermHoldingSnapshotV1",
    "LongTermReUnderwriteV1",
    "ProfitReviewV1",
    "BusinessOutcomeStatusV1",
    "PaperTacticalCapitalBaselineV1",
    "PaperExecutionReceiptV1",
    "ProfitAttributionV2",
    "BusinessOutcomeStatusV2",
}


class ContractError(ValueError):
    pass


def now_iso() -> str:
    return dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ContractError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(UTC)


def _required(data: dict[str, Any], fields: Iterable[str]) -> None:
    missing = [field for field in fields if data.get(field) is None]
    if missing:
        raise ContractError(f"missing required fields: {', '.join(missing)}")


def _positive(data: dict[str, Any], fields: Iterable[str], allow_zero: bool = False) -> None:
    for field in fields:
        value = data.get(field)
        if not isinstance(value, (int, float)) or (value < 0 if allow_zero else value <= 0):
            raise ContractError(f"{field} must be {'non-negative' if allow_zero else 'positive'}")


def _digest(data: Any) -> str:
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def validate_contract(data: dict[str, Any]) -> dict[str, Any]:
    schema = data.get("schema_version")
    if schema not in SCHEMAS:
        raise ContractError(f"unsupported schema_version: {schema}")
    if schema == "ProfitObjectiveV1":
        _required(data, ["objective_id", "sleeve", "principal_policy", "target_return_pct", "horizon", "return_formula", "risk_boundaries", "frozen_at"])
        if data["sleeve"] not in {"short_tactical", "long_compound"}:
            raise ContractError("invalid sleeve")
        parse_time(data["frozen_at"])
    elif schema == "ValuationCaseV1":
        _required(data, ["valuation_case_id", "symbol", "as_of", "current_price", "fair_value_low", "fair_value_base", "fair_value_high", "method", "evidence_ids", "invalidation_conditions"])
        _positive(data, ["current_price", "fair_value_low", "fair_value_base", "fair_value_high"])
        if not data["fair_value_low"] <= data["fair_value_base"] <= data["fair_value_high"]:
            raise ContractError("fair value interval is not ordered")
        parse_time(data["as_of"])
    elif schema == "CandidateRankingV1":
        _required(data, ["ranking_id", "snapshot_id", "strategy_version", "ranked_at", "candidates", "unique_top1"])
        parse_time(data["ranked_at"])
        if not data["candidates"] or data["unique_top1"] != data["candidates"][0]["symbol"]:
            raise ContractError("unique_top1 must equal ranked candidate 1")
    elif schema == "TradePlanV1":
        _required(data, ["trade_plan_id", "ranking_id", "symbol", "action", "current_price", "price_as_of", "entry_low", "entry_high", "entry_trigger", "valid_until", "target_1", "target_2", "stop_price", "latest_exit_at", "max_allowed_loss_pct", "invalidation_conditions", "evidence_ids"])
        if data["action"] not in {"ENTER_NOW", "WAIT_FOR_ENTRY"}:
            raise ContractError("TradePlan action must be ENTER_NOW or WAIT_FOR_ENTRY")
        _positive(data, ["current_price", "entry_low", "entry_high", "target_1", "target_2", "stop_price", "max_allowed_loss_pct"])
        if not data["entry_low"] <= data["entry_high"]:
            raise ContractError("entry range is not ordered")
        for key in ["price_as_of", "valid_until", "latest_exit_at"]:
            parse_time(data[key])
    elif schema == "MinimalExecutionReceiptV1":
        _required(data, ["receipt_id", "trade_id", "recommendation_id", "symbol", "rail", "event", "quantity", "price", "fee_usd", "slippage_usd", "executed_at", "human_confirmed"])
        if data["rail"] != "crypto" or data["event"] not in {"entry", "exit"}:
            raise ContractError("minimal receipt only supports crypto entry/exit")
        if data["human_confirmed"] is not True:
            raise ContractError("receipt must be explicitly human-confirmed")
        _positive(data, ["quantity", "price"])
        _positive(data, ["fee_usd", "slippage_usd"], allow_zero=True)
        parse_time(data["executed_at"])
    elif schema == "ProfitAttributionV1":
        _required(data, ["as_of", "period_start", "period_end", "realized_profit_usd", "ending_open_position_change_usd", "fees_and_slippage_usd", "net_profit_usd", "status", "trade_results"])
        for key in ["as_of", "period_start", "period_end"]:
            parse_time(data[key])
        if data["opening_tactical_capital_usd"] is None and data["monthly_money_weighted_net_roi_pct"] is not None:
            raise ContractError("monthly ROI must be null without opening tactical capital")
    elif schema == "LongTermHoldingSnapshotV1":
        _required(data, ["snapshot_id", "as_of", "source", "human_confirmed", "holdings", "cash_rails", "baseline_value_usd"])
        if data["human_confirmed"] is not True:
            raise ContractError("long-term snapshot must be human-confirmed")
        _positive(data, ["baseline_value_usd"])
        for holding in data["holdings"]:
            _required(holding, ["symbol", "quantity", "cost_basis_usd", "staking_quantity"])
            _positive(holding, ["quantity", "cost_basis_usd", "staking_quantity"], allow_zero=True)
        parse_time(data["as_of"])
    elif schema == "LongTermReUnderwriteV1":
        _required(data, ["reunderwrite_id", "short_trade_id", "reviewed_at", "independent_from_short_thesis", "longterm_thesis_pass", "valuation_pass", "liquidity_pass", "concentration_pass", "user_confirmed", "decision", "evidence_ids"])
        parse_time(data["reviewed_at"])
        allowed = all(data[key] is True for key in ["independent_from_short_thesis", "longterm_thesis_pass", "valuation_pass", "liquidity_pass", "concentration_pass", "user_confirmed"])
        expected = "ALLOW_LONG_TERM_TRANSFER" if allowed else "KEEP_ORIGINAL_SHORT_EXIT"
        if data["decision"] != expected:
            raise ContractError(f"re-underwrite decision must be {expected}")
    elif schema == "ProfitReviewV1":
        _required(data, ["review_id", "reviewed_at", "sleeve", "change_type", "metric", "baseline", "single_rule_change", "expected_profit_effect", "required_sample", "failure_condition", "rollback_version", "approved"])
        if data["change_type"] not in {"profit_rule", "safety_pause"}:
            raise ContractError("change_type must be profit_rule or safety_pause")
        parse_time(data["reviewed_at"])
    elif schema == "BusinessOutcomeStatusV1":
        _required(data, ["as_of", "delivery_status", "short_profit_status", "long_path_status", "unique_blocker", "unique_next_step"])
        parse_time(data["as_of"])
    elif schema == "PaperTacticalCapitalBaselineV1":
        _required(data, ["baseline_id", "month_id", "request_mode", "evidence_mode", "initial_capital_usd", "strategy_version", "opened_at", "live_orders_enabled"])
        if data["request_mode"] != "tactical_1_7d" or data["evidence_mode"] != "paper":
            raise ContractError("paper baseline must be tactical_1_7d and paper-only")
        if data["live_orders_enabled"] is not False:
            raise ContractError("paper baseline cannot enable live orders")
        _positive(data, ["initial_capital_usd"])
        parse_time(data["opened_at"])
    elif schema == "PaperExecutionReceiptV1":
        _required(data, ["receipt_id", "baseline_id", "trade_id", "symbol", "request_mode", "evidence_mode", "strategy_version", "snapshot_id", "event", "quantity", "price", "fee_usd", "slippage_usd", "executed_at", "live_orders_enabled"])
        if data["request_mode"] != "tactical_1_7d" or data["evidence_mode"] != "paper":
            raise ContractError("paper receipt must be tactical_1_7d and paper-only")
        if data["event"] not in {"entry", "exit"}:
            raise ContractError("paper receipt event must be entry or exit")
        if data["live_orders_enabled"] is not False:
            raise ContractError("paper receipt cannot enable live orders")
        _positive(data, ["quantity", "price"])
        _positive(data, ["fee_usd", "slippage_usd"], allow_zero=True)
        parse_time(data["executed_at"])
    elif schema == "ProfitAttributionV2":
        _required(data, ["evidence_mode", "request_mode", "as_of", "period_start", "period_end", "opening_capital_usd", "net_profit_usd", "money_weighted_net_roi_pct", "status", "trade_results"])
        if data["evidence_mode"] not in {"paper", "live"}:
            raise ContractError("profit attribution evidence_mode must be paper or live")
        if data["request_mode"] != "tactical_1_7d":
            raise ContractError("profit attribution v2 only supports tactical_1_7d")
        _positive(data, ["opening_capital_usd"])
        for key in ["as_of", "period_start", "period_end"]:
            parse_time(data[key])
        if data["evidence_mode"] == "paper" and data.get("business_ready_eligible") is not False:
            raise ContractError("paper attribution cannot be business-ready eligible")
    elif schema == "BusinessOutcomeStatusV2":
        _required(data, ["as_of", "delivery_status", "execution_readiness", "short_profit_status", "long_path_status", "business_outcome_status", "all_blockers", "unique_blocker", "unique_next_step", "v1_compat"])
        parse_time(data["as_of"])
        if data["execution_readiness"] not in {"PAPER_ONLY_READY", "MANUAL_LIVE_READY", "NO_DEPLOY"}:
            raise ContractError("unsupported execution_readiness")
        if data["business_outcome_status"] not in {"EVIDENCE_PENDING", "BUSINESS_READY"}:
            raise ContractError("unsupported business_outcome_status")
        if not isinstance(data["short_profit_status"], dict) or not isinstance(data["long_path_status"], dict):
            raise ContractError("v2 short and long statuses must be objects")
        if not isinstance(data["all_blockers"], list):
            raise ContractError("all_blockers must be an array")
    return data


def rank_candidates(snapshot_id: str, strategy_version: str, candidates: list[dict[str, Any]], ranked_at: str | None = None) -> dict[str, Any]:
    if not candidates:
        raise ContractError("at least one evidence-backed candidate is required")
    normalized = []
    for raw in candidates:
        item = copy.deepcopy(raw)
        _required(item, ["symbol", "request_mode", "current_price", "price_as_of", "fair_value_base", "conservative_ev_pct", "expected_drawdown_pct", "catalyst_certainty", "catalyst_verified", "liquidity_status", "data_quality_status", "sample_size", "probability_tier", "entry_plan", "evidence_ids"])
        parse_time(item["price_as_of"])
        if not isinstance(item["evidence_ids"], list) or not item["evidence_ids"]:
            raise ContractError("price and ranking evidence_ids must be non-empty; TradePlan BLOCKED")
        _positive(item, ["current_price", "fair_value_base", "expected_drawdown_pct"])
        item["value_discount_pct"] = round((item["fair_value_base"] / item["current_price"] - 1) * 100, 6)
        item["ev_to_drawdown"] = round(item["conservative_ev_pct"] / item["expected_drawdown_pct"], 6)
        blockers = list(item.get("risk_blockers", []))
        item["eligible"] = all([
            item["request_mode"] == "tactical_1_7d",
            item["value_discount_pct"] > 0,
            item["catalyst_verified"] is True,
            item["liquidity_status"] == "verified",
            item["data_quality_status"] == "verified",
            item["conservative_ev_pct"] > 0,
            item["ev_to_drawdown"] >= 2.0,
            not blockers,
        ])
        normalized.append(item)
    normalized.sort(key=lambda x: (
        int(x["eligible"]), x["conservative_ev_pct"], x["ev_to_drawdown"],
        x["value_discount_pct"], x["catalyst_certainty"], x["sample_size"],
    ), reverse=True)
    result = {
        "schema_version": "CandidateRankingV1",
        "ranking_id": f"rank-{_digest([snapshot_id, strategy_version, normalized])[:16]}",
        "snapshot_id": snapshot_id,
        "strategy_version": strategy_version,
        "ranked_at": ranked_at or now_iso(),
        "ranking_order": ["conservative_net_profit", "ev_to_drawdown", "value_discount", "catalyst_certainty", "sample_quality", "liquidity"],
        "candidates": normalized,
        "unique_top1": normalized[0]["symbol"],
    }
    return validate_contract(result)


def build_trade_plan(ranking: dict[str, Any]) -> dict[str, Any]:
    validate_contract(ranking)
    top = ranking["candidates"][0]
    plan = top["entry_plan"]
    _required(plan, ["entry_low", "entry_high", "entry_trigger", "valid_until", "target_1", "target_2", "stop_price", "latest_exit_at"])
    signal = top.get("current_signal_complete") is True
    if top["eligible"] and signal and top["sample_size"] >= 30 and top["probability_tier"] == "calibrated" and top.get("holdout_pass") is True:
        action, direct, max_loss = "ENTER_NOW", "enter_now", 0.5
    elif top["eligible"] and signal and top["sample_size"] >= 10:
        action, direct, max_loss = "ENTER_NOW", "small_entry_now", 0.25
    else:
        action, direct, max_loss = "WAIT_FOR_ENTRY", "do_not_enter_now", min(float(top.get("suggested_max_loss_pct", 0.25)), 0.25)
    result = {
        "schema_version": "TradePlanV1",
        "trade_plan_id": f"plan-{_digest([ranking['ranking_id'], top['symbol'], action, plan])[:16]}",
        "ranking_id": ranking["ranking_id"],
        "symbol": top["symbol"],
        "action": action,
        "current_direct_decision": direct,
        "current_price": top["current_price"],
        "price_as_of": top["price_as_of"],
        "fair_value_range": top.get("fair_value_range", [top["fair_value_base"], top["fair_value_base"]]),
        "value_discount_pct": top["value_discount_pct"],
        "catalyst": top.get("catalyst_summary", ""),
        "entry_low": plan["entry_low"], "entry_high": plan["entry_high"],
        "entry_trigger": plan["entry_trigger"], "valid_until": plan["valid_until"],
        "target_1": plan["target_1"], "target_2": plan["target_2"], "stop_price": plan["stop_price"],
        "latest_exit_at": plan["latest_exit_at"],
        "expected_upside_pct": top.get("expected_upside_pct"),
        "expected_drawdown_pct": top["expected_drawdown_pct"],
        "reward_risk_ratio": top["ev_to_drawdown"],
        "confidence": {"tier": top["probability_tier"], "sample_size": top["sample_size"], "data_quality": top["data_quality_status"]},
        "max_allowed_loss_pct": max_loss,
        "invalidation_conditions": top.get("invalidation_conditions", []),
        "evidence_ids": top.get("evidence_ids", []),
    }
    return validate_contract(result)


def evaluate_market_evidence(
    *,
    snapshot_id: str,
    quotes: list[dict[str, Any]],
    as_of: str,
    scan_started_at: str,
    scan_completed_at: str,
    symbol: str | None = None,
    current_plan: dict[str, Any] | None = None,
    plan_valid_until: str | None = None,
    max_quote_age_seconds: float = 60.0,
    max_source_latency_seconds: float = 12.0,
    max_scan_duration_seconds: float = 120.0,
    max_cross_source_spread_pct: float = 1.0,
) -> dict[str, Any]:
    """Certify current public prices without converting stale history into a live plan."""

    now = parse_time(as_of)
    started = parse_time(scan_started_at)
    completed = parse_time(scan_completed_at)
    reasons: list[str] = []
    if not snapshot_id:
        reasons.append("snapshot_id_required")
    duration = (completed - started).total_seconds()
    if duration < 0 or duration > max_scan_duration_seconds:
        reasons.append("scan_duration_exceeded")
    distinct_sources: set[str] = set()
    prices: list[float] = []
    quote_times: list[str] = []
    for quote in quotes:
        source = str(quote.get("source") or "")
        price = quote.get("price")
        quote_as_of = quote.get("as_of")
        latency = quote.get("latency_seconds")
        if not source or quote.get("public") is not True:
            reasons.append("non_public_or_unnamed_source")
            continue
        distinct_sources.add(source)
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
            reasons.append(f"invalid_price:{source}")
            continue
        prices.append(float(price))
        try:
            age = (now - parse_time(str(quote_as_of))).total_seconds()
        except (ContractError, TypeError):
            reasons.append(f"invalid_quote_time:{source}")
            continue
        quote_times.append(str(quote_as_of))
        if age < 0 or age > max_quote_age_seconds:
            reasons.append(f"stale_quote:{source}")
        if isinstance(latency, bool) or not isinstance(latency, (int, float)) or latency < 0 or latency > max_source_latency_seconds:
            reasons.append(f"source_timeout:{source}")
    if len(distinct_sources) < 2 or len(prices) < 2:
        reasons.append("two_public_price_sources_required")
    spread_pct = None
    if len(prices) >= 2:
        spread_pct = (max(prices) / min(prices) - 1.0) * 100.0
        if spread_pct > max_cross_source_spread_pct:
            reasons.append("cross_source_price_conflict")
    normalized_current_plan = copy.deepcopy(current_plan) if isinstance(current_plan, dict) else None
    required_plan_fields = [
        "snapshot_id", "symbol", "generated_at", "entry_low", "entry_high",
        "entry_trigger", "valid_until", "target_1", "target_2", "stop_price",
        "latest_exit_at",
    ]
    if normalized_current_plan is None:
        reasons.append("current_plan_rebuild_required")
    else:
        missing_plan_fields = [
            field for field in required_plan_fields
            if normalized_current_plan.get(field) is None
        ]
        if missing_plan_fields:
            reasons.append("current_plan_fields_missing")
        else:
            if normalized_current_plan["snapshot_id"] != snapshot_id:
                reasons.append("current_plan_snapshot_mismatch")
            if symbol is not None and normalized_current_plan["symbol"] != symbol:
                reasons.append("current_plan_symbol_mismatch")
            try:
                generated_at = parse_time(str(normalized_current_plan["generated_at"]))
                valid_until = parse_time(str(normalized_current_plan["valid_until"]))
                latest_exit = parse_time(str(normalized_current_plan["latest_exit_at"]))
                plan_age = (now - generated_at).total_seconds()
                if plan_age < 0 or plan_age > max_quote_age_seconds:
                    reasons.append("current_plan_not_fresh")
                if valid_until <= now:
                    reasons.append("trade_plan_expired")
                if latest_exit <= now or latest_exit > now + dt.timedelta(days=7):
                    reasons.append("latest_exit_outside_tactical_horizon")
            except ContractError:
                reasons.append("invalid_current_plan_time")
            numeric_fields = ["entry_low", "entry_high", "target_1", "target_2", "stop_price"]
            if any(
                isinstance(normalized_current_plan.get(field), bool)
                or not isinstance(normalized_current_plan.get(field), (int, float))
                or normalized_current_plan[field] <= 0
                for field in numeric_fields
            ):
                reasons.append("invalid_current_plan_levels")
            elif prices:
                consensus = sum(prices) / len(prices)
                if normalized_current_plan["entry_low"] > normalized_current_plan["entry_high"]:
                    reasons.append("invalid_current_entry_range")
                if not normalized_current_plan["target_1"] > consensus:
                    reasons.append("target_1_not_above_current_price")
                if not normalized_current_plan["target_2"] > normalized_current_plan["target_1"]:
                    reasons.append("target_2_not_above_target_1")
                if not normalized_current_plan["stop_price"] < min(
                    consensus, normalized_current_plan["entry_low"]
                ):
                    reasons.append("stop_not_below_entry_and_current_price")
    if plan_valid_until is not None:
        try:
            if parse_time(plan_valid_until) <= now:
                reasons.append("trade_plan_expired")
        except ContractError:
            reasons.append("invalid_plan_expiry")
    deduped_reasons = list(dict.fromkeys(reasons))
    result = {
        "schema_version": "MarketEvidenceGateV1",
        "snapshot_id": snapshot_id,
        "symbol": symbol,
        "as_of": as_of,
        "certified_at": scan_completed_at,
        "status": "FRESH" if not deduped_reasons else "NO_FRESH_DECISION",
        "reasons": deduped_reasons,
        "source_count": len(distinct_sources),
        "scan_duration_seconds": round(duration, 6),
        "cross_source_spread_pct": round(spread_pct, 6) if spread_pct is not None else None,
        "consensus_price": round(sum(prices) / len(prices), 12) if prices else None,
        "quote_as_of_range": [min(quote_times), max(quote_times)] if quote_times else None,
        "current_plan": normalized_current_plan,
        "limits": {
            "quote_age_seconds": max_quote_age_seconds,
            "source_latency_seconds": max_source_latency_seconds,
            "scan_duration_seconds": max_scan_duration_seconds,
            "cross_source_spread_pct": max_cross_source_spread_pct,
        },
    }
    result["evidence_digest"] = _digest(result)
    return result


def build_current_trade_plan(ranking: dict[str, Any], market_evidence: dict[str, Any]) -> dict[str, Any]:
    """Return a TradePlan only when its snapshot has fresh, consistent current evidence."""

    validate_contract(ranking)
    top = ranking["candidates"][0]
    if (
        market_evidence.get("status") != "FRESH"
        or market_evidence.get("snapshot_id") != ranking.get("snapshot_id")
        or (
            market_evidence.get("symbol") is not None
            and market_evidence.get("symbol") != top.get("symbol")
        )
    ):
        reasons = list(market_evidence.get("reasons") or [])
        if market_evidence.get("snapshot_id") != ranking.get("snapshot_id"):
            reasons.append("snapshot_mismatch")
        if market_evidence.get("symbol") not in {None, top.get("symbol")}:
            reasons.append("symbol_mismatch")
        return {
            "schema_version": "CurrentDecisionGateV1",
            "status": "NO_FRESH_DECISION",
            "snapshot_id": ranking.get("snapshot_id"),
            "unique_top1": ranking.get("unique_top1"),
            "reasons": list(dict.fromkeys(reasons)),
            "trade_plan": None,
        }
    certified_price = market_evidence.get("consensus_price")
    certified_at = market_evidence.get("certified_at")
    certified_plan = market_evidence.get("current_plan")
    if not isinstance(certified_price, (int, float)) or certified_price <= 0 or not certified_at or not isinstance(certified_plan, dict):
        return {
            "schema_version": "CurrentDecisionGateV1",
            "status": "NO_FRESH_DECISION",
            "snapshot_id": ranking.get("snapshot_id"),
            "unique_top1": ranking.get("unique_top1"),
            "reasons": ["certified_current_price_required"],
            "trade_plan": None,
        }
    current_ranking = copy.deepcopy(ranking)
    current_top = current_ranking["candidates"][0]
    current_top["current_price"] = float(certified_price)
    current_top["price_as_of"] = str(certified_at)
    current_top["value_discount_pct"] = round(
        (float(current_top["fair_value_base"]) / float(certified_price) - 1) * 100,
        6,
    )
    current_top["eligible"] = bool(
        current_top.get("eligible") and current_top["value_discount_pct"] > 0
    )
    current_top["current_signal_complete"] = bool(
        current_top.get("current_signal_complete")
        and float(certified_plan["entry_low"]) <= float(certified_price) <= float(certified_plan["entry_high"])
    )
    current_top["entry_plan"] = {
        key: certified_plan[key]
        for key in [
            "entry_low", "entry_high", "entry_trigger", "valid_until",
            "target_1", "target_2", "stop_price", "latest_exit_at",
        ]
    }
    result = build_trade_plan(current_ranking)
    result["trade_plan_id"] = f"plan-current-{_digest([result, market_evidence['evidence_digest']])[:16]}"
    result["market_evidence_digest"] = market_evidence["evidence_digest"]
    result["market_evidence"] = market_evidence
    return result


def build_paper_tactical_baseline(
    *,
    month_id: str,
    strategy_version: str,
    opened_at: str,
    initial_capital_usd: float = 500.0,
) -> dict[str, Any]:
    result = {
        "schema_version": "PaperTacticalCapitalBaselineV1",
        "baseline_id": f"paper-baseline-{_digest([month_id, strategy_version, opened_at, initial_capital_usd])[:16]}",
        "month_id": month_id,
        "request_mode": "tactical_1_7d",
        "evidence_mode": "paper",
        "initial_capital_usd": initial_capital_usd,
        "strategy_version": strategy_version,
        "opened_at": opened_at,
        "live_orders_enabled": False,
        "legacy_unknown_horizon_imported": False,
    }
    return validate_contract(result)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        existing = []
        if path.exists():
            existing = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        record_id = next((v for k, v in record.items() if k.endswith("_id")), None)
        if record_id and any(record_id in item.values() for item in existing):
            raise ContractError(f"append-only record already exists: {record_id}")
        with path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            out.flush()
            os.fsync(out.fileno())


def append_receipt(path: Path, receipt: dict[str, Any]) -> None:
    validate_contract(receipt)
    _append_jsonl(path, receipt)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_paper_receipt(path: Path, baseline: dict[str, Any], receipt: dict[str, Any]) -> None:
    validate_contract(baseline)
    validate_contract(receipt)
    if receipt["baseline_id"] != baseline["baseline_id"]:
        raise ContractError("paper receipt baseline mismatch")
    if receipt["strategy_version"] != baseline["strategy_version"]:
        raise ContractError("paper receipt strategy version mismatch")
    existing = load_jsonl(path)
    for prior in existing:
        validate_contract(prior)
        if prior.get("trade_id") != receipt["trade_id"]:
            continue
        for field in ["baseline_id", "strategy_version", "snapshot_id", "request_mode", "evidence_mode", "symbol"]:
            if prior.get(field) != receipt.get(field):
                raise ContractError(f"paper trade identity mismatch: {field}")
    _append_jsonl(path, receipt)


def calculate_paper_profit_attribution(
    baseline: dict[str, Any],
    receipts: list[dict[str, Any]],
    *,
    period_start: str,
    period_end: str,
    marks: dict[str, float] | None = None,
    as_of: str | None = None,
    target_pct: float = 100.0,
    emergency_pause_pct: float = -15.0,
) -> dict[str, Any]:
    """Money-weighted attribution for the isolated tactical_1_7d Paper ledger."""

    validate_contract(baseline)
    start, end = parse_time(period_start), parse_time(period_end)
    stamp = parse_time(as_of or now_iso())
    marks = marks or {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    eligible_receipt_count = 0
    for receipt in receipts:
        validate_contract(receipt)
        if receipt.get("schema_version") != "PaperExecutionReceiptV1":
            raise ContractError("legacy or live receipts cannot enter the paper denominator")
        if receipt["baseline_id"] != baseline["baseline_id"]:
            raise ContractError("paper receipt belongs to another baseline")
        if receipt["strategy_version"] != baseline["strategy_version"]:
            raise ContractError("paper receipt belongs to another strategy version")
        receipt_time = parse_time(receipt["executed_at"])
        if receipt_time > stamp:
            raise ContractError("paper receipt is after attribution as_of")
        if receipt_time <= end:
            grouped.setdefault(receipt["trade_id"], []).append(receipt)
            eligible_receipt_count += 1

    realized = unrealized = fees = 0.0
    missing_end_marks: list[str] = []
    trade_results: list[dict[str, Any]] = []
    for trade_id, events in sorted(grouped.items()):
        ordered = sorted(events, key=lambda item: parse_time(item["executed_at"]))
        symbol = ordered[0]["symbol"]
        lots: list[list[float]] = []
        trade_realized = 0.0
        trade_fees = 0.0
        invested = 0.0
        for event in ordered:
            event_time = parse_time(event["executed_at"])
            if event_time < start:
                raise ContractError("paper tactical baseline cannot inherit pre-period positions")
            if event_time > end:
                continue
            trade_fees += float(event["fee_usd"]) + float(event["slippage_usd"])
            if event["event"] == "entry":
                lots.append([float(event["quantity"]), float(event["price"])])
                invested += float(event["quantity"]) * float(event["price"])
                continue
            remaining = float(event["quantity"])
            proceeds = remaining * float(event["price"])
            basis = 0.0
            while remaining > 1e-12 and lots:
                taken = min(remaining, lots[0][0])
                basis += taken * lots[0][1]
                lots[0][0] -= taken
                remaining -= taken
                if lots[0][0] <= 1e-12:
                    lots.pop(0)
            if remaining > 1e-12:
                raise ContractError(f"paper exit quantity exceeds entries: {trade_id}")
            trade_realized += proceeds - basis
        open_qty = sum(item[0] for item in lots)
        open_basis = sum(item[0] * item[1] for item in lots)
        if open_qty > 1e-12 and symbol not in marks:
            missing_end_marks.append(symbol)
            trade_unrealized = 0.0
        else:
            mark = float(marks.get(symbol, 0.0))
            trade_unrealized = open_qty * mark - open_basis
        realized += trade_realized
        unrealized += trade_unrealized
        fees += trade_fees
        trade_results.append({
            "trade_id": trade_id,
            "symbol": symbol,
            "invested_usd": round(invested, 6),
            "realized_pnl_usd": round(trade_realized - trade_fees, 6),
            "unrealized_pnl_usd": round(trade_unrealized, 6),
            "single_trade_roi_pct": round((trade_realized + trade_unrealized - trade_fees) / invested * 100, 6) if invested else None,
        })
    net_profit = realized + unrealized - fees
    opening_capital = float(baseline["initial_capital_usd"])
    roi = round(net_profit / opening_capital * 100, 6)
    if missing_end_marks:
        status = "PAPER_EVIDENCE_MISSING"
        blocker = "缺少 Paper 未平仓标的期末价格: " + ", ".join(sorted(set(missing_end_marks)))
    elif eligible_receipt_count == 0:
        status, blocker = "COLLECTING_SAMPLES", "尚无 tactical_1_7d Paper 成交样本"
    elif roi <= emergency_pause_pct:
        status, blocker = "PAPER_FAILED_PAUSED", "Paper 回撤触发 15% 紧急暂停线"
    elif stamp < end:
        status = "PAPER_EXPERIMENT_RUNNING" if roi >= 0 else "PAPER_NOT_ON_TRACK"
        blocker = "Paper 月度结算窗口尚未结束"
    else:
        status = "PAPER_GOAL_HIT" if roi >= target_pct else "PAPER_NOT_ON_TRACK"
        blocker = "" if status == "PAPER_GOAL_HIT" else "Paper 资金加权净 ROI 未达目标"
    result = {
        "schema_version": "ProfitAttributionV2",
        "evidence_mode": "paper",
        "request_mode": "tactical_1_7d",
        "baseline_id": baseline["baseline_id"],
        "strategy_version": baseline["strategy_version"],
        "as_of": as_of or now_iso(),
        "period_start": period_start,
        "period_end": period_end,
        "opening_capital_usd": opening_capital,
        "realized_profit_usd": round(realized, 6),
        "ending_open_position_change_usd": round(unrealized, 6),
        "fees_and_slippage_usd": round(fees, 6),
        "net_profit_usd": round(net_profit, 6),
        "money_weighted_net_roi_pct": roi,
        "status": status,
        "blocker": blocker,
        "sample_count": len(trade_results),
        "trade_results": trade_results,
        "business_ready_eligible": False,
        "live_orders_enabled": False,
        "legacy_unknown_horizon_imported": False,
    }
    return validate_contract(result)


def calculate_profit_attribution(receipts: list[dict[str, Any]], period_start: str, period_end: str, opening_capital_usd: float | None, marks: dict[str, float] | None = None, as_of: str | None = None, target_pct: float = 100.0, emergency_pause_pct: float = -15.0, opening_marks: dict[str, float] | None = None) -> dict[str, Any]:
    start, end = parse_time(period_start), parse_time(period_end)
    marks = marks or {}
    opening_marks = opening_marks or {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for receipt in receipts:
        validate_contract(receipt)
        stamp = parse_time(receipt["executed_at"])
        if stamp <= end:
            grouped.setdefault(receipt["trade_id"], []).append(receipt)
    realized = unrealized = fees = 0.0
    missing_end_marks: list[str] = []
    trade_results = []
    for trade_id, events in sorted(grouped.items()):
        ordered = sorted(events, key=lambda x: parse_time(x["executed_at"]))
        symbol = ordered[0]["symbol"]
        lots: list[list[float]] = []
        for event in [x for x in ordered if parse_time(x["executed_at"]) < start]:
            if event["event"] == "entry":
                lots.append([float(event["quantity"]), float(event["price"])])
            else:
                remaining = float(event["quantity"])
                while remaining > 1e-12 and lots:
                    taken = min(remaining, lots[0][0])
                    lots[0][0] -= taken
                    remaining -= taken
                    if lots[0][0] <= 1e-12:
                        lots.pop(0)
                if remaining > 1e-12:
                    raise ContractError(f"exit quantity exceeds confirmed entries before period: {trade_id}")
        had_opening_position = bool(lots)
        if had_opening_position and symbol in opening_marks:
            _positive({"opening_mark": opening_marks[symbol]}, ["opening_mark"])
            for lot in lots:
                lot[1] = float(opening_marks[symbol])
        period_events = [x for x in ordered if start <= parse_time(x["executed_at"]) <= end]
        trade_realized = 0.0
        period_entry_cost = 0.0
        for event in period_events:
            if event["event"] == "entry":
                lots.append([float(event["quantity"]), float(event["price"])])
                period_entry_cost += event["quantity"] * event["price"]
            else:
                remaining = float(event["quantity"])
                proceeds = remaining * event["price"]
                basis = 0.0
                while remaining > 1e-12 and lots:
                    taken = min(remaining, lots[0][0])
                    basis += taken * lots[0][1]
                    lots[0][0] -= taken
                    remaining -= taken
                    if lots[0][0] <= 1e-12:
                        lots.pop(0)
                if remaining > 1e-12:
                    raise ContractError(f"exit quantity exceeds confirmed entries: {trade_id}")
                trade_realized += proceeds - basis
        trade_fees = sum(x["fee_usd"] + x["slippage_usd"] for x in period_events)
        fees += trade_fees
        open_qty = sum(lot[0] for lot in lots)
        open_basis = sum(lot[0] * lot[1] for lot in lots)
        if open_qty > 1e-12 and symbol not in marks:
            missing_end_marks.append(symbol)
            trade_unrealized = 0.0
        else:
            trade_unrealized = open_qty * marks.get(symbol, 0.0) - open_basis
        realized += trade_realized
        unrealized += trade_unrealized
        denominator = period_entry_cost + (open_basis if had_opening_position else 0.0)
        trade_results.append({"trade_id": trade_id, "symbol": symbol, "invested_usd": round(denominator, 6), "realized_pnl_usd": round(trade_realized - trade_fees, 6), "unrealized_pnl_usd": round(trade_unrealized, 6), "single_trade_roi_pct": round(((trade_realized + trade_unrealized - trade_fees) / denominator * 100), 6) if denominator else None})
    net_pnl = realized + unrealized - fees
    if opening_capital_usd is None or opening_capital_usd <= 0:
        status, roi, blocker = "BLOCKED", None, "缺少本月月初战术资金池本金"
    elif missing_end_marks:
        status, roi = "BLOCKED", None
        blocker = "缺少未平仓标的期末价格证据: " + ", ".join(sorted(set(missing_end_marks)))
    else:
        roi = round(net_pnl / opening_capital_usd * 100, 6)
        stamp = parse_time(as_of or now_iso())
        if roi <= emergency_pause_pct:
            status = "FAILED"
            blocker = "触发战术池 15% 紧急暂停线"
        elif stamp < end:
            status = "EXPERIMENT_RUNNING" if roi >= 0 else "NOT_ON_TRACK"
            blocker = "月度结算窗口尚未结束"
        else:
            status = "GOAL_HIT" if roi >= target_pct else "FAILED"
            blocker = "" if status == "GOAL_HIT" else "月度资金加权净 ROI 未达目标"
    result = {
        "schema_version": "ProfitAttributionV1", "as_of": as_of or now_iso(),
        "period_start": period_start, "period_end": period_end,
        "opening_tactical_capital_usd": opening_capital_usd,
        "realized_profit_usd": round(realized, 6), "ending_open_position_change_usd": round(unrealized, 6),
        "fees_and_slippage_usd": round(fees, 6), "net_profit_usd": round(net_pnl, 6),
        "monthly_money_weighted_net_roi_pct": roi, "status": status, "blocker": blocker,
        "trade_results": trade_results,
    }
    return validate_contract(result)


def calculate_long_term_path(previous_snapshot: dict[str, Any], current_snapshot: dict[str, Any], net_new_principal_usd: float) -> dict[str, Any]:
    """Measure long-term progress without treating added principal as investment return."""
    validate_contract(previous_snapshot)
    validate_contract(current_snapshot)
    if net_new_principal_usd < 0:
        raise ContractError("net_new_principal_usd must be non-negative")
    previous_value = float(previous_snapshot["baseline_value_usd"])
    current_value = float(current_snapshot["baseline_value_usd"])
    economic_gain = current_value - previous_value - net_new_principal_usd
    return_pct = economic_gain / previous_value * 100
    elapsed_days = (parse_time(current_snapshot["as_of"]) - parse_time(previous_snapshot["as_of"])).total_seconds() / 86400
    if elapsed_days <= 0:
        raise ContractError("current long-term snapshot must be later than previous snapshot")
    if elapsed_days < 30 or 1 + return_pct / 100 <= 0:
        annualized_pct = None
        status = "EXPERIMENT_RUNNING" if elapsed_days >= 0 else "BLOCKED"
    else:
        annualized_pct = ((1 + return_pct / 100) ** (365.25 / elapsed_days) - 1) * 100
        status = "ON_5Y_PATH" if annualized_pct >= 58.489 else "ON_10Y_PATH" if annualized_pct >= 25.893 else "NOT_ON_TRACK"
    return {
        "previous_snapshot_id": previous_snapshot["snapshot_id"], "current_snapshot_id": current_snapshot["snapshot_id"],
        "elapsed_days": round(elapsed_days, 6), "net_new_principal_usd_excluded": net_new_principal_usd,
        "economic_gain_usd": round(economic_gain, 6), "principal_adjusted_return_pct": round(return_pct, 6),
        "annualized_return_pct": round(annualized_pct, 6) if annualized_pct is not None else None,
        "required_5y_cagr_pct": 58.489, "required_10y_cagr_pct": 25.893, "status": status,
    }


def append_profit_review(path: Path, review: dict[str, Any]) -> None:
    validate_contract(review)
    if review["approved"] and review["change_type"] == "profit_rule":
        stamp = parse_time(review["reviewed_at"])
        for prior in load_jsonl(path):
            validate_contract(prior)
            if prior.get("approved") and prior["change_type"] == "profit_rule":
                delta = abs((stamp - parse_time(prior["reviewed_at"])).total_seconds())
                if delta < 14 * 86400:
                    raise ContractError("only one core profit-rule change is allowed per 14 days")
    _append_jsonl(path, review)


def reunderwrite_short_to_long(data: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(data)
    result["schema_version"] = "LongTermReUnderwriteV1"
    gates = ["independent_from_short_thesis", "longterm_thesis_pass", "valuation_pass", "liquidity_pass", "concentration_pass", "user_confirmed"]
    result["decision"] = "ALLOW_LONG_TERM_TRANSFER" if all(result.get(k) is True for k in gates) else "KEEP_ORIGINAL_SHORT_EXIT"
    return validate_contract(result)


def build_business_status(delivery_status: str, attribution: dict[str, Any] | None, long_snapshot: dict[str, Any] | None, as_of: str | None = None) -> dict[str, Any]:
    short_status = attribution.get("status", "BLOCKED") if attribution else "BLOCKED"
    if attribution and attribution.get("blocker"):
        short_blocker = attribution["blocker"]
    elif not attribution:
        short_blocker = "缺少月初战术本金和最小成交回执"
    else:
        short_blocker = ""
    if long_snapshot is None:
        long_status, long_blocker = "BLOCKED", "缺少用户确认的长期持仓、现金、成本和质押快照"
    else:
        validate_contract(long_snapshot)
        long_status, long_blocker = "PATH_DEFINED", "需要真实收益时间序列验证 5 年/10 年目标路径"
    blockers: list[tuple[str, str]] = []
    if short_status not in {"GOAL_HIT", "EXPERIMENT_RUNNING"}:
        short_next = "确认本月月初战术资金池本金，并追加真实最小成交回执" if "本金" in short_blocker else "补齐期末价格证据后重新计算资金加权净 ROI" if "期末价格" in short_blocker else "执行本期风险处置并只复盘一个核心赚钱规则"
        blockers.append((short_blocker or f"短期利润状态为 {short_status}", short_next))
    if long_status != "PATH_DEFINED":
        blockers.append((long_blocker, "确认长期持仓、现金、成本和质押权威快照"))
    if delivery_status not in {"BUSINESS_READY"}:
        blockers.append((f"系统交付仅达到 {delivery_status}", "继续运行并积累 90 天可归因净利润证据"))
    if long_status == "PATH_DEFINED" and not blockers:
        blockers.append((long_blocker, "继续运行并积累 90 天可归因净利润证据"))
    blocker, next_step = blockers[0] if blockers else ("无", "维持当前规则并等待下一次复核")
    return validate_contract({
        "schema_version": "BusinessOutcomeStatusV1", "as_of": as_of or now_iso(),
        "delivery_status": delivery_status, "short_profit_status": short_status,
        "long_path_status": long_status, "unique_blocker": blocker, "unique_next_step": next_step,
    })


def build_business_status_v2(
    *,
    delivery_status: str,
    paper_attribution: dict[str, Any] | None,
    live_attribution: dict[str, Any] | None,
    longterm_authority: str,
    market_evidence_status: str = "FRESH",
    paper_mode_enabled: bool = True,
    live_cash_available: bool = False,
    ninety_day_live_evidence: bool = False,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Separate mechanism readiness from execution and outcome evidence."""

    if paper_attribution is not None:
        validate_contract(paper_attribution)
        if paper_attribution.get("evidence_mode") != "paper":
            raise ContractError("paper_attribution must use paper evidence")
    if live_attribution is not None:
        validate_contract(live_attribution)
        if live_attribution.get("schema_version") == "ProfitAttributionV2" and live_attribution.get("evidence_mode") != "live":
            raise ContractError("live_attribution must use live evidence")
    if longterm_authority not in {"legacy_estimate", "user_confirmed"}:
        raise ContractError("unsupported longterm_authority")

    delivery_ready = delivery_status in {"RUNTIME_VERIFIED", "BUSINESS_READY"}
    if not delivery_ready:
        execution_readiness = "NO_DEPLOY"
    elif paper_mode_enabled:
        execution_readiness = "PAPER_ONLY_READY"
    elif live_cash_available and market_evidence_status == "FRESH":
        execution_readiness = "MANUAL_LIVE_READY"
    else:
        execution_readiness = "NO_DEPLOY"

    paper_status = paper_attribution.get("status") if paper_attribution else (
        "COLLECTING_SAMPLES" if paper_mode_enabled else "NOT_ENABLED"
    )
    live_status = live_attribution.get("status") if live_attribution else "NOT_STARTED"
    if longterm_authority == "legacy_estimate":
        long_status = {
            "status": "DATA_DEGRADED",
            "authority": "legacy_estimate",
            "decision_scope": "RESEARCH_ONLY",
            "formal_baseline_value_usd": None,
            "annualized_return_pct": None,
            "on_path_status": None,
        }
    else:
        long_status = {
            "status": "BASELINE_CONFIRMED",
            "authority": "user_confirmed",
            "decision_scope": "FORMAL_PATH_MEASUREMENT",
            "formal_baseline_value_usd": None,
            "annualized_return_pct": None,
            "on_path_status": None,
        }

    blockers: list[dict[str, Any]] = []

    def add_blocker(priority: int, axis: str, code: str, message: str, next_step: str, *, blocking: bool = True) -> None:
        blockers.append({
            "priority": priority,
            "axis": axis,
            "code": code,
            "message": message,
            "next_step": next_step,
            "blocking": blocking,
        })

    if not delivery_ready:
        add_blocker(1, "delivery", "system_not_runtime_verified", f"系统交付仅达到 {delivery_status}", "修复版本或运行验证后重新计算状态")
    if market_evidence_status != "FRESH":
        add_blocker(2, "market_evidence", "no_fresh_decision", "当前行情或 TradePlan 已过期、冲突或超时", "重新运行 Crypto 1–7 日扫描并生成同一快照的新计划")
    if not live_cash_available:
        add_blocker(3, "execution", "live_cash_missing", "真钱通道没有已确认可部署资金", "只有决定进入真钱阶段时再确认同通道资金", blocking=not paper_mode_enabled)
    if paper_mode_enabled and paper_status in {"COLLECTING_SAMPLES", "PAPER_NOT_ON_TRACK", "PAPER_EXPERIMENT_RUNNING", "PAPER_EVIDENCE_MISSING", "PAPER_FAILED_PAUSED"}:
        add_blocker(4, "short_profit.paper", "paper_evidence_incomplete", f"短期 Paper 状态为 {paper_status}", "运行一次新的 Crypto 1–7 日 Paper 扫描并建立首个专用 Paper 样本")
    if live_status not in {"GOAL_HIT"}:
        add_blocker(4, "short_profit.live", "live_profit_unproven", f"短期真钱利润状态为 {live_status}", "保持与 Paper 分母隔离并等待未来真实成交证据", blocking=False)
    if longterm_authority == "legacy_estimate":
        add_blocker(5, "long_path", "legacy_estimate_only", "长期持仓仍为旧估算，只能用于研究", "保持 DATA_DEGRADED；未来完整确认后再建立正式路径")
    if not ninety_day_live_evidence:
        add_blocker(6, "business_outcome", "ninety_day_evidence_pending", "尚无 90 天可归因真钱结果", "持续积累独立真钱时间序列，期间不得宣称 BUSINESS_READY")

    blockers.sort(key=lambda item: (item["priority"], item["axis"], item["code"]))
    primary = next((item for item in blockers if item["blocking"]), None)
    unique_blocker = primary["message"] if primary else "无"
    unique_next_step = primary["next_step"] if primary else "维持当前规则并等待下一次复核"
    business_ready = all([
        delivery_status == "BUSINESS_READY",
        live_status == "GOAL_HIT",
        longterm_authority == "user_confirmed",
        ninety_day_live_evidence,
    ])
    short_status = {"paper": paper_status, "live": live_status}
    v1_compat = {
        "schema_version": "BusinessOutcomeStatusV1",
        "as_of": as_of or now_iso(),
        "delivery_status": delivery_status,
        "short_profit_status": live_status,
        "long_path_status": long_status["status"],
        "unique_blocker": unique_blocker,
        "unique_next_step": unique_next_step,
    }
    result = {
        "schema_version": "BusinessOutcomeStatusV2",
        "as_of": as_of or now_iso(),
        "delivery_status": delivery_status,
        "execution_readiness": execution_readiness,
        "short_profit_status": short_status,
        "long_path_status": long_status,
        "business_outcome_status": "BUSINESS_READY" if business_ready else "EVIDENCE_PENDING",
        "all_blockers": blockers,
        "unique_blocker": unique_blocker,
        "unique_next_step": unique_next_step,
        "v1_compat": v1_compat,
        "live_orders_enabled": False,
    }
    return validate_contract(result)


def project_business_status_v1(status_v2: dict[str, Any]) -> dict[str, Any]:
    validate_contract(status_v2)
    return validate_contract(copy.deepcopy(status_v2["v1_compat"]))


def persist_status_if_changed(path: Path, status: dict[str, Any]) -> str:
    compare = {k: v for k, v in status.items() if k != "as_of"}
    if path.exists():
        old = json.loads(path.read_text())
        if _digest({k: v for k, v in old.items() if k != "as_of"}) == _digest(compare):
            return "NO_UPDATE"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)
    return "UPDATED"


def self_test() -> dict[str, Any]:
    ranked = rank_candidates("demo-snapshot", "demo-v1", [{
        "symbol": "DEMO", "request_mode": "tactical_1_7d", "current_price": 90, "price_as_of": "2030-01-01T00:00:00Z", "fair_value_base": 120,
        "conservative_ev_pct": 8, "expected_drawdown_pct": 3, "catalyst_certainty": 0.8,
        "catalyst_verified": True, "catalyst_summary": "deterministic demo only", "liquidity_status": "verified",
        "data_quality_status": "verified", "sample_size": 35, "probability_tier": "calibrated",
        "current_signal_complete": True, "holdout_pass": True, "evidence_ids": ["demo-evidence"],
        "entry_plan": {"entry_low": 89, "entry_high": 91, "entry_trigger": "demo trigger", "valid_until": "2030-01-02T00:00:00Z", "target_1": 100, "target_2": 108, "stop_price": 86, "latest_exit_at": "2030-01-08T00:00:00Z"},
    }], ranked_at="2030-01-01T00:00:00Z")
    plan = build_trade_plan(ranked)
    attribution = calculate_profit_attribution([], "2030-01-01T00:00:00Z", "2030-01-31T23:59:59Z", None, as_of="2030-01-15T00:00:00Z")
    baseline = build_paper_tactical_baseline(month_id="2030-01", strategy_version="demo-v2", opened_at="2030-01-01T00:00:00Z")
    paper = calculate_paper_profit_attribution(baseline, [], period_start="2030-01-01T00:00:00Z", period_end="2030-01-31T23:59:59Z", as_of="2030-01-15T00:00:00Z")
    status = build_business_status_v2(delivery_status="RUNTIME_VERIFIED", paper_attribution=paper, live_attribution=None, longterm_authority="legacy_estimate", live_cash_available=False, as_of="2030-01-15T00:00:00Z")
    assert ranked["unique_top1"] == "DEMO" and plan["action"] == "ENTER_NOW" and attribution["status"] == "BLOCKED"
    assert status["execution_readiness"] == "PAPER_ONLY_READY" and status["business_outcome_status"] == "EVIDENCE_PENDING"
    return {"status": "PASS", "checks": ["unique_top1", "enter_now_gate", "missing_live_capital_does_not_block_paper", "paper_live_isolation", "four_axis_status", "no_live_orders"]}


def initialize_paper_runtime(
    runtime_dir: Path,
    *,
    month_id: str,
    strategy_version: str,
    opened_at: str,
    period_end: str,
    as_of: str,
    delivery_status: str = "TESTED",
) -> dict[str, Any]:
    """Create/update only the isolated Paper baseline and its honest current status."""

    baseline = build_paper_tactical_baseline(
        month_id=month_id,
        strategy_version=strategy_version,
        opened_at=opened_at,
    )
    receipts_path = runtime_dir / "tactical_1_7d_paper_receipts.jsonl"
    receipts = load_jsonl(receipts_path)
    attribution = calculate_paper_profit_attribution(
        baseline,
        receipts,
        period_start=opened_at,
        period_end=period_end,
        as_of=as_of,
    )
    status = build_business_status_v2(
        delivery_status=delivery_status,
        paper_attribution=attribution,
        live_attribution=None,
        longterm_authority="legacy_estimate",
        market_evidence_status="FRESH",
        paper_mode_enabled=True,
        live_cash_available=False,
        ninety_day_live_evidence=False,
        as_of=as_of,
    )
    baseline_result = persist_status_if_changed(runtime_dir / "paper_tactical_capital_baseline_v1.json", baseline)
    attribution_result = persist_status_if_changed(runtime_dir / "paper_profit_attribution_v2.json", attribution)
    status_result = persist_status_if_changed(runtime_dir / "business_outcome_status_v2.json", status)
    return {
        "schema_version": "ProfitRuntimeInitializationResultV1",
        "baseline_write": baseline_result,
        "attribution_write": attribution_result,
        "status_write": status_result,
        "baseline": baseline,
        "attribution": attribution,
        "status": status,
        "receipts_path": str(receipts_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--init-paper-runtime")
    parser.add_argument("--month-id")
    parser.add_argument("--strategy-version", default="profit-outcome-status-separation-v2")
    parser.add_argument("--opened-at")
    parser.add_argument("--period-end")
    parser.add_argument("--as-of")
    parser.add_argument("--delivery-status", choices=["DESIGNED", "CODED", "TESTED", "RUNTIME_VERIFIED", "BUSINESS_READY"], default="TESTED")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, sort_keys=True))
        return 0
    if args.init_paper_runtime:
        required = [args.month_id, args.opened_at, args.period_end, args.as_of]
        if any(value is None for value in required):
            parser.error("--month-id, --opened-at, --period-end and --as-of are required")
        result = initialize_paper_runtime(
            Path(args.init_paper_runtime).expanduser().resolve(),
            month_id=args.month_id,
            strategy_version=args.strategy_version,
            opened_at=args.opened_at,
            period_end=args.period_end,
            as_of=args.as_of,
            delivery_status=args.delivery_status,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    parser.error("use --self-test or --init-paper-runtime")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
