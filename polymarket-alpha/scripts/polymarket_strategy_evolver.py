#!/usr/bin/env python3
"""Evidence-gated, paper-only strategy overlay evolution for Polymarket Alpha."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config" / "policy.json"
DEFAULT_OVERLAY = ROOT / "config" / "paper_strategy_overlay.json"
DEFAULT_LEDGER = ROOT / "data" / "paper_ledger.json"


def load_core():
    path = ROOT / "scripts" / "polymarket_alpha.py"
    spec = importlib.util.spec_from_file_location("polymarket_evolver_core", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load_core()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(*parts: Any) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def new_overlay(base_policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "polymarket-paper-strategy-overlay-v1",
        "base_policy_version": base_policy["version"],
        "overlay_version": "paper-overlay-v1",
        "paper_only": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "active_changes": [],
        "decision_history": [],
        "updated_at": now_iso(),
    }


def load_overlay(path: str | Path, base_policy: dict[str, Any]) -> dict[str, Any]:
    target = Path(path)
    overlay = core.read_json(target) if target.exists() else new_overlay(base_policy)
    if overlay.get("paper_only") is not True or overlay.get("live_orders_enabled") is not False or overlay.get("private_api_used") is not False:
        raise ValueError("unsafe strategy overlay")
    return overlay


def canonical_proposal(back_case: dict[str, Any]) -> dict[str, Any] | None:
    proposal = back_case.get("proposed_change")
    if not isinstance(proposal, dict) or not proposal.get("type"):
        return None
    return {
        "type": str(proposal["type"]),
        "value": proposal.get("value"),
        "domain": proposal.get("domain", back_case.get("domain")),
        "model_version": proposal.get("model_version", back_case.get("model_version")),
        "reason": proposal.get("reason", back_case.get("failure_category")),
    }


def validate_conservative(proposal: dict[str, Any], base_policy: dict[str, Any]) -> tuple[bool, str]:
    kind = proposal["type"]
    value = proposal.get("value")
    gate = base_policy["entry_gate"]
    try:
        if kind == "block_model_version":
            return (bool(proposal.get("model_version") or value), "model_version_required")
        if kind == "probability_cap":
            return (0.0 < float(value) <= 1.0, "probability_cap_must_be_in_0_1")
        if kind == "raise_min_net_edge":
            return (float(value) >= float(gate["min_net_edge_per_share"]), "cannot_lower_net_edge")
        if kind == "raise_min_independent_sources":
            return (int(value) >= int(gate["min_independent_sources"]), "cannot_lower_source_count")
        if kind == "raise_min_calibration_samples":
            return (int(value) >= int(gate["min_calibration_samples"]), "cannot_lower_calibration_samples")
        if kind == "lower_max_spread":
            return (0.0 < float(value) <= float(gate["max_spread_per_share"]), "cannot_widen_spread")
        if kind == "lower_max_price_impact":
            return (0.0 < float(value) <= float(gate["max_price_impact_per_share"]), "cannot_widen_price_impact")
    except (TypeError, ValueError):
        return False, "invalid_change_value"
    return False, "change_type_not_allowlisted"


def is_reviewed(back_case: dict[str, Any]) -> bool:
    return back_case.get("status") in {"reviewed", "completed", "complete"}


def is_explicit_error(back_case: dict[str, Any]) -> bool:
    return back_case.get("explicit_error_type") in {"data", "rule", "rules"} or back_case.get("explicit_data_or_rule_error") is True


def evolve(
    ledger: dict[str, Any], base_policy: dict[str, Any], overlay: dict[str, Any],
    min_similar_failures: int = 3, rollback_min_samples: int = 5,
) -> dict[str, Any]:
    core.assert_ledger_safe(ledger)
    updated = deepcopy(overlay)
    active_by_id = {row["change_id"]: row for row in updated.get("active_changes", [])}
    history_keys = {row.get("decision_key") for row in updated.get("decision_history", [])}
    groups: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []

    for back_case in ledger.get("back_cases", []):
        if not is_reviewed(back_case):
            continue
        proposal = canonical_proposal(back_case)
        if not proposal or not back_case.get("failure_category"):
            rejected.append({"back_case_id": back_case.get("back_case_id"), "reason": "structured_review_incomplete"})
            continue
        safe, reason = validate_conservative(proposal, base_policy)
        if not safe:
            rejected.append({"back_case_id": back_case.get("back_case_id"), "reason": reason})
            continue
        group_key = stable_hash(back_case.get("failure_category"), proposal)
        bucket = groups.setdefault(group_key, {"proposal": proposal, "failure_category": back_case["failure_category"], "cases": []})
        bucket["cases"].append(back_case)

    applied: list[str] = []
    for group_key, bucket in groups.items():
        distinct_cases = {str(row.get("paper_trade_id") or row.get("back_case_id")) for row in bucket["cases"]}
        explicit = any(is_explicit_error(row) for row in bucket["cases"])
        if len(distinct_cases) < min_similar_failures and not explicit:
            continue
        proposal = bucket["proposal"]
        change_id = f"pm-change-{stable_hash(bucket['failure_category'], proposal)}"
        # A stable change ID is never re-applied from the same historical evidence.
        # A reverted idea needs a materially different proposal (and therefore ID).
        if change_id in active_by_id:
            continue
        evidence_ids = sorted(str(row.get("back_case_id")) for row in bucket["cases"])
        change = {
            "change_id": change_id,
            "status": "paper_applied",
            "applied_at": now_iso(),
            "failure_category": bucket["failure_category"],
            "proposal": proposal,
            "evidence_back_case_ids": evidence_ids,
            "evidence_count": len(distinct_cases),
            "activation_rule": "explicit_data_or_rule_error" if explicit else f"{min_similar_failures}_similar_failures",
            "paper_only": True,
        }
        active_by_id[change_id] = change
        applied.append(change_id)
        decision_key = f"apply:{change_id}:{stable_hash(evidence_ids)}"
        if decision_key not in history_keys:
            updated.setdefault("decision_history", []).append({
                "decision_key": decision_key, "at": now_iso(), "action": "paper_apply",
                "change_id": change_id, "evidence_back_case_ids": evidence_ids,
            })
            history_keys.add(decision_key)

    reverted: list[str] = []
    closed = ledger.get("closed_positions", [])
    for change_id, change in active_by_id.items():
        if change.get("status") != "paper_applied":
            continue
        samples = [row for row in closed if change_id in (row.get("overlay_change_ids") or [])]
        if len(samples) < rollback_min_samples:
            continue
        wins = sum(float(row.get("net_pnl_usd", 0)) > 0 for row in samples)
        pnl = sum(float(row.get("net_pnl_usd", 0)) for row in samples)
        win_rate = wins / len(samples)
        if win_rate >= 0.40 and pnl >= 0:
            continue
        change["status"] = "paper_reverted"
        change["reverted_at"] = now_iso()
        change["rollback_evidence"] = {"forward_samples": len(samples), "win_rate": win_rate, "net_pnl_usd": pnl}
        reverted.append(change_id)
        decision_key = f"revert:{change_id}:{len(samples)}:{pnl:.8f}"
        if decision_key not in history_keys:
            updated.setdefault("decision_history", []).append({
                "decision_key": decision_key, "at": now_iso(), "action": "paper_revert",
                "change_id": change_id, "forward_samples": len(samples), "win_rate": win_rate, "net_pnl_usd": pnl,
            })

    updated["active_changes"] = sorted(active_by_id.values(), key=lambda row: row["change_id"])
    updated["updated_at"] = now_iso()
    effective_ids = sorted(row["change_id"] for row in updated["active_changes"] if row.get("status") == "paper_applied")
    return {
        "overlay": updated,
        "summary": {
            "reviewed_back_cases": sum(is_reviewed(row) for row in ledger.get("back_cases", [])),
            "applied": applied, "reverted": reverted, "rejected": rejected,
            "effective_change_ids": effective_ids,
            "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        },
    }


def self_test() -> dict[str, Any]:
    policy = core.read_json(DEFAULT_POLICY)
    ledger = core.new_ledger(policy)
    overlay = new_overlay(policy)
    proposal = {"type": "raise_min_net_edge", "value": 0.06, "reason": "edge_overstatement"}
    for index in range(3):
        ledger["back_cases"].append({
            "back_case_id": f"b{index}", "paper_trade_id": f"t{index}", "status": "reviewed",
            "failure_category": "edge_overstatement", "proposed_change": proposal,
        })
    result = evolve(ledger, policy, overlay)
    assert len(result["summary"]["applied"]) == 1
    assert result["overlay"]["paper_only"] is True and result["overlay"]["live_orders_enabled"] is False
    return {"status": "pass", "tests": ["three_failure_gate", "paper_only_overlay"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evolve a conservative paper-only strategy overlay")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--overlay", default=str(DEFAULT_OVERLAY))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    policy = core.read_json(args.policy)
    ledger = core.load_ledger(args.ledger, policy)
    overlay = load_overlay(args.overlay, policy)
    result = evolve(ledger, policy, overlay)
    if not args.dry_run:
        core.write_json(args.overlay, result["overlay"])
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
