#!/usr/bin/env python3
"""Read-only attribution report for closed paper trades.

This script answers why the paper ledger is winning or losing:
- groups closed trades by symbol, entry mode, strategy family and failure cause
- highlights friction drag, missed profit protection, time decay and stop losses
- writes report/experiment only; never opens, closes or mutates paper trades
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import statistics
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
LEDGER_PATH = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
REPORTS_DIR = ACTIVE_ROOT / "reports"
EXPERIMENTS_DIR = ACTIVE_ROOT / "experiments"
CHINA_TZ = dt.timezone(dt.timedelta(hours=8))


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def now_local() -> dt.datetime:
    return now_utc().astimezone(CHINA_TZ)


def rel(path: Path | None) -> str | None:
    if not path:
        return None
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path | None, default: Any = None) -> Any:
    if not path or not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_dt(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if out == out and abs(out) != float("inf") else default
    except Exception:
        return default


def pct_return(entry: float, exit_price: float) -> float:
    return (exit_price / entry - 1.0) * 100.0 if entry else 0.0


def trade_age_hours(trade: dict[str, Any]) -> float | None:
    opened = parse_dt(trade.get("opened_at"))
    closed = parse_dt(trade.get("closed_at"))
    if not opened or not closed:
        return None
    return round(max(0.0, (closed - opened).total_seconds() / 3600.0), 4)


def trade_max_float_pct(trade: dict[str, Any]) -> float:
    risk = trade.get("risk_state") if isinstance(trade.get("risk_state"), dict) else {}
    return max(
        safe_float(trade.get("max_unrealized_pnl_pct"), -999.0),
        safe_float(risk.get("highest_unrealized_pnl_pct"), -999.0),
    )


def trade_min_float_pct(trade: dict[str, Any]) -> float:
    risk = trade.get("risk_state") if isinstance(trade.get("risk_state"), dict) else {}
    return min(
        safe_float(trade.get("min_unrealized_pnl_pct"), 999.0),
        safe_float(risk.get("lowest_unrealized_pnl_pct"), 999.0),
        safe_float(trade.get("realized_pnl_pct"), 0.0),
    )


def trade_friction_usd(trade: dict[str, Any]) -> float:
    return round(
        safe_float(trade.get("entry_commission_usd"))
        + safe_float(trade.get("exit_commission_usd"))
        + safe_float(trade.get("estimated_slippage_usd"))
        + safe_float(trade.get("slippage_usd")),
        6,
    )


def trade_execution_bps(trade: dict[str, Any]) -> float:
    return max(
        safe_float(trade.get("spread_bps")),
        safe_float(trade.get("entry_spread_bps")),
        safe_float(trade.get("exit_spread_bps")),
        safe_float(trade.get("slippage_bps")),
        safe_float(trade.get("entry_slippage_bps")),
        safe_float(trade.get("exit_slippage_bps")),
    )


def trade_capture_ratio_pct(trade: dict[str, Any]) -> float | None:
    max_float = trade_max_float_pct(trade)
    realized = safe_float(trade.get("realized_pnl_pct"))
    if max_float <= 0:
        return None
    return round(realized / max_float * 100.0, 4)


def market_context_state(trade: dict[str, Any]) -> dict[str, Any]:
    context = trade.get("market_context_at_entry") if isinstance(trade.get("market_context_at_entry"), dict) else {}
    regime = trade.get("market_regime") or context.get("market_regime")
    atmosphere = trade.get("market_atmosphere") or context.get("market_atmosphere")
    short_term = trade.get("short_term_state") or context.get("short_term_state")
    sentiment = trade.get("sentiment_state") or context.get("sentiment_state")
    missing = [
        name
        for name, value in {
            "market_regime": regime,
            "market_atmosphere": atmosphere,
            "short_term_state": short_term,
            "sentiment_state": sentiment,
        }.items()
        if not value or str(value).startswith("unknown")
    ]
    return {
        "market_regime": regime,
        "market_atmosphere": atmosphere,
        "short_term_state": short_term,
        "sentiment_state": sentiment,
        "missing_context_fields": missing,
    }


def trade_diagnostic_tags(trade: dict[str, Any]) -> list[str]:
    pnl = safe_float(trade.get("realized_pnl_usd"))
    pnl_pct = safe_float(trade.get("realized_pnl_pct"))
    max_float = trade_max_float_pct(trade)
    min_float = trade_min_float_pct(trade)
    capture = trade_capture_ratio_pct(trade)
    age = trade_age_hours(trade)
    entry_mode = str(trade.get("paper_entry_mode") or "").lower()
    family = str(trade.get("strategy_family") or "").lower()
    exit_reason = str(trade.get("exit_reason") or "").lower()
    market_context = market_context_state(trade)
    risk = trade.get("risk_state") if isinstance(trade.get("risk_state"), dict) else {}
    tags: list[str] = []
    if market_context["missing_context_fields"]:
        tags.append("market_regime_context_missing")
    if pnl <= 0 and ("momentum" in entry_mode or "momentum" in family or "breakout" in family) and max_float < 1.0:
        tags.append("entry_timing_late_or_false_breakout")
    if pnl <= 0 and min_float <= -5.0 and max_float < 2.0:
        tags.append("entry_timing_too_early_drawdown_first")
    if pnl <= 0 and max_float >= 2.0 and (capture is not None and capture < 0):
        tags.append("exit_timing_gave_back_positive_mfe")
    if pnl <= 0 and ("stop" in exit_reason) and max_float <= 1.0:
        tags.append("setup_invalidated_without_positive_excursion")
    if pnl <= 0 and age is not None and age >= 24 and max_float <= 1.0:
        tags.append("capital_tied_in_low_followthrough_trade")
    if safe_float(risk.get("last_info_pressure_score"), 100.0) <= 30.0:
        tags.append("information_pressure_faded")
    if trade_execution_bps(trade) >= 10.0:
        tags.append("execution_cost_or_spread_visible")
    if safe_float(trade.get("notional_usd")) and trade_friction_usd(trade) / safe_float(trade.get("notional_usd")) * 10000.0 >= 15.0:
        tags.append("friction_drag_visible")
    if "risk_on" in str(market_context.get("market_regime") or "").lower() and pnl <= 0 and max_float <= 1.0:
        tags.append("market_regime_followthrough_misread")
    if pnl > 0 and capture is not None and capture >= 60:
        tags.append("good_profit_capture")
    return sorted(set(tags))


def classify_failure(trade: dict[str, Any], avg_notional: float) -> list[str]:
    pnl = safe_float(trade.get("realized_pnl_usd"))
    pnl_pct = safe_float(trade.get("realized_pnl_pct"))
    exit_reason = str(trade.get("exit_reason") or trade.get("outcome") or "").lower()
    entry_mode = str(trade.get("paper_entry_mode") or "").lower()
    family = str(trade.get("strategy_family") or "").lower()
    risk = trade.get("risk_state") if isinstance(trade.get("risk_state"), dict) else {}
    age = trade_age_hours(trade)
    max_float = trade_max_float_pct(trade)
    friction = trade_friction_usd(trade)
    notional = safe_float(trade.get("notional_usd"))
    categories: list[str] = []
    if pnl > 0:
        if "take" in exit_reason or "profit" in exit_reason:
            categories.append("winner_take_profit")
        elif "trail" in exit_reason:
            categories.append("winner_trailing_exit")
        else:
            categories.append("winner_other")
    else:
        if "stop" in exit_reason:
            categories.append("stop_loss_failure")
        if "time" in exit_reason or "expired" in exit_reason:
            categories.append("time_decay_failure")
        if max_float >= 2.0:
            categories.append("missed_profit_protection")
        if "momentum" in entry_mode or "momentum" in family or "breakout" in family:
            categories.append("momentum_follow_through_failed")
        if safe_float(risk.get("last_info_pressure_score"), 100.0) <= 30.0:
            categories.append("information_pressure_faded")
        if friction >= abs(pnl) * 0.25 and abs(pnl) <= 0.5:
            categories.append("friction_drag_dominated_small_trade")
        if trade_execution_bps(trade) >= 10.0:
            categories.append("execution_cost_visible")
        if avg_notional and notional > avg_notional * 1.5 and pnl_pct < -3.0:
            categories.append("oversized_loser")
        if not categories:
            categories.append("unclassified_negative_trade")
    categories.extend(trade_diagnostic_tags(trade))
    categories = sorted(set(categories))
    return categories


def group_stats(trades: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[str(trade.get(key) or "UNKNOWN")].append(trade)
    rows = []
    for name, items in groups.items():
        pnl_values = [safe_float(item.get("realized_pnl_usd")) for item in items]
        winners = [value for value in pnl_values if value > 0]
        notionals = [safe_float(item.get("notional_usd")) for item in items]
        rows.append(
            {
                key: name,
                "trade_count": len(items),
                "win_rate_pct": round(len(winners) / len(items) * 100.0, 4) if items else 0.0,
                "net_pnl_usd": round(sum(pnl_values), 6),
                "avg_pnl_usd": round(statistics.mean(pnl_values), 6) if pnl_values else 0.0,
                "notional_usd": round(sum(notionals), 6),
            }
        )
    return sorted(rows, key=lambda item: (item["net_pnl_usd"], -item["trade_count"]))


def stable_change_id(change_type: str, affected_items: list[dict[str, Any]], action: str) -> str:
    fingerprint_payload = {
        "source": "paper_trade_attribution",
        "change_type": change_type,
        "affected_items": [
            {
                "name": item.get("name"),
                "category": item.get("category"),
                "paper_entry_mode": item.get("paper_entry_mode"),
                "strategy_family": item.get("strategy_family"),
            }
            for item in affected_items
            if isinstance(item, dict)
        ],
        "proposed_action": action,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    return f"pc-attribution-{change_type}-{fingerprint}"


def attribution_proposed_change(
    stamp: str,
    index: int,
    change_type: str,
    title: str,
    rationale: str,
    affected_items: list[dict[str, Any]],
    proposed_adjustment: dict[str, Any],
    validation_plan: dict[str, Any],
    risk_controls: list[str],
) -> dict[str, Any]:
    return {
        "proposed_change_id": stable_change_id(
            change_type,
            affected_items,
            str(proposed_adjustment.get("action") or change_type),
        ),
        "proposed_change_sequence": index,
        "generated_run_stamp": stamp,
        "status": "proposed_pending_human_review",
        "scope": "paper_only_closed_trade_attribution",
        "source": "paper_trade_attribution_report",
        "change_type": change_type,
        "title": title,
        "rationale": rationale,
        "affected_items": affected_items,
        "proposed_adjustment": proposed_adjustment,
        "validation_plan": validation_plan,
        "risk_controls": risk_controls,
        "live_orders_enabled": False,
        "private_api_used": False,
        "operator_note": "This attribution proposal is paper-only. It does not mutate strategy config, open trades, or authorize live trading.",
    }


def build_attribution_proposed_changes(
    stamp: str,
    cause_counter: Counter[str],
    cause_pnl: dict[str, float],
    worst_entry_modes: list[dict[str, Any]],
    worst_families: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []

    if cause_counter.get("missed_profit_protection", 0) >= 2:
        affected = [
            {
                "name": "missed_profit_protection",
                "category": "missed_profit_protection",
                "count": cause_counter.get("missed_profit_protection", 0),
                "net_pnl_usd": round(cause_pnl.get("missed_profit_protection", 0.0), 6),
            }
        ]
        changes.append(
            attribution_proposed_change(
                stamp,
                len(changes) + 1,
                "tighten_profit_protection",
                "Tighten profit protection after early unrealized gains",
                "Recent losing paper trades reached at least +2% floating profit but closed negative, so the exit layer is giving back recoverable gains.",
                affected,
                {
                    "action": "arm_break_even_or_trailing_floor_after_2pct_float_gain",
                    "candidate_rules": [
                        "arm break-even floor once net unrealized gain exceeds +2%",
                        "tighten trailing floor when information pressure fades",
                        "keep stop-loss and time-expiry controls active",
                    ],
                },
                {
                    "forward_sample_allowed": "paper_only",
                    "minimum_new_closed_samples": 8,
                    "success_criteria": "reduce missed_profit_protection count and improve realized capture after fees/slippage",
                },
                [
                    "paper_only",
                    "no_config_mutation_without_human_confirmation",
                    "no_live_orders",
                    "do_not_widen_initial_stop_to_save_trades",
                ],
            )
        )

    if cause_counter.get("time_decay_failure", 0) >= 2:
        affected = [
            {
                "name": "time_decay_failure",
                "category": "time_decay_failure",
                "count": cause_counter.get("time_decay_failure", 0),
                "net_pnl_usd": round(cause_pnl.get("time_decay_failure", 0.0), 6),
            }
        ]
        changes.append(
            attribution_proposed_change(
                stamp,
                len(changes) + 1,
                "shorten_time_expiry",
                "Shorten expiry for stale low-pressure paper trades",
                "Multiple losing trades timed out instead of resolving quickly. Weak follow-through should stop consuming risk capacity.",
                affected,
                {
                    "action": "reduce_max_holding_window_when_info_pressure_or_volume_fades",
                    "candidate_rules": [
                        "review at half-life before full expiry",
                        "exit earlier if information pressure falls below threshold",
                        "avoid carrying stagnant micro positions through fresh scans",
                    ],
                },
                {
                    "forward_sample_allowed": "paper_only",
                    "minimum_new_closed_samples": 8,
                    "success_criteria": "lower average losing age without reducing winner capture",
                },
                [
                    "paper_only",
                    "expiry_change_requires_forward_validation",
                    "no_live_orders",
                ],
            )
        )

    if cause_counter.get("momentum_follow_through_failed", 0) >= 3:
        affected = [
            {
                "name": "momentum_follow_through_failed",
                "category": "momentum_follow_through_failed",
                "count": cause_counter.get("momentum_follow_through_failed", 0),
                "net_pnl_usd": round(cause_pnl.get("momentum_follow_through_failed", 0.0), 6),
            }
        ]
        changes.append(
            attribution_proposed_change(
                stamp,
                len(changes) + 1,
                "tighten_momentum_confirmation",
                "Require stronger follow-through before momentum entries",
                "Momentum or breakout-style paper entries are failing to continue after entry, which points to weak confirmation or late chase risk.",
                affected,
                {
                    "action": "require_volume_and_orderbook_follow_through_confirmation",
                    "candidate_rules": [
                        "require 5m/15m volume expansion to persist after trigger",
                        "require spread/depth and taker-buy support to stay healthy",
                        "downgrade late extended breakouts to watch unless retest confirms",
                    ],
                },
                {
                    "forward_sample_allowed": "paper_only",
                    "minimum_new_closed_samples": 10,
                    "success_criteria": "momentum subgroup win_rate >= 55% and net PnL positive after friction",
                },
                [
                    "paper_only",
                    "no_chasing_without_current_signal_confirmation",
                    "no_live_orders",
                ],
            )
        )

    if worst_entry_modes and worst_entry_modes[0].get("net_pnl_usd", 0) < 0:
        worst = worst_entry_modes[0]
        affected = [
            {
                "name": worst.get("paper_entry_mode"),
                "paper_entry_mode": worst.get("paper_entry_mode"),
                "trade_count": worst.get("trade_count"),
                "win_rate_pct": worst.get("win_rate_pct"),
                "net_pnl_usd": worst.get("net_pnl_usd"),
            }
        ]
        changes.append(
            attribution_proposed_change(
                stamp,
                len(changes) + 1,
                "review_negative_entry_mode",
                f"Review weak entry mode {worst.get('paper_entry_mode')}",
                "The weakest entry mode by realized PnL is still active in attribution evidence and should not receive larger paper size without a forward retest.",
                affected,
                {
                    "action": "downgrade_or_retest_entry_mode_before_new_sizing",
                    "candidate_rules": [
                        "keep at smallest paper size until subgroup improves",
                        "compare against stricter retest variant before promotion",
                        "do not increase notional from this mode while net PnL is negative",
                    ],
                },
                {
                    "forward_sample_allowed": "smallest_paper_only_after_gate_passes",
                    "minimum_new_closed_samples": 5,
                    "success_criteria": "entry mode subgroup turns net positive and avoids outsized drawdown",
                },
                [
                    "paper_only",
                    "smallest_notional_until_revalidated",
                    "no_live_orders",
                ],
            )
        )

    if worst_families and worst_families[0].get("net_pnl_usd", 0) < 0:
        worst = worst_families[0]
        affected = [
            {
                "name": worst.get("strategy_family"),
                "strategy_family": worst.get("strategy_family"),
                "trade_count": worst.get("trade_count"),
                "win_rate_pct": worst.get("win_rate_pct"),
                "net_pnl_usd": worst.get("net_pnl_usd"),
            }
        ]
        changes.append(
            attribution_proposed_change(
                stamp,
                len(changes) + 1,
                "review_negative_strategy_family",
                f"Review weak strategy family {worst.get('strategy_family')}",
                "The weakest strategy family by realized PnL is dragging the closed-trade distribution and needs a paper-only quality gate before further sampling.",
                affected,
                {
                    "action": "require_family_level_revalidation_before_capacity_expansion",
                    "candidate_rules": [
                        "route weak family through recovery optimizer or retest lab first",
                        "cap new exposure at smallest paper scout size",
                        "require positive forward evidence before larger sizing",
                    ],
                },
                {
                    "forward_sample_allowed": "paper_only_retest",
                    "minimum_new_closed_samples": 5,
                    "success_criteria": "strategy family subgroup net PnL positive after friction",
                },
                [
                    "paper_only",
                    "recovery_gate_must_still_apply",
                    "no_live_orders",
                ],
            )
        )

    return changes


def build_record(args: argparse.Namespace) -> dict[str, Any]:
    ledger = read_json(args.ledger, {})
    closed = [item for item in ledger.get("closed_trades") or [] if isinstance(item, dict)]
    cutoff = now_utc() - dt.timedelta(days=float(args.lookback_days))
    recent = []
    for trade in closed:
        closed_at = parse_dt(trade.get("closed_at") or trade.get("updated_at"))
        if closed_at is None or closed_at >= cutoff:
            recent.append(trade)
    avg_notional = statistics.mean([safe_float(item.get("notional_usd")) for item in recent]) if recent else 0.0
    attributed = []
    cause_counter: Counter[str] = Counter()
    cause_pnl: dict[str, float] = defaultdict(float)
    for trade in recent:
        categories = classify_failure(trade, avg_notional)
        context = market_context_state(trade)
        capture_ratio = trade_capture_ratio_pct(trade)
        for category in categories:
            cause_counter[category] += 1
            cause_pnl[category] += safe_float(trade.get("realized_pnl_usd"))
        attributed.append(
            {
                "paper_trade_id": trade.get("paper_trade_id"),
                "symbol": trade.get("symbol"),
                "paper_entry_mode": trade.get("paper_entry_mode"),
                "strategy_family": trade.get("strategy_family"),
                "outcome": trade.get("outcome"),
                "exit_reason": trade.get("exit_reason"),
                "realized_pnl_usd": trade.get("realized_pnl_usd"),
                "realized_pnl_pct": trade.get("realized_pnl_pct"),
                "max_unrealized_pnl_pct": trade_max_float_pct(trade),
                "min_unrealized_pnl_pct": trade_min_float_pct(trade),
                "mfe_capture_ratio_pct": capture_ratio,
                "age_hours": trade_age_hours(trade),
                "notional_usd": trade.get("notional_usd"),
                "friction_usd": trade_friction_usd(trade),
                "execution_cost_bps": trade_execution_bps(trade),
                "market_context": context,
                "diagnostic_tags": trade_diagnostic_tags(trade),
                "categories": categories,
            }
        )
    pnl_values = [safe_float(item.get("realized_pnl_usd")) for item in recent]
    winners = [value for value in pnl_values if value > 0]
    losers = [value for value in pnl_values if value <= 0]
    worst_entry_modes = group_stats(recent, "paper_entry_mode")[:6]
    worst_families = group_stats(recent, "strategy_family")[:6]
    symbol_stats = group_stats(recent, "symbol")
    symbol_stats_by_symbol = {item.get("symbol"): item for item in symbol_stats}
    market_regime_stats = group_stats(
        [
            {
                **trade,
                "market_regime_resolved": market_context_state(trade).get("market_regime") or "UNKNOWN",
            }
            for trade in recent
        ],
        "market_regime_resolved",
    )
    for item in attributed:
        symbol_row = symbol_stats_by_symbol.get(item.get("symbol")) or {}
        item["symbol_performance"] = {
            "trade_count": symbol_row.get("trade_count"),
            "win_rate_pct": symbol_row.get("win_rate_pct"),
            "net_pnl_usd": symbol_row.get("net_pnl_usd"),
        }
        if safe_float(symbol_row.get("net_pnl_usd")) < 0 and "symbol_selection_negative_cluster" not in item["categories"]:
            item["categories"].append("symbol_selection_negative_cluster")
            item["diagnostic_tags"].append("symbol_selection_negative_cluster")
            cause_counter["symbol_selection_negative_cluster"] += 1
            cause_pnl["symbol_selection_negative_cluster"] += safe_float(item.get("realized_pnl_usd"))
    missed = [item for item in attributed if "missed_profit_protection" in item["categories"]]
    capture_values = [
        item["mfe_capture_ratio_pct"]
        for item in attributed
        if item.get("mfe_capture_ratio_pct") is not None
    ]
    diagnostic_counts = Counter(tag for item in attributed for tag in item.get("diagnostic_tags") or [])
    entry_timing_issue_count = sum(
        1
        for item in attributed
        if {
            "entry_timing_late_or_false_breakout",
            "entry_timing_too_early_drawdown_first",
        }.intersection(set(item.get("diagnostic_tags") or []))
    )
    execution_cost_visible_count = sum(
        1
        for item in attributed
        if {
            "execution_cost_or_spread_visible",
            "friction_drag_visible",
        }.intersection(set(item.get("diagnostic_tags") or []))
    )
    top_causes = [
        {
            "category": category,
            "count": count,
            "net_pnl_usd": round(cause_pnl[category], 6),
        }
        for category, count in cause_counter.most_common()
    ]
    proposed_focus = []
    if cause_counter.get("missed_profit_protection", 0) >= 2:
        proposed_focus.append("tighten_profit_protection_after_2pct_float_gain")
    if cause_counter.get("time_decay_failure", 0) >= 2:
        proposed_focus.append("shorten_or_review_time_expiry_for_low_pressure_trades")
    if cause_counter.get("momentum_follow_through_failed", 0) >= 3:
        proposed_focus.append("require_stronger_follow_through_confirmation_for_momentum_entries")
    if worst_entry_modes and worst_entry_modes[0]["net_pnl_usd"] < 0:
        proposed_focus.append(f"review_entry_mode:{worst_entry_modes[0]['paper_entry_mode']}")
    created = now_local()
    generated_run_stamp = created.strftime("%Y%m%d-%H%M%S")
    proposed_changes = build_attribution_proposed_changes(
        generated_run_stamp,
        cause_counter,
        cause_pnl,
        worst_entry_modes,
        worst_families,
    )
    return {
        "run_id": f"{created.strftime('%Y%m%d-%H%M%S')}-paper-trade-attribution-report",
        "created_at": created.isoformat(),
        "scope": "paper_only_closed_trade_attribution",
        "lookback_days": args.lookback_days,
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
        "source_ledger": rel(args.ledger),
        "summary": {
            "closed_trades_total": len(closed),
            "closed_trades_in_window": len(recent),
            "wins": len(winners),
            "losses": len(losers),
            "win_rate_pct": round(len(winners) / len(recent) * 100.0, 4) if recent else 0.0,
            "net_pnl_usd": round(sum(pnl_values), 6),
            "avg_win_usd": round(statistics.mean(winners), 6) if winners else 0.0,
            "avg_loss_usd": round(statistics.mean(losers), 6) if losers else 0.0,
            "total_friction_usd": round(sum(trade_friction_usd(item) for item in recent), 6),
            "missed_profit_protection_count": len(missed),
            "avg_mfe_capture_ratio_pct": round(statistics.mean(capture_values), 4) if capture_values else None,
            "entry_timing_issue_count": entry_timing_issue_count,
            "exit_timing_issue_count": diagnostic_counts.get("exit_timing_gave_back_positive_mfe", 0),
            "market_regime_context_missing_count": diagnostic_counts.get("market_regime_context_missing", 0),
            "market_regime_misread_count": diagnostic_counts.get("market_regime_followthrough_misread", 0),
            "symbol_selection_negative_cluster_count": diagnostic_counts.get("symbol_selection_negative_cluster", 0),
            "execution_cost_visible_count": execution_cost_visible_count,
        },
        "top_failure_categories": top_causes,
        "diagnostic_tag_counts": [
            {"tag": tag, "count": count}
            for tag, count in diagnostic_counts.most_common()
        ],
        "worst_entry_modes": worst_entry_modes,
        "worst_strategy_families": worst_families,
        "symbol_attribution": symbol_stats[:8],
        "market_regime_attribution": market_regime_stats[:8],
        "recent_attributed_trades": sorted(
            attributed,
            key=lambda item: parse_dt(next((t.get("closed_at") for t in recent if t.get("paper_trade_id") == item.get("paper_trade_id")), None)) or dt.datetime.fromtimestamp(0, tz=dt.timezone.utc),
            reverse=True,
        )[: int(args.max_recent_trades)],
        "proposed_focus": proposed_focus,
        "proposed_changes": proposed_changes,
        "operator_note": "Read-only attribution. Proposed focus items are paper-only review hints, not strategy config changes or live trading authorization.",
    }


def render_report(record: dict[str, Any]) -> str:
    summary = record.get("summary") or {}
    lines = [
        f"# Paper Trade Attribution Report | {record['run_id']}",
        "",
        "- scope: `paper_only_closed_trade_attribution`",
        "- live_orders_enabled: `false`",
        "- private_api_used: `false`",
        "- ledger_mutated: `false`",
        f"- lookback_days: `{record.get('lookback_days')}`",
        "",
        "## Summary",
        "",
        f"- closed_trades_in_window: `{summary.get('closed_trades_in_window')}`",
        f"- win_rate_pct: `{summary.get('win_rate_pct')}`",
        f"- net_pnl_usd: `{summary.get('net_pnl_usd')}`",
        f"- avg_win_usd: `{summary.get('avg_win_usd')}`",
        f"- avg_loss_usd: `{summary.get('avg_loss_usd')}`",
        f"- total_friction_usd: `{summary.get('total_friction_usd')}`",
        f"- missed_profit_protection_count: `{summary.get('missed_profit_protection_count')}`",
        f"- avg_mfe_capture_ratio_pct: `{summary.get('avg_mfe_capture_ratio_pct')}`",
        f"- entry_timing_issue_count: `{summary.get('entry_timing_issue_count')}`",
        f"- exit_timing_issue_count: `{summary.get('exit_timing_issue_count')}`",
        f"- market_regime_context_missing_count: `{summary.get('market_regime_context_missing_count')}`",
        f"- market_regime_misread_count: `{summary.get('market_regime_misread_count')}`",
        f"- symbol_selection_negative_cluster_count: `{summary.get('symbol_selection_negative_cluster_count')}`",
        f"- execution_cost_visible_count: `{summary.get('execution_cost_visible_count')}`",
        "",
        "## Failure / Outcome Categories",
        "",
        "| Category | Count | Net PnL USD |",
        "|---|---:|---:|",
    ]
    for item in record.get("top_failure_categories") or []:
        lines.append(f"| `{item.get('category')}` | `{item.get('count')}` | `{item.get('net_pnl_usd')}` |")
    if not record.get("top_failure_categories"):
        lines.append("| - | - | - |")
    lines.extend(["", "## Diagnostic Tags", "", "| Tag | Count |", "|---|---:|"])
    for item in record.get("diagnostic_tag_counts") or []:
        lines.append(f"| `{item.get('tag')}` | `{item.get('count')}` |")
    if not record.get("diagnostic_tag_counts"):
        lines.append("| - | - |")
    lines.extend(["", "## Worst Entry Modes", "", "| Entry Mode | Trades | Win % | Net PnL | Notional |", "|---|---:|---:|---:|---:|"])
    for item in record.get("worst_entry_modes") or []:
        lines.append(
            f"| `{item.get('paper_entry_mode')}` | `{item.get('trade_count')}` | `{item.get('win_rate_pct')}` | `{item.get('net_pnl_usd')}` | `{item.get('notional_usd')}` |"
        )
    if not record.get("worst_entry_modes"):
        lines.append("| - | - | - | - | - |")
    lines.extend(["", "## Symbol Attribution", "", "| Symbol | Trades | Win % | Net PnL | Notional |", "|---|---:|---:|---:|---:|"])
    for item in record.get("symbol_attribution") or []:
        lines.append(
            f"| `{item.get('symbol')}` | `{item.get('trade_count')}` | `{item.get('win_rate_pct')}` | `{item.get('net_pnl_usd')}` | `{item.get('notional_usd')}` |"
        )
    if not record.get("symbol_attribution"):
        lines.append("| - | - | - | - | - |")
    lines.extend(["", "## Market Regime Attribution", "", "| Regime | Trades | Win % | Net PnL | Notional |", "|---|---:|---:|---:|---:|"])
    for item in record.get("market_regime_attribution") or []:
        lines.append(
            f"| `{item.get('market_regime_resolved')}` | `{item.get('trade_count')}` | `{item.get('win_rate_pct')}` | `{item.get('net_pnl_usd')}` | `{item.get('notional_usd')}` |"
        )
    if not record.get("market_regime_attribution"):
        lines.append("| - | - | - | - | - |")
    lines.extend(["", "## Proposed Paper-Only Focus", ""])
    for item in record.get("proposed_focus") or ["no_new_focus_generated"]:
        lines.append(f"- `{item}`")
    lines.extend(["", "## Structured Proposed Changes", "", "| ID | Type | Status | Title |", "|---|---|---|---|"])
    for item in record.get("proposed_changes") or []:
        lines.append(
            f"| `{item.get('proposed_change_id')}` | `{item.get('change_type')}` | "
            f"`{item.get('status')}` | {item.get('title')} |"
        )
    if not record.get("proposed_changes"):
        lines.append("| - | - | - | - |")
    lines.extend(
        [
            "",
            "## Recent Attributed Trades",
            "",
            "| Trade | Symbol | Mode | Exit | PnL % | Max/Min Float % | MFE Capture % | Exec bps | Context Missing | Diagnostic Tags |",
            "|---|---|---|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for item in record.get("recent_attributed_trades") or []:
        context = item.get("market_context") if isinstance(item.get("market_context"), dict) else {}
        lines.append(
            f"| `{item.get('paper_trade_id')}` | `{item.get('symbol')}` | `{item.get('paper_entry_mode')}` | "
            f"`{item.get('exit_reason')}` | `{item.get('realized_pnl_pct')}` | "
            f"`{item.get('max_unrealized_pnl_pct')}/{item.get('min_unrealized_pnl_pct')}` | "
            f"`{item.get('mfe_capture_ratio_pct')}` | `{item.get('execution_cost_bps')}` | "
            f"`{', '.join(context.get('missing_context_fields') or []) or '-'}` | "
            f"{', '.join(item.get('diagnostic_tags') or []) or '-'} |"
        )
    if not record.get("recent_attributed_trades"):
        lines.append("| - | - | - | - | - | - | - | - | - | - |")
    return "\n".join(lines) + "\n"


def seed_self_test_ledger(path: Path) -> None:
    now = now_utc()
    payload = {
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "live_orders_enabled": False,
        "private_api_used": False,
        "cash_usd": 98.0,
        "equity_usd": 98.0,
        "open_positions": [],
        "paper_orders": [],
        "closed_trades": [
            {
                "paper_trade_id": "paper-test-win",
                "symbol": "AAAUSDT",
                "status": "closed",
                "outcome": "hit",
                "exit_reason": "take_profit",
                "paper_entry_mode": "breakout_probe",
                "strategy_family": "momentum",
                "opened_at": (now - dt.timedelta(hours=8)).isoformat(),
                "closed_at": (now - dt.timedelta(hours=6)).isoformat(),
                "notional_usd": 25,
                "entry_price": 10,
                "exit_price": 11,
                "realized_pnl_usd": 2.3,
                "realized_pnl_pct": 9.2,
                "entry_commission_usd": 0.025,
                "exit_commission_usd": 0.0275,
            },
            {
                "paper_trade_id": "paper-test-missed",
                "symbol": "BBBUSDT",
                "status": "closed",
                "outcome": "failed",
                "exit_reason": "time_expired",
                "paper_entry_mode": "momentum_probe",
                "strategy_family": "momentum",
                "opened_at": (now - dt.timedelta(hours=20)).isoformat(),
                "closed_at": (now - dt.timedelta(hours=1)).isoformat(),
                "notional_usd": 75,
                "entry_price": 10,
                "exit_price": 9.9,
                "realized_pnl_usd": -1.0,
                "realized_pnl_pct": -1.333,
                "max_unrealized_pnl_pct": 3.1,
                "min_unrealized_pnl_pct": -2.0,
                "entry_commission_usd": 0.075,
                "exit_commission_usd": 0.074,
                "entry_slippage_bps": 12.0,
                "risk_state": {"last_info_pressure_score": 20},
            },
        ],
    }
    write_json(path, payload)


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="paper-trade-attribution-") as tmp:
        ledger = Path(tmp) / "ledger.json"
        seed_self_test_ledger(ledger)
        args = argparse.Namespace(ledger=ledger, lookback_days=30, max_recent_trades=10)
        record = build_record(args)
        record_again = build_record(args)
        assert record["summary"]["closed_trades_in_window"] == 2, record
        categories = {item["category"] for item in record["top_failure_categories"]}
        assert "winner_take_profit" in categories, record
        assert "missed_profit_protection" in categories, record
        assert "exit_timing_gave_back_positive_mfe" in categories, record
        assert record["summary"]["exit_timing_issue_count"] >= 1, record
        assert record["summary"]["market_regime_context_missing_count"] >= 1, record
        assert record["summary"]["execution_cost_visible_count"] >= 1, record
        recent_tags = {tag for item in record["recent_attributed_trades"] for tag in item.get("diagnostic_tags", [])}
        assert "market_regime_context_missing" in recent_tags, record
        change_ids = [item["proposed_change_id"] for item in record.get("proposed_changes") or []]
        change_ids_again = [item["proposed_change_id"] for item in record_again.get("proposed_changes") or []]
        assert change_ids and all(item.startswith("pc-attribution-") for item in change_ids), record
        assert change_ids == change_ids_again, (record, record_again)
        assert record["live_orders_enabled"] is False and record["ledger_mutated"] is False, record
    return {"status": "ok", "live_orders_enabled": False, "private_api_used": False, "ledger_mutated": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a read-only closed paper trade attribution report.")
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    parser.add_argument("--report-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument("--experiment-dir", type=Path, default=EXPERIMENTS_DIR)
    parser.add_argument("--lookback-days", type=float, default=30.0)
    parser.add_argument("--max-recent-trades", type=int, default=12)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    record = build_record(args)
    out_stamp = record["run_id"].removesuffix("-paper-trade-attribution-report")
    report_path = args.report_dir / f"{now_local().strftime('%Y-%m-%d')}-paper-trade-attribution-{out_stamp}.md"
    experiment_path = args.experiment_dir / f"{out_stamp}-paper-trade-attribution-report.json"
    record["outputs"] = {"report": rel(report_path), "experiment": rel(experiment_path)}
    write_json(experiment_path, record)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(record), encoding="utf-8")
    if args.compact_output:
        print(json.dumps({
            "status": "ok",
            "run_id": record["run_id"],
            "closed_trades_in_window": (record.get("summary") or {}).get("closed_trades_in_window"),
            "win_rate_pct": (record.get("summary") or {}).get("win_rate_pct"),
            "net_pnl_usd": (record.get("summary") or {}).get("net_pnl_usd"),
            "top_failure_categories": record.get("top_failure_categories")[:5],
            "proposed_change_count": len(record.get("proposed_changes") or []),
            "live_orders_enabled": False,
            "private_api_used": False,
            "ledger_mutated": False,
            "outputs": record["outputs"],
        }, ensure_ascii=False, indent=2))
    else:
        print(render_report(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
