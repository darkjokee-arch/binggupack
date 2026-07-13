# -*- coding: utf-8 -*-
"""restore 손상내성 — 손상 ledger 에서 traceback 없이 복구 경로가 열리는지.

적대검증 확정 결함 재현: 본체 ledger 가 sqlite 로 안 열리면(restore 가 곧 치료 경로인데)
restore dry-run/confirm 이 archive._cols 의 'file is not a database'(DatabaseError) 로
crash 하던 비대칭 방어(backup 쪽만 INVALID_BACKUP)를 고친다.

시나리오: 정상 ledger → 백업 → 본체 GARBAGE 덮어쓰기 →
  ① dry-run 이 crash 없이 corrupt 표기(-1 + current_ledger_corrupt)
  ② 정확 confirm 이 손상 파일 byte 보존(pre_restore_corrupt_) + 교체 성공
  ③ 손상 backup 은 여전히 INVALID_BACKUP (기존 방어 불변)
전부 tmp_path 격리 — 운영홈 무접촉(conftest 의 BINGGU_HOME 격리 병행).
"""
import os
import sqlite3

import pytest

from binggupack.workspace import archive


GARBAGE = b"this is not a sqlite database at all \x00\x01\x02"


@pytest.fixture
def ledger(tmp_path):
    led = str(tmp_path / "ledger.sqlite")
    conn = sqlite3.connect(led)
    conn.execute("CREATE TABLE nodes(node_id TEXT, node_type TEXT, sentence TEXT, "
                 "state TEXT, semantic_subtype TEXT, speaker TEXT, created_at TEXT, "
                 "use_count INTEGER, candidate INTEGER)")
    conn.execute("CREATE TABLE edges(edge_id TEXT, relation TEXT, source TEXT, "
                 "target TEXT, state TEXT)")
    conn.execute("INSERT INTO nodes VALUES('n1','judgment','손상내성 시험','active',"
                 "NULL,'owner','t0',0,1)")
    conn.execute("INSERT INTO edges VALUES('e1','supports','n1','n1','active')")
    conn.commit()
    conn.close()
    return led


@pytest.fixture
def backup(ledger):
    res = archive.backup_ledger(ledger)
    assert res["status"] == "OK"
    return res["out_path"]


def _corrupt(path):
    with open(path, "wb") as f:
        f.write(GARBAGE)


def test_cols_swallows_database_error(tmp_path):
    # _cols 가 OperationalError 만 잡던 갭 — 손상 파일의 DatabaseError 도 포섭
    bad = str(tmp_path / "bad.sqlite")
    _corrupt(bad)
    conn = sqlite3.connect(bad)
    try:
        assert archive._cols(conn.cursor(), "nodes") == []
    finally:
        conn.close()


def test_dry_run_on_corrupt_ledger_no_crash(ledger, backup):
    _corrupt(ledger)
    res = archive.restore_ledger(backup, ledger)
    assert res["status"] == "DRY_RUN"
    assert res["current_ledger_corrupt"] is True
    assert res["current_nodes"] == -1 and res["current_edges"] == -1
    assert res["backup_nodes"] == 1
    with open(ledger, "rb") as f:                 # write 0 — 손상 본체 그대로
        assert f.read() == GARBAGE


def test_confirm_mismatch_on_corrupt_ledger_write_zero(ledger, backup):
    _corrupt(ledger)
    res = archive.restore_ledger(backup, ledger, confirm="RESTORE wrong.sqlite")
    assert res["status"] == "CONFIRM_MISMATCH"
    with open(ledger, "rb") as f:
        assert f.read() == GARBAGE


def test_confirm_restore_preserves_bytes_and_replaces(ledger, backup):
    _corrupt(ledger)
    res = archive.restore_ledger(backup, ledger,
                                 confirm="RESTORE " + os.path.basename(backup))
    assert res["status"] == "OK" and res["nodes"] == 1
    pre = res["pre_snapshot"]
    assert pre and os.path.exists(pre)
    assert "pre_restore_corrupt_" in os.path.basename(pre)
    with open(pre, "rb") as f:                    # 손상 파일 byte 보존(복구의 복구)
        assert f.read() == GARBAGE
    data = archive.read_all(ledger)               # 복구된 ledger 정상 읽기
    assert [n["node_id"] for n in data["nodes"]] == ["n1"]


def test_healthy_ledger_flow_unchanged(ledger, backup):
    # 정상 ledger 는 기존 sqlite pre-snapshot 흐름 그대로(corrupt 표기·copy2 경로 없음)
    res = archive.restore_ledger(backup, ledger,
                                 confirm="RESTORE " + os.path.basename(backup))
    assert res["status"] == "OK"
    assert "current_ledger_corrupt" not in res
    assert os.path.basename(res["pre_snapshot"]).startswith("pre_restore_")
    assert "pre_restore_corrupt_" not in os.path.basename(res["pre_snapshot"])


def test_corrupt_backup_still_invalid(ledger, tmp_path):
    # 기존 방어(backup 쪽) 절대 불변 — 손상 backup 은 confirm 이 맞아도 교체 0
    bad = str(tmp_path / "badbackup.sqlite")
    _corrupt(bad)
    res = archive.restore_ledger(bad, ledger, confirm="RESTORE badbackup.sqlite")
    assert res["status"] == "INVALID_BACKUP"
    assert len(archive.read_all(ledger)["nodes"]) == 1
