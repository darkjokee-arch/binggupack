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
from openbinggu_incoming_to_staging import SECRET_PATTERNS          # PII/secret 재스캔 위임(신규 정의 0)

CONFIRM_CAVEAT = "candidate · unverified · 사람 승인해도 pack 미기록 · export 별도 단계"

# 사람 도장 후 sync_edges 적재까지의 caveat — 운영 import 는 별도(owner-only) 단계임을 명시.
SYNC_CAVEAT = ("승인 대기 종료 · sync_edges 적재(어댑터 전용 저장소) · 운영 미등재 · "
               "운영 import 는 별도 명령(hag_sync_adapter --import-edges, owner-only)")


def _has_secret(text):
    """edge sentence/문장에 PII/secret 패턴이 있으면 True(보수적 STOP). 신규 패턴 정의 0."""
    if not isinstance(text, str):
        return False
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            return True
    return False


def apply_confirm_to_sync(approved_edges, sync_db_path, nodes_by_id=None,
                          actor="human", now=0):
    """5층 graph_confirm 의 approved[] → sync_edges 에 'confirmed'(사람 도장) 멱등 기록.

    헌법 준수:
      - actor='human' 만 수신 — 그 외 SyncError(BLOCK). node→node 강한관계 자동생성 금지·사람 도장만.
      - 신규 EDGE SAVE 경로 신설 0 — hag_sync_adapter(open_sync_db/edge_key) 재사용.
      - 운영 ledger write 0 — sync_edges(어댑터 전용 저장소)에만 'confirmed' 기록.
        영구(운영 edges) 등재는 owner-only import_confirmed_edges 별도 단계.
      - 적재 전 재검증: relation=supports_judgment + evidence + 매트릭스(validate_verb_edge) +
        PII/secret 재스캔(차단) + dangling(노드 부재) skip.
      - 멱등: edge_key PK · INSERT OR IGNORE 동작(record_proposals 멱등 재사용). 2회 → 적재 0.

    approved_edges = build_graph_confirm(...)['approved'] (decision='approved' 항목들).
    nodes_by_id = {node_id: {id, properties{label_kind, candidate}, sentence?}} — 재검증·secret 스캔용.
    반환 {applied, rejected[], dangling[], detail}.
    """
    # 신규 모듈 호출은 함수 내부 import — graph_preview/confirm 순수성 보존(adapter 미존재 환경도 import 가능).
    import sys as _sys
    _sys.path.insert(0, os.path.join(HERE, "hybrid_agi"))
    from hag_sync_adapter import (open_sync_db, edge_key, record_proposals,
                                   confirm_edge, SyncError)

    if actor != "human":
        raise SyncError("BLOCK actor!=human (사람 도장만 sync 적재): %r" % actor)

    nodes_by_id = nodes_by_id or {}
    proposals, key_meta, rejected, dangling = [], [], [], []
    for e in (approved_edges or []):
        if e.get("decision") != "approved":          # approved 항목만(방어)
            rejected.append({**_edge_brief(e), "reason": "not_approved"})
            continue
        sid, did, rel = e.get("source_id"), e.get("target_id"), e.get("relation")
        # 1) supports_judgment 만(node→node 강한관계 = 사람 도장만, 매트릭스 위임 재검)
        if rel != SUPPORTS:
            rejected.append({**_edge_brief(e), "reason": "not_supports_judgment"})
            continue
        if not e.get("evidence_refs"):
            rejected.append({**_edge_brief(e), "reason": "no_evidence"})
            continue
        # 2) dangling — 노드 부재(nodes_by_id 제공 시) skip (매트릭스보다 먼저 — 노드 없으면 검증 무의미)
        if nodes_by_id and (sid not in nodes_by_id or did not in nodes_by_id):
            dangling.append(_edge_brief(e))
            continue
        edge_obj = {"id": "c", "source": sid, "target": did,
                    "properties": {"relation": rel, "candidate": True},
                    "evidence_refs": list(e.get("evidence_refs") or []),
                    "promotion_allowed": False}
        v = validate_verb_edge(edge_obj, nodes_by_id)
        if v["verdict"] != "PASS":
            rejected.append({**_edge_brief(e), "reason": "matrix:%s" % v.get("reason", "fail")})
            continue
        # 3) PII/secret 재스캔 — source/target 문장에 시크릿이면 적재 차단
        src_txt = (nodes_by_id.get(sid, {}).get("sentence") if nodes_by_id else None) or ""
        dst_txt = (nodes_by_id.get(did, {}).get("sentence") if nodes_by_id else None) or ""
        if _has_secret(src_txt) or _has_secret(dst_txt):
            rejected.append({**_edge_brief(e), "reason": "secret_redact"})
            continue
        ek = edge_key(sid, did, rel)
        proposals.append({
            "edge_key": ek, "src_node_id": sid, "dst_node_id": did, "relation": rel,
            "src_checksum": None, "dst_checksum": None, "kmap_version": "v1",
            "evidence_refs": list(e.get("evidence_refs") or []), "status": "proposed",
        })
        key_meta.append(ek)

    conn = open_sync_db(sync_db_path)
    try:
        # 멱등 적재(proposed) → 사람 도장(confirmed). confirm_edge 가 actor!=human 차단(이중 게이트).
        record_proposals(conn, proposals, now=now)
        applied = 0
        cur = conn.cursor()
        for ek in key_meta:
            # 멱등: 이미 confirmed/imported(이전 도장분)면 noop — proposed→confirmed 전이만 신규 카운트.
            cur.execute("SELECT status FROM sync_edges WHERE edge_key = ?", (ek,))
            row = cur.fetchone()
            if row is None or row[0] != "proposed":
                continue
            r = confirm_edge(conn, ek, actor=actor, now=now)
            if r.get("status") == "confirmed":
                applied += 1
    finally:
        conn.close()
    return {"applied": applied, "rejected": rejected, "dangling": dangling,
            "caveat": SYNC_CAVEAT,
            "detail": "사람 도장 %d건 → sync_edges 'confirmed' 적재 · 운영 import 는 owner-only 별도 단계" % applied}


