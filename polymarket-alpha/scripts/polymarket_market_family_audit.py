#!/usr/bin/env python3
"""Rank live Polymarket families by research feasibility, never by presumed edge."""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FAMILIES = {
    "elections": {"any_tags": {"Elections"}, "source_feasibility": 0.8, "model_status": "unresearched"},
    "esports": {"any_tags": {"Esports"}, "source_feasibility": 0.7, "model_status": "unresearched"},
    "baseball": {"any_tags": {"MLB", "baseball"}, "source_feasibility": 0.9, "model_status": "blocked"},
    "basketball": {"any_tags": {"Basketball"}, "source_feasibility": 0.9, "model_status": "unresearched"},
    "macro_indicators": {"any_tags": {"Macro Indicators"}, "source_feasibility": 1.0, "model_status": "unresearched"},
    "mentions": {"any_tags": {"Mentions"}, "source_feasibility": 0.7, "model_status": "unresearched"},
    "geopolitics": {"any_tags": {"Geopolitics"}, "source_feasibility": 0.5, "model_status": "unresearched"},
    "finance_daily_direction": {"any_tags": {"Daily-Close", "Up or Down"}, "source_feasibility": 1.0, "model_status": "unresearched"},
    "crypto_barrier": {"all_tags": {"Crypto", "Hit Price"}, "source_feasibility": 1.0, "model_status": "blocked"},
    "stock_weekly": {"all_tags": {"Weekly", "Stocks"}, "source_feasibility": 1.0, "model_status": "blocked"},
    "football": {"any_tags": {"Soccer"}, "source_feasibility": 0.9, "model_status": "blocked"},
    "weather": {"any_tags": {"Weather"}, "source_feasibility": 1.0, "model_status": "blocked"},
    "social_count": {"any_tags": {"Tweet Markets"}, "source_feasibility": 0.8, "model_status": "blocked"},
    "fdv": {"any_tags": {"FDV"}, "source_feasibility": 0.4, "model_status": "unresearched"},
    "technology_events": {"any_tags": {"AI", "Big Tech", "Tech", "AI Releases", "KPIs", "OpenAI", "Anthropic"}, "source_feasibility": 0.7, "model_status": "unresearched"},
    "corporate_events": {"any_tags": {"IPOs", "IPO", "Privates", "Earnings", "M&A"}, "source_feasibility": 0.7, "model_status": "unresearched"},
    "macro_policy": {"any_tags": {"Economy", "Global Rates", "Fed Rates", "Economic Policy"}, "source_feasibility": 0.8, "model_status": "unresearched"},
    "entertainment": {"any_tags": {"TV", "Movies", "Awards", "Emmys", "Music", "Celebrities"}, "source_feasibility": 0.6, "model_status": "unresearched"},
    "token_launch": {"any_tags": {"token launch"}, "source_feasibility": 0.6, "model_status": "unresearched"},
    "finance_barrier": {"any_tags": {"Hit Price", "Finance Updown", "Pyth Finance"}, "exclude_tags": {"Crypto", "Weekly", "Daily-Close", "Up or Down"}, "source_feasibility": 1.0, "model_status": "unresearched"},
}


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load("market_family_core", ROOT / "scripts/polymarket_alpha.py")


def tags(market: dict[str, Any]) -> set[str]:
    return set((market.get("_sampling_raw") or {}).get("tags") or [])


def belongs(market: dict[str, Any], rule: dict[str, Any]) -> bool:
    values = tags(market)
    any_tags = set(rule.get("any_tags") or [])
    all_tags = set(rule.get("all_tags") or [])
    exclude_tags = set(rule.get("exclude_tags") or [])
    return (not any_tags or bool(values & any_tags)) and (not all_tags or all_tags <= values) and not bool(values & exclude_tags)


def event_identity(market: dict[str, Any], identity_enrichment: dict[str, Any] | None = None) -> str | None:
    event = (market.get("events") or [{}])[0]
    if event.get("id") or market.get("event_id"):
        return f"event:{event.get('id') or market.get('event_id')}"
    neg_risk_id = (market.get("_sampling_raw") or {}).get("neg_risk_market_id")
    if neg_risk_id:
        return f"neg-risk:{neg_risk_id}"
    condition_id = str(market.get("conditionId") or (market.get("_sampling_raw") or {}).get("condition_id") or "")
    enriched = ((identity_enrichment or {}).get("by_condition_id") or {}).get(condition_id) or {}
    if enriched.get("event_id"):
        return f"event:{enriched['event_id']}"
    return None


