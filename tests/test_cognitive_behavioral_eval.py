from __future__ import annotations

from binggupack.eval.paperthin_behavioral import run_reference_behavioral_eval


def test_self_generated_abc_fixture_is_blocked_as_insufficient_evidence():
    out = run_reference_behavioral_eval()
    assert out["mandela"]["verdict"] == "BLOCK"
    assert out["mandela"]["manifest_seal"]
    assert out["evaluation"]["verdict"] == "INSUFFICIENT EVIDENCE"
    assert len(out["runs"]) == 9
    assert out["general_performance_claim"] is False
