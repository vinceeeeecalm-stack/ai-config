#!/usr/bin/env python3
"""Validate and render the deterministic V3 investment decision package.

This is a research/write gate only. It never sends orders or moves assets.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from v3_decision_contracts import recommendation_from_payload
from v3_evidence_snapshot import snapshot_from_payload
from v3_horizon_router import ResearchMode
from v3_portfolio_state import portfolio_state_from_payload


def validate_package(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != "investment-research-package-v3":
        errors.append("schema_version:investment-research-package-v3_required")
    try:
        state = portfolio_state_from_payload(payload["portfolio_state"])
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"portfolio_state:{exc}")
        state = None
    try:
        snapshot = snapshot_from_payload(payload["evidence_snapshot"])
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"evidence_snapshot:{exc}")
        snapshot = None

    request_specs = payload.get("request_specs")
    request_modes: dict[str, str] = {}
    if not isinstance(request_specs, list) or not request_specs:
        errors.append("request_specs:non_empty_array_required")
    else:
        for index, spec in enumerate(request_specs):
            if not isinstance(spec, dict):
                errors.append(f"request_specs[{index}]:object_required")
                continue
            request_id = str(spec.get("request_id") or "")
            mode = str(spec.get("request_mode") or "")
            if not request_id or mode not in {item.value for item in ResearchMode}:
                errors.append(f"request_specs[{index}]:invalid_identity_or_mode")
            elif request_id in request_modes:
                errors.append(f"request_specs:duplicate_request_id:{request_id}")
            else:
                request_modes[request_id] = mode

    raw_recommendations = payload.get("recommendations")
    if not isinstance(raw_recommendations, list):
        errors.append("recommendations:array_required")
        raw_recommendations = []
    recommendation_ids: set[str] = set()
    preferred_by_mode: dict[str, int] = {}
    validated: list[Any] = []
    for index, raw in enumerate(raw_recommendations):
        try:
            item = recommendation_from_payload(raw)
            item.validate(evidence_snapshot=snapshot)
        except (TypeError, ValueError) as exc:
            errors.append(f"recommendations[{index}]:{exc}")
            continue
        if item.recommendation_id in recommendation_ids:
            errors.append(
                f"recommendations:duplicate_id:{item.recommendation_id}"
            )
        recommendation_ids.add(item.recommendation_id)
        expected_mode = request_modes.get(item.request_id)
        if expected_mode != item.mode.value:
            errors.append(
                f"recommendations[{index}]:request_mode_mismatch:"
                f"{item.request_id}:{expected_mode}:{item.mode.value}"
            )
        if snapshot and item.evidence_snapshot_id != snapshot.snapshot_id:
            errors.append(f"recommendations[{index}]:snapshot_mismatch")
        if state:
            cash_key = (
                "cash.crypto.USDT"
                if item.asset_class == "crypto"
                else "cash.us_equity.USD"
            )
            available = state.get(cash_key)
            if available is None:
                errors.append(
                    f"recommendations[{index}]:cash_field_missing:{cash_key}"
                )
            elif float(item.deployable_cash) > float(available):
                errors.append(
                    f"recommendations[{index}]:deployable_cash_exceeds_state"
                )
        if item.research_decision.value == "preferred":
            preferred_by_mode[item.mode.value] = (
                preferred_by_mode.get(item.mode.value, 0) + 1
            )
        validated.append(item)

    for mode, count in preferred_by_mode.items():
        if count > 1:
            errors.append(f"mode:{mode}:multiple_preferred_candidates")
    no_candidate_modes = payload.get("no_qualified_candidate_modes") or []
    if not isinstance(no_candidate_modes, list):
        errors.append("no_qualified_candidate_modes:array_required")
        no_candidate_modes = []
    covered_modes = {item.mode.value for item in validated} | set(
        str(item) for item in no_candidate_modes
    )
    rejected_candidates = payload.get("rejected_candidates") or []
    if not isinstance(rejected_candidates, list):
        errors.append("rejected_candidates:array_required")
        rejected_candidates = []
    rejected_modes: set[str] = set()
    for index, rejected in enumerate(rejected_candidates):
        if not isinstance(rejected, dict):
            errors.append(f"rejected_candidates[{index}]:object_required")
            continue
        mode = str(rejected.get("request_mode") or "")
        symbol = str(rejected.get("symbol") or "")
        reasons = rejected.get("rejection_reasons")
        gaps = rejected.get("evidence_gaps")
        if mode not in request_modes.values() or not symbol:
            errors.append(
                f"rejected_candidates[{index}]:invalid_mode_or_symbol"
            )
        if rejected.get("current_direct_decision") != "do_not_enter_now":
            errors.append(
                f"rejected_candidates[{index}]:do_not_enter_now_required"
            )
        if not isinstance(reasons, list) or not reasons:
            errors.append(
                f"rejected_candidates[{index}]:rejection_reasons_required"
            )
        if not isinstance(gaps, list):
            errors.append(f"rejected_candidates[{index}]:evidence_gaps_array_required")
        if rejected.get("evidence_snapshot_id") != (
            snapshot.snapshot_id if snapshot else None
        ):
            errors.append(f"rejected_candidates[{index}]:snapshot_mismatch")
        if snapshot:
            try:
                observed = snapshot.numeric(
                    str(rejected["decision_price_evidence_id"]),
                    require_fresh=True,
                )
                decision_price = float(rejected["decision_price"])
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"rejected_candidates[{index}]:price:{exc}")
            else:
                if abs(observed - decision_price) > max(
                    1e-8, abs(observed) * 1e-8
                ):
                    errors.append(
                        f"rejected_candidates[{index}]:price_evidence_mismatch"
                    )
        forbidden_placeholders = {
            "probability_event",
            "scenarios",
            "targets",
            "price_stop",
            "event_exit_date",
        } & set(rejected)
        if forbidden_placeholders:
            errors.append(
                f"rejected_candidates[{index}]:forbidden_trade_placeholders:"
                f"{sorted(forbidden_placeholders)}"
            )
        rejected_modes.add(mode)
    for mode in no_candidate_modes:
        if str(mode) not in rejected_modes:
            errors.append(f"mode:{mode}:nearest_rejected_candidate_required")
    for request_mode in request_modes.values():
        if request_mode not in covered_modes:
            errors.append(
                f"mode:{request_mode}:candidate_or_explicit_no_candidate_required"
            )

    return {
        "status": "ok" if not errors else "failed",
        "passed": not errors,
        "errors": errors,
        "portfolio_state_id": state.state_id if state else None,
        "evidence_snapshot_id": snapshot.snapshot_id if snapshot else None,
        "request_count": len(request_modes),
        "recommendation_count": len(validated),
        "preferred_by_mode": preferred_by_mode,
        "no_qualified_candidate_modes": sorted(
            str(item) for item in no_candidate_modes
        ),
        "rejected_candidate_count": len(rejected_candidates),
    }


def render_decision_first(payload: dict[str, Any], audit: dict[str, Any]) -> str:
    lines = ["# Investment Research V3 Decision Card", ""]
    if not audit["passed"]:
        lines.extend(["**DECISION: BLOCKED — package validation failed.**", ""])
        lines.extend(f"- {item}" for item in audit["errors"])
        return "\n".join(lines) + "\n"
    for item in payload.get("recommendations") or []:
        lines.extend(
            [
                f"## {item['symbol']} · {item['request_mode']}",
                "",
                f"- Current: `{item['current_direct_decision']}`",
                f"- Research: `{item['research_decision']}`",
                f"- Execution: `{item['execution_decision']}`; deployable cash `{item['deployable_cash']}` from `{item['cash_source']}`",
                f"- Decision price: `{item['decision_price']}` as of `{item['price_as_of']}`",
                f"- Valid until: `{item['decision_valid_until']}`; review due: `{item['review_due_at']}`",
                "",
            ]
        )
    for mode in payload.get("no_qualified_candidate_modes") or []:
        lines.extend([f"## {mode}", "", "- 今日无合格标的。", ""])
    for item in payload.get("rejected_candidates") or []:
        lines.extend(
            [
                f"- Nearest rejected: `{item['symbol']}` at `{item['decision_price']}`",
                f"- Reasons: {', '.join(item['rejection_reasons'])}",
                "",
            ]
        )
    lines.extend(
        [
            "## Audit",
            "",
            f"- Portfolio state: `{audit['portfolio_state_id']}`",
            f"- Evidence snapshot: `{audit['evidence_snapshot_id']}`",
            f"- Recommendations validated: `{audit['recommendation_count']}`",
            "- Live trading: `disabled`; human confirmation required.",
        ]
    )
    return "\n".join(lines) + "\n"


def _self_test_package(*, negative: bool) -> dict[str, Any]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from v3_decision_contracts import (  # noqa: PLC0415
        CurrentDirectDecision,
        ExecutionDecision,
        LongTermDCAPlanV2,
        RecommendationV2,
        ResearchDecision,
        ScenarioV2,
    )
    from v3_evidence_snapshot import (  # noqa: PLC0415
        EvidenceSnapshotV2,
        NumericEvidenceV2,
    )
    from v3_horizon_router import RequestSpecV2  # noqa: PLC0415
    from v3_portfolio_state import (  # noqa: PLC0415
        PortfolioFieldObservationV2,
        PortfolioStateV2,
    )

    now = "2026-07-25T08:30:00+08:00"
    state = PortfolioStateV2.resolve(
        state_id="state-self-test",
        as_of=now,
        observations=(
            PortfolioFieldObservationV2(
                key="cash.crypto.USDT",
                value=0.0,
                as_of=now,
                source="self-test",
                source_kind="current_override",
                confidence=1.0,
                evidence_id="cash-self-test",
            ),
            PortfolioFieldObservationV2(
                key="cash.us_equity.USD",
                value=0.0,
                as_of=now,
                source="self-test",
                source_kind="current_override",
                confidence=1.0,
                evidence_id="cash-equity-self-test",
            ),
        ),
    )
    snapshot = EvidenceSnapshotV2.build(
        snapshot_id="snapshot-self-test",
        generated_at=now,
        cutoff_at=now,
        records=(
            NumericEvidenceV2(
                evidence_id="sol-price-self-test",
                category="spot",
                symbol="SOL",
                metric="price",
                value=188.25,
                unit="USDT",
                as_of="2026-07-25T08:29:00+08:00",
                source="frozen-self-test",
                source_role="primary",
                max_age_seconds=300,
            ),
        ),
    )
    scenarios = (
        ScenarioV2("bear", 25, "1x"),
        ScenarioV2("base", 50, "3x"),
        ScenarioV2("bull", 25, "6x"),
    )
    request = RequestSpecV2(
        request_id="request-self-test",
        mode=ResearchMode.LONGTERM_DCA,
        query="SOL 长期 DCA",
        requested_at=now,
    )
    recommendation = RecommendationV2(
        recommendation_id="recommendation-self-test",
        request_id=request.request_id,
        mode=request.mode,
        symbol="SOL",
        asset_class="crypto",
        research_decision=ResearchDecision.PREFERRED,
        current_direct_decision=CurrentDirectDecision.SMALL_ENTRY_NOW,
        execution_decision=ExecutionDecision.NO_DEPLOY_CASH,
        evidence_snapshot_id=snapshot.snapshot_id,
        decision_price=999.0 if negative else 188.25,
        decision_price_evidence_id="sol-price-self-test",
        price_as_of="2026-07-25T08:29:00+08:00",
        deployable_cash=0,
        cash_source="settled USDT only",
        execution_blockers=("no_deployable_cash",),
        decision_valid_until="2026-07-26T08:30:00+08:00",
        review_due_at="2026-10-25T08:30:00+08:00",
        created_at=now,
        execution_status="blocked",
        longterm_plan=LongTermDCAPlanV2(
            fundamental_quality_rank=1,
            raw_upside_rank=1,
            portfolio_next_dollar_rank=1,
            market_capacity="large",
            adoption="growing",
            value_capture="fees and staking",
            supply_dilution="bounded but nonzero",
            staking_net_yield_pct=5.0,
            staking_liquidity_risk="unbonding and protocol risk",
            five_year_scenarios=scenarios,
            ten_year_scenarios=scenarios,
            contribution_plan="monthly",
            quarterly_review_at="2026-10-25T08:30:00+08:00",
            annual_review_at="2027-07-25T08:30:00+08:00",
            thesis_invalidation=("adoption reverses",),
        ),
    )
    return {
        "schema_version": "investment-research-package-v3",
        "portfolio_state": state.to_dict(),
        "evidence_snapshot": snapshot.to_dict(),
        "request_specs": [request.to_dict()],
        "recommendations": [recommendation.to_dict()],
        "no_qualified_candidate_modes": [],
        "rejected_candidates": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="V3 manual research dispatch.")
    parser.add_argument("--package-json")
    parser.add_argument("--report-md")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--negative", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        payload = _self_test_package(negative=args.negative)
    elif args.package_json:
        with Path(args.package_json).expanduser().resolve().open(
            "r", encoding="utf-8"
        ) as handle:
            payload = json.load(handle)
    else:
        parser.error("--package-json or --self-test is required")
    audit = validate_package(payload)
    report = render_decision_first(payload, audit)
    if args.report_md:
        Path(args.report_md).expanduser().resolve().write_text(
            report, encoding="utf-8"
        )
    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
