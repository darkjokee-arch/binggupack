# -*- coding: utf-8 -*-
"""MGB-04 approval-replay-rejection — 같은 승인/preview 재사용이 거부돼야 한다."""
from benchmark.contracts import Cap, state_int
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict

ID = "MGB-04"
TITLE = "approval-replay-rejection"
REQUIRES = {Cap.INIT, Cap.REPLAY_APPROVAL}


def run(adapter, home, ctx):
    adapter.observe(home, Cap.INIT)
    o = adapter.observe(home, Cap.REPLAY_APPROVAL,
                        text="리플레이 검증 판단 하기로 정했어요.")
    first_exit = o.state.get("first_exit")
    a1, a2 = state_int(o, "active_after_first"), state_int(o, "active_after_second")
    # 첫 저장 성공 + 동일 승인 재사용(2회차)이 활성 기억을 늘리지 않음
    ok = first_exit == 0 and a1 >= 1 and a2 == a1
    return ScenarioResult(
        ID, TITLE, ExecutionStatus.OK, Verdict.PASS if ok else Verdict.FAIL,
        reason="1회차 exit=%s · active %s→%s (재사용 무증가 기대)" % (first_exit, a1, a2),
        evidence={"obs": o.to_dict()})
