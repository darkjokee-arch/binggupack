# -*- coding: utf-8 -*-
"""MGB-05 speaker-provenance — owner 와 AI 발화를 구분해 저장하는지.

공개 CLI 한계: explain 은 speaker 필드를 렌더링하지 않는다. v0.1 은 pair 가 owner/AI 를 구분해
저장(ai_accepts 관계)하는 것까지를 공개 관찰로 판정하고, speaker 조회 자체의 공개 노출은 SPEC 에
한계로 명시한다.
"""
from benchmark.contracts import Cap, state_int
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict

ID = "MGB-05"
TITLE = "speaker-provenance"
REQUIRES = {Cap.INIT, Cap.PAIR}


def run(adapter, home, ctx):
    adapter.observe(home, Cap.INIT)
    o = adapter.observe(home, Cap.PAIR,
                        owner_text="결론부터 짧게 받는 걸 선호합니다.",
                        ai_text="스테이징에서 먼저 검증하기로 했어요.")
    # owner/ai 두 축이 관계(ai_accepts)로 저장 = 화자 구분 저장의 공개 증거
    relation_saved = ("ai_accepts" in (o.stdout or "")) or state_int(o, "active_count") >= 2
    ok = o.exit_code == 0 and relation_saved
    return ScenarioResult(
        ID, TITLE, ExecutionStatus.OK, Verdict.PASS if ok else Verdict.FAIL,
        reason="pair owner/ai 구분 저장 exit=%s · 관계저장=%s" % (o.exit_code, relation_saved),
        evidence={"obs": o.to_dict(),
                  "note": "explain 은 speaker 필드 미노출(공개 CLI 한계) — SPEC 명시"})
