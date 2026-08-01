#!/usr/bin/env python3
"""Rank strategy backlog proposals for paper-only validation.

Read-only. This script does not mutate strategy config, the paper ledger, or
automation state. It turns proposed changes into an operator decision board so
paper validation can focus on the changes most likely to improve Phase 2.
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
LEDGER_PATH = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
BACKLOG_PATH = ACTIVE_ROOT / "experiments" / "strategy-iteration-backlog.json"
REPORT_PATH = ACTIVE_ROOT / "reports" / "STRATEGY_PROPOSAL_DECISION_BOARD.md"
EXPERIMENT_PATH = ACTIVE_ROOT / "experiments" / "strategy-proposal-decision-board.json"
LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))


PROTECTIVE_TYPES = {
    "retire_failed_sampling_paths",
    "interval_quality_filter",
    "tighten_exploratory_probe_gate",
    "tighten_momentum_confirmation",
    "tighten_profit_protection",
}


def now_local() -> dt.datetime:
    return dt.datetime.now(LOCAL_TZ).replace(microsecond=0)


def read_json(path: Path, default: Any = None) -> Any:
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


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def rel(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def latest(pattern: str, directory: Path = ACTIVE_ROOT / "experiments") -> Path | None:
    files = sorted(directory.glob(pattern))
    return files[-1] if files else None


def affected_impact(items: list[dict[str, Any]]) -> dict[str, Any]:
    count = 0
    net_loss_usd = 0.0
    worst_win_rate: float | None = None
    target_gap_usd = 0.0
    names: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("category") or item.get("paper_entry_mode") or item.get("strategy_family") or "")
        if name:
            names.append(name)
        item_count = as_int(item.get("count"), as_int(item.get("closed_count"), as_int(item.get("trade_count"), 1)))
        count += max(0, item_count)
        for key in ("net_pnl_usd", "realized_pnl_usd"):
            pnl = as_float(item.get(key), 0.0)
            if pnl < 0:
                net_loss_usd += abs(pnl)
        target_gap_usd += max(0.0, as_float(item.get("target_gap_usd"), 0.0))
        if item.get("win_rate_pct") is not None:
            rate = as_float(item.get("win_rate_pct"), 0.0)
            worst_win_rate = rate if worst_win_rate is None else min(worst_win_rate, rate)
    return {
        "affected_count": count,
        "estimated_loss_at_risk_usd": round(net_loss_usd, 6),
        "target_gap_usd": round(target_gap_usd, 6),
        "worst_win_rate_pct": round(worst_win_rate, 6) if worst_win_rate is not None else None,
        "affected_names": names[:8],
    }


def decision_for(change_type: str, validation_plan: dict[str, Any], impact: dict[str, Any]) -> tuple[str, str]:
    if change_type == "monthly_target_recovery_posture":
        return "keep_conservative_sizing", "Do not increase notional until Phase 2 improves."
    if change_type == "retire_failed_sampling_paths":
        return "enforce_retirement_or_cooldown", "Keep failed paths out of new samples except explicit research retests."
    if change_type == "interval_quality_filter":
        return "enforce_interval_filter", "Allow only minimal quality scout exceptions on weak intervals."
    if change_type == "tighten_profit_protection":
        return "paper_exit_rule_ab_test", "Evaluate break-even/trailing floor on future paper trades after +2% float gain."
    if change_type in {"tighten_momentum_confirmation", "tighten_exploratory_probe_gate"}:
        return "paper_entry_filter_ab_test", "Require stricter current-signal, volume and microstructure confirmation before new samples."
    if str(validation_plan.get("forward_sample_allowed") or "").lower().startswith("paper"):
        return "paper_forward_validation", "Forward-test only at minimum paper size."
    return "manual_review_only", "Keep as backlog until a concrete paper validation path is defined."


def priority_score(change: dict[str, Any], impact: dict[str, Any]) -> float:
    change_type = str(change.get("change_type") or "")
    score = 0.0
    score += min(30.0, impact.get("estimated_loss_at_risk_usd", 0.0) * 1.2)
    score += min(24.0, impact.get("affected_count", 0) * 2.0)
    score += min(18.0, as_int(change.get("seen_count"), 0) * 1.2)
    if change_type in PROTECTIVE_TYPES:
        score += 16.0
    if change_type in {"retire_failed_sampling_paths", "tighten_momentum_confirmation", "tighten_profit_protection"}:
        score += 10.0
    if impact.get("worst_win_rate_pct") is not None and impact["worst_win_rate_pct"] < 45:
        score += 8.0
    if change_type == "monthly_target_recovery_posture":
        score = max(score, 42.0)
    return round(min(100.0, score), 4)


def build_record() -> dict[str, Any]:
    created = now_local()
    backlog = read_json(BACKLOG_PATH, {}) or {}
    ledger = read_json(LEDGER_PATH, {}) or {}
    attribution_path = latest("*paper-trade-attribution-report.json")
    phase_path = latest("*phase-goal-readiness-audit.json")
    items = backlog.get("items") if isinstance(backlog.get("items"), dict) else {}
    decisions: list[dict[str, Any]] = []
    for change_id, change in sorted(items.items()):
        if not isinstance(change, dict) or not change.get("currently_present", True):
            continue
        affected = change.get("affected_items") if isinstance(change.get("affected_items"), list) else []
        validation_plan = change.get("validation_plan") if isinstance(change.get("validation_plan"), dict) else {}
        impact = affected_impact(affected)
        decision, rationale = decision_for(str(change.get("change_type") or ""), validation_plan, impact)
        decisions.append(
            {
                "proposed_change_id": change_id,
                "source": change.get("source"),
                "change_type": change.get("change_type"),
                "title": change.get("title"),
                "operator_status": change.get("operator_status"),
                "seen_count": change.get("seen_count"),
                "priority_score": priority_score(change, impact),
                "decision": decision,
                "decision_rationale": rationale,
                "impact": impact,
                "minimum_new_closed_samples": validation_plan.get("minimum_new_closed_samples"),
                "success_criteria": validation_plan.get("success_criteria"),
                "forward_sample_allowed": validation_plan.get("forward_sample_allowed"),
                "risk_controls": change.get("risk_controls") or [],
                "paper_only": True,
                "config_mutation_allowed": False,
                "ledger_mutation_allowed": False,
                "live_orders_enabled": False,
                "private_api_used": False,
            }
        )
    decisions.sort(key=lambda item: item["priority_score"], reverse=True)
    top = decisions[:5]
    phase2_state = {
        "closed_trades": len(ledger.get("closed_trades") or []),
        "equity_usd": ledger.get("equity_usd"),
        "net_return_pct": ledger.get("net_return_pct"),
        "max_drawdown_pct": ledger.get("max_drawdown_pct"),
        "live_orders_enabled": ledger.get("live_orders_enabled") is True,
        "private_api_used": ledger.get("private_api_used") is True,
    }
    return {
        "run_id": f"{created.strftime('%Y%m%d-%H%M%S')}-strategy-proposal-decision-board",
        "created_at": created.isoformat(),
        "scope": "paper_only_strategy_proposal_decision",
        "status": "ok" if decisions else "missing_backlog",
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "ledger_mutated": False,
        "config_mutated": False,
        "source_backlog": rel(BACKLOG_PATH),
        "source_attribution": rel(attribution_path),
        "source_phase_readiness": rel(phase_path),
        "phase2_state": phase2_state,
        "summary": {
            "proposal_count": len(decisions),
            "top_priority_id": top[0]["proposed_change_id"] if top else None,
            "top_priority_score": top[0]["priority_score"] if top else None,
            "paper_ab_test_candidate_count": sum(1 for item in decisions if "ab_test" in str(item.get("decision"))),
            "protective_gate_candidate_count": sum(1 for item in decisions if "filter" in str(item.get("decision")) or "retire" in str(item.get("decision"))),
        },
        "top_decisions": top,
        "decisions": decisions,
        "operator_note": (
            "This board ranks pending proposals for paper-only validation. It does not approve, apply, "
            "or mutate strategy config, and it never authorizes live trading."
        ),
        "outputs": {
            "report": rel(REPORT_PATH),
            "experiment": rel(EXPERIMENT_PATH),
        },
    }


def render_report(record: dict[str, Any]) -> str:
    summary = record.get("summary") or {}
    lines = [
        "# Strategy Proposal Decision Board",
        "",
        f"- generated_at: `{record.get('created_at')}`",
        "- scope: `paper_only_strategy_proposal_decision`",
        f"- status: `{record.get('status')}`",
        f"- proposal_count: `{summary.get('proposal_count')}`",
        f"- top_priority_id: `{summary.get('top_priority_id')}`",
        f"- live_orders_enabled: `{record.get('live_orders_enabled')}`",
        f"- private_api_used: `{record.get('private_api_used')}`",
        f"- config_mutated: `{record.get('config_mutated')}`",
        f"- ledger_mutated: `{record.get('ledger_mutated')}`",
        "",
        "## Top Decisions",
        "",
        "| Priority | ID | Decision | Impact | Samples |",
        "|---:|---|---|---:|---:|",
    ]
    for item in record.get("top_decisions") or []:
        impact = item.get("impact") or {}
        lines.append(
            f"| `{item.get('priority_score')}` | `{item.get('proposed_change_id')}` | `{item.get('decision')}` | "
            f"`${impact.get('estimated_loss_at_risk_usd')}` | `{item.get('minimum_new_closed_samples') if item.get('minimum_new_closed_samples') is not None else '-'}` |"
        )
    if not record.get("top_decisions"):
        lines.append("| - | - | - | - | - |")
    lines.extend(["", "## Validation Protocol", ""])
    for item in record.get("top_decisions") or []:
        lines.append(f"### {item.get('proposed_change_id')}")
        lines.append(f"- title: {item.get('title')}")
        lines.append(f"- decision: `{item.get('decision')}`")
        lines.append(f"- rationale: {item.get('decision_rationale')}")
        lines.append(f"- forward_sample_allowed: `{item.get('forward_sample_allowed')}`")
        lines.append(f"- success_criteria: {item.get('success_criteria')}")
        lines.append(f"- risk_controls: `{', '.join(item.get('risk_controls') or [])}`")
        lines.append("")
    lines.extend(
        [
            "## Guardrails",
            "",
            "- Do not mutate strategy config from this board alone.",
            "- Do not increase notional until Phase 2 evidence improves.",
            "- Keep all validation paper-only; live trading still requires separate Phase 2/3 proof and explicit human approval.",
            "",
        ]
    )
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    fixture = {
        "proposed_change_id": "pc-test-tighten",
        "change_type": "tighten_profit_protection",
        "seen_count": 5,
        "affected_items": [{"count": 4, "net_pnl_usd": -8.0, "win_rate_pct": 25}],
        "validation_plan": {"minimum_new_closed_samples": 3, "forward_sample_allowed": "paper_only"},
    }
    impact = affected_impact(fixture["affected_items"])
    decision, _ = decision_for(fixture["change_type"], fixture["validation_plan"], impact)
    score = priority_score(fixture, impact)
    assert impact["affected_count"] == 4, impact
    assert impact["estimated_loss_at_risk_usd"] == 8.0, impact
    assert decision == "paper_exit_rule_ab_test", decision
    assert score > 30.0, score
    with tempfile.TemporaryDirectory(prefix="strategy_proposal_decision_", dir="/private/tmp") as tmp_text:
        tmp = Path(tmp_text)
        out = tmp / "record.json"
        write_json(out, {"status": "ok", "live_orders_enabled": False})
        loaded = read_json(out, {})
        assert loaded.get("live_orders_enabled") is False, loaded
    return {
        "status": "ok",
        "impact_scoring_verified": True,
        "decision_mapping_verified": True,
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
                    "top_decisions": record.get("top_decisions")[:5],
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
