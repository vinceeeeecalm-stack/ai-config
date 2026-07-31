# Crypto Key Person Intelligence Panel

## Purpose

Manual reports must consume `social_key_person_intel` from `active-alpha-paper-monitor` when present. The panel explains what key crypto people or official entities said, which assets are affected, whether the identity and message are verified, and how the item changes DCA pace, watch priority or risk controls.

This panel sits after `Macro Regime Panel` and before `Asset Micro Thesis Matrix`.

## Required Output

For each relevant item:

| Field | Meaning |
|---|---|
| Who | `person_or_entity`, `role`, `platform` |
| Asset | `asset_tags` |
| What changed | short summary and event type |
| Verification | identity status and confirmation status |
| Market check | price/volume confirmation window and whether still missing |
| Impact | DCA pace, thesis confidence, watch priority, risk alert or no impact |
| Max action | `watch / paper_only / conditional_action / risk_alert` |

## Manual Processing Rules

- Single-source social posts cannot increase real-money size.
- Unverified accounts, reposts, screenshots and rumors are `watch_only`.
- Official security, tokenomics, unlock, delisting or regulatory alerts may pause new DCA until confirmed resolved.
- Positive roadmap or ecosystem news may improve thesis confidence only if official or cross-source confirmed.
- Social items may affect `macro_regime_dca_pace`, `asset_micro_thesis_matrix`, `candidate_deep_dive` and `missing_data_downgrade_panel`, but never bypass price, liquidity, risk or human-confirmation gates.

