import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).resolve().parents[1] / "scripts/polymarket_uncovered_market_audit.py"
S = importlib.util.spec_from_file_location("uncovered", P)
M = importlib.util.module_from_spec(S); assert S.loader; S.loader.exec_module(M)


class UncoveredAuditTests(unittest.TestCase):
    def test_self(self):
        self.assertEqual(M.self_test()["status"], "pass")

    def test_technology_candidate(self):
        market = {"_sampling_raw": {"tags": ["AI", "OpenAI"]}}
        self.assertTrue(M.matches(market, M.CANDIDATES["technology_events"]))


if __name__ == "__main__":
    unittest.main()
