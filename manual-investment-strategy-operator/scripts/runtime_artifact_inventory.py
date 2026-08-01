#!/usr/bin/env python3
"""Inventory and optionally archive generated investment runtime artifacts.

The default mode is read-only and prints a manifest. Archiving is explicit,
keeps original relative paths, verifies file hashes after the move, and never
deletes an existing archive target.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


RUNTIME_DIRS = (
    "reports",
    "experiments",
    "handoffs",
    "cache",
    "paper_trades",
    "subagent_outputs",
    "subagent_tasks",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(source_root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for dirname in RUNTIME_DIRS:
        directory = source_root / dirname
        if not directory.exists():
            continue
        for path in sorted(p for p in directory.rglob("*") if p.is_file()):
            rows.append(
                {
                    "relative_path": str(path.relative_to(source_root)),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
    return {
        "schema_version": "investment-runtime-manifest-v1",
        "generated_at": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "source_root": str(source_root),
        "runtime_directories": list(RUNTIME_DIRS),
        "file_count": len(rows),
        "total_bytes": sum(row["size_bytes"] for row in rows),
        "files": rows,
    }


def archive_runtime(
    source_root: Path, archive_root: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    if source_root.resolve() == archive_root.resolve():
        raise ValueError("archive_root must differ from source_root")
    archive_root.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for row in manifest["files"]:
        rel = Path(row["relative_path"])
        source = source_root / rel
        target = archive_root / rel
        if target.exists():
            raise FileExistsError(f"archive target already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        if target.stat().st_size != row["size_bytes"] or sha256(target) != row["sha256"]:
            raise RuntimeError(f"archive verification failed: {target}")
        moved.append(str(rel))

    for dirname in RUNTIME_DIRS:
        directory = source_root / dirname
        if not directory.exists():
            continue
        for path in sorted(
            (p for p in directory.rglob("*") if p.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                path.rmdir()
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass

    return {
        "status": "archived",
        "archive_root": str(archive_root),
        "moved_file_count": len(moved),
        "verified": len(moved) == manifest["file_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inventory or archive generated investment runtime artifacts."
    )
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--manifest-json")
    parser.add_argument("--archive-root")
    parser.add_argument("--move-after-inventory", action="store_true")
    args = parser.parse_args()

    source_root = Path(args.source_root).expanduser().resolve()
    if not source_root.exists() or not source_root.is_dir():
        raise SystemExit(f"source root is not a directory: {source_root}")

    manifest = inventory(source_root)
    if args.manifest_json:
        output = Path(args.manifest_json).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    result: dict[str, Any] = {"manifest": manifest, "archive": None}
    if args.move_after_inventory:
        if not args.archive_root:
            raise SystemExit("--archive-root is required with --move-after-inventory")
        result["archive"] = archive_runtime(
            source_root,
            Path(args.archive_root).expanduser().resolve(),
            manifest,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
