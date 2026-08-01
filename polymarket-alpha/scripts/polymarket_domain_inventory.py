#!/usr/bin/env python3
"""Build a reproducible domain and expiry inventory from a verified live snapshot."""
from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
    return module


core = load("polymarket_domain_inventory_core", ROOT / "scripts" / "polymarket_alpha.py")
public = load("polymarket_domain_inventory_public", ROOT / "scripts" / "polymarket_public_data.py")


def build(snapshot_dir: Path) -> dict:
    verification = public.verify_manifest(snapshot_dir)
    if verification["status"] != "pass":
        raise ValueError("snapshot manifest integrity failed")
    manifest = json.loads((snapshot_dir / "snapshot-manifest.json").read_text(encoding="utf-8"))
    rows = json.loads((snapshot_dir / "markets.json").read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    all_counts = Counter(); expiry_counts = Counter(); confidence_counts = Counter(); reason_counts = Counter(); examples: dict[str, list[dict]] = {}; low_confidence_examples = []
    eligible_total = 0
    for raw in rows:
        market = core.normalize_market(raw); domain = market["domain"]; all_counts[domain] += 1
        confidence_counts[market["domain_route_confidence"]] += 1; reason_counts[market["domain_route_reason"]] += 1
        if market["domain_route_confidence"] == "low" and len(low_confidence_examples) < 20:
            low_confidence_examples.append({"market_id":market["market_id"],"question":market["question"],"domain":domain,"reason":market["domain_route_reason"]})
        hours = core.hours_to_expiry(market.get("end_date"), now)
        if hours is None or not 1 <= hours <= 30 * 24:
            continue
        eligible_total += 1; expiry_counts[domain] += 1
        bucket = examples.setdefault(domain, [])
        if len(bucket) < 5:
            bucket.append({
                "market_id": market["market_id"], "condition_id": market.get("condition_id"),
                "question": market["question"], "hours_to_expiry": hours,
                "market_yes_price": market.get("market_yes_price"),
            })
    return {
        "schema_version": "polymarket-domain-inventory-v1", "created_at": core.now_iso(),
        "domain_router_version": core.DOMAIN_ROUTER_VERSION,
        "snapshot_dir": str(snapshot_dir), "snapshot_manifest_created_at": manifest.get("created_at"),
        "snapshot_verification": verification, "terminal_cursor_proven": manifest.get("terminal_cursor_proven"),
        "all_tradeable_markets": len(rows), "domain_counts": dict(all_counts),
        "one_hour_to_30_day_markets": eligible_total, "eligible_domain_counts": dict(expiry_counts),
        "routing_confidence_counts":dict(confidence_counts),"routing_reason_counts":dict(reason_counts),"low_confidence_examples":low_confidence_examples,
        "examples": examples, "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }


def markdown(payload: dict) -> str:
    lines = [
        "# Polymarket Live Domain Inventory", "",
        f"- Router: `{payload['domain_router_version']}`",
        f"- Verified tradable markets: {payload['all_tradeable_markets']}",
        f"- Markets expiring in 1 hour–30 days: {payload['one_hour_to_30_day_markets']}",
        f"- Terminal cursor proven: `{str(payload['terminal_cursor_proven']).lower()}`", "",
        f"- High/medium/low confidence: {payload['routing_confidence_counts'].get('high',0)} / {payload['routing_confidence_counts'].get('medium',0)} / {payload['routing_confidence_counts'].get('low',0)}", "",
        "## Domain coverage", "",
        "| Domain | All | 1h–30d |", "|---|---:|---:|",
    ]
    for domain in sorted(payload["domain_counts"]):
        lines.append(f"| {domain} | {payload['domain_counts'][domain]} | {payload['eligible_domain_counts'].get(domain, 0)} |")
    lines.extend(["", "This inventory is routing evidence, not a probability estimate or trade recommendation.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verified Polymarket domain inventory")
    parser.add_argument("--snapshot-dir", default=str(ROOT / "cache" / "forward_cycle_002_20260711"))
    parser.add_argument("--output", default=str(ROOT / "experiments" / "current-domain-inventory.json"))
    parser.add_argument("--report", default=str(ROOT / "reports" / "CURRENT_DOMAIN_INVENTORY.md"))
    args = parser.parse_args(); payload = build(Path(args.snapshot_dir))
    core.write_json(args.output, payload)
    target = Path(args.report); target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp"); temp.write_text(markdown(payload), encoding="utf-8"); temp.replace(target)
    print(json.dumps({"markets": payload["all_tradeable_markets"], "eligible": payload["one_hour_to_30_day_markets"], "router": payload["domain_router_version"], "output": args.output}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
