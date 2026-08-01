#!/usr/bin/env python3
"""Audit the two frozen Stage-2 model studies without reopening their holdouts."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> Any:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def sha(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def metric(evidence: dict[str, Any], segment: str, cutoff: str = "T-24h") -> dict[str, Any]:
    return next(row for row in evidence["metrics"] if row["segment"] == segment and row["cutoff"] == cutoff)


def build() -> dict[str, Any]:
    selection = read("config/stage2_model_selection.json")
    rows = []
    if len(selection["selected_market_types"]) != 2 or selection.get("third_market_type_expansion_allowed") is not False:
        raise ValueError("Stage 2 must freeze exactly two model types and forbid expansion")
    for selected in selection["selected_market_types"]:
        evidence = read(selected["evidence_path"])
        protocol = read(selected["protocol_path"])
        validation, final = metric(evidence, "validation"), metric(evidence, "final_holdout")
        lower = final.get("paired_brier_improvement_lower_95")
        if lower is None: lower = final.get("brier_improvement_lower_95_date_clustered")
        split = evidence.get("chronological_split") or evidence.get("split")
        final_count = int(final["games"])
        governance_ok = all((
            evidence.get("final_holdout_first_inspected_at") is not None,
            evidence.get("final_holdout_reuse_for_model_selection_allowed") is False,
            evidence.get("parameter_changes_without_materially_different_prefrozen_model_and_fresh_oos_allowed") is False,
            final_count >= 30,
        ))
        passed = bool(evidence.get("research_promotion_candidate") is True and lower is not None and lower > 0 and governance_ok)
        rows.append({
            **{key: selected[key] for key in ("selection_id", "exact_market_type", "domain", "model_version")},
            "protocol_path": selected["protocol_path"], "protocol_sha256": sha(selected["protocol_path"]),
            "evidence_path": selected["evidence_path"], "evidence_sha256": sha(selected["evidence_path"]),
            "implementation_path": selected["implementation_path"], "implementation_sha256": sha(selected["implementation_path"]),
            "protocol_created_at": protocol.get("created_at"), "split": split,
            "independent_final_oos_events": final_count, "final_historical_hit_rate": final.get("historical_hit_rate"),
            "validation": {k: validation.get(k) for k in ("games", "model_brier", "market_brier", "brier_improvement", "model_log_loss", "market_log_loss", "log_loss_improvement")},
            "final_holdout": {k: final.get(k) for k in ("games", "model_brier", "market_brier", "brier_improvement", "paired_brier_improvement_lower_95", "brier_improvement_lower_95_date_clustered", "model_log_loss", "market_log_loss", "log_loss_improvement", "historical_hit_rate", "calibration_bins")},
            "final_holdout_first_inspected_at": evidence["final_holdout_first_inspected_at"],
            "final_holdout_reuse_for_model_selection_allowed": False,
            "governance_passed": governance_ok, "conclusion": "pass" if passed else "fail_frozen",
            "paper_estimates_allowed": passed,
            "failure_reasons": [] if passed else ["robust_relative_market_improvement_not_proven", "final_holdout_closed_to_retuning"],
        })
    eligible = [row["model_version"] for row in rows if row["paper_estimates_allowed"]]
    return {
        "schema_version": "polymarket-stage2-model-audit-v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete", "formal_market_type_count": 2, "research_scope_frozen": True,
        "models": rows, "stage3_forward_paper_eligible_models": eligible,
        "frozen_failed_models": [row["model_version"] for row in rows if row["conclusion"] == "fail_frozen"],
        "current_model_qualified_direction": None,
        "current_action": "NO_BET" if not eligible else "MODEL_ELIGIBLE_AWAIT_DAILY_NET_EV_GATE",
        "stage2_finite_research_complete": True, "third_market_type_researched": False,
        "paper_only": True, "live_orders_enabled": False, "private_api_used": False,
        "real_money_execution_authorized": False,
    }


def markdown(p: dict[str, Any]) -> str:
    lines = ["# Stage 2 Independent Model Report", "", f"Conclusion: **{p['current_action']}**. The two preselected studies are complete and the final holdouts remain frozen.", "", "| Exact market type | Model | Final OOS | Model Brier | Market Brier | Improvement | 95% lower | Conclusion |", "|---|---|---:|---:|---:|---:|---:|---|"]
    for row in p["models"]:
        f = row["final_holdout"]; lower = f.get("paired_brier_improvement_lower_95")
        if lower is None: lower = f.get("brier_improvement_lower_95_date_clustered")
        lines.append(f"| {row['exact_market_type']} | `{row['model_version']}` | {row['independent_final_oos_events']} | {f['model_brier']:.6f} | {f['market_brier']:.6f} | {f['brier_improvement']:.6f} | {lower:.6f} | `{row['conclusion']}` |")
    lines += ["", "Neither failure authorizes a third Stage-2 model type. A new version requires a materially different model, a new pre-frozen protocol, and fresh OOS/forward evidence.", "", "These are independent model directions, but neither has recommendation evidence qualification. Market-implied direction alone remains NO BET.", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--output", default=str(ROOT / "experiments/current-stage2-model-audit.json")); ap.add_argument("--report", default=str(ROOT / "reports/STAGE2_MODEL_REPORT.md")); args = ap.parse_args()
    p = build(); out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); Path(args.report).write_text(markdown(p), encoding="utf-8"); print(json.dumps(p, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
