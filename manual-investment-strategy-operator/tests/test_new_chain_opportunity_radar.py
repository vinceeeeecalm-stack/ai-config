import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RADAR_PATH = (
    ROOT
    / "active-alpha-paper-monitor"
    / "scripts"
    / "new_chain_opportunity_radar.py"
)
REGISTRY_PATH = (
    ROOT
    / "active-alpha-paper-monitor"
    / "config"
    / "new_chain_event_registry.json"
)
REPLAY_PATH = (
    ROOT
    / "active-alpha-paper-monitor"
    / "config"
    / "incident_replays"
    / "cashcat_202607.json"
)

SPEC = importlib.util.spec_from_file_location("new_chain_opportunity_radar", RADAR_PATH)
RADAR = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(RADAR)


class NewChainOpportunityRadarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        cls.policy = cls.registry["defaults"]

    def test_cashcat_replay_discovers_dex_only_asset_without_hindsight_probability(self):
        result = RADAR.run_replay(REPLAY_PATH, self.policy)
        self.assertTrue(result["passed"], result["failures"])
        self.assertFalse(result["counterfactual_predictability_claimed"])
        stages = [item["event_stage"] for item in result["results"]]
        self.assertEqual(stages, ["event_watch", "ecosystem_watch", "asset_watch"])
        candidate = result["results"][-1]["candidates"][0]
        self.assertEqual(candidate["symbol"], "CASHCAT")
        self.assertEqual(candidate["discovery_origin"], "new_chain_event")
        self.assertEqual(candidate["max_candidate_action"], "watch")
        self.assertEqual(candidate["captured_at"], "2026-07-09T14:17:00Z")
        self.assertIn("market_snapshot", candidate)
        self.assertGreaterEqual(len(candidate["source_urls"]), 2)
        self.assertIsNone(candidate["forecast_probability_pct"])
        self.assertIn(
            "contract_cross_verification_missing",
            candidate["security_gate_failures"],
        )

    def test_missing_security_cannot_be_promoted_by_momentum(self):
        replay = json.loads(REPLAY_PATH.read_text(encoding="utf-8"))
        snapshot = replay["snapshots"][-1]
        snapshot["assets"][0]["volume_24h_usd"] = 500000000
        result = RADAR.evaluate_snapshot(snapshot, self.policy)
        self.assertEqual(result["max_active_action"], "watch")

    def test_complete_identity_market_security_and_capacity_allows_paper_only(self):
        result = RADAR.self_test(self.policy)
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(
            result["qualified_fixture"]["max_active_action"],
            "paper_only",
        )
        self.assertFalse(result["live_orders_enabled"])


if __name__ == "__main__":
    unittest.main()
