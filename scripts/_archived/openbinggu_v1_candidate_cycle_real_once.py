# -*- coding: utf-8 -*-
"""OpenBinggu v1.0 — 후보 관리 1사이클 실연 (owner GO ③ 2026-06-11, 13/13 GO 기실연).

흐름: 보기(candidate_list) → 기각(DEPRECATE <n> <id8>) → 수정(REPLACE <n> <id8> WITH) →
수용(ACCEPT <n> <id8>) → 철회(UNACCEPT <n> <id8>) → 피드백(set_review_due + resolve 성공).

조건(owner 12): 스냅샷 선확보 · 전후 대조 · fixture 최소(저장 2건) · 기존 예약 row 미터치 ·
raw 0 · confirmed 0 · OpenCrab 0 · nodes 기존 row 직접 변경 0 · candidate-only ·
chain INTACT · rollback — **종료 시 스냅샷 원복 실행으로 실증 + 장부 무오염**(증적은 출력/기록).

모드 2종 (v0.8.1 공개 검증성 기준):
  (무인자)        real 모드 — private 설정 모듈(공개 트리 미포함) 환경에서만. 공개 clone 에서는
                  import 단계에서 안전 실패.
  --dry-run-temp  temp SQLite 전용 — 시드(기존 데이터·미래 due 예약 모사) 후 동일 사이클 재현
                  + private 미접근 증명 + temp 정리. real DB·운영 store 접근 0.
CLI: python openbinggu_v1_candidate_cycle_real_once.py --dry-run-temp
"""
import json
import os
import shutil
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_staging_write_selftest import OPERATING_PATHS  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import set_review_due, resolve_review  # noqa: E402
from openbinggu_candidate_list_view import list_candidates  # noqa: E402
from openbinggu_conversation_candidate_save import save_selected  # noqa: E402
from openbinggu_candidate_deprecate_ux import deprecate_from_list  # noqa: E402
from openbinggu_candidate_replace_ux import replace_from_list  # noqa: E402
from openbinggu_owner_accept_ux import (  # noqa: E402
    open_accept, accept_from_list, unaccept_from_list, accepted_view)
# private 설정 모듈(real DB 경로)은 real 모드에서만 lazy import — 공개 clone 에서 --dry-run-temp 상시 실행 가능.

TABLES = ["nodes", "edges", "evidence", "applied_registry", "audit_log",
          "judgment_reviews", "edge_proposals", "deprecations"]
FIX_TEXT = ("이 실연 후보 판단은 마진 검토가 끝날 때까지 보류한다. "
            "후보 관리 일사이클 실연 절차가 진행 중이다.")
NEW_SENT = "재검토 결과 이 실연 후보 판단은 조건부로 진행한다."
SEED_TEXT = "이 시드 판단은 미래 검증 예정 상태로 보류한다."   # temp 시드 — 미래 due 예약 모사


def _rows(db, t):
    try:
        return {str(r) for r in db.con.execute("SELECT * FROM " + t)}
    except Exception:
        return set()


def _idx_of(rows, nid):
    for i, r in enumerate(rows, 1):
        if r["node_id"] == nid:
            return i, r["id8"]
    return None, None


def _wal_checkpoint_local(con):
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.commit()


