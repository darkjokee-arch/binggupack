#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu — real staging 위 연필→묶음 승인→볼펜 확정 1사이클 실연 (owner GO, 조건 8개 고정).

조건 매핑: ①스냅샷 선확보 ②최소 1사이클(batch 1·확정 1·기각 1) ③승인 후 candidate 유지 확인
④볼펜 6종 매트릭스 통과 ⑤confirmed 0 ⑥rejected 경로+중복 확정 차단 재검증 ⑦read-back 수량 직접 확인
⑧rollback 절차 사전 문서화(OPENBINGGU_REAL_STAGING_CYCLE_ROLLBACK_PROCEDURE.md — 본 실행 전 작성됨).

불변: staging 한정(운영 write 0) · confirmed 승격 0 · deploy 0 · 자동 관계추론 0(동사는 owner 사이클 GO 범위 내 지정).
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_staging_write_selftest import OPERATING_PATHS  # noqa: E402
from openbinggu_proposal_batch_approval_g2b import open_staging, build_batch, decide_batch  # noqa: E402
from openbinggu_proposal_to_verb_edge_g2c import finalize_proposal, reject_proposal, _staging_node_view  # noqa: E402
from openbinggu_real_staging_apply_once import REAL_STAGING_DB, SNAP_DIR, _wal_checkpoint  # noqa: E402
import watcher_edge_proposal_g2 as wprop  # noqa: E402

# 실연 대상 (syn_plain 그룹 — 도메인 원칙 설명 문장 3종)
GAMMA = "node:STAGING:wch:40b7ec64"   # consumer view 가 결정적 evidence_basis 를 제공함을 명시
BETA = "node:STAGING:wch:f2bc46b2"    # candidate→confirmed 격상 금지 review-only 원칙 기술
PII_A = "node:STAGING:wch:4cf0c23e"   # 주민번호 노출 사례 문장
PII_B = "node:STAGING:wch:911c2324"   # 연락처 노출 사례 문장


def load_staging_nodes(db):
    """staging nodes → proposal 생산기 입력 형식. evidence_refs는 EvidenceSupports 엣지에서 역산.
    label_kind 는 view 계층 재분류(_staging_node_view) — DB 무수정."""
    nodes = []
    for (nid,) in db.con.execute("SELECT node_id FROM nodes WHERE state='active' ORDER BY node_id"):
        view = _staging_node_view(db, nid)
        refs = [r[0] for r in db.con.execute(
            "SELECT source FROM edges WHERE target=? AND relation='evidence_supports' ORDER BY source", (nid,))]
        nodes.append({"id": nid, "evidence_refs": refs,
                      "properties": {"label_kind": view["properties"]["label_kind"],
                                     "sentence": view["properties"]["sentence"], "candidate": True}})
    ev_index = [{"evidence_id": r[0], "source_path": r[1] or ""}
                for r in db.con.execute("SELECT evidence_id, source_pointer_id FROM evidence ORDER BY evidence_id")]
    return nodes, ev_index


