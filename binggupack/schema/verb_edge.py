# -*- coding: utf-8 -*-
"""OpenBinggu G2 — 동사형 엣지 6종 스키마 + 기각 도장(deprecated) 검증기 (dry-run only).

v1.11.0 strangler phase1: 핵심 로직을 scripts/openbinggu_verb_edge_schema.py 에서
이 모듈로 이관했다. scripts 파일은 backward-compatible thin wrapper 로 유지되며,
공개 심볼/동작/exit code 는 byte-identical 하다(기능 변경 0).

4cli R3 지시 2·3·4·8 + 글로벌 조사 후보 1(Wikidata deprecated rank) 반영:
  - 6종 동사형 relation의 **스키마 정본** (허용 source/target label_kind 매트릭스 + 방향).
  - 자동 생산 금지 원칙: 6종 강한 라벨은 이 스키마로 **검증만** 한다 (생산은 명시 단서/승인 주입 —
    watcher_edge_proposal_g2 는 약한 후보 2종(nearby/stance)만 자동 생산).
  - deprecated(기각 도장): candidate/confirmed 에 더해 "틀렸음을 보존-제외"하는 제3 상태.
    요건 = deprecated_reason 필수 + 반박 evidence 포인터 권장. 기본 소비(view)에서 제외.
  - node→node 는 proposal 한정 — 본 그래프 진입은 C-2 승인 후만 (이 모듈은 판정만, write 0).
"""
import sys

# ---------------- 6종 동사형 relation 정본 ----------------
# (한글 동사, 허용 source label_kind 집합, 허용 target label_kind 집합)
# 근거: a3_complex fixture 실사용 방향 + 보수 원칙(판단 중심 수렴).

VERB_EDGES = {
    "supports_judgment": {"verb": "근거가_된다", "src": {"증거", "상태", "개념"}, "tgt": {"판단"}},
    "contradicts":       {"verb": "반박한다",   "src": {"판단", "증거"},          "tgt": {"판단"}},
    "depends_on":        {"verb": "선행조건이다", "src": {"판단"},                "tgt": {"상태", "개념", "판단"}},
    "blocks":            {"verb": "막는다",     "src": {"상태", "판단"},          "tgt": {"판단"}},
    "enables":           {"verb": "가능하게_한다", "src": {"상태", "판단"},        "tgt": {"판단"}},
    "refines":           {"verb": "정밀화한다",  "src": {"개념", "판단"},          "tgt": {"판단", "개념"}},
}

# proposal 전용 약한 라벨 (자동 생산 허용 — 본 그래프 엣지 아님)
WEAK_LABELS = {"nearby_candidate", "stance_candidate"}

# 상태 enum — 기각 도장(deprecated) 포함 (Wikidata rank 차용)
VALID_STATUS = {"candidate", "confirmed", "deprecated"}


def validate_verb_edge(edge, nodes_by_id):
    """6종 동사형 엣지 1건 검증. 반환 {"verdict": PASS|FAIL, "reason": ...}. write 0.
    요건: relation 6종 내 / source·target 노드 실재 / label_kind 매트릭스 적합 /
          evidence_refs 비어있지 않음(헌법) / promotion_allowed=false / candidate=true."""
    def fail(reason):
        return {"verdict": "FAIL", "reason": reason, "edge_id": edge.get("id")}

    rel = (edge.get("properties") or {}).get("relation")
    if rel not in VERB_EDGES:
        return fail("relation 6종 외: %s" % rel)
    spec = VERB_EDGES[rel]
    src = nodes_by_id.get(edge.get("source"))
    tgt = nodes_by_id.get(edge.get("target"))
    if src is None or tgt is None:
        return fail("source/target 노드 미존재 (dangling)")
    sk = (src.get("properties") or {}).get("label_kind")
    tk = (tgt.get("properties") or {}).get("label_kind")
    if sk not in spec["src"]:
        return fail("source label_kind 매트릭스 위반: %s -%s->" % (sk, rel))
    if tk not in spec["tgt"]:
        return fail("target label_kind 매트릭스 위반: -%s-> %s" % (rel, tk))
    if edge.get("source") == edge.get("target"):
        return fail("self-loop")
    if not edge.get("evidence_refs"):
        return fail("evidence_refs 비어있음 (헌법: 근거 없는 엣지 확정 금지)")
    if edge.get("promotion_allowed") is not False:
        return fail("promotion_allowed != false")
    if (edge.get("properties") or {}).get("candidate") is not True:
        return fail("candidate != true")
    return {"verdict": "PASS", "reason": "6종 매트릭스 적합", "edge_id": edge.get("id")}


def validate_deprecated(item):
    """기각 도장 검증. deprecated 상태면 deprecated_reason 필수.
    반환 {"verdict": PASS|FAIL, "reason": ...}."""
    status = item.get("status", "candidate")
    if status not in VALID_STATUS:
        return {"verdict": "FAIL", "reason": "status enum 외: %s" % status}
    if status == "deprecated":
        p = item.get("properties") or {}
        if not (p.get("deprecated_reason") or "").strip():
            return {"verdict": "FAIL", "reason": "deprecated인데 deprecated_reason 없음"}
    return {"verdict": "PASS", "reason": "status 적합"}


