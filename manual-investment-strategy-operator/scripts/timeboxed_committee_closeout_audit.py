#!/usr/bin/env python3
"""Audit whether a partial Research Committee run was closed out safely.

This read-only checker is for ordinary manual dispatches. It verifies that a
timeboxed subagent run can end with a degraded but usable report instead of
waiting indefinitely, while still blocking execute_now when the committee is
partial or under-verified.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "manual_strategy_config.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def max_action_is_safe(action: str | None, allowed: str) -> bool:
    if not action:
        return False
    allowed_actions = {"watch", "paper_only", "conditional_action", "risk_alert", "no_deploy", "hold", "trim_review"}
    if allowed == "watch":
        return action == "watch"
    return action in allowed_actions


def build_audit(
    *,
    config: dict[str, Any],
    task_package: dict[str, Any],
    collection: dict[str, Any],
    quality_audit: dict[str, Any],
) -> dict[str, Any]:
    gate = config.get("research_committee_gate") or {}
    closeout = gate.get("timeboxed_closeout") or {}
    manifest = collection.get("collection_manifest") or {}
    validation = manifest.get("validation") or {}
    expected_count = int(manifest.get("expected_task_count") or task_package.get("task_count") or 0)
    collected_count = int(manifest.get("collected_agent_count") or 0)
    missing_agents = manifest.get("missing_agents") or []
    missing_count = len(missing_agents)
    partial = collected_count < expected_count or bool(missing_agents)
    quality_status = quality_audit.get("status")
    quality_max_action = quality_audit.get("max_allowed_action")
    partial_max = closeout.get("partial_committee_max_allowed_action") or "watch"

    checks = [
        {
            "id": "timeboxed_closeout_config_enabled",
            "passed": closeout.get("enabled") is True and closeout.get("default_for_manual_dispatch") is True,
            "detail": "timeboxed closeout must be enabled by default for ordinary manual dispatches",
        },
        {
            "id": "partial_outputs_collected",
            "passed": collected_count > 0,
            "detail": f"collected {collected_count} of {expected_count} expected roles",
        },
        {
            "id": "missing_roles_visible",
            "passed": (not partial) or missing_count > 0,
            "detail": f"missing_agents={missing_agents}",
        },
        {
            "id": "quality_audit_degraded_when_partial",
            "passed": (not partial) or quality_status in {"failed", "degraded", "partial"},
            "detail": f"quality_status={quality_status}",
        },
        {
            "id": "partial_committee_action_capped",
            "passed": (not partial) or max_action_is_safe(quality_max_action, partial_max),
            "detail": f"quality_max_action={quality_max_action}; partial_max={partial_max}",
        },
        {
            "id": "partial_committee_cannot_execute_now",
            "passed": closeout.get("partial_committee_cannot_satisfy_execute_now") is True,
            "detail": "partial committee cannot satisfy execute_now",
        },
        {
            "id": "live_orders_disabled",
            "passed": quality_audit.get("live_orders_enabled") is False and manifest.get("live_orders_enabled") is False,
            "detail": f"quality_live_orders={quality_audit.get('live_orders_enabled')}; manifest_live_orders={manifest.get('live_orders_enabled')}",
        },
        {
            "id": "quality_gate_not_passed_for_partial",
            "passed": (not partial) or validation.get("committee_quality_gate_passed") is False,
            "detail": f"collector_quality_gate={validation.get('committee_quality_gate_passed')}",
        },
    ]
    failed = [item for item in checks if not item["passed"]]
    status = "ok" if not failed else "failed"
    if status == "ok" and partial:
        status = "degraded_but_valid_closeout"

    return {
        "generated_at": utc_now(),
        "audit_version": "timeboxed-committee-closeout-audit-v1",
        "status": status,
        "does_not_authorize_trades": True,
        "live_orders_enabled": False,
        "auto_trading_enabled": False,
        "ordinary_manual_dispatch_can_end": status in {"ok", "degraded_but_valid_closeout"},
        "execute_now_allowed": False,
        "max_allowed_action": quality_max_action if status in {"ok", "degraded_but_valid_closeout"} else "watch",
        "timebox_policy": {
            "enabled": closeout.get("enabled"),
            "initial_wait_seconds": closeout.get("initial_wait_seconds"),
            "convergence_wait_seconds_after_interrupt": closeout.get("convergence_wait_seconds_after_interrupt"),
            "max_total_external_wait_seconds": closeout.get("max_total_external_wait_seconds"),
            "partial_committee_max_allowed_action": partial_max,
            "deep_full_committee_requires_explicit_user_request": closeout.get("deep_full_committee_requires_explicit_user_request"),
        },
        "committee_collection": {
            "source_package_run_id": manifest.get("source_package_run_id") or task_package.get("run_id"),
            "expected_task_count": expected_count,
            "collected_agent_count": collected_count,
            "missing_agent_count": missing_count,
            "collected_agents": manifest.get("collected_agents") or [],
            "missing_agents": missing_agents,
            "partial_committee": partial,
        },
        "quality_audit_summary": {
            "status": quality_status,
            "max_allowed_action": quality_max_action,
            "quality_gate_blockers": quality_audit.get("quality_gate_blockers") or [],
            "quality_gap_summary": quality_audit.get("quality_gap_summary") or {},
        },
        "checks": checks,
        "failed_checks": failed,
        "operator_note": (
            "A valid closeout means the report can finish and feed the learning loop. "
            "It does not mean the research committee passed, and it never authorizes execute_now."
        ),
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def render_markdown(payload: dict[str, Any]) -> str:
    closeout = payload.get("timebox_policy") or {}
    collection = payload.get("committee_collection") or {}
    quality = payload.get("quality_audit_summary") or {}
    lines = [
        f"# Timeboxed Committee Closeout Audit | {collection.get('source_package_run_id')}",
        "",
        "This read-only audit checks whether a partial Research Committee run was closed safely. It does not authorize trades.",
        "",
        "## Summary",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["status", f"`{payload.get('status')}`"],
                ["ordinary_manual_dispatch_can_end", f"`{payload.get('ordinary_manual_dispatch_can_end')}`"],
                ["execute_now_allowed", f"`{payload.get('execute_now_allowed')}`"],
                ["max_allowed_action", f"`{payload.get('max_allowed_action')}`"],
                ["expected/collected/missing", f"`{collection.get('expected_task_count')}/{collection.get('collected_agent_count')}/{collection.get('missing_agent_count')}`"],
                ["quality_status", f"`{quality.get('status')}`"],
            ],
        ),
        "",
        "## Timebox Policy",
        "",
        markdown_table(
            ["Field", "Value"],
            [
                ["initial_wait_seconds", f"`{closeout.get('initial_wait_seconds')}`"],
                ["convergence_wait_seconds_after_interrupt", f"`{closeout.get('convergence_wait_seconds_after_interrupt')}`"],
                ["max_total_external_wait_seconds", f"`{closeout.get('max_total_external_wait_seconds')}`"],
                ["partial_committee_max_allowed_action", f"`{closeout.get('partial_committee_max_allowed_action')}`"],
                ["deep_full_committee_requires_explicit_user_request", f"`{closeout.get('deep_full_committee_requires_explicit_user_request')}`"],
            ],
        ),
        "",
        "## Missing Agents",
        "",
    ]
    missing = collection.get("missing_agents") or []
    lines.extend([f"- `{agent}`" for agent in missing] or ["- none"])
    lines.extend(["", "## Checks", "", markdown_table(["Check", "Passed", "Detail"], [[c["id"], f"`{c['passed']}`", c["detail"]] for c in payload.get("checks") or []])])
    lines.extend(["", "术语备注：`timeboxed closeout` 是到点收口；`partial committee` 是研究角色未齐的降级研究委员会，可用于学习和观察，不能用于强执行。", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit timeboxed Research Committee closeout safety")
    parser.add_argument("--config-json", default=str(DEFAULT_CONFIG))
    parser.add_argument("--task-package-json", required=True)
    parser.add_argument("--collection-json", required=True)
    parser.add_argument("--quality-audit-json", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    payload = build_audit(
        config=load_json(args.config_json),
        task_package=load_json(args.task_package_json),
        collection=load_json(args.collection_json),
        quality_audit=load_json(args.quality_audit_json),
    )
    if args.output:
        write_json(Path(args.output), payload)
    if args.markdown_output:
        write_text(Path(args.markdown_output), render_markdown(payload))
    if args.format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ordinary_manual_dispatch_can_end"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
