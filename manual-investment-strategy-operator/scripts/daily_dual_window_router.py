#!/usr/bin/env python3
"""Resolve the single 08:30/23:30 Asia/Shanghai automation branch."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

from recommendation_execution_calendar_gate import baseline_snapshot_sha256


TZ = ZoneInfo("Asia/Shanghai")
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def parse_now(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(TZ)
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def route(now: dt.datetime) -> dict[str, object]:
    minutes = now.hour * 60 + now.minute
    if 6 * 60 <= minutes < 16 * 60:
        branch = "morning"
        report_date = now.date()
        slot = "08:30"
    elif minutes >= 16 * 60:
        branch = "evening"
        report_date = now.date()
        slot = "23:30"
    elif minutes < 3 * 60:
        branch = "evening"
        report_date = now.date() - dt.timedelta(days=1)
        slot = "23:30-delayed"
    else:
        branch = "out_of_window"
        report_date = now.date()
        slot = "none"
    date_text = report_date.isoformat()
    payload = {
        "status": "ok" if branch != "out_of_window" else "blocked_out_of_window",
        "timezone": "Asia/Shanghai",
        "local_now": now.isoformat(),
        "branch": branch,
        "nominal_slot": slot,
        "report_date": date_text,
        "morning_report": f"manual-investment-strategy-operator/reports/{date_text}-0830-morning-investment-console.md",
        "evening_report": f"manual-investment-strategy-operator/reports/{date_text}-2330-evening-review.md",
        "live_orders_enabled": False,
        "external_messages_enabled": False,
        "impulse_continuity_required_even_without_morning": branch == "evening",
        "historical_baseline_mutation_allowed": False,
        "weekend_legislative_lookahead_required": now.weekday() >= 5,
    }
    if branch == "morning":
        payload["required_morning_steps"] = [
            "run_us_crypto_legislative_event_radar",
            "run_position_first_review",
            "run_dynamic_candidate_scan",
            "freeze_formal_candidate_baselines",
        ]
    return payload


def valid_same_day_ad_hoc(record: dict[str, object], report_date: str, cutoff: dt.datetime) -> bool:
    if (
        record.get("baseline_frozen") is not True
        or record.get("historical_baseline_mutation_forbidden") is not True
        or record.get("calendar_gate_status_at_publish") != "passed"
        or record.get("baseline_snapshot_sha256") != baseline_snapshot_sha256(record)
    ):
        return False
    try:
        generated = parse_now(str(record.get("generated_at") or ""))
        price_as_of = parse_now(str(record.get("price_as_of") or ""))
    except ValueError:
        return False
    if generated.date().isoformat() != report_date or generated > cutoff or price_as_of > cutoff:
        return False
    required = [
        record.get("recommendation_id"),
        record.get("price_as_of"),
        record.get("forecast_probability_pct"),
        record.get("entry_trigger") or record.get("entry_range"),
        record.get("target_1_price_or_scenario") or record.get("target_range"),
        record.get("stop_or_invalid"),
        record.get("execution_action") or record.get("action_allowed") or record.get("action"),
    ]
    return all(value not in (None, "", [], {}) for value in required)


def resolve_evening_baseline(payload: dict[str, object], workspace_root: Path) -> dict[str, object]:
    if payload.get("branch") != "evening":
        return payload
    morning_path = workspace_root / str(payload["morning_report"])
    if morning_path.exists() and morning_path.stat().st_size > 0:
        payload["evening_baseline_status"] = "morning_report"
        payload["evening_baseline_path"] = str(morning_path)
        payload["evening_review_scope"] = "morning_review"
        payload["required_evening_steps"] = [
            "review_frozen_baseline",
            "run_us_crypto_legislative_event_radar",
            "run_candidate_official_event_relay",
            "run_read_only_impulse_continuity",
            "write_outcome_and_omission_audit",
        ]
        return payload
    history_path = workspace_root / "manual-investment-strategy-operator/recommendations/recommendation_history.json"
    records: list[dict[str, object]] = []
    if history_path.exists():
        try:
            raw = json.loads(history_path.read_text(encoding="utf-8"))
            records = [item for item in raw.get("recommendations", []) if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError):
            records = []
    cutoff = parse_now(str(payload["local_now"]))
    eligible = [item for item in records if valid_same_day_ad_hoc(item, str(payload["report_date"]), cutoff)]
    if eligible:
        payload["evening_baseline_status"] = "same_day_ad_hoc_recommendations"
        payload["evening_review_scope"] = "ad_hoc_recommendation_review"
        payload["ad_hoc_recommendation_ids"] = [item["recommendation_id"] for item in eligible]
        payload["ad_hoc_symbols"] = sorted({str(item.get("symbol")) for item in eligible if item.get("symbol")})
    else:
        payload["evening_baseline_status"] = "missing_attribution_baseline"
        payload["evening_review_scope"] = "attribution_blocked_impulse_continuity_only"
        payload["ad_hoc_recommendation_ids"] = []
        payload["ad_hoc_symbols"] = []
    payload["required_evening_steps"] = [
        "run_us_crypto_legislative_event_radar",
        "run_candidate_official_event_relay",
        "run_read_only_impulse_continuity",
        "write_current_safety_status_even_if_attribution_blocked",
    ]
    payload["current_safety_scan_blocked"] = False
    return payload


def self_test() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "manual-investment-strategy-operator/recommendations/recommendation_history.json"
        history.parent.mkdir(parents=True)
        history.write_text(json.dumps({"recommendations": [{
            "recommendation_id": "fixture-ad-hoc",
            "generated_at": "2026-07-21T21:41:00+08:00",
            "price_as_of": "2026-07-21T21:40:00+08:00",
            "forecast_probability_pct": 55,
            "entry_trigger": "closed-bar confirmation",
            "target_1_price_or_scenario": "target fixture",
            "stop_or_invalid": "stop fixture",
            "execution_action": "no_deploy",
            "baseline_frozen": True,
            "historical_baseline_mutation_forbidden": True,
            "calendar_gate_status_at_publish": "passed",
        }]}), encoding="utf-8")
        raw = json.loads(history.read_text(encoding="utf-8"))
        fixture = raw["recommendations"][0]
        fixture["baseline_snapshot_sha256"] = baseline_snapshot_sha256(fixture)
        history.write_text(json.dumps(raw), encoding="utf-8")
        fallback = resolve_evening_baseline(route(parse_now("2026-07-21T23:30:00+08:00")), root)
    checks = {
        "0830_morning": route(parse_now("2026-07-21T08:30:00+08:00"))["branch"] == "morning",
        "morning_legislative_radar_required": "run_us_crypto_legislative_event_radar" in route(parse_now("2026-07-21T08:30:00+08:00"))["required_morning_steps"],
        "sunday_72h_lookahead_required": route(parse_now("2026-07-26T08:30:00+08:00"))["weekend_legislative_lookahead_required"] is True,
        "2330_evening": route(parse_now("2026-07-21T23:30:00+08:00"))["branch"] == "evening",
        "delayed_evening_date": route(parse_now("2026-07-22T00:15:00+08:00"))["report_date"] == "2026-07-21",
        "forbidden_gap": route(parse_now("2026-07-21T04:00:00+08:00"))["branch"] == "out_of_window",
        "ad_hoc_fallback_without_morning": fallback["evening_review_scope"] == "ad_hoc_recommendation_review",
        "impulse_continues_without_morning": fallback["impulse_continuity_required_even_without_morning"] is True,
        "baseline_remains_immutable": fallback["historical_baseline_mutation_allowed"] is False,
        "legislative_radar_required": "run_us_crypto_legislative_event_radar" in fallback["required_evening_steps"],
    }
    return {"status": "ok" if all(checks.values()) else "failed", "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--now")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--workspace-root", type=Path, default=WORKSPACE_ROOT)
    args = parser.parse_args()
    payload = self_test() if args.self_test else resolve_evening_baseline(route(parse_now(args.now)), args.workspace_root)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
