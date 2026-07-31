#!/usr/bin/env python3
"""Full-market paper-only scan for equal-share YES+NO binary pair dislocations."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import time
from http.client import HTTPException, IncompleteRead
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CLOB_PRICE_SIDE_FOR_BEST_ASK = "SELL"
CLOB_PRICE_SIDE_FOR_BEST_BID = "BUY"
DEFAULT_BUDGET_USD = 75.0
EXTRA_SLIPPAGE_BPS = 5.0
SETTLEMENT_RISK_RESERVE_PER_PAIR = 0.002
MINT_OPERATION_RISK_RESERVE_PER_PAIR = 0.003
MIN_RESEARCH_NET_EDGE_PER_PAIR = 0.005
MIN_MAIN_GATE_EDGE_PER_PAIR = 0.04
DEFAULT_LEDGER = ROOT / "data/binary_pair_research_ledger.json"
CTF_SEMANTIC_CONTRACT = ROOT / "config/polymarket_ctf_semantic_contract.json"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


core = load("binary_pair_core", ROOT / "scripts/polymarket_alpha.py")
public = load("binary_pair_public", ROOT / "scripts/polymarket_public_data.py")


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_ctf_semantic_contract(path: Path = CTF_SEMANTIC_CONTRACT) -> dict[str, Any]:
    contract = core.read_json(path)
    proof = contract.get("read_only_condition_proof") or {}
    if (contract.get("status") != "official_general_semantics_validated"
            or contract.get("general_semantics_validated") is not True
            or contract.get("per_market_execution_validated") is not False
            or contract.get("paper_entry_eligible") is not False
            or contract.get("live_orders_enabled") is not False
            or contract.get("private_api_used") is not False
            or contract.get("binary_partition") != [1, 2]
            or proof.get("chain_id") != 137
            or str(proof.get("conditional_tokens_contract", "")).lower() != "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
            or proof.get("method_signature") != "getOutcomeSlotCount(bytes32)"
            or proof.get("method_selector") != "0xd42dc0c2"
            or proof.get("required_outcome_slot_count") != 2
            or proof.get("minimum_consistent_public_rpc_sources") != 2
            or set(proof.get("public_rpc_sources") or []) != {
                "https://polygon.publicnode.com", "https://polygon.api.onfinality.io/public"}):
        raise ValueError("unsafe or incomplete CTF semantic contract")
    return contract


def polygon_rpc_call(endpoint: str, method: str, params: list[Any], allowed_endpoints: list[str],
                     timeout: float = 15.0, retries: int = 1) -> Any:
    if endpoint not in allowed_endpoints or method not in {"eth_chainId", "eth_call"}:
        raise ValueError("unapproved Polygon read-only RPC request")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    errors = []
    for attempt in range(retries + 1):
        try:
            request = Request(endpoint, data=body, method="POST", headers={
                "Accept": "application/json", "Content-Type": "application/json",
                "User-Agent": "polymarket-alpha-paper-research/1.0",
            })
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("error") or not isinstance(payload.get("result"), str):
                raise ValueError(f"JSON-RPC error: {payload.get('error')}")
            return payload["result"]
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError,
                HTTPException, IncompleteRead, ValueError) as exc:
            errors.append(f"attempt_{attempt + 1}:{type(exc).__name__}:{exc}")
            if attempt < retries:
                time.sleep(min(2 ** attempt, 2))
    raise RuntimeError("; ".join(errors))


def verify_prepared_binary_condition(condition_id: str, contract: dict[str, Any] | None = None,
                                     rpc_call=None) -> dict[str, Any]:
    contract = contract or load_ctf_semantic_contract()
    proof = contract["read_only_condition_proof"]
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", condition_id or ""):
        return {"status": "invalid_condition_id", "prepared_binary_condition": False, "sources": []}
    endpoints = list(proof["public_rpc_sources"]); caller = rpc_call or polygon_rpc_call
    call_data = proof["method_selector"] + condition_id[2:]
    rows = []
    for endpoint in endpoints:
        try:
            chain_hex = caller(endpoint, "eth_chainId", [], endpoints)
            result_hex = caller(endpoint, "eth_call", [{
                "to": proof["conditional_tokens_contract"], "data": call_data,
            }, "latest"], endpoints)
            chain_id, outcome_slots = int(chain_hex, 16), int(result_hex, 16)
            valid = chain_id == int(proof["chain_id"]) and outcome_slots == int(proof["required_outcome_slot_count"])
            rows.append({"endpoint": endpoint, "status": "ok" if valid else "mismatch",
                         "chain_id": chain_id, "outcome_slot_count": outcome_slots})
        except Exception as exc:
            rows.append({"endpoint": endpoint, "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
    consistent = sum(row["status"] == "ok" for row in rows)
    verified = consistent >= int(proof["minimum_consistent_public_rpc_sources"])
    return {
        "status": "verified" if verified else "insufficient_consistent_sources",
        "prepared_binary_condition": verified, "consistent_source_count": consistent,
        "required_consistent_source_count": int(proof["minimum_consistent_public_rpc_sources"]),
        "method_signature": proof["method_signature"], "method_selector": proof["method_selector"],
        "sources": rows, "read_only": True, "wallet_used": False, "private_api_used": False,
    }


def new_ledger() -> dict[str, Any]:
    return {
        "schema_version": "polymarket-binary-pair-research-ledger-v1", "created_at": core.now_iso(),
        "updated_at": core.now_iso(), "scan_observations": [], "candidate_observations": [],
        "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }


def load_ledger(path: Path) -> dict[str, Any]:
    ledger = core.read_json(path) if path.exists() else new_ledger()
    if (ledger.get("paper_estimates_emitted") is not False or ledger.get("main_paper_ledger_mutated") is not False
            or ledger.get("live_orders_enabled") is not False or ledger.get("private_api_used") is not False):
        raise ValueError("unsafe binary pair research ledger")
    return ledger


def record_observation(ledger: dict[str, Any], payload: dict[str, Any]) -> str:
    scan_id = core.stable_id("pm-binary-pair-scan", str(payload["scan_completed_at"]), str(payload["source_snapshot_manifest_sha256"]))
    minimum = min((row.get("verified_paired_best_ask_sum") for row in payload.get("top_verified_pairs", []) if row.get("verified_paired_best_ask_sum") is not None), default=None)
    if not any(row.get("scan_id") == scan_id for row in ledger["scan_observations"]):
        ledger["scan_observations"].append({
            "scan_id": scan_id, "observed_at": payload["scan_completed_at"],
            "source_snapshot_manifest_sha256": payload["source_snapshot_manifest_sha256"],
            "binary_markets_scanned": payload["binary_markets_scanned"],
            "complete_top_of_book_pairs": payload["complete_top_of_book_pairs"],
            "minimum_verified_paired_best_ask_sum": minimum,
            "maximum_verified_paired_best_bid_sum": max(
                (row.get("verified_paired_best_bid_sum") for row in payload.get("top_verified_bid_pairs", [])
                 if row.get("verified_paired_best_bid_sum") is not None), default=None),
            "research_candidate_count": payload["research_candidate_count"],
            "hypothetical_mint_sell_economic_candidate_count": payload.get("hypothetical_mint_sell_economic_candidate_count", 0),
            "hypothetical_mint_sell_candidate_count": payload.get("hypothetical_mint_sell_candidate_count", 0),
            "main_gate_candidate_count": payload["main_gate_candidate_count"], "decision": payload["decision"],
        })
        for candidate in payload.get("research_candidates", []):
            ledger["candidate_observations"].append({"scan_id": scan_id, "observed_at": payload["scan_completed_at"],
                                                     "structural_path": "buy_yes_and_no", **candidate})
        for candidate in payload.get("hypothetical_mint_sell_candidates", []):
            ledger["candidate_observations"].append({"scan_id": scan_id, "observed_at": payload["scan_completed_at"],
                                                     "structural_path": "hypothetical_mint_and_sell",
                                                     "general_semantic_contract_validated": True,
                                                     "per_market_execution_validated": False,
                                                     "paper_entry_eligible": False, **candidate})
    ledger["updated_at"] = core.now_iso()
    return scan_id


def binary_tokens(market: dict[str, Any]) -> tuple[str, str] | None:
    mapping = {str(outcome).lower(): token for outcome, token in public.token_map(market).items()}
    return (mapping["yes"], mapping["no"]) if set(mapping) == {"yes", "no"} else None


def batch_best_asks(tokens: list[str], chunk_size: int = 500) -> tuple[dict[str, float], list[dict[str, Any]]]:
    prices, requests = {}, []
    unique = list(dict.fromkeys(tokens))
    for index in range(0, len(unique), chunk_size):
        chunk = unique[index:index + chunk_size]
        body = [{"token_id": token, "side": CLOB_PRICE_SIDE_FOR_BEST_ASK} for token in chunk]
        response = public.post_json(f"{public.CLOB_BASE}/prices", body, timeout=30, retries=2)
        if not isinstance(response, dict):
            raise ValueError("batch prices response is not a mapping")
        for token in chunk:
            value = (response.get(token) or {}).get(CLOB_PRICE_SIDE_FOR_BEST_ASK)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if 0 < numeric < 1:
                prices[token] = numeric
        requests.append({"endpoint": f"{public.CLOB_BASE}/prices", "method": "POST", "side": CLOB_PRICE_SIDE_FOR_BEST_ASK, "requested": len(chunk), "returned": sum(token in prices for token in chunk), "status": "ok"})
    return prices, requests


def batch_best_bids(tokens: list[str], chunk_size: int = 500) -> tuple[dict[str, float], list[dict[str, Any]]]:
    prices, requests = {}, []
    unique = list(dict.fromkeys(tokens))
    for index in range(0, len(unique), chunk_size):
        chunk = unique[index:index + chunk_size]
        body = [{"token_id": token, "side": CLOB_PRICE_SIDE_FOR_BEST_BID} for token in chunk]
        response = public.post_json(f"{public.CLOB_BASE}/prices", body, timeout=30, retries=2)
        if not isinstance(response, dict):
            raise ValueError("batch bid prices response is not a mapping")
        for token in chunk:
            value = (response.get(token) or {}).get(CLOB_PRICE_SIDE_FOR_BEST_BID)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if 0 < numeric < 1:
                prices[token] = numeric
        requests.append({"endpoint": f"{public.CLOB_BASE}/prices", "method": "POST",
                         "side": CLOB_PRICE_SIDE_FOR_BEST_BID, "requested": len(chunk),
                         "returned": sum(token in prices for token in chunk), "status": "ok"})
    return prices, requests


def asks(book: dict[str, Any]) -> list[tuple[float, float]]:
    rows = []
    for row in book.get("asks") or []:
        try:
            price, size = float(row["price"]), float(row["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < price < 1 and size > 0:
            rows.append((price, size))
    return sorted(rows)


def bids(book: dict[str, Any]) -> list[tuple[float, float]]:
    rows = []
    for row in book.get("bids") or []:
        try:
            price, size = float(row["price"]), float(row["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < price < 1 and size > 0:
            rows.append((price, size))
    return sorted(rows, reverse=True)


def fill_shares(book: dict[str, Any], shares: float, fee_rate: float) -> dict[str, Any] | None:
    remaining, gross, fees, filled = shares, 0.0, 0.0, 0.0
    levels = []
    for price, size in asks(book):
        take = min(remaining, size)
        if take <= 0:
            continue
        gross += take * price; fees += take * fee_rate * price * (1 - price); filled += take
        levels.append({"price": price, "shares": take}); remaining -= take
        if remaining <= 1e-9:
            break
    if remaining > 1e-8 or filled <= 0:
        return None
    return {"shares": filled, "gross_cost": gross, "fee_cost": fees, "vwap": gross / filled, "best_ask": asks(book)[0][0], "levels": levels}


def paired_execution(yes_book: dict[str, Any], no_book: dict[str, Any], budget_usd: float, fee_rate: float) -> dict[str, Any] | None:
    yes_levels, no_levels = asks(yes_book), asks(no_book)
    if not yes_levels or not no_levels:
        return None
    maximum = min(sum(size for _, size in yes_levels), sum(size for _, size in no_levels))
    def quote(shares: float):
        y, n = fill_shares(yes_book, shares, fee_rate), fill_shares(no_book, shares, fee_rate)
        if y is None or n is None:
            return None
        gross = y["gross_cost"] + n["gross_cost"]; fees = y["fee_cost"] + n["fee_cost"]
        slippage = gross * EXTRA_SLIPPAGE_BPS / 10000.0; total = gross + fees + slippage
        return y, n, gross, fees, slippage, total
    low, high = 0.0, maximum
    for _ in range(60):
        mid = (low + high) / 2; result = quote(mid)
        if result is not None and result[-1] <= budget_usd:
            low = mid
        else:
            high = mid
    if low <= 1e-8:
        return None
    y, n, gross, fees, slippage, total = quote(low)
    cost_per_pair = total / low
    edge = 1.0 - cost_per_pair - SETTLEMENT_RISK_RESERVE_PER_PAIR
    return {
        "equal_shares": low, "budget_usd": budget_usd, "gross_cost": gross,
        "entry_fee_cost": fees, "extra_slippage_cost": slippage, "total_entry_cost": total,
        "cost_per_pair": cost_per_pair, "settlement_risk_reserve_per_pair": SETTLEMENT_RISK_RESERVE_PER_PAIR,
        "net_edge_per_pair": edge, "expected_locked_profit_usd_after_reserve": edge * low,
        "max_price_impact": max(y["vwap"] - y["best_ask"], n["vwap"] - n["best_ask"]),
        "yes": y, "no": n,
    }


def fill_sales(book: dict[str, Any], shares: float, fee_rate: float) -> dict[str, Any] | None:
    remaining, gross, fees, filled = shares, 0.0, 0.0, 0.0
    levels = []
    for price, size in bids(book):
        take = min(remaining, size)
        if take <= 0:
            continue
        gross += take * price; fees += take * fee_rate * price * (1 - price); filled += take
        levels.append({"price": price, "shares": take}); remaining -= take
        if remaining <= 1e-9:
            break
    if remaining > 1e-8 or filled <= 0:
        return None
    return {"shares": filled, "gross_proceeds": gross, "fee_cost": fees,
            "vwap": gross / filled, "best_bid": bids(book)[0][0], "levels": levels}


def hypothetical_mint_and_sell_execution(yes_book: dict[str, Any], no_book: dict[str, Any],
                                         budget_usd: float, fee_rate: float) -> dict[str, Any] | None:
    """Research-only economics; does not assert that a mint/split operation is available."""
    yes_levels, no_levels = bids(yes_book), bids(no_book)
    if not yes_levels or not no_levels or budget_usd <= 0:
        return None
    shares = min(budget_usd, sum(size for _, size in yes_levels), sum(size for _, size in no_levels))
    y, n = fill_sales(yes_book, shares, fee_rate), fill_sales(no_book, shares, fee_rate)
    if y is None or n is None:
        return None
    gross = y["gross_proceeds"] + n["gross_proceeds"]
    fees = y["fee_cost"] + n["fee_cost"]
    slippage = gross * EXTRA_SLIPPAGE_BPS / 10000.0
    net_proceeds = gross - fees - slippage
    edge = net_proceeds / shares - 1.0 - MINT_OPERATION_RISK_RESERVE_PER_PAIR
    return {
        "equal_shares": shares, "collateral_required_usd": shares,
        "gross_sale_proceeds": gross, "sale_fee_cost": fees,
        "extra_slippage_cost": slippage, "net_sale_proceeds": net_proceeds,
        "net_proceeds_per_pair": net_proceeds / shares,
        "mint_operation_risk_reserve_per_pair": MINT_OPERATION_RISK_RESERVE_PER_PAIR,
        "net_edge_per_pair": edge, "expected_locked_profit_usd_after_reserve": edge * shares,
        "max_price_impact": max(y["best_bid"] - y["vwap"], n["best_bid"] - n["vwap"]),
        "yes": y, "no": n,
        "general_semantic_contract_validated": True,
        "per_market_prepared_condition_verified": False,
        "per_market_adapter_route_verified": False,
        "paper_entry_eligible": False,
    }


def scan(snapshot_dir: Path, top_verify: int, budget_usd: float) -> dict[str, Any]:
    semantic_contract = load_ctf_semantic_contract()
    verification = public.verify_manifest(snapshot_dir); manifest = core.read_json(snapshot_dir / "snapshot-manifest.json")
    if verification.get("status") != "pass" or manifest.get("data_status") != "ok" or manifest.get("terminal_cursor_proven") is not True:
        raise ValueError("binary pair scan requires complete verified live snapshot")
    markets = core.read_json(snapshot_dir / "markets.json"); binary = []
    for market in markets:
        tokens = binary_tokens(market)
        if tokens:
            binary.append((market, tokens))
    tokens = [token for _, pair in binary for token in pair]
    captured_from = datetime.now(timezone.utc)
    price_map, requests = batch_best_asks(tokens)
    bid_map, bid_requests = batch_best_bids(tokens); requests.extend(bid_requests)
    captured_to = datetime.now(timezone.utc)
    rows = []
    for market, (yes_token, no_token) in binary:
        yes, no = price_map.get(yes_token), price_map.get(no_token)
        rows.append({
            "condition_id": str(market.get("conditionId") or ""), "market_id": str(market.get("id") or ""),
            "question": market.get("question"), "yes_token_id": yes_token, "no_token_id": no_token,
            "negative_risk": bool(market.get("negRisk")),
            "yes_best_ask": yes, "no_best_ask": no,
            "paired_best_ask_sum": yes + no if yes is not None and no is not None else None,
            "yes_best_bid": bid_map.get(yes_token), "no_best_bid": bid_map.get(no_token),
            "paired_best_bid_sum": (bid_map[yes_token] + bid_map[no_token]
                                     if yes_token in bid_map and no_token in bid_map else None),
        })
    complete = [row for row in rows if row["paired_best_ask_sum"] is not None]
    complete.sort(key=lambda row: row["paired_best_ask_sum"]); verification_rows = complete[:top_verify]
    complete_bids = [row for row in rows if row["paired_best_bid_sum"] is not None]
    complete_bids.sort(key=lambda row: row["paired_best_bid_sum"], reverse=True)
    bid_verification_rows = complete_bids[:top_verify]
    combined_verification = list({(row["yes_token_id"], row["no_token_id"]): row
                                  for row in verification_rows + bid_verification_rows}.values())
    verify_tokens = [token for row in combined_verification for token in (row["yes_token_id"], row["no_token_id"])]
    books_payload = public.post_json(f"{public.CLOB_BASE}/books", [{"token_id": token} for token in verify_tokens], timeout=30, retries=2) if verify_tokens else []
    books = {str(row.get("asset_id")): row for row in books_payload if isinstance(row, dict) and row.get("asset_id")}
    requests.append({"endpoint": f"{public.CLOB_BASE}/books", "method": "POST", "requested": len(verify_tokens), "returned": len(books), "status": "ok"})
    research_candidates = []
    hypothetical_mint_sell_economic_candidates = []
    hypothetical_mint_sell_candidates = []
    for row in combined_verification:
        ybook, nbook = books.get(row["yes_token_id"]) or {}, books.get(row["no_token_id"]) or {}
        yasks, nasks = asks(ybook), asks(nbook); yask = yasks[0][0] if yasks else None; nask = nasks[0][0] if nasks else None
        ybids, nbids = bids(ybook), bids(nbook); ybid = ybids[0][0] if ybids else None; nbid = nbids[0][0] if nbids else None
        row["verified_yes_best_ask"] = yask; row["verified_no_best_ask"] = nask
        row["verified_paired_best_ask_sum"] = yask + nask if yask is not None and nask is not None else None
        row["verified_yes_best_bid"] = ybid; row["verified_no_best_bid"] = nbid
        row["verified_paired_best_bid_sum"] = ybid + nbid if ybid is not None and nbid is not None else None
        row["price_endpoint_matches_book"] = bool(yask is not None and nask is not None and abs(yask - row["yes_best_ask"]) <= .01 and abs(nask - row["no_best_ask"]) <= .01)
        row["bid_price_endpoint_matches_book"] = bool(ybid is not None and nbid is not None and
                                                        abs(ybid - (row["yes_best_bid"] or 0)) <= .01 and
                                                        abs(nbid - (row["no_best_bid"] or 0)) <= .01)
        ask_interesting = row["verified_paired_best_ask_sum"] is not None and row["verified_paired_best_ask_sum"] < 1
        bid_interesting = row["verified_paired_best_bid_sum"] is not None and row["verified_paired_best_bid_sum"] > 1
        if ask_interesting or bid_interesting:
            info = public.get_json(f"{public.CLOB_BASE}/clob-markets/{row['condition_id']}", timeout=20, retries=2)
            fd = info.get("fd") or {}; fee_rate = float(fd.get("r") or 0); row["fee_contract"] = fd
            if ask_interesting:
                execution = paired_execution(ybook, nbook, budget_usd, fee_rate); row["paired_execution"] = execution
                if execution and execution["net_edge_per_pair"] >= MIN_RESEARCH_NET_EDGE_PER_PAIR and execution["max_price_impact"] <= .01:
                    research_candidates.append(row)
            if bid_interesting:
                mint_sell = hypothetical_mint_and_sell_execution(ybook, nbook, budget_usd, fee_rate)
                row["hypothetical_mint_and_sell_execution"] = mint_sell
                if mint_sell and mint_sell["net_edge_per_pair"] >= MIN_RESEARCH_NET_EDGE_PER_PAIR and mint_sell["max_price_impact"] <= .01:
                    hypothetical_mint_sell_economic_candidates.append(row)
                    condition_proof = verify_prepared_binary_condition(row["condition_id"], semantic_contract)
                    route_name = "negative_risk" if row["negative_risk"] else "standard"
                    adapter = (semantic_contract.get("market_routes") or {}).get(route_name, {}).get("collateral_adapter")
                    mint_sell.update({
                        "per_market_prepared_condition_verified": condition_proof["prepared_binary_condition"],
                        "per_market_adapter_route_verified": bool(adapter),
                        "adapter_route": route_name, "collateral_adapter": adapter,
                        "condition_proof": condition_proof,
                    })
                    if condition_proof["prepared_binary_condition"] and adapter:
                        hypothetical_mint_sell_candidates.append(row)
    main_gate = [row for row in research_candidates if row["paired_execution"]["net_edge_per_pair"] >= MIN_MAIN_GATE_EDGE_PER_PAIR]
    return {
        "schema_version": "polymarket-binary-pair-arbitrage-scan-v2", "created_at": core.now_iso(),
        "source_snapshot_created_at": manifest.get("created_at"), "source_snapshot_manifest_sha256": file_sha(snapshot_dir / "snapshot-manifest.json"),
        "scan_started_at": captured_from.isoformat(), "scan_completed_at": captured_to.isoformat(),
        "binary_markets_scanned": len(binary), "tokens_requested": len(binary) * 2,
        "complete_top_of_book_pairs": len(complete), "missing_top_of_book_pairs": len(binary) - len(complete),
        "complete_top_of_book_bid_pairs": len(complete_bids),
        "batch_request_count": len(requests),
        "price_side_contract": "SELL=best ask and BUY=best bid empirically verified against full order book",
        "top_verified_pairs": verification_rows, "research_candidate_count": len(research_candidates),
        "research_candidates": research_candidates, "main_gate_candidate_count": len(main_gate),
        "main_gate_candidates": main_gate, "minimum_research_net_edge_per_pair": MIN_RESEARCH_NET_EDGE_PER_PAIR,
        "minimum_main_gate_edge_per_pair": MIN_MAIN_GATE_EDGE_PER_PAIR,
        "top_verified_bid_pairs": bid_verification_rows,
        "hypothetical_mint_sell_economic_candidate_count": len(hypothetical_mint_sell_economic_candidates),
        "hypothetical_mint_sell_candidate_count": len(hypothetical_mint_sell_candidates),
        "hypothetical_mint_sell_candidates": hypothetical_mint_sell_candidates,
        "ctf_semantic_contract_schema_version": semantic_contract["schema_version"],
        "ctf_semantic_contract_sha256": file_sha(CTF_SEMANTIC_CONTRACT),
        "ctf_official_source_urls": [row["url"] for row in semantic_contract["official_sources"]],
        "mint_sell_general_semantic_contract_validated": True,
        "mint_sell_per_market_execution_validated": False,
        "mint_sell_promotion_blocked_reason": "candidate-specific prepared-condition proof, adapter route and forward leg-risk validation required",
        "decision": ("research_candidate_present_requires_forward_leg_risk_validation" if research_candidates else
                     "hypothetical_mint_sell_edge_semantically_blocked" if hypothetical_mint_sell_candidates else
                     "cash_no_executable_binary_pair_dislocation"),
        "paper_entry_eligible": False, "paper_estimates_emitted": False, "main_paper_ledger_mutated": False,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
    }


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Polymarket Binary Pair Structural Scan", "",
        f"- Decision: `{payload['decision']}`", f"- Binary markets scanned: {payload['binary_markets_scanned']}",
        f"- Complete paired top-of-book prices: {payload['complete_top_of_book_pairs']}",
        f"- Research / main-gate candidates: {payload['research_candidate_count']} / {payload['main_gate_candidate_count']}", "",
        f"- Hypothetical mint-and-sell economic / condition-proof candidates: {payload.get('hypothetical_mint_sell_economic_candidate_count', 0)} / {payload.get('hypothetical_mint_sell_candidate_count', 0)}",
        f"- Mint/split general semantics / per-market execution validated: `{str(payload.get('mint_sell_general_semantic_contract_validated')).lower()}` / `{str(payload.get('mint_sell_per_market_execution_validated')).lower()}`", "",
        f"- Append-only research scan observations: {payload.get('research_ledger_scan_count')}",
        f"- Protected main artifacts unchanged: `{str(payload.get('protected_artifacts_unchanged')).lower()}`", "",
        "| Question | YES ask | NO ask | Sum | Verified sum |", "|---|---:|---:|---:|---:|",
    ]
    for row in payload["top_verified_pairs"][:20]:
        lines.append(f"| {row['question']} | {row['yes_best_ask']} | {row['no_best_ask']} | {row['paired_best_ask_sum']} | {row.get('verified_paired_best_ask_sum')} |")
    lines.extend(["", "Equal-share YES+NO pays $1 gross only after valid binary resolution. The reverse mint-and-sell calculation is explicitly hypothetical until official complete-set mint/split availability and operational semantics are traceably validated. Both paths withhold paper entry and never place either leg.", ""])
    return "\n".join(lines)


def self_test() -> dict[str, Any]:
    market = {"outcomes": '["Yes","No"]', "clobTokenIds": '["y","n"]'}
    assert binary_tokens(market) == ("y", "n")
    book_y = {"asks": [{"price": ".40", "size": "100"}]}; book_n = {"asks": [{"price": ".50", "size": "100"}]}
    execution = paired_execution(book_y, book_n, 75, .04)
    assert execution and execution["net_edge_per_pair"] > 0 and execution["equal_shares"] > 0
    no_edge = paired_execution({"asks": [{"price": ".51", "size": "100"}]}, {"asks": [{"price": ".52", "size": "100"}]}, 75, .04)
    assert no_edge and no_edge["net_edge_per_pair"] < 0
    mint_sell = hypothetical_mint_and_sell_execution(
        {"bids": [{"price": ".55", "size": "100"}]},
        {"bids": [{"price": ".55", "size": "100"}]}, 75, .04)
    assert mint_sell and mint_sell["net_edge_per_pair"] > 0 and mint_sell["paper_entry_eligible"] is False
    assert mint_sell["general_semantic_contract_validated"] is True and mint_sell["per_market_prepared_condition_verified"] is False
    semantic = load_ctf_semantic_contract(); assert semantic["general_semantics_validated"] is True
    ledger = new_ledger(); assert ledger["paper_estimates_emitted"] is False and ledger["main_paper_ledger_mutated"] is False
    return {"status": "pass", "tests": ["binary_token_contract", "equal_share_execution", "fee_slippage_reserve", "negative_edge_rejected", "official_general_ctf_semantics", "candidate_specific_mint_sell_block", "isolated_research_ledger"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--snapshot-dir", default=str(ROOT / "cache/current_validation_snapshot"))
    parser.add_argument("--top-verify", type=int, default=20)
    parser.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--output", default=str(ROOT / "experiments/current-binary-pair-arbitrage.json"))
    parser.add_argument("--report", default=str(ROOT / "reports/CURRENT_BINARY_PAIR_ARBITRAGE.md"))
    args = parser.parse_args()
    protected = [ROOT / "data/paper_ledger.json", ROOT / "experiments/current-crypto-barrier-estimates.json"]
    before = {str(path.relative_to(ROOT)): file_sha(path) for path in protected} if not args.self_test else {}
    payload = self_test() if args.self_test else scan(Path(args.snapshot_dir), args.top_verify, args.budget_usd)
    if not args.self_test:
        ledger = load_ledger(Path(args.ledger)); scan_id = record_observation(ledger, payload); core.write_json(Path(args.ledger), ledger)
        after = {str(path.relative_to(ROOT)): file_sha(path) for path in protected}
        if before != after:
            raise RuntimeError("binary pair scan observed protected artifact mutation")
        payload.update({
            "scan_id": scan_id, "research_ledger_scan_count": len(ledger["scan_observations"]),
            "research_ledger_candidate_observation_count": len(ledger["candidate_observations"]),
            "protected_artifact_sha256_before": before, "protected_artifact_sha256_after": after,
            "protected_artifacts_unchanged": True,
        })
        core.write_json(Path(args.output), payload); Path(args.report).write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
