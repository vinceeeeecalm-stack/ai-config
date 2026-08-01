#!/usr/bin/env python3
"""Read-only closure audit for the paused $100 sports-paper repair.

The audit never scans markets, writes a ledger, changes an automation, or
creates a run.  It distinguishes a completed repair from permission to resume:
REPAIR_COMPLETE_PAUSED still carries explicit restore blockers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SKILL = Path("/Users/vincentpan/.codex/skills/active-sports-paper-monitor")
SKILL_CREATOR = Path("/Users/vincentpan/.codex/skills/.system/skill-creator")
AUTOMATIONS = Path("/Users/vincentpan/.codex/automations")
ERROR_BOOK = ROOT / "data/sports_error_book"
RUNTIME = ROOT / "data/sports_100usd_runtime"
GOAL = ROOT / "reports/CURRENT_SPORTS_PAPER_REPAIR_GOAL.md"
LEDGER_100 = ROOT / "data/sports_100usd_paper_ledger.json"
LEDGER_500 = ROOT / "data/paper_ledger.json"
MISSED_REVIEWS = ROOT / "data/sports_100usd_missed_candidate_reviews.jsonl"

SAFE_FLAGS = {
    "paper_only": True,
    "live_orders_enabled": False,
    "private_api_used": False,
    "real_money_execution_authorized": False,
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], cwd: Path = ROOT, timeout: int = 180) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        output = completed.stdout.strip()
        return {
            "passed": completed.returncode == 0,
            "returncode": completed.returncode,
            "output_tail": output[-3000:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"passed": False, "returncode": None, "output_tail": str(exc)}


def parse_json_output(result: dict[str, Any]) -> dict[str, Any] | None:
    if not result["passed"]:
        return None
    try:
        return json.loads(result["output_tail"])
    except json.JSONDecodeError:
        return None


def check(checks: list[dict[str, Any]], name: str, passed: bool,
          evidence: Any, required: bool = True) -> None:
    checks.append({
        "name": name,
        "required": required,
        "passed": bool(passed),
        "evidence": evidence,
    })


def load_toml(path: Path) -> dict[str, Any]:
    """Read the flat scalar fields used by Codex automation TOML files.

    The desktop system Python may be 3.9 (before stdlib tomllib), and closure
    auditing must not add a package dependency. Automation files used here are
    flat key/value documents, so JSON decoding quoted TOML strings is enough.
    """
    data: dict[str, Any] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if value.startswith('"') and value.endswith('"'):
            try:
                data[key] = json.loads(value)
            except json.JSONDecodeError:
                data[key] = value[1:-1]
        elif value in {"true", "false"}:
            data[key] = value == "true"
        else:
            try:
                data[key] = int(value)
            except ValueError:
                data[key] = value
    return data


def active_sports_automations() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not AUTOMATIONS.exists():
        return rows
    for path in sorted(AUTOMATIONS.glob("*/automation.toml")):
        data = load_toml(path)
        if data.get("id") == "polymarket-100-paper" or data.get("status") != "ACTIVE":
            continue
        searchable = " ".join(str(data.get(key, "")) for key in ("id", "name", "prompt")).lower()
        if any(term in searchable for term in ("sport", "体育", "polymarket", "tennis", "football")):
            rows.append({
                "id": str(data.get("id")),
                "name": str(data.get("name")),
                "status": str(data.get("status")),
            })
    return rows


def matching_processes() -> list[str]:
    result = run(["ps", "-axo", "pid=,command="], timeout=10)
    if not result["passed"]:
        return ["PROCESS_LIST_UNAVAILABLE"]
    needles = (
        "polymarket_daily_sports_report.py",
        "polymarket_periodic_review.py",
        "record_20260717_priority_sports_research.py",
        "sports_run_guard.py start",
        "polymarket-100-paper",
    )
    return [
        line.strip() for line in result["output_tail"].splitlines()
        if any(needle in line for needle in needles)
    ]


def main() -> int:
    checks: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    goal_text = GOAL.read_text(encoding="utf-8") if GOAL.exists() else ""
    check(
        checks,
        "finite_integrated_goal",
        all(marker in goal_text for marker in (
            "## 收口条件", "REPAIR_COMPLETE_PAUSED", "REPAIR_INCOMPLETE_PAUSED",
            "不得自恢复", "机器可读 closure audit",
        )),
        {"path": str(GOAL), "exists": GOAL.exists()},
    )

    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    reference = SKILL / "references/operations-and-learning-loop.md"
    required_skill_markers = (
        "references/operations-and-learning-loop.md",
        "DUPLICATE_RUN_BLOCKED",
        "NO_STATE_CHANGE",
        "PROCESS_INCIDENT",
        "UNKNOWN",
        "sports_error_book",
        "sports_run_guard.py",
    )
    check(
        checks,
        "skill_hard_rules_and_reference",
        reference.exists() and all(marker in skill_text for marker in required_skill_markers),
        {
            "skill": str(SKILL / "SKILL.md"),
            "reference": str(reference),
            "missing_markers": [m for m in required_skill_markers if m not in skill_text],
        },
    )

    skill_validation = run([
        sys.executable,
        str(SKILL_CREATOR / "scripts/quick_validate.py"),
        str(SKILL),
    ])
    check(checks, "skill_yaml_and_structure_validation", skill_validation["passed"], skill_validation)

    book_validation = run([
        sys.executable,
        str(SKILL / "scripts/learning_loop_validator.py"),
        "--root", str(ERROR_BOOK),
        "--skill", str(SKILL),
        "validate",
    ])
    book_payload = parse_json_output(book_validation)
    minimum_counts = {"common": 8, "football": 3, "tennis": 4}
    counts = (book_payload or {}).get("counts", {})
    books_pass = bool(
        book_payload
        and book_payload.get("status") == "pass"
        and all(int(counts.get(track, 0)) >= minimum for track, minimum in minimum_counts.items())
        and book_payload.get("total_records") == book_payload.get("unique_ids")
    )
    check(checks, "append_only_error_books", books_pass, book_payload or book_validation)

    common_text = (ERROR_BOOK / "common.jsonl").read_text(encoding="utf-8")
    check(
        checks,
        "legacy_counts_are_unverified",
        "459" in common_text and "UNVERIFIED" in common_text and "2" in common_text,
        {
            "book": str(ERROR_BOOK / "common.jsonl"),
            "rule": "2 heartbeat / 459 continuation is retained only as UNVERIFIED legacy claim",
        },
    )

    run_guard_tests = run([
        sys.executable,
        str(SKILL / "scripts/sports_run_guard_test.py"),
    ])
    check(checks, "run_guard_tests", run_guard_tests["passed"], run_guard_tests)

    learning_tests = run([
        sys.executable,
        str(SKILL / "scripts/learning_loop_validator_test.py"),
    ])
    check(checks, "learning_loop_tests", learning_tests["passed"], learning_tests)

    result_gate = run([
        sys.executable,
        str(ROOT / "scripts/polymarket_sports_result_gate.py"),
        "--input", str(MISSED_REVIEWS),
    ])
    result_payload = parse_json_output(result_gate)
    result_pass = bool(
        result_payload
        and result_payload.get("unknown_excluded_from_all_result_metrics") is True
        and result_payload.get("paper_only") is True
        and result_payload.get("live_orders_enabled") is False
        and result_payload.get("private_api_used") is False
        and result_payload.get("real_money_execution_authorized") is False
        and result_payload.get("price_edge_calibration_eligible") == 0
        and result_payload.get("exit_calibration_eligible") == 0
    )
    result_summary = None if not result_payload else {
        key: value for key, value in result_payload.items()
        if key not in {"rows", "unknown_rows"}
    }
    check(checks, "result_verification_gate", result_pass, result_summary or result_gate)

    project_tests = run([
        sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py",
    ], timeout=300)
    check(checks, "project_test_suite", project_tests["passed"], project_tests)

    automation_path = AUTOMATIONS / "polymarket-100-paper/automation.toml"
    automation = load_toml(automation_path) if automation_path.exists() else {}
    check(
        checks,
        "target_automation_paused",
        automation.get("id") == "polymarket-100-paper" and automation.get("status") == "PAUSED",
        {"path": str(automation_path), "id": automation.get("id"), "status": automation.get("status")},
    )

    active_lock = RUNTIME / "active_run.lock.json"
    processes = matching_processes()
    check(
        checks,
        "no_active_100usd_run_lock_or_process",
        not active_lock.exists() and not processes,
        {"active_lock": str(active_lock) if active_lock.exists() else None, "matching_processes": processes},
    )

    ledger = json.loads(LEDGER_100.read_text(encoding="utf-8"))
    account = ledger.get("account", {})
    ledger_pass = (
        account.get("cash_usd") == 100.0
        and account.get("equity_usd") == 100.0
        and account.get("open_position_count") == 0
        and account.get("closed_paper_count") == 0
        and all(account.get(key) is value for key, value in SAFE_FLAGS.items())
    )
    check(
        checks,
        "paper_account_safe_and_unchanged_state",
        ledger_pass,
        {
            "path": str(LEDGER_100),
            "cash_usd": account.get("cash_usd"),
            "equity_usd": account.get("equity_usd"),
            "open_position_count": account.get("open_position_count"),
            "closed_paper_count": account.get("closed_paper_count"),
            "safety_flags": {key: account.get(key) for key in SAFE_FLAGS},
        },
    )

    goal_mtime = GOAL.stat().st_mtime if GOAL.exists() else 0
    ledgers_predate_goal = (
        LEDGER_100.exists() and LEDGER_500.exists()
        and LEDGER_100.stat().st_mtime < goal_mtime
        and LEDGER_500.stat().st_mtime < goal_mtime
    )
    check(
        checks,
        "trading_ledgers_not_modified_by_repair",
        ledgers_predate_goal,
        {
            "goal_mtime": datetime.fromtimestamp(goal_mtime).astimezone().isoformat(timespec="seconds"),
            "ledger_100": {
                "mtime": datetime.fromtimestamp(LEDGER_100.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
                "sha256": sha256(LEDGER_100),
            },
            "ledger_500": {
                "mtime": datetime.fromtimestamp(LEDGER_500.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
                "sha256": sha256(LEDGER_500),
            },
        },
    )

    overlaps = active_sports_automations()
    if overlaps:
        warnings.append({
            "code": "OTHER_ACTIVE_SPORTS_OR_POLYMARKET_AUTOMATIONS",
            "blocking_repair_closure": False,
            "blocking_automatic_restore": True,
            "automations": overlaps,
        })

    required = [row for row in checks if row["required"]]
    failed = [row for row in required if not row["passed"]]
    status = "REPAIR_COMPLETE_PAUSED" if not failed else "REPAIR_INCOMPLETE_PAUSED"
    payload = {
        "schema_version": "polymarket-sports-repair-closure-audit-v1",
        "created_at": now_iso(),
        "status": status,
        "repair_goal_closed_if_complete": not failed,
        "automation_restore_authorized": False,
        "required_checks": len(required),
        "required_passed": len(required) - len(failed),
        "required_failed": len(failed),
        "failed_check_names": [row["name"] for row in failed],
        "checks": checks,
        "warnings": warnings,
        "restore_blockers": [
            "用户明确批准恢复自动化",
            "确认或暂停其他 ACTIVE 的体育/Polymarket 自动化，避免重复调度",
            "把 sports_run_guard 接入实际 scheduler 入口，而不只停留在 Skill 规则和单元测试",
            "联网完成 CLOB token/orderbook/比分新鲜度 preflight，失败必须 WAIT/PASS",
            "验证 NO_STATE_CHANGE 单报告前向行为，不产生重复 Markdown",
            "第一次恢复只运行一个隔离 dry-run；不得下单或补造 fill",
        ],
        **SAFE_FLAGS,
        "main_500usd_ledger_in_scope": False,
        "market_scan_performed": False,
        "paper_fill_created": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status == "REPAIR_COMPLETE_PAUSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
