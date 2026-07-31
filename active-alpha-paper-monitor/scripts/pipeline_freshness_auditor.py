#!/usr/bin/env python3
"""Audit whether downstream paper artifacts are synced to the latest runner.

Read-only. This script never fetches market data, mutates the paper ledger,
opens paper trades, places live orders, or calls private APIs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
REPORT_DIR = ACTIVE_ROOT / "reports"
EXPERIMENT_DIR = ACTIVE_ROOT / "experiments"
LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def local_now() -> dt.datetime:
    return utc_now().astimezone(LOCAL_TZ)


def parse_timestamp(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def artifact_age_hours(value: Any, now: dt.datetime | None = None) -> float | None:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    current = (now or utc_now()).astimezone(dt.timezone.utc)
    return round(max(0.0, (current - parsed).total_seconds() / 3600.0), 4)


def freshness_status(value: Any, max_age_hours: float = 6.0, now: dt.datetime | None = None) -> str:
    age = artifact_age_hours(value, now=now)
    if age is None:
        return "missing"
    return "fresh" if age <= max_age_hours else "stale"


def read_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def latest(pattern: str, directory: Path = EXPERIMENT_DIR) -> Path | None:
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def has_top_blocked_candidates(payload: dict[str, Any]) -> bool:
    no_entry = payload.get("no_entry_summary") if isinstance(payload.get("no_entry_summary"), dict) else {}
    candidates = no_entry.get("top_blocked_candidates") if isinstance(no_entry, dict) else []
    return bool([item for item in (candidates or []) if isinstance(item, dict)])


def runner_is_safe_paper_source(payload: dict[str, Any]) -> bool:
    if payload.get("live_orders_enabled") is True:
        return False
    if payload.get("private_api_used") is True:
        return False
    if payload.get("allow_real_orders") is True:
        return False
    if payload.get("safety_errors"):
        return False
    return True


def latest_candidate_runner(
    directory: Path = EXPERIMENT_DIR,
    scan_limit: int = 80,
    now: dt.datetime | None = None,
) -> tuple[Path | None, dict[str, Any]]:
    files = sorted(directory.glob("*validation-progress-runner.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    latest_any = files[0] if files else None
    for path in files[: max(1, scan_limit)]:
        payload = read_json(path)
        if runner_is_safe_paper_source(payload) and has_top_blocked_candidates(payload):
            no_entry = payload.get("no_entry_summary") or {}
            created_at = payload.get("created_at") or payload.get("generated_at") or payload.get("completed_at")
            return path, {
                "status": "candidate_bearing_runner_selected",
                "artifact": rel(path),
                "created_at": created_at,
                "age_hours": artifact_age_hours(created_at, now=now),
                "freshness_status": freshness_status(created_at, now=now),
                "candidate_count": len(no_entry.get("top_blocked_candidates") or []),
                "latest_any_runner": rel(latest_any),
                "scanned_runner_count": min(len(files), scan_limit),
            }
    fallback_payload = read_json(latest_any)
    fallback_created_at = fallback_payload.get("created_at") or fallback_payload.get("generated_at") or fallback_payload.get("completed_at")
    return latest_any, {
        "status": "fallback_no_candidate_runner",
        "artifact": rel(latest_any),
        "created_at": fallback_created_at,
        "age_hours": artifact_age_hours(fallback_created_at, now=now),
        "freshness_status": freshness_status(fallback_created_at, now=now),
        "candidate_count": 0,
        "latest_any_runner": rel(latest_any),
        "scanned_runner_count": min(len(files), scan_limit),
    }


def rel(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT))
    except Exception:
        return str(path)


def normalize_artifact_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        return rel(path)
    if text.startswith("active-alpha-paper-monitor/"):
        return text
    if text.startswith("experiments/") or text.startswith("reports/") or text.startswith("handoffs/"):
        return f"active-alpha-paper-monitor/{text}"
    return text


def status_for_source(expected: Path | None, actual_ref: Any) -> tuple[str, str, str]:
    expected_rel = rel(expected)
    actual = normalize_artifact_ref(actual_ref)
    if not expected_rel:
        return "missing_expected_source", expected_rel, actual
    if not actual:
        return "missing_source_ref", expected_rel, actual
    if actual == expected_rel:
        return "synced", expected_rel, actual
    return "stale_source", expected_rel, actual


def check_row(
    name: str,
    artifact_path: Path | None,
    payload: dict[str, Any],
    source_field: str | None,
    expected_source: Path | None,
    upstream_status: str | None = None,
) -> dict[str, Any]:
    if not artifact_path:
        return {
            "name": name,
            "status": "missing",
            "artifact": "",
            "source_field": source_field,
            "expected_source": rel(expected_source),
            "actual_source": "",
            "next_action": f"run_{name}",
        }
    if not source_field:
        status = "present"
        expected_rel = ""
        actual = ""
    else:
        status, expected_rel, actual = status_for_source(expected_source, payload.get(source_field))
    if upstream_status and upstream_status not in {"synced", "present"}:
        status = "upstream_stale"
    next_action = "none"
    if status in {"missing", "missing_source_ref", "stale_source", "upstream_stale"}:
        next_action = f"rerun_{name}_after_latest_runner"
    return {
        "name": name,
        "status": status,
        "artifact": rel(artifact_path),
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "source_field": source_field,
        "expected_source": expected_rel,
        "actual_source": actual,
        "next_action": next_action,
    }


def build_record(
    experiment_dir: Path = EXPERIMENT_DIR,
    now_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    runner_path = latest("*validation-progress-runner.json", experiment_dir)
    runner_payload = read_json(runner_path)
    candidate_runner_path, candidate_runner_meta = latest_candidate_runner(experiment_dir, now=now_utc)
    watchlist_path = latest("*recovery-watchlist-monitor.json", experiment_dir)
    watchlist_payload = read_json(watchlist_path)
    recovery_sampler_path = latest("*recovery-watchlist-paper-sampler.json", experiment_dir)
    recovery_sampler_payload = read_json(recovery_sampler_path)
    retest_path = latest("*top-blocked-candidate-retest-lab.json", experiment_dir)
    retest_payload = read_json(retest_path)
    retest_sampler_path = latest("*top-blocked-retest-quality-scout-sampler.json", experiment_dir)
    retest_sampler_payload = read_json(retest_sampler_path)
    capital_path = latest("*paper-capital-allocation-audit.json", experiment_dir)
    capital_payload = read_json(capital_path)

    watchlist = check_row(
        "recovery_watchlist_monitor",
        watchlist_path,
        watchlist_payload,
        "source_dynamic_market_context",
        candidate_runner_path,
    )
    recovery_sampler = check_row(
        "recovery_watchlist_paper_sampler",
        recovery_sampler_path,
        recovery_sampler_payload,
        "source_watchlist",
        watchlist_path,
        upstream_status=watchlist["status"],
    )
    retest = check_row(
        "top_blocked_candidate_retest_lab",
        retest_path,
        retest_payload,
        "source_runner",
        candidate_runner_path,
    )
    retest_sampler = check_row(
        "top_blocked_retest_quality_scout_sampler",
        retest_sampler_path,
        retest_sampler_payload,
        "source_retest",
        retest_path,
        upstream_status=retest["status"],
    )
    capital = check_row(
        "paper_capital_allocation_auditor",
        capital_path,
        capital_payload,
        None,
        None,
    )

    checks = [watchlist, recovery_sampler, retest, retest_sampler, capital]
    stale = [row for row in checks if row["status"] in {"stale_source", "upstream_stale"}]
    missing = [row for row in checks if row["status"].startswith("missing")]
    artifact_sync_status = "pass" if not stale and not missing else "warn"
    status = artifact_sync_status
    runner_created_at = runner_payload.get("created_at") or runner_payload.get("generated_at") or runner_payload.get("completed_at")
    runner_age_hours = artifact_age_hours(runner_created_at, now=now_utc)
    runner_freshness = freshness_status(runner_created_at, now=now_utc)
    current_market_readiness_status = "fresh" if runner_freshness == "fresh" else "stale_not_actionable"
    next_actions: list[str] = []
    if not runner_path:
        status = "blocked"
        current_market_readiness_status = "missing_not_actionable"
        next_actions.append("Run validation_progress_runner before relying on downstream paper reports.")
    elif runner_freshness != "fresh":
        status = "warn"
        next_actions.append("Refresh validation_progress_runner with current public market data before any new paper entry or current-signal claim.")
    for row in stale + missing:
        next_actions.append(f"{row['name']}: {row['next_action']}")
    if not next_actions:
        next_actions.append("All tracked downstream paper artifacts are synced or present.")

    now = local_now()
    return {
        "run_id": f"{now.strftime('%Y%m%d-%H%M%S')}-pipeline-freshness-audit",
        "created_at": now.isoformat(),
        "scope": "paper_only_pipeline_freshness",
        "status": status,
        "artifact_sync_status": artifact_sync_status,
        "current_market_readiness_status": current_market_readiness_status,
        "live_orders_enabled": False,
        "private_api_used": False,
        "allow_real_orders": False,
        "ledger_mutated": False,
        "market_data_fetched": False,
        "latest_runner": {
            "artifact": rel(runner_path),
            "run_id": runner_payload.get("run_id"),
            "created_at": runner_created_at,
            "age_hours": runner_age_hours,
            "freshness_status": runner_freshness,
            "dynamic_scan_status": (runner_payload.get("dynamic_scan_universe") or {}).get("status"),
        },
        "latest_candidate_runner": candidate_runner_meta,
        "summary": {
            "check_count": len(checks),
            "stale_count": len(stale),
            "missing_count": len(missing),
            "synced_or_present_count": len(checks) - len(stale) - len(missing),
            "runner_stale_count": 0 if runner_freshness == "fresh" else 1,
        },
        "checks": checks,
        "next_actions": next_actions,
        "outputs": {},
    }


def render_report(record: dict[str, Any]) -> str:
    lines = [
        f"# Pipeline Freshness Audit | {record['run_id']}",
        "",
        "Read-only check. No market data fetch, no paper trade, no ledger mutation, no live order.",
        "",
        f"- status: `{record.get('status')}`",
        f"- artifact_sync_status: `{record.get('artifact_sync_status')}`",
        f"- current_market_readiness_status: `{record.get('current_market_readiness_status')}`",
        f"- latest_runner: `{(record.get('latest_runner') or {}).get('artifact') or 'missing'}`",
        f"- latest_runner_freshness: `{(record.get('latest_runner') or {}).get('freshness_status')}` age_hours `{(record.get('latest_runner') or {}).get('age_hours')}`",
        f"- latest_candidate_runner: `{(record.get('latest_candidate_runner') or {}).get('artifact') or 'missing'}`",
        f"- candidate_runner_status: `{(record.get('latest_candidate_runner') or {}).get('status')}`",
        f"- latest_runner_dynamic_scan_status: `{(record.get('latest_runner') or {}).get('dynamic_scan_status')}`",
        f"- stale_count: `{(record.get('summary') or {}).get('stale_count')}`",
        f"- missing_count: `{(record.get('summary') or {}).get('missing_count')}`",
        f"- live_orders_enabled: `{record.get('live_orders_enabled')}`",
        f"- private_api_used: `{record.get('private_api_used')}`",
        "",
        "## Artifact Sync",
        "",
        "| Artifact | Status | Expected Source | Actual Source | Next Action |",
        "|---|---|---|---|---|",
    ]
    for row in record.get("checks") or []:
        lines.append(
            "| "
            + f"`{row.get('name')}` | `{row.get('status')}` | "
            + f"`{row.get('expected_source') or '-'}` | `{row.get('actual_source') or '-'}` | "
            + f"`{row.get('next_action')}` |"
        )
    lines.extend(["", "## Next Actions", ""])
    for item in record.get("next_actions") or []:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pipeline_freshness_audit_", dir="/private/tmp") as tmp_text:
        tmp = Path(tmp_text)
        runner = tmp / "experiments" / "20260627-100000-validation-progress-runner.json"
        newer_empty_runner = tmp / "experiments" / "20260627-110000-validation-progress-runner.json"
        watchlist = tmp / "experiments" / "20260627-100100-recovery-watchlist-monitor.json"
        recovery_sampler = tmp / "experiments" / "20260627-100200-recovery-watchlist-paper-sampler.json"
        retest = tmp / "experiments" / "20260627-100300-top-blocked-candidate-retest-lab.json"
        retest_sampler = tmp / "experiments" / "20260627-100400-top-blocked-retest-quality-scout-sampler.json"
        capital = tmp / "experiments" / "20260627-100500-paper-capital-allocation-audit.json"
        runner.parent.mkdir(parents=True, exist_ok=True)
        runner.write_text(
            json.dumps(
                {
                    "run_id": "runner",
                    "created_at": "2026-07-09T10:00:00+00:00",
                    "dynamic_scan_universe": {"status": "ok"},
                    "no_entry_summary": {"top_blocked_candidates": [{"symbol": "TESTUSDT"}]},
                    "live_orders_enabled": False,
                    "private_api_used": False,
                }
            ),
            encoding="utf-8",
        )
        newer_empty_runner.write_text(
            json.dumps(
                {
                    "run_id": "newer-empty-maintenance-runner",
                    "created_at": "2026-07-09T10:00:00+00:00",
                    "dynamic_scan_universe": {"status": "ok"},
                    "no_entry_summary": {"top_blocked_candidates": []},
                }
            ),
            encoding="utf-8",
        )
        watchlist.write_text(
            json.dumps({"run_id": "watch", "source_dynamic_market_context": rel(runner)}),
            encoding="utf-8",
        )
        recovery_sampler.write_text(
            json.dumps({"run_id": "sampler", "source_watchlist": rel(watchlist)}),
            encoding="utf-8",
        )
        retest.write_text(
            json.dumps({"run_id": "retest", "source_runner": rel(runner)}),
            encoding="utf-8",
        )
        retest_sampler.write_text(
            json.dumps({"run_id": "retest-sampler", "source_retest": rel(retest)}),
            encoding="utf-8",
        )
        capital.write_text(json.dumps({"run_id": "capital"}), encoding="utf-8")
        candidate_runner, candidate_meta = latest_candidate_runner(runner.parent)
        assert candidate_runner == runner, candidate_meta
        assert candidate_meta["status"] == "candidate_bearing_runner_selected", candidate_meta
        synced = check_row("recovery_watchlist_monitor", watchlist, read_json(watchlist), "source_dynamic_market_context", runner)
        assert synced["status"] == "synced", synced
        retest_synced = check_row("top_blocked_candidate_retest_lab", watchlist, {"source_runner": rel(runner)}, "source_runner", candidate_runner)
        assert retest_synced["status"] == "synced", retest_synced
        stale = check_row(
            "recovery_watchlist_monitor",
            watchlist,
            {"source_dynamic_market_context": "active-alpha-paper-monitor/experiments/old-runner.json"},
            "source_dynamic_market_context",
            runner,
        )
        assert stale["status"] == "stale_source", stale
        downstream = check_row(
            "recovery_watchlist_paper_sampler",
            watchlist,
            {"source_watchlist": rel(watchlist)},
            "source_watchlist",
            watchlist,
            upstream_status="stale_source",
        )
        assert downstream["status"] == "upstream_stale", downstream
        fixed_now = dt.datetime(2026, 7, 10, 12, 0, tzinfo=dt.timezone.utc)
        assert freshness_status("2026-07-10T10:00:00+00:00", now=fixed_now) == "fresh"
        assert freshness_status("2026-07-09T10:00:00+00:00", now=fixed_now) == "stale"
        assert freshness_status(None, now=fixed_now) == "missing"
        stale_record = build_record(runner.parent, now_utc=fixed_now)
        assert stale_record["artifact_sync_status"] == "pass", stale_record
        assert stale_record["status"] == "warn", stale_record
        assert stale_record["current_market_readiness_status"] == "stale_not_actionable", stale_record

        fresh_created_at = "2026-07-10T10:00:00+00:00"
        for runner_path in (runner, newer_empty_runner):
            runner_payload = read_json(runner_path)
            runner_payload["created_at"] = fresh_created_at
            runner_path.write_text(json.dumps(runner_payload), encoding="utf-8")
        fresh_record = build_record(runner.parent, now_utc=fixed_now)
        assert fresh_record["artifact_sync_status"] == "pass", fresh_record
        assert fresh_record["status"] == "pass", fresh_record
        assert fresh_record["current_market_readiness_status"] == "fresh", fresh_record
    return {
        "status": "ok",
        "synced_source_verified": True,
        "stale_source_verified": True,
        "upstream_stale_verified": True,
        "candidate_bearing_runner_selection_verified": True,
        "runner_wall_clock_freshness_verified": True,
        "synced_but_stale_pipeline_warn_verified": True,
        "synced_and_fresh_pipeline_pass_verified": True,
        "uses_temporary_files_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return 0
    record = build_record()
    if not args.no_write:
        run_stamp = local_now().strftime("%Y%m%d-%H%M%S")
        date = local_now().strftime("%Y-%m-%d")
        report_path = REPORT_DIR / f"{date}-pipeline-freshness-audit-{run_stamp}.md"
        experiment_path = EXPERIMENT_DIR / f"{run_stamp}-pipeline-freshness-audit.json"
        record["outputs"] = {"report": rel(report_path), "experiment": rel(experiment_path)}
        write_json(experiment_path, record)
        write_text(report_path, render_report(record))
    if args.compact_output:
        print(
            json.dumps(
                {
                    "status": record.get("status"),
                    "artifact_sync_status": record.get("artifact_sync_status"),
                    "current_market_readiness_status": record.get("current_market_readiness_status"),
                    "run_id": record.get("run_id"),
                    "summary": record.get("summary"),
                    "latest_runner": record.get("latest_runner"),
                    "latest_candidate_runner": record.get("latest_candidate_runner"),
                    "next_actions": record.get("next_actions"),
                    "outputs": record.get("outputs"),
                    "live_orders_enabled": record.get("live_orders_enabled"),
                    "private_api_used": record.get("private_api_used"),
                    "ledger_mutated": record.get("ledger_mutated"),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
