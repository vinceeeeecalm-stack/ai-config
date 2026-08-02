from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "derivatives_shadow_outcome_reviewer",
    SCRIPTS / "derivatives_shadow_outcome_reviewer.py",
)
M = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(M)


def observation(symbol: str = "AAAUSDT", rank: int = 4) -> dict:
    observed = datetime(2030, 1, 1, tzinfo=timezone.utc)
    return {
        "schema_version": "DerivativesShadowObservationV1",
        "observation_id": f"derivatives-observation-{symbol.lower()}",
        "derivatives_snapshot_id": f"derivatives-{symbol.lower()}",
        "symbol": symbol,
        "derivatives_symbol": symbol,
        "discovery_rank": rank,
        "snapshot_id": "snapshot-test",
        "strategy_version": "unified-shortterm-derivatives-shadow-v2",
        "config_digest": "a" * 64,
        "source_digest": "b" * 64,
        "observed_at": M.iso(observed),
        "captured_at": M.iso(observed),
        "review_due_at": M.iso(observed + timedelta(days=7)),
        "evaluation_window_hours": 168,
        "production_signal_stage": "early_watch" if rank > 3 else "pre_breakout",
        "production_impulse_score_points": 42.0,
        "spot_factor": {"current_price": 100.0},
        "directional_state": "OI_ONLY_CONFLICT",
        "directional_blockers": ["spot_buying_confirmed"],
        "oi_alone_can_authorize": False,
        "source_status": "COMPLETE",
        "production_rule_changed": False,
        "formal_action_eligible": False,
        "paper_roi_eligible": False,
        "real_money_roi_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }


def bars(*, target_index: int | None = 100, low_index: int | None = 200, same_bar: bool = False) -> list[dict]:
    start = datetime(2030, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(7 * 24 * 4):
        opened = start + timedelta(minutes=15 * index)
        high = 101.0
        low = 99.0
        if target_index is not None and index == target_index:
            high = 106.0
        if low_index is not None and index == low_index:
            low = 95.5
        if same_bar and index == target_index:
            low = 96.5
        rows.append({
            "open_time": int(opened.timestamp() * 1000),
            "close_time": int((opened + timedelta(minutes=15) - timedelta(milliseconds=1)).timestamp() * 1000),
            "high": high,
            "low": low,
            "close": 101.0,
        })
    return rows


class DerivativesShadowOutcomeReviewerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = M.validate_config(M.load_json(ROOT / "config" / "derivatives_shadow_outcome_review_v1.json"))
        self.observation = observation()
        self.as_of = self.observation["review_due_at"]

    def test_three_frozen_paths_and_metrics(self) -> None:
        review = M.build_review(self.observation, bars(), as_of=self.as_of, config=self.config)
        self.assertEqual([row["first_trigger"] for row in review["path_outcomes"]], ["TARGET", "STOP", "NONE"])
        self.assertEqual(review["mfe_pct"], 6.0)
        self.assertEqual(review["mae_pct"], -4.5)
        self.assertEqual(review["end_return_pct"], 1.0)
        self.assertEqual(review["rank_band"], "RANK_4_20")

    def test_same_bar_is_conservative_stop_first(self) -> None:
        review = M.build_review(
            self.observation,
            bars(target_index=100, low_index=None, same_bar=True),
            as_of=self.as_of,
            config=self.config,
        )
        self.assertEqual(review["path_outcomes"][0]["first_trigger"], "AMBIGUOUS_STOP_FIRST")

    def test_not_due_never_calls_provider_or_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            observations = root / "observations.jsonl"
            outcomes = root / "outcomes.jsonl"
            observations.write_text(M.canonical_json(self.observation) + "\n", encoding="utf-8")

            def forbidden_provider(_observation, _as_of):
                raise AssertionError("provider must not run before review_due_at")

            result = M.review_due_observations(
                observation_ledger=observations,
                outcome_ledger=outcomes,
                as_of="2030-01-07T23:59:59Z",
                config=self.config,
                bar_provider=forbidden_provider,
                max_workers=1,
                write=True,
            )
            self.assertEqual(result["not_due_count"], 1)
            self.assertEqual(result["due_unreviewed_count"], 0)
            self.assertFalse(outcomes.exists())

    def test_legacy_row_without_frozen_clock_is_not_backfilled(self) -> None:
        legacy = copy.deepcopy(self.observation)
        legacy["observation_id"] = "legacy-observation"
        legacy.pop("review_due_at")
        legacy.pop("evaluation_window_hours")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            observations = root / "observations.jsonl"
            outcomes = root / "outcomes.jsonl"
            observations.write_text(
                M.canonical_json(legacy) + "\n" + M.canonical_json(self.observation) + "\n",
                encoding="utf-8",
            )

            def forbidden_provider(_observation, _as_of):
                raise AssertionError("provider must not run for legacy or not-due rows")

            result = M.review_due_observations(
                observation_ledger=observations,
                outcome_ledger=outcomes,
                as_of="2030-01-07T23:59:59Z",
                config=self.config,
                bar_provider=forbidden_provider,
                max_workers=1,
                write=True,
            )
            self.assertEqual(result["observation_count"], 2)
            self.assertEqual(result["reviewable_observation_count"], 1)
            self.assertEqual(result["legacy_unreviewable_count"], 1)
            self.assertFalse(result["legacy_unreviewable"][0]["review_due_at_backfilled"])
            self.assertFalse(result["legacy_unreviewable"][0]["market_data_requested"])
            self.assertFalse(outcomes.exists())

    def test_candidate_local_failure_is_retryable(self) -> None:
        second = observation("BBBUSDT", rank=2)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            observations = root / "observations.jsonl"
            outcomes = root / "outcomes.jsonl"
            observations.write_text(
                M.canonical_json(self.observation) + "\n" + M.canonical_json(second) + "\n",
                encoding="utf-8",
            )

            def provider(item, _as_of):
                if item["symbol"] == "BBBUSDT":
                    raise RuntimeError("fixture failure")
                return bars()

            result = M.review_due_observations(
                observation_ledger=observations,
                outcome_ledger=outcomes,
                as_of=self.as_of,
                config=self.config,
                bar_provider=provider,
                max_workers=2,
                write=True,
            )
            self.assertEqual(result["appended_count"], 1)
            self.assertEqual(result["data_blocked_count"], 1)
            self.assertEqual(len(M.load_records(outcomes)), 1)
            blocked = next(row for row in result["results"] if row["symbol"] == "BBBUSDT")
            self.assertEqual(blocked["append_status"], "NOT_APPENDED_RETRYABLE")

    def test_incomplete_window_is_data_blocked_not_zero(self) -> None:
        review = M.build_review(self.observation, bars()[:-2], as_of=self.as_of, config=self.config)
        self.assertEqual(review["window_status"], "DATA_BLOCKED")
        self.assertEqual(review["path_outcomes"], [])
        self.assertNotIn("end_return_pct", review)

    def test_append_is_idempotent_and_collision_blocks(self) -> None:
        review = M.build_review(self.observation, bars(), as_of=self.as_of, config=self.config)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "outcomes.jsonl"
            self.assertEqual(M.append_review(path, review, self.observation, self.config), "APPENDED")
            self.assertEqual(M.append_review(path, review, self.observation, self.config), "NO_UPDATE")
            changed = copy.deepcopy(review)
            changed["end_price"] = 999.0
            with self.assertRaisesRegex(M.ShadowOutcomeError, "append_only_collision"):
                M.append_review(path, changed, self.observation, self.config)

    def test_original_binding_and_safety_are_mandatory(self) -> None:
        review = M.build_review(self.observation, bars(), as_of=self.as_of, config=self.config)
        for field in ("snapshot_id", "strategy_version", "config_digest", "source_digest"):
            self.assertEqual(review[field], self.observation[field])
        for field, expected in M.SAFETY_FLAGS.items():
            self.assertIs(review[field], expected)
        changed = copy.deepcopy(review)
        changed["real_money_roi_eligible"] = True
        with self.assertRaisesRegex(M.ShadowOutcomeError, "unsafe_review"):
            M.validate_review(changed, self.observation, self.config)

    def test_cohort_summary_keeps_factor_stage_and_rank_separate(self) -> None:
        first = M.build_review(self.observation, bars(), as_of=self.as_of, config=self.config)
        top3_observation = observation("BBBUSDT", rank=2)
        top3_observation["directional_state"] = "CROWDED_CONFLICT"
        second = M.build_review(top3_observation, bars(target_index=None, low_index=10), as_of=self.as_of, config=self.config)
        summary = M.summarize_cohorts([first, second])
        self.assertEqual(summary["directional_state:OI_ONLY_CONFLICT"]["sample_count"], 1)
        self.assertEqual(summary["directional_state:CROWDED_CONFLICT"]["sample_count"], 1)
        self.assertEqual(summary["rank_band:TOP3"]["sample_count"], 1)
        self.assertEqual(summary["rank_band:RANK_4_20"]["sample_count"], 1)

    def test_config_thresholds_cannot_be_retroactively_changed(self) -> None:
        changed = copy.deepcopy(self.config)
        changed["threshold_pairs"][0]["target_return_pct"] = 4.0
        with self.assertRaisesRegex(M.ShadowOutcomeError, "frozen_threshold_pairs_mismatch"):
            M.validate_config(changed)

    def test_self_test(self) -> None:
        self.assertEqual(M.self_test()["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
