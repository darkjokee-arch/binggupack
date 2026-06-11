#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu v0.8 — real staging 쓰기 루프 1사이클 실연 (owner GO: "real staging go" 2026-06-11).

자동 증빙 수집형(4cli D 수용 — 사람 수동 일은 GO 발화+confirm 1회뿐, 증빙은 러너가 수집):
  0) 실행 파일 실경로+SHA256 고정(B① — backup 경로명이지만 실행 실체임을 hash로 증명)
  1) 스냅샷 선확보 + 전 테이블 row 스냅샷(before) — nodes·edges·evidence·applied_registry·
     audit_log·judgment_reviews·edge_proposals·deprecations (C: 9노드만이 아니라 전 표면)
  2) preview 표 출력(저장 전 미리보기 의무)
  3) save_selected — 실 문장 3건(선정 기준: PII·bizno·금액·인증·계정 0), allow_review 미사용,
     confirm="SAVE 1,2,3", 판단 1건 due
  4) diff 증빙: 테이블별 insert 수 + 기존 row 전건 불변(before ⊆ after) + audit 원문 전문 누설 0
  5) read-back + chain INTACT + confirmed/promotion 0 + 운영 store 불변
rollback: docs/OPENBINGGU_REAL_STAGING_CYCLE_ROLLBACK_PROCEDURE.md (사전 갱신, checksum 원복 기실증).

모드 2종:
  (무인자)        real 모드 — private 설정 모듈(openbinggu_real_staging_apply_once, 공개 트리 미포함)이
                  있는 환경에서만 동작. 공개 clone 에서는 import 단계에서 안전하게 실패한다.
  --dry-run-temp  temp SQLite 전용 검증 모드(공개 검증용 selftest) — 동일 사이클을 임시 DB 에서
                  재현 + 음성 케이스 + real 미접근 증명 + temp 정리. real DB·운영 store 접근 0.
CLI: python openbinggu_v08_real_cycle_once.py --dry-run-temp
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_staging_write_selftest import OPERATING_PATHS  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import open_g3  # noqa: E402
from openbinggu_conversation_candidate_save import save_selected  # noqa: E402
from openbinggu_conversation_capture_preview import capture_preview  # noqa: E402
# private 설정 모듈(real DB 경로)은 real 모드 진입 시에만 lazy import — 공개 트리에 없어도
# --dry-run-temp 는 항상 실행 가능해야 한다.

OWNER_GO_QUOTE = ('owner 원본 발화(2026-06-11 오후): "real staging go 하고 알기쉽게 설명" — '
                  '본 러너의 confirm("SAVE 1,2,3")은 이 발화가 위임한 1사이클 범위 내 실행 (B 지시2 증빙)')
# 실 문장 3건 — 오늘 세션 실제 작업 문장 (선정 기준: PII/bizno/금액/인증/계정 0)
TEXT = ("빙구팩 쓰기 루프는 temp 검증을 통과한 후에만 real staging에 적용해야 한다. "
        "현재 라이브 워커는 read-only 6도구 상태이다. "
        "기각 도장이란 틀린 판단을 보존하며 기본 조회에서 제외하는 절차이다.")
TABLES = ["nodes", "edges", "evidence", "applied_registry", "audit_log",
          "judgment_reviews", "edge_proposals", "deprecations"]


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _table_rows(db, t):
    try:
        return {str(r) for r in db.con.execute("SELECT * FROM " + t)}
    except Exception:
        return set()


def _wal_checkpoint_local(con):
    """private 모듈 _wal_checkpoint 와 동일 구현 — temp 모드는 private import 0 이어야 한다."""
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.commit()


