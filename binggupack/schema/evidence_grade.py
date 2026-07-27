# -*- coding: utf-8 -*-
"""evidence_locator 등급 정본 — `match_method` → (confidence, 뜻) 단일 표. 순수 함수·write 0.

왜 패키지로 올렸나 (1단계 결함 D10):
  등급 표가 백필(`scripts/binggu_backfill_evidence_locator.py`)에만 있었고, 앞막이
  (`scripts/openbinggu_conversation_candidate_save.py`)는 `confidence="T1"` 을 하드코딩했다.
  그래서 **같은 테이블을 두 방식으로 세면 답이 갈렸다** — `match_method IN PRIMARY_METHODS`
  로 세면 앞막이 행이 1차 0건, `confidence=='T1'` 로 세면 앞막이 행이 전건 1차. 두 소비자가
  같은 표를 보게 하는 것이 이 모듈의 존재 이유다(두 번째 진실 원본 금지).

등급 축 (스펙 §1 · 재검증 NEW2.10 — "요약본을 원문으로 계상 금지")
  T1  원문 좌표 확보 — 1차 출처. 이것만 G7 '증거 위치 보유' 분자에 들어간다.
  T2  위치는 있으나 원문 대화가 아니거나(2차 요약본) 좌표가 자기참조/폴백 — owner 확인 대상.
  T3  메아리(빙구팩 자기 렌더)·자기참조 — 독립 원본 아님.
  T4  회수 불가 — `evidence_locator` 에 행을 만들지 않고 `system_provenance` 에 사유만.

CLI: python -m binggupack.schema.evidence_grade --selftest
"""
from __future__ import annotations

import sys

# ── 등급 표 ──────────────────────────────────────────────────────────────────
# match_method : (기본 confidence, 한 줄 설명)
# ★ `live_capture` 의 confidence 는 행마다 다르다(아래 live_capture_confidence).
#   표에 적힌 값은 **바닥값(T2)** 이다 — 근거를 확인하기 전에는 1차로 세지 않는다.
GRADE = {
    "session_exact":           ("T1", "세션로그 대화 턴 원문 정확일치(1차 출처)"),
    "session_norm":            ("T1", "세션로그 대화 턴 공백정규화 일치(1차 출처·원문 슬라이스 보존)"),
    "live_capture":            ("T2", "저장 시점 앞막이 — origin+독립 컨테이너면 T1, 폴백/자기참조면 T2"),
    "session_speaker_mismatch": ("T2", "세션로그에서 찾았으나 노드 화자와 턴 역할 불일치 — owner 확인"),
    "session_late":            ("T2", "세션로그 위치이나 저장 시각 이후 발화(재언급) — owner 확인"),
    "md_exact":                ("T2", "문서(2차 요약본) 라인 일치 — 원문 대화 아님"),
    "session_echo":            ("T3", "도구 입출력·주입 블록에서만 발견(빙구팩 자기 렌더) — 원본 아님"),
    "self_reference":          ("T3", "컨테이너가 발췌 자신과 동일(독립 원본 아님·NEW2.10 강등)"),
    "none":                    ("T4", "회수 불가 — evidence_locator 미기재, 사유만 기록"),
}

# 사후 회수(백필)에서 '1차 출처'로 세는 방법 2종. **이 튜플의 내용은 바꾸지 않는다** —
# 백필 집계(`_stats`)와 G7 분자 정의가 이 축을 그대로 쓰고 있어서, 여기에 live_capture 를
# 끼워 넣으면 폴백 좌표(T2) 행까지 분자에 섞인다(D9 가 경고한 지표 오염 재현).
PRIMARY_METHODS = ("session_exact", "session_norm")

# 앞막이 축 — 1차 여부가 method 가 아니라 confidence 로 갈린다.
LIVE_METHODS = ("live_capture",)
PRIMARY_CONFIDENCE = "T1"

# 폴백 source_id 접두 — 원본 좌표가 없어 발화 자신을 해시한 자기좌표
# (`openbinggu_conversation_candidate_save._source_coords`).
FALLBACK_SOURCE_PREFIX = "utterance:"

# 우선순위(작을수록 좋음) — 같은 evidence 에 여러 후보가 걸리면 이 순서로 고른다.
# live_capture 는 저장 시점 동결이라 어떤 사후 회수보다 우선(-1). 백필은 앞막이 행을
# 만들지 않으므로 실제로 경쟁하지는 않지만, KeyError 없이 조회되도록 등록해 둔다.
METHOD_RANK = {"live_capture": -1, "session_exact": 0, "session_norm": 1,
               "session_speaker_mismatch": 2, "session_late": 3, "md_exact": 4,
               "session_echo": 5}


def grade_of(match_method):
    """(confidence, 설명) 조회. 미등록 method 는 조용히 넘기지 않고 ('?', '') 로 표면화한다."""
    return GRADE.get(match_method, ("?", ""))


