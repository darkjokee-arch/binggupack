# -*- coding: utf-8 -*-
"""백업 무결성 회귀 (MF1.1) — 리더가 살아있어도 **전 행**이 백업된다.

적대검토 MF1.1 실증: 구 경로(`PRAGMA wal_checkpoint(TRUNCATE)` + `shutil.copy2`)는
다른 연결이 읽기 트랜잭션을 잡고 있으면 checkpoint 가 busy 로 실패하는데 반환값을 버려서
**무음**이고, main 파일만 복사돼 501행 중 1행짜리 사본이 만들어졌다(예외 0).
운영 ledger 는 항상 리더가 붙어 있다(MCP·capture hook·auto_pull) — 즉 가정이 아니라 상시 조건이다.

여기서는 그 조건을 **실제로 재현**(리더가 BEGIN+SELECT 를 쥔 상태)해서
  ① `safe_backup`(Online Backup API) 사본 행수 == 원본 행수
  ② 운영 호출자 `StagingDB.snapshot` 도 같은 조건에서 전 행 보존
  ③ 대조군: 구 copy2 경로는 같은 조건에서 행을 잃는다(checkpoint 가 busy 일 때만 판정)
  ④ 사본이 원본과 다르면 **조용히 통과하지 않는다**(BackupVerifyError)
"""
import os
import shutil
import sqlite3
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import binggu_schema as bs  # noqa: E402

N_ROWS = 500


def _seed_wal_db(path, n=0):
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    con.commit()
    if n:
        con.executemany("INSERT INTO t(v) VALUES(?)", [("row-%d" % i,) for i in range(n)])
        con.commit()
    return con


def _append(con, n, table="t"):
    if table == "t":
        con.executemany("INSERT INTO t(v) VALUES(?)", [("row-%d" % i,) for i in range(n)])
    else:
        con.executemany("INSERT INTO nodes(node_id,node_type,sentence) VALUES(?,?,?)",
                        [("n%d" % i, "judgment", "문장 %d" % i) for i in range(n)])
    con.commit()


class _LiveReader:
    """다른 프로세스의 상시 리더 시뮬.

    ★핵심: 리더는 **쓰기 전에** 읽기 스냅샷을 잡는다. 그래야 이후 커밋분이 WAL 에 남고
    checkpoint 가 그 지점을 넘어가지 못한다 — 운영 ledger 에서 MCP·capture hook 이
    붙어 있는 동안 저장이 일어나는 상황 그대로다.
    """

    def __init__(self, path, table="t"):
        self.con = sqlite3.connect(str(path))
        self.table = table

    def __enter__(self):
        self.con.execute("BEGIN")
        self.con.execute("SELECT count(*) FROM %s" % self.table).fetchone()
        return self.con

    def __exit__(self, *exc):
        try:
            self.con.execute("ROLLBACK")
        finally:
            self.con.close()
        return False


def _rows(path, table="t"):
    con = sqlite3.connect(str(path))
    try:
        return con.execute("SELECT count(*) FROM %s" % table).fetchone()[0]
    finally:
        con.close()


def _rows_or_zero(path, table="t"):
    try:
        return _rows(path, table)
    except sqlite3.OperationalError:
        return 0      # 테이블조차 없는 사본 = 손실 100%


# ── ① Online Backup API ─────────────────────────────────────────────────────
def test_safe_backup_keeps_all_rows_while_reader_holds_txn(tmp_path):
    src = tmp_path / "src.sqlite"
    dst = tmp_path / "snap.sqlite"
    writer = _seed_wal_db(src, n=1)
    try:
        with _LiveReader(src):
            _append(writer, N_ROWS)               # 리더 스냅샷 이후 커밋 = WAL 잔존분
            summary = bs.safe_backup(str(src), str(dst))
        assert _rows(dst) == N_ROWS + 1, "리더 점유 중 사본이 잘렸다"
        assert summary["verified"] is True
        assert summary["counts"]["t"] == N_ROWS + 1
        assert summary["mode"] in ("mode=ro", "query_only")
    finally:
        writer.close()


