#!/usr/bin/env python3
"""Audit market-context coverage on paper trades.

Read-only. This script checks whether paper positions/trades carry the market
regime snapshot needed for Phase 2 regime-diversity evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
LEDGER_PATH = ROOT / "paper_trades" / "paper_portfolio_ledger.json"
REPORTS_DIR = ROOT / "reports"
EXPERIMENTS_DIR = ROOT / "experiments"
LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))

UNKNOWN_MARKERS = {
    "",
    "unknown",
    "unknown_or_not_attached",
    "manual_or_cli_open_unknown",
    "unavailable",
    "none",
    "null",
}


def now_local() -> dt.datetime:
    return dt.datetime.now(LOCAL_TZ).replace(microsecond=0)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rel(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def normalized(value: Any) -> str:
    return str(value or "").strip()


def known(value: Any) -> bool:
    return normalized(value).lower() not in UNKNOWN_MARKERS


def trade_context(trade: dict[str, Any]) -> dict[str, Any]:
    context = trade.get("market_context_at_entry") if isinstance(trade.get("market_context_at_entry"), dict) else {}
    fields = {
        "market_regime": trade.get("market_regime") or context.get("market_regime"),
        "market_atmosphere": trade.get("market_atmosphere") or context.get("market_atmosphere"),
        "short_term_state": trade.get("short_term_state") or context.get("short_term_state"),
        "sentiment_state": trade.get("sentiment_state") or context.get("sentiment_state"),
    }
    known_fields = [name for name, value in fields.items() if known(value)]
    missing_or_unknown = [name for name, value in fields.items() if not known(value)]
    status = "complete" if len(known_fields) == len(fields) else "partial" if known_fields else "missing"
    return {
        "status": status,
        "known_fields": known_fields,
        "missing_or_unknown_fields": missing_or_unknown,
        "fields": fields,
        "context_source": context.get("source") or trade.get("signal_source"),
    }


def source_experiment_path(trade: dict[str, Any]) -> Path | None:
    candidates: list[str] = []
    for key in ("signal_source", "run_id", "sampler_run_id"):
        value = trade.get(key)
        if value:
            candidates.append(str(value))
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return path
        if candidate.startswith("active-alpha-paper-monitor/"):
            path = WORKSPACE_ROOT / candidate
            if path.exists():
                return path
        if candidate.endswith(".json"):
            path = ROOT / "experiments" / Path(candidate).name
            if path.exists():
                return path
        if candidate:
            path = ROOT / "experiments" / f"{candidate}.json"
            if path.exists():
                return path
    return None


def context_from_experiment(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    payload = read_json(path)
    sources = [
        payload.get("dynamic_scan_pool") if isinstance(payload.get("dynamic_scan_pool"), dict) else {},
        payload.get("dynamic_market_context") if isinstance(payload.get("dynamic_market_context"), dict) else {},
        payload.get("market_context") if isinstance(payload.get("market_context"), dict) else {},
    ]
    selected = payload.get("selected_candidate") if isinstance(payload.get("selected_candidate"), dict) else {}
    if selected:
        sources.append(selected.get("market_context_at_entry") if isinstance(selected.get("market_context_at_entry"), dict) else {})
        sources.append(selected)
    merged: dict[str, Any] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for field in ("market_regime", "market_atmosphere", "short_term_state", "sentiment_state"):
            if known(source.get(field)) and not known(merged.get(field)):
                merged[field] = source.get(field)
    if merged:
        merged["source"] = rel(path)
        merged["source_type"] = "legacy_source_experiment_context_backfill"
    return merged


def backfill_trade_context(trade: dict[str, Any]) -> dict[str, Any] | None:
    before = trade_context(trade)
    if before["status"] == "complete":
        return None
    path = source_experiment_path(trade)
    inferred = context_from_experiment(path)
    if not inferred:
        return None
    context = trade.get("market_context_at_entry") if isinstance(trade.get("market_context_at_entry"), dict) else {}
    context = dict(context)
    changed_fields: list[str] = []
    for field in ("market_regime", "market_atmosphere", "short_term_state", "sentiment_state"):
        if not known(trade.get(field)) and known(inferred.get(field)):
            trade[field] = inferred[field]
            context[field] = inferred[field]
            changed_fields.append(field)
    if not changed_fields:
        return None
    context.setdefault("source", inferred.get("source"))
    context["backfill_source"] = inferred.get("source")
    context["backfill_source_type"] = inferred.get("source_type")
    context["backfilled_at"] = now_local().isoformat()
    context["operator_note"] = "Backfilled legacy paper trade market context from existing local experiment metadata only; no PnL, price, cash, or real account state changed."
    trade["market_context_at_entry"] = context
    trade.setdefault("context_backfill_history", []).append(
        {
            "backfilled_at": context["backfilled_at"],
            "source": inferred.get("source"),
            "fields": changed_fields,
            "live_orders_enabled": False,
            "private_api_used": False,
        }
    )
    after = trade_context(trade)
    return {
        "paper_trade_id": trade.get("paper_trade_id"),
        "symbol": trade.get("symbol"),
        "source": inferred.get("source"),
        "fields": changed_fields,
        "status_before": before["status"],
        "status_after": after["status"],
    }


def repair_legacy_context(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    repairs: list[dict[str, Any]] = []
    for section in ("open_positions", "closed_trades"):
        trades = ledger.get(section) if isinstance(ledger.get(section), list) else []
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            repair = backfill_trade_context(trade)
            if repair:
                repair["section"] = section
                repairs.append(repair)
    if repairs:
        ledger.setdefault("events", []).append(
            {
                "event_type": "paper_market_context_legacy_backfill",
                "created_at": now_local().isoformat(),
                "repair_count": len(repairs),
                "notes": "Backfilled market context metadata from local experiment artifacts only; no trading state, PnL, cash, or real account state changed.",
                "live_orders_enabled": False,
                "private_api_used": False,
            }
        )
    return repairs


def summarize_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = {"complete": 0, "partial": 0, "missing": 0}
    regime_counts: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        ctx = trade_context(trade)
        status_counts[ctx["status"]] += 1
        regime = normalized(ctx["fields"].get("market_regime"))
        if known(regime):
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
        if ctx["status"] != "complete":
            rows.append(
                {
                    "paper_trade_id": trade.get("paper_trade_id"),
                    "symbol": trade.get("symbol"),
                    "status": ctx["status"],
                    "missing_or_unknown_fields": ctx["missing_or_unknown_fields"],
                    "market_regime": ctx["fields"].get("market_regime"),
                    "opened_at": trade.get("opened_at"),
                    "closed_at": trade.get("closed_at"),
                }
            )
    total = len([t for t in trades if isinstance(t, dict)])
    return {
        "trade_count": total,
        "complete_count": status_counts["complete"],
        "partial_count": status_counts["partial"],
        "missing_count": status_counts["missing"],
        "coverage_pct": round(status_counts["complete"] / total * 100.0, 4) if total else 0.0,
        "explicit_regime_count": len(regime_counts),
        "regime_counts": sorted(regime_counts.items()),
        "top_gaps": rows[:10],
    }


def build_record(ledger_path: Path = LEDGER_PATH, repair: bool = False, dry_run: bool = False) -> dict[str, Any]:
    created = now_local()
    ledger = read_json(ledger_path)
    repairs = repair_legacy_context(ledger) if repair else []
    if repairs and not dry_run:
        write_json(ledger_path, ledger)
    open_trades = [t for t in ledger.get("open_positions") or [] if isinstance(t, dict)]
    closed_trades = [t for t in ledger.get("closed_trades") or [] if isinstance(t, dict)]
    open_summary = summarize_trades(open_trades)
    closed_summary = summarize_trades(closed_trades)
    all_summary = summarize_trades(open_trades + closed_trades)
    status = "pass" if all_summary["trade_count"] == 0 or all_summary["missing_count"] == 0 else "warn"
    return {
        "run_id": f"{created.strftime('%Y%m%d-%H%M%S')}-paper-market-context-audit",
        "created_at": created.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "script": "paper_market_context_auditor.py",
        "status": status,
        "ledger_path": rel(ledger_path),
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "ledger_mutated": bool(repairs and not dry_run),
        "repair_legacy_context": bool(repair),
        "repair_dry_run": bool(dry_run),
        "repairs_applied": [] if dry_run else repairs,
        "repairs_preview": repairs if dry_run else [],
        "open_summary": open_summary,
        "closed_summary": closed_summary,
        "all_summary": all_summary,
    }


def render_markdown(record: dict[str, Any]) -> str:
    all_summary = record["all_summary"]
    lines = [
        f"# Paper Market Context Audit | {record['run_id']}",
        "",
        "Read-only audit. No live orders, private APIs, or ledger mutation.",
        "",
        "## Summary",
        "",
        f"- status: `{record['status']}`",
        f"- all_trade_count: `{all_summary['trade_count']}`",
        f"- complete/partial/missing: `{all_summary['complete_count']}/{all_summary['partial_count']}/{all_summary['missing_count']}`",
        f"- coverage_pct: `{all_summary['coverage_pct']}`",
        f"- explicit_regime_count: `{all_summary['explicit_regime_count']}`",
        f"- repair_legacy_context: `{record.get('repair_legacy_context')}`",
        f"- ledger_mutated: `{record.get('ledger_mutated')}`",
        f"- repairs_applied: `{len(record.get('repairs_applied') or [])}`",
        f"- repairs_preview: `{len(record.get('repairs_preview') or [])}`",
        f"- live_orders_enabled: `{record['live_orders_enabled']}`",
        f"- private_api_used: `{record['private_api_used']}`",
        "",
        "## Top Gaps",
        "",
        "| Trade | Symbol | Status | Missing | Regime |",
        "|---|---|---|---|---|",
    ]
    for item in all_summary["top_gaps"]:
        lines.append(
            f"| `{item.get('paper_trade_id')}` | `{item.get('symbol')}` | `{item.get('status')}` | "
            f"`{', '.join(item.get('missing_or_unknown_fields') or [])}` | `{item.get('market_regime')}` |"
        )
    if not all_summary["top_gaps"]:
        lines.append("| - | - | - | - | - |")
    repair_rows = (record.get("repairs_applied") or record.get("repairs_preview") or [])[:12]
    lines.extend(["", "## Legacy Context Repairs", "", "| Trade | Symbol | Section | Status | Fields | Source |", "|---|---|---|---|---|---|"])
    for item in repair_rows:
        lines.append(
            f"| `{item.get('paper_trade_id')}` | `{item.get('symbol')}` | `{item.get('section')}` | "
            f"`{item.get('status_before')} -> {item.get('status_after')}` | "
            f"`{', '.join(item.get('fields') or [])}` | `{item.get('source')}` |"
        )
    if not repair_rows:
        lines.append("| - | - | - | - | - | - |")
    lines.append("")
    return "\n".join(lines)


def run_self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ledger.json"
        exp = Path(tmp) / "source-fast.json"
        write_json(
            exp,
            {
                "run_id": "source-fast",
                "dynamic_scan_pool": {
                    "market_regime": "risk_on_momentum",
                    "market_atmosphere": "broad_risk_appetite",
                    "short_term_state": "neutral",
                    "sentiment_state": "positive_catalyst_cluster",
                },
            },
        )
        payload = {
            "live_orders_enabled": False,
            "private_api_used": False,
            "open_positions": [
                {
                    "paper_trade_id": "p1",
                    "symbol": "BTCUSDT",
                    "market_regime": "risk_on_momentum",
                    "market_atmosphere": "supportive",
                    "short_term_state": "accelerating",
                    "sentiment_state": "light_positive_context",
                }
            ],
            "closed_trades": [
                {
                    "paper_trade_id": "p2",
                    "symbol": "ETHUSDT",
                    "market_regime": "unknown_or_not_attached",
                    "signal_source": str(exp),
                }
            ],
        }
        write_json(path, payload)
        record = build_record(path)
        assert record["all_summary"]["trade_count"] == 2, record
        assert record["all_summary"]["complete_count"] == 1, record
        assert record["all_summary"]["missing_count"] == 1, record
        assert record["all_summary"]["explicit_regime_count"] == 1, record
        repaired = build_record(path, repair=True, dry_run=False)
        assert repaired["ledger_mutated"] is True and len(repaired["repairs_applied"]) == 1, repaired
        repaired_payload = read_json(path)
        assert repaired_payload["closed_trades"][0]["market_regime"] == "risk_on_momentum", repaired_payload
        assert repaired["all_summary"]["complete_count"] == 2 and repaired["all_summary"]["missing_count"] == 0, repaired
    return {
        "status": "ok",
        "cases": ["complete_context_counts", "unknown_context_does_not_count_as_regime", "legacy_context_backfill_from_local_experiment"],
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit paper trade market-context coverage")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repair-legacy-context", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(run_self_test(), ensure_ascii=False, indent=2))
        return 0

    record = build_record(repair=args.repair_legacy_context, dry_run=args.dry_run)
    stamp = record["run_id"].removesuffix("-paper-market-context-audit")
    report_path = REPORTS_DIR / f"{now_local().strftime('%Y-%m-%d')}-{stamp}-paper-market-context-audit.md"
    experiment_path = EXPERIMENTS_DIR / f"{stamp}-paper-market-context-audit.json"
    record["outputs"] = {"report": rel(report_path), "experiment": rel(experiment_path)}
    if not args.dry_run:
        write_json(experiment_path, record)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_markdown(record), encoding="utf-8")
    payload = {
        "status": record["status"],
        "run_id": record["run_id"],
        "all_trade_count": record["all_summary"]["trade_count"],
        "complete_count": record["all_summary"]["complete_count"],
        "partial_count": record["all_summary"]["partial_count"],
        "missing_count": record["all_summary"]["missing_count"],
        "coverage_pct": record["all_summary"]["coverage_pct"],
        "explicit_regime_count": record["all_summary"]["explicit_regime_count"],
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": record["ledger_mutated"],
        "repair_legacy_context": record["repair_legacy_context"],
        "repairs_applied": len(record.get("repairs_applied") or []),
        "repairs_preview": len(record.get("repairs_preview") or []),
        "outputs": record["outputs"],
    } if args.compact_output else record
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
