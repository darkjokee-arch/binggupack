# -*- coding: utf-8 -*-
"""label_kind 한영 매핑 단일 정본(canonical) + deterministic 5종 분류기.

v1.16 strangler Phase2: 정본 로직을 scripts/ 에서 binggupack.classifier 패키지로 이관.
scripts/openbinggu_label_kind_map.py 는 이 모듈을 re-export 하는 backward-compatible
thin wrapper 다(기존 bare-name import 호환). **매핑 정의 정본은 이 모듈이다.**

배경 (4cli R3 지시 1): A0 validator=영문 5종(doc/evidence/concept/state/judgment),
loader=한글 5종(문서/증거/개념/상태/판단), 생산기=한글 "판단" 하드코딩 — 매핑표 없이
정렬하면 적재 전건 거부. 이 모듈이 유일한 매핑 정본이다 (다른 곳에 매핑 중복 정의 금지).

분류 규칙: 전부 정규식(LLM 0·멱등). 명확히 매칭될 때만 해당 종, 아니면 "판단" fallback
(현행 하드코딩과 동일값 → 회귀 0). rule_id로 분류 근거 추적.
"""
import re

# ---------------- 매핑 정본 ----------------

KIND_KO = ["문서", "증거", "개념", "상태", "판단"]
KO2EN = {"문서": "doc", "증거": "evidence", "개념": "concept", "상태": "state", "판단": "judgment"}
EN2KO = {v: k for k, v in KO2EN.items()}
# localbinggu_merge_adapter.NODE_MAP 과 동일 (정합 검증은 selftest에서)
KIND_TO_SPACE_NTYPE = {
    "문서": ("resource", "Document"), "증거": ("evidence", "Evidence"),
    "개념": ("concept", "Concept"), "상태": ("claim", "Claim"), "판단": ("claim", "Claim"),
}

# ---------------- deterministic 분류 규칙 (우선순위 순) ----------------
# 보수 원칙: 패턴이 명확할 때만 해당 종. 애매하면 판단 fallback (오분류보다 안전).

_RULES = [
    ("증거", "ev_record", re.compile(
        r"(기록되어 있|기록돼 있|로그에 .{0,12}(남|찍|있)|적혀 있|첨부되|캡처(했|된|되)|스크린샷|출력에 .{0,8}(나타|찍|보))")),
    ("문서", "doc_ref", re.compile(
        r"^(이|본|해당) ?(문서|보고서|설계서|가이드|튜토리얼|README|runbook)|(문서|보고서|설계서)(는|가) .{0,20}(정의|기술|설명|규정)한다")),
    ("개념", "concept_def", re.compile(
        r"((이|란|라)는 .{0,16}(절차|개념|규칙|원칙|방식|용어)(이다|다)\.?$|[을를] (말한다|의미한다|뜻한다)\.?$|(이란|란) )")),
    ("상태", "state_now", re.compile(
        r"((상태|중)(이다|다)\.?$|진행 중|가동 중|완료(된 상태|되어 있)|남아 ?있다|되어 ?있다\.?$|^현재 )")),
    ("판단", "judgment_verdict", re.compile(
        r"(해야 (한다|함)|하는 (것이|게) (낫|좋)|보류(한다|함)|진행(한다|함)|채택(한다|함)|기각(한다|함)|권고(한다|함)|금지(한다|함)|(이|가) (낫다|위험하다|안전하다)|않는 것이 (낫|좋))")),
]

FALLBACK = ("판단", "fallback_judgment")


def classify_label_kind(sentence):
    """핵심 문장 → (label_kind_ko, rule_id). 100% deterministic·멱등."""
    s = (sentence or "").strip()
    if not s:
        return FALLBACK
    for kind, rule_id, rx in _RULES:
        if rx.search(s):
            return kind, rule_id
    return FALLBACK


def to_a0_node_type(kind_ko):
    """A0 validator(영문 5종) 호출용 변환. 미지 값은 None (호출측 fail-closed)."""
    return KO2EN.get(kind_ko)
