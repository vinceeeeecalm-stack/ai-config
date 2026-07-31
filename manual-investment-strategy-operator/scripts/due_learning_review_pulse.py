#!/usr/bin/env python3
"""Run a short learning-review pulse only when review samples are due.

This guard keeps ordinary manual dispatch finite. It first builds the learning
review calendar. If no paper sample is actually due, it writes a status report
and exits without running the active paper validation runner. If a paper sample
is due, it runs one timeboxed validation pulse.

The script never places real orders, never moves cash, and never mutates the
real portfolio ledger. The only allowed mutation is the active monitor's paper
ledger when a simulated paper trade is ready to review.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from pathlib import Path
from typing import Any

from learning_review_calendar import (
    DEFAULT_PAPER_LEDGER,
    DEFAULT_RECOMMENDATION_LEDGER,
    build_calendar,
    render_markdown as render_calendar_markdown,
)


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = WORKSPACE_ROOT / "manual-investment-strategy-operator"
ACTIVE_ROOT = WORKSPACE_ROOT / "active-alpha-paper-monitor"
EXPERIMENTS = MANUAL_ROOT / "experiments"
REPORTS = MANUAL_ROOT / "reports"
SHANGHAI = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")
DEFAULT_VALIDATION_RUNNER = ACTIVE_ROOT / "scripts" / "validation_progress_runner.py"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


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
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def display_time(value: Any) -> str:
    parsed = parse_time(value)
    if parsed is None:
        return str(value or "")
    return parsed.astimezone(SHANGHAI).replace(microsecond=0).isoformat()


def hours_until(value: Any, as_of: dt.datetime) -> float | None:
    parsed = parse_time(value)
    if parsed is None:
        return None
    return round((parsed - as_of).total_seconds() / 3600, 2)


def truncate(text: str, limit: int = 5000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def run_validation_pulse(timeout_seconds: int) -> dict[str, Any]:
    command = [
        "python3",
        str(DEFAULT_VALIDATION_RUNNER),
        "--cycles",
        "1",
        "--skip-fast",
        "--no-lock",
        "--compact-output",
        "--format",
        "json",
        "--exit-timeout-seconds",
        "120",
        "--validation-timeout-seconds",
        "90",
    ]
    started_at = utc_now()
    try:
        completed = subprocess.run(
            command,
            cwd=str(WORKSPACE_ROOT),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "started_at": started_at,
            "finished_at": utc_now(),
            "command": command,
            "returncode": completed.returncode,
            "stdout": truncate(completed.stdout),
            "stderr": truncate(completed.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "started_at": started_at,
            "finished_at": utc_now(),
            "command": command,
            "returncode": None,
            "timed_out": True,
            "stdout": truncate(exc.stdout or ""),
            "stderr": truncate(exc.stderr or ""),
        }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def cell(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            return " ".join(f"`{item}`" for item in value)
        return str(value).replace("\n", " ").replace("|", "\\|")

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(cell(item) for item in row) + " |" for row in rows)
    return "\n".join(lines)


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    validation = payload.get("validation_pulse") or {}
    return "\n\n".join([
        "# Due Learning Review Pulse",
        "这是一份学习复盘守门报告：未到期不跑长任务，到期才跑一次短脉冲模拟复盘。",
        "## 一页结论",
        markdown_table(
            ["项目", "值"],
            [
                ["状态", payload.get("status")],
                ["动作", summary.get("action")],
                ["dry run", summary.get("dry_run")],
                ["paper 短脉冲已到期", summary.get("paper_pulse_due")],
                ["是否运行 paper 短脉冲", summary.get("paper_pulse_ran")],
                ["paper due now", summary.get("paper_due_now_count")],
                ["paper 24小时内到期", summary.get("paper_expiring_24h_count")],
                ["下一次 paper 复盘", summary.get("next_paper_review_at")],
                ["下一次 paper 复盘（上海）", summary.get("next_paper_review_at_shanghai")],
                ["距离下一次 paper 复盘（小时）", summary.get("hours_until_next_paper_review")],
                ["最大当前动作", summary.get("max_allowed_action")],
                ["真实下单", summary.get("live_orders_enabled")],
            ],
        ),
        "## 为什么这样处理",
        "\n".join(f"- {item}" for item in summary.get("plain_notes") or []),
        "## 运行结果",
        markdown_table(
            ["字段", "值"],
            [
                ["returncode", validation.get("returncode")],
                ["timed_out", validation.get("timed_out")],
                ["command", validation.get("command")],
            ],
        ) if validation else "- 未运行 validation pulse，因为没有 paper 样本真正到期。",
        "## 术语小注",
        "- `due_now`：已经到复盘时间，可以跑短脉冲复盘。",
        "- `due_within_24h`：24小时内会到期，但现在还不该提前反复跑。",
        "- `paper pulse`：只复盘模拟仓位，不是真实交易。",
        "- `max_allowed_action`：当前证据允许的最高动作等级，不等于收益保证。",
        "",
    ])


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    as_of = parse_time(args.as_of) if args.as_of else dt.datetime.now(dt.timezone.utc)
    if as_of is None:
        raise ValueError("--as-of must be ISO-8601")
    calendar = build_calendar(
        Path(args.recommendation_ledger),
        Path(args.paper_ledger),
        as_of,
        max(args.max_items, 1),
    )
    paper = calendar.get("paper_review") or {}
    summary = calendar.get("summary") or {}
    paper_due_now = int(paper.get("due_now_count") or 0)
    expiring_24h = int(paper.get("expiring_24h_count") or 0)
    pulse_due = bool(args.force_run or paper_due_now > 0)
    should_run = bool(pulse_due and not args.dry_run)
    validation: dict[str, Any] | None = None
    if should_run:
        action = "run_short_paper_validation_pulse"
    elif pulse_due and args.dry_run:
        action = "dry_run_due_pulse_ready"
    else:
        action = "skip_not_due"

    if should_run:
        validation = run_validation_pulse(args.timeout_seconds)
        if validation.get("returncode") not in {0, None}:
            status = "degraded"
        elif validation.get("timed_out"):
            status = "degraded"
        else:
            status = "ok"
    elif pulse_due and args.dry_run:
        status = "ok"
        validation = {
            "dry_run": True,
            "would_run": True,
            "command": [
                "python3",
                str(DEFAULT_VALIDATION_RUNNER),
                "--cycles",
                "1",
                "--skip-fast",
                "--no-lock",
                "--compact-output",
                "--format",
                "json",
                "--exit-timeout-seconds",
                "120",
                "--validation-timeout-seconds",
                "90",
            ],
            "returncode": None,
            "timed_out": False,
        }
    else:
        status = "ok"

    next_paper = summary.get("next_paper_review_at")
    return {
        "generated_at": utc_now(),
        "status": status,
        "pulse_version": "due-learning-review-pulse-v1",
        "read_only_guard": True,
        "manual_dispatch_only": True,
        "live_orders_enabled": False,
        "mutates_real_portfolio": False,
        "mutates_recommendation_ledger": False,
        "may_mutate_paper_ledger_when_due": should_run,
        "inputs": {
            "recommendation_ledger": str(args.recommendation_ledger),
            "paper_ledger": str(args.paper_ledger),
            "force_run": bool(args.force_run),
            "dry_run": bool(args.dry_run),
            "as_of": as_of.isoformat(),
        },
        "summary": {
            "action": action,
            "dry_run": bool(args.dry_run),
            "paper_pulse_due": pulse_due,
            "paper_pulse_ran": should_run,
            "paper_due_now_count": paper_due_now,
            "paper_expiring_24h_count": expiring_24h,
            "next_paper_review_at": next_paper,
            "next_paper_review_at_shanghai": display_time(next_paper),
            "hours_until_next_paper_review": hours_until(next_paper, as_of),
            "next_recommendation_review_at": summary.get("next_recommendation_review_at"),
            "max_allowed_action": summary.get("max_allowed_action_from_learning_calendar"),
            "live_orders_enabled": False,
            "plain_notes": [
                "现在只检查学习复盘是否到期，不抓新行情，也不生成新的买卖建议。",
                "没有 due_now 样本时直接跳过，避免把“快到期”误当作“必须继续跑”。",
                "dry-run 模式只验证到期路径，不会启动 active validation，也不会改 paper 账本。",
                "即使运行 paper pulse，它也只处理模拟仓位，不会触碰真实资金。",
            ],
        },
        "learning_calendar": calendar,
        "validation_pulse": validation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run due learning review pulse only when samples are due")
    parser.add_argument("--recommendation-ledger", default=str(DEFAULT_RECOMMENDATION_LEDGER))
    parser.add_argument("--paper-ledger", default=str(DEFAULT_PAPER_LEDGER))
    parser.add_argument("--max-items", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--force-run", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Report whether a due pulse would run without invoking validation")
    parser.add_argument("--as-of", default="", help="Optional ISO-8601 time for deterministic due/not-due checks")
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    parser.add_argument("--calendar-output-json", default="")
    parser.add_argument("--calendar-output-md", default="")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    payload = build_payload(args)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    output_json = Path(args.output_json) if args.output_json else EXPERIMENTS / f"due_learning_review_pulse_{stamp}.json"
    output_md = Path(args.output_md) if args.output_md else REPORTS / f"due_learning_review_pulse_{stamp}.md"
    write_json(output_json, payload)
    write_text(output_md, render_markdown(payload))

    if args.calendar_output_json:
        write_json(Path(args.calendar_output_json), payload["learning_calendar"])
    if args.calendar_output_md:
        write_text(Path(args.calendar_output_md), render_calendar_markdown(payload["learning_calendar"]))

    payload["written_outputs"] = {
        "json": str(output_json),
        "markdown": str(output_md),
        "calendar_json": args.calendar_output_json,
        "calendar_markdown": args.calendar_output_md,
    }

    if args.format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
