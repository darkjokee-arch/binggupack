# -*- coding: utf-8 -*-
"""Recall→Outcome Attribution v0.1 — 합격기준 8개 + 헌법 경계 + CLI E2E.

_isolate_binggu_home(conftest autouse)로 운영홈 격리. 각 테스트는 home 을 명시 인자로 넘겨
tmp 에서만 write. subprocess CLI 도 env BINGGU_HOME 으로 격리.
"""
import os
import subprocess
import sys

from binggupack.pack import outcome_attribution as OA
from binggupack.pack import recall_trace as RT

TS = "2026-07-18T00:00:00Z"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINGGU = os.path.join(REPO, "binggu.py")


def _mk_trace(home, nodes=("node:CONV:aa01",)):
    RT.set_trace_flag(True, home=home)
    recalled = [{"node_id": n, "semantic_subtype": "교훈", "rank_score": 0.9, "relevance": 0.8}
                for n in nodes]
    return RT.record_trace("작업", "preflight", recalled, TS, home=home)["trace_id"]


def test_module_selftest_gate():
    """모듈 selftest(합격기준 8개 + PII 0 + ledger 불변) GATE=GO."""
    assert OA._selftest() == 0


def test_record_and_aggregate(tmp_path):
    home = str(tmp_path)
    tid = _mk_trace(home)
    r = OA.record_run_outcome(tid, ["node:CONV:aa01"], "applied", "success", "pytest", "d1", TS, home=home)
    assert r["recorded"] and r["trust_tier"] == "ai_observation"
    agg = OA.aggregate_run_outcomes(home)["overall"]
    # 합격기준8: 집계 필드
    assert agg["traces"] == 1 and agg["applied"] == 1 and agg["applied_success"] == 1
    assert agg["pending_traces"] == 0


def test_evidence_fail_closed(tmp_path):
    """합격기준4: evidence 없으면 기록 거부."""
    home = str(tmp_path)
    tid = _mk_trace(home)
    assert OA.record_run_outcome(tid, ["node:CONV:aa01"], "applied", "success", "pytest",
                                 "", TS, home=home)["reason"] == "evidence_required"


def test_trace_required(tmp_path):
    """합격기준3: trace 없으면 거부."""
    home = str(tmp_path)
    _mk_trace(home)  # store 생성
    assert OA.record_run_outcome("rtr-dead", ["node:CONV:aa01"], "applied", "success",
                                 "pytest", "d1", TS, home=home)["reason"] == "trace_not_found"


def test_node_subset_gate(tmp_path):
    """합격기준2: 회상된 node ID 부분집합만 연결 가능."""
    home = str(tmp_path)
    tid = _mk_trace(home)
    assert OA.record_run_outcome(tid, ["node:CONV:zz99"], "applied", "success", "pytest",
                                 "d1", TS, home=home)["reason"] == "node_not_in_trace"


def test_dup_once(tmp_path):
    """합격기준5: 같은 (trace, 증거) 반복 → 1회로 끝."""
    home = str(tmp_path)
    tid = _mk_trace(home)
    OA.record_run_outcome(tid, ["node:CONV:aa01"], "applied", "success", "pytest", "d1", TS, home=home)
    assert OA.record_run_outcome(tid, ["node:CONV:aa01"], "applied", "failure", "pytest",
                                 "d1", TS, home=home)["reason"] == "dup_outcome"


def test_enum_whitelist(tmp_path):
    home = str(tmp_path)
    tid = _mk_trace(home)
    assert OA.record_run_outcome(tid, ["node:CONV:aa01"], "zzz", "success", "pytest", "d1", TS,
                                 home=home)["reason"] == "invalid_application"
    assert OA.record_run_outcome(tid, ["node:CONV:aa01"], "applied", "great", "pytest", "d1", TS,
                                 home=home)["reason"] == "invalid_result"
    assert OA.record_run_outcome(tid, ["node:CONV:aa01"], "applied", "success", "freeform", "d1", TS,
                                 home=home)["reason"] == "invalid_evidence_kind"


def test_overturn_append_only(tmp_path):
    """합격기준7: 정정 시 원본 삭제 안 하고 reversal 이력 append."""
    home = str(tmp_path)
    tid = _mk_trace(home)
    OA.record_run_outcome(tid, ["node:CONV:aa01"], "applied", "success", "pytest", "d1", TS, home=home)
    ov = OA.overturn_run_outcome(1, TS, home=home)
    assert ov["overturned"]
    con = RT._open_store(home)
    try:
        total = con.execute("SELECT COUNT(*) FROM recall_run_outcomes").fetchone()[0]
        orig = con.execute("SELECT 1 FROM recall_run_outcomes WHERE outcome_id=?",
                           (ov["outcome_id"],)).fetchone()
    finally:
        con.close()
    assert total == 2 and orig is not None       # 원본 보존 + reversal
    assert OA.overturn_run_outcome(1, TS, home=home)["reason"] == "already_overturned"
    agg = OA.aggregate_run_outcomes(home)["overall"]
    assert agg["overturned"] == 1 and agg["applied_success"] == 0  # 정정 원본 집계 제외


def test_ledger_untouched(tmp_path):
    """합격기준6: 결과 기록이 운영 ledger(node/edge/기억) 불변."""
    home = str(tmp_path)
    ledger = os.path.join(home, "ledger.sqlite")
    with open(ledger, "wb") as f:
        f.write(b"LEDGER-SENTINEL")
    mt = os.path.getmtime(ledger)
    tid = _mk_trace(home)
    OA.record_run_outcome(tid, ["node:CONV:aa01"], "applied", "success", "pytest", "d1", TS, home=home)
    OA.overturn_run_outcome(1, TS, home=home)
    assert os.path.getmtime(ledger) == mt  # ledger sibling store 라 미접촉


def test_signal_only(tmp_path):
    """집계는 signal_only(랭킹/자동수정 진입 0) 라벨."""
    home = str(tmp_path)
    assert OA.aggregate_run_outcomes(home)["signal_only"] is True


def test_cli_e2e(tmp_path):
    """CLI: staging 자동 경로 record → 목록 → overturn."""
    home = str(tmp_path)
    tid = _mk_trace(home)
    OA.stage_last_trace(tid, ["node:CONV:aa01"], "preflight", TS, home=home)
    env = dict(os.environ, BINGGU_HOME=home)

    def run(*args):
        return subprocess.run([sys.executable, BINGGU, *args], env=env,
                              capture_output=True, text=True, cwd=REPO)

    r = run("outcome", "record", "--application", "applied", "--result", "success",
            "--evidence-kind", "pytest", "--evidence-digest", "shaX")
    assert r.returncode == 0 and "결과-귀속 기록" in r.stdout, r.stdout + r.stderr
    r2 = run("outcome")
    assert "적용=applied" in r2.stdout and "결과=success" in r2.stdout, r2.stdout
    r3 = run("outcome", "--overturn", "1")
    assert r3.returncode == 0 and "정정" in r3.stdout, r3.stdout + r3.stderr
    # 재정정 거부
    r4 = run("outcome", "--overturn", "1")
    assert r4.returncode == 1 and "already_overturned" in r4.stdout, r4.stdout
