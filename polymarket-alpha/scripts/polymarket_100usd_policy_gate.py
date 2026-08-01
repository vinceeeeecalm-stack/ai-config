#!/usr/bin/env python3
"""Classify a $100 sports-paper candidate into win-rate or value lanes."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config/sports_100usd_policy.json"
REQUIRED_GATES = ("contract", "sources", "probability", "price", "liquidity", "execution")


def tick_below(value: float, tick: float) -> float:
    return round(math.floor((value - 1e-9) / tick) * tick, 6)


def evaluate(policy: dict[str, Any], p_exec: float, ask_cents: float,
             friction: dict[str, float], gates: dict[str, bool], tick_cents: float = 0.1,
             research_probability_lower_bound: float | None = None,
             research_probability_upper_bound: float | None = None) -> dict[str, Any]:
    friction_cents = sum(float(friction.get(key, 0.0)) for key in ("fees", "spread", "slippage", "impact"))
    net_ev_cents = 100.0 * p_exec - ask_cents - friction_cents
    failed_gates = [gate for gate in REQUIRED_GATES if gates.get(gate) is not True]
    high = policy["lanes"]["high_confidence"]
    value = policy["lanes"]["value"]
    exploration = policy["lanes"].get("exploration_micro", {})
    lane = "PASS"
    reason = "probability_or_net_ev_threshold_not_met"
    stake = 0.0
    if failed_gates:
        reason = "six_gate_failure"
    elif p_exec >= float(high["minimum_calibrated_p_exec"]):
        probability_floor = high.get("minimum_research_probability_lower_bound")
        if research_probability_lower_bound is None:
            reason = "high_confidence_research_lower_bound_missing"
        elif research_probability_lower_bound < float(probability_floor):
            reason = "high_confidence_research_lower_bound_below_80"
        elif net_ev_cents >= float(high["minimum_net_ev_cents"]):
            lane, reason, stake = "HIGH_CONFIDENCE", "probability_floor_at_least_80_and_net_ev_at_least_3c", float(high["normal_stake_usd"])
        else:
            reason = "high_probability_net_ev_below_3c"
    elif (p_exec >= float(value["minimum_calibrated_p_exec"])
          and p_exec < float(value["maximum_calibrated_p_exec_exclusive"])):
        if net_ev_cents >= float(value["minimum_net_ev_cents"]):
            lane, reason, stake = "VALUE", "p_exec_at_least_60_and_net_ev_at_least_5c", float(value["normal_stake_usd"][0])
        else:
            reason = "value_lane_net_ev_below_5c"
    formal_reason = reason
    if (lane == "PASS" and not failed_gates and exploration.get("enabled") is True):
        probability_width = None
        if research_probability_lower_bound is not None and research_probability_upper_bound is not None:
            probability_width = research_probability_upper_bound - research_probability_lower_bound
        if research_probability_lower_bound is None or research_probability_upper_bound is None:
            reason = "exploration_probability_interval_missing"
        elif p_exec < float(exploration["minimum_calibrated_p_exec"]):
            reason = "exploration_p_exec_below_70"
        elif research_probability_lower_bound < float(exploration["minimum_research_probability_lower_bound"]):
            reason = "exploration_research_lower_bound_below_65"
        elif probability_width > float(exploration["maximum_probability_interval_width"]):
            reason = "exploration_probability_interval_too_wide"
        elif ask_cents > float(exploration["maximum_ask_cents"]):
            reason = "exploration_ask_above_90c"
        elif net_ev_cents < float(exploration["minimum_net_ev_cents"]):
            reason = "exploration_net_ev_below_minus_4c"
        else:
            lane = "EXPLORATION_MICRO"
            reason = "pilot_micro_entry_for_prospective_calibration"
            stake = float(exploration["stake_usd"])
    max_ask_high = tick_below(100.0 * p_exec - friction_cents - float(high["minimum_net_ev_cents"]), tick_cents)
    max_ask_value = tick_below(100.0 * p_exec - friction_cents - float(value["minimum_net_ev_cents"]), tick_cents)
    return {
        "action": ("PAPER_BUY_MICRO" if lane == "EXPLORATION_MICRO"
                   else "PAPER_BUY" if lane != "PASS" else "WAIT_PASS"),
        "lane": lane,
        "reason": reason,
        "formal_lane_rejection_reason": formal_reason if lane == "EXPLORATION_MICRO" else None,
        "calibrated_p_exec": p_exec,
        "research_probability_lower_bound": research_probability_lower_bound,
        "research_probability_upper_bound": research_probability_upper_bound,
        "actual_ask_cents": ask_cents,
        "friction_breakdown_cents": friction,
        "total_friction_cents": round(friction_cents, 6),
        "net_ev_cents": round(net_ev_cents, 6),
        "failed_gates": failed_gates,
        "max_ask_high_confidence_cents": max_ask_high,
        "max_ask_high_confidence_cents_strictly_below": max_ask_high,
        "max_ask_value_cents": max_ask_value,
        "stake_usd": stake,
        "counts_as_formal_recommendation": lane != "EXPLORATION_MICRO" and lane != "PASS",
        "counts_as_real_money": False,
        "paper_only": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "real_money_execution_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--p-exec", type=float, required=True)
    parser.add_argument("--research-lower-bound", type=float)
    parser.add_argument("--research-upper-bound", type=float)
    parser.add_argument("--ask-cents", type=float, required=True)
    parser.add_argument("--friction-json", required=True)
    parser.add_argument("--gates-json", required=True)
    parser.add_argument("--tick-cents", type=float, default=0.1)
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    result = evaluate(policy, args.p_exec, args.ask_cents, json.loads(args.friction_json),
                      json.loads(args.gates_json), args.tick_cents, args.research_lower_bound,
                      args.research_upper_bound)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
