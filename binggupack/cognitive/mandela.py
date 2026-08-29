"""Behavioral-eval integrity checks; never modifies scores or product state."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

_ALLOWED_TREATMENT_KEYS = {"cognitive_layer", "recall", "outcome", "paperthin_patterns"}
_GAMED_METRICS = {"use_count", "recall_calls", "skill_calls", "invocations"}


def audit_benchmark(manifest: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []

    def add(code: str, severity: str, message: str) -> None:
        findings.append({"code": code, "severity": severity, "message": message})

    if manifest.get("expected_answers_visible"):
        add("ANSWER_LEAKAGE", "critical", "expected answers are visible before execution")
    if manifest.get("designer_id") and manifest.get("designer_id") == manifest.get("scorer_id"):
        add("SCORER_DESIGNER_COUPLING", "high", "designer and scorer are the same")
    benchmark = set(manifest.get("benchmark_source_ids") or [])
    training = set(manifest.get("training_source_ids") or [])
    if benchmark.intersection(training):
        add("BENCHMARK_CONTAMINATION", "critical", "benchmark sources overlap training/design sources")
    baseline = dict(manifest.get("baseline_conditions") or {})
    treatment = dict(manifest.get("treatment_conditions") or {})
    unfair = False
    for key in set(baseline) | set(treatment):
        if key in _ALLOWED_TREATMENT_KEYS:
            continue
        if baseline.get(key) != treatment.get(key):
            unfair = True
            break
    if unfair:
        add("UNFAIR_BASELINE", "high", "baseline and treatment differ outside the cognitive layer")
    group_conditions = dict(manifest.get("recall_group_conditions") or {})
    recall_on = dict(group_conditions.get("recall_on") or {})
    recall_off = dict(group_conditions.get("recall_off") or {})
    if recall_on and recall_off:
        comparable_on = {k: v for k, v in recall_on.items() if k != "recall"}
        comparable_off = {k: v for k, v in recall_off.items() if k != "recall"}
        if comparable_on != comparable_off:
            add("RECALL_GROUP_CONDITION_MISMATCH", "high",
                "recall and non-recall groups differ beyond recall availability")
    if manifest.get("selection_strategy") != "fixed_manifest":
        add("CHERRY_PICKING", "high", "examples were not fixed before treatment results")
    metrics = {str(m) for m in manifest.get("primary_metrics") or []}
    if not metrics or metrics.issubset(_GAMED_METRICS):
        add("METRIC_GAMING", "high", "primary metrics measure invocation rather than behavior")
    if manifest.get("outcome_labels_visible_before_decision"):
        add("OUTCOME_LEAKAGE", "critical", "future outcome labels are visible to the decision path")
    severities = {f["severity"] for f in findings}
    verdict = "BLOCK" if severities.intersection({"critical", "high"}) else "REFINE" if findings else "PASS"
    # Reuse the existing pure commit/reveal seal primitive without opening its
    # human-only vault or blind ledger.
    from scripts.hybrid_agi.hag_commit_reveal import compute_seal

    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    seal = compute_seal(canonical, "binggupack-mandela-v1")
    return {"verdict": verdict, "findings": findings, "score_adjustment": 0, "writes": 0,
            "manifest_seal": seal, "manifest_unchanged": True}


def evaluate_behavioral_runs(runs: list[dict[str, Any]], mandela: dict[str, Any]) -> dict[str, Any]:
    """Compare fixed A/B/C fixtures and return a bounded, non-causal verdict."""
    if mandela.get("verdict") != "PASS":
        return {"verdict": "INSUFFICIENT EVIDENCE", "reason": "mandela audit did not pass",
                "comparisons": {}, "signal_only": True}
    by_scenario: dict[str, set[str]] = defaultdict(set)
    for row in runs:
        by_scenario[str(row.get("scenario"))].add(str(row.get("variant")))
    complete = {name for name, variants in by_scenario.items() if {"A", "B", "C"} <= variants}
    if len(complete) < 2:
        return {"verdict": "INSUFFICIENT EVIDENCE", "reason": "fewer than two complete A/B/C scenarios",
                "comparisons": {}, "signal_only": True}
    metrics = sorted({key for row in runs for key, value in row.items()
                      if key not in {"scenario", "variant"} and isinstance(value, (int, float))})
    aggregates: dict[str, dict[str, float]] = {}
    for variant in ("A", "B", "C"):
        selected = [row for row in runs if row.get("scenario") in complete and row.get("variant") == variant]
        aggregates[variant] = {
            metric: sum(float(row.get(metric, 0.0)) for row in selected) / len(selected)
            for metric in metrics
        }
    c_vs_b = {metric: round(aggregates["C"][metric] - aggregates["B"][metric], 6) for metric in metrics}
    positive = c_vs_b.get("task_completion", 0.0) > 0 or c_vs_b.get("factual_error", 0.0) < 0
    regressed = c_vs_b.get("task_completion", 0.0) < 0 or c_vs_b.get("factual_error", 0.0) > 0
    verdict = "REGRESSED" if regressed else "IMPROVED" if positive else "NO MATERIAL CHANGE"
    return {"verdict": verdict, "aggregates": aggregates, "comparisons": {"C_vs_B": c_vs_b},
            "scenarios": len(complete), "signal_only": True, "causal_claim": False}
