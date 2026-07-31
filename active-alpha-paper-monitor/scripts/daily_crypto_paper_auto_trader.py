#!/usr/bin/env python3
"""
Daily crypto paper auto-trader wrapper.

Research-only. This invokes the shared realistic paper loop in daily mode so
the same execution model used by the Sunday stress test can run on a regular
schedule. It never enables live orders or private trading APIs.

All CLI args are intentionally passed through to sunday_crypto_realistic_paper_loop,
including --external-agent-outputs-json for externally orchestrated subagent
research panels.
"""

import sys

from sunday_crypto_realistic_paper_loop import main


if __name__ == "__main__":
    if "--loop-kind" not in sys.argv:
        sys.argv.extend(["--loop-kind", "daily", "--allow-outside-window"])
    if "--compact-output" not in sys.argv:
        sys.argv.append("--compact-output")
    main()