def _edge_brief(e):
    return {"source_id": e.get("source_id"), "target_id": e.get("target_id"),
            "relation": e.get("relation")}


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
        if i in defer:
            deferred.append({**base, "decision": "deferred"})
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

    # ---- apply_confirm_to_sync (도장 → sync_edges 실저장 · temp 만) ----
    import tempfile
    import sqlite3
    sys.path.insert(0, os.path.join(HERE, "hybrid_agi"))
    from hag_sync_adapter import SyncError

    tmp = tempfile.mkdtemp(prefix="graph_confirm_sync_")
    sync_db = os.path.join(tmp, "sync.sqlite")
    # nodes_by_id (재검증·secret 스캔용) — 정상 graph 의 노드들에 sentence 부여
    nbi = {n["id"]: {"id": n["id"],
                     "properties": {"label_kind": n.get("label_kind"), "candidate": True},
                     "sentence": n.get("text", "")}
           for n in g["nodes"]}
    appr = build_graph_confirm(g, approve=[1, 2])["approved"]   # 정상 supports edge 2건 승인

    # T20: actor != 'human' → BLOCK
    blk = False
    try:
        apply_confirm_to_sync(appr, sync_db, nodes_by_id=nbi, actor="ai", now=10)
    except SyncError:
        blk = True
    ck(blk, "T20 apply actor!=human BLOCK")

    # T21: 정상 approved 2건 → sync 'confirmed' 적재 2건
    r1 = apply_confirm_to_sync(appr, sync_db, nodes_by_id=nbi, actor="human", now=20)
    cnt = sqlite3.connect(sync_db).execute(
        "SELECT count(*) FROM sync_edges WHERE status='confirmed'").fetchone()[0]
    ck(r1["applied"] == 2 and cnt == 2, "T21 approved 2건 → sync confirmed 2건")
    ck("운영 미등재" in r1["caveat"] and "owner-only" in r1["detail"],
       "T21b caveat=운영 미등재·import 별도(owner-only)")

    # T22: 2회 실행 → 멱등(applied=0·중복 0)
    r2 = apply_confirm_to_sync(appr, sync_db, nodes_by_id=nbi, actor="human", now=30)
    cnt2 = sqlite3.connect(sync_db).execute("SELECT count(*) FROM sync_edges").fetchone()[0]
    ck(r2["applied"] == 0 and cnt2 == 2, "T22 2회 실행 → 멱등(applied 0·중복 0)")

    # T23: secret 감지 edge → 적재 차단(reject)
    # secret-like 문자열은 런타임 조립(공개 tree-scan 오탐 회피 — 실코드에 시크릿 리터럴 0).
    sdb2 = os.path.join(tmp, "sync2.sqlite")
    nbi_sec = dict(nbi)
    sid0 = appr[0]["source_id"]
    _fake_secret = "api" + "_key=" + "sk-" + "live-" + ("A" * 16)
    nbi_sec[sid0] = {**nbi[sid0], "sentence": _fake_secret}
    r3 = apply_confirm_to_sync(appr, sdb2, nodes_by_id=nbi_sec, actor="human", now=40)
    ck(any(x["reason"] == "secret_redact" for x in r3["rejected"]),
       "T23 secret 감지 edge 적재 차단(secret_redact)")
    ck(r3["applied"] < 2, "T23b secret edge 는 applied 에서 제외")

    # T24: dangling(노드 부재) edge → skip(적재 0)
    sdb3 = os.path.join(tmp, "sync3.sqlite")
    dang = [{**appr[0], "source_id": "ghost-src"}]   # nodes_by_id 에 없는 노드
    r4 = apply_confirm_to_sync(dang, sdb3, nodes_by_id=nbi, actor="human", now=50)
    ck(len(r4["dangling"]) >= 1 and r4["applied"] == 0, "T24 dangling edge skip(적재 0)")

    # T25: supports 외 relation(approve 단계 통과 못 함) 직접 주입 → 적재 차단
    sdb4 = os.path.join(tmp, "sync4.sqlite")
    bad_rel = [{"decision": "approved", "source_id": appr[0]["source_id"],
                "target_id": appr[0]["target_id"], "relation": "depends_on",
                "evidence_refs": ["EVC-1"]}]
    r5 = apply_confirm_to_sync(bad_rel, sdb4, nodes_by_id=nbi, actor="human", now=60)
    ck(r5["applied"] == 0 and any(x["reason"] == "not_supports_judgment" for x in r5["rejected"]),
       "T25 supports 외 relation 적재 차단(node→node 자동 강한관계 금지)")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