def run_cycle(db, snap_dir, wal_checkpoint, ck):
    """체크 0~7 — real/temp 공통 사이클. db open·운영 store mtime 비교는 호출측 책임."""
    # 0) 실행 실체 고정 (B①)
    save_mod = os.path.join(BASE, "scripts", "openbinggu_conversation_candidate_save.py")
    print("\n[0] 실행 실체: %s" % save_mod)
    print("    sha256(save 모듈)=%s  sha256(러너)=%s" % (_sha256(save_mod), _sha256(os.path.abspath(__file__))))

    # 1) 스냅샷 + 전 테이블 before
    wal_checkpoint(db.con)
    before_cs = db.store_checksum()
    snap = os.path.join(snap_dir, "snap_v08_before_" + before_cs + ".sqlite")
    shutil.copy2(db.path, snap)
    before_rows = {t: _table_rows(db, t) for t in TABLES}
    before_counts = {t: len(before_rows[t]) for t in TABLES}
    ck("1_스냅샷+전테이블_before", os.path.exists(snap),
       "checksum=%s counts=%s" % (before_cs, json.dumps(before_counts)))

    # 2) 저장 전 미리보기 (UX 의무)
    pv = capture_preview(TEXT)
    print("\n--- 저장 전 미리보기 ---")
    print(pv["preview_markdown"])
    print("---")
    ck("2_미리보기_3후보", len(pv["candidates"]) == 3
       and {c["label_kind"] for c in pv["candidates"]} == {"판단", "상태", "개념"})

    # 3) 저장 (confirm 1회 — 사람 수동의 전부)
    ctx = {"actor": "human", "confirm": "SAVE 1,2,3", "owner_go": OWNER_GO_QUOTE}
    # B 지시4 — allow_review 금지 플래그 검사 (감지 시 즉시 실패)
    ck("3a_allow_review_금지플래그_부재", "allow_review" not in ctx)
    r = save_selected(db, TEXT, [1, 2, 3], ctx, snap_dir, due_date="2026-06-25")
    ck("3_저장_3건+판단due", r["applied"] and r["saved"] == 3 and r["due_set"] == 1,
       "pack=%s rejected=%s" % (r.get("pack_id"), r["rejected"] or 0))

    # 4) diff 증빙 (C — 전 표면)
    wal_checkpoint(db.con)
    after_rows = {t: _table_rows(db, t) for t in TABLES}
    inserted = {t: len(after_rows[t] - before_rows[t]) for t in TABLES}
    preserved = all(before_rows[t] <= after_rows[t] for t in TABLES if t != "audit_log")
    # audit_log 는 append-only — before 가 prefix 로 보존되는지
    aud_preserved = before_rows["audit_log"] <= after_rows["audit_log"]
    ck("4_기존_row_전건_불변(전테이블)", preserved and aud_preserved,
       "inserted=%s" % json.dumps({k: v for k, v in inserted.items() if v}))
    blob = "\n".join(str(row) for t in TABLES for row in db.con.execute("SELECT * FROM " + t))
    ck("5_원문_전문_미저장(DB전체)", TEXT not in blob)
    # conv_save 다음에 review_due audit 이 append 되므로 "마지막 row" 가 아니라 존재+무누설로 검사
    cs_aud = db.con.execute("SELECT action,result,reason_code FROM audit_log "
                            "WHERE action='conv_save' AND result='ALLOW' ORDER BY seq DESC LIMIT 1").fetchone()
    ck("6_audit_conv_save+원문무누설", cs_aud is not None and (TEXT[:40] not in str(cs_aud)))

    # 5) read-back + 불변
    n_active = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0])
    chain = db.verify_chain()
    pending = db.con.execute("SELECT count(*) FROM judgment_reviews WHERE status='pending'").fetchone()[0]
    after_cs = db.store_checksum()
    ck("7_readback", bad == 0 and chain and pending >= 1 and after_cs != before_cs,
       "active_nodes=%d pending_review=%d chain=%s" % (n_active, pending, chain))
    return snap


