# -*- coding: utf-8 -*-
"""binggu_graph_confirm.py — 5층 사람 승인 confirm 흐름 (read-only · dry-run · 저장 0).

정본 파이프라인: 1층 node → 2층 edge → 3층 graph → 4층 validation → **5층 사람 승인** → 6층 pack/export.
본 모듈 = 5층. graph_preview(3·4층 결과)를 사람이 approve/reject/defer. report only · pack/export 미실행.

불변 (전부 selftest 증명):
  - **자동 approve 0** — 기본 전부 deferred(pending). 사람이 명시 선택한 idx 만 approved/rejected.
  - approve 시 재검증(validate_verb_edge 위임) — supports_judgment 외·evidence 없음·매트릭스 위반 = approve 차단(invalid_disabled).
  - approve+reject 충돌 = reject 우선(보수).
  - approve 결과도 candidate/unverified — pack/edges.jsonl/DB write 0 · export 미실행.
  - 신규 predicate 0 · semantic_subtype 기반 approve 0(edge 는 label_kind 기반).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from binggu_rationale_suggest import SUPPORTS                        # supports_judgment
from openbinggu_verb_edge_schema import validate_verb_edge          # 매트릭스 재검증 위임

CONFIRM_CAVEAT = "candidate · unverified · 사람 승인해도 pack 미기록 · export 별도 단계"


def build_graph_confirm(graph_preview, approve=None, reject=None, defer=None):
    """graph_preview(3·4층) → 5층 사람 승인 결과. read-only · write 0.
    approve/reject/defer = 사람이 명시 선택한 edge idx(1-based) 집합. 미지정 = 전부 deferred."""
    edges = graph_preview.get("edges", [])            # 4층 validation 통과한 valid edge 만
    nodes_by_id = {n["id"]: {"id": n["id"],
                             "properties": {"label_kind": n.get("label_kind"), "candidate": True}}
                   for n in graph_preview.get("nodes", [])}
    approve = set(approve or [])
    reject = set(reject or [])
    defer = set(defer or [])

    approved, rejected, deferred, invalid = [], [], [], []
    for i, e in enumerate(edges, 1):
        base = {"idx": i, "source_id": e.get("source_id"), "target_id": e.get("target_id"),
                "relation": e.get("relation"), "verb": e.get("verb"),
                "evidence_refs": e.get("evidence_refs"), "validation_status": "pass",
                "caveat": CONFIRM_CAVEAT}
        if i in reject:                               # 충돌 시 reject 우선
            rejected.append({**base, "decision": "rejected"})
            continue
        if i in approve:
            # 안전 재검증: supports_judgment · evidence 필수 · 매트릭스
            edge_obj = {"id": "c%d" % i, "source": e.get("source_id"), "target": e.get("target_id"),
                        "properties": {"relation": e.get("relation"), "candidate": True},
                        "evidence_refs": e.get("evidence_refs") or [], "promotion_allowed": False}
            v = validate_verb_edge(edge_obj, nodes_by_id)
            if e.get("relation") != SUPPORTS or not e.get("evidence_refs") or v["verdict"] != "PASS":
                invalid.append({**base, "decision": "approve_blocked",
                                "reason": v.get("reason", "재검증 실패"), "approvable": False})
            else:
                approved.append({**base, "decision": "approved",
                                 "note": "사람 명시 승인 · 여전히 candidate · pack 미기록"})
            continue
        deferred.append({**base, "decision": "deferred"})   # 기본 pending(자동 approve 0)

    # 4층 validation fail 항목 = approve 불가(참고 표시) + approve 차단된 것
    invalid_disabled = [{"check": v["check"], "detail": v["detail"], "approvable": False}
                        for v in graph_preview.get("validation", []) if v["status"] == "fail"] + invalid

    summary = {"total_edges": len(edges), "approved": len(approved), "rejected": len(rejected),
               "deferred": len(deferred), "invalid_disabled": len(invalid_disabled),
               "auto_approved": 0}
    return {"approved": approved, "rejected": rejected, "deferred": deferred,
            "invalid_disabled": invalid_disabled, "summary": summary,
            "note": "graph confirm — report only · 자동 approve 0 · 승인도 candidate · "
                    "pack/export 미실행 · DB/pack write 0 · 사람 명시 선택만 반영"}


# ---------------- selftest (순수 함수 · write 0) ----------------
def _selftest():
    import binggu_graph_preview as gp
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    def N(i, k, s, r):
        return {"id": i, "properties": {"label_kind": k, "sentence": s}, "evidence_refs": list(r)}

    # 정상 graph_preview: 증거2→판단 supports 2건
    g = gp.build_graph_preview([
        N("node:e1", "증거", "로그에 오타 3회", ["EVC-1"]),
        N("node:e2", "상태", "빌드가 깨져 있다", ["EVC-2"]),
        N("node:j", "판단", "배포 전 확인하자", ["EVC-3"]),
    ])
    nedges = g["summary"]["edges_valid"]
    ck(nedges == 2, "graph_preview valid edge 2건(증거→판단·상태→판단)")

    # 1) 기본(인자 없음) → 전부 deferred · 자동 approve 0
    c0 = build_graph_confirm(g)
    ck(c0["summary"]["deferred"] == nedges and c0["summary"]["approved"] == 0
       and c0["summary"]["auto_approved"] == 0, "기본 → 전부 deferred · 자동 approve 0")

    # 2) approve [1] → edge 1 approved, 나머지 deferred
    c1 = build_graph_confirm(g, approve=[1])
    ck(c1["summary"]["approved"] == 1 and c1["approved"][0]["idx"] == 1
       and c1["summary"]["deferred"] == nedges - 1, "approve [1] → edge 1만 approved")
    ck("candidate" in c1["approved"][0]["caveat"] and "pack 미기록" in c1["approved"][0]["note"],
       "approved 도 candidate · pack 미기록 caveat")

    # 3) reject [2] → edge 2 rejected
    c2 = build_graph_confirm(g, reject=[2])
    ck(c2["summary"]["rejected"] == 1 and c2["rejected"][0]["idx"] == 2, "reject [2] → edge 2 rejected")

    # 4) defer 명시 → deferred
    c3 = build_graph_confirm(g, defer=[1])
    ck(c3["summary"]["deferred"] == nedges and c3["summary"]["approved"] == 0, "defer 명시 → deferred")

    # 5) approve+reject 충돌 → reject 우선
    c4 = build_graph_confirm(g, approve=[1], reject=[1])
    ck(c4["summary"]["rejected"] == 1 and c4["summary"]["approved"] == 0, "approve+reject 충돌 → reject 우선")

    # 6) 매트릭스 위반 edge 를 graph_preview.edges 에 합성 주입 → approve 차단
    g_bad = dict(g)
    g_bad["edges"] = [{"source_id": "node:j", "target_id": "node:e1", "relation": SUPPORTS,  # 판단→증거 위반
                       "verb": "근거가_된다", "evidence_refs": ["EVC-1"]}]
    g_bad["nodes"] = g["nodes"]
    cb = build_graph_confirm(g_bad, approve=[1])
    ck(cb["summary"]["approved"] == 0 and cb["summary"]["invalid_disabled"] >= 1,
       "매트릭스 위반 edge approve 차단(invalid_disabled)")

    # 7) evidence 없는 edge approve 차단
    g_noev = dict(g)
    g_noev["edges"] = [{"source_id": "node:e1", "target_id": "node:j", "relation": SUPPORTS,
                        "verb": "근거가_된다", "evidence_refs": []}]
    g_noev["nodes"] = g["nodes"]
    cn = build_graph_confirm(g_noev, approve=[1])
    ck(cn["summary"]["approved"] == 0, "evidence 없는 edge approve 차단")

    # 8) supports 외 relation approve 차단
    g_rel = dict(g)
    g_rel["edges"] = [{"source_id": "node:e1", "target_id": "node:j", "relation": "depends_on",
                       "verb": "선행조건이다", "evidence_refs": ["EVC-1"]}]
    g_rel["nodes"] = g["nodes"]
    cr = build_graph_confirm(g_rel, approve=[1])
    ck(cr["summary"]["approved"] == 0, "supports_judgment 외 relation approve 차단")

    # 9) validation fail 항목 → invalid_disabled 참고 표시(approve 불가)
    g_fail = gp.build_graph_preview(
        [N("a", "증거", "t1", ["E1"]), N("b", "판단", "t2", [])],
        edge_candidates=[{"source_id": "a", "target_id": "ghost", "relation": SUPPORTS, "evidence_refs": ["E1"]}])
    cf = build_graph_confirm(g_fail)
    ck(cf["summary"]["invalid_disabled"] >= 1 and all(it["approvable"] is False for it in cf["invalid_disabled"]),
       "validation fail → invalid_disabled(approve 불가 표시)")

    # 10) report only — 반환 dict 외 부작용 0(순수 함수·write 0 자명)
    ck("pack/export 미실행" in c0["note"] and "DB/pack write 0" in c0["note"], "report only · write 0 명시")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