def end_time(market: dict[str, Any]) -> datetime | None:
    value = market.get("endDate") or (market.get("_sampling_raw") or {}).get("end_date_iso")
    return core.parse_iso(value)


def rule_complete(market: dict[str, Any]) -> bool:
    raw = market.get("_sampling_raw") or {}
    description = str(market.get("description") or raw.get("description") or "").lower()
    return bool(market.get("resolutionSource") or raw.get("resolution_source") or "resolution source" in description)


def observed_spreads(market: dict[str, Any], books: dict[str, Any]) -> list[float]:
    values = []
    for token in (market.get("_sampling_raw") or {}).get("tokens") or []:
        book = books.get(str(token.get("token_id"))) or {}
        bids = [float(row["price"]) for row in book.get("bids") or []]
        asks = [float(row["price"]) for row in book.get("asks") or []]
        if bids and asks:
            values.append(min(asks) - max(bids))
    return values


def feasibility_score(event_count: int, near_count: int, completeness: float, source: float) -> float:
    # Inventory breadth is capped so thousands of correlated markets cannot dominate.
    return round(
        35 * min(event_count / 500, 1)
        + 30 * min(near_count / 100, 1)
        + 25 * completeness
        + 10 * source,
        2,
    )


def audit(markets: list[dict[str, Any]], books: dict[str, Any], as_of: datetime,
          status_overrides: dict[str, Any] | None = None,
          identity_enrichment: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = (status_overrides or {}).get("families") or {}
    rows = []
    for family, rule in FAMILIES.items():
        selected = [market for market in markets if belongs(market, rule)]
        identities = [event_identity(market, identity_enrichment) for market in selected]
        event_count = len({identity for identity in identities if identity})
        proven_contract_count = sum(bool(identity) for identity in identities)
        near = [market for market in selected if (end := end_time(market)) and 0 <= (end - as_of).total_seconds() <= 30 * 86400]
        near_event_count = len({identity for market in near if (identity := event_identity(market, identity_enrichment))})
        complete = sum(rule_complete(market) for market in selected)
        spread_rows = [spreads for market in selected if (spreads := observed_spreads(market, books))]
        flat_spreads = [value for values in spread_rows for value in values]
        completeness = complete / len(selected) if selected else 0.0
        score = feasibility_score(event_count, near_event_count, completeness, float(rule["source_feasibility"]))
        override = overrides.get(family) or {}
        model_status = override.get("model_status", rule["model_status"])
        rows.append({
            "family": family,
            "model_status": model_status,
            "model_status_reason": override.get("reason"),
            "model_status_evidence": override.get("evidence") or [],
            "market_count": len(selected),
            "independent_event_count": event_count,
            "event_identity_proven_contract_count": proven_contract_count,
            "event_identity_proven_pct": round(100 * proven_contract_count / len(selected), 2) if selected else 0.0,
            "unproven_event_identity_contract_count": len(selected) - proven_contract_count,
            "near_term_30d_market_count": len(near),
            "near_term_30d_contract_count": len(near),
            "near_term_30d_independent_event_count": near_event_count,
            "rule_complete_count": complete,
            "rule_complete_pct": round(100 * completeness, 2),
            "book_observed_market_count": len(spread_rows),
            "observed_token_median_spread_cents": round(100 * statistics.median(flat_spreads), 3) if flat_spreads else None,
            "source_feasibility_prior": rule["source_feasibility"],
            "research_feasibility_score": score,
            "score_is_alpha_evidence": False,
        })
    rows.sort(key=lambda row: (row["research_feasibility_score"], row["independent_event_count"]), reverse=True)
    eligible = [row for row in rows if row["model_status"] == "unresearched" and row["independent_event_count"] >= 30]
    history_fallback = [row for row in rows if row["model_status"] == "unresearched" and row["independent_event_count"] >= 1]
    selected_family = eligible[0]["family"] if eligible else (history_fallback[0]["family"] if history_fallback else None)
    selection_stage = "phase_zero_live_inventory" if eligible else ("history_feasibility_fallback" if history_fallback else "none")
    return {
        "schema_version": "polymarket-market-family-audit-v1",
        "created_at": core.now_iso(),
        "inventory_as_of": as_of.isoformat(),
        "market_count": len(markets),
        "family_overlap_allowed": True,
        "independence_unit": "Gamma event ID or CLOB negative-risk market ID; ungrouped conditions never count as independent events",
        "ranking_purpose": "research feasibility only; never evidence of probability edge or trade eligibility",
        "status_override_schema_version": (status_overrides or {}).get("schema_version"),
        "identity_enrichment_schema_version": (identity_enrichment or {}).get("schema_version"),
        "selected_next_research_family": selected_family,
        "selected_next_research_stage": selection_stage,
        "families": rows,
        "execution_observation_limit": "only markets with fetched public CLOB books are measured; absence is unknown, not illiquidity",
        "paper_estimates_emitted": False,
        "main_paper_ledger_mutated": False,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Polymarket Market Family Audit", "",
        f"- Inventory markets: {payload['market_count']}",
        f"- Next research family: `{payload['selected_next_research_family']}`",
        f"- Selection stage: `{payload['selected_next_research_stage']}`",
        "- Ranking meaning: research feasibility only; it is not Alpha evidence and cannot enable a paper entry.",
        "- Independence: threshold contracts inside the same event count once.", "",
        "| Family | Status | Markets | Events | <=30d events | Rules | Books observed | Score |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["families"]:
        lines.append(
            f"| {row['family']} | {row['model_status']} | {row['market_count']} | "
            f"{row['independent_event_count']} | {row['near_term_30d_independent_event_count']} | "
            f"{row['rule_complete_pct']:.1f}% | {row['book_observed_market_count']} | "
            f"{row['research_feasibility_score']:.2f} |"
        )
    lines.extend(["", "Book coverage is a limited public snapshot. Missing books are not classified as illiquid.", ""])
    return "\n".join(lines)


def frozen_protocol(selected_family: str | None) -> dict[str, Any]:
    if selected_family is None:
        return {
            "schema_version": "polymarket-no-eligible-family-protocol-v1", "created_at": core.now_iso(),
            "family": None, "status": "no_eligible_family_pending_more_identity_or_history_evidence",
            "paper_entry_eligible": False, "automatic_promotion": False,
            "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
            "live_orders_enabled": False, "private_api_used": False,
        }
    if selected_family == "esports":
        return {
            "schema_version": "polymarket-esports-research-protocol-v1",
            "created_at": core.now_iso(), "family": "esports",
            "status": "protocol_frozen_model_not_implemented",
            "selection_basis": "highest remaining unresearched family after baseball V1 failed its untouched OOS benchmark",
            "null_hypothesis": "no title-specific esports model beats contemporaneous Polymarket prices after friction and uncertainty discount",
            "independence_unit": "one scheduled match; maps, rounds, handicaps, totals and props from that match are clustered",
            "initial_contract_scope": "pregame full-match winner only; map markets, live markets, handicaps, totals, props and parlays excluded",
            "forecast_cutoffs": ["T-24h", "T-60m"],
            "title_isolation": "Valorant, CS2, Dota 2 and League of Legends are modeled and validated separately; no cross-title pooling",
            "allowed_inputs": ["timestamped official tournament schedule and results", "timestamped rosters", "title-specific public match history available before cutoff"],
            "forbidden_inputs": ["post-cutoff data", "future Polymarket prices", "final results during selection", "private account or order data"],
            "chronological_split": {"development": "earliest 60%", "validation": "next 20%", "final_holdout": "latest 20%; sealed until model and thresholds are frozen"},
            "minimum_evidence": {"independent_events_per_enabled_title": 30, "final_holdout_events_per_enabled_title": 30},
            "automatic_promotion": False, "paper_entry_eligible": False,
            "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
            "live_orders_enabled": False, "private_api_used": False,
        }
    if selected_family != "baseball":
        scopes = {
            "geopolitics": "taxonomy first; isolate one event class, actor set, horizon and resolution authority before any model; no cross-conflict pooling",
            "elections": "one race and office type per model; official result resolution with timestamped polls only; no cross-country pooling",
            "mentions": "one speaker, platform, counting rule and time window per model; no cross-person or cross-format pooling",
            "macro_indicators": "one official statistical series and release convention per model; use vintage data observable before cutoff",
            "finance_daily_direction": "one asset class, symbol and official close convention per model; no cross-asset pooling",
            "fdv": "one token launch and valuation convention per event; resolution source and circulating-supply rules must be explicit",
            "basketball": "one league and pregame full-game winner contract per model; live, props, spreads and totals excluded",
            "technology_events": "taxonomy first; isolate one company/product, event type, deadline and official resolution authority; no pooling of launches, KPIs, acquisitions, model releases or rankings",
            "corporate_events": "taxonomy first; isolate one IPO, earnings, acquisition or private-valuation contract type with point-in-time company evidence",
            "macro_policy": "taxonomy first; isolate one central bank, policy action, decision window and official announcement authority; no cross-country pooling",
            "entertainment": "taxonomy first; isolate one show, award, release or audience-measurement format and its official resolution authority",
            "token_launch": "taxonomy first; one objective token-launch definition, project set and deadline convention per model; announcements and tradable launches stay separate",
            "finance_barrier": "one asset class, named price source, hit-price convention and horizon per model; crypto, weekly and daily-close contracts excluded",
        }
        if selected_family not in scopes:
            raise ValueError(f"no frozen protocol template for selected family: {selected_family}")
        return {
            "schema_version": "polymarket-taxonomy-first-research-protocol-v1",
            "created_at": core.now_iso(), "family": selected_family,
            "status": "taxonomy_protocol_frozen_model_not_selected",
            "selection_basis": "highest remaining unresearched family after independent-event, near-term, rule-completeness and public-source feasibility scoring",
            "null_hypothesis": f"no {selected_family} subtype model beats contemporaneous Polymarket prices after friction and uncertainty discount",
            "initial_contract_scope": scopes[selected_family],
            "phase_zero_gate": "enumerate homogeneous subtypes, resolution authorities, rule ambiguity and point-in-time source availability before choosing a model",
            "independence_unit": "one underlying event; related thresholds, outcomes and derivative contracts remain one cluster",
            "forecast_cutoffs": ["T-7d", "T-24h"],
            "allowed_inputs": ["timestamped official sources", "timestamped independent public sources observable before cutoff", "Polymarket prices at or before cutoff"],
            "forbidden_inputs": ["post-cutoff data", "future Polymarket prices", "final outcomes during model selection", "private account or order data", "automated access prohibited by source terms"],
            "chronological_split": {"development": "earliest 60%", "validation": "next 20%", "final_holdout": "latest 20%; sealed until subtype, model and thresholds are frozen"},
            "minimum_evidence": {"phase_zero_live_inventory_events": 30, "total_settled_events_for_60_20_20_split": 150, "validation_events": 30, "final_holdout_events": 30},
            "automatic_promotion": False, "paper_entry_eligible": False,
            "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
            "live_orders_enabled": False, "private_api_used": False,
        }
    return {
        "schema_version": "polymarket-baseball-research-protocol-v1",
        "created_at": core.now_iso(),
        "family": selected_family,
        "status": "protocol_frozen_model_not_implemented",
        "selection_basis": "largest eligible independent-event inventory after capped breadth, near-term, rule-completeness, and public-source feasibility scoring",
        "null_hypothesis": "no baseball model beats the contemporaneous Polymarket price after fees, spread, slippage, and uncertainty discount",
        "independence_unit": "one scheduled game; moneyline, run-line, total, inning, and player-prop contracts from that game are clustered",
        "initial_contract_scope": "pregame full-game moneyline only; all props, live markets, run lines, totals, and parlays excluded",
        "forecast_cutoffs": ["T-24h", "T-60m"],
        "model_v1_frozen_spec": {
            "version": "pm-baseball-elo-v1",
            "initial_rating": 1500.0,
            "elo_scale": 400.0,
            "k_factor_grid": [10.0, 20.0, 30.0],
            "home_advantage_grid": [0.0, 25.0, 50.0],
            "warmup_start": "2023-03-01",
            "game_types": ["R", "F", "D", "L", "W"],
            "selection_metric": "lowest development-set binary Brier score",
            "parameter_selection_scope": "development split only; validation and final cannot alter parameters",
        },
        "game_mapping_rule": "match both team names or unique nicknames and nearest scheduled UTC start within 12 hours; require one-to-one gamePk",
        "allowed_inputs": ["official schedule and results", "timestamped probable and confirmed starters", "timestamped public lineups and injuries", "pregame weather", "team and pitcher statistics available before each cutoff"],
        "forbidden_inputs": ["post-cutoff data", "future Polymarket prices", "final results during model selection", "private account or order data"],
        "chronological_split": {"development": "earliest 60%", "validation": "next 20%", "final_holdout": "latest 20%; sealed until model and thresholds are frozen"},
        "minimum_evidence": {"settled_markets_scanned": 500, "independent_forward_trades": 30, "final_holdout_events": 30},
        "metrics": ["Brier score", "log loss", "calibration by probability bin", "net EV after friction", "CLV", "max drawdown"],
        "automatic_promotion": False,
        "paper_entry_eligible": False,
        "paper_estimates_emitted": False,
        "main_paper_ledger_mutated": False,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def self_test() -> dict[str, Any]:
    market = {"id": "m1", "endDate": "2026-07-20T00:00:00Z", "description": "Resolution source: official.", "events": [{"id": "e1"}], "_sampling_raw": {"tags": ["Elections"], "tokens": []}}
    duplicate = {**market, "id": "m2"}
    ungrouped = {"id": "m3", "endDate": "2026-07-20T00:00:00Z", "description": "Resolution source: official.", "_sampling_raw": {"tags": ["Elections"], "tokens": []}}
    result = audit([market, duplicate], {}, datetime(2026, 7, 12, tzinfo=timezone.utc),
                   {"schema_version": "test", "families": {"esports": {"model_status": "blocked", "reason": "test"}}})
    row = next(item for item in result["families"] if item["family"] == "elections")
    assert row["market_count"] == 2 and row["independent_event_count"] == 1
    assert row["near_term_30d_contract_count"] == 2 and row["near_term_30d_independent_event_count"] == 1
    esports = next(item for item in result["families"] if item["family"] == "esports")
    assert esports["model_status"] == "blocked"
    assert row["rule_complete_pct"] == 100.0
    assert event_identity(ungrouped) is None
    return {"status": "pass", "tests": ["event_deduplication", "near_term_event_deduplication", "ungrouped_condition_exclusion", "rule_completeness", "status_override"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", default=str(ROOT / "cache/current_validation_snapshot/markets.json"))
    parser.add_argument("--books", default=str(ROOT / "cache/current_validation_snapshot/books.json"))
    parser.add_argument("--output", default=str(ROOT / "experiments/current-market-family-audit.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_MARKET_FAMILY_AUDIT.md"))
    parser.add_argument("--protocol", default=str(ROOT / "experiments/current-next-family-research-protocol.json"))
    parser.add_argument("--status-overrides", default=str(ROOT / "experiments/family-research-status.json"))
    parser.add_argument("--identity-enrichment", default=str(ROOT / "cache/current_family_identity_enrichment.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        payload = self_test()
    else:
        markets = core.read_json(Path(args.markets))
        books = core.read_json(Path(args.books))
        manifest_path = Path(args.markets).with_name("snapshot-manifest.json")
        manifest = core.read_json(manifest_path) if manifest_path.exists() else {}
        as_of = core.parse_iso(manifest.get("created_at")) or datetime.now(timezone.utc)
        status_path = Path(args.status_overrides)
        status_overrides = core.read_json(status_path) if status_path.exists() else {}
        enrichment_path = Path(args.identity_enrichment)
        candidates = [enrichment_path, ROOT / "cache/current_uncovered_identity_enrichment.json"]
        snapshot_sha = __import__("hashlib").sha256(Path(args.markets).read_bytes()).hexdigest()
        valid = [core.read_json(path) for path in candidates if path.exists()]
        identity_enrichment = {"schema_version": "polymarket-merged-identity-enrichment-v1", "by_condition_id": {}}
        for artifact in valid:
            identity_enrichment["by_condition_id"].update(artifact.get("by_condition_id") or {})
        identity_enrichment["source_artifact_count"] = len(valid)
        identity_enrichment["current_snapshot_sha256"] = snapshot_sha
        identity_enrichment["matching_snapshot_artifact_count"] = sum(artifact.get("source_snapshot_sha256") == snapshot_sha for artifact in valid)
        payload = audit(markets, books, as_of, status_overrides, identity_enrichment)
        core.write_json(Path(args.output), payload)
        core.write_json(Path(args.protocol), frozen_protocol(payload["selected_next_research_family"]))
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
