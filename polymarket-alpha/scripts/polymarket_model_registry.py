#!/usr/bin/env python3
"""Build the authoritative paper-model eligibility registry from evidence."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


ROOT=Path(__file__).resolve().parents[1]


def load(name:str,path:Path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(module);return module


core=load("model_registry_core",ROOT/"scripts/polymarket_alpha.py")


SOURCES=(
    ("crypto","experiments/current-crypto-barrier-walk-forward.json","model_family"),
    ("weather","experiments/current-weather-walk-forward.json","model_version"),
    ("weather","experiments/current-weather-nyc-walk-forward.json","model_version"),
    ("sports","experiments/current-football-walk-forward.json","model_version"),
    ("sports","experiments/current-baseball-elo-walk-forward.json","model_version"),
    ("sports","experiments/current-basketball-nba-elo-v1.json","model_version"),
    ("sports","experiments/current-lolesports-gpr-research.json","model_version"),
    ("social_count","experiments/current-social-count-walk-forward.json","model_version"),
    ("technology","experiments/current-stock-weekly-walk-forward.json","model_version"),
)


def build(approvals_path:Path)->dict[str,Any]:
    approvals=core.read_json(approvals_path)
    if approvals.get("paper_only") is not True or approvals.get("live_orders_enabled") is not False or approvals.get("private_api_used") is not False:raise ValueError("unsafe model approvals")
    approved=set(str(value) for value in approvals.get("approved_model_versions",[]));models=[]
    for domain,relative,key in SOURCES:
        path=ROOT/relative
        if not path.exists():
            models.append({"domain":domain,"evidence_path":relative,"model_version":None,"evidence_status":"missing","paper_estimates_allowed":False,"block_reasons":["evidence_missing"]});continue
        evidence=core.read_json(path);version=evidence.get(key);candidate=evidence.get("research_promotion_candidate")
        if candidate is None:candidate=evidence.get("sustained_improvement")
        evidence_allows=evidence.get("paper_estimates_allowed") is True;manual_approved=bool(version and str(version) in approved);reasons=[]
        if candidate is not True:reasons.append("oos_research_promotion_not_proven")
        if evidence_allows is not True:reasons.append("evidence_does_not_allow_paper_estimates")
        if not manual_approved:reasons.append("manual_model_approval_missing")
        if evidence.get("model_outputs_are_true_probabilities") is not True:reasons.append("calibrated_probability_semantics_not_proven")
        metrics=evidence.get("metrics") or []
        final=next((row for row in metrics if row.get("segment")=="final_holdout" and row.get("cutoff")=="T-24h"),None)
        split=evidence.get("chronological_split") or evidence.get("split")
        frozen=evidence.get("model_status") in {"frozen_failed","frozen","retired"}
        state="retired" if evidence.get("model_status")=="retired" else "frozen" if frozen else "pass" if not reasons else "fail"
        models.append({"domain":domain,"market_family":evidence.get("league") or domain,"evidence_path":relative,"model_version":str(version) if version else None,"evidence_status":"loaded","data_feature_version":evidence.get("data_version") or evidence.get("protocol_sha256"),"chronological_split":split,"final_holdout_first_inspected_at":evidence.get("final_holdout_first_inspected_at"),"final_holdout_reuse_for_model_selection_allowed":evidence.get("final_holdout_reuse_for_model_selection_allowed"),"independent_final_oos_events":final.get("games") if final else None,"final_oos_metrics":final,"research_promotion_candidate":candidate is True,"evidence_paper_estimates_allowed":evidence_allows,"manual_approved":manual_approved,"paper_estimates_allowed":not reasons,"status":state,"promotion_status":evidence.get("promotion_status"),"block_reasons":reasons})
    family_controls=[{"control_id":"pm-control-weather-day1-v1-retired","model_family":"gfs_ecmwf_fixed_day1_normal_error_v1","domains":["weather"],"same_class_oos_failures":4,"evidence_paths":["experiments/current-weather-walk-forward.json","experiments/current-weather-nyc-walk-forward.json"],"action":"retire_from_paper_estimates","automatic_change_basis":"at_least_3_same_class_failures","paper_only":True}]
    eligible=sorted(row["model_version"] for row in models if row.get("paper_estimates_allowed") and row.get("model_version"))
    return {"schema_version":"polymarket-model-registry-v2","created_at":core.now_iso(),"models":models,"eligible_paper_model_versions":eligible,"blocked_or_unapproved_model_versions":sorted(row["model_version"] for row in models if not row.get("paper_estimates_allowed") and row.get("model_version")),"paper_model_family_controls":family_controls,"new_version_contract":{"materially_different_model_required":True,"new_prefrozen_protocol_required":True,"fresh_oos_or_forward_samples_required":True},"automatic_downgrade_triggers":["recent_window_materially_worse_than_market","calibration_drift","data_rule_or_event_structure_change","at_least_three_repeatable_backcase_defects"],"automatic_upgrade_to_real_money_allowed":False,"all_models_blocked":not eligible,"paper_only":True,"live_orders_enabled":False,"private_api_used":False,"real_money_execution_authorized":False}


def markdown(payload:dict[str,Any])->str:
    lines=["# Polymarket Model Registry","",f"- Eligible paper model versions: {len(payload['eligible_paper_model_versions'])}",f"- All models blocked: `{str(payload['all_models_blocked']).lower()}`","","| Domain | Model | OOS candidate | Manual approval | Paper estimates | Block reasons |","|---|---|---:|---:|---:|---|"]
    for row in payload["models"]:lines.append(f"| {row['domain']} | `{row.get('model_version')}` | {row.get('research_promotion_candidate',False)} | {row.get('manual_approved',False)} | {row.get('paper_estimates_allowed',False)} | {', '.join(row.get('block_reasons',[]))} |")
    lines.extend(["","The registry is a paper-entry safety contract. It never authorizes live orders or real-money parameters.",""])
    return "\n".join(lines)


def self_test()->dict[str,Any]:
    payload=build(ROOT/"config/model_approvals.json");assert payload["all_models_blocked"] is True and payload["eligible_paper_model_versions"]==[];assert payload["paper_model_family_controls"][0]["same_class_oos_failures"]>=3
    return {"status":"pass","tests":["unapproved_models_blocked","failed_oos_models_blocked","three_failure_family_retirement","paper_only_flags"]}


def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--approvals",default=str(ROOT/"config/model_approvals.json"));parser.add_argument("--output",default=str(ROOT/"experiments/current-model-registry.json"));parser.add_argument("--report",default=str(ROOT/"reports/CURRENT_MODEL_REGISTRY.md"));parser.add_argument("--self-test",action="store_true");args=parser.parse_args()
    if args.self_test:payload=self_test()
    else:
        payload=build(Path(args.approvals));core.write_json(args.output,payload);target=Path(args.report);target.parent.mkdir(parents=True,exist_ok=True);temp=target.with_suffix(target.suffix+".tmp");temp.write_text(markdown(payload),encoding="utf-8");temp.replace(target)
    print(json.dumps(payload,ensure_ascii=False,indent=2));return 0


if __name__=="__main__":raise SystemExit(main())