def main():
    print("=" * 78)
    print("OpenBinggu — real staging 1사이클 실연 (연필→묶음 승인→볼펜 확정)")
    print("=" * 78)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-40s %s" % ("OK" if ok else "FAIL", name, detail))

    db = open_staging(REAL_STAGING_DB)  # 운영 경로면 PermissionError + edge_proposals 테이블 보장

    # ① 스냅샷 선확보
    _wal_checkpoint(db.con)
    before = db.store_checksum()
    snap = os.path.join(SNAP_DIR, "snap_cycle_before_" + before + ".sqlite")
    shutil.copy2(REAL_STAGING_DB, snap)
    ck("1_snapshot_확보", os.path.exists(snap), "checksum=%s" % before)

    # 연필 후보 생산 (자동 — 약한 라벨만)
    nodes, ev_index = load_staging_nodes(db)
    nodes_by_id = {n["id"]: n for n in nodes}
    proposals, stats = wprop.build_proposals(nodes, ev_index)
    strong0 = all(p["label"] in ("nearby_candidate", "stance_candidate") for p in proposals)
    ck("2_연필_후보_생산", len(proposals) > 0 and strong0,
       "n=%d labels=%s" % (len(proposals), stats["labels"]))

    # ② 묶음 승인 1batch (1클릭)
    batch = build_batch(proposals, nodes_by_id)
    r_ap = decide_batch(db, batch, nodes_by_id, "approve", {"actor": "human"}, SNAP_DIR)
    n_prop = db.con.execute("SELECT count(*) FROM edge_proposals").fetchone()[0]
    ck("3_묶음_승인_1batch", r_ap["applied"] and n_prop == len(proposals), "proposals=%d" % n_prop)

    # ③ 승인 후 candidate 유지
    bad_cand = db.con.execute(
        "SELECT count(*) FROM edge_proposals WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
    ck("4_승인후_candidate_유지", bad_cand == 0)

    # 대상 proposal id 식별 (감마-베타 쌍 / PII 쌍)
    def find_prop(a, b):
        row = db.con.execute(
            "SELECT proposal_id FROM edge_proposals WHERE (source=? AND target=?) OR (source=? AND target=?)",
            (a, b, b, a)).fetchone()
        return row[0] if row else None

    prop_main = find_prop(GAMMA, BETA)
    prop_pii = find_prop(PII_A, PII_B)
    ck("5_실연_쌍_식별", bool(prop_main and prop_pii))

    # ④ 볼펜 확정 1건 — 감마가 베타를 refines (판단→판단, 매트릭스 통과 기대)
    edges_before = db.con.execute("SELECT count(*) FROM edges").fetchone()[0]
    r_fin = finalize_proposal(db, prop_main, "refines", GAMMA, BETA, {"actor": "human"}, SNAP_DIR)
    edges_after = db.con.execute("SELECT count(*) FROM edges").fetchone()[0]
    fin_row = db.con.execute("SELECT relation, candidate, evidence_refs FROM edges WHERE edge_id=?",
                             (r_fin.get("edge_id"),)).fetchone() if r_fin.get("applied") else None
    ck("6_볼펜_확정_매트릭스_통과", r_fin.get("applied") and edges_after == edges_before + 1
       and fin_row and fin_row[0] == "refines" and fin_row[1] == 1
       and json.loads(fin_row[2]), "refines 감마→베타, evidence 승계")

    # ⑥-a rejected 경로 — PII 쌍은 6종 부적합으로 기각 (사유 필수)
    r_rej = reject_proposal(db, prop_pii, "PII 사례 병렬 나열 — 6종 동사 관계 부적합", {"actor": "human"})
    st_rej = db.con.execute("SELECT state FROM edge_proposals WHERE proposal_id=?", (prop_pii,)).fetchone()[0]
    ck("7_rejected_경로", r_rej["applied"] and st_rej == "rejected")

    # ⑥-b 중복 확정 차단 재검증
    r_dup = finalize_proposal(db, prop_main, "refines", GAMMA, BETA, {"actor": "human"}, SNAP_DIR)
    ck("8_중복_확정_차단", (not r_dup["applied"]) and r_dup["reason"] == "already_finalized")
    # 기각된 proposal 확정 시도 차단
    r_rejfin = finalize_proposal(db, prop_pii, "refines", PII_A, PII_B, {"actor": "human"}, SNAP_DIR)
    ck("9_기각후_확정_차단", (not r_rejfin["applied"]) and r_rejfin["reason"] == "proposal_rejected")

    # ⑤ confirmed 0 + promotion 0 전수
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edge_proposals WHERE promotion_allowed!=0").fetchone()[0])
    ck("10_confirmed_0_promotion_0", bad == 0)

    # audit chain
    ck("11_audit_chain_INTACT", db.verify_chain())

    # ⑦ read-back 수량 직접 확인
    _wal_checkpoint(db.con)
    cnt = {t: db.con.execute("SELECT count(*) FROM " + t).fetchone()[0]
           for t in ("nodes", "edges", "evidence", "edge_proposals", "audit_log")}
    fin_cnt = db.con.execute("SELECT count(*) FROM edge_proposals WHERE state='finalized'").fetchone()[0]
    rej_cnt = db.con.execute("SELECT count(*) FROM edge_proposals WHERE state='rejected'").fetchone()[0]
    acc_cnt = db.con.execute("SELECT count(*) FROM edge_proposals WHERE state='accepted_candidate'").fetchone()[0]
    exp_edges = 9 + 1  # EvidenceSupports 9 + 볼펜 refines 1
    ck("12_readback_수량", cnt["nodes"] == 9 and cnt["edges"] == exp_edges and cnt["evidence"] == 9
       and fin_cnt == 1 and rej_cnt == 1 and acc_cnt == cnt["edge_proposals"] - 2,
       "nodes=%d edges=%d evidence=%d proposals=%d(fin=%d/rej=%d/accepted=%d) audit=%d" %
       (cnt["nodes"], cnt["edges"], cnt["evidence"], cnt["edge_proposals"], fin_cnt, rej_cnt, acc_cnt,
        cnt["audit_log"]))
    db.close()

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    unchanged = op_before == op_after
    ck("13_운영_store_불변", unchanged)

    ok_all = all(o for _, o in checks)
    print("-" * 78)
    print("RESULT: %d/%d PASS  confirmed=0 deploy=0 자동관계추론=0" % (sum(1 for _, o in checks if o), len(checks)))
    print("rollback 절차: docs/OPENBINGGU_REAL_STAGING_CYCLE_ROLLBACK_PROCEDURE.md (사전 문서화, snapshot=%s)"
          % os.path.basename(snap))
    print("GATE:", "GO" if ok_all else "NO-GO")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
