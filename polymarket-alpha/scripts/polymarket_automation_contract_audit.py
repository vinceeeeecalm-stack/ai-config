#!/usr/bin/env python3
"""Read-only audit of the persistent Polymarket paper-only schedules."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTOMATION_ROOT = Path.home() / ".codex" / "automations"
EXPECTED = {
    "polymarket-paper-validation-cycle": {
        "name": "Polymarket Daily Manual Decision + Paper Validation",
        "status": "ACTIVE",
        "rrule": "FREQ=HOURLY;INTERVAL=1",
        "script": "polymarket_100usd_hourly_scan.py",
        "required_prompt_terms": [
            "polymarket_100usd_scheduler_entrypoint.py", "RUN_STARTED", "NO_STATE_CHANGE",
            "paper_only=true", "live_orders_enabled=false", "private_api_used=false",
            "$500 主账本", "每一轮都必须返回用户可见",
        ],
    },
    "polymarket-deadline-shadow-monitor": {
        "name": "Polymarket Deadline Shadow Monitor",
        "status": "PAUSED",
        "rrule": "FREQ=MINUTELY;INTERVAL=30",
        "script": "polymarket_deadline_shadow_cycle.py",
        "required_prompt_terms": ["公开市场数据", "禁止真实订单", "私有 API", "共享锁", "不得绕过 gate"],
    },
}
FORBIDDEN_PROMPT_TERMS = ["live_orders_enabled=true", "private_api_used=true", "allow_real_orders=true"]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load("automation_audit_core", ROOT / "scripts/polymarket_alpha.py")


def parse_contract(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    values: dict[str, Any] = {}
    for line in raw.splitlines():
        if " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        if value.startswith('"'):
            try:
                values[key] = json.loads(value)
            except json.JSONDecodeError:
                values[key] = None
        elif value in {"true", "false"}:
            values[key] = value == "true"
        elif re.fullmatch(r"\d+", value):
            values[key] = int(value)
    target = re.search(r'target\s*=.*project_id\s*=\s*"([^"]+)"', raw)
    values["project_id"] = target.group(1) if target else None
    cwds = re.search(r'cwds\s*=\s*(\[[^\n]+\])', raw)
    try:
        values["cwds"] = json.loads(cwds.group(1)) if cwds else []
    except json.JSONDecodeError:
        values["cwds"] = []
    return values, raw


def audit(automation_root: Path = DEFAULT_AUTOMATION_ROOT, project_root: Path = ROOT.parent) -> dict[str, Any]:
    rows, failures = [], []
    for automation_id, expected in EXPECTED.items():
        path = automation_root / automation_id / "automation.toml"
        row_failures = []
        values: dict[str, Any] = {}
        raw = ""
        if not path.exists():
            row_failures.append("automation_missing")
        else:
            try:
                values, raw = parse_contract(path)
            except Exception as exc:
                row_failures.append(f"automation_unreadable:{type(exc).__name__}:{exc}")
        checks = {
            "id_matches": values.get("id") == automation_id,
            "name_matches": values.get("name") == expected["name"],
            "kind_is_cron": values.get("kind") == "cron",
            "status_matches": values.get("status") == expected["status"],
            "cadence_matches": values.get("rrule") == expected["rrule"],
            "script_matches": expected["script"] in str(values.get("prompt") or ""),
            "local_execution": values.get("execution_environment") == "local",
            "project_matches": bool(values.get("project_id")) and (
                values.get("project_id") == str(project_root) or str(project_root) in values.get("cwds", [])
            ),
            "cwd_matches_project": str(project_root) in values.get("cwds", []),
            "required_prompt_terms_present": all(term in str(values.get("prompt") or "") for term in expected["required_prompt_terms"]),
            "forbidden_prompt_terms_absent": all(term not in str(values.get("prompt") or "") for term in FORBIDDEN_PROMPT_TERMS),
        }
        row_failures.extend(name for name, passed in checks.items() if not passed)
        failures.extend(f"{automation_id}:{reason}" for reason in row_failures)
        rows.append({
            "automation_id": automation_id, "path": str(path), "checks": checks,
            "failures": row_failures, "contract_ok": not row_failures,
            "status": values.get("status"), "kind": values.get("kind"),
            "execution_environment": values.get("execution_environment"),
        })
    return {
        "schema_version": "polymarket-automation-contract-audit-v1", "created_at": core.now_iso(),
        "status": "ok" if not failures else "blocked", "expected_automation_count": len(EXPECTED),
        "verified_automation_count": sum(row["contract_ok"] for row in rows),
        "automations": rows, "failures": failures, "read_only": True,
        "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Polymarket Automation Contract Audit", "",
        f"- Status: `{payload['status']}`",
        f"- Verified: {payload['verified_automation_count']} / {payload['expected_automation_count']}", "",
        "| Automation | Status | Cadence | Script | Local | Project/CWD | Prompt safety | Contract |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["automations"]:
        c = row["checks"]
        lines.append(f"| {row['automation_id']}:{row['status']} | {c['status_matches']} | {c['cadence_matches']} | {c['script_matches']} | {c['local_execution']} | {c['project_matches'] and c['cwd_matches_project']} | {c['required_prompt_terms_present'] and c['forbidden_prompt_terms_absent']} | {row['contract_ok']} |")
    if payload["failures"]:
        lines.extend(["", "Failures:", ""] + [f"- `{item}`" for item in payload["failures"]])
    lines.extend(["", "This audit is read-only and cannot create, resume, edit or delete an automation.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--automation-root", default=str(DEFAULT_AUTOMATION_ROOT))
    parser.add_argument("--project-root", default=str(ROOT.parent))
    parser.add_argument("--output", default=str(ROOT / "experiments/current-automation-contract-audit.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_AUTOMATION_CONTRACT_AUDIT.md"))
    args = parser.parse_args()
    payload = audit(Path(args.automation_root), Path(args.project_root))
    core.write_json(Path(args.output), payload)
    report = Path(args.report); report.parent.mkdir(parents=True, exist_ok=True); report.write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
