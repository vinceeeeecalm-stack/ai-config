#!/usr/bin/env python3
"""Daily Polymarket football-first, tennis-second human decision report.

The scanner is public-data and paper-only.  It discovers sports by the official
Gamma sports tags and uses the sports kickoff field (Gamma ``endDate`` /
``gameStartTime``), rather than the generic market resolution horizon.  An
external, traceable research artifact is required before any direction can be
recommended; market prices are never promoted into model probabilities.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
FOOTBALL_TAG_ID = 100350
TENNIS_TAG_ID = 864
LOCAL = ZoneInfo("Asia/Shanghai")
DEFAULT_RESEARCH = ROOT / "experiments/current-sports-event-research.json"
DEFAULT_OUTPUT = ROOT / "experiments/current-daily-sports-decision.json"
DEFAULT_REPORT = ROOT / "reports/CURRENT_DAILY_SPORTS_DECISION.md"
DEFAULT_LEDGER = ROOT / "data/sports_decision_observation_ledger.json"
DEFAULT_SETTLEMENT = ROOT / "experiments/current-sports-settlement-review.json"
DEFAULT_SETTLEMENT_REPORT = ROOT / "reports/CURRENT_SPORTS_SETTLEMENT_REVIEW.md"
DEFAULT_RESEARCH_QUEUE = ROOT / "experiments/current-sports-research-queue.json"
DEFAULT_RESEARCH_QUEUE_REPORT = ROOT / "reports/CURRENT_SPORTS_RESEARCH_QUEUE.md"
PROP_SUFFIXES = (
    " - halftime result", " - second half result", " - exact score",
    " - first team to score", " - more markets", " - spread", " - total",
    " - both teams to score", " - winning margin", " - set betting",
)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader
    spec.loader.exec_module(module); return module


core = load("sports_report_core", ROOT / "scripts/polymarket_alpha.py")
public = load("sports_report_public", ROOT / "scripts/polymarket_public_data.py")


def parse_time(value: Any) -> dt.datetime | None:
    return core.parse_iso(value)


def kickoff_of(event: dict[str, Any]) -> dt.datetime | None:
    values = []
    for market in event.get("markets") or []:
        parsed = parse_time(market.get("gameStartTime"))
        if parsed: values.append(parsed)
    return min(values) if values else parse_time(event.get("eventStartTime") or event.get("endDate"))


def event_phase(kickoff: dt.datetime | None, now: dt.datetime) -> tuple[str, int]:
    if not kickoff: return "kickoff_missing", 9
    hours = (kickoff - now).total_seconds() / 3600
    if -4 <= hours <= 3: return ("possibly_live_unverified", 0) if hours <= 0 else ("upcoming_0_6h", 1)
    if 3 < hours <= 6: return "upcoming_0_6h", 1
    if kickoff.astimezone(LOCAL).date() == now.astimezone(LOCAL).date(): return "same_day", 2
    return "next_24h", 3


def canonical_event(event: dict[str, Any], sport: str) -> bool:
    title = str(event.get("title") or "").lower()
    if any(title.endswith(suffix) for suffix in PROP_SUFFIXES): return False
    markets = [row for row in event.get("markets") or [] if row.get("active") is True and row.get("closed") is not True]
    if not markets or not event.get("gameId"): return False
    if sport == "football":
        return len(markets) == 3 and any("draw" in str(row.get("question") or "").lower() for row in markets)
    return " vs" in title or " v " in title


def fetch_tag_events(tag_id: int, sport: str, now: dt.datetime, pages: int = 12) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    start = now - dt.timedelta(hours=4); end = now + dt.timedelta(hours=30)
    cursor = None; rows: list[dict[str, Any]] = []; requests = []
    for _ in range(pages):
        params: dict[str, Any] = {
            "active": "true", "closed": "false", "tag_id": tag_id,
            "start_time_min": start.isoformat(), "start_time_max": end.isoformat(),
            # Gamma's current events/keyset contract rejects the legacy
            # start_date ordering parameters with HTTP 422. The time window
            # remains explicit; sort locally after the response instead.
            "limit": 100,
        }
        if cursor: params["after_cursor"] = cursor
        url = f"{public.GAMMA_BASE}/events/keyset?{urlencode(params)}"
        try:
            payload = public.get_json(url, timeout=30, retries=1)
            page = payload.get("events") or []; cursor = payload.get("next_cursor")
            requests.append({"url": url, "status": "ok", "rows": len(page), "next_cursor": bool(cursor)})
            rows.extend({**event, "_sport": sport} for event in page if canonical_event(event, sport))
            if not cursor or not page: break
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
            break
    unique = {str(row.get("gameId")): row for row in rows}
    return sorted(unique.values(), key=lambda row: kickoff_of(row) or dt.datetime.max.replace(tzinfo=dt.timezone.utc)), requests


def role_for_market(event: dict[str, Any], market: dict[str, Any], index: int) -> str:
    question = str(market.get("question") or "").lower()
    if "draw" in question: return "DRAW"
    if event.get("_sport") == "football": return "HOME" if index == 0 else "AWAY"
    return "PLAYER_1" if index == 0 else "PLAYER_2"


def execute_market(event: dict[str, Any], market: dict[str, Any], index: int, notional: float = 25.0) -> dict[str, Any]:
    aux = public.fetch_market_aux(market, True, False)
    tokens = public.token_map(market); by_side = {}
    for side, outcome in (("YES", "yes"), ("NO", "no")):
        token = next((token for name, token in tokens.items() if name.lower() == outcome), None)
        book = (aux.get("books") or {}).get(token) if token else None
        simulation = core.simulate_buy(book or {}, notional, 5.0)
        by_side[side] = {**simulation, "token_id": token}
    return {
        "market_id": str(market.get("id") or ""), "condition_id": market.get("conditionId"),
        "market_slug": market.get("slug"), "question": market.get("question"),
        "role": role_for_market(event, market, index), "execution_by_side": by_side,
        "fee_contract": public.fee_contract(market, aux), "rules": market.get("description") or event.get("description"),
        "resolution_source": market.get("resolutionSource") or event.get("resolutionSource"),
        "book_errors": aux.get("errors") or [],
    }


def research_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("event_slug")): row for row in payload.get("research_items") or [] if row.get("event_slug")}


def source_gate(row: dict[str, Any]) -> tuple[bool, dict[str, int]]:
    counts = {"official": 0, "independent": 0}
    for source in row.get("sources") or []:
        kind = str(source.get("kind") or "").lower()
        if "official" in kind: counts["official"] += 1
        elif "independent" in kind: counts["independent"] += 1
    return counts["official"] >= 1 and counts["independent"] >= 2, counts


def live_gate(research: dict[str, Any], now: dt.datetime) -> tuple[bool, float | None]:
    updated = parse_time((research.get("live_state") or {}).get("updated_at"))
    age = (now - updated).total_seconds() if updated else None
    return bool(age is not None and -30 <= age <= 120 and (research.get("live_state") or {}).get("score") is not None), age


def build_candidate(event: dict[str, Any], markets: list[dict[str, Any]], research: dict[str, Any] | None,
                    now: dt.datetime, policy: dict[str, Any]) -> dict[str, Any]:
    kickoff = kickoff_of(event); phase, priority = event_phase(kickoff, now)
    mids = []
    for market in markets:
        ex = market["execution_by_side"]["YES"]
        bid, ask = ex.get("best_bid"), ex.get("best_ask")
        mids.append((float(bid) + float(ask)) / 2 if bid is not None and ask is not None else None)
    required_roles = {"HOME", "DRAW", "AWAY"} if event.get("_sport") == "football" else None
    observed_roles = {str(market.get("role")) for market in markets}
    consensus_complete = bool(mids) and all(value is not None for value in mids)
    if required_roles is not None:
        consensus_complete = consensus_complete and observed_roles == required_roles and len(markets) == 3
    total = sum(mids) if consensus_complete else 0
    consensus = [{"role": market["role"], "probability": mids[i] / total if total else None}
                 for i, market in enumerate(markets)]
    consensus_best = (max(consensus, key=lambda row: row["probability"])
                      if total else {"role": None, "probability": None})
    consensus_market = next((market for market in markets if market.get("role") == consensus_best["role"]), None) or {}
    consensus_execution = (consensus_market.get("execution_by_side") or {}).get("YES") or {}
    base = {
        "event_id": str(event.get("id")), "game_id": str(event.get("gameId")), "event_slug": event.get("slug"),
        "title": event.get("title"), "sport": event.get("_sport"), "series": ((event.get("series") or [{}])[0]).get("title"),
        "kickoff_at": kickoff.isoformat() if kickoff else None,
        "kickoff_beijing": kickoff.astimezone(LOCAL).isoformat() if kickoff else None,
        "event_phase": phase, "event_priority": priority, "market_consensus": consensus,
        "market_consensus_complete": bool(total),
        "market_consensus_direction": consensus_best["role"], "market_consensus_probability": consensus_best["probability"],
        "market_consensus_executable_price": consensus_execution.get("fill_price") or consensus_execution.get("best_ask"),
        "market_consensus_executable_sell_price": consensus_execution.get("best_bid"),
        "market_consensus_is_research_probability": False, "markets": markets,
        "paper_only": True, "real_money_execution_authorized": False,
    }
    if not research:
        return {**base, "decision_status": "pass", "final_action": "NO_BET", "direction": "NO_BET",
                "research_status": "missing",
                "research_probability": None, "confidence_interval": [None, None], "net_edge_per_share": None,
                "executable_price": base.get("market_consensus_executable_price"),
                "current_executable_sell_price": base.get("market_consensus_executable_sell_price"),
                "maximum_acceptable_price": None, "failed_gates": ["independent_event_research_missing"],
                "research_queue_status": "candidate",
                "research_missing_fields": ["official_source", "two_independent_sources", "independent_probability", "confidence_interval", "lineup_injuries", "failure_paths"],
                "conditional_trigger": "补齐官方来源、两个独立来源、概率区间、阵容/伤病和失败路径后重研。"}
    target = next((row for row in markets if str(row.get("market_id")) == str(research.get("target_market_id"))
                   or str(row.get("market_slug")) == str(research.get("target_market_slug"))), None)
    side = str(research.get("target_side") or "YES").upper(); probability = research.get("research_probability")
    source_ok, source_counts = source_gate(research); rules_ok = research.get("rules_clear") is True
    failures = []
    if research.get("research_status") not in {"complete", "experimental_complete", "validated"}: failures.append("research_incomplete")
    if not source_ok: failures.append("source_gate_failed")
    if not rules_ok: failures.append("rules_unclear")
    if not target: failures.append("target_market_missing")
    if probability is None: failures.append("research_probability_missing")
    execution = ((target or {}).get("execution_by_side") or {}).get(side) or {}
    price = execution.get("fill_price"); spread = execution.get("spread"); impact = execution.get("price_impact")
    if execution.get("fillable") is not True: failures.append("not_fillable")
    if spread is None or float(spread) > .02: failures.append("spread_above_2c")
    if impact is None or float(impact) > .01: failures.append("impact_above_1c")
    fee = 0.0
    fee_rate = ((target or {}).get("fee_contract") or {}).get("fee_rate")
    if price is not None and fee_rate:
        fee = float(fee_rate) * float(price) * (1 - float(price))
    discount = float(policy["entry_gate"].get("uncertainty_discount", .01)) + float(policy["entry_gate"].get("resolution_risk_reserve", .005))
    net_edge = float(probability) - float(price) - fee - discount if probability is not None and price is not None else None
    alpha_max_price = float(probability) - fee - discount - .05 if probability is not None else None
    max_price = (float(research["maximum_acceptable_price"]) if research.get("maximum_acceptable_price") is not None
                 else alpha_max_price)
    if probability is not None and float(probability) < .60: failures.append("research_probability_below_60pct")
    if net_edge is None or net_edge < .05: failures.append("net_edge_below_5c")
    live_ok, live_age = live_gate(research, now)
    if phase == "possibly_live_unverified" and not live_ok: failures.append("live_state_stale_or_missing")
    hard = {"research_incomplete", "source_gate_failed", "rules_unclear", "target_market_missing", "research_probability_missing", "not_fillable", "spread_above_2c", "impact_above_1c"}
    if not failures and phase != "possibly_live_unverified": status, action = "pre_match_entry", f"BUY_{side}"
    elif not failures and live_ok: status, action = "watch_live_first", f"BUY_{side}_ONLY_IF_LIVE_THESIS_CONFIRMED"
    elif not hard.intersection(failures) and research.get("conditional_trigger"): status, action = "watch_live_first" if phase == "possibly_live_unverified" else "late_recheck", "WAIT"
    else: status, action = "pass", "NO_BET"
    return {**base, "decision_status": status, "final_action": action,
            "research_status": research.get("research_status"),
            "direction": research.get("direction") or (target or {}).get("role") or "NO_BET", "target_side": side,
            "target_market_id": (target or {}).get("market_id"), "target_question": (target or {}).get("question"),
            "research_probability": probability, "confidence_interval": [research.get("confidence_low"), research.get("confidence_high")],
            "probability_status": "calibrated" if int(research.get("calibrated_oos_samples") or 0) >= 30 else "research_estimate_unvalidated",
            "calibrated_oos_samples": int(research.get("calibrated_oos_samples") or 0), "source_counts": source_counts,
            "executable_price": price, "current_executable_sell_price": execution.get("best_bid"), "spread": spread,
            "price_impact": impact, "fee_per_share": fee, "uncertainty_and_rule_discount": discount,
            "net_edge_per_share": net_edge, "maximum_acceptable_price": max_price,
            "alpha_maximum_acceptable_price": alpha_max_price, "failed_gates": sorted(set(failures)),
            "method": research.get("method"), "key_evidence": research.get("key_evidence") or [],
            "historical_base_rate": research.get("historical_base_rate"), "historical_base_rate_samples": research.get("historical_base_rate_samples"),
            "first_goal_profile": research.get("first_goal_profile"), "trailing_response": research.get("trailing_response"),
            "lead_protection": research.get("lead_protection"), "failure_paths": research.get("failure_paths") or [],
            "strength_classification": research.get("strength_classification"),
            "tennis_profile": research.get("tennis_profile") or {},
            "conditional_trigger": research.get("conditional_trigger"), "live_playbook": research.get("live_playbook") or {},
            "exit_plan": research.get("exit_plan") or {}, "settlement_rules": research.get("settlement_rules"),
            "sources": research.get("sources") or [], "live_state": research.get("live_state"), "live_data_age_seconds": live_age,
            "research_missing_fields": research.get("research_missing_fields") or [],
            "suggested_equity_pct": research.get("suggested_equity_pct", [1.0, 2.0]),
            "estimated_loss_probability": 1 - float(probability) if probability is not None else None,
            "validated_80pct_standard_met": bool(int(research.get("calibrated_oos_samples") or 0) >= 30 and float(probability or 0) >= .80),
        }


def build(events: list[dict[str, Any]], research_payload: dict[str, Any], policy: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    research = research_index(research_payload); candidates = []
    eligible = []
    for event in events:
        kickoff = kickoff_of(event)
        if not kickoff or not (-4 <= (kickoff - now).total_seconds() / 3600 <= 24): continue
        phase, priority = event_phase(kickoff, now)
        gamma_prices = []
        for market in event.get("markets") or []:
            try: gamma_prices.append(float(market.get("bestAsk")))
            except (TypeError, ValueError): pass
        eligible.append((0 if event.get("_sport") == "football" else 1, priority,
                         -max(gamma_prices or [0]), kickoff, event))
    eligible.sort(key=lambda item: item[:4])
    football = [item[-1] for item in eligible if item[-1].get("_sport") == "football"][:12]
    tennis = [item[-1] for item in eligible if item[-1].get("_sport") == "tennis"][:6]
    promoted = [item[-1] for item in eligible if str(item[-1].get("slug")) in research]
    deep_events = list({str(event.get("id")): event for event in promoted + football + tennis}.values())
    executed_by_event: dict[str, dict[int, dict[str, Any]]] = {str(event.get("id")): {} for event in deep_events}
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {}
        for event in deep_events:
            for index, market in enumerate(event.get("markets") or []):
                futures[pool.submit(execute_market, event, market, index)] = (str(event.get("id")), index)
        for future in as_completed(futures):
            event_id, index = futures[future]
            try: executed_by_event[event_id][index] = future.result()
            except Exception as exc:
                executed_by_event[event_id][index] = {"market_id": "", "condition_id": None, "market_slug": None,
                    "question": "order book unavailable", "role": "UNKNOWN", "execution_by_side": {"YES": {}, "NO": {}},
                    "fee_contract": {"status": "missing"}, "rules": None, "resolution_source": None,
                    "book_errors": [f"{type(exc).__name__}:{exc}"]}
    for event in deep_events:
        executed = [executed_by_event[str(event.get("id"))][index] for index in sorted(executed_by_event[str(event.get("id"))])]
        candidate = build_candidate(event, executed, research.get(str(event.get("slug"))), now, policy)
        candidate["display_status"] = operation_status(candidate)
        candidate["operation_instruction"] = operation_instruction(candidate)
        candidate["exit_instruction"] = exit_instruction(candidate)
        candidates.append(candidate)
    candidates.sort(key=lambda row: (0 if row["sport"] == "football" else 1, row["event_priority"],
                                     -(row.get("market_consensus_probability") or 0), row.get("kickoff_at") or ""))
    entries = [row for row in candidates if row["decision_status"] == "pre_match_entry"][:1]
    conditional = [row for row in candidates if row["decision_status"] in {"watch_live_first", "late_recheck"} and row not in entries][:1]
    experimental_value = [row for row in candidates if row.get("research_status") in {"complete", "experimental_complete", "validated"}
                          and row.get("net_edge_per_share") is not None and float(row["net_edge_per_share"]) > 0
                          and row not in entries and row not in conditional][:1]
    closest = [row for row in candidates if row["decision_status"] == "pass" and row.get("research_probability") is not None][:1]
    research_queue = build_research_queue(candidates, now)
    queued_slugs = {row.get("event_slug") for row in research_queue}
    for row in candidates:
        if row.get("research_probability") is None:
            row["research_queue_status"] = ("terminal_insufficient" if row.get("research_status") == "insufficient" else
                                            ("queued" if row.get("event_slug") in queued_slugs else "deferred_next_cycle"))
            row["display_status"] = operation_status(row)
            row["operation_instruction"] = operation_instruction(row)
    high_probability_alerts = build_high_probability_alerts(candidates)
    priority_candidates = [{**row, "candidate_rank": index + 1} for index, row in enumerate(high_probability_alerts[:2])]
    relay_schedule = build_relay_schedule(candidates)
    conclusion = (f"今日可立即入场：{entries[0]['title']}，{entries[0]['final_action']}。" if entries else
                  (f"今日优先候选：第一候选 {priority_candidates[0]['title']}"
                   + (f"；第二候选 {priority_candidates[1]['title']}" if len(priority_candidates) > 1 else "")
                   + "；按候选表的价格等待、盘中确认或临场研究指令执行。" if priority_candidates else
                   "今日没有达到候选提醒线的赛事，保持观察。"))
    return {
        "schema_version": "polymarket-daily-sports-decision-v1", "created_at": now.isoformat(),
        "report_date_beijing": now.astimezone(LOCAL).date().isoformat(), "one_line_conclusion": conclusion,
        "scanned_event_count": len(eligible), "deep_research_candidate_count": len(candidates),
        "football_event_count": sum(item[-1].get("_sport") == "football" for item in eligible),
        "tennis_event_count": sum(item[-1].get("_sport") == "tennis" for item in eligible), "main_recommendations": entries,
        "conditional_candidates": conditional, "experimental_value_candidates": experimental_value,
        "closest_candidates": closest,
        "high_probability_alerts": high_probability_alerts, "priority_candidates": priority_candidates,
        "relay_schedule": relay_schedule, "research_queue": research_queue,
        "all_candidates": candidates,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        "real_money_execution_authorized": False, "manual_confirmation_required": True,
    }


def exit_instruction(row: dict[str, Any]) -> str:
    if row.get("research_probability") is None:
        return "只作强热门提醒；独立研究完成前不入场，也不制定虚假退出价。"
    price = row.get("executable_price")
    max_price = row.get("maximum_acceptable_price")
    edge = row.get("net_edge_per_share")
    if price is not None and max_price is not None and float(price) > float(max_price):
        return "当前不追价；若已有低价仓位，首球后或可执行bid上涨约20c时先回收本金，80c以上优先大幅退出。"
    if row.get("decision_status") == "pre_match_entry" and float(row.get("research_probability") or 0) >= .78:
        return "可持有至终场，但首球后仍应按可执行bid回收部分本金；红牌、核心伤退或场面反转立即退出。"
    if row.get("decision_status") in {"watch_live_first", "late_recheck"}:
        return "先等盘中触发；入场后优先利用首球或约20c上涨分批卖出，不默认死拿到终场。"
    return "不入场；仅保留临场复核提醒。"


def operation_instruction(row: dict[str, Any]) -> str:
    if row.get("research_status") == "insufficient":
        missing = ", ".join(row.get("research_missing_fields") or ["关键证据"])
        return f"研究完成但证据不足：缺少{missing}；本场冻结，不继续等待或生成虚假概率。"
    if row.get("decision_status") == "pre_match_entry":
        return f"可入场：只在可执行ask不高于{fnum(row.get('maximum_acceptable_price'))}时挂限价。"
    if row.get("research_probability") is not None:
        price = row.get("executable_price")
        max_price = row.get("maximum_acceptable_price")
        if price is not None and max_price is not None and float(price) > float(max_price):
            return f"等待价格：当前{fnum(price)}，只在不高于{fnum(max_price)}且实时论点仍成立时考虑；已有仓位按退出规则处理。"
        return "盘中确认：在指定观察点核验比分、分钟、机会质量和最新盘口后再决定。"
    if row.get("research_queue_status") == "queued" or float(row.get("market_consensus_probability") or 0) >= .60:
        return "自动深研排队中：系统将补齐阵容、近况、规则、官方+双独立来源和独立估值；完成前市场概率只作热门提示。"
    if row.get("research_queue_status") == "deferred_next_cycle":
        return "下一轮研究：当前优先级低于本轮Top 4，保留盘口和时间点，下一小时重新排序。"
    return "跳过：当前既无独立研究，也没有足够强的市场热门信号。"


def operation_status(row: dict[str, Any]) -> str:
    if row.get("research_status") == "insufficient":
        return "研究完成／证据不足"
    if row.get("decision_status") == "pre_match_entry": return "可入场"
    if row.get("research_probability") is not None:
        price, max_price = row.get("executable_price"), row.get("maximum_acceptable_price")
        if price is not None and max_price is not None and float(price) > float(max_price): return "重点候选/价格等待"
        return "重点候选/盘中确认"
    if row.get("research_queue_status") == "queued":
        if float(row.get("market_consensus_probability") or 0) >= .60:
            return "强热门候选／研究排队中"
        return "跳过"
    if float(row.get("market_consensus_probability") or 0) >= .60: return "强热门候选／研究排队中"
    if row.get("research_queue_status") == "deferred_next_cycle": return "跳过"
    return "跳过"


def build_research_queue(candidates: list[dict[str, Any]], now: dt.datetime | None = None) -> list[dict[str, Any]]:
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = []
    for row in candidates:
        if row.get("research_probability") is not None:
            continue
        if row.get("research_status") == "insufficient":
            continue
        if row.get("event_phase") == "possibly_live_unverified":
            kickoff = parse_time(row.get("kickoff_at"))
            age_hours = (now - kickoff).total_seconds() / 3600 if kickoff else 99
            if row.get("executable_price") is None or age_hours > 2.25:
                continue
        rows.append(row)
    rows.sort(key=lambda row: (row.get("event_priority", 9), 0 if row.get("sport") == "football" else 1,
                               -float(row.get("market_consensus_probability") or 0)))
    return [{"queue_rank": index + 1, "event_slug": row.get("event_slug"), "game_id": row.get("game_id"),
             "title": row.get("title"), "sport": row.get("sport"), "series": row.get("series"),
             "kickoff_beijing": row.get("kickoff_beijing"), "event_phase": row.get("event_phase"),
             "market_consensus_direction": row.get("market_consensus_direction"),
             "market_consensus_probability": row.get("market_consensus_probability"),
             "executable_price": row.get("executable_price"), "status": "queued",
             "priority_reason": "market_strong_favorite" if float(row.get("market_consensus_probability") or 0) >= .60 else "time_window_top_candidate",
             "missing_fields": row.get("research_missing_fields") or [],
             "required_sources": {"official": 1, "independent": 2},
             "output_contract": "polymarket-sports-event-research-v1",
             "output_path": "experiments/current-sports-event-research.json"}
            for index, row in enumerate(rows[:4])]


def build_high_probability_alerts(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alerts = []
    for row in candidates:
        market_probability = float(row.get("market_consensus_probability") or 0)
        research_probability = float(row.get("research_probability") or 0)
        positive_research_value = research_probability > 0 and row.get("net_edge_per_share") is not None and float(row["net_edge_per_share"]) > 0
        if max(market_probability, research_probability) < .60 and not positive_research_value:
            continue
        if positive_research_value and research_probability < .60:
            alert_level = "实验价值候选／中等胜率"
        elif research_probability >= .78 and row.get("net_edge_per_share") is not None and float(row["net_edge_per_share"]) > 0:
            alert_level = "高胜率且价格可研究"
        elif research_probability >= .60:
            alert_level = "强队高亮／价格或证据未通过"
        else:
            alert_level = "市场强热门／尚无独立研究概率"
        alerts.append({**row, "alert_level": alert_level, "operation_instruction": operation_instruction(row),
                       "exit_instruction": exit_instruction(row)})
    alerts.sort(key=lambda row: (0 if row["sport"] == "football" else 1, row["event_priority"],
                                 -max(float(row.get("research_probability") or 0), float(row.get("market_consensus_probability") or 0))))
    return alerts[:6]


def build_relay_schedule(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        kickoff = parse_time(row.get("kickoff_at"))
        if not kickoff:
            continue
        local = kickoff.astimezone(LOCAL)
        bucket_hour = (local.hour // 2) * 2
        key = f"{local:%Y-%m-%d} {bucket_hour:02d}:00–{(bucket_hour + 2) % 24:02d}:00"
        buckets.setdefault(key, []).append(row)
    schedule = []
    status_rank = {"pre_match_entry": 0, "watch_live_first": 1, "late_recheck": 2, "pass": 3}
    for window, rows in buckets.items():
        rows.sort(key=lambda row: (status_rank.get(row.get("decision_status"), 9),
                                   0 if row.get("research_probability") is not None else 1,
                                   -float(row.get("market_consensus_probability") or 0)))
        for index, best in enumerate(rows[:2]):
            schedule.append({"time_window": window, "candidate_rank": index + 1, **best,
                             "operation_instruction": operation_instruction(best),
                             "exit_instruction": exit_instruction(best)})
    schedule.sort(key=lambda row: (row["time_window"], row["candidate_rank"]))
    return schedule


def fnum(value: Any, digits: int = 3) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def display_direction(row: dict[str, Any]) -> str:
    if row.get("research_probability") is not None:
        return f"{row.get('direction') or row.get('market_consensus_direction') or '—'} / {row.get('target_side') or '—'}"
    return f"{row.get('market_consensus_direction') or '—'}（市场热门）"


def markdown(payload: dict[str, Any]) -> str:
    lines = ["# 每日 Polymarket 体育竞技专业决策报告", "", f"**{payload['one_line_conclusion']}**", "",
             f"- 报告时间：`{payload['created_at']}`（北京时间日期 `{payload['report_date_beijing']}`）",
             f"- 扫描：足球 {payload['football_event_count']} 场；网球 {payload['tennis_event_count']} 场；正式推荐 {len(payload['main_recommendations'])} 场；条件候选 {len(payload['conditional_candidates'])} 场",
             "- 当前最大动作：paper-only 研究与人工建议；真钱未授权。", "- 每日保证出报告，不保证每日存在正 EV 盘口。", ""]
    lines += ["## 北京时间赛程表", "", "| 时间 | 项目/赛事 | 比赛 | 状态 | 市场热门方向/概率 | 最终动作 |", "|---|---|---|---|---|---|"]
    for row in payload["all_candidates"]:
        lines.append(f"| {row.get('kickoff_beijing') or '—'} | {row['sport']} / {row.get('series') or '—'} | {row['title']} | {row['display_status']} | {row.get('market_consensus_direction')} / {fnum(row.get('market_consensus_probability'))} | {row['operation_instruction']} |")
    if not payload["all_candidates"]: lines.append("| — | — | 当前窗口没有可交易足球/网球主赛事 | pass | — | NO_BET |")
    lines += ["", "## 今日第一、第二候选", "",
              "> 候选排名回答“今天优先研究谁”，不等于无条件市价买入。操作栏会明确告诉你是买、等价格、看盘中还是先补研究。", "",
              "| 排名 | 比赛与方向 | 胜率依据 | 当前价格 | 具体操作 | 退出路径 |", "|---:|---|---|---:|---|---|"]
    for row in payload.get("priority_candidates") or []:
        basis = (f"研究概率 {fnum(row.get('research_probability'))}" if row.get("research_probability") is not None
                 else f"市场共识 {fnum(row.get('market_consensus_probability'))}（非独立胜率）")
        lines.append(f"| 第{row['candidate_rank']}候选 | {row['title']} / {display_direction(row)} | {basis} | {fnum(row.get('executable_price'))} | {row['operation_instruction']} | {row['exit_instruction']} |")
    if not payload.get("priority_candidates"): lines.append("| — | 当前没有达到候选提醒线的赛事 | — | — | 保持观察 | — |")
    lines += ["", "## 高胜率／强队重点提醒", "",
              "> 本区回答“哪队最可能赢”；它不自动等于“当前价格值得买”。市场概率会明确标为市场共识，不能冒充独立研究胜率。", "",
              "| 比赛 | 强提醒等级 | 热门方向 | 市场共识 | 研究概率 | 买价 / 最高价 | 操作状态 |", "|---|---|---|---:|---:|---:|---|"]
    for row in payload.get("high_probability_alerts") or []:
        lines.append(f"| {row['title']} | {row['alert_level']} | {row.get('market_consensus_direction') or '—'} | {fnum(row.get('market_consensus_probability'))} | {fnum(row.get('research_probability'))} | {fnum(row.get('executable_price'))} / {fnum(row.get('maximum_acceptable_price'))} | {row['operation_instruction']} |")
    if not payload.get("high_probability_alerts"): lines.append("| — | 当前没有达到60%市场热门或研究概率的赛事 | — | — | — | — | 保持现金 |")
    lines += ["", "## 分时接力执行表", "",
              "> 每个两小时时段最多保留第一、第二候选。前一场结束或退出后再复核下一场，不能把多个短价热门串成无条件连投。", "",
              "| 北京时间段 | 排名 | 比赛与方向 | 具体操作 | 入场上限 | 退出方式 |", "|---|---:|---|---|---:|---|"]
    for row in payload.get("relay_schedule") or []:
        lines.append(f"| {row['time_window']} | 第{row['candidate_rank']}候选 | {row['title']} / {display_direction(row)} | {row['operation_instruction']} | {fnum(row.get('maximum_acceptable_price'))} | {row['exit_instruction']} |")
    if not payload.get("relay_schedule"): lines.append("| — | — | 无 | 保持观察 | — | — |")
    lines += ["", "## 自动独立研究队列", "",
              "> 强热门不会再因为研究文件缺失而直接消失；队列只表示系统正在补证据，不表示已经允许买入。", "",
              "| 队列 | 比赛 | 开赛时间 | 市场热门 | 当前价格 | 状态 | 缺失证据 |", "|---:|---|---|---:|---:|---|---|"]
    for row in payload.get("research_queue") or []:
        lines.append(f"| {row['queue_rank']} | {row['title']} | {row.get('kickoff_beijing') or '—'} | {row.get('market_consensus_direction')} / {fnum(row.get('market_consensus_probability'))} | {fnum(row.get('executable_price'))} | {row['status']} | {', '.join(row.get('missing_fields') or [])} |")
    if not payload.get("research_queue"): lines.append("| — | 当前Top候选均已有研究或没有达到研究优先线 | — | — | — | complete | — |")
    lines += ["", "## 今日可立即入场", ""]
    if not payload["main_recommendations"]: lines.append("**无。今日没有通过概率、价格、来源、规则与流动性全部门槛的赛前盘口。**")
    else:
        for row in payload["main_recommendations"]: lines += detail(row)
    lines += ["", "## 条件候选与盘中触发", ""]
    if not payload["conditional_candidates"]: lines.append("无合格条件候选。")
    else:
        for row in payload["conditional_candidates"]: lines += detail(row)
    lines += ["", "## 实验价值候选", ""]
    if not payload.get("experimental_value_candidates"): lines.append("无。")
    else:
        for row in payload["experimental_value_candidates"]: lines += detail(row)
    lines += ["", "## 最接近但仍须 Pass 的候选", ""]
    if not payload.get("closest_candidates"): lines.append("无具备独立研究概率的接近候选。")
    else:
        for row in payload["closest_candidates"]: lines += detail(row)
    lines += ["", "## 足球深度报告", ""]
    football = [row for row in payload["all_candidates"] if row["sport"] == "football"][:5]
    lines += [f"- {row['title']}：{row['decision_status']}；首球 `{row.get('first_goal_profile') or '待研究'}`；落后反应 `{row.get('trailing_response') or '待研究'}`；领先保护 `{row.get('lead_protection') or '待研究'}`。" for row in football] or ["- 当前窗口无足球主赛事。"]
    lines += ["", "## 网球次级报告", ""]
    tennis = [row for row in payload["all_candidates"] if row["sport"] == "tennis"][:5]
    lines += [f"- {row['title']}：{row['decision_status']}；研究概率 `{fnum(row.get('research_probability'))}`；动作 `{row['final_action']}`。" for row in tennis] or ["- 当前窗口无网球候选。"]
    lines += ["", "## Pass 清单", ""]
    passed = [row for row in payload["all_candidates"] if row["decision_status"] == "pass"]
    lines += [f"- {row['title']}：`{', '.join(row.get('failed_gates') or ['no_positive_edge'])}`；市场概率仅为基准。" for row in passed] or ["- 无。"]
    lines += ["", "## 具体人工操作清单", "",
              "1. 只对“今日主推荐”按最高接受价挂限价；条件候选必须等触发条件成立。",
              "2. 进球或破发后以可执行 bid 计算退出，不假设停盘期间能够成交。",
              "3. 任何比分、分钟、红牌或伤退数据超过120秒，停止新增仓位。",
              "4. 禁止账户 all-in；建议比例只针对可完全亏损的隔离风险资金。", "",
              "## 昨日结算与 Back Case", "", "- [体育结算复盘](CURRENT_SPORTS_SETTLEMENT_REVIEW.md) 按独立 game/condition 记录市场与研究概率评分；本报告不伪造尚未结算结果。",
              "- 由现有 [每日结算复盘](CURRENT_DAILY_SETTLEMENT_REVIEW.md) 与 [Back Case](CURRENT_CAPTURE_BACKCASE_CYCLE.md) 账本持续更新。",
              "- [周度复盘](CURRENT_WEEKLY_REVIEW.md) / [月度复盘](CURRENT_MONTHLY_REVIEW.md)", "",
              "## 数据来源和更新时间", "", "- Polymarket Gamma `/events/keyset`（体育标签）与公开 CLOB 双边 order book。",
              "- 独立研究概率只读取带来源、时间和版本的体育研究 artifact；缺失时明确 NO BET。", ""]
    return "\n".join(lines)


def detail(row: dict[str, Any]) -> list[str]:
    lines = [f"### {row['title']}", "", f"- 方向：**{row['direction']} / {row.get('target_side') or '—'}**；候选状态：`{row.get('display_status') or row['decision_status']}`；具体操作：{row.get('operation_instruction') or '待复核'}；等级：`{row['probability_status']}`",
             f"- 研究概率：{fnum(row.get('research_probability'))}（区间 {fnum((row.get('confidence_interval') or [None,None])[0])}–{fnum((row.get('confidence_interval') or [None,None])[1])}）；市场共识不是研究概率。",
             f"- 为什么强：{json.dumps(row.get('key_evidence') or ['独立研究证据不足'], ensure_ascii=False)}",
             f"- 估计方法：{row.get('method') or '尚无独立估值模型'}；历史基础率：{fnum(row.get('historical_base_rate'))}（样本 {row.get('historical_base_rate_samples') or 0}）。",
             f"- 可执行买价 / 卖价 / 最高买价：{fnum(row.get('executable_price'))} / {fnum(row.get('current_executable_sell_price'))} / {fnum(row.get('maximum_acceptable_price'))}",
             f"- Alpha 5¢优势价格上限：{fnum(row.get('alpha_maximum_acceptable_price'))}；上方最高买价若更高，仅适用于报告注明的极小实验仓。",
             f"- 净优势 / spread / 冲击：{fnum(row.get('net_edge_per_share'))} / {fnum(row.get('spread'))} / {fnum(row.get('price_impact'))}",
             f"- 建议权益比例：{row.get('suggested_equity_pct')}%；估计失败概率：{fnum(row.get('estimated_loss_probability'))}",
             f"- 失败路径：{json.dumps(row.get('failure_paths') or [], ensure_ascii=False)}",
             f"- 强弱性质：{row.get('strength_classification') or '—'}",
             f"- 等待条件：{row.get('conditional_trigger') or '—'}",
             f"- 盘中计划：{json.dumps(row.get('live_playbook') or {}, ensure_ascii=False)}",
             f"- 退出计划：{json.dumps(row.get('exit_plan') or {}, ensure_ascii=False)}",
             f"- 结算规则：{row.get('settlement_rules') or '必须临场复核原始合同'}",
             f"- 是否达到已校准80%标准：{row.get('validated_80pct_standard_met')}"]
    if row.get("sport") == "tennis":
        lines.append(f"- 网球专项（场地/Elo/发接发/伤病疲劳/赛制）：{json.dumps(row.get('tennis_profile') or {}, ensure_ascii=False)}")
    sources = row.get("sources") or []
    if sources:
        lines.append("- 来源：" + "；".join(f"[{source.get('name')}]({source.get('url')})（{source.get('kind')}，{source.get('fresh_at')}）" for source in sources))
    lines.append("")
    return lines


def append_ledger(path: Path, payload: dict[str, Any]) -> None:
    ledger = core.read_json(path) if path.exists() else {"schema_version": "sports-decision-observation-ledger-v1", "observations": []}
    ledger.update({"append_only": True, "paper_only": True, "counts_as_paper_trade": False,
                   "live_orders_enabled": False, "private_api_used": False, "real_money_execution_authorized": False})
    observation = {"observation_id": core.stable_id("sports-observation", payload["created_at"]),
                   "created_at": payload["created_at"], "report_date_beijing": payload["report_date_beijing"],
                   "events": [{"game_id": row["game_id"], "event_slug": row["event_slug"], "sport": row["sport"],
                               "kickoff_at": row.get("kickoff_at"),
                               "decision_status": row["decision_status"], "direction": row["direction"],
                               "research_probability": row.get("research_probability"), "market_consensus": row.get("market_consensus"),
                               "target_market_id": row.get("target_market_id"), "target_side": row.get("target_side"),
                               "target_role": next((market.get("role") for market in row.get("markets") or [] if str(market.get("market_id")) == str(row.get("target_market_id"))), None),
                               "executable_price": row.get("executable_price"), "final_action": row.get("final_action"),
                               "condition_ids": [market.get("condition_id") for market in row.get("markets") or []]}
                              for row in payload["all_candidates"]]}
    if not any(row.get("observation_id") == observation["observation_id"] for row in ledger.get("observations") or []):
        ledger.setdefault("observations", []).append(observation)
    ledger["observation_count"] = len(ledger.get("observations") or [])
    ledger["independent_condition_count"] = len({condition for obs in ledger.get("observations") or [] for event in obs.get("events") or [] for condition in event.get("condition_ids") or [] if condition})
    ledger["independent_event_count"] = len({event.get("game_id") for obs in ledger.get("observations") or [] for event in obs.get("events") or [] if event.get("game_id")})
    core.write_json(path, ledger)


def write_research_queue(payload: dict[str, Any], output: Path, report: Path) -> None:
    queue_payload = {"schema_version": "sports-event-research-queue-v1", "created_at": payload["created_at"],
                     "report_date_beijing": payload["report_date_beijing"], "status": "queued" if payload.get("research_queue") else "clear",
                     "queue_count": len(payload.get("research_queue") or []), "items": payload.get("research_queue") or [],
                     "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
                     "real_money_execution_authorized": False}
    core.write_json(output, queue_payload)
    lines = ["# Polymarket 体育独立研究队列", "", f"- 生成时间：`{payload['created_at']}`", f"- 状态：`{queue_payload['status']}`",
             f"- 待研究：{queue_payload['queue_count']} 场", "", "| 排名 | 比赛 | 时间 | 市场热门 | 缺失证据 |", "|---:|---|---|---|---|"]
    for row in queue_payload["items"]:
        lines.append(f"| {row['queue_rank']} | {row['title']} | {row.get('kickoff_beijing') or '—'} | {row.get('market_consensus_direction')} / {fnum(row.get('market_consensus_probability'))} | {', '.join(row.get('missing_fields') or [])} |")
    if not queue_payload["items"]: lines.append("| — | 无 | — | — | — |")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")


def resolved_winner(event: dict[str, Any]) -> tuple[str | None, str | None]:
    markets = event.get("markets") or []
    for index, market in enumerate(markets):
        outcomes = public.parse_jsonish(market.get("outcomes")); prices = public.parse_jsonish(market.get("outcomePrices"))
        if market.get("closed") is not True or not outcomes or len(outcomes) != len(prices): continue
        try: yes_index = next(i for i, name in enumerate(outcomes) if str(name).lower() == "yes")
        except StopIteration: continue
        try: won = float(prices[yes_index]) >= .999
        except (TypeError, ValueError): won = False
        if won: return role_for_market(event, market, index), str(market.get("conditionId") or "")
    return None, None


def settle_ledger(path: Path, now: dt.datetime, output: Path, report: Path) -> dict[str, Any]:
    ledger = core.read_json(path); resolved_ids = {str(row.get("game_id")) for row in ledger.get("resolutions") or []}
    latest: dict[str, dict[str, Any]] = {}
    for observation in ledger.get("observations") or []:
        for event in observation.get("events") or []: latest[str(event.get("game_id"))] = event
    added = []
    for game_id, observed in list(latest.items())[:100]:
        if game_id in resolved_ids: continue
        kickoff = parse_time(observed.get("kickoff_at"))
        if not kickoff or now < kickoff + dt.timedelta(hours=2): continue
        try: events = public.get_json(f"{public.GAMMA_BASE}/events?slug={observed.get('event_slug')}", timeout=20, retries=1)
        except Exception: continue
        if not events: continue
        event = {**events[0], "_sport": observed.get("sport")}; role, condition = resolved_winner(event)
        if not role: continue
        target_role = observed.get("target_role"); side = observed.get("target_side")
        actual = None
        if target_role and side in {"YES", "NO"}:
            yes_actual = 1 if role == target_role else 0; actual = yes_actual if side == "YES" else 1 - yes_actual
        market_probability = next((row.get("probability") for row in observed.get("market_consensus") or [] if row.get("role") == target_role), None)
        model_probability = observed.get("research_probability")
        price = observed.get("executable_price")
        resolution = {"resolution_id": core.stable_id("sports-resolution", game_id), "resolved_at": now.isoformat(),
                      "game_id": game_id, "event_slug": observed.get("event_slug"), "winning_role": role,
                      "winning_condition_id": condition, "target_role": target_role, "target_side": side, "actual_target": actual,
                      "research_probability": model_probability, "market_probability": market_probability,
                      "model_brier": (float(model_probability) - actual) ** 2 if model_probability is not None and actual is not None else None,
                      "market_brier": (float(market_probability) - actual) ** 2 if market_probability is not None and actual is not None else None,
                      "counterfactual_roi": (actual / float(price) - 1) if actual is not None and price else None,
                      "final_action_at_observation": observed.get("final_action"), "counts_as_independent_event": True}
        ledger.setdefault("resolutions", []).append(resolution); added.append(resolution); resolved_ids.add(game_id)
        if actual == 0 and (str(observed.get("final_action") or "").startswith("BUY") or float(model_probability or 0) >= .80):
            ledger.setdefault("back_cases", []).append({"back_case_id": core.stable_id("sports-back-case", game_id),
                "game_id": game_id, "created_at": now.isoformat(), "status": "review_required",
                "failure_category": "recommended_loss" if str(observed.get("final_action") or "").startswith("BUY") else "high_confidence_failure",
                "original_observation": observed, "resolution": resolution, "paper_only": True})
    ledger.setdefault("back_cases", [])
    ledger["resolved_independent_event_count"] = len({row.get("game_id") for row in ledger.get("resolutions") or []})
    ledger["back_case_count"] = len(ledger.get("back_cases") or []); core.write_json(path, ledger)
    resolved = ledger.get("resolutions") or []
    comparable = [row for row in resolved if row.get("model_brier") is not None and row.get("market_brier") is not None]
    payload = {"schema_version": "sports-settlement-review-v1", "created_at": now.isoformat(), "resolutions_added": len(added),
               "resolved_independent_event_count": ledger["resolved_independent_event_count"], "comparable_event_count": len(comparable),
               "mean_model_brier": sum(row["model_brier"] for row in comparable) / len(comparable) if comparable else None,
               "mean_market_brier": sum(row["market_brier"] for row in comparable) / len(comparable) if comparable else None,
               "back_case_count": ledger["back_case_count"], "recent_resolutions": resolved[-10:],
               "paper_only": True, "live_orders_enabled": False, "private_api_used": False, "real_money_execution_authorized": False}
    core.write_json(output, payload)
    lines = ["# Polymarket 体育结算与 Back Case 复盘", "", f"- 更新时间：`{payload['created_at']}`",
             f"- 独立已结算赛事：{payload['resolved_independent_event_count']}；可比较模型/市场样本：{payload['comparable_event_count']}；Back Case：{payload['back_case_count']}",
             "- 同一赛事的重复小时快照不增加独立赛事数。", "", "| 赛事 | 胜方角色 | 当时动作 | 模型Brier | 市场Brier | 反事实ROI |", "|---|---|---|---:|---:|---:|"]
    for row in payload["recent_resolutions"]:
        lines.append(f"| {row.get('event_slug')} | {row.get('winning_role')} | {row.get('final_action_at_observation')} | {fnum(row.get('model_brier'))} | {fnum(row.get('market_brier'))} | {fnum(row.get('counterfactual_roi'))} |")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8"); return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research", default=str(DEFAULT_RESEARCH)); parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT)); parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--settlement-output", default=str(DEFAULT_SETTLEMENT)); parser.add_argument("--settlement-report", default=str(DEFAULT_SETTLEMENT_REPORT))
    parser.add_argument("--research-queue-output", default=str(DEFAULT_RESEARCH_QUEUE)); parser.add_argument("--research-queue-report", default=str(DEFAULT_RESEARCH_QUEUE_REPORT))
    parser.add_argument("--archive-root", default=str(ROOT / "reports/daily-sports")); parser.add_argument("--fixture-events")
    args = parser.parse_args(); now = dt.datetime.now(dt.timezone.utc)
    if args.fixture_events:
        events = core.read_json(Path(args.fixture_events)).get("events") or []; requests = []
    else:
        football, req1 = fetch_tag_events(FOOTBALL_TAG_ID, "football", now)
        tennis, req2 = fetch_tag_events(TENNIS_TAG_ID, "tennis", now); events = football + tennis; requests = req1 + req2
    research = core.read_json(Path(args.research)) if Path(args.research).exists() else {"research_items": []}
    payload = build(events, research, core.read_json(ROOT / "config/policy.json"), now); payload["request_log"] = requests
    output = Path(args.output); report = Path(args.report); core.write_json(output, payload); rendered = markdown(payload)
    report.write_text(rendered, encoding="utf-8")
    base = Path(args.archive_root) / payload["report_date_beijing"] / f"sports-decision-{now.astimezone(LOCAL):%H%M%S}"
    base.parent.mkdir(parents=True, exist_ok=True); base.with_suffix(".md").write_text(rendered, encoding="utf-8"); core.write_json(base.with_suffix(".json"), payload)
    append_ledger(Path(args.ledger), payload)
    write_research_queue(payload, Path(args.research_queue_output), Path(args.research_queue_report))
    settlement = settle_ledger(Path(args.ledger), now, Path(args.settlement_output), Path(args.settlement_report))
    payload["settlement_review"] = {key: settlement[key] for key in ("resolved_independent_event_count", "comparable_event_count", "back_case_count")}
    core.write_json(output, payload)
    try:
        report_display = str(report.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        # Scheduler runs may deliberately render into an isolated temporary
        # directory.  The artifacts are already written; keep the final
        # status successful instead of turning display-path formatting into a
        # false scan failure.
        report_display = str(report.resolve())
    print(json.dumps({"status": "ok", "scanned": payload["scanned_event_count"], "main": len(payload["main_recommendations"]), "conditional": len(payload["conditional_candidates"]), "report": report_display}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
