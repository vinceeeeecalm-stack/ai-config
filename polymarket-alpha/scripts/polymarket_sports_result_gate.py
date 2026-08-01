#!/usr/bin/env python3
"""Guard sports post-match learning against unproven final results.

This module is deliberately read-only by default.  It normalizes a review row
into VERIFIED or UNKNOWN and refuses to derive direction hits, Brier scores, or
calibration eligibility from a missing/invalid result.  It does not create a
paper fill or alter the append-only sports ledger.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


VALID_OUTCOMES = {"YES", "NO"}
UNKNOWN_STATUSES = {"UNKNOWN", "UNVERIFIED", "PENDING", "MISSING", "CONFLICTING", "DISPUTED"}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _outcome(value: Any) -> str | None:
    value = _text(value).upper()
    if value in VALID_OUTCOMES:
        return value
    if value in {"1", "TRUE"}:
        return "YES"
    if value in {"0", "FALSE"}:
        return "NO"
    return None


def _result_sources(row: dict[str, Any]) -> list[str]:
    sources: list[str] = []
    value = row.get("result_sources")
    if isinstance(value, list):
        sources.extend(_text(item) for item in value if _text(item))
    elif _text(value):
        sources.append(_text(value))
    for key in ("official_result_source", "independent_result_source", "official_source", "independent_source"):
        if _text(row.get(key)):
            sources.append(_text(row.get(key)))
    return list(dict.fromkeys(sources))


def normalize_result(row: dict[str, Any]) -> dict[str, Any]:
    """Return a result-graded row without trusting stale hit fields."""
    status = _text(row.get("final_result_status") or row.get("result_status") or "").upper()
    result = next((_text(row.get(key)) for key in
                   ("final_result", "official_result", "settlement_result", "result_text", "result")
                   if _text(row.get(key))), "")
    outcome = _outcome(row.get("binary_outcome"))
    sources = _result_sources(row)
    source_conflict = row.get("source_conflict") is True or row.get("result_source_conflict") is True
    if source_conflict:
        reason = "result_sources_conflict"
    elif status in UNKNOWN_STATUSES:
        reason = "final_result_missing_or_unverified" if not result else "result_status_not_verified"
    elif status and status not in {"VERIFIED", "RESOLVED", "SETTLED", "COMPLETE"}:
        reason = "result_status_not_verified"
    elif not result:
        reason = "final_result_missing"
    elif outcome is None:
        reason = "binary_outcome_missing_or_invalid"
    elif not sources:
        reason = "result_sources_missing"
    else:
        direction_hit = row.get("direction_hit") if isinstance(row.get("direction_hit"), bool) else None
        return {
            "review_id": row.get("review_id"),
            "final_result_status": "VERIFIED",
            "final_result": result,
            "result_sources": sources,
            "binary_outcome": int(outcome == "YES"),
            # Only expose a hit after result verification.  The stored value is
            # retained as evidence, but cannot make an UNKNOWN row a hit.
            "direction_hit": direction_hit,
            "eligible_for_direction_calibration": direction_hit is not None,
            "eligible_for_price_edge_calibration": bool(row.get("eligible_for_price_edge_calibration")) and
                row.get("counterfactual_fill_valid") is True,
            "eligible_for_exit_calibration": bool(row.get("eligible_for_exit_calibration")) and
                row.get("counterfactual_fill_valid") is True,
            "brier_score": row.get("brier_score_midpoint"),
            "unknown_reason": None,
        }
    return {
        "review_id": row.get("review_id"),
        "final_result_status": "UNKNOWN",
        "final_result": result or None,
        "result_sources": sources,
        "binary_outcome": None,
        "direction_hit": None,
        "eligible_for_direction_calibration": False,
        "eligible_for_price_edge_calibration": False,
        "eligible_for_exit_calibration": False,
        "brier_score": None,
        "unknown_reason": reason,
    }


def audit(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    normalized = [normalize_result(row) for row in rows]
    verified = [row for row in normalized if row["final_result_status"] == "VERIFIED"]
    unknown = [row for row in normalized if row["final_result_status"] == "UNKNOWN"]
    hits = sum(row["direction_hit"] is True for row in verified)
    misses = sum(row["direction_hit"] is False for row in verified)
    return {
        "schema_version": "polymarket-sports-result-gate-v1",
        "rows": len(normalized),
        "verified_results": len(verified),
        "unknown_or_unverified_results": len(unknown),
        "direction_hits_verified_only": hits,
        "direction_misses_verified_only": misses,
        "direction_hit_rate_verified_only": (hits / (hits + misses) if hits + misses else None),
        "direction_calibration_eligible": sum(row["eligible_for_direction_calibration"] for row in normalized),
        "price_edge_calibration_eligible": sum(row["eligible_for_price_edge_calibration"] for row in normalized),
        "exit_calibration_eligible": sum(row["eligible_for_exit_calibration"] for row in normalized),
        "unknown_rows": unknown,
        "rows": normalized,
        "unknown_excluded_from_all_result_metrics": True,
        "paper_only": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "real_money_execution_authorized": False,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"line {line_no} is not an object")
        rows.append(value)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = audit(load_jsonl(args.input))
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
