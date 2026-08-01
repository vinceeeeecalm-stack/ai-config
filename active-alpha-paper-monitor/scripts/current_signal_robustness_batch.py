#!/usr/bin/env python3
"""Batch current-signal robustness audits for the sampler candidate queue."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import current_signal_robustness_auditor as robustness


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "experiments" / "current-signal-robustness-audit.json"
REPORT = ROOT / "reports" / "CURRENT_SIGNAL_ROBUSTNESS_AUDIT.md"


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def ordered_probe_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("top_candidates") if isinstance(payload.get("top_candidates"), list) else []
    return [
        row for row in rows
        if isinstance(row, dict)
        and row.get("current_signal") is True
        and not row.get("promotion_blockers")
    ]


def local_candidate_rank(rows: list[dict[str, Any]], target: dict[str, Any]) -> int:
    peers = [
        row for row in rows
        if row.get("symbol") == target.get("symbol") and row.get("interval") == target.get("interval")
    ]
    for index, row in enumerate(peers):
        if row.get("strategy") == target.get("strategy"):
            return index
    return -1


def render(payload: dict[str, Any]) -> str:
    lines = [
        "# Current Signal Robustness Batch",
        "",
        f"- run_id: `{payload.get('run_id')}`",
        f"- status: `{payload.get('status')}`",
        f"- candidate_count: `{payload.get('candidate_count')}`",
        f"- allowed_count: `{payload.get('allowed_count')}`",
        f"- dry_run_only_count: `{payload.get('dry_run_only_count')}`",
        "- scope: paper-only; no ledger mutation and no live order permission.",
        "",
        "| Candidate | Family | Signal active | Neighbor support | Holdout trades | Wilson 95% | Gate | Failed gates |",
        "|---|---|---:|---:|---:|---|---|---|",
    ]
    for item in payload.get("candidate_audits") or []:
        ref = item.get("reference_candidate") or {}
        neighborhood = item.get("parameter_neighborhood") or {}
        uncertainty = item.get("holdout_uncertainty") or {}
        lines.append(
            f"| `{ref.get('symbol')} {ref.get('interval')}` | `{ref.get('family')}` | "
            f"`{item.get('reference_current_signal_active')}` | `{neighborhood.get('no_blocker_share_pct')}%` | "
            f"`{uncertainty.get('trade_count')}` | `{uncertainty.get('wilson_95pct_lower_pct')}–{uncertainty.get('wilson_95pct_upper_pct')}%` | "
            f"`{item.get('paper_retest_gate')}` | `{', '.join(item.get('failed_gates') or []) or '-'}` |"
        )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    probe_path = Path(args.probe) if args.probe else robustness.latest_probe()
    probe = read_json(probe_path) if probe_path else {}
    rows = ordered_probe_candidates(probe)[: max(0, args.max_candidates)]
    audits = []
    for row in rows:
        rank = local_candidate_rank(ordered_probe_candidates(probe), row)
        if rank < 0:
            continue
        child_args = SimpleNamespace(
            probe=str(probe_path),
            cache_dirs=args.cache_dirs,
            symbol=str(row.get("symbol") or ""),
            interval=str(row.get("interval") or ""),
            candidate_rank=rank,
            initial=args.initial,
            max_distance=args.max_distance,
            min_current_neighbors=args.min_current_neighbors,
            min_no_blocker_neighbors=args.min_no_blocker_neighbors,
            min_no_blocker_share_pct=args.min_no_blocker_share_pct,
            min_holdout_trades=args.min_holdout_trades,
            absolute_min_holdout_trades=args.absolute_min_holdout_trades,
            opportunity_coverage_ratio=args.opportunity_coverage_ratio,
            min_wilson_lower_pct=args.min_wilson_lower_pct,
        )
        result = robustness.audit(child_args)
        result["strategy_match_key"] = {
            "symbol": row.get("symbol"),
            "interval": row.get("interval"),
            "strategy": row.get("strategy"),
        }
        audits.append(result)
    allowed = [item for item in audits if item.get("paper_retest_gate") == "allow_minimum_paper_retest"]
    return {
        "run_id": f"{now.strftime('%Y%m%d-%H%M%S')}-current-signal-robustness-batch",
        "created_at": now.isoformat(),
        "status": "candidate_available_for_minimum_paper_retest" if allowed else "no_candidate_passed_robustness",
        "source_probe": str(probe_path) if probe_path else None,
        "candidate_count": len(audits),
        "allowed_count": len(allowed),
        "dry_run_only_count": len(audits) - len(allowed),
        "paper_retest_gate": "allow_matching_candidates_only" if allowed else "dry_run_only",
        "candidate_audits": audits,
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
    }


def self_test() -> dict[str, Any]:
    rows = [
        {"symbol": "AAAUSDT", "interval": "4h", "strategy": {"family": "x", "lookback": 1}, "current_signal": True, "promotion_blockers": []},
        {"symbol": "AAAUSDT", "interval": "4h", "strategy": {"family": "x", "lookback": 2}, "current_signal": True, "promotion_blockers": []},
        {"symbol": "BBBUSDT", "interval": "1h", "strategy": {"family": "y"}, "current_signal": True, "promotion_blockers": ["blocked"]},
    ]
    payload = {"top_candidates": rows}
    selected = ordered_probe_candidates(payload)
    assert len(selected) == 2
    assert local_candidate_rank(selected, selected[1]) == 1
    return {"status": "ok", "candidate_filter_verified": True, "exact_strategy_rank_verified": True, "live_orders_enabled": False, "ledger_mutated": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch audit sampler current-signal candidates.")
    parser.add_argument("--probe", default="")
    parser.add_argument("--cache-dirs", default=str(ROOT / "cache" / "binance_klines" / "dynamic_20260711"))
    parser.add_argument("--max-candidates", type=int, default=12)
    parser.add_argument("--initial", type=float, default=500.0)
    parser.add_argument("--max-distance", type=int, default=2)
    parser.add_argument("--min-current-neighbors", type=int, default=5)
    parser.add_argument("--min-no-blocker-neighbors", type=int, default=8)
    parser.add_argument("--min-no-blocker-share-pct", type=float, default=15.0)
    parser.add_argument("--min-holdout-trades", type=int, default=20)
    parser.add_argument("--absolute-min-holdout-trades", type=int, default=6)
    parser.add_argument("--opportunity-coverage-ratio", type=float, default=0.4)
    parser.add_argument("--min-wilson-lower-pct", type=float, default=45.0)
    parser.add_argument("--output", default=str(OUTPUT))
    parser.add_argument("--report", default=str(REPORT))
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    payload = run(args)
    if not args.dry_run:
        output = Path(args.output)
        report = Path(args.report)
        output.parent.mkdir(parents=True, exist_ok=True)
        report.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report.write_text(render(payload), encoding="utf-8")
    summary = {
        "status": payload.get("status"),
        "candidate_count": payload.get("candidate_count"),
        "allowed_count": payload.get("allowed_count"),
        "dry_run_only_count": payload.get("dry_run_only_count"),
        "paper_retest_gate": payload.get("paper_retest_gate"),
        "outputs": {"experiment": args.output, "report": args.report},
        "live_orders_enabled": False,
        "ledger_mutated": False,
    }
    print(json.dumps(summary if args.compact_output else payload, ensure_ascii=False, indent=None if args.compact_output else 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
