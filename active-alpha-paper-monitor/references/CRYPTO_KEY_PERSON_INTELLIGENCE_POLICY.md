# Crypto Key Person Intelligence Policy

## Purpose

This policy adds a crypto-only social intelligence layer. It monitors key people, official accounts, exchange/stablecoin entities, fund-flow analysts and crypto policy accounts for information that may change narrative, risk, DCA pace or paper candidates.

Social intelligence is never a standalone real-trade trigger. It can raise `watch`, `paper_only`, `conditional_action` or `risk_alert`; manual review and the normal price, liquidity, data-quality and risk gates still apply.

## Registry

Use `config/crypto_key_person_registry.json` as the source of monitored entities.

Each entity should include:

- `id`
- `platform`
- `handle`
- `person_or_entity`
- `role`
- `verified_identity_status`
- `asset_tags`
- `credibility_tier`
- `official_url`
- `rss_url` when available
- `search_terms`
- `last_verified_at`

Unverified or manually verified identities default to `watch_only` unless confirmed by an official source.

## Source Priority

| Source | Use | Notes |
|---|---|---|
| Official project blog/RSS/site | Roadmap, upgrades, tokenomics, security | Highest credibility |
| Official announcement page fallback | Project, exchange, stablecoin, fund-flow or regulator page titles when RSS/social sources fail | Lower freshness quality if no timestamp is available |
| X Recent Search | Key-person posts and official account posts | Optional `X_BEARER_TOKEN`; never log token |
| Bluesky searchPosts | Public social posts | Public AppView first, degraded if unavailable |
| Farcaster/Neynar | Crypto social context | Optional `NEYNAR_API_KEY`; never log key |
| Reddit crypto search | Community reaction and rumor pressure | Context only, noisy by default |

## Event Types

- `roadmap_upgrade`
- `tokenomics_unlock`
- `listing_delisting`
- `security_risk`
- `regulatory_policy`
- `staking_policy`
- `ecosystem_partnership`
- `fund_flow`
- `founder_confirm_denial`
- `market_rumor`
- `general_commentary`

## Scoring

The monitor outputs `social_intel_score_points`, not probability.

Core dimensions:

- identity credibility
- asset relevance
- event type severity
- freshness
- cross-source confirmation
- price/volume confirmation placeholder
- historical outcome score when available

`forecast_probability_pct` must remain null unless a separate historical base-rate or paper workflow provides it.

## Action Limits

| Condition | Max action |
|---|---|
| Unverified identity, repost, screenshot or rumor | `watch` |
| Single-source official update without market confirmation | `watch` or `conditional_action` |
| Official security, tokenomics, delisting or regulatory risk | `risk_alert` |
| Cross-source confirmed social catalyst plus price/volume confirmation | `paper_only` or `conditional_action` |
| Stale social post | `watch` |

No social item can output `execute_now`.

## Handoff

Social intelligence should be written under `social_key_person_intel` in handoff payloads and can also appear as `candidate_type: social_key_person_intel`.

Required fields:

- `intel_id`
- `platform`
- `person_or_entity`
- `role`
- `verified_identity_status`
- `post_url`
- `posted_at`
- `captured_at`
- `asset_tags`
- `event_type`
- `summary`
- `source_credibility_score`
- `market_relevance_score`
- `social_intel_score_points`
- `confirmation_status`
- `price_reaction_window`
- `recommended_max_action`
- `risk_flags`
