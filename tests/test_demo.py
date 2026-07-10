# -*- coding: utf-8 -*-
"""binggu demo 회귀 — 60초 데모의 안전 불변식(운영 장부 미접촉·승인 전 0·승인분만 저장·회상·근거).

데모는 격리 임시 홈에서만 동작하고, 비대화형(--non-interactive)이 운영 승인을 우회하지 않는다.
"""
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_demo(args, env=None, timeout=120):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "binggu.py"), "demo", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=e, cwd=ROOT, timeout=timeout)


def test_demo_non_interactive_runs_offline(tmp_path):
    home = str(tmp_path / "demohome")
    r = _run_demo(["--non-interactive", "--home", home, "--keep"])
    assert r.returncode == 0, r.stderr
    out = r.stdout
    # 후보 발견 + 승인 전 활성 기억 0
    assert "기억 후보를 발견" in out
    assert "현재 활성 기억: 0개" in out
    # 승인한 것만(한국어 선호) 저장 · 거절 후보 미저장
    assert "저는 앞으로 답변을 한국어로 받는 걸 선호합니다." in out
    assert "고르지 않은 후보는 저장되지 않음" in out
    # 회상 + 근거(provenance)
    assert "회상" in out
    assert "provenance" in out


def test_demo_only_approved_saved_in_ledger(tmp_path):
    home = str(tmp_path / "demohome2")
    r = _run_demo(["--non-interactive", "--home", home, "--keep"])
    assert r.returncode == 0, r.stderr
    led = os.path.join(home, "ledger.sqlite")
    assert os.path.exists(led), "데모 격리 장부가 생성돼야 함"
    con = sqlite3.connect(led)
    rows = [x[0] for x in con.execute("SELECT sentence FROM nodes WHERE state='active'")]
    con.close()
    # 승인한 1건만 저장 · 거절 후보(주간 회고)는 없음
    assert len(rows) == 1, rows
    assert "한국어" in rows[0]
    assert not any("회고" in s for s in rows)


def test_demo_does_not_touch_operating_ledger(tmp_path):
    # BINGGU_HOME 을 '운영' 홈으로 지정하고, demo 는 별도 --home 을 쓴다 → 운영 장부 불변.
    op_home = tmp_path / "ophome"
    op_home.mkdir()
    op_led = op_home / "ledger.sqlite"
    op_led.write_bytes(b"OPERATING-SENTINEL")
    before = op_led.read_bytes()
    demo_home = str(tmp_path / "demohome3")
    r = _run_demo(["--non-interactive", "--home", demo_home, "--keep"],
                  env={"BINGGU_HOME": str(op_home)})
    assert r.returncode == 0, r.stderr
    assert op_led.read_bytes() == before  # 운영 ledger 바이트 불변


def test_demo_refuses_operating_home(tmp_path):
    # demo --home 이 운영 홈과 같으면 거부(비대화형 데모가 운영 경로를 못 쓰게).
    op_home = tmp_path / "ophome2"
    op_home.mkdir()
    r = _run_demo(["--non-interactive", "--home", str(op_home)],
                  env={"BINGGU_HOME": str(op_home)})
    assert r.returncode == 1
    assert "BLOCK" in r.stdout


def test_demo_cleans_up_temp_home_by_default(tmp_path):
    # --home/--keep 없이 실행하면 임시 폴더를 스스로 정리(안내 문구).
    r = _run_demo(["--non-interactive"])
    assert r.returncode == 0, r.stderr
    assert "데모 데이터를 정리했습니다" in r.stdout
