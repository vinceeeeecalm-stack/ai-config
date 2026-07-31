import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).resolve().parents[1] / "scripts/polymarket_basketball_lab.py"
S = importlib.util.spec_from_file_location("basketball_lab", P)
M = importlib.util.module_from_spec(S); assert S.loader; S.loader.exec_module(M)


class BasketballLabTests(unittest.TestCase):
    def test_self(self):
        self.assertEqual(M.self_test()["status"], "pass")

    def test_opening_after_t24_is_retained_but_not_t24_eligible(self):
        market = {"id": "m", "question": "A vs. B", "sportsMarketType": "moneyline", "gameStartTime": "2026-07-12T20:00:00Z", "startDate": "2026-07-12T10:00:00Z", "outcomes": '["A","B"]', "outcomePrices": '["1","0"]', "clobTokenIds": '["a","b"]', "closed": True}
        row, reason = M.strict_moneyline("nba", {"id": "e"}, market)
        self.assertIsNone(reason); self.assertFalse(row["available_at_t24"]); self.assertTrue(row["available_at_t60"])

    def test_fractional_timestamp(self):
        self.assertIsNotNone(M.parse_time("2026-03-24T12:05:13.26752Z"))
        self.assertIsNotNone(M.parse_time("2025-09-21 21:00:00+00"))


if __name__ == "__main__":
    unittest.main()
