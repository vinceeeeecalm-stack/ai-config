# Tactical Top1 Research Handoff Contract

## Purpose

This contract closes the production gap between a real Active Top3 scan and
Manual's `UniversalInvestmentRunInputV1`. It separates slow research from the
final 60-second market certification so a long web/committee pass cannot reuse
an expired price.

The sequence is fixed:

```text
provisional scanner
→ TacticalResearchRequestV1
→ four-role slow dossier
→ final fresh scanner
→ TacticalMarketCertificationV1
→ same-Top1 rebind
→ UniversalInvestmentRunInputV1
→ ENTER_NOW / WAIT_FOR_ENTRY / NO_TRADE
```

## Pre-research gate

The request is always generated, but `deep_research_recommended=false` when
any of these already fails:

- positive conservative EV;
- at least 30 no-lookahead observations and untouched holdout;
- fees/slippage evidence;
- Profit Factor above 1;
- at least three positive walk-forward windows;
- maximum historical strategy drawdown no worse than the configured 15% gate;
- historical reward/risk at least 2.

This prevents spending minutes on valuation and event research when the same
strategy version cannot authorize the candidate regardless of the narrative.

## Four required roles

Every dossier lists exactly these roles, each with `PASS` or `BLOCKED`:

1. `valuation_fundamentals`
2. `official_catalyst`
3. `market_liquidity`
4. `risk_challenge`

The market role may provide preliminary context inside the slow dossier, but a
separate final market certification is still mandatory after all slow work.

## TacticalResearchDossierV1

Required top-level shape:

```json
{
  "schema_version": "TacticalResearchDossierV1",
  "dossier_id": "stable-id",
  "request_id": "tactical-research-...",
  "provisional_snapshot_id": "snapshot-...",
  "symbol": "ASSETUSDT",
  "prepared_at": "ISO-8601 UTC",
  "valid_until": "no more than 24 hours after prepared_at",
  "roles": [],
  "evidence_items": [],
  "field_evidence": {},
  "valuation_method_class": "adoption_value_capture_scenario",
  "fair_value": {},
  "catalyst": {},
  "downside": {},
  "fundamentals": {},
  "risk_challenge": {},
  "source_failures": [],
  "live_orders_enabled": false,
  "private_api_used": false
}
```

Allowed `valuation_method_class` values:

- `network_fee_capture`
- `protocol_revenue_value_capture`
- `adoption_value_capture_scenario`
- `monetary_premium_scenario`
- `crypto_sum_of_parts`

Historical targets, recent returns, technical resistance, Impulse scores,
social heat, research confidence and risk-adjusted path scores are forbidden
as fair-value methods.

### Evidence items

Each item requires:

```json
{
  "evidence_id": "stable-id",
  "category": "valuation | catalyst | fundamentals | risk | market",
  "source_type": "official | independent_public | onchain_public | market_public",
  "url_or_provider": "public source",
  "as_of": "ISO-8601 UTC",
  "status": "verified | blocked | missing | disputed | stale",
  "public": true,
  "summary": "short source-backed fact"
}
```

`field_evidence` maps `fair_value / catalyst / fundamentals / downside /
risk_challenge` to evidence IDs. Fair value requires at least two public
verified items and rejects `category=market`. A verified catalyst requires an
official source and an exact future `realization_by` inside 1–7 days.

Every crypto fundamental dimension is an object containing `status=verified`,
a short summary and evidence IDs:

- adoption
- real_fees
- value_capture
- supply_dilution
- staking_net_yield
- liquidity
- security
- regulation

### Source failures

Source failures are field-local objects:

```json
{
  "source": "provider-or-url",
  "field": "catalyst",
  "blocking": true,
  "reason": "official calendar unavailable"
}
```

An optional non-blocking source failure remains audit evidence and does not
poison unrelated fields. A blocking failure on valuation, catalyst, risk or a
required fundamental field forces `NO_TRADE`.

## Final rebind

Preserve the original `TacticalResearchRequestV1` used to create the dossier.
After slow research, run a new scanner. The handoff validates the dossier
against the original request, then compares its symbol with the final request:

- same symbol: reusable non-market research may be rebound to the final four
  digests after freshness and provenance validation;
- different symbol: `TOP1_CHANGED`, `NO_TRADE`, no copied evidence, and a new
  request for the final Top1.

## TacticalMarketCertificationV1

Required:

- exact final scanner binding and scanner-signal digest;
- symbol and certified time;
- total scan duration no more than 120 seconds;
- two distinct public price sources;
- each quote no older than 60 seconds and latency no more than 12 seconds;
- price difference no more than 1%;
- certified average within 1% of the final scanner price;
- verified spread/depth, data quality and risk gate;
- `human_confirmation_required=true`;
- `live_orders_enabled=false / private_api_used=false`.

The current plan is rebuilt only after certification. Targets and stops use
the same frozen historical strategy parameters; target 1 must preserve at
least 2:1 reward/risk at the certified current price, and fair-value base must
support target 1.

## Handoff statuses

- `PRE_RESEARCH_GATES_FAILED`
- `EVIDENCE_PENDING`
- `TOP1_CHANGED`
- `EVIDENCE_BLOCKED`
- `MARKET_CERTIFICATION_PENDING`
- `CERTIFICATION_BLOCKED`
- `ARBITRATED_NO_TRADE`
- `ARBITRATED_ACTION`

Only `ARBITRATED_ACTION` can contain `ENTER_NOW` or `WAIT_FOR_ENTRY`. Every
other status must expose `NO_TRADE` and a null decision card. Even an
`ARBITRATED_ACTION` remains a human-confirmation draft and never places an
order.
