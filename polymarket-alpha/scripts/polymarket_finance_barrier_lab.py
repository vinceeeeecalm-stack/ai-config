#!/usr/bin/env python3
"""Finance-barrier Phase-0 taxonomy for non-crypto hit-price markets."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load("finance_barrier_core", ROOT / "scripts/polymarket_alpha.py")
family = load("finance_barrier_family", ROOT / "scripts/polymarket_market_family_audit.py")


def asset_class(question: str, tags: list[str]) -> str:
    text = f"{question} {' '.join(tags)}".lower()
    if re.search(r"gold|silver|xauusd|xagusd|natural gas|crude oil|\bwti\b|commodit", text):
        return "commodity"
    if re.search(r"index|indices|indicies", text) and not re.search(r"\bspy\b|\bewy\b", text):
        return "cash_index"
    if re.search(r"stocks?|equities|\([A-Z]{1,5}\)", f"{question} {' '.join(tags)}"):
        return "listed_equity_or_etf"
    return "other"


def source_authority(description: str) -> str:
    text = description.lower()
    if "prices will be used exactly as published by pyth" in text:
        return "Pyth"
    if "resolution source" in text and "tradingview" in text:
        return "TradingView"
    return "other_or_unspecified"


def session_contract(description: str) -> str:
    text = description.lower()
    if "regular trading hours" in text and "pre-market or after-hours" in text:
        return "primary_exchange_regular_hours"
    if "trading session" in text and "business days" in text:
        return "specified_futures_or_metal_session"
    if "tradingview 1 minute candle" in text:
        return "named_chart_one_minute"
    return "other_or_unspecified"


def horizon(question: str, end_time: datetime | None) -> str:
    text = question.lower()
    if re.search(r"in (january|february|march|april|may|june|july|august|september|october|november|december)", text):
        return "calendar_month"
    if re.search(r"in 20\d{2}|by (?:december 31|year.end)", text):
        return "calendar_year"
    if end_time:
        return "dated_other"
    return "unspecified"


def direction(question: str) -> str:
    text = question.lower()
    if "(high)" in text or re.search(r"\bhit high\b|rise to|reach", text):
        return "upper_barrier"
    if "(low)" in text or re.search(r"\bdip to\b|fall to", text):
        return "lower_barrier"
    return "other"


def merged_enrichment() -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for name in ("current_family_identity_enrichment.json", "current_uncovered_identity_enrichment.json"):
        path = ROOT / "cache" / name
        if path.exists():
            mapping.update(core.read_json(path).get("by_condition_id") or {})
    return {"by_condition_id": mapping}


def audit(markets: list[dict[str, Any]], enrichment: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    rows = []
    for market in markets:
        if not family.belongs(market, family.FAMILIES["finance_barrier"]):
            continue
        raw = market.get("_sampling_raw") or {}
        question = str(market.get("question") or raw.get("question") or "")
        description = str(market.get("description") or raw.get("description") or "")
        tags = [str(tag) for tag in raw.get("tags") or []]
        end = family.end_time(market)
        source = source_authority(description)
        session = session_contract(description)
        rows.append({
            "event_id": family.event_identity(market, enrichment),
            "asset_class": asset_class(question, tags),
            "source_authority": source,
            "session_contract": session,
            "horizon": horizon(question, end),
            "direction": direction(question),
            "within_30d": bool(end and as_of <= end <= as_of + timedelta(days=30)),
            "expired_but_live": bool(end and end < as_of),
            "rule_clear": source != "other_or_unspecified" and session != "other_or_unspecified" and family.rule_complete(market),
        })
    groups = []
    keys = {(r["asset_class"], r["source_authority"], r["session_contract"], r["horizon"]) for r in rows}
    for key in sorted(keys):
        selected = [r for r in rows if (r["asset_class"], r["source_authority"], r["session_contract"], r["horizon"]) == key]
        events = {r["event_id"] for r in selected if r["event_id"]}
        clean = {r["event_id"] for r in selected if r["event_id"] and r["rule_clear"] and not r["expired_but_live"]}
        near = {r["event_id"] for r in selected if r["event_id"] and r["within_30d"]}
        directions = sorted({r["direction"] for r in selected})
        candidate = bool(len(events) >= 30 and len(clean) / len(events) >= .90 and key[0] != "other")
        groups.append({
            "asset_class": key[0], "source_authority": key[1], "session_contract": key[2], "horizon": key[3],
            "directions": directions, "contract_count": len(selected), "independent_event_count": len(events),
            "clean_event_count": len(clean), "near_term_30d_event_count": len(near),
            "phase_zero_history_candidate": candidate,
        })
    groups.sort(key=lambda r: (r["phase_zero_history_candidate"], r["near_term_30d_event_count"], r["independent_event_count"]), reverse=True)
    candidates = [r for r in groups if r["phase_zero_history_candidate"]]
    return {
        "schema_version": "polymarket-finance-barrier-taxonomy-v1", "created_at": core.now_iso(),
        "inventory_as_of": as_of.isoformat(), "contract_count": len(rows),
        "independent_event_count": len({r["event_id"] for r in rows if r["event_id"]}), "groups": groups,
        "selected_group_for_history_research": candidates[0] if candidates else None,
        "model_status": "blocked_pending_terminal_history" if candidates else "blocked_no_homogeneous_live_group",
        "paper_entry_eligible": False, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "live_orders_enabled": False, "private_api_used": False,
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Polymarket Finance Barrier Phase-0 Audit", "",
        f"- Contracts / events: {payload['contract_count']} / {payload['independent_event_count']}",
        f"- Selected history group: `{payload['selected_group_for_history_research']}`",
        f"- Status: `{payload['model_status']}`", "",
        "| Asset class | Source | Session | Horizon | Directions | Contracts | Events | Clean | <=30d | Candidate |",
        "|---|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in payload["groups"]:
        lines.append(f"| {row['asset_class']} | {row['source_authority']} | {row['session_contract']} | {row['horizon']} | {', '.join(row['directions'])} | {row['contract_count']} | {row['independent_event_count']} | {row['clean_event_count']} | {row['near_term_30d_event_count']} | {str(row['phase_zero_history_candidate']).lower()} |")
    lines.extend(["", "Crypto, weekly and daily-close contracts are excluded by the family router. Thresholds within one symbol-period event remain one independent cluster.", ""])
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    assert asset_class("Will Gold (XAUUSD) hit (HIGH) $4,300 in July?", ["Commodities"]) == "commodity"
    assert source_authority("Prices will be used exactly as published by Pyth, without rounding.") == "Pyth"
    assert direction("Will Apple (AAPL) hit (LOW) $200 in July?") == "lower_barrier"
    return {"status": "pass", "tests": ["commodity_route", "pyth_source", "lower_barrier"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--markets", default=str(ROOT / "cache/current_validation_snapshot/markets.json"))
    parser.add_argument("--snapshot-manifest", default=str(ROOT / "cache/current_validation_snapshot/snapshot-manifest.json"))
    parser.add_argument("--output", default=str(ROOT / "experiments/current-finance-barrier-taxonomy-audit.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_FINANCE_BARRIER_TAXONOMY_AUDIT.md"))
    args = parser.parse_args()
    if args.self_test:
        payload = self_test()
    else:
        snapshot = core.read_json(args.snapshot_manifest)
        if snapshot.get("terminal_cursor_proven") is not True:
            raise ValueError("snapshot is not terminal")
        payload = audit(core.read_json(args.markets), merged_enrichment(), core.parse_iso(snapshot.get("created_at")) or datetime.now(timezone.utc))
        core.write_json(args.output, payload)
        Path(args.report).write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
