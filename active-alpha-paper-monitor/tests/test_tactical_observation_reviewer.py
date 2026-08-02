import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "tactical_evidence_ledger.py"
SPEC = importlib.util.spec_from_file_location("tactical_evidence_outcomes", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def observation(identifier="obs-1", symbol="EULUSDT", **changes):
    record = {
        "schema_version": "ObservationSampleV1",
        "observation_id": identifier,
        "candidate_id": symbol,
        "setup_id": f"setup-{identifier}",
        "symbol": symbol,
        "asset_class": "crypto",
        "request_mode": "tactical_1_7d",
        "strategy_family": "impulse_capture",
        "strategy_version": "v13",
        "snapshot_id": f"snapshot-{identifier}",
        "config_digest": DIGEST_A,
        "source_digest": DIGEST_B,
        "observed_at": "2030-01-01T00:00:00Z",
        "price": 100.0,
        "price_as_of": "2030-01-01T00:00:00Z",
        "rank": 1,
        "accepted": False,
        "rejection_reasons": ["negative_conservative_ev"],
        "entry_trigger_state": "not_triggered",
        "next_check_at": "2030-01-01T01:00:00Z",
        "review_due_at": "2030-01-08T00:00:00Z",
        "evidence_ids": [f"evidence-{identifier}"],
        "historical_summary": {"conservative_expected_value_pct": -1.0},
        "diagnostic_thresholds": {
            "target_return_pct": 10.0,
            "stop_loss_pct": 5.0,
            "horizon_hours": 120,
            "source": "frozen_scanner_historical_contract",
            "same_bar_stop_first": True,
        },
        "formal_action_eligible": False,
        "paper_entry_eligible": False,
        "paper_live_separated": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }
    record.update(changes)
    return record


def bar(open_hour, *, high, low, close):
    open_ms = int((M._parse_time("2030-01-01T00:00:00Z").timestamp() + open_hour * 3600) * 1000)
    return {
        "open_time": open_ms,
        "close_time": open_ms + 15 * 60 * 1000 - 1,
        "high": high,
        "low": low,
        "close": close,
    }


class TacticalObservationReviewerTest(unittest.TestCase):
    def test_scanner_observation_freezes_strategy_thresholds_and_due_time(self):
        signal = {
            "symbol": "EULUSDT",
            "current_price": 100.0,
            "impulse_id": "fixture-impulse",
            "stage": "no_current_impulse",
            "breakout_close": False,
            "official_event_status": "not_checked_by_market_scanner",
            "historical_comparison": {
                "target_return_pct": 12.0,
                "stop_loss_pct": 4.0,
                "horizon_bars": 3,
                "conservative_expected_value_pct": -1.0,
                "evidence_ids": ["history-fixture"],
            },
        }
        item = M.build_scanner_observation(
            signal,
            rank=1,
            snapshot_id="snapshot-fixture",
            strategy_version="v13",
            config_digest=DIGEST_A,
            source_digest=DIGEST_B,
            observed_at="2030-01-01T00:00:00Z",
            committee_degraded=True,
        )
        self.assertEqual(item["diagnostic_thresholds"]["target_return_pct"], 12.0)
        self.assertEqual(item["diagnostic_thresholds"]["stop_loss_pct"], 4.0)
        self.assertEqual(item["diagnostic_thresholds"]["horizon_hours"], 72)
        self.assertEqual(item["review_due_at"], "2030-01-04T00:00:00Z")

    def test_rejected_target_first_is_missed_upside_not_profit(self):
        item = observation()
        review = M.build_observation_outcome_review(
            item,
            [bar(1, high=111, low=99, close=110)],
            as_of="2030-01-08T00:00:01Z",
        )
        self.assertEqual(review["first_trigger"], "TARGET")
        self.assertEqual(review["counterfactual_outcome"], "MISSED_UPSIDE")
        self.assertFalse(review["real_money_roi_eligible"])
        self.assertFalse(review["formal_action_eligible"])

    def test_rejected_stop_first_is_protected_downside(self):
        review = M.build_observation_outcome_review(
            observation(),
            [bar(1, high=102, low=94, close=96)],
            as_of="2030-01-08T01:00:00Z",
        )
        self.assertEqual(review["first_trigger"], "STOP")
        self.assertEqual(review["counterfactual_outcome"], "PROTECTED_DOWNSIDE")

    def test_same_bar_target_and_stop_is_conservative_stop_first(self):
        review = M.build_observation_outcome_review(
            observation(),
            [bar(1, high=111, low=94, close=100)],
            as_of="2030-01-08T01:00:00Z",
        )
        self.assertEqual(review["first_trigger"], "AMBIGUOUS_STOP_FIRST")
        self.assertTrue(review["path_ambiguous"])
        self.assertEqual(review["counterfactual_outcome"], "PROTECTED_DOWNSIDE")

    def test_legacy_observation_gets_path_only_without_invented_thresholds(self):
        item = observation()
        item.pop("diagnostic_thresholds")
        review = M.build_observation_outcome_review(
            item,
            [bar(1, high=120, low=90, close=105)],
            as_of="2030-01-08T01:00:00Z",
        )
        self.assertEqual(review["window_status"], "PATH_ONLY")
        self.assertIsNone(review["target_return_pct"])
        self.assertIsNone(review["stop_loss_pct"])
        self.assertEqual(review["counterfactual_outcome"], "UNCLASSIFIED_LEGACY")

    def test_future_or_pre_observation_bars_cannot_create_hindsight_result(self):
        future = bar(24 * 8, high=120, low=100, close=115)
        review = M.build_observation_outcome_review(
            observation(),
            [future],
            as_of="2030-01-09T00:00:00Z",
        )
        self.assertEqual(review["window_status"], "DATA_BLOCKED")
        self.assertEqual(review["closed_bar_count"], 0)

    def test_not_due_produces_no_final_review_and_never_calls_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            observations = Path(tmp) / "observations.jsonl"
            outcomes = Path(tmp) / "outcomes.jsonl"
            M.append_observation(observations, observation())

            def forbidden_provider(_item, _as_of):
                raise AssertionError("provider must not run before review_due_at")

            result = M.review_due_observations(
                observation_ledger=observations,
                outcome_ledger=outcomes,
                as_of="2030-01-07T23:59:59Z",
                bar_provider=forbidden_provider,
            )
            self.assertEqual(result["due_unreviewed_count"], 0)
            self.assertEqual(result["appended_count"], 0)
            self.assertFalse(outcomes.exists())

    def test_source_failure_is_retryable_and_recovery_appends_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            observations = Path(tmp) / "observations.jsonl"
            outcomes = Path(tmp) / "outcomes.jsonl"
            M.append_observation(observations, observation())

            def failed_provider(_item, _as_of):
                raise TimeoutError("fixture")

            failed = M.review_due_observations(
                observation_ledger=observations,
                outcome_ledger=outcomes,
                as_of="2030-01-08T01:00:00Z",
                bar_provider=failed_provider,
            )
            self.assertEqual(failed["data_blocked_count"], 1)
            self.assertFalse(outcomes.exists())

            recovered = M.review_due_observations(
                observation_ledger=observations,
                outcome_ledger=outcomes,
                as_of="2030-01-08T01:00:00Z",
                bar_provider=lambda _item, _as_of: [bar(1, high=111, low=99, close=110)],
            )
            self.assertEqual(recovered["appended_count"], 1)
            again = M.review_due_observations(
                observation_ledger=observations,
                outcome_ledger=outcomes,
                as_of="2030-01-08T02:00:00Z",
                bar_provider=lambda _item, _as_of: (_ for _ in ()).throw(AssertionError()),
            )
            self.assertEqual(again["already_reviewed_due_count"], 1)
            self.assertEqual(len(outcomes.read_text().splitlines()), 1)

    def test_one_symbol_failure_does_not_block_other_due_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            observations = Path(tmp) / "observations.jsonl"
            outcomes = Path(tmp) / "outcomes.jsonl"
            M.append_observation(observations, observation("obs-a", "AAAUSDT"))
            M.append_observation(observations, observation("obs-b", "BBBUSDT"))

            def provider(item, _as_of):
                if item["symbol"] == "AAAUSDT":
                    raise TimeoutError("fixture")
                return [bar(1, high=111, low=99, close=110)]

            result = M.review_due_observations(
                observation_ledger=observations,
                outcome_ledger=outcomes,
                as_of="2030-01-08T01:00:00Z",
                bar_provider=provider,
                max_workers=2,
            )
            self.assertEqual(result["data_blocked_count"], 1)
            self.assertEqual(result["appended_count"], 1)
            self.assertEqual(len(outcomes.read_text().splitlines()), 1)

    def test_summary_keeps_outcome_reviews_out_of_paper_and_live_denominators(self):
        with tempfile.TemporaryDirectory() as tmp:
            observations = Path(tmp) / "observations.jsonl"
            outcomes = Path(tmp) / "outcomes.jsonl"
            trades = Path(tmp) / "trades.jsonl"
            item = observation()
            M.append_observation(observations, item)
            review = M.build_observation_outcome_review(
                item,
                [bar(1, high=111, low=99, close=110)],
                as_of="2030-01-08T01:00:00Z",
            )
            M.append_outcome_review(outcomes, review, observation=item)
            summary = M.summarize(observations, trades, outcomes)
            self.assertEqual(summary["observation_outcome_review_count"], 1)
            self.assertEqual(summary["paper_trade_sample_count"], 0)
            self.assertEqual(summary["real_money_roi_sample_count"], 0)
            self.assertFalse(summary["observation_review_enters_paper_or_live_roi"])

    def test_binding_mismatch_is_rejected(self):
        item = observation()
        review = M.build_observation_outcome_review(
            item,
            [bar(1, high=111, low=99, close=110)],
            as_of="2030-01-08T01:00:00Z",
        )
        changed = copy.deepcopy(review)
        changed["source_digest"] = "c" * 64
        with self.assertRaises(M.EvidenceContractError):
            M.validate_outcome_review(changed, item)

    def test_orphan_outcome_is_rejected_by_summary_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            observations = Path(tmp) / "observations.jsonl"
            outcomes = Path(tmp) / "outcomes.jsonl"
            trades = Path(tmp) / "trades.jsonl"
            item = observation()
            review = M.build_observation_outcome_review(
                item,
                [bar(1, high=111, low=99, close=110)],
                as_of="2030-01-08T01:00:00Z",
            )
            outcomes.write_text(json.dumps(review) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(
                M.EvidenceContractError,
                "outcome review requires an existing observation",
            ):
                M.summarize(observations, trades, outcomes)
            with self.assertRaisesRegex(
                M.EvidenceContractError,
                "outcome review requires an existing observation",
            ):
                M.review_due_observations(
                    observation_ledger=observations,
                    outcome_ledger=outcomes,
                    as_of="2030-01-08T01:00:00Z",
                    bar_provider=lambda *_: [],
                )


if __name__ == "__main__":
    unittest.main()
