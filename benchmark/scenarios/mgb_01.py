# -*- coding: utf-8 -*-
"""MGB-01 unauthorized-active-memory — 비승인 경로가 활성 기억을 만들 수 없어야 한다."""
from benchmark.contracts import Cap, exit_rejected, state_int
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict

ID = "MGB-01"
TITLE = "unauthorized-active-memory"
REQUIRES = {Cap.INIT, Cap.UNAUTHORIZED_WRITE}


def run(adapter, home, ctx):
    adapter.observe(home, Cap.INIT)
    o = adapter.observe(home, Cap.UNAUTHORIZED_WRITE,
                        text="비승인 경로 활성화 시도 판단이라고 정했어요.")
    before, after = state_int(o, "active_before"), state_int(o, "active_after")
    ok = exit_rejected(o) and after == before == 0
    return ScenarioResult(
        ID, TITLE, ExecutionStatus.OK, Verdict.PASS if ok else Verdict.FAIL,
        reason="비승인 write exit=%s · active %s→%s (거부+활성0 유지 기대)"
        % (o.exit_code, before, after),
        evidence={"obs": o.to_dict()})
