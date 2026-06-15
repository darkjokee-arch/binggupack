# -*- coding: utf-8 -*-
"""hag_l1_proposition.py — L1 명제층(원자명제).

하이브리드 AGI 계층:
  - L0 = 사람 원문 노드(불변 · raw). AI 는 L0 을 쪼개거나 수정하지 않음.
  - L1 = 원자명제(proposition). L0 한 줄을 사람이 명시 추출하거나, AI 가 제안하고 사람이 도장(stamp) 찍어 확정.

불변 (전부 selftest 증명):
  - **타입 강제 분리** — L0 는 raw(원문) 타입, L1 는 proposition 타입. L0 객체를 L1 로 직접 못 씀(별 클래스 · 변환 함수만).
  - **L0 미변경** — L1 추출은 L0 dict 를 읽기만. derived_from_l0_id 로 참조, 원문 바이트 불변.
  - **derived_from 필수** — 모든 L1 은 출처 L0 id 를 가져야 함(없으면 생성 거부).
  - **source_span 필수** — L1 은 L0 원문 내 글자 구간 (start, end) 을 가져야 함(없으면 생성 거부).
  - **ai_inferred 는 도장 전 비영구** — extracted_by='ai_inferred' 는 stamped_by 없으면 is_permanent()=False(휘발 제안).
    사람 명시추출(extracted_by='human')은 자체로 영구. AI 제안은 사람 stamp 후에만 영구.
  - 영구 저장 0 · DB write 0 · 순수 함수 · 결정론적(주입 ts).

운영 ledger(~/.binggupack/*.sqlite) 절대 미접촉 — 본 모듈은 어떤 파일/DB 도 열지 않음.
"""
import os
import sys

EXTRACT_HUMAN = "human"            # 사람 명시 추출 → 자체 영구
EXTRACT_AI = "ai_inferred"         # AI 제안 → 사람 도장 전 비영구(휘발)
VALID_EXTRACTORS = (EXTRACT_HUMAN, EXTRACT_AI)

L1_CAVEAT_AI_UNSTAMPED = "ai_inferred · 미도장 · 비영구(휘발) · 사람 stamp 전 저장 금지"


class L0Raw(object):
    """L0 = 사람 원문 노드(raw). 불변 전제. AI 는 이 객체를 만들지도, 쪼개지도 않음(사람 입력 표상)."""
    __slots__ = ("l0_id", "raw", "created_at")

    def __init__(self, l0_id, raw, created_at):
        if not l0_id:
            raise ValueError("L0 raw requires l0_id")
        if raw is None:
            raise ValueError("L0 raw requires raw text")
        self.l0_id = l0_id
        self.raw = raw                  # 원문 그대로 — 절대 가공 안 함
        self.created_at = created_at

    def slice(self, start, end):
        """source_span 검증용 — 원문에서 구간 텍스트를 읽기만(원문 불변)."""
        return self.raw[start:end]


