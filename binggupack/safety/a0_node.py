# -*- coding: utf-8 -*-
"""OpenBinggu A0 — 노드 정본(constitution) validator (정본 impl).

v1.16 strangler Phase2: 정본 로직을 scripts/openbinggu_a0_node_dryrun.py 에서 이관.
scripts/openbinggu_a0_node_dryrun.py 는 이 모듈을 re-export 하는 backward-compatible
thin wrapper(synthetic selftest 잔류)다.

헌법 제1조: 핵심 문장 노드(단어 금지) · 5종(doc·evidence·concept·state·judgment) · 세부 노드는 핵심 문장 종속.
A2 edge validator의 전제(양끝 노드가 정본 PASS) — A0가 선행 검증.

범위: 판정만. operating store write 0. apply/ingest/merge 0.
binggu_p1_config(안전벨트·가치관 로더)는 scripts/ 정본 — 진입점 sys.path 에 scripts/ 가 있으면
해소되고, 부재 시 graceful 폴백(동일 안전 기본값).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:  # 안전벨트(헌법) — 무근거 메모 보존 여부는 base 모듈이 강제
    from binggu_p1_config import preserves_unsupported_notes
except Exception:  # pragma: no cover — base 부재 시에도 보존이 안전한 기본값
    def preserves_unsupported_notes():
        return True

LABEL_KINDS = {"doc", "evidence", "concept", "state", "judgment"}
LAYER = {"doc": "objective", "evidence": "objective", "state": "objective",
         "judgment": "subjective", "concept": "neutral"}
_TERMINAL = re.compile(r"(다|음|임|까|요|함|됨|된다|이다|한다|난다)\.?$|[.!?]$")
_NOISE_PREFIX = re.compile(r"^(그리고|또한|그래서|그러나|하지만|즉|또|및)\b")


def _is_word(s):
    """단어/키워드 노드: 공백 없음 또는 너무 짧음."""
    s = (s or "").strip()
    return (" " not in s) or len(s) < 6


def _has_independent_meaning(s):
    """독립 의미: 충분 길이 + 종결성."""
    s = (s or "").strip()
    return len(s) >= 10 and bool(_TERMINAL.search(s))


def classify_node(node, nodes_index=None, status="candidate"):
    """노드 정본 판정. verdict: PASS / REVIEW / FAIL + reason + guard."""
    nodes_index = nodes_index or {}
    s = node.get("sentence", "")
    nt = node.get("node_type") or node.get("label_kind")

    def out(v, reason, guard):
        return {"verdict": v, "reason": reason, "guard": guard, "node_id": node.get("id")}

    # 🔒 안전벨트(헌법·owner 정체성) 최우선 — 형식 게이트보다 먼저.
    #   owner가 직감(owner_hunch/source=owner)으로 명시한 메모는 짧거나 비종결이어도
    #   검열·자동폐기 금지 → 근거 보강 보류(REVIEW). 명백 noise·기존 근거 있으면 정상 흐름.
    #   owner "50% 직감 실행" 가치관 보호. (적대검증: 짧은/비종결 직감 discard 라우팅 차단)
    if status == "candidate" \
            and (bool(node.get("owner_hunch")) or node.get("source") == "owner") \
            and preserves_unsupported_notes() and not node.get("noise") \
            and not node.get("evidence_refs"):
        return out("REVIEW", "근거 보강 필요(owner 직감/메모 보존·형식 면제)",
                   "node_3_hunch_needs_evidence")
    # 제1조: 단어/키워드/태그 노드
    if _is_word(s):
        return out("FAIL", "단어/키워드/태그 노드(핵심문장 아님)", "node_1_word")
    # 제1조: 독립 의미 없음(짧음/비종결)
    if not _has_independent_meaning(s):
        return out("FAIL", "독립 의미 없는 노드(짧음/비종결)", "node_1_meaning")
    # 무차별 노드화(잡정보) — 접속사 시작 등 핵심성 약함
    if _NOISE_PREFIX.search(s.strip()):
        return out("REVIEW", "잡정보 추정(접속사 시작) 무차별 노드화", "node_1_noise")
    # 제1조: 5종 외 타입
    if nt not in LABEL_KINDS:
        return out("REVIEW", "5종 외 node_type", "node_1_type5")
    # 세부 노드 종속: parent_id 있는데 parent 없거나 핵심문장 아님
    pid = node.get("parent_id")
    if pid is not None:
        parent = nodes_index.get(pid)
        if not parent or _is_word(parent.get("sentence")) or not _has_independent_meaning(parent.get("sentence")):
            return out("FAIL", "세부 노드인데 parent 핵심문장 없음", "node_1_detail_parent")
    # evidence 노드는 evidence_refs 필수
    if nt == "evidence" and not node.get("evidence_refs"):
        return out("FAIL", "증거 노드인데 evidence_refs 없음", "node_3_evidence")
    # 판단 노드 confirmed인데 evidence_refs 없음
    if nt == "judgment" and status == "confirmed" and not node.get("evidence_refs"):
        return out("FAIL", "판단 노드 확정인데 evidence_refs 없음", "node_3_judgment_confirmed")
    # objective/subjective 혼동(declared_layer ≠ 타입 유도 layer) — 구조적 결함은 evidence 무관 우선
    dl = node.get("declared_layer")
    if dl is not None and dl != LAYER.get(nt):
        return out("REVIEW", "objective/subjective 타입 혼동", "node_5_layer")
    # 철학필터 — 근거 공백 갈래 (candidate 한정, 위 형식·구조 검증을 통과한 노드 대상)
    #   🔒 안전벨트: owner 직감/짧은 메모(근거 없는 사람 판단)는 자동폐기 금지(보존).
    #   discard 는 명백 무의미/노이즈만. 보존 경계는 base 안전벨트 + 설정/가치관이 조정.
    if status == "candidate" and not node.get("evidence_refs"):
        owner_hunch = (
            bool(node.get("owner_hunch"))
            or node.get("source") == "owner"
            or nt == "judgment"  # 확정 전 판단 = 직감 메모로 간주(보존)
        )
        if owner_hunch and preserves_unsupported_notes():
            # owner 직감 — 폐기 대신 근거 보강 보류(REVIEW). 검열·자동폐기 금지.
            return out("REVIEW", "근거 보강 필요(owner 직감/메모 보존)", "node_3_hunch_needs_evidence")
        if node.get("noise"):
            # 명백 무의미/노이즈 + 무근거 → discard 사유 기록(폐기 추천, 확정은 사람)
            return out("DISCARD", "무근거 + 노이즈 노드 폐기 사유 기록", "node_3_no_evidence_noise")

    return out("PASS", "핵심 문장 노드(정본 충족)", None)


# ============================================================
# 철학필터 — 열린 분류(keep / challenge / discard) 추천
#   AI 는 추천만, 확정은 사람(actor=human). semantic centroid 새로 박지 않음.
#   가치관 부합=keep / 다르지만 근거있음=challenge / 무근거·노이즈=discard.
# ============================================================
try:  # 가치관 로더(없어도 graceful) + 안전벨트
    from binggu_p1_config import auto_value_judgment_allowed, is_confirm_actor, load_user_ontology
except Exception:  # pragma: no cover
    def load_user_ontology(home=None):
        return None

    def is_confirm_actor(actor):
        return actor == "human"

    def auto_value_judgment_allowed():
        return False

OPEN_VERDICTS = {"keep", "challenge", "discard"}


def recommend_open_classification(node, nodes_index=None, status="candidate", home=None):
    """열린 분류 추천(keep/challenge/discard). 추천만 — 확정 아님(confirmed=False 고정).

    매핑(정본 verdict → 열린 분류 추천):
      PASS                         → keep      (가치관/정본 부합)
      REVIEW(node_3_hunch...)      → challenge (다르지만 근거 보강 여지 = 도전 대상)
      REVIEW(그 외)                → challenge (사람 판단 보류 = 도전)
      DISCARD                      → discard   (무근거·노이즈)
      FAIL                         → discard   (정본 위반 — 형식/구조 결함)
    가치관 파일(user_ontology)은 '읽는 자리'로만 동봉 — 빙구팩은 내용 자동 판정 0(헌법).
    """
    base = classify_node(node, nodes_index, status=status)
    v, guard = base["verdict"], base["guard"]
    if v == "PASS":
        rec, why = "keep", "가치관/정본 부합 추천"
    elif v == "DISCARD" or v == "FAIL":
        rec, why = "discard", "무근거/정본 위반 폐기 추천"
    else:  # REVIEW
        rec, why = "challenge", "근거 있으나 재검토 대상 — 도전 추천"
    # 가치관 동봉(읽는 자리만 · 자동 판정 0)
    ontology_present = bool(load_user_ontology(home))
    return {
        "recommendation": rec,
        "why": why,
        "node_id": node.get("id"),
        "base_verdict": v,
        "base_guard": guard,
        "confirmed": False,            # AI 는 절대 확정하지 않는다(헌법)
        "actor": None,                 # 확정 actor 미정 — 사람 도장 전
        "auto_value_judgment": auto_value_judgment_allowed(),  # 항상 False
        "ontology_consulted": ontology_present,
    }


def confirm_open_classification(rec, actor, choice, reason=None):
    """열린 분류 확정 — actor=human 만 통과(allowlist). AI/auto 는 차단(추천 유지).

    choice 는 keep/challenge/discard 중 사람이 고른 최종. 반환은 기록용 dict.
    🔒 안전벨트: is_confirm_actor(=human)만 confirmed=True. 그 외 confirmed=False 유지.
    """
    if choice not in OPEN_VERDICTS:
        return {"confirmed": False, "reason": "choice_invalid", "node_id": rec.get("node_id")}
    if not is_confirm_actor(actor):
        return {"confirmed": False, "reason": "G4_no_auto",
                "node_id": rec.get("node_id"), "actor": actor}
    return {
        "confirmed": True,
        "node_id": rec.get("node_id"),
        "choice": choice,
        "actor": actor,
        "recommendation_was": rec.get("recommendation"),
        "reason": reason,
    }
