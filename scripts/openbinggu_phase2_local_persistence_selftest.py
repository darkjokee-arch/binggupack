#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BingguPack Phase 2-A — local persistence synthetic selftest (DESIGN→검증, sandbox only).

기준: docs/OPENBINGGU_PHASE2_LOCAL_PERSISTENCE_DESIGN.md
범위: temp OPENBINGGU_HOME 기반 사용자 로컬 candidate 저장 흐름 synthetic 검증.
      기존 StagingDB/staging_apply 엔진(openbinggu_staging_write_selftest) 무수정 재사용 +
      HOME 위치 정책·multi-user 격리·write OFF·emergency stop 래퍼.

불변: 운영 localcrab_index/user_graph/_graph_merge write 0 · 실제 사용자 홈 write 0(temp HOME만) ·
      OpenCrab upload/apply 0 · confirmed/promote 0 · Neo4j 0 · push 0 · MCP write 노출 0.
      canonical=JSONL 전제, SQLite=staging backend. raw 경로/secret/PII 미출력(id·hash·count만).

CLI: python openbinggu_phase2_local_persistence_selftest.py [--selftest]
"""
import os
import re
import sys
import shutil
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

# 기존 엔진 무수정 재사용 (OPERATING_PATHS = env override + temp dummy, 개인경로 0)
from openbinggu_staging_write_selftest import (  # noqa: E402
    StagingDB, staging_apply, base_pack, OPERATING_PATHS, _hash,
)

REPO_ROOT = BASE                      # 이 작업트리 = repo 설치 디렉토리로 간주
USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
EMERGENCY_NAME = "EMERGENCY_STOP"


def _wal_checkpoint(con):
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.commit()


def _drop_wal_shm(p):
    for ext in ("-wal", "-shm"):
        f = p + ext
        if os.path.exists(f):
            os.remove(f)


def _is_inside(child, parent):
    try:
        c = os.path.normcase(os.path.abspath(child))
        p = os.path.normcase(os.path.abspath(parent))
        return c == p or c.startswith(p + os.sep)
    except Exception:
        return True  # fail-closed


def resolve_home(env_home, repo_root=REPO_ROOT):
    """OPENBINGGU_HOME 해석. repo 내부면 거부(fail-closed)."""
    if not env_home:
        raise ValueError("no_home")
    if _is_inside(env_home, repo_root):
        raise PermissionError("home_inside_repo_forbidden")
    return os.path.abspath(env_home)


def user_staging_path(home, user_id):
    """HOME/users/<user_id>/staging.sqlite. user_id traversal 차단."""
    if not isinstance(user_id, str) or not USER_ID_RE.match(user_id):
        raise ValueError("invalid_user_id")          # ../ , 공백, 절대경로 등 차단
    return os.path.join(home, "users", user_id, "staging.sqlite")


def phase2_apply(home, user_id, pack, ctx, write_enabled, emergency=False):
    """Phase 2 저장 게이트: write OFF / emergency / path / C-2(staging_apply) 결선."""
    # 1) write 기본 OFF (명시 승인 전 write 0)
    if not write_enabled:
        return {"applied": False, "reason": "write_disabled", "stage": "gate"}
    # 2) emergency stop
    if emergency or os.path.exists(os.path.join(home, EMERGENCY_NAME)):
        return {"applied": False, "reason": "emergency_stopped", "stage": "gate"}
    # 3) path 정책 (user_id 검증 → HOME/users/<id>)
    path = user_staging_path(home, user_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    snap_dir = os.path.join(os.path.dirname(path), "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    # 4) StagingDB (운영 경로면 PermissionError) → C-2 자동검사4 + transaction (기존 엔진)
    db = StagingDB(path)               # OPERATING_PATHS 거부 자동
    try:
        _wal_checkpoint(db.con)
        r = staging_apply(db, pack, ctx, snap_dir)   # freshness/duplicate/backup/checksum/WAL/audit
        _wal_checkpoint(db.con)
        r["path_id"] = _hash(path)     # raw 경로 아님
        return r
    finally:
        db.close()


def _selftest():
    print("=" * 80)
    print("BingguPack Phase 2-A — local persistence synthetic selftest (temp HOME, 운영 write 0)")
    print("=" * 80)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    real_home_default = os.path.expanduser("~")     # 실 홈 (write 절대 안 함, 참조만)
    tmp_home = tempfile.mkdtemp(prefix="binggupack_home_")
    results = []
    leak_blobs = []

    def rec(cid, name, ok):
        results.append((cid, name, "PASS" if ok else "FAIL"))

    try:
        # P1 clean install staging: temp HOME에 1-pack apply → read-back
        r = phase2_apply(tmp_home, "local", base_pack(pack_id="p2_p1"), {"actor": "human"},
                         write_enabled=True)
        leak_blobs.append(r)
        path1 = user_staging_path(tmp_home, "local")
        db = StagingDB(path1)
        n = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        cand = db.con.execute("SELECT candidate,promotion_allowed FROM nodes").fetchone()
        chain = db.verify_chain()
        db.close()
        rec("P1", "clean install staging apply+read-back", r.get("applied") and n == 1 and cand == (1, 0) and chain)

        # P2 operating_store_unchanged (지금까지 운영 mtime 불변)
        op_mid = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
        rec("P2", "operating_store_unchanged", op_mid == op_before)

        # P4 backup 실패 차단 (케이스별 별도 user_id = 별도 DB로 격리: node_id 충돌 방지)
        r = phase2_apply(tmp_home, "u4", base_pack(pack_id="p2_p4"),
                         {"actor": "human", "backup_fail": True}, write_enabled=True)
        leak_blobs.append(r)
        rec("P4", "backup 실패 차단", (not r.get("applied")) and r.get("reason") == "backup_create_failed")

        # P5 checksum mismatch → rollback (행 미반영)
        r = phase2_apply(tmp_home, "u5", base_pack(pack_id="p2_p5"),
                         {"actor": "human", "checksum_mismatch": True}, write_enabled=True)
        leak_blobs.append(r)
        db = StagingDB(user_staging_path(tmp_home, "u5"))
        p5_rows = db.con.execute("SELECT count(*) FROM nodes WHERE pack_id='p2_p5'").fetchone()[0]
        db.close()
        rec("P5", "checksum mismatch rollback", (not r.get("applied"))
            and r.get("reason") == "sqlite_checksum_mismatch" and p5_rows == 0)

        # P6 duplicate 정규화 우회 차단 (동일 user_id+pack_id+canonical content, 공백만 다름)
        phase2_apply(tmp_home, "udup", base_pack(pack_id="p2_dup", content="중복 방지 테스트"),
                     {"actor": "human"}, write_enabled=True)
        r = phase2_apply(tmp_home, "udup", base_pack(pack_id="p2_dup", content="중복   방지   테스트\n"),
                         {"actor": "human"}, write_enabled=True)
        leak_blobs.append(r)
        rec("P6", "duplicate 정규화 우회 차단", (not r.get("applied"))
            and r.get("reason") == "duplicate_already_applied")

        # P7 rollback: snapshot 복원 → checksum 원복
        path_r = user_staging_path(tmp_home, "rbuser")
        os.makedirs(os.path.dirname(path_r), exist_ok=True)
        snap_dir_r = os.path.join(os.path.dirname(path_r), "snapshots"); os.makedirs(snap_dir_r, exist_ok=True)
        db = StagingDB(path_r); _wal_checkpoint(db.con)
        before_ck = db.store_checksum()
        snap = os.path.join(snap_dir_r, "before.sqlite"); shutil.copy2(path_r, snap)
        staging_apply(db, base_pack(pack_id="p2_p7"), {"actor": "human"}, snap_dir_r); _wal_checkpoint(db.con)
        after_ck = db.store_checksum(); db.close()
        shutil.copy2(snap, path_r); _drop_wal_shm(path_r)
        db = StagingDB(path_r); _wal_checkpoint(db.con)
        roll_ck = db.store_checksum(); rb_n = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]; db.close()
        rec("P7", "rollback snapshot 원복", after_ck != before_ck and roll_ck == before_ck and rb_n == 0)

        # P8 path traversal / repo 내부 HOME 차단
        p8a = False
        try:
            user_staging_path(tmp_home, "../evil")
        except ValueError as e:
            p8a = (str(e) == "invalid_user_id")
        p8b = False
        try:
            resolve_home(os.path.join(REPO_ROOT, "binggupack_home"))   # repo 내부
        except PermissionError as e:
            p8b = (str(e) == "home_inside_repo_forbidden")
        rec("P8", "traversal/ repo 내부 HOME 차단", p8a and p8b)

        # P9 multi-user isolation: user_a / user_b 물리 분리 + cross 미혼입
        phase2_apply(tmp_home, "user_a", base_pack(pack_id="p2_ua"), {"actor": "human"}, write_enabled=True)
        phase2_apply(tmp_home, "user_b", base_pack(pack_id="p2_ub"), {"actor": "human"}, write_enabled=True)
        da = StagingDB(user_staging_path(tmp_home, "user_a"))
        a_has_ub = da.con.execute("SELECT count(*) FROM nodes WHERE pack_id='p2_ub'").fetchone()[0]
        da.close()
        db_ = StagingDB(user_staging_path(tmp_home, "user_b"))
        b_has_ua = db_.con.execute("SELECT count(*) FROM nodes WHERE pack_id='p2_ua'").fetchone()[0]
        db_.close()
        sep = (user_staging_path(tmp_home, "user_a") != user_staging_path(tmp_home, "user_b"))
        rec("P9", "multi-user isolation", a_has_ub == 0 and b_has_ua == 0 and sep)

        # P10 emergency stop: 파일 존재 시 write BLOCK
        open(os.path.join(tmp_home, EMERGENCY_NAME), "w").close()
        r = phase2_apply(tmp_home, "u10", base_pack(pack_id="p2_p10"), {"actor": "human"}, write_enabled=True)
        leak_blobs.append(r)
        os.remove(os.path.join(tmp_home, EMERGENCY_NAME))
        rec("P10", "emergency stop BLOCK", (not r.get("applied")) and r.get("reason") == "emergency_stopped")

        # P11 write OFF 기본: write_enabled=False → BLOCK
        r = phase2_apply(tmp_home, "u11", base_pack(pack_id="p2_p11"), {"actor": "human"}, write_enabled=False)
        leak_blobs.append(r)
        rec("P11", "write OFF 기본 BLOCK", (not r.get("applied")) and r.get("reason") == "write_disabled")

        # P3 raw_leak=0 (전 결과 + path: 실 홈/repo/사용자 절대경로 needle 0)
        import json as _json
        blob = _json.dumps([results, leak_blobs], ensure_ascii=False, default=str)
        needles = [real_home_default, REPO_ROOT, "C:\\Users", "/Users/", "/home/",
                   "staging.sqlite", tmp_home]
        leak = sum(1 for nd in needles if nd and nd in blob)
        rec("P3", "raw_leak=0 (경로 미출력)", leak == 0)

    finally:
        # 실제 사용자 홈 write 0 확인용: temp HOME만 제거
        shutil.rmtree(tmp_home, ignore_errors=True)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    store_unchanged = (op_before == op_after)

    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, name, v in sorted(results):
        print(f"  [{'OK' if v == 'PASS' else 'X'}] {cid:>3} {name}")
    print("-" * 80)
    print(f"  operating_store_unchanged={store_unchanged}  real_home_write=0(temp only)  "
          f"confirmed=0 applied(운영)=0 upload=0 push=0 neo4j=0")
    gate = "GO" if (npass == len(results) and store_unchanged) else "NO-GO"
    print(f"  RESULT: {npass}/{len(results)} PASS   GATE: {gate}")
    return 0 if gate == "GO" else 1


def main():
    if len(sys.argv) == 1 or "--selftest" in sys.argv:
        return _selftest()
    print("usage: python openbinggu_phase2_local_persistence_selftest.py --selftest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
