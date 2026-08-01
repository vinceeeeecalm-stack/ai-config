#!/usr/bin/env python3
"""Guarded scheduler entrypoint for the isolated $100 sports-paper workflow.

This is the only supported scheduler boundary.  It delegates run ownership to
the active-sports-paper-monitor run guard and adds one-report-per-run plus
NO_STATE_CHANGE enforcement.  It never scans, trades, or writes either paper
ledger itself.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_DIR = ROOT / "data/sports_100usd_runtime"
DEFAULT_REPORT_DIR = ROOT / "reports/sports_100usd_paper_daily"
GUARD_PATH = Path("/Users/vincentpan/.codex/skills/active-sports-paper-monitor/scripts/sports_run_guard.py")
VOLATILE_KEYS = {
    "created_at", "generated_at", "generated_at_utc", "observed_at", "received_at",
    "requested_at", "started_at", "finished_at", "updated_at", "timestamp",
}
SAFE_FLAGS = {
    "paper_only": True,
    "live_orders_enabled": False,
    "private_api_used": False,
    "real_money_execution_authorized": False,
}


def load_module():
    spec = importlib.util.spec_from_file_location("sports_run_guard_runtime", GUARD_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load run guard: {GUARD_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load_module()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def materialize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: materialize(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS and not key.endswith("_timestamp")
        }
    if isinstance(value, list):
        return [materialize(item) for item in value]
    return value


def material_digest(value: dict[str, Any]) -> str:
    payload = json.dumps(materialize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def journal_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_no} must be an object")
        rows.append(row)
    return rows


def validate_safe_state(state: dict[str, Any]) -> list[str]:
    safety = state.get("safety") if isinstance(state.get("safety"), dict) else state
    return [key for key, expected in SAFE_FLAGS.items() if safety.get(key) is not expected]


def load_material_state(value: Path) -> dict[str, Any]:
    if value.exists():
        return load_object(value)
    if str(value) == "POSTMORTEM_COMPLETE":
        return {"material_state": str(value), "safety": dict(SAFE_FLAGS)}
    raise FileNotFoundError(value)


def commit_report(state_dir: Path, report_dir: Path, run_id: str,
                  material_state_path: Path, report_source: Path,
                  report_name: str) -> dict[str, Any]:
    lock_path = state_dir / "active_run.lock.json"
    journal_path = state_dir / "report_journal.jsonl"
    last_state_path = state_dir / "last_material_state.json"
    if not lock_path.exists():
        return {"status": "PROCESS_INCIDENT", "reason": "run_lock_missing"}
    owner = load_object(lock_path)
    if owner.get("run_id") != run_id:
        return {"status": "PROCESS_INCIDENT", "reason": "run_lock_owner_mismatch", "lock_owner": owner}
    if any(row.get("run_id") == run_id and row.get("status") == "REPORT_COMMITTED"
           for row in journal_rows(journal_path)):
        return {"status": "PROCESS_INCIDENT", "reason": "primary_report_already_committed", "run_id": run_id}
    state = load_material_state(material_state_path)
    unsafe = validate_safe_state(state)
    if unsafe:
        return {"status": "PROCESS_INCIDENT", "reason": "unsafe_material_state", "unsafe_flags": unsafe}
    digest = material_digest(state)
    previous = load_object(last_state_path) if last_state_path.exists() else {}
    if previous.get("material_digest") == digest:
        row = {
            "status": "NO_STATE_CHANGE", "run_id": run_id,
            "scheduler_trigger_id": owner.get("scheduler_trigger_id"),
            "material_digest": digest, "primary_report_path": None,
            **SAFE_FLAGS,
        }
        append_jsonl(journal_path, row)
        return row
    if Path(report_name).name != report_name or not report_name.lower().endswith((".json", ".md")):
        return {"status": "PROCESS_INCIDENT", "reason": "unsafe_report_name"}
    if not report_source.exists():
        return {"status": "PROCESS_INCIDENT", "reason": "report_source_missing"}
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / report_name
    if report_source.resolve() != target.resolve():
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return {"status": "PROCESS_INCIDENT", "reason": "report_path_already_exists", "path": str(target)}
        try:
            os.write(descriptor, report_source.read_bytes())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    row = {
        "status": "REPORT_COMMITTED", "run_id": run_id,
        "scheduler_trigger_id": owner.get("scheduler_trigger_id"),
        "material_digest": digest, "primary_report_path": str(target),
        **SAFE_FLAGS,
    }
    atomic_json(last_state_path, {"material_digest": digest, "state": materialize(state)})
    append_jsonl(journal_path, row)
    return row


def status(state_dir: Path) -> dict[str, Any]:
    result = guard.inspect(state_dir)
    journal = journal_rows(state_dir / "report_journal.jsonl")
    result.update({
        "report_committed_count": sum(row.get("status") == "REPORT_COMMITTED" for row in journal),
        "no_state_change_count": sum(row.get("status") == "NO_STATE_CHANGE" for row in journal),
        **SAFE_FLAGS,
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--scheduler-trigger-id", required=True)
    start.add_argument("--scan-type", required=True)
    start.add_argument("--run-id")
    start.add_argument("--parent-run-id")
    start.add_argument("--max-seconds", type=int, default=900)
    finish = sub.add_parser("finish")
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--continuation-count", type=int, default=0)
    finish.add_argument("--run-status", default="completed")
    commit = sub.add_parser("commit-report")
    commit.add_argument("--run-id", required=True)
    commit.add_argument("--material-state", type=Path, required=True)
    commit.add_argument("--report-source", type=Path, required=True)
    commit.add_argument("--report-name", required=True)
    commit.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    sub.add_parser("status")
    args = parser.parse_args()
    try:
        if args.command == "start":
            result = guard.start(args.state_dir, args.scheduler_trigger_id, args.scan_type,
                                 args.run_id, args.parent_run_id, args.max_seconds)
        elif args.command == "finish":
            result = guard.finish(args.state_dir, args.run_id, args.continuation_count, args.run_status)
        elif args.command == "commit-report":
            result = commit_report(args.state_dir, args.report_dir, args.run_id,
                                   args.material_state, args.report_source, args.report_name)
        else:
            result = status(args.state_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"status": "PROCESS_INCIDENT", "reason": "entrypoint_exception", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {
        "RUN_STARTED", "RUN_FINISHED", "DUPLICATE_RUN_BLOCKED", "REPORT_COMMITTED",
        "NO_STATE_CHANGE", "ok",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