class L1Proposition(object):
    """L1 = 원자명제. L0Raw 와 별 타입(타입 강제 분리). 변환 함수로만 생성."""
    __slots__ = ("l1_id", "proposition", "derived_from_l0_id", "source_span",
                 "extracted_by", "origin", "stamped_by", "edited_before_stamp", "created_at")

    def __init__(self, l1_id, proposition, derived_from_l0_id, source_span,
                 extracted_by, origin, created_at,
                 stamped_by=None, edited_before_stamp=False):
        # --- 필수 필드 검증 (없으면 생성 거부) ---
        if not l1_id:
            raise ValueError("L1 requires l1_id")
        if proposition is None or proposition == "":
            raise ValueError("L1 requires proposition")
        if not derived_from_l0_id:
            raise ValueError("L1 requires derived_from_l0_id (출처 L0 없는 명제 거부)")
        if (not isinstance(source_span, (tuple, list)) or len(source_span) != 2
                or not all(isinstance(x, int) for x in source_span)):
            raise ValueError("L1 requires source_span=(start,end) int 쌍")
        start, end = source_span[0], source_span[1]
        if start < 0 or end < start:
            raise ValueError("L1 source_span 범위 오류")
        if extracted_by not in VALID_EXTRACTORS:
            raise ValueError("extracted_by must be one of %r" % (VALID_EXTRACTORS,))
        self.l1_id = l1_id
        self.proposition = proposition
        self.derived_from_l0_id = derived_from_l0_id
        self.source_span = (start, end)
        self.extracted_by = extracted_by
        self.origin = origin
        self.stamped_by = stamped_by               # 사람 도장(actor) — 없으면 미도장
        self.edited_before_stamp = bool(edited_before_stamp)
        self.created_at = created_at

    def is_permanent(self):
        """영구 여부. human 추출=자체 영구. ai_inferred=stamped_by(사람 도장) 있어야 영구."""
        if self.extracted_by == EXTRACT_HUMAN:
            return True
        return self.stamped_by is not None

    def to_dict(self):
        return {
            "l1_id": self.l1_id,
            "proposition": self.proposition,
            "derived_from_l0_id": self.derived_from_l0_id,
            "source_span": list(self.source_span),
            "extracted_by": self.extracted_by,
            "origin": self.origin,
            "stamped_by": self.stamped_by,
            "edited_before_stamp": self.edited_before_stamp,
            "created_at": self.created_at,
            "is_permanent": self.is_permanent(),
            "caveat": None if self.is_permanent() else L1_CAVEAT_AI_UNSTAMPED,
        }


def extract_l1_human(l0, l1_id, source_span, created_at, proposition=None, origin="human_explicit"):
    """사람 명시 추출. L0 원문 구간을 사람이 명제로 확정. 자체 영구.

    proposition 미지정 시 L0 원문의 source_span 구간을 그대로 명제로 사용(타입만 raw→proposition 전환).
    L0 dict 는 읽기만 — 원문 불변.
    """
    if not isinstance(l0, L0Raw):
        raise TypeError("extract_l1 requires L0Raw (타입 강제 분리 — L0 raw != L1 proposition)")
    start, end = source_span[0], source_span[1]
    if end > len(l0.raw):
        raise ValueError("source_span 이 L0 원문 길이를 초과")
    text = proposition if proposition is not None else l0.slice(start, end)
    return L1Proposition(
        l1_id=l1_id, proposition=text, derived_from_l0_id=l0.l0_id,
        source_span=(start, end), extracted_by=EXTRACT_HUMAN, origin=origin,
        created_at=created_at, stamped_by=None, edited_before_stamp=False)


def propose_l1_ai(l0, l1_id, proposition, source_span, created_at, origin="ai_inference"):
    """AI 제안. 도장 전이므로 비영구(휘발). stamped_by 없음."""
    if not isinstance(l0, L0Raw):
        raise TypeError("propose_l1 requires L0Raw (타입 강제 분리)")
    start, end = source_span[0], source_span[1]
    if end > len(l0.raw):
        raise ValueError("source_span 이 L0 원문 길이를 초과")
    return L1Proposition(
        l1_id=l1_id, proposition=proposition, derived_from_l0_id=l0.l0_id,
        source_span=(start, end), extracted_by=EXTRACT_AI, origin=origin,
        created_at=created_at, stamped_by=None, edited_before_stamp=False)


def stamp_l1(prop, stamped_by, edited=False, attestation=None, verifier=None):
    """사람 도장. AI 제안(ai_inferred)을 사람이 승인 → 영구화. actor='human' 외 거부(allowlist).

    H2-1 — AI 제안(ai_inferred) 도장은 attestation 의 dict 값을 믿지 않고, **vault verifier 콜백**으로만 검증.
      verifier = hag_commit_reveal.CommitRevealVault.verify_attestation (HMAC 위조 차단).
      - verifier 없음 → 차단(검증 수단 없는 AI발 영구화 금지).
      - verifier(attestation) False → 차단(위조 dict·blind 미통과·copy 의심).
    사람 명시추출(human)은 자체 영구라 attestation 불필요.

    edited=True 면 사람이 도장 전 명제를 수정했음을 기록(edited_before_stamp).
    원본 prop 은 불변(새 L1Proposition 반환).
    """
    if stamped_by != "human":
        raise PermissionError("stamp actor must be 'human' (allowlist default-deny · AI 자동 도장 0)")
    if prop.extracted_by == EXTRACT_AI:
        if not callable(verifier):
            raise PermissionError(
                "AI 제안 도장엔 attestation verifier(vault.verify_attestation) 필수 — dict 값만 신뢰 금지(H2-1)")
        if not verifier(attestation):
            raise PermissionError("attestation 검증 실패(위조/blind 미통과/copy 의심) — 도장 차단")
    return L1Proposition(
        l1_id=prop.l1_id, proposition=prop.proposition,
        derived_from_l0_id=prop.derived_from_l0_id, source_span=prop.source_span,
        extracted_by=prop.extracted_by, origin=prop.origin, created_at=prop.created_at,
        stamped_by=stamped_by, edited_before_stamp=bool(edited))


