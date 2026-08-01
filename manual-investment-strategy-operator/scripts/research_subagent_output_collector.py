#!/usr/bin/env python3
"""Collect per-role subagent JSON outputs into one external output package.

This tool is the bridge after research_subagent_task_packager.py and before
research_committee_quality_auditor.py. It validates collection completeness and
writes a single JSON object with `agent_outputs` for the existing research
quality/audit pipeline. It does not fetch data or authorize trades.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
RESEARCH_PANEL_RUNNER = SCRIPT_DIR / "research_panel_runner.py"


def load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("research_panel_runner_for_output_collector", RESEARCH_PANEL_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {RESEARCH_PANEL_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = load_runner()

ACTION_RANK = {
    "watch": 0,
    "hold": 0,
    "no_deploy": 0,
    "block": 0,
    "risk_alert": 0,
    "trim_review": 1,
    "paper_only": 1,
    "conditional_action": 2,
    "execute_now": 3,
}


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


def normalize_output_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("agent_outputs"), list):
        return [item for item in payload["agent_outputs"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def expected_output_paths(package: dict[str, Any], outputs_dir: str | None = None) -> list[dict[str, Any]]:
    rows = []
    override_dir = Path(outputs_dir) if outputs_dir else None
    for task in package.get("task_files") or []:
        task_id = task.get("task_id")
        agent_id = task.get("agent_id")
        expected = task.get("expected_output_json")
        if not expected and task.get("task_json"):
            expected = str(Path(task["task_json"]).with_suffix(".output.json"))
        if override_dir and task_id:
            expected = str(override_dir / f"{task_id}.output.json")
        rows.append(
            {
                "task_id": task_id,
                "agent_id": agent_id,
                "expected_output_json": expected,
            }
        )
    return rows


def task_action_limits(package: dict[str, Any]) -> dict[str, str]:
    limits: dict[str, str] = {}
    for task in package.get("task_files") or []:
        task_json = task.get("task_json")
        agent_id = task.get("agent_id")
        if not agent_id:
            continue
        default_limit = "conditional_action"
        if task_json and Path(task_json).exists():
            try:
                payload = load_json(task_json)
                default_limit = payload.get("max_allowed_action_for_subagent") or default_limit
            except Exception:
                pass
        limits[str(agent_id)] = str(default_limit)
    return limits


def contract_errors_for_outputs(package: dict[str, Any], outputs: list[dict[str, Any]], missing_agents: list[str] | None = None) -> list[str]:
    errors: list[str] = []
    contract = package.get("collection_contract") or {}
    no_execute_now = bool(contract.get("no_execute_now", True))
    fail_on_missing = bool(contract.get("fail_on_missing_expected_agents", False))
    if fail_on_missing and missing_agents:
        errors.append(f"missing expected subagent outputs: {', '.join(missing_agents)}")
    limits = task_action_limits(package)
    for output in outputs:
        agent_id = str(output.get("agent_id") or "unknown_agent")
        action = str(output.get("recommended_max_action") or "watch")
        if no_execute_now and action == "execute_now":
            errors.append(f"{agent_id}.recommended_max_action=execute_now violates collection_contract.no_execute_now")
        max_allowed = limits.get(agent_id)
        if max_allowed and ACTION_RANK.get(action, 0) > ACTION_RANK.get(max_allowed, 0):
            errors.append(f"{agent_id}.recommended_max_action={action} exceeds task max_allowed_action_for_subagent={max_allowed}")
    return errors


def collect_outputs(package: dict[str, Any], explicit_paths: list[str], outputs_dir: str | None = None) -> dict[str, Any]:
    expected_rows = expected_output_paths(package, outputs_dir=outputs_dir)
    loaded_outputs: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []

    for row in expected_rows:
        path = Path(str(row.get("expected_output_json") or ""))
        if not path.exists():
            source_rows.append({**row, "exists": False, "loaded_count": 0, "error": None})
            continue
        try:
            outputs = normalize_output_payload(load_json(path))
            loaded_outputs.extend(outputs)
            source_rows.append({**row, "exists": True, "loaded_count": len(outputs), "error": None})
        except Exception as exc:  # noqa: BLE001
            source_rows.append({**row, "exists": True, "loaded_count": 0, "error": str(exc)})

    for path_str in explicit_paths:
        path = Path(path_str)
        try:
            outputs = normalize_output_payload(load_json(path))
            loaded_outputs.extend(outputs)
            source_rows.append(
                {
                    "task_id": None,
                    "agent_id": None,
                    "expected_output_json": str(path),
                    "exists": True,
                    "loaded_count": len(outputs),
                    "error": None,
                    "explicit": True,
                }
            )
        except Exception as exc:  # noqa: BLE001
            source_rows.append(
                {
                    "task_id": None,
                    "agent_id": None,
                    "expected_output_json": str(path),
                    "exists": path.exists(),
                    "loaded_count": 0,
                    "error": str(exc),
                    "explicit": True,
                }
            )

    by_agent: dict[str, dict[str, Any]] = {}
    duplicate_agents: list[str] = []
    for output in loaded_outputs:
        agent_id = output.get("agent_id")
        if not agent_id:
            continue
        if agent_id in by_agent:
            duplicate_agents.append(str(agent_id))
        by_agent[str(agent_id)] = output

    expected_agents = [str(row["agent_id"]) for row in expected_rows if row.get("agent_id")]
    collected_agents = sorted(by_agent)
    missing_agents = [agent for agent in expected_agents if agent not in by_agent]
    outputs = [
        RUNNER.normalize_external_agent_output(by_agent[agent], index)
        if hasattr(RUNNER, "normalize_external_agent_output")
        else by_agent[agent]
        for index, agent in enumerate(sorted(by_agent))
    ]
    validation = RUNNER.validate_external_agent_outputs(outputs)
    contract_errors = contract_errors_for_outputs(package, outputs, missing_agents=missing_agents)
    return {
        "agent_outputs": outputs,
        "collection_manifest": {
            "generated_at": utc_now(),
            "source_package_run_id": package.get("run_id"),
            "source_package_version": package.get("package_version"),
            "expected_task_count": package.get("task_count"),
            "expected_agents": expected_agents,
            "collected_agent_count": len(collected_agents),
            "collected_agents": collected_agents,
            "missing_agents": missing_agents,
            "duplicate_agents_overwritten": sorted(set(duplicate_agents)),
            "source_rows": source_rows,
            "external_output_target": package.get("external_output_target"),
            "live_orders_enabled": False,
            "manual_arbitration_required": True,
            "contract_errors": contract_errors,
            "contract_passed": not contract_errors,
            "validation": validation,
        },
    }


def render_markdown(collection: dict[str, Any]) -> str:
    manifest = collection["collection_manifest"]
    lines = [
        f"# Research Subagent Output Collection | {manifest.get('source_package_run_id')}",
        "",
        "This collection packages per-role subagent outputs for the Research Committee quality audit. It does not authorize trades.",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Expected tasks | {manifest.get('expected_task_count')} |",
        f"| Collected agents | {manifest.get('collected_agent_count')} |",
        f"| Missing agents | {len(manifest.get('missing_agents') or [])} |",
        f"| Quality gate passed | `{(manifest.get('validation') or {}).get('committee_quality_gate_passed')}` |",
        f"| Contract passed | `{manifest.get('contract_passed')}` |",
        f"| Contract errors | {len(manifest.get('contract_errors') or [])} |",
        f"| Live orders enabled | `{manifest.get('live_orders_enabled')}` |",
        "",
        "## Missing Agents",
        "",
    ]
    if manifest.get("missing_agents"):
        for agent in manifest["missing_agents"]:
            lines.append(f"- `{agent}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Contract Errors", ""])
    if manifest.get("contract_errors"):
        for error in manifest["contract_errors"]:
            lines.append(f"- {error}")
    else:
        lines.append("- none")
    lines.extend(["", "## Sources", "", "| Agent | Exists | Loaded | Path | Error |", "|---|---|---:|---|---|"])
    for row in manifest.get("source_rows") or []:
        lines.append(
            f"| `{row.get('agent_id') or '-'}` | `{row.get('exists')}` | {row.get('loaded_count')} | "
            f"`{row.get('expected_output_json')}` | {row.get('error') or '-'} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect per-role subagent outputs into an external agent output package")
    parser.add_argument("--task-package-json", required=True)
    parser.add_argument("--outputs-dir", default="")
    parser.add_argument("--agent-output-json", action="append", default=[], help="Additional explicit output JSON; repeatable")
    parser.add_argument("--output", default="")
    parser.add_argument("--manifest-output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--fail-on-missing", action="store_true")
    args = parser.parse_args()

    package = load_json(args.task_package_json)
    collection = collect_outputs(package, args.agent_output_json or [], outputs_dir=args.outputs_dir or None)
    output_path = args.output or package.get("external_output_target")
    if output_path:
        write_json(Path(output_path), {"agent_outputs": collection["agent_outputs"]})
    if args.manifest_output:
        write_json(Path(args.manifest_output), collection)
    if args.markdown_output:
        write_text(Path(args.markdown_output), render_markdown(collection))
    if args.format == "markdown":
        print(render_markdown(collection))
    else:
        print(json.dumps(collection, ensure_ascii=False, indent=2))

    if args.fail_on_missing and collection["collection_manifest"]["missing_agents"]:
        return 1
    if collection["collection_manifest"].get("contract_errors"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
