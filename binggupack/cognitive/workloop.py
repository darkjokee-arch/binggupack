"""One non-mutating work envelope connecting six cognitive patterns.

Catchup is the re-entry surface and is built from the same structured state;
SAVE/approval/commit and outcome writes remain explicit existing BingguPack calls.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .catchup import build_catchup
from .mandela import audit_benchmark, evaluate_behavioral_runs
from .patterns import (
    fact_check_candidate,
    propose_sip_candidates,
    reconstruct_intent,
    select_load_bearing_objection,
    select_next_best_action,
)


def run_cognitive_workloop(spec: dict[str, Any]) -> dict[str, Any]:
    """Run the read/check/challenge/verify/close/next-action envelope with write 0."""
    now = spec.get("now") or datetime.now(timezone.utc).isoformat()
    readchk = reconstruct_intent(
        spec.get("request", ""),
        available_facts=spec.get("available_facts"),
        ambiguity_candidates=spec.get("ambiguities"),
    )
    hate = select_load_bearing_objection(
        list(spec.get("objections") or []), test_result=spec.get("falsification_result")
    )
    sip = propose_sip_candidates(
        list(spec.get("candidate_items") or []),
        existing_candidates=list(spec.get("existing_candidates") or []),
    )
    fact_results = []
    for candidate in sip["candidates"]:
        result = fact_check_candidate(
            candidate, list(spec.get("fact_evidence") or []), now=now,
            max_age_days=int(spec.get("fact_max_age_days", 30)),
        )
        candidate["fact_status"] = result["status"]
        canonical_bundle = json.dumps(
            {"candidate": candidate["text"], "kind": candidate["kind"], "verification": result},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        candidate["verification_bundle_digest"] = hashlib.sha256(
            canonical_bundle.encode("utf-8", "replace")
        ).hexdigest()
        if candidate.get("needs_factchk"):
            # Current text-only SAVE binding cannot preserve this structured bundle.
            # Keep it useful as a checked proposal, but never claim it is save-ready.
            candidate["save_ready"] = False
            candidate["evidence_binding"] = "EPHEMERAL_ONLY"
        fact_results.append(result)
    nba = select_next_best_action(list(spec.get("actions") or []), {
        "blocker": spec.get("blocker") or (
            hate.get("objection") if hate.get("status") == "BLOCKER_CONFIRMED" else None
        ),
        "recall": list(spec.get("recall") or []),
        "outcomes": list(spec.get("outcomes") or []),
        "constraints": readchk["constraints"],
    })
    recall_nodes = [dict(item) for item in spec.get("recall") or []]
    catchup = build_catchup(
        repo_state=dict(spec.get("repo_state") or {"available": False, "error": "repo state not supplied"}),
        recall_result={"relevant_nodes": recall_nodes},
        outcomes=list(spec.get("outcomes") or []),
        outcome_summary=dict(spec.get("outcome_summary") or {"overall": {"pending_traces": 0}}),
        superseded=list(spec.get("superseded") or []),
        query=readchk["recall_query"],
        max_chars=int(spec.get("context_max_chars", 8000)),
        next_action=nba,
    )
    manifest = dict(spec.get("benchmark_manifest") or {})
    mandela = audit_benchmark(manifest) if manifest else {
        "verdict": "NOT_RUN", "findings": [], "score_adjustment": 0, "writes": 0
    }
    behavioral = (evaluate_behavioral_runs(list(spec.get("behavioral_runs") or []), mandela)
                  if manifest else {"verdict": "INSUFFICIENT EVIDENCE", "reason": "no fixed benchmark manifest"})
    return {
        "readchk": readchk,
        "recall": recall_nodes,
        "hate": hate,
        "factchk": fact_results,
        "verification": dict(spec.get("verification") or {}),
        "sip": sip,
        "nba": nba,
        "catchup": catchup,
        "mandela": mandela,
        "behavioral_eval": behavioral,
        "outcome_contract": {
            "writer": "binggupack.pack.outcome_attribution.record_run_outcome",
            "requires_trace_subset": True,
            "requires_evidence_digest": True,
            "called": False,
        },
        "safety": {"writes": 0, "auto_save": False, "approval_bypass": False,
                   "external_mutation": False},
    }
