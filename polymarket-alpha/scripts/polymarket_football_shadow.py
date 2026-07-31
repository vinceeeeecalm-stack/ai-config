#!/usr/bin/env python3
"""Forward-only shadow forecasts for the frozen, failed World Cup 1X2 V1 model."""
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
MODEL_VERSION="pm-football-dk-devig-close-v1-20260711"
MODEL_FROZEN_AT="2026-07-11T13:54:00+00:00"
DEFAULT_LEDGER=ROOT/"data/football_research_forecast_ledger.json"


def load(name:str,path:Path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(module);return module


core=load("football_shadow_core",ROOT/"scripts/polymarket_alpha.py")
public=load("football_shadow_public",ROOT/"scripts/polymarket_public_data.py")
football=load("football_shadow_lab",ROOT/"scripts/polymarket_football_lab.py")


def new_ledger()->dict[str,Any]:
    return {"schema_version":"football-research-forecast-ledger-v1","created_at":core.now_iso(),"updated_at":core.now_iso(),"open_forecasts":[],"resolved_forecasts":[],"events":[],"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False}


def load_ledger(path:Path)->dict[str,Any]:
    ledger=core.read_json(path) if path.exists() else new_ledger()
    if (ledger.get("paper_estimates_emitted") is not False or ledger.get("main_paper_ledger_mutated") is not False
            or ledger.get("live_orders_enabled") is not False or ledger.get("private_api_used") is not False):
        raise ValueError("unsafe football shadow ledger")
    return ledger


def file_sha(path:Path)->str|None:return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def active_events()->tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    url=f"{football.GAMMA}/series?{urlencode({'slug':'soccer-fifwc'})}";payload=football.get_json(url,timeout=20,retries=2)
    summaries=(payload[0].get("events") or []) if isinstance(payload,list) and payload else []
    events=[row for row in summaries if not row.get("parentEventId") and " vs. " in str(row.get("title") or "") and " - " not in str(row.get("title") or "")]
    requests=[{"url":url,"status":"ok","rows":len(summaries),"main_event_summaries":len(events),"lightweight_series_index":True}];known={str(row.get("id")) for row in events}
    # The series index can include a prop while omitting its parent main event.
    # Resolve only those explicit parent IDs; never download the 20MB active-events payload.
    for parent_id in sorted({str(row.get("parentEventId")) for row in summaries if row.get("parentEventId")} - known):
        detail_url=f"{football.GAMMA}/events/{parent_id}";detail=football.get_json(detail_url,timeout=20,retries=2);requests.append({"url":detail_url,"status":"ok","parent_event_lookup":True})
        if not detail.get("parentEventId") and " vs. " in str(detail.get("title") or "") and football.is_main_1x2_event(detail):events.append(detail);known.add(parent_id)
    return events,requests


def espn_schedule()->tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    today=datetime.now(timezone.utc).date();end=today+timedelta(days=2);date_window=f"{today:%Y%m%d}-{end:%Y%m%d}"
    url=f"{football.ESPN_SITE}/scoreboard?dates={date_window}&limit=50";payload=football.get_json(url,timeout=20,retries=1)
    rows=payload.get("events") or []
    return rows,[{"url":url,"status":"ok","rows":len(rows)}]


def match_espn(event:dict[str,Any],schedule:list[dict[str,Any]])->tuple[dict[str,Any]|None,float|None]:
    kickoff=core.parse_iso(event.get("startTime") or event.get("endDate"));ranked=[]
    for row in schedule:
        candidate=core.parse_iso(row.get("date"))
        if kickoff and candidate and abs((candidate-kickoff).total_seconds())<=7200:ranked.append((football.match_score(event,row),row))
    ranked.sort(key=lambda item:item[0],reverse=True)
    return (ranked[0][1],ranked[0][0]) if ranked and ranked[0][0]>=.72 else (None,None)


def fetch_dk_odds(espn_event_id:str)->tuple[dict[str,Any]|None,list[dict[str,Any]]]:
    url=f"{football.ESPN_CORE}/events/{espn_event_id}/competitions/{espn_event_id}/odds?lang=en&region=us"
    payload=football.get_json(url);odds=football.parse_provider_odds(payload)
    return odds,[{"url":url,"status":"ok","provider_count":payload.get("count",0),"draftkings_parsed":odds is not None}]


def live_book(token:str)->tuple[dict[str,Any]|None,dict[str,Any]]:
    url=f"{football.CLOB}/book?{urlencode({'token_id':token})}";payload=football.get_json(url,timeout=30)
    bids=[float(row["price"]) for row in payload.get("bids") or [] if row.get("price") is not None]
    asks=[float(row["price"]) for row in payload.get("asks") or [] if row.get("price") is not None]
    if not bids or not asks:return None,{"url":url,"status":"failed","error":"two_sided_book_missing","bids":len(bids),"asks":len(asks)}
    bid=max(bids);ask=min(asks);mid=(bid+ask)/2;spread=ask-bid
    if not 0<=bid<=ask<=1:return None,{"url":url,"status":"failed","error":"invalid_book_order","best_bid":bid,"best_ask":ask}
    return {"best_bid":bid,"best_ask":ask,"mid":mid,"spread":spread},{"url":url,"status":"ok","best_bid":bid,"best_ask":ask,"spread":spread}


def settle(ledger:dict[str,Any])->dict[str,Any]:
    settled=[];requests=[];details={}
    for event_id in sorted({str(row["event_id"]) for row in ledger.get("open_forecasts",[])}):
        url=f"{football.GAMMA}/events/{event_id}";details[event_id]=football.get_json(url);requests.append({"url":url,"status":"ok"})
    for row in list(ledger.get("open_forecasts",[])):
        event=details.get(str(row["event_id"])) or {};winner=football.winner_index(event)
        if winner is None:continue
        actual=1 if int(row["market_index"])==winner else 0
        closed={**row,"status":"resolved","resolved_at":core.now_iso(),"actual_yes":actual,"winning_market_index":winner}
        ledger["open_forecasts"].remove(row);ledger["resolved_forecasts"].append(closed);settled.append(row["forecast_id"])
    return {"settled":settled,"requests":requests}


def audit(ledger:dict[str,Any])->dict[str,Any]:
    groups={}
    for row in ledger.get("resolved_forecasts",[]):
        if row.get("actual_yes") in {0,1}:groups.setdefault(row["event_group"],[]).append(row)
    scored=[]
    for group,items in sorted(groups.items()):
        winners=[row for row in items if row["actual_yes"]==1]
        if len(items)!=3 or len(winners)!=1:continue
        model_brier=statistics.fmean((float(row["model_score"])-row["actual_yes"])**2 for row in items)
        market_brier=statistics.fmean((float(row["market_probability_at_forecast"])-row["actual_yes"])**2 for row in items)
        winner=winners[0];model_log=-math.log(max(float(winner["model_score"]),1e-12));market_log=-math.log(max(float(winner["market_probability_at_forecast"]),1e-12))
        scored.append({"event_group":group,"contracts":3,"model_brier":model_brier,"market_brier":market_brier,"market_minus_model_brier":market_brier-model_brier,"model_log_loss":model_log,"market_log_loss":market_log,"market_minus_model_log_loss":market_log-model_log})
    def summary(field:str)->tuple[float|None,float|None]:
        values=[row[field] for row in scored]
        if not values:return None,None
        mean=statistics.fmean(values);lower=mean-1.96*statistics.stdev(values)/math.sqrt(len(values)) if len(values)>=2 else None
        return mean,lower
    brier,brier_lower=summary("market_minus_model_brier");log,log_lower=summary("market_minus_model_log_loss")
    promising=len(scored)>=30 and brier_lower is not None and brier_lower>0 and log_lower is not None and log_lower>0
    return {"schema_version":"football-shadow-audit-v1","resolved_forecasts":sum(len(items) for items in groups.values()),"resolved_event_groups":len(scored),"group_rows":scored,"mean_market_minus_model_brier":brier,"paired_brier_95pct_lower":brier_lower,"mean_market_minus_model_log_loss":log,"paired_log_loss_95pct_lower":log_lower,"research_evidence_status":"promising_requires_manual_review" if promising else "insufficient_or_nonpositive_fresh_oos_evidence","paper_promotion_automatic":False}


def collect(ledger_path:Path,output:Path)->dict[str,Any]:
    protected=[ROOT/"data/paper_ledger.json",ROOT/"experiments/current-crypto-barrier-estimates.json"]
    before={str(path.relative_to(ROOT)):file_sha(path) for path in protected};ledger=load_ledger(ledger_path);settlement=settle(ledger)
    walk=core.read_json(ROOT/"experiments/current-football-walk-forward.json")
    if walk.get("research_promotion_candidate") is not False or walk.get("paper_estimates_allowed") is not False:raise ValueError("football frozen-failure contract mismatch")
    events,event_requests=active_events();schedule,schedule_requests=espn_schedule();now=datetime.now(timezone.utc);freeze=core.parse_iso(MODEL_FROZEN_AT)
    existing={(row["event_group"],row["model_version"]) for row in ledger["open_forecasts"]+ledger["resolved_forecasts"]};opened=[];excluded=[];source_requests=[]
    for event in events:
        event_id=str(event.get("id"));espn,score=match_espn(event,schedule)
        if not espn:excluded.append({"event_id":event_id,"reason":"official_schedule_match_missing"});continue
        kickoff=core.parse_iso(espn.get("date"));group=f"FIFAWC:{event_id}"
        if not kickoff:excluded.append({"event_id":event_id,"reason":"kickoff_missing"});continue
        cutoff=kickoff-timedelta(hours=1);timing={"cutoff_at":cutoff.isoformat(),"capture_window_end_at":kickoff.isoformat()}
        if cutoff<freeze:excluded.append({"event_id":event_id,"reason":"cutoff_before_model_freeze",**timing});continue
        if (group,MODEL_VERSION) in existing:excluded.append({"event_id":event_id,"reason":"forecast_already_recorded",**timing});continue
        if now<cutoff:excluded.append({"event_id":event_id,"reason":"fixed_cutoff_not_reached",**timing});continue
        if now>=kickoff:excluded.append({"event_id":event_id,"reason":"forecast_window_missed",**timing});continue
        detail_url=f"{football.GAMMA}/events/{event_id}";detail=football.get_json(detail_url);source_requests.append({"url":detail_url,"status":"ok"})
        if not football.is_main_1x2_event(detail):excluded.append({"event_id":event_id,"reason":"main_90m_1x2_contract_invalid"});continue
        roles=football.market_roles(detail,espn);markets=detail.get("markets") or []
        if roles is None:excluded.append({"event_id":event_id,"reason":"outcome_role_mapping_failed"});continue
        odds,odds_requests=fetch_dk_odds(str(espn["id"]));source_requests.extend(odds_requests)
        if not odds:excluded.append({"event_id":event_id,"reason":"draftkings_odds_missing"});continue
        books=[]
        for market in markets:
            token=next((value for outcome,value in public.token_map(market).items() if outcome.lower()=="yes"),None)
            if not token:books.append(None);source_requests.append({"status":"failed","error":"yes_token_missing","market_id":str(market.get("id"))});continue
            book,request=live_book(token);books.append(book);source_requests.append(request)
        if any(book is None for book in books):excluded.append({"event_id":event_id,"reason":"two_sided_live_book_missing"});continue
        mids=[float(book["mid"]) for book in books if book];total=sum(mids)
        if not .80<=total<=1.20:excluded.append({"event_id":event_id,"reason":"market_probability_sum_invalid","sum":total});continue
        market_probabilities=[value/total for value in mids]
        for index,(market,role,book) in enumerate(zip(markets,roles,books)):
            forecast_id=core.stable_id("pm-football-shadow",event_id,str(market.get("id")),MODEL_VERSION)
            ledger["open_forecasts"].append({"forecast_id":forecast_id,"created_at":core.now_iso(),"captured_at":now.isoformat(),"status":"open","research_only":True,"domain":"football","event_id":event_id,"event_group":group,"market_id":str(market.get("id")),"condition_id":str(market.get("conditionId")),"market_index":index,"role":role,"question":market.get("question"),"kickoff_at":kickoff.isoformat(),"cutoff_at":cutoff.isoformat(),"model_version":MODEL_VERSION,"model_score":odds["devig"][role],"market_probability_at_forecast":market_probabilities[index],"live_book_at_forecast":book,"forecast_inputs":{"provider":odds["provider"],"provider_id":odds["provider_id"],"raw_implied":odds["raw_implied"],"overround":odds["overround"],"official_schedule_match_score":score},"model_frozen_at":MODEL_FROZEN_AT,"historical_oos_gate_met":False,"source_independence_gate_met":False,"tactical_lineup_gate_met":False,"not_a_true_probability":True,"not_eligible_for_paper_entry":True,"paper_only":True,"live_orders_enabled":False,"private_api_used":False});opened.append(forecast_id)
        existing.add((group,MODEL_VERSION))
    ledger["latest_audit"]=audit(ledger);ledger["updated_at"]=core.now_iso();ledger["events"].append({"at":core.now_iso(),"type":"football_shadow_cycle","opened":opened,"settled":settlement["settled"],"excluded":excluded})
    core.write_json(ledger_path,ledger);after={str(path.relative_to(ROOT)):file_sha(path) for path in protected}
    if before!=after:raise RuntimeError("football shadow observed protected artifact mutation")
    payload={"schema_version":"football-shadow-cycle-v2","created_at":core.now_iso(),"model_version":MODEL_VERSION,"model_frozen_at":MODEL_FROZEN_AT,"strict_pre_event_capture":True,"retroactive_backfill_allowed":False,"opened":opened,"settled":settlement["settled"],"excluded":excluded,"open_forecasts":len(ledger["open_forecasts"]),"resolved_forecasts":len(ledger["resolved_forecasts"]),"audit":ledger["latest_audit"],"request_count":len(event_requests)+len(schedule_requests)+len(settlement["requests"])+len(source_requests),"protected_artifacts_unchanged":True,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False}
    core.write_json(output,payload);return payload


def self_test()->dict[str,Any]:
    ledger=new_ledger();assert ledger["paper_estimates_emitted"] is False and ledger["main_paper_ledger_mutated"] is False
    assert audit(ledger)["research_evidence_status"]=="insufficient_or_nonpositive_fresh_oos_evidence"
    assert core.parse_iso(MODEL_FROZEN_AT)>core.parse_iso("2026-07-11T13:53:00+00:00")
    return {"status":"pass","tests":["separate_football_ledger","post_holdout_freeze_boundary","no_paper_estimates"]}


def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("--ledger",default=str(DEFAULT_LEDGER));parser.add_argument("--output",default=str(ROOT/"experiments/current-football-shadow-cycle.json"));parser.add_argument("--self-test",action="store_true");args=parser.parse_args()
    payload=self_test() if args.self_test else collect(Path(args.ledger),Path(args.output));print(json.dumps(payload,ensure_ascii=False,indent=2));return 0


if __name__=="__main__":raise SystemExit(main())
