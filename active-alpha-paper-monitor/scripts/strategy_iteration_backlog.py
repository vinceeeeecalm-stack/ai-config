#!/usr/bin/env python3
"""Maintain the paper-only strategy iteration backlog.

The validation auditor emits proposed strategy changes, but the operator needs
those changes to persist across runs. This script merges the latest proposed
changes into a stable backlog keyed by `proposed_change_id`.

It is deliberately read-only with respect to trading state: it does not mutate
the paper ledger, does not update strategy config, and never calls live/private
exchange APIs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
BACKLOG_JSON = ACTIVE_ROOT / "experiments" / "strategy-iteration-backlog.json"
BACKLOG_REPORT = ACTIVE_ROOT / "reports" / "STRATEGY_ITERATION_BACKLOG.md"

sys.path.insert(0, str(SCRIPT_DIR))
from validation_sample_auditor import build_audit  # noqa: E402


def now_local() -> str:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0).isoformat()


def rel(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def latest_file(pattern: str) -> Path | None:
    paths = list(ACTIVE_ROOT.glob(pattern))
    if not paths:
        return None
    return max(paths, key=lambda item: item.stat().st_mtime)


def tagged_change(change: dict[str, Any], source: str) -> dict[str, Any]:
    out = dict(change)
    out.setdefault("source", source)
    return out


def default_operator_status(change: dict[str, Any], existing: dict[str, Any]) -> str:
    preserved = str(existing.get("operator_status") or "")
    if preserved in {
        "auto_apply_paper_only",
        "paper_ab_testing",
        "paper_applied",
        "paper_reverted",
        "paper_promoted_candidate",
    }:
        return preserved
    scope = str(change.get("scope") or "")
    if scope.startswith("paper_only"):
        return "auto_apply_paper_only"
    return preserved or change.get("status") or "proposed_pending_human_review"


def current_proposed_changes() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    audit = build_audit(root=WORKSPACE_ROOT)
    recovery = audit.get("validation_recovery_plan") if isinstance(audit.get("validation_recovery_plan"), dict) else {}
    audit_changes = recovery.get("proposed_changes") if isinstance(recovery.get("proposed_changes"), list) else []

    attribution_path = latest_file("experiments/*paper-trade-attribution-report.json")
    attribution = read_json(attribution_path, {}) if attribution_path else {}
    attribution_changes = (
        attribution.get("proposed_changes")
        if isinstance(attribution, dict) and isinstance(attribution.get("proposed_changes"), list)
        else []
    )
    cross_section_path = ACTIVE_ROOT / "experiments" / "cross-sectional-rotation-lab.json"
    cross_section = read_json(cross_section_path, {})
    cross_section_changes = (
        cross_section.get("proposed_changes")
        if isinstance(cross_section, dict) and isinstance(cross_section.get("proposed_changes"), list)
        else []
    )
    audit["_strategy_backlog_sources"] = {
        "validation_audit_generated_at": audit.get("generated_at"),
        "validation_audit_status": audit.get("status"),
        "attribution_experiment": rel(attribution_path),
        "attribution_run_id": attribution.get("run_id") if isinstance(attribution, dict) else None,
        "attribution_created_at": attribution.get("created_at") if isinstance(attribution, dict) else None,
        "validation_proposed_change_count": len([item for item in audit_changes if isinstance(item, dict)]),
        "attribution_proposed_change_count": len([item for item in attribution_changes if isinstance(item, dict)]),
        "cross_sectional_rotation_experiment": rel(cross_section_path) if cross_section else "-",
        "cross_sectional_rotation_run_id": cross_section.get("run_id") if isinstance(cross_section, dict) else None,
        "cross_sectional_rotation_proposed_change_count": len([item for item in cross_section_changes if isinstance(item, dict)]),
    }
    merged: list[dict[str, Any]] = []
    for item in audit_changes:
        if isinstance(item, dict):
            merged.append(tagged_change(item, "validation_sample_auditor"))
    for item in attribution_changes:
        if isinstance(item, dict):
            merged.append(tagged_change(item, "paper_trade_attribution_report"))
    for item in cross_section_changes:
        if isinstance(item, dict):
            merged.append(tagged_change(item, "cross_sectional_rotation_lab"))
    return audit, merged


def merge_backlog(previous: dict[str, Any], audit: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    generated_at = now_local()
    previous_items = previous.get("items") if isinstance(previous.get("items"), dict) else {}
    items: dict[str, Any] = dict(previous_items)
    current_ids: set[str] = set()

    for change in changes:
        change_id = str(change.get("proposed_change_id") or "").strip()
        if not change_id:
            continue
        current_ids.add(change_id)
        existing = items.get(change_id) if isinstance(items.get(change_id), dict) else {}
        history = existing.get("history") if isinstance(existing.get("history"), list) else []
        seen_count = int(existing.get("seen_count") or 0) + 1
        status = default_operator_status(change, existing)
        items[change_id] = {
            "proposed_change_id": change_id,
            "operator_status": status,
            "first_seen_at": existing.get("first_seen_at") or generated_at,
            "last_seen_at": generated_at,
            "seen_count": seen_count,
            "currently_present": True,
            "scope": change.get("scope"),
            "change_type": change.get("change_type"),
            "title": change.get("title"),
            "rationale": change.get("rationale"),
            "affected_items": change.get("affected_items") or [],
            "proposed_adjustment": change.get("proposed_adjustment") or {},
            "validation_plan": change.get("validation_plan") or {},
            "risk_controls": change.get("risk_controls") or [],
            "source": change.get("source"),
            "live_orders_enabled": False,
            "private_api_used": False,
            "history": [
                *history[-9:],
                {
                    "seen_at": generated_at,
                    "generated_run_stamp": change.get("generated_run_stamp"),
                    "affected_item_count": len(change.get("affected_items") or []),
                    "status": change.get("status"),
                    "source": change.get("source"),
                },
            ],
        }

    for change_id, item in list(items.items()):
        if change_id not in current_ids:
            item = dict(item)
            item["currently_present"] = False
            item.setdefault("operator_status", "not_seen_in_latest_audit")
            items[change_id] = item

    portfolio = audit.get("current_portfolio_metrics") if isinstance(audit.get("current_portfolio_metrics"), dict) else {}
    paper = audit.get("paper_sample_metrics") if isinstance(audit.get("paper_sample_metrics"), dict) else {}
    sample_plan = audit.get("validation_sample_plan") if isinstance(audit.get("validation_sample_plan"), dict) else {}
    sources = audit.get("_strategy_backlog_sources") if isinstance(audit.get("_strategy_backlog_sources"), dict) else {}
    active_items = [item for item in items.values() if item.get("currently_present")]
    pending_items = [item for item in active_items if str(item.get("operator_status", "")).startswith("proposed")]
    status_counts = Counter(str(item.get("operator_status") or "unknown") for item in active_items)

    return {
        "generated_at": generated_at,
        "scope": "paper_only_strategy_iteration_backlog",
        "live_orders_enabled": False,
        "private_api_used": False,
        "source": {
            "audit_generated_at": audit.get("generated_at"),
            "audit_status": audit.get("status"),
            **sources,
        },
        "portfolio_snapshot": {
            "equity_usd": portfolio.get("equity_usd"),
            "cash_usd": portfolio.get("cash_usd"),
            "open_count": portfolio.get("open_count"),
            "closed_count": portfolio.get("closed_count"),
            "net_return_pct": portfolio.get("net_return_pct"),
            "max_drawdown_pct": portfolio.get("max_drawdown_pct"),
        },
        "paper_sample_snapshot": {
            "closed_count": paper.get("closed_count"),
            "win_rate_pct": paper.get("win_rate_pct"),
            "realized_pnl_usd": paper.get("realized_pnl_usd"),
            "realized_net_return_on_closed_notional_pct": paper.get("realized_net_return_on_closed_notional_pct"),
        },
        "sample_gaps": sample_plan.get("sample_gaps") or {},
        "failed_gates": sample_plan.get("failed_gates") or [],
        "summary": {
            "total_backlog_items": len(items),
            "active_proposed_changes": len(active_items),
            "pending_human_review": len(pending_items),
            "operator_status_counts": dict(sorted(status_counts.items())),
            "current_ids": sorted(current_ids),
            "active_sources": dict(sorted(Counter(str(item.get("source") or "unknown") for item in active_items).items())),
        },
        "items": dict(sorted(items.items())),
        "operator_note": "Backlog is tracking only. It does not mutate strategy config, paper ledger, or live trading permissions.",
    }


def render_markdown(backlog: dict[str, Any]) -> str:
    summary = backlog.get("summary") or {}
    paper = backlog.get("paper_sample_snapshot") or {}
    portfolio = backlog.get("portfolio_snapshot") or {}
    sample_gaps = backlog.get("sample_gaps") or {}
    active_items = [
        item for item in (backlog.get("items") or {}).values()
        if isinstance(item, dict) and item.get("currently_present")
    ]
    sources = (backlog.get("source") or {}) if isinstance(backlog.get("source"), dict) else {}
    lines = [
        "# Strategy Iteration Backlog",
        "",
        f"- generated_at: `{backlog.get('generated_at')}`",
        "- scope: `paper_only_strategy_iteration_backlog`",
        "- live_orders_enabled: `false`",
        "- private_api_used: `false`",
        "",
        "## Current Evidence",
        "",
        f"- equity_usd: `${portfolio.get('equity_usd')}`",
        f"- closed_trades: `{paper.get('closed_count')}`",
        f"- win_rate_pct: `{paper.get('win_rate_pct')}`",
        f"- realized_pnl_usd: `${paper.get('realized_pnl_usd')}`",
        f"- net_return_on_closed_notional_pct: `{paper.get('realized_net_return_on_closed_notional_pct')}`",
        f"- sample_gaps: `{sample_gaps}`",
        f"- attribution_experiment: `{sources.get('attribution_experiment') or '-'}`",
        f"- attribution_proposed_change_count: `{sources.get('attribution_proposed_change_count')}`",
        "",
        "## Backlog Summary",
        "",
        f"- total_backlog_items: `{summary.get('total_backlog_items')}`",
        f"- active_proposed_changes: `{summary.get('active_proposed_changes')}`",
        f"- pending_human_review: `{summary.get('pending_human_review')}`",
        f"- operator_status_counts: `{summary.get('operator_status_counts')}`",
        f"- active_sources: `{summary.get('active_sources')}`",
        "",
        "## Active Proposed Changes",
        "",
        "| ID | Source | Type | Status | Seen | Title |",
        "|---|---|---|---|---:|---|",
    ]
    if active_items:
        for item in active_items:
            lines.append(
                f"| `{item.get('proposed_change_id')}` | `{item.get('source') or '-'}` | "
                f"`{item.get('change_type')}` | `{item.get('operator_status')}` | "
                f"`{item.get('seen_count')}` | {item.get('title')} |"
            )
    else:
        lines.append("| - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Operator Rules",
            "",
            "- `auto_apply_paper_only` 代表允许 paper-only auto evolver 自动写入 overlay。",
            "- `paper_applied` / `paper_ab_testing` / `paper_reverted` 只影响模拟策略，不代表真实交易授权。",
            "- `proposed_pending_human_review` 代表只进入待审查 backlog，不代表配置已改变。",
            "- 任何提案进入真实交易前仍需 Phase 2/Phase 3 证据和人工确认。",
            "- 本文件不会开仓、平仓、调用私有 API 或改动 paper ledger。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Write paper-only strategy iteration backlog.")
    parser.add_argument("--output-json", default=str(BACKLOG_JSON))
    parser.add_argument("--output-report", default=str(BACKLOG_REPORT))
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()

    audit, changes = current_proposed_changes()
    output_json = Path(args.output_json)
    output_report = Path(args.output_report)
    previous = read_json(output_json, {})
    backlog = merge_backlog(previous if isinstance(previous, dict) else {}, audit, changes)
    write_json(output_json, backlog)
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_report.write_text(render_markdown(backlog), encoding="utf-8")

    result = {
        "status": "ok",
        "json": rel(output_json),
        "report": rel(output_report),
        "active_proposed_changes": (backlog.get("summary") or {}).get("active_proposed_changes"),
        "pending_human_review": (backlog.get("summary") or {}).get("pending_human_review"),
        "live_orders_enabled": False,
        "private_api_used": False,
    }
    if args.compact_output:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"Wrote {rel(output_report)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