def default_view_filter(items):
    """기본 소비 view 필터 — deprecated 는 제외(보존하되 안 보임). 순수 함수, write 0."""
    return [it for it in items if it.get("status", "candidate") != "deprecated"]


# ---------------- selftest ----------------

def _n(nid, kind):
    return {"id": nid, "properties": {"label_kind": kind, "candidate": True}}


def _e(eid, src, tgt, rel, refs=("EVC-1",), promo=False, cand=True):
    return {"id": eid, "source": src, "target": tgt,
            "properties": {"relation": rel, "candidate": cand},
            "evidence_refs": list(refs), "promotion_allowed": promo}


def _selftest():
    nodes = {n["id"]: n for n in [
        _n("n_ev", "증거"), _n("n_st", "상태"), _n("n_co", "개념"),
        _n("n_j1", "판단"), _n("n_j2", "판단"), _n("n_doc", "문서"),
    ]}
    cases = [
        # (이름, edge, 기대 verdict)
        ("supports_증거→판단", _e("e1", "n_ev", "n_j1", "supports_judgment"), "PASS"),
        ("supports_상태→판단", _e("e2", "n_st", "n_j1", "supports_judgment"), "PASS"),
        ("contradicts_판단→판단", _e("e3", "n_j2", "n_j1", "contradicts"), "PASS"),
        ("blocks_상태→판단", _e("e4", "n_st", "n_j1", "blocks"), "PASS"),
        ("enables_판단→판단", _e("e5", "n_j2", "n_j1", "enables"), "PASS"),
        ("refines_개념→판단", _e("e6", "n_co", "n_j1", "refines"), "PASS"),
        ("depends_판단→상태", _e("e7", "n_j1", "n_st", "depends_on"), "PASS"),
        # FAIL 군
        ("매트릭스_문서→판단_supports", _e("f1", "n_doc", "n_j1", "supports_judgment"), "FAIL"),
        ("매트릭스_supports_역방향", _e("f2", "n_j1", "n_ev", "supports_judgment"), "FAIL"),
        ("6종외_relation", _e("f3", "n_ev", "n_j1", "related_to"), "FAIL"),
        ("dangling_target", _e("f4", "n_ev", "n_missing", "supports_judgment"), "FAIL"),
        ("self_loop", _e("f5", "n_j1", "n_j1", "contradicts"), "FAIL"),
        ("evidence_없음", _e("f6", "n_ev", "n_j1", "supports_judgment", refs=()), "FAIL"),
        ("promotion_true", _e("f7", "n_ev", "n_j1", "supports_judgment", promo=True), "FAIL"),
        ("candidate_false", _e("f8", "n_ev", "n_j1", "supports_judgment", cand=False), "FAIL"),
    ]
    print("=" * 74)
    print("OpenBinggu G2 — 동사형 엣지 6종 스키마 + deprecated selftest (dry-run)")
    print("=" * 74)
    all_ok = True
    for name, edge, exp in cases:
        r = validate_verb_edge(edge, nodes)
        ok = r["verdict"] == exp
        all_ok = all_ok and ok
        print("  [%s] %-28s verdict=%-4s %s" % ("OK" if ok else "FAIL", name, r["verdict"],
                                                "" if ok else r["reason"]))

    # deprecated(기각 도장) 케이스
    dep_cases = [
        ("candidate_기본", {"status": "candidate", "properties": {}}, "PASS"),
        ("deprecated_사유있음", {"status": "deprecated",
                              "properties": {"deprecated_reason": "낙찰가 공개로 반증"}}, "PASS"),
        ("deprecated_사유없음", {"status": "deprecated", "properties": {}}, "FAIL"),
        ("enum외_status", {"status": "rejected", "properties": {}}, "FAIL"),
    ]
    for name, item, exp in dep_cases:
        r = validate_deprecated(item)
        ok = r["verdict"] == exp
        all_ok = all_ok and ok
        print("  [%s] %-28s verdict=%-4s %s" % ("OK" if ok else "FAIL", name, r["verdict"],
                                                "" if ok else r["reason"]))

    # 기본 view 에서 deprecated 제외(보존-제외) + candidate/confirmed 잔존
    items = [{"id": "a", "status": "candidate"}, {"id": "b", "status": "deprecated"},
             {"id": "c", "status": "confirmed"}]
    view = default_view_filter(items)
    vf_ok = [it["id"] for it in view] == ["a", "c"] and len(items) == 3  # 원본 불변(보존)
    all_ok = all_ok and vf_ok
    print("  [%s] %-28s (deprecated 제외·원본 보존)" % ("OK" if vf_ok else "FAIL", "default_view_filter"))

    # 약한 라벨은 6종 검증기에서 FAIL (proposal 전용 — 본 그래프 진입 금지 증명)
    weak = validate_verb_edge(_e("w1", "n_ev", "n_j1", "nearby_candidate"), nodes)
    wk_ok = weak["verdict"] == "FAIL"
    all_ok = all_ok and wk_ok
    print("  [%s] %-28s (약한 라벨 본그래프 거부)" % ("OK" if wk_ok else "FAIL", "weak_label_rejected"))

    print("\n  operating_store_unchanged: True (판정만, FS write 0)")
    gate = "GO" if all_ok else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)
