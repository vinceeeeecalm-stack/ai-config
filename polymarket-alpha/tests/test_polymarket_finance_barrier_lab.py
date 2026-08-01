import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).resolve().parents[1] / "scripts/polymarket_finance_barrier_lab.py"
S = importlib.util.spec_from_file_location("finance_barrier", P)
M = importlib.util.module_from_spec(S)
assert S.loader
S.loader.exec_module(M)


class FinanceBarrierTests(unittest.TestCase):
    def test_self(self):
        self.assertEqual(M.self_test()["status"], "pass")

    def test_equity_session(self):
        description = "Only prices achieved during regular trading hours qualify; pre-market or after-hours prices do not qualify."
        self.assertEqual(M.session_contract(description), "primary_exchange_regular_hours")


if __name__ == "__main__":
    unittest.main()
