#!/usr/bin/env python3
"""Frozen NBA Elo V1 with independent results and point-in-time market prices."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
ESPN_BASES = {"nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba", "wnba": "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"}
ESPN = ESPN_BASES["nba"]
CLOB = "https://clob.polymarket.com"
PROTOCOL = ROOT / "experiments/basketball-nba-research-protocol-v1.json"
CUTOFFS = {"T-24h": timedelta(hours=24), "T-60m": timedelta(minutes=60)}
MAX_AGE = {"T-24h": 21600, "T-60m": 7200}


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module


core = load("basketball_model_core", ROOT / "scripts/polymarket_alpha.py")
public = load("basketball_model_public", ROOT / "scripts/polymarket_public_data.py")
basketball = load("basketball_model_lab", ROOT / "scripts/polymarket_basketball_lab.py")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_json(url: str, timeout: float = 60.0) -> Any:
    if not url.startswith(tuple(base + "/" for base in ESPN_BASES.values())):
        raise ValueError("non-ESPN NBA URL blocked")
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "polymarket-paper-research/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    ranges = []; current = start.replace(day=1)
    while current <= end:
        next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        ranges.append((max(start, current), min(end, next_month - timedelta(days=1)))); current = next_month
    return ranges


def parse_espn_event(event: dict[str, Any]) -> dict[str, Any] | None:
    season_type = int((event.get("season") or {}).get("type") or 0)
    if season_type not in {2, 3} or not ((event.get("status") or {}).get("type") or {}).get("completed"):
        return None
    competition = (event.get("competitions") or [{}])[0]; competitors = competition.get("competitors") or []
    if len(competitors) != 2 or sum(bool(row.get("winner")) for row in competitors) != 1:
        return None
    roles = {}
    for row in competitors:
        team = row.get("team") or {}; role = str(row.get("homeAway") or "")
        roles[role] = {"team_id": str(team.get("id") or ""), "name": str(team.get("name") or team.get("shortDisplayName") or ""), "display_name": str(team.get("displayName") or ""), "winner": bool(row.get("winner")), "score": int(float(row.get("score") or 0))}
    if set(roles) != {"home", "away"}:
        return None
    return {"espn_game_id": str(event.get("id") or ""), "game_start_time": event.get("date"), "season_year": (event.get("season") or {}).get("year"), "season_type": season_type, "neutral_site": bool(competition.get("neutralSite")), "home": roles["home"], "away": roles["away"], "home_win": int(roles["home"]["winner"])}


def fetch_espn_schedule(output_dir: Path, start: str = "2023-10-01", end: str = "2026-06-30", workers: int = 6, league: str = "nba") -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True); (output_dir / "pages").mkdir(exist_ok=True); ranges = month_ranges(date.fromisoformat(start), date.fromisoformat(end))
    def fetch(period: tuple[date, date]):
        first, last = period; value = first.strftime("%Y%m%d") + "-" + last.strftime("%Y%m%d"); url = f"{ESPN_BASES[league]}/scoreboard?{urlencode({'dates': value, 'limit': 1000})}"; page = output_dir / "pages" / f"{value}.json"
        if page.exists():
            events = core.read_json(page).get("events") or []
            return events, {"url": url, "status": "cached", "rows": len(events), "range": value}
        try:
            payload = get_json(url); events = payload.get("events") or []; core.write_json(page, {"range": value, "request_url": url, "events": events})
            return events, {"url": url, "status": "ok", "rows": len(events), "range": value}
        except Exception as exc:
            return [], {"url": url, "status": "failed", "error": f"{type(exc).__name__}:{exc}", "range": value}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
        results = list(executor.map(fetch, ranges))
    games = {}; requests = []
    for events, request in results:
        requests.append(request)
        for event in events:
            row = parse_espn_event(event)
            if row:
                games[row["espn_game_id"]] = row
    rows = sorted(games.values(), key=lambda row: (row["game_start_time"], row["espn_game_id"])); core.write_json(output_dir / "games.json", rows); core.write_json(output_dir / "request-log.json", requests)
    relative = ["games.json", "request-log.json"] + [str(path.relative_to(output_dir)) for path in sorted((output_dir / "pages").glob("*.json"))]
    minimum_games = 3000 if league == "nba" else 500
    manifest = {"schema_version": "polymarket-basketball-espn-results-v1", "created_at": core.now_iso(), "league": league, "source": f"{ESPN_BASES[league]}/scoreboard", "start_date": start, "end_date": end, "expected_month_slices": len(ranges), "cached_month_slices": len(list((output_dir / "pages").glob("*.json"))), "final_regular_postseason_games": len(rows), "minimum_games": minimum_games, "season_type_counts": dict(Counter(str(row["season_type"]) for row in rows)), "failed_request_count": sum(row["status"] == "failed" for row in requests), "data_status": "ok" if len(rows) >= minimum_games and all(row["status"] in {"ok", "cached"} for row in requests) and len(list((output_dir / "pages").glob("*.json"))) == len(ranges) else "degraded", "files": [{"path": name, "bytes": (output_dir / name).stat().st_size, "sha256": sha256(output_dir / name)} for name in relative], "research_only": True, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}
    core.write_json(output_dir / "manifest.json", manifest); return manifest


ALIASES = {
    "pho": "suns", "la clippers": "clippers", "philadelphia 76ers": "76ers", "portland trail blazers": "trail blazers",
    "atlanta dream": "dream", "chicago sky": "sky", "connecticut sun": "sun", "dallas wings": "wings",
    "golden state valkyries": "valkyries", "indiana fever": "fever", "las vegas aces": "aces",
    "los angeles sparks": "sparks", "minnesota lynx": "lynx", "new york liberty": "liberty",
    "phoenix mercury": "mercury", "seattle storm": "storm", "washington mystics": "mystics",
    "portland fire": "fire", "portlandfire": "fire", "toronto tempo": "tempo"
}


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    text = " ".join("".join(char if char.isalnum() else " " for char in text).split())
    return ALIASES.get(text, text)


def map_games(history_dir: Path, espn_dir: Path, output_dir: Path) -> dict[str, Any]:
    if core.read_json(history_dir / "manifest.json").get("terminal_cursor_proven") is not True or core.read_json(espn_dir / "manifest.json").get("data_status") != "ok":
        raise ValueError("basketball history or ESPN results degraded")
    gamma = core.read_json(history_dir / "eligible-moneylines.json"); espn = core.read_json(espn_dir / "games.json"); output_dir.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for game in espn:
        stamp = basketball.parse_time(game["game_start_time"]); assert stamp
        by_day.setdefault(stamp.date().isoformat(), []).append(game)
    mappings = []; excluded = Counter(); used = set(); disagreement = 0
    for market in sorted(gamma, key=lambda row: row["game_start_time"]):
        start = basketball.parse_time(market["game_start_time"]); assert start
        teams = {normalize(value) for value in market["teams"]}; candidates = []
        for day in ((start - timedelta(days=1)).date(), start.date(), (start + timedelta(days=1)).date()):
            for game in by_day.get(day.isoformat(), []):
                event_start = basketball.parse_time(game["game_start_time"]); assert event_start
                event_teams = {normalize(game["home"]["name"]), normalize(game["away"]["name"])}
                if event_teams == teams and abs((event_start - start).total_seconds()) <= 10800 and game["espn_game_id"] not in used:
                    candidates.append((abs((event_start - start).total_seconds()), game))
        if len(candidates) != 1:
            excluded["mapping_missing_or_ambiguous"] += 1; continue
        game = candidates[0][1]; used.add(game["espn_game_id"]); espn_winner = game["home"]["name"] if game["home_win"] else game["away"]["name"]
        if normalize(espn_winner) != normalize(market["winning_outcome"]):
            disagreement += 1; excluded["winner_disagreement"] += 1; continue
        home_outcome = next((team for team in market["teams"] if normalize(team) == normalize(game["home"]["name"])), None)
        away_outcome = next((team for team in market["teams"] if normalize(team) == normalize(game["away"]["name"])), None)
        if not home_outcome or not away_outcome:
            excluded["role_mapping_failed"] += 1; continue
        mappings.append({**market, "espn_game_id": game["espn_game_id"], "season_type": game["season_type"], "home_team_id": game["home"]["team_id"], "away_team_id": game["away"]["team_id"], "home_outcome": home_outcome, "away_outcome": away_outcome, "home_win": game["home_win"], "winner_agreement": True})
    core.write_json(output_dir / "mapped-moneylines.json", mappings); core.write_json(output_dir / "mapping-exclusions.json", dict(excluded)); relative = ["mapped-moneylines.json", "mapping-exclusions.json"]
    league = str(core.read_json(history_dir / "manifest.json").get("league") or "nba"); minimum_mapped = 1000 if league == "nba" else 300
    manifest = {"schema_version": "polymarket-basketball-game-mapping-v1", "created_at": core.now_iso(), "league": league, "gamma_moneylines": len(gamma), "mapped_regular_postseason_games": len(mappings), "minimum_mapped_games": minimum_mapped, "unique_espn_game_ids": len({row["espn_game_id"] for row in mappings}), "winner_disagreement_count": disagreement, "exclusion_reason_counts": dict(excluded), "mapping_rate_pct": round(100 * len(mappings) / len(gamma), 4) if gamma else 0, "data_status": "ok" if len(mappings) >= minimum_mapped and disagreement == 0 else "degraded", "files": [{"path": name, "bytes": (output_dir / name).stat().st_size, "sha256": sha256(output_dir / name)} for name in relative], "research_only": True, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}
    core.write_json(output_dir / "manifest.json", manifest); return manifest


def latest_before(history: list[dict[str, Any]], cutoff: datetime, market_start: datetime):
    rows = [row for row in history if market_start.timestamp() <= float(row.get("t", 0)) <= cutoff.timestamp()]
    return max(rows, key=lambda row: float(row["t"])) if rows else None


def cutoff_observations(markets: list[dict[str, Any]], histories: dict[str, Any]) -> list[dict[str, Any]]:
    observations = []
    for market in markets:
        game_start = basketball.parse_time(market["game_start_time"]); market_start = basketball.parse_time(market["market_start_time"]); assert game_start and market_start
        for label, delta in CUTOFFS.items():
            cutoff = game_start - delta; outcome_rows = {}; failures = []
            if market_start > cutoff:
                failures.append("market_not_open_at_cutoff")
            for outcome, token in market["tokens"].items():
                point = latest_before((histories.get(str(token)) or {}).get("history") or [], cutoff, market_start)
                if point is None:
                    failures.append(f"price_missing:{outcome}"); continue
                age = cutoff.timestamp() - float(point["t"])
                if age > MAX_AGE[label]:
                    failures.append(f"price_stale:{outcome}")
                outcome_rows[outcome] = {"token_id": str(token), "price": float(point["p"]), "timestamp": int(point["t"]), "age_seconds": age}
            total = sum(row["price"] for row in outcome_rows.values()) if len(outcome_rows) == 2 else None
            normalized = {outcome: row["price"] / total for outcome, row in outcome_rows.items()} if total and total > 0 and len(outcome_rows) == 2 else {}
            observations.append({"game_id": market["game_id"], "espn_game_id": market["espn_game_id"], "cutoff_label": label, "cutoff_at": cutoff.isoformat(), "winning_outcome": market["winning_outcome"], "outcome_prices": outcome_rows, "raw_probability_sum": total, "normalized_market_probabilities": normalized, "status": "complete" if not failures else "excluded", "exclusion_reasons": failures, "future_price_data_used": False, "execution_evidence_eligible": False})
    return observations


def fetch_cutoff_prices(mapping_dir: Path, output_dir: Path, max_batches: int) -> dict[str, Any]:
    if core.read_json(mapping_dir / "manifest.json").get("data_status") != "ok":
        raise ValueError("basketball mapping degraded")
    markets = core.read_json(mapping_dir / "mapped-moneylines.json"); output_dir.mkdir(parents=True, exist_ok=True); hp = output_dir / "price-history.json"; rp = output_dir / "request-log.json"
    histories = core.read_json(hp) if hp.exists() else {}; requests = core.read_json(rp) if rp.exists() else []; groups = []
    for index in range(0, len(markets), 10):
        batch = markets[index:index + 10]; tokens = [str(token) for market in batch for token in market["tokens"].values()]
        if all(token in histories for token in tokens):
            continue
        starts = [basketball.parse_time(market["game_start_time"]) - timedelta(hours=30) for market in batch]
        ends = [basketball.parse_time(market["game_start_time"]) - timedelta(minutes=55) for market in batch]
        groups.append((tokens, min(starts), max(ends)))
    try:
        for number, (tokens, start, end) in enumerate(groups[:max_batches], 1):
            body = {"markets": tokens, "start_ts": int(start.timestamp()), "end_ts": int(end.timestamp()), "fidelity": 5}
            try:
                payload = public.post_json(f"{CLOB}/batch-prices-history", body, timeout=12, retries=1); result = payload.get("history") if isinstance(payload, dict) else {}
                for token in tokens:
                    histories[token] = {"history": sorted((result.get(token) or []) if isinstance(result, dict) else [], key=lambda row: int(row["t"])), "fidelity_minutes": 5, "attempted_at": core.now_iso()}
                requests.append({"status": "ok", "tokens": tokens, "start_ts": body["start_ts"], "end_ts": body["end_ts"], "nonempty": sum(bool(histories[token]["history"]) for token in tokens), "created_at": core.now_iso()})
            except Exception as exc:
                requests.append({"status": "failed", "tokens": tokens, "error": f"{type(exc).__name__}:{exc}", "created_at": core.now_iso()})
            if number % 10 == 0:
                core.write_json(hp, histories); core.write_json(rp, requests)
    finally:
        core.write_json(hp, histories); core.write_json(rp, requests)
    observations = cutoff_observations(markets, histories); core.write_json(output_dir / "cutoff-observations.json", observations); complete = Counter(row["cutoff_label"] for row in observations if row["status"] == "complete")
    tokens = {str(token) for market in markets for token in market["tokens"].values()}; nonempty = sum(bool((histories.get(token) or {}).get("history")) for token in tokens); relative = ["price-history.json", "request-log.json", "cutoff-observations.json"]
    manifest = {"schema_version": "polymarket-basketball-cutoff-prices-v1", "created_at": core.now_iso(), "source_mapping_manifest_sha256": sha256(mapping_dir / "manifest.json"), "official_source": f"{CLOB}/batch-prices-history", "fidelity_minutes": 5, "eligible_games": len(markets), "target_tokens": len(tokens), "nonempty_token_histories": nonempty, "token_history_coverage_pct": round(100 * nonempty / len(tokens), 4) if tokens else 0, "complete_observations_by_cutoff": dict(complete), "execution_evidence_eligible": False, "execution_limit": "historical trades have no recoverable spread, depth, executable VWAP or impact; forward books required", "data_status": "ok" if tokens and nonempty / len(tokens) >= .95 and all(complete[label] / len(markets) >= .90 for label in CUTOFFS) else "degraded", "files": [{"path": name, "bytes": (output_dir / name).stat().st_size, "sha256": sha256(output_dir / name)} for name in relative], "research_only": True, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}
    core.write_json(output_dir / "manifest.json", manifest); return manifest


def elo_prob(home: float, away: float, advantage: float) -> float:
    return 1.0 / (1.0 + 10 ** (-((home + advantage) - away) / 400.0))


def model_predictions(games: list[dict[str, Any]], k_factor: float, advantage: float) -> dict[str, float]:
    ratings: dict[str, float] = {}; predictions = {}
    for game in sorted(games, key=lambda row: (row["game_start_time"], row["espn_game_id"])):
        home = ratings.get(game["home"]["team_id"], 1500.0); away = ratings.get(game["away"]["team_id"], 1500.0); probability = elo_prob(home, away, advantage); predictions[game["espn_game_id"]] = probability
        delta = k_factor * (float(game["home_win"]) - probability); ratings[game["home"]["team_id"]] = home + delta; ratings[game["away"]["team_id"]] = away - delta
    return predictions


def lower_95(rows: list[dict[str, Any]], values: list[float]) -> float | None:
    grouped: dict[str, list[float]] = {}
    for row, value in zip(rows, values):
        grouped.setdefault(row["game_date"], []).append(value)
    means = [statistics.mean(group) for group in grouped.values()]
    return statistics.mean(means) - 1.96 * statistics.stdev(means) / math.sqrt(len(means)) if len(means) >= 2 else None


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eps = 1e-9; brier = [row["market_brier"] - row["model_brier"] for row in rows]; logs = [row["market_log_loss"] - row["model_log_loss"] for row in rows]
    bins: dict[int, list[dict[str, Any]]] = {}
    for row in rows: bins.setdefault(min(9, int(float(row["model_probability"]) * 10)), []).append(row)
    calibration = []
    for index, group in sorted(bins.items()):
        n = len(group); observed = statistics.mean(float(row["actual"]) for row in group); z = 1.96
        denominator = 1 + z * z / n; center = (observed + z * z / (2 * n)) / denominator
        margin = z * math.sqrt(observed * (1 - observed) / n + z * z / (4 * n * n)) / denominator
        calibration.append({"probability_bin": f"{index / 10:.1f}-{(index + 1) / 10:.1f}", "samples": n,
                            "mean_model_probability": statistics.mean(float(row["model_probability"]) for row in group),
                            "historical_hit_rate": observed, "wilson_95_low": max(0.0, center - margin),
                            "wilson_95_high": min(1.0, center + margin)})
    return {"games": len(rows), "distinct_dates": len({row["game_date"] for row in rows}), "model_brier": statistics.mean(row["model_brier"] for row in rows), "market_brier": statistics.mean(row["market_brier"] for row in rows), "brier_improvement": statistics.mean(brier), "brier_improvement_lower_95_date_clustered": lower_95(rows, brier), "model_log_loss": statistics.mean(row["model_log_loss"] for row in rows), "market_log_loss": statistics.mean(row["market_log_loss"] for row in rows), "log_loss_improvement": statistics.mean(logs), "log_loss_improvement_lower_95_date_clustered": lower_95(rows, logs), "historical_hit_rate": statistics.mean((float(row["model_probability"]) >= .5) == bool(row["actual"]) for row in rows), "calibration_bins": calibration}


def walk_forward(espn_dir: Path, mapping_dir: Path, price_dir: Path, output: Path, report: Path, protocol_path: Path = PROTOCOL) -> dict[str, Any]:
    protocol = core.read_json(protocol_path)
    for directory in (espn_dir, mapping_dir, price_dir):
        if core.read_json(directory / "manifest.json").get("data_status") != "ok":
            raise ValueError(f"degraded basketball input: {directory}")
    games = core.read_json(espn_dir / "games.json"); mapped = core.read_json(mapping_dir / "mapped-moneylines.json"); observations = core.read_json(price_dir / "cutoff-observations.json")
    dates = sorted({basketball.parse_time(row["game_start_time"]).date().isoformat() for row in mapped}); dev_end = int(len(dates) * .60); val_end = int(len(dates) * .80); split_dates = {"development": set(dates[:dev_end]), "validation": set(dates[dev_end:val_end]), "final_holdout": set(dates[val_end:])}; mapped_by_id = {row["espn_game_id"]: row for row in mapped}
    candidates = []
    for k_factor in (10.0, 20.0, 30.0):
        for advantage in (0.0, 25.0, 50.0, 75.0):
            prediction = model_predictions(games, k_factor, advantage); errors = []
            for game_id, market in mapped_by_id.items():
                game_date = basketball.parse_time(market["game_start_time"]).date().isoformat()
                if game_date in split_dates["development"] and game_id in prediction:
                    model_home = prediction[game_id]; p = model_home if market["teams"][0] == market["home_outcome"] else 1 - model_home; actual = float(market["winning_outcome"] == market["teams"][0]); errors.append((p - actual) ** 2)
            candidates.append({"k_factor": k_factor, "home_advantage": advantage, "development_brier": statistics.mean(errors), "development_games": len(errors)})
    selected = min(candidates, key=lambda row: (row["development_brier"], row["k_factor"], row["home_advantage"])); prediction = model_predictions(games, selected["k_factor"], selected["home_advantage"]); obs_map = {(row["espn_game_id"], row["cutoff_label"]): row for row in observations if row["status"] == "complete"}; score_rows = []
    for market in mapped:
        game_id = market["espn_game_id"]; game_date = basketball.parse_time(market["game_start_time"]).date().isoformat(); segment = next(name for name, values in split_dates.items() if game_date in values); model_home = prediction[game_id]; model_p = model_home if market["teams"][0] == market["home_outcome"] else 1 - model_home; actual = float(market["winning_outcome"] == market["teams"][0])
        for cutoff in CUTOFFS:
            observation = obs_map.get((game_id, cutoff))
            if not observation:
                continue
            market_p = float(observation["normalized_market_probabilities"][market["teams"][0]]); eps = 1e-9
            score_rows.append({"espn_game_id": game_id, "game_date": game_date, "segment": segment, "cutoff": cutoff, "actual": actual, "model_probability": model_p, "market_probability": market_p, "model_brier": (model_p - actual) ** 2, "market_brier": (market_p - actual) ** 2, "model_log_loss": -(actual * math.log(max(eps, model_p)) + (1 - actual) * math.log(max(eps, 1 - model_p))), "market_log_loss": -(actual * math.log(max(eps, market_p)) + (1 - actual) * math.log(max(eps, 1 - market_p)))})
    summaries = []
    for segment in split_dates:
        for cutoff in CUTOFFS:
            rows = [row for row in score_rows if row["segment"] == segment and row["cutoff"] == cutoff]; summaries.append({"segment": segment, "cutoff": cutoff, **metrics(rows)})
    validation = [row for row in summaries if row["segment"] == "validation"]; final = [row for row in summaries if row["segment"] == "final_holdout"]
    brier_pass = all((row["brier_improvement_lower_95_date_clustered"] or -1) > 0 for row in [*validation, *final]); log_pass = all((row["log_loss_improvement_lower_95_date_clustered"] or -1) > 0 for row in [*validation, *final]); promoted = brier_pass or log_pass
    league = str(protocol.get("league") or "nba")
    previous = core.read_json(output) if output.exists() else {}
    payload = {"schema_version": f"polymarket-basketball-{league}-elo-v1", "created_at": core.now_iso(), "league": league, "model_version": protocol["model_v1_frozen_spec"]["version"], "protocol_created_at": protocol["created_at"], "protocol_sha256": sha256(protocol_path), "mapped_games": len(mapped), "distinct_dates": len(dates), "split": {name: {"dates": len(values), "games": sum(basketball.parse_time(row["game_start_time"]).date().isoformat() in values for row in mapped)} for name, values in split_dates.items()}, "candidate_results": candidates, "selected": selected, "metrics": summaries, "same_date_games_never_cross_segments": True, "parameter_selection_scope": "development only", "final_holdout_first_inspected_at": previous.get("final_holdout_first_inspected_at") or core.now_iso(), "final_holdout_reuse_for_model_selection_allowed": False, "model_status": "frozen_failed" if not promoted else "frozen_probability_candidate", "parameter_changes_without_materially_different_prefrozen_model_and_fresh_oos_allowed": False, "research_promotion_candidate": promoted, "promotion_status": "manual_research_review_and_forward_execution_evidence_required" if promoted else "research_fail_no_sustained_date_clustered_oos_improvement", "paper_estimates_allowed": False, "paper_estimates_emitted": False, "model_outputs_are_true_probabilities": False, "main_paper_ledger_mutated": False, "live_orders_enabled": False, "private_api_used": False}
    core.write_json(output, payload); lines = [f"# Polymarket {league.upper()} Elo V1", "", f"- Selected: K={selected['k_factor']}, home advantage={selected['home_advantage']}", f"- Mapped games / dates: {len(mapped)} / {len(dates)}", f"- Promotion: `{payload['promotion_status']}`", "", "| Segment | Cutoff | Games | Dates | Model Brier | Market Brier | Improvement | 95% lower |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in summaries:
        lines.append(f"| {row['segment']} | {row['cutoff']} | {row['games']} | {row['distinct_dates']} | {row['model_brier']:.5f} | {row['market_brier']:.5f} | {row['brier_improvement']:.5f} | {row['brier_improvement_lower_95_date_clustered']:.5f} |")
    lines.extend(["", "Historical trade prices are a probability benchmark, not execution evidence. No paper estimate is emitted without forward two-sided books, friction and manual review.", ""]); report.write_text("\n".join(lines), encoding="utf-8"); return payload


def self_test() -> dict[str, Any]:
    assert month_ranges(date(2026, 1, 15), date(2026, 3, 2)) == [(date(2026, 1, 15), date(2026, 1, 31)), (date(2026, 2, 1), date(2026, 2, 28)), (date(2026, 3, 1), date(2026, 3, 2))]
    assert normalize("PHO") == "suns" and abs(elo_prob(1500, 1500, 0) - .5) < 1e-12
    cutoff = basketball.parse_time("2026-07-10T19:00:00Z"); start = basketball.parse_time("2026-07-09T00:00:00Z"); point = latest_before([{"t": int(cutoff.timestamp()) - 1, "p": .51}, {"t": int(cutoff.timestamp()) + 1, "p": .99}], cutoff, start)
    assert point and point["p"] == .51
    return {"status": "pass", "tests": ["month_ranges", "team_alias", "elo_neutral", "post_cutoff_exclusion"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    schedule = sub.add_parser("fetch-espn"); schedule.add_argument("--league", choices=list(ESPN_BASES), default="nba"); schedule.add_argument("--output-dir", default=str(ROOT / "cache/current_basketball_espn")); schedule.add_argument("--start", default="2023-10-01"); schedule.add_argument("--end", default="2026-06-30"); schedule.add_argument("--workers", type=int, default=6)
    mapping = sub.add_parser("map-games"); mapping.add_argument("--history-dir", default=str(ROOT / "cache/current_basketball_history/nba")); mapping.add_argument("--espn-dir", default=str(ROOT / "cache/current_basketball_espn")); mapping.add_argument("--output-dir", default=str(ROOT / "cache/current_basketball_mapping"))
    prices = sub.add_parser("fetch-cutoff-prices"); prices.add_argument("--mapping-dir", default=str(ROOT / "cache/current_basketball_mapping")); prices.add_argument("--output-dir", default=str(ROOT / "cache/current_basketball_cutoff_prices")); prices.add_argument("--max-batches", type=int, default=200)
    walk = sub.add_parser("walk-forward"); walk.add_argument("--espn-dir", default=str(ROOT / "cache/current_basketball_espn")); walk.add_argument("--mapping-dir", default=str(ROOT / "cache/current_basketball_mapping")); walk.add_argument("--price-dir", default=str(ROOT / "cache/current_basketball_cutoff_prices")); walk.add_argument("--protocol", default=str(PROTOCOL)); walk.add_argument("--output", default=str(ROOT / "experiments/current-basketball-nba-elo-v1.json")); walk.add_argument("--report", default=str(ROOT / "reports/CURRENT_BASKETBALL_NBA_MODEL.md"))
    sub.add_parser("self-test"); args = parser.parse_args()
    if args.command == "fetch-espn": payload = fetch_espn_schedule(Path(args.output_dir), args.start, args.end, args.workers, args.league)
    elif args.command == "map-games": payload = map_games(Path(args.history_dir), Path(args.espn_dir), Path(args.output_dir))
    elif args.command == "fetch-cutoff-prices": payload = fetch_cutoff_prices(Path(args.mapping_dir), Path(args.output_dir), args.max_batches)
    elif args.command == "walk-forward": payload = walk_forward(Path(args.espn_dir), Path(args.mapping_dir), Path(args.price_dir), Path(args.output), Path(args.report), Path(args.protocol))
    else: payload = self_test()
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
