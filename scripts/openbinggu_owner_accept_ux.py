# -*- coding: utf-8 -*-
"""OpenBinggu v1.0 — owner_accepted UX (개인 장부 수용 기록, record-only).

owner 최종 원칙(2026-06-11 GO — R2 §2의 current-view 스키마를 supersede):
  - confirmed 어휘 금지 · 상태명 = owner_accepted.
  - 기존 candidate row 직접 변경 0 — **별도 record-only 테이블** owner_acceptances 에
    event 행 append 만 한다(같은 node_id 다중 event 허용, nodes 상태 모델 무변).
  - UNIQUE↔철회 보존 모순 해소: UNIQUE 없음 — 철회(owner_unaccept)도 삭제 아닌 보존형
    event, **현재 상태 = node 별 최신 event** 로 계산. 재수용 = 새 event.
  - confirm = "ACCEPT <n> <id8>" / "UNACCEPT <n> <id8>" 이중 바인딩 — transaction 밖에서
    받고, 실행 직전 목록 재실행 + id8 재검증(stale 오지정 BLOCK, 기각/replace UX 동형).
  - deprecated wins: 기각 후보 ACCEPT = BLOCK. duplicate accept = **BLOCK(already_accepted)**
    (멱등 무시가 아닌 명시 거부 — 사람이 현재 상태를 모른 채 누른 행동을 그대로 통과시키지 않는다).
  - OpenCrab 업로드에는 accepted_filter_ids(accepted ∩ active) **필터 입력으로만** —
    업로드 승인(UPLOAD 문구·G1~G7)은 완전 별개. 자동 confirmed 생성 0.

불변: real staging 접근 0 · OpenCrab 호출 0 · raw 원문 저장 0 · 운영 store write 0.
CLI: python openbinggu_owner_accept_ux.py --selftest
     (real staging 적용은 별도 GO 필요 — 본 단계는 temp selftest만)
"""
import os
import shutil
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_staging_write_selftest import OPERATING_PATHS, _now_iso  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import open_g3, deprecate_item  # noqa: E402
from openbinggu_candidate_list_view import list_candidates, node_id8, T1  # noqa: E402
from openbinggu_conversation_candidate_save import save_selected  # noqa: E402

ACCEPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_acceptances(
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    event TEXT NOT NULL CHECK(event IN ('owner_accepted','owner_unaccept')),
    reason TEXT NOT NULL,
    ts TEXT);
