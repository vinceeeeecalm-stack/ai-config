#!/usr/bin/env python3
"""Finalize due tactical observations without mixing them into trading ROI."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from impulse_capture_scanner import fetch_json, summarize_klines
from tactical_evidence_ledger import review_due_observations


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def live_bar_provider(timeout: float):
    def provide(observation: dict[str, Any], as_of: str) -> list[dict[str, Any]]:
        start = parse_time(observation["observed_at"])
        due = parse_time(observation["review_due_at"])
        cutoff = min(due, parse_time(as_of))
        raw = fetch_json(
            "/api/v3/klines",
            {
                "symbol": observation["symbol"],
                "interval": "15m",
                "startTime": int(start.timestamp() * 1000),
                "endTime": int(cutoff.timestamp() * 1000),
                "limit": 1000,
            },
            timeout=min(max(float(timeout), 0.25), 12.0),
            max_bases=2,
            transport="curl",
        )
        return summarize_klines(raw)

    return provide


def fixture_bar_provider(directory: Path):
    def provide(observation: dict[str, Any], _as_of: str) -> list[dict[str, Any]]:
        path = directory / f"{observation['symbol']}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("fixture bars must be an array")
        if payload and isinstance(payload[0], list):
            return summarize_klines(payload)
        return payload

    return provide


def run(args: argparse.Namespace) -> dict[str, Any]:
    as_of = args.as_of or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    parse_time(as_of)
    provider = (
        fixture_bar_provider(Path(args.bars_dir).expanduser().resolve())
        if args.bars_dir
        else live_bar_provider(args.timeout)
    )
    result = review_due_observations(
        observation_ledger=Path(args.observation_ledger).expanduser().resolve(),
        outcome_ledger=Path(args.outcome_ledger).expanduser().resolve(),
        as_of=as_of,
        bar_provider=provider,
        max_workers=args.max_workers,
        write=not args.no_write,
    )
    result["human_confirmation_required"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review final-due tactical observations using closed public 15m bars."
    )
    parser.add_argument("--observation-ledger", required=True)
    parser.add_argument("--outcome-ledger", required=True)
    parser.add_argument("--as-of")
    parser.add_argument("--bars-dir", help="Deterministic normalized or Binance-array fixture bars")
    parser.add_argument("--timeout", type=float, default=4.0)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run(args)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(output)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
