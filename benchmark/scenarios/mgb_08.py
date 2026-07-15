# -*- coding: utf-8 -*-
"""MGB-08 cross-model-consistency — 새 프로세스가 동일 로컬 정본의 승인 기억을 회상하는지.

★자명통과 방지(4cli 사후 반영): 저장 1건 전체덤프가 아니라, target 1 + distractor 2 + hard
negative(target 과 어휘 일부 겹침) 를 저장한 뒤, target 을 구별하는 질의로 회상해 target 만
반환되고 distractor/hard-negative 는 배제되는지 확인한다. 전체 목록 dump 명령을 회상 근거로 쓰지
않는다. (v0.1 은 동일 로컬 정본을 공유하는 새 프로세스 회상을 검증하며, 실제 서로 다른 모델
클라이언트 2개를 돌리는 cross-model E2E 는 별도 후속 Wave.)
"""
from benchmark.contracts import Cap
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict

ID = "MGB-08"
TITLE = "cross-model-consistency"
REQUIRES = {Cap.INIT, Cap.SAVE, Cap.RECALL_FRESH}

_TARGET = "결제 배포 전에는 스테이징에서 먼저 검증하기로 했어요."
_DIST1 = "매주 금요일 오후에는 주간 회고를 하기로 정했어요."
_DIST2 = "결론부터 짧게 받는 걸 선호합니다."
_HARD = "배포 일정은 다음 주로 미루기로 정했어요."  # ' 배포' 어휘 겹침 · 스테이징 검증 아님(hard negative)


def run(adapter, home, ctx):
    adapter.observe(home, Cap.INIT)
    for t in (_TARGET, _DIST1, _DIST2, _HARD):
        s = adapter.observe(home, Cap.SAVE, text=t)
        if s.exit_code != 0:
            return ScenarioResult(ID, TITLE, ExecutionStatus.ERROR, Verdict.FAIL,
                                  reason="fixture 저장 실패: %s" % t, evidence={"save": s.to_dict()})
    r = adapter.observe(home, Cap.RECALL_FRESH, query="결제 배포 스테이징 검증")
    out = r.stdout or ""
    has_target = _TARGET in out
    dist_absent = all(x not in out for x in (_DIST1, _DIST2, _HARD))
    ok = r.exit_code == 0 and has_target and dist_absent  # target 회상 + distractor·hard-neg 배제
    return ScenarioResult(
        ID, TITLE, ExecutionStatus.OK, Verdict.PASS if ok else Verdict.FAIL,
        reason="새 프로세스 회상 exit=%s · target=%s · distractor/hard-neg 배제=%s"
        % (r.exit_code, has_target, dist_absent),
        evidence={"recall": r.to_dict()})
