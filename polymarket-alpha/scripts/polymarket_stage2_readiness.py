#!/usr/bin/env python3
"""Build the non-blocking Stage-2 long-term readiness board."""
from __future__ import annotations

import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def read(path: str, default: Any = None) -> Any:
    p = ROOT / path
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def sha(path: str) -> str | None:
    p = ROOT / path
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def build() -> dict[str, Any]:
    models = read("experiments/current-stage2-model-audit.json", {})
    forward = read("experiments/current-daily-forward-benchmark.json", {})
    candidate = read("data/daily_candidate_observation_ledger.json", {"events": []})
    ledger = read("data/paper_ledger.json", {})
    audit = read("experiments/current-goal-audit.json", {})
    comparison = read("experiments/current-same-window-comparison.json", {})
    baseline = read("experiments/20260711-historical-market-baseline.json", {})
    daily = read("experiments/current-daily-priority-cycle.json", {})
    scans = [e for e in candidate.get("events", []) if e.get("event_type") == "scan_observation"]
    resolved = [e for e in candidate.get("events", []) if e.get("event_type") == "resolution_observation"]
    closed = ledger.get("closed_positions", [])
    approved = models.get("stage3_forward_paper_eligible_models", [])
    model_rows = [{"model_version": m["model_version"], "status": m["conclusion"], "independent_final_oos_events": m["independent_final_oos_events"], "brier_improvement": m["final_holdout"]["brier_improvement"]} for m in models.get("models", [])]
    natural = {
        "settled_markets_scanned": baseline.get("settled_markets_scanned", 0), "settled_markets_target": 500,
        "approved_model_type_count": len(approved), "model_oos": model_rows,
        "forward_observation_count": len(scans), "forward_independent_observed_conditions": len({e.get("condition_id") for e in scans if e.get("condition_id")}),
        "forward_independent_resolved_conditions": len({e.get("condition_id") for e in resolved if e.get("condition_id")}),
        "formal_paper_closed_count": len(closed), "paper_targets": [30, 100],
        "enabled_domain_independent_samples": audit.get("domain_sample_counts", {}), "per_domain_target": 30,
        "net_roi_pct": audit.get("net_roi_pct"), "mean_clv_per_share": audit.get("mean_clv_per_share"),
        "max_drawdown_pct": ledger.get("max_drawdown_pct", 0.0),
        "forward_model_vs_market": forward.get("approved_model_summaries", []),
        "average_trade_net_ev_lower_95": audit.get("average_trade_net_ev_lower_95"),
        "polymarket_binance_valid_30d_windows": comparison.get("valid_window_count", 0),
        "consecutive_30d_windows_beating_binance": comparison.get("best_consecutive_windows_beating_binance", 0),
    }
    coverage = daily.get("model_coverage_queue", [])
    return {
        "schema_version": "polymarket-stage2-readiness-v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "engineering_ready_strategy_unproven",
        "states": {"engineering_ready": True, "model_research_passed": bool(approved), "forward_evidence_pending": True, "strategy_edge_unproven": True, "manual_review_required": True},
        "natural_accumulation_metrics": natural,
        "coverage_queue": coverage,
        "coverage_queue_contract": {"formal_stage2_research_expansion_allowed": False, "records_market_count_execution_viability_model_coverage_gaps_and_next_condition": True},
        "backcase_learning_contract": {"minimum_similar_failures_for_ordinary_change": 3, "single_explicit_data_or_rule_error_may_propose_change": True, "stable_change_id_required": True, "old_version_retained": True, "replayable_and_rollback_capable": True, "forward_degradation_rolls_back_paper_overlay": True, "real_money_parameters_mutable": False},
        "artifact_integrity_manifest": {p: sha(p) for p in ("data/daily_candidate_observation_ledger.json", "data/paper_ledger.json", "experiments/current-model-registry.json", "experiments/current-stage2-model-audit.json")},
        "current_maximum_allowed_action": "paper_only_daily_report_and_forward_observation",
        "stage3_entry_allowed": bool(approved), "stage3_blockers": [] if approved else ["no_stage2_model_passed_frozen_oos"],
        "long_term_metrics_are_stage2_blockers": False,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False, "real_money_execution_authorized": False,
    }


def md(p: dict[str, Any]) -> str:
    m=p["natural_accumulation_metrics"]; s=p["states"]
    lines=["# Polymarket Long-Term Readiness Board","",f"- Current maximum action: `{p['current_maximum_allowed_action']}`",f"- Stage 3 entry allowed: `{str(p['stage3_entry_allowed']).lower()}`","","| State | Value |","|---|---|"]
    lines += [f"| {k} | `{str(v).lower()}` |" for k,v in s.items()]
    lines += ["","| Evidence | Current / target |","|---|---|",f"| Settled markets scanned | {m['settled_markets_scanned']} / {m['settled_markets_target']} |",f"| Approved model types | {m['approved_model_type_count']} |",f"| Forward observations / independent resolved | {m['forward_observation_count']} / {m['forward_independent_resolved_conditions']} |",f"| Closed paper trades | {m['formal_paper_closed_count']} / 30 / 100 |",f"| Net ROI / mean CLV / max drawdown | {m['net_roi_pct']} / {m['mean_clv_per_share']} / {m['max_drawdown_pct']}% |",f"| Valid 30-day comparison windows | {m['polymarket_binance_valid_30d_windows']} |","","Natural-time evidence remains pending and does not block Stage-2 engineering completion. Engineering readiness is not evidence of strategy edge.",""]
    return "\n".join(lines)


def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument("--output",default=str(ROOT/"experiments/current-stage2-readiness.json"));ap.add_argument("--report",default=str(ROOT/"reports/STAGE2_READINESS_BOARD.md"));a=ap.parse_args();p=build();Path(a.output).write_text(json.dumps(p,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");Path(a.report).write_text(md(p),encoding="utf-8");print(json.dumps(p,ensure_ascii=False,indent=2));return 0


if __name__=="__main__":raise SystemExit(main())
