#!/usr/bin/env python3
"""Strict completion audit for Polymarket V1.1 Stage 1 only."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "experiments/current-stage1-acceptance-audit.json"
DEFAULT_REPORT = ROOT / "reports/STAGE1_FINAL_ACCEPTANCE.md"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module


core = load("stage1_audit_core", ROOT / "scripts/polymarket_alpha.py")


def regression_evidence(run_tests: bool) -> dict[str, Any]:
    if not run_tests:
        return {"status": "not_run", "tests_passed": None, "returncode": None}
    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
                            cwd=ROOT, text=True, capture_output=True, timeout=120)
    output = result.stdout + "\n" + result.stderr
    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    return {"status": "pass" if result.returncode == 0 else "failed", "tests_passed": int(match.group(1)) if match else None,
            "returncode": result.returncode, "output_tail": output[-4000:]}


def audit(data: dict[str, Any]) -> dict[str, Any]:
    daily = data["daily"]; manual = data["manual"]; policy = data["policy"]
    scan_ledger = data["scan_ledger"]; candidate_ledger = data["candidate_ledger"]
    automation = data["automation"]; validation = data["validation"]; regression = data["regression"]
    items = manual.get("watch_items") or []; recommendations = [row for row in items if row.get("category") in {"recommended_investment", "alpha_primary_recommendation", "high_win_small_return_recommendation"}]
    decisions = daily.get("all_decisions") or []
    decision_index = {(str(row.get("market_id")), str(row.get("side"))): row for row in decisions}
    required_item_fields = {"market", "bet_direction", "market_consensus_side", "market_consensus_is_model_probability",
        "executable_price", "model_estimated_probability", "confidence_interval", "oos_or_forward_samples",
        "historical_hit_rate", "net_edge_per_share", "maximum_acceptable_price", "suggested_paper_amount_usd",
        "failure_paths", "exit_conditions", "final_action", "execution_tests_by_side"}
    required_steps = {"safety_preflight", "daily_1_24h_priority", "daily_manual_decision_report",
                      "daily_candidate_forward_ledger", "daily_forward_probability_benchmark"}
    step_rows = {row.get("name"): row for row in validation.get("steps", [])}
    recommendation_matches = []
    for row in recommendations:
        source = decision_index.get((str(row.get("market_id")), str(row.get("side"))))
        recommendation_matches.append(bool(source and source.get("gate_passed") is True and not source.get("failed_gates")))
    checks = []
    def add(identifier: str, name: str, passed: bool, evidence: Any) -> None:
        checks.append({"acceptance_id": identifier, "name": name, "status": "pass" if passed else "fail", "evidence": evidence})

    stable_cycles = len({row.get("cycle_id") for row in scan_ledger.get("observations", []) if row.get("cycle_id")})
    add("S1-01", "每日人工决策报告稳定生成",
        manual.get("status") == "ok" and daily.get("status") == "ok" and stable_cycles >= 2
        and step_rows.get("daily_manual_decision_report", {}).get("status") == "ok",
        {"daily_status": daily.get("status"), "manual_status": manual.get("status"), "distinct_daily_cycles": stable_cycles})
    limits_ok = 0 <= int(manual.get("watch_item_count", -1)) <= 4 and 0 <= int(manual.get("recommendation_count", -1)) <= 2
    limits_ok = limits_ok and manual.get("watch_item_count") == len(items) and manual.get("recommendation_count") == len(recommendations)
    add("S1-02", "0–4 个观察且最多 1–2 个推荐", limits_ok,
        {"watch_items": len(items), "recommendations": len(recommendations)})
    directions_ok = all(row.get("bet_direction") in {"YES", "NO", "NO_BET"} for row in items)
    directions_ok = directions_ok and all(row.get("bet_direction") in {"YES", "NO"} for row in recommendations)
    add("S1-03", "所有标的明确显示 YES、NO 或 NO BET", directions_ok,
        [{"market": row.get("market"), "bet_direction": row.get("bet_direction")} for row in items])
    pass_rows = [row for row in items if row.get("final_action") == "PASS"]
    pass_books_ok = all(set((row.get("execution_tests_by_side") or {}).keys()) >= {"YES", "NO"}
                        and row.get("bet_direction") == "NO_BET" for row in pass_rows)
    add("S1-04", "Pass 市场展示双边盘口且不构成建议", pass_books_ok,
        {"pass_rows": len(pass_rows), "all_have_yes_no_tests": pass_books_ok})
    consensus_ok = all(row.get("market_consensus_is_model_probability") is False for row in items)
    consensus_ok = consensus_ok and all("model_estimated_probability" in row for row in items)
    add("S1-05", "市场共识与独立模型概率严格分离", consensus_ok,
        {"items_checked": len(items), "consensus_is_model_probability": False})
    thresholds = policy.get("entry_gate") or {}
    strict_thresholds = (float(thresholds.get("min_model_probability", 0)) >= .8
        and float(thresholds.get("min_confidence_lower", 0)) >= .7
        and float(thresholds.get("min_net_edge_per_share", 0)) >= .04
        and int(thresholds.get("min_calibration_samples", 0)) >= 30
        and float(thresholds.get("max_spread_per_share", 99)) <= .02
        and float(thresholds.get("max_price_impact_per_share", 99)) <= .01)
    recommendation_gate_ok = strict_thresholds and all(recommendation_matches)
    recommendation_gate_ok = recommendation_gate_ok and daily.get("selected_candidate_count") == len(recommendations)
    add("S1-06", "推荐必须通过概率、校准、来源、规则、流动性和净 EV 全部门槛",
        recommendation_gate_ok, {"recommendations": len(recommendations), "all_source_gate_passed": all(recommendation_matches),
                                 "threshold_contract": strict_thresholds})
    no_opportunity = len(recommendations) == 0
    cash_ok = (not no_opportunity or (manual.get("decision") == "cash_no_recommendation"
        and daily.get("paper_opened_count") == 0 and len(data["paper_ledger"].get("open_positions") or []) == 0
        and all(row.get("bet_direction") == "NO_BET" for row in items)))
    add("S1-07", "无合格机会时保持空仓", cash_ok,
        {"recommendations": len(recommendations), "manual_decision": manual.get("decision"),
         "paper_opened": daily.get("paper_opened_count"), "open_positions": len(data["paper_ledger"].get("open_positions") or [])})
    traceability_ok = all(data["artifact_exists"].values()) and manual.get("source_daily_cycle_id") == daily.get("cycle_id")
    traceability_ok = traceability_ok and bool(daily.get("snapshot_manifest_sha256")) and not daily.get("snapshot_contract_failures")
    traceability_ok = traceability_ok and candidate_ledger.get("append_only") is True and len(candidate_ledger.get("events") or []) > 0
    traceability_ok = traceability_ok and all(required_item_fields <= set(row.keys()) for row in items)
    add("S1-08", "报告、JSON、观察账本和安全证据可追溯", traceability_ok,
        {"artifact_exists": data["artifact_exists"], "source_cycle_matches": manual.get("source_daily_cycle_id") == daily.get("cycle_id"),
         "candidate_events": len(candidate_ledger.get("events") or []), "snapshot_sha256": daily.get("snapshot_manifest_sha256")})
    safe_flags = [policy.get("paper_only") is True, policy.get("live_orders_enabled") is False,
                  policy.get("private_api_used") is False, manual.get("paper_only") is True,
                  manual.get("real_money_execution_authorized") is False, manual.get("live_orders_enabled") is False,
                  manual.get("private_api_used") is False, candidate_ledger.get("counts_as_paper_trade") is False]
    automation_ok = automation.get("status") == "ok" and automation.get("verified_automation_count") == automation.get("expected_automation_count")
    add("S1-09", "自动化按计划运行并保持 paper-only", automation_ok and all(safe_flags),
        {"automation_status": automation.get("status"), "verified": automation.get("verified_automation_count"),
         "expected": automation.get("expected_automation_count"), "all_safety_flags": all(safe_flags)})
    e2e_ok = validation.get("status") == "ok" and required_steps <= set(step_rows)
    e2e_ok = e2e_ok and all(row.get("status") == "ok" for row in validation.get("steps", []))
    regression_ok = regression.get("status") == "pass" and int(regression.get("tests_passed") or 0) > 0
    add("S1-10", "回归测试和完整端到端周期通过", e2e_ok and regression_ok,
        {"regression": regression, "validation_cycle_id": validation.get("cycle_id"),
         "validation_status": validation.get("status"), "step_count": len(step_rows), "required_steps_present": required_steps <= set(step_rows)})
    boundary_ok = (data["stage_scope"].get("current_stage") == 1 and data["stage_scope"].get("phase2_started") is False
                   and data["stage_scope"].get("long_term_strategy_effectiveness_proven") is False)
    add("S1-11", "阶段结论与长期证据边界明确", boundary_ok, data["stage_scope"])
    complete = all(row["status"] == "pass" for row in checks)
    return {"schema_version": "polymarket-stage1-acceptance-audit-v1", "created_at": core.now_iso(),
            "stage": 1, "stage_name": "daily_manual_decision_system", "status": "complete" if complete else "incomplete",
            "stage1_complete": complete, "checks_passed": sum(row["status"] == "pass" for row in checks),
            "checks_total": len(checks), "acceptance_matrix": checks,
            "current_direction": "NO_BET" if not recommendations else ",".join(row["bet_direction"] for row in recommendations),
            "current_recommendation_count": len(recommendations),
            "long_term_strategy_effectiveness_proven": False, "phase2_started": False,
            "next_action": "stop_and_wait_for_human_phase2_confirmation" if complete else "repair_failed_stage1_checks",
            "paper_only": True, "real_money_execution_authorized": False,
            "live_orders_enabled": False, "private_api_used": False}


def markdown(payload: dict[str, Any]) -> str:
    lines = ["# Polymarket Stage 1 Final Acceptance", "", f"- Stage status: `{payload['status']}`",
             f"- Checks: {payload['checks_passed']} / {payload['checks_total']}",
             f"- Current recommended direction: `{payload['current_direction']}`",
             f"- Current recommendation count: {payload['current_recommendation_count']}",
             "- Long-term strategy effectiveness proven: `false`", "- Phase 2 started: `false`", "",
             "| ID | Acceptance | Status |", "|---|---|---|"]
    for row in payload["acceptance_matrix"]:
        lines.append(f"| {row['acceptance_id']} | {row['name']} | {row['status']} |")
    lines.extend(["", "## Stage conclusion", ""])
    if payload["stage1_complete"]:
        lines.append("Stage 1 is complete. Development stops here and waits for explicit human confirmation before a separate Stage 2 goal is created.")
    else:
        failed = [row["acceptance_id"] for row in payload["acceptance_matrix"] if row["status"] != "pass"]
        lines.append("Stage 1 is incomplete. Failed checks: " + ", ".join(failed))
    lines.extend(["", "The current result does not prove positive expectancy, ROI superiority, calibrated independent models, 30/100 forward paper trades, or three 30-day windows.",
                  "Real orders, private APIs, wallet signing and real-money execution remain disabled.", ""])
    return "\n".join(lines)


def read(path: str) -> dict[str, Any]:
    return core.read_json(ROOT / path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--run-tests", action="store_true")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT)); parser.add_argument("--report", default=str(DEFAULT_REPORT)); args = parser.parse_args()
    artifact_paths = {"daily_report": ROOT / "reports/CURRENT_DAILY_MANUAL_DECISION.md",
                      "daily_json": ROOT / "experiments/current-daily-manual-decision.json",
                      "candidate_ledger": ROOT / "data/daily_candidate_observation_ledger.json",
                      "automation_audit": ROOT / "experiments/current-automation-contract-audit.json",
                      "validation_cycle": ROOT / "experiments/current-validation-cycle.json"}
    data = {"daily": read("experiments/current-daily-priority-cycle.json"),
            "manual": read("experiments/current-daily-manual-decision.json"), "policy": read("config/policy.json"),
            "scan_ledger": read("data/daily_priority_scan_ledger.json"),
            "candidate_ledger": read("data/daily_candidate_observation_ledger.json"),
            "paper_ledger": read("data/paper_ledger.json"),
            "automation": read("experiments/current-automation-contract-audit.json"),
            "validation": read("experiments/current-validation-cycle.json"),
            "regression": regression_evidence(args.run_tests),
            "artifact_exists": {name: path.exists() for name, path in artifact_paths.items()},
            "stage_scope": {"current_stage": 1, "phase2_started": False, "long_term_strategy_effectiveness_proven": False}}
    payload = audit(data); core.write_json(Path(args.output), payload); Path(args.report).write_text(markdown(payload), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "stage1_complete": payload["stage1_complete"],
                      "checks_passed": payload["checks_passed"], "checks_total": payload["checks_total"],
                      "current_direction": payload["current_direction"], "next_action": payload["next_action"]}, ensure_ascii=False, indent=2))
    return 0 if payload["stage1_complete"] else 1


if __name__ == "__main__": raise SystemExit(main())
