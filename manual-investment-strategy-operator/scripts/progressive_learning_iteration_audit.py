#!/usr/bin/env python3
"""Audit whether the goal system is learning across dispatches.

This script does not judge whether the investment target is achieved. It
checks whether the mechanism needed to improve from rough 60% learning samples
toward validated 80%+ decisions is present: recommendation reviews, paper
samples, research evidence, and a concrete next evidence queue.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
DEFAULT_RECOMMENDATION_LEDGER = MANUAL_ROOT / "recommendations" / "recommendation_history.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def latest_matching(patterns: list[str]) -> Path | None:
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(path for path in MANUAL_ROOT.glob(pattern) if path.is_file())
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def action_rank(level: str | None) -> int:
    ranks = {
        "block": 0,
        "no_deploy": 1,
        "watch": 2,
        "paper_only": 3,
        "conditional_action": 4,
        "conditional_action_or_watch": 4,
        "hold_or_conditional_action": 4,
        "execute_now": 5,
    }
    return ranks.get(str(level or "").strip(), 2)


def strictest_action(*levels: str | None) -> str:
    known = [str(level) for level in levels if level]
    if not known:
        return "watch"
    return min(known, key=action_rank)


def summarize_recommendations(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "missing",
            "path": str(path),
            "total": 0,
            "pending": 0,
            "resolved": 0,
            "superseded": 0,
            "outcome_reviews": 0,
            "proposed_changes": 0,
        }
    payload = load_json(path)
    recommendations = payload.get("recommendations") or []
    resolved_statuses = {"hit", "failed", "not_triggered", "expired", "invalidated"}
    return {
        "status": "ok",
        "path": str(path),
        "total": len(recommendations),
        "pending": sum(1 for item in recommendations if item.get("outcome_status") == "pending"),
        "resolved": sum(1 for item in recommendations if item.get("outcome_status") in resolved_statuses),
        "superseded": sum(1 for item in recommendations if item.get("outcome_status") == "superseded"),
        "outcome_reviews": len(payload.get("outcome_reviews") or []),
        "proposed_changes": len(payload.get("proposed_changes") or []),
    }


def ratio(current: int | float, target: int | float) -> float:
    if target <= 0:
        return 1.0
    return min(max(float(current) / float(target), 0.0), 1.0)


def build_audit(
    dashboard: dict[str, Any],
    quality_audit: dict[str, Any] | None,
    evidence_backlog: dict[str, Any] | None,
    task_package: dict[str, Any] | None,
    recommendation_ledger_path: Path,
) -> dict[str, Any]:
    learning = dashboard.get("progressive_learning_state") or {}
    evidence = dashboard.get("evidence_and_gate_state") or {}
    validation_gaps = evidence.get("validation_sample_gaps") or {}
    rec = summarize_recommendations(recommendation_ledger_path)
    quality_validation = (quality_audit or {}).get("external_agent_validation") or {}
    evidence_goal = (evidence_backlog or {}).get("evidence_goal") or {}

    paper_closed = as_int(learning.get("paper_closed_count"))
    paper_needed = as_int(validation_gaps.get("closed_paper_trades_needed") or learning.get("closed_paper_trades_needed"))
    paper_target = paper_closed + paper_needed
    rec_resolved = as_int(rec.get("resolved") or learning.get("recommendation_resolved_count"))
    rec_needed = as_int(validation_gaps.get("calibration_resolved_needed") or learning.get("calibration_resolved_needed"))
    rec_target = rec_resolved + rec_needed
    evidence_verified = as_int(
        quality_validation.get("evidence_verified_known_role_count")
        or evidence_goal.get("current_evidence_verified_roles")
    )
    evidence_target = as_int(evidence_goal.get("target_evidence_verified_roles"), 6)
    walkforward_needed = as_int(validation_gaps.get("walkforward_target_research_pass_needed"))
    paper_win = as_float(learning.get("paper_win_rate_pct"))

    learning_progress_score = round(
        100
        * (
            ratio(paper_closed, paper_target or 20)
            + ratio(rec_resolved, rec_target or 10)
            + ratio(evidence_verified, evidence_target or 6)
        )
        / 3,
        2,
    )
    mechanism_ready = (
        dashboard.get("status") == "ok"
        and learning.get("status") == "active"
        and rec.get("status") == "ok"
        and dashboard.get("paper_validation_review_calendar") is not None
    )
    action_cap = strictest_action(
        evidence.get("report_max_allowed_action"),
        evidence.get("short_term_tactical_max_allowed_action"),
        (quality_audit or {}).get("max_allowed_action"),
    )
    if walkforward_needed or rec_needed or paper_needed or evidence_verified < evidence_target:
        action_cap = strictest_action(action_cap, "paper_only" if paper_needed or rec_needed else "watch")

    backlog_tasks = (evidence_backlog or {}).get("research_evidence_tasks") or []
    execution_backlog = dashboard.get("execution_backlog") or []
    task_count = as_int((task_package or {}).get("task_count"))

    audit = {
        "generated_at": utc_now(),
        "audit_version": "progressive-learning-iteration-audit-v1",
        "status": "ok" if mechanism_ready else "degraded",
        "live_orders_enabled": False,
        "auto_trading_enabled": False,
        "does_not_authorize_trades": True,
        "manual_dispatch_boundary": "passive_manual_dispatch_only",
        "goal_complete": False,
        "goal_mechanism_ready": bool(mechanism_ready),
        "ready_for_progressive_learning_loop": bool(mechanism_ready and (execution_backlog or backlog_tasks)),
        "max_allowed_action": action_cap,
        "learning_progress_score_points": learning_progress_score,
        "score_note": "This score measures evidence-loop progress, not return probability or profit guarantee.",
        "learning_first_acceptance": {
            "acceptance_target": "goal_mechanism_ready + ready_for_progressive_learning_loop",
            "not_required_this_iteration": [
                "goal_complete",
                "guaranteed_profit",
                "already_validated_80_pct_true_forecast_accuracy",
            ],
            "current_starting_point": "60%-79% reviewable learning samples are allowed as watch/paper/conditional records.",
            "promotion_rule": "Only resolved outcomes, evidence calibration, double-80 readiness, cash-rail proof, and human confirmation can promote a candidate.",
        },
        "learning_stage": learning.get("learning_stage"),
        "next_stage": learning.get("next_stage"),
        "paper_validation": {
            "closed_count": paper_closed,
            "closed_target": paper_target or 20,
            "closed_needed": paper_needed,
            "win_rate_pct": paper_win,
            "learning_floor_met": learning.get("paper_learning_floor_met"),
        },
        "recommendation_learning": {
            "total": rec.get("total"),
            "pending": rec.get("pending"),
            "resolved": rec_resolved,
            "resolved_target": rec_target or 10,
            "resolved_needed": rec_needed,
            "outcome_reviews": rec.get("outcome_reviews"),
            "proposed_changes": rec.get("proposed_changes"),
        },
        "research_committee_learning": {
            "committee_quality_gate_passed": bool((quality_audit or {}).get("status") == "ok"),
            "evidence_verified_roles": evidence_verified,
            "target_evidence_verified_roles": evidence_target,
            "additional_evidence_verified_roles_needed": max(evidence_target - evidence_verified, 0),
            "roles_with_ok_sources": quality_validation.get("roles_with_ok_source_count") or evidence_goal.get("roles_with_ok_sources"),
            "quality_blockers": (quality_audit or {}).get("quality_gate_blockers") or [],
            "role_native_evidence_gaps": quality_validation.get("role_native_evidence_gaps") or {},
        },
        "next_learning_queue": {
            "execution_backlog_count": len(execution_backlog),
            "research_evidence_task_count": len(backlog_tasks),
            "subagent_task_count": task_count,
            "research_agents_to_repair": [item.get("agent_id") for item in backlog_tasks],
            "top_execution_backlog": execution_backlog[:5],
        },
        "must_not_claim": [
            "Do not claim 5y/10y 10x is achieved.",
            "Do not claim 80% true forecast probability until calibration and evidence gates pass.",
            "Do not output execute_now while paper, recommendation, research, or cash-rail gates are incomplete.",
        ],
    }
    return audit


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join("" if item is None else str(item) for item in row) + " |")
    return "\n".join(lines)


def render_markdown(payload: dict[str, Any]) -> str:
    paper = payload.get("paper_validation") or {}
    rec = payload.get("recommendation_learning") or {}
    research = payload.get("research_committee_learning") or {}
    queue = payload.get("next_learning_queue") or {}
    acceptance = payload.get("learning_first_acceptance") or {}
    summary_rows = [
        ["goal_mechanism_ready", f"`{payload.get('goal_mechanism_ready')}`"],
        ["goal_complete", f"`{payload.get('goal_complete')}`"],
        ["ready_for_progressive_learning_loop", f"`{payload.get('ready_for_progressive_learning_loop')}`"],
        ["max_allowed_action", f"`{payload.get('max_allowed_action')}`"],
        ["learning_stage", f"`{payload.get('learning_stage')}`"],
        ["learning_progress_score", f"`{payload.get('learning_progress_score_points')}`"],
    ]
    evidence_rows = [
        ["closed paper trades", paper.get("closed_count"), paper.get("closed_target"), paper.get("closed_needed")],
        ["recommendation resolved", rec.get("resolved"), rec.get("resolved_target"), rec.get("resolved_needed")],
        [
            "evidence-verified roles",
            research.get("evidence_verified_roles"),
            research.get("target_evidence_verified_roles"),
            research.get("additional_evidence_verified_roles_needed"),
        ],
    ]
    queue_rows = [
        ["execution_backlog", queue.get("execution_backlog_count")],
        ["research_evidence_tasks", queue.get("research_evidence_task_count")],
        ["subagent_tasks", queue.get("subagent_task_count")],
        ["agents_to_repair", ", ".join(queue.get("research_agents_to_repair") or [])],
    ]
    return "\n".join([
        "# Progressive Learning Iteration Audit",
        "",
        "This audit checks whether the system is learning. It does not prove the return target has been achieved.",
        "",
        markdown_table(["Metric", "Value"], summary_rows),
        "",
        "## Learning-First Acceptance",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["acceptance_target", acceptance.get("acceptance_target")],
                ["not_required_this_iteration", ", ".join(acceptance.get("not_required_this_iteration") or [])],
                ["current_starting_point", acceptance.get("current_starting_point")],
                ["promotion_rule", acceptance.get("promotion_rule")],
            ],
        ),
        "",
        "## Evidence Progress",
        "",
        markdown_table(["Evidence", "Current", "Target", "Needed"], evidence_rows),
        "",
        "## Next Learning Queue",
        "",
        markdown_table(["Queue", "Value"], queue_rows),
        "",
        "## Guardrails",
        "",
        "- Do not claim 5y/10y 10x is achieved.",
        "- Do not claim 80% true forecast probability until samples and evidence gates pass.",
        "- Keep real trading manual-confirmation only.",
        "",
        "Term note: `evidence-verified` means the role has traceable sources and clear gaps. It does not mean a trade is ready.",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit progressive learning loop status")
    parser.add_argument("--dashboard-json", default="", help="Goal execution dashboard JSON")
    parser.add_argument("--quality-audit-json", default="", help="Research committee quality audit JSON")
    parser.add_argument("--evidence-backlog-json", default="", help="Research evidence backlog JSON")
    parser.add_argument("--task-package-json", default="", help="Research subagent task package JSON")
    parser.add_argument("--recommendation-ledger", default=str(DEFAULT_RECOMMENDATION_LEDGER))
    parser.add_argument("--output", default="", help="Optional JSON output")
    parser.add_argument("--markdown-output", default="", help="Optional Markdown output")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    dashboard_path = Path(args.dashboard_json) if args.dashboard_json else latest_matching([
        "experiments/*goal*dashboard*.json",
        "experiments/*goal-execution-dashboard*.json",
    ])
    if dashboard_path is None:
        parser.error("No dashboard JSON supplied and none found")

    quality_path = Path(args.quality_audit_json) if args.quality_audit_json else latest_matching([
        "experiments/*committee-quality*.json",
        "experiments/*quality-audit*.json",
        "experiments/*committee_quality*.json",
        "experiments/*quality_audit*.json",
    ])
    backlog_path = Path(args.evidence_backlog_json) if args.evidence_backlog_json else latest_matching([
        "experiments/*research-evidence-backlog*.json",
        "experiments/*research_evidence_backlog*.json",
    ])
    task_package_path = Path(args.task_package_json) if args.task_package_json else latest_matching([
        "experiments/*research-subagent-task-pack*.json",
        "experiments/*research_subagent_task_package*.json",
        "experiments/*research_subagent_task_pack*.json",
    ])

    payload = build_audit(
        load_json(dashboard_path),
        load_json(quality_path) if quality_path and quality_path.exists() else None,
        load_json(backlog_path) if backlog_path and backlog_path.exists() else None,
        load_json(task_package_path) if task_package_path and task_package_path.exists() else None,
        Path(args.recommendation_ledger),
    )
    payload["input_paths"] = {
        "dashboard_json": str(dashboard_path),
        "quality_audit_json": str(quality_path) if quality_path else None,
        "evidence_backlog_json": str(backlog_path) if backlog_path else None,
        "task_package_json": str(task_package_path) if task_package_path else None,
        "recommendation_ledger": args.recommendation_ledger,
    }
    if args.output:
        write_json(Path(args.output), payload)
    if args.markdown_output:
        Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_output).write_text(render_markdown(payload), encoding="utf-8")
    if args.format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
