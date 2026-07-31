import sys
import unittest
from pathlib import Path


ACTIVE_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "active-alpha-paper-monitor"
    / "scripts"
)
sys.path.insert(0, str(ACTIVE_SCRIPTS))

from us_open_dynamic_scanner import build_candidate, compute_metrics  # noqa: E402


def closed_rows(count: int = 140) -> list[dict]:
    price = 100.0
    output = []
    for index in range(count):
        price *= 1.001
        output.append(
            {
                "ts": 1_700_000_000 + index * 86_400,
                "close": price,
                "adjusted_close": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "volume": 1_000_000,
                "is_closed": True,
            }
        )
    return output


class UsOpenDynamicScannerTests(unittest.TestCase):
    def test_action_price_uses_live_quote_but_path_uses_closed_bars(self) -> None:
        rows = closed_rows()
        quote = {
            "shortName": "TEST",
            "quoteType": "EQUITY",
            "regularMarketPrice": 125.0,
            "regularMarketTime": 1_800_000_000,
        }
        metrics = compute_metrics(
            "TEST",
            quote,
            rows=rows,
            benchmark_rows=rows,
            benchmark_symbol="SPY",
        )
        self.assertEqual(metrics["price"], 125.0)
        self.assertNotEqual(metrics["closed_reference_price"], 125.0)
        self.assertEqual(
            metrics["price_source"],
            "Yahoo screener regularMarketPrice",
        )

    def test_missing_live_quote_falls_back_to_last_closed_price(self) -> None:
        rows = closed_rows()
        metrics = compute_metrics(
            "TEST",
            {"shortName": "TEST", "quoteType": "EQUITY"},
            rows=rows,
            benchmark_rows=rows,
            benchmark_symbol="SPY",
        )
        self.assertEqual(metrics["price"], rows[-1]["close"])
        self.assertEqual(
            metrics["price_source"],
            "Yahoo closed daily chart fallback",
        )

    def test_large_fresh_quote_gap_blocks_mechanical_entry_levels(self) -> None:
        rows = closed_rows()
        metrics = compute_metrics(
            "TEST",
            {
                "shortName": "TEST",
                "quoteType": "EQUITY",
                "regularMarketPrice": rows[-1]["close"] * 1.12,
                "regularMarketTime": 1_800_000_000,
            },
            rows=rows,
            benchmark_rows=rows,
            benchmark_symbol="SPY",
        )
        candidate = build_candidate(metrics, {"day_gainers"}, 1, "", None)
        self.assertTrue(candidate["gap_repricing_guard"])
        self.assertIn("no entry", candidate["entry_zone"])
        self.assertIsNone(candidate["target_price"])
        self.assertIsNone(candidate["stop_loss"])


if __name__ == "__main__":
    unittest.main()
