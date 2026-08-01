#!/usr/bin/env python3
"""Aggregate paper, walk-forward, and recommendation validation samples.

This script is read-only. It does not open, close, or mutate paper trades.
The purpose is to make the evidence gap visible before any strategy can be
promoted from research/paper into a human-confirmed live candidate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ACTIVE_ROOT = ROOT / "active-alpha-paper-monitor"
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
DEFAULT_PAPER_DIR = ACTIVE_ROOT / "paper_trades"
DEFAULT_EXPERIMENT_DIR = ACTIVE_ROOT / "experiments"
DEFAULT_RECOMMENDATION_LEDGER = MANUAL_ROOT / "recommendations" / "recommendation_history.json"
LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))

DEFAULT_THRESHOLDS = {
    "min_closed_paper_trades": 30,
    "min_paper_win_rate_pct": 55.0,
    "min_paper_net_return_pct": 5.0,
    "min_recovery_probe_closed_count": 3,
    "min_recovery_probe_net_return_pct": 2.0,
    "max_paper_drawdown_pct": -15.0,
    "min_recommendation_outcome_reviews": 10,
    "min_calibration_resolved": 10,
    "min_walkforward_frames": 10,
    "min_target_research_pass": 1,
    "min_paper_only_candidates": 1,
}

HIT_OUTCOMES = {"hit", "take_profit", "profit", "target_hit", "trailing_profit_protection"}
FAILED_OUTCOMES = {"failed", "stop", "stop_loss", "expired", "invalidated"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_time(value: Any) -> dt.datetime:
    if not value:
        return dt.datetime.fromtimestamp(0, tz=dt.timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return dt.datetime.fromtimestamp(0, tz=dt.timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def local_month_id(value: dt.datetime) -> str:
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m")


def monthly_goal_state_from_ledger(ledger: dict[str, Any], now: dt.datetime | None = None) -> dict[str, Any]:
    """Return the active monthly doubling target.

    The paper objective compounds by month: each new month should target 2x the
    month-start paper equity, not always 2x the original $500 seed. Existing
    ledgers may not have an explicit baseline, so this function reads an
    explicit `monthly_goal_baselines` field when present, then falls back to
    the first ledger event in the current month, then to the original seed.
    """
    now_dt = now or dt.datetime.now(dt.timezone.utc)
    month_id = local_month_id(now_dt)
    initial = as_float(ledger.get("initial_capital_usd"), 500.0) or 500.0
    equity = as_float(ledger.get("equity_usd"), initial) or initial

    baseline: float | None = None
    baseline_source = "missing"
    baseline_created_at: str | None = None
    baselines = ledger.get("monthly_goal_baselines")
    if isinstance(baselines, dict):
        record = baselines.get(month_id)
        if isinstance(record, dict):
            baseline = (
                as_float(record.get("month_start_equity_usd"))
                or as_float(record.get("baseline_equity_usd"))
                or as_float(record.get("initial_capital_usd"))
            )
            baseline_created_at = record.get("month_started_at") or record.get("created_at")
        else:
            baseline = as_float(record)
        if baseline is not None:
            baseline_source = "ledger_monthly_goal_baselines"
    elif isinstance(baselines, list):
        for record in baselines:
            if not isinstance(record, dict) or str(record.get("month_id")) != month_id:
                continue
            baseline = (
                as_float(record.get("month_start_equity_usd"))
                or as_float(record.get("baseline_equity_usd"))
                or as_float(record.get("initial_capital_usd"))
            )
            baseline_created_at = record.get("month_started_at") or record.get("created_at")
            if baseline is not None:
                baseline_source = "ledger_monthly_goal_baselines"
                break

    if baseline is None:
        month_events: list[tuple[dt.datetime, float, dict[str, Any]]] = []
        for event in ledger.get("events") or []:
            if not isinstance(event, dict):
                continue
            event_time = parse_time(event.get("created_at") or event.get("updated_at"))
            if local_month_id(event_time) != month_id:
                continue
            event_equity = as_float(event.get("equity_usd"))
            if event_equity is not None:
                month_events.append((event_time, event_equity, event))
        if month_events:
            month_events.sort(key=lambda item: item[0])
            first_time, first_equity, _ = month_events[0]
            baseline = first_equity
            baseline_created_at = first_time.isoformat()
            baseline_source = "first_ledger_event_in_month"

    if baseline is None:
        created_at = parse_time(ledger.get("created_at"))
        if local_month_id(created_at) == month_id:
            baseline = initial
            baseline_created_at = ledger.get("created_at")
            baseline_source = "ledger_initial_capital_current_month"
        else:
            baseline = equity
            baseline_created_at = now_dt.isoformat()
            baseline_source = "current_equity_fallback_no_month_baseline"

    target = baseline * 2.0
    denominator = max(target - baseline, 1e-9)
    target_gap = max(0.0, target - equity)
    required_return_pct = ((target / equity - 1.0) * 100.0) if equity > 0 else None
    progress = max(0.0, min(100.0, (equity - baseline) / denominator * 100.0))
    return {
        "target_model": "monthly_compounding_double",
        "month_id": month_id,
        "baseline_source": baseline_source,
        "baseline_created_at": baseline_created_at,
        "lifetime_initial_capital_usd": round(initial, 6),
        "initial_capital_usd": round(baseline, 6),
        "month_start_equity_usd": round(baseline, 6),
        "target_equity_usd": round(target, 6),
        "current_equity_usd": round(equity, 6),
        "target_gap_usd": round(target_gap, 6),
        "progress_pct": round(progress, 4),
        "target_return_pct": 100.0,
        "current_return_pct": round((equity / baseline - 1.0) * 100.0 if baseline else 0.0, 4),
        "required_return_pct_from_current_equity": round(required_return_pct, 6) if required_return_pct is not None else None,
    }


def object_walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from object_walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from object_walk(child)


def json_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(path for path in directory.rglob("*.json") if path.is_file())


def trade_sort_key(trade: dict[str, Any], source_path: Path | None = None) -> tuple[int, int, dt.datetime, float]:
    status = normalized_trade_status(trade)
    status_score = 2 if status == "closed" or trade.get("realized_pnl_usd") is not None else 1
    richness_score = sum(
        1
        for key in (
            "notional_usd",
            "quantity",
            "entry_price",
            "opened_at",
            "closed_at",
            "strategy_candidate",
            "simulated_api_order_lifecycle",
        )
        if trade.get(key) is not None
    )
    timestamp = parse_time(
        trade.get("closed_at")
        or trade.get("updated_at")
        or trade.get("opened_at")
        or trade.get("created_at")
        or trade.get("reviewed_at")
    )
    mtime = source_path.stat().st_mtime if source_path and source_path.exists() else 0.0
    return status_score, richness_score, timestamp, mtime


def normalized_trade_status(trade: dict[str, Any]) -> str:
    status = str(trade.get("status") or "").lower()
    if status == "closed" or trade.get("realized_pnl_usd") is not None:
        return "closed"
    if status == "open":
        return "open"
    if status in {"planned", "watch", "expired", "invalidated"}:
        return status
    if trade.get("entry_price") is not None and trade.get("realized_pnl_usd") is None:
        return "open"
    return status or "unknown"


def trade_outcome(trade: dict[str, Any]) -> str:
    outcome = str(trade.get("outcome") or "").lower()
    if outcome in HIT_OUTCOMES:
        return "hit"
    if outcome in FAILED_OUTCOMES:
        return "failed"
    realized = as_float(trade.get("realized_pnl_usd"))
    if realized is not None:
        return "hit" if realized > 0 else "failed"
    return "pending"


def trade_notional(trade: dict[str, Any]) -> float:
    for key in ("notional_usd", "virtual_notional_usd", "gross_quote_usd", "requested_notional_usd"):
        value = as_float(trade.get(key))
        if value is not None:
            return value
    entry = as_float(trade.get("entry_price"))
    quantity = as_float(trade.get("quantity"))
    if entry is not None and quantity is not None:
        return entry * quantity
    return 0.0


def pct_distance(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return (numerator / denominator - 1.0) * 100.0


def looks_like_trade(obj: dict[str, Any]) -> bool:
    if not obj.get("paper_trade_id") or obj.get("paper_order_id"):
        return False
    trade_markers = {
        "entry_price",
        "opened_at",
        "closed_at",
        "expires_at",
        "realized_pnl_usd",
        "unrealized_pnl_usd",
        "strategy_candidate",
        "notional_usd",
        "virtual_notional_usd",
        "paper_entry_mode",
    }
    return any(key in obj for key in trade_markers)


def collect_paper_samples(paper_dir: Path) -> dict[str, Any]:
    trades: dict[str, dict[str, Any]] = {}
    orders: dict[str, dict[str, Any]] = {}
    source_files: set[str] = set()
    malformed_files: list[dict[str, str]] = []

    for path in json_files(paper_dir):
        try:
            payload = load_json(path)
        except Exception as exc:  # noqa: BLE001
            malformed_files.append({"path": str(path), "error": str(exc)})
            continue
        source_files.add(str(path))
        for obj in object_walk(payload):
            trade_id = obj.get("paper_trade_id")
            if looks_like_trade(obj):
                existing = trades.get(str(trade_id))
                candidate = dict(obj)
                candidate["_source_path"] = str(path)
                if existing is None or trade_sort_key(candidate, path) >= trade_sort_key(existing, Path(existing.get("_source_path"))):
                    trades[str(trade_id)] = candidate
            order_id = obj.get("paper_order_id") or obj.get("order_id")
            if order_id and ("live_orders_enabled" in obj or "symbol" in obj):
                orders[str(order_id)] = {**obj, "_source_path": str(path)}

    closed = [trade for trade in trades.values() if normalized_trade_status(trade) == "closed"]
    open_positions = [trade for trade in trades.values() if normalized_trade_status(trade) == "open"]
    hit = sum(1 for trade in closed if trade_outcome(trade) == "hit")
    failed = sum(1 for trade in closed if trade_outcome(trade) == "failed")
    resolved = hit + failed
    realized_pnl = sum(as_float(trade.get("realized_pnl_usd"), 0.0) or 0.0 for trade in closed)
    closed_notional = sum(trade_notional(trade) for trade in closed)
    open_notional = sum(trade_notional(trade) for trade in open_positions)
    closed_notional_missing_count = sum(1 for trade in closed if trade_notional(trade) <= 0)
    live_orders_enabled = any(bool(item.get("live_orders_enabled")) for item in list(trades.values()) + list(orders.values()))
    private_api_used = any(bool(item.get("private_api_used")) for item in list(trades.values()) + list(orders.values()))

    by_symbol: dict[str, dict[str, Any]] = {}
    by_strategy_family: dict[str, dict[str, Any]] = {}
    by_interval: dict[str, dict[str, Any]] = {}
    by_entry_mode: dict[str, dict[str, Any]] = {}
    symbol_counter: dict[str, Counter] = defaultdict(Counter)
    family_counter: dict[str, Counter] = defaultdict(Counter)
    interval_counter: dict[str, Counter] = defaultdict(Counter)
    entry_mode_counter: dict[str, Counter] = defaultdict(Counter)
    symbol_pnl: dict[str, float] = defaultdict(float)
    symbol_notional: dict[str, float] = defaultdict(float)
    family_pnl: dict[str, float] = defaultdict(float)
    family_notional: dict[str, float] = defaultdict(float)
    interval_pnl: dict[str, float] = defaultdict(float)
    interval_notional: dict[str, float] = defaultdict(float)
    entry_mode_pnl: dict[str, float] = defaultdict(float)
    entry_mode_notional: dict[str, float] = defaultdict(float)
    execution_values: dict[str, list[float]] = defaultdict(list)
    for trade in trades.values():
        status = normalized_trade_status(trade)
        if status not in {"closed", "open"}:
            continue
        symbol = str(trade.get("symbol") or "UNKNOWN")
        family = str(trade.get("strategy_family") or ((trade.get("strategy_candidate") or {}).get("strategy") or {}).get("family") or "UNKNOWN")
        interval = str(((trade.get("strategy_candidate") or {}).get("strategy") or {}).get("interval") or (trade.get("strategy_candidate") or {}).get("interval") or "UNKNOWN")
        entry_mode = str(trade.get("paper_entry_mode") or "UNKNOWN")
        symbol_counter[symbol][status] += 1
        family_counter[family][status] += 1
        interval_counter[interval][status] += 1
        entry_mode_counter[entry_mode][status] += 1
        for key in ("commission_bps", "entry_slippage_bps", "entry_spread_bps", "entry_depth_1pct_usd"):
            value = as_float(trade.get(key))
            if value is not None:
                execution_values[key].append(value)
        if status == "closed":
            outcome = trade_outcome(trade)
            realized_value = as_float(trade.get("realized_pnl_usd"), 0.0) or 0.0
            notional_value = trade_notional(trade)
            symbol_counter[symbol][outcome] += 1
            family_counter[family][outcome] += 1
            interval_counter[interval][outcome] += 1
            entry_mode_counter[entry_mode][outcome] += 1
            symbol_pnl[symbol] += realized_value
            symbol_notional[symbol] += notional_value
            family_pnl[family] += realized_value
            family_notional[family] += notional_value
            interval_pnl[interval] += realized_value
            interval_notional[interval] += notional_value
            entry_mode_pnl[entry_mode] += realized_value
            entry_mode_notional[entry_mode] += notional_value
    for symbol, counts in sorted(symbol_counter.items()):
        resolved_symbol = counts.get("hit", 0) + counts.get("failed", 0)
        by_symbol[symbol] = {
            "closed_count": counts.get("closed", 0),
            "open_count": counts.get("open", 0),
            "hit_count": counts.get("hit", 0),
            "failed_count": counts.get("failed", 0),
            "win_rate_pct": (counts.get("hit", 0) / resolved_symbol * 100.0) if resolved_symbol else None,
            "realized_pnl_usd": round(symbol_pnl[symbol], 6),
            "closed_notional_usd": round(symbol_notional[symbol], 6),
            "realized_net_return_pct": (symbol_pnl[symbol] / symbol_notional[symbol] * 100.0) if symbol_notional[symbol] else None,
        }

    def render_group(
        counter: dict[str, Counter],
        pnl_map: dict[str, float],
        notional_map: dict[str, float],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name, counts in sorted(counter.items()):
            resolved_group = counts.get("hit", 0) + counts.get("failed", 0)
            pnl = pnl_map.get(name, 0.0)
            notional = notional_map.get(name, 0.0)
            result[name] = {
                "closed_count": counts.get("closed", 0),
                "open_count": counts.get("open", 0),
                "hit_count": counts.get("hit", 0),
                "failed_count": counts.get("failed", 0),
                "win_rate_pct": (counts.get("hit", 0) / resolved_group * 100.0) if resolved_group else None,
                "realized_pnl_usd": round(pnl, 6),
                "closed_notional_usd": round(notional, 6),
                "realized_net_return_pct": (pnl / notional * 100.0) if notional else None,
            }
        return result

    def average(key: str) -> float | None:
        values = execution_values.get(key) or []
        return round(sum(values) / len(values), 6) if values else None

    recent_closed = sorted(closed, key=lambda item: trade_sort_key(item, Path(item.get("_source_path"))), reverse=True)[:10]
    now = dt.datetime.now(dt.timezone.utc)
    open_exit_calendar = []
    unrealized_values = []
    for trade in open_positions:
        entry = as_float(trade.get("entry_price"))
        last = as_float(trade.get("last_price"), entry)
        stop = as_float(trade.get("stop_price"))
        take = as_float(trade.get("take_profit_price"))
        opened_at = parse_time(trade.get("opened_at"))
        expires_at = parse_time(trade.get("expires_at"))
        hours_open = (now - opened_at).total_seconds() / 3600.0 if opened_at.timestamp() > 0 else None
        hours_to_expiry = (expires_at - now).total_seconds() / 3600.0 if expires_at.timestamp() > 0 else None
        unrealized_pct = as_float(trade.get("unrealized_pnl_pct"))
        risk_state = trade.get("risk_state") or {}
        trailing_floor_pct = as_float(risk_state.get("trailing_floor_pct"))
        highest_unrealized_pnl_pct = as_float(risk_state.get("highest_unrealized_pnl_pct"))
        distance_to_trailing_floor_pct = None
        if unrealized_pct is not None and trailing_floor_pct is not None:
            distance_to_trailing_floor_pct = unrealized_pct - trailing_floor_pct
        if unrealized_pct is not None:
            unrealized_values.append(unrealized_pct)
        open_exit_calendar.append(
            {
                "paper_trade_id": trade.get("paper_trade_id"),
                "symbol": trade.get("symbol"),
                "paper_entry_mode": trade.get("paper_entry_mode"),
                "strategy_family": trade.get("strategy_family"),
                "opened_at": trade.get("opened_at"),
                "expires_at": trade.get("expires_at"),
                "hours_open": round(hours_open, 2) if hours_open is not None else None,
                "hours_to_expiry": round(hours_to_expiry, 2) if hours_to_expiry is not None else None,
                "entry_price": entry,
                "last_price": last,
                "unrealized_pnl_pct": unrealized_pct,
                "profit_protection_armed": bool(risk_state.get("profit_protection_armed")),
                "short_horizon_profit_protection": bool(risk_state.get("short_horizon_profit_protection")),
                "highest_unrealized_pnl_pct": highest_unrealized_pnl_pct,
                "trailing_floor_pct": trailing_floor_pct,
                "distance_to_trailing_floor_pct": round(distance_to_trailing_floor_pct, 4)
                if distance_to_trailing_floor_pct is not None
                else None,
                "profit_protection_trigger_pct": as_float(risk_state.get("profit_protection_trigger_pct")),
                "profit_trailing_giveback_pct": as_float(risk_state.get("profit_trailing_giveback_pct")),
                "last_info_pressure_score": as_float(risk_state.get("last_info_pressure_score")),
                "stop_price": stop,
                "take_profit_price": take,
                "distance_to_stop_pct": round(pct_distance(last, stop), 4) if pct_distance(last, stop) is not None else None,
                "distance_to_take_pct": round(pct_distance(take, last), 4) if pct_distance(take, last) is not None else None,
                "source_path": trade.get("_source_path"),
            }
        )
    open_exit_calendar.sort(key=lambda item: (item.get("hours_to_expiry") is None, item.get("hours_to_expiry") or 10**9))
    expiring_24h = sum(1 for item in open_exit_calendar if item.get("hours_to_expiry") is not None and item["hours_to_expiry"] <= 24)
    expiring_72h = sum(1 for item in open_exit_calendar if item.get("hours_to_expiry") is not None and item["hours_to_expiry"] <= 72)
    return {
        "status": "ok",
        "paper_dir": str(paper_dir),
        "source_file_count": len(source_files),
        "malformed_files": malformed_files,
        "unique_trade_count": len(trades),
        "unique_order_count": len(orders),
        "closed_count": len(closed),
        "open_count": len(open_positions),
        "resolved_count": resolved,
        "hit_count": hit,
        "failed_count": failed,
        "win_rate_pct": (hit / resolved * 100.0) if resolved else None,
        "realized_pnl_usd": round(realized_pnl, 6),
        "closed_notional_usd": round(closed_notional, 6),
        "open_notional_usd": round(open_notional, 6),
        "closed_notional_missing_count": closed_notional_missing_count,
        "closed_notional_data_quality": "degraded" if closed_notional_missing_count else "verified",
        "realized_net_return_on_closed_notional_pct": (realized_pnl / closed_notional * 100.0) if closed_notional else None,
        "live_orders_enabled_any": live_orders_enabled,
        "private_api_used_any": private_api_used,
        "by_symbol": by_symbol,
        "by_strategy_family": render_group(family_counter, family_pnl, family_notional),
        "by_interval": render_group(interval_counter, interval_pnl, interval_notional),
        "by_entry_mode": render_group(entry_mode_counter, entry_mode_pnl, entry_mode_notional),
        "execution_quality_summary": {
            "avg_commission_bps": average("commission_bps"),
            "avg_entry_slippage_bps": average("entry_slippage_bps"),
            "avg_entry_spread_bps": average("entry_spread_bps"),
            "avg_entry_depth_1pct_usd": average("entry_depth_1pct_usd"),
        },
        "open_position_exit_summary": {
            "open_count": len(open_positions),
            "expiring_24h_count": expiring_24h,
            "expiring_72h_count": expiring_72h,
            "avg_unrealized_pnl_pct": round(sum(unrealized_values) / len(unrealized_values), 6) if unrealized_values else None,
            "nearest_expiry_at": open_exit_calendar[0].get("expires_at") if open_exit_calendar else None,
            "operator_note": "Open-position calendar is read-only evidence hygiene; it does not force exits or authorize live trades.",
        },
        "open_exit_calendar": open_exit_calendar[:12],
        "recent_closed": [
            {
                "paper_trade_id": trade.get("paper_trade_id"),
                "symbol": trade.get("symbol"),
                "outcome": trade_outcome(trade),
                "realized_pnl_usd": trade.get("realized_pnl_usd"),
                "realized_pnl_pct": trade.get("realized_pnl_pct"),
                "closed_at": trade.get("closed_at"),
                "source_path": trade.get("_source_path"),
            }
            for trade in recent_closed
        ],
    }


def collect_current_portfolio_metrics(paper_dir: Path) -> dict[str, Any]:
    ledger_path = paper_dir / "paper_portfolio_ledger.json"
    if not ledger_path.exists():
        return {"status": "missing", "path": str(ledger_path)}
    payload = load_json(ledger_path)
    monthly_goal_state = monthly_goal_state_from_ledger(payload)
    return {
        "status": "ok",
        "path": str(ledger_path),
        "initial_capital_usd": payload.get("initial_capital_usd"),
        "cash_usd": payload.get("cash_usd"),
        "open_value_usd": payload.get("open_value_usd"),
        "equity_usd": payload.get("equity_usd"),
        "net_return_pct": payload.get("net_return_pct"),
        "max_drawdown_pct": payload.get("max_drawdown_pct"),
        "open_count": len(payload.get("open_positions") or []),
        "closed_count": len(payload.get("closed_trades") or []),
        "paper_order_count": len(payload.get("paper_orders") or []),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "updated_at": payload.get("updated_at"),
        "created_at": payload.get("created_at"),
        "monthly_goal_state": monthly_goal_state,
    }


def collect_recommendation_samples(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "missing",
            "path": str(path),
            "total_recommendations": 0,
            "outcome_reviews": 0,
            "resolved_count": 0,
            "hit_count": 0,
            "failed_count": 0,
            "pending_count": 0,
            "superseded_count": 0,
            "hit_rate_pct": None,
        }
    payload = load_json(path)
    recommendations = payload.get("recommendations") or []
    hit = sum(1 for item in recommendations if item.get("outcome_status") == "hit")
    failed = sum(1 for item in recommendations if item.get("outcome_status") == "failed")
    pending = sum(1 for item in recommendations if item.get("outcome_status") == "pending")
    superseded = sum(1 for item in recommendations if item.get("outcome_status") == "superseded")
    resolved = hit + failed
    return {
        "status": "ok",
        "path": str(path),
        "total_recommendations": len(recommendations),
        "outcome_reviews": len(payload.get("outcome_reviews") or []),
        "resolved_count": resolved,
        "hit_count": hit,
        "failed_count": failed,
        "pending_count": pending,
        "superseded_count": superseded,
        "hit_rate_pct": (hit / resolved * 100.0) if resolved else None,
    }


def walkforward_metrics(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    ranked = payload.get("top_ranked") or []
    stage_counts = payload.get("stage_counts") or {}
    target_pass_count = sum(
        int(count or 0)
        for stage, count in stage_counts.items()
        if str(stage).startswith("target_research_pass")
    )
    paper_only_count = sum(int(count or 0) for stage, count in stage_counts.items() if "paper" in str(stage))
    research_watch_count = sum(int(count or 0) for stage, count in stage_counts.items() if "research_watch" in str(stage))
    return {
        "status": "ok",
        "evidence_path": str(path),
        "generated_at": payload.get("generated_at"),
        "frames_loaded": payload.get("frames_loaded", 0),
        "stage_counts": stage_counts,
        "target_research_pass_count": target_pass_count,
        "paper_only_count": paper_only_count,
        "research_watch_count": research_watch_count,
        "top_candidates": [
            {
                "symbol": item.get("symbol"),
                "interval": item.get("interval"),
                "stage": (item.get("best_oos") or {}).get("stage"),
                "trade_count": (item.get("best_oos") or {}).get("trade_count"),
                "win_rate_pct": (item.get("best_oos") or {}).get("win_rate_pct"),
                "net_return_pct": (item.get("best_oos") or {}).get("net_return_pct"),
                "max_drawdown_pct": (item.get("best_oos") or {}).get("max_drawdown_pct"),
                "weekly_double_trade_count": (item.get("best_oos") or {}).get("weekly_double_trade_count"),
                "current_signal": (item.get("best_oos") or {}).get("current_signal"),
            }
            for item in ranked[:8]
        ],
    }


def looks_like_walkforward_evidence(path: Path) -> bool:
    try:
        payload = load_json(path)
    except Exception:  # noqa: BLE001
        return False
    if payload.get("audit_version") or payload.get("walkforward_sample_metrics"):
        return False
    if "frames_loaded" not in payload:
        return False
    return bool(payload.get("top_ranked") or payload.get("results") or payload.get("stage_counts"))


def latest_walkforward(experiment_dir: Path) -> dict[str, Any]:
    files = sorted(experiment_dir.glob("*weekly-goal*.json")) + sorted(experiment_dir.glob("*walkforward*.json"))
    files = [path for path in files if path.is_file() and looks_like_walkforward_evidence(path)]
    if not files:
        return {
            "status": "missing",
            "evidence_path": None,
            "frames_loaded": 0,
            "stage_counts": {},
            "target_research_pass_count": 0,
            "paper_only_count": 0,
            "research_watch_count": 0,
            "top_candidates": [],
        }
    latest = max(files, key=lambda item: item.stat().st_mtime)
    try:
        return walkforward_metrics(latest)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "evidence_path": str(latest),
            "error": str(exc),
            "frames_loaded": 0,
            "stage_counts": {},
            "target_research_pass_count": 0,
            "paper_only_count": 0,
            "research_watch_count": 0,
            "top_candidates": [],
        }


def current_signal_candidate_summary(item: dict[str, Any]) -> dict[str, Any]:
    train = item.get("train_summary") or {}
    validation = item.get("validation_summary") or {}
    oos = item.get("oos_summary") or {}
    initial = 500.0
    authoritative_three_segment = (
        item.get("validation_protocol") == "train_50_validation_25_final_holdout_25"
        and item.get("holdout_used_for_selection") is False
        and isinstance(item.get("promotion_blockers"), list)
    )
    blockers: list[str] = list(item.get("promotion_blockers") or []) if authoritative_three_segment else []
    if not authoritative_three_segment:
        if int(train.get("trade_count") or 0) < 12:
            blockers.append("train_trade_sample_below_12")
        if (as_float(train.get("final_capital"), 0.0) or 0.0) <= initial:
            blockers.append("train_final_capital_below_initial")
        if (as_float(train.get("net_return_pct"), -999.0) or -999.0) < 20.0:
            blockers.append("train_net_return_below_20_pct")
        if (as_float(train.get("max_drawdown_pct"), 0.0) or 0.0) < -35.0:
            blockers.append("train_drawdown_too_deep")
        if int(oos.get("trade_count") or 0) < 10:
            blockers.append("oos_trade_sample_below_10")
        if (as_float(oos.get("max_drawdown_pct"), 0.0) or 0.0) < -35.0:
            blockers.append("oos_drawdown_too_deep")
    return {
        "symbol": item.get("symbol"),
        "interval": item.get("interval"),
        "stage": item.get("stage"),
        "current_signal": True,
        "strategy": item.get("strategy") or {},
        "strategy_family": (item.get("strategy") or {}).get("family"),
        "validation_protocol": item.get("validation_protocol"),
        "holdout_used_for_selection": item.get("holdout_used_for_selection"),
        "authoritative_three_segment": authoritative_three_segment,
        "train_trade_count": train.get("trade_count"),
        "train_win_rate_pct": train.get("win_rate_pct"),
        "train_net_return_pct": train.get("net_return_pct"),
        "train_final_capital": train.get("final_capital"),
        "train_max_drawdown_pct": train.get("max_drawdown_pct"),
        "validation_trade_count": validation.get("trade_count"),
        "validation_win_rate_pct": validation.get("win_rate_pct"),
        "validation_net_return_pct": validation.get("net_return_pct"),
        "validation_final_capital": validation.get("final_capital"),
        "validation_max_drawdown_pct": validation.get("max_drawdown_pct"),
        "oos_trade_count": oos.get("trade_count"),
        "oos_win_rate_pct": oos.get("win_rate_pct"),
        "oos_net_return_pct": oos.get("net_return_pct"),
        "oos_final_capital": oos.get("final_capital"),
        "oos_max_drawdown_pct": oos.get("max_drawdown_pct"),
        "oos_weekly_double_trade_count": oos.get("weekly_double_trade_count"),
        "promotion_blockers": blockers,
        "why_not_live_ready": "current signal is active, but train/OOS/paper calibration gates still block promotion",
        "recommended_max_action": item.get("recommended_max_action") or "paper_only",
    }


def current_signal_metrics(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    raw_candidates = payload.get("top_candidates") or payload.get("candidates") or []
    by_symbol_interval: dict[tuple[str, str], dict[str, Any]] = {}
    for item in raw_candidates:
        key = (str(item.get("symbol") or "UNKNOWN"), str(item.get("interval") or "UNKNOWN"))
        summary = current_signal_candidate_summary(item)
        oos = item.get("oos_summary") or {}
        train = item.get("train_summary") or {}
        score = (
            1 if not summary.get("promotion_blockers") else 0,
            1 if int(oos.get("trade_count") or 0) >= 10 else 0,
            1 if int(train.get("trade_count") or 0) >= 12 else 0,
            1 if (as_float(oos.get("max_drawdown_pct"), -999.0) or -999.0) >= -15.0 else 0,
            1 if (as_float(oos.get("win_rate_pct"), 0.0) or 0.0) >= 65.0 else 0,
            1 if (as_float(train.get("max_drawdown_pct"), -999.0) or -999.0) >= -25.0 else 0,
            as_float(oos.get("win_rate_pct"), 0.0) or 0.0,
            as_float(oos.get("max_drawdown_pct"), -999.0) or -999.0,
            as_float(oos.get("final_capital"), 0.0) or 0.0,
            as_float(oos.get("net_return_pct"), 0.0) or 0.0,
            int(oos.get("trade_count") or 0),
        )
        existing = by_symbol_interval.get(key)
        if existing is None:
            by_symbol_interval[key] = {**item, "_score": score}
            continue
        if score > existing.get("_score", (0.0, 0.0, 0.0, 0)):
            by_symbol_interval[key] = {**item, "_score": score}

    summaries = [current_signal_candidate_summary(item) for item in by_symbol_interval.values()]
    summaries.sort(
        key=lambda item: (
            as_float(item.get("oos_final_capital"), 0.0) or 0.0,
            as_float(item.get("oos_net_return_pct"), 0.0) or 0.0,
            as_float(item.get("oos_win_rate_pct"), 0.0) or 0.0,
        ),
        reverse=True,
    )
    near_misses = [
        item
        for item in summaries
        if int(item.get("oos_trade_count") or 0) >= 10
        and (
            (as_float(item.get("oos_final_capital"), 0.0) or 0.0) >= 1000.0
            or (
                (as_float(item.get("oos_net_return_pct"), 0.0) or 0.0) >= 50.0
                and (as_float(item.get("oos_win_rate_pct"), 0.0) or 0.0) >= 70.0
            )
        )
        and item.get("promotion_blockers")
    ]
    return {
        "status": "ok",
        "evidence_path": str(path),
        "generated_at": payload.get("generated_at"),
        "frames_loaded": payload.get("frames_loaded", 0),
        "strategies_scanned": payload.get("strategies_scanned"),
        "current_signal_strategies": payload.get("current_signal_strategies"),
        "unique_symbol_interval_count": len(summaries),
        "top_current_signals": summaries[:8],
        "near_miss_target_research_candidates": near_misses[:8],
        "operator_note": "Near-miss candidates are paper validation priorities, not live trade permissions.",
    }


def looks_like_current_signal_evidence(path: Path) -> bool:
    try:
        payload = load_json(path)
    except Exception:  # noqa: BLE001
        return False
    return "current_signal_strategies" in payload and ("candidates" in payload or "top_candidates" in payload)


def latest_current_signal_probe(experiment_dir: Path) -> dict[str, Any]:
    files = sorted(experiment_dir.glob("*current-signal-probe*.json"))
    files = [path for path in files if path.is_file() and looks_like_current_signal_evidence(path)]
    if not files:
        return {
            "status": "missing",
            "evidence_path": None,
            "frames_loaded": 0,
            "strategies_scanned": 0,
            "current_signal_strategies": 0,
            "unique_symbol_interval_count": 0,
            "top_current_signals": [],
            "near_miss_target_research_candidates": [],
        }
    latest = max(files, key=lambda item: item.stat().st_mtime)
    try:
        return current_signal_metrics(latest)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "evidence_path": str(latest),
            "error": str(exc),
            "frames_loaded": 0,
            "strategies_scanned": 0,
            "current_signal_strategies": 0,
            "unique_symbol_interval_count": 0,
            "top_current_signals": [],
            "near_miss_target_research_candidates": [],
        }


def gap(current: Any, required: Any) -> int:
    return max(0, int(required or 0) - int(current or 0))


def build_sample_plan(
    paper: dict[str, Any],
    rec: dict[str, Any],
    walkforward: dict[str, Any],
    thresholds: dict[str, Any],
    portfolio: dict[str, Any] | None = None,
    current_signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failed: list[str] = []
    if int(paper.get("closed_count") or 0) < int(thresholds["min_closed_paper_trades"]):
        failed.append("paper_closed_sample_too_small")
    win_rate = as_float(paper.get("win_rate_pct"), -1.0)
    if win_rate is None or win_rate < float(thresholds["min_paper_win_rate_pct"]):
        failed.append("paper_win_rate_below_gate")
    net_return = as_float(paper.get("realized_net_return_on_closed_notional_pct"), -999.0)
    if net_return is None or net_return < float(thresholds["min_paper_net_return_pct"]):
        failed.append("paper_closed_realized_return_below_gate")
    if int(rec.get("outcome_reviews") or 0) < int(thresholds["min_recommendation_outcome_reviews"]):
        failed.append("recommendation_outcome_sample_too_small")
    if int(rec.get("resolved_count") or 0) < int(thresholds["min_calibration_resolved"]):
        failed.append("probability_calibration_sample_too_small")
    if walkforward.get("status") != "ok":
        failed.append("walkforward_evidence_missing")
    if int(walkforward.get("frames_loaded") or 0) < int(thresholds["min_walkforward_frames"]):
        failed.append("walkforward_frame_sample_too_small")
    if int(walkforward.get("target_research_pass_count") or 0) < int(thresholds["min_target_research_pass"]):
        failed.append("walkforward_no_target_research_pass")
    if int(walkforward.get("paper_only_count") or 0) < int(thresholds["min_paper_only_candidates"]):
        failed.append("walkforward_no_paper_only_candidates")
    if paper.get("live_orders_enabled_any") is True:
        failed.append("paper_ledger_live_orders_flag_unexpected")
    if paper.get("private_api_used_any") is True:
        failed.append("paper_private_api_used_unexpected")
    current_drawdown = as_float((portfolio or {}).get("max_drawdown_pct"))
    if current_drawdown is not None and current_drawdown < float(thresholds["max_paper_drawdown_pct"]):
        failed.append("paper_drawdown_too_deep")

    max_action = "paper_only"
    if "paper_ledger_live_orders_flag_unexpected" in failed or "paper_private_api_used_unexpected" in failed:
        max_action = "block"
    elif failed:
        max_action = "paper_only"
    else:
        max_action = "conditional_action"

    top_paper_candidates = [
        item
        for item in walkforward.get("top_candidates") or []
        if item.get("stage") in {"paper_only", "paper_forward_candidate", "small_probe_review"}
    ][:5]
    if not top_paper_candidates:
        top_paper_candidates = (walkforward.get("top_candidates") or [])[:3]

    queue: list[dict[str, Any]] = [
        {
            "symbol": item.get("symbol"),
            "interval": item.get("interval"),
            "stage": item.get("stage"),
            "why_not_live_ready": "needs forward paper samples, recommendation calibration, and target_research_pass evidence",
            "trade_count": item.get("trade_count"),
            "win_rate_pct": item.get("win_rate_pct"),
            "net_return_pct": item.get("net_return_pct"),
            "max_drawdown_pct": item.get("max_drawdown_pct"),
        }
        for item in top_paper_candidates
    ]
    seen = {(item.get("symbol"), item.get("interval"), item.get("stage")) for item in queue}
    for item in (current_signal or {}).get("near_miss_target_research_candidates") or []:
        key = (item.get("symbol"), item.get("interval"), "current_signal_near_miss")
        if key in seen:
            continue
        seen.add(key)
        queue.append(
            {
                "symbol": item.get("symbol"),
                "interval": item.get("interval"),
                "stage": "current_signal_near_miss",
                "why_not_live_ready": "; ".join(item.get("promotion_blockers") or []) or item.get("why_not_live_ready"),
                "trade_count": item.get("oos_trade_count"),
                "win_rate_pct": item.get("oos_win_rate_pct"),
                "net_return_pct": item.get("oos_net_return_pct"),
                "max_drawdown_pct": item.get("oos_max_drawdown_pct"),
            }
        )

    return {
        "status": "insufficient_evidence" if failed else "sample_gate_passed",
        "max_allowed_action": max_action,
        "failed_gates": sorted(set(failed)),
        "sample_gaps": {
            "closed_paper_trades_needed": gap(paper.get("closed_count"), thresholds["min_closed_paper_trades"]),
            "recommendation_outcome_reviews_needed": gap(rec.get("outcome_reviews"), thresholds["min_recommendation_outcome_reviews"]),
            "calibration_resolved_needed": gap(rec.get("resolved_count"), thresholds["min_calibration_resolved"]),
            "walkforward_target_research_pass_needed": gap(walkforward.get("target_research_pass_count"), thresholds["min_target_research_pass"]),
        },
        "next_validation_queue": queue[:8],
        "operator_note": "This audit is evidence hygiene only. Walk-forward and paper samples do not authorize live trades without manual confirmation.",
    }


def load_active_config(root: Path) -> dict[str, Any]:
    path = root / "active-alpha-paper-monitor" / "config" / "active_alpha_monitor_config.json"
    if not path.exists():
        return {}
    try:
        return load_json(path)
    except Exception:  # noqa: BLE001
        return {}


def build_validation_capacity_state(
    paper: dict[str, Any],
    portfolio: dict[str, Any],
    sample_plan: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    sampler_cfg = config.get("current_signal_near_miss_sampler") or {}
    base_max_open = int(sampler_cfg.get("base_max_open_positions") or 8)
    boosted_max_open = int(sampler_cfg.get("boosted_max_open_positions") or base_max_open)
    notional = as_float(sampler_cfg.get("default_notional_usd"), 25.0) or 25.0
    boost_conditions = sampler_cfg.get("target_sprint_open_boost_conditions") or {}
    high_cash_ratio_threshold = as_float(boost_conditions.get("high_cash_ratio_threshold_pct"), 45.0) or 45.0
    max_open_exposure_pct = as_float(boost_conditions.get("max_total_open_exposure_pct"), 85.0) or 85.0
    min_cash_reserve = as_float(boost_conditions.get("min_cash_reserve_usd"), 50.0) or 50.0

    open_count = int(paper.get("open_count") or 0)
    cash = as_float(portfolio.get("cash_usd"), 0.0) or 0.0
    equity = as_float(portfolio.get("equity_usd"), 0.0) or 0.0
    open_notional = as_float(paper.get("open_notional_usd"), 0.0) or 0.0
    closed_gap = int((sample_plan.get("sample_gaps") or {}).get("closed_paper_trades_needed") or 0)
    cash_ratio_pct = (cash / equity * 100.0) if equity else 0.0
    open_exposure_pct = (open_notional / equity * 100.0) if equity else 0.0
    open_summary = paper.get("open_position_exit_summary") or {}
    expiring_72h = int(open_summary.get("expiring_72h_count") or 0)

    boost_active = (
        bool(sampler_cfg.get("target_sprint_open_boost_enabled", True))
        and closed_gap > int(boost_conditions.get("closed_paper_trades_needed_gt") or 0)
        and cash_ratio_pct >= high_cash_ratio_threshold
        and open_exposure_pct <= max_open_exposure_pct
        and cash - notional >= min_cash_reserve
    )
    effective_max_open = boosted_max_open if boost_active else base_max_open
    open_slots_remaining = max(0, effective_max_open - open_count)

    if open_count >= effective_max_open:
        recommended_runner_mode = "exit_only_until_slots_free"
        reason = "open paper positions are at or above the effective capacity; adding samples now increases noise instead of closing evidence gaps"
        next_runner_hint = (
            "validation_progress_runner --cycles 1 --skip-fast --dynamic-scan-pool "
            "--dynamic-max-symbols 18 --no-lock --compact-output"
        )
        sample_action = "pause_new_samples"
    elif open_count >= max(base_max_open, effective_max_open - 1) and expiring_72h > 0:
        recommended_runner_mode = "exit_first_then_reassess"
        reason = "open paper positions are near capacity and at least one position is close to review/expiry"
        next_runner_hint = "run paper_position_exit_monitor first; only sample again after a slot frees or data quality improves"
        sample_action = "hold_new_samples_temporarily"
    elif closed_gap > 0 and open_slots_remaining > 0 and cash - notional >= min_cash_reserve:
        recommended_runner_mode = "small_forward_sample_allowed"
        reason = "closed sample gap remains and there is still controlled paper capacity"
        next_runner_hint = (
            "validation_progress_runner --cycles 1 --dynamic-scan-pool --dynamic-max-symbols 18 "
            "--include-near-miss-sampler --no-lock --compact-output"
        )
        sample_action = "allow_one_small_sample_if_verified"
    else:
        recommended_runner_mode = "exit_monitor_only"
        reason = "cash, capacity, or validation conditions do not support additional sampling"
        next_runner_hint = (
            "validation_progress_runner --cycles 1 --skip-fast --dynamic-scan-pool "
            "--dynamic-max-symbols 18 --no-lock --compact-output"
        )
        sample_action = "pause_new_samples"

    return {
        "status": "ok",
        "base_max_open_positions": base_max_open,
        "boosted_max_open_positions": boosted_max_open,
        "effective_max_open_positions": effective_max_open,
        "target_sprint_open_boost_active": boost_active,
        "open_count": open_count,
        "open_slots_remaining": open_slots_remaining,
        "cash_usd": round(cash, 6),
        "equity_usd": round(equity, 6),
        "cash_ratio_pct": round(cash_ratio_pct, 4),
        "open_notional_usd": round(open_notional, 6),
        "open_exposure_pct": round(open_exposure_pct, 4),
        "expiring_72h_count": expiring_72h,
        "closed_paper_trades_needed": closed_gap,
        "sample_action": sample_action,
        "recommended_runner_mode": recommended_runner_mode,
        "reason": reason,
        "next_runner_hint": next_runner_hint,
        "operator_note": "Capacity state is paper-only validation guidance. It does not authorize live trades.",
    }


def classify_quality_group(stats: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, Any]:
    closed = int(stats.get("closed_count") or 0)
    open_count = int(stats.get("open_count") or 0)
    win_rate = as_float(stats.get("win_rate_pct"))
    net_return = as_float(stats.get("realized_net_return_pct"))
    min_win = float(thresholds["min_paper_win_rate_pct"])
    min_return = float(thresholds["min_paper_net_return_pct"])
    min_recovery_closed = int(thresholds.get("min_recovery_probe_closed_count") or 3)
    min_recovery_return = float(thresholds.get("min_recovery_probe_net_return_pct") or 2.0)
    if closed <= 0:
        status = "unproven_open_or_no_sample" if open_count else "unproven_no_sample"
        reason = "no resolved paper sample yet"
    elif closed == 1:
        status = "single_sample_watch"
        reason = "only one resolved sample; do not scale from this alone"
    elif (win_rate is not None and win_rate <= 25.0) and (net_return is not None and net_return < 0):
        status = "retire_from_new_samples"
        reason = "resolved samples show very low hit rate and negative net return"
    elif (
        closed >= min_recovery_closed
        and win_rate is not None
        and win_rate >= min_win
        and net_return is not None
        and net_return >= min_recovery_return
    ):
        status = "eligible_small_paper_only"
        if net_return >= min_return:
            reason = "resolved samples clear current paper quality gates, but still paper-only"
        else:
            reason = (
                "resolved samples clear the recovery probe gate but not the full promotion return gate; "
                "continue minimum-size paper validation only"
            )
    elif (win_rate is not None and win_rate < min_win) or (net_return is not None and net_return < min_return):
        status = "cooldown_until_retested"
        reason = "below paper win-rate or net-return gate"
    else:
        status = "eligible_small_paper_only"
        reason = "resolved samples clear current paper quality gates, but still paper-only"
    return {
        "status": status,
        "reason": reason,
        "closed_count": closed,
        "open_count": open_count,
        "win_rate_pct": win_rate,
        "realized_net_return_pct": net_return,
        "realized_pnl_usd": as_float(stats.get("realized_pnl_usd"), 0.0),
        "closed_notional_usd": as_float(stats.get("closed_notional_usd"), 0.0),
    }


def ranked_quality_actions(groups: dict[str, dict[str, Any]], thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for name, stats in groups.items():
        action = classify_quality_group(stats, thresholds)
        actions.append({"name": name, **action})
    action_rank = {
        "retire_from_new_samples": 0,
        "cooldown_until_retested": 1,
        "single_sample_watch": 2,
        "unproven_open_or_no_sample": 3,
        "unproven_no_sample": 4,
        "eligible_small_paper_only": 5,
    }
    actions.sort(
        key=lambda item: (
            action_rank.get(str(item.get("status")), 99),
            -(int(item.get("closed_count") or 0)),
            as_float(item.get("realized_net_return_pct"), 0.0) or 0.0,
        )
    )
    return actions


def proposed_change(
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
    fingerprint_payload = {
        "change_type": change_type,
        "affected_items": [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "reason": item.get("reason"),
            }
            for item in affected_items
            if isinstance(item, dict)
        ],
        "proposed_action": proposed_adjustment.get("action"),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    return {
        "proposed_change_id": f"pc-{change_type}-{fingerprint}",
        "proposed_change_sequence": index,
        "generated_run_stamp": stamp,
        "status": "proposed_pending_human_review",
        "scope": "paper_only_strategy_research",
        "change_type": change_type,
        "title": title,
        "rationale": rationale,
        "affected_items": affected_items,
        "proposed_adjustment": proposed_adjustment,
        "validation_plan": validation_plan,
        "risk_controls": risk_controls,
        "live_orders_enabled": False,
        "private_api_used": False,
        "operator_note": "This is a proposed paper-only strategy iteration. It does not mutate config, open trades, or authorize live trading.",
    }


def build_proposed_changes(
    retired_modes: list[dict[str, Any]],
    cooled_modes: list[dict[str, Any]],
    retired_families: list[dict[str, Any]],
    cooled_families: list[dict[str, Any]],
    weak_intervals: list[dict[str, Any]],
    eligible_modes: list[dict[str, Any]],
    eligible_families: list[dict[str, Any]],
    target_gap: float,
    required_return_pct: float | None,
) -> list[dict[str, Any]]:
    stamp = dt.datetime.now(LOCAL_TZ).strftime("%Y%m%d-%H%M%S")
    changes: list[dict[str, Any]] = []

    if retired_modes or retired_families:
        changes.append(
            proposed_change(
                stamp,
                len(changes) + 1,
                "retire_failed_sampling_paths",
                "Retire deeply negative paper sampling paths",
                "Resolved paper samples show low hit rate and negative realized net return; continuing these paths would add low-quality samples and pull the monthly target further behind.",
                [*retired_modes[:4], *retired_families[:4]],
                {
                    "action": "keep_blocked_until_offline_retest_passes",
                    "block_groups": ["entry_mode", "strategy_family"],
                    "minimum_retest_requirement": "separate offline retest plus fresh paper evidence before re-entry",
                },
                {
                    "forward_sample_allowed": False,
                    "review_after": "offline_retest_result_or_next_monthly_review",
                    "success_criteria": "no new samples from retired paths until a replacement strategy shows positive net expectancy after friction",
                },
                [
                    "paper_only",
                    "no_config_mutation_without_human_confirmation",
                    "no_live_orders",
                    "retired paths cannot bypass validation_recovery_plan_gate",
                ],
            )
        )

    if cooled_modes:
        changes.append(
            proposed_change(
                stamp,
                len(changes) + 1,
                "tighten_exploratory_probe_gate",
                "Tighten exploratory probe eligibility",
                "Exploratory entry modes have enough resolved samples to show weak or negative net return. They should not keep consuming paper capacity without stronger current-signal and liquidity evidence.",
                cooled_modes[:6],
                {
                    "action": "cooldown_then_retest_with_stricter_filters",
                    "candidate_filters": [
                        "require current_signal=true",
                        "require fresh usable current-signal artifact with frames_loaded > 0",
                        "require positive OOS net return and controlled drawdown",
                        "require spread/depth quote pass",
                    ],
                    "max_notional_usd": 25,
                },
                {
                    "forward_sample_allowed": "only_after_filters_pass",
                    "minimum_new_closed_samples": 3,
                    "success_criteria": "win_rate >= 55%, realized net return >= +2%, no single trade contributes most PnL",
                },
                [
                    "paper_only",
                    "smallest_notional_until_revalidated",
                    "stop_loss_required",
                    "no_duplicate_symbol_open_position",
                ],
            )
        )

    interval_blocks = [item for item in weak_intervals if item.get("status") in {"retire_from_new_samples", "cooldown_until_retested"}]
    if interval_blocks:
        changes.append(
            proposed_change(
                stamp,
                len(changes) + 1,
                "interval_quality_filter",
                "Restrict weak intervals until they recover",
                "Some intervals are producing negative realized net return. The system should avoid adding exposure from these intervals unless the setup is a strict quality scout or independent retest.",
                interval_blocks[:6],
                {
                    "action": "block_or_minimize_weak_interval_sampling",
                    "allowed_exception": "current_signal_quality_scout_probe only when weak interval is the sole blocker",
                    "max_notional_usd": 25,
                },
                {
                    "forward_sample_allowed": "minimal_scout_only",
                    "minimum_new_closed_samples": 3,
                    "success_criteria": "interval subgroup turns positive after fees/slippage and avoids deep drawdown",
                },
                [
                    "paper_only",
                    "recovery_gate_must_run_before_sizing",
                    "weak interval cannot override weak symbol or retired family blocks",
                ],
            )
        )

    if eligible_modes or eligible_families:
        changes.append(
            proposed_change(
                stamp,
                len(changes) + 1,
                "prioritize_positive_early_evidence",
                "Prioritize small paper probes from eligible positive groups",
                "Some groups may have early positive evidence but are still below full Phase 2 proof. They can be used to collect forward samples while remaining paper-only.",
                [*eligible_modes[:4], *eligible_families[:4]],
                {
                    "action": "prefer_small_forward_samples",
                    "max_notional_usd": 25,
                    "sample_mode": "eligible_small_paper_only",
                },
                {
                    "forward_sample_allowed": True,
                    "minimum_new_closed_samples": 5,
                    "success_criteria": "group remains net positive after friction and does not exceed drawdown budget",
                },
                [
                    "paper_only",
                    "no_live_promotion_before_phase2",
                    "max_open_positions_and_liquidity_gates_still_apply",
                ],
            )
        )

    if target_gap > 0:
        changes.append(
            proposed_change(
                stamp,
                len(changes) + 1,
                "monthly_target_recovery_posture",
                "Keep sizing conservative until quality recovers",
                "The paper ledger is below the monthly compounding target, so increasing size would amplify a strategy with unproven or negative expectancy.",
                [
                    {
                        "name": "monthly_compounding_double",
                        "status": "not_on_track",
                        "target_gap_usd": round(target_gap, 6),
                        "required_return_pct_from_current_equity": round(required_return_pct or 0.0, 6),
                    }
                ],
                {
                    "action": "do_not_increase_notional_or_capital",
                    "max_allowed_action": "paper_only",
                    "cash_usage_rule": "deploy only when strict paper gate passes; otherwise keep scanning and retesting",
                },
                {
                    "forward_sample_allowed": "only_for_strict_or_revalidated_setups",
                    "monthly_review_required": True,
                    "success_criteria": "positive closed-trade net return, improved win rate, and enough samples before increasing paper notional",
                },
                [
                    "paper_only",
                    "no_monthly_target_claim_from_backtest_or_single_trade",
                    "no_capital_increase_until_phase2_gates_improve",
                ],
            )
        )

    return changes


def build_validation_recovery_plan(
    paper: dict[str, Any],
    portfolio: dict[str, Any],
    sample_plan: dict[str, Any],
    capacity_state: dict[str, Any],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    monthly_goal = portfolio.get("monthly_goal_state") or {}
    initial = as_float(monthly_goal.get("initial_capital_usd"), as_float(portfolio.get("initial_capital_usd"), 500.0)) or 500.0
    equity = as_float(monthly_goal.get("current_equity_usd"), as_float(portfolio.get("equity_usd"), initial)) or initial
    monthly_target = as_float(monthly_goal.get("target_equity_usd"), initial * 2.0) or (initial * 2.0)
    target_gap = max(0.0, monthly_target - equity)
    required_return_pct = as_float(monthly_goal.get("required_return_pct_from_current_equity"))
    if required_return_pct is None:
        required_return_pct = ((monthly_target / equity - 1.0) * 100.0) if equity > 0 else None
    failed_gates = set(sample_plan.get("failed_gates") or [])

    by_entry_mode = ranked_quality_actions(paper.get("by_entry_mode") or {}, thresholds)
    by_strategy_family = ranked_quality_actions(paper.get("by_strategy_family") or {}, thresholds)
    by_interval = ranked_quality_actions(paper.get("by_interval") or {}, thresholds)
    by_symbol = ranked_quality_actions(paper.get("by_symbol") or {}, thresholds)

    retired_modes = [item for item in by_entry_mode if item.get("status") == "retire_from_new_samples"]
    cooled_modes = [item for item in by_entry_mode if item.get("status") == "cooldown_until_retested"]
    retired_families = [item for item in by_strategy_family if item.get("status") == "retire_from_new_samples"]
    cooled_families = [item for item in by_strategy_family if item.get("status") == "cooldown_until_retested"]
    eligible_modes = [item for item in by_entry_mode if item.get("status") == "eligible_small_paper_only"]
    eligible_families = [item for item in by_strategy_family if item.get("status") == "eligible_small_paper_only"]
    weak_intervals = [item for item in by_interval if item.get("status") in {"retire_from_new_samples", "cooldown_until_retested"}]
    weak_symbols = [item for item in by_symbol if item.get("status") in {"retire_from_new_samples", "cooldown_until_retested"}]

    new_sample_policy = "exit_monitor_and_research_only"
    if not failed_gates:
        new_sample_policy = "paper_only_small_samples_allowed"
    elif "paper_win_rate_below_gate" in failed_gates or "paper_closed_realized_return_below_gate" in failed_gates:
        new_sample_policy = "no_new_samples_from_retired_or_cooldown_groups"

    next_actions: list[str] = []
    if capacity_state.get("sample_action") in {"pause_new_samples", "hold_new_samples_temporarily"}:
        next_actions.append("Keep running exit review; do not expand paper exposure while capacity gate is paused.")
    if retired_modes:
        names = ", ".join(f"`{item['name']}`" for item in retired_modes[:4])
        next_actions.append(f"Retire these entry modes from new paper samples until a separate offline retest passes: {names}.")
    if cooled_modes:
        names = ", ".join(f"`{item['name']}`" for item in cooled_modes[:4])
        next_actions.append(f"Cooldown these entry modes; only allow research/watch until retested: {names}.")
    if retired_families:
        names = ", ".join(f"`{item['name']}`" for item in retired_families[:4])
        next_actions.append(f"Do not allocate new paper capital to these weak strategy families: {names}.")
    if eligible_modes or eligible_families:
        mode_names = ", ".join(f"`{item['name']}`" for item in eligible_modes[:3]) or "none"
        family_names = ", ".join(f"`{item['name']}`" for item in eligible_families[:3]) or "none"
        next_actions.append(f"If sampling resumes, prefer small paper-only probes from eligible modes {mode_names} and families {family_names}.")
    if target_gap > 0:
        next_actions.append(
            "Monthly-double target is not on track; required return from current equity to target is "
            f"{round(required_return_pct or 0.0, 4)}%, so the priority is quality recovery before larger sizing."
        )
    proposed_changes = build_proposed_changes(
        retired_modes,
        cooled_modes,
        retired_families,
        cooled_families,
        weak_intervals,
        eligible_modes,
        eligible_families,
        target_gap,
        required_return_pct,
    )

    return {
        "status": "not_on_track" if target_gap > 0 else "on_track_or_above_target",
        "monthly_target": {
            "target_model": monthly_goal.get("target_model") or "monthly_compounding_double",
            "month_id": monthly_goal.get("month_id"),
            "baseline_source": monthly_goal.get("baseline_source"),
            "baseline_created_at": monthly_goal.get("baseline_created_at"),
            "lifetime_initial_capital_usd": monthly_goal.get("lifetime_initial_capital_usd") or portfolio.get("initial_capital_usd"),
            "month_start_equity_usd": monthly_goal.get("month_start_equity_usd") or round(initial, 6),
            "initial_capital_usd": round(initial, 6),
            "target_equity_usd": round(monthly_target, 6),
            "current_equity_usd": round(equity, 6),
            "target_gap_usd": round(target_gap, 6),
            "progress_pct": monthly_goal.get("progress_pct"),
            "target_return_pct": monthly_goal.get("target_return_pct") or 100.0,
            "current_return_pct": monthly_goal.get("current_return_pct"),
            "required_return_pct_from_current_equity": round(required_return_pct, 6) if required_return_pct is not None else None,
        },
        "new_sample_policy": new_sample_policy,
        "retired_entry_modes": retired_modes[:8],
        "cooldown_entry_modes": cooled_modes[:8],
        "eligible_entry_modes": eligible_modes[:8],
        "retired_strategy_families": retired_families[:8],
        "cooldown_strategy_families": cooled_families[:8],
        "eligible_strategy_families": eligible_families[:8],
        "weak_intervals": weak_intervals[:8],
        "weak_symbols": weak_symbols[:8],
        "next_actions": next_actions,
        "proposed_changes": proposed_changes,
        "operator_note": "Recovery plan is paper-only strategy hygiene. It does not authorize live trades or override the validation capacity gate.",
    }


def build_audit(
    root: Path = ROOT,
    paper_dir: Path | None = None,
    experiment_dir: Path | None = None,
    recommendation_ledger: Path | None = None,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    threshold_values = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        threshold_values.update({key: value for key, value in thresholds.items() if key in threshold_values})
    paper_root = paper_dir or root / "active-alpha-paper-monitor" / "paper_trades"
    experiment_root = experiment_dir or root / "active-alpha-paper-monitor" / "experiments"
    rec_path = recommendation_ledger or root / "manual-investment-strategy-operator" / "recommendations" / "recommendation_history.json"
    paper = collect_paper_samples(paper_root)
    portfolio = collect_current_portfolio_metrics(paper_root)
    rec = collect_recommendation_samples(rec_path)
    walkforward = latest_walkforward(experiment_root)
    current_signal = latest_current_signal_probe(experiment_root)
    sample_plan = build_sample_plan(paper, rec, walkforward, threshold_values, portfolio, current_signal)
    config = load_active_config(root)
    capacity_state = build_validation_capacity_state(paper, portfolio, sample_plan, config)
    recovery_plan = build_validation_recovery_plan(paper, portfolio, sample_plan, capacity_state, threshold_values)
    return {
        "generated_at": utc_now(),
        "status": "ok",
        "audit_version": "validation-sample-audit-v1",
        "thresholds": threshold_values,
        "current_portfolio_metrics": portfolio,
        "paper_sample_metrics": paper,
        "validation_capacity_state": capacity_state,
        "validation_recovery_plan": recovery_plan,
        "recommendation_sample_metrics": rec,
        "walkforward_sample_metrics": walkforward,
        "current_signal_probe_metrics": current_signal,
        "validation_sample_plan": sample_plan,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    paper = payload.get("paper_sample_metrics") or {}
    portfolio = payload.get("current_portfolio_metrics") or {}
    capacity = payload.get("validation_capacity_state") or {}
    rec = payload.get("recommendation_sample_metrics") or {}
    walk = payload.get("walkforward_sample_metrics") or {}
    current_signal = payload.get("current_signal_probe_metrics") or {}
    plan = payload.get("validation_sample_plan") or {}
    recovery = payload.get("validation_recovery_plan") or {}
    monthly_target = recovery.get("monthly_target") or {}
    lines = [
        "# Validation Sample Audit",
        "",
        f"- generated_at: `{payload.get('generated_at')}`",
        f"- max_allowed_action: `{plan.get('max_allowed_action')}`",
        f"- failed_gates: `{plan.get('failed_gates')}`",
        f"- recovery_status: `{recovery.get('status')}`",
        f"- new_sample_policy: `{recovery.get('new_sample_policy')}`",
        "",
        "## Samples",
        "",
        "| Area | Count / State |",
        "|---|---:|",
        f"| closed paper trades | `{paper.get('closed_count')}` |",
        f"| open paper trades | `{paper.get('open_count')}` |",
        f"| current paper equity | `{portfolio.get('equity_usd')}` |",
        f"| current paper max drawdown | `{portfolio.get('max_drawdown_pct')}` |",
        f"| paper win rate | `{paper.get('win_rate_pct')}` |",
        f"| closed notional data quality | `{paper.get('closed_notional_data_quality')}` |",
        f"| recommendation outcome reviews | `{rec.get('outcome_reviews')}` |",
        f"| recommendation resolved | `{rec.get('resolved_count')}` |",
        f"| walk-forward frames | `{walk.get('frames_loaded')}` |",
        f"| walk-forward stage counts | `{walk.get('stage_counts')}` |",
        f"| current-signal unique symbols/intervals | `{current_signal.get('unique_symbol_interval_count')}` |",
        f"| current-signal near misses | `{len(current_signal.get('near_miss_target_research_candidates') or [])}` |",
        "",
        "## Sample Gaps",
        "",
        "| Gap | Needed |",
        "|---|---:|",
    ]
    for key, value in (plan.get("sample_gaps") or {}).items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(
        [
            "",
            "## Monthly Target Recovery",
            "",
            "| Field | Value |",
            "|---|---:|",
            f"| target model | `{monthly_target.get('target_model')}` |",
            f"| month | `{monthly_target.get('month_id')}` |",
            f"| baseline source | `{monthly_target.get('baseline_source')}` |",
            f"| month-start equity | `{monthly_target.get('month_start_equity_usd')}` |",
            f"| lifetime initial capital | `{monthly_target.get('lifetime_initial_capital_usd')}` |",
            f"| target equity | `{monthly_target.get('target_equity_usd')}` |",
            f"| current equity | `{monthly_target.get('current_equity_usd')}` |",
            f"| target gap | `{monthly_target.get('target_gap_usd')}` |",
            f"| progress | `{monthly_target.get('progress_pct')}` |",
            f"| required return from current equity | `{monthly_target.get('required_return_pct_from_current_equity')}` |",
            "",
            "## Recovery Actions",
            "",
        ]
    )
    actions = recovery.get("next_actions") or []
    if actions:
        lines.extend([f"- {item}" for item in actions])
    else:
        lines.append("- none")
    proposed_changes = recovery.get("proposed_changes") or []
    lines.extend(
        [
            "",
            "## Proposed Strategy Changes",
            "",
            "| ID | Type | Status | Title | Forward Test |",
            "|---|---|---|---|---|",
        ]
    )
    if proposed_changes:
        for item in proposed_changes[:8]:
            validation = item.get("validation_plan") or {}
            lines.append(
                f"| `{item.get('proposed_change_id')}` | `{item.get('change_type')}` | `{item.get('status')}` | "
                f"{item.get('title')} | `{validation.get('forward_sample_allowed')}` |"
            )
    else:
        lines.append("| - | - | - | - | - |")
    lines.extend(
        [
            "",
            "## Strategy Quality Triage",
            "",
            "| Group | Name | Status | Closed | Win Rate | Net Return | Reason |",
            "|---|---|---|---:|---:|---:|---|",
        ]
    )
    triage_rows = []
    for group_key, group_label in (
        ("retired_entry_modes", "entry_mode"),
        ("cooldown_entry_modes", "entry_mode"),
        ("eligible_entry_modes", "entry_mode"),
        ("retired_strategy_families", "strategy_family"),
        ("cooldown_strategy_families", "strategy_family"),
        ("eligible_strategy_families", "strategy_family"),
    ):
        for item in recovery.get(group_key) or []:
            triage_rows.append((group_label, item))
    if triage_rows:
        for group_label, item in triage_rows[:16]:
            lines.append(
                f"| `{group_label}` | `{item.get('name')}` | `{item.get('status')}` | "
                f"`{item.get('closed_count')}` | `{item.get('win_rate_pct')}` | "
                f"`{item.get('realized_net_return_pct')}` | {item.get('reason')} |"
            )
    else:
        lines.append("| none | - | - | 0 | - | - | - |")
    lines.extend(
        [
            "",
            "## Validation Capacity State",
            "",
            "| Area | Value |",
            "|---|---:|",
            f"| open paper trades | `{capacity.get('open_count')}` |",
            f"| effective max open positions | `{capacity.get('effective_max_open_positions')}` |",
            f"| open slots remaining | `{capacity.get('open_slots_remaining')}` |",
            f"| target sprint boost active | `{capacity.get('target_sprint_open_boost_active')}` |",
            f"| cash ratio | `{capacity.get('cash_ratio_pct')}` |",
            f"| open exposure | `{capacity.get('open_exposure_pct')}` |",
            f"| expiring within 72h | `{capacity.get('expiring_72h_count')}` |",
            f"| sample action | `{capacity.get('sample_action')}` |",
            f"| recommended runner mode | `{capacity.get('recommended_runner_mode')}` |",
            "",
            f"- reason: {capacity.get('reason')}",
            f"- next_runner_hint: `{capacity.get('next_runner_hint')}`",
            "",
        ]
    )
    lines.extend(["", "## Next Validation Queue", "", "| Symbol | Interval | Stage | Trades | Win Rate | Net Return |", "|---|---|---|---:|---:|---:|"])
    for item in plan.get("next_validation_queue") or []:
        lines.append(
            f"| `{item.get('symbol')}` | `{item.get('interval')}` | `{item.get('stage')}` | "
            f"`{item.get('trade_count')}` | `{item.get('win_rate_pct')}` | `{item.get('net_return_pct')}` |"
        )
    if not plan.get("next_validation_queue"):
        lines.append("| none | - | - | 0 | - | - |")
    lines.extend(["", "## Current-Signal Near Misses", "", "| Symbol | Interval | Stage | OOS Trades | OOS Win | OOS Return | Blockers |", "|---|---|---|---:|---:|---:|---|"])
    for item in current_signal.get("near_miss_target_research_candidates") or []:
        blockers = ", ".join(item.get("promotion_blockers") or [])
        lines.append(
            f"| `{item.get('symbol')}` | `{item.get('interval')}` | `{item.get('stage')}` | "
            f"`{item.get('oos_trade_count')}` | `{item.get('oos_win_rate_pct')}` | "
            f"`{item.get('oos_net_return_pct')}` | {blockers or '-'} |"
        )
    if not current_signal.get("near_miss_target_research_candidates"):
        lines.append("| none | - | - | 0 | - | - | - |")
    open_summary = paper.get("open_position_exit_summary") or {}
    lines.extend(
        [
            "",
            "## Open Paper Exit Calendar",
            "",
            f"- expiring_24h_count: `{open_summary.get('expiring_24h_count')}`",
            f"- expiring_72h_count: `{open_summary.get('expiring_72h_count')}`",
            f"- avg_unrealized_pnl_pct: `{open_summary.get('avg_unrealized_pnl_pct')}`",
            f"- nearest_expiry_at: `{open_summary.get('nearest_expiry_at')}`",
            "",
            "| Symbol | Mode | PnL % | Protection | Trail Floor % | Floor Dist % | Stop Dist % | Take Dist % | Hours To Expiry |",
            "|---|---|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    for item in paper.get("open_exit_calendar") or []:
        protection = "armed" if item.get("profit_protection_armed") else "watch"
        lines.append(
            f"| `{item.get('symbol')}` | `{item.get('paper_entry_mode')}` | `{item.get('unrealized_pnl_pct')}` | "
            f"`{protection}` | `{item.get('trailing_floor_pct')}` | `{item.get('distance_to_trailing_floor_pct')}` | "
            f"`{item.get('distance_to_stop_pct')}` | `{item.get('distance_to_take_pct')}` | `{item.get('hours_to_expiry')}` |"
        )
    if not paper.get("open_exit_calendar"):
        lines.append("| none | - | - | - | - | - |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build validation sample audit for paper and walk-forward evidence")
    parser.add_argument("--paper-dir", default=str(DEFAULT_PAPER_DIR))
    parser.add_argument("--experiment-dir", default=str(DEFAULT_EXPERIMENT_DIR))
    parser.add_argument("--recommendation-ledger", default=str(DEFAULT_RECOMMENDATION_LEDGER))
    parser.add_argument("--output", default="", help="Optional JSON output path")
    parser.add_argument("--markdown-output", default="", help="Optional Markdown output path")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    payload = build_audit(
        ROOT,
        paper_dir=Path(args.paper_dir),
        experiment_dir=Path(args.experiment_dir),
        recommendation_ledger=Path(args.recommendation_ledger),
    )
    if args.output:
        write_json(Path(args.output), payload)
    if args.markdown_output:
        Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_output).write_text(render_markdown(payload), encoding="utf-8")
    if args.format == "markdown":
        print(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
