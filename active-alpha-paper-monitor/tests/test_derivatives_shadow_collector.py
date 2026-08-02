import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


M = load_module("derivatives_shadow_collector_test", SCRIPTS / "derivatives_shadow_collector.py")
SCANNER = load_module("derivatives_shadow_scanner_test", SCRIPTS / "impulse_capture_scanner.py")
CONFIG = json.loads((ROOT / "config" / "derivatives_shadow_v2.json").read_text(encoding="utf-8"))


def handoff(symbol, rank, *, strong=True):
    return {
        "schema_version": "DerivativesShadowSpotHandoffV1",
        "symbol": symbol,
        "discovery_rank": rank,
        "captured_at": "2030-01-01T00:00:00Z",
        "current_price": 10.0,
        "return_1m_pct": 0.2 if strong else -0.2,
        "return_5m_pct": 1.0 if strong else -1.0,
        "return_15m_pct": 1.5 if strong else -1.5,
        "relative_volume_5m": 2.0 if strong else 0.8,
        "spot_taker_buy_ratio_5m": 0.62 if strong else 0.42,
        "orderbook_imbalance": 0.2 if strong else -0.2,
        "higher_lows": strong,
        "breakout_close": False,
        "spread_bps": 2.0,
        "depth_bid_usd": 100000.0,
        "depth_ask_usd": 100000.0,
        "data_quality": "verified",
        "production_signal_stage": "no_current_impulse",
        "production_impulse_score_points": 10.0,
        "formal_action_eligible": False,
        "production_ranking_input": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }


