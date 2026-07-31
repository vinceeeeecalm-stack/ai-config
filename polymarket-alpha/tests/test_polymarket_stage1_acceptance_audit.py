import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_stage1_acceptance_audit.py"
spec = importlib.util.spec_from_file_location("polymarket_stage1_acceptance_audit", SCRIPT)
module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)

def fixture():
    item = {"market_id": "m", "market": "Q", "category": "insufficient_data_rules_liquidity_or_model",
        "bet_direction": "NO_BET", "market_consensus_side": "NO", "market_consensus_is_model_probability": False,
        "executable_price": .2, "model_estimated_probability": None, "confidence_interval": [None, None],
        "oos_or_forward_samples": 0, "historical_hit_rate": None, "net_edge_per_share": None,
        "maximum_acceptable_price": None, "suggested_paper_amount_usd": 0, "failure_paths": [],
        "exit_conditions": [], "final_action": "PASS", "execution_tests_by_side": {"YES": {}, "NO": {}}}
    return {"daily": {"status": "ok", "cycle_id": "c", "snapshot_manifest_sha256": "a" * 64,
        "snapshot_contract_failures": [], "selected_candidate_count": 0, "paper_opened_count": 0, "all_decisions": []},
        "manual": {"status": "ok", "source_daily_cycle_id": "c", "watch_item_count": 1, "recommendation_count": 0,
            "watch_items": [item], "decision": "cash_no_recommendation", "paper_only": True,
            "real_money_execution_authorized": False, "live_orders_enabled": False, "private_api_used": False},
        "policy": {"paper_only": True, "live_orders_enabled": False, "private_api_used": False,
            "entry_gate": {"min_model_probability": .8, "min_confidence_lower": .72, "min_net_edge_per_share": .045,
                           "min_calibration_samples": 30, "max_spread_per_share": .02, "max_price_impact_per_share": .01}},
        "scan_ledger": {"observations": [{"cycle_id": "a"}, {"cycle_id": "b"}]},
        "candidate_ledger": {"append_only": True, "counts_as_paper_trade": False, "events": [{"event_id": "e"}]},
        "paper_ledger": {"open_positions": []}, "automation": {"status": "ok", "verified_automation_count": 2, "expected_automation_count": 2},
        "validation": {"status": "ok", "cycle_id": "v", "steps": [{"name": name, "status": "ok"} for name in
            ("safety_preflight", "daily_1_24h_priority", "daily_manual_decision_report", "daily_candidate_forward_ledger", "daily_forward_probability_benchmark")]},
        "regression": {"status": "pass", "tests_passed": 1},
        "artifact_exists": {"a": True},
        "stage_scope": {"current_stage": 1, "phase2_started": False, "long_term_strategy_effectiveness_proven": False}}

class Stage1AcceptanceAuditTests(unittest.TestCase):
    def test_complete_fixture_passes_all_eleven_checks(self):
        payload = module.audit(fixture())
        self.assertTrue(payload["stage1_complete"]); self.assertEqual(payload["checks_passed"], 11)
        self.assertEqual(payload["next_action"], "stop_and_wait_for_human_phase2_confirmation")

    def test_missing_double_sided_pass_book_fails(self):
        data = fixture(); data["manual"]["watch_items"][0]["execution_tests_by_side"].pop("NO")
        payload = module.audit(data)
        self.assertFalse(payload["stage1_complete"])
        self.assertEqual(next(row for row in payload["acceptance_matrix"] if row["acceptance_id"] == "S1-04")["status"], "fail")

    def test_unsafe_real_money_flag_fails(self):
        data = fixture(); data["manual"]["real_money_execution_authorized"] = True
        payload = module.audit(data)
        self.assertFalse(payload["stage1_complete"])
        self.assertEqual(next(row for row in payload["acceptance_matrix"] if row["acceptance_id"] == "S1-09")["status"], "fail")

if __name__ == "__main__": unittest.main()
