#!/usr/bin/env python3
"""Frozen expanding Beta-Binomial models for earnings-beat markets."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
CLOB = "https://clob.polymarket.com"
PROTOCOL = ROOT / "experiments/corporate-earnings-research-protocol-v1.json"
CUTOFFS = {"T-24h": timedelta(hours=24), "T-60m": timedelta(minutes=60)}
MAX_AGE = {"T-24h": 43200, "T-60m": 7200}


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path); module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module


core = load("earnings_model_core", ROOT / "scripts/polymarket_alpha.py")
public = load("earnings_model_public", ROOT / "scripts/polymarket_public_data.py")
corporate = load("earnings_model_corporate", ROOT / "scripts/polymarket_corporate_lab.py")


def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_time(value: Any):
    parsed = corporate.core.parse_iso(value)
    if parsed: return parsed
    text = str(value); match = re.match(r"^(.*\.)(\d+)(Z|[+-]\d\d:?\d\d)$", text)
    if match: text = f"{match.group(1)}{match.group(2)[:6].ljust(6, '0')}{match.group(3)}"
    if re.search(r"[+-]\d\d$", text): text += ":00"
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def latest_before(history: list[dict[str, Any]], cutoff: datetime, market_start: datetime):
    rows = [row for row in history if market_start.timestamp() <= float(row.get("t", 0)) <= cutoff.timestamp()]
    return max(rows, key=lambda row: float(row["t"])) if rows else None


def cutoff_observations(events: list[dict[str, Any]], histories: dict[str, Any]) -> list[dict[str, Any]]:
    observations = []
    for event in events:
        release = parse_time(event["earnings_release_at"]); market_start = parse_time(event["market_created_at"])
        for label, delta in CUTOFFS.items():
            cutoff = release - delta; prices = {}; failures = []
            if market_start > cutoff: failures.append("market_not_open_at_cutoff")
            for outcome, token in event["tokens"].items():
                point = latest_before((histories.get(str(token)) or {}).get("history") or [], cutoff, market_start)
                if point is None: failures.append(f"price_missing:{outcome}"); continue
                age = cutoff.timestamp() - float(point["t"])
                if age > MAX_AGE[label]: failures.append(f"price_stale:{outcome}")
                prices[outcome] = {"token_id": str(token), "price": float(point["p"]), "timestamp": int(point["t"]), "age_seconds": age}
            total = sum(row["price"] for row in prices.values()) if len(prices) == 2 else None
            normalized = {outcome: row["price"] / total for outcome, row in prices.items()} if total and total > 0 and len(prices) == 2 else {}
            observations.append({"event_id": event["event_id"], "ticker": event["ticker"], "release_date": release.date().isoformat(), "cutoff_label": label, "cutoff_at": cutoff.isoformat(), "winning_outcome": event["winning_outcome"], "outcome_prices": prices, "normalized_market_probabilities": normalized, "status": "complete" if not failures else "excluded", "exclusion_reasons": failures, "future_price_data_used": False, "execution_evidence_eligible": False})
    return observations


def fetch_cutoff_prices(history_dir: Path, output_dir: Path, max_batches: int) -> dict[str, Any]:
    source = core.read_json(history_dir / "manifest.json")
    if source.get("terminal_cursor_proven") is not True or source.get("model_sample_gate_met") is not True: raise ValueError("earnings history gate not met")
    events = core.read_json(history_dir / "strict-earnings-events.json"); output_dir.mkdir(parents=True, exist_ok=True); history_path = output_dir / "price-history.json"; request_path = output_dir / "request-log.json"; histories = core.read_json(history_path) if history_path.exists() else {}; requests = core.read_json(request_path) if request_path.exists() else []; groups = []
    for index in range(0, len(events), 10):
        batch = events[index:index + 10]; tokens = [str(token) for event in batch for token in event["tokens"].values()]
        if all(token in histories for token in tokens): continue
        starts = [parse_time(event["earnings_release_at"]) - timedelta(hours=36) for event in batch]; ends = [parse_time(event["earnings_release_at"]) - timedelta(minutes=55) for event in batch]; groups.append((tokens, min(starts), max(ends)))
    try:
        for number, (tokens, start, end) in enumerate(groups[:max_batches], 1):
            body = {"markets": tokens, "start_ts": int(start.timestamp()), "end_ts": int(end.timestamp()), "fidelity": 5}
            try:
                payload = public.post_json(f"{CLOB}/batch-prices-history", body, timeout=12, retries=1); result = payload.get("history") if isinstance(payload, dict) else {}
                for token in tokens: histories[token] = {"history": sorted((result.get(token) or []) if isinstance(result, dict) else [], key=lambda row: int(row["t"])), "fidelity_minutes": 5, "attempted_at": core.now_iso()}
                requests.append({"status": "ok", "tokens": tokens, "start_ts": body["start_ts"], "end_ts": body["end_ts"], "nonempty": sum(bool(histories[token]["history"]) for token in tokens), "created_at": core.now_iso()})
            except Exception as exc: requests.append({"status": "failed", "tokens": tokens, "error": f"{type(exc).__name__}:{exc}", "created_at": core.now_iso()})
            if number % 10 == 0: core.write_json(history_path, histories); core.write_json(request_path, requests)
    finally: core.write_json(history_path, histories); core.write_json(request_path, requests)
    token_windows = {}
    for event in events:
        release = parse_time(event["earnings_release_at"])
        for token in event["tokens"].values(): token_windows[str(token)] = (release - timedelta(hours=36), release - timedelta(minutes=55))
    for token, (start, end) in token_windows.items():
        existing = histories.get(token) or {}
        if existing.get("history") or existing.get("explicit_retry_attempted_at"): continue
        url = f"{CLOB}/prices-history?{urlencode({'market': token, 'startTs': int(start.timestamp()), 'endTs': int(end.timestamp()), 'fidelity': 5})}"
        try:
            payload = public.get_json(url, timeout=20, retries=1); rows = sorted(payload.get("history") or [], key=lambda row: int(row["t"])); histories[token] = {**existing, "history": rows, "fidelity_minutes": 5, "explicit_retry_attempted_at": core.now_iso()}; requests.append({"status": "ok_explicit_retry", "token": token, "url": url, "points": len(rows), "created_at": core.now_iso()})
        except Exception as exc:
            histories[token] = {**existing, "explicit_retry_attempted_at": core.now_iso(), "explicit_retry_error": f"{type(exc).__name__}:{exc}"}; requests.append({"status": "failed_explicit_retry", "token": token, "url": url, "error": f"{type(exc).__name__}:{exc}", "created_at": core.now_iso()})
        if len(requests) % 20 == 0: core.write_json(history_path, histories); core.write_json(request_path, requests)
    core.write_json(history_path, histories); core.write_json(request_path, requests)
    observations = cutoff_observations(events, histories); core.write_json(output_dir / "cutoff-observations.json", observations); complete = Counter(row["cutoff_label"] for row in observations if row["status"] == "complete"); tokens = {str(token) for event in events for token in event["tokens"].values()}; nonempty = sum(bool((histories.get(token) or {}).get("history")) for token in tokens); relative = ["price-history.json", "request-log.json", "cutoff-observations.json"]
    manifest = {"schema_version": "polymarket-corporate-earnings-cutoff-prices-v1", "created_at": core.now_iso(), "source_history_manifest_sha256": sha256(history_dir / "manifest.json"), "official_source": f"{CLOB}/batch-prices-history", "fidelity_minutes": 5, "eligible_events": len(events), "target_tokens": len(tokens), "nonempty_token_histories": nonempty, "token_history_coverage_pct": round(100 * nonempty / len(tokens), 4) if tokens else 0, "complete_observations_by_cutoff": dict(complete), "execution_evidence_eligible": False, "execution_limit": "historical trades lack spread, depth, executable VWAP and impact; forward books required", "data_status": "ok" if tokens and nonempty / len(tokens) >= .95 and all(complete[label] / len(events) >= .90 for label in CUTOFFS) else "degraded", "files": [{"path": name, "bytes": (output_dir / name).stat().st_size, "sha256": sha256(output_dir / name)} for name in relative], "research_only": True, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}; core.write_json(output_dir / "manifest.json", manifest); return manifest


def candidate_probability(name: str, event: dict[str, Any], state: dict[str, Any]) -> float:
    global_rate = (state["global_yes"] + 1) / (state["global_n"] + 2); basis = event["eps_basis"]; basis_rate = (state["basis_yes"].get(basis, 0) + 1) / (state["basis_n"].get(basis, 0) + 2); ticker = event["ticker"]; ticker_yes = state["ticker_yes"].get(ticker, 0); ticker_n = state["ticker_n"].get(ticker, 0)
    if name == "global_beta_1_1": return global_rate
    if name == "eps_basis_beta_1_1": return basis_rate
    strength = int(name.rsplit("_", 1)[-1]); prior = basis_rate if "ticker_basis" in name else global_rate
    return (ticker_yes + strength * prior) / (ticker_n + strength)


def online_predictions(events: list[dict[str, Any]], candidates: list[str]) -> dict[str, dict[str, float]]:
    predictions = {name: {} for name in candidates}; state = {"global_yes": 0, "global_n": 0, "basis_yes": {}, "basis_n": {}, "ticker_yes": {}, "ticker_n": {}}
    by_date: dict[str, list[dict[str, Any]]] = {}
    for event in events: by_date.setdefault(str(event["earnings_release_at"])[:10], []).append(event)
    for release_date in sorted(by_date):
        batch = by_date[release_date]
        for event in batch:
            for name in candidates: predictions[name][event["event_id"]] = candidate_probability(name, event, state)
        for event in batch:
            actual = int(event["winning_outcome"] == "Yes"); basis = event["eps_basis"]; ticker = event["ticker"]; state["global_yes"] += actual; state["global_n"] += 1; state["basis_yes"][basis] = state["basis_yes"].get(basis, 0) + actual; state["basis_n"][basis] = state["basis_n"].get(basis, 0) + 1; state["ticker_yes"][ticker] = state["ticker_yes"].get(ticker, 0) + actual; state["ticker_n"][ticker] = state["ticker_n"].get(ticker, 0) + 1
    return predictions


def lower_95(rows: list[dict[str, Any]], values: list[float]) -> float | None:
    grouped: dict[str, list[float]] = {}
    for row, value in zip(rows, values): grouped.setdefault(row["release_date"], []).append(value)
    means = [statistics.mean(group) for group in grouped.values()]
    return statistics.mean(means) - 1.96 * statistics.stdev(means) / math.sqrt(len(means)) if len(means) >= 2 else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    brier = [row["market_brier"] - row["model_brier"] for row in rows]; logs = [row["market_log_loss"] - row["model_log_loss"] for row in rows]
    return {"events": len(rows), "distinct_dates": len({row["release_date"] for row in rows}), "model_brier": statistics.mean(row["model_brier"] for row in rows), "market_brier": statistics.mean(row["market_brier"] for row in rows), "brier_improvement": statistics.mean(brier), "brier_improvement_lower_95_date_clustered": lower_95(rows, brier), "model_log_loss": statistics.mean(row["model_log_loss"] for row in rows), "market_log_loss": statistics.mean(row["market_log_loss"] for row in rows), "log_loss_improvement": statistics.mean(logs), "log_loss_improvement_lower_95_date_clustered": lower_95(rows, logs)}


def walk_forward(history_dir: Path, price_dir: Path, output: Path, report: Path) -> dict[str, Any]:
    protocol = core.read_json(PROTOCOL)
    for directory in (history_dir, price_dir):
        if core.read_json(directory / "manifest.json").get("data_status") != "ok": raise ValueError(f"degraded earnings input: {directory}")
    events = sorted(core.read_json(history_dir / "strict-earnings-events.json"), key=lambda row: (row["earnings_release_at"], row["event_id"])); observations = core.read_json(price_dir / "cutoff-observations.json"); candidates = [row["name"] for row in protocol["model_v1_frozen_candidates"]]; predictions = online_predictions(events, candidates); dates = sorted({str(row["earnings_release_at"])[:10] for row in events})
    if len(dates) < 90: raise ValueError("fewer than 90 release dates")
    split = {"development": set(dates[:-60]), "validation": set(dates[-60:-30]), "final_holdout": set(dates[-30:])}; development = [row for row in events if str(row["earnings_release_at"])[:10] in split["development"]]; candidate_results = []
    for name in candidates:
        errors = [(predictions[name][row["event_id"]] - float(row["winning_outcome"] == "Yes")) ** 2 for row in development]; candidate_results.append({"name": name, "development_brier": statistics.mean(errors), "development_events": len(errors)})
    preference = {name: index for index, name in enumerate(candidates)}; selected = min(candidate_results, key=lambda row: (row["development_brier"], preference[row["name"]])); obs_map = {(row["event_id"], row["cutoff_label"]): row for row in observations if row["status"] == "complete"}; score_rows = []; eps = 1e-9
    for event in events:
        release_date = str(event["earnings_release_at"])[:10]; segment = next(name for name, values in split.items() if release_date in values); actual = float(event["winning_outcome"] == "Yes"); model_p = predictions[selected["name"]][event["event_id"]]
        for cutoff in CUTOFFS:
            observation = obs_map.get((event["event_id"], cutoff))
            if not observation: continue
            market_p = float(observation["normalized_market_probabilities"]["Yes"]); score_rows.append({"event_id": event["event_id"], "release_date": release_date, "segment": segment, "cutoff": cutoff, "actual": actual, "model_probability": model_p, "market_probability": market_p, "model_brier": (model_p - actual) ** 2, "market_brier": (market_p - actual) ** 2, "model_log_loss": -(actual * math.log(max(eps, model_p)) + (1 - actual) * math.log(max(eps, 1 - model_p))), "market_log_loss": -(actual * math.log(max(eps, market_p)) + (1 - actual) * math.log(max(eps, 1 - market_p)))})
    summaries = []
    for segment in split:
        for cutoff in CUTOFFS:
            rows = [row for row in score_rows if row["segment"] == segment and row["cutoff"] == cutoff]; summaries.append({"segment": segment, "cutoff": cutoff, **summarize(rows)})
    validation = [row for row in summaries if row["segment"] == "validation"]; final = [row for row in summaries if row["segment"] == "final_holdout"]; enough_dates = all(row["distinct_dates"] >= 30 for row in [*validation, *final]); brier_pass = enough_dates and all((row["brier_improvement_lower_95_date_clustered"] or -1) > 0 for row in [*validation, *final]); log_pass = enough_dates and all((row["log_loss_improvement_lower_95_date_clustered"] or -1) > 0 for row in [*validation, *final]); promoted = brier_pass or log_pass
    payload = {"schema_version": "polymarket-corporate-earnings-beta-binomial-v1", "created_at": core.now_iso(), "model_version": "pm-corporate-earnings-beta-binomial-v1", "protocol_created_at": protocol["created_at"], "events": len(events), "distinct_release_dates": len(dates), "split": {name: {"dates": len(values), "events": sum(str(row["earnings_release_at"])[:10] in values for row in events)} for name, values in split.items()}, "candidate_results": candidate_results, "selected": selected, "metrics": summaries, "same_date_events_never_cross_segments": True, "outcomes_from_same_date_not_used_for_each_other": True, "parameter_selection_scope": "development only", "final_holdout_first_inspected_at": core.now_iso(), "research_promotion_candidate": promoted, "promotion_status": "manual_review_and_forward_execution_evidence_required" if promoted else "research_fail_no_sustained_date_clustered_oos_improvement", "model_outputs_are_true_probabilities": False, "paper_estimates_allowed": False, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}; core.write_json(output, payload)
    lines = ["# Polymarket Corporate Earnings Model V1", "", f"- Selected: `{selected['name']}`", f"- Events / dates: {len(events)} / {len(dates)}", f"- Promotion: `{payload['promotion_status']}`", "", "| Segment | Cutoff | Events | Dates | Model Brier | Market Brier | Improvement | 95% lower |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in summaries: lines.append(f"| {row['segment']} | {row['cutoff']} | {row['events']} | {row['distinct_dates']} | {row['model_brier']:.5f} | {row['market_brier']:.5f} | {row['brier_improvement']:.5f} | {row['brier_improvement_lower_95_date_clustered']:.5f} |")
    lines.extend(["", "All model scores use completed earlier release dates only. Historical trades are not execution evidence; no paper estimate is emitted without forward books and friction.", ""]); report.write_text("\n".join(lines), encoding="utf-8"); return payload


def self_test() -> dict[str, Any]:
    events = [{"event_id":"a","ticker":"X","eps_basis":"gaap","earnings_release_at":"2026-01-01T00:00:00Z","winning_outcome":"Yes"},{"event_id":"b","ticker":"X","eps_basis":"gaap","earnings_release_at":"2026-01-01T12:00:00Z","winning_outcome":"No"},{"event_id":"c","ticker":"X","eps_basis":"gaap","earnings_release_at":"2026-01-02T00:00:00Z","winning_outcome":"Yes"}]
    p = online_predictions(events, ["global_beta_1_1"])["global_beta_1_1"]; assert p["a"] == p["b"] == .5 and abs(p["c"] - .5) < 1e-12
    return {"status":"pass","tests":["same_date_no_leakage","beta_prior"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True); prices = sub.add_parser("fetch-cutoff-prices"); prices.add_argument("--history-dir", default=str(ROOT / "cache/current_corporate_earnings_history")); prices.add_argument("--output-dir", default=str(ROOT / "cache/current_corporate_earnings_cutoff_prices")); prices.add_argument("--max-batches", type=int, default=100); walk = sub.add_parser("walk-forward"); walk.add_argument("--history-dir", default=str(ROOT / "cache/current_corporate_earnings_history")); walk.add_argument("--price-dir", default=str(ROOT / "cache/current_corporate_earnings_cutoff_prices")); walk.add_argument("--output", default=str(ROOT / "experiments/current-corporate-earnings-model-v1.json")); walk.add_argument("--report", default=str(ROOT / "reports/CURRENT_CORPORATE_EARNINGS_MODEL.md")); sub.add_parser("self-test"); args = parser.parse_args()
    if args.command == "fetch-cutoff-prices": payload = fetch_cutoff_prices(Path(args.history_dir), Path(args.output_dir), args.max_batches)
    elif args.command == "walk-forward": payload = walk_forward(Path(args.history_dir), Path(args.price_dir), Path(args.output), Path(args.report))
    else: payload = self_test()
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
