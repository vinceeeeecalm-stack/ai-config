#!/usr/bin/env python3
"""Plan recovery of the single paper-only automation without mutating it."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from resume_preflight import read_automation_status


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
AUTOMATION_ROOT = Path("/Users/vincentpan/.codex/automations")
EXPECTED_ID = "active-alpha-hourly-crypto-paper-loop"
EXPECTED_PATH = AUTOMATION_ROOT / EXPECTED_ID / "automation.toml"
REPORT_PATH = ACTIVE_ROOT / "reports" / "AUTOMATION_RECOVERY_PLAN.md"
EXPERIMENT_PATH = ACTIVE_ROOT / "experiments" / "automation-recovery-plan.json"
LOCAL_TZ = ZoneInfo("Asia/Shanghai")

PROPOSED_PROMPT = """Run the active-alpha-paper-monitor hourly crypto paper-only validation loop using read-only public or authorized market data. Never place real orders, never use private trading APIs, never withdraw, never trade margin/futures/perpetuals, and never expose API keys. Keep live_orders_enabled=false, private_api_used=false, and allow_real_orders=false.

Start with resume_preflight.py. If preflight is not ready, market data is stale, or durable Kline files are missing, run report/audit steps only and open no paper positions. When explicitly approved and preflight is safe, rebuild the durable public Binance Kline cache with required 1m/5m/15m/1h/4h coverage plus optional 1d research data before current-signal research. Then run validation_progress_runner.py and the paper audit, attribution, strategy backlog, paper auto-evolver, recovery watchlist/sampler, signal contract, paper/testnet risk control, capital allocation, ledger integrity, market context, phase readiness, artifact index, and update reports/LATEST_ACTIVE_ALPHA_STATUS.md. Do not generate HTML, image cards, or push assets unless the user explicitly requests a one-time visualization. Every run must leave readable Markdown reports. Paper/backtest PnL never counts as live revenue or authorizes real trading."""


def now_local() -> dt.datetime:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0)


def rel(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def file_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def automation_inventory(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/automation.toml")) if root.exists() else []:
        audit = read_automation_status(path)
        rows.append(
            {
                "path": str(path),
                "sha256": file_fingerprint(path),
                "id": audit.get("id"),
                "status": audit.get("status"),
                "contract_status": (audit.get("contract") or {}).get("status"),
                "contract_errors": (audit.get("contract") or {}).get("errors") or [],
            }
        )
    return rows


def build_record(automation_root: Path = AUTOMATION_ROOT) -> dict[str, Any]:
    before = automation_inventory(automation_root)
    matching = [item for item in before if item.get("id") == EXPECTED_ID]
    safe_matching = [item for item in matching if item.get("contract_status") == "pass"]

    if not before:
        status = "restore_required_user_approval"
        reason = "no_local_automation_definition"
        next_action = "After explicit user approval, create exactly one PAUSED paper-only automation from the proposed contract, then rerun preflight."
    elif len(before) > 1:
        status = "duplicate_or_unrelated_automations_require_review"
        reason = f"automation_count_{len(before)}"
        next_action = "Do not create another automation. Review duplicates or unrelated entries and preserve at most one safe active-alpha definition."
    elif not matching:
        status = "unexpected_single_automation_requires_review"
        reason = "expected_automation_id_missing"
        next_action = "Do not overwrite the existing automation. Review it before any active-alpha restoration."
    elif not safe_matching:
        status = "unsafe_existing_automation_requires_review"
        reason = "expected_automation_contract_blocked"
        next_action = "Keep the automation inactive and repair its paper-only contract only after explicit user approval."
    else:
        current_status = str(safe_matching[0].get("status") or "unknown")
        status = "safe_existing_paused" if current_status == "PAUSED" else "safe_existing_active" if current_status == "ACTIVE" else "safe_contract_unexpected_status"
        reason = f"single_safe_automation_{current_status.lower()}"
        next_action = "No restoration required; run preflight before any state change."

    after = automation_inventory(automation_root)
    mutation_detected = before != after
    prompt_hash = hashlib.sha256(PROPOSED_PROMPT.encode("utf-8")).hexdigest()
    created = now_local()
    return {
        "run_id": f"{created.strftime('%Y%m%d-%H%M%S')}-automation-recovery-plan",
        "created_at": created.isoformat(),
        "scope": "single_paper_only_automation_recovery_planning",
        "status": status,
        "reason": reason,
        "requires_explicit_user_approval": status != "safe_existing_paused" and status != "safe_existing_active",
        "max_allowed_action": "planning_only",
        "automation_mutated": mutation_detected,
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "inventory": {
            "root": str(automation_root),
            "count": len(before),
            "expected_id_count": len(matching),
            "safe_expected_id_count": len(safe_matching),
            "items": before,
        },
        "proposed_contract": {
            "id": EXPECTED_ID,
            "name": "Active Alpha Hourly Crypto Paper Loop",
            "kind": "cron",
            "schedule": "hourly_interval_1",
            "execution_environment": "local",
            "workspace": str(WORKSPACE_ROOT),
            "initial_status": "PAUSED",
            "prompt_sha256": prompt_hash,
            "prompt": PROPOSED_PROMPT,
            "safety_flags": {
                "paper_only": True,
                "read_only_market_data": True,
                "live_orders_enabled": False,
                "private_api_used": False,
                "withdrawals_enabled": False,
                "margin_futures_perpetuals_enabled": False,
            },
        },
        "next_action": next_action,
        "outputs": {},
    }


def render_report(record: dict[str, Any]) -> str:
    inventory = record.get("inventory") or {}
    contract = record.get("proposed_contract") or {}
    lines = [
        "# Automation Recovery Plan",
        "",
        "Read-only planning artifact. It does not create, update, activate, pause, or delete an automation.",
        "",
        f"- status: `{record.get('status')}`",
        f"- reason: `{record.get('reason')}`",
        f"- automation_count: `{inventory.get('count')}`",
        f"- expected_id_count: `{inventory.get('expected_id_count')}`",
        f"- safe_expected_id_count: `{inventory.get('safe_expected_id_count')}`",
        f"- requires_explicit_user_approval: `{record.get('requires_explicit_user_approval')}`",
        f"- max_allowed_action: `{record.get('max_allowed_action')}`",
        f"- automation_mutated: `{record.get('automation_mutated')}`",
        "",
        "## Proposed Safe Contract",
        "",
        f"- id: `{contract.get('id')}`",
        f"- initial_status: `{contract.get('initial_status')}`",
        f"- schedule: `{contract.get('schedule')}`",
        f"- execution_environment: `{contract.get('execution_environment')}`",
        f"- workspace: `{contract.get('workspace')}`",
        f"- prompt_sha256: `{contract.get('prompt_sha256')}`",
        f"- safety_flags: `{contract.get('safety_flags')}`",
        "",
        "## Next Action",
        "",
        f"- {record.get('next_action')}",
        "",
        "## Safety",
        "",
        "- `live_orders_enabled=false`",
        "- `private_api_used=false`",
        "- `allow_real_orders=false`",
        "",
    ]
    return "\n".join(lines)


def valid_automation_text(*, status: str = "PAUSED", automation_id: str = EXPECTED_ID) -> str:
    return "\n".join(
        [
            "version = 1",
            f'id = "{automation_id}"',
            'kind = "cron"',
            'name = "Active Alpha Hourly Crypto Paper Loop"',
            'prompt = "Run the active-alpha-paper-monitor hourly crypto paper validation loop in paper-only mode. Use read-only public market data, never place real orders, never use private trading APIs, never withdraw, never trade margin/futures/perpetuals, and never expose API keys."',
            f'status = "{status}"',
            'rrule = "FREQ=HOURLY;INTERVAL=1"',
            'execution_environment = "local"',
            f'cwds = ["{WORKSPACE_ROOT}"]',
            "",
        ]
    )


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="automation_recovery_planner_") as tmp_text:
        root = Path(tmp_text)
        missing = build_record(root)
        assert missing["status"] == "restore_required_user_approval", missing
        assert missing["automation_mutated"] is False, missing

        safe_path = root / EXPECTED_ID / "automation.toml"
        safe_path.parent.mkdir(parents=True)
        safe_path.write_text(valid_automation_text(), encoding="utf-8")
        safe_before = file_fingerprint(safe_path)
        safe = build_record(root)
        assert safe["status"] == "safe_existing_paused", safe
        assert safe["automation_mutated"] is False and file_fingerprint(safe_path) == safe_before, safe

        duplicate_path = root / "duplicate" / "automation.toml"
        duplicate_path.parent.mkdir(parents=True)
        duplicate_path.write_text(valid_automation_text(), encoding="utf-8")
        duplicate = build_record(root)
        assert duplicate["status"] == "duplicate_or_unrelated_automations_require_review", duplicate
        duplicate_path.unlink()
        duplicate_path.parent.rmdir()

        unsafe_text = valid_automation_text().replace("paper-only", "live").replace("never place real orders", "place orders")
        safe_path.write_text(unsafe_text, encoding="utf-8")
        unsafe = build_record(root)
        assert unsafe["status"] == "unsafe_existing_automation_requires_review", unsafe
        assert unsafe["automation_mutated"] is False, unsafe

    return {
        "status": "ok",
        "missing_restore_plan_verified": True,
        "safe_singleton_no_action_verified": True,
        "duplicate_review_verified": True,
        "unsafe_contract_review_verified": True,
        "no_automation_mutation_verified": True,
        "live_orders_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan recovery of the single paper-only automation")
    parser.add_argument("--automation-root", type=Path, default=AUTOMATION_ROOT)
    parser.add_argument("--report-output", type=Path, default=REPORT_PATH)
    parser.add_argument("--json-output", type=Path, default=EXPERIMENT_PATH)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    record = build_record(args.automation_root)
    if not args.no_write:
        record["outputs"] = {
            "report": rel(args.report_output),
            "experiment": rel(args.json_output),
        }
        write_json(args.json_output, record)
        write_text(args.report_output, render_report(record))
    if args.compact_output:
        print(
            json.dumps(
                {
                    "status": record.get("status"),
                    "reason": record.get("reason"),
                    "automation_count": (record.get("inventory") or {}).get("count"),
                    "requires_explicit_user_approval": record.get("requires_explicit_user_approval"),
                    "max_allowed_action": record.get("max_allowed_action"),
                    "automation_mutated": record.get("automation_mutated"),
                    "next_action": record.get("next_action"),
                    "outputs": record.get("outputs"),
                    "live_orders_enabled": record.get("live_orders_enabled"),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
