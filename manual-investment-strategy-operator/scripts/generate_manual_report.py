#!/usr/bin/env python3
"""Generate a full manual investment report with Research Committee gating.

The runner orchestrates the manual skill's local evidence pipeline:

1. Portfolio snapshot
2. Goal path projection
3. Macro regime snapshot
4. Asset goal contribution panel
5. Optional active-alpha crypto / US scanner snapshots
6. Initial daily context
7. Research Committee panel validation
8. Final daily context
9. Readable Markdown report

It is intentionally conservative. If a valid external 6+ subagent output file
is not supplied, the generated research panel is marked degraded and the report
must not promote any new action to execute_now.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from audit_warning_explainer import build_warning_explanations
from v3_evidence_snapshot import EvidenceSnapshotV2, NumericEvidenceV2


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
ACTIVE_ROOT = ROOT / "active-alpha-paper-monitor"
TMP_ROOT = Path("/private/tmp")
CONFIG_PATH = MANUAL_ROOT / "config" / "manual_strategy_config.json"

DEFAULT_CRYPTO_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,ADAUSDT,NIGHTUSDT"
PROTECTED_US_EQUITY_TACTICAL_SYMBOLS = {"CRCL"}
US_EQUITY_CASH_SYMBOLS = {"USD_US_EQUITY"}


def load_strategy_version() -> str:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        version = str(payload.get("version") or "").strip()
        if version:
            return version
    except Exception:  # noqa: BLE001
        pass
    return "manual-version-unknown"


STRATEGY_VERSION = load_strategy_version()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def local_date() -> str:
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return dt.datetime.now().strftime("%Y-%m-%d")


def date_compact(date_text: str) -> str:
    return date_text.replace("-", "")


def load_json(path: Path | str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_env_file(path: str | None) -> list[str]:
    """Load optional local secrets without ever logging values."""

    if not path:
        return []
    candidate = Path(path).expanduser()
    if not candidate.exists():
        return []
    loaded: list[str] = []
    for raw_line in candidate.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value or key in os.environ:
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded


def money(value: Any) -> str:
    if value is None:
        return "`n/a`"
    try:
        return f"`${float(value):,.2f}`"
    except (TypeError, ValueError):
        return f"`{value}`"


def pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return "`n/a`"
    try:
        return f"`{float(value):.{digits}f}%`"
    except (TypeError, ValueError):
        return f"`{value}`"


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def resolve_current_tactical_symbol_from_portfolio_snapshot(path: Path | str | None) -> tuple[str | None, str, list[dict[str, Any]]]:
    """Resolve the deployable US tactical benchmark from a portfolio snapshot.

    CRCL is protected as a long-term Circle holding, and cash is not a tactical
    equity symbol. Among the remaining US-equity holdings, pick the largest
    current value as the current deployable tactical position.
    """

    if not path:
        return None, "missing_portfolio_snapshot", []
    try:
        payload = load_json(path)
    except Exception as exc:  # noqa: BLE001
        return None, f"unreadable_portfolio_snapshot:{short(exc, 120)}", []
    candidates: list[dict[str, Any]] = []
    for holding in payload.get("holdings") or []:
        if not isinstance(holding, dict):
            continue
        symbol = str(holding.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        if str(holding.get("rail") or "").lower() != "us_equity":
            continue
        if symbol in PROTECTED_US_EQUITY_TACTICAL_SYMBOLS or symbol in US_EQUITY_CASH_SYMBOLS:
            continue
        value = as_float((holding.get("values") or {}).get("current"), None)
        if value is None:
            value = as_float(holding.get("market_value") or holding.get("current_value_usd"), 0.0)
        candidates.append({
            "symbol": symbol,
            "current_value_usd": value or 0.0,
            "data_quality": holding.get("data_quality"),
            "bucket": holding.get("bucket"),
        })
    candidates.sort(key=lambda item: item.get("current_value_usd") or 0.0, reverse=True)
    if not candidates:
        return None, "no_deployable_us_equity_tactical_holding_found", []
    return str(candidates[0]["symbol"]), "portfolio_snapshot_largest_nonprotected_us_equity_holding", candidates[:5]


def ensure_current_tactical_symbol(args: argparse.Namespace, portfolio_path: Path | str | None, steps: list[dict[str, Any]]) -> None:
    if str(getattr(args, "current_tactical_symbol", "") or "").strip():
        steps.append({
            "step": "current_tactical_symbol_resolution",
            "status": "ok",
            "symbol": args.current_tactical_symbol,
            "source": "explicit_cli_argument",
        })
        return
    symbol, source, candidates = resolve_current_tactical_symbol_from_portfolio_snapshot(portfolio_path)
    if symbol:
        args.current_tactical_symbol = symbol
        steps.append({
            "step": "current_tactical_symbol_resolution",
            "status": "ok",
            "symbol": symbol,
            "source": source,
            "candidates": candidates,
        })
    else:
        args.current_tactical_symbol = ""
        steps.append({
            "step": "current_tactical_symbol_resolution",
            "status": "degraded",
            "source": source,
            "candidates": candidates,
            "impact": "us_tactical_scanner_runs_without_current_tactical_benchmark; no execute_now",
        })


def short(value: Any, max_len: int = 120) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join("" if item is None else str(item) for item in row) + " |")
    return "\n".join(lines)


def price(value: Any, digits: int = 4) -> str:
    if value is None:
        return "`n/a`"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return f"`{value}`"
    if abs(numeric) >= 100:
        return f"`{numeric:,.2f}`"
    if abs(numeric) >= 1:
        return f"`{numeric:,.4f}`"
    return f"`{numeric:.6f}`"


def price_range_from_pct(value: Any, low_pct: float, high_pct: float) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "`n/a`"
    low = numeric * low_pct
    high = numeric * high_pct
    if low > high:
        low, high = high, low
    return f"{price(low)} - {price(high)}"


def parse_datetime(value: Any) -> dt.datetime:
    if not value:
        return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).replace(microsecond=0)


def clamp_evidence_as_of(as_of: Any, cutoff_at: str) -> str:
    """Preserve source precision without allowing evidence beyond the frozen cutoff."""

    if not as_of:
        return cutoff_at
    text = str(as_of)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        evidence_time = dt.datetime.fromisoformat(text)
        cutoff_time = dt.datetime.fromisoformat(cutoff_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return cutoff_at
    if evidence_time.tzinfo is None:
        evidence_time = evidence_time.replace(tzinfo=dt.timezone.utc)
    if cutoff_time.tzinfo is None:
        cutoff_time = cutoff_time.replace(tzinfo=dt.timezone.utc)
    if evidence_time.astimezone(dt.timezone.utc) > cutoff_time.astimezone(dt.timezone.utc):
        return cutoff_at
    return str(as_of)


def date_after(value: Any, days: int) -> str:
    return (parse_datetime(value) + dt.timedelta(days=days)).date().isoformat()


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def price_range_from_values(low: Any, high: Any) -> str:
    low_value = safe_float(low)
    high_value = safe_float(high)
    if low_value is None or high_value is None:
        return "`n/a`"
    if low_value > high_value:
        low_value, high_value = high_value, low_value
    return f"{price(low_value)} - {price(high_value)}"


def crypto_asset_map(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    market = ((context.get("crypto_market_panel") or {}).get("summary") or {}).get("assets") or []
    return {str(item.get("symbol")): item for item in market if item.get("symbol")}


def scanner_candidates(context: dict[str, Any]) -> list[dict[str, Any]]:
    full_payload = context.get("us_open_dynamic_scanner_full_payload") or {}
    if full_payload.get("candidates"):
        return full_payload.get("candidates") or []
    scanner = ((context.get("us_open_dynamic_scanner_panel") or {}).get("summary") or {})
    return scanner.get("candidates") or []


def risk_path_summary(item: dict[str, Any]) -> dict[str, Any]:
    risk = item.get("risk_adjusted_path") or {}
    windows = risk.get("windows") or {}
    daily20 = windows.get("daily_20") or {}
    daily60 = windows.get("daily_60") or {}
    return {
        "lane": risk.get("lane"),
        "sharpe_20": daily20.get("sharpe"),
        "sharpe_60": daily60.get("sharpe"),
        "sortino_60": daily60.get("sortino"),
        "information_ratio_60": daily60.get("information_ratio"),
        "relative_strength_60_pct": daily60.get("relative_strength_pct"),
        "max_drawdown_60_pct": daily60.get("max_drawdown_pct"),
        "quality_score": risk.get("quality_score"),
        "ranking_adjustment_points": risk.get("ranking_adjustment_points"),
        "persistence_label": risk.get("persistence_label"),
        "data_quality": risk.get("data_quality"),
        "promotion_status": risk.get("promotion_status"),
        "flags": risk.get("flags") or [],
        "risk_free_rate": risk.get("risk_free_rate") or {},
        "live_gate_effect": risk.get("live_gate_effect"),
        "dca_pacing": risk.get("dca_pacing_if_longterm_thesis_passes") or {},
    }


def refresh_risk_adjusted_path_panel(context: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in scanner_candidates(context):
        if not isinstance(item, dict) or not item.get("risk_adjusted_path"):
            continue
        rows.append({
            "symbol": item.get("symbol") or item.get("candidate_symbol"),
            "asset_class": "us_equity",
            "source": "us_open_dynamic_scanner",
            "base_score_points": item.get(
                "open_scan_score_points_before_risk_adjustment"
            ),
            "adjusted_score_points": item.get("open_scan_score_points"),
            **risk_path_summary(item),
        })
    for item in (context.get("impulse_capture_panel") or {}).get("signals") or []:
        if not isinstance(item, dict) or not item.get("risk_adjusted_path"):
            continue
        rows.append({
            "symbol": item.get("symbol"),
            "asset_class": "crypto",
            "source": "impulse_capture_scanner",
            "base_score_points": item.get(
                "impulse_score_points_before_risk_adjustment"
            ),
            "adjusted_score_points": item.get("impulse_score_points"),
            **risk_path_summary(item),
        })
    rank_ablation: list[dict[str, Any]] = []
    for asset_class in ("us_equity", "crypto"):
        cohort = [item for item in rows if item.get("asset_class") == asset_class]
        before = sorted(
            cohort,
            key=lambda item: safe_float(item.get("base_score_points"), -10_000),
            reverse=True,
        )
        after = sorted(
            cohort,
            key=lambda item: safe_float(item.get("adjusted_score_points"), -10_000),
            reverse=True,
        )
        before_rank = {
            (item.get("symbol"), item.get("source")): rank
            for rank, item in enumerate(before, start=1)
        }
        after_rank = {
            (item.get("symbol"), item.get("source")): rank
            for rank, item in enumerate(after, start=1)
        }
        for item in cohort:
            key = (item.get("symbol"), item.get("source"))
            rank_ablation.append({
                "symbol": item.get("symbol"),
                "asset_class": asset_class,
                "base_rank": before_rank.get(key),
                "risk_adjusted_rank": after_rank.get(key),
                "rank_change": (
                    before_rank.get(key) - after_rank.get(key)
                    if before_rank.get(key) and after_rank.get(key)
                    else None
                ),
                "base_score_points": item.get("base_score_points"),
                "adjusted_score_points": item.get("adjusted_score_points"),
            })
    paired_backtest = {
        "status": "insufficient_closed_risk_adjusted_samples",
        "same_candidate_pool_rank_ablation_completed": bool(rank_ablation),
        "closed_sample_count": 0,
        "independent_non_overlapping_windows": 0,
        "base_strategy_expected_return_pct": None,
        "risk_adjusted_strategy_expected_return_pct": None,
        "base_strategy_max_drawdown_pct": None,
        "risk_adjusted_strategy_max_drawdown_pct": None,
        "reason": (
            "The factor begins in shadow mode in this run. Current rank changes are "
            "auditable, but forward returns do not yet exist and cannot be fabricated."
        ),
    }
    promotion_gate = {
        "passed": False,
        "required_independent_non_overlapping_windows": 3,
        "observed_independent_non_overlapping_windows": 0,
        "required_closed_paper_samples": 20,
        "observed_closed_paper_samples": 0,
        "required_win_rate_pct": 55,
        "observed_win_rate_pct": None,
        "required_net_return_pct": 5,
        "observed_net_return_pct": None,
        "max_allowed_drawdown_pct": -15,
        "observed_max_drawdown_pct": None,
        "max_allowed_drawdown_deterioration_points_vs_base": 2,
        "observed_drawdown_deterioration_points_vs_base": None,
        "failed_gates": [
            "independent_windows_lt_3",
            "closed_samples_lt_20",
            "win_rate_unavailable",
            "net_return_unavailable",
            "drawdown_comparison_unavailable",
        ],
    }
    panel = {
        "status": "ok" if rows else "degraded_missing_risk_adjusted_paths",
        "calculation_version": "risk-adjusted-path-v1",
        "strategy_role": "secondary_path_quality_factor",
        "hard_sharpe_gt_3_gate": False,
        "promotion_status": "research_only_paper_only",
        "live_gate_effect": "none_until_promotion",
        "candidate_count": len(rows),
        "candidates": rows,
        "rank_ablation": rank_ablation,
        "paired_backtest": paired_backtest,
        "promotion_gate": promotion_gate,
        "formula_parameters": {
            "us_daily_annualization": 252,
            "crypto_daily_annualization": 365,
            "crypto_4h_annualization": 2190,
            "daily_windows": [20, 60, 126],
            "crypto_4h_windows": [42, 126],
            "trend_adjustment_limit_points": 7.5,
            "value_repair_adjustment_limit_points": 4,
            "risk_free_source": "EvidenceSnapshotV2 US Treasury 3-month yield; explicit zero fallback",
            "closed_bars_only": True,
        },
        "ablation_available": any(
            item.get("base_score_points") is not None
            and item.get("adjusted_score_points") is not None
            for item in rows
        ),
    }
    context["risk_adjusted_path_panel"] = panel
    return panel


def write_risk_adjusted_evidence_snapshot(
    context: dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    cutoff_at = utc_now()
    generated_at = utc_now()
    records: list[NumericEvidenceV2] = []

    def add_record(
        *,
        evidence_id: str,
        category: str,
        symbol: str,
        metric: str,
        value: Any,
        unit: str,
        as_of: str | None,
        source: str,
        max_age_seconds: int,
    ) -> None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(number):
            return
        evidence_as_of = clamp_evidence_as_of(as_of, cutoff_at)
        records.append(
            NumericEvidenceV2(
                evidence_id=evidence_id,
                category=category,
                symbol=symbol,
                metric=metric,
                value=number,
                unit=unit,
                as_of=evidence_as_of,
                source=source,
                source_role="primary",
                max_age_seconds=max_age_seconds,
            )
        )

    add_record(
        evidence_id=f"{run_id}-us-equity-cash",
        category="portfolio_baseline",
        symbol="USD",
        metric="us_equity_deployable_cash",
        value=0,
        unit="USD",
        as_of=cutoff_at,
        source="Frozen handoff baseline; no later confirmed settled cash",
        max_age_seconds=86_400,
    )
    add_record(
        evidence_id=f"{run_id}-crypto-cash",
        category="portfolio_baseline",
        symbol="USDT",
        metric="crypto_deployable_cash",
        value=0,
        unit="USDT",
        as_of=cutoff_at,
        source="Frozen handoff baseline; no later confirmed settled cash",
        max_age_seconds=86_400,
    )

    macro = (context.get("macro_regime_panel") or {}).get("summary") or {}
    treasury = macro.get("treasury_yield_curve") or {}
    treasury_date = treasury.get("date")
    treasury_as_of = (
        f"{str(treasury_date).split('T')[0]}T00:00:00+00:00"
        if treasury_date
        else cutoff_at
    )
    add_record(
        evidence_id=f"{run_id}-us-treasury-3m",
        category="risk_free_rate",
        symbol="USD",
        metric="us_treasury_3m_yield_pct",
        value=treasury.get("yield_3m_pct"),
        unit="percent_annual",
        as_of=treasury_as_of,
        source="U.S. Treasury daily yield curve",
        max_age_seconds=7 * 86_400,
    )

    panel = context.get("risk_adjusted_path_panel") or {}
    us_scanner = (context.get("us_open_dynamic_scanner_panel") or {}).get("summary") or {}
    us_as_of = us_scanner.get("created_at") or cutoff_at
    impulse = context.get("impulse_capture_panel") or {}
    crypto_as_of = impulse.get("captured_at") or cutoff_at
    source_items = []
    source_items.extend(
        (
            item,
            us_as_of,
            "Yahoo public adjusted closed daily bars",
            86_400,
        )
        for item in scanner_candidates(context)
    )
    source_items.extend(
        (
            item,
            crypto_as_of,
            "Binance public spot closed daily and 4-hour bars",
            86_400,
        )
        for item in impulse.get("signals") or []
        if isinstance(item, dict)
    )
    for item, as_of, source, max_age in source_items:
        symbol = str(item.get("symbol") or item.get("candidate_symbol") or "").upper()
        if not symbol or not item.get("risk_adjusted_path"):
            continue
        slug = "".join(char.lower() for char in symbol if char.isalnum())
        risk = risk_path_summary(item)
        current_price = item.get("price", item.get("current_price"))
        add_record(
            evidence_id=f"{run_id}-{slug}-price",
            category="market_price",
            symbol=symbol,
            metric="last_price",
            value=current_price,
            unit="USD_or_USDT_per_asset",
            as_of=item.get("price_as_of") or as_of,
            source=item.get("price_source") or source,
            max_age_seconds=900,
        )
        metric_map = {
            "sharpe_20d": (risk.get("sharpe_20"), "ratio"),
            "sharpe_60d": (risk.get("sharpe_60"), "ratio"),
            "information_ratio_60d": (
                risk.get("information_ratio_60"),
                "ratio",
            ),
            "max_drawdown_60d_pct": (
                risk.get("max_drawdown_60_pct"),
                "percent",
            ),
            "risk_adjusted_quality_score": (
                risk.get("quality_score"),
                "points",
            ),
            "risk_adjusted_ranking_adjustment": (
                risk.get("ranking_adjustment_points"),
                "points",
            ),
        }
        for metric_name, (metric_value, unit) in metric_map.items():
            add_record(
                evidence_id=f"{run_id}-{slug}-{metric_name}",
                category="risk_adjusted_path",
                symbol=symbol,
                metric=metric_name,
                value=metric_value,
                unit=unit,
                as_of=as_of,
                source=source,
                max_age_seconds=max_age,
            )

    snapshot = EvidenceSnapshotV2.build(
        snapshot_id=f"evidence-snapshot-v2-{run_id}-risk-adjusted-market",
        generated_at=generated_at,
        cutoff_at=cutoff_at,
        records=records,
    )
    stamp = dt.datetime.fromisoformat(cutoff_at).strftime("%Y%m%d-%H%M%S")
    path = (
        MANUAL_ROOT
        / "experiments"
        / f"{stamp}-{run_id}-risk-adjusted-market-evidence-snapshot.json"
    )
    write_json(path, snapshot.to_dict())
    audit_path = (
        MANUAL_ROOT
        / "experiments"
        / f"{stamp}-{run_id}-risk-adjusted-path-audit.json"
    )
    write_json(
        audit_path,
        {
            "audit_version": "risk-adjusted-path-audit-v1",
            "generated_at": generated_at,
            "snapshot_id": snapshot.snapshot_id,
            "formula_parameters": panel.get("formula_parameters"),
            "candidate_metrics": panel.get("candidates"),
            "rank_ablation": panel.get("rank_ablation"),
            "paired_backtest": panel.get("paired_backtest"),
            "promotion_gate": panel.get("promotion_gate"),
            "live_gate_effect": "none_until_promotion",
            "live_orders_enabled": False,
        },
    )
    result = {
        "status": "ok",
        "snapshot_id": snapshot.snapshot_id,
        "path": str(path),
        "audit_path": str(audit_path),
        "record_count": len(snapshot.records),
        "conflict_count": len(snapshot.conflicts),
        "risk_adjusted_candidate_count": panel.get("candidate_count"),
        "live_orders_enabled": False,
    }
    context["risk_adjusted_evidence_snapshot"] = result
    return result


def source_portfolio_holding(context: dict[str, Any], symbol: str) -> dict[str, Any]:
    source_path = (context.get("source_files") or {}).get("portfolio_snapshot_json")
    if not source_path:
        return {}
    try:
        source = load_json(Path(source_path))
    except (OSError, json.JSONDecodeError):
        return {}
    for item in source.get("holdings") or []:
        if item.get("symbol") == symbol:
            return item
    return {}


def dynamic_crypto_entry_ranges(market: dict[str, Any]) -> tuple[str, str]:
    last = safe_float(market.get("last_price"))
    if last is None:
        return "`n/a`", "`n/a`"
    high = safe_float(market.get("high_24h"), last)
    low = safe_float(market.get("low_24h"), last)
    change_abs = abs(safe_float(market.get("change_24h_pct"), 0.0) or 0.0) / 100.0
    intraday_range = abs((high or last) - (low or last)) / last if last else 0.0
    pullback = clamp(max(intraday_range * 1.2, change_abs * 0.8, 0.025), 0.025, 0.12)
    near_low = last * (1.0 - pullback * 0.55)
    near_high = last * (1.0 + min(0.006, pullback * 0.12))
    optimal_low = last * (1.0 - pullback * 1.45)
    optimal_high = last * (1.0 - pullback * 0.85)
    return price_range_from_values(near_low, near_high), price_range_from_values(optimal_low, optimal_high)


def dynamic_tactical_ladder(current_price: Any, holding: dict[str, Any] | None = None) -> dict[str, str]:
    price_value = safe_float(current_price)
    if price_value is None:
        return {
            "secondary_entry": "`n/a`",
            "optimal_entry": "`n/a`",
            "trim": "`n/a`",
            "full_exit": "`n/a`",
            "invalid": "`n/a`",
        }
    prices = (holding or {}).get("prices") or {}
    one_day = safe_float(prices.get("1d"))
    seven_day = safe_float(prices.get("7d"))
    thirty_day = safe_float(prices.get("30d"))
    one_day_move = abs(price_value / one_day - 1.0) if one_day else 0.0
    seven_day_run = max(0.0, price_value / seven_day - 1.0) if seven_day else 0.0
    thirty_day_run = max(0.0, price_value / thirty_day - 1.0) if thirty_day else 0.0
    runup_pressure = max(one_day_move * 2.0, seven_day_run * 0.65, thirty_day_run * 0.28, 0.045)
    pullback = clamp(runup_pressure, 0.055, 0.18)
    upside = clamp(max(0.035, seven_day_run * 0.35, one_day_move * 1.5), 0.035, 0.11)
    secondary_entry = price_range_from_values(price_value * (1 - pullback * 0.62), price_value * (1 - pullback * 0.28))
    optimal_entry = price_range_from_values(price_value * (1 - pullback * 1.18), price_value * (1 - pullback * 0.78))
    trim = price_range_from_values(price_value * (1 + upside * 0.75), price_value * (1 + upside * 1.15))
    full_exit = price_range_from_values(price_value * (1 + upside * 1.35), price_value * (1 + upside * 1.8))
    invalid = price(price_value * (1 - clamp(pullback * 1.12, 0.075, 0.16)))
    return {
        "secondary_entry": secondary_entry,
        "optimal_entry": optimal_entry,
        "trim": trim,
        "full_exit": full_exit,
        "invalid": invalid,
    }


def build_dca_execution_rows(context: dict[str, Any]) -> list[list[Any]]:
    panel = (context.get("asset_goal_contribution_panel") or {}).get("summary") or {}
    guidance = panel.get("dca_guidance") or {}
    crypto_assets = crypto_asset_map(context)
    rows: list[list[Any]] = []
    for item in guidance.get("suggested_ranges") or []:
        symbol = item.get("symbol")
        if not symbol:
            continue
        bounds = item.get("suggested_monthly_range_usd") or []
        low_amount = safe_float(bounds[0], 0.0) if bounds else 0.0
        high_amount = safe_float(bounds[-1], low_amount) if bounds else low_amount
        pair = f"{symbol}USDT" if symbol != "USDT" else "USDT"
        market = crypto_assets.get(pair, {})
        condition = item.get("condition") or ""
        role = item.get("role") or ""
        timing = item.get("long_horizon_timing_decision") or {}
        timing_decision = timing.get("decision")
        if timing_decision:
            condition = f"{condition}; timing={timing_decision}; wait_cost={money(timing.get('staking_wait_cost_usd'))}; required_pullback={pct(timing.get('required_pullback_to_wait_pct'))}"
        if symbol == "USDT" or not market:
            action = "opportunity reserve" if symbol == "USDT" else "watch/conditional"
            near = f"保留 {money(low_amount)} - {money(high_amount)}"
            optimal = "等待更优风险回报，不设置币种限价"
        else:
            near_range, optimal_range = dynamic_crypto_entry_ranges(market)
            front_load_amount = safe_float(timing.get("front_load_amount_usd"), 0.0)
            if low_amount <= 0:
                action = "watch/conditional tail"
                near = "近价不主动加仓"
                optimal = f"最多 {money(high_amount)} @ {optimal_range}"
            elif timing_decision == "accelerated_dca":
                action = "accelerated DCA"
                extra = f"；可前置 {money(front_load_amount)}" if front_load_amount > 0 else ""
                near = f"{money(high_amount)} 上限内优先近价 @ {near_range}{extra}"
                optimal = f"若快速回调再优化 @ {optimal_range}"
            elif timing_decision == "limit_order_wait":
                action = "conditional limit DCA"
                near = f"先不追；最多 {money(low_amount)} 观察 @ {near_range}"
                optimal = f"{money(high_amount)} 主仓 @ {optimal_range}"
            else:
                action = "conditional DCA"
                extra_amount = max((high_amount or 0.0) - (low_amount or 0.0), 0.0)
                near = f"{money(low_amount)} @ {near_range}"
                optimal = f"{money(extra_amount)} 追加 @ {optimal_range}" if extra_amount else f"{money(high_amount)} @ {optimal_range}"
        rows.append([
            pair,
            action,
            near,
            optimal,
            "7-14d复盘；长期 thesis 季度复盘",
            short(f"{role}: {condition}", 120),
        ])
    return rows


def build_us_tactical_ladder_rows(context: dict[str, Any], tactical_component: dict[str, Any] | None) -> list[list[Any]]:
    if not tactical_component:
        return []
    symbol = tactical_component.get("symbol")
    current_price = tactical_component.get("current_price")
    holding = source_portfolio_holding(context, str(symbol))
    ladder = dynamic_tactical_ladder(current_price, holding)
    return [[
        symbol,
        "当前动态战术仓/benchmark",
        ladder["secondary_entry"],
        ladder["optimal_entry"],
        ladder["trim"],
        f"{ladder['full_exit']}；跌破 {ladder['invalid']} 失效",
        "1-3个交易日看入场；最多10个交易日复盘",
    ]]


def dca_guidance_by_symbol(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    panel = (context.get("asset_goal_contribution_panel") or {}).get("summary") or {}
    guidance = panel.get("dca_guidance") or {}
    return {
        str(item.get("symbol")): item
        for item in guidance.get("suggested_ranges") or []
        if item.get("symbol")
    }


def dca_timing_by_symbol(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    panel = (context.get("asset_goal_contribution_panel") or {}).get("summary") or {}
    guidance = panel.get("dca_guidance") or {}
    timing = {
        str(item.get("symbol")): item
        for item in guidance.get("long_horizon_timing_decisions") or []
        if item.get("symbol")
    }
    for item in guidance.get("suggested_ranges") or []:
        embedded = item.get("long_horizon_timing_decision") or {}
        if embedded.get("symbol"):
            timing[str(embedded["symbol"])] = embedded
    return timing


def long_term_price_scenario_by_symbol(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    panel = (context.get("long_term_price_scenario_panel") or {}).get("summary") or {}
    return {
        str(item.get("symbol")): item
        for item in panel.get("assets") or []
        if item.get("symbol")
    }


def scenario_range_text(values: Any) -> str:
    if not isinstance(values, list) or len(values) < 2:
        return "`n/a`"
    return price_range_from_values(values[0], values[1])


def scenario_summary_text(context: dict[str, Any], symbol: str) -> str:
    scenario = long_term_price_scenario_by_symbol(context).get(symbol) or {}
    if not scenario:
        return "long-term scenario missing; keep as watch/conditional until refreshed"
    return (
        f"5y base {scenario_range_text(scenario.get('five_year_base_price_range'))}; "
        f"10y base {scenario_range_text(scenario.get('ten_year_base_price_range'))}; "
        f"fit={scenario.get('ten_x_goal_fit')}; DCA={scenario.get('dca_implication')}; "
        f"confidence={scenario.get('confidence_band')}"
    )


def guidance_amounts(item: dict[str, Any] | None) -> tuple[float, float, float]:
    bounds = (item or {}).get("suggested_monthly_range_usd") or []
    low_amount = safe_float(bounds[0], 0.0) if bounds else 0.0
    high_amount = safe_float(bounds[-1], low_amount) if bounds else low_amount
    extra_amount = max((high_amount or 0.0) - (low_amount or 0.0), 0.0)
    return low_amount or 0.0, high_amount or 0.0, extra_amount


def dca_entry_spec(
    context: dict[str, Any],
    symbol: str,
    default_near_pct: tuple[float, float],
    default_pullback_pct: tuple[float, float],
) -> dict[str, Any]:
    crypto_assets = crypto_asset_map(context)
    guidance = dca_guidance_by_symbol(context).get(symbol) or {}
    timing = dca_timing_by_symbol(context).get(symbol) or guidance.get("long_horizon_timing_decision") or {}
    pair = f"{symbol}USDT"
    market = crypto_assets.get(pair, {})
    near_range, optimal_range = dynamic_crypto_entry_ranges(market) if market else ("`n/a`", "`n/a`")
    if near_range == "`n/a`" and market.get("last_price") is not None:
        near_range = price_range_from_pct(market.get("last_price"), *default_near_pct)
    if optimal_range == "`n/a`" and market.get("last_price") is not None:
        optimal_range = price_range_from_pct(market.get("last_price"), *default_pullback_pct)
    low_amount, high_amount, extra_amount = guidance_amounts(guidance)
    if low_amount <= 0:
        position_plan = f"near $0; conditional pullback/tail cap {money(high_amount)}"
    else:
        position_plan = f"near {money(low_amount)}; pullback add {money(extra_amount)}; monthly cap {money(high_amount)}"
    return {
        "pair": pair,
        "near_range": near_range,
        "optimal_range": optimal_range,
        "position_size_plan": position_plan,
        "role": guidance.get("role"),
        "condition": guidance.get("condition"),
        "long_horizon_timing_decision": timing,
        "dca_timing_decision": timing.get("decision"),
        "staking_wait_cost_usd": timing.get("staking_wait_cost_usd"),
        "staking_activation_delay_days": timing.get("staking_activation_delay_days"),
        "staking_activation_delay_cost_usd": timing.get("staking_activation_delay_cost_usd"),
        "estimated_staking_start_if_buy_now": timing.get("estimated_staking_start_if_buy_now"),
        "estimated_staking_start_if_wait": timing.get("estimated_staking_start_if_wait"),
        "required_pullback_to_wait_pct": timing.get("required_pullback_to_wait_pct"),
        "time_in_market_bias_score": timing.get("time_in_market_bias_score"),
        "wait_requires_specific_pullback_trigger": timing.get("wait_requires_specific_pullback_trigger"),
        "front_load_extra_months": timing.get("front_load_extra_months"),
        "front_load_amount_usd": timing.get("front_load_amount_usd"),
        "near_term_total_budget_usd": timing.get("near_term_total_budget_usd"),
        "long_term_low_value_zone_status": timing.get("long_term_low_value_zone_status") or timing.get("long_term_value_zone_status"),
        "low_value_evidence_summary": timing.get("low_value_evidence_summary"),
        "early_entry_benefit_summary": timing.get("early_entry_benefit_summary"),
        "dynamic_buy_strategy": timing.get("dynamic_buy_strategy"),
        "waiting_burden_of_proof_comment": timing.get("waiting_burden_of_proof_comment"),
        "front_load_reason": timing.get("front_load_reason"),
        "minimum_extra_discount_required_to_wait_pct": timing.get("minimum_extra_discount_required_to_wait_pct") or timing.get("required_pullback_to_wait_pct"),
        "cash_idle_drag_comment": timing.get("cash_idle_drag_comment"),
        "long_term_low_value_entry_panel": timing.get("long_term_low_value_entry_panel"),
        "monthly_low_usd": low_amount,
        "monthly_high_usd": high_amount,
    }


def calibration_rows(rec: dict[str, Any]) -> list[list[Any]]:
    calibration = rec.get("calibration") or {}
    rows: list[list[Any]] = []
    for bucket, item in (calibration.get("buckets") or {}).items():
        rows.append([
            bucket,
            item.get("count"),
            item.get("resolved_count"),
            item.get("hit"),
            item.get("failed"),
            pct(item.get("hit_rate_pct")),
            f"`{item.get('brier_score'):.4f}`" if isinstance(item.get("brier_score"), (int, float)) else "`n/a`",
        ])
    return rows


def progressive_learning_summary(rec: dict[str, Any]) -> dict[str, Any]:
    calibration = (rec.get("calibration") or {}).get("buckets") or {}
    rec_summary = rec.get("summary") or {}
    status_counts = rec_summary.get("status_counts") or {}
    resolved_count = int(status_counts.get("hit", 0) or 0) + int(status_counts.get("failed", 0) or 0)
    if resolved_count < 10:
        stage = "cold_start"
        next_stage = "early_calibration"
        needed = 10 - resolved_count
        max_learning_action = "watch / paper_only / conditional_action"
    elif resolved_count < 20:
        stage = "early_calibration"
        next_stage = "usable_calibration"
        needed = 20 - resolved_count
        max_learning_action = "paper_only / conditional_action / small_probe_review"
    elif resolved_count < 50:
        stage = "usable_calibration"
        next_stage = "validated_ramp"
        needed = 50 - resolved_count
        max_learning_action = "conditional_action / proposed_changes_after_human_review"
    else:
        stage = "validated_ramp"
        next_stage = "maintain_multi_regime_validation"
        needed = 0
        max_learning_action = "conditional_action / execute_now_candidate_only_if_double80_and_all_gates_pass"

    def bucket_count(name: str) -> int:
        item = calibration.get(name) or {}
        return int(item.get("count", 0) or 0)

    def bucket_resolved(name: str) -> int:
        item = calibration.get(name) or {}
        return int(item.get("resolved_count", 0) or 0)

    return {
        "stage": stage,
        "next_stage": next_stage,
        "resolved_count": resolved_count,
        "resolved_needed_for_next_stage": needed,
        "pending_count": int(status_counts.get("pending", 0) or 0),
        "superseded_count": int(status_counts.get("superseded", 0) or 0),
        "below_60_count": bucket_count("below_60"),
        "learning_60_to_79_count": bucket_count("60_to_79"),
        "learning_60_to_79_resolved": bucket_resolved("60_to_79"),
        "strong_80_plus_count": bucket_count("80_plus"),
        "strong_80_plus_resolved": bucket_resolved("80_plus"),
        "missing_probability_count": bucket_count("missing_probability"),
        "max_learning_action": max_learning_action,
        "execute_now_note": "80% is an execute_now candidate threshold, not the starting threshold for learning samples.",
    }


def current_config_hash() -> str:
    path = MANUAL_ROOT / "config" / "manual_strategy_config.json"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "config_unavailable"


def recommendation_record(
    recommendation_id: str,
    run_id: str,
    generated_at: str,
    config_hash: str,
    asset_class: str,
    symbol: str,
    bucket: str,
    action: str,
    direction: str,
    time_window: str,
    data_quality_status: str,
    validation_status: str,
    promotion_status: str,
    risk_decision: str,
    **extra: Any,
) -> dict[str, Any]:
    record = {
        "recommendation_id": recommendation_id,
        "run_id": run_id,
        "strategy_version": STRATEGY_VERSION,
        "config_hash": config_hash,
        "generated_at": generated_at,
        "asset_class": asset_class,
        "symbol": symbol,
        "bucket": bucket,
        "action": action,
        "direction": direction,
        "time_window": time_window,
        "data_quality_status": data_quality_status,
        "validation_status": validation_status,
        "promotion_status": promotion_status,
        "risk_decision": risk_decision,
        "outcome_status": "pending",
    }
    record.update({key: value for key, value in extra.items() if value is not None})
    return record


def execution_calendar_defaults(context: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Add the auditable time/funding state required for formal candidates."""
    asset_class = str(record.get("asset_class") or "")
    generated_at = str(record.get("generated_at") or context.get("generated_at") or utc_now())
    if asset_class.startswith("us_equity"):
        source_panel = (context.get("us_open_dynamic_scanner_panel") or {}).get("summary") or {}
        price_as_of = source_panel.get("created_at") or source_panel.get("generated_at")
        price_source = "us_open_dynamic_scanner_panel_or_portfolio_snapshot"
        allowed_session = "us_regular_session_only"
        cash_symbol = "USD_US_EQUITY"
        settlement_constraint = "Only settled US-equity cash is deployable; sale proceeds enter cash pending T+1 settlement and a new manual review."
    else:
        source_panel = (context.get("crypto_market_panel") or {}).get("summary") or {}
        price_as_of = source_panel.get("generated_at")
        price_source = "crypto_market_panel_public_spot_sources"
        allowed_session = "crypto_24x7_closed_bar_only"
        cash_symbol = "USDT"
        settlement_constraint = "Only verified available spot cash in the crypto rail is deployable; locked, staked or unsettled assets are excluded."
    if not price_as_of:
        price_as_of = generated_at
        freshness_status = "degraded"
        data_age_minutes: float | str = "not_computable"
    else:
        age = abs((parse_datetime(generated_at) - parse_datetime(price_as_of)).total_seconds()) / 60.0
        data_age_minutes = round(age, 2)
        freshness_status = "verified_realtime" if age <= 20 else "verified_recent_close" if age <= 720 else "stale"

    holdings = (context.get("portfolio_snapshot") or {}).get("top_holdings") or []
    cash_row = next((item for item in holdings if item.get("symbol") == cash_symbol), {})
    deployable_cash = safe_float(cash_row.get("current_value"), 0.0) or 0.0
    entry_end = record.get("entry_deadline") or record.get("latest_exit_or_review_date") or date_after(generated_at, 1)
    is_tactical = record.get("capital_sleeve") == "tactical_alpha_sleeve" or record.get("bucket") == "us_tactical_alpha_sleeve"
    if is_tactical:
        expected_holding = {"min_calendar_days": 1, "base_calendar_days": 14, "min_trading_days": 1, "base_trading_days": 10}
        max_holding = {"calendar_days": 30, "trading_days": 21}
        time_stop = "Exit or rebuild the thesis if no planned progress occurs within 10 trading days; leveraged ETFs retain their stricter absolute limit."
    else:
        expected_holding = {"min_calendar_days": 0, "base_calendar_days": 30, "min_trading_days": 0, "base_trading_days": 21}
        max_holding = {"calendar_days": 90, "trading_days": 63}
        time_stop = "Review at the stated DCA/thesis checkpoint; do not turn an untriggered tactical idea into an indefinite hold."
    risk_text = str(record.get("risk_decision") or "").lower()
    if "no_deploy" in risk_text or "block" in risk_text:
        action_allowed = "no_deploy"
    elif "conditional" in risk_text:
        action_allowed = "conditional_action"
    elif "paper" in risk_text:
        action_allowed = "paper_only"
    else:
        action_allowed = "watch"
    market_verified = freshness_status in {"verified_realtime", "verified_recent_close"}
    coverage_checked_at = str(price_as_of or generated_at)
    symbol = str(record.get("symbol") or "")
    explicit_coverage = (
        (context.get("candidate_coverage_matrix_by_symbol") or {}).get(symbol)
        or ((context.get("research_panel") or {}).get("candidate_coverage_matrix_by_symbol") or {}).get(symbol)
        or {}
    )

    def explicit_role_coverage(role: str) -> dict[str, Any]:
        item = explicit_coverage.get(role) if isinstance(explicit_coverage, dict) else None
        if isinstance(item, dict):
            symbols = {str(value).upper() for value in item.get("symbols") or []}
            if symbol.upper() in symbols and item.get("sources") and item.get("checked_at"):
                return item
        return {
            "status": "degraded",
            "checked_at": str(generated_at),
            "sources": [f"candidate_specific_{role}_evidence_missing"],
            "symbols": [symbol],
        }

    candidate_coverage_matrix = {
        "market": {
            "status": "verified_current" if market_verified else "degraded",
            "checked_at": coverage_checked_at,
            "sources": [price_source],
            "symbols": [str(record.get("symbol") or "")],
        },
        "official_event": explicit_role_coverage("official_event"),
        "social_news": explicit_role_coverage("social_news"),
        "asset_specific_risk": explicit_role_coverage("asset_specific_risk"),
    }
    coverage_verified = market_verified and all(
        candidate_coverage_matrix[role].get("status") == "verified_current"
        for role in ("official_event", "social_news", "asset_specific_risk")
    )
    if not coverage_verified:
        action_allowed = "no_deploy"
    observation_action = {
        "conditional_action": "conditional_action",
        "paper_only": "paper_only",
    }.get(action_allowed, "watch")
    impulse_signals = (context.get("impulse_capture_panel") or {}).get("signals") or []
    impulse_signal = next(
        (item for item in impulse_signals if str(item.get("symbol") or "").upper() == symbol.upper()),
        {},
    )
    impulse_action = impulse_signal.get("observation_action")
    if impulse_action in {"conditional_action", "risk_alert", "paper_only", "watch"}:
        observation_action = impulse_action
    impulse_monitoring_required = bool(
        is_tactical
        or asset_class != "us_equity_spot"
        or impulse_signal.get("requires_event_relay") is True
    )
    defaults = {
        "price_as_of": str(price_as_of),
        "price_source": price_source,
        "data_age_minutes": data_age_minutes,
        "data_freshness_status": freshness_status,
        "current_direct_decision": "do_not_enter_now",
        "current_state_already_evaluated": True,
        "primary_action_is_future_trigger": False,
        "decision_price_ceiling": "not_applicable_no_entry",
        "decision_valid_until": str(entry_end),
        "current_state_vector": {
            "returns_pct": {
                "1d": "missing_from_context_builder",
                "5d": "missing_from_context_builder",
                "20d": "missing_from_context_builder",
                "60d": "missing_from_context_builder",
                "90d": "missing_from_context_builder",
            },
            "realized_volatility": "missing_from_context_builder",
            "volume_ratio_vs_20d": "missing_from_context_builder",
            "risk_regime": (context.get("macro_regime_panel") or {}).get("regime")
            or "degraded_context_only",
        },
        "historical_cycle_conditioning": {
            "data_start": "missing_from_context_builder",
            "data_end": str(parse_datetime(generated_at).date()),
            "analog_selection_rule_frozen": "No candidate-specific analog sample was available in the context builder; current entry is blocked.",
            "feature_vector": {"status": "missing_candidate_specific_history"},
            "sample_size": 0,
            "target_first_count": 0,
            "stop_first_count": 0,
            "unresolved_count": 0,
            "target_first_pct": 0,
            "stop_first_pct": 0,
            "median_forward_return_pct": "missing",
            "median_mfe_pct": "missing",
            "median_mae_pct": "missing",
            "pullback_before_target_pct": "missing",
            "missed_upside_if_wait_pct": "missing",
            "limitations": ["Candidate-specific historical conditioning was not supplied; do_not_enter_now is mandatory."],
        },
        "macro_event_conditioning": {
            "event_within_10_trading_days": False,
            "event_type": "not_certified_by_context_builder",
            "limitations": ["A fresh candidate-specific event calendar is required before any later entry decision."],
        },
        "derivatives_and_flow": {
            "status": "missing",
            "volume_and_liquidity": {"status": "missing_candidate_specific_snapshot"},
            "limitations": ["Missing derivatives/flow evidence is an entry blocker, not a neutral input."],
        },
        "buy_now_vs_wait": {
            "buy_now_expected_path": "Not estimable from the generic context builder.",
            "wait_expected_discount_pct": "missing",
            "wait_fill_probability_pct": "missing",
            "missed_upside_probability_pct": "missing",
            "time_in_market_cost_pct": "missing",
            "event_gap_risk_pct": "missing",
            "probability_weighted_preference": "wait",
        },
        "probability_provenance": {
            "probability_type": "missing_candidate_specific_history",
            "base_rate": "not_available",
            "adjustments": "none",
            "limitations": "Generic report generation cannot supply an entry probability.",
            "range_low_pct": 0,
            "range_high_pct": 100,
        },
        "entry_window_start": parse_datetime(generated_at).date().isoformat(),
        "entry_window_end": str(entry_end),
        "allowed_session": allowed_session,
        "entry_trigger": record.get("entry_range") or record.get("entry_zone") or "review_only_no_new_entry",
        "no_entry_if_not_triggered": True,
        "expected_holding_days": expected_holding,
        "max_holding_days": max_holding,
        "target_1_price_or_scenario": record.get("target_range") or record.get("target_price_or_scenario") or "thesis review",
        "target_1_evaluation_window": record.get("target_time_window") or record.get("time_window"),
        "target_2_price_or_scenario": record.get("target_price_or_scenario") or record.get("target_range") or "second thesis review",
        "target_2_evaluation_window": record.get("target_time_window") or record.get("time_window"),
        "time_stop": time_stop,
        "event_exit_date": "none_scheduled_verified_at_generation",
        "event_handling_plan": "Refresh earnings/FOMC/regulatory/unlock calendar before action; do not carry an unreviewed full tactical position through a binary event.",
        "post_exit_state": "cash_pending_manual_reallocation",
        "auto_relay_forbidden": True,
        "current_deployable_cash_usd": round(deployable_cash, 2),
        "settlement_constraint": settlement_constraint,
        "action_allowed": action_allowed,
        "execution_action": action_allowed,
        "observation_action": observation_action,
        "observation_trigger": impulse_signal.get("observation_trigger") or "Use a new timestamped observation signal when the declared closed-bar volume/price/flow trigger fires; never rewrite this baseline.",
        "observation_signal_id": impulse_signal.get("impulse_id") or "no_active_impulse_at_generation",
        "baseline_recommendation_id": impulse_signal.get("baseline_recommendation_id"),
        "impulse_monitoring_required": impulse_monitoring_required,
        "impulse_check_deadline": str(entry_end) if impulse_monitoring_required else "not_required_no_active_impulse_setup",
        "candidate_coverage_matrix": candidate_coverage_matrix,
        "baseline_frozen": True,
        "historical_baseline_mutation_forbidden": True,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    frozen = dict(record)
    frozen.update(defaults)
    baseline_fields = [
        "recommendation_id", "symbol", "generated_at", "price_as_of", "entry_window_start",
        "entry_window_end", "entry_trigger", "current_direct_decision",
        "decision_price_ceiling", "decision_valid_until", "current_state_vector",
        "historical_cycle_conditioning", "macro_event_conditioning",
        "derivatives_and_flow", "buy_now_vs_wait", "target_1_price_or_scenario",
        "target_2_price_or_scenario", "stop_or_invalid", "forecast_probability_pct",
        "execution_action", "observation_action",
    ]
    encoded = json.dumps(
        {field: frozen.get(field) for field in baseline_fields},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    defaults["baseline_snapshot_sha256"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return defaults


def build_recommendation_records(context: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    generated_at = context.get("generated_at") or utc_now()
    config_hash = current_config_hash()
    crypto_status = (context.get("crypto_market_panel") or {}).get("status") or "missing"
    crypto_assets = crypto_asset_map(context)
    asset_goal_status = (context.get("asset_goal_contribution_panel") or {}).get("status") or "missing"
    tactical = ((context.get("us_tactical_performance_panel") or {}).get("summary") or {})
    tactical_component = next(
        (
            item
            for item in tactical.get("current_components") or []
            if item.get("inclusion_reason") == "current_tactical_position"
        ),
        {},
    )
    fresh_scope = (context.get("fresh_market_intelligence_snapshot") or {}).get("asset_scope") or {}
    tactical_symbol = (
        tactical_component.get("symbol")
        or fresh_scope.get("current_tactical_symbol")
        or "CURRENT_TACTICAL"
    )
    tactical_price = tactical_component.get("current_price")
    tactical_data_quality = tactical_component.get("data_quality") or tactical.get("data_quality") or "degraded"

    sol_spec = dca_entry_spec(context, "SOL", (0.97, 1.01), (0.92, 0.96))
    ada_spec = dca_entry_spec(context, "ADA", (0.97, 1.01), (0.90, 0.95))
    night_spec = dca_entry_spec(context, "NIGHT", (0.97, 1.01), (0.88, 0.95))
    tactical_holding = source_portfolio_holding(context, str(tactical_symbol))
    tactical_price = tactical_price or ((tactical_holding.get("prices") or {}).get("current"))
    tactical_data_quality = (
        tactical_component.get("data_quality")
        or tactical_holding.get("data_quality")
        or tactical.get("data_quality")
        or "degraded"
    )
    tactical_ladder = dynamic_tactical_ladder(tactical_price, tactical_holding)
    tactical_quantity = tactical_component.get("quantity") or tactical_holding.get("quantity")
    tactical_value = (
        tactical_component.get("market_value")
        or tactical_component.get("current_value_usd")
        or tactical_holding.get("market_value")
        or ((tactical_holding.get("values") or {}).get("current") if tactical_holding else None)
    )
    if tactical_value is None and tactical_quantity is not None and tactical_price is not None:
        tactical_value = (safe_float(tactical_quantity, 0.0) or 0.0) * (safe_float(tactical_price, 0.0) or 0.0)
    tactical_quantity_float = safe_float(tactical_quantity)
    tactical_value_float = safe_float(tactical_value)
    tactical_position_plan = (
        f"confirmed tactical sleeve only; current {tactical_quantity or 'n/a'} shares / "
        f"{money(tactical_value)}; source position identified dynamically"
    )
    records = [
        recommendation_record(
            f"{run_id}-SOL-DCA",
            run_id,
            generated_at,
            config_hash,
            "crypto",
            "SOLUSDT",
            "long_term_dca",
            "dca_plan",
            "add",
            "7-14d tactical review / monthly DCA / 5-10y thesis",
            crypto_status,
            "human_confirmation_required",
            "research_only",
            "conditional_action",
            entry_range=f"near {sol_spec['near_range']}; pullback {sol_spec['optimal_range']}",
            secondary_entry=sol_spec["near_range"],
            optimal_entry=sol_spec["optimal_range"],
            entry_deadline=date_after(generated_at, 14),
            target_range="long-term accelerator contribution; quarterly thesis review",
            target_price_or_scenario="increase 5-10y 10x goal probability through SOL growth + staking compounding if thesis remains verified",
            target_time_window="monthly DCA review; quarterly thesis review; 5-10y goal measurement",
            latest_exit_or_review_date=date_after(generated_at, 14),
            stop_or_invalid="pause if BTC/ETH risk appetite breaks further or SOL liquidity/chain thesis deteriorates",
            forecast_invalid_if="macro/fund-flow risk worsens and SOL loses relative strength versus ADA/ETH",
            position_size_plan=sol_spec["position_size_plan"],
            planned_amount_usd_low=sol_spec["monthly_low_usd"],
            planned_amount_usd_high=sol_spec["monthly_high_usd"],
            planned_near_amount_usd=sol_spec["monthly_low_usd"],
            planned_pullback_add_usd=max(sol_spec["monthly_high_usd"] - sol_spec["monthly_low_usd"], 0.0),
            capital_sleeve="crypto_dca_sleeve",
            cash_rail_source="crypto_rail_monthly_dca_confirmed_or_pending",
            action_source="context_dynamic_dca_builder",
            long_horizon_timing_decision=sol_spec.get("long_horizon_timing_decision"),
            dca_timing_decision=sol_spec.get("dca_timing_decision"),
            staking_wait_cost_usd=sol_spec.get("staking_wait_cost_usd"),
            required_pullback_to_wait_pct=sol_spec.get("required_pullback_to_wait_pct"),
            time_in_market_bias_score=sol_spec.get("time_in_market_bias_score"),
            wait_requires_specific_pullback_trigger=sol_spec.get("wait_requires_specific_pullback_trigger"),
            front_load_extra_months=sol_spec.get("front_load_extra_months"),
            front_load_amount_usd=sol_spec.get("front_load_amount_usd"),
            near_term_total_budget_usd=sol_spec.get("near_term_total_budget_usd"),
            long_term_low_value_zone_status=sol_spec.get("long_term_low_value_zone_status"),
            low_value_evidence_summary=sol_spec.get("low_value_evidence_summary"),
            early_entry_benefit_summary=sol_spec.get("early_entry_benefit_summary"),
            dynamic_buy_strategy=sol_spec.get("dynamic_buy_strategy"),
            waiting_burden_of_proof_comment=sol_spec.get("waiting_burden_of_proof_comment"),
            front_load_reason=sol_spec.get("front_load_reason"),
            minimum_extra_discount_required_to_wait_pct=sol_spec.get("minimum_extra_discount_required_to_wait_pct"),
            cash_idle_drag_comment=sol_spec.get("cash_idle_drag_comment"),
            long_term_low_value_entry_panel=sol_spec.get("long_term_low_value_entry_panel"),
            long_term_price_scenario=scenario_summary_text(context, "SOL"),
            outcome_evaluation_method="not_triggered if entry zones do not fill by entry_deadline; otherwise review DCA thesis, drawdown, and relative strength at latest_exit_or_review_date",
            thesis_confidence_pct=72,
        ),
        recommendation_record(
            f"{run_id}-ADA-DCA",
            run_id,
            generated_at,
            config_hash,
            "crypto",
            "ADAUSDT",
            "long_term_dca_satellite",
            "dca_plan",
            "add_small",
            "14-30d tactical review / monthly DCA / 5-10y thesis",
            crypto_status,
            "human_confirmation_required",
            "research_only",
            "conditional_action",
            entry_range=f"near {ada_spec['near_range']}; pullback {ada_spec['optimal_range']}",
            secondary_entry=ada_spec["near_range"],
            optimal_entry=ada_spec["optimal_range"],
            entry_deadline=date_after(generated_at, 21),
            target_range="deep-value staking satellite contribution; quarterly thesis review",
            target_price_or_scenario="deep-value satellite improves goal convexity if ADA discount closes while staking remains sustainable",
            target_time_window="monthly DCA review; quarterly thesis review; 5-10y goal measurement",
            latest_exit_or_review_date=date_after(generated_at, 30),
            stop_or_invalid="do not raise size if ecosystem/liquidity remains materially weaker than SOL",
            forecast_invalid_if="ADA fails to improve ecosystem/fund-flow evidence while SOL remains stronger",
            position_size_plan=ada_spec["position_size_plan"],
            planned_amount_usd_low=ada_spec["monthly_low_usd"],
            planned_amount_usd_high=ada_spec["monthly_high_usd"],
            planned_near_amount_usd=ada_spec["monthly_low_usd"],
            planned_pullback_add_usd=max(ada_spec["monthly_high_usd"] - ada_spec["monthly_low_usd"], 0.0),
            capital_sleeve="crypto_dca_sleeve",
            cash_rail_source="crypto_rail_monthly_dca_confirmed_or_pending",
            action_source="context_dynamic_dca_builder",
            long_horizon_timing_decision=ada_spec.get("long_horizon_timing_decision"),
            dca_timing_decision=ada_spec.get("dca_timing_decision"),
            staking_wait_cost_usd=ada_spec.get("staking_wait_cost_usd"),
            required_pullback_to_wait_pct=ada_spec.get("required_pullback_to_wait_pct"),
            time_in_market_bias_score=ada_spec.get("time_in_market_bias_score"),
            wait_requires_specific_pullback_trigger=ada_spec.get("wait_requires_specific_pullback_trigger"),
            front_load_extra_months=ada_spec.get("front_load_extra_months"),
            front_load_amount_usd=ada_spec.get("front_load_amount_usd"),
            near_term_total_budget_usd=ada_spec.get("near_term_total_budget_usd"),
            long_term_low_value_zone_status=ada_spec.get("long_term_low_value_zone_status"),
            low_value_evidence_summary=ada_spec.get("low_value_evidence_summary"),
            early_entry_benefit_summary=ada_spec.get("early_entry_benefit_summary"),
            dynamic_buy_strategy=ada_spec.get("dynamic_buy_strategy"),
            waiting_burden_of_proof_comment=ada_spec.get("waiting_burden_of_proof_comment"),
            front_load_reason=ada_spec.get("front_load_reason"),
            minimum_extra_discount_required_to_wait_pct=ada_spec.get("minimum_extra_discount_required_to_wait_pct"),
            cash_idle_drag_comment=ada_spec.get("cash_idle_drag_comment"),
            long_term_low_value_entry_panel=ada_spec.get("long_term_low_value_entry_panel"),
            long_term_price_scenario=scenario_summary_text(context, "ADA"),
            outcome_evaluation_method="not_triggered if entry zones do not fill by entry_deadline; otherwise review discount, staking yield, and ecosystem evidence at latest_exit_or_review_date",
            thesis_confidence_pct=63,
        ),
        recommendation_record(
            f"{run_id}-NIGHT-WATCH",
            run_id,
            generated_at,
            config_hash,
            "crypto",
            "NIGHTUSDT",
            "tail_convexity",
            "watch",
            "hold_existing_only",
            "30d",
            crypto_status,
            "research_panel_required_before_add",
            "research_only",
            "no_deploy",
            entry_range=f"watch only; tail add only near {night_spec['optimal_range']} if data improves",
            secondary_entry="no near-price add",
            optimal_entry=night_spec["optimal_range"],
            entry_deadline=date_after(generated_at, 30),
            target_range="5-10y high-convexity tail only; no short-term target without verified liquidity/unlock data",
            target_price_or_scenario="tail_convexity only if official roadmap, float, DUST demand, and liquidity evidence improve",
            target_time_window="30d data refresh / quarterly thesis review / 5-10y tail option",
            latest_exit_or_review_date=date_after(generated_at, 30),
            stop_or_invalid="block new add if float/unlock/liquidity remain missing or disputed",
            forecast_invalid_if="official thesis/floating supply/liquidity evidence fails to improve",
            position_size_plan=night_spec["position_size_plan"],
            planned_amount_usd_low=night_spec["monthly_low_usd"],
            planned_amount_usd_high=night_spec["monthly_high_usd"],
            planned_near_amount_usd=night_spec["monthly_low_usd"],
            planned_pullback_add_usd=max(night_spec["monthly_high_usd"] - night_spec["monthly_low_usd"], 0.0),
            capital_sleeve="crypto_tail_convexity_sleeve",
            cash_rail_source="crypto_rail_existing_position_only_until_verified",
            action_source="context_dynamic_dca_builder",
            long_horizon_timing_decision=night_spec.get("long_horizon_timing_decision"),
            dca_timing_decision=night_spec.get("dca_timing_decision"),
            staking_wait_cost_usd=night_spec.get("staking_wait_cost_usd"),
            required_pullback_to_wait_pct=night_spec.get("required_pullback_to_wait_pct"),
            time_in_market_bias_score=night_spec.get("time_in_market_bias_score"),
            wait_requires_specific_pullback_trigger=night_spec.get("wait_requires_specific_pullback_trigger"),
            front_load_extra_months=night_spec.get("front_load_extra_months"),
            front_load_amount_usd=night_spec.get("front_load_amount_usd"),
            near_term_total_budget_usd=night_spec.get("near_term_total_budget_usd"),
            long_term_low_value_zone_status=night_spec.get("long_term_low_value_zone_status"),
            low_value_evidence_summary=night_spec.get("low_value_evidence_summary"),
            early_entry_benefit_summary=night_spec.get("early_entry_benefit_summary"),
            dynamic_buy_strategy=night_spec.get("dynamic_buy_strategy"),
            waiting_burden_of_proof_comment=night_spec.get("waiting_burden_of_proof_comment"),
            front_load_reason=night_spec.get("front_load_reason"),
            minimum_extra_discount_required_to_wait_pct=night_spec.get("minimum_extra_discount_required_to_wait_pct"),
            cash_idle_drag_comment=night_spec.get("cash_idle_drag_comment"),
            long_term_low_value_entry_panel=night_spec.get("long_term_low_value_entry_panel"),
            long_term_price_scenario=scenario_summary_text(context, "NIGHT"),
            thesis="Existing tail can remain, but new large DCA needs verified float/unlock/liquidity and official thesis data.",
        ),
        recommendation_record(
            f"{run_id}-{tactical_symbol}-TACTICAL",
            run_id,
            generated_at,
            config_hash,
            "us_equity",
            tactical_symbol,
            "us_tactical_alpha_sleeve",
            "watch",
            "reunderwrite_before_buy_or_trim",
            "1-10 trading days",
            tactical_data_quality,
            "double_80_not_met_human_confirmation_required",
            "research_only",
            "watch_due_to_missing_pre_entry_downside_certification",
            forecast_probability_pct=62,
            execution_readiness_score=78,
            target_before_stop_probability_pct=55,
            stop_before_target_probability_pct=45,
            expected_mae_pct_5d_10d_20d={"5d": -8, "10d": -12, "20d": -18},
            stress_gap_pct=-20,
            reward_risk_ratio=2.0,
            binary_event_calendar=["fresh_event_calendar_required_before_execution"],
            financing_and_dilution_snapshot="not_certified_by_context_builder; run current SEC filing review",
            capital_funding_gap_status="unknown_material",
            contract_quality_snapshot="not_certified_by_context_builder",
            valuation_expectation_risk="not_certified_by_context_builder",
            position_size_from_stress_loss="zero until pre-entry downside gate passes with fresh evidence",
            entry_range=f"secondary {tactical_ladder['secondary_entry']}; optimal {tactical_ladder['optimal_entry']}",
            secondary_entry=tactical_ladder["secondary_entry"],
            optimal_entry=tactical_ladder["optimal_entry"],
            entry_deadline=date_after(generated_at, 3),
            target_range=f"trim {tactical_ladder['trim']}; full exit/strong trim {tactical_ladder['full_exit']}",
            target_price_or_scenario="2-4 week tactical trend continuation or cash relay into stronger verified candidate",
            target_time_window="1-5 trading day entry/trim review; max 10 trading days holding review",
            trim_or_partial_exit=tactical_ladder["trim"],
            full_exit_or_invalidation=f"{tactical_ladder['full_exit']}; break below {tactical_ladder['invalid']} invalidates",
            latest_exit_or_review_date=date_after(generated_at, 14),
            stop_or_invalid=f"break below {tactical_ladder['invalid']} or holding exceeds 10 trading days without trend confirmation",
            forecast_invalid_if="a stronger candidate passes true probability >=80 and readiness >=80, or semiconductor trend breaks",
            position_size_plan=tactical_position_plan,
            current_quantity=tactical_quantity_float,
            current_market_value_usd=tactical_value_float,
            capital_sleeve="tactical_alpha_sleeve",
            cash_rail_source="us_equity_cash_or_current_tactical_position_confirmed",
            action_source="context_dynamic_us_tactical_builder",
            outcome_evaluation_method="hit if target/trim zone is reached before latest_exit_or_review_date; failed if stop/invalidation triggers first; otherwise expired/not_triggered on review",
        ),
        recommendation_record(
            f"{run_id}-ETH-NOADD",
            run_id,
            generated_at,
            config_hash,
            "crypto",
            "ETH/lcETH",
            "existing_core_overweight",
            "hold",
            "no_new_dca",
            "30-90d",
            asset_goal_status,
            "human_confirmation_required_for_any_change",
            "research_only",
            "block_new_add",
            target_time_window="30-90d concentration and thesis review",
            latest_exit_or_review_date=date_after(generated_at, 60),
            position_size_plan="hold existing lcETH; no new DCA until concentration improves",
            action_source="asset_goal_contribution_panel",
            long_term_price_scenario=scenario_summary_text(context, "ETH"),
            thesis="ETH/lcETH quality remains, but portfolio concentration and low staking yield make it a goal drag for new DCA.",
        ),
        recommendation_record(
            f"{run_id}-CRCL-HOLD",
            run_id,
            generated_at,
            config_hash,
            "us_equity",
            "CRCL",
            "protected_long_term_holding",
            "hold",
            "hold_protected",
            "30-90d",
            "verified_or_degraded_public_equity_data",
            "human_confirmation_required_for_any_trim",
            "research_only",
            "hold",
            target_time_window="30-90d protected long-term thesis review",
            latest_exit_or_review_date=date_after(generated_at, 60),
            position_size_plan="hold protected CRCL; do not use as routine tactical funding source",
            action_source="protected_long_term_holding_policy",
            thesis="Protected Circle exposure is not a routine funding source for tactical rotation.",
        ),
    ]
    for record in records:
        sleeve = str(record.get("capital_sleeve") or "")
        is_formal_candidate = sleeve in {"tactical_alpha_sleeve", "crypto_dca_sleeve", "crypto_tail_convexity_sleeve"}
        if is_formal_candidate:
            for key, value in execution_calendar_defaults(context, record).items():
                record.setdefault(key, value)
    return records


def refresh_recommendation_summary(context: dict[str, Any]) -> None:
    ledger_path = MANUAL_ROOT / "recommendations" / "recommendation_history.json"
    script = MANUAL_ROOT / "scripts" / "recommendation_history.py"
    try:
        raw = subprocess.check_output(
            [sys.executable, str(script), "--path", str(ledger_path), "export-panel", "--format", "json"],
            text=True,
        )
        panel = json.loads(raw)
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
        context["recommendation_history_summary"] = {
            "ledger_path": str(ledger_path),
            "status": "ok",
            "summary": panel.get("summary"),
            "due_review": panel.get("due_review", []),
            "calibration": panel.get("calibration", {}),
            "pending_proposed_changes": panel.get("pending_proposed_changes", []),
            "pending_recommendations": pending,
        }
    except Exception as exc:  # noqa: BLE001
        context["recommendation_history_write"] = {
            "status": "refresh_failed",
            "error": str(exc),
        }


def write_recommendation_records(context: dict[str, Any], run_id: str, enabled: bool = True) -> dict[str, Any]:
    records = build_recommendation_records(context, run_id)
    readiness = context.get("report_readiness") or {}
    if readiness.get("execute_now_allowed") is not True:
        blocked_records = [
            record.get("recommendation_id")
            for record in records
            if record.get("action") == "execute_now"
            or record.get("risk_decision") == "execute_now"
            or record.get("max_allowed_action") == "execute_now"
        ]
        if blocked_records:
            raise ValueError(
                "recommendation records attempted execute_now while report_readiness blocks it: "
                + ", ".join(str(item) for item in blocked_records)
            )
    record_path = TMP_ROOT / f"{run_id}-recommendation-records.json"
    write_json(record_path, {"recommendations": records})
    calendar_records = [record for record in records if record.get("execution_action")]
    calendar_record_path = TMP_ROOT / f"{run_id}-execution-calendar-records.json"
    write_json(calendar_record_path, {"recommendations": calendar_records})
    gate_proc = subprocess.run(
        [
            sys.executable,
            str(MANUAL_ROOT / "scripts" / "recommendation_execution_calendar_gate.py"),
            "--records",
            str(calendar_record_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        gate_payload = json.loads(gate_proc.stdout)
    except json.JSONDecodeError:
        gate_payload = {"status": "blocked", "error": gate_proc.stderr or "calendar gate returned non-JSON"}
    context["recommendation_execution_calendar_gate"] = gate_payload
    if gate_proc.returncode != 0 or gate_payload.get("status") != "passed":
        result = {
            "status": "failed_calendar_gate",
            "record_file": str(record_path),
            "calendar_record_file": str(calendar_record_path),
            "written_count": 0,
            "calendar_gate": gate_payload,
        }
        context["recommendation_history_write"] = result
        return result
    for record in calendar_records:
        record["calendar_gate_status_at_publish"] = "passed"
    write_json(record_path, {"recommendations": records})
    if not enabled:
        result = {
            "status": "skipped",
            "reason": "disabled by CLI",
            "record_file": str(record_path),
            "calendar_record_file": str(calendar_record_path),
            "calendar_gate": gate_payload,
        }
        context["recommendation_history_write"] = result
        return result
    ledger_path = MANUAL_ROOT / "recommendations" / "recommendation_history.json"
    script = MANUAL_ROOT / "scripts" / "recommendation_history.py"
    try:
        subprocess.check_output(
            [
                sys.executable,
                str(script),
                "--path",
                str(ledger_path),
                "add",
                "--record-file",
                str(record_path),
                "--replace",
                "--supersede-open-equivalent",
                "--supersede-reason",
                "Superseded by a newer equivalent recommendation generated by the latest manual report; keep only the latest pending plan active for review.",
            ],
            text=True,
            stderr=subprocess.PIPE,
        )
        result = {
            "status": "ok",
            "record_file": str(record_path),
            "calendar_record_file": str(calendar_record_path),
            "ledger_path": str(ledger_path),
            "written_count": len(records),
            "recommendation_ids": [record["recommendation_id"] for record in records],
            "calendar_gate": gate_payload,
        }
    except Exception as exc:  # noqa: BLE001
        result = {
            "status": "failed",
            "record_file": str(record_path),
            "ledger_path": str(ledger_path),
            "written_count": 0,
            "error": str(exc),
        }
    context["recommendation_history_write"] = result
    refresh_recommendation_summary(context)
    return result


def refresh_outcome_review_drafts(context: dict[str, Any], fetch_market_data: bool = False) -> dict[str, Any]:
    ledger_path = MANUAL_ROOT / "recommendations" / "recommendation_history.json"
    script = MANUAL_ROOT / "scripts" / "recommendation_outcome_reviewer.py"
    try:
        cmd = [
            sys.executable,
            str(script),
            "--path",
            str(ledger_path),
            "--format",
            "json",
        ]
        if fetch_market_data:
            cmd.append("--fetch-market-data")
        raw = subprocess.check_output(cmd, text=True, stderr=subprocess.PIPE, timeout=60)
        panel = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        panel = {
            "generated_at": utc_now(),
            "status": "failed",
            "error": str(exc),
            "note": "Outcome review draft generation failed; do not upgrade execute_now.",
        }
    context["recommendation_outcome_review_drafts"] = panel
    return panel


def refresh_strategy_promotion_evidence(context: dict[str, Any]) -> dict[str, Any]:
    script = MANUAL_ROOT / "scripts" / "strategy_promotion_evaluator.py"
    try:
        raw = subprocess.check_output(
            [
                sys.executable,
                str(script),
                "--format",
                "json",
            ],
            text=True,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        panel = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        panel = {
            "generated_at": utc_now(),
            "status": "failed",
            "error": str(exc),
            "strategy_evidence_status": "failed",
            "tactical_promotion_gate_passed": False,
            "max_real_action_from_evidence": "paper_only",
            "tactical_failed_gates": ["strategy_promotion_evaluator_failed"],
            "strategies": [],
        }
    context["strategy_promotion_evidence_panel"] = panel
    readiness = context.setdefault("report_readiness", {})
    if panel.get("tactical_promotion_gate_passed") is not True:
        readiness["execute_now_allowed"] = False
        current_max = readiness.get("max_allowed_action")
        if current_max in {None, "", "execute_now", "conditional_action"}:
            readiness["max_allowed_action"] = panel.get("max_real_action_from_evidence") or "paper_only"
        failed_gates = list(readiness.get("failed_gates") or [])
        for gate in panel.get("tactical_failed_gates") or ["strategy_promotion_evidence_insufficient"]:
            if gate not in failed_gates:
                failed_gates.append(gate)
        if "strategy_promotion_evidence_gate_failed" not in failed_gates:
            failed_gates.append("strategy_promotion_evidence_gate_failed")
        readiness["failed_gates"] = failed_gates
    return panel


def refresh_strategy_iteration_backlog(context: dict[str, Any]) -> dict[str, Any]:
    script = MANUAL_ROOT / "scripts" / "strategy_iteration_backlog.py"
    try:
        raw = subprocess.check_output(
            [
                sys.executable,
                str(script),
                "--format",
                "json",
            ],
            text=True,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        panel = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        panel = {
            "generated_at": utc_now(),
            "status": "failed",
            "error": str(exc),
            "candidate_strategies": [],
            "summary": {
                "candidate_strategy_count": 0,
                "execute_now_allowed": False,
                "max_allowed_action": "watch",
            },
        }
    context["strategy_iteration_backlog_panel"] = panel
    return panel


def collect_execute_now_candidates(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect candidate-like evidence that could satisfy the final double-80 gate.

    This does not promote anything by itself. It only extracts structured
    candidates from active handoffs and US-open scanner summaries so the final
    readiness gate can explain why execute_now is or is not allowed.
    """

    candidates: list[dict[str, Any]] = []

    for handoff in context.get("latest_active_alpha_handoffs") or []:
        if not isinstance(handoff, dict):
            continue
        candidates.append({
            "source": "active_handoff",
            "symbol": handoff.get("symbol"),
            "candidate_type": handoff.get("candidate_type"),
            "monitor_recommendation": handoff.get("monitor_recommendation"),
            "max_allowed_action": handoff.get("max_allowed_action"),
            "forecast_probability_pct": handoff.get("forecast_probability_pct"),
            "execution_readiness_score": handoff.get("execution_readiness_score"),
            "data_quality_status": handoff.get("data_quality_status"),
            "research_panel_missing": handoff.get("research_panel_missing"),
            "research_committee_degraded": handoff.get("research_committee_degraded"),
            "live_orders_enabled": handoff.get("live_orders_enabled"),
            "manual_review_required": bool(
                handoff.get("requires_manual_review_before_real_money")
                or handoff.get("requires_manual_review")
                or handoff.get("manual_review_required")
            ),
            "risk_adjusted_path": handoff.get("risk_adjusted_path"),
            "path": handoff.get("path"),
        })

    us_summary = ((context.get("us_open_dynamic_scanner_panel") or {}).get("summary") or {})
    for item in us_summary.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        candidates.append({
            "source": "us_open_dynamic_scanner",
            "symbol": item.get("symbol"),
            "candidate_type": "us_open_scan",
            "monitor_recommendation": item.get("monitor_recommendation"),
            "max_allowed_action": us_summary.get("max_allowed_action"),
            "forecast_probability_pct": item.get("forecast_probability_pct"),
            "execution_readiness_score": item.get("execution_readiness_score"),
            "data_quality_status": item.get("data_quality_status"),
            "research_panel_missing": us_summary.get("research_panel_missing"),
            "research_committee_degraded": us_summary.get("research_committee_degraded"),
            "live_orders_enabled": False,
            "manual_review_required": bool(us_summary.get("requires_manual_review")),
            "risk_adjusted_path": item.get("risk_adjusted_path"),
            "path": (context.get("us_open_dynamic_scanner_panel") or {}).get("path"),
        })

    return candidates


def finalize_execute_now_readiness(context: dict[str, Any]) -> dict[str, Any]:
    """Open a positive execute_now path only when every hard gate is proven.

    Current evidence is expected to fail this gate. The purpose is to avoid a
    permanent hard-coded false while preserving all conservative blockers until
    research, promotion, calibration, target probability, readiness, data
    quality, and human-review prerequisites are all present.
    """

    readiness = context.setdefault("report_readiness", {})
    research = context.get("research_panel") or {}
    research_validation = context.get("research_panel_validation") or {}
    promotion = context.get("strategy_promotion_evidence_panel") or {}
    missing_panel = context.get("missing_data_downgrade_panel") or []
    recommendation_write = context.get("recommendation_history_write") or {}
    fresh_market = context.get("fresh_market_intelligence_snapshot") or {}
    candidates = collect_execute_now_candidates(context)

    failed_gates = list(readiness.get("failed_gates") or [])
    blockers: list[str] = []

    def fail(gate: str) -> None:
        if gate not in failed_gates:
            failed_gates.append(gate)
        if gate not in blockers:
            blockers.append(gate)

    if research_validation.get("status") != "verified":
        fail("research_panel_not_verified")
    if research.get("research_committee_degraded") is True:
        fail("research_committee_degraded")
    old_thesis_reuse_allowed = research.get("old_thesis_reuse_allowed")
    prior_thesis_status = research.get("prior_thesis_status")

    if promotion.get("tactical_promotion_gate_passed") is not True:
        fail("strategy_promotion_evidence_gate_failed")
        for gate in promotion.get("tactical_failed_gates") or []:
            fail(str(gate))
    if promotion.get("max_real_action_from_evidence") not in {"conditional_action", "execute_now"}:
        fail("strategy_evidence_action_ceiling_below_live_candidate")

    if recommendation_write.get("status") != "ok" or as_float(recommendation_write.get("written_count"), 0.0) <= 0:
        fail("recommendation_history_write_not_confirmed")

    if not fresh_market or fresh_market.get("market_intelligence_degraded") is True:
        fail("market_intelligence_degraded")
    if fresh_market and fresh_market.get("background_loop_started") is not False:
        fail("manual_dispatch_boundary_not_proven")
    if fresh_market and fresh_market.get("live_orders_enabled") is not False:
        fail("fresh_market_live_order_flag_not_false")

    blocking_missing = [
        item for item in missing_panel
        if isinstance(item, dict) and item.get("impact") == "block"
    ]
    if blocking_missing:
        fail("missing_data_block_present")

    qualified_candidates: list[dict[str, Any]] = []
    for item in candidates:
        probability = as_float(item.get("forecast_probability_pct"), 0.0) or 0.0
        readiness_score = as_float(item.get("execution_readiness_score"), 0.0) or 0.0
        quality = str(item.get("data_quality_status") or "").lower()
        action = item.get("max_allowed_action") or item.get("monitor_recommendation")
        candidate_failed = []
        if probability < 80:
            candidate_failed.append("forecast_probability_below_80")
        if readiness_score < 80:
            candidate_failed.append("execution_readiness_below_80")
        if item.get("research_panel_missing") or item.get("research_committee_degraded"):
            candidate_failed.append("candidate_research_panel_degraded")
        if item.get("live_orders_enabled") is not False:
            candidate_failed.append("candidate_live_order_flag_not_false")
        if item.get("manual_review_required") is not True:
            candidate_failed.append("candidate_manual_review_draft_missing")
        if any(token in quality for token in ["disputed", "stale", "missing"]):
            candidate_failed.append("candidate_data_quality_blocked")
        if action not in {"conditional_action", "execute_now", "small_probe_review", "paper_only"}:
            candidate_failed.append("candidate_action_not_promotable")
        annotated = dict(item)
        annotated["forecast_probability_pct"] = probability
        annotated["execution_readiness_score"] = readiness_score
        annotated["target_achievement_gate_passed"] = not candidate_failed
        annotated["target_achievement_failed_gates"] = candidate_failed
        if not candidate_failed:
            qualified_candidates.append(annotated)

    if not candidates:
        fail("no_structured_tactical_candidates")
    elif not qualified_candidates:
        fail("no_candidate_passed_double_80_target_achievement_gate")

    execute_now_allowed = not blockers
    readiness.update({
        "execute_now_allowed": execute_now_allowed,
        "execute_now_gate_version": "positive-path-v1",
        "execute_now_gate_checked_at": utc_now(),
        "execute_now_candidate_count": len(candidates),
        "execute_now_qualified_candidate_count": len(qualified_candidates),
        "execute_now_qualified_candidates": qualified_candidates[:3],
        "failed_gates": failed_gates,
        "gate_summary": {
            "research_panel_status": research_validation.get("status"),
            "research_committee_degraded": research.get("research_committee_degraded"),
            "strategy_promotion_gate_passed": promotion.get("tactical_promotion_gate_passed"),
            "max_real_action_from_evidence": promotion.get("max_real_action_from_evidence"),
            "blocking_missing_data_count": len(blocking_missing),
            "recommendation_history_write_status": recommendation_write.get("status"),
            "recommendation_history_written_count": recommendation_write.get("written_count"),
            "market_intelligence_degraded": fresh_market.get("market_intelligence_degraded"),
            "candidate_count": len(candidates),
            "qualified_candidate_count": len(qualified_candidates),
            "prior_thesis_status": prior_thesis_status,
            "old_thesis_reuse_allowed": old_thesis_reuse_allowed,
        },
    })
    if execute_now_allowed:
        readiness["max_allowed_action"] = "execute_now"
        readiness["reason"] = (
            "All hard gates passed: verified research committee, promotion evidence, "
            "double-80 target achievement, data quality, and manual-review prerequisites."
        )
    else:
        current_max = readiness.get("max_allowed_action")
        evidence_max = promotion.get("max_real_action_from_evidence")
        if evidence_max in {"paper_only", "watch", "no_deploy", "risk_alert"}:
            readiness["max_allowed_action"] = evidence_max
        elif current_max in {None, "", "execute_now"}:
            readiness["max_allowed_action"] = "conditional_action_or_watch"
        readiness["reason"] = "execute_now blocked by: " + ", ".join(blockers or failed_gates or ["unknown_gate"])
    return readiness


def refresh_goal_execution_dashboard(context: dict[str, Any], context_path: Path, run_id: str) -> dict[str, Any]:
    script = MANUAL_ROOT / "scripts" / "goal_execution_dashboard.py"
    output_path = TMP_ROOT / f"{run_id}-goal-execution-dashboard.json"
    markdown_path = TMP_ROOT / f"{run_id}-goal-execution-dashboard.md"
    try:
        raw = subprocess.check_output(
            [
                sys.executable,
                str(script),
                "--context-json",
                str(context_path),
                "--output",
                str(output_path),
                "--markdown-output",
                str(markdown_path),
                "--format",
                "json",
            ],
            text=True,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        panel = json.loads(raw)
        panel["dashboard_json"] = str(output_path)
        panel["dashboard_markdown"] = str(markdown_path)
    except Exception as exc:  # noqa: BLE001
        panel = {
            "generated_at": utc_now(),
            "status": "failed",
            "error": str(exc),
            "dashboard_version": "goal-execution-dashboard-v1",
            "evidence_and_gate_state": {
                "report_max_allowed_action": "watch",
                "short_term_tactical_max_allowed_action": "watch",
                "crypto_dca_max_allowed_action": "watch",
                "execute_now_allowed": False,
            },
            "execution_backlog": [
                {
                    "priority": "P0",
                    "area": "goal execution dashboard",
                    "task": "Fix dashboard generation before claiming the operating plan is fully bound to evidence.",
                    "max_action": "watch",
                }
            ],
        }
    context["goal_execution_dashboard"] = panel
    return panel


def run_integrity_audit(report_path: Path, context_path: Path, run_id: str, timeout: int = 60) -> dict[str, Any]:
    audit_path = TMP_ROOT / f"{run_id}-report-integrity-audit.json"
    cmd = [
        sys.executable,
        str(MANUAL_ROOT / "scripts" / "report_integrity_audit.py"),
        "--report-md",
        str(report_path),
        "--context-json",
        str(context_path),
        "--run-id",
        run_id,
        "--format",
        "json",
    ]
    try:
        raw = subprocess.check_output(cmd, text=True, stderr=subprocess.PIPE, timeout=timeout)
        payload = json.loads(raw)
        write_json(audit_path, payload)
        payload["audit_path"] = str(audit_path)
        return payload
    except subprocess.CalledProcessError as exc:
        payload: dict[str, Any]
        try:
            payload = json.loads(exc.output)
        except Exception:  # noqa: BLE001
            payload = {
                "status": "failed",
                "error": exc.stderr or exc.output or str(exc),
            }
        write_json(audit_path, payload)
        payload["audit_path"] = str(audit_path)
        return payload
    except Exception as exc:  # noqa: BLE001
        payload = {
            "status": "failed",
            "error": str(exc),
            "audit_path": str(audit_path),
        }
        write_json(audit_path, payload)
        return payload


def run_objective_coverage_audit(
    report_path: Path,
    context_path: Path,
    run_id: str,
    monthly_dca: float,
    timeout: int = 60,
) -> dict[str, Any]:
    audit_path = TMP_ROOT / f"{run_id}-objective-coverage-audit.json"
    cmd = [
        sys.executable,
        str(MANUAL_ROOT / "scripts" / "objective_coverage_audit.py"),
        "--report-md",
        str(report_path),
        "--context-json",
        str(context_path),
        "--run-id",
        run_id,
        "--expected-monthly-dca",
        str(monthly_dca),
        "--format",
        "json",
    ]
    try:
        raw = subprocess.check_output(cmd, text=True, stderr=subprocess.PIPE, timeout=timeout)
        payload = json.loads(raw)
        write_json(audit_path, payload)
        payload["audit_path"] = str(audit_path)
        return payload
    except subprocess.CalledProcessError as exc:
        try:
            payload = json.loads(exc.output)
        except Exception:  # noqa: BLE001
            payload = {
                "status": "failed",
                "error": exc.stderr or exc.output or str(exc),
            }
        write_json(audit_path, payload)
        payload["audit_path"] = str(audit_path)
        return payload
    except Exception as exc:  # noqa: BLE001
        payload = {
            "status": "failed",
            "error": str(exc),
            "audit_path": str(audit_path),
        }
        write_json(audit_path, payload)
        return payload


def render_integrity_audit_section(audit_result: dict[str, Any]) -> str:
    checks = audit_result.get("checks") or []
    failed = [item for item in checks if not item.get("passed") and item.get("severity") == "error"]
    warnings = [item for item in checks if not item.get("passed") and item.get("severity") == "warning"]
    rows = [
        ["status", f"`{audit_result.get('status')}`"],
        ["passed", f"`{audit_result.get('passed')}`"],
        ["failed", f"`{audit_result.get('failed')}`"],
        ["warnings", f"`{audit_result.get('warnings')}`"],
        ["audit_path", f"`{audit_result.get('audit_path')}`"],
    ]
    failed_rows = [
        [item.get("name"), item.get("severity"), short(item.get("evidence"), 120)]
        for item in failed[:8]
    ]
    warning_rows = [
        [item.get("name"), item.get("severity"), short(item.get("evidence"), 120)]
        for item in warnings[:8]
    ]
    plain_warning_rows = [
        [
            item.get("area"),
            item.get("source_check_id"),
            item.get("plain_meaning"),
            item.get("action_limit"),
        ]
        for item in build_warning_explanations(warnings)[:8]
    ]
    return "\n".join([
        "## Report Integrity Audit",
        "",
        "该审计检查报告结构是否完整。它不是投资结论本身；若出现 warning，表示对应结论需要降级，而不是系统自动下单。",
        "",
        markdown_table(["字段", "结果"], rows),
        "",
        "**失败项**",
        "",
        markdown_table(["check", "severity", "evidence"], failed_rows) if failed_rows else "- 无",
        "",
        "**警告项**",
        "",
        markdown_table(["check", "severity", "evidence"], warning_rows) if warning_rows else "- 无",
        "",
        "**警告项直白解释**",
        "",
        markdown_table(["领域", "检查项", "直白含义", "动作限制"], plain_warning_rows) if plain_warning_rows else "- 无",
        "",
        "这对你意味着什么：如果这里有 warning，报告仍可读，但对应行动不能升级为高确定性执行。",
    ])


def render_objective_coverage_audit_section(audit_result: dict[str, Any]) -> str:
    checks = audit_result.get("checks") or []
    failed = [item for item in checks if not item.get("passed") and item.get("severity") == "error"]
    warnings = [item for item in checks if not item.get("passed") and item.get("severity") == "warning"]
    paper = audit_result.get("paper_ledger_metrics") or {}
    rows = [
        ["status", f"`{audit_result.get('status')}`"],
        ["passed", f"`{audit_result.get('passed')}`"],
        ["failed", f"`{audit_result.get('failed')}`"],
        ["warnings", f"`{audit_result.get('warnings')}`"],
        ["audit_path", f"`{audit_result.get('audit_path')}`"],
        ["paper_closed_trades", f"`{paper.get('closed_count')}`"],
        ["paper_open_positions", f"`{paper.get('open_count')}`"],
        ["paper_net_return", pct(paper.get("net_return_pct"))],
        ["paper_win_rate", pct(paper.get("win_rate_pct"))],
    ]
    failed_rows = [
        [item.get("name"), item.get("severity"), short(item.get("evidence"), 120)]
        for item in failed[:10]
    ]
    warning_rows = [
        [item.get("name"), item.get("severity"), short(item.get("evidence"), 120)]
        for item in warnings[:10]
    ]
    plain_warning_rows = [
        [
            item.get("area"),
            item.get("source_check_id"),
            item.get("plain_meaning"),
            item.get("action_limit"),
        ]
        for item in build_warning_explanations(warnings)[:10]
    ]
    return "\n".join([
        "## Objective Coverage Audit",
        "",
        "该审计对照用户原始目标：5/10 年 10x、每月 DCA、美股战术 50%+、两路现金、动态战术仓和学习闭环。它不证明收益一定达成，只证明报告是否覆盖这些目标约束。",
        "",
        markdown_table(["字段", "结果"], rows),
        "",
        "**失败项**",
        "",
        markdown_table(["check", "severity", "evidence"], failed_rows) if failed_rows else "- 无",
        "",
        "**警告项**",
        "",
        markdown_table(["check", "severity", "evidence"], warning_rows) if warning_rows else "- 无",
        "",
        "**警告项直白解释**",
        "",
        markdown_table(["领域", "检查项", "直白含义", "动作限制"], plain_warning_rows) if plain_warning_rows else "- 无",
        "",
        "这对你意味着什么：如果目标覆盖审计有 warning，说明目标相关面板仍能运行，但某些证据还不足以支持更激进动作。",
    ])


def run_json_command(cmd: list[str], output_path: Path, step: str, required: bool, timeout: int = 180) -> dict[str, Any]:
    started = utc_now()
    progress_path = os.environ.get("MANUAL_PROGRESS_JSONL", "")
    def emit(event_type: str, status: str, detail: str = "") -> None:
        if not progress_path:
            return
        event = {
            "event_type": event_type,
            "occurred_at": utc_now(),
            "step_id": step,
            "status": status,
        }
        if detail:
            event["detail"] = detail
        try:
            with Path(progress_path).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                handle.flush()
        except OSError:
            # Progress must never change an investment research result.
            return
    emit("step_started", "running")
    try:
        raw = subprocess.check_output(cmd, text=True, stderr=subprocess.PIPE, timeout=timeout)
        payload = json.loads(raw)
        write_json(output_path, payload)
        result = {
            "step": step,
            "status": "ok",
            "output_path": str(output_path),
            "started_at": started,
            "finished_at": utc_now(),
        }
        emit("step_completed", "success")
        return result
    except Exception as exc:  # noqa: BLE001
        error_payload = {
            "generated_at": utc_now(),
            "status": "error",
            "data_quality": "missing",
            "step": step,
            "error": str(exc),
            "command": cmd,
        }
        if not required:
            write_json(output_path, error_payload)
            result = {
                "step": step,
                "status": "degraded",
                "output_path": str(output_path),
                "error": str(exc),
                "started_at": started,
                "finished_at": utc_now(),
            }
            emit("step_degraded", "degraded", str(exc))
            return result
        emit("step_failed", "failed", str(exc))
        raise RuntimeError(f"{step} failed: {exc}") from exc


def annotate_fresh_market_intelligence(
    context: dict[str, Any],
    paths: dict[str, Path | None],
    steps: list[dict[str, Any]],
    args: argparse.Namespace,
    run_id: str,
) -> dict[str, Any]:
    """Attach the manual-dispatch market refresh evidence to the context.

    The report generator may run with cached inputs, network failures, or
    skipped active scanners. This panel makes that visible and blocks new
    execute_now actions when the run did not actually refresh key market and
    sentiment inputs.
    """

    step_by_name = {str(item.get("step")): item for item in steps if isinstance(item, dict)}
    attempted = [
        "market_data_source_preflight",
        "portfolio_snapshot",
        "goal_projection",
        "macro_regime",
        "asset_goal_contribution",
    ]
    if not args.skip_active_scanners:
        attempted.extend([
            "crypto_multisource_snapshot",
            "crypto_impulse_capture",
            "us_open_dynamic_scanner",
        ])
    if args.use_existing_inputs:
        attempted = [
            "market_data_source_preflight_cache",
            "portfolio_snapshot_cache",
            "goal_projection_cache",
            "macro_regime_cache",
            "asset_goal_contribution_cache",
            "crypto_multisource_snapshot_cache",
            "crypto_impulse_capture_cache",
            "us_open_dynamic_scanner_cache",
        ]

    succeeded: list[str] = []
    failed: list[dict[str, Any]] = []
    for name in attempted:
        base_name = name.replace("_cache", "")
        path_key = {
            "portfolio_snapshot": "portfolio",
            "market_data_source_preflight": "preflight",
            "goal_projection": "goal",
            "macro_regime": "macro",
            "asset_goal_contribution": "asset_goal",
            "crypto_multisource_snapshot": "crypto",
            "crypto_impulse_capture": "impulse",
            "us_open_dynamic_scanner": "us_scanner",
        }.get(base_name)
        path = paths.get(path_key) if path_key else None
        step = step_by_name.get(base_name)
        if path and Path(path).exists() and (not step or step.get("status") in {None, "ok"}):
            succeeded.append(name)
        else:
            failed.append({
                "source": name,
                "reason": (step or {}).get("error") or "missing_or_not_run",
            })

    macro_summary = ((context.get("macro_regime_panel") or {}).get("summary") or {})
    crypto_summary = ((context.get("crypto_market_panel") or {}).get("summary") or {})
    us_summary = ((context.get("us_open_dynamic_scanner_panel") or {}).get("summary") or {})
    sentiment_parts = []
    fear = crypto_summary.get("fear_greed") or {}
    if fear:
        sentiment_parts.append(f"Fear & Greed={fear.get('value') or fear.get('score')} ({fear.get('classification') or fear.get('label')})")
    if us_summary.get("scan_status"):
        sentiment_parts.append(f"US scanner={us_summary.get('scan_status')}")
    if not sentiment_parts:
        sentiment_parts.append("sentiment inputs missing or only available through degraded context")

    crypto_assets = crypto_summary.get("assets") or []
    us_candidates = us_summary.get("candidates") or []
    degraded_reasons: list[str] = []
    internal_degraded_sources: list[dict[str, Any]] = []
    supplemental_degraded_sources: list[dict[str, Any]] = []

    def add_internal_degraded(source: str, reason: str, impact: str = "conditional only") -> None:
        reason = short(reason, 240)
        if not any(item.get("source") == source and item.get("reason") == reason for item in internal_degraded_sources):
            internal_degraded_sources.append({
                "source": source,
                "reason": reason,
                "impact": impact,
            })

    def add_supplemental_degraded(source: str, reason: str, impact: str = "smaller size / explain in report") -> None:
        reason = short(reason, 240)
        if not any(item.get("source") == source and item.get("reason") == reason for item in supplemental_degraded_sources):
            supplemental_degraded_sources.append({
                "source": source,
                "reason": reason,
                "impact": impact,
            })

    def read_path_json(key: str) -> dict[str, Any]:
        path = paths.get(key)
        if not path:
            return {}
        try:
            payload = load_json(path)
        except Exception as exc:  # noqa: BLE001
            add_internal_degraded(key, f"could_not_read_output_json: {exc}")
            return {}
        return payload if isinstance(payload, dict) else {}

    portfolio_payload = read_path_json("portfolio")
    preflight_payload = read_path_json("preflight")
    macro_payload = read_path_json("macro")
    crypto_payload = read_path_json("crypto")
    impulse_payload = read_path_json("impulse")
    us_scanner_payload = read_path_json("us_scanner")

    preflight_quality = str(preflight_payload.get("data_quality") or "").lower()
    preflight_missing = preflight_payload.get("missing_critical_categories") or []
    if preflight_quality in {"missing", "missing_or_degraded", "degraded", "stale", "disputed"} or preflight_missing:
        add_internal_degraded(
            "market_data_source_preflight",
            f"data_quality={preflight_payload.get('data_quality')}; missing_critical_categories={','.join(preflight_missing[:8])}",
        )

    portfolio_errors = portfolio_payload.get("source_errors") or []
    if portfolio_errors:
        add_internal_degraded(
            "portfolio_snapshot",
            f"public price source errors present: {len(portfolio_errors)}; report may be using stale ledger-price fallback",
        )

    macro_quality = str(macro_payload.get("data_quality") or (context.get("macro_regime_panel") or {}).get("status") or "").lower()
    missing_macro_count = (((macro_payload.get("macro_regime") or {}).get("missing_component_count")))
    macro_components = ((macro_payload.get("macro_regime") or {}).get("components") or [])
    critical_macro_components = {
        "VIX risk pressure",
        "QQQ 20d trend",
        "SOXX 20d trend",
        "SPY 20d trend",
        "10Y yield pressure",
        "10Y-2Y curve",
        "BTC 24h trend",
        "ETH 24h trend",
    }
    missing_critical_macro = [
        str(item.get("name"))
        for item in macro_components
        if item.get("score_points") is None and str(item.get("name")) in critical_macro_components
    ]
    # CPI, PCE and fund-flow gaps are important context, but they should not
    # stop the whole manual dispatch from producing learning samples when the
    # core risk proxies and Treasury curve are otherwise usable.
    macro_core_missing = bool(missing_critical_macro) or ((as_float(missing_macro_count, 0) or 0) > 2)
    macro_source_errors = macro_payload.get("source_errors") or []
    if macro_quality in {"missing", "missing_or_degraded", "stale", "disputed"} or macro_core_missing:
        add_internal_degraded(
            "macro_regime",
            "macro data quality="
            f"{macro_payload.get('data_quality')}; missing_component_count={missing_macro_count}; "
            f"missing_critical_macro={','.join(missing_critical_macro[:6]) or 'none'}",
        )
    elif macro_quality == "degraded":
        add_supplemental_degraded(
            "macro_regime",
            "core macro components are present, but supplemental macro/fund-flow fields are missing: "
            + ", ".join((macro_payload.get("missing_data") or [])[:5]),
        )
    if macro_source_errors and macro_quality != "degraded":
        add_supplemental_degraded(
            "macro_regime",
            f"source_errors={len(macro_source_errors)}",
        )

    crypto_market_quality = crypto_payload.get("market_quality") or {}
    crypto_core_symbols = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"}
    degraded_crypto_assets = [
        symbol
        for symbol, item in crypto_market_quality.items()
        if isinstance(item, dict) and str(item.get("status") or "").lower() not in {"verified", "ok"}
    ]
    crypto_source_errors = []
    for source_name, source_payload in (crypto_payload.get("sources") or {}).items():
        if isinstance(source_payload, dict) and source_payload.get("error"):
            crypto_source_errors.append(source_name)
    blocking_crypto_errors = [
        name
        for name in crypto_source_errors
        if name in {"fear_greed", "coingecko_markets", "defillama_chains", "binance_spot_24hr", "binance_klines_1d", "binance_orderbook_depth"}
    ]
    core_crypto_degraded = [symbol for symbol in degraded_crypto_assets if symbol in crypto_core_symbols]
    noncore_crypto_degraded = [symbol for symbol in degraded_crypto_assets if symbol not in crypto_core_symbols]
    optional_crypto_errors = [name for name in crypto_source_errors if name not in blocking_crypto_errors]
    if core_crypto_degraded or blocking_crypto_errors:
        add_internal_degraded(
            "crypto_multisource_snapshot",
            "degraded_assets="
            + ",".join(core_crypto_degraded[:8])
            + f"; source_errors={','.join(blocking_crypto_errors[:8])}",
        )
    if noncore_crypto_degraded or optional_crypto_errors:
        add_supplemental_degraded(
            "crypto_multisource_snapshot",
            "noncore_degraded_assets="
            + ",".join(noncore_crypto_degraded[:8])
            + f"; optional_source_errors={','.join(optional_crypto_errors[:8])}",
        )

    impulse_signals = impulse_payload.get("signals") or []
    impulse_errors = impulse_payload.get("errors") or []
    endpoint_audit = impulse_payload.get("public_endpoint_audit") or {}
    endpoint_successes = sum(
        int((item or {}).get("success_count") or 0)
        for item in endpoint_audit.values()
        if isinstance(item, dict)
    )
    identity_status = str(
        (impulse_payload.get("dynamic_discovery_audit") or {}).get(
            "crypto_identity_status"
        )
        or ""
    )
    if not impulse_signals or endpoint_successes <= 0:
        add_internal_degraded(
            "crypto_impulse_capture",
            f"signals={len(impulse_signals)}; public_endpoint_successes={endpoint_successes}; errors={len(impulse_errors)}",
        )
    elif identity_status.startswith("degraded"):
        add_supplemental_degraded(
            "crypto_impulse_capture",
            f"dynamic crypto identity cross-check={identity_status}",
        )

    us_scan_status = str(us_scanner_payload.get("scan_status") or (context.get("us_open_dynamic_scanner_panel") or {}).get("status") or "").lower()
    us_candidate_count = len(us_scanner_payload.get("candidates") or [])
    if us_scan_status in {"missing", "stale", "disputed"} or (us_scan_status == "degraded" and us_candidate_count <= 0):
        add_internal_degraded(
            "us_open_dynamic_scanner",
            f"scan_status={us_scanner_payload.get('scan_status')}; degraded_no_actionable_trade={us_scanner_payload.get('degraded_no_actionable_trade')}",
        )
    elif us_scan_status == "degraded" or us_scanner_payload.get("degraded_no_actionable_trade") is True:
        add_supplemental_degraded(
            "us_open_dynamic_scanner",
            f"partial scanner degradation; candidate_count={us_candidate_count}; degraded_no_actionable_trade={us_scanner_payload.get('degraded_no_actionable_trade')}",
        )

    if args.use_existing_inputs:
        degraded_reasons.append("use_existing_inputs_cache_not_fresh_manual_refresh")
    if args.skip_active_scanners:
        degraded_reasons.append("active_market_scanners_skipped")
    if not paths.get("crypto"):
        degraded_reasons.append("crypto_multisource_snapshot_missing")
    if not paths.get("us_scanner"):
        degraded_reasons.append("us_open_dynamic_scanner_missing")
    if failed:
        degraded_reasons.append("one_or_more_sources_failed")
    degraded_reasons.extend(
        f"{item['source']}_internal_data_degraded"
        for item in internal_degraded_sources
        if f"{item['source']}_internal_data_degraded" not in degraded_reasons
    )

    snapshot = {
        "snapshot_id": f"{run_id}-fresh-market-intelligence",
        "captured_at": utc_now(),
        "manual_dispatch_only": True,
        "background_loop_started": False,
        "live_orders_enabled": False,
        "asset_scope": {
            "crypto_symbols": args.crypto_symbols.split(",") if args.crypto_symbols else [],
            "current_tactical_symbol": args.current_tactical_symbol,
        },
        "sources_attempted": attempted,
        "sources_succeeded": succeeded,
        "missing_or_failed_sources": failed,
        "internal_degraded_sources": internal_degraded_sources,
        "supplemental_degraded_sources": supplemental_degraded_sources,
        "market_sentiment_summary": "; ".join(sentiment_parts),
        "source_preflight_summary": {
            "status": preflight_payload.get("data_quality"),
            "generated_at": preflight_payload.get("generated_at"),
            "env_file_loaded": preflight_payload.get("env_file_loaded"),
            "env_file_keys_loaded": preflight_payload.get("env_file_keys_loaded") or [],
            "missing_critical_categories": preflight_payload.get("missing_critical_categories") or [],
            "api_env_presence": preflight_payload.get("api_env_presence") or {},
            "recommendation": preflight_payload.get("recommendation"),
        },
        "macro_summary": {
            "status": (context.get("macro_regime_panel") or {}).get("status"),
            "generated_at": macro_summary.get("generated_at"),
            "pace": macro_summary.get("dca_pace"),
            "regime": macro_summary.get("macro_regime"),
        },
        "crypto_market_summary": {
            "status": (context.get("crypto_market_panel") or {}).get("status"),
            "generated_at": crypto_summary.get("generated_at"),
            "asset_count": len(crypto_assets),
        },
        "us_equity_market_summary": {
            "status": (context.get("us_open_dynamic_scanner_panel") or {}).get("status"),
            "created_at": us_summary.get("created_at") or us_summary.get("generated_at"),
            "candidate_count": len(us_candidates),
            "max_allowed_action": us_summary.get("max_allowed_action"),
        },
        "objective_strategy_mapping": {
            "monthly_tactical_goal": "map US equity tactical data, dynamic candidates, cash drag, and double-80 gates to the monthly tactical return objective",
            "five_to_ten_year_goal": "map crypto DCA, staking, thesis quality, and goal-gap evidence to the 5-10y 10x objective",
            "cash_rail_decision": "keep crypto and US equity cash rails separate; deploy only when the relevant rail is confirmed and the trigger is clear",
            "data_summary_is_not_enough": True,
        },
        "downgrade_effect": "block_execute_now" if degraded_reasons else "none",
        "market_intelligence_degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
    }
    context["fresh_market_intelligence_snapshot"] = snapshot

    readiness = context.setdefault("report_readiness", {})
    if snapshot["market_intelligence_degraded"]:
        readiness["execute_now_allowed"] = False
        failed_gates = list(readiness.get("failed_gates") or [])
        for gate in ["manual_dispatch_market_sentiment_refresh_gate_failed", "market_intelligence_degraded"]:
            if gate not in failed_gates:
                failed_gates.append(gate)
        readiness["failed_gates"] = failed_gates
        if readiness.get("max_allowed_action") in {None, "", "execute_now"}:
            readiness["max_allowed_action"] = "conditional_action_or_watch"
        missing_panel = context.setdefault("missing_data_downgrade_panel", [])
        if isinstance(missing_panel, list):
            missing_panel.append({
                "category": "fresh_market_intelligence",
                "status": "degraded",
                "impact": "conditional only",
                "reason": "Manual dispatch did not complete a fully fresh market/sentiment refresh: "
                + ", ".join(degraded_reasons),
            })
    return snapshot


def latest_matching(patterns: list[str]) -> Path | None:
    for pattern in patterns:
        existing = [path for path in TMP_ROOT.glob(pattern) if path.exists()]
        if existing:
            return max(existing, key=lambda path: path.stat().st_mtime)
    return None


def require_existing(name: str, patterns: list[str]) -> Path:
    path = latest_matching(patterns)
    if path is None:
        raise FileNotFoundError(f"No existing {name} file found in /private/tmp for patterns: {patterns}")
    return path


def optional_existing(patterns: list[str]) -> Path | None:
    return latest_matching(patterns)


def build_inputs(args: argparse.Namespace, run_id: str, date_text: str) -> tuple[dict[str, Path | None], list[dict[str, Any]]]:
    compact = date_compact(date_text)
    steps: list[dict[str, Any]] = []
    paths: dict[str, Path | None] = {}
    sanm_review_path = TMP_ROOT / f"current_sanm_paper_review_{compact}.json"

    # Review the frozen SANM simulation before loading or scanning candidates.
    # It writes only to the local paper ledger and cannot create a real order.
    steps.append(run_json_command(
        [
            sys.executable,
            str(ACTIVE_ROOT / "scripts" / "us_equity_paper_reviewer.py"),
        ],
        sanm_review_path,
        "sanm_paper_review",
        required=False,
        timeout=args.timeout_seconds,
    ))
    paths["sanm_paper_review"] = sanm_review_path if sanm_review_path.exists() else None

    if args.use_existing_inputs:
        paths["preflight"] = optional_existing([
            f"market_data_source_preflight_{compact}.json",
            "market_data_source_preflight_*.json",
        ])
        paths["portfolio"] = require_existing("portfolio snapshot", [
            f"current_portfolio_snapshot_{compact}.json",
            f"portfolio_comparison_snapshot_{compact}*.json",
            "current_portfolio_snapshot_*.json",
        ])
        paths["goal"] = require_existing("goal projection", [
            f"current_goal_projection_{compact}.json",
            f"goal_projection_{compact}*.json",
            "current_goal_projection_*.json",
        ])
        paths["macro"] = optional_existing([
            f"macro_regime_snapshot_{compact}.json",
            "macro_regime_snapshot_*.json",
        ])
        paths["asset_goal"] = optional_existing([
            f"asset_goal_contribution_{compact}.json",
            "asset_goal_contribution_*.json",
        ])
        paths["crypto"] = optional_existing([
            f"current_crypto_multisource_{compact}.json",
            f"manual_report_multisource_snapshot_{compact}*.json",
            "*crypto*multisource*snapshot*.json",
        ])
        paths["us_scanner"] = optional_existing([
            f"current_us_open_scanner_{compact}.json",
            f"us_open_dynamic_scanner_{compact}*.json",
            "*us_open*scanner*.json",
        ])
        paths["impulse"] = optional_existing([
            f"current_impulse_capture_{compact}.json",
            "current_impulse_capture_*.json",
        ])
        ensure_current_tactical_symbol(args, paths.get("portfolio"), steps)
        steps.append({"step": "use_existing_inputs", "status": "ok", "paths": {k: str(v) for k, v in paths.items() if v}})
        return paths, steps

    portfolio_path = TMP_ROOT / f"current_portfolio_snapshot_{compact}.json"
    preflight_path = TMP_ROOT / f"market_data_source_preflight_{compact}.json"
    goal_path = TMP_ROOT / f"current_goal_projection_{compact}.json"
    macro_path = TMP_ROOT / f"macro_regime_snapshot_{compact}.json"
    asset_goal_path = TMP_ROOT / f"asset_goal_contribution_{compact}.json"
    crypto_path = TMP_ROOT / f"current_crypto_multisource_{compact}.json"
    us_path = TMP_ROOT / f"current_us_open_scanner_{compact}.json"
    impulse_path = TMP_ROOT / f"current_impulse_capture_{compact}.json"
    steps.append(run_json_command(
        [
            sys.executable,
            str(MANUAL_ROOT / "scripts" / "market_data_source_preflight.py"),
            *(
                ["--env-file", args.env_file]
                if getattr(args, "env_file", None)
                else []
            ),
            "--format",
            "json",
        ],
        preflight_path,
        "market_data_source_preflight",
        required=False,
        timeout=args.timeout_seconds,
    ))
    steps.append(run_json_command(
        [sys.executable, str(MANUAL_ROOT / "scripts" / "portfolio_comparison_snapshot.py")],
        portfolio_path,
        "portfolio_snapshot",
        required=True,
        timeout=args.timeout_seconds,
    ))
    ensure_current_tactical_symbol(args, portfolio_path, steps)
    steps.append(run_json_command(
        [
            sys.executable,
            str(MANUAL_ROOT / "scripts" / "goal_path_projection.py"),
            "--snapshot-json",
            str(portfolio_path),
            "--monthly-dca",
            str(args.monthly_dca),
            "--format",
            "json",
        ],
        goal_path,
        "goal_projection",
        required=True,
        timeout=args.timeout_seconds,
    ))
    steps.append(run_json_command(
        [sys.executable, str(MANUAL_ROOT / "scripts" / "macro_regime_snapshot.py"), "--format", "json"],
        macro_path,
        "macro_regime",
        required=False,
        timeout=args.timeout_seconds,
    ))
    steps.append(run_json_command(
        [
            sys.executable,
            str(MANUAL_ROOT / "scripts" / "asset_goal_contribution.py"),
            "--portfolio-snapshot-json",
            str(portfolio_path),
            "--goal-projection-json",
            str(goal_path),
            "--monthly-dca",
            str(args.monthly_dca),
            "--format",
            "json",
        ],
        asset_goal_path,
        "asset_goal_contribution",
        required=False,
        timeout=args.timeout_seconds,
    ))
    if not args.skip_active_scanners:
        risk_free_cli: list[str] = []
        try:
            macro_payload = load_json(macro_path)
            treasury = macro_payload.get("treasury_yield_curve") or {}
            risk_free_rate = treasury.get("yield_3m_pct")
            risk_free_date = treasury.get("date")
            if risk_free_rate is not None:
                risk_free_cli = [
                    "--risk-free-rate-pct",
                    str(risk_free_rate),
                    "--risk-free-evidence-id",
                    f"us-treasury-3m:{risk_free_date or 'unknown-date'}",
                ]
        except Exception:  # noqa: BLE001
            risk_free_cli = []
        us_scanner_command = [
            sys.executable,
            str(ACTIVE_ROOT / "scripts" / "us_open_dynamic_scanner.py"),
            "--json-only",
            "--max-symbols",
            "25",
            *risk_free_cli,
        ]
        if args.current_tactical_symbol:
            us_scanner_command.extend(["--current-tactical-symbol", args.current_tactical_symbol])
        steps.append(run_json_command(
            [
                sys.executable,
                str(ACTIVE_ROOT / "scripts" / "run_multisource_alpha_snapshot.py"),
                "--symbols",
                args.crypto_symbols,
            ],
            crypto_path,
            "crypto_multisource_snapshot",
            required=False,
            timeout=args.timeout_seconds,
        ))
        steps.append(run_json_command(
            [
                sys.executable,
                str(ACTIVE_ROOT / "scripts" / "impulse_capture_scanner.py"),
                "--symbols",
                args.crypto_symbols,
                "--dynamic-top",
                "20",
                "--no-write",
                "--recommendation-history",
                str(MANUAL_ROOT / "recommendations" / "recommendation_history.json"),
                *risk_free_cli,
            ],
            impulse_path,
            "crypto_impulse_capture",
            required=False,
            timeout=args.timeout_seconds,
        ))
        steps.append(run_json_command(
            us_scanner_command,
            us_path,
            "us_open_dynamic_scanner",
            required=False,
            timeout=args.timeout_seconds,
        ))

    paths.update({
        "preflight": preflight_path if preflight_path.exists() else None,
        "portfolio": portfolio_path,
        "goal": goal_path,
        "macro": macro_path if macro_path.exists() else None,
        "asset_goal": asset_goal_path if asset_goal_path.exists() else None,
        "crypto": crypto_path if crypto_path.exists() else None,
        "us_scanner": us_path if us_path.exists() else None,
        "impulse": impulse_path if impulse_path.exists() else None,
        "sanm_paper_review": sanm_review_path if sanm_review_path.exists() else None,
    })
    return paths, steps


def build_context_and_research(args: argparse.Namespace, run_id: str, date_text: str, paths: dict[str, Path | None]) -> tuple[Path, Path, list[dict[str, Any]]]:
    compact = date_compact(date_text)
    steps: list[dict[str, Any]] = []
    context_pre_path = TMP_ROOT / f"{run_id}-daily-context-pre-research.json"
    research_path = TMP_ROOT / f"{run_id}-research-panel.json"
    context_final_path = TMP_ROOT / f"{run_id}-daily-context-final.json"

    base_cmd = [
        sys.executable,
        str(MANUAL_ROOT / "scripts" / "build_daily_report_context.py"),
        "--portfolio-snapshot-json",
        str(paths["portfolio"]),
        "--goal-projection-json",
        str(paths["goal"]),
        "--monthly-dca",
        str(args.monthly_dca),
        "--format",
        "json",
    ]
    if paths.get("macro"):
        base_cmd.extend(["--macro-regime-json", str(paths["macro"])])
    if paths.get("asset_goal"):
        base_cmd.extend(["--asset-goal-contribution-json", str(paths["asset_goal"])])
    if paths.get("crypto"):
        base_cmd.extend(["--crypto-snapshot-json", str(paths["crypto"])])
    if paths.get("us_scanner"):
        base_cmd.extend(["--us-scanner-json", str(paths["us_scanner"])])

    steps.append(run_json_command(base_cmd, context_pre_path, "daily_context_pre_research", required=True, timeout=args.timeout_seconds))

    research_cmd = [
        sys.executable,
        str(MANUAL_ROOT / "scripts" / "research_panel_runner.py"),
        "--run-id",
        run_id,
        "--daily-context-json",
        str(context_pre_path),
        "--format",
        "json",
    ]
    if paths.get("crypto"):
        research_cmd.extend(["--crypto-snapshot-json", str(paths["crypto"])])
    if paths.get("us_scanner"):
        research_cmd.extend(["--us-scanner-json", str(paths["us_scanner"])])
    default_external_outputs = TMP_ROOT / f"{run_id}-external-subagent-outputs.json"
    external_agent_outputs_json = args.external_agent_outputs_json
    if not external_agent_outputs_json and default_external_outputs.exists():
        external_agent_outputs_json = str(default_external_outputs)
        steps.append({
            "step": "external_subagent_outputs_auto_detect",
            "status": "ok",
            "output_path": str(default_external_outputs),
        })
    elif not external_agent_outputs_json:
        steps.append({
            "step": "external_subagent_outputs_auto_detect",
            "status": "degraded",
            "error": f"{default_external_outputs} not found; local fallback research panel will block execute_now.",
        })
        if args.require_external_subagents:
            raise FileNotFoundError(
                f"--require-external-subagents was set but no external subagent outputs were supplied "
                f"and {default_external_outputs} does not exist"
            )
    if external_agent_outputs_json:
        research_cmd.extend(["--external-agent-outputs-json", external_agent_outputs_json])
    steps.append(run_json_command(research_cmd, research_path, "research_panel", required=True, timeout=args.timeout_seconds))

    final_cmd = list(base_cmd)
    final_cmd.extend(["--research-panel-json", str(research_path)])
    steps.append(run_json_command(final_cmd, context_final_path, "daily_context_final", required=True, timeout=args.timeout_seconds))

    return context_final_path, research_path, steps


def render_fresh_market_intelligence_section(context: dict[str, Any]) -> str:
    snapshot = context.get("fresh_market_intelligence_snapshot") or {}
    rows = [
        ["snapshot_id", f"`{snapshot.get('snapshot_id')}`"],
        ["captured_at", f"`{snapshot.get('captured_at')}`"],
        ["manual_dispatch_only", f"`{snapshot.get('manual_dispatch_only')}`"],
        ["background_loop_started", f"`{snapshot.get('background_loop_started')}`"],
        ["live_orders_enabled", f"`{snapshot.get('live_orders_enabled')}`"],
        ["market_intelligence_degraded", f"`{snapshot.get('market_intelligence_degraded')}`"],
        ["downgrade_effect", f"`{snapshot.get('downgrade_effect')}`"],
    ]
    source_rows = []
    attempted = set(snapshot.get("sources_attempted") or [])
    succeeded = set(snapshot.get("sources_succeeded") or [])
    failed_by_source = {
        item.get("source"): item.get("reason")
        for item in snapshot.get("missing_or_failed_sources") or []
        if isinstance(item, dict)
    }
    internal_degraded_by_source = {
        item.get("source"): item.get("reason")
        for item in snapshot.get("internal_degraded_sources") or []
        if isinstance(item, dict)
    }
    supplemental_degraded_by_source = {
        item.get("source"): item.get("reason")
        for item in snapshot.get("supplemental_degraded_sources") or []
        if isinstance(item, dict)
    }
    for source_name in sorted(attempted):
        status = "ok"
        reason = failed_by_source.get(source_name)
        if source_name not in succeeded:
            status = "missing/degraded"
        elif internal_degraded_by_source.get(source_name):
            status = "degraded"
            reason = internal_degraded_by_source.get(source_name)
        elif supplemental_degraded_by_source.get(source_name):
            status = "partial"
            reason = supplemental_degraded_by_source.get(source_name)
        source_rows.append([
            source_name,
            status,
            short(reason, 120),
        ])
    extra_internal_sources = [
        [item.get("source"), "degraded", short(item.get("reason"), 120)]
        for item in snapshot.get("internal_degraded_sources") or []
        if isinstance(item, dict) and item.get("source") not in attempted
    ]
    source_rows.extend(extra_internal_sources)
    extra_supplemental_sources = [
        [item.get("source"), "partial", short(item.get("reason"), 120)]
        for item in snapshot.get("supplemental_degraded_sources") or []
        if isinstance(item, dict) and item.get("source") not in attempted
    ]
    source_rows.extend(extra_supplemental_sources)
    summary_rows = [
        ["source_preflight", short(snapshot.get("source_preflight_summary"), 140)],
        ["market_sentiment", short(snapshot.get("market_sentiment_summary"), 140)],
        ["macro", short(snapshot.get("macro_summary"), 140)],
        ["crypto_market", short(snapshot.get("crypto_market_summary"), 140)],
        ["us_equity_market", short(snapshot.get("us_equity_market_summary"), 140)],
        ["degraded_reasons", short(", ".join(snapshot.get("degraded_reasons") or []), 160)],
        ["supplemental_gaps", short(", ".join(item.get("source", "") for item in snapshot.get("supplemental_degraded_sources") or []), 160)],
    ]
    return "\n".join([
        "## 1A. Fresh Market Intelligence Panel",
        "",
        "`Fresh Market Intelligence` 是“本次手动报告重新取数”的意思，不代表后台自动盯盘，也不代表自动下单。",
        "",
        markdown_table(["字段", "状态"], rows),
        "",
        markdown_table(["数据源", "状态", "缺口/原因"], source_rows) if source_rows else "- 未记录数据源刷新状态。",
        "",
        markdown_table(["维度", "摘要"], summary_rows),
        "",
        "这对你意味着什么：如果 `market_intelligence_degraded=true`，本轮最多只能给持有、等待、conditional、paper 或 no_deploy，不能新增 `execute_now`。",
    ])


def render_impulse_continuity_section(context: dict[str, Any]) -> str:
    panel = context.get("impulse_capture_panel") or {}
    rows = []
    for item in (panel.get("signals") or [])[:8]:
        rows.append([
            item.get("symbol"),
            item.get("stage"),
            item.get("execution_action") or "no_deploy",
            item.get("observation_action") or item.get("recommended_max_action"),
            item.get("impulse_id"),
            item.get("baseline_recommendation_id") or "unlinked_no_valid_frozen_baseline",
            item.get("official_event_status"),
        ])
    return "\n".join([
        "## 1B. Signal Continuity / 盘中异动续接",
        "",
        "真实执行与观察提醒分开：观察升级不等于买入；候选官方事件尚未回传时，真实执行保持 `NO_DEPLOY`。",
        "",
        markdown_table(
            ["symbol", "stage", "execution", "observation", "signal_id", "baseline_id", "event relay"],
            rows or [["none", "no_scan", "no_deploy", "no_observation", "-", "-", "missing"]],
        ),
    ])


def render_risk_adjusted_path_section(context: dict[str, Any]) -> str:
    panel = context.get("risk_adjusted_path_panel") or {}
    ranks = {
        (item.get("symbol"), item.get("asset_class")): item
        for item in panel.get("rank_ablation") or []
    }
    rows = []
    for item in panel.get("candidates") or []:
        rank = ranks.get((item.get("symbol"), item.get("asset_class"))) or {}
        rows.append([
            item.get("symbol"),
            item.get("asset_class"),
            item.get("lane"),
            item.get("sharpe_20"),
            item.get("sharpe_60"),
            item.get("information_ratio_60"),
            pct(item.get("max_drawdown_60_pct")),
            item.get("quality_score"),
            item.get("ranking_adjustment_points"),
            f"{rank.get('base_rank')}→{rank.get('risk_adjusted_rank')}",
            item.get("persistence_label"),
            short(", ".join(item.get("flags") or []), 80),
        ])
    return "\n".join([
        "## 1C. 风险调整后的路径质量",
        "",
        "Sharpe 只回答“这段收益是否值得承担相应波动”，不回答资产是否低估，也不直接生成上涨概率。趋势通道最多调整 `±7.5` 分；超跌修复通道只有在价值与催化门通过后才允许最多调整 `±4` 分。",
        "",
        markdown_table(
            ["标的", "资产", "通道", "S20", "S60", "IR60", "MDD60", "路径分", "排名调整", "基础→调整排名", "持续性", "标记"],
            rows or [["n/a", "n/a", "n/a", "`n/a`", "`n/a`", "`n/a`", "`n/a`", "`n/a`", "`0`", "-", "missing", "risk path missing"]],
        ),
        "",
        markdown_table(
            ["审计项", "状态"],
            [
                ["策略角色", f"`{panel.get('strategy_role')}`"],
                ["Sharpe>3硬门槛", f"`{panel.get('hard_sharpe_gt_3_gate')}`"],
                ["因子状态", f"`{panel.get('promotion_status')}`"],
                ["是否影响真实执行门", f"`{panel.get('live_gate_effect')}`"],
                ["有/无因子配对排名", f"`{panel.get('ablation_available')}`"],
                ["配对回测状态", f"`{(panel.get('paired_backtest') or {}).get('status')}`"],
                ["封闭模拟样本", f"`{(panel.get('paired_backtest') or {}).get('closed_sample_count')}` / `20`"],
                ["独立非重叠窗口", f"`{(panel.get('paired_backtest') or {}).get('independent_non_overlapping_windows')}` / `3`"],
                ["晋级门", f"`{(panel.get('promotion_gate') or {}).get('passed')}`"],
                ["EvidenceSnapshotV2", f"`{(context.get('risk_adjusted_evidence_snapshot') or {}).get('snapshot_id')}`"],
                ["证据快照文件", f"`{(context.get('risk_adjusted_evidence_snapshot') or {}).get('path')}`"],
                ["公式与排名审计", f"`{(context.get('risk_adjusted_evidence_snapshot') or {}).get('audit_path')}`"],
            ],
        ),
    ])


def render_sanm_paper_review_section(context: dict[str, Any]) -> str:
    panel = context.get("sanm_paper_review_panel") or {}
    rows = []
    for item in panel.get("reviews") or []:
        mark = item.get("mark") or {}
        rows.append([
            item.get("paper_trade_id"),
            item.get("symbol"),
            price(mark.get("price_usd")),
            pct(mark.get("unrealized_roi_pct")),
            pct(mark.get("post_fill_mfe_pct")),
            pct(mark.get("post_fill_mae_pct")),
            mark.get("target_1_hit"),
            mark.get("target_2_hit"),
            mark.get("stop_hit"),
            mark.get("window_status"),
            mark.get("as_of"),
        ])
    return "\n".join([
        "## 1B. SANM 模拟仓强制复盘",
        "",
        "原始模拟条件保持冻结：`$1,000 @ $165`，T1 `$178`，T2 `$181.50`，只在正常交易时段收盘价不高于 `$157.50` 时判定止损，时间止损为 `2026-08-07` 收盘。",
        "",
        markdown_table(
            ["paper id", "symbol", "latest", "ROI", "MFE", "MAE", "T1", "T2", "close stop", "状态", "截至"],
            rows or [["missing", "SANM", "`n/a`", "`n/a`", "`n/a`", "`n/a`", "-", "-", "-", "review_degraded", "-"]],
        ),
        "",
        "这里只复盘模拟判断准确性；`real_portfolio_impact_usd=$0`，不会转化为真实订单。",
    ])


def render_full_market_deep_analysis_section(context: dict[str, Any]) -> str:
    snapshot = context.get("fresh_market_intelligence_snapshot") or {}
    dashboard = context.get("goal_execution_dashboard") or {}
    goal = dashboard.get("goal_path") or {}
    dca = dashboard.get("crypto_dca_operating_plan") or {}
    us = dashboard.get("us_tactical_operating_state") or {}
    evidence = dashboard.get("evidence_and_gate_state") or {}
    readiness = context.get("report_readiness") or {}
    market_degraded = bool(snapshot.get("market_intelligence_degraded"))
    execute_now_allowed = evidence.get("execute_now_allowed")
    if execute_now_allowed is None:
        execute_now_allowed = readiness.get("execute_now_allowed")
    max_action = (
        evidence.get("report_max_allowed_action")
        or readiness.get("max_allowed_action")
        or ("conditional_action_or_watch" if market_degraded else "conditional_action")
    )
    monthly_status = us.get("monthly_progress_status") or "not_available"
    monthly_gap = us.get("monthly_gap_usd")
    required_daily = us.get("required_daily_return_to_monthly_target_pct")
    dca_max_action = dca.get("max_allowed_action") or evidence.get("crypto_dca_max_allowed_action")
    primary_pair = dca.get("primary_pair")
    secondary_pair = dca.get("secondary_pair")
    five_year_req = goal.get("five_year_current_principal_required_annual_pct")
    ten_year_req = goal.get("ten_year_current_principal_required_annual_pct")
    cash_note = (
        "两路现金分开判断；未被券商/交易所确认的现金只能作为 conditional sizing。"
    )
    if market_degraded:
        tactical_fit = "降级：全盘数据不完整，不能新增 execute_now，只能等触发或继续观察。"
        dca_fit = "降级：长期 DCA 可以保留草案，但需要按缺失数据缩小金额或等待确认。"
    else:
        tactical_fit = (
            f"月度战术目标状态={monthly_status}; 月度缺口={money(monthly_gap)}; "
            f"追赶目标所需日均收益={pct(required_daily)}; 最大动作={evidence.get('short_term_tactical_max_allowed_action') or max_action}。"
        )
        dca_fit = (
            f"长期目标所需年化：5年={pct(five_year_req)}, 10年={pct(ten_year_req)}; "
            f"DCA主线={primary_pair or '-'} / {secondary_pair or '-'}; 最大动作={dca_max_action or max_action}。"
        )
    rows = [
        ["调度合约", "本轮必须完成：取数、情绪、全盘分析、目标映射、操作结论；不完整则降级。"],
        ["月度战术收益目标", tactical_fit],
        ["5-10年长期目标", dca_fit],
        ["现金通道", cash_note],
        ["最大允许动作", f"`{max_action}`; execute_now_allowed=`{execute_now_allowed}`"],
        ["不应该做什么", "不能因为单条新闻、社交热度或单日涨跌就绕过数据质量、双80、现金确认和人工确认。"],
    ]
    return "\n".join([
        "## 1B. Full-Market Deep Analysis Panel",
        "",
        "`Full-Market Deep Analysis` 是“把全盘数据翻译成目标导向动作”的意思：不是后台自动交易，也不是只罗列行情。",
        "",
        markdown_table(["维度", "策略判断"], rows),
        "",
        "这对你意味着什么：每次调度后，报告必须明确现在做什么、等什么触发、不做什么；如果不能说明如何服务月度或长期目标，候选只能观察，不能成为主动作。",
    ])


def render_passive_dispatch_closeout_section(context: dict[str, Any]) -> str:
    readiness = context.get("report_readiness") or {}
    research = context.get("research_panel") or {}
    fresh = context.get("fresh_market_intelligence_snapshot") or {}
    summary_rows = [
        ["运行边界", "本 skill 是被动调度：用户触发一次，系统跑一次报告；不会后台自动盯盘、下单或转账。"],
        ["本次是否后台循环", f"`{fresh.get('background_loop_started', False)}`"],
        ["真实下单 API", f"`{fresh.get('live_orders_enabled', False)}`"],
        ["是否允许 execute_now", f"`{readiness.get('execute_now_allowed')}`"],
        ["最大动作等级", f"`{readiness.get('max_allowed_action')}`"],
        ["研究委员会是否降级", f"`{research.get('research_committee_degraded')}`"],
        ["市场情报是否降级", f"`{fresh.get('market_intelligence_degraded')}`"],
    ]
    stop_rows = [
        ["继续运行", "只有当用户明确要求 fresh report / 深度研究，或 paper/recommendation 已经到期可复盘，或本地机制有可修 bug。"],
        ["收口汇报", "当机制可用、没有 due-now 复盘、继续运行只能等待市场时间或做开放式研究时，必须停下来汇报状态。"],
        ["目标状态", "`goal_mechanism_ready` 表示机制能跑；`goal_complete` 才表示真实财务目标已经被结果证明。"],
    ]
    return "\n".join([
        "## 1C. 被动调度与本轮收口边界",
        "",
        markdown_table(["项目", "状态/说明"], summary_rows),
        "",
        markdown_table(["场景", "规则"], stop_rows),
        "",
        "这对你意味着什么：本报告可以主动取数和深入分析，但它不是自动交易程序；如果证据不足或样本未到期，正确动作是记录、等待触发或复盘，而不是一直空跑。",
    ])


def render_portfolio_section(context: dict[str, Any]) -> str:
    portfolio = context.get("portfolio_snapshot") or {}
    comparisons = portfolio.get("comparisons") or {}
    rows = []
    for window in ["1d", "7d", "30d"]:
        comp = comparisons.get(window) or {}
        rows.append([
            window,
            pct(comp.get("total_change_pct")),
            money(comp.get("total_change_usd")),
            pct(comp.get("crypto_change_pct")),
            pct(comp.get("us_equity_change_pct")),
        ])

    holdings = []
    for item in (portfolio.get("top_holdings") or [])[:10]:
        holdings.append([
            item.get("symbol"),
            item.get("rail"),
            money(item.get("current_value")),
            pct(item.get("current_weight_pct")),
            short(item.get("data_quality"), 50),
        ])

    return "\n".join([
        "## 2. 持仓总览",
        "",
        markdown_table(
            ["项目", "金额"],
            [
                ["总资产", money(portfolio.get("total_value"))],
                ["Crypto", money(portfolio.get("crypto_value"))],
                ["美股", money(portfolio.get("us_equity_value"))],
                ["现金/稳定币合计", money(portfolio.get("cash_or_stablecoin"))],
                ["估值时间", f"`{portfolio.get('generated_at')}`"],
            ],
        ),
        "",
        markdown_table(["窗口", "总资产变化", "变化额", "Crypto", "美股"], rows),
        "",
        markdown_table(["资产", "通道", "当前估值", "组合占比", "数据质量"], holdings),
    ])


def render_cost_basis_section(context: dict[str, Any]) -> str:
    panel = context.get("cost_basis_reconciliation_panel") or {}
    payload = panel.get("summary") or {}
    summary = payload.get("summary") or {}
    holdings = payload.get("holdings") or []
    rows = []
    for item in holdings:
        if item.get("audit_status") in {"full_avg_cost_available", "partial_known_lots", "missing_cost_basis"}:
            rows.append([
                item.get("symbol"),
                item.get("audit_status"),
                money(item.get("market_value_usd")),
                money(item.get("avg_cost")) if item.get("avg_cost") is not None else "`unknown`",
                item.get("known_lot_count"),
                item.get("missing_lot_count"),
                pct(item.get("known_quantity_pct")),
                item.get("max_pnl_status"),
            ])

    next_actions = payload.get("next_actions") or []
    next_action_lines = "\n".join(f"- {short(item, 180)}" for item in next_actions) or "- `n/a`"

    return "\n".join([
        "## 3. 成本覆盖与收益口径",
        "",
        markdown_table(
            ["项目", "状态"],
            [
                ["审计状态", f"`{panel.get('status')}`"],
                ["成本口径上限", f"`{summary.get('max_allowed_pnl_claim')}`"],
                ["完整成本持仓数", summary.get("full_avg_cost_count")],
                ["部分已知 lots", ", ".join(summary.get("partial_known_lot_symbols") or []) or "`none`"],
                ["已确认 lot 成本", money(summary.get("known_lot_cost_basis_usd"))],
                ["缺失重大成本", ", ".join(summary.get("missing_material_symbols") or []) or "`none`"],
            ],
        ),
        "",
        markdown_table(
            ["资产", "成本状态", "当前估值", "avg_cost", "已知lots", "缺失lots", "已知数量占比", "PnL口径"],
            rows[:12],
        ),
        "",
        "**影响**：`partial_cost_aware_only` 时，报告可以使用 API 市值和已确认 lot 做仓位判断，但不得宣称完整成本收益率；lcETH/ADA/SOL 的完整收益口径需要补齐交易导出。",
        "",
        "**下一步**",
        "",
        next_action_lines,
    ])


def render_goal_section(context: dict[str, Any]) -> str:
    projection = context.get("goal_path_projection") or {}
    required = projection.get("required") or {}
    rows = []
    for years in ["5", "10"]:
        item = required.get(years) or {}
        rows.append([
            f"{years}年 当前本金10x",
            money(item.get("current_principal_10x_target") or item.get("current_principal_target")),
            pct((item.get("current_principal_required_annual_rate") or 0) * 100),
        ])
        rows.append([
            f"{years}年 累计投入10x",
            money(item.get("cumulative_capital_10x_target") or item.get("cumulative_capital_target")),
            pct((item.get("cumulative_capital_required_annual_rate") or 0) * 100),
        ])

    panel = (context.get("asset_goal_contribution_panel") or {}).get("summary") or {}
    allocation = panel.get("allocation_by_goal_label") or {}
    allocation_rows = [
        ["accelerator", money(allocation.get("accelerator_value_usd")), pct(allocation.get("accelerator_weight_pct"))],
        ["tail_convexity", money(allocation.get("tail_convexity_value_usd")), pct(allocation.get("tail_convexity_weight_pct"))],
        ["neutral", money(allocation.get("neutral_value_usd")), pct(allocation.get("neutral_weight_pct"))],
        ["drag", money(allocation.get("drag_value_usd")), pct(allocation.get("drag_weight_pct"))],
    ]
    asset_rows = []
    for asset in (panel.get("assets") or [])[:8]:
        staking = asset.get("staking_compounding_summary") or {}
        asset_rows.append([
            asset.get("symbol"),
            asset.get("goal_gap_contribution"),
            money(asset.get("current_value_usd")),
            pct(asset.get("current_weight_pct")),
            pct(staking.get("apy_used_pct")),
            asset.get("dca_weight_implication"),
        ])

    return "\n".join([
        "## 4. 目标差距与资产贡献",
        "",
        markdown_table(["目标", "目标金额", "所需年化"], rows),
        "",
        markdown_table(["贡献标签", "金额", "组合占比"], allocation_rows),
        "",
        markdown_table(["资产", "目标标签", "估值", "权重", "质押APY", "DCA含义"], asset_rows),
    ])


def render_goal_execution_dashboard_section(context: dict[str, Any]) -> str:
    dashboard = context.get("goal_execution_dashboard") or {}
    goal = dashboard.get("goal_path") or {}
    dca = dashboard.get("crypto_dca_operating_plan") or {}
    us = dashboard.get("us_tactical_operating_state") or {}
    evidence = dashboard.get("evidence_and_gate_state") or {}
    learning = dashboard.get("progressive_learning_state") or {}
    paper_calendar = dashboard.get("paper_validation_review_calendar") or {}
    open_summary = paper_calendar.get("open_position_exit_summary") or {}
    gaps = evidence.get("validation_sample_gaps") or {}
    objective_rows = [
        ["portfolio_value", money(dashboard.get("portfolio_value_usd"))],
        ["monthly_dca", money(dashboard.get("monthly_dca_usd"))],
        ["5y_current_required_annual", pct(goal.get("five_year_current_principal_required_annual_pct"))],
        ["10y_current_required_annual", pct(goal.get("ten_year_current_principal_required_annual_pct"))],
        ["goal_mechanism", "`learning_and_execution_backlog_ready`"],
        ["report_max_action", f"`{evidence.get('report_max_allowed_action')}`"],
        ["tactical_max_action", f"`{evidence.get('short_term_tactical_max_allowed_action')}`"],
        ["crypto_dca_max_action", f"`{evidence.get('crypto_dca_max_allowed_action')}`"],
        ["execute_now_allowed", f"`{evidence.get('execute_now_allowed')}`"],
    ]
    dca_rows = [
        ["primary_pair", f"`{dca.get('primary_pair')}`"],
        ["secondary_pair", f"`{dca.get('secondary_pair')}`"],
        ["satellite_pair", f"`{dca.get('satellite_pair')}`"],
        ["max_action", f"`{dca.get('max_allowed_action')}`"],
    ]
    us_rows = [
        ["sleeve", f"`{us.get('sleeve_id')}`"],
        ["current_value", money(us.get("current_tactical_value_usd"))],
        ["cash_drag", pct(us.get("tactical_cash_drag_pct"))],
        ["monthly_status", f"`{us.get('monthly_progress_status')}`"],
        ["monthly_gap", money(us.get("monthly_gap_usd"))],
        ["required_daily_to_monthly_target", pct(us.get("required_daily_return_to_monthly_target_pct"))],
    ]
    gate_rows = [
        ["research_committee_degraded", f"`{evidence.get('research_committee_degraded')}`"],
        ["validation_sample_status", f"`{evidence.get('validation_sample_status')}`"],
        ["closed_paper_trades_needed", f"`{gaps.get('closed_paper_trades_needed')}`"],
        ["calibration_resolved_needed", f"`{gaps.get('calibration_resolved_needed')}`"],
        ["walkforward_target_pass_needed", f"`{gaps.get('walkforward_target_research_pass_needed')}`"],
        ["progressive_learning_stage", f"`{learning.get('learning_stage')}`"],
        ["learning_floor_to_target", f"`{learning.get('learning_floor_pct')}% -> {learning.get('validated_probability_target_pct')}%`"],
    ]
    learning_rows = [
        ["stage", f"`{learning.get('learning_stage')}`"],
        ["next_stage", f"`{learning.get('next_stage')}`"],
        ["paper_closed_count", f"`{learning.get('paper_closed_count')}`"],
        ["paper_win_rate", pct(learning.get("paper_win_rate_pct"))],
        ["paper_60pct_floor_met", f"`{learning.get('paper_learning_floor_met')}`"],
        ["recommendation_resolved_count", f"`{learning.get('recommendation_resolved_count')}`"],
        ["recommendation_hit_rate", pct(learning.get("recommendation_hit_rate_pct"))],
        ["max_learning_action", f"`{learning.get('max_learning_action')}`"],
    ]
    paper_summary_rows = [
        ["open_paper_positions", f"`{open_summary.get('open_count')}`"],
        ["expiring_24h", f"`{open_summary.get('expiring_24h_count')}`"],
        ["expiring_72h", f"`{open_summary.get('expiring_72h_count')}`"],
        ["nearest_expiry_at", f"`{open_summary.get('nearest_expiry_at')}`"],
        ["sample_action", f"`{paper_calendar.get('sample_action')}`"],
        ["recommended_runner_mode", f"`{paper_calendar.get('recommended_runner_mode')}`"],
    ]
    paper_review_rows = []
    for item in (paper_calendar.get("next_open_position_reviews") or [])[:5]:
        paper_review_rows.append([
            item.get("paper_trade_id"),
            item.get("symbol"),
            item.get("expires_at"),
            item.get("hours_to_expiry"),
            pct(item.get("unrealized_pnl_pct")),
        ])
    backlog_rows = []
    for item in dashboard.get("execution_backlog") or []:
        backlog_rows.append([
            item.get("priority"),
            item.get("area"),
            short(item.get("task"), 160),
            item.get("max_action"),
        ])
    return "\n".join([
        "## 5. 目标执行总控台",
        "",
        markdown_table(["Metric", "Value"], objective_rows),
        "",
        markdown_table(["Crypto DCA", "Value"], dca_rows),
        "",
        markdown_table(["US Tactical", "Value"], us_rows),
        "",
        markdown_table(["Evidence Gate", "State"], gate_rows),
        "",
        "**渐进学习状态（不是收益保证）**",
        "",
        "这部分说明系统现在如何从 60% 左右的学习样本逐步校准到 80%+；它只决定建议能否升级，不会自动交易。",
        "",
        markdown_table(["Learning Field", "Value"], learning_rows),
        "",
        "**Paper 复盘日历（只用于积累证据）**",
        "",
        markdown_table(["Paper Review", "Value"], paper_summary_rows),
        "",
        markdown_table(
            ["paper_trade_id", "symbol", "expires_at", "hours_to_expiry", "unrealized_pnl"],
            paper_review_rows or [["-", "-", "-", "-", "-"]],
        ),
        "",
        "**下一步执行 backlog（不是买入清单）**",
        "",
        markdown_table(["Priority", "Area", "Task", "Max Action"], backlog_rows or [["-", "-", "No backlog generated", "-"]]),
    ])


def render_objective_traceability_section(context: dict[str, Any]) -> str:
    dashboard = context.get("goal_execution_dashboard") or {}
    evidence = dashboard.get("evidence_and_gate_state") or {}
    trace_rows = [
        [
            "5年/10年 10x",
            "Goal Gap / Price Scenario / Goal Execution Dashboard",
            "goal_path_projection.py, asset_goal_contribution.py, long_term_price_scenario_panel",
            "输出目标所需年化、资产贡献和5y/10y价格情景；缺失时只做目标追踪",
        ],
        [
            "$1,000/月 crypto DCA",
            "DCA Pair Gate / Candidate Deep Dive",
            "crypto snapshot, recommendation_history.json",
            "输出两档 DCA；数据降级时只能 conditional/watch",
        ],
        [
            "长期高凸性与质押复利",
            "Asset Goal Contribution / Staking Model",
            "APY, 解锁, 链上/项目证据",
            "质押或解锁缺失时缩小仓位或不新增",
        ],
        [
            "美股月/季 50%+ 战术目标",
            "US Tactical Performance / Rotation Relay",
            "us_tactical_performance_tracker.py, scanner handoff",
            "未过双80或现金不明时停在 paper/conditional",
        ],
        [
            "每次调度主动取数",
            "Fresh Market Intelligence / Research Committee",
            "数据源、时间戳、缺失/降级原因",
            "缺 fresh 数据时 block execute_now",
        ],
        [
            "从60%学习到80%+",
            "Recommendation History / Learning Calendar",
            "paper samples, resolved recommendation outcomes",
            "样本不足时不能宣称80%真实概率",
        ],
        [
            "被动手动执行边界",
            "Manual Dispatch Contract / Current Turn Closeout",
            "manual_dispatch_run.py, next_dispatch_readiness.py",
            "不自动下单、不转账；该收口时停止长跑",
        ],
    ]
    state_rows = [
        ["report_max_allowed_action", f"`{evidence.get('report_max_allowed_action')}`"],
        ["execute_now_allowed", f"`{evidence.get('execute_now_allowed')}`"],
        ["research_committee_degraded", f"`{evidence.get('research_committee_degraded')}`"],
        ["validation_sample_status", f"`{evidence.get('validation_sample_status')}`"],
    ]
    return "\n".join([
        "## 5A. 目标到机制映射",
        "",
        "这张表回答“这个报告到底怎样服务你的目标”。它不是收益保证，而是确保每个目标都有对应的数据、脚本、证据和降级规则。",
        "",
        markdown_table(["目标", "负责面板", "证据/脚本", "缺失时处理"], trace_rows),
        "",
        markdown_table(["当前门槛", "状态"], state_rows),
        "",
        "术语备注：`traceability` 是目标追溯，意思是每个目标都要能找到负责的面板和证据；`execute_now` 是可立即人工确认的动作，不是自动下单。",
    ])


def render_strategy_library_section(context: dict[str, Any]) -> str:
    research = context.get("research_panel") or {}
    validation = research.get("external_agent_validation") or {}
    rec_write = context.get("recommendation_history_write") or {}
    promotion = context.get("strategy_promotion_evidence_panel") or {}
    iteration = context.get("strategy_iteration_backlog_panel") or {}
    walkforward = promotion.get("walkforward_metrics") or {}
    validation_audit = promotion.get("validation_sample_audit") or {}
    validation_plan = validation_audit.get("validation_sample_plan") or {}
    paper_samples = validation_audit.get("paper_sample_metrics") or {}
    portfolio_samples = validation_audit.get("current_portfolio_metrics") or {}
    rec_samples = validation_audit.get("recommendation_sample_metrics") or {}
    promotion_rows = []
    for item in promotion.get("strategies") or []:
        promotion_rows.append([
            item.get("strategy_id"),
            item.get("promotion_status"),
            item.get("max_allowed_action"),
            item.get("evidence_status"),
            short("; ".join(item.get("failed_gates") or []), 90),
        ])
    promotion_by_id = {
        item.get("strategy_id"): item
        for item in promotion.get("strategies") or []
        if item.get("strategy_id")
    }
    strategy_descriptions = [
        [
            "goal_weighted_dca",
            "Crypto 月度 DCA",
            "SOL/ADA 两步 DCA；ETH/lcETH 不新增",
        ],
        [
            "staking_compound_satellite",
            "SOL/ADA/ETH-lcETH 质押复利",
            "APY 进入目标测算，但不能替代价格上涨",
        ],
        [
            "core_satellite_barbell",
            "核心-卫星/杠铃",
            "CRCL 保护；ETH 超配；新增资金偏 accelerator",
        ],
        [
            "convex_tail_sleeve",
            "NIGHT/高凸性尾仓",
            "float、解锁、深度和官方 thesis 未补齐前不扩大",
        ],
        [
            "dynamic_reserve_timing",
            "USDT/现金机会仓",
            "保留等待触发，但不长期闲置",
        ],
        [
            "trend_rotation_relay",
            "美股短中期趋势接力",
            "当前动态战术仓两步回补/止盈；未过双80不 execute_now",
        ],
        [
            "event_news_alpha",
            "消息/事件 Alpha",
            "DELL/MU/GCTS 等候选只进入 watch，不能单靠消息实盘",
        ],
        [
            "walk_forward_paper_validation",
            "回测/模拟验证",
            "样本不足时禁止写成真实 80% 胜率",
        ],
    ]
    rows = []
    for strategy_id, usage, implication in strategy_descriptions:
        evidence = promotion_by_id.get(strategy_id) or {}
        rows.append([
            strategy_id,
            usage,
            evidence.get("promotion_status") or "missing_evidence",
            evidence.get("max_allowed_action") or "watch",
            implication,
        ])
    gate_rows = [
        ["research_roles", f"`{validation.get('unique_known_role_count') or len(research.get('agent_outputs') or [])}`"],
        ["research_committee_degraded", f"`{research.get('research_committee_degraded')}`"],
        ["recommendation_history_write", f"`{rec_write.get('status')}` ({rec_write.get('written_count', 0)} records)"],
        ["strategy_promotion_gate_passed", f"`{promotion.get('tactical_promotion_gate_passed')}`"],
        ["strategy_evidence_max_action", f"`{promotion.get('max_real_action_from_evidence')}`"],
        ["validation_sample_max_action", f"`{validation_plan.get('max_allowed_action')}`"],
        ["validation_sample_failed_gates", f"`{short('; '.join(validation_plan.get('failed_gates') or []), 120)}`"],
        ["deduped_closed_paper_samples", f"`{paper_samples.get('closed_count')}` / `{(validation_audit.get('thresholds') or {}).get('min_closed_paper_trades')}`"],
        ["paper_current_equity_return", f"{pct(portfolio_samples.get('net_return_pct'), 2)} drawdown {pct(portfolio_samples.get('max_drawdown_pct'), 2)}"],
        ["paper_closed_return_quality", f"`{paper_samples.get('closed_notional_data_quality')}` {pct(paper_samples.get('realized_net_return_on_closed_notional_pct'), 2)}"],
        ["recommendation_calibration_resolved", f"`{rec_samples.get('resolved_count')}` / `{(validation_audit.get('thresholds') or {}).get('min_calibration_resolved')}`"],
        ["walkforward_frames", f"`{walkforward.get('frames_loaded')}`"],
        ["walkforward_stage_counts", f"`{walkforward.get('stage_counts')}`"],
        ["walkforward_best", f"`{walkforward.get('best_symbol')}` `{walkforward.get('best_interval')}` `{walkforward.get('best_stage')}`"],
        ["execute_now_policy", "`blocked unless double-80 + human confirmation + verified cash rail`"],
    ]
    sample_gap_rows = []
    for key, value in (validation_plan.get("sample_gaps") or {}).items():
        sample_gap_rows.append([key, f"`{value}`"])
    validation_queue_rows = []
    for item in validation_plan.get("next_validation_queue") or []:
        validation_queue_rows.append([
            item.get("symbol"),
            item.get("interval"),
            item.get("stage"),
            item.get("trade_count"),
            pct(item.get("win_rate_pct"), 2),
            pct(item.get("net_return_pct"), 2),
            pct(item.get("max_drawdown_pct"), 2),
        ])
    walkforward_rows = []
    for item in walkforward.get("top_candidates") or []:
        walkforward_rows.append([
            item.get("symbol"),
            item.get("interval"),
            item.get("stage"),
            item.get("trade_count"),
            pct(item.get("win_rate_pct"), 2),
            pct(item.get("net_return_pct"), 2),
            pct(item.get("max_drawdown_pct"), 2),
        ])
    iteration_rows = []
    for item in iteration.get("candidate_strategies") or []:
        iteration_rows.append([
            item.get("strategy_id"),
            item.get("family"),
            item.get("objective"),
            item.get("current_allowed_action"),
            short(item.get("plain_note"), 120),
        ])
    return "\n".join([
        "## 6. 策略库与晋级状态",
        "",
        markdown_table(["策略族", "用途", "晋级状态", "最大动作", "本轮含义"], rows),
        "",
        "**证据驱动晋级判定**",
        "",
        markdown_table(["strategy_id", "promotion_status", "max_action", "evidence_status", "failed_gates"], promotion_rows),
        "",
        markdown_table(["Gate", "状态"], gate_rows),
        "",
        "**验证样本台账（防止把研究收益误写成实盘把握）**",
        "",
        markdown_table(["缺口", "还需要"], sample_gap_rows or [["n/a", "`0`"]]),
        "",
        markdown_table(["symbol", "interval", "stage", "trades", "win_rate", "net_return", "max_dd"], validation_queue_rows or [["n/a", "n/a", "missing", 0, "`n/a`", "`n/a`", "`n/a`"]]),
        "",
        "**Walk-forward 研究证据（只做晋级参考，不等于实盘许可）**",
        "",
        markdown_table(["symbol", "interval", "stage", "trades", "win_rate", "net_return", "max_dd"], walkforward_rows or [["n/a", "n/a", "missing", 0, "`n/a`", "`n/a`", "`n/a`"]]),
        "",
        "**下一轮策略迭代候选（不是买卖清单）**",
        "",
        markdown_table(["strategy_id", "family", "objective", "max_action", "说明"], iteration_rows or [["n/a", "n/a", "n/a", "watch", "No strategy iteration backlog generated"]]),
    ])


def render_research_section(context: dict[str, Any]) -> str:
    panel = context.get("research_panel") or {}
    agent_rows = []
    for output in panel.get("agent_outputs") or []:
        agent_rows.append([
            output.get("agent_id"),
            output.get("origin") or "unknown",
            output.get("recommended_max_action"),
            pct(output.get("confidence_pct"), 0),
            output.get("data_quality"),
            short("; ".join(output.get("missing_data") or []), 90),
        ])
    missing = panel.get("missing_data_summary") or []
    disconfirming = panel.get("disconfirming_evidence") or []
    return "\n".join([
        "## 7. 旧结论挑战与 Research Committee",
        "",
        markdown_table(
            ["字段", "结论"],
            [
                ["research_method", f"`{panel.get('research_method')}`"],
                ["research_committee_degraded", f"`{panel.get('research_committee_degraded')}`"],
                ["prior_thesis_status", f"`{panel.get('prior_thesis_status')}`"],
                ["old_thesis_reuse_allowed", f"`{panel.get('old_thesis_reuse_allowed')}`"],
                ["max_allowed_action", f"`{panel.get('max_allowed_action')}`"],
                ["external_roles_used", short(", ".join(panel.get("external_roles_used") or []), 160)],
                ["local_fallback_roles", short(", ".join(panel.get("local_fallback_roles") or []), 160)],
                ["missing_external_roles", short(", ".join(panel.get("missing_external_roles") or []), 160)],
                ["arbiter_decision", short(panel.get("arbiter_decision"), 180)],
            ],
        ),
        "",
        markdown_table(["subagent", "origin", "max_action", "confidence", "data_quality", "missing_data"], agent_rows),
        "",
        "**反证 / 降级点**",
        "",
        "\n".join(f"- {item}" for item in disconfirming[:6]) or "- 无",
        "",
        "**缺失数据摘要**",
        "",
        "\n".join(f"- {item}" for item in missing[:12]) or "- 无",
    ])


def render_macro_section(context: dict[str, Any]) -> str:
    macro = context.get("macro_regime_panel") or {}
    summary = macro.get("summary") or {}
    regime = summary.get("macro_regime") or {}
    missing = summary.get("missing_data") or []
    rows = [
        ["data_quality", f"`{macro.get('status')}`"],
        ["classification", f"`{regime.get('classification')}`"],
        ["risk_score", f"`{regime.get('risk_score_points')}`"],
        ["DCA节奏", f"`{regime.get('dca_pace')}`"],
        ["美股战术姿态", f"`{regime.get('us_tactical_risk_posture')}`"],
        ["时间", f"`{summary.get('generated_at')}`"],
    ]
    return "\n".join([
        "## 8. 宏观与资金流节奏",
        "",
        markdown_table(["项目", "状态"], rows),
        "",
        "宏观只调整 DCA 节奏和战术 sizing，不能单独触发某个标的强买入。",
        "",
        "**宏观缺口**",
        "",
        "\n".join(f"- {item}" for item in missing[:8]) or "- 无",
    ])


def render_long_term_trend_matrix_section(context: dict[str, Any]) -> str:
    panel = (context.get("asset_goal_contribution_panel") or {}).get("summary") or {}
    crypto_assets = crypto_asset_map(context)
    rows = []
    for item in panel.get("assets") or []:
        symbol = item.get("symbol")
        market = crypto_assets.get(f"{symbol}USDT") or crypto_assets.get(symbol) or {}
        staking = item.get("staking_compounding_summary") or {}
        rows.append([
            symbol,
            item.get("goal_gap_contribution"),
            item.get("dca_weight_implication"),
            price(market.get("last_price")) if market else "`n/a`",
            pct(market.get("change_24h_pct")) if market else "`n/a`",
            pct(staking.get("apy_used_pct")),
            short("; ".join(item.get("rationale") or []), 110),
        ])
    return "\n".join([
        "## 8A. 长期趋势矩阵",
        "",
        "这张表只展示本次持仓和推荐真正相关的资产；它把长期目标贡献、市场位置和质押复利放在同一层看。",
        "",
        markdown_table(
            ["资产", "目标角色", "DCA含义", "现价", "24h", "质押APY", "长期判断依据"],
            rows or [["n/a", "missing", "watch", "`n/a`", "`n/a`", "`n/a`", "no asset goal panel"]],
        ),
    ])


def render_long_term_price_scenario_section(context: dict[str, Any]) -> str:
    panel = context.get("long_term_price_scenario_panel") or {}
    summary = panel.get("summary") or {}
    rows = []
    for item in summary.get("assets") or []:
        rows.append([
            item.get("symbol"),
            price(item.get("current_price")),
            money(item.get("current_market_cap")),
            money(item.get("current_fdv")),
            scenario_range_text(item.get("five_year_base_price_range")),
            scenario_range_text(item.get("ten_year_base_price_range")),
            scenario_range_text(item.get("ten_year_bull_price_range")),
            item.get("ten_x_goal_fit"),
            item.get("dca_implication"),
            item.get("confidence_band"),
            short("; ".join(item.get("downgrade_reasons") or []), 120),
        ])
    missing = summary.get("missing_data") or []
    return "\n".join([
        "## 8A-1. Long-Term Price Scenario Panel",
        "",
        "这张表把长期 thesis 翻译成 5年/10年价格情景。它不是价格保证，只用于判断新增 DCA 是否提高 10x 目标概率。",
        "",
        markdown_table(
            ["资产", "现价", "市值", "FDV", "5y base", "10y base", "10y bull", "10x适配", "DCA含义", "置信带", "降级原因"],
            rows or [["n/a", "`n/a`", "`n/a`", "`n/a`", "`n/a`", "`n/a`", "`n/a`", "missing", "watch", "low", "no scenario panel"]],
        ),
        "",
        "**质押/复利说明**",
        "",
        "\n".join(
            f"- `{item.get('symbol')}`: {item.get('staking_adjusted_return_note')}"
            for item in (summary.get("assets") or [])[:8]
        ) or "- 无",
        "",
        "**缺口 / 降级**",
        "",
        "\n".join(f"- {item}" for item in missing[:10]) or "- 无",
        "",
        "术语备注：`base` 是中性情景；`bull` 是强牛市情景；`FDV` 是完全稀释估值，意思是把未来可能释放的供应也算进去看估值压力。",
    ])


def render_crypto_key_person_section(context: dict[str, Any]) -> str:
    handoffs = [
        item
        for item in context.get("latest_active_alpha_handoffs") or []
        if item.get("candidate_type") == "social_key_person_intel"
    ]
    rows = []
    for item in handoffs[:5]:
        rows.append([
            item.get("handoff_id"),
            item.get("created_at"),
            item.get("monitor_recommendation"),
            item.get("max_allowed_action"),
            f"`{item.get('research_committee_degraded')}`",
            short(item.get("research_panel_missing_reason"), 140),
        ])
    if not rows:
        rows.append([
            "no_fresh_social_handoff",
            "`n/a`",
            "watch",
            "watch",
            "`True`",
            "No fresh verified crypto key-person handoff in current context.",
        ])
    return "\n".join([
        "## 8B. Crypto Key Person Intelligence Panel",
        "",
        "关键人物/官方社交情报只能改变观察优先级、风险提示或 conditional 条件；单源社交消息不能单独触发真实买入。",
        "",
        markdown_table(
            ["handoff", "时间", "monitor建议", "动作上限", "research降级", "原因/限制"],
            rows,
        ),
    ])


def render_asset_micro_and_staking_section(context: dict[str, Any]) -> str:
    panel = (context.get("asset_goal_contribution_panel") or {}).get("summary") or {}
    crypto_assets = crypto_asset_map(context)
    micro_rows = []
    staking_rows = []
    for item in panel.get("assets") or []:
        symbol = item.get("symbol")
        market = crypto_assets.get(f"{symbol}USDT") or crypto_assets.get(symbol) or {}
        staking = item.get("staking_compounding_summary") or {}
        micro_rows.append([
            symbol,
            item.get("data_quality_status"),
            money(market.get("quote_volume_24h_usd")) if market else "`n/a`",
            price(market.get("bid")) if market else "`n/a`",
            price(market.get("ask")) if market else "`n/a`",
            pct(market.get("spread_bps"), 4) if market else "`n/a`",
            short("; ".join(item.get("missing_data") or []), 100),
        ])
        staking_rows.append([
            symbol,
            f"`{staking.get('staking_allowed')}`",
            staking.get("provider_or_protocol"),
            pct(staking.get("apy_used_pct")),
            f"`{staking.get('count_as_immediate_liquidity')}`",
            f"`{staking.get('five_year_compounding_multiplier')}`",
            f"`{staking.get('ten_year_compounding_multiplier')}`",
            f"`{staking.get('five_year_price_multiple_required_for_10x_after_staking')}`",
            f"`{staking.get('ten_year_price_multiple_required_for_10x_after_staking')}`",
        ])
    return "\n".join([
        "## 8C. Asset Micro Thesis Matrix / Staking Compounding Model",
        "",
        "**微观数据矩阵**",
        "",
        markdown_table(
            ["资产", "数据质量", "24h成交额", "bid", "ask", "spread_bps", "缺失数据"],
            micro_rows or [["n/a", "missing", "`n/a`", "`n/a`", "`n/a`", "`n/a`", "no asset rows"]],
        ),
        "",
        "**质押复利模型**",
        "",
        markdown_table(
            ["资产", "可质押", "服务/协议", "APY", "立即流动性", "5y复利倍数", "10y复利倍数", "5y达10x所需价格倍数", "10y达10x所需价格倍数"],
            staking_rows or [["n/a", "`False`", "n/a", "`n/a`", "`False`", "`n/a`", "`n/a`", "`n/a`", "`n/a`"]],
        ),
    ])


def render_dca_section(context: dict[str, Any]) -> str:
    panel = (context.get("asset_goal_contribution_panel") or {}).get("summary") or {}
    guidance = panel.get("dca_guidance") or {}
    crypto_market = (context.get("crypto_market_panel") or {}).get("summary") or {}
    crypto_assets = crypto_asset_map(context)
    risk_by_symbol = {
        str(item.get("symbol") or "").replace("USDT", ""): risk_path_summary(item)
        for item in (context.get("impulse_capture_panel") or {}).get("signals") or []
        if isinstance(item, dict) and item.get("risk_adjusted_path")
    }
    fear = crypto_market.get("fear_greed") or {}
    rows = []
    for item in guidance.get("suggested_ranges") or []:
        bounds = item.get("suggested_monthly_range_usd") or []
        amount = " - ".join(money(value) for value in bounds) if bounds else "`n/a`"
        symbol = item.get("symbol")
        market_symbol = f"{symbol}USDT" if symbol not in {"USDT"} else "USDT"
        market = crypto_assets.get(market_symbol, {})
        risk = risk_by_symbol.get(str(symbol), {})
        dca_pacing = risk.get("dca_pacing") or {}
        rows.append([
            symbol,
            item.get("role"),
            amount,
            price(market.get("last_price")) if market else "`n/a`",
            pct(market.get("change_24h_pct")),
            f"{risk.get('sharpe_20')}/{risk.get('sharpe_60')}",
            risk.get("persistence_label") or "missing",
            dca_pacing.get("pace") or "longterm_thesis_first",
            short(item.get("condition"), 90),
        ])
    execution_rows = build_dca_execution_rows(context)
    avoid = guidance.get("avoid_or_zero_new_dca") or []
    timing_rows = []
    for item in guidance.get("long_horizon_timing_decisions") or []:
        timing_rows.append([
            item.get("symbol"),
            f"`{item.get('decision')}`",
            item.get("dynamic_buy_strategy"),
            item.get("long_term_low_value_zone_status") or item.get("long_term_value_zone_status"),
            money(item.get("planned_amount_usd")),
            money(item.get("front_load_amount_usd")),
            money(item.get("near_term_total_budget_usd")),
            item.get("time_in_market_bias_score"),
            "yes" if item.get("wait_requires_specific_pullback_trigger") else "no",
            f"{item.get('planned_wait_days')}d",
            money(item.get("staking_wait_cost_usd")),
            short(f"now {item.get('estimated_staking_start_if_buy_now') or 'n/a'} / wait {item.get('estimated_staking_start_if_wait') or 'n/a'}", 48),
            money(item.get("staking_activation_delay_cost_usd")),
            pct(item.get("required_pullback_to_wait_pct")),
            short(item.get("cash_idle_drag_comment"), 70),
            short(item.get("reason"), 110),
        ])
    return "\n".join([
        "## 9. Crypto DCA 方向",
        "",
        markdown_table(
            ["数据", "状态"],
            [
                ["market_snapshot", f"`{crypto_market.get('generated_at')}`"],
                ["Fear & Greed", f"`{fear.get('value')} / {fear.get('classification')}`"],
                ["live_orders_enabled", f"`{crypto_market.get('live_orders_enabled')}`"],
            ],
        ),
        "",
        markdown_table(
            ["字段", "结论"],
            [
                ["primary_pair", f"`{guidance.get('primary_pair')}`"],
                ["secondary_pair", f"`{guidance.get('secondary_pair')}`"],
                ["satellite_pair", f"`{guidance.get('satellite_pair')}`"],
                ["monthly_dca", money(guidance.get("monthly_dca_usd"))],
            ],
        ),
        "",
        markdown_table(
            ["标的", "角色", "建议月度区间", "现价", "24h", "Sharpe 20/60", "路径状态", "DCA节奏影响", "条件"],
            rows,
        ),
        "",
        "**长期低位 / 质押机会成本判断**",
        "",
        "这张表回答：现在买并开始质押，是否比继续等回调更划算。`时间在场倾向` 越高，越说明长期低位、质押和低配因素支持先买第一档；`required_pullback_to_wait` 是等待需要换来的最低折扣，低于这个折扣时更偏向先买；`质押启动` 用来比较现在买与等待后买，奖励大概何时开始体现；`现金闲置拖累` 指现金等待是否正在拖慢 5-10 年 10x 目标。",
        "",
        markdown_table(
            ["资产", "时间决策", "动态买入策略", "长期低位状态", "计划金额", "可前置", "近期待投入上限", "时间在场倾向", "等待需触发", "等待期", "等待少拿质押", "质押启动", "启动延迟成本", "等待所需折扣", "现金闲置拖累", "原因"],
            timing_rows or [["n/a", "`missing`", "n/a", "n/a", "`n/a`", "`n/a`", "`n/a`", "n/a", "n/a", "n/a", "`n/a`", "n/a", "`n/a`", "`n/a`", "n/a", "timing model missing"]],
        ),
        "",
        "**两步 DCA 执行表（最多两档；真实下单仍需人工确认）**",
        "",
        "`action_source`: `context_dynamic_dca_builder`；金额来自 `asset_goal_contribution_panel.dca_guidance`，价格区间来自实时 24h 波动/价差，而不是固定币种模板。",
        "",
        markdown_table(["交易对", "动作", "近价小仓", "更优回调主仓", "复盘", "失效/降级"], execution_rows),
        "",
        "**本期不新增或仅机会型**",
        "",
        "\n".join(f"- `{item.get('symbol')}`: {item.get('reason')}" for item in avoid) or "- 无",
    ])


def render_candidate_deep_dive_section(context: dict[str, Any]) -> str:
    rows = []
    crypto_risk = {
        str(item.get("symbol") or "").replace("USDT", ""): risk_path_summary(item)
        for item in (context.get("impulse_capture_panel") or {}).get("signals") or []
        if isinstance(item, dict) and item.get("risk_adjusted_path")
    }
    for pair, action, near_entry, optimal_entry, review, condition in build_dca_execution_rows(context):
        base_symbol = str(pair or "").replace("/USDT", "").replace("USDT", "")
        risk = crypto_risk.get(base_symbol, {})
        rows.append([
            pair,
            action,
            "crypto_dca",
            f"{risk.get('persistence_label') or 'missing'}; S20/S60={risk.get('sharpe_20')}/{risk.get('sharpe_60')}",
            near_entry,
            optimal_entry,
            "monthly DCA window",
            "long-term scenario",
            review,
            "conditional, not calibrated 80%",
            "data_quality_dependent",
            condition,
        ])
    for item in scanner_candidates(context)[:3]:
        risk = risk_path_summary(item)
        rows.append([
            item.get("symbol"),
            item.get("monitor_recommendation") or "watch",
            "us_tactical_candidate",
            f"{risk.get('persistence_label')}; score={risk.get('quality_score')}; adj={risk.get('ranking_adjustment_points')}",
            item.get("entry_zone"),
            item.get("entry_zone"),
            item.get("target_time_window"),
            price(item.get("target_price")),
            item.get("latest_exit_date"),
            f"{item.get('forecast_probability_pct')}%",
            f"{item.get('execution_readiness_score')} pts",
            item.get("stop_loss"),
        ])
    tactical_summary = ((context.get("us_tactical_performance_panel") or {}).get("summary") or {})
    tactical_component = next(
        (
            item
            for item in tactical_summary.get("current_components") or []
            if item.get("inclusion_reason") == "current_tactical_position"
        ),
        {},
    )
    for symbol, role, secondary, optimal, trim, full_exit, timing in build_us_tactical_ladder_rows(context, tactical_component):
        rows.append([
            symbol,
            "conditional_action",
            role,
            "current_position_path_recertification_required",
            secondary,
            optimal,
            timing,
            trim,
            timing,
            "requires double-80 for execute_now",
            "readiness_degraded_until_cash_and_research_verified",
            full_exit,
        ])
    return "\n".join([
        "## 9A. Unified Candidate Deep Dive",
        "",
        "所有候选先进入卡片，再进入最终行动清单；缺少价格、时间、风险或数据质量时只能 watch / paper / conditional。",
        "",
        markdown_table(
            [
                "标的",
                "动作上限",
                "类别",
                "风险调整路径",
                "次优/近价入场",
                "最优入场",
                "目标窗口",
                "目标/减仓",
                "最晚复盘/退出",
                "真实概率",
                "执行准备度",
                "失效/止损",
            ],
            rows or [["n/a", "watch", "missing", "missing", "`n/a`", "`n/a`", "`n/a`", "`n/a`", "`n/a`", "`n/a`", "`n/a`", "no candidates"]],
        ),
    ])


def render_technical_execution_window_section(context: dict[str, Any]) -> str:
    crypto_assets = crypto_asset_map(context)
    rows = []
    for symbol in ["SOLUSDT", "ADAUSDT", "NIGHTUSDT", "ETHUSDT", "BTCUSDT"]:
        item = crypto_assets.get(symbol)
        if not item:
            continue
        rows.append([
            symbol,
            price(item.get("last_price")),
            price(item.get("low_24h")),
            price(item.get("high_24h")),
            pct(item.get("change_24h_pct")),
            money(item.get("quote_volume_24h_usd")),
            pct(item.get("spread_bps"), 4),
            "DCA最多两档；缺少深度/链上数据时降级",
        ])
    scanner = (context.get("us_open_dynamic_scanner_panel") or {}).get("summary") or {}
    for item in scanner_candidates(context)[:3]:
        rows.append([
            item.get("symbol"),
            price(item.get("price")),
            item.get("entry_zone"),
            price(item.get("target_price")),
            "`n/a`",
            "`n/a`",
            "`n/a`",
            f"US tactical watch; latest_exit={item.get('latest_exit_date')}",
        ])
    rows.append([
        scanner.get("deployable_tactical_position") or "current_tactical_position",
        "`see US tactical panel`",
        "`secondary_entry`",
        "`trim/full_exit`",
        "`n/a`",
        "`n/a`",
        "`n/a`",
        "当前战术仓用两步行动表，不 hardcode SOXL",
    ])
    return "\n".join([
        "## 9B. Technical Execution Window",
        "",
        "技术执行窗口只负责把已通过目标/风险筛选的想法压缩成最多两档入场/出场，不负责绕过 Research Committee 或双80门槛。",
        "",
        markdown_table(
            ["标的", "现价", "24h低/入场", "24h高/目标", "24h", "成交额", "价差bps", "执行说明"],
            rows,
        ),
    ])


def render_us_tactical_section(context: dict[str, Any]) -> str:
    panel = context.get("us_tactical_performance_panel") or {}
    summary = panel.get("summary") or {}
    scanner = (context.get("us_open_dynamic_scanner_panel") or {}).get("summary") or {}
    components = []
    tactical_component: dict[str, Any] | None = None
    for item in summary.get("current_components") or []:
        if item.get("inclusion_reason") == "current_tactical_position" and tactical_component is None:
            tactical_component = item
        components.append([
            item.get("symbol"),
            money(item.get("current_value_usd")),
            item.get("inclusion_reason"),
            short(item.get("data_quality"), 50),
        ])
    ladder_rows = build_us_tactical_ladder_rows(context, tactical_component)
    candidate_rows = []
    for item in scanner_candidates(context)[:3]:
        gate = "watch"
        try:
            if float(item.get("forecast_probability_pct") or 0) >= 80 and float(item.get("execution_readiness_score") or 0) >= 80:
                gate = "conditional_review"
        except (TypeError, ValueError):
            gate = "watch"
        risk = risk_path_summary(item)
        candidate_rows.append([
            item.get("symbol"),
            price(item.get("price")),
            item.get("entry_zone"),
            price(item.get("target_price")),
            price(item.get("stop_loss")),
            f"{risk.get('sharpe_20')}/{risk.get('sharpe_60')}",
            risk.get("information_ratio_60"),
            pct(risk.get("max_drawdown_60_pct")),
            f"{risk.get('persistence_label')} / {risk.get('ranking_adjustment_points')}",
            f"{item.get('forecast_probability_pct')}% / {item.get('execution_readiness_score')} pts",
            gate,
        ])
    rows = [
        ["sleeve_id", f"`{summary.get('sleeve_id')}`"],
        ["当前战术池", money(summary.get("current_tactical_value_usd"))],
        ["当前收益率", pct(summary.get("current_return_pct"))],
        ["月度50%目标", money(summary.get("monthly_target_value_usd"))],
        ["月度缺口", money(summary.get("monthly_gap_usd"))],
        ["战术现金", money(summary.get("tactical_cash_value_usd"))],
        ["现金拖累", pct(summary.get("tactical_cash_drag_pct"))],
        ["scanner_status", f"`{scanner.get('scan_status')}`"],
        ["deployable_tactical_position", f"`{scanner.get('deployable_tactical_position')}`"],
        ["data_quality", f"`{summary.get('data_quality')}`"],
        ["max_allowed_action", f"`{summary.get('max_allowed_action')}`"],
    ]
    return "\n".join([
        "## 10. 美股战术池与接力",
        "",
        markdown_table(["项目", "数值"], rows),
        "",
        markdown_table(["组成", "金额", "纳入原因", "数据质量"], components),
        "",
        "**当前战术仓两步接力计划**",
        "",
        "`action_source`: `context_dynamic_us_tactical_builder`；入场/退出区间由当前战术仓最近 1d/7d/30d 走势与当前价动态生成，不固定写死 SOXL。",
        "",
        markdown_table(["标的", "角色", "次优入场", "最优入场", "部分止盈/减仓", "全部退出/失效", "时间"], ladder_rows),
        "",
        "**动态候选审查（未过双80不替换当前战术仓）**",
        "",
        markdown_table(
            ["候选", "现价", "入场区", "目标", "止损", "Sharpe 20/60", "IR60", "MDD60", "路径/排名调整", "概率/执行度", "动作上限"],
            candidate_rows,
        ),
        "",
        "行动格式保持最多两档：次优入场、最优入场；部分止盈/全部退出。若动态候选未过真实概率80%和执行度80分，不能升级为 execute_now。",
    ])


def render_missing_and_actions(context: dict[str, Any]) -> str:
    missing_rows = []
    for item in context.get("missing_data_downgrade_panel") or []:
        missing_rows.append([
            item.get("category"),
            item.get("status"),
            item.get("impact"),
            short(item.get("reason"), 110),
        ])
    readiness = context.get("report_readiness") or {}
    rec = context.get("recommendation_history_summary") or {}
    write_status = context.get("recommendation_history_write") or {}
    review_drafts = context.get("recommendation_outcome_review_drafts") or {}
    pending = rec.get("pending_recommendations") or []
    due_review = rec.get("due_review") or []
    rec_summary = rec.get("summary") or {}
    pending_rows = [
        [
            item.get("recommendation_id"),
            item.get("symbol"),
            item.get("action"),
            item.get("time_window"),
            item.get("outcome_status"),
        ]
        for item in pending[:12]
    ]
    due_rows = [
        [
            item.get("recommendation_id"),
            item.get("symbol"),
            item.get("action"),
            item.get("due_at"),
            item.get("days_overdue"),
        ]
        for item in due_review[:8]
    ]
    calibration_table_rows = calibration_rows(rec)
    resolved_count = sum(int((row[2] or 0)) for row in calibration_table_rows)
    learning_status = (
        "calibration_available" if resolved_count else "calibration_unavailable_pending_outcomes"
    )
    progressive = progressive_learning_summary(rec)
    tactical_summary = ((context.get("us_tactical_performance_panel") or {}).get("summary") or {})
    tactical_component = next(
        (
            item
            for item in tactical_summary.get("current_components") or []
            if item.get("inclusion_reason") == "current_tactical_position"
        ),
        {},
    )
    action_rows = []
    for pair, action, near_entry, optimal_entry, review, condition in build_dca_execution_rows(context):
        channel = "Crypto reserve" if pair == "USDT" else ("Crypto tail" if "tail" in str(action) else "Crypto DCA")
        action_rows.append([
            channel,
            action,
            f"{pair}: {near_entry}；{optimal_entry}",
            "本月新增 crypto cash 确认到账；按目标差距和数据质量执行",
            f"{condition}；{review}",
        ])
    for symbol, _role, secondary, optimal, trim, full_exit, timing in build_us_tactical_ladder_rows(context, tactical_component):
        action_rows.append([
            "US tactical",
            "conditional_action",
            f"{symbol}: 次优回补 {secondary}；最优回补 {optimal}",
            "美股 buying power 确认；当前战术主线未失效；无更强双80候选",
            f"止盈/减仓 {trim}；强制退出/失效 {full_exit}；{timing}",
        ])
    action_rows.append([
        "Protected hold",
        "hold",
        "CRCL 不作为常规战术资金来源；ETH/lcETH 不新增",
        "长期 thesis 未破坏但仓位/集中度已偏高",
        "只在 thesis 破坏、严重超配或极端低估时单独复盘",
    ])
    return "\n".join([
        "## 11. 缺失数据 / 降级 / 最终动作",
        "",
        markdown_table(["类别", "状态", "影响", "原因"], missing_rows),
        "",
        markdown_table(
            ["字段", "结论"],
            [
                ["can_generate_today_report", f"`{readiness.get('can_generate_today_report')}`"],
                ["max_allowed_action", f"`{readiness.get('max_allowed_action')}`"],
                ["execute_now_allowed", f"`{readiness.get('execute_now_allowed')}`"],
                ["recommendation_history_write", f"`{write_status.get('status')}` ({write_status.get('written_count', 0)} records)"],
                ["reason", readiness.get("reason")],
            ],
        ),
        "",
        "**本轮默认最终操作清单（可执行摘要）**",
        "",
        markdown_table(["通道/资产", "动作上限", "价格/金额动作", "触发条件", "失效/复盘"], action_rows),
        "",
        "**Recommendation History 学习闭环 / 概率校准**",
        "",
        "**Progressive Learning Confidence Gate**",
        "",
        markdown_table(
            ["字段", "状态"],
            [
                ["learning_stage", f"`{progressive.get('stage')}`"],
                ["next_stage", f"`{progressive.get('next_stage')}`"],
                ["resolved_count", f"`{progressive.get('resolved_count')}`"],
                ["resolved_needed_for_next_stage", f"`{progressive.get('resolved_needed_for_next_stage')}`"],
                ["60_to_79_learning_samples", f"`{progressive.get('learning_60_to_79_count')}` total / `{progressive.get('learning_60_to_79_resolved')}` resolved"],
                ["80_plus_strong_candidates", f"`{progressive.get('strong_80_plus_count')}` total / `{progressive.get('strong_80_plus_resolved')}` resolved"],
                ["max_learning_action", f"`{progressive.get('max_learning_action')}`"],
                ["execute_now_note", progressive.get("execute_now_note")],
            ],
        ),
        "",
        "这对你意味着什么：`60%-79%` 的判断可以作为学习型 watch/paper/conditional 进入账本；只有复盘样本逐步证明有效，才允许把同类信号提高到 `80%+` 的强候选。",
        "",
        markdown_table(
            ["字段", "状态"],
            [
                ["total_recommendations", f"`{rec_summary.get('total_recommendations')}`"],
                ["status_counts", f"`{rec_summary.get('status_counts')}`"],
                ["due_review_count", f"`{len(due_review)}`"],
                ["outcome_reviews", f"`{rec_summary.get('outcome_reviews')}`"],
                ["learning_status", f"`{learning_status}`"],
                ["resolved_hit_rate", pct(rec_summary.get("hit_rate_pct_resolved_only"))],
            ],
        ),
        "",
        markdown_table(
            ["probability_bucket", "count", "resolved", "hit", "failed", "hit_rate", "brier"],
            calibration_table_rows,
        ),
        "",
        "**到期未复盘建议**",
        "",
        markdown_table(["recommendation_id", "symbol", "action", "due_at", "days_overdue"], due_rows)
        if due_rows
        else "- 当前没有到期但未复盘的建议；概率校准仍需等待建议自然到期。",
        "",
        "**Recommendation Outcome Review 草案**",
        "",
        markdown_table(
            ["字段", "状态"],
            [
                ["pending_count", f"`{review_drafts.get('pending_count')}`"],
                ["due_or_reviewable_count", f"`{review_drafts.get('due_or_reviewable_count')}`"],
                ["draft_review_count", f"`{review_drafts.get('draft_review_count')}`"],
                ["upcoming_count", f"`{review_drafts.get('upcoming_count')}`"],
                ["fetch_market_data", f"`{review_drafts.get('fetch_market_data')}`"],
            ],
        ),
        "",
        "**Recommendation History 待复盘**",
        "",
        markdown_table(["recommendation_id", "symbol", "action", "window", "status"], pending_rows),
    ])


def render_report(context: dict[str, Any], run_id: str, date_text: str, steps: list[dict[str, Any]], research_path: Path) -> str:
    portfolio = context.get("portfolio_snapshot") or {}
    readiness = context.get("report_readiness") or {}
    research = context.get("research_panel") or {}
    title = f"# Manual Investment Strategy Report | {date_text}"
    metadata = markdown_table(
        ["字段", "内容"],
        [
            ["run_id", f"`{run_id}`"],
            ["skill_version", f"`{STRATEGY_VERSION}`"],
            ["generated_at", f"`{context.get('generated_at')}`"],
            ["portfolio_value", money(portfolio.get("total_value"))],
            ["research_panel", f"`{research_path}`"],
            ["research_committee_degraded", f"`{research.get('research_committee_degraded')}`"],
            ["max_allowed_action", f"`{readiness.get('max_allowed_action')}`"],
            ["execute_now_allowed", f"`{readiness.get('execute_now_allowed')}`"],
        ],
    )
    step_rows = [[item.get("step"), item.get("status"), short(item.get("output_path") or item.get("error"), 100)] for item in steps]
    one_page = "\n".join([
        "## 1. 一页结论",
        "",
        "1. 这次报告通过 Research Committee Gate 生成：若没有外部 6+ 真实 subagent JSON，系统会自动标记降级，并阻止新的 `execute_now`。",
        "2. 长期目标仍以 5年/10年 10x 为锚：新增 crypto DCA 要优先提高 accelerator 权重，而不是机械补 BTC/ETH。",
        "3. 美股战术池以动态识别的可部署仓位和现金接力为核心；只有候选真实目标达成概率 >=80% 且执行度 >=80，才允许主推荐。",
        "4. Sharpe/Sortino/相对基准只作为收益路径质量次级因子：高 Sharpe 不等于低估或高胜率，负 Sharpe 也不会自动删除已通过价值与催化门的修复候选。",
    ])
    sources = context.get("source_files") or {}
    source_lines = "\n".join(f"- `{key}`: `{value}`" for key, value in sources.items() if value)

    return "\n\n".join([
        title,
        "本报告不是财务建议，不自动下单；所有真实交易都需要你人工确认。",
        "## Run Metadata",
        metadata,
        one_page,
        render_fresh_market_intelligence_section(context),
        render_sanm_paper_review_section(context),
        render_impulse_continuity_section(context),
        render_risk_adjusted_path_section(context),
        render_full_market_deep_analysis_section(context),
        render_passive_dispatch_closeout_section(context),
        render_portfolio_section(context),
        render_cost_basis_section(context),
        render_goal_section(context),
        render_goal_execution_dashboard_section(context),
        render_objective_traceability_section(context),
        render_strategy_library_section(context),
        render_research_section(context),
        render_long_term_trend_matrix_section(context),
        render_long_term_price_scenario_section(context),
        render_macro_section(context),
        render_crypto_key_person_section(context),
        render_asset_micro_and_staking_section(context),
        render_dca_section(context),
        render_candidate_deep_dive_section(context),
        render_technical_execution_window_section(context),
        render_us_tactical_section(context),
        render_missing_and_actions(context),
        "## Pipeline Steps",
        markdown_table(["step", "status", "output/error"], step_rows),
        "## Sources",
        source_lines,
    ]) + "\n"


def next_report_path(output_dir: Path, date_text: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(output_dir.glob(f"{date_text}-goal-10x-daily-manual-report-*.md"))
    seq = len(existing) + 1
    return output_dir / f"{date_text}-goal-10x-daily-manual-report-{seq:03d}.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a full manual investment report")
    parser.add_argument("--date", default=local_date(), help="Report date in YYYY-MM-DD, defaults to Asia/Shanghai today")
    parser.add_argument("--run-id", help="Run id, defaults to YYYYMMDD-manual-report-001")
    parser.add_argument("--monthly-dca", type=float, default=1000.0)
    parser.add_argument("--use-existing-inputs", action="store_true", help="Use existing /private/tmp JSON inputs instead of fetching fresh data")
    parser.add_argument("--skip-active-scanners", action="store_true", help="Skip active-alpha crypto and US scanner live steps")
    parser.add_argument("--require-fresh-market-intelligence", action="store_true", help="Fail if the manual dispatch cannot prove fresh market/sentiment refresh")
    parser.add_argument("--env-file", help="Optional ignored local KEY=VALUE file for market-data API keys; values are never logged")
    parser.add_argument("--crypto-symbols", default=DEFAULT_CRYPTO_SYMBOLS)
    parser.add_argument("--current-tactical-symbol", default="")
    parser.add_argument("--external-agent-outputs-json", help="Optional real subagent outputs matching MULTI_AGENT_RESEARCH_SCHEMA.md")
    parser.add_argument("--require-external-subagents", action="store_true", help="Fail instead of generating a degraded fallback report when no external subagent JSON is available")
    parser.add_argument("--output-dir", default=str(MANUAL_ROOT / "reports"))
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--skip-recommendation-history-write", action="store_true", help="Do not append this run's recommendations to the learning ledger")
    parser.add_argument("--fetch-outcome-review-market-data", action="store_true", help="Fetch public historical prices for due recommendation review drafts")
    parser.add_argument("--skip-report-integrity-audit", action="store_true", help="Do not run the post-report integrity audit")
    parser.add_argument("--skip-objective-coverage-audit", action="store_true", help="Do not run the post-report top-level objective coverage audit")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()

    env_file_loaded_keys = load_env_file(args.env_file)
    run_id = args.run_id or f"{date_compact(args.date)}-manual-report-001"
    input_paths, steps = build_inputs(args, run_id, args.date)
    if env_file_loaded_keys:
        steps.insert(0, {
            "step": "env_file_load",
            "status": "ok",
            "output_path": f"{args.env_file} ({len(env_file_loaded_keys)} keys loaded; values redacted)",
        })
    final_context_path, research_path, context_steps = build_context_and_research(args, run_id, args.date, input_paths)
    steps.extend(context_steps)

    context = load_json(final_context_path)
    if input_paths.get("impulse"):
        context["impulse_capture_panel"] = load_json(input_paths["impulse"])
    if input_paths.get("us_scanner"):
        context["us_open_dynamic_scanner_full_payload"] = load_json(
            input_paths["us_scanner"]
        )
    if input_paths.get("sanm_paper_review"):
        context["sanm_paper_review_panel"] = load_json(input_paths["sanm_paper_review"])
    refresh_risk_adjusted_path_panel(context)
    fresh_market_snapshot = annotate_fresh_market_intelligence(context, input_paths, steps + context_steps, args, run_id)
    write_risk_adjusted_evidence_snapshot(context, run_id=run_id)
    if args.require_fresh_market_intelligence and fresh_market_snapshot.get("market_intelligence_degraded"):
        write_json(final_context_path, context)
        raise RuntimeError(
            "--require-fresh-market-intelligence was set, but Fresh Market Intelligence is degraded: "
            + ", ".join(fresh_market_snapshot.get("degraded_reasons") or ["unknown_reason"])
        )
    recommendation_write = write_recommendation_records(
        context,
        run_id,
        enabled=not args.skip_recommendation_history_write,
    )
    outcome_review_drafts = refresh_outcome_review_drafts(
        context,
        fetch_market_data=args.fetch_outcome_review_market_data,
    )
    strategy_promotion_evidence = refresh_strategy_promotion_evidence(context)
    strategy_iteration_backlog = refresh_strategy_iteration_backlog(context)
    finalize_execute_now_readiness(context)
    write_json(final_context_path, context)
    goal_execution_dashboard = refresh_goal_execution_dashboard(context, final_context_path, run_id)
    write_json(final_context_path, context)
    report_path = next_report_path(Path(args.output_dir), args.date)
    report = render_report(context, run_id, args.date, steps, research_path)
    write_text(report_path, report)
    audit_result = {"status": "skipped", "reason": "disabled by CLI"}
    if not args.skip_report_integrity_audit:
        audit_result = run_integrity_audit(report_path, final_context_path, run_id, timeout=args.timeout_seconds)
        report = report + "\n" + render_integrity_audit_section(audit_result)
        write_text(report_path, report)
        steps.append({
            "step": "report_integrity_audit",
            "status": audit_result.get("status"),
            "output_path": audit_result.get("audit_path"),
        })
    objective_audit_result = {"status": "skipped", "reason": "disabled by CLI"}
    if not args.skip_objective_coverage_audit:
        objective_audit_result = run_objective_coverage_audit(
            report_path,
            final_context_path,
            run_id,
            args.monthly_dca,
            timeout=args.timeout_seconds,
        )
        report = report + "\n" + render_objective_coverage_audit_section(objective_audit_result)
        write_text(report_path, report)
        steps.append({
            "step": "objective_coverage_audit",
            "status": objective_audit_result.get("status"),
            "output_path": objective_audit_result.get("audit_path"),
        })

    summary = {
        "run_id": run_id,
        "generated_at": utc_now(),
        "report_path": str(report_path),
        "daily_context_json": str(final_context_path),
        "research_panel_json": str(research_path),
        "research_committee_degraded": (context.get("research_panel") or {}).get("research_committee_degraded"),
        "max_allowed_action": (context.get("report_readiness") or {}).get("max_allowed_action"),
        "execute_now_allowed": (context.get("report_readiness") or {}).get("execute_now_allowed"),
        "recommendation_history_write": recommendation_write,
        "recommendation_outcome_review_drafts": {
            "pending_count": outcome_review_drafts.get("pending_count"),
            "due_or_reviewable_count": outcome_review_drafts.get("due_or_reviewable_count"),
            "draft_review_count": outcome_review_drafts.get("draft_review_count"),
            "upcoming_count": outcome_review_drafts.get("upcoming_count"),
            "fetch_market_data": outcome_review_drafts.get("fetch_market_data"),
            "status": outcome_review_drafts.get("status", "ok"),
        },
        "strategy_promotion_evidence": {
            "status": strategy_promotion_evidence.get("status"),
            "tactical_promotion_gate_passed": strategy_promotion_evidence.get("tactical_promotion_gate_passed"),
            "max_real_action_from_evidence": strategy_promotion_evidence.get("max_real_action_from_evidence"),
            "tactical_failed_gates": strategy_promotion_evidence.get("tactical_failed_gates"),
        },
        "strategy_iteration_backlog": {
            "status": strategy_iteration_backlog.get("status"),
            "candidate_strategy_count": (strategy_iteration_backlog.get("summary") or {}).get("candidate_strategy_count"),
            "max_allowed_action": (strategy_iteration_backlog.get("summary") or {}).get("max_allowed_action"),
            "execute_now_allowed": (strategy_iteration_backlog.get("summary") or {}).get("execute_now_allowed"),
        },
        "goal_execution_dashboard": {
            "status": goal_execution_dashboard.get("status"),
            "dashboard_json": goal_execution_dashboard.get("dashboard_json"),
            "report_max_allowed_action": ((goal_execution_dashboard.get("evidence_and_gate_state") or {}).get("report_max_allowed_action")),
            "short_term_tactical_max_allowed_action": ((goal_execution_dashboard.get("evidence_and_gate_state") or {}).get("short_term_tactical_max_allowed_action")),
            "backlog_count": len(goal_execution_dashboard.get("execution_backlog") or []),
        },
        "report_integrity_audit": {
            "status": audit_result.get("status"),
            "audit_path": audit_result.get("audit_path"),
            "passed": audit_result.get("passed"),
            "failed": audit_result.get("failed"),
            "warnings": audit_result.get("warnings"),
        },
        "objective_coverage_audit": {
            "status": objective_audit_result.get("status"),
            "audit_path": objective_audit_result.get("audit_path"),
            "passed": objective_audit_result.get("passed"),
            "failed": objective_audit_result.get("failed"),
            "warnings": objective_audit_result.get("warnings"),
        },
        "steps": steps,
    }
    if args.format == "markdown":
        print(report)
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
