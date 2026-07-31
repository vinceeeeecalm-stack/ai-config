#!/usr/bin/env python3
"""Lightweight shared-lock cycle for fixed-window Polymarket research shadows."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DOMAIN_OUTPUTS = {
    "weather": ROOT / "experiments/current-weather-shadow-cycle.json",
    "football": ROOT / "experiments/current-football-shadow-cycle.json",
    "social_count": ROOT / "experiments/current-social-count-shadow-cycle.json",
    "stock_weekly": ROOT / "experiments/current-stock-weekly-shadow-cycle.json",
}
PROTECTED = [ROOT / "data/paper_ledger.json", ROOT / "experiments/current-crypto-barrier-estimates.json"]
RETRY_CADENCE_MINUTES = 30
FULL_CYCLE_CADENCE_MINUTES = 60


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


validation = load("deadline_validation", ROOT / "scripts/polymarket_validation_cycle.py")
core = load("deadline_core", ROOT / "scripts/polymarket_alpha.py")


def file_sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def window_bounds(domain: str, row: dict[str, Any]):
    cutoff = core.parse_iso(row.get("cutoff_at"))
    start = core.parse_iso(row.get("capture_window_start_at"))
    end = core.parse_iso(row.get("capture_window_end_at"))
    if domain == "football":
        return cutoff, end
    return start, end or cutoff


def deadline_inventory(outputs: dict[str, dict[str, Any]], now: datetime,
                       full_cycle_last_completed_at: datetime | None = None) -> dict[str, Any]:
    upcoming: dict[tuple[Any, ...], dict[str, Any]] = {}
    active_unrecorded: dict[tuple[Any, ...], dict[str, Any]] = {}
    for domain, payload in outputs.items():
        for row in payload.get("excluded", []):
            start, end = window_bounds(domain, row)
            if start is None or end is None or end <= now:
                continue
            identity = row.get("event_id") or row.get("tracking_id") or row.get("condition_id")
            key = (domain, identity, start.isoformat(), end.isoformat())
            item = {
                "domain": domain, "identity": identity, "reason": row.get("reason"),
                "capture_window_start_at": start.isoformat(), "capture_window_end_at": end.isoformat(),
                "seconds_until_start": max(0.0, (start - now).total_seconds()),
                "seconds_until_end": (end - now).total_seconds(),
            }
            if start <= now < end and row.get("reason") != "forecast_already_recorded":
                deadline_retry = now + timedelta(minutes=RETRY_CADENCE_MINUTES)
                full_retry = (full_cycle_last_completed_at + timedelta(minutes=FULL_CYCLE_CADENCE_MINUTES)
                              if full_cycle_last_completed_at is not None else None)
                candidates = [
                    {"source": "deadline_shadow", "expected_at": deadline_retry},
                    *([{"source": "full_validation", "expected_at": full_retry}]
                      if full_retry is not None and full_retry > now else []),
                ]
                candidates = [candidate for candidate in candidates if candidate["expected_at"] < end]
                candidates.sort(key=lambda candidate: candidate["expected_at"])
                next_retry = candidates[0] if candidates else None
                item.update({
                    "missing_market_count": row.get("missing_market_count"),
                    "missing_live_books": row.get("missing_live_books", []),
                    "retry_cadence_minutes": RETRY_CADENCE_MINUTES,
                    "next_expected_retry_at": next_retry["expected_at"].isoformat() if next_retry else None,
                    "next_expected_retry_source": next_retry["source"] if next_retry else None,
                    "retry_candidates_before_window_end": [
                        {"source": candidate["source"], "expected_at": candidate["expected_at"].isoformat()}
                        for candidate in candidates],
                    "scheduled_retry_remains_before_window_end": bool(candidates),
                })
                active_unrecorded[key] = item
            elif now < start:
                upcoming[key] = item
    ordered = sorted(upcoming.values(), key=lambda row: (row["capture_window_start_at"], row["domain"], str(row["identity"])))
    active = sorted(active_unrecorded.values(), key=lambda row: (row["capture_window_end_at"], row["domain"], str(row["identity"])))
    return {
        "active_unrecorded_windows": active,
        "active_unrecorded_window_count": len(active),
        "upcoming_windows": ordered,
        "upcoming_window_count": len(ordered),
        "next_window": ordered[0] if ordered else None,
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Polymarket Deadline Shadow Cycle", "",
        f"- Status: `{payload['status']}`",
        f"- Opened / settled research forecasts: {payload.get('opened_count', 0)} / {payload.get('settled_count', 0)}",
        f"- Active unrecorded windows: {payload.get('active_unrecorded_window_count', 0)}",
        f"- Upcoming unique windows: {payload.get('upcoming_window_count', 0)}",
        f"- Forward protocol: `{payload.get('forward_protocol_status')}`",
        f"- Automation runtime freshness: `{payload.get('automation_runtime_status')}`",
        f"- Capture Back Cases added / active / total: {payload.get('capture_backcases_added')} / {payload.get('capture_backcases_active')} / {payload.get('capture_backcases_total')}",
        f"- Main paper artifacts unchanged: `{str(payload.get('protected_artifacts_unchanged')).lower()}`", "",
        "| Domain | Identity | Window start | Window end | Reason | Missing | Next expected retry | Source |", "|---|---|---|---|---|---:|---|---|",
    ]
    rows = list(payload.get("active_unrecorded_windows", [])) + list(payload.get("upcoming_windows", []))[:20]
    for row in rows:
        lines.append(f"| {row['domain']} | {row['identity']} | {row['capture_window_start_at']} | {row['capture_window_end_at']} | {row['reason']} | {row.get('missing_market_count') or ''} | {row.get('next_expected_retry_at') or ''} | {row.get('next_expected_retry_source') or ''} |")
    lines.extend(["", "This cycle is research-only. It never emits main-ledger estimates, never backfills missed windows, and never enables live orders or private APIs.", ""])
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    now = datetime(2026, 7, 12, 4, 30, tzinfo=timezone.utc)
    outputs = {"weather": {"excluded": [{"event_id": "e1", "reason": "fixed_cutoff_not_reached", "capture_window_start_at": "2026-07-12T04:00:00+00:00", "cutoff_at": "2026-07-12T05:00:00+00:00"}]}}
    inventory = deadline_inventory(outputs, now)
    assert inventory["active_unrecorded_window_count"] == 1
    future = {"social_count": {"excluded": [{"tracking_id": "t1", "reason": "fixed_cutoff_not_reached", "capture_window_start_at": "2026-07-12T06:00:00+00:00", "capture_window_end_at": "2026-07-12T07:00:00+00:00"}]}}
    assert deadline_inventory(future, now)["upcoming_window_count"] == 1
    return {"status": "pass", "tests": ["active_window_detection", "upcoming_window_detection", "shared_validation_lock_dependency"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--lock", default=str(validation.DEFAULT_LOCK))
    parser.add_argument("--weather-timeout-seconds", type=float, default=120)
    parser.add_argument("--football-timeout-seconds", type=float, default=120)
    parser.add_argument("--social-count-timeout-seconds", type=float, default=120)
    parser.add_argument("--stock-weekly-timeout-seconds", type=float, default=180)
    parser.add_argument("--output", default=str(ROOT / "experiments/current-deadline-shadow-cycle.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_DEADLINE_SHADOW_CYCLE.md"))
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2)); return 0
    lock_path = Path(args.lock)
    lock = validation.acquire_lock(lock_path)
    if not lock["acquired"]:
        print(json.dumps({"status": "skipped_locked", "lock": lock}, indent=2)); return 0
    started = time.monotonic(); before = {str(path.relative_to(ROOT)): file_sha(path) for path in PROTECTED}; steps = []
    try:
        preflight = validation.safety_preflight(ROOT); steps.append(preflight)
        python = sys.executable
        automation_contract = None
        if preflight["status"] == "ok":
            automation_contract = validation.run_step("automation_contract_audit", [python, "scripts/polymarket_automation_contract_audit.py"], 30)
            steps.append(automation_contract)
        if preflight["status"] == "ok" and automation_contract and automation_contract["status"] == "ok":
            steps.append(validation.run_step("automation_runtime_audit", [python, "scripts/polymarket_automation_runtime_audit.py"], 30))
            specs = [
                ("weather", [python, "scripts/polymarket_weather_shadow.py", "--ledger", "data/weather_research_forecast_ledger.json", "--output", "experiments/current-weather-shadow-cycle.json"], args.weather_timeout_seconds),
                ("football", [python, "scripts/polymarket_football_shadow.py", "--ledger", "data/football_research_forecast_ledger.json", "--output", "experiments/current-football-shadow-cycle.json"], args.football_timeout_seconds),
                ("social_count", [python, "scripts/polymarket_social_count_shadow.py", "--ledger", "data/social_count_research_forecast_ledger.json", "--output", "experiments/current-social-count-shadow-cycle.json"], args.social_count_timeout_seconds),
                ("stock_weekly", [python, "scripts/polymarket_stock_weekly_shadow.py", "--ledger", "data/stock_weekly_research_forecast_ledger.json", "--ohlc-dir", "cache/current_stock_weekly_shadow_ohlc", "--output", "experiments/current-stock-weekly-shadow-cycle.json"], args.stock_weekly_timeout_seconds),
            ]
            for name, command, timeout in specs:
                steps.append(validation.run_step(f"{name}_shadow", command, timeout))
            steps.append(validation.run_step("forward_protocol_audit", [python, "scripts/polymarket_forward_protocol_audit.py"], 30))
            steps.append(validation.run_step("capture_backcase_cycle", [python, "scripts/polymarket_capture_backcase.py"], 30))
        after = {str(path.relative_to(ROOT)): file_sha(path) for path in PROTECTED}
        outputs = {domain: core.read_json(path) for domain, path in DOMAIN_OUTPUTS.items() if path.exists()}
        current_validation_path = ROOT / "experiments/current-validation-cycle.json"
        current_validation = core.read_json(current_validation_path) if current_validation_path.exists() else {}
        full_cycle_last_completed = core.parse_iso(current_validation.get("created_at"))
        inventory = deadline_inventory(outputs, datetime.now(timezone.utc), full_cycle_last_completed)
        forward_path = ROOT / "experiments/current-forward-protocol-audit.json"
        forward = core.read_json(forward_path) if forward_path.exists() else {}
        runtime_path = ROOT / "experiments/current-automation-runtime-audit.json"
        runtime = core.read_json(runtime_path) if runtime_path.exists() else {}
        backcase_path = ROOT / "experiments/current-capture-backcase-cycle.json"
        backcases = core.read_json(backcase_path) if backcase_path.exists() else {}
        protected_unchanged = before == after
        all_ok = preflight["status"] == "ok" and automation_contract is not None and automation_contract["status"] == "ok" and protected_unchanged and all(step.get("status") == "ok" for step in steps)
        opened = {domain: payload.get("opened", []) for domain, payload in outputs.items()}
        settled = {domain: payload.get("settled", []) for domain, payload in outputs.items()}
        payload = {
            "schema_version": "polymarket-deadline-shadow-cycle-v1", "created_at": validation.now_iso(),
            "status": "ok" if all_ok else ("blocked_safety" if preflight["status"] != "ok" or automation_contract is None or automation_contract["status"] != "ok" or not protected_unchanged else "degraded"),
            "duration_seconds": time.monotonic() - started, "steps": steps, "lock": lock,
            "opened": opened, "opened_count": sum(len(rows) for rows in opened.values()),
            "settled": settled, "settled_count": sum(len(rows) for rows in settled.values()),
            **inventory, "forward_protocol_status": forward.get("status"),
            "automation_runtime_status": runtime.get("status"),
            "capture_backcases_added": backcases.get("added_count"),
            "capture_backcases_active": backcases.get("active_backcase_count"),
            "capture_backcases_total": backcases.get("total_backcase_count"),
            "forward_new_missed_window_count": forward.get("missed_capture_window_count"),
            "forward_historical_missed_window_count": forward.get("historical_missed_capture_window_count"),
            "protected_artifact_sha256_before": before, "protected_artifact_sha256_after": after,
            "protected_artifacts_unchanged": protected_unchanged, "paper_estimates_emitted": False,
            "main_paper_ledger_mutated": False, "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        }
        validation.atomic_json(Path(args.output), payload)
        report = Path(args.report); report.parent.mkdir(parents=True, exist_ok=True)
        temp = report.with_suffix(report.suffix + ".tmp"); temp.write_text(markdown(payload), encoding="utf-8"); temp.replace(report)
        print(json.dumps({k: payload[k] for k in ("status", "duration_seconds", "opened_count", "settled_count", "active_unrecorded_window_count", "upcoming_window_count", "forward_protocol_status", "automation_runtime_status", "capture_backcases_added", "capture_backcases_active", "capture_backcases_total")}, indent=2))
        return 0 if all_ok else 1
    finally:
        validation.release_lock(lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
