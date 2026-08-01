#!/usr/bin/env python3
"""Generate WeChat-friendly paper trading push assets.

Outputs:
- reports/LATEST_ACTIVE_ALPHA_PUSH_SUMMARY.md
- reports/LATEST_ACTIVE_ALPHA_PUSH_CARD.svg
- reports/LATEST_ACTIVE_ALPHA_PUSH_CARD.png when a local converter is available

This script is read-only with respect to trading state.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import visual_dashboard_builder as dashboard


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
SUMMARY_MD = ACTIVE_ROOT / "reports" / "LATEST_ACTIVE_ALPHA_PUSH_SUMMARY.md"
CARD_SVG = ACTIVE_ROOT / "reports" / "LATEST_ACTIVE_ALPHA_PUSH_CARD.svg"
CARD_PNG = ACTIVE_ROOT / "reports" / "LATEST_ACTIVE_ALPHA_PUSH_CARD.png"
ASSET_JSON = ACTIVE_ROOT / "experiments" / "wechat-push-assets-latest.json"


def rel(path: Path | None) -> str:
    if path is None:
        return "-"
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def svg_escape(value: Any) -> str:
    return dashboard.esc(value)


def compact_reason_counts(reason_counts: dict[str, Any], limit: int = 3) -> str:
    if not reason_counts:
        return "-"
    items = sorted(reason_counts.items(), key=lambda item: str(item[0]))[:limit]
    return "; ".join(f"{key}:{value}" for key, value in items)


def build_summary(payload: dict[str, Any]) -> str:
    ledger = payload["ledger"]
    rows = payload["closed_rows"]
    runner = payload["runner"]
    phase = payload["phase"]["phase_status"]
    verdict = payload["compounding"]["verdict"]
    open_positions = ledger.get("open_positions") or []
    no_entry = runner.get("no_entry") or {}
    reason_counts = no_entry.get("reason_counts") or {}
    watch = ", ".join((runner.get("current_top_symbols") or [])[:5]) or "-"
    selected = ", ".join((runner.get("selected_symbols") or [])[:10]) or "-"
    cached_watch = ", ".join((runner.get("cached_top_symbols") or [])[:5]) or "-"
    cached_selected = ", ".join((runner.get("cached_selected_symbols") or [])[:10]) or "-"
    automation_status = (payload.get("automation") or {}).get("status") or "missing"
    automation_recovery = payload.get("automation_recovery") or {}
    current_action = (
        "await_explicit_resume_confirmation"
        if automation_status == "PAUSED"
        else "restore_single_safe_automation_after_explicit_approval"
        if automation_status in {"missing", "MISSING"}
        else (no_entry.get("status") or "paper_scan")
    )
    proposed = runner.get("proposed_changes") or []
    proposed_titles = "; ".join(item.get("title", "-") for item in proposed[:3]) or "-"
    recovery = payload.get("recovery_watchlist") or {}
    recovery_items = recovery.get("watchlist") or []
    recovery_sampler = payload.get("recovery_sampler") or {}
    pipeline_repair = payload.get("pipeline_repair") or {}
    capital_allocation = payload.get("capital_allocation") or {}
    ledger_integrity = payload.get("ledger_integrity") or {}
    sampler_items = recovery_sampler.get("decisions") or []
    blocked_retest = payload.get("blocked_retest") or {}
    blocked_retest_items = blocked_retest.get("results") or []
    blocked_retest_sampler = payload.get("blocked_retest_sampler") or {}
    blocked_retest_sampler_items = blocked_retest_sampler.get("decisions") or []
    trade_attribution = payload.get("trade_attribution") or {}
    attribution_summary = trade_attribution.get("summary") or {}
    capital_allocation = payload.get("capital_allocation") or {}
    ledger_integrity = payload.get("ledger_integrity") or {}
    binance_health = payload.get("binance_market_data_health") or {}
    binance_kline_cache = payload.get("binance_kline_cache") or {}
    kline_research_repro = payload.get("kline_research_reproducibility") or {}
    signal_contract = payload.get("paper_signal_contract") or {}
    market_context = payload.get("paper_market_context") or {}
    sample_growth = dashboard.sample_growth_action_board(payload)
    recovery_lines = []
    for item in recovery_items[:3]:
        entry = item.get("entry_zone") or {}
        recovery_lines.append(
            f"- {item.get('symbol')} `{item.get('recommended_max_action')}` "
            f"data `{item.get('dynamic_scan_status')}`, degraded `{item.get('data_layer_degraded')}`, "
            f"price `{item.get('current_price')}`, trigger `{entry.get('breakout_confirm_above')}`, "
            f"stop `{item.get('stop_loss')}`, buy `{item.get('recent_taker_buy_quote_ratio')}`, "
            f"imb `{item.get('order_book_imbalance_20')}`, p `{item.get('forecast_probability_pct')}%`"
        )
    recovery_text = "\n".join(recovery_lines) or "- 暂无 recovery watchlist 候选"
    sampler_lines = []
    for item in sampler_items[:3]:
        reasons = ", ".join(str(reason) for reason in (item.get("reasons") or [])[:2]) or "-"
        sampler_lines.append(
            f"- {item.get('symbol')} `{item.get('decision')}` reasons `{reasons}` "
            f"price `{item.get('current_price')}`, trigger `{item.get('trigger')}`"
        )
    sampler_text = "\n".join(sampler_lines) or "- 暂无 recovery sampler 决策"
    blocked_lines = []
    for item in (no_entry.get("top_blocked_candidates") or [])[:3]:
        blocked_lines.append(
            f"- {item.get('symbol')} `{item.get('primary_block_reason')}` "
            f"score `{item.get('selection_score')}`, OOS `{item.get('oos_win_rate_pct')}%/{item.get('oos_net_return_pct')}%`"
        )
    blocked_text = "\n".join(blocked_lines) or "- 暂无 top blocked candidate"
    sample_growth_lines = []
    for item in (sample_growth.get("actions") or [])[:3]:
        sample_growth_lines.append(
            f"- {item.get('symbol')} block `{item.get('block')}`, next `{item.get('next_step')}`, "
            f"max `{dashboard.money(item.get('max_notional'), 2)}`"
        )
    sample_growth_text = "\n".join(sample_growth_lines) or "- 暂无 sample-growth action"
    trigger_watch_lines = []
    for item in (sample_growth.get("trigger_watch") or [])[:3]:
        trigger_watch_lines.append(
            f"- {item.get('symbol')} last `{item.get('last_price')}`, trigger `{item.get('trigger')}`, "
            f"distance `{item.get('distance')}`, spread `{item.get('spread_bps')}`"
        )
    trigger_watch_text = "\n".join(trigger_watch_lines) or "- 暂无 quality scout trigger watch"
    retest_lines = []
    ordered_retests = sorted(
        [item for item in blocked_retest_items if isinstance(item, dict)],
        key=lambda item: (
            item.get("decision") != "candidate_for_min_quality_scout_after_current_signal",
            -float(((item.get("best_variant") or {}).get("oos") or {}).get("net_return_pct") or 0.0),
        ),
    )
    for item in ordered_retests[:3]:
        best = item.get("best_variant") or {}
        oos = best.get("oos") or {}
        retest_lines.append(
            f"- {item.get('symbol')} `{item.get('decision')}` "
            f"variant `{best.get('variant')}`, OOS `{oos.get('win_rate_pct')}%/{oos.get('net_return_pct')}%`"
        )
    retest_text = "\n".join(retest_lines) or "- 暂无 blocked retest 结果"
    scout_lines = []
    for item in blocked_retest_sampler_items[:3]:
        reasons = ", ".join(str(reason) for reason in (item.get("reasons") or [])[:2]) or "-"
        scout_lines.append(
            f"- {item.get('symbol')} `{item.get('decision')}` reasons `{reasons}` "
            f"price `{item.get('last_price')}`, breakout `{item.get('breakout_level')}`"
        )
    scout_text = "\n".join(scout_lines) or "- 暂无 quality scout sampler 决策"
    attribution_lines = []
    for item in (trade_attribution.get("top_failure_categories") or [])[:4]:
        attribution_lines.append(
            f"- {item.get('category')} count `{item.get('count')}`, net `{item.get('net_pnl_usd')}`"
        )
    attribution_text = "\n".join(attribution_lines) or "- 暂无 trade attribution"
    recent = rows[:3]
    recent_lines = []
    for row in recent:
        recent_lines.append(
            f"- {row.get('symbol')} {dashboard.money(row.get('pnl'), 3)} "
            f"({dashboard.pct(row.get('pnl_pct'), 2)}) {row.get('outcome')}"
        )
    recent_text = "\n".join(recent_lines) or "- 暂无 closed trade"
    recovery_label = "Recovery Watchlist" if recovery.get("freshness_status") == "fresh" else "Historical Cached Recovery Watchlist"
    recovery_sampler_label = "Recovery Paper Sampler" if recovery_sampler.get("freshness_status") == "fresh" else "Historical Cached Recovery Paper Sampler"
    pipeline_repair_label = "Pipeline Freshness Repair" if pipeline_repair.get("freshness_status") == "fresh" else "Historical Cached Pipeline Freshness Repair"
    blocked_retest_label = "Top Blocked Retest Lab" if blocked_retest.get("freshness_status") == "fresh" else "Historical Cached Top Blocked Retest Lab"
    blocked_sampler_label = "Top Blocked Retest Quality Scout Sampler" if blocked_retest_sampler.get("freshness_status") == "fresh" else "Historical Cached Top Blocked Retest Quality Scout Sampler"

    return f"""# Active Alpha Paper Update

