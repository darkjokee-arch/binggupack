# -*- coding: utf-8 -*-
"""OpenBinggu G3 — 기각 도장(deprecated) staging 연동 + 판단 검증 리마인드 (staging 한정).

owner 조건 고정:
  - deprecated = **삭제가 아니라 보존 + 기본조회 제외** (Wikidata rank 차용, 글로벌 조사 후보 1)
  - deprecated_reason 필수 (없으면 차단)
  - 기각된 proposal 재확정 차단 유지 (G2-C 기존 게이트 + deprecated 엣지 물리 잔존 = 재확정 자동 차단)
  - 리마인드 = **자동 승격 0, 사람 검토 유도까지만** (목록 생성이 전부 — 상태 변경 없음)

write = staging SQLite 한정 (StagingDB 운영경로 거부 재사용). confirmed 0 · deploy 0.
CLI: python openbinggu_deprecate_and_remind_g3.py --selftest
"""
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openbinggu_staging_write_selftest import StagingDB, OPERATING_PATHS, _hash  # 무수정 재사용
from openbinggu_proposal_batch_approval_g2b import open_staging  # edge_proposals 테이블 보장

G3_SCHEMA = """
CREATE TABLE IF NOT EXISTS deprecations(
    item_id TEXT, kind TEXT, reason TEXT, counter_evidence_ref TEXT, ts TEXT,
    PRIMARY KEY(item_id, kind));
CREATE TABLE IF NOT EXISTS judgment_reviews(
    review_id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT, due_date TEXT,
    status TEXT DEFAULT 'pending', outcome TEXT, resolved_reason TEXT, ts TEXT);
"""

OUTCOMES = {"성공", "실패", "불확실", "판정불가"}


def open_g3(path):
    db = open_staging(path)
    db.con.executescript(G3_SCHEMA)
    db.con.commit()
    return db


# ---------------- 기각 도장 (deprecated) ----------------

def deprecate_item(db, kind, item_id, reason, ctx, snap_dir, counter_evidence_ref=None):
    """node/edge 1건 deprecated — 보존(물리 잔존) + state 변경 + 사유 기록. 사람만."""
    before = db.store_checksum()

    def block(rc):
        db.audit_append(ctx.get("actor", "human"), "deprecate", item_id, "BLOCK", rc, before, before)
        return {"applied": False, "reason": rc}

    if ctx.get("actor") in ("auto", "reader"):
        return block("G4_no_auto")
    if not (reason or "").strip():
        return block("deprecated_reason_required")
    table, col = ("nodes", "node_id") if kind == "node" else ("edges", "edge_id")
    if kind not in ("node", "edge"):
        return block("kind_invalid")
    row = db.con.execute("SELECT state FROM %s WHERE %s=?" % (table, col), (item_id,)).fetchone()
    if not row:
        return block("item_not_found")
    if row[0] == "deprecated":
        return block("already_deprecated")
    if row[0] == "tombstoned":
        return block("tombstoned_item")

    snap = os.path.join(snap_dir, "snap_g3_" + _hash(before))
    shutil.copy2(db.path, snap)
    db.con.execute("BEGIN")
    db.con.execute("UPDATE %s SET state='deprecated' WHERE %s=?" % (table, col), (item_id,))
    db.con.execute("INSERT INTO deprecations(item_id,kind,reason,counter_evidence_ref,ts) VALUES(?,?,?,?,?)",
                   (item_id, kind, reason[:200], counter_evidence_ref, "2026-06-11"))
    db.con.execute("COMMIT")
    db.audit_append(ctx.get("actor", "human"), "deprecate", item_id, "ALLOW", reason[:80],
                    before, db.store_checksum())
    return {"applied": True, "reason": None, "snapshot": snap}


def active_view(db):
    """기본 소비 view — deprecated/tombstoned 제외 (물리 보존은 그대로)."""
    nodes = [r[0] for r in db.con.execute("SELECT node_id FROM nodes WHERE state='active' ORDER BY node_id")]
    edges = [r[0] for r in db.con.execute("SELECT edge_id FROM edges WHERE state='active' ORDER BY edge_id")]
    return {"nodes": nodes, "edges": edges}


# ---------------- 판단 검증 리마인드 (자동 승격 0 — 목록 생성까지만) ----------------

