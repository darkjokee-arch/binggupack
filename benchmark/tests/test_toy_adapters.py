# -*- coding: utf-8 -*-
"""toy adapter 회귀 — 계약이 BingguPack 전용이 아님을 증명.

conforming 은 12 PASS, failing 은 지정 시나리오에서 (ERROR 아닌) FAIL 이어야 한다.
"""
from benchmark.adapters.toy_conforming import ToyConformingAdapter
from benchmark.adapters.toy_failing import ToyFailingAdapter
from benchmark.runner import run_benchmark


def _by_id(results):
    return {r.id: r for r in results}


def test_conforming_all_pass():
    results, summary = run_benchmark(ToyConformingAdapter())
    assert summary["TOTAL"] == 12 and summary["total_matches_expected"] is True
    assert summary["PASS"] == 12 and summary["FAIL"] == 0
    assert summary["operating_state_ok"] is True


def test_failing_fails_only_expected_and_not_error():
    results, summary = run_benchmark(ToyFailingAdapter())
    m = _by_id(results)
    assert m["MGB-01"].verdict.value == "FAIL"
    assert m["MGB-04"].verdict.value == "FAIL"
    assert summary["FAIL"] == 2 and summary["PASS"] == 10
    # 계약 위반이지 실행 오류가 아니어야 한다(정상 실행 후 FAIL).
    assert all(r.execution_status.value != "ERROR" for r in results)
