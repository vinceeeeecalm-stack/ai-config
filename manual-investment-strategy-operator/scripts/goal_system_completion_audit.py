#!/usr/bin/env python3
"""Audit the end-to-end 10x investment-system objective.

This is a system-level audit, not a single-report lint. It checks whether the
local skills, docs, ledgers, reports, paper-validation evidence, recommendation
history, and installed skill copies collectively prove the user's long-running
objective is satisfied.

The audit is intentionally conservative: it distinguishes process
infrastructure being implemented from the financial target being achieved or
the short-term tactical system being ready for real execute_now suggestions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
ACTIVE_ROOT = ROOT / "active-alpha-paper-monitor"
UNIFIED_ROOT = ROOT / "unified-longterm-alpha-investor"
CODEX_SKILL_ROOT = Path.home() / ".codex" / "skills"
QODER_SKILL_ROOT = Path.home() / ".qoderwork" / "skills"
SOURCE_DOC_SEARCH_ROOTS = [ROOT, CODEX_SKILL_ROOT, QODER_SKILL_ROOT]
SOURCE_DOC_SUFFIXES = {".sql", ".ddl"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str | None) -> Any:
    if not path:
        return None
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_text(path: Path | str | None) -> str:
    if not path:
        return ""
    path = Path(path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def newest(pattern: str) -> Path | None:
    matches = [Path(path) for path in glob.glob(pattern)]
    if not matches:
        return None
    return max(matches, key=lambda item: item.stat().st_mtime)


def is_test_or_smoke_artifact(path: Path, payload: dict[str, Any] | None = None) -> bool:
    text = path.name.lower()
    if "smoke" in text:
        return True
    run_id = str((payload or {}).get("run_id") or "").lower()
    return "smoke" in run_id


def newest_non_smoke(pattern: str) -> Path | None:
    matches = [Path(path) for path in glob.glob(pattern)]
    if not matches:
        return None
    loaded: list[tuple[Path, dict[str, Any]]] = []
    for path in matches:
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            payload = {}
        loaded.append((path, payload if isinstance(payload, dict) else {}))
    non_smoke = [
        (path, payload)
        for path, payload in loaded
        if not is_test_or_smoke_artifact(path, payload)
    ]
    candidates = non_smoke or loaded
    return max(candidates, key=lambda item: item[0].stat().st_mtime)[0]


def file_exists(path: Path | str | None) -> bool:
    return bool(path) and Path(path).exists()


def status_from(condition: bool, weak: bool = False) -> str:
    if condition and not weak:
        return "proven"
    if condition and weak:
        return "partially_proven"
    return "missing"


def severity_for(status: str) -> str:
    if status == "proven":
        return "pass"
    if status in {"partially_proven", "not_applicable"}:
        return "warning"
    return "error"


def check(
    checks: list[dict[str, Any]],
    requirement_id: str,
    requirement: str,
    status: str,
    evidence: str,
    severity: str | None = None,
    next_action: str | None = None,
) -> None:
    checks.append(
        {
            "requirement_id": requirement_id,
            "requirement": requirement,
            "status": status,
            "severity": severity or severity_for(status),
            "evidence": evidence,
            "next_action": next_action,
        }
    )


def pct(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def count_items(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 1


def summarize_recommendations(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "missing"}
    recs = payload.get("recommendations") or []
    reviews = payload.get("outcome_reviews") or []
    proposed = payload.get("proposed_changes") or []
    pending = [item for item in recs if item.get("outcome_status") == "pending"]
    resolved = [item for item in recs if item.get("outcome_status") in {"hit", "failed", "not_triggered", "expired", "invalidated"}]
    superseded = [item for item in recs if item.get("outcome_status") == "superseded"]
    return {
        "status": "ok",
        "total": len(recs),
        "pending": len(pending),
        "resolved": len(resolved),
        "superseded": len(superseded),
        "outcome_reviews": len(reviews),
        "proposed_changes": len(proposed),
    }


def summarize_portfolio_ledger(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "missing"}
    holdings = payload.get("holdings") or []
    cost_fields = [item for item in holdings if item.get("avg_cost") is not None]
    partial_lot_holdings = [
        item
        for item in holdings
        if item.get("avg_cost") is None
        and any(lot.get("cost_basis") is not None for lot in (item.get("lots") or []))
    ]
    missing_material_cost = [
        item.get("symbol")
        for item in holdings
        if item.get("avg_cost") is None
        and not any(lot.get("cost_basis") is not None for lot in (item.get("lots") or []))
        and (item.get("market_value") or 0) > 1
    ]
    known_lot_cost_basis = 0.0
    for item in holdings:
        for lot in item.get("lots") or []:
            try:
                known_lot_cost_basis += float(lot.get("cost_basis") or 0.0)
            except (TypeError, ValueError):
                pass
    staking = [item for item in holdings if item.get("staking")]
    lceth = next((item for item in holdings if item.get("symbol") == "lcETH"), None)
    ada = next((item for item in holdings if item.get("symbol") == "ADA"), None)
    sol = next((item for item in holdings if item.get("symbol") == "SOL"), None)
    rails = payload.get("cash_rails") or {}
    return {
        "status": "ok",
        "holdings_count": len(holdings),
        "cost_basis_count": len(cost_fields),
        "cost_basis_missing_count": max(0, len(holdings) - len(cost_fields)),
        "partial_lot_count": len(partial_lot_holdings),
        "partial_lot_symbols": [item.get("symbol") for item in partial_lot_holdings],
        "known_or_partial_cost_count": len(cost_fields) + len(partial_lot_holdings),
        "known_lot_cost_basis_usd": round(known_lot_cost_basis, 6),
        "missing_material_cost_basis_symbols": missing_material_cost,
        "cost_basis_reconciliation_status": (payload.get("cost_basis_reconciliation") or {}).get("status"),
        "staking_count": len(staking),
        "has_lceth_staking": bool(lceth and lceth.get("staking")),
        "has_ada_staking": bool(ada and ada.get("staking")),
        "has_sol_staking": bool(sol and sol.get("staking")),
        "crypto_rail_status": (rails.get("crypto_rail") or {}).get("data_quality_status"),
        "us_equity_rail_status": (rails.get("us_equity_rail") or {}).get("data_quality_status"),
        "cross_rail_transfer_assumption": rails.get("cross_rail_transfer_assumption"),
    }


def report_contains(report_text: str, needles: list[str]) -> bool:
    return all(needle in report_text for needle in needles)


def compare_file(src: Path, dst: Path) -> bool:
    if not src.exists() or not dst.exists():
        return False
    return src.read_bytes() == dst.read_bytes()


def discover_source_documents() -> list[Path]:
    docs: list[Path] = []
    seen: set[Path] = set()
    for root in SOURCE_DOC_SEARCH_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SOURCE_DOC_SUFFIXES:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            docs.append(path)
    return sorted(docs)


def render_markdown(payload: dict[str, Any]) -> str:
    rows = []
    for item in payload["checks"]:
        rows.append(
            "| {id} | {status} | {severity} | {evidence} | {next_action} |".format(
                id=item["requirement_id"],
                status=item["status"],
                severity=item["severity"],
                evidence=str(item["evidence"]).replace("|", "\\|")[:220],
                next_action=(item.get("next_action") or "").replace("|", "\\|")[:220],
            )
        )
    summary = payload["summary"]
    return "\n".join(
        [
            f"# Goal System Completion Audit | {payload['generated_at']}",
            "",
            "本审计只证明系统当前状态，不证明投资收益已经实现；任何真实交易仍需人工确认。",
            "",
            "## Verdict",
            "",
            f"- system_plan_implemented: `{summary['system_plan_implemented']}`",
            f"- ready_for_manual_reports: `{summary['ready_for_manual_reports']}`",
            f"- ready_for_crypto_dca_guidance: `{summary['ready_for_crypto_dca_guidance']}`",
            f"- ready_for_tactical_execute_now: `{summary['ready_for_tactical_execute_now']}`",
            f"- ready_for_auto_live_trading: `{summary['ready_for_auto_live_trading']}`",
            f"- ready_for_progressive_learning_loop: `{summary['ready_for_progressive_learning_loop']}`",
            f"- learning_calendar_ready: `{summary['learning_calendar_ready']}`",
            f"- operating_playbook_ready: `{summary['operating_playbook_ready']}`",
            f"- long_term_price_scenario_ready: `{summary['long_term_price_scenario_ready']}`",
            f"- long_horizon_dca_timing_ready: `{summary['long_horizon_dca_timing_ready']}`",
            f"- active_goal_execution_matrix_ready: `{summary['active_goal_execution_matrix_ready']}`",
            f"- investment_decision_validity_ready: `{summary['investment_decision_validity_ready']}`",
            f"- next_dispatch_readiness_ready: `{summary['next_dispatch_readiness_ready']}`",
            f"- next_goal_execution_queue_ready: `{summary['next_goal_execution_queue_ready']}`",
            f"- goal_mechanism_ready: `{summary['goal_mechanism_ready']}`",
            f"- objective_financial_outcome_verified: `{summary['objective_financial_outcome_verified']}`",
            f"- goal_complete: `{summary['goal_complete']}`",
            f"- max_allowed_current_action: `{summary['max_allowed_current_action']}`",
            f"- pass / warning / error: `{summary['pass_count']} / {summary['warning_count']} / {summary['error_count']}`",
            "",
            "## Checks",
            "",
            "| Requirement | Status | Severity | Evidence | Next Action |",
            "| --- | --- | --- | --- | --- |",
            *rows,
            "",
            "## Next Required Work",
            "",
            *[f"- {item}" for item in summary["next_required_work"]],
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the 10x goal investment-system implementation")
    parser.add_argument("--context-json", default="/private/tmp/20260530-goal-dashboard-v17-daily-context-final.json")
    parser.add_argument("--report-md", default=str(MANUAL_ROOT / "reports" / "2026-05-30-goal-10x-daily-manual-report-016.md"))
    parser.add_argument("--dashboard-json", default="/private/tmp/20260530-goal-dashboard-v17-goal-execution-dashboard.json")
    parser.add_argument("--report-integrity-json", default="/private/tmp/20260530-goal-dashboard-v17-report-integrity-audit.json")
    parser.add_argument("--objective-coverage-json", default="/private/tmp/20260530-goal-dashboard-v17-objective-coverage-audit.json")
    parser.add_argument("--recommendation-ledger", default=str(MANUAL_ROOT / "recommendations" / "recommendation_history.json"))
    parser.add_argument("--portfolio-ledger", default=str(UNIFIED_ROOT / "config" / "portfolio_ledger.json"))
    parser.add_argument("--paper-ledger", default=str(ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"))
    parser.add_argument("--learning-review-calendar-json", default="", help="Optional learning review calendar JSON; defaults to latest local artifact")
    parser.add_argument("--output", default="/private/tmp/goal_system_completion_audit_20260530.json")
    parser.add_argument("--output-json", dest="output_json", default=None)
    parser.add_argument("--markdown-output", default="/private/tmp/goal_system_completion_audit_20260530.md")
    parser.add_argument("--output-md", dest="output_md", default=None)
    parser.add_argument("--format", choices=["json"], default="json")
    args = parser.parse_args()

    context_path = Path(args.context_json)
    report_path = Path(args.report_md)
    dashboard_path = Path(args.dashboard_json)
    report_integrity_path = Path(args.report_integrity_json)
    objective_coverage_path = Path(args.objective_coverage_json)
    recommendation_path = Path(args.recommendation_ledger)
    portfolio_ledger_path = Path(args.portfolio_ledger)
    paper_ledger_path = Path(args.paper_ledger)
    learning_calendar_path = (
        Path(args.learning_review_calendar_json)
        if args.learning_review_calendar_json
        else newest(str(MANUAL_ROOT / "experiments" / "learning_review_calendar_*.json"))
    )
    manual_dispatch_summary_path = newest(str(MANUAL_ROOT / "experiments" / "manual_dispatch_summary_*.json"))
    next_dispatch_readiness_path = newest_non_smoke(str(MANUAL_ROOT / "experiments" / "next_dispatch_readiness_*.json"))
    next_goal_execution_queue_path = newest_non_smoke(str(MANUAL_ROOT / "experiments" / "next_goal_execution_queue_*.json"))
    current_goal_status_path = newest_non_smoke(str(MANUAL_ROOT / "experiments" / "current_goal_status_*.json"))
    next_manual_dispatch_plan_path = newest_non_smoke(str(MANUAL_ROOT / "experiments" / "next_manual_dispatch_execution_plan_*.json"))
    portfolio_evidence_request_path = newest_non_smoke(str(MANUAL_ROOT / "experiments" / "portfolio_evidence_request_report_*.json"))
    goal_evidence_closure_package_path = newest_non_smoke(str(MANUAL_ROOT / "experiments" / "goal_evidence_closure_package_*.json"))
    recommendation_calibration_package_path = newest_non_smoke(str(MANUAL_ROOT / "experiments" / "recommendation_calibration_package_*.json"))

    context = load_json(context_path) if context_path.exists() else {}
    dashboard = load_json(dashboard_path) if dashboard_path.exists() else {}
    report_integrity = load_json(report_integrity_path) if report_integrity_path.exists() else {}
    objective_coverage = load_json(objective_coverage_path) if objective_coverage_path.exists() else {}
    recommendation_summary = summarize_recommendations(load_json(recommendation_path) if recommendation_path.exists() else None)
    portfolio_summary = summarize_portfolio_ledger(load_json(portfolio_ledger_path) if portfolio_ledger_path.exists() else None)
    paper_ledger = load_json(paper_ledger_path) if paper_ledger_path.exists() else {}
    learning_calendar = load_json(learning_calendar_path) if learning_calendar_path and learning_calendar_path.exists() else {}
    manual_dispatch_summary = load_json(manual_dispatch_summary_path) if manual_dispatch_summary_path else {}
    next_dispatch_readiness = load_json(next_dispatch_readiness_path) if next_dispatch_readiness_path else {}
    next_goal_execution_queue = load_json(next_goal_execution_queue_path) if next_goal_execution_queue_path else {}
    current_goal_status = load_json(current_goal_status_path) if current_goal_status_path else {}
    next_manual_dispatch_plan = load_json(next_manual_dispatch_plan_path) if next_manual_dispatch_plan_path else {}
    portfolio_evidence_request = load_json(portfolio_evidence_request_path) if portfolio_evidence_request_path else {}
    goal_evidence_closure_package = load_json(goal_evidence_closure_package_path) if goal_evidence_closure_package_path else {}
    recommendation_calibration_package = (
        load_json(recommendation_calibration_package_path) if recommendation_calibration_package_path else {}
    )
    report_text = read_text(report_path)
    price_scenario_panel = context.get("long_term_price_scenario_panel") or {}
    price_scenario_summary = price_scenario_panel.get("summary") or {}
    price_scenario_assets = price_scenario_summary.get("assets") or []
    price_scenario_symbols = {
        item.get("symbol")
        for item in price_scenario_assets
        if isinstance(item, dict) and item.get("symbol")
    }

    checks: list[dict[str, Any]] = []

    source_docs = discover_source_documents()
    source_authority_doc = MANUAL_ROOT / "references" / "SOURCE_DOCUMENT_AUTHORITY.md"
    source_authority_text = read_text(source_authority_doc)
    source_authority_declared = (
        source_authority_doc.exists()
        and "no local `.sql` or `.ddl` source files" in source_authority_text
        and "current source of truth" in source_authority_text
    )
    check(
        checks,
        "sql_docs_or_skill_docs",
        "Use existing SQL/DDL docs as a base, or explicitly identify that skill docs are the actual local source of truth.",
        "proven" if (source_docs or source_authority_declared) else "partially_proven",
        (
            f"{len(source_docs)} SQL/DDL files found: {[str(path) for path in source_docs[:8]]}"
            if source_docs
            else f"No .sql/.ddl files found across {[str(root) for root in SOURCE_DOC_SEARCH_ROOTS]}; source authority declared in {source_authority_doc}."
            if source_authority_declared
            else f"No .sql/.ddl files found across {[str(root) for root in SOURCE_DOC_SEARCH_ROOTS]}; using manual/active/unified SKILL.md and references as current authoritative local docs."
        ),
        "pass" if (source_docs or source_authority_declared) else "warning",
        None if (source_docs or source_authority_declared) else "If SQL/DDL artifacts exist elsewhere, add them to the workspace or link them into the audit inputs.",
    )

    required_docs = [
        MANUAL_ROOT / "SKILL.md",
        ACTIVE_ROOT / "SKILL.md",
        MANUAL_ROOT / "references" / "GOAL_10X_EXECUTION_PLAN.md",
        MANUAL_ROOT / "references" / "GOAL_10X_OPERATING_PLAYBOOK.md",
        MANUAL_ROOT / "references" / "ACTIVE_GOAL_EXECUTION_MATRIX.md",
        MANUAL_ROOT / "references" / "OBJECTIVE_TO_MECHANISM_TRACEABILITY.md",
        MANUAL_ROOT / "references" / "GOAL_ORIENTED_DCA_POLICY.md",
        MANUAL_ROOT / "references" / "GOAL_10X_STRATEGY_LIBRARY.md",
        MANUAL_ROOT / "references" / "LONG_TERM_PRICE_SCENARIO_POLICY.md",
        MANUAL_ROOT / "references" / "MANUAL_REPORT_WORKFLOW.md",
        MANUAL_ROOT / "references" / "PASSIVE_DISPATCH_RUNTIME_POLICY.md",
        MANUAL_ROOT / "references" / "MANUAL_DISPATCH_STRATEGY_CONTRACT.md",
        MANUAL_ROOT / "references" / "MANUAL_DISPATCH_MARKET_SENTIMENT_POLICY.md",
        MANUAL_ROOT / "references" / "FULL_MARKET_DEEP_ANALYSIS_OBJECTIVE_GATE.md",
        MANUAL_ROOT / "references" / "GOAL_EVIDENCE_CLOSURE_POLICY.md",
        MANUAL_ROOT / "references" / "OBJECTIVE_COVERAGE_AUDIT.md",
        MANUAL_ROOT / "references" / "SUBAGENT_ORCHESTRATION_RUNBOOK.md",
        MANUAL_ROOT / "references" / "RESEARCH_EVIDENCE_BACKLOG_POLICY.md",
        MANUAL_ROOT / "references" / "SOURCE_DOCUMENT_AUTHORITY.md",
        ACTIVE_ROOT / "references" / "SUBAGENT_ORCHESTRATION_RUNBOOK.md",
        ACTIVE_ROOT / "references" / "RESEARCH_EVIDENCE_BACKLOG_POLICY.md",
    ]
    missing_docs = [str(path) for path in required_docs if not path.exists()]
    check(
        checks,
        "core_docs_available",
        "Have local strategy docs covering manual reports, active monitoring, 10x plan, strategy library, objective audit, and subagent orchestration.",
        "proven" if not missing_docs else "missing",
        "All required docs exist." if not missing_docs else f"Missing: {missing_docs}",
    )

    config_path = MANUAL_ROOT / "config" / "manual_strategy_config.json"
    active_config_path = ACTIVE_ROOT / "config" / "active_alpha_monitor_config.json"
    manual_config = load_json(config_path) if config_path.exists() else {}
    active_config = load_json(active_config_path) if active_config_path.exists() else {}
    dispatch_runner = manual_config.get("manual_dispatch_runner") or {}
    dispatch_orchestrates = dispatch_runner.get("orchestrates") or []
    dispatch_runner_script = MANUAL_ROOT / "scripts" / "manual_dispatch_run.py"
    dispatch_runner_text = read_text(dispatch_runner_script)
    operating_playbook_path = MANUAL_ROOT / (dispatch_runner.get("operating_playbook") or "references/GOAL_10X_OPERATING_PLAYBOOK.md")
    operating_playbook_text = read_text(operating_playbook_path)
    check(
        checks,
        "manual_dispatch_runner_entrypoint",
        "Manual reports have a one-command passive dispatch entrypoint that orchestrates fresh report generation and audits.",
        "proven"
        if dispatch_runner_script.exists()
        and dispatch_runner.get("enabled") is True
        and dispatch_runner.get("preferred_entrypoint_for_manual_reports") is True
        and dispatch_runner.get("live_orders_enabled") is False
        and dispatch_runner.get("mutates_portfolio_ledger") is False
        and "progressive_learning_iteration_audit" in dispatch_orchestrates
        and ("paper_validation_pulse_exit_only" in dispatch_orchestrates or "due_learning_review_pulse" in dispatch_orchestrates)
        and "learning_review_calendar" in dispatch_orchestrates
        and "next_dispatch_readiness" in dispatch_orchestrates
        and "next_goal_execution_queue" in dispatch_orchestrates
        else "missing",
        (
            f"script_exists={dispatch_runner_script.exists()}; enabled={dispatch_runner.get('enabled')}; "
            f"preferred={dispatch_runner.get('preferred_entrypoint_for_manual_reports')}; "
            f"live_orders_enabled={dispatch_runner.get('live_orders_enabled')}; "
            f"mutates_portfolio_ledger={dispatch_runner.get('mutates_portfolio_ledger')}; "
            f"orchestrates={dispatch_orchestrates}"
        ),
    )
    passive_runtime_cfg = dispatch_runner.get("passive_runtime_policy") or {}
    passive_runtime_path = MANUAL_ROOT / (passive_runtime_cfg.get("reference") or "references/PASSIVE_DISPATCH_RUNTIME_POLICY.md")
    passive_runtime_text = read_text(passive_runtime_path)
    passive_runtime_ok = bool(
        passive_runtime_path.exists()
        and passive_runtime_cfg.get("enabled") is True
        and passive_runtime_cfg.get("required") is True
        and passive_runtime_cfg.get("ordinary_dispatch_must_be_finite") is True
        and passive_runtime_cfg.get("deep_research_must_not_block_readable_report") is True
        and passive_runtime_cfg.get("stop_is_not_goal_completion") is True
        and passive_runtime_cfg.get("stop_when_readiness_recommends_stop_and_report_status") is True
        and "The manual skill is a user-triggered decision support tool" in passive_runtime_text
        and "Long-running simulation is a separate active-monitor concern" in passive_runtime_text
        and "A stop is not completion" in passive_runtime_text
    )
    check(
        checks,
        "passive_dispatch_runtime_policy",
        "Manual dispatch has an explicit finite-runtime boundary so active data collection does not become auto-trading or endless research.",
        "proven" if passive_runtime_ok else "missing",
        (
            f"path={passive_runtime_path}; exists={passive_runtime_path.exists()}; "
            f"enabled={passive_runtime_cfg.get('enabled')}; required={passive_runtime_cfg.get('required')}; "
            f"finite={passive_runtime_cfg.get('ordinary_dispatch_must_be_finite')}; "
            f"deep_research_nonblocking={passive_runtime_cfg.get('deep_research_must_not_block_readable_report')}; "
            f"stop_not_completion={passive_runtime_cfg.get('stop_is_not_goal_completion')}; "
            f"stop_on_readiness={passive_runtime_cfg.get('stop_when_readiness_recommends_stop_and_report_status')}"
        ),
        "pass" if passive_runtime_ok else "warning",
        "Wire manual_dispatch_runner.passive_runtime_policy and references/PASSIVE_DISPATCH_RUNTIME_POLICY.md." if not passive_runtime_ok else None,
    )
    operating_playbook_ok = bool(
        operating_playbook_path.exists()
        and dispatch_runner.get("operating_playbook") == "references/GOAL_10X_OPERATING_PLAYBOOK.md"
        and "被动手动调度" in operating_playbook_text
        and "不自动下单" in operating_playbook_text
        and "Timebox Rule" in operating_playbook_text
        and "Crypto DCA Rule" in operating_playbook_text
        and "US Equity Tactical Rule" in operating_playbook_text
        and "Action Level Ladder" in operating_playbook_text
        and "Learning Loop" in operating_playbook_text
        and "正式投资判断不要用 smoke 结果" in operating_playbook_text
    )
    check(
        checks,
        "operating_playbook",
        "Daily manual use has a concise playbook for passive boundaries, timebox closeout, DCA, US tactical actions, learning samples, and command modes.",
        "proven" if operating_playbook_ok else "missing",
        (
            f"path={operating_playbook_path}; exists={operating_playbook_path.exists()}; "
            f"config={dispatch_runner.get('operating_playbook')}; "
            f"has_passive_boundary={'被动手动调度' in operating_playbook_text}; "
            f"has_no_auto_orders={'不自动下单' in operating_playbook_text}; "
            f"has_timebox={'Timebox Rule' in operating_playbook_text}; "
            f"has_crypto_dca={'Crypto DCA Rule' in operating_playbook_text}; "
            f"has_us_tactical={'US Equity Tactical Rule' in operating_playbook_text}; "
            f"has_action_ladder={'Action Level Ladder' in operating_playbook_text}; "
            f"has_learning_loop={'Learning Loop' in operating_playbook_text}"
        ),
        "pass" if operating_playbook_ok else "warning",
        "Add or repair references/GOAL_10X_OPERATING_PLAYBOOK.md and wire manual_dispatch_runner.operating_playbook." if not operating_playbook_ok else None,
    )
    active_goal_matrix_cfg = dispatch_runner.get("active_goal_execution_matrix") or {}
    active_goal_matrix_path = MANUAL_ROOT / (
        active_goal_matrix_cfg.get("reference") or "references/ACTIVE_GOAL_EXECUTION_MATRIX.md"
    )
    active_goal_matrix_text = read_text(active_goal_matrix_path)
    active_goal_matrix_ok = bool(
        active_goal_matrix_path.exists()
        and active_goal_matrix_cfg.get("enabled") is True
        and active_goal_matrix_cfg.get("required") is True
        and active_goal_matrix_cfg.get("blocks_goal_mechanism_ready_if_missing") is True
        and "Requirement To Execution Map" in active_goal_matrix_text
        and "Next Trigger Rules" in active_goal_matrix_text
        and "Do Not Do" in active_goal_matrix_text
        and "goal_mechanism_ready=true" in active_goal_matrix_text
        and "goal_complete=false" in active_goal_matrix_text
        and "5-10 年 10x" in active_goal_matrix_text
        and "美股战术" in active_goal_matrix_text
        and "due_learning_review_pulse.py" in active_goal_matrix_text
    )
    check(
        checks,
        "active_goal_execution_matrix",
        "The active user goal is bound to a concrete execution matrix that names requirements, evidence gaps, action limits, and next triggers.",
        "proven" if active_goal_matrix_ok else "missing",
        (
            f"path={active_goal_matrix_path}; exists={active_goal_matrix_path.exists()}; "
            f"config_enabled={active_goal_matrix_cfg.get('enabled')}; "
            f"config_required={active_goal_matrix_cfg.get('required')}; "
            f"blocks_goal_ready={active_goal_matrix_cfg.get('blocks_goal_mechanism_ready_if_missing')}; "
            f"has_requirement_map={'Requirement To Execution Map' in active_goal_matrix_text}; "
            f"has_next_triggers={'Next Trigger Rules' in active_goal_matrix_text}; "
            f"has_do_not_do={'Do Not Do' in active_goal_matrix_text}; "
            f"mentions_goal_ready={'goal_mechanism_ready=true' in active_goal_matrix_text}; "
            f"mentions_goal_complete_false={'goal_complete=false' in active_goal_matrix_text}; "
            f"mentions_10x={'5-10 年 10x' in active_goal_matrix_text}; "
            f"mentions_us_tactical={'美股战术' in active_goal_matrix_text}; "
            f"uses_due_guard={'due_learning_review_pulse.py' in active_goal_matrix_text}"
        ),
        "pass" if active_goal_matrix_ok else "error",
        "Add or repair references/ACTIVE_GOAL_EXECUTION_MATRIX.md and wire manual_dispatch_runner.active_goal_execution_matrix." if not active_goal_matrix_ok else None,
    )
    investment_decision_cfg = dispatch_runner.get("investment_decision_validity") or {}
    investment_decision_status = (manual_dispatch_summary or {}).get("investment_decision_status") or {}
    investment_decision_ok = bool(
        investment_decision_cfg.get("enabled") is True
        and investment_decision_cfg.get("required_in_dispatch_summary") is True
        and "build_investment_decision_status" in dispatch_runner_text
        and "formal_investment_report_valid" in dispatch_runner_text
        and "execute_now_valid" in dispatch_runner_text
        and isinstance(investment_decision_status.get("formal_investment_report_valid"), bool)
        and isinstance(investment_decision_status.get("execute_now_valid"), bool)
        and isinstance(investment_decision_status.get("invalid_reasons"), list)
        and (
            manual_dispatch_summary.get("smoke_mode") is not True
            or (
                investment_decision_status.get("formal_investment_report_valid") is False
                and "smoke_mode_enabled" in investment_decision_status.get("invalid_reasons", [])
            )
        )
    )
    check(
        checks,
        "investment_decision_validity",
        "Manual dispatch summary separates script status=ok, formal investment-report validity, and execute_now eligibility.",
        "proven" if investment_decision_ok else "missing",
        (
            f"config_enabled={investment_decision_cfg.get('enabled')}; "
            f"required_in_summary={investment_decision_cfg.get('required_in_dispatch_summary')}; "
            f"script_has_builder={'build_investment_decision_status' in dispatch_runner_text}; "
            f"latest_summary={manual_dispatch_summary_path}; "
            f"summary_status={(manual_dispatch_summary or {}).get('status')}; "
            f"smoke={(manual_dispatch_summary or {}).get('smoke_mode')}; "
            f"formal_valid={investment_decision_status.get('formal_investment_report_valid')}; "
            f"execute_now_valid={investment_decision_status.get('execute_now_valid')}; "
            f"invalid_reasons={investment_decision_status.get('invalid_reasons')}; "
            f"plain_note={investment_decision_status.get('plain_note')}"
        ),
        "pass" if investment_decision_ok else "warning",
        "Run manual_dispatch_run.py after the investment decision validity field is wired, and keep smoke/degraded runs marked non-formal." if not investment_decision_ok else None,
    )
    next_dispatch_cfg = dispatch_runner.get("next_dispatch_readiness") or {}
    next_dispatch_script = MANUAL_ROOT / "scripts" / "next_dispatch_readiness.py"
    next_dispatch_summary = (next_dispatch_readiness or {}).get("summary") or {}
    next_dispatch_paths = (next_dispatch_readiness or {}).get("paths") or {}
    dispatch_summary_next = (manual_dispatch_summary or {}).get("next_dispatch_readiness_summary") or {}
    dispatch_summary_next_summary = dispatch_summary_next.get("summary") or {}
    next_dispatch_latest_json_ok = bool(
        next_dispatch_readiness_path
        and isinstance(next_dispatch_summary.get("ready_for_next_manual_dispatch"), bool)
        and isinstance(next_dispatch_summary.get("goal_mechanism_ready"), bool)
        and isinstance(next_dispatch_summary.get("goal_complete"), bool)
        and isinstance(next_dispatch_summary.get("immediate_focus"), list)
        and isinstance(next_dispatch_summary.get("execute_now_preblocked_reasons"), list)
    )
    next_dispatch_run_summary_ok = bool(
        (manual_dispatch_summary or {}).get("next_dispatch_readiness_status") == "ok"
        or dispatch_summary_next.get("status") == "ok"
        or isinstance(dispatch_summary_next_summary.get("ready_for_next_manual_dispatch"), bool)
    )
    next_dispatch_readiness_ok = bool(
        next_dispatch_cfg.get("enabled") is True
        and next_dispatch_cfg.get("script") == "scripts/next_dispatch_readiness.py"
        and next_dispatch_cfg.get("live_orders_enabled") is False
        and next_dispatch_cfg.get("mutates_ledgers") is False
        and "next_dispatch_readiness" in (dispatch_runner.get("orchestrates") or [])
        and investment_decision_cfg.get("formal_report_requires_next_dispatch_readiness") is True
        and next_dispatch_script.exists()
        and "build_next_dispatch_readiness_command" in dispatch_runner_text
        and "next_dispatch_readiness_summary" in dispatch_runner_text
        and "next_dispatch_readiness_not_run" in dispatch_runner_text
        and next_dispatch_latest_json_ok
        and next_dispatch_run_summary_ok
    )
    check(
        checks,
        "next_dispatch_readiness",
        "Manual dispatch has a read-only preflight that combines completion audit, learning calendar, and closure gaps before the next report.",
        "proven" if next_dispatch_readiness_ok else "missing",
        (
            f"config_enabled={next_dispatch_cfg.get('enabled')}; "
            f"script={next_dispatch_cfg.get('script')}; "
            f"live_orders_enabled={next_dispatch_cfg.get('live_orders_enabled')}; "
            f"mutates_ledgers={next_dispatch_cfg.get('mutates_ledgers')}; "
            f"orchestrated={'next_dispatch_readiness' in (dispatch_runner.get('orchestrates') or [])}; "
            f"formal_requires={investment_decision_cfg.get('formal_report_requires_next_dispatch_readiness')}; "
            f"script_exists={next_dispatch_script.exists()}; "
            f"latest_readiness={next_dispatch_readiness_path}; "
            f"latest_ready_for_dispatch={next_dispatch_summary.get('ready_for_next_manual_dispatch')}; "
            f"latest_goal_mechanism_ready={next_dispatch_summary.get('goal_mechanism_ready')}; "
            f"latest_goal_complete={next_dispatch_summary.get('goal_complete')}; "
            f"latest_max_action={next_dispatch_summary.get('max_allowed_current_action')}; "
            f"latest_immediate_focus={next_dispatch_summary.get('immediate_focus')}; "
            f"latest_paths={next_dispatch_paths}; "
            f"manual_summary={manual_dispatch_summary_path}; "
            f"manual_summary_next_status={(manual_dispatch_summary or {}).get('next_dispatch_readiness_status') or dispatch_summary_next.get('status')}"
        ),
        "pass" if next_dispatch_readiness_ok else "warning",
        "Run next_dispatch_readiness.py and then manual_dispatch_run.py so the next dispatch readiness panel is present in the latest summary." if not next_dispatch_readiness_ok else None,
    )
    next_goal_queue_cfg = dispatch_runner.get("next_goal_execution_queue") or {}
    next_goal_queue_script = MANUAL_ROOT / "scripts" / "next_goal_execution_queue.py"
    next_goal_queue_summary = (next_goal_execution_queue or {}).get("summary") or {}
    next_goal_queue_tasks = (next_goal_execution_queue or {}).get("tasks") or []
    dispatch_summary_queue = (manual_dispatch_summary or {}).get("next_goal_execution_queue_summary") or {}
    dispatch_summary_queue_summary = dispatch_summary_queue.get("summary") or {}
    next_goal_queue_latest_json_ok = bool(
        next_goal_execution_queue_path
        and next_goal_execution_queue.get("status") == "ok"
        and next_goal_execution_queue.get("read_only") is True
        and next_goal_execution_queue.get("live_orders_enabled") is False
        and next_goal_execution_queue.get("mutates_ledgers") is False
        and isinstance(next_goal_queue_summary.get("task_count"), int)
        and isinstance(next_goal_queue_summary.get("p0_open_count"), int)
        and isinstance(next_goal_queue_summary.get("execute_now_blocking_task_ids"), list)
        and isinstance(next_goal_queue_tasks, list)
    )
    next_goal_queue_run_summary_ok = bool(
        (manual_dispatch_summary or {}).get("next_goal_execution_queue_status") == "ok"
        or dispatch_summary_queue.get("status") == "ok"
        or isinstance(dispatch_summary_queue_summary.get("task_count"), int)
    )
    next_goal_queue_ok = bool(
        next_goal_queue_cfg.get("enabled") is True
        and next_goal_queue_cfg.get("script") == "scripts/next_goal_execution_queue.py"
        and next_goal_queue_cfg.get("live_orders_enabled") is False
        and next_goal_queue_cfg.get("mutates_ledgers") is False
        and "next_goal_execution_queue" in (dispatch_runner.get("orchestrates") or [])
        and next_goal_queue_script.exists()
        and "build_next_goal_execution_queue_command" in dispatch_runner_text
        and "next_goal_execution_queue_summary" in dispatch_runner_text
        and "Next Goal Execution Queue" in dispatch_runner_text
        and next_goal_queue_latest_json_ok
        and next_goal_queue_run_summary_ok
    )
    check(
        checks,
        "next_goal_execution_queue",
        "Manual dispatch produces a read-only next-step queue from readiness, learning reviews, and evidence blockers.",
        "proven" if next_goal_queue_ok else "missing",
        (
            f"config_enabled={next_goal_queue_cfg.get('enabled')}; "
            f"script={next_goal_queue_cfg.get('script')}; "
            f"live_orders_enabled={next_goal_queue_cfg.get('live_orders_enabled')}; "
            f"mutates_ledgers={next_goal_queue_cfg.get('mutates_ledgers')}; "
            f"orchestrated={'next_goal_execution_queue' in (dispatch_runner.get('orchestrates') or [])}; "
            f"script_exists={next_goal_queue_script.exists()}; "
            f"latest_queue={next_goal_execution_queue_path}; "
            f"latest_status={next_goal_execution_queue.get('status')}; "
            f"latest_goal_mechanism_ready={next_goal_queue_summary.get('goal_mechanism_ready')}; "
            f"latest_goal_complete={next_goal_queue_summary.get('goal_complete')}; "
            f"latest_task_count={next_goal_queue_summary.get('task_count')}; "
            f"latest_p0_open={next_goal_queue_summary.get('p0_open_count')}; "
            f"latest_blocking_tasks={next_goal_queue_summary.get('execute_now_blocking_task_ids')}; "
            f"manual_summary_queue_status={(manual_dispatch_summary or {}).get('next_goal_execution_queue_status') or dispatch_summary_queue.get('status')}"
        ),
        "pass" if next_goal_queue_ok else "warning",
        "Run next_goal_execution_queue.py or a non-smoke manual_dispatch_run.py so the next-step queue is present and valid." if not next_goal_queue_ok else None,
    )
    status_report_cfg = dispatch_runner.get("current_goal_status_report") or {}
    status_report_script = MANUAL_ROOT / "scripts" / "current_goal_status_report.py"
    status_summary = (current_goal_status or {}).get("summary") or {}
    status_plain = (current_goal_status or {}).get("plain_answer") or {}
    status_report_ok = bool(
        status_report_cfg.get("enabled") is True
        and status_report_cfg.get("script") == "scripts/current_goal_status_report.py"
        and status_report_cfg.get("live_orders_enabled") is False
        and status_report_cfg.get("mutates_ledgers") is False
        and status_report_script.exists()
        and (current_goal_status or {}).get("read_only") is True
        and (current_goal_status or {}).get("live_orders_enabled") is False
        and (current_goal_status or {}).get("mutates_ledgers") is False
        and isinstance(status_summary.get("goal_mechanism_ready"), bool)
        and isinstance(status_summary.get("goal_complete"), bool)
        and isinstance(status_summary.get("ready_for_next_manual_dispatch"), bool)
        and isinstance(status_plain.get("why_stop_now"), str)
        and "current_goal_status_report.py" in "\n".join(
            str(command)
            for task_item in next_goal_queue_tasks
            if isinstance(task_item, dict)
            for command in task_item.get("commands") or []
        )
    )
    check(
        checks,
        "current_goal_status_report",
        "When the current turn should stop, the system can generate a concise read-only user-facing status page.",
        "proven" if status_report_ok else "missing",
        (
            f"config_enabled={status_report_cfg.get('enabled')}; "
            f"script={status_report_cfg.get('script')}; "
            f"script_exists={status_report_script.exists()}; "
            f"latest_status={current_goal_status_path}; "
            f"read_only={(current_goal_status or {}).get('read_only')}; "
            f"live_orders_enabled={(current_goal_status or {}).get('live_orders_enabled')}; "
            f"mutates_ledgers={(current_goal_status or {}).get('mutates_ledgers')}; "
            f"goal_mechanism_ready={status_summary.get('goal_mechanism_ready')}; "
            f"goal_complete={status_summary.get('goal_complete')}; "
            f"recommended_action={status_summary.get('recommended_current_turn_action')}"
        ),
        "pass" if status_report_ok else "warning",
        "Run current_goal_status_report.py after next_dispatch_readiness.py and next_goal_execution_queue.py." if not status_report_ok else None,
    )
    next_plan_cfg = dispatch_runner.get("next_manual_dispatch_execution_plan") or {}
    next_plan_script = MANUAL_ROOT / "scripts" / "next_manual_dispatch_execution_plan.py"
    next_plan_summary = (next_manual_dispatch_plan or {}).get("summary") or {}
    next_plan_commands = (next_manual_dispatch_plan or {}).get("commands") or {}
    next_plan_ok = bool(
        next_plan_cfg.get("enabled") is True
        and next_plan_cfg.get("script") == "scripts/next_manual_dispatch_execution_plan.py"
        and next_plan_cfg.get("live_orders_enabled") is False
        and next_plan_cfg.get("mutates_ledgers") is False
        and "next_manual_dispatch_execution_plan" in dispatch_orchestrates
        and next_plan_script.exists()
        and (next_manual_dispatch_plan or {}).get("read_only") is True
        and (next_manual_dispatch_plan or {}).get("live_orders_enabled") is False
        and (next_manual_dispatch_plan or {}).get("mutates_ledgers") is False
        and isinstance(next_plan_summary.get("goal_mechanism_ready"), bool)
        and isinstance(next_plan_summary.get("goal_complete"), bool)
        and isinstance(next_plan_summary.get("ready_for_next_manual_dispatch"), bool)
        and isinstance(next_manual_dispatch_plan.get("dispatch_loop"), list)
        and isinstance(next_manual_dispatch_plan.get("crypto_dca_framework"), list)
        and isinstance(next_manual_dispatch_plan.get("us_tactical_framework"), list)
        and "manual_dispatch_run.py" in str(next_plan_commands.get("formal_manual_dispatch") or "")
        and (
            "validation_progress_runner.py" in str(next_plan_commands.get("paper_review_pulse") or "")
            or "due_learning_review_pulse.py" in str(next_plan_commands.get("paper_review_pulse") or "")
        )
    )
    check(
        checks,
        "next_manual_dispatch_execution_plan",
        "After readiness and queue, the system can generate a concise read-only plan for the next manual dispatch.",
        "proven" if next_plan_ok else "missing",
        (
            f"config_enabled={next_plan_cfg.get('enabled')}; "
            f"script={next_plan_cfg.get('script')}; "
            f"script_exists={next_plan_script.exists()}; "
            f"orchestrated={'next_manual_dispatch_execution_plan' in dispatch_orchestrates}; "
            f"latest_plan={next_manual_dispatch_plan_path}; "
            f"read_only={(next_manual_dispatch_plan or {}).get('read_only')}; "
            f"live_orders_enabled={(next_manual_dispatch_plan or {}).get('live_orders_enabled')}; "
            f"mutates_ledgers={(next_manual_dispatch_plan or {}).get('mutates_ledgers')}; "
            f"goal_mechanism_ready={next_plan_summary.get('goal_mechanism_ready')}; "
            f"goal_complete={next_plan_summary.get('goal_complete')}; "
            f"manual_dispatch_command={'manual_dispatch_run.py' in str(next_plan_commands.get('formal_manual_dispatch') or '')}; "
            f"paper_pulse_command={('validation_progress_runner.py' in str(next_plan_commands.get('paper_review_pulse') or '') or 'due_learning_review_pulse.py' in str(next_plan_commands.get('paper_review_pulse') or ''))}"
        ),
        "pass" if next_plan_ok else "warning",
        "Run next_manual_dispatch_execution_plan.py after next_dispatch_readiness.py and next_goal_execution_queue.py." if not next_plan_ok else None,
    )
    pulse_cfg = dispatch_runner.get("paper_validation_pulse") or {}
    safe_pulse_command = str(pulse_cfg.get("safe_queue_command") or "")
    next_dispatch_command_texts = [
        str(entry.get("command") or "")
        for entry in (next_dispatch_readiness or {}).get("commands") or []
        if isinstance(entry, dict)
    ]
    next_queue_command_texts = [
        str(command)
        for entry in next_goal_queue_tasks
        if isinstance(entry, dict)
        for command in entry.get("commands") or []
    ]
    closure_command_texts = [
        str(command)
        for entry in (goal_evidence_closure_package or {}).get("closure_items") or []
        if isinstance(entry, dict)
        for command in entry.get("automation_commands") or []
    ]
    exposed_commands = next_dispatch_command_texts + next_queue_command_texts + closure_command_texts
    long_loop_commands = [
        command
        for command in exposed_commands
        if "sunday_crypto_realistic_paper_loop.py" in command
    ]
    validation_pulse_commands = [
        command
        for command in exposed_commands
        if "validation_progress_runner.py" in command
    ]
    due_guard_commands = [
        command
        for command in exposed_commands
        if "due_learning_review_pulse.py" in command
    ]
    safe_pulse_is_direct_validation = bool(
        "validation_progress_runner.py" in safe_pulse_command
        and "--cycles 1" in safe_pulse_command
        and "--skip-fast" in safe_pulse_command
        and "--exit-timeout-seconds" in safe_pulse_command
        and "--validation-timeout-seconds" in safe_pulse_command
    )
    safe_pulse_is_due_guard = bool(
        "due_learning_review_pulse.py" in safe_pulse_command
        and "--timeout-seconds" in safe_pulse_command
    )
    timeboxed_paper_pulse_ok = bool(
        pulse_cfg.get("enabled_by_default_outside_smoke") is True
        and (safe_pulse_is_direct_validation or safe_pulse_is_due_guard)
        and (
            "validation_progress_runner.py" in dispatch_runner_text
            or "due_learning_review_pulse.py" in dispatch_runner_text
        )
        and (
            "validation_progress_runner.py" in next_dispatch_script.read_text(encoding="utf-8")
            or "due_learning_review_pulse.py" in next_dispatch_script.read_text(encoding="utf-8")
        )
        and (
            "validation_progress_runner.py" in next_goal_queue_script.read_text(encoding="utf-8")
            or "due_learning_review_pulse.py" in next_goal_queue_script.read_text(encoding="utf-8")
        )
        and not long_loop_commands
        and bool(validation_pulse_commands or due_guard_commands)
    )
    check(
        checks,
        "timeboxed_paper_validation_pulse",
        "Next readiness and next-step queues expose only short, timeboxed paper validation pulses for ordinary manual dispatch.",
        "proven" if timeboxed_paper_pulse_ok else "missing",
        (
            f"config_enabled={pulse_cfg.get('enabled_by_default_outside_smoke')}; "
            f"safe_queue_command={safe_pulse_command}; "
            f"validation_pulse_command_count={len(validation_pulse_commands)}; "
            f"due_guard_command_count={len(due_guard_commands)}; "
            f"long_loop_command_count={len(long_loop_commands)}; "
            f"next_dispatch_commands={len(next_dispatch_command_texts)}; "
            f"next_queue_commands={len(next_queue_command_texts)}; "
            f"closure_commands={len(closure_command_texts)}"
        ),
        "pass" if timeboxed_paper_pulse_ok else "warning",
        "Replace ordinary paper-review commands with due_learning_review_pulse.py, or a timeboxed validation_progress_runner.py --cycles 1 --skip-fast, and keep long active loops out of manual dispatch queues." if not timeboxed_paper_pulse_ok else None,
    )
    check(
        checks,
        "no_auto_live_trading",
        "Current system must not auto-trade real money; all live actions require human confirmation.",
        "proven"
        if manual_config.get("auto_trading_enabled") is False
        and active_config.get("auto_trading_enabled") is False
        and active_config.get("live_order_api_enabled") is False
        else "missing",
        f"manual.auto_trading_enabled={manual_config.get('auto_trading_enabled')}; active.auto_trading_enabled={active_config.get('auto_trading_enabled')}; active.live_order_api_enabled={active_config.get('live_order_api_enabled')}",
    )
    dispatch_contract = manual_config.get("manual_dispatch_strategy_contract") or {}
    fresh_gate = manual_config.get("manual_dispatch_market_sentiment_refresh_gate") or {}
    full_market_gate = manual_config.get("full_market_deep_analysis_objective_gate") or {}
    fresh_context = context.get("fresh_market_intelligence_snapshot") or {}
    required_loop = set(dispatch_contract.get("required_loop") or [])
    expected_loop = {
        "portfolio_and_cash_refresh",
        "market_data_refresh",
        "sentiment_and_news_refresh",
        "full_market_deep_analysis",
        "monthly_tactical_goal_mapping",
        "five_to_ten_year_goal_mapping",
        "plain_action_plan",
        "human_confirmation_boundary",
    }
    check(
        checks,
        "manual_dispatch_strategy_contract",
        "Manual dispatch must form one passive loop from market/sentiment refresh to goal-oriented strategy and action plan.",
        "proven"
        if dispatch_contract.get("enabled") is True
        and dispatch_contract.get("required") is True
        and expected_loop.issubset(required_loop)
        and (dispatch_contract.get("failure_behavior") or {}).get("incomplete_loop_blocks_execute_now") is True
        and (dispatch_contract.get("failure_behavior") or {}).get("single_price_or_single_news_source_cannot_replace_full_analysis") is True
        else "missing",
        (
            f"enabled={dispatch_contract.get('enabled')}; required={dispatch_contract.get('required')}; "
            f"loop_items={sorted(required_loop)}; failure_behavior={dispatch_contract.get('failure_behavior')}"
        ),
    )
    check(
        checks,
        "manual_dispatch_fresh_market_intelligence_gate",
        "Manual reports must refresh market/sentiment data on demand while remaining passive and non-trading.",
        "proven"
        if fresh_gate.get("enabled") is True
        and fresh_gate.get("required") is True
        and fresh_gate.get("decision_rules", {}).get("missing_fresh_refresh_blocks_execute_now") is True
        and (
            not fresh_context
            or (
                fresh_context.get("manual_dispatch_only") is True
                and fresh_context.get("background_loop_started") is False
                and fresh_context.get("live_orders_enabled") is False
                and (
                    fresh_context.get("market_intelligence_degraded") is not True
                    or context.get("report_readiness", {}).get("execute_now_allowed") is False
                )
            )
        )
        else "missing",
        (
            f"gate_enabled={fresh_gate.get('enabled')}; required={fresh_gate.get('required')}; "
            f"snapshot={fresh_context.get('snapshot_id')}; degraded={fresh_context.get('market_intelligence_degraded')}; "
            f"background_loop_started={fresh_context.get('background_loop_started')}; "
            f"live_orders_enabled={fresh_context.get('live_orders_enabled')}; "
            f"execute_now={context.get('report_readiness', {}).get('execute_now_allowed')}"
        ),
        "warning" if not fresh_context else "pass",
        "Generate the next manual report with manual-v2.7 so the context includes fresh_market_intelligence_snapshot." if not fresh_context else None,
    )
    check(
        checks,
        "full_market_deep_analysis_objective_gate",
        "Manual reports must translate full market/sentiment data into monthly tactical and 5-10y long-term objective decisions.",
        "proven"
        if full_market_gate.get("enabled") is True
        and full_market_gate.get("required") is True
        and full_market_gate.get("degraded_behavior", {}).get("missing_goal_mapping_blocks_execute_now") is True
        and (
            not report_text
            or (
                "Full-Market Deep Analysis Panel" in report_text
                and "月度战术收益目标" in report_text
                and "5-10年长期目标" in report_text
                and "现金通道" in report_text
            )
        )
        else "missing",
        (
            f"gate_enabled={full_market_gate.get('enabled')}; required={full_market_gate.get('required')}; "
            f"report_panel_present={'Full-Market Deep Analysis Panel' in report_text if report_text else None}; "
            f"objective_mapping_present={bool(fresh_context.get('objective_strategy_mapping'))}"
        ),
        "pass" if report_text and "Full-Market Deep Analysis Panel" in report_text else "warning",
        "Run the next manual report with manual-v2.8 so the report includes Full-Market Deep Analysis Panel." if "Full-Market Deep Analysis Panel" not in report_text else None,
    )

    portfolio = context.get("portfolio_snapshot") or {}
    total_value = pct(portfolio.get("total_value"))
    check(
        checks,
        "holdings_loaded",
        "Current holdings are locally loaded with portfolio value and top holdings.",
        "proven" if total_value and (portfolio.get("top_holdings") or []) else "missing",
        f"total_value={portfolio.get('total_value')}; top_holdings={len(portfolio.get('top_holdings') or [])}",
    )
    check(
        checks,
        "cost_basis_coverage",
        "Existing holding costs are captured enough to support recommendation and performance tracking.",
        "partially_proven" if portfolio_summary.get("known_or_partial_cost_count", 0) > 0 else "missing",
        (
            f"holdings={portfolio_summary.get('holdings_count')}; "
            f"avg_cost_present={portfolio_summary.get('cost_basis_count')}; "
            f"partial_lot_symbols={portfolio_summary.get('partial_lot_symbols')}; "
            f"known_lot_cost_basis_usd={portfolio_summary.get('known_lot_cost_basis_usd')}; "
            f"missing_material_symbols={portfolio_summary.get('missing_material_cost_basis_symbols')}"
        ),
        "warning",
        "Import missing material lots, especially lcETH and residual ADA/SOL history, before claiming full cost-aware performance.",
    )
    check(
        checks,
        "staking_and_cash_rails",
        "Ledger distinguishes crypto and US equity rails and captures staking/lock/liquidity fields.",
        "proven"
        if portfolio_summary.get("has_lceth_staking")
        and portfolio_summary.get("has_ada_staking")
        and portfolio_summary.get("has_sol_staking")
        and portfolio_summary.get("cross_rail_transfer_assumption") == "not_assumed"
        else "partially_proven",
        f"lcETH={portfolio_summary.get('has_lceth_staking')}; ADA={portfolio_summary.get('has_ada_staking')}; SOL={portfolio_summary.get('has_sol_staking')}; crypto_rail={portfolio_summary.get('crypto_rail_status')}; us_rail={portfolio_summary.get('us_equity_rail_status')}; cross_rail={portfolio_summary.get('cross_rail_transfer_assumption')}",
        "pass" if portfolio_summary.get("us_equity_rail_status") != "missing" else "warning",
        "US equity cash rail remains missing/degraded; confirm broker buying power before sizing US trades." if portfolio_summary.get("us_equity_rail_status") == "missing" else None,
    )

    goal_path = (dashboard.get("goal_path") or {})
    monthly_dca = dashboard.get("monthly_dca_usd")
    check(
        checks,
        "five_and_ten_year_10x_math",
        "Plan quantifies 5-year and 10-year 10x required return using current principal and $1,000 monthly DCA.",
        "proven"
        if goal_path.get("five_year_current_principal_required_annual_pct")
        and goal_path.get("ten_year_current_principal_required_annual_pct")
        and float(monthly_dca or 0) == 1000.0
        else "missing",
        f"monthly_dca={monthly_dca}; 5y_current_required={goal_path.get('five_year_current_principal_required_annual_pct')}; 10y_current_required={goal_path.get('ten_year_current_principal_required_annual_pct')}; 5y_cumulative_required={goal_path.get('five_year_cumulative_capital_required_annual_pct')}; 10y_cumulative_required={goal_path.get('ten_year_cumulative_capital_required_annual_pct')}",
    )
    price_scenario_cfg = manual_config.get("long_term_price_scenario_panel") or {}
    price_scenario_policy = MANUAL_ROOT / "references" / "LONG_TERM_PRICE_SCENARIO_POLICY.md"
    price_scenario_policy_text = read_text(price_scenario_policy)
    price_scenario_wired = bool(
        price_scenario_cfg.get("enabled") is True
        and price_scenario_cfg.get("required_for_long_term_dca") is True
        and price_scenario_policy.exists()
        and "ETH-Specific Rule" in price_scenario_policy_text
        and "build_long_term_price_scenario_panel" in read_text(MANUAL_ROOT / "scripts" / "build_daily_report_context.py")
        and "render_long_term_price_scenario_section" in read_text(MANUAL_ROOT / "scripts" / "generate_manual_report.py")
        and "long_term_price_scenario_panel_present" in read_text(MANUAL_ROOT / "scripts" / "report_integrity_audit.py")
        and "price_scenario_maps_goal_to_dca_implication" in read_text(MANUAL_ROOT / "scripts" / "objective_coverage_audit.py")
        and {"ETH", "SOL", "ADA", "NIGHT"}.issubset(price_scenario_symbols)
        and "Long-Term Price Scenario Panel" in report_text
    )
    check(
        checks,
        "long_term_price_scenario_gate",
        "Long-term DCA decisions include 5-year/10-year survival/base/bull price scenarios and goal-fit labels for relevant crypto assets.",
        "proven" if price_scenario_wired else "partially_proven" if price_scenario_cfg.get("enabled") is True else "missing",
        (
            f"config_enabled={price_scenario_cfg.get('enabled')}; "
            f"required_for_long_term_dca={price_scenario_cfg.get('required_for_long_term_dca')}; "
            f"policy_exists={price_scenario_policy.exists()}; "
            f"policy_has_eth_rule={'ETH-Specific Rule' in price_scenario_policy_text}; "
            f"context_symbols={sorted(price_scenario_symbols)}; "
            f"panel_status={price_scenario_panel.get('status')}; "
            f"report_panel_present={'Long-Term Price Scenario Panel' in report_text}; "
            f"context_has_core_assets={ {'ETH', 'SOL', 'ADA', 'NIGHT'}.issubset(price_scenario_symbols) }"
        ),
        "pass" if price_scenario_wired else "warning",
        "Run the next formal manual report with refreshed long_term_price_scenario_panel, then rerun report_integrity_audit.py and objective_coverage_audit.py." if not price_scenario_wired else None,
    )
    asset_goal_summary = (context.get("asset_goal_contribution_panel") or {}).get("summary") or {}
    dca_guidance = asset_goal_summary.get("dca_guidance") or {}
    dca_timing_decisions = dca_guidance.get("long_horizon_timing_decisions") or []
    dca_timing_symbols = {
        item.get("symbol")
        for item in dca_timing_decisions
        if isinstance(item, dict) and item.get("symbol")
    }
    dca_timing_required_fields = {
        "decision",
        "planned_wait_days",
        "planned_amount_usd",
        "staking_wait_cost_usd",
        "front_load_extra_months",
        "front_load_amount_usd",
        "near_term_total_budget_usd",
        "required_pullback_to_wait_pct",
        "long_term_value_zone_status",
        "reason",
        "plain_language_note",
    }
    dca_timing_field_complete = bool(
        dca_timing_decisions
        and all(
            dca_timing_required_fields.issubset(set(item))
            and item.get("decision")
            and item.get("required_pullback_to_wait_pct") is not None
            for item in dca_timing_decisions
            if isinstance(item, dict) and item.get("symbol") in {"SOL", "ADA"}
        )
    )
    dca_timing_config = (manual_config.get("goal_oriented_dca") or {}).get("long_horizon_low_value_acceleration_gate") or {}
    dca_timing_cost_model = dca_timing_config.get("staking_opportunity_cost_model") or {}
    dca_timing_config_ok = bool(
        dca_timing_config.get("enabled") is True
        and dca_timing_cost_model.get("enabled") is True
        and "required_pullback_to_wait_pct" in (dca_timing_cost_model.get("required_fields") or [])
        and "front_load_amount_usd" in (dca_timing_cost_model.get("required_fields") or [])
        and "near_term_total_budget_usd" in (dca_timing_cost_model.get("required_fields") or [])
        and "accelerated_dca" in (dca_timing_config.get("allowed_timing_decisions") or [])
        and "limit_order_wait" in (dca_timing_config.get("allowed_timing_decisions") or [])
    )
    dca_timing_report_ok = bool(
        "长期低位 / 质押机会成本判断" in report_text
        and "required_pullback_to_wait" in report_text
        and "等待少拿质押" in report_text
        and "可前置" in report_text
        and "近期待投入上限" in report_text
    )
    report_integrity_checks = report_integrity.get("checks") or []
    objective_checks = objective_coverage.get("checks") or []
    dca_timing_integrity_ok = any(
        item.get("name") == "long_horizon_dca_timing_panel_present" and item.get("passed") is True
        for item in report_integrity_checks
        if isinstance(item, dict)
    )
    dca_timing_objective_ok = any(
        item.get("name") == "long_horizon_dca_timing_covers_staking_opportunity_cost" and item.get("passed") is True
        for item in objective_checks
        if isinstance(item, dict)
    )
    dca_timing_scripts_ok = bool(
        "def long_horizon_timing_decision" in read_text(MANUAL_ROOT / "scripts" / "asset_goal_contribution.py")
        and "long_horizon_timing_decision" in read_text(MANUAL_ROOT / "scripts" / "generate_manual_report.py")
        and "long_horizon_dca_timing_panel_present" in read_text(MANUAL_ROOT / "scripts" / "report_integrity_audit.py")
        and "long_horizon_dca_timing_covers_staking_opportunity_cost" in read_text(MANUAL_ROOT / "scripts" / "objective_coverage_audit.py")
        and "long_horizon_timing_decision" in read_text(MANUAL_ROOT / "scripts" / "recommendation_history.py")
    )
    long_horizon_dca_timing_ok = bool(
        dca_timing_config_ok
        and dca_timing_scripts_ok
        and dca_timing_report_ok
        and dca_timing_field_complete
        and {"SOL", "ADA"}.issubset(dca_timing_symbols)
        and dca_timing_integrity_ok
        and dca_timing_objective_ok
    )
    check(
        checks,
        "long_horizon_dca_timing_gate",
        "Long-term DCA timing compares long-horizon value zones, staking opportunity cost, missed-upside risk, and waiting-for-pullback discipline before sizing new crypto buys.",
        "proven" if long_horizon_dca_timing_ok else "missing",
        (
            f"config_ok={dca_timing_config_ok}; scripts_ok={dca_timing_scripts_ok}; "
            f"report_panel={dca_timing_report_ok}; context_symbols={sorted(dca_timing_symbols)}; "
            f"field_complete={dca_timing_field_complete}; "
            f"integrity_check={dca_timing_integrity_ok}; objective_check={dca_timing_objective_ok}; "
            f"decisions={[{'symbol': item.get('symbol'), 'decision': item.get('decision'), 'required_pullback_to_wait_pct': item.get('required_pullback_to_wait_pct'), 'front_load_amount_usd': item.get('front_load_amount_usd'), 'near_term_total_budget_usd': item.get('near_term_total_budget_usd')} for item in dca_timing_decisions if isinstance(item, dict)]}"
        ),
        "pass" if long_horizon_dca_timing_ok else "error",
        "Run a formal manual report after the long-horizon DCA timing patch, then rerun report_integrity_audit.py and objective_coverage_audit.py with DCA recommendation records." if not long_horizon_dca_timing_ok else None,
    )
    check(
        checks,
        "goal_outcome_not_yet_verified",
        "Do not claim the financial 10x target has already been achieved.",
        "blocked_by_evidence",
        f"Current portfolio value is {portfolio.get('total_value')}; dashboard states 5y status={goal_path.get('five_year_status')} and 10y status={goal_path.get('ten_year_status')}. This is a plan/evidence system, not achieved 10x outcome.",
        "warning",
        "Keep goal active until actual portfolio growth proves 10x or until user revises target.",
    )

    crypto_plan = dashboard.get("crypto_dca_operating_plan") or {}
    check(
        checks,
        "crypto_dca_plan",
        "Manual reports produce target-driven crypto DCA direction, not default BTC buying.",
        "proven" if crypto_plan.get("primary_pair") and crypto_plan.get("secondary_pair") else "missing",
        f"primary={crypto_plan.get('primary_pair')}; secondary={crypto_plan.get('secondary_pair')}; satellite={crypto_plan.get('satellite_pair')}; max_action={crypto_plan.get('max_allowed_action') or crypto_plan.get('max_action')}",
    )

    us_state = dashboard.get("us_tactical_operating_state") or {}
    check(
        checks,
        "us_tactical_50pct_goal",
        "The tactical sleeve tracks the monthly ROI 100% attack goal separately from long-term protected holdings and DCA principal.",
        "proven" if us_state.get("monthly_gap_usd") is not None and "CRCL" in (us_state.get("protected_symbols_excluded") or []) else "missing",
        f"sleeve={us_state.get('sleeve_id')}; current={us_state.get('current_tactical_value_usd')}; monthly_gap={us_state.get('monthly_gap_usd')}; cash_drag={us_state.get('tactical_cash_drag_pct')}; protected={us_state.get('protected_symbols_excluded')}; data_quality={us_state.get('data_quality')}",
        "warning" if us_state.get("data_quality") == "degraded_user_stated_cash" else "pass",
        "Broker cash/buying power export is still needed before real US tactical sizing." if us_state.get("data_quality") == "degraded_user_stated_cash" else None,
    )

    traceability_cfg = manual_config.get("objective_traceability_gate") or {}
    traceability_doc = MANUAL_ROOT / "references" / "OBJECTIVE_TO_MECHANISM_TRACEABILITY.md"
    traceability_doc_text = read_text(traceability_doc)
    traceability_ok = bool(
        traceability_cfg.get("enabled") is True
        and traceability_cfg.get("required") is True
        and traceability_doc.exists()
        and "Requirement Matrix" in traceability_doc_text
        and "Long-Term Price Scenario Panel" in traceability_doc_text
        and "long_term_price_scenario_panel" in traceability_doc_text
        and "5_year_10x_aggressive_path" in (traceability_cfg.get("required_objectives") or [])
        and "manual_confirmation_no_auto_trading" in (traceability_cfg.get("required_objectives") or [])
        and "render_objective_traceability_section" in read_text(MANUAL_ROOT / "scripts" / "generate_manual_report.py")
    )
    traceability_panel_present = "目标到机制映射" in report_text if report_text else True
    check(
        checks,
        "objective_traceability_gate",
        "User objectives are mapped to report panels, scripts, evidence, and downgrade behavior.",
        "proven" if traceability_ok and traceability_panel_present else "partially_proven" if traceability_ok else "missing",
        (
            f"config_enabled={traceability_cfg.get('enabled')}; required={traceability_cfg.get('required')}; "
            f"doc_exists={traceability_doc.exists()}; required_objectives={traceability_cfg.get('required_objectives')}; "
            f"price_scenario_mapped={'Long-Term Price Scenario Panel' in traceability_doc_text and 'long_term_price_scenario_panel' in traceability_doc_text}; "
            f"report_panel_present={traceability_panel_present}"
        ),
        "pass" if traceability_ok and traceability_panel_present else "warning",
        "Run the next formal manual report so the Objective Traceability panel appears in the audited report." if traceability_ok and not traceability_panel_present else None,
    )

    strategy_iteration_cfg = manual_config.get("strategy_iteration_backlog_gate") or {}
    strategy_iteration_script = MANUAL_ROOT / "scripts" / "strategy_iteration_backlog.py"
    strategy_iteration_context = context.get("strategy_iteration_backlog_panel") or {}
    candidate_strategies = strategy_iteration_context.get("candidate_strategies") or []
    strategy_iteration_script_text = read_text(strategy_iteration_script)
    strategy_iteration_wired = bool(
        strategy_iteration_cfg.get("enabled") is True
        and strategy_iteration_cfg.get("required") is True
        and strategy_iteration_script.exists()
        and "strategy_iteration_backlog_panel" in read_text(MANUAL_ROOT / "scripts" / "generate_manual_report.py")
        and len(strategy_iteration_cfg.get("required_strategy_candidates") or []) >= 6
        and "crypto_drawdown_weighted_dca" in strategy_iteration_script_text
        and "us_short_mid_trend_relay" in strategy_iteration_script_text
    )
    strategy_iteration_report_present = "下一轮策略迭代候选" in report_text if report_text else True
    strategy_iteration_context_present = bool(candidate_strategies)
    check(
        checks,
        "strategy_iteration_backlog",
        "The system can keep searching for better crypto DCA, staking, tail-risk, and US tactical strategies without promoting them directly to live trades.",
        "proven"
        if strategy_iteration_wired and (strategy_iteration_report_present or strategy_iteration_context_present)
        else "partially_proven"
        if strategy_iteration_wired
        else "missing",
        (
            f"config_enabled={strategy_iteration_cfg.get('enabled')}; required={strategy_iteration_cfg.get('required')}; "
            f"script_exists={strategy_iteration_script.exists()}; "
            f"required_candidates={strategy_iteration_cfg.get('required_strategy_candidates')}; "
            f"context_candidates={len(candidate_strategies)}; "
            f"report_panel_present={strategy_iteration_report_present}; "
            f"max_action={strategy_iteration_cfg.get('max_allowed_action_for_new_strategy')}"
        ),
        "pass" if strategy_iteration_wired and (strategy_iteration_report_present or strategy_iteration_context_present) else "warning",
        "Run the next manual report so strategy_iteration_backlog_panel is written into the audited context." if strategy_iteration_wired and not (strategy_iteration_report_present or strategy_iteration_context_present) else None,
    )

    research = context.get("research_panel") or {}
    research_validation = context.get("research_panel_validation") or {}
    external_research_validation = research.get("external_agent_validation") or {}
    research_quality_gate_passed = external_research_validation.get("committee_quality_gate_passed")
    quality_requirements = external_research_validation.get("committee_quality_gate_requirements") or {}
    evidence_verified_roles = external_research_validation.get(
        "evidence_verified_known_role_count",
        external_research_validation.get("verified_known_role_count"),
    )
    min_evidence_roles = quality_requirements.get("min_evidence_verified_known_roles", 6)
    min_ok_sources = quality_requirements.get("min_roles_with_ok_sources", 6)
    research_gate_warning = bool(research.get("research_committee_degraded")) or research_quality_gate_passed is False
    check(
        checks,
        "research_committee_gate",
        "Research Committee gate exists, validates subagent roles, and blocks execute_now when degraded.",
        "proven" if file_exists(MANUAL_ROOT / "references" / "SUBAGENT_ORCHESTRATION_RUNBOOK.md") and context.get("report_readiness", {}).get("execute_now_allowed") is False else "missing",
        (
            f"method={research.get('research_method')}; degraded={research.get('research_committee_degraded')}; "
            f"validation_status={research_validation.get('status')}; quality_gate={research_quality_gate_passed}; "
            f"evidence_verified_roles={evidence_verified_roles}; "
            f"ok_source_roles={external_research_validation.get('roles_with_ok_source_count')}; "
            f"execute_now={context.get('report_readiness', {}).get('execute_now_allowed')}"
        ),
        "warning" if research_gate_warning else "pass",
        (
            "Run the next market report with higher-quality external subagent outputs: "
            f"at least {min_evidence_roles} evidence-verified roles and {min_ok_sources} roles with ok sources."
        ) if research_gate_warning else None,
    )

    latest_evidence_backlog = newest(str(MANUAL_ROOT / "experiments" / "research_evidence_backlog_*.json"))
    evidence_backlog = load_json(latest_evidence_backlog) if latest_evidence_backlog else {}
    backlog_tasks = evidence_backlog.get("research_evidence_tasks") or []
    backlog_goal = evidence_backlog.get("evidence_goal") or {}
    check(
        checks,
        "research_evidence_backlog",
        "Failed Research Committee quality audits produce a role-specific evidence repair backlog for the next subagent run.",
        "proven" if latest_evidence_backlog and backlog_tasks else "missing",
        (
            f"backlog={latest_evidence_backlog}; tasks={len(backlog_tasks)}; "
            f"evidence_verified={backlog_goal.get('current_evidence_verified_roles')}/{backlog_goal.get('target_evidence_verified_roles')}; "
            f"additional_needed={backlog_goal.get('additional_evidence_verified_roles_needed')}"
        )
        if latest_evidence_backlog
        else "No research_evidence_backlog_*.json artifact found.",
        "pass" if latest_evidence_backlog and backlog_tasks else "warning",
        None if latest_evidence_backlog and backlog_tasks else "Run research_evidence_backlog_builder.py after failed quality audits.",
    )

    latest_task_package = newest(str(MANUAL_ROOT / "experiments" / "research_subagent_task_package_*.json"))
    task_package = load_json(latest_task_package) if latest_task_package else {}
    task_files = task_package.get("task_files") or []
    task_json_paths = [Path(item.get("task_json", "")) for item in task_files]
    prompt_paths = [Path(item.get("prompt_markdown", "")) for item in task_files]
    all_task_files_exist = bool(task_files) and all(path.exists() for path in task_json_paths + prompt_paths)
    check(
        checks,
        "research_subagent_task_package",
        "Evidence backlog can be packaged into per-role subagent task files and prompts for the next external research run.",
        "proven" if latest_task_package and all_task_files_exist else "missing",
        (
            f"package={latest_task_package}; tasks={len(task_files)}; "
            f"external_output_target={task_package.get('external_output_target')}; files_exist={all_task_files_exist}"
        )
        if latest_task_package
        else "No research_subagent_task_package_*.json artifact found.",
        "pass" if latest_task_package and all_task_files_exist else "warning",
        None if latest_task_package and all_task_files_exist else "Run research_subagent_task_packager.py after generating evidence backlog.",
    )

    latest_output_collection = newest(str(MANUAL_ROOT / "experiments" / "research_subagent_output_collection_*.json"))
    output_collection = load_json(latest_output_collection) if latest_output_collection else {}
    collection_manifest = output_collection.get("collection_manifest") or {}
    collected_agent_count = collection_manifest.get("collected_agent_count", 0)
    expected_agent_count = count_items(collection_manifest.get("expected_agents"))
    collection_validation = collection_manifest.get("validation") or {}
    collection_has_manifest = bool(collection_manifest)
    check(
        checks,
        "research_subagent_output_collection",
        "Per-role subagent outputs can be collected, deduplicated, and pre-validated before Research Committee quality audit.",
        "proven" if latest_output_collection and collection_has_manifest else "missing",
        (
            f"collection={latest_output_collection}; collected={collected_agent_count}/{expected_agent_count}; "
            f"missing={collection_manifest.get('missing_agents')}; "
            f"quality_gate={collection_validation.get('committee_quality_gate_passed')}"
        )
        if latest_output_collection
        else "No research_subagent_output_collection_*.json artifact found.",
        "pass" if latest_output_collection and collection_has_manifest and collected_agent_count >= 6 else "warning",
        None
        if latest_output_collection and collection_has_manifest and collected_agent_count >= 6
        else "Run research_subagent_output_collector.py after subagents write their per-role outputs.",
    )

    closure_script = MANUAL_ROOT / "scripts" / "goal_evidence_closure_packager.py"
    evidence_request_script = MANUAL_ROOT / "scripts" / "portfolio_evidence_request_report.py"
    warning_explainer_script = MANUAL_ROOT / "scripts" / "audit_warning_explainer.py"
    import_validator_script = MANUAL_ROOT / "scripts" / "goal_evidence_import_validator.py"
    import_preview_script = MANUAL_ROOT / "scripts" / "goal_evidence_import_preview.py"
    latest_closure_json = newest(str(MANUAL_ROOT / "experiments" / "goal_evidence_closure_package_*.json"))
    latest_closure_md = newest(str(MANUAL_ROOT / "reports" / "goal_evidence_closure_package_*.md"))
    latest_evidence_request_json = newest(str(MANUAL_ROOT / "experiments" / "portfolio_evidence_request_report_*.json"))
    latest_evidence_request_md = newest(str(MANUAL_ROOT / "reports" / "portfolio_evidence_request_report_*.md"))
    latest_import_validation = newest(str(MANUAL_ROOT / "experiments" / "goal_evidence_import_validation_*.json"))
    latest_import_preview = newest(str(MANUAL_ROOT / "experiments" / "goal_evidence_import_preview_*.json"))
    closure_payload = load_json(latest_closure_json) if latest_closure_json else {}
    evidence_request_payload = load_json(latest_evidence_request_json) if latest_evidence_request_json else {}
    import_validation_payload = load_json(latest_import_validation) if latest_import_validation else {}
    import_preview_payload = load_json(latest_import_preview) if latest_import_preview else {}
    closure_items = closure_payload.get("closure_items") or []
    check(
        checks,
        "goal_evidence_closure_package",
        "Unresolved goal blockers are packaged into a read-only evidence closure checklist.",
        "proven" if closure_script.exists() and latest_closure_json and latest_closure_md and closure_items else "missing",
        (
            f"script_exists={closure_script.exists()}; latest_json={latest_closure_json}; "
            f"latest_md={latest_closure_md}; closure_items={len(closure_items)}; "
            f"read_only={closure_payload.get('read_only')}; live_orders_enabled={closure_payload.get('live_orders_enabled')}"
        ),
        "pass" if latest_closure_json and latest_closure_md and closure_items else "warning",
        None if latest_closure_json and latest_closure_md and closure_items else "Run goal_evidence_closure_packager.py after the next completion audit.",
    )
    evidence_request_cfg = (dispatch_runner.get("portfolio_evidence_request_report") or {})
    evidence_request_summary = (evidence_request_payload or {}).get("summary") or {}
    evidence_request_items = (evidence_request_payload or {}).get("user_requests") or []
    evidence_request_ok = bool(
        evidence_request_cfg.get("enabled") is True
        and evidence_request_cfg.get("script") == "scripts/portfolio_evidence_request_report.py"
        and evidence_request_cfg.get("live_orders_enabled") is False
        and evidence_request_cfg.get("mutates_ledgers") is False
        and evidence_request_script.exists()
        and latest_evidence_request_json
        and latest_evidence_request_md
        and evidence_request_payload.get("read_only") is True
        and evidence_request_payload.get("live_orders_enabled") is False
        and evidence_request_payload.get("mutates_ledgers") is False
        and len(evidence_request_items) >= 3
        and "钱包助记词、私钥、seed phrase" in (evidence_request_payload.get("do_not_provide") or [])
        and evidence_request_summary.get("blocking_gap_count") is not None
    )
    check(
        checks,
        "portfolio_evidence_request_report",
        "Portfolio evidence gaps can be converted into a user-facing checklist without requesting sensitive permissions.",
        "proven" if evidence_request_ok else "missing",
        (
            f"config_enabled={evidence_request_cfg.get('enabled')}; "
            f"script={evidence_request_cfg.get('script')}; "
            f"script_exists={evidence_request_script.exists()}; "
            f"latest_json={latest_evidence_request_json}; latest_md={latest_evidence_request_md}; "
            f"read_only={evidence_request_payload.get('read_only')}; "
            f"live_orders_enabled={evidence_request_payload.get('live_orders_enabled')}; "
            f"mutates_ledgers={evidence_request_payload.get('mutates_ledgers')}; "
            f"user_requests={len(evidence_request_items)}; "
            f"blocking_gap_count={evidence_request_summary.get('blocking_gap_count')}"
        ),
        "pass" if evidence_request_ok else "warning",
        "Run portfolio_evidence_request_report.py after portfolio_evidence_gap_packager.py." if not evidence_request_ok else None,
    )
    readability_policy = read_text(MANUAL_ROOT / "references" / "REPORT_READABILITY_AND_TERMS_POLICY.md")
    status_script_text = read_text(status_report_script)
    report_script_text = read_text(MANUAL_ROOT / "scripts" / "generate_manual_report.py")
    warning_explainer_ok = bool(
        warning_explainer_script.exists()
        and "audit_warning_explainer.py" in readability_policy
        and "plain_warning_explanations" in status_script_text
        and "build_warning_explanations" in report_script_text
    )
    check(
        checks,
        "audit_warning_plain_language_explainer",
        "Audit warnings are translated into plain Chinese in status and report audit panels.",
        status_from(warning_explainer_ok),
        (
            f"script_exists={warning_explainer_script.exists()}; "
            f"policy_mentions={'audit_warning_explainer.py' in readability_policy}; "
            f"status_report_uses_plain_warnings={'plain_warning_explanations' in status_script_text}; "
            f"daily_report_uses_explainer={'build_warning_explanations' in report_script_text}"
        ),
        "pass" if warning_explainer_ok else "warning",
        "Use scripts/audit_warning_explainer.py in status and report audit panels." if not warning_explainer_ok else None,
    )
    check(
        checks,
        "goal_evidence_import_validator",
        "User-filled evidence templates can be validated before any ledger import or strategy upgrade.",
        "proven" if import_validator_script.exists() and latest_import_validation and import_validation_payload.get("read_only") is True else "missing",
        (
            f"script_exists={import_validator_script.exists()}; latest_validation={latest_import_validation}; "
            f"status={import_validation_payload.get('status')}; read_only={import_validation_payload.get('read_only')}; "
            f"candidate_gap_closures={(import_validation_payload.get('summary') or {}).get('candidate_gap_closures')}"
        ),
        "pass" if import_validator_script.exists() and latest_import_validation else "warning",
        "Run goal_evidence_import_validator.py after filling import templates." if not latest_import_validation else None,
    )
    import_preview_config = (manual_config.get("goal_evidence_closure_package") or {}).get("import_preview", {})
    check(
        checks,
        "goal_evidence_import_preview",
        "Validated evidence CSVs can be converted into a read-only proposed ledger update preview before any human import.",
        "proven"
        if import_preview_script.exists()
        and import_preview_config.get("enabled") is True
        and import_preview_config.get("mutates_ledger") is False
        and (not import_preview_payload or import_preview_payload.get("mutates_ledger") is False)
        and (not import_preview_payload or import_preview_payload.get("live_orders_enabled") is False)
        else "missing",
        (
            f"script_exists={import_preview_script.exists()}; latest_preview={latest_import_preview}; "
            f"status={import_preview_payload.get('status')}; read_only={import_preview_payload.get('read_only')}; "
            f"mutates_ledger={import_preview_payload.get('mutates_ledger')}; "
            f"live_orders_enabled={import_preview_payload.get('live_orders_enabled')}"
        ),
        "pass" if import_preview_payload else "warning",
        "Run goal_evidence_import_preview.py after CSV validation to inspect proposed updates before any manual import." if not import_preview_payload else None,
    )

    evidence = dashboard.get("evidence_and_gate_state") or {}
    validation_gaps = evidence.get("validation_sample_gaps") or {}
    check(
        checks,
        "paper_validation_gate",
        "Short-term tactical strategies are gated by paper/walk-forward evidence before real execute_now.",
        "blocked_by_evidence"
        if evidence.get("short_term_tactical_max_allowed_action") == "paper_only"
        else "partially_proven",
        f"validation_status={evidence.get('validation_sample_status')}; short_term_max={evidence.get('short_term_tactical_max_allowed_action')}; failed={evidence.get('validation_sample_failed_gates')}; gaps={validation_gaps}",
        "warning",
        "Continue paper validation: need more closed paper trades, calibrated outcomes, and at least one target research pass.",
    )

    check(
        checks,
        "recommendation_learning_loop",
        "Recommendation history exists and supports learning-loop review, but resolved calibration must be sufficient before claiming true 80% probabilities.",
        "partially_proven" if recommendation_summary.get("total", 0) > 0 and recommendation_summary.get("outcome_reviews", 0) > 0 else "missing",
        f"total={recommendation_summary.get('total')}; pending={recommendation_summary.get('pending')}; resolved={recommendation_summary.get('resolved')}; superseded={recommendation_summary.get('superseded')}; outcome_reviews={recommendation_summary.get('outcome_reviews')}; proposed_changes={recommendation_summary.get('proposed_changes')}",
        "warning",
        "Resolve at least 10 hit/failed/not_triggered/expired/invalidated outcomes for calibration.",
    )
    recommendation_history_cfg = manual_config.get("recommendation_history") or {}
    calibration_script = MANUAL_ROOT / (
        recommendation_history_cfg.get("calibration_packager_script") or "scripts/recommendation_calibration_packager.py"
    )
    outcome_reviewer_script = MANUAL_ROOT / (
        recommendation_history_cfg.get("outcome_reviewer_script") or "scripts/recommendation_outcome_reviewer.py"
    )
    calibration_gate = recommendation_calibration_package.get("calibration_gate") or {}
    calibration_counts = recommendation_calibration_package.get("counts") or {}
    calibration_review_summary = recommendation_calibration_package.get("review_panel_summary") or {}
    calibration_package_ok = bool(
        calibration_script.exists()
        and outcome_reviewer_script.exists()
        and recommendation_history_cfg.get("calibration_package_required_for_monthly_review") is True
        and recommendation_history_cfg.get("calibration_package_mutates_ledger") is False
        and recommendation_history_cfg.get("calibration_confirmation_requires_human") is True
        and recommendation_history_cfg.get("superseded_records_counted_for_calibration") is False
        and recommendation_calibration_package.get("mutates_ledger") is False
        and recommendation_calibration_package.get("live_orders_enabled") is False
        and recommendation_calibration_package.get("human_confirmation_required") is True
        and (recommendation_calibration_package.get("superseded_policy") or {}).get("counted_for_calibration") is False
    )
    check(
        checks,
        "recommendation_calibration_package",
        "Recommendation learning has a read-only calibration package that queues due outcomes for human confirmation and refuses to count superseded records as hit/failed evidence.",
        "proven" if calibration_package_ok else "missing",
        (
            f"script_exists={calibration_script.exists()}; reviewer_exists={outcome_reviewer_script.exists()}; "
            f"latest_package={recommendation_calibration_package_path}; "
            f"mutates_ledger={recommendation_calibration_package.get('mutates_ledger')}; "
            f"live_orders_enabled={recommendation_calibration_package.get('live_orders_enabled')}; "
            f"human_confirmation_required={recommendation_calibration_package.get('human_confirmation_required')}; "
            f"current_resolved={calibration_gate.get('current_resolved')}; "
            f"ready_hit_failed_if_confirmed={calibration_gate.get('ready_hit_failed_if_confirmed')}; "
            f"resolved_needed={calibration_gate.get('resolved_needed_after_ready_confirmations')}; "
            f"due_or_reviewable={calibration_review_summary.get('due_or_reviewable_count')}; "
            f"upcoming={calibration_review_summary.get('upcoming_count')}; "
            f"superseded_not_counted={calibration_counts.get('superseded_not_counted')}"
        ),
        "pass" if calibration_package_ok else "warning",
        "Run recommendation_calibration_packager.py during monthly review or after recommendations become due." if not calibration_package_ok else None,
    )

    learning_state = dashboard.get("progressive_learning_state") or {}
    paper_calendar = dashboard.get("paper_validation_review_calendar") or {}
    manual_learning_cfg = manual_config.get("progressive_learning_confidence_policy") or {}
    check(
        checks,
        "progressive_learning_mechanism",
        "System may start with judgment-only evidence, graduate to wide intervals at n=10-29, and use calibrated untouched holdouts at n>=30.",
        "proven"
        if manual_learning_cfg.get("enabled") is True
        and learning_state.get("status") == "active"
        and learning_state.get("validated_probability_target_pct") is None
        and file_exists(ACTIVE_ROOT / "scripts" / "validation_progress_runner.py")
        and file_exists(ACTIVE_ROOT / "scripts" / "validation_sample_auditor.py")
        else "missing",
        (
            f"policy_enabled={manual_learning_cfg.get('enabled')}; stage={learning_state.get('learning_stage')}; "
            f"paper_closed={learning_state.get('paper_closed_count')}; paper_win_rate={learning_state.get('paper_win_rate_pct')}; "
            f"rec_resolved={learning_state.get('recommendation_resolved_count')}; "
            f"floor={learning_state.get('learning_floor_pct')} -> target={learning_state.get('validated_probability_target_pct')}; "
            f"next_paper_reviews={len(paper_calendar.get('next_open_position_reviews') or [])}; "
            f"runner_mode={paper_calendar.get('recommended_runner_mode')}"
        ),
        "pass" if learning_state.get("status") == "active" else "warning",
        "Keep resolving paper trades and recommendation outcomes; do not upgrade actions until the applicable sample, EV, signal and risk gates pass.",
    )
    progressive_iteration_cfg = manual_config.get("progressive_learning_iteration_audit") or {}
    progressive_iteration_script = MANUAL_ROOT / "scripts" / "progressive_learning_iteration_audit.py"
    latest_progressive_iteration = newest(str(MANUAL_ROOT / "experiments" / "progressive_learning_iteration_audit_*.json"))
    progressive_iteration_payload = load_json(latest_progressive_iteration) if latest_progressive_iteration else {}
    check(
        checks,
        "progressive_learning_iteration_audit",
        "Each dispatch can produce a learning-progress audit that quantifies sample gaps and next evidence tasks.",
        "proven"
        if progressive_iteration_cfg.get("enabled") is True
        and progressive_iteration_cfg.get("required_after_manual_dispatch") is True
        and progressive_iteration_script.exists()
        and (not progressive_iteration_payload or progressive_iteration_payload.get("goal_complete") is False)
        and (not progressive_iteration_payload or progressive_iteration_payload.get("ready_for_progressive_learning_loop") is True)
        else "missing",
        (
            f"config_enabled={progressive_iteration_cfg.get('enabled')}; "
            f"required_after_manual_dispatch={progressive_iteration_cfg.get('required_after_manual_dispatch')}; "
            f"script_exists={progressive_iteration_script.exists()}; latest={latest_progressive_iteration}; "
            f"latest_score={progressive_iteration_payload.get('learning_progress_score_points')}; "
            f"latest_max_action={progressive_iteration_payload.get('max_allowed_action')}; "
            f"goal_complete={progressive_iteration_payload.get('goal_complete')}"
        ),
        "pass" if progressive_iteration_payload else "warning",
        None if progressive_iteration_payload else "Run progressive_learning_iteration_audit.py after the next manual dispatch.",
    )

    learning_calendar_cfg = (dispatch_runner.get("learning_review_calendar") or {})
    learning_calendar_script = MANUAL_ROOT / "scripts" / "learning_review_calendar.py"
    learning_calendar_summary = learning_calendar.get("summary") or {}
    learning_calendar_paper = learning_calendar.get("paper_review") or {}
    learning_calendar_rec = learning_calendar.get("recommendation_review") or {}
    learning_calendar_ok = bool(
        learning_calendar_cfg.get("enabled_by_default_outside_smoke") is True
        and learning_calendar_script.exists()
        and learning_calendar.get("status") == "ok"
        and learning_calendar.get("manual_dispatch_only") is True
        and learning_calendar.get("live_orders_enabled") is False
        and learning_calendar.get("mutates_real_portfolio") is False
        and learning_calendar.get("mutates_recommendation_ledger") is False
        and isinstance(learning_calendar_summary.get("early_calibration_needed"), dict)
        and isinstance(learning_calendar_summary.get("usable_calibration_needed"), dict)
        and learning_calendar_paper.get("due_now_count") is not None
        and learning_calendar_paper.get("expiring_24h_count") is not None
    )
    check(
        checks,
        "learning_review_calendar",
        "Each manual dispatch can show the next paper/recommendation review, due-now vs within-24h distinction, and early/usable calibration gaps.",
        "proven" if learning_calendar_ok else "missing",
        (
            f"config_enabled={learning_calendar_cfg.get('enabled_by_default_outside_smoke')}; "
            f"script_exists={learning_calendar_script.exists()}; latest={learning_calendar_path}; "
            f"status={learning_calendar.get('status')}; "
            f"next_action_hint={learning_calendar_summary.get('next_action_hint')}; "
            f"next_paper_review_at={learning_calendar_summary.get('next_paper_review_at')}; "
            f"next_recommendation_review_at={learning_calendar_summary.get('next_recommendation_review_at')}; "
            f"early_needed={learning_calendar_summary.get('early_calibration_needed')}; "
            f"usable_needed={learning_calendar_summary.get('usable_calibration_needed')}; "
            f"paper_due_now={learning_calendar_paper.get('due_now_count')}; "
            f"paper_expiring_24h={learning_calendar_paper.get('expiring_24h_count')}; "
            f"rec_due_now={learning_calendar_rec.get('due_now_count')}"
        ),
        "pass" if learning_calendar_ok else "warning",
        "Run learning_review_calendar.py or a non-smoke manual dispatch to refresh the review calendar." if not learning_calendar_ok else None,
    )

    integrity_failed = count_items(report_integrity.get("failed"))
    integrity_warnings = count_items(report_integrity.get("warnings"))
    objective_failed = count_items(objective_coverage.get("failed"))
    objective_warnings = count_items(objective_coverage.get("warnings"))
    check(
        checks,
        "latest_report_audits",
        "Latest target report has integrity and objective-coverage audits.",
        "proven" if integrity_failed == 0 and objective_failed == 0 and report_integrity.get("status") == "ok" and objective_coverage.get("status") == "ok" else "missing",
        f"report_integrity_status={report_integrity.get('status')}; failed={integrity_failed}; warnings={integrity_warnings}; objective_status={objective_coverage.get('status')}; objective_failed={objective_failed}; objective_warnings={objective_warnings}",
        "warning" if integrity_warnings or objective_warnings else "pass",
    )

    report_section_groups = [
        ["## 1. 一页结论"],
        ["## 2. 持仓总览"],
        ["目标差距与资产贡献"],
        ["Long-Term Price Scenario Panel"],
        ["目标执行总控台"],
        ["策略库与晋级状态"],
        ["旧结论挑战与 Research Committee"],
        ["Crypto DCA 方向"],
        ["美股战术池与接力"],
        ["缺失数据", "降级", "最终动作"],
    ]
    missing_section_groups = [
        group
        for group in report_section_groups
        if not any(needle in report_text for needle in group)
    ]
    check(
        checks,
        "readable_report_structure",
        "Manual report exposes one-page conclusion, portfolio, goal dashboard, DCA, US tactical, and research committee panels.",
        "proven" if not missing_section_groups else "missing",
        f"report={report_path}; missing_section_groups={missing_section_groups}",
    )

    installed_pairs = [
        (MANUAL_ROOT / "SKILL.md", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "SKILL.md"),
        (MANUAL_ROOT / "config" / "manual_strategy_config.json", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "config" / "manual_strategy_config.json"),
        (MANUAL_ROOT / "references" / "GOAL_10X_OPERATING_PLAYBOOK.md", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "references" / "GOAL_10X_OPERATING_PLAYBOOK.md"),
        (MANUAL_ROOT / "references" / "OBJECTIVE_TO_MECHANISM_TRACEABILITY.md", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "references" / "OBJECTIVE_TO_MECHANISM_TRACEABILITY.md"),
        (MANUAL_ROOT / "references" / "GOAL_ORIENTED_DCA_POLICY.md", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "references" / "GOAL_ORIENTED_DCA_POLICY.md"),
        (MANUAL_ROOT / "references" / "LONG_TERM_PRICE_SCENARIO_POLICY.md", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "references" / "LONG_TERM_PRICE_SCENARIO_POLICY.md"),
        (MANUAL_ROOT / "references" / "PASSIVE_DISPATCH_RUNTIME_POLICY.md", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "references" / "PASSIVE_DISPATCH_RUNTIME_POLICY.md"),
        (MANUAL_ROOT / "references" / "MANUAL_DISPATCH_STRATEGY_CONTRACT.md", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "references" / "MANUAL_DISPATCH_STRATEGY_CONTRACT.md"),
        (MANUAL_ROOT / "scripts" / "build_daily_report_context.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "build_daily_report_context.py"),
        (MANUAL_ROOT / "scripts" / "asset_goal_contribution.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "asset_goal_contribution.py"),
        (MANUAL_ROOT / "scripts" / "manual_dispatch_run.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "manual_dispatch_run.py"),
        (MANUAL_ROOT / "scripts" / "learning_review_calendar.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "learning_review_calendar.py"),
        (MANUAL_ROOT / "scripts" / "next_dispatch_readiness.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "next_dispatch_readiness.py"),
        (MANUAL_ROOT / "scripts" / "next_goal_execution_queue.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "next_goal_execution_queue.py"),
        (MANUAL_ROOT / "scripts" / "current_goal_status_report.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "current_goal_status_report.py"),
        (MANUAL_ROOT / "scripts" / "next_manual_dispatch_execution_plan.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "next_manual_dispatch_execution_plan.py"),
        (MANUAL_ROOT / "scripts" / "progressive_learning_iteration_audit.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "progressive_learning_iteration_audit.py"),
        (MANUAL_ROOT / "scripts" / "goal_system_completion_audit.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "goal_system_completion_audit.py"),
        (MANUAL_ROOT / "scripts" / "strategy_iteration_backlog.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "strategy_iteration_backlog.py"),
        (MANUAL_ROOT / "scripts" / "recommendation_history.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "recommendation_history.py"),
        (MANUAL_ROOT / "scripts" / "recommendation_outcome_reviewer.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "recommendation_outcome_reviewer.py"),
        (MANUAL_ROOT / "scripts" / "recommendation_calibration_packager.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "recommendation_calibration_packager.py"),
        (MANUAL_ROOT / "scripts" / "generate_manual_report.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "generate_manual_report.py"),
        (MANUAL_ROOT / "scripts" / "report_integrity_audit.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "report_integrity_audit.py"),
        (MANUAL_ROOT / "scripts" / "objective_coverage_audit.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "objective_coverage_audit.py"),
        (MANUAL_ROOT / "scripts" / "research_panel_runner.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "research_panel_runner.py"),
        (MANUAL_ROOT / "scripts" / "research_evidence_backlog_builder.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "research_evidence_backlog_builder.py"),
        (MANUAL_ROOT / "scripts" / "research_subagent_task_packager.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "research_subagent_task_packager.py"),
        (MANUAL_ROOT / "scripts" / "research_subagent_output_collector.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "research_subagent_output_collector.py"),
        (MANUAL_ROOT / "scripts" / "portfolio_evidence_request_report.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "portfolio_evidence_request_report.py"),
        (MANUAL_ROOT / "scripts" / "audit_warning_explainer.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "audit_warning_explainer.py"),
        (MANUAL_ROOT / "scripts" / "goal_evidence_closure_packager.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "goal_evidence_closure_packager.py"),
        (MANUAL_ROOT / "scripts" / "goal_evidence_import_validator.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "goal_evidence_import_validator.py"),
        (MANUAL_ROOT / "scripts" / "goal_evidence_import_preview.py", CODEX_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "goal_evidence_import_preview.py"),
        (ACTIVE_ROOT / "SKILL.md", CODEX_SKILL_ROOT / "active-alpha-paper-monitor" / "SKILL.md"),
        (ACTIVE_ROOT / "scripts" / "research_panel_bridge.py", CODEX_SKILL_ROOT / "active-alpha-paper-monitor" / "scripts" / "research_panel_bridge.py"),
        (MANUAL_ROOT / "SKILL.md", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "SKILL.md"),
        (MANUAL_ROOT / "config" / "manual_strategy_config.json", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "config" / "manual_strategy_config.json"),
        (MANUAL_ROOT / "references" / "GOAL_10X_OPERATING_PLAYBOOK.md", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "references" / "GOAL_10X_OPERATING_PLAYBOOK.md"),
        (MANUAL_ROOT / "references" / "OBJECTIVE_TO_MECHANISM_TRACEABILITY.md", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "references" / "OBJECTIVE_TO_MECHANISM_TRACEABILITY.md"),
        (MANUAL_ROOT / "references" / "GOAL_ORIENTED_DCA_POLICY.md", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "references" / "GOAL_ORIENTED_DCA_POLICY.md"),
        (MANUAL_ROOT / "references" / "LONG_TERM_PRICE_SCENARIO_POLICY.md", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "references" / "LONG_TERM_PRICE_SCENARIO_POLICY.md"),
        (MANUAL_ROOT / "references" / "PASSIVE_DISPATCH_RUNTIME_POLICY.md", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "references" / "PASSIVE_DISPATCH_RUNTIME_POLICY.md"),
        (MANUAL_ROOT / "references" / "MANUAL_DISPATCH_STRATEGY_CONTRACT.md", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "references" / "MANUAL_DISPATCH_STRATEGY_CONTRACT.md"),
        (MANUAL_ROOT / "scripts" / "build_daily_report_context.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "build_daily_report_context.py"),
        (MANUAL_ROOT / "scripts" / "asset_goal_contribution.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "asset_goal_contribution.py"),
        (MANUAL_ROOT / "scripts" / "manual_dispatch_run.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "manual_dispatch_run.py"),
        (MANUAL_ROOT / "scripts" / "learning_review_calendar.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "learning_review_calendar.py"),
        (MANUAL_ROOT / "scripts" / "next_dispatch_readiness.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "next_dispatch_readiness.py"),
        (MANUAL_ROOT / "scripts" / "next_goal_execution_queue.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "next_goal_execution_queue.py"),
        (MANUAL_ROOT / "scripts" / "current_goal_status_report.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "current_goal_status_report.py"),
        (MANUAL_ROOT / "scripts" / "next_manual_dispatch_execution_plan.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "next_manual_dispatch_execution_plan.py"),
        (MANUAL_ROOT / "scripts" / "progressive_learning_iteration_audit.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "progressive_learning_iteration_audit.py"),
        (MANUAL_ROOT / "scripts" / "goal_system_completion_audit.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "goal_system_completion_audit.py"),
        (MANUAL_ROOT / "scripts" / "strategy_iteration_backlog.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "strategy_iteration_backlog.py"),
        (MANUAL_ROOT / "scripts" / "recommendation_history.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "recommendation_history.py"),
        (MANUAL_ROOT / "scripts" / "recommendation_outcome_reviewer.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "recommendation_outcome_reviewer.py"),
        (MANUAL_ROOT / "scripts" / "recommendation_calibration_packager.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "recommendation_calibration_packager.py"),
        (MANUAL_ROOT / "scripts" / "generate_manual_report.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "generate_manual_report.py"),
        (MANUAL_ROOT / "scripts" / "report_integrity_audit.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "report_integrity_audit.py"),
        (MANUAL_ROOT / "scripts" / "objective_coverage_audit.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "objective_coverage_audit.py"),
        (MANUAL_ROOT / "scripts" / "research_evidence_backlog_builder.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "research_evidence_backlog_builder.py"),
        (MANUAL_ROOT / "scripts" / "research_subagent_task_packager.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "research_subagent_task_packager.py"),
        (MANUAL_ROOT / "scripts" / "research_subagent_output_collector.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "research_subagent_output_collector.py"),
        (MANUAL_ROOT / "scripts" / "portfolio_evidence_request_report.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "portfolio_evidence_request_report.py"),
        (MANUAL_ROOT / "scripts" / "audit_warning_explainer.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "audit_warning_explainer.py"),
        (MANUAL_ROOT / "scripts" / "goal_evidence_closure_packager.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "goal_evidence_closure_packager.py"),
        (MANUAL_ROOT / "scripts" / "goal_evidence_import_validator.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "goal_evidence_import_validator.py"),
        (MANUAL_ROOT / "scripts" / "goal_evidence_import_preview.py", QODER_SKILL_ROOT / "manual-investment-strategy-operator" / "scripts" / "goal_evidence_import_preview.py"),
        (ACTIVE_ROOT / "SKILL.md", QODER_SKILL_ROOT / "active-alpha-paper-monitor" / "SKILL.md"),
    ]
    sync_failures = [f"{src} -> {dst}" for src, dst in installed_pairs if not compare_file(src, dst)]
    check(
        checks,
        "installed_skill_sync",
        "Workspace skill changes are synced into .codex and .qoderwork installed skill directories.",
        "proven" if not sync_failures else "missing",
        "All sampled installed files match workspace versions." if not sync_failures else f"Out of sync: {sync_failures}",
    )

    paper_open = len((paper_ledger or {}).get("open_positions") or [])
    paper_closed = len((paper_ledger or {}).get("closed_trades") or [])
    check(
        checks,
        "active_paper_engine_running",
        "Active monitor has paper ledger and simulated positions/orders, with live orders disabled.",
        "proven" if file_exists(paper_ledger_path) and (paper_open or paper_closed) and (paper_ledger or {}).get("live_orders_enabled") is False else "missing",
        f"paper_ledger={paper_ledger_path}; open={paper_open}; closed_in_ledger={paper_closed}; live_orders_enabled={(paper_ledger or {}).get('live_orders_enabled')}",
        "warning" if paper_closed == 0 else "pass",
        "Closed-trade sample is still insufficient for tactical promotion." if paper_closed == 0 else None,
    )

    pass_count = sum(1 for item in checks if item["severity"] == "pass")
    warning_count = sum(1 for item in checks if item["severity"] == "warning")
    error_count = sum(1 for item in checks if item["severity"] == "error")
    max_allowed_current_action = (
        context.get("report_readiness", {}).get("max_allowed_action")
        or (dashboard.get("evidence_and_gate_state") or {}).get("report_max_allowed_action")
        or "unknown"
    )
    ready_for_crypto_dca_guidance = bool(crypto_plan.get("primary_pair") and crypto_plan.get("max_allowed_action") in {"conditional_action", "execute_now", "watch"})
    ready_for_auto_live_trading = False
    system_plan_implemented = error_count == 0
    ready_for_manual_reports = any(item["requirement_id"] == "latest_report_audits" and item["status"] == "proven" for item in checks)
    ready_for_tactical_execute_now = False
    ready_for_progressive_learning_loop = any(
        item["requirement_id"] == "progressive_learning_mechanism" and item["status"] == "proven"
        for item in checks
    )
    learning_calendar_ready = any(
        item["requirement_id"] == "learning_review_calendar" and item["status"] == "proven"
        for item in checks
    )
    operating_playbook_ready = any(
        item["requirement_id"] == "operating_playbook" and item["status"] == "proven"
        for item in checks
    )
    long_term_price_scenario_ready = any(
        item["requirement_id"] == "long_term_price_scenario_gate" and item["status"] == "proven"
        for item in checks
    )
    long_horizon_dca_timing_ready = any(
        item["requirement_id"] == "long_horizon_dca_timing_gate" and item["status"] == "proven"
        for item in checks
    )
    active_goal_execution_matrix_ready = any(
        item["requirement_id"] == "active_goal_execution_matrix" and item["status"] == "proven"
        for item in checks
    )
    investment_decision_validity_ready = any(
        item["requirement_id"] == "investment_decision_validity" and item["status"] == "proven"
        for item in checks
    )
    next_dispatch_readiness_ready = any(
        item["requirement_id"] == "next_dispatch_readiness" and item["status"] == "proven"
        for item in checks
    )
    next_goal_execution_queue_ready = any(
        item["requirement_id"] == "next_goal_execution_queue" and item["status"] == "proven"
        for item in checks
    )
    next_manual_dispatch_plan_ready = any(
        item["requirement_id"] == "next_manual_dispatch_execution_plan" and item["status"] == "proven"
        for item in checks
    )
    goal_mechanism_ready = bool(
        system_plan_implemented
        and ready_for_manual_reports
        and ready_for_crypto_dca_guidance
        and ready_for_progressive_learning_loop
        and learning_calendar_ready
        and operating_playbook_ready
        and long_term_price_scenario_ready
        and long_horizon_dca_timing_ready
        and active_goal_execution_matrix_ready
        and investment_decision_validity_ready
        and next_dispatch_readiness_ready
        and next_goal_execution_queue_ready
        and next_manual_dispatch_plan_ready
    )
    objective_financial_outcome_verified = False
    goal_complete = bool(objective_financial_outcome_verified and ready_for_tactical_execute_now and error_count == 0)

    backlog = dashboard.get("execution_backlog") or []
    next_required_work = [
        f"{item.get('priority')} {item.get('area')}: {item.get('task')}"
        for item in backlog
        if isinstance(item, dict)
    ]
    if not next_required_work:
        next_required_work = [
            "Run a fresh full market report with real external subagent outputs.",
            "Close enough paper trades and recommendation outcomes to calibrate tactical probabilities.",
            "Fill missing cost basis and US equity cash/buying-power evidence.",
        ]

    payload = {
        "generated_at": utc_now(),
        "workspace_root": str(ROOT),
        "inputs": {
            "context_json": str(context_path),
            "report_md": str(report_path),
            "dashboard_json": str(dashboard_path),
            "report_integrity_json": str(report_integrity_path),
            "objective_coverage_json": str(objective_coverage_path),
            "recommendation_ledger": str(recommendation_path),
            "portfolio_ledger": str(portfolio_ledger_path),
            "paper_ledger": str(paper_ledger_path),
            "learning_review_calendar_json": str(learning_calendar_path) if learning_calendar_path else "",
            "manual_dispatch_summary_json": str(manual_dispatch_summary_path) if manual_dispatch_summary_path else "",
            "next_dispatch_readiness_json": str(next_dispatch_readiness_path) if next_dispatch_readiness_path else "",
            "next_goal_execution_queue_json": str(next_goal_execution_queue_path) if next_goal_execution_queue_path else "",
            "next_manual_dispatch_execution_plan_json": str(next_manual_dispatch_plan_path) if next_manual_dispatch_plan_path else "",
            "portfolio_evidence_request_report_json": str(portfolio_evidence_request_path) if portfolio_evidence_request_path else "",
            "recommendation_calibration_package_json": str(recommendation_calibration_package_path) if recommendation_calibration_package_path else "",
        },
        "summary": {
            "system_plan_implemented": system_plan_implemented,
            "ready_for_manual_reports": ready_for_manual_reports,
            "ready_for_crypto_dca_guidance": ready_for_crypto_dca_guidance,
            "ready_for_tactical_execute_now": ready_for_tactical_execute_now,
            "ready_for_auto_live_trading": ready_for_auto_live_trading,
            "ready_for_progressive_learning_loop": ready_for_progressive_learning_loop,
            "learning_calendar_ready": learning_calendar_ready,
            "operating_playbook_ready": operating_playbook_ready,
            "long_term_price_scenario_ready": long_term_price_scenario_ready,
            "long_horizon_dca_timing_ready": long_horizon_dca_timing_ready,
            "active_goal_execution_matrix_ready": active_goal_execution_matrix_ready,
            "investment_decision_validity_ready": investment_decision_validity_ready,
            "next_dispatch_readiness_ready": next_dispatch_readiness_ready,
            "next_goal_execution_queue_ready": next_goal_execution_queue_ready,
            "next_manual_dispatch_plan_ready": next_manual_dispatch_plan_ready,
            "goal_mechanism_ready": goal_mechanism_ready,
            "objective_financial_outcome_verified": objective_financial_outcome_verified,
            "goal_complete": goal_complete,
            "max_allowed_current_action": max_allowed_current_action,
            "pass_count": pass_count,
            "warning_count": warning_count,
            "error_count": error_count,
            "next_required_work": next_required_work,
        },
        "checks": checks,
    }

    output_path = Path(args.output_json or args.output)
    markdown_path = Path(args.output_md or args.markdown_output)
    write_json(output_path, payload)
    write_text(markdown_path, render_markdown(payload))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
