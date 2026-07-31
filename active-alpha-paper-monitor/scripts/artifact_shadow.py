"""Small helpers for durable shadow copies of active-alpha artifacts.

Shadow files live under /private/tmp so auditors can still read recent reports
when the workspace copy is temporarily unavailable. These helpers do not change
the primary artifact, paper ledger, or trading state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SHADOW_BASE = Path("/private/tmp/active-alpha-paper-monitor-shadow")


def _shadow_path(path: Path) -> Path:
    source = Path(path).resolve()
    try:
        root = source.parents[1]
        rel = source.relative_to(root)
        return SHADOW_BASE / root.name / rel
    except Exception:
        return SHADOW_BASE / source.name


def write_shadow_text(path: Path, text: str) -> Path:
    shadow = _shadow_path(Path(path))
    shadow.parent.mkdir(parents=True, exist_ok=True)
    shadow.write_text(text, encoding="utf-8")
    return shadow


def write_shadow_json(path: Path, payload: Any) -> Path:
    shadow = _shadow_path(Path(path))
    shadow.parent.mkdir(parents=True, exist_ok=True)
    with shadow.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return shadow