def set_review_due(db, node_id, due_date, ctx):
    """판단 노드에 검증예정일 등록. 사람만 · 노드 active 실재 · pending 중복 차단."""
    before = db.store_checksum()

    def block(rc):
        db.audit_append(ctx.get("actor", "human"), "review_due", node_id, "BLOCK", rc, before, before)
        return {"applied": False, "reason": rc}

    if ctx.get("actor") in ("auto", "reader"):
        return block("G4_no_auto")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_date or ""):
        return block("due_date_invalid")
    row = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (node_id,)).fetchone()
    if not row or row[0] != "active":
        return block("node_not_active")
    if db.con.execute("SELECT 1 FROM judgment_reviews WHERE node_id=? AND status='pending'",
                      (node_id,)).fetchone():
        return block("pending_review_exists")
    db.con.execute("INSERT INTO judgment_reviews(node_id,due_date,status,ts) VALUES(?,?,'pending',?)",
                   (node_id, due_date, "2026-06-11"))
    db.con.commit()
    db.audit_append(ctx.get("actor", "human"), "review_due", node_id, "ALLOW", due_date,
                    before, db.store_checksum())
    return {"applied": True, "reason": None}


def list_due_reminders(db, today):
    """due 경과 + pending → 사람 검토 유도 목록(마크다운). 상태 변경·승격 0 (read-only)."""
    rows = db.con.execute(
        "SELECT r.node_id, r.due_date, n.sentence FROM judgment_reviews r "
        "JOIN nodes n ON n.node_id=r.node_id "
        "WHERE r.status='pending' AND r.due_date<=? ORDER BY r.due_date", (today,)).fetchall()
    lines = ["# 판단 검증 리마인드 — %s 기준 %d건 (사람 검토 유도, 자동 변경 없음)" % (today, len(rows))]
    for nid, due, sent in rows:
        lines.append("- [ ] (%s 예정) %s — `%s` → 결과 입력: 성공/실패/불확실/판정불가" % (due, (sent or "")[:50], nid))
    return {"count": len(rows), "items": [r[0] for r in rows], "markdown": "\n".join(lines)}


def resolve_review(db, node_id, outcome, reason, ctx):
    """사람이 결과 입력. 기록만 — 노드 자체(state·candidate) 무변. 강등 원하면 별도 deprecate(사람 행동)."""
    before = db.store_checksum()

    def block(rc):
        db.audit_append(ctx.get("actor", "human"), "review_resolve", node_id, "BLOCK", rc, before, before)
        return {"applied": False, "reason": rc}

    if ctx.get("actor") in ("auto", "reader"):
        return block("G4_no_auto")
    if outcome not in OUTCOMES:
        return block("outcome_invalid")
    if not (reason or "").strip():
        return block("resolve_reason_required")
    cur = db.con.execute(
        "UPDATE judgment_reviews SET status='resolved', outcome=?, resolved_reason=? "
        "WHERE node_id=? AND status='pending'", (outcome, reason[:200], node_id))
    db.con.commit()
    if cur.rowcount == 0:
        return block("no_pending_review")
    db.audit_append(ctx.get("actor", "human"), "review_resolve", node_id, "ALLOW", outcome,
                    before, db.store_checksum())
    return {"applied": True, "reason": None}


# ---------------- selftest ----------------

