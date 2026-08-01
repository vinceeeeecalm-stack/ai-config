#!/usr/bin/env python3
"""Independent cross-asset long-term next-dollar ranking.

This workflow never inherits tactical ranks or fixed BTC/ETH/SOL/ADA roles.
Every run reranks current holdings, listed companies, regular ETFs, crypto, and
cash from the supplied evidence snapshot. It is research-only.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


INVESTMENT_GOALS = {
    "capital_preservation",
    "compound_15_25",
    "aggressive_10x",
}
SCORE_FIELDS = (
    "fundamental_or_network_score",
    "moat_and_value_capture_score",
    "balance_sheet_or_supply_score",
    "valuation_score",
    "five_year_scenario_score",
    "ten_year_scenario_score",
    "survival_score",
    "drawdown_resilience_score",
    "diversification_score",
    "liquidity_score",
)
WEIGHTS = {
    "capital_preservation": {
        "fundamental_or_network_score": 0.12,
        "moat_and_value_capture_score": 0.10,
        "balance_sheet_or_supply_score": 0.13,
        "valuation_score": 0.10,
        "five_year_scenario_score": 0.05,
        "ten_year_scenario_score": 0.03,
        "survival_score": 0.20,
        "drawdown_resilience_score": 0.15,
        "diversification_score": 0.05,
        "liquidity_score": 0.07,
    },
    "compound_15_25": {
        "fundamental_or_network_score": 0.17,
        "moat_and_value_capture_score": 0.16,
        "balance_sheet_or_supply_score": 0.10,
        "valuation_score": 0.13,
        "five_year_scenario_score": 0.12,
        "ten_year_scenario_score": 0.10,
        "survival_score": 0.08,
        "drawdown_resilience_score": 0.05,
        "diversification_score": 0.05,
        "liquidity_score": 0.04,
    },
    "aggressive_10x": {
        "fundamental_or_network_score": 0.14,
        "moat_and_value_capture_score": 0.16,
        "balance_sheet_or_supply_score": 0.08,
        "valuation_score": 0.11,
        "five_year_scenario_score": 0.18,
        "ten_year_scenario_score": 0.15,
        "survival_score": 0.06,
        "drawdown_resilience_score": 0.03,
        "diversification_score": 0.05,
        "liquidity_score": 0.04,
    },
}


@dataclass(frozen=True)
class LongTermCandidateV1:
    symbol: str
    asset_class: str
    is_current_holding: bool
    fundamental_or_network_score: float
    moat_and_value_capture_score: float
    balance_sheet_or_supply_score: float
    valuation_score: float
    five_year_scenario_score: float
    ten_year_scenario_score: float
    survival_score: float
    drawdown_resilience_score: float
    diversification_score: float
    liquidity_score: float
    data_quality_status: str
    evidence_ids: tuple[str, ...]
    thesis_summary: str
    five_year_scenarios: dict[str, Any]
    ten_year_scenarios: dict[str, Any]
    correlation_overlap: str
    hard_blockers: tuple[str, ...] = ()

    def validation_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.symbol or not self.asset_class:
            errors.append("identity_required")
        for name in SCORE_FIELDS:
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 100
            ):
                errors.append(f"{name}:range_0_100_required")
        if self.data_quality_status not in {
            "verified",
            "degraded",
            "disputed",
            "stale",
            "missing",
        }:
            errors.append("data_quality_status:unsupported")
        if not self.evidence_ids:
            errors.append("evidence_ids_required")
        if not self.thesis_summary or not self.correlation_overlap:
            errors.append("thesis_and_correlation_required")
        if not self.five_year_scenarios or not self.ten_year_scenarios:
            errors.append("five_and_ten_year_scenarios_required")
        return errors


def candidate_from_payload(payload: dict[str, Any]) -> LongTermCandidateV1:
    try:
        item = LongTermCandidateV1(
            symbol=str(payload["symbol"]),
            asset_class=str(payload["asset_class"]),
            is_current_holding=bool(payload.get("is_current_holding", False)),
            fundamental_or_network_score=payload["fundamental_or_network_score"],
            moat_and_value_capture_score=payload["moat_and_value_capture_score"],
            balance_sheet_or_supply_score=payload["balance_sheet_or_supply_score"],
            valuation_score=payload["valuation_score"],
            five_year_scenario_score=payload["five_year_scenario_score"],
            ten_year_scenario_score=payload["ten_year_scenario_score"],
            survival_score=payload["survival_score"],
            drawdown_resilience_score=payload["drawdown_resilience_score"],
            diversification_score=payload["diversification_score"],
            liquidity_score=payload["liquidity_score"],
            data_quality_status=str(payload["data_quality_status"]),
            evidence_ids=tuple(payload.get("evidence_ids") or ()),
            thesis_summary=str(payload["thesis_summary"]),
            five_year_scenarios=dict(payload["five_year_scenarios"]),
            ten_year_scenarios=dict(payload["ten_year_scenarios"]),
            correlation_overlap=str(payload["correlation_overlap"]),
            hard_blockers=tuple(payload.get("hard_blockers") or ()),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid_longterm_candidate:{exc}") from exc
    errors = item.validation_errors()
    if errors:
        raise ValueError(";".join(errors))
    return item


def _cash_candidate(market_regime: str) -> LongTermCandidateV1:
    risk_off = market_regime in {"risk_off", "crisis", "liquidity_contraction"}
    defensive = 96 if risk_off else 74
    optionality = 86 if risk_off else 42
    return LongTermCandidateV1(
        symbol="CASH",
        asset_class="cash",
        is_current_holding=True,
        fundamental_or_network_score=45,
        moat_and_value_capture_score=35,
        balance_sheet_or_supply_score=defensive,
        valuation_score=optionality,
        five_year_scenario_score=30,
        ten_year_scenario_score=20,
        survival_score=99,
        drawdown_resilience_score=99,
        diversification_score=80,
        liquidity_score=100,
        data_quality_status="verified",
        evidence_ids=(f"market-regime:{market_regime}",),
        thesis_summary="Preserve optionality until valuation, liquidity, or risk regime improves.",
        five_year_scenarios={"bear": "purchasing-power erosion", "base": "dry powder", "bull": "redeployed after trigger"},
        ten_year_scenarios={"bear": "inflation drag", "base": "stability", "bull": "future redeployment"},
        correlation_overlap="low market beta while held as cash",
    )


def _score(item: LongTermCandidateV1, goal: str, market_regime: str) -> float:
    score = sum(getattr(item, field) * weight for field, weight in WEIGHTS[goal].items())
    if item.data_quality_status == "degraded":
        score -= 8
    elif item.data_quality_status in {"disputed", "stale", "missing"}:
        score -= 30
    if item.hard_blockers:
        score -= 40
    if market_regime in {"risk_off", "crisis", "liquidity_contraction"}:
        score += (item.survival_score + item.liquidity_score) / 20
        score -= (100 - item.drawdown_resilience_score) / 10
    return round(max(0.0, min(100.0, score)), 4)


def rank_longterm_next_dollar(
    candidates: Iterable[LongTermCandidateV1 | dict[str, Any]],
    *,
    investment_goal: str,
    market_regime: str,
    deployable_cash: float,
    planned_amount: float,
) -> dict[str, Any]:
    if investment_goal not in INVESTMENT_GOALS:
        raise ValueError(f"unsupported_investment_goal:{investment_goal}")
    if deployable_cash < 0 or planned_amount < 0:
        raise ValueError("cash_and_planned_amount_must_be_non_negative")
    accepted: list[LongTermCandidateV1] = []
    rejected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in candidates:
        try:
            item = raw if isinstance(raw, LongTermCandidateV1) else candidate_from_payload(raw)
        except (TypeError, ValueError) as exc:
            rejected.append(
                {
                    "symbol": raw.get("symbol") if isinstance(raw, dict) else None,
                    "reason": f"invalid_candidate:{exc}",
                }
            )
            continue
        key = (item.symbol.upper(), item.asset_class)
        if key in seen:
            rejected.append({"symbol": item.symbol, "reason": "duplicate_candidate"})
            continue
        seen.add(key)
        if item.data_quality_status in {"disputed", "stale", "missing"} or item.hard_blockers:
            rejected.append(
                {
                    "symbol": item.symbol,
                    "reason": ",".join(item.hard_blockers)
                    if item.hard_blockers
                    else f"data_quality_{item.data_quality_status}",
                }
            )
            continue
        accepted.append(item)
    accepted.append(_cash_candidate(market_regime))
    ranked = sorted(
        (
            {
                "candidate": item,
                "score": _score(item, investment_goal, market_regime),
            }
            for item in accepted
        ),
        key=lambda row: (row["score"], row["candidate"].symbol),
        reverse=True,
    )
    winner_row = ranked[0]
    winner = winner_row["candidate"]
    direct_decision = (
        "do_not_enter_now"
        if winner.asset_class == "cash" or winner_row["score"] < 60
        else "small_entry_now"
    )
    executable_amount = (
        min(deployable_cash, planned_amount)
        if direct_decision != "do_not_enter_now" and deployable_cash > 0
        else 0.0
    )
    execution_decision = (
        "manual_execute_candidate"
        if executable_amount > 0
        else ("no_deploy_cash" if deployable_cash == 0 else "no_action")
    )
    runner_rejections = [
        {
            "symbol": row["candidate"].symbol,
            "asset_class": row["candidate"].asset_class,
            "score": row["score"],
            "rejection_reason": (
                f"lower marginal contribution than {winner.symbol}; "
                f"correlation={row['candidate'].correlation_overlap}"
            ),
        }
        for row in ranked[1:3]
    ]
    return {
        "schema_version": "longterm-next-dollar-ranking-v1",
        "investment_goal": investment_goal,
        "market_regime": market_regime,
        "best_candidate": winner.symbol,
        "research_decision": "preferred",
        "current_direct_decision": direct_decision,
        "account_state": {
            "deployable_cash": deployable_cash,
            "planned_amount": planned_amount,
        },
        "execution_decision": execution_decision,
        "executable_amount": executable_amount,
        "winner": {
            **asdict(winner),
            "portfolio_next_dollar_rank": 1,
            "marginal_contribution_score": winner_row["score"],
            "score_weights": WEIGHTS[investment_goal],
        },
        "runners_up_rejections": runner_rejections,
        "hard_rejections": rejected,
        "candidate_pool": [
            {
                "symbol": row["candidate"].symbol,
                "asset_class": row["candidate"].asset_class,
                "score": row["score"],
                "is_current_holding": row["candidate"].is_current_holding,
            }
            for row in ranked
        ],
        "fixed_asset_roles_used": False,
        "tactical_rank_used": False,
        "human_confirmation_required": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def _fixture(symbol: str, asset_class: str, base: float) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "asset_class": asset_class,
        "is_current_holding": symbol in {"ETH", "QQQ"},
        **{field: min(99.0, base + index) for index, field in enumerate(SCORE_FIELDS)},
        "data_quality_status": "verified",
        "evidence_ids": [f"fixture:{symbol}"],
        "thesis_summary": f"Deterministic thesis for {symbol}",
        "five_year_scenarios": {"bear": 0.5, "base": 2, "bull": 5},
        "ten_year_scenarios": {"bear": 0.7, "base": 3, "bull": 8},
        "correlation_overlap": "measured against current portfolio",
    }


def self_test() -> dict[str, Any]:
    pool = [_fixture("QQQ", "etf", 70), _fixture("SOL", "crypto", 76), _fixture("MSFT", "us_equity", 74)]
    risk_on = rank_longterm_next_dollar(
        pool,
        investment_goal="aggressive_10x",
        market_regime="risk_on",
        deployable_cash=0,
        planned_amount=1000,
    )
    risk_off = rank_longterm_next_dollar(
        pool,
        investment_goal="capital_preservation",
        market_regime="risk_off",
        deployable_cash=1000,
        planned_amount=1000,
    )
    checks = {
        "zero_cash_preserves_research_winner": risk_on["best_candidate"] != "CASH" and risk_on["executable_amount"] == 0,
        "risk_off_can_select_cash": risk_off["best_candidate"] == "CASH",
        "only_one_winner": risk_on["winner"]["portfolio_next_dollar_rank"] == 1,
        "at_most_two_runner_rejections": len(risk_on["runners_up_rejections"]) <= 2,
        "no_fixed_asset_roles": risk_on["fixed_asset_roles_used"] is False,
        "live_orders_disabled": risk_on["live_orders_enabled"] is False,
    }
    return {"status": "ok" if all(checks.values()) else "failed", "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        payload = self_test()
    else:
        if not args.input:
            parser.error("--input is required unless --self-test is used")
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        payload = rank_longterm_next_dollar(
            raw.get("candidates") or [],
            investment_goal=str(raw["investment_goal"]),
            market_regime=str(raw["market_regime"]),
            deployable_cash=float(raw.get("deployable_cash") or 0),
            planned_amount=float(raw.get("planned_amount") or 0),
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status", "ok") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