> 只读 paper trading 状态；不会下真实订单。

**Equity**: {dashboard.money(ledger.get("equity_usd"), 3)}  
**Net Return**: {dashboard.pct(ledger.get("net_return_pct"), 2)}  
**Win Rate**: {dashboard.pct((payload["wins"] / max(1, payload["wins"] + payload["losses"]) * 100), 2)}  
**Closed Trades**: {len(rows)} / Phase2 target 30-50  
**Open Positions**: {len(open_positions)}  
**Phase**: P1 `{phase.get("phase1_paper_execution_loop")}`, P2 `{phase.get("phase2_positive_expectancy_proof")}`, P3 `{phase.get("phase3_monthly_double_pressure_test") or phase.get("phase3_testnet_or_tiny_live_readiness")}`, P4 `{phase.get("phase4_real_auto_trading_candidate") or phase.get("phase4_live_or_testnet_candidate")}`
**Automation**: `{automation_status}` · `live_orders_enabled=false`  
**Automation Recovery**: `{automation_recovery.get("status")}` · approval `{automation_recovery.get("requires_explicit_user_approval")}` · max action `{automation_recovery.get("max_allowed_action")}` · mutated `{automation_recovery.get("automation_mutated")}`  
**信号缓存**: `{runner.get("current_signal_state")}` · signal age `{runner.get("current_signal_age_hours")}`h · runner `{runner.get("freshness_status")}` age `{runner.get("age_hours")}`h

