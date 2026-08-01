#!/usr/bin/env python3
"""Append every recommendation, wait and NO BET decision to a paper-only ledger."""
from __future__ import annotations
import argparse,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
def now():return datetime.now(timezone.utc).isoformat()
def read(p:Path,d=None):return json.loads(p.read_text(encoding="utf-8")) if p.exists() else d
def stable(*v):return "pm-manual-decision-"+hashlib.sha256("|".join(map(str,v)).encode()).hexdigest()[:20]
def new():return {"schema_version":"polymarket-manual-decision-ledger-v1","created_at":now(),"updated_at":now(),"append_only":True,"independence_unit":"condition_id","hourly_snapshots_are_not_independent_events":True,"events":[],"paper_only":True,"live_orders_enabled":False,"private_api_used":False,"real_money_execution_authorized":False}
def build(report:dict[str,Any],ledger:dict[str,Any])->dict[str,Any]:
    if not (ledger.get("append_only") is True and ledger.get("paper_only") is True and ledger.get("live_orders_enabled") is False and ledger.get("private_api_used") is False):raise ValueError("unsafe decision ledger")
    existing={e["event_id"] for e in ledger["events"]};added=0
    research=report.get("event_research_artifact") or "no-event-research"
    for x in report.get("decision_items",[]):
        eid=stable(report.get("source_daily_cycle_id"),research,x.get("condition_id"),x.get("recommendation_level"))
        if eid in existing:continue
        ledger["events"].append({"event_id":eid,"event_type":"manual_decision_observation","observed_at":report.get("created_at"),"source_daily_cycle_id":report.get("source_daily_cycle_id"),"condition_id":x.get("condition_id"),"market_id":x.get("market_id"),"market":x.get("market"),"direction":x.get("independent_model_direction"),"bet_direction":x.get("bet_direction"),"recommendation_level":x.get("recommendation_level"),"recommendation_type":x.get("recommendation_type"),"final_action":x.get("final_action"),"research_probability":x.get("model_estimated_probability"),"confidence_interval":x.get("confidence_interval"),"probability_status":x.get("probability_status"),"executable_price":x.get("executable_price"),"maximum_acceptable_price":x.get("maximum_acceptable_price"),"net_ev_per_share":x.get("net_edge_per_share"),"spread":x.get("spread"),"price_impact":x.get("price_impact"),"fee_per_share":x.get("entry_fee_per_share"),"slippage_per_share":x.get("extra_slippage_per_share"),"suggested_paper_amount_usd":x.get("suggested_paper_amount_usd"),"latest_entry_time":x.get("latest_entry_time"),"sources":x.get("research_sources"),"settlement_rules":x.get("settlement_rules"),"failure_paths":x.get("failure_paths"),"exit_conditions":x.get("exit_conditions"),"conditional_trigger":x.get("conditional_trigger"),"entered_paper":False,"counts_as_independent_event":False,"resolution_status":"pending"});existing.add(eid);added+=1
    ledger["updated_at"]=now();return {"ledger":ledger,"added":added}
def main()->int:
    a=argparse.ArgumentParser();a.add_argument("--report-json",default=str(ROOT/"experiments/current-daily-manual-decision.json"));a.add_argument("--ledger",default=str(ROOT/"data/manual_decision_observation_ledger.json"));a.add_argument("--output",default=str(ROOT/"experiments/current-manual-decision-ledger-cycle.json"));x=a.parse_args();lp=Path(x.ledger);r=build(read(Path(x.report_json),{}),read(lp,new()));lp.write_text(json.dumps(r["ledger"],ensure_ascii=False,indent=2)+"\n",encoding="utf-8");out={"schema_version":"polymarket-manual-decision-ledger-cycle-v1","created_at":now(),"status":"ok","events_added":r["added"],"total_observations":len(r["ledger"]["events"]),"independent_conditions":len({e.get("condition_id") for e in r["ledger"]["events"] if e.get("condition_id")}),"paper_only":True,"live_orders_enabled":False,"private_api_used":False,"real_money_execution_authorized":False};Path(x.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps(out,ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