def live_capture_confidence(source_id, container_sha, excerpt_sha_value):
    """앞막이 1행의 등급 산출. 반환 (confidence, reason). 순수 함수.

    T1 은 조건 2개를 **둘 다** 만족할 때만 준다.
      ① source_id 가 폴백이 아니다 — `utterance:<hash>` 는 발화 자신을 해시한 자기좌표라,
         30일 롤링으로 세션 로그가 사라지면 가리킬 원본이 남지 않는다(D8 실측: 운영 경로가
         origin 미배선이라 전건 이 폴백이었다).
      ② container_sha != excerpt_sha — 발췌를 담은 **독립 컨테이너**가 실재한다. 같으면
         '컨테이너가 발췌 자신' 이라, 백필이라면 self_reference(T3)로 강등하는 형태다(NEW2.10).

    하나라도 어긋나면 T2. 앞막이에는 '저장 순간 그 발화를 실제로 받았다'는 사실이 추가로
    있으므로 사후 회수의 self_reference(T3)까지 내리지는 않는다 — 대신 1차 출처 분자에서는
    빠진다(`is_primary_source`). 등급을 속이지 않는 것이 이 함수의 유일한 일이다.
    """
    sid = "" if source_id is None else str(source_id)
    csha = "" if container_sha is None else str(container_sha)
    esha = "" if excerpt_sha_value is None else str(excerpt_sha_value)
    has_origin = bool(sid) and not sid.startswith(FALLBACK_SOURCE_PREFIX)
    independent = bool(csha) and csha != esha
    if has_origin and independent:
        return "T1", "origin+independent_container"
    if not has_origin and not independent:
        return "T2", "fallback_source+self_container"
    if not has_origin:
        return "T2", "fallback_source"
    return "T2", "self_container"


def is_primary_source(match_method, confidence=None):
    """G7 '증거 위치 보유' 분자에 넣어도 되는 행인가 — **집계 축 단일 판정자**.

    사후 회수는 method 로, 앞막이는 confidence 로 갈린다. 두 축을 한 함수로 묶어야
    `count(*) WHERE match_method IN (...)` 과 `count(*) WHERE confidence='T1'` 이
    서로 다른 답을 내는 상태(D10)가 재발하지 않는다.
    """
    if match_method in PRIMARY_METHODS:
        return True
    if match_method in LIVE_METHODS:
        return confidence == PRIMARY_CONFIDENCE
    return False


# ── selftest ─────────────────────────────────────────────────────────────────

def _selftest():
    ok = tot = 0

    def chk(name, cond, extra=""):
        nonlocal ok, tot
        tot += 1
        ok += 1 if cond else 0
        print(("[PASS] " if cond else "[FAIL] ") + name + (("  " + str(extra)) if extra else ""))

    print("=" * 74)
    print("evidence_grade — 등급 정본 selftest (순수 함수 · write 0)")
    print("=" * 74)

    chk("1 live_capture 가 등급표에 등록돼 있다(D10 — 표에 없어 집계가 갈리던 항목)",
        "live_capture" in GRADE, grade_of("live_capture"))
    chk("2 앞막이 바닥값은 T2(근거 확인 전 1차 승격 금지)",
        GRADE["live_capture"][0] == "T2")
    chk("3 origin 명시 + 독립 컨테이너 → T1",
        live_capture_confidence("session:S-1", "cafe", "beef")[0] == "T1")
    chk("4 폴백 source_id(utterance:) → T1 아님",
        live_capture_confidence("utterance:abcd", "cafe", "beef") == ("T2", "fallback_source"),
        live_capture_confidence("utterance:abcd", "cafe", "beef"))
    chk("5 container == excerpt(자기참조) → T1 아님",
        live_capture_confidence("session:S-1", "same", "same") == ("T2", "self_container"))
    chk("6 둘 다 어긋나면 사유가 합쳐져 보인다",
        live_capture_confidence("utterance:x", "same", "same")
        == ("T2", "fallback_source+self_container"))
    chk("7 source_id 빈칸/None 도 폴백 취급(빈칸을 T1 로 올리지 않음)",
        live_capture_confidence(None, "cafe", "beef")[0] == "T2"
        and live_capture_confidence("", "cafe", "beef")[0] == "T2")
    chk("8 container_sha 부재 → 독립 컨테이너 없음",
        live_capture_confidence("session:S-1", None, "beef") == ("T2", "self_container"))

    chk("9 PRIMARY_METHODS 는 사후 회수 2종 고정(앞막이 혼입 금지 — D9 지표 오염)",
        PRIMARY_METHODS == ("session_exact", "session_norm"))
    chk("10 1차 판정: 사후 회수는 method 로 통과", is_primary_source("session_exact", "T1")
        and is_primary_source("session_norm", "T1"))
    chk("11 1차 판정: 앞막이는 T1 만 통과, T2 는 탈락",
        is_primary_source("live_capture", "T1") and not is_primary_source("live_capture", "T2"))
    chk("12 1차 판정: 2차/메아리/자기참조/미회수는 전부 탈락",
        not any(is_primary_source(m, GRADE[m][0])
                for m in ("md_exact", "session_echo", "self_reference", "none",
                          "session_late", "session_speaker_mismatch")))
    chk("13 미등록 method 는 ('?','') 로 표면화(조용한 통과 금지)",
        grade_of("no_such_method") == ("?", "") and not is_primary_source("no_such_method", "T1"))
    chk("14 등급표 전 항목이 T1~T4 안에 있다",
        all(c in ("T1", "T2", "T3", "T4") for c, _ in GRADE.values()))
    chk("15 METHOD_RANK 는 등급표 안의 method 만 담는다(오탈자 조기 검출)",
        set(METHOD_RANK) <= set(GRADE), set(METHOD_RANK) - set(GRADE))
    chk("16 앞막이가 사후 회수보다 우선순위가 높다(저장 시점 동결)",
        METHOD_RANK["live_capture"] < METHOD_RANK["session_exact"])

    print("\nRESULT: %d/%d" % (ok, tot))
    gate = "GO" if ok == tot else "NO-GO"
    print("GATE: %s" % gate)
    return ok == tot


if __name__ == "__main__":
    sys.exit(0 if _selftest() else 1)