**资金分配**: `{capital_allocation.get("policy")}` · authorization `{capital_allocation.get("current_deployment_authorization")}` · current deploy `{dashboard.money(capital_allocation.get("max_deployable_now_usd"), 2)}` · conditional research `{dashboard.money(capital_allocation.get("research_max_deployable_usd"), 2)}` · research trade `{dashboard.money(capital_allocation.get("research_per_trade_notional_usd"), 2)}` · idle/wait `{dashboard.money(capital_allocation.get("idle_or_waiting_cash_usd"), 2)}`  
**授权阻断**: `{compact_reason_counts({reason: 1 for reason in (capital_allocation.get("authorization_blockers") or [])}, 4)}`
**分配原因**: `{compact_reason_counts({reason: 1 for reason in (capital_allocation.get("decision_reasons") or [])}, 4)}`
**账本完整性**: `{ledger_integrity.get("status")}` · orders `{ledger_integrity.get("order_count")}` · warnings `{ledger_integrity.get("warning_count")}` · blocked `{ledger_integrity.get("blocked_count")}` · live `{ledger_integrity.get("live_orders_enabled")}` · private `{ledger_integrity.get("private_api_used")}`
**Binance 数据层**: `{binance_health.get("status")}` · pass/warn/block `{binance_health.get("passed_symbol_count")}/{binance_health.get("warning_symbol_count")}/{binance_health.get("blocked_symbol_count")}` · hosts `{len(binance_health.get("base_urls") or [])}` · fallback_fail `{binance_health.get("endpoint_fallback_failure_count")}` · live `{binance_health.get("live_orders_enabled")}` · private `{binance_health.get("private_api_used")}`
**Binance K线缓存**: `{binance_kline_cache.get("status")}` · storage `{binance_kline_cache.get("storage_status")}` · declared/actual files `{binance_kline_cache.get("declared_file_count")}/{binance_kline_cache.get("actual_file_count")}` · refresh `{binance_kline_cache.get("refresh_required")}` · keys_logged `{binance_kline_cache.get("private_api_keys_logged")}`
**研究可复现性**: `{kline_research_repro.get("status")}` · reproducible/nonreproducible `{kline_research_repro.get("reproducible_count")}/{kline_research_repro.get("nonreproducible_count")}` · promotion `{kline_research_repro.get("promotion_allowed_count")}` · max action `{kline_research_repro.get("max_allowed_action")}`
**信号合约**: `{signal_contract.get("status")}` · complete/partial/incomplete `{signal_contract.get("complete_count")}/{signal_contract.get("partial_count")}/{signal_contract.get("incomplete_count")}` · unsafe `{signal_contract.get("unsafe_count")}`
**Market Context**: `{market_context.get("status")}` · coverage `{market_context.get("coverage_pct")}%` · complete/partial/missing `{market_context.get("complete_count")}/{market_context.get("partial_count")}/{market_context.get("missing_count")}` · regimes `{market_context.get("explicit_regime_count")}`

