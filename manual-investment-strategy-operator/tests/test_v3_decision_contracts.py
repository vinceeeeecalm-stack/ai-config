import dataclasses
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from v3_decision_contracts import (  # noqa: E402
    CurrentDirectDecision,
    ExecutionDecision,
    HoldingPeriodV2,
    LongTermDCAPlanV2,
    OutcomeReviewV2,
    ProbabilityEstimateV2,
    RecommendationV2,
    ResearchDecision,
    RiskAdjustedPathV2,
    ScenarioV2,
    TacticalPlanV2,
)
from v3_evidence_snapshot import EvidenceSnapshotV2, NumericEvidenceV2  # noqa: E402
from v3_horizon_router import ResearchMode  # noqa: E402


NOW = "2026-07-25T12:00:00+08:00"


def scenarios():
    return (
        ScenarioV2("bear", 25, "capital loss"),
        ScenarioV2("base", 50, "moderate gain"),
        ScenarioV2("bull", 25, "large gain"),
    )


def frozen_snapshot():
    return EvidenceSnapshotV2.build(
        snapshot_id="snapshot-v3",
        generated_at="2026-07-25T12:00:01+08:00",
        cutoff_at=NOW,
        records=[
            NumericEvidenceV2(
                evidence_id="sol-price",
                category="spot",
                symbol="SOL",
                metric="spot_price",
                value=188.25,
                unit="USD",
                as_of="2026-07-25T11:59:45+08:00",
                source="public-primary",
                source_role="primary",
                max_age_seconds=120,
            )
        ],
    )


def longterm_plan():
    return LongTermDCAPlanV2(
        fundamental_quality_rank=2,
        raw_upside_rank=1,
        portfolio_next_dollar_rank=1,
        market_capacity="Large smart-contract settlement market.",
        adoption="Active users and developer adoption tracked quarterly.",
        value_capture="Fees and staking accrue to the asset.",
        supply_dilution="Issuance is offset in part by burns.",
        staking_net_yield_pct=4.5,
        staking_liquidity_risk="Unbonding and liquid-staking counterparty risk.",
        five_year_scenarios=scenarios(),
        ten_year_scenarios=scenarios(),
        contribution_plan="Use only new monthly crypto contributions.",
        quarterly_review_at="2026-10-25T12:00:00+08:00",
        annual_review_at="2027-07-25T12:00:00+08:00",
        thesis_invalidation=("Sustained loss of developers and fee demand.",),
    )


def probability():
    return ProbabilityEstimateV2(
        event_id="target1-before-stop-7d",
        event_definition="Target 1 trades before the price stop within seven calendar days.",
        probability_pct=55,
        sample_size=40,
        base_probability_pct=52,
        adjustments=("Current volume regime +3 percentage points.",),
        limitations="Historical analogs do not guarantee this path.",
        calibration_status="calibrated",
    )


def risk_adjusted_path():
    windows = {
        "daily_20": {
            "sharpe": 2.1,
            "sortino": 3.0,
            "information_ratio": 1.2,
            "max_drawdown_pct": -6.0,
        },
        "daily_60": {
            "sharpe": 1.3,
            "sortino": 1.8,
            "information_ratio": 1.1,
            "max_drawdown_pct": -12.0,
        },
    }
    return RiskAdjustedPathV2(
        lane="trend_continuation",
        calculation_version="risk-adjusted-path-v1",
        benchmark="BTCUSDT",
        risk_free_rate={
            "annual_pct": 4.0,
            "evidence_id": "risk-free-evidence",
            "fallback_used": False,
        },
        windows=windows,
        sharpe={key: value["sharpe"] for key, value in windows.items()},
        sortino={key: value["sortino"] for key, value in windows.items()},
        information_ratio={
            key: value["information_ratio"] for key, value in windows.items()
        },
        max_drawdown={
            key: value["max_drawdown_pct"] for key, value in windows.items()
        },
        quality_score=68.0,
        persistence_label="positive_but_not_exceptional",
        flags=(),
        data_quality="verified",
        evidence_ids=("risk-free-evidence",),
        ranking_adjustment_points=2.7,
    )


def tactical_plan(*, event_exit_date=None):
    return TacticalPlanV2(
        entry_window_start="2026-07-25T21:30:00+08:00",
        entry_window_end="2026-07-27T04:00:00+08:00",
        allowed_session="us_regular_session_only",
        entry_price_min=185,
        entry_price_max=190,
        target_1_price=205,
        target_2_price=220,
        price_stop=178,
        time_stop="Exit if target 1 is not tested by the third session close.",
        latest_exit_or_review_at="2026-08-01T04:00:00+08:00",
        expected_holding_days=HoldingPeriodV2(1, 4, 7, 1, 3, 5),
        target_1_evaluation_window="sessions 2 through 4",
        target_2_evaluation_window="sessions 4 through 5",
        event_plan="Exit before material scheduled event risk.",
        historical_event_reaction="Frozen sample summary with evidence IDs.",
        derivatives_or_options="Options unavailable; probability locally degraded.",
        volume_microstructure="Volume and spread checked at snapshot cutoff.",
        financing_dilution_or_unlock="No new filing in snapshot; monitor SEC/IR.",
        downside_gap_pressure="Elevated event-gap risk reflected in bear case.",
        probability_event=probability(),
        scenarios=scenarios(),
        event_exit_date=event_exit_date,
    )


