from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from binggupack.cognitive.patterns import (
    fact_check_candidate,
    propose_sip_candidates,
    reconstruct_intent,
    select_load_bearing_objection,
    select_next_best_action,
)
from binggupack.eval.paperthin import audit_benchmark, evaluate_behavioral_runs


def test_readchk_reconstructs_intent_constraints_deliverables_and_query():
    request = """# Goal
Implement read-only catchup for BingguPack.

Constraints:
- Never write to the ledger.
- Preserve human approval and G4_no_auto.

Deliverables:
- CLI command
- regression tests
"""
    out = reconstruct_intent(request)
    assert "catchup" in out["intent"].lower()
    assert any("ledger" in line.lower() for line in out["constraints"])
    assert any("CLI" in line for line in out["deliverables"])
    assert "catchup" in out["recall_query"].lower()
    assert out["needs_user_question"] is False


def test_readchk_only_escalates_material_unresolved_ambiguity():
    resolved = reconstruct_intent(
        "Implement the safe option.",
        ambiguity_candidates=[
            {"text": "CLI or MCP", "material": True, "safe_default": "CLI"},
            {"text": "function name", "material": False},
        ],
    )
    assert resolved["needs_user_question"] is False
    assert resolved["resolved_ambiguities"][0]["resolution"] == "CLI"

    blocked = reconstruct_intent(
        "Replace the public storage contract.",
        ambiguity_candidates=[
            {"text": "keep compatibility or break it", "material": True},
        ],
    )
    assert blocked["needs_user_question"] is True
    assert blocked["question"] == "keep compatibility or break it"

    known = reconstruct_intent(
        "Implement the interface.",
        available_facts={"surface": "CLI"},
        ambiguity_candidates=[{"text": "CLI or MCP", "material": True, "fact_key": "surface"}],
    )
    assert known["needs_user_question"] is False
    assert known["resolved_ambiguities"][0]["resolution"] == "CLI"

    conflict = reconstruct_intent("Constraints:\n- Must use public CLI.\n- Never use public CLI.")
    assert conflict["needs_user_question"] is True
    assert "conflicting constraints" in conflict["question"]


def test_hate_returns_one_load_bearing_objection_and_cheapest_test():
    objections = [
        {"text": "naming is inconsistent", "impact": 0.2, "likelihood": 0.8,
         "falsification_test": "inspect help text", "test_cost": 0.1},
        {"text": "catchup mutates the ledger", "impact": 1.0, "likelihood": 0.7,
         "falsification_tests": [
             {"test": "run full release suite", "cost": 0.9},
             {"test": "compare ledger hash before and after", "cost": 0.1},
         ]},
    ]
    out = select_load_bearing_objection(objections)
    assert out["objection"] == "catchup mutates the ledger"
    assert out["falsification_test"] == "compare ledger hash before and after"
    assert len(out["considered"]) == 2

    assert select_load_bearing_objection([], test_result="pass")["status"] == "NO_BLOCKER"
    proof = {"test": "compare ledger hash before and after", "digest": "a" * 64}
    assert select_load_bearing_objection(
        objections, test_result="pass", test_evidence=proof
    )["status"] == "FALSIFIED"
    assert select_load_bearing_objection(
        objections, test_result="fail", test_evidence=proof
    )["status"] == "BLOCKER_CONFIRMED"
    assert select_load_bearing_objection(objections, test_result="pass")["status"] == "TEST_REQUIRED"
    assert select_load_bearing_objection(objections, change_kinds=["typo"])["status"] == "SKIP"


def test_sip_proposes_ephemeral_typed_candidates_without_duplicates_or_authority():
    items = [
        {"kind": "lesson", "text": "Run the no-mutation test before release.",
         "source_refs": ["pytest:test_no_mutation"]},
        {"kind": "lesson", "text": " run the no-mutation test before release ",
         "source_refs": ["duplicate"]},
        {"kind": "state", "text": "Catchup tests are green.",
         "source_refs": ["pytest:catchup"],
         "external_claims": [{"claim_id": "python-spec", "claim": "Python 3.14 is supported",
                              "claim_type": "external_spec"}]},
    ]
    out = propose_sip_candidates(items)
    assert len(out["candidates"]) == 2
    assert out["duplicates"] == 1
    assert out["candidates"][1]["needs_factchk"] is True
    assert out["ephemeral"] is True
    assert out["writes"] == 0
    assert out["commit_allowed"] is False
    assert out["requires_human_approval"] is True
    assert propose_sip_candidates([])["candidates"] == []


