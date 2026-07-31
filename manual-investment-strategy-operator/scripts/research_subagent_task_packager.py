#!/usr/bin/env python3
"""Package Research Evidence Backlog tasks for real subagent execution.

The evidence backlog says what evidence is missing. This packager turns it into
per-role task files and a bundle manifest that an external Codex/subagent
orchestrator can dispatch. It does not spawn agents, fetch market data, or
authorize trades.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
DEFAULT_OUTPUT_ROOT = MANUAL_ROOT / "subagent_tasks"
REQUIRED_OUTPUT_FIELDS = [
    "agent_id",
    "asset_scope",
    "sources_used",
    "source_refs",
    "evidence_items",
    "signals",
    "plain_language_notes",
    "confidence_pct",
    "data_quality",
    "missing_data",
    "failed_gates",
    "recommended_max_action",
    "what_would_change_my_mind",
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str | None) -> Any:
    if not path:
        return None
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def run_prefetch_cache(package_run_id: str, args: argparse.Namespace) -> str:
    output = args.prefetch_cache_output or f"/private/tmp/{package_run_id}-research-committee-role-native-evidence-cache.json"
    cmd = [
        sys.executable,
        str(MANUAL_ROOT / "scripts" / "research_committee_evidence_prefetch.py"),
        "--run-id",
        f"{package_run_id}-evidence-prefetch",
        "--output",
        output,
        "--crypto-symbols",
        args.prefetch_crypto_symbols,
        "--timeout-seconds",
        str(args.prefetch_timeout_seconds),
    ]
    if args.prefetch_current_tactical_symbol:
        cmd.extend(["--current-tactical-symbol", args.prefetch_current_tactical_symbol])
    result = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, check=False, timeout=args.prefetch_timeout_seconds * 4)
    if result.returncode != 0:
        raise SystemExit(
            "research_committee_evidence_prefetch failed: "
            + (result.stderr or result.stdout)[-2000:]
        )
    return output


def evidence_cache_context_paths(path: str) -> list[str]:
    paths = [path]
    try:
        cache = load_json(path)
    except Exception:
        return paths
    for artifact_path in cache.get("artifact_paths") or []:
        if artifact_path not in paths:
            paths.append(str(artifact_path))
    return paths


def dedupe_paths(paths: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in paths:
        if not item:
            continue
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def context_manifest(paths: list[str]) -> list[dict[str, Any]]:
    manifest = []
    for item in paths:
        path = Path(item)
        manifest.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else None,
                "note": "read-only context; do not modify from subagent",
            }
        )
    return manifest


def source_category_checklist(task: dict[str, Any]) -> list[str]:
    checklist = []
    for category in task.get("source_categories") or []:
        fields = ", ".join(category.get("fields") or [])
        examples = ", ".join(category.get("examples") or [])
        checklist.append(
            f"Cover `{category.get('id')}` using {examples or 'a fresh authoritative source'}; "
            f"capture fields: {fields or 'source, timestamp, status, coverage'}."
        )
    if not checklist:
        checklist.append("Record at least one fresh source with status=ok or explain why no source is available.")
    return checklist


def build_prompt(
    task: dict[str, Any],
    package_run_id: str,
    context_paths: list[str],
    output_target: str,
    expected_output_json: str,
) -> str:
    checklist = "\n".join(f"- {item}" for item in source_category_checklist(task))
    blockers = "\n".join(f"- {item}" for item in task.get("action_blockers_to_keep_visible") or [])
    required_fields = ", ".join(REQUIRED_OUTPUT_FIELDS)
    context_text = "\n".join(f"- {path}" for path in context_paths) or "- no context paths supplied"
    machine_requirements = "\n".join(f"- {item}" for item in task.get("machine_verification_requirements") or [])
    plain_language_requirements = "\n".join(f"- {item}" for item in task.get("plain_language_requirements") or [])
    return f"""You are `{task['agent_id']}` for research package `{package_run_id}`.

Work independently. Challenge prior conclusions. Do not give real trading instructions.
Return exactly one JSON object, not markdown.

Required output fields:
{required_fields}

Required structured evidence fields:
- `source_refs`: list of sources with `source_ref`, `name`, `url_or_provider`, `fresh_at`, `status`, `coverage`.
- `evidence_items`: list of machine-checkable fields with `field_id`, `value`, `unit`, `as_of`, `source_ref`, `verification_status`, `missing_reason`.
- `plain_language_notes`: list of short notes explaining hard terms such as funding, OI, spread, depth, slippage, drawdown, OOS, EV, and walk-forward.

Evidence gaps to repair:
{chr(10).join(f"- {item}" for item in task.get("needed") or []) or "- none"}

Research goal:
{task.get("goal")}

Required source checklist:
{checklist}

Machine-verifiable evidence rules:
{machine_requirements or "- Every ok source must include name, url_or_provider, fresh_at, status, and specific coverage."}