def discovery(symbols, weak_symbols=()):
    weak_symbols = set(weak_symbols)
    return {
        "schema_version": "fast-candidate-funnel-v1",
        "phase": "discovery",
        "top_candidates": [{"symbol": symbol, "discovery_score": 100 - index} for index, symbol in enumerate(symbols)],
        "derivatives_shadow_handoff": [handoff(symbol, index, strong=symbol not in weak_symbols) for index, symbol in enumerate(symbols, start=1)],
        "derivatives_shadow_handoff_enabled": True,
        "production_rule_changed": False,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def oi_history(growth=1.0):
    now_ms = 1893456000000
    return [
        {"timestamp": now_ms - (288 - index) * 300000, "sumOpenInterestValue": 1000.0 + growth * index}
        for index in range(289)
    ]


def source(*, funding="0.0001", taker_buy="60", taker_sell="40", growth=1.0):
    return {
        "premium_index": {"lastFundingRate": funding, "markPrice": "10.01", "indexPrice": "10.0"},
        "ticker_24h": {"quoteVolume": "1000000"},
        "oi_history": oi_history(growth),
        "taker_history": [{"buyVol": taker_buy, "sellVol": taker_sell}],
        "errors": [],
        "global_errors": [],
    }


class DerivativesShadowCollectorTests(unittest.TestCase):
    def test_all_candidates_are_collected_including_rank_four(self):
        symbols = ["ALPHAUSDT", "BETAUSDT", "GAMMAUSDT", "DELTAUSDT"]
        result = M.run_shadow(discovery(symbols), CONFIG, {symbol: source() for symbol in symbols})
        self.assertEqual(result["candidate_symbols"], symbols)
        self.assertEqual(result["candidate_count"], 4)
        self.assertEqual(result["records"][3]["discovery_rank"], 4)
        self.assertEqual(result["complete_coverage_rate_pct"], 100.0)
        self.assertEqual(result["production_signal_stage_counts"], {"no_current_impulse": 4})
        self.assertTrue(result["within_wall_clock_budget"])
        self.assertTrue(all(record["formal_action_eligible"] is False for record in result["records"]))
        self.assertTrue(all(record["review_due_at"] == "2030-01-08T00:00:00Z" for record in result["records"]))

    def test_oi_alone_and_hot_funding_are_conflicts(self):
        payload = discovery(["WEAKUSDT", "HOTUSDT"], weak_symbols={"WEAKUSDT"})
        sources = {
            "WEAKUSDT": source(),
            "HOTUSDT": source(funding="0.002"),
        }
        result = M.run_shadow(payload, CONFIG, sources)
        by_symbol = {item["symbol"]: item for item in result["records"]}
        self.assertEqual(by_symbol["WEAKUSDT"]["directional_state"], "OI_ONLY_CONFLICT")
        self.assertEqual(by_symbol["HOTUSDT"]["directional_state"], "CROWDED_CONFLICT")
        self.assertFalse(by_symbol["WEAKUSDT"]["oi_alone_can_authorize"])
        self.assertFalse(by_symbol["HOTUSDT"]["formal_action_eligible"])

    def test_candidate_failure_is_local_and_missing_is_not_zero(self):
        payload = discovery(["GOODUSDT", "MISSINGUSDT"])
        result = M.run_shadow(payload, CONFIG, {"GOODUSDT": source(), "MISSINGUSDT": {"errors": ["no_contract"]}})
        by_symbol = {item["symbol"]: item for item in result["records"]}
        self.assertEqual(by_symbol["GOODUSDT"]["source_status"], "COMPLETE")
        self.assertEqual(by_symbol["MISSINGUSDT"]["source_status"], "DEGRADED")
        self.assertIsNone(by_symbol["MISSINGUSDT"]["derivatives_factor"]["oi_change_1h_pct"])
        self.assertNotEqual(by_symbol["MISSINGUSDT"]["derivatives_factor"]["oi_change_1h_pct"], 0)

    def test_public_contract_multiplier_alias_is_explicit_and_not_guessed_without_evidence(self):
        available = {"BTCUSDT", "1000PEPEUSDT", "1000SHIBUSDT"}
        self.assertEqual(M.resolve_derivatives_symbol("BTCUSDT", available), "BTCUSDT")
        self.assertEqual(M.resolve_derivatives_symbol("PEPEUSDT", available), "1000PEPEUSDT")
        self.assertEqual(M.resolve_derivatives_symbol("SHIBUSDT", available), "1000SHIBUSDT")
        self.assertIsNone(M.resolve_derivatives_symbol("UNKNOWNUSDT", available))

    def test_binding_is_shared_and_production_input_is_not_mutated(self):
        payload = discovery(["AUSDT", "BUSDT"])
        before = copy.deepcopy(payload["top_candidates"])
        result = M.run_shadow(payload, CONFIG, {"AUSDT": source(), "BUSDT": source()})
        bindings = {(row["snapshot_id"], row["strategy_version"], row["config_digest"], row["source_digest"]) for row in result["records"]}
        self.assertEqual(len(bindings), 1)
        self.assertEqual(payload["top_candidates"], before)
        self.assertFalse(result["production_rule_changed"])
        self.assertFalse(result["paper_roi_eligible"])
        self.assertFalse(result["real_money_roi_eligible"])
        self.assertEqual(result["derivatives_lead_candidate_count"], 2)

    def test_append_is_idempotent_and_collision_is_blocked(self):
        result = M.run_shadow(discovery(["AUSDT"]), CONFIG, {"AUSDT": source()})
        with tempfile.TemporaryDirectory() as temp:
            ledger = Path(temp) / "derivatives.jsonl"
            self.assertEqual(M.append_observations(ledger, result), {"APPENDED": 1, "NO_UPDATE": 0})
            self.assertEqual(M.append_observations(ledger, result), {"APPENDED": 0, "NO_UPDATE": 1})
            changed = copy.deepcopy(result)
            changed["records"][0]["directional_state"] = "NO_FUEL"
            with self.assertRaisesRegex(M.DerivativesShadowError, "append_only_collision"):
                M.append_observations(ledger, changed)

    def test_twenty_candidate_bound_and_mixed_handoff_time_rejected(self):
        symbols = [f"C{index:02d}USDT" for index in range(20)]
        result = M.run_shadow(discovery(symbols), CONFIG, {symbol: source() for symbol in symbols})
        self.assertEqual(result["candidate_count"], 20)
        invalid = discovery(["AUSDT", "BUSDT"])
        invalid["derivatives_shadow_handoff"][1]["captured_at"] = "2030-01-01T00:01:00Z"
        with self.assertRaisesRegex(M.DerivativesShadowError, "mixed_handoff_capture_times"):
            M.run_shadow(invalid, CONFIG, {"AUSDT": source(), "BUSDT": source()})

    def test_scanner_handoff_is_opt_in_data_only_and_preserves_rank_order(self):
        signals = [
            {"symbol": "BUSDT", "current_price": 2.0, "taker_buy_ratio_5m": 0.6, "volume_multiple_5m_vs_median": 2.0, "data_quality": "verified"},
            {"symbol": "AUSDT", "current_price": 1.0, "taker_buy_ratio_5m": 0.4, "volume_multiple_5m_vs_median": 1.0, "data_quality": "verified"},
        ]
        captured = M.parse_time("2030-01-01T00:00:00Z", "captured")
        rows = SCANNER.build_derivatives_shadow_handoff(signals, ["AUSDT", "BUSDT"], captured)
        self.assertEqual([row["symbol"] for row in rows], ["AUSDT", "BUSDT"])
        self.assertTrue(all(row["production_ranking_input"] is False for row in rows))
        self.assertTrue(all(row["formal_action_eligible"] is False for row in rows))


if __name__ == "__main__":
    unittest.main()
