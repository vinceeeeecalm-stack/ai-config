#!/usr/bin/env python3
"""Local safety invariant auditor for active-alpha-paper-monitor.

This auditor is offline and read-only. It scans the active skill and hourly
automation for hard safety regressions: live-order flags, private Binance
endpoints, signing/private account API markers, and suspicious API key leaks.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ACTIVE_ROOT = SCRIPT_DIR.parent
WORKSPACE_ROOT = ACTIVE_ROOT.parent
DEFAULT_AUTOMATION = Path("/Users/vincentpan/.codex/automations/active-alpha-hourly-crypto-paper-loop/automation.toml")

TEXT_SUFFIXES = {".py", ".json", ".md", ".toml", ".txt"}
EXCLUDED_DIRS = {"reports", "experiments", "paper_trades", "handoffs", "__pycache__"}

TRUE_FLAG_PATTERNS = {
    "live_orders_enabled_true": re.compile(r"""['"]live_orders_enabled['"]\s*[:=]\s*(?:true|True)\b"""),
    "allow_real_orders_true": re.compile(r"""['"]allow_real_orders['"]\s*[:=]\s*(?:true|True)\b"""),
    "private_api_used_true": re.compile(r"""['"]private_api_used['"]\s*[:=]\s*(?:true|True)\b"""),
    "private_api_keys_used_true": re.compile(r"""['"]private_api_keys_used['"]\s*[:=]\s*(?:true|True)\b"""),
    "withdrawal_allowed_true": re.compile(r"""['"]withdrawal_allowed['"]\s*[:=]\s*(?:true|True)\b"""),
    "margin_futures_perpetuals_enabled_true": re.compile(r"""['"]margin_futures_perpetuals_enabled['"]\s*[:=]\s*(?:true|True)\b"""),
}

PRIVATE_ENDPOINT_PATTERNS = {
    "binance_spot_order_endpoint": re.compile(r"""/api/v3/(?:order|openOrders|allOrders)\b"""),
    "binance_spot_account_endpoint": re.compile(r"""/api/v3/(?:account|myTrades)\b"""),
    "binance_user_stream_endpoint": re.compile(r"""/api/v3/userDataStream\b"""),
    "binance_withdraw_endpoint": re.compile(r"""/sapi/v1/capital/withdraw"""),
    "binance_margin_endpoint": re.compile(r"""/sapi/v1/margin/"""),
    "binance_futures_order_endpoint": re.compile(r"""/fapi/v1/(?:order|batchOrders|allOrders)\b"""),
}

PRIVATE_SIGNING_PATTERNS = {
    "private_signature_param": re.compile(r"""signature\s*=""", re.IGNORECASE),
    "hmac_signing_code": re.compile(r"""\bhmac\."""),
}

WARNING_PATTERNS = {
    "binance_api_key_header_marker": re.compile(r"""X-MBX-APIKEY""", re.IGNORECASE),
}

SUSPICIOUS_KEY_LITERAL_PATTERNS = {
    "suspicious_32_hex_api_key_literal": re.compile(r"""['"][a-fA-F0-9]{32}['"]"""),
    "suspicious_long_alnum_api_key_literal": re.compile(r"""['"][A-Za-z0-9]{48,}['"]"""),
}

DECLARED_SHA256_LINE = re.compile(
    r"^\s*['\"]sha256['\"]\s*:\s*['\"][a-fA-F0-9]{64}['\"]\s*,?\s*$"
)


class FileReadTimeout(Exception):
    pass


def _read_timeout_handler(signum: int, frame: Any) -> None:
    raise FileReadTimeout("file read timed out")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def iter_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if not path.exists():
            continue
        if path.is_file():
            if path.suffix in TEXT_SUFFIXES:
                files.append(path)
            continue
        for child in path.rglob("*"):
            if child.is_file() and child.suffix in TEXT_SUFFIXES:
                if any(part in EXCLUDED_DIRS for part in child.parts):
                    continue
                if child.name == "safety_invariant_auditor.py":
                    continue
                files.append(child)
    return sorted(set(files))


