#!/usr/bin/env python3
"""Validate US crypto legislative catalysts without promoting vote rumors."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


STAGE_ORDER = {
    "introduced": 1,
    "committee_markup_scheduled": 2,
    "committee_advanced": 3,
    "floor_schedule_pending": 4,
    "floor_vote_scheduled": 5,
    "floor_passed": 6,
    "other_chamber_or_reconciliation_pending": 7,
    "presented_to_president": 8,
    "signed_or_vetoed": 9,
}
OFFICIAL_TIERS = {
    "official_floor",
    "official_clerk",
    "official_committee",
    "official_bill_status",
}


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timezone_required:{value}")
    return parsed


def normalize(value: Any) -> str:
    return " ".join(str(value or "").lower().replace(".", " ").split())


def source_valid(item: dict[str, Any]) -> bool:
    try:
        parse_iso(str(item.get("checked_at") or item.get("occurred_at") or ""))
    except (TypeError, ValueError):
        return False
    return (
        str(item.get("source_url") or "").startswith(("https://", "http://"))
        and item.get("source_tier") in OFFICIAL_TIERS
    )


def action_stage(
    actions: list[dict[str, Any]],
) -> tuple[str, str, list[str]]:
    valid = [item for item in actions if source_valid(item)]
    if not valid:
        return "introduced", "unknown", ["official_action_evidence_missing"]
    chosen = max(
        valid,
        key=lambda item: (
            parse_iso(
                str(item.get("occurred_at") or item.get("checked_at"))
            ),
            STAGE_ORDER.get(str(item.get("stage")), 0),
        ),
    )
    return (
        str(chosen.get("stage") or "introduced"),
        str(chosen.get("chamber") or "unknown"),
        [],
    )


def item_mentions_bill(
    item: dict[str, Any], identifiers: list[str]
) -> bool:
    if "bill_present" in item:
        return item.get("bill_present") is True
    haystack = normalize(
        " ".join(
            str(value)
            for value in (
                item.get("published_item_ids"),
                item.get("published_items"),
                item.get("schedule_text"),
            )
            if value
        )
    )
    return any(
        normalize(identifier) in haystack
        for identifier in identifiers
        if identifier
    )


def schedule_verdict(
    checks: list[dict[str, Any]], identifiers: list[str]
) -> tuple[str, str | None, list[str], list[str]]:
    official = [item for item in checks if source_valid(item)]
    failures = [
        str(item.get("failure") or "invalid_official_schedule_evidence")
        for item in checks
        if not source_valid(item)
    ]
    scheduled = [
        item for item in official if item_mentions_bill(item, identifiers)
    ]
    if scheduled:
        chosen = max(
            scheduled,
            key=lambda item: parse_iso(str(item.get("checked_at"))),
        )
        return (
            "official_floor_vote_scheduled",
            chosen.get("scheduled_at"),
            sorted({str(item.get("chamber")) for item in official}),
            failures,
        )
    chambers = sorted({str(item.get("chamber")) for item in official})
    if official:
        return (
            "not_on_published_floor_schedule",
            None,
            chambers,
            failures,
        )
    return (
        "schedule_unavailable",
        None,
        chambers,
        failures or ["official_schedule_missing"],
    )


def remaining_steps(stage: str, chamber: str) -> list[str]:
    if stage == "signed_or_vetoed":
        return []
    if stage == "presented_to_president":
        return ["presidential_action"]
    if stage == "other_chamber_or_reconciliation_pending":
        return [
            "resolve_text_between_chambers",
            "final_passage",
            "presidential_action",
        ]
    if stage == "floor_passed":
        return [
            "other_chamber_or_reconciliation",
            "final_passage",
            "presidential_action",
        ]
    if stage == "floor_vote_scheduled":
        return [
            f"{chamber}_floor_vote",
            "remaining_chamber_or_reconciliation",
            "presidential_action",
        ]
    if stage in {"committee_advanced", "floor_schedule_pending"}:
        return [
            f"{chamber}_floor_scheduling",
            f"{chamber}_floor_vote",
            "remaining_chamber_or_reconciliation",
            "presidential_action",
        ]
    return [
        "committee_action",
        "floor_scheduling",
        "floor_vote",
        "remaining_chamber_or_reconciliation",
        "presidential_action",
    ]


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    cutoff = parse_iso(str(payload.get("cutoff_at") or ""))
    bill_id = str(payload.get("bill_id") or "")
    bill_name = str(payload.get("bill_name") or "")
    identifiers = [bill_id, bill_name, *(payload.get("aliases") or [])]
    errors: list[str] = []
    if not bill_id or not bill_name:
        errors.append("bill_identity_missing")

    stage, chamber, action_errors = action_stage(
        list(payload.get("official_actions") or [])
    )
    errors.extend(action_errors)
    (
        schedule_status,
        scheduled_at,
        checked_chambers,
        schedule_failures,
    ) = schedule_verdict(
        list(payload.get("official_schedule_checks") or []),
        identifiers,
    )

    is_weekend = cutoff.weekday() >= 5
    required_chambers = {"house", "senate"}
    weekend_complete = not is_weekend or required_chambers.issubset(
        {item.lower() for item in checked_chambers}
    )
    if is_weekend and not weekend_complete:
        errors.append(
            "weekend_house_and_senate_schedule_coverage_incomplete"
        )

    secondary_claims = list(payload.get("secondary_claims") or [])
    vote_claim_present = any(
        "vote" in normalize(item.get("claim"))
        or "投票" in str(item.get("claim") or "")
        for item in secondary_claims
    )
    false_vote_claim_blocked = (
        vote_claim_present
        and schedule_status != "official_floor_vote_scheduled"
    )
    if schedule_status == "official_floor_vote_scheduled":
        event_label = "official_floor_vote_scheduled"
    elif vote_claim_present:
        event_label = "expected_window_unconfirmed"
    else:
        event_label = schedule_status

    baseline_mentioned = payload.get("baseline_mentioned_event")
    omission = baseline_mentioned is False
    quality = (
        "verified_official_schedule"
        if schedule_status != "schedule_unavailable" and not errors
        else "degraded"
    )
    return {
        "schema_version": "us-crypto-legislative-event-radar-v1",
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "cutoff_at": cutoff.isoformat(),
        "bill_id": bill_id,
        "bill_name": bill_name,
        "current_chamber": chamber,
        "current_stage": stage,
        "remaining_steps": remaining_steps(stage, chamber),
        "official_schedule_status": schedule_status,
        "event_label": event_label,
        "scheduled_at": scheduled_at,
        "official_schedule_chambers_checked": checked_chambers,
        "weekend_72h_handoff_required": is_weekend,
        "weekend_72h_handoff_complete": weekend_complete,
        "lookahead_start": cutoff.isoformat(),
        "lookahead_end": (cutoff + timedelta(hours=72)).isoformat(),
        "next_review_at": (cutoff + timedelta(hours=6)).isoformat(),
        "secondary_claims": secondary_claims,
        "secondary_vote_claim_present": vote_claim_present,
        "false_vote_claim_blocked": false_vote_claim_blocked,
        "source_failures": schedule_failures,
        "affected_assets": list(payload.get("affected_assets") or []),
        "transmission_logic": list(
            payload.get("transmission_logic") or []
        ),
        "market_confirmation_status": payload.get(
            "market_confirmation_status"
        )
        or "not_evaluated",
        "baseline_coverage_status": (
            "omitted_material_event_watch"
            if omission
            else "covered"
            if baseline_mentioned is True
            else "unknown"
        ),
        "omission_detected": omission,
        "data_quality": quality,
        "max_active_action": "watch",
        "forecast_probability_pct": None,
        "probability_note": (
            "Legislative importance and schedule claims are not price "
            "probabilities."
        ),
        "live_orders_enabled": False,
        "human_confirmation_required": True,
        "errors": errors,
        "passed": not errors,
    }


def self_test() -> dict[str, Any]:
    fixture = {
        "cutoff_at": "2026-07-26T15:40:00Z",
        "bill_id": "H.R. 3633",
        "bill_name": "CLARITY Act",
        "aliases": ["Digital Asset Market Clarity Act"],
        "official_actions": [
            {
                "stage": "floor_passed",
                "chamber": "house",
                "occurred_at": "2025-07-17T19:30:00Z",
                "checked_at": "2026-07-26T15:30:00Z",
                "source_tier": "official_clerk",
                "source_url": "https://clerk.house.gov/Votes/2025199",
            },
            {
                "stage": "committee_advanced",
                "chamber": "senate",
                "occurred_at": "2026-05-14T16:00:00Z",
                "checked_at": "2026-07-26T15:31:00Z",
                "source_tier": "official_committee",
                "source_url": "https://www.banking.senate.gov/",
            },
        ],
        "official_schedule_checks": [
            {
                "chamber": "house",
                "checked_at": "2026-07-26T15:32:00Z",
                "source_tier": "official_floor",
                "source_url": "https://repcloakroom.house.gov/",
                "published_items": ["House in pro forma at 9:30 AM"],
            },
            {
                "chamber": "senate",
                "checked_at": "2026-07-26T15:33:00Z",
                "source_tier": "official_floor",
                "source_url": "https://www.senate.gov/",
                "published_items": [
                    "5:30 PM cloture vote on Clayton nomination"
                ],
            },
        ],
        "secondary_claims": [
            {
                "claim": "CLARITY vote Monday",
                "source_tier": "secondary",
            }
        ],
        "baseline_mentioned_event": False,
    }
    result = evaluate(fixture)
    checks = {
        "senate_committee_not_floor_passage": (
            result["current_stage"] == "committee_advanced"
        ),
        "monday_vote_claim_blocked": (
            result["false_vote_claim_blocked"] is True
        ),
        "official_schedule_absence_visible": (
            result["official_schedule_status"]
            == "not_on_published_floor_schedule"
        ),
        "weekend_handoff_complete": (
            result["weekend_72h_handoff_complete"] is True
        ),
        "omission_recorded": result["omission_detected"] is True,
        "no_probability_fabricated": (
            result["forecast_probability_pct"] is None
        ),
        "no_live_action": (
            result["max_active_action"] == "watch"
            and result["live_orders_enabled"] is False
        ),
    }
    return {
        "status": "ok" if all(checks.values()) else "failed",
        "checks": checks,
        "result": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-json", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        payload = self_test()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["status"] == "ok" else 1
    if not args.input_json:
        parser.error(
            "--input-json is required unless --self-test is used"
        )
    payload = evaluate(
        json.loads(args.input_json.read_text(encoding="utf-8"))
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
