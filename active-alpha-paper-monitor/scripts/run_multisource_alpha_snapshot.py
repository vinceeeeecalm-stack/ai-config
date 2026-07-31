#!/usr/bin/env python3
"""Run the Active-owned multi-source alpha snapshot implementation."""

import runpy
import sys
from pathlib import Path


def find_shared_script():
    here = Path(__file__).resolve()
    local_shared = here.with_name("multisource_alpha_snapshot.shared.py")
    if local_shared.exists():
        return local_shared
    raise SystemExit("Active-owned multisource_alpha_snapshot.shared.py is missing.")


if __name__ == "__main__":
    script_path = find_shared_script()
    sys.argv[0] = str(script_path)
    runpy.run_path(str(script_path), run_name="__main__")
