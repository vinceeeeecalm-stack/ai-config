#!/usr/bin/env python3
"""Build a local visual HTML dashboard for the active-alpha paper system.

This script is display-only. It reads local paper ledger and experiment
artifacts, then writes a self-contained HTML file with KPI cards, trend charts,
trade tables and strategy iteration panels. It never fetches market data, never
places orders, and never mutates the paper ledger.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from kline_cache_storage import inspect_kline_cache_storage


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
LOCAL_TZ = ZoneInfo("Asia/Shanghai")

LEDGER_PATH = ACTIVE_ROOT / "paper_trades" / "paper_portfolio_ledger.json"
OUTPUT_HTML = ACTIVE_ROOT / "reports" / "LATEST_ACTIVE_ALPHA_DASHBOARD.html"
OUTPUT_JSON = ACTIVE_ROOT / "experiments" / "visual-dashboard-latest.json"
ARTIFACT_INDEX_JSON = ACTIVE_ROOT / "experiments" / "automation-artifact-housekeeper-latest.json"
KLINE_RESEARCH_REPRO_JSON = ACTIVE_ROOT / "experiments" / "kline-research-reproducibility-audit.json"
AUTOMATION_RECOVERY_PLAN_JSON = ACTIVE_ROOT / "experiments" / "automation-recovery-plan.json"
AUTOMATION_PATH = Path("/Users/vincentpan/.codex/automations/active-alpha-hourly-crypto-paper-loop/automation.toml")


def now_local() -> dt.datetime:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def latest_file(pattern: str) -> Path | None:
    paths = list(ACTIVE_ROOT.glob(pattern))
    if not paths:
        return None
    return max(paths, key=lambda item: item.stat().st_mtime)


def rel(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def parse_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(LOCAL_TZ)


def artifact_age_hours(value: Any) -> float | None:
    parsed = parse_time(value)
    if not parsed:
        return None
    age = (now_local() - parsed).total_seconds() / 3600.0
    return round(max(0.0, age), 4)


def freshness_status(value: Any, max_age_hours: float = 6.0) -> str:
    age = artifact_age_hours(value)
    if age is None:
        return "missing"
    return "fresh" if age <= max_age_hours else "stale"


def effective_artifact_status(status: Any, created_at: Any, max_age_hours: float = 6.0) -> str:
    return str(status or "missing") if freshness_status(created_at, max_age_hours) == "fresh" else "stale_not_actionable"


def automation_summary() -> dict[str, Any]:
    if not AUTOMATION_PATH.exists():
        return {"status": "missing", "path": str(AUTOMATION_PATH)}
    text = AUTOMATION_PATH.read_text(encoding="utf-8")
    summary: dict[str, Any] = {"path": str(AUTOMATION_PATH)}
    for key in ("id", "name", "status", "rrule"):
        marker = f'{key} = "'
        start = text.find(marker)
        if start < 0:
            continue
        start += len(marker)
        end = text.find('"', start)
        if end >= 0:
            summary[key] = text[start:end]
    return summary


def latest_automation_recovery_summary() -> dict[str, Any]:
    payload = read_json(AUTOMATION_RECOVERY_PLAN_JSON, {})
    inventory = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {}
    contract = payload.get("proposed_contract") if isinstance(payload.get("proposed_contract"), dict) else {}
    return {
        "status": payload.get("status") or "missing",
        "reason": payload.get("reason"),
        "automation_count": inventory.get("count"),
        "requires_explicit_user_approval": payload.get("requires_explicit_user_approval"),
        "max_allowed_action": payload.get("max_allowed_action"),
        "automation_mutated": payload.get("automation_mutated"),
        "proposed_initial_status": contract.get("initial_status"),
        "prompt_sha256": contract.get("prompt_sha256"),
        "next_action": payload.get("next_action"),
    }


def money(value: Any, digits: int = 2) -> str:
    try:
        return f"${float(value):,.{digits}f}"
    except Exception:
        return "-"


def pct(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}%"
    except Exception:
        return "-"


def short_time(value: Any) -> str:
    parsed = parse_time(value)
    if not parsed:
        return "-"
    return parsed.strftime("%m-%d %H:%M")


def latest_runner_payload() -> tuple[Path | None, dict[str, Any]]:
    path = latest_file("experiments/*validation-progress-runner.json")
    return path, read_json(path, {}) if path else {}


def child_stdout_json(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        item = payload.get(key)
        if isinstance(item, dict) and isinstance(item.get("stdout_json"), dict):
            return item["stdout_json"]
        if isinstance(item, dict):
            return item
    return {}


def runner_post_validation(payload: dict[str, Any]) -> dict[str, Any]:
    return child_stdout_json(payload, "post_validation", "post_audit", "after_validation_sample_audit")


def svg_line_chart(
    points: list[tuple[str, float]],
    *,
    width: int = 760,
    height: int = 240,
    color: str = "#2f7dd1",
    fill: str = "#dcecff",
    baseline: float | None = None,
) -> str:
    pad_l, pad_r, pad_t, pad_b = 42, 14, 16, 30
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b
    if not points:
        return f'<svg viewBox="0 0 {width} {height}" class="chart"><text x="24" y="120">No data</text></svg>'
    values = [point[1] for point in points]
    if baseline is not None:
        values.append(float(baseline))
    min_v = min(values)
    max_v = max(values)
    if math.isclose(min_v, max_v):
        min_v -= 1
        max_v += 1
    span = max_v - min_v

    def xy(index: int, value: float) -> tuple[float, float]:
        x = pad_l + (inner_w * index / max(1, len(points) - 1))
        y = pad_t + inner_h - ((value - min_v) / span * inner_h)
        return x, y

    coords = [xy(index, value) for index, (_, value) in enumerate(points)]
    path = " ".join(("M" if index == 0 else "L") + f"{x:.2f},{y:.2f}" for index, (x, y) in enumerate(coords))
    area = f"{path} L {coords[-1][0]:.2f},{pad_t + inner_h:.2f} L {coords[0][0]:.2f},{pad_t + inner_h:.2f} Z"
    grid = []
    for step in range(5):
        y = pad_t + inner_h * step / 4
        value = max_v - span * step / 4
        grid.append(f'<line x1="{pad_l}" y1="{y:.2f}" x2="{width-pad_r}" y2="{y:.2f}" class="grid"/>')
        grid.append(f'<text x="6" y="{y+4:.2f}" class="axis">{value:.0f}</text>')
    baseline_line = ""
    if baseline is not None:
        _, by = xy(0, float(baseline))
        baseline_line = f'<line x1="{pad_l}" y1="{by:.2f}" x2="{width-pad_r}" y2="{by:.2f}" class="baseline"/>'
    first_label = esc(points[0][0])
    last_label = esc(points[-1][0])
    circles = "".join(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.2"/>' for x, y in coords[-10:])
    return f"""
<svg viewBox="0 0 {width} {height}" class="chart" role="img">
  {''.join(grid)}
  {baseline_line}
  <path d="{area}" fill="{fill}" opacity="0.72"/>
  <path d="{path}" fill="none" stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
  <g fill="{color}">{circles}</g>
  <text x="{pad_l}" y="{height-8}" class="axis">{first_label}</text>
  <text x="{width-pad_r-92}" y="{height-8}" class="axis">{last_label}</text>
</svg>
""".strip()


def svg_bar_chart(rows: list[tuple[str, float]], *, width: int = 760, height: int = 250) -> str:
    pad_l, pad_r, pad_t, pad_b = 42, 14, 16, 56
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b
    if not rows:
        return f'<svg viewBox="0 0 {width} {height}" class="chart"><text x="24" y="120">No data</text></svg>'
    values = [value for _, value in rows]
    min_v = min(values + [0])
    max_v = max(values + [0])
    if math.isclose(min_v, max_v):
        min_v -= 1
        max_v += 1
    span = max_v - min_v
    zero_y = pad_t + inner_h - ((0 - min_v) / span * inner_h)
    bar_gap = 5
    bar_w = max(6, (inner_w - bar_gap * (len(rows) - 1)) / len(rows))
    bars = []
    labels = []
    for index, (label, value) in enumerate(rows):
        x = pad_l + index * (bar_w + bar_gap)
        y = pad_t + inner_h - ((value - min_v) / span * inner_h)
        h = abs(zero_y - y)
        top = min(y, zero_y)
        cls = "bar-positive" if value >= 0 else "bar-negative"
        bars.append(f'<rect x="{x:.2f}" y="{top:.2f}" width="{bar_w:.2f}" height="{max(2,h):.2f}" rx="4" class="{cls}"><title>{esc(label)} {value:.2f}</title></rect>')
        if index % max(1, len(rows) // 8) == 0 or len(rows) <= 10:
            labels.append(f'<text x="{x:.2f}" y="{height-24}" class="axis rotate">{esc(label[:8])}</text>')
    grid = f'<line x1="{pad_l}" y1="{zero_y:.2f}" x2="{width-pad_r}" y2="{zero_y:.2f}" class="baseline"/>'
    return f"""
<svg viewBox="0 0 {width} {height}" class="chart" role="img">
  {grid}
  {''.join(bars)}
  {''.join(labels)}
