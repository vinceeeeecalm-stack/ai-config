import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "manual-investment-strategy-operator" / "scripts"
FIXTURES = ROOT / "manual-investment-strategy-operator" / "tests" / "fixtures"

import sys

sys.path.insert(0, str(SCRIPTS))

from v3_portfolio_state import resolve_portfolio_state_from_files  # noqa: E402


class DeterministicPortfolioStateV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = resolve_portfolio_state_from_files(
            overrides_path=FIXTURES / "synthetic_current_overrides_v2.json",
            legacy_ledger_path=FIXTURES / "synthetic_legacy_ledger_v2.json",
        )

    def test_current_baseline_has_no_ghost_positions_or_cash(self):
        for key in (
            "holdings.NIGHT.quantity",
            "holdings.ENA.quantity",
            "holdings.USDT.quantity",
            "holdings.SOXL.quantity",
            "cash.crypto.USDT",
            "cash.us_equity.USD",
        ):
            self.assertEqual(self.state.get(key), 0.0, key)

    def test_stale_cash_and_soxl_conflicts_are_explicit(self):
        conflicts = {item.key: item for item in self.state.conflicts}
        self.assertEqual(conflicts["cash.crypto.USDT"].rejected_value, 222.0)
        self.assertEqual(conflicts["holdings.USDT.quantity"].rejected_value, 333.0)
        self.assertEqual(conflicts["holdings.SOXL.quantity"].rejected_value, 7.0)

    def test_test_inputs_are_public_synthetic_fixtures(self):
        for filename in (
            "synthetic_current_overrides_v2.json",
            "synthetic_legacy_ledger_v2.json",
        ):
            path = FIXTURES / filename
            self.assertTrue(path.is_file())
            self.assertTrue(path.is_relative_to(FIXTURES))

    def test_every_selected_field_has_provenance(self):
        for key, value in self.state.fields.items():
            self.assertTrue(value.as_of, key)
            self.assertTrue(value.source, key)
            self.assertTrue(value.source_kind, key)
            self.assertTrue(value.evidence_id, key)
            self.assertGreaterEqual(value.confidence, 0)
            self.assertLessEqual(value.confidence, 1)


if __name__ == "__main__":
    unittest.main()
