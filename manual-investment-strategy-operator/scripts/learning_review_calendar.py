#!/usr/bin/env python3
"""Build a read-only learning review calendar for manual dispatch.

The calendar merges two queues:
- active-alpha paper positions that will soon need simulated exit review
- manual recommendation records that are due or approaching due review

It never places orders and never mutates ledgers. Its purpose is to make each
manual dispatch visibly move the learning loop forward, even when no real
execute-now recommendation is allowed yet.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from recommendation_history import build_summary, due_at, load_ledger, parse_time


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = WORKSPACE_ROOT / "manual-investment-strategy-operator"
ACTIVE_ROOT = WORKSPACE_ROOT / "active-alpha-paper-monitor"
DEFAULT_RECOMMENDATION_LEDGER = MANUAL_ROOT / "recommendations" / "recommendation_history.json"
DEFAULT_PAPER_LEDGER = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
EARLY_CALIBRATION_TARGET = 10
USABLE_CALIBRATION_TARGET = 20


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_iso(text: Any) -> dt.datetime | None:
    if not isinstance(text, str) or not text:
        return None
    return parse_time(text)


def round_float(value: Any, digits: int = 4) -> float | None:
    if isinstance(value, (int, float)):
        return round(float(value), digits)
    return None


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


def build_paper_review_queue(paper_ledger: dict[str, Any], as_of: dt.datetime, max_items: int) -> dict[str, Any]:
    open_positions = paper_ledger.get("open_positions") or []
    closed_positions = paper_ledger.get("closed_positions") or paper_ledger.get("closed_trades") or []
    queue: list[dict[str, Any]] = []
    due_now = 0
    expiring_24h = 0
    expiring_72h = 0
    nearest_expiry_at: str | None = None

    for position in open_positions:
        expiry = parse_iso(position.get("expires_at"))
        hours_to_expiry = None
        review_state = "missing_expiry"
        if expiry:
            hours_to_expiry = (expiry - as_of).total_seconds() / 3600
            if hours_to_expiry <= 0:
                review_state = "due_now"
                due_now += 1
            elif hours_to_expiry <= 24:
                review_state = "due_within_24h"
                expiring_24h += 1
            elif hours_to_expiry <= 72:
                review_state = "due_within_72h"
                expiring_72h += 1
            else:
                review_state = "upcoming"
            if nearest_expiry_at is None or expiry.isoformat() < nearest_expiry_at:
                nearest_expiry_at = expiry.isoformat()

        queue.append({
            "paper_trade_id": position.get("paper_trade_id"),
            "symbol": position.get("symbol"),
            "strategy_family": position.get("strategy_family"),
            "paper_entry_mode": position.get("paper_entry_mode"),
            "opened_at": position.get("opened_at"),
            "expires_at": expiry.isoformat() if expiry else position.get("expires_at"),
            "hours_to_expiry": round_float(hours_to_expiry, 2),
            "review_state": review_state,
            "unrealized_pnl_pct": round_float(position.get("unrealized_pnl_pct"), 4),
            "stop_pct": round_float(position.get("stop_pct"), 4),
            "take_profit_pct": round_float(position.get("take_profit_pct"), 4),
            "status": position.get("status"),
            "outcome": position.get("outcome"),
        })

    queue.sort(key=lambda item: (
        item.get("hours_to_expiry") is None,
        item.get("hours_to_expiry") if item.get("hours_to_expiry") is not None else 10**9,
        str(item.get("symbol") or ""),
    ))
    closed_count = len(closed_positions)
    return {
        "live_orders_enabled": bool(paper_ledger.get("live_orders_enabled")),
        "open_count": len(open_positions),
        "closed_count": closed_count,
        "early_calibration_target": EARLY_CALIBRATION_TARGET,
        "early_calibration_needed": max(0, EARLY_CALIBRATION_TARGET - closed_count),
        "usable_calibration_target": USABLE_CALIBRATION_TARGET,
        "usable_calibration_needed": max(0, USABLE_CALIBRATION_TARGET - closed_count),
        "closed_target": USABLE_CALIBRATION_TARGET,
        "closed_needed": max(0, USABLE_CALIBRATION_TARGET - closed_count),
        "due_now_count": due_now,
        "expiring_24h_count": expiring_24h,
        "expiring_72h_count": expiring_72h,
        "nearest_expiry_at": nearest_expiry_at,
        "queue": queue[:max_items],
    }


def build_recommendation_review_queue(ledger: dict[str, Any], as_of: dt.datetime, max_items: int) -> dict[str, Any]:
    summary = build_summary(ledger)
    queue: list[dict[str, Any]] = []
    due_count = 0

    for record in ledger.get("recommendations", []):
        if record.get("outcome_status") != "pending":
            continue
        due_time = due_at(record)
        if due_time is None:
            review_state = "missing_due_time"
            days_until_due = None
        else:
            days_until_due = (due_time - as_of).total_seconds() / 86400
            if days_until_due <= 0:
                review_state = "due_now"
                due_count += 1
            elif days_until_due <= 7:
                review_state = "due_within_7d"
            elif days_until_due <= 30:
                review_state = "due_within_30d"
            else:
                review_state = "upcoming"

        queue.append({
            "recommendation_id": record.get("recommendation_id"),
            "symbol": record.get("symbol"),
            "asset_class": record.get("asset_class"),
            "bucket": record.get("bucket"),
            "action": record.get("action"),
            "generated_at": record.get("generated_at"),
            "due_at": due_time.isoformat() if due_time else None,
            "days_until_due": round_float(days_until_due, 2),
            "review_state": review_state,
            "forecast_probability_pct": round_float(record.get("forecast_probability_pct"), 2),
            "execution_readiness_score": round_float(record.get("execution_readiness_score"), 2),
            "risk_decision": record.get("risk_decision"),
            "data_quality_status": record.get("data_quality_status"),
            "outcome_status": record.get("outcome_status"),
        })

    queue.sort(key=lambda item: (
        item.get("days_until_due") is None,
        item.get("days_until_due") if item.get("days_until_due") is not None else 10**9,
        str(item.get("recommendation_id") or ""),
    ))
    resolved_count = (summary.get("status_counts") or {}).get("hit", 0) + (summary.get("status_counts") or {}).get("failed", 0)
    next_due_at = queue[0].get("due_at") if queue else None
    return {
        "summary": summary,
        "pending_count": (summary.get("status_counts") or {}).get("pending", 0),
        "resolved_count": resolved_count,
        "early_calibration_target": EARLY_CALIBRATION_TARGET,
        "early_calibration_needed": max(0, EARLY_CALIBRATION_TARGET - resolved_count),
        "usable_calibration_target": USABLE_CALIBRATION_TARGET,
        "usable_calibration_needed": max(0, USABLE_CALIBRATION_TARGET - resolved_count),
        "resolved_target": USABLE_CALIBRATION_TARGET,
        "resolved_needed": max(0, USABLE_CALIBRATION_TARGET - resolved_count),
        "due_now_count": due_count,
        "next_due_at": next_due_at,
        "queue": queue[:max_items],
    }


def build_calendar(
    recommendation_ledger_path: Path,
    paper_ledger_path: Path,
    as_of: dt.datetime,
    max_items: int,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    paper_payload: dict[str, Any] = {}
    recommendation_payload: dict[str, Any] = {}

    try:
        paper_ledger = load_json(paper_ledger_path)
        paper_payload = build_paper_review_queue(paper_ledger, as_of, max_items)
    except Exception as exc:  # noqa: BLE001
        errors.append({"area": "paper_ledger", "path": str(paper_ledger_path), "error": str(exc)})

    try:
        recommendation_ledger = load_ledger(recommendation_ledger_path)
        recommendation_payload = build_recommendation_review_queue(recommendation_ledger, as_of, max_items)
    except Exception as exc:  # noqa: BLE001
        errors.append({"area": "recommendation_ledger", "path": str(recommendation_ledger_path), "error": str(exc)})

    paper_early_needed = int(paper_payload.get("early_calibration_needed") or 0)
    paper_usable_needed = int(paper_payload.get("usable_calibration_needed") or USABLE_CALIBRATION_TARGET)
    rec_early_needed = int(recommendation_payload.get("early_calibration_needed") or 0)
    rec_usable_needed = int(recommendation_payload.get("usable_calibration_needed") or USABLE_CALIBRATION_TARGET)
    max_allowed_action = "watch"
    if not errors and paper_usable_needed == 0 and rec_usable_needed == 0:
        max_allowed_action = "conditional_action_review"
    elif not errors:
        max_allowed_action = "paper_only"

    next_paper = paper_payload.get("nearest_expiry_at")
    next_rec = recommendation_payload.get("next_due_at")
    next_action_hint = "wait_for_next_due_review"
    if errors:
        next_action_hint = "repair_learning_ledgers_before_new_execute_now"
    elif (paper_payload.get("due_now_count") or 0) > 0 or (recommendation_payload.get("due_now_count") or 0) > 0:
        next_action_hint = "review_due_samples_now"
    elif (paper_payload.get("expiring_24h_count") or 0) > 0:
        next_action_hint = "prepare_review_within_24h"
    elif next_paper or next_rec:
        next_action_hint = "monitor_next_review_window"

    blocked_reasons: list[str] = []
    if errors:
        blocked_reasons.append("learning_ledger_error")
    if paper_usable_needed > 0:
        blocked_reasons.append(f"paper_usable_samples_needed_{paper_usable_needed}")
    if rec_usable_needed > 0:
        blocked_reasons.append(f"recommendation_usable_samples_needed_{rec_usable_needed}")

    return {
        "generated_at": utc_now(),
        "as_of": as_of.isoformat(),
        "status": "degraded" if errors else "ok",
        "calendar_version": "learning-review-calendar-v1",
        "manual_dispatch_only": True,
        "live_orders_enabled": False,
        "mutates_real_portfolio": False,
        "mutates_recommendation_ledger": False,
        "inputs": {
            "recommendation_ledger": str(recommendation_ledger_path),
            "paper_ledger": str(paper_ledger_path),
        },
        "summary": {
            "next_paper_review_at": next_paper,
            "next_recommendation_review_at": next_rec,
            "next_action_hint": next_action_hint,
            "max_allowed_action_from_learning_calendar": max_allowed_action,
            "execute_now_allowed": False,
            "blocked_execute_now_reasons": blocked_reasons,
            "early_calibration_needed": {
                "paper": paper_early_needed,
                "recommendation": rec_early_needed,
            },
            "usable_calibration_needed": {
                "paper": paper_usable_needed,
                "recommendation": rec_usable_needed,
            },
        },
        "paper_review": paper_payload,
        "recommendation_review": recommendation_payload,
        "errors": errors,
        "operator_note": (
            "This is a read-only learning calendar. It schedules review work "
            "and never authorizes live trades."
        ),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    paper = payload.get("paper_review") or {}
    rec = payload.get("recommendation_review") or {}
    lines = [
        "# Learning Review Calendar",
        "",
        "这是手动调度的学习复盘日历。它只告诉我们下一次该复盘什么，不会下单、不会转账、不会改真实持仓。",
        "",
        "## One Page",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["status", payload.get("status")],
                ["as_of", payload.get("as_of")],
                ["next_paper_review_at", summary.get("next_paper_review_at")],
                ["next_recommendation_review_at", summary.get("next_recommendation_review_at")],
                ["next_action_hint", summary.get("next_action_hint")],
                ["max_allowed_action", summary.get("max_allowed_action_from_learning_calendar")],
                ["execute_now_allowed", summary.get("execute_now_allowed")],
                ["blocked_reasons", ", ".join(summary.get("blocked_execute_now_reasons") or [])],
            ],
        ),
        "",
        "## Paper Review Queue",
        "",
        markdown_table(
            ["Metric", "Value"],
            [
                ["open_count", paper.get("open_count")],
                ["closed_count", paper.get("closed_count")],
                ["early_calibration_target", paper.get("early_calibration_target")],
                ["early_calibration_needed", paper.get("early_calibration_needed")],
                ["usable_calibration_target", paper.get("usable_calibration_target")],
                ["usable_calibration_needed", paper.get("usable_calibration_needed")],
                ["due_now_count", paper.get("due_now_count")],
                ["expiring_24h_count", paper.get("expiring_24h_count")],
                ["expiring_72h_count", paper.get("expiring_72h_count")],
            ],
        ),
        "",
        markdown_table(
            ["Symbol", "Mode", "Review State", "Expires At", "Hours", "PnL %"],
            [
                [
                    item.get("symbol"),
                    item.get("paper_entry_mode"),
                    item.get("review_state"),
                    item.get("expires_at"),
                    item.get("hours_to_expiry"),
                    item.get("unrealized_pnl_pct"),
                ]
                for item in paper.get("queue", [])
            ] or [["none", "", "", "", "", ""]],
        ),
        "",
        "## Recommendation Review Queue",
        "",
        markdown_table(
            ["Metric", "Value"],
            [
                ["pending_count", rec.get("pending_count")],
                ["resolved_count", rec.get("resolved_count")],
                ["early_calibration_target", rec.get("early_calibration_target")],
                ["early_calibration_needed", rec.get("early_calibration_needed")],
                ["usable_calibration_target", rec.get("usable_calibration_target")],
                ["usable_calibration_needed", rec.get("usable_calibration_needed")],
                ["due_now_count", rec.get("due_now_count")],
            ],
        ),
        "",
        markdown_table(
            ["Recommendation", "Symbol", "Action", "Review State", "Due At", "Days"],
            [
                [
                    item.get("recommendation_id"),
                    item.get("symbol"),
                    item.get("action"),
                    item.get("review_state"),
                    item.get("due_at"),
                    item.get("days_until_due"),
                ]
                for item in rec.get("queue", [])
            ] or [["none", "", "", "", "", ""]],
        ),
        "",
        "## Terms",
        "",
        "- `paper review`: 纸面交易复盘，用模拟仓位检验策略，不是真实交易。",
        "- `resolved recommendation`: 已经被标记为命中或失败的历史建议，能用于校准胜率。",
        "- `max_allowed_action`: 当前学习证据允许的最大动作等级；这里永远不直接授权真实下单。",
        "",
    ]
    if payload.get("errors"):
        lines.extend(["## Errors", ""])
        for error in payload.get("errors") or []:
            lines.append(f"- `{error.get('area')}`: {error.get('error')}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a read-only learning review calendar")
    parser.add_argument("--recommendation-ledger", default=str(DEFAULT_RECOMMENDATION_LEDGER))
    parser.add_argument("--paper-ledger", default=str(DEFAULT_PAPER_LEDGER))
    parser.add_argument("--as-of", default="")
    parser.add_argument("--max-items", type=int, default=8)
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    as_of = parse_time(args.as_of) if args.as_of else dt.datetime.now(dt.timezone.utc)
    if as_of is None:
        raise ValueError("--as-of must be ISO-8601")
    payload = build_calendar(
        Path(args.recommendation_ledger),
        Path(args.paper_ledger),
        as_of,
        max(args.max_items, 1),
    )
    if args.output:
        write_json(Path(args.output), payload)
    if args.markdown_output:
        write_text(Path(args.markdown_output), render_markdown(payload))
    if args.format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
