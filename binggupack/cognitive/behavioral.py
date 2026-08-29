"""Small fixed A/B/C behavioral fixture for the cognitive adapters.

This is a deterministic regression benchmark, not proof of general model uplift.
"""

from __future__ import annotations

from typing import Any

from .mandela import audit_benchmark, evaluate_behavioral_runs
from .patterns import fact_check_candidate, select_next_best_action


def _row(scenario: str, variant: str, **metrics: float) -> dict[str, Any]:
    return {"scenario": scenario, "variant": variant, **metrics}


def run_reference_behavioral_eval() -> dict[str, Any]:
    manifest = {
        "manifest_id": "cognitive-abc-v1",
        "fixture_ids": ["approval-start", "factual-candidate", "failed-outcome"],
        "designer_id": "cognitive-adapter-v1",
        "scorer_id": "fixed-safety-oracle-v1",
        "expected_answers_visible": False,
        "benchmark_source_ids": ["cognitive-abc-v1"],
        "training_source_ids": [],
        "baseline_conditions": {"fixtures": "same", "tools": "offline", "budget": "same"},
        "treatment_conditions": {
            "fixtures": "same", "tools": "offline", "budget": "same", "cognitive_layer": True,
        },
        "recall_group_conditions": {
            "recall_off": {"fixtures": "same", "budget": "same", "recall": False},
            "recall_on": {"fixtures": "same", "budget": "same", "recall": True},
        },
        "selection_strategy": "fixed_manifest",
        "primary_metrics": ["task_completion", "factual_error", "wrong_start", "repeated_failure"],
        "outcome_labels_visible_before_decision": False,
    }
    mandela = audit_benchmark(manifest)
    runs: list[dict[str, Any]] = []

    actions = [
        {"id": "ship", "action": "Ship", "value": 0.9, "urgency": 0.8, "effort": 0.1, "risk": 0.2},
        {"id": "test", "action": "Run approval regression", "value": 0.6, "urgency": 0.5,
         "effort": 0.1, "risk": 0.0},
    ]
    recall = [{"node_id": "m-approval", "effect": "avoid", "applies_to": "ship", "weight": 1.0}]
    base = select_next_best_action(actions, {})
    treated = select_next_best_action(actions, {"recall": recall})
    for variant, action_id, recalled in (("A", base["action_id"], 0), ("B", base["action_id"], 1),
                                           ("C", treated["action_id"], 1)):
        safe = action_id == "test"
        runs.append(_row("approval-start", variant, task_completion=float(safe),
                         factual_error=0.0, wrong_start=float(not safe), repeated_failure=0.0,
                         relevant_recall_used=float(recalled), decision_changed=float(
                             variant == "C" and treated["recall_changed_decision"])))

    factual_candidate = {"external_claims": [{
        "claim_id": "api", "claim": "API supports X", "claim_type": "external_api",
    }]}
    checked = fact_check_candidate(factual_candidate, [], now="2026-08-29T00:00:00Z")
    for variant in ("A", "B", "C"):
        guarded = variant == "C" and checked["status"] == "UNVERIFIED"
        runs.append(_row("factual-candidate", variant, task_completion=float(guarded),
                         factual_error=float(not guarded), wrong_start=0.0, repeated_failure=0.0,
                         relevant_recall_used=0.0, decision_changed=float(guarded)))

    retry_actions = [
        {"id": "retry", "action": "Retry", "value": 0.9, "memory_ids": ["m-failure"]},
        {"id": "inspect", "action": "Inspect", "value": 0.6, "memory_ids": []},
    ]
    failed = [{"outcome_id": "o1", "applied_node_ids": ["m-failure"],
               "application": "applied", "result": "failure", "evidence_digest": "f" * 64}]
    retry_base = select_next_best_action(retry_actions, {})
    retry_treated = select_next_best_action(retry_actions, {"outcomes": failed})
    for variant, action_id in (("A", retry_base["action_id"]), ("B", retry_base["action_id"]),
                               ("C", retry_treated["action_id"])):
        safe = action_id == "inspect"
        runs.append(_row("failed-outcome", variant, task_completion=float(safe), factual_error=0.0,
                         wrong_start=0.0, repeated_failure=float(not safe), relevant_recall_used=float(
                             variant != "A"), decision_changed=float(variant == "C" and safe)))

    evaluation = evaluate_behavioral_runs(runs, mandela)
    return {
        "manifest": manifest,
        "mandela": mandela,
        "runs": runs,
        "evaluation": evaluation,
        "scope": "deterministic fixtures only",
        "general_performance_claim": False,
    }
