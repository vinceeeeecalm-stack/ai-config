#!/usr/bin/env python3
"""Repair stale downstream paper artifacts after a validation runner pass.

Paper-only orchestration:
- audits pipeline freshness
- reruns stale/missing downstream watchlist/retest samplers with explicit source paths
- writes a repair report and final freshness audit
- never calls private APIs or places live orders
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
EXPERIMENTS_DIR = ACTIVE_ROOT / "experiments"
REPORTS_DIR = ACTIVE_ROOT / "reports"
LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))

sys.path.insert(0, str(SCRIPT_DIR))
import pipeline_freshness_auditor as pfa  # noqa: E402


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def local_now() -> dt.datetime:
    return utc_now().astimezone(LOCAL_TZ)


def rel(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def latest(pattern: str) -> Path | None:
    files = sorted(EXPERIMENTS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def read_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def rows_by_name(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row.get("name")): row for row in record.get("checks") or [] if isinstance(row, dict)}


def needs_repair(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    return str(row.get("status")) in {"missing", "missing_source_ref", "stale_source", "upstream_stale"}


def parse_child_json(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                return {}
    return {}


def child_has_safety_error(payload: dict[str, Any]) -> bool:
    return any(
        payload.get(key) is True
        for key in ("live_orders_enabled", "private_api_used", "allow_real_orders", "private_api_keys_used")
    )


def run_child(label: str, command: list[str], timeout: int, dry_run: bool) -> dict[str, Any]:
    started = local_now()
    if dry_run:
        return {
            "label": label,
            "command": command,
            "status": "dry_run_skipped",
            "started_at": started.isoformat(),
            "completed_at": local_now().isoformat(),
            "returncode": None,
            "stdout_json": {},
            "live_orders_enabled": False,
            "private_api_used": False,
        }
    try:
        proc = subprocess.run(
            command,
            cwd=WORKSPACE_ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        payload = parse_child_json(proc.stdout)
        status = "ok" if proc.returncode == 0 else "failed"
        if child_has_safety_error(payload):
            status = "blocked_safety_error"
        return {
            "label": label,
            "command": command,
            "status": status,
            "started_at": started.isoformat(),
            "completed_at": local_now().isoformat(),
            "returncode": proc.returncode,
            "stdout_json": payload,
            "stdout_tail": proc.stdout[-1200:],
            "stderr_tail": proc.stderr[-1200:],
            "live_orders_enabled": bool(payload.get("live_orders_enabled") is True),
            "private_api_used": bool(payload.get("private_api_used") is True),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "label": label,
            "command": command,
            "status": "timeout",
            "started_at": started.isoformat(),
            "completed_at": local_now().isoformat(),
            "returncode": None,
            "stdout_tail": (exc.stdout or "")[-1200:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-1200:] if isinstance(exc.stderr, str) else "",
            "live_orders_enabled": False,
            "private_api_used": False,
        }


def repair_plan(initial: dict[str, Any]) -> list[dict[str, Any]]:
    rows = rows_by_name(initial)
    plan: list[dict[str, Any]] = []
    if needs_repair(rows.get("recovery_watchlist_monitor")):
        plan.append({"name": "recovery_watchlist_monitor", "reason": rows.get("recovery_watchlist_monitor", {}).get("status")})
    if needs_repair(rows.get("recovery_watchlist_paper_sampler")):
        plan.append({"name": "recovery_watchlist_paper_sampler", "reason": rows.get("recovery_watchlist_paper_sampler", {}).get("status")})
    if needs_repair(rows.get("top_blocked_candidate_retest_lab")):
        plan.append({"name": "top_blocked_candidate_retest_lab", "reason": rows.get("top_blocked_candidate_retest_lab", {}).get("status")})
    if needs_repair(rows.get("top_blocked_retest_quality_scout_sampler")):
        plan.append({"name": "top_blocked_retest_quality_scout_sampler", "reason": rows.get("top_blocked_retest_quality_scout_sampler", {}).get("status")})
    return plan


def build_record(args: argparse.Namespace) -> dict[str, Any]:
    created = local_now()
    initial = pfa.build_record()
    planned = repair_plan(initial)
    children: list[dict[str, Any]] = []

    names = {item["name"] for item in planned}
    if "recovery_watchlist_monitor" in names:
        children.append(
            run_child(
                "recovery_watchlist_monitor",
                [sys.executable, str(SCRIPT_DIR / "recovery_watchlist_monitor.py"), "--compact-output", "--max-candidates", str(args.max_recovery_candidates)],
                args.timeout_seconds,
                args.dry_run,
            )
        )
    if "recovery_watchlist_paper_sampler" in names or "recovery_watchlist_monitor" in names:
        watchlist_path = latest("*recovery-watchlist-monitor.json")
        command = [sys.executable, str(SCRIPT_DIR / "recovery_watchlist_paper_sampler.py"), "--compact-output"]
        if watchlist_path:
            command.extend(["--watchlist-json", str(watchlist_path)])
        children.append(
            run_child(
                "recovery_watchlist_paper_sampler",
                command,
                args.timeout_seconds,
                args.dry_run,
            )
        )
    if "top_blocked_candidate_retest_lab" in names:
        children.append(
            run_child(
                "top_blocked_candidate_retest_lab",
                [sys.executable, str(SCRIPT_DIR / "top_blocked_candidate_retest_lab.py"), "--compact-output"],
                args.timeout_seconds,
                args.dry_run,
            )
        )
    if "top_blocked_retest_quality_scout_sampler" in names or "top_blocked_candidate_retest_lab" in names:
        retest_path = latest("*top-blocked-candidate-retest-lab.json")
        command = [sys.executable, str(SCRIPT_DIR / "top_blocked_retest_quality_scout_sampler.py"), "--compact-output"]
        if retest_path:
            command.extend(["--retest-json", str(retest_path)])
        children.append(
            run_child(
                "top_blocked_retest_quality_scout_sampler",
                command,
                args.timeout_seconds,
                args.dry_run,
            )
        )

    final = pfa.build_record()
    safety_block = any(child.get("status") == "blocked_safety_error" for child in children)
    child_failures = [child for child in children if child.get("status") in {"failed", "timeout", "blocked_safety_error"}]
    status = "ok"
    if safety_block:
        status = "blocked_safety_error"
    elif child_failures:
        status = "degraded_child_failure"
    elif (final.get("summary") or {}).get("stale_count", 0) or (final.get("summary") or {}).get("missing_count", 0):
        status = "warn_unrepaired"
    return {
        "run_id": f"{created.strftime('%Y%m%d-%H%M%S')}-pipeline-freshness-repair-runner",
        "created_at": created.isoformat(),
        "scope": "paper_only_pipeline_freshness_repair",
        "status": status,
        "dry_run": bool(args.dry_run),
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "ledger_mutated": any((child.get("stdout_json") or {}).get("ledger_mutated") is True for child in children),
        "initial_freshness": {
            "status": initial.get("status"),
            "summary": initial.get("summary"),
            "next_actions": initial.get("next_actions"),
        },
        "repair_plan": planned,
        "children": children,
        "final_freshness": {
            "status": final.get("status"),
            "summary": final.get("summary"),
            "next_actions": final.get("next_actions"),
        },
        "outputs": {},
    }


def render_report(record: dict[str, Any]) -> str:
    lines = [
        f"# Pipeline Freshness Repair Runner | {record['run_id']}",
        "",
        "- scope: `paper_only_pipeline_freshness_repair`",
        "- live_orders_enabled: `false`",
        "- private_api_used: `false`",
        "- allow_real_orders: `false`",
        f"- ledger_mutated: `{str(record.get('ledger_mutated')).lower()}`",
        f"- status: `{record.get('status')}`",
        "",
        "## Freshness",
        "",
        f"- initial: `{(record.get('initial_freshness') or {}).get('status')}` {(record.get('initial_freshness') or {}).get('summary')}",
        f"- final: `{(record.get('final_freshness') or {}).get('status')}` {(record.get('final_freshness') or {}).get('summary')}",
        "",
        "## Repair Plan",
        "",
    ]
    if record.get("repair_plan"):
        for item in record.get("repair_plan") or []:
            lines.append(f"- `{item.get('name')}` because `{item.get('reason')}`")
    else:
        lines.append("- No repair needed.")
    lines.extend(["", "## Child Runs", "", "| Label | Status | Opened | Blocked | Ledger Mutated | Output |", "|---|---|---:|---:|---|---|"])
    for child in record.get("children") or []:
        payload = child.get("stdout_json") or {}
        outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
        lines.append(
            f"| `{child.get('label')}` | `{child.get('status')}` | "
            f"`{payload.get('opened_count', '-')}` | `{payload.get('blocked_count', '-')}` | "
            f"`{payload.get('ledger_mutated', '-')}` | `{outputs.get('report') or '-'}` |"
        )
    if not record.get("children"):
        lines.append("| - | - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Rule",
            "",
            "This runner only repairs freshness by rerunning paper-only downstream reports/samplers with explicit latest source artifacts. It cannot bypass trigger, liquidity, capacity, recovery or safety gates.",
        ]
    )
    return "\n".join(lines) + "\n"


def self_test() -> dict[str, Any]:
    fake = {
        "checks": [
            {"name": "recovery_watchlist_monitor", "status": "synced"},
            {"name": "recovery_watchlist_paper_sampler", "status": "stale_source"},
            {"name": "top_blocked_candidate_retest_lab", "status": "synced"},
            {"name": "top_blocked_retest_quality_scout_sampler", "status": "upstream_stale"},
            {"name": "paper_capital_allocation_auditor", "status": "present"},
        ]
    }
    plan = repair_plan(fake)
    labels = [item["name"] for item in plan]
    with tempfile.TemporaryDirectory(prefix="pipeline-freshness-repair-", dir="/private/tmp") as tmp:
        tmp_path = Path(tmp)
        test_report = tmp_path / "report.md"
        write_text(test_report, render_report({
            "run_id": "self-test",
            "status": "ok",
            "ledger_mutated": False,
            "initial_freshness": {"status": "warn", "summary": {"stale_count": 2}},
            "final_freshness": {"status": "pass", "summary": {"stale_count": 0}},
            "repair_plan": plan,
            "children": [],
        }))
        assert test_report.exists()
    assert labels == ["recovery_watchlist_paper_sampler", "top_blocked_retest_quality_scout_sampler"], labels
    return {
        "status": "ok",
        "planned_stale_downstream_samplers": labels,
        "uses_temporary_files_only": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair stale downstream paper artifacts after latest validation runner.")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--max-recovery-candidates", type=int, default=6)
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    record = build_record(args)
    stamp = record["run_id"].removesuffix("-pipeline-freshness-repair-runner")
    date = local_now().strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"{date}-pipeline-freshness-repair-{stamp}.md"
    experiment_path = EXPERIMENTS_DIR / f"{stamp}-pipeline-freshness-repair-runner.json"
    record["outputs"] = {"report": rel(report_path), "experiment": rel(experiment_path)}
    write_json(experiment_path, record)
    write_text(report_path, render_report(record))
    if args.compact_output:
        print(json.dumps({
            "status": record.get("status"),
            "run_id": record.get("run_id"),
            "initial_freshness": record.get("initial_freshness"),
            "final_freshness": record.get("final_freshness"),
            "child_count": len(record.get("children") or []),
            "ledger_mutated": record.get("ledger_mutated"),
            "live_orders_enabled": False,
            "private_api_used": False,
            "outputs": record.get("outputs"),
        }, ensure_ascii=False, indent=2))
    else:
        print(render_report(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
