"""StagingDB.write_lock stale lock 자동 복구 회귀 가드.

배경(codex 감사 확정 결함): 기존 write_lock 은 lock 파일 pid '문자열'만 대조해
죽은 프로세스가 남긴 lock 도 영구 차단(staging_write_locked) — 수동 os.remove 만이
해법이었다. 수정 후 계약:
  - 죽은 pid / 쓰레기 pid lock = stale → 제거 후 O_EXCL 재시도 1회(자동 복구)
  - 살아있는 타 pid lock = 기존대로 fail-closed(RuntimeError + lock 경로·pid 안내)
  - 같은 pid 재진입 허용(기존 semantics 불변)
★생존 검사는 Windows 에서 os.kill(pid,0) 금지(TerminateProcess) — OpenProcess 판정.

전부 tmp_path 격리(StagingDB 는 temp sqlite·운영 홈 0). live-pid 시나리오의
자식 프로세스는 finally 에서 반드시 kill(잔여 프로세스 0).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = str(REPO_ROOT / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from openbinggu_staging_write_selftest import (  # noqa: E402
    StagingDB,
    _pid_alive,
    base_pack,
    staging_apply,
)

DEAD_PID = 999999999  # 실존 불가 수준의 pid — dead 판정 기대


def _mk_db(tmp_path):
    db = StagingDB(str(tmp_path / "stale_lock.sqlite"))
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir(exist_ok=True)
    return db, str(snap_dir)


def _write_lock_file(db, content):
    lock = db.path + ".lock"
    with open(lock, "w") as f:
        f.write(content)
    return lock


def test_dead_pid_lock_auto_recovers(tmp_path):
    """① 죽은 pid lock → 저장 성공 + lock 자동 정리(작업 후 잔존 0)."""
    db, snap_dir = _mk_db(tmp_path)
    try:
        lock = _write_lock_file(db, str(DEAD_PID))
        assert not _pid_alive(DEAD_PID)
        res = staging_apply(db, base_pack(), {"actor": "human"}, snap_dir)
        assert res["applied"] is True
        assert not os.path.exists(lock)  # stale 제거 + 정상 해제
    finally:
        db.close()


def test_live_foreign_pid_lock_fail_closed(tmp_path):
    """② 살아있는 타 프로세스 lock → RuntimeError(메시지에 lock 경로) → 자식 종료 후 정리."""
    db, snap_dir = _mk_db(tmp_path)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        lock = _write_lock_file(db, str(child.pid))
        assert _pid_alive(child.pid)
        try:
            staging_apply(db, base_pack(), {"actor": "human"}, snap_dir)
            raise AssertionError("live foreign lock 인데 저장이 통과함 — fail-closed 회귀")
        except RuntimeError as e:
            msg = str(e)
            assert msg.startswith("staging_write_locked")
            assert lock in msg  # lock 경로 안내
            assert str(child.pid) in msg  # 점유 pid 안내
        assert os.path.exists(lock)  # 타 프로세스 lock 을 지우지 않음
    finally:
        child.kill()
        child.wait()
        db.close()
    # 자식 사망 후엔 같은 lock 이 stale 로 자동 복구되어 저장 가능
    db2 = StagingDB(str(tmp_path / "stale_lock.sqlite"))
    try:
        res = staging_apply(db2, base_pack(pack_id="p2", content="c2"), {"actor": "human"}, snap_dir)
        assert res["applied"] is True
        assert not os.path.exists(db2.path + ".lock")
    finally:
        db2.close()


def test_same_pid_reentrant_allowed(tmp_path):
    """③ 같은 pid 재진입 허용 유지 — 재진입 측은 owner 아님(해제는 바깥 holder)."""
    db, _ = _mk_db(tmp_path)
    try:
        lock = _write_lock_file(db, str(os.getpid()))
        with db.write_lock():
            pass
        assert os.path.exists(lock)  # 재진입은 non-owner — 바깥 lock 을 지우지 않음
        os.remove(lock)
    finally:
        db.close()


def test_garbage_pid_lock_treated_stale(tmp_path):
    """④ 쓰레기 pid 문자열 lock → stale 취급(자동 정리 후 저장 성공)."""
    db, snap_dir = _mk_db(tmp_path)
    try:
        lock = _write_lock_file(db, "not-a-pid")
        res = staging_apply(db, base_pack(), {"actor": "human"}, snap_dir)
        assert res["applied"] is True
        assert not os.path.exists(lock)
    finally:
        db.close()


def test_pid_alive_probe_current_process(tmp_path):
    """_pid_alive 자체 검증 — 현재 프로세스 alive·비양수 dead(TerminateProcess 부작용 0 증명)."""
    assert _pid_alive(os.getpid()) is True
    assert _pid_alive(0) is False
    assert _pid_alive(-1) is False
