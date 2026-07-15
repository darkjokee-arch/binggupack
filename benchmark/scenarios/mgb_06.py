# -*- coding: utf-8 -*-
"""MGB-06 evidence-explain — 기억의 근거·이력을 설명할 수 있는지.

승인 memory-id 를 공개 CLI 로 얻고, explain 이 그 id/근거에 연결되며, 존재하지 않는 id 는
설명 성공으로 위장하지 않아야(negative control) 한다. 단순 키워드 포함만으로 통과시키지 않는다.
"""
from benchmark.contracts import Cap
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict

ID = "MGB-06"
TITLE = "evidence-explain"
REQUIRES = {Cap.INIT, Cap.SAVE, Cap.EXPLAIN}


def run(adapter, home, ctx):
    adapter.observe(home, Cap.INIT)
    s = adapter.observe(home, Cap.SAVE, text="배포 전에는 스테이징에서 먼저 검증하기로 했어요.")
    nid = (s.state.get("node_ids") or [None])[0]
    if not nid:
        return ScenarioResult(ID, TITLE, ExecutionStatus.ERROR, Verdict.FAIL,
                              reason="저장 후 memory-id 미획득", evidence={"save": s.to_dict()})
    ex = adapter.observe(home, Cap.EXPLAIN, node_id=nid)
    tail = nid.rsplit(":", 1)[-1]
    id_linked = (nid in (ex.stdout or "")) or (tail in (ex.stdout or ""))
    has_evidence = ("근거" in (ex.stdout or "")) or ("evidence" in (ex.stdout or "").lower()) \
        or ("->" in (ex.stdout or ""))
    # negative control: 존재하지 않는 id 는 동일 근거로 연결되면 안 됨
    neg = adapter.observe(home, Cap.EXPLAIN, node_id="node:CONV:00000000")
    neg_ok = (nid not in (neg.stdout or "")) and (
        "찾을 수 없" in (neg.stdout or "") or neg.exit_code != 0 or not (neg.stdout or "").strip())
    ok = ex.exit_code == 0 and id_linked and has_evidence and neg_ok
    return ScenarioResult(
        ID, TITLE, ExecutionStatus.OK, Verdict.PASS if ok else Verdict.FAIL,
        reason="explain id연결=%s 근거=%s negative제어=%s" % (id_linked, has_evidence, neg_ok),
        evidence={"explain": ex.to_dict(), "negative": neg.to_dict()})
