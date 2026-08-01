#!/usr/bin/env python3
"""Build a read-only migration index for Recommendation History V1.

The source ledger is never rewritten. Each legacy recommendation is retained
verbatim in the index together with a content hash and a reviewability
classification. The script writes only to the two paths explicitly supplied by
the caller.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import recommendation_history as history


INDEX_SCHEMA_VERSION = "recommendation-history-v1-migration-index-v1"
SUMMARY_SCHEMA_VERSION = "recommendation-history-v1-migration-summary-v1"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def classify_legacy_record(record: Any) -> dict[str, Any]:
    reasons: list[str] = []
    if not isinstance(record, dict):
        return {
            "classification": "legacy_unreviewable",
            "reasons": ["record_not_object"],
            "due_at": None,
        }

    recommendation_id = record.get("recommendation_id")
    if not isinstance(recommendation_id, str) or not recommendation_id.strip():
        reasons.append("missing_recommendation_id")

    due_time: dt.datetime | None = None
    structured_field: str | None = None
    for field in history.STRUCTURED_DUE_FIELDS:
        if field not in record or record.get(field) in (None, ""):
            continue
        structured_field = field
        due_time = history.parse_time(str(record.get(field)))
        if due_time is None:
            reasons.append(f"invalid_{field}")
        break

    if structured_field is None:
        generated_at = history.parse_time(record.get("generated_at"))
        if generated_at is None:
            reasons.append("missing_or_invalid_generated_at")
        duration_days = history.parse_review_days(
            record.get("time_window"),
            require_explicit_duration=True,
        )
        if duration_days is None:
            reasons.append("no_structured_due_date_or_explicit_duration")
        if generated_at is not None and duration_days is not None:
            due_time = generated_at + dt.timedelta(days=duration_days)

    if reasons or due_time is None:
        return {
            "classification": "legacy_unreviewable",
            "reasons": reasons or ["due_at_undetermined"],
            "due_at": None,
        }
    return {
        "classification": "reviewable",
        "reasons": [],
        "due_at": due_time.isoformat(),
    }


def build_outputs(source_path: Path, ledger: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if ledger.get("schema_version") != history.SCHEMA_VERSION:
        raise ValueError(
            f"Expected V1 ledger schema {history.SCHEMA_VERSION!r}; "
            f"got {ledger.get('schema_version')!r}"
        )
    recommendations = ledger.get("recommendations")
    if not isinstance(recommendations, list):
        raise ValueError("ledger.recommendations must be a list")

    indexed_records: list[dict[str, Any]] = []
    classification_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for position, raw_record in enumerate(recommendations):
        classification = classify_legacy_record(raw_record)
        classification_counts[classification["classification"]] += 1
        reason_counts.update(classification["reasons"])
        indexed_records.append({
            "source_position": position,
            "recommendation_id": (
                raw_record.get("recommendation_id")
                if isinstance(raw_record, dict)
                else None
            ),
            "classification": classification["classification"],
            "classification_reasons": classification["reasons"],
            "review_due_at": classification["due_at"],
            "raw_sha256": sha256_value(raw_record),
            # Verbatim JSON value: no prices, probabilities, actions, or
            # outcomes are inferred or rewritten during migration.
            "raw_record": raw_record,
        })

    generated_at = history.utc_now()
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": {
            "path": str(source_path.resolve()),
            "sha256": source_sha256,
            "schema_version": ledger.get("schema_version"),
        },
        "records": indexed_records,
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "generated_at": generated_at,
        "source": index["source"],
        "total_records": len(indexed_records),
        "classification_counts": dict(classification_counts),
        "unreviewable_reason_counts": dict(reason_counts),
        "source_outcome_review_count": len(ledger.get("outcome_reviews", [])),
        "source_proposed_change_count": len(ledger.get("proposed_changes", [])),
        "migration_policy": {
            "source_ledger_modified": False,
            "raw_records_embedded_verbatim": True,
            "probabilities_or_prices_rewritten": False,
        },
    }
    return index, summary


def write_json_explicit(path: Path, value: Any, *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Output exists; pass --force to replace it: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a read-only Recommendation History V1 migration index"
    )
    parser.add_argument("--input", required=True, help="Existing V1 ledger to read")
    parser.add_argument("--index-output", required=True, help="Explicit path for the immutable record index")
    parser.add_argument("--summary-output", required=True, help="Explicit path for the migration summary")
    parser.add_argument("--force", action="store_true", help="Replace explicit output files if they exist")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    source = Path(args.input).resolve()
    index_output = Path(args.index_output).resolve()
    summary_output = Path(args.summary_output).resolve()
    if len({source, index_output, summary_output}) != 3:
        raise ValueError("Input, index output, and summary output must be three distinct paths")

    source_before = hashlib.sha256(source.read_bytes()).hexdigest()
    ledger = history.load_json(source)
    index, summary = build_outputs(source, ledger)
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_before:
        raise RuntimeError("Source ledger changed while migration index was being built")

    # Validate both destinations before writing either output.
    if not args.force:
        for output in (index_output, summary_output):
            if output.exists():
                raise FileExistsError(f"Output exists; pass --force to replace it: {output}")
    write_json_explicit(index_output, index, force=args.force)
    write_json_explicit(summary_output, summary, force=args.force)
    print(
        f"Indexed {summary['total_records']} V1 recommendations: "
        f"{summary['classification_counts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
