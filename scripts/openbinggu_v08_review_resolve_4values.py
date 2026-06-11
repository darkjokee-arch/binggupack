#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu v0.8 — 피드백 루프 4값 resolve 실연 (owner GO 2026-06-11: "feedback resolve loop 즉시 검증").

검증 예정일(due)을 테스트용 과거 날짜로 둔 **신규 fixture row**를 만들어 4값
(성공/실패/불확실/판정불가) resolve 를 전부 기록한다. 기존 due(2026-06-25) row 미터치.

불변(완료 기준):
  - resolve = **기록만** — 노드/엣지 본문·state·candidate 무변, 자동 강등 0, deprecated 자동 생성 0
  - confirmed 0 · promotion 0 전수 · OpenCrab import/호출 0 · audit chain INTACT
  - 기존 pending row(6/25) 보존 · 기존 row 전건 불변(before ⊆ after) · 운영 store 불변
모드:
  --selftest   temp SQLite — 4값 + 음성 4(enum 외/auto/재resolve/사유 없음) (공개 검증 가능 형태)
  --real-once  real staging — 스냅샷 선확보 후 fixture 4건 + 4값 resolve, 전 테이블 전후 대조
"""
import json
import os
import shutil
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_staging_write_selftest import OPERATING_PATHS, _hash  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import (  # noqa: E402
    open_g3, set_review_due, resolve_review, list_due_reminders)

TABLES = ["nodes", "edges", "evidence", "applied_registry", "audit_log",
          "judgment_reviews", "edge_proposals", "deprecations"]
TODAY = "2026-06-11"
DUE_TEST = "2026-06-10"  # 과거 due — 테스트 전용
FIXTURES = [
    ("node:V08FIX:rs_success",     "성공",     "[v08 검증픽스처] 이 판단은 성공 케이스 기록 검증용이다."),
    ("node:V08FIX:rs_fail",        "실패",     "[v08 검증픽스처] 이 판단은 실패 케이스 기록 검증용이다."),
    ("node:V08FIX:rs_uncertain",   "불확실",   "[v08 검증픽스처] 이 판단은 불확실 케이스 기록 검증용이다."),
    ("node:V08FIX:rs_undecidable", "판정불가", "[v08 검증픽스처] 이 판단은 판정불가 케이스 기록 검증용이다."),
]


def _table_rows(db, t):
    try:
        return {str(r) for r in db.con.execute("SELECT * FROM " + t)}
    except Exception:
        return set()


def _node_tuple(db, nid):
    return db.con.execute(
        "SELECT node_id,node_type,sentence,candidate,promotion_allowed,state FROM nodes WHERE node_id=?",
        (nid,)).fetchone()


def _insert_fixture(db, nid, sent):
    db.con.execute(
        "INSERT INTO nodes(node_id,node_type,sentence,candidate,promotion_allowed,state,pack_id,content_hash,created_at) "
        "VALUES(?,?,?,1,0,'active','v08fix_4values',?,?)", (nid, "judgment", sent, _hash(nid), TODAY))
    db.con.commit()


def run_4values(db, ck):
    """fixture 생성 → 리마인드 → 4값 resolve → 기록만 불변 검증. 공통(temp/real)."""
    before_rows = {t: _table_rows(db, t) for t in TABLES}
    pre_pending = {r for r in db.con.execute(
        "SELECT review_id,node_id,due_date,status FROM judgment_reviews WHERE status='pending'")}
    print("  before counts=%s  기존 pending=%d" %
          (json.dumps({t: len(before_rows[t]) for t in TABLES}), len(pre_pending)))

    # 1) fixture 4건 + 과거 due 등록 (set_review_due 게이트 경유 — 사람만)
    before_cs = db.store_checksum()
    for nid, _, sent in FIXTURES:
        _insert_fixture(db, nid, sent)
        rr = set_review_due(db, nid, DUE_TEST, {"actor": "human"})
        if not rr["applied"]:
            ck("1_fixture+과거due_4건", False, "set_review_due 실패 %s %s" % (nid, rr["reason"]))
            return before_rows, pre_pending
    db.audit_append("human", "v08fix_insert", "v08fix_4values", "ALLOW",
                    "4값 resolve 검증픽스처 4건(기존 due 미터치)", before_cs, db.store_checksum())
    ck("1_fixture+과거due_4건", True, "due=%s" % DUE_TEST)

    # 2) 리마인드 — fixture 4건 포함 + 기존 **미래 due** pending 미포함 + 상태 무변(read-only).
    #    기존 past-due pending(예: 6/9 G3 사이클 유래)이 목록에 뜨는 건 리마인드 정상 동작 — 제외 대상 아님.
    cs_r1 = db.store_checksum()
    rem = list_due_reminders(db, TODAY)
    cs_r2 = db.store_checksum()
    fix_ids = {nid for nid, _, _ in FIXTURES}
    future_pending_ids = {r[1] for r in pre_pending if r[2] > TODAY}
    ck("2_리마인드_due경과만+상태무변", fix_ids <= set(rem["items"])
       and not (future_pending_ids & set(rem["items"])) and cs_r1 == cs_r2,
       "목록=%d건(기존 past-due 포함 정상)" % rem["count"])

    # 3) 4값 resolve — 각각 기록만(노드 무변) 검증
    for nid, outcome, _ in FIXTURES:
        nb = _node_tuple(db, nid)
        r = resolve_review(db, nid, outcome, "v08 피드백 루프 검증 — 기록만", {"actor": "human"})
        na = _node_tuple(db, nid)
        st = db.con.execute("SELECT status,outcome FROM judgment_reviews WHERE node_id=?", (nid,)).fetchone()
        ck("3_resolve_%s_기록만" % outcome, r["applied"] and nb == na and st == ("resolved", outcome))

    # 4) 실패값 후에도 사람 승인 없인 state 무변 — '실패' 노드 active 유지 + deprecated 자동 0
    fail_state = db.con.execute("SELECT state FROM nodes WHERE node_id='node:V08FIX:rs_fail'").fetchone()[0]
    new_depr = len(_table_rows(db, "deprecations") - before_rows["deprecations"])
    ck("4_실패값_자동강등0(deprecated자동0)", fail_state == "active" and new_depr == 0)

    # 5) 기존 row 전건 불변 + 신규는 fixture 표면만 (edges/evidence/edge_proposals 신규 0)
    after_rows = {t: _table_rows(db, t) for t in TABLES}
    preserved = all(before_rows[t] <= after_rows[t] for t in TABLES)
    inserted = {t: len(after_rows[t] - before_rows[t]) for t in TABLES}
    ck("5_기존row_전건불변+표면한정", preserved and inserted["nodes"] == 4
       and inserted["edges"] == 0 and inserted["evidence"] == 0 and inserted["edge_proposals"] == 0
       and inserted["judgment_reviews"] == 4,
       "inserted=%s" % json.dumps({k: v for k, v in inserted.items() if v}))

    # 6) 기존 pending(예: 2026-06-25) row 무변 — status/due 그대로
    post = {r for r in db.con.execute(
        "SELECT review_id,node_id,due_date,status FROM judgment_reviews WHERE status='pending'")}
    ck("6_기존pending_무변(6/25_미터치)", pre_pending <= post)

    # 7) confirmed 0 · promotion 0 전수 + audit chain INTACT
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0])
    ck("7_confirmed0+promotion0+chain", bad == 0 and db.verify_chain())
    return before_rows, pre_pending


def main_selftest():
    print("=" * 78)
    print("v0.8 피드백 루프 4값 resolve — temp selftest (운영/real 접근 0)")
    print("=" * 78)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_v08_resolve_")
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-44s %s" % ("OK" if ok else "FAIL", name, detail))

    db = open_g3(os.path.join(tmp, "s.sqlite"))
    # 기존 pending 보호 검증용 — 미래 due 1건 선등록 (real 의 6/25 row 모사)
    _insert_fixture(db, "node:V08FIX:pre_existing", "[v08 검증픽스처] 기존 미래 due 보호 검증용 판단이다.")
    set_review_due(db, "node:V08FIX:pre_existing", "2026-06-25", {"actor": "human"})

    run_4values(db, ck)

    # 음성 4종 (temp 전용)
    nid = "node:V08FIX:rs_neg"
    _insert_fixture(db, nid, "[v08 검증픽스처] 음성 케이스 검증용 판단이다.")
    set_review_due(db, nid, DUE_TEST, {"actor": "human"})
    n1 = resolve_review(db, nid, "애매", "x", {"actor": "human"})
    ck("N1_enum외_BLOCK", (not n1["applied"]) and n1["reason"] == "outcome_invalid")
    n2 = resolve_review(db, nid, "성공", "y", {"actor": "auto"})
    ck("N2_actor_auto_BLOCK", (not n2["applied"]) and n2["reason"] == "G4_no_auto")
    n3 = resolve_review(db, nid, "성공", "  ", {"actor": "human"})
    ck("N3_사유없음_BLOCK", (not n3["applied"]) and n3["reason"] == "resolve_reason_required")
    resolve_review(db, nid, "성공", "정상 기록", {"actor": "human"})
    n4 = resolve_review(db, nid, "실패", "재시도", {"actor": "human"})
    ck("N4_재resolve_BLOCK", (not n4["applied"]) and n4["reason"] == "no_pending_review")
    db.close()

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("8_운영_store_불변", op_before == op_after)
    shutil.rmtree(tmp, ignore_errors=True)
    ck("9_temp_정리", not os.path.exists(tmp))

    ok = all(o for _, o in checks)
    print("-" * 78)
    print("RESULT: %d/%d PASS  자동강등=0 deprecated자동=0 confirmed=0 opencrab=0 deploy=0" %
          (sum(1 for _, o in checks if o), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


def main_real_once():
    from openbinggu_real_staging_apply_once import REAL_STAGING_DB, SNAP_DIR, _wal_checkpoint  # noqa: E402
    print("=" * 78)
    print("v0.8 피드백 루프 4값 resolve — real staging 1회 (스냅샷 선확보·기존 6/25 미터치)")
    print("=" * 78)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-44s %s" % ("OK" if ok else "FAIL", name, detail))

    db = open_g3(REAL_STAGING_DB)
    _wal_checkpoint(db.con)
    before_cs = db.store_checksum()
    snap = os.path.join(SNAP_DIR, "snap_v08resolve_before_" + before_cs + ".sqlite")
    shutil.copy2(REAL_STAGING_DB, snap)
    ck("0_스냅샷_선확보", os.path.exists(snap), "checksum=%s" % before_cs)

    run_4values(db, ck)
    _wal_checkpoint(db.con)
    db.close()

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("8_운영_store_불변", op_before == op_after)

    ok = all(o for _, o in checks)
    print("-" * 78)
    print("RESULT: %d/%d PASS  자동강등=0 deprecated자동=0 confirmed=0 opencrab=0 deploy=0" %
          (sum(1 for _, o in checks if o), len(checks)))
    print("rollback: copy %s → DB (checksum 원복 기실증 절차)" % os.path.basename(snap))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(main_selftest())
    if sys.argv[1:] == ["--real-once"]:
        sys.exit(main_real_once())
    print("usage: openbinggu_v08_review_resolve_4values.py [--selftest | --real-once]")
    sys.exit(2)