**当前动作**: `{current_action}`  
**未开仓/未加仓原因**: `{compact_reason_counts(reason_counts)}`  
**Watch**: {watch}  
**Scan Pool**: {selected}
**Historical Cached Watch/Pool**: {cached_watch} / {cached_selected}

**Sample Growth**: `{sample_growth.get("status")}` · gap `{sample_growth.get("closed_trade_gap")}` · policy `{sample_growth.get("capital_policy")}` · deploy `{dashboard.money(sample_growth.get("max_deployable"), 2)}` · conditional research trade `{dashboard.money(sample_growth.get("per_trade"), 2)}`
{sample_growth_text}
**Trigger Watch**
{trigger_watch_text}

**{recovery_label}**: freshness `{recovery.get("freshness_status")}` age `{recovery.get("age_hours")}`h · queue `{recovery.get("queue_count")}`, actionable `{recovery.get("actionable_paper_scout_count")}`, data `{recovery.get("dynamic_scan_status")}`, degraded `{recovery.get("data_layer_degraded")}`
{recovery_text}

**{recovery_sampler_label}**: freshness `{recovery_sampler.get("freshness_status")}` age `{recovery_sampler.get("age_hours")}`h · status `{recovery_sampler.get("status")}`, opened `{recovery_sampler.get("opened_count")}`, blocked `{recovery_sampler.get("blocked_count")}`, ledger_mutated `{recovery_sampler.get("ledger_mutated")}`
{sampler_text}

**{pipeline_repair_label}**: freshness `{pipeline_repair.get("freshness_status")}` age `{pipeline_repair.get("age_hours")}`h · status `{pipeline_repair.get("status")}`, initial `{pipeline_repair.get("initial_status")}`, final `{pipeline_repair.get("final_status")}`, repair_plan `{pipeline_repair.get("repair_plan_count")}`, child `{pipeline_repair.get("child_count")}`, ledger_mutated `{pipeline_repair.get("ledger_mutated")}`

**Top Blocked Candidates**
{blocked_text}

**{blocked_retest_label}**: freshness `{blocked_retest.get("freshness_status")}` age `{blocked_retest.get("age_hours")}`h · status `{blocked_retest.get("status")}` · candidates `{blocked_retest.get("candidates_seen")}`, quality_review `{blocked_retest.get("quality_scout_review_count")}`, keep_blocked `{blocked_retest.get("keep_blocked_count")}`, ledger_mutated `{blocked_retest.get("ledger_mutated")}`
{retest_text}

**{blocked_sampler_label}**: freshness `{blocked_retest_sampler.get("freshness_status")}` age `{blocked_retest_sampler.get("age_hours")}`h · status `{blocked_retest_sampler.get("status")}` · candidates `{blocked_retest_sampler.get("candidate_count")}`, opened `{blocked_retest_sampler.get("opened_count")}`, blocked `{blocked_retest_sampler.get("blocked_count")}`, ledger_mutated `{blocked_retest_sampler.get("ledger_mutated")}`
{scout_text}

**Paper Trade Attribution**: closed `{attribution_summary.get("closed_trades_in_window")}`, win `{attribution_summary.get("win_rate_pct")}%`, net `{attribution_summary.get("net_pnl_usd")}`, missed_protection `{attribution_summary.get("missed_profit_protection_count")}`, timing `{attribution_summary.get("entry_timing_issue_count")}/{attribution_summary.get("exit_timing_issue_count")}`, regime `{attribution_summary.get("market_regime_context_missing_count")}/{attribution_summary.get("market_regime_misread_count")}`, exec_cost `{attribution_summary.get("execution_cost_visible_count")}`
{attribution_text}

**策略迭代提案**: {proposed_titles}

**最近交易**
{recent_text}

