# -*- coding: utf-8 -*-
"""추천① MF2/MF3 보강 — `binggu status` 지능 루프 롤업이 상태별로 올바른 문구를 낸다.

CLI E2E(subprocess · env BINGGU_HOME + --ledger 로 tmp 격리). 검증 대상:
  - 히트 축(MF2): use_count 롤업이 1 SELECT 로 통합돼 실 use_count 를 반영.
  - 사람판정 축(MF3): trace OFF(잠김) / trace ON·N=0(고장 아님 힌트) / N>0(used 비율) 분기.
  - 결과-귀속(별도 축 · signal_only) 라인.
  - golden_drift 재검토 '후보' 표시가 '자동변경 0'(owner: 제안만 · 사람이 raw 확인 후 도장)임을 명시.

롤업은 read-only 표시일 뿐 — status 는 항상 rc=0(집계 실패해도 불사). 모든 케이스에서 rc==0 확인.
"""
import os
import subprocess
import sys

from binggupack.pack import recall_trace as RT

TS = "2026-07-19T00:00:00Z"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINGGU = os.path.join(REPO, "binggu.py")

HEADER = "지능 루프(read-only · 표시일 뿐 · 규칙 자동변경 0):"
RESULT_ATTR = "결과-귀속(signal_only · 인과 아님):"


def _run(home, *args):
    """--ledger 는 전역 인자라 서브커맨드 앞에 온다: binggu --ledger <p> <cmd>."""
    ledger = os.path.join(home, "ledger.sqlite")
    env = dict(os.environ, BINGGU_HOME=home)
    return subprocess.run([sys.executable, BINGGU, "--ledger", ledger, *args],
                          env=env, capture_output=True, text=True, cwd=REPO, timeout=120)


def _init(home):
    r = _run(home, "init")
    assert r.returncode == 0, r.stdout + r.stderr
    return os.path.join(home, "ledger.sqlite")


def test_status_trace_off_shows_locked(tmp_path):
    """trace OFF(기본) → 사람판정 축이 '§25 owner 게이트 · 잠김'으로 가시화(축적 0 이유 명시)."""
    home = str(tmp_path)
    _init(home)
    r = _run(home, "status")
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert HEADER in out
    assert "히트(수동 recall --record 신호): use_count 합 0 · 히트 노드 0개" in out
    assert "회상 trace OFF(§25 owner 게이트 · 잠김)" in out
    assert "trace enable" in out                        # 켜는 액션 힌트
    assert "0건 (고장 아님" not in out                  # 잠김 분기 → N=0 분기와 배타


def test_status_trace_on_n_zero(tmp_path):
    """trace ON 인데 판정 0건 → '고장 아님'(도장/판정 액션 힌트) + 결과-귀속 라인(전부 0)."""
    home = str(tmp_path)
    _init(home)
    RT.set_trace_flag(True, home=home)
    r = _run(home, "status")
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert HEADER in out
    assert "사람판정(used): 0건 (고장 아님 — 도장:" in out
    assert "회상 trace OFF" not in out                  # ON 이므로 잠김 문구 없음
    assert RESULT_ATTR in out
    assert "trace 0 · 적용 0(성공 0/실패 0) · 미결 0" in out


def test_status_trace_on_with_human_outcome(tmp_path):
    """trace ON + 사람 판정(used) 1건 → usefulness 100% + 결과-귀속 미결 카운트."""
    home = str(tmp_path)
    _init(home)
    RT.set_trace_flag(True, home=home)
    tid = RT.record_trace(
        "작업", "preflight",
        [{"node_id": "node:CONV:aa01", "semantic_subtype": "교훈", "rank_score": 0.9, "relevance": 0.8}],
        TS, home=home)["trace_id"]
    assert RT.record_outcome(tid, "node:CONV:aa01", "used", {"actor": "human"}, TS, home=home)["recorded"]

    r = _run(home, "status")
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "사람판정(used): used 1/1(100%) · ignored 0 · corrected 0" in out
    # 회상 trace 는 1건 존재하나 결과-귀속(run outcome)은 미연결 → 미결 1
    assert RESULT_ATTR in out
    assert "trace 1 · 적용 0(성공 0/실패 0) · 미결 1" in out


def test_status_hit_rollup_reflects_use_count(tmp_path):
    """MF2: use_count 롤업이 실제 노드 use_count 합/히트 노드 수를 반영(1 SELECT 통합)."""
    import sqlite3
    home = str(tmp_path)
    ledger = _init(home)
    con = sqlite3.connect(ledger)
    try:
        con.execute(
            "INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
            "created_at,semantic_subtype,use_count) VALUES"
            "('node:CONV:hit1','judgment','x',0,'active','h',?, '교훈',7)", (TS,))
        con.commit()
    finally:
        con.close()

    r = _run(home, "status")
    assert r.returncode == 0, r.stdout + r.stderr
    assert HEADER in r.stdout
    assert "히트(수동 recall --record 신호): use_count 합 7 · 히트 노드 1개" in r.stdout


def test_status_golden_drift_is_review_candidate_not_auto(tmp_path):
    """golden_drift 는 재검토 '후보'로만 표시 — '자동변경 0'(owner: 사람이 raw 확인 후 도장)."""
    home = str(tmp_path)
    _init(home)
    RT.set_trace_flag(True, home=home)
    # 같은 노드 bb02 를 서로 다른 3 trace 에서 ignored/ignored/corrected → 표본 N=3·bad 1.0 → 후보
    for i, verdict in enumerate(["ignored", "ignored", "corrected"]):
        tid = RT.record_trace(
            "다른 작업 %d" % i, "why_search",
            [{"node_id": "node:CONV:bb02", "semantic_subtype": "교훈", "rank_score": 0.5, "relevance": 0.4}],
            TS, home=home)["trace_id"]
        RT.record_outcome(tid, "node:CONV:bb02", verdict, {"actor": "human"}, TS, home=home)

    r = _run(home, "status")
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "사람판정(used): used 0/3(0%) · ignored 2 · corrected 1" in out
    assert "재검토 후보 1건(golden_drift" in out
    assert "자동변경 0" in out                          # 규칙 자기수정 금지(owner 원칙)