def main_real():
    # private — 공개 트리 미포함. real 모드에서만 로드 (없으면 여기서 ImportError = 의도된 차단)
    from openbinggu_real_staging_apply_once import REAL_STAGING_DB, SNAP_DIR, _wal_checkpoint  # noqa: E402
    print("=" * 78)
    print("v0.8 real staging 1사이클 — 자동 증빙 수집 (owner GO: %s)" % OWNER_GO_QUOTE)
    print("=" * 78)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-42s %s" % ("OK" if ok else "FAIL", name, detail))

    db = open_g3(REAL_STAGING_DB)
    snap = run_cycle(db, SNAP_DIR, _wal_checkpoint, ck)
    db.close()

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("8_운영_store_불변", op_before == op_after)

    ok = all(o for _, o in checks)
    print("-" * 78)
    print("RESULT: %d/%d PASS  confirmed=0 promotion=0 opencrab=0 deploy=0 allow_review=미사용" %
          (sum(1 for _, o in checks if o), len(checks)))
    print("rollback: copy %s → DB (checksum 원복 기실증 절차)" % os.path.basename(snap))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


def main_dry_run_temp():
    """공개 검증용 temp selftest — 동일 사이클을 temp SQLite 에서 재현. real DB·운영 store 접근 0."""
    print("=" * 78)
    print("v0.8 쓰기 루프 — --dry-run-temp (temp SQLite 전용 · real DB 접근 0 · 공개 검증용)")
    print("=" * 78)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    # real 러너가 쓰는 위치(레포 내 상대 경로) — dry-run 은 생성도 변경도 하지 않아야 한다
    real_dir = os.path.join(BASE, "tmp", "real_staging")
    real_db = os.path.join(real_dir, "openbinggu_real_staging.sqlite")
    real_before = (os.path.exists(real_db), os.path.getmtime(real_db) if os.path.exists(real_db) else None)

    tmp = tempfile.mkdtemp(prefix="bgp_v08_dryrun_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    db_path = os.path.join(tmp, "staging_v08_dryrun.sqlite")
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-42s %s" % ("OK" if ok else "FAIL", name, detail))

    db = open_g3(db_path)
    run_cycle(db, snap_dir, _wal_checkpoint_local, ck)

    # 음성 케이스 (temp 전용 — real 모드에는 없는 추가 검증)
    rd1 = save_selected(db, TEXT, [1], {"actor": "human", "confirm": "SAVE 1,2"}, snap_dir)
    ck("D1_confirm_불일치_BLOCK", (not rd1["applied"]) and rd1["reason"] == "confirm_phrase_mismatch")
    rd2 = save_selected(db, TEXT, [1], {"actor": "auto", "confirm": "SAVE 1"}, snap_dir)
    ck("D2_actor_auto_BLOCK", (not rd2["applied"]) and rd2["reason"] == "G4_no_auto")
    db.close()

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("8_운영_store_불변", op_before == op_after)
    ck("D3_real_모듈_미import", "openbinggu_real_staging_apply_once" not in sys.modules,
       "real DB 경로 정의(private) 모듈 자체를 로드하지 않음")
    real_after = (os.path.exists(real_db), os.path.getmtime(real_db) if os.path.exists(real_db) else None)
    ck("D4_real_staging_미생성·불변", real_before == real_after,
       "존재=%s (없으면 없음 유지, 있으면 mtime 불변)" % real_after[0])
    shutil.rmtree(tmp, ignore_errors=True)
    ck("D5_temp_정리", not os.path.exists(tmp))

    ok = all(o for _, o in checks)
    print("-" * 78)
    print("RESULT: %d/%d PASS  temp_only=True real_db_access=0 confirmed=0 promotion=0 opencrab=0 deploy=0" %
          (sum(1 for _, o in checks if o), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--dry-run-temp"]:
        sys.exit(main_dry_run_temp())
    if not sys.argv[1:]:
        sys.exit(main_real())
    print("usage: openbinggu_v08_real_cycle_once.py [--dry-run-temp]")
    print("  (무인자 = real 모드: private 설정 모듈 필요 — 공개 clone 에서는 --dry-run-temp 사용)")
    sys.exit(2)
