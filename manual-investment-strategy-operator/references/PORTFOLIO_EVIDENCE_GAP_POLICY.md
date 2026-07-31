# Portfolio Evidence Gap Policy

## Purpose

This policy converts recurring degraded inputs into a concrete import backlog.
It does not authorize trades and does not mutate the ledger. It tells the report
exactly which missing evidence blocks full cost-aware PnL, precise lcETH
valuation, and verified US equity tactical sizing.

## Required Evidence Package

Every full manual report should include a `Portfolio Evidence Gap Panel` after
the `Cost Basis Reconciliation Panel`.

Run:

```bash
python3 manual-investment-strategy-operator/scripts/portfolio_evidence_gap_packager.py --format json
```

Optional template generation:

```bash
python3 manual-investment-strategy-operator/scripts/portfolio_evidence_gap_packager.py --templates-dir manual-investment-strategy-operator/import_templates
```

## What It Tracks

| Category | Examples | Blocking effect |
|---|---|---|
| `cost_basis` | lcETH missing lots, partial ADA/SOL lots | Blocks full cost-aware PnL and tax-lot-aware rotation |
| `staking_redemption` | lcETH conversion ratio, redeemable ETH, unstake delay, fees | Blocks precise lcETH valuation and liquidity sizing |
| `cash_rail` | US broker settled cash/buying power, crypto stablecoin availability | Blocks real sizing; keeps actions conditional/watch |

## Import Templates

The packager defines three template schemas:

- `crypto_lot_import_template.csv`: exchange and wallet lots for ADA/SOL/NIGHT/USDT/ETH dust.
- `lceth_staking_lot_import_template.csv`: lcETH acquisition, staking rewards, conversion ratio, redemption preview and fees.
- `us_equity_cash_rail_template.csv`: broker cash, buying power, unsettled cash, pending orders and permission flags.

Templates are intentionally header-only. Imported rows must come from broker,
exchange, wallet, staking-provider, or user-confirmed source files.

## Report Rules

- Screenshot quantities may update position size, but must not invent cost basis.
- User-stated broker cash may be shown, but remains `degraded_user_stated` until
  a broker export or timestamped screenshot confirms settled cash and buying power.
- lcETH must remain `degraded` for valuation/liquidity until account-specific
  conversion ratio, redeemable underlying, fees and unstake delay are confirmed.
- Crypto DCA cash must distinguish stablecoins already in the crypto rail from
  future planned deposits or filled/open limit orders.
- When the package status is `open_gaps`, full PnL claims and real tactical sizing
  remain capped at `conditional_action_or_watch`.

## After Evidence Is Imported

After the user provides exports or screenshots, rerun:

```bash
python3 manual-investment-strategy-operator/scripts/cost_basis_reconciliation_audit.py --format json
python3 manual-investment-strategy-operator/scripts/portfolio_evidence_gap_packager.py --format json
python3 manual-investment-strategy-operator/scripts/portfolio_comparison_snapshot.py
```

Only after the gaps close may reports upgrade from partial-cost wording to full
cost-aware performance and verified tactical sizing.
