# -*- coding: utf-8 -*-
"""binggu_graph_preview.py — 3층 graph화 preview (read-only · candidate/unverified).

정본 파이프라인: 1층 node → 2층 rationale/edge → **3층 graph화** → graph validation → 사람 승인 → pack/export.
본 모듈 = 3층(graph 조립) + graph validation. pack 파일/edges.jsonl 미기록 · 저장 0 · report/preview only.

불변 (전부 selftest 증명):
  - 신규 predicate 0 — supports_judgment 만(validate_verb_edge 위임).
  - evidence 없는 edge 제외 · invalid relation 제외 · self-loop/missing endpoint 제외 · 중복 dedup · cycle 감지.
  - semantic_subtype 을 canonical(label_kind)로 쓰면 warning(승격 차단 신호).
  - hallucinated node/concept 0(입력 node 만) · graph 전체 candidate/unverified.
  - save_selected / pack 승격은 본 모듈 범위 밖(graph validation 이후 별도 단계).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from binggu_rationale_suggest import suggest_rationale, SUPPORTS  # 2층 재사용(id 포함 edge)
from openbinggu_verb_edge_schema import validate_verb_edge, VERB_EDGES  # 매트릭스 검증 위임

CANONICAL = {"문서", "증거", "개념", "상태", "판단"}
SUBTYPES = {"교훈", "결정", "선호", "설계결정", "버그패턴", "사실"}
GRAPH_CAVEAT = "candidate · unverified · 사람 승인 전 저장 0 · pack 미기록"


def _node_text(n):
    return n.get("properties", {}).get("sentence") or n.get("label", "")


def _detect_cycle(edge_pairs):
    """방향 그래프 cycle 감지(DFS). edge_pairs = [(src_id, tgt_id)]. 반환 cycle 경로 or None."""
    adj = {}
    for s, t in edge_pairs:
        adj.setdefault(s, []).append(t)
    WHITE, GRAY, BLACK = 0, 1, 2
    color, stack = {}, []

    def visit(u):
        color[u] = GRAY
        stack.append(u)
        for v in adj.get(u, []):
            if color.get(v, WHITE) == GRAY:
                return stack[stack.index(v):] + [v]
            if color.get(v, WHITE) == WHITE:
                r = visit(v)
                if r:
                    return r
        stack.pop()
        color[u] = BLACK
        return None

    for node in list(adj.keys()):
        if color.get(node, WHITE) == WHITE:
            r = visit(node)
            if r:
                return r
    return None


def build_graph_preview(nodes, evidence_items=None, edge_candidates=None):
    """3층 graph preview 조립 + validation. read-only · write 0.
    nodes = 1층/builder node 후보 [{id, properties{label_kind, sentence}, evidence_refs}].
    edge_candidates 미지정 시 nodes 에서 2층(suggest_rationale)으로 생성(id 포함)."""
    node_ids = {n.get("id") for n in nodes}
    nodes_by_id = {n.get("id"): {"id": n.get("id"),
                                 "properties": {"label_kind": n.get("properties", {}).get("label_kind"),
                                                "candidate": True}} for n in nodes}

    if edge_candidates is None:
        cands = [{"id": n.get("id"), "text": _node_text(n),
                  "label_kind": n.get("properties", {}).get("label_kind"),
                  "semantic_subtype": None,                     # subtype 미승격
                  "evidence_refs": n.get("evidence_refs") or []} for n in nodes]
        edge_candidates = suggest_rationale(cands)["suggested_edges"]

    # graph nodes / evidence
    g_nodes = [{"id": n.get("id"), "text": _node_text(n)[:60],
                "label_kind": n.get("properties", {}).get("label_kind"),
                "evidence_refs": n.get("evidence_refs") or [],
                "status": "candidate", "caveat": GRAPH_CAVEAT} for n in nodes]
    ev_ids = set()
    for n in nodes:
        ev_ids |= set(n.get("evidence_refs") or [])
    ev_text = {e.get("id") or e.get("item_id"): e.get("text", "") for e in (evidence_items or [])
               if isinstance(e, dict)}
    g_evidence = [{"id": e, "text": ev_text.get(e, "")[:60], "status": "candidate"} for e in sorted(ev_ids)]

    # ---- validation ----
    validation, valid_edges, seen = [], [], set()

    def vlog(check, status, detail):
        validation.append({"check": check, "status": status, "detail": detail})

    for e in edge_candidates:
        sid = e.get("source_id")
        tid = e.get("target_id")
        rel = e.get("relation")
        tag = "%s -%s-> %s" % (sid, rel, tid)
        if rel not in VERB_EDGES:                       # 신규/미지 predicate
            vlog("invalid_relation", "fail", tag + " (6종 외 — 제외)")
            continue
        if rel != SUPPORTS:                             # 본 파이프라인은 supports_judgment만
            vlog("relation_not_allowed", "fail", tag + " (supports_judgment만 허용 — 제외)")
            continue
        if sid not in node_ids or tid not in node_ids:  # dangling
            vlog("missing_endpoint", "fail", tag + " (source/target node 없음 — 제외)")
            continue
        if sid == tid:
            vlog("self_loop", "fail", tag + " (self-loop — 제외)")
            continue
        if not e.get("evidence_refs"):
            vlog("missing_evidence", "fail", tag + " (evidence_refs 없음 — 제외)")
            continue
        edge_obj = {"id": tag, "source": sid, "target": tid,
                    "properties": {"relation": rel, "candidate": True},
                    "evidence_refs": list(e["evidence_refs"]), "promotion_allowed": False}
        v = validate_verb_edge(edge_obj, nodes_by_id)   # 매트릭스/evidence/candidate 위임
        if v["verdict"] != "PASS":
            vlog("matrix_violation", "fail", tag + " (%s — 제외)" % v["reason"])
            continue
        key = (sid, tid, rel)
        if key in seen:
            vlog("duplicate_edge", "warn", tag + " (중복 — dedup)")
            continue
        seen.add(key)
        valid_edges.append({"source_id": sid, "target_id": tid, "relation": rel,
                            "verb": "근거가_된다", "evidence_refs": list(e["evidence_refs"]),
                            "status": "candidate", "promotion_allowed": False, "caveat": GRAPH_CAVEAT})

    # cycle 감지(유효 edge 대상)
    cyc = _detect_cycle([(e["source_id"], e["target_id"]) for e in valid_edges])
    vlog("cycle", "warn" if cyc else "pass", (" -> ".join(cyc)) if cyc else "no cycle")

    # semantic_subtype 이 canonical label_kind 자리에 쓰였는지(승격 오용) 감지
    for n in nodes:
        lk = n.get("properties", {}).get("label_kind")
        if lk in SUBTYPES and lk not in CANONICAL:
            vlog("subtype_as_canonical", "warn", "node %s label_kind=%s (subtype 승격 금지)" % (n.get("id"), lk))

    fails = [v for v in validation if v["status"] == "fail"]
    warns = [v for v in validation if v["status"] == "warn"]
    summary = {"nodes": len(g_nodes), "evidence": len(g_evidence),
               "edges_valid": len(valid_edges),
               "edges_excluded": len(fails), "warnings": len(warns)}
    return {"nodes": g_nodes, "evidence": g_evidence, "edges": valid_edges,
            "validation": validation, "summary": summary,
            "note": "3층 graph preview — candidate/unverified · pack/edges.jsonl 미기록 · 사람 승인 전 저장 0 · save_selected 승격은 별도 단계"}


# ---------------- selftest (순수 함수 · write 0) ----------------
def _selftest():
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    def N(nid, kind, sent, refs):
        return {"id": nid, "properties": {"label_kind": kind, "sentence": sent}, "evidence_refs": list(refs)}

    # 1) 정상 graph: 증거(evidence)→판단 supports_judgment 1건
    g = build_graph_preview([
        N("node:ev", "증거", "로그에 오타가 3번 찍혔다", ["EVC-1"]),
        N("node:j", "판단", "보내기 전 확인하자", ["EVC-2"]),
    ])
    ck(g["summary"]["nodes"] == 2 and g["summary"]["edges_valid"] == 1, "정상 graph: node 2 · valid edge 1")
    ck(g["edges"][0]["relation"] == SUPPORTS and g["edges"][0]["source_id"] == "node:ev", "edge id 기반 supports_judgment")
    ck(g["summary"]["evidence"] == 2, "evidence 2건 조립(EVC-1·EVC-2)")
    ck(all(n["status"] == "candidate" for n in g["nodes"]) and "candidate" in g["note"], "graph 전체 candidate/unverified")

    # 2) 신규/미허용 relation 제외
    g2 = build_graph_preview(
        [N("a", "증거", "t1", ["E1"]), N("b", "판단", "t2", [])],
        edge_candidates=[{"source_id": "a", "target_id": "b", "relation": "related_to", "evidence_refs": ["E1"]},
                         {"source_id": "a", "target_id": "b", "relation": "depends_on", "evidence_refs": ["E1"]}])
    ck(g2["summary"]["edges_valid"] == 0 and any(v["check"] in ("invalid_relation", "relation_not_allowed")
                                                 for v in g2["validation"]),
       "신규/미허용 relation 제외(supports_judgment만)")

    # 3) evidence 없는 edge 제외
    g3 = build_graph_preview(
        [N("a", "증거", "t1", []), N("b", "판단", "t2", [])],
        edge_candidates=[{"source_id": "a", "target_id": "b", "relation": SUPPORTS, "evidence_refs": []}])
    ck(g3["summary"]["edges_valid"] == 0 and any(v["check"] == "missing_evidence" for v in g3["validation"]),
       "evidence 없는 edge 제외")

    # 4) self-loop 제외
    g4 = build_graph_preview(
        [N("a", "증거", "t1", ["E1"])],
        edge_candidates=[{"source_id": "a", "target_id": "a", "relation": SUPPORTS, "evidence_refs": ["E1"]}])
    ck(g4["summary"]["edges_valid"] == 0 and any(v["check"] == "self_loop" for v in g4["validation"]),
       "self-loop 제외")

    # 5) missing endpoint 제외
    g5 = build_graph_preview(
        [N("a", "증거", "t1", ["E1"])],
        edge_candidates=[{"source_id": "a", "target_id": "ghost", "relation": SUPPORTS, "evidence_refs": ["E1"]}])
    ck(g5["summary"]["edges_valid"] == 0 and any(v["check"] == "missing_endpoint" for v in g5["validation"]),
       "missing endpoint(dangling) 제외")

    # 6) 매트릭스 위반 제외(문서→판단 supports)
    g6 = build_graph_preview(
        [N("a", "문서", "t1", ["E1"]), N("b", "판단", "t2", [])],
        edge_candidates=[{"source_id": "a", "target_id": "b", "relation": SUPPORTS, "evidence_refs": ["E1"]}])
    ck(g6["summary"]["edges_valid"] == 0 and any(v["check"] == "matrix_violation" for v in g6["validation"]),
       "매트릭스 위반(문서→판단) 제외")

    # 7) 중복 edge dedup
    g7 = build_graph_preview(
        [N("a", "증거", "t1", ["E1"]), N("b", "판단", "t2", [])],
        edge_candidates=[{"source_id": "a", "target_id": "b", "relation": SUPPORTS, "evidence_refs": ["E1"]},
                         {"source_id": "a", "target_id": "b", "relation": SUPPORTS, "evidence_refs": ["E1"]}])
    ck(g7["summary"]["edges_valid"] == 1 and any(v["check"] == "duplicate_edge" for v in g7["validation"]),
       "중복 edge dedup(valid 1 + warn)")

    # 8) cycle 감지(warn) — 매트릭스 우회 합성 edge로 직접 주입
    build_graph_preview(
        [N("x", "판단", "t1", ["E1"]), N("y", "판단", "t2", ["E2"])],
        edge_candidates=[{"source_id": "x", "target_id": "y", "relation": SUPPORTS, "evidence_refs": ["E1"]},
                         {"source_id": "y", "target_id": "x", "relation": SUPPORTS, "evidence_refs": ["E2"]}])
    # 판단→판단 supports 는 매트릭스 위반이라 valid 0 → cycle 없음(정상). cycle 함수 자체 단위 검증:
    ck(_detect_cycle([("a", "b"), ("b", "c"), ("c", "a")]) is not None, "cycle 감지 함수(a→b→c→a)")
    ck(_detect_cycle([("a", "b"), ("b", "c")]) is None, "비순환 그래프 cycle 없음")

    # 9) semantic_subtype 을 canonical 자리에 쓰면 warning
    g9 = build_graph_preview([N("a", "교훈", "subtype을 label_kind에 잘못 넣음", ["E1"])])
    ck(any(v["check"] == "subtype_as_canonical" and v["status"] == "warn" for v in g9["validation"]),
       "semantic_subtype 이 canonical 자리 → warning(승격 차단 신호)")

    # 10) hallucination 0 — graph node id 는 입력 id 에서만
    in_ids = {"node:ev", "node:j"}
    ck(all(n["id"] in in_ids for n in g["nodes"]), "graph node = 입력 node id 만(새 노드 0)")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