</svg>
""".strip()


def build_equity_points(ledger: dict[str, Any]) -> list[tuple[str, float]]:
    initial = float(ledger.get("initial_capital_usd") or 500.0)
    trades = []
    for trade in ledger.get("closed_trades") or []:
        closed_at = parse_time(trade.get("closed_at") or trade.get("updated_at") or trade.get("expires_at"))
        if not closed_at:
            continue
        pnl = float(trade.get("realized_pnl_usd") or 0.0)
        trades.append((closed_at, pnl))
    trades.sort(key=lambda item: item[0])
    points = []
    equity = initial
    if trades:
        points.append((trades[0][0].strftime("%m-%d"), equity))
    for when, pnl in trades:
        equity += pnl
        points.append((when.strftime("%m-%d %H:%M"), equity))
    if not points:
        points.append((now_local().strftime("%m-%d %H:%M"), float(ledger.get("equity_usd") or initial)))
    current = float(ledger.get("equity_usd") or equity)
    if not math.isclose(points[-1][1], current, rel_tol=1e-6, abs_tol=1e-6):
        points.append((now_local().strftime("%m-%d %H:%M"), current))
    return points


def closed_trade_rows(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for trade in ledger.get("closed_trades") or []:
        pnl = float(trade.get("realized_pnl_usd") or 0.0)
        rows.append(
            {
                "id": trade.get("paper_trade_id"),
                "symbol": trade.get("symbol"),
                "outcome": trade.get("outcome"),
                "pnl": pnl,
                "pnl_pct": trade.get("realized_pnl_pct"),
                "notional": trade.get("notional_usd"),
                "opened_at": trade.get("opened_at"),
                "closed_at": trade.get("closed_at"),
                "entry_mode": trade.get("paper_entry_mode"),
                "family": trade.get("strategy_family"),
                "max_unrealized": ((trade.get("risk_state") or {}).get("highest_unrealized_pnl_pct")),
            }
        )
    rows.sort(key=lambda item: parse_time(item.get("closed_at")) or dt.datetime.min.replace(tzinfo=LOCAL_TZ), reverse=True)
    return rows


def aggregate_by_symbol(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = row.get("symbol") or "UNKNOWN"
        item = agg.setdefault(symbol, {"symbol": symbol, "count": 0, "wins": 0, "pnl": 0.0, "notional": 0.0})
        item["count"] += 1
        item["wins"] += 1 if float(row.get("pnl") or 0) > 0 else 0
        item["pnl"] += float(row.get("pnl") or 0.0)
        item["notional"] += float(row.get("notional") or 0.0)
    for item in agg.values():
        item["win_rate"] = (item["wins"] / item["count"] * 100) if item["count"] else 0
    return sorted(agg.values(), key=lambda item: item["pnl"])


def runner_summary(payload: dict[str, Any]) -> dict[str, Any]:
    post = runner_post_validation(payload)
    recovery = post.get("validation_recovery_plan") or payload.get("validation_recovery_plan") or {}
    market = payload.get("dynamic_scan_universe") or payload.get("dynamic_scan_pool") or {}
    current_signal = latest_file("experiments/*current-signal-probe.json")
    current_payload = read_json(current_signal, {}) if current_signal else {}
    top_candidates = current_payload.get("top_candidates") or current_payload.get("top_current_signals") or []
    runner_created_at = payload.get("created_at") or payload.get("generated_at") or payload.get("completed_at")
    current_generated_at = current_payload.get("generated_at") or current_payload.get("created_at")
    runner_freshness = freshness_status(runner_created_at)
    current_freshness = freshness_status(current_generated_at)
    cached_top_symbols: list[str] = []
    seen_symbols: set[str] = set()
    for item in top_candidates:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        cached_top_symbols.append(symbol)
        if len(cached_top_symbols) >= 6:
            break
    frames_loaded = int(current_payload.get("frames_loaded") or 0)
    if frames_loaded <= 0:
        current_signal_state = "empty_or_missing"
    elif current_freshness != "fresh":
        current_signal_state = "stale_not_actionable"
    else:
        current_signal_state = "usable_and_fresh"
    cached_selected_symbols = market.get("selected_symbols") or []
    return {
        "run_id": payload.get("run_id"),
        "status": payload.get("status"),
        "created_at": runner_created_at,
        "age_hours": artifact_age_hours(runner_created_at),
        "freshness_status": runner_freshness,
        "market_regime": market.get("market_regime") or market.get("regime"),
        "market_atmosphere": market.get("market_atmosphere"),
        "sentiment_state": market.get("sentiment_state"),
        "selected_symbols": cached_selected_symbols if runner_freshness == "fresh" else [],
        "cached_selected_symbols": cached_selected_symbols,
        "no_entry": payload.get("no_entry_summary") or {},
        "proposed_changes": recovery.get("proposed_changes") or [],
        "current_signal_path": rel(current_signal),
        "current_signal_generated_at": current_generated_at,
        "current_signal_age_hours": artifact_age_hours(current_generated_at),
        "current_signal_freshness_status": current_freshness,
        "current_signal_state": current_signal_state,
        "current_signal_frames": frames_loaded,
        "current_signal_strategies": current_payload.get("strategies_scanned"),
        "current_signal_candidates": current_payload.get("deduplicated_candidate_count"),
        "current_top_symbols": cached_top_symbols if current_signal_state == "usable_and_fresh" else [],
        "cached_top_symbols": cached_top_symbols,
    }


def sample_growth_action_board(payload: dict[str, Any]) -> dict[str, Any]:
    runner = payload.get("runner") or {}
    no_entry = runner.get("no_entry") or {}
    capital = payload.get("capital_allocation") or {}
    context = payload.get("paper_market_context") or {}
    closed_count = len(payload.get("closed_rows") or [])
    reason_counts = no_entry.get("reason_counts") or {}
    per_trade = capital.get("per_trade_notional_usd")
    max_deploy = capital.get("max_deployable_now_usd")
    authorized_candidate_notional = min(float(max_deploy or 0.0), float(per_trade or 0.0))
    blocked_sampler = payload.get("blocked_retest_sampler") or {}
    actions = []
    for item in (no_entry.get("top_blocked_candidates") or [])[:5]:
        reason = str(item.get("primary_block_reason") or "")
        if reason.startswith("validation_recovery_plan_block"):
            next_step = "retest_or_independent_quality_scout"
        elif reason == "not_entry_eligible":
            next_step = "wait_for_full_trigger_contract"
        elif "liquidity" in reason or "spread" in reason:
            next_step = "wait_for_microstructure_improvement"
        else:
            next_step = "collect_more_forward_evidence"
        actions.append(
            {
                "symbol": item.get("symbol"),
                "block": reason or "-",
                "next_step": next_step,
                "max_notional": authorized_candidate_notional if "quality_scout" in next_step else 0,
            }
        )
    notes = [
        "paper-only; no live orders",
        "do not size up before Phase 2 proof",
    ]
    if context.get("status") == "warn":
        notes.append("future trades must add market context evidence")
    trigger_watch = []
    for item in (blocked_sampler.get("decisions") or [])[:5]:
        reasons = item.get("reasons") or []
        distance = None
        for reason in reasons:
            text = str(reason)
            if text.startswith("current_trigger_not_confirmed_distance_"):
                distance = text.removeprefix("current_trigger_not_confirmed_distance_")
                break
        trigger_watch.append(
            {
                "symbol": item.get("symbol"),
                "decision": item.get("decision"),
                "last_price": item.get("last_price"),
                "trigger": item.get("breakout_level"),
                "distance": distance,
                "spread_bps": item.get("spread_bps"),
                "depth": item.get("depth"),
                "oos_win_rate_pct": (item.get("oos") or {}).get("win_rate_pct"),
                "oos_net_return_pct": (item.get("oos") or {}).get("net_return_pct"),
                "reasons": reasons[:3],
            }
        )
    automation_paused = (payload.get("automation") or {}).get("status") == "PAUSED"
    status = "scan_active_but_no_paper_entry" if no_entry.get("status") == "no_new_entry" else no_entry.get("status")
    if automation_paused:
        status = "paused_ready_for_user_confirmation"
        notes.append("automation paused; cached candidates are not current entry instructions")
    elif (payload.get("automation") or {}).get("status") in {"missing", "MISSING", None}:
        status = "not_ready_operational_authorization_blocked"
        notes.append("automation definition missing; no current paper deployment is authorized")
    return {
        "status": status,
        "closed_trade_gap": max(0, 30 - closed_count),
        "capital_policy": capital.get("policy"),
        "max_deployable": max_deploy,
        "per_trade": per_trade,
        "reason_counts": reason_counts,
        "actions": actions,
        "trigger_watch": trigger_watch,
        "notes": notes,
    }


def latest_phase_summary() -> dict[str, Any]:
    path = latest_file("experiments/*phase-goal-readiness-audit.json")
    payload = read_json(path, {}) if path else {}
    return {
        "path": rel(path),
        "phase_status": payload.get("phase_status") or {},
        "phase2_blocker_count": payload.get("phase2_blocker_count"),
        "phase3_blocker_count": payload.get("phase3_blocker_count"),
    }


def latest_compounding_summary() -> dict[str, Any]:
    path = latest_file("experiments/*binance-api-compounding-flow-audit.json")
    payload = read_json(path, {}) if path else {}
    return {
        "path": rel(path),
        "verdict": payload.get("verdict") or {},
    }


def latest_impulse_summary() -> dict[str, Any]:
    path = latest_file("experiments/*impulse-capture.json")
    payload = read_json(path, {}) if path else {}
    captured_at = payload.get("captured_at")
    return {
        "path": rel(path),
        "captured_at": captured_at,
        "age_hours": artifact_age_hours(captured_at),
        "freshness_status": freshness_status(captured_at),
        "signals": payload.get("signals") or [],
        "anchor": payload.get("anchor") or {},
    }


def latest_recovery_watchlist_summary() -> dict[str, Any]:
    path = latest_file("experiments/*recovery-watchlist-monitor.json")
    payload = read_json(path, {}) if path else {}
    watchlist = payload.get("watchlist") if isinstance(payload.get("watchlist"), list) else []
    dynamic_context = payload.get("dynamic_market_context") if isinstance(payload.get("dynamic_market_context"), dict) else {}
    created_at = payload.get("created_at")
    freshness = freshness_status(created_at)
    artifact_dynamic_status = dynamic_context.get("dynamic_scan_status")
    return {
        "path": rel(path),
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": artifact_age_hours(created_at),
        "freshness_status": freshness,
        "queue_count": payload.get("queue_count", len(watchlist)),
        "artifact_actionable_paper_scout_count": payload.get("actionable_paper_scout_count"),
        "actionable_paper_scout_count": payload.get("actionable_paper_scout_count") if freshness == "fresh" else 0,
        "artifact_dynamic_scan_status": artifact_dynamic_status,
        "dynamic_scan_status": effective_artifact_status(artifact_dynamic_status, created_at),
        "dynamic_scan_reason": dynamic_context.get("dynamic_scan_reason"),
        "data_layer_degraded": dynamic_context.get("data_layer_degraded"),
        "watchlist": watchlist,
    }


def latest_recovery_sampler_summary() -> dict[str, Any]:
    path = latest_file("experiments/*recovery-watchlist-paper-sampler.json")
    payload = read_json(path, {}) if path else {}
    decisions = payload.get("decisions") if isinstance(payload.get("decisions"), list) else []
    created_at = payload.get("created_at")
    return {
        "path": rel(path),
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": artifact_age_hours(created_at),
        "freshness_status": freshness_status(created_at),
        "artifact_status": payload.get("status"),
        "status": effective_artifact_status(payload.get("status"), created_at),
        "opened_count": payload.get("opened_count"),
        "blocked_count": payload.get("blocked_count"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "decisions": decisions,
    }


def latest_pipeline_repair_summary() -> dict[str, Any]:
    path = latest_file("experiments/*pipeline-freshness-repair-runner.json")
    payload = read_json(path, {}) if path else {}
    initial = payload.get("initial_freshness") if isinstance(payload.get("initial_freshness"), dict) else {}
    final = payload.get("final_freshness") if isinstance(payload.get("final_freshness"), dict) else {}
    children = payload.get("children") if isinstance(payload.get("children"), list) else []
    created_at = payload.get("created_at")
    return {
        "path": rel(path),
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": artifact_age_hours(created_at),
        "freshness_status": freshness_status(created_at),
        "artifact_status": payload.get("status"),
        "status": effective_artifact_status(payload.get("status"), created_at),
        "initial_status": initial.get("status"),
        "initial_summary": initial.get("summary") or {},
        "final_status": final.get("status"),
        "final_summary": final.get("summary") or {},
        "repair_plan_count": len(payload.get("repair_plan") or []),
        "child_count": len(children),
        "children": children,
        "ledger_mutated": payload.get("ledger_mutated"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
    }


def latest_blocked_retest_summary() -> dict[str, Any]:
    path = latest_file("experiments/*top-blocked-candidate-retest-lab.json")
    payload = read_json(path, {}) if path else {}
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    created_at = payload.get("created_at")
    return {
        "path": rel(path),
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": artifact_age_hours(created_at),
        "freshness_status": freshness_status(created_at),
        "artifact_status": payload.get("status"),
        "status": effective_artifact_status(payload.get("status"), created_at),
        "candidates_seen": payload.get("candidates_seen", len(results)),
        "quality_scout_review_count": payload.get("quality_scout_review_count"),
        "keep_blocked_count": payload.get("keep_blocked_count"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "results": results,
    }


def latest_blocked_retest_sampler_summary() -> dict[str, Any]:
    path = latest_file("experiments/*top-blocked-retest-quality-scout-sampler.json")
    payload = read_json(path, {}) if path else {}
    decisions = payload.get("decisions") if isinstance(payload.get("decisions"), list) else []
    created_at = payload.get("created_at")
    return {
        "path": rel(path),
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": artifact_age_hours(created_at),
        "freshness_status": freshness_status(created_at),
        "artifact_status": payload.get("status"),
        "status": effective_artifact_status(payload.get("status"), created_at),
        "candidate_count": payload.get("candidate_count", len(decisions)),
        "opened_count": payload.get("opened_count"),
        "blocked_count": payload.get("blocked_count"),
        "ledger_mutated": payload.get("ledger_mutated"),
        "decisions": decisions,
    }


def latest_trade_attribution_summary() -> dict[str, Any]:
    path = latest_file("experiments/*paper-trade-attribution-report.json")
    payload = read_json(path, {}) if path else {}
    return {
        "path": rel(path),
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "summary": payload.get("summary") or {},
        "top_failure_categories": payload.get("top_failure_categories") or [],
        "worst_entry_modes": payload.get("worst_entry_modes") or [],
        "proposed_focus": payload.get("proposed_focus") or [],
        "ledger_mutated": payload.get("ledger_mutated"),
    }


def latest_capital_allocation_summary() -> dict[str, Any]:
    path = latest_file("experiments/*paper-capital-allocation-audit.json")
    payload = read_json(path, {}) if path else {}
    decision = payload.get("allocation_decision") if isinstance(payload.get("allocation_decision"), dict) else {}
    portfolio = payload.get("portfolio") if isinstance(payload.get("portfolio"), dict) else {}
    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
    phase2 = payload.get("phase2_quality_state") if isinstance(payload.get("phase2_quality_state"), dict) else {}
    return {
        "path": rel(path),
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "policy": decision.get("policy"),
        "current_deployment_authorization": decision.get("current_deployment_authorization"),
        "authorization_blockers": decision.get("authorization_blockers") or [],
        "research_max_new_positions": decision.get("research_max_new_positions"),
        "research_per_trade_notional_usd": decision.get("research_per_trade_notional_usd"),
        "research_max_deployable_usd": decision.get("research_max_deployable_usd"),
        "max_new_positions_now": decision.get("max_new_positions_now"),
        "per_trade_notional_usd": decision.get("per_trade_notional_usd"),
        "max_deployable_now_usd": decision.get("max_deployable_now_usd"),
        "reserve_cash_usd": decision.get("reserve_cash_usd"),
        "idle_or_waiting_cash_usd": decision.get("idle_or_waiting_cash_usd"),
        "target_cash_ratio_pct": decision.get("target_cash_ratio_pct"),
        "decision_reasons": decision.get("decision_reasons") or [],
        "current_instruction": decision.get("current_instruction"),
        "cash_ratio_pct": portfolio.get("cash_ratio_pct"),
        "phase2_status": phase2.get("status"),
        "closed_count": quality.get("closed_count"),
        "win_rate_pct": quality.get("win_rate_pct"),
        "realized_pnl_usd": quality.get("realized_pnl_usd"),
        "ledger_mutated": payload.get("ledger_mutated"),
    }


def latest_ledger_integrity_summary() -> dict[str, Any]:
    path = latest_file("experiments/*paper-ledger-integrity-audit.json")
    payload = read_json(path, {}) if path else {}
    audit = payload.get("audit") if isinstance(payload.get("audit"), dict) else {}
    repairs = payload.get("repairs_applied") if isinstance(payload.get("repairs_applied"), list) else []
    return {
        "path": rel(path),
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "status": audit.get("status"),
        "blocked_count": audit.get("blocked_count"),
        "warning_count": audit.get("warning_count"),
        "order_count": audit.get("order_count"),
        "open_position_count": audit.get("open_position_count"),
        "closed_trade_count": audit.get("closed_trade_count"),
        "repairs_applied": len(repairs),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
    }


def latest_binance_market_data_health_summary() -> dict[str, Any]:
    path = latest_file("experiments/*binance-market-data-health.json")
    payload = read_json(path, {}) if path else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    symbols = payload.get("symbols") if isinstance(payload.get("symbols"), list) else []
    endpoint_sources = {
        item.get("symbol"): item.get("endpoint_sources") or {}
        for item in symbols
        if isinstance(item, dict)
    }
    fallback_failure_count = sum(
        int((source or {}).get("fallback_failure_count") or 0)
        for symbol_sources in endpoint_sources.values()
        for source in symbol_sources.values()
        if isinstance(source, dict)
    )
    created_at = payload.get("created_at") or payload.get("generated_at") or payload.get("completed_at")
    artifact_status = summary.get("status")
    data_freshness = freshness_status(created_at)
    effective_status = artifact_status if data_freshness == "fresh" else "stale_not_actionable"
    return {
        "path": rel(path),
        "run_id": payload.get("run_id"),
        "created_at": created_at,
        "age_hours": artifact_age_hours(created_at),
        "freshness_status": data_freshness,
        "artifact_status": artifact_status,
        "status": effective_status,
        "passed_symbol_count": summary.get("passed_symbol_count"),
        "warning_symbol_count": summary.get("warning_symbol_count"),
        "blocked_symbol_count": summary.get("blocked_symbol_count"),
        "base_urls": payload.get("base_urls") or ([payload.get("base_url")] if payload.get("base_url") else []),
        "primary_base_url": (payload.get("base_urls") or [payload.get("base_url") or "-"])[0],
        "endpoint_fallback_failure_count": fallback_failure_count,
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "top_symbols": [
            {
                "symbol": item.get("symbol"),
                "status": item.get("status"),
                "spread_bps": (item.get("depth") or {}).get("spread_bps"),
                "depth_1pct_min_usd": min(
                    (item.get("depth") or {}).get("depth_1pct_bid_usd") or 0.0,
                    (item.get("depth") or {}).get("depth_1pct_ask_usd") or 0.0,
                ),
                "taker_buy_quote_ratio": (item.get("recent_trades") or {}).get("taker_buy_quote_ratio"),
            }
            for item in symbols[:4]
            if isinstance(item, dict)
        ],
    }


def latest_binance_kline_cache_summary() -> dict[str, Any]:
    path = latest_file("experiments/*binance-kline-cache-builder.json")
    payload = read_json(path, {}) if path else {}
    files_written = payload.get("files_written") if isinstance(payload.get("files_written"), list) else []
    discovered = (
        payload.get("top_discovery_ranked")
        or payload.get("top_discovered_symbols")
        or payload.get("top_symbols")
        or []
    )
    if not isinstance(discovered, list):
        discovered = []
    selected_symbols = payload.get("selected_symbols") if isinstance(payload.get("selected_symbols"), list) else []
    intervals = payload.get("requested_intervals") if isinstance(payload.get("requested_intervals"), list) else []
    if not intervals:
        intervals = sorted(
            {
                item.get("interval")
                for item in files_written
                if isinstance(item, dict) and item.get("interval")
            }
        )
    storage = inspect_kline_cache_storage(payload)
    artifact_status = payload.get("status") or "missing"
    effective_status = artifact_status if storage.get("replay_available") else "storage_missing_refresh_required"
    return {
        "path": rel(path),
        "run_id": payload.get("run_id"),
        "generated_at": payload.get("generated_at"),
        "completed_at": payload.get("completed_at"),
        "status": effective_status,
        "artifact_status": artifact_status,
        "cache_dir": storage.get("cache_dir"),
        "cache_dir_exists": storage.get("cache_dir_exists"),
        "storage_status": storage.get("storage_status"),
        "declared_file_count": storage.get("declared_file_count"),
        "actual_file_count": storage.get("actual_file_count"),
        "replay_available": storage.get("replay_available"),
        "refresh_required": storage.get("refresh_required"),
        "selected_symbol_count": len(selected_symbols),
        "selected_symbols": selected_symbols,
        "requested_intervals": intervals,
        "file_count": payload.get("file_count") if payload.get("file_count") is not None else len(files_written),
        "failure_count": payload.get("failure_count"),
        "fallback_event_count": payload.get("fallback_event_count"),
        "binance_public_market_data_only": payload.get("binance_public_market_data_only"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_keys_logged": payload.get("private_api_keys_logged"),
        "top_discovered_symbols": [
            {
                "symbol": item.get("symbol"),
                "score": item.get("score"),
                "price_change_pct_24h": item.get("price_change_pct_24h"),
                "quote_volume_24h": item.get("quote_volume_24h"),
            }
            for item in discovered[:5]
            if isinstance(item, dict)
        ],
    }


def latest_kline_research_reproducibility_summary() -> dict[str, Any]:
    payload = read_json(KLINE_RESEARCH_REPRO_JSON, {})
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "status": payload.get("status") or "missing",
        "created_at": payload.get("created_at"),
        "artifact_count": summary.get("artifact_count"),
        "reproducible_count": summary.get("reproducible_count"),
        "nonreproducible_count": summary.get("nonreproducible_count"),
        "promotion_allowed_count": summary.get("promotion_allowed_count"),
        "max_allowed_action": summary.get("max_allowed_action"),
        "fresh_cache_rebuild_required": summary.get("fresh_cache_rebuild_required"),
        "report": (payload.get("outputs") or {}).get("report"),
    }


def latest_paper_signal_contract_summary() -> dict[str, Any]:
    path = latest_file("experiments/*paper-signal-contract-audit.json")
    payload = read_json(path, {}) if path else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    signals = payload.get("signals") if isinstance(payload.get("signals"), list) else []
    return {
        "path": rel(path),
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "status": payload.get("status"),
        "signal_count": summary.get("signal_count"),
        "complete_count": summary.get("complete_count"),
        "partial_count": summary.get("partial_count"),
        "incomplete_count": summary.get("incomplete_count"),
        "unsafe_count": summary.get("unsafe_count"),
        "missing_field_counts": summary.get("missing_field_counts") or {},
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
        "top_signals": [
            {
                "symbol": item.get("symbol"),
                "type": item.get("signal_type"),
                "status": item.get("status"),
                "action": item.get("action"),
                "confidence": item.get("confidence"),
            }
            for item in signals[:4]
            if isinstance(item, dict)
        ],
    }


def latest_paper_market_context_summary() -> dict[str, Any]:
    path = latest_file("experiments/*paper-market-context-audit.json")
    payload = read_json(path, {}) if path else {}
    summary = payload.get("all_summary") if isinstance(payload.get("all_summary"), dict) else {}
    return {
        "path": rel(path),
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "status": payload.get("status"),
        "trade_count": summary.get("trade_count"),
        "complete_count": summary.get("complete_count"),
        "partial_count": summary.get("partial_count"),
        "missing_count": summary.get("missing_count"),
        "coverage_pct": summary.get("coverage_pct"),
        "explicit_regime_count": summary.get("explicit_regime_count"),
        "top_gaps": summary.get("top_gaps") or [],
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "private_api_used": payload.get("private_api_used"),
    }


def build_payload() -> dict[str, Any]:
    ledger = read_json(LEDGER_PATH, {})
    runner_path, runner_payload = latest_runner_payload()
    rows = closed_trade_rows(ledger if isinstance(ledger, dict) else {})
    wins = len([row for row in rows if row["pnl"] > 0])
    losses = len([row for row in rows if row["pnl"] <= 0])
    monthly = (ledger.get("monthly_goal_baselines") or {}).get(now_local().strftime("%Y-%m"), {})
    if not monthly and isinstance(ledger.get("monthly_goal_baselines"), dict):
        monthly = next(iter((ledger.get("monthly_goal_baselines") or {}).values()), {})
    generated = now_local().isoformat()
    return {
        "generated_at": generated,
        "scope": "paper_only_visual_dashboard",
        "live_orders_enabled": False,
        "private_api_used": False,
        "automation": automation_summary(),
        "automation_recovery": latest_automation_recovery_summary(),
        "ledger": ledger,
        "closed_rows": rows,
        "wins": wins,
        "losses": losses,
        "symbol_rows": aggregate_by_symbol(rows),
        "equity_points": build_equity_points(ledger if isinstance(ledger, dict) else {}),
        "monthly_goal": monthly,
        "runner_path": rel(runner_path),
        "runner": runner_summary(runner_payload if isinstance(runner_payload, dict) else {}),
        "phase": latest_phase_summary(),
        "compounding": latest_compounding_summary(),
        "impulse": latest_impulse_summary(),
        "recovery_watchlist": latest_recovery_watchlist_summary(),
        "recovery_sampler": latest_recovery_sampler_summary(),
        "pipeline_repair": latest_pipeline_repair_summary(),
        "blocked_retest": latest_blocked_retest_summary(),
        "blocked_retest_sampler": latest_blocked_retest_sampler_summary(),
        "trade_attribution": latest_trade_attribution_summary(),
        "capital_allocation": latest_capital_allocation_summary(),
        "ledger_integrity": latest_ledger_integrity_summary(),
        "binance_market_data_health": latest_binance_market_data_health_summary(),
        "binance_kline_cache": latest_binance_kline_cache_summary(),
        "kline_research_reproducibility": latest_kline_research_reproducibility_summary(),
        "paper_signal_contract": latest_paper_signal_contract_summary(),
        "paper_market_context": latest_paper_market_context_summary(),
        "artifact_index": read_json(ARTIFACT_INDEX_JSON, {}),
    }


def card(title: str, value: str, note: str = "", cls: str = "") -> str:
    return f"""
