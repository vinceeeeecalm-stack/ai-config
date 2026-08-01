#!/usr/bin/env python3
"""Audit whether Phase 2 quality recovery actions are enforced by paper gates.

Read-only. Compares the Phase 2 quality recovery board with the latest
validation recovery plan and latest no-entry evidence. It never mutates the
paper ledger, strategy config, automation state, or live accounts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
EXPERIMENTS_DIR = ACTIVE_ROOT / "experiments"
REPORT_PATH = ACTIVE_ROOT / "reports" / "PHASE2_QUALITY_GATE_ENFORCEMENT_AUDIT.md"
EXPERIMENT_PATH = EXPERIMENTS_DIR / "phase2-quality-gate-enforcement-audit.json"
PHASE2_QUALITY_PATH = EXPERIMENTS_DIR / "phase2-quality-recovery-action-board.json"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def now_local() -> dt.datetime:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0)


def rel(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path | None, default: Any = None) -> Any:
    if not path or not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def latest(pattern: str) -> Path | None:
    files = list(EXPERIMENTS_DIR.glob(pattern))
    return max(files, key=lambda item: item.stat().st_mtime) if files else None


def norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def names_from(rows: Any) -> set[str]:
    if not isinstance(rows, list):
        return set()
    return {norm(item.get("name")) for item in rows if isinstance(item, dict) and item.get("name")}


def latest_runner_payload() -> tuple[Path | None, dict[str, Any]]:
    path = latest("*validation-progress-runner.json")
    return path, read_json(path, {}) if path else {}


def recovery_plan_from_runner(runner: dict[str, Any]) -> dict[str, Any]:
    return ((runner.get("post_validation") or {}).get("stdout_json") or {}).get("validation_recovery_plan") or {}


def no_entry_hits(runner: dict[str, Any], name: str) -> list[dict[str, Any]]:
    no_entry = runner.get("no_entry_summary") if isinstance(runner.get("no_entry_summary"), dict) else {}
    candidates = no_entry.get("top_blocked_candidates") if isinstance(no_entry.get("top_blocked_candidates"), list) else []
    needle = norm(name)
    hits: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        text = " ".join(
            norm(item.get(field))
            for field in (
                "entry_mode_estimate",
                "strategy_family",
                "validation_recovery_match",
                "primary_block_reason",
            )
        )
        if needle and needle in text:
            hits.append(
                {
                    "symbol": item.get("symbol"),
                    "primary_block_reason": item.get("primary_block_reason"),
                    "validation_recovery_match": item.get("validation_recovery_match"),
                    "selection_score": item.get("selection_score"),
                }
            )
    return hits[:8]


def evaluate_item(item: dict[str, Any], gated_names: set[str], runner: dict[str, Any]) -> dict[str, Any]:
    name = str(item.get("name") or "")
    normalized = norm(name)
    hits = no_entry_hits(runner, name)
    in_gate = normalized in gated_names
    if in_gate and hits:
        status = "enforced_and_blocking_current_candidates"
    elif in_gate:
        status = "enforced_by_recovery_plan"
    else:
        status = "missing_from_recovery_plan"
    return {
        "name": name,
        "normalized_name": normalized,
        "source_action": item.get("action"),
        "trade_count": item.get("trades") or item.get("trade_count"),
        "win_rate_pct": item.get("win_rate_pct"),
        "net_pnl_usd": item.get("net_pnl_usd"),
        "enforcement_status": status,
        "current_block_hits": hits,
    }


def build_payload() -> dict[str, Any]:
    created = now_local()
    run_id = f"{created.strftime('%Y%m%d-%H%M%S')}-phase2-quality-gate-enforcement"
    quality = read_json(PHASE2_QUALITY_PATH, {}) or {}
    runner_path, runner = latest_runner_payload()
    plan = recovery_plan_from_runner(runner)
    gated_entry_names = names_from(plan.get("retired_entry_modes")) | names_from(plan.get("cooldown_entry_modes"))
    gated_family_names = names_from(plan.get("retired_strategy_families")) | names_from(plan.get("cooldown_strategy_families"))
    entry_rows = [
        evaluate_item(item, gated_entry_names, runner)
        for item in (quality.get("blocked_entry_modes") or [])
        if isinstance(item, dict)
    ]
    family_rows = [
        evaluate_item(item, gated_family_names, runner)
        for item in (quality.get("blocked_strategy_families") or [])
        if isinstance(item, dict)
    ]
    all_rows = entry_rows + family_rows
    blocking = [row for row in all_rows if row["enforcement_status"] == "missing_from_recovery_plan"]
    current_hits = [row for row in all_rows if row.get("current_block_hits")]
    status = "pass" if not blocking else "warn"
    payload = {
        "run_id": run_id,
        "created_at": created.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "scope": "phase2_quality_gate_enforcement_read_only",
        "status": status,
        "summary": {
            "checked_count": len(all_rows),
            "enforced_count": len([row for row in all_rows if row["enforcement_status"] != "missing_from_recovery_plan"]),
            "current_blocking_count": len(current_hits),
            "missing_from_recovery_plan_count": len(blocking),
        },
        "entry_mode_rows": entry_rows,
        "strategy_family_rows": family_rows,
        "missing_from_recovery_plan": blocking,
        "current_block_hits": current_hits,
        "sources": {
            "phase2_quality_board": rel(PHASE2_QUALITY_PATH),
            "validation_runner": rel(runner_path),
        },
        "outputs": {"report": rel(REPORT_PATH), "experiment": rel(EXPERIMENT_PATH)},
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "ledger_mutated": False,
        "strategy_config_mutated": False,
    }
    return payload


def render(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Phase 2 Quality Gate Enforcement Audit",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- created_at: `{payload['created_at']}`",
        f"- status: `{payload['status']}`",
        "- scope: `phase2_quality_gate_enforcement_read_only`",
        f"- live_orders_enabled: `{payload['live_orders_enabled']}`",
        f"- private_api_used: `{payload['private_api_used']}`",
        f"- ledger_mutated: `{payload['ledger_mutated']}`",
        "",
        "## Summary",
        "",
        f"- checked_count: `{summary['checked_count']}`",
        f"- enforced_count: `{summary['enforced_count']}`",
        f"- current_blocking_count: `{summary['current_blocking_count']}`",
        f"- missing_from_recovery_plan_count: `{summary['missing_from_recovery_plan_count']}`",
        "",
        "## Entry Modes",
        "",
        "| Entry Mode | Action | Status | Current Block Hits |",
        "|---|---|---|---:|",
    ]
    for row in payload["entry_mode_rows"]:
        lines.append(
            f"| `{row['name']}` | `{row.get('source_action')}` | `{row['enforcement_status']}` | `{len(row.get('current_block_hits') or [])}` |"
        )
    if not payload["entry_mode_rows"]:
        lines.append("| - | - | - | - |")
    lines.extend(["", "## Strategy Families", "", "| Family | Action | Status | Current Block Hits |", "|---|---|---|---:|"])
    for row in payload["strategy_family_rows"]:
        lines.append(
            f"| `{row['name']}` | `{row.get('source_action')}` | `{row['enforcement_status']}` | `{len(row.get('current_block_hits') or [])}` |"
        )
    if not payload["strategy_family_rows"]:
        lines.append("| - | - | - | - |")
    lines.extend(["", "## Current Block Hits", "", "| Name | Symbol | Reason |", "|---|---|---|"])
    for row in payload["current_block_hits"]:
        for hit in row.get("current_block_hits") or []:
            lines.append(f"| `{row['name']}` | `{hit.get('symbol')}` | `{hit.get('primary_block_reason')}` |")
    if not payload["current_block_hits"]:
        lines.append("| - | - | - |")
    lines.extend(["", "## Sources", ""])
    for key, value in payload["sources"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    return "\n".join(lines)


def write_outputs(payload: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPERIMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render(payload), encoding="utf-8")
    EXPERIMENT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def self_test() -> dict[str, Any]:
    runner = {
        "post_validation": {
            "stdout_json": {
                "validation_recovery_plan": {
                    "cooldown_entry_modes": [{"name": "weak_mode"}],
                    "retired_strategy_families": [{"name": "bad family"}],
                }
            }
        },
        "no_entry_summary": {
            "top_blocked_candidates": [
                {
                    "symbol": "ABCUSDT",
                    "entry_mode_estimate": "weak_mode",
                    "primary_block_reason": "validation_recovery_plan_block:weak_mode",
                }
            ]
        },
    }
    quality_item = {"name": "weak_mode", "action": "smallest_paper_only_after_retest"}
    family_item = {"name": "bad family", "action": "retire_or_cooldown_from_new_samples"}
    entry = evaluate_item(quality_item, names_from([{"name": "weak_mode"}]), runner)
    family = evaluate_item(family_item, names_from([{"name": "bad family"}]), runner)
    with tempfile.TemporaryDirectory(prefix="phase2_quality_gate_enforcement_", dir="/private/tmp") as tmp_text:
        tmp = Path(tmp_text)
        report = tmp / "report.md"
        payload = {
            "run_id": "self-test",
            "created_at": now_local().isoformat(),
            "status": "pass",
            "summary": {
                "checked_count": 2,
                "enforced_count": 2,
                "current_blocking_count": 1,
                "missing_from_recovery_plan_count": 0,
            },
            "entry_mode_rows": [entry],
            "strategy_family_rows": [family],
            "current_block_hits": [entry],
            "sources": {},
            "live_orders_enabled": False,
            "private_api_used": False,
            "ledger_mutated": False,
        }
        report.write_text(render(payload), encoding="utf-8")
        rendered = report.exists() and "Phase 2 Quality Gate Enforcement Audit" in report.read_text(encoding="utf-8")
    assert entry["enforcement_status"] == "enforced_and_blocking_current_candidates", entry
    assert family["enforcement_status"] == "enforced_by_recovery_plan", family
    assert rendered
    return {
        "status": "ok",
        "enforced_blocking_path_verified": True,
        "enforced_no_current_hit_path_verified": True,
        "render_verified": True,
        "uses_temporary_files_only": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Phase 2 quality gate enforcement.")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    payload = build_payload()
    write_outputs(payload)
    if args.compact_output:
        print(
            json.dumps(
                {
                    "status": payload["status"],
                    "run_id": payload["run_id"],
                    "summary": payload["summary"],
                    "outputs": payload["outputs"],
                    "live_orders_enabled": payload["live_orders_enabled"],
                    "private_api_used": payload["private_api_used"],
                    "ledger_mutated": payload["ledger_mutated"],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
