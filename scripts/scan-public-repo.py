#!/usr/bin/env python3
"""Secret/privacy gate for content intended for the public canonical repository.

Findings never echo the matched value; only rule, path, line and a value hash are
reported.  The scanner covers current trackable files and every reachable Git blob.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Iterable


SECRET_RULES = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
    "openai_key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "credential_url": re.compile(r"https?://[^\s/:@]+:[^\s/@]+@[^\s]+"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{32,}={0,2}\b", re.IGNORECASE),
}

GENERIC_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|client[_-]?secret|password|private[_-]?key)\b"
    r"\s*[:=]\s*[\"']?([A-Za-z0-9._~+/-]{20,}={0,2})"
)

FORBIDDEN_PRIVACY_PATHS = [
    re.compile(r"(^|/)(runtime|reports?|paper_trades|cache|handoffs?|screenshots?|account_exports?)(/|$)", re.IGNORECASE),
    re.compile(r"(^|/)(portfolio_ledger|crypto_portfolio|portfolio_accounts|current_position_overrides|user_confirmed_account_states|execution_receipts|dca_contributions)\.jsonl?$", re.IGNORECASE),
    re.compile(r"(^|/)\.env(?:\.|$)", re.IGNORECASE),
]

TEXT_SUFFIXES = {
    ".c", ".cc", ".css", ".go", ".h", ".html", ".java", ".js", ".json",
    ".jsonl", ".jsx", ".md", ".mjs", ".py", ".rb", ".rs", ".sh", ".sql",
    ".swift", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}

# Exact synthetic fixture used by safety_invariant_auditor.py to prove leakage
# detection.  The value is never emitted here; only its SHA-256 is allowlisted.
SAFE_FIXTURES = {
    ("active-alpha-paper-monitor/scripts/safety_invariant_auditor.py", "high_entropy_credential_assignment", "a8ae6e6ee929abea3afcfc5258c8ccd6f85273e0d4626d26c7279f3250f77c8e"),
}


def run(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        list(args), cwd=root, input=input_bytes, check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def entropy(value: str) -> float:
    counts = {char: value.count(char) for char in set(value)}
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())


def placeholder(value: str) -> bool:
    lowered = value.casefold()
    markers = ("example", "placeholder", "dummy", "redacted", "changeme", "your_", "test_")
    return any(marker in lowered for marker in markers) or len(set(lowered)) <= 4


def text_findings(text: str, path: str, scope: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in SECRET_RULES.items():
            for match in pattern.finditer(line):
                value = match.group(0)
                findings.append({
                    "category": "secret", "rule": rule, "scope": scope, "path": path,
                    "line": line_number, "value_sha256": hashlib.sha256(value.encode()).hexdigest(),
                })
        for match in GENERIC_ASSIGNMENT.finditer(line):
            value = match.group(2)
            value_hash = hashlib.sha256(value.encode()).hexdigest()
            if (
                not placeholder(value)
                and entropy(value) >= 3.5
                and (path, "high_entropy_credential_assignment", value_hash) not in SAFE_FIXTURES
            ):
                findings.append({
                    "category": "secret", "rule": "high_entropy_credential_assignment", "scope": scope,
                    "path": path, "line": line_number,
                    "value_sha256": value_hash,
                })
    return findings


def trackable_paths(root: Path) -> list[str]:
    raw = run(root, "git", "ls-files", "-co", "--exclude-standard", "-z")
    return sorted(item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item)


def privacy_findings(paths: Iterable[str], scope: str) -> list[dict[str, object]]:
    findings = []
    for path in paths:
        if any(pattern.search(path) for pattern in FORBIDDEN_PRIVACY_PATHS):
            findings.append({"category": "privacy", "rule": "forbidden_private_path", "scope": scope, "path": path})
    return findings


def scan_worktree(root: Path) -> tuple[list[dict[str, object]], int]:
    paths = trackable_paths(root)
    findings = privacy_findings(paths, "worktree")
    for relative in paths:
        path = root / relative
        if not path.is_file() or (path.suffix.casefold() not in TEXT_SUFFIXES and path.name not in {"AGENTS.md", "SKILL.md", "README"}):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(text_findings(text, relative, "worktree"))
    return findings, len(paths)


def history_blobs(root: Path) -> list[tuple[str, str]]:
    rows = run(root, "git", "rev-list", "--objects", "--all").decode("utf-8", "replace").splitlines()
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        oid, _, path = row.partition(" ")
        if not path or oid in seen:
            continue
        seen.add(oid)
        try:
            kind = run(root, "git", "cat-file", "-t", oid).strip()
            size = int(run(root, "git", "cat-file", "-s", oid).strip())
        except (subprocess.CalledProcessError, ValueError):
            continue
        if kind == b"blob" and size <= 2_000_000:
            result.append((oid, path))
    return result


def scan_history(root: Path) -> tuple[list[dict[str, object]], int]:
    blobs = history_blobs(root)
    findings = privacy_findings((path for _, path in blobs), "git_history")
    for oid, path in blobs:
        if Path(path).suffix.casefold() not in TEXT_SUFFIXES and Path(path).name not in {"AGENTS.md", "SKILL.md", "README"}:
            continue
        try:
            content = run(root, "git", "cat-file", "-p", oid).decode("utf-8")
        except (subprocess.CalledProcessError, UnicodeDecodeError):
            continue
        findings.extend(text_findings(content, path, "git_history"))
    return findings, len(blobs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    worktree_findings, worktree_count = scan_worktree(root)
    history_findings, history_count = scan_history(root)
    unique = {
        json.dumps(item, ensure_ascii=False, sort_keys=True): item
        for item in worktree_findings + history_findings
    }
    findings = list(unique.values())
    report = {
        "schema_version": "PublicRepositorySafetyScanV1",
        "root": str(root),
        "trackable_file_count": worktree_count,
        "history_blob_count": history_count,
        "secret_finding_count": sum(item["category"] == "secret" for item in findings),
        "privacy_finding_count": sum(item["category"] == "privacy" for item in findings),
        "findings": findings,
        "result": "PASS" if not findings else "FAIL",
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).expanduser().resolve().write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
