#!/usr/bin/env python3
"""Point-in-time World Cup 1X2 research using archived bookmaker odds."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT=Path(__file__).resolve().parents[1]
GAMMA="https://gamma-api.polymarket.com"
DATA_API="https://data-api.polymarket.com"
CLOB="https://clob.polymarket.com"
ESPN_SITE="https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"
ESPN_CORE="https://sports.core.api.espn.com/v2/sports/soccer/leagues/fifa.world"
SERIES_ID="11433"
MODEL_VERSION="pm-football-dk-devig-close-v1-20260711"


def load(name:str,path:Path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(module);return module


core=load("football_core",ROOT/"scripts/polymarket_alpha.py")
public=load("football_public",ROOT/"scripts/polymarket_public_data.py")


def get_json(url:str,timeout:float=60,retries:int=3)->Any:
    if not url.startswith((GAMMA+"/",DATA_API+"/",CLOB+"/",ESPN_SITE+"/",ESPN_CORE+"/")):raise ValueError("football source host not allowlisted")
    errors=[]
    for attempt in range(retries+1):
        try:
            with urlopen(Request(url,headers={"Accept":"application/json","User-Agent":"polymarket-paper-research/1.0"}),timeout=timeout) as response:return json.loads(response.read().decode())
        except Exception as exc:
            errors.append(f"attempt_{attempt+1}:{type(exc).__name__}:{exc}")
            if attempt<retries:time.sleep(min(2**attempt,3))
    raise RuntimeError(";".join(errors))


def sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(directory:Path,schema:str,sources:list[str],files:list[str],**extra:Any)->dict[str,Any]:
    payload={"schema_version":schema,"created_at":core.now_iso(),"source_urls":sources,"files":[{"path":name,"size_bytes":(directory/name).stat().st_size,"sha256":sha(directory/name)} for name in files],"paper_only":True,"live_orders_enabled":False,"private_api_used":False,**extra};core.write_json(directory/"manifest.json",payload);return payload


def normalize(text:str)->str:
    text=unicodedata.normalize("NFKD",text).encode("ascii","ignore").decode().lower().replace("united states","usa").replace("korea republic","south korea").replace("cote d'ivoire","ivory coast").replace("bosnia-herzegovina","bosnia and herzegovina")
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def is_main_1x2_event(event:dict[str,Any])->bool:
    return not event.get("parentEventId") and len(event.get("markets") or [])==3 and " vs. " in str(event.get("title") or "") and str(event.get("seriesSlug") or "")=="soccer-fifwc"


def espn_teams(event:dict[str,Any])->tuple[str,str]:
    competition=(event.get("competitions") or [{}])[0];home=away=""
    for row in competition.get("competitors") or []:
        name=str((row.get("team") or {}).get("displayName") or "")
        if row.get("homeAway")=="home":home=name
        elif row.get("homeAway")=="away":away=name
    return home,away


def match_score(gamma:dict[str,Any],espn:dict[str,Any])->float:
    start=core.parse_iso(gamma.get("startTime") or gamma.get("endDate"));kickoff=core.parse_iso(espn.get("date"));time_score=0 if not start or not kickoff else max(0,1-abs((start-kickoff).total_seconds())/7200)
    home,away=espn_teams(espn);title=normalize(str(gamma.get("title") or ""));name_score=statistics.fmean([SequenceMatcher(None,normalize(home),title).ratio(),SequenceMatcher(None,normalize(away),title).ratio()])
    return .65*time_score+.35*name_score


def discover(output_dir:Path,workers:int=4)->dict[str,Any]:
    output_dir.mkdir(parents=True,exist_ok=True);schedule_url=f"{ESPN_SITE}/scoreboard?dates=2026&limit=200";schedule=get_json(schedule_url)
    espn_events=schedule.get("events") or [];dates=sorted({str(row.get("date"))[:10] for row in espn_events if row.get("date") and core.parse_iso(row.get("date"))<=datetime.now(timezone.utc)})
    def fetch_date(date:str)->tuple[list[dict[str,Any]],dict[str,Any]]:
        next_date=(datetime.fromisoformat(date)+timedelta(days=1)).date().isoformat();params={"series_id":SERIES_ID,"closed":"true","limit":100,"offset":0,"end_date_min":date+"T00:00:00Z","end_date_max":next_date+"T00:00:00Z","order":"endDate","ascending":"true"};url=f"{GAMMA}/events?{urlencode(params)}";page=get_json(url,timeout=90)
        return [row for row in page if is_main_1x2_event(row)],{"url":url,"status":"ok","rows":len(page),"main_events":sum(is_main_1x2_event(row) for row in page),"terminal_page_proven":len(page)<100}
    with ThreadPoolExecutor(max_workers=max(1,min(workers,6))) as executor:results=list(executor.map(fetch_date,dates))
    gamma_events=[];requests=[]
    for rows,request in results:gamma_events.extend(rows);requests.append(request)
    gamma_events=list({str(row["id"]):row for row in gamma_events}.values());mappings=[];used=set()
    for gamma in sorted(gamma_events,key=lambda row:str(row.get("startTime") or row.get("endDate"))):
        candidates=[row for row in espn_events if str(row.get("id")) not in used and abs(((core.parse_iso(row.get("date")) or datetime.min.replace(tzinfo=timezone.utc))-(core.parse_iso(gamma.get("startTime") or gamma.get("endDate")) or datetime.max.replace(tzinfo=timezone.utc))).total_seconds())<=7200]
        ranked=sorted(((match_score(gamma,row),row) for row in candidates),key=lambda item:item[0],reverse=True)
        if ranked and ranked[0][0]>=.72:
            espn=ranked[0][1];used.add(str(espn["id"]));mappings.append({"gamma_event":gamma,"espn_event":espn,"match_score":ranked[0][0]})
    core.write_json(output_dir/"matches.json",mappings);core.write_json(output_dir/"request-log.json",[{"url":schedule_url,"status":"ok","rows":len(espn_events)},*requests])
    complete=all(row["terminal_page_proven"] for row in requests)
    return manifest(output_dir,"football-world-cup-discovery-v1",[schedule_url,f"{GAMMA}/events"],["matches.json","request-log.json"],espn_events=len(espn_events),completed_date_slices=len(dates),gamma_main_events=len(gamma_events),mapped_matches=len(mappings),terminal_slices_proven=complete,data_status="ok" if complete and len(mappings)>=90 else "degraded")


def american_probability(odds:Any)->float|None:
    try:value=float(odds)
    except (TypeError,ValueError):return None
    if value==0:return None
    return (-value)/(-value+100) if value<0 else 100/(value+100)


def parse_provider_odds(payload:dict[str,Any])->dict[str,Any]|None:
    items=payload.get("items") or [];item=next((row for row in items if str((row.get("provider") or {}).get("id"))=="100"),None)
    if not item:return None
    raw={"home":american_probability((item.get("homeTeamOdds") or {}).get("moneyLine")),"draw":american_probability((item.get("drawOdds") or {}).get("moneyLine")),"away":american_probability((item.get("awayTeamOdds") or {}).get("moneyLine"))}
    if any(value is None for value in raw.values()):return None
    total=sum(raw.values());return {"provider":"DraftKings","provider_id":"100","raw_implied":raw,"devig":{key:value/total for key,value in raw.items()},"overround":total-1,"details":item.get("details")}


def fetch_odds(discovery_dir:Path,output_dir:Path,workers:int=8)->dict[str,Any]:
    matches=core.read_json(discovery_dir/"matches.json");output_dir.mkdir(parents=True,exist_ok=True)
    def fetch(row:dict[str,Any])->dict[str,Any]:
        event_id=str(row["espn_event"]["id"]);url=f"{ESPN_CORE}/events/{event_id}/competitions/{event_id}/odds?lang=en&region=us";payload=get_json(url);return {"espn_event_id":event_id,"url":url,"odds":parse_provider_odds(payload),"provider_count":payload.get("count",0)}
    with ThreadPoolExecutor(max_workers=max(1,min(workers,10))) as executor:rows=list(executor.map(fetch,matches))
    core.write_json(output_dir/"odds.json",rows);core.write_json(output_dir/"request-log.json",[{"url":row["url"],"status":"ok","provider_count":row["provider_count"],"odds_parsed":row["odds"] is not None} for row in rows])
    parsed=sum(row["odds"] is not None for row in rows)
    return manifest(output_dir,"football-espn-odds-v1",[ESPN_CORE],["odds.json","request-log.json"],matches=len(matches),odds_parsed=parsed,failed_request_count=0,data_status="ok" if parsed>=90 else "degraded")


def winner_index(event:dict[str,Any])->int|None:
    winners=[]
    for index,market in enumerate(event.get("markets") or []):
        try:prices=json.loads(market.get("outcomePrices")) if isinstance(market.get("outcomePrices"),str) else market.get("outcomePrices")
        except Exception:prices=[]
        if prices and float(prices[0])>=.99:winners.append(index)
    return winners[0] if len(winners)==1 else None


def market_roles(event:dict[str,Any],espn:dict[str,Any])->list[str]|None:
    home,away=espn_teams(espn);roles=[]
    for market in event.get("markets") or []:
        text=normalize(str(market.get("groupItemTitle") or market.get("question") or ""))
        if "draw" in text:roles.append("draw")
        elif SequenceMatcher(None,normalize(home),text).ratio()>=SequenceMatcher(None,normalize(away),text).ratio():roles.append("home")
        else:roles.append("away")
    return roles if sorted(roles)==["away","draw","home"] else None


def latest_trade(condition:str,cutoff:int)->tuple[float|None,list[dict[str,Any]]]:
    requests=[];offset=0;seen=set()
    while True:
        url=f"{DATA_API}/trades?{urlencode({'market':condition,'limit':1000,'offset':offset})}";page=get_json(url,timeout=30)
        if not isinstance(page,list):raise ValueError("football trade page invalid")
        timestamps=[int(row["timestamp"]) for row in page if row.get("timestamp") is not None]
        if timestamps!=sorted(timestamps,reverse=True):raise ValueError("football trade page not newest-first")
        fingerprint=hashlib.sha256(json.dumps([(row.get('timestamp'),row.get('price'),row.get('outcome')) for row in page],sort_keys=True).encode()).hexdigest()
        if fingerprint in seen:raise ValueError("football repeated trade page")
        seen.add(fingerprint);requests.append({"url":url,"status":"ok","rows":len(page)})
        eligible=[row for row in page if row.get("timestamp") is not None and int(row["timestamp"])<=cutoff]
        if eligible:
            trade=eligible[0];price=float(trade["price"]);yes=price if str(trade.get("outcome")).lower()=="yes" else 1-price if str(trade.get("outcome")).lower()=="no" else None
            return yes,requests
        if len(page)<1000:return None,requests
        offset+=len(page)
        if offset>=10000:raise ValueError("football trade page budget exhausted")


def explicit_window_price(token:str,cutoff:int)->tuple[float|None,dict[str,Any]]:
    params={"market":token,"startTs":cutoff-48*3600,"endTs":cutoff,"fidelity":10};url=f"{CLOB}/prices-history?{urlencode(params)}";payload=get_json(url,timeout=45)
    history=payload.get("history") or [];eligible=[row for row in history if row.get("p") is not None and int(row.get("t",0))<=cutoff]
    value=float(max(eligible,key=lambda row:int(row["t"]))["p"]) if eligible else None
    return value,{"url":url,"status":"ok","points":len(history),"eligible_points":len(eligible)}


def fetch_market_prices(discovery_dir:Path,output_dir:Path,workers:int=8)->dict[str,Any]:
    matches=core.read_json(discovery_dir/"matches.json");output_dir.mkdir(parents=True,exist_ok=True)
    tasks=[]
    for row in matches:
        kickoff=core.parse_iso(row["espn_event"].get("date"));
        if not kickoff:continue
        cutoff=int((kickoff-timedelta(hours=1)).timestamp())
        for market in row["gamma_event"].get("markets") or []:
            token=next((token for outcome,token in public.token_map(market).items() if outcome.lower()=="yes"),None)
            tasks.append((str(row["gamma_event"]["id"]),str(market.get("conditionId")),str(token or ""),cutoff))
    def fetch(task:tuple[str,str,str,int])->dict[str,Any]:
        event,condition,token,cutoff=task
        if not token:return {"gamma_event_id":event,"condition_id":condition,"cutoff":cutoff,"yes_price":None,"requests":[{"status":"failed","error":"yes_token_missing"}]}
        price,request=explicit_window_price(token,cutoff);return {"gamma_event_id":event,"condition_id":condition,"cutoff":cutoff,"yes_price":price,"requests":[request]}
    with ThreadPoolExecutor(max_workers=max(1,min(workers,10))) as executor:rows=list(executor.map(fetch,tasks))
    flat_requests=[{"gamma_event_id":row["gamma_event_id"],"condition_id":row["condition_id"],**request} for row in rows for request in row["requests"]]
    core.write_json(output_dir/"prices.json",rows);core.write_json(output_dir/"request-log.json",flat_requests);nonempty=sum(row["yes_price"] is not None for row in rows)
    return manifest(output_dir,"football-polymarket-cutoff-prices-v1",[f"{CLOB}/prices-history"],["prices.json","request-log.json"],contracts=len(rows),nonempty_prices=nonempty,failed_request_count=sum(request.get("status")=="failed" for row in rows for request in row["requests"]),cutoff="kickoff_minus_1h",history_window_hours=48,fidelity_minutes=10,data_status="ok" if nonempty>=270 and not any(request.get("status")=="failed" for row in rows for request in row["requests"]) else "degraded")


def paired_summary(rows:list[dict[str,Any]])->dict[str,Any]:
    diffs=[row["market_minus_model_brier"] for row in rows];logs=[row["market_minus_model_log_loss"] for row in rows]
    def calc(values:list[float])->tuple[float|None,float|None]:
        if not values:return None,None
        mean=statistics.fmean(values);lower=mean-1.96*statistics.stdev(values)/math.sqrt(len(values)) if len(values)>=2 else None;return mean,lower
    mean,lower=calc(diffs);log_mean,log_lower=calc(logs);return {"event_groups":len(rows),"mean_market_minus_model_brier":mean,"paired_brier_95pct_lower":lower,"mean_market_minus_model_log_loss":log_mean,"paired_log_loss_95pct_lower":log_lower,"group_rows":rows}


def walk_forward(discovery_dir:Path,odds_dir:Path,price_dir:Path,output:Path)->dict[str,Any]:
    for directory in (discovery_dir,odds_dir,price_dir):
        if core.read_json(directory/"manifest.json").get("data_status")!="ok":raise ValueError(f"degraded football input:{directory}")
    matches=core.read_json(discovery_dir/"matches.json");odds={row["espn_event_id"]:row["odds"] for row in core.read_json(odds_dir/"odds.json") if row.get("odds")};prices={(row["gamma_event_id"],row["condition_id"]):row["yes_price"] for row in core.read_json(price_dir/"prices.json")};rows=[];exclusions={}
    def exclude(reason:str):exclusions.__setitem__(reason,exclusions.get(reason,0)+1)
    for mapping in matches:
        gamma=mapping["gamma_event"];espn=mapping["espn_event"];event_id=str(gamma["id"]);winner=winner_index(gamma);roles=market_roles(gamma,espn);external=odds.get(str(espn["id"]));markets=gamma.get("markets") or []
        if winner is None or roles is None or not external:exclude("resolution_roles_or_odds_missing");continue
        market=[prices.get((event_id,str(contract.get("conditionId")))) for contract in markets]
        if any(value is None for value in market):exclude("cutoff_price_missing");continue
        total=sum(float(value) for value in market)
        if not .80<=total<=1.20:exclude("market_probability_sum_invalid");continue
        market=[float(value)/total for value in market];model=[external["devig"][role] for role in roles];actual=[1 if index==winner else 0 for index in range(3)]
        model_brier=statistics.fmean((p-y)**2 for p,y in zip(model,actual));market_brier=statistics.fmean((p-y)**2 for p,y in zip(market,actual));model_log=-math.log(max(model[winner],1e-12));market_log=-math.log(max(market[winner],1e-12))
        rows.append({"event_group":f"FIFAWC:{event_id}","gamma_event_id":event_id,"espn_event_id":str(espn["id"]),"kickoff":espn["date"],"title":gamma["title"],"roles":roles,"winner_index":winner,"model_probabilities":model,"market_probabilities":market,"model_brier":model_brier,"market_brier":market_brier,"market_minus_model_brier":market_brier-model_brier,"model_log_loss":model_log,"market_log_loss":market_log,"market_minus_model_log_loss":market_log-model_log,"overround":external["overround"]})
    rows.sort(key=lambda row:row["kickoff"]);n=len(rows);final_size=30;validation_size=30;development_size=n-final_size-validation_size
    development=rows[:max(0,development_size)];validation=rows[max(0,development_size):max(0,development_size)+validation_size];final=rows[-final_size:] if n>=final_size else []
    scores={"development":paired_summary(development),"validation":paired_summary(validation),"final":paired_summary(final)};sustained=all(scores[name]["event_groups"]>=30 and (scores[name]["paired_brier_95pct_lower"] or -999)>0 and (scores[name]["paired_log_loss_95pct_lower"] or -999)>0 for name in ("validation","final"))
    payload={"schema_version":"football-world-cup-walk-forward-v1","created_at":core.now_iso(),"model_version":MODEL_VERSION,"model":"DraftKings closing 1X2 implied probabilities, multiplicatively de-vigged","market":"90-minute 1X2 only","rows":n,"split":{"development":len(development),"validation":len(validation),"final":len(final),"chronological_event_group_split":True,"final_holdout_inspected_once":True},"exclusions":exclusions,**scores,"research_promotion_candidate":sustained,"paper_estimates_allowed":False,"promotion_status":"manual_research_review_required" if sustained else "research_fail_or_insufficient_sustained_oos_improvement","source_independence_gate_met":False,"tactical_lineup_gate_met":False,"model_outputs_are_true_probabilities":False,"paper_only":True,"live_orders_enabled":False,"private_api_used":False};core.write_json(output,payload);return payload


def self_test()->dict[str,Any]:
    assert round(american_probability(-150),6)==.6 and round(american_probability(200),6)==round(1/3,6)
    sample={"items":[{"provider":{"id":"100"},"homeTeamOdds":{"moneyLine":-110},"awayTeamOdds":{"moneyLine":250},"drawOdds":{"moneyLine":220}}]};parsed=parse_provider_odds(sample);assert parsed and abs(sum(parsed["devig"].values())-1)<1e-9
    return {"status":"pass","tests":["american_odds","multiplicative_devig","paper_promotion_disabled"]}


def main()->int:
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="command",required=True)
    discover_p=sub.add_parser("discover");discover_p.add_argument("--output-dir",default=str(ROOT/"cache/football_world_cup_discovery"));discover_p.add_argument("--workers",type=int,default=4)
    odds_p=sub.add_parser("fetch-odds");odds_p.add_argument("--discovery-dir",default=str(ROOT/"cache/football_world_cup_discovery"));odds_p.add_argument("--output-dir",default=str(ROOT/"cache/football_world_cup_odds"));odds_p.add_argument("--workers",type=int,default=8)
    prices_p=sub.add_parser("fetch-market-prices");prices_p.add_argument("--discovery-dir",default=str(ROOT/"cache/football_world_cup_discovery"));prices_p.add_argument("--output-dir",default=str(ROOT/"cache/football_world_cup_market_prices"));prices_p.add_argument("--workers",type=int,default=8)
    walk=sub.add_parser("walk-forward");walk.add_argument("--discovery-dir",default=str(ROOT/"cache/football_world_cup_discovery"));walk.add_argument("--odds-dir",default=str(ROOT/"cache/football_world_cup_odds"));walk.add_argument("--price-dir",default=str(ROOT/"cache/football_world_cup_market_prices"));walk.add_argument("--output",default=str(ROOT/"experiments/current-football-walk-forward.json"))
    sub.add_parser("self-test");args=parser.parse_args()
    if args.command=="discover":payload=discover(Path(args.output_dir),args.workers)
    elif args.command=="fetch-odds":payload=fetch_odds(Path(args.discovery_dir),Path(args.output_dir),args.workers)
    elif args.command=="fetch-market-prices":payload=fetch_market_prices(Path(args.discovery_dir),Path(args.output_dir),args.workers)
    elif args.command=="walk-forward":payload=walk_forward(Path(args.discovery_dir),Path(args.odds_dir),Path(args.price_dir),Path(args.output))
    else:payload=self_test()
    print(json.dumps(payload,ensure_ascii=False,indent=2));return 0


if __name__=="__main__":raise SystemExit(main())
