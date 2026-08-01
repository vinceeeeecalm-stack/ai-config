#!/usr/bin/env python3
"""Audit whether cached walk-forward research can still be reproduced."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from kline_cache_storage import MANIFEST_NAME, inspect_kline_cache_manifest, kline_data_paths


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_REPORT = ACTIVE_ROOT / "reports" / "KLINE_RESEARCH_REPRODUCIBILITY_AUDIT.md"
DEFAULT_EXPERIMENT = ACTIVE_ROOT / "experiments" / "kline-research-reproducibility-audit.json"


def now_local() -> dt.datetime:
    return dt.datetime.now(tz=LOCAL_TZ).replace(microsecond=0)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def inspect_cache_tree(path_text: str) -> dict[str, Any]:
    path = Path(path_text).expanduser()
    if not path.is_dir():
        return {
            "path": path_text,
            "exists": False,
            "data_file_count": 0,
            "verified_file_count": 0,
            "manifest_count": 0,
            "valid_manifest_count": 0,
            "manifest_integrity_ok": False,
            "manifest_rows": [],
        }
    manifest_paths = sorted(path.rglob(MANIFEST_NAME))
    if (path / MANIFEST_NAME).is_file():
        manifest_paths = [path / MANIFEST_NAME]
    manifest_rows = [inspect_kline_cache_manifest(item.parent) for item in manifest_paths]
    data_file_count = len(kline_data_paths(path, recursive=True))
    verified_file_count = sum(int(item.get("verified_file_count") or 0) for item in manifest_rows)
    valid_manifest_count = sum(item.get("manifest_valid") is True for item in manifest_rows)
    integrity_ok = (
        bool(manifest_rows)
        and valid_manifest_count == len(manifest_rows)
        and verified_file_count == data_file_count
        and data_file_count > 0
    )
    return {
        "path": path_text,
        "exists": True,
        "data_file_count": data_file_count,
        "verified_file_count": verified_file_count,
        "manifest_count": len(manifest_rows),
        "valid_manifest_count": valid_manifest_count,
        "manifest_integrity_ok": integrity_ok,
        "manifest_rows": manifest_rows,
    }


def top_candidate_summary(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("top_ranked") if isinstance(payload.get("top_ranked"), list) else []
    out = []
    for item in rows[:5]:
        if not isinstance(item, dict):
            continue
        best = item.get("best_oos") if isinstance(item.get("best_oos"), dict) else {}
        out.append(
            {
                "symbol": item.get("symbol"),
                "interval": item.get("interval"),
                "stage": best.get("stage"),
                "trade_count": best.get("trade_count"),
                "win_rate_pct": best.get("win_rate_pct"),
                "net_return_pct": best.get("net_return_pct"),
                "max_drawdown_pct": best.get("max_drawdown_pct"),
                "current_signal": best.get("current_signal"),
            }
        )
    return out


def inspect_research_artifact(path: Path) -> dict[str, Any]:
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        payload = {}
    cache_dirs = [str(item) for item in (payload.get("cache_dirs") or []) if str(item).strip()]
    cache_rows = [inspect_cache_tree(item) for item in cache_dirs]
    actual_file_count = sum(int(item["data_file_count"]) for item in cache_rows)
    verified_file_count = sum(int(item["verified_file_count"]) for item in cache_rows)
    cache_manifest_integrity_ok = bool(cache_rows) and all(item["manifest_integrity_ok"] for item in cache_rows)
    declared_frames = int(payload.get("frames_loaded") or 0)
    raw_cache_available = actual_file_count > 0 and cache_manifest_integrity_ok
    enough_files_for_declared_frames = verified_file_count >= declared_frames if declared_frames > 0 else False
    validation_protocol = payload.get("validation_protocol")
    holdout_not_used_for_selection = payload.get("holdout_used_for_selection") is False
    protocol_ok = (
        validation_protocol == "grid_on_first_50pct_rank_on_next_25pct_single_final_25pct_holdout"
        and holdout_not_used_for_selection
    )
    assumptions = payload.get("execution_assumptions") if isinstance(payload.get("execution_assumptions"), dict) else {}
    execution_model_ok = (
        0.0 < float(assumptions.get("capital_fraction_per_trade") or 0.0) <= 0.5
        and float(assumptions.get("commission_bps_per_side") or 0.0) > 0.0
        and float(assumptions.get("slippage_bps_per_side") or 0.0) > 0.0
        and float(assumptions.get("round_trip_spread_bps") or 0.0) > 0.0
        and assumptions.get("same_bar_stop_take_policy") == "conservative_stop_first"
        and assumptions.get("entry_timing") == "next_bar_open_after_signal_close"
        and assumptions.get("overlapping_positions_per_strategy") is False
    )
    reproducible = raw_cache_available and enough_files_for_declared_frames and protocol_ok and execution_model_ok
    return {
        "artifact": rel(path),
        "generated_at": payload.get("generated_at"),
        "declared_frames_loaded": declared_frames,
        "actual_cache_json_file_count": actual_file_count,
        "verified_cache_file_count": verified_file_count,
        "cache_dirs": cache_rows,
        "raw_cache_available": raw_cache_available,
        "cache_manifest_integrity_ok": cache_manifest_integrity_ok,
        "enough_files_for_declared_frames": enough_files_for_declared_frames,
        "validation_protocol": validation_protocol,
        "holdout_not_used_for_selection": holdout_not_used_for_selection,
        "protocol_ok": protocol_ok,
        "execution_model_ok": execution_model_ok,
        "execution_assumptions": assumptions,
        "reproducibility_status": (
            "reproducible"
            if reproducible
            else "raw_cache_or_three_segment_execution_contract_missing"
        ),
        "promotion_allowed": reproducible,
        "stage_counts": payload.get("stage_counts") or {},
        "top_candidates": top_candidate_summary(payload),
    }


def build_record(experiment_dir: Path, max_artifacts: int) -> dict[str, Any]:
    paths = sorted(
        experiment_dir.glob("*weekly-goal*.json"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )[:max_artifacts]
    rows = [inspect_research_artifact(path) for path in paths]
    reproducible_count = sum(item.get("promotion_allowed") is True for item in rows)
    missing_count = len(rows) - reproducible_count
    current_artifact = rows[0] if rows else {}
    current_reproducible = current_artifact.get("promotion_allowed") is True
    return {
        "run_id": f"{now_local().strftime('%Y%m%d-%H%M%S')}-kline-research-reproducibility-audit",
        "created_at": now_local().isoformat(),
        "status": (
            "pass_current_reproducible"
            if current_reproducible and missing_count == 0
            else "pass_current_reproducible_with_historical_gaps"
            if current_reproducible
            else "blocked_nonreproducible_research"
        ),
        "scope": "paper_only_research_reproducibility",
        "live_orders_enabled": False,
        "private_api_used": False,
        "ledger_mutated": False,
        "summary": {
            "artifact_count": len(rows),
            "reproducible_count": reproducible_count,
            "nonreproducible_count": missing_count,
            "promotion_allowed_count": reproducible_count,
            "current_artifact": current_artifact.get("artifact"),
            "current_artifact_reproducible": current_reproducible,
            "historical_nonreproducible_count": max(0, missing_count - (0 if current_reproducible else 1)),
            "max_allowed_action": "paper_research_candidate" if current_reproducible else "historical_summary_only",
            "fresh_cache_rebuild_required": not current_reproducible,
        },
        "artifacts": rows,
        "next_actions": [
            "Rebuild Binance public Kline data into active-alpha-paper-monitor/cache/binance_klines/.",
            "Rerun weekly_goal_strategy_lab with train/test split, friction, and durable cache references.",
            "Do not promote historical high-return candidates until raw Klines and exact parameters are reproducible.",
        ],
    }


def render_report(record: dict[str, Any]) -> str:
    summary = record.get("summary") or {}
    lines = [
        "# Kline Research Reproducibility Audit",
        "",
        "Paper-only evidence audit. No market fetch, ledger mutation, or live order.",
        "",
        f"- status: `{record.get('status')}`",
        f"- artifacts: `{summary.get('artifact_count')}`",
        f"- reproducible/nonreproducible: `{summary.get('reproducible_count')}/{summary.get('nonreproducible_count')}`",
        f"- current_artifact: `{summary.get('current_artifact')}`",
        f"- current_artifact_reproducible: `{summary.get('current_artifact_reproducible')}`",
        f"- historical_nonreproducible_count: `{summary.get('historical_nonreproducible_count')}`",
        f"- max_allowed_action: `{summary.get('max_allowed_action')}`",
        f"- fresh_cache_rebuild_required: `{summary.get('fresh_cache_rebuild_required')}`",
        "",
        "| Artifact | Declared Frames | Actual/Verified Files | Manifest | Protocol | Execution | Reproducibility | Promotion |",
        "|---|---:|---:|---|---|---|---|---|",
    ]
    for item in record.get("artifacts") or []:
        lines.append(
            f"| `{item.get('artifact')}` | `{item.get('declared_frames_loaded')}` | "
            f"`{item.get('actual_cache_json_file_count')}/{item.get('verified_cache_file_count')}` | "
            f"`{item.get('cache_manifest_integrity_ok')}` | `{item.get('protocol_ok')}` | "
            f"`{item.get('execution_model_ok')}` | `{item.get('reproducibility_status')}` | "
            f"`{item.get('promotion_allowed')}` |"
        )
    if not record.get("artifacts"):
        lines.append("| - | - | - | `False` | `False` | `False` | `missing` | `False` |")
    lines.extend(["", "## Historical Candidates", ""])
    for artifact in record.get("artifacts") or []:
        for item in artifact.get("top_candidates") or []:
            lines.append(
                f"- `{item.get('symbol')}` `{item.get('interval')}` stage `{item.get('stage')}`: "
                f"trades `{item.get('trade_count')}`, win `{item.get('win_rate_pct')}`, net "
                f"`{item.get('net_return_pct')}`, DD `{item.get('max_drawdown_pct')}`. "
                "Historical summary only; raw Kline replay is required."
            )
    lines.extend(["", "## Next Actions", ""])
    lines.extend(f"- {item}" for item in record.get("next_actions") or [])
    lines.extend(
        [
            "",
            "## Safety",
            "",
            f"- live_orders_enabled: `{record.get('live_orders_enabled')}`",
            f"- private_api_used: `{record.get('private_api_used')}`",
            f"- ledger_mutated: `{record.get('ledger_mutated')}`",
            "",
        ]
    )
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="kline_repro_audit_") as tmp_text:
        root = Path(tmp_text)
        experiments = root / "experiments"
        cache = root / "cache"
        experiments.mkdir()
        cache.mkdir()
        from kline_cache_storage import build_kline_cache_manifest

        rows = [
            {"t": 1_700_000_000_000 + i * 3_600_000, "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 2, "ct": 1_700_000_000_000 + i * 3_600_000 + 3_599_999, "qv": 20, "n": 3}
            for i in range(300)
        ]
        for index in range(2):
            shifted = [{**row, "t": row["t"] + index * 300 * 3_600_000, "ct": row["ct"] + index * 300 * 3_600_000} for row in rows]
            target = cache / f"TEST{index}USDT_1h_{shifted[0]['t']}_{shifted[-1]['t']}.json"
            target.write_text(json.dumps(shifted), encoding="utf-8")
        build_kline_cache_manifest(cache, required_intervals=["1h"], selected_symbols=["TEST0USDT", "TEST1USDT"])
        artifact = experiments / "20260101-weekly-goal.json"
        artifact.write_text(
            json.dumps(
                {
                    "cache_dirs": [str(cache)],
                    "frames_loaded": 2,
                    "validation_protocol": "grid_on_first_50pct_rank_on_next_25pct_single_final_25pct_holdout",
                    "holdout_used_for_selection": False,
                    "execution_assumptions": {
                        "capital_fraction_per_trade": 0.25,
                        "commission_bps_per_side": 10,
                        "slippage_bps_per_side": 8,
                        "round_trip_spread_bps": 10,
                        "same_bar_stop_take_policy": "conservative_stop_first",
                        "entry_timing": "next_bar_open_after_signal_close",
                        "overlapping_positions_per_strategy": False,
                    },
                    "top_ranked": [],
                }
            ),
            encoding="utf-8",
        )
        available = build_record(experiments, 5)
        assert available["status"] == "pass_current_reproducible", available
        stale_protocol_payload = read_json(artifact, {})
        stale_protocol_payload.pop("validation_protocol")
        artifact.write_text(json.dumps(stale_protocol_payload), encoding="utf-8")
        stale_protocol = build_record(experiments, 5)
        assert stale_protocol["status"] == "blocked_nonreproducible_research", stale_protocol
        artifact.write_text(json.dumps({**stale_protocol_payload, "validation_protocol": "grid_on_first_50pct_rank_on_next_25pct_single_final_25pct_holdout"}), encoding="utf-8")
        for path in cache.glob("*.json"):
            if path.name != MANIFEST_NAME:
                path.unlink()
        missing = build_record(experiments, 5)
        assert missing["status"] == "blocked_nonreproducible_research", missing
        assert missing["summary"]["max_allowed_action"] == "historical_summary_only", missing
    return {
        "status": "ok",
        "reproducible_path_verified": True,
        "missing_raw_cache_block_verified": True,
        "legacy_validation_protocol_block_verified": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Kline research reproducibility.")
    parser.add_argument("--experiment-dir", default=str(ACTIVE_ROOT / "experiments"))
    parser.add_argument("--max-artifacts", type=int, default=10)
    parser.add_argument("--report-output", default=str(DEFAULT_REPORT))
    parser.add_argument("--json-output", default=str(DEFAULT_EXPERIMENT))
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--compact-output", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0

    record = build_record(Path(args.experiment_dir), max(1, args.max_artifacts))
    report_path = Path(args.report_output)
    json_path = Path(args.json_output)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    record["outputs"] = {"report": rel(report_path), "experiment": rel(json_path)}
    report_path.write_text(render_report(record), encoding="utf-8")
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.compact_output:
        print(
            json.dumps(
                {
                    "status": record["status"],
                    **record["summary"],
                    "outputs": record["outputs"],
                    "live_orders_enabled": False,
                    "private_api_used": False,
                },
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
