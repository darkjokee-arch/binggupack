# -*- coding: utf-8 -*-
"""OpenBinggu G2-C — 승인된 연필 후보 → 사용자 동사 선택 → 볼펜(6종 엣지) 확정 주입 (staging 한정).

흐름: G2-B에서 묶음 승인된 proposal(accepted_candidate) 중 1건을 사용자가 골라
      동사 6종(supports_judgment 등) + 방향을 **직접 선택** → 검증 게이트 전건 통과 시에만
      staging edges 테이블에 insert (candidate=1 유지 — confirmed 승격 0).

안전 (owner 금지선):
  - write = temp/staging SQLite 한정 (StagingDB 운영경로 거부 재사용). 운영 store write 0.
  - 확정 엣지도 candidate=1 · promotion_allowed=0 — confirmed 생성 0. deploy 0.
  - relation 은 사용자 선택값만 (자동 추론 0). openbinggu_verb_edge_schema.validate_verb_edge 가
    label_kind 매트릭스·방향·증거 승계를 전건 검증 (FAIL = 주입 거부).
  - 이중 확정 차단(proposal state=finalized 마킹) + 기각 경로(state=rejected, edges 0).

CLI: python openbinggu_proposal_to_verb_edge_g2c.py --selftest
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openbinggu_staging_write_selftest import StagingDB, OPERATING_PATHS, _hash  # 무수정 재사용
from openbinggu_proposal_batch_approval_g2b import open_staging, build_batch, decide_batch  # G2-B 재사용
import openbinggu_verb_edge_schema as schema
import openbinggu_label_kind_map as lkmap


def _staging_node_view(db, node_id):
    """staging nodes row → validate_verb_edge 용 노드 dict. node_type 영문/한글 모두 수용.
    구형 노드(G0 이전, node_type='Claim' 등 5종 외) = G0 분류기로 sentence 재분류 fallback
    (view 계층 변환만 — DB 노드 무수정·deterministic)."""
    row = db.con.execute("SELECT node_id, node_type, sentence FROM nodes WHERE node_id=? AND state='active'",
                         (node_id,)).fetchone()
    if not row:
        return None
    nt = row[1]
    kind_ko = lkmap.EN2KO.get(nt, nt)  # 영문이면 한글로, 이미 한글이면 그대로
    if kind_ko not in lkmap.KIND_KO:   # 구형(Claim 등) → 문장 재분류 fallback
        kind_ko, _ = lkmap.classify_label_kind(row[2])
    return {"id": row[0], "properties": {"label_kind": kind_ko, "sentence": row[2], "candidate": True}}


def finalize_proposal(db, proposal_id, relation, src_id, tgt_id, ctx, snap_dir):
    """proposal 1건 → 사용자 선택 relation/방향으로 staging edges 확정 주입.
    반환 {"applied": bool, "reason": ...}. 실패 = 주입 0 + audit BLOCK."""
    before = db.store_checksum()

    def block(reason):
        db.audit_append(ctx.get("actor", "human"), "finalize_edge", proposal_id,
                        "BLOCK", reason, before, before)
        return {"applied": False, "reason": reason}

    if ctx.get("actor") in ("auto", "reader"):
        return block("G4_no_auto")
    row = db.con.execute(
        "SELECT proposal_id, source, target, evidence_refs, state FROM edge_proposals WHERE proposal_id=?",
        (proposal_id,)).fetchone()
    if not row:
        return block("proposal_not_found")
    _, p_src, p_tgt, p_refs_json, p_state = row
    if p_state == "finalized":
        return block("already_finalized")
    if p_state == "rejected":
        return block("proposal_rejected")
    if p_state != "accepted_candidate":
        return block("proposal_not_accepted")
    # 방향 선택은 자유지만 노드쌍은 proposal 과 동일해야 함 (다른 쌍 끼워넣기 차단)
    if {src_id, tgt_id} != {p_src, p_tgt}:
        return block("pair_mismatch")

    src_view = _staging_node_view(db, src_id)
    tgt_view = _staging_node_view(db, tgt_id)
    if src_view is None or tgt_view is None:
        return block("node_not_in_staging")

    refs = json.loads(p_refs_json or "[]")
    edge_id = "edge:STAGING:g2c:" + _hash(src_id + "→" + tgt_id + "::" + relation)
    edge = {"id": edge_id, "source": src_id, "target": tgt_id,
            "properties": {"relation": relation, "candidate": True},
            "evidence_refs": refs, "promotion_allowed": False}
    verdict = schema.validate_verb_edge(edge, {src_id: src_view, tgt_id: tgt_view})
    if verdict["verdict"] != "PASS":
        return block("schema:" + verdict["reason"])
    if db.con.execute("SELECT 1 FROM edges WHERE edge_id=?", (edge_id,)).fetchone():
        return block("edge_already_exists")

    snap = os.path.join(snap_dir, "snap_g2c_" + _hash(before))
    shutil.copy2(db.path, snap)
    try:
        db.con.execute("BEGIN")
        db.con.execute(
            "INSERT INTO edges(edge_id,relation,source,target,candidate,state,evidence_refs,pack_id,content_hash,created_at) "
            "VALUES(?,?,?,?,1,'active',?,?,?,?)",
            (edge_id, relation, src_id, tgt_id, json.dumps(refs, ensure_ascii=False),
             "g2c", _hash(edge_id), "2026-06-11"))
        db.con.execute("UPDATE edge_proposals SET state='finalized' WHERE proposal_id=?", (proposal_id,))
        if ctx.get("checksum_mismatch"):
            db.con.execute("ROLLBACK")
            db.audit_append(ctx.get("actor", "human"), "finalize_edge", proposal_id,
                            "ROLLBACK", "sqlite_checksum_mismatch", before, db.store_checksum())
            return {"applied": False, "reason": "sqlite_checksum_mismatch"}
        db.con.execute("COMMIT")
    except Exception as ex:
        db.con.execute("ROLLBACK")
        return {"applied": False, "reason": "exception:" + type(ex).__name__}
    after = db.store_checksum()
    db.audit_append(ctx.get("actor", "human"), "finalize_edge", proposal_id, "ALLOW",
                    relation, before, after)
    return {"applied": True, "reason": None, "edge_id": edge_id, "snapshot": snap}


def reject_proposal(db, proposal_id, reason_text, ctx):
    """proposal 기각 — edges insert 0, state=rejected + audit. 사유 필수."""
    before = db.store_checksum()
    if ctx.get("actor") in ("auto", "reader"):
        db.audit_append(ctx.get("actor"), "reject_proposal", proposal_id, "BLOCK", "G4_no_auto", before, before)
        return {"applied": False, "reason": "G4_no_auto"}
    if not (reason_text or "").strip():
        db.audit_append(ctx.get("actor", "human"), "reject_proposal", proposal_id, "BLOCK",
                        "reason_required", before, before)
        return {"applied": False, "reason": "reason_required"}
    cur = db.con.execute("UPDATE edge_proposals SET state='rejected' WHERE proposal_id=? AND state='accepted_candidate'",
                         (proposal_id,))
    db.con.commit()
    if cur.rowcount == 0:
        db.audit_append(ctx.get("actor", "human"), "reject_proposal", proposal_id, "BLOCK",
                        "not_in_accepted_state", before, before)
        return {"applied": False, "reason": "not_in_accepted_state"}
    db.audit_append(ctx.get("actor", "human"), "reject_proposal", proposal_id, "REJECT",
                    reason_text[:80], before, db.store_checksum())
    return {"applied": True, "reason": None}


# ---------------- selftest ----------------

def _seed(db, snap_dir):
    """staging에 노드 3개 직접 insert(테스트 시드) + proposal 2건 묶음 승인(G2-B 경로)."""
    rows = [("node:a", "evidence", "로그에 결과가 기록되어 있다."),
            ("node:b", "judgment", "이 입찰은 마진이 낮아 보류한다."),
            ("node:c", "doc", "이 문서는 배포 절차를 정의한다.")]
    for nid, nt, s in rows:
        db.con.execute("INSERT OR IGNORE INTO nodes(node_id,node_type,sentence,candidate,promotion_allowed,state,pack_id,content_hash,created_at) "
                       "VALUES(?,?,?,1,0,'active','seed',?, '2026-06-11')", (nid, nt, s, _hash(nid)))
    db.con.commit()
    nodes_by_id = {nid: {"id": nid, "properties": {"label_kind": lkmap.EN2KO.get(nt, nt), "sentence": s,
                                                   "candidate": True}} for nid, nt, s in rows}
    props = [{"id": "prop:1", "label": "nearby_candidate", "signal": "co_evidence",
              "source": "node:a", "target": "node:b", "evidence_refs": ["EVC-1"]},
             {"id": "prop:2", "label": "nearby_candidate", "signal": "same_file",
              "source": "node:b", "target": "node:c", "evidence_refs": ["EVC-2"]}]
    batch = build_batch(props, nodes_by_id)
    r = decide_batch(db, batch, nodes_by_id, "approve", {"actor": "human"}, snap_dir)
    assert r["applied"], "seed batch approval failed"
    return props


def run():
    before_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="obg_g2c_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if ok else "FAIL"))

    db = open_staging(os.path.join(tmp, "s.sqlite"))
    _seed(db, snap_dir)

    # 1. 정상 확정 — prop:1 을 supports_judgment(증거→판단)로
    r1 = finalize_proposal(db, "prop:1", "supports_judgment", "node:a", "node:b",
                           {"actor": "human"}, snap_dir)
    e = db.con.execute("SELECT relation, candidate, evidence_refs FROM edges WHERE edge_id=?",
                       (r1.get("edge_id"),)).fetchone() if r1["applied"] else None
    st = db.con.execute("SELECT state FROM edge_proposals WHERE proposal_id='prop:1'").fetchone()[0]
    rec(1, "정상 확정 (supports 증거→판단, candidate 유지, 증거 승계, finalized 마킹)",
        r1["applied"] and e and e[0] == "supports_judgment" and e[1] == 1
        and json.loads(e[2]) == ["EVC-1"] and st == "finalized")

    # 2. 이중 확정 차단
    r2 = finalize_proposal(db, "prop:1", "supports_judgment", "node:a", "node:b",
                           {"actor": "human"}, snap_dir)
    rec(2, "이중 확정 차단 (already_finalized)", (not r2["applied"]) and r2["reason"] == "already_finalized")

    # 3. 매트릭스 위반 — prop:2 (판단↔문서)를 supports_judgment 로 (문서는 src 불가·판단은 tgt만)
    r3 = finalize_proposal(db, "prop:2", "supports_judgment", "node:c", "node:b",
                           {"actor": "human"}, snap_dir)
    rec(3, "매트릭스 위반 거부 (문서→판단 supports)", (not r3["applied"]) and r3["reason"].startswith("schema:"))

    # 4. 6종 외 relation 거부
    r4 = finalize_proposal(db, "prop:2", "related_to", "node:b", "node:c", {"actor": "human"}, snap_dir)
    rec(4, "6종 외 relation 거부", (not r4["applied"]) and r4["reason"].startswith("schema:"))

    # 5. 노드쌍 바꿔치기 차단
    r5 = finalize_proposal(db, "prop:2", "depends_on", "node:a", "node:b", {"actor": "human"}, snap_dir)
    rec(5, "노드쌍 mismatch 차단", (not r5["applied"]) and r5["reason"] == "pair_mismatch")

    # 6. actor=auto 차단
    r6 = finalize_proposal(db, "prop:2", "depends_on", "node:b", "node:c", {"actor": "auto"}, snap_dir)
    rec(6, "actor=auto 차단", (not r6["applied"]) and r6["reason"] == "G4_no_auto")

    # 7. 미존재 proposal 차단
    r7 = finalize_proposal(db, "prop:nope", "depends_on", "node:b", "node:c", {"actor": "human"}, snap_dir)
    rec(7, "미존재 proposal 차단", (not r7["applied"]) and r7["reason"] == "proposal_not_found")

    # 8. checksum rollback — prop:2 정상 방향(판단→문서? depends_on tgt={상태,개념,판단})
    #    문서는 depends_on tgt 불가 → refines(개념,판단→판단,개념)도 문서 불가.
    #    prop:2 는 의미상 6종 어디에도 안 맞는 쌍(판단↔문서) — "후보였지만 확정 불가" 케이스로
    #    기각 경로 검증에 사용 (현실: 모든 연필이 볼펜이 되는 건 아님).
    r8 = reject_proposal(db, "prop:2", "문서↔판단 쌍은 6종 어디에도 부적합", {"actor": "human"})
    st8 = db.con.execute("SELECT state FROM edge_proposals WHERE proposal_id='prop:2'").fetchone()[0]
    n_edges = db.con.execute("SELECT count(*) FROM edges").fetchone()[0]
    rec(8, "기각 경로 (rejected 마킹, edges 불변)", r8["applied"] and st8 == "rejected" and n_edges == 1)

    # 9. 기각 후 확정 시도 차단 + 사유 없는 기각 차단
    r9a = finalize_proposal(db, "prop:2", "depends_on", "node:b", "node:c", {"actor": "human"}, snap_dir)
    r9b = reject_proposal(db, "prop:1", "", {"actor": "human"})
    rec(9, "기각 후 확정 차단 + 사유 필수", (not r9a["applied"]) and r9a["reason"] == "proposal_rejected"
        and (not r9b["applied"]) and r9b["reason"] == "reason_required")

    # 10. checksum mismatch rollback (새 시드 DB)
    db2 = open_staging(os.path.join(tmp, "s2.sqlite"))
    _seed(db2, snap_dir)
    r10 = finalize_proposal(db2, "prop:1", "supports_judgment", "node:a", "node:b",
                            {"actor": "human", "checksum_mismatch": True}, snap_dir)
    rolled = db2.con.execute("SELECT count(*) FROM edges").fetchone()[0] == 0
    st10 = db2.con.execute("SELECT state FROM edge_proposals WHERE proposal_id='prop:1'").fetchone()[0]
    rec(10, "checksum rollback (edges 0 + finalized 마킹도 롤백)",
        (not r10["applied"]) and rolled and st10 == "accepted_candidate")
    db2.close()

    # 11. audit chain
    intact = db.verify_chain()
    db.con.execute("UPDATE audit_log SET action='TAMPER' WHERE seq=(SELECT min(seq) FROM audit_log)")
    db.con.commit()
    rec(11, "audit chain intact→변조 BROKEN", intact and (not db.verify_chain()))

    # 12. confirmed 0 + promotion 0 전수
    conf = db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0]
    promo = db.con.execute("SELECT count(*) FROM nodes WHERE promotion_allowed!=0").fetchone()[0]
    rec(12, "confirmed 0 · promotion 0 전수", conf == 0 and promo == 0)
    db.close()

    after_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    store_unchanged = before_mtime == after_mtime
    shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 74)
    print("OpenBinggu G2-C — proposal→동사 6종 볼펜 확정 selftest (temp staging, 운영 write 0)")
    print("=" * 74)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print("%s %2d %s" % ("[OK]" if v == "PASS" else "[X]", cid, desc))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("operating_store_unchanged=%s  confirmed_created=0  auto_relation_inference=0  deploy=0"
          % store_unchanged)
    gate = "GO" if (npass == len(results) and store_unchanged) else "NO-GO"
    print("GATE:", gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(run())
