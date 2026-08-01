import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "polymarket_market_family_audit.py"
spec = importlib.util.spec_from_file_location("polymarket_market_family_audit", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class MarketFamilyAuditTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_all_tags_are_required(self):
        rule = {"all_tags": {"Crypto", "Hit Price"}}
        market = {"_sampling_raw": {"tags": ["Crypto"]}}
        self.assertFalse(module.belongs(market, rule))

    def test_excluded_tag_blocks_family(self):
        rule = {"any_tags": {"Hit Price"}, "exclude_tags": {"Crypto"}}
        market = {"_sampling_raw": {"tags": ["Crypto", "Hit Price"]}}
        self.assertFalse(module.belongs(market, rule))

    def test_protocol_matches_selected_family(self):
        protocol = module.frozen_protocol("baseball")
        self.assertEqual(protocol["family"], "baseball")
        self.assertIn("moneyline", protocol["initial_contract_scope"])

    def test_esports_protocol_is_title_isolated(self):
        protocol = module.frozen_protocol("esports")
        self.assertEqual(protocol["family"], "esports")
        self.assertIn("separately", protocol["title_isolation"])

    def test_taxonomy_first_protocol_keeps_holdout_sealed(self):
        protocol = module.frozen_protocol("geopolitics")
        self.assertEqual(protocol["family"], "geopolitics")
        self.assertIn("sealed", protocol["chronological_split"]["final_holdout"])
        self.assertFalse(protocol["paper_entry_eligible"])

    def test_technology_protocol_is_taxonomy_first(self):
        protocol = module.frozen_protocol("technology_events")
        self.assertIn("launches", protocol["initial_contract_scope"])

    def test_condition_id_is_not_independent_event_fallback(self):
        market = {"id": "m1", "conditionId": "c1", "_sampling_raw": {}}
        self.assertIsNone(module.event_identity(market))

    def test_small_inventory_can_only_enter_history_fallback(self):
        market = {"id": "m1", "endDate": "2026-07-20T00:00:00Z", "description": "Resolution source: official.", "events": [{"id": "e1"}], "_sampling_raw": {"tags": ["Elections"], "tokens": []}}
        result = module.audit([market], {}, module.datetime(2026, 7, 12, tzinfo=module.timezone.utc), {"families": {}})
        self.assertEqual(result["selected_next_research_family"], "elections")
        self.assertEqual(result["selected_next_research_stage"], "history_feasibility_fallback")


if __name__ == "__main__":
    unittest.main()