def run_cycle(db_path, snap_dir, wal_checkpoint, ck, min_rows):
    """체크 0~12 — real/temp 공통. 종료 시 스냅샷 원복(무오염)까지 포함."""
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    db = open_accept(db_path)
    wal_checkpoint(db.con)
    before_cs = db.store_checksum()
    snap = os.path.join(snap_dir, "snap_v1cycle_before_" + before_cs + ".sqlite")
    shutil.copy2(db_path, snap)
    before_rows = {t: _rows(db, t) for t in TABLES}
    pre_pending = {r for r in db.con.execute(
        "SELECT review_id,node_id,due_date,status FROM judgment_reviews WHERE status='pending'")}
    ck("0_스냅샷+before", os.path.exists(snap), "checksum=%s counts=%s 기존pending=%d" % (
        before_cs, json.dumps({t: len(before_rows[t]) for t in TABLES}), len(pre_pending)))

    # 1. 보기 — read-only (checksum 동일)
    cs1a = db.store_checksum()
    v1 = list_candidates(db)
    cs1b = db.store_checksum()
    ck("1_보기(read-only)", len(v1["rows"]) >= min_rows and cs1a == cs1b, "목록=%d건" % len(v1["rows"]))

    # 2. fixture 최소 2건 저장 (판단+상태)
    r2 = save_selected(db, FIX_TEXT, [1, 2], {"actor": "human", "confirm": "SAVE 1,2"}, snap_dir)
    rows2 = list_candidates(db)["rows"]
    fixA = next((r["node_id"] for r in rows2 if "보류한다" in r["sentence"] and "마진 검토" in r["sentence"]), None)
    fixB = next((r["node_id"] for r in rows2 if "진행 중" in r["sentence"] and "실연" in r["sentence"]), None)
    ck("2_fixture_저장_2건", r2["applied"] and r2["saved"] == 2 and bool(fixA and fixB))

    # 3. 기각 — DEPRECATE <n> <id8> (상태 fixture)
    iB, hB = _idx_of(rows2, fixB)
    r3 = deprecate_from_list(db, iB, hB, "실연 — 기각 도장 검증",
                             {"actor": "human", "confirm": "DEPRECATE %s %s" % (iB, hB)}, snap_dir)
    stB = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (fixB,)).fetchone()[0]
    ck("3_기각_DEPRECATE", r3["applied"] and r3["node_id"] == fixB and stB == "deprecated")

    # 4. 수정 — REPLACE <n> <id8> WITH (판단 fixture → 신규 판단)
    rows4 = list_candidates(db)["rows"]
    iA, hA = _idx_of(rows4, fixA)
    r4 = replace_from_list(db, iA, hA, NEW_SENT, "실연 — 수정 묶음 검증",
                           {"actor": "human",
                            "confirm": "REPLACE %s %s WITH %s" % (iA, hA, NEW_SENT)}, snap_dir)
    newA = r4.get("new_node_id")
    stA = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (fixA,)).fetchone()[0]
    dep = db.con.execute("SELECT reason FROM deprecations WHERE item_id=?", (fixA,)).fetchone()
    nA = db.con.execute("SELECT state,candidate,promotion_allowed FROM nodes WHERE node_id=?",
                        (newA,)).fetchone() if newA else None
    ck("4_수정_REPLACE(기각+신규+역링크)", r4["applied"] and stA == "deprecated"
       and dep is not None and dep[0].startswith("replaced_by:%s|" % newA) and nA == ("active", 1, 0))

    # 5. 수용 — ACCEPT <n> <id8> (신규 노드, row 무변)
    rows5 = list_candidates(db)["rows"]
    iN, hN = _idx_of(rows5, newA)
    nb5 = db.con.execute("SELECT * FROM nodes WHERE node_id=?", (newA,)).fetchone()
    r5 = accept_from_list(db, iN, hN, "실연 — 수용 기록",
                          {"actor": "human", "confirm": "ACCEPT %s %s" % (iN, hN)})
    na5 = db.con.execute("SELECT * FROM nodes WHERE node_id=?", (newA,)).fetchone()
    ck("5_수용_ACCEPT(row무변)", r5["applied"] and nb5 == na5 and accepted_view(db).get(newA) is True)

    # 6. 철회 — UNACCEPT (보존형 event)
    r6 = unaccept_from_list(db, iN, hN, "실연 — 철회 보존형",
                            {"actor": "human", "confirm": "UNACCEPT %s %s" % (iN, hN)})
    evn = db.con.execute("SELECT count(*) FROM owner_acceptances WHERE node_id=?", (newA,)).fetchone()[0]
    ck("6_철회_UNACCEPT(보존형_event2)", r6["applied"] and evn == 2 and newA not in accepted_view(db))

    # 7. 피드백 — 기존 resolve runner 연결 (due 등록 → 성공 resolve, 기록만)
    rd = set_review_due(db, newA, "2026-06-10", {"actor": "human"})
    rr = resolve_review(db, newA, "성공", "실연 — 피드백 1값 검증", {"actor": "human"})
    st7 = db.con.execute("SELECT status,outcome FROM judgment_reviews WHERE node_id=?", (newA,)).fetchone()
    nA7 = db.con.execute("SELECT state,candidate FROM nodes WHERE node_id=?", (newA,)).fetchone()
    ck("7_피드백_resolve성공(기록만)", rd["applied"] and rr["applied"]
       and st7 == ("resolved", "성공") and nA7 == ("active", 1))

    # 8. 기존 row 전건 불변 + 기존 예약 무변 + 신규 표면 한정
    after_rows = {t: _rows(db, t) for t in TABLES}
    preserved = all(before_rows[t] <= after_rows[t] for t in TABLES)
    post_pending = {r for r in db.con.execute(
        "SELECT review_id,node_id,due_date,status FROM judgment_reviews WHERE status='pending'")}
    inserted = {t: len(after_rows[t] - before_rows[t]) for t in TABLES}
    ck("8_기존row_불변+기존예약_미터치", preserved and pre_pending <= post_pending,
       "inserted=%s" % json.dumps({k: v for k, v in inserted.items() if v}))

    # 9. raw 0 + confirmed 0 + candidate-only
    blob = "\n".join(str(row) for t in TABLES for row in db.con.execute("SELECT * FROM " + t))
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0])
    ck("9_raw0+confirmed0+candidate-only", FIX_TEXT not in blob and bad == 0)

    # 10. audit chain
    ck("10_audit_chain_INTACT", db.verify_chain())

    # 11. 운영 store 불변
    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("11_운영_store_불변", op_before == op_after)

    # 12. 원복 — 장부 무오염 (rollback 실증 겸용) + 기존 예약 생존 재확인
    wal_checkpoint(db.con)
    db.con.close()
    for ext in ("-wal", "-shm"):
        p = db_path + ext
        if os.path.exists(p):
            os.remove(p)
    shutil.copy2(snap, db_path)
    db2 = open_accept(db_path)
    rest_cs = db2.store_checksum()
    fix_left = db2.con.execute(
        "SELECT count(*) FROM nodes WHERE sentence LIKE '%실연%'").fetchone()[0]
    pend2 = {r for r in db2.con.execute(
        "SELECT review_id,node_id,due_date,status FROM judgment_reviews WHERE status='pending'")}
    chain2 = db2.verify_chain()
    db2.close()
    ck("12_원복(rollback실증+무오염+기존예약생존)", rest_cs == before_cs and fix_left == 0
       and pend2 == pre_pending and chain2, "원복 checksum=%s" % rest_cs)


