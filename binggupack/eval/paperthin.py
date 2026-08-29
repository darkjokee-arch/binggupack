"""Mandela integrity checks for Paperthin-derived behavioral evaluations."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any

_ALLOWED_TREATMENT_KEYS = {"cognitive_layer", "recall", "outcome", "paperthin_patterns"}
_GAMED_METRICS = {"use_count", "recall_calls", "skill_calls", "invocations"}


def _seal(manifest: dict[str, Any]) -> str:
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(("binggupack-mandela-v1\0" + canonical).encode()).hexdigest()


def audit_benchmark(
    manifest: dict[str, Any], observations: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Audit a sealed manifest and actual observations without modifying either."""
    findings: list[dict[str, str]] = []

    def add(code: str, severity: str, message: str) -> None:
        findings.append({"code": code, "severity": severity, "message": message})

    required = {
        "manifest_id", "fixture_ids", "fixture_digests", "designer_id", "scorer_id",
        "benchmark_source_ids", "training_source_ids", "baseline_conditions",
        "treatment_conditions", "recall_group_conditions", "selection_strategy",
        "primary_metrics", "metric_directions", "outcome_labels_visible_before_decision",
        "independent_observation_source",
    }
    missing = sorted(key for key in required if key not in manifest)
    if missing:
        add("MANIFEST_INCOMPLETE", "high", "missing required fields: " + ",".join(missing))
    if manifest.get("expected_answers_visible"):
        add("ANSWER_LEAKAGE", "critical", "expected answers are visible before execution")
    if manifest.get("designer_id") == manifest.get("scorer_id"):
        add("SCORER_DESIGNER_COUPLING", "high", "designer and scorer are the same")
    benchmark = set(manifest.get("benchmark_source_ids") or [])
    training = set(manifest.get("training_source_ids") or [])
    if not benchmark or benchmark.intersection(training):
        add("BENCHMARK_CONTAMINATION", "critical", "benchmark source is missing or overlaps design sources")
    baseline = dict(manifest.get("baseline_conditions") or {})
    treatment = dict(manifest.get("treatment_conditions") or {})
    if any(
        baseline.get(key) != treatment.get(key)
        for key in set(baseline) | set(treatment)
        if key not in _ALLOWED_TREATMENT_KEYS
    ):
        add("UNFAIR_BASELINE", "high", "conditions differ outside the cognitive layer")
    groups = dict(manifest.get("recall_group_conditions") or {})
    recall_on = {k: v for k, v in dict(groups.get("recall_on") or {}).items() if k != "recall"}
    recall_off = {k: v for k, v in dict(groups.get("recall_off") or {}).items() if k != "recall"}
    if not recall_on or recall_on != recall_off:
        add("RECALL_GROUP_CONDITION_MISMATCH", "high", "recall groups are absent or not comparable")
    if manifest.get("selection_strategy") != "fixed_manifest":
        add("CHERRY_PICKING", "high", "fixtures were not fixed before observations")
    metrics = {str(metric) for metric in manifest.get("primary_metrics") or []}
    directions = dict(manifest.get("metric_directions") or {})
    if not metrics or metrics.issubset(_GAMED_METRICS) or not metrics <= set(directions):
        add("METRIC_GAMING", "high", "behavioral metrics or directions are incomplete")
    if manifest.get("outcome_labels_visible_before_decision"):
        add("OUTCOME_LEAKAGE", "critical", "future outcome labels are visible to decisions")
    if not manifest.get("independent_observation_source"):
        add("SCORER_OBSERVATION_COUPLING", "high", "observations are produced by the treatment code")

    fixture_ids = [str(item) for item in manifest.get("fixture_ids") or []]
    fixture_digests = dict(manifest.get("fixture_digests") or {})
    if len(fixture_ids) < 2 or set(fixture_ids) != set(fixture_digests):
        add("FIXTURE_MANIFEST_INVALID", "high", "fixture ids and sealed digests do not match")
    if observations is None:
        add("OBSERVATIONS_MISSING", "high", "manifest-only self-report cannot pass")
    else:
        keys = [(str(row.get("scenario")), str(row.get("variant"))) for row in observations]
        counts = Counter(keys)
        if any(count != 1 for count in counts.values()):
            add("DUPLICATE_OBSERVATION", "high", "scenario/variant rows are not unique")
        expected = {(fixture, variant) for fixture in fixture_ids for variant in ("A", "B", "C")}
        if set(keys) != expected:
            add("OBSERVATION_SET_MISMATCH", "high", "observed rows differ from the sealed manifest")
        for row in observations:
            scenario = str(row.get("scenario"))
            if row.get("fixture_digest") != fixture_digests.get(scenario):
                add("FIXTURE_BINDING_MISMATCH", "high", "observation is not bound to its fixture")
                break
            if row.get("scorer_id") != manifest.get("scorer_id"):
                add("SCORER_BINDING_MISMATCH", "high", "observation scorer differs from manifest")
                break
    severities = {item["severity"] for item in findings}
    verdict = "BLOCK" if severities & {"critical", "high"} else "REFINE" if findings else "PASS"
    return {
        "verdict": verdict, "findings": findings, "score_adjustment": 0, "writes": 0,
        "manifest_seal": _seal(manifest), "manifest_unchanged": True,
    }


