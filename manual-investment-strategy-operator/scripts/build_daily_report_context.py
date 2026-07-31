#!/usr/bin/env python3
"""Build a daily manual-report context package.

This script aggregates current portfolio snapshot, 10x goal projection,
recommendation history, and recent active-alpha handoffs into one JSON payload.
It does not fetch data by itself unless a snapshot is already provided by
portfolio_comparison_snapshot.py. Networked fetching should remain explicit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
ACTIVE_ROOT = ROOT / "active-alpha-paper-monitor"
DEFAULT_RECOMMENDATION_LEDGER = MANUAL_ROOT / "recommendations" / "recommendation_history.json"
DEFAULT_HANDOFF_DIR = ACTIVE_ROOT / "handoffs"
DEFAULT_US_TACTICAL_PERFORMANCE_LEDGER = MANUAL_ROOT / "performance" / "us_tactical_performance.json"
DEFAULT_PORTFOLIO_LEDGER_CANDIDATES = [
    ROOT / "unified-longterm-alpha-investor" / "config" / "portfolio_ledger.json",
    Path.cwd() / "unified-longterm-alpha-investor" / "config" / "portfolio_ledger.json",
    Path.home() / "Documents" / "investing" / "unified-longterm-alpha-investor" / "config" / "portfolio_ledger.json",
]
DEFAULT_PORTFOLIO_LEDGER = next(
    (path for path in DEFAULT_PORTFOLIO_LEDGER_CANDIDATES if path.exists()),
    DEFAULT_PORTFOLIO_LEDGER_CANDIDATES[0],
)

REQUIRED_RESEARCH_PANEL_FIELDS = {
    "run_id",
    "agent_outputs",
    "researcher_votes",
    "bull_case",
    "bear_case",
    "base_case",
    "disconfirming_evidence",
    "prior_thesis_status",
    "arbiter_decision",
    "old_thesis_reuse_allowed",
    "missing_data_summary",
    "max_allowed_action",
    "research_committee_degraded",
    "research_panel_missing_reason",
    "research_method",
    "external_agent_validation",
    "external_roles_used",
    "local_fallback_roles",
    "missing_external_roles",
    "required_external_roles",
}

MIN_RESEARCH_AGENT_OUTPUTS = 6
REQUIRED_EXTERNAL_RESEARCH_ROLES = {
    "prior_thesis_challenge_agent",
    "portfolio_state_agent",
    "macro_regime_agent",
    "crypto_market_agent",
    "onchain_defi_agent",
    "social_news_agent",
    "us_equity_alpha_agent",
    "backtest_validation_agent",
}
ACTION_RANK = {
    "block": 0,
    "no_deploy": 1,
    "no_action": 1,
    "hold": 2,
    "watch": 2,
    "paper_only": 3,
    "conditional_action_or_watch": 4,
    "conditional_action": 5,
    "small_probe_review": 6,
    "execute_now": 7,
}

PRICE_SCENARIO_MULTIPLIERS = {
    "BTC": {
        "5y_survival": (0.8, 1.4),
        "5y_base": (1.2, 2.2),
        "5y_bull": (2.2, 3.5),
        "10y_base": (1.6, 3.0),
        "10y_bull": (3.0, 5.0),
        "label": "quality_hold",
        "confidence": "medium",
    },
    "ETH": {
        "5y_survival": (1.25, 2.0),
        "5y_base": (2.0, 4.0),
        "5y_bull": (5.0, 7.0),
        "10y_base": (3.0, 6.0),
        "10y_bull": (7.5, 12.5),
        "label": "quality_hold",
        "confidence": "medium",
    },
    "SOL": {
        "5y_survival": (1.4, 2.2),
        "5y_base": (2.2, 6.0),
        "5y_bull": (6.0, 10.0),
        "10y_base": (3.5, 14.5),
        "10y_bull": (10.0, 22.0),
        "label": "strong_engine",
        "confidence": "medium",
    },
    "ADA": {
        "5y_survival": (1.4, 2.5),
        "5y_base": (2.5, 10.5),
        "5y_bull": (8.0, 18.0),
        "10y_base": (5.0, 21.0),
        "10y_bull": (12.0, 35.0),
        "label": "satellite",
        "confidence": "medium",
    },
    "NIGHT": {
        "5y_survival": (0.4, 1.5),
        "5y_base": (3.0, 20.0),
        "5y_bull": (15.0, 40.0),
        "10y_base": (7.0, 65.0),
        "10y_bull": (25.0, 120.0),
        "label": "tail_convexity",
        "confidence": "low",
    },
    "SUI": {
        "5y_survival": (1.0, 2.0),
        "5y_base": (2.5, 8.5),
        "5y_bull": (7.0, 16.0),
        "10y_base": (5.0, 20.0),
        "10y_bull": (15.0, 45.0),
        "label": "satellite",
        "confidence": "low",
    },
    "LINK": {
        "5y_survival": (1.2, 2.0),
        "5y_base": (2.5, 8.5),
        "5y_bull": (7.0, 14.0),
        "10y_base": (5.0, 18.0),
        "10y_bull": (12.0, 30.0),
        "label": "satellite",
        "confidence": "medium",
    },
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path | str) -> Any:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def run_json(cmd: list[str]) -> Any:
    output = subprocess.check_output(cmd, text=True)
    return json.loads(output)


def summarize_portfolio(snapshot: dict[str, Any]) -> dict[str, Any]:
    totals = snapshot.get("totals", {})
    current = totals.get("current", {})
    holdings = snapshot.get("holdings", [])
    top_holdings = sorted(
        [
            {
                "symbol": item.get("symbol"),
                "display": item.get("display"),
                "rail": item.get("rail"),
                "quantity": item.get("quantity"),
                "current_value": (item.get("values") or {}).get("current"),
                "current_weight_pct": (item.get("weights") or {}).get("current"),
                "data_quality": item.get("data_quality"),
            }
            for item in holdings
        ],
        key=lambda item: item["current_value"] or 0,
        reverse=True,
    )
    return {
        "generated_at": snapshot.get("generated_at"),
        "total_value": current.get("total_value"),
        "crypto_value": current.get("crypto_value"),
        "us_equity_value": current.get("us_equity_value"),
        "cash_or_stablecoin": current.get("cash_or_stablecoin"),
        "comparisons": snapshot.get("comparisons", {}),
        "top_holdings": top_holdings,
        "data_quality_summary": snapshot.get("data_quality_summary", {}),
        "valuation_policy": snapshot.get("valuation_policy"),
    }


def recommendation_summary(ledger_path: Path, as_of: str | None = None) -> dict[str, Any]:
    script = MANUAL_ROOT / "scripts" / "recommendation_history.py"
    if not ledger_path.exists():
        return {
            "ledger_path": str(ledger_path),
            "status": "missing",
            "summary": None,
            "pending_recommendations": [],
        }
    cmd = [sys.executable, str(script), "--path", str(ledger_path), "export-panel", "--format", "json"]
    if as_of:
        cmd.extend(["--as-of", as_of])
    panel = run_json(cmd)
    ledger = load_json(ledger_path)
    pending = [
        {
            "recommendation_id": item.get("recommendation_id"),
            "symbol": item.get("symbol"),
            "asset_class": item.get("asset_class"),
            "action": item.get("action"),
            "time_window": item.get("time_window"),
            "generated_at": item.get("generated_at"),
            "outcome_status": item.get("outcome_status"),
            "risk_decision": item.get("risk_decision"),
        }
        for item in ledger.get("recommendations", [])
        if item.get("outcome_status") == "pending"
    ]
    return {
        "ledger_path": str(ledger_path),
        "status": "ok",
        "summary": panel.get("summary"),
        "due_review": panel.get("due_review", []),
        "calibration": panel.get("calibration", {}),
        "pending_proposed_changes": panel.get("pending_proposed_changes", []),
        "pending_recommendations": pending,
    }


def us_tactical_performance_summary(
    snapshot_path: Path,
    ledger_path: Path,
    disabled: bool = False,
) -> dict[str, Any]:
    if disabled:
        return {
            "ledger_path": str(ledger_path),
            "status": "disabled",
            "summary": None,
        }
    script = MANUAL_ROOT / "scripts" / "us_tactical_performance_tracker.py"
    if not script.exists():
        return {
            "ledger_path": str(ledger_path),
            "status": "script_missing",
            "summary": None,
        }
    if not ledger_path.exists():
        return {
            "ledger_path": str(ledger_path),
            "status": "ledger_missing",
            "summary": None,
        }
    try:
        summary = run_json([
            sys.executable,
            str(script),
            "--path",
            str(ledger_path),
            "summary",
            "--snapshot-json",
            str(snapshot_path),
            "--format",
            "json",
        ])
    except Exception as exc:  # noqa: BLE001
        return {
            "ledger_path": str(ledger_path),
            "status": "error",
            "error": str(exc),
            "summary": None,
        }
    return {
        "ledger_path": str(ledger_path),
        "status": summary.get("status", "ok"),
        "summary": summary,
    }


def cost_basis_reconciliation_summary(ledger_path: Path, disabled: bool = False) -> dict[str, Any]:
    if disabled:
        return {
            "ledger_path": str(ledger_path),
            "status": "disabled",
            "summary": None,
        }
    script = MANUAL_ROOT / "scripts" / "cost_basis_reconciliation_audit.py"
    if not script.exists():
        return {
            "ledger_path": str(ledger_path),
            "status": "script_missing",
            "summary": None,
        }
    if not ledger_path.exists():
        return {
            "ledger_path": str(ledger_path),
            "status": "ledger_missing",
            "summary": None,
        }
    try:
        summary = run_json([
            sys.executable,
            str(script),
            "--ledger",
            str(ledger_path),
            "--format",
            "json",
        ])
    except Exception as exc:  # noqa: BLE001
        return {
            "ledger_path": str(ledger_path),
            "status": "error",
            "error": str(exc),
            "summary": None,
        }
    return {
        "ledger_path": str(ledger_path),
        "status": summary.get("status", "ok"),
        "summary": summary,
    }


def portfolio_evidence_gap_summary(
    ledger_path: Path,
    disabled: bool = False,
) -> dict[str, Any]:
    if disabled:
        return {
            "ledger_path": str(ledger_path),
            "status": "disabled",
            "summary": None,
        }
    script = MANUAL_ROOT / "scripts" / "portfolio_evidence_gap_packager.py"
    if not script.exists():
        return {
            "ledger_path": str(ledger_path),
            "status": "script_missing",
            "summary": None,
        }
    if not ledger_path.exists():
        return {
            "ledger_path": str(ledger_path),
            "status": "ledger_missing",
            "summary": None,
        }
    try:
        summary = run_json([
            sys.executable,
            str(script),
            "--ledger",
            str(ledger_path),
            "--format",
            "json",
        ])
    except Exception as exc:  # noqa: BLE001
        return {
            "ledger_path": str(ledger_path),
            "status": "error",
            "error": str(exc),
            "summary": None,
        }
    return {
        "ledger_path": str(ledger_path),
        "status": summary.get("status", "ok"),
        "summary": summary,
    }


def macro_regime_summary(path: str | None) -> dict[str, Any]:
    if not path:
        return {
            "status": "missing",
            "summary": None,
            "reason": "No macro regime snapshot was supplied. Run scripts/macro_regime_snapshot.py before building a full report.",
        }
    try:
        summary = load_json(path)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "unreadable",
            "path": path,
            "summary": None,
            "error": str(exc),
        }
    return {
        "status": summary.get("data_quality", "ok"),
        "path": path,
        "summary": summary,
    }


def asset_goal_contribution_summary(path: str | None) -> dict[str, Any]:
    if not path:
        return {
            "status": "missing",
            "summary": None,
            "reason": "No asset goal contribution panel was supplied. Run scripts/asset_goal_contribution.py for full DCA goal alignment.",
        }
    try:
        summary = load_json(path)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "unreadable",
            "path": path,
            "summary": None,
            "error": str(exc),
        }
    return {
        "status": summary.get("data_quality", "ok"),
        "path": path,
        "summary": summary,
    }


def asset_goal_has_timing_model(asset_goal: dict[str, Any]) -> bool:
    summary = asset_goal.get("summary") or {}
    guidance = summary.get("dca_guidance") or {}
    return bool(guidance.get("long_horizon_timing_decisions"))


def crypto_market_summary(path: str | None) -> dict[str, Any]:
    if not path:
        return {"status": "missing", "summary": None}
    try:
        payload = load_json(path)
    except Exception as exc:  # noqa: BLE001
        return {"status": "unreadable", "path": path, "summary": None, "error": str(exc)}

    sources = payload.get("sources") or {}
    spot = sources.get("binance_spot_24hr") or {}
    derivatives = sources.get("binance_derivatives") or {}
    assets: list[dict[str, Any]] = []
    for symbol, wrapped in sorted(spot.items()):
        data = (wrapped or {}).get("data") or {}
        bid = as_float(data.get("bidPrice"))
        ask = as_float(data.get("askPrice"))
        last = as_float(data.get("lastPrice"))
        spread_bps = None
        if bid is not None and ask is not None and last:
            spread_bps = round(((ask - bid) / last) * 10000, 4)
        derivative = derivatives.get(symbol) or {}
        premium = ((derivative.get("premium_index") or {}).get("data") or {})
        oi = ((derivative.get("open_interest") or {}).get("data") or {})
        assets.append({
            "symbol": symbol,
            "last_price": last,
            "change_24h_pct": as_float(data.get("priceChangePercent")),
            "high_24h": as_float(data.get("highPrice")),
            "low_24h": as_float(data.get("lowPrice")),
            "quote_volume_24h_usd": as_float(data.get("quoteVolume")),
            "bid": bid,
            "ask": ask,
            "spread_bps": spread_bps,
            "funding_rate": as_float(premium.get("lastFundingRate")),
            "open_interest_contracts": as_float(oi.get("openInterest")),
        })

    fear_greed_items = (((sources.get("fear_greed") or {}).get("data") or {}).get("data") or [])
    fear_greed = fear_greed_items[0] if fear_greed_items else {}
    fundamentals: dict[str, dict[str, Any]] = {}
    for item in (((sources.get("coingecko_markets") or {}).get("data")) or []):
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        fundamentals[symbol] = {
            "id": item.get("id"),
            "name": item.get("name"),
            "current_price": as_float(item.get("current_price")),
            "market_cap": as_float(item.get("market_cap")),
            "fully_diluted_valuation": as_float(item.get("fully_diluted_valuation")),
            "circulating_supply": as_float(item.get("circulating_supply")),
            "total_supply": as_float(item.get("total_supply")),
            "max_supply": as_float(item.get("max_supply")),
            "ath": as_float(item.get("ath")),
            "ath_change_percentage": as_float(item.get("ath_change_percentage")),
            "last_updated": item.get("last_updated"),
        }
    return {
        "status": "ok" if assets else "degraded",
        "path": path,
        "summary": {
            "generated_at": payload.get("generated_at"),
            "private_api_keys_used": payload.get("private_api_keys_used"),
            "live_orders_enabled": payload.get("live_orders_enabled"),
            "fear_greed": {
                "value": fear_greed.get("value"),
                "classification": fear_greed.get("value_classification"),
            },
            "assets": assets,
            "market_fundamentals": fundamentals,
            "asset_scores": payload.get("asset_scores") or [],
        },
    }


def us_open_scanner_summary(path: str | None) -> dict[str, Any]:
    if not path:
        return {"status": "missing", "summary": None}
    try:
        payload = load_json(path)
    except Exception as exc:  # noqa: BLE001
        return {"status": "unreadable", "path": path, "summary": None, "error": str(exc)}

    candidates: list[dict[str, Any]] = []
    for item in payload.get("candidates") or []:
        candidates.append({
            "symbol": item.get("symbol") or item.get("candidate_symbol"),
            "price": item.get("price"),
            "entry_zone": item.get("entry_zone"),
            "target_price": item.get("target_price"),
            "target_time_window": item.get("target_time_window"),
            "stop_loss": item.get("stop_loss"),
            "latest_exit_date": item.get("latest_exit_date"),
            "forecast_probability_pct": item.get("forecast_probability_pct"),
            "execution_readiness_score": item.get("execution_readiness_score"),
            "monitor_recommendation": item.get("monitor_recommendation"),
            "data_quality_status": item.get("data_quality_status"),
            "why_better_than_current_tactical_position": item.get("why_better_than_current_tactical_position"),
        })
    return {
        "status": payload.get("scan_status") or "ok",
        "path": path,
        "summary": {
            "created_at": payload.get("created_at"),
            "scan_status": payload.get("scan_status"),
            "degraded_no_actionable_trade": payload.get("degraded_no_actionable_trade"),
            "deployable_tactical_position": payload.get("deployable_tactical_position") or payload.get("current_tactical_position"),
            "max_allowed_action": payload.get("max_allowed_action"),
            "research_panel_missing": payload.get("research_panel_missing"),
            "research_panel_missing_reason": payload.get("research_panel_missing_reason"),
            "tactical_rotation_relay": payload.get("tactical_rotation_relay"),
            "candidates": candidates,
            "errors": payload.get("errors") or [],
            "failed_core_sources": payload.get("failed_core_sources") or [],
        },
    }


def build_asset_goal_contribution(
    snapshot_path: Path,
    goal_projection_path: str | None,
    ledger_path: str,
    monthly_dca: float,
    disabled: bool = False,
) -> dict[str, Any]:
    if disabled:
        return {"status": "disabled", "summary": None}
    script = MANUAL_ROOT / "scripts" / "asset_goal_contribution.py"
    if not script.exists():
        return {"status": "script_missing", "summary": None}
    try:
        cmd = [
            sys.executable,
            str(script),
            "--portfolio-snapshot-json",
            str(snapshot_path),
            "--ledger-json",
            ledger_path,
            "--monthly-dca",
            str(monthly_dca),
            "--format",
            "json",
        ]
        if goal_projection_path:
            cmd.extend(["--goal-projection-json", goal_projection_path])
        summary = run_json(cmd)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "error": str(exc),
            "summary": None,
        }
    return {
        "status": summary.get("data_quality", "ok"),
        "summary": summary,
    }


def price_range(current_price: float | None, multipliers: tuple[float, float]) -> list[float] | None:
    if current_price is None:
        return None
    low, high = multipliers
    return [round(current_price * low, 6), round(current_price * high, 6)]


def goal_fit_label(symbol: str, asset: dict[str, Any], current_weight_pct: float | None) -> str:
    default_label = PRICE_SCENARIO_MULTIPLIERS.get(symbol, {}).get("label")
    contribution = asset.get("goal_gap_contribution")
    implication = str(asset.get("dca_weight_implication") or asset.get("action_implication") or "")
    if symbol == "ETH" and (current_weight_pct or 0.0) >= 30:
        return "drag_for_new_dca"
    if symbol == "ADA":
        return "satellite"
    if symbol == "NIGHT":
        return "tail_convexity"
    if contribution == "accelerator":
        return "strong_engine"
    if contribution == "tail_convexity":
        return "tail_convexity"
    if "avoid_new_dca" in implication or contribution == "drag":
        return "drag_for_new_dca"
    if default_label:
        return str(default_label)
    return "satellite"


def dca_implication_from_asset(symbol: str, asset: dict[str, Any], label: str) -> str:
    implication = str(asset.get("dca_weight_implication") or asset.get("action_implication") or "")
    contribution = asset.get("goal_gap_contribution")
    if label == "strong_engine":
        return "increase"
    if symbol == "ETH" and label == "drag_for_new_dca":
        return "hold_only_or_no_new_dca"
    if "avoid_new_dca" in implication or "reduce_new" in implication:
        return "no_new_dca"
    if label == "tail_convexity":
        return "small_dca_only_or_watch"
    if label == "satellite" and (contribution == "accelerator" or "increase" in implication):
        return "increase"
    if label == "satellite":
        return "small_add_or_maintain"
    return "hold_only"


def staking_adjusted_note(asset: dict[str, Any]) -> str:
    staking = asset.get("staking_compounding_summary") or {}
    apy = staking.get("apy_used_pct")
    five = staking.get("five_year_compounding_multiplier")
    ten = staking.get("ten_year_compounding_multiplier")
    five_required = staking.get("five_year_price_multiple_required_for_10x_after_staking")
    ten_required = staking.get("ten_year_price_multiple_required_for_10x_after_staking")
    if apy is None:
        return "No verified staking APY in this context; price appreciation must carry the scenario."
    return (
        f"APY {apy}% gives about {five}x over 5y and {ten}x over 10y; "
        f"after staking, 10x still needs about {five_required}x / {ten_required}x price multiple."
    )


def build_long_term_price_scenario_panel(
    asset_goal_contribution: dict[str, Any],
    crypto_market: dict[str, Any],
) -> dict[str, Any]:
    asset_goal = asset_goal_contribution.get("summary") or {}
    crypto_summary = crypto_market.get("summary") or {}
    fundamentals = crypto_summary.get("market_fundamentals") or {}
    market_assets = {
        str(item.get("symbol") or "").replace("USDT", ""): item
        for item in crypto_summary.get("assets") or []
        if item.get("symbol")
    }
    rows: list[dict[str, Any]] = []
    panel_missing: list[str] = []
    for asset in asset_goal.get("assets") or []:
        symbol = str(asset.get("symbol") or "").upper()
        if not symbol or symbol == "USDT":
            continue
        if asset.get("rail") != "crypto":
            continue
        profile = PRICE_SCENARIO_MULTIPLIERS.get(symbol, {
            "5y_survival": (0.8, 1.8),
            "5y_base": (1.5, 4.0),
            "5y_bull": (4.0, 8.0),
            "10y_base": (2.0, 8.0),
            "10y_bull": (6.0, 15.0),
            "label": "satellite",
            "confidence": "low",
        })
        cg = fundamentals.get(symbol) or {}
        market = market_assets.get(symbol) or {}
        current_price = as_float(cg.get("current_price")) or as_float(market.get("last_price"))
        current_weight_pct = as_float(asset.get("current_weight_pct"))
        label = goal_fit_label(symbol, asset, current_weight_pct)
        if symbol == "ETH" and label == "drag_for_new_dca":
            label_note = "quality_hold + drag_for_new_dca"
        else:
            label_note = label
        downgrade_reasons: list[str] = []
        if current_price is None:
            downgrade_reasons.append("current_price_missing")
        if cg.get("market_cap") is None:
            downgrade_reasons.append("market_cap_missing")
        if cg.get("fully_diluted_valuation") is None:
            downgrade_reasons.append("fdv_missing")
        if symbol == "NIGHT":
            downgrade_reasons.append("supply_and_unlock_need_official_cross_check")
        downgrade_reasons.extend(asset.get("missing_data") or [])
        confidence = profile.get("confidence", "low")
        if downgrade_reasons and confidence == "medium":
            confidence = "low_to_medium"
        if downgrade_reasons:
            panel_missing.extend([f"{symbol}: {reason}" for reason in downgrade_reasons[:3]])
        rows.append({
            "symbol": symbol,
            "current_price": current_price,
            "current_market_cap": cg.get("market_cap"),
            "current_fdv": cg.get("fully_diluted_valuation"),
            "supply_basis": {
                "circulating_supply": cg.get("circulating_supply"),
                "total_supply": cg.get("total_supply"),
                "max_supply": cg.get("max_supply"),
                "source": "CoinGecko markets" if cg else "market_cap_missing",
            },
            "five_year_survival_price_range": price_range(current_price, profile["5y_survival"]),
            "five_year_base_price_range": price_range(current_price, profile["5y_base"]),
            "five_year_bull_price_range": price_range(current_price, profile["5y_bull"]),
            "ten_year_base_price_range": price_range(current_price, profile["10y_base"]),
            "ten_year_bull_price_range": price_range(current_price, profile["10y_bull"]),
            "staking_adjusted_return_note": staking_adjusted_note(asset),
            "ten_x_goal_fit": label_note,
            "dca_implication": dca_implication_from_asset(symbol, asset, label),
            "confidence_band": confidence,
            "downgrade_reasons": downgrade_reasons,
            "scenario_degraded": bool(downgrade_reasons),
            "source_updated_at": cg.get("last_updated") or crypto_summary.get("generated_at"),
        })
    status = "ok" if rows and not any(item.get("scenario_degraded") for item in rows) else "degraded" if rows else "missing"
    return {
        "status": status,
        "summary": {
            "generated_at": utc_now(),
            "method": "scenario_ranges_from_current_price_market_cap_supply_staking_and_goal_role",
            "not_a_price_target_or_guarantee": True,
            "assets": rows,
            "missing_data": panel_missing,
            "max_allowed_action": "conditional_action" if status == "ok" else "watch_or_smaller_size",
        },
    }


def latest_handoff_paths(handoff_dir: Path, limit: int) -> list[Path]:
    if not handoff_dir.exists():
        return []
    paths = sorted(
        handoff_dir.glob("*handoff*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return paths[:limit]


def normalize_handoff(path: Path) -> dict[str, Any]:
    try:
        payload = load_json(path)
    except Exception as exc:  # noqa: BLE001
        return {"path": str(path), "status": "unreadable", "error": str(exc)}

    candidate = payload.get("selected_candidate") or {}
    opened = payload.get("opened_position") or {}
    research_missing = bool(payload.get("research_panel_missing") or not payload.get("research_panel"))
    max_allowed = (
        payload.get("max_allowed_action")
        or (payload.get("research_panel") or {}).get("max_allowed_action")
        or payload.get("monitor_recommendation")
        or "watch"
    )
    if research_missing and max_allowed in {"execute_now", "small_probe_review"}:
        max_allowed = "watch"

    return {
        "path": str(path),
        "handoff_id": payload.get("handoff_id"),
        "created_at": payload.get("created_at"),
        "candidate_type": payload.get("candidate_type"),
        "asset_class": payload.get("asset_class"),
        "symbol": payload.get("symbol") or candidate.get("symbol") or opened.get("symbol"),
        "monitor_recommendation": payload.get("monitor_recommendation"),
        "max_allowed_action": max_allowed,
        "research_panel_missing": research_missing,
        "research_committee_degraded": bool(payload.get("research_committee_degraded") or research_missing),
        "research_panel_missing_reason": payload.get("research_panel_missing_reason"),
        "forecast_probability_pct": payload.get("forecast_probability_pct") or candidate.get("forecast_probability_pct"),
        "execution_readiness_score": payload.get("execution_readiness_score") or candidate.get("execution_readiness_score"),
        "data_quality_status": payload.get("data_quality_status") or candidate.get("data_quality_status"),
        "live_orders_enabled": payload.get("live_orders_enabled"),
        "requires_manual_review": bool(
            payload.get("requires_manual_review")
            or payload.get("requires_manual_review_before_real_money")
            or payload.get("manual_review_required")
        ),
    }


def build_missing_data_panel(
    portfolio_summary: dict[str, Any],
    handoffs: list[dict[str, Any]],
    recommendation_status: str,
    recommendation_panel: dict[str, Any] | None = None,
    us_tactical_performance: dict[str, Any] | None = None,
    macro_regime: dict[str, Any] | None = None,
    asset_goal_contribution: dict[str, Any] | None = None,
    long_term_price_scenario: dict[str, Any] | None = None,
    research_panel_validation: dict[str, Any] | None = None,
    cost_basis_reconciliation: dict[str, Any] | None = None,
    portfolio_evidence_gap: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    panel: list[dict[str, str]] = []
    quality = portfolio_summary.get("data_quality_summary") or {}

    if "lceth" in quality and "degraded" in str(quality["lceth"]):
        panel.append({
            "category": "staking_valuation",
            "status": "degraded",
            "impact": "smaller size",
            "reason": "lcETH is still valued through ETH spot proxy; do not add ETH/lcETH until conversion/unlock basis is verified.",
        })
    if "us_equity_cash" in quality and "user_stated" in str(quality["us_equity_cash"]):
        panel.append({
            "category": "us_equity_cash",
            "status": "degraded",
            "impact": "conditional only",
            "reason": "US equity cash is user-stated and should be confirmed by broker cash/buying-power export before sizing trades.",
        })
    if recommendation_status != "ok":
        panel.append({
            "category": "recommendation_history",
            "status": recommendation_status,
            "impact": "conditional only",
            "reason": "Recommendation history is unavailable; new execute_now actions should be blocked.",
        })
    if recommendation_panel and recommendation_status == "ok":
        summary = recommendation_panel.get("summary") or {}
        calibration = recommendation_panel.get("calibration") or {}
        buckets = calibration.get("buckets") or {}
        resolved = sum(int((item or {}).get("resolved_count") or 0) for item in buckets.values())
        due_count = len(recommendation_panel.get("due_review") or [])
        if resolved == 0:
            panel.append({
                "category": "recommendation_calibration",
                "status": "unresolved",
                "impact": "block execute_now",
                "reason": "No recommendations have resolved to hit/failed yet; probability calibration is unavailable, so tactical execute_now remains blocked.",
            })
        if due_count:
            panel.append({
                "category": "recommendation_due_review",
                "status": "due",
                "impact": "manual review required",
                "reason": f"{due_count} pending recommendation(s) are due for outcome review before strategy parameters can be trusted.",
            })
    if any(h.get("research_committee_degraded") for h in handoffs):
        panel.append({
            "category": "active_alpha_handoff_research_panel",
            "status": "degraded",
            "impact": "block handoff execute_now",
            "reason": "At least one recent active-alpha handoff lacks a full research_panel; those handoff candidates cannot authorize execute_now even when this manual report has a valid Research Committee panel.",
        })
    if research_panel_validation:
        validation_status = research_panel_validation.get("status")
        if validation_status in {"missing", "invalid", "degraded"}:
            impact = "block" if validation_status in {"missing", "invalid"} else "conditional only"
            panel.append({
                "category": "research_panel",
                "status": str(validation_status),
                "impact": impact,
                "reason": research_panel_validation.get("reason")
                or "This run lacks a complete Research Committee panel; execute_now is blocked.",
            })
    if us_tactical_performance:
        status = us_tactical_performance.get("status")
        summary = us_tactical_performance.get("summary") or {}
        if status not in {"ok", "disabled"}:
            panel.append({
                "category": "us_tactical_performance",
                "status": str(status),
                "impact": "conditional only",
                "reason": "US tactical 50% target tracker is unavailable; tactical suggestions must state the target gap manually.",
            })
        elif summary.get("data_quality") == "degraded_user_stated_cash":
            panel.append({
                "category": "us_tactical_performance",
                "status": "degraded_user_stated_cash",
                "impact": "conditional only",
                "reason": "Tactical cash is included from user-stated broker cash; confirm buying power before sizing a real rotation.",
            })
    if macro_regime:
        status = macro_regime.get("status")
        summary = macro_regime.get("summary") or {}
        if status in {"missing", "unreadable"}:
            panel.append({
                "category": "macro_regime",
                "status": str(status),
                "impact": "conditional only",
                "reason": macro_regime.get("reason") or "Macro regime snapshot is unavailable; DCA pace must default to split_more/conditional.",
            })
        elif str(status).startswith("missing") or status == "degraded":
            panel.append({
                "category": "macro_regime",
                "status": str(status),
                "impact": "dca pace only",
                "reason": "Macro regime data is degraded; it may slow or split DCA but cannot authorize a strong single-asset action.",
            })
        if (summary.get("fund_flows") or {}).get("data_quality") == "missing":
            panel.append({
                "category": "fund_flows",
                "status": "missing",
                "impact": "conditional only",
                "reason": "Digital asset ETF/fund-flow confirmation is missing; crypto DCA recommendations cannot be upgraded by flow evidence.",
            })
    if asset_goal_contribution:
        status = asset_goal_contribution.get("status")
        summary = asset_goal_contribution.get("summary") or {}
        if status in {"missing", "unreadable", "script_missing", "error"}:
            panel.append({
                "category": "asset_goal_contribution",
                "status": str(status),
                "impact": "conditional only",
                "reason": asset_goal_contribution.get("reason") or "Asset-level goal contribution is unavailable; DCA recommendations must state target alignment manually.",
            })
        elif status == "degraded":
            panel.append({
                "category": "asset_goal_contribution",
                "status": "degraded",
                "impact": "smaller size",
                "reason": "Some staking, unlock, or asset thesis inputs are degraded; DCA can proceed only as staged/conditional.",
            })
        for item in summary.get("concentration_flags") or []:
            panel.append({
                "category": "goal_concentration",
                "status": "flagged",
                "impact": "smaller size",
                "reason": item,
            })
    if long_term_price_scenario:
        status = long_term_price_scenario.get("status")
        summary = long_term_price_scenario.get("summary") or {}
        if status in {"missing", "unreadable", "error"}:
            panel.append({
                "category": "long_term_price_scenario",
                "status": str(status),
                "impact": "conditional only",
                "reason": "Long-term price scenario panel is unavailable; DCA recommendations must not claim 5y/10y 10x fit.",
            })
        elif status == "degraded":
            missing = summary.get("missing_data") or []
            panel.append({
                "category": "long_term_price_scenario",
                "status": "degraded",
                "impact": "smaller size",
                "reason": "Some market cap, FDV, supply, unlock, or conversion inputs are missing: "
                + "; ".join(str(item) for item in missing[:6]),
            })
    if cost_basis_reconciliation:
        status = cost_basis_reconciliation.get("status")
        summary = (cost_basis_reconciliation.get("summary") or {}).get("summary") or {}
        failed_gates = (cost_basis_reconciliation.get("summary") or {}).get("failed_gates") or []
        missing_material = summary.get("missing_material_symbols") or []
        partial_symbols = summary.get("partial_known_lot_symbols") or []
        if status in {"script_missing", "ledger_missing", "error"}:
            panel.append({
                "category": "cost_basis_reconciliation",
                "status": str(status),
                "impact": "conditional only",
                "reason": "Cost-basis reconciliation is unavailable; reports must avoid cost-aware PnL and tax-lot claims.",
            })
        elif status == "partial" or "missing_material_cost_basis" in failed_gates:
            panel.append({
                "category": "cost_basis_reconciliation",
                "status": "partial",
                "impact": "conditional only",
                "reason": (
                    "Full cost-aware PnL is blocked. Missing material cost basis: "
                    f"{missing_material or ['unknown']}; partial verified lots: {partial_symbols or ['none']}."
                ),
            })
    if portfolio_evidence_gap:
        status = portfolio_evidence_gap.get("status")
        summary = portfolio_evidence_gap.get("summary") or {}
        readiness = (summary.get("summary") or {}) if isinstance(summary, dict) else {}
        package_readiness = (summary.get("readiness") or {}) if isinstance(summary, dict) else {}
        if status in {"script_missing", "ledger_missing", "error"}:
            panel.append({
                "category": "portfolio_evidence_gap_package",
                "status": str(status),
                "impact": "conditional only",
                "reason": "Portfolio evidence gap package is unavailable; missing lot/cash evidence must be tracked manually.",
            })
        elif status == "open_gaps":
            p0_gaps = readiness.get("p0_gap_ids") or []
            panel.append({
                "category": "portfolio_evidence_gap_package",
                "status": "open_gaps",
                "impact": str(package_readiness.get("max_allowed_effect") or "conditional only"),
                "reason": f"Open evidence gaps remain: {p0_gaps or ['see portfolio_evidence_gap_panel']}.",
            })
    return panel


def validate_research_panel(panel: dict[str, Any] | None) -> dict[str, Any]:
    if panel is None:
        return {
            "status": "missing",
            "valid": False,
            "reason": "No research_panel JSON was supplied for this manual report run.",
            "missing_fields": sorted(REQUIRED_RESEARCH_PANEL_FIELDS),
            "agent_output_count": 0,
        }
    if not isinstance(panel, dict):
        return {
            "status": "invalid",
            "valid": False,
            "reason": "research_panel must be a JSON object.",
            "missing_fields": sorted(REQUIRED_RESEARCH_PANEL_FIELDS),
            "agent_output_count": 0,
        }

    missing_fields = sorted(REQUIRED_RESEARCH_PANEL_FIELDS - set(panel))
    agent_outputs = panel.get("agent_outputs")
    agent_output_count = len(agent_outputs) if isinstance(agent_outputs, list) else 0
    validation = panel.get("external_agent_validation") or {}
    research_method = panel.get("research_method")
    errors = []
    if missing_fields:
        errors.append(f"missing fields: {missing_fields}")
    if not isinstance(agent_outputs, list):
        errors.append("agent_outputs must be a list")
    elif agent_output_count < MIN_RESEARCH_AGENT_OUTPUTS:
        errors.append(f"agent_outputs has fewer than {MIN_RESEARCH_AGENT_OUTPUTS} roles")

    if errors:
        return {
            "status": "invalid",
            "valid": False,
            "reason": "; ".join(errors),
            "missing_fields": missing_fields,
            "agent_output_count": agent_output_count,
        }

    if panel.get("research_committee_degraded"):
        return {
            "status": "degraded",
            "valid": True,
            "reason": panel.get("research_panel_missing_reason")
            or "Research panel is present but degraded; execute_now remains blocked.",
            "missing_fields": [],
            "agent_output_count": agent_output_count,
            "research_method": research_method,
            "external_agent_validation": validation,
            "max_allowed_action": panel.get("max_allowed_action"),
        }

    external_valid = validation.get("valid") is True
    quality_gate_passed = validation.get("committee_quality_gate_passed") is True
    unique_roles = int(validation.get("unique_known_role_count") or 0)
    successful_roles = int(validation.get("successful_known_role_count") or unique_roles or 0)
    evidence_verified_roles = int(
        validation.get("evidence_verified_known_role_count")
        if validation.get("evidence_verified_known_role_count") is not None
        else validation.get("verified_known_role_count") or 0
    )
    missing_required_roles = (
        panel.get("missing_external_roles")
        or validation.get("missing_required_external_roles")
        or sorted(REQUIRED_EXTERNAL_RESEARCH_ROLES - set(validation.get("unique_known_roles") or []))
    )
    local_fallback_roles = panel.get("local_fallback_roles") or []
    all_roles_degraded = (
        successful_roles > 0
        and int(validation.get("degraded_role_count") or 0) >= successful_roles
    )
    if (
        research_method != "external_subagent_outputs"
        or not external_valid
        or not quality_gate_passed
        or unique_roles < MIN_RESEARCH_AGENT_OUTPUTS
        or successful_roles < MIN_RESEARCH_AGENT_OUTPUTS
        or bool(missing_required_roles)
        or all_roles_degraded
    ):
        reasons = []
        if research_method != "external_subagent_outputs":
            reasons.append(f"research_method={research_method}")
        if not external_valid:
            reasons.append("external_agent_validation.valid is not true")
        if not quality_gate_passed:
            requirements = validation.get("committee_quality_gate_requirements") or {}
            reasons.append(f"committee_quality_gate_passed is not true; requirements={requirements}")
        min_evidence_roles = (validation.get("committee_quality_gate_requirements") or {}).get(
            "min_evidence_verified_known_roles",
            6,
        )
        if evidence_verified_roles < int(min_evidence_roles or 6):
            reasons.append(f"evidence_verified_known_role_count={evidence_verified_roles}")
        if unique_roles < MIN_RESEARCH_AGENT_OUTPUTS:
            reasons.append(f"unique_known_role_count={unique_roles}")
        if successful_roles < MIN_RESEARCH_AGENT_OUTPUTS:
            reasons.append(f"successful_known_role_count={successful_roles}")
        if missing_required_roles:
            reasons.append(f"missing_required_external_roles={missing_required_roles}")
        if local_fallback_roles:
            reasons.append(f"local_fallback_roles_present={local_fallback_roles}")
        if all_roles_degraded:
            reasons.append("all successful external roles are degraded")
        return {
            "status": "degraded",
            "valid": True,
            "reason": "; ".join(reasons) + "; execute_now remains blocked.",
            "missing_fields": [],
            "agent_output_count": agent_output_count,
            "research_method": research_method,
            "external_agent_validation": validation,
            "missing_external_roles": missing_required_roles,
            "local_fallback_roles": local_fallback_roles,
            "evidence_verified_known_role_count": evidence_verified_roles,
            "max_allowed_action": panel.get("max_allowed_action"),
        }

    return {
        "status": "verified",
        "valid": True,
        "reason": "Research panel schema is complete, quality gate passed, and all required external subagent roles are present.",
        "missing_fields": [],
        "agent_output_count": agent_output_count,
        "research_method": research_method,
        "external_agent_validation": validation,
        "missing_external_roles": [],
        "local_fallback_roles": [],
        "max_allowed_action": panel.get("max_allowed_action"),
    }


def more_restrictive_action(left: str, right: str | None) -> str:
    if not right:
        return left
    left_rank = ACTION_RANK.get(left, 99)
    right_rank = ACTION_RANK.get(right, 99)
    return left if left_rank <= right_rank else right


def max_action_from_context(
    handoffs: list[dict[str, Any]],
    missing_panel: list[dict[str, str]],
    research_panel_validation: dict[str, Any] | None = None,
) -> str:
    research_cap = (research_panel_validation or {}).get("max_allowed_action")
    if research_panel_validation and research_panel_validation.get("status") in {"missing", "invalid"}:
        return more_restrictive_action("watch", research_cap)
    if any(item.get("impact") == "block" for item in missing_panel):
        return more_restrictive_action("conditional_action_or_watch", research_cap)
    if any(h.get("research_committee_degraded") for h in handoffs):
        return more_restrictive_action("conditional_action_or_watch", research_cap)
    if research_panel_validation and research_panel_validation.get("status") == "degraded":
        return more_restrictive_action("conditional_action_or_watch", research_cap)
    return more_restrictive_action("conditional_action", research_cap)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build manual daily report context")
    parser.add_argument("--portfolio-snapshot-json", required=True, help="Snapshot from portfolio_comparison_snapshot.py")
    parser.add_argument("--goal-projection-json", help="Projection from goal_path_projection.py")
    parser.add_argument("--monthly-dca", type=float, default=1000.0)
    parser.add_argument("--recommendation-ledger", default=str(DEFAULT_RECOMMENDATION_LEDGER))
    parser.add_argument("--as-of", help="ISO-8601 timestamp for due-review; defaults to now inside recommendation_history.py")
    parser.add_argument("--handoff-dir", default=str(DEFAULT_HANDOFF_DIR))
    parser.add_argument("--handoff-limit", type=int, default=5)
    parser.add_argument("--research-panel-json", help="Optional research_panel JSON from research_panel_runner.py")
    parser.add_argument("--macro-regime-json", help="Optional macro regime snapshot from macro_regime_snapshot.py")
    parser.add_argument("--asset-goal-contribution-json", help="Optional asset goal contribution panel from asset_goal_contribution.py")
    parser.add_argument("--crypto-snapshot-json", help="Optional multisource crypto snapshot from active-alpha-paper-monitor")
    parser.add_argument("--us-scanner-json", help="Optional US open dynamic scanner snapshot from active-alpha-paper-monitor")
    parser.add_argument("--portfolio-ledger-json", default=str(DEFAULT_PORTFOLIO_LEDGER))
    parser.add_argument("--disable-asset-goal-contribution", action="store_true")
    parser.add_argument("--us-tactical-performance-ledger", default=str(DEFAULT_US_TACTICAL_PERFORMANCE_LEDGER))
    parser.add_argument("--disable-us-tactical-performance", action="store_true")
    parser.add_argument("--disable-cost-basis-reconciliation", action="store_true")
    parser.add_argument("--disable-portfolio-evidence-gap", action="store_true")
    parser.add_argument("--output", default="")
    parser.add_argument("--format", choices=["json"], default="json")
    args = parser.parse_args()

    snapshot_path = Path(args.portfolio_snapshot_json)
    snapshot = load_json(snapshot_path)
    portfolio = summarize_portfolio(snapshot)

    if args.goal_projection_json:
        projection = load_json(Path(args.goal_projection_json))
    else:
        projection_script = MANUAL_ROOT / "scripts" / "goal_path_projection.py"
        projection = run_json([
            sys.executable,
            str(projection_script),
            "--snapshot-json",
            str(snapshot_path),
            "--monthly-dca",
            str(args.monthly_dca),
            "--format",
            "json",
        ])

    rec = recommendation_summary(Path(args.recommendation_ledger), as_of=args.as_of)
    macro = macro_regime_summary(args.macro_regime_json)
    crypto_market = crypto_market_summary(args.crypto_snapshot_json)
    us_scanner = us_open_scanner_summary(args.us_scanner_json)
    if args.asset_goal_contribution_json:
        asset_goal = asset_goal_contribution_summary(args.asset_goal_contribution_json)
        if not asset_goal_has_timing_model(asset_goal):
            regenerated_asset_goal = build_asset_goal_contribution(
                snapshot_path=snapshot_path,
                goal_projection_path=args.goal_projection_json,
                ledger_path=args.portfolio_ledger_json,
                monthly_dca=args.monthly_dca,
                disabled=args.disable_asset_goal_contribution,
            )
            if regenerated_asset_goal.get("summary"):
                regenerated_asset_goal["source_replaced_stale_asset_goal_json"] = args.asset_goal_contribution_json
                regenerated_asset_goal["regeneration_reason"] = "supplied asset goal panel lacked long_horizon_timing_decisions"
                asset_goal = regenerated_asset_goal
    else:
        asset_goal = build_asset_goal_contribution(
            snapshot_path=snapshot_path,
            goal_projection_path=args.goal_projection_json,
            ledger_path=args.portfolio_ledger_json,
            monthly_dca=args.monthly_dca,
            disabled=args.disable_asset_goal_contribution,
        )
    price_scenario = build_long_term_price_scenario_panel(asset_goal, crypto_market)
    tactical_perf = us_tactical_performance_summary(
        snapshot_path=snapshot_path,
        ledger_path=Path(args.us_tactical_performance_ledger),
        disabled=args.disable_us_tactical_performance,
    )
    cost_basis = cost_basis_reconciliation_summary(
        ledger_path=Path(args.portfolio_ledger_json),
        disabled=args.disable_cost_basis_reconciliation,
    )
    evidence_gap = portfolio_evidence_gap_summary(
        ledger_path=Path(args.portfolio_ledger_json),
        disabled=args.disable_portfolio_evidence_gap,
    )
    handoffs = [normalize_handoff(path) for path in latest_handoff_paths(Path(args.handoff_dir), args.handoff_limit)]
    research_panel = load_json(args.research_panel_json) if args.research_panel_json else None
    research_validation = validate_research_panel(research_panel)
    missing_panel = build_missing_data_panel(
        portfolio,
        handoffs,
        rec["status"],
        rec,
        tactical_perf,
        macro,
        asset_goal,
        price_scenario,
        research_validation,
        cost_basis,
        evidence_gap,
    )

    context = {
        "generated_at": utc_now(),
        "source_files": {
            "portfolio_snapshot_json": str(snapshot_path),
            "goal_projection_json": args.goal_projection_json,
            "recommendation_ledger": args.recommendation_ledger,
            "handoff_dir": args.handoff_dir,
            "macro_regime_json": args.macro_regime_json,
            "asset_goal_contribution_json": args.asset_goal_contribution_json,
            "crypto_snapshot_json": args.crypto_snapshot_json,
            "us_scanner_json": args.us_scanner_json,
            "portfolio_ledger_json": args.portfolio_ledger_json,
            "us_tactical_performance_ledger": args.us_tactical_performance_ledger,
            "cost_basis_reconciliation_script": str(MANUAL_ROOT / "scripts" / "cost_basis_reconciliation_audit.py"),
            "portfolio_evidence_gap_script": str(MANUAL_ROOT / "scripts" / "portfolio_evidence_gap_packager.py"),
        },
        "portfolio_snapshot": portfolio,
        "goal_path_projection": projection,
        "macro_regime_panel": macro,
        "crypto_market_panel": crypto_market,
        "us_open_dynamic_scanner_panel": us_scanner,
        "asset_goal_contribution_panel": asset_goal,
        "long_term_price_scenario_panel": price_scenario,
        "cost_basis_reconciliation_panel": cost_basis,
        "portfolio_evidence_gap_panel": evidence_gap,
        "recommendation_history_summary": rec,
        "us_tactical_performance_panel": tactical_perf,
        "latest_active_alpha_handoffs": handoffs,
        "research_panel": research_panel,
        "research_panel_validation": research_validation,
        "missing_data_downgrade_panel": missing_panel,
        "report_readiness": {
            "can_generate_today_report": True,
            "max_allowed_action": max_action_from_context(handoffs, missing_panel, research_validation),
            "execute_now_allowed": False,
            "reason": (
                "manual review required; execute_now requires full research committee and double-80 gates"
                if research_validation.get("status") == "verified"
                else f"research panel {research_validation.get('status')}; execute_now blocked"
            ),
        },
    }
    if args.output:
        write_json(Path(args.output), context)
    print(json.dumps(context, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
