#!/usr/bin/env python3
"""Isolated public-data discovery cycle for the $100 sports paper workflow.

The general validation cycle owns the separate $500 paper ledger.  This entry
point deliberately uses an ephemeral compatible ledger for market discovery,
writes only sports research/observation artifacts, and fails closed if either
trading ledger changes during the scan.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

import polymarket_alpha as core


ROOT = Path(__file__).resolve().parents[1]
MAIN_LEDGER = ROOT / "data/paper_ledger.json"
SPORTS_LEDGER = ROOT / "data/sports_100usd_paper_ledger.json"
OUTPUT = ROOT / "experiments/current-sports-100usd-hourly-scan.json"
REPORT = ROOT / "reports/CURRENT_SPORTS_100USD_HOURLY_SCAN.md"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_step(name: str, command: list[str], timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=timeout, check=False,
        )
        return {
            "name": name,
            "status": "ok" if result.returncode == 0 else "failed",
            "returncode": result.returncode,
            "duration_seconds": time.monotonic() - started,
            "output_tail": result.stdout[-3000:],
        }
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return {
            "name": name, "status": "timeout", "returncode": None,
            "duration_seconds": time.monotonic() - started,
            "output_tail": output[-3000:],
        }


def scan_commands(python: str, temporary_ledger: Path, runner_timeout: float) -> list[tuple[str, list[str], float]]:
    snapshot = ROOT / "cache/current_sports_100usd_snapshot"
    return [
        (
            "public_market_runner_dry_run",
            [
                python, "scripts/polymarket_runner.py", "--dry-run", "--ledger", str(temporary_ledger),
                "--max-markets", "0", "--book-limit", "50", "--keyset-max-pages", "30",
                "--keyset-wall-clock-seconds", "90", "--snapshot-dir", str(snapshot),
                "--output", "experiments/current-sports-100usd-runner.json",
                "--report", "reports/CURRENT_SPORTS_100USD_RUNNER.md",
            ],
            runner_timeout,
        ),
        (
            "sports_daily_priority",
            [
                python, "scripts/polymarket_daily_priority.py", "--snapshot-dir", str(snapshot),
                "--paper-ledger", str(temporary_ledger),
                "--scan-ledger", "data/sports_100usd_discovery_scan_ledger.json",
                "--output", "experiments/current-daily-priority-cycle.json",
                "--report", "reports/CURRENT_DAILY_PRIORITY.md",
            ],
            240,
        ),
        (
            "sports_decision_report",
            [
                python, "scripts/polymarket_daily_sports_report.py",
                "--research", "experiments/current-sports-event-research.json",
                "--output", "experiments/current-daily-sports-decision.json",
                "--report", "reports/CURRENT_DAILY_SPORTS_DECISION.md",
                "--ledger", "data/sports_decision_observation_ledger.json",
            ],
            240,
        ),
        ("sports_acceptance", [python, "scripts/polymarket_daily_sports_acceptance.py"], 30),
    ]


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Sports $100 Hourly Scan", "",
        f"- Status: `{payload['status']}`",
        f"- Created: `{payload['created_at']}`",
        f"- Markets scanned: {payload.get('markets_scanned')}",
        f"- Sports events: {payload.get('sports_events')}",
        f"- Formal recommendations: {payload.get('formal_recommendations')}",
        f"- Conditional candidates: {payload.get('conditional_candidates')}",
        f"- $500 ledger unchanged: `{payload['main_ledger_unchanged']}`",
        f"- $100 ledger unchanged by discovery: `{payload['sports_ledger_unchanged']}`", "",
        "| Step | Status | Seconds |", "|---|---|---:|",
    ]
    lines.extend(
        f"| {row['name']} | {row['status']} | {row['duration_seconds']:.2f} |"
        for row in payload["steps"]
    )
    lines.extend(["", "This workflow is paper-only and does not place real orders.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-timeout-seconds", type=float, default=300)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    python = sys.executable
    if args.self_test:
        commands = scan_commands(python, Path("/tmp/isolated-sports-ledger.json"), 300)
        serialized = "\n".join(" ".join(command) for _, command, _ in commands)
        payload = {
            "status": "pass",
            "main_ledger_not_routed_to_children": str(MAIN_LEDGER) not in serialized and "data/paper_ledger.json" not in serialized,
            "sports_ledger_not_routed_to_discovery_children": str(SPORTS_LEDGER) not in serialized,
            "paper_only": True, "live_orders_enabled": False,
            "private_api_used": False, "real_money_execution_authorized": False,
        }
        payload["status"] = "pass" if all(
            payload[key] is True for key in (
                "main_ledger_not_routed_to_children", "sports_ledger_not_routed_to_discovery_children",
                "paper_only",
            )
        ) else "fail"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["status"] == "pass" else 1

    started = time.monotonic()
    main_before, sports_before = sha256(MAIN_LEDGER), sha256(SPORTS_LEDGER)
    steps: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="sports-100usd-hourly-") as temporary:
        temporary_ledger = Path(temporary) / "paper_ledger.json"
        policy = core.read_json(ROOT / "config/policy.json")
        ledger = core.new_ledger(policy)
        for key in ("initial_equity_usd", "cash_usd", "equity_usd", "high_watermark_usd"):
            ledger[key] = 100.0
        core.write_json(temporary_ledger, ledger)
        for name, command, timeout in scan_commands(python, temporary_ledger, args.runner_timeout_seconds):
            row = run_step(name, command, timeout)
            steps.append(row)
            if row["status"] != "ok":
                break

    main_after, sports_after = sha256(MAIN_LEDGER), sha256(SPORTS_LEDGER)
    runner_path = ROOT / "experiments/current-sports-100usd-runner.json"
    decision_path = ROOT / "experiments/current-daily-sports-decision.json"
    runner = core.read_json(runner_path) if runner_path.exists() else {}
    decision = core.read_json(decision_path) if decision_path.exists() else {}
    ledgers_safe = main_before == main_after and sports_before == sports_after
    steps_ok = len(steps) == 4 and all(row["status"] == "ok" for row in steps)
    status = "ok" if steps_ok and ledgers_safe else "PROCESS_INCIDENT"
    payload = {
        "schema_version": "sports-100usd-hourly-scan-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "duration_seconds": time.monotonic() - started,
        "steps": steps,
        "markets_scanned": (runner.get("scan") or {}).get("markets_scanned"),
        "sports_events": decision.get("scanned_event_count"),
        "formal_recommendations": len(decision.get("main_recommendations") or []),
        "conditional_candidates": len(decision.get("conditional_candidates") or []),
        "main_ledger_sha256_before": main_before,
        "main_ledger_sha256_after": main_after,
        "main_ledger_unchanged": main_before == main_after,
        "sports_ledger_sha256_before": sports_before,
        "sports_ledger_sha256_after": sports_after,
        "sports_ledger_unchanged": sports_before == sports_after,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        "real_money_execution_authorized": False,
    }
    atomic_json(OUTPUT, payload)
    REPORT.write_text(markdown(payload), encoding="utf-8")
    print(json.dumps({
        key: payload[key] for key in (
            "status", "duration_seconds", "markets_scanned", "sports_events",
            "formal_recommendations", "conditional_candidates",
            "main_ledger_unchanged", "sports_ledger_unchanged",
        )
    }, ensure_ascii=False, indent=2))
    return 0 if status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
