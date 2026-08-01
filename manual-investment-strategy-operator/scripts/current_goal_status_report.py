#!/usr/bin/env python3
"""Generate a concise current-status report for the 10x goal system.

This is the user-facing companion to `stop_and_report_status`. It is read-only:
it does not fetch market data, mutate ledgers, place orders, move cash, or
authorize trades.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from audit_warning_explainer import GENERIC_TERM_NOTES, build_warning_explanations


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
EXPERIMENTS = MANUAL_ROOT / "experiments"
REPORTS = MANUAL_ROOT / "reports"
SHANGHAI = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")


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


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def display_time(value: Any) -> str:
    parsed = parse_time(value)
    if not parsed:
        return str(value or "")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(SHANGHAI).replace(microsecond=0).isoformat()


def hours_until(value: Any, now: dt.datetime | None = None) -> float | None:
    parsed = parse_time(value)
    if not parsed:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    current = now or dt.datetime.now(dt.timezone.utc)
    return round((parsed - current).total_seconds() / 3600, 2)


def review_state(value: Any, now: dt.datetime | None = None) -> str:
    remaining = hours_until(value, now)
    if remaining is None:
        return "missing"
    if remaining <= 0:
        return "due_now"
    if remaining <= 24:
        return "due_within_24h"
    if remaining <= 72:
        return "upcoming_72h"
    return "scheduled"


def artifact_key(path: Path) -> tuple[dt.datetime, float, str]:
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError):
        payload = {}
    generated_at = parse_time(payload.get("generated_at") if isinstance(payload, dict) else None)
    return (
        generated_at or dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc),
        path.stat().st_mtime,
        path.name,
    )


def newest(pattern: str) -> Path | None:
    candidates = list(EXPERIMENTS.glob(pattern))
    if not candidates:
        return None
    return max(candidates, key=artifact_key)


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return ""
        return str(value).replace("\n", " ").replace("|", "\\|")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(cell(item) for item in row) + " |" for row in rows)
    return "\n".join(lines)


def compact_list(items: Any, limit: int = 6) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item) for item in items[:limit]]


def command_by_mode(readiness: dict[str, Any], mode: str) -> str | None:
    for entry in readiness.get("commands") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("mode") == mode and entry.get("command"):
            return str(entry["command"])
    return None


def load_optional(path: str | None, label: str) -> tuple[dict[str, Any], str | None]:
    if not path:
        return {}, f"{label}:missing_path"
    candidate = Path(path)
    if not candidate.exists():
        return {}, f"{label}:not_found:{candidate}"
    try:
        payload = load_json(candidate)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"{label}:unreadable:{exc}"
    if not isinstance(payload, dict):
        return {}, f"{label}:not_object"
    return payload, None


def build_status(
    completion: dict[str, Any],
    readiness: dict[str, Any],
    queue: dict[str, Any],
    evidence_blockers: dict[str, Any],
    paths: dict[str, str],
) -> dict[str, Any]:
    completion_summary = completion.get("summary") or {}
    readiness_summary = readiness.get("summary") or {}
    queue_summary = queue.get("summary") or {}
    evidence_summary = evidence_blockers.get("summary") or {}
    tasks = queue.get("tasks") or []
    p0_tasks = [item for item in tasks if isinstance(item, dict) and item.get("priority") == "P0"]
    blocking_tasks = [
        item for item in tasks if isinstance(item, dict) and item.get("blocks_execute_now") is True
    ]
    report_path = ((completion.get("inputs") or {}).get("report_md"))
    warning_explanations = build_warning_explanations(completion)
    next_paper_review_at = readiness_summary.get("next_paper_review_at") or queue_summary.get("next_paper_review_at")
    next_recommendation_review_at = (
        readiness_summary.get("next_recommendation_review_at")
        or queue_summary.get("next_recommendation_review_at")
    )
    now = dt.datetime.now(dt.timezone.utc)
    next_paper_hours = hours_until(next_paper_review_at, now)
    next_paper_state = review_state(next_paper_review_at, now)
    next_rec_hours = hours_until(next_recommendation_review_at, now)
    next_rec_state = review_state(next_recommendation_review_at, now)
    formal_manual_dispatch_command = command_by_mode(readiness, "formal_manual_dispatch")
    should_stop_open_loop = evidence_summary.get("should_stop_open_loop_research")
    evidence_next_mode = evidence_summary.get("recommended_next_mode")
    evidence_reason = ""
    if should_stop_open_loop is True:
        evidence_reason = (
            "证据阻断分类显示，多数剩余缺口需要 API/账户证据或等待样本到期，"
            "继续公开网页长循环的边际价值很低。"
        )
    return {
        "generated_at": utc_now(),
        "status": "ok",
        "read_only": True,
        "live_orders_enabled": False,
        "mutates_ledgers": False,
        "does_not_authorize_trades": True,
        "paths": paths | {"latest_report_md": report_path},
        "summary": {
            "goal_mechanism_ready": bool(completion_summary.get("goal_mechanism_ready")),
            "active_goal_execution_matrix_ready": bool(completion_summary.get("active_goal_execution_matrix_ready")),
            "goal_complete": bool(completion_summary.get("goal_complete")),
            "ready_for_next_manual_dispatch": bool(readiness_summary.get("ready_for_next_manual_dispatch")),
            "ready_for_tactical_execute_now": bool(readiness_summary.get("ready_for_tactical_execute_now")),
            "max_allowed_current_action": readiness_summary.get("max_allowed_current_action")
            or completion_summary.get("max_allowed_current_action"),
            "current_tactical_symbol": readiness_summary.get("current_tactical_symbol")
            or queue_summary.get("current_tactical_symbol"),
            "current_tactical_symbol_source": readiness_summary.get("current_tactical_symbol_source")
            or queue_summary.get("current_tactical_symbol_source"),
            "formal_manual_dispatch_command": formal_manual_dispatch_command,
            "recommended_current_turn_action": readiness_summary.get("recommended_current_turn_action")
            or queue_summary.get("recommended_current_turn_action"),
            "should_continue_current_turn": bool(readiness_summary.get("should_continue_current_turn")),
            "current_turn_closeout_reason": readiness_summary.get("current_turn_closeout_reason")
            or queue_summary.get("current_turn_closeout_reason"),
            "next_paper_review_at": next_paper_review_at,
            "next_paper_review_at_shanghai": display_time(next_paper_review_at),
            "next_paper_review_state": next_paper_state,
            "hours_until_next_paper_review": next_paper_hours,
            "next_recommendation_review_at": next_recommendation_review_at,
            "next_recommendation_review_at_shanghai": display_time(next_recommendation_review_at),
            "next_recommendation_review_state": next_rec_state,
            "hours_until_next_recommendation_review": next_rec_hours,
            "paper_closed_count": readiness_summary.get("paper_closed_count"),
            "recommendation_resolved_count": readiness_summary.get("recommendation_resolved_count"),
            "pass_count": completion_summary.get("pass_count"),
            "warning_count": completion_summary.get("warning_count"),
            "error_count": completion_summary.get("error_count"),
            "p0_open_count": queue_summary.get("p0_open_count"),
            "execute_now_blocking_task_count": len(blocking_tasks),
            "evidence_blocker_status": evidence_blockers.get("status"),
            "evidence_blocker_next_mode": evidence_next_mode,
            "evidence_should_stop_open_loop_research": should_stop_open_loop,
            "evidence_retryable_public_count": evidence_summary.get("retryable_public_count"),
            "evidence_paid_or_api_needed_count": evidence_summary.get("paid_or_entitled_api_needed_count"),
            "evidence_account_or_broker_needed_count": evidence_summary.get(
                "user_account_or_broker_evidence_needed_count"
            ),
            "evidence_time_gated_needed_count": evidence_summary.get("time_gated_learning_outcome_needed_count"),
            "evidence_public_blocked_count": evidence_summary.get(
                "public_blocked_or_not_machine_readable_count"
            ),
        },
        "plain_answer": {
            "what_is_done": (
                "手动调度机制已能生成报告、写入建议记录、运行审计、输出学习复盘日历和下一步队列。"
            ),
            "what_is_not_done": (
                "真实 5年/10年 10x 财务结果尚未发生；短线 execute_now 仍缺 paper 样本、推荐结果校准和部分持仓/现金证据。"
            ),
            "why_stop_now": (
                "当前没有 due-now 复盘；继续运行主要是在等待市场时间或做开放式研究，所以应收口汇报。"
                + (f" {evidence_reason}" if evidence_reason else "")
            ),
            "next_useful_trigger": (
                "等最近 paper 样本到期后跑短脉冲复盘，或用户要求 fresh 市场/持仓报告时再跑正式调度。"
            ),
            "current_tactical_note": (
                "当前战术仓是系统本轮识别出的美股短线资金来源或比较基准，不是写死标的；未来持仓变化后会重新识别。"
            ),
        },
        "top_next_tasks": [
            {
                "task_id": item.get("task_id"),
                "priority": item.get("priority"),
                "area": item.get("area"),
                "action": item.get("action"),
                "max_allowed_until_done": item.get("max_allowed_until_done"),
            }
            for item in p0_tasks[:5]
            if isinstance(item, dict)
        ],
        "execute_now_blockers": compact_list(readiness_summary.get("execute_now_preblocked_reasons"), 8),
        "next_required_work": compact_list(completion_summary.get("next_required_work"), 8),
        "evidence_blocker_classification": {
            "available": bool(evidence_blockers),
            "summary": evidence_summary,
            "category_summary": evidence_blockers.get("category_summary") or [],
            "output_report": paths.get("evidence_blocker_json"),
        },
        "plain_warning_explanations": warning_explanations,
        "terms_note": {
            **GENERIC_TERM_NOTES,
            "goal_mechanism_ready": "机制可用，不代表收益目标完成。",
            "active_goal_execution_matrix_ready": "当前原始目标已经绑定到本地执行矩阵和审计检查里。",
            "goal_complete": "真实财务结果达标；当前仍为 false。",
            "stop_and_report_status": "本轮到检查点，应汇报状态，不继续长循环。",
            "current_tactical_symbol": "当前美股战术仓；它是本轮可作为短线资金来源或比较基准的持仓识别结果，不是固定写死的票。",
            "evidence_blocker": "证据阻断项，说明结论升级缺的是什么证据，以及它能否靠继续公开搜索解决。",
            "open_loop_research": "开放式长循环研究，指没有明确到期样本或新证据，却不断重复搜索和运行。",
        },
    }


def render_markdown(status: dict[str, Any]) -> str:
    summary = status.get("summary") or {}
    plain = status.get("plain_answer") or {}
    evidence = status.get("evidence_blocker_classification") or {}
    evidence_summary = evidence.get("summary") or {}
    rows = [
        ["机制可用", summary.get("goal_mechanism_ready")],
        ["目标执行矩阵生效", summary.get("active_goal_execution_matrix_ready")],
        ["目标完成", summary.get("goal_complete")],
        ["下一次可跑正式报告", summary.get("ready_for_next_manual_dispatch")],
        ["可真实立即执行", summary.get("ready_for_tactical_execute_now")],
        ["最大当前动作", summary.get("max_allowed_current_action")],
        ["当前美股战术仓", summary.get("current_tactical_symbol")],
        ["战术仓识别来源", summary.get("current_tactical_symbol_source")],
        ["正式调度命令", summary.get("formal_manual_dispatch_command")],
        ["审计通过/警告/错误", f"{summary.get('pass_count')} / {summary.get('warning_count')} / {summary.get('error_count')}"],
        ["本轮建议动作", summary.get("recommended_current_turn_action")],
        ["是否继续本轮", summary.get("should_continue_current_turn")],
        ["收口原因", summary.get("current_turn_closeout_reason")],
        ["下一次 paper 复盘", summary.get("next_paper_review_at")],
        ["下一次 paper 复盘（上海）", summary.get("next_paper_review_at_shanghai")],
        ["paper 复盘状态", summary.get("next_paper_review_state")],
        ["距离 paper 复盘（小时）", summary.get("hours_until_next_paper_review")],
        ["下一次 recommendation 复盘", summary.get("next_recommendation_review_at")],
        ["下一次 recommendation 复盘（上海）", summary.get("next_recommendation_review_at_shanghai")],
        ["recommendation 复盘状态", summary.get("next_recommendation_review_state")],
        ["距离 recommendation 复盘（小时）", summary.get("hours_until_next_recommendation_review")],
        ["证据阻断分类状态", summary.get("evidence_blocker_status")],
        ["证据阻断下一步模式", summary.get("evidence_blocker_next_mode")],
        ["建议停止开放式研究", summary.get("evidence_should_stop_open_loop_research")],
    ]
    task_rows = [
        [
            item.get("priority"),
            item.get("area"),
            item.get("task_id"),
            item.get("action"),
            item.get("max_allowed_until_done"),
        ]
        for item in status.get("top_next_tasks") or []
    ]
    blockers = status.get("execute_now_blockers") or []
    required = status.get("next_required_work") or []
    warning_rows = [
        [
            item.get("area"),
            item.get("requirement_id"),
            item.get("plain_meaning"),
            item.get("who_can_fix"),
            item.get("action_limit"),
        ]
        for item in status.get("plain_warning_explanations") or []
    ]
    evidence_rows = [
        ["可限时重试公开数据", evidence_summary.get("retryable_public_count"), "下次正式调度内重试一次，失败就降级"],
        ["公开源受阻/不可机器校验", evidence_summary.get("public_blocked_or_not_machine_readable_count"), "不要反复空跑，按降级处理"],
        ["需要付费/API 权限", evidence_summary.get("paid_or_entitled_api_needed_count"), "只有提供 key 或订阅后再补"],
        ["需要账户/券商/钱包证据", evidence_summary.get("user_account_or_broker_evidence_needed_count"), "需要用户截图或导出"],
        ["需要等待样本到期/复盘", evidence_summary.get("time_gated_learning_outcome_needed_count"), "到点跑短复盘，不提前长跑"],
        ["安全/职责边界限制", evidence_summary.get("safety_or_scope_cap_count"), "不是数据问题，必须保持人工确认和动作降级"],
    ]
    return "\n\n".join([
        "# Current Goal Status Report",
        "这是一份只读状态汇报，不抓新行情、不下单、不转账、不修改持仓账本。",
        "## 一页状态",
        markdown_table(["项目", "值"], rows),
        "## 直白结论",
        "\n".join([
            f"- 已完成：{plain.get('what_is_done')}",
            f"- 未完成：{plain.get('what_is_not_done')}",
            f"- 为什么现在收口：{plain.get('why_stop_now')}",
            f"- 下一次有用触发：{plain.get('next_useful_trigger')}",
            f"- 当前战术仓备注：{plain.get('current_tactical_note')}",
        ]),
        "## P0 下一步",
        markdown_table(["优先级", "领域", "任务", "动作", "完成前最大动作"], task_rows) if task_rows else "- 暂无 P0 任务。",
        "## Execute Now 阻断",
        "\n".join(f"- `{item}`" for item in blockers) if blockers else "- 暂无记录。",
        "## 为什么不继续空跑",
        markdown_table(["类型", "数量", "处理方式"], evidence_rows) if evidence.get("available") else "- 暂无证据阻断分类报告。",
        "## 剩余 Warning 怎么理解",
        markdown_table(["领域", "检查项", "直白含义", "谁能解决", "动作限制"], warning_rows) if warning_rows else "- 暂无 warning。",
        "## 下一步证据工作",
        "\n".join(f"- {item}" for item in required) if required else "- 暂无记录。",
        "## 术语小注",
        "\n".join(f"- `{key}`：{value}" for key, value in (status.get("terms_note") or {}).items()),
    ]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a read-only current goal status report")
    parser.add_argument("--completion-audit-json")
    parser.add_argument("--readiness-json")
    parser.add_argument("--queue-json")
    parser.add_argument("--evidence-blocker-json")
    parser.add_argument("--output-json")
    parser.add_argument("--output-md")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    completion_path = Path(args.completion_audit_json) if args.completion_audit_json else newest("goal_system_completion_audit_*.json")
    readiness_path = Path(args.readiness_json) if args.readiness_json else newest("next_dispatch_readiness_*.json")
    queue_path = Path(args.queue_json) if args.queue_json else newest("next_goal_execution_queue_*.json")
    evidence_path = (
        Path(args.evidence_blocker_json)
        if args.evidence_blocker_json
        else newest("evidence_blocker_classification_*.json")
    )

    errors: list[str] = []
    completion, error = load_optional(str(completion_path) if completion_path else None, "completion")
    if error:
        errors.append(error)
    readiness, error = load_optional(str(readiness_path) if readiness_path else None, "readiness")
    if error:
        errors.append(error)
    queue, error = load_optional(str(queue_path) if queue_path else None, "queue")
    if error:
        errors.append(error)
    evidence_blockers: dict[str, Any] = {}
    evidence_error: str | None = None
    if evidence_path:
        evidence_blockers, evidence_error = load_optional(str(evidence_path), "evidence_blocker")

    if errors:
        payload = {
            "generated_at": utc_now(),
            "status": "failed",
            "read_only": True,
            "live_orders_enabled": False,
            "mutates_ledgers": False,
            "errors": errors,
        }
    else:
        payload = build_status(
            completion,
            readiness,
            queue,
            evidence_blockers,
            {
                "completion_audit_json": str(completion_path),
                "readiness_json": str(readiness_path),
                "queue_json": str(queue_path),
                "evidence_blocker_json": str(evidence_path) if evidence_path else "",
                "evidence_blocker_error": evidence_error or "",
            },
        )

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_json = Path(args.output_json) if args.output_json else EXPERIMENTS / f"current_goal_status_{stamp}.json"
    output_md = Path(args.output_md) if args.output_md else REPORTS / f"current_goal_status_{stamp}.md"
    markdown = render_markdown(payload) if payload.get("status") == "ok" else "# Current Goal Status Report\n\n状态生成失败。\n"
    write_json(output_json, payload)
    write_text(output_md, markdown)
    payload["written_outputs"] = {"json": str(output_json), "markdown": str(output_md)}

    if args.format == "markdown":
        print(markdown)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
