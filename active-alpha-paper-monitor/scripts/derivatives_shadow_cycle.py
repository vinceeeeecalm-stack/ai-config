#!/usr/bin/env python3
"""Run one bounded, public-only derivatives shadow sampling cycle.

The cycle reuses the existing production discovery candidate set only as a
shadow handoff.  It never changes discovery ranking, never emits a formal user
action and never enters Paper or real-money ROI.  Runtime observations are
append-only and mature exactly seven days after capture.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import derivatives_shadow_collector as collector  # noqa: E402


ROOT = SCRIPTS_DIR.parent
SCANNER = ROOT / "scripts" / "impulse_capture_scanner.py"
DEFAULT_CONFIG = ROOT / "config" / "derivatives_shadow_v2.json"
DEFAULT_LEDGER = ROOT / "runtime" / "derivatives-shadow-v2-observations.jsonl"
DEFAULT_LATEST_OUTPUT = ROOT / "runtime" / "derivatives-shadow-v2-latest-cycle.json"
SCHEMA_VERSION = "DerivativesShadowCycleV1"


class DerivativesShadowCycleError(ValueError):
    """Raised when the bounded cycle or one of its safety boundaries fails."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DerivativesShadowCycleError(f"invalid_json:{path}") from exc
    if not isinstance(value, dict):
        raise DerivativesShadowCycleError(f"json_object_required:{path}")
    return value


def scanner_command(
    *,
    dynamic_top: int,
    top: int,
    max_workers: int,
    timeout_seconds: int,
) -> list[str]:
    if not 1 <= dynamic_top <= 100:
        raise DerivativesShadowCycleError("dynamic_top_out_of_range")
    if not 1 <= top <= 20:
        raise DerivativesShadowCycleError("top_out_of_range")
    if not 1 <= max_workers <= 16:
        raise DerivativesShadowCycleError("max_workers_out_of_range")
    if not 1 <= timeout_seconds <= 12:
        raise DerivativesShadowCycleError("source_timeout_out_of_range")
    return [
        sys.executable,
        str(SCANNER),
        "--dynamic-top",
        str(dynamic_top),
        "--top",
        str(top),
        "--max-workers",
        str(max_workers),
        "--timeout",
        str(timeout_seconds),
        "--request-mode",
        "tactical_1_7d",
        "--discovery-only",
        "--derivatives-shadow-handoff",
        "--no-write",
    ]


def run_public_discovery(
    *,
    dynamic_top: int,
    top: int,
    max_workers: int,
    timeout_seconds: int,
    whole_round_timeout_seconds: int,
) -> dict[str, Any]:
    if not 1 <= whole_round_timeout_seconds <= 120:
        raise DerivativesShadowCycleError("discovery_timeout_out_of_range")
    command = scanner_command(
        dynamic_top=dynamic_top,
        top=top,
        max_workers=max_workers,
        timeout_seconds=timeout_seconds,
    )
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=whole_round_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DerivativesShadowCycleError("discovery_whole_round_timeout") from exc
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip().replace("\n", " ")[:400]
        raise DerivativesShadowCycleError(f"discovery_failed:{completed.returncode}:{stderr}")
    try:
        discovery = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DerivativesShadowCycleError("discovery_stdout_not_json") from exc
    if not isinstance(discovery, dict):
        raise DerivativesShadowCycleError("discovery_stdout_object_required")
    discovery["cycle_discovery_elapsed_seconds"] = round(elapsed, 6)
    return discovery


