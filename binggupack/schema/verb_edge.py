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

# ---------------- 화자축 relation 정본 (SPEAKER_EDGES · 2026-07-27 신설) ----------------
# 실측 ledger 53건이 쓰고 있던 relation 6종의 정식 등록. **소급 유효 등록만**이고
# 기존 행 UPDATE 0 · 저장 경로 배선 0 (판정 함수 제공까지가 이번 스코프).
#
# ★ label_kind 매트릭스를 걸지 않는다 — 운영 ledger 실측 53건의 node_type 쌍이
#   judgment→judgment 20 / judgment→state 4 / evidence→judgment 4 / judgment→evidence 4 /
#   state→state 3 / judgment→doc 3 / state→evidence 3 … 로 5종×5종 전면 분산(15개 쌍)이다.
#   화자축은 "무엇에 대한 관계"가 아니라 "누가 누구에게 반응했나"라서 의미 매트릭스가
#   원리적으로 맞지 않는다. 검증 축은 speaker 다(src≠tgt · 둘 다 {ai,owner} · self-loop 금지).
#
# 방향 규약(정본: openbinggu_conversation_candidate_save.py:231-236 주석):
#   relation prefix = source(반응 주체), 대상이 target. 먼저 말한 사람 → 반응한 사람이 아니라
#   반응 주체가 source 다. 실측 정합: ai_* 25건 전부 (ai→owner) · owner_* 28건 전부 (owner→ai).
SPEAKERS = {"ai", "owner"}

SPEAKER_EDGES = {
    "ai_accepts":    {"verb": "수용한다", "src_speaker": "ai",    "tgt_speaker": "owner"},
    "ai_refutes":    {"verb": "반박한다", "src_speaker": "ai",    "tgt_speaker": "owner"},
    "ai_revises":    {"verb": "수정한다", "src_speaker": "ai",    "tgt_speaker": "owner"},
    "owner_accepts": {"verb": "수용한다", "src_speaker": "owner", "tgt_speaker": "ai"},
    "owner_refutes": {"verb": "반박한다", "src_speaker": "owner", "tgt_speaker": "ai"},
    "owner_revises": {"verb": "수정한다", "src_speaker": "owner", "tgt_speaker": "ai"},
}

# ---------------- 증빙축 relation 정본 (GROUNDING_EDGES · 2026-07-27 신설) ----------------
# evidence_supports 는 source 가 evidence 테이블 id(`EVC-*`)라 nodes 에 부재하다.
# 실측: 500건 전부 source ∈ evidence(500/500) · target ∈ nodes(500/500) · source ∈ nodes 는 0건.
# → VERB_EDGES 매트릭스(nodes↔nodes 전제)에 먹이면 dangling FAIL 이 확정이므로 **별도 축**.
EVIDENCE_ID_PREFIXES = ("EVC-",)

GROUNDING_EDGES = {
    "evidence_supports": {"verb": "증빙한다", "src_space": "evidence", "tgt_space": "node"},
}


def looks_like_evidence_id(item_id):
    """evidence 테이블 조회가 불가할 때의 폴백 판정 — id prefix 규칙(`EVC-`). 순수 함수."""
    return isinstance(item_id, str) and item_id.upper().startswith(EVIDENCE_ID_PREFIXES)


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

    # ---- VERB_EDGES 원형 불변 잠금 ----
    # `rel not in VERB_EDGES` 를 '강한 의미엣지인가' 판정으로 쓰는 소비자가 있다
    # (binggu_cloud_pack_export.py:127 · binggu_graph_confirm.py:74 · binggu_graph_preview.py:100).
    # 화자축/증빙축을 여기에 합치면 그 판정이 조용히 샌다 → 6종 고정을 테스트로 못박는다.
    frozen6 = {"supports_judgment", "contradicts", "depends_on", "blocks", "enables", "refines"}
    v6_ok = set(VERB_EDGES) == frozen6
    all_ok = all_ok and v6_ok
    print("  [%s] %-28s (6종 고정 — 화자/증빙축 혼입 금지)" % ("OK" if v6_ok else "FAIL", "VERB_EDGES_frozen6"))

    # ---- SPEAKER_EDGES 레지스트리 형태 ----
    spk_ok = (
        set(SPEAKER_EDGES) == {"ai_accepts", "ai_refutes", "ai_revises",
                               "owner_accepts", "owner_refutes", "owner_revises"}
        and all(s["src_speaker"] in SPEAKERS and s["tgt_speaker"] in SPEAKERS
                and s["src_speaker"] != s["tgt_speaker"] for s in SPEAKER_EDGES.values())
        # 방향 규약: relation prefix == source 화자
        and all(rel.split("_", 1)[0] == s["src_speaker"] for rel, s in SPEAKER_EDGES.items())
        # 화자축은 의미 매트릭스를 갖지 않는다(src/tgt label_kind 키 부재)
        and all("src" not in s and "tgt" not in s for s in SPEAKER_EDGES.values())
        # VERB_EDGES 와 교집합 0
        and not (set(SPEAKER_EDGES) & set(VERB_EDGES))
    )
    all_ok = all_ok and spk_ok
    print("  [%s] %-28s (6종·화자축·매트릭스 미적용)" % ("OK" if spk_ok else "FAIL", "SPEAKER_EDGES"))

    # ---- GROUNDING_EDGES 레지스트리 형태 ----
    gnd_ok = (
        set(GROUNDING_EDGES) == {"evidence_supports"}
        and GROUNDING_EDGES["evidence_supports"]["src_space"] == "evidence"
        and GROUNDING_EDGES["evidence_supports"]["tgt_space"] == "node"
        and not (set(GROUNDING_EDGES) & set(VERB_EDGES))
        and not (set(GROUNDING_EDGES) & set(SPEAKER_EDGES))
        and looks_like_evidence_id("EVC-CONV-f112cf41")
        and not looks_like_evidence_id("node:CONV:f112cf41")
        and not looks_like_evidence_id(None)
    )
    all_ok = all_ok and gnd_ok
    print("  [%s] %-28s (evidence→node · id prefix 폴백)" % ("OK" if gnd_ok else "FAIL", "GROUNDING_EDGES"))

    print("\n  operating_store_unchanged: True (판정만, FS write 0)")
    gate = "GO" if all_ok else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)
