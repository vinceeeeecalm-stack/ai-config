#!/usr/bin/env python3
"""Token-launch rule audit and terminal history across official Gamma tags."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,re,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
ROOT=Path(__file__).resolve().parents[1];GAMMA="https://gamma-api.polymarket.com";TAGS={"token_launches":104479,"pre_market":102368}
def load(n,p):
 s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);assert s.loader;s.loader.exec_module(m);return m
core=load("token_core",ROOT/"scripts/polymarket_alpha.py");family=load("token_family",ROOT/"scripts/polymarket_market_family_audit.py");public=load("token_public",ROOT/"scripts/polymarket_public_data.py")
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def enrichment():
 x={}
 for n in ("current_family_identity_enrichment.json","current_uncovered_identity_enrichment.json"):
  p=ROOT/"cache"/n
  if p.exists():x.update(core.read_json(p).get("by_condition_id") or {})
 return {"by_condition_id":x}
def rule_profile(q,d):
 t=d.lower();kind="governance_token" if "governance token" in t else "any_token";definition="public_transferable_tradable_no_announcement" if all(x in t for x in ("publicly transferable","tradable","announcements alone do not qualify")) else "other_definition";source="official_project_plus_media" if "primary resolution source" in t and "credible reporting" in t else "other_source";deadline="quarter_end" if re.search(r"march 31|june 30|september 30|december 31",q.lower()) else "other_deadline";return kind,definition,source,deadline
def live_audit(markets,en,asof):
 rows=[]
 for m in markets:
  if not family.belongs(m,family.FAMILIES["token_launch"]):continue
  raw=m.get("_sampling_raw") or {};q=str(m.get("question") or raw.get("question") or "");d=str(m.get("description") or raw.get("description") or "");end=family.end_time(m);p=rule_profile(q,d)
  rows.append({"event_id":family.event_identity(m,en),"profile":p,"within_30d":bool(end and asof<=end<=asof+timedelta(days=30)),"expired":bool(end and end<asof)})
 groups=[]
 for p in sorted({r["profile"] for r in rows}):
  ss=[r for r in rows if r["profile"]==p];ev={r["event_id"] for r in ss if r["event_id"]};clean={r["event_id"] for r in ss if r["event_id"] and p[1]=="public_transferable_tradable_no_announcement" and p[2]=="official_project_plus_media" and not r["expired"]};near={r["event_id"] for r in ss if r["event_id"] and r["within_30d"]}
  groups.append({"token_kind":p[0],"launch_definition":p[1],"source_contract":p[2],"deadline_class":p[3],"contract_count":len(ss),"independent_event_count":len(ev),"clean_event_count":len(clean),"near_term_30d_event_count":len(near),"phase_zero_history_candidate":len(ev)>=30 and len(clean)==len(ev)})
 groups.sort(key=lambda r:(r["phase_zero_history_candidate"],r["independent_event_count"]),reverse=True);c=[r for r in groups if r["phase_zero_history_candidate"]]
 return {"schema_version":"polymarket-token-launch-live-v1","created_at":core.now_iso(),"inventory_as_of":asof.isoformat(),"contract_count":len(rows),"independent_event_count":len({r["event_id"] for r in rows if r["event_id"]}),"groups":groups,"selected_group_for_history_research":c[0] if c else None,"paper_entry_eligible":False,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"live_orders_enabled":False,"private_api_used":False}
def load_events(d):
 x={}
 for p in sorted((d/"pages").glob("page-*.json")):
  for e in core.read_json(p).get("events") or []:x[str(e.get("id"))]=e
 return list(x.values())
def strict_event(e):
 title=str(e.get("title") or "");markets=e.get("markets") or []
 if "launch a token" not in title.lower() or len(markets)!=1:return None
 m=markets[0];d=str(m.get("description") or e.get("description") or "");profile=rule_profile(title,d);res=public.infer_resolution(m)
 if res.get("winning_outcome") not in {"Yes","No"} or profile[1]!="public_transferable_tradable_no_announcement" or profile[2]!="official_project_plus_media":return None
 project=re.sub(r"^Will\s+","",title,flags=re.I).split(" launch a token",1)[0].strip();return {"event_id":str(e.get("id") or ""),"market_id":str(m.get("id") or ""),"project":project,"token_kind":profile[0],"deadline_class":profile[3],"end_time":m.get("endDate") or e.get("endDate"),"winning_outcome":res["winning_outcome"],"tokens":public.token_map(m)}
def discover_tag(name,tag,d,maxp,wall):
 (d/"pages").mkdir(parents=True,exist_ok=True);contract={"endpoint":f"{GAMMA}/events/keyset","closed":True,"tag_id":tag,"limit":20};sp=d/"state.json";lp=d/"request-log.json";st=core.read_json(sp) if sp.exists() else {"schema_version":"token-launch-history-state-v1","contract":contract,"next_cursor":None,"terminal_cursor_proven":False,"pages_fetched":0};req=core.read_json(lp) if lp.exists() else []
 if st.get("contract")!=contract:raise ValueError("token history contract mismatch")
 core.write_json(sp,st);core.write_json(lp,req);start=time.monotonic();n=0
 while not st["terminal_cursor_proven"] and n<maxp and time.monotonic()-start<wall:
  q={"closed":"true","tag_id":tag,"limit":20}
  if st.get("next_cursor"):q["after_cursor"]=st["next_cursor"]
  u=f"{GAMMA}/events/keyset?{urlencode(q)}"
  try:
   z=public.get_json(u,timeout=25,retries=2);ev=z.get("events");num=st["pages_fetched"]+1;cur=z.get("next_cursor");core.write_json(d/"pages"/f"page-{num:04d}.json",{"page_number":num,"request_url":u,"events":ev,"next_cursor":cur});req.append({"page_number":num,"url":u,"status":"ok","rows":len(ev),"terminal":not bool(cur),"created_at":core.now_iso()});st.update({"pages_fetched":num,"next_cursor":cur,"terminal_cursor_proven":not bool(cur),"updated_at":core.now_iso()});core.write_json(lp,req);core.write_json(sp,st);n+=1
  except Exception as ex:req.append({"url":u,"status":"failed","error":f"{type(ex).__name__}:{ex}","created_at":core.now_iso()});core.write_json(lp,req);break
 ev=load_events(d);rows=[x for e in ev if (x:=strict_event(e))];core.write_json(d/"strict-token-launch-events.json",rows);rel=["state.json","request-log.json","strict-token-launch-events.json"]+[str(p.relative_to(d)) for p in sorted((d/"pages").glob("*.json"))];out={"schema_version":"polymarket-token-launch-tag-history-v1","created_at":core.now_iso(),"tag_name":name,"tag_id":tag,"pages_fetched":st["pages_fetched"],"terminal_cursor_proven":st["terminal_cursor_proven"],"events_fetched":len(ev),"strict_events":len(rows),"files":[{"path":x,"bytes":(d/x).stat().st_size,"sha256":sha(d/x)} for x in rel],"research_only":True,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"live_orders_enabled":False,"private_api_used":False};core.write_json(d/"manifest.json",out);return out,rows
def aggregate(live,root,manifests):
 rows={}
 for n in TAGS:
  for r in core.read_json(root/n/"strict-token-launch-events.json"):rows[r["event_id"]]=r
 dates={str(r.get("end_time") or "")[:10] for r in rows.values()};terminal=all(x["terminal_cursor_proven"] for x in manifests)
 return {"schema_version":"polymarket-token-launch-history-audit-v1","created_at":core.now_iso(),"live":live,"tag_manifests":manifests,"strict_deduplicated_settled_events":len(rows),"distinct_projects":len({r["project"] for r in rows.values()}),"distinct_deadline_dates":len(dates),"minimum_total_for_frozen_split":150,"model_sample_gate_met":len(rows)>=150,"data_status":"ok" if terminal else "degraded","point_in_time_feature_source_ready":False,"paper_entry_eligible":False,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"live_orders_enabled":False,"private_api_used":False}
def md(o):return "\n".join(["# Polymarket Token Launch Data Gate","",f"- Live contracts / events: {o['live']['contract_count']} / {o['live']['independent_event_count']}",f"- Strict deduplicated settled history: {o['strict_deduplicated_settled_events']}",f"- Model sample gate: `{str(o['model_sample_gate_met']).lower()}`",f"- Point-in-time feature source ready: `{str(o['point_in_time_feature_source_ready']).lower()}`","","No probability or paper permission is emitted.",""])
def self_test():
 q="Will Arc launch a token by September 30 2026?";d="Primary resolution source for this market. consensus of credible reporting. actively and publicly transferable and tradable. Announcements alone do not qualify.";assert rule_profile(q,d)[1]=="public_transferable_tradable_no_announcement";return {"status":"pass","tests":["strict_launch_definition"]}
def main():
 p=argparse.ArgumentParser();p.add_argument("--self-test",action="store_true");p.add_argument("--discover-history",action="store_true");p.add_argument("--max-pages",type=int,default=150);p.add_argument("--wall-clock-seconds",type=float,default=240);p.add_argument("--markets",default=str(ROOT/"cache/current_validation_snapshot/markets.json"));p.add_argument("--snapshot-manifest",default=str(ROOT/"cache/current_validation_snapshot/snapshot-manifest.json"));p.add_argument("--history-dir",default=str(ROOT/"cache/current_token_launch_history"));p.add_argument("--output",default=str(ROOT/"experiments/current-token-launch-history-audit.json"));p.add_argument("--report",default=str(ROOT/"reports/CURRENT_TOKEN_LAUNCH_DATA_GATE.md"));a=p.parse_args()
 if a.self_test:o=self_test()
 else:
  s=core.read_json(a.snapshot_manifest);live=live_audit(core.read_json(a.markets),enrichment(),core.parse_iso(s.get("created_at")) or datetime.now(timezone.utc));root=Path(a.history_dir);man=[]
  for n,t in TAGS.items():
   if a.discover_history:m,_=discover_tag(n,t,root/n,a.max_pages,a.wall_clock_seconds)
   else:m=core.read_json(root/n/"manifest.json")
   man.append(m)
  o=aggregate(live,root,man);core.write_json(a.output,o);Path(a.report).write_text(md(o),encoding="utf-8")
 print(json.dumps(o,ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
