import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from v3_evidence_snapshot import (  # noqa: E402
    EvidenceSnapshotV2,
    NumericEvidenceV2,
    evaluate_category_coverage,
)


def evidence(
    evidence_id,
    value,
    *,
    metric="spot_price",
    as_of="2026-07-25T11:59:30+08:00",
    source_role="primary",
    max_age_seconds=120,
):
    return NumericEvidenceV2(
        evidence_id=evidence_id,
        category="spot",
        symbol="SOL",
        metric=metric,
        value=value,
        unit="USD",
        as_of=as_of,
        source=f"{evidence_id}-source",
        source_role=source_role,
        max_age_seconds=max_age_seconds,
    )


def snapshot(records):
    return EvidenceSnapshotV2.build(
        snapshot_id="snapshot-1",
        generated_at="2026-07-25T12:00:02+08:00",
        cutoff_at="2026-07-25T12:00:00+08:00",
        records=records,
    )


class EvidenceSnapshotV2Test(unittest.TestCase):
    def test_numeric_evidence_id_resolves_frozen_price(self):
        frozen = snapshot([evidence("sol-price-primary", 188.25)])
        self.assertEqual(frozen.numeric("sol-price-primary"), 188.25)
        self.assertEqual(
            frozen.resolve_numeric(symbol="SOL", metric="spot_price").evidence_id,
            "sol-price-primary",
        )

    def test_stale_numeric_evidence_is_locally_blocked(self):
        frozen = snapshot(
            [
                evidence(
                    "sol-oi-stale",
                    1200000,
                    metric="open_interest",
                    as_of="2026-07-25T10:00:00+08:00",
                    max_age_seconds=300,
                ),
                evidence("sol-price-fresh", 188.25),
            ]
        )
        with self.assertRaisesRegex(ValueError, "stale"):
            frozen.numeric("sol-oi-stale")
        self.assertEqual(frozen.numeric("sol-price-fresh"), 188.25)

    def test_cross_source_disagreement_is_recorded(self):
        frozen = snapshot(
            [
                evidence("sol-primary", 100),
                evidence("sol-cross", 95, source_role="cross_check"),
            ]
        )
        self.assertEqual(len(frozen.conflicts), 1)
        self.assertEqual(frozen.conflicts[0].selected_evidence_id, "sol-primary")

    def test_close_cross_source_values_do_not_create_conflict(self):
        frozen = snapshot(
            [
                evidence("sol-primary-close", 100),
                evidence("sol-cross-close", 99.5, source_role="cross_check"),
            ]
        )
        self.assertEqual(frozen.conflicts, ())

    def test_non_numeric_and_duplicate_evidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "numeric_value_required"):
            snapshot([evidence("bad-bool", True)])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            snapshot([evidence("duplicate", 100), evidence("duplicate", 100)])

    def test_missing_options_and_onchain_degrade_only_their_categories(self):
        import json

        registry_path = (
            Path(__file__).resolve().parents[1]
            / "config"
            / "v3_data_source_registry.json"
        )
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        coverage = evaluate_category_coverage(
            snapshot([evidence("sol-price-only", 188.25)]),
            required_categories=("spot", "options", "onchain"),
            registry=registry,
        )
        self.assertEqual(coverage["status"], "partial_local_degrade")
        self.assertEqual(coverage["categories"]["spot"]["status"], "fresh")
        self.assertEqual(
            coverage["categories"]["options"]["status"],
            "missing_local_degrade",
        )
        self.assertEqual(
            coverage["categories"]["onchain"]["status"],
            "missing_local_degrade",
        )
        self.assertTrue(coverage["unrelated_categories_preserved"])


if __name__ == "__main__":
    unittest.main()