def test_sip_forces_pure_preview_even_when_semantic_mode_is_enabled(tmp_path, monkeypatch):
    import scripts.openbinggu_conversation_capture_preview as preview

    monkeypatch.setenv("BINGGU_HOME", str(tmp_path))
    (tmp_path / "semantic_label_enabled").write_text("1", encoding="utf-8")
    monkeypatch.setattr(preview.canon, "suggest_label_kind", lambda _text: (_ for _ in ()).throw(
        AssertionError("semantic classifier must stay off")
    ))
    monkeypatch.setattr(preview, "_suggest_subtype", lambda _text: (_ for _ in ()).throw(
        AssertionError("semantic subtype must stay off")
    ))
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file())
    out = propose_sip_candidates([{"kind": "lesson", "text": "Always run the regression test."}])
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file())
    assert out["candidates"]
    assert out["candidates"][0]["canonical_gate_eligible"] is True
    assert before == after


def test_factchk_is_conditional_and_preserves_provenance():
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    internal = {"kind": "decision", "text": "Use catchup first", "external_claims": []}
    assert fact_check_candidate(internal, [], now=now)["status"] == "NOT_APPLICABLE"

    candidate = {
        "kind": "lesson",
        "text": "External facts",
        "external_claims": [
            {"claim_id": "c1", "claim": "API v2 supports JSON", "claim_type": "external_api"}
        ],
    }
    claim_digest = hashlib.sha256("API v2 supports JSON".encode()).hexdigest()
    verified = fact_check_candidate(candidate, [{
        "claim_id": "c1", "stance": "supports", "source_uri": "https://example.test/spec",
        "source_digest": "a" * 64, "claim_digest": claim_digest,
        "checked_at": "2026-08-28T00:00:00Z",
    }], now=now)
    assert verified["status"] == "VERIFIED"
    assert verified["claims"][0]["evidence"][0]["source_digest"] == "a" * 64

    contradicted = fact_check_candidate(candidate, [{
        "claim_id": "c1", "stance": "refutes", "source_uri": "https://example.test/spec",
        "source_digest": "b" * 64, "claim_digest": claim_digest,
        "checked_at": "2026-08-28T00:00:00Z",
    }], now=now)
    assert contradicted["status"] == "CONTRADICTED"
    assert fact_check_candidate(candidate, [], now=now)["status"] == "UNVERIFIED"
    stale = fact_check_candidate(candidate, [{
        "claim_id": "c1", "stance": "supports", "source_uri": "https://example.test/old",
        "source_digest": "c" * 64, "claim_digest": claim_digest,
        "checked_at": "2025-01-01T00:00:00Z",
    }], now=now, max_age_days=30)
    assert stale["status"] == "STALE"
    malformed = fact_check_candidate(candidate, [{
        "claim_id": "c1", "stance": "supports", "checked_at": "2099-01-01T00:00:00Z",
    }], now=now)
    assert malformed["status"] == "UNVERIFIED"
    assert malformed["provenance_complete"] is False


def test_nba_uses_recall_but_keeps_outcome_signal_out_of_ranking():
    actions = [
        {"id": "ship", "action": "Ship now", "value": 0.9, "urgency": 0.8,
         "effort": 0.1, "risk": 0.2, "memory_ids": ["m-risk"]},
        {"id": "regress", "action": "Run approval regression", "value": 0.6, "urgency": 0.5,
         "effort": 0.2, "risk": 0.1, "resolves_blocker": True, "memory_ids": ["m-risk"]},
    ]
    baseline = select_next_best_action(actions, {})
    recalled = select_next_best_action(actions, {
        "blocker": "approval safety unverified",
        "recall": [{"node_id": "m-risk", "effect": "avoid", "applies_to": "ship", "weight": 1.0}],
        "outcomes": [{"applied_node_ids": ["m-risk"], "application": "applied", "result": "failure"}],
    })
    assert baseline["action_id"] == "ship"
    assert recalled["action_id"] == "regress"
    assert recalled["counterfactual_without_recall"] == "ship"
    assert recalled["recall_changed_decision"] is True
    assert recalled["evidence"]
    assert recalled["outcome_used_for_ranking"] is False
    assert recalled["outcome_signals"]

    low = select_next_best_action([
        {"id": "a", "action": "A", "value": 0.5},
        {"id": "b", "action": "B", "value": 0.49},
    ], {})
    assert low["confidence"] == "low"


