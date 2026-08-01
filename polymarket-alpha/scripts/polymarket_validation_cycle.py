#!/usr/bin/env python3
"""Single-entry, lock-safe Polymarket paper validation cycle."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "data" / ".polymarket-validation-cycle.lock"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def acquire_lock(path: Path, stale_seconds: float = 1800) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    stale_removed = False
    if path.exists():
        age = time.time() - path.stat().st_mtime
        if age <= stale_seconds:
            return {"acquired": False, "reason": "fresh_cycle_lock_exists", "age_seconds": age, "stale_removed": False}
        path.unlink(); stale_removed = True
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        payload = json.dumps({"pid": os.getpid(), "created_at": now_iso()}).encode()
        os.write(descriptor, payload); os.close(descriptor)
        return {"acquired": True, "reason": None, "age_seconds": 0, "stale_removed": stale_removed}
    except FileExistsError:
        return {"acquired": False, "reason": "cycle_lock_race", "age_seconds": 0, "stale_removed": stale_removed}


def release_lock(path: Path) -> None:
    try: path.unlink()
    except FileNotFoundError: pass


def run_step(name: str, command: list[str], timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout_seconds, check=False)
        return {
            "name": name, "status": "ok" if result.returncode == 0 else "failed",
            "returncode": result.returncode, "duration_seconds": time.monotonic() - started,
            "stdout_tail": result.stdout[-4000:], "stderr_tail": result.stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name, "status": "failed", "returncode": None,
            "duration_seconds": time.monotonic() - started, "error": "step_timeout",
            "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
        }


def safety_preflight(root: Path = ROOT) -> dict[str, Any]:
    failures = []
    def load(relative: str) -> Any:
        return json.loads((root / relative).read_text(encoding="utf-8"))
    try:
        policy = load("config/policy.json")
        if (policy.get("paper_only") is not True or policy.get("live_orders_enabled") is not False
                or policy.get("private_api_used") is not False or policy.get("real_money_execution_authorized") is not False):
            failures.append("unsafe_policy_flags")
        if int(policy.get("max_open_positions", 99)) > 2: failures.append("max_open_positions_above_2")
        if float(policy.get("max_risk_pool_pct", 99)) > 8: failures.append("early_stage_max_risk_pool_above_8pct")
        sizing = policy.get("early_stage_sizing") or {}
        report_policy = policy.get("manual_decision_report") or {}
        if (sizing.get("active") is not True or sizing.get("automatic_risk_increase_allowed") is not False
                or float(policy.get("default_risk_pool_pct", 99)) > 5):
            failures.append("early_stage_sizing_contract_failed")
        if (report_policy.get("enabled") is not True
                or int(report_policy.get("max_watch_items", 99)) > 4
                or int(report_policy.get("max_recommendations", 99)) > 2
                or report_policy.get("manual_confirmation_required_for_real_money") is not True):
            failures.append("manual_decision_report_contract_failed")
        entry_gate = policy.get("entry_gate") or {}
        if (entry_gate.get("daily_priority") is not True
                or float(entry_gate.get("min_hours_to_expiry", -1)) < 0
                or float(entry_gate.get("max_days_to_expiry", 99)) > 1
                or float(entry_gate.get("secondary_research_max_days_to_expiry", 99)) > 30):
            failures.append("ongoing_and_next_24h_priority_contract_failed")
        high_win = policy.get("high_win_small_return_gate") or {}
        high_win_sizing = high_win.get("position_equity_pct") or []
        if (high_win.get("enabled") is not True
                or float(high_win.get("min_model_probability", 0)) < .90
                or float(high_win.get("min_confidence_lower", 0)) < .85
                or int(high_win.get("min_independent_oos_samples", 0)) < 30
                or float(high_win.get("min_net_ev_per_share_exclusive", -1)) != 0
                or int(high_win.get("max_daily_recommendations", 99)) > 1
                or not high_win_sizing or float(max(high_win_sizing)) > 2
                or high_win.get("cannot_displace_alpha_primary") is not True):
            failures.append("high_win_small_return_gate_contract_failed")
        experimental = policy.get("experimental_research_gate") or {}
        exp_sizing = experimental.get("position_equity_pct") or []
        if (experimental.get("enabled") is not True
                or float(experimental.get("minimum_research_probability", 0)) < .65
                or float(experimental.get("minimum_confidence_lower", 0)) < .50
                or float(experimental.get("minimum_alpha_net_edge_per_share", 0)) < .05
                or int(experimental.get("minimum_official_sources", 0)) < 1
                or int(experimental.get("minimum_independent_sources", 0)) < 2
                or int(experimental.get("maximum_daily_recommendations", 99)) > 1
                or not exp_sizing or float(max(exp_sizing)) > 1
                or experimental.get("real_money_recommendation_allowed") is not False):
            failures.append("experimental_research_gate_contract_failed")
        overlay = load("config/paper_strategy_overlay.json")
        if overlay.get("paper_only") is not True or overlay.get("live_orders_enabled") is not False or overlay.get("private_api_used") is not False:
            failures.append("unsafe_overlay_flags")
        ledger = load("data/paper_ledger.json")
        if ledger.get("live_orders_enabled") is not False or ledger.get("private_api_used") is not False:
            failures.append("unsafe_ledger_flags")
        estimates = load("experiments/current-crypto-barrier-estimates.json")
        estimate_status = load("experiments/current-crypto-barrier-estimate-status.json")
        if estimate_status.get("paper_estimates_allowed") is not True and estimates:
            failures.append("nonempty_estimates_without_model_promotion")
        approvals = load("config/model_approvals.json")
        if approvals.get("paper_only") is not True or approvals.get("live_orders_enabled") is not False or approvals.get("private_api_used") is not False:
            failures.append("unsafe_model_approval_flags")
        registry_path = root / "experiments/current-model-registry.json"
        if registry_path.exists():
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            if registry.get("paper_only") is not True or registry.get("live_orders_enabled") is not False or registry.get("private_api_used") is not False:
                failures.append("unsafe_model_registry_flags")
        shadow_path = root / "data/research_forecast_ledger.json"
        if shadow_path.exists():
            shadow = json.loads(shadow_path.read_text(encoding="utf-8"))
            if (shadow.get("paper_estimates_emitted") is not False
                    or shadow.get("main_paper_ledger_mutated") is not False
                    or shadow.get("live_orders_enabled") is not False
                    or shadow.get("private_api_used") is not False):
                failures.append("unsafe_shadow_research_ledger_flags")
        weather_shadow_path = root / "data/weather_research_forecast_ledger.json"
        if weather_shadow_path.exists():
            weather_shadow = json.loads(weather_shadow_path.read_text(encoding="utf-8"))
            if (weather_shadow.get("paper_estimates_emitted") is not False
                    or weather_shadow.get("main_paper_ledger_mutated") is not False
                    or weather_shadow.get("live_orders_enabled") is not False
                    or weather_shadow.get("private_api_used") is not False):
                failures.append("unsafe_weather_shadow_research_ledger_flags")
        football_shadow_path = root / "data/football_research_forecast_ledger.json"
        if football_shadow_path.exists():
            football_shadow = json.loads(football_shadow_path.read_text(encoding="utf-8"))
            if (football_shadow.get("paper_estimates_emitted") is not False
                    or football_shadow.get("main_paper_ledger_mutated") is not False
                    or football_shadow.get("live_orders_enabled") is not False
                    or football_shadow.get("private_api_used") is not False):
                failures.append("unsafe_football_shadow_research_ledger_flags")
        social_shadow_path = root / "data/social_count_research_forecast_ledger.json"
        if social_shadow_path.exists():
            social_shadow = json.loads(social_shadow_path.read_text(encoding="utf-8"))
            if (social_shadow.get("paper_estimates_emitted") is not False
                    or social_shadow.get("main_paper_ledger_mutated") is not False
                    or social_shadow.get("live_orders_enabled") is not False
                    or social_shadow.get("private_api_used") is not False):
                failures.append("unsafe_social_count_shadow_research_ledger_flags")
        stock_shadow_path = root / "data/stock_weekly_research_forecast_ledger.json"
        if stock_shadow_path.exists():
            stock_shadow = json.loads(stock_shadow_path.read_text(encoding="utf-8"))
            if (stock_shadow.get("paper_estimates_emitted") is not False
                    or stock_shadow.get("main_paper_ledger_mutated") is not False
                    or stock_shadow.get("live_orders_enabled") is not False
                    or stock_shadow.get("private_api_used") is not False):
                failures.append("unsafe_stock_weekly_shadow_research_ledger_flags")
        binary_pair_ledger_path = root / "data/binary_pair_research_ledger.json"
        if binary_pair_ledger_path.exists():
            binary_pair_ledger = json.loads(binary_pair_ledger_path.read_text(encoding="utf-8"))
            if (binary_pair_ledger.get("paper_estimates_emitted") is not False
                    or binary_pair_ledger.get("main_paper_ledger_mutated") is not False
                    or binary_pair_ledger.get("live_orders_enabled") is not False
                    or binary_pair_ledger.get("private_api_used") is not False):
                failures.append("unsafe_binary_pair_research_ledger_flags")
        capture_backcase_path = root / "data/forward_capture_backcase_ledger.json"
        if capture_backcase_path.exists():
            capture_backcases = json.loads(capture_backcase_path.read_text(encoding="utf-8"))
            if (capture_backcases.get("paper_estimates_emitted") is not False
                    or capture_backcases.get("main_paper_ledger_mutated") is not False
                    or capture_backcases.get("live_orders_enabled") is not False
                    or capture_backcases.get("private_api_used") is not False):
                failures.append("unsafe_capture_backcase_ledger_flags")
        daily_priority_ledger_path = root / "data/daily_priority_scan_ledger.json"
        if daily_priority_ledger_path.exists():
            daily_priority = json.loads(daily_priority_ledger_path.read_text(encoding="utf-8"))
            if (daily_priority.get("paper_only") is not True
                    or daily_priority.get("live_orders_enabled") is not False
                    or daily_priority.get("private_api_used") is not False):
                failures.append("unsafe_daily_priority_scan_ledger_flags")
        sports_ledger_path = root / "data/sports_decision_observation_ledger.json"
        if sports_ledger_path.exists():
            sports_ledger = json.loads(sports_ledger_path.read_text(encoding="utf-8"))
            if (sports_ledger.get("append_only") is not True or sports_ledger.get("paper_only") is not True
                    or sports_ledger.get("counts_as_paper_trade") is not False
                    or sports_ledger.get("live_orders_enabled") is not False
                    or sports_ledger.get("private_api_used") is not False
                    or sports_ledger.get("real_money_execution_authorized") is not False):
                failures.append("unsafe_sports_decision_observation_ledger_flags")
        sports_report_path = root / "experiments/current-daily-sports-decision.json"
        if sports_report_path.exists():
            sports_report = json.loads(sports_report_path.read_text(encoding="utf-8"))
            if (sports_report.get("paper_only") is not True or sports_report.get("live_orders_enabled") is not False
                    or sports_report.get("private_api_used") is not False
                    or sports_report.get("real_money_execution_authorized") is not False):
                failures.append("unsafe_daily_sports_report_flags")
        candidate_ledger_path = root / "data/daily_candidate_observation_ledger.json"
        if candidate_ledger_path.exists():
            candidate_ledger = json.loads(candidate_ledger_path.read_text(encoding="utf-8"))
            if (candidate_ledger.get("append_only") is not True
                    or candidate_ledger.get("paper_only") is not True
                    or candidate_ledger.get("counts_as_paper_trade") is not False
                    or candidate_ledger.get("live_orders_enabled") is not False
                    or candidate_ledger.get("private_api_used") is not False):
                failures.append("unsafe_daily_candidate_observation_ledger_flags")
        daily_benchmark_path = root / "experiments/current-daily-forward-benchmark.json"
        if daily_benchmark_path.exists():
            daily_benchmark = json.loads(daily_benchmark_path.read_text(encoding="utf-8"))
            if (daily_benchmark.get("paper_only") is not True
                    or daily_benchmark.get("counts_as_paper_trade") is not False
                    or daily_benchmark.get("live_orders_enabled") is not False
                    or daily_benchmark.get("private_api_used") is not False):
                failures.append("unsafe_daily_forward_benchmark_flags")
        ctf_semantic = load("config/polymarket_ctf_semantic_contract.json")
        ctf_proof = ctf_semantic.get("read_only_condition_proof") or {}
        if (ctf_semantic.get("status") != "official_general_semantics_validated"
                or ctf_semantic.get("general_semantics_validated") is not True
                or ctf_semantic.get("per_market_execution_validated") is not False
                or ctf_semantic.get("paper_entry_eligible") is not False
                or ctf_semantic.get("live_orders_enabled") is not False
                or ctf_semantic.get("private_api_used") is not False
                or ctf_semantic.get("binary_partition") != [1, 2]
                or ctf_proof.get("chain_id") != 137
                or str(ctf_proof.get("conditional_tokens_contract", "")).lower() != "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
                or ctf_proof.get("method_signature") != "getOutcomeSlotCount(bytes32)"
                or ctf_proof.get("method_selector") != "0xd42dc0c2"
                or ctf_proof.get("required_outcome_slot_count") != 2
                or ctf_proof.get("minimum_consistent_public_rpc_sources") != 2
                or set(ctf_proof.get("public_rpc_sources") or []) != {
                    "https://polygon.publicnode.com", "https://polygon.api.onfinality.io/public"}):
            failures.append("unsafe_ctf_semantic_contract")
        stock_research_path = root / "experiments/current-stock-weekly-walk-forward.json"
        if stock_research_path.exists():
            stock = json.loads(stock_research_path.read_text(encoding="utf-8"))
            if (stock.get("paper_estimates_allowed") is not False
                    or stock.get("main_paper_ledger_mutated") is not False
                    or stock.get("live_orders_enabled") is not False
                    or stock.get("private_api_used") is not False):
                failures.append("unsafe_stock_weekly_research_flags")
        family_audit_path = root / "experiments/current-market-family-audit.json"
        if family_audit_path.exists():
            family_audit = json.loads(family_audit_path.read_text(encoding="utf-8"))
            if (family_audit.get("paper_estimates_emitted") is not False
                    or family_audit.get("main_paper_ledger_mutated") is not False
                    or family_audit.get("live_orders_enabled") is not False
                    or family_audit.get("private_api_used") is not False):
                failures.append("unsafe_market_family_audit_flags")
        for relative in ("cache/current_baseball_history/manifest.json", "cache/current_baseball_cutoff_prices/manifest.json"):
            path = root / relative
            if path.exists():
                baseball = json.loads(path.read_text(encoding="utf-8"))
                if (baseball.get("paper_estimates_emitted") is not False
                        or baseball.get("main_paper_ledger_mutated") is not False
                        or baseball.get("live_orders_enabled") is not False
                        or baseball.get("private_api_used") is not False):
                    failures.append(f"unsafe_baseball_research_flags:{relative}")
        baseball_model_path = root / "experiments/current-baseball-elo-walk-forward.json"
        if baseball_model_path.exists():
            baseball_model = json.loads(baseball_model_path.read_text(encoding="utf-8"))
            if (baseball_model.get("paper_estimates_allowed") is not False
                    or baseball_model.get("main_paper_ledger_mutated") is not False
                    or baseball_model.get("live_orders_enabled") is not False
                    or baseball_model.get("private_api_used") is not False):
                failures.append("unsafe_baseball_model_flags")
        esports_paths = [
            *(f"cache/current_esports_2026_history/{title}/manifest.json" for title in ("cs2", "dota2", "lol", "valorant")),
            "cache/current_lol_cutoff_prices/manifest.json",
            "experiments/current-esports-history-audit.json", "experiments/current-esports-source-probe.json",
            "experiments/lol-research-protocol-v1.json", "experiments/current-lol-external-data-gate.json",
            "experiments/family-research-status.json",
            "experiments/current-geopolitics-taxonomy-audit.json",
            "experiments/current-elections-taxonomy-audit.json",
            "cache/current_elections_governor_history/manifest.json",
            "experiments/current-macro-taxonomy-audit.json",
            "cache/current_macro_history/manifest.json",
            "cache/current_family_identity_enrichment.json",
            "experiments/current-mentions-taxonomy-audit.json",
            "cache/current_mentions_history/manifest.json",
            "experiments/current-fdv-taxonomy-audit.json",
            "cache/current_fdv_history/manifest.json",
            "experiments/current-finance-daily-taxonomy-audit.json",
            "cache/current_finance_daily_history/manifest.json",
            "cache/current_finance_daily_cutoff_prices/manifest.json",
            "cache/current_finance_daily_ohlc/manifest.json",
            "experiments/finance-daily-research-protocol-v1.json",
            "experiments/current-finance-daily-model-v1.json",
            "experiments/current-basketball-history-audit.json",
            "experiments/basketball-nba-research-protocol-v1.json",
            "experiments/basketball-wnba-research-protocol-v1.json",
            "cache/current_basketball_history/nba/manifest.json",
            "cache/current_basketball_history/wnba/manifest.json",
            "cache/current_basketball_history/nbasl/manifest.json",
            "cache/current_basketball_espn/manifest.json",
            "cache/current_basketball_mapping/manifest.json",
            "cache/current_basketball_cutoff_prices/manifest.json",
            "cache/current_wnba_espn/manifest.json",
            "cache/current_wnba_mapping/manifest.json",
            "cache/current_wnba_cutoff_prices/manifest.json",
            "experiments/current-basketball-nba-elo-v1.json",
            "experiments/current-basketball-wnba-elo-v1.json",
            "cache/current_uncovered_identity_enrichment.json",
            "experiments/current-uncovered-market-audit.json",
            "experiments/current-technology-taxonomy-audit.json",
            "experiments/current-corporate-taxonomy-audit.json",
            "cache/current_corporate_earnings_history/manifest.json",
            "cache/current_corporate_earnings_cutoff_prices/manifest.json",
            "experiments/corporate-earnings-research-protocol-v1.json",
            "experiments/current-corporate-earnings-model-v1.json",
            "experiments/current-macro-policy-taxonomy-audit.json",
            "experiments/current-entertainment-taxonomy-audit.json",
            "experiments/current-token-launch-history-audit.json",
            "cache/current_token_launch_history/token_launches/manifest.json",
            "cache/current_token_launch_history/pre_market/manifest.json",
            "experiments/current-finance-barrier-taxonomy-audit.json",
            "experiments/current-forward-protocol-audit.json",
            "experiments/current-deadline-shadow-cycle.json",
            "experiments/current-automation-contract-audit.json",
            "experiments/current-weather-v2-feasibility.json",
            "experiments/current-binary-pair-arbitrage.json",
        ]
        for relative in esports_paths:
            path = root / relative
            if not path.exists():
                continue
            esports = json.loads(path.read_text(encoding="utf-8"))
            if (esports.get("paper_estimates_emitted") is not False
                    or esports.get("main_paper_ledger_mutated") is not False
                    or esports.get("live_orders_enabled") is not False
                    or esports.get("private_api_used") is not False):
                failures.append(f"unsafe_esports_research_flags:{relative}")
        lol_gate_path = root / "experiments/current-lol-external-data-gate.json"
        if lol_gate_path.exists():
            lol_gate = json.loads(lol_gate_path.read_text(encoding="utf-8"))
            if (lol_gate.get("status") != "blocked_compliant_structured_source_missing"
                    or lol_gate.get("max_action") != "market_benchmark_only"
                    or lol_gate.get("model_implemented") is not False
                    or lol_gate.get("final_holdout_inspected") is not False
                    or lol_gate.get("paper_entry_eligible") is not False):
                failures.append("unsafe_lol_external_data_gate")
    except Exception as exc:
        failures.append(f"preflight_read_error:{type(exc).__name__}:{exc}")
    return {
        "name": "safety_preflight", "status": "ok" if not failures else "failed",
        "returncode": 0 if not failures else 1, "duration_seconds": 0.0,
        "failures": failures, "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }
def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Polymarket Validation Cycle", "",
        f"- Cycle ID: `{payload['cycle_id']}`", f"- Status: `{payload['status']}`",
        f"- Duration: {payload['duration_seconds']:.1f}s", f"- Paper only: `{str(payload['paper_only']).lower()}`",
        f"- Markets scanned: {payload.get('markets_scanned')}", f"- Paper opened: {payload.get('paper_opened')}",
        f"- Shadow forecasts opened: {payload.get('shadow_opened')}",
        f"- Weather shadow forecasts opened: {payload.get('weather_shadow_opened')}",
        f"- Football shadow forecasts opened: {payload.get('football_shadow_opened')}",
        f"- Social-count shadow forecasts opened: {payload.get('social_count_shadow_opened')}",
        f"- Stock-weekly shadow forecasts opened: {payload.get('stock_weekly_shadow_opened')}",
        f"- Daily 1–24h status / inventory / complete books: `{payload.get('daily_priority_status')}` / {payload.get('daily_inventory_1_24h_count')} / {payload.get('daily_complete_book_market_count')}",
        f"- Daily selected / opened / decision: {payload.get('daily_selected_candidate_count')} / {payload.get('daily_paper_opened_count')} / `{payload.get('daily_decision')}`",
        f"- Daily candidate observations added / resolutions added / total scans / total resolutions / unresolved: {payload.get('daily_candidate_scan_events_added')} / {payload.get('daily_candidate_resolution_events_added')} / {payload.get('daily_candidate_total_scan_events')} / {payload.get('daily_candidate_total_resolution_events')} / {payload.get('daily_candidate_unresolved_markets')}",
        f"- Binary pair scan / research / main-gate candidates: {payload.get('binary_pair_markets_scanned')} / {payload.get('binary_pair_research_candidate_count')} / {payload.get('binary_pair_main_gate_candidate_count')}",
        f"- Binary pair decision: `{payload.get('binary_pair_decision')}`",
        f"- Hypothetical mint-and-sell economic / condition-proof candidates / general semantics / per-market execution: {payload.get('binary_pair_hypothetical_mint_sell_economic_candidate_count')} / {payload.get('binary_pair_hypothetical_mint_sell_candidate_count')} / `{str(payload.get('binary_pair_mint_sell_general_semantics_validated')).lower()}` / `{str(payload.get('binary_pair_mint_sell_per_market_execution_validated')).lower()}`",
        f"- Binary pair append-only scan observations: {payload.get('binary_pair_research_scan_count')}",
        f"- Next research family: `{payload.get('selected_next_research_family')}`",
        f"- Forward protocol status: `{payload.get('forward_protocol_status')}`",
        f"- Automation runtime freshness: `{payload.get('automation_runtime_status')}`",
        f"- Current/historical missed windows: {payload.get('missed_capture_window_count')} / {payload.get('historical_missed_capture_window_count')}",
        f"- Capture Back Cases added / active / total: {payload.get('capture_backcases_added')} / {payload.get('capture_backcases_active')} / {payload.get('capture_backcases_total')}",
        f"- Open positions: {payload.get('open_positions')}", f"- Closed trades: {payload.get('closed_trades')}",
        f"- Position observations: {payload.get('position_observations')}",
        f"- Pending counterfactuals: {payload.get('pending_counterfactuals')}",
        f"- Goal complete: `{str(payload.get('goal_complete')).lower()}`", "", "## Steps", "",
    ]
    for step in payload.get("steps", []):
        lines.append(f"- `{step['name']}`: {step['status']} ({step['duration_seconds']:.1f}s)")
    lines.extend(["", "This cycle reads only public market data and mutates only the simulated paper ledger and local evidence artifacts.", ""])
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp:
        lock = Path(temp) / "cycle.lock"
        first = acquire_lock(lock); second = acquire_lock(lock)
        assert first["acquired"] is True and second["acquired"] is False
        release_lock(lock); third = acquire_lock(lock); assert third["acquired"] is True
        release_lock(lock)
    assert safety_preflight(ROOT)["status"] == "ok"
    return {"status": "pass", "tests": ["exclusive_lock", "lock_release", "safety_preflight"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one complete Polymarket paper validation cycle")
    parser.add_argument("--lock", default=str(DEFAULT_LOCK)); parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true"); parser.add_argument("--runner-timeout-seconds", type=float, default=300)
    parser.add_argument("--shadow-timeout-seconds", type=float, default=240)
    parser.add_argument("--weather-shadow-timeout-seconds", type=float, default=180)
    parser.add_argument("--football-shadow-timeout-seconds", type=float, default=180)
    parser.add_argument("--social-count-shadow-timeout-seconds", type=float, default=180)
    parser.add_argument("--stock-weekly-shadow-timeout-seconds", type=float, default=240)
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), indent=2)); return 0
    lock_path = Path(args.lock); lock = acquire_lock(lock_path)
    if not lock["acquired"]:
        print(json.dumps({"status": "skipped_locked", "lock": lock}, indent=2)); return 0
    cycle_started = time.monotonic(); cycle_id = datetime.now(timezone.utc).strftime("pm-cycle-%Y%m%dT%H%M%SZ")
    python = sys.executable; steps = []
    try:
        preflight = safety_preflight(ROOT); steps.append(preflight)
        if preflight["status"] != "ok":
            payload = {
                "schema_version": "polymarket-validation-cycle-v1", "cycle_id": cycle_id, "created_at": now_iso(),
                "status": "blocked_by_safety_preflight", "duration_seconds": time.monotonic() - cycle_started,
                "dry_run": args.dry_run, "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
                "lock": lock, "steps": steps, "markets_scanned": None, "paper_opened": [],
                "open_positions": None, "closed_trades": None, "goal_complete": False,
            }
            atomic_json(ROOT / "experiments/current-validation-cycle.json", payload)
            report = ROOT / "reports/CURRENT_VALIDATION_CYCLE.md"; temp = report.with_suffix(report.suffix + ".tmp"); temp.write_text(markdown(payload), encoding="utf-8"); temp.replace(report)
            print(json.dumps({"cycle_id": cycle_id, "status": payload["status"], "failures": preflight["failures"]}, indent=2)); return 1
        automation_contract = run_step("automation_contract_audit", [python, "scripts/polymarket_automation_contract_audit.py"], 30)
        steps.append(automation_contract)
        if automation_contract["status"] != "ok":
            payload = {
                "schema_version": "polymarket-validation-cycle-v1", "cycle_id": cycle_id, "created_at": now_iso(),
                "status": "blocked_by_automation_contract", "duration_seconds": time.monotonic() - cycle_started,
                "dry_run": args.dry_run, "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
                "lock": lock, "steps": steps, "markets_scanned": None, "paper_opened": [],
                "open_positions": None, "closed_trades": None, "goal_complete": False,
            }
            atomic_json(ROOT / "experiments/current-validation-cycle.json", payload)
            report = ROOT / "reports/CURRENT_VALIDATION_CYCLE.md"; temp = report.with_suffix(report.suffix + ".tmp"); temp.write_text(markdown(payload), encoding="utf-8"); temp.replace(report)
            print(json.dumps({"cycle_id": cycle_id, "status": payload["status"], "automation_contract": automation_contract}, indent=2)); return 1
        steps.append(run_step("automation_runtime_audit", [python, "scripts/polymarket_automation_runtime_audit.py"], 30))
        steps.append(run_step("binance_window_export", [python, "scripts/binance_window_exporter.py"], 60))
        steps.append(run_step("stage2_frozen_model_audit", [python, "scripts/polymarket_stage2_model_audit.py"], 60))
        steps.append(run_step("model_registry_refresh", [python, "scripts/polymarket_model_registry.py"], 60))
        if not args.dry_run:
            # Deadline-sensitive shadows do not depend on the full-market snapshot.
            # Capture them before the heavier runner so an in-window cycle cannot
            # drift past a fixed cutoff while pagination is still running.
            steps.append(run_step("weather_shadow_forward_forecasts", [
                python, "scripts/polymarket_weather_shadow.py",
                "--ledger", "data/weather_research_forecast_ledger.json",
                "--output", "experiments/current-weather-shadow-cycle.json",
            ], args.weather_shadow_timeout_seconds))
            steps.append(run_step("football_shadow_forward_forecasts", [
                python, "scripts/polymarket_football_shadow.py",
                "--ledger", "data/football_research_forecast_ledger.json",
                "--output", "experiments/current-football-shadow-cycle.json",
            ], args.football_shadow_timeout_seconds))
            steps.append(run_step("social_count_shadow_forward_forecasts", [
                python, "scripts/polymarket_social_count_shadow.py",
                "--ledger", "data/social_count_research_forecast_ledger.json",
                "--output", "experiments/current-social-count-shadow-cycle.json",
            ], args.social_count_shadow_timeout_seconds))
            steps.append(run_step("stock_weekly_shadow_forward_forecasts", [
                python, "scripts/polymarket_stock_weekly_shadow.py",
                "--ledger", "data/stock_weekly_research_forecast_ledger.json",
                "--ohlc-dir", "cache/current_stock_weekly_shadow_ohlc",
                "--output", "experiments/current-stock-weekly-shadow-cycle.json",
            ], args.stock_weekly_shadow_timeout_seconds))
        runner_command = [
            python, "scripts/polymarket_runner.py", "--max-markets", "0", "--book-limit", "50",
            "--keyset-max-pages", "30", "--keyset-wall-clock-seconds", "90",
            "--estimates-json", "experiments/current-crypto-barrier-estimates.json",
            "--snapshot-dir", "cache/current_validation_snapshot",
            "--output", "experiments/current-validation-runner.json",
            "--report", "reports/CURRENT_VALIDATION_RUNNER.md",
        ]
        if args.dry_run: runner_command.append("--dry-run")
        steps.append(run_step("polymarket_runner", runner_command, args.runner_timeout_seconds))
        if not args.dry_run:
            steps.append(run_step("daily_1_24h_priority", [
                python, "scripts/polymarket_daily_priority.py", "--lock-held",
                "--snapshot-dir", "cache/current_validation_snapshot",
                "--output", "experiments/current-daily-priority-cycle.json",
                "--report", "reports/CURRENT_DAILY_PRIORITY.md",
            ], 240))
            steps.append(run_step("daily_manual_decision_report", [
                python, "scripts/polymarket_daily_manual_report.py",
                "--daily", "experiments/current-daily-priority-cycle.json",
                "--ledger", "data/paper_ledger.json",
                "--output", "experiments/current-daily-manual-decision.json",
                "--report", "reports/CURRENT_DAILY_MANUAL_DECISION.md",
            ], 30))
            steps.append(run_step("daily_football_tennis_decision_report", [
                python, "scripts/polymarket_daily_sports_report.py",
                "--research", "experiments/current-sports-event-research.json",
                "--output", "experiments/current-daily-sports-decision.json",
                "--report", "reports/CURRENT_DAILY_SPORTS_DECISION.md",
                "--ledger", "data/sports_decision_observation_ledger.json",
            ], 240))
            steps.append(run_step("daily_sports_acceptance", [
                python, "scripts/polymarket_daily_sports_acceptance.py",
            ], 30))
            steps.append(run_step("manual_decision_append_only_ledger", [python, "scripts/polymarket_manual_decision_ledger.py"], 30))
            steps.append(run_step("daily_candidate_forward_ledger", [
                python, "scripts/polymarket_daily_candidate_ledger.py",
                "--daily", "experiments/current-daily-priority-cycle.json",
                "--ledger", "data/daily_candidate_observation_ledger.json",
                "--output", "experiments/current-daily-candidate-observation-cycle.json",
                "--report", "reports/CURRENT_DAILY_CANDIDATE_OBSERVATIONS.md",
            ], 90))
            steps.append(run_step("daily_forward_probability_benchmark", [
                python, "scripts/polymarket_daily_forward_benchmark.py",
                "--ledger", "data/daily_candidate_observation_ledger.json",
                "--output", "experiments/current-daily-forward-benchmark.json",
                "--report", "reports/CURRENT_DAILY_FORWARD_BENCHMARK.md",
            ], 30))
            steps.append(run_step("daily_lolesports_gpr_coverage", [
                python, "scripts/polymarket_lolesports_gpr_lab.py", "--only-if-daily-esports",
                "--output", "experiments/current-lolesports-gpr-research.json",
                "--report", "reports/CURRENT_DAILY_LOLESPORTS_GPR.md",
            ], 90))
            steps.append(run_step("binary_pair_structural_scan", [
                python, "scripts/polymarket_binary_pair_arbitrage.py",
                "--snapshot-dir", "cache/current_validation_snapshot",
                "--output", "experiments/current-binary-pair-arbitrage.json",
                "--report", "reports/CURRENT_BINARY_PAIR_ARBITRAGE.md",
            ], 240))
            steps.append(run_step("market_family_audit", [
                python, "scripts/polymarket_market_family_audit.py",
                "--output", "experiments/current-market-family-audit.json",
                "--report", "reports/CURRENT_MARKET_FAMILY_AUDIT.md",
                "--protocol", "experiments/current-next-family-research-protocol.json",
            ], 60))
            steps.append(run_step("shadow_forward_forecasts", [
                python, "scripts/polymarket_shadow_forecaster.py",
                "--snapshot-dir", "cache/current_validation_snapshot",
                "--ledger", "data/research_forecast_ledger.json",
                "--kline-dir", "cache/current_shadow_klines",
                "--output", "experiments/current-shadow-forecast-cycle.json",
            ], args.shadow_timeout_seconds))
            steps.append(run_step("forward_protocol_audit", [
                python, "scripts/polymarket_forward_protocol_audit.py",
                "--output", "experiments/current-forward-protocol-audit.json",
                "--report", "reports/CURRENT_FORWARD_PROTOCOL_AUDIT.md",
            ], 60))
            steps.append(run_step("capture_backcase_cycle", [python, "scripts/polymarket_capture_backcase.py"], 30))
            steps.append(run_step("daily_weekly_monthly_review", [python, "scripts/polymarket_periodic_review.py"], 30))
        steps.append(run_step("same_window_comparison", [python, "scripts/polymarket_window_comparator.py"], 60))
        steps.append(run_step("goal_audit", [
            python, "scripts/polymarket_alpha.py", "audit", "--ledger", "data/paper_ledger.json",
            "--binance-metrics-json", "experiments/current-binance-30d-windows.json",
            "--validation-metrics-json", "experiments/20260711-historical-market-baseline.json",
            "--output", "experiments/current-goal-audit.json", "--report", "reports/CURRENT_GOAL_AUDIT.md",
        ], 60))
        steps.append(run_step("stage2_readiness_board", [python, "scripts/polymarket_stage2_readiness.py"], 60))
        runner = json.loads((ROOT / "experiments/current-validation-runner.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-validation-runner.json").exists() else {}
        audit = json.loads((ROOT / "experiments/current-goal-audit.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-goal-audit.json").exists() else {}
        ledger = json.loads((ROOT / "data/paper_ledger.json").read_text(encoding="utf-8")) if (ROOT / "data/paper_ledger.json").exists() else {}
        shadow = json.loads((ROOT / "experiments/current-shadow-forecast-cycle.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-shadow-forecast-cycle.json").exists() else {}
        weather_shadow = json.loads((ROOT / "experiments/current-weather-shadow-cycle.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-weather-shadow-cycle.json").exists() else {}
        football_shadow = json.loads((ROOT / "experiments/current-football-shadow-cycle.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-football-shadow-cycle.json").exists() else {}
        social_count_shadow = json.loads((ROOT / "experiments/current-social-count-shadow-cycle.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-social-count-shadow-cycle.json").exists() else {}
        stock_weekly_shadow = json.loads((ROOT / "experiments/current-stock-weekly-shadow-cycle.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-stock-weekly-shadow-cycle.json").exists() else {}
        forward_protocol = json.loads((ROOT / "experiments/current-forward-protocol-audit.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-forward-protocol-audit.json").exists() else {}
        family_audit = json.loads((ROOT / "experiments/current-market-family-audit.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-market-family-audit.json").exists() else {}
        binary_pair = json.loads((ROOT / "experiments/current-binary-pair-arbitrage.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-binary-pair-arbitrage.json").exists() else {}
        automation_runtime = json.loads((ROOT / "experiments/current-automation-runtime-audit.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-automation-runtime-audit.json").exists() else {}
        capture_backcases = json.loads((ROOT / "experiments/current-capture-backcase-cycle.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-capture-backcase-cycle.json").exists() else {}
        daily_priority = json.loads((ROOT / "experiments/current-daily-priority-cycle.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-daily-priority-cycle.json").exists() else {}
        daily_candidates = json.loads((ROOT / "experiments/current-daily-candidate-observation-cycle.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-daily-candidate-observation-cycle.json").exists() else {}
        daily_manual = json.loads((ROOT / "experiments/current-daily-manual-decision.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-daily-manual-decision.json").exists() else {}
        daily_sports = json.loads((ROOT / "experiments/current-daily-sports-decision.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-daily-sports-decision.json").exists() else {}
        daily_benchmark = json.loads((ROOT / "experiments/current-daily-forward-benchmark.json").read_text(encoding="utf-8")) if (ROOT / "experiments/current-daily-forward-benchmark.json").exists() else {}
        all_steps_ok = all(step["status"] == "ok" for step in steps)
        runner_ok = runner.get("status") == "ok"
        payload = {
            "schema_version": "polymarket-validation-cycle-v1", "cycle_id": cycle_id, "created_at": now_iso(),
            "status": "ok" if all_steps_ok and runner_ok else "degraded_no_new_entry",
            "duration_seconds": time.monotonic() - cycle_started, "dry_run": args.dry_run,
            "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
            "lock": lock, "steps": steps, "runner_status": runner.get("status"),
            "markets_scanned": (runner.get("scan") or {}).get("markets_scanned"),
            "paper_opened": (runner.get("paper_entry") or {}).get("opened", []),
            "shadow_opened": shadow.get("opened", []) if not args.dry_run else [],
            "shadow_open_forecasts": shadow.get("open_forecasts") if not args.dry_run else None,
            "weather_shadow_opened": weather_shadow.get("opened", []) if not args.dry_run else [],
            "weather_shadow_open_forecasts": weather_shadow.get("open_forecasts") if not args.dry_run else None,
            "football_shadow_opened": football_shadow.get("opened", []) if not args.dry_run else [],
            "football_shadow_open_forecasts": football_shadow.get("open_forecasts") if not args.dry_run else None,
            "social_count_shadow_opened": social_count_shadow.get("opened", []) if not args.dry_run else [],
            "social_count_shadow_open_forecasts": social_count_shadow.get("open_forecasts") if not args.dry_run else None,
            "stock_weekly_shadow_opened": stock_weekly_shadow.get("opened", []) if not args.dry_run else [],
            "stock_weekly_shadow_open_forecasts": stock_weekly_shadow.get("open_forecasts") if not args.dry_run else None,
            "daily_priority_status": daily_priority.get("status") if not args.dry_run else None,
            "daily_inventory_1_24h_count": daily_priority.get("inventory_1_24h_count") if not args.dry_run else None,
            "daily_complete_book_market_count": daily_priority.get("complete_two_sided_book_market_count") if not args.dry_run else None,
            "daily_selected_candidate_count": daily_priority.get("selected_candidate_count") if not args.dry_run else None,
            "daily_paper_opened_count": daily_priority.get("paper_opened_count") if not args.dry_run else None,
            "daily_decision": daily_priority.get("decision") if not args.dry_run else None,
            "daily_manual_report_status": daily_manual.get("status") if not args.dry_run else None,
            "daily_manual_watch_item_count": daily_manual.get("watch_item_count") if not args.dry_run else None,
            "daily_manual_recommendation_count": daily_manual.get("recommendation_count") if not args.dry_run else None,
            "daily_real_money_execution_authorized": daily_manual.get("real_money_execution_authorized") if not args.dry_run else None,
            "daily_sports_scanned_event_count": daily_sports.get("scanned_event_count") if not args.dry_run else None,
            "daily_sports_main_recommendation_count": len(daily_sports.get("main_recommendations") or []) if not args.dry_run else None,
            "daily_sports_conditional_candidate_count": len(daily_sports.get("conditional_candidates") or []) if not args.dry_run else None,
            "daily_sports_real_money_execution_authorized": daily_sports.get("real_money_execution_authorized") if not args.dry_run else None,
            "daily_forward_resolved_condition_count": daily_benchmark.get("resolved_condition_count") if not args.dry_run else None,
            "daily_forward_approved_model_forecast_count": daily_benchmark.get("approved_model_independent_forecast_count") if not args.dry_run else None,
            "daily_forward_promotion_model_versions": daily_benchmark.get("forward_promotion_model_versions") if not args.dry_run else [],
            "daily_candidate_scan_events_added": daily_candidates.get("scan_events_added") if not args.dry_run else None,
            "daily_candidate_resolution_events_added": daily_candidates.get("resolution_events_added") if not args.dry_run else None,
            "daily_candidate_total_scan_events": daily_candidates.get("total_scan_events") if not args.dry_run else None,
            "daily_candidate_total_resolution_events": daily_candidates.get("total_resolution_events") if not args.dry_run else None,
            "daily_candidate_unresolved_markets": daily_candidates.get("unresolved_observed_markets") if not args.dry_run else None,
            "selected_next_research_family": family_audit.get("selected_next_research_family") if not args.dry_run else None,
            "binary_pair_markets_scanned": binary_pair.get("binary_markets_scanned") if not args.dry_run else None,
            "binary_pair_research_candidate_count": binary_pair.get("research_candidate_count") if not args.dry_run else None,
            "binary_pair_main_gate_candidate_count": binary_pair.get("main_gate_candidate_count") if not args.dry_run else None,
            "binary_pair_hypothetical_mint_sell_economic_candidate_count": binary_pair.get("hypothetical_mint_sell_economic_candidate_count") if not args.dry_run else None,
            "binary_pair_hypothetical_mint_sell_candidate_count": binary_pair.get("hypothetical_mint_sell_candidate_count") if not args.dry_run else None,
            "binary_pair_mint_sell_general_semantics_validated": binary_pair.get("mint_sell_general_semantic_contract_validated") if not args.dry_run else None,
            "binary_pair_mint_sell_per_market_execution_validated": binary_pair.get("mint_sell_per_market_execution_validated") if not args.dry_run else None,
            "binary_pair_decision": binary_pair.get("decision") if not args.dry_run else None,
            "binary_pair_research_scan_count": binary_pair.get("research_ledger_scan_count") if not args.dry_run else None,
            "forward_protocol_status": forward_protocol.get("status") if not args.dry_run else None,
            "automation_runtime_status": automation_runtime.get("status"),
            "missed_capture_window_count": forward_protocol.get("missed_capture_window_count") if not args.dry_run else None,
            "historical_missed_capture_window_count": forward_protocol.get("historical_missed_capture_window_count") if not args.dry_run else None,
            "capture_backcases_added": capture_backcases.get("added_count") if not args.dry_run else None,
            "capture_backcases_active": capture_backcases.get("active_backcase_count") if not args.dry_run else None,
            "capture_backcases_total": capture_backcases.get("total_backcase_count") if not args.dry_run else None,
            "open_positions": len(ledger.get("open_positions", [])), "closed_trades": len(ledger.get("closed_positions", [])),
            "position_observations": len(ledger.get("position_observations", [])),
            "pending_counterfactuals": sum(row.get("counterfactual_status") == "pending" for row in ledger.get("closed_positions", [])),
            "cash_usd": ledger.get("cash_usd"), "goal_complete": audit.get("goal_complete", False),
        }
        atomic_json(ROOT / "experiments/current-validation-cycle.json", payload)
        report = ROOT / "reports/CURRENT_VALIDATION_CYCLE.md"; temp = report.with_suffix(report.suffix + ".tmp"); temp.write_text(markdown(payload), encoding="utf-8"); temp.replace(report)
        print(json.dumps({key: payload[key] for key in ("cycle_id", "status", "duration_seconds", "markets_scanned", "paper_opened", "open_positions", "closed_trades", "goal_complete")}, ensure_ascii=False, indent=2))
        return 0 if all_steps_ok else 1
    finally:
        release_lock(lock_path)


if __name__ == "__main__":
    raise SystemExit(main())
