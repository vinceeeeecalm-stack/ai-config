import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "new_chain_opportunity_radar.py"
SPEC = importlib.util.spec_from_file_location("new_chain_event_freshness", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


POLICY = {
    "min_cross_source_contract_confirmations": 2,
    "min_liquidity_usd": 500000,
    "min_volume_1h_usd": 100000,
    "min_volume_24h_usd": 1000000,
    "min_unique_traders_1h": 100,
    "max_buy_or_sell_tax_pct": 5,
    "max_top10_non_lp_holder_pct": 35,
    "max_deployer_holder_pct": 5,
}


def event(watch_until):
    payload = {
        "event_id": "test-mainnet",
        "entity": "Test",
        "chain_name": "Test Chain",
        "status": "mainnet_live",
        "verified": True,
        "geckoterminal_network": "test",
        "dexscreener_chain_id": "test",
    }
    if watch_until is not None:
        payload["watch_until"] = watch_until
    return payload


class NewChainEventFreshnessTests(unittest.TestCase):
    def test_expired_event_skips_all_dex_sources(self):
        cutoff = datetime(2026, 8, 2, 4, 1, 43, tzinfo=timezone.utc)
        with mock.patch.object(M, "get_json") as get_json:
            result = M.scan_live_event(
                event("2026-08-01T23:59:59-04:00"),
                POLICY,
                1,
                captured_at=cutoff,
            )

        get_json.assert_not_called()
        self.assertEqual(result["event_status"], "expired")
        self.assertEqual(result["event_stage"], "watch_expired")
        self.assertFalse(result["current_catalyst_eligible"])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["source_status"], [])
        self.assertEqual(result["max_active_action"], "watch")
        self.assertFalse(result["live_orders_enabled"])

    def test_missing_watch_until_is_invalid_and_not_scanned(self):
        cutoff = datetime(2026, 8, 2, 4, 0, tzinfo=timezone.utc)
        with mock.patch.object(M, "get_json") as get_json:
            result = M.scan_live_event(
                event(None), POLICY, 1, captured_at=cutoff
            )

        get_json.assert_not_called()
        self.assertEqual(result["event_status"], "invalid_window")
        self.assertEqual(result["event_stage"], "watch_window_invalid")
        self.assertEqual(result["window_reason"], "watch_until_missing")
        self.assertFalse(result["current_catalyst_eligible"])

    def test_active_event_keeps_cross_venue_discovery(self):
        cutoff = datetime(2026, 8, 2, 4, 0, tzinfo=timezone.utc)

        def response(url, timeout):
            if "geckoterminal" in url:
                return {"data": []}
            return {"pairs": []}

        with mock.patch.object(M, "get_json", side_effect=response) as get_json:
            result = M.scan_live_event(
                event("2026-08-02T04:00:01Z"),
                POLICY,
                1,
                captured_at=cutoff,
            )

        self.assertEqual(get_json.call_count, 3)
        self.assertEqual(result["event_status"], "active")
        self.assertEqual(result["event_stage"], "ecosystem_watch")
        self.assertTrue(result["current_catalyst_eligible"])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["max_active_action"], "watch")
        self.assertFalse(result["live_orders_enabled"])


if __name__ == "__main__":
    unittest.main()
