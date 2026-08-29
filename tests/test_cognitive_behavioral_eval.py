from __future__ import annotations

from binggupack.cognitive.behavioral import run_reference_behavioral_eval


def test_fixed_abc_behavioral_fixture_is_mandela_clean_and_bounded():
    out = run_reference_behavioral_eval()
    assert out["mandela"]["verdict"] == "PASS"
    assert out["mandela"]["manifest_seal"]
    assert out["evaluation"]["verdict"] == "IMPROVED"
    assert out["evaluation"]["scenarios"] == 3
    assert len(out["runs"]) == 9
    assert out["general_performance_claim"] is False
    c_rows = [row for row in out["runs"] if row["variant"] == "C"]
    assert all(row["task_completion"] == 1.0 for row in c_rows)