def evaluate_behavioral_runs(
    runs: list[dict[str, Any]], manifest: dict[str, Any], mandela: dict[str, Any]
) -> dict[str, Any]:
    """Compare exactly sealed A/B/C observations and keep claims non-causal."""
    if mandela.get("verdict") != "PASS":
        return {
            "verdict": "INSUFFICIENT EVIDENCE", "reason": "mandela audit did not pass",
            "comparisons": {}, "signal_only": True, "causal_claim": False,
        }
    fixture_ids = set(str(item) for item in manifest.get("fixture_ids") or [])
    if len(fixture_ids) < 2:
        return {
            "verdict": "INSUFFICIENT EVIDENCE", "reason": "fewer than two sealed fixtures",
            "comparisons": {}, "signal_only": True, "causal_claim": False,
        }
    by_scenario: dict[str, set[str]] = defaultdict(set)
    for row in runs:
        by_scenario[str(row.get("scenario"))].add(str(row.get("variant")))
    if set(by_scenario) != fixture_ids or any(value != {"A", "B", "C"} for value in by_scenario.values()):
        return {
            "verdict": "INSUFFICIENT EVIDENCE", "reason": "observation set is incomplete",
            "comparisons": {}, "signal_only": True, "causal_claim": False,
        }
    metrics = list(manifest.get("primary_metrics") or [])
    aggregates: dict[str, dict[str, float]] = {}
    for variant in ("A", "B", "C"):
        selected = [row for row in runs if row.get("variant") == variant]
        aggregates[variant] = {
            metric: sum(float(row.get(metric, 0.0)) for row in selected) / len(selected)
            for metric in metrics
        }
    delta = {metric: round(aggregates["C"][metric] - aggregates["B"][metric], 6) for metric in metrics}
    directions = dict(manifest.get("metric_directions") or {})
    regressions = [metric for metric, value in delta.items()
                   if (directions.get(metric) == "higher" and value < 0)
                   or (directions.get(metric) == "lower" and value > 0)]
    improvements = [metric for metric, value in delta.items()
                    if (directions.get(metric) == "higher" and value > 0)
                    or (directions.get(metric) == "lower" and value < 0)]
    verdict = "REGRESSED" if regressions else "IMPROVED" if improvements else "NO MATERIAL CHANGE"
    return {
        "verdict": verdict, "aggregates": aggregates, "comparisons": {"C_vs_B": delta},
        "scenarios": len(fixture_ids), "regressed_metrics": regressions,
        "improved_metrics": improvements, "signal_only": True, "causal_claim": False,
    }
