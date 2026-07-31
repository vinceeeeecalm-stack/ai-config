#!/usr/bin/env python3
"""Validate the compact single-best-candidate report contract."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ALLOWED_VERDICTS = {
    "small_entry_allowed",
    "wait_for_entry",
    "manage_existing_only",
    "no_qualified_trade",
}
ALLOWED_EVIDENCE_TYPES = {"FACT", "DERIVED", "JUDGMENT"}
ALLOWED_PROBABILITY_TYPES = {
    "historical_path",
    "event_estimate",
    "scenario_weight",
    "judgment_only",
    "unavailable",
}


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _require(mapping: dict[str, Any], fields: list[str], prefix: str, errors: list[str]) -> None:
    for field in fields:
        if not _present(mapping.get(field)):
            errors.append(f"missing:{prefix}{field}")


def _parse_iso(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        errors.append(f"invalid_datetime:{field}")
        return
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"invalid_datetime:{field}")


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if payload.get("mode") != "single_best_candidate":
        errors.append("invalid:mode")
    if "candidates" in payload:
        errors.append("invalid:multiple_candidates_not_allowed")

    candidate = payload.get("candidate")
    if not isinstance(candidate, dict):
        return {"status": "fail", "errors": ["missing:candidate"], "warnings": []}

    _require(
        candidate,
        [
            "symbol",
            "asset_class",
            "plain_language_description",
            "verdict",
            "current_price",
            "price_as_of",
            "price_source",
            "why_now",
            "evidence_chain",
            "entry_plan",
            "exit_plan",
            "holding_period",
            "scenarios",
            "probability_provenance",
            "invalidation",
            "funding",
        ],
        "candidate.",
        errors,
    )

    if candidate.get("verdict") not in ALLOWED_VERDICTS:
        errors.append("invalid:candidate.verdict")
    _parse_iso(candidate.get("price_as_of"), "candidate.price_as_of", errors)

    evidence = candidate.get("evidence_chain", [])
    if not isinstance(evidence, list) or not evidence:
        errors.append("invalid:candidate.evidence_chain")
    else:
        seen: set[str] = set()
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"invalid:evidence_chain[{index}]")
                continue
            kind = item.get("type")
            if kind not in ALLOWED_EVIDENCE_TYPES:
                errors.append(f"invalid:evidence_chain[{index}].type")
            else:
                seen.add(kind)
            _require(item, ["statement", "basis", "as_of"], f"evidence_chain[{index}].", errors)
        missing_types = ALLOWED_EVIDENCE_TYPES - seen
        if missing_types:
            errors.append("missing:evidence_types:" + ",".join(sorted(missing_types)))

    entry = candidate.get("entry_plan", {})
    if isinstance(entry, dict):
        _require(
            entry,
            ["window_start", "window_end", "allowed_session", "primary_zone", "trigger", "deadline", "do_not_buy_if"],
            "entry_plan.",
            errors,
        )
        _parse_iso(entry.get("window_start"), "entry_plan.window_start", errors)
        _parse_iso(entry.get("window_end"), "entry_plan.window_end", errors)
    else:
        errors.append("invalid:candidate.entry_plan")

    exit_plan = candidate.get("exit_plan", {})
    if isinstance(exit_plan, dict):
        _require(
            exit_plan,
            ["target_1", "target_2", "price_stop", "time_stop", "latest_exit_or_review"],
            "exit_plan.",
            errors,
        )
        _parse_iso(exit_plan.get("latest_exit_or_review"), "exit_plan.latest_exit_or_review", errors)
    else:
        errors.append("invalid:candidate.exit_plan")

    scenarios = candidate.get("scenarios", [])
    if not isinstance(scenarios, list) or len(scenarios) != 3:
        errors.append("invalid:scenarios_require_bull_base_bear")
    else:
        labels = {item.get("case") for item in scenarios if isinstance(item, dict)}
        if labels != {"bull", "base", "bear"}:
            errors.append("invalid:scenario_labels")
        total = 0.0
        for index, item in enumerate(scenarios):
            if not isinstance(item, dict):
                errors.append(f"invalid:scenarios[{index}]")
                continue
            _require(item, ["probability_pct", "price_path", "window", "drivers"], f"scenarios[{index}].", errors)
            try:
                total += float(item.get("probability_pct", 0))
            except (TypeError, ValueError):
                errors.append(f"invalid:scenarios[{index}].probability_pct")
        if abs(total - 100.0) > 0.01:
            errors.append(f"invalid:scenario_probability_sum:{total}")

    provenance = candidate.get("probability_provenance", {})
    if isinstance(provenance, dict):
        _require(
            provenance,
            ["probability_type", "method", "sample_size", "base_rate", "adjustments", "limitations"],
            "probability_provenance.",
            errors,
        )
        probability_type = provenance.get("probability_type")
        if probability_type not in ALLOWED_PROBABILITY_TYPES:
            errors.append("invalid:probability_provenance.probability_type")
        if probability_type in {"judgment_only", "unavailable"} and candidate.get("verdict") == "small_entry_allowed":
            errors.append("invalid:uncalibrated_probability_cannot_allow_entry")
        sample_size = provenance.get("sample_size")
        if isinstance(sample_size, (int, float)) and sample_size < 20:
            warnings.append("low_sample_size_use_wide_probability_range")
    else:
        errors.append("invalid:candidate.probability_provenance")

    funding = candidate.get("funding", {})
    if isinstance(funding, dict):
        _require(funding, ["deployable_cash", "cash_source", "position_size", "settlement_constraint"], "funding.", errors)
        cash = funding.get("deployable_cash")
        if candidate.get("verdict") == "small_entry_allowed" and (not isinstance(cash, (int, float)) or cash <= 0):
            errors.append("invalid:entry_allowed_without_deployable_cash")
    else:
        errors.append("invalid:candidate.funding")

    runners_up = payload.get("runners_up", [])
    if not isinstance(runners_up, list) or len(runners_up) > 2:
        errors.append("invalid:runners_up_max_two")

    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
        "allowed_max_action": candidate.get("verdict") if not errors else "no_qualified_trade",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = validate(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
