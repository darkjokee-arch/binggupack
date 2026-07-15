# -*- coding: utf-8 -*-
"""MGB-07 supersede-with-history — 잘못된 기억을 흔적없이 삭제하지 않고 교체·폐기하는지.

폐기 후 원본은 물리 삭제되지 않고 'deprecated' 상태로 이력에 남아야 한다.
"""
from benchmark.contracts import Cap
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict

ID = "MGB-07"
TITLE = "supersede-with-history"
REQUIRES = {Cap.INIT, Cap.SAVE, Cap.SUPERSEDE}


def run(adapter, home, ctx):
    adapter.observe(home, Cap.INIT)
    s = adapter.observe(home, Cap.SAVE, text="배포 전에는 스테이징에서 먼저 검증하기로 했어요.")
    nid = (s.state.get("node_ids") or [None])[0]
    if not nid:
        return ScenarioResult(ID, TITLE, ExecutionStatus.ERROR, Verdict.FAIL,
                              reason="저장 후 memory-id 미획득", evidence={"save": s.to_dict()})
    o = adapter.observe(home, Cap.SUPERSEDE, node_id=nid, n=1)
    ok = (o.exit_code == 0
          and o.state.get("target_state_after") == "deprecated"
          and o.state.get("target_present_after") is True)  # 물리삭제 0 = 이력 보존
    return ScenarioResult(
        ID, TITLE, ExecutionStatus.OK, Verdict.PASS if ok else Verdict.FAIL,
        reason="폐기 exit=%s · state_after=%s · present(이력보존)=%s"
        % (o.exit_code, o.state.get("target_state_after"), o.state.get("target_present_after")),
        evidence={"obs": o.to_dict()})
