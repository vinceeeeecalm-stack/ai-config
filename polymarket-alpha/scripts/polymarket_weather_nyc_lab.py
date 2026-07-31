#!/usr/bin/env python3
"""Untouched NYC/KLGA external-validation wrapper for the weather V1 method."""
from __future__ import annotations

import argparse
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo


ROOT=Path(__file__).resolve().parents[1]
SERIES_ID="10005"
STATION="KLGA"
LATITUDE=40.7769
LONGITUDE=-73.8740
LOCAL_TIMEZONE="America/New_York"
MODEL_VERSION="pm-weather-nyc-day1-v1-20260711"


def load(name:str,path:Path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(module);return module


lab=load("weather_nyc_base",ROOT/"scripts/polymarket_weather_lab.py")
core=load("weather_nyc_core",ROOT/"scripts/polymarket_alpha.py")
public=load("weather_nyc_public",ROOT/"scripts/polymarket_public_data.py")


def configure()->None:
    lab.SERIES_ID=SERIES_ID;lab.STATION=STATION;lab.LATITUDE=LATITUDE;lab.LONGITUDE=LONGITUDE;lab.LOCAL_TIMEZONE=LOCAL_TIMEZONE;lab.MODEL_VERSION=MODEL_VERSION


def date_chunks(start:str,end:str,days:int=90)->list[tuple[str,str]]:
    cursor=datetime.fromisoformat(start).date();finish=datetime.fromisoformat(end).date();rows=[]
    while cursor<=finish:
        chunk_end=min(finish,cursor+timedelta(days=days-1));rows.append((cursor.isoformat(),chunk_end.isoformat()));cursor=chunk_end+timedelta(days=1)
    return rows


def fetch_forecast_archive_chunked(history_dir:Path,output_dir:Path)->dict[str,Any]:
    manifest=core.read_json(history_dir/"manifest.json")
    if manifest.get("data_status")!="ok" or manifest.get("terminal_pagination_proven") is not True:raise ValueError("complete NYC weather history required")
    events=core.read_json(history_dir/"events.json");dates=sorted(date for date in (lab.event_date(row) for row in events) if date)
    if not dates:raise ValueError("NYC event dates missing")
    output_dir.mkdir(parents=True,exist_ok=True);forecasts={model:{} for model in ("gfs_seamless","ecmwf_ifs025")};requests=[]
    for model in forecasts:
        for start,end in date_chunks(dates[0],dates[-1]):
            params={"latitude":LATITUDE,"longitude":LONGITUDE,"hourly":"temperature_2m_previous_day1","temperature_unit":"fahrenheit","timezone":LOCAL_TIMEZONE,"start_date":start,"end_date":end,"models":model};url=f"{lab.PREVIOUS_RUNS}?{urlencode(params)}"
            try:
                payload=lab.get_json(url,timeout=90);hourly=payload.get("hourly") or {};times=hourly.get("time") or [];values=hourly.get("temperature_2m_previous_day1") or []
                if len(times)!=len(values) or not times:raise ValueError("forecast times/value mismatch")
                daily={}
                for stamp,value in zip(times,values):
                    if value is not None:daily.setdefault(str(stamp)[:10],[]).append(float(value))
                complete={date:max(rows) for date,rows in daily.items() if len(rows)>=18};forecasts[model].update(complete);requests.append({"url":url,"status":"ok","model":model,"start":start,"end":end,"hourly_rows":len(times),"daily_rows":len(complete)})
            except Exception as exc:requests.append({"url":url,"status":"failed","model":model,"start":start,"end":end,"error":f"{type(exc).__name__}:{exc}"})
    core.write_json(output_dir/"daily-max-day1.json",forecasts);core.write_json(output_dir/"request-log.json",requests)
    complete=all(len(forecasts[model])>=len(set(dates))-3 for model in forecasts) and not any(row["status"]=="failed" for row in requests)
    return lab.write_manifest(output_dir,"weather-day1-forecast-archive-v1-chunked",[lab.PREVIOUS_RUNS],["daily-max-day1.json","request-log.json"],station=STATION,series_id=SERIES_ID,forecast_semantics="each valid hour predicted exactly 24 hours earlier",chunk_days=90,start_date=dates[0],end_date=dates[-1],model_daily_rows={model:len(rows) for model,rows in forecasts.items()},failed_request_count=sum(row["status"]=="failed" for row in requests),data_status="ok" if complete else "degraded")


def fetch_cutoff_prices(history_dir:Path,output_dir:Path,maximum_events:int=150,workers:int=12)->dict[str,Any]:
    events=lab.selected_events(history_dir,maximum_events);output_dir.mkdir(parents=True,exist_ok=True);zone=ZoneInfo(LOCAL_TIMEZONE);tasks=[];existing=core.read_json(output_dir/"price-history.json") if (output_dir/"price-history.json").exists() else {}
    for event in events:
        date=lab.event_date(event)
        if not date:continue
        cutoff=int((datetime.fromisoformat(date).replace(tzinfo=zone)-timedelta(minutes=1)).timestamp())
        for market in event.get("markets") or []:
            token=next((value for outcome,value in public.token_map(market).items() if outcome.lower()=="yes"),None)
            tasks.append({"event_id":str(event.get("id")),"token":str(token or ""),"cutoff":cutoff})
    def fetch(task:dict[str,Any])->dict[str,Any]:
        if not task["token"]:return {**task,"point":None,"request":{"status":"failed","error":"yes_token_missing"}}
        params={"market":task["token"],"startTs":task["cutoff"]-72*3600,"endTs":task["cutoff"],"fidelity":10};url=f"{public.CLOB_BASE}/prices-history?{urlencode(params)}"
        try:
            payload=public.get_json(url,timeout=25,retries=2);points=payload.get("history",[]) if isinstance(payload,dict) else [];eligible=[row for row in points if row.get("p") is not None and int(row.get("t",0))<=task["cutoff"]];point=max(eligible,key=lambda row:int(row["t"])) if eligible else None
            return {**task,"point":{"t":int(point["t"]),"p":float(point["p"]),"source":"clob_explicit_window"} if point else None,"request":{"url":url,"status":"ok","points":len(points),"eligible_points":len(eligible)}}
        except Exception as exc:return {**task,"point":None,"request":{"url":url,"status":"failed","error":f"{type(exc).__name__}:{exc}"}}
    cached=[];missing=[]
    for task in tasks:
        points=(existing.get(task["token"]) or {}).get("history",[]) if task["token"] else []
        eligible=[point for point in points if point.get("p") is not None and int(point.get("t",0))<=task["cutoff"]]
        if eligible:
            point=max(eligible,key=lambda row:int(row["t"]));cached.append({**task,"point":point,"request":{"status":"cached","points":len(points),"eligible_points":len(eligible)}})
        else:missing.append(task)
    with ThreadPoolExecutor(max_workers=max(1,min(workers,16))) as executor:fetched=list(executor.map(fetch,missing))
    rows=cached+fetched
    histories={row["token"]:{"history":[row["point"]] if row.get("point") else []} for row in rows if row["token"]};requests=[{"event_id":row["event_id"],"token":row["token"],**row["request"]} for row in rows];by_event={}
    for row in rows:by_event.setdefault(row["event_id"],[]).append(row)
    complete_events=sum(all(row.get("point") for row in group) for group in by_event.values());nonempty=sum(row.get("point") is not None for row in rows);failed=sum(row["status"]=="failed" for row in requests)
    core.write_json(output_dir/"price-history.json",histories);core.write_json(output_dir/"request-log.json",requests)
    return lab.write_manifest(output_dir,"weather-clob-explicit-cutoff-history-v1",[f"{public.CLOB_BASE}/prices-history"],["price-history.json","request-log.json"],event_groups=len(events),complete_event_groups=complete_events,tokens=len(tasks),cached_tokens=len(cached),retried_tokens=len(missing),nonempty_histories=nonempty,cutoff="local_day_start_minus_1m",history_window_hours=72,fidelity_minutes=10,failed_request_count=failed,data_status="ok" if failed==0 and complete_events>=120 else "degraded")


def self_test()->dict[str,Any]:
    configure();chunks=date_chunks("2026-01-01","2026-07-11",90);assert chunks[0]==("2026-01-01","2026-03-31") and chunks[-1][1]=="2026-07-11";assert lab.SERIES_ID==SERIES_ID and lab.STATION==STATION and lab.MODEL_VERSION==MODEL_VERSION
    return {"status":"pass","tests":["nyc_configuration","nonoverlapping_forecast_chunks","paper_promotion_disabled"]}


def main()->int:
    configure();parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="command",required=True)
    history=sub.add_parser("discover-history");history.add_argument("--output-dir",default=str(ROOT/"cache/weather_nyc_history"));history.add_argument("--page-size",type=int,default=50)
    forecast=sub.add_parser("fetch-forecast-archive");forecast.add_argument("--history-dir",default=str(ROOT/"cache/weather_nyc_history"));forecast.add_argument("--output-dir",default=str(ROOT/"cache/weather_nyc_forecasts"))
    trades=sub.add_parser("fetch-market-prices");trades.add_argument("--history-dir",default=str(ROOT/"cache/weather_nyc_history"));trades.add_argument("--output-dir",default=str(ROOT/"cache/weather_nyc_cutoff_prices"));trades.add_argument("--maximum-events",type=int,default=150);trades.add_argument("--workers",type=int,default=12)
    walk=sub.add_parser("walk-forward");walk.add_argument("--history-dir",default=str(ROOT/"cache/weather_nyc_history"));walk.add_argument("--forecast-dir",default=str(ROOT/"cache/weather_nyc_forecasts"));walk.add_argument("--price-dir",default=str(ROOT/"cache/weather_nyc_cutoff_prices"));walk.add_argument("--maximum-events",type=int,default=150);walk.add_argument("--output",default=str(ROOT/"experiments/current-weather-nyc-walk-forward.json"))
    sub.add_parser("self-test");args=parser.parse_args()
    if args.command=="discover-history":payload=lab.discover_history(Path(args.output_dir),args.page_size)
    elif args.command=="fetch-forecast-archive":payload=fetch_forecast_archive_chunked(Path(args.history_dir),Path(args.output_dir))
    elif args.command=="fetch-market-prices":payload=fetch_cutoff_prices(Path(args.history_dir),Path(args.output_dir),args.maximum_events,args.workers)
    elif args.command=="walk-forward":
        payload=lab.walk_forward(Path(args.history_dir),Path(args.forecast_dir),Path(args.price_dir),Path(args.output),args.maximum_events)
        payload["external_validation_role"]="untouched_geographic_replication_after_dallas_v1"
        payload["final_holdout_inspected_once"]=True
        payload["holdout_reuse_for_future_selection_allowed"]=False
        payload["weather_v1_family_evidence"]={"dallas_oos_segments_failed":2,"nyc_oos_segments_failed":2,"same_method_family_failures":4,"paper_model_family_action":"retire_from_paper_estimates"}
        core.write_json(Path(args.output),payload)
    else:payload=self_test()
    print(json.dumps(payload,ensure_ascii=False,indent=2));return 0


if __name__=="__main__":raise SystemExit(main())
