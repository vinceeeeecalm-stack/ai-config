#!/usr/bin/env python3
"""Forward-only shadow forecasts for the frozen, failed Dallas weather V1 model."""
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
from zoneinfo import ZoneInfo


ROOT=Path(__file__).resolve().parents[1]
MODEL_VERSION="pm-weather-dallas-day1-v1-20260711-forward-v2"
MODEL_FROZEN_AT="2026-07-11T13:10:00+00:00"
CAPTURE_WINDOW_MINUTES=60
DEFAULT_LEDGER=ROOT/"data/weather_research_forecast_ledger.json"


def load(name:str,path:Path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(module);return module


core=load("weather_shadow_core",ROOT/"scripts/polymarket_alpha.py")
weather=load("weather_shadow_lab",ROOT/"scripts/polymarket_weather_lab.py")
public=load("weather_shadow_public",ROOT/"scripts/polymarket_public_data.py")


def new_ledger()->dict[str,Any]:
    return {"schema_version":"weather-research-forecast-ledger-v1","created_at":core.now_iso(),"updated_at":core.now_iso(),"open_forecasts":[],"resolved_forecasts":[],"events":[],"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False}


def load_ledger(path:Path)->dict[str,Any]:
    ledger=core.read_json(path) if path.exists() else new_ledger()
    if ledger.get("paper_estimates_emitted") is not False or ledger.get("main_paper_ledger_mutated") is not False or ledger.get("live_orders_enabled") is not False or ledger.get("private_api_used") is not False: raise ValueError("unsafe weather shadow ledger")
    return ledger


def file_sha(path:Path)->str|None:return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def capture_state(now:datetime,cutoff:datetime)->str:
    window_start=cutoff-timedelta(minutes=CAPTURE_WINDOW_MINUTES)
    if now<window_start:return "fixed_cutoff_not_reached"
    if now>=cutoff:return "forecast_window_missed"
    return "eligible"


def active_events()->tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    url=f"{weather.GAMMA}/events?{urlencode({'series_id':weather.SERIES_ID,'closed':'false','limit':20,'order':'endDate','ascending':'true'})}"
    payload=weather.get_json(url);return payload,[{"url":url,"status":"ok","rows":len(payload)}]


def forecast_maxima(date:str)->tuple[dict[str,float],list[dict[str,Any]]]:
    result={};requests=[]
    for model in ("gfs_seamless","ecmwf_ifs025"):
        params={"latitude":weather.LATITUDE,"longitude":weather.LONGITUDE,"hourly":"temperature_2m_previous_day1","temperature_unit":"fahrenheit","timezone":weather.LOCAL_TIMEZONE,"start_date":date,"end_date":date,"models":model}
        url=f"{weather.PREVIOUS_RUNS}?{urlencode(params)}";payload=weather.get_json(url)
        hourly=payload.get("hourly") or {};values=[float(value) for value in hourly.get("temperature_2m_previous_day1") or [] if value is not None]
        if len(values)<18:raise ValueError("weather shadow fixed-lead forecast incomplete")
        result[model]=max(values);requests.append({"url":url,"status":"ok","hourly_rows":len(values)})
    return result,requests


def latest_yes_trade(condition:str,cutoff:int)->tuple[float|None,list[dict[str,Any]]]:
    requests=[];offset=0;seen=set()
    while True:
        url=f"{weather.DATA_API}/trades?{urlencode({'market':condition,'limit':1000,'offset':offset})}";page=weather.get_json(url,timeout=30)
        if not isinstance(page,list):raise ValueError("weather shadow trade page invalid")
        timestamps=[int(row["timestamp"]) for row in page if row.get("timestamp") is not None]
        if timestamps!=sorted(timestamps,reverse=True):raise ValueError("weather shadow trade page not newest-first")
        fingerprint=hashlib.sha256(json.dumps([(row.get('timestamp'),row.get('price'),row.get('outcome')) for row in page],sort_keys=True).encode()).hexdigest()
        if fingerprint in seen:raise ValueError("weather shadow repeated trade page")
        seen.add(fingerprint);requests.append({"url":url,"status":"ok","rows":len(page)})
        eligible=[row for row in page if row.get("timestamp") is not None and int(row["timestamp"])<=cutoff]
        if eligible:return weather.trade_yes_price(eligible[0]),requests
        if len(page)<1000:return None,requests
        offset+=len(page)
        if offset>=10000:raise ValueError("weather shadow trade page budget exhausted")


def live_yes_midpoint(market:dict[str,Any])->tuple[dict[str,Any]|None,dict[str,Any]]:
    token=next((value for outcome,value in public.token_map(market).items() if outcome.lower()=="yes"),None)
    if not token:return None,{"status":"failed","error":"yes_token_missing","market_id":str(market.get("id"))}
    url=f"{public.CLOB_BASE}/book?{urlencode({'token_id':token})}"
    try:
        book=public.get_json(url);bids=core.levels(book,"bids");asks=core.levels(book,"asks")
        if not bids or not asks:return None,{"url":url,"status":"failed","error":"two_sided_book_missing"}
        best_bid,best_ask=bids[0][0],asks[0][0]
        if best_bid>=best_ask:return None,{"url":url,"status":"failed","error":"crossed_or_invalid_book"}
        return {"mid":(best_bid+best_ask)/2,"best_bid":best_bid,"best_ask":best_ask,"spread":best_ask-best_bid,"token_id":token,"book_timestamp":book.get("timestamp"),"book_hash":book.get("hash")},{"url":url,"status":"ok"}
    except Exception as exc:
        return None,{"url":url,"status":"failed","error":f"{type(exc).__name__}:{exc}"}


def settle(ledger:dict[str,Any])->dict[str,Any]:
    settled=[];requests=[]
    event_ids=sorted({str(row["event_id"]) for row in ledger.get("open_forecasts",[])})
    details={}
    for event_id in event_ids:
        url=f"{weather.GAMMA}/events/{event_id}";details[event_id]=weather.get_json(url);requests.append({"url":url,"status":"ok"})
    for row in list(ledger.get("open_forecasts",[])):
        event=details.get(str(row["event_id"])) or {};winner=weather.resolved_winner(event)
        if not winner:continue
        actual=1 if str(winner.get("id"))==str(row["market_id"]) else 0
        closed={**row,"status":"resolved","resolved_at":core.now_iso(),"actual_yes":actual,"winning_market_id":str(winner.get("id"))}
        ledger["open_forecasts"].remove(row);ledger["resolved_forecasts"].append(closed);settled.append(row["forecast_id"])
    return {"settled":settled,"requests":requests}


def audit(ledger:dict[str,Any])->dict[str,Any]:
    rows=[row for row in ledger.get("resolved_forecasts",[]) if row.get("actual_yes") in {0,1}]
    groups={}
    for row in rows:groups.setdefault(row["event_group"],[]).append(row)
    scored=[]
    for group,items in sorted(groups.items()):
        model=statistics.fmean((float(x["model_score"])-x["actual_yes"])**2 for x in items);market=statistics.fmean((float(x["market_probability_at_forecast"])-x["actual_yes"])**2 for x in items)
        scored.append({"event_group":group,"contracts":len(items),"model_brier":model,"market_brier":market,"market_minus_model_brier":market-model})
    diffs=[x["market_minus_model_brier"] for x in scored];mean=statistics.fmean(diffs) if diffs else None
    lower=mean-1.96*statistics.stdev(diffs)/math.sqrt(len(diffs)) if len(diffs)>=2 else None
    return {"schema_version":"weather-shadow-audit-v1","resolved_forecasts":len(rows),"resolved_event_groups":len(scored),"group_rows":scored,"mean_market_minus_model_brier":mean,"paired_brier_95pct_lower":lower,"research_evidence_status":"promising_requires_manual_review" if len(scored)>=30 and lower is not None and lower>0 else "insufficient_or_nonpositive_fresh_oos_evidence","paper_promotion_automatic":False}


def collect(ledger_path:Path,output:Path)->dict[str,Any]:
    protected=[ROOT/"data/paper_ledger.json",ROOT/"experiments/current-crypto-barrier-estimates.json"]
    before={str(path.relative_to(ROOT)):file_sha(path) for path in protected};ledger=load_ledger(ledger_path);settlement=settle(ledger)
    events,event_requests=active_events();now=datetime.now(timezone.utc);freeze=core.parse_iso(MODEL_FROZEN_AT);zone=ZoneInfo(weather.LOCAL_TIMEZONE)
    existing={(row["event_group"],row["model_version"]) for row in ledger["open_forecasts"]+ledger["resolved_forecasts"]};opened=[];excluded=[];source_requests=[]
    walk=core.read_json(ROOT/"experiments/current-weather-walk-forward.json")
    if walk.get("research_promotion_candidate") is not False or walk.get("paper_estimates_allowed") is not False:raise ValueError("weather frozen-failure contract mismatch")
    for event in events:
        date=weather.event_date(event);group=f"{weather.STATION}:{date}" if date else ""
        if not date:excluded.append({"event_id":str(event.get('id')),"reason":"event_date_missing"});continue
        cutoff=(datetime.fromisoformat(date).replace(tzinfo=zone)-timedelta(minutes=1)).astimezone(timezone.utc);window_start=cutoff-timedelta(minutes=CAPTURE_WINDOW_MINUTES)
        timing={"cutoff_at":cutoff.isoformat(),"capture_window_start_at":window_start.isoformat()}
        if cutoff<freeze:excluded.append({"event_id":str(event.get('id')),"reason":"cutoff_before_model_freeze",**timing});continue
        if (group,MODEL_VERSION) in existing:excluded.append({"event_id":str(event.get('id')),"reason":"forecast_already_recorded"});continue
        timing_state=capture_state(now,cutoff)
        if timing_state!="eligible":excluded.append({"event_id":str(event.get('id')),"reason":timing_state,**timing});continue
        markets=event.get("markets") or [];buckets=[weather.parse_bucket(str(m.get("groupItemTitle") or m.get("question") or "")) for m in markets]
        if any(item is None for item in buckets):excluded.append({"event_id":str(event.get('id')),"reason":"bucket_ladder_unparsed"});continue
        forecasts,forecast_requests=forecast_maxima(date);source_requests.extend(forecast_requests);forecast=statistics.fmean(forecasts.values())
        model=weather.bucket_probabilities([item for item in buckets if item is not None],forecast,float(walk["selected_fit"]["bias_f"]),float(walk["selected_fit"]["sigma_f"]))
        market=[];books=[];book_requests=[]
        for contract in markets:
            book,book_request=live_yes_midpoint(contract);source_requests.append(book_request);book_requests.append(book_request);books.append(book);market.append(book["mid"] if book else None)
        if any(value is None for value in market):
            missing=[{"market_id":str(contract.get("id")),"question":contract.get("question"),
                      "bucket":contract.get("groupItemTitle"),"error":request.get("error")}
                     for contract,book,request in zip(markets,books,book_requests) if book is None]
            excluded.append({"event_id":str(event.get('id')),"reason":"two_sided_live_book_missing",
                             "market_count":len(markets),"missing_market_count":len(missing),
                             "missing_live_books":missing,**timing});continue
        total=sum(float(value) for value in market)
        if not .80<=total<=1.20:excluded.append({"event_id":str(event.get('id')),"reason":"market_ladder_probability_sum_invalid"});continue
        market=[float(value)/total for value in market]
        for index,contract in enumerate(markets):
            forecast_id=core.stable_id("pm-weather-shadow",str(event.get("id")),str(contract.get("id")),MODEL_VERSION)
            ledger["open_forecasts"].append({"forecast_id":forecast_id,"created_at":core.now_iso(),"captured_at":now.isoformat(),"status":"open","research_only":True,"domain":"weather","event_id":str(event.get("id")),"event_group":group,"market_id":str(contract.get("id")),"condition_id":str(contract.get("conditionId")),"question":contract.get("question"),"bucket":buckets[index],"target_date":date,"cutoff_at":cutoff.isoformat(),"capture_window_start_at":window_start.isoformat(),"model_version":MODEL_VERSION,"model_score":model[index],"market_probability_at_forecast":market[index],"live_book_at_forecast":books[index],"market_probability_source":"official_clob_two_sided_midpoint_captured_pre_event","forecast_inputs":{"gfs_day1_high_f":forecasts["gfs_seamless"],"ecmwf_day1_high_f":forecasts["ecmwf_ifs025"],"selected_model":"mean","bias_f":walk["selected_fit"]["bias_f"],"sigma_f":walk["selected_fit"]["sigma_f"]},"model_frozen_at":MODEL_FROZEN_AT,"not_a_true_probability":True,"not_eligible_for_paper_entry":True,"source_independence_gate_met":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False});opened.append(forecast_id)
        existing.add((group,MODEL_VERSION))
    ledger["latest_audit"]=audit(ledger);ledger["updated_at"]=core.now_iso();ledger["events"].append({"at":core.now_iso(),"type":"weather_shadow_cycle","opened":opened,"settled":settlement["settled"],"excluded":excluded})
    precommit={str(path.relative_to(ROOT)):file_sha(path) for path in protected}
    if before!=precommit:raise RuntimeError("weather shadow observed protected artifact mutation")
    core.write_json(ledger_path,ledger)
    payload={"schema_version":"weather-shadow-cycle-v2","created_at":core.now_iso(),"model_version":MODEL_VERSION,"model_frozen_at":MODEL_FROZEN_AT,"capture_window_minutes":CAPTURE_WINDOW_MINUTES,"strict_pre_event_capture":True,"retroactive_backfill_allowed":False,"opened":opened,"settled":settlement["settled"],"excluded":excluded,"open_forecasts":len(ledger["open_forecasts"]),"resolved_forecasts":len(ledger["resolved_forecasts"]),"audit":ledger["latest_audit"],"request_count":len(event_requests)+len(settlement["requests"])+len(source_requests),"protected_artifacts_unchanged_through_precommit":True,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False}
    core.write_json(output,payload);return payload


def self_test()->dict[str,Any]:
    ledger=new_ledger();assert ledger["paper_estimates_emitted"] is False and ledger["main_paper_ledger_mutated"] is False
    assert audit(ledger)["research_evidence_status"]=="insufficient_or_nonpositive_fresh_oos_evidence"
    cutoff=datetime(2026,7,13,5,0,tzinfo=timezone.utc);assert capture_state(cutoff-timedelta(minutes=61),cutoff)=="fixed_cutoff_not_reached";assert capture_state(cutoff-timedelta(minutes=30),cutoff)=="eligible";assert capture_state(cutoff,cutoff)=="forecast_window_missed"
    return {"status":"pass","tests":["separate_weather_ledger","strict_pre_event_capture_window","no_retroactive_backfill","no_paper_estimates"]}


def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--ledger",default=str(DEFAULT_LEDGER));parser.add_argument("--output",default=str(ROOT/"experiments/current-weather-shadow-cycle.json"));parser.add_argument("--self-test",action="store_true");args=parser.parse_args()
    payload=self_test() if args.self_test else collect(Path(args.ledger),Path(args.output));print(json.dumps(payload,ensure_ascii=False,indent=2));return 0


if __name__=="__main__":raise SystemExit(main())
