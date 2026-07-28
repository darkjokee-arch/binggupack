# -*- coding: utf-8 -*-
"""evidence_locator 스키마 플래그 게이트 회귀 (MF1.4 · NEW1.4 · NEW2.6 · NEW2.5).

이 저장소는 hook·MCP 가 `scripts/` 를 **직접 실행**하므로 파일 편집 = 즉시 운영 배포다.
그래서 신규 스키마는 기본 OFF 여야 하고, OFF 상태에서 기존 동작이 1바이트도 변하면 안 된다.

여기서 못박는 것 4가지
  ① OFF: 생성 객체(테이블·인덱스) 집합이 v4 정본 정의와 **완전 동일**(신규 0) · user_version 4.
  ② NEW1.4: 인덱스가 **자기 테이블 없이** 등록되는 상태가 어떤 플래그에서도 성립하지 않는다.
     (binggu_schema 의 인덱스 루프는 try 가 없어서, 테이블 없이 인덱스만 등록되면
      `no such table` 이 **매 ledger open 마다** raise → capture hook·MCP·CLI 전멸)
  ③ NEW2.6: 플래그 ON 이 user_version 을 5로 올리지 않는다(미래의 정식 v5 마이그레이션이
     `5 < 5` 거짓으로 영구 skip 되는 스쿼팅 방지).
  ④ NEW2.5: write 경로 판정축인 `has_table()` 이 env 가 아니라 **ledger 실재**를 본다.
"""
import os
import sqlite3
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import binggu_schema as bs  # noqa: E402


# ── 관찰 헬퍼 (읽기 전용) ────────────────────────────────────────────────────
def _tables(con):
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _indexes(con):
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")}


def _open(path):
    con = sqlite3.connect(str(path))
    bs.apply_schema(con)
    return con


def _uv(con):
    return int(con.execute("PRAGMA user_version").fetchone()[0])


# ── ① OFF = 기존 스키마 그대로 ────────────────────────────────────────────────
def test_flag_off_creates_exactly_the_v4_canon(tmp_path):
    """OFF: 테이블·인덱스 집합이 v4 정본 정의(_TABLE_COLUMNS/_INDEXES)와 완전 동일."""
    with bs.evloc_env(False):
        con = _open(tmp_path / "off.sqlite")
        try:
            assert _tables(con) == set(bs._TABLE_COLUMNS)
            assert _indexes(con) == {name for name, _t, _c in bs._INDEXES}
            # 신규 축은 흔적조차 없다
            for t in bs.EVLOC_TABLES:
                assert not bs.has_table(con, t)
            assert not any(n.startswith("idx_evloc") or n.startswith("idx_sysprov")
                           for n in _indexes(con))
            assert _uv(con) == bs.SCHEMA_VERSION == 4
        finally:
            con.close()


def test_flag_off_reopen_is_idempotent_and_silent(tmp_path):
    """OFF: 같은 ledger 를 몇 번 열어도 예외 0 · 객체 집합 불변(매 프롬프트 hook 경로)."""
    p = tmp_path / "off_idem.sqlite"
    with bs.evloc_env(False):
        con = _open(p)
        snap = (_tables(con), _indexes(con), _uv(con))
        con.close()
        for _ in range(3):
            con = _open(p)          # raise 하면 여기서 죽는다
            assert (_tables(con), _indexes(con), _uv(con)) == snap
            con.close()


# ── ② NEW1.4: 인덱스는 자기 테이블과 반드시 같이 활성 ────────────────────────
@pytest.mark.parametrize("on", [False, True])
def test_every_active_index_has_its_table_active(on):
    """플래그 어느 쪽이든 '테이블 없는 인덱스' 조합이 성립하지 않는다(NEW1.4 근본 원인).

    이 단언이 깨지면 `CREATE INDEX ... ON <없는 테이블>` 이 apply_schema 안에서 raise 되어
    ledger 를 여는 모든 프로세스가 죽는다.
    """
    with bs.evloc_env(on):
        active_tables = set(bs._active_table_columns())
        for name, table, _cols in bs._active_indexes():
            assert table in active_tables, "인덱스 %s 의 테이블 %s 가 비활성" % (name, table)


def test_flag_off_index_list_is_the_untouched_baseline():
    """OFF 인덱스 목록은 기존 리스트 **그 자체**(추가 0) — evloc 인덱스는 ON 에서만 등장."""
    with bs.evloc_env(False):
        assert bs._active_indexes() == bs._INDEXES
        assert bs._active_table_columns() == bs._TABLE_COLUMNS
    with bs.evloc_env(True):
        on_idx = bs._active_indexes()
        assert on_idx[:len(bs._INDEXES)] == bs._INDEXES          # 기존분 순서·내용 불변
        assert {n for n, _t, _c in on_idx} - {n for n, _t, _c in bs._INDEXES} == {
            "idx_evloc_evidence", "idx_evloc_batch", "idx_sysprov_subject"}


