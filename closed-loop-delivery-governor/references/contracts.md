# Governance contracts

## Contents

1. Shared rules
2. ProjectGovernanceV1
3. GoalContractV1
4. DataAuthorityV1
5. AcceptanceScenarioV1
6. DeviationRecordV1
7. ActiveChangeLockV1
8. VerificationResultV1
9. ReleaseEvidenceV1

## Shared rules

- Use UTF-8 JSON; use JSONL only for append-only event streams.
- Use ISO-8601 UTC timestamps.
- Give every record a stable `id` and `schema_version`.
- Freeze a goal by content hash. A changed goal needs a new ID and `supersedes_id`.
- Keep secrets and full private account payloads outside governance files.
- Treat `BLOCKED` as an orthogonal state: retain the highest verified level and list blockers.

## ProjectGovernanceV1

Required fields:

```json
{
  "schema_version": "ProjectGovernanceV1",
  "project_id": "stable-project-id",
  "name": "Human name",
  "canonical_root": "/absolute/path",
  "default_risk": "medium",
  "test_commands": [],
  "runtime_checks": [],
  "digest_excludes": [],
  "business_ready_requires_external_inputs": true
}
```

Commands run from `canonical_root`. Do not store shell secrets in commands.

## GoalContractV1

Required fields:

```json
{
  "schema_version": "GoalContractV1",
  "id": "goal-id",
  "title": "Observable outcome",
  "business_outcome": "Why this matters",
  "scope": [],
  "non_goals": [],
  "constraints": [],
  "acceptance_scenario_ids": [],
  "required_level": "RUNTIME_VERIFIED",
  "risk": "high",
  "supersedes_id": null
}
```

`begin` adds `frozen_at`, `frozen_by`, and `content_hash` to the stored copy. Never edit that copy.

## DataAuthorityV1

Represent authority by field group, not by whole application:

```json
{
  "schema_version": "DataAuthorityV1",
  "authorities": [
    {
      "field_group": "account.quantity",
      "authority": "user-confirmed account snapshot",
      "writer": "account adapter",
      "freshness_seconds": 86400,
      "fallback": "historical reference only",
      "formal_use_allowed": true
    }
  ]
}
```

If a fallback is not allowed for formal use, UI and decision layers must label it historical, estimated, or blocked.

## AcceptanceScenarioV1

Required fields:

```json
{
  "schema_version": "AcceptanceScenarioV1",
  "id": "scenario-id",
  "title": "User-observable behavior",
  "level": "RUNTIME_VERIFIED",
  "preconditions": [],
  "steps": [],
  "expected": [],
  "evidence": []
}
```

Use separate scenarios for success, blocking, rollback, restart, and stale-version behavior.

## DeviationRecordV1

Append one JSON object per line:

```json
{
  "schema_version": "DeviationRecordV1",
  "id": "dev-id",
  "goal_id": "goal-id",
  "severity": "P1",
  "expected": "Expected behavior",
  "actual": "Observed behavior",
  "reproduction": [],
  "root_layer": "runtime",
  "acceptance_scenario_id": "scenario-id",
  "status": "open",
  "supersedes_id": null,
  "created_at": "...",
  "created_by": "thread-id"
}
```

Correct a record by appending a superseding record. Never edit prior lines.

## ActiveChangeLockV1

Store locks in the Git common directory, keyed by the absolute worktree hash. Required fields include owner thread, worktree, branch, goal ID, source digest, timestamps, and optional integration owner. A different worktree receives a different lock.

## VerificationResultV1

Required fields include verification ID, goal ID, level, source digest, verifier thread, PASS/FAIL, check result hashes, findings, and timestamp. High-risk `BUSINESS_READY` requires a PASS where verifier and implementation owner differ.

## ReleaseEvidenceV1

Required fields include release ID, goal ID, achieved level, blocked flag, blockers, Git commit/branch/dirty state, source digest, build/runtime versions, test and runtime check hashes, open deviation counts, verifier ID, and timestamp.

Release evidence is invalid when its source digest differs from the current digest.
