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
    # objective/subjective 혼동(declared_layer ≠ 타입 유도 layer)
    dl = node.get("declared_layer")
    if dl is not None and dl != LAYER.get(nt):
        return out("REVIEW", "objective/subjective 타입 혼동", "node_5_layer")

    return out("PASS", "핵심 문장 노드(정본 충족)", None)


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
        ("정상_judgment", n("j1", "이 입찰은 마진이 낮아 보류하는 것이 낫다.", "judgment"), "candidate", "PASS", None),
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
