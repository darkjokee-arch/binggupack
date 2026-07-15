# -*- coding: utf-8 -*-
"""MGB-12 operational-home-isolation — 벤치마크가 운영 정본을 수정하지 않는지.

격리 홈에서 write op 을 수행해도 운영 ledger fingerprint(존재·size·mtime_ns·digest)가 불변이어야
한다. 운영 정본이 없는 adapter(operating_fingerprint=None)는 자명 통과. runner 도 매 시나리오
전후로 이 불변을 별도 강제한다(이 시나리오는 명시적 재확인).
"""
from benchmark.contracts import Cap, fp_content_equal
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict

ID = "MGB-12"
TITLE = "operational-home-isolation"
REQUIRES = {Cap.INIT, Cap.SAVE}


def run(adapter, home, ctx):
    fp0 = adapter.operating_fingerprint()
    adapter.observe(home, Cap.INIT)
    adapter.observe(home, Cap.SAVE, text="격리 검증용 판단 문장 하기로 정했어요.")
    fp1 = adapter.operating_fingerprint()
    ok = fp_content_equal(fp0, fp1)  # content 기준(mtime 외부 SQLite 활동 오탐 제외)
    return ScenarioResult(
        ID, TITLE, ExecutionStatus.OK, Verdict.PASS if ok else Verdict.FAIL,
        reason="운영 정본 fingerprint 불변=%s (None=운영정본 없는 adapter)" % ok,
        evidence={"fp_before": fp0, "fp_after": fp1},
        operating_state_invariant=ok)
