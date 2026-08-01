#!/usr/bin/env python3
"""
Crypto key person social intelligence monitor.

Research/handoff only. It scans configured crypto key people and official
sources, produces structured social intelligence, and never places orders.
API keys are read from environment variables only and are never logged.
"""

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_panel_bridge import active_research_panel_overlay


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(__file__).resolve().parents[1]
CN_TZ = timezone(timedelta(hours=8))
USER_AGENT = "codex-active-alpha-social-intel/1.0"
MAX_SUMMARY_CHARS = 220
OFFICIAL_PAGE_LINK_RE = re.compile(
    r"<a\b[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<text>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)

EVENT_KEYWORDS = {
    "security_risk": ["hack", "exploit", "breach", "incident", "vulnerability", "slash", "compromised"],
    "tokenomics_unlock": ["unlock", "vesting", "emission", "airdrop", "redemption", "thaw", "supply"],
    "listing_delisting": ["listing", "listed", "delist", "delisting", "suspend trading"],
    "regulatory_policy": ["sec", "regulation", "regulatory", "lawsuit", "etf", "approval", "enforcement"],
    "staking_policy": ["staking", "validator", "slashing", "apy", "rewards"],
    "roadmap_upgrade": ["upgrade", "mainnet", "roadmap", "fork", "release", "launch"],
    "ecosystem_partnership": ["partnership", "integrat", "collaboration", "ecosystem", "adoption"],
    "fund_flow": ["inflow", "outflow", "fund flow", "etp", "etf flow", "aum"],
    "founder_confirm_denial": ["confirm", "deny", "false", "clarify", "correction"],
    "market_rumor": ["rumor", "unconfirmed", "allegedly", "reportedly", "leak"],
}

RISK_EVENTS = {"security_risk", "tokenomics_unlock", "listing_delisting", "regulatory_policy"}


def utc_now():
    return datetime.now(timezone.utc)


def parse_dt(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(text.replace("Z", "+0000"), fmt).astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def fetch_json(url, headers=None, timeout=18):
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url, timeout=18):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def clean_text(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))
    return re.sub(r"\s+", " ", text).strip()


def short_summary(text):
    text = clean_text(text)
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    return text[: MAX_SUMMARY_CHARS - 3].rstrip() + "..."


def detect_event_type(text):
    haystack = clean_text(text).lower()
    for event_type, keywords in EVENT_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return event_type
    return "general_commentary"


def match_assets(text, registry_assets):
    haystack = clean_text(text).upper()
    found = [asset for asset in registry_assets if asset.upper() in haystack]
    return found or list(registry_assets)


def credibility_score(entry):
    status = entry.get("verified_identity_status")
    tier = entry.get("credibility_tier")
    role = entry.get("role") or ""
    score = {
        "official_source": 42,
        "known_public_person": 32,
        "manual_verify_required": 18,
        "unverified": 6,
    }.get(status, 8)
    score += {"official": 10, "high": 7, "medium": 3}.get(tier, 0)
    if "official" in role:
        score += 6
    if "regulator" in role or "exchange" in role or "stablecoin" in role:
        score += 4
    return min(score, 60)


def event_score(event_type):
    return {
        "security_risk": 22,
        "tokenomics_unlock": 20,
        "listing_delisting": 20,
        "regulatory_policy": 18,
        "staking_policy": 14,
        "roadmap_upgrade": 14,
        "ecosystem_partnership": 11,
        "fund_flow": 10,
        "founder_confirm_denial": 9,
        "market_rumor": 3,
        "general_commentary": 2,
    }.get(event_type, 2)


def freshness_score(posted_at, now, stale_hours):
    if not posted_at:
        return 4, ["missing_posted_at"]
    age_hours = (now - posted_at).total_seconds() / 3600
    if age_hours > stale_hours:
        return 0, ["stale"]
    if age_hours <= 6:
        return 15, []
    if age_hours <= 24:
        return 10, []
    return 5, []


def source_item_id(seed):
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def make_item(entry, platform, title, body, url, posted_at, source_kind):
    text = " ".join([title or "", body or ""]).strip()
    event_type = detect_event_type(text)
    return {
        "entry": entry,
        "platform": platform,
        "title": clean_text(title),
        "body": clean_text(body),
        "post_url": url,
        "posted_at_dt": posted_at,
        "posted_at": posted_at.isoformat() if posted_at else None,
        "event_type": event_type,
        "asset_tags": match_assets(text, entry.get("asset_tags") or []),
        "source_kind": source_kind,
    }


