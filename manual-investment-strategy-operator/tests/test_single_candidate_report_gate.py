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
        "mode": "single_best_candidate",
        "candidate": {
            "symbol": "TEST",
            "asset_class": "us_equity",
            "plain_language_description": "A test company.",
            "verdict": "wait_for_entry",
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
            },
            "invalidation": "Official catalyst is cancelled.",
            "funding": {
                "deployable_cash": 0,
                "cash_source": "new settled USD only",
                "position_size": 0,
                "settlement_constraint": "No purchase before cash settles.",
            },
        },
        "runners_up": [{"symbol": "ALT", "rejection_reason": "Worse reward/risk"}],
    }


class SingleCandidateGateTest(unittest.TestCase):
    def test_valid_waiting_card_passes(self):
        self.assertEqual(MODULE.validate(valid_payload())["status"], "pass")

    def test_multiple_candidates_fail(self):
        payload = valid_payload()
        payload["candidates"] = [payload["candidate"]]
        self.assertIn("invalid:multiple_candidates_not_allowed", MODULE.validate(payload)["errors"])

    def test_uncalibrated_probability_cannot_allow_entry(self):
        payload = valid_payload()
        payload["candidate"]["verdict"] = "small_entry_allowed"
        payload["candidate"]["funding"]["deployable_cash"] = 100
        payload["candidate"]["probability_provenance"]["probability_type"] = "judgment_only"
        self.assertIn("invalid:uncalibrated_probability_cannot_allow_entry", MODULE.validate(payload)["errors"])

    def test_zero_cash_cannot_allow_entry(self):
        payload = valid_payload()
        payload["candidate"]["verdict"] = "small_entry_allowed"
        self.assertIn("invalid:entry_allowed_without_deployable_cash", MODULE.validate(payload)["errors"])

    def test_scenario_probabilities_must_sum_to_100(self):
        payload = copy.deepcopy(valid_payload())
        payload["candidate"]["scenarios"][0]["probability_pct"] = 20
        errors = MODULE.validate(payload)["errors"]
        self.assertTrue(any(error.startswith("invalid:scenario_probability_sum") for error in errors))


if __name__ == "__main__":
    unittest.main()