**复利证明**: `{verdict.get("overall_status")}`  
**Max Allowed Action**: `{verdict.get("max_allowed_action")}`  

详情 HTML: `{rel(ACTIVE_ROOT / "reports" / "LATEST_ACTIVE_ALPHA_DASHBOARD.html")}`
"""


def svg_text(x: int, y: int, text: Any, *, size: int = 28, color: str = "#17202a", weight: int = 500) -> str:
    return f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color}">{svg_escape(text)}</text>'


def svg_pill(x: int, y: int, text: str, *, fill: str = "#eef4fb", color: str = "#344351") -> str:
    width = max(150, min(520, 22 * len(text) + 34))
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="44" rx="22" fill="{fill}"/>'
        f'{svg_text(x + 18, y + 30, text, size=22, color=color, weight=600)}'
    )


def mini_line(points: list[tuple[str, float]], x: int, y: int, w: int, h: int) -> str:
    if not points:
        return ""
    values = [p[1] for p in points]
    lo, hi = min(values), max(values)
    if abs(hi - lo) < 1e-9:
        lo -= 1
        hi += 1
    coords = []
    for idx, (_, value) in enumerate(points):
        px = x + w * idx / max(1, len(points) - 1)
        py = y + h - ((value - lo) / (hi - lo) * h)
        coords.append((px, py))
    path = " ".join(("M" if idx == 0 else "L") + f"{px:.1f},{py:.1f}" for idx, (px, py) in enumerate(coords))
    area = f"{path} L {coords[-1][0]:.1f},{y+h} L {coords[0][0]:.1f},{y+h} Z"
    return (
        f'<path d="{area}" fill="#dcecff" opacity="0.8"/>'
        f'<path d="{path}" fill="none" stroke="#2f7dd1" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>'
    )


def render_card_svg(payload: dict[str, Any]) -> str:
    ledger = payload["ledger"]
    rows = payload["closed_rows"]
    runner = payload["runner"]
    phase = payload["phase"]["phase_status"]
    no_entry = runner.get("no_entry") or {}
    reason_counts = no_entry.get("reason_counts") or {}
    equity = float(ledger.get("equity_usd") or 0)
    initial = float(ledger.get("initial_capital_usd") or 500)
    wins = payload["wins"]
    losses = payload["losses"]
    win_rate = wins / max(1, wins + losses) * 100
    watch = ", ".join((runner.get("current_top_symbols") or [])[:4]) or "-"
    signal_state = runner.get("current_signal_state") or "missing"
    automation_status = (payload.get("automation") or {}).get("status") or "missing"
    proposed = runner.get("proposed_changes") or []
    proposed_title = proposed[0].get("title") if proposed else "-"
    recovery = payload.get("recovery_watchlist") or {}
    recovery_items = recovery.get("watchlist") or []
    recovery_sampler = payload.get("recovery_sampler") or {}
    pipeline_repair = payload.get("pipeline_repair") or {}
    capital_allocation = payload.get("capital_allocation") or {}
    ledger_integrity = payload.get("ledger_integrity") or {}
    binance_health = payload.get("binance_market_data_health") or {}
    binance_kline_cache = payload.get("binance_kline_cache") or {}
    kline_research_repro = payload.get("kline_research_reproducibility") or {}
    signal_contract = payload.get("paper_signal_contract") or {}
    recovery_prefix = "Recovery" if recovery.get("freshness_status") == "fresh" else "Cached recovery"
    recovery_symbols = ", ".join(str(item.get("symbol") or "-") for item in recovery_items[:2]) or "-"
    recovery_line = f"{recovery_prefix}: {recovery_symbols}"
    sampler_line = (
        f"Sampler {recovery_sampler.get('status') or '-'} "
        f"open {recovery_sampler.get('opened_count')} / block {recovery_sampler.get('blocked_count')}"
    )
    repair_line = (
        f"Repair {pipeline_repair.get('status') or '-'} "
        f"{pipeline_repair.get('initial_status') or '-'}->{pipeline_repair.get('final_status') or '-'} "
        f"child {pipeline_repair.get('child_count')}"
    )
    capital_line = (
        f"Capital now {dashboard.money(capital_allocation.get('max_deployable_now_usd'), 0)} · "
        f"research {dashboard.money(capital_allocation.get('research_max_deployable_usd'), 0)}"
    )
    ledger_integrity_line = (
        f"Ledger {ledger_integrity.get('status') or '-'} · "
        f"orders {ledger_integrity.get('order_count')} · "
        f"warn {ledger_integrity.get('warning_count')} block {ledger_integrity.get('blocked_count')}"
    )
    binance_health_line = (
        f"Binance data {binance_health.get('status') or '-'} · "
        f"{binance_health.get('passed_symbol_count')}/{binance_health.get('warning_symbol_count')}/{binance_health.get('blocked_symbol_count')}"
    )
    binance_kline_line = (
        f"Kline {binance_kline_cache.get('status') or '-'} · "
        f"files {binance_kline_cache.get('actual_file_count')}/{binance_kline_cache.get('declared_file_count')} · "
        f"repro {kline_research_repro.get('reproducible_count')}/{kline_research_repro.get('artifact_count')}"
    )
    signal_contract_line = (
        f"Signal {signal_contract.get('status') or '-'} · "
        f"{signal_contract.get('complete_count')}/{signal_contract.get('partial_count')}/{signal_contract.get('incomplete_count')}"
    )
    recent = rows[:4]
    net_return = float(ledger.get("net_return_pct") or 0)
    net_color = "#108a55" if net_return >= 0 else "#c73e3a"
    phase2 = phase.get("phase2_positive_expectancy_proof") or "unknown"
    generated = payload["generated_at"].replace("+08:00", "")

    trade_rows = []
    start_y = 1138
    for idx, row in enumerate(recent):
        y = start_y + idx * 72
        pnl = float(row.get("pnl") or 0)
        color = "#108a55" if pnl >= 0 else "#c73e3a"
        trade_rows.append(svg_text(74, y, row.get("symbol"), size=28, weight=700))
        trade_rows.append(svg_text(262, y, row.get("outcome"), size=24, color="#657381", weight=500))
        trade_rows.append(svg_text(548, y, dashboard.money(pnl, 3), size=28, color=color, weight=760))
        trade_rows.append(svg_text(790, y, dashboard.pct(row.get("pnl_pct"), 2), size=24, color=color, weight=650))

    automation_line = (
        f"Automation {automation_status} · runner {runner.get('freshness_status') or '-'} · "
        f"signal {signal_state}"
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1680" viewBox="0 0 1080 1680">
  <rect width="1080" height="1680" fill="#f4f6f8"/>
  <rect x="0" y="0" width="1080" height="210" fill="#101820"/>
  {svg_text(54, 82, "Active Alpha Paper", size=46, color="#ffffff", weight=800)}
  {svg_text(54, 136, f"Binance crypto paper dashboard · {automation_status}", size=26, color="#b9c5d0", weight=500)}
  {svg_text(54, 178, generated, size=22, color="#91a1af", weight=500)}
  {svg_pill(720, 52, "PAPER ONLY", fill="#e8f7ef", color="#108a55")}

  <rect x="44" y="246" width="992" height="226" rx="28" fill="#ffffff"/>
  {svg_text(74, 306, "Paper Equity", size=26, color="#657381", weight=600)}
  {svg_text(74, 382, dashboard.money(equity, 3), size=64, color="#17202a", weight=850)}
  {svg_text(74, 432, f"Initial {dashboard.money(initial, 0)} · Cash {dashboard.money(ledger.get('cash_usd'), 3)}", size=24, color="#657381", weight=500)}
  {svg_text(668, 318, "Net Return", size=24, color="#657381", weight=600)}
  {svg_text(668, 382, dashboard.pct(net_return, 2), size=54, color=net_color, weight=850)}
  {svg_text(668, 432, f"Max DD {dashboard.pct(ledger.get('max_drawdown_pct'), 2)}", size=24, color="#657381", weight=500)}

  <rect x="44" y="504" width="992" height="252" rx="28" fill="#ffffff"/>
  {svg_text(74, 564, "Equity Trend", size=28, color="#17202a", weight=760)}
  {mini_line(payload["equity_points"], 74, 598, 914, 120)}

  <rect x="44" y="788" width="310" height="194" rx="24" fill="#ffffff"/>
  {svg_text(74, 848, "Win Rate", size=24, color="#657381", weight=600)}
  {svg_text(74, 916, dashboard.pct(win_rate, 1), size=52, color="#17202a", weight=850)}
  {svg_text(74, 956, f"{wins} wins / {losses} losses", size=22, color="#657381", weight=500)}

  <rect x="386" y="788" width="310" height="194" rx="24" fill="#ffffff"/>
  {svg_text(416, 848, "Closed Trades", size=24, color="#657381", weight=600)}
  {svg_text(416, 916, f"{len(rows)}", size=52, color="#17202a", weight=850)}
  {svg_text(416, 956, "Phase2 target 30-50", size=22, color="#657381", weight=500)}

  <rect x="728" y="788" width="308" height="194" rx="24" fill="#ffffff"/>
  {svg_text(758, 848, "Phase 2", size=24, color="#657381", weight=600)}
  {svg_text(758, 914, phase2, size=35, color="#c73e3a" if phase2 != "proven" else "#108a55", weight=850)}
  {svg_text(758, 956, "Positive expectancy gate", size=22, color="#657381", weight=500)}

  <rect x="44" y="1014" width="992" height="358" rx="28" fill="#ffffff"/>
  {svg_text(74, 1074, "Recent Closed Trades", size=30, color="#17202a", weight=800)}
  {''.join(trade_rows)}

  <rect x="44" y="1404" width="992" height="258" rx="28" fill="#ffffff"/>
  {svg_text(74, 1462, f"Current Watch · {signal_state}", size=26, color="#657381", weight=650)}
  {svg_text(74, 1510, watch, size=30, color="#17202a", weight=780)}
  {svg_text(74, 1546, automation_line, size=17, color="#657381", weight=500)}
  {svg_text(74, 1578, recovery_line, size=17, color="#657381", weight=500)}
  {svg_text(74, 1610, binance_health_line, size=17, color="#657381", weight=500)}
  {svg_text(74, 1642, binance_kline_line, size=17, color="#657381", weight=500)}
  {svg_text(612, 1546, sampler_line, size=17, color="#657381", weight=500)}
  {svg_text(612, 1578, capital_line, size=17, color="#657381", weight=500)}
  {svg_text(612, 1610, ledger_integrity_line, size=17, color="#657381", weight=500)}
  {svg_text(612, 1642, f"{signal_contract_line} · {repair_line}", size=16, color="#657381", weight=500)}
</svg>
"""


