# -*- coding: utf-8 -*-
"""binggu demo 회귀 — 60초 데모의 안전 불변식(운영 장부 미접촉·승인 전 0·승인분만 저장·회상·근거).

데모는 격리 임시 홈에서만 동작하고, 비대화형(--non-interactive)이 운영 승인을 우회하지 않는다.
"""
import os
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


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


# ===== P0.1 격리 경로 하드닝 회귀 =====

def test_demo_blocks_existing_ledger_in_home(tmp_path):
    # --home 아래에 이미 ledger.sqlite 가 있으면 BLOCK(기존 장부 재사용/오염 금지).
    dh = tmp_path / "dh_existing"
    dh.mkdir()
    (dh / "ledger.sqlite").write_bytes(b"EXISTING-SENTINEL")
    r = _run_demo(["--non-interactive", "--home", str(dh)])
    assert r.returncode == 1
    assert "BLOCK" in r.stdout
    assert (dh / "ledger.sqlite").read_bytes() == b"EXISTING-SENTINEL"  # sentinel byte 불변


def test_demo_blocks_symlink_to_operating_home(tmp_path):
    # --home 이 운영 홈을 가리키는 symlink 면 BLOCK(realpath/samefile 해소). symlink 미지원 환경은 skip.
    op = tmp_path / "op"
    op.mkdir()
    (op / "ledger.sqlite").write_bytes(b"OP-SENTINEL")
    link = tmp_path / "demolink"
    try:
        os.symlink(str(op), str(link), target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlink unsupported on this platform/privilege")
    r = _run_demo(["--non-interactive", "--home", str(link)], env={"BINGGU_HOME": str(op)})
    assert r.returncode == 1
    assert "BLOCK" in r.stdout
    assert (op / "ledger.sqlite").read_bytes() == b"OP-SENTINEL"


def test_demo_blocks_case_alias_of_operating_home(tmp_path):
    # 대소문자만 다른 동일 경로(대소문자 무시 FS: Windows/macOS 기본)면 BLOCK. 대소문자 구분 FS는 skip.
    if os.path.normcase("A") != os.path.normcase("a"):
        pytest.skip("case-sensitive filesystem")
    op = tmp_path / "OpHome"
    op.mkdir()
    (op / "ledger.sqlite").write_bytes(b"OP-SENTINEL")
    alias = str(tmp_path / "ophome")  # 대소문자 무시 FS 에서 op 와 같은 실제 폴더
    r = _run_demo(["--non-interactive", "--home", alias], env={"BINGGU_HOME": str(op)})
    assert r.returncode == 1
    assert (op / "ledger.sqlite").read_bytes() == b"OP-SENTINEL"


def _demo_args(**kw):
    class _A:
        pass
    a = _A()
    a.non_interactive = kw.get("non_interactive", True)
    a.keep = kw.get("keep", False)
    a.home = kw.get("home", None)
    return a


def test_demo_restores_binggu_home_after_success(monkeypatch, tmp_path):
    # 정상 종료 후 기존 BINGGU_HOME 복구(in-process).
    import binggu
    monkeypatch.setenv("BINGGU_HOME", "SENTINEL_HOME_VALUE")
    rc = binggu.cmd_demo(_demo_args(home=str(tmp_path / "dh_ok")))
    assert rc == 0
    assert os.environ.get("BINGGU_HOME") == "SENTINEL_HOME_VALUE"


def test_demo_restores_home_and_cleans_temp_on_exception(monkeypatch, tmp_path):
    # 예외가 나도 BINGGU_HOME 복구 + 자동 생성 임시 홈 정리(finally).
    import binggu
    import tempfile as _tf
    monkeypatch.setenv("BINGGU_HOME", "SENTINEL_HOME_VALUE")
    known = tmp_path / "known_tmp_home"
    monkeypatch.setattr(_tf, "mkdtemp", lambda *a, **k: str(known))

    def _boom(*a, **k):
        raise RuntimeError("demo boom")

    monkeypatch.setattr(binggu, "capture_preview", _boom)
    with pytest.raises(RuntimeError):
        binggu.cmd_demo(_demo_args(home=None))  # 자동 임시 홈 → created_tmp
    assert os.environ.get("BINGGU_HOME") == "SENTINEL_HOME_VALUE"  # 예외에도 복구
    assert not known.exists()  # 예외에도 임시 홈 정리


def test_same_path_helper_symlink_and_case(tmp_path):
    # canonical 경로 비교 헬퍼 단위 검증(심링크·대소문자).
    import binggu
    d = tmp_path / "Dir"
    d.mkdir()
    if os.path.normcase("A") == os.path.normcase("a"):
        assert binggu._same_path(str(d), str(tmp_path / "dir"))  # 대소문자 별칭
    link = tmp_path / "lnk"
    try:
        os.symlink(str(d), str(link), target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        return
    assert binggu._same_path(str(link), str(d))  # 심링크 → 같은 실제 대상
