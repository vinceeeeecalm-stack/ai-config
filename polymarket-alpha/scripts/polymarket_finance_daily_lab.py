#!/usr/bin/env python3
"""Public-only finance daily-direction taxonomy and settled-history gate."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,re,time,urllib.request
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import urlencode
ROOT=Path(__file__).resolve().parents[1];GAMMA="https://gamma-api.polymarket.com";TAG_ID=104152;LIMIT=20
CLOB="https://clob.polymarket.com";CUTOFFS={"T-24h":timedelta(hours=24),"T-60m":timedelta(minutes=60)};MAX_AGE={"T-24h":21600,"T-60m":7200}
YAHOO="https://query1.finance.yahoo.com"
def load(n,p):
 s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);assert s.loader;s.loader.exec_module(m);return m
core=load("fin_core",ROOT/"scripts/polymarket_alpha.py");family=load("fin_family",ROOT/"scripts/polymarket_market_family_audit.py");public=load("fin_public",ROOT/"scripts/polymarket_public_data.py")
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def parse_time(value):
 if not value:return None
 text=str(value)
 match=re.match(r"^(.*\.)(\d+)(Z|[+-]\d\d:?\d\d)$",text)
 if match:text=f"{match.group(1)}{match.group(2)[:6].ljust(6,'0')}{match.group(3)}"
 try:return datetime.fromisoformat(text.replace("Z","+00:00"))
 except ValueError:return core.parse_iso(value)
def contract_type(t):
 l=t.lower()
 if "opens up or down" in l:return "open_direction"
 if "up or down" in l:return "close_to_close_direction"
 if "closes above" in l:return "close_threshold"
 return "other"
def symbol(t):
 x=re.search(r"\(([A-Z0-9.]{1,10})\)",t);return x.group(1) if x else "UNKNOWN"
def asset_class(t,tags=None):
 tags=set(tags or []);l=t.lower()
 if "Stocks" in tags or "Equities" in tags:return "equity"
 if "Commodities" in tags or re.search(r"gold|silver|natural gas|crude oil|wti",l):return "commodity"
 if "Indicies" in tags or re.search(r"s&p 500 index|\(spx\)",l):return "index"
 return "unknown"
def diagnostics(t):
 l=t.lower();source="Pyth" if "pyth" in l else ("official_index" if "official s&p 500 index" in l else "source_unspecified")
 prior="most recent prior trading day" in l;regular=bool(re.search(r"regular (?:trading )?session|regular trading hours|final minute",l));tie=bool(re.search(r"exactly equal|same closing price",l))
 return {"price_source":source,"prior_close_defined":prior,"session_close_defined":regular,"tie_rule_defined":tie,"high_rule_ambiguity":source=="source_unspecified" or not prior or not regular or not tie}
def live_audit(markets,enrichment,asof):
 rows=[]
 for m in markets:
  if not family.belongs(m,family.FAMILIES["finance_daily_direction"]):continue
  raw=m.get("_sampling_raw") or {};ev=(m.get("events") or [{}])[0];q=str(m.get("question") or raw.get("question") or "");d=str(ev.get("description") or m.get("description") or raw.get("description") or "");t=f"{q}\n{d}";end=family.end_time(m)
  rows.append({"market_id":str(m.get("id") or m.get("conditionId") or ""),"event_id":family.event_identity(m,enrichment),"question":q,"symbol":symbol(q),"asset_class":asset_class(t,raw.get("tags") or []),"contract_type":contract_type(q),"end_time":end.isoformat() if end else None,"expired_but_live":bool(end and end<asof),**diagnostics(t)})
 groups=[]
 for k in sorted({(r["contract_type"],r["asset_class"],r["price_source"]) for r in rows}):
  ss=[r for r in rows if (r["contract_type"],r["asset_class"],r["price_source"])==k];events={r["event_id"] for r in ss if r["event_id"]};clean=[r for r in ss if not r["high_rule_ambiguity"] and not r["expired_but_live"]]
  groups.append({"contract_type":k[0],"asset_class":k[1],"price_source":k[2],"contract_count":len(ss),"independent_event_count":len(events),"clean_contract_count":len(clean),"symbols":sorted({r["symbol"] for r in ss}),"phase_zero_candidate":len(events)>=20 and len(clean)==len(ss)})
 groups.sort(key=lambda r:(r["phase_zero_candidate"],r["independent_event_count"]),reverse=True);c=[r for r in groups if r["phase_zero_candidate"]]
 return {"schema_version":"polymarket-finance-daily-live-v1","created_at":core.now_iso(),"inventory_as_of":asof.isoformat(),"family":"finance_daily_direction","contract_count":len(rows),"independent_event_count":len({r["event_id"] for r in rows if r["event_id"]}),"groups":groups,"selected_group_for_history_research":c[0] if c else None,"model_status":"blocked_pending_terminal_history","contract_rows":rows,"paper_entry_eligible":False,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"live_orders_enabled":False,"private_api_used":False}
def load_events(d):
 x={}
 for p in sorted((d/"pages").glob("page-*.json")):
  for e in core.read_json(p).get("events") or []:x[str(e.get("id"))]=e
 return sorted(x.values(),key=lambda r:int(r.get("id") or 0))
def settled(e):
 term=[]
 for m in e.get("markets") or []:
  r=public.infer_resolution(m)
  if r.get("winning_outcome") is not None:term.append({"market_id":str(m.get("id") or ""),"winning_outcome":r["winning_outcome"]})
 if not term:return None
 title=str(e.get("title") or "");text=f"{title}\n{e.get('description') or ''}";tags={str(z.get("label") or z) for z in e.get("tags") or []}
 return {"event_id":str(e.get("id") or ""),"title":title,"symbol":symbol(title),"asset_class":asset_class(text,tags),"contract_type":contract_type(title),"end_time":e.get("endDate"),"terminal_markets":term,**diagnostics(text)}
def eligible_equity_markets(d):
 out=[]
 for e in load_events(d):
  row=settled(e)
  if not row or row["contract_type"]!="close_to_close_direction" or row["asset_class"]!="equity" or row["price_source"]!="Pyth" or row["high_rule_ambiguity"]:continue
  for m in e.get("markets") or []:
   resolution=public.infer_resolution(m)
   outcomes=public.parse_jsonish(m.get("outcomes"));tokens=public.parse_jsonish(m.get("clobTokenIds"))
   if resolution.get("winning_outcome") is None or len(outcomes)!=2 or len(tokens)!=2 or set(outcomes)!={"Up","Down"}:continue
   end=parse_time(e.get("endDate") or m.get("endDate"));start=parse_time(m.get("startDate") or e.get("startDate"))
   if not end or not start:continue
   out.append({"event_id":str(e.get("id") or ""),"market_id":str(m.get("id") or ""),"symbol":row["symbol"],"settlement_date":end.date().isoformat(),"end_time":end.isoformat(),"market_start_time":start.isoformat(),"winning_outcome":resolution["winning_outcome"],"tokens":dict(zip(outcomes,map(str,tokens)))})
 return out
def latest_before(history,cutoff,start):
 rows=[r for r in history if start.timestamp()<=float(r.get("t",0))<=cutoff.timestamp()]
 return max(rows,key=lambda r:float(r["t"])) if rows else None
def cutoff_observations(markets,histories):
 out=[]
 for m in markets:
  end=parse_time(m["end_time"]);start=parse_time(m["market_start_time"]);assert end and start
  for label,delta in CUTOFFS.items():
   cutoff=end-delta;prices={};fail=[]
   if start>cutoff:fail.append("market_not_open_at_cutoff")
   for outcome,token in m["tokens"].items():
    point=latest_before((histories.get(token) or {}).get("history") or [],cutoff,start)
    if point is None:fail.append(f"price_missing:{outcome}");continue
    age=cutoff.timestamp()-float(point["t"])
    if age>MAX_AGE[label]:fail.append(f"price_stale:{outcome}")
    prices[outcome]={"token_id":token,"price":float(point["p"]),"timestamp":int(point["t"]),"age_seconds":age}
   total=sum(v["price"] for v in prices.values()) if len(prices)==2 else None
   normalized={k:v["price"]/total for k,v in prices.items()} if total and total>0 and len(prices)==2 else {}
   out.append({"event_id":m["event_id"],"market_id":m["market_id"],"symbol":m["symbol"],"settlement_date":m["settlement_date"],"winning_outcome":m["winning_outcome"],"cutoff_label":label,"cutoff_at":cutoff.isoformat(),"outcome_prices":prices,"raw_probability_sum":total,"normalized_market_probabilities":normalized,"status":"complete" if not fail else "excluded","exclusion_reasons":fail,"future_price_data_used":False,"execution_evidence_eligible":False})
 return out
def fetch_cutoff_prices(history_dir,output_dir,max_batches):
 source=core.read_json(history_dir/"manifest.json")
 if source.get("terminal_cursor_proven") is not True:raise ValueError("finance discovery has no terminal cursor proof")
 markets=eligible_equity_markets(history_dir);output_dir.mkdir(parents=True,exist_ok=True);hp=output_dir/"price-history.json";rp=output_dir/"request-log.json";histories=core.read_json(hp) if hp.exists() else {};requests=core.read_json(rp) if rp.exists() else []
 groups=[]
 for i in range(0,len(markets),10):
  batch=markets[i:i+10];tokens=[t for m in batch for t in m["tokens"].values()]
  if all(t in histories for t in tokens):continue
  ends=[parse_time(m["end_time"]) for m in batch];groups.append((tokens,min(x-timedelta(hours=30) for x in ends if x),max(x-timedelta(minutes=55) for x in ends if x)))
 try:
  for n,(tokens,start,end) in enumerate(groups[:max_batches],1):
   body={"markets":tokens,"start_ts":int(start.timestamp()),"end_ts":int(end.timestamp()),"fidelity":5}
   try:
    z=public.post_json(f"{CLOB}/batch-prices-history",body,timeout=12,retries=1);result=z.get("history") if isinstance(z,dict) else {}
    for token in tokens:histories[token]={"history":sorted((result.get(token) or []) if isinstance(result,dict) else [],key=lambda r:int(r["t"])),"fidelity_minutes":5,"attempted_at":core.now_iso()}
    requests.append({"status":"ok","tokens":tokens,"start_ts":body["start_ts"],"end_ts":body["end_ts"],"nonempty":sum(bool(histories[t]["history"]) for t in tokens),"created_at":core.now_iso()})
   except Exception as ex:requests.append({"status":"failed","tokens":tokens,"error":f"{type(ex).__name__}:{ex}","created_at":core.now_iso()})
   if n%10==0:core.write_json(hp,histories);core.write_json(rp,requests)
 finally:core.write_json(hp,histories);core.write_json(rp,requests)
 core.write_json(output_dir/"eligible-markets.json",markets);obs=cutoff_observations(markets,histories);core.write_json(output_dir/"cutoff-observations.json",obs);complete=Counter(r["cutoff_label"] for r in obs if r["status"]=="complete");tokens={t for m in markets for t in m["tokens"].values()};nonempty=sum(bool((histories.get(t) or {}).get("history")) for t in tokens);relative=["eligible-markets.json","price-history.json","request-log.json","cutoff-observations.json"]
 manifest={"schema_version":"polymarket-finance-daily-cutoff-price-v1","created_at":core.now_iso(),"source_history_manifest_sha256":sha(history_dir/"manifest.json"),"official_source":f"{CLOB}/batch-prices-history","fidelity_minutes":5,"eligible_symbol_date_events":len(markets),"distinct_settlement_dates":len({m["settlement_date"] for m in markets}),"target_tokens":len(tokens),"nonempty_token_histories":nonempty,"token_history_coverage_pct":round(100*nonempty/len(tokens),4) if tokens else 0,"complete_observations_by_cutoff":dict(complete),"observation_count":len(obs),"execution_evidence_eligible":False,"execution_limit":"historical trade prices have no spread, depth, executable VWAP or impact; forward books are required","data_status":"ok" if tokens and nonempty/len(tokens)>=.95 and all(complete[x]/len(markets)>=.9 for x in CUTOFFS) else "degraded","files":[{"path":x,"bytes":(output_dir/x).stat().st_size,"sha256":sha(output_dir/x)} for x in relative],"research_only":True,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"live_orders_enabled":False,"private_api_used":False};core.write_json(output_dir/"manifest.json",manifest);return manifest
def yahoo_json(url):
 if not url.startswith(YAHOO+"/v8/finance/chart/"):raise ValueError("non-Yahoo chart URL blocked")
 req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 polymarket-alpha-paper-research/1.0"})
 with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
def fetch_ohlc(history_dir,output_dir,start="2025-01-01"):
 markets=eligible_equity_markets(history_dir);symbols=sorted({m["symbol"] for m in markets});output_dir.mkdir(parents=True,exist_ok=True);start_ts=int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp());end_ts=int((datetime.now(timezone.utc)+timedelta(days=2)).timestamp());all_rows={};requests=[]
 for ticker in symbols:
  url=f"{YAHOO}/v8/finance/chart/{ticker}?{urlencode({'period1':start_ts,'period2':end_ts,'interval':'1d','events':'history','includeAdjustedClose':'true'})}"
  try:
   z=yahoo_json(url);result=z["chart"]["result"][0];quote=result["indicators"]["quote"][0];adjusted=(result["indicators"].get("adjclose") or [{}])[0].get("adjclose") or quote["close"];rows=[]
   for i,stamp in enumerate(result["timestamp"]):
    values={k:quote[k][i] for k in ("open","high","low","close")}
    if any(v is None for v in values.values()) or adjusted[i] is None:continue
    factor=float(adjusted[i])/float(values["close"]);rows.append({"date":datetime.fromtimestamp(stamp,timezone.utc).date().isoformat(),"timestamp":int(stamp),**{k:float(v)*factor for k,v in values.items()},"adjustment_factor":factor})
   all_rows[ticker]=rows;requests.append({"symbol":ticker,"url":url,"status":"ok","rows":len(rows)})
  except Exception as ex:requests.append({"symbol":ticker,"url":url,"status":"failed","error":f"{type(ex).__name__}:{ex}"})
 core.write_json(output_dir/"ohlc.json",all_rows);core.write_json(output_dir/"request-log.json",requests);usable={s for s,rows in all_rows.items() if len(rows)>=250};excluded=sorted(set(symbols)-usable);events=sum(m["symbol"] in usable for m in markets);relative=["ohlc.json","request-log.json"]
 manifest={"schema_version":"polymarket-finance-daily-yahoo-ohlc-v1","created_at":core.now_iso(),"source":YAHOO,"start_date":start,"requested_symbols":symbols,"usable_symbols":sorted(usable),"excluded_symbols":excluded,"row_counts":{s:len(r) for s,r in all_rows.items()},"eligible_events_after_source_gate":events,"split_adjusted_ohlc":True,"failed_request_count":sum(r["status"]=="failed" for r in requests),"data_status":"ok" if len(usable)>=18 and events>=1200 else "degraded","files":[{"path":x,"bytes":(output_dir/x).stat().st_size,"sha256":sha(output_dir/x)} for x in relative],"research_only":True,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"live_orders_enabled":False,"private_api_used":False};core.write_json(output_dir/"manifest.json",manifest);return manifest
def history(d,maxp,wall):
 (d/"pages").mkdir(parents=True,exist_ok=True);contract={"endpoint":f"{GAMMA}/events/keyset","closed":True,"tag_id":TAG_ID,"limit":LIMIT};sp=d/"state.json";lp=d/"request-log.json";st=core.read_json(sp) if sp.exists() else {"schema_version":"polymarket-finance-daily-history-state-v1","contract":contract,"next_cursor":None,"terminal_cursor_proven":False,"pages_fetched":0}
 if st.get("contract")!=contract:raise ValueError("finance history contract mismatch")
 req=core.read_json(lp) if lp.exists() else [];core.write_json(sp,st);core.write_json(lp,req);start=time.monotonic();n=0
 while not st["terminal_cursor_proven"] and n<maxp and time.monotonic()-start<wall:
  q={"closed":"true","tag_id":TAG_ID,"limit":LIMIT}
  if st.get("next_cursor"):q["after_cursor"]=st["next_cursor"]
  u=f"{GAMMA}/events/keyset?{urlencode(q)}"
  try:
   z=public.get_json(u,timeout=25,retries=2);ev=z.get("events")
   if not isinstance(ev,list):raise ValueError("keyset missing events")
   num=int(st["pages_fetched"])+1;cur=z.get("next_cursor");core.write_json(d/"pages"/f"page-{num:04d}.json",{"page_number":num,"request_url":u,"events":ev,"next_cursor":cur});req.append({"page_number":num,"url":u,"status":"ok","rows":len(ev),"terminal":not bool(cur),"created_at":core.now_iso()});st.update({"pages_fetched":num,"next_cursor":cur,"terminal_cursor_proven":not bool(cur),"updated_at":core.now_iso()});core.write_json(lp,req);core.write_json(sp,st);n+=1
  except Exception as ex:req.append({"url":u,"status":"failed","error":f"{type(ex).__name__}:{ex}","created_at":core.now_iso()});core.write_json(lp,req);break
 ev=load_events(d);rows=[r for e in ev if (r:=settled(e))];core.write_json(d/"settled-finance-daily-events.json",rows);groups=[]
 for k in sorted({(r["contract_type"],r["asset_class"],r["price_source"]) for r in rows}):
  ss=[r for r in rows if (r["contract_type"],r["asset_class"],r["price_source"])==k];strict=[r for r in ss if not r["high_rule_ambiguity"]];groups.append({"contract_type":k[0],"asset_class":k[1],"price_source":k[2],"settled_event_count":len(ss),"strict_event_count":len(strict),"independent_symbols":len({r["symbol"] for r in strict}),"model_sample_gate_met":len(strict)>=150})
 groups.sort(key=lambda r:(r["model_sample_gate_met"],r["strict_event_count"]),reverse=True);rel=["state.json","request-log.json","settled-finance-daily-events.json"]+[str(p.relative_to(d)) for p in sorted((d/"pages").glob("page-*.json"))]
 selected=[r for r in rows if r["contract_type"]=="close_to_close_direction" and r["asset_class"]=="equity" and r["price_source"]=="Pyth" and not r["high_rule_ambiguity"]]
 settlement_dates=sorted({str(r.get("end_time") or "")[:10] for r in selected if r.get("end_time")})
 out={"schema_version":"polymarket-finance-daily-history-v1","created_at":core.now_iso(),"official_source":f"{GAMMA}/events/keyset","tag_id":TAG_ID,"pages_fetched":st["pages_fetched"],"terminal_cursor_proven":st["terminal_cursor_proven"],"events_fetched":len(ev),"settled_events":len(rows),"homogeneous_groups":groups,"minimum_total_for_frozen_60_20_20_split":150,"selected_group_for_model_research":next((r for r in groups if r["model_sample_gate_met"]),None),"model_sample_gate_met":any(r["model_sample_gate_met"] for r in groups),"selected_equity_scope":{"event_count":len(selected),"distinct_settlement_dates":len(settlement_dates),"first_date":settlement_dates[0] if settlement_dates else None,"last_date":settlement_dates[-1] if settlement_dates else None,"correlation_unit":"settlement_date; all symbols on the same date remain in one chronological split and confidence intervals are date-clustered","minimum_distinct_final_oos_dates":30,"historical_promotion_evidence_gate_met":False},"data_status":"ok" if st["terminal_cursor_proven"] else "degraded","files":[{"path":x,"bytes":(d/x).stat().st_size,"sha256":sha(d/x)} for x in rel],"research_only":True,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"live_orders_enabled":False,"private_api_used":False};core.write_json(d/"manifest.json",out);return out
def freeze_protocol(h,path):
 p=Path(path)
 if p.exists():return core.read_json(p)
 selected=h.get("selected_group_for_model_research") if h else None
 if not h or h.get("terminal_cursor_proven") is not True or not selected: return None
 payload={
  "schema_version":"polymarket-finance-daily-research-protocol-v1","created_at":core.now_iso(),"family":"finance_daily_direction","status":"protocol_frozen_inputs_not_acquired_model_not_fitted",
  "null_hypothesis":"no independent finance daily-direction model beats the contemporaneous executable Polymarket price after fees, spread, slippage and uncertainty discount",
  "contract_scope":"Pyth-resolved equity close-to-close Up/Down only; threshold, open-direction, commodity, index, live, parlay and ambiguous-rule contracts excluded",
  "independence_unit":"one symbol and settlement date event","correlation_unit":"settlement date; every symbol sharing a date stays in the same chronological segment and uncertainty is date-clustered",
  "forecast_cutoffs":["T-24h","T-60m"],
  "historical_corpus":{"source_manifest":"cache/current_finance_daily_history/manifest.json","terminal_cursor_required":True,"strict_event_count":selected["strict_event_count"],"distinct_symbols":selected["independent_symbols"],**(h.get("selected_equity_scope") or {})},
  "external_feature_contract":{"status":"not_acquired","required":"timestamped split-adjusted regular-session daily OHLC available before each cutoff, immutable raw cache, request log, SHA-256 manifest and explicit corporate-action handling","settlement_label":"official terminal Polymarket outcome under the event's Pyth rule","forbidden":"post-cutoff bars, revised future data, final outcome as a feature, private account/order data"},
  "market_benchmark_contract":{"status":"not_acquired","source":"public CLOB batch price history","selection":"last trade at or before each fixed cutoff; never nearest-after or backfilled","execution_boundary":"historical trades are calibration benchmarks only; spread, depth, impact and executable VWAP require forward order books"},
  "model_v1_frozen_candidate_set":{"target":"binary Up outcome","features":["lagged split-adjusted close returns over 1, 5 and 20 sessions","20-session realized volatility","symbol indicator with pooled regularization"],"candidates":["raw contemporaneous market probability baseline","unregularized zero-skill 0.5 baseline","L2 logistic C=0.01","L2 logistic C=0.1","L2 logistic C=1.0","L2 logistic C=10.0"],"preprocessing":"fit medians, scales and encoders on development only","selection_metric":"lowest development date-clustered binary Brier score; ties prefer simpler model","parameter_selection_scope":"development only"},
  "chronological_split":{"development":"earliest 60% of distinct settlement dates","validation":"next 20% of distinct settlement dates","historical_diagnostic_holdout":"latest 20% of distinct settlement dates; cannot count as pristine promotion evidence because taxonomy inspection preceded this freeze","fresh_final_oos":"events strictly after protocol created_at; minimum 30 distinct settlement dates and never used for selection"},
  "promotion_gate":{"validation":"positive paired date-clustered 95% lower bound for Brier or log-loss improvement versus market","fresh_final_oos":"same metric must retain a positive paired date-clustered 95% lower bound over at least 30 dates","net_value":"positive after executable forward spread, impact, fees, slippage and uncertainty discount","automatic_promotion":False,"manual_review_required":True},
  "probability_language":"all outputs remain model scores until calibration and fresh OOS gates pass","paper_entry_eligible":False,"paper_estimates_emitted":False,"main_paper_ledger_mutated":False,"live_orders_enabled":False,"private_api_used":False}
 core.write_json(p,payload);return payload
def report(l,h,protocol=None):
 a=["# Polymarket Finance Daily Direction Data Gate","",f"- Live: {l['contract_count']} contracts / {l['independent_event_count']} events",f"- Live research group: `{l['selected_group_for_history_research']}`","- History feasibility only; no probability or paper permission.",""]
 if h:
  s=h.get("selected_equity_scope") or {};a.extend([f"- Terminal history: {h['events_fetched']} events / {h['settled_events']} settled",f"- Strict Pyth equity direction scope: {s.get('event_count',0)} symbol-date events across {s.get('distinct_settlement_dates',0)} settlement dates",f"- Basic model-development sample gate: `{str(h['model_sample_gate_met']).lower()}`",f"- Historical promotion-evidence gate: `{str(s.get('historical_promotion_evidence_gate_met',False)).lower()}`",""])
 if protocol:a.extend([f"- Frozen protocol: `{protocol['schema_version']}`",f"- Protocol status: `{protocol['status']}`","- All same-date symbols remain in one split; promotion requires at least 30 genuinely post-freeze OOS dates.",""])
 return "\n".join(a)
def self_test():
 t='Apple (AAPL) Up or Down on July 13? Close price on most recent prior trading day. Pyth. regular trading session. exactly equal resolves Down.';assert contract_type(t)=="close_to_close_direction" and symbol(t)=="AAPL";assert diagnostics(t)["high_rule_ambiguity"] is False;return {"status":"pass","tests":["direction_scope","symbol_parse","pyth_source","prior_close_and_tie"]}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--self-test",action="store_true");p.add_argument("--discover-history",action="store_true");p.add_argument("--fetch-cutoff-prices",action="store_true");p.add_argument("--fetch-ohlc",action="store_true");p.add_argument("--max-pages",type=int,default=150);p.add_argument("--max-batches",type=int,default=150);p.add_argument("--wall-clock-seconds",type=float,default=240);p.add_argument("--markets",default=str(ROOT/"cache/current_validation_snapshot/markets.json"));p.add_argument("--snapshot-manifest",default=str(ROOT/"cache/current_validation_snapshot/snapshot-manifest.json"));p.add_argument("--identity-enrichment",default=str(ROOT/"cache/current_family_identity_enrichment.json"));p.add_argument("--history-dir",default=str(ROOT/"cache/current_finance_daily_history"));p.add_argument("--price-dir",default=str(ROOT/"cache/current_finance_daily_cutoff_prices"));p.add_argument("--ohlc-dir",default=str(ROOT/"cache/current_finance_daily_ohlc"));p.add_argument("--output",default=str(ROOT/"experiments/current-finance-daily-taxonomy-audit.json"));p.add_argument("--report",default=str(ROOT/"reports/CURRENT_FINANCE_DAILY_DATA_GATE.md"));a=p.parse_args()
 if a.self_test:o=self_test()
 elif a.fetch_cutoff_prices:o=fetch_cutoff_prices(Path(a.history_dir),Path(a.price_dir),a.max_batches)
 elif a.fetch_ohlc:o=fetch_ohlc(Path(a.history_dir),Path(a.ohlc_dir))
 else:
  sm=core.read_json(a.snapshot_manifest);en=core.read_json(a.identity_enrichment)
  if sm.get("terminal_cursor_proven") is not True:raise ValueError("snapshot not terminal")
  l=live_audit(core.read_json(a.markets),en,core.parse_iso(sm.get("created_at")) or datetime.now(timezone.utc));core.write_json(a.output,l);hp=Path(a.history_dir);h=history(hp,a.max_pages,a.wall_clock_seconds) if a.discover_history else (core.read_json(hp/"manifest.json") if (hp/"manifest.json").exists() else None);protocol=freeze_protocol(h,ROOT/"experiments/finance-daily-research-protocol-v1.json");Path(a.report).write_text(report(l,h,protocol),encoding="utf-8");o={"live":{k:v for k,v in l.items() if k!="contract_rows"},"history":h,"protocol":protocol}
 print(json.dumps(o,ensure_ascii=False,indent=2));return 0
if __name__=="__main__":raise SystemExit(main())
