#!/usr/bin/env python3
"""Finite Stage-2 acceptance matrix; natural-time evidence is explicitly non-blocking."""
from __future__ import annotations
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
def read(p:str,d:Any=None)->Any:
    x=ROOT/p;return json.loads(x.read_text(encoding="utf-8")) if x.exists() else d
def check(cid:str,passed:bool,evidence:Any)->dict[str,Any]:return {"check_id":cid,"passed":bool(passed),"evidence":evidence}

def build()->dict[str,Any]:
    models=read("experiments/current-stage2-model-audit.json",{});policy=read("config/policy.json",{});daily=read("experiments/current-daily-manual-decision.json",{});forward=read("experiments/current-daily-forward-benchmark.json",{});registry=read("experiments/current-model-registry.json",{});ready=read("experiments/current-stage2-readiness.json",{});comparison=read("experiments/current-same-window-comparison.json",{});cycle=read("experiments/current-validation-cycle.json",{});tests=read("experiments/current-stage2-test-results.json",{})
    cats=set((daily.get("category_counts_all_scanned_markets") or {}).keys());required={"alpha_primary_recommendation","high_win_small_return_recommendation","high_probability_too_expensive","price_edge_probability_insufficient","market_high_probability_no_independent_model","insufficient_data_rules_liquidity_or_model"}
    h=policy.get("high_win_small_return_gate") or {};e=policy.get("entry_gate") or {};s=policy.get("early_stage_sizing") or {}
    checks=[
      check("two_prefrozen_chronological_final_oos",models.get("formal_market_type_count")==2 and all(x.get("governance_passed") and x.get("independent_final_oos_events",0)>=30 for x in models.get("models",[])),[x.get("model_version") for x in models.get("models",[])]),
      check("each_model_pass_fail_frozen",len(models.get("models",[]))==2 and all(x.get("conclusion") in {"pass","fail_frozen"} for x in models.get("models",[])),models.get("frozen_failed_models")),
      check("dual_gates_six_categories_sizing",e.get("min_net_edge_per_share")>=.045 and h.get("min_net_ev_per_share_exclusive")==0 and max(h.get("position_equity_pct",[99]))<=2 and s.get("ordinary_position_equity_pct")==[2.0,5.0] and required.issubset(cats),sorted(cats)),
      check("condition_level_forward_scoring",forward.get("independence_unit")=="condition_id" and forward.get("hourly_snapshots_do_not_increase_independent_sample_count") is True and "market_baseline" in forward and "approved_model_summaries" in forward,forward.get("resolved_condition_count")),
      check("registry_freeze_backcase_learning_coverage",registry.get("new_version_contract",{}).get("materially_different_model_required") is True and ready.get("backcase_learning_contract",{}).get("minimum_similar_failures_for_ordinary_change")==3 and isinstance(ready.get("coverage_queue"),list),{"models":len(registry.get("models",[])),"coverage":len(ready.get("coverage_queue",[]))}),
      check("binance_comparator_and_readiness",comparison.get("comparison_status") is not None and ready.get("long_term_metrics_are_stage2_blockers") is False,comparison.get("comparison_status")),
      check("automation_safety_regression_e2e",cycle.get("status")=="ok" and tests.get("status")=="pass" and policy.get("paper_only") is True and policy.get("live_orders_enabled") is False and policy.get("private_api_used") is False and policy.get("real_money_execution_authorized") is False,{"cycle":cycle.get("status"),"tests":tests}),
      check("finite_delivery",models.get("stage2_finite_research_complete") is True and models.get("third_market_type_researched") is False and ready.get("states",{}).get("strategy_edge_unproven") is True,models.get("current_action")),
    ]
    passed=all(x["passed"] for x in checks);eligible=models.get("stage3_forward_paper_eligible_models",[])
    return {"schema_version":"polymarket-stage2-acceptance-v1","created_at":datetime.now(timezone.utc).isoformat(),"status":"pass" if passed else "fail","checks":checks,"passed_checks":sum(x["passed"] for x in checks),"total_checks":len(checks),"stage2_complete":passed,"models_entering_stage3":eligible,"models_failed_and_frozen":models.get("frozen_failed_models",[]),"current_alpha_primary_recommendations":daily.get("alpha_primary_recommendation_count",0),"current_high_win_small_return_recommendations":daily.get("high_win_small_return_recommendation_count",0),"current_action":"NO_BET" if not eligible else daily.get("decision"),"long_term_metrics_pending":ready.get("natural_accumulation_metrics"),"natural_time_metrics_block_completion":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False,"real_money_execution_authorized":False}

def md(p:dict[str,Any])->str:
    lines=["# Polymarket Stage 2 Acceptance Matrix","",f"Conclusion: **{p['status'].upper()}** — current action `{p['current_action']}`.","","| Check | Result |","|---|---|"]+[f"| {x['check_id']} | {'PASS' if x['passed'] else 'FAIL'} |" for x in p["checks"]]
    lines += ["",f"- Stage 3 eligible models: {p['models_entering_stage3'] or 'none'}",f"- Failed and frozen: {', '.join(p['models_failed_and_frozen']) or 'none'}",f"- Current Alpha / high-win recommendations: {p['current_alpha_primary_recommendations']} / {p['current_high_win_small_return_recommendations']}","- 30/100 paper trades and three 30-day windows remain long-term evidence, not Stage-2 blockers.","- Real-money execution remains unauthorized.",""]
    return "\n".join(lines)
def main()->int:
    a=argparse.ArgumentParser();a.add_argument("--output",default=str(ROOT/"experiments/current-stage2-acceptance-audit.json"));a.add_argument("--report",default=str(ROOT/"reports/STAGE2_FINAL_ACCEPTANCE.md"));x=a.parse_args();p=build();Path(x.output).write_text(json.dumps(p,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");Path(x.report).write_text(md(p),encoding="utf-8");print(json.dumps(p,ensure_ascii=False,indent=2));return 0 if p["status"]=="pass" else 1
if __name__=="__main__":raise SystemExit(main())
