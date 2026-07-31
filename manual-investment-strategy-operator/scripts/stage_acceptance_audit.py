#!/usr/bin/env python3
"""Audit temporary stage completion for the manual investment system.

This audit answers a narrower question than `goal_system_completion_audit.py`:
is the current system complete enough to keep iterating from a 60% learning
floor toward an 80% validated decision process?

It does not authorize trades and does not claim the 5y/10y 10x financial goal
has already happened.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
ACTIVE_ROOT = ROOT / "active-alpha-paper-monitor"
DEFAULT_PAPER_LEDGER = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
DEFAULT_RECOMMENDATION_LEDGER = MANUAL_ROOT / "recommendations" / "recommendation_history.json"
DEFAULT_CONFIG = MANUAL_ROOT / "config" / "manual_strategy_config.json"


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
        candidates.extend(item for item in MANUAL_ROOT.glob(pattern) if item.is_file())
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def summarize_paper_ledger(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    closed = payload.get("closed_trades") or []
    open_positions = payload.get("open_positions") or []
    hit_count = sum(1 for item in closed if item.get("outcome") == "hit")
    failed_count = sum(1 for item in closed if item.get("outcome") == "failed")
    resolved_count = hit_count + failed_count
    win_rate = round(hit_count / resolved_count * 100.0, 4) if resolved_count else None
    return {
        "path": str(path),
        "status": "ok",
        "live_orders_enabled": bool(payload.get("live_orders_enabled")),
        "closed_count": len(closed),
        "open_count": len(open_positions),
        "hit_count": hit_count,
        "failed_count": failed_count,
        "resolved_count": resolved_count,
        "win_rate_pct": win_rate,
        "net_return_pct": payload.get("net_return_pct"),
        "max_drawdown_pct": payload.get("max_drawdown_pct"),
        "updated_at": payload.get("updated_at"),
    }


def summarize_recommendation_ledger(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    recommendations = payload.get("recommendations") or []
    resolved_statuses = {"hit", "failed", "not_triggered", "expired", "invalidated"}
    return {
        "path": str(path),
        "status": "ok",
        "total": len(recommendations),
        "pending": sum(1 for item in recommendations if item.get("outcome_status") == "pending"),
        "resolved": sum(1 for item in recommendations if item.get("outcome_status") in resolved_statuses),
        "superseded": sum(1 for item in recommendations if item.get("outcome_status") == "superseded"),
        "outcome_reviews": len(payload.get("outcome_reviews") or []),
        "proposed_changes": len(payload.get("proposed_changes") or []),
        "updated_at": payload.get("updated_at"),
    }


def audit_status_ok(path: Path | None) -> bool | None:
    if path is None or not path.exists():
        return None
    payload = load_json(path)
    return payload.get("status") == "ok" and int(payload.get("failed") or 0) == 0


def build_audit(
    completion_audit: dict[str, Any] | None,
    report_integrity_path: Path | None,
    objective_coverage_path: Path | None,
    paper_ledger_path: Path,
    recommendation_ledger_path: Path,
    config_path: Path,
) -> dict[str, Any]:
    config = load_json(config_path)
    stage_config = config.get("stage_acceptance_and_compounding_policy") or {}
    thresholds = stage_config.get("acceptance_thresholds") or {}
    min_closed = int(thresholds.get("min_closed_paper_trades") or 10)
    min_win = float(thresholds.get("min_paper_win_rate_pct") or 60)

    paper = summarize_paper_ledger(paper_ledger_path)
    rec = summarize_recommendation_ledger(recommendation_ledger_path)
    completion_summary = (completion_audit or {}).get("summary") or {}

    report_integrity_ok = audit_status_ok(report_integrity_path)
    objective_coverage_ok = audit_status_ok(objective_coverage_path)
    paper_win = as_float(paper.get("win_rate_pct"))
    paper_floor_met = (
        paper.get("closed_count", 0) >= min_closed
        and paper_win is not None
        and paper_win >= min_win
    )

    checks = [
        {
            "check": "system_plan_implemented",
            "passed": bool(completion_summary.get("system_plan_implemented")),
            "evidence": completion_summary.get("system_plan_implemented"),
        },
        {
            "check": "ready_for_manual_reports",
            "passed": bool(completion_summary.get("ready_for_manual_reports")),
            "evidence": completion_summary.get("ready_for_manual_reports"),
        },
        {
            "check": "ready_for_progressive_learning_loop",
            "passed": bool(completion_summary.get("ready_for_progressive_learning_loop")),
            "evidence": completion_summary.get("ready_for_progressive_learning_loop"),
        },
        {
            "check": "report_integrity_ok",
            "passed": bool(report_integrity_ok),
            "evidence": str(report_integrity_path) if report_integrity_path else "missing",
        },
        {
            "check": "objective_coverage_ok",
            "passed": bool(objective_coverage_ok),
            "evidence": str(objective_coverage_path) if objective_coverage_path else "missing",
        },
        {
            "check": "recommendation_history_records_exist",
            "passed": rec.get("total", 0) > 0,
            "evidence": f"total={rec.get('total')}; outcome_reviews={rec.get('outcome_reviews')}",
        },
        {
            "check": "paper_learning_floor_60pct_met",
            "passed": bool(paper_floor_met),
            "evidence": f"closed={paper.get('closed_count')}; win_rate={paper.get('win_rate_pct')}%; min_closed={min_closed}; min_win={min_win}%",
        },
        {
            "check": "live_orders_disabled",
            "passed": not bool(paper.get("live_orders_enabled")),
            "evidence": f"paper_live_orders_enabled={paper.get('live_orders_enabled')}",
        },
    ]
    temporarily_complete = all(item["passed"] for item in checks)
    compounding = stage_config.get("compounding_reference") or {}

    return {
        "generated_at": utc_now(),
        "audit_version": "stage-acceptance-audit-v1",
        "status": "ok" if temporarily_complete else "incomplete",
        "stage_system_complete": bool(temporarily_complete),
        "temporarily_complete": bool(temporarily_complete),
        "goal_complete": False,
        "does_not_authorize_trades": True,
        "live_orders_enabled": False,
        "max_allowed_action": completion_summary.get("max_allowed_current_action") or "paper_only",
        "stage_definition": {
            "meaning": "System build milestone is complete enough to continue iterative learning from a 60% floor.",
            "not_meaning": "This does not mean the 5y/10y 10x financial result is achieved and does not enable automatic trading.",
        },
        "compounding_path_reference": {
            "five_year_10x_monthly_required_pct": compounding.get("five_year_10x_monthly_required_pct", 3.91),
            "five_year_10x_quarterly_required_pct": compounding.get("five_year_10x_quarterly_required_pct", 12.2),
            "ten_year_10x_monthly_required_pct": compounding.get("ten_year_10x_monthly_required_pct", 1.94),
            "ten_year_10x_quarterly_required_pct": compounding.get("ten_year_10x_quarterly_required_pct", 5.93),
            "floor_semantics": compounding.get("floor_semantics") or "These are minimum compounding floors, not ceilings.",
            "plain_note": "这些复利数字是最低线，不是上限；每次调度要尽量寻找高于最低线的正期望机会，同时控制大回撤、记录结果并复盘，复利路径才有机会逼近 10x。",
        },
        "paper_learning_evidence": paper,
        "recommendation_learning_evidence": rec,
        "checks": checks,
        "next_iteration_focus": [
            "Keep recording every DCA/tactical recommendation with review dates.",
            "Use paper and historical outcomes to separate real 60%+ edges from noise.",
            "Do not promote to 80% true-confidence candidates until resolved outcomes and double-80 gates support it.",
            "Keep DCA sizing tied to the long-term compounding path and avoid large drawdowns that break compounding.",
        ],
        "input_paths": {
            "completion_audit_json": str((completion_audit or {}).get("_source_path") or ""),
            "report_integrity_json": str(report_integrity_path) if report_integrity_path else "",
            "objective_coverage_json": str(objective_coverage_path) if objective_coverage_path else "",
            "paper_ledger": str(paper_ledger_path),
            "recommendation_ledger": str(recommendation_ledger_path),
            "config": str(config_path),
        },
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join("" if item is None else str(item) for item in row) + " |")
    return "\n".join(lines)


def render_markdown(payload: dict[str, Any]) -> str:
    paper = payload.get("paper_learning_evidence") or {}
    rec = payload.get("recommendation_learning_evidence") or {}
    comp = payload.get("compounding_path_reference") or {}
    checks = payload.get("checks") or []
    return "\n".join([
        "# Stage Acceptance Audit",
        "",
        "This audit checks the temporary system-completion stage. It does not prove the final 10x financial outcome.",
        "",
        markdown_table(
            ["Metric", "Value"],
            [
                ["stage_system_complete", f"`{payload.get('stage_system_complete')}`"],
                ["temporarily_complete", f"`{payload.get('temporarily_complete')}`"],
                ["goal_complete", f"`{payload.get('goal_complete')}`"],
                ["max_allowed_action", f"`{payload.get('max_allowed_action')}`"],
                ["paper_closed", paper.get("closed_count")],
                ["paper_win_rate", f"{paper.get('win_rate_pct')}%"],
                ["recommendation_records", rec.get("total")],
            ],
        ),
        "",
        "## Compounding Path",
        "",
        markdown_table(
            ["Path", "Required Average Growth"],
            [
                ["5y 10x monthly", f"{comp.get('five_year_10x_monthly_required_pct')}%"],
                ["5y 10x quarterly", f"{comp.get('five_year_10x_quarterly_required_pct')}%"],
                ["10y 10x monthly", f"{comp.get('ten_year_10x_monthly_required_pct')}%"],
                ["10y 10x quarterly", f"{comp.get('ten_year_10x_quarterly_required_pct')}%"],
            ],
        ),
        "",
        comp.get("plain_note") or "",
        "",
        "## Checks",
        "",
        markdown_table(
            ["Check", "Passed", "Evidence"],
            [[item.get("check"), f"`{item.get('passed')}`", item.get("evidence")] for item in checks],
        ),
        "",
        "## Guardrail",
        "",
        "A 60% learning-floor win rate means the system can keep iterating. It does not mean any single new trade is guaranteed, and it does not enable automatic trading.",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit temporary stage acceptance for the 10x manual strategy system")
    parser.add_argument("--completion-audit-json", default="")
    parser.add_argument("--report-integrity-json", default="")
    parser.add_argument("--objective-coverage-json", default="")
    parser.add_argument("--paper-ledger", default=str(DEFAULT_PAPER_LEDGER))
    parser.add_argument("--recommendation-ledger", default=str(DEFAULT_RECOMMENDATION_LEDGER))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    completion_path = Path(args.completion_audit_json) if args.completion_audit_json else latest_matching([
        "experiments/*goal_system_completion_audit*.json",
    ])
    report_integrity_path = Path(args.report_integrity_json) if args.report_integrity_json else latest_matching([
        "experiments/*report-integrity-audit*.json",
        "experiments/*report_integrity_audit*.json",
    ])
    objective_coverage_path = Path(args.objective_coverage_json) if args.objective_coverage_json else latest_matching([
        "experiments/*objective-coverage-audit*.json",
        "experiments/*objective_coverage_audit*.json",
    ])

    completion_audit = load_json(completion_path) if completion_path and completion_path.exists() else None
    if isinstance(completion_audit, dict) and completion_path:
        completion_audit["_source_path"] = str(completion_path)

    payload = build_audit(
        completion_audit,
        report_integrity_path,
        objective_coverage_path,
        Path(args.paper_ledger),
        Path(args.recommendation_ledger),
        Path(args.config),
    )
    if args.output_json:
        write_json(Path(args.output_json), payload)
    if args.output_md:
        Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_md).write_text(render_markdown(payload), encoding="utf-8")
    if args.format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
