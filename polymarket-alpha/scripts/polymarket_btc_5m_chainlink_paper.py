#!/usr/bin/env python3
"""Forward-only BTC 5-minute Chainlink contract paper-validation pipeline.

This adapter reuses the public CLOB fill and isolated-ledger mechanics of the
hourly pipeline, while changing the contract identity, labels, checkpoints and
settlement evidence to match Polymarket's current BTC 5m Chainlink series.
It never places live orders or treats a model score as a high-win-rate claim.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import polymarket_btc_hourly_paper as core

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "btc_5m_chainlink_paper_policy.json"
DATA_DIR = ROOT / "data" / "btc_5m_chainlink_paper"
REPORT_DIR = ROOT / "reports" / "btc_5m_chainlink_paper"
SAFE = core.SAFE


def load_policy() -> dict[str, Any]:
    policy = core.read_json(POLICY_PATH)
    if not isinstance(policy, dict) or any(policy.get(key) is not value for key, value in SAFE.items()):
        raise ValueError("unsafe or invalid BTC 5m Chainlink policy")
    if policy.get("contract_window_minutes") != 5 or policy.get("contract_resolution") != "chainlink_btcusd_terminal":
        raise ValueError("unexpected BTC 5m Chainlink contract policy")
    return policy


def chainlink_rules_clear(market: dict[str, Any], start: datetime, end: datetime) -> tuple[bool, bool, list[str]]:
    """Require the exact public contract family rather than guessing from a slug."""
    text = " ".join(str(market.get(key) or "") for key in ("question", "description", "resolutionSource", "resolutionDescription")).lower()
    failures = []
    if "chainlink" not in text or not re.search(r"btc\s*/\s*usd|bitcoin", text):
        failures.append("chainlink_btcusd_rule_not_proven")
    if not market.get("resolutionSource"):
        failures.append("resolution_source_missing")
    if abs((end - start).total_seconds() - 300) > 30:
        failures.append("not_five_minute_contract")
    up_tie = bool(re.search(r"(?:greater than or equal|greater than or equal to|>=|at or above)", text))
    down_tie = bool(re.search(r"(?:less than or equal|<=|down\s+(?:if|when).{0,80}(?:equal|<=))", text))
    if not up_tie or down_tie:
        failures.append("tie_rule_ambiguous")
    return not failures, up_tie, failures


def forward_samples(base: Path) -> list[dict[str, Any]]:
    """Build labels only from previously captured public terminal outcomes.

    Binance data supplies ex-ante features.  The label is the terminal outcome
    of the matching Polymarket Chainlink contract, never a recreated Binance
    five-minute return.  This prevents a source-mismatch from being presented
    as model validation.
    """
    state = core.read_json(base / "state.json", {}) or {}
    outcomes = state.get("hourly_coverage", {}) if isinstance(state, dict) else {}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for path in sorted((base / "snapshots").glob("*.json")):
        snapshot = core.read_json(path, {}) or {}
        market = snapshot.get("market", {}) if isinstance(snapshot.get("market"), dict) else {}
        condition_id = str(market.get("conditionId") or "")
        checkpoint = snapshot.get("checkpoint_minute")
        feature_row = snapshot.get("feature_row")
        resolution = (outcomes.get(condition_id) or {}).get("resolution")
        if not (condition_id and isinstance(checkpoint, int) and isinstance(feature_row, dict)
                and snapshot.get("rules_clear") and resolution in {"UP", "DOWN"}):
            continue
        key = (condition_id, checkpoint)
        if key in seen:
            continue
        seen.add(key)
        captured_at = str(snapshot.get("captured_at") or "")
        rows.append({"captured_at": captured_at, "features": {str(checkpoint): feature_row},
                     "label": int(resolution == "UP"), "condition_id": condition_id,
                     "label_source": "polymarket_gamma_terminal_outcome"})
    return sorted(rows, key=lambda row: (row["captured_at"], row["condition_id"]))


def collect_forward_history(base: Path, policy: dict[str, Any], at: datetime, **_: Any) -> dict[str, Any]:
    rows = forward_samples(base)
    return {
        "complete": len(rows) >= int(policy["min_training_samples"]),
        "forward_resolved_samples": len(rows),
        "minimum_training_samples": int(policy["min_training_samples"]),
        "updated_at": core.iso(at),
        "label_source": "polymarket_gamma_terminal_outcome",
        "feature_source": "binance_public_closed_klines",
        **SAFE,
    }


def train_forward_history(base: Path, policy: dict[str, Any]) -> dict[str, Any]:
    rows = forward_samples(base)
    if len(rows) < int(policy["min_training_samples"]):
        return {"status": "forward_history_not_ready", "samples": len(rows), **SAFE}
    model = core.train_from_rows(rows, policy)
    model.update({
        "schema_version": "btc-5m-chainlink-model-v1",
        "model_version": "btc-5m-chainlink-l2-logistic-v1",
        "training_samples": len(rows),
        "training_label_source": "polymarket_gamma_terminal_outcome",
        "feature_source": "binance_public_closed_klines",
    })
    core.write_json(base / "model.json", model)
    return {"status": "trained", "samples": len(rows), "approved": model["approved"], **SAFE}


def phase_chainlink(state: dict[str, Any], model: dict[str, Any] | None, at: datetime,
                    policy: dict[str, Any]) -> str:
    started = core.parse_time(state.get("shadow_started_at")) or at
    resolved = [item for item in state.get("hourly_coverage", {}).values() if item.get("resolved")]
    expected = len(policy["shadow_checkpoint_minutes"]) * len(resolved)
    captured = sum(len(item.get("checkpoints", [])) for item in resolved)
    coverage = captured / expected if expected else 0.0
    if ((at - started).total_seconds() < int(policy["shadow_hours"]) * 3600
            or len(resolved) < int(policy["shadow_min_complete_hours"])
            or coverage < float(policy["shadow_min_checkpoint_coverage"])):
        return "shadow"
    return "paper" if model and model.get("approved") else "shadow_model_unapproved"


def settle_chainlink_positions(ledger: dict[str, Any], at: datetime,
                               fetch: Callable[[str], Any] = core.public_json) -> list[dict[str, Any]]:
    """Settle only on the public, terminal Polymarket outcome for this contract.

    The public Chainlink stream page does not expose an equivalent historical
    candle API.  We record that limitation explicitly instead of substituting
    a Binance price as settlement evidence.
    """
    results = []
    for position in list(ledger.get("open_positions", [])):
        try:
            rows = fetch(f"{core.GAMMA}/markets?condition_ids={position['condition_id']}&limit=1")
            market = rows[0] if isinstance(rows, list) and rows else None
            winner = core.terminal_outcome(market or {})
            if not winner:
                continue
            won = position["side"] == winner
            payout = float(position["shares"]) if won else 0.0
            ledger["accounts"][position["account"]]["cash_usd"] += payout
            closed = {
                **position,
                "status": "closed",
                "closed_at": core.iso(at),
                "settlement_status": "settled",
                "resolution": winner,
                "payout_usd": payout,
                "net_pnl_usd": payout - float(position["total_entry_cost"]),
                "trade_roi": (payout - float(position["total_entry_cost"])) / float(position["total_entry_cost"]),
                "settlement_evidence": {
                    "source": "polymarket_gamma_terminal_outcome",
                    "resolution_source": (market or {}).get("resolutionSource"),
                    "condition_id": position["condition_id"],
                    "independent_chainlink_price_check": "unavailable_not_substituted",
                },
            }
            ledger["open_positions"].remove(position)
            ledger["closed_positions"].append(closed)
            ledger["paper_orders"].append({
                "paper_order_id": position["paper_trade_id"] + "-SETTLE",
                "paper_trade_id": position["paper_trade_id"], "type": "RESOLUTION", "status": "FILLED",
                "average_fill_price": 1.0 if won else 0.0, "executed_shares": position["shares"],
                "notional_usd": payout, "commission_usd": 0.0, **SAFE,
            })
            results.append(closed)
        except Exception as exc:
            results.append({"paper_trade_id": position["paper_trade_id"],
                            "status": "settlement_fetch_failed", "error": type(exc).__name__})
    core.update_equity(ledger)
    return results


def configure_core() -> dict[str, Any]:
    policy = load_policy()
    core.REPORT_DIR = REPORT_DIR
    core.load_policy = lambda path=None: policy
    core.rules_clear = chainlink_rules_clear
    core.collect_history = collect_forward_history
    core.train_history = train_forward_history
    core.phase = phase_chainlink
    core.settle_open = settle_chainlink_positions
    return policy


def report(result: dict[str, Any], base: Path) -> None:
    audit = result.get("audit", {})
    readiness = audit.get("recommendation_readiness", {})
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# BTC 5m Chainlink Polymarket Paper",
        "",
        f"- Time: {result.get('created_at')}",
        f"- Phase: `{result.get('phase')}`",
        f"- Action: `{result.get('action')}`",
        f"- Forward resolved samples: {result.get('history', {}).get('forward_resolved_samples', 0)}",
        f"- Paper trades settled: {audit.get('closed_trades', 0)}",
        f"- Recommendation status: `{readiness.get('status', 'paper_only')}`",
        "- Settlement evidence: `Polymarket terminal outcome; unavailable Chainlink numerical check is never replaced with Binance`",
        "- Live orders: `false`",
        "",
        "## Recommendation blockers",
        "",
        *[f"- {item}" for item in readiness.get("blockers", [])],
        "",
    ]
    (REPORT_DIR / "LATEST_BTC_5M_CHAINLINK_PAPER.md").write_text("\n".join(lines), encoding="utf-8")


def cycle(base: Path = DATA_DIR) -> dict[str, Any]:
    configure_core()
    result = core.cycle(base)
    result["strategy_id"] = "btc-5m-chainlink"
    result["contract_resolution"] = "chainlink_btcusd_terminal"
    result["settlement_limit"] = "gamma_terminal_only_when_public_chainlink_price_history_is_unavailable"
    core.write_json(base / "latest-cycle.json", result)
    report(result, base)
    return result


def preflight(base: Path = DATA_DIR) -> dict[str, Any]:
    configure_core()
    result = core.preflight(base)
    local = self_test()
    if local["status"] != "pass":
        result["status"] = "blocked"
    result["chainlink_contract_test"] = local["status"]
    return result


def self_test() -> dict[str, Any]:
    start = datetime(2026, 7, 26, 10, tzinfo=timezone.utc)
    fixture = {
        "description": "Resolves using Chainlink BTC/USD. Up is greater than or equal to the opening price.",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
    }
    clear, tie_up, failures = chainlink_rules_clear(fixture, start, start.replace(minute=5))
    assert clear and tie_up and not failures
    _, _, failures = chainlink_rules_clear(fixture, start, start.replace(minute=10))
    assert "not_five_minute_contract" in failures
    assert core.checkpoint_for_market(start.replace(minute=2), start, start.replace(minute=5), {"checkpoint_minutes": [2]}) == 2
    return {"status": "pass", "tests": ["chainlink_contract_identity", "five_minute_duration", "checkpoint_alignment"], **SAFE}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("command", choices=("cycle", "self-test", "train", "audit", "preflight"))
    args = parser.parse_args()
    policy = configure_core()
    if args.command == "self-test":
        result = self_test()
    elif args.command == "preflight":
        result = preflight(args.data_dir)
    elif args.command == "train":
        result = train_forward_history(args.data_dir, policy)
    elif args.command == "audit":
        result = core.audit_ledger(core.load_ledger(args.data_dir / "ledger.json", policy),
                                   core.read_json(args.data_dir / "state.json", {}),
                                   core.read_json(args.data_dir / "model.json"), policy)
    else:
        result = cycle(args.data_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"ok", "pass", "SKIPPED_LOCKED", "degraded", "trained", "forward_history_not_ready"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
