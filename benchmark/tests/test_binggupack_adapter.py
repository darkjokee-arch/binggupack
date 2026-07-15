# -*- coding: utf-8 -*-
"""BingguPack adapter 실행 회귀 — 공개 CLI 프로필 12 시나리오.

★느림(공개 CLI subprocess 다수). 결과 계약:
  · TOTAL == 12 (분모 유지) · MGB-10 = UNSUPPORTED/UNSUPPORTED
  · FAIL == 0 · 운영 ledger 불변(operating_state_ok)
"""
from benchmark.adapters.binggupack import BingguPackAdapter
from benchmark.runner import run_benchmark


def test_binggupack_profile_12_scenarios():
    results, summary = run_benchmark(BingguPackAdapter())
    m = {r.id: r for r in results}

    assert summary["TOTAL"] == 12 and summary["total_matches_expected"] is True
    # MGB-03(시간 신선도)·MGB-10(tamper)은 공개 CLI 프로필에서 UNSUPPORTED 로 고정
    # (내부 함수 직접호출·sleep flaky 로 PASS 위장하지 않음).
    for sid in ("MGB-03", "MGB-10"):
        assert m[sid].execution_status.value == "UNSUPPORTED", sid
        assert m[sid].verdict.value == "UNSUPPORTED", sid
    # 정직화된 계약 — 나머지 시나리오에 FAIL 이 없어야 한다.
    assert summary["FAIL"] == 0, [r.id for r in results if r.verdict.value == "FAIL"]
    assert summary["UNSUPPORTED"] == 2
    assert summary["PASS"] == 10
    # 운영 정본 불변(사후 sentinel · content 기준).
    assert summary["operating_state_ok"] is True