# ── ③ NEW2.6: user_version 스쿼팅 금지 ───────────────────────────────────────
def test_flag_on_creates_tables_without_bumping_user_version(tmp_path):
    with bs.evloc_env(True):
        con = _open(tmp_path / "on.sqlite")
        try:
            for t in bs.EVLOC_TABLES:
                assert bs.has_table(con, t)
            assert {"idx_evloc_evidence", "idx_evloc_batch", "idx_sysprov_subject"} <= _indexes(con)
            # ★ 여전히 4 — 정식 v5 마이그레이션 자리를 실험 축이 차지하지 않는다
            assert _uv(con) == 4
            # v4 정본 테이블 집합은 '추가만' 됐다(제거·개명 0)
            assert set(bs._TABLE_COLUMNS) <= _tables(con)
            assert _tables(con) - set(bs._TABLE_COLUMNS) == set(bs.EVLOC_TABLES)
        finally:
            con.close()


def test_downgrade_on_to_off_is_silent_and_non_destructive(tmp_path):
    """ON 으로 만든 ledger 를 OFF 프로세스(구 설치본 시뮬)가 열어도 예외 0 · 데이터 무접촉."""
    p = tmp_path / "down.sqlite"
    with bs.evloc_env(True):
        con = _open(p)
        con.execute("INSERT INTO evidence_locator(loc_id,evidence_id,excerpt_text)"
                    " VALUES('L1','EVC-1','원문 발췌')")
        con.execute("INSERT INTO system_provenance(prov_id,subject_kind,subject_id)"
                    " VALUES('P1','node','node:1')")
        con.commit()
        before = bs.locator_checksum(con)
        probe_before = bs.integrity_probe(con)
        con.close()

    with bs.evloc_env(False):
        con = _open(p)              # 예외 0 이어야 한다
        try:
            assert bs.has_table(con, "evidence_locator")           # 남아있되 무해
            assert con.execute("SELECT count(*) FROM evidence_locator").fetchone()[0] == 1
            assert bs.locator_checksum(con) == before              # 행 무접촉
            assert bs.integrity_probe(con) == probe_before
            assert _uv(con) == 4
        finally:
            con.close()


# ── ④ NEW2.5: write 판정은 env 가 아니라 ledger 실재 ─────────────────────────
def test_has_table_is_env_independent(tmp_path):
    p = tmp_path / "envindep.sqlite"
    with bs.evloc_env(True):
        con = _open(p)
        con.close()
    # env 를 꺼도 '테이블이 있다'는 사실은 그대로여야 한다(hook·MCP·CLI 결론 통일)
    with bs.evloc_env(False):
        con = sqlite3.connect(str(p))
        try:
            assert bs.has_table(con, "evidence_locator") is True
            assert bs.table_columns(con, "evidence_locator")[0] == "loc_id"
            assert bs.has_table(con, "no_such_table_here") is False
            assert bs.table_columns(con, "no_such_table_here") == []
        finally:
            con.close()


# ── fail-open 봉인: 스키마 불일치 선재 테이블 ────────────────────────────────
def test_post_apply_raises_on_schema_mismatch_and_leaves_no_index(tmp_path):
    """동명·이스키마 테이블이 먼저 있으면(CREATE IF NOT EXISTS 는 no-op) 사유와 함께 raise.

    이때 '잘못된 테이블에 인덱스만 걸린' 상태가 남으면 안 된다(NEW1.4 재발 방지).
    """
    p = tmp_path / "mismatch.sqlite"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE evidence_locator(wrong_col TEXT)")
    con.commit()
    con.close()

    with bs.evloc_env(True):
        con = sqlite3.connect(str(p))
        try:
            with pytest.raises(bs.EvlocSchemaError) as ei:
                bs.apply_schema(con)
            assert "evidence_locator" in str(ei.value)      # 사유가 담겨 있다(침묵 금지)
            assert not any(n.startswith("idx_evloc") for n in _indexes(con))
        finally:
            con.close()


def test_flag_env_name_and_restore(monkeypatch):
    """플래그 이름 정본 + evloc_env 가 종료 시 원복(테스트 오염 방지)."""
    assert bs.EVLOC_FLAG_ENV == "BINGGU_EVLOC_V5"
    monkeypatch.setenv(bs.EVLOC_FLAG_ENV, "1")
    with bs.evloc_env(False):
        assert bs.evloc_enabled() is False
    assert bs.evloc_enabled() is True
    monkeypatch.delenv(bs.EVLOC_FLAG_ENV)
    with bs.evloc_env(True):
        assert bs.evloc_enabled() is True
    assert os.environ.get(bs.EVLOC_FLAG_ENV) is None
