#!/usr/bin/env python3
"""Synchronize canonical workspace investment skill sources to Codex skills.

V3 uses exact source manifests. Generated reports, experiments, caches,
handoffs and paper runtime data are never copied into the installed package.
Any managed installed source that is absent from the workspace is moved to a
recoverable temporary backup before verification.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INSTALLED_ROOT = Path.home() / ".codex" / "skills"

MANUAL_SKILL = "manual-investment-strategy-operator"
ACTIVE_SKILL = "active-alpha-paper-monitor"
UNIFIED_SKILL = "unified-longterm-alpha-investor"
GOVERNOR_SKILL = "closed-loop-delivery-governor"

SKILL_SPECS: dict[str, dict[str, tuple[str, ...]]] = {
    MANUAL_SKILL: {
        "directories": (
            "references",
            "scripts",
            "config",
            "examples",
            "evidence_templates",
            "import_templates",
            "tests",
        ),
        "files": ("SKILL.md",),
    },
    ACTIVE_SKILL: {
        "directories": ("references", "scripts", "config"),
        "files": ("SKILL.md",),
    },
    UNIFIED_SKILL: {
        "directories": ("references", "config"),
        "files": ("SKILL.md",),
    },
    GOVERNOR_SKILL: {
        "directories": ("agents", "references", "scripts"),
        "files": ("SKILL.md",),
    },
}

RUNTIME_PARTS = {
    "reports",
    "experiments",
    "handoffs",
    "cache",
    "paper_trades",
    "performance",
    "recommendations",
    "subagent_outputs",
    "subagent_tasks",
    "__pycache__",
    ".pytest_cache",
    ".git",
}

IGNORED_FILENAMES = {".DS_Store"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_digest(files: dict[str, str]) -> str:
    payload = json.dumps(files, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def is_runtime_or_ignored(path: Path) -> bool:
    return path.name in IGNORED_FILENAMES or any(
        part in RUNTIME_PARTS for part in path.parts
    )


def iter_managed_relative_files(
    skill_root: Path, spec: dict[str, tuple[str, ...]]
) -> Iterable[Path]:
    seen: set[Path] = set()
    for rel_text in spec["files"]:
        rel = Path(rel_text)
        if rel not in seen:
            seen.add(rel)
            yield rel
    for directory_text in spec["directories"]:
        directory = skill_root / directory_text
        if not directory.exists():
            continue
        for path in sorted(p for p in directory.rglob("*") if p.is_file()):
            rel = path.relative_to(skill_root)
            if is_runtime_or_ignored(rel) or rel in seen:
                continue
            seen.add(rel)
            yield rel


def build_skill_manifest(
    root: Path, skill_name: str, *, require_declared: bool
) -> dict[str, str]:
    skill_root = root / skill_name
    spec = SKILL_SPECS[skill_name]
    files: dict[str, str] = {}
    for rel in iter_managed_relative_files(skill_root, spec):
        path = skill_root / rel
        key = f"{skill_name}/{rel.as_posix()}"
        if not path.exists():
            if require_declared:
                files[key] = "__MISSING__"
            continue
        if not path.is_file():
            if require_declared:
                files[key] = "__NOT_A_FILE__"
            continue
        files[key] = sha256(path)
    return dict(sorted(files.items()))


def build_full_manifest(root: Path, *, require_declared: bool) -> dict[str, str]:
    files: dict[str, str] = {}
    for skill_name in SKILL_SPECS:
        files.update(
            build_skill_manifest(
                root, skill_name, require_declared=require_declared
            )
        )
    return dict(sorted(files.items()))


def compare_manifests(
    workspace_manifest: dict[str, str], installed_manifest: dict[str, str]
) -> dict[str, list[str]]:
    workspace_keys = set(workspace_manifest)
    installed_keys = set(installed_manifest)
    return {
        "missing_installed": sorted(workspace_keys - installed_keys),
        "extra_installed": sorted(installed_keys - workspace_keys),
        "hash_mismatches": sorted(
            key
            for key in workspace_keys & installed_keys
            if workspace_manifest[key] != installed_manifest[key]
        ),
    }


def validate_skill_file(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"missing_skill_file:{path}"]
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        errors.append(f"invalid_frontmatter_start:{path}")
        return errors
    pieces = text.split("---", 2)
    if len(pieces) < 3:
        errors.append(f"invalid_frontmatter:{path}")
        return errors
    keys = {
        line.split(":", 1)[0].strip()
        for line in pieces[1].splitlines()
        if ":" in line
    }
    if keys != {"name", "description"}:
        errors.append(f"invalid_frontmatter_keys:{path}:{sorted(keys)}")
    if len(text.splitlines()) > 500:
        errors.append(f"skill_too_long:{path}:{len(text.splitlines())}")
    return errors


def validate_json_files(root: Path, manifest: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for rel_text, digest in manifest.items():
        if not rel_text.endswith(".json") or digest.startswith("__"):
            continue
        path = root / rel_text
        try:
            with path.open("r", encoding="utf-8") as handle:
                json.load(handle)
        except Exception as exc:  # noqa: BLE001 - diagnostic boundary
            errors.append(f"invalid_json:{rel_text}:{exc}")
    return errors


def validate_tree(root: Path, manifest: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for skill_name in SKILL_SPECS:
        errors.extend(validate_skill_file(root / skill_name / "SKILL.md"))
    errors.extend(
        f"missing_or_invalid:{path}"
        for path, digest in manifest.items()
        if digest.startswith("__")
    )
    errors.extend(validate_json_files(root, manifest))
    return errors


def copy_workspace_manifest(
    workspace_root: Path,
    installed_root: Path,
    workspace_manifest: dict[str, str],
) -> list[str]:
    copied: list[str] = []
    for rel_text in workspace_manifest:
        source = workspace_root / rel_text
        target = installed_root / rel_text
        if (
            target.exists()
            and target.is_file()
            and target.stat().st_size == source.stat().st_size
            and sha256(target) == workspace_manifest[rel_text]
        ):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(rel_text)
    return copied


def move_managed_extras_to_backup(
    installed_root: Path, extras: list[str]
) -> tuple[str | None, list[str]]:
    if not extras:
        return None, []
    backup_root = Path(tempfile.mkdtemp(prefix="investment-skill-sync-backup-"))
    moved: list[str] = []
    for rel_text in extras:
        source = installed_root / rel_text
        if not source.exists() or not source.is_file():
            continue
        target = backup_root / rel_text
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        moved.append(rel_text)
    return str(backup_root), moved


def run_command(
    command: list[str], *, timeout: int = 120, capture_full: bool = False
) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        result = {
            "command": command,
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-4000:],
            "passed": proc.returncode == 0,
        }
        if capture_full:
            result["_stdout_full"] = proc.stdout
            result["_stderr_full"] = proc.stderr
        return result
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returncode": None,
            "stdout_tail": (exc.stdout or "")[-4000:]
            if isinstance(exc.stdout, str)
            else "",
            "stderr_tail": (exc.stderr or "")[-4000:]
            if isinstance(exc.stderr, str)
            else "",
            "passed": False,
            "error": "timeout",
        }


def critical_summary_failures(summary: dict[str, Any]) -> list[str]:
    def failed_status(value: Any) -> bool:
        normalized = str(value or "").strip().lower()
        return normalized in {"failed", "blocked", "error"} or normalized.startswith(
            ("failed_", "blocked_", "error_")
        )

    failures: list[str] = []
    if summary.get("status") != "ok":
        failures.append(f"summary_status:{summary.get('status')}")
    if summary.get("smoke_mode") is not True:
        failures.append("summary_not_smoke_mode")
    schema_status = (summary.get("schema_baseline_summary") or {}).get("status")
    if schema_status in {None, "schema_baseline_blocked"}:
        failures.append(f"schema_status:{schema_status}")
    for step in summary.get("steps") or []:
        status = step.get("status")
        if failed_status(status):
            failures.append(f"step:{step.get('name')}:{status}")
    report = summary.get("report_summary") or {}
    for key in (
        "report_integrity_audit",
        "objective_coverage_audit",
        "recommendation_execution_calendar_gate",
        "recommendation_history_write",
        "fresh_market_intelligence",
    ):
        value = report.get(key)
        if isinstance(value, dict) and failed_status(value.get("status")):
            failures.append(f"report:{key}:{value.get('status')}")
    return failures


def run_legacy_dispatch_smoke(
    installed_root: Path, run_date: str, monthly_dca: float
) -> dict[str, Any]:
    manual_root = installed_root / MANUAL_SKILL
    run_stamp = run_date.replace("-", "")
    run_id = f"{run_stamp}-v3-sync-smoke"
    summary_json = Path("/private/tmp") / f"{run_id}.json"
    summary_md = Path("/private/tmp") / f"{run_id}.md"
    snapshot_path = Path("/private/tmp") / f"current_portfolio_snapshot_{run_stamp}.json"
    goal_path = Path("/private/tmp") / f"current_goal_projection_{run_stamp}.json"

    snapshot = run_command(
        [
            sys.executable,
            str(manual_root / "scripts" / "portfolio_comparison_snapshot.py"),
        ],
        timeout=60,
        capture_full=True,
    )
    if snapshot["passed"]:
        snapshot_path.write_text(
            snapshot.pop("_stdout_full"), encoding="utf-8"
        )
        snapshot.pop("_stderr_full", None)
    else:
        return {"passed": False, "stage": "portfolio_snapshot", **snapshot}

    goal = run_command(
        [
            sys.executable,
            str(manual_root / "scripts" / "goal_path_projection.py"),
            "--snapshot-json",
            str(snapshot_path),
            "--monthly-dca",
            str(monthly_dca),
            "--format",
            "json",
        ],
        timeout=60,
        capture_full=True,
    )
    if goal["passed"]:
        goal_path.write_text(goal.pop("_stdout_full"), encoding="utf-8")
        goal.pop("_stderr_full", None)
    else:
        return {"passed": False, "stage": "goal_projection", **goal}

    dispatch = run_command(
        [
            sys.executable,
            str(manual_root / "scripts" / "manual_dispatch_run.py"),
            "--smoke",
            "--date",
            run_date,
            "--run-id",
            run_id,
            "--skip-active-scanners",
            "--skip-validation-pulse",
            "--skip-learning-review-calendar",
            "--skip-report-audits",
            "--skip-system-audit",
            "--summary-json",
            str(summary_json),
            "--summary-md",
            str(summary_md),
            "--timeout-seconds",
            "30",
            "--format",
            "json",
        ],
        timeout=120,
    )
    failures: list[str] = []
    summary: dict[str, Any] | None = None
    if not dispatch["passed"]:
        failures.append(f"dispatch_returncode:{dispatch.get('returncode')}")
    if not summary_json.exists():
        failures.append("summary_missing")
    else:
        try:
            summary = json.loads(summary_json.read_text(encoding="utf-8"))
            if not isinstance(summary, dict):
                failures.append("summary_not_object")
            else:
                failures.extend(critical_summary_failures(summary))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"summary_invalid_json:{exc}")
    return {
        "passed": not failures,
        "failures": failures,
        "dispatch": dispatch,
        "summary_json": str(summary_json),
        "summary": summary,
    }


def run_v3_dispatch_smoke(installed_root: Path) -> dict[str, Any]:
    script = (
        installed_root
        / MANUAL_SKILL
        / "scripts"
        / "v3_manual_dispatch.py"
    )
    positive = run_command(
        [sys.executable, str(script), "--self-test"], timeout=60
    )
    negative = run_command(
        [sys.executable, str(script), "--self-test", "--negative"], timeout=60
    )
    failures: list[str] = []
    if not positive["passed"]:
        failures.append("positive_self_test_failed")
    try:
        positive_audit = json.loads(positive["stdout_tail"])
    except Exception as exc:  # noqa: BLE001
        positive_audit = None
        failures.append(f"positive_output_invalid:{exc}")
    else:
        if positive_audit.get("passed") is not True:
            failures.append("positive_inner_audit_failed")
    if negative.get("returncode") == 0:
        failures.append("negative_self_test_false_green")
    try:
        negative_audit = json.loads(negative["stdout_tail"])
    except Exception as exc:  # noqa: BLE001
        negative_audit = None
        failures.append(f"negative_output_invalid:{exc}")
    else:
        if negative_audit.get("passed") is not False:
            failures.append("negative_inner_audit_not_failed")
    return {
        "passed": not failures,
        "failures": failures,
        "positive": positive,
        "positive_audit": positive_audit,
        "negative": negative,
        "negative_audit": negative_audit,
    }


def run_smoke(
    installed_root: Path, run_date: str, monthly_dca: float
) -> dict[str, Any]:
    manual_root = installed_root / MANUAL_SKILL
    tests = run_command(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(manual_root / "tests"),
            "-v",
        ],
        timeout=180,
    )
    calendar = run_command(
        [
            sys.executable,
            str(
                manual_root
                / "scripts"
                / "recommendation_execution_calendar_gate.py"
            ),
            "--self-test",
        ],
        timeout=60,
    )
    v3_dispatch = run_v3_dispatch_smoke(installed_root)
    failures: list[str] = []
    if not tests["passed"]:
        failures.append("unittest_failed")
    if not calendar["passed"]:
        failures.append("calendar_self_test_failed")
    if not v3_dispatch["passed"]:
        failures.extend(
            f"v3_dispatch:{item}" for item in v3_dispatch["failures"]
        )
    return {
        "passed": not failures,
        "failures": failures,
        "unittest": tests,
        "calendar_self_test": calendar,
        "v3_dispatch": v3_dispatch,
        "legacy_dispatch": {
            "status": "retired_from_release_gate",
            "reason": "V3 is the formal write path; legacy remains read-only fallback.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync workspace investment skills using an exact manifest."
    )
    parser.add_argument("--installed-root", default=str(DEFAULT_INSTALLED_ROOT))
    parser.add_argument("--workspace-root", default=str(WORKSPACE_ROOT))
    parser.add_argument(
        "--check", action="store_true", help="Compare only; never write."
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Compatibility flag; V3 always checks the full managed manifest.",
    )
    parser.add_argument(
        "--hash-check",
        action="store_true",
        help="Compatibility flag; V3 always hashes the full managed manifest.",
    )
    parser.add_argument("--with-smoke", action="store_true")
    parser.add_argument("--date", default=dt.datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--monthly-dca", type=float, default=1000.0)
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).expanduser().resolve()
    installed_root = Path(args.installed_root).expanduser().resolve()
    workspace_manifest = build_full_manifest(
        workspace_root, require_declared=True
    )
    workspace_errors = validate_tree(workspace_root, workspace_manifest)

    result: dict[str, Any] = {
        "schema_version": "investment-skill-sync-v3",
        "generated_at": utc_now(),
        "workspace_root": str(workspace_root),
        "installed_root": str(installed_root),
        "check_only": args.check,
        "workspace_manifest_file_count": len(workspace_manifest),
        "workspace_manifest_sha256": manifest_digest(workspace_manifest),
        "workspace_validation_errors": workspace_errors,
        "copied": [],
        "managed_extras_backup_root": None,
        "managed_extras_moved": [],
        "manifest_diff": {},
        "installed_validation_errors": [],
        "smoke": None,
        "passed": False,
    }

    if workspace_errors:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    installed_manifest_before = build_full_manifest(
        installed_root, require_declared=False
    )
    before_diff = compare_manifests(
        workspace_manifest, installed_manifest_before
    )

    if not args.check:
        result["copied"] = copy_workspace_manifest(
            workspace_root, installed_root, workspace_manifest
        )
        backup_root, moved = move_managed_extras_to_backup(
            installed_root, before_diff["extra_installed"]
        )
        result["managed_extras_backup_root"] = backup_root
        result["managed_extras_moved"] = moved

    installed_manifest = build_full_manifest(
        installed_root, require_declared=False
    )
    diff = compare_manifests(workspace_manifest, installed_manifest)
    installed_errors = validate_tree(installed_root, installed_manifest)
    result["installed_manifest_file_count"] = len(installed_manifest)
    result["installed_manifest_sha256"] = manifest_digest(installed_manifest)
    result["manifest_diff"] = diff
    result["installed_validation_errors"] = installed_errors

    manifests_equal = not any(diff.values())
    validation_ok = not installed_errors
    if args.with_smoke and manifests_equal and validation_ok:
        result["smoke"] = run_smoke(
            installed_root, args.date, args.monthly_dca
        )

    smoke_ok = not args.with_smoke or bool(
        result["smoke"] and result["smoke"].get("passed")
    )
    result["passed"] = (
        not workspace_errors and manifests_equal and validation_ok and smoke_ok
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
