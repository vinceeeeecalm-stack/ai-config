#!/usr/bin/env python3
"""Build a Research Committee panel from current report context.

This runner converts existing evidence into the unified research_panel schema.
It is conservative by design: unless externally collected subagent outputs are
provided, it marks the panel as `research_committee_degraded` and blocks
`execute_now`. That makes the report auditable without pretending a real
parallel subagent committee ran inside this local script.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any


ROLE_IDS = [
    "prior_thesis_challenge_agent",
    "portfolio_state_agent",
    "macro_regime_agent",
    "crypto_market_agent",
    "onchain_defi_agent",
    "social_news_agent",
    "us_equity_alpha_agent",
    "backtest_validation_agent",
]

DEFAULT_REQUIRED_EXTERNAL_ROLE_IDS = set(ROLE_IDS)

REQUIRED_AGENT_FIELDS = {
    "agent_id",
    "asset_scope",
    "sources_used",
    "source_refs",
    "evidence_items",
    "signals",
    "plain_language_notes",
    "confidence_pct",
    "data_quality",
    "missing_data",
    "failed_gates",
    "recommended_max_action",
    "what_would_change_my_mind",
}

REQUIRED_SOURCE_FIELDS = {"name", "url_or_provider", "fresh_at", "status", "coverage"}
REQUIRED_SOURCE_REF_FIELDS = {"source_ref", "name", "url_or_provider", "fresh_at", "status", "coverage"}
REQUIRED_EVIDENCE_ITEM_FIELDS = {
    "field_id",
    "value",
    "unit",
    "as_of",
    "source_ref",
    "verification_status",
    "missing_reason",
}
REQUIRED_SIGNAL_FIELDS = {"asset", "signal_type", "direction", "summary", "time_window"}

VALID_DATA_QUALITY = {"verified", "degraded", "disputed", "stale", "missing"}
VALID_SOURCE_STATUS = {"ok", "fallback", "failed"}
VALID_EVIDENCE_STATUS = {"verified", "fallback", "missing", "disputed", "stale"}
VALID_ACTIONS = {
    "watch",
    "paper_only",
    "conditional_action",
    "risk_alert",
    "no_deploy",
    "block",
    "hold",
    "trim_review",
    "execute_now",
}
VALID_DIRECTIONS = {"bullish", "neutral", "bearish", "mixed"}

HARD_TERMS_REQUIRING_NOTE = {
    "oos",
    "walk-forward",
    "walkforward",
    "ev",
    "funding",
    "oi",
    "open interest",
    "slippage",
    "spread",
    "depth",
    "drawdown",
    "basis",
}

ROLE_NATIVE_EVIDENCE_PATTERNS = {
    "macro_regime_agent": [
        "fed",
        "rate expectation",
        "cme",
        "pce",
        "fund-flow",
        "fund flow",
        "fund_flows",
        "etf",
        "dxy",
        "vix",
        "treasury",
        "10y",
        "2y",
    ],
    "crypto_market_agent": [
        "order-book",
        "order book",
        "depth",
        "slippage",
        "float",
        "unlock",
        "venue",
        "liquidity",
        "funding",
        "open interest",
        "oi",
    ],
    "onchain_defi_agent": [
        "tvl",
        "fee",
        "revenue",
        "active address",
        "developer",
        "stablecoin flow",
        "chain",
        "explorer",
        "unlock",
        "float",
        "dust",
        "apy",
    ],
    "social_news_agent": [
        "social api",
        "x api",
        "posted_at",
        "timestamp",
        "official",
        "key-person",
        "key person",
        "source error",
        "403",
        "429",
        "catalyst",
        "price/volume confirmation",
    ],
    "us_equity_alpha_agent": [
        "screener",
        "intraday",
        "spread",
        "depth",
        "day_gainers",
        "most_actives",
        "fresh market",
        "candidate",
    ],
}

ACTION_BLOCKER_PATTERNS = [
    "broker",
    "cash",
    "buying power",
    "cost basis",
    "paper",
    "calibration",
    "walk-forward",
    "walkforward",
    "target_research_pass",
    "double-80",
    "double_80",
    "sample tier",
    "conservative ev",
    "reward/risk",
    "realtime signal",
    "execute_now",
    "human confirmation",
    "live_orders",
    "external pool",
    "cash floor",
    "margin",
    "permission",
]


def material_missing_data(output: dict[str, Any]) -> list[str]:
    missing = []
    for item in output.get("missing_data") or []:
        text = str(item)
        if text.lower() not in {"none", "n/a", "not_applicable", "not applicable"}:
            missing.append(text)
    return missing


def source_counts(output: dict[str, Any]) -> dict[str, int]:
    counts = {"ok": 0, "fallback": 0, "failed": 0, "total": 0}
    for item in output.get("sources_used") or []:
        status = item.get("status")
        counts["total"] += 1
        if status in counts:
            counts[status] += 1
    return counts


def role_native_evidence_gaps(output: dict[str, Any]) -> list[str]:
    role = output.get("agent_id")
    patterns = ROLE_NATIVE_EVIDENCE_PATTERNS.get(str(role), [])
    if not patterns:
        return []
    items = [str(item) for item in (output.get("missing_data") or []) + (output.get("failed_gates") or [])]
    gaps: list[str] = []
    for item in items:
        lowered = item.lower()
        if any(pattern in lowered for pattern in patterns):
            gaps.append(item)
    return gaps


def role_action_blockers(output: dict[str, Any]) -> list[str]:
    items = [str(item) for item in (output.get("missing_data") or []) + (output.get("failed_gates") or [])]
    blockers = []
    for item in items:
        lowered = item.lower()
        if any(pattern in lowered for pattern in ACTION_BLOCKER_PATTERNS):
            blockers.append(item)
    if output.get("recommended_max_action") in {"block", "no_deploy", "risk_alert"}:
        blockers.append(f"recommended_max_action={output.get('recommended_max_action')}")
    return blockers


def role_evidence_quality_status(output: dict[str, Any], output_errors: list[str]) -> str:
    """Separate research evidence quality from action readiness.

    A subagent can have high-quality evidence and still recommend watch/paper
    because it found real blockers such as missing cost basis, cash rail
    uncertainty, or insufficient paper samples. Those blockers belong in
    action readiness, not in the research-role evidence-quality gate.
    """

    if output_errors:
        return "invalid"
    data_quality = output.get("data_quality")
    counts = source_counts(output)
    if data_quality in {"missing", "stale", "disputed"}:
        return str(data_quality)
    if counts["ok"] == 0:
        return "degraded_no_ok_source"
    if counts["failed"] > counts["ok"]:
        return "degraded_source_failures"
    if counts["fallback"] > counts["ok"]:
        return "degraded_fallback_heavy"
    if role_native_evidence_gaps(output):
        return "degraded_role_native_gaps"
    return "verified"


def hard_terms_without_notes(output: dict[str, Any]) -> list[str]:
    notes = [str(item).lower() for item in output.get("plain_language_notes") or []]
    for signal in output.get("signals") or []:
        if isinstance(signal, dict):
            for key in ("plain_language_note", "term_note", "glossary_note"):
                if signal.get(key):
                    notes.append(str(signal.get(key)).lower())
    notes_text = " ".join(notes)
    gaps: list[str] = []
    for index, signal in enumerate(output.get("signals") or []):
        if not isinstance(signal, dict):
            continue
        text = " ".join(
            str(signal.get(key) or "").lower()
            for key in ("signal_type", "summary", "time_window")
        )
        for term in HARD_TERMS_REQUIRING_NOTE:
            pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
            if re.search(pattern, text) and not re.search(pattern, notes_text):
                gaps.append(f"signals[{index}] uses `{term}` without plain_language_notes")
                break
    return gaps


def role_action_readiness_status(output: dict[str, Any]) -> str:
    data_quality = output.get("data_quality")
    if data_quality in {"missing", "stale", "disputed"}:
        return "blocked_by_data_quality"
    if output.get("recommended_max_action") in {"block", "no_deploy", "risk_alert"}:
        return "blocked_by_role_decision"
    if role_action_blockers(output):
        return "not_ready_action_blockers"
    if material_missing_data(output) or output.get("failed_gates"):
        return "not_ready_missing_data_or_failed_gates"
    if output.get("recommended_max_action") in {"execute_now", "conditional_action"}:
        return "ready_for_role_max_action"
    return "watch_or_paper_ready"


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _signal_payloads(value: Any) -> list[Any]:
    """Accept useful nested subagent signal maps without treating them as fatal.

    External research agents often return structured sections such as
    {"rates_policy": {...}, "inflation": {...}} instead of the strict list of
    signal rows. Preserve that evidence by collapsing each section into a
    conservative signal row. This is schema normalization only; it does not
    upgrade data quality or action readiness.
    """

    if isinstance(value, list) or value is None:
        return _as_list(value)
    if not isinstance(value, dict):
        return _as_list(value)
    if REQUIRED_SIGNAL_FIELDS.intersection(value.keys()):
        return [value]

    rows: list[dict[str, Any]] = []
    for key, item in value.items():
        if isinstance(item, list):
            for index, subitem in enumerate(item):
                if isinstance(subitem, dict) and REQUIRED_SIGNAL_FIELDS.intersection(subitem.keys()):
                    rows.append(subitem)
                else:
                    rows.append(
                        {
                            "asset": str(key),
                            "signal_type": "research",
                            "direction": "mixed",
                            "summary": json.dumps(subitem, ensure_ascii=False, default=str)[:1200],
                            "time_window": "current",
                        }
                    )
            continue
        if isinstance(item, dict):
            rows.append(
                {
                    "asset": str(item.get("asset") or key),
                    "signal_type": str(item.get("signal_type") or key),
                    "direction": item.get("direction") or item.get("read") or "mixed",
                    "summary": str(
                        item.get("summary")
                        or item.get("interpretation")
                        or item.get("read")
                        or json.dumps(item, ensure_ascii=False, default=str)[:1200]
                    ),
                    "time_window": str(item.get("time_window") or item.get("timestamp") or "current"),
                }
            )
            continue
        rows.append(
            {
                "asset": str(key),
                "signal_type": "research",
                "direction": "mixed",
                "summary": str(item),
                "time_window": "current",
            }
        )
    return rows


def normalize_data_quality(value: Any) -> tuple[str, str | None]:
    """Convert human-readable subagent quality notes into the strict enum.

    Real subagents often return useful but non-enum values such as
    "medium_high" or {"overall": "medium"}. Keep this conservative: any
    ambiguous or partial quality becomes `degraded`, never `verified`.
    """

    if isinstance(value, str) and value in VALID_DATA_QUALITY:
        return str(value), None
    note = f"data_quality normalized from {value!r}"
    if isinstance(value, dict):
        text = " ".join(str(item).lower() for item in value.values())
    else:
        text = str(value or "").lower()
    if "disputed" in text or "conflict" in text:
        return "disputed", note
    if "stale" in text:
        return "stale", note
    if "missing" in text and not any(token in text for token in ["medium", "partial", "degraded", "verified"]):
        return "missing", note
    if "verified" in text and not any(token in text for token in ["degraded", "partial", "medium", "missing", "failed"]):
        return "verified", note
    return "degraded", note


def normalize_source_status(value: Any) -> tuple[str, str | None]:
    if isinstance(value, str) and value in VALID_SOURCE_STATUS:
        return str(value), None
    text = str(value or "").lower()
    note = f"source.status normalized from {value!r}"
    if any(token in text for token in ["fail", "error", "timeout", "blocked", "missing", "unavailable"]):
        return "failed", note
    if any(token in text for token in ["partial", "fallback", "degraded", "reference", "snippet", "rate_limited"]):
        return "fallback", note
    if any(token in text for token in ["ok", "read", "verified", "official", "success", "crawled"]):
        return "ok", note
    return "fallback", note


def normalize_action(value: Any) -> tuple[str, str | None]:
    if isinstance(value, str) and value in VALID_ACTIONS:
        return str(value), None
    text = str(value or "").lower()
    note = f"recommended_max_action normalized from {value!r}"
    if "execute_now" in text:
        return "execute_now", note
    if "block" in text:
        return "block", note
    if "no_deploy" in text or "no deploy" in text:
        return "no_deploy", note
    if "risk_alert" in text or "risk alert" in text:
        return "risk_alert", note
    if "conditional" in text:
        return "conditional_action", note
    if "paper" in text:
        return "paper_only", note
    if "trim" in text:
        return "trim_review", note
    if "hold" in text:
        return "hold", note
    return "watch", note


def normalize_direction(value: Any) -> tuple[str, str | None]:
    if isinstance(value, str) and value in VALID_DIRECTIONS:
        return str(value), None
    text = str(value or "").lower()
    note = f"signal.direction normalized from {value!r}"
    if any(token in text for token in ["bull", "positive", "constructive", "upside", "rebound"]):
        if any(token in text for token in ["weak", "mixed", "uncertain", "cautious"]):
            return "mixed", note
        return "bullish", note
    if any(token in text for token in ["bear", "weak", "risk_off", "risk-off", "downtrend", "negative"]):
        return "bearish", note
    if any(token in text for token in ["hold", "stable", "neutral"]):
        return "neutral", note
    return "mixed", note


def normalize_external_agent_output(output: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(output, dict):
        return output

    normalized = dict(output)
    notes: list[str] = []
    agent_id = normalized.get("agent_id") or f"agent_outputs[{index}]"

    if "asset_scope" in normalized:
        normalized["asset_scope"] = [str(item) for item in _as_list(normalized.get("asset_scope")) if item is not None]
    normalized.setdefault("asset_scope", [])

    quality, note = normalize_data_quality(normalized.get("data_quality"))
    normalized["data_quality"] = quality
    if note:
        notes.append(note)

    action, note = normalize_action(normalized.get("recommended_max_action"))
    normalized["recommended_max_action"] = action
    if note:
        notes.append(note)

    confidence = normalized.get("confidence_pct")
    try:
        normalized["confidence_pct"] = max(0, min(100, float(confidence)))
    except (TypeError, ValueError):
        normalized["confidence_pct"] = 0
        notes.append(f"{agent_id}.confidence_pct defaulted to 0 from {confidence!r}")

    for field in ["missing_data", "failed_gates", "what_would_change_my_mind"]:
        normalized[field] = [str(item) for item in _as_list(normalized.get(field)) if item is not None]

    sources: list[dict[str, Any]] = []
    for source_index, item in enumerate(_as_list(normalized.get("sources_used"))):
        if not isinstance(item, dict):
            notes.append(f"{agent_id}.sources_used[{source_index}] dropped non-object source")
            continue
        source_item = dict(item)
        status, note = normalize_source_status(source_item.get("status"))
        source_item["status"] = status
        if note:
            notes.append(f"{agent_id}.sources_used[{source_index}].{note}")
        source_item.setdefault("name", f"{agent_id}_source_{source_index + 1}")
        source_item.setdefault(
            "url_or_provider",
            source_item.get("path") or source_item.get("url") or source_item.get("provider") or source_item.get("name"),
        )
        source_item.setdefault("fresh_at", utc_now())
        source_item.setdefault("coverage", source_item.get("key_evidence") or "unspecified")
        sources.append(source_item)
    normalized["sources_used"] = sources

    source_refs: list[dict[str, Any]] = []
    supplied_refs = _as_list(normalized.get("source_refs"))
    if supplied_refs:
        for ref_index, item in enumerate(supplied_refs):
            if not isinstance(item, dict):
                notes.append(f"{agent_id}.source_refs[{ref_index}] dropped non-object source ref")
                continue
            ref_item = dict(item)
            status, note = normalize_source_status(ref_item.get("status"))
            ref_item["status"] = status
            if note:
                notes.append(f"{agent_id}.source_refs[{ref_index}].{note}")
            ref_item.setdefault("source_ref", f"src_{ref_index + 1}")
            ref_item.setdefault("name", ref_item.get("source_ref"))
            ref_item.setdefault("url_or_provider", ref_item.get("provider") or ref_item.get("url") or ref_item.get("name"))
            ref_item.setdefault("fresh_at", utc_now())
            ref_item.setdefault("coverage", "unspecified")
            source_refs.append(ref_item)
    else:
        for ref_index, source_item in enumerate(sources):
            source_refs.append(
                {
                    "source_ref": f"src_{ref_index + 1}",
                    "name": source_item.get("name"),
                    "url_or_provider": source_item.get("url_or_provider"),
                    "fresh_at": source_item.get("fresh_at"),
                    "status": source_item.get("status"),
                    "coverage": source_item.get("coverage"),
                }
            )
    normalized["source_refs"] = source_refs

    evidence_items: list[dict[str, Any]] = []
    for evidence_index, item in enumerate(_as_list(normalized.get("evidence_items"))):
        if not isinstance(item, dict):
            notes.append(f"{agent_id}.evidence_items[{evidence_index}] dropped non-object evidence item")
            continue
        evidence_item = dict(item)
        evidence_item.setdefault("field_id", f"evidence_{evidence_index + 1}")
        evidence_item.setdefault("value", None)
        evidence_item.setdefault("unit", "")
        evidence_item.setdefault("as_of", evidence_item.get("fresh_at") or utc_now())
        evidence_item.setdefault("source_ref", source_refs[0]["source_ref"] if source_refs else "")
        evidence_item.setdefault("verification_status", "fallback")
        evidence_item.setdefault("missing_reason", "")
        evidence_items.append(evidence_item)
    normalized["evidence_items"] = evidence_items

    signals: list[dict[str, Any]] = []
    for signal_index, item in enumerate(_signal_payloads(normalized.get("signals"))):
        if not isinstance(item, dict):
            notes.append(f"{agent_id}.signals[{signal_index}] dropped non-object signal")
            continue
        signal_item = dict(item)
        direction, note = normalize_direction(signal_item.get("direction"))
        signal_item["direction"] = direction
        if note:
            notes.append(f"{agent_id}.signals[{signal_index}].{note}")
        signal_item.setdefault("asset", "portfolio")
        signal_item.setdefault("signal_type", "research")
        signal_item.setdefault("summary", "No summary supplied by subagent.")
        signal_item.setdefault("time_window", "current")
        signals.append(signal_item)
    normalized["signals"] = signals
    normalized["plain_language_notes"] = [str(item) for item in _as_list(normalized.get("plain_language_notes")) if item is not None]

    if notes:
        existing_notes = _as_list(normalized.get("normalization_notes"))
        normalized["normalization_notes"] = [str(item) for item in existing_notes if item is not None] + notes
        normalized["schema_normalized"] = True
    return normalized


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: str | None) -> Any:
    if not path:
        return None
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def source(name: str, status: str, coverage: str, fresh_at: str | None = None, provider: str | None = None) -> dict[str, Any]:
    normalized_status, _ = normalize_source_status(status)
    return {
        "name": name,
        "url_or_provider": provider or name,
        "fresh_at": fresh_at,
        "status": normalized_status,
        "coverage": coverage,
    }


def agent(
    agent_id: str,
    asset_scope: list[str],
    sources_used: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    confidence_pct: int,
    data_quality: str,
    missing_data: list[str],
    failed_gates: list[str],
    recommended_max_action: str,
    what_would_change_my_mind: list[str],
) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "asset_scope": asset_scope,
        "sources_used": sources_used,
        "source_refs": [
            {
                "source_ref": f"src_{index + 1}",
                "name": item.get("name"),
                "url_or_provider": item.get("url_or_provider"),
                "fresh_at": item.get("fresh_at"),
                "status": item.get("status"),
                "coverage": item.get("coverage"),
            }
            for index, item in enumerate(sources_used)
        ],
        "evidence_items": [],
        "signals": signals,
        "plain_language_notes": [],
        "confidence_pct": confidence_pct,
        "data_quality": data_quality,
        "missing_data": missing_data,
        "failed_gates": failed_gates,
        "recommended_max_action": recommended_max_action,
        "what_would_change_my_mind": what_would_change_my_mind,
    }


def signal(asset: str, signal_type: str, direction: str, summary: str, time_window: str) -> dict[str, str]:
    return {
        "asset": asset,
        "signal_type": signal_type,
        "direction": direction,
        "summary": summary,
        "time_window": time_window,
    }


def portfolio_role(context: dict[str, Any]) -> dict[str, Any]:
    portfolio = context.get("portfolio_snapshot", {})
    holdings = portfolio.get("top_holdings", [])
    total = portfolio.get("total_value")
    eth = next((h for h in holdings if h.get("symbol") == "ETH"), {})
    sol = next((h for h in holdings if h.get("symbol") == "SOL"), {})
    ada = next((h for h in holdings if h.get("symbol") == "ADA"), {})
    cash = portfolio.get("cash_or_stablecoin")
    quality = portfolio.get("data_quality_summary", {})

    signals = [
        signal("portfolio", "valuation", "neutral", f"Total portfolio value is approximately {total}.", "current"),
    ]
    if eth:
        signals.append(signal("ETH/lcETH", "positioning", "bearish", f"ETH/lcETH weight is {eth.get('current_weight_pct'):.2f}% and remains concentrated.", "5y-10y"))
    if sol:
        signals.append(signal("SOL", "positioning", "bullish", f"SOL weight is {sol.get('current_weight_pct'):.2f}%, still underweight versus goal-oriented growth sleeve.", "5y-10y"))
    if ada:
        signals.append(signal("ADA", "positioning", "mixed", f"ADA weight is {ada.get('current_weight_pct'):.2f}%, usable as satellite but not primary engine.", "5y-10y"))
    if cash is not None:
        signals.append(signal("cash_rails", "liquidity", "mixed", f"Combined cash/stablecoin is {cash}; crypto cash is near zero while US equity cash is user-stated.", "current"))

    missing = []
    failed = []
    if "lceth" in quality and "degraded" in str(quality["lceth"]):
        missing.append("lcETH receipt conversion/unlock/fee verification")
        failed.append("staking_valuation_degraded")
    if "us_equity_cash" in quality and "user_stated" in str(quality["us_equity_cash"]):
        missing.append("broker-confirmed US equity cash/buying power")
        failed.append("us_equity_cash_not_broker_verified")

    return agent(
        "portfolio_state_agent",
        ["portfolio", "ETH/lcETH", "SOL", "ADA", "NIGHT", "US equities"],
        [source("portfolio_snapshot", "ok", "holdings_weights_cash", portfolio.get("generated_at"), "local_snapshot")],
        signals,
        78 if failed else 86,
        "degraded" if failed else "verified",
        missing,
        failed,
        "conditional_action" if failed else "conditional_action",
        [
            "Broker cash export verifies US equity cash.",
            "lcETH provider data verifies conversion ratio, redemption delay, and fees.",
        ],
    )


def goal_role(context: dict[str, Any]) -> dict[str, Any]:
    projection = context.get("goal_path_projection", {})
    required = projection.get("required", {})
    five = required.get("5", {})
    ten = required.get("10", {})
    asset_goal = (context.get("asset_goal_contribution_panel") or {}).get("summary") or {}
    allocation = asset_goal.get("allocation_by_goal_label") or {}
    dca_guidance = asset_goal.get("dca_guidance") or {}
    signals = [
        signal(
            "portfolio",
            "goal_gap",
            "mixed",
            "5y current-principal 10x requires about "
            f"{(five.get('current_principal_required_annual_rate') or 0) * 100:.1f}% annualized; "
            "10y requires about "
            f"{(ten.get('current_principal_required_annual_rate') or 0) * 100:.1f}%.",
            "5y-10y",
        )
    ]
    if asset_goal:
        signals.append(
            signal(
                "portfolio",
                "goal_contribution",
                "mixed",
                "Accelerator weight is "
                f"{allocation.get('accelerator_weight_pct')}%, drag weight is "
                f"{allocation.get('drag_weight_pct')}%; primary DCA pair is "
                f"{dca_guidance.get('primary_pair')}, secondary is {dca_guidance.get('secondary_pair')}.",
                "5y-10y",
            )
        )
        for item in asset_goal.get("assets", []):
            if item.get("symbol") in {"ETH", "SOL", "ADA", "NIGHT", "BTC"}:
                signals.append(
                    signal(
                        item.get("symbol"),
                        "goal_contribution",
                        "mixed",
                        f"{item.get('symbol')} is labeled {item.get('goal_gap_contribution')} with DCA implication {item.get('dca_weight_implication')}.",
                        "5y-10y",
                    )
                )
    missing = ["prior recommendation outcomes are still pending; no hit-rate evidence yet"]
    for item in asset_goal.get("missing_data") or []:
        if item not in missing:
            missing.append(item)
    failed = ["insufficient_outcome_history"]
    for flag in asset_goal.get("concentration_flags") or []:
        failed.append("goal_concentration_flag:" + flag)
    return agent(
        "prior_thesis_challenge_agent",
        ["portfolio", "SOL", "ADA", "NIGHT", "SOXL"],
        [
            source("goal_path_projection", "ok", "required_return_paths", None, "local_projection"),
            source("asset_goal_contribution_panel", "fallback" if asset_goal.get("data_quality") == "degraded" else "ok", "asset_labels_staking_compounding_dca_implication", asset_goal.get("generated_at"), "scripts/asset_goal_contribution.py"),
        ],
        signals,
        70 if asset_goal else 68,
        "degraded" if asset_goal.get("data_quality") == "degraded" else "verified",
        missing,
        failed,
        "conditional_action",
        [
            "Resolved recommendation history shows SOL/ADA DCA underperforming or outperforming expectations.",
            "5y required return becomes unrealistic after new capital or drawdown changes.",
            "Asset goal contribution panel shows accelerator exposure no longer underweight or ETH concentration no longer a drag.",
        ],
    )


def macro_role(context: dict[str, Any]) -> dict[str, Any]:
    macro_panel = context.get("macro_regime_panel") or {}
    summary = macro_panel.get("summary") or {}
    regime = summary.get("macro_regime") or {}
    if summary:
        status = macro_panel.get("status") or summary.get("data_quality") or "degraded"
        source_status = "ok" if status == "verified" else "fallback"
        missing = list(summary.get("missing_data") or [])
        failed = ["macro_data_degraded"] if status != "verified" else []
        fund_flow_quality = (summary.get("fund_flows") or {}).get("data_quality")
        if fund_flow_quality == "missing" and "digital asset ETF/fund flows" not in " ".join(missing):
            missing.append("digital asset ETF/fund flows")
            failed.append("fund_flows_missing")
        dca_pace = regime.get("dca_pace") or "split_more"
        posture = regime.get("us_tactical_risk_posture") or "conditional_only"
        return agent(
            "macro_regime_agent",
            ["macro", "crypto", "us_equity"],
            [
                source(
                    "macro_regime_snapshot",
                    source_status,
                    "rates_dxy_vix_yields_cpi_risk_appetite",
                    summary.get("generated_at"),
                    "scripts/macro_regime_snapshot.py",
                )
            ],
            [
                signal(
                    "macro",
                    "macro",
                    "mixed",
                    f"Regime {regime.get('classification')} with risk score {regime.get('risk_score_points')}; DCA pace {dca_pace}.",
                    "1d-30d",
                ),
                signal(
                    "us_tactical",
                    "macro",
                    "mixed",
                    f"US tactical posture is {posture}; macro cannot bypass sample-tier, conservative-EV, reward/risk, realtime-signal, or account-risk gates.",
                    "1d-10d",
                ),
            ],
            72 if status == "verified" else 60,
            "verified" if status == "verified" else "degraded",
            missing,
            failed,
            "conditional_action",
            [
                "Fed/PCE/fund-flow data confirms improving liquidity and risk appetite.",
                "VIX/DXY/yields reverse adversely enough to force split_more or wait_for_pullback.",
            ],
        )
    return agent(
        "macro_regime_agent",
        ["macro", "crypto", "us_equity"],
        [source("daily_report_context", "fallback", "no dedicated macro feed in context", context.get("generated_at"), "local_context")],
        [
            signal("macro", "macro", "mixed", "No dedicated Fed/DXY/VIX/ETF-flow panel was supplied to the runner; DCA pace must stay conservative.", "1d-30d"),
            signal("risk_assets", "macro", "mixed", "Macro can change DCA timing, but should not trigger single-asset strong buy without fresh source checks.", "1d-30d"),
        ],
        45,
        "missing",
        ["Fed/CME rate expectations", "DXY", "VIX", "10Y/2Y yields", "CPI/PCE", "digital asset ETF/fund flows"],
        ["macro_panel_missing"],
        "conditional_action",
        [
            "Fresh verified macro panel shows improving liquidity and risk appetite.",
            "ETF/fund flows turn materially positive for BTC/ETH/SOL-linked assets.",
        ],
    )


def crypto_role(crypto_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not crypto_snapshot:
        return agent(
            "crypto_market_agent",
            ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "NIGHTUSDT"],
            [source("crypto_multisource_snapshot", "failed", "missing", None, "local_file")],
            [],
            20,
            "missing",
            ["crypto multisource snapshot"],
            ["crypto_market_data_missing"],
            "watch",
            ["Run multisource crypto snapshot with Binance/CoinGecko/DeFiLlama data."],
        )

    generated_at = crypto_snapshot.get("generated_at")
    scores = crypto_snapshot.get("asset_scores") or []
    signals = []
    for row in scores:
        symbol = row.get("symbol")
        score = row.get("multi_source_alpha_score_points")
        interpretation = row.get("interpretation")
        direction = "bullish" if score and score >= 30 else ("neutral" if score and score >= 15 else "bearish")
        signals.append(
            signal(
                symbol,
                "trend",
                direction,
                f"24h {row.get('price_change_24h_pct')}%, quote volume {row.get('quote_volume_24h_usd')}, alpha score {score}, interpretation {interpretation}.",
                "1d-7d",
            )
        )
    return agent(
        "crypto_market_agent",
        [row.get("symbol") for row in scores if row.get("symbol")],
        [source("crypto_multisource_snapshot", "ok", "price_volume_funding_open_interest_sentiment", generated_at, "local_snapshot")],
        signals,
        74 if signals else 35,
        "verified" if signals else "missing",
        ["order-book depth by symbol not embedded in summarized context"],
        [],
        "paper_only" if signals else "watch",
        [
            "SOL/ADA/NIGHT scores improve with verified liquidity and catalyst.",
            "Funding/OI and spot volume confirm constructive continuation rather than weak bounce.",
        ],
    )


def onchain_role(crypto_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    sources = (crypto_snapshot or {}).get("sources", {})
    defi = sources.get("defillama_chains") if isinstance(sources, dict) else None
    status = defi.get("status") if isinstance(defi, dict) else "missing"
    return agent(
        "onchain_defi_agent",
        ["SOL", "ADA", "ETH", "NIGHT"],
        [source("defillama_chains", status or "missing", "chain_tvl_context", (crypto_snapshot or {}).get("generated_at"), "DeFiLlama")],
        [
            signal("SOL", "onchain", "mixed", "Needs chain-specific TVL, fees, stablecoin and activity confirmation before overweighting beyond DCA.", "30d-5y"),
            signal("ADA", "onchain", "mixed", "Deep-value thesis needs ecosystem/TVL/developer data to justify raising target weight.", "30d-5y"),
            signal("NIGHT", "onchain", "mixed", "NIGHT needs official float/unlock/ecosystem data before any new add.", "30d-5y"),
        ],
        48 if status == "ok" else 30,
        "degraded" if status == "ok" else "missing",
        ["asset-level TVL/fees/revenue/stablecoin supply", "unlock and circulating supply verification for NIGHT"],
        ["asset_micro_onchain_incomplete"],
        "conditional_action",
        [
            "Asset-specific DeFiLlama/explorer metrics confirm SOL growth or ADA recovery.",
            "NIGHT official tokenomics and liquidity become verified.",
        ],
    )


def social_role(context: dict[str, Any]) -> dict[str, Any]:
    handoffs = context.get("latest_active_alpha_handoffs", [])
    social = [h for h in handoffs if h.get("candidate_type") == "social_key_person_intel"]
    if social:
        status = "degraded" if any(h.get("research_committee_degraded") for h in social) else "verified"
        signals = [signal("crypto_social", "catalyst", "mixed", "Social/key-person handoff exists but must remain watch/risk context.", "1d-7d")]
    else:
        status = "missing"
        signals = [signal("crypto_social", "catalyst", "neutral", "No fresh key-person social handoff was present in the daily context.", "1d-7d")]
    return agent(
        "social_news_agent",
        ["BTC", "ETH", "SOL", "ADA", "NIGHT", "USDC", "COIN", "CRCL"],
        [source("latest_active_alpha_handoffs", "fallback", "social_news_context", context.get("generated_at"), "active-alpha")],
        signals,
        40 if status == "missing" else 55,
        status,
        [] if social else ["fresh crypto key-person social handoff"],
        ["social_context_missing"] if not social else [],
        "watch",
        [
            "Official account or verified key person confirms roadmap, tokenomics, security or regulatory event.",
            "Price/volume reaction confirms the social catalyst across multiple sources.",
        ],
    )


def us_equity_role(us_snapshot: dict[str, Any] | None, context: dict[str, Any]) -> dict[str, Any]:
    candidates = (us_snapshot or {}).get("candidates") or []
    current = (us_snapshot or {}).get("current_tactical_position") or "current_tactical_position"
    signals = []
    for item in candidates[:3]:
        setup_quality = item.get("setup_quality_score")
        reward_risk = item.get("reward_risk_ratio")
        sample_size = (item.get("probability_event") or {}).get("sample_size")
        conservative_ev = item.get("conservative_expected_value_pct")
        action = item.get("monitor_recommendation")
        signals.append(
            signal(
                item.get("symbol") or item.get("candidate_symbol"),
                "tactical_alpha",
                "neutral",
                f"US discovery candidate has setup quality {setup_quality}, RR {reward_risk}, sample n={sample_size}, conservative EV={conservative_ev}, action {action}; scanner score is not probability.",
                "1d-10d",
            )
        )
    if not signals:
        signals.append(signal(current, "tactical_alpha", "mixed", "No stronger US tactical candidate was available; keep relay logic conditional.", "1d-10d"))
    status = (us_snapshot or {}).get("scan_status")
    degraded = bool((us_snapshot or {}).get("degraded_no_actionable_trade"))
    return agent(
        "us_equity_alpha_agent",
        ["SOXL", "CRCL", "COIN", "dynamic_us_candidates"],
        [source("us_open_dynamic_scanner", status or "missing", "us_equity_candidates", (us_snapshot or {}).get("created_at"), "Yahoo/local_scanner")],
        signals,
        58 if candidates else 35,
        "degraded" if degraded else ("verified" if candidates else "missing"),
        ["complete Yahoo screener coverage", "US equity walk-forward base-rate evidence"] if degraded else ["US equity walk-forward base-rate evidence"],
        ["sample_ev_signal_risk_gate_not_certified"],
        "watch",
        [
            "A candidate beats the current tactical position and passes sample-tier, positive conservative-EV, RR>=2, realtime-signal, and account-risk gates.",
            "SOXL reaches trim zone or valid pullback entry with confirmed semiconductor trend.",
        ],
    )


def backtest_role(context: dict[str, Any]) -> dict[str, Any]:
    rec = context.get("recommendation_history_summary", {})
    summary = rec.get("summary") or {}
    pending = rec.get("pending_recommendations") or []
    handoffs = context.get("latest_active_alpha_handoffs", [])
    paper = [h for h in handoffs if "paper" in str(h.get("candidate_type"))]
    return agent(
        "backtest_validation_agent",
        ["recommendations", "paper_trades", "us_tactical", "crypto_tactical"],
        [
            source("recommendation_history", rec.get("status") or "missing", "outcome_tracking", summary.get("updated_at"), "local_ledger"),
            source("active_alpha_handoffs", "fallback", "paper_candidates", context.get("generated_at"), "active-alpha"),
        ],
        [
            signal("recommendation_history", "validation", "mixed", f"{summary.get('total_recommendations', 0)} recommendations recorded; {len(pending)} still pending.", "monthly"),
            signal("active_alpha_paper", "validation", "mixed", f"{len(paper)} recent paper handoff(s) present; manual must not upgrade without research committee.", "1d-7d"),
        ],
        52,
        "degraded",
        ["resolved outcomes for new recommendation ledger", "probability bucket calibration", "US equity walk-forward"],
        ["insufficient_resolved_sample_size"],
        "paper_only",
        [
            "At least 20 comparable recommendations or 3 monthly windows are resolved.",
            "US tactical candidates pass walk-forward/base-rate validation.",
        ],
    )


def load_external_agent_outputs(path: str | None) -> list[dict[str, Any]]:
    payload = load_json(path)
    if payload is None:
        return []
    if isinstance(payload, dict) and "agent_outputs" in payload:
        outputs = list(payload["agent_outputs"])
        normalized = [normalize_external_agent_output(output, index) for index, output in enumerate(outputs)]
        for output in normalized:
            if isinstance(output, dict):
                output["origin"] = "external_subagent"
        return normalized
    if isinstance(payload, list):
        normalized = [normalize_external_agent_output(output, index) for index, output in enumerate(payload)]
        for output in normalized:
            if isinstance(output, dict):
                output["origin"] = "external_subagent"
        return normalized
    raise ValueError("External agent output must be a list or object with agent_outputs")


def require_list(value: Any, field: str, errors: list[str], agent_id: str) -> None:
    if not isinstance(value, list):
        errors.append(f"{agent_id}.{field} must be a list")


def validate_external_agent_output(output: dict[str, Any], index: int) -> list[str]:
    errors: list[str] = []
    if not isinstance(output, dict):
        return [f"agent_outputs[{index}] must be an object"]

    agent_id = output.get("agent_id") or f"agent_outputs[{index}]"
    missing = sorted(REQUIRED_AGENT_FIELDS - set(output))
    if missing:
        errors.append(f"{agent_id} missing fields: {missing}")

    if output.get("agent_id") not in ROLE_IDS:
        errors.append(f"{agent_id} is not a recognized research role")
    if output.get("data_quality") not in VALID_DATA_QUALITY:
        errors.append(f"{agent_id}.data_quality must be one of {sorted(VALID_DATA_QUALITY)}")
    if output.get("recommended_max_action") not in VALID_ACTIONS:
        errors.append(f"{agent_id}.recommended_max_action must be one of {sorted(VALID_ACTIONS)}")
    if output.get("recommended_max_action") == "execute_now":
        errors.append(f"{agent_id}.recommended_max_action=execute_now is forbidden for subagent outputs")

    confidence = output.get("confidence_pct")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 100:
        errors.append(f"{agent_id}.confidence_pct must be a number between 0 and 100")

    for field in [
        "asset_scope",
        "sources_used",
        "source_refs",
        "evidence_items",
        "signals",
        "plain_language_notes",
        "missing_data",
        "failed_gates",
        "what_would_change_my_mind",
    ]:
        require_list(output.get(field), field, errors, agent_id)

    for source_index, item in enumerate(output.get("sources_used") or []):
        if not isinstance(item, dict):
            errors.append(f"{agent_id}.sources_used[{source_index}] must be an object")
            continue
        missing_source = sorted(REQUIRED_SOURCE_FIELDS - set(item))
        if missing_source:
            errors.append(f"{agent_id}.sources_used[{source_index}] missing fields: {missing_source}")
        if item.get("status") not in VALID_SOURCE_STATUS:
            errors.append(f"{agent_id}.sources_used[{source_index}].status must be one of {sorted(VALID_SOURCE_STATUS)}")
        if item.get("status") == "ok":
            for field in ["name", "url_or_provider", "fresh_at", "coverage"]:
                value = str(item.get(field) or "").strip().lower()
                if value in {"", "unspecified", "unknown", "n/a", "none"}:
                    errors.append(f"{agent_id}.sources_used[{source_index}].{field} must be specific for ok sources")

    for source_index, item in enumerate(output.get("source_refs") or []):
        if not isinstance(item, dict):
            errors.append(f"{agent_id}.source_refs[{source_index}] must be an object")
            continue
        missing_source = sorted(REQUIRED_SOURCE_REF_FIELDS - set(item))
        if missing_source:
            errors.append(f"{agent_id}.source_refs[{source_index}] missing fields: {missing_source}")
        if item.get("status") not in VALID_SOURCE_STATUS:
            errors.append(f"{agent_id}.source_refs[{source_index}].status must be one of {sorted(VALID_SOURCE_STATUS)}")

    for evidence_index, item in enumerate(output.get("evidence_items") or []):
        if not isinstance(item, dict):
            errors.append(f"{agent_id}.evidence_items[{evidence_index}] must be an object")
            continue
        missing_evidence = sorted(REQUIRED_EVIDENCE_ITEM_FIELDS - set(item))
        if missing_evidence:
            errors.append(f"{agent_id}.evidence_items[{evidence_index}] missing fields: {missing_evidence}")
        if item.get("verification_status") not in VALID_EVIDENCE_STATUS:
            errors.append(
                f"{agent_id}.evidence_items[{evidence_index}].verification_status must be one of {sorted(VALID_EVIDENCE_STATUS)}"
            )
        if item.get("verification_status") == "verified":
            for field in ["field_id", "as_of", "source_ref"]:
                value = str(item.get(field) or "").strip().lower()
                if value in {"", "unspecified", "unknown", "n/a", "none"}:
                    errors.append(f"{agent_id}.evidence_items[{evidence_index}].{field} must be specific for verified evidence")

    for signal_index, item in enumerate(output.get("signals") or []):
        if not isinstance(item, dict):
            errors.append(f"{agent_id}.signals[{signal_index}] must be an object")
            continue
        missing_signal = sorted(REQUIRED_SIGNAL_FIELDS - set(item))
        if missing_signal:
            errors.append(f"{agent_id}.signals[{signal_index}] missing fields: {missing_signal}")
        if item.get("direction") not in VALID_DIRECTIONS:
            errors.append(f"{agent_id}.signals[{signal_index}].direction must be one of {sorted(VALID_DIRECTIONS)}")

    for note_index, item in enumerate(output.get("plain_language_notes") or []):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{agent_id}.plain_language_notes[{note_index}] must be a non-empty string")

    errors.extend(f"{agent_id}.{gap}" for gap in hard_terms_without_notes(output))

    return errors


def validate_external_agent_outputs(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    role_counts: dict[str, int] = {}
    successful_roles: set[str] = set()
    verified_roles: set[str] = set()
    evidence_verified_roles: set[str] = set()
    action_ready_roles: set[str] = set()
    action_not_ready_roles: set[str] = set()
    evidence_incomplete_roles: set[str] = set()
    degraded_roles: set[str] = set()
    missing_or_stale_roles: set[str] = set()
    roles_with_ok_sources: set[str] = set()
    roles_without_ok_sources: set[str] = set()
    role_evidence_quality: dict[str, str] = {}
    role_action_readiness: dict[str, str] = {}
    role_native_evidence_gap_map: dict[str, list[str]] = {}
    role_action_blocker_map: dict[str, list[str]] = {}
    for index, output in enumerate(outputs):
        output_errors = validate_external_agent_output(output, index)
        errors.extend(output_errors)
        agent_id = output.get("agent_id") if isinstance(output, dict) else None
        if agent_id:
            role_counts[agent_id] = role_counts.get(agent_id, 0) + 1
        if agent_id in ROLE_IDS:
            data_quality = output.get("data_quality")
            if data_quality in {"verified", "degraded"}:
                successful_roles.add(agent_id)
            if data_quality == "verified":
                verified_roles.add(agent_id)
            if data_quality == "degraded":
                degraded_roles.add(agent_id)
            if data_quality in {"missing", "stale", "disputed"}:
                missing_or_stale_roles.add(agent_id)
            counts = source_counts(output)
            if counts["ok"] > 0:
                roles_with_ok_sources.add(agent_id)
            else:
                roles_without_ok_sources.add(agent_id)
            evidence_quality = role_evidence_quality_status(output, output_errors)
            action_readiness = role_action_readiness_status(output)
            role_evidence_quality[agent_id] = evidence_quality
            role_action_readiness[agent_id] = action_readiness
            native_gaps = role_native_evidence_gaps(output)
            action_blockers = role_action_blockers(output)
            role_native_evidence_gap_map[agent_id] = native_gaps
            role_action_blocker_map[agent_id] = action_blockers
            if evidence_quality == "verified":
                evidence_verified_roles.add(agent_id)
            else:
                evidence_incomplete_roles.add(agent_id)
            if action_readiness in {"ready_for_role_max_action", "watch_or_paper_ready"}:
                action_ready_roles.add(agent_id)
            else:
                action_not_ready_roles.add(agent_id)

    duplicates = sorted(role for role, count in role_counts.items() if count > 1)
    if duplicates:
        errors.append(f"Duplicate external agent roles are not allowed: {duplicates}")

    unique_known_roles = sorted(role for role in role_counts if role in ROLE_IDS)
    missing_required_external_roles = sorted(DEFAULT_REQUIRED_EXTERNAL_ROLE_IDS - set(unique_known_roles))
    evidence_quality_gate_passed = (
        len(successful_roles) >= 6
        and len(evidence_verified_roles) >= 6
        and len(roles_with_ok_sources) >= 6
        and not missing_required_external_roles
        and not errors
    )
    committee_quality_gate_passed = (
        len(successful_roles) >= 6
        and evidence_quality_gate_passed
        and len(roles_with_ok_sources) >= 6
        and not missing_required_external_roles
    )
    return {
        "valid": not errors,
        "errors": errors,
        "unique_known_role_count": len(unique_known_roles),
        "unique_known_roles": unique_known_roles,
        "successful_known_role_count": len(successful_roles),
        "successful_known_roles": sorted(successful_roles),
        "verified_known_role_count": len(verified_roles),
        "verified_known_roles": sorted(verified_roles),
        "evidence_verified_known_role_count": len(evidence_verified_roles),
        "evidence_verified_known_roles": sorted(evidence_verified_roles),
        "role_evidence_quality": role_evidence_quality,
        "evidence_incomplete_known_role_count": len(evidence_incomplete_roles),
        "evidence_incomplete_known_roles": sorted(evidence_incomplete_roles),
        "role_native_evidence_gaps": role_native_evidence_gap_map,
        "action_ready_known_role_count": len(action_ready_roles),
        "action_ready_known_roles": sorted(action_ready_roles),
        "action_not_ready_known_role_count": len(action_not_ready_roles),
        "action_not_ready_known_roles": sorted(action_not_ready_roles),
        "role_action_readiness": role_action_readiness,
        "role_action_blockers": role_action_blocker_map,
        "evidence_quality_gate_passed": evidence_quality_gate_passed,
        "degraded_role_count": len(degraded_roles),
        "degraded_roles": sorted(degraded_roles),
        "missing_or_stale_role_count": len(missing_or_stale_roles),
        "missing_or_stale_roles": sorted(missing_or_stale_roles),
        "roles_with_ok_source_count": len(roles_with_ok_sources),
        "roles_with_ok_sources": sorted(roles_with_ok_sources),
        "roles_without_ok_sources": sorted(roles_without_ok_sources),
        "required_external_roles": sorted(DEFAULT_REQUIRED_EXTERNAL_ROLE_IDS),
        "missing_required_external_roles": missing_required_external_roles,
        "all_required_external_roles_present": not missing_required_external_roles,
        "committee_quality_gate_passed": committee_quality_gate_passed,
        "committee_quality_gate_requirements": {
            "min_successful_known_roles": 6,
            "min_evidence_verified_known_roles": 6,
            "min_roles_with_ok_sources": 6,
            "all_required_external_roles_present": True,
            "note": "evidence quality is separate from action readiness; roles may be evidence-verified while still blocking execute_now because they found real missing data or failed gates",
        },
    }


def build_votes(agent_outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    votes = []
    for output in agent_outputs:
        max_action = output.get("recommended_max_action") or "watch"
        vote = "watch"
        if max_action in {"block", "no_deploy", "risk_alert"}:
            vote = "block"
        elif max_action in {"conditional_action", "hold", "trim_review"}:
            vote = "hold"
        elif max_action == "paper_only":
            vote = "watch"
        votes.append({
            "agent_id": output.get("agent_id"),
            "asset": ",".join(output.get("asset_scope") or []),
            "vote": vote,
            "max_action": max_action,
            "reason": (output.get("signals") or [{"summary": "no signal"}])[0].get("summary", "no signal"),
        })
    return votes


def panel_max_action(agent_outputs: list[dict[str, Any]], degraded: bool) -> str:
    if degraded:
        return "watch"
    if not agent_outputs:
        return "watch"
    if any(output.get("recommended_max_action") in {"block", "no_deploy"} for output in agent_outputs):
        return "no_deploy"
    if any(output.get("recommended_max_action") == "risk_alert" for output in agent_outputs):
        return "risk_alert"
    if any(output.get("data_quality") in {"disputed", "stale", "missing"} for output in agent_outputs):
        return "watch"

    priority = {
        "watch": 0,
        "paper_only": 1,
        "hold": 2,
        "trim_review": 2,
        "conditional_action": 3,
        "execute_now": 4,
    }
    normalized = [
        action
        for action in (output.get("recommended_max_action") or "watch" for output in agent_outputs)
        if action in priority
    ]
    if not normalized:
        return "watch"
    most_restrictive = min(normalized, key=lambda action: priority[action])
    return "conditional_action" if most_restrictive == "execute_now" else most_restrictive


def derive_prior_thesis_status(agent_outputs: list[dict[str, Any]], degraded: bool) -> str:
    prior = next((item for item in agent_outputs if item.get("agent_id") == "prior_thesis_challenge_agent"), None)
    if prior is None:
        return "insufficient_data"
    if prior.get("data_quality") in {"missing", "stale", "disputed"}:
        return "insufficient_data"
    failed = prior.get("failed_gates") or []
    missing = prior.get("missing_data") or []
    if any("invalid" in str(item) or "thesis_broken" in str(item) for item in failed):
        return "invalidated"
    if degraded or failed or missing:
        return "weakened"
    return "confirmed"


def old_thesis_reuse_allowed(
    prior_status: str,
    agent_outputs: list[dict[str, Any]],
    disconfirming_evidence: list[str],
) -> bool:
    if prior_status != "confirmed":
        return False
    if disconfirming_evidence:
        return False
    if any(output.get("data_quality") in {"disputed", "stale", "missing"} for output in agent_outputs):
        return False
    if any(output.get("recommended_max_action") in {"risk_alert", "no_deploy", "block"} for output in agent_outputs):
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a research_panel JSON")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--daily-context-json", required=True)
    parser.add_argument("--crypto-snapshot-json")
    parser.add_argument("--us-scanner-json")
    parser.add_argument("--external-agent-outputs-json", help="Optional real subagent output list")
    parser.add_argument("--output", help="Optional path to save the research_panel JSON")
    parser.add_argument("--format", choices=["json"], default="json")
    args = parser.parse_args()

    context = load_json(args.daily_context_json)
    crypto_snapshot = load_json(args.crypto_snapshot_json)
    us_snapshot = load_json(args.us_scanner_json)
    external_load_warning = None
    try:
        external_outputs = load_external_agent_outputs(args.external_agent_outputs_json)
        external_validation = validate_external_agent_outputs(external_outputs)
    except Exception as exc:  # noqa: BLE001
        external_outputs = []
        external_validation = validate_external_agent_outputs([])
        external_load_warning = f"external subagent outputs could not be loaded: {exc}"
    if external_outputs and not external_validation["valid"]:
        external_load_warning = "invalid external subagent outputs: " + "; ".join(external_validation["errors"])
        external_outputs = []

    local_outputs = [
        goal_role(context),
        portfolio_role(context),
        macro_role(context),
        crypto_role(crypto_snapshot),
        onchain_role(crypto_snapshot),
        social_role(context),
        us_equity_role(us_snapshot, context),
        backtest_role(context),
    ]
    for output in local_outputs:
        output["origin"] = "local_fallback"
    external_by_id = {item.get("agent_id"): item for item in external_outputs if item.get("agent_id")}
    agent_outputs = [external_by_id.get(item["agent_id"], item) for item in local_outputs]
    external_roles_used = sorted(
        output.get("agent_id")
        for output in agent_outputs
        if output.get("origin") == "external_subagent" and output.get("agent_id") in ROLE_IDS
    )
    local_fallback_roles = sorted(
        output.get("agent_id")
        for output in agent_outputs
        if output.get("origin") != "external_subagent" and output.get("agent_id") in ROLE_IDS
    )
    missing_external_roles = sorted(DEFAULT_REQUIRED_EXTERNAL_ROLE_IDS - set(external_roles_used))
    successful_roles = [item for item in agent_outputs if item.get("data_quality") != "missing"]
    has_real_external_committee = (
        external_validation["valid"]
        and external_validation["unique_known_role_count"] >= 6
        and external_validation.get("successful_known_role_count", 0) >= 6
        and not external_validation.get("missing_required_external_roles")
    )
    all_external_roles_degraded = (
        has_real_external_committee
        and external_validation.get("degraded_role_count", 0) >= external_validation.get("successful_known_role_count", 0)
    )
    quality_gate_failed = has_real_external_committee and not external_validation.get("committee_quality_gate_passed")
    degraded = (not has_real_external_committee) or len(successful_roles) < 6 or all_external_roles_degraded or quality_gate_failed

    missing_data = []
    for output in agent_outputs:
        for item in output.get("missing_data") or []:
            if item not in missing_data:
                missing_data.append(item)

    disconfirming_evidence = []
    for output in agent_outputs:
        for item in output.get("failed_gates") or []:
            text = str(item)
            if text not in disconfirming_evidence:
                disconfirming_evidence.append(text)
    if not disconfirming_evidence:
        disconfirming_evidence = [
            "No US equity dynamic candidate passed the sample-tier, conservative-EV, realtime-signal, and account-risk gates.",
            "Crypto multisource alpha scores do not support aggressive immediate deployment.",
            "Recommendation history has pending but not resolved evidence.",
        ]
    prior_status = derive_prior_thesis_status(agent_outputs, degraded)
    old_reuse_allowed = old_thesis_reuse_allowed(prior_status, agent_outputs, disconfirming_evidence)

    panel = {
        "run_id": args.run_id,
        "generated_at": utc_now(),
        "research_method": "external_subagent_outputs" if has_real_external_committee else "local_degraded_runner",
        "external_agent_validation": external_validation,
        "external_roles_used": external_roles_used,
        "local_fallback_roles": local_fallback_roles,
        "missing_external_roles": missing_external_roles,
        "required_external_roles": sorted(DEFAULT_REQUIRED_EXTERNAL_ROLE_IDS),
        "agent_outputs": agent_outputs,
        "researcher_votes": build_votes(agent_outputs),
        "bull_case": next(
            (
                signal.get("summary")
                for output in agent_outputs
                for signal in output.get("signals", [])
                if signal.get("direction") == "bullish" and signal.get("summary")
            ),
            "SOL-led DCA plus disciplined US tactical relay can improve 10y 10x odds if liquidity and trend improve.",
        ),
        "base_case": next(
            (
                signal.get("summary")
                for output in agent_outputs
                for signal in output.get("signals", [])
                if signal.get("direction") == "neutral" and signal.get("summary")
            ),
            "Continue split crypto DCA, keep ETH/lcETH no-add due concentration, and use tactical positions only after evidence gates pass.",
        ),
        "bear_case": next(
            (
                signal.get("summary")
                for output in agent_outputs
                for signal in output.get("signals", [])
                if signal.get("direction") == "bearish" and signal.get("summary")
            ),
            "Macro/liquidity weakens, concentration drags, tactical candidates fail calibrated EV/risk gates, and pending recommendations lack evidence.",
        ),
        "disconfirming_evidence": disconfirming_evidence,
        "prior_thesis_status": prior_status,
        "arbiter_decision": (
            "No execute_now. Use the most conservative common action from the research roles; allow conditional DCA or tactical relay "
            "only after trigger prices, data checks, sample-tier/EV/signal/risk gates, and human confirmation."
        ),
        "old_thesis_reuse_allowed": old_reuse_allowed,
        "missing_data_summary": missing_data,
        "max_allowed_action": panel_max_action(agent_outputs, degraded),
        "research_committee_degraded": degraded,
        "research_panel_missing_reason": None if not degraded else (
            (external_load_warning + "; ") if external_load_warning else ""
        ) + (
            "No valid external 6+ successful subagent output file was supplied, quality gate failed, or all external roles were degraded; "
            "panel blocks execute_now."
        ),
    }
    payload = json.dumps(panel, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
