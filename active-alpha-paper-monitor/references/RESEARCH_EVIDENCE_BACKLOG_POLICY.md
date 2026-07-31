# Research Evidence Backlog Policy

This policy mirrors the manual skill's evidence backlog process for active monitor handoffs.

When an active monitor handoff or a manual report receives external subagent outputs but the Research Committee quality audit fails, the system should generate a research evidence backlog before the next market scan.

Use the manual skill tool:

```bash
python3 manual-investment-strategy-operator/scripts/research_evidence_backlog_builder.py \
  --quality-audit-json manual-investment-strategy-operator/experiments/{quality_audit}.json \
  --output manual-investment-strategy-operator/experiments/{run_id}-research-evidence-backlog.json \
  --markdown-output manual-investment-strategy-operator/reports/{run_id}-research-evidence-backlog.md
```

The backlog is a data collection plan only. It does not authorize live trading and does not allow active-alpha to bypass manual confirmation.

If the backlog is packaged into per-role subagent tasks, the final step before passing results into active scripts is output collection:

```bash
python3 manual-investment-strategy-operator/scripts/research_subagent_output_collector.py \
  --task-package-json manual-investment-strategy-operator/experiments/{run_id}-research-subagent-task-package.json \
  --output /private/tmp/{monitor_run_id}-external-subagent-outputs.json \
  --manifest-output manual-investment-strategy-operator/experiments/{run_id}-research-subagent-output-collection.json \
  --markdown-output manual-investment-strategy-operator/reports/{run_id}-research-subagent-output-collection.md
```

The collector is upstream of active monitor handoff generation. It may prove that subagent outputs are still missing or weak; in that case active scripts must keep `research_panel_missing=true` / `research_committee_degraded=true`.

Active monitor usage:

- Use backlog tasks to guide the next social, crypto market, onchain, macro, or US open scan.
- Keep handoff `max_allowed_action` at `watch/paper_only/risk_alert/no_deploy` until a subsequent quality audit passes.
- Preserve action blockers such as paper sample size, recommendation calibration, double-80, cash rails, and human confirmation.
