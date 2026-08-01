#!/usr/bin/env python3
"""One-command manual dispatch runner for the investment strategy skill.

This is a passive orchestration entrypoint. It runs only when invoked by the
user or Codex, fetches/uses market evidence through generate_manual_report.py,
then optionally runs the system completion audit against the generated report.

It never places live orders, never transfers funds, and never mutates the
portfolio ledger. Recommendation-history writes are delegated to
generate_manual_report.py and can be disabled with --skip-recommendation-history-write
or --smoke.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
ACTIVE_ROOT = ROOT / "active-alpha-paper-monitor"
SCRIPT_DIR = MANUAL_ROOT / "scripts"
ACTIVE_SCRIPT_DIR = ACTIVE_ROOT / "scripts"
DEFAULT_OUTPUT_DIR = MANUAL_ROOT / "reports"
TMP_ROOT = Path("/private/tmp")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def local_date() -> str:
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return dt.datetime.now().strftime("%Y-%m-%d")


def compact_date(date_text: str) -> str:
    return date_text.replace("-", "")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def emit_progress(path: Path | None, event_type: str, **payload: Any) -> None:
    """Append a small, machine-readable, redacted progress event.

    The dashboard consumes this file while a manual run is active.  It is
    deliberately separate from stdout so the existing JSON summary contract
    stays unchanged.
    """
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"event_type": event_type, "occurred_at": utc_now(), **payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        handle.flush()


def load_json(path: Path | str | None) -> Any:
    if not path:
        return None
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def artifact_sort_key(path: Path) -> tuple[float, str]:
    return (path.stat().st_mtime, path.name)


def newest_experiment(pattern: str) -> Path | None:
    candidates = [path for path in (MANUAL_ROOT / "experiments").glob(pattern) if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=artifact_sort_key)


def run_command(
    command: list[str],
    *,
    timeout: int,
    env: dict[str, str],
    progress_path: Path | None = None,
    step_name: str = "",
) -> dict[str, Any]:
    started_at = utc_now()
    if step_name:
        emit_progress(progress_path, "step_started", step_id=step_name, status="running")
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        result = {
            "command": command,
            "status": "timeout",
            "returncode": None,
            "started_at": started_at,
            "finished_at": utc_now(),
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "error": f"timed out after {timeout}s",
        }
        if step_name:
            emit_progress(progress_path, "step_failed", step_id=step_name, status="failed", detail=result["error"])
        return result
    result = {
        "command": command,
        "status": "ok" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "started_at": started_at,
        "finished_at": utc_now(),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    if step_name:
        emit_progress(
            progress_path,
            "step_completed" if result["status"] == "ok" else "step_failed",
            step_id=step_name,
            status="success" if result["status"] == "ok" else "failed",
        )
    return result


def parse_json_stdout(step: dict[str, Any]) -> dict[str, Any]:
    stdout = step.get("stdout") or ""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def audit_status_is_failed(report_summary: dict[str, Any], key: str) -> bool:
    audit = report_summary.get(key) or {}
    status = audit.get("status")
    if status in (None, "skipped"):
        return False
    return status != "ok" or bool(audit.get("failed"))


def build_operating_playbook_summary(system_summary: dict[str, Any], report_summary: dict[str, Any]) -> dict[str, Any]:
    playbook_path = MANUAL_ROOT / "references" / "GOAL_10X_OPERATING_PLAYBOOK.md"
    playbook_text = read_text(playbook_path)
    max_action = report_summary.get("max_allowed_action") or "unknown"
    return {
        "path": str(playbook_path),
        "exists": playbook_path.exists(),
        "ready": bool(
            system_summary.get("operating_playbook_ready")
            or (
                playbook_path.exists()
                and "Timebox Rule" in playbook_text
                and "Crypto DCA Rule" in playbook_text
                and "US Equity Tactical Rule" in playbook_text
                and "Action Level Ladder" in playbook_text
                and "Learning Loop" in playbook_text
            )
        ),
        "manual_dispatch_only": True,
        "live_orders_enabled": False,
        "auto_transfers_enabled": False,
        "normal_dispatch_should_timebox": "yes; if data/subagents are incomplete, close out with a downgraded readable report",
        "smoke_result_valid_for_investment_decision": False,
        "reading_order": [
            "Investment Decision Validity",
            "One Page Conclusion",
            "Portfolio / Goal Gap",
            "Crypto DCA Direction",
            "US Tactical Relay",
            "Risk / Downgrade",
            "Learning Review Calendar",
        ],
        "action_level_ladder": [
            "no_action",
            "watch",
            "paper_only",
            "conditional_action",
            "execute_now_candidate",
            "execute_now",
        ],
        "current_max_action": max_action,
        "current_max_action_plain_note": (
            "只能模拟/观察，不能真实下单"
            if max_action in {"paper_only", "watch"}
            else "只能在触发条件和人工确认后考虑"
            if max_action == "conditional_action"
            else "需要继续看报告风险门"
        ),
    }


def current_tactical_symbol_resolution_from_report(report_summary: dict[str, Any]) -> dict[str, Any]:
    for step in report_summary.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("step") == "current_tactical_symbol_resolution":
            return {
                "status": step.get("status") or "unknown",
                "symbol": step.get("symbol") or "",
                "source": step.get("source") or "",
                "candidates": step.get("candidates") or [],
            }
    return {
        "status": "missing",
        "symbol": "",
        "source": "report_summary.steps.current_tactical_symbol_resolution_not_found",
        "candidates": [],
    }


def format_tactical_candidates(candidates: list[Any]) -> str:
    formatted: list[str] = []
    for item in candidates[:5]:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol") or "unknown"
        value = item.get("market_value_usd")
        if value is None:
            value = item.get("current_value_usd")
        if isinstance(value, (int, float)):
            formatted.append(f"{symbol} ${value:,.2f}")
        else:
            formatted.append(str(symbol))
    return ", ".join(formatted)


def build_investment_decision_status(
    *,
    args: argparse.Namespace,
    status: str,
    report_summary: dict[str, Any],
    report_audit_failed: bool,
    objective_audit_failed: bool,
    system_summary: dict[str, Any],
    progressive_payload: dict[str, Any],
    learning_calendar_payload: dict[str, Any],
    validation_pulse_payload: dict[str, Any],
    next_readiness_payload: dict[str, Any],
) -> dict[str, Any]:
    report_integrity = report_summary.get("report_integrity_audit") or {}
    objective_coverage = report_summary.get("objective_coverage_audit") or {}
    recommendation_write = report_summary.get("recommendation_history_write") or {}
    reasons: list[str] = []
    if args.smoke:
        reasons.append("smoke_mode_enabled")
    if status != "ok":
        reasons.append(f"dispatch_status_{status}")
    if args.skip_report_audits or report_integrity.get("status") != "ok" or objective_coverage.get("status") != "ok":
        reasons.append("report_audits_not_all_ok")
    if report_audit_failed or objective_audit_failed:
        reasons.append("report_or_objective_audit_failed")
    if args.skip_recommendation_history_write or recommendation_write.get("status") != "ok":
        reasons.append("recommendation_history_not_written")
    if args.skip_system_audit or not system_summary:
        reasons.append("system_audit_not_run")
    if args.skip_validation_pulse or not validation_pulse_payload:
        reasons.append("paper_validation_pulse_not_run")
    if args.skip_learning_review_calendar or not learning_calendar_payload:
        reasons.append("learning_review_calendar_not_run")
    if not progressive_payload:
        reasons.append("progressive_learning_audit_not_run")
    if not args.smoke and not next_readiness_payload:
        reasons.append("next_dispatch_readiness_not_run")

    formal_valid = not reasons
    execute_now_allowed = bool(report_summary.get("execute_now_allowed"))
    return {
        "formal_investment_report_valid": formal_valid,
        "execute_now_valid": bool(formal_valid and execute_now_allowed),
        "valid_for_watch_or_conditional_review": bool(report_summary.get("report_path") and status == "ok"),
        "invalid_reasons": reasons,
        "plain_note": (
            "可作为正式人工投资判断入口，但仍需按动作等级和人工确认执行。"
            if formal_valid
            else "不能作为正式投资判断，只能用于结构测试、降级阅读或排查。"
        ),
    }


def compact_step_output(step: dict[str, Any], max_chars: int = 1200) -> dict[str, Any]:
    compacted = dict(step)
    for key in ["stdout", "stderr"]:
        text = str(compacted.get(key) or "")
        if len(text) > max_chars:
            compacted[f"{key}_truncated"] = True
            compacted[f"{key}_excerpt"] = text[:max_chars] + "..."
            compacted.pop(key, None)
        else:
            compacted[f"{key}_truncated"] = False
    return compacted


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return ""
        return str(value).replace("\n", " ").replace("|", "\\|")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(cell(part) for part in row) + " |" for row in rows)
    return "\n".join(lines)


def build_report_command(args: argparse.Namespace, run_id: str) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "generate_manual_report.py"),
        "--date",
        args.date,
        "--run-id",
        run_id,
        "--monthly-dca",
        str(args.monthly_dca),
        "--crypto-symbols",
        args.crypto_symbols,
        "--output-dir",
        args.output_dir,
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    if args.current_tactical_symbol:
        command.extend(["--current-tactical-symbol", args.current_tactical_symbol])
    if args.env_file:
        command.extend(["--env-file", args.env_file])
    if args.external_agent_outputs_json:
        command.extend(["--external-agent-outputs-json", args.external_agent_outputs_json])
    if args.require_external_subagents:
        command.append("--require-external-subagents")
    if args.use_existing_inputs or args.smoke:
        command.append("--use-existing-inputs")
    if args.skip_active_scanners:
        command.append("--skip-active-scanners")
    require_fresh = (
        (args.require_fresh_market_intelligence or not args.allow_degraded_market_intelligence)
        and not args.smoke
    )
    if require_fresh:
        command.append("--require-fresh-market-intelligence")
    if args.skip_recommendation_history_write or args.smoke:
        command.append("--skip-recommendation-history-write")
    if args.fetch_outcome_review_market_data:
        command.append("--fetch-outcome-review-market-data")
    if args.skip_report_audits or args.smoke:
        command.extend(["--skip-report-integrity-audit", "--skip-objective-coverage-audit"])
    return command


def build_system_audit_command(args: argparse.Namespace, report_summary: dict[str, Any], run_id: str) -> list[str]:
    report_path = report_summary.get("report_path")
    context_path = report_summary.get("daily_context_json")
    dashboard_path = (report_summary.get("goal_execution_dashboard") or {}).get("dashboard_json")
    integrity_path = (report_summary.get("report_integrity_audit") or {}).get("audit_path")
    objective_path = (report_summary.get("objective_coverage_audit") or {}).get("audit_path")
    output_json = MANUAL_ROOT / "experiments" / f"goal_system_completion_audit_{run_id}.json"
    output_md = MANUAL_ROOT / "reports" / f"goal_system_completion_audit_{run_id}.md"
    command = [
        sys.executable,
        str(SCRIPT_DIR / "goal_system_completion_audit.py"),
        "--context-json",
        str(context_path or ""),
        "--report-md",
        str(report_path or ""),
        "--dashboard-json",
        str(dashboard_path or ""),
        "--report-integrity-json",
        str(integrity_path or ""),
        "--objective-coverage-json",
        str(objective_path or ""),
        "--output-json",
        str(output_json),
        "--output-md",
        str(output_md),
    ]
    return command


def build_progressive_learning_audit_command(report_summary: dict[str, Any], run_id: str) -> list[str]:
    dashboard_path = (report_summary.get("goal_execution_dashboard") or {}).get("dashboard_json")
    output_json = MANUAL_ROOT / "experiments" / f"progressive_learning_iteration_audit_{run_id}.json"
    output_md = MANUAL_ROOT / "reports" / f"progressive_learning_iteration_audit_{run_id}.md"
    return [
        sys.executable,
        "-B",
        str(SCRIPT_DIR / "progressive_learning_iteration_audit.py"),
        "--dashboard-json",
        str(dashboard_path or ""),
        "--output",
        str(output_json),
        "--markdown-output",
        str(output_md),
    ]


def build_timeboxed_closeout_audit_command(args: argparse.Namespace, run_id: str) -> list[str]:
    output_json = MANUAL_ROOT / "experiments" / f"timeboxed_committee_closeout_audit_{run_id}.json"
    output_md = MANUAL_ROOT / "reports" / f"timeboxed_committee_closeout_audit_{run_id}.md"
    return [
        sys.executable,
        "-B",
        str(SCRIPT_DIR / "timeboxed_committee_closeout_audit.py"),
        "--task-package-json",
        args.timeboxed_task_package_json,
        "--collection-json",
        args.timeboxed_collection_json,
        "--quality-audit-json",
        args.timeboxed_quality_audit_json,
        "--output",
        str(output_json),
        "--markdown-output",
        str(output_md),
    ]


def build_validation_pulse_command(args: argparse.Namespace) -> list[str]:
    """Run the due-only paper learning guard.

    The guard first checks whether a paper review is actually due. If nothing
    is due, it exits quickly with a status report instead of repeatedly running
    the active validation runner.
    """

    return [
        sys.executable,
        "-B",
        str(SCRIPT_DIR / "due_learning_review_pulse.py"),
        "--timeout-seconds",
        str(args.validation_pulse_timeout_seconds),
        "--format",
        "json",
    ]


def build_learning_review_calendar_command(args: argparse.Namespace, run_id: str) -> list[str]:
    output_json = MANUAL_ROOT / "experiments" / f"learning_review_calendar_{run_id}.json"
    output_md = MANUAL_ROOT / "reports" / f"learning_review_calendar_{run_id}.md"
    return [
        sys.executable,
        "-B",
        str(SCRIPT_DIR / "learning_review_calendar.py"),
        "--max-items",
        str(args.learning_review_calendar_max_items),
        "--output",
        str(output_json),
        "--markdown-output",
        str(output_md),
        "--format",
        "json",
    ]


def build_next_dispatch_readiness_command(run_id: str) -> list[str]:
    output_json = MANUAL_ROOT / "experiments" / f"next_dispatch_readiness_{run_id}.json"
    output_md = MANUAL_ROOT / "reports" / f"next_dispatch_readiness_{run_id}.md"
    return [
        sys.executable,
        "-B",
        str(SCRIPT_DIR / "next_dispatch_readiness.py"),
        "--completion-audit-json",
        str(MANUAL_ROOT / "experiments" / f"goal_system_completion_audit_{run_id}.json"),
        "--learning-calendar-json",
        str(MANUAL_ROOT / "experiments" / f"learning_review_calendar_{run_id}.json"),
        "--closure-package-json",
        str(MANUAL_ROOT / "experiments" / f"goal_evidence_closure_package_{run_id}.json"),
        "--output-json",
        str(output_json),
        "--output-md",
        str(output_md),
        "--format",
        "json",
    ]


def build_goal_evidence_closure_package_command(run_id: str) -> list[str]:
    output_json = MANUAL_ROOT / "experiments" / f"goal_evidence_closure_package_{run_id}.json"
    output_md = MANUAL_ROOT / "reports" / f"goal_evidence_closure_package_{run_id}.md"
    return [
        sys.executable,
        "-B",
        str(SCRIPT_DIR / "goal_evidence_closure_packager.py"),
        "--audit-json",
        str(MANUAL_ROOT / "experiments" / f"goal_system_completion_audit_{run_id}.json"),
        "--output-json",
        str(output_json),
        "--output-md",
        str(output_md),
        "--format",
        "json",
    ]


def build_next_goal_execution_queue_command(run_id: str) -> list[str]:
    output_json = MANUAL_ROOT / "experiments" / f"next_goal_execution_queue_{run_id}.json"
    output_md = MANUAL_ROOT / "reports" / f"next_goal_execution_queue_{run_id}.md"
    return [
        sys.executable,
        "-B",
        str(SCRIPT_DIR / "next_goal_execution_queue.py"),
        "--readiness-json",
        str(MANUAL_ROOT / "experiments" / f"next_dispatch_readiness_{run_id}.json"),
        "--learning-calendar-json",
        str(MANUAL_ROOT / "experiments" / f"learning_review_calendar_{run_id}.json"),
        "--output-json",
        str(output_json),
        "--output-md",
        str(output_md),
        "--format",
        "json",
    ]


def build_next_manual_dispatch_execution_plan_command(run_id: str) -> list[str]:
    output_json = MANUAL_ROOT / "experiments" / f"next_manual_dispatch_execution_plan_{run_id}.json"
    output_md = MANUAL_ROOT / "reports" / f"next_manual_dispatch_execution_plan_{run_id}.md"
    return [
        sys.executable,
        "-B",
        str(SCRIPT_DIR / "next_manual_dispatch_execution_plan.py"),
        "--completion-audit-json",
        str(MANUAL_ROOT / "experiments" / f"goal_system_completion_audit_{run_id}.json"),
        "--readiness-json",
        str(MANUAL_ROOT / "experiments" / f"next_dispatch_readiness_{run_id}.json"),
        "--queue-json",
        str(MANUAL_ROOT / "experiments" / f"next_goal_execution_queue_{run_id}.json"),
        "--output-json",
        str(output_json),
        "--output-md",
        str(output_md),
        "--format",
        "json",
    ]


def build_schema_baseline_command(run_id: str) -> list[str]:
    output_json = MANUAL_ROOT / "experiments" / f"schema_baseline_audit_{run_id}.json"
    output_md = MANUAL_ROOT / "reports" / f"schema_baseline_audit_{run_id}.md"
    return [
        sys.executable,
        "-B",
        str(SCRIPT_DIR / "schema_baseline_auditor.py"),
        "--root",
        str(ROOT),
        "--output",
        str(output_json),
        "--markdown-output",
        str(output_md),
    ]


def build_evidence_blocker_classifier_command(args: argparse.Namespace, run_id: str) -> list[str] | None:
    backlog_path = Path(args.evidence_backlog_json) if args.evidence_backlog_json else None
    if not backlog_path:
        backlog_path = newest_experiment("research_evidence_backlog_*.json")
    if not backlog_path or not backlog_path.exists():
        return None
    quality_path = Path(args.evidence_quality_audit_json) if args.evidence_quality_audit_json else None
    if not quality_path and args.timeboxed_quality_audit_json:
        quality_path = Path(args.timeboxed_quality_audit_json)
    if not quality_path:
        quality_path = newest_experiment("*committee-quality.json") or newest_experiment("research_committee_quality_audit_*.json")
    output_json = MANUAL_ROOT / "experiments" / f"evidence_blocker_classification_{run_id}.json"
    output_md = MANUAL_ROOT / "reports" / f"evidence_blocker_classification_{run_id}.md"
    command = [
        sys.executable,
        "-B",
        str(SCRIPT_DIR / "evidence_blocker_classifier.py"),
        "--backlog-json",
        str(backlog_path),
        "--run-id",
        f"{run_id}-evidence-blocker-classification",
        "--output-json",
        str(output_json),
        "--output-md",
        str(output_md),
        "--format",
        "json",
    ]
    if quality_path and quality_path.exists():
        command.extend(["--quality-json", str(quality_path)])
    return command


def render_markdown(payload: dict[str, Any]) -> str:
    report = payload.get("report_summary") or {}
    system = payload.get("system_audit_summary") or {}
    learning = payload.get("progressive_learning_audit_summary") or {}
    closeout = payload.get("timeboxed_closeout_audit_summary") or {}
    closeout_collection = closeout.get("committee_collection") or {}
    validation_pulse = payload.get("validation_pulse_summary") or {}
    validation_guard_summary = validation_pulse.get("summary") or {}
    validation_delta = validation_pulse.get("progress_delta") or {}
    open_review = validation_pulse.get("open_position_exit_summary") or {}
    learning_calendar = payload.get("learning_review_calendar_summary") or {}
    learning_calendar_summary = learning_calendar.get("summary") or {}
    learning_calendar_paper = learning_calendar.get("paper_review") or {}
    learning_calendar_rec = learning_calendar.get("recommendation_review") or {}
    learning_calendar_early = learning_calendar_summary.get("early_calibration_needed") or {}
    learning_calendar_usable = learning_calendar_summary.get("usable_calibration_needed") or {}
    next_readiness = payload.get("next_dispatch_readiness_summary") or {}
    next_readiness_summary = next_readiness.get("summary") or {}
    next_queue = payload.get("next_goal_execution_queue_summary") or {}
    next_queue_summary = next_queue.get("summary") or {}
    next_plan = payload.get("next_manual_dispatch_execution_plan_summary") or {}
    next_plan_summary = next_plan.get("summary") or {}
    evidence_blockers = payload.get("evidence_blocker_classification_summary") or {}
    evidence_blocker_summary = evidence_blockers.get("summary") or {}
    schema = payload.get("schema_baseline_summary") or {}
    acceptance = payload.get("iteration_acceptance") or {}
    operating = payload.get("operating_playbook_summary") or {}
    decision = payload.get("investment_decision_status") or {}
    paper_learning = learning.get("paper_validation") or {}
    recommendation_learning = learning.get("recommendation_learning") or {}
    research_learning = learning.get("research_committee_learning") or {}
    report_audit = report.get("report_integrity_audit") or {}
    objective_audit = report.get("objective_coverage_audit") or {}
    tactical_resolution = payload.get("current_tactical_symbol_resolution") or {}
    tactical_candidates = tactical_resolution.get("candidates") or []
    return "\n".join([
        "# Manual Dispatch Run Summary",
        "",
        "本摘要来自一次手动调度。它不会下单、不会转账、不会自动改持仓账本。",
        "",
        "## Summary",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["run_id", payload.get("run_id")],
                ["status", payload.get("status")],
                ["formal_investment_report_valid", decision.get("formal_investment_report_valid")],
                ["execute_now_valid", decision.get("execute_now_valid")],
                ["investment_decision_note", decision.get("plain_note")],
                ["current_tactical_symbol", payload.get("current_tactical_symbol")],
                ["current_tactical_symbol_source", payload.get("current_tactical_symbol_source")],
                ["current_tactical_symbol_resolution_status", tactical_resolution.get("status")],
                ["current_tactical_candidates", format_tactical_candidates(tactical_candidates)],
                ["smoke_mode", payload.get("smoke_mode")],
                ["manual_dispatch_only", True],
                ["default_requires_fresh_market_intelligence", payload.get("default_requires_fresh_market_intelligence")],
                ["allow_degraded_market_intelligence", payload.get("allow_degraded_market_intelligence")],
                ["live_orders_enabled", False],
                ["schema_baseline_status", schema.get("status")],
                ["sql_files_found", schema.get("sql_file_count")],
                ["schema_max_allowed_action", schema.get("max_allowed_action_from_schema_baseline")],
                ["report_path", report.get("report_path")],
                ["context_json", report.get("daily_context_json")],
                ["research_panel_json", report.get("research_panel_json")],
                ["max_allowed_action", report.get("max_allowed_action")],
                ["execute_now_allowed", report.get("execute_now_allowed")],
                ["research_committee_degraded", report.get("research_committee_degraded")],
                ["report_integrity", f"{report_audit.get('status')} failed={report_audit.get('failed')}"],
                ["objective_coverage", f"{objective_audit.get('status')} failed={objective_audit.get('failed')}"],
                ["system_goal_complete", system.get("goal_complete")],
                ["system_ready_for_auto_live_trading", system.get("ready_for_auto_live_trading")],
                ["goal_mechanism_ready", acceptance.get("goal_mechanism_ready")],
                ["ready_for_progressive_learning_loop", acceptance.get("ready_for_progressive_learning_loop")],
                ["iteration_acceptance_ready", acceptance.get("ready")],
                ["operating_playbook_ready", operating.get("ready")],
                ["operating_current_max_action", operating.get("current_max_action")],
                ["progressive_learning_audit", payload.get("progressive_learning_audit_md")],
                ["learning_stage", learning.get("learning_stage")],
                ["learning_progress_score", learning.get("learning_progress_score_points")],
                ["learning_max_allowed_action", learning.get("max_allowed_action")],
                ["validation_pulse_status", validation_pulse.get("status")],
                ["validation_pulse_closed_delta", validation_delta.get("closed_count_delta")],
                ["validation_pulse_nearest_review", open_review.get("nearest_expiry_at")],
                ["learning_review_calendar_status", learning_calendar.get("status")],
                ["learning_review_next_paper", learning_calendar_summary.get("next_paper_review_at")],
                ["learning_review_next_recommendation", learning_calendar_summary.get("next_recommendation_review_at")],
                ["learning_review_max_action", learning_calendar_summary.get("max_allowed_action_from_learning_calendar")],
                ["next_dispatch_ready", next_readiness_summary.get("ready_for_next_manual_dispatch")],
                ["next_dispatch_focus", ", ".join(next_readiness_summary.get("immediate_focus") or [])],
                ["next_dispatch_max_action", next_readiness_summary.get("max_allowed_current_action")],
                ["next_dispatch_report", payload.get("next_dispatch_readiness_md")],
                ["next_goal_queue_tasks", next_queue_summary.get("task_count")],
                ["next_goal_queue_p0_open", next_queue_summary.get("p0_open_count")],
                ["next_goal_queue_report", payload.get("next_goal_execution_queue_md")],
                ["next_manual_dispatch_plan_status", next_plan.get("status")],
                ["next_manual_dispatch_plan_report", payload.get("next_manual_dispatch_execution_plan_md")],
                ["timeboxed_closeout_status", closeout.get("status")],
                ["timeboxed_closeout_can_end", closeout.get("ordinary_manual_dispatch_can_end")],
                ["timeboxed_closeout_execute_now", closeout.get("execute_now_allowed")],
                ["evidence_blocker_next_mode", evidence_blocker_summary.get("recommended_next_mode")],
                ["evidence_blocker_stop_open_loop", evidence_blocker_summary.get("should_stop_open_loop_research")],
                ["evidence_blocker_report", payload.get("evidence_blocker_classification_md")],
            ],
        ),
        "",
        "当前战术仓说明：`current_tactical_symbol` 是本轮可作为美股短线资金来源或比较基准的持仓识别结果，不是固定写死的标的；如果以后战术仓从 SOXL 变成 APLD、COIN 或其他票，这里会跟着持仓快照动态变化。",
        "",
        "## Iteration Acceptance",
        "",
        markdown_table(
            ["Field", "Meaning"],
            [
                ["acceptance_target", acceptance.get("acceptance_target")],
                ["not_required_this_iteration", acceptance.get("not_required_this_iteration")],
                ["learning_path", acceptance.get("learning_path")],
            ],
        ),
        "",
        "本轮验收重点是机制能否在每次手动调度中取数、记录、复盘、归因并生成下一轮补证据任务；不是证明这一次已经完成 5年/10年 10x 或稳定 80% 命中。",
        "",
        "## Investment Decision Validity",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["formal_investment_report_valid", decision.get("formal_investment_report_valid")],
                ["execute_now_valid", decision.get("execute_now_valid")],
                ["valid_for_watch_or_conditional_review", decision.get("valid_for_watch_or_conditional_review")],
                ["plain_note", decision.get("plain_note")],
                ["invalid_reasons", ", ".join(decision.get("invalid_reasons") or [])],
            ],
        ),
        "",
        "这个面板专门防止把 `status=ok`、`smoke` 或跳过审计的结果误读成正式投资建议。正式报告也只是人工判断入口，不是自动下单。",
        "",
        "## Operating Playbook Quick Read",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["playbook", operating.get("path")],
                ["ready", operating.get("ready")],
                ["manual_dispatch_only", operating.get("manual_dispatch_only")],
                ["live_orders_enabled", operating.get("live_orders_enabled")],
                ["auto_transfers_enabled", operating.get("auto_transfers_enabled")],
                ["timebox_rule", operating.get("normal_dispatch_should_timebox")],
                ["smoke_valid_for_investment_decision", operating.get("smoke_result_valid_for_investment_decision")],
                ["current_max_action", operating.get("current_max_action")],
                ["plain_note", operating.get("current_max_action_plain_note")],
            ],
        ),
        "",
        "**跑完报告先看这 6 个位置：**",
        "",
        "\n".join(f"{idx}. {item}" for idx, item in enumerate(operating.get("reading_order") or [], start=1)),
        "",
        "**动作等级从低到高：** " + " -> ".join(operating.get("action_level_ladder") or []),
        "",
        "这个面板来自日常操作手册，目的是让普通调度跑完后立刻能判断“能不能用、该看哪里、为什么不能强执行”。",
        "",
        "## Schema Baseline",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["status", schema.get("status")],
                ["sql_file_count", schema.get("sql_file_count")],
                ["sql_interpretation", schema.get("sql_interpretation")],
                ["max_allowed_action", schema.get("max_allowed_action_from_schema_baseline")],
            ],
        ),
        "",
        "## Progressive Learning",
        "",
        markdown_table(
            ["Evidence", "Current", "Target", "Needed"],
            [
                ["closed paper trades", paper_learning.get("closed_count"), paper_learning.get("closed_target"), paper_learning.get("closed_needed")],
                ["recommendation resolved", recommendation_learning.get("resolved"), recommendation_learning.get("resolved_target"), recommendation_learning.get("resolved_needed")],
                ["evidence-verified roles", research_learning.get("evidence_verified_roles"), research_learning.get("target_evidence_verified_roles"), research_learning.get("additional_evidence_verified_roles_needed")],
            ],
        ),
        "",
        "## Paper Validation Pulse",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["status", f"`{validation_pulse.get('status')}`" if validation_pulse else "`not_run`"],
                ["guard_action", f"`{validation_guard_summary.get('action')}`" if validation_guard_summary else "`n/a`"],
                ["paper_pulse_ran", f"`{validation_guard_summary.get('paper_pulse_ran')}`" if validation_guard_summary else "`n/a`"],
                ["paper_due_now", f"`{validation_guard_summary.get('paper_due_now_count')}`" if validation_guard_summary else "`n/a`"],
                ["paper_expiring_24h", f"`{validation_guard_summary.get('paper_expiring_24h_count')}`" if validation_guard_summary else "`n/a`"],
                ["closed_count_delta", f"`{validation_delta.get('closed_count_delta')}`" if validation_delta else "`n/a`"],
                ["open_count_delta", f"`{validation_delta.get('open_count_delta')}`" if validation_delta else "`n/a`"],
                ["closed_paper_trades_needed_delta", f"`{validation_delta.get('closed_paper_trades_needed_delta')}`" if validation_delta else "`n/a`"],
                ["open_positions", f"`{open_review.get('open_count')}`" if open_review else "`n/a`"],
                ["nearest_paper_review", f"`{open_review.get('nearest_expiry_at')}`" if open_review else "`n/a`"],
                ["output_report", (validation_pulse.get("outputs") or {}).get("report") if validation_pulse else ""],
            ],
        ),
        "",
        "这个面板只检查纸面仓位是否能转成学习样本。它不会真实下单，也不会改变真实持仓；没有到期样本时，结果为 0 是正常的。",
        "",
        "## Learning Review Calendar",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["status", f"`{learning_calendar.get('status')}`" if learning_calendar else "`not_run`"],
                ["next_paper_review_at", f"`{learning_calendar_summary.get('next_paper_review_at')}`" if learning_calendar_summary else "`n/a`"],
                ["next_recommendation_review_at", f"`{learning_calendar_summary.get('next_recommendation_review_at')}`" if learning_calendar_summary else "`n/a`"],
                ["next_action_hint", f"`{learning_calendar_summary.get('next_action_hint')}`" if learning_calendar_summary else "`n/a`"],
                ["paper_early_needed", f"`{learning_calendar_early.get('paper')}`" if learning_calendar_early else "`n/a`"],
                ["recommendation_early_needed", f"`{learning_calendar_early.get('recommendation')}`" if learning_calendar_early else "`n/a`"],
                ["paper_usable_needed", f"`{learning_calendar_usable.get('paper')}`" if learning_calendar_usable else "`n/a`"],
                ["recommendation_usable_needed", f"`{learning_calendar_usable.get('recommendation')}`" if learning_calendar_usable else "`n/a`"],
                ["paper_due_now", f"`{learning_calendar_paper.get('due_now_count')}`" if learning_calendar_paper else "`n/a`"],
                ["paper_expiring_24h", f"`{learning_calendar_paper.get('expiring_24h_count')}`" if learning_calendar_paper else "`n/a`"],
                ["max_allowed_action", f"`{learning_calendar_summary.get('max_allowed_action_from_learning_calendar')}`" if learning_calendar_summary else "`n/a`"],
                ["output_report", payload.get("learning_review_calendar_md")],
            ],
        ),
        "",
        "这个面板把纸面仓位到期复盘和历史建议到期复盘合到一个日历里。它的作用是告诉下一次该复盘什么、还缺多少样本，不授权真实交易。",
        "",
        "## Next Dispatch Readiness",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["status", f"`{next_readiness.get('status')}`" if next_readiness else "`not_run`"],
                ["ready_for_next_manual_dispatch", f"`{next_readiness_summary.get('ready_for_next_manual_dispatch')}`" if next_readiness_summary else "`n/a`"],
                ["goal_mechanism_ready", f"`{next_readiness_summary.get('goal_mechanism_ready')}`" if next_readiness_summary else "`n/a`"],
                ["goal_complete", f"`{next_readiness_summary.get('goal_complete')}`" if next_readiness_summary else "`n/a`"],
                ["ready_for_tactical_execute_now", f"`{next_readiness_summary.get('ready_for_tactical_execute_now')}`" if next_readiness_summary else "`n/a`"],
                ["max_allowed_current_action", f"`{next_readiness_summary.get('max_allowed_current_action')}`" if next_readiness_summary else "`n/a`"],
                ["immediate_focus", ", ".join(next_readiness_summary.get("immediate_focus") or []) if next_readiness_summary else ""],
                ["next_paper_review_at", f"`{next_readiness_summary.get('next_paper_review_at')}`" if next_readiness_summary else "`n/a`"],
                ["next_recommendation_review_at", f"`{next_readiness_summary.get('next_recommendation_review_at')}`" if next_readiness_summary else "`n/a`"],
                ["paper_closed_needed", f"`{next_readiness_summary.get('paper_closed_needed')}`" if next_readiness_summary else "`n/a`"],
                ["recommendation_early_resolved_needed", f"`{next_readiness_summary.get('recommendation_early_resolved_needed')}`" if next_readiness_summary else "`n/a`"],
                ["portfolio_blocking_gap_count", f"`{next_readiness_summary.get('portfolio_blocking_gap_count')}`" if next_readiness_summary else "`n/a`"],
                ["output_report", payload.get("next_dispatch_readiness_md")],
            ],
        ),
        "",
        "这个面板是下一次调度前检查。它只读，不抓新行情、不改账本、不授权交易；它告诉下一步先复盘什么、为什么还不能强执行。",
        "",
        "## Next Goal Execution Queue",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["status", f"`{next_queue.get('status')}`" if next_queue else "`not_run`"],
                ["task_count", f"`{next_queue_summary.get('task_count')}`" if next_queue_summary else "`n/a`"],
                ["p0_open_count", f"`{next_queue_summary.get('p0_open_count')}`" if next_queue_summary else "`n/a`"],
                ["execute_now_blocking_task_ids", ", ".join(next_queue_summary.get("execute_now_blocking_task_ids") or []) if next_queue_summary else ""],
                ["output_report", payload.get("next_goal_execution_queue_md")],
            ],
        ),
        "",
        "这个面板把 readiness、学习日历和证据缺口压缩成下一步工作队列。它不是买卖清单，只说明下一次调度前后该优先处理哪些事项。",
        "",
        "## Next Manual Dispatch Execution Plan",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["status", f"`{next_plan.get('status')}`" if next_plan else "`not_run`"],
                ["goal_mechanism_ready", f"`{next_plan_summary.get('goal_mechanism_ready')}`" if next_plan_summary else "`n/a`"],
                ["goal_complete", f"`{next_plan_summary.get('goal_complete')}`" if next_plan_summary else "`n/a`"],
                ["ready_for_next_manual_dispatch", f"`{next_plan_summary.get('ready_for_next_manual_dispatch')}`" if next_plan_summary else "`n/a`"],
                ["ready_for_tactical_execute_now", f"`{next_plan_summary.get('ready_for_tactical_execute_now')}`" if next_plan_summary else "`n/a`"],
                ["max_allowed_current_action", f"`{next_plan_summary.get('max_allowed_current_action')}`" if next_plan_summary else "`n/a`"],
                ["recommended_current_turn_action", f"`{next_plan_summary.get('recommended_current_turn_action')}`" if next_plan_summary else "`n/a`"],
                ["next_paper_review_at_shanghai", f"`{next_plan_summary.get('next_paper_review_at_shanghai')}`" if next_plan_summary else "`n/a`"],
                ["next_recommendation_review_at_shanghai", f"`{next_plan_summary.get('next_recommendation_review_at_shanghai')}`" if next_plan_summary else "`n/a`"],
                ["output_report", payload.get("next_manual_dispatch_execution_plan_md")],
            ],
        ),
        "",
        "这个面板把下一次正式调度怎么跑、何时复盘、何时停止压成一页计划。它不是买卖清单，不授权真实交易。",
        "",
        "## Timeboxed Committee Closeout",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["status", f"`{closeout.get('status')}`" if closeout else "`not_run`"],
                ["ordinary_manual_dispatch_can_end", f"`{closeout.get('ordinary_manual_dispatch_can_end')}`" if closeout else "`n/a`"],
                ["execute_now_allowed", f"`{closeout.get('execute_now_allowed')}`" if closeout else "`n/a`"],
                ["expected/collected/missing", (
                    f"`{closeout_collection.get('expected_task_count')}/"
                    f"{closeout_collection.get('collected_agent_count')}/"
                    f"{closeout_collection.get('missing_agent_count')}`"
                    if closeout_collection else "`n/a`"
                )],
                ["missing_agents", ", ".join(closeout_collection.get("missing_agents") or []) if closeout_collection else ""],
            ],
        ),
        "",
        "这个面板回答的是“本次研究委员会是否可以到点收口”，不是“研究委员会是否已经通过”。部分收口永远不能打开 `execute_now`。",
        "",
        "## Evidence Blocker Classification",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["status", f"`{evidence_blockers.get('status')}`" if evidence_blockers else "`not_run`"],
                ["source_item_count", f"`{evidence_blocker_summary.get('source_item_count')}`" if evidence_blocker_summary else "`n/a`"],
                ["retryable_public_count", f"`{evidence_blocker_summary.get('retryable_public_count')}`" if evidence_blocker_summary else "`n/a`"],
                ["paid_or_api_needed", f"`{evidence_blocker_summary.get('paid_or_entitled_api_needed_count')}`" if evidence_blocker_summary else "`n/a`"],
                ["account_or_broker_needed", f"`{evidence_blocker_summary.get('user_account_or_broker_evidence_needed_count')}`" if evidence_blocker_summary else "`n/a`"],
                ["time_gated_needed", f"`{evidence_blocker_summary.get('time_gated_learning_outcome_needed_count')}`" if evidence_blocker_summary else "`n/a`"],
                ["should_stop_open_loop", f"`{evidence_blocker_summary.get('should_stop_open_loop_research')}`" if evidence_blocker_summary else "`n/a`"],
                ["recommended_next_mode", f"`{evidence_blocker_summary.get('recommended_next_mode')}`" if evidence_blocker_summary else "`n/a`"],
                ["output_report", payload.get("evidence_blocker_classification_md")],
            ],
        ),
        "",
        "这个面板把研究缺口分成“可重试公开数据 / 需要 API / 需要账户导出 / 等样本到期 / 安全边界”。如果大多数缺口不是公开数据能解决的，普通调度就应该收口，避免继续空跑。",
        "",
        "## Next Required Work",
        "",
        "\n".join(f"- {item}" for item in system.get("next_required_work") or []),
        "",
        "## Term Notes",
        "",
        "- `smoke_mode`: 冒烟测试模式，用缓存和跳过写推荐历史来验证脚本链路，不代表正式市场报告。",
        "- `fresh_market_intelligence`: 本次调度重新取市场数据、情绪和消息，而不是直接沿用旧报告。",
        "- `allow_degraded_market_intelligence`: 允许在部分数据源失败时生成降级报告；不允许把降级报告升级成强操作。",
        "- `execute_now_allowed`: 是否允许给出立即执行动作。即便为 true，也仍需要人工确认；当前系统通常保持 false。",
        "- `goal_mechanism_ready`: 机制可用，表示每次调度能形成“取数 -> 建议 -> 记录 -> 复盘 -> 归因 -> 下一轮补证据”的闭环。",
        "- `system_goal_complete`: 是否真实证明长期目标已完成。它不是报告能生成，而是财务目标和验证链都达标。",
        "- `schema_baseline`: 本地账本和结构说明是否可读。没有 `.sql` 文件时，当前系统会使用 Markdown schema 和 JSON 账本作为权威基础。",
        "- `paper_validation_pulse`: 纸面验证脉冲，只复核模拟仓位和样本缺口，用来积累经验，不是真实交易。",
        "- `learning_review_calendar`: 学习复盘日历，把纸面交易和历史建议的下一次复盘时间放到同一个清单，帮助系统逐步校准命中率。",
        "- `timeboxed_closeout`: 到点收集已有研究输出并降级缺席角色，避免一次普通手动调度无限等待。",
        "- `operating_playbook`: 日常操作手册，说明一次普通手动调度怎么跑、跑完先看什么、什么时候必须降级收口。",
        "- `formal_investment_report_valid`: 这份调度结果是否能作为正式人工投资判断入口；它不等于可以马上执行。",
        "- `execute_now_valid`: 这份报告是否同时满足正式报告、动作门槛和立即执行条件；即使为 true 也仍需人工确认。",
        "- `evidence blocker`: 证据阻断项，表示结论升级缺少什么证据。它可能需要 API、账户导出或等待样本到期，不一定能靠继续搜索解决。",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one full manual dispatch cycle")
    parser.add_argument("--date", default=local_date())
    parser.add_argument("--run-id", default="")
    parser.add_argument("--monthly-dca", type=float, default=1000.0)
    parser.add_argument("--crypto-symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,ADAUSDT,NIGHTUSDT")
    parser.add_argument("--current-tactical-symbol", default="")
    parser.add_argument("--env-file", default="")
    parser.add_argument("--external-agent-outputs-json", default="")
    parser.add_argument("--timeboxed-task-package-json", default="", help="Optional task package JSON for closeout audit")
    parser.add_argument("--timeboxed-collection-json", default="", help="Optional collected subagent output manifest JSON for closeout audit")
    parser.add_argument("--timeboxed-quality-audit-json", default="", help="Optional Research Committee quality audit JSON for closeout audit")
    parser.add_argument("--evidence-backlog-json", default="", help="Optional Research Committee evidence backlog JSON for blocker classification")
    parser.add_argument("--evidence-quality-audit-json", default="", help="Optional quality audit JSON paired with the evidence backlog")
    parser.add_argument("--skip-evidence-blocker-classification", action="store_true", help="Skip read-only evidence blocker classification")
    parser.add_argument("--require-external-subagents", action="store_true")
    parser.add_argument("--require-fresh-market-intelligence", action="store_true", help="Kept for compatibility; fresh market intelligence is required by default outside smoke mode")
    parser.add_argument("--allow-degraded-market-intelligence", action="store_true", help="Allow a degraded readable report when fresh market intelligence cannot be fully proven; still blocks execute_now")
    parser.add_argument("--use-existing-inputs", action="store_true")
    parser.add_argument("--skip-active-scanners", action="store_true")
    parser.add_argument("--skip-recommendation-history-write", action="store_true")
    parser.add_argument("--fetch-outcome-review-market-data", action="store_true")
    parser.add_argument("--skip-validation-pulse", action="store_true", help="Skip paper-only validation pulse after report generation")
    parser.add_argument("--validation-pulse-timeout-seconds", type=int, default=180)
    parser.add_argument("--skip-learning-review-calendar", action="store_true", help="Skip read-only learning review calendar")
    parser.add_argument("--learning-review-calendar-max-items", type=int, default=8)
    parser.add_argument("--skip-report-audits", action="store_true")
    parser.add_argument("--skip-system-audit", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="Use cached inputs and skip ledger writes/report audits for a fast safety smoke test")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--summary-json", default="")
    parser.add_argument("--summary-md", default="")
    parser.add_argument("--progress-jsonl", default="", help="Optional append-only runtime progress event stream")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    run_id = args.run_id or f"{compact_date(args.date)}-manual-dispatch-001"
    summary_json = Path(args.summary_json) if args.summary_json else MANUAL_ROOT / "experiments" / f"manual_dispatch_summary_{run_id}.json"
    summary_md = Path(args.summary_md) if args.summary_md else MANUAL_ROOT / "reports" / f"manual_dispatch_summary_{run_id}.md"
    progress_path = Path(args.progress_jsonl) if args.progress_jsonl else None
    env = os.environ.copy()
    env.setdefault("PYTHONPYCACHEPREFIX", "/private/tmp/python-pycache")
    if progress_path:
        env["MANUAL_PROGRESS_JSONL"] = str(progress_path)
        emit_progress(progress_path, "run_started", run_id=run_id, status="running")

    schema_step = run_command(build_schema_baseline_command(run_id), timeout=args.timeout_seconds, env=env, progress_path=progress_path, step_name="schema_baseline_auditor")
    schema_payload = parse_json_stdout(schema_step)
    schema_status = schema_payload.get("status") if isinstance(schema_payload, dict) else None
    schema_blocked = schema_step.get("status") != "ok" or schema_status == "schema_baseline_blocked"
    steps = [{"name": "schema_baseline_auditor", **compact_step_output(schema_step)}]

    if schema_blocked:
        report_step = {
            "status": "skipped",
            "returncode": None,
            "started_at": utc_now(),
            "finished_at": utc_now(),
            "stdout": "",
            "stderr": "schema baseline blocked; manual report generation skipped",
        }
        report_summary = {}
    else:
        report_step = run_command(build_report_command(args, run_id), timeout=args.timeout_seconds, env=env, progress_path=progress_path, step_name="generate_manual_report")
        report_summary = parse_json_stdout(report_step)
    steps.append({"name": "generate_manual_report", **compact_step_output(report_step)})
    system_step: dict[str, Any] | None = None
    progressive_step: dict[str, Any] | None = None
    closeout_step: dict[str, Any] | None = None
    validation_pulse_step: dict[str, Any] | None = None
    learning_calendar_step: dict[str, Any] | None = None
    closure_package_step: dict[str, Any] | None = None
    next_readiness_step: dict[str, Any] | None = None
    next_plan_step: dict[str, Any] | None = None
    evidence_blocker_step: dict[str, Any] | None = None
    system_payload: dict[str, Any] = {}
    progressive_payload: dict[str, Any] = {}
    closeout_payload: dict[str, Any] = {}
    validation_pulse_payload: dict[str, Any] = {}
    learning_calendar_payload: dict[str, Any] = {}
    closure_package_payload: dict[str, Any] = {}
    next_readiness_payload: dict[str, Any] = {}
    next_queue_payload: dict[str, Any] = {}
    next_plan_payload: dict[str, Any] = {}
    evidence_blocker_payload: dict[str, Any] = {}
    if report_step.get("status") == "ok" and not args.smoke and not args.skip_validation_pulse:
        validation_pulse_step = run_command(
            build_validation_pulse_command(args),
            timeout=args.validation_pulse_timeout_seconds + 30,
            env=env, progress_path=progress_path, step_name="paper_validation_pulse",
        )
        steps.append({"name": "paper_validation_pulse", **compact_step_output(validation_pulse_step)})
        validation_pulse_payload = parse_json_stdout(validation_pulse_step)
    if report_step.get("status") == "ok" and not args.smoke and not args.skip_learning_review_calendar:
        learning_calendar_step = run_command(
            build_learning_review_calendar_command(args, run_id),
            timeout=min(args.timeout_seconds, 120),
            env=env, progress_path=progress_path, step_name="learning_review_calendar",
        )
        steps.append({"name": "learning_review_calendar", **compact_step_output(learning_calendar_step)})
        learning_calendar_payload = parse_json_stdout(learning_calendar_step)
    if report_step.get("status") == "ok" and not args.skip_system_audit and not args.smoke:
        system_step = run_command(build_system_audit_command(args, report_summary, run_id), timeout=args.timeout_seconds, env=env, progress_path=progress_path, step_name="goal_system_completion_audit")
        steps.append({"name": "goal_system_completion_audit", **compact_step_output(system_step)})
        system_payload = parse_json_stdout(system_step)
    if system_payload and not args.smoke:
        closure_package_step = run_command(
            build_goal_evidence_closure_package_command(run_id),
            timeout=min(args.timeout_seconds, 120),
            env=env, progress_path=progress_path, step_name="goal_evidence_closure_package",
        )
        steps.append({"name": "goal_evidence_closure_package", **compact_step_output(closure_package_step)})
        closure_package_payload = parse_json_stdout(closure_package_step)
    if report_step.get("status") == "ok" and not args.smoke:
        progressive_step = run_command(build_progressive_learning_audit_command(report_summary, run_id), timeout=args.timeout_seconds, env=env, progress_path=progress_path, step_name="progressive_learning_iteration_audit")
        steps.append({"name": "progressive_learning_iteration_audit", **compact_step_output(progressive_step)})
        progressive_payload = parse_json_stdout(progressive_step)
    has_closeout_inputs = bool(
        args.timeboxed_task_package_json and args.timeboxed_collection_json and args.timeboxed_quality_audit_json
    )
    if has_closeout_inputs:
        closeout_step = run_command(build_timeboxed_closeout_audit_command(args, run_id), timeout=args.timeout_seconds, env=env, progress_path=progress_path, step_name="timeboxed_committee_closeout_audit")
        steps.append({"name": "timeboxed_committee_closeout_audit", **compact_step_output(closeout_step)})
        closeout_payload = parse_json_stdout(closeout_step)
    if (
        report_step.get("status") == "ok"
        and not args.smoke
        and not args.skip_system_audit
        and not args.skip_learning_review_calendar
        and system_payload
        and learning_calendar_payload
        and closure_package_payload
    ):
        next_readiness_step = run_command(
            build_next_dispatch_readiness_command(run_id),
            timeout=min(args.timeout_seconds, 120),
            env=env, progress_path=progress_path, step_name="next_dispatch_readiness",
        )
        steps.append({"name": "next_dispatch_readiness", **compact_step_output(next_readiness_step)})
        next_readiness_payload = parse_json_stdout(next_readiness_step)
    if next_readiness_payload:
        next_queue_step = run_command(
            build_next_goal_execution_queue_command(run_id),
            timeout=min(args.timeout_seconds, 120),
            env=env, progress_path=progress_path, step_name="next_goal_execution_queue",
        )
        steps.append({"name": "next_goal_execution_queue", **compact_step_output(next_queue_step)})
        next_queue_payload = parse_json_stdout(next_queue_step)
    if next_queue_payload:
        next_plan_step = run_command(
            build_next_manual_dispatch_execution_plan_command(run_id),
            timeout=min(args.timeout_seconds, 120),
            env=env, progress_path=progress_path, step_name="next_manual_dispatch_execution_plan",
        )
        steps.append({"name": "next_manual_dispatch_execution_plan", **compact_step_output(next_plan_step)})
        next_plan_payload = parse_json_stdout(next_plan_step)
    if report_step.get("status") == "ok" and not args.smoke and not args.skip_evidence_blocker_classification:
        evidence_command = build_evidence_blocker_classifier_command(args, run_id)
        if evidence_command:
            evidence_blocker_step = run_command(
                evidence_command,
                timeout=min(args.timeout_seconds, 120),
                env=env, progress_path=progress_path, step_name="evidence_blocker_classifier",
            )
            steps.append({"name": "evidence_blocker_classifier", **compact_step_output(evidence_blocker_step)})
            evidence_blocker_payload = parse_json_stdout(evidence_blocker_step)

    system_summary = system_payload.get("summary") if isinstance(system_payload, dict) else {}
    progressive_status = progressive_payload.get("status") if isinstance(progressive_payload, dict) else None
    closeout_status = closeout_payload.get("status") if isinstance(closeout_payload, dict) else None
    validation_pulse_status = validation_pulse_payload.get("status") if isinstance(validation_pulse_payload, dict) else None
    learning_calendar_status = learning_calendar_payload.get("status") if isinstance(learning_calendar_payload, dict) else None
    closure_package_status = closure_package_payload.get("status") if isinstance(closure_package_payload, dict) else None
    next_readiness_status = next_readiness_payload.get("status") if isinstance(next_readiness_payload, dict) else None
    next_queue_status = next_queue_payload.get("status") if isinstance(next_queue_payload, dict) else None
    next_plan_status = next_plan_payload.get("status") if isinstance(next_plan_payload, dict) else None
    evidence_blocker_status = evidence_blocker_payload.get("status") if isinstance(evidence_blocker_payload, dict) else None
    report_audit_failed = audit_status_is_failed(report_summary, "report_integrity_audit")
    objective_audit_failed = audit_status_is_failed(report_summary, "objective_coverage_audit")
    goal_mechanism_ready = bool(progressive_payload.get("goal_mechanism_ready")) if isinstance(progressive_payload, dict) else False
    ready_for_learning_loop = bool(progressive_payload.get("ready_for_progressive_learning_loop")) if isinstance(progressive_payload, dict) else False
    operating_playbook_summary = build_operating_playbook_summary(system_summary or {}, report_summary)
    current_tactical_resolution = current_tactical_symbol_resolution_from_report(report_summary)
    current_tactical_symbol = current_tactical_resolution.get("symbol") or ""
    current_tactical_symbol_source = current_tactical_resolution.get("source") or ""
    status = "ok"
    if schema_step.get("status") != "ok":
        status = "schema_baseline_failed"
    elif schema_status == "schema_baseline_blocked":
        status = "schema_baseline_blocked"
    elif report_step.get("status") != "ok":
        status = "report_failed"
    elif report_audit_failed or objective_audit_failed:
        status = "report_audit_failed"
    elif system_step and system_step.get("status") != "ok":
        status = "system_audit_failed"
    elif learning_calendar_step and learning_calendar_step.get("status") != "ok":
        status = "learning_review_calendar_failed"
    elif closure_package_step and closure_package_step.get("status") != "ok":
        status = "goal_evidence_closure_package_failed"
    elif progressive_step and progressive_step.get("status") != "ok":
        status = "progressive_learning_audit_failed"
    elif closeout_step and closeout_step.get("status") != "ok":
        status = "timeboxed_closeout_audit_failed"
    elif next_readiness_step and next_readiness_step.get("status") != "ok":
        status = "next_dispatch_readiness_failed"
    elif next_queue_payload and next_queue_status != "ok":
        status = "next_goal_execution_queue_failed"
    elif next_plan_payload and next_plan_status != "ok":
        status = "next_manual_dispatch_execution_plan_failed"
    elif evidence_blocker_step and evidence_blocker_step.get("status") != "ok":
        status = "evidence_blocker_classification_failed"
    investment_decision_status = build_investment_decision_status(
        args=args,
        status=status,
        report_summary=report_summary,
        report_audit_failed=report_audit_failed,
        objective_audit_failed=objective_audit_failed,
        system_summary=system_summary or {},
        progressive_payload=progressive_payload if isinstance(progressive_payload, dict) else {},
        learning_calendar_payload=learning_calendar_payload if isinstance(learning_calendar_payload, dict) else {},
        validation_pulse_payload=validation_pulse_payload if isinstance(validation_pulse_payload, dict) else {},
        next_readiness_payload=next_readiness_payload if isinstance(next_readiness_payload, dict) else {},
    )

    payload = {
        "run_id": run_id,
        "generated_at": utc_now(),
        "status": status,
        "investment_decision_status": investment_decision_status,
        "smoke_mode": bool(args.smoke),
        "manual_dispatch_only": True,
        "default_requires_fresh_market_intelligence": not bool(args.smoke),
        "allow_degraded_market_intelligence": bool(args.allow_degraded_market_intelligence),
        "background_loop_started": False,
        "live_orders_enabled": False,
        "mutates_portfolio_ledger": False,
        "schema_baseline_summary": schema_payload if isinstance(schema_payload, dict) else {},
        "schema_baseline_audit_json": str(MANUAL_ROOT / "experiments" / f"schema_baseline_audit_{run_id}.json"),
        "schema_baseline_audit_md": str(MANUAL_ROOT / "reports" / f"schema_baseline_audit_{run_id}.md"),
        "report_summary": report_summary,
        "current_tactical_symbol_resolution": current_tactical_resolution,
        "current_tactical_symbol": current_tactical_symbol,
        "current_tactical_symbol_source": current_tactical_symbol_source,
        "report_audit_failed": report_audit_failed,
        "objective_audit_failed": objective_audit_failed,
        "system_audit_summary": system_summary or {},
        "system_audit_json": (system_payload.get("inputs") or {}) if system_payload else {},
        "validation_pulse_summary": validation_pulse_payload if isinstance(validation_pulse_payload, dict) else {},
        "validation_pulse_status": validation_pulse_status,
        "learning_review_calendar_summary": learning_calendar_payload if isinstance(learning_calendar_payload, dict) else {},
        "learning_review_calendar_status": learning_calendar_status,
        "learning_review_calendar_json": str(MANUAL_ROOT / "experiments" / f"learning_review_calendar_{run_id}.json") if learning_calendar_payload else "",
        "learning_review_calendar_md": str(MANUAL_ROOT / "reports" / f"learning_review_calendar_{run_id}.md") if learning_calendar_payload else "",
        "goal_evidence_closure_package_summary": closure_package_payload if isinstance(closure_package_payload, dict) else {},
        "goal_evidence_closure_package_status": closure_package_status,
        "goal_evidence_closure_package_json": str(MANUAL_ROOT / "experiments" / f"goal_evidence_closure_package_{run_id}.json") if closure_package_payload else "",
        "goal_evidence_closure_package_md": str(MANUAL_ROOT / "reports" / f"goal_evidence_closure_package_{run_id}.md") if closure_package_payload else "",
        "next_dispatch_readiness_summary": next_readiness_payload if isinstance(next_readiness_payload, dict) else {},
        "next_dispatch_readiness_status": next_readiness_status,
        "next_dispatch_readiness_json": str(MANUAL_ROOT / "experiments" / f"next_dispatch_readiness_{run_id}.json") if next_readiness_payload else "",
        "next_dispatch_readiness_md": str(MANUAL_ROOT / "reports" / f"next_dispatch_readiness_{run_id}.md") if next_readiness_payload else "",
        "next_goal_execution_queue_summary": next_queue_payload if isinstance(next_queue_payload, dict) else {},
        "next_goal_execution_queue_status": next_queue_status,
        "next_goal_execution_queue_json": str(MANUAL_ROOT / "experiments" / f"next_goal_execution_queue_{run_id}.json") if next_queue_payload else "",
        "next_goal_execution_queue_md": str(MANUAL_ROOT / "reports" / f"next_goal_execution_queue_{run_id}.md") if next_queue_payload else "",
        "next_manual_dispatch_execution_plan_summary": next_plan_payload if isinstance(next_plan_payload, dict) else {},
        "next_manual_dispatch_execution_plan_status": next_plan_status,
        "next_manual_dispatch_execution_plan_json": str(MANUAL_ROOT / "experiments" / f"next_manual_dispatch_execution_plan_{run_id}.json") if next_plan_payload else "",
        "next_manual_dispatch_execution_plan_md": str(MANUAL_ROOT / "reports" / f"next_manual_dispatch_execution_plan_{run_id}.md") if next_plan_payload else "",
        "evidence_blocker_classification_summary": evidence_blocker_payload if isinstance(evidence_blocker_payload, dict) else {},
        "evidence_blocker_classification_status": evidence_blocker_status,
        "evidence_blocker_classification_json": str(MANUAL_ROOT / "experiments" / f"evidence_blocker_classification_{run_id}.json") if evidence_blocker_payload else "",
        "evidence_blocker_classification_md": str(MANUAL_ROOT / "reports" / f"evidence_blocker_classification_{run_id}.md") if evidence_blocker_payload else "",
        "progressive_learning_audit_summary": progressive_payload if isinstance(progressive_payload, dict) else {},
        "progressive_learning_audit_status": progressive_status,
        "progressive_learning_audit_json": ((progressive_payload.get("input_paths") or {}) if isinstance(progressive_payload, dict) else {}),
        "progressive_learning_audit_md": str(MANUAL_ROOT / "reports" / f"progressive_learning_iteration_audit_{run_id}.md") if progressive_payload else "",
        "timeboxed_closeout_audit_summary": closeout_payload if isinstance(closeout_payload, dict) else {},
        "timeboxed_closeout_audit_status": closeout_status,
        "timeboxed_closeout_audit_md": str(MANUAL_ROOT / "reports" / f"timeboxed_committee_closeout_audit_{run_id}.md") if closeout_payload else "",
        "operating_playbook_summary": operating_playbook_summary,
        "iteration_acceptance": {
            "acceptance_target": "goal_mechanism_ready + ready_for_progressive_learning_loop",
            "not_required_this_iteration": "goal_complete, guaranteed profit, or already-validated 80% true forecast accuracy",
            "learning_path": "start with reviewable 60%+ learning samples; promote toward 80%+ only after resolved outcomes and evidence calibration",
            "goal_mechanism_ready": goal_mechanism_ready,
            "ready_for_progressive_learning_loop": ready_for_learning_loop,
            "goal_complete": False,
            "ready": bool(goal_mechanism_ready and ready_for_learning_loop),
        },
        "steps": steps,
        "output_json": str(summary_json),
        "output_md": str(summary_md),
    }
    write_json(summary_json, payload)
    write_text(summary_md, render_markdown(payload))
    emit_progress(
        progress_path,
        "run_completed",
        run_id=run_id,
        status="success" if status == "ok" else "partial",
        formal_report_valid=investment_decision_status.get("formal_investment_report_valid"),
    )
    if args.format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
