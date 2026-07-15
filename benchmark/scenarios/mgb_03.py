# -*- coding: utf-8 -*-
"""MGB-03 stale-approval-rejection — 시간·상태 신선도가 만료된 승인이 거부되는지.

MGB-02(내용 결속)와 달리 MGB-03 은 '시간·상태 신선도 만료'를 검증한다.
★BingguPack v0.1 은 이 항목을 UNSUPPORTED 로 둔다(4cli 사후 반영): save preview_id 는 텍스트 해시
결속(내용)만 검증하고, 신선도 창(GATE_WINDOW_SEC) 만료를 공개 CLI 로 결정적으로 재현할 수 없다.
sleep 기반 flaky 테스트를 넣지 않고, 결정적 만료 fixture 가 없으므로 STALE_FRESHNESS capability
를 선언하는 adapter 에서만 이 시나리오가 실행된다(runner 가 미지원 시 UNSUPPORTED 처리).
"""
from benchmark.contracts import Cap
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict

ID = "MGB-03"
TITLE = "stale-approval-rejection"
REQUIRES = {Cap.INIT, Cap.STALE_FRESHNESS}  # BingguPack 미지원 → runner 가 UNSUPPORTED 처리


def run(adapter, home, ctx):
    adapter.observe(home, Cap.INIT)
    o = adapter.observe(home, Cap.STALE_FRESHNESS, text="처음 판단 문장을 하기로 정했어요.")
    st = o.state
    ok = (st.get("stale_rejected") is True
          and st.get("active_before") == st.get("active_after")   # 거부 후 활성 기억 불변
          and st.get("digest_present") is False)                  # 대상 digest 미생성
    return ScenarioResult(
        ID, TITLE, ExecutionStatus.OK, Verdict.PASS if ok else Verdict.FAIL,
        reason="stale 거부=%s active불변=%s digest미생성=%s"
        % (st.get("stale_rejected"), st.get("active_before") == st.get("active_after"),
           st.get("digest_present") is False),
        evidence={"obs": o.to_dict()})
