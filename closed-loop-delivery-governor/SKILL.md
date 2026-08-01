---
name: closed-loop-delivery-governor
description: Govern material changes to products, data pipelines, automations, APIs, persistence, services, releases, and business workflows. Use whenever Codex plans, implements, fixes, migrates, or releases a workflow whose inputs must become durable, verifiable outputs. Enforce a frozen goal, single-writer ownership, data authority, append-only deviations, graded completion, runtime proof, release evidence, and independent verification for high-risk work. Do not use for copy-only or other trivial edits with no behavioral effect.
---

# Closed-loop delivery governor

Use deterministic governance before changing a material workflow. Never replace project-specific tests or domain safety rules with this Skill.

## Classify the change

Treat the change as material when it affects behavior, data authority, automation, persistence, interfaces, services, migrations, deployments, or a business loop.

Classify risk:

- `high`: accounts, money, authentication, destructive migration, schedules, deployment, source-of-truth changes, external writes, or safety boundaries.
- `medium`: user workflows, business logic, read models, or non-destructive contracts.
- `low`: isolated behavioral changes with no persistent or external effect.

Skip governance only for copy, comments, formatting, or similarly behavior-free edits.

## Start a governed change

1. Locate the canonical project root and read its `AGENTS.md` plus `.codex/governance/project.json`.
2. If governance is absent, initialize it with `scripts/governance.py init`; do not guess project-specific data authority.
3. Create a `GoalContractV1` and referenced `AcceptanceScenarioV1` files. Include the business outcome, scope, non-goals, constraints, observable expectations, and required completion level.
4. Run `scripts/governance.py begin --goal-file <path>`. This freezes the goal and acquires the worktree lock using `CODEX_THREAD_ID`.
5. Stop before editing if another thread owns the same worktree. Read-only inspection may continue. Use a separate Git worktree for safe parallel implementation.

Read [contracts.md](references/contracts.md) before creating or changing governance JSON.

## Implement and correct

- Modify only the frozen scope.
- Convert every user-reported gap into `governance.py deviation add` before fixing it.
- Add or update an acceptance scenario for every deviation. Never answer dissatisfaction with an untracked whole-product redesign.
- Keep compatibility or fallback data out of formal outputs unless `DataAuthorityV1` explicitly permits formal use.
- Preserve immutable history; supersede frozen goals and records instead of rewriting them.

## Verify completion

Use only these claims:

- `DESIGNED`: goal and acceptance are frozen.
- `CODED`: implementation exists at the recorded source digest.
- `TESTED`: configured tests passed at that digest.
- `RUNTIME_VERIFIED`: loaded versions, golden paths, and runtime invariants passed at that digest.
- `BUSINESS_READY`: no open P0/P1 deviations, required external inputs are confirmed, and high-risk work has an independent PASS from a different thread.
- `BLOCKED`: retain the highest achieved level and list the missing input or authority.

Run, in order:

```text
governance.py verify --level CODED
governance.py verify --level TESTED
governance.py verify --level RUNTIME_VERIFIED
governance.py verify --level BUSINESS_READY ...
governance.py release
governance.py close
```

Do not say “complete”, “usable”, or equivalent beyond the level returned by the script.

## Independent verification

For high-risk work, dispatch a read-only verifier with only:

- canonical project root;
- frozen goal and acceptance IDs;
- raw diff or source digest;
- runtime endpoint or verification bundle.

Do not reveal the implementation narrative, suspected bugs, or expected PASS. The verifier must not edit or fix. Record its result with a different `CODEX_THREAD_ID`; a FAIL becomes an append-only deviation and blocks `BUSINESS_READY`.

## Release evidence

Generate `ReleaseEvidenceV1` with `governance.py release`. Bind Git state, source digest, build/runtime versions, test results, blockers, deviations, and verifier identity. Store only allowlisted metadata; never include secrets, tokens, private keys, or full account data.

If source files change after verification, treat prior verification and release evidence as stale and rerun the gates.

## Lock recovery

Never break a lock automatically. After confirming the previous writer is no longer active, run `governance.py takeover --reason <reason>`. Preserve the prior owner and reason in the audit events.
