#!/usr/bin/env python3
"""Build the daily human-decision report from the strict 1–24h scan artifact."""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DAILY = ROOT / "experiments/current-daily-priority-cycle.json"
DEFAULT_LEDGER = ROOT / "data/paper_ledger.json"
DEFAULT_OUTPUT = ROOT / "experiments/current-daily-manual-decision.json"
DEFAULT_REPORT = ROOT / "reports/CURRENT_DAILY_MANUAL_DECISION.md"
DEFAULT_RESEARCH = ROOT / "experiments/current-event-level-research.json"
DEFAULT_EXIT_CONDITIONS = [
    "updated_probability_below_executable_market_price",
    "core_thesis_invalidated",
    "market_price_above_model_fair_ceiling",
    "data_or_resolution_rule_integrity_failure",
    "remaining_upside_no_longer_compensates_hold_risk",
]
CATEGORY_LABELS = {
    "alpha_primary_recommendation": "Alpha 主推荐",
    "high_win_small_return_recommendation": "高胜率小收益推荐",
    "experimental_research_recommendation": "实验性研究推荐",
    "conditional_watch": "等待价格或催化剂",
    "no_bet": "NO BET",
}
CATEGORY_RANK = {key: index for index, key in enumerate(CATEGORY_LABELS)}


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module


core = load("daily_manual_core", ROOT / "scripts/polymarket_alpha.py")


