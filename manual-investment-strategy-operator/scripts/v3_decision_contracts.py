#!/usr/bin/env python3
"""Typed V3 recommendation and outcome contracts with mode-specific validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Iterable

from v3_evidence_snapshot import EvidenceSnapshotV2
from v3_horizon_router import ResearchMode


def _parse_iso(value: str, field_name: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field_name}:required_iso_datetime")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field_name}:invalid_iso_datetime")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{field_name}:timezone_required")
        return None
    return parsed


def _validate_scenarios(
    scenarios: Iterable["ScenarioV2"], field_name: str, errors: list[str]
) -> None:
    items = tuple(scenarios)
    if {item.case for item in items} != {"bear", "base", "bull"} or len(items) != 3:
        errors.append(f"{field_name}:bear_base_bull_required")
        return
    if any(
        isinstance(item.probability_pct, bool)
        or not isinstance(item.probability_pct, (int, float))
        for item in items
    ):
        errors.append(f"{field_name}:numeric_probabilities_required")
        return
    total = sum(item.probability_pct for item in items)
    if abs(total - 100.0) > 1e-6:
        errors.append(f"{field_name}:probabilities_must_sum_100")
    for item in items:
        if not 0.0 <= item.probability_pct <= 100.0:
            errors.append(f"{field_name}:{item.case}:probability_out_of_range")
        if not item.outcome:
            errors.append(f"{field_name}:{item.case}:outcome_required")


class ResearchDecision(str, Enum):
    PREFERRED = "preferred"
    ELIGIBLE = "eligible"
    WATCH = "watch"
    REJECT = "reject"
    HOLD_QUALITY = "hold_quality"


class CurrentDirectDecision(str, Enum):
    ENTER_NOW = "enter_now"
    SMALL_ENTRY_NOW = "small_entry_now"
    DO_NOT_ENTER_NOW = "do_not_enter_now"
    HOLD_EXISTING = "hold_existing"
    EXIT_NOW = "exit_now"


class ExecutionDecision(str, Enum):
    MANUAL_EXECUTE_CANDIDATE = "manual_execute_candidate"
    NO_DEPLOY_CASH = "no_deploy_cash"
    NO_DEPLOY_EVIDENCE = "no_deploy_evidence"
    NO_DEPLOY_RISK = "no_deploy_risk"
    PAPER_ONLY = "paper_only"
    WATCH = "watch"
    NO_ACTION = "no_action"
    # Source-compatibility aliases. Their serialized values remain canonical.
    DEPLOY = "manual_execute_candidate"
    BLOCKED = "no_deploy_cash"


@dataclass(frozen=True)
class ScenarioV2:
    case: str
    probability_pct: float
    outcome: str


@dataclass(frozen=True)
class ProbabilityEstimateV2:
    event_id: str
    event_definition: str
    probability_pct: float
    sample_size: int
    base_probability_pct: float
    adjustments: tuple[str, ...]
    limitations: str
    calibration_status: str

    def validate(self, errors: list[str], field_name: str = "probability_event") -> None:
        if not self.event_id or not self.event_definition:
            errors.append(f"{field_name}:single_event_identity_required")
        if (
            isinstance(self.probability_pct, bool)
            or not isinstance(self.probability_pct, (int, float))
            or not 0.0 <= self.probability_pct <= 100.0
        ):
            errors.append(f"{field_name}:probability_out_of_range")
        if (
            isinstance(self.base_probability_pct, bool)
            or not isinstance(self.base_probability_pct, (int, float))
            or not 0.0 <= self.base_probability_pct <= 100.0
        ):
            errors.append(f"{field_name}:base_probability_out_of_range")
        if isinstance(self.sample_size, bool) or not isinstance(self.sample_size, int) or self.sample_size < 0:
            errors.append(f"{field_name}:non_negative_integer_sample_size_required")
        allowed = {"judgment_only", "wide_interval", "calibrated"}
        if self.calibration_status not in allowed:
            errors.append(f"{field_name}:unsupported_calibration_status")
        elif self.sample_size < 10 and self.calibration_status != "judgment_only":
            errors.append(f"{field_name}:n_lt_10_must_be_judgment_only")
        elif 10 <= self.sample_size < 30 and self.calibration_status == "calibrated":
            errors.append(f"{field_name}:n_lt_30_cannot_be_calibrated")
        if not self.limitations:
            errors.append(f"{field_name}:limitations_required")


@dataclass(frozen=True)
class RiskAdjustedPathV2:
    lane: str
    calculation_version: str
    benchmark: str
    risk_free_rate: dict[str, Any]
    windows: dict[str, Any]
    sharpe: dict[str, Any]
    sortino: dict[str, Any]
    information_ratio: dict[str, Any]
    max_drawdown: dict[str, Any]
    quality_score: float | None
    persistence_label: str
    flags: tuple[str, ...]
    data_quality: str
    evidence_ids: tuple[str, ...]
    promotion_status: str = "research_only_paper_only"
    ranking_adjustment_points: float = 0.0
    live_gate_effect: str = "none_until_promotion"

    def validate(self, errors: list[str]) -> None:
        if self.lane not in {
            "trend_continuation",
            "value_repair",
            "longterm_timing",
        }:
            errors.append("risk_adjusted_path:lane:unsupported")
        for name in (
            "calculation_version",
            "benchmark",
            "persistence_label",
            "data_quality",
            "promotion_status",
            "live_gate_effect",
        ):
            if not getattr(self, name):
                errors.append(f"risk_adjusted_path:{name}:required")
        if not isinstance(self.risk_free_rate, dict):
            errors.append("risk_adjusted_path:risk_free_rate:object_required")
        else:
            rate = self.risk_free_rate.get("annual_pct")
            if (
                isinstance(rate, bool)
                or not isinstance(rate, (int, float))
            ):
                errors.append("risk_adjusted_path:risk_free_rate:annual_pct_required")
            if not self.risk_free_rate.get("evidence_id") and not self.risk_free_rate.get(
                "fallback_used"
            ):
                errors.append(
                    "risk_adjusted_path:risk_free_rate:"
                    "evidence_id_or_explicit_fallback_required"
                )
        if not isinstance(self.windows, dict):
            errors.append("risk_adjusted_path:windows:object_required")
        else:
            for required_window in ("daily_20", "daily_60"):
                if required_window not in self.windows:
                    errors.append(
                        f"risk_adjusted_path:windows:{required_window}:required"
                    )
        for name in ("sharpe", "sortino", "information_ratio", "max_drawdown"):
            if not isinstance(getattr(self, name), dict):
                errors.append(f"risk_adjusted_path:{name}:object_required")
        if self.quality_score is not None and (
            isinstance(self.quality_score, bool)
            or not isinstance(self.quality_score, (int, float))
            or not 0.0 <= self.quality_score <= 100.0
        ):
            errors.append("risk_adjusted_path:quality_score:range_0_100_or_null")
        adjustment_limit = 4.0 if self.lane == "value_repair" else 7.5
        if (
            isinstance(self.ranking_adjustment_points, bool)
            or not isinstance(self.ranking_adjustment_points, (int, float))
            or abs(self.ranking_adjustment_points) > adjustment_limit
        ):
            errors.append(
                "risk_adjusted_path:ranking_adjustment_points:"
                f"bounded_plus_minus_{adjustment_limit:g}_required"
            )
        if self.live_gate_effect != "none_until_promotion":
            errors.append(
                "risk_adjusted_path:live_gate_effect:"
                "none_until_promotion_required"
            )


@dataclass(frozen=True)
class LongTermDCAPlanV2:
    fundamental_quality_rank: int
    raw_upside_rank: int
    portfolio_next_dollar_rank: int
    market_capacity: str
    adoption: str
    value_capture: str
    supply_dilution: str
    staking_net_yield_pct: float | None
    staking_liquidity_risk: str
    five_year_scenarios: tuple[ScenarioV2, ...]
    ten_year_scenarios: tuple[ScenarioV2, ...]
    contribution_plan: str
    quarterly_review_at: str
    annual_review_at: str
    thesis_invalidation: tuple[str, ...]

    def validate(self, errors: list[str]) -> None:
        for name in (
            "fundamental_quality_rank",
            "raw_upside_rank",
            "portfolio_next_dollar_rank",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                errors.append(f"longterm_plan:{name}:positive_integer_required")
        for name in (
            "market_capacity",
            "adoption",
            "value_capture",
            "supply_dilution",
            "staking_liquidity_risk",
            "contribution_plan",
        ):
            if not getattr(self, name):
                errors.append(f"longterm_plan:{name}:required")
        _validate_scenarios(self.five_year_scenarios, "longterm_plan:five_year_scenarios", errors)
        _validate_scenarios(self.ten_year_scenarios, "longterm_plan:ten_year_scenarios", errors)
        _parse_iso(self.quarterly_review_at, "longterm_plan:quarterly_review_at", errors)
        _parse_iso(self.annual_review_at, "longterm_plan:annual_review_at", errors)
        if not self.thesis_invalidation:
            errors.append("longterm_plan:thesis_invalidation:required")
        if self.staking_net_yield_pct is not None and (
            isinstance(self.staking_net_yield_pct, bool)
            or not isinstance(self.staking_net_yield_pct, (int, float))
        ):
            errors.append("longterm_plan:staking_net_yield_pct:number_or_null_required")


@dataclass(frozen=True)
class HoldingPeriodV2:
    min_calendar_days: int
    base_calendar_days: int
    max_calendar_days: int
    min_trading_days: int
    base_trading_days: int
    max_trading_days: int

    def validate(self, errors: list[str]) -> None:
        calendar = (
            self.min_calendar_days,
            self.base_calendar_days,
            self.max_calendar_days,
        )
        trading = (
            self.min_trading_days,
            self.base_trading_days,
            self.max_trading_days,
        )
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 1
            for item in calendar + trading
        ):
            errors.append("tactical_plan:holding_period:positive_integers_required")
        if not (calendar[0] <= calendar[1] <= calendar[2]):
            errors.append("tactical_plan:holding_period:calendar_order_invalid")
        if not (trading[0] <= trading[1] <= trading[2]):
            errors.append("tactical_plan:holding_period:trading_order_invalid")


@dataclass(frozen=True)
class TacticalPlanV2:
    entry_window_start: str
    entry_window_end: str
    allowed_session: str
    entry_price_min: float
    entry_price_max: float
    target_1_price: float
    target_2_price: float | None
    price_stop: float
    time_stop: str
    latest_exit_or_review_at: str
    expected_holding_days: HoldingPeriodV2
    target_1_evaluation_window: str
    target_2_evaluation_window: str | None
    event_plan: str
    historical_event_reaction: str
    derivatives_or_options: str
    volume_microstructure: str
    financing_dilution_or_unlock: str
    downside_gap_pressure: str
    probability_event: ProbabilityEstimateV2
    scenarios: tuple[ScenarioV2, ...]
    event_exit_date: str | None = None
    discovery_origin: str = "known_asset_market_scan"
    catalyst_derivation_chain: tuple[str, ...] = ()
    affiliation_status: str | None = None
    venue_discovery: str | None = None
    new_chain_security_gate: str | None = None
    auto_relay_forbidden: bool = True
    post_exit_state: str = "cash_pending_manual_reallocation"

    def validate(self, errors: list[str], *, event_trade: bool) -> None:
        start = _parse_iso(self.entry_window_start, "tactical_plan:entry_window_start", errors)
        end = _parse_iso(self.entry_window_end, "tactical_plan:entry_window_end", errors)
        latest = _parse_iso(
            self.latest_exit_or_review_at, "tactical_plan:latest_exit_or_review_at", errors
        )
        if start and end and start >= end:
            errors.append("tactical_plan:entry_window_must_increase")
        if end and latest and end > latest:
            errors.append("tactical_plan:latest_review_before_entry_deadline")
        if not self.allowed_session or not self.time_stop:
            errors.append("tactical_plan:allowed_session_and_time_stop_required")
        self.expected_holding_days.validate(errors)
        for name in (
            "target_1_evaluation_window",
            "event_plan",
            "historical_event_reaction",
            "derivatives_or_options",
            "volume_microstructure",
            "financing_dilution_or_unlock",
            "downside_gap_pressure",
        ):
            if not getattr(self, name):
                errors.append(f"tactical_plan:{name}:required")
        if self.target_2_price is not None and not self.target_2_evaluation_window:
            errors.append("tactical_plan:target_2_evaluation_window:required")
        if self.auto_relay_forbidden is not True:
            errors.append("tactical_plan:auto_relay_forbidden:true_required")
        if self.post_exit_state != "cash_pending_manual_reallocation":
            errors.append("tactical_plan:post_exit_state:cash_required")
        for name in ("entry_price_min", "entry_price_max", "target_1_price", "price_stop"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                errors.append(f"tactical_plan:{name}:positive_number_required")
        if self.target_2_price is not None and (
            isinstance(self.target_2_price, bool)
            or not isinstance(self.target_2_price, (int, float))
            or self.target_2_price <= 0
        ):
            errors.append("tactical_plan:target_2_price:positive_number_or_null_required")
        if self.entry_price_min > self.entry_price_max:
            errors.append("tactical_plan:entry_price_range_invalid")
        self.probability_event.validate(errors)
        _validate_scenarios(self.scenarios, "tactical_plan:scenarios", errors)
        if self.discovery_origin not in {
            "known_asset_market_scan",
            "new_chain_event",
            "company_or_protocol_event",
            "user_supplied_candidate",
        }:
            errors.append("tactical_plan:discovery_origin:unsupported")
        if self.discovery_origin == "new_chain_event":
            if len(self.catalyst_derivation_chain) < 5:
                errors.append(
                    "tactical_plan:catalyst_derivation_chain:"
                    "official_event_to_decision_required"
                )
            if self.affiliation_status not in {
                "official_asset",
                "official_ecosystem_partner",
                "unaffiliated_narrative_asset",
            }:
                errors.append(
                    "tactical_plan:affiliation_status:verified_status_required"
                )
            if not self.venue_discovery:
                errors.append("tactical_plan:venue_discovery:required")
            if not self.new_chain_security_gate:
                errors.append("tactical_plan:new_chain_security_gate:required")
            elif self.new_chain_security_gate != "pass":
                errors.append(
                    "tactical_plan:new_chain_security_gate:"
                    "entry_requires_pass"
                )
        if event_trade:
            if not self.event_exit_date:
                errors.append("tactical_plan:event_exit_date:required_for_event_trade")
            else:
                _parse_iso(self.event_exit_date, "tactical_plan:event_exit_date", errors)


@dataclass(frozen=True)
class ExistingPositionReviewPlanV2:
    position_evidence_id: str
    original_horizon: str
    remaining_horizon: str
    original_thesis: str
    cost_basis_status: str
    would_exit_realize_loss: bool | None
    thesis_status: str
    invalidation: tuple[str, ...]
    hold_trim_exit_decision: str
    next_review_at: str

    def validate(self, errors: list[str]) -> None:
        for name in (
            "position_evidence_id",
            "original_horizon",
            "remaining_horizon",
            "original_thesis",
            "cost_basis_status",
            "hold_trim_exit_decision",
        ):
            if not getattr(self, name):
                errors.append(f"position_review:{name}:required")
        if self.thesis_status not in {"intact", "weakened", "invalidated"}:
            errors.append("position_review:thesis_status:unsupported")
        if not self.invalidation:
            errors.append("position_review:invalidation:required")
        if self.would_exit_realize_loss not in {True, False, None}:
            errors.append("position_review:would_exit_realize_loss:boolean_or_null_required")
        _parse_iso(self.next_review_at, "position_review:next_review_at", errors)


@dataclass(frozen=True)
class DailyDualWindowPlanV2:
    branch: str
    baseline_id: str

    def validate(self, errors: list[str]) -> None:
        if self.branch not in {"morning", "evening"}:
            errors.append("daily_dual_window:branch:unsupported")
        if not self.baseline_id:
            errors.append("daily_dual_window:baseline_id:required")


@dataclass(frozen=True)
class RecommendationV2:
    recommendation_id: str
    request_id: str
    mode: ResearchMode
    symbol: str
    asset_class: str
    research_decision: ResearchDecision
    current_direct_decision: CurrentDirectDecision
    execution_decision: ExecutionDecision
    evidence_snapshot_id: str
    decision_price: float
    decision_price_evidence_id: str
    price_as_of: str
    deployable_cash: float
    cash_source: str
    execution_blockers: tuple[str, ...]
    decision_valid_until: str
    review_due_at: str
    created_at: str
    risk_adjusted_path: RiskAdjustedPathV2 | None = None
    longterm_plan: LongTermDCAPlanV2 | None = None
    tactical_plan: TacticalPlanV2 | None = None
    position_review_plan: ExistingPositionReviewPlanV2 | None = None
    daily_dual_window_plan: DailyDualWindowPlanV2 | None = None
    actual_fill_price: float | None = None
    observation_status: str = "pending"
    execution_status: str = "not_executed"
    outcome_status: str = "pending"
    data_quality_status: str = "degraded"
    human_confirmation_required: bool = True
    live_orders_enabled: bool = False
    private_api_used: bool = False

    def validation_errors(
        self, *, evidence_snapshot: EvidenceSnapshotV2 | None = None
    ) -> list[str]:
        errors: list[str] = []
        for name in ("recommendation_id", "request_id", "symbol", "asset_class"):
            if not getattr(self, name):
                errors.append(f"{name}:required")
        if not isinstance(self.mode, ResearchMode):
            errors.append("mode:ResearchMode_required")
        if not isinstance(self.research_decision, ResearchDecision):
            errors.append("research_decision:ResearchDecision_required")
        if not isinstance(self.current_direct_decision, CurrentDirectDecision):
            errors.append("current_direct_decision:CurrentDirectDecision_required")
        if not isinstance(self.execution_decision, ExecutionDecision):
            errors.append("execution_decision:ExecutionDecision_required")
        if self.observation_status not in {
            "pending",
            "triggered",
            "not_triggered",
            "expired",
            "invalidated",
            "not_applicable",
        }:
            errors.append("observation_status:unsupported")
        if self.execution_status not in {
            "not_executed",
            "planned",
            "blocked",
            "partially_executed",
            "executed",
            "cancelled",
            "not_applicable",
        }:
            errors.append("execution_status:unsupported")
        if self.outcome_status not in {
            "pending",
            "hit",
            "failed",
            "not_triggered",
            "expired",
            "invalidated",
            "superseded",
        }:
            errors.append("outcome_status:unsupported")
        if not self.data_quality_status:
            errors.append("data_quality_status:required")
        if self.human_confirmation_required is not True:
            errors.append("human_confirmation_required:true_required")
        if self.live_orders_enabled is not False:
            errors.append("live_orders_enabled:false_required")
        if self.private_api_used is not False:
            errors.append("private_api_used:false_required")
        if self.risk_adjusted_path is not None:
            self.risk_adjusted_path.validate(errors)
        price_at = _parse_iso(self.price_as_of, "price_as_of", errors)
        valid_until = _parse_iso(self.decision_valid_until, "decision_valid_until", errors)
        review_at = _parse_iso(self.review_due_at, "review_due_at", errors)
        created = _parse_iso(self.created_at, "created_at", errors)
        if price_at and created and price_at > created:
            errors.append("price_as_of:after_created_at")
        if created and valid_until and created >= valid_until:
            errors.append("decision_valid_until:must_be_after_created_at")
        if valid_until and review_at and valid_until > review_at:
            errors.append("review_due_at:before_decision_expiry")
        if (
            isinstance(self.decision_price, bool)
            or not isinstance(self.decision_price, (int, float))
            or self.decision_price <= 0
        ):
            errors.append("decision_price:positive_number_required")
        if not self.decision_price_evidence_id:
            errors.append("decision_price_evidence_id:required")
        if (
            isinstance(self.deployable_cash, bool)
            or not isinstance(self.deployable_cash, (int, float))
            or self.deployable_cash < 0
        ):
            errors.append("deployable_cash:non_negative_number_required")
        if not self.cash_source:
            errors.append("cash_source:required")
        if self.execution_decision == ExecutionDecision.MANUAL_EXECUTE_CANDIDATE:
            if self.deployable_cash <= 0:
                errors.append("execution_decision:deploy_requires_cash")
            if self.execution_blockers:
                errors.append("execution_decision:deploy_forbidden_with_blockers")
            if self.current_direct_decision not in {
                CurrentDirectDecision.ENTER_NOW,
                CurrentDirectDecision.SMALL_ENTRY_NOW,
            }:
                errors.append("execution_decision:deploy_requires_entry_now_decision")
        elif self.deployable_cash == 0:
            if self.execution_decision != ExecutionDecision.NO_DEPLOY_CASH:
                errors.append("execution_decision:zero_cash_requires_no_deploy_cash")
            if "no_deployable_cash" not in self.execution_blockers:
                errors.append("execution_blockers:no_deployable_cash_required")

        plans = [
            self.longterm_plan,
            self.tactical_plan,
            self.position_review_plan,
            self.daily_dual_window_plan,
        ]
        if sum(plan is not None for plan in plans) != 1:
            errors.append("mode_plan:exactly_one_required")
        elif self.mode == ResearchMode.LONGTERM_DCA:
            if self.longterm_plan is None:
                errors.append("mode_plan:longterm_plan_required")
            else:
                self.longterm_plan.validate(errors)
        elif self.mode in {ResearchMode.TACTICAL_1_7D, ResearchMode.EVENT_TRADE_1_3W}:
            if self.tactical_plan is None:
                errors.append("mode_plan:tactical_plan_required")
            else:
                self.tactical_plan.validate(
                    errors, event_trade=self.mode == ResearchMode.EVENT_TRADE_1_3W
                )
        elif self.mode == ResearchMode.EXISTING_POSITION_REVIEW:
            if self.position_review_plan is None:
                errors.append("mode_plan:position_review_plan_required")
            else:
                self.position_review_plan.validate(errors)
        elif self.mode == ResearchMode.DAILY_DUAL_WINDOW:
            if self.daily_dual_window_plan is None:
                errors.append("mode_plan:daily_dual_window_plan_required")
            else:
                self.daily_dual_window_plan.validate(errors)

        if evidence_snapshot is not None:
            if self.evidence_snapshot_id != evidence_snapshot.snapshot_id:
                errors.append("evidence_snapshot_id:mismatch")
            else:
                try:
                    evidence_price = evidence_snapshot.numeric(
                        self.decision_price_evidence_id, require_fresh=True
                    )
                except (KeyError, ValueError) as exc:
                    errors.append(str(exc))
                else:
                    if abs(evidence_price - float(self.decision_price)) > max(
                        1e-8, abs(evidence_price) * 1e-8
                    ):
                        errors.append("decision_price:evidence_value_mismatch")
        return errors

    def validate(self, *, evidence_snapshot: EvidenceSnapshotV2 | None = None) -> None:
        errors = self.validation_errors(evidence_snapshot=evidence_snapshot)
        if errors:
            raise ValueError(";".join(errors))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = "recommendation-v2"
        payload["request_mode"] = self.mode.value
        payload["generated_at"] = payload.pop("created_at")
        payload.pop("mode", None)
        payload["research_decision"] = self.research_decision.value
        payload["current_direct_decision"] = self.current_direct_decision.value
        payload["execution_decision"] = self.execution_decision.value
        return payload


@dataclass(frozen=True)
class OutcomeReviewV2:
    outcome_review_id: str
    recommendation_id: str
    reviewed_at: str
    review_due_at: str
    observation_triggered: bool
    executed: bool
    window_status: str
    first_result: str
    mfe_pct: float
    mae_pct: float
    actual_fill_price: float | None = None
    slippage_pct: float | None = None
    target_1_first_triggered_at: str | None = None
    stop_first_triggered_at: str | None = None
    event_gap_pct: float | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.outcome_review_id or not self.recommendation_id:
            errors.append("outcome_review_id_and_recommendation_id:required")
        _parse_iso(self.reviewed_at, "reviewed_at", errors)
        _parse_iso(self.review_due_at, "review_due_at", errors)
        if self.window_status not in {"ongoing", "expired", "resolved"}:
            errors.append("window_status:unsupported")
        allowed_results = {"target_before_stop", "stop_before_target", "neither", "same_bar_unknown"}
        if self.first_result not in allowed_results:
            errors.append("first_result:unsupported")
        if self.executed and self.actual_fill_price is None:
            errors.append("actual_fill_price:required_when_executed")
        if self.actual_fill_price is not None and (
            isinstance(self.actual_fill_price, bool)
            or not isinstance(self.actual_fill_price, (int, float))
            or self.actual_fill_price <= 0
        ):
            errors.append("actual_fill_price:positive_number_or_null_required")
        if not self.executed and (
            self.actual_fill_price is not None or self.slippage_pct is not None
        ):
            errors.append("fill_and_slippage:forbidden_when_not_executed")
        for name in ("mfe_pct", "mae_pct", "slippage_pct", "event_gap_pct"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                errors.append(f"{name}:number_or_null_required")
        target_at = (
            _parse_iso(self.target_1_first_triggered_at, "target_1_first_triggered_at", errors)
            if self.target_1_first_triggered_at
            else None
        )
        stop_at = (
            _parse_iso(self.stop_first_triggered_at, "stop_first_triggered_at", errors)
            if self.stop_first_triggered_at
            else None
        )
        if self.first_result == "target_before_stop" and (
            target_at is None or (stop_at is not None and target_at >= stop_at)
        ):
            errors.append("first_result:target_before_stop_inconsistent")
        if self.first_result == "stop_before_target" and (
            stop_at is None or (target_at is not None and stop_at >= target_at)
        ):
            errors.append("first_result:stop_before_target_inconsistent")
        if self.first_result == "neither" and (target_at is not None or stop_at is not None):
            errors.append("first_result:neither_inconsistent")
        return errors

    def validate(self) -> None:
        errors = self.validation_errors()
        if errors:
            raise ValueError(";".join(errors))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = "outcome-review-v2"
        payload["observation_status"] = (
            "triggered"
            if self.observation_triggered
            else ("pending" if self.window_status == "ongoing" else "not_triggered")
        )
        payload["execution_status"] = "executed" if self.executed else "not_executed"
        payload["outcome_status"] = {
            "target_before_stop": "hit",
            "stop_before_target": "failed",
            "same_bar_unknown": "invalidated",
            "neither": "pending" if self.window_status == "ongoing" else "expired",
        }[self.first_result]
        return payload


def _scenarios_from_payload(value: Any, field_name: str) -> tuple[ScenarioV2, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name}:array_required")
    try:
        return tuple(
            ScenarioV2(
                case=str(item["case"]),
                probability_pct=item["probability_pct"],
                outcome=str(item["outcome"]),
            )
            for item in value
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{field_name}:invalid_scenario") from exc


def _probability_from_payload(value: Any) -> ProbabilityEstimateV2:
    if not isinstance(value, dict):
        raise ValueError("probability_event:object_required")
    try:
        return ProbabilityEstimateV2(
            event_id=str(value["event_id"]),
            event_definition=str(value["event_definition"]),
            probability_pct=value["probability_pct"],
            sample_size=value["sample_size"],
            base_probability_pct=value["base_probability_pct"],
            adjustments=tuple(value.get("adjustments") or ()),
            limitations=str(value["limitations"]),
            calibration_status=str(value["calibration_status"]),
        )
    except KeyError as exc:
        raise ValueError(f"probability_event:missing:{exc.args[0]}") from exc


def _risk_adjusted_path_from_payload(value: Any) -> RiskAdjustedPathV2 | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("risk_adjusted_path:object_or_null_required")
    try:
        return RiskAdjustedPathV2(
            lane=str(value["lane"]),
            calculation_version=str(value["calculation_version"]),
            benchmark=str(value["benchmark"]),
            risk_free_rate=dict(value["risk_free_rate"]),
            windows=dict(value["windows"]),
            sharpe=dict(value["sharpe"]),
            sortino=dict(value["sortino"]),
            information_ratio=dict(value["information_ratio"]),
            max_drawdown=dict(value["max_drawdown"]),
            quality_score=value.get("quality_score"),
            persistence_label=str(value["persistence_label"]),
            flags=tuple(value.get("flags") or ()),
            data_quality=str(value["data_quality"]),
            evidence_ids=tuple(value.get("evidence_ids") or ()),
            promotion_status=str(
                value.get("promotion_status", "research_only_paper_only")
            ),
            ranking_adjustment_points=value.get(
                "ranking_adjustment_points", 0.0
            ),
            live_gate_effect=str(
                value.get("live_gate_effect", "none_until_promotion")
            ),
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"risk_adjusted_path:invalid:{exc}") from exc


def recommendation_from_payload(payload: dict[str, Any]) -> RecommendationV2:
    """Parse the canonical wire contract into the typed V3 contract."""

    if not isinstance(payload, dict):
        raise ValueError("recommendation:object_required")
    if payload.get("schema_version") != "recommendation-v2":
        raise ValueError("schema_version:recommendation-v2_required")
    try:
        mode = ResearchMode(payload["request_mode"])
        common: dict[str, Any] = {
            "recommendation_id": str(payload["recommendation_id"]),
            "request_id": str(payload["request_id"]),
            "mode": mode,
            "symbol": str(payload["symbol"]),
            "asset_class": str(payload["asset_class"]),
            "research_decision": ResearchDecision(payload["research_decision"]),
            "current_direct_decision": CurrentDirectDecision(
                payload["current_direct_decision"]
            ),
            "execution_decision": ExecutionDecision(payload["execution_decision"]),
            "evidence_snapshot_id": str(payload["evidence_snapshot_id"]),
            "decision_price": payload["decision_price"],
            "decision_price_evidence_id": str(
                payload["decision_price_evidence_id"]
            ),
            "price_as_of": str(payload["price_as_of"]),
            "deployable_cash": payload["deployable_cash"],
            "cash_source": str(payload["cash_source"]),
            "execution_blockers": tuple(payload.get("execution_blockers") or ()),
            "decision_valid_until": str(payload["decision_valid_until"]),
            "review_due_at": str(payload["review_due_at"]),
            "created_at": str(payload["generated_at"]),
            "risk_adjusted_path": _risk_adjusted_path_from_payload(
                payload.get("risk_adjusted_path")
            ),
            "actual_fill_price": payload.get("actual_fill_price"),
            "observation_status": str(payload["observation_status"]),
            "execution_status": str(payload["execution_status"]),
            "outcome_status": str(payload["outcome_status"]),
            "data_quality_status": str(payload["data_quality_status"]),
            "human_confirmation_required": payload[
                "human_confirmation_required"
            ],
            "live_orders_enabled": payload["live_orders_enabled"],
            "private_api_used": payload["private_api_used"],
        }
    except (KeyError, ValueError) as exc:
        raise ValueError(f"recommendation:invalid_common_field:{exc}") from exc

    if mode == ResearchMode.LONGTERM_DCA:
        value = payload.get("longterm_plan")
        if not isinstance(value, dict):
            raise ValueError("longterm_plan:object_required")
        try:
            common["longterm_plan"] = LongTermDCAPlanV2(
                fundamental_quality_rank=value["fundamental_quality_rank"],
                raw_upside_rank=value["raw_upside_rank"],
                portfolio_next_dollar_rank=value["portfolio_next_dollar_rank"],
                market_capacity=str(value["market_capacity"]),
                adoption=str(value["adoption"]),
                value_capture=str(value["value_capture"]),
                supply_dilution=str(value["supply_dilution"]),
                staking_net_yield_pct=value.get("staking_net_yield_pct"),
                staking_liquidity_risk=str(value["staking_liquidity_risk"]),
                five_year_scenarios=_scenarios_from_payload(
                    value["five_year_scenarios"], "five_year_scenarios"
                ),
                ten_year_scenarios=_scenarios_from_payload(
                    value["ten_year_scenarios"], "ten_year_scenarios"
                ),
                contribution_plan=str(value["contribution_plan"]),
                quarterly_review_at=str(value["quarterly_review_at"]),
                annual_review_at=str(value["annual_review_at"]),
                thesis_invalidation=tuple(value["thesis_invalidation"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"longterm_plan:invalid:{exc}") from exc
    elif mode in {ResearchMode.TACTICAL_1_7D, ResearchMode.EVENT_TRADE_1_3W}:
        value = payload.get("tactical_plan")
        if not isinstance(value, dict):
            raise ValueError("tactical_plan:object_required")
        try:
            common["tactical_plan"] = TacticalPlanV2(
                entry_window_start=str(value["entry_window_start"]),
                entry_window_end=str(value["entry_window_end"]),
                allowed_session=str(value["allowed_session"]),
                entry_price_min=value["entry_price_min"],
                entry_price_max=value["entry_price_max"],
                target_1_price=value["target_1_price"],
                target_2_price=value.get("target_2_price"),
                price_stop=value["price_stop"],
                time_stop=str(value["time_stop"]),
                latest_exit_or_review_at=str(value["latest_exit_or_review_at"]),
                expected_holding_days=HoldingPeriodV2(
                    **value["expected_holding_days"]
                ),
                target_1_evaluation_window=str(
                    value["target_1_evaluation_window"]
                ),
                target_2_evaluation_window=value.get(
                    "target_2_evaluation_window"
                ),
                event_plan=str(value["event_plan"]),
                historical_event_reaction=str(
                    value["historical_event_reaction"]
                ),
                derivatives_or_options=str(value["derivatives_or_options"]),
                volume_microstructure=str(value["volume_microstructure"]),
                financing_dilution_or_unlock=str(
                    value["financing_dilution_or_unlock"]
                ),
                downside_gap_pressure=str(value["downside_gap_pressure"]),
                probability_event=_probability_from_payload(
                    value["probability_event"]
                ),
                scenarios=_scenarios_from_payload(
                    value["scenarios"], "tactical_scenarios"
                ),
                event_exit_date=value.get("event_exit_date"),
                auto_relay_forbidden=value.get("auto_relay_forbidden", True),
                post_exit_state=str(
                    value.get(
                        "post_exit_state",
                        "cash_pending_manual_reallocation",
                    )
                ),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"tactical_plan:invalid:{exc}") from exc
    elif mode == ResearchMode.EXISTING_POSITION_REVIEW:
        value = payload.get("position_review_plan")
        if not isinstance(value, dict):
            raise ValueError("position_review_plan:object_required")
        try:
            common["position_review_plan"] = ExistingPositionReviewPlanV2(
                position_evidence_id=str(value["position_evidence_id"]),
                original_horizon=str(value["original_horizon"]),
                remaining_horizon=str(value["remaining_horizon"]),
                original_thesis=str(value["original_thesis"]),
                cost_basis_status=str(value["cost_basis_status"]),
                would_exit_realize_loss=value.get("would_exit_realize_loss"),
                thesis_status=str(value["thesis_status"]),
                invalidation=tuple(value["invalidation"]),
                hold_trim_exit_decision=str(
                    value["hold_trim_exit_decision"]
                ),
                next_review_at=str(value["next_review_at"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"position_review_plan:invalid:{exc}") from exc
    else:
        value = payload.get("daily_dual_window_plan")
        if not isinstance(value, dict):
            raise ValueError("daily_dual_window_plan:object_required")
        try:
            common["daily_dual_window_plan"] = DailyDualWindowPlanV2(
                branch=str(value["branch"]),
                baseline_id=str(value["baseline_id"]),
            )
        except KeyError as exc:
            raise ValueError(f"daily_dual_window_plan:invalid:{exc}") from exc

    return RecommendationV2(**common)


def validate_recommendation_v2_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Shared write-gate adapter used by recommendation history and dispatch."""

    try:
        recommendation = recommendation_from_payload(payload)
        recommendation.validate()
    except (TypeError, ValueError) as exc:
        return {"status": "failed", "errors": [str(exc)]}
    return {"status": "ok", "errors": []}