def fetch_rss_items(entry, lookback_hours):
    if not entry.get("rss_url"):
        return [], None
    try:
        raw = fetch_text(entry["rss_url"])
        root = ET.fromstring(raw)
        now = utc_now()
        items = []
        for node in root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            title = node.findtext("title") or node.findtext("{http://www.w3.org/2005/Atom}title") or ""
            desc = node.findtext("description") or node.findtext("summary") or node.findtext("{http://www.w3.org/2005/Atom}summary") or ""
            link = node.findtext("link") or ""
            atom_link = node.find("{http://www.w3.org/2005/Atom}link")
            if atom_link is not None and atom_link.get("href"):
                link = atom_link.get("href")
            posted = parse_dt(node.findtext("pubDate") or node.findtext("published") or node.findtext("{http://www.w3.org/2005/Atom}updated"))
            if posted and (now - posted).total_seconds() / 3600 > lookback_hours:
                continue
            items.append(make_item(entry, "official_rss", title, desc, link, posted, "official_feed"))
        return items, None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


def official_page_keywords(entry):
    terms = []
    for asset in entry.get("asset_tags") or []:
        terms.append(str(asset).lower())
    for term in entry.get("search_terms") or []:
        terms.extend(str(term).lower().split())
    for keywords in EVENT_KEYWORDS.values():
        terms.extend(keywords)
    return {term.strip().lower() for term in terms if len(term.strip()) >= 3}


def normalize_url(base_url, href):
    if not href or href.startswith(("javascript:", "mailto:", "#")):
        return ""
    return urllib.parse.urljoin(base_url, html.unescape(href))


def fetch_official_page_items(entry, max_items):
    url = entry.get("announcement_url") or entry.get("official_url")
    if not url:
        return [], None
    try:
        raw = fetch_text(url)
        keywords = official_page_keywords(entry)
        seen = set()
        items = []
        for match in OFFICIAL_PAGE_LINK_RE.finditer(raw):
            text = clean_text(match.group("text"))
            if len(text) < 18:
                continue
            haystack = text.lower()
            if keywords and not any(keyword in haystack for keyword in keywords):
                continue
            href = normalize_url(url, match.group("href"))
            if not href or href in seen:
                continue
            seen.add(href)
            items.append(make_item(entry, "official_page", text, "", href, None, "official_page"))
            if len(items) >= max_items:
                break
        if not items:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.IGNORECASE | re.DOTALL)
            if title_match:
                title = clean_text(title_match.group(1))
                if title:
                    items.append(make_item(entry, "official_page", title, "", url, None, "official_page"))
        return items, None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


def fetch_bluesky_items(entry, lookback_hours, max_items):
    terms = entry.get("search_terms") or []
    if not terms:
        return [], None
    try:
        q = terms[0]
        params = urllib.parse.urlencode({"q": q, "limit": min(max_items, 25), "sort": "latest"})
        body = fetch_json(f"https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?{params}")
        now = utc_now()
        items = []
        for post in body.get("posts", []):
            record = post.get("record") or {}
            text = record.get("text") or ""
            posted = parse_dt(record.get("createdAt"))
            if posted and (now - posted).total_seconds() / 3600 > lookback_hours:
                continue
            uri = post.get("uri") or ""
            handle = ((post.get("author") or {}).get("handle") or "").strip()
            url = f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}" if handle and uri else ""
            items.append(make_item(entry, "bluesky", text, "", url, posted, "public_search"))
        return items, None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


def fetch_reddit_items(entry, lookback_hours, max_items):
    terms = entry.get("search_terms") or []
    if not terms:
        return [], None
    try:
        params = urllib.parse.urlencode({"q": terms[0], "sort": "new", "t": "week", "limit": min(max_items, 25)})
        body = fetch_json(f"https://www.reddit.com/search.json?{params}")
        now = utc_now()
        items = []
        for child in ((body.get("data") or {}).get("children") or []):
            data = child.get("data") or {}
            posted = parse_dt(data.get("created_utc"))
            if posted and (now - posted).total_seconds() / 3600 > lookback_hours:
                continue
            url = data.get("url") or ("https://www.reddit.com" + data.get("permalink", ""))
            items.append(make_item(entry, "reddit", data.get("title"), data.get("selftext"), url, posted, "public_search"))
        return items, None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


