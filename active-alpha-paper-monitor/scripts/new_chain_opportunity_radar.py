#!/usr/bin/env python3
"""Discover new-chain DEX assets without granting execution authority."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "config" / "new_chain_event_registry.json"
DEFAULT_REPLAY = (
    ROOT / "config" / "incident_replays" / "cashcat_202607.json"
)
USER_AGENT = "active-alpha-new-chain-radar/1.0 research-only"


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timezone_required:{value}")
    return parsed


def as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def event_stage(event: dict[str, Any], assets: list[dict[str, Any]]) -> str:
    if event.get("verified") is not True:
        return "unverified_event"
    if event.get("status") != "mainnet_live":
        return "event_watch"
    if not assets:
        return "ecosystem_watch"
    return "asset_watch"


def market_gate(asset: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    liquidity = as_float(asset.get("liquidity_usd"))
    volume_1h = as_float(asset.get("volume_1h_usd"))
    volume_24h = as_float(asset.get("volume_24h_usd"))
    traders_1h = as_float(asset.get("unique_traders_1h"))
    traders_24h = as_float(asset.get("unique_traders_24h"))
    if liquidity is None or liquidity < float(policy["min_liquidity_usd"]):
        failures.append("liquidity_below_or_missing")
    if not (
        volume_1h is not None
        and volume_1h >= float(policy["min_volume_1h_usd"])
    ) and not (
        volume_24h is not None
        and volume_24h >= float(policy["min_volume_24h_usd"])
    ):
        failures.append("volume_acceleration_below_or_missing")
    if not (
        traders_1h is not None
        and traders_1h >= float(policy["min_unique_traders_1h"])
    ) and not (
        traders_24h is not None
        and traders_24h >= float(policy["min_unique_traders_1h"])
    ):
        failures.append("unique_trader_breadth_below_or_missing")
    if len(asset.get("source_urls") or []) < 2:
        failures.append("cross_source_market_confirmation_missing")
    if asset.get("affiliation_status") not in {
        "official_asset",
        "official_ecosystem_partner",
        "unaffiliated_narrative_asset",
    }:
        failures.append("affiliation_status_unknown")
    if not asset.get("narrative_links"):
        failures.append("narrative_or_utility_link_missing")
    return failures


def security_gate(asset: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    security = asset.get("security") or {}
    failures: list[str] = []
    required_confirmations = int(
        policy["min_cross_source_contract_confirmations"]
    )
    if int(security.get("contract_verified_source_count") or 0) < required_confirmations:
        failures.append("contract_cross_verification_missing")
    if security.get("honeypot_status") != "clean":
        failures.append("sellability_or_honeypot_not_verified")
    for key in ("buy_tax_pct", "sell_tax_pct"):
        value = as_float(security.get(key))
        if value is None:
            failures.append(f"{key}_missing")
        elif value > float(policy["max_buy_or_sell_tax_pct"]):
            failures.append(f"{key}_too_high")
    if security.get("owner_privileges") not in {
        "none",
        "renounced",
        "timelocked_and_disclosed",
    }:
        failures.append("owner_privileges_not_cleared")
    if security.get("lp_status") not in {
        "locked",
        "burned",
        "concentrated_liquidity_verified",
    }:
        failures.append("lp_status_not_verified")
    top10 = as_float(security.get("top10_non_lp_holder_pct"))
    if top10 is None:
        failures.append("top10_holder_concentration_missing")
    elif top10 > float(policy["max_top10_non_lp_holder_pct"]):
        failures.append("top10_holder_concentration_high")
    deployer = as_float(security.get("deployer_holder_pct"))
    if deployer is None:
        failures.append("deployer_concentration_missing")
    elif deployer > float(policy["max_deployer_holder_pct"]):
        failures.append("deployer_concentration_high")
    return failures


def derivation_chain(event: dict[str, Any], asset: dict[str, Any]) -> list[str]:
    return [
        f"FACT official event: {event.get('entity')} {event.get('status')}",
        f"FACT ecosystem: {event.get('chain_name') or 'product event'}",
        (
            "FACT venue: "
            f"{asset.get('dex') or 'DEX'} pair {asset.get('pair_address') or 'unknown'}"
        ),
        (
            "DERIVED selection: liquidity/volume/trader participation "
            "evaluated outside the CEX universe"
        ),
        (
            "JUDGMENT narrative/utility: "
            + "; ".join(asset.get("narrative_links") or ["missing"])
        ),
        f"FACT affiliation: {asset.get('affiliation_status') or 'unknown'}",
    ]


def evaluate_snapshot(
    snapshot: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    event = snapshot.get("event") or {}
    assets = list(snapshot.get("assets") or [])
    stage = event_stage(event, assets)
    evaluated: list[dict[str, Any]] = []
    for asset in assets:
        market_failures = market_gate(asset, policy)
        security_failures = security_gate(asset, policy)
        severe = any(
            item
            in {
                "sellability_or_honeypot_not_verified",
                "owner_privileges_not_cleared",
                "lp_status_not_verified",
            }
            for item in security_failures
        )
        if not market_failures and not security_failures:
            action = "paper_only"
        elif severe and any(
            word in " ".join(security_failures)
            for word in ("too_high", "concentration_high")
        ):
            action = "risk_alert"
        else:
            action = "watch"
        evaluated.append(
            {
                "symbol": asset.get("symbol"),
                "contract_address": asset.get("contract_address"),
                "pair_address": asset.get("pair_address"),
                "captured_at": asset.get("captured_at")
                or snapshot.get("cutoff_at"),
                "source_urls": list(asset.get("source_urls") or []),
                "market_snapshot": {
                    "price_usd": as_float(asset.get("price_usd")),
                    "liquidity_usd": as_float(asset.get("liquidity_usd")),
                    "volume_1h_usd": as_float(asset.get("volume_1h_usd")),
                    "volume_24h_usd": as_float(asset.get("volume_24h_usd")),
                    "unique_traders_1h": as_float(
                        asset.get("unique_traders_1h")
                    ),
                    "unique_traders_24h": as_float(
                        asset.get("unique_traders_24h")
                    ),
                    "txns_1h": as_float(asset.get("txns_1h")),
                },
                "affiliation_status": asset.get("affiliation_status"),
                "discovery_origin": "new_chain_event",
                "market_gate_status": "pass" if not market_failures else "degraded",
                "security_capacity_status": (
                    "pass" if not security_failures else "incomplete_or_failed"
                ),
                "market_gate_failures": market_failures,
                "security_gate_failures": security_failures,
                "catalyst_derivation_chain": derivation_chain(event, asset),
                "why_now": (
                    "A verified chain event is transmitting into a newly "
                    "tradeable DEX pool with measurable participation."
                ),
                "what_invalidates": (
                    "Official event reversal, contract mismatch, sellability "
                    "failure, liquidity collapse or concentrated distribution."
                ),
                "max_candidate_action": action,
                "forecast_probability_pct": None,
                "probability_note": (
                    "Discovery strength is not a price probability; historical "
                    "or forward samples are required."
                ),
            }
        )
    max_action = "watch"
    if any(item["max_candidate_action"] == "risk_alert" for item in evaluated):
        max_action = "risk_alert"
    elif any(item["max_candidate_action"] == "paper_only" for item in evaluated):
        max_action = "paper_only"
    return {
        "schema_version": "new-chain-opportunity-radar-v1",
        "snapshot_id": snapshot.get("snapshot_id"),
        "cutoff_at": snapshot.get("cutoff_at"),
        "event_id": event.get("event_id"),
        "event_stage": stage,
        "candidates": evaluated,
        "max_active_action": max_action,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
        "historical_baseline_mutation_allowed": False,
    }


def _normalize_gecko_pool(row: dict[str, Any], captured_at: str) -> dict[str, Any]:
    attributes = row.get("attributes") or {}
    relationships = row.get("relationships") or {}
    base = ((relationships.get("base_token") or {}).get("data") or {}).get("id")
    txns = attributes.get("transactions") or {}
    h1_txns = txns.get("h1") or {}
    volume = attributes.get("volume_usd") or {}
    return {
        "symbol": attributes.get("name"),
        "contract_address": base,
        "pair_address": attributes.get("address"),
        "dex": ((relationships.get("dex") or {}).get("data") or {}).get("id"),
        "affiliation_status": "unknown_affiliation",
        "narrative_links": [],
        "source_urls": ["https://www.geckoterminal.com/dex-api"],
        "captured_at": captured_at,
        "price_usd": as_float(attributes.get("base_token_price_usd")),
        "liquidity_usd": as_float(attributes.get("reserve_in_usd")),
        "volume_1h_usd": as_float(volume.get("h1")),
        "volume_24h_usd": as_float(volume.get("h24")),
        "unique_traders_1h": None,
        "txns_1h": int(h1_txns.get("buys") or 0)
        + int(h1_txns.get("sells") or 0),
        "security": {},
    }


def scan_live_event(
    event: dict[str, Any], policy: dict[str, Any], timeout: float
) -> dict[str, Any]:
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    assets: dict[str, dict[str, Any]] = {}
    source_status: list[dict[str, Any]] = []
    network = event.get("geckoterminal_network")
    if network:
        for kind in ("new_pools", "trending_pools"):
            url = (
                "https://api.geckoterminal.com/api/v2/networks/"
                f"{urllib.parse.quote(str(network))}/{kind}?page=1"
            )
            try:
                payload = get_json(url, timeout)
                rows = payload.get("data") or []
                for row in rows:
                    item = _normalize_gecko_pool(row, captured_at)
                    key = str(item.get("pair_address") or item.get("contract_address"))
                    assets[key] = item
                source_status.append(
                    {"source": f"geckoterminal_{kind}", "status": "ok", "count": len(rows)}
                )
            except Exception as exc:  # noqa: BLE001 - source-local degradation
                source_status.append(
                    {
                        "source": f"geckoterminal_{kind}",
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    query = event.get("chain_name")
    if query:
        url = (
            "https://api.dexscreener.com/latest/dex/search?"
            + urllib.parse.urlencode({"q": str(query)})
        )
        try:
            payload = get_json(url, timeout)
            rows = [
                row
                for row in payload.get("pairs") or []
                if row.get("chainId") == event.get("dexscreener_chain_id")
            ]
            for row in rows:
                pair = str(row.get("pairAddress") or "")
                base = row.get("baseToken") or {}
                existing = assets.get(pair, {})
                existing.update(
                    {
                        "symbol": base.get("symbol") or existing.get("symbol"),
                        "contract_address": base.get("address")
                        or existing.get("contract_address"),
                        "pair_address": pair or existing.get("pair_address"),
                        "dex": row.get("dexId") or existing.get("dex"),
                        "affiliation_status": "unknown_affiliation",
                        "narrative_links": [],
                        "source_urls": sorted(
                            set(
                                (existing.get("source_urls") or [])
                                + [row.get("url") or url]
                            )
                        ),
                        "captured_at": captured_at,
                        "price_usd": as_float(row.get("priceUsd")),
                        "liquidity_usd": as_float(
                            (row.get("liquidity") or {}).get("usd")
                        ),
                        "volume_1h_usd": as_float(
                            (row.get("volume") or {}).get("h1")
                        ),
                        "volume_24h_usd": as_float(
                            (row.get("volume") or {}).get("h24")
                        ),
                        "txns_1h": sum(
                            int(value or 0)
                            for value in (
                                (row.get("txns") or {}).get("h1") or {}
                            ).values()
                        ),
                        "security": existing.get("security") or {},
                    }
                )
                assets[pair] = existing
            source_status.append(
                {"source": "dexscreener_search", "status": "ok", "count": len(rows)}
            )
        except Exception as exc:  # noqa: BLE001 - source-local degradation
            source_status.append(
                {
                    "source": "dexscreener_search",
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    snapshot = {
        "snapshot_id": f"new-chain-{event.get('event_id')}-{captured_at}",
        "cutoff_at": captured_at,
        "event": event,
        "assets": list(assets.values()),
    }
    result = evaluate_snapshot(snapshot, policy)
    result["source_status"] = source_status
    result["data_quality_status"] = (
        "verified"
        if source_status and all(item["status"] == "ok" for item in source_status)
        else "partial_local_degrade"
    )
    return result


def run_replay(path: Path, policy: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = [evaluate_snapshot(item, policy) for item in payload["snapshots"]]
    failures: list[str] = []
    for source, result in zip(payload["snapshots"], results):
        if result["event_stage"] != source["expected_stage"]:
            failures.append(
                f"{source['snapshot_id']}:stage:{result['event_stage']}"
            )
        expected_action = source.get("expected_max_action")
        if expected_action and result["max_active_action"] != expected_action:
            failures.append(
                f"{source['snapshot_id']}:action:{result['max_active_action']}"
            )
        if source.get("hindsight_return_must_not_be_probability"):
            if any(
                item.get("forecast_probability_pct") is not None
                for item in result["candidates"]
            ):
                failures.append(f"{source['snapshot_id']}:hindsight_probability")
    return {
        "schema_version": payload["schema_version"],
        "incident_id": payload["incident_id"],
        "passed": not failures,
        "failures": failures,
        "results": results,
        "counterfactual_predictability_claimed": False,
    }


def self_test(policy: dict[str, Any]) -> dict[str, Any]:
    replay = run_replay(DEFAULT_REPLAY, policy)
    synthetic = {
        "snapshot_id": "synthetic-qualified-new-chain",
        "cutoff_at": "2026-07-25T12:00:00+08:00",
        "event": {
            "event_id": "synthetic-mainnet",
            "entity": "Example",
            "chain_name": "Example Chain",
            "status": "mainnet_live",
            "verified": True,
        },
        "assets": [
            {
                "symbol": "EXAMPLE",
                "contract_address": "0x1",
                "pair_address": "0x2",
                "dex": "example-dex",
                "affiliation_status": "unaffiliated_narrative_asset",
                "narrative_links": ["documented narrative"],
                "source_urls": ["source-a", "source-b"],
                "liquidity_usd": 1000000,
                "volume_1h_usd": 250000,
                "unique_traders_1h": 250,
                "security": {
                    "contract_verified_source_count": 2,
                    "honeypot_status": "clean",
                    "buy_tax_pct": 0,
                    "sell_tax_pct": 0,
                    "owner_privileges": "renounced",
                    "lp_status": "locked",
                    "top10_non_lp_holder_pct": 20,
                    "deployer_holder_pct": 1
                }
            }
        ]
    }
    qualified = evaluate_snapshot(synthetic, policy)
    failures = list(replay["failures"])
    if qualified["max_active_action"] != "paper_only":
        failures.append("qualified_fixture_not_paper_only")
    if qualified["candidates"][0]["discovery_origin"] != "new_chain_event":
        failures.append("discovery_origin_missing")
    return {
        "passed": not failures,
        "failures": failures,
        "replay": replay,
        "qualified_fixture": qualified,
        "live_orders_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--event-id")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    policy = registry["defaults"]
    if args.self_test:
        result = self_test(policy)
    elif args.replay:
        result = run_replay(args.replay, policy)
    elif args.live:
        events = registry.get("events") or []
        if args.event_id:
            events = [
                event for event in events if event.get("event_id") == args.event_id
            ]
        result = {
            "schema_version": "new-chain-live-scan-v1",
            "generated_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "events": [
                scan_live_event(event, policy, args.timeout)
                for event in events
                if event.get("verified") is True
            ],
            "live_orders_enabled": False,
        }
    else:
        result = run_replay(DEFAULT_REPLAY, policy)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.get("passed", True) else 1


if __name__ == "__main__":
    sys.exit(main())
