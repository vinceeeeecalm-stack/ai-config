import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


M = load_module("derivatives_shadow_cycle_test", SCRIPTS / "derivatives_shadow_cycle.py")
CONFIG = json.loads((ROOT / "config" / "derivatives_shadow_v2.json").read_text(encoding="utf-8"))


def handoff(symbol, rank):
    return {
        "schema_version": "DerivativesShadowSpotHandoffV1",
        "symbol": symbol,
        "discovery_rank": rank,
        "captured_at": "2030-01-01T00:00:00Z",
        "current_price": 10.0 + rank,
        "return_1m_pct": 0.1,
        "return_5m_pct": 0.4,
        "return_15m_pct": 0.8,
        "relative_volume_5m": 2.0,
        "spot_taker_buy_ratio_5m": 0.61,
        "orderbook_imbalance": 0.2,
        "higher_lows": True,
        "breakout_close": True,
        "spread_bps": 2.0,
        "depth_bid_usd": 100000.0,
        "depth_ask_usd": 100000.0,
        "data_quality": "complete",
        "production_signal_stage": "early_watch",
        "production_impulse_score_points": 3.0,
        "formal_action_eligible": False,
        "production_ranking_input": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }


def discovery():
    rows = [handoff("AUSDT", 1), handoff("BUSDT", 2)]
    return {
        "schema_version": "fast-candidate-funnel-v1",
        "phase": "discovery",
        "top_candidates": [{"symbol": row["symbol"], "discovery_score": 100 - index} for index, row in enumerate(rows)],
        "derivatives_shadow_handoff": rows,
        "derivatives_shadow_handoff_enabled": True,
        "production_rule_changed": False,
        "formal_action_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }


def empty_discovery():
    return {
        "schema_version": "fast-candidate-funnel-v1",
        "phase": "discovery",
        "top_candidates": [],
        "blocked_candidates": [],
        "eligible_candidate_count": 0,
        "derivatives_shadow_handoff": [],
        "derivatives_shadow_handoff_enabled": True,
        "fresh_transport_verified": True,
        "errors": [],
        "dynamic_discovery_audit": {
            "exchange_spot_identity": {
                "authority": "Binance /api/v3/exchangeInfo",
                "batch_status": "verified",
                "batch_definition_count": 4,
                "full_snapshot_status": "not_attempted",
                "formal_identity_relaxed": False,
            }
        },
    }


def sources():
    oi_history = [
        {
            "timestamp": 1893456000000 - (288 - index) * 300000,
            "sumOpenInterestValue": 1000.0 + index,
        }
        for index in range(289)
    ]
    return {
        "AUSDT": {
            "premium_index": {"lastFundingRate": "0.0001", "markPrice": "10.01", "indexPrice": "10.0"},
            "ticker_24h": {"quoteVolume": "5000000"},
            "oi_history": oi_history,
            "taker_history": [{"buyVol": "62", "sellVol": "38"}],
            "errors": [],
            "global_errors": [],
        },
        "BUSDT": {
            "premium_index": {"lastFundingRate": "0.0001", "markPrice": "10.01", "indexPrice": "10.0"},
            "ticker_24h": {"quoteVolume": "5000000"},
            "oi_history": oi_history,
            "taker_history": [{"buyVol": "40", "sellVol": "60"}],
            "errors": ["candidate_local_source_failure"],
            "global_errors": [],
        },
    }


class DerivativesShadowCycleTests(unittest.TestCase):
    def test_append_only_repeat_is_no_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "observations.jsonl"
            first = M.run_cycle(discovery(), CONFIG, ledger=ledger, fixture_sources=sources())
            before = ledger.read_bytes()
            second = M.run_cycle(discovery(), CONFIG, ledger=ledger, fixture_sources=sources())
            self.assertEqual(first["append_result"], {"APPENDED": 2, "NO_UPDATE": 0})
            self.assertEqual(second["append_result"], {"APPENDED": 0, "NO_UPDATE": 2})
            self.assertEqual(before, ledger.read_bytes())

    def test_same_snapshot_with_refreshed_source_is_still_no_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "observations.jsonl"
            first_sources = sources()
            second_sources = copy.deepcopy(first_sources)
            second_sources["AUSDT"]["premium_index"]["lastFundingRate"] = "0.0002"
            first = M.run_cycle(discovery(), CONFIG, ledger=ledger, fixture_sources=first_sources)
            before = ledger.read_bytes()
            second = M.run_cycle(discovery(), CONFIG, ledger=ledger, fixture_sources=second_sources)
            self.assertEqual(first["append_result"], {"APPENDED": 2, "NO_UPDATE": 0})
            self.assertEqual(second["append_result"], {"APPENDED": 0, "NO_UPDATE": 2})
            self.assertNotEqual(first["source_digest"], second["source_digest"])
            self.assertEqual(first["source_lineage_digest"], second["source_lineage_digest"])
            self.assertEqual(before, ledger.read_bytes())

    def test_cycle_keeps_all_safety_axes_false(self):
        result = M.run_cycle(discovery(), CONFIG, ledger=None, fixture_sources=sources())
        self.assertEqual(result["append_result"], {"APPENDED": 0, "NO_UPDATE": 0})
        for field in (
            "production_rule_changed",
            "formal_action_eligible",
            "paper_roi_eligible",
            "real_money_roi_eligible",
            "business_ready_eligible",
            "live_orders_enabled",
            "private_api_used",
        ):
            self.assertFalse(result[field])
        self.assertTrue(result["human_confirmation_required"])
        self.assertTrue(all(row["oi_alone_can_authorize"] is False for row in result["shadow_run"]["records"]))

    def test_verified_empty_discovery_is_structured_no_update(self):
        self.assertTrue(M.verified_empty_discovery(empty_discovery()))
        result = M.structured_no_update(
            empty_discovery(),
            CONFIG,
            run_status="NO_ELIGIBLE_CANDIDATES",
            blockers=[],
        )
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["append_result"], {"APPENDED": 0, "NO_UPDATE": 0})
        self.assertEqual(result["shadow_run"]["records"], [])
        self.assertEqual(result["all_blockers"], [])

    def test_unverified_or_contradictory_empty_discovery_is_degraded(self):
        contradictory = empty_discovery()
        contradictory["eligible_candidate_count"] = 7
        self.assertFalse(M.verified_empty_discovery(contradictory))

        missing_identity = empty_discovery()
        missing_identity.pop("dynamic_discovery_audit")
        self.assertFalse(M.verified_empty_discovery(missing_identity))

        failed_identity = empty_discovery()
        failed_identity["dynamic_discovery_audit"]["exchange_spot_identity"].update(
            {
                "batch_status": "transport_failed",
                "full_snapshot_status": "transport_failed",
            }
        )
        self.assertFalse(M.verified_empty_discovery(failed_identity))

        with self.assertRaisesRegex(
            M.collector.DerivativesShadowError,
            "candidate_and_handoff_lists_required",
        ):
            M.run_cycle(contradictory, CONFIG, ledger=None)

    def test_invalid_handoff_cli_writes_structured_latest_state_without_ledger_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            malformed = discovery()
            malformed.pop("derivatives_shadow_handoff")
            discovery_path = tmp / "discovery.json"
            latest_path = tmp / "latest.json"
            ledger_path = tmp / "observations.jsonl"
            discovery_path.write_text(json.dumps(malformed), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "derivatives_shadow_cycle.py"),
                    "--discovery-input",
                    str(discovery_path),
                    "--ledger",
                    str(ledger_path),
                    "--latest-output",
                    str(latest_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["run_status"], "DATA_DEGRADED")
            self.assertIn("candidate_and_handoff_lists_required", payload["all_blockers"])
            self.assertEqual(json.loads(latest_path.read_text(encoding="utf-8")), payload)
            self.assertFalse(ledger_path.exists())

    def test_candidate_local_failure_does_not_drop_round(self):
        result = M.run_cycle(discovery(), CONFIG, ledger=None, fixture_sources=sources())
        self.assertEqual(result["candidate_count"], 2)
        self.assertIn(result["shadow_run"]["run_status"], {"SHADOW_EVIDENCE_COLLECTED", "DATA_DEGRADED"})
        self.assertEqual(len(result["shadow_run"]["records"]), 2)

    def test_scanner_command_is_bounded_shadow_only(self):
        command = M.scanner_command(dynamic_top=40, top=20, max_workers=8, timeout_seconds=8)
        self.assertIn("--discovery-only", command)
        self.assertIn("--derivatives-shadow-handoff", command)
        self.assertIn("--no-write", command)
        self.assertIn("tactical_1_7d", command)
        with self.assertRaises(M.DerivativesShadowCycleError):
            M.scanner_command(dynamic_top=40, top=21, max_workers=8, timeout_seconds=8)
        with self.assertRaisesRegex(M.DerivativesShadowCycleError, "discovery_timeout_out_of_range"):
            M.run_public_discovery(
                dynamic_top=40,
                top=20,
                max_workers=8,
                timeout_seconds=8,
                whole_round_timeout_seconds=121,
            )

    def test_unsafe_cycle_projection_is_rejected(self):
        result = M.run_cycle(discovery(), CONFIG, ledger=None, fixture_sources=sources())
        polluted = copy.deepcopy(result)
        polluted["formal_action_eligible"] = True
        with self.assertRaises(M.DerivativesShadowCycleError):
            M.validate_cycle(polluted)

    def test_self_test(self):
        self.assertEqual(M.self_test()["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