def _print_result(checks):
    ok = all(o for _, o in checks)
    print("-" * 78)
    print("RESULT: %d/%d PASS  confirmed=0 opencrab=0 deploy=0 hosted=0 — 증적은 본 출력/기록, DB는 원복" %
          (sum(1 for _, o in checks if o), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


def main_real():
    from openbinggu_real_staging_apply_once import REAL_STAGING_DB, SNAP_DIR, _wal_checkpoint  # noqa: E402  private
    print("=" * 78)
    print("v1.0 후보 관리 1사이클 — real staging 실연 (스냅샷 선확보·종료 시 원복)")
    print("=" * 78)
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-52s %s" % ("OK" if ok else "FAIL", name, detail))

    run_cycle(REAL_STAGING_DB, SNAP_DIR, _wal_checkpoint, ck, min_rows=12)
    return _print_result(checks)


def main_dry_run_temp():
    """공개 검증용 — temp SQLite 시드(기존 후보 1 + 미래 due 예약 모사) 후 동일 사이클 재현."""
    print("=" * 78)
    print("v1.0 후보 관리 1사이클 — --dry-run-temp (temp 전용 · real DB 접근 0 · 공개 검증용)")
    print("=" * 78)
    real_dir = os.path.join(BASE, "tmp", "real_staging")
    real_db = os.path.join(real_dir, "openbinggu_real_staging.sqlite")
    real_before = (os.path.exists(real_db), os.path.getmtime(real_db) if os.path.exists(real_db) else None)
    tmp = tempfile.mkdtemp(prefix="bgp_v1cycle_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    db_path = os.path.join(tmp, "staging_v1cycle.sqlite")
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-52s %s" % ("OK" if ok else "FAIL", name, detail))

    # 시드 — real 의 "기존 데이터 + 미래 due 예약(6/25 모사)" 동형 재현
    db = open_accept(db_path)
    rs = save_selected(db, SEED_TEXT, [1], {"actor": "human", "confirm": "SAVE 1"},
                       snap_dir, due_date="2026-06-25")
    db.close()
    ck("S_시드(기존후보1+미래due예약)", rs["applied"] and rs["saved"] == 1 and rs["due_set"] == 1)

    run_cycle(db_path, snap_dir, _wal_checkpoint_local, ck, min_rows=1)

    # temp 전용 증명 3종 (v0.8.1 --dry-run-temp 기준)
    ck("D1_real_모듈_미import", "openbinggu_real_staging_apply_once" not in sys.modules,
       "real DB 경로 정의(private) 모듈 자체를 로드하지 않음")
    real_after = (os.path.exists(real_db), os.path.getmtime(real_db) if os.path.exists(real_db) else None)
    ck("D2_real_staging_미생성·불변", real_before == real_after, "존재=%s" % real_after[0])
    shutil.rmtree(tmp, ignore_errors=True)
    ck("D3_temp_정리", not os.path.exists(tmp))
    return _print_result(checks)


if __name__ == "__main__":
    if sys.argv[1:] == ["--dry-run-temp"]:
        sys.exit(main_dry_run_temp())
    if not sys.argv[1:]:
        sys.exit(main_real())
    print("usage: openbinggu_v1_candidate_cycle_real_once.py [--dry-run-temp]")
    print("  (무인자 = real 모드: private 설정 모듈 필요 — 공개 clone 에서는 --dry-run-temp 사용)")
    sys.exit(2)
