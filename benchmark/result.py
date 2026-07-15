# -*- coding: utf-8 -*-
"""MGB v0.1 결과 모델 — execution_status 와 verdict 를 별도 축으로 기록한다.

owner 확정(2026-07-15):
  execution_status ∈ {OK, ERROR, UNSUPPORTED, SKIPPED}
  verdict          ∈ {PASS, FAIL, UNSUPPORTED, NOT_RUN}
  권장 매핑:
    실행 성공 · 계약 충족       → OK          / PASS
    실행 성공 · 계약 위반       → OK          / FAIL
    실행 자체 오류             → ERROR       / FAIL
    공개 인터페이스 부족        → UNSUPPORTED / UNSUPPORTED
    명시적 선행조건 미충족      → SKIPPED     / NOT_RUN
  ERROR · UNSUPPORTED · SKIPPED 를 PASS 로 집계하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ExecutionStatus(str, Enum):
    OK = "OK"
    ERROR = "ERROR"
    UNSUPPORTED = "UNSUPPORTED"
    SKIPPED = "SKIPPED"


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_RUN = "NOT_RUN"


# 권장 매핑(runner 가 강제하지 않고 시나리오가 명시하되, 조합 무결성만 검증).
_ALLOWED_PAIRS = {
    (ExecutionStatus.OK, Verdict.PASS),
    (ExecutionStatus.OK, Verdict.FAIL),
    (ExecutionStatus.ERROR, Verdict.FAIL),
    (ExecutionStatus.UNSUPPORTED, Verdict.UNSUPPORTED),
    (ExecutionStatus.SKIPPED, Verdict.NOT_RUN),
}


@dataclass
class ScenarioResult:
    """한 시나리오의 판정. runner 의 시나리오 계약 코드가 계산한다(adapter 가 PASS 를 반환하지 않는다)."""
    id: str
    title: str
    execution_status: ExecutionStatus
    verdict: Verdict
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    # 운영 정본(예: 운영 ledger) 불변 여부. True=불변, False=오염(hard FAIL 유발), None=측정 안 함.
    operating_state_invariant: bool | None = None

    def __post_init__(self) -> None:
        pair = (ExecutionStatus(self.execution_status), Verdict(self.verdict))
        if pair not in _ALLOWED_PAIRS:
            raise ValueError(
                "허용되지 않은 (execution_status, verdict) 조합: %s/%s — 매핑 위반"
                % (self.execution_status, self.verdict))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "execution_status": ExecutionStatus(self.execution_status).value,
            "verdict": Verdict(self.verdict).value,
            "reason": self.reason,
            "evidence": self.evidence,
            "operating_state_invariant": self.operating_state_invariant,
        }


def summarize(results: list[ScenarioResult], *, expected_total: int = 12) -> dict:
    """verdict 기준 집계. PASS 만 통과로 세고 ERROR/UNSUPPORTED/SKIPPED 를 PASS 로 섞지 않는다.

    12개 중 하나라도 결과 목록에서 누락되면 명시적으로 드러낸다(분모 축소 금지).
    """
    counts = {v: 0 for v in ("PASS", "FAIL", "UNSUPPORTED", "NOT_RUN")}
    for r in results:
        counts[Verdict(r.verdict).value] += 1
    total = len(results)
    # 운영 정본 오염이 하나라도 있으면 전체 실행 무결성 실패(개별 verdict 와 무관한 hard fail 신호).
    operating_state_ok = all(r.operating_state_invariant is not False for r in results)
    ids = [r.id for r in results]
    return {
        "PASS": counts["PASS"],
        "FAIL": counts["FAIL"],
        "UNSUPPORTED": counts["UNSUPPORTED"],
        "NOT_RUN": counts["NOT_RUN"],
        "TOTAL": total,
        "expected_total": expected_total,
        "total_matches_expected": total == expected_total,
        "operating_state_ok": operating_state_ok,
        "scenario_ids": ids,
    }
