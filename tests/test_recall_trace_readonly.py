# -*- coding: utf-8 -*-
"""추천① MF1 보강 — read-only 집계가 store(ledger + recall_trace.sqlite)를 1바이트도 안 건드림.

Core 요약(MF1): aggregate()/aggregate_run_outcomes() 의 read 경로를 _open_store_ro(mode=ro)로
전환 — apply_schema/makedirs 미경유. 이 테스트는 그 read-only 불변식을 pytest 로 못박는다:
  - 집계 전/후 recall_trace.sqlite 의 (mtime · 바이트 해시 · 사이드카 파일 집합) 완전 불변.
  - sibling ledger.sqlite(운영 장부 대역)도 집계로 미접촉.
  - _open_store_ro 커넥션이 write 를 물리적으로 거부("readonly database").
  - store 부재 시 집계가 store 파일을 생성하지 않음(순수 read 경로).

헌법 정합: 운영홈 write 0 · 자동저장 0 — 집계는 표시(read-only)일 뿐. conftest 의
_isolate_binggu_home 이 BINGGU_HOME 을 temp 로 강제하지만, 각 검증은 home 을 명시 인자로 넘겨
tmp 안에서만 write 한다(subprocess 아님 · in-process).
"""
import hashlib
import os
import sqlite3

import pytest

from binggupack.pack import outcome_attribution as OA
from binggupack.pack import recall_trace as RT

TS = "2026-07-19T00:00:00Z"


def _fingerprint(path):
    """(mtime_ns, sha256) — mtime 는 coarse-resolution 오탐 대비 보조, 해시가 본 불변식."""
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return (os.stat(path).st_mtime_ns, digest)


def _home_files(home):
    return sorted(os.listdir(home))


def _mk_trace(home, node_ids=("node:CONV:aa01", "node:CONV:bb02")):
    RT.set_trace_flag(True, home=home)
    recalled = [{"node_id": n, "semantic_subtype": "교훈", "rank_score": 0.9, "relevance": 0.8}
                for n in node_ids]
    return RT.record_trace("작업", "preflight", recalled, TS, home=home)["trace_id"]


def test_recall_trace_selftest_gate():
    """모듈 selftest(신규 'readonly 집계 후 store mtime 불변' 체크 포함) GATE=GO.

    test_outcome_attribution.test_module_selftest_gate 와 대칭 — recall_trace 는 pytest 직접
    커버리지가 없었어서 여기서 selftest 를 pytest 로 가시화한다(러너 등록 없이도 게이트가 돈다)."""
    assert RT._selftest() == 0


def test_aggregate_is_readonly_store_unchanged(tmp_path):
    """recall_trace.aggregate() 후 recall_trace.sqlite 바이트/mtime/사이드카 완전 불변."""
    home = str(tmp_path)
    tid = _mk_trace(home)
    assert RT.record_outcome(tid, "node:CONV:aa01", "used", {"actor": "human"}, TS, home=home)["recorded"]
    store = RT.trace_store_path(home)

    before_fp = _fingerprint(store)
    before_files = _home_files(home)
    agg = RT.aggregate(home=home)
    assert agg["overall"]["used"] == 1                  # 집계 자체는 정상 동작
    RT.aggregate(home=home)                             # 2회차도 write 0

    assert _fingerprint(store) == before_fp             # 바이트/mtime 불변(mode=ro)
    assert _home_files(home) == before_files            # -wal/-shm/-journal 사이드카 0


def test_aggregate_run_outcomes_is_readonly_store_unchanged(tmp_path):
    """outcome_attribution.aggregate_run_outcomes() 후 sibling store 불변(RT._open_store_ro 공유)."""
    home = str(tmp_path)
    tid = _mk_trace(home)
    r = OA.record_run_outcome(tid, ["node:CONV:aa01"], "applied", "success", "pytest",
                              "sha-abc", TS, home=home)
    assert r["recorded"]
    store = RT.trace_store_path(home)

    before_fp = _fingerprint(store)
    before_files = _home_files(home)
    agg = OA.aggregate_run_outcomes(home=home)["overall"]
    assert agg["applied"] == 1 and agg["applied_success"] == 1
    OA.aggregate_run_outcomes(home=home)

    assert _fingerprint(store) == before_fp
    assert _home_files(home) == before_files


def test_ledger_sibling_untouched_by_both_aggregations(tmp_path):
    """운영 장부(ledger.sqlite) sentinel 이 두 집계 경로로 1바이트도 안 바뀜(추천① '+ ledger')."""
    home = str(tmp_path)
    ledger = os.path.join(home, "ledger.sqlite")
    with open(ledger, "wb") as f:
        f.write(b"LEDGER-SENTINEL-DO-NOT-TOUCH")
    ledger_fp = _fingerprint(ledger)

    tid = _mk_trace(home)
    RT.record_outcome(tid, "node:CONV:aa01", "used", {"actor": "human"}, TS, home=home)
    OA.record_run_outcome(tid, ["node:CONV:bb02"], "applied", "success", "ci", "d1", TS, home=home)

    RT.aggregate(home=home)
    OA.aggregate_run_outcomes(home=home)

    assert _fingerprint(ledger) == ledger_fp            # 집계는 recall_trace.sqlite 만 연다


def test_ro_connection_physically_rejects_write(tmp_path):
    """_open_store_ro 커넥션은 write 를 물리적으로 거부(mode=ro) — INSERT 시 readonly 에러."""
    home = str(tmp_path)
    _mk_trace(home)                                     # store 생성(_open_store)
    before_files = _home_files(home)

    con = RT._open_store_ro(home)
    try:
        with pytest.raises(sqlite3.OperationalError) as ei:
            con.execute("INSERT INTO recall_traces(trace_id,kind,query_sha,ts) "
                        "VALUES('x','why_search','z',?)", (TS,))
        assert "readonly" in str(ei.value).lower()
    finally:
        con.close()

    # ro open 자체가 -wal/-shm 사이드카를 만들지 않음(write 0)
    assert _home_files(home) == before_files


def test_aggregate_on_absent_store_creates_no_file(tmp_path):
    """store 부재 시 두 집계 모두 빈 결과 + store 파일 미생성(read 경로가 store 를 안 만듦)."""
    home = str(tmp_path)
    assert not os.path.exists(RT.trace_store_path(home))

    a1 = RT.aggregate(home=home)
    a2 = OA.aggregate_run_outcomes(home=home)
    assert a1["overall"]["traces"] == 0 and a1["overall"]["usefulness_rate"] is None
    assert a2["overall"]["traces"] == 0 and a2["overall"]["applied"] == 0

    assert not os.path.exists(RT.trace_store_path(home))   # 집계가 store 를 생성하지 않음
    assert _home_files(home) == []
