#!/usr/bin/env python3
"""Deterministically audit the full unified short-term objective without trading.

The auditor binds each layer to frozen governance evidence and, where required,
runtime observations.  It never promotes a profit rule and never emits a market
action.  File existence alone is deliberately insufficient for a verified state.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "UnifiedShortTermEngineReadinessV1"
RUNTIME_LEVELS = {"RUNTIME_VERIFIED", "BUSINESS_READY"}
SAFETY = {
    "production_rule_changed": False,
    "formal_action_eligible": False,
    "paper_roi_eligible": False,
    "real_money_roi_eligible": False,
    "business_ready_eligible": False,
    "live_orders_enabled": False,
    "private_api_used": False,
    "human_confirmation_required": True,
}


class ReadinessAuditError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_time(value: Any, field: str) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ReadinessAuditError(f"missing_time:{field}")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReadinessAuditError(f"invalid_time:{field}") from exc
    if parsed.tzinfo is None:
        raise ReadinessAuditError(f"timezone_required:{field}")
    return parsed.astimezone(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReadinessAuditError(f"json_object_required:{path}")
    return value


def load_jsonl_safe(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], []
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"malformed_jsonl:{path.name}:{number}")
            continue
        if not isinstance(value, dict):
            errors.append(f"non_object_jsonl:{path.name}:{number}")
            continue
        records.append(value)
    return records, errors


def json_files(directory: Path) -> list[dict[str, Any]]:
    records = []
    if not directory.exists():
        return records
    for path in sorted(directory.glob("*.json")):
        try:
            records.append(load_json(path))
        except (OSError, json.JSONDecodeError, ReadinessAuditError):
            continue
    return records


def parse_automation_fields(text: str) -> dict[str, Any]:
    """Parse only the top-level scalar whitelist used by the audit.

    The canonical automation file contains simple TOML basic strings and
    integers.  Keeping this parser intentionally narrow preserves Python 3.9
    compatibility without accepting nested or executable content.
    """

    allowed = {"id", "kind", "name", "prompt", "status", "rrule", "notification_policy", "target_thread_id"}
    result: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = (part.strip() for part in line.split("=", 1))
        if key not in allowed:
            continue
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise ReadinessAuditError(f"automation_scalar_invalid:{key}") from exc
        if not isinstance(value, (str, int, float, bool)):
            raise ReadinessAuditError(f"automation_scalar_required:{key}")
        result[key] = value
    return result


def release_state(repo: Path, goal_id: str) -> dict[str, Any]:
    releases = [row for row in json_files(repo / ".codex/governance/releases") if row.get("goal_id") == goal_id]
    releases.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    if not releases:
        return {"goal_id": goal_id, "verified": False, "blockers": [f"release_missing:{goal_id}"], "evidence_ids": []}
    release = releases[0]
    blockers = []
    if release.get("schema_version") != "ReleaseEvidenceV1":
        blockers.append(f"release_schema_invalid:{goal_id}")
    if release.get("achieved_level") not in RUNTIME_LEVELS:
        blockers.append(f"runtime_level_missing:{goal_id}")
    if release.get("blocked") is True:
        blockers.append(f"release_blocked:{goal_id}")
    release_owner = release.get("created_by")
    if not isinstance(release_owner, str) or not release_owner:
        blockers.append(f"release_owner_missing:{goal_id}")
    verifier_id = release.get("independent_verification_id")
    verifier = next((row for row in json_files(repo / ".codex/governance/verification") if row.get("id") == verifier_id), None)
    if not verifier_id or verifier is None:
        blockers.append(f"independent_verification_missing:{goal_id}")
    elif verifier.get("schema_version") != "VerificationResultV1":
        blockers.append(f"independent_verification_schema_invalid:{goal_id}")
    elif verifier.get("level") not in RUNTIME_LEVELS:
        blockers.append(f"independent_verification_level_insufficient:{goal_id}")
    elif verifier.get("result") != "PASS":
        blockers.append(f"independent_verification_not_pass:{goal_id}")
    elif verifier.get("goal_id") != goal_id or verifier.get("source_digest") != release.get("source_digest"):
        blockers.append(f"independent_verification_binding_mismatch:{goal_id}")
    elif not isinstance(verifier.get("verifier_thread_id"), str) or not verifier["verifier_thread_id"]:
        blockers.append(f"independent_verifier_identity_missing:{goal_id}")
    elif verifier.get("verifier_thread_id") == release_owner:
        blockers.append(f"independent_verifier_same_as_release_owner:{goal_id}")
    return {
        "goal_id": goal_id,
        "verified": not blockers,
        "achieved_level": release.get("achieved_level"),
        "source_digest": release.get("source_digest"),
        "release_id": release.get("id"),
        "independent_verification_id": verifier_id,
        "blockers": blockers,
        "evidence_ids": [value for value in (release.get("id"), verifier_id) if value],
    }


def automation_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"active": False, "blockers": ["phase1_automation_missing"], "path": str(path) if path else None}
    try:
        payload = parse_automation_fields(path.read_text(encoding="utf-8"))
    except (OSError, ReadinessAuditError):
        return {"active": False, "blockers": ["phase1_automation_invalid"], "path": str(path)}
    prompt = str(payload.get("prompt") or "")
    required_prompt_tokens = (
        "derivatives_shadow_cycle.py",
        "derivatives_shadow_outcome_reviewer.py",
        "derivatives_shadow_promotion_evaluator.py",
        "2026-08-16T04:15:00Z",
    )
    rrule = str(payload.get("rrule") or "")
    blockers = []
    expected_rrule = "FREQ=DAILY;BYHOUR=0,4,8,12,16,20;BYMINUTE=15;BYSECOND=0;UNTIL=20260823T161500Z"
    unsafe_prompt_tokens = ("AUTO_LIVE_ORDER", "PRIVATE_API", "LIVE_ORDERS_ENABLED=TRUE", "PRIVATE_API_USED=TRUE")
    required_safety_phrases = ("不要启动阶段二", "不要自动下单", "不要把影子结果计入 Paper/真钱 ROI 或 BUSINESS_READY")
    if payload.get("status") != "ACTIVE":
        blockers.append("phase1_automation_not_active")
    if payload.get("kind") != "heartbeat":
        blockers.append("phase1_automation_not_heartbeat")
    if any(token not in prompt for token in required_prompt_tokens) or rrule != expected_rrule:
        blockers.append("phase1_automation_contract_incomplete")
    if any(token in prompt.upper() for token in unsafe_prompt_tokens) or any(phrase not in prompt for phrase in required_safety_phrases):
        blockers.append("phase1_automation_safety_boundary_invalid")
    return {
        "active": not blockers,
        "id": payload.get("id"),
        "status": payload.get("status"),
        "rrule": payload.get("rrule"),
        "path": str(path),
        "blockers": blockers,
    }


def load_promotion_evaluation(repo: Path, outcomes_path: Path) -> dict[str, Any]:
    script = repo / "active-alpha-paper-monitor/scripts/derivatives_shadow_promotion_evaluator.py"
    config_path = repo / "active-alpha-paper-monitor/config/derivatives_shadow_promotion_gate_v1.json"
    if not script.exists() or not config_path.exists():
        return {"decision": "EVIDENCE_MISSING", "all_blockers": ["promotion_gate_implementation_missing"]}
    spec = importlib.util.spec_from_file_location("readiness_promotion_evaluator", script)
    if spec is None or spec.loader is None:
        return {"decision": "EVIDENCE_MISSING", "all_blockers": ["promotion_gate_load_failed"]}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.evaluate(module.load_jsonl(outcomes_path), module.load_json(config_path))
    except Exception as exc:  # fail closed; the detailed evaluator still owns diagnosis
        return {"decision": "EVIDENCE_INVALID", "all_blockers": [f"promotion_gate_error:{type(exc).__name__}"]}


def observation_summary(path: Path) -> dict[str, Any]:
    rows, errors = load_jsonl_safe(path)
    valid = [row for row in rows if row.get("schema_version") == "DerivativesShadowObservationV1"]
    invalid_count = len(rows) - len(valid)
    states: dict[str, int] = {}
    for row in valid:
        state = str(row.get("directional_state") or "UNKNOWN")
        states[state] = states.get(state, 0) + 1
    return {
        "observation_count": len(valid),
        "unique_snapshot_count": len({row.get("snapshot_id") for row in valid if row.get("snapshot_id")}),
        "directional_state_counts": dict(sorted(states.items())),
        "invalid_record_count": invalid_count + len(errors),
        "errors": errors,
    }


def live_profit_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"status": "UNMEASURED", "trade_count": 0, "blockers": ["live_profit_time_series_missing"]}
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError, ReadinessAuditError):
        return {"status": "UNMEASURED", "trade_count": 0, "blockers": ["live_profit_summary_invalid"]}
    trade_count = payload.get("trade_count")
    if payload.get("schema_version") != "ShortTermProfitAttributionV1" or payload.get("evidence_mode") != "live":
        return {"status": "UNMEASURED", "trade_count": 0, "blockers": ["live_profit_summary_not_authoritative"]}
    if isinstance(trade_count, bool) or not isinstance(trade_count, int) or trade_count <= 0:
        return {"status": "UNMEASURED", "trade_count": 0, "blockers": ["live_profit_trade_sample_missing"]}
    return {
        "status": str(payload.get("profit_status") or "EXPERIMENT_RUNNING"),
        "trade_count": trade_count,
        "money_weighted_net_roi_pct": payload.get("money_weighted_net_roi_pct"),
        "blockers": [],
    }


def latest_cycle_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"status": "UNKNOWN", "run_status": None, "blockers": []}
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError, ReadinessAuditError):
        return {
            "status": "INVALID",
            "run_status": None,
            "blockers": ["phase1_latest_cycle_invalid"],
        }
    if payload.get("schema_version") != "DerivativesShadowCycleV1":
        return {
            "status": "INVALID",
            "run_status": payload.get("run_status"),
            "blockers": ["phase1_latest_cycle_schema_invalid"],
        }
    run_status = str(payload.get("run_status") or "UNKNOWN")
    safety_invalid = any(
        payload.get(field) is not expected
        for field, expected in SAFETY.items()
    )
    if safety_invalid:
        return {
            "status": "INVALID",
            "run_status": run_status,
            "blockers": ["phase1_latest_cycle_safety_invalid"],
        }
    blockers = [str(item) for item in payload.get("all_blockers") or []]
    if run_status == "DATA_DEGRADED":
        blockers = blockers or ["phase1_latest_cycle_data_degraded"]
        return {
            "status": "DATA_DEGRADED",
            "run_status": run_status,
            "captured_at": payload.get("captured_at"),
            "snapshot_id": payload.get("snapshot_id"),
            "blockers": blockers,
        }
    if run_status not in {"SHADOW_EVIDENCE_COLLECTED", "NO_ELIGIBLE_CANDIDATES"}:
        blockers = blockers or [f"phase1_latest_cycle_unknown_status:{run_status}"]
        return {
            "status": "INVALID",
            "run_status": run_status,
            "blockers": blockers,
        }
    return {
        "status": "HEALTHY",
        "run_status": run_status,
        "captured_at": payload.get("captured_at"),
        "snapshot_id": payload.get("snapshot_id"),
        "blockers": [],
    }


def layer(layer_id: str, status: str, reason: str, evidence: list[str], blockers: list[str]) -> dict[str, Any]:
    return {"layer_id": layer_id, "status": status, "reason": reason, "evidence_ids": evidence, "blockers": blockers}


def build_readiness(
    repo: Path,
    *,
    now: dt.datetime,
    automation_path: Path | None,
    observation_path: Path | None = None,
    outcome_path: Path | None = None,
    live_profit_path: Path | None = None,
    latest_cycle_path: Path | None = None,
) -> dict[str, Any]:
    repo = repo.resolve()
    observation_path = observation_path or repo / "active-alpha-paper-monitor/runtime/derivatives-shadow-v2-observations.jsonl"
    outcome_path = outcome_path or repo / "active-alpha-paper-monitor/runtime/derivatives-shadow-v2-outcomes.jsonl"
    latest_cycle_path = latest_cycle_path or repo / "active-alpha-paper-monitor/runtime/derivatives-shadow-v2-latest-cycle.json"
    releases = {
        "stage0": release_state(repo, "unified-short-term-opportunity-engine-v1"),
        "dynamic_candidate": release_state(repo, "crypto-dynamic-spot-eligibility-failover-v15"),
        "manual_core": release_state(repo, "universal-investment-core-github-canonical-v3"),
        "phase1_shadow": release_state(repo, "unified-shortterm-derivatives-shadow-v2"),
        "phase1_sampling": release_state(repo, "derivatives-shadow-continuous-sampling-v1"),
    }
    automation = automation_state(automation_path)
    observations = observation_summary(observation_path)
    outcomes, outcome_errors = load_jsonl_safe(outcome_path)
    promotion = load_promotion_evaluation(repo, outcome_path)
    live_profit = live_profit_state(live_profit_path)
    latest_cycle = latest_cycle_state(latest_cycle_path)
    foundation_ok = releases["stage0"]["verified"] and releases["dynamic_candidate"]["verified"] and releases["manual_core"]["verified"]
    phase1_engineering_ok = releases["phase1_shadow"]["verified"] and releases["phase1_sampling"]["verified"]
    phase1_runtime_ok = (
        automation["active"]
        and observations["observation_count"] > 0
        and observations["invalid_record_count"] == 0
        and not latest_cycle["blockers"]
    )
    earliest_raw = promotion.get("earliest_production_promotion_at") or "2026-08-16T04:30:20Z"
    earliest = parse_time(earliest_raw, "earliest_production_promotion_at")
    decision = str(promotion.get("decision") or "EVIDENCE_MISSING")
    if not phase1_engineering_ok:
        phase1_status = "EVIDENCE_MISSING"
    elif not phase1_runtime_ok:
        phase1_status = "RUNTIME_DEGRADED"
    elif decision == "PROMOTION_REVIEW_ELIGIBLE" and now >= earliest:
        phase1_status = "PROMOTION_REVIEW_ELIGIBLE"
    elif decision == "REJECT_PHASE1" and now >= earliest:
        phase1_status = "REJECTED"
    else:
        phase1_status = "EVIDENCE_COLLECTING"

    layers = [
        layer("unified_contract_and_stage0_baseline", "RUNTIME_VERIFIED" if releases["stage0"]["verified"] else "EVIDENCE_MISSING", "七个合同和无前视阶段零基线。", releases["stage0"]["evidence_ids"], releases["stage0"]["blockers"]),
        layer("dynamic_liquidity_candidate_pool", "RUNTIME_VERIFIED" if releases["dynamic_candidate"]["verified"] else "EVIDENCE_MISSING", "动态候选由独立发布的现货身份、流动性与局部失败运行证据支持。", releases["dynamic_candidate"]["evidence_ids"], releases["dynamic_candidate"]["blockers"]),
        layer("derivatives_factors_front_loaded", phase1_status, "OI、funding、basis 与合约主动成交只在影子层连续采样，尚未改变生产排名。", releases["phase1_shadow"]["evidence_ids"] + releases["phase1_sampling"]["evidence_ids"], releases["phase1_shadow"]["blockers"] + releases["phase1_sampling"]["blockers"] + automation["blockers"] + observations["errors"] + latest_cycle["blockers"]),
        layer("persistent_near_miss_and_rank_acceleration", "SHADOW_ONLY", "阶段零具备确定性接口；阶段二尚未冻结和激活。", releases["stage0"]["evidence_ids"], ["phase2_not_activated"]),
        layer("adaptive_holding_clock", "SHADOW_ONLY", "分钟级至七天持有时钟仅完成影子合同；阶段三尚未激活。", releases["stage0"]["evidence_ids"], ["phase3_not_activated"]),
        layer("data_fairness_and_local_degradation", "PARTIAL_RUNTIME_VERIFIED" if phase1_engineering_ok else "EVIDENCE_MISSING", "阶段一已验证候选级局部降级；全市场覆盖公平仍待独立阶段。", releases["phase1_shadow"]["evidence_ids"], ["phase4_full_fairness_not_activated"]),
        layer("time_matched_regression", "BASELINE_ONLY", "阶段零三组七天路径可回放；按动态预计持有时间分组尚未晋升。", releases["stage0"]["evidence_ids"], ["phase5_not_activated"]),
        layer("unique_top1_and_formal_action", "RUNTIME_VERIFIED" if releases["manual_core"]["verified"] else "EVIDENCE_MISSING", "每轮唯一 research_top1，用户动作仅三种；本审计不生成动作。", releases["manual_core"]["evidence_ids"], releases["manual_core"]["blockers"]),
        layer("lifecycle_monitoring_and_alerting", "SHADOW_ONLY", "生命周期不超过七天的合同存在；事件提醒与去重尚未激活。", releases["stage0"]["evidence_ids"], ["phase6_lifecycle_not_activated"]),
        layer("missed_opportunity_append_only_review", "SHADOW_ONLY", "漏检合同已验证；自动发现真实漏检尚未激活。", releases["stage0"]["evidence_ids"], ["phase6_missed_review_not_activated"]),
        layer("unified_live_profit_attribution", live_profit["status"], "只接受同一真钱战术池的资金加权净 ROI；Paper 永远隔离。", [], live_profit["blockers"]),
    ]
    blockers: list[str] = []
    for item in layers:
        blockers.extend(item["blockers"])
    blockers.extend(outcome_errors)
    blockers.extend(str(item) for item in promotion.get("all_blockers") or [])
    blockers = list(dict.fromkeys(blockers))

    if not foundation_ok:
        unique_blocker = next((item for item in blockers if item.startswith(("release_", "runtime_level_", "independent_"))), "foundation_evidence_missing")
        unique_next_step = "修复阶段零或 Manual 权威内核的发布/独立复验绑定；不要运行新阶段。"
        next_eligible_phase = None
    elif not phase1_engineering_ok:
        unique_blocker = next((item for item in blockers if "unified-shortterm-derivatives" in item or "derivatives-shadow-continuous" in item), "phase1_engineering_evidence_missing")
        unique_next_step = "修复阶段一影子采集的发布证据；生产规则保持阶段零。"
        next_eligible_phase = None
    elif not automation["active"]:
        unique_blocker = automation["blockers"][0]
        unique_next_step = "恢复已批准的阶段一有界采样/结算自动化，不修改赚钱规则。"
        next_eligible_phase = None
    elif latest_cycle["blockers"]:
        unique_blocker = latest_cycle["blockers"][0]
        unique_next_step = "恢复阶段一 discovery→handoff 当前采样链；保留已有观察并禁止回填漏采。"
        next_eligible_phase = None
    elif observations["observation_count"] == 0 or observations["invalid_record_count"]:
        unique_blocker = "phase1_observation_evidence_missing_or_invalid"
        unique_next_step = "修复阶段一公开观察账本并恢复追加式采样；不要激活阶段二。"
        next_eligible_phase = None
    elif phase1_status == "PROMOTION_REVIEW_ELIGIBLE":
        unique_blocker = "phase1_requires_separate_governed_promotion"
        unique_next_step = "新建 superseding GoalContract，只申请把阶段一衍生品前移晋升为生产候选因子。"
        next_eligible_phase = "PHASE1_PRODUCTION_PROMOTION_REVIEW"
    elif phase1_status == "REJECTED":
        unique_blocker = "phase1_rejected_by_preregistered_gate"
        unique_next_step = "封存阶段一拒绝结论；在新的十四天窗口只申请阶段二持续候选与排名加速度。"
        next_eligible_phase = "PHASE2_NEAR_MISS_RANK_ACCELERATION"
    else:
        unique_blocker = "phase1_forward_outcomes_and_gate_pending"
        unique_next_step = "继续已批准的阶段一公开影子采样和七天结算，等待预注册晋升门给出合格或拒绝。"
        next_eligible_phase = None

    if unique_blocker not in blockers:
        blockers.insert(0, unique_blocker)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now),
        "active_goal": "unified-short-term-opportunity-engine-v1",
        "audit_goal": "unified-shortterm-phase-readiness-audit-v1",
        "system_delivery_status": "RUNTIME_VERIFIED" if foundation_ok and phase1_engineering_ok else "EVIDENCE_MISSING",
        "overall_engine_status": "INCREMENTAL_VALIDATION",
        "current_production_phase": "PHASE0_BASELINE",
        "current_shadow_phase": "PHASE1_DERIVATIVES_FRONT_LOAD",
        "phase1_status": phase1_status,
        "next_eligible_phase": next_eligible_phase,
        "business_outcome_status": "EVIDENCE_PENDING",
        "live_profit_status": live_profit["status"],
        "paper_status": "INTERNAL_REGRESSION_ONLY",
        "long_term_zone_status": "OUT_OF_SCOPE_UNCHANGED",
        "phase1_evidence": {
            **observations,
            "complete_review_count": len(outcomes),
            "promotion_decision": decision,
            "promotion_earliest_at": iso(earliest),
            "automation": automation,
            "latest_cycle": latest_cycle,
        },
        "layer_status": layers,
        "all_blockers": blockers,
        "unique_blocker": unique_blocker,
        "unique_next_step": unique_next_step,
        "fourteen_day_single_rule_gate_enforced": True,
        "formal_actions_allowed_elsewhere": ["ENTER_NOW", "WAIT_FOR_ENTRY", "NO_TRADE"],
        **SAFETY,
    }
    payload["audit_digest"] = sha256_json(payload)
    return payload


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for directory in (".codex/governance/releases", ".codex/governance/verification", "active-alpha-paper-monitor/scripts", "active-alpha-paper-monitor/config", "active-alpha-paper-monitor/runtime"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        # Use the real evaluator implementation while all other evidence is a minimal frozen fixture.
        real_repo = Path(__file__).resolve().parents[2]
        (root / "active-alpha-paper-monitor/scripts/derivatives_shadow_promotion_evaluator.py").write_text((real_repo / "active-alpha-paper-monitor/scripts/derivatives_shadow_promotion_evaluator.py").read_text(encoding="utf-8"), encoding="utf-8")
        (root / "active-alpha-paper-monitor/config/derivatives_shadow_promotion_gate_v1.json").write_text((real_repo / "active-alpha-paper-monitor/config/derivatives_shadow_promotion_gate_v1.json").read_text(encoding="utf-8"), encoding="utf-8")
        for goal in ("unified-short-term-opportunity-engine-v1", "crypto-dynamic-spot-eligibility-failover-v15", "universal-investment-core-github-canonical-v3", "unified-shortterm-derivatives-shadow-v2", "derivatives-shadow-continuous-sampling-v1"):
            digest = hashlib.sha256(goal.encode()).hexdigest()
            verifier_id = f"verify-{goal}"
            (root / f".codex/governance/releases/{goal}.json").write_text(json.dumps({"schema_version": "ReleaseEvidenceV1", "id": f"release-{goal}", "goal_id": goal, "achieved_level": "RUNTIME_VERIFIED", "blocked": False, "source_digest": digest, "independent_verification_id": verifier_id, "created_by": "owner", "created_at": "2026-08-02T00:00:00Z"}), encoding="utf-8")
            (root / f".codex/governance/verification/{goal}.json").write_text(json.dumps({"schema_version": "VerificationResultV1", "id": verifier_id, "goal_id": goal, "level": "RUNTIME_VERIFIED", "source_digest": digest, "verifier_thread_id": "independent", "result": "PASS"}), encoding="utf-8")
        observation = {"schema_version": "DerivativesShadowObservationV1", "observation_id": "obs-1", "snapshot_id": "snap-1", "directional_state": "OI_ONLY_CONFLICT"}
        observations = root / "active-alpha-paper-monitor/runtime/derivatives-shadow-v2-observations.jsonl"
        observations.write_text(json.dumps(observation) + "\n", encoding="utf-8")
        automation = root / "automation.toml"
        automation.write_text('kind="heartbeat"\nstatus="ACTIVE"\nrrule="FREQ=DAILY;BYHOUR=0,4,8,12,16,20;BYMINUTE=15;BYSECOND=0;UNTIL=20260823T161500Z"\nprompt="derivatives_shadow_cycle.py derivatives_shadow_outcome_reviewer.py derivatives_shadow_promotion_evaluator.py 2026-08-16T04:15:00Z 不要启动阶段二 不要自动下单 不要把影子结果计入 Paper/真钱 ROI 或 BUSINESS_READY"\n', encoding="utf-8")
        result = build_readiness(root, now=dt.datetime(2026, 8, 2, tzinfo=dt.timezone.utc), automation_path=automation)
        assert result["system_delivery_status"] == "RUNTIME_VERIFIED"
        assert result["phase1_status"] == "EVIDENCE_COLLECTING"
        assert result["live_profit_status"] == "UNMEASURED"
        assert result["unique_blocker"] == "phase1_forward_outcomes_and_gate_pending"
        assert result["unique_blocker"] in result["all_blockers"]
        assert all(result[key] is expected for key, expected in SAFETY.items())
    return {"schema_version": "UnifiedShortTermEngineReadinessSelfTestV1", "status": "PASS", "checks": ["authority_binding", "phase_isolation", "unmeasured_live_profit", "safe_output"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--automation", default="/Users/vincentpan/.codex/automations/automation/automation.toml")
    parser.add_argument("--observations")
    parser.add_argument("--outcomes")
    parser.add_argument("--live-profit-summary")
    parser.add_argument("--latest-cycle")
    parser.add_argument("--as-of")
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        payload = self_test()
    else:
        now = parse_time(args.as_of, "as_of") if args.as_of else dt.datetime.now(dt.timezone.utc)
        payload = build_readiness(
            Path(args.repo),
            now=now,
            automation_path=Path(args.automation).expanduser() if args.automation else None,
            observation_path=Path(args.observations).expanduser() if args.observations else None,
            outcome_path=Path(args.outcomes).expanduser() if args.outcomes else None,
            live_profit_path=Path(args.live_profit_summary).expanduser() if args.live_profit_summary else None,
            latest_cycle_path=Path(args.latest_cycle).expanduser() if args.latest_cycle else None,
        )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(output)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