def fetch_x_items(entry, lookback_hours, max_items):
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        return [], "missing_X_BEARER_TOKEN"
    query_terms = entry.get("search_terms") or [entry.get("handle") or ""]
    query = query_terms[0]
    if entry.get("handle"):
        query = f'from:{entry["handle"]} OR "{query}"'
    try:
        start_time = (utc_now() - timedelta(hours=lookback_hours)).isoformat().replace("+00:00", "Z")
        params = urllib.parse.urlencode({
            "query": query,
            "max_results": max(10, min(max_items, 100)),
            "start_time": start_time,
            "tweet.fields": "created_at,author_id,public_metrics",
        })
        body = fetch_json(
            f"https://api.twitter.com/2/tweets/search/recent?{params}",
            headers={"Authorization": f"Bearer {token}"},
        )
        items = []
        for tweet in body.get("data", [])[:max_items]:
            text = tweet.get("text") or ""
            posted = parse_dt(tweet.get("created_at"))
            url = f"https://x.com/i/web/status/{tweet.get('id')}" if tweet.get("id") else ""
            items.append(make_item(entry, "x", text, "", url, posted, "recent_search"))
        return items, None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


def sample_items(registry):
    by_id = {entry["id"]: entry for entry in registry.get("accounts", [])}
    now = utc_now()
    samples = [
        ("ethereum_foundation_blog", "Ethereum upgrade roadmap update", "Mainnet upgrade and staking parameter update for validators.", "https://example.com/eth-upgrade", now - timedelta(hours=2)),
        ("midnight_network", "Unverified rumor about NIGHT unlock", "Rumor says NIGHT token unlock may be accelerated. No official confirmation.", "https://example.com/night-rumor", now - timedelta(hours=1)),
        ("binance_official", "Security incident notice", "Security incident under investigation; deposits and withdrawals for affected asset suspended.", "https://example.com/security-risk", now - timedelta(minutes=30)),
    ]
    items = []
    for entry_id, title, body, url, posted in samples:
        entry = by_id.get(entry_id) or next(iter(by_id.values()))
        items.append(make_item(entry, "self_test_fixture", title, body, url, posted, "fixture"))
    return items


def score_items(raw_items, stale_hours):
    now = utc_now()
    grouped = {}
    for item in raw_items:
        for asset in item.get("asset_tags") or []:
            grouped.setdefault((asset, item["event_type"]), set()).add(item["platform"])

    output = []
    for item in raw_items:
        entry = item["entry"]
        fresh_points, risk_flags = freshness_score(item.get("posted_at_dt"), now, stale_hours)
        if entry.get("verified_identity_status") in ("manual_verify_required", "unverified"):
            risk_flags.append("identity_not_fully_verified")
        if item["event_type"] == "market_rumor":
            risk_flags.append("unverified_rumor")

        source_points = credibility_score(entry)
        if item.get("source_kind") in ("public_search", "recent_search"):
            source_points = min(source_points, 22)
            risk_flags.append("public_search_not_official_identity")
        relevance_points = min(25, 10 + len(item.get("asset_tags") or []) * 5)
        event_points = event_score(item["event_type"])
        confirmation_status = "single_source_unconfirmed"
        cross_source = any(len(grouped.get((asset, item["event_type"]), set())) >= 2 for asset in item.get("asset_tags") or [])
        if cross_source:
            confirmation_status = "cross_source_confirmed"
        if entry.get("verified_identity_status") == "official_source" and item.get("source_kind") in ("official_feed", "official_page", "fixture"):
            confirmation_status = "official_confirmed"
        confirmation_points = 10 if confirmation_status in ("cross_source_confirmed", "official_confirmed") else 0
        score = min(100, source_points + relevance_points + event_points + fresh_points + confirmation_points)

        if item.get("post_url", "").startswith("https://i.redd.it"):
            risk_flags.append("screenshot_or_media_only")

        if "stale" in risk_flags:
            max_action = "watch"
        elif "public_search_not_official_identity" in risk_flags and confirmation_status != "cross_source_confirmed":
            max_action = "watch"
        elif item["event_type"] in RISK_EVENTS and source_points >= 35:
            max_action = "risk_alert"
        elif "unverified_rumor" in risk_flags or "identity_not_fully_verified" in risk_flags:
            max_action = "watch"
        elif confirmation_status in ("cross_source_confirmed", "official_confirmed") and score >= 70:
            max_action = "conditional_action"
        elif score >= 55:
            max_action = "paper_only"
        else:
            max_action = "watch"

        seed = "|".join([
            entry.get("id", ""),
            item.get("platform", ""),
            item.get("post_url", ""),
            item.get("title", ""),
            item.get("posted_at") or "",
        ])
        output.append({
            "intel_id": "social-" + source_item_id(seed),
            "platform": item["platform"],
            "person_or_entity": entry.get("person_or_entity"),
            "role": entry.get("role"),
            "verified_identity_status": entry.get("verified_identity_status"),
            "post_url": item.get("post_url"),
            "posted_at": item.get("posted_at"),
            "captured_at": now.isoformat(),
            "asset_tags": item.get("asset_tags") or [],
            "event_type": item["event_type"],
            "summary": short_summary(" ".join([item.get("title") or "", item.get("body") or ""])),
            "source_credibility_score": source_points,
            "market_relevance_score": relevance_points,
            "social_intel_score_points": int(score),
            "confirmation_status": confirmation_status,
            "price_reaction_window": "requires 1h/4h/24h price-volume confirmation before any manual action",
            "recommended_max_action": max_action,
            "risk_flags": sorted(set(risk_flags)),
            "forecast_probability_pct": None,
            "live_orders_enabled": False,
        })
    output.sort(key=lambda row: (row["recommended_max_action"] == "risk_alert", row["social_intel_score_points"]), reverse=True)
    return output


