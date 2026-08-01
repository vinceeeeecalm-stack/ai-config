#!/usr/bin/env python3
"""Forward-only shadow forecasts for the frozen, failed X post-count V1."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


ROOT=Path(__file__).resolve().parents[1]
MODEL_VERSION="pm-social-count-trailing-nb-v1-research-forward-v2"
MODEL_FROZEN_AT="2026-07-11T14:30:00+00:00"
CAPTURE_WINDOW_MINUTES=60
DEFAULT_LEDGER=ROOT/"data/social_count_research_forecast_ledger.json"


def load(name:str,path:Path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(module);return module


core=load("social_shadow_core",ROOT/"scripts/polymarket_alpha.py")
public=load("social_shadow_public",ROOT/"scripts/polymarket_public_data.py")
lab=load("social_shadow_lab",ROOT/"scripts/polymarket_social_count_lab.py")


def new_ledger()->dict[str,Any]:
    return {"schema_version":"social-count-research-forecast-ledger-v1","created_at":core.now_iso(),"updated_at":core.now_iso(),"open_forecasts":[],"resolved_forecasts":[],"events":[],"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False}


def load_ledger(path:Path)->dict[str,Any]:
    ledger=core.read_json(path) if path.exists() else new_ledger()
    if ledger.get("paper_estimates_emitted") is not False or ledger.get("main_paper_ledger_mutated") is not False or ledger.get("live_orders_enabled") is not False or ledger.get("private_api_used") is not False:raise ValueError("unsafe social-count shadow ledger")
    return ledger


def file_sha(path:Path)->str|None:return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def capture_state(now:datetime,start:datetime)->str:
    window_start=start-timedelta(minutes=CAPTURE_WINDOW_MINUTES)
    if now<window_start:return "fixed_cutoff_not_reached"
    if now>=start:return "forecast_window_missed"
    return "eligible"


def live_book(token:str)->tuple[dict[str,float]|None,dict[str,Any]]:
    url=f"{public.CLOB_BASE}/book?{urlencode({'token_id':token})}";payload=public.get_json(url,timeout=20,retries=2)
    bids=[float(row["price"]) for row in payload.get("bids") or [] if row.get("price") is not None];asks=[float(row["price"]) for row in payload.get("asks") or [] if row.get("price") is not None]
    if not bids or not asks:return None,{"url":url,"status":"failed","error":"two_sided_book_missing"}
    bid=max(bids);ask=min(asks)
    if not 0<=bid<=ask<=1:return None,{"url":url,"status":"failed","error":"invalid_book_order"}
    return {"best_bid":bid,"best_ask":ask,"mid":(bid+ask)/2,"spread":ask-bid},{"url":url,"status":"ok","best_bid":bid,"best_ask":ask,"spread":ask-bid}


def winner_index(event:dict[str,Any])->int|None:
    winners=[]
    for index,market in enumerate(event.get("markets") or []):
        prices=market.get("outcomePrices")
        try:prices=json.loads(prices) if isinstance(prices,str) else prices
        except Exception:prices=[]
        if prices and float(prices[0])>=.99:winners.append(index)
    return winners[0] if len(winners)==1 else None


def settle(ledger:dict[str,Any])->dict[str,Any]:
    settled=[];requests=[];details={}
    for event_id in sorted({str(row["event_id"]) for row in ledger.get("open_forecasts",[])}):
        url=f"{lab.GAMMA}/events/{event_id}";details[event_id]=lab.get_json(url);requests.append({"url":url,"status":"ok"})
    for row in list(ledger.get("open_forecasts",[])):
        winner=winner_index(details.get(str(row["event_id"])) or {})
        if winner is None:continue
        closed={**row,"status":"resolved","resolved_at":core.now_iso(),"actual_yes":1 if int(row["market_index"])==winner else 0,"winning_market_index":winner}
        ledger["open_forecasts"].remove(row);ledger["resolved_forecasts"].append(closed);settled.append(row["forecast_id"])
    return {"settled":settled,"requests":requests}


def audit(ledger:dict[str,Any])->dict[str,Any]:
    groups={}
    for row in ledger.get("resolved_forecasts",[]):
        if row.get("actual_yes") in {0,1}:groups.setdefault(row["event_group"],[]).append(row)
    scored=[]
    for group,items in sorted(groups.items()):
        winners=[row for row in items if row["actual_yes"]==1]
        if len(winners)!=1:continue
        model=statistics.fmean((float(row["model_score"])-row["actual_yes"])**2 for row in items);market=statistics.fmean((float(row["market_probability_at_forecast"])-row["actual_yes"])**2 for row in items);winner=winners[0]
        scored.append({"event_group":group,"contracts":len(items),"model_brier":model,"market_brier":market,"market_minus_model_brier":market-model,"market_minus_model_log_loss":-math.log(max(float(winner["market_probability_at_forecast"]),1e-12))+math.log(max(float(winner["model_score"]),1e-12))})
    def calc(field:str):
        values=[row[field] for row in scored]
        if not values:return None,None
        mean=statistics.fmean(values);return mean,mean-1.96*statistics.stdev(values)/math.sqrt(len(values)) if len(values)>=2 else None
    brier,brier_lower=calc("market_minus_model_brier");log,log_lower=calc("market_minus_model_log_loss");promising=len(scored)>=30 and (brier_lower or -1)>0 and (log_lower or -1)>0
    return {"schema_version":"social-count-shadow-audit-v1","resolved_event_groups":len(scored),"group_rows":scored,"mean_market_minus_model_brier":brier,"paired_brier_95pct_lower":brier_lower,"mean_market_minus_model_log_loss":log,"paired_log_loss_95pct_lower":log_lower,"research_evidence_status":"promising_requires_manual_review" if promising else "insufficient_or_nonpositive_fresh_oos_evidence","paper_promotion_automatic":False}


def collect(ledger_path:Path,output:Path)->dict[str,Any]:
    protected=[ROOT/"data/paper_ledger.json",ROOT/"experiments/current-crypto-barrier-estimates.json"];before={str(path.relative_to(ROOT)):file_sha(path) for path in protected};ledger=load_ledger(ledger_path);settlement=settle(ledger)
    walk=core.read_json(ROOT/"experiments/current-social-count-walk-forward.json")
    if walk.get("research_promotion_candidate") is not False or walk.get("paper_estimates_allowed") is not False:raise ValueError("social-count frozen-failure contract mismatch")
    url=f"{lab.XTRACKER}/trackings?activeOnly=true";payload=lab.get_json(url);trackings=payload.get("data") or [];hourly=core.read_json(ROOT/"cache/social_count_history/hourly-history.json");now=datetime.now(timezone.utc);freeze=core.parse_iso(MODEL_FROZEN_AT);opened=[];excluded=[];requests=[{"url":url,"status":"ok","rows":len(trackings)}]
    existing={(row["event_group"],row["model_version"]) for row in ledger["open_forecasts"]+ledger["resolved_forecasts"]}
    for tracking in trackings:
        user=tracking.get("user") or {};tracking_id=str(tracking.get("id"));slug=lab.tracking_slug(tracking);start=core.parse_iso(tracking.get("startDate"));end=core.parse_iso(tracking.get("endDate"));group=f"XCOUNT:{tracking_id}"
        if str(user.get("platform"))!="X":excluded.append({"tracking_id":tracking_id,"reason":"truth_social_out_of_model_scope"});continue
        if not slug or not start or not end:excluded.append({"tracking_id":tracking_id,"reason":"tracking_contract_incomplete"});continue
        window_start=start-timedelta(minutes=CAPTURE_WINDOW_MINUTES);timing={"cutoff_at":start.isoformat(),"capture_window_start_at":window_start.isoformat(),"capture_window_end_at":start.isoformat()}
        if start<freeze:excluded.append({"tracking_id":tracking_id,"reason":"cutoff_before_model_freeze",**timing});continue
        if (group,MODEL_VERSION) in existing:excluded.append({"tracking_id":tracking_id,"reason":"forecast_already_recorded",**timing});continue
        timing_state=capture_state(now,start)
        if timing_state!="eligible":excluded.append({"tracking_id":tracking_id,"reason":timing_state,**timing});continue
        event_url=f"{lab.GAMMA}/events?{urlencode({'slug':slug})}";events=lab.get_json(event_url);requests.append({"url":event_url,"status":"ok","rows":len(events)});event=next((row for row in events if row.get("slug")==slug),None)
        if not event:excluded.append({"tracking_id":tracking_id,"reason":"gamma_event_missing"});continue
        contract=lab.event_contract(event);buckets=[row.get("bucket") for row in contract["markets"]]
        if contract["kind"]!="count_ladder" or any(bucket is None for bucket in buckets):excluded.append({"tracking_id":tracking_id,"reason":"count_ladder_invalid"});continue
        user_id=str(user.get("id") or tracking.get("userId"));history=[point for point in (hourly.get(user_id) or {}).get("hours",[]) if start-timedelta(days=60)<=core.parse_iso(point["timestamp"])<start]
        if len(history)<30*24:excluded.append({"tracking_id":tracking_id,"reason":"prior_history_under_30d_observed_hours"});continue
        counts=[float(point["count"]) for point in history];duration=(end-start).total_seconds()/3600;mean_h=statistics.fmean(counts);var_h=statistics.variance(counts);model=lab.bucket_probabilities([bucket for bucket in buckets if bucket is not None],mean_h*duration,max(var_h,mean_h)*duration);books=[]
        for market in event.get("markets") or []:
            token=next((value for outcome,value in public.token_map(market).items() if outcome.lower()=="yes"),None)
            if not token:books.append(None);continue
            book,request=live_book(token);books.append(book);requests.append(request)
        if any(book is None for book in books):excluded.append({"tracking_id":tracking_id,"reason":"two_sided_live_book_missing"});continue
        mids=[float(book["mid"]) for book in books if book];total=sum(mids)
        if not .80<=total<=1.20:excluded.append({"tracking_id":tracking_id,"reason":"market_probability_sum_invalid","sum":total});continue
        market=[value/total for value in mids]
        for index,(market_row,bucket,book) in enumerate(zip(event.get("markets") or [],buckets,books)):
            forecast_id=core.stable_id("pm-social-count-shadow",tracking_id,str(market_row.get("id")),MODEL_VERSION);ledger["open_forecasts"].append({"forecast_id":forecast_id,"created_at":core.now_iso(),"captured_at":now.isoformat(),"status":"open","research_only":True,"domain":"social_count","event_id":str(event.get("id")),"event_group":group,"tracking_id":tracking_id,"market_id":str(market_row.get("id")),"market_index":index,"question":market_row.get("question"),"bucket":bucket,"handle":user.get("handle"),"cutoff_at":start.isoformat(),"capture_window_start_at":window_start.isoformat(),"capture_window_end_at":start.isoformat(),"end_at":end.isoformat(),"model_version":MODEL_VERSION,"model_score":model[index],"market_probability_at_forecast":market[index],"live_book_at_forecast":book,"forecast_inputs":{"prior_observed_hours":len(history),"mean_posts_per_hour":mean_h,"variance_posts_per_hour":var_h,"duration_hours":duration},"model_frozen_at":MODEL_FROZEN_AT,"historical_oos_gate_met":False,"independent_confirmation_sources_present":0,"not_a_true_probability":True,"not_eligible_for_paper_entry":True,"paper_only":True,"live_orders_enabled":False,"private_api_used":False});opened.append(forecast_id)
        existing.add((group,MODEL_VERSION))
    ledger["latest_audit"]=audit(ledger);ledger["updated_at"]=core.now_iso();ledger["events"].append({"at":core.now_iso(),"type":"social_count_shadow_cycle","opened":opened,"settled":settlement["settled"],"excluded":excluded});core.write_json(ledger_path,ledger);after={str(path.relative_to(ROOT)):file_sha(path) for path in protected}
    if before!=after:raise RuntimeError("social-count shadow observed protected artifact mutation")
    result={"schema_version":"social-count-shadow-cycle-v3","created_at":core.now_iso(),"model_version":MODEL_VERSION,"model_frozen_at":MODEL_FROZEN_AT,"capture_window_minutes":CAPTURE_WINDOW_MINUTES,"strict_pre_event_capture":True,"strict_fixed_window_capture":True,"retroactive_backfill_allowed":False,"opened":opened,"settled":settlement["settled"],"excluded":excluded,"open_forecasts":len(ledger["open_forecasts"]),"resolved_forecasts":len(ledger["resolved_forecasts"]),"audit":ledger["latest_audit"],"request_count":len(requests)+len(settlement["requests"]),"protected_artifacts_unchanged":True,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False};core.write_json(output,result);return result


def self_test()->dict[str,Any]:
    ledger=new_ledger();assert ledger["paper_estimates_emitted"] is False and audit(ledger)["research_evidence_status"]=="insufficient_or_nonpositive_fresh_oos_evidence";assert core.parse_iso(MODEL_FROZEN_AT)>core.parse_iso("2026-07-11T14:28:00Z")
    start=datetime(2026,7,13,0,0,tzinfo=timezone.utc);assert capture_state(start-timedelta(minutes=30),start)=="eligible";assert capture_state(start,start)=="forecast_window_missed"
    return {"status":"pass","tests":["separate_social_count_ledger","strict_pre_event_capture","post_holdout_freeze_boundary","no_paper_estimates"]}


def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--ledger",default=str(DEFAULT_LEDGER));parser.add_argument("--output",default=str(ROOT/"experiments/current-social-count-shadow-cycle.json"));parser.add_argument("--self-test",action="store_true");args=parser.parse_args();payload=self_test() if args.self_test else collect(Path(args.ledger),Path(args.output));print(json.dumps(payload,ensure_ascii=False,indent=2));return 0


if __name__=="__main__":raise SystemExit(main())