def validate_cycle(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("schema_version") != SCHEMA_VERSION:
        raise DerivativesShadowCycleError(f"schema:{SCHEMA_VERSION}")
    shadow = result.get("shadow_run")
    if not isinstance(shadow, dict):
        raise DerivativesShadowCycleError("shadow_run_required")
    for field, expected in (
        ("production_rule_changed", False),
        ("formal_action_eligible", False),
        ("paper_roi_eligible", False),
        ("real_money_roi_eligible", False),
        ("business_ready_eligible", False),
        ("live_orders_enabled", False),
        ("private_api_used", False),
        ("human_confirmation_required", True),
    ):
        if result.get(field) is not expected:
            raise DerivativesShadowCycleError(f"unsafe_cycle:{field}")
    if shadow.get("production_rule_changed") is not False:
        raise DerivativesShadowCycleError("shadow_production_rule_changed")
    if shadow.get("formal_action_eligible") is not False:
        raise DerivativesShadowCycleError("shadow_formal_action_eligible")
    if shadow.get("paper_roi_eligible") is not False or shadow.get("real_money_roi_eligible") is not False:
        raise DerivativesShadowCycleError("shadow_roi_pollution")
    if shadow.get("live_orders_enabled") is not False or shadow.get("private_api_used") is not False:
        raise DerivativesShadowCycleError("shadow_execution_pollution")
    records = shadow.get("records")
    if not isinstance(records, list):
        raise DerivativesShadowCycleError("shadow_records_required")
    candidate_count = result.get("candidate_count")
    if not records and not (
        candidate_count == 0
        and result.get("run_status") in {"NO_ELIGIBLE_CANDIDATES", "DATA_DEGRADED"}
    ):
        raise DerivativesShadowCycleError("shadow_records_required")
    if any(record.get("oi_alone_can_authorize") is not False for record in records):
        raise DerivativesShadowCycleError("oi_alone_authorization_pollution")
    return result


def canonical_digest(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def structured_no_update(
    discovery: dict[str, Any] | None,
    config: dict[str, Any],
    *,
    run_status: str,
    blockers: list[str],
) -> dict[str, Any]:
    """Return an auditable zero-write cycle without disguising missing evidence."""

    if run_status not in {"NO_ELIGIBLE_CANDIDATES", "DATA_DEGRADED"}:
        raise DerivativesShadowCycleError("invalid_no_update_status")
    discovery = discovery if isinstance(discovery, dict) else {}
    captured_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    source_digest = canonical_digest(discovery) if discovery else None
    identity = (
        (discovery.get("dynamic_discovery_audit") or {}).get("exchange_spot_identity")
        if isinstance(discovery.get("dynamic_discovery_audit"), dict)
        else None
    )
    shadow = {
        "schema_version": "DerivativesShadowRunV1",
        "engine_version": "DerivativesShadowCollectorV2",
        "snapshot_id": "derivatives-shadow-empty-" + canonical_digest(
            {"captured_at": captured_at, "run_status": run_status, "blockers": blockers}
        )[:20],
        "captured_at": captured_at,
        "strategy_version": "unified-shortterm-derivatives-shadow-v2",
        "config_digest": canonical_digest(config),
        "source_digest": source_digest,
        "source_lineage_digest": source_digest,
        "evidence_mode": "public_runtime",
        "run_status": run_status,
        "candidate_count": 0,
        "candidate_symbols": [],
        "records": [],
        "directional_state_counts": {
            "CONFIRMED_LONG": 0,
            "CROWDED_CONFLICT": 0,
            "OI_ONLY_CONFLICT": 0,
            "NO_FUEL": 0,
            "DATA_INSUFFICIENT": 0,
        },
        "source_status_counts": {"COMPLETE": 0, "PARTIAL": 0, "DEGRADED": 0},
        "complete_coverage_rate_pct": None,
        "identity_audit": identity,
        "all_blockers": list(dict.fromkeys(blockers)),
        "production_rule_changed": False,
        "formal_action_eligible": False,
        "paper_roi_eligible": False,
        "real_money_roi_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "engine_version": "unified-shortterm-derivatives-shadow-v2",
        "snapshot_id": shadow["snapshot_id"],
        "strategy_version": shadow["strategy_version"],
        "config_digest": shadow["config_digest"],
        "source_digest": source_digest,
        "source_lineage_digest": source_digest,
        "captured_at": captured_at,
        "evidence_mode": "public_runtime",
        "run_status": run_status,
        "candidate_count": 0,
        "append_result": {"APPENDED": 0, "NO_UPDATE": 0},
        "all_blockers": shadow["all_blockers"],
        "shadow_run": shadow,
        "unique_next_step": "retry_at_next_preregistered_cycle_without_backfill",
        "production_rule_changed": False,
        "formal_action_eligible": False,
        "paper_roi_eligible": False,
        "real_money_roi_eligible": False,
        "business_ready_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    return validate_cycle(result)


def verified_empty_discovery(discovery: dict[str, Any]) -> bool:
    discovery_audit = discovery.get("dynamic_discovery_audit")
    identity_audit = (
        discovery_audit.get("exchange_spot_identity")
        if isinstance(discovery_audit, dict)
        else None
    )
    authoritative_identity_verified = (
        isinstance(identity_audit, dict)
        and identity_audit.get("authority") == "Binance /api/v3/exchangeInfo"
        and identity_audit.get("formal_identity_relaxed") is False
        and (
            identity_audit.get("batch_status") == "verified"
            or identity_audit.get("full_snapshot_status")
            == "verified_authoritative_fallback"
        )
    )
    eligible_candidate_count = discovery.get("eligible_candidate_count")
    return (
        discovery.get("schema_version") == "fast-candidate-funnel-v1"
        and discovery.get("phase") == "discovery"
        and discovery.get("derivatives_shadow_handoff_enabled") is True
        and discovery.get("top_candidates") == []
        and discovery.get("derivatives_shadow_handoff") == []
        and isinstance(eligible_candidate_count, int)
        and not isinstance(eligible_candidate_count, bool)
        and eligible_candidate_count == 0
        and not (discovery.get("errors") or [])
        and discovery.get("fresh_transport_verified") is True
        and authoritative_identity_verified
    )


def run_cycle(
    discovery: dict[str, Any],
    config: dict[str, Any],
    *,
    ledger: Path | None,
    fixture_sources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_payload = fixture_sources
    if isinstance(source_payload, dict) and isinstance(source_payload.get("derivatives_by_symbol"), dict):
        source_payload = source_payload["derivatives_by_symbol"]
    shadow = collector.run_shadow(discovery, config, source_payload)
    append_result = (
        collector.append_observations(ledger, shadow)
        if ledger is not None
        else {"APPENDED": 0, "NO_UPDATE": 0}
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "engine_version": "unified-shortterm-derivatives-shadow-v2",
        "snapshot_id": shadow["snapshot_id"],
        "strategy_version": shadow["strategy_version"],
        "config_digest": shadow["config_digest"],
        "source_digest": shadow["source_digest"],
        "source_lineage_digest": shadow["source_lineage_digest"],
        "captured_at": shadow["captured_at"],
        "evidence_mode": shadow["evidence_mode"],
        "run_status": shadow["run_status"],
        "candidate_count": shadow["candidate_count"],
        "append_result": append_result,
        "shadow_run": shadow,
        "unique_next_step": "settle_only_after_frozen_seven_day_review_due_at",
        "production_rule_changed": False,
        "formal_action_eligible": False,
        "paper_roi_eligible": False,
        "real_money_roi_eligible": False,
        "business_ready_eligible": False,
        "live_orders_enabled": False,
        "private_api_used": False,
        "human_confirmation_required": True,
    }
    return validate_cycle(result)


def write_json(path: Path | None, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path is None:
        print(rendered, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")


def write_cycle_outputs(
    result: dict[str, Any],
    *,
    output_path: Path | None,
    latest_path: Path | None,
) -> None:
    if latest_path is not None:
        write_json(latest_path, result)
    if output_path is not None:
        if latest_path is None or output_path.resolve() != latest_path.resolve():
            write_json(output_path, result)
    else:
        write_json(None, result)


def self_test() -> dict[str, Any]:
    command = scanner_command(dynamic_top=20, top=20, max_workers=8, timeout_seconds=8)
    expected = {
        "--discovery-only",
        "--derivatives-shadow-handoff",
        "--no-write",
        "tactical_1_7d",
    }
    missing = sorted(item for item in expected if item not in command)
    if missing:
        raise DerivativesShadowCycleError("self_test_scanner_boundary_missing:" + ",".join(missing))
    return {
        "schema_version": "DerivativesShadowCycleSelfTestV1",
        "status": "PASS",
        "checks": ["bounded_scanner_command", "shadow_handoff_opt_in", "no_scanner_write"],
        "production_rule_changed": False,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--discovery-input")
    parser.add_argument("--fixture-sources")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--output")
    parser.add_argument("--latest-output", default=str(DEFAULT_LATEST_OUTPUT))
    parser.add_argument("--no-append", action="store_true")
    parser.add_argument("--dynamic-top", type=int, default=40)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--source-timeout", type=int, default=8)
    parser.add_argument("--discovery-timeout", type=int, default=120)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.self_test:
        write_json(Path(args.output) if args.output else None, self_test())
        return
    config = load_json(Path(args.config))
    discovery: dict[str, Any] | None = None
    if args.discovery_input:
        discovery = load_json(Path(args.discovery_input))
    else:
        try:
            discovery = run_public_discovery(
                dynamic_top=args.dynamic_top,
                top=args.top,
                max_workers=args.max_workers,
                timeout_seconds=args.source_timeout,
                whole_round_timeout_seconds=args.discovery_timeout,
            )
        except DerivativesShadowCycleError as exc:
            result = structured_no_update(
                discovery,
                config,
                run_status="DATA_DEGRADED",
                blockers=[str(exc)],
            )
            write_cycle_outputs(
                result,
                output_path=Path(args.output) if args.output else None,
                latest_path=Path(args.latest_output) if args.latest_output else None,
            )
            return
    fixture_sources = load_json(Path(args.fixture_sources)) if args.fixture_sources else None
    ledger = None if args.no_append else Path(args.ledger)
    if verified_empty_discovery(discovery):
        result = structured_no_update(
            discovery,
            config,
            run_status="NO_ELIGIBLE_CANDIDATES",
            blockers=[],
        )
    else:
        try:
            result = run_cycle(discovery, config, ledger=ledger, fixture_sources=fixture_sources)
        except collector.DerivativesShadowError as exc:
            result = structured_no_update(
                discovery,
                config,
                run_status="DATA_DEGRADED",
                blockers=[str(exc)],
            )
    write_cycle_outputs(
        result,
        output_path=Path(args.output) if args.output else None,
        latest_path=Path(args.latest_output) if args.latest_output else None,
    )


if __name__ == "__main__":
    main()
