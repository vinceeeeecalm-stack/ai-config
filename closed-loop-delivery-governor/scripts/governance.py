#!/usr/bin/env python3
"""Deterministic closed-loop delivery governance using only the Python stdlib."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone


LEVELS = ["DESIGNED", "CODED", "TESTED", "RUNTIME_VERIFIED", "BUSINESS_READY"]
DEFAULT_EXCLUDES = [
    ".git/**",
    ".codex/governance/**",
    "node_modules/**",
    "dist/**",
    ".next/**",
    "coverage/**",
    "**/__pycache__/**",
]
SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


class GovernanceError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def thread_id() -> str:
    return os.environ.get("CODEX_THREAD_ID") or "manual-unidentified"


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def content_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GovernanceError(f"missing_file:{path}") from exc
    except json.JSONDecodeError as exc:
        raise GovernanceError(f"invalid_json:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise GovernanceError(f"json_object_required:{path}")
    return value


def atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def append_jsonl(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def sanitize(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        result = value
        for pattern in SECRET_PATTERNS:
            result = pattern.sub("[REDACTED]", result)
        return result
    return value


def state_dir(root: Path) -> Path:
    return root / ".codex" / "governance"


def find_root(start: Path, explicit: str | None = None, allow_uninitialized: bool = False) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (state_dir(candidate) / "project.json").exists():
            return candidate
    if allow_uninitialized:
        return current
    raise GovernanceError("governance_not_initialized")


def git_output(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise GovernanceError(f"git_failed:{' '.join(args)}:{result.stderr.strip()}")
    return result.stdout.strip()


def dedicated_git_root(root: Path) -> Path | None:
    value = git_output(root, "rev-parse", "--show-toplevel", check=False)
    if not value:
        return None
    candidate = Path(value).resolve()
    return candidate if candidate == root.resolve() else None


def git_common_dir(root: Path) -> Path | None:
    if not dedicated_git_root(root):
        return None
    value = git_output(root, "rev-parse", "--git-common-dir", check=False)
    if not value:
        return None
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def relative_excluded(relative: str, excludes: list[str]) -> bool:
    normalized = relative.replace(os.sep, "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in excludes)


def hash_file(hasher: "hashlib._Hash", path: Path, relative: str) -> None:
    hasher.update(relative.encode())
    hasher.update(b"\0")
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    hasher.update(b"\0")


def source_files(root: Path, config: dict) -> list[Path]:
    excludes = [*DEFAULT_EXCLUDES, *config.get("digest_excludes", [])]
    git_root = dedicated_git_root(root)
    paths: list[Path] = []
    if git_root:
        raw = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True,
            check=True,
        ).stdout
        for item in raw.split(b"\0"):
            if not item:
                continue
            relative = item.decode("utf-8", errors="surrogateescape")
            path = root / relative
            if path.is_file() and not relative_excluded(relative, excludes):
                paths.append(path)
    else:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if not relative_excluded(relative, excludes):
                paths.append(path)
    return sorted(set(paths), key=lambda path: path.relative_to(root).as_posix())


def source_digest(root: Path, config: dict) -> str:
    hasher = hashlib.sha256()
    for path in source_files(root, config):
        hash_file(hasher, path, path.relative_to(root).as_posix())
    governance = state_dir(root)
    contract_paths = [governance / "project.json", governance / "data-authority.json"]
    current_path = governance / "current.json"
    if current_path.exists():
        current = load_json(current_path)
        goal_path = governance / "goals" / f"{current['goal_id']}.json"
        contract_paths.append(goal_path)
        if goal_path.exists():
            goal = load_json(goal_path)
            contract_paths.extend(
                governance / "acceptance" / f"{scenario_id}.json"
                for scenario_id in goal.get("acceptance_scenario_ids", [])
            )
    else:
        contract_paths.extend(sorted((governance / "acceptance").glob("*.json")))
    for path in sorted(set(contract_paths), key=lambda item: item.as_posix()):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            hash_file(hasher, path, relative)
    return hasher.hexdigest()


def git_state(root: Path) -> dict:
    if not dedicated_git_root(root):
        return {"available": False, "commit": None, "branch": None, "dirty": None}
    status = git_output(root, "status", "--porcelain")
    return {
        "available": True,
        "commit": git_output(root, "rev-parse", "HEAD", check=False) or None,
        "branch": git_output(root, "branch", "--show-current", check=False) or None,
        "dirty": bool(status),
    }


def lock_path(root: Path) -> Path:
    git_root = dedicated_git_root(root)
    key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:20]
    if git_root:
        common = git_output(root, "rev-parse", "--git-common-dir")
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = (root / common_path).resolve()
        return common_path / "codex-governance-locks" / f"{key}.json"
    return state_dir(root) / "locks" / f"{key}.json"


def append_event(root: Path, event_type: str, payload: dict) -> None:
    append_jsonl(
        state_dir(root) / "events.jsonl",
        sanitize(
            {
                "schema_version": "GovernanceEventV1",
                "event_type": event_type,
                "created_at": now_iso(),
                "thread_id": thread_id(),
                **payload,
            }
        ),
    )


def require_schema(value: dict, expected: str, path: str) -> None:
    if value.get("schema_version") != expected:
        raise GovernanceError(f"schema_mismatch:{path}:{expected}")


def project_config(root: Path) -> dict:
    value = load_json(state_dir(root) / "project.json")
    require_schema(value, "ProjectGovernanceV1", "project.json")
    canonical = Path(value.get("canonical_root", "")).resolve()
    if canonical != root.resolve():
        canonical_common = git_common_dir(canonical)
        current_common = git_common_dir(root)
        if not canonical_common or canonical_common != current_common:
            raise GovernanceError("canonical_root_mismatch")
    return value


def current_goal(root: Path) -> dict:
    current = load_json(state_dir(root) / "current.json")
    return load_json(state_dir(root) / "goals" / f"{current['goal_id']}.json")


def validate_acceptance(root: Path, goal: dict) -> None:
    scenario_ids = goal.get("acceptance_scenario_ids")
    if not isinstance(scenario_ids, list) or not scenario_ids:
        raise GovernanceError("acceptance_scenarios_required")
    for scenario_id in scenario_ids:
        path = state_dir(root) / "acceptance" / f"{scenario_id}.json"
        scenario = load_json(path)
        require_schema(scenario, "AcceptanceScenarioV1", str(path))
        if scenario.get("id") != scenario_id:
            raise GovernanceError(f"acceptance_id_mismatch:{scenario_id}")


def lock_owned(root: Path) -> dict:
    path = lock_path(root)
    lock = load_json(path)
    require_schema(lock, "ActiveChangeLockV1", str(path))
    if lock.get("owner_thread_id") != thread_id():
        raise GovernanceError(f"worktree_locked_by:{lock.get('owner_thread_id')}")
    return lock


def command_result(command: str, root: Path, timeout: int) -> dict:
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=root,
        shell=True,
        capture_output=True,
        text=False,
        timeout=timeout,
        check=False,
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    return {
        "command": command,
        "exit_code": result.returncode,
        "duration_ms": duration_ms,
        "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
    }


def verification_path(root: Path, level: str) -> Path:
    goal = current_goal(root)
    return state_dir(root) / "verification" / f"{goal['id']}-{level.lower()}.json"


def load_status(root: Path) -> dict:
    path = state_dir(root) / "status.json"
    if not path.exists():
        return {
            "schema_version": "GovernanceStatusV1",
            "highest_level": None,
            "source_digest": None,
            "blocked": False,
            "blockers": [],
        }
    return load_json(path)


def level_at_least(actual: str | None, required: str) -> bool:
    return actual in LEVELS and LEVELS.index(actual) >= LEVELS.index(required)


def open_deviations(root: Path) -> list[dict]:
    path = state_dir(root) / "deviations.jsonl"
    if not path.exists():
        return []
    latest: dict[str, dict] = {}
    superseded: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GovernanceError(f"invalid_deviation_jsonl:line_{number}") from exc
        latest[row["id"]] = row
        if row.get("supersedes_id"):
            superseded.add(row["supersedes_id"])
    return [row for key, row in latest.items() if key not in superseded and row.get("status") != "closed"]


def cmd_init(args: argparse.Namespace) -> dict:
    root = find_root(Path.cwd(), args.project_root, allow_uninitialized=True)
    governance = state_dir(root)
    governance.mkdir(parents=True, exist_ok=True)
    for name in ["goals", "acceptance", "verification", "releases", "locks"]:
        (governance / name).mkdir(exist_ok=True)

    project_path = governance / "project.json"
    if project_path.exists() and not args.allow_existing:
        raise GovernanceError("governance_already_initialized")
    if not project_path.exists():
        atomic_write_json(
            project_path,
            {
                "schema_version": "ProjectGovernanceV1",
                "project_id": args.project_id,
                "name": args.name,
                "canonical_root": str(root),
                "default_risk": args.risk,
                "test_commands": args.test_command or [],
                "runtime_checks": args.runtime_check or [],
                "command_timeout_seconds": args.command_timeout,
                "digest_excludes": args.digest_exclude or [],
                "business_ready_requires_external_inputs": args.external_inputs_required,
                "created_at": now_iso(),
            },
        )

    authority_path = governance / "data-authority.json"
    if not authority_path.exists():
        atomic_write_json(
            authority_path,
            {"schema_version": "DataAuthorityV1", "authorities": []},
        )

    agents_path = root / "AGENTS.md"
    marker = "## Closed-loop delivery governance"
    block = (
        f"\n{marker}\n\n"
        "Material changes to products, data pipelines, automations, APIs, persistence, services, "
        "releases, or business workflows must use the `closed-loop-delivery-governor` Skill.\n\n"
        "- Read `.codex/governance/project.json` and `data-authority.json` before editing.\n"
        "- Acquire the governance lock before mutation; one writer per worktree.\n"
        "- Freeze a goal and acceptance scenarios before implementation.\n"
        "- Record user-reported gaps as append-only deviations before fixing them.\n"
        "- Never claim a level above the result returned by the governance gate.\n"
    )
    existing = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    if marker not in existing:
        agents_path.write_text(existing.rstrip() + block + "\n", encoding="utf-8")

    append_event(root, "governance_initialized", {"project_id": args.project_id})
    return {"status": "initialized", "project_root": str(root), "governance_dir": str(governance)}


def cmd_begin(args: argparse.Namespace) -> dict:
    root = find_root(Path.cwd(), args.project_root)
    config = project_config(root)
    goal_source = load_json(Path(args.goal_file).expanduser().resolve())
    require_schema(goal_source, "GoalContractV1", args.goal_file)
    required = ["id", "title", "business_outcome", "scope", "non_goals", "constraints", "acceptance_scenario_ids", "required_level", "risk"]
    missing = [key for key in required if key not in goal_source]
    if missing:
        raise GovernanceError(f"goal_fields_missing:{','.join(missing)}")
    if goal_source["required_level"] not in LEVELS or goal_source["risk"] not in {"low", "medium", "high"}:
        raise GovernanceError("invalid_goal_level_or_risk")
    validate_acceptance(root, goal_source)

    path = lock_path(root)
    if path.exists():
        lock = load_json(path)
        if lock.get("owner_thread_id") != thread_id():
            raise GovernanceError(f"worktree_locked_by:{lock.get('owner_thread_id')}")

    immutable = dict(goal_source)
    immutable_hash = content_hash(immutable)
    stored_path = state_dir(root) / "goals" / f"{goal_source['id']}.json"
    if stored_path.exists():
        stored = load_json(stored_path)
        if stored.get("content_hash") != immutable_hash:
            raise GovernanceError("frozen_goal_immutable_create_superseding_goal")
    else:
        frozen = {
            **immutable,
            "frozen_at": now_iso(),
            "frozen_by": thread_id(),
            "content_hash": immutable_hash,
        }
        atomic_write_json(stored_path, frozen)

    digest = source_digest(root, config)
    git = git_state(root)
    lock = {
        "schema_version": "ActiveChangeLockV1",
        "owner_thread_id": thread_id(),
        "worktree": str(root),
        "branch": git["branch"],
        "goal_id": goal_source["id"],
        "source_digest_at_begin": digest,
        "started_at": now_iso(),
        "heartbeat_at": now_iso(),
        "integration_owner_thread_id": args.integration_owner or thread_id(),
    }
    atomic_write_json(path, lock)
    atomic_write_json(
        state_dir(root) / "current.json",
        {"schema_version": "CurrentGovernanceGoalV1", "goal_id": goal_source["id"], "updated_at": now_iso()},
    )
    atomic_write_json(
        state_dir(root) / "status.json",
        {
            "schema_version": "GovernanceStatusV1",
            "goal_id": goal_source["id"],
            "highest_level": "DESIGNED",
            "source_digest": digest,
            "blocked": False,
            "blockers": [],
            "updated_at": now_iso(),
        },
    )
    append_event(root, "change_begun", {"goal_id": goal_source["id"], "source_digest": digest})
    return {"status": "DESIGNED", "goal_id": goal_source["id"], "lock_path": str(path), "source_digest": digest}


def cmd_status(args: argparse.Namespace) -> dict:
    root = find_root(Path.cwd(), args.project_root)
    config = project_config(root)
    status = load_status(root)
    digest = source_digest(root, config)
    effective = status.get("highest_level")
    stale = bool(status.get("source_digest") and status["source_digest"] != digest and effective != "DESIGNED")
    if stale:
        effective = "DESIGNED"
    lock = None
    path = lock_path(root)
    if path.exists():
        lock = load_json(path)
    return {
        **status,
        "effective_level": effective,
        "current_source_digest": digest,
        "verification_stale": stale,
        "lock": lock,
        "open_deviations": len(open_deviations(root)),
    }


def cmd_deviation_add(args: argparse.Namespace) -> dict:
    root = find_root(Path.cwd(), args.project_root)
    lock_owned(root)
    goal = current_goal(root)
    if args.record_file:
        record = load_json(Path(args.record_file).expanduser().resolve())
        require_schema(record, "DeviationRecordV1", args.record_file)
    else:
        record = {
            "schema_version": "DeviationRecordV1",
            "id": args.id or f"dev-{uuid.uuid4().hex[:12]}",
            "goal_id": goal["id"],
            "severity": args.severity,
            "expected": args.expected,
            "actual": args.actual,
            "reproduction": args.reproduction or [],
            "root_layer": args.root_layer,
            "acceptance_scenario_id": args.acceptance_scenario_id,
            "status": args.status,
            "supersedes_id": args.supersedes_id,
        }
    if record.get("goal_id") != goal["id"]:
        raise GovernanceError("deviation_goal_mismatch")
    if record.get("severity") not in {"P0", "P1", "P2", "P3"}:
        raise GovernanceError("invalid_deviation_severity")
    record = sanitize({**record, "created_at": now_iso(), "created_by": thread_id()})
    append_jsonl(state_dir(root) / "deviations.jsonl", record)
    append_event(root, "deviation_appended", {"deviation_id": record["id"], "severity": record["severity"]})
    return {"status": "recorded", "deviation_id": record["id"]}


def import_independent_result(
    root: Path,
    path_value: str,
    goal: dict,
    digest: str,
    expected_level: str,
) -> dict:
    source = load_json(Path(path_value).expanduser().resolve())
    require_schema(source, "VerificationResultV1", path_value)
    allowed = {
        key: source.get(key)
        for key in [
            "schema_version", "id", "goal_id", "level", "source_digest",
            "verifier_thread_id", "result", "checks", "findings", "created_at",
        ]
    }
    if allowed["goal_id"] != goal["id"] or allowed["source_digest"] != digest:
        raise GovernanceError("independent_verification_goal_or_digest_mismatch")
    if allowed["level"] != expected_level:
        raise GovernanceError("independent_verification_level_mismatch")
    if allowed["result"] != "PASS":
        raise GovernanceError("independent_verification_not_pass")
    if allowed["verifier_thread_id"] in {None, "", goal.get("frozen_by"), thread_id()}:
        raise GovernanceError("independent_verifier_must_differ")
    target = state_dir(root) / "verification" / f"{allowed['id']}.json"
    atomic_write_json(target, sanitize(allowed))
    return allowed


def cmd_verify(args: argparse.Namespace) -> dict:
    root = find_root(Path.cwd(), args.project_root)
    config = project_config(root)
    lock = lock_owned(root)
    goal = current_goal(root)
    level = args.level
    if level not in LEVELS[1:]:
        raise GovernanceError("verify_level_must_be_coded_or_higher")
    status = load_status(root)
    current_digest = source_digest(root, config)
    current_level = status.get("highest_level")

    prerequisite = LEVELS[LEVELS.index(level) - 1]
    if not level_at_least(current_level, prerequisite):
        raise GovernanceError(f"missing_prerequisite:{prerequisite}")
    if level not in {"CODED"} and status.get("source_digest") != current_digest:
        raise GovernanceError("source_changed_since_previous_gate_rerun_coded")

    results: list[dict] = []
    timeout = int(config.get("command_timeout_seconds", 600))
    commands: list[str] = []
    if level == "TESTED":
        commands = config.get("test_commands", [])
        if not commands:
            raise GovernanceError("test_commands_required")
    elif level == "RUNTIME_VERIFIED":
        commands = config.get("runtime_checks", [])
        if not commands:
            raise GovernanceError("runtime_checks_required")
    for command in commands:
        result = command_result(command, root, timeout)
        results.append(result)
        if result["exit_code"] != 0:
            raise GovernanceError(f"gate_command_failed:{level}:{command}")

    independent = None
    if args.independent_result:
        independent = import_independent_result(
            root,
            args.independent_result,
            goal,
            current_digest,
            level,
        )
    if level == "BUSINESS_READY":
        if args.blocker:
            raise GovernanceError("business_ready_cannot_have_blockers")
        severe = [row for row in open_deviations(root) if row.get("severity") in {"P0", "P1"}]
        if severe:
            raise GovernanceError(f"open_severe_deviations:{len(severe)}")
        if config.get("business_ready_requires_external_inputs") and not args.external_inputs_confirmed:
            raise GovernanceError("external_inputs_not_confirmed")
        if goal.get("risk") == "high":
            if independent is None:
                raise GovernanceError("independent_verification_required")

    verification_id = f"verify-{goal['id']}-{level.lower()}-{uuid.uuid4().hex[:8]}"
    record = {
        "schema_version": "VerificationResultV1",
        "id": verification_id,
        "goal_id": goal["id"],
        "level": level,
        "source_digest": current_digest,
        "implementation_owner_thread_id": lock["owner_thread_id"],
        "verifier_thread_id": independent.get("verifier_thread_id") if independent else thread_id(),
        "result": "PASS",
        "checks": results,
        "findings": [],
        "created_at": now_iso(),
    }
    atomic_write_json(verification_path(root, level), record)
    new_status = {
        "schema_version": "GovernanceStatusV1",
        "goal_id": goal["id"],
        "highest_level": level,
        "source_digest": current_digest,
        "blocked": bool(args.blocker),
        "blockers": args.blocker or [],
        "last_verification_id": verification_id,
        "independent_verification_id": independent.get("id") if independent else status.get("independent_verification_id"),
        "updated_at": now_iso(),
    }
    atomic_write_json(state_dir(root) / "status.json", new_status)
    append_event(root, "level_verified", {"goal_id": goal["id"], "level": level, "blocked": bool(args.blocker)})
    return new_status


def deviation_counts(rows: list[dict]) -> dict:
    return {severity: sum(1 for row in rows if row.get("severity") == severity) for severity in ["P0", "P1", "P2", "P3"]}


def cmd_release(args: argparse.Namespace) -> dict:
    root = find_root(Path.cwd(), args.project_root)
    config = project_config(root)
    lock_owned(root)
    goal = current_goal(root)
    status = load_status(root)
    digest = source_digest(root, config)
    if status.get("source_digest") != digest:
        raise GovernanceError("verification_stale_source_changed")
    if not status.get("highest_level"):
        raise GovernanceError("no_verified_level")
    release_id = args.release_id or f"release-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    rows = open_deviations(root)
    release = sanitize(
        {
            "schema_version": "ReleaseEvidenceV1",
            "id": release_id,
            "project_id": config["project_id"],
            "goal_id": goal["id"],
            "achieved_level": status["highest_level"],
            "blocked": status.get("blocked", False),
            "blockers": status.get("blockers", []),
            "git": git_state(root),
            "source_digest": digest,
            "build_version": args.build_version,
            "runtime_version": args.runtime_version,
            "database_version": args.database_version,
            "last_verification_id": status.get("last_verification_id"),
            "independent_verification_id": status.get("independent_verification_id"),
            "open_deviation_counts": deviation_counts(rows),
            "created_at": now_iso(),
            "created_by": thread_id(),
        }
    )
    atomic_write_json(state_dir(root) / "releases" / f"{release_id}.json", release)
    append_event(root, "release_evidence_created", {"release_id": release_id, "level": status["highest_level"]})
    return release


def cmd_close(args: argparse.Namespace) -> dict:
    root = find_root(Path.cwd(), args.project_root)
    lock = lock_owned(root)
    path = lock_path(root)
    path.unlink()
    append_event(root, "change_closed", {"goal_id": lock["goal_id"]})
    return {"status": "closed", "goal_id": lock["goal_id"]}


def cmd_takeover(args: argparse.Namespace) -> dict:
    root = find_root(Path.cwd(), args.project_root)
    project_config(root)
    path = lock_path(root)
    previous = load_json(path)
    if previous.get("owner_thread_id") == thread_id():
        raise GovernanceError("lock_already_owned")
    replacement = {
        **previous,
        "owner_thread_id": thread_id(),
        "heartbeat_at": now_iso(),
        "takeover_at": now_iso(),
        "takeover_reason": args.reason,
        "previous_owner_thread_id": previous.get("owner_thread_id"),
    }
    atomic_write_json(path, replacement)
    append_event(
        root,
        "lock_taken_over",
        {
            "goal_id": previous.get("goal_id"),
            "previous_owner_thread_id": previous.get("owner_thread_id"),
            "reason": sanitize(args.reason),
        },
    )
    return {"status": "taken_over", "previous_owner": previous.get("owner_thread_id"), "owner": thread_id()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="governance")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--project-root")
    init.add_argument("--project-id", required=True)
    init.add_argument("--name", required=True)
    init.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    init.add_argument("--test-command", action="append")
    init.add_argument("--runtime-check", action="append")
    init.add_argument("--digest-exclude", action="append")
    init.add_argument("--command-timeout", type=int, default=600)
    init.add_argument("--external-inputs-required", action="store_true")
    init.add_argument("--allow-existing", action="store_true")
    init.set_defaults(func=cmd_init)

    begin = sub.add_parser("begin")
    begin.add_argument("--project-root")
    begin.add_argument("--goal-file", required=True)
    begin.add_argument("--integration-owner")
    begin.set_defaults(func=cmd_begin)

    status = sub.add_parser("status")
    status.add_argument("--project-root")
    status.set_defaults(func=cmd_status)

    deviation = sub.add_parser("deviation")
    deviation_sub = deviation.add_subparsers(dest="deviation_command", required=True)
    add = deviation_sub.add_parser("add")
    add.add_argument("--project-root")
    add.add_argument("--record-file")
    add.add_argument("--id")
    add.add_argument("--severity", choices=["P0", "P1", "P2", "P3"], default="P2")
    add.add_argument("--expected")
    add.add_argument("--actual")
    add.add_argument("--reproduction", action="append")
    add.add_argument("--root-layer", default="unknown")
    add.add_argument("--acceptance-scenario-id")
    add.add_argument("--status", choices=["open", "closed"], default="open")
    add.add_argument("--supersedes-id")
    add.set_defaults(func=cmd_deviation_add)

    verify = sub.add_parser("verify")
    verify.add_argument("--project-root")
    verify.add_argument("--level", choices=LEVELS[1:], required=True)
    verify.add_argument("--blocker", action="append")
    verify.add_argument("--external-inputs-confirmed", action="store_true")
    verify.add_argument("--independent-result")
    verify.set_defaults(func=cmd_verify)

    release = sub.add_parser("release")
    release.add_argument("--project-root")
    release.add_argument("--release-id")
    release.add_argument("--build-version")
    release.add_argument("--runtime-version")
    release.add_argument("--database-version")
    release.set_defaults(func=cmd_release)

    close = sub.add_parser("close")
    close.add_argument("--project-root")
    close.set_defaults(func=cmd_close)

    takeover = sub.add_parser("takeover")
    takeover.add_argument("--project-root")
    takeover.add_argument("--reason", required=True)
    takeover.set_defaults(func=cmd_takeover)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.func(args)
    except (GovernanceError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