Plain-language and report-readability rules:
{plain_language_requirements or "- Add short plain-language notes for hard market terms inside signals when relevant."}

Pass condition:
{task.get("pass_condition")}

Action blockers that must remain visible even if evidence improves:
{blockers or "- none"}

Read-only context files:
{context_text}

Role-native evidence cache:
- If any context JSON has `cache_version` beginning with `research-committee-role-native-evidence-cache`, inspect it first even when the filename is different.
- You may cite only cache/artifact fields that are actually present.
- If the cache did not repair a required evidence item, keep that item in `missing_data`.
- The cache can improve evidence quality, but it does not remove action blockers such as broker cash, calibration, paper validation, or double-80 gates.
- When using hard terms such as walk-forward, OOS, EV, funding, OI, slippage, spread, depth or drawdown, add a short plain-language note in `signals` so the manual report can translate it for the user.

Output collection target for the orchestrator:
{output_target}

Suggested per-role output file:
{expected_output_json}

Rules:
- `sources_used` must include name, url_or_provider, fresh_at, status, and coverage.
- `source_refs` must give stable ids that evidence_items can cite.
- `evidence_items` must not cite a source_ref that is absent from source_refs.
- Use `verification_status=verified` only when the value is directly present in the cited source or local artifact.
- Use `status=ok` only for a fresh source you actually inspected.
- If a required source is unavailable, put the missing item in `missing_data` and downgrade `data_quality`.
- Do not use `execute_now`; maximum allowed role action is watch, paper_only, conditional_action, risk_alert, no_deploy, hold, or trim_review.
- Do not hide action blockers to get evidence credit.
- Social/news signals alone cannot authorize trading.
- The manual strategy operator is the final arbiter.
"""


def build_task_package(
    backlog: dict[str, Any],
    package_run_id: str,
    context_paths: list[str],
    output_dir: Path,
    external_output_target: str,
    package_manifest_path: str = "<path-to-task-package-json>",
) -> dict[str, Any]:
    task_files = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for index, task in enumerate(backlog.get("research_evidence_tasks") or [], start=1):
        agent_id = str(task.get("agent_id"))
        task_id = f"{package_run_id}-{index:02d}-{slug(agent_id)}"
        task_json_path = output_dir / f"{task_id}.task.json"
        prompt_path = output_dir / f"{task_id}.prompt.md"
        expected_output_path = output_dir / f"{task_id}.output.json"
        task_payload = {
            "task_id": task_id,
            "package_run_id": package_run_id,
            "agent_id": agent_id,
            "priority": task.get("priority"),
            "current_evidence_quality": task.get("current_evidence_quality"),
            "current_action_readiness": task.get("current_action_readiness"),
            "needed": task.get("needed") or [],
            "goal": task.get("goal"),
            "source_categories": task.get("source_categories") or [],
            "acceptance_checklist": source_category_checklist(task),
            "pass_condition": task.get("pass_condition"),
            "machine_verification_requirements": task.get("machine_verification_requirements") or [],
            "plain_language_requirements": task.get("plain_language_requirements") or [],
            "action_blockers_to_keep_visible": task.get("action_blockers_to_keep_visible") or [],
            "expected_output_fields": REQUIRED_OUTPUT_FIELDS,
            "external_output_target": external_output_target,
            "expected_output_json": str(expected_output_path),
            "context_paths": context_manifest(context_paths),
            "max_allowed_action_for_subagent": "conditional_action",
            "live_orders_enabled": False,
            "prompt_path": str(prompt_path),
        }
        write_json(task_json_path, task_payload)
        write_text(
            prompt_path,
            build_prompt(task, package_run_id, context_paths, external_output_target, str(expected_output_path)),
        )
        task_files.append(
            {
                "task_id": task_id,
                "agent_id": agent_id,
                "priority": task.get("priority"),
                "task_json": str(task_json_path),
                "prompt_markdown": str(prompt_path),
                "expected_output_json": str(expected_output_path),
            }
        )

    quality_audit_command = (
        "python3 manual-investment-strategy-operator/scripts/research_committee_quality_auditor.py "
        f"--external-agent-outputs-json {external_output_target} "
        f"--run-id {package_run_id}-committee-quality "
        f"--output manual-investment-strategy-operator/experiments/{package_run_id}-committee-quality.json "
        f"--markdown-output manual-investment-strategy-operator/reports/{package_run_id}-committee-quality.md "
        f"--normalized-output /private/tmp/{package_run_id}-external-subagent-outputs.normalized.json"
    )
    panel_command = (
        "python3 manual-investment-strategy-operator/scripts/research_panel_runner.py "
        f"--run-id {package_run_id}-research-panel "
        "--daily-context-json /private/tmp/20260530-evidence-action-context-v2.json "
        f"--external-agent-outputs-json /private/tmp/{package_run_id}-external-subagent-outputs.normalized.json "
        f"--output /private/tmp/{package_run_id}-research-panel.json"
    )
    output_collection_command = (
        "python3 manual-investment-strategy-operator/scripts/research_subagent_output_collector.py "
        f"--task-package-json {package_manifest_path} "
        f"--output {external_output_target} "
        f"--manifest-output manual-investment-strategy-operator/experiments/{package_run_id}-subagent-output-collection.json "
        f"--markdown-output manual-investment-strategy-operator/reports/{package_run_id}-subagent-output-collection.md "
        "--fail-on-missing"
    )
    return {
        "run_id": package_run_id,
        "generated_at": utc_now(),
        "package_version": "research-subagent-task-package-v2",
        "source_backlog_run_id": backlog.get("run_id"),
        "source_quality_audit": backlog.get("source_quality_audit"),
        "live_orders_enabled": False,
        "external_output_target": external_output_target,
        "context_paths": context_manifest(context_paths),
        "task_count": len(task_files),
        "task_files": task_files,
        "collection_contract": {
            "expected_format": "JSON array or object with agent_outputs list",
            "required_agent_output_fields": REQUIRED_OUTPUT_FIELDS,
            "no_execute_now": True,
            "fail_on_missing_expected_agents": True,
            "manual_arbitration_required": True,
        },
        "post_collection_commands": {
            "collect_outputs": output_collection_command,
            "quality_audit": quality_audit_command,
            "research_panel": panel_command,
        },
        "operator_note": (
            "Dispatch these prompts to real subagents. This package does not fetch data, spawn agents, "
        "or authorize real trades. When present, role-native evidence cache files are read-only context for subagents."
        ),
    }


def render_markdown(package: dict[str, Any]) -> str:
    lines = [
        f"# Research Subagent Task Package | {package['run_id']}",
        "",
        "This package is for dispatching real subagents. It does not authorize trades.",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Task count | {package['task_count']} |",
        f"| External output target | `{package['external_output_target']}` |",
        f"| Live orders enabled | `{package['live_orders_enabled']}` |",
        "",
        "## Tasks",
        "",
        "| Agent | Priority | Task JSON | Prompt | Expected Output |",
        "|---|---|---|---|---|",
    ]
    for task in package.get("task_files") or []:
        lines.append(
            f"| `{task['agent_id']}` | `{task.get('priority')}` | `{task['task_json']}` | "
            f"`{task['prompt_markdown']}` | `{task.get('expected_output_json')}` |"
        )
    lines.extend(
        [
            "",
            "## Post-Collection Commands",
            "",
            "```bash",
            package["post_collection_commands"]["collect_outputs"],
            package["post_collection_commands"]["quality_audit"],
            package["post_collection_commands"]["research_panel"],
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Package Research Evidence Backlog tasks for real subagent dispatch")
    parser.add_argument("--evidence-backlog-json", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--context-json", action="append", default=[], help="Read-only context JSON/path to include; repeatable")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--external-output-target", default="")
    parser.add_argument("--evidence-cache-json", default="", help="Optional role-native evidence cache JSON to include in every subagent task context")
    parser.add_argument("--auto-prefetch-evidence-cache", action="store_true", help="Run research_committee_evidence_prefetch.py and include its cache/artifacts")
    parser.add_argument("--prefetch-cache-output", default="")
    parser.add_argument("--prefetch-crypto-symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,ADAUSDT,NEARUSDT,INJUSDT,FETUSDT")
    parser.add_argument("--prefetch-current-tactical-symbol", default="")
    parser.add_argument("--prefetch-timeout-seconds", type=int, default=45)
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    backlog = load_json(args.evidence_backlog_json)
    package_run_id = args.run_id or f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M')}-subagent-task-package"
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_ROOT / package_run_id
    external_target = args.external_output_target or f"/private/tmp/{package_run_id}-external-subagent-outputs.json"
    cache_context_paths: list[str] = []
    evidence_cache = args.evidence_cache_json
    if args.auto_prefetch_evidence_cache:
        evidence_cache = run_prefetch_cache(package_run_id, args)
    if evidence_cache:
        cache_context_paths = evidence_cache_context_paths(evidence_cache)
    context_paths = dedupe_paths([args.evidence_backlog_json] + list(args.context_json or []) + cache_context_paths)
    package = build_task_package(
        backlog,
        package_run_id,
        context_paths,
        output_dir,
        external_target,
        package_manifest_path=args.output or "<path-to-task-package-json>",
    )

    if args.output:
        write_json(Path(args.output), package)
    if args.markdown_output:
        write_text(Path(args.markdown_output), render_markdown(package))

    if args.format == "markdown":
        print(render_markdown(package))
    else:
        print(json.dumps(package, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