def text_matches(path: Path, text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        findings.extend(line_matches(path, line, line_no))
    return findings


def line_matches(path: Path, line: str, line_no: int) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    lower = line.lower()
    if "true" in lower:
        for name, pattern in TRUE_FLAG_PATTERNS.items():
            key = name.removesuffix("_true")
            if key in lower and pattern.search(line):
                findings.append(finding_from_line(path, name, line_no, line, "blocked"))
    endpoint_markers = {
        "binance_spot_order_endpoint": ("/api/v3/order", "/api/v3/openOrders", "/api/v3/allOrders"),
        "binance_spot_account_endpoint": ("/api/v3/account", "/api/v3/myTrades"),
        "binance_user_stream_endpoint": ("/api/v3/userDataStream",),
        "binance_withdraw_endpoint": ("/sapi/v1/capital/withdraw",),
        "binance_margin_endpoint": ("/sapi/v1/margin/",),
        "binance_futures_order_endpoint": ("/fapi/v1/order", "/fapi/v1/batchOrders", "/fapi/v1/allOrders"),
    }
    for name, markers in endpoint_markers.items():
        if any(marker in line for marker in markers):
            findings.append(finding_from_line(path, name, line_no, line, "blocked"))
    if "signature" in lower and "=" in line:
        findings.append(finding_from_line(path, "private_signature_param", line_no, line, "blocked"))
    if "hmac." in lower:
        findings.append(finding_from_line(path, "hmac_signing_code", line_no, line, "blocked"))
    if "x-mbx-apikey" in lower:
        findings.append(finding_from_line(path, "binance_api_key_header_marker", line_no, line, "warning"))
    if not DECLARED_SHA256_LINE.match(line):
        for rule in suspicious_quoted_literal_rules(line):
            findings.append(finding_from_line(path, rule, line_no, line, "blocked"))
    return findings


def suspicious_quoted_literal_rules(line: str) -> list[str]:
    rules: list[str] = []
    quote: str | None = None
    token: list[str] = []
    escaped = False
    for ch in line:
        if quote is None:
            if ch in {"'", '"'}:
                quote = ch
                token = []
                escaped = False
            continue
        if escaped:
            token.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == quote:
            value = "".join(token)
            if len(value) == 32 and all(c in "0123456789abcdefABCDEF" for c in value):
                rules.append("suspicious_32_hex_api_key_literal")
            elif len(value) >= 48 and value.isalnum():
                rules.append("suspicious_long_alnum_api_key_literal")
            quote = None
            token = []
            continue
        token.append(ch)
    return rules


def finding(path: Path, name: str, match: re.Match[str], severity: str) -> dict[str, Any]:
    line_no = match.string.count("\n", 0, match.start()) + 1
    line_start = match.string.rfind("\n", 0, match.start()) + 1
    line_end = match.string.find("\n", match.start())
    if line_end < 0:
        line_end = len(match.string)
    line = match.string[line_start:line_end].strip()
    return {
        "rule": name,
        "severity": severity,
        "path": rel(path),
        "line": line_no,
        "excerpt": line[:240],
    }


def finding_from_line(path: Path, name: str, line_no: int, line: str, severity: str) -> dict[str, Any]:
    return {
        "rule": name,
        "severity": severity,
        "path": rel(path),
        "line": line_no,
        "excerpt": line.strip()[:240],
    }


def audit_paths(paths: list[Path]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    read_errors: list[dict[str, Any]] = []
    files = iter_files(paths)
    for path in files:
        try:
            text = read_text_with_timeout(path)
        except (OSError, FileReadTimeout) as exc:
            read_errors.append(
                {
                    "rule": "file_read_error",
                    "severity": "warning",
                    "path": rel(path),
                    "line": 0,
                    "excerpt": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        findings.extend(text_matches(path, text))
    findings.extend(read_errors)
    blocked = [item for item in findings if item.get("severity") == "blocked"]
    return {
        "status": "blocked" if blocked else "pass",
        "files_scanned": len(files),
        "blocked_count": len(blocked),
        "warning_count": len([item for item in findings if item.get("severity") == "warning"]),
        "findings": findings[:100],
    }


def read_text_with_timeout(path: Path, timeout_seconds: float = 1.0) -> str:
    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _read_timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Active Alpha Safety Invariant Audit",
        "",
        f"- status: `{payload.get('status')}`",
        f"- files_scanned: `{payload.get('files_scanned')}`",
        f"- blocked_count: `{payload.get('blocked_count')}`",
        "",
        "| Rule | File | Line | Excerpt |",
        "|---|---|---:|---|",
    ]
    findings = payload.get("findings") or []
    if findings:
        for item in findings:
            excerpt = str(item.get("excerpt") or "").replace("|", "\\|")
            lines.append(f"| `{item.get('rule')}` | `{item.get('path')}` | {item.get('line')} | {excerpt} |")
    else:
        lines.append("| none | - | - | - |")
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="active_alpha_safety_audit_", dir="/private/tmp") as tmp_text:
        tmp = Path(tmp_text)
        safe = tmp / "safe.py"
        safe.write_text(
            '\n'.join(
                [
                    '"No live orders. live_orders_enabled=false. /fapi/v1/openInterest is public context only."',
                    '"sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",',
                ]
            ) + "\n",
            encoding="utf-8",
        )
        unsafe = tmp / "unsafe.py"
        unsafe.write_text(
            '\n'.join(
                [
                    '{"live_orders_enabled": True}',
                    'endpoint = "/api/v3/order"',
                    'headers = {"X-MBX-APIKEY": "redacted"}',
                    'leaked = "FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE"',
                    'api_key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"',
                ]
            ),
            encoding="utf-8",
        )
        safe_payload = audit_paths([safe])
        unsafe_payload = audit_paths([unsafe])
        assert safe_payload["status"] == "pass", safe_payload
        assert unsafe_payload["status"] == "blocked" and unsafe_payload["blocked_count"] >= 3, unsafe_payload
        assert unsafe_payload["warning_count"] >= 1, unsafe_payload
    return {
        "status": "ok",
        "safe_fixture_status": safe_payload["status"],
        "unsafe_fixture_status": unsafe_payload["status"],
        "unsafe_blocked_count": unsafe_payload["blocked_count"],
        "uses_temporary_files_only": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ACTIVE_ROOT))
    parser.add_argument("--automation", default=str(DEFAULT_AUTOMATION))
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--compact-output", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return
    paths = [Path(args.root), Path(args.automation)]
    payload = audit_paths(paths)
    if args.format == "markdown":
        print(render_markdown(payload))
    else:
        if args.compact_output:
            payload = {
                "status": payload["status"],
                "files_scanned": payload["files_scanned"],
                "blocked_count": payload["blocked_count"],
                "warning_count": payload["warning_count"],
                "findings": payload["findings"][:20],
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
