#!/usr/bin/env python3
"""Read-only freshness audit for Polymarket recurring automation artifacts."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "full_validation": (Path("experiments/current-validation-cycle.json"), 90 * 60),
    "deadline_shadow": (Path("experiments/current-deadline-shadow-cycle.json"), 45 * 60),
}


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def artifact_time(payload: dict[str, Any]) -> datetime | None:
    for key in ("created_at", "completed_at", "updated_at"):
        parsed = parse_time(payload.get(key))
        if parsed is not None:
            return parsed.astimezone(timezone.utc)
    return None


def audit(root: Path = ROOT, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows = []
    for name, (relative, max_age_seconds) in TARGETS.items():
        path = root / relative
        if not path.exists():
            rows.append({"name": name, "artifact": str(relative), "status": "missing",
                         "max_age_seconds": max_age_seconds, "age_seconds": None})
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rows.append({"name": name, "artifact": str(relative), "status": "invalid",
                         "max_age_seconds": max_age_seconds, "age_seconds": None,
                         "error": f"{type(exc).__name__}:{exc}"})
            continue
        observed = artifact_time(payload)
        if observed is None:
            status, age = "timestamp_missing", None
        else:
            age = (now - observed).total_seconds()
            status = "future_timestamp" if age < -60 else "fresh" if age <= max_age_seconds else "stale"
        rows.append({"name": name, "artifact": str(relative), "status": status,
                     "artifact_status": payload.get("status"),
                     "observed_at": observed.isoformat() if observed else None,
                     "max_age_seconds": max_age_seconds, "age_seconds": age})
    overall = "ok" if all(row["status"] == "fresh" for row in rows) else "degraded"
    return {
        "schema_version": "polymarket-automation-runtime-audit-v1",
        "created_at": now.isoformat(), "status": overall, "artifacts": rows,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = ["# Polymarket Automation Runtime Audit", "", f"- Status: `{payload['status']}`", "",
             "| Task | Artifact | Runtime state | Artifact state | Age seconds | Maximum |",
             "|---|---|---|---|---:|---:|"]
    for row in payload["artifacts"]:
        lines.append(f"| {row['name']} | {row['artifact']} | {row['status']} | {row.get('artifact_status')} | {row.get('age_seconds')} | {row['max_age_seconds']} |")
    lines.extend(["", "This audit is diagnostic and read-only. A stale full cycle never suppresses the deadline monitor that may recover coverage.", ""])
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    assert parse_time("2026-07-12T04:00:00Z") is not None
    assert artifact_time({"created_at": "bad"}) is None
    return {"status": "pass", "tests": ["timestamp_parse", "missing_timestamp"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--output", default=str(ROOT / "experiments/current-automation-runtime-audit.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_AUTOMATION_RUNTIME_AUDIT.md"))
    args = parser.parse_args()
    if args.self_test:
        payload = self_test()
    else:
        payload = audit()
        output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = Path(args.report); report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
