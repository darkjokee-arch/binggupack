# -*- coding: utf-8 -*-
"""결과 모델 회귀 — execution_status/verdict 조합 규칙과 집계(비-PASS 를 PASS 로 안 셈)."""
import pytest

from benchmark.result import ExecutionStatus, ScenarioResult, Verdict, summarize


def test_disallowed_pairs_raise():
    with pytest.raises(ValueError):
        ScenarioResult("X", "x", ExecutionStatus.OK, Verdict.UNSUPPORTED)
    with pytest.raises(ValueError):
        ScenarioResult("X", "x", ExecutionStatus.UNSUPPORTED, Verdict.PASS)
    with pytest.raises(ValueError):
        ScenarioResult("X", "x", ExecutionStatus.SKIPPED, Verdict.PASS)


def test_allowed_pairs_ok():
    ScenarioResult("A", "a", ExecutionStatus.OK, Verdict.PASS)
    ScenarioResult("A", "a", ExecutionStatus.OK, Verdict.FAIL)
    ScenarioResult("A", "a", ExecutionStatus.ERROR, Verdict.FAIL)
    ScenarioResult("A", "a", ExecutionStatus.UNSUPPORTED, Verdict.UNSUPPORTED)
    ScenarioResult("A", "a", ExecutionStatus.SKIPPED, Verdict.NOT_RUN)


def test_summarize_never_counts_nonpass_as_pass():
    rs = [
        ScenarioResult("1", "a", ExecutionStatus.OK, Verdict.PASS),
        ScenarioResult("2", "b", ExecutionStatus.ERROR, Verdict.FAIL),
        ScenarioResult("3", "c", ExecutionStatus.UNSUPPORTED, Verdict.UNSUPPORTED),
        ScenarioResult("4", "d", ExecutionStatus.SKIPPED, Verdict.NOT_RUN),
    ]
    s = summarize(rs, expected_total=4)
    assert (s["PASS"], s["FAIL"], s["UNSUPPORTED"], s["NOT_RUN"]) == (1, 1, 1, 1)
    assert s["TOTAL"] == 4 and s["total_matches_expected"] is True


def test_missing_scenario_shrinks_total_not_hidden():
    rs = [ScenarioResult("1", "a", ExecutionStatus.OK, Verdict.PASS)]
    s = summarize(rs, expected_total=12)
    assert s["TOTAL"] == 1 and s["total_matches_expected"] is False  # 분모 축소가 드러남


def test_operating_state_false_flags_summary():
    rs = [ScenarioResult("1", "a", ExecutionStatus.OK, Verdict.PASS,
                         operating_state_invariant=False)]
    s = summarize(rs, expected_total=1)
    assert s["operating_state_ok"] is False
