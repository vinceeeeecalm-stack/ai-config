#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config" / "policy.json"
DEFAULT_OVERLAY = ROOT / "config" / "paper_strategy_overlay.json"
DEFAULT_LEDGER = ROOT / "data" / "paper_ledger.json"
DOMAIN_ROUTER_VERSION = "polymarket-domain-router-v4-daily-disambiguation"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def load_effective_policy(
    policy: dict[str, Any], overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply only conservative, paper-only changes from the strategy overlay."""
    effective = deepcopy(policy)
    effective["paper_only"] = True
    effective["live_orders_enabled"] = False
    effective["private_api_used"] = False
    controls = {"blocked_model_versions": [], "probability_caps": []}
    active_ids: list[str] = []
    if overlay:
        if overlay.get("paper_only") is not True or overlay.get("live_orders_enabled") is not False or overlay.get("private_api_used") is not False:
            raise ValueError("unsafe paper strategy overlay")
        gate = effective["entry_gate"]
        for change in overlay.get("active_changes", []):
            if change.get("status") != "paper_applied" or change.get("paper_only") is not True:
                continue
            proposal = change.get("proposal") or {}
            kind, value = proposal.get("type"), proposal.get("value")
            try:
                if kind == "block_model_version":
                    model = str(proposal.get("model_version") or value or "")
                    if not model:
                        continue
                    controls["blocked_model_versions"].append(model)
                elif kind == "probability_cap" and 0 < float(value) <= 1:
                    controls["probability_caps"].append({
                        "value": float(value), "domain": proposal.get("domain"),
                        "model_version": proposal.get("model_version"), "change_id": change["change_id"],
                    })
                elif kind == "raise_min_net_edge" and float(value) >= gate["min_net_edge_per_share"]:
                    gate["min_net_edge_per_share"] = float(value)
                elif kind == "raise_min_independent_sources" and int(value) >= gate["min_independent_sources"]:
                    gate["min_independent_sources"] = int(value)
                elif kind == "raise_min_calibration_samples" and int(value) >= gate["min_calibration_samples"]:
                    gate["min_calibration_samples"] = int(value)
                elif kind == "lower_max_spread" and 0 < float(value) <= gate["max_spread_per_share"]:
                    gate["max_spread_per_share"] = float(value)
                elif kind == "lower_max_price_impact" and 0 < float(value) <= gate["max_price_impact_per_share"]:
                    gate["max_price_impact_per_share"] = float(value)
                else:
                    continue
            except (TypeError, ValueError):
                continue
            active_ids.append(str(change["change_id"]))
    active_ids = sorted(set(active_ids))
    controls["blocked_model_versions"] = sorted(set(controls["blocked_model_versions"]))
    effective["paper_overlay_controls"] = controls
    effective["overlay_change_ids"] = active_ids
    effective["effective_strategy_version"] = stable_id("pm-strategy", policy["version"], *active_ids)
    return effective


def parse_jsonish(value: Any, default: Any) -> Any:
    if value is None:
        return deepcopy(default)
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return deepcopy(default)


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def route_domain_details(market: dict[str, Any]) -> dict[str, str]:
    """Question-first domain routing with an auditable, conservative fallback.

    Descriptions often contain incidental words such as ``game``, ``software``
    or the name of a sporting venue. They are resolution evidence, not reliable
    topic labels, so V3 deliberately excludes them from classification.
    """
    tags = market.get("tags") if isinstance(market.get("tags"), list) else []
    question = " ".join(str(market.get(key, "")) for key in ("question", "slug", "market_slug")).lower()
    metadata = " ".join([str(market.get("category", "")), *[str(tag) for tag in tags]]).lower()

    def contains(text: str, *keywords: str) -> bool:
        return any(re.search(rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])", text) for keyword in keywords)

    def result(domain: str, confidence: str, reason: str) -> dict[str, str]:
        return {"domain": domain, "confidence": confidence, "reason": reason}

    # These are event subjects, not sports outcomes, even when the venue is a match.
    if contains(question, "perform", "halftime show", "album", "song", "concert", "box office", "oscar", "grammy", "movie", "rotten tomatoes", "tomatometer"):
        return result("other", "high", "question_entertainment_event")

    # Behavioral/count markets take precedence over the identity of the speaker.
    social_terms = ("post", "posts", "posted", "tweet", "tweets", "followers", "mention", "mentions", "post count", "say", "said")
    if contains(question, *social_terms):
        return result("social_count", "high", "question_social_behavior_or_count")

    if contains(question, "fed", "fomc", "cpi", "gdp", "inflation", "interest rate", "base rate", "key rate", "unemployment", "nonfarm payrolls", "pce", "central bank", "bank of korea", "bank of russia", "ecb", "bank of england"):
        return result("macro", "high", "question_macro_release_or_policy")
    if contains(question, "election", "president", "senate", "congress", "minister", "vote", "politics", "political", "governor", "parliament", "primary", "nominee", "invade", "invasion", "ceasefire", "capture", "annex", "war", "sanction"):
        return result("politics", "high", "question_politics_or_geopolitics")
    if contains(question, "bitcoin", "ethereum", "crypto", "token", "btc", "eth", "solana", "defi", "blockchain", "stablecoin"):
        return result("crypto", "high", "question_crypto")
    if (contains(question, "world cup", "nba", "nfl", "mlb", "nhl", "ufc", "wimbledon", "tennis", "soccer", "football", "basketball", "baseball", "hockey", "match", "league", "esports", "tournament", "pentakill")
            or re.search(r"\b(?:win|at)\s+(?:the\s+)?msi\b", question)):
        return result("sports", "high", "question_sport_competition")
    if contains(question, "openai", "apple", "google", "microsoft", "ai model", "artificial intelligence", "technology", "software", "gemini", "chatgpt", "iphone", "android"):
        return result("technology", "high", "question_technology")

    # Official category/tags may resolve terse questions, but never override an
    # explicit question classification above.
    if contains(metadata, "economy", "global rates", "fed", "fomc", "gdp", "inflation", "central bank"):
        return result("macro", "medium", "official_metadata_macro")
    if contains(metadata, "politics", "elections", "geopolitics", "ukraine", "middle east", "government"):
        return result("politics", "medium", "official_metadata_politics")
    if contains(metadata, "crypto", "bitcoin", "ethereum", "defi", "blockchain"):
        return result("crypto", "medium", "official_metadata_crypto")
    if contains(metadata, "sports", "soccer", "tennis", "basketball", "baseball", "hockey", "esports"):
        return result("sports", "medium", "official_metadata_sports")
    if contains(metadata, "technology", "ai", "artificial intelligence", "software"):
        return result("technology", "medium", "official_metadata_technology")
    if contains(metadata, "mentions", "social media"):
        return result("social_count", "medium", "official_metadata_social_count")
    return result("other", "low", "no_supported_domain_signal")


def route_domain(market: dict[str, Any]) -> str:
    return route_domain_details(market)["domain"]


def normalize_market(raw: dict[str, Any]) -> dict[str, Any]:
    outcomes = parse_jsonish(raw.get("outcomes"), [])
    prices = parse_jsonish(raw.get("outcomePrices", raw.get("outcome_prices")), [])
    token_ids = parse_jsonish(raw.get("clobTokenIds"), [])
    normalized_tokens = raw.get("tokens") if isinstance(raw.get("tokens"), dict) else {}
    yes_index = next((i for i, item in enumerate(outcomes) if str(item).lower() == "yes"), 0)
    no_index = next((i for i, item in enumerate(outcomes) if str(item).lower() == "no"), 1 if len(outcomes) > 1 else 0)
    yes_token = token_ids[yes_index] if yes_index < len(token_ids) else next(
        (token for outcome, token in normalized_tokens.items() if str(outcome).lower() == "yes"),
        raw.get("yes_token_id"),
    )
    yes_price = as_float(prices[yes_index], None) if yes_index < len(prices) else as_float(raw.get("yes_price"), None)
    no_token = token_ids[no_index] if no_index < len(token_ids) else next(
        (token for outcome, token in normalized_tokens.items() if str(outcome).lower() == "no"),
        raw.get("no_token_id"),
    )
    no_price = as_float(prices[no_index], None) if no_index < len(prices) else as_float(raw.get("no_price"), None)
    fee_contract = raw.get("fee_contract") if isinstance(raw.get("fee_contract"), dict) else {}
    fee_schedule = raw.get("feeSchedule") if isinstance(raw.get("feeSchedule"), dict) else {}
    fees_enabled = bool(raw.get("feesEnabled", fee_contract.get("fees_enabled", False)))
    fee_rate = as_float(
        fee_contract.get("fee_rate", fee_schedule.get("rate", fee_schedule.get("r"))), None
    )
    events = raw.get("events") if isinstance(raw.get("events"), list) else []
    event = events[0] if events and isinstance(events[0], dict) else {}
    event_id = raw.get("event_id")
    if event_id is None and events and isinstance(events[0], dict):
        event_id = events[0].get("id")
    routing = route_domain_details(raw)
    return {
        "market_id": str(raw.get("id") or raw.get("conditionId") or raw.get("market_id") or ""),
        "gamma_market_id": str(raw.get("id")) if raw.get("id") and not str(raw.get("id")).startswith("0x") else None,
        "condition_id": raw.get("conditionId") or raw.get("condition_id"),
        "event_id": str(event_id) if event_id is not None else None,
        "question": raw.get("question", ""),
        "description": raw.get("description", ""),
        "slug": raw.get("slug"),
        "domain": routing["domain"],
        "domain_route_confidence": routing["confidence"],
        "domain_route_reason": routing["reason"],
        "end_date": raw.get("endDate") or raw.get("end_date"),
        "game_start_time": (raw.get("gameStartTime") or raw.get("game_start_time")
                            or event.get("gameStartTime") or event.get("startDate")),
        "event_status": raw.get("eventStatus") or raw.get("event_status") or event.get("status"),
        "live_score": raw.get("liveScore") or raw.get("live_score") or event.get("score"),
        "live_state_updated_at": (raw.get("liveStateUpdatedAt") or raw.get("live_state_updated_at")
                                  or event.get("updatedAt")),
        "resolution_source": raw.get("resolutionSource") or raw.get("resolution_source"),
        "active": bool(raw.get("active", True)),
        "closed": bool(raw.get("closed", False)),
        "accepting_orders": bool(raw.get("acceptingOrders", raw.get("accepting_orders", True))),
        "yes_token_id": str(yes_token or ""),
        "no_token_id": str(no_token or ""),
        "market_yes_price": yes_price,
        "market_no_price": no_price,
        "raw_liquidity_usd": as_float(raw.get("liquidityNum", raw.get("liquidity")), 0.0),
        "raw_volume_usd": as_float(raw.get("volumeNum", raw.get("volume")), 0.0),
        "fees_enabled": fees_enabled,
        "fee_rate": fee_rate,
        "fee_schedule_source": fee_contract.get("source", "gamma.feeSchedule" if fee_schedule else "missing"),
    }


def taker_fee_usd(shares: float, price: float, fee_rate: float, decimals: int = 5) -> float:
    if shares <= 0 or not 0 < price < 1 or fee_rate <= 0:
        return 0.0
    fee = shares * fee_rate * price * (1 - price)
    return round(fee, decimals) if fee >= 10 ** (-decimals) else 0.0


def hours_to_expiry(end_date: Any, now: datetime | None = None) -> float | None:
    if not end_date:
        return None
    try:
        parsed = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (parsed - (now or datetime.now(timezone.utc))).total_seconds() / 3600
    except ValueError:
        return None


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def levels(book: dict[str, Any], side: str) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    for level in book.get(side, []) or []:
        price = as_float(level.get("price") if isinstance(level, dict) else level[0])
        size = as_float(level.get("size") if isinstance(level, dict) else level[1])
        if price is not None and size is not None and 0 < price < 1 and size > 0:
            rows.append((price, size))
    return sorted(rows, key=lambda row: row[0], reverse=(side == "bids"))


def simulate_buy(book: dict[str, Any], notional: float, slippage_bps: float) -> dict[str, Any]:
    asks = levels(book, "asks")
    bids = levels(book, "bids")
    if not asks or not bids or notional <= 0:
        return {"status": "missing_book", "fillable": False}
    remaining = notional
    shares = 0.0
    spent = 0.0
    for price, size in asks:
        capacity = price * size
        take_usd = min(remaining, capacity)
        shares += take_usd / price
        spent += take_usd
        remaining -= take_usd
        if remaining <= 1e-9:
            break
    if remaining > 0.01 or shares <= 0:
        return {"status": "insufficient_depth", "fillable": False, "unfilled_usd": round(remaining, 6)}
    best_ask, best_bid = asks[0][0], bids[0][0]
    raw_vwap = spent / shares
    fill = min(0.999999, raw_vwap * (1 + slippage_bps / 10000))
    return {
        "status": "ok",
        "fillable": True,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": best_ask - best_bid,
        "raw_vwap": raw_vwap,
        "fill_price": fill,
        "shares": shares,
        "price_impact": raw_vwap - best_ask,
    }


def simulate_sell(book: dict[str, Any], shares_to_sell: float, slippage_bps: float) -> dict[str, Any]:
    bids = levels(book, "bids")
    asks = levels(book, "asks")
    if not bids or not asks or shares_to_sell <= 0:
        return {"status": "missing_book", "fillable": False}
    remaining = shares_to_sell
    proceeds = 0.0
    sold = 0.0
    for price, size in bids:
        take = min(remaining, size)
        sold += take
        proceeds += take * price
        remaining -= take
        if remaining <= 1e-9:
            break
    if remaining > 1e-8:
        return {"status": "insufficient_depth", "fillable": False, "unfilled_shares": round(remaining, 8)}
    best_bid, best_ask = bids[0][0], asks[0][0]
    raw_vwap = proceeds / sold
    fill = max(0.000001, raw_vwap * (1 - slippage_bps / 10000))
    return {
        "status": "ok", "fillable": True, "best_bid": best_bid, "best_ask": best_ask,
        "spread": best_ask - best_bid, "raw_vwap": raw_vwap, "fill_price": fill,
        "shares": sold, "price_impact": best_bid - raw_vwap,
    }


def source_gate(estimate: dict[str, Any], gate: dict[str, Any]) -> tuple[bool, dict[str, int]]:
    sources = estimate.get("sources") or []
    official = sum(1 for source in sources if source.get("kind") == "official" and source.get("url"))
    independent = len({source.get("url") for source in sources if source.get("kind") == "independent" and source.get("url")})
    passed = official >= int(bool(gate["require_official_source"])) and independent >= gate["min_independent_sources"]
    return passed, {"official": official, "independent": independent}


def evaluate_candidate(
    market: dict[str, Any], estimate: dict[str, Any] | None, book: dict[str, Any] | None,
    policy: dict[str, Any], planned_notional: float, side: str = "YES",
) -> dict[str, Any]:
    side = side.upper()
    if side not in {"YES", "NO"}:
        raise ValueError("candidate side must be YES or NO")
    gate = policy["entry_gate"]
    friction = policy["friction"]
    failures: list[str] = []
    if not market["active"] or market["closed"] or not market["accepting_orders"]:
        failures.append("market_not_tradeable")
    token_id = market["yes_token_id"] if side == "YES" else market.get("no_token_id")
    market_price = market.get("market_yes_price") if side == "YES" else market.get("market_no_price")
    if not market["market_id"] or not token_id:
        failures.append("market_or_token_id_missing")
    if market.get("fees_enabled") and market.get("fee_rate") is None and friction.get("require_fee_schedule_when_enabled", True):
        failures.append("enabled_fee_schedule_missing")
    expiry = hours_to_expiry(market["end_date"])
    if expiry is None:
        failures.append("expiry_missing_or_invalid")
    elif expiry < gate["min_hours_to_expiry"] or expiry > gate["max_days_to_expiry"] * 24:
        failures.append("expiry_outside_preferred_window")
    if not estimate:
        failures.append("probability_estimate_missing")
        estimate = {}
    yes_probability = as_float(estimate.get("probability"))
    yes_ci_low = as_float(estimate.get("confidence_low"))
    yes_ci_high = as_float(estimate.get("confidence_high"))
    probability = yes_probability if side == "YES" or yes_probability is None else 1 - yes_probability
    ci_low = yes_ci_low if side == "YES" or yes_ci_high is None else 1 - yes_ci_high
    ci_high = yes_ci_high if side == "YES" or yes_ci_low is None else 1 - yes_ci_low
    raw_probability = probability
    raw_ci_low, raw_ci_high = ci_low, ci_high
    if not estimate.get("model_version"):
        failures.append("model_version_missing")
    controls = policy.get("paper_overlay_controls") or {}
    if estimate.get("model_version") in controls.get("blocked_model_versions", []):
        failures.append("model_version_blocked_by_paper_overlay")
    applied_caps = []
    for cap in controls.get("probability_caps", []):
        domain_match = not cap.get("domain") or cap.get("domain") == market.get("domain")
        model_match = not cap.get("model_version") or cap.get("model_version") == estimate.get("model_version")
        if probability is not None and domain_match and model_match:
            probability = min(probability, float(cap["value"]))
            if ci_low is not None:
                ci_low = min(ci_low, probability)
            if ci_high is not None:
                ci_high = min(ci_high, probability)
            applied_caps.append(cap.get("change_id"))
    if probability is None or not 0 <= probability <= 1:
        failures.append("probability_invalid")
    elif probability < gate["min_model_probability"]:
        failures.append("model_probability_below_gate")
    if ci_low is None or ci_high is None or probability is None or not (0 <= ci_low <= probability <= ci_high <= 1):
        failures.append("confidence_interval_invalid")
    elif ci_low < gate["min_confidence_lower"]:
        failures.append("confidence_lower_below_gate")
    calibration_samples = int(estimate.get("calibration_samples") or 0)
    if calibration_samples < gate["min_calibration_samples"] and not gate["allow_uncalibrated_research"]:
        failures.append("domain_model_calibration_insufficient")
    sources_ok, source_counts = source_gate(estimate, gate)
    if not sources_ok:
        failures.append("source_confirmation_insufficient")
    rules = estimate.get("rules_review") or {}
    if gate["require_clear_rules"] and (rules.get("status") != "clear" or not rules.get("reviewed_at")):
        failures.append("resolution_rules_not_cleared")
    failure_paths = estimate.get("failure_paths") or []
    if not failure_paths:
        failures.append("failure_paths_missing")
    elif any(path.get("controlled") is not True for path in failure_paths):
        failures.append("uncontrolled_failure_path")
    execution = simulate_buy(book or {}, planned_notional, friction["extra_slippage_bps"])
    if not execution.get("fillable"):
        failures.append(execution.get("status", "execution_unavailable"))
    else:
        if execution["spread"] > gate["max_spread_per_share"]:
            failures.append("spread_above_gate")
        if execution["price_impact"] > gate["max_price_impact_per_share"]:
            failures.append("price_impact_above_gate")
    conservative_p = None
    net_ev = None
    if probability is not None and execution.get("fillable"):
        conservative_p = max(0.0, probability - gate["uncertainty_discount"])
        fee_rate = float(market.get("fee_rate") or 0.0)
        entry_fee_per_share = fee_rate * execution["fill_price"] * (1 - execution["fill_price"])
        net_ev = conservative_p - execution["fill_price"] - entry_fee_per_share - gate["resolution_risk_reserve"]
        if net_ev < gate["min_net_edge_per_share"]:
            failures.append("net_ev_below_gate")
    else:
        entry_fee_per_share = None
    return {
        **market,
        "side": side,
        "token_id": str(token_id or ""),
        "market_side_price": market_price,
        "model_version": estimate.get("model_version"),
        "model_yes_probability": yes_probability,
        "yes_confidence_low": yes_ci_low,
        "yes_confidence_high": yes_ci_high,
        "raw_model_probability": raw_probability,
        "raw_confidence_low": raw_ci_low,
        "raw_confidence_high": raw_ci_high,
        "model_probability": probability,
        "confidence_low": ci_low,
        "confidence_high": ci_high,
        "calibration_samples": calibration_samples,
        "historical_hit_rate": as_float(estimate.get("historical_hit_rate")),
        "model_brier_score": as_float(estimate.get("model_brier_score")),
        "market_brier_score": as_float(estimate.get("market_brier_score")),
        "model_log_loss": as_float(estimate.get("model_log_loss")),
        "market_log_loss": as_float(estimate.get("market_log_loss")),
        "source_counts": source_counts,
        "rules_review": rules,
        "failure_paths": failure_paths,
        "planned_notional_usd": round(planned_notional, 2),
        "execution": execution,
        "conservative_probability": conservative_p,
        "net_ev_per_share": net_ev,
        "fees_enabled": bool(market.get("fees_enabled")),
        "fee_rate": market.get("fee_rate"),
        "entry_fee_per_share": entry_fee_per_share,
        "correlation_group": estimate.get("correlation_group") or market.get("event_id"),
        "strategy_version": policy.get("effective_strategy_version", policy["version"]),
        "overlay_change_ids": list(policy.get("overlay_change_ids", [])),
        "probability_cap_change_ids": [item for item in applied_caps if item],
        "gate_passed": not failures,
        "failed_gates": failures,
        "decision": "PAPER_CANDIDATE" if not failures else "PASS",
    }


def evaluate_high_win_small_return(candidate: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    gate = policy.get("high_win_small_return_gate") or {}
    failures = set(candidate.get("failed_gates") or [])
    allowed_alpha_failures = {"net_ev_below_gate"}
    blockers = sorted(failures - allowed_alpha_failures)
    probability = candidate.get("model_probability")
    confidence_low = candidate.get("confidence_low")
    samples = int(candidate.get("calibration_samples") or 0)
    hit_rate = candidate.get("historical_hit_rate")
    model_brier, market_brier = candidate.get("model_brier_score"), candidate.get("market_brier_score")
    model_log, market_log = candidate.get("model_log_loss"), candidate.get("market_log_loss")
    if gate.get("enabled") is not True: blockers.append("high_win_gate_disabled")
    if not candidate.get("model_version"): blockers.append("independent_model_missing")
    if probability is None or float(probability) < float(gate.get("min_model_probability", .90)):
        blockers.append("high_win_probability_below_gate")
    if confidence_low is None or float(confidence_low) < float(gate.get("min_confidence_lower", .85)):
        blockers.append("high_win_confidence_lower_below_gate")
    if samples < int(gate.get("min_independent_oos_samples", 30)):
        blockers.append("high_win_oos_samples_below_gate")
    if gate.get("require_calibration_bin_alignment", True):
        if hit_rate is None or probability is None or abs(float(hit_rate) - float(probability)) > .10:
            blockers.append("high_win_calibration_bin_misaligned_or_missing")
    if gate.get("require_not_materially_worse_than_market", True):
        metric_pass = ((model_brier is not None and market_brier is not None and float(model_brier) <= float(market_brier))
                       or (model_log is not None and market_log is not None and float(model_log) <= float(market_log)))
        if not metric_pass: blockers.append("high_win_model_not_proven_noninferior_to_market")
    net_ev = candidate.get("net_ev_per_share")
    if net_ev is None or float(net_ev) <= float(gate.get("min_net_ev_per_share_exclusive", 0)):
        blockers.append("high_win_net_ev_not_positive")
    blockers = sorted(set(blockers))
    return {"eligible": not blockers, "failed_gates": blockers,
            "tail_risk_label": gate.get("tail_risk_label", "低收益、高尾部损失风险")}


def scan_payload(markets_raw: Any, books: dict[str, Any], estimates: dict[str, Any], policy: dict[str, Any],
                 available_equity_usd: float | None = None) -> dict[str, Any]:
    markets_list = markets_raw.get("markets", []) if isinstance(markets_raw, dict) else markets_raw
    equity = float(available_equity_usd if available_equity_usd is not None else policy["initial_equity_usd"])
    risk_pool = equity * policy["default_risk_pool_pct"] / 100
    high_win_max_pct = float(((policy.get("high_win_small_return_gate") or {}).get("position_equity_pct") or [0.5, 2.0])[-1])
    provisional = min(risk_pool * policy["primary_allocation_pct"] / 100, equity * high_win_max_pct / 100)
    def estimate_for(raw_or_normalized: dict[str, Any]) -> dict[str, Any] | None:
        keys = (
            raw_or_normalized.get("market_id"), raw_or_normalized.get("id"),
            raw_or_normalized.get("condition_id"), raw_or_normalized.get("conditionId"),
        )
        return next((estimates[str(key)] for key in keys if key is not None and str(key) in estimates), None)

    candidates = []
    for raw in markets_list:
        normalized = normalize_market(raw)
        estimate = estimate_for(raw)
        for side, token_key in (("YES", "yes_token_id"), ("NO", "no_token_id")):
            token = str(normalized.get(token_key) or "")
            candidate = evaluate_candidate(normalized, estimate, books.get(token), policy, provisional, side=side)
            high_win = evaluate_high_win_small_return(candidate, policy)
            candidate["high_win_small_return_eligible"] = high_win["eligible"]
            candidate["high_win_small_return_failed_gates"] = high_win["failed_gates"]
            candidate["tail_risk_label"] = high_win["tail_risk_label"] if high_win["eligible"] else None
            candidate["recommendation_type"] = "alpha_primary" if candidate["gate_passed"] else ("high_win_small_return" if high_win["eligible"] else None)
            candidate["paper_entry_eligible"] = bool(candidate["gate_passed"] or high_win["eligible"])
            candidates.append(candidate)
    alpha_ranked = sorted((item for item in candidates if item["gate_passed"]), key=lambda item: item["net_ev_per_share"], reverse=True)
    high_win_ranked = sorted((item for item in candidates if not item["gate_passed"] and item["high_win_small_return_eligible"]),
                             key=lambda item: (item["model_probability"], item["net_ev_per_share"]), reverse=True)
    ranked = alpha_ranked + high_win_ranked[:int((policy.get("high_win_small_return_gate") or {}).get("max_daily_recommendations", 1))]
    passed = []
    selection_rejections = []
    for item in ranked:
        if not passed:
            passed.append(item)
            continue
        existing_groups = {row.get("correlation_group") for row in passed}
        group = item.get("correlation_group")
        if group is None or None in existing_groups:
            selection_rejections.append({"market_id": item["market_id"], "reason": "second_position_correlation_group_missing"})
            continue
        if group in existing_groups:
            selection_rejections.append({"market_id": item["market_id"], "reason": "same_event_correlation_block"})
            continue
        passed.append(item)
        if len(passed) == 2:
            break
    selected = []
    for index, item in enumerate(passed):
        alpha_count = sum(row.get("recommendation_type") == "alpha_primary" for row in passed)
        if item.get("recommendation_type") == "high_win_small_return":
            notional = equity * high_win_max_pct / 100
        elif alpha_count == 2:
            alpha_index = [row for row in passed[:index + 1] if row.get("recommendation_type") == "alpha_primary"]
            notional = risk_pool * policy["two_candidate_allocation_pct"][len(alpha_index) - 1] / 100
        else:
            notional = risk_pool * policy["primary_allocation_pct"] / 100
        refreshed = evaluate_candidate(item, estimate_for(item), books.get(item["token_id"]), policy, notional, side=item["side"])
        high_win = evaluate_high_win_small_return(refreshed, policy)
        refreshed["high_win_small_return_eligible"] = high_win["eligible"]
        refreshed["high_win_small_return_failed_gates"] = high_win["failed_gates"]
        refreshed["tail_risk_label"] = high_win["tail_risk_label"] if high_win["eligible"] else None
        refreshed["recommendation_type"] = "alpha_primary" if refreshed["gate_passed"] else ("high_win_small_return" if high_win["eligible"] else None)
        refreshed["paper_entry_eligible"] = bool(refreshed["gate_passed"] or high_win["eligible"])
        if refreshed["paper_entry_eligible"]:
            selected.append(refreshed)
    return {
        "schema_version": "polymarket-scan-v2",
        "created_at": now_iso(),
        "policy_version": policy["version"],
        "domain_router_version": DOMAIN_ROUTER_VERSION,
        "strategy_version": policy.get("effective_strategy_version", policy["version"]),
        "overlay_change_ids": list(policy.get("overlay_change_ids", [])),
        "paper_only": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "markets_scanned": len({row["market_id"] for row in candidates}),
        "side_decisions_evaluated": len(candidates),
        "domain_counts": {domain: len({row["market_id"] for row in candidates if row["domain"] == domain}) for domain in policy["domains"]},
        "risk_pool_usd": risk_pool,
        "risk_pool_equity_basis_usd": equity,
        "selected_candidates": selected,
        "selection_rejections": selection_rejections,
        "all_decisions": candidates,
    }


def new_ledger(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "polymarket-paper-ledger-v1",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "initial_equity_usd": policy["initial_equity_usd"],
        "cash_usd": policy["initial_equity_usd"],
        "equity_usd": policy["initial_equity_usd"],
        "high_watermark_usd": policy["initial_equity_usd"],
        "max_drawdown_pct": 0.0,
        "open_positions": [],
        "closed_positions": [],
        "paper_orders": [],
        "back_cases": [],
        "position_observations": [],
        "events": [],
        "clv_min_horizon_minutes": float((policy.get("evaluation") or {}).get("clv_min_horizon_minutes", 60)),
        "observation_max_gap_hours": float((policy.get("evaluation") or {}).get("max_observation_gap_hours", 6)),
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def load_ledger(path: str | Path, policy: dict[str, Any]) -> dict[str, Any]:
    target = Path(path)
    ledger = read_json(target) if target.exists() else new_ledger(policy)
    ledger.setdefault("events", [])
    ledger.setdefault("position_observations", [])
    ledger.setdefault("clv_min_horizon_minutes", float((policy.get("evaluation") or {}).get("clv_min_horizon_minutes", 60)))
    ledger.setdefault("observation_max_gap_hours", float((policy.get("evaluation") or {}).get("max_observation_gap_hours", 6)))
    return ledger


def assert_ledger_safe(ledger: dict[str, Any]) -> None:
    if ledger.get("live_orders_enabled") is not False or ledger.get("private_api_used") is not False:
        raise ValueError("unsafe ledger flags; paper mutation blocked")


def enter_scan(scan: dict[str, Any], ledger: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    assert_ledger_safe(ledger)
    slots = policy["max_open_positions"] - len(ledger["open_positions"])
    if slots <= 0:
        return {"opened": [], "blocked": ["max_open_positions_reached"]}
    already = {position["market_id"] for position in ledger["open_positions"]}
    opened = []
    blocked = []
    max_pool = ledger["equity_usd"] * policy["max_risk_pool_pct"] / 100
    current_cost = sum(position.get("risk_committed_usd", position["cost_basis_usd"] + position.get("entry_fee_usd", 0)) for position in ledger["open_positions"])
    remaining_pool = max(0.0, max_pool - current_cost)
    for candidate in scan.get("selected_candidates", [])[:slots]:
        if candidate["market_id"] in already:
            blocked.append(f"duplicate_market:{candidate['market_id']}")
            continue
        if not candidate.get("paper_entry_eligible", candidate.get("gate_passed")):
            blocked.append(f"gate_not_passed:{candidate['market_id']}")
            continue
        requested_budget = min(candidate["planned_notional_usd"], remaining_pool, ledger["cash_usd"])
        fill = candidate["execution"]["fill_price"]
        fee_rate = float(candidate.get("fee_rate") or 0.0)
        fee_ratio_to_trade_value = fee_rate * (1 - fill)
        requested = requested_budget / (1 + fee_ratio_to_trade_value)
        if requested <= 0:
            blocked.append("risk_pool_or_cash_exhausted")
            break
        shares = requested / fill
        fee = taker_fee_usd(shares, fill, fee_rate, policy["friction"].get("fee_round_decimals", 5))
        total = requested + fee
        if total > ledger["cash_usd"]:
            blocked.append("cash_insufficient_after_fee")
            continue
        trade_id = stable_id("pm-paper", candidate["market_id"], scan["created_at"], len(ledger["paper_orders"]))
        order_id = stable_id("pm-order", trade_id, "BUY")
        position = {
            "paper_trade_id": trade_id,
            "market_id": candidate["market_id"],
            "condition_id": candidate.get("condition_id"),
            "gamma_market_id": candidate.get("gamma_market_id"),
            "yes_token_id": candidate["yes_token_id"],
            "no_token_id": candidate.get("no_token_id"),
            "token_id": candidate["token_id"],
            "question": candidate["question"],
            "domain": candidate["domain"],
            "side": candidate["side"],
            "opened_at": now_iso(),
            "end_date": candidate["end_date"],
            "entry_price": fill,
            "shares": shares,
            "cost_basis_usd": requested,
            "entry_fee_usd": fee,
            "risk_committed_usd": total,
            "fees_enabled": bool(candidate.get("fees_enabled")),
            "fee_rate": fee_rate,
            "fee_schedule_source": candidate.get("fee_schedule_source"),
            "model_probability_at_entry": candidate["model_probability"],
            "recommendation_type": candidate.get("recommendation_type"),
            "tail_risk_label": candidate.get("tail_risk_label"),
            "model_yes_probability_at_entry": candidate.get("model_yes_probability"),
            "confidence_low_at_entry": candidate["confidence_low"],
            "model_version": candidate["model_version"],
            "strategy_version": candidate.get("strategy_version", policy.get("effective_strategy_version", policy["version"])),
            "overlay_change_ids": list(candidate.get("overlay_change_ids", [])),
            "net_ev_per_share_at_entry": candidate["net_ev_per_share"],
            "spread_at_entry": candidate["execution"]["spread"],
            "price_impact_at_entry": candidate["execution"]["price_impact"],
            "slippage_at_entry": candidate["execution"]["fill_price"] - candidate["execution"]["best_ask"],
            "failed_gates": [],
            "status": "open",
        }
        ledger["open_positions"].append(position)
        ledger["paper_orders"].append({
            "paper_order_id": order_id, "paper_trade_id": trade_id, "created_at": now_iso(),
            "side": "BUY", "outcome_side": position["side"], "token_id": position["token_id"],
            "type": "MARKET", "status": "FILLED", "average_fill_price": fill,
            "executed_shares": shares, "notional_usd": requested, "commission_usd": fee,
            "spread_per_share": candidate["execution"]["spread"], "slippage_and_impact_per_share": candidate["execution"]["fill_price"] - candidate["execution"]["best_ask"],
            "strategy_version": position["strategy_version"], "overlay_change_ids": position["overlay_change_ids"],
            "live_orders_enabled": False, "private_api_used": False,
        })
        ledger["cash_usd"] -= total
        remaining_pool -= total
        opened.append(trade_id)
    ledger["updated_at"] = now_iso()
    ledger["events"].append({"at": now_iso(), "type": "paper_entry_batch", "opened": opened, "blocked": blocked})
    return {"opened": opened, "blocked": blocked}


def settle(ledger: dict[str, Any], resolutions: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    assert_ledger_safe(ledger)
    settled, pending, counterfactual_resolved = [], [], []
    for position in list(ledger["open_positions"]):
        resolution = resolutions.get(position["market_id"])
        if resolution not in ("YES", "NO", True, False, 1, 0):
            pending.append(position["paper_trade_id"])
            continue
        resolved_side = "YES" if resolution in ("YES", True, 1) else "NO"
        won = resolved_side == str(position.get("side", "YES")).upper()
        payout = position["shares"] if won else 0.0
        net_pnl = payout - position["cost_basis_usd"] - position["entry_fee_usd"]
        closed = {**position, "status": "closed", "closed_at": now_iso(), "resolution": resolved_side, "side_won": won, "exit_price": 1.0 if won else 0.0, "payout_usd": payout, "net_pnl_usd": net_pnl, "return_on_cost_pct": net_pnl / position["cost_basis_usd"] * 100, "outcome": "win" if net_pnl > 0 else "loss"}
        ledger["open_positions"].remove(position)
        ledger["closed_positions"].append(closed)
        ledger["cash_usd"] += payout
        ledger["paper_orders"].append({"paper_order_id": stable_id("pm-order", position["paper_trade_id"], "SETTLE"), "paper_trade_id": position["paper_trade_id"], "created_at": now_iso(), "side": "SETTLE", "outcome_side": position.get("side", "YES"), "token_id": position.get("token_id") or position.get("yes_token_id"), "type": "RESOLUTION", "status": "FILLED", "average_fill_price": closed["exit_price"], "executed_shares": position["shares"], "notional_usd": payout, "commission_usd": 0.0, "strategy_version": position.get("strategy_version"), "overlay_change_ids": list(position.get("overlay_change_ids", [])), "live_orders_enabled": False, "private_api_used": False})
        if not won:
            ledger["back_cases"].append({
                "back_case_id": stable_id("pm-backcase", position["paper_trade_id"]),
                "paper_trade_id": position["paper_trade_id"], "created_at": now_iso(),
                "forecast_probability": position["model_probability_at_entry"], "entry_price": position["entry_price"],
                "model_version": position["model_version"], "actual_outcome": 0, "outcome_side": position.get("side", "YES"),
                "domain": position.get("domain"), "strategy_version": position.get("strategy_version"),
                "overlay_change_ids": list(position.get("overlay_change_ids", [])),
                "required_review": sorted(BACK_CASE_REVIEW_FIELDS),
                "review_answers": {}, "avoidable_rule_misread": None,
                "parameter_change_allowed": False,
                "parameter_change_rule": "three_similar_failures_or_explicit_data_or_rule_error",
                "status": "review_required",
            })
        settled.append(position["paper_trade_id"])
    for closed in ledger["closed_positions"]:
        if closed.get("exit_reason") and closed.get("counterfactual_status") == "pending":
            resolution = resolutions.get(closed["market_id"])
            if resolution in ("YES", "NO", True, False, 1, 0):
                resolved_side = "YES" if resolution in ("YES", True, 1) else "NO"
                side_won = resolved_side == str(closed.get("side", "YES")).upper()
                hold_payout = closed["shares"] if side_won else 0.0
                closed["counterfactual_status"] = "resolved"
                closed["counterfactual_resolution"] = resolved_side
                closed["counterfactual_side_won"] = side_won
                closed["counterfactual_hold_payout_usd"] = hold_payout
                closed["exit_vs_hold_value_usd"] = closed.get("exit_proceeds_usd", 0) - hold_payout
                closed["counterfactual_resolved_at"] = now_iso()
                for back_case in ledger.get("back_cases", []):
                    if back_case.get("paper_trade_id") == closed["paper_trade_id"]:
                        back_case["counterfactual_resolution"] = closed["counterfactual_resolution"]
                        back_case["exit_vs_hold_value_usd"] = closed["exit_vs_hold_value_usd"]
                        back_case["counterfactual_status"] = "resolved"
                        back_case["counterfactual_resolved_at"] = closed["counterfactual_resolved_at"]
                counterfactual_resolved.append(closed["paper_trade_id"])
    ledger["equity_usd"] = ledger["cash_usd"] + sum(position["cost_basis_usd"] for position in ledger["open_positions"])
    ledger["high_watermark_usd"] = max(ledger["high_watermark_usd"], ledger["equity_usd"])
    drawdown = (ledger["equity_usd"] / ledger["high_watermark_usd"] - 1) * 100 if ledger["high_watermark_usd"] else 0
    ledger["max_drawdown_pct"] = min(ledger.get("max_drawdown_pct", 0), drawdown)
    ledger["updated_at"] = now_iso()
    return {"settled": settled, "pending": pending, "counterfactual_resolved": counterfactual_resolved}


def monitor_positions(
    ledger: dict[str, Any], books: dict[str, Any], estimates: dict[str, Any],
    events: dict[str, Any], policy: dict[str, Any], observed_at: str | None = None,
) -> dict[str, Any]:
    assert_ledger_safe(ledger)
    ledger.setdefault("position_observations", [])
    observation_time = observed_at or now_iso()
    observation_dt = parse_iso(observation_time) or datetime.now(timezone.utc)
    exited, held, degraded = [], [], []
    friction = policy["friction"]
    for position in list(ledger["open_positions"]):
        market_id = position["market_id"]
        estimate = estimates.get(market_id) or {}
        event = events.get(market_id) or {}
        position_side = str(position.get("side", "YES")).upper()
        position_token = str(position.get("token_id") or position.get("yes_token_id") or "")
        book = books.get(position_token) or {}
        quote = simulate_sell(book, float(position["shares"]), friction["extra_slippage_bps"])
        yes_probability = as_float(estimate.get("probability"))
        probability = yes_probability if position_side == "YES" or yes_probability is None else 1 - yes_probability
        opened_at = parse_iso(position.get("opened_at"))
        age_minutes = (observation_dt - opened_at).total_seconds() / 60 if opened_at else None
        observation_id = stable_id("pm-position-observation", position["paper_trade_id"], observation_time)
        source_refs = []
        for source in estimate.get("sources", []) if isinstance(estimate.get("sources"), list) else []:
            if isinstance(source, dict):
                ref = source.get("artifact_id") or source.get("url") or source.get("id")
                if ref:
                    source_refs.append(str(ref))
        event_evidence_ids = event.get("evidence_ids") if isinstance(event.get("evidence_ids"), list) else []
        observation = {
            "observation_id": observation_id,
            "paper_trade_id": position["paper_trade_id"],
            "market_id": market_id,
            "observed_at": observation_time,
            "minutes_since_entry": age_minutes,
            "model_probability": probability,
            "model_yes_probability": yes_probability,
            "outcome_side": position_side,
            "token_id": position_token,
            "model_version": estimate.get("model_version") or position.get("model_version"),
            "fair_value_ceiling": as_float(estimate.get("fair_value_ceiling")),
            "estimate_source_refs": sorted(set(source_refs)),
            "event_evidence_ids": sorted(str(value) for value in event_evidence_ids),
            "event_state": {
                "thesis_invalidated": event.get("thesis_invalidated"),
                "rules_disputed": event.get("rules_disputed"),
                "data_status": event.get("data_status"),
            },
            "book_status": quote.get("status"),
            "best_bid": quote.get("best_bid"),
            "best_ask": quote.get("best_ask"),
            "spread": quote.get("spread"),
            "simulated_exit_fill_price": quote.get("fill_price"),
            "paper_only": True,
            "live_orders_enabled": False,
            "private_api_used": False,
        }
        if quote.get("fillable"):
            position["last_market_price"] = quote["best_bid"]
            position["last_marked_at"] = observation_time
            position["latest_clv_per_share"] = quote["best_bid"] - position["entry_price"]
            min_clv_minutes = float((policy.get("evaluation") or {}).get("clv_min_horizon_minutes", 60))
            if position.get("clv_reference_price") is None and age_minutes is not None and age_minutes >= min_clv_minutes:
                position["clv_reference_price"] = quote["best_bid"]
                position["clv_observed_at"] = observation_time
                position["clv_horizon_minutes"] = age_minutes
                position["clv_per_share"] = quote["best_bid"] - position["entry_price"]
                position["clv_observation_id"] = observation_id
                observation["is_clv_reference"] = True
        reasons = []
        if event.get("thesis_invalidated") is True:
            reasons.append("core_thesis_invalidated")
        if event.get("rules_disputed") is True:
            reasons.append("resolution_rules_disputed")
        if event.get("data_status") in {"missing", "stale", "disputed"}:
            reasons.append(f"data_status_{event['data_status']}")
        if probability is not None and quote.get("fillable") and probability < quote["best_bid"]:
            reasons.append("updated_probability_below_market_price")
        fair_ceiling = as_float(estimate.get("fair_value_ceiling")) if position_side == "YES" else None
        if position_side == "NO":
            yes_floor = as_float(estimate.get("fair_value_floor"))
            fair_ceiling = 1 - yes_floor if yes_floor is not None else None
        if fair_ceiling is not None and quote.get("fillable") and quote["best_bid"] >= fair_ceiling:
            reasons.append("market_price_at_or_above_fair_ceiling")
        if not reasons:
            observation["decision"] = "hold"
            observation["decision_reasons"] = []
            ledger["position_observations"].append(observation)
            held.append({"paper_trade_id": position["paper_trade_id"], "market_id": market_id, "updated_probability": probability, "quote": quote})
            continue
        if not quote.get("fillable"):
            observation["decision"] = "exit_degraded_no_fill"
            observation["decision_reasons"] = reasons + [quote.get("status", "exit_quote_missing")]
            ledger["position_observations"].append(observation)
            degraded.append({"paper_trade_id": position["paper_trade_id"], "reasons": reasons + [quote.get("status", "exit_quote_missing")]})
            continue
        gross = quote["fill_price"] * position["shares"]
        exit_fee = taker_fee_usd(
            float(position["shares"]), quote["fill_price"], float(position.get("fee_rate") or 0.0),
            friction.get("fee_round_decimals", 5),
        )
        proceeds = gross - exit_fee
        net_pnl = proceeds - position["cost_basis_usd"] - position["entry_fee_usd"]
        closed = {
            **position, "status": "closed", "closed_at": observation_time, "exit_price": quote["fill_price"],
            "exit_reason": reasons, "updated_probability_before_exit": probability,
            "updated_yes_probability_before_exit": yes_probability,
            "exit_proceeds_usd": proceeds, "exit_fee_usd": exit_fee, "net_pnl_usd": net_pnl,
            "return_on_cost_pct": net_pnl / position["cost_basis_usd"] * 100,
            "outcome": "early_exit", "counterfactual_status": "pending",
            "exit_observation_id": observation_id,
        }
        ledger["open_positions"].remove(position)
        ledger["closed_positions"].append(closed)
        ledger["cash_usd"] += proceeds
        ledger["paper_orders"].append({
            "paper_order_id": stable_id("pm-order", position["paper_trade_id"], "SELL", observation_time),
            "paper_trade_id": position["paper_trade_id"], "created_at": observation_time, "side": "SELL",
            "outcome_side": position_side, "token_id": position_token,
            "type": "MARKET", "status": "FILLED", "average_fill_price": quote["fill_price"],
            "executed_shares": position["shares"], "notional_usd": gross, "commission_usd": exit_fee,
            "spread_per_share": quote["spread"], "slippage_and_impact_per_share": quote["best_bid"] - quote["fill_price"],
            "order_reason": reasons, "live_orders_enabled": False, "private_api_used": False,
            "strategy_version": position.get("strategy_version"),
            "overlay_change_ids": list(position.get("overlay_change_ids", [])),
        })
        ledger["back_cases"].append({
            "back_case_id": stable_id("pm-backcase", position["paper_trade_id"], "early-exit"),
            "paper_trade_id": position["paper_trade_id"], "created_at": now_iso(), "type": "early_exit_review",
            "forecast_probability": position["model_probability_at_entry"], "entry_price": position["entry_price"],
            "exit_probability": probability, "exit_price": quote["fill_price"], "exit_fee_usd": exit_fee,
            "exit_yes_probability": yes_probability, "outcome_side": position_side,
            "exit_reasons": reasons, "counterfactual_status": "pending",
            "exit_observation_id": observation_id,
            "model_version": position.get("model_version"), "domain": position.get("domain"),
            "strategy_version": position.get("strategy_version"),
            "overlay_change_ids": list(position.get("overlay_change_ids", [])),
            "required_review": sorted(BACK_CASE_REVIEW_FIELDS),
            "review_answers": {}, "avoidable_rule_misread": None,
            "parameter_change_allowed": False,
            "parameter_change_rule": "three_similar_failures_or_explicit_data_or_rule_error",
            "status": "review_required",
        })
        observation["decision"] = "exit"
        observation["decision_reasons"] = reasons
        observation["exit_fee_usd"] = exit_fee
        observation["exit_proceeds_usd"] = proceeds
        ledger["position_observations"].append(observation)
        exited.append(position["paper_trade_id"])
    ledger["equity_usd"] = ledger["cash_usd"] + sum(position["cost_basis_usd"] for position in ledger["open_positions"])
    ledger["high_watermark_usd"] = max(ledger["high_watermark_usd"], ledger["equity_usd"])
    drawdown = (ledger["equity_usd"] / ledger["high_watermark_usd"] - 1) * 100 if ledger["high_watermark_usd"] else 0
    ledger["max_drawdown_pct"] = min(ledger.get("max_drawdown_pct", 0), drawdown)
    ledger["updated_at"] = now_iso()
    ledger["events"].append({"at": now_iso(), "type": "position_monitor", "exited": exited, "held_count": len(held), "degraded": degraded})
    return {"exited": exited, "held": held, "degraded": degraded}


def monitor_pending_counterfactuals(
    ledger: dict[str, Any], books: dict[str, Any], policy: dict[str, Any], observed_at: str | None = None,
) -> dict[str, Any]:
    """Keep early exits observable until resolution, including their first eligible CLV snapshot."""
    assert_ledger_safe(ledger)
    ledger.setdefault("position_observations", [])
    observation_time = observed_at or now_iso()
    observation_dt = parse_iso(observation_time) or datetime.now(timezone.utc)
    tracked, degraded = [], []
    friction = policy["friction"]
    min_clv_minutes = float((policy.get("evaluation") or {}).get("clv_min_horizon_minutes", 60))
    for position in ledger.get("closed_positions", []):
        if not position.get("exit_reason") or position.get("counterfactual_status") != "pending":
            continue
        opened_at = parse_iso(position.get("opened_at"))
        age_minutes = (observation_dt - opened_at).total_seconds() / 60 if opened_at else None
        quote = simulate_sell(
            books.get(str(position.get("token_id") or position.get("yes_token_id") or "")) or {},
            float(position.get("shares") or 0), friction["extra_slippage_bps"],
        )
        observation_id = stable_id("pm-position-observation", position["paper_trade_id"], observation_time)
        observation = {
            "observation_id": observation_id,
            "paper_trade_id": position["paper_trade_id"],
            "market_id": position["market_id"],
            "observed_at": observation_time,
            "minutes_since_entry": age_minutes,
            "model_probability": None,
            "outcome_side": str(position.get("side", "YES")).upper(),
            "token_id": str(position.get("token_id") or position.get("yes_token_id") or ""),
            "model_version": position.get("model_version"),
            "estimate_source_refs": [],
            "event_evidence_ids": [],
            "event_state": {"counterfactual_status": "pending"},
            "book_status": quote.get("status"),
            "best_bid": quote.get("best_bid"),
            "best_ask": quote.get("best_ask"),
            "spread": quote.get("spread"),
            "simulated_exit_fill_price": quote.get("fill_price"),
            "decision": "track_early_exit_counterfactual",
            "decision_reasons": ["awaiting_final_resolution"],
            "paper_only": True,
            "live_orders_enabled": False,
            "private_api_used": False,
        }
        if quote.get("fillable"):
            position["counterfactual_last_market_price"] = quote["best_bid"]
            position["counterfactual_last_marked_at"] = observation_time
            if position.get("clv_reference_price") is None and age_minutes is not None and age_minutes >= min_clv_minutes:
                position["clv_reference_price"] = quote["best_bid"]
                position["clv_observed_at"] = observation_time
                position["clv_horizon_minutes"] = age_minutes
                position["clv_per_share"] = quote["best_bid"] - position["entry_price"]
                position["clv_observation_id"] = observation_id
                observation["is_clv_reference"] = True
            tracked.append(position["paper_trade_id"])
        else:
            degraded.append({"paper_trade_id": position["paper_trade_id"], "reason": quote.get("status", "book_missing")})
        ledger["position_observations"].append(observation)
    ledger["updated_at"] = observation_time
    return {"tracked": tracked, "degraded": degraded}


BACK_CASE_REVIEW_FIELDS = {
    "data_completeness_and_latency",
    "failed_assumption",
    "rules_news_liquidity_omissions",
    "market_lead_lag",
    "correct_counterfactual_action",
    "new_downgrade_rule_or_probability_cap",
    "historical_replay",
    "oos_before_after",
    "defect_classification",
}


def back_case_is_complete(back_case: dict[str, Any]) -> bool:
    if back_case.get("status") not in {"reviewed", "completed", "complete"}:
        return False
    answers = back_case.get("review_answers")
    if not isinstance(answers, dict) or not BACK_CASE_REVIEW_FIELDS.issubset(answers):
        return False
    if answers.get("correct_counterfactual_action") not in {"bet", "reduce", "wait", "pass"}:
        return False
    replay = answers.get("historical_replay")
    oos = answers.get("oos_before_after")
    if not isinstance(replay, dict) or replay.get("status") not in {"passed", "failed", "not_applicable_with_reason"}:
        return False
    if not isinstance(oos, dict) or oos.get("status") not in {"improved", "unchanged", "degraded", "insufficient_samples"}:
        return False
    if back_case.get("type") == "early_exit_review":
        if back_case.get("counterfactual_status") != "resolved":
            return False
        if back_case.get("counterfactual_resolution") not in {"YES", "NO"}:
            return False
        if back_case.get("exit_vs_hold_value_usd") is None or back_case.get("counterfactual_resolved_at") is None:
            return False
    return back_case.get("avoidable_rule_misread") in {True, False}


def resolved_outcome(row: dict[str, Any]) -> int | None:
    resolution = row.get("resolution") or row.get("counterfactual_resolution")
    if resolution in {"YES", "NO"}:
        return int(resolution == str(row.get("side", "YES")).upper())
    return None


def compare_30d_windows(ledger: dict[str, Any], binance: dict[str, Any] | None) -> dict[str, Any]:
    """Compare only explicit, complete, same-capital 30-day Binance windows."""
    windows = (binance or {}).get("windows")
    if not isinstance(windows, list):
        windows = []
    all_positions = list(ledger.get("closed_positions", [])) + list(ledger.get("open_positions", []))
    initial_equity = float(ledger.get("initial_equity_usd", 0))
    ledger_start = parse_iso(ledger.get("created_at"))
    ledger_end = parse_iso(ledger.get("updated_at"))
    max_gap_hours = float(ledger.get("observation_max_gap_hours", 6))
    valid_observations = sorted(
        parse_iso(event.get("at"))
        for event in ledger.get("events", [])
        if event.get("type") == "paper_observation_cycle"
        and event.get("market_discovery_complete") is True
        and parse_iso(event.get("at")) is not None
    )
    results = []
    for window in sorted(windows, key=lambda row: str(row.get("start_at", ""))):
        failures = []
        start, end = parse_iso(window.get("start_at")), parse_iso(window.get("end_at"))
        if not start or not end or abs((end - start).total_seconds() - 30 * 86400) > 60:
            failures.append("not_exact_30d_window")
        if window.get("data_status") != "complete":
            failures.append("binance_window_incomplete")
        if window.get("friction_complete") is not True:
            failures.append("binance_friction_incomplete")
        binance_capital = as_float(window.get("initial_equity_usd"))
        if binance_capital is None or abs(binance_capital - initial_equity) > 1e-6:
            failures.append("capital_mismatch")
        if start and end and (ledger_start is None or ledger_start > start or ledger_end is None or ledger_end < end):
            failures.append("polymarket_observation_window_incomplete")
        if start and end:
            in_window = [point for point in valid_observations if start <= point <= end]
            coverage_points = [start, *in_window, end]
            max_gap = max(
                ((right - left).total_seconds() / 3600 for left, right in zip(coverage_points, coverage_points[1:])),
                default=30 * 24,
            )
            if len(in_window) < 2 or max_gap > max_gap_hours:
                failures.append("polymarket_full_market_observation_gap")
        included = []
        boundary_positions = []
        if start and end:
            for row in all_positions:
                opened, closed_at = parse_iso(row.get("opened_at")), parse_iso(row.get("closed_at"))
                if not opened:
                    continue
                if start <= opened < end:
                    if closed_at is None or closed_at > end:
                        boundary_positions.append(row.get("paper_trade_id"))
                    else:
                        included.append(row)
                elif opened < start and (closed_at is None or closed_at >= start):
                    boundary_positions.append(row.get("paper_trade_id"))
        if boundary_positions:
            failures.append("unmarked_boundary_position")
        friction_complete = all(
            row.get("entry_fee_usd") is not None
            and row.get("spread_at_entry") is not None
            and row.get("price_impact_at_entry") is not None
            and row.get("slippage_at_entry") is not None
            for row in included
        )
        if included and not friction_complete:
            failures.append("polymarket_friction_or_sample_missing")
        pnl = sum(float(row.get("net_pnl_usd", 0)) for row in included)
        pm_roi = pnl / initial_equity * 100 if initial_equity else None
        bn_roi = as_float(window.get("net_roi_pct"))
        if bn_roi is None:
            failures.append("binance_roi_missing")
        valid = not failures
        results.append({
            "window_id": window.get("window_id"), "start_at": window.get("start_at"), "end_at": window.get("end_at"),
            "valid_same_window": valid, "failed_gates": failures, "polymarket_closed_trades": len(included),
            "polymarket_net_pnl_usd": pnl, "polymarket_net_roi_pct": pm_roi,
            "binance_net_roi_pct": bn_roi, "roi_delta_pct_points": pm_roi - bn_roi if valid and pm_roi is not None else None,
            "beats_binance": bool(valid and pm_roi is not None and bn_roi is not None and pm_roi > bn_roi),
            "boundary_position_ids": boundary_positions,
        })
    consecutive = 0
    previous_end = None
    best_consecutive = 0
    for row in results:
        start, end = parse_iso(row.get("start_at")), parse_iso(row.get("end_at"))
        contiguous = previous_end is None or (start is not None and abs((start - previous_end).total_seconds()) <= 60)
        if row["beats_binance"] and contiguous:
            consecutive += 1
        elif row["beats_binance"]:
            consecutive = 1
        else:
            consecutive = 0
        best_consecutive = max(best_consecutive, consecutive)
        previous_end = end
    valid_rows = [row for row in results if row["valid_same_window"]]
    pm_pnl = sum(row["polymarket_net_pnl_usd"] for row in valid_rows)
    # Each window resets to identical capital; aggregate ROI uses total capital-time denominator.
    aggregate_pm_roi = pm_pnl / (initial_equity * len(valid_rows)) * 100 if initial_equity and valid_rows else None
    aggregate_bn_roi = sum(float(row["binance_net_roi_pct"]) for row in valid_rows) / len(valid_rows) if valid_rows else None
    return {
        "schema_version": "polymarket-binance-window-comparison-v1", "windows": results,
        "observation_max_gap_hours": max_gap_hours,
        "valid_window_count": len(valid_rows), "best_consecutive_windows_beating_binance": best_consecutive,
        "aggregate_polymarket_roi_pct": aggregate_pm_roi, "aggregate_binance_roi_pct": aggregate_bn_roi,
        "aggregate_roi_advantage_pct_points": aggregate_pm_roi - aggregate_bn_roi if aggregate_pm_roi is not None and aggregate_bn_roi is not None else None,
        "comparison_status": "same_window_verified" if valid_rows else "same_window_evidence_missing",
    }


def audit_ledger(
    ledger: dict[str, Any], binance: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validation = validation or {}
    closed = ledger.get("closed_positions", [])
    scored = []
    for row in closed:
        outcome = resolved_outcome(row)
        probability = as_float(row.get("model_probability_at_entry"))
        if outcome is not None and probability is not None and 0 <= probability <= 1:
            scored.append((min(1 - 1e-12, max(1e-12, probability)), outcome, row))
    brier = sum((p - y) ** 2 for p, y, _ in scored) / len(scored) if scored else None
    log_loss = -sum(y * math.log(p) + (1-y) * math.log(1-p) for p, y, _ in scored) / len(scored) if scored else None
    pnl = sum(float(row.get("net_pnl_usd", 0)) for row in closed)
    cost = sum(float(row.get("cost_basis_usd", 0)) for row in closed)
    roi = pnl / cost * 100 if cost else None
    wins = sum(1 for row in closed if row.get("net_pnl_usd", 0) > 0)
    observations_by_trade: dict[str, list[dict[str, Any]]] = {}
    observation_id_counts: dict[str, int] = {}
    for observation in ledger.get("position_observations", []):
        observations_by_trade.setdefault(str(observation.get("paper_trade_id")), []).append(observation)
        observation_id = str(observation.get("observation_id") or "")
        observation_id_counts[observation_id] = observation_id_counts.get(observation_id, 0) + 1
    min_clv_minutes = float(ledger.get("clv_min_horizon_minutes", 60))

    def has_valid_first_clv_observation(row: dict[str, Any]) -> bool:
        if row.get("clv_per_share") is None or not row.get("clv_observed_at") or row.get("clv_horizon_minutes") is None:
            return False
        if float(row["clv_horizon_minutes"]) < min_clv_minutes or not row.get("clv_observation_id"):
            return False
        observations = observations_by_trade.get(str(row.get("paper_trade_id")), [])
        eligible = sorted(
            (
                observation for observation in observations
                if observation.get("best_bid") is not None
                and as_float(observation.get("minutes_since_entry")) is not None
                and float(observation["minutes_since_entry"]) >= min_clv_minutes
            ),
            key=lambda observation: str(observation.get("observed_at", "")),
        )
        if not eligible or eligible[0].get("observation_id") != row.get("clv_observation_id"):
            return False
        reference = eligible[0]
        return (
            observation_id_counts.get(str(reference.get("observation_id")), 0) == 1
            and reference.get("paper_only") is True
            and reference.get("live_orders_enabled") is False
            and reference.get("private_api_used") is False
            and reference.get("is_clv_reference") is True
            and reference.get("observed_at") == row.get("clv_observed_at")
            and abs(float(reference["best_bid"]) - float(row.get("clv_reference_price", reference["best_bid"]))) <= 1e-9
        )

    clv_rows = [row for row in closed if has_valid_first_clv_observation(row)]
    clvs = [float(row["clv_per_share"]) for row in clv_rows]
    mean_clv = sum(clvs) / len(clvs) if clvs else None
    n = len(closed)
    returns = [float(row.get("net_pnl_usd", 0)) / float(row.get("cost_basis_usd", 1)) for row in closed]
    mean_return = sum(returns) / n if n else None
    se = math.sqrt(sum((value - mean_return) ** 2 for value in returns) / (n - 1) / n) if n > 1 else None
    ci_low = mean_return - 1.96 * se if se is not None else None
    domains = {}
    for row in closed:
        bucket = domains.setdefault(row.get("domain", "other"), {"trades": 0, "net_pnl_usd": 0.0})
        bucket["trades"] += 1
        bucket["net_pnl_usd"] += float(row.get("net_pnl_usd", 0))
    binance_roi = as_float((binance or {}).get("net_roi_pct"))
    settled_markets_scanned = int(validation.get("settled_markets_scanned") or 0)
    # Forward evidence is ledger-derived. External validation artifacts cannot inflate it.
    forward_trade_count = n
    domain_sample_counts = {key: value["trades"] for key, value in domains.items()}
    enabled_domains = validation.get("enabled_domains") or list(domain_sample_counts)
    friction_complete = all(
        row.get("entry_fee_usd") is not None
        and row.get("fee_rate") is not None
        and row.get("spread_at_entry") is not None
        and row.get("price_impact_at_entry") is not None
        and row.get("slippage_at_entry") is not None
        for row in closed
    ) and bool(closed)
    largest_loss_pct = max(
        (max(0.0, -float(row.get("net_pnl_usd", 0))) / float(ledger.get("initial_equity_usd", 1)) * 100 for row in closed),
        default=0.0,
    )
    back_cases_by_trade: dict[str, list[dict[str, Any]]] = {}
    for back_case in ledger.get("back_cases", []):
        back_cases_by_trade.setdefault(str(back_case.get("paper_trade_id")), []).append(back_case)
    review_required = [row for row in closed if float(row.get("net_pnl_usd", 0)) <= 0 or row.get("exit_reason")]
    complete_review_ids = []
    avoidable_rule_losses = 0
    unknown_rule_classifications = 0
    for row in review_required:
        cases = back_cases_by_trade.get(str(row.get("paper_trade_id")), [])
        complete_cases = [case for case in cases if back_case_is_complete(case)]
        if complete_cases:
            complete_review_ids.append(row.get("paper_trade_id"))
            if any(case.get("avoidable_rule_misread") is True for case in complete_cases):
                avoidable_rule_losses += 1
        else:
            unknown_rule_classifications += 1
    scoring_coverage_complete = bool(closed) and len(scored) == len(closed)
    clv_coverage_complete = bool(closed) and len(clv_rows) == len(closed)
    score_comparison = validation.get("market_score_comparison") or {}
    score_comparison_proven = (
        score_comparison.get("sustained_improvement") is True
        and int(score_comparison.get("model_samples") or 0) == len(scored)
        and int(score_comparison.get("market_samples") or 0) == len(scored)
        and len(scored) >= 100
        and bool(score_comparison.get("artifact_id"))
    )
    window_comparison = compare_30d_windows(ledger, binance)
    same_window_delta = window_comparison.get("aggregate_roi_advantage_pct_points")
    checks = {
        "settled_markets_scanned_at_least_500": settled_markets_scanned >= 500,
        "forward_paper_trades_at_least_100": forward_trade_count >= 100,
        "each_enabled_domain_at_least_30": bool(enabled_domains) and all(int(domain_sample_counts.get(domain, 0)) >= 30 for domain in enabled_domains),
        "realistic_friction_fields_complete": friction_complete,
        "net_roi_positive": roi is not None and roi > 0,
        "mean_trade_net_ev_95pct_lower_positive": ci_low is not None and ci_low > 0,
        "forecast_scoring_coverage_complete": scoring_coverage_complete,
        "brier_or_log_loss_improves_vs_market": score_comparison_proven,
        "clv_protocol_coverage_complete": clv_coverage_complete,
        "mean_clv_positive": clv_coverage_complete and mean_clv is not None and mean_clv > 0,
        "max_drawdown_within_15pct": ledger.get("max_drawdown_pct", 0) >= -15,
        "single_event_max_loss_within_20pct": largest_loss_pct <= 20,
        "zero_avoidable_rule_misread_losses": avoidable_rule_losses == 0 and unknown_rule_classifications == 0,
        "all_losses_and_early_exits_have_complete_back_case": len(complete_review_ids) == len(review_required),
        "polymarket_beats_binance_by_5pct": same_window_delta is not None and same_window_delta >= 5,
        "two_positive_domains": sum(1 for row in domains.values() if row["net_pnl_usd"] > 0) >= 2,
        "three_consecutive_30d_windows_beat_binance": window_comparison["best_consecutive_windows_beating_binance"] >= 3,
    }
    return {
        "schema_version": "polymarket-audit-v2", "created_at": now_iso(), "paper_only": True,
        "live_orders_enabled": False, "private_api_used": False, "closed_trades": n, "wins": wins,
        "win_rate_pct": wins / n * 100 if n else None, "net_pnl_usd": pnl, "net_roi_pct": roi,
        "mean_trade_return_95pct_lower": ci_low, "resolved_forecast_samples": len(scored),
        "unresolved_closed_forecasts": n - len(scored), "brier_score": brier, "log_loss": log_loss,
        "mean_clv_per_share": mean_clv, "clv_protocol_samples": len(clv_rows),
        "position_observation_count": len(ledger.get("position_observations", [])),
        "max_drawdown_pct": ledger.get("max_drawdown_pct", 0),
        "largest_single_event_loss_pct_of_initial_equity": largest_loss_pct,
        "avoidable_rule_misread_losses": avoidable_rule_losses,
        "unknown_rule_classifications": unknown_rule_classifications,
        "review_required_trade_count": len(review_required),
        "complete_back_case_trade_count": len(complete_review_ids),
        "settled_markets_scanned": settled_markets_scanned,
        "forward_paper_trades": forward_trade_count,
        "domain_sample_counts": domain_sample_counts,
        "domains": domains, "legacy_binance_net_roi_pct": binance_roi,
        "legacy_binance_comparison_status": (binance or {}).get("comparison_status"),
        "same_window_comparison": window_comparison,
        "roi_advantage_vs_binance_pct_points": same_window_delta,
        "acceptance_checks": checks, "goal_complete": all(checks.values()),
    }


def audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Polymarket Alpha Goal Completion Audit",
        "",
        f"- Goal complete: `{str(audit['goal_complete']).lower()}`",
        f"- Settled markets scanned: {audit['settled_markets_scanned']}",
        f"- Forward closed paper trades: {audit['forward_paper_trades']}",
        f"- Resolved forecast samples: {audit['resolved_forecast_samples']}",
        f"- CLV protocol samples: {audit['clv_protocol_samples']}",
        f"- Append-only position observations: {audit['position_observation_count']}",
        f"- Valid same-window periods: {audit['same_window_comparison']['valid_window_count']}",
        f"- Best consecutive windows beating Binance: {audit['same_window_comparison']['best_consecutive_windows_beating_binance']}",
        "",
        "Only ledger-derived forward evidence counts. Missing, partial, legacy,",
        "unresolved, or externally asserted evidence remains a failed gate.",
        "",
        "## Acceptance checks",
        "",
    ]
    for name, passed in audit["acceptance_checks"].items():
        lines.append(f"- [{'x' if passed else ' '}] `{name}`")
    lines.extend(["", "## Current blockers", ""])
    blockers = [name for name, passed in audit["acceptance_checks"].items() if not passed]
    lines.extend(f"- `{name}`" for name in blockers)
    if not blockers:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def self_test(policy: dict[str, Any]) -> dict[str, Any]:
    end = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    market = {"id": "m1", "event_id": "event-1", "question": "Will Bitcoin exceed a level?", "endDate": end, "active": True, "closed": False, "acceptingOrders": True, "outcomes": '["Yes","No"]', "outcomePrices": '["0.80","0.20"]', "clobTokenIds": '["yes1","no1"]', "resolutionSource": "https://official.test/rules", "feesEnabled": True, "feeSchedule": {"rate": 0.07}}
    book = {"bids": [{"price": "0.79", "size": "10000"}], "asks": [{"price": "0.80", "size": "10000"}]}
    estimate = {"probability": 0.88, "confidence_low": 0.78, "confidence_high": 0.93, "model_version": "fixture-v1", "calibration_samples": 50, "sources": [{"kind": "official", "url": "https://official.test"}, {"kind": "independent", "url": "https://a.test"}, {"kind": "independent", "url": "https://b.test"}], "rules_review": {"status": "clear", "reviewed_at": now_iso()}, "failure_paths": [{"name": "price_reversal", "controlled": True}]}
    scan = scan_payload([market], {"yes1": book}, {"m1": estimate}, policy)
    assert len(scan["selected_candidates"]) == 1
    assert scan["selected_candidates"][0]["entry_fee_per_share"] > 0
    second_market = {**market, "id": "m2", "question": "Will Ethereum exceed a level?", "clobTokenIds": '["yes2","no2"]'}
    correlated = scan_payload([market, second_market], {"yes1": book, "yes2": book}, {"m1": estimate, "m2": estimate}, policy)
    assert len(correlated["selected_candidates"]) == 1
    assert correlated["selection_rejections"][0]["reason"] == "same_event_correlation_block"
    equal_price_estimate = {**estimate, "probability": 0.80, "confidence_low": 0.75, "confidence_high": 0.85}
    zero_edge = scan_payload([market], {"yes1": book}, {"m1": equal_price_estimate}, policy)
    assert not zero_edge["selected_candidates"]
    missing_sources = {**estimate, "sources": []}
    source_block = scan_payload([market], {"yes1": book}, {"m1": missing_sources}, policy)
    assert not source_block["selected_candidates"]
    ledger = new_ledger(policy)
    result = enter_scan(scan, ledger, policy)
    assert len(result["opened"]) == 1 and len(ledger["open_positions"]) == 1
    assert sum(row["risk_committed_usd"] for row in ledger["open_positions"]) <= policy["initial_equity_usd"] * 0.20
    settle_result = settle(ledger, {"m1": "NO"}, policy)
    assert len(settle_result["settled"]) == 1 and len(ledger["back_cases"]) == 1
    monitor_ledger = new_ledger(policy)
    enter_scan(scan, monitor_ledger, policy)
    monitor_result = monitor_positions(monitor_ledger, {"yes1": book}, {"m1": {"probability": 0.70}}, {}, policy)
    assert len(monitor_result["exited"]) == 1 and monitor_ledger["closed_positions"][0]["counterfactual_status"] == "pending"
    settle(monitor_ledger, {"m1": "YES"}, policy)
    assert monitor_ledger["closed_positions"][0]["counterfactual_status"] == "resolved"
    assert ledger["live_orders_enabled"] is False and ledger["private_api_used"] is False
    return {"status": "pass", "tests": ["strict_candidate_pass", "zero_edge_block", "missing_sources_block", "same_event_correlation_block", "paper_entry_risk_pool", "loss_back_case", "dynamic_exit", "exit_counterfactual_resolution", "paper_only_flags"], "live_orders_enabled": False, "private_api_used": False}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Polymarket paper-only alpha kernel")
    root.add_argument("--policy", default=str(DEFAULT_POLICY))
    sub = root.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--markets-json", required=True); scan.add_argument("--books-json", required=True); scan.add_argument("--estimates-json", required=True); scan.add_argument("--output", required=True)
    enter = sub.add_parser("enter")
    enter.add_argument("--scan-json", required=True); enter.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    settle_p = sub.add_parser("settle")
    settle_p.add_argument("--ledger", default=str(DEFAULT_LEDGER)); settle_p.add_argument("--resolutions-json", required=True)
    monitor = sub.add_parser("monitor")
    monitor.add_argument("--ledger", default=str(DEFAULT_LEDGER)); monitor.add_argument("--books-json", required=True); monitor.add_argument("--estimates-json", required=True); monitor.add_argument("--events-json")
    audit = sub.add_parser("audit")
    audit.add_argument("--ledger", default=str(DEFAULT_LEDGER)); audit.add_argument("--binance-metrics-json"); audit.add_argument("--validation-metrics-json"); audit.add_argument("--output"); audit.add_argument("--report")
    sub.add_parser("self-test")
    return root


def main() -> int:
    args = parser().parse_args()
    policy = read_json(args.policy)
    if policy.get("paper_only") is not True or policy.get("live_orders_enabled") is not False:
        raise SystemExit("unsafe policy: paper_only=true and live_orders_enabled=false required")
    if args.command == "self-test":
        payload = self_test(policy)
    elif args.command == "scan":
        payload = scan_payload(read_json(args.markets_json), read_json(args.books_json), read_json(args.estimates_json), policy)
        write_json(args.output, payload)
    elif args.command == "enter":
        ledger = load_ledger(args.ledger, policy); payload = enter_scan(read_json(args.scan_json), ledger, policy); write_json(args.ledger, ledger)
    elif args.command == "settle":
        ledger = load_ledger(args.ledger, policy); payload = settle(ledger, read_json(args.resolutions_json), policy); write_json(args.ledger, ledger)
    elif args.command == "monitor":
        ledger = load_ledger(args.ledger, policy)
        payload = monitor_positions(ledger, read_json(args.books_json), read_json(args.estimates_json), read_json(args.events_json) if args.events_json else {}, policy)
        write_json(args.ledger, ledger)
    else:
        ledger = load_ledger(args.ledger, policy)
        payload = audit_ledger(
            ledger,
            read_json(args.binance_metrics_json) if args.binance_metrics_json else None,
            read_json(args.validation_metrics_json) if args.validation_metrics_json else None,
        )
        if args.output: write_json(args.output, payload)
        if args.report:
            target = Path(args.report); target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(target.suffix + ".tmp")
            temp.write_text(audit_markdown(payload), encoding="utf-8"); temp.replace(target)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
