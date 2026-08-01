#!/usr/bin/env python3
"""Create a compact artifact inventory for the active-alpha automation.

The hourly paper loop intentionally writes reports, experiments and handoffs so
every decision is auditable. This helper keeps that audit trail readable by
writing one index file and surfacing whether the local automation set is still
consolidated. By default it never deletes or moves files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
AUTOMATION_ROOT = Path("/Users/vincentpan/.codex/automations")
TARGET_AUTOMATION_ID = "active-alpha-hourly-crypto-paper-loop"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
INDEX_REPORT = ACTIVE_ROOT / "reports" / "AUTOMATION_ARTIFACT_INDEX.md"
INDEX_JSON = ACTIVE_ROOT / "experiments" / "automation-artifact-housekeeper-latest.json"


REPORT_TYPE_MARKERS = (
    ("validation_runner", "validation-progress-runner"),
    ("pipeline_freshness", "pipeline-freshness-audit"),
    ("pipeline_freshness_repair", "pipeline-freshness-repair"),
    ("strategy_proposal_decision", "STRATEGY_PROPOSAL_DECISION_BOARD"),
    ("strategy_proposal_enforcement", "STRATEGY_PROPOSAL_ENFORCEMENT_AUDIT"),
    ("preflight", "resume-preflight"),
    ("fast_crypto", "fast-crypto-paper"),
    ("exit_monitor", "paper-exit-monitor"),
    ("phase_goal", "phase-goal-readiness"),
    ("phase2_quality", "PHASE2_QUALITY_RECOVERY_ACTION_BOARD"),
    ("phase2_quality", "phase2-quality-recovery-action-board"),
    ("phase2_quality_gate", "PHASE2_QUALITY_GATE_ENFORCEMENT_AUDIT"),
    ("phase2_quality_gate", "phase2-quality-gate-enforcement-audit"),
    ("phase3_pressure", "PHASE3_PRESSURE_ACTION_BOARD"),
    ("phase3_pressure", "phase3-pressure-action-board"),
    ("compounding", "binance-api-compounding-flow-audit"),
    ("binance_kline_cache", "binance-kline-cache-builder"),
    ("kline_research_reproducibility", "KLINE_RESEARCH_REPRODUCIBILITY_AUDIT"),
    ("kline_research_reproducibility", "kline-research-reproducibility-audit"),
    ("impulse", "impulse-capture"),
    ("social_intel", "social-key-person-intel"),
    ("daily_crypto", "daily-crypto-paper"),
    ("sunday_crypto", "sunday-crypto"),
)


def now_local() -> dt.datetime:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0)


def rel(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def file_type(path: Path) -> str:
    name = path.name
    for key, marker in REPORT_TYPE_MARKERS:
        if marker in name:
            return key
    if name.startswith("LATEST_") or name.startswith("AUTOMATION_"):
        return "fixed_operator_view"
    return "other"


def newest(paths: list[Path], limit: int) -> list[dict[str, Any]]:
    ordered = sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)
    rows = []
    for path in ordered[:limit]:
        stat = path.stat()
        rows.append(
            {
                "path": rel(path),
                "mtime": dt.datetime.fromtimestamp(stat.st_mtime, tz=LOCAL_TZ).isoformat(),
                "size_bytes": stat.st_size,
                "type": file_type(path),
            }
        )
    return rows


def automation_inventory() -> dict[str, Any]:
    files = list(AUTOMATION_ROOT.glob("*/automation.toml")) if AUTOMATION_ROOT.exists() else []
    entries = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        entry: dict[str, Any] = {"path": str(path)}
        for key in ("id", "kind", "name", "status", "rrule", "execution_environment"):
            marker = f'{key} = "'
            start = text.find(marker)
            if start == -1:
                continue
            start += len(marker)
            end = text.find('"', start)
            if end != -1:
                entry[key] = text[start:end]
        entries.append(entry)
    target = [item for item in entries if item.get("id") == TARGET_AUTOMATION_ID]
    active = [item for item in entries if item.get("status") == "ACTIVE"]
    return {
        "count": len(entries),
        "active_count": len(active),
        "target_present": bool(target),
        "target_status": target[0].get("status") if target else "missing",
        "consolidated": len(entries) == 1 and bool(target),
        "entries": entries,
    }


def directory_inventory(path: Path) -> dict[str, Any]:
    files = [item for item in path.glob("*") if item.is_file()] if path.exists() else []
    by_type = Counter(file_type(item) for item in files)
    total_size = sum(item.stat().st_size for item in files)
    return {
        "path": rel(path),
        "file_count": len(files),
        "total_size_bytes": total_size,
        "by_type": dict(sorted(by_type.items())),
        "latest": newest(files, 8),
    }


def build_payload() -> dict[str, Any]:
    reports = directory_inventory(ACTIVE_ROOT / "reports")
    experiments = directory_inventory(ACTIVE_ROOT / "experiments")
    handoffs = directory_inventory(ACTIVE_ROOT / "handoffs")
    paper_trades = directory_inventory(ACTIVE_ROOT / "paper_trades")
    automation = automation_inventory()
    return {
        "generated_at": now_local().isoformat(),
        "scope": "paper_only_artifact_inventory",
        "live_orders_enabled": False,
        "private_api_used": False,
        "automation": automation,
        "directories": {
            "reports": reports,
            "experiments": experiments,
            "handoffs": handoffs,
            "paper_trades": paper_trades,
        },
        "operator_policy": {
            "single_human_entrypoint": rel(ACTIVE_ROOT / "reports" / "LATEST_ACTIVE_ALPHA_STATUS.md"),
            "artifact_policy": "reports/experiments/handoffs are audit artifacts, not new scheduled jobs",
            "default_cleanup_mode": "index_only_no_delete_no_move",
            "physical_archive_requires_confirmation": True,
        },
    }


def render(payload: dict[str, Any]) -> str:
    automation = payload["automation"]
    dirs = payload["directories"]
    lines = [
        "# Active Alpha Artifact Index",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        "- scope: `paper_only_artifact_inventory`",
        "- live_orders_enabled: `false`",
        "- private_api_used: `false`",
        "",
        "## Automation Consolidation",
        "",
        f"- automation_toml_count: `{automation['count']}`",
        f"- active_automation_count: `{automation['active_count']}`",
        f"- target_present: `{automation['target_present']}`",
        f"- target_status: `{automation['target_status']}`",
        f"- consolidated: `{automation['consolidated']}`",
        "",
        "## Human Entry Point",
        "",
        f"- single_latest_dashboard: `{payload['operator_policy']['single_human_entrypoint']}`",
        "- rule: `看 LATEST_ACTIVE_ALPHA_STATUS.md；历史文件只作为审计证据。`",
        "",
        "## Artifact Counts",
        "",
        "| Directory | Files | Size bytes | Main types |",
        "|---|---:|---:|---|",
    ]
    for key in ("reports", "experiments", "handoffs", "paper_trades"):
        item = dirs[key]
        type_text = ", ".join(f"{name}:{count}" for name, count in item["by_type"].items()) or "-"
        lines.append(f"| `{item['path']}` | `{item['file_count']}` | `{item['total_size_bytes']}` | `{type_text}` |")
    lines.extend(["", "## Latest Reports", "", "| Type | Path | Modified |", "|---|---|---|"])
    for item in dirs["reports"]["latest"]:
        lines.append(f"| `{item['type']}` | `{item['path']}` | `{item['mtime']}` |")
    lines.extend(
        [
            "",
            "## Cleanup Policy",
            "",
            "- 当前模式是 `index_only_no_delete_no_move`，不会删除、移动或压缩任何审计证据。",
            "- 如果后续你确认要物理整理，可以把旧 reports 归档到月份目录；experiments 建议保留在原位，避免破坏样本追溯。",
            "- 任何真实交易、私有 API、提现、合约或杠杆权限仍保持关闭。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Write active-alpha automation artifact inventory.")
    parser.add_argument("--output", default=str(INDEX_REPORT))
    parser.add_argument("--json-output", default=str(INDEX_JSON))
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()

    payload = build_payload()
    report = Path(args.output)
    json_output = Path(args.json_output)
    report.parent.mkdir(parents=True, exist_ok=True)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render(payload), encoding="utf-8")
    with json_output.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if args.compact_output:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "report": rel(report),
                    "json": rel(json_output),
                    "automation_count": payload["automation"]["count"],
                    "consolidated": payload["automation"]["consolidated"],
                    "reports_count": payload["directories"]["reports"]["file_count"],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(f"Wrote {rel(report)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