CREATE INDEX IF NOT EXISTS idx_owner_acceptances_node ON owner_acceptances(node_id, event_id);
"""


def open_accept(path):
    db = open_g3(path)
    db.con.executescript(ACCEPT_SCHEMA)
    db.con.commit()
    return db


def latest_event(db, node_id):
    row = db.con.execute(
        "SELECT event FROM owner_acceptances WHERE node_id=? ORDER BY event_id DESC LIMIT 1",
        (node_id,)).fetchone()
    return row[0] if row else None


def accept_by_node_id(db, node_id, reason, ctx, ts=None):
    """저장 직후 알려진 node_id 를 직접 수용 기록(pair --accept 통합 전용).
    _gate(목록 재실행·id8·confirm 재검증)를 건너뛴다 — 호출측(pair PAIR confirm)이 이미
    사람 확인을 수행했으므로 이중 확인은 과잉. 검증(actor·active·중복·audit)은 accept_from_list 동형."""
    db.con.executescript(ACCEPT_SCHEMA)          # 스키마 보장(멱등)
    db.con.commit()
    before = db.store_checksum()

    def block(rc):
        db.audit_append(ctx.get("actor", "human"), "owner_accept", node_id, "BLOCK", rc, before, before)
        return {"applied": False, "reason": rc}

    if ctx.get("actor", "").strip().lower() != "human":
        return block("G4_no_auto")
    if not (reason or "").strip():
        return block("reason_required")
    st = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (node_id,)).fetchone()
    if not st or st[0] != "active":
        return block("target_not_active")          # deprecated wins
    if latest_event(db, node_id) == "owner_accepted":
        return block("already_accepted")
    with db.write_lock():
        db.con.execute("INSERT INTO owner_acceptances(node_id,event,reason,ts) VALUES(?,?,?,?)",
                       (node_id, "owner_accepted", reason[:200], _now_iso(ts)))
        db.con.commit()
        db.audit_append(ctx.get("actor", "human"), "owner_accept", node_id,
                        "ALLOW", reason[:80], before, db.store_checksum(), ts=ts)
    return {"applied": True, "node_id": node_id, "event": "owner_accepted"}


def accepted_view(db):
    """현재 수용 상태 = node 별 최신 event. 반환 {node_id: True(accepted)} — read-only."""
    out = {}
    for nid, ev in db.con.execute(
            "SELECT node_id, event FROM owner_acceptances ORDER BY event_id"):
        out[nid] = (ev == "owner_accepted")
    return {nid: True for nid, acc in out.items() if acc}


def accepted_filter_ids(db):
    """OpenCrab 업로드 **필터 입력 전용** — accepted ∩ active (deprecated wins).
    업로드 승인(UPLOAD 문구·preflight G1~G7)과 완전 별개 — 본 함수는 후보 선별 입력일 뿐."""
    acc = accepted_view(db)
    if not acc:
        return []
    rows = db.con.execute(
        "SELECT node_id FROM nodes WHERE state='active' AND node_id IN (%s) ORDER BY node_id"
        % ",".join("?" * len(acc)), list(acc))
    return [r[0] for r in rows]


def _gate(db, action, verb, index, node_hash8, reason, ctx, status, kind):
    """공통 입구 게이트 — BLOCK 시 (None, block_result), 통과 시 (row, None)."""
    before = db.store_checksum()

    def block(rc):
        db.audit_append(ctx.get("actor", "human"), action, "idx:%s" % index,
                        "BLOCK", rc, before, before)
        return {"applied": False, "reason": rc}

    if ctx.get("actor") in ("auto", "reader"):
        return None, block("G4_no_auto")
    if ctx.get("confirm") != "%s %s %s" % (verb, index, node_hash8):
        return None, block("confirm_phrase_mismatch")
    if not (reason or "").strip():
        return None, block("reason_required")
    rows = list_candidates(db, status, kind)["rows"]
    if not isinstance(index, int) or index < 1 or index > len(rows):
        return None, block("index_out_of_range")
    row = rows[index - 1]
    if node_id8(row["node_id"]) != node_hash8:
        return None, block("node_hash_mismatch")
    return row, None


def accept_from_list(db, index, node_hash8, reason, ctx, status="all", kind=None, ts=None):
    """목록 인덱스 1건 수용 기록. confirm="ACCEPT <index> <id8>" 정확 일치 의무.
    candidate row 무변 — owner_acceptances 에 event 1행 append 만."""
    row, blocked = _gate(db, "owner_accept", "ACCEPT", index, node_hash8, reason, ctx, status, kind)
    if blocked:
        return blocked
    before = db.store_checksum()

    def block(rc):
        db.audit_append(ctx.get("actor", "human"), "owner_accept", row["node_id"],
                        "BLOCK", rc, before, before)
        return {"applied": False, "reason": rc}

    st = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (row["node_id"],)).fetchone()
    if not st or st[0] != "active":
        return block("target_not_active")          # deprecated wins
    if latest_event(db, row["node_id"]) == "owner_accepted":
        return block("already_accepted")           # duplicate = 명시 BLOCK (정책 고정)
    with db.write_lock():
        db.con.execute("INSERT INTO owner_acceptances(node_id,event,reason,ts) VALUES(?,?,?,?)",
                       (row["node_id"], "owner_accepted", reason[:200], _now_iso(ts)))
        db.con.commit()
        db.audit_append(ctx.get("actor", "human"), "owner_accept", row["node_id"],
                        "ALLOW", reason[:80], before, db.store_checksum(), ts=ts)
    return {"applied": True, "node_id": row["node_id"], "event": "owner_accepted"}


def unaccept_from_list(db, index, node_hash8, reason, ctx, status="all", kind=None, ts=None):
    """수용 철회 — 삭제 아닌 보존형 event append. confirm="UNACCEPT <index> <id8>"."""
    row, blocked = _gate(db, "owner_unaccept", "UNACCEPT", index, node_hash8, reason, ctx, status, kind)
    if blocked:
        return blocked
    before = db.store_checksum()
    if latest_event(db, row["node_id"]) != "owner_accepted":
        db.audit_append(ctx.get("actor", "human"), "owner_unaccept", row["node_id"],
                        "BLOCK", "not_currently_accepted", before, before)
        return {"applied": False, "reason": "not_currently_accepted"}
    with db.write_lock():
        db.con.execute("INSERT INTO owner_acceptances(node_id,event,reason,ts) VALUES(?,?,?,?)",
                       (row["node_id"], "owner_unaccept", reason[:200], _now_iso(ts)))
        db.con.commit()
        db.audit_append(ctx.get("actor", "human"), "owner_unaccept", row["node_id"],
                        "ALLOW", reason[:80], before, db.store_checksum(), ts=ts)
    return {"applied": True, "node_id": row["node_id"], "event": "owner_unaccept"}


# ---------------- selftest (temp) ----------------

def _insert_shift_node(db):
    from openbinggu_staging_write_selftest import _hash
    db.con.execute(
        "INSERT INTO nodes(node_id,node_type,sentence,candidate,promotion_allowed,state,pack_id,content_hash,created_at) "
        "VALUES('node:AAA:shift','judgment','[검증픽스처] 목록 시프트 유발용 판단이다.',1,0,'active','accux_fix',?, ?)",
        (_hash("node:AAA:shift"), _now_iso()))
    db.con.commit()


def _idx_of(rows, node_id):
    for i, r in enumerate(rows, 1):
        if r["node_id"] == node_id:
            return i, r["id8"]
    return None, None


def main_selftest():
    print("=" * 78)
    print("owner_accepted UX — temp selftest (record-only event·candidate row 무변·운영/real 접근 0)")
    print("=" * 78)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_accux_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-52s %s" % ("OK" if ok else "FAIL", name, detail))

    db = open_accept(os.path.join(tmp, "s.sqlite"))
    # selftest 픽스처는 판단+상태+개념 3종 다양성을 요구(216~ kind 검사) — capture SSOT 게이트
    # (6/27 추가)가 순수 상태/개념을 막으므로, 프로그램적 셋업은 explicit=True 로 게이트 우회.
    r0 = save_selected(db, T1, [1, 2, 3], {"actor": "human", "confirm": "SAVE 1,2,3"}, snap_dir, explicit=True)
    ck("0_시나리오_구성(후보3건)", r0["applied"] and r0["saved"] == 3)
    rows0 = list_candidates(db)["rows"]
    j_nid = next(r["node_id"] for r in rows0 if r["kind"] == "판단")
    s_nid = next(r["node_id"] for r in rows0 if r["kind"] == "상태")

    def node_tuple(nid):
        return db.con.execute("SELECT node_id,node_type,sentence,candidate,promotion_allowed,state "
                              "FROM nodes WHERE node_id=?", (nid,)).fetchone()

    # 1. 정상 accept — event 1행 + candidate row byte-identical 무변
    i1, h1 = _idx_of(rows0, j_nid)
    nb = node_tuple(j_nid)
    r1 = accept_from_list(db, i1, h1, "유지 판단 — 검증 통과 이력",
                          {"actor": "human", "confirm": "ACCEPT %s %s" % (i1, h1)})
    na = node_tuple(j_nid)
    ev_n = db.con.execute("SELECT count(*) FROM owner_acceptances WHERE node_id=?", (j_nid,)).fetchone()[0]
    ck("1_정상_accept(event1행+candidate_row_무변)",
       r1["applied"] and nb == na and ev_n == 1 and accepted_view(db).get(j_nid) is True)

    # 2. duplicate accept — 명시 BLOCK 정책 (already_accepted)
    r2 = accept_from_list(db, i1, h1, "또 수용",
                          {"actor": "human", "confirm": "ACCEPT %s %s" % (i1, h1)})
    ck("2_duplicate_accept_BLOCK(already_accepted_정책)", (not r2["applied"]) and r2["reason"] == "already_accepted")

    # 3. unaccept 보존형 — 행 append(삭제 0), 최신 view 에서 제외
    r3 = unaccept_from_list(db, i1, h1, "재검토 필요",
                            {"actor": "human", "confirm": "UNACCEPT %s %s" % (i1, h1)})
    ev3 = db.con.execute("SELECT count(*) FROM owner_acceptances WHERE node_id=?", (j_nid,)).fetchone()[0]
    ck("3_unaccept_보존형(event누적+view제외)",
       r3["applied"] and ev3 == 2 and j_nid not in accepted_view(db))

    # 4. 재수용 허용 — UNIQUE 모순 해소 증명 (event 3행 누적·view 복귀)
    r4 = accept_from_list(db, i1, h1, "재검토 끝 재수용",
                          {"actor": "human", "confirm": "ACCEPT %s %s" % (i1, h1)})
    ev4 = db.con.execute("SELECT count(*) FROM owner_acceptances WHERE node_id=?", (j_nid,)).fetchone()[0]
    ck("4_재수용_허용(철회보존_모순해소)", r4["applied"] and ev4 == 3 and accepted_view(db).get(j_nid) is True)

    # 5. deprecated 후보 ACCEPT BLOCK (deprecated wins) + 기각된 accepted 노드의 필터 제외
    deprecate_item(db, "node", s_nid, "기각 표본", {"actor": "human"}, snap_dir)
    rows5 = list_candidates(db)["rows"]
    i5, h5 = _idx_of(rows5, s_nid)
    r5 = accept_from_list(db, i5, h5, "사유", {"actor": "human", "confirm": "ACCEPT %s %s" % (i5, h5)})
    flt = accepted_filter_ids(db)
    ck("5_deprecated_ACCEPT_BLOCK+필터=accepted∩active",
       (not r5["applied"]) and r5["reason"] == "target_not_active"
       and flt == [j_nid])

    # 6. id8 mismatch BLOCK
    wrong_h = rows5[0]["id8"] if rows5[0]["node_id"] != j_nid else rows5[1]["id8"]
    i6, _ = _idx_of(rows5, j_nid)
    r6 = accept_from_list(db, i6, wrong_h, "사유", {"actor": "human", "confirm": "ACCEPT %s %s" % (i6, wrong_h)})
    ck("6_id8_mismatch_BLOCK", (not r6["applied"]) and r6["reason"] == "node_hash_mismatch")

    # 7. stale index BLOCK — 시프트 노드 삽입으로 목록 어긋남
    rows7 = list_candidates(db)["rows"]
    stale_i, stale_h = 2, rows7[1]["id8"]
    _insert_shift_node(db)
    r7 = accept_from_list(db, stale_i, stale_h, "사유",
                          {"actor": "human", "confirm": "ACCEPT %s %s" % (stale_i, stale_h)})
    ck("7_stale_index_BLOCK", (not r7["applied"]) and r7["reason"] == "node_hash_mismatch")

    # 8. confirm 불일치 / 9. auto / 10. 사유 없음 / 11. 미수용 unaccept
    rows8 = list_candidates(db)["rows"]
    i8, h8 = _idx_of(rows8, j_nid)
    r8 = accept_from_list(db, i8, h8, "사유", {"actor": "human", "confirm": "ACCEPT %s %s" % (i8 + 1, h8)})
    ck("8_confirm_불일치_BLOCK", (not r8["applied"]) and r8["reason"] == "confirm_phrase_mismatch")
    r9 = accept_from_list(db, i8, h8, "사유", {"actor": "auto", "confirm": "ACCEPT %s %s" % (i8, h8)})
    ck("9_actor_auto_BLOCK", (not r9["applied"]) and r9["reason"] == "G4_no_auto")
    r10 = accept_from_list(db, i8, h8, "  ", {"actor": "human", "confirm": "ACCEPT %s %s" % (i8, h8)})
    ck("10_사유없음_BLOCK", (not r10["applied"]) and r10["reason"] == "reason_required")
    i11, h11 = _idx_of(rows8, next(r["node_id"] for r in rows8 if r["kind"] == "개념"))
    r11 = unaccept_from_list(db, i11, h11, "사유",
                             {"actor": "human", "confirm": "UNACCEPT %s %s" % (i11, h11)})
    ck("11_미수용_unaccept_BLOCK", (not r11["applied"]) and r11["reason"] == "not_currently_accepted")

    # 12. raw 원문 저장 0 (전문 비재현) + 이벤트 행에 문장 미저장(노드 참조만)
    blob = "\n".join(str(row) for t in ("nodes", "owner_acceptances", "audit_log")
                     for row in db.con.execute("SELECT * FROM " + t))
    sent_in_events = db.con.execute(
        "SELECT count(*) FROM owner_acceptances WHERE reason LIKE '%보류한다%'").fetchone()[0]
    ck("12_raw_원문_0(전문비재현+이벤트=참조만)", T1 not in blob and sent_in_events == 0)

    # 13. audit chain INTACT / 14. confirmed 0 · promotion 0 + OpenCrab import 0
    ck("13_audit_chain_INTACT", db.verify_chain())
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0])
    ck("14_confirmed0_promotion0+opencrab_import0", bad == 0
       and not any("opencrab" in m.lower() for m in sys.modules if "mcp" in m.lower()))
    db.close()

    # 15. 운영 store 불변 + temp 정리
    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    shutil.rmtree(tmp, ignore_errors=True)
    ck("15_운영store_불변+temp_정리", op_before == op_after and not os.path.exists(tmp))

    ok = all(o for _, o in checks)
    print("-" * 78)
    print("RESULT: %d/%d PASS  candidate_row변경=0 confirmed=0 자동승격=0 opencrab=0 deploy=0" %
          (sum(1 for _, o in checks if o), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(main_selftest())
    print("usage: openbinggu_owner_accept_ux.py [--selftest]")
    print("real staging 적용은 별도 GO 필요 — 본 단계는 temp selftest만")
    sys.exit(2)
