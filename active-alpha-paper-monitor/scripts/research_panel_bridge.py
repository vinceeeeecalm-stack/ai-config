#!/usr/bin/env python3
"""Bridge externally collected subagent outputs into active-alpha handoffs.

Active monitor scripts do not spawn subagents themselves. When an external
orchestrator supplies the unified agent output JSON, this helper validates and
embeds it as a `research_panel`; otherwise callers keep the conservative
`research_panel_missing` path.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
MANUAL_RESEARCH_RUNNER = (
    WORKSPACE_ROOT
    / "manual-investment-strategy-operator"
    / "scripts"
    / "research_panel_runner.py"
)


def _load_manual_runner() -> Any | None:
    if not MANUAL_RESEARCH_RUNNER.exists():
        return None
    spec = importlib.util.spec_from_file_location("manual_research_panel_runner", MANUAL_RESEARCH_RUNNER)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _first_signal_summaries(agent_outputs: list[dict[str, Any]], direction: str) -> list[str]:
    summaries: list[str] = []
    for output in agent_outputs:
        for signal in output.get("signals") or []:
            if signal.get("direction") == direction and signal.get("summary"):
                summaries.append(str(signal["summary"]))
                break
    return summaries[:5]


def active_research_panel_overlay(
    external_agent_outputs_json: str | None,
    run_id: str,
    missing_reason: str,
) -> dict[str, Any]:
    if not external_agent_outputs_json:
        return {
            "research_panel_missing": True,
            "research_panel_missing_reason": missing_reason,
            "research_committee_degraded": True,
            "max_allowed_action": "watch",
            "external_agent_validation": {
                "valid": False,
                "errors": ["external_agent_outputs_json_missing"],
                "unique_known_role_count": 0,
                "successful_known_role_count": 0,
            },
        }

    runner = _load_manual_runner()
    if runner is None:
        return {
            "research_panel_missing": True,
            "research_panel_missing_reason": f"{missing_reason}; manual research validator unavailable",
            "research_committee_degraded": True,
            "max_allowed_action": "watch",
            "external_agent_validation": {
                "valid": False,
                "errors": ["manual_research_validator_unavailable"],
                "unique_known_role_count": 0,
                "successful_known_role_count": 0,
            },
        }

    try:
        agent_outputs = runner.load_external_agent_outputs(external_agent_outputs_json)
        validation = runner.validate_external_agent_outputs(agent_outputs)
    except Exception as exc:  # noqa: BLE001
        return {
            "research_panel_missing": True,
            "research_panel_missing_reason": f"{missing_reason}; external agent output load failed: {exc}",
            "research_committee_degraded": True,
            "max_allowed_action": "watch",
            "external_agent_validation": {
                "valid": False,
                "errors": [f"external_agent_output_load_failed: {exc}"],
                "unique_known_role_count": 0,
                "successful_known_role_count": 0,
            },
        }

    valid_external = (
        bool(validation.get("valid"))
        and int(validation.get("unique_known_role_count") or 0) >= 6
        and int(validation.get("successful_known_role_count") or 0) >= 6
        and bool(validation.get("committee_quality_gate_passed"))
        and int(validation.get("degraded_role_count") or 0) < int(validation.get("successful_known_role_count") or 0)
        and not validation.get("missing_required_external_roles")
    )
    if not valid_external:
        failure_reason = (
            "external subagent outputs were supplied but did not pass the committee quality gate"
        )
        return {
            "research_panel_missing": True,
            "research_panel_missing_reason": (
                f"{failure_reason}; external agent output invalid, fewer than 6 roles, or committee quality gate failed: "
                f"{validation.get('errors') or validation}"
            ),
            "research_committee_degraded": True,
            "max_allowed_action": "watch",
            "research_method": "external_subagent_outputs_quality_gate_failed",
            "external_agent_validation": validation,
        }

    active_agent_outputs: list[dict[str, Any]] = []
    for output in agent_outputs:
        active_output = dict(output)
        if active_output.get("recommended_max_action") == "execute_now":
            active_output["recommended_max_action"] = "conditional_action"
            failed_gates = list(active_output.get("failed_gates") or [])
            failed_gates.append("active_monitor_caps_execute_now_to_conditional_action")
            active_output["failed_gates"] = failed_gates
        active_agent_outputs.append(active_output)

    max_action = runner.panel_max_action(active_agent_outputs, degraded=False)
    disconfirming = [
        str(signal.get("summary"))
        for output in active_agent_outputs
        for signal in (output.get("signals") or [])
        if signal.get("direction") == "bearish" and signal.get("summary")
    ][:8]
    prior_status = runner.derive_prior_thesis_status(active_agent_outputs, degraded=False)
    panel = {
        "run_id": run_id,
        "agent_outputs": active_agent_outputs,
        "researcher_votes": runner.build_votes(active_agent_outputs),
        "bull_case": _first_signal_summaries(active_agent_outputs, "bullish"),
        "bear_case": _first_signal_summaries(active_agent_outputs, "bearish"),
        "base_case": _first_signal_summaries(active_agent_outputs, "mixed")
        or _first_signal_summaries(active_agent_outputs, "neutral"),
        "disconfirming_evidence": disconfirming,
        "prior_thesis_status": prior_status,
        "arbiter_decision": (
            "Active monitor embedded externally collected research panel. "
            "Manual skill must still re-check portfolio, cash rails, risk gates, and human confirmation."
        ),
        "old_thesis_reuse_allowed": runner.old_thesis_reuse_allowed(prior_status, active_agent_outputs, disconfirming),
        "missing_data_summary": sorted(
            {
                str(item)
                for output in active_agent_outputs
                for item in (output.get("missing_data") or [])
                if item
            }
        )[:20],
        "max_allowed_action": max_action,
        "research_committee_degraded": False,
        "research_panel_missing_reason": None,
        "research_method": "external_subagent_outputs_embedded_by_active_monitor",
        "external_roles_used": sorted(
            output.get("agent_id")
            for output in active_agent_outputs
            if output.get("agent_id")
        ),
        "local_fallback_roles": [],
        "missing_external_roles": list(validation.get("missing_required_external_roles") or []),
        "required_external_roles": list(validation.get("required_external_roles") or []),
        "external_agent_validation": validation,
    }
    return {
        "research_panel": panel,
        "research_panel_missing": False,
        "research_panel_missing_reason": None,
        "research_committee_degraded": False,
        "max_allowed_action": max_action,
    }
