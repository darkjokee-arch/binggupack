# -*- coding: utf-8 -*-
"""MGB-11 untracked-candidate-is-not-memory — 자동수집/미리보기 후보와 활성 기억이 분리되는지.

후보 미리보기(preview)는 저장이 아니므로 활성 기억 수를 늘리지 않아야 한다.
"""
from benchmark.contracts import Cap, state_int
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict

ID = "MGB-11"
TITLE = "untracked-candidate-is-not-memory"
REQUIRES = {Cap.INIT, Cap.PREVIEW, Cap.LIST_ACTIVE}


def run(adapter, home, ctx):
    adapter.observe(home, Cap.INIT)
    before = state_int(adapter.observe(home, Cap.LIST_ACTIVE), "active_count")
    p = adapter.observe(home, Cap.PREVIEW, text="후보만 보여줄 판단 문장 하기로 정했어요.")
    after = state_int(adapter.observe(home, Cap.LIST_ACTIVE), "active_count")
    candidate_shown = bool(p.state.get("preview_id"))
    ok = p.exit_code == 0 and candidate_shown and after == before
    return ScenarioResult(
        ID, TITLE, ExecutionStatus.OK, Verdict.PASS if ok else Verdict.FAIL,
        reason="후보 표시=%s · active %s→%s (후보는 활성 아님 기대)"
        % (candidate_shown, before, after),
        evidence={"preview": p.to_dict()})
