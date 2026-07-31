import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/polymarket_binary_pair_arbitrage.py"
spec = importlib.util.spec_from_file_location("polymarket_binary_pair_arbitrage", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class BinaryPairArbitrageTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(module.self_test()["status"], "pass")

    def test_depth_limits_equal_shares(self):
        yes = {"asks": [{"price": ".4", "size": "2"}]}; no = {"asks": [{"price": ".5", "size": "5"}]}
        execution = module.paired_execution(yes, no, 75, .04)
        self.assertIsNotNone(execution)
        self.assertLessEqual(execution["equal_shares"], 2.0 + 1e-8)

    def test_nonbinary_rejected(self):
        market = {"outcomes": '["A","B","C"]', "clobTokenIds": '["a","b","c"]'}
        self.assertIsNone(module.binary_tokens(market))

    def test_hypothetical_mint_sell_is_depth_limited_and_never_eligible(self):
        yes = {"bids": [{"price": ".56", "size": "2"}]}
        no = {"bids": [{"price": ".55", "size": "5"}]}
        execution = module.hypothetical_mint_and_sell_execution(yes, no, 75, .04)
        self.assertIsNotNone(execution)
        self.assertLessEqual(execution["equal_shares"], 2.0)
        self.assertGreater(execution["sale_fee_cost"], 0)
        self.assertGreater(execution["extra_slippage_cost"], 0)
        self.assertTrue(execution["general_semantic_contract_validated"])
        self.assertFalse(execution["per_market_prepared_condition_verified"])
        self.assertFalse(execution["paper_entry_eligible"])

    def test_hypothetical_mint_sell_rejects_non_edge_economics(self):
        yes = {"bids": [{"price": ".49", "size": "10"}]}
        no = {"bids": [{"price": ".49", "size": "10"}]}
        execution = module.hypothetical_mint_and_sell_execution(yes, no, 10, 0)
        self.assertIsNotNone(execution)
        self.assertLess(execution["net_edge_per_pair"], 0)

    def test_observation_is_append_only_and_deduplicated(self):
        ledger = module.new_ledger()
        payload = {
            "scan_completed_at": "2026-07-12T00:00:00+00:00", "source_snapshot_manifest_sha256": "a" * 64,
            "binary_markets_scanned": 10, "complete_top_of_book_pairs": 9, "top_verified_pairs": [{"verified_paired_best_ask_sum": 1.01}],
            "research_candidate_count": 0, "main_gate_candidate_count": 0, "research_candidates": [], "decision": "cash",
        }
        first = module.record_observation(ledger, payload); second = module.record_observation(ledger, payload)
        self.assertEqual(first, second)
        self.assertEqual(len(ledger["scan_observations"]), 1)

    def test_hypothetical_candidate_is_recorded_with_hard_blocks(self):
        ledger = module.new_ledger()
        payload = {
            "scan_completed_at": "2026-07-12T00:01:00+00:00", "source_snapshot_manifest_sha256": "b" * 64,
            "binary_markets_scanned": 10, "complete_top_of_book_pairs": 9,
            "top_verified_pairs": [], "top_verified_bid_pairs": [],
            "research_candidate_count": 0, "main_gate_candidate_count": 0, "research_candidates": [],
            "hypothetical_mint_sell_candidate_count": 1,
            "hypothetical_mint_sell_candidates": [{"condition_id": "c"}], "decision": "blocked",
        }
        module.record_observation(ledger, payload)
        candidate = ledger["candidate_observations"][0]
        self.assertEqual(candidate["structural_path"], "hypothetical_mint_and_sell")
        self.assertTrue(candidate["general_semantic_contract_validated"])
        self.assertFalse(candidate["per_market_execution_validated"])
        self.assertFalse(candidate["paper_entry_eligible"])

    def test_ctf_semantic_contract_rejects_paper_enablement(self):
        contract = module.load_ctf_semantic_contract()
        contract["paper_entry_eligible"] = True
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaises(ValueError):
                module.load_ctf_semantic_contract(path)

    def test_ctf_semantic_contract_rejects_rpc_or_selector_drift(self):
        for field, value in (("method_selector", "0x00000000"),
                             ("public_rpc_sources", ["https://example.com"])):
            contract = module.load_ctf_semantic_contract()
            contract["read_only_condition_proof"][field] = value
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "contract.json"
                path.write_text(json.dumps(contract), encoding="utf-8")
                with self.assertRaises(ValueError):
                    module.load_ctf_semantic_contract(path)

    def test_two_source_condition_proof_verifies_binary_prepared_market(self):
        condition = "0x" + "a" * 64
        def rpc_call(endpoint, method, params, allowed):
            self.assertIn(endpoint, allowed)
            return "0x89" if method == "eth_chainId" else "0x2"
        proof = module.verify_prepared_binary_condition(condition, rpc_call=rpc_call)
        self.assertEqual(proof["status"], "verified")
        self.assertEqual(proof["consistent_source_count"], 2)
        self.assertTrue(proof["prepared_binary_condition"])
        self.assertFalse(proof["wallet_used"])

    def test_condition_proof_requires_two_consistent_sources(self):
        condition = "0x" + "b" * 64
        first = module.load_ctf_semantic_contract()["read_only_condition_proof"]["public_rpc_sources"][0]
        def rpc_call(endpoint, method, params, allowed):
            if endpoint != first:
                raise RuntimeError("unavailable")
            return "0x89" if method == "eth_chainId" else "0x2"
        proof = module.verify_prepared_binary_condition(condition, rpc_call=rpc_call)
        self.assertEqual(proof["status"], "insufficient_consistent_sources")
        self.assertFalse(proof["prepared_binary_condition"])

    def test_invalid_condition_id_never_calls_rpc(self):
        proof = module.verify_prepared_binary_condition("not-a-condition", rpc_call=lambda *args: self.fail("RPC called"))
        self.assertEqual(proof["status"], "invalid_condition_id")


if __name__ == "__main__":
    unittest.main()
