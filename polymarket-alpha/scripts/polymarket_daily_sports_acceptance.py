#!/usr/bin/env python3
"""Acceptance audit for the football-first, tennis-second daily report."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path): return json.loads(path.read_text(encoding="utf-8"))


def build() -> dict:
    decision_path = ROOT / "experiments/current-daily-sports-decision.json"
    ledger_path = ROOT / "data/sports_decision_observation_ledger.json"
    settlement_path = ROOT / "experiments/current-sports-settlement-review.json"
    research_queue_path = ROOT / "experiments/current-sports-research-queue.json"
    report_path = ROOT / "reports/CURRENT_DAILY_SPORTS_DECISION.md"
    decision = read(decision_path); ledger = read(ledger_path); settlement = read(settlement_path)
    research = read(ROOT / "experiments/current-sports-event-research.json")
    research_queue = read(research_queue_path) if research_queue_path.exists() else {}
    source = (ROOT / "scripts/polymarket_daily_sports_report.py").read_text(encoding="utf-8")
    validation = (ROOT / "scripts/polymarket_validation_cycle.py").read_text(encoding="utf-8")
    tests = (ROOT / "tests/test_polymarket_daily_sports_report.py").read_text(encoding="utf-8")
    report_text = report_path.read_text(encoding="utf-8")
    researched = [row for row in decision.get("all_candidates") or [] if row.get("research_probability") is not None]
    required = {"direction", "research_probability", "confidence_interval", "executable_price", "maximum_acceptable_price",
                "net_edge_per_share", "suggested_equity_pct", "failure_paths", "live_playbook", "exit_plan", "settlement_rules", "sources"}
    checks = {
        "current_report_and_immutable_archive": report_path.exists() and bool(list((ROOT / "reports/daily-sports" / decision["report_date_beijing"]).glob("sports-decision-*.md"))),
        "football_first_scan_operational": decision.get("scanned_event_count", 0) > 0 and decision.get("football_event_count", 0) > 0,
        "tennis_secondary_contract_implemented": "tennis_profile" in source and "surface_elo_and_serve_return" in tests,
        "one_main_one_conditional_limit": len(decision.get("main_recommendations") or []) <= 1 and len(decision.get("conditional_candidates") or []) <= 1,
        "ranked_candidates_are_separate_from_trade_action": "## 今日第一、第二候选" in report_text and len(decision.get("priority_candidates") or []) <= 2 and all("operation_instruction" in row and "exit_instruction" in row for row in decision.get("priority_candidates") or []),
        "at_most_two_ranked_candidates_per_relay_window": "## 分时接力执行表" in report_text and all(sum(1 for item in decision.get("relay_schedule") or [] if item.get("time_window") == window) <= 2 for window in {row.get("time_window") for row in decision.get("relay_schedule") or []}) and len({(row.get("time_window"), row.get("candidate_rank")) for row in decision.get("relay_schedule") or []}) == len(decision.get("relay_schedule") or []),
        "missing_research_enters_bounded_sports_queue": "## 自动独立研究队列" in report_text and research_queue.get("schema_version") == "sports-event-research-queue-v1" and research_queue.get("queue_count", 0) <= 4 and all(row.get("output_path") == "experiments/current-sports-event-research.json" for row in research_queue.get("items") or []),
        "research_candidate_required_fields": bool(researched) or (len(research.get("research_items") or []) > 0 and all(required - {"research_probability", "confidence_interval", "executable_price", "maximum_acceptable_price", "net_edge_per_share", "suggested_equity_pct"} <= set(row) for row in research.get("research_items") or [])),
        "market_consensus_never_model_probability": all(row.get("market_consensus_is_research_probability") is False for row in decision.get("all_candidates") or []),
        "live_freshness_blocks_unverified_buy": all(not (row.get("event_phase") == "possibly_live_unverified" and row.get("final_action", "").startswith("BUY") and (row.get("live_data_age_seconds") is None or row.get("live_data_age_seconds") > 120)) for row in decision.get("all_candidates") or []),
        "append_only_unique_event_ledger": ledger.get("append_only") is True and ledger.get("independent_event_count", 0) <= ledger.get("independent_condition_count", 0),
        "settlement_and_backcase_contract": settlement.get("schema_version") == "sports-settlement-review-v1" and "back_cases" in ledger,
        "hourly_validation_cycle_hook": "daily_football_tennis_decision_report" in validation,
        "paper_only_safety_invariants": decision.get("paper_only") is True and decision.get("live_orders_enabled") is False and decision.get("private_api_used") is False and decision.get("real_money_execution_authorized") is False,
    }
    return {"schema_version": "daily-sports-acceptance-v1", "status": "pass" if all(checks.values()) else "fail",
            "checks": checks, "evidence": {"report": str(report_path.relative_to(ROOT)), "scanned_events": decision.get("scanned_event_count"),
            "main_recommendations": len(decision.get("main_recommendations") or []), "conditional_candidates": len(decision.get("conditional_candidates") or []),
            "observation_count": ledger.get("observation_count"), "independent_event_count": ledger.get("independent_event_count"),
            "resolved_independent_event_count": settlement.get("resolved_independent_event_count")}}


def markdown(payload: dict) -> str:
    lines = ["# 每日 Polymarket 体育报告验收矩阵", "", f"- 状态：`{payload['status']}`", "", "| 验收项 | 结果 |", "|---|---|"]
    lines += [f"| {key} | {'PASS' if value else 'FAIL'} |" for key, value in payload["checks"].items()]
    lines += ["", "## 当前证据", "", "```json", json.dumps(payload["evidence"], ensure_ascii=False, indent=2), "```", "",
              "策略是否长期有效仍未证明；当前只证明工程、报告、证据门和 paper-only 安全合同可运行。", ""]
    return "\n".join(lines)


def main() -> int:
    payload = build(); out = ROOT / "experiments/current-daily-sports-acceptance.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "reports/CURRENT_DAILY_SPORTS_ACCEPTANCE.md").write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__": raise SystemExit(main())
