import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_public_data.py"
spec = importlib.util.spec_from_file_location("polymarket_public_data", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class PolymarketPublicDataTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_manifest_detects_tamper(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            module.write_snapshot(root, [], [], {}, "live")
            self.assertEqual(module.verify_manifest(root)["status"], "pass")
            (root / "markets.json").write_text("[]\n ", encoding="utf-8")
            result = module.verify_manifest(root)
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(any("mismatch" in item for item in result["failures"]))

    def test_non_official_host_blocked(self):
        with self.assertRaises(ValueError):
            module.assert_public_url("https://example.com/markets")

    def test_sampling_pagination_proves_terminal_and_normalizes_tradeable_market(self):
        calls = []
        row = {
            "condition_id": "0xabc", "question": "Will Bitcoin rise?", "description": "rules",
            "market_slug": "bitcoin-rise", "end_date_iso": "2030-01-01T00:00:00Z",
            "active": True, "closed": False, "archived": False, "accepting_orders": True,
            "enable_order_book": True, "taker_base_fee": 1000,
            "tokens": [{"token_id": "yes", "outcome": "Yes", "price": 0.7}, {"token_id": "no", "outcome": "No", "price": 0.3}],
        }

        def fetch(url):
            calls.append(url)
            if len(calls) == 1:
                return {"data": [row], "next_cursor": "cursor-1"}
            return {"data": [], "next_cursor": "LTE="}

        rows, requests = module.fetch_sampling_markets(fetch=fetch)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["conditionId"], "0xabc")
        self.assertEqual(module.token_map(rows[0]), {"Yes": "yes", "No": "no"})
        self.assertTrue(requests[-1]["terminal_cursor"])
        self.assertIn("next_cursor=cursor-1", calls[1])

    def test_sampling_identical_duplicate_condition_is_observed_and_deduplicated(self):
        row = {"condition_id": "x", "active": True, "closed": False, "accepting_orders": True, "enable_order_book": True, "tokens": []}
        rows, requests = module.fetch_sampling_markets(fetch=lambda _: {"data": [row, row], "next_cursor": "LTE="})
        self.assertEqual(len(rows), 1)
        self.assertTrue(any(item.get("observation") == "duplicate_sampling_rows_deduplicated" for item in requests))
        self.assertFalse(any(item.get("status") == "failed" for item in requests))

    def test_sampling_conflicting_duplicate_condition_degrades(self):
        first = {"condition_id": "x", "question": "A?", "active": True, "closed": False, "accepting_orders": True, "enable_order_book": True, "tokens": []}
        second = {**first, "question": "B?"}
        rows, requests = module.fetch_sampling_markets(fetch=lambda _: {"data": [first, second], "next_cursor": "LTE="})
        self.assertEqual(len(rows), 1)
        self.assertTrue(any(item.get("error") == "conflicting_duplicate_sampling_condition" for item in requests))

    def test_sampling_missing_condition_id_degrades(self):
        row = {"active": True, "closed": False, "accepting_orders": True, "enable_order_book": True, "tokens": []}
        rows, requests = module.fetch_sampling_markets(fetch=lambda _: {"data": [row], "next_cursor": "LTE="})
        self.assertEqual(rows, [])
        self.assertTrue(any(item.get("error") == "missing_sampling_condition_id" for item in requests))


if __name__ == "__main__":
    unittest.main()
