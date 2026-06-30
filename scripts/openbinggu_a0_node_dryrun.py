#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu A0 — 노드 정본 validator (backward-compatible thin wrapper).

v1.16 strangler Phase2: 정본 판정 로직은 binggupack.safety.a0_node 로 이관됐고, 이 파일은
공개 심볼(LABEL_KINDS/LAYER/classify_node/recommend_open_classification/
confirm_open_classification 등)이 byte-identical 한 thin wrapper 다. 기존 호출처
(import openbinggu_a0_node_dryrun as m / from openbinggu_a0_node_dryrun import LABEL_KINDS 등
bare-name import; label_kind_map wrapper 의 importlib 동적 LABEL_KINDS 참조 포함)는 그대로 동작.

synthetic selftest(operating store write 0)는 이 wrapper 에 잔류한다.
binggu_p1_config(scripts/ 정본)는 진입점 sys.path 의 scripts/ 로 해소된다.

CLI: python scripts/openbinggu_a0_node_dryrun.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로
if HERE not in sys.path:
    sys.path.insert(0, HERE)   # binggu_p1_config(scripts/ 정본) 해소 경로

from binggupack.safety.a0_node import *  # noqa: E402,F401,F403
from binggupack.safety.a0_node import (  # noqa: E402,F401  (전체 명시 re-export)
    LABEL_KINDS,
    LAYER,
    OPEN_VERDICTS,
    _is_word,
    _has_independent_meaning,
    classify_node,
    recommend_open_classification,
    confirm_open_classification,
    preserves_unsupported_notes,
    load_user_ontology,
    is_confirm_actor,
    auto_value_judgment_allowed,
)


# ---------------- selftest (synthetic · operating store write 0 · wrapper 잔류) ----------------

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