def convert_svg_to_png(svg_path: Path, png_path: Path) -> dict[str, Any]:
    sips = shutil.which("sips")
    if sips:
        try:
            subprocess.run(
                [sips, "-s", "format", "png", str(svg_path), "--out", str(png_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=30,
            )
            if png_path.exists():
                return {"status": "ok", "converter": "sips", "png": rel(png_path)}
        except Exception as exc:  # noqa: BLE001
            sips_error = f"{type(exc).__name__}: {exc}"
        else:
            sips_error = "sips did not create output"
    else:
        sips_error = "sips not found"

    qlmanage = shutil.which("qlmanage")
    if qlmanage:
        out_dir = png_path.parent
        generated = out_dir / f"{svg_path.name}.png"
        try:
            subprocess.run(
                [qlmanage, "-t", "-s", "1080", "-o", str(out_dir), str(svg_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=30,
            )
            if generated.exists():
                generated.replace(png_path)
                return {"status": "ok", "converter": "qlmanage_thumbnail_fallback", "png": rel(png_path), "warning": "qlmanage may create a square thumbnail"}
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "failed",
                "converter": "qlmanage",
                "reason": f"{type(exc).__name__}: {exc}",
                "sips_error": sips_error,
            }
    return {
        "status": "unavailable",
        "converter": None,
        "reason": "no local SVG to PNG converter found",
        "sips_error": sips_error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate WeChat-friendly active-alpha push assets.")
    parser.add_argument("--summary-output", default=str(SUMMARY_MD))
    parser.add_argument("--svg-output", default=str(CARD_SVG))
    parser.add_argument("--png-output", default=str(CARD_PNG))
    parser.add_argument("--json-output", default=str(ASSET_JSON))
    parser.add_argument("--skip-png", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()

    payload = dashboard.build_payload()
    summary_path = Path(args.summary_output)
    svg_path = Path(args.svg_output)
    png_path = Path(args.png_output)
    json_path = Path(args.json_output)
    for path in (summary_path, svg_path, png_path, json_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(build_summary(payload), encoding="utf-8")
    svg_path.write_text(render_card_svg(payload), encoding="utf-8")
    png_status = {"status": "skipped", "reason": "skip_png"}
    if not args.skip_png:
        png_status = convert_svg_to_png(svg_path, png_path)
    result = {
        "generated_at": payload["generated_at"],
        "scope": "paper_only_wechat_push_assets",
        "live_orders_enabled": False,
        "private_api_used": False,
        "automation_recovery_status": (payload.get("automation_recovery") or {}).get("status"),
        "automation_recovery_requires_approval": (payload.get("automation_recovery") or {}).get("requires_explicit_user_approval"),
        "automation_recovery_max_allowed_action": (payload.get("automation_recovery") or {}).get("max_allowed_action"),
        "automation_recovery_mutated": (payload.get("automation_recovery") or {}).get("automation_mutated"),
        "summary_md": rel(summary_path),
        "card_svg": rel(svg_path),
        "card_png": rel(png_path) if png_path.exists() else None,
        "png_status": png_status,
        "closed_trades": len(payload["closed_rows"]),
        "equity_usd": payload["ledger"].get("equity_usd"),
        "proposed_change_count": len(payload["runner"].get("proposed_changes") or []),
        "recovery_watchlist_count": (payload.get("recovery_watchlist") or {}).get("queue_count"),
        "recovery_actionable_paper_scout_count": (payload.get("recovery_watchlist") or {}).get("actionable_paper_scout_count"),
        "recovery_sampler_opened_count": (payload.get("recovery_sampler") or {}).get("opened_count"),
        "recovery_sampler_blocked_count": (payload.get("recovery_sampler") or {}).get("blocked_count"),
        "pipeline_repair_status": (payload.get("pipeline_repair") or {}).get("status"),
        "pipeline_repair_child_count": (payload.get("pipeline_repair") or {}).get("child_count"),
        "pipeline_repair_ledger_mutated": (payload.get("pipeline_repair") or {}).get("ledger_mutated"),
        "capital_allocation_policy": (payload.get("capital_allocation") or {}).get("policy"),
        "capital_current_deployment_authorization": (payload.get("capital_allocation") or {}).get("current_deployment_authorization"),
        "capital_authorization_blockers": (payload.get("capital_allocation") or {}).get("authorization_blockers") or [],
        "capital_research_max_deployable_usd": (payload.get("capital_allocation") or {}).get("research_max_deployable_usd"),
        "capital_research_per_trade_notional_usd": (payload.get("capital_allocation") or {}).get("research_per_trade_notional_usd"),
        "capital_max_deployable_now_usd": (payload.get("capital_allocation") or {}).get("max_deployable_now_usd"),
        "capital_per_trade_notional_usd": (payload.get("capital_allocation") or {}).get("per_trade_notional_usd"),
        "ledger_integrity_status": (payload.get("ledger_integrity") or {}).get("status"),
        "ledger_integrity_warning_count": (payload.get("ledger_integrity") or {}).get("warning_count"),
        "ledger_integrity_blocked_count": (payload.get("ledger_integrity") or {}).get("blocked_count"),
        "binance_market_data_health_status": (payload.get("binance_market_data_health") or {}).get("status"),
        "binance_market_data_health_warning_symbol_count": (payload.get("binance_market_data_health") or {}).get("warning_symbol_count"),
        "binance_market_data_health_blocked_symbol_count": (payload.get("binance_market_data_health") or {}).get("blocked_symbol_count"),
        "binance_kline_cache_status": (payload.get("binance_kline_cache") or {}).get("status"),
        "binance_kline_cache_file_count": (payload.get("binance_kline_cache") or {}).get("actual_file_count"),
        "binance_kline_cache_declared_file_count": (payload.get("binance_kline_cache") or {}).get("declared_file_count"),
        "binance_kline_cache_storage_status": (payload.get("binance_kline_cache") or {}).get("storage_status"),
        "binance_kline_cache_refresh_required": (payload.get("binance_kline_cache") or {}).get("refresh_required"),
        "kline_research_reproducibility_status": (payload.get("kline_research_reproducibility") or {}).get("status"),
        "kline_research_promotion_allowed_count": (payload.get("kline_research_reproducibility") or {}).get("promotion_allowed_count"),
        "binance_kline_cache_failure_count": (payload.get("binance_kline_cache") or {}).get("failure_count"),
        "binance_kline_cache_selected_symbol_count": (payload.get("binance_kline_cache") or {}).get("selected_symbol_count"),
        "paper_signal_contract_status": (payload.get("paper_signal_contract") or {}).get("status"),
        "paper_signal_contract_complete_count": (payload.get("paper_signal_contract") or {}).get("complete_count"),
        "paper_signal_contract_incomplete_count": (payload.get("paper_signal_contract") or {}).get("incomplete_count"),
    }
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    result["json"] = rel(json_path)
    if args.compact_output:
        print(json.dumps({"status": "ok", **result}, ensure_ascii=False))
    else:
        print(f"Wrote {rel(summary_path)} and {rel(svg_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
