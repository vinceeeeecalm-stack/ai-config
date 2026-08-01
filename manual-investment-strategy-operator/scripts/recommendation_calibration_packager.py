#!/usr/bin/env python3
"""Build a human-confirmation package for recommendation calibration.

This script does not mutate recommendation_history.json. It turns due outcome
review drafts into an explicit queue with exact `recommendation_history.py
review` commands that a human can run after checking the evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
DEFAULT_LEDGER = ROOT / "manual-investment-strategy-operator" / "recommendations" / "recommendation_history.json"
if not DEFAULT_LEDGER.exists():
    DEFAULT_LEDGER = SCRIPT_DIR.parents[0] / "recommendations" / "recommendation_history.json"
RECOMMENDATION_HISTORY = SCRIPT_DIR / "recommendation_history.py"
OUTCOME_REVIEWER = SCRIPT_DIR / "recommendation_outcome_reviewer.py"

RESOLVED_FOR_CALIBRATION = {"hit", "failed"}


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HISTORY = load_module(RECOMMENDATION_HISTORY, "recommendation_history_for_calibration_packager")
REVIEWER = load_module(OUTCOME_REVIEWER, "recommendation_outcome_reviewer_for_calibration_packager")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def shell_quote(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text.replace("'", "'\"'\"'") + "'"


def calibration_counts(ledger: dict[str, Any]) -> dict[str, Any]:
    recommendations = ledger.get("recommendations") or []
    outcome_reviews = ledger.get("outcome_reviews") or []
    counts: dict[str, int] = {}
    for record in recommendations:
        status = str(record.get("outcome_status") or "missing")
        counts[status] = counts.get(status, 0) + 1
    resolved = sum(counts.get(status, 0) for status in RESOLVED_FOR_CALIBRATION)
    return {
        "total_recommendations": len(recommendations),
        "outcome_status_counts": counts,
        "outcome_reviews": len(outcome_reviews),
        "resolved_for_calibration": resolved,
        "hit_count": counts.get("hit", 0),
        "failed_count": counts.get("failed", 0),
        "pending_count": counts.get("pending", 0),
        "superseded_count": counts.get("superseded", 0),
        "superseded_not_counted": counts.get("superseded", 0),
    }


def build_confirmation_command(review: dict[str, Any], ledger_path: Path) -> str:
    cmd = [
        "python3",
        "manual-investment-strategy-operator/scripts/recommendation_history.py",
        "--path",
        str(ledger_path),
        "review",
        "--recommendation-id",
        str(review.get("recommendation_id")),
        "--outcome-status",
        str(review.get("recommended_outcome_status")),
        "--reviewed-at",
        utc_now(),
    ]
    actual_return = review.get("actual_return_pct_window")
    if isinstance(actual_return, (int, float)):
        cmd.extend(["--actual-return-pct", f"{actual_return:.6f}"])
    notes = f"{review.get('reason') or 'human confirmed outcome'}; draft_status={review.get('status')}"
    cmd.extend(["--actual-notes", notes])
    cmd.extend(["--attribution", "market_price_review,human_confirmed"])
    cmd.extend(["--what-should-change", "Update probability calibration only; do not auto-change strategy parameters without separate proposed_change approval."])
    return " ".join(shell_quote(item) for item in cmd)


def next_due_items(ledger: dict[str, Any], as_of: dt.datetime, max_items: int) -> list[dict[str, Any]]:
    rows = []
    for record in ledger.get("recommendations") or []:
        if record.get("outcome_status") != "pending":
            continue
        due = HISTORY.due_at(record)
        if due is None:
            rows.append(
                {
                    "recommendation_id": record.get("recommendation_id"),
                    "symbol": record.get("symbol"),
                    "status": "due_at_unparseable",
                    "due_at": None,
                    "days_until_due": None,
                    "forecast_probability_pct": record.get("forecast_probability_pct"),
                    "action": record.get("action"),
                }
            )
            continue
        rows.append(
            {
                "recommendation_id": record.get("recommendation_id"),
                "symbol": record.get("symbol"),
                "status": "due" if due <= as_of else "upcoming",
                "due_at": due.isoformat(),
                "days_until_due": (due - as_of).days,
                "forecast_probability_pct": record.get("forecast_probability_pct"),
                "action": record.get("action"),
            }
        )
    return sorted(rows, key=lambda item: item.get("due_at") or "9999")[:max_items]


def build_package(
    ledger_path: Path,
    as_of: dt.datetime,
    fetch_market_data: bool,
    max_items: int,
    min_calibration_resolved: int,
) -> dict[str, Any]:
    ledger = load_json(ledger_path)
    counts = calibration_counts(ledger)
    review_panel = REVIEWER.build_review_panel(ledger, as_of, fetch_market_data, max_items=max_items)
    confirmation_queue = []
    for review in review_panel.get("draft_reviews") or []:
        outcome = review.get("recommended_outcome_status")
        if outcome not in RESOLVED_FOR_CALIBRATION:
            # not_triggered/expired/invalidated are still useful hygiene, but do
            # not satisfy the current hit/failed probability calibration gate.
            calibration_credit = False
        else:
            calibration_credit = True
        confirmation_queue.append(
            {
                "recommendation_id": review.get("recommendation_id"),
                "symbol": review.get("symbol"),
                "recommended_outcome_status": outcome,
                "calibration_credit_if_confirmed": calibration_credit,
                "actual_return_pct_window": review.get("actual_return_pct_window"),
                "reason": review.get("reason"),
                "evidence_status": review.get("status"),
                "confirmation_command": build_confirmation_command(review, ledger_path),
            }
        )

    calibration_credit_ready = sum(1 for item in confirmation_queue if item["calibration_credit_if_confirmed"])
    resolved_after_ready = counts["resolved_for_calibration"] + calibration_credit_ready
    return {
        "generated_at": utc_now(),
        "as_of": as_of.isoformat(),
        "package_version": "recommendation-calibration-package-v1",
        "ledger_path": str(ledger_path),
        "fetch_market_data": fetch_market_data,
        "live_orders_enabled": False,
        "mutates_ledger": False,
        "human_confirmation_required": True,
        "counts": counts,
        "calibration_gate": {
            "min_resolved_required": min_calibration_resolved,
            "current_resolved": counts["resolved_for_calibration"],
            "ready_hit_failed_if_confirmed": calibration_credit_ready,
            "projected_resolved_after_ready_confirmations": resolved_after_ready,
            "resolved_needed_after_ready_confirmations": max(0, min_calibration_resolved - resolved_after_ready),
            "gate_would_pass_after_ready_confirmations": resolved_after_ready >= min_calibration_resolved,
        },
        "review_panel_summary": {
            "pending_count": review_panel.get("pending_count"),
            "due_or_reviewable_count": review_panel.get("due_or_reviewable_count"),
            "draft_review_count": review_panel.get("draft_review_count"),
            "upcoming_count": review_panel.get("upcoming_count"),
            "due_unresolved_count": len(review_panel.get("due_unresolved") or []),
        },
        "confirmation_queue": confirmation_queue,
        "due_unresolved": review_panel.get("due_unresolved") or [],
        "next_due_items": next_due_items(ledger, as_of, max_items),
        "superseded_policy": {
            "superseded_count": counts["superseded_count"],
            "counted_for_calibration": False,
            "reason": "Superseded recommendations were replaced by newer equivalent plans and must not be counted as hit/failed probability evidence unless a human performs a separate market-window review and explicitly changes the outcome.",
        },
        "operator_note": "Run the confirmation_command only after human review. This package is evidence workflow, not an investment recommendation.",
    }


def render_markdown(package: dict[str, Any]) -> str:
    gate = package["calibration_gate"]
    counts = package["counts"]
    lines = [
        f"# Recommendation Calibration Package | {package['as_of']}",
        "",
        "This package does not mutate the ledger. It prepares human-confirmable outcome reviews for probability calibration.",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Total recommendations | {counts['total_recommendations']} |",
        f"| Pending | {counts['pending_count']} |",
        f"| Superseded, not counted | {counts['superseded_not_counted']} |",
        f"| Current hit/failed resolved | {gate['current_resolved']} |",
        f"| Ready hit/failed if confirmed | {gate['ready_hit_failed_if_confirmed']} |",
        f"| Resolved needed after ready confirmations | {gate['resolved_needed_after_ready_confirmations']} |",
        f"| Gate would pass after confirmations | `{gate['gate_would_pass_after_ready_confirmations']}` |",
        "",
        "## Confirmation Queue",
        "",
        "| Recommendation | Symbol | Draft Outcome | Calibration Credit | Reason |",
        "|---|---|---|---|---|",
    ]
    queue = package.get("confirmation_queue") or []
    if queue:
        for item in queue:
            lines.append(
                f"| `{item.get('recommendation_id')}` | `{item.get('symbol')}` | "
                f"`{item.get('recommended_outcome_status')}` | `{item.get('calibration_credit_if_confirmed')}` | "
                f"{item.get('reason') or '-'} |"
            )
    else:
        lines.append("| none | - | - | - | - |")
    lines.extend(["", "## Next Due Items", "", "| Recommendation | Symbol | Status | Due At | Days |", "|---|---|---|---|---:|"])
    for item in package.get("next_due_items") or []:
        lines.append(
            f"| `{item.get('recommendation_id')}` | `{item.get('symbol')}` | "
            f"{item.get('status')} | {item.get('due_at')} | {item.get('days_until_due')} |"
        )
    lines.extend(["", "## Confirmation Commands", ""])
    if queue:
        for item in queue:
            lines.append("```bash")
            lines.append(item["confirmation_command"])
            lines.append("```")
    else:
        lines.append("No confirmable hit/failed draft outcomes are ready yet.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build human-confirmation package for recommendation calibration")
    parser.add_argument("--path", default=str(DEFAULT_LEDGER))
    parser.add_argument("--as-of", default="")
    parser.add_argument("--fetch-market-data", action="store_true")
    parser.add_argument("--max-items", type=int, default=20)
    parser.add_argument("--min-calibration-resolved", type=int, default=10)
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    as_of = REVIEWER.parse_time(args.as_of) if args.as_of else dt.datetime.now(dt.timezone.utc)
    if as_of is None:
        raise ValueError("--as-of must be ISO-8601")
    package = build_package(
        Path(args.path),
        as_of,
        fetch_market_data=args.fetch_market_data,
        max_items=args.max_items,
        min_calibration_resolved=args.min_calibration_resolved,
    )
    if args.output:
        write_json(Path(args.output), package)
    if args.markdown_output:
        write_text(Path(args.markdown_output), render_markdown(package))
    if args.format == "markdown":
        print(render_markdown(package))
    else:
        print(json.dumps(package, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
