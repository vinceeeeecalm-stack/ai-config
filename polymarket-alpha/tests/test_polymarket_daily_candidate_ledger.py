import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_daily_candidate_ledger.py"
spec = importlib.util.spec_from_file_location("daily_candidate_ledger", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)


class DailyCandidateLedgerTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_unsafe_flags_are_rejected(self):
        ledger = module.new_ledger(); ledger["counts_as_paper_trade"] = True
        with self.assertRaises(ValueError): module.assert_safe(ledger)


if __name__ == "__main__": unittest.main()
