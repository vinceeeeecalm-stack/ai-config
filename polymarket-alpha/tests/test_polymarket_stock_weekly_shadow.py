import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_stock_weekly_shadow.py"
spec = importlib.util.spec_from_file_location("polymarket_stock_weekly_shadow", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class StockWeeklyShadowTests(unittest.TestCase):
    def test_self_test(self):
        result = module.self_test()
        self.assertEqual(result["status"], "pass")
        self.assertIs(result["live_orders_enabled"], False)

    def test_capture_at_cutoff_is_missed(self):
        cutoff = datetime(2026, 7, 13, 13, 29, tzinfo=timezone.utc)
        self.assertEqual(module.capture_state(cutoff, cutoff), "capture_window_missed")

    def test_capture_inside_window_is_eligible(self):
        cutoff = datetime(2026, 7, 13, 13, 29, tzinfo=timezone.utc)
        self.assertEqual(module.capture_state(cutoff - timedelta(minutes=1), cutoff), "eligible")

    def test_domain_provenance(self):
        self.assertEqual(module.DOMAIN, "stock_weekly")

    def test_invalid_gamma_price_rejected(self):
        market = {"outcomes": '["Yes", "No"]', "outcomePrices": '["1", "0"]'}
        self.assertIsNone(module.gamma_yes_probability(market))

    def test_fresh_two_sided_book_records_realistic_execution(self):
        observed = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        book = {
            "timestamp": str(int(observed.timestamp() * 1000)), "hash": "hash",
            "bids": [{"price": "0.40", "size": "1000"}],
            "asks": [{"price": "0.42", "size": "1000"}],
        }
        result = module.executable_market_snapshot(book, 0.41, observed)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["market_probability"], 0.41)
        self.assertTrue(result["execution_quote"]["fillable"])
        self.assertTrue(result["execution_gate_passed"])

    def test_inactive_book_age_is_diagnostic_not_false_staleness(self):
        observed = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        old = observed - timedelta(seconds=module.BOOK_ACTIVITY_STALE_SECONDS + 1)
        book = {
            "timestamp": str(int(old.timestamp() * 1000)),
            "bids": [{"price": "0.40", "size": "1000"}],
            "asks": [{"price": "0.42", "size": "1000"}],
        }
        result = module.executable_market_snapshot(book, 0.41, observed)
        self.assertIsNotNone(result)
        self.assertTrue(result["book_activity_age_exceeds_15m"])


if __name__ == "__main__":
    unittest.main()
