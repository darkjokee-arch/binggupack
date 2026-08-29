# -*- coding: utf-8 -*-
"""롤백은 DROP 이 아니라 batch 단위 DELETE 다 (MF1.3).

`excerpt_text` 는 원본(세션로그 30일 롤링 · `_archive` 로 옮겨간 md)이 사라진 뒤
**유일하게 살아남는 사본**이 된다. 그래서
  ① 저장소 어디에도 `DROP TABLE evidence_locator|system_provenance` 가 없어야 하고
  ② 롤백은 `DELETE WHERE batch_id=?` (다른 배치 무접촉)이며
  ③ 지우기 전에 export(jsonl) 로 전량이 나와 있어야 하고(행수 대조)
  ④ 그 변화가 전용 무결성 축(locator_checksum / verify_locator_tail)에 잡혀야 한다.
"""
from pathlib import Path
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import binggu_schema as bs  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import open_g3  # noqa: E402
from openbinggu_staging_write_selftest import (  # noqa: E402
    evloc_mirror_path,
    insert_locators,
    loc_row,
    verify_locator_tail,
)

_SCAN_EXT = (".py", ".ts", ".js", ".ps1", ".sh", ".sql")
_SKIP_DIR = {".git", "__pycache__", "node_modules", ".pytest_cache", ".venv", "venv",
             "build", "dist", ".mypy_cache", ".ruff_cache"}


def _repo_sources():
    for root, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIR]
        for fn in files:
            if fn.endswith(_SCAN_EXT):
                yield os.path.join(root, fn)


def test_no_drop_table_for_evloc_tables_anywhere_in_repo():
    """DROP 은 어떤 경로·어떤 스크립트에도 없다(§6-3 '전면 금지')."""
    hits = []
    for path in _repo_sources():
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        low = text.lower()
        if "drop table" not in low:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            ll = line.lower()
            if "drop table" in ll and any(t in ll for t in bs.EVLOC_TABLES):
                # 테스트 자신의 단언 문구는 제외(이 파일)
                if os.path.abspath(path) != os.path.abspath(__file__):
                    hits.append("%s:%d" % (os.path.relpath(path, _ROOT), i))
    assert hits == [], "evloc 테이블 DROP 발견: %s" % hits


def _make(tmp_path, name):
    home = tmp_path / name
    home.mkdir(parents=True, exist_ok=True)
    with bs.evloc_env(True):
        db = open_g3(str(home / "ledger.sqlite"))
    return db


def _rows_of(batch):
    return [loc_row("EVC-%s-%d" % (batch, i), "원문 발췌 %s-%d" % (batch, i),
                    source_id="session:S-%s" % batch, locator="uuid:turn-%d" % i,
                    batch_id=batch)
            for i in range(3)]


def test_batch_delete_rollback_keeps_other_batches(tmp_path):
    db = _make(tmp_path, "evloc_rollback_home")
    try:
        r1 = insert_locators(db.con, _rows_of("B1"), db_path=db.path)
        r2 = insert_locators(db.con, _rows_of("B2"), db_path=db.path)
        assert r1["inserted"] == 3 and r2["inserted"] == 3
        assert verify_locator_tail(db.con) is True

        # ③ 지우기 전에 export — 행수 대조가 통과해야만 DELETE 로 넘어간다
        export = [dict(zip([c[0] for c in cur.description], row))
                  for cur in [db.con.execute(
                      "SELECT * FROM evidence_locator WHERE batch_id='B1'")]
                  for row in cur.fetchall()]
        target_n = db.con.execute(
            "SELECT count(*) FROM evidence_locator WHERE batch_id='B1'").fetchone()[0]
        assert len(export) == target_n == 3
        assert all(e["excerpt_text"] for e in export)         # 전문이 export 에 실려 있다

        before_ck = bs.locator_checksum(db.con)
        db.con.execute("DELETE FROM evidence_locator WHERE batch_id=?", ("B1",))
        db.con.commit()

        # ② 다른 배치는 무접촉
        assert db.con.execute(
            "SELECT count(*) FROM evidence_locator WHERE batch_id='B1'").fetchone()[0] == 0
        assert db.con.execute(
            "SELECT count(*) FROM evidence_locator WHERE batch_id='B2'").fetchone()[0] == 3
        # 테이블은 살아있다(DROP 아님) — 재적재 가능
        assert bs.has_table(db.con, "evidence_locator")
        # ④ 전용 축이 변화를 본다
        assert bs.locator_checksum(db.con) != before_ck
        assert verify_locator_tail(db.con) is False

        # export 로 복원 가능함을 실제로 보인다(유일 사본 논증의 실효성)
        restored = insert_locators(db.con, export, db_path=db.path)
        assert restored["inserted"] == 3
        assert bs.locator_checksum(db.con) == before_ck
    finally:
        db.close()


def test_mirror_jsonl_is_append_only_second_copy(tmp_path):
    """excerpt 이중 보관 — ledger 밖 jsonl 이 append-only 로 쌓인다(단일 실패점 회피)."""
    db = _make(tmp_path, "evloc_mirror2_home")
    try:
        insert_locators(db.con, _rows_of("M1"), db_path=db.path)
        path = evloc_mirror_path(db.path)
        first = Path(path).read_text(encoding='utf-8').splitlines()
        insert_locators(db.con, _rows_of("M2"), db_path=db.path)
        second = Path(path).read_text(encoding='utf-8').splitlines()
        assert second[:len(first)] == first          # 기존 줄 재작성 0
        assert len(second) == len(first) + 3
        rec = json.loads(second[-1])
        assert rec["excerpt_text"] and rec["_persisted"] is True

        # ledger 를 통째로 잃어도 원문이 남아있다
        db.con.execute("DELETE FROM evidence_locator")
        db.con.commit()
        texts = {json.loads(ln)["excerpt_text"] for ln in second}
        assert len(texts) == 6
    finally:
        db.close()


def test_insert_locators_never_raises_and_reports_reason(tmp_path):
    """적재 함수는 raise 하지 않되 **침묵하지도 않는다** — 사유를 반환한다."""
    db = _make(tmp_path, "evloc_reason_home")
    try:
        assert insert_locators(db.con, [], db_path=db.path)["reason"] == "no_rows"
        # 테이블 부재 ledger
        with bs.evloc_env(False):
            db2 = open_g3(str(tmp_path / "evloc_reason_off_home.sqlite"))
        try:
            rep = insert_locators(db2.con, _rows_of("X"), db_path=db2.path)
            assert rep["reason"] == "table_absent" and rep["skipped"] is True
            # ★ D2/D13: 기능이 꺼진 상태(table_absent)면 미러도 안 쓴다 — 꺼둔 기능의
            #   산출물이 운영홈에 쌓이면 "OFF = 기존 동작 불변" 이 깨진다(excerpt 평문·
            #   TTL/회전 없음). 이중 보관은 '테이블은 있는데 INSERT 가 실패/폐기된' 경우 전용.
            assert rep["mirrored"] == 0 and rep.get("mirror_skipped") == "table_absent"
        finally:
            db2.close()
    finally:
        db.close()
