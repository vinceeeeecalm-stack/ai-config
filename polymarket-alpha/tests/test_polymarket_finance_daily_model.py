import importlib.util
import unittest
from pathlib import Path

P = Path(__file__).resolve().parents[1] / "scripts/polymarket_finance_daily_model.py"
S = importlib.util.spec_from_file_location("finance_model", P)
M = importlib.util.module_from_spec(S); assert S.loader; S.loader.exec_module(M)


class FinanceModelTests(unittest.TestCase):
    def test_self(self):
        self.assertEqual(M.self_test()["status"], "pass")

    def test_future_ohlc_is_excluded(self):
        markets = [{"event_id": "1", "symbol": "X", "settlement_date": "2026-02-01", "winning_outcome": "Up"}]
        ohlc = {"X": [{"date": f"2026-01-{day:02d}", "close": 100 + day} for day in range(1, 32)] + [{"date": "2026-02-02", "close": 9999}]}
        rows = M.event_features(markets, ohlc)
        self.assertEqual(rows[0]["last_feature_date"], "2026-01-31")
        self.assertFalse(rows[0]["post_event_feature_used"])


if __name__ == "__main__":
    unittest.main()
