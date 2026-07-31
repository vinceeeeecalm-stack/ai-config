#!/usr/bin/env python3
"""Summarize readiness for the next manual investment dispatch.

This is a read-only preflight. It does not fetch market data, mutate ledgers,
place orders, or authorize trades. Its job is to turn the latest completion
audit, learning calendar, and evidence closure package into a short answer:
can the next manual dispatch run, what is still blocked, and what should be
reviewed first?
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
EXPERIMENTS = MANUAL_ROOT / "experiments"
REPORTS = MANUAL_ROOT / "reports"
SHANGHAI = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def suggested_live_run_id(now: dt.datetime | None = None) -> str:
    current = now or dt.datetime.now(dt.timezone.utc)
    local = current.astimezone(SHANGHAI)
    return local.strftime("%Y%m%d-live-manual-dispatch-%H%M")


def default_manual_dispatch_base_command(now: dt.datetime | None = None) -> str:
    return (
        "python3 manual-investment-strategy-operator/scripts/manual_dispatch_run.py "
        f"--run-id {suggested_live_run_id(now)} --monthly-dca 1000"
    )


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_write_outputs(output_json: Path, output_md: Path, payload: dict[str, Any], markdown: str) -> dict[str, str]:
    """Write outputs, falling back to /private/tmp when an installed skill path is read-only."""
    written: dict[str, str] = {}
    try:
        write_json(output_json, payload)
        written["json"] = str(output_json)
    except PermissionError:
        fallback = Path("/private/tmp") / output_json.name
        write_json(fallback, payload)
        written["json"] = str(fallback)
        written["json_fallback_reason"] = f"permission_denied:{output_json}"

    try:
        write_text(output_md, markdown)
        written["markdown"] = str(output_md)
    except PermissionError:
        fallback = Path("/private/tmp") / output_md.name
        write_text(fallback, markdown)
        written["markdown"] = str(fallback)
        written["markdown_fallback_reason"] = f"permission_denied:{output_md}"

    return written


def parse_generated_at(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def json_generated_at(path: Path) -> dt.datetime | None:
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return parse_generated_at(payload.get("generated_at"))


def artifact_sort_key(path: Path, payload: dict[str, Any] | None = None) -> tuple[dt.datetime, float, str]:
    generated_at = parse_generated_at((payload or {}).get("generated_at")) if payload else json_generated_at(path)
    return (
        generated_at or dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc),
        path.stat().st_mtime,
        path.name,
    )


def is_test_or_smoke_artifact(path: Path, payload: dict[str, Any] | None = None) -> bool:
    """Identify artifacts produced directly by smoke/integration structure checks.

    Do not inspect referenced input paths here. A formal completion audit may
    cite a degraded smoke summary as evidence while still being the latest
    authoritative system audit.
    """
    text = path.name.lower()
    if "smoke" in text:
        return True
    run_id = str((payload or {}).get("run_id") or "").lower()
    if "smoke" in run_id:
        return True
    return False


def newest(pattern: str, prefer_non_test: bool = False) -> Path | None:
    raw_candidates = list(EXPERIMENTS.glob(pattern))
    if not raw_candidates:
        return None
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in raw_candidates:
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            payload = {}
        candidates.append((path, payload if isinstance(payload, dict) else {}))
    if prefer_non_test:
        non_test = [
            (path, payload)
            for path, payload in candidates
            if not is_test_or_smoke_artifact(path, payload)
        ]
        if non_test:
            candidates = non_test
    return max(candidates, key=lambda item: artifact_sort_key(item[0], item[1]))[0]


def newest_goal_completion_audit() -> Path | None:
    """Prefer the latest non-smoke system audit so test runs do not mask formal readiness."""
    candidates = list(EXPERIMENTS.glob("goal_system_completion_audit_*.json"))
    if not candidates:
        return None
    loaded: list[tuple[Path, dict[str, Any]]] = []
    for path in candidates:
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            payload = {}
        loaded.append((path, payload if isinstance(payload, dict) else {}))

    non_test = [
        (path, payload)
        for path, payload in loaded
        if not is_test_or_smoke_artifact(path, payload)
    ]
    if non_test:
        loaded = non_test
    return max(loaded, key=lambda item: artifact_sort_key(item[0], item[1]))[0]


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


def compact_next_work(items: Any, limit: int = 8) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item) for item in items[:limit]]


def load_context_from_completion_audit(completion_audit: dict[str, Any]) -> dict[str, Any]:
    inputs = completion_audit.get("inputs") or {}
    context_path = inputs.get("context_json")
    if not context_path:
        return {}
    try:
        payload = load_json(Path(str(context_path)))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_current_tactical_symbol(completion_audit: dict[str, Any]) -> str | None:
    """Resolve the current deployable tactical symbol from the latest context.

    This keeps next-step commands dynamic: SOXL can be the current result, but
    the command is not hardcoded to SOXL and can change with future holdings.
    """
    context = load_context_from_completion_audit(completion_audit)
    tactical_panel = context.get("us_tactical_performance_panel") or {}
    tactical_summary = tactical_panel.get("summary") or tactical_panel
    for component in tactical_summary.get("current_components") or []:
        if not isinstance(component, dict):
            continue
        symbol = str(component.get("symbol") or "").strip()
        if (
            symbol
            and component.get("inclusion_reason") == "current_tactical_position"
            and not symbol.startswith("USD")
        ):
            return symbol
    return None


def formal_manual_dispatch_command(current_tactical_symbol: str | None) -> str:
    base_command = default_manual_dispatch_base_command()
    if current_tactical_symbol:
        return f"{base_command} --current-tactical-symbol {current_tactical_symbol}"
    return base_command


def closure_rows(closure_package: dict[str, Any]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for item in closure_package.get("closure_items") or []:
        if not isinstance(item, dict):
            continue
        rows.append([
            item.get("priority"),
            item.get("area"),
            item.get("status") or item.get("blocker"),
            item.get("max_allowed_until_closed") or item.get("max_allowed"),
            item.get("user_action"),
        ])
    return rows


def turn_closeout_decision(
    *,
    ready_for_manual_reports: bool,
    goal_mechanism_ready: bool,
    goal_complete: bool,
    paper_due_now: int,
    paper_expiring_24h: int,
    rec_due_now: int,
    max_action: Any,
) -> dict[str, Any]:
    """Decide whether the current assistant turn should keep running.

    This is intentionally separate from the next-work queue. The queue can
    contain future evidence tasks while the current manual turn should still
    stop and report status once there is no due-now review to perform.
    """
    if not ready_for_manual_reports or not goal_mechanism_ready:
        return {
            "action": "repair_before_next_report",
            "should_continue_current_turn": True,
            "reason": "manual_dispatch_mechanism_not_ready",
            "next_modes": ["repair_readiness", "rerun_system_audit"],
            "plain_note": "手动报告机制还没通过基础就绪检查，本轮应先修复阻断项。",
        }

    if paper_due_now:
        return {
            "action": "run_short_paper_validation_pulse",
            "should_continue_current_turn": True,
            "reason": "paper_review_due_now",
            "next_modes": ["short_paper_validation_pulse"],
            "plain_note": "有模拟交易样本已经到期，可以跑短脉冲复盘；这不是长循环模拟。",
        }

    if paper_expiring_24h:
        return {
            "action": "stop_and_report_status",
            "should_continue_current_turn": False,
            "reason": "paper_review_upcoming_not_due",
            "next_modes": [
                "status_report",
                "short_paper_validation_pulse_at_due_time",
                "formal_manual_dispatch_when_user_requests_market_report",
            ],
            "max_allowed_current_action": max_action,
            "plain_note": (
                "有模拟交易样本快到期但尚未到期；本轮应汇报下一次复盘时间，"
                "不要为了未成熟样本反复空跑。"
            ),
        }

    if rec_due_now:
        return {
            "action": "run_recommendation_review_draft",
            "should_continue_current_turn": True,
            "reason": "recommendation_review_due",
            "next_modes": ["recommendation_review_draft"],
            "plain_note": "有历史建议到期，应先生成复盘草案，人工确认后才写入结果。",
        }

    if goal_complete:
        return {
            "action": "stop_and_report_goal_complete_status",
            "should_continue_current_turn": False,
            "reason": "goal_complete_true",
            "next_modes": ["status_report"],
            "plain_note": "真实财务目标已被审计标记完成，本轮应汇报状态而不是继续补规则。",
        }

    return {
        "action": "stop_and_report_status",
        "should_continue_current_turn": False,
        "reason": "mechanism_ready_no_due_now_review",
        "next_modes": [
            "status_report",
            "formal_manual_dispatch_when_user_requests_market_report",
            "deep_research_only_when_explicitly_requested",
        ],
        "max_allowed_current_action": max_action,
        "plain_note": (
            "机制已经可用，但真实目标还没完成；没有到期复盘时，本轮应收口汇报，"
            "剩余证据等下一次调度或用户明确要求深研时再处理。"
        ),
    }


def build_readiness(
    completion_audit: dict[str, Any],
    learning_calendar: dict[str, Any],
    closure_package: dict[str, Any],
    paths: dict[str, str],
) -> dict[str, Any]:
    completion_summary = completion_audit.get("summary") or {}
    learning_summary = learning_calendar.get("summary") or {}
    paper_review = learning_calendar.get("paper_review") or {}
    recommendation_review = learning_calendar.get("recommendation_review") or {}
    closure_summary = (
        closure_package.get("source_package_summary")
        or closure_package.get("source_packages")
        or {}
    )

    ready_for_manual_reports = bool(completion_summary.get("ready_for_manual_reports"))
    goal_mechanism_ready = bool(completion_summary.get("goal_mechanism_ready"))
    active_goal_execution_matrix_ready = bool(completion_summary.get("active_goal_execution_matrix_ready"))
    goal_complete = bool(completion_summary.get("goal_complete"))
    ready_for_execute_now = bool(completion_summary.get("ready_for_tactical_execute_now"))
    max_action = completion_summary.get("max_allowed_current_action") or "unknown"

    paper_due_now = int(paper_review.get("due_now_count") or 0)
    paper_expiring_24h = int(paper_review.get("expiring_24h_count") or 0)
    rec_due_now = int(recommendation_review.get("due_now_count") or 0)
    closeout = turn_closeout_decision(
        ready_for_manual_reports=ready_for_manual_reports,
        goal_mechanism_ready=goal_mechanism_ready,
        goal_complete=goal_complete,
        paper_due_now=paper_due_now,
        paper_expiring_24h=paper_expiring_24h,
        rec_due_now=rec_due_now,
        max_action=max_action,
    )

    immediate_focus: list[str] = []
    if paper_due_now or paper_expiring_24h:
        immediate_focus.append("review_nearest_paper_trade")
    if rec_due_now:
        immediate_focus.append("review_due_recommendations")
    if not immediate_focus and ready_for_manual_reports:
        immediate_focus.append("run_formal_manual_dispatch_with_fresh_data")
    if not ready_for_manual_reports:
        immediate_focus.append("repair_manual_dispatch_before_next_report")

    execute_now_preblocked_reasons: list[str] = []
    if not ready_for_execute_now:
        execute_now_preblocked_reasons.append("tactical_execute_now_not_ready")
    if max_action not in {"execute_now", "execute_now_candidate"}:
        execute_now_preblocked_reasons.append(f"max_action_is_{max_action}")
    if int(learning_summary.get("usable_calibration_needed", {}).get("paper", 0) or 0) > 0:
        execute_now_preblocked_reasons.append("paper_usable_samples_still_needed")
    if int(learning_summary.get("usable_calibration_needed", {}).get("recommendation", 0) or 0) > 0:
        execute_now_preblocked_reasons.append("recommendation_calibration_samples_still_needed")

    current_tactical_symbol = resolve_current_tactical_symbol(completion_audit)
    commands = [
        {
            "mode": "formal_manual_dispatch",
            "command": formal_manual_dispatch_command(current_tactical_symbol),
            "when": (
                "需要正式今日/周度判断时使用；不要加 --smoke。"
                + (
                    f" 当前动态战术仓已从最新上下文识别为 {current_tactical_symbol}。"
                    if current_tactical_symbol
                    else " 当前战术仓未能从上下文确认，运行时会重新由持仓流程识别。"
                )
            ),
        },
        {
            "mode": "paper_review_dry_run",
            "command": (
                "python3 manual-investment-strategy-operator/scripts/due_learning_review_pulse.py "
                "--timeout-seconds 180 --format json"
            ),
            "when": "paper 样本到期检查时使用；未到期会自动跳过，到期才触发一次短脉冲复盘。",
        },
        {
            "mode": "recommendation_review",
            "command": (
                "python3 manual-investment-strategy-operator/scripts/recommendation_outcome_reviewer.py "
                "--format json"
            ),
            "when": "recommendation 到期后生成结果复盘草案。",
        },
    ]

    return {
        "generated_at": utc_now(),
        "status": "ok",
        "read_only": True,
        "live_orders_enabled": False,
        "mutates_ledgers": False,
        "paths": paths,
        "summary": {
            "ready_for_next_manual_dispatch": ready_for_manual_reports and goal_mechanism_ready,
            "goal_mechanism_ready": goal_mechanism_ready,
            "active_goal_execution_matrix_ready": active_goal_execution_matrix_ready,
            "goal_complete": goal_complete,
            "ready_for_tactical_execute_now": ready_for_execute_now,
            "max_allowed_current_action": max_action,
            "current_tactical_symbol": current_tactical_symbol,
            "current_tactical_symbol_source": (
                "latest_context.us_tactical_performance_panel.current_components"
                if current_tactical_symbol
                else "not_resolved"
            ),
            "immediate_focus": immediate_focus,
            "execute_now_preblocked_reasons": execute_now_preblocked_reasons,
            "next_paper_review_at": learning_summary.get("next_paper_review_at"),
            "next_recommendation_review_at": learning_summary.get("next_recommendation_review_at"),
            "paper_closed_count": paper_review.get("closed_count"),
            "paper_closed_needed": paper_review.get("closed_needed"),
            "recommendation_resolved_count": recommendation_review.get("resolved_count"),
            "recommendation_early_resolved_needed": recommendation_review.get("early_calibration_needed"),
            "recommendation_resolved_needed": recommendation_review.get("resolved_needed"),
            "portfolio_evidence_gap_count": closure_summary.get("portfolio_evidence_gap_count"),
            "portfolio_blocking_gap_count": closure_summary.get("portfolio_blocking_gap_count"),
            "recommended_current_turn_action": closeout.get("action"),
            "should_continue_current_turn": closeout.get("should_continue_current_turn"),
            "current_turn_closeout_reason": closeout.get("reason"),
            "current_turn_next_modes": closeout.get("next_modes"),
            "current_turn_plain_note": closeout.get("plain_note"),
        },
        "current_turn_closeout": closeout,
        "next_required_work": compact_next_work(completion_summary.get("next_required_work")),
        "closure_items": closure_package.get("closure_items") or [],
        "commands": commands,
        "plain_notes": [
            "`ready_for_next_manual_dispatch=true` 只代表下一次可以正式跑报告，不代表可以买入。",
            "`execute_now` 仍被样本、现金通道和双80门槛阻断时，只能输出观察、模拟或条件动作。",
            "`goal_complete=false` 代表真实 5年/10年 10x 尚未被结果证明。",
            "`stop_and_report_status` 代表本轮应该收口汇报，不代表系统以后不继续学习。",
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    command_rows = [
        [item["mode"], item["command"], item["when"]]
        for item in payload.get("commands") or []
    ]
    next_work_rows = [[idx + 1, item] for idx, item in enumerate(payload.get("next_required_work") or [])]

    parts = [
        f"# Next Dispatch Readiness | {payload['generated_at']}",
        "",
        "本报告是下一次手动调度前的只读检查。它不抓新行情、不自动下单、不修改账本。",
        "",
        "## Summary",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["ready_for_next_manual_dispatch", summary.get("ready_for_next_manual_dispatch")],
                ["goal_mechanism_ready", summary.get("goal_mechanism_ready")],
                ["active_goal_execution_matrix_ready", summary.get("active_goal_execution_matrix_ready")],
                ["goal_complete", summary.get("goal_complete")],
                ["ready_for_tactical_execute_now", summary.get("ready_for_tactical_execute_now")],
                ["max_allowed_current_action", summary.get("max_allowed_current_action")],
                ["immediate_focus", ", ".join(summary.get("immediate_focus") or [])],
                ["next_paper_review_at", summary.get("next_paper_review_at")],
                ["next_recommendation_review_at", summary.get("next_recommendation_review_at")],
                ["paper_closed_needed", summary.get("paper_closed_needed")],
                ["recommendation_early_resolved_needed", summary.get("recommendation_early_resolved_needed")],
                ["recommendation_resolved_needed", summary.get("recommendation_resolved_needed")],
                ["portfolio_blocking_gap_count", summary.get("portfolio_blocking_gap_count")],
                ["recommended_current_turn_action", summary.get("recommended_current_turn_action")],
                ["should_continue_current_turn", summary.get("should_continue_current_turn")],
                ["current_turn_closeout_reason", summary.get("current_turn_closeout_reason")],
            ],
        ),
        "",
        "## Current Turn Closeout",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["action", summary.get("recommended_current_turn_action")],
                ["should_continue_current_turn", summary.get("should_continue_current_turn")],
                ["reason", summary.get("current_turn_closeout_reason")],
                ["next_modes", ", ".join(summary.get("current_turn_next_modes") or [])],
                ["plain_note", summary.get("current_turn_plain_note")],
            ],
        ),
        "",
        "## Why Execute Now Is Still Blocked",
        "",
        markdown_table(
            ["Reason"],
            [[reason] for reason in summary.get("execute_now_preblocked_reasons") or ["none"]],
        ),
        "",
        "## Next Required Work",
        "",
        markdown_table(["#", "Work"], next_work_rows or [["", "No next work found in completion audit."]]),
        "",
        "## Evidence Closure Items",
        "",
        markdown_table(
            ["Priority", "Area", "Status", "Max Allowed", "User Action"],
            closure_rows({"closure_items": payload.get("closure_items")}) or [["", "", "", "", "No closure items found."]],
        ),
        "",
        "## Commands",
        "",
        markdown_table(["Mode", "Command", "When"], command_rows),
        "",
        "## 术语小注",
        "",
        "- `readiness`：调度前可用性检查，不是收益预测。",
        "- `execute_now_preblocked`：当前仍有前置门槛没过，所以不能写成立即执行。",
        "- `paper_closed_needed`：还需要关闭多少模拟交易样本，才能更认真校准短线策略。",
        "- `recommendation_resolved_needed`：还需要多少真实建议结果，才能校准所谓概率。",
    ]
    return "\n".join(parts) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--completion-audit-json", type=Path)
    parser.add_argument("--learning-calendar-json", type=Path)
    parser.add_argument("--closure-package-json", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    completion_path = args.completion_audit_json or newest_goal_completion_audit()
    learning_path = args.learning_calendar_json or newest("learning_review_calendar_*.json", prefer_non_test=True)
    closure_path = args.closure_package_json or newest("goal_evidence_closure_package_*.json", prefer_non_test=True)

    missing = [
        name
        for name, path in [
            ("completion_audit_json", completion_path),
            ("learning_calendar_json", learning_path),
            ("closure_package_json", closure_path),
        ]
        if path is None or not Path(path).exists()
    ]
    if missing:
        raise SystemExit(f"Missing required inputs: {', '.join(missing)}")

    paths = {
        "completion_audit_json": str(completion_path),
        "learning_calendar_json": str(learning_path),
        "closure_package_json": str(closure_path),
        "selection_policy": "prefer_latest_non_smoke_artifacts; explicit CLI paths override defaults",
    }
    payload = build_readiness(
        load_json(completion_path),
        load_json(learning_path),
        load_json(closure_path),
        paths,
    )

    output_json = args.output_json or EXPERIMENTS / "next_dispatch_readiness_latest.json"
    output_md = args.output_md or REPORTS / "next_dispatch_readiness_latest.md"
    markdown = render_markdown(payload)
    payload["written_outputs"] = safe_write_outputs(output_json, output_md, payload, markdown)
    write_json(Path(payload["written_outputs"]["json"]), payload)

    if args.format == "markdown":
        print(markdown, end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
