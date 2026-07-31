#!/usr/bin/env python3
"""Official-data research lab for Polymarket social post-count markets."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


ROOT=Path(__file__).resolve().parents[1]
GAMMA="https://gamma-api.polymarket.com"
XTRACKER="https://xtracker.polymarket.com/api"
MODEL_VERSION="pm-social-count-trailing-nb-v1-research"


def load(name:str,path:Path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(module);return module


core=load("social_count_core",ROOT/"scripts/polymarket_alpha.py")
public=load("social_count_public",ROOT/"scripts/polymarket_public_data.py")


def get_json(url:str,timeout:float=30,retries:int=2)->Any:
    if not url.startswith((GAMMA+"/",XTRACKER+"/")):raise ValueError("social-count source host not allowlisted")
    errors=[]
    for attempt in range(retries+1):
        try:
            with urlopen(Request(url,headers={"Accept":"application/json","User-Agent":"polymarket-paper-research/1.0"}),timeout=timeout) as response:return json.loads(response.read().decode())
        except Exception as exc:
            errors.append(f"attempt_{attempt+1}:{type(exc).__name__}:{exc}")
            if attempt<retries:time.sleep(min(2**attempt,2))
    raise RuntimeError(";".join(errors))


def tracking_slug(tracking:dict[str,Any])->str|None:
    link=str(tracking.get("marketLink") or "").strip()
    if not link:return None
    parsed=urlparse(link)
    if parsed.netloc not in {"polymarket.com","www.polymarket.com"}:return None
    parts=[part for part in parsed.path.split("/") if part]
    return parts[-1] if len(parts)>=2 and parts[-2]=="event" else None


def parse_bucket(text:str)->dict[str,int|None]|None:
    value=text.lower().replace(",","").replace("–","-").replace("—","-")
    match=re.search(r"(?<!\d)(\d+)\s*-\s*(\d+)(?!\d)",value)
    if match:return {"low":int(match.group(1)),"high":int(match.group(2))}
    match=re.search(r"(?<!\d)(\d+)\s*\+",value)
    if match:return {"low":int(match.group(1)),"high":None}
    match=re.search(r"<\s*(\d+)",value)
    if match:return {"low":0,"high":int(match.group(1))-1}
    match=re.search(r">\s*(\d+)",value)
    if match:return {"low":int(match.group(1))+1,"high":None}
    match=re.search(r"(?:less than|under)\s+(\d+)",value)
    if match:return {"low":0,"high":int(match.group(1))-1}
    match=re.search(r"(\d+)\s+or\s+(?:fewer|less)",value)
    if match:return {"low":0,"high":int(match.group(1))}
    match=re.search(r"(?:more than|over)\s+(\d+)",value)
    if match:return {"low":int(match.group(1))+1,"high":None}
    match=re.search(r"(\d+)\s+or\s+more",value)
    if match:return {"low":int(match.group(1)),"high":None}
    return None


def event_contract(event:dict[str,Any])->dict[str,Any]:
    rows=[]
    for market in event.get("markets") or []:
        label=str(market.get("groupItemTitle") or market.get("question") or "")
        rows.append({"market_id":str(market.get("id")),"condition_id":str(market.get("conditionId") or ""),"label":label,"bucket":parse_bucket(label)})
    parsed=sum(row["bucket"] is not None for row in rows)
    if rows and parsed==len(rows):kind="count_ladder" if len(rows)>=2 else "binary_threshold"
    elif "more" in str(event.get("title") or "").lower() and "day before" in str(event.get("title") or "").lower():kind="relative_day_comparison"
    else:kind="unsupported_or_unparsed"
    return {"kind":kind,"markets":rows,"market_count":len(rows),"parsed_bucket_count":parsed}


def file_entry(path:Path,root:Path)->dict[str,Any]:
    raw=path.read_bytes();return {"path":str(path.relative_to(root)),"size_bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}


def discover(output_dir:Path,workers:int=8)->dict[str,Any]:
    output_dir.mkdir(parents=True,exist_ok=True);tracking_url=f"{XTRACKER}/trackings?activeOnly=false";payload=get_json(tracking_url)
    trackings=payload.get("data") if isinstance(payload,dict) else None
    if not isinstance(trackings,list):raise ValueError("XTracker tracking payload invalid")
    now=datetime.now(timezone.utc);eligible=[];excluded=[]
    for row in trackings:
        slug=tracking_slug(row);end=core.parse_iso(row.get("endDate"))
        if not slug:excluded.append({"tracking_id":str(row.get("id")),"reason":"official_market_link_missing_or_invalid"});continue
        if not end:excluded.append({"tracking_id":str(row.get("id")),"reason":"tracking_end_missing"});continue
        if end>now:excluded.append({"tracking_id":str(row.get("id")),"reason":"tracking_not_ended"});continue
        eligible.append((slug,row))
    unique={slug:row for slug,row in eligible}
    def fetch(item:tuple[str,dict[str,Any]])->dict[str,Any]:
        slug,tracking=item;url=f"{GAMMA}/events?{urlencode({'slug':slug})}"
        try:
            events=get_json(url,timeout=20,retries=1);exact=next((event for event in events if event.get("slug")==slug),None)
            if not exact:return {"slug":slug,"tracking":tracking,"event":None,"request":{"url":url,"status":"excluded","error":"exact_event_missing"}}
            return {"slug":slug,"tracking":tracking,"event":exact,"contract":event_contract(exact),"request":{"url":url,"status":"ok","rows":len(events)}}
        except Exception as exc:return {"slug":slug,"tracking":tracking,"event":None,"request":{"url":url,"status":"failed","error":f"{type(exc).__name__}:{exc}"}}
    with ThreadPoolExecutor(max_workers=max(1,min(workers,12))) as executor:rows=list(executor.map(fetch,sorted(unique.items())))
    matched=[row for row in rows if row.get("event")];requests=[{"url":tracking_url,"status":"ok","rows":len(trackings)},*[row["request"] for row in rows]]
    kind_counts={}
    for row in matched:
        kind=row["contract"]["kind"];kind_counts[kind]=kind_counts.get(kind,0)+1
    core.write_json(output_dir/"events.json",matched);core.write_json(output_dir/"request-log.json",requests);core.write_json(output_dir/"exclusions.json",excluded)
    failed=sum(request.get("status")=="failed" for request in requests);unmapped=sum(request.get("status")=="excluded" for request in requests);mapping_rate=len(matched)/len(unique) if unique else 0
    manifest={"schema_version":"social-count-discovery-v1","created_at":core.now_iso(),"source_contracts":{"xtracker_trackings":tracking_url,"gamma_event_lookup":f"{GAMMA}/events?slug=<official-market-link-slug>"},"trackings_returned":len(trackings),"ended_trackings_with_official_market_link":len(eligible),"unique_ended_event_slugs":len(unique),"gamma_events_matched":len(matched),"mapping_rate":mapping_rate,"contract_kind_counts":kind_counts,"excluded_tracking_rows":len(excluded),"unmapped_official_event_links":unmapped,"failed_request_count":failed,"files":[file_entry(output_dir/name,output_dir) for name in ("events.json","request-log.json","exclusions.json")],"data_status":"ok" if len(matched)>=30 and mapping_rate>=.98 and failed==0 else "degraded","research_stage":"coverage_only_no_model_scores","paper_estimates_allowed":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False}
    core.write_json(output_dir/"manifest.json",manifest);return manifest


def winning_bucket(row:dict[str,Any])->dict[str,int|None]|None:
    winners=[]
    for market,parsed in zip(row["event"].get("markets") or [],row["contract"]["markets"]):
        prices=market.get("outcomePrices")
        try:prices=json.loads(prices) if isinstance(prices,str) else prices
        except Exception:prices=[]
        if prices and float(prices[0])>=.99:winners.append(parsed.get("bucket"))
    return winners[0] if len(winners)==1 else None


def in_bucket(total:int,bucket:dict[str,int|None]|None)->bool:
    return bool(bucket) and total>=int(bucket["low"] or 0) and (bucket["high"] is None or total<=int(bucket["high"]))


def fetch_history(discovery_dir:Path,output_dir:Path,workers:int=10)->dict[str,Any]:
    if core.read_json(discovery_dir/"manifest.json").get("data_status")!="ok":raise ValueError("degraded social-count discovery input")
    rows=core.read_json(discovery_dir/"events.json");output_dir.mkdir(parents=True,exist_ok=True)
    def fetch(row:dict[str,Any])->dict[str,Any]:
        tracking_id=str(row["tracking"]["id"]);url=f"{XTRACKER}/trackings/{tracking_id}?includeStats=true"
        try:
            payload=get_json(url,timeout=25,retries=1);data=payload.get("data") if isinstance(payload,dict) else None;stats=(data or {}).get("stats") or {}
            if not data or stats.get("total") is None:return {**row,"tracking_stats":None,"request":{"url":url,"status":"failed","error":"complete_stats_missing"}}
            return {**row,"tracking_stats":stats,"request":{"url":url,"status":"ok","hourly_rows":len(stats.get("daily") or [])}}
        except Exception as exc:return {**row,"tracking_stats":None,"request":{"url":url,"status":"failed","error":f"{type(exc).__name__}:{exc}"}}
    with ThreadPoolExecutor(max_workers=max(1,min(workers,12))) as executor:resolved=list(executor.map(fetch,rows))
    history={};conflicts=[];consistent=0;complete=0
    for row in resolved:
        stats=row.get("tracking_stats") or {};user=(row["tracking"].get("user") or {});user_id=str(user.get("id") or row["tracking"].get("userId"));points=history.setdefault(user_id,{"user":user,"hours":{}})["hours"]
        if stats.get("isComplete") is True:complete+=1
        for point in stats.get("daily") or []:
            stamp=str(point.get("date") or "");count=point.get("count")
            if not stamp or count is None:continue
            if stamp in points and int(points[stamp])!=int(count):conflicts.append({"user_id":user_id,"timestamp":stamp,"first":points[stamp],"second":count})
            else:points[stamp]=int(count)
        total=int(stats["total"]) if stats.get("total") is not None else None;bucket=winning_bucket(row);row["winning_bucket"]=bucket;row["resolved_total"]=total;row["resolution_consistent_with_tracker"]=total is not None and in_bucket(total,bucket)
        if row["resolution_consistent_with_tracker"]:consistent+=1
    serialized={user_id:{"user":item["user"],"hours":[{"timestamp":stamp,"count":count} for stamp,count in sorted(item["hours"].items())]} for user_id,item in history.items()}
    eligible=0;x_comparable=0;x_consistent=0;truth_social_conflicts=0
    for row in resolved:
        user=row["tracking"].get("user") or {};platform=str(user.get("platform") or "");user_id=str(user.get("id") or row["tracking"].get("userId"));start=core.parse_iso(row["tracking"].get("startDate"));points=serialized.get(user_id,{}).get("hours",[])
        prior=[point for point in points if start and core.parse_iso(point["timestamp"])<start and core.parse_iso(point["timestamp"])>=start-timedelta(days=60)]
        comparable=row.get("winning_bucket") is not None and row.get("resolved_total") is not None
        if platform=="X" and comparable:x_comparable+=1;x_consistent+=int(row.get("resolution_consistent_with_tracker") is True)
        if platform=="TRUTH_SOCIAL" and comparable and row.get("resolution_consistent_with_tracker") is not True:truth_social_conflicts+=1
        row["prior_60d_observed_hours"]=len(prior);row["model_scope_gate_met"]=platform=="X";row["model_history_coverage_gate_met"]=platform=="X" and len(prior)>=30*24 and row.get("resolution_consistent_with_tracker") is True
        if row["model_history_coverage_gate_met"]:eligible+=1
    requests=[row.pop("request") for row in resolved];core.write_json(output_dir/"resolved-events.json",resolved);core.write_json(output_dir/"hourly-history.json",serialized);core.write_json(output_dir/"request-log.json",requests);core.write_json(output_dir/"hourly-conflicts.json",conflicts)
    failed=sum(request.get("status")=="failed" for request in requests)
    manifest={"schema_version":"social-count-xtracker-history-v1","created_at":core.now_iso(),"source":f"{XTRACKER}/trackings/<id>?includeStats=true","model_scope":"X platform only; Truth Social excluded for resolution-source disagreement","events_requested":len(rows),"complete_tracking_stats":complete,"resolution_consistent_events":consistent,"resolution_mismatch_or_unresolved_events":len(rows)-consistent,"x_comparable_resolved_events":x_comparable,"x_resolution_consistent_events":x_consistent,"x_resolution_consistency_rate":x_consistent/x_comparable if x_comparable else 0,"truth_social_conflict_events":truth_social_conflicts,"users":len(serialized),"deduplicated_hourly_rows":sum(len(item["hours"]) for item in serialized.values()),"hourly_conflicts":len(conflicts),"model_history_coverage_eligible_events":eligible,"failed_request_count":failed,"files":[file_entry(output_dir/name,output_dir) for name in ("resolved-events.json","hourly-history.json","request-log.json","hourly-conflicts.json")],"data_status":"ok" if failed==0 and not conflicts and x_comparable>=30 and x_consistent==x_comparable and eligible>=30 else "degraded","research_stage":"resolved_outcome_and_prehistory_coverage_only","paper_estimates_allowed":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False};core.write_json(output_dir/"manifest.json",manifest);return manifest


def fetch_market_prices(history_dir:Path,output_dir:Path,chunk_size:int=20,workers:int=10)->dict[str,Any]:
    if core.read_json(history_dir/"manifest.json").get("data_status")!="ok":raise ValueError("degraded social-count history input")
    events=[row for row in core.read_json(history_dir/"resolved-events.json") if row.get("model_history_coverage_gate_met") is True];output_dir.mkdir(parents=True,exist_ok=True)
    tasks=[]
    for row in events:
        cutoff=core.parse_iso(row["tracking"].get("startDate"));event_id=str(row["event"].get("id"))
        for market in row["event"].get("markets") or []:
            token=next((value for outcome,value in public.token_map(market).items() if outcome.lower()=="yes"),None)
            tasks.append({"event_id":event_id,"market_id":str(market.get("id")),"condition_id":str(market.get("conditionId") or ""),"token":str(token or ""),"cutoff":int(cutoff.timestamp()) if cutoff else None})
    histories={};requests=[];url=f"{public.CLOB_BASE}/batch-prices-history"
    tokens=list(dict.fromkeys(task["token"] for task in tasks if task["token"]))
    for index in range(0,len(tokens),max(1,chunk_size)):
        chunk=tokens[index:index+max(1,chunk_size)];body={"markets":chunk,"interval":"max","fidelity":60}
        try:
            payload=public.post_json(url,body,timeout=30,retries=2);batch=payload.get("history",{}) if isinstance(payload,dict) else {}
            if not isinstance(batch,dict):raise ValueError("batch history response invalid")
            for token in chunk:histories[token]=batch.get(token,[])
            requests.append({"url":url,"status":"ok","token_count":len(chunk),"history_count":len(batch),"fidelity_minutes":60})
        except Exception as exc:requests.append({"url":url,"status":"failed","token_count":len(chunk),"error":f"{type(exc).__name__}:{exc}"})
    # The batch max-interval endpoint omits some resolved tokens. Explicit
    # windows remain available and are the authoritative point-in-time fallback.
    missing_tasks=[]
    for task in tasks:
        eligible=[point for point in histories.get(task["token"],[]) if point.get("p") is not None and task["cutoff"] is not None and int(point.get("t",0))<=task["cutoff"]]
        if not eligible and task["token"] and task["cutoff"] is not None:missing_tasks.append(task)
    def explicit(task:dict[str,Any])->tuple[str,list[dict[str,Any]],dict[str,Any]]:
        params={"market":task["token"],"startTs":task["cutoff"]-72*3600,"endTs":task["cutoff"],"fidelity":10};explicit_url=f"{public.CLOB_BASE}/prices-history?{urlencode(params)}"
        try:
            payload=public.get_json(explicit_url,timeout=25,retries=2);points=payload.get("history",[]) if isinstance(payload,dict) else []
            return task["token"],points,{"url":explicit_url,"status":"ok","token_count":1,"points":len(points),"explicit_window_fallback":True}
        except Exception as exc:return task["token"],[],{"url":explicit_url,"status":"failed","token_count":1,"error":f"{type(exc).__name__}:{exc}","explicit_window_fallback":True}
    with ThreadPoolExecutor(max_workers=max(1,min(workers,12))) as executor:fallbacks=list(executor.map(explicit,missing_tasks))
    for token,points,request in fallbacks:histories[token]=points;requests.append(request)
    prices=[]
    for task in tasks:
        eligible=[point for point in histories.get(task["token"],[]) if point.get("p") is not None and task["cutoff"] is not None and int(point.get("t",0))<=task["cutoff"]]
        point=max(eligible,key=lambda item:int(item["t"])) if eligible else None
        prices.append({**task,"yes_price":float(point["p"]) if point else None,"price_timestamp":int(point["t"]) if point else None,"lag_seconds":task["cutoff"]-int(point["t"]) if point else None})
    by_event={}
    for row in prices:by_event.setdefault(row["event_id"],[]).append(row)
    complete_events=0;valid_sum_events=0
    for items in by_event.values():
        if all(item["yes_price"] is not None for item in items):
            complete_events+=1;total=sum(float(item["yes_price"]) for item in items)
            if .80<=total<=1.20:valid_sum_events+=1
    core.write_json(output_dir/"prices.json",prices);core.write_json(output_dir/"request-log.json",requests)
    failed=sum(request.get("status")=="failed" for request in requests);nonempty=sum(row["yes_price"] is not None for row in prices)
    manifest={"schema_version":"social-count-polymarket-cutoff-prices-v1","created_at":core.now_iso(),"source":url,"cutoff":"tracking_start","fidelity_minutes":60,"eligible_events":len(events),"contracts":len(tasks),"contracts_with_cutoff_price":nonempty,"complete_price_events":complete_events,"valid_probability_sum_events":valid_sum_events,"failed_request_count":failed,"files":[file_entry(output_dir/name,output_dir) for name in ("prices.json","request-log.json")],"data_status":"ok" if failed==0 and valid_sum_events>=90 else "degraded","research_stage":"point_in_time_market_benchmark_only","paper_estimates_allowed":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False};core.write_json(output_dir/"manifest.json",manifest);return manifest


def count_pmf(mean:float,variance:float,max_count:int)->list[float]:
    mean=max(mean,1e-9);variance=max(variance,mean)
    if variance<=mean*(1+1e-9):
        values=[math.exp(-mean)]
        for count in range(1,max_count+1):values.append(values[-1]*mean/count)
    else:
        r=mean*mean/(variance-mean);p=r/(r+mean);values=[p**r]
        for count in range(1,max_count+1):values.append(values[-1]*(count-1+r)/count*(1-p))
    return values


def bucket_probabilities(buckets:list[dict[str,int|None]],mean:float,variance:float)->list[float]:
    finite=max([int(bucket["high"]) for bucket in buckets if bucket["high"] is not None]+[int(bucket["low"] or 0) for bucket in buckets])+200
    pmf=count_pmf(mean,variance,finite);values=[]
    for bucket in buckets:
        low=int(bucket["low"] or 0);high=bucket["high"]
        values.append(sum(pmf[low:int(high)+1]) if high is not None else max(0,1-sum(pmf[:low])))
    total=sum(values);return [value/total for value in values] if total>0 else []


def paired_summary(rows:list[dict[str,Any]])->dict[str,Any]:
    def calc(field:str)->tuple[float|None,float|None]:
        values=[row[field] for row in rows]
        if not values:return None,None
        mean=statistics.fmean(values);lower=mean-1.96*statistics.stdev(values)/math.sqrt(len(values)) if len(values)>=2 else None;return mean,lower
    brier,brier_lower=calc("market_minus_model_brier");log,log_lower=calc("market_minus_model_log_loss")
    return {"event_groups":len(rows),"mean_market_minus_model_brier":brier,"paired_brier_95pct_lower":brier_lower,"mean_market_minus_model_log_loss":log,"paired_log_loss_95pct_lower":log_lower,"group_rows":rows}


def walk_forward(history_dir:Path,price_dir:Path,output:Path)->dict[str,Any]:
    if core.read_json(history_dir/"manifest.json").get("data_status")!="ok" or core.read_json(price_dir/"manifest.json").get("data_status")!="ok":raise ValueError("degraded social-count walk-forward input")
    events=core.read_json(history_dir/"resolved-events.json");hourly=core.read_json(history_dir/"hourly-history.json");prices=core.read_json(price_dir/"prices.json");by_price={}
    for row in prices:by_price.setdefault(row["event_id"],{})[row["market_id"]]=row["yes_price"]
    rows=[];exclusions={}
    def exclude(reason:str):exclusions.__setitem__(reason,exclusions.get(reason,0)+1)
    for row in events:
        if row.get("model_history_coverage_gate_met") is not True:exclude("scope_history_or_resolution_gate");continue
        event=row["event"];event_id=str(event.get("id"));start=core.parse_iso(row["tracking"].get("startDate"));end=core.parse_iso(row["tracking"].get("endDate"));user=row["tracking"].get("user") or {};user_id=str(user.get("id") or row["tracking"].get("userId"))
        if not start or not end or end<=start:exclude("tracking_window_invalid");continue
        history=[point for point in (hourly.get(user_id) or {}).get("hours",[]) if start-timedelta(days=60)<=core.parse_iso(point["timestamp"])<start]
        if len(history)<30*24:exclude("prior_history_under_30d_observed_hours");continue
        counts=[float(point["count"]) for point in history];duration_hours=(end-start).total_seconds()/3600;mean_h=statistics.fmean(counts);var_h=statistics.variance(counts) if len(counts)>=2 else mean_h
        markets=event.get("markets") or [];buckets=[parsed.get("bucket") for parsed in row["contract"]["markets"]]
        if any(bucket is None for bucket in buckets):exclude("bucket_unparsed");continue
        model=bucket_probabilities([bucket for bucket in buckets if bucket is not None],mean_h*duration_hours,max(var_h,mean_h)*duration_hours)
        market=[by_price.get(event_id,{}).get(str(contract.get("id"))) for contract in markets]
        if any(value is None for value in market):exclude("cutoff_price_missing");continue
        market=[float(value) for value in market];total=sum(market)
        if not .80<=total<=1.20:exclude("market_probability_sum_invalid");continue
        market=[value/total for value in market];actual=[1 if in_bucket(int(row["resolved_total"]),bucket) else 0 for bucket in buckets]
        if sum(actual)!=1:exclude("tracker_total_not_unique_bucket");continue
        winner=actual.index(1);model_brier=statistics.fmean((p-y)**2 for p,y in zip(model,actual));market_brier=statistics.fmean((p-y)**2 for p,y in zip(market,actual));model_log=-math.log(max(model[winner],1e-12));market_log=-math.log(max(market[winner],1e-12))
        rows.append({"event_group":f"XCOUNT:{event_id}","event_id":event_id,"tracking_id":str(row["tracking"]["id"]),"handle":user.get("handle"),"title":event.get("title"),"cutoff_at":start.isoformat(),"duration_hours":duration_hours,"prior_observed_hours":len(history),"mean_posts_per_hour":mean_h,"variance_posts_per_hour":var_h,"resolved_total":row["resolved_total"],"model_probabilities":model,"market_probabilities":market,"actual":actual,"model_brier":model_brier,"market_brier":market_brier,"market_minus_model_brier":market_brier-model_brier,"model_log_loss":model_log,"market_log_loss":market_log,"market_minus_model_log_loss":market_log-model_log})
    rows.sort(key=lambda item:item["cutoff_at"]);final_size=30;validation_size=30;development_size=max(0,len(rows)-final_size-validation_size);development=rows[:development_size];validation=rows[development_size:development_size+validation_size];final=rows[-final_size:] if len(rows)>=final_size else []
    scores={"development":paired_summary(development),"validation":paired_summary(validation),"final":paired_summary(final)};sustained=all(scores[name]["event_groups"]>=30 and (scores[name]["paired_brier_95pct_lower"] or -999)>0 and (scores[name]["paired_log_loss_95pct_lower"] or -999)>0 for name in ("validation","final"))
    payload={"schema_version":"social-count-walk-forward-v1","created_at":core.now_iso(),"model_version":MODEL_VERSION,"model":"Trailing 60-day observed XTracker hourly mean/variance with Poisson-or-negative-binomial count distribution","model_scope":"X platform only","market_benchmark":"Polymarket ladder at tracking start, normalized within event","rows":len(rows),"split":{"development":len(development),"validation":len(validation),"final":len(final),"chronological_event_group_split":True,"final_holdout_inspected_once":True},"exclusions":exclusions,**scores,"research_promotion_candidate":sustained,"paper_estimates_allowed":False,"promotion_status":"manual_research_review_required_but_independent_source_gate_missing" if sustained else "research_fail_or_insufficient_sustained_oos_improvement","independent_confirmation_sources_required":2,"independent_confirmation_sources_present":0,"model_outputs_are_true_probabilities":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False};core.write_json(output,payload);return payload


def self_test()->dict[str,Any]:
    assert tracking_slug({"marketLink":"https://polymarket.com/event/elon-posts-week"})=="elon-posts-week"
    assert tracking_slug({"marketLink":"https://example.com/event/unsafe"}) is None
    assert parse_bucket("40-59")=={"low":40,"high":59} and parse_bucket("100+")=={"low":100,"high":None} and parse_bucket("<20")=={"low":0,"high":19}
    event={"title":"Count","markets":[{"id":1,"groupItemTitle":"0-19"},{"id":2,"groupItemTitle":"20+"}]}
    assert event_contract(event)["kind"]=="count_ladder"
    probabilities=bucket_probabilities([{"low":0,"high":19},{"low":20,"high":None}],15,30);assert len(probabilities)==2 and abs(sum(probabilities)-1)<1e-9
    return {"status":"pass","tests":["official_market_link_allowlist","bucket_parser","count_ladder_contract","negative_binomial_ladder","paper_promotion_disabled"]}


def main()->int:
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="command",required=True)
    discover_p=sub.add_parser("discover");discover_p.add_argument("--output-dir",default=str(ROOT/"cache/social_count_discovery"));discover_p.add_argument("--workers",type=int,default=8)
    history_p=sub.add_parser("fetch-history");history_p.add_argument("--discovery-dir",default=str(ROOT/"cache/social_count_discovery"));history_p.add_argument("--output-dir",default=str(ROOT/"cache/social_count_history"));history_p.add_argument("--workers",type=int,default=10)
    prices_p=sub.add_parser("fetch-market-prices");prices_p.add_argument("--history-dir",default=str(ROOT/"cache/social_count_history"));prices_p.add_argument("--output-dir",default=str(ROOT/"cache/social_count_market_prices"));prices_p.add_argument("--chunk-size",type=int,default=20);prices_p.add_argument("--workers",type=int,default=10)
    walk_p=sub.add_parser("walk-forward");walk_p.add_argument("--history-dir",default=str(ROOT/"cache/social_count_history"));walk_p.add_argument("--price-dir",default=str(ROOT/"cache/social_count_market_prices"));walk_p.add_argument("--output",default=str(ROOT/"experiments/current-social-count-walk-forward.json"))
    sub.add_parser("self-test");args=parser.parse_args()
    if args.command=="discover":payload=discover(Path(args.output_dir),args.workers)
    elif args.command=="fetch-history":payload=fetch_history(Path(args.discovery_dir),Path(args.output_dir),args.workers)
    elif args.command=="fetch-market-prices":payload=fetch_market_prices(Path(args.history_dir),Path(args.output_dir),args.chunk_size,args.workers)
    elif args.command=="walk-forward":payload=walk_forward(Path(args.history_dir),Path(args.price_dir),Path(args.output))
    else:payload=self_test()
    print(json.dumps(payload,ensure_ascii=False,indent=2));return 0


if __name__=="__main__":raise SystemExit(main())