def test_staging_snapshot_uses_safe_backup_path(tmp_path):
    """운영 호출자(StagingDB.snapshot — staging_apply 가 매 저장마다 부른다)도 전 행 보존."""
    from openbinggu_staging_write_selftest import StagingDB

    db = StagingDB(str(tmp_path / "stg.sqlite"))
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    try:
        with _LiveReader(tmp_path / "stg.sqlite", table="nodes"):
            _append(db.con, N_ROWS, table="nodes")     # 리더 점유 중 저장
            snap = db.snapshot(str(snap_dir), "snap_reader")
        assert _rows(snap, "nodes") == N_ROWS
    finally:
        db.close()


# ── ③ 대조군: 구 copy2 경로는 같은 조건에서 행을 잃는다 ──────────────────────
def test_legacy_checkpoint_copy2_loses_rows_when_checkpoint_is_busy(tmp_path):
    src = tmp_path / "legacy_src.sqlite"
    dst = tmp_path / "legacy_snap.sqlite"
    writer = _seed_wal_db(src, n=1)
    try:
        with _LiveReader(src):
            _append(writer, N_ROWS)
            busy, log, ckpt = writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if busy == 0:
                pytest.skip("이 환경에서는 checkpoint 가 busy 로 실패하지 않음 — 대조 전제 미성립")
            shutil.copy2(str(src), str(dst))       # -wal 미복사 = 구 경로 그대로
            # 사본에 테이블 자체가 없을 수도 있다(아직 아무것도 checkpoint 되지 않은 WAL) —
            # 그것도 '조용히 잘린 백업'의 한 형태라 예외가 아니라 0행으로 계상한다.
            legacy_rows = _rows_or_zero(dst)
            safe_dst = tmp_path / "legacy_safe.sqlite"
            bs.safe_backup(str(src), str(safe_dst))
        assert legacy_rows < N_ROWS + 1, (
            "구 경로가 이 환경에서 행을 잃지 않았다(busy=%s log=%s ckpt=%s)" % (busy, log, ckpt))
        assert _rows(safe_dst) == N_ROWS + 1       # 같은 조건·같은 순간의 신 경로는 완전
    finally:
        writer.close()


# ── ④ 사본 불일치는 조용히 통과하지 않는다 ──────────────────────────────────
def test_backup_verify_rejects_short_copy(tmp_path):
    """safe_backup 내부 검증(_backup_verify)이 잘린 사본을 raise 로 표면화한다.

    safe_backup 은 정상 경로에서 반드시 이 검증을 거친다(위 테스트의 verified=True 로 확인) —
    여기서는 '사본이 원본과 어긋난 상태'를 만들어 검증기가 실제로 잡는지를 본다.
    """
    src = tmp_path / "v_src.sqlite"
    dst = tmp_path / "v_dst.sqlite"
    writer = _seed_wal_db(src, n=50)
    try:
        bs.safe_backup(str(src), str(dst))
        tampered = sqlite3.connect(str(dst))
        tampered.execute("DELETE FROM t WHERE id > 10")
        tampered.commit()
        tampered.close()

        src_con, _mode = bs._connect_source_readonly(str(src))
        try:
            with pytest.raises(bs.BackupVerifyError) as ei:
                bs._backup_verify(src_con, str(dst))
        finally:
            src_con.close()
        assert "행수" in str(ei.value)
    finally:
        writer.close()


def test_safe_backup_guards_bad_arguments(tmp_path):
    missing = tmp_path / "nope.sqlite"
    with pytest.raises(FileNotFoundError):
        bs.safe_backup(str(missing), str(tmp_path / "out.sqlite"))
    src = tmp_path / "g.sqlite"
    _seed_wal_db(src, n=3).close()
    with pytest.raises(ValueError):
        bs.safe_backup(str(src), str(src))
    dst = tmp_path / "g2.sqlite"
    bs.safe_backup(str(src), str(dst))
    with pytest.raises(FileExistsError):
        bs.safe_backup(str(src), str(dst), overwrite=False)


def test_safe_backup_does_not_write_to_source(tmp_path):
    """백업은 소스에 write 0 — 파일 size/mtime 불변(운영 ledger 를 읽기만 한다는 근거)."""
    src = tmp_path / "ro_src.sqlite"
    writer = _seed_wal_db(src, n=20)
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    writer.close()
    before = (os.path.getsize(src), os.path.getmtime(src))
    bs.safe_backup(str(src), str(tmp_path / "ro_dst.sqlite"))
    assert (os.path.getsize(src), os.path.getmtime(src)) == before