# ---------------- selftest (순수 함수 · write 0 · 결정론적) ----------------
def _selftest():
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    TS = "2026-06-15T00:00:00Z"        # 주입 ts(결정론) — 실시간 시각/난수 금지
    raw = "빌드가 깨져 있다. 배포 전 확인하자."
    l0 = L0Raw(l0_id="L0-1", raw=raw, created_at=TS)
    raw_snapshot = l0.raw               # L0 미변경 검증 baseline

    # 1) 사람 명시 추출 → L1 생성 · 자체 영구
    span = (0, 9)                       # "빌드가 깨져 있다"
    p_h = extract_l1_human(l0, "L1-h1", span, TS)
    ck(isinstance(p_h, L1Proposition), "사람 추출 → L1Proposition 생성")
    ck(p_h.proposition == raw[0:9], "L1 명제 = L0 구간 텍스트")
    ck(p_h.is_permanent() is True, "human 추출은 자체 영구")

    # 2) derived_from 존재
    ck(p_h.derived_from_l0_id == "L0-1", "derived_from_l0_id 존재(출처 L0)")

    # 3) source_span 필수 — 누락/형식오류 거부
    try:
        L1Proposition("x", "p", "L0-1", None, EXTRACT_HUMAN, "o", TS); ck(False, "source_span 누락 거부")
    except ValueError:
        ck(True, "source_span 누락 → 생성 거부")
    try:
        L1Proposition("x", "p", "L0-1", (5,), EXTRACT_HUMAN, "o", TS); ck(False, "source_span 형식 거부")
    except ValueError:
        ck(True, "source_span 형식오류 → 생성 거부")
    ck(p_h.source_span == (0, 9), "source_span (start,end) 보존")

    # 4) derived_from 누락 거부
    try:
        L1Proposition("x", "p", "", (0, 1), EXTRACT_HUMAN, "o", TS); ck(False, "derived_from 누락 거부")
    except ValueError:
        ck(True, "derived_from_l0_id 누락 → 생성 거부")

    # 5) ai_inferred 는 도장 전 비영구
    span_ai = (11, len(raw))           # "배포 전 확인하자." 구간(원문 길이 내)
    p_ai = propose_l1_ai(l0, "L1-ai1", "배포 전 빌드를 확인해야 한다", span_ai, TS)
    ck(p_ai.extracted_by == EXTRACT_AI, "AI 제안 extracted_by=ai_inferred")
    ck(p_ai.stamped_by is None and p_ai.is_permanent() is False, "ai_inferred 미도장 → 비영구(휘발)")
    ck(p_ai.to_dict()["caveat"] == L1_CAVEAT_AI_UNSTAMPED, "미도장 AI 제안 caveat 표시")

    # 6) 사람 도장 → 영구화 (AI 제안은 vault verifier 콜백으로만 검증 · H2-1)
    #    GOOD_V = vault.verify_attestation 모사(실제 HMAC 검증은 hag_commit_reveal selftest 가 커버).
    GOOD_V = lambda att: (isinstance(att, dict) and att.get("blind_passed") is True
                          and att.get("copy_suspected") is not True)
    ATT_OK = {"qid": "q1", "blind_passed": True, "copy_suspected": False}
    p_ai_stamped = stamp_l1(p_ai, "human", edited=False, attestation=ATT_OK, verifier=GOOD_V)
    ck(p_ai_stamped.is_permanent() is True, "사람 도장(+verifier 통과) 후 ai_inferred 영구화")
    ck(p_ai_stamped.stamped_by == "human", "stamped_by=human 기록")
    ck(p_ai.is_permanent() is False, "원본 AI 제안 객체는 불변(여전히 비영구)")

    # 6b) H2-1 — verifier 없으면 차단(dict 값만 믿지 않음)
    try:
        stamp_l1(p_ai, "human", attestation=ATT_OK); ck(False, "verifier 누락 도장 차단 실패")
    except PermissionError:
        ck(True, "AI 제안 도장 verifier 누락 → 차단")
    # 6c) verifier 가 위조 dict 를 reject (vault HMAC 불일치 모사)
    try:
        stamp_l1(p_ai, "human", attestation={"blind_passed": True, "copy_suspected": False},
                 verifier=lambda att: False); ck(False, "위조 attestation 차단 실패")
    except PermissionError:
        ck(True, "verifier reject(위조 dict) → 도장 차단(H2-1)")
    # 6d) blind 미통과 / copy 의심은 GOOD_V 가 False → 차단
    try:
        stamp_l1(p_ai, "human", attestation={"blind_passed": False, "copy_suspected": False},
                 verifier=GOOD_V); ck(False, "blind 미통과 차단 실패")
    except PermissionError:
        ck(True, "blind_passed=False → verifier False → 도장 차단")
    try:
        stamp_l1(p_ai, "human", attestation={"blind_passed": True, "copy_suspected": True},
                 verifier=GOOD_V); ck(False, "copy 의심 차단 실패")
    except PermissionError:
        ck(True, "copy_suspected=True → verifier False → 베껴쓰기 도장 차단")

    # 7) 도장 actor allowlist — human 외 거부
    for bad in ("ai", "auto", "system", "agent", "AUTO", "", None):
        try:
            stamp_l1(p_ai, bad, attestation=ATT_OK, verifier=GOOD_V)
            ck(False, "actor=%r 도장 차단 실패" % (bad,))
        except PermissionError:
            ck(True, "actor=%r 도장 차단(allowlist)" % (bad,))

    # 8) edited_before_stamp 기록
    p_edited = stamp_l1(p_ai, "human", edited=True, attestation=ATT_OK, verifier=GOOD_V)
    ck(p_edited.edited_before_stamp is True, "edited_before_stamp=True 기록")
    ck(p_h.edited_before_stamp is False, "기본 edited_before_stamp=False")

    # 9) 타입 강제 분리 — L0 객체를 L1 추출 함수에 직접 못 씀(dict/str 거부)
    try:
        extract_l1_human({"l0_id": "L0-1", "raw": raw}, "x", (0, 3), TS); ck(False, "dict L0 거부 실패")
    except TypeError:
        ck(True, "L0 dict(raw 아님) → 추출 거부(타입 강제 분리)")
    try:
        propose_l1_ai(raw, "x", "p", (0, 3), TS); ck(False, "str L0 거부 실패")
    except TypeError:
        ck(True, "L0 str → 제안 거부(타입 강제 분리)")

    # 10) L0 미변경 — 모든 L1 추출/제안/도장 후 원문 바이트 불변
    ck(l0.raw == raw_snapshot, "L0 원문 미변경(추출/제안/도장 후 raw 불변)")

    # 11) extracted_by 잘못된 값 거부
    try:
        L1Proposition("x", "p", "L0-1", (0, 1), "robot", "o", TS); ck(False, "잘못된 extracted_by 거부 실패")
    except ValueError:
        ck(True, "extracted_by 비허용값 → 거부")

    # 12) source_span 이 L0 길이 초과 시 거부
    try:
        extract_l1_human(l0, "x", (0, len(raw) + 10), TS); ck(False, "span 초과 거부 실패")
    except ValueError:
        ck(True, "source_span L0 길이 초과 → 거부")

    print("\nGATE: %s" % ("GO" if ok else "STOP"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
