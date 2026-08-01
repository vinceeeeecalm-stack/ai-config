#!/usr/bin/env python3
"""Read-only readiness audit for the single hourly $100 sports automation."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUTOMATIONS = Path("/Users/vincentpan/.codex/automations")
TARGET_ID = "polymarket-paper-validation-cycle"
ENTRY = ROOT / "scripts/polymarket_100usd_scheduler_entrypoint.py"
POLICY = ROOT / "config/sports_100usd_policy.json"
RUNTIME = ROOT / "data/sports_100usd_runtime"
DRY_RUN = ROOT / "reports/sports_100usd_paper_daily/2026-07-17_202915_BJT_ISOLATED_DRY_RUN.json"
ABSENT_REPORT = ROOT / "reports/sports_100usd_paper_daily/2026-07-17_202916_BJT_MUST_NOT_EXIST.json"
LEDGER_100 = ROOT / "data/sports_100usd_paper_ledger.json"
LEDGER_500 = ROOT / "data/paper_ledger.json"


def flat_toml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if " = " not in line:
            continue
        key, raw = line.split(" = ", 1)
        if raw.startswith('"') and raw.endswith('"'):
            try:
                data[key] = json.loads(raw)
            except json.JSONDecodeError:
                data[key] = raw[1:-1]
        else:
            data[key] = raw
    return data


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def run_tests() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
        cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    return {"passed": result.returncode == 0, "returncode": result.returncode, "output_tail": result.stdout[-2000:]}


def check(rows: list[dict[str, Any]], name: str, passed: bool, evidence: Any) -> None:
    rows.append({"name": name, "passed": bool(passed), "evidence": evidence})


def lock_clearance(target: dict[str, Any], active_lock: dict[str, Any] | None) -> dict[str, Any]:
    """Treat a bounded lock as healthy when it must expire before the next hourly slot."""
    if not active_lock:
        return {"safe": True, "reason": "no_active_lock", "next_occurrence": None, "lock_expires": None}
    try:
        created_at = datetime.fromtimestamp(int(target["created_at"]) / 1000, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        elapsed_hours = max(0, int((now - created_at).total_seconds() // 3600) + 1)
        next_occurrence = created_at + timedelta(hours=elapsed_hours)
        started_at = datetime.fromisoformat(str(active_lock["started_at"]).replace("Z", "+00:00"))
        lock_expires = started_at + timedelta(seconds=int(active_lock["max_seconds"]))
        safe = lock_expires + timedelta(seconds=120) <= next_occurrence
        return {
            "safe": safe,
            "reason": "bounded_lock_clears_before_next_hourly_slot" if safe else "lock_can_overlap_next_hourly_slot",
            "next_occurrence": next_occurrence.isoformat(),
            "lock_expires": lock_expires.isoformat(),
            "safety_margin_seconds": 120,
        }
    except (KeyError, TypeError, ValueError) as exc:
        return {"safe": False, "reason": "unparseable_lock_or_schedule", "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-target-status", choices=("PAUSED", "ACTIVE"), required=True)
    args = parser.parse_args()
    ledger_baseline = {
        "ledger_100_sha256": sha256(LEDGER_100),
        "ledger_100_mtime_epoch": LEDGER_100.stat().st_mtime,
        "ledger_500_sha256": sha256(LEDGER_500),
        "ledger_500_mtime_epoch": LEDGER_500.stat().st_mtime,
    }
    checks: list[dict[str, Any]] = []
    automations = {
        path.parent.name: flat_toml(path)
        for path in AUTOMATIONS.glob("*/automation.toml")
    }
    target = automations.get(TARGET_ID, {})
    other_active_broad_scans = sorted(
        key for key, value in automations.items()
        if key != TARGET_ID
        and value.get("status") == "ACTIVE"
        and value.get("rrule") == "FREQ=HOURLY;INTERVAL=1"
    )
    non_conflicting_active_jobs = sorted(
        key for key, value in automations.items()
        if key != TARGET_ID
        and value.get("status") == "ACTIVE"
        and key not in other_active_broad_scans
    )
    prompt = str(target.get("prompt") or "")
    prompt_markers = (
        "polymarket_100usd_scheduler_entrypoint.py", " start --scheduler-trigger-id",
        "commit-report", "NO_STATE_CHANGE",
        "config/sports_100usd_policy.json", "HIGH_CONFIDENCE", "net_EV>=3c", "net_EV>=5c",
        "20或30分钟", "candidate_id",
    )
    check(checks, "single_hourly_target", target.get("rrule") == "FREQ=HOURLY;INTERVAL=1"
          and target.get("status") == args.expected_target_status and not other_active_broad_scans,
          {"target_status": target.get("status"), "rrule": target.get("rrule"),
           "other_active_broad_scans": other_active_broad_scans,
           "non_conflicting_active_jobs": non_conflicting_active_jobs})
    check(checks, "scheduler_prompt_uses_real_entrypoint", all(marker in prompt for marker in prompt_markers),
          {"missing_markers": [marker for marker in prompt_markers if marker not in prompt]})

    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    high = policy["lanes"]["high_confidence"]
    value = policy["lanes"]["value"]
    exploration = policy["lanes"]["exploration_micro"]
    policy_ok = (
        high["minimum_calibrated_p_exec"] == .8
        and high["minimum_research_probability_lower_bound"] == .8
        and high["minimum_net_ev_cents"] == 3
        and value["minimum_calibrated_p_exec"] == .6
        and value["minimum_net_ev_cents"] == 5
        and exploration["minimum_calibrated_p_exec"] == .7
        and exploration["minimum_net_ev_cents"] == -4
        and exploration["maximum_ask_cents"] == 90
        and exploration["stake_usd"] == 1
        and exploration["maximum_entries_per_bjt_day"] == 2
        and exploration["counts_as_formal_recommendation"] is False
        and policy["scheduler"]["default_active_broad_scan_count"] == 1
        and policy["scheduler"]["adaptive_candidate_interval_minutes"] == [20, 30]
        and policy["paper_only"] is True and policy["live_orders_enabled"] is False
    )
    check(checks, "hit_rate_and_value_policy", policy_ok, {"policy": str(POLICY)})

    runtime_status = subprocess.run(
        [sys.executable, str(ENTRY), "--state-dir", str(RUNTIME), "status"],
        cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    runtime = json.loads(runtime_status.stdout) if runtime_status.returncode == 0 else {}
    clearance = lock_clearance(target, runtime.get("active_lock"))
    check(checks, "guarded_dry_run_and_no_state_change",
          clearance["safe"] and runtime.get("consumed_trigger_count") >= 2
          and runtime.get("report_committed_count") >= 1 and runtime.get("no_state_change_count") >= 1
          and DRY_RUN.exists() and not ABSENT_REPORT.exists(),
          {"runtime": runtime, "lock_clearance": clearance, "dry_run": str(DRY_RUN),
           "duplicate_report_absent": not ABSENT_REPORT.exists()})

    preflight = json.loads(DRY_RUN.read_text(encoding="utf-8")) if DRY_RUN.exists() else {}
    check(checks, "clob_and_score_preflight",
          preflight.get("status") == "PASS" and (preflight.get("clob") or {}).get("complete") is True
          and (preflight.get("score_freshness") or {}).get("complete") is True
          and preflight.get("candidate_entry_authorized") is False,
          {"status": preflight.get("status"), "clob": (preflight.get("clob") or {}).get("complete"),
           "score": (preflight.get("score_freshness") or {}).get("complete"),
           "entry_authorized": preflight.get("candidate_entry_authorized")})

    ledger_current = {
        "ledger_100_sha256": sha256(LEDGER_100),
        "ledger_100_mtime_epoch": LEDGER_100.stat().st_mtime,
        "ledger_500_sha256": sha256(LEDGER_500),
        "ledger_500_mtime_epoch": LEDGER_500.stat().st_mtime,
    }
    ledger_ok = ledger_current == ledger_baseline
    check(checks, "both_trading_ledgers_unchanged", ledger_ok,
          {"baseline": ledger_baseline, "current": ledger_current,
           "note": "This read-only readiness audit must not modify either trading ledger."})

    tests = run_tests()
    check(checks, "full_test_suite", tests["passed"], tests)
    failed = [row["name"] for row in checks if not row["passed"]]
    status = "RESTORE_READY" if not failed else "RESTORE_BLOCKED"
    payload = {
        "schema_version": "sports-100usd-restore-readiness-v1",
        "status": status,
        "expected_target_status": args.expected_target_status,
        "checks_passed": len(checks) - len(failed),
        "checks_total": len(checks),
        "failed_checks": failed,
        "checks": checks,
        "default_active_sports_scheduler_count": 1 if args.expected_target_status == "ACTIVE" else 0,
        "adaptive_monitor_policy": "candidate-bound, 20/30 minutes, auto-expiring, max two",
        "paper_only": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "real_money_execution_authorized": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status == "RESTORE_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