def write_report(path, payload):
    lines = [
        "# Crypto Key Person Intelligence",
        "",
        f"- Generated at: `{payload['created_at']}`",
        f"- Scan status: `{payload['scan_status']}`",
        f"- Scope: `{payload['scope']}`",
        f"- Live orders enabled: `{payload['live_orders_enabled']}`",
        f"- Research committee degraded: `{payload.get('research_committee_degraded')}`",
        f"- Max allowed action: `{payload.get('max_allowed_action')}`",
        f"- Research panel missing reason: `{payload.get('research_panel_missing_reason') or 'none'}`",
        "",
        "| Action | Score | Entity | Assets | Event | Confirmation | Summary |",
        "|---|---:|---|---|---|---|---|",
    ]
    for item in payload["social_key_person_intel"]:
        summary = (item.get("summary") or "").replace("|", "/")
        lines.append(
            f"| {item['recommended_max_action']} | {item['social_intel_score_points']} | "
            f"{item['person_or_entity']} | {', '.join(item.get('asset_tags') or [])} | "
            f"{item['event_type']} | {item['confirmation_status']} | {summary} |"
        )
    if not payload["social_key_person_intel"]:
        lines.append("| watch | 0 | none | - | none | missing | No current key-person items found. |")
    if payload.get("source_errors"):
        lines.extend(["", "## Source Errors", ""])
        for error in payload["source_errors"][:20]:
            lines.append(f"- `{error['source']}`: {error['status']}")
    lines.extend([
        "",
        "Social intelligence is a candidate input only. It cannot trigger real trades without manual review, price/volume confirmation, liquidity checks and risk gates.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(ROOT / "config" / "crypto_key_person_registry.json"))
    ap.add_argument("--output-root", default=str(ROOT))
    ap.add_argument("--symbols", default="BTC,ETH,SOL,ADA,NIGHT,USDC,COIN,CRCL")
    ap.add_argument("--lookback-hours", type=int, default=48)
    ap.add_argument("--stale-hours", type=int, default=72)
    ap.add_argument("--max-items-per-source", type=int, default=10)
    ap.add_argument("--max-network-queries", type=int, default=30)
    ap.add_argument("--external-agent-outputs-json", help="optional externally collected 6+ subagent output JSON")
    ap.add_argument("--json-only", action="store_true")
    ap.add_argument("--self-test-fixture", action="store_true")
    args = ap.parse_args()

    registry_path = Path(args.registry)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    target_assets = {asset.strip().upper() for asset in args.symbols.split(",") if asset.strip()}
    accounts = [
        entry for entry in registry.get("accounts", [])
        if target_assets.intersection({asset.upper() for asset in entry.get("asset_tags", [])})
    ]

    source_errors = []
    raw_items = []
    query_count = 0
    if args.self_test_fixture:
        raw_items.extend(sample_items(registry))
    else:
        for entry in accounts:
            if query_count >= args.max_network_queries:
                break
            rss_items, error = fetch_rss_items(entry, args.lookback_hours)
            query_count += 1 if entry.get("rss_url") else 0
            raw_items.extend(rss_items[: args.max_items_per_source])
            if error:
                source_errors.append({"source": f"rss:{entry['id']}", "status": error})

            if query_count < args.max_network_queries and (not rss_items or error):
                page_items, page_error = fetch_official_page_items(entry, args.max_items_per_source)
                query_count += 1 if entry.get("announcement_url") or entry.get("official_url") else 0
                raw_items.extend(page_items[: args.max_items_per_source])
                if page_error:
                    source_errors.append({"source": f"official_page:{entry['id']}", "status": page_error})

            if query_count < args.max_network_queries:
                x_items, error = fetch_x_items(entry, args.lookback_hours, args.max_items_per_source)
                query_count += 1
                raw_items.extend(x_items[: args.max_items_per_source])
                if error and error != "missing_X_BEARER_TOKEN":
                    source_errors.append({"source": f"x:{entry['id']}", "status": error})

            if query_count < args.max_network_queries:
                bsky_items, error = fetch_bluesky_items(entry, args.lookback_hours, args.max_items_per_source)
                query_count += 1
                raw_items.extend(bsky_items[: args.max_items_per_source])
                if error:
                    source_errors.append({"source": f"bluesky:{entry['id']}", "status": error})

            if query_count < args.max_network_queries:
                reddit_items, error = fetch_reddit_items(entry, args.lookback_hours, args.max_items_per_source)
                query_count += 1
                raw_items.extend(reddit_items[: args.max_items_per_source])
                if error:
                    source_errors.append({"source": f"reddit:{entry['id']}", "status": error})

            time.sleep(0.05)

    intel = score_items(raw_items, args.stale_hours)
    now = utc_now()
    day = now.astimezone(CN_TZ).date().isoformat()
    scan_status = "ok" if intel else ("partial" if source_errors else "degraded")
    payload = {
        "handoff_id": f"{day.replace('-', '')}-crypto-key-person-intel-001",
        "created_at": now.isoformat(),
        "source_skill": "active-alpha-paper-monitor",
        "target_skill": "manual-investment-strategy-operator",
        "candidate_type": "social_key_person_intel",
        "asset_class": "crypto",
        "scope": registry.get("scope", "crypto_only"),
        "registry_version": registry.get("version"),
        "registry_path": str(registry_path),
        "private_api_keys_used": bool(os.environ.get("X_BEARER_TOKEN") or os.environ.get("NEYNAR_API_KEY")),
        "private_api_keys_logged": False,
        "live_orders_enabled": False,
        "research_panel_missing": True,
        "research_panel_missing_reason": "subagent research committee is not invoked inside this local social monitor; social intelligence is watch/risk context only",
        "research_committee_degraded": True,
        "max_allowed_action": "watch",
        "requires_manual_review": True,
        "scan_status": scan_status,
        "data_quality_status": "social_context_only_verify_before_action",
        "social_key_person_intel": intel,
        "source_errors": source_errors,
        "decision_rule": "social intelligence cannot trigger execute_now; manual skill must re-check price, liquidity, risk and human confirmation",
    }
    payload.update(
        active_research_panel_overlay(
            args.external_agent_outputs_json,
            payload["handoff_id"],
            "subagent research committee is not invoked inside this local social monitor; social intelligence is watch/risk context only",
        )
    )

    if args.json_only:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    root = Path(args.output_root)
    handoff_dir = root / "handoffs"
    report_dir = root / "reports"
    experiment_dir = root / "experiments"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    experiment_dir.mkdir(parents=True, exist_ok=True)
    handoff_path = handoff_dir / f"{day}-social-key-person-intel-handoff.json"
    report_path = report_dir / f"{day}-social-key-person-intel.md"
    experiment_path = experiment_dir / f"{day.replace('-', '')}-social-key-person-intel.json"
    handoff_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    experiment_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(report_path, payload)
    print(json.dumps({
        "handoff": str(handoff_path.relative_to(WORKSPACE_ROOT)),
        "report": str(report_path.relative_to(WORKSPACE_ROOT)),
        "experiment": str(experiment_path.relative_to(WORKSPACE_ROOT)),
        "intel_count": len(intel),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
