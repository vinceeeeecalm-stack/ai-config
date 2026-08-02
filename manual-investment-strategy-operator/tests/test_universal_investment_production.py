import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "universal_investment_core.py"
SPEC = importlib.util.spec_from_file_location("universal_core_production", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def binding():
    return {
        "snapshot_id": "snapshot-production-v12",
        "strategy_version": "impulse-capture-tactical-v6",
        "config_digest": "a" * 64,
        "source_digest": "b" * 64,
    }


def history_row(symbol="GOODUSDT", rank=1, ev=6.0, expected=4.0):
    return {
        "rank": rank,
        "symbol": symbol,
        "setup_quality_score": 70.0 - rank,
        "sample_size": 40,
        "out_of_sample_win_rate_interval_pct": [45.0, 70.0],
        "conservative_expected_value_pct": ev,
        "expected_return_pct": expected,
        "profit_factor": 1.5,
        "max_drawdown_pct": -10.0,
        "reward_risk_ratio": 2.4,
        "walk_forward_positive_windows": 4,
        "walk_forward_total_windows": 5,
        "evidence_ids": [f"history:{symbol}"],
        "forecast_probability_emitted": False,
    }


def signal(symbol="GOODUSDT", price=100.0):
    return {
        "symbol": symbol,
        "current_price": price,
        "mid": price,
        "stage": "trigger",
        "data_quality": "verified",
        "spread_bps": 2.0,
        "depth_1pct_bid_usd": 100000.0,
        "depth_1pct_ask_usd": 100000.0,
        "historical_comparison": {
            "sample_size": 40,
            "conservative_expected_value_pct": 6.0,
            "profit_factor": 1.5,
            "max_drawdown_pct": -10.0,
            "reward_risk_ratio": 2.4,
            "walk_forward_positive_windows": 4,
            "walk_forward_total_windows": 5,
            "untouched_holdout": True,
            "lookahead_free": True,
            "friction_pct": 0.2,
            "evidence_ids": [f"history:{symbol}"],
        },
    }


def scanner():
    bind = binding()
    return {
        "run_type": "impulse_capture_scan",
        "request_mode": "tactical_1_7d",
        **bind,
        "captured_at": "2030-01-01T00:00:20Z",
        "live_orders_enabled": False,
        "private_api_used": False,
        "signals": [signal(), signal("BADUSDT", 50.0)],
        "discovery_top3": [
            {"symbol": "GOODUSDT", "current_price": 100.0},
            {"symbol": "BADUSDT", "current_price": 50.0},
        ],
        "ranked_historical_comparison": {
            "schema_version": "ranked-historical-comparison-v1",
            "primary_symbol": "GOODUSDT",
            "rows": [
                history_row(),
                history_row("BADUSDT", rank=2, ev=-2.0, expected=-1.0),
            ],
        },
    }


def complete_research(symbol="GOODUSDT", price=100.0, *, source_failures=None):
    plan = {
        "entry_low": price * 0.99,
        "entry_high": price * 1.01,
        "entry_trigger": "fresh closed 5m confirmation",
        "valid_until": "2030-01-01T00:01:00Z",
        "target_1": price * 1.12,
        "target_2": price * 1.2,
        "stop_price": price * 0.95,
        "latest_exit_at": "2030-01-07T00:00:00Z",
        "max_allowed_loss_pct": 0.5,
    }
    current_plan = {
        **copy.deepcopy(plan),
        **binding(),
        "symbol": symbol,
        "generated_at": "2030-01-01T00:00:35Z",
    }
    live = {
        **binding(),
        "symbol": symbol,
        "certified_at": "2030-01-01T00:00:35Z",
        "scan_duration_seconds": 20.0,
        "quotes": [
            {
                "source": "public-a",
                "public": True,
                "price": price,
                "as_of": "2030-01-01T00:00:30Z",
                "latency_seconds": 2.0,
            },
            {
                "source": "public-b",
                "public": True,
                "price": price * 1.002,
                "as_of": "2030-01-01T00:00:32Z",
                "latency_seconds": 3.0,
            },
        ],
        "current_plan": current_plan,
        "current_plan_digest": M.digest(current_plan),
    }
    return {
        "binding": binding(),
        "signal_as_of": "2030-01-01T00:00:30Z",
        "current_signal_complete": True,
        "fair_value": {
            "low": price * 1.15,
            "base": price * 1.3,
            "high": price * 1.45,
            "method": "verified multi-factor fixture",
            "uncertainty": "high",
        },
        "catalyst": {
            "summary": "verified fixture catalyst",
            "verified": True,
            "realization_by": "2030-01-05T00:00:00Z",
            "time_certainty": 0.8,
        },
        "downside": {
            "expected_drawdown_pct": 5.0,
            "invalidation_conditions": ["catalyst invalidated"],
        },
        "fundamentals": {
            "adoption": "measured",
            "real_fees": "positive",
            "value_capture": "verified",
            "supply_dilution": "bounded",
            "staking_net_yield": "measured",
            "liquidity": "verified",
            "security": "reviewed",
            "regulation": "scenario_reviewed",
        },
        "plan": plan,
        "liquidity_status": "verified",
        "data_quality_status": "verified",
        "risk_gate_pass": True,
        "source_failures": list(source_failures or []),
        "evidence_ids": [f"official:{symbol}", f"live:{symbol}"],
        "live_evidence": live,
    }


def production_input(research=None, cash=0.0):
    return {
        "schema_version": "UniversalInvestmentRunInputV1",
        "decided_at": "2030-01-01T00:00:40Z",
        "scanner_result": scanner(),
        "research_by_symbol": research if research is not None else {"GOODUSDT": complete_research()},
        "account": {
            "authority": "user_confirmed",
            "deployable_cash": cash,
            "settled": True,
            "max_manual_amount": cash,
        },
        "delivery_status": "RUNTIME_VERIFIED",
        "live_profit_status": "UNMEASURED",
        "long_path_status": "DATA_DEGRADED",
        "regression_status": "COLLECTING_SAMPLES",
    }


class UniversalInvestmentProductionTest(unittest.TestCase):
    def test_complete_real_input_enters_market_judgment_but_zero_cash_stays_zero(self):
        result = M.run_production_input(production_input())
        self.assertEqual(result["research_ranking"]["research_top1"], "GOODUSDT")
        self.assertEqual(result["decision"]["current_action"], "ENTER_NOW")
        self.assertEqual(result["decision"]["account_execution"], "NO_DEPLOY_CASH")
        self.assertEqual(result["decision"]["executable_amount"], 0)
        self.assertFalse(result["decision"]["paper_action_exposed"])
        self.assertEqual(result["status"]["live_profit_status"], "UNMEASURED")
        self.assertEqual(result["status"]["long_path_status"], "DATA_DEGRADED")

    def test_incomplete_real_research_keeps_unique_top1_but_no_fake_card(self):
        result = M.run_production_input(production_input(research={}))
        self.assertEqual(result["research_ranking"]["research_top1"], "GOODUSDT")
        self.assertEqual(result["decision"]["current_action"], "NO_TRADE")
        self.assertIsNone(result["decision"]["decision_card"])
        self.assertIn("fair_value_missing", result["decision"]["no_trade_reasons"])
        self.assertIn(
            "verified_1_7d_catalyst_missing",
            result["decision"]["no_trade_reasons"],
        )

    def test_current_signal_not_complete_produces_specific_wait_card(self):
        research = complete_research()
        research["current_signal_complete"] = False
        result = M.run_production_input(production_input({"GOODUSDT": research}))
        self.assertEqual(result["decision"]["current_action"], "WAIT_FOR_ENTRY")
        self.assertIsInstance(result["decision"]["decision_card"], dict)
        self.assertIn("entry_trigger", result["decision"]["decision_card"])

    def test_stale_live_quote_is_no_trade_even_with_positive_research(self):
        research = complete_research()
        research["live_evidence"]["quotes"][0]["as_of"] = "2029-12-31T23:00:00Z"
        result = M.run_production_input(production_input({"GOODUSDT": research}, cash=1000))
        self.assertEqual(result["decision"]["current_action"], "NO_TRADE")
        self.assertIsNone(result["decision"]["decision_card"])
        self.assertIn("stale_live_price", result["decision"]["no_trade_reasons"])

    def test_low_target_stop_reward_risk_cannot_be_scored_around(self):
        research = complete_research()
        research["plan"]["target_1"] = 104.0
        research["live_evidence"]["current_plan"]["target_1"] = 104.0
        research["live_evidence"]["current_plan_digest"] = M.digest(
            research["live_evidence"]["current_plan"]
        )
        result = M.run_production_input(production_input({"GOODUSDT": research}))
        self.assertEqual(result["decision"]["current_action"], "NO_TRADE")
        self.assertIn(
            "current_plan_reward_risk_below_two",
            result["decision"]["no_trade_reasons"],
        )

    def test_candidate_local_source_failure_cannot_beat_clean_candidate(self):
        payload = production_input()
        payload["scanner_result"]["ranked_historical_comparison"]["rows"][1] = history_row(
            "BADUSDT", rank=2, ev=20.0, expected=15.0
        )
        payload["scanner_result"]["signals"][1]["historical_comparison"].update(
            {
                "conservative_expected_value_pct": 20.0,
                "profit_factor": 2.0,
                "max_drawdown_pct": -8.0,
                "reward_risk_ratio": 2.4,
                "walk_forward_positive_windows": 5,
            }
        )
        payload["research_by_symbol"]["BADUSDT"] = complete_research(
            "BADUSDT", 50.0, source_failures=["official catalyst source failed"]
        )
        result = M.run_production_input(payload)
        self.assertEqual(result["research_ranking"]["research_top1"], "GOODUSDT")
        failed = next(
            item
            for item in result["research_ranking"]["ranked_candidates"]
            if item["symbol"] == "BADUSDT"
        )
        self.assertIn("candidate_source_failure", failed["hard_gate_failures"])

    def test_binding_mismatch_is_a_no_trade_reason_not_silent_rebinding(self):
        research = complete_research()
        research["binding"]["source_digest"] = "c" * 64
        result = M.run_production_input(production_input({"GOODUSDT": research}))
        self.assertEqual(result["decision"]["current_action"], "NO_TRADE")
        self.assertIn(
            "research_binding_mismatch:source_digest",
            result["decision"]["no_trade_reasons"],
        )

    def test_same_production_input_is_deterministic(self):
        payload = production_input()
        self.assertEqual(M.run_production_input(payload), M.run_production_input(copy.deepcopy(payload)))

    def test_cli_reads_real_input_and_writes_auditable_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "input.json"
            output_path = Path(temporary) / "output.json"
            input_path.write_text(json.dumps(production_input()), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(output["schema_version"], "UniversalInvestmentRunResultV1")
            self.assertEqual(output["decision"]["current_action"], "ENTER_NOW")
            self.assertFalse(output["live_orders_enabled"])


if __name__ == "__main__":
    unittest.main()
