#!/usr/bin/env python3
"""Title-isolated, public-only historical discovery for esports match winners."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
TITLES = {
    "cs2": {"sport": "cs2", "tag_id": 100780, "series_id": "10310", "resolution": "https://hltv.org"},
    "dota2": {"sport": "dota2", "tag_id": 102366, "series_id": "10309", "resolution": "https://www.liquipedia.net/dota2/Main_Page"},
    "lol": {"sport": "lol", "tag_id": 65, "series_id": "10311", "resolution": "https://liquipedia.net/leagueoflegends/Main_Page"},
    "valorant": {"sport": "val", "tag_id": 101672, "series_id": "10369", "resolution": "https://liquipedia.net/valorant/Main_Page"},
}
DERIVATIVE = re.compile(r"\b(map|game)\s*(?:1|2|3|4|5|one|two|three|four|five)\b|\b(first blood|round handicap|total rounds|pistol round)\b", re.I)
WINDOW_START = "2026-01-01T00:00:00Z"
WINDOW_END = "2026-07-12T00:00:00Z"
CUTOFFS = {"T-24h": timedelta(hours=24), "T-60m": timedelta(minutes=60)}
MAX_PRICE_AGE_SECONDS = {"T-24h": 6 * 3600, "T-60m": 30 * 60}


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
    return module


core = load("esports_core", ROOT / "scripts/polymarket_alpha.py")
public = load("esports_public", ROOT / "scripts/polymarket_public_data.py")
baseball = load("esports_shared", ROOT / "scripts/polymarket_baseball_lab.py")


def official_metadata() -> dict[str, dict[str, Any]]:
    rows = public.get_json(f"{GAMMA}/sports", timeout=25, retries=2)
    by_sport = {str(row.get("sport")): row for row in rows}
    result = {}
    for title, contract in TITLES.items():
        row = by_sport.get(contract["sport"])
        if not row:
            raise ValueError(f"official sports metadata missing {title}")
        tags = {int(value) for value in str(row.get("tags") or "").split(",") if value.strip().isdigit()}
        if contract["tag_id"] not in tags or str(row.get("series")) != contract["series_id"] or str(row.get("resolution")) != contract["resolution"]:
            raise ValueError(f"official sports metadata contract changed for {title}")
        result[title] = row
    return result


def rule_complete(event: dict[str, Any], market: dict[str, Any]) -> bool:
    text = " ".join(str(value or "") for value in (event.get("resolutionSource"), event.get("description"), market.get("resolutionSource"), market.get("description"))).lower()
    return "resolution source" in text or "official information" in text or "official final" in text


def parse_match_winner(title: str, event: dict[str, Any], market: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if str(market.get("sportsMarketType") or "").lower() != "moneyline":
        return None, "not_moneyline"
    question = str(market.get("question") or "")
    combined = f"{event.get('title', '')} {question}"
    if DERIVATIVE.search(question):
        return None, "map_or_round_derivative"
    if re.search(r"\b(in-game|live betting|tournament winner|win the tournament|win .* championship)\b", combined, re.I):
        return None, "live_or_futures_market"
    game_start = baseball.parse_time(market.get("gameStartTime"))
    market_start = baseball.parse_time(market.get("startDate") or event.get("startDate"))
    if not game_start:
        return None, "game_start_missing"
    if not market_start or market_start >= game_start - timedelta(minutes=60):
        return None, "not_available_before_t60"
    outcomes, tokens = public.parse_jsonish(market.get("outcomes")), public.token_map(market)
    if len(outcomes) != 2 or len(tokens) != 2 or set(outcomes) != set(tokens):
        return None, "two_outcome_token_contract_failed"
    resolution = public.infer_resolution(market)
    if resolution.get("winning_outcome") not in outcomes:
        return None, "terminal_resolution_missing"
    if not rule_complete(event, market):
        return None, "resolution_rule_incomplete"
    event_id = str(event.get("id") or "")
    if not event_id:
        return None, "event_id_missing"
    official_game_id = event.get("gameId") or market.get("gameId")
    identity = str(official_game_id) if official_game_id else f"event:{event_id}:{game_start.isoformat()}"
    return {
        "title": title, "event_id": event_id, "match_id": identity,
        "match_identity_source": "official_game_id" if official_game_id else "event_id_plus_game_start",
        "market_id": str(market.get("id")), "condition_id": market.get("conditionId"),
        "question": question, "slug": market.get("slug"),
        "match_start_time": game_start.isoformat(), "market_start_time": market_start.isoformat(),
        "outcomes": outcomes, "tokens": tokens, "winning_outcome": resolution["winning_outcome"],
        "available_at_t24": market_start <= game_start - timedelta(hours=24),
        "available_at_t60": True, "pregame_full_match_only": True,
    }, None


def extract(title: str, events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows, excluded, seen = [], Counter(), set()
    for event in events:
        for market in event.get("markets") or []:
            row, reason = parse_match_winner(title, event, market)
            if reason:
                excluded[reason] += 1; continue
            assert row
            if row["match_id"] in seen:
                excluded["duplicate_match_id"] += 1; continue
            seen.add(row["match_id"]); rows.append(row)
    rows.sort(key=lambda row: (row["match_start_time"], row["match_id"]))
    return rows, dict(sorted(excluded.items()))


def load_events(directory: Path) -> list[dict[str, Any]]:
    events = {}
    for path in sorted((directory / "pages").glob("page-*.json")):
        for event in (core.read_json(path).get("events") or []):
            events[str(event.get("id"))] = event
    return sorted(events.values(), key=lambda row: int(row.get("id") or 0))


def latest_before(history: list[dict[str, Any]], cutoff, market_start) -> dict[str, Any] | None:
    """Return only a trade point that was observable by the frozen cutoff."""
    rows = [row for row in history if market_start.timestamp() <= float(row.get("t", 0)) <= cutoff.timestamp()]
    return max(rows, key=lambda row: float(row["t"])) if rows else None


def build_cutoff_observations(eligible: list[dict[str, Any]], histories: dict[str, Any]) -> list[dict[str, Any]]:
    observations = []
    for market in eligible:
        match_start = baseball.parse_time(market["match_start_time"])
        market_start = baseball.parse_time(market["market_start_time"])
        assert match_start and market_start
        for label, delta in CUTOFFS.items():
            cutoff = match_start - delta
            outcome_rows, failures = {}, []
            if market_start > cutoff:
                failures.append("market_not_open_at_cutoff")
            for outcome, token in market["tokens"].items():
                point = latest_before((histories.get(str(token)) or {}).get("history") or [], cutoff, market_start)
                if point is None:
                    failures.append(f"price_missing:{outcome}")
                    continue
                age = cutoff.timestamp() - float(point["t"])
                if age > MAX_PRICE_AGE_SECONDS[label]:
                    failures.append(f"price_stale:{outcome}")
                outcome_rows[outcome] = {
                    "token_id": str(token), "price": float(point["p"]),
                    "timestamp": int(point["t"]), "age_seconds": age,
                }
            raw_sum = sum(row["price"] for row in outcome_rows.values()) if len(outcome_rows) == 2 else None
            normalized = (
                {outcome: row["price"] / raw_sum for outcome, row in outcome_rows.items()}
                if raw_sum and raw_sum > 0 and len(outcome_rows) == 2 else {}
            )
            observations.append({
                "title": market["title"], "match_id": market["match_id"],
                "event_id": market["event_id"], "market_id": market["market_id"],
                "cutoff_label": label, "cutoff_at": cutoff.isoformat(),
                "match_start_time": match_start.isoformat(), "winning_outcome": market["winning_outcome"],
                "outcome_prices": outcome_rows, "raw_probability_sum": raw_sum,
                "normalized_market_probabilities": normalized,
                "status": "complete" if not failures else "excluded", "exclusion_reasons": failures,
                "future_price_data_used": False, "execution_evidence_eligible": False,
            })
    return observations


def fetch_cutoff_prices(title: str, history_root: Path, output_dir: Path, max_batches: int) -> dict[str, Any]:
    history_dir = history_root / title
    source = core.read_json(history_dir / "manifest.json")
    if source.get("terminal_cursor_proven") is not True:
        raise ValueError(f"{title} discovery has no terminal cursor proof")
    eligible = core.read_json(history_dir / "eligible-match-winners.json")
    output_dir.mkdir(parents=True, exist_ok=True)
    histories_path, requests_path = output_dir / "price-history.json", output_dir / "request-log.json"
    histories = core.read_json(histories_path) if histories_path.exists() else {}
    requests = core.read_json(requests_path) if requests_path.exists() else []
    groups = []
    for index in range(0, len(eligible), 10):
        markets = eligible[index:index + 10]
        tokens = [str(token) for market in markets for token in market["tokens"].values()]
        if all(token in histories for token in tokens):
            continue
        starts = [baseball.parse_time(market["match_start_time"]) - timedelta(hours=30) for market in markets]
        ends = [baseball.parse_time(market["match_start_time"]) - timedelta(minutes=55) for market in markets]
        groups.append((tokens, min(starts), max(ends)))
    processed = 0
    try:
        for tokens, start, end in groups[:max_batches]:
            body = {"markets": tokens, "start_ts": int(start.timestamp()), "end_ts": int(end.timestamp()), "fidelity": 5}
            try:
                payload = public.post_json(f"{CLOB}/batch-prices-history", body, timeout=12, retries=1)
                result = payload.get("history") if isinstance(payload, dict) else {}
                for token in tokens:
                    rows = result.get(token, []) if isinstance(result, dict) else []
                    histories[token] = {"history": sorted(rows, key=lambda row: int(row["t"])), "fidelity_minutes": 5, "attempted_at": core.now_iso()}
                requests.append({"status": "ok", "tokens": tokens, "token_count": len(tokens), "start_ts": body["start_ts"], "end_ts": body["end_ts"], "nonempty": sum(bool((histories.get(token) or {}).get("history")) for token in tokens), "created_at": core.now_iso()})
            except Exception as exc:
                requests.append({"status": "failed", "tokens": tokens, "token_count": len(tokens), "start_ts": body["start_ts"], "end_ts": body["end_ts"], "error": f"{type(exc).__name__}:{exc}", "created_at": core.now_iso()})
            processed += 1
            if processed % 10 == 0:
                core.write_json(histories_path, histories); core.write_json(requests_path, requests)
    finally:
        core.write_json(histories_path, histories); core.write_json(requests_path, requests)
    observations = build_cutoff_observations(eligible, histories)
    core.write_json(output_dir / "cutoff-observations.json", observations)
    complete = Counter(row["cutoff_label"] for row in observations if row["status"] == "complete")
    target_tokens = {str(token) for market in eligible for token in market["tokens"].values()}
    nonempty_tokens = sum(bool((histories.get(token) or {}).get("history")) for token in target_tokens)
    token_coverage = nonempty_tokens / len(target_tokens) if target_tokens else 0.0
    eligible_t24 = sum(bool(market.get("available_at_t24")) for market in eligible)
    t24_coverage = complete["T-24h"] / eligible_t24 if eligible_t24 else 0.0
    t60_coverage = complete["T-60m"] / len(eligible) if eligible else 0.0
    relative = ["price-history.json", "request-log.json", "cutoff-observations.json"]
    manifest = {
        "schema_version": "polymarket-esports-cutoff-price-manifest-v1", "created_at": core.now_iso(),
        "title": title, "source_history_manifest_sha256": baseball.sha256(history_dir / "manifest.json"),
        "official_sources": [f"{CLOB}/batch-prices-history"], "fidelity_minutes": 5,
        "target_tokens": len(target_tokens), "nonempty_token_histories": nonempty_tokens,
        "token_history_coverage_pct": round(100 * token_coverage, 4),
        "eligible_matches": len(eligible), "eligible_t24": eligible_t24,
        "complete_observations_by_cutoff": dict(complete),
        "complete_t24_coverage_pct": round(100 * t24_coverage, 4),
        "complete_t60_coverage_pct": round(100 * t60_coverage, 4),
        "observation_count": len(observations), "execution_evidence_eligible": False,
        "execution_limit": "trade-price history has no historical spread, depth, VWAP, or slippage; forward books are required for execution evidence",
        "data_status": "ok" if token_coverage >= 0.95 and t24_coverage >= 0.90 and t60_coverage >= 0.90 else "degraded",
        "files": baseball.manifest_files(output_dir, relative), "research_only": True,
        "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(output_dir / "manifest.json", manifest)
    return manifest


def discover_title(title: str, root: Path, metadata: dict[str, Any], max_pages: int, wall_clock_seconds: float) -> dict[str, Any]:
    contract = TITLES[title]; directory = root / title; (directory / "pages").mkdir(parents=True, exist_ok=True)
    query = {"endpoint": f"{GAMMA}/events/keyset", "closed": True, "series_id": int(contract["series_id"]), "start_time_min": WINDOW_START, "end_date_max": WINDOW_END, "limit": 100}
    state_path, log_path = directory / "state.json", directory / "request-log.json"
    state = core.read_json(state_path) if state_path.exists() else {"schema_version": "polymarket-esports-history-state-v1", "contract": query, "next_cursor": None, "terminal_cursor_proven": False, "pages_fetched": 0}
    if state.get("contract") != query:
        raise ValueError(f"existing {title} history cache contract mismatch")
    requests = core.read_json(log_path) if log_path.exists() else []
    core.write_json(directory / "sports-metadata.json", metadata); core.write_json(state_path, state); core.write_json(log_path, requests)
    started = time.monotonic(); fetched = 0
    while not state["terminal_cursor_proven"] and fetched < max_pages and time.monotonic() - started < wall_clock_seconds:
        page_limit = int(state.get("adaptive_page_size") or 100)
        params: dict[str, Any] = {"closed": "true", "series_id": int(contract["series_id"]), "start_time_min": WINDOW_START, "end_date_max": WINDOW_END, "limit": page_limit}
        if state.get("next_cursor"): params["after_cursor"] = state["next_cursor"]
        url = f"{GAMMA}/events/keyset?{urlencode(params)}"
        try:
            payload = public.get_json(url, timeout=25, retries=2); events = payload.get("events")
            if not isinstance(events, list): raise ValueError("keyset response missing events")
            number = int(state["pages_fetched"]) + 1; cursor = payload.get("next_cursor")
            core.write_json(directory / "pages" / f"page-{number:04d}.json", {"page_number": number, "request_url": url, "events": events, "next_cursor": cursor})
            requests.append({"page_number": number, "url": url, "status": "ok", "requested_limit": page_limit, "rows": len(events), "terminal": not bool(cursor), "created_at": core.now_iso()})
            state.update({"pages_fetched": number, "next_cursor": cursor, "terminal_cursor_proven": not bool(cursor), "updated_at": core.now_iso()})
            core.write_json(log_path, requests); core.write_json(state_path, state); fetched += 1
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "requested_limit": page_limit, "error": f"{type(exc).__name__}:{exc}", "created_at": core.now_iso()})
            if page_limit > 20:
                fallback_params = {**params, "limit": 20}; fallback_url = f"{GAMMA}/events/keyset?{urlencode(fallback_params)}"
                try:
                    payload = public.get_json(fallback_url, timeout=25, retries=2); events = payload.get("events")
                    if not isinstance(events, list): raise ValueError("fallback keyset response missing events")
                    number = int(state["pages_fetched"]) + 1; cursor = payload.get("next_cursor")
                    core.write_json(directory / "pages" / f"page-{number:04d}.json", {"page_number": number, "request_url": fallback_url, "events": events, "next_cursor": cursor})
                    requests.append({"page_number": number, "url": fallback_url, "status": "ok_fallback", "requested_limit": 20, "rows": len(events), "terminal": not bool(cursor), "created_at": core.now_iso()})
                    state.update({"pages_fetched": number, "next_cursor": cursor, "terminal_cursor_proven": not bool(cursor), "adaptive_page_size": 20, "updated_at": core.now_iso()})
                    core.write_json(log_path, requests); core.write_json(state_path, state); fetched += 1; continue
                except Exception as fallback_exc:
                    requests.append({"url": fallback_url, "status": "failed_fallback", "requested_limit": 20, "error": f"{type(fallback_exc).__name__}:{fallback_exc}", "created_at": core.now_iso()})
            core.write_json(log_path, requests); break
    events = load_events(directory); eligible, excluded = extract(title, events); core.write_json(directory / "eligible-match-winners.json", eligible)
    relative = ["sports-metadata.json", "state.json", "request-log.json", "eligible-match-winners.json"] + [str(path.relative_to(directory)) for path in sorted((directory / "pages").glob("page-*.json"))]
    payload = {
        "schema_version": "polymarket-esports-title-history-manifest-v1", "created_at": core.now_iso(), "title": title,
        "tag_id": contract["tag_id"], "series_id": contract["series_id"], "official_resolution_source": contract["resolution"],
        "frozen_window_start": WINDOW_START, "frozen_window_end": WINDOW_END,
        "pages_fetched": state["pages_fetched"], "terminal_cursor_proven": state["terminal_cursor_proven"],
        "events_fetched": len(events), "markets_fetched": sum(len(event.get("markets") or []) for event in events),
        "eligible_match_winners": len(eligible), "eligible_t24": sum(row["available_at_t24"] for row in eligible),
        "independent_match_ids": len({row["match_id"] for row in eligible}), "excluded_reason_counts": excluded,
        "data_status": "ok" if state["terminal_cursor_proven"] and len(eligible) >= 30 else "degraded",
        "files": baseball.manifest_files(directory, relative), "research_only": True,
        "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(directory / "manifest.json", payload); return payload


def probe_sources(output: Path) -> dict[str, Any]:
    probes = []
    for title, contract in TITLES.items():
        url = contract["resolution"]
        try:
            request = Request(url, headers={"User-Agent": "polymarket-paper-research/1.0 (public read-only)"})
            with urlopen(request, timeout=20) as response:
                probes.append({"title": title, "url": url, "status": "accessible", "http_status": response.status, "content_type": response.headers.get("Content-Type")})
        except Exception as exc:
            probes.append({"title": title, "url": url, "status": "inaccessible", "error": f"{type(exc).__name__}:{exc}"})
    payload = {"schema_version": "polymarket-esports-source-probe-v1", "created_at": core.now_iso(), "probes": probes,
               "probe_is_model_evidence": False, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}
    core.write_json(output, payload); return payload


def freeze_title_protocol(selected: str | None, output: Path) -> dict[str, Any]:
    payload = {
        "schema_version": "polymarket-esports-title-model-protocol-v1", "created_at": core.now_iso(),
        "family": "esports", "title": selected, "status": "protocol_frozen_model_not_implemented",
        "history_window": {"start": WINDOW_START, "end": WINDOW_END},
        "contract_scope": "pregame full-match winner only; maps, games, rounds, live, handicaps, totals, props, futures and parlays excluded",
        "independence_unit": "one scheduled match ID", "forecast_cutoffs": ["T-24h", "T-60m"],
        "external_result_source": TITLES.get(selected or "", {}).get("resolution"),
        "model_v1_frozen_spec": {"version": f"pm-{selected}-elo-v1" if selected else None, "initial_rating": 1500.0, "k_factor_grid": [10.0, 20.0, 30.0], "selection_metric": "development Brier score", "parameter_selection_scope": "development 60% only"},
        "chronological_split": {"development": "earliest 60%", "validation": "next 20%", "final_holdout": "latest 20%; sealed"},
        "paper_entry_eligible": False, "automatic_promotion": False, "paper_estimates_emitted": False,
        "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(output, payload); return payload


def aggregate(root: Path, manifests: list[dict[str, Any]], source_probe: dict[str, Any], output: Path, report: Path, protocol: Path) -> dict[str, Any]:
    access = {row["title"]: row for row in source_probe.get("probes") or []}
    enriched = [{**row, "source_access_status": (access.get(row["title"]) or {}).get("status", "missing")} for row in manifests]
    ranked = sorted(enriched, key=lambda row: (row["data_status"] == "ok", row["source_access_status"] == "accessible", row["eligible_t24"], row["eligible_match_winners"]), reverse=True)
    eligible = [row for row in ranked if row["data_status"] == "ok" and row["source_access_status"] == "accessible"]
    selected = eligible[0]["title"] if eligible else None
    payload = {"schema_version": "polymarket-esports-history-audit-v1", "created_at": core.now_iso(), "titles": ranked,
               "selected_title_for_model_research": selected, "selection_is_alpha_evidence": False,
               "source_probe_created_at": source_probe.get("created_at"), "selection_rule": "terminal history and accessible external result source, then maximum T-24h sample count",
               "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}
    core.write_json(output, payload)
    freeze_title_protocol(selected, protocol)
    lines = ["# Polymarket Esports Historical Audit", "", f"- Selected title for model research: `{selected}`", "- Selection means data feasibility only, never Alpha or paper eligibility.", "", "| Title | Status | Events | Markets | Match winners | T-24h |", "|---|---|---:|---:|---:|---:|"]
    for row in ranked: lines.append(f"| {row['title']} ({row['source_access_status']}) | {row['data_status']} | {row['events_fetched']} | {row['markets_fetched']} | {row['eligible_match_winners']} | {row['eligible_t24']} |")
    lines.extend(["", "Titles remain isolated. Maps, rounds, live markets, handicaps, totals, props and futures are excluded.", ""])
    report.parent.mkdir(parents=True, exist_ok=True); report.write_text("\n".join(lines), encoding="utf-8"); return payload


def self_test() -> dict[str, Any]:
    market = {"id": "m1", "question": "Alpha vs Beta", "sportsMarketType": "moneyline", "gameStartTime": "2026-07-12T20:00:00Z", "startDate": "2026-07-10T00:00:00Z", "description": "Resolution source: official information.", "outcomes": '["Alpha", "Beta"]', "outcomePrices": '["1", "0"]', "clobTokenIds": '["a", "b"]', "closed": True}
    row, reason = parse_match_winner("valorant", {"id": "e1"}, market); assert reason is None and row and row["available_at_t24"]
    derivative = {**market, "question": "Alpha vs Beta - Map 1 Winner"}; assert parse_match_winner("valorant", {"id": "e1"}, derivative)[1] == "map_or_round_derivative"
    cutoff = baseball.parse_time("2026-07-12T19:00:00Z"); market_start = baseball.parse_time("2026-07-10T00:00:00Z")
    assert cutoff and market_start
    point = latest_before([{"t": int(cutoff.timestamp()) - 10, "p": 0.55}, {"t": int(cutoff.timestamp()) + 10, "p": 0.99}], cutoff, market_start)
    assert point and point["p"] == 0.55
    return {"status": "pass", "tests": ["full_match_moneyline_parse", "map_derivative_exclusion", "post_cutoff_price_exclusion"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover-history"); discover.add_argument("--title", choices=["all", *TITLES], default="all"); discover.add_argument("--output-dir", default=str(ROOT / "cache/current_esports_2026_history")); discover.add_argument("--max-pages", type=int, default=100); discover.add_argument("--wall-clock-seconds", type=float, default=240)
    prices = sub.add_parser("fetch-cutoff-prices"); prices.add_argument("--title", choices=list(TITLES), required=True); prices.add_argument("--history-dir", default=str(ROOT / "cache/current_esports_2026_history")); prices.add_argument("--output-dir"); prices.add_argument("--max-batches", type=int, default=200)
    audit = sub.add_parser("audit"); audit.add_argument("--history-dir", default=str(ROOT / "cache/current_esports_2026_history"))
    sub.add_parser("self-test"); args = parser.parse_args()
    if args.command == "self-test": payload = self_test()
    elif args.command == "audit":
        root = Path(args.history_dir); manifests = [core.read_json(root / title / "manifest.json") for title in TITLES]
        source_probe = probe_sources(ROOT / "experiments/current-esports-source-probe.json")
        payload = aggregate(root, manifests, source_probe, ROOT / "experiments/current-esports-history-audit.json", ROOT / "reports/CURRENT_ESPORTS_HISTORY_AUDIT.md", ROOT / "experiments/lol-research-protocol-v1.json")
    elif args.command == "fetch-cutoff-prices":
        output = Path(args.output_dir) if args.output_dir else ROOT / f"cache/current_{args.title}_cutoff_prices"
        payload = fetch_cutoff_prices(args.title, Path(args.history_dir), output, args.max_batches)
    else:
        metadata = official_metadata(); titles = list(TITLES) if args.title == "all" else [args.title]
        manifests = [discover_title(title, Path(args.output_dir), metadata[title], args.max_pages, args.wall_clock_seconds) for title in titles]
        if args.title == "all":
            source_probe = probe_sources(ROOT / "experiments/current-esports-source-probe.json")
            payload = aggregate(Path(args.output_dir), manifests, source_probe, ROOT / "experiments/current-esports-history-audit.json", ROOT / "reports/CURRENT_ESPORTS_HISTORY_AUDIT.md", ROOT / "experiments/lol-research-protocol-v1.json")
        else: payload = manifests[0]
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
