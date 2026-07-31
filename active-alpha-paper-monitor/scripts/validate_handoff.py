#!/usr/bin/env python3
"""Validate active-alpha handoff files before manual consumption.

The validator is intentionally conservative: a handoff can be valid but
degraded when it has `research_panel_missing=true` with a clear reason. It
fails only on missing safety-critical fields or accidental live-order state.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ALLOWED_CANDIDATE_TYPES = {
    "watch_candidate",
    "paper_candidate",
    "us_open_scan",
    "social_key_person_intel",
    "daily_crypto_paper_auto_trader",
    "fast_crypto_paper_auto_trader",
    "sunday_crypto_realistic_paper_loop",
    "paper_position_exit_monitor",
    "event_alert",
    "risk_alert",
}

VALID_RESEARCH_AGENT_IDS = {
    "prior_thesis_challenge_agent",
    "portfolio_state_agent",
    "macro_regime_agent",
    "crypto_market_agent",
    "onchain_defi_agent",
    "social_news_agent",
    "us_equity_alpha_agent",
    "backtest_validation_agent",
}

REQUIRED_AGENT_FIELDS = {
    "agent_id",
    "asset_scope",
    "sources_used",
    "signals",
    "confidence_pct",
    "data_quality",
    "missing_data",
    "failed_gates",
    "recommended_max_action",
    "what_would_change_my_mind",
}

REQUIRED_SOURCE_FIELDS = {"name", "url_or_provider", "fresh_at", "status", "coverage"}
REQUIRED_SIGNAL_FIELDS = {"asset", "signal_type", "direction", "summary", "time_window"}
VALID_SOURCE_STATUS = {"ok", "fallback", "failed"}
VALID_DATA_QUALITY = {"verified", "degraded", "disputed", "stale", "missing"}
VALID_ACTIONS = {"watch", "paper_only", "conditional_action", "risk_alert", "no_deploy", "block", "hold", "trim_review"}
DEGRADED_ALLOWED_ACTIONS = {"watch", "paper_only", "risk_alert", "no_deploy", "hold", "trim_review"}
REQUIRED_EXTERNAL_ROLE_COUNT = len(VALID_RESEARCH_AGENT_IDS)
ACTION_RANK = {
    "no_deploy": 0,
    "block": 0,
    "risk_alert": 0,
    "watch": 1,
    "hold": 1,
    "trim_review": 1,
    "paper_only": 2,
    "conditional_action": 3,
    "small_probe_review": 4,
    "small_probe_candidate": 4,
    "execute_now": 5,
}

REQUIRED_COMMON_FIELDS = {
    "handoff_id",
    "created_at",
    "source_skill",
    "target_skill",
    "candidate_type",
    "live_orders_enabled",
}

REQUIRED_RESEARCH_PANEL_FIELDS = {
    "run_id",
    "agent_outputs",
    "researcher_votes",
    "bull_case",
    "bear_case",
    "base_case",
    "disconfirming_evidence",
    "prior_thesis_status",
    "arbiter_decision",
    "old_thesis_reuse_allowed",
    "missing_data_summary",
    "max_allowed_action",
    "research_committee_degraded",
    "research_panel_missing_reason",
    "research_method",
    "external_roles_used",
    "local_fallback_roles",
    "missing_external_roles",
    "required_external_roles",
    "external_agent_validation",
}
REQUIRED_RISK_PATH_FIELDS = {
    "lane",
    "calculation_version",
    "benchmark",
    "risk_free_rate",
    "windows",
    "sharpe",
    "sortino",
    "information_ratio",
    "max_drawdown",
    "quality_score",
    "persistence_label",
    "flags",
    "data_quality",
    "evidence_ids",
    "live_gate_effect",
}


def validate_risk_adjusted_path(path: Any, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(path, dict):
        return [f"{label} must be an object"]
    missing = sorted(REQUIRED_RISK_PATH_FIELDS - set(path))
    if missing:
        errors.append(f"{label} missing fields: {missing}")
    if path.get("lane") not in {"trend_continuation", "value_repair", "longterm_timing"}:
        errors.append(f"{label}.lane is unsupported")
    if path.get("live_gate_effect") != "none_until_promotion":
        errors.append(f"{label}.live_gate_effect must be none_until_promotion")
    adjustment = path.get("ranking_adjustment_points")
    if not isinstance(adjustment, (int, float)):
        errors.append(f"{label}.ranking_adjustment_points must be numeric")
    else:
        limit = 4.0 if path.get("lane") == "value_repair" else 7.5
        if abs(float(adjustment)) > limit:
            errors.append(f"{label}.ranking_adjustment_points exceeds ±{limit}")
    windows = path.get("windows")
    if not isinstance(windows, dict):
        errors.append(f"{label}.windows must be an object")
    else:
        for window in ("daily_20", "daily_60"):
            if not isinstance(windows.get(window), dict):
                errors.append(f"{label}.windows.{window} is required")
    risk_free = path.get("risk_free_rate")
    if not isinstance(risk_free, dict):
        errors.append(f"{label}.risk_free_rate must be an object")
    elif risk_free.get("fallback_used") is False and not risk_free.get("evidence_id"):
        errors.append(f"{label}.risk_free_rate needs evidence_id when fallback is false")
    return errors


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iter_handoff_paths(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(path.glob("*handoff*.json"))
    raise FileNotFoundError(path)


def has_manual_review_flag(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get("requires_manual_review")
        or payload.get("requires_manual_review_before_real_money")
        or payload.get("manual_review_required")
    )


def validate_agent_output(output: dict[str, Any], index: int) -> list[str]:
    errors: list[str] = []
    if not isinstance(output, dict):
        return [f"research_panel.agent_outputs[{index}] must be an object"]

    agent_id = output.get("agent_id")
    label = agent_id or f"agent_outputs[{index}]"
    missing = sorted(REQUIRED_AGENT_FIELDS - set(output))
    if missing:
        errors.append(f"{label} missing fields: {missing}")
    if agent_id not in VALID_RESEARCH_AGENT_IDS:
        errors.append(f"{label} is not a recognized research role")
    if output.get("data_quality") not in VALID_DATA_QUALITY:
        errors.append(f"{label}.data_quality must be one of {sorted(VALID_DATA_QUALITY)}")
    if output.get("recommended_max_action") not in VALID_ACTIONS:
        errors.append(f"{label}.recommended_max_action must be one of {sorted(VALID_ACTIONS)}")
    if not isinstance(output.get("confidence_pct"), (int, float)) or not 0 <= output.get("confidence_pct") <= 100:
        errors.append(f"{label}.confidence_pct must be 0-100")

    for field in ["asset_scope", "sources_used", "signals", "missing_data", "failed_gates", "what_would_change_my_mind"]:
        if not isinstance(output.get(field), list):
            errors.append(f"{label}.{field} must be a list")

    for source_index, item in enumerate(output.get("sources_used") or []):
        if not isinstance(item, dict):
            errors.append(f"{label}.sources_used[{source_index}] must be an object")
            continue
        missing_source = sorted(REQUIRED_SOURCE_FIELDS - set(item))
        if missing_source:
            errors.append(f"{label}.sources_used[{source_index}] missing fields: {missing_source}")
        if item.get("status") not in VALID_SOURCE_STATUS:
            errors.append(f"{label}.sources_used[{source_index}].status must be one of {sorted(VALID_SOURCE_STATUS)}")

    for signal_index, item in enumerate(output.get("signals") or []):
        if not isinstance(item, dict):
            errors.append(f"{label}.signals[{signal_index}] must be an object")
            continue
        missing_signal = sorted(REQUIRED_SIGNAL_FIELDS - set(item))
        if missing_signal:
            errors.append(f"{label}.signals[{signal_index}] missing fields: {missing_signal}")

    return errors


def validate_payload(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    missing = sorted(REQUIRED_COMMON_FIELDS - set(payload))
    if missing:
        errors.append(f"missing common fields: {missing}")

    if payload.get("source_skill") != "active-alpha-paper-monitor":
        errors.append("source_skill must be active-alpha-paper-monitor")
    if payload.get("target_skill") != "manual-investment-strategy-operator":
        errors.append("target_skill must be manual-investment-strategy-operator")

    candidate_type = payload.get("candidate_type")
    if candidate_type not in ALLOWED_CANDIDATE_TYPES:
        errors.append(f"unsupported candidate_type: {candidate_type!r}")

    if payload.get("live_orders_enabled") is not False:
        errors.append("live_orders_enabled must be false")

    if not has_manual_review_flag(payload):
        errors.append("handoff must require manual review")

    research_panel = payload.get("research_panel")
    missing_research = payload.get("research_panel_missing")
    missing_reason = payload.get("research_panel_missing_reason")
    committee_degraded = payload.get("research_committee_degraded")

    if research_panel:
        if not isinstance(research_panel, dict):
            errors.append("research_panel must be an object")
        else:
            missing_panel = sorted(REQUIRED_RESEARCH_PANEL_FIELDS - set(research_panel))
            if missing_panel:
                errors.append(f"research_panel missing fields: {missing_panel}")
            agent_outputs = research_panel.get("agent_outputs")
            if not isinstance(agent_outputs, list):
                errors.append("research_panel.agent_outputs must be a list")
            elif len(agent_outputs) < 6:
                errors.append("research_panel must include at least 6 agent outputs")
            else:
                role_ids = [item.get("agent_id") for item in agent_outputs if isinstance(item, dict)]
                unique_roles = {role for role in role_ids if role in VALID_RESEARCH_AGENT_IDS}
                if len(unique_roles) < 6:
                    errors.append("research_panel must include at least 6 unique recognized research roles")
                duplicates = sorted(role for role in unique_roles if role_ids.count(role) > 1)
                if duplicates:
                    errors.append(f"research_panel has duplicate research roles: {duplicates}")
                for index, output in enumerate(agent_outputs):
                    errors.extend(validate_agent_output(output, index))
            panel_degraded = research_panel.get("research_committee_degraded")
            if panel_degraded is not committee_degraded:
                errors.append("top-level research_committee_degraded must match research_panel.research_committee_degraded")
            panel_max_allowed = research_panel.get("max_allowed_action")
            if payload.get("max_allowed_action") and panel_max_allowed and payload.get("max_allowed_action") != panel_max_allowed:
                warnings.append("top-level max_allowed_action differs from research_panel.max_allowed_action; using the more conservative ceiling")
            validation = research_panel.get("external_agent_validation")
            if research_panel.get("research_method") == "external_subagent_outputs_embedded_by_active_monitor":
                if not isinstance(validation, dict):
                    errors.append("external research_panel must include external_agent_validation object")
                else:
                    if validation.get("valid") is not True:
                        errors.append("external_agent_validation.valid must be true for embedded external research_panel")
                    if int(validation.get("unique_known_role_count") or 0) < 6:
                        errors.append("external_agent_validation.unique_known_role_count must be at least 6")
                    if int(validation.get("successful_known_role_count") or 0) < 6:
                        errors.append("external_agent_validation.successful_known_role_count must be at least 6")
                    if validation.get("committee_quality_gate_passed") is not True:
                        errors.append("external_agent_validation.committee_quality_gate_passed must be true for embedded external research_panel")
                    evidence_verified = int(
                        validation.get("evidence_verified_known_role_count")
                        if validation.get("evidence_verified_known_role_count") is not None
                        else validation.get("verified_known_role_count") or 0
                    )
                    if evidence_verified < 6:
                        errors.append("external_agent_validation.evidence_verified_known_role_count must be at least 6")
                    if int(validation.get("roles_with_ok_source_count") or 0) < 6:
                        errors.append("external_agent_validation.roles_with_ok_source_count must be at least 6")
                    missing_required = validation.get("missing_required_external_roles") or []
                    if missing_required:
                        errors.append(f"external_agent_validation missing required external roles: {missing_required}")
                    if int(validation.get("degraded_role_count") or 0) >= int(validation.get("successful_known_role_count") or 0):
                        errors.append("external_agent_validation cannot have all successful roles degraded")
                    if int(validation.get("unique_known_role_count") or 0) < REQUIRED_EXTERNAL_ROLE_COUNT:
                        warnings.append("default active research committee expects all 8 roles; missing roles force manual re-arbitration")
    elif missing_research is True and missing_reason:
        warnings.append("research_panel missing with reason; handoff is degraded and cannot authorize execute_now")
        if committee_degraded is not True:
            errors.append("research_panel_missing=true must set research_committee_degraded=true")
    else:
        errors.append("handoff must include research_panel or research_panel_missing_reason")

    max_allowed = payload.get("max_allowed_action") or (research_panel or {}).get("max_allowed_action")
    monitor_recommendation = payload.get("monitor_recommendation")
    if missing_research and max_allowed not in DEGRADED_ALLOWED_ACTIONS:
        errors.append("missing research_panel cannot allow actions above watch/paper_only/risk_alert/no_deploy")
    if missing_research and monitor_recommendation in {"execute_now", "small_probe_review", "small_probe_candidate"}:
        errors.append("missing research_panel cannot use execute_now/small_probe monitor_recommendation")
    degraded_research = bool(missing_research or committee_degraded)
    if max_allowed and monitor_recommendation:
        max_rank = ACTION_RANK.get(max_allowed)
        recommendation_rank = ACTION_RANK.get(monitor_recommendation)
        if max_rank is not None and recommendation_rank is not None and recommendation_rank > max_rank:
            message = (
                "monitor_recommendation is above max_allowed_action; write the higher pre-research grade to "
                "pre_research_candidate_grade and keep monitor_recommendation within the real-action ceiling"
            )
            if degraded_research:
                errors.append(message)
            else:
                warnings.append(message)
    for index, candidate in enumerate(payload.get("candidates") or []):
        if not isinstance(candidate, dict):
            continue
        if candidate.get("risk_adjusted_path") is not None:
            errors.extend(
                validate_risk_adjusted_path(
                    candidate.get("risk_adjusted_path"),
                    f"candidates[{index}].risk_adjusted_path",
                )
            )
        candidate_action = candidate.get("monitor_recommendation")
        if missing_research and candidate_action in {"execute_now", "small_probe_review", "small_probe_candidate"}:
            errors.append(f"candidates[{index}] cannot use {candidate_action} when research_panel is missing")
        if max_allowed and candidate_action:
            max_rank = ACTION_RANK.get(max_allowed)
            candidate_rank = ACTION_RANK.get(candidate_action)
            if max_rank is not None and candidate_rank is not None and candidate_rank > max_rank:
                message = (
                    f"candidates[{index}] monitor_recommendation is above max_allowed_action; write the higher "
                    "pre-research grade to pre_research_candidate_grade and keep candidate monitor_recommendation "
                    "within the real-action ceiling"
                )
                if degraded_research:
                    errors.append(message)
                else:
                    warnings.append(message)

    status = "fail" if errors else ("degraded" if warnings else "pass")
    return {
        "path": str(path),
        "status": status,
        "candidate_type": candidate_type,
        "handoff_id": payload.get("handoff_id"),
        "errors": errors,
        "warnings": warnings,
    }


def print_markdown(results: list[dict[str, Any]]) -> None:
    print("# Handoff Validation")
    print()
    print("| Status | Candidate Type | Handoff | Issues |")
    print("|---|---|---|---|")
    for result in results:
        issues = result["errors"] or result["warnings"] or ["ok"]
        issue_text = "; ".join(issues)
        print(f"| {result['status']} | {result.get('candidate_type')} | `{result.get('handoff_id')}` | {issue_text} |")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate active-alpha handoff JSON files")
    parser.add_argument("path", help="Handoff JSON file or directory")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    args = parser.parse_args()

    results = []
    for path in iter_handoff_paths(Path(args.path)):
        try:
            payload = load_json(path)
            if not isinstance(payload, dict):
                raise ValueError("handoff root must be a JSON object")
            results.append(validate_payload(path, payload))
        except Exception as exc:  # noqa: BLE001 - validation CLI should report all files.
            results.append({
                "path": str(path),
                "status": "fail",
                "candidate_type": None,
                "handoff_id": None,
                "errors": [str(exc)],
                "warnings": [],
            })

    if args.format == "json":
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    else:
        print_markdown(results)

    return 1 if any(result["status"] == "fail" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
