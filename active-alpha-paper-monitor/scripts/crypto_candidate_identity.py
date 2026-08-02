#!/usr/bin/env python3
"""Deterministic identity boundary for the Crypto opportunity universe.

The exchange lists both native Crypto assets and tokenized securities under
USDT pairs.  A small suffix convention is useful only as a conservative
request for identity evidence; it must never be interpreted as alpha.
"""

from __future__ import annotations

import json
import subprocess
from urllib.request import Request, urlopen


TOKENIZED_SECURITY_SUFFIXES = ("B", "ON")

# Exact native assets whose suffix collides with the tokenized-security naming
# convention.  This list is deliberately narrow and evidence-reviewed.  Other
# collisions require a bounded exact public identity lookup in the formal
# scanner; the fast dynamic pool excludes them conservatively.
KNOWN_NATIVE_CRYPTO_SUFFIX_BASES = frozenset({"BNB", "TON", "SHIB"})
BINANCE_PUBLIC_PRODUCT_URL = (
    "https://www.binance.com/bapi/asset/v1/public/asset-service/product/get-products"
)
TOKENIZED_SECURITY_TAGS = frozenset({"bstocks", "xstocks", "tokenized-stock"})
TOKENIZED_SECURITY_NAME_MARKERS = (
    "(bstocks)",
    "tokenized stock",
    "tokenized equity",
    "tokenized etf",
)


def normalize_base(symbol_or_base: str) -> str:
    value = str(symbol_or_base or "").strip().upper()
    return value[:-4] if value.endswith("USDT") else value


def requires_public_identity_resolution(symbol_or_base: str) -> bool:
    base = normalize_base(symbol_or_base)
    return bool(base) and base.endswith(TOKENIZED_SECURITY_SUFFIXES) and base not in KNOWN_NATIVE_CRYPTO_SUFFIX_BASES


def fast_opportunity_identity_rejection_reason(symbol_or_base: str) -> str | None:
    """Conservative zero-network gate used by broad dynamic ranking."""

    if requires_public_identity_resolution(symbol_or_base):
        return "tokenized_security_or_unresolved_suffix_identity"
    return None


def build_binance_product_identity(payload: object) -> dict[str, dict]:
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("Binance public product payload has no data list")
    return {
        str(row.get("s") or "").upper(): row
        for row in rows
        if isinstance(row, dict) and row.get("s")
    }


def fetch_binance_public_product_identity(timeout: float = 8.0) -> dict[str, dict]:
    bounded_timeout = max(0.5, float(timeout))
    result = subprocess.run(
        [
            "curl",
            "--compressed",
            "--connect-timeout",
            str(min(1.0, bounded_timeout)),
            "--max-time",
            str(bounded_timeout),
            "--retry",
            "0",
            "-sS",
            BINANCE_PUBLIC_PRODUCT_URL,
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=bounded_timeout + 0.5,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or f"Binance product catalog returncode={result.returncode}"
        )
    payload = json.loads(result.stdout)
    return build_binance_product_identity(payload)


def product_identity_rejection_reason(
    symbol: str,
    product_identity: dict[str, dict] | None,
    *,
    source_available: bool,
) -> str | None:
    """Return a deterministic opportunity rejection without using price data."""

    normalized = str(symbol or "").strip().upper()
    record = (product_identity or {}).get(normalized)
    if record:
        tags = {str(tag or "").strip().lower() for tag in record.get("tags") or []}
        name = str(record.get("an") or "").strip().lower()
        if tags.intersection(TOKENIZED_SECURITY_TAGS) or any(
            marker in name for marker in TOKENIZED_SECURITY_NAME_MARKERS
        ):
            return "tokenized_security_product"
        return None
    if requires_public_identity_resolution(normalized):
        return (
            "not_confirmed_in_binance_public_product_catalog"
            if source_available
            else "tokenized_security_or_unresolved_suffix_identity"
        )
    return None
