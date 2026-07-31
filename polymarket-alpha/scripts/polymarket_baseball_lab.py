#!/usr/bin/env python3
"""Forward-safe historical corpus builder for Polymarket MLB moneylines.

The lab is public/read-only and research-only.  It never emits a probability
estimate to the main paper ledger and never calls an authenticated endpoint.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import random
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
MLB_STATS = "https://statsapi.mlb.com"
MLB_TAG_ID = 100381
MLB_SERIES_ID = "3"
PAGE_SIZE = 100
CUTOFFS = {"T-24h": timedelta(hours=24), "T-60m": timedelta(minutes=60)}
MAX_PRICE_AGE_SECONDS = {"T-24h": 6 * 3600, "T-60m": 30 * 60}
ELO_K_GRID = (10.0, 20.0, 30.0)
ELO_HOME_GRID = (0.0, 25.0, 50.0)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load("baseball_core", ROOT / "scripts/polymarket_alpha.py")
public = load("baseball_public", ROOT / "scripts/polymarket_public_data.py")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_files(directory: Path, relative_paths: list[str]) -> list[dict[str, Any]]:
    rows = []
    for relative in relative_paths:
        path = directory / relative
        rows.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path)})
    return rows


def page_files(directory: Path) -> list[Path]:
    return sorted((directory / "pages").glob("page-*.json"))


def load_events(directory: Path) -> list[dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for path in page_files(directory):
        payload = core.read_json(path)
        for event in payload.get("events") or []:
            events[str(event.get("id"))] = event
    return sorted(events.values(), key=lambda row: int(row.get("id") or 0))


def get_mlb_metadata() -> dict[str, Any]:
    sports = public.get_json(f"{GAMMA}/sports", timeout=25, retries=2)
    row = next((item for item in sports if str(item.get("sport")).lower() == "mlb"), None)
    if not row:
        raise ValueError("official sports metadata has no MLB row")
    tag_ids = {int(value) for value in str(row.get("tags") or "").split(",") if value.strip().isdigit()}
    if MLB_TAG_ID not in tag_ids or str(row.get("series")) != MLB_SERIES_ID:
        raise ValueError("official MLB tag/series contract changed")
    return row


def parse_time(value: Any) -> datetime | None:
    """Parse Gamma timestamps, including its legacy ``+00`` suffix."""
    text = str(value or "")
    if text.endswith("+00"):
        text += ":00"
    return core.parse_iso(text)


def resolution_rule_complete(event: dict[str, Any], market: dict[str, Any]) -> bool:
    text = " ".join(str(value or "") for value in (
        market.get("resolutionSource"), market.get("description"),
        event.get("resolutionSource"), event.get("description"),
    )).lower()
    return "resolution source" in text or "official final statistics" in text or "mlb.com" in text


def parse_moneyline(event: dict[str, Any], market: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if str(market.get("sportsMarketType") or "").lower() != "moneyline":
        return None, "not_moneyline"
    title = f"{event.get('title', '')} {market.get('question', '')}".lower()
    if "in-game" in title or "live betting" in title:
        return None, "in_game_market"
    game_start = parse_time(market.get("gameStartTime"))
    if game_start is None:
        return None, "game_start_missing"
    official_game_id = event.get("gameId") or market.get("gameId")
    event_id = str(event.get("id") or "")
    if not event_id:
        return None, "event_id_missing"
    market_start = parse_time(market.get("startDate") or event.get("startDate"))
    if market_start is None or market_start >= game_start - timedelta(minutes=60):
        return None, "not_available_before_t60"
    outcomes = public.parse_jsonish(market.get("outcomes"))
    tokens = public.token_map(market)
    if len(outcomes) != 2 or len(tokens) != 2 or set(outcomes) != set(tokens):
        return None, "two_outcome_token_contract_failed"
    resolution = public.infer_resolution(market)
    if resolution.get("winning_outcome") not in outcomes:
        return None, "terminal_resolution_missing"
    if not resolution_rule_complete(event, market):
        return None, "resolution_rule_incomplete"
    return {
        "event_id": event_id,
        "game_id": str(official_game_id) if official_game_id else f"event:{event_id}:{game_start.isoformat()}",
        "game_identity_source": "official_game_id" if official_game_id else "event_id_plus_game_start",
        "market_id": str(market.get("id")), "condition_id": market.get("conditionId"),
        "question": market.get("question"), "slug": market.get("slug"),
        "game_start_time": game_start.isoformat(), "market_start_time": market_start.isoformat(),
        "outcomes": outcomes, "tokens": tokens,
        "winning_outcome": resolution.get("winning_outcome"),
        "available_at_t24": market_start <= game_start - timedelta(hours=24),
        "available_at_t60": market_start <= game_start - timedelta(minutes=60),
        "resolution_rule_complete": True,
        "sports_market_type": "moneyline", "pregame_only": True,
    }, None


def extract_eligible(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    eligible, excluded = [], Counter()
    seen_games: set[str] = set()
    for event in events:
        for market in event.get("markets") or []:
            row, reason = parse_moneyline(event, market)
            if reason:
                excluded[reason] += 1
                continue
            assert row
            if row["game_id"] in seen_games:
                excluded["duplicate_game_id"] += 1
                continue
            seen_games.add(row["game_id"])
            eligible.append(row)
    eligible.sort(key=lambda row: (row["game_start_time"], row["game_id"]))
    return eligible, dict(sorted(excluded.items()))


def discover_history(directory: Path, max_pages: int, wall_clock_seconds: float) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pages").mkdir(parents=True, exist_ok=True)
    contract = {"endpoint": f"{GAMMA}/events/keyset", "closed": True, "tag_id": MLB_TAG_ID, "limit": PAGE_SIZE}
    state_path, request_path = directory / "state.json", directory / "request-log.json"
    state = core.read_json(state_path) if state_path.exists() else {
        "schema_version": "polymarket-baseball-history-state-v1", "contract": contract,
        "next_cursor": None, "terminal_cursor_proven": False, "pages_fetched": 0,
    }
    if state.get("contract") != contract:
        raise ValueError("existing baseball history cache uses a different discovery contract")
    requests = core.read_json(request_path) if request_path.exists() else []
    # Persist the initial cursor contract even when the first network page fails;
    # this keeps the degraded manifest complete and makes the next run resumable.
    if not state_path.exists():
        core.write_json(state_path, state)
    if not request_path.exists():
        core.write_json(request_path, requests)
    metadata = get_mlb_metadata()
    core.write_json(directory / "sports-metadata.json", metadata)
    started = time.monotonic()
    pages_this_run = 0
    while not state.get("terminal_cursor_proven") and pages_this_run < max_pages and time.monotonic() - started < wall_clock_seconds:
        params: dict[str, Any] = {"closed": "true", "tag_id": MLB_TAG_ID, "limit": PAGE_SIZE}
        if state.get("next_cursor"):
            params["after_cursor"] = state["next_cursor"]
        url = f"{GAMMA}/events/keyset?{urlencode(params)}"
        try:
            payload = public.get_json(url, timeout=25, retries=2)
            rows = payload.get("events") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("keyset response lacks events list")
            page_number = int(state["pages_fetched"]) + 1
            page_payload = {"page_number": page_number, "request_url": url, "events": rows, "next_cursor": payload.get("next_cursor")}
            core.write_json(directory / "pages" / f"page-{page_number:04d}.json", page_payload)
            cursor = payload.get("next_cursor")
            requests.append({"page_number": page_number, "url": url, "status": "ok", "rows": len(rows), "terminal": not bool(cursor), "created_at": core.now_iso()})
            state.update({"next_cursor": cursor, "terminal_cursor_proven": not bool(cursor), "pages_fetched": page_number, "updated_at": core.now_iso()})
            core.write_json(request_path, requests); core.write_json(state_path, state)
            pages_this_run += 1
            if not cursor:
                break
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}", "created_at": core.now_iso()})
            core.write_json(request_path, requests)
            break
    events = load_events(directory)
    eligible, excluded = extract_eligible(events)
    core.write_json(directory / "eligible-moneylines.json", eligible)
    relative = ["sports-metadata.json", "state.json", "request-log.json", "eligible-moneylines.json"] + [str(path.relative_to(directory)) for path in page_files(directory)]
    payload = {
        "schema_version": "polymarket-baseball-history-manifest-v1", "created_at": core.now_iso(),
        "official_sources": [f"{GAMMA}/sports", f"{GAMMA}/events/keyset"],
        "mlb_tag_id": MLB_TAG_ID, "mlb_series_id": MLB_SERIES_ID,
        "official_resolution_source": metadata.get("resolution"),
        "pages_fetched": state.get("pages_fetched"), "terminal_cursor_proven": state.get("terminal_cursor_proven"),
        "events_fetched": len(events), "markets_fetched": sum(len(event.get("markets") or []) for event in events),
        "eligible_moneylines": len(eligible), "eligible_t24": sum(row["available_at_t24"] for row in eligible),
        "independent_game_ids": len({row["game_id"] for row in eligible}), "excluded_reason_counts": excluded,
        "data_status": "ok" if state.get("terminal_cursor_proven") and len(eligible) >= 30 else "degraded",
        "files": manifest_files(directory, relative),
        "research_only": True, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(directory / "manifest.json", payload)
    return payload


def latest_before(history: list[dict[str, Any]], cutoff: datetime, market_start: datetime) -> dict[str, Any] | None:
    rows = [row for row in history if market_start.timestamp() <= float(row.get("t", 0)) <= cutoff.timestamp()]
    return max(rows, key=lambda row: float(row["t"])) if rows else None


def build_cutoff_observations(eligible: list[dict[str, Any]], histories: dict[str, Any]) -> list[dict[str, Any]]:
    observations = []
    for market in eligible:
        game_start = parse_time(market["game_start_time"]); market_start = parse_time(market["market_start_time"])
        assert game_start and market_start
        for label, delta in CUTOFFS.items():
            cutoff = game_start - delta
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
                outcome_rows[outcome] = {"token_id": str(token), "price": float(point["p"]), "timestamp": int(point["t"]), "age_seconds": age}
            raw_sum = sum(row["price"] for row in outcome_rows.values()) if len(outcome_rows) == 2 else None
            normalized = {outcome: row["price"] / raw_sum for outcome, row in outcome_rows.items()} if raw_sum and raw_sum > 0 and len(outcome_rows) == 2 else {}
            observations.append({
                "game_id": market["game_id"], "event_id": market["event_id"], "market_id": market["market_id"],
                "cutoff_label": label, "cutoff_at": cutoff.isoformat(), "game_start_time": game_start.isoformat(),
                "winning_outcome": market["winning_outcome"], "outcome_prices": outcome_rows,
                "raw_probability_sum": raw_sum, "normalized_market_probabilities": normalized,
                "status": "complete" if not failures else "excluded", "exclusion_reasons": failures,
                "future_price_data_used": False, "execution_evidence_eligible": False,
            })
    return observations


def fetch_cutoff_prices(history_dir: Path, output_dir: Path, max_batches: int) -> dict[str, Any]:
    source = core.read_json(history_dir / "manifest.json")
    if source.get("terminal_cursor_proven") is not True:
        raise ValueError("baseball discovery has no terminal cursor proof")
    eligible = core.read_json(history_dir / "eligible-moneylines.json")
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
        starts = [parse_time(market["game_start_time"]) - timedelta(hours=30) for market in markets]
        ends = [parse_time(market["game_start_time"]) - timedelta(minutes=55) for market in markets]
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
                requests.append({"status": "failed", "tokens": tokens, "token_count": len(tokens), "error": f"{type(exc).__name__}:{exc}", "created_at": core.now_iso()})
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
    payload = {
        "schema_version": "polymarket-baseball-cutoff-price-manifest-v1", "created_at": core.now_iso(),
        "source_history_manifest_sha256": sha256(history_dir / "manifest.json"),
        "official_sources": [f"{CLOB}/batch-prices-history"], "fidelity_minutes": 5,
        "target_tokens": len(target_tokens), "nonempty_token_histories": nonempty_tokens,
        "token_history_coverage_pct": round(100 * token_coverage, 4),
        "eligible_games": len(eligible), "complete_observations_by_cutoff": dict(complete),
        "complete_t24_coverage_pct": round(100 * t24_coverage, 4),
        "complete_t60_coverage_pct": round(100 * t60_coverage, 4),
        "observation_count": len(observations), "execution_evidence_eligible": False,
        "execution_limit": "trade-price history has no historical spread, depth, VWAP, or slippage; forward books are required for execution evidence",
        "data_status": "ok" if token_coverage >= 0.95 and t24_coverage >= 0.90 and t60_coverage >= 0.90 else "degraded",
        "files": manifest_files(output_dir, relative),
        "research_only": True, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(output_dir / "manifest.json", payload)
    return payload


def mlb_json(url: str, timeout: float = 30.0) -> Any:
    if not url.startswith(MLB_STATS + "/api/v1/"):
        raise ValueError("non-MLB Stats URL blocked")
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "polymarket-paper-research/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def fetch_mlb_schedule(history_dir: Path, output_dir: Path) -> dict[str, Any]:
    eligible = core.read_json(history_dir / "eligible-moneylines.json")
    if not eligible:
        raise ValueError("eligible baseball history is empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    end = max(parse_time(row["game_start_time"]) for row in eligible).date()
    requests, games = [], {}
    ranges = [("2023-03-01", "2023-12-31"), ("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-12-31"), ("2026-01-01", end.isoformat())]
    for start, stop in ranges:
        params = {"sportId": 1, "startDate": start, "endDate": stop, "gameTypes": "R,F,D,L,W"}
        url = f"{MLB_STATS}/api/v1/schedule?{urlencode(params)}"
        try:
            payload = mlb_json(url)
            count = 0
            for date_row in payload.get("dates") or []:
                for game in date_row.get("games") or []:
                    status = game.get("status") or {}
                    teams = game.get("teams") or {}
                    away, home = teams.get("away") or {}, teams.get("home") or {}
                    if status.get("abstractGameState") != "Final" or game.get("gameType") not in {"R", "F", "D", "L", "W"}:
                        continue
                    if away.get("score") is None or home.get("score") is None or int(away["score"]) == int(home["score"]):
                        continue
                    row = {
                        "game_pk": str(game.get("gamePk")), "game_date": game.get("gameDate"),
                        "official_date": game.get("officialDate"), "game_type": game.get("gameType"),
                        "away_team_id": str((away.get("team") or {}).get("id")), "away_team": (away.get("team") or {}).get("name"),
                        "home_team_id": str((home.get("team") or {}).get("id")), "home_team": (home.get("team") or {}).get("name"),
                        "away_score": int(away["score"]), "home_score": int(home["score"]),
                        "home_win": int(int(home["score"]) > int(away["score"])),
                    }
                    if row["game_pk"] != "None" and parse_time(row["game_date"]):
                        games[row["game_pk"]] = row; count += 1
            requests.append({"url": url, "status": "ok", "final_games": count})
        except Exception as exc:
            requests.append({"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
    rows = sorted(games.values(), key=lambda row: (row["game_date"], row["game_pk"]))
    core.write_json(output_dir / "schedule.json", rows); core.write_json(output_dir / "request-log.json", requests)
    relative = ["schedule.json", "request-log.json"]
    payload = {
        "schema_version": "polymarket-baseball-mlb-schedule-v1", "created_at": core.now_iso(),
        "official_source": f"{MLB_STATS}/api/v1/schedule", "warmup_start": "2023-03-01", "end_date": end.isoformat(),
        "game_types": ["R", "F", "D", "L", "W"], "final_games": len(rows),
        "failed_request_count": sum(row["status"] == "failed" for row in requests),
        "data_status": "ok" if len(rows) >= 5000 and all(row["status"] == "ok" for row in requests) else "degraded",
        "files": manifest_files(output_dir, relative), "research_only": True,
        "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(output_dir / "manifest.json", payload)
    return payload


ALIASES = {"d backs": "diamondbacks", "dbacks": "diamondbacks", "a s": "athletics", "oakland a s": "athletics"}


def normalized_team(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return ALIASES.get(text, text)


def team_variants(value: Any) -> set[str]:
    text = normalized_team(value); words = text.split()
    values = {text}
    if words and words[-1] not in {"sox", "jays"}:
        values.add(words[-1])
    if len(words) >= 2 and " ".join(words[-2:]) in {"red sox", "white sox", "blue jays"}:
        values.add(" ".join(words[-2:]))
    return values


def team_matches(market_name: str, official_name: str) -> bool:
    return bool(team_variants(market_name) & team_variants(official_name))


def map_moneylines(eligible: list[dict[str, Any]], schedule: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    mapped, excluded, used = [], Counter(), set()
    by_date: dict[str, list[dict[str, Any]]] = {}
    for game in schedule:
        dt = parse_time(game["game_date"])
        if dt:
            for offset in (-1, 0, 1):
                by_date.setdefault((dt.date() + timedelta(days=offset)).isoformat(), []).append(game)
    proposals = []
    for market in eligible:
        start = parse_time(market["game_start_time"]); outcomes = market["outcomes"]
        candidates = []
        for game in by_date.get(start.date().isoformat(), []):
            game_time = parse_time(game["game_date"]); delta = abs((game_time - start).total_seconds())
            direct = team_matches(outcomes[0], game["away_team"]) and team_matches(outcomes[1], game["home_team"])
            reverse = team_matches(outcomes[1], game["away_team"]) and team_matches(outcomes[0], game["home_team"])
            if delta <= 12 * 3600 and (direct or reverse):
                candidates.append((delta, game, outcomes[1] if direct else outcomes[0]))
        if not candidates:
            excluded["official_game_not_matched"] += 1; continue
        candidates.sort(key=lambda item: (item[0], item[1]["game_pk"]))
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            excluded["ambiguous_official_game_match"] += 1; continue
        delta, game, home_outcome = candidates[0]
        proposals.append((delta, market, game, home_outcome))
    for delta, market, game, home_outcome in sorted(proposals, key=lambda item: (item[0], item[1]["game_start_time"])):
        if game["game_pk"] in used:
            excluded["duplicate_official_game_pk"] += 1; continue
        used.add(game["game_pk"])
        mapped.append({**market, "official_game_pk": game["game_pk"], "official_game_date": game["game_date"],
                       "official_away_team": game["away_team"], "official_home_team": game["home_team"],
                       "home_outcome": home_outcome, "home_win": game["home_win"], "mapping_delta_seconds": delta})
    mapped.sort(key=lambda row: (row["official_game_date"], row["official_game_pk"]))
    return mapped, dict(sorted(excluded.items()))


def elo_game_predictions(schedule: list[dict[str, Any]], k_factor: float, home_advantage: float) -> dict[str, float]:
    ratings: dict[str, float] = {}; predictions = {}
    for game in sorted(schedule, key=lambda row: (row["game_date"], row["game_pk"])):
        away, home = game["away_team_id"], game["home_team_id"]
        away_rating, home_rating = ratings.get(away, 1500.0), ratings.get(home, 1500.0)
        probability = 1.0 / (1.0 + 10.0 ** (-(home_rating + home_advantage - away_rating) / 400.0))
        predictions[game["game_pk"]] = probability
        error = float(game["home_win"]) - probability
        ratings[home] = home_rating + k_factor * error
        ratings[away] = away_rating - k_factor * error
    return predictions


def brier(rows: list[dict[str, Any]], key: str) -> float:
    return sum((float(row[key]) - float(row["home_win"])) ** 2 for row in rows) / len(rows)


def log_loss(rows: list[dict[str, Any]], key: str) -> float:
    values = []
    for row in rows:
        p = min(1 - 1e-9, max(1e-9, float(row[key]))); y = float(row["home_win"])
        values.append(-(y * math.log(p) + (1 - y) * math.log(1 - p)))
    return sum(values) / len(values)


def calibration_bins(rows: list[dict[str, Any]], key: str = "model_probability") -> list[dict[str, Any]]:
    groups: dict[int, list[dict[str, Any]]] = {}
    for row in rows: groups.setdefault(min(9, int(float(row[key]) * 10)), []).append(row)
    result = []
    for index, group in sorted(groups.items()):
        n = len(group); observed = sum(float(row["home_win"]) for row in group) / n
        z = 1.96; denominator = 1 + z * z / n; center = (observed + z * z / (2 * n)) / denominator
        margin = z * math.sqrt(observed * (1 - observed) / n + z * z / (4 * n * n)) / denominator
        result.append({"probability_bin": f"{index / 10:.1f}-{(index + 1) / 10:.1f}", "samples": n,
                       "mean_model_probability": sum(float(row[key]) for row in group) / n,
                       "historical_hit_rate": observed, "wilson_95_low": max(0.0, center - margin),
                       "wilson_95_high": min(1.0, center + margin)})
    return result


def paired_lower_95(rows: list[dict[str, Any]], seed: int = 20260712, draws: int = 2000) -> float:
    diffs = [(row["market_probability"] - row["home_win"]) ** 2 - (row["model_probability"] - row["home_win"]) ** 2 for row in rows]
    rng = random.Random(seed); means = []
    for _ in range(draws):
        means.append(sum(diffs[rng.randrange(len(diffs))] for _ in diffs) / len(diffs))
    means.sort(); return means[int(0.025 * (draws - 1))]


def run_elo_walk_forward(history_dir: Path, price_dir: Path, schedule_dir: Path, output: Path, report: Path) -> dict[str, Any]:
    protocol_path = ROOT / "experiments/baseball-research-protocol-v1.json"
    protocol = core.read_json(protocol_path); spec = protocol.get("model_v1_frozen_spec") or {}
    if spec.get("version") != "pm-baseball-elo-v1" or tuple(spec.get("k_factor_grid") or []) != ELO_K_GRID or tuple(spec.get("home_advantage_grid") or []) != ELO_HOME_GRID:
        raise ValueError("frozen baseball Elo protocol mismatch")
    if core.read_json(price_dir / "manifest.json").get("data_status") != "ok" or core.read_json(schedule_dir / "manifest.json").get("data_status") != "ok":
        raise ValueError("baseball cutoff or MLB schedule data is degraded")
    eligible = core.read_json(history_dir / "eligible-moneylines.json")
    schedule = core.read_json(schedule_dir / "schedule.json")
    observations = core.read_json(price_dir / "cutoff-observations.json")
    mapped, excluded = map_moneylines(eligible, schedule)
    observation_map = {(row["game_id"], row["cutoff_label"]): row for row in observations if row["status"] == "complete"}
    candidates = []
    n = len(mapped); train_end, validation_end = int(n * 0.60), int(n * 0.80)
    if min(train_end, validation_end - train_end, n - validation_end) < 30:
        raise ValueError("mapped chronological split has fewer than 30 games")
    for k in ELO_K_GRID:
        for home_adv in ELO_HOME_GRID:
            predictions = elo_game_predictions(schedule, k, home_adv)
            train_rows = [{"home_win": row["home_win"], "model_probability": predictions[row["official_game_pk"]]} for row in mapped[:train_end]]
            candidates.append({"k_factor": k, "home_advantage": home_adv, "development_brier": brier(train_rows, "model_probability")})
    candidates.sort(key=lambda row: (row["development_brier"], row["k_factor"], row["home_advantage"]))
    selected = candidates[0]; predictions = elo_game_predictions(schedule, selected["k_factor"], selected["home_advantage"])
    segments = {"development": mapped[:train_end], "validation": mapped[train_end:validation_end], "final_holdout": mapped[validation_end:]}
    metrics = []
    for segment, games in segments.items():
        for cutoff in CUTOFFS:
            rows = []
            for game in games:
                observation = observation_map.get((game["game_id"], cutoff))
                if not observation: continue
                market_p = (observation.get("normalized_market_probabilities") or {}).get(game["home_outcome"])
                if market_p is None: continue
                rows.append({"game_id": game["game_id"], "home_win": game["home_win"], "model_probability": predictions[game["official_game_pk"]], "market_probability": float(market_p)})
            model_brier, market_brier = brier(rows, "model_probability"), brier(rows, "market_probability")
            metrics.append({"segment": segment, "cutoff": cutoff, "games": len(rows),
                            "model_brier": model_brier, "market_brier": market_brier,
                            "brier_improvement": market_brier - model_brier, "paired_brier_improvement_lower_95": paired_lower_95(rows),
                            "model_log_loss": log_loss(rows, "model_probability"), "market_log_loss": log_loss(rows, "market_probability"),
                            "log_loss_improvement": log_loss(rows, "market_probability") - log_loss(rows, "model_probability"),
                            "historical_hit_rate": sum((float(row["model_probability"]) >= .5) == bool(row["home_win"]) for row in rows) / len(rows),
                            "calibration_bins": calibration_bins(rows)})
    oos = [row for row in metrics if row["segment"] in {"validation", "final_holdout"}]
    probability_pass = all(row["brier_improvement"] > 0 and row["paired_brier_improvement_lower_95"] > 0 and row["log_loss_improvement"] > 0 for row in oos)
    previous = core.read_json(output) if output.exists() else {}
    payload = {
        "schema_version": "polymarket-baseball-elo-walk-forward-v1", "created_at": core.now_iso(),
        "model_version": "pm-baseball-elo-v1", "protocol_sha256": sha256(protocol_path),
        "parameter_candidates": candidates, "selected_parameters": selected,
        "parameter_selection_scope": "development only", "chronological_split": {key: len(value) for key, value in segments.items()},
        "mapped_games": len(mapped), "mapping_exclusions": excluded, "metrics": metrics,
        "final_holdout_first_inspected_at": previous.get("final_holdout_first_inspected_at") or core.now_iso(),
        "final_holdout_reuse_for_model_selection_allowed": False,
        "model_status": "frozen_failed" if not probability_pass else "frozen_probability_candidate",
        "parameter_changes_without_materially_different_prefrozen_model_and_fresh_oos_allowed": False,
        "probability_oos_pass": probability_pass,
        "status": "probability_research_pass_execution_missing" if probability_pass else "failed_oos_market_benchmark",
        "promotion_status": "research_fail_no_sustained_oos_improvement" if not probability_pass else "probability_only_execution_missing",
        "research_promotion_candidate": False,
        "model_outputs_are_true_probabilities": False,
        "paper_estimates_allowed": False, "paper_estimates_blockers": ["historical_execution_evidence_missing", "forward_shadow_evidence_missing"] + ([] if probability_pass else ["sustained_oos_improvement_failed"]),
        "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False,
    }
    core.write_json(output, payload)
    lines = ["# Polymarket Baseball Elo V1", "", f"- Status: `{payload['status']}`", f"- Mapped games: {len(mapped)}", f"- Split: {payload['chronological_split']}", f"- Selected: K={selected['k_factor']}, home advantage={selected['home_advantage']}", f"- Paper estimates allowed: `false`", "", "| Segment | Cutoff | Games | Model Brier | Market Brier | Improvement | 95% lower |", "|---|---|---:|---:|---:|---:|---:|"]
    for row in metrics:
        lines.append(f"| {row['segment']} | {row['cutoff']} | {row['games']} | {row['model_brier']:.5f} | {row['market_brier']:.5f} | {row['brier_improvement']:.5f} | {row['paired_brier_improvement_lower_95']:.5f} |")
    lines.extend(["", "Parameters are selected on development games only. Historical prices do not contain spread/depth/VWAP/slippage, so even a probability pass cannot enable paper entry.", ""])
    report.parent.mkdir(parents=True, exist_ok=True); report.write_text("\n".join(lines), encoding="utf-8")
    return payload


def self_test() -> dict[str, Any]:
    market = {
        "id": "m1", "question": "A vs. B", "sportsMarketType": "moneyline",
        "gameStartTime": "2026-07-12T20:00:00Z", "startDate": "2026-07-10T00:00:00Z",
        "description": "The primary resolution source is official final statistics.",
        "outcomes": '["A", "B"]', "outcomePrices": '["1", "0"]',
        "clobTokenIds": '["ta", "tb"]', "closed": True,
    }
    event = {"id": "e1", "gameId": 123, "markets": [market]}
    row, reason = parse_moneyline(event, market)
    assert reason is None and row and row["game_id"] == "123" and row["available_at_t24"]
    duplicate, excluded = extract_eligible([event, {**event, "id": "e2"}])
    assert len(duplicate) == 1 and excluded["duplicate_game_id"] == 1
    market["sportsMarketType"] = "spread"
    assert parse_moneyline(event, market)[1] == "not_moneyline"
    return {"status": "pass", "tests": ["strict_moneyline_parse", "game_id_deduplication", "non_moneyline_exclusion"]}


def research_markdown(history: dict[str, Any], prices: dict[str, Any], model: dict[str, Any] | None = None) -> str:
    complete = prices.get("complete_observations_by_cutoff") or {}
    return "\n".join([
        "# Polymarket Baseball Research Corpus", "",
        f"- History status: `{history.get('data_status')}`",
        f"- Terminal cursor proven: `{str(history.get('terminal_cursor_proven')).lower()}`",
        f"- Official MLB events / markets: {history.get('events_fetched')} / {history.get('markets_fetched')}",
        f"- Eligible independent pregame moneylines: {history.get('eligible_moneylines')}",
        f"- Eligible at T-24h: {history.get('eligible_t24')}",
        f"- Cutoff-price status: `{prices.get('data_status')}`",
        f"- Token history coverage: {prices.get('nonempty_token_histories')} / {prices.get('target_tokens')} ({prices.get('token_history_coverage_pct')}%)",
        f"- Complete T-24h observations: {complete.get('T-24h')} ({prices.get('complete_t24_coverage_pct')}%)",
        f"- Complete T-60m observations: {complete.get('T-60m')} ({prices.get('complete_t60_coverage_pct')}%)", "",
        "## Scope and safety", "",
        "Only settled, pregame, full-game MLB moneylines with two outcomes, a scheduled start, a complete resolution rule, and a unique game identity are retained.",
        "All awards, futures, props, live markets, run lines, totals and parlays are excluded.",
        "Every cutoff price is selected at or before its cutoff; future prices are never used.",
        "Trade-price history contains no historical spread, depth, VWAP or slippage, so this corpus is not execution evidence and cannot authorize a paper entry.",
        f"Current independent model status: `{(model or {}).get('status', 'not_run')}`; paper estimates remain disabled.", "",
    ])


def write_research_report(history_dir: Path, price_dir: Path, output: Path) -> dict[str, Any]:
    history, prices = core.read_json(history_dir / "manifest.json"), core.read_json(price_dir / "manifest.json")
    model_path = ROOT / "experiments/current-baseball-elo-walk-forward.json"
    model = core.read_json(model_path) if model_path.exists() else None
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(research_markdown(history, prices, model), encoding="utf-8")
    return {"status": "ok", "output": str(output), "history_status": history.get("data_status"), "price_status": prices.get("data_status")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover-history")
    discover.add_argument("--output-dir", default=str(ROOT / "cache/current_baseball_history"))
    discover.add_argument("--max-pages", type=int, default=100)
    discover.add_argument("--wall-clock-seconds", type=float, default=240)
    prices = sub.add_parser("fetch-cutoff-prices")
    prices.add_argument("--history-dir", default=str(ROOT / "cache/current_baseball_history"))
    prices.add_argument("--output-dir", default=str(ROOT / "cache/current_baseball_cutoff_prices"))
    prices.add_argument("--max-batches", type=int, default=200)
    schedule = sub.add_parser("fetch-mlb-schedule")
    schedule.add_argument("--history-dir", default=str(ROOT / "cache/current_baseball_history"))
    schedule.add_argument("--output-dir", default=str(ROOT / "cache/current_baseball_mlb_schedule"))
    walk = sub.add_parser("walk-forward")
    walk.add_argument("--history-dir", default=str(ROOT / "cache/current_baseball_history"))
    walk.add_argument("--price-dir", default=str(ROOT / "cache/current_baseball_cutoff_prices"))
    walk.add_argument("--schedule-dir", default=str(ROOT / "cache/current_baseball_mlb_schedule"))
    walk.add_argument("--output", default=str(ROOT / "experiments/current-baseball-elo-walk-forward.json"))
    walk.add_argument("--report", default=str(ROOT / "reports/CURRENT_BASEBALL_ELO_WALK_FORWARD.md"))
    report = sub.add_parser("report")
    report.add_argument("--history-dir", default=str(ROOT / "cache/current_baseball_history"))
    report.add_argument("--price-dir", default=str(ROOT / "cache/current_baseball_cutoff_prices"))
    report.add_argument("--output", default=str(ROOT / "reports/CURRENT_BASEBALL_RESEARCH.md"))
    sub.add_parser("self-test")
    args = parser.parse_args()
    if args.command == "discover-history":
        payload = discover_history(Path(args.output_dir), args.max_pages, args.wall_clock_seconds)
    elif args.command == "fetch-cutoff-prices":
        payload = fetch_cutoff_prices(Path(args.history_dir), Path(args.output_dir), args.max_batches)
    elif args.command == "fetch-mlb-schedule":
        payload = fetch_mlb_schedule(Path(args.history_dir), Path(args.output_dir))
    elif args.command == "walk-forward":
        payload = run_elo_walk_forward(Path(args.history_dir), Path(args.price_dir), Path(args.schedule_dir), Path(args.output), Path(args.report))
    elif args.command == "report":
        payload = write_research_report(Path(args.history_dir), Path(args.price_dir), Path(args.output))
    else:
        payload = self_test()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("data_status") in {None, "ok"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
