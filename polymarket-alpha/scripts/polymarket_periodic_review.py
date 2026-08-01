#!/usr/bin/env python3
"""Generate daily settlement, weekly/monthly review and error-notebook entry points."""
from __future__ import annotations
import argparse,json,math
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
def read(p:str,d):
 x=ROOT/p;return json.loads(x.read_text(encoding="utf-8")) if x.exists() else d
def now():return datetime.now(timezone.utc)
def parse(s):
 try:return datetime.fromisoformat(str(s).replace("Z","+00:00"))
 except:return datetime.min.replace(tzinfo=timezone.utc)
def normalize_winner(value):
 """Return a proven binary outcome or None; never map missing to NO."""
 value=str(value or "").strip().upper()
 return value if value in {"YES","NO"} else None
def build()->dict[str,Any]:
 ledger=read("data/manual_decision_observation_ledger.json",{"events":[]});forward=read("data/daily_candidate_observation_ledger.json",{"events":[]})
 resolutions={e.get("condition_id"):e for e in forward.get("events",[]) if e.get("event_type")=="resolution_observation"};events=ledger.get("events",[]);canonical={}
 for e in sorted(events,key=lambda x:x.get("observed_at") or ""):
  canonical.setdefault(e.get("condition_id"),e)
 scored=[];pending=[];errors=[]
 for c,e in canonical.items():
  r=resolutions.get(c)
  if not r:
   pending.append({"condition_id":c,"market":e.get("market"),"direction":e.get("direction"),
                   "winning_outcome":None,"hit":None,"brier":None,"log_loss":None,
                   "resolution_status":"missing_resolution_record"})
   continue
  win=normalize_winner(r.get("winning_outcome"))
  if win is None:
   # A missing/invalid resolution is censored, not a NO outcome and not a
   # direction miss. Keep it visible for follow-up without scoring it.
   pending.append({"condition_id":c,"market":e.get("market"),"direction":e.get("direction"),
                   "winning_outcome":None,"hit":None,"brier":None,"log_loss":None,
                   "resolution_status":r.get("resolution_proof_status") or "unverified"})
   continue
  p=e.get("research_probability");direction=e.get("direction");outcome=1 if win=="YES" else 0
  if p is not None:
   pp=max(1e-6,min(1-1e-6,float(p)));b=(pp-outcome)**2;ll=-(outcome*math.log(pp)+(1-outcome)*math.log(1-pp))
  else:b=ll=None
  hit=direction==win if direction else None;row={"condition_id":c,"market":e.get("market"),"recommendation_level":e.get("recommendation_level"),"direction":direction,"winning_outcome":win,"hit":hit,"brier":b,"log_loss":ll,"entered_paper":e.get("entered_paper",False)};scored.append(row)
  if hit is False:errors.append({**row,"error_type":"high_confidence_failure" if (e.get("research_probability") or 0)>=.8 else "direction_failure","back_case_required":True})
 types=Counter(x.get("recommendation_level") for x in events);cut_week=now()-timedelta(days=7);cut_month=now()-timedelta(days=30)
 return {"schema_version":"polymarket-periodic-review-v1","created_at":now().isoformat(),"status":"ok","daily":{"newly_scored_independent_conditions":len(scored),"scored":scored,"pending_unverified_results":pending,"back_cases_required":len(errors)},"weekly":{"observation_count":sum(parse(e.get("observed_at"))>=cut_week for e in events),"recommendation_level_counts":dict(types),"resolved_independent_conditions":len(scored),"unverified_result_conditions":len(pending),"error_type_counts":dict(Counter(e["error_type"] for e in errors)),"roi":None,"mean_clv":None},"monthly":{"observation_count":sum(parse(e.get("observed_at"))>=cut_month for e in events),"resolved_independent_conditions":len(scored),"unverified_result_conditions":len(pending),"net_roi":None,"roi_confidence_interval_95":None,"relative_market_brier_log_loss":None,"binance_same_window_status":read("experiments/current-same-window-comparison.json",{}).get("comparison_status")},"error_notebook":errors,"independence_unit":"condition_id","hourly_snapshots_do_not_inflate_samples":True,"unverified_results_excluded_from_direction_price_exit_metrics":True,"paper_only":True,"live_orders_enabled":False,"private_api_used":False,"real_money_execution_authorized":False}
def render(p,title,section):
 d=p[section];return "\n".join([f"# {title}","",f"- 更新时间：`{p['created_at']}`",f"- 状态：`{p['status']}`","",f"```json\n{json.dumps(d,ensure_ascii=False,indent=2)}\n```","","同一 condition 的重复小时快照不增加独立样本数；暂无结算时指标保持 pending，而不是填零。",""])
def main()->int:
 a=argparse.ArgumentParser();a.add_argument("--output",default=str(ROOT/"experiments/current-periodic-review.json"));x=a.parse_args();p=build();Path(x.output).write_text(json.dumps(p,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");outs=[("reports/CURRENT_DAILY_SETTLEMENT_REVIEW.md","每日结算与 Back Case","daily"),("reports/CURRENT_WEEKLY_REVIEW.md","Polymarket 每周复盘","weekly"),("reports/CURRENT_MONTHLY_REVIEW.md","Polymarket 每月复盘","monthly"),("reports/CURRENT_ERROR_NOTEBOOK.md","Polymarket 错题本","error_notebook")]
 for path,title,sec in outs:
  body=render(p,title,sec) if sec!="error_notebook" else "\n".join([f"# {title}","",f"- 当前错题：{len(p['error_notebook'])}","",json.dumps(p['error_notebook'],ensure_ascii=False,indent=2),""])
  (ROOT/path).write_text(body,encoding="utf-8")
 print(json.dumps({"status":p["status"],"resolved":p["daily"]["newly_scored_independent_conditions"],"back_cases_required":p["daily"]["back_cases_required"]},ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
