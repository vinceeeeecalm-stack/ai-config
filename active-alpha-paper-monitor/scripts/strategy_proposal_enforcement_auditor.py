#!/usr/bin/env python3
"""Audit whether strategy proposal decisions are reflected in paper gates.

Read-only. This script does not mutate strategy config, the paper ledger,
automation state, or live accounts. It verifies that proposal decisions from
the decision board are either enforced by recovery/capital gates or clearly
marked as pending paper-only validation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
EXPERIMENT_DIR = ACTIVE_ROOT / "experiments"
REPORT_PATH = ACTIVE_ROOT / "reports" / "STRATEGY_PROPOSAL_ENFORCEMENT_AUDIT.md"
EXPERIMENT_PATH = EXPERIMENT_DIR / "strategy-proposal-enforcement-audit.json"
DECISION_PATH = EXPERIMENT_DIR / "strategy-proposal-decision-board.json"
LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))


def now_local() -> dt.datetime:
    return dt.datetime.now(LOCAL_TZ).replace(microsecond=0)


def read_json(path: Path | None, default: Any = None) -> Any:
    if not path:
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def latest(pattern: str, directory: Path = EXPERIMENT_DIR) -> Path | None:
    files = sorted(directory.glob(pattern))
    return files[-1] if files else None


def rel(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def name_set(rows: Any) -> set[str]:
    if not isinstance(rows, list):
        return set()
    return {norm(item.get("name")) for item in rows if isinstance(item, dict) and item.get("name")}


def affected_names(decision: dict[str, Any]) -> list[str]:
    impact = decision.get("impact") if isinstance(decision.get("impact"), dict) else {}
    return [str(item) for item in (impact.get("affected_names") or []) if item]


def no_entry_block_hits(runner: dict[str, Any], names: list[str]) -> list[str]:
    no_entry = runner.get("no_entry_summary") if isinstance(runner.get("no_entry_summary"), dict) else {}
    candidates = no_entry.get("top_blocked_candidates") if isinstance(no_entry.get("top_blocked_candidates"), list) else []
    needles = {norm(item) for item in names}
    hits: list[str] = []
    for row in candidates:
        if not isinstance(row, dict):
            continue
        fields = [
            row.get("entry_mode_estimate"),
            row.get("strategy_family"),
            row.get("interval"),
            row.get("validation_recovery_match"),
            row.get("primary_block_reason"),
        ]
        text = " ".join(norm(field) for field in fields)
        if any(needle and needle in text for needle in needles):
            hits.append(str(row.get("symbol") or row.get("primary_block_reason") or "blocked_candidate"))
    return hits[:8]


def recovery_enforcement(decision: dict[str, Any], plan: dict[str, Any], runner: dict[str, Any]) -> dict[str, Any]:
    names = affected_names(decision)
    normalized_names = {norm(item) for item in names}
    retired_or_cooldown = (
        name_set(plan.get("retired_entry_modes"))
        | name_set(plan.get("cooldown_entry_modes"))
        | name_set(plan.get("retired_strategy_families"))
        | name_set(plan.get("cooldown_strategy_families"))
        | name_set(plan.get("weak_intervals"))
        | name_set(plan.get("weak_symbols"))
    )
    matched = sorted(name for name in normalized_names if name in retired_or_cooldown)
    missing = sorted(name for name in normalized_names if name not in retired_or_cooldown)
    block_hits = no_entry_block_hits(runner, names)
    if normalized_names and not missing:
        status = "enforced_by_recovery_plan"
    elif matched:
        status = "partially_enforced_by_recovery_plan"
    else:
        status = "not_enforced_by_recovery_plan"
    if block_hits and status.startswith("enforced"):
        status = "enforced_and_blocking_current_candidates"
    return {
        "status": status,
        "affected_names": names,
        "matched_gate_names": matched,
        "missing_gate_names": missing,
        "current_block_hits": block_hits,
    }


def capital_enforcement(capital: dict[str, Any]) -> dict[str, Any]:
    policy = capital.get("policy") or capital.get("allocation_policy") or capital.get("capital_policy")
    per_trade = capital.get("per_trade_notional_usd") or capital.get("max_single_trade_notional_usd")
    max_deploy = capital.get("max_deployable_now_usd")
    max_new_positions = capital.get("max_new_positions_now")
    phase2 = capital.get("phase2_status") or capital.get("phase2_quality_status")
    try:
        per_trade_float = float(per_trade or 0)
    except (TypeError, ValueError):
        per_trade_float = 0.0
    try:
        max_positions_int = int(max_new_positions if max_new_positions is not None else 1)
    except (TypeError, ValueError):
        max_positions_int = 1
    conservative = (
        policy == "minimum_quality_scout_only"
        and per_trade_float <= 25.0
        and max_positions_int <= 1
        and phase2 != "passed"
    )
    return {
        "status": "enforced_by_capital_policy" if conservative else "not_enforced_by_capital_policy",
        "policy": policy,
        "per_trade_notional_usd": per_trade,
        "max_deployable_now_usd": max_deploy,
        "max_new_positions_now": max_new_positions,
        "phase2_status": phase2,
    }


def evaluate_decision(
    decision: dict[str, Any],
    plan: dict[str, Any],
    runner: dict[str, Any],
    capital: dict[str, Any],
) -> dict[str, Any]:
    decision_type = str(decision.get("decision") or "")
    change_type = str(decision.get("change_type") or decision.get("type") or "")
    if decision_type in {"enforce_retirement_or_cooldown", "enforce_interval_filter"}:
        evidence = recovery_enforcement(decision, plan, runner)
    elif decision_type == "keep_conservative_sizing" or change_type == "monthly_target_recovery_posture":
        evidence = capital_enforcement(capital)
    elif decision_type == "paper_entry_filter_ab_test":
        evidence = recovery_enforcement(decision, plan, runner)
        if evidence["status"] == "not_enforced_by_recovery_plan":
            evidence["status"] = "pending_entry_filter_ab_test"
        else:
            evidence["status"] = f"{evidence['status']}_plus_pending_ab_test"
    elif decision_type == "paper_exit_rule_ab_test":
        evidence = {
            "status": "awaiting_next_open_position_exit_rule_test",
            "affected_names": affected_names(decision),
            "matched_gate_names": [],
            "missing_gate_names": [],
            "current_block_hits": [],
        }
    else:
        evidence = {"status": "manual_review_only", "affected_names": affected_names(decision)}
    blocking = evidence["status"] in {"not_enforced_by_recovery_plan", "not_enforced_by_capital_policy"}
    return {
        "proposed_change_id": decision.get("proposed_change_id"),
        "change_type": change_type,
        "decision": decision_type,
        "priority_score": decision.get("priority_score"),
        "enforcement_status": evidence.get("status"),
        "evidence": evidence,
        "blocking": blocking,
        "paper_only": True,
        "config_mutation_allowed": False,
        "ledger_mutation_allowed": False,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def build_record() -> dict[str, Any]:
    created = now_local()
    decision = read_json(DECISION_PATH, {}) or {}
    runner_path = latest("*validation-progress-runner.json")
    runner = read_json(runner_path, {}) or {}
    plan = ((runner.get("post_validation") or {}).get("stdout_json") or {}).get("validation_recovery_plan") or {}
    capital_path = latest("*paper-capital-allocation-audit.json")
    capital_payload = read_json(capital_path, {}) or {}
    if isinstance(capital_payload.get("allocation_decision"), dict):
        capital = dict(capital_payload.get("allocation_decision") or {})
        phase2_state = capital_payload.get("phase2_quality_state") if isinstance(capital_payload.get("phase2_quality_state"), dict) else {}
        capital.setdefault("phase2_status", phase2_state.get("status"))
    elif isinstance(capital_payload.get("allocation"), dict):
        capital = dict(capital_payload.get("allocation") or {})
    else:
        capital = capital_payload
    rows = [
        evaluate_decision(row, plan, runner, capital)
        for row in (decision.get("decisions") or decision.get("top_decisions") or [])
        if isinstance(row, dict)
    ]
    enforced_count = sum(1 for row in rows if str(row.get("enforcement_status") or "").startswith("enforced"))
    pending_count = sum(1 for row in rows if "pending" in str(row.get("enforcement_status") or "") or "awaiting" in str(row.get("enforcement_status") or ""))
    blocking_count = sum(1 for row in rows if row.get("blocking"))
    status = "pass" if rows and blocking_count == 0 else ("warn" if rows else "missing_decisions")
    return {
        "run_id": f"{created.strftime('%Y%m%d-%H%M%S')}-strategy-proposal-enforcement-audit",
        "created_at": created.isoformat(),
        "scope": "paper_only_strategy_proposal_enforcement",
        "status": status,
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "ledger_mutated": False,
        "config_mutated": False,
        "source_decision_board": rel(DECISION_PATH),
        "source_runner": rel(runner_path),
        "source_capital_allocation": rel(capital_path),
        "summary": {
            "decision_count": len(rows),
            "enforced_count": enforced_count,
            "pending_test_count": pending_count,
            "blocking_count": blocking_count,
            "recovery_plan_status": plan.get("status"),
            "new_sample_policy": plan.get("new_sample_policy"),
        },
        "top_rows": rows[:8],
        "rows": rows,
        "operator_note": (
            "This audit checks whether paper-only strategy decisions are visible in current gates. "
            "Pending A/B tests are not failures; they require future paper samples."
        ),
        "outputs": {
            "report": rel(REPORT_PATH),
            "experiment": rel(EXPERIMENT_PATH),
        },
    }


def render_report(record: dict[str, Any]) -> str:
    summary = record.get("summary") or {}
    lines = [
        "# Strategy Proposal Enforcement Audit",
        "",
        f"- generated_at: `{record.get('created_at')}`",
        f"- status: `{record.get('status')}`",
        f"- decisions: `{summary.get('decision_count')}`",
        f"- enforced: `{summary.get('enforced_count')}`",
        f"- pending_tests: `{summary.get('pending_test_count')}`",
        f"- blocking: `{summary.get('blocking_count')}`",
        f"- new_sample_policy: `{summary.get('new_sample_policy')}`",
        f"- live_orders_enabled: `{record.get('live_orders_enabled')}`",
        f"- private_api_used: `{record.get('private_api_used')}`",
        f"- config_mutated: `{record.get('config_mutated')}`",
        f"- ledger_mutated: `{record.get('ledger_mutated')}`",
        "",
        "| ID | Decision | Enforcement | Blocking | Evidence |",
        "|---|---|---|---:|---|",
    ]
    for row in record.get("top_rows") or []:
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        hits = ", ".join(evidence.get("current_block_hits") or evidence.get("matched_gate_names") or [])
        lines.append(
            f"| `{row.get('proposed_change_id')}` | `{row.get('decision')}` | "
            f"`{row.get('enforcement_status')}` | `{row.get('blocking')}` | {hits or '-'} |"
        )
    if not record.get("top_rows"):
        lines.append("| - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This audit does not apply strategy changes.",
            "- Pending A/B tests require future paper-only samples before promotion.",
            "- Live orders remain blocked.",
            "",
        ]
    )
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    plan = {
        "retired_entry_modes": [{"name": "bad_probe"}],
        "cooldown_entry_modes": [{"name": "soft_probe"}],
        "weak_intervals": [{"name": "1h"}],
        "status": "ok",
    }
    runner = {"no_entry_summary": {"top_blocked_candidates": [{"symbol": "ABCUSDT", "validation_recovery_match": "bad_probe"}]}}
    decision = {
        "proposed_change_id": "pc-test",
        "decision": "enforce_retirement_or_cooldown",
        "change_type": "retire_failed_sampling_paths",
        "impact": {"affected_names": ["bad_probe"]},
    }
    result = evaluate_decision(decision, plan, runner, {})
    assert result["enforcement_status"] == "enforced_and_blocking_current_candidates", result
    missing = evaluate_decision({**decision, "impact": {"affected_names": ["missing_probe"]}}, plan, runner, {})
    assert missing["blocking"] is True, missing
    capital = evaluate_decision(
        {
            "proposed_change_id": "pc-capital",
            "decision": "keep_conservative_sizing",
            "change_type": "monthly_target_recovery_posture",
        },
        plan,
        runner,
        {
            "policy": "minimum_quality_scout_only",
            "per_trade_notional_usd": 25.0,
            "max_new_positions_now": 1,
            "phase2_status": "not_passed",
        },
    )
    assert capital["enforcement_status"] == "enforced_by_capital_policy", capital
    with tempfile.TemporaryDirectory(prefix="strategy_proposal_enforcement_", dir="/private/tmp") as tmp_text:
        path = Path(tmp_text) / "record.json"
        write_json(path, {"status": "ok"})
        assert read_json(path, {}).get("status") == "ok"
    return {
        "status": "ok",
        "recovery_enforcement_verified": True,
        "blocking_gap_verified": True,
        "capital_policy_enforcement_verified": True,
        "uses_temporary_files_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    record = build_record()
    if not args.no_write:
        write_json(EXPERIMENT_PATH, record)
        write_text(REPORT_PATH, render_report(record))
    if args.compact_output:
        print(
            json.dumps(
                {
                    "status": record.get("status"),
                    "run_id": record.get("run_id"),
                    "summary": record.get("summary"),
                    "top_rows": record.get("top_rows")[:8],
                    "outputs": record.get("outputs"),
                    "live_orders_enabled": record.get("live_orders_enabled"),
                    "private_api_used": record.get("private_api_used"),
                    "ledger_mutated": record.get("ledger_mutated"),
                    "config_mutated": record.get("config_mutated"),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