def run():
    before_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="obg_g3_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if ok else "FAIL"))

    db = open_g3(os.path.join(tmp, "s.sqlite"))
    for nid, s in [("n1", "이 입찰은 보류한다."), ("n2", "마진 확보로 참여한다."), ("n3", "절차가 진행 중이다.")]:
        db.con.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,promotion_allowed,state,pack_id,content_hash,created_at) "
                       "VALUES(?,?,?,1,0,'active','g3test',?, '2026-06-11')", (nid, "judgment", s, _hash(nid)))
    db.con.execute("INSERT INTO edges(edge_id,relation,source,target,candidate,state,evidence_refs,pack_id,content_hash,created_at) "
                   "VALUES('e1','refines','n2','n1',1,'active','[\"EVC-1\"]','g3test','h','2026-06-11')")
    db.con.commit()

    # 1. 정상 deprecate — 보존 + 기본 view 제외
    r1 = deprecate_item(db, "node", "n1", "낙찰가 공개로 반증됨", {"actor": "human"}, snap_dir, "EVC-9")
    phys = db.con.execute("SELECT count(*) FROM nodes WHERE node_id='n1'").fetchone()[0]
    view = active_view(db)
    dep = db.con.execute("SELECT reason FROM deprecations WHERE item_id='n1'").fetchone()
    rec(1, "deprecate 보존+기본조회 제외+사유 기록", r1["applied"] and phys == 1
        and "n1" not in view["nodes"] and "n2" in view["nodes"] and dep is not None)

    # 2. 사유 없음 차단
    r2 = deprecate_item(db, "node", "n2", "  ", {"actor": "human"}, snap_dir)
    rec(2, "deprecated_reason 필수", (not r2["applied"]) and r2["reason"] == "deprecated_reason_required")

    # 3. 이중 deprecate 차단
    r3 = deprecate_item(db, "node", "n1", "또 기각", {"actor": "human"}, snap_dir)
    rec(3, "이중 deprecate 차단", (not r3["applied"]) and r3["reason"] == "already_deprecated")

    # 4. auto 차단 + 미존재 차단
    r4a = deprecate_item(db, "node", "n2", "사유", {"actor": "auto"}, snap_dir)
    r4b = deprecate_item(db, "edge", "e_nope", "사유", {"actor": "human"}, snap_dir)
    rec(4, "auto/미존재 차단", (not r4a["applied"]) and r4a["reason"] == "G4_no_auto"
        and (not r4b["applied"]) and r4b["reason"] == "item_not_found")

    # 5. 엣지 deprecate — 보존 + view 제외
    r5 = deprecate_item(db, "edge", "e1", "관계 근거 철회", {"actor": "human"}, snap_dir)
    view5 = active_view(db)
    phys5 = db.con.execute("SELECT count(*) FROM edges WHERE edge_id='e1'").fetchone()[0]
    rec(5, "엣지 deprecate 보존+제외", r5["applied"] and phys5 == 1 and "e1" not in view5["edges"])

    # 6. set_due 정상 + pending 중복 차단
    r6a = set_review_due(db, "n2", "2026-06-10", {"actor": "human"})
    r6b = set_review_due(db, "n2", "2026-07-01", {"actor": "human"})
    rec(6, "검증예정일 등록 + pending 중복 차단", r6a["applied"]
        and (not r6b["applied"]) and r6b["reason"] == "pending_review_exists")

    # 7. due 형식·비active 노드·auto 차단
    r7a = set_review_due(db, "n3", "06/10", {"actor": "human"})
    r7b = set_review_due(db, "n1", "2026-06-10", {"actor": "human"})  # deprecated 노드
    r7c = set_review_due(db, "n3", "2026-06-10", {"actor": "auto"})
    rec(7, "형식/비active/auto 차단", all(not r["applied"] for r in (r7a, r7b, r7c)))

    # 8. 리마인드 — 과거 due 1건 목록, 미래 due 0건, 상태 무변(자동 승격 0)
    before8 = db.store_checksum()
    rem_today = list_due_reminders(db, "2026-06-11")
    rem_past = list_due_reminders(db, "2026-06-09")
    after8 = db.store_checksum()
    rec(8, "리마인드 목록(검토 유도만, 상태 무변)", rem_today["count"] == 1 and "n2" in rem_today["items"]
        and rem_past["count"] == 0 and before8 == after8 and "자동 변경 없음" in rem_today["markdown"])

    # 9. resolve 정상 — 기록만, 노드 무변
    node_before = db.con.execute("SELECT state,candidate FROM nodes WHERE node_id='n2'").fetchone()
    r9 = resolve_review(db, "n2", "실패", "실제 낙찰가가 예상과 달랐음", {"actor": "human"})
    node_after = db.con.execute("SELECT state,candidate FROM nodes WHERE node_id='n2'").fetchone()
    st9 = db.con.execute("SELECT status,outcome FROM judgment_reviews WHERE node_id='n2'").fetchone()
    rec(9, "resolve 기록만(노드 state/candidate 무변)", r9["applied"] and node_before == node_after
        and st9 == ("resolved", "실패"))

    # 10. resolve 가드 — outcome enum·사유 필수·pending 없음·auto
    r10a = resolve_review(db, "n2", "성공", "재차", {"actor": "human"})       # pending 없음
    r10b = resolve_review(db, "n3", "애매", "x", {"actor": "human"})          # enum 외
    rec(10, "resolve 가드 (pending 없음/enum 외)", (not r10a["applied"]) and r10a["reason"] == "no_pending_review"
        and (not r10b["applied"]) and r10b["reason"] == "outcome_invalid")

    # 11. audit chain
    intact = db.verify_chain()
    db.con.execute("UPDATE audit_log SET action='TAMPER' WHERE seq=(SELECT min(seq) FROM audit_log)")
    db.con.commit()
    rec(11, "audit chain intact→변조 BROKEN", intact and (not db.verify_chain()))

    # 12. confirmed 0 · promotion 0 전수
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0])
    rec(12, "confirmed 0 · promotion 0 전수", bad == 0)
    db.close()

    after_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    unchanged = before_mtime == after_mtime
    shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 74)
    print("OpenBinggu G3 — deprecated 도장 + 판단 검증 리마인드 selftest (temp, 운영 write 0)")
    print("=" * 74)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print("%s %2d %s" % ("[OK]" if v == "PASS" else "[X]", cid, desc))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("operating_store_unchanged=%s  auto_promotion=0  confirmed=0  deploy=0" % unchanged)
    gate = "GO" if (npass == len(results) and unchanged) else "NO-GO"
    print("GATE:", gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(run())
