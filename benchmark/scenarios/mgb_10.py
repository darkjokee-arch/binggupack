# -*- coding: utf-8 -*-
"""MGB-10 tamper-detection — 장부의 부분 손상·변조가 감지되는지.

★v0.1 black-box 공개 CLI 프로필에서 BingguPack 은 이 항목을 UNSUPPORTED 로 남긴다:
  · store_checksum / verify_tail_state 내부 기능은 존재하나, 출하 공개 CLI 로 독립 호출·검증 불가.
  · audit 0건일 때 무조건 통과하는 경로가 있음.
  · checksum 이 내부 PRAGMA 컬럼 순서에 결속되고 speaker 가 제외돼 외부 adapter 가 독립 재현 불가.
따라서 공개 CLI 로 PASS 를 주장할 수 없다. 내부 함수를 직접 호출해 PASS 시키지 않는다.
INTEGRITY_PUBLIC capability 를 선언하는 adapter(예: toy_conforming)만 이 시나리오가 실행된다.
"""
from benchmark.contracts import Cap
from benchmark.result import ExecutionStatus, ScenarioResult, Verdict

ID = "MGB-10"
TITLE = "tamper-detection"
REQUIRES = {Cap.INIT, Cap.INTEGRITY_PUBLIC}  # BingguPack 미지원 → runner 가 UNSUPPORTED 처리


def run(adapter, home, ctx):
    # INTEGRITY_PUBLIC 를 지원하는 adapter 에서만 도달한다.
    adapter.observe(home, Cap.INIT)
    clean = adapter.observe(home, Cap.INTEGRITY_PUBLIC, tamper=None)
    tampered = adapter.observe(home, Cap.INTEGRITY_PUBLIC, tamper={"synthetic": True})
    # 손상 없으면 통과 신호, 합성 손상은 감지(fail-closed)
    ok = (clean.state.get("tamper_detected") is False
          and tampered.state.get("tamper_detected") is True)
    return ScenarioResult(
        ID, TITLE, ExecutionStatus.OK, Verdict.PASS if ok else Verdict.FAIL,
        reason="clean_detected=%s · tampered_detected=%s"
        % (clean.state.get("tamper_detected"), tampered.state.get("tamper_detected")),
        evidence={"clean": clean.to_dict(), "tampered": tampered.to_dict()})