def recommendation(*, mode=ResearchMode.LONGTERM_DCA):
    return RecommendationV2(
        recommendation_id=f"rec-{mode.value}",
        request_id=f"request:{mode.value}",
        mode=mode,
        symbol="SOL",
        asset_class="crypto",
        research_decision=ResearchDecision.PREFERRED,
        current_direct_decision=CurrentDirectDecision.SMALL_ENTRY_NOW,
        execution_decision=ExecutionDecision.BLOCKED,
        evidence_snapshot_id="snapshot-v3",
        decision_price=188.25,
        decision_price_evidence_id="sol-price",
        price_as_of="2026-07-25T11:59:45+08:00",
        deployable_cash=0,
        cash_source="new settled USDT only",
        execution_blockers=("no_deployable_cash",),
        decision_valid_until="2026-07-26T12:00:00+08:00",
        review_due_at="2026-10-25T12:00:00+08:00",
        created_at=NOW,
        longterm_plan=longterm_plan() if mode == ResearchMode.LONGTERM_DCA else None,
        tactical_plan=tactical_plan()
        if mode in {ResearchMode.TACTICAL_1_7D, ResearchMode.EVENT_TRADE_1_3W}
        else None,
    )


class RecommendationV2Test(unittest.TestCase):
    def test_longterm_dca_needs_no_tactical_price_stop(self):
        item = recommendation()
        self.assertEqual(item.validation_errors(evidence_snapshot=frozen_snapshot()), [])
        self.assertFalse(hasattr(item.longterm_plan, "price_stop"))

    def test_zero_cash_blocks_execution_not_research_conclusion(self):
        item = recommendation()
        self.assertEqual(item.research_decision, ResearchDecision.PREFERRED)
        self.assertEqual(item.current_direct_decision, CurrentDirectDecision.SMALL_ENTRY_NOW)
        self.assertEqual(item.execution_decision, ExecutionDecision.BLOCKED)
        self.assertEqual(item.validation_errors(evidence_snapshot=frozen_snapshot()), [])

    def test_deploy_requires_cash_and_no_blockers(self):
        item = dataclasses.replace(recommendation(), execution_decision=ExecutionDecision.DEPLOY)
        errors = item.validation_errors(evidence_snapshot=frozen_snapshot())
        self.assertIn("execution_decision:deploy_requires_cash", errors)
        self.assertIn("execution_decision:deploy_forbidden_with_blockers", errors)

    def test_longterm_and_tactical_cannot_share_one_record(self):
        item = dataclasses.replace(recommendation(), tactical_plan=tactical_plan())
        self.assertIn("mode_plan:exactly_one_required", item.validation_errors())

    def test_tactical_scenario_probabilities_must_sum_100(self):
        invalid_scenarios = (
            ScenarioV2("bear", 25, "loss"),
            ScenarioV2("base", 50, "flat"),
            ScenarioV2("bull", 35, "gain"),
        )
        plan = dataclasses.replace(tactical_plan(), scenarios=invalid_scenarios)
        item = dataclasses.replace(
            recommendation(mode=ResearchMode.TACTICAL_1_7D),
            tactical_plan=plan,
            review_due_at="2026-08-01T04:00:00+08:00",
        )
        self.assertIn(
            "tactical_plan:scenarios:probabilities_must_sum_100",
            item.validation_errors(),
        )

    def test_probability_is_bound_to_one_named_event(self):
        invalid_probability = dataclasses.replace(probability(), event_id="")
        plan = dataclasses.replace(tactical_plan(), probability_event=invalid_probability)
        item = dataclasses.replace(
            recommendation(mode=ResearchMode.TACTICAL_1_7D),
            tactical_plan=plan,
            review_due_at="2026-08-01T04:00:00+08:00",
        )
        self.assertIn(
            "probability_event:single_event_identity_required",
            item.validation_errors(),
        )

    def test_probability_calibration_respects_sample_size(self):
        invalid_probability = dataclasses.replace(
            probability(), sample_size=9, calibration_status="calibrated"
        )
        errors = []
        invalid_probability.validate(errors)
        self.assertIn("probability_event:n_lt_10_must_be_judgment_only", errors)

    def test_event_trade_requires_explicit_event_exit_date(self):
        item = dataclasses.replace(
            recommendation(mode=ResearchMode.EVENT_TRADE_1_3W),
            review_due_at="2026-08-01T04:00:00+08:00",
        )
        self.assertIn(
            "tactical_plan:event_exit_date:required_for_event_trade",
            item.validation_errors(),
        )

    def test_new_chain_candidate_requires_derivation_and_security_gate(self):
        plan = dataclasses.replace(
            tactical_plan(),
            discovery_origin="new_chain_event",
            affiliation_status="unaffiliated_narrative_asset",
        )
        item = dataclasses.replace(
            recommendation(mode=ResearchMode.TACTICAL_1_7D),
            tactical_plan=plan,
            review_due_at="2026-08-01T04:00:00+08:00",
        )
        errors = item.validation_errors()
        self.assertIn(
            "tactical_plan:catalyst_derivation_chain:"
            "official_event_to_decision_required",
            errors,
        )
        self.assertIn("tactical_plan:venue_discovery:required", errors)
        self.assertIn("tactical_plan:new_chain_security_gate:required", errors)

    def test_qualified_new_chain_candidate_keeps_affiliation_explicit(self):
        plan = dataclasses.replace(
            tactical_plan(),
            discovery_origin="new_chain_event",
            catalyst_derivation_chain=(
                "official event",
                "chain ecosystem",
                "DEX pair",
                "market confirmation",
                "manual decision",
            ),
            affiliation_status="unaffiliated_narrative_asset",
            venue_discovery="GeckoTerminal plus DEX Screener",
            new_chain_security_gate="pass",
        )
        item = dataclasses.replace(
            recommendation(mode=ResearchMode.TACTICAL_1_7D),
            tactical_plan=plan,
            review_due_at="2026-08-01T04:00:00+08:00",
        )
        self.assertEqual(
            item.validation_errors(evidence_snapshot=frozen_snapshot()),
            [],
        )

    def test_decision_price_must_match_fresh_snapshot_evidence(self):
        item = dataclasses.replace(recommendation(), decision_price=190)
        self.assertIn(
            "decision_price:evidence_value_mismatch",
            item.validation_errors(evidence_snapshot=frozen_snapshot()),
        )

    def test_review_date_is_explicit_iso_not_natural_language(self):
        item = dataclasses.replace(recommendation(), review_due_at="复盘 2026 天后")
        self.assertIn("review_due_at:invalid_iso_datetime", item.validation_errors())

    def test_live_orders_or_missing_human_confirmation_are_blocked(self):
        live = dataclasses.replace(recommendation(), live_orders_enabled=True)
        self.assertIn("live_orders_enabled:false_required", live.validation_errors())
        automated = dataclasses.replace(
            recommendation(), human_confirmation_required=False
        )
        self.assertIn(
            "human_confirmation_required:true_required",
            automated.validation_errors(),
        )

    def test_risk_adjusted_path_is_optional_and_does_not_change_live_gate(self):
        item = dataclasses.replace(
            recommendation(),
            risk_adjusted_path=risk_adjusted_path(),
        )
        self.assertEqual(
            item.validation_errors(evidence_snapshot=frozen_snapshot()),
            [],
        )
        self.assertEqual(
            item.risk_adjusted_path.live_gate_effect,
            "none_until_promotion",
        )

    def test_risk_adjusted_path_cannot_claim_live_gate_promotion(self):
        invalid_path = dataclasses.replace(
            risk_adjusted_path(),
            live_gate_effect="direct_live_gate",
        )
        item = dataclasses.replace(
            recommendation(),
            risk_adjusted_path=invalid_path,
        )
        self.assertIn(
            "risk_adjusted_path:live_gate_effect:none_until_promotion_required",
            item.validation_errors(),
        )


