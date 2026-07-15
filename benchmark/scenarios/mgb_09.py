# -*- coding: utf-8 -*-
"""MGB-09 remote-intent-no-ledger-write — 원격 intent 만으로 로컬 장부 write 가 없어야 한다."""
from benchmark.contracts import Cap, state_int
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict

ID = "MGB-09"
TITLE = "remote-intent-no-ledger-write"
REQUIRES = {Cap.INIT, Cap.REMOTE_INTENT}


def run(adapter, home, ctx):
    adapter.observe(home, Cap.INIT)
    o = adapter.observe(home, Cap.REMOTE_INTENT)
    before, after = state_int(o, "active_before"), state_int(o, "active_after")
    ok = o.exit_code == 0 and before == after  # 원격 intent 조회로 로컬 활성 기억 불변
    return ScenarioResult(
        ID, TITLE, ExecutionStatus.OK, Verdict.PASS if ok else Verdict.FAIL,
        reason="원격 intent 조회 exit=%s · 로컬 active %s→%s (불변 기대)"
        % (o.exit_code, before, after),
        evidence={"obs": o.to_dict()})