def test_mandela_detects_leakage_coupling_contamination_and_metric_gaming():
    manifest = {
        "manifest_id": "m1", "fixture_ids": ["s1", "s2"],
        "fixture_digests": {"s1": "a" * 64, "s2": "b" * 64},
        "designer_id": "designer-a", "scorer_id": "scorer-b",
        "expected_answers_visible": False,
        "benchmark_source_ids": ["eval-1"], "training_source_ids": ["train-1"],
        "baseline_conditions": {"task": "same", "tools": "same"},
        "treatment_conditions": {"task": "same", "tools": "same", "cognitive_layer": True},
        "recall_group_conditions": {
            "recall_off": {"task": "same", "recall": False},
            "recall_on": {"task": "same", "recall": True},
        },
        "selection_strategy": "fixed_manifest", "primary_metrics": ["task_completion"],
        "metric_directions": {"task_completion": "higher"},
        "outcome_labels_visible_before_decision": False,
        "independent_observation_source": True,
    }
    observations = [
        {"scenario": scenario, "variant": variant, "fixture_digest": manifest["fixture_digests"][scenario],
         "scorer_id": "scorer-b", "task_completion": 1}
        for scenario in ("s1", "s2") for variant in ("A", "B", "C")
    ]
    clean = audit_benchmark(manifest, observations)
    assert clean["verdict"] == "PASS"

    self_report = audit_benchmark(manifest)
    assert self_report["verdict"] == "BLOCK"
    assert "OBSERVATIONS_MISSING" in {item["code"] for item in self_report["findings"]}

    bad = audit_benchmark({
        "designer_id": "same", "scorer_id": "same", "expected_answers_visible": True,
        "benchmark_source_ids": ["x"], "training_source_ids": ["x"],
        "baseline_conditions": {"task": "easy"},
        "treatment_conditions": {"task": "easy", "extra_tool": True},
        "selection_strategy": "cherry_picked", "primary_metrics": ["use_count"],
        "outcome_labels_visible_before_decision": True,
    })
    codes = {f["code"] for f in bad["findings"]}
    assert {"ANSWER_LEAKAGE", "SCORER_DESIGNER_COUPLING", "BENCHMARK_CONTAMINATION",
            "UNFAIR_BASELINE", "CHERRY_PICKING", "METRIC_GAMING", "OUTCOME_LEAKAGE"} <= codes
    assert bad["verdict"] == "BLOCK"


def test_behavioral_eval_returns_honest_bounded_verdict():
    insufficient = evaluate_behavioral_runs([], {}, {"verdict": "PASS", "findings": []})
    assert insufficient["verdict"] == "INSUFFICIENT EVIDENCE"

    runs = [
        {"scenario": "s1", "variant": "A", "task_completion": 0, "factual_error": 1},
        {"scenario": "s1", "variant": "B", "task_completion": 1, "factual_error": 1},
        {"scenario": "s1", "variant": "C", "task_completion": 1, "factual_error": 0},
        {"scenario": "s2", "variant": "A", "task_completion": 0, "factual_error": 1},
        {"scenario": "s2", "variant": "B", "task_completion": 0, "factual_error": 1},
        {"scenario": "s2", "variant": "C", "task_completion": 1, "factual_error": 0},
    ]
    manifest = {
        "fixture_ids": ["s1", "s2"],
        "primary_metrics": ["task_completion", "factual_error"],
        "metric_directions": {"task_completion": "higher", "factual_error": "lower"},
    }
    out = evaluate_behavioral_runs(runs, manifest, {"verdict": "PASS", "findings": []})
    assert out["verdict"] == "IMPROVED"
    assert out["comparisons"]["C_vs_B"]["task_completion"] > 0

    regressed = [dict(row) for row in runs]
    for row in regressed:
        if row["variant"] == "C":
            row["factual_error"] = 2
    assert evaluate_behavioral_runs(
        regressed, manifest, {"verdict": "PASS"}
    )["verdict"] == "REGRESSED"