class OutcomeReviewV2Test(unittest.TestCase):
    def test_outcome_separates_observed_trigger_from_real_execution(self):
        review = OutcomeReviewV2(
            outcome_review_id="outcome-1",
            recommendation_id="rec-1",
            reviewed_at="2026-08-01T12:00:00+08:00",
            review_due_at="2026-08-01T04:00:00+08:00",
            observation_triggered=True,
            executed=False,
            window_status="resolved",
            first_result="target_before_stop",
            mfe_pct=12,
            mae_pct=-3,
            target_1_first_triggered_at="2026-07-29T03:00:00+08:00",
        )
        self.assertEqual(review.validation_errors(), [])

    def test_executed_outcome_requires_actual_fill(self):
        review = OutcomeReviewV2(
            outcome_review_id="outcome-2",
            recommendation_id="rec-2",
            reviewed_at="2026-08-01T12:00:00+08:00",
            review_due_at="2026-08-01T04:00:00+08:00",
            observation_triggered=True,
            executed=True,
            window_status="expired",
            first_result="neither",
            mfe_pct=2,
            mae_pct=-2,
        )
        self.assertIn(
            "actual_fill_price:required_when_executed", review.validation_errors()
        )

    def test_first_trigger_order_must_match_declared_result(self):
        review = OutcomeReviewV2(
            outcome_review_id="outcome-3",
            recommendation_id="rec-3",
            reviewed_at="2026-08-01T12:00:00+08:00",
            review_due_at="2026-08-01T04:00:00+08:00",
            observation_triggered=True,
            executed=True,
            actual_fill_price=188,
            slippage_pct=0.1,
            window_status="resolved",
            first_result="target_before_stop",
            mfe_pct=5,
            mae_pct=-6,
            target_1_first_triggered_at="2026-07-30T03:00:00+08:00",
            stop_first_triggered_at="2026-07-29T03:00:00+08:00",
        )
        self.assertIn(
            "first_result:target_before_stop_inconsistent",
            review.validation_errors(),
        )


if __name__ == "__main__":
    unittest.main()
