#!/usr/bin/env python3
"""Highest-priority ongoing and next-24h Polymarket paper opportunity cycle."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
MIN_HOURS = 0.0
MAX_HOURS = 24.0
MAX_SNAPSHOT_AGE_SECONDS = 90 * 60
DEFAULT_SNAPSHOT = ROOT / "cache/current_validation_snapshot"
DEFAULT_LEDGER = ROOT / "data/daily_priority_scan_ledger.json"
PROTECTED_ESTIMATE = ROOT / "experiments/current-crypto-barrier-estimates.json"


def market_family(market: dict[str, Any]) -> str:
    """Route a daily market narrowly enough to prevent cross-sport model leakage."""
    text = " ".join(str(market.get(key) or "") for key in ("question", "description", "slug")).lower()
    if "pentakill" in text:
        return "esports_tournament_prop"
    if re.search(r"\bwin\s+(?:the\s+)?msi\b", text):
        return "esports_tournament_outright"
    if " vs " in f" {text} " or " versus " in f" {text} ":
        return "esports_match_winner"
    if any(term in text for term in ("msi", "league of legends", "lolesports")):
        return "esports_unclassified"
    if any(term in text for term in ("tomatometer", "rotten tomatoes")):
        return "entertainment_review_score"
    if any(term in text for term in ("bitcoin", " btc", "ethereum", " eth", "solana", " sol")) and any(
            term in text for term in ("dip to", "hit ", "reach ", "above", "below", "price")):
        return "crypto_price_barrier"
    if any(term in text for term in ("temperature", "degrees fahrenheit", "daily high")):
        return "weather_daily_high"
    if any(term in text for term in ("mlb", "baseball", "world series")):
        return "baseball_match"
    if any(term in text for term in ("world cup", "premier league", "champions league", "soccer", "football")):
        return "football_match"
    return f"{market.get('domain') or 'other'}_unclassified"


def registered_model_family(model: dict[str, Any]) -> str | None:
    explicit = model.get("market_family")
    if explicit:
        return str(explicit)
    version = str(model.get("model_version") or "").lower()
    if "first-passage-barrier" in version: return "crypto_price_barrier"
    if "football" in version: return "football_match"
    if "baseball" in version: return "baseball_match"
    if "lolesports" in version: return "esports_match_winner"
    if "weather" in version: return "weather_daily_high"
    if "social-count" in version: return "social_count"
    if "stock-weekly" in version: return "stock_weekly"
    return None


def build_market_watchlist(decisions: list[dict[str, Any]], model_registry: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in decisions:
        grouped.setdefault(str(row["market_id"]), []).append(row)
    rows = []
    for market_id, sides in grouped.items():
        first = sides[0]; family = market_family(first)
        applicable = [model for model in model_registry.get("models", []) if registered_model_family(model) == family]
        eligible = [model for model in applicable if model.get("paper_estimates_allowed") is True]
        by_side = {str(row["side"]): row for row in sides}
        def midpoint(side: str) -> float | None:
            execution = (by_side.get(side) or {}).get("execution") or {}
            bid, ask = execution.get("best_bid"), execution.get("best_ask")
            return (float(bid) + float(ask)) / 2 if bid is not None and ask is not None else None
        yes_midpoint, no_midpoint = midpoint("YES"), midpoint("NO")
        midpoint_total = (yes_midpoint + no_midpoint) if yes_midpoint is not None and no_midpoint is not None else None
        normalized_yes_midpoint = yes_midpoint / midpoint_total if midpoint_total and midpoint_total > 0 else None
        yes_decision = by_side.get("YES") or first
        model_yes_probability = yes_decision.get("model_yes_probability")
        model_version = yes_decision.get("model_version")
        failures = sorted({reason for row in sides for reason in row.get("failed_gates", [])})
        alpha_selected = any(row.get("gate_passed") is True for row in sides)
        high_win_selected = any(row.get("high_win_small_return_eligible") is True for row in sides)
        rows.append({
            "market_id": market_id, "condition_id": first.get("condition_id"), "question": first.get("question"), "domain": first.get("domain"),
            "market_family": family, "end_date": first.get("end_date"),
            "applicable_registered_model_versions": [model.get("model_version") for model in applicable],
            "applicable_eligible_model_versions": [model.get("model_version") for model in eligible],
            "yes_fill_price": (by_side.get("YES") or {}).get("execution", {}).get("fill_price"),
            "no_fill_price": (by_side.get("NO") or {}).get("execution", {}).get("fill_price"),
            "yes_spread": (by_side.get("YES") or {}).get("execution", {}).get("spread"),
            "no_spread": (by_side.get("NO") or {}).get("execution", {}).get("spread"),
            "yes_price_impact": (by_side.get("YES") or {}).get("execution", {}).get("price_impact"),
            "no_price_impact": (by_side.get("NO") or {}).get("execution", {}).get("price_impact"),
            "yes_midpoint": yes_midpoint, "no_midpoint": no_midpoint,
            "normalized_yes_midpoint": normalized_yes_midpoint,
            "market_consensus_side": ("YES" if normalized_yes_midpoint is not None and normalized_yes_midpoint >= .5
                                      else "NO" if normalized_yes_midpoint is not None else None),
            "market_consensus_probability_proxy": (max(normalized_yes_midpoint, 1 - normalized_yes_midpoint)
                                                     if normalized_yes_midpoint is not None else None),
            "market_consensus_is_model_probability": False,
            "model_version": model_version,
            "model_yes_probability": model_yes_probability,
            "model_yes_confidence_low": yes_decision.get("yes_confidence_low"),
            "model_yes_confidence_high": yes_decision.get("yes_confidence_high"),
            "model_calibration_samples": int(yes_decision.get("calibration_samples") or 0),
            "model_historical_hit_rate": yes_decision.get("historical_hit_rate"),
            "model_brier_score_oos": yes_decision.get("model_brier_score"),
            "market_brier_score_oos": yes_decision.get("market_brier_score"),
            "model_log_loss_oos": yes_decision.get("model_log_loss"),
            "market_log_loss_oos": yes_decision.get("market_log_loss"),
            "model_source_counts": yes_decision.get("source_counts"),
            "model_rules_review": yes_decision.get("rules_review"),
            "approved_model_probability_present": bool(model_version and model_yes_probability is not None),
            "planned_notional_usd": first.get("planned_notional_usd"),
            "failed_gates": failures,
            "route_blocker": ("no_applicable_registered_model" if not applicable
                              else "applicable_model_not_paper_eligible" if not eligible
                              else "no_approved_market_specific_estimate"),
            "action": ("ALPHA_PRIMARY_CANDIDATE" if alpha_selected else
                       "HIGH_WIN_SMALL_RETURN_CANDIDATE" if high_win_selected else "PASS_NO_TRADE"),
        })
    return sorted(rows, key=lambda row: (str(row.get("end_date") or ""), str(row.get("question") or "")))


def family_status_key(family: str) -> str | None:
    if family.startswith("esports_"): return "esports"
    if family == "entertainment_review_score": return "entertainment"
    return None


def family_specific_next_action(family: str, broad: dict[str, Any] | None) -> str:
    if family == "esports_tournament_outright":
        return "require_bracket_path_model_with_comparable_tournament_states_and_fresh_oos"
    if family == "esports_tournament_prop":
        return "require_official_event_level_pentakill_history_and_fresh_oos"
    if family == "entertainment_review_score":
        return "require_at_least_30_independent_same_source_review_score_events_and_point_in_time_features"
    if family == "crypto_price_barrier":
        return "do_not_retune_inspected_holdout; require_materially_different_prefrozen_model_and_fresh_oos"
    return str((broad or {}).get("resume_if") or "wait_for_traceable_model_and_fresh_oos")


def build_model_coverage_queue(watchlist: list[dict[str, Any]], model_registry: dict[str, Any],
                               family_status: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in watchlist: grouped[str(row["market_family"])].append(row)
    models = model_registry.get("models", []); gate = policy["entry_gate"]; queue = []
    for family, rows in grouped.items():
        applicable = [model for model in models if registered_model_family(model) == family]
        eligible = [model for model in applicable if model.get("paper_estimates_allowed") is True]
        broad = (family_status.get("families") or {}).get(family_status_key(family) or "")
        execution_viable = 0
        for row in rows:
            yes_ok = row.get("yes_spread") is not None and row["yes_spread"] <= gate["max_spread_per_share"] and row.get("yes_price_impact") is not None and row["yes_price_impact"] <= gate["max_price_impact_per_share"]
            no_ok = row.get("no_spread") is not None and row["no_spread"] <= gate["max_spread_per_share"] and row.get("no_price_impact") is not None and row["no_price_impact"] <= gate["max_price_impact_per_share"]
            execution_viable += int(yes_ok or no_ok)
        if eligible:
            state = "eligible_model_available"
        elif applicable:
            state = "applicable_model_blocked_or_failed"
        else:
            state = "no_applicable_model"
        queue.append({
            "market_family": family, "current_market_count": len(rows),
            "execution_viable_market_count": execution_viable,
            "applicable_model_versions": [model.get("model_version") for model in applicable],
            "eligible_model_versions": [model.get("model_version") for model in eligible],
            "coverage_state": state, "broad_family_status": (broad or {}).get("model_status"),
            "broad_family_reason": (broad or {}).get("reason"),
            "repeat_tuning_prohibited": bool(applicable and not eligible),
            "next_research_action": family_specific_next_action(family, broad),
            "paper_entry_allowed": bool(eligible),
        })
    return sorted(queue, key=lambda row: (-row["current_market_count"], -row["execution_viable_market_count"], row["market_family"]))


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); return module


core = load("daily_priority_core", ROOT / "scripts/polymarket_alpha.py")
public = load("daily_priority_public", ROOT / "scripts/polymarket_public_data.py")
runner = load("daily_priority_runner", ROOT / "scripts/polymarket_runner.py")
validation = load("daily_priority_validation", ROOT / "scripts/polymarket_validation_cycle.py")
evolver = load("daily_priority_evolver", ROOT / "scripts/polymarket_strategy_evolver.py")


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_contract(snapshot_dir: Path, now: datetime) -> tuple[bool, dict[str, Any]]:
    verification = public.verify_manifest(snapshot_dir); manifest = core.read_json(snapshot_dir / "snapshot-manifest.json")
    created = core.parse_iso(manifest.get("created_at")); age = (now - created).total_seconds() if created else None
    failures = []
    if verification.get("status") != "pass": failures.append("snapshot_manifest_integrity_failed")
    if manifest.get("data_status") != "ok": failures.append("snapshot_data_status_not_ok")
    if manifest.get("terminal_cursor_proven") is not True: failures.append("full_market_terminal_cursor_not_proven")
    if age is None or age < -60 or age > MAX_SNAPSHOT_AGE_SECONDS: failures.append("snapshot_missing_future_or_stale")
    return not failures, {"manifest": manifest, "verification": verification, "age_seconds": age, "failures": failures}


def within_daily_window(market: dict[str, Any], now: datetime) -> bool:
    hours = core.hours_to_expiry(market.get("endDate") or market.get("end_date"), now)
    return bool(hours is not None and MIN_HOURS < hours <= MAX_HOURS)


def daily_inventory(markets: list[dict[str, Any]], now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible, excluded = [], []
    for market in markets:
        if not within_daily_window(market, now):
            continue
        normalized = core.normalize_market(market)
        reasons = []
        if not normalized["active"] or normalized["closed"] or not normalized["accepting_orders"]:
            reasons.append("market_not_tradeable")
        if not normalized["condition_id"]: reasons.append("condition_id_missing")
        outcome_map = {str(outcome).lower(): token for outcome, token in public.token_map(market).items()}
        if set(outcome_map) != {"yes", "no"} or not normalized["yes_token_id"] or not normalized["no_token_id"]:
            reasons.append("binary_tokens_missing")
        if reasons:
            excluded.append({"market_id": normalized["market_id"], "question": normalized["question"], "reasons": reasons})
        else:
            eligible.append(market)
    return eligible, excluded


def fetch_details(markets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conditions = [str(market.get("conditionId") or "") for market in markets]
    details, requests = public.fetch_gamma_details_by_condition(conditions)
    rows = []
    for market in markets:
        condition = str(market.get("conditionId") or "")
        detail = details.get(condition)
        rows.append(detail if detail else market)
    return rows, requests


def reconcile_detail_windows(sampling_markets: list[dict[str, Any]], details: list[dict[str, Any]],
                             now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted, conflicts = [], []
    for sampling, detail in zip(sampling_markets, details):
        if within_daily_window(detail, now):
            accepted.append(detail)
        else:
            conflicts.append({
                "market_id": str(detail.get("id") or detail.get("conditionId") or ""),
                "condition_id": str(detail.get("conditionId") or sampling.get("conditionId") or ""),
                "question": detail.get("question") or sampling.get("question"),
                "reason": "sampling_gamma_end_date_conflict_or_detail_outside_0_24h",
                "sampling_end_date": sampling.get("endDate"), "gamma_end_date": detail.get("endDate"),
            })
    return accepted, conflicts


def fetch_books(markets: list[dict[str, Any]], chunk_size: int = 500) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tokens = list(dict.fromkeys(token for market in markets for token in public.token_map(market).values()))
    books, requests = {}, []
    for index in range(0, len(tokens), chunk_size):
        chunk = tokens[index:index + chunk_size]
        try:
            payload = public.post_json(f"{public.CLOB_BASE}/books", [{"token_id": token} for token in chunk], timeout=30, retries=2)
            if not isinstance(payload, list): raise ValueError("batch books response is not a list")
            for book in payload:
                if isinstance(book, dict) and book.get("asset_id"):
                    books[str(book["asset_id"])] = book
            requests.append({"endpoint": f"{public.CLOB_BASE}/books", "status": "ok", "requested": len(chunk), "returned": sum(token in books for token in chunk)})
        except Exception as exc:
            requests.append({"endpoint": f"{public.CLOB_BASE}/books", "status": "failed", "requested": len(chunk), "error": f"{type(exc).__name__}:{exc}"})
    return books, requests


def attach_fee_contracts(markets: list[dict[str, Any]], max_workers: int = 8) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    info, requests = {}, []
    def fetch_one(condition: str):
        url = f"{public.CLOB_BASE}/clob-markets/{quote(condition, safe='')}"
        return condition, url, public.get_json(url, timeout=20, retries=2)
    conditions = list(dict.fromkeys(str(market.get("conditionId") or "") for market in markets if market.get("conditionId")))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_one, condition): condition for condition in conditions}
        for future in as_completed(futures):
            condition = futures[future]
            try:
                condition, url, payload = future.result(); info[condition] = payload
                requests.append({"endpoint": url, "status": "ok"})
            except Exception as exc:
                requests.append({"condition_id": condition, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
    enriched = []
    for market in markets:
        row = dict(market); condition = str(row.get("conditionId") or "")
        row["fee_contract"] = public.fee_contract(row, {"clob_market_info": info.get(condition)})
        enriched.append(row)
    return enriched, requests


def maximum_acceptable_price(candidate: dict[str, Any], policy: dict[str, Any]) -> float | None:
    probability = candidate.get("model_probability")
    if probability is None: return None
    gate = policy["entry_gate"]; conservative = max(0.0, float(probability) - float(gate["uncertainty_discount"]))
    fee_rate = float(candidate.get("fee_rate") or 0.0); reserve = float(gate["resolution_risk_reserve"])
    required = float(gate["min_net_edge_per_share"]); low, high = 0.0, min(0.999999, conservative)
    for _ in range(60):
        price = (low + high) / 2
        edge = conservative - price - fee_rate * price * (1 - price) - reserve
        if edge >= required: low = price
        else: high = price
    return round(low, 6)


def new_scan_ledger() -> dict[str, Any]:
    return {"schema_version": "polymarket-daily-priority-scan-ledger-v1", "created_at": core.now_iso(),
            "updated_at": core.now_iso(), "observations": [], "paper_only": True,
            "live_orders_enabled": False, "private_api_used": False}


def append_observation(path: Path, payload: dict[str, Any]) -> int:
    ledger = core.read_json(path) if path.exists() else new_scan_ledger()
    if ledger.get("paper_only") is not True or ledger.get("live_orders_enabled") is not False or ledger.get("private_api_used") is not False:
        raise ValueError("unsafe daily priority ledger")
    observation = {key: payload.get(key) for key in (
        "cycle_id", "created_at", "snapshot_manifest_sha256", "inventory_1_24h_count",
        "complete_two_sided_book_market_count", "selected_candidate_count", "paper_opened_count", "decision")}
    if not any(row.get("cycle_id") == observation["cycle_id"] for row in ledger["observations"]):
        ledger["observations"].append(observation)
    ledger["updated_at"] = core.now_iso(); core.write_json(path, ledger); return len(ledger["observations"])


def run(snapshot_dir: Path, paper_ledger_path: Path, scan_ledger_path: Path, lock_held: bool) -> dict[str, Any]:
    lock_path = validation.DEFAULT_LOCK; acquired = None
    if not lock_held:
        acquired = validation.acquire_lock(lock_path)
        if not acquired["acquired"]:
            return {"status": "skipped_locked", "lock": acquired, "paper_only": True,
                    "live_orders_enabled": False, "private_api_used": False}
    try:
        estimate_sha_before = file_sha(PROTECTED_ESTIMATE)
        now = datetime.now(timezone.utc); safe = validation.safety_preflight(ROOT)
        snapshot_ok, snapshot = snapshot_contract(snapshot_dir, now)
        raw_markets = core.read_json(snapshot_dir / "markets.json") if snapshot_ok else []
        inventory, pre_excluded = daily_inventory(raw_markets, now)
        details, detail_requests = fetch_details(inventory) if inventory else ([], [])
        details, detail_time_conflicts = reconcile_detail_windows(inventory, details, now)
        details, fee_requests = attach_fee_contracts(details) if details else ([], [])
        books, book_requests = fetch_books(details) if details else ({}, [])
        policy = core.load_effective_policy(core.read_json(ROOT / "config/policy.json"),
                                            evolver.load_overlay(ROOT / "config/paper_strategy_overlay.json", core.read_json(ROOT / "config/policy.json")))
        ledger = core.load_ledger(paper_ledger_path, policy); core.assert_ledger_safe(ledger)
        market_keys = {str(value) for market in details for value in (market.get("id"), market.get("conditionId")) if value}
        raw_estimates = core.read_json(PROTECTED_ESTIMATE)
        daily_estimates = {key: value for key, value in raw_estimates.items() if str(key) in market_keys}
        model_registry = core.read_json(ROOT / "experiments/current-model-registry.json")
        family_status = core.read_json(ROOT / "experiments/family-research-status.json")
        approved, governance = runner.validate_model_governance(
            daily_estimates, model_registry,
            core.read_json(ROOT / "config/model_approvals.json"))
        scan = core.scan_payload(details, books, approved, policy, float(ledger.get("equity_usd", policy["initial_equity_usd"])))
        complete_books = 0
        for market in details:
            tokens = list(public.token_map(market).values())
            if len(tokens) == 2 and all(core.levels(books.get(token, {}), "bids") and core.levels(books.get(token, {}), "asks") for token in tokens):
                complete_books += 1
        global_blockers = list(snapshot["failures"] if not snapshot_ok else [])
        if safe["status"] != "ok": global_blockers.append("safety_preflight_failed")
        global_blockers.extend(governance.get("blockers") or [])
        if global_blockers:
            entry = {"opened": [], "blocked": global_blockers}
        else:
            entry = core.enter_scan(scan, ledger, policy)
            core.write_json(paper_ledger_path, ledger)
        selected = []
        for candidate in scan["selected_candidates"]:
            selected.append({
                "market_id": candidate["market_id"], "question": candidate["question"], "domain": candidate["domain"],
                "side": candidate["side"], "model_probability": candidate["model_probability"],
                "confidence_low": candidate["confidence_low"], "confidence_high": candidate["confidence_high"],
                "model_version": candidate.get("model_version"), "calibration_samples": candidate.get("calibration_samples"),
                "historical_hit_rate": candidate.get("historical_hit_rate"),
                "model_brier_score": candidate.get("model_brier_score"), "market_brier_score": candidate.get("market_brier_score"),
                "model_log_loss": candidate.get("model_log_loss"), "market_log_loss": candidate.get("market_log_loss"),
                "recommendation_type": candidate.get("recommendation_type"), "tail_risk_label": candidate.get("tail_risk_label"),
                "executable_fill_price": candidate["execution"]["fill_price"],
                "maximum_acceptable_price": maximum_acceptable_price(candidate, policy),
                "net_ev_per_share": candidate["net_ev_per_share"], "paper_amount_usd": candidate["planned_notional_usd"],
                "spread": candidate["execution"]["spread"], "price_impact": candidate["execution"]["price_impact"],
                "entry_fee_per_share": candidate.get("entry_fee_per_share"),
                "extra_slippage_per_share": ((candidate["execution"].get("fill_price") or 0) - (candidate["execution"].get("raw_vwap") or 0)),
                "failure_paths": candidate["failure_paths"],
                "exit_conditions": ["updated_probability_below_executable_market_price", "core_thesis_invalidated",
                                    "market_price_above_model_fair_ceiling", "data_or_resolution_rule_integrity_failure",
                                    "remaining_upside_no_longer_compensates_hold_risk"],
            })
        failures = Counter(reason for row in scan["all_decisions"] for reason in row["failed_gates"])
        watchlist = build_market_watchlist(scan["all_decisions"], model_registry)
        model_coverage_queue = build_model_coverage_queue(watchlist, model_registry, family_status, policy)
        domain_markets: dict[str, set[str]] = {}; domain_keys: dict[str, set[str]] = {}
        for row in scan["all_decisions"]:
            domain = str(row["domain"]); domain_markets.setdefault(domain, set()).add(str(row["market_id"]))
            domain_keys.setdefault(domain, set()).update(str(value) for value in (row.get("market_id"), row.get("condition_id")) if value)
        domain_model_routes = []
        for domain, market_ids in sorted(domain_markets.items()):
            families = {row["market_family"] for row in watchlist if row["domain"] == domain}
            registered = [model for model in model_registry.get("models", []) if registered_model_family(model) in families]
            domain_model_routes.append({
                "domain": domain, "market_count": len(market_ids),
                "market_families": sorted(families),
                "registered_model_versions": [model.get("model_version") for model in registered],
                "eligible_model_versions": [model.get("model_version") for model in registered if model.get("paper_estimates_allowed") is True],
                "status": "approved_market_specific_estimate_present" if any(key in approved for key in domain_keys[domain])
                          else ("no_applicable_registered_model" if not registered
                                else "no_approved_market_specific_estimate"),
            })
        decision = "paper_candidates_opened" if entry.get("opened") else "cash_no_daily_candidate_passed_all_gates"
        payload = {
            "schema_version": "polymarket-daily-priority-cycle-v1", "cycle_id": core.stable_id("pm-daily-priority", now.isoformat()),
            "created_at": core.now_iso(), "status": "ok" if not global_blockers else "blocked",
            "priority": "ongoing_then_same_day_then_next_24h", "horizon_hours": [MIN_HOURS, MAX_HOURS],
            "snapshot_manifest_sha256": file_sha(snapshot_dir / "snapshot-manifest.json") if snapshot_dir.exists() else None,
            "snapshot_age_seconds": snapshot.get("age_seconds"), "snapshot_contract_failures": snapshot.get("failures"),
            "sampling_inventory_1_24h_count": len(inventory),
            "inventory_1_24h_count": len(details),
            "pre_execution_excluded_count": len(pre_excluded) + len(detail_time_conflicts),
            "sampling_gamma_end_date_conflict_count": len(detail_time_conflicts),
            "sampling_gamma_end_date_conflicts": detail_time_conflicts,
            "gamma_detail_request_count": len(detail_requests), "fee_request_count": len(fee_requests),
            "book_batch_request_count": len(book_requests), "books_returned": len(books),
            "complete_two_sided_book_market_count": complete_books,
            "model_estimates_received": len(daily_estimates), "model_estimates_approved": len(approved),
            "model_governance": governance, "domain_model_routes": domain_model_routes,
            "market_watchlist": watchlist, "model_coverage_queue": model_coverage_queue,
            "selected_candidate_count": len(selected), "selected_candidates": selected,
            "paper_opened_count": len(entry.get("opened", [])), "paper_opened": entry.get("opened", []),
            "entry_blockers": entry.get("blocked", []), "top_failed_gates": dict(failures.most_common(20)),
            "decision": decision, "all_decisions": scan["all_decisions"],
            "formal_estimate_sha256_before": estimate_sha_before,
            "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        }
        estimate_sha_after = file_sha(PROTECTED_ESTIMATE)
        if estimate_sha_before != estimate_sha_after:
            raise RuntimeError("daily priority cycle mutated formal estimates")
        payload["formal_estimate_sha256_after"] = estimate_sha_after
        payload["formal_estimate_artifact_unchanged"] = True
        payload["scan_ledger_observation_count"] = append_observation(scan_ledger_path, payload)
        return payload
    finally:
        if acquired and acquired.get("acquired"): validation.release_lock(lock_path)


def markdown(payload: dict[str, Any]) -> str:
    lines = ["# Polymarket Daily 1–24h Priority", "", f"- Status: `{payload['status']}`",
             f"- Decision: `{payload['decision']}`", f"- 1–24h inventory: {payload['inventory_1_24h_count']}",
             f"- Sampling/Gamma end-date conflicts excluded: {payload['sampling_gamma_end_date_conflict_count']}",
             f"- Complete two-sided books: {payload['complete_two_sided_book_market_count']}",
             f"- Approved estimates: {payload['model_estimates_approved']}",
             f"- Selected / opened: {payload['selected_candidate_count']} / {payload['paper_opened_count']}", ""]
    if payload["selected_candidates"]:
        lines.extend(["| Side | Question | Probability | CI low | Fill | Max price | Net EV | Paper USD |",
                      "|---|---|---:|---:|---:|---:|---:|---:|"])
        for row in payload["selected_candidates"]:
            lines.append(f"| {row['side']} | {row['question']} | {row['model_probability']:.3f} | {row['confidence_low']:.3f} | {row['executable_fill_price']:.3f} | {row['maximum_acceptable_price']:.3f} | {row['net_ev_per_share']:.3f} | {row['paper_amount_usd']:.2f} |")
    else:
        lines.extend(["No market passed every probability, source, rule, calibration, spread, depth, friction and net-EV gate. Correct action: cash.", ""])
    lines.extend(["## Explicit execution list", "", "Prices are modeled executable fills for the displayed paper notional; they are not recommendations or approved probabilities.", "",
                  "| Action | Question | Family | Ends UTC | YES fill | NO fill | Spread Y/N | Impact Y/N | Depth test USD | Main blocker |",
                  "|---|---|---|---|---:|---:|---:|---:|---:|---|"])
    for row in payload["market_watchlist"]:
        main_blocker = "none" if row["action"] in {"ALPHA_PRIMARY_CANDIDATE", "HIGH_WIN_SMALL_RETURN_CANDIDATE"} else row["route_blocker"]
        fmt = lambda value: "—" if value is None else f"{value:.3f}"
        lines.append(f"| {row['action']} | {row['question']} | {row['market_family']} | {row['end_date']} | {fmt(row['yes_fill_price'])} | {fmt(row['no_fill_price'])} | {fmt(row['yes_spread'])}/{fmt(row['no_spread'])} | {fmt(row['yes_price_impact'])}/{fmt(row['no_price_impact'])} | {float(row['planned_notional_usd'] or 0):.2f} | {main_blocker} |")
    lines.extend(["", "## Exact event-family model routing", "", "| Domain | Families | Markets | Applicable models | Eligible models | Status |",
                  "|---|---|---:|---:|---:|---|"])
    for row in payload["domain_model_routes"]:
        lines.append(f"| {row['domain']} | {', '.join(row['market_families'])} | {row['market_count']} | {len(row['registered_model_versions'])} | {len(row['eligible_model_versions'])} | {row['status']} |")
    lines.extend(["", "## Model coverage and research queue", "", "| Family | Markets | Execution viable | Coverage | Repeat tuning | Next evidence action |",
                  "|---|---:|---:|---|---:|---|"])
    for row in payload["model_coverage_queue"]:
        lines.append(f"| {row['market_family']} | {row['current_market_count']} | {row['execution_viable_market_count']} | {row['coverage_state']} | {str(row['repeat_tuning_prohibited']).lower()} | {row['next_research_action']} |")
    lines.extend(["", "A failed or blocked model is never retuned against an already inspected holdout. A materially different model requires a pre-frozen protocol and fresh OOS evidence.", "", "## Top gate failures", ""])
    for reason, count in payload["top_failed_gates"].items(): lines.append(f"- {reason}: {count}")
    return "\n".join(lines) + "\n"


def self_test() -> dict[str, Any]:
    now = datetime(2026, 7, 12, 12, tzinfo=timezone.utc)
    market = {"endDate": "2026-07-13T00:00:00+00:00"}; assert within_daily_window(market, now)
    assert not within_daily_window({"endDate": "2026-07-14T00:00:00+00:00"}, now)
    policy = core.read_json(ROOT / "config/policy.json")
    candidate = {"model_probability": .85, "fee_rate": .04}
    cap = maximum_acceptable_price(candidate, policy); assert cap is not None and cap < .85
    return {"status": "pass", "tests": ["strict_1_24h_window", "friction_adjusted_max_price"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT)); parser.add_argument("--lock-held", action="store_true")
    parser.add_argument("--paper-ledger", default=str(ROOT / "data/paper_ledger.json")); parser.add_argument("--scan-ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--output", default=str(ROOT / "experiments/current-daily-priority-cycle.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_DAILY_PRIORITY.md")); args = parser.parse_args()
    payload = self_test() if args.self_test else run(Path(args.snapshot_dir), Path(args.paper_ledger), Path(args.scan_ledger), args.lock_held)
    if not args.self_test:
        core.write_json(Path(args.output), payload); Path(args.report).write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload if args.self_test else {key: payload.get(key) for key in ("status", "decision", "inventory_1_24h_count", "complete_two_sided_book_market_count", "model_estimates_approved", "selected_candidate_count", "paper_opened_count")}, ensure_ascii=False, indent=2)); return 0 if payload.get("status") in {"ok", "pass"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
