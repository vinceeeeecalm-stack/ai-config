#!/usr/bin/env python3
"""Public-only FDV taxonomy and terminal settled-history rule gate."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, re, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[1];GAMMA="https://gamma-api.polymarket.com";FDV_TAG_ID=139;PAGE_LIMIT=20
def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);assert s.loader;s.loader.exec_module(m);return m
core=load("fdv_core",ROOT/"scripts/polymarket_alpha.py");family=load("fdv_family",ROOT/"scripts/polymarket_market_family_audit.py");public=load("fdv_public",ROOT/"scripts/polymarket_public_data.py")
def sha256(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def event_type(text):
    low=text.lower()
    if re.search(r"one day after launch|1 day after launch",low):return "launch_plus_one_day_fdv"
    if re.search(r"hit .*fdv|fdv .* before|fdv .* by ",low):return "fdv_barrier_before_deadline"
    if re.search(r"market cap.*one day after launch|fdv one day after launch",low):return "launch_fdv_bracket"
    return "other_fdv"

def price_source(text):
    low=text.lower()
    for name,pat in [("CoinGecko",r"coingecko"),("CoinMarketCap",r"coinmarketcap"),("Dexscreener",r"dexscreener"),("Binance",r"binance"),("Coinbase",r"coinbase")]:
        if re.search(pat,low):return name
    if "most liquid price source" in low:return "most_liquid_source_unspecified"
    return "price_source_unspecified"

def supply_definition(text):
    low=text.lower()
    if "total token supply" in low:return "total_token_supply"
    if "maximum token supply" in low or "max token supply" in low:return "maximum_token_supply"
    if re.search(r"fully diluted valuation|fdv",low) and re.search(r"reported by|according to",low):return "provider_reported_fdv"
    return "supply_definition_unspecified"

def timing_definition(text):
    low=text.lower()
    if re.search(r"4:00 pm et on the calendar day following launch",low):return "next_calendar_day_1600_et"
    if re.search(r"24 hours after launch",low):return "exact_24h_after_launch"
    if re.search(r"one day after launch|1 day after launch",low):return "one_day_unspecified_clock"
    if re.search(r"before| by | in (?:january|february|march|april|may|june|july|august|september|october|november|december)",low):return "deadline_barrier"
    return "timing_unspecified"

def diagnostics(text):
    source=price_source(text);supply=supply_definition(text);timing=timing_definition(text)
    named=source not in {"most_liquid_source_unspecified","price_source_unspecified"}
    return {"price_source":source,"named_price_source":named,"supply_definition":supply,"timing_definition":timing,
            "launch_definition_present":bool(re.search(r"actively, publicly transferable and tradable|token launch",text,re.I)),
            "high_rule_ambiguity":not named or supply=="supply_definition_unspecified" or timing in {"timing_unspecified","one_day_unspecified_clock"}}

def live_audit(markets,enrichment,as_of):
    rows=[]
    for m in markets:
        if not family.belongs(m,family.FAMILIES["fdv"]):continue
        raw=m.get("_sampling_raw") or {};ev=(m.get("events") or [{}])[0];q=str(m.get("question") or raw.get("question") or "");desc=str(ev.get("description") or m.get("description") or raw.get("description") or "");text=f"{q}\n{desc}";end=family.end_time(m)
        rows.append({"market_id":str(m.get("id") or m.get("conditionId") or ""),"event_id":family.event_identity(m,enrichment),"question":q,"event_type":event_type(text),"end_time":end.isoformat() if end else None,"expired_but_live":bool(end and end<as_of),**diagnostics(text)})
    groups=[]
    for key in sorted({(r["event_type"],r["price_source"],r["supply_definition"],r["timing_definition"]) for r in rows}):
        ss=[r for r in rows if (r["event_type"],r["price_source"],r["supply_definition"],r["timing_definition"])==key];events={r["event_id"] for r in ss if r["event_id"]};clean=[r for r in ss if not r["high_rule_ambiguity"] and not r["expired_but_live"]]
        groups.append({"event_type":key[0],"price_source":key[1],"supply_definition":key[2],"timing_definition":key[3],"contract_count":len(ss),"independent_event_count":len(events),"clean_contract_count":len(clean),"expired_but_live_count":sum(r["expired_but_live"] for r in ss),"phase_zero_candidate":len(events)>=30 and len(clean)==len(ss)})
    groups.sort(key=lambda r:(r["phase_zero_candidate"],r["independent_event_count"],r["clean_contract_count"]),reverse=True);c=[r for r in groups if r["phase_zero_candidate"]]
    return {"schema_version":"polymarket-fdv-live-taxonomy-v1","created_at":core.now_iso(),"inventory_as_of":as_of.isoformat(),"family":"fdv","contract_count":len(rows),"independent_event_count":len({r["event_id"] for r in rows if r["event_id"]}),"near_term_30d_event_count":len({r["event_id"] for r in rows if r["event_id"] and r["end_time"] and 0<=(core.parse_iso(r["end_time"])-as_of).total_seconds()<=30*86400}),"groups":groups,"selected_group_for_history_research":c[0] if c else None,"model_status":"blocked_pending_terminal_history","contract_rows":rows,"paper_entry_eligible":False,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"live_orders_enabled":False,"private_api_used":False}

def load_events(d):
    x={}
    for p in sorted((d/"pages").glob("page-*.json")):
        for e in core.read_json(p).get("events") or []:x[str(e.get("id"))]=e
    return sorted(x.values(),key=lambda r:int(r.get("id") or 0))
def settled_row(e):
    terminal=[]
    for m in e.get("markets") or []:
        r=public.infer_resolution(m)
        if r.get("winning_outcome") is not None:terminal.append({"market_id":str(m.get("id") or ""),"winning_outcome":r["winning_outcome"]})
    if not terminal:return None
    text=f"{e.get('title') or ''}\n{e.get('description') or ''}"
    return {"event_id":str(e.get("id") or ""),"title":e.get("title"),"event_type":event_type(text),"end_time":e.get("endDate"),"terminal_market_count":len(terminal),"terminal_markets":terminal,**diagnostics(text)}
def discover_history(d,max_pages,wall):
    (d/"pages").mkdir(parents=True,exist_ok=True);contract={"endpoint":f"{GAMMA}/events/keyset","closed":True,"tag_id":FDV_TAG_ID,"limit":PAGE_LIMIT};sp=d/"state.json";lp=d/"request-log.json";state=core.read_json(sp) if sp.exists() else {"schema_version":"polymarket-fdv-history-state-v1","contract":contract,"next_cursor":None,"terminal_cursor_proven":False,"pages_fetched":0}
    if state.get("contract")!=contract:raise ValueError("fdv history cache contract mismatch")
    reqs=core.read_json(lp) if lp.exists() else [];core.write_json(sp,state);core.write_json(lp,reqs);start=time.monotonic();n=0
    while not state["terminal_cursor_proven"] and n<max_pages and time.monotonic()-start<wall:
        p={"closed":"true","tag_id":FDV_TAG_ID,"limit":PAGE_LIMIT};
        if state.get("next_cursor"):p["after_cursor"]=state["next_cursor"]
        u=f"{GAMMA}/events/keyset?{urlencode(p)}"
        try:
            z=public.get_json(u,timeout=25,retries=2);events=z.get("events")
            if not isinstance(events,list):raise ValueError("keyset response missing events")
            num=int(state["pages_fetched"])+1;cursor=z.get("next_cursor");core.write_json(d/"pages"/f"page-{num:04d}.json",{"page_number":num,"request_url":u,"events":events,"next_cursor":cursor});reqs.append({"page_number":num,"url":u,"status":"ok","rows":len(events),"terminal":not bool(cursor),"created_at":core.now_iso()});state.update({"pages_fetched":num,"next_cursor":cursor,"terminal_cursor_proven":not bool(cursor),"updated_at":core.now_iso()});core.write_json(lp,reqs);core.write_json(sp,state);n+=1
        except Exception as exc:reqs.append({"url":u,"status":"failed","error":f"{type(exc).__name__}:{exc}","created_at":core.now_iso()});core.write_json(lp,reqs);break
    events=load_events(d);rows=[r for e in events if (r:=settled_row(e))];core.write_json(d/"settled-fdv-events.json",rows);groups=[]
    for key in sorted({(r["event_type"],r["price_source"],r["supply_definition"],r["timing_definition"]) for r in rows}):
        ss=[r for r in rows if (r["event_type"],r["price_source"],r["supply_definition"],r["timing_definition"])==key];strict=[r for r in ss if not r["high_rule_ambiguity"]];groups.append({"event_type":key[0],"price_source":key[1],"supply_definition":key[2],"timing_definition":key[3],"settled_event_count":len(ss),"strict_event_count":len(strict),"model_sample_gate_met":len(strict)>=150})
    groups.sort(key=lambda r:(r["model_sample_gate_met"],r["strict_event_count"]),reverse=True);rel=["state.json","request-log.json","settled-fdv-events.json"]+[str(p.relative_to(d)) for p in sorted((d/"pages").glob("page-*.json"))]
    out={"schema_version":"polymarket-fdv-history-manifest-v1","created_at":core.now_iso(),"official_source":f"{GAMMA}/events/keyset","fdv_tag_id":FDV_TAG_ID,"pages_fetched":state["pages_fetched"],"terminal_cursor_proven":state["terminal_cursor_proven"],"events_fetched":len(events),"settled_fdv_events":len(rows),"homogeneous_groups":groups,"minimum_total_for_frozen_60_20_20_split":150,"selected_group_for_model_research":next((r for r in groups if r["model_sample_gate_met"]),None),"model_sample_gate_met":any(r["model_sample_gate_met"] for r in groups),"data_status":"ok" if state["terminal_cursor_proven"] else "degraded","files":[{"path":x,"bytes":(d/x).stat().st_size,"sha256":sha256(d/x)} for x in rel],"research_only":True,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"live_orders_enabled":False,"private_api_used":False};core.write_json(d/"manifest.json",out);return out

def markdown(live,h):
    a=["# Polymarket FDV Data Gate","",f"- Live: {live['contract_count']} contracts / {live['independent_event_count']} launch events",f"- <=30d events: {live['near_term_30d_event_count']}",f"- Live candidate: `{live['selected_group_for_history_research']}`","- No model probability or paper permission is emitted.","","| Type | Price source | Supply | Timing | Contracts | Events | Clean | Candidate |","|---|---|---|---|---:|---:|---:|---|"]
    for r in live["groups"]:a.append(f"| {r['event_type']} | {r['price_source']} | {r['supply_definition']} | {r['timing_definition']} | {r['contract_count']} | {r['independent_event_count']} | {r['clean_contract_count']} | {str(r['phase_zero_candidate']).lower()} |")
    if h:a.extend(["",f"Terminal history: {h['events_fetched']} events; strict model sample gate `{str(h['model_sample_gate_met']).lower()}`.",""])
    return "\n".join(a)
def self_test():
    t='FDV one day after launch. total token supply multiplied by price. 4:00 PM ET on the calendar day following launch. CoinGecko.';assert event_type(t)=="launch_plus_one_day_fdv";assert diagnostics(t)["high_rule_ambiguity"] is False;assert diagnostics(t.replace('CoinGecko','most liquid price source available'))["high_rule_ambiguity"] is True;return {"status":"pass","tests":["launch_taxonomy","named_source_gate","liquid_source_ambiguity","supply_and_timing"]}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--self-test",action="store_true");p.add_argument("--discover-history",action="store_true");p.add_argument("--max-pages",type=int,default=100);p.add_argument("--wall-clock-seconds",type=float,default=240);p.add_argument("--markets",default=str(ROOT/"cache/current_validation_snapshot/markets.json"));p.add_argument("--snapshot-manifest",default=str(ROOT/"cache/current_validation_snapshot/snapshot-manifest.json"));p.add_argument("--identity-enrichment",default=str(ROOT/"cache/current_family_identity_enrichment.json"));p.add_argument("--history-dir",default=str(ROOT/"cache/current_fdv_history"));p.add_argument("--output",default=str(ROOT/"experiments/current-fdv-taxonomy-audit.json"));p.add_argument("--report",default=str(ROOT/"reports/CURRENT_FDV_DATA_GATE.md"));a=p.parse_args()
    if a.self_test:o=self_test()
    else:
        sm=core.read_json(a.snapshot_manifest);en=core.read_json(a.identity_enrichment)
        if sm.get("terminal_cursor_proven") is not True:raise ValueError("snapshot has no terminal cursor proof")
        live=live_audit(core.read_json(a.markets),en,core.parse_iso(sm.get("created_at")) or datetime.now(timezone.utc));core.write_json(a.output,live);hp=Path(a.history_dir);h=discover_history(hp,a.max_pages,a.wall_clock_seconds) if a.discover_history else (core.read_json(hp/"manifest.json") if (hp/"manifest.json").exists() else None);Path(a.report).write_text(markdown(live,h),encoding="utf-8");o={"live":{k:v for k,v in live.items() if k!="contract_rows"},"history":h}
    print(json.dumps(o,ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
