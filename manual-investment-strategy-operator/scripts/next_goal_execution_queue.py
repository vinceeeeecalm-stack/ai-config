#!/usr/bin/env python3
"""Build the next actionable queue for the 10x goal system.

This read-only queue sits one level above readiness. It turns the latest
readiness preflight, learning review calendar, and closure package into a
short operational queue for the next manual dispatch. It does not fetch market
data, mutate ledgers, place orders, move cash, or authorize trades.
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
SAFE_PAPER_VALIDATION_PULSE_COMMAND = (
    "python3 manual-investment-strategy-operator/scripts/due_learning_review_pulse.py "
    "--timeout-seconds 180 --format json"
)


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


def parse_generated_at(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_test_or_smoke_artifact(path: Path, payload: dict[str, Any] | None = None) -> bool:
    text = path.name.lower()
    if "smoke" in text:
        return True
    run_id = str((payload or {}).get("run_id") or "").lower()
    return "smoke" in run_id


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

    def key(item: tuple[Path, dict[str, Any]]) -> tuple[dt.datetime, float, str]:
        path, payload = item
        generated_at = parse_generated_at(payload.get("generated_at"))
        return (
            generated_at or dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc),
            path.stat().st_mtime,
            path.name,
        )

    return max(candidates, key=key)[0]

def priority_rank(priority: str | None) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(str(priority or "P3"), 3)


def parse_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def task(
    task_id: str,
    priority: str,
    area: str,
    action: str,
    why_it_matters: str,
    source: str,
    blocks_execute_now: bool,
    max_allowed_until_done: str,
    commands: list[str] | None = None,
    human_required: bool = True,
    earliest_at: str | None = None,
    due_at: str | None = None,
    related_assets: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "priority": priority,
        "area": area,
        "action": action,
        "why_it_matters": why_it_matters,
        "source": source,
        "blocks_execute_now": blocks_execute_now,
        "max_allowed_until_done": max_allowed_until_done,
        "human_required": human_required,
        "earliest_at": earliest_at,
        "due_at": due_at,
        "related_assets": related_assets or [],
        "commands": commands or [],
    }


def normalize_commands(commands: list[str]) -> list[str]:
    normalized: list[str] = []
    for command in commands:
        if "sunday_crypto_realistic_paper_loop.py" in command or "validation_progress_runner.py" in command:
            replacement = SAFE_PAPER_VALIDATION_PULSE_COMMAND
            if replacement not in normalized:
                normalized.append(replacement)
            continue
        normalized.append(command)
    return normalized


def formal_manual_dispatch_command(readiness: dict[str, Any]) -> str:
    for entry in readiness.get("commands") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("mode") == "formal_manual_dispatch" and entry.get("command"):
            return str(entry["command"])

    summary = readiness.get("summary") or {}
    current_tactical_symbol = str(summary.get("current_tactical_symbol") or "").strip()
    base_command = default_manual_dispatch_base_command()
    if current_tactical_symbol:
        return f"{base_command} --current-tactical-symbol {current_tactical_symbol}"
    return base_command


def from_closure_item(item: dict[str, Any]) -> dict[str, Any]:
    blocker_id = str(item.get("blocker_id") or "closure_item")
    area = str(item.get("area") or "证据缺口")
    priority = str(item.get("priority") or "P1")
    return task(
        task_id=f"closure_{blocker_id}",
        priority=priority,
        area=area,
        action=str(item.get("user_action") or "补齐该缺口的证据。"),
        why_it_matters=str(item.get("why_it_matters") or "该缺口会影响动作等级或复盘质量。"),
        source=str(item.get("source") or "goal_evidence_closure_package"),
        blocks_execute_now=priority == "P0",
        max_allowed_until_done=str(item.get("max_allowed_until_closed") or "watch"),
        commands=normalize_commands([str(cmd) for cmd in item.get("automation_commands") or []]),
        human_required=True,
    )


def build_queue(
    readiness: dict[str, Any],
    learning_calendar: dict[str, Any],
    closure_package: dict[str, Any],
    evidence_blockers: dict[str, Any],
    paths: dict[str, str],
) -> dict[str, Any]:
    readiness_summary = readiness.get("summary") or {}
    learning_summary = learning_calendar.get("summary") or {}
    evidence_summary = evidence_blockers.get("summary") or {}
    paper = learning_calendar.get("paper_review") or {}
    recommendation = learning_calendar.get("recommendation_review") or {}
    current_turn_closeout = readiness.get("current_turn_closeout") or {}
    queue: list[dict[str, Any]] = []

    if evidence_summary.get("should_stop_open_loop_research") is True:
        queue.append(
            task(
                task_id="stop_open_loop_research_until_due_or_evidence",
                priority="P0",
                area="运行收口",
                action=(
                    "不要继续开放式公开搜索长循环；等 paper 样本到期、用户补账户/券商/钱包证据，"
                    "或提供付费/API 数据源后再推进对应缺口。"
                ),
                why_it_matters=(
                    "证据阻断分类显示，剩余缺口主要不是公开网页能解决的问题；继续空跑不会提高 "
                    "5-10 年 DCA 目标或美股战术胜率。"
                ),
                source="evidence_blocker_classification",
                blocks_execute_now=True,
                max_allowed_until_done=str(evidence_summary.get("max_current_action") or "watch"),
                human_required=False,
                commands=[
                    "python3 manual-investment-strategy-operator/scripts/current_goal_status_report.py --format json"
                ],
            )
        )

    if current_turn_closeout.get("action") == "stop_and_report_status":
        queue.append(
            task(
                task_id="current_turn_stop_and_report_status",
                priority="P0",
                area="本轮收口",
                action="停止继续追证据或长循环，把当前机制状态、剩余缺口和下一次触发条件汇报给用户。",
                why_it_matters="手动 skill 是被动调度程序；机制已可用且无到期复盘时，应及时收口，避免把长期证据队列误当成本轮必须完成的任务。",
                source="next_dispatch_readiness.current_turn_closeout",
                blocks_execute_now=False,
                max_allowed_until_done=str(readiness_summary.get("max_allowed_current_action") or "paper_only"),
                human_required=False,
                commands=[
                    "python3 manual-investment-strategy-operator/scripts/current_goal_status_report.py --format json"
                ],
            )
        )

    next_paper_at = learning_summary.get("next_paper_review_at")
    if int(paper.get("due_now_count") or 0) > 0 or int(paper.get("expiring_24h_count") or 0) > 0:
        queue.append(
            task(
                task_id="review_nearest_paper_trade",
                priority="P0",
                area="短线策略模拟复盘",
                action="在最近 paper 样本到期时运行 dry-run 复盘，记录 hit/failed/not_triggered，而不是提前升级真实交易。",
                why_it_matters="短线策略要从 paper 样本学习，只有足够样本闭环后，80% 真实概率才有校准基础。",
                source="learning_review_calendar.paper_review",
                blocks_execute_now=True,
                max_allowed_until_done="paper_only",
                earliest_at=next_paper_at,
                due_at=next_paper_at,
                related_assets=[item.get("symbol") for item in (paper.get("queue") or [])[:3] if item.get("symbol")],
                commands=[SAFE_PAPER_VALIDATION_PULSE_COMMAND],
            )
        )

    next_rec_at = learning_summary.get("next_recommendation_review_at")
    if int(recommendation.get("due_now_count") or 0) > 0:
        queue.append(
            task(
                task_id="review_due_recommendations",
                priority="P0",
                area="推荐结果校准",
                action="复盘已到期 recommendation，人工确认结果后再写入 hit/failed/not_triggered/expired/invalidated。",
                why_it_matters="没有足够已验证结果，就不能把候选写成 80% 真实预测概率。",
                source="learning_review_calendar.recommendation_review",
                blocks_execute_now=True,
                max_allowed_until_done="paper_only_or_conditional_action",
                due_at=next_rec_at,
                commands=[
                    "python3 manual-investment-strategy-operator/scripts/recommendation_outcome_reviewer.py --format json"
                ],
            )
        )
    elif next_rec_at:
        queue.append(
            task(
                task_id="prepare_next_recommendation_review",
                priority="P2",
                area="推荐结果校准",
                action="等待下一批 recommendation 到期；到期前只准备复盘，不把 pending 建议提前算作命中。",
                why_it_matters="推荐学习必须依靠到期后的真实结果，不能提前用主观判断补样本。",
                source="learning_review_calendar.recommendation_review",
                blocks_execute_now=False,
                max_allowed_until_done="watch",
                due_at=next_rec_at,
                commands=[
                    "python3 manual-investment-strategy-operator/scripts/recommendation_outcome_reviewer.py --format json"
                ],
            )
        )

    for entry in closure_package.get("closure_items") or readiness.get("closure_items") or []:
        if isinstance(entry, dict):
            queue.append(from_closure_item(entry))

    if readiness_summary.get("ready_for_next_manual_dispatch") is True:
        queue.append(
            task(
                task_id="run_next_formal_manual_dispatch",
                priority="P1",
                area="正式手动调度",
                action="下一次需要投资判断时，运行非 smoke 手动调度，让系统重新抓取市场数据、情绪、宏观、链上和美股候选。",
                why_it_matters="目标系统依靠每次调度主动取数和复盘，不依靠旧结论延续。",
                source="next_dispatch_readiness",
                blocks_execute_now=False,
                max_allowed_until_done=str(readiness_summary.get("max_allowed_current_action") or "paper_only"),
                commands=[formal_manual_dispatch_command(readiness)],
            )
        )

    unique: dict[str, dict[str, Any]] = {}
    for entry in queue:
        existing = unique.get(entry["task_id"])
        if existing is None or priority_rank(entry["priority"]) < priority_rank(existing["priority"]):
            unique[entry["task_id"]] = entry

    ordered = sorted(
        unique.values(),
        key=lambda entry: (
            priority_rank(entry.get("priority")),
            parse_time(entry.get("due_at") or entry.get("earliest_at")) or dt.datetime.max.replace(tzinfo=dt.timezone.utc),
            entry.get("task_id") or "",
        ),
    )
    p0_open = sum(1 for item in ordered if item.get("priority") == "P0")
    blocks_execute_now = [item["task_id"] for item in ordered if item.get("blocks_execute_now")]

    return {
        "generated_at": utc_now(),
        "status": "ok",
        "queue_version": "next-goal-execution-queue-v1",
        "read_only": True,
        "live_orders_enabled": False,
        "mutates_ledgers": False,
        "paths": paths,
        "summary": {
            "goal_mechanism_ready": readiness_summary.get("goal_mechanism_ready"),
            "active_goal_execution_matrix_ready": readiness_summary.get("active_goal_execution_matrix_ready"),
            "goal_complete": readiness_summary.get("goal_complete"),
            "ready_for_next_manual_dispatch": readiness_summary.get("ready_for_next_manual_dispatch"),
            "ready_for_tactical_execute_now": readiness_summary.get("ready_for_tactical_execute_now"),
            "max_allowed_current_action": readiness_summary.get("max_allowed_current_action"),
            "current_tactical_symbol": readiness_summary.get("current_tactical_symbol"),
            "current_tactical_symbol_source": readiness_summary.get("current_tactical_symbol_source"),
            "task_count": len(ordered),
            "p0_open_count": p0_open,
            "execute_now_blocking_task_ids": blocks_execute_now,
            "next_paper_review_at": readiness_summary.get("next_paper_review_at"),
            "next_recommendation_review_at": readiness_summary.get("next_recommendation_review_at"),
            "recommended_current_turn_action": readiness_summary.get("recommended_current_turn_action")
            or current_turn_closeout.get("action"),
            "should_continue_current_turn": readiness_summary.get("should_continue_current_turn")
            if "should_continue_current_turn" in readiness_summary
            else current_turn_closeout.get("should_continue_current_turn"),
            "current_turn_closeout_reason": readiness_summary.get("current_turn_closeout_reason")
            or current_turn_closeout.get("reason"),
            "evidence_blocker_status": evidence_blockers.get("status"),
            "evidence_blocker_next_mode": evidence_summary.get("recommended_next_mode"),
            "evidence_should_stop_open_loop_research": evidence_summary.get("should_stop_open_loop_research"),
            "evidence_retryable_public_count": evidence_summary.get("retryable_public_count"),
            "evidence_paid_or_api_needed_count": evidence_summary.get("paid_or_entitled_api_needed_count"),
            "evidence_account_or_broker_needed_count": evidence_summary.get(
                "user_account_or_broker_evidence_needed_count"
            ),
            "evidence_time_gated_needed_count": evidence_summary.get("time_gated_learning_outcome_needed_count"),
        },
        "tasks": ordered,
        "plain_notes": [
            "`queue` 是下一步工作队列，不是买卖清单。",
            "`active_goal_execution_matrix_ready` 表示用户原始目标已经绑定到本地执行矩阵和审计检查里。",
            "`P0` 代表会影响 execute_now 或关键学习闭环的事项。",
            "`max_allowed_until_done` 是该任务完成前允许的最高动作等级，不代表收益概率。",
            "`stop_and_report_status` 是本轮停止继续跑并汇报状态，不是停止系统后续学习。",
            "`evidence_blocker_next_mode` 会说明下一步是重试公开数据、等样本到期，还是需要用户/API 证据。",
        ],
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value).replace("\n", " ").replace("|", "\\|")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(cell(part) for part in row) + " |" for row in rows)
    return "\n".join(lines)


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    task_rows = [
        [
            item.get("priority"),
            item.get("area"),
            item.get("task_id"),
            item.get("action"),
            item.get("due_at") or item.get("earliest_at"),
            item.get("max_allowed_until_done"),
        ]
        for item in payload.get("tasks") or []
    ]
    command_rows: list[list[Any]] = []
    for item in payload.get("tasks") or []:
        for command in item.get("commands") or []:
            command_rows.append([item.get("task_id"), command])
    return "\n".join(
        [
            f"# Next Goal Execution Queue | {payload.get('generated_at')}",
            "",
            "这是下一次手动调度前的只读工作队列。它不抓新行情、不下单、不改账本。",
            "",
            "## Summary",
            "",
            markdown_table(
                ["Field", "Value"],
                [
                    ["goal_mechanism_ready", summary.get("goal_mechanism_ready")],
                    ["active_goal_execution_matrix_ready", summary.get("active_goal_execution_matrix_ready")],
                    ["goal_complete", summary.get("goal_complete")],
                    ["ready_for_next_manual_dispatch", summary.get("ready_for_next_manual_dispatch")],
                    ["ready_for_tactical_execute_now", summary.get("ready_for_tactical_execute_now")],
                    ["max_allowed_current_action", summary.get("max_allowed_current_action")],
                    ["current_tactical_symbol", summary.get("current_tactical_symbol")],
                    ["current_tactical_symbol_source", summary.get("current_tactical_symbol_source")],
                    ["task_count", summary.get("task_count")],
                    ["p0_open_count", summary.get("p0_open_count")],
                    ["execute_now_blocking_task_ids", summary.get("execute_now_blocking_task_ids")],
                    ["next_paper_review_at", summary.get("next_paper_review_at")],
                    ["next_recommendation_review_at", summary.get("next_recommendation_review_at")],
                    ["recommended_current_turn_action", summary.get("recommended_current_turn_action")],
                    ["should_continue_current_turn", summary.get("should_continue_current_turn")],
                    ["current_turn_closeout_reason", summary.get("current_turn_closeout_reason")],
                    ["evidence_blocker_next_mode", summary.get("evidence_blocker_next_mode")],
                    ["evidence_should_stop_open_loop_research", summary.get("evidence_should_stop_open_loop_research")],
                    ["evidence_retryable_public_count", summary.get("evidence_retryable_public_count")],
                    ["evidence_paid/API/account/time_gated", f"{summary.get('evidence_paid_or_api_needed_count')} / {summary.get('evidence_account_or_broker_needed_count')} / {summary.get('evidence_time_gated_needed_count')}"],
                ],
            ),
            "",
            "## Queue",
            "",
            markdown_table(
                ["Priority", "Area", "Task", "Action", "Time", "Max Allowed"],
                task_rows,
            ),
            "",
            "## Read-Only Commands",
            "",
            markdown_table(["Task", "Command"], command_rows) if command_rows else "No commands generated.",
            "",
            "## 术语小注",
            "",
            "- `P0`：优先级最高，通常会影响是否能升级真实执行建议。",
            "- `active_goal_execution_matrix_ready`：目标执行矩阵已生效，意思是你的原始目标已经被写进本地执行和审计链路。",
            "- `paper`：模拟交易样本，用来检验短线策略，不动真钱。",
            "- `execute_now`：立即执行建议；当前仍需要更多样本和证据，不应强行输出。",
            "- `queue`：下一步工作队列，不是买入或卖出指令。",
            "- `evidence_blocker`：证据阻断分类，说明缺口能否靠公开搜索解决；不能解决时应收口等证据。",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readiness-json", type=Path)
    parser.add_argument("--learning-calendar-json", type=Path)
    parser.add_argument("--closure-package-json", type=Path)
    parser.add_argument("--evidence-blocker-json", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    readiness_path = args.readiness_json or newest("next_dispatch_readiness*.json", prefer_non_test=True)
    learning_path = args.learning_calendar_json or newest("learning_review_calendar_*.json", prefer_non_test=True)
    closure_path = args.closure_package_json or newest("goal_evidence_closure_package_*.json", prefer_non_test=True)
    evidence_path = args.evidence_blocker_json or newest("evidence_blocker_classification_*.json", prefer_non_test=True)
    missing = [
        name
        for name, path in [
            ("readiness_json", readiness_path),
            ("learning_calendar_json", learning_path),
            ("closure_package_json", closure_path),
        ]
        if path is None or not Path(path).exists()
    ]
    if missing:
        raise SystemExit(f"Missing required inputs: {', '.join(missing)}")

    paths = {
        "readiness_json": str(readiness_path),
        "learning_calendar_json": str(learning_path),
        "closure_package_json": str(closure_path),
        "evidence_blocker_json": str(evidence_path) if evidence_path else "",
    }
    payload = build_queue(
        load_json(readiness_path),
        load_json(learning_path),
        load_json(closure_path),
        load_json(evidence_path) if evidence_path and evidence_path.exists() else {},
        paths,
    )
    output_json = args.output_json or EXPERIMENTS / "next_goal_execution_queue_latest.json"
    output_md = args.output_md or REPORTS / "next_goal_execution_queue_latest.md"
    write_json(output_json, payload)
    write_text(output_md, render_markdown(payload))

    if args.format == "markdown":
        print(render_markdown(payload), end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
