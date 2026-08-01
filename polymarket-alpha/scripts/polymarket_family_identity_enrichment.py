#!/usr/bin/env python3
"""Fetch public Gamma event identities without mutating the source snapshot."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
    return module


core = load("identity_core", ROOT / "scripts/polymarket_alpha.py")
public = load("identity_public", ROOT / "scripts/polymarket_public_data.py")
family = load("identity_family", ROOT / "scripts/polymarket_market_family_audit.py")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def condition_id(market: dict[str, Any]) -> str:
    return str(market.get("conditionId") or (market.get("_sampling_raw") or {}).get("condition_id") or "")


def build(markets: list[dict[str, Any]], snapshot_path: Path,
          fetch_details: Callable[[list[str]], tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]] = public.fetch_gamma_details_by_condition) -> dict[str, Any]:
    targets = []
    for market in markets:
        if family.event_identity(market) is not None: continue
        cid = condition_id(market)
        if cid: targets.append(cid)
    targets = list(dict.fromkeys(targets))
    details, requests = fetch_details(targets)
    mapping = {}
    for cid in targets:
        detail = details.get(cid) or {}; events = detail.get("events") if isinstance(detail.get("events"), list) else []
        event = events[0] if events and isinstance(events[0], dict) else {}
        mapping[cid] = {"event_id": str(event.get("id")) if event.get("id") is not None else None,
                        "event_slug": event.get("slug"), "market_id": str(detail.get("id") or "") or None,
                        "status": "event_identity_found" if event.get("id") is not None else "event_identity_missing"}
    found = sum(row["status"] == "event_identity_found" for row in mapping.values())
    return {"schema_version": "polymarket-family-identity-enrichment-v1", "created_at": core.now_iso(),
            "source_snapshot": str(snapshot_path), "source_snapshot_sha256": sha256(snapshot_path),
            "target_condition_count": len(targets), "event_identity_found_count": found,
            "event_identity_coverage_pct": round(100 * found / len(targets), 4) if targets else 100.0,
            "request_count": len(requests), "failed_request_count": sum(row.get("status") == "failed" for row in requests),
            "requests": requests, "by_condition_id": mapping,
            "research_only": True, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
            "live_orders_enabled": False, "private_api_used": False}


def self_test() -> dict[str, Any]:
    markets = [{"conditionId": "c1", "_sampling_raw": {}}, {"conditionId": "c2", "_sampling_raw": {}}]
    def fake(ids):
        assert ids == ["c1", "c2"]
        return {"c1": {"id": "m1", "conditionId": "c1", "events": [{"id": "e1", "slug": "event-one"}]}}, [{"status": "ok"}]
    payload = build(markets, Path(__file__).resolve(), fake)
    assert payload["event_identity_found_count"] == 1 and payload["by_condition_id"]["c2"]["status"] == "event_identity_missing"
    return {"status": "pass", "tests": ["event_identity_mapping", "missing_identity_retained", "source_snapshot_hash"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--markets", default=str(ROOT / "cache/current_validation_snapshot/markets.json")); parser.add_argument("--output", default=str(ROOT / "cache/current_family_identity_enrichment.json")); args = parser.parse_args()
    if args.self_test:
        payload = self_test()
    else:
        status_path = ROOT / "experiments/family-research-status.json"
        statuses = (core.read_json(status_path).get("families") or {}) if status_path.exists() else {}
        target_families = [name for name, rule in family.FAMILIES.items() if (statuses.get(name) or {}).get("model_status", rule["model_status"]) == "unresearched"]
        all_markets = core.read_json(args.markets)
        scoped = [market for market in all_markets if any(family.belongs(market, family.FAMILIES[name]) for name in target_families)]
        payload = build(scoped, Path(args.markets)); payload["target_families"] = target_families
        payload["scoped_market_count"] = len(scoped)
    if not args.self_test: core.write_json(args.output, payload)
    print(json.dumps(payload if args.self_test else {k: v for k, v in payload.items() if k not in {"requests", "by_condition_id"}}, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
