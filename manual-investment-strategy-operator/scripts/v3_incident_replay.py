#!/usr/bin/env python3
"""Deterministic historical-incident replay helpers for V3 regression tests."""

from __future__ import annotations

from typing import Any

from v3_evidence_snapshot import snapshot_from_payload


def replay_impulse_coverage(
    payload: dict[str, Any], *, move_threshold_pct: float = 15.0
) -> dict[str, Any]:
    """Audit whether a frozen snapshot contained a material missed impulse.

    This is an outcome audit, not a claim that the move was predictable.
    """

    snapshot = snapshot_from_payload(payload["evidence_snapshot"])
    symbol = str(payload["symbol"])
    previous = snapshot.resolve_numeric(
        symbol=symbol, metric="previous_price", require_fresh=False
    )
    current = snapshot.resolve_numeric(
        symbol=symbol, metric="current_price", require_fresh=True
    )
    volume_ratio = snapshot.resolve_numeric(
        symbol=symbol, metric="volume_ratio_24h", require_fresh=True
    )
    catalyst_count = snapshot.resolve_numeric(
        symbol=symbol, metric="verified_catalyst_count_24h", require_fresh=True
    )
    move_pct = (current.value / previous.value - 1.0) * 100.0
    material_impulse = (
        move_pct >= move_threshold_pct
        and volume_ratio.value >= 1.5
        and catalyst_count.value >= 1
    )
    prior_coverage = bool(payload.get("prior_watch_or_recommendation_present"))
    return {
        "schema_version": "incident-replay-v3",
        "snapshot_id": snapshot.snapshot_id,
        "symbol": symbol,
        "move_pct": move_pct,
        "volume_ratio_24h": volume_ratio.value,
        "verified_catalyst_count_24h": catalyst_count.value,
        "material_impulse": material_impulse,
        "coverage_gap": material_impulse and not prior_coverage,
        "counterfactual_predictability_claimed": False,
        "reusable_rule": (
            "For every dynamic crypto scan, retain the top 24h price/volume "
            "impulses and verified catalyst count in the frozen snapshot, "
            "including rejected candidates and rejection reasons."
        ),
    }