<section class="card {esc(cls)}">
  <div class="label">{esc(title)}</div>
  <div class="value">{esc(value)}</div>
  <div class="note">{esc(note)}</div>
</section>
""".strip()


def render_trade_table(rows: list[dict[str, Any]], limit: int = 12) -> str:
    body = []
    for row in rows[:limit]:
        cls = "good" if row["pnl"] > 0 else "bad"
        body.append(
            "<tr>"
            f"<td>{esc(short_time(row.get('closed_at')))}</td>"
            f"<td><strong>{esc(row.get('symbol'))}</strong><span>{esc(row.get('entry_mode'))}</span></td>"
            f"<td class=\"{cls}\">{money(row.get('pnl'), 3)}</td>"
            f"<td>{pct(row.get('pnl_pct'), 2)}</td>"
            f"<td>{esc(row.get('outcome'))}</td>"
            f"<td>{pct(row.get('max_unrealized'), 2)}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="6">No closed trades</td></tr>'


def render_symbol_table(rows: list[dict[str, Any]], limit: int = 10) -> str:
    body = []
    ordered = sorted(rows, key=lambda item: item["pnl"], reverse=True)
    for row in ordered[:limit]:
        cls = "good" if row["pnl"] > 0 else "bad"
        body.append(
            "<tr>"
            f"<td><strong>{esc(row['symbol'])}</strong></td>"
            f"<td>{row['count']}</td>"
            f"<td>{pct(row['win_rate'], 1)}</td>"
            f"<td class=\"{cls}\">{money(row['pnl'], 3)}</td>"
            f"<td>{money(row['notional'], 0)}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="5">No symbol data</td></tr>'


def render_proposed_changes(rows: list[dict[str, Any]]) -> str:
    body = []
    for item in rows[:6]:
        body.append(
            "<tr>"
            f"<td>{esc(item.get('proposed_change_id'))}</td>"
            f"<td><strong>{esc(item.get('title'))}</strong><span>{esc(item.get('change_type'))}</span></td>"
            f"<td>{esc(item.get('status'))}</td>"
            f"<td>{esc((item.get('proposed_adjustment') or {}).get('action'))}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="4">No proposed changes</td></tr>'


def render_impulse_rows(signals: list[dict[str, Any]]) -> str:
    body = []
    for item in signals[:8]:
        score = item.get("impulse_score_points")
        cls = "good" if isinstance(score, (int, float)) and score >= 70 else "watch"
        body.append(
            "<tr>"
            f"<td><strong>{esc(item.get('symbol'))}</strong></td>"
            f"<td>{esc(item.get('stage'))}</td>"
            f"<td>{esc(item.get('recommended_max_action'))}</td>"
            f"<td class=\"{cls}\">{esc(score)}</td>"
            f"<td>{esc(item.get('volume_multiple_5m_vs_median'))}</td>"
            f"<td>{esc(item.get('taker_buy_ratio_5m'))}</td>"
            f"<td>{esc(item.get('spread_bps'))}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="7">No impulse signals</td></tr>'


def render_blocked_candidate_rows(items: list[dict[str, Any]]) -> str:
    body = []
    for item in items[:8]:
        body.append(
            "<tr>"
            f"<td><strong>{esc(item.get('symbol'))}</strong><span>{esc(item.get('stage'))}</span></td>"
            f"<td>{esc(item.get('interval'))}</td>"
            f"<td>{esc(item.get('strategy_family'))}</td>"
            f"<td>{esc(item.get('entry_mode_estimate'))}</td>"
            f"<td>{esc(item.get('selection_score'))}</td>"
            f"<td>{esc(item.get('oos_win_rate_pct'))}</td>"
            f"<td>{esc(item.get('oos_net_return_pct'))}</td>"
            f"<td>{esc(item.get('primary_block_reason'))}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="8">No blocked candidates</td></tr>'


def render_sample_growth_rows(items: list[dict[str, Any]]) -> str:
    body = []
    for item in items[:5]:
        body.append(
            "<tr>"
            f"<td><strong>{esc(item.get('symbol'))}</strong></td>"
            f"<td>{esc(item.get('block'))}</td>"
            f"<td>{esc(item.get('next_step'))}</td>"
            f"<td>{money(item.get('max_notional'), 2)}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="4">No sample-growth action rows</td></tr>'


def render_trigger_watch_rows(items: list[dict[str, Any]]) -> str:
    body = []
    for item in items[:5]:
        body.append(
            "<tr>"
            f"<td><strong>{esc(item.get('symbol'))}</strong></td>"
            f"<td>{esc(item.get('decision'))}</td>"
            f"<td>{esc(item.get('last_price'))}</td>"
            f"<td>{esc(item.get('trigger'))}</td>"
            f"<td>{esc(item.get('distance'))}</td>"
            f"<td>{esc(item.get('spread_bps'))}</td>"
            f"<td>{esc(item.get('depth'))}</td>"
            f"<td>{esc(item.get('oos_win_rate_pct'))}</td>"
            f"<td>{esc(item.get('oos_net_return_pct'))}</td>"
            f"<td>{esc(', '.join(str(reason) for reason in (item.get('reasons') or [])) or '-')}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="10">No quality scout trigger watch</td></tr>'


def render_recovery_rows(items: list[dict[str, Any]]) -> str:
    body = []
    for item in items[:8]:
        action = str(item.get("recommended_max_action") or "-")
        cls = "good" if "allowed" in action else ("bad" if "blocked" in action else "watch")
        entry = item.get("entry_zone") or {}
        body.append(
            "<tr>"
            f"<td><strong>{esc(item.get('symbol'))}</strong><span>{esc(item.get('strategy_family'))}</span></td>"
            f"<td class=\"{cls}\">{esc(action)}</td>"
            f"<td>{esc(item.get('current_price'))}</td>"
            f"<td>{esc(item.get('spread_bps'))}</td>"
            f"<td>{esc(item.get('book_depth_min_usd_20'))}</td>"
            f"<td>{esc(item.get('recent_taker_buy_quote_ratio'))}</td>"
            f"<td>{esc(item.get('order_book_imbalance_20'))}</td>"
            f"<td>{esc(item.get('realized_volatility_pct'))}</td>"
            f"<td>{esc(entry.get('breakout_confirm_above'))}</td>"
            f"<td>{esc(item.get('stop_loss'))}</td>"
            f"<td>{esc(item.get('forecast_probability_pct'))}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="11">No recovery watchlist candidates</td></tr>'


def render_recovery_sampler_rows(items: list[dict[str, Any]]) -> str:
    body = []
    for item in items[:5]:
        decision = str(item.get("decision") or "-")
        cls = "good" if "opened" in decision else ("bad" if "blocked" in decision else "watch")
        reasons = ", ".join(str(reason) for reason in (item.get("reasons") or [])[:3]) or "-"
        body.append(
            "<tr>"
            f"<td><strong>{esc(item.get('symbol'))}</strong></td>"
            f"<td class=\"{cls}\">{esc(decision)}</td>"
            f"<td>{esc(reasons)}</td>"
            f"<td>{esc(item.get('current_price'))}</td>"
            f"<td>{esc(item.get('trigger'))}</td>"
            f"<td>{esc(item.get('spread_bps'))}</td>"
            f"<td>{esc(item.get('forecast_probability_pct'))}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="7">No recovery sampler decisions</td></tr>'


def render_repair_child_rows(items: list[dict[str, Any]]) -> str:
    body = []
    for item in items[:6]:
        stdout_json = item.get("stdout_json") if isinstance(item.get("stdout_json"), dict) else {}
        body.append(
            "<tr>"
            f"<td>{esc(item.get('label'))}</td>"
            f"<td>{esc(item.get('status'))}</td>"
            f"<td>{esc(stdout_json.get('opened_count', '-'))}</td>"
            f"<td>{esc(stdout_json.get('blocked_count', '-'))}</td>"
            f"<td>{esc(stdout_json.get('ledger_mutated', '-'))}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="5">No repair child runs</td></tr>'


def render_blocked_retest_rows(items: list[dict[str, Any]]) -> str:
    body = []
    ordered = sorted(
        [item for item in items if isinstance(item, dict)],
        key=lambda item: (
            item.get("decision") != "candidate_for_min_quality_scout_after_current_signal",
            -float(((item.get("best_variant") or {}).get("oos") or {}).get("net_return_pct") or 0.0),
        ),
    )
    for item in ordered[:8]:
        best = item.get("best_variant") or {}
        oos = best.get("oos") or {}
        setup = item.get("current_setup") or {}
        decision = str(item.get("decision") or "-")
        cls = "good" if decision == "candidate_for_min_quality_scout_after_current_signal" else "watch"
        body.append(
            "<tr>"
            f"<td><strong>{esc(item.get('symbol'))}</strong><span>{esc(item.get('source_block_reason'))}</span></td>"
            f"<td>{esc(item.get('interval'))}</td>"
            f"<td>{esc(best.get('variant'))}</td>"
            f"<td>{esc(oos.get('trade_count'))}</td>"
            f"<td>{esc(oos.get('win_rate_pct'))}</td>"
            f"<td>{esc(oos.get('net_return_pct'))}</td>"
            f"<td>{esc(setup.get('distance_to_breakout_pct'))}</td>"
            f"<td class=\"{cls}\">{esc(decision)}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="8">No blocked retest results</td></tr>'


def render_blocked_retest_sampler_rows(items: list[dict[str, Any]]) -> str:
    body = []
    for item in items[:5]:
        decision = str(item.get("decision") or "-")
        cls = "good" if "opened" in decision else ("bad" if "blocked" in decision else "watch")
        reasons = ", ".join(str(reason) for reason in (item.get("reasons") or [])[:3]) or "-"
        oos = item.get("oos") or {}
        body.append(
            "<tr>"
            f"<td><strong>{esc(item.get('symbol'))}</strong><span>{esc(item.get('best_variant'))}</span></td>"
            f"<td class=\"{cls}\">{esc(decision)}</td>"
            f"<td>{esc(reasons)}</td>"
            f"<td>{esc(item.get('last_price'))}</td>"
            f"<td>{esc(item.get('breakout_level'))}</td>"
            f"<td>{esc(item.get('spread_bps'))}</td>"
            f"<td>{esc(item.get('depth'))}</td>"
            f"<td>{esc(oos.get('win_rate_pct'))}</td>"
            f"<td>{esc(oos.get('net_return_pct'))}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="9">No quality scout sampler decisions</td></tr>'


def render_attribution_category_rows(items: list[dict[str, Any]]) -> str:
    body = []
    for item in items[:8]:
        cls = "bad" if float(item.get("net_pnl_usd") or 0.0) < 0 else "good"
        body.append(
            "<tr>"
            f"<td><strong>{esc(item.get('category'))}</strong></td>"
            f"<td>{esc(item.get('count'))}</td>"
            f"<td class=\"{cls}\">{money(item.get('net_pnl_usd'), 3)}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="3">No attribution categories</td></tr>'


def render_attribution_entry_mode_rows(items: list[dict[str, Any]]) -> str:
    body = []
    for item in items[:6]:
        cls = "bad" if float(item.get("net_pnl_usd") or 0.0) < 0 else "good"
        body.append(
            "<tr>"
            f"<td><strong>{esc(item.get('paper_entry_mode'))}</strong></td>"
            f"<td>{esc(item.get('trade_count'))}</td>"
            f"<td>{pct(item.get('win_rate_pct'), 1)}</td>"
            f"<td class=\"{cls}\">{money(item.get('net_pnl_usd'), 3)}</td>"
            "</tr>"
        )
    return "\n".join(body) or '<tr><td colspan="4">No entry-mode attribution</td></tr>'


def render_html(payload: dict[str, Any]) -> str:
    ledger = payload["ledger"]
    runner = payload["runner"]
    phase = payload["phase"]["phase_status"]
    verdict = payload["compounding"]["verdict"]
    monthly = payload["monthly_goal"] or {}
    closed_rows = payload["closed_rows"]
    equity_points = payload["equity_points"]
    recent_pnl_rows = [(short_time(row.get("closed_at")), float(row.get("pnl") or 0.0)) for row in list(reversed(closed_rows[:18]))]
    initial_capital = float(ledger.get("initial_capital_usd") or 500.0)
    equity = float(ledger.get("equity_usd") or 0.0)
    target = float(monthly.get("target_equity_usd") or initial_capital * 2)
    progress = max(0.0, min(100.0, (equity / target * 100) if target else 0.0))
    win_total = max(1, payload["wins"] + payload["losses"])
    win_pct = payload["wins"] / win_total * 100
    loss_pct = 100 - win_pct
    artifacts = payload.get("artifact_index") or {}
    dirs = artifacts.get("directories") or {}
    report_count = ((dirs.get("reports") or {}).get("file_count"))
    experiment_count = ((dirs.get("experiments") or {}).get("file_count"))
    selected_symbols = ", ".join(runner.get("selected_symbols") or []) or "-"
    top_symbols = ", ".join(runner.get("current_top_symbols") or []) or "-"
    cached_selected_symbols = ", ".join(runner.get("cached_selected_symbols") or []) or "-"
    cached_top_symbols = ", ".join(runner.get("cached_top_symbols") or []) or "-"
    runner_freshness_class = "safe" if runner.get("freshness_status") == "fresh" else "risk"
    no_entry = runner.get("no_entry") or {}
    no_entry_reasons = no_entry.get("reason_counts") or {}
    recovery = payload.get("recovery_watchlist") or {}
    recovery_rows = recovery.get("watchlist") or []
    recovery_sampler = payload.get("recovery_sampler") or {}
    recovery_sampler_rows = recovery_sampler.get("decisions") or []
    pipeline_repair = payload.get("pipeline_repair") or {}
    blocked_retest = payload.get("blocked_retest") or {}
    blocked_retest_rows = blocked_retest.get("results") or []
    blocked_retest_sampler = payload.get("blocked_retest_sampler") or {}
    blocked_retest_sampler_rows = blocked_retest_sampler.get("decisions") or []
    trade_attribution = payload.get("trade_attribution") or {}
    attribution_summary = trade_attribution.get("summary") or {}
    capital_allocation = payload.get("capital_allocation") or {}
    automation_recovery = payload.get("automation_recovery") or {}
    ledger_integrity = payload.get("ledger_integrity") or {}
    binance_health = payload.get("binance_market_data_health") or {}
    binance_kline_cache = payload.get("binance_kline_cache") or {}
    kline_research_repro = payload.get("kline_research_reproducibility") or {}
    signal_contract = payload.get("paper_signal_contract") or {}
    market_context = payload.get("paper_market_context") or {}
    sample_growth = sample_growth_action_board(payload)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Active Alpha Paper Dashboard</title>
  <style>
    :root {{
      --bg: #f4f6f8;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #657381;
      --line: #dce2e8;
      --good: #108a55;
      --bad: #c73e3a;
      --warn: #a86b00;
      --blue: #2f7dd1;
      --soft-blue: #e9f2ff;
      --soft-green: #e8f7ef;
      --soft-red: #fdeceb;
      --shadow: 0 12px 32px rgba(28, 38, 49, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    header {{
      padding: 28px 32px 18px;
      background: #101820;
      color: white;
    }}
    header h1 {{ margin: 0 0 10px; font-size: 28px; line-height: 1.2; }}
    header p {{ margin: 0; color: #b9c5d0; }}
    main {{ padding: 24px 32px 40px; max-width: 1480px; margin: 0 auto; }}
    .grid {{ display: grid; gap: 16px; }}
    .kpis {{ grid-template-columns: repeat(6, minmax(150px, 1fr)); margin-bottom: 18px; }}
    .two {{ grid-template-columns: minmax(0, 1.4fr) minmax(360px, 0.8fr); }}
    .three {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .card, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      box-shadow: var(--shadow);
    }}
    .card {{ padding: 16px; min-height: 114px; }}
    .label {{ color: var(--muted); font-size: 13px; margin-bottom: 8px; }}
    .value {{ font-size: 26px; font-weight: 740; line-height: 1.12; }}
    .note {{ color: var(--muted); font-size: 12px; margin-top: 8px; line-height: 1.35; }}
    .panel {{ padding: 18px; margin-bottom: 16px; overflow: hidden; }}
    .panel h2 {{ margin: 0 0 12px; font-size: 18px; }}
    .panel h3 {{ margin: 0 0 8px; font-size: 14px; color: var(--muted); }}
    .chart {{ width: 100%; height: auto; display: block; }}
    .grid .grid {{ box-shadow: none; }}
    .grid line.grid {{ stroke: #edf1f5; }}
    .baseline {{ stroke: #9aa8b5; stroke-dasharray: 5 5; }}
    .axis {{ fill: #72808d; font-size: 12px; }}
    .bar-positive {{ fill: var(--good); opacity: 0.85; }}
    .bar-negative {{ fill: var(--bad); opacity: 0.82; }}
    .progress-track {{ height: 14px; background: #e7ecf2; border-radius: 999px; overflow: hidden; }}
    .progress-fill {{ height: 100%; width: {progress:.2f}%; background: linear-gradient(90deg, #2f7dd1, #2bb673); }}
    .donut {{
      width: 148px; height: 148px; border-radius: 50%;
      background: conic-gradient(var(--good) 0 {win_pct:.2f}%, var(--bad) {win_pct:.2f}% 100%);
      display: grid; place-items: center; margin: 12px auto;
    }}
    .donut div {{
      width: 102px; height: 102px; border-radius: 50%; background: white;
      display: grid; place-items: center; text-align: center; font-weight: 760;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 9px 8px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 650; background: #f8fafc; }}
    td span {{ display: block; color: var(--muted); font-size: 12px; margin-top: 3px; }}
    .good {{ color: var(--good); font-weight: 700; }}
    .bad {{ color: var(--bad); font-weight: 700; }}
    .watch {{ color: var(--warn); font-weight: 700; }}
    .pillrow {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .pill {{
      border: 1px solid var(--line); background: #f8fafc; border-radius: 999px;
      padding: 6px 10px; font-size: 12px; color: #344351;
    }}
    .callout {{ background: var(--soft-blue); border-left: 4px solid var(--blue); padding: 12px 14px; border-radius: 6px; color: #23384c; }}
    .risk {{ background: var(--soft-red); border-left-color: var(--bad); }}
    .safe {{ background: var(--soft-green); border-left-color: var(--good); }}
    footer {{ color: var(--muted); font-size: 12px; padding: 8px 32px 32px; max-width: 1480px; margin: 0 auto; }}
    @media (max-width: 1100px) {{
      .kpis {{ grid-template-columns: repeat(3, minmax(150px, 1fr)); }}
      .two, .three {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 700px) {{
      header, main {{ padding-left: 16px; padding-right: 16px; }}
      .kpis {{ grid-template-columns: 1fr 1fr; }}
      .value {{ font-size: 22px; }}
      table {{ font-size: 12px; }}
    }}
  </style>
</head>
<body>
<header>
  <h1>Active Alpha Paper Dashboard</h1>
  <p>本地 Binance crypto paper 交易系统可视化看板 · 生成时间 {esc(payload["generated_at"])} · automation {esc((payload.get("automation") or {}).get("status"))} · live orders blocked</p>
</header>
<main>
  <section class="panel">
    <h2>Automation Recovery</h2>
    <div class="callout {"safe" if automation_recovery.get("status") in {"safe_existing_paused", "safe_existing_active"} else "risk"}">
      Status {esc(automation_recovery.get("status"))} · reason {esc(automation_recovery.get("reason"))} · count {esc(automation_recovery.get("automation_count"))} · explicit approval {esc(automation_recovery.get("requires_explicit_user_approval"))} · max action {esc(automation_recovery.get("max_allowed_action"))} · mutated {esc(automation_recovery.get("automation_mutated"))}
    </div>
    <p class="note">Proposed initial status {esc(automation_recovery.get("proposed_initial_status"))} · prompt hash {esc(automation_recovery.get("prompt_sha256"))}</p>
    <p class="note">Next: {esc(automation_recovery.get("next_action"))}</p>
  </section>
  <section class="grid kpis">
    {card("Paper Equity", money(equity, 3), f"Initial {money(initial_capital, 0)} / cash {money(ledger.get('cash_usd'), 3)}")}
    {card("Net Return", pct(ledger.get("net_return_pct"), 2), f"Realized PnL {money(sum(row['pnl'] for row in closed_rows), 3)}", "bad" if float(ledger.get("net_return_pct") or 0) < 0 else "good")}
    {card("Win Rate", pct((payload["wins"] / max(1, payload["wins"] + payload["losses"]) * 100), 2), f"{payload['wins']} wins / {payload['losses']} losses")}
    {card("Closed Trades", str(len(closed_rows)), "Phase 2 target: 30-50 closed trades")}
    {card("Max Drawdown", pct(ledger.get("max_drawdown_pct"), 2), "Target control: within -10% to -15%")}
    {card("Open Positions", str(len(ledger.get("open_positions") or [])), "No real orders; paper only")}
  </section>

  <section class="grid two">
    <div class="panel">
      <h2>Equity Trend</h2>
      {svg_line_chart(equity_points, baseline=initial_capital)}
    </div>
    <div class="panel">
      <h2>Monthly Double Progress</h2>
      <div class="progress-track"><div class="progress-fill"></div></div>
      <p class="note">Current {money(equity, 3)} / Target {money(target, 2)} · required from current {pct(((target / equity - 1) * 100) if equity else None, 2)}</p>
      <div class="donut"><div>{pct(win_pct, 1)}<br/><span class="note">win rate</span></div></div>
      <p class="note">Wins {payload["wins"]} · Losses {payload["losses"]} · Loss share {pct(loss_pct, 1)}</p>
    </div>
  </section>

  <section class="grid two">
    <div class="panel">
      <h2>Recent Trade PnL</h2>
      {svg_bar_chart(recent_pnl_rows)}
    </div>
    <div class="panel">
      <h2>Phase & Safety</h2>
      <div class="callout safe">
        Phase 1: {esc(phase.get("phase1_paper_execution_loop"))} · Phase 2: {esc(phase.get("phase2_positive_expectancy_proof"))} · Phase 3: {esc(phase.get("phase3_monthly_double_pressure_test") or phase.get("phase3_testnet_or_tiny_live_readiness"))} · Phase 4: {esc(phase.get("phase4_live_or_testnet_candidate"))}
      </div>
      <p class="note">Compounding: {esc(verdict.get("overall_status"))}</p>
      <p class="note">Max allowed action: {esc(verdict.get("max_allowed_action"))}</p>
      <div class="callout risk">Blockers: {esc(", ".join(verdict.get("blockers") or []))}</div>
    </div>
  </section>

  <section class="grid two">
    <div class="panel">
      <h2>Cached Runner Evidence</h2>
      <div class="pillrow">
        <span class="pill">Run {esc(runner.get("run_id"))}</span>
        <span class="pill">Regime {esc(runner.get("market_regime"))}</span>
        <span class="pill">Atmosphere {esc(runner.get("market_atmosphere"))}</span>
        <span class="pill">Sentiment {esc(runner.get("sentiment_state"))}</span>
      </div>
      <div class="callout {runner_freshness_class}">Runner freshness {esc(runner.get("freshness_status"))} · age {esc(runner.get("age_hours"))}h · generated {esc(runner.get("created_at"))}</div>
      <p class="note">Current-signal state {esc(runner.get("current_signal_state"))} · freshness {esc(runner.get("current_signal_freshness_status"))} · age {esc(runner.get("current_signal_age_hours"))}h · generated {esc(runner.get("current_signal_generated_at"))}</p>
      <p class="note">Frames {esc(runner.get("current_signal_frames"))}, strategies {esc(runner.get("current_signal_strategies"))}, candidates {esc(runner.get("current_signal_candidates"))}</p>
      <h3>Current top symbols</h3>
      <p>{esc(top_symbols)}</p>
      <h3>Historical cached top symbols</h3>
      <p class="note">{esc(cached_top_symbols)}</p>
      <h3>Current selected scan pool</h3>
      <p class="note">{esc(selected_symbols)}</p>
      <h3>Historical cached scan pool</h3>
      <p class="note">{esc(cached_selected_symbols)}</p>
      <div class="callout">No-entry reasons: {esc(no_entry_reasons)}</div>
    </div>
    <div class="panel">
      <h2>Strategy Iteration Backlog</h2>
      <table>
        <thead><tr><th>ID</th><th>Proposal</th><th>Status</th><th>Action</th></tr></thead>
        <tbody>{render_proposed_changes(runner.get("proposed_changes") or [])}</tbody>
      </table>
    </div>
  </section>

  <section class="panel">
    <h2>Sample Growth Action Board</h2>
    <div class="callout">
      Status {esc(sample_growth.get("status"))} · closed-trade gap {esc(sample_growth.get("closed_trade_gap"))} · policy {esc(sample_growth.get("capital_policy"))} · deployable {money(sample_growth.get("max_deployable"), 2)} · per trade {money(sample_growth.get("per_trade"), 2)}
    </div>
    <table>
      <thead><tr><th>Symbol</th><th>Current Block</th><th>Next Evidence Step</th><th>Max Paper Notional</th></tr></thead>
      <tbody>{render_sample_growth_rows(sample_growth.get("actions") or [])}</tbody>
    </table>
    <h3>Quality Scout Trigger Watch</h3>
    <table>
      <thead><tr><th>Symbol</th><th>Decision</th><th>Last</th><th>Trigger</th><th>Distance</th><th>Spread bps</th><th>Depth USD</th><th>OOS Win %</th><th>OOS Net %</th><th>Reasons</th></tr></thead>
      <tbody>{render_trigger_watch_rows(sample_growth.get("trigger_watch") or [])}</tbody>
    </table>
    <p class="note">Reasons: {esc(sample_growth.get("reason_counts"))}</p>
    <p class="note">Notes: {esc("; ".join(sample_growth.get("notes") or []))}</p>
  </section>

  <section class="panel">
    <h2>Top Blocked Candidates</h2>
    <div class="callout">
      Paper-only diagnosis · these candidates were scanned but blocked by validation recovery or entry gates.
    </div>
    <table>
      <thead><tr><th>Symbol</th><th>Interval</th><th>Family</th><th>Entry Mode</th><th>Score</th><th>OOS Win %</th><th>OOS Net %</th><th>Block Reason</th></tr></thead>
      <tbody>{render_blocked_candidate_rows((runner.get("no_entry") or {}).get("top_blocked_candidates") or [])}</tbody>
    </table>
  </section>

  <section class="grid two">
    <div class="panel">
      <h2>Recent Closed Trades</h2>
      <table>
        <thead><tr><th>Closed</th><th>Trade</th><th>Net PnL</th><th>PnL %</th><th>Outcome</th><th>Max Float</th></tr></thead>
        <tbody>{render_trade_table(closed_rows)}</tbody>
      </table>
    </div>
    <div class="panel">
      <h2>Symbol Performance</h2>
      <table>
        <thead><tr><th>Symbol</th><th>Trades</th><th>Win Rate</th><th>Net PnL</th><th>Notional</th></tr></thead>
        <tbody>{render_symbol_table(payload["symbol_rows"])}</tbody>
      </table>
    </div>
  </section>

  <section class="panel">
    <h2>{"Current" if payload["impulse"].get("freshness_status") == "fresh" else "Historical Cached"} Impulse / Watch Signals</h2>
    <div class="callout {"safe" if payload["impulse"].get("freshness_status") == "fresh" else "risk"}">Freshness {esc(payload["impulse"].get("freshness_status"))} · age {esc(payload["impulse"].get("age_hours"))}h · captured {esc(payload["impulse"].get("captured_at"))}</div>
    <table>
      <thead><tr><th>Symbol</th><th>Stage</th><th>Action</th><th>Score</th><th>5m Vol x</th><th>Buy Ratio</th><th>Spread bps</th></tr></thead>
      <tbody>{render_impulse_rows(payload["impulse"].get("signals") or [])}</tbody>
    </table>
  </section>

  <section class="panel">
    <h2>{"Current" if recovery.get("freshness_status") == "fresh" else "Historical Cached"} Recovery Watchlist</h2>
    <div class="callout {"safe" if recovery.get("freshness_status") == "fresh" else "risk"}">
      Freshness {esc(recovery.get("freshness_status"))} · age {esc(recovery.get("age_hours"))}h · conditional paper-only triggers · queue {esc(recovery.get("queue_count"))} · actionable scouts {esc(recovery.get("actionable_paper_scout_count"))} · data {esc(recovery.get("dynamic_scan_status"))} · degraded {esc(recovery.get("data_layer_degraded"))} · reason {esc(recovery.get("dynamic_scan_reason"))} · source {esc(recovery.get("path"))}
    </div>
    <table>
      <thead><tr><th>Symbol</th><th>Action</th><th>Price</th><th>Spread bps</th><th>Depth USD</th><th>Buy Ratio</th><th>Imbalance</th><th>Vol %</th><th>Trigger</th><th>Stop</th><th>Prob.</th></tr></thead>
      <tbody>{render_recovery_rows(recovery_rows)}</tbody>
    </table>
  </section>

  <section class="panel">
    <h2>{"Current" if recovery_sampler.get("freshness_status") == "fresh" else "Historical Cached"} Recovery Paper Sampler</h2>
    <div class="callout {"safe" if recovery_sampler.get("freshness_status") == "fresh" else "risk"}">
      Freshness {esc(recovery_sampler.get("freshness_status"))} · age {esc(recovery_sampler.get("age_hours"))}h · paper-only execution bridge · status {esc(recovery_sampler.get("status"))} · opened {esc(recovery_sampler.get("opened_count"))} · blocked {esc(recovery_sampler.get("blocked_count"))} · ledger mutated {esc(recovery_sampler.get("ledger_mutated"))}
    </div>
    <table>
      <thead><tr><th>Symbol</th><th>Decision</th><th>Reasons</th><th>Price</th><th>Trigger</th><th>Spread bps</th><th>Prob.</th></tr></thead>
      <tbody>{render_recovery_sampler_rows(recovery_sampler_rows)}</tbody>
    </table>
  </section>

  <section class="panel">
    <h2>{"Current" if pipeline_repair.get("freshness_status") == "fresh" else "Historical Cached"} Pipeline Freshness Repair</h2>
    <div class="callout {"safe" if pipeline_repair.get("freshness_status") == "fresh" else "risk"}">
      Freshness {esc(pipeline_repair.get("freshness_status"))} · age {esc(pipeline_repair.get("age_hours"))}h · auto repair bridge · status {esc(pipeline_repair.get("status"))} · initial {esc(pipeline_repair.get("initial_status"))} · final {esc(pipeline_repair.get("final_status"))} · repair plan {esc(pipeline_repair.get("repair_plan_count"))} · children {esc(pipeline_repair.get("child_count"))} · ledger mutated {esc(pipeline_repair.get("ledger_mutated"))}
    </div>
    <div class="grid three">
      {card("Freshness Before", esc(pipeline_repair.get("initial_status")), f"stale {(pipeline_repair.get('initial_summary') or {}).get('stale_count')} · missing {(pipeline_repair.get('initial_summary') or {}).get('missing_count')}")}
      {card("Freshness After", esc(pipeline_repair.get("final_status")), f"stale {(pipeline_repair.get('final_summary') or {}).get('stale_count')} · missing {(pipeline_repair.get('final_summary') or {}).get('missing_count')}")}
      {card("Safety", f"live {pipeline_repair.get('live_orders_enabled')}", f"private API {pipeline_repair.get('private_api_used')}")}
    </div>
    <table>
      <thead><tr><th>Child</th><th>Status</th><th>Opened</th><th>Blocked</th><th>Ledger Mutated</th></tr></thead>
      <tbody>{render_repair_child_rows(pipeline_repair.get("children") or [])}</tbody>
    </table>
  </section>

  <section class="panel">
    <h2>{"Current" if blocked_retest.get("freshness_status") == "fresh" else "Historical Cached"} Top Blocked Retest Lab</h2>
    <div class="callout {"safe" if blocked_retest.get("freshness_status") == "fresh" else "risk"}">
      Freshness {esc(blocked_retest.get("freshness_status"))} · age {esc(blocked_retest.get("age_hours"))}h · status {esc(blocked_retest.get("status"))} · research-only retest · candidates {esc(blocked_retest.get("candidates_seen"))} · quality scout review {esc(blocked_retest.get("quality_scout_review_count"))} · keep blocked {esc(blocked_retest.get("keep_blocked_count"))} · ledger mutated {esc(blocked_retest.get("ledger_mutated"))}
    </div>
    <table>
      <thead><tr><th>Symbol</th><th>Interval</th><th>Best Variant</th><th>OOS Trades</th><th>OOS Win %</th><th>OOS Net %</th><th>Dist. Breakout %</th><th>Decision</th></tr></thead>
      <tbody>{render_blocked_retest_rows(blocked_retest_rows)}</tbody>
    </table>
  </section>

  <section class="panel">
    <h2>{"Current" if blocked_retest_sampler.get("freshness_status") == "fresh" else "Historical Cached"} Top Blocked Retest Quality Scout Sampler</h2>
    <div class="callout {"safe" if blocked_retest_sampler.get("freshness_status") == "fresh" else "risk"}">
      Freshness {esc(blocked_retest_sampler.get("freshness_status"))} · age {esc(blocked_retest_sampler.get("age_hours"))}h · status {esc(blocked_retest_sampler.get("status"))} · paper-only execution bridge · candidates {esc(blocked_retest_sampler.get("candidate_count"))} · opened {esc(blocked_retest_sampler.get("opened_count"))} · blocked {esc(blocked_retest_sampler.get("blocked_count"))} · ledger mutated {esc(blocked_retest_sampler.get("ledger_mutated"))}
    </div>
    <table>
      <thead><tr><th>Symbol</th><th>Decision</th><th>Reasons</th><th>Price</th><th>Breakout</th><th>Spread bps</th><th>Depth USD</th><th>OOS Win %</th><th>OOS Net %</th></tr></thead>
      <tbody>{render_blocked_retest_sampler_rows(blocked_retest_sampler_rows)}</tbody>
    </table>
  </section>

  <section class="panel">
    <h2>Paper Capital Allocation</h2>
    <div class="callout">
      Policy {esc(capital_allocation.get("policy"))} · authorization {esc(capital_allocation.get("current_deployment_authorization"))} · deployable now {money(capital_allocation.get("max_deployable_now_usd"), 2)} · conditional research capacity {money(capital_allocation.get("research_max_deployable_usd"), 2)} · research trade size {money(capital_allocation.get("research_per_trade_notional_usd"), 2)} · idle/waiting {money(capital_allocation.get("idle_or_waiting_cash_usd"), 2)} · ledger mutated {esc(capital_allocation.get("ledger_mutated"))}
    </div>
    <div class="grid three">
      {card("Cash Ratio", pct(capital_allocation.get("cash_ratio_pct"), 2), f"target {pct(capital_allocation.get('target_cash_ratio_pct'), 2)}")}
      {card("Phase 2 State", esc(capital_allocation.get("phase2_status")), f"closed {capital_allocation.get('closed_count')} · win {capital_allocation.get('win_rate_pct')}%")}
      {card("Current / Research Slots", f"{esc(capital_allocation.get('max_new_positions_now'))} / {esc(capital_allocation.get('research_max_new_positions'))}", esc(capital_allocation.get("current_instruction")))}
    </div>
    <p class="note">Authorization blockers: {esc(', '.join(capital_allocation.get("authorization_blockers") or ['none']))}</p>
    <p class="note">Reasons: {esc(', '.join(capital_allocation.get("decision_reasons") or ['missing_capital_allocation_audit']))}</p>
  </section>

  <section class="panel">
    <h2>Paper Ledger Integrity</h2>
    <div class="callout">
      Status {esc(ledger_integrity.get("status"))} · orders {esc(ledger_integrity.get("order_count"))} · closed {esc(ledger_integrity.get("closed_trade_count"))} · warnings {esc(ledger_integrity.get("warning_count"))} · blocked {esc(ledger_integrity.get("blocked_count"))}
    </div>
    <div class="grid three">
      {card("Order Coverage", esc(ledger_integrity.get("order_count")), f"open {ledger_integrity.get('open_position_count')} · closed {ledger_integrity.get('closed_trade_count')}")}
      {card("Safety Flags", f"live {ledger_integrity.get('live_orders_enabled')}", f"private API {ledger_integrity.get('private_api_used')}")}
      {card("Repairs", esc(ledger_integrity.get("repairs_applied")), esc(ledger_integrity.get("created_at")))}
    </div>
  </section>

  <section class="panel">
    <h2>Paper Market Context</h2>
    <div class="callout">
      Status {esc(market_context.get("status"))} · coverage {pct(market_context.get("coverage_pct"), 2)} · complete/partial/missing {esc(market_context.get("complete_count"))}/{esc(market_context.get("partial_count"))}/{esc(market_context.get("missing_count"))} · regimes {esc(market_context.get("explicit_regime_count"))}
    </div>
    <div class="grid three">
      {card("Context Coverage", pct(market_context.get("coverage_pct"), 2), f"trades {market_context.get('trade_count')}")}
      {card("Explicit Regimes", esc(market_context.get("explicit_regime_count")), esc(market_context.get("created_at")))}
      {card("Safety", f"live {market_context.get('live_orders_enabled')}", f"private API {market_context.get('private_api_used')}")}
    </div>
  </section>

  <section class="panel">
    <h2>Binance Market Data Health</h2>
    <div class="callout">
      Status {esc(binance_health.get("status"))} · passed/warning/blocked {esc(binance_health.get("passed_symbol_count"))}/{esc(binance_health.get("warning_symbol_count"))}/{esc(binance_health.get("blocked_symbol_count"))} · hosts {esc(len(binance_health.get("base_urls") or []))} · primary {esc(binance_health.get("primary_base_url"))} · fallback failures {esc(binance_health.get("endpoint_fallback_failure_count"))} · live {esc(binance_health.get("live_orders_enabled"))} · private {esc(binance_health.get("private_api_used"))}
    </div>
    <div class="grid three">
      {card("Data Status", esc(binance_health.get("status")), esc(binance_health.get("created_at")))}
      {card("Symbols", f"{binance_health.get('passed_symbol_count')}/{binance_health.get('warning_symbol_count')}/{binance_health.get('blocked_symbol_count')}", "pass / warn / block")}
      {card("Safety", f"live {binance_health.get('live_orders_enabled')}", f"private API {binance_health.get('private_api_used')}")}
    </div>
    <p class="note">Core: {esc(', '.join(f"{item.get('symbol')}:{item.get('status')} spread {item.get('spread_bps')} buy {item.get('taker_buy_quote_ratio')}" for item in (binance_health.get('top_symbols') or [])) or '-')}</p>
  </section>

  <section class="panel">
    <h2>Binance Kline Cache</h2>
    <div class="callout {"safe" if binance_kline_cache.get("replay_available") else "risk"}">
      Status {esc(binance_kline_cache.get("status"))} · storage {esc(binance_kline_cache.get("storage_status"))} · declared/actual files {esc(binance_kline_cache.get("declared_file_count"))}/{esc(binance_kline_cache.get("actual_file_count"))} · refresh required {esc(binance_kline_cache.get("refresh_required"))} · live {esc(binance_kline_cache.get("live_orders_enabled"))}
    </div>
    <div class="grid three">
      {card("Kline Files", esc(binance_kline_cache.get("actual_file_count")), f"declared {binance_kline_cache.get('declared_file_count')} · {binance_kline_cache.get('selected_symbol_count')} symbols")}
      {card("Intervals", esc(", ".join(binance_kline_cache.get("requested_intervals") or []) or "-"), esc(binance_kline_cache.get("completed_at") or binance_kline_cache.get("generated_at")))}
      {card("Safety", f"live {binance_kline_cache.get('live_orders_enabled')}", f"keys logged {binance_kline_cache.get('private_api_keys_logged')}")}
    </div>
    <p class="note">Selected: {esc(", ".join((binance_kline_cache.get("selected_symbols") or [])[:12]) or "-")}</p>
    <p class="note">Discovery: {esc(", ".join(f"{item.get('symbol')}:{item.get('score')}" for item in (binance_kline_cache.get("top_discovered_symbols") or [])) or "-")}</p>
  </section>

  <section class="panel">
    <h2>Kline Research Reproducibility</h2>
    <div class="callout {"safe" if kline_research_repro.get("status") == "pass" else "risk"}">
      Status {esc(kline_research_repro.get("status"))} · reproducible/nonreproducible {esc(kline_research_repro.get("reproducible_count"))}/{esc(kline_research_repro.get("nonreproducible_count"))} · promotion allowed {esc(kline_research_repro.get("promotion_allowed_count"))} · max action {esc(kline_research_repro.get("max_allowed_action"))}
    </div>
    <div class="grid three">
      {card("Research Artifacts", esc(kline_research_repro.get("artifact_count")), esc(kline_research_repro.get("created_at")))}
      {card("Promotion Allowed", esc(kline_research_repro.get("promotion_allowed_count")), "raw Kline replay required")}
      {card("Cache Rebuild", esc(kline_research_repro.get("fresh_cache_rebuild_required")), esc(kline_research_repro.get("report")))}
    </div>
  </section>

  <section class="panel">
    <h2>Paper Signal Contract</h2>
    <div class="callout">
      Status {esc(signal_contract.get("status"))} · complete/partial/incomplete {esc(signal_contract.get("complete_count"))}/{esc(signal_contract.get("partial_count"))}/{esc(signal_contract.get("incomplete_count"))} · unsafe {esc(signal_contract.get("unsafe_count"))}
    </div>
    <div class="grid three">
      {card("Signals", esc(signal_contract.get("signal_count")), esc(signal_contract.get("created_at")))}
      {card("Completeness", f"{signal_contract.get('complete_count')}/{signal_contract.get('partial_count')}/{signal_contract.get('incomplete_count')}", "complete / partial / incomplete")}
      {card("Safety", f"live {signal_contract.get('live_orders_enabled')}", f"private API {signal_contract.get('private_api_used')}")}
    </div>
    <p class="note">Top: {esc(', '.join(f"{item.get('symbol')}:{item.get('status')} {item.get('action')}" for item in (signal_contract.get('top_signals') or [])) or '-')}</p>
  </section>

  <section class="grid two">
    <div class="panel">
      <h2>Paper Trade Attribution</h2>
      <div class="callout">
        Closed {esc(attribution_summary.get("closed_trades_in_window"))} · win {esc(attribution_summary.get("win_rate_pct"))}% · net {money(attribution_summary.get("net_pnl_usd"), 3)} · friction {money(attribution_summary.get("total_friction_usd"), 3)} · missed protection {esc(attribution_summary.get("missed_profit_protection_count"))} · timing {esc(attribution_summary.get("entry_timing_issue_count"))}/{esc(attribution_summary.get("exit_timing_issue_count"))} · regime {esc(attribution_summary.get("market_regime_context_missing_count"))}/{esc(attribution_summary.get("market_regime_misread_count"))} · exec cost {esc(attribution_summary.get("execution_cost_visible_count"))}
      </div>
      <table>
        <thead><tr><th>Category</th><th>Count</th><th>Net PnL</th></tr></thead>
        <tbody>{render_attribution_category_rows(trade_attribution.get("top_failure_categories") or [])}</tbody>
      </table>
    </div>
    <div class="panel">
      <h2>Worst Entry Modes</h2>
      <table>
        <thead><tr><th>Entry Mode</th><th>Trades</th><th>Win Rate</th><th>Net PnL</th></tr></thead>
        <tbody>{render_attribution_entry_mode_rows(trade_attribution.get("worst_entry_modes") or [])}</tbody>
      </table>
      <p class="note">Focus: {esc(', '.join(trade_attribution.get("proposed_focus") or ['no_new_focus_generated']))}</p>
    </div>
  </section>

  <section class="grid three">
    <div class="panel">
      <h2>Artifact Counts</h2>
      <p class="value">{esc(report_count)} reports</p>
      <p class="note">{esc(experiment_count)} experiments · historical evidence, not extra schedulers</p>
    </div>
    <div class="panel">
      <h2>Safety Flags</h2>
      <p class="good">live_orders_enabled=false</p>
      <p class="good">private_api_used=false</p>
      <p class="note">This dashboard is read-only and display-only.</p>
    </div>
    <div class="panel">
      <h2>Source Files</h2>
      <p class="note">Ledger: {esc(rel(LEDGER_PATH))}</p>
      <p class="note">Runner: {esc(payload["runner_path"])}</p>
      <p class="note">Current signal: {esc(runner.get("current_signal_path"))}</p>
    </div>
  </section>
</main>
<footer>
  本 HTML 是本地静态可视化，不会自动下单。刷新数据请重新运行 visual_dashboard_builder.py 或让 automation 后续接入该生成器。
</footer>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a local visual HTML dashboard for active-alpha paper trading.")
    parser.add_argument("--output", default=str(OUTPUT_HTML))
    parser.add_argument("--json-output", default=str(OUTPUT_JSON))
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()

    payload = build_payload()
    output = Path(args.output)
    json_output = Path(args.json_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(payload), encoding="utf-8")
    with json_output.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "generated_at": payload["generated_at"],
                "scope": payload["scope"],
                "live_orders_enabled": False,
                "private_api_used": False,
                "output": rel(output),
                "closed_trades": len(payload["closed_rows"]),
                "equity_usd": payload["ledger"].get("equity_usd"),
                "win_count": payload["wins"],
                "loss_count": payload["losses"],
                "proposed_change_count": len(payload["runner"].get("proposed_changes") or []),
                "recovery_watchlist_count": payload["recovery_watchlist"].get("queue_count"),
                "recovery_actionable_paper_scout_count": payload["recovery_watchlist"].get("actionable_paper_scout_count"),
                "recovery_sampler_opened_count": payload["recovery_sampler"].get("opened_count"),
                "recovery_sampler_blocked_count": payload["recovery_sampler"].get("blocked_count"),
                "pipeline_repair_status": payload["pipeline_repair"].get("status"),
                "pipeline_repair_child_count": payload["pipeline_repair"].get("child_count"),
                "pipeline_repair_ledger_mutated": payload["pipeline_repair"].get("ledger_mutated"),
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")
    if args.compact_output:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "dashboard": rel(output),
                    "json": rel(json_output),
                    "generated_at": payload["generated_at"],
                    "closed_trades": len(payload["closed_rows"]),
                    "live_orders_enabled": False,
                    "private_api_used": False,
                    "recovery_actionable_paper_scout_count": payload["recovery_watchlist"].get("actionable_paper_scout_count"),
                    "recovery_sampler_opened_count": payload["recovery_sampler"].get("opened_count"),
                    "pipeline_repair_status": payload["pipeline_repair"].get("status"),
                    "pipeline_repair_child_count": payload["pipeline_repair"].get("child_count"),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(f"Wrote {rel(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
