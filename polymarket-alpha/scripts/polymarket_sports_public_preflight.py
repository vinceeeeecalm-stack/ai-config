#!/usr/bin/env python3
"""Read-only CLOB and live-score freshness preflight for one sports event."""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


public = load("sports_preflight_public", ROOT / "scripts/polymarket_public_data.py")
core = load("sports_preflight_core", ROOT / "scripts/polymarket_alpha.py")


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def age_seconds(value: Any, current: dt.datetime) -> float | None:
    if isinstance(value, str) and value.isdigit() and len(value) >= 13:
        parsed = dt.datetime.fromtimestamp(int(value) / 1000, tz=dt.timezone.utc)
    else:
        parsed = parse_time(value)
    return round((current - parsed).total_seconds(), 3) if parsed else None


def normalized_team(value: str) -> set[str]:
    stop = {"fc", "f.c", "club", "football", "the"}
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if token not in stop}


def gamma_teams(title: str) -> tuple[str, str] | None:
    parts = re.split(r"\s+vs\.?\s+|\s+v\s+", title, flags=re.IGNORECASE)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else None


def score_pair(event: dict[str, Any]) -> tuple[str, str] | None:
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    home = next((row for row in competitors if row.get("homeAway") == "home"), None)
    away = next((row for row in competitors if row.get("homeAway") == "away"), None)
    if not home or not away:
        return None
    return str(home.get("score")), str(away.get("score"))


def find_score_event(events: list[dict[str, Any]], home: str, away: str) -> dict[str, Any] | None:
    home_tokens, away_tokens = normalized_team(home), normalized_team(away)
    best: tuple[int, dict[str, Any]] | None = None
    for event in events:
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors") or []
        home_row = next((row for row in competitors if row.get("homeAway") == "home"), {})
        away_row = next((row for row in competitors if row.get("homeAway") == "away"), {})
        score = (
            len(home_tokens & normalized_team(str((home_row.get("team") or {}).get("displayName") or "")))
            + len(away_tokens & normalized_team(str((away_row.get("team") or {}).get("displayName") or "")))
        )
        if score >= 2 and (best is None or score > best[0]):
            best = (score, event)
    return best[1] if best else None


def fetch_json(url: str, timeout: float = 15.0) -> tuple[Any, dt.datetime]:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "sports-paper-preflight/1.0"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload, now()


def run_preflight(event_slug: str, market_slug: str, espn_league: str,
                  cash_usd: float = 5.0, current: dt.datetime | None = None) -> dict[str, Any]:
    current = current or now()
    event = public.get_json(f"{public.GAMMA_BASE}/events/slug/{event_slug}", timeout=15, retries=2)
    market = public.get_json(f"{public.GAMMA_BASE}/markets/slug/{market_slug}", timeout=15, retries=2)
    tokens = public.token_map(market)
    yes_token = next((token for outcome, token in tokens.items() if outcome.lower() == "yes"), None)
    aux = public.fetch_market_aux(market, True, False)
    book = (aux.get("books") or {}).get(yes_token) if yes_token else None
    simulation = core.simulate_buy(book or {}, cash_usd, cash_usd)
    fee = public.fee_contract(market, aux)
    quote_age = age_seconds((book or {}).get("timestamp"), current)
    clob_complete = bool(
        yes_token and simulation.get("fillable") is True
        and simulation.get("best_bid") is not None and simulation.get("best_ask") is not None
        and fee.get("status") == "ok" and quote_age is not None and -30 <= quote_age <= 120
        and not aux.get("errors")
    )

    teams = gamma_teams(str(event.get("title") or ""))
    event_date = str(event.get("eventDate") or event.get("startTime") or current.date().isoformat())[:10].replace("-", "")
    espn_url = "https://site.api.espn.com/apis/site/v2/sports/soccer/" + espn_league + "/scoreboard?" + urlencode({"dates": event_date})
    espn_payload, espn_received = fetch_json(espn_url)
    espn_event = find_score_event(espn_payload.get("events") or [], *(teams or ("", ""))) if teams else None
    espn_pair = score_pair(espn_event or {})
    gamma_score = str(event.get("score") or "").strip()
    espn_score = "-".join(espn_pair) if espn_pair else None
    espn_state = (((espn_event or {}).get("status") or {}).get("type") or {}).get("state")
    score_match = bool(gamma_score and espn_score and gamma_score == espn_score)
    observation_age = round((now() - espn_received).total_seconds(), 3)
    score_fresh = bool(
        event.get("live") is True and espn_state == "in" and score_match
        and -30 <= observation_age <= 120
    )
    gamma_score_age = age_seconds(event.get("updatedAt"), current)
    safety = {
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        "real_money_execution_authorized": False,
    }
    return {
        "schema_version": "sports-public-preflight-v1",
        "generated_at": now().isoformat(),
        "status": "PASS" if clob_complete and score_fresh else "WAIT_PASS",
        "event": {
            "event_slug": event_slug, "market_slug": market_slug, "title": event.get("title"),
            "contract_question": market.get("question"), "resolution_source": market.get("resolutionSource"),
        },
        "clob": {
            "complete": clob_complete, "token_id": yes_token,
            "best_bid": simulation.get("best_bid"), "best_ask": simulation.get("best_ask"),
            "spread": simulation.get("spread"), "fill_price_for_cash": simulation.get("fill_price"),
            "cash_usd": cash_usd, "quote_timestamp": (book or {}).get("timestamp"),
            "quote_age_seconds": quote_age, "fee_contract": fee, "errors": aux.get("errors") or [],
        },
        "score_freshness": {
            "complete": score_fresh, "gamma_score": gamma_score or None,
            "gamma_event_updated_at": event.get("updatedAt"), "gamma_age_seconds": gamma_score_age,
            "espn_score": espn_score, "espn_state": espn_state,
            "espn_detail": ((espn_event or {}).get("status") or {}).get("displayClock"),
            "espn_observed_at": espn_received.isoformat(), "observation_age_seconds": observation_age,
            "scores_reconciled": score_match, "espn_url": espn_url,
            "freshness_basis": "fresh public ESPN capture cross-reconciled to Gamma score; provider update timestamp unavailable",
        },
        "source_gate_for_entry_complete": False,
        "candidate_entry_authorized": False,
        "note": "Preflight validates public transport and freshness only. Official plus two independent event sources, independent probability, net EV and all six gates are still required before a paper fill.",
        "safety": safety,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-slug", required=True)
    parser.add_argument("--market-slug", required=True)
    parser.add_argument("--espn-league", required=True)
    parser.add_argument("--cash-usd", type=float, default=5.0)
    args = parser.parse_args()
    try:
        result = run_preflight(args.event_slug, args.market_slug, args.espn_league, args.cash_usd)
    except Exception as exc:
        result = {
            "schema_version": "sports-public-preflight-v1", "status": "WAIT_PASS",
            "error": f"{type(exc).__name__}:{exc}", "candidate_entry_authorized": False,
            "safety": {"paper_only": True, "live_orders_enabled": False, "private_api_used": False,
                       "real_money_execution_authorized": False},
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
