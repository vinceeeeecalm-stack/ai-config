#!/usr/bin/env python3
"""
Fast crypto paper auto-trader wrapper.

Research-only. This runs the shared paper loop in fast mode: shorter
intervals, fewer strategy permutations, and compact output so hourly
automation can react faster without placing live orders.

All CLI args are intentionally passed through to sunday_crypto_realistic_paper_loop,
including --external-agent-outputs-json for externally orchestrated subagent
research panels.
"""

import sys

from sunday_crypto_realistic_paper_loop import main


if __name__ == "__main__":
    if "--loop-kind" not in sys.argv:
        sys.argv.extend(["--loop-kind", "fast", "--allow-outside-window"])
    if "--compact-output" not in sys.argv:
        sys.argv.append("--compact-output")
    main()
