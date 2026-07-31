# Historical Cycle and Event Conditioning Policy

## Purpose

This policy prevents a current investment decision from being based on a
single fresh snapshot, a generic catalyst label, or a future trigger the user
may not see in time. Every formal US-equity or crypto candidate must condition
the current state on comparable historical paths and relevant macro, earnings,
or regulatory events before the report says whether the asset is investable
now.

The user-facing primary decision must be one of:

- `enter_now`
- `small_entry_now`
- `do_not_enter_now`

This is a theoretical market decision at the frozen price and timestamp.
`execution_action` remains the real-money ceiling. For example, a candidate can
be `small_entry_now` in theory but `no_deploy` in practice when settled cash is
zero. Never invent buying power or an asset sale.

Future prices or signals may appear only under “what would change the next
review.” They must not replace the current direct decision.

## Frozen Current State Vector

Freeze the state vector before observing the later outcome. Record:

- price and timestamp;
- 1h/4h/1d/5d/20d/60d/90d returns when the venue supports them;
- distance from 20/50/200-day averages and recent high/low;
- realized volatility or ATR and recent gap distribution;
- current volume versus 20-day median;
- spread, depth and slippage proxy;
- cross-asset regime: DXY, 2Y, 10Y, VIX, QQQ and BTC/ETH;
- asset-specific derivatives and flow fields.

For crypto, attempt funding, open interest, OI change, liquidations, taker
imbalance, ETF/fund flow and stablecoin/chain flow. For US equities, attempt
options IV, implied move, skew, put/call volume or OI, strike concentration and
gamma exposure where available. Missing derivatives data is a real limitation,
not a neutral value.

## Historical Cycle Conditioning

Define the analog-selection rule before measuring the future path. At minimum,
condition on:

- trend bucket;
- volatility bucket;
- volume bucket;
- price extension or drawdown bucket;
- derivatives positioning bucket;
- broad risk regime.

For each reproducible sample, calculate:

- target-first, stop-first and unresolved counts;
- median forward return;
- median and adverse-tail MFE/MAE;
- median time to target and time to stop;
- frequency of a material pullback before target;
- frequency that waiting missed the upside;
- severe gap or liquidation path when relevant.

Do not choose only successful analogs, only one market cycle, or only dates that
look similar after seeing the result. Document data start/end, feature
definitions, filters, target, stop and horizon.

Sample rules:

- `n < 10`: block `enter_now` and `small_entry_now`; output judgment-only range.
- `10 <= n < 20`: use a probability range at least 15 percentage points wide;
  maximum action is `watch`, `paper_only` or theoretical `do_not_enter_now`.
- `n >= 20`: a measured base rate may be published, but qualitative
  adjustments remain separate and normally cannot exceed 10 percentage points
  in total without a calibrated model.

## Macro and Discrete-Event Conditioning

When a material event is within ten trading days, a generic “FOMC risk” or
“earnings catalyst” label is insufficient.

### FOMC and Macro Events

Record:

- official event time;
- expected decision and source;
- whether the decision would be the first, second, or later consecutive
  hold/cut/hike;
- surprise versus the prevailing expectation;
- statement, projections and press-conference availability;
- pre-event DXY, 2Y, 10Y, VIX, QQQ, BTC/ETH trend and liquidity regime.

Historical analogs must match the expected decision and consecutive-decision
state before further matching the pre-event regime. Measure at least the
`-5d`, `-1d`, event-day, `+1d`, `+3d`, `+5d` and `+10d` windows where data
exists, plus target-first/stop-first order. Never pool all FOMC meetings into
one unconditional average.

### Earnings

Record the confirmed release time, company guidance, consensus where available,
recent estimate revisions, prior company earnings gaps and drift, sector-season
analogs, current options-implied move and the historical realized move. Distinguish:

- probability the operating result beats the expectation bar;
- probability the stock reaches the target before the stop;
- probability a good result is already priced in.

### Regulatory or Legislative Events

Record the exact stage: introduced, committee text/markup, chamber passage,
conference, presidential signature or effective rule. Measure the historical
or process base rate to the next stage where available. A draft, press release,
or scheduled markup is not equivalent to enacted law.

### New-Chain and Platform Launch Events

Record the official announcement, public-mainnet time, chain ID, canonical
bridge/DEX/wallet support and the first liquid native pools. Fan the event out
to company equity, infrastructure assets, chain activity and DEX-only native
assets. Do not wait for a CEX listing before creating an observation candidate.

For a native asset, state whether the link is official, infrastructural or only
narrative. Measure liquidity, unique-trader breadth, volume-to-liquidity,
holder/deployer concentration, sellability, taxes, owner privileges and LP
status. Missing security evidence limits the current decision to
`do_not_enter_now` even if the observation action remains `watch`.

## Buy-Now Versus Wait Test

Every DCA and tactical candidate must compare:

- expected benefit of buying at the frozen current price;
- expected additional discount from waiting;
- probability the waiting order never fills;
- missed-upside frequency;
- staking or other time-in-market cost;
- event and gap risk during the wait.

The report must state whether buying now or waiting has the better
probability-weighted path. A future trigger can describe the next review but
cannot hide the current conclusion.

## Probability Provenance

Keep three layers separate:

1. `FACT`: source-backed current or historical observation.
2. `DERIVED`: reproducible calculation from the frozen data.
3. `JUDGMENT`: explicit adjustment for unmodeled catalysts, data gaps or
   portfolio fit.

Publish the measured base rate before adjustments. Show target-first and
stop-first separately. Do not convert ranking, research confidence, evidence
quality, social heat or execution readiness into a price probability.

## Current Direct Decision Gate

Every formal candidate must include:

- `current_direct_decision`;
- `current_state_already_evaluated=true`;
- `primary_action_is_future_trigger=false`;
- `decision_price_ceiling`;
- `decision_valid_until`;
- `historical_cycle_conditioning`;
- `macro_event_conditioning`;
- `derivatives_and_flow`;
- `buy_now_vs_wait`.

Rules:

- `enter_now` or `small_entry_now` requires a current verified price, an
  unexpired decision window, a numeric maximum acceptable current price, and no
  data-quality, downside, capital, sample-size or cash-independent risk block.
- `do_not_enter_now` means no new order now. The report may show the next review
  time and what evidence could change the later decision, but those items are
  not today’s entry instruction.
- Theoretical entry approval never overrides settled cash. If the rail has zero
  cash, `current_deployable_cash_usd=0` and `execution_action=no_deploy`.
- A changed current state requires a new timestamped recommendation. Do not
  silently change the frozen entry, probability or analog sample.

Validate this contract with:

```bash
python3 scripts/historical_cycle_event_conditioning_gate.py --input candidate.json
```

Then run the ordinary execution-calendar, downside/capital, Research Committee,
probability, portfolio and human-confirmation gates.

## Controlled Learning

Daily prices and one-off event results stay in the report or recommendation
outcome review. Promote only reusable feature definitions, source requirements,
sample rules, gates, tests and error handling.

Every future rule change must record:

`omission -> root cause -> general rule -> affected files -> evidence count ->
validation result -> rollback plan`.
