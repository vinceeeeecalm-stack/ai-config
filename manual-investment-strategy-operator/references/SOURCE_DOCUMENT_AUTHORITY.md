# V3 Source Authority

## Canonical source

The workspace under `/Users/vincentpan/Documents/investing` is the only editable source for:

- `manual-investment-strategy-operator`
- `active-alpha-paper-monitor`
- `unified-longterm-alpha-investor`

The installed copies under `~/.codex/skills` are generated artifacts. They may only be changed by `scripts/sync_installed_skill.py`; a complete managed-file SHA-256 manifest must match before release.

## Runtime boundary

`reports/`, `experiments/`, `handoffs/`, `cache/`, `paper_trades/`, run guards, task outputs and other generated files are runtime evidence, not skill source. They are excluded from the installed-source manifest.

Historical runtime files must be inventoried and hashed before any archive move. They are never silently deleted or used as a substitute for current portfolio evidence.

## Data authority

`PortfolioStateV2` resolves current values using:

1. newer explicit user trade confirmation;
2. newer account export or screenshot;
3. current override state;
4. legacy ledger, for historical cost, lot and staking context.

The current override fixes `NIGHT=0`, `ENA=0`, `USDT=0`, `SOXL=0` and US-equity cash `$0` until later evidence supersedes it. Legacy quantities or cash cannot override those values.

`EvidenceSnapshotV2` is the only numerical market-evidence object for a run. Generated reports, prose notes and old recommendation records are not market-data authorities.

## Legacy role

`unified-longterm-alpha-investor` is a schema and historical compatibility layer. Manual and Active must not import missing Unified runtime scripts. Legacy ledgers may supply cost and lot context but never newer quantity or cash truth.
