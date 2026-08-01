#!/usr/bin/env python3
"""Track the tactical sleeve against the monthly 100% attack goal.

The tracker is intentionally narrow: it measures only the current deployable
US tactical sleeve, such as tactical shares plus broker cash waiting for the
next relay. Long-term protected holdings are excluded by policy.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MANUAL_ROOT = ROOT / "manual-investment-strategy-operator"
DEFAULT_LEDGER = MANUAL_ROOT / "performance" / "us_tactical_performance.json"

DEFAULT_TACTICAL_SYMBOLS = ["SOXL"]
DEFAULT_TACTICAL_CASH_SYMBOLS = ["USD_US_EQUITY"]
DEFAULT_PROTECTED_SYMBOLS = ["CRCL"]
DEFAULT_CONDITIONAL_SYMBOLS = ["COIN"]
DEFAULT_MONTHLY_TARGET_PCT = 100.0
DEFAULT_QUARTERLY_TARGET_PCT = 100.0


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def load_json(path: Path | str) -> Any:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path | str, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def empty_ledger() -> dict[str, Any]:
    return {
        "schema_version": "us-tactical-performance-v1",
        "updated_at": None,
        "default_policy": {
            "target_monthly_return_pct": DEFAULT_MONTHLY_TARGET_PCT,
            "target_quarterly_return_pct": DEFAULT_QUARTERLY_TARGET_PCT,
            "protected_holdings_excluded": DEFAULT_PROTECTED_SYMBOLS,
            "conditional_symbols_excluded_by_default": DEFAULT_CONDITIONAL_SYMBOLS,
            "tactical_cash_symbols": DEFAULT_TACTICAL_CASH_SYMBOLS,
            "notes": [
                "This ledger tracks only the dynamically deployable US tactical sleeve, not the whole US equity portfolio.",
                "CRCL is protected long-term by default and is excluded from the tactical monthly attack target.",
                "The quarterly field is a reporting checkpoint, not a lower alternative to the monthly 100% attack goal.",
                "COIN is conditional supplemental liquidity and is excluded unless a report explicitly designates it as deployable.",
            ],
        },
        "sleeves": [],
        "events": [],
    }


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_ledger()
    ledger = load_json(path)
    ledger.setdefault("schema_version", "us-tactical-performance-v1")
    ledger.setdefault("default_policy", empty_ledger()["default_policy"])
    ledger.setdefault("sleeves", [])
    ledger.setdefault("events", [])
    return ledger


def split_symbols(value: str | None, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    return symbols or list(default)


def current_value(holding: dict[str, Any]) -> float:
    values = holding.get("values") or {}
    value = values.get("current")
    return float(value or 0.0)


def current_price(holding: dict[str, Any]) -> float | None:
    prices = holding.get("prices") or {}
    value = prices.get("current")
    return float(value) if value is not None else None


def collect_components(
    snapshot: dict[str, Any],
    tactical_symbols: list[str],
    cash_symbols: list[str],
    protected_symbols: list[str],
    include_conditional_symbols: list[str],
) -> list[dict[str, Any]]:
    tactical_set = {item.upper() for item in tactical_symbols}
    cash_set = {item.upper() for item in cash_symbols}
    protected_set = {item.upper() for item in protected_symbols}
    conditional_set = {item.upper() for item in include_conditional_symbols}
    components: list[dict[str, Any]] = []

    for holding in snapshot.get("holdings", []):
        symbol = str(holding.get("symbol") or "").upper()
        if symbol in protected_set:
            continue
        reason = None
        if symbol in tactical_set:
            reason = "current_tactical_position"
        elif symbol in cash_set:
            reason = "tactical_cash_waiting_for_relay"
        elif symbol in conditional_set:
            reason = "conditional_supplemental_liquidity"
        if not reason:
            continue
        components.append({
            "symbol": symbol,
            "display": holding.get("display"),
            "rail": holding.get("rail"),
            "bucket": holding.get("bucket"),
            "quantity": holding.get("quantity"),
            "current_price": current_price(holding),
            "current_value_usd": current_value(holding),
            "current_weight_pct": (holding.get("weights") or {}).get("current"),
            "data_quality": holding.get("data_quality"),
            "inclusion_reason": reason,
        })
    return components


def total_value(components: list[dict[str, Any]]) -> float:
    return sum(float(item.get("current_value_usd") or 0.0) for item in components)


def active_sleeve(ledger: dict[str, Any]) -> dict[str, Any] | None:
    active = [item for item in ledger.get("sleeves", []) if item.get("status", "active") == "active"]
    if active:
        return active[-1]
    sleeves = ledger.get("sleeves", [])
    return sleeves[-1] if sleeves else None


def symbols_from_sleeve(sleeve: dict[str, Any] | None, inclusion_reason: str) -> list[str]:
    if not sleeve:
        return []
    explicit_key = {
        "current_tactical_position": "current_tactical_symbols",
        "tactical_cash_waiting_for_relay": "current_tactical_cash_symbols",
        "conditional_supplemental_liquidity": "current_conditional_symbols",
    }.get(inclusion_reason)
    if explicit_key and sleeve.get(explicit_key):
        return [str(item).upper() for item in sleeve.get(explicit_key, [])]
    symbols: list[str] = []
    for component in sleeve.get("baseline_components", []):
        if component.get("inclusion_reason") == inclusion_reason and component.get("symbol"):
            symbols.append(str(component["symbol"]).upper())
    return symbols


def pct_change(current: float, baseline: float) -> float | None:
    if baseline <= 0:
        return None
    return (current / baseline - 1.0) * 100.0


def safe_compound_required(target: float, current: float, days: float) -> float | None:
    if current <= 0 or target <= 0 or days <= 0:
        return None
    if current >= target:
        return 0.0
    return (math.pow(target / current, 1.0 / days) - 1.0) * 100.0


def build_summary(
    snapshot: dict[str, Any],
    ledger: dict[str, Any],
    tactical_symbols: list[str],
    cash_symbols: list[str],
    protected_symbols: list[str],
    include_conditional_symbols: list[str],
) -> dict[str, Any]:
    generated_at = snapshot.get("generated_at") or utc_now()
    as_of = parse_dt(generated_at) or dt.datetime.now(dt.timezone.utc)
    components = collect_components(
        snapshot,
        tactical_symbols=tactical_symbols,
        cash_symbols=cash_symbols,
        protected_symbols=protected_symbols,
        include_conditional_symbols=include_conditional_symbols,
    )
    current = total_value(components)
    sleeve = active_sleeve(ledger)

    if not sleeve:
        return {
            "generated_at": utc_now(),
            "as_of": generated_at,
            "status": "baseline_missing",
            "data_quality": "degraded",
            "max_allowed_action": "watch",
            "reason": "No active US tactical sleeve baseline exists. Run init after confirming the current tactical pool.",
            "current_components": components,
            "current_tactical_value_usd": round(current, 2),
            "protected_symbols_excluded": protected_symbols,
            "conditional_symbols_included": include_conditional_symbols,
            "recommended_next_step": "init_baseline_or_confirm_existing_baseline_before_using_target_gap",
        }

    baseline_value = float(sleeve.get("baseline_value_usd") or 0.0)
    monthly_target_pct = float(sleeve.get("target_monthly_return_pct") or DEFAULT_MONTHLY_TARGET_PCT)
    quarterly_target_pct = float(sleeve.get("target_quarterly_return_pct") or DEFAULT_QUARTERLY_TARGET_PCT)
    if current <= 0:
        return {
            "generated_at": utc_now(),
            "as_of": generated_at,
            "status": "baseline_rebuild_required",
            "data_quality": "stale_tactical_ledger",
            "max_allowed_action": "watch",
            "reason": "The historical tactical sleeve has no confirmed current components. Rebuild the baseline from user-confirmed settled cash and holdings before calculating ROI progress.",
            "sleeve_id": sleeve.get("sleeve_id"),
            "baseline_at": sleeve.get("baseline_at"),
            "baseline_value_usd": round(baseline_value, 2),
            "current_components": components,
            "current_tactical_value_usd": 0.0,
            "target_monthly_return_pct": monthly_target_pct,
            "target_quarterly_return_pct": quarterly_target_pct,
            "protected_symbols_excluded": protected_symbols,
            "conditional_symbols_included": include_conditional_symbols,
            "required_daily_return_to_monthly_target_pct": None,
            "required_daily_return_to_quarterly_target_pct": None,
            "recommended_next_step": "confirm_settled_cash_and_tactical_holdings_then_rebuild_baseline",
        }
    baseline_at = parse_dt(sleeve.get("baseline_at"))
    elapsed_days = (as_of - baseline_at).total_seconds() / 86400 if baseline_at else None
    elapsed_days_clamped_month = min(max(elapsed_days or 0.0, 0.0), 30.0)
    elapsed_days_clamped_quarter = min(max(elapsed_days or 0.0, 0.0), 90.0)
    monthly_target = baseline_value * (1.0 + monthly_target_pct / 100.0)
    quarterly_target = baseline_value * (1.0 + quarterly_target_pct / 100.0)
    monthly_required_line = baseline_value * (1.0 + monthly_target_pct / 100.0 * elapsed_days_clamped_month / 30.0)
    quarterly_required_line = baseline_value * (1.0 + quarterly_target_pct / 100.0 * elapsed_days_clamped_quarter / 90.0)
    monthly_gap = monthly_target - current
    quarterly_gap = quarterly_target - current
    days_left_month = max(30.0 - elapsed_days_clamped_month, 0.0)
    days_left_quarter = max(90.0 - elapsed_days_clamped_quarter, 0.0)
    current_return = pct_change(current, baseline_value)

    cash_value = sum(
        float(item.get("current_value_usd") or 0.0)
        for item in components
        if item.get("inclusion_reason") == "tactical_cash_waiting_for_relay"
    )
    deployed_value = current - cash_value
    cash_drag_pct = cash_value / current * 100.0 if current else None
    data_flags = [str(item.get("data_quality") or "") for item in components]
    has_user_stated_cash = any("user_stated" in item for item in data_flags)
    data_quality = "degraded_user_stated_cash" if has_user_stated_cash else "verified_or_public_proxy"

    return {
        "generated_at": utc_now(),
        "as_of": generated_at,
        "status": "ok",
        "data_quality": data_quality,
        "max_allowed_action": "conditional_action" if has_user_stated_cash else "conditional_action_or_execute_subject_to_other_gates",
        "sleeve_id": sleeve.get("sleeve_id"),
        "baseline_at": sleeve.get("baseline_at"),
        "baseline_value_usd": round(baseline_value, 2),
        "baseline_components": sleeve.get("baseline_components", []),
        "current_components": components,
        "current_tactical_value_usd": round(current, 2),
        "deployed_tactical_value_usd": round(deployed_value, 2),
        "tactical_cash_value_usd": round(cash_value, 2),
        "tactical_cash_drag_pct": round(cash_drag_pct, 2) if cash_drag_pct is not None else None,
        "current_return_pct": round(current_return, 2) if current_return is not None else None,
        "target_monthly_return_pct": monthly_target_pct,
        "target_quarterly_return_pct": quarterly_target_pct,
        "monthly_target_value_usd": round(monthly_target, 2),
        "quarterly_target_value_usd": round(quarterly_target, 2),
        "monthly_gap_usd": round(monthly_gap, 2),
        "quarterly_gap_usd": round(quarterly_gap, 2),
        "monthly_gap_pct_of_current": round(monthly_gap / current * 100.0, 2) if current else None,
        "quarterly_gap_pct_of_current": round(quarterly_gap / current * 100.0, 2) if current else None,
        "elapsed_days_since_baseline": round(elapsed_days, 2) if elapsed_days is not None else None,
        "monthly_required_line_value_usd": round(monthly_required_line, 2),
        "quarterly_required_line_value_usd": round(quarterly_required_line, 2),
        "monthly_progress_status": "achieved" if current >= monthly_target else ("on_track" if current >= monthly_required_line else "behind"),
        "quarterly_progress_status": "achieved" if current >= quarterly_target else ("on_track" if current >= quarterly_required_line else "behind"),
        "required_daily_return_to_monthly_target_pct": (
            round(safe_compound_required(monthly_target, current, days_left_month), 2)
            if days_left_month > 0
            else None
        ),
        "required_daily_return_to_quarterly_target_pct": (
            round(safe_compound_required(quarterly_target, current, days_left_quarter), 2)
            if days_left_quarter > 0
            else None
        ),
        "protected_symbols_excluded": protected_symbols,
        "conditional_symbols_included": include_conditional_symbols,
        "interpretation": build_interpretation(current, monthly_target, monthly_required_line, cash_value, has_user_stated_cash),
    }


def build_interpretation(
    current_value_usd: float,
    monthly_target_usd: float,
    monthly_required_line_usd: float,
    cash_value_usd: float,
    has_user_stated_cash: bool,
) -> list[str]:
    notes: list[str] = []
    if current_value_usd >= monthly_target_usd:
        notes.append("Monthly tactical target is already achieved; protect gains before forcing new risk.")
    elif current_value_usd < monthly_required_line_usd:
        notes.append("Tactical sleeve is behind the linear monthly target path; the report must prioritize high-quality relay setups or explicitly accept cash wait.")
    else:
        notes.append("Tactical sleeve is on or above the linear monthly target path; avoid low-quality churn.")
    if cash_value_usd > 0:
        notes.append("Cash inside the tactical sleeve is a deliberate relay state, but it creates goal drag if no high-quality setup appears within the wait window.")
    if has_user_stated_cash:
        notes.append("Broker cash is user-stated; confirm buying power before sizing any real order.")
    return notes


def init_baseline(
    snapshot: dict[str, Any],
    ledger: dict[str, Any],
    sleeve_id: str,
    tactical_symbols: list[str],
    cash_symbols: list[str],
    protected_symbols: list[str],
    include_conditional_symbols: list[str],
    force: bool,
    baseline_source: str | None,
) -> dict[str, Any]:
    if any(item.get("sleeve_id") == sleeve_id for item in ledger.get("sleeves", [])) and not force:
        raise SystemExit(f"Sleeve {sleeve_id} already exists; pass --force to replace it.")

    components = collect_components(
        snapshot,
        tactical_symbols=tactical_symbols,
        cash_symbols=cash_symbols,
        protected_symbols=protected_symbols,
        include_conditional_symbols=include_conditional_symbols,
    )
    value = total_value(components)
    generated_at = snapshot.get("generated_at") or utc_now()
    sleeve = {
        "sleeve_id": sleeve_id,
        "status": "active",
        "baseline_at": generated_at,
        "baseline_value_usd": round(value, 2),
        "baseline_components": components,
        "current_tactical_symbols": tactical_symbols,
        "current_tactical_cash_symbols": cash_symbols,
        "current_conditional_symbols": include_conditional_symbols,
        "target_monthly_return_pct": DEFAULT_MONTHLY_TARGET_PCT,
        "target_quarterly_return_pct": DEFAULT_QUARTERLY_TARGET_PCT,
        "protected_holdings_excluded": protected_symbols,
        "conditional_symbols_excluded_by_default": [
            item for item in DEFAULT_CONDITIONAL_SYMBOLS if item not in include_conditional_symbols
        ],
        "baseline_source": baseline_source or "snapshot current tactical position plus tactical cash",
        "notes": [
            "Baseline should be reset only after user confirms a new tactical sleeve cycle.",
            "Performance target applies to deployable tactical sleeve only; long-term protected holdings are excluded.",
        ],
    }
    ledger["sleeves"] = [item for item in ledger.get("sleeves", []) if item.get("sleeve_id") != sleeve_id]
    ledger["sleeves"].append(sleeve)
    ledger["updated_at"] = utc_now()
    return ledger


def render_markdown(summary: dict[str, Any]) -> str:
    if summary.get("status") != "ok":
        return (
            f"### US Tactical Performance Panel\n"
            f"- 状态: `{summary.get('status')}`\n"
            f"- 当前可识别战术资金: `${summary.get('current_tactical_value_usd')}`\n"
            f"- 处理: {summary.get('reason')}\n"
        )
    lines = [
        "### US Tactical Performance Panel",
        f"- Sleeve: `{summary.get('sleeve_id')}`",
        f"- 当前战术资金池: `${summary.get('current_tactical_value_usd')}`，基准 `${summary.get('baseline_value_usd')}`，当前收益 `{summary.get('current_return_pct')}%`",
        f"- 月度 ROI 100% 进攻目标值: `${summary.get('monthly_target_value_usd')}`，缺口 `${summary.get('monthly_gap_usd')}`，状态 `{summary.get('monthly_progress_status')}`",
        f"- 季度报告检查点: `${summary.get('quarterly_target_value_usd')}`，缺口 `${summary.get('quarterly_gap_usd')}`，状态 `{summary.get('quarterly_progress_status')}`",
        f"- 战术现金: `${summary.get('tactical_cash_value_usd')}`，现金拖累 `{summary.get('tactical_cash_drag_pct')}%`",
        f"- 数据质量: `{summary.get('data_quality')}`；最大动作: `{summary.get('max_allowed_action')}`",
    ]
    for note in summary.get("interpretation", []):
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Track US tactical sleeve performance")
    parser.add_argument("--path", default=str(DEFAULT_LEDGER), help="US tactical performance ledger path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize or replace a tactical sleeve baseline")
    init_parser.add_argument("--snapshot-json", required=True)
    init_parser.add_argument("--sleeve-id", required=True)
    init_parser.add_argument("--tactical-symbols", default=",".join(DEFAULT_TACTICAL_SYMBOLS))
    init_parser.add_argument("--cash-symbols", default=",".join(DEFAULT_TACTICAL_CASH_SYMBOLS))
    init_parser.add_argument("--protected-symbols", default=",".join(DEFAULT_PROTECTED_SYMBOLS))
    init_parser.add_argument("--include-conditional-symbols", default="")
    init_parser.add_argument("--baseline-source")
    init_parser.add_argument("--force", action="store_true")

    summary_parser = subparsers.add_parser("summary", help="Summarize current tactical sleeve performance")
    summary_parser.add_argument("--snapshot-json", required=True)
    summary_parser.add_argument("--tactical-symbols")
    summary_parser.add_argument("--cash-symbols")
    summary_parser.add_argument("--protected-symbols")
    summary_parser.add_argument("--include-conditional-symbols", default="")
    summary_parser.add_argument("--format", choices=["json", "markdown"], default="json")

    event_parser = subparsers.add_parser("record-event", help="Append a tactical performance event")
    event_parser.add_argument("--event-json", required=True)

    args = parser.parse_args()
    path = Path(args.path)
    ledger = load_ledger(path)

    if args.command == "init":
        snapshot = load_json(args.snapshot_json)
        updated = init_baseline(
            snapshot=snapshot,
            ledger=ledger,
            sleeve_id=args.sleeve_id,
            tactical_symbols=split_symbols(args.tactical_symbols, DEFAULT_TACTICAL_SYMBOLS),
            cash_symbols=split_symbols(args.cash_symbols, DEFAULT_TACTICAL_CASH_SYMBOLS),
            protected_symbols=split_symbols(args.protected_symbols, DEFAULT_PROTECTED_SYMBOLS),
            include_conditional_symbols=split_symbols(args.include_conditional_symbols, []),
            force=args.force,
            baseline_source=args.baseline_source,
        )
        write_json(path, updated)
        print(json.dumps({"status": "ok", "path": str(path), "sleeve_id": args.sleeve_id}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "summary":
        snapshot = load_json(args.snapshot_json)
        sleeve = active_sleeve(ledger)
        tactical_default = symbols_from_sleeve(sleeve, "current_tactical_position") or DEFAULT_TACTICAL_SYMBOLS
        cash_default = symbols_from_sleeve(sleeve, "tactical_cash_waiting_for_relay") or DEFAULT_TACTICAL_CASH_SYMBOLS
        summary = build_summary(
            snapshot=snapshot,
            ledger=ledger,
            tactical_symbols=split_symbols(args.tactical_symbols, tactical_default),
            cash_symbols=split_symbols(args.cash_symbols, cash_default),
            protected_symbols=split_symbols(args.protected_symbols, DEFAULT_PROTECTED_SYMBOLS),
            include_conditional_symbols=split_symbols(args.include_conditional_symbols, []),
        )
        if args.format == "markdown":
            sys.stdout.write(render_markdown(summary))
        else:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.command == "record-event":
        event = load_json(args.event_json)
        event.setdefault("recorded_at", utc_now())
        ledger.setdefault("events", []).append(event)
        sleeve = active_sleeve(ledger)
        if sleeve and event.get("update_active_sleeve"):
            for key in ("current_tactical_symbols", "current_tactical_cash_symbols", "current_conditional_symbols", "status"):
                if key in event:
                    sleeve[key] = event[key]
        ledger["updated_at"] = utc_now()
        write_json(path, ledger)
        print(json.dumps({"status": "ok", "path": str(path), "events": len(ledger.get("events", []))}, ensure_ascii=False, indent=2))
        return 0

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
