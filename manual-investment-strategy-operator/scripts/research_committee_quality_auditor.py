#!/usr/bin/env python3
"""Audit externally collected Research Committee subagent outputs.

This tool does not create research conclusions and does not promote actions.
It only answers: "why did this external committee pass or fail the quality
gate, and what needs to improve before manual reports can treat it as
non-degraded evidence?"
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
WORKSPACE_ROOT = ROOT.parent
RESEARCH_PANEL_RUNNER = SCRIPT_DIR / "research_panel_runner.py"


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("research_panel_runner_for_quality_audit", RESEARCH_PANEL_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {RESEARCH_PANEL_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = load_runner()
ROLE_IDS = list(RUNNER.ROLE_IDS)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def role_source_counts(output: dict[str, Any]) -> dict[str, int]:
    counts = {"ok": 0, "fallback": 0, "failed": 0, "total": 0}
    for item in output.get("sources_used") or []:
        status = item.get("status")
        counts["total"] += 1
        if status in counts:
            counts[status] += 1
    return counts


def material_missing_data(output: dict[str, Any]) -> list[str]:
    missing = []
    for item in output.get("missing_data") or []:
        text = str(item)
        if text.lower() not in {"none", "n/a", "not_applicable", "not applicable"}:
            missing.append(text)
    return missing


def source_verification_gaps(output: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    role = output.get("agent_id") or "unknown"
    source_refs = output.get("source_refs") or []
    evidence_items = output.get("evidence_items") or []
    for index, source in enumerate(output.get("sources_used") or []):
        if not isinstance(source, dict):
            gaps.append(f"{role}.sources_used[{index}] is not an object")
            continue
        status = source.get("status")
        fresh_at = str(source.get("fresh_at") or "").strip()
        coverage = str(source.get("coverage") or "").strip().lower()
        provider = str(source.get("url_or_provider") or "").strip().lower()
        if status == "ok" and not fresh_at:
            gaps.append(f"{role}.sources_used[{index}] ok source missing fresh_at timestamp")
        if status == "ok" and coverage in {"", "unspecified", "unknown", "n/a", "none"}:
            gaps.append(f"{role}.sources_used[{index}] ok source missing specific coverage")
        if status == "ok" and provider in {"", "unspecified", "unknown", "n/a", "none"}:
            gaps.append(f"{role}.sources_used[{index}] ok source missing url_or_provider")
    if not isinstance(source_refs, list):
        gaps.append(f"{role}.source_refs is not a list")
        source_refs = []
    if not isinstance(evidence_items, list):
        gaps.append(f"{role}.evidence_items is not a list")
        evidence_items = []
    refs = set()
    for index, source_ref in enumerate(source_refs):
        if not isinstance(source_ref, dict):
            gaps.append(f"{role}.source_refs[{index}] is not an object")
            continue
        ref = str(source_ref.get("source_ref") or "").strip()
        if not ref:
            gaps.append(f"{role}.source_refs[{index}] missing source_ref")
            continue
        refs.add(ref)
        if source_ref.get("status") == "ok":
            for field in ("name", "url_or_provider", "fresh_at", "coverage"):
                value = str(source_ref.get(field) or "").strip().lower()
                if value in {"", "unspecified", "unknown", "n/a", "none"}:
                    gaps.append(f"{role}.source_refs[{index}] ok ref missing {field}")
    if role_source_counts(output).get("ok", 0) > 0 and not evidence_items:
        gaps.append(f"{role}.evidence_items missing despite ok sources")
    for index, evidence in enumerate(evidence_items):
        if not isinstance(evidence, dict):
            gaps.append(f"{role}.evidence_items[{index}] is not an object")
            continue
        status = str(evidence.get("verification_status") or "").strip()
        ref = str(evidence.get("source_ref") or "").strip()
        if status == "verified" and ref not in refs:
            gaps.append(f"{role}.evidence_items[{index}] verified item cites missing source_ref `{ref}`")
        if status == "verified":
            for field in ("field_id", "as_of", "source_ref"):
                value = str(evidence.get(field) or "").strip().lower()
                if value in {"", "unspecified", "unknown", "n/a", "none"}:
                    gaps.append(f"{role}.evidence_items[{index}] verified item missing {field}")
    return gaps


def hard_term_note_gaps(output: dict[str, Any]) -> list[str]:
    if hasattr(RUNNER, "hard_terms_without_notes"):
        return RUNNER.hard_terms_without_notes(output)
    return []


def role_quality_notes(output: dict[str, Any], validation_errors: list[str]) -> list[str]:
    notes: list[str] = []
    role = output.get("agent_id") or "unknown"
    counts = role_source_counts(output)
    missing = material_missing_data(output)
    source_gaps = source_verification_gaps(output)
    term_gaps = hard_term_note_gaps(output)
    role_errors = [err for err in validation_errors if str(err).startswith(str(role))]
    evidence_quality = (RUNNER.role_evidence_quality_status(output, role_errors) if hasattr(RUNNER, "role_evidence_quality_status") else None)
    action_readiness = (RUNNER.role_action_readiness_status(output) if hasattr(RUNNER, "role_action_readiness_status") else None)
    native_evidence_gaps = RUNNER.role_native_evidence_gaps(output) if hasattr(RUNNER, "role_native_evidence_gaps") else []
    action_blockers = RUNNER.role_action_blockers(output) if hasattr(RUNNER, "role_action_blockers") else []
    if evidence_quality != "verified":
        if native_evidence_gaps:
            notes.append(f"Role-native evidence gaps: {', '.join(native_evidence_gaps[:4])}")
        if counts["ok"] > 0 and not missing:
            notes.append(
                "Role has ok sources and no material missing_data, but evidence quality is not verified. "
                "Next subagent run should either justify sources or list the actual missing evidence."
            )
        else:
            notes.append("Role evidence is not verified; fill missing_data or upgrade sources before relying on it.")
    elif action_readiness and action_readiness not in {"ready_for_role_max_action", "watch_or_paper_ready"}:
        notes.append(
            f"Role evidence is verified, but action readiness is `{action_readiness}`; keep action capped by the reported blockers."
        )
        if action_blockers:
            notes.append(f"Action blockers: {', '.join(action_blockers[:4])}")
    if counts["ok"] == 0:
        notes.append("No ok source recorded; at least one fresh source with status=ok is required for quality gate credit.")
    if source_gaps:
        notes.append(f"Machine-verifiable source gaps: {', '.join(source_gaps[:4])}")
    if term_gaps:
        notes.append(f"Plain-language term note gaps: {', '.join(term_gaps[:4])}")
    if missing:
        notes.append(f"Material missing data: {', '.join(missing[:4])}")
    if output.get("failed_gates"):
        notes.append(f"Role failed gates: {', '.join(str(item) for item in output.get('failed_gates')[:4])}")
    if role_errors:
        notes.extend(role_errors[:4])
    return notes


def build_role_rows(outputs: list[dict[str, Any]], validation: dict[str, Any]) -> list[dict[str, Any]]:
    by_role = {output.get("agent_id"): output for output in outputs if isinstance(output, dict)}
    errors = validation.get("errors") or []
    rows: list[dict[str, Any]] = []
    for role in ROLE_IDS:
        output = by_role.get(role)
        if not output:
            rows.append(
                {
                    "agent_id": role,
                    "present": False,
                    "data_quality": "missing",
                    "recommended_max_action": "watch",
                    "source_counts": {"ok": 0, "fallback": 0, "failed": 0, "total": 0},
                    "signal_count": 0,
                    "missing_data_count": 1,
                    "failed_gate_count": 1,
                    "quality_credit": {
                        "successful_role": False,
                        "verified_role": False,
                        "has_ok_source": False,
                    },
                    "notes": ["Required external role is missing."],
                }
            )
            continue
        counts = role_source_counts(output)
        evidence_quality = (validation.get("role_evidence_quality") or {}).get(role)
        action_readiness = (validation.get("role_action_readiness") or {}).get(role)
        native_evidence_gaps = (validation.get("role_native_evidence_gaps") or {}).get(role) or []
        action_blockers = (validation.get("role_action_blockers") or {}).get(role) or []
        rows.append(
            {
                "agent_id": role,
                "present": True,
                "origin": output.get("origin"),
                "data_quality": output.get("data_quality"),
                "evidence_quality": evidence_quality,
                "action_readiness": action_readiness,
                "recommended_max_action": output.get("recommended_max_action"),
                "confidence_pct": output.get("confidence_pct"),
                "source_counts": counts,
                "signal_count": len(output.get("signals") or []),
                "missing_data_count": len(material_missing_data(output)),
                "failed_gate_count": len(output.get("failed_gates") or []),
                "normalization_notes": output.get("normalization_notes") or [],
                "native_evidence_gaps": native_evidence_gaps,
                "action_blockers": action_blockers,
                "source_verification_gaps": source_verification_gaps(output),
                "plain_language_note_gaps": hard_term_note_gaps(output),
                "quality_credit": {
                    "successful_role": output.get("data_quality") in {"verified", "degraded"},
                    "verified_role": output.get("data_quality") == "verified",
                    "evidence_verified_role": evidence_quality == "verified",
                    "action_ready_role": action_readiness in {"ready_for_role_max_action", "watch_or_paper_ready"},
                    "has_ok_source": counts["ok"] > 0,
                },
                "notes": role_quality_notes(output, errors),
            }
        )
    return rows


def build_audit(path: str, run_id: str | None = None) -> dict[str, Any]:
    outputs = RUNNER.load_external_agent_outputs(path)
    validation = RUNNER.validate_external_agent_outputs(outputs)
    role_rows = build_role_rows(outputs, validation)
    requirements = validation["committee_quality_gate_requirements"]
    verified_needed = max(
        0,
        requirements.get("min_evidence_verified_known_roles", requirements.get("min_verified_known_roles", 2))
        - validation.get("evidence_verified_known_role_count", validation.get("verified_known_role_count", 0)),
    )
    ok_source_needed = max(0, validation["committee_quality_gate_requirements"]["min_roles_with_ok_sources"] - validation.get("roles_with_ok_source_count", 0))
    successful_needed = max(0, validation["committee_quality_gate_requirements"]["min_successful_known_roles"] - validation.get("successful_known_role_count", 0))
    blockers: list[str] = []
    if validation.get("errors"):
        blockers.append("schema_errors")
    if validation.get("missing_required_external_roles"):
        blockers.append("missing_required_roles")
    if successful_needed:
        blockers.append("not_enough_successful_roles")
    if verified_needed:
        blockers.append("not_enough_evidence_verified_roles")
    if ok_source_needed:
        blockers.append("not_enough_roles_with_ok_sources")
    if validation.get("degraded_role_count", 0) >= max(1, validation.get("successful_known_role_count", 0)):
        blockers.append("all_or_most_successful_roles_degraded")

    repair_queue = []
    action_blocker_queue = []
    for row in role_rows:
        evidence_needs_repair = (
            not row["quality_credit"].get("evidence_verified_role")
            or not row["quality_credit"]["has_ok_source"]
            or bool(row.get("native_evidence_gaps"))
            or bool(row.get("source_verification_gaps"))
            or bool(row.get("plain_language_note_gaps"))
        )
        if evidence_needs_repair:
            repair_queue.append(
                {
                    "agent_id": row["agent_id"],
                    "priority": "P0" if row["present"] and not row["quality_credit"].get("evidence_verified_role") else "P1",
                    "needed": (
                        (row.get("native_evidence_gaps") or [])
                        + (row.get("source_verification_gaps") or [])
                        + (row.get("plain_language_note_gaps") or [])
                        + row["notes"]
                    )[:6]
                    or ["No evidence repair needed."],
                }
            )
        if row.get("action_readiness") not in {None, "ready_for_role_max_action", "watch_or_paper_ready"}:
            action_blocker_queue.append(
                {
                    "agent_id": row["agent_id"],
                    "priority": "P0",
                    "action_readiness": row.get("action_readiness"),
                    "blockers": (row.get("action_blockers") or row["notes"])[:6],
                }
            )

    status = "passed" if validation.get("committee_quality_gate_passed") and validation.get("valid") else "failed"
    return {
        "run_id": run_id or f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M')}-research-committee-quality-audit",
        "generated_at": utc_now(),
        "audit_version": "research-committee-quality-audit-v1",
        "source_path": str(Path(path)),
        "status": status,
        "max_allowed_action": "paper_only" if status == "passed" else "watch",
        "live_orders_enabled": False,
        "external_agent_validation": validation,
        "quality_gate_blockers": blockers,
        "quality_gap_summary": {
            "successful_roles_needed": successful_needed,
            "verified_roles_needed": verified_needed,
            "roles_with_ok_source_needed": ok_source_needed,
            "missing_required_roles": validation.get("missing_required_external_roles") or [],
        },
        "role_rows": role_rows,
        "repair_queue": repair_queue,
        "action_blocker_queue": action_blocker_queue,
        "normalized_agent_outputs": outputs,
        "operator_note": (
            "This audit only diagnoses external subagent output quality. It does not authorize execute_now "
            "and does not replace manual strategy promotion, double-80, or human confirmation gates."
        ),
    }


def render_markdown(audit: dict[str, Any]) -> str:
    validation = audit["external_agent_validation"]
    lines = [
        f"# Research Committee Quality Audit | {audit['run_id']}",
        "",
        "This audit diagnoses external subagent output quality only. It does not authorize live trading.",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Status | `{audit['status']}` |",
        f"| Committee quality gate | `{validation.get('committee_quality_gate_passed')}` |",
        f"| Valid schema | `{validation.get('valid')}` |",
        f"| Unique roles | {validation.get('unique_known_role_count')} |",
        f"| Successful roles | {validation.get('successful_known_role_count')} |",
        f"| Verified roles | {validation.get('verified_known_role_count')} |",
        f"| Evidence-verified roles | {validation.get('evidence_verified_known_role_count')} |",
        f"| Roles with ok source | {validation.get('roles_with_ok_source_count')} |",
        f"| Max allowed action | `{audit.get('max_allowed_action')}` |",
        "",
        "## Quality Gaps",
        "",
        "| Gap | Count |",
        "|---|---:|",
        f"| Successful roles needed | {audit['quality_gap_summary']['successful_roles_needed']} |",
        f"| Verified roles needed | {audit['quality_gap_summary']['verified_roles_needed']} |",
        f"| Roles with ok source needed | {audit['quality_gap_summary']['roles_with_ok_source_needed']} |",
        f"| Missing required roles | {len(audit['quality_gap_summary']['missing_required_roles'])} |",
        "",
        "## Role Detail",
        "",
        "| Role | Present | Data Quality | Evidence Quality | Action Readiness | Ok/Fallback/Failed | Signals | Action | Notes |",
        "|---|---|---|---|---|---:|---:|---|---|",
    ]
    for row in audit["role_rows"]:
        counts = row["source_counts"]
        notes = "<br>".join(row["notes"][:3]) if row["notes"] else "-"
        lines.append(
            f"| `{row['agent_id']}` | `{row['present']}` | `{row['data_quality']}` | "
            f"`{row.get('evidence_quality')}` | `{row.get('action_readiness')}` | "
            f"{counts['ok']}/{counts['fallback']}/{counts['failed']} | {row['signal_count']} | "
            f"`{row['recommended_max_action']}` | {notes} |"
        )
    lines.extend(["", "## Repair Queue", ""])
    for item in audit["repair_queue"][:12]:
        needed = "; ".join(item["needed"])
        lines.append(f"- `{item['priority']}` `{item['agent_id']}`: {needed}")
    if not audit["repair_queue"]:
        lines.append("- none")
    lines.extend(["", "## Action Blockers", ""])
    for item in audit.get("action_blocker_queue", [])[:12]:
        blockers = "; ".join(item.get("blockers") or [])
        lines.append(
            f"- `{item['priority']}` `{item['agent_id']}` `{item.get('action_readiness')}`: {blockers or 'No blocker details supplied.'}"
        )
    if not audit.get("action_blocker_queue"):
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit external subagent output quality for the Research Committee gate")
    parser.add_argument("--external-agent-outputs-json", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--normalized-output", default="", help="Optional normalized JSON package for reuse by report/monitor runners")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--fail-on-quality-gate", action="store_true", help="Exit non-zero when committee quality gate fails")
    args = parser.parse_args()

    audit = build_audit(args.external_agent_outputs_json, run_id=args.run_id or None)
    if args.output:
        write_json(Path(args.output), audit)
    if args.markdown_output:
        write_text(Path(args.markdown_output), render_markdown(audit))
    if args.normalized_output:
        write_json(
            Path(args.normalized_output),
            {
                "agent_outputs": audit["normalized_agent_outputs"],
                "quality_audit": {
                    key: value
                    for key, value in audit.items()
                    if key not in {"normalized_agent_outputs"}
                },
            },
        )
    if args.format == "markdown":
        print(render_markdown(audit))
    else:
        print(json.dumps(audit, ensure_ascii=False, indent=2))
    if args.fail_on_quality_gate and audit["status"] != "passed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
