# US Equity Premarket and Intraday Stage-0 Shadow Policy

Active owns dynamic discovery, sector/factor evidence, historical comparison, candidate memory and shadow lifecycle. Manual remains the only final user-facing arbitrator.

Use the Public Equity Investing plugin's Alpaca connector for authorized read-only `clock / assets / snapshot / quote / trade / 1m / 5m bars`. Convert each response through `scripts/us_equity_intraday_shadow.py`; never persist credentials, account/order fields, connection metadata or an unrestricted raw payload.

Both premarket `04:00–09:29 ET` and regular session `09:30–15:55 ET` execute the complete shadow funnel. The stage-zero output may use `ENTER_NOW / WAIT_FOR_ENTRY / NO_TRADE` to test semantics, but must always set:

```text
formal_action_eligible=false
production_rule_changed=false
paper_roi_eligible=false
real_money_roi_eligible=false
business_ready_eligible=false
live_orders_enabled=false
private_api_used=false
```

IEX is not SIP. Stage zero rejects SIP even when an input envelope self-asserts success; a later SIP upgrade requires a new governed authority adapter. Plain second-source dictionaries cannot upgrade IEX: only an allowed public-price connector envelope can produce `IEX_CROSS_VERIFIED`. `IEX_ONLY` cannot produce a premarket shadow ENTER; stale or conflicting prices always produce `NO_TRADE`. Rank only after exchange, identity, OTC, $5, halt/delisting and liquidity eligibility. Failure of one symbol or one connector source must not erase healthy candidates.

Every round requires a fresh `USEquityDynamicUniverseAuditV1` covering active Nasdaq, NYSE and NYSE American assets plus unusual-volume, gainers, sector-rotation and news-catalyst discovery. Missing, stale or static-whitelist-only universe proof forces `NO_TRADE`.

Runtime ledgers belong under `runtime/` and remain outside Git. Near-miss, lifecycle and missed-opportunity writes are append-only: equivalent IDs return `NO_UPDATE`, conflicting IDs block. Shadow/Paper outcomes can only feed regression evidence and cannot enter live funds-weighted ROI.
