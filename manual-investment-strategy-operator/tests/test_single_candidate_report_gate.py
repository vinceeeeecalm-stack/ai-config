import copy
import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "single_candidate_report_gate.py"
SPEC = importlib.util.spec_from_file_location("single_candidate_report_gate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def valid_payload():
    return {
        "mode": "ranked_best_candidate",
        "ranking_context": {
            "evidence_snapshot_id": "snapshot-ranked-v1",
            "strategy_version": "ranked-tactical-v5",
            "generated_at": "2026-07-23T09:00:00+08:00",
            "ranking_method": "conservative_ev_then_return_with_drawdown_and_stability_penalty",
            "short_term_business_goal": "tactical_sleeve_monthly_roi_100pct_attack_goal",
            "historical_validation_first": True,
            "deterministic_input_hash": "fixture-ranked-v1",
            "primary_rank_reason": "Highest conservative EV with the best liquidity-adjusted return path.",
        },
        "candidate": {
            "symbol": "TEST",
            "asset_class": "us_equity",
            "plain_language_description": "A test company.",
            "verdict": "wait_for_entry",
            "best_candidate": "TEST",
            "research_decision": "preferred",
            "current_direct_decision": "do_not_enter_now",
            "account_state": {"deployable_cash": 0, "cash_source": "new settled USD only"},
            "execution_decision": "no_deploy_cash",
            "executable_amount": 0,
            "reward_risk_ratio": 2.0,
            "realtime_signal_complete": True,
            "current_price": 10.0,
            "price_as_of": "2026-07-23T09:00:00+08:00",
            "price_source": "test_feed",
            "why_now": "Verified catalyst and a defined pullback.",
            "evidence_chain": [
                {"type": "FACT", "statement": "Catalyst is dated.", "basis": "official IR", "as_of": "2026-07-23"},
                {"type": "DERIVED", "statement": "Reward/risk is 2.0.", "basis": "(12-10)/(10-9)", "as_of": "2026-07-23"},
                {"type": "JUDGMENT", "statement": "Pullback entry is preferable.", "basis": "arbiter inference", "as_of": "2026-07-23"},
            ],
            "entry_plan": {
                "window_start": "2026-07-23T21:30:00+08:00",
                "window_end": "2026-07-25T04:00:00+08:00",
                "allowed_session": "us_regular_session_only",
                "primary_zone": [9.8, 10.0],
                "trigger": "Reclaim 10 after touching the zone.",
                "deadline": "2026-07-25",
                "do_not_buy_if": "No reclaim or price above 10.5.",
            },
            "exit_plan": {
                "target_1": {"price": 11.0, "window": "1-3 trading days"},
                "target_2": {"price": 12.0, "window": "3-5 trading days"},
                "price_stop": 9.0,
                "time_stop": "Exit if target 1 is not tested in three sessions.",
                "latest_exit_or_review": "2026-07-31T04:00:00+08:00",
            },
            "holding_period": "1-5 trading days",
            "scenarios": [
                {"case": "bull", "probability_pct": 30, "price_path": "12", "window": "5d", "drivers": "catalyst"},
                {"case": "base", "probability_pct": 45, "price_path": "10-11", "window": "5d", "drivers": "consolidation"},
                {"case": "bear", "probability_pct": 25, "price_path": "9", "window": "5d", "drivers": "failed catalyst"},
            ],
            "probability_provenance": {
                "probability_type": "historical_path",
                "method": "Target before stop over independent samples.",
                "sample_size": 40,
                "base_rate": 55,
                "adjustments": ["No qualitative override"],
                "limitations": "Historical analogs are imperfect.",
                "calibration_status": "calibrated",
                "conservative_expected_value_pct": 1.2,
                "untouched_holdout": True,
                "lookahead_free": True,
            },
            "invalidation": "Official catalyst is cancelled.",
            "funding": {
                "deployable_cash": 0,
                "cash_source": "new settled USD only",
                "position_size": 0,
                "settlement_constraint": "No purchase before cash settles.",
            },
        },
        "runners_up": [
            {
                "rank": 2,
                "symbol": "ALT",
                "asset_class": "crypto",
                "sample_size": 52,
                "historical_win_rate_pct": 61.5,
                "win_rate_interval_pct": [53.0, 69.0],
                "conservative_expected_value_pct": 0.8,
                "expected_return_pct": 4.2,
                "max_drawdown_pct": -8.0,
                "profit_factor": 1.25,
                "reward_risk_ratio": 2.1,
                "liquidity_status": "verified",
                "current_direct_decision": "do_not_enter_now",
                "decision_valid_until": "2026-07-25T04:00:00+08:00",
                "evidence_snapshot_id": "snapshot-ranked-v1",
                "why_ranked_lower": [
                    "Lower conservative EV than TEST.",
                    "Current entry is farther from support."
                ]
            }
        ],
    }


class SingleCandidateGateTest(unittest.TestCase):
    def test_valid_waiting_card_passes(self):
        self.assertEqual(MODULE.validate(valid_payload())["status"], "pass")

    def test_unstructured_multiple_candidates_fail(self):
        payload = valid_payload()
        payload["candidates"] = [payload["candidate"]]
        self.assertIn("invalid:multiple_candidates_not_allowed", MODULE.validate(payload)["errors"])

    def test_ranked_alternative_requires_positive_historical_quality(self):
        payload = valid_payload()
        payload["runners_up"][0]["conservative_expected_value_pct"] = -0.1
        errors = MODULE.validate(payload)["errors"]
        self.assertIn(
            "invalid:runners_up[0].positive_conservative_ev_required",
            errors,
        )

    def test_ranked_output_is_deterministic_for_same_payload(self):
        payload = valid_payload()
        self.assertEqual(MODULE.validate(payload), MODULE.validate(copy.deepcopy(payload)))

    def test_uncalibrated_probability_cannot_allow_entry(self):
        payload = valid_payload()
        payload["candidate"]["verdict"] = "small_entry_allowed"
        payload["candidate"]["current_direct_decision"] = "small_entry_now"
        payload["candidate"]["funding"]["deployable_cash"] = 100
        payload["candidate"]["account_state"]["deployable_cash"] = 100
        payload["candidate"]["execution_decision"] = "manual_execute_candidate"
        payload["candidate"]["executable_amount"] = 100
        payload["candidate"]["probability_provenance"]["probability_type"] = "judgment_only"
        payload["candidate"]["probability_provenance"]["sample_size"] = 8
        payload["candidate"]["probability_provenance"]["calibration_status"] = "judgment_only"
        self.assertIn("invalid:judgment_only_cannot_allow_entry", MODULE.validate(payload)["errors"])

    def test_zero_cash_preserves_theoretical_entry_and_blocks_execution_only(self):
        payload = valid_payload()
        payload["candidate"]["verdict"] = "small_entry_allowed"
        payload["candidate"]["current_direct_decision"] = "small_entry_now"
        self.assertEqual(MODULE.validate(payload)["status"], "pass")

    def test_scenario_probabilities_must_sum_to_100(self):
        payload = copy.deepcopy(valid_payload())
        payload["candidate"]["scenarios"][0]["probability_pct"] = 20
        errors = MODULE.validate(payload)["errors"]
        self.assertTrue(any(error.startswith("invalid:scenario_probability_sum") for error in errors))


if __name__ == "__main__":
    unittest.main()
