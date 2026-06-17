#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu A0 — 노드 정본(constitution) validator (dry-run only).

헌법 제1조: 핵심 문장 노드(단어 금지) · 5종(doc·evidence·concept·state·judgment) · 세부 노드는 핵심 문장 종속.
A2 edge validator의 전제(양끝 노드가 정본 PASS) — A0가 선행 검증.

범위: 판정 + synthetic selftest. operating store write 0. apply/ingest/merge 0.
CLI: python openbinggu_a0_node_dryrun.py --selftest
"""
import sys
import os
import re

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
    from binggu_p1_config import load_user_ontology, is_confirm_actor, auto_value_judgment_allowed
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


# ---------------- selftest ----------------

def _selftest():
    nodes = {
        "p_core": {"id": "p_core", "sentence": "이 프로젝트의 빌드 절차는 make build 로 통일한다.", "node_type": "doc"},
    }

    def n(nid, s, nt, **kw):
        d = {"id": nid, "sentence": s, "node_type": nt}
        d.update(kw)
        return d

    cases = [
        ("정상_doc", n("d1", "이 문서는 배포 절차를 정의한다.", "doc"), "candidate", "PASS", None),
        ("정상_evidence", n("e1", "테스트 로그에 통과 결과가 기록되어 있다.", "evidence", evidence_refs=["EV1"]), "candidate", "PASS", None),
        ("정상_concept", n("c1", "redaction 은 민감정보를 제거하는 절차이다.", "concept"), "candidate", "PASS", None),
        ("정상_state", n("s1", "현재 테스트 스위트는 전부 통과한 상태이다.", "state"), "candidate", "PASS", None),
        ("판단_근거있음_PASS", n("j1", "이 입찰은 마진이 낮아 보류하는 것이 낫다.", "judgment", evidence_refs=["EV-mg"]), "candidate", "PASS", None),
        ("판단_무근거_직감보존", n("j1b", "이 입찰은 마진이 낮아 보류하는 것이 낫다.", "judgment"), "candidate", "REVIEW", "node_3_hunch_needs_evidence"),
        ("단어노드", n("w1", "마진", "concept"), "candidate", "FAIL", "node_1_word"),
        ("키워드태그노드", n("w2", "보류태그", "concept"), "candidate", "FAIL", "node_1_word"),
        ("짧고_독립의미없음", n("w3", "낮음", "state"), "candidate", "FAIL", "node_1_word"),
        ("비종결_의미없음", n("w4", "그 입찰 관련 어떤", "judgment"), "candidate", "FAIL", "node_1_meaning"),
        ("무차별_잡정보", n("x1", "그리고 또한 이 부분도 관련이 있다고 한다.", "doc"), "candidate", "REVIEW", "node_1_noise"),
        ("5종외_타입", n("t1", "이 노드는 알 수 없는 타입을 가진다고 한다.", "unknown_type"), "candidate", "REVIEW", "node_1_type5"),
        ("세부노드_parent없음", n("det1", "이 세부 항목은 상위 문장에 종속된다.", "concept", parent_id="missing"), "candidate", "FAIL", "node_1_detail_parent"),
        ("증거노드_evidence없음", n("e2", "어떤 증거가 여기에 있다고 주장한다.", "evidence"), "candidate", "FAIL", "node_3_evidence"),
        ("판단노드_확정_evidence없음", n("j2", "이 결정은 위험하다고 확정한다.", "judgment"), "confirmed", "FAIL", "node_3_judgment_confirmed"),
        ("layer혼동", n("j3", "이 방향이 더 낫다고 판단한다.", "judgment", declared_layer="objective"), "candidate", "REVIEW", "node_5_layer"),
        ("정상_세부노드", n("det2", "이 세부 항목은 상위 빌드 문장에 종속된다.", "concept", parent_id="p_core"), "candidate", "PASS", None),
        # 철학필터 — 근거 공백 갈래
        ("무근거_노이즈_DISCARD", n("ns1", "이 메모는 의미 없는 잡음 조각이라고 적혀 있다.", "concept", noise=True), "candidate", "DISCARD", "node_3_no_evidence_noise"),
        ("owner직감_플래그_보존", n("oh1", "이 방향이 더 나을 것 같다는 직감이 든다고 적는다.", "concept", owner_hunch=True), "candidate", "REVIEW", "node_3_hunch_needs_evidence"),
        ("owner소스_짧은메모_보존", n("oh2", "다음에는 이 거래처를 우선 검토하자고 메모한다.", "state", source="owner"), "candidate", "REVIEW", "node_3_hunch_needs_evidence"),
        ("판단_확정_무근거_FAIL", n("jc1", "이 결정은 안전하다고 확정한다고 적는다.", "judgment"), "confirmed", "FAIL", "node_3_judgment_confirmed"),
        # 적대검증 회귀 — 짧은/비종결 owner 직감은 형식 게이트보다 먼저 보존(검열 차단)
        ("owner직감_짧은메모_보존", n("oh3", "위험", "judgment", owner_hunch=True), "candidate", "REVIEW", "node_3_hunch_needs_evidence"),
        ("owner소스_비종결_보존", n("oh4", "이 거래처는 좀 그런 느낌", "state", source="owner"), "candidate", "REVIEW", "node_3_hunch_needs_evidence"),
        # 단 noise 명시 owner 직감은 형식 면제 안 됨(명백 노이즈) — 정상 흐름 유지
        ("owner직감_noise_제외", n("oh5", "ㅁㄴㅇㄹ", "judgment", owner_hunch=True, noise=True), "candidate", "FAIL", "node_1_word"),
    ]

    print("=" * 76)
    print("OpenBinggu A0 — 노드 정본 validator (synthetic / selftest)")
    print("=" * 76)
    all_ok = True
    for name, node, st, exp_v, exp_g in cases:
        idx = dict(nodes)
        idx[node["id"]] = node
        r = classify_node(node, idx, status=st)
        ok = (r["verdict"] == exp_v) and ((exp_g is None) or (r["guard"] == exp_g))
        all_ok = all_ok and ok
        print("  [%s] %-26s verdict=%-7s guard=%-26s" % ("OK" if ok else "FAIL", name, r["verdict"], str(r["guard"])))

    # ---- 열린 분류(keep/challenge/discard) 추천 + 사람 도장 게이트 ----
    print("\n  -- 철학필터 열린 분류 추천(AI 추천만, 확정은 사람) --")

    def rck(name, cond):
        nonlocal all_ok
        all_ok = all_ok and cond
        print("  [%s] %s" % ("OK" if cond else "FAIL", name))

    keep_node = n("rk1", "이 문서는 배포 절차를 정의한다.", "doc")
    chal_node = n("rc1", "이 방향이 더 나을 것 같다는 직감이 든다고 적는다.", "concept", owner_hunch=True)
    disc_node = n("rd1", "이 메모는 의미 없는 잡음 조각이라고 적혀 있다.", "concept", noise=True)

    rk = recommend_open_classification(keep_node)
    rc = recommend_open_classification(chal_node)
    rd = recommend_open_classification(disc_node)
    rck("R1 PASS→keep 추천 + confirmed=False", rk["recommendation"] == "keep" and rk["confirmed"] is False)
    rck("R2 직감 REVIEW→challenge 추천 + confirmed=False", rc["recommendation"] == "challenge" and rc["confirmed"] is False)
    rck("R3 노이즈 DISCARD→discard 추천 + confirmed=False", rd["recommendation"] == "discard" and rd["confirmed"] is False)
    rck("R4 AI 자동 가치관 판정 항상 False(헌법)", rk["auto_value_judgment"] is False)

    # 사람 도장 게이트 — actor=human 만 확정, auto/agent/None 차단
    cf_auto = confirm_open_classification(rk, "auto", "keep")
    cf_agent = confirm_open_classification(rk, "agent", "keep")
    cf_none = confirm_open_classification(rk, None, "keep")
    cf_human = confirm_open_classification(rk, "human", "keep")
    rck("R5 actor=auto 확정 차단(confirmed False)", cf_auto["confirmed"] is False and cf_auto["reason"] == "G4_no_auto")
    rck("R6 actor=agent/None 확정 차단", cf_agent["confirmed"] is False and cf_none["confirmed"] is False)
    rck("R7 actor=human 확정 통과(confirmed True)", cf_human["confirmed"] is True and cf_human["choice"] == "keep")
    cf_badchoice = confirm_open_classification(rk, "human", "bogus")
    rck("R8 잘못된 choice 차단", cf_badchoice["confirmed"] is False and cf_badchoice["reason"] == "choice_invalid")

    print("\n  operating_store_unchanged: True (판정만, FS write 0)")
    gate = "GO" if all_ok else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        _selftest()
    else:
        print("usage: openbinggu_a0_node_dryrun.py [--selftest]")
        sys.exit(2)


if __name__ == "__main__":
    main()
