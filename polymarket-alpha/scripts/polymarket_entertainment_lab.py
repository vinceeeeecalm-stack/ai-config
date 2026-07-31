#!/usr/bin/env python3
"""Entertainment Phase-0 taxonomy by format, property and resolution authority."""
from __future__ import annotations
import argparse,importlib.util,json,re
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
def load(n,p):
 s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);assert s.loader;s.loader.exec_module(m);return m
core=load("ent_core",ROOT/"scripts/polymarket_alpha.py");family=load("ent_family",ROOT/"scripts/polymarket_market_family_audit.py")
def event_type(q):
 t=q.lower()
 if re.search(r"big brother|survivor|bachelor|bachelorette|reality",t):return "reality_show_outcome"
 if re.search(r"emmy|oscar|academy award|grammy|golden globe|award",t):return "award_outcome"
 if re.search(r"box office|gross |opening weekend|ticket sales",t):return "box_office"
 if re.search(r"billboard|spotify|album sales|streams|chart",t):return "music_chart_or_sales"
 if re.search(r"release|premiere|air by|renewed|cancelled",t):return "release_or_renewal"
 if re.search(r"marry|divorce|dating|arrested|celebrity|followers",t):return "celebrity_event"
 return "other_entertainment"
def property_name(q):
 t=q.lower();patterns=[("Big Brother USA","big brother"),("Emmys","emmy"),("Oscars","oscar|academy award"),("Grammys","grammy"),("Golden Globes","golden globe"),("Billboard","billboard"),("Spotify","spotify"),("Movies","box office|opening weekend")]
 for n,p in patterns:
  if re.search(p,t):return n
 return "Other"
def source_authority(d):
 t=d.lower()
 if re.search(r"official (?:website|announcement|results)|television academy|academy of motion picture|recording academy|cbs|abc|nbc|show's official",t):return "official_show_or_award"
 if re.search(r"box office mojo|the numbers|nielsen|comscore",t):return "named_audience_or_boxoffice"
 if re.search(r"billboard|spotify charts|official charts",t):return "named_music_chart"
 if "consensus of credible reporting" in t or "credible reporting" in t:return "media_consensus"
 if "resolution source" in t or "resolve based on" in t:return "named_other_source"
 return "source_unspecified"
def enrichment():
 x={}
 for n in ("current_family_identity_enrichment.json","current_uncovered_identity_enrichment.json"):
  p=ROOT/"cache"/n
  if p.exists():x.update(core.read_json(p).get("by_condition_id") or {})
 return {"by_condition_id":x}
def audit(markets,en,asof):
 rows=[]
 for m in markets:
  if not family.belongs(m,family.FAMILIES["entertainment"]):continue
  raw=m.get("_sampling_raw") or {};q=str(m.get("question") or raw.get("question") or "");d=str(m.get("description") or raw.get("description") or "");end=family.end_time(m);src=source_authority(d)
  rows.append({"event_id":family.event_identity(m,en),"event_type":event_type(q),"property":property_name(q),"source":src,"within_30d":bool(end and asof<=end<=asof+timedelta(days=30)),"expired":bool(end and end<asof),"clear":src!="source_unspecified" and family.rule_complete(m)})
 groups=[]
 for k in sorted({(r["event_type"],r["property"],r["source"]) for r in rows}):
  ss=[r for r in rows if (r["event_type"],r["property"],r["source"])==k];ev={r["event_id"] for r in ss if r["event_id"]};clean={r["event_id"] for r in ss if r["event_id"] and r["clear"] and not r["expired"]};near={r["event_id"] for r in ss if r["event_id"] and r["within_30d"]};ratio=len(clean)/len(ev) if ev else 0
  groups.append({"event_type":k[0],"property":k[1],"source_authority":k[2],"contract_count":len(ss),"independent_event_count":len(ev),"clean_event_count":len(clean),"near_term_30d_event_count":len(near),"phase_zero_history_candidate":bool(ev and len(ev)>=30 and ratio>=.9 and k[1]!="Other" and k[0]!="other_entertainment")})
 groups.sort(key=lambda r:(r["phase_zero_history_candidate"],r["near_term_30d_event_count"],r["independent_event_count"]),reverse=True);c=[r for r in groups if r["phase_zero_history_candidate"]]
 return {"schema_version":"polymarket-entertainment-taxonomy-v1","created_at":core.now_iso(),"inventory_as_of":asof.isoformat(),"contract_count":len(rows),"independent_event_count":len({r["event_id"] for r in rows if r["event_id"]}),"groups":groups,"selected_group_for_history_research":c[0] if c else None,"model_status":"blocked_pending_terminal_history" if c else "blocked_no_homogeneous_live_group","paper_entry_eligible":False,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"live_orders_enabled":False,"private_api_used":False}
def md(d):
 a=["# Polymarket Entertainment Phase-0 Audit","",f"- Contracts / events: {d['contract_count']} / {d['independent_event_count']}",f"- Selected history group: `{d['selected_group_for_history_research']}`",f"- Status: `{d['model_status']}`","","| Type | Property | Source | Contracts | Events | Clean | <=30d | Candidate |","|---|---|---|---:|---:|---:|---:|---|"]
 for r in d["groups"]:a.append(f"| {r['event_type']} | {r['property']} | {r['source_authority']} | {r['contract_count']} | {r['independent_event_count']} | {r['clean_event_count']} | {r['near_term_30d_event_count']} | {str(r['phase_zero_history_candidate']).lower()} |")
 a.extend(["","Reality shows, awards, box office, music charts, releases and celebrity events remain isolated.",""]);return "\n".join(a)
def self_test():
 assert event_type("Who will win Big Brother USA?")=="reality_show_outcome";assert property_name("Best Drama at the Emmys?")=="Emmys";return {"status":"pass","tests":["reality","emmys"]}
def main():
 p=argparse.ArgumentParser();p.add_argument("--self-test",action="store_true");p.add_argument("--markets",default=str(ROOT/"cache/current_validation_snapshot/markets.json"));p.add_argument("--snapshot-manifest",default=str(ROOT/"cache/current_validation_snapshot/snapshot-manifest.json"));p.add_argument("--output",default=str(ROOT/"experiments/current-entertainment-taxonomy-audit.json"));p.add_argument("--report",default=str(ROOT/"reports/CURRENT_ENTERTAINMENT_TAXONOMY_AUDIT.md"));a=p.parse_args()
 if a.self_test:o=self_test()
 else:
  s=core.read_json(a.snapshot_manifest);o=audit(core.read_json(a.markets),enrichment(),core.parse_iso(s.get("created_at")) or datetime.now(timezone.utc));core.write_json(a.output,o);Path(a.report).write_text(md(o),encoding="utf-8")
 print(json.dumps(o,ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
