#!/usr/bin/env python3
"""Build and verify durable Binance Kline cache manifests."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any


MANIFEST_NAME = "kline-cache-manifest.json"
MANIFEST_VERSION = "kline-cache-manifest-v1"
KLINE_FILE_RE = re.compile(
    r"^(?P<symbol>[A-Z0-9]+)_(?P<interval>\d+[mhdw])_(?P<start>\d+)_(?P<end>\d+)\.json$"
)
REQUIRED_ROW_KEYS = {"t", "o", "h", "l", "c", "v", "ct", "qv", "n"}


def cache_dir_from_payload(payload: dict[str, Any]) -> str:
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
    return str(payload.get("cache_dir") or outputs.get("cache_dir") or "").strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def kline_data_paths(cache_dir: Path, recursive: bool = False) -> list[Path]:
    iterator = cache_dir.rglob("*.json") if recursive else cache_dir.glob("*.json")
    return sorted(path for path in iterator if path.is_file() and KLINE_FILE_RE.match(path.name))


def validate_kline_file(path: Path) -> dict[str, Any]:
    match = KLINE_FILE_RE.match(path.name)
    errors: list[str] = []
    if not match:
        return {"status": "invalid", "errors": ["invalid_kline_filename"]}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"status": "invalid", "errors": [f"json_parse_error:{type(exc).__name__}"]}
    if not isinstance(rows, list) or not rows:
        return {"status": "invalid", "errors": ["rows_missing_or_empty"], "bars": 0}

    timestamps: list[int] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"row_{index}_not_object")
            break
        missing = REQUIRED_ROW_KEYS - set(row)
        if missing:
            errors.append(f"row_{index}_missing_keys:{','.join(sorted(missing))}")
            break
        try:
            timestamp = int(row["t"])
            open_price = float(row["o"])
            high = float(row["h"])
            low = float(row["l"])
            close = float(row["c"])
            volume = float(row["v"])
            quote_volume = float(row["qv"])
            close_time = int(row["ct"])
            trade_count = int(row["n"])
        except (TypeError, ValueError):
            errors.append(f"row_{index}_numeric_type_error")
            break
        if min(open_price, high, low, close) <= 0:
            errors.append(f"row_{index}_nonpositive_price")
            break
        if high < max(open_price, close, low) or low > min(open_price, close, high):
            errors.append(f"row_{index}_ohlc_invariant_failed")
            break
        if volume < 0 or quote_volume < 0 or trade_count < 0 or close_time < timestamp:
            errors.append(f"row_{index}_volume_or_time_invariant_failed")
            break
        timestamps.append(timestamp)

    if timestamps and timestamps != sorted(set(timestamps)):
        errors.append("timestamps_not_strictly_increasing_unique")
    expected_start = int(match.group("start"))
    expected_end = int(match.group("end"))
    if timestamps and (timestamps[0] != expected_start or timestamps[-1] != expected_end):
        errors.append("filename_window_mismatch")
    return {
        "status": "valid" if not errors else "invalid",
        "errors": errors,
        "symbol": match.group("symbol"),
        "interval": match.group("interval"),
        "bars": len(rows),
        "first_open_time_ms": timestamps[0] if timestamps else None,
        "last_open_time_ms": timestamps[-1] if timestamps else None,
    }


def build_kline_cache_manifest(
    cache_dir: Path,
    *,
    required_intervals: list[str] | None = None,
    selected_symbols: list[str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    cache_dir = cache_dir.expanduser().resolve()
    rows = []
    for path in kline_data_paths(cache_dir):
        validation = validate_kline_file(path)
        rows.append(
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                **validation,
            }
        )
    valid_rows = [item for item in rows if item.get("status") == "valid"]
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "generated_at": generated_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "cache_dir": str(cache_dir),
        "required_intervals": list(required_intervals or []),
        "selected_symbols": list(selected_symbols or []),
        "file_count": len(rows),
        "valid_file_count": len(valid_rows),
        "invalid_file_count": len(rows) - len(valid_rows),
        "files": rows,
        "live_orders_enabled": False,
        "private_api_used": False,
    }
    output = cache_dir / MANIFEST_NAME
    temporary = cache_dir / f".{MANIFEST_NAME}.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return {**manifest, "manifest_path": str(output)}


def inspect_kline_cache_manifest(cache_dir: Path) -> dict[str, Any]:
    cache_dir = cache_dir.expanduser()
    manifest_path = cache_dir / MANIFEST_NAME
    data_paths = kline_data_paths(cache_dir) if cache_dir.is_dir() else []
    base = {
        "manifest_path": str(manifest_path),
        "manifest_exists": manifest_path.is_file(),
        "data_file_count": len(data_paths),
        "verified_file_count": 0,
        "hash_mismatch_count": 0,
        "schema_invalid_count": 0,
        "missing_manifest_file_count": 0,
        "untracked_data_file_count": 0,
        "manifest_valid": False,
    }
    if not manifest_path.is_file():
        return {**base, "manifest_status": "manifest_missing"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {**base, "manifest_status": f"manifest_parse_error:{type(exc).__name__}"}
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        return {**base, "manifest_status": "manifest_version_unsupported"}

    listed = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    listed_names: set[str] = set()
    hash_mismatch = 0
    schema_invalid = 0
    missing = 0
    verified = 0
    unsafe_paths = 0
    for item in listed:
        if not isinstance(item, dict):
            schema_invalid += 1
            continue
        name = str(item.get("path") or "")
        if not name or Path(name).name != name or not KLINE_FILE_RE.match(name):
            unsafe_paths += 1
            continue
        listed_names.add(name)
        path = cache_dir / name
        if not path.is_file():
            missing += 1
            continue
        if int(item.get("size_bytes") or -1) != path.stat().st_size or str(item.get("sha256")) != sha256_file(path):
            hash_mismatch += 1
            continue
        validation = validate_kline_file(path)
        if validation.get("status") != "valid" or item.get("status") != "valid":
            schema_invalid += 1
            continue
        if int(item.get("bars") or -1) != int(validation.get("bars") or 0):
            schema_invalid += 1
            continue
        verified += 1
    actual_names = {path.name for path in data_paths}
    untracked = len(actual_names - listed_names)
    declared_count = int(manifest.get("file_count") or 0)
    valid = (
        declared_count > 0
        and declared_count == len(listed)
        and int(manifest.get("valid_file_count") or 0) == declared_count
        and verified == declared_count
        and not hash_mismatch
        and not schema_invalid
        and not missing
        and not unsafe_paths
        and not untracked
    )
    return {
        **base,
        "manifest_status": "verified" if valid else "manifest_integrity_failed",
        "manifest_valid": valid,
        "manifest_version": manifest.get("manifest_version"),
        "manifest_generated_at": manifest.get("generated_at"),
        "manifest_declared_file_count": declared_count,
        "verified_file_count": verified,
        "hash_mismatch_count": hash_mismatch,
        "schema_invalid_count": schema_invalid,
        "missing_manifest_file_count": missing,
        "unsafe_manifest_path_count": unsafe_paths,
        "untracked_data_file_count": untracked,
        "required_intervals": manifest.get("required_intervals") or [],
        "selected_symbols": manifest.get("selected_symbols") or [],
    }


def inspect_kline_cache_storage(payload: dict[str, Any]) -> dict[str, Any]:
    files_written = payload.get("files_written") if isinstance(payload.get("files_written"), list) else []
    declared_file_count = int(payload.get("file_count") if payload.get("file_count") is not None else len(files_written))
    cache_dir_text = cache_dir_from_payload(payload)
    cache_dir = Path(cache_dir_text).expanduser() if cache_dir_text else None
    cache_dir_exists = bool(cache_dir and cache_dir.is_dir())

    declared_paths = [Path(str(item["path"])).expanduser() for item in files_written if isinstance(item, dict) and item.get("path")]
    existing_declared_file_count = sum(path.is_file() for path in declared_paths)
    missing_declared_file_count = len(declared_paths) - existing_declared_file_count
    actual_paths = kline_data_paths(cache_dir) if cache_dir_exists and cache_dir else []
    actual_file_count = len(actual_paths)
    manifest = inspect_kline_cache_manifest(cache_dir) if cache_dir_exists and cache_dir else {
        "manifest_status": "cache_dir_missing",
        "manifest_valid": False,
        "manifest_exists": False,
    }
    interval_contract = payload.get("interval_contract") if isinstance(payload.get("interval_contract"), dict) else {}
    required_intervals = interval_contract.get("required_intervals") or manifest.get("required_intervals") or []
    actual_intervals = {match.group("interval") for path in actual_paths if (match := KLINE_FILE_RE.match(path.name))}
    missing_required_intervals = [interval for interval in required_intervals if interval not in actual_intervals]

    if not cache_dir_text:
        storage_status = "missing_cache_dir_reference"
    elif not cache_dir_exists:
        storage_status = "cache_dir_missing"
    elif actual_file_count <= 0:
        storage_status = "cache_dir_empty"
    elif not manifest.get("manifest_valid"):
        storage_status = str(manifest.get("manifest_status") or "manifest_invalid")
    elif declared_paths and missing_declared_file_count > 0:
        storage_status = "partial_files_missing"
    elif declared_file_count > 0 and actual_file_count < declared_file_count:
        storage_status = "actual_file_count_below_declared"
    elif missing_required_intervals:
        storage_status = "required_interval_coverage_incomplete"
    else:
        storage_status = "available_verified_manifest"

    replay_available = storage_status == "available_verified_manifest" and actual_file_count > 0
    return {
        "cache_dir": cache_dir_text,
        "cache_dir_exists": cache_dir_exists,
        "storage_status": storage_status,
        "declared_file_count": declared_file_count,
        "declared_path_count": len(declared_paths),
        "existing_declared_file_count": existing_declared_file_count,
        "missing_declared_file_count": missing_declared_file_count,
        "actual_file_count": actual_file_count,
        "required_intervals": required_intervals,
        "actual_intervals": sorted(actual_intervals),
        "missing_required_intervals": missing_required_intervals,
        "interval_coverage_complete": not missing_required_intervals,
        "manifest": manifest,
        "replay_available": replay_available,
        "refresh_required": not replay_available,
    }


def _unit_rows(start: int, count: int) -> list[dict[str, Any]]:
    return [
        {"t": start + i * 60_000, "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 2, "ct": start + i * 60_000 + 59_999, "qv": 20, "n": 3}
        for i in range(count)
    ]


def self_test() -> dict[str, Any]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "cache"
        root.mkdir()
        rows = _unit_rows(1_700_000_000_000, 3)
        first = root / f"BTCUSDT_1m_{rows[0]['t']}_{rows[-1]['t']}.json"
        first.write_text(json.dumps(rows), encoding="utf-8")
        payload = {
            "cache_dir": str(root),
            "file_count": 1,
            "files_written": [{"path": str(first), "interval": "1m"}],
            "interval_contract": {"required_intervals": ["1m"]},
        }
        missing_manifest = inspect_kline_cache_storage(payload)
        assert missing_manifest["storage_status"] == "manifest_missing", missing_manifest
        build_kline_cache_manifest(root, required_intervals=["1m"], selected_symbols=["BTCUSDT"])
        available = inspect_kline_cache_storage(payload)
        assert available["storage_status"] == "available_verified_manifest", available
        assert available["actual_file_count"] == 1 and available["replay_available"], available

        first.write_text(first.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        tampered = inspect_kline_cache_storage(payload)
        assert tampered["storage_status"] == "manifest_integrity_failed", tampered
        assert tampered["manifest"]["hash_mismatch_count"] == 1, tampered

        first.unlink()
        missing = inspect_kline_cache_storage(payload)
        assert missing["storage_status"] == "cache_dir_empty", missing
        assert missing["actual_file_count"] == 0 and missing["refresh_required"], missing

    return {
        "status": "ok",
        "manifest_required_verified": True,
        "available_verified": True,
        "hash_tamper_block_verified": True,
        "schema_validation_verified": True,
        "missing_verified": True,
        "required_interval_coverage_verified": True,
        "live_orders_enabled": False,
        "private_api_used": False,
    }


if __name__ == "__main__":
    print(json.dumps(self_test(), ensure_ascii=False, indent=2))
