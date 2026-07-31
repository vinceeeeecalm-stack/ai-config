#!/usr/bin/env python3
"""Frozen quantitative evidence snapshot with freshness and conflict semantics."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


SOURCE_ROLE_PRIORITY = {"supplemental": 100, "cross_check": 200, "primary": 300}


def _parse_iso(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}:required_iso_datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name}:invalid_iso_datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name}:timezone_required")
    return parsed


@dataclass(frozen=True)
class NumericEvidenceV2:
    evidence_id: str
    category: str
    symbol: str
    metric: str
    value: float
    unit: str
    as_of: str
    source: str
    source_role: str
    max_age_seconds: int

    def validate(self, cutoff: datetime) -> None:
        if not self.evidence_id:
            raise ValueError("evidence_id:required")
        if not self.category or not self.symbol or not self.metric:
            raise ValueError(f"{self.evidence_id}:category_symbol_metric_required")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError(f"{self.evidence_id}:numeric_value_required")
        if not math.isfinite(float(self.value)):
            raise ValueError(f"{self.evidence_id}:finite_value_required")
        if not self.unit or not self.source:
            raise ValueError(f"{self.evidence_id}:unit_source_required")
        if self.source_role not in SOURCE_ROLE_PRIORITY:
            raise ValueError(f"{self.evidence_id}:unsupported_source_role")
        if isinstance(self.max_age_seconds, bool) or not isinstance(self.max_age_seconds, int):
            raise ValueError(f"{self.evidence_id}:integer_max_age_required")
        if self.max_age_seconds <= 0:
            raise ValueError(f"{self.evidence_id}:positive_max_age_required")
        if _parse_iso(self.as_of, "evidence.as_of") > cutoff:
            raise ValueError(f"{self.evidence_id}:evidence_after_snapshot_cutoff")


@dataclass(frozen=True)
class EvidenceConflictV2:
    symbol: str
    metric: str
    selected_evidence_id: str
    compared_evidence_id: str
    selected_value: float
    compared_value: float
    relative_difference: float


@dataclass(frozen=True)
class EvidenceSnapshotV2:
    snapshot_id: str
    generated_at: str
    cutoff_at: str
    records: tuple[NumericEvidenceV2, ...]
    conflicts: tuple[EvidenceConflictV2, ...]

    @classmethod
    def build(
        cls,
        *,
        snapshot_id: str,
        generated_at: str,
        cutoff_at: str,
        records: Iterable[NumericEvidenceV2],
        conflict_relative_tolerance: float = 0.01,
    ) -> "EvidenceSnapshotV2":
        if not snapshot_id:
            raise ValueError("snapshot_id:required")
        generated = _parse_iso(generated_at, "generated_at")
        cutoff = _parse_iso(cutoff_at, "cutoff_at")
        if generated < cutoff:
            raise ValueError("generated_at:must_be_at_or_after_cutoff")
        if conflict_relative_tolerance < 0:
            raise ValueError("conflict_relative_tolerance:non_negative_required")

        items = tuple(records)
        seen: set[str] = set()
        grouped: dict[tuple[str, str], list[NumericEvidenceV2]] = {}
        for record in items:
            record.validate(cutoff)
            if record.evidence_id in seen:
                raise ValueError(f"evidence_id:duplicate:{record.evidence_id}")
            seen.add(record.evidence_id)
            grouped.setdefault((record.symbol, record.metric), []).append(record)

        conflicts: list[EvidenceConflictV2] = []
        for (symbol, metric), candidates in sorted(grouped.items()):
            if len(candidates) < 2:
                continue
            selected = max(
                candidates,
                key=lambda item: (
                    SOURCE_ROLE_PRIORITY[item.source_role],
                    _parse_iso(item.as_of, "evidence.as_of").timestamp(),
                    item.evidence_id,
                ),
            )
            for compared in candidates:
                if compared.evidence_id == selected.evidence_id:
                    continue
                denominator = max(abs(float(selected.value)), abs(float(compared.value)), 1e-12)
                difference = abs(float(selected.value) - float(compared.value)) / denominator
                if difference > conflict_relative_tolerance:
                    conflicts.append(
                        EvidenceConflictV2(
                            symbol=symbol,
                            metric=metric,
                            selected_evidence_id=selected.evidence_id,
                            compared_evidence_id=compared.evidence_id,
                            selected_value=float(selected.value),
                            compared_value=float(compared.value),
                            relative_difference=difference,
                        )
                    )
        return cls(
            snapshot_id=snapshot_id,
            generated_at=generated_at,
            cutoff_at=cutoff_at,
            records=items,
            conflicts=tuple(conflicts),
        )

    def is_fresh(self, evidence_id: str) -> bool:
        record = self.record(evidence_id)
        cutoff = _parse_iso(self.cutoff_at, "cutoff_at")
        observed = _parse_iso(record.as_of, "evidence.as_of")
        return (cutoff - observed).total_seconds() <= record.max_age_seconds

    def record(self, evidence_id: str) -> NumericEvidenceV2:
        for record in self.records:
            if record.evidence_id == evidence_id:
                return record
        raise KeyError(f"evidence_id:not_found:{evidence_id}")

    def numeric(self, evidence_id: str, *, require_fresh: bool = True) -> float:
        record = self.record(evidence_id)
        if require_fresh and not self.is_fresh(evidence_id):
            raise ValueError(f"evidence_id:stale:{evidence_id}")
        return float(record.value)

    def resolve_numeric(
        self, *, symbol: str, metric: str, require_fresh: bool = True
    ) -> NumericEvidenceV2:
        candidates = [
            item for item in self.records if item.symbol == symbol and item.metric == metric
        ]
        if require_fresh:
            candidates = [item for item in candidates if self.is_fresh(item.evidence_id)]
        if not candidates:
            suffix = "fresh_record_not_found" if require_fresh else "record_not_found"
            raise KeyError(f"{symbol}:{metric}:{suffix}")
        return max(
            candidates,
            key=lambda item: (
                SOURCE_ROLE_PRIORITY[item.source_role],
                _parse_iso(item.as_of, "evidence.as_of").timestamp(),
                item.evidence_id,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "EvidenceSnapshotV2",
            "snapshot_id": self.snapshot_id,
            "generated_at": self.generated_at,
            "cutoff_at": self.cutoff_at,
            "records": [asdict(item) for item in self.records],
            "conflicts": [asdict(item) for item in self.conflicts],
        }


def snapshot_from_payload(payload: dict[str, object]) -> EvidenceSnapshotV2:
    """Build and validate a snapshot from a portable JSON payload."""

    if not isinstance(payload, dict):
        raise ValueError("snapshot_payload:object_required")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise ValueError("records:array_required")
    try:
        records = tuple(NumericEvidenceV2(**item) for item in raw_records)
        return EvidenceSnapshotV2.build(
            snapshot_id=str(payload["snapshot_id"]),
            generated_at=str(payload["generated_at"]),
            cutoff_at=str(payload["cutoff_at"]),
            records=records,
            conflict_relative_tolerance=float(
                payload.get("conflict_relative_tolerance", 0.01)
            ),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"snapshot_payload:invalid:{exc}") from exc


def evaluate_category_coverage(
    snapshot: EvidenceSnapshotV2,
    *,
    required_categories: Iterable[str],
    registry: dict[str, object],
) -> dict[str, object]:
    """Apply category-local degradation while preserving unrelated evidence."""

    category_registry = registry.get("categories")
    if not isinstance(category_registry, dict):
        raise ValueError("source_registry:categories_object_required")
    rows: dict[str, dict[str, object]] = {}
    for category in required_categories:
        if category not in category_registry:
            raise ValueError(f"source_registry:unknown_category:{category}")
        records = [item for item in snapshot.records if item.category == category]
        fresh = [item for item in records if snapshot.is_fresh(item.evidence_id)]
        policy = category_registry[category]
        if not isinstance(policy, dict):
            raise ValueError(f"source_registry:invalid_category:{category}")
        status = (
            "fresh"
            if fresh
            else "stale_local_degrade"
            if records
            else "missing_local_degrade"
        )
        rows[category] = {
            "status": status,
            "fresh_evidence_ids": [item.evidence_id for item in fresh],
            "available_stale_evidence_ids": [
                item.evidence_id for item in records if item not in fresh
            ],
            "degrade_action": policy.get("local_degrade_if_missing"),
        }
    degraded = sorted(
        category for category, row in rows.items() if row["status"] != "fresh"
    )
    return {
        "status": "complete" if not degraded else "partial_local_degrade",
        "categories": rows,
        "degraded_categories": degraded,
        "unrelated_categories_preserved": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and freeze an EvidenceSnapshotV2 JSON payload."
    )
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json")
    args = parser.parse_args()
    with Path(args.input_json).expanduser().resolve().open(
        "r", encoding="utf-8"
    ) as handle:
        payload = json.load(handle)
    snapshot = snapshot_from_payload(payload)
    text = json.dumps(
        snapshot.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
    )
    if args.output_json:
        Path(args.output_json).expanduser().resolve().write_text(
            text + "\n", encoding="utf-8"
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
