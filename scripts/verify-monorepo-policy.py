#!/usr/bin/env python3
"""Read-only policy checks for the investing monorepo."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ROOT = Path("/Users/vincentpan/Documents/investing")
EXPECTED_BRANCH = "codex/investing-monorepo-bootstrap"
MOBILE_ORIGINAL_HEAD = "1d169c15b8e3c23b56f0ec8336195863f1dbcb33"
BACKUP_EVIDENCE = ROOT / ".codex" / "governance" / "migration-backup.json"

FORBIDDEN_PARTS = {
    ".netlify",
    ".sports-run-state",
    ".sports_run_guard",
    "$CODEX_HOME",
    "automations",
    "cache",
    "coverage",
    "data",
    "dist",
    "experiments",
    "handoffs",
    "node_modules",
    "outputs",
    "paper_trades",
    "performance",
    "recommendations",
    "reports",
    "runtime",
    "state",
    "subagent_outputs",
    "subagent_tasks",
    "tmp",
}
FORBIDDEN_SUFFIXES = {".db", ".log", ".sqlite", ".sqlite-shm", ".sqlite-wal"}
FORBIDDEN_NAMES = {
    "MAIN_INVESTMENT_SESSION_HANDOFF.md",
    "MAIN_INVESTMENT_SESSION_HANDOFF_2026-07-27.md",
    "crypto_portfolio.json",
    "current_position_overrides.json",
    "portfolio_accounts.json",
    "portfolio_ledger.json",
    "user_strategy_memory.json",
}
EXCLUDED_STATE_FILES = [
    "manual-investment-strategy-operator/config/current_position_overrides.json",
    "manual-investment-strategy-operator/config/user_strategy_memory.json",
    "unified-longterm-alpha-investor/config/crypto_portfolio.json",
    "unified-longterm-alpha-investor/config/portfolio_accounts.json",
    "unified-longterm-alpha-investor/config/portfolio_ledger.json",
]
SECRET_PATTERNS = [
    re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(rb"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    return result


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


def tracked_paths() -> list[str]:
    raw = git("ls-files", "-z").stdout
    return sorted(item.decode(errors="surrogateescape") for item in raw.split(b"\0") if item)


def verify_backup() -> dict:
    evidence = json.loads(BACKUP_EVIDENCE.read_text(encoding="utf-8"))
    bundle = Path(evidence["bundle_path"])
    workspace = Path(evidence["workspace_snapshot_path"])
    nested_git = Path(evidence["nested_git_backup_path"])
    require(bundle.is_file(), "mobile_bundle_missing")
    require(workspace.is_dir(), "mobile_workspace_snapshot_missing")
    require(nested_git.is_dir(), "mobile_nested_git_backup_missing")
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    require(digest == evidence["bundle_sha256"], "mobile_bundle_hash_mismatch")
    verify = subprocess.run(["git", "bundle", "verify", str(bundle)], capture_output=True)
    require(verify.returncode == 0, "mobile_bundle_invalid")
    inventory = Path(evidence["excluded_file_inventory_path"])
    require(inventory.is_file(), "excluded_inventory_missing")
    inventory_bytes = inventory.read_bytes()
    inventory_digest = hashlib.sha256(inventory_bytes).hexdigest()
    require(inventory_digest == evidence["excluded_file_inventory_sha256"], "excluded_inventory_hash_mismatch")
    inventory_text = inventory_bytes.decode(encoding="utf-8")
    inventory_lines = inventory_text.splitlines()
    require(len(inventory_lines) == evidence["excluded_file_count"], "excluded_inventory_count_mismatch")
    for relative in EXCLUDED_STATE_FILES:
        full_path = ROOT / relative
        require(full_path.is_file(), f"excluded_state_file_missing:{relative}")
        require(str(full_path) in inventory_text, f"excluded_state_not_in_inventory:{relative}")
    return {
        "bundle_sha256": digest,
        "workspace_snapshot_present": True,
        "excluded_inventory_sha256": inventory_digest,
        "excluded_file_count": len(inventory_lines),
        "required_state_files_covered": len(EXCLUDED_STATE_FILES),
    }


def main() -> int:
    actual_root = Path(git("rev-parse", "--show-toplevel").stdout.decode().strip()).resolve()
    branch = git("branch", "--show-current").stdout.decode().strip()
    require(actual_root == EXPECTED_ROOT, "unexpected_repository_root")
    require(branch == EXPECTED_BRANCH, "unexpected_repository_branch")
    require(not (ROOT / "mobile-investment-console" / ".git").exists(), "nested_mobile_git_present")
    ancestor = git("merge-base", "--is-ancestor", MOBILE_ORIGINAL_HEAD, "HEAD", check=False)
    require(ancestor.returncode == 0, "mobile_history_not_preserved")

    paths = tracked_paths()
    require(paths, "tracked_source_required")
    violations: list[str] = []
    secret_hits: list[str] = []
    for relative in paths:
        path = Path(relative)
        if any(part in FORBIDDEN_PARTS for part in path.parts):
            violations.append(relative)
            continue
        if any(relative.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
            violations.append(relative)
            continue
        if path.name in FORBIDDEN_NAMES:
            violations.append(relative)
            continue
        if path.name == ".env" or path.name.startswith(".env."):
            violations.append(relative)
            continue
        full_path = ROOT / path
        if not full_path.is_file():
            continue
        content = full_path.read_bytes()
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            secret_hits.append(relative)

    require(not violations, f"forbidden_tracked_paths:{','.join(violations[:10])}")
    require(not secret_hits, f"secret_shaped_content:{','.join(secret_hits[:10])}")
    backup = verify_backup()
    print(json.dumps({
        "schema_version": "InvestingMonorepoPolicyCheckV1",
        "result": "PASS",
        "root": str(actual_root),
        "branch": branch,
        "tracked_file_count": len(paths),
        "original_mobile_head_is_ancestor": True,
        "nested_git_present": False,
        "forbidden_tracked_path_count": 0,
        "secret_shaped_file_count": 0,
        "backup": backup,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({
            "schema_version": "InvestingMonorepoPolicyCheckV1",
            "result": "FAIL",
            "error": str(exc),
        }, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