def event_phase(row: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    """Classify operational priority without pretending an inferred start is live data."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    start = core.parse_iso(row.get("game_start_time") or row.get("gameStartTime") or row.get("event_start_time"))
    end = core.parse_iso(row.get("end_date"))
    explicit = str(row.get("event_status") or "").strip().lower()
    live = explicit in {"live", "ongoing", "in_play", "in-play", "started"}
    if not live and start and end:
        live = start <= now < end
    updated = core.parse_iso(row.get("live_state_updated_at"))
    age = (now - updated).total_seconds() if updated else None
    max_age = 120.0
    fresh = bool(live and age is not None and -30 <= age <= max_age)
    local = ZoneInfo("Asia/Shanghai")
    same_day = bool(end and end.astimezone(local).date() == now.astimezone(local).date())
    if live:
        phase, priority = "ongoing", 1
    elif same_day:
        phase, priority = "same_day", 2
    else:
        phase, priority = "next_24h", 3
    return {
        "event_phase": phase,
        "event_priority": priority,
        "game_start_time": start.isoformat() if start else None,
        "live_score": row.get("live_score"),
        "live_data_age_seconds": age,
        "live_data_status": "fresh" if fresh else "stale_or_missing" if live else "not_required_pre_match",
        "live_data_verified": fresh if live else None,
    }


def position_management_plan(item: dict[str, Any], row: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Turn the probability-to-market gap into an explicit hold/sell paper plan."""
    cfg = policy.get("in_play_exit_policy") or {}
    probability = item.get("model_estimated_probability")
    low = (item.get("confidence_interval") or [None])[0]
    current_sell = ((item.get("execution_tests_by_side") or {}).get(str(row.get("side") or "")) or {}).get("best_bid")
    explicit_entry = row.get("paper_entry_price") or row.get("entry_price")
    recommended = item.get("category") in {"alpha_primary_recommendation", "high_win_small_return_recommendation", "experimental_research_recommendation"}
    entry = explicit_entry or (item.get("executable_price") if recommended else None)
    edge = float(probability) - float(current_sell) if probability is not None and current_sell is not None else None
    gain = float(current_sell) - float(entry) if current_sell is not None and entry is not None else None
    remaining_upside = 1 - float(current_sell) if current_sell is not None else None
    profit_trigger = min(.99, float(entry) + float(cfg.get("profit_lock_price_gain_per_share", .10))) if entry is not None else None
    recover_trigger = min(.99, float(entry) + float(cfg.get("recover_stake_price_gain_per_share", .20))) if entry is not None else None
    recover_fraction = min(1.0, float(entry) / float(current_sell)) if current_sell and entry else None
    live_unverified = item.get("event_phase") == "ongoing" and item.get("live_data_verified") is not True
    if not recommended and explicit_entry is None:
        return {
            "position_management_mode": "wait_live_data_verification" if live_unverified else "no_position",
            "position_management_instruction": ("当前无仓位；先核验120秒内实时比分、时间和关键事件，再重新计算概率与净EV"
                                                if live_unverified else "当前无仓位；等待入场条件通过后才启用盘中退出计划"),
            "hold_to_settlement_allowed": False,
            "current_executable_sell_price": current_sell,
            "research_probability_minus_sell_price": edge,
            "mark_to_market_gain_per_share": None,
            "remaining_upside_per_share": remaining_upside,
            "profit_lock_trigger_price": None,
            "recover_stake_trigger_price": None,
            "suggested_sell_fraction": 0.0,
            "stake_recovery_sell_fraction_at_current_price": None,
            "full_exit_triggers": [],
        }
    mode, instruction, sell_fraction, hold = "tactical_price_exit", "等待盘口达到止盈线；论点恶化则退出", 0.0, False
    if live_unverified:
        mode, instruction = "manual_review_live_data_stale", "暂停新增；核验实时比分、时间和关键事件后再决定持有或退出"
    elif edge is not None and edge <= float(cfg.get("full_exit_when_model_edge_lte", 0.0)):
        mode, instruction, sell_fraction = "full_exit", "研究概率已不高于可卖价格，卖出全部 paper 仓位", 1.0
    elif gain is not None and gain >= float(cfg.get("recover_stake_price_gain_per_share", .20)):
        mode, instruction, sell_fraction = "partial_profit_lock_recover_stake", "卖出足够份额收回初始成本，其余仓位按模型优势管理", round(recover_fraction or .5, 4)
    elif ((edge is not None and edge <= float(cfg.get("de_risk_when_model_edge_lte", .02)))
          or (gain is not None and gain >= float(cfg.get("profit_lock_price_gain_per_share", .10)))):
        sell_fraction = float(cfg.get("partial_sell_fraction", .33))
        mode, instruction = "partial_profit_lock", f"卖出约 {sell_fraction:.0%} 锁定利润；剩余仓位仅在研究优势仍为正时持有"
    elif (probability is not None and low is not None and edge is not None and remaining_upside is not None
          and float(probability) >= float(cfg.get("hold_min_probability", .85))
          and float(low) >= float(cfg.get("hold_min_confidence_lower", .75))
          and edge >= float(cfg.get("hold_min_remaining_edge", .02))
          and remaining_upside >= float(cfg.get("hold_min_remaining_upside", .05))):
        mode, instruction, hold = "hold_to_settlement", "研究优势、可信区间和剩余收益均通过，可持有至结算", True
    return {
        "position_management_mode": mode,
        "position_management_instruction": instruction,
        "hold_to_settlement_allowed": hold,
        "current_executable_sell_price": current_sell,
        "research_probability_minus_sell_price": edge,
        "mark_to_market_gain_per_share": gain,
        "remaining_upside_per_share": remaining_upside,
        "profit_lock_trigger_price": round(profit_trigger, 4) if profit_trigger is not None else None,
        "recover_stake_trigger_price": round(recover_trigger, 4) if recover_trigger is not None else None,
        "suggested_sell_fraction": sell_fraction,
        "stake_recovery_sell_fraction_at_current_price": round(recover_fraction, 4) if recover_fraction is not None else None,
        "full_exit_triggers": ["updated_probability_lte_executable_sell_price", "core_thesis_invalidated", "live_data_or_rules_conflict"],
    }


def merge_event_research(daily: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    """Overlay traceable event research without treating market price as a forecast."""
    indexed={str(x.get("condition_id") or x.get("market_id")):x for x in research.get("research_items",[]) if x.get("condition_id") or x.get("market_id")}
    for row in daily.get("all_decisions") or []:
        item=indexed.get(str(row.get("condition_id") or "")) or indexed.get(str(row.get("market_id") or ""))
        if not item:continue
        researched=core.parse_iso(item.get("researched_at"));created=core.parse_iso(daily.get("created_at"))
        max_age=float(item.get("max_age_minutes") or 60)
        if not researched or not created or abs((created-researched).total_seconds())>max_age*60:continue
        if item.get("research_yes_probability") is None:
            row["research_status"] = item.get("research_status") or "insufficient"
            row["model_version"] = item.get("research_version")
            row["research_version"] = item.get("research_version")
            row["research_sources"] = item.get("sources") or []
            row["source_data_times"] = item.get("source_data_times") or []
            row["settlement_rules"] = item.get("settlement_rules")
            row["failure_paths"] = item.get("failure_paths") or []
            row["key_evidence"] = item.get("key_evidence") or []
            row["conditional_trigger"] = item.get("conditional_trigger")
            row["latest_entry_time"] = item.get("latest_entry_time") or row.get("end_date")
            row["research_exit_conditions"] = item.get("exit_conditions") or []
            row["failed_gates"] = ["probability_estimate_missing", "research_insufficient", "no_bet"]
            row["gate_passed"] = False
            continue
        yes=float(item["research_yes_probability"]);side=str(row.get("side") or "YES").upper();p=yes if side=="YES" else 1-yes
        low=float(item["confidence_low"]);high=float(item["confidence_high"])
        row["model_probability"]=p;row["confidence_low"]=low if side=="YES" else 1-high;row["confidence_high"]=high if side=="YES" else 1-low
        row["model_version"]=item.get("research_version");row["calibration_samples"]=int(item.get("calibrated_oos_samples") or 0)
        row["historical_hit_rate"]=item.get("historical_hit_rate");row["source_counts"]={"official":sum(s.get("kind")=="official" for s in item.get("sources",[])),"independent":len({s.get("url") for s in item.get("sources",[]) if s.get("kind")=="independent" and s.get("url")})}
        row["rules_review"]={"status":"clear" if item.get("rules_clear") else "blocked","reviewed_at":item.get("researched_at")}
        row["failure_paths"]=item.get("failure_paths") or [];row["settlement_rules"]=item.get("settlement_rules")
        row["research_method"]=item.get("method");row["research_version"]=item.get("research_version");row["research_sources"]=item.get("sources") or []
        row["source_data_times"]=item.get("source_data_times") or [];row["historical_base_rate"]=item.get("historical_base_rate");row["historical_base_rate_samples"]=item.get("historical_base_rate_samples")
        row["key_evidence"]=item.get("key_evidence") or [];row["latest_entry_time"]=item.get("latest_entry_time");row["research_exit_conditions"]=item.get("exit_conditions") or []
        row["conditional_trigger"]=item.get("conditional_trigger")
        for field in ("game_start_time", "event_status", "live_score", "live_state_updated_at", "paper_entry_price", "entry_price"):
            if item.get(field) is not None: row[field] = item.get(field)
        execution=row.get("execution") or {};conservative=max(0.0,p-float(item.get("uncertainty_discount") or .01));price=execution.get("fill_price")
        if price is not None:
            fee=float(row.get("fee_rate") or 0)*float(price)*(1-float(price));row["entry_fee_per_share"]=fee;row["conservative_probability"]=conservative;row["net_ev_per_share"]=conservative-float(price)-fee-float(item.get("rule_risk_reserve") or .005)
        failures=[];gate=core.read_json(ROOT/"config/policy.json")["entry_gate"]
        if p<gate["min_model_probability"]:failures.append("model_probability_below_gate")
        if row["confidence_low"]<gate["min_confidence_lower"]:failures.append("confidence_lower_below_gate")
        if int(row["calibration_samples"])<gate["min_calibration_samples"]:failures.append("domain_model_calibration_insufficient")
        if row["source_counts"]["official"]<1 or row["source_counts"]["independent"]<2:failures.append("source_confirmation_insufficient")
        if not item.get("rules_clear"):failures.append("resolution_rules_not_cleared")
        if not row["failure_paths"]:failures.append("failure_paths_missing")
        if not execution.get("fillable"):failures.append("execution_not_fillable")
        if execution.get("spread") is None or execution.get("spread")>gate["max_spread_per_share"]:failures.append("spread_above_gate")
        if execution.get("price_impact") is None or execution.get("price_impact")>gate["max_price_impact_per_share"]:failures.append("price_impact_above_gate")
        if row.get("net_ev_per_share") is None or row["net_ev_per_share"]<gate["min_net_edge_per_share"]:failures.append("net_ev_below_gate")
        row["failed_gates"]=failures;row["gate_passed"]=not failures
    daily["event_research_artifact"]=research.get("research_run_id");return daily


def maximum_acceptable_price(row: dict[str, Any], policy: dict[str, Any], category: str) -> float | None:
    probability = row.get("model_probability")
    if probability is None:
        return None
    gate = policy["entry_gate"]
    conservative = max(0.0, float(probability) - float(gate["uncertainty_discount"]))
    fee_rate = float(row.get("fee_rate") or 0.0)
    required = (0.0 if category == "high_win_small_return_recommendation" or row.get("experimental_subtype") == "high_win_small_return"
                else float(gate["min_net_edge_per_share"]))
    reserve = float(gate["resolution_risk_reserve"])
    low, high = 0.0, min(0.999999, conservative)
    for _ in range(60):
        price = (low + high) / 2
        edge = conservative - price - fee_rate * price * (1 - price) - reserve
        if edge >= required: low = price
        else: high = price
    return round(low, 6)


def experimental_eligibility(row: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    gate=policy.get("experimental_research_gate") or {}; failures=set(row.get("failed_gates") or [])
    permitted={"domain_model_calibration_insufficient","model_calibration_insufficient","calibration_samples_below_gate"}
    p=row.get("model_probability"); low=row.get("confidence_low"); ev=row.get("net_ev_per_share")
    source=row.get("source_counts") or {}; rules=row.get("rules_review") or {}; execution=row.get("execution") or {}
    evidence_ok=(int(source.get("official",0))>=int(gate.get("minimum_official_sources",1)) and int(source.get("independent",0))>=int(gate.get("minimum_independent_sources",2)) and rules.get("status")=="clear" and bool(row.get("failure_paths")))
    execution_ok=bool(execution.get("fillable") and execution.get("spread") is not None and execution["spread"]<=policy["entry_gate"]["max_spread_per_share"] and execution.get("price_impact") is not None and execution["price_impact"]<=policy["entry_gate"]["max_price_impact_per_share"])
    alpha=bool(p is not None and low is not None and ev is not None and float(p)>=gate.get("minimum_research_probability",.8) and float(low)>=gate.get("minimum_confidence_lower",.7) and float(ev)>=gate.get("minimum_alpha_net_edge_per_share",.045))
    high=bool(p is not None and low is not None and ev is not None and float(p)>=gate.get("high_win_minimum_probability",.9) and float(low)>=gate.get("high_win_minimum_confidence_lower",.85) and float(ev)>gate.get("high_win_minimum_net_ev_exclusive",0))
    calibration_missing=int(row.get("calibration_samples") or 0)<policy["entry_gate"]["min_calibration_samples"]
    eligible=bool(gate.get("enabled") and evidence_ok and execution_ok and calibration_missing and (alpha or high) and (not failures or failures<=permitted))
    return {"eligible":eligible,"subtype":"high_win_small_return" if high and not alpha else "alpha","evidence_ok":evidence_ok,"execution_ok":execution_ok}


def classify(row: dict[str, Any], policy: dict[str, Any]) -> str:
    failures = set(row.get("failed_gates") or [])
    if row.get("gate_passed") is True:
        return "alpha_primary_recommendation"
    high_win = core.evaluate_high_win_small_return(row, policy)
    if high_win["eligible"]:
        return "high_win_small_return_recommendation"
    experimental=experimental_eligibility(row,policy)
    if experimental["eligible"]:
        row["experimental_subtype"]=experimental["subtype"];return "experimental_research_recommendation"
    probability=row.get("model_probability");ev=row.get("net_ev_per_share");execution=row.get("execution") or {}
    if probability is not None and (ev is not None and float(ev)>0 or row.get("conditional_trigger")):
        return "conditional_watch"
    if probability is not None and not execution.get("fillable"):
        return "conditional_watch"
    return "no_bet"


def make_item(row: dict[str, Any], category: str, execution_viable: bool, policy: dict[str, Any]) -> dict[str, Any]:
    execution = row.get("execution") or {}; probability = row.get("model_probability")
    samples = int(row.get("calibration_samples") or 0)
    historical_hit_rate = row.get("historical_hit_rate")
    model_brier = row.get("model_brier_score"); market_brier = row.get("market_brier_score")
    model_log_loss = row.get("model_log_loss"); market_log_loss = row.get("market_log_loss")
    beats_market = ((model_brier is not None and market_brier is not None and model_brier < market_brier)
                    or (model_log_loss is not None and market_log_loss is not None and model_log_loss < market_log_loss))
    recommended = category in {"alpha_primary_recommendation", "high_win_small_return_recommendation", "experimental_research_recommendation"}
    true_probability_language_allowed = bool(probability is not None and historical_hit_rate is not None
        and samples >= policy["entry_gate"]["min_calibration_samples"] and beats_market
        and row.get("model_version") and recommended)
    suggested = float(row.get("planned_notional_usd") or 0) if recommended else 0.0
    if category=="experimental_research_recommendation": suggested=min(suggested or policy["initial_equity_usd"]*.005,policy["initial_equity_usd"]*.01)
    independent_model_direction = row.get("side") if row.get("model_version") and probability is not None else None
    slippage = ((execution.get("fill_price") or 0) - (execution.get("raw_vwap") or 0)) if execution.get("fillable") else None
    return {
        "market_id": row.get("market_id"), "condition_id": row.get("condition_id"),
        "market": row.get("question"),
        "side": row.get("side") if row.get("model_version") else None,
        "bet_direction": row.get("side") if recommended else "NO_BET",
        "independent_model_direction": independent_model_direction,
        "execution_evaluated_side": row.get("side"), "end_date": row.get("end_date"),
        "category": category, "category_label": CATEGORY_LABELS[category],
        "executable_price": execution.get("fill_price"), "spread": execution.get("spread"),
        "price_impact": execution.get("price_impact"), "execution_viable": execution_viable,
        "model_estimated_probability": probability,
        "confidence_interval": [row.get("confidence_low"), row.get("confidence_high")],
        "oos_or_forward_samples": samples, "historical_hit_rate": historical_hit_rate,
        "model_version": row.get("model_version"), "model_brier_score": model_brier,
        "market_brier_score": market_brier, "model_log_loss": model_log_loss, "market_log_loss": market_log_loss,
        "true_probability_language_allowed": true_probability_language_allowed,
        "net_edge_per_share": row.get("net_ev_per_share"),
        "maximum_acceptable_price": maximum_acceptable_price(row, policy, category),
        "recommendation_type": ("alpha_primary" if category == "alpha_primary_recommendation"
                                else "high_win_small_return" if category == "high_win_small_return_recommendation"
                                else "experimental_research" if category == "experimental_research_recommendation" else "none"),
        "recommendation_level": ("validated_recommendation" if category in {"alpha_primary_recommendation","high_win_small_return_recommendation"}
                                 else "experimental_research_recommendation" if category=="experimental_research_recommendation"
                                 else "conditional_watch" if category=="conditional_watch" else "no_bet"),
        "probability_status": "calibrated_probability" if true_probability_language_allowed else "research_estimated_probability_unvalidated",
        "calibration_disclaimer": None if true_probability_language_allowed else "尚未证明真实胜率达到80%",
        "suggested_paper_amount_usd": round(suggested, 2),
        "suggested_equity_pct": round(100 * suggested / float(policy["initial_equity_usd"]), 2) if suggested else 0.0,
        "failure_paths": row.get("failure_paths") or [],
        "exit_conditions": row.get("research_exit_conditions") or (DEFAULT_EXIT_CONDITIONS if recommended else []),
        "latest_entry_time": row.get("latest_entry_time") or row.get("end_date"),
        "settlement_rules": row.get("settlement_rules") or row.get("description"),
        "research_method": row.get("research_method"), "research_version": row.get("research_version") or row.get("model_version"),
        "research_sources": row.get("research_sources") or [], "source_data_times": row.get("source_data_times") or [],
        "historical_base_rate": row.get("historical_base_rate"), "historical_base_rate_samples": row.get("historical_base_rate_samples"),
        "key_evidence": row.get("key_evidence") or [], "conditional_trigger": row.get("conditional_trigger"),
        "tail_risk_label": (row.get("tail_risk_label") or (policy.get("high_win_small_return_gate") or {}).get("tail_risk_label"))
                           if category == "high_win_small_return_recommendation" else None,
        "entry_fee_per_share": row.get("entry_fee_per_share"), "extra_slippage_per_share": slippage,
        "failed_gates": row.get("failed_gates") or [],
        "final_action": "PAPER_ONLY_MANUAL_REVIEW" if recommended else ("WAIT" if category=="conditional_watch" else "NO_BET"),
    }


def best_side_per_market(decisions: list[dict[str, Any]], policy: dict[str, Any], now: dt.datetime | None = None) -> list[dict[str, Any]]:
    now = now or dt.datetime.now(dt.timezone.utc)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in decisions: grouped.setdefault(str(row.get("market_id") or ""), []).append(row)
    selected = []
    for sides in grouped.values():
        ranked = []
        for row in sides:
            phase = event_phase(row, now)
            category = classify(row, policy); execution = row.get("execution") or {}
            if phase["event_phase"] == "ongoing" and phase["live_data_verified"] is not True and category in {
                    "alpha_primary_recommendation", "high_win_small_return_recommendation", "experimental_research_recommendation"}:
                category = "conditional_watch"
                row["conditional_trigger"] = "实时比分/时间/关键事件数据在120秒内更新且研究概率与净EV重新通过"
            viable = bool(execution.get("fillable") and execution.get("spread") is not None
                and execution["spread"] <= policy["entry_gate"]["max_spread_per_share"]
                and execution.get("price_impact") is not None
                and execution["price_impact"] <= policy["entry_gate"]["max_price_impact_per_share"])
            edge = row.get("net_ev_per_share")
            ranked.append((CATEGORY_RANK[category], -int(viable), -(float(edge) if edge is not None else -99), row, category, viable))
        _, _, _, row, category, viable = min(ranked, key=lambda item: item[:3])
        side_tests = {}
        for side_row in sides:
            execution = side_row.get("execution") or {}
            side_tests[str(side_row.get("side") or "UNKNOWN")] = {
                "executable_price": execution.get("fill_price"),
                "best_bid": execution.get("best_bid"),
                "best_ask": execution.get("best_ask"),
                "spread": execution.get("spread"),
                "price_impact": execution.get("price_impact"),
                "fillable": bool(execution.get("fillable")),
            }
        selected.append((row, category, viable, side_tests))
    selected.sort(key=lambda item: (CATEGORY_RANK[item[1]], event_phase(item[0], now)["event_priority"], -int(item[2]), str(item[0].get("end_date") or "")))
    items = []
    for row, category, viable, side_tests in selected:
        item = make_item(row, category, viable, policy)
        item.update(event_phase(row, now))
        item["execution_tests_by_side"] = side_tests
        yes_test, no_test = side_tests.get("YES") or {}, side_tests.get("NO") or {}
        yes_mid = ((float(yes_test["best_bid"]) + float(yes_test["best_ask"])) / 2
                   if yes_test.get("best_bid") is not None and yes_test.get("best_ask") is not None else None)
        no_mid = ((float(no_test["best_bid"]) + float(no_test["best_ask"])) / 2
                  if no_test.get("best_bid") is not None and no_test.get("best_ask") is not None else None)
        total = yes_mid + no_mid if yes_mid is not None and no_mid is not None else None
        normalized_yes = yes_mid / total if total and total > 0 else None
        item["market_consensus_side"] = ("YES" if normalized_yes is not None and normalized_yes >= .5
                                         else "NO" if normalized_yes is not None else None)
        item["market_consensus_probability_proxy"] = (max(normalized_yes, 1 - normalized_yes)
                                                        if normalized_yes is not None else None)
        item["market_consensus_is_model_probability"] = False
        item.update(position_management_plan(item, row, policy))
        items.append(item)
    return items


def build(daily: dict[str, Any], ledger: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    report_policy = policy.get("manual_decision_report") or {}
    max_watch = min(4, int(report_policy.get("max_watch_items", 4)))
    max_recommendations = min(2, int(report_policy.get("max_recommendations", 2)))
    report_time = core.parse_iso(daily.get("created_at")) or dt.datetime.now(dt.timezone.utc)
    all_items = best_side_per_market(daily.get("all_decisions") or [], policy, report_time)
    current_equity = float(ledger.get("equity_usd") or policy["initial_equity_usd"])
    for item in all_items:
        item["suggested_equity_pct"] = round(100 * item["suggested_paper_amount_usd"] / current_equity, 2) if item["suggested_paper_amount_usd"] else 0.0
    alpha = [item for item in all_items if item["category"] == "alpha_primary_recommendation"]
    high_win = [item for item in all_items if item["category"] == "high_win_small_return_recommendation"][:1]
    experimental = [item for item in all_items if item["category"] == "experimental_research_recommendation"][:1]
    recommendations = (alpha + high_win + experimental)[:max_recommendations]
    chosen_ids = {item["market_id"] for item in recommendations}
    watch_items = recommendations + [item for item in all_items
        if item["market_id"] not in chosen_ids and item["category"] not in {"alpha_primary_recommendation", "high_win_small_return_recommendation", "experimental_research_recommendation"}][:max(0, max_watch - len(recommendations))]
    counts = {key: sum(item["category"] == key for item in all_items) for key in CATEGORY_LABELS}
    if not all_items:
        counts["no_bet"] = 1
    closed = len(ledger.get("closed_positions") or []); sizing = policy.get("early_stage_sizing") or {}
    return {
        "schema_version": "polymarket-daily-manual-decision-v3", "created_at": core.now_iso(),
        "source_daily_cycle_id": daily.get("cycle_id"), "source_daily_status": daily.get("status"),
        "status": "ok" if daily.get("status") == "ok" else "degraded_cash_only",
        "decision": "manual_review_recommendations_available" if recommendations else "cash_no_recommendation",
        "scanned_1_24h_markets": daily.get("inventory_1_24h_count", 0),
        "full_market_scanned": daily.get("full_market_scanned") or daily.get("markets_scanned"),
        "data_freshness_seconds": daily.get("snapshot_age_seconds"), "current_maximum_allowed_action":"paper_only_manual_recommendation",
        "watch_item_count": len(watch_items), "recommendation_count": len(recommendations),
        "ongoing_market_count": sum(row.get("event_phase") == "ongoing" for row in all_items),
        "same_day_market_count": sum(row.get("event_phase") == "same_day" for row in all_items),
        "alpha_primary_recommendation_count": sum(row["category"] == "alpha_primary_recommendation" for row in recommendations),
        "high_win_small_return_recommendation_count": sum(row["category"] == "high_win_small_return_recommendation" for row in recommendations),
        "experimental_research_recommendation_count": sum(row["category"] == "experimental_research_recommendation" for row in recommendations),
        "conditional_watch_count":sum(row["category"]=="conditional_watch" for row in all_items),
        "no_bet_count":sum(row["category"]=="no_bet" for row in all_items),
        "category_counts_all_scanned_markets": counts, "watch_items": watch_items, "decision_items": all_items,
        "guarantee": "daily_report_only_not_daily_opportunity_or_win_rate",
        "probability_language_policy": {"minimum_independent_oos_or_forward_samples": policy["entry_gate"]["min_calibration_samples"],
            "preferred_samples": [50, 100], "uncalibrated_output_label": "model_estimated_probability_not_true_win_rate"},
        "sizing_policy": {**sizing, "closed_paper_trades": closed,
            "risk_increase_review_eligible": bool(closed >= int(sizing.get("minimum_closed_paper_trades_for_review", 30)))},
        "paper_only": True, "manual_confirmation_required": True,
        "real_money_execution_authorized": False, "live_orders_enabled": False, "private_api_used": False,
    }


def fmt(value: Any, digits: int = 3) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def markdown(payload: dict[str, Any]) -> str:
    lines = ["# 每日 Polymarket 高概率人工决策报告", "",
        f"- 状态：`{payload['status']}`", f"- 最终结论：`{payload['decision']}`",
        f"- 报告时间：`{payload['created_at']}`",f"- 全市场扫描 / 进行中 / 当天结束 / 未来 24 小时候选：{payload.get('full_market_scanned') or '—'} / {payload['ongoing_market_count']} / {payload['same_day_market_count']} / {payload['scanned_1_24h_markets']}",
        f"- Alpha / 高胜率 / 实验性 / 等待 / NO BET：{payload['alpha_primary_recommendation_count']} / {payload['high_win_small_return_recommendation_count']} / {payload['experimental_research_recommendation_count']} / {payload['conditional_watch_count']} / {payload['no_bet_count']}",
        f"- 数据新鲜度：{payload.get('data_freshness_seconds')} 秒；最大允许动作：`{payload['current_maximum_allowed_action']}`",
        "- 保证每日出报告；不保证每日有机会，也不预先保证 80% 胜率。",
        "- 仅允许 paper；任何真钱动作都需要人工确认，当前未获授权。", ""]
    closest = payload.get("closest_candidate") or {}
    closest_summary = ""
    if closest:
        closest_summary = (f"最接近候选：{closest.get('question') or closest.get('market_id')}；"
                           f"{closest.get('side') or '—'} 最高可执行价 {fmt(closest.get('executable_price'))}；"
                           f"距 2¢ spread 门槛缺口 {fmt(closest.get('price_gap_to_spread_gate'))}。")
    if not payload["watch_items"]:
        lines.extend([
            "**今日无值得下注盘口，保持现金。**",
            closest_summary or "最近候选最高价格：无（快照无有效 1–24 小时候选）；价格缺口：无法计算。",
            "",
        ])
    elif not payload["recommendation_count"]:
        lines.extend(["**今日无值得下注盘口，保持现金。** 以下为最接近候选及精确价格/证据触发条件。",
                      closest_summary or "最近候选最高价格：无法计算；价格缺口：无法计算。", ""])
    section_order=[("alpha_primary_recommendation","Alpha 主推荐"),("high_win_small_return_recommendation","高胜率小收益推荐"),("experimental_research_recommendation","实验性研究推荐"),("conditional_watch","等待价格或催化剂"),("no_bet","NO BET")]
    lines.extend(["## 分类动作清单",""])
    for key,label in section_order:
        rows=[r for r in payload["watch_items"] if r["category"]==key]
        summary="；".join(f"{r['market']} → {r.get('independent_model_direction') or '无方向'} / {r['final_action']}" for r in rows)
        if not summary and key == "no_bet" and not payload["watch_items"]:
            summary = "今日无值得下注盘口，保持现金"
        summary = summary or "无"
        lines.append(f"- **{label}**：{summary}")
    lines.extend(["","## 候选详细研究",""])
    for index, row in enumerate(payload["watch_items"], 1):
        ci = row["confidence_interval"]
        lines.extend([f"## {index}. {row['market']}", "",
            f"- 优先级：`{row['event_phase']}`；分类：**{row['category_label']}**；研究方向：`{row.get('independent_model_direction') or '—'}`；下注方向：`{row['bet_direction']}`；最终动作：`{row['final_action']}`", "",
            "| 字段 | 值 |", "|---|---|", f"| 可执行价格 | {fmt(row['executable_price'])} |",
            f"| 开赛时间 / 直播比分 | {row.get('game_start_time') or '—'} / {row.get('live_score') or '—'} |",
            f"| 直播数据状态 / 年龄 | {row.get('live_data_status')} / {fmt(row.get('live_data_age_seconds'), 0)} 秒 |",
            f"| 模型估计概率 | {fmt(row['model_estimated_probability'])} |",
            f"| 独立模型方向 | {row.get('independent_model_direction') or '—'} |",
            f"| 市场共识方向（仅基准） | {row.get('market_consensus_side') or '—'} / {fmt(row.get('market_consensus_probability_proxy'))} |",
            f"| 可信区间 | {fmt(ci[0])} – {fmt(ci[1])} |",
            f"| 独立 OOS/forward 样本 | {row['oos_or_forward_samples']} |",
            f"| 历史命中率 | {fmt(row['historical_hit_rate'])} |",
            f"| 模型/市场 Brier | {fmt(row.get('model_brier_score'))} / {fmt(row.get('market_brier_score'))} |",
            f"| 模型/市场 Log Loss | {fmt(row.get('model_log_loss'))} / {fmt(row.get('market_log_loss'))} |",
            f"| 净价格优势/份 | {fmt(row['net_edge_per_share'])} |",
            f"| 最高接受价格 | {fmt(row['maximum_acceptable_price'])} |",
            f"| 建议 paper 金额 | ${row['suggested_paper_amount_usd']:.2f}（权益 {row['suggested_equity_pct']:.2f}%） |",
            f"| 推荐类型 | {row['recommendation_type']} |",
            f"| 推荐等级 / 概率口径 | {row['recommendation_level']} / {row['probability_status']} |",
            f"| 基础率 / 样本 | {fmt(row.get('historical_base_rate'))} / {row.get('historical_base_rate_samples') or '—'} |",
            f"| 最晚入场时间 | {row.get('latest_entry_time') or '—'} |",
            f"| 手续费/额外滑点 | {fmt(row.get('entry_fee_per_share'))} / {fmt(row.get('extra_slippage_per_share'))} |",
            f"| Spread / 冲击 | {fmt(row['spread'])} / {fmt(row['price_impact'])} |",
            f"| 当前可卖价 / 概率-卖价 gap | {fmt(row.get('current_executable_sell_price'))} / {fmt(row.get('research_probability_minus_sell_price'))} |",
            f"| 盘中管理 | {row.get('position_management_mode')}：{row.get('position_management_instruction')} |",
            f"| +10¢ 止盈线 / +20¢ 收本线 | {fmt(row.get('profit_lock_trigger_price'))} / {fmt(row.get('recover_stake_trigger_price'))} |",
            f"| 建议卖出比例 / 可持有至结算 | {float(row.get('suggested_sell_fraction') or 0):.0%} / {str(bool(row.get('hold_to_settlement_allowed'))).lower()} |",
            f"| 尾部风险 | {row.get('tail_risk_label') or '—'} |", ""])
        side_tests = row.get("execution_tests_by_side") or {}
        lines.extend(["盘口测试（仅用于判断能否成交，不代表下注方向；市场共识也不是独立模型概率）：", "",
            "| 测试侧 | 可执行价格 | Spread | 价格冲击 | 可成交 |",
            "|---|---:|---:|---:|---|"])
        for side in ("YES", "NO"):
            test = side_tests.get(side) or {}
            lines.append(f"| {side} | {fmt(test.get('executable_price'))} | {fmt(test.get('spread'))} | {fmt(test.get('price_impact'))} | {str(bool(test.get('fillable'))).lower()} |")
        lines.append("")
        failures = row["failed_gates"] or ["none"]
        lines.extend(["主要依据："+("；".join(row.get("key_evidence") or []) or "尚缺事件级证据。"),"", "未通过门槛：" + ", ".join(f"`{item}`" for item in failures) + "。", "",
            "失败路径：" + (json.dumps(row["failure_paths"], ensure_ascii=False) if row["failure_paths"] else "尚未形成可验证清单。"), ""])
        if row.get("conditional_trigger"):lines.extend([f"精确等待条件：{row['conditional_trigger']}",""])
        if row.get("settlement_rules"):lines.extend([f"结算规则：{row['settlement_rules']}",""])
        if row.get("calibration_disclaimer"):lines.extend([f"概率声明：**{row['calibration_disclaimer']}**。",""])
        if row.get("research_sources"):
            lines.extend(["来源："+"；".join(f"[{s.get('name')}]({s.get('url')})（{s.get('kind')}，{s.get('fresh_at') or '时间未提供'}）" for s in row["research_sources"]),""])
        if row["exit_conditions"]: lines.extend(["退出条件：" + ", ".join(f"`{item}`" for item in row["exit_conditions"]) + "。", ""])
    lines.extend(["## 口径与仓位门", "",
        "未经至少 30 个同维度独立 OOS/forward 样本校准的输出，只称“模型估计概率”，不称“真实胜率”。",
        "Alpha 主推荐按总权益 2%–5%；高胜率小收益推荐按 0.5%–2%，实验性研究推荐按 0.5%–1%。系统不得自动升级真钱参数。", "",
        "## 结算、错题本与定期复盘入口","",
        "- [每日结算与 Back Case](CURRENT_DAILY_SETTLEMENT_REVIEW.md)","- [错题本](CURRENT_ERROR_NOTEBOOK.md)","- [每周复盘](CURRENT_WEEKLY_REVIEW.md)","- [每月复盘](CURRENT_MONTHLY_REVIEW.md)",""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", default=str(DEFAULT_DAILY)); parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT)); parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--research",default=str(DEFAULT_RESEARCH));parser.add_argument("--archive-root",default=str(ROOT/"reports/daily"))
    args = parser.parse_args(); daily = core.read_json(Path(args.daily)); ledger = core.read_json(Path(args.ledger)); policy = core.read_json(ROOT / "config/policy.json")
    research=core.read_json(Path(args.research)) if Path(args.research).exists() else {"research_items":[]};daily=merge_event_research(daily,research)
    runner=ROOT/"experiments/current-validation-runner.json"
    if runner.exists():daily["full_market_scanned"]=(core.read_json(runner).get("scan") or {}).get("markets_scanned")
    payload = build(daily, ledger, policy)
    payload["closest_candidate"] = (research.get("scan_summary") or {}).get("closest_candidate")
    rendered=markdown(payload);core.write_json(Path(args.output), payload); Path(args.report).write_text(rendered, encoding="utf-8")
    stamp=dt.datetime.fromisoformat(payload["created_at"].replace("Z","+00:00"));folder=Path(args.archive_root)/stamp.strftime("%Y-%m-%d");folder.mkdir(parents=True,exist_ok=True);base=folder/f"manual-decision-{stamp.strftime('%H%M%S-%f')}"
    (base.with_suffix(".md")).write_text(rendered,encoding="utf-8");core.write_json(base.with_suffix(".json"),payload);payload["immutable_archive_markdown"]=str(base.with_suffix(".md").relative_to(ROOT));payload["immutable_archive_json"]=str(base.with_suffix(".json").relative_to(ROOT));core.write_json(Path(args.output),payload)
    print(json.dumps({key: payload[key] for key in ("status", "decision", "scanned_1_24h_markets", "watch_item_count", "recommendation_count")}, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
