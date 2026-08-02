from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "derivatives_shadow_promotion_evaluator",
    SCRIPTS / "derivatives_shadow_promotion_evaluator.py",
)
M = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(M)


def review(index: int, *, experiment: bool, early: bool, trigger: str, snapshot: int) -> dict:
    state = "CONFIRMED_LONG" if experiment else "OI_ONLY_CONFLICT"
    rank = 4 + index % 17 if early else 1 + index % 3
    return {
        "schema_version": "DerivativesShadowOutcomeReviewV1",
        "review_id": f"review-{index}-{state}-{rank}-{snapshot}",
        "observation_id": f"observation-{index}-{state}-{rank}-{snapshot}",
        "symbol": f"S{index}USDT",
        "discovery_rank": rank,
        "rank_band": "RANK_4_20" if early else "TOP3",
        "snapshot_id": f"snapshot-{snapshot}",
        "strategy_version": "unified-shortterm-derivatives-shadow-v2",
        "config_digest": "a" * 64,
        "source_digest": "b" * 64,
        "reviewer_version": "derivatives-shadow-outcome-review-v1",
        "review_config_digest": "c42e46023e248a9053637f3b4543b29cc2ad3096c3f5952df6ebd0a4eb5a395e",
        "observed_at": "2026-07-01T00:00:00Z",
        "review_due_at": "2026-07-08T00:00:00Z",
        "reviewed_at": "2026-07-08T00:00:00Z",
        "generated_at": "2026-07-08T00:00:00Z",
        "window_start_at": "2026-07-01T00:00:00Z",
        "window_end_at": "2026-07-07T23:59:59.999000Z",
        "closed_bar_count": 672,
        "window_status": "COMPLETE",
        "directional_state_at_capture": state,
        "production_signal_stage_at_capture": "early_watch",
        "mfe_pct": 6.0 if trigger == "TARGET" else 2.0,
        "mae_pct": -2.0 if experiment else -3.5,
        "end_return_pct": 2.0 if trigger == "TARGET" else -1.0,
        "path_outcomes": [
            {"threshold_id": "target_5_stop_3", "first_trigger": trigger},
            {"threshold_id": "target_8_stop_4", "first_trigger": "NONE"},
            {"threshold_id": "target_10_stop_5", "first_trigger": "NONE"},
        ],
        **M.SAFETY_FLAGS,
    }


def eligible_fixture() -> list[dict]:
    rows = []
    # 10 experiment: all targets, six are early-discovery candidates.
    for index in range(10):
        rows.append(review(index, experiment=True, early=index < 6, trigger="TARGET", snapshot=index % 2))
    # 30 controls: 12 targets and 18 stops; 15 controls are rank 4-20 with five targets.
    for offset in range(30):
        rows.append(review(100 + offset, experiment=False, early=offset < 15, trigger="TARGET" if offset < 12 else "STOP", snapshot=offset % 2))
    return rows


class PromotionEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = M.validate_config(M.load_json(ROOT / "config" / "derivatives_shadow_promotion_gate_v1.json"))

    def test_empty_and_small_ledgers_require_more_evidence(self) -> None:
        empty = M.evaluate([], self.config)
        self.assertEqual(empty["decision"], "MORE_EVIDENCE_REQUIRED")
        self.assertIn("complete_reviews", empty["all_blockers"])
        small = M.evaluate(eligible_fixture()[:20], self.config)
        self.assertEqual(small["decision"], "MORE_EVIDENCE_REQUIRED")

    def test_eligible_fixture_only_allows_separate_review(self) -> None:
        result = M.evaluate(eligible_fixture(), self.config)
        self.assertEqual(result["decision"], "PROMOTION_REVIEW_ELIGIBLE")
        self.assertEqual(result["decision_effect"], "SEPARATE_GOVERNED_REVIEW_ONLY")
        self.assertFalse(result["production_rule_changed"])
        self.assertFalse(result["formal_action_eligible"])

    def test_nonpositive_uplift_rejects_phase1(self) -> None:
        rows = eligible_fixture()
        for row in rows:
            if row["directional_state_at_capture"] == "CONFIRMED_LONG":
                row["path_outcomes"][0]["first_trigger"] = "STOP"
                row["mfe_pct"] = 1.0
                row["mae_pct"] = -4.0
        result = M.evaluate(rows, self.config)
        self.assertEqual(result["decision"], "REJECT_PHASE1")
        self.assertEqual(result["decision_effect"], "NO_PRODUCTION_CHANGE")

    def test_diagnostic_paths_cannot_replace_primary(self) -> None:
        rows = eligible_fixture()
        for row in rows:
            if row["directional_state_at_capture"] == "CONFIRMED_LONG":
                row["path_outcomes"][0]["first_trigger"] = "STOP"
                row["path_outcomes"][1]["first_trigger"] = "TARGET"
                row["path_outcomes"][2]["first_trigger"] = "TARGET"
        self.assertEqual(M.evaluate(rows, self.config)["decision"], "REJECT_PHASE1")

    def test_safety_or_review_binding_pollution_is_blocked(self) -> None:
        rows = eligible_fixture()
        unsafe = copy.deepcopy(rows)
        unsafe[0]["real_money_roi_eligible"] = True
        with self.assertRaisesRegex(M.PromotionGateError, "unsafe_review"):
            M.evaluate(unsafe, self.config)
        wrong = copy.deepcopy(rows)
        wrong[0]["review_config_digest"] = "0" * 64
        with self.assertRaisesRegex(M.PromotionGateError, "review_config_digest_mismatch"):
            M.evaluate(wrong, self.config)

    def test_config_cannot_switch_primary_or_cohorts(self) -> None:
        changed = copy.deepcopy(self.config)
        changed["primary_threshold_id"] = "target_8_stop_4"
        with self.assertRaisesRegex(M.PromotionGateError, "frozen_config_changed:primary_threshold_id"):
            M.validate_config(changed)
        changed = copy.deepcopy(self.config)
        changed["experiment_directional_states"] = ["OI_ONLY_CONFLICT"]
        with self.assertRaisesRegex(M.PromotionGateError, "frozen_config_changed:experiment_directional_states"):
            M.validate_config(changed)

    def test_numeric_sample_gates_are_frozen_exactly(self) -> None:
        for field in (
            "minimum_complete_reviews",
            "minimum_experiment_samples",
            "minimum_control_samples",
            "minimum_rank_4_20_experiment_samples",
            "minimum_rank_4_20_control_samples",
        ):
            changed = copy.deepcopy(self.config)
            changed[field] = 1
            with self.assertRaisesRegex(M.PromotionGateError, f"frozen_config_changed:{field}"):
                M.validate_config(changed)

    def test_time_window_and_cross_review_lineage_are_required(self) -> None:
        rows = eligible_fixture()
        missing_time = copy.deepcopy(rows)
        missing_time[0].pop("review_due_at")
        with self.assertRaisesRegex(M.PromotionGateError, "missing_time:review_due_at"):
            M.evaluate(missing_time, self.config)
        future = copy.deepcopy(rows)
        future[0]["window_end_at"] = "2026-07-08T00:00:01Z"
        with self.assertRaisesRegex(M.PromotionGateError, "window_end_outside"):
            M.evaluate(future, self.config)
        mixed = copy.deepcopy(rows)
        mixed[0]["source_digest"] = "c" * 64
        mixed_result = M.evaluate(mixed, self.config)
        self.assertEqual(mixed_result["decision"], "MORE_EVIDENCE_REQUIRED")
        self.assertIn("cross_review_lineage_mismatch:source_digest", mixed_result["all_blockers"])
        self.assertFalse(mixed_result["evidence_pool_valid"])
        self.assertEqual(mixed_result["primary_metrics"]["all_experiment"]["sample_count"], 0)

    def test_absolute_future_results_are_blocked(self) -> None:
        rows = eligible_fixture()
        for row in rows:
            row.update({
                "observed_at": "2099-01-01T00:00:00Z",
                "review_due_at": "2099-01-08T00:00:00Z",
                "reviewed_at": "2099-01-08T00:00:00Z",
                "generated_at": "2099-01-08T00:00:00Z",
                "window_start_at": "2099-01-01T00:00:00Z",
                "window_end_at": "2099-01-07T23:59:59.999000Z",
            })
        with self.assertRaisesRegex(M.PromotionGateError, "absolute_future_time"):
            M.evaluate(rows, self.config)

    def test_sufficient_but_partially_failed_benefit_gate_rejects(self) -> None:
        rows = eligible_fixture()
        early_experiment = [
            row for row in rows
            if row["directional_state_at_capture"] == "CONFIRMED_LONG" and row["rank_band"] == "RANK_4_20"
        ]
        for row in early_experiment[-2:]:
            row["path_outcomes"][0]["first_trigger"] = "STOP"
        early_control = [
            row for row in rows
            if row["directional_state_at_capture"] != "CONFIRMED_LONG" and row["rank_band"] == "RANK_4_20"
        ]
        for row in early_control[9:]:
            row["path_outcomes"][0]["first_trigger"] = "STOP"
        result = M.evaluate(rows, self.config)
        self.assertTrue(all(result["sample_checks"].values()))
        self.assertGreater(result["rank_4_20_uplift_percentage_points"], 0)
        self.assertLess(result["rank_4_20_uplift_percentage_points"], 10)
        self.assertEqual(result["decision"], "REJECT_PHASE1")

    def test_self_test(self) -> None:
        self.assertEqual(M.self_test()["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
