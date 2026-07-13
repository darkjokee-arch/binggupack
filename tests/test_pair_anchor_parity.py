"""pair 저장 게이트 회귀망 — 사람 도장(save-n) 승격 + 번호축 패리티 (2026-07-13 실사용 결함 2건).

결함1(번호축): 게이트 대조는 preview 기록 모드의 후보 번호로 통과시키면서 저장(_pick_one_node)은
무조건 explicit=True 후보에서 꺼내 — 사람이 고른 번호와 다른 문장이 저장됐다(owner 실사용 발생).
결함2(pair 승격 갭): save_selected 에는 _maybe_promote_actor_by_gate 가 있는데 save_paired 에는
없어 — MCP/에이전트 세션에서 owner 가 '세이브 n' 을 쳐도 pair 는 무조건 G4 차단(owner 지적).

CLAUDECODE=1 을 명시 주입해 에이전트 세션(deny 전용)을 시뮬 — 도장만이 사람 증명인 환경에서
승격/차단을 결정적으로 검증한다(로컬/CI 환경 차이 제거).
"""
import json
import os
import re
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 비명시 preview 에서 판단 후보 2건이 나오는 대화 fixture(중간 비판단 문장 포함 — explicit
# 모드와 후보 번호축이 달라지는 구조. 실사용 결함을 재현했던 owner 원문과 동일 패턴).
CONVO = ("항상 백업 정책은 최신을 유지한다. 낡은 백업 절차를 계속 적용하지않는다. "
         "그리고 오늘 회의 내용을 그대로 옮겨 적었다(메모가 길어질 수도 있다. 팀이 달라도) "
         "임시 규칙은 모든 상황에 항상 맞을순 없다. 그렇기 때문에 담당자는 혼란이 온다.")


def _env(home):
    e = dict(os.environ)
    e.update({"BINGGU_HOME": home, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
              "CLAUDECODE": "1"})
    return e


def _run(args, env):
    return subprocess.run([sys.executable, os.path.join(ROOT, "binggu.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, cwd=ROOT, timeout=120)


def _stamp(prompt, env):
    """UserPromptSubmit 훅 실흐름 그대로 — 사람 'SAVE n(세이브 n)' 발화 도장."""
    return subprocess.run([sys.executable, os.path.join(ROOT, "hooks", "binggu_save_gate_hook.py")],
                          input=json.dumps({"hook_event_name": "UserPromptSubmit",
                                            "prompt": prompt, "cwd": "x"}),
                          capture_output=True, text=True, env=env, timeout=60)


def _make_home(tmp_path):
    home = str(tmp_path / ".binggupack")
    os.makedirs(os.path.join(home, "snapshots"), exist_ok=True)
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from openbinggu_owner_accept_ux import open_accept
    ledger = os.path.join(home, "ledger.sqlite")
    open_accept(ledger).close()
    return home, ledger


def _preview_candidates(home, env):
    """CLI preview 실행 → 표에서 후보 문장 파싱 + last_preview 기록 확인."""
    r = _run(["preview", CONVO], env)
    assert r.returncode == 0, r.stderr
    cands = re.findall(r"^\| (\d+) \| \S+ \| (.+?) \|", r.stdout, re.M)
    lp = os.path.join(home, "last_preview_candidates.json")
    assert os.path.exists(lp)
    meta = json.load(open(lp, encoding="utf-8"))
    assert len(meta["items"]) == len(cands)
    return {int(i): s.strip() for i, s in cands}


def _active_sentences(ledger):
    con = sqlite3.connect("file:%s?mode=ro" % ledger.replace("\\", "/"), uri=True)
    rows = [r[0] for r in con.execute("SELECT sentence FROM nodes WHERE state='active'")]
    con.close()
    return rows


def test_pair_human_stamp_promotes_and_number_parity(tmp_path):
    """도장('세이브 2') → pair --owner-pick 2 가 사람 승격 저장되고, 저장 문장이
    사람이 본 preview 의 2번과 byte-identical(번호축 패리티)."""
    home, ledger = _make_home(tmp_path)
    env = _env(home)
    cands = _preview_candidates(home, env)
    assert len(cands) >= 2, cands
    _stamp("세이브 2", env)
    r = _run(["--ledger", ledger, "pair", CONVO,
              "--owner-pick", "2", "--confirm", "PAIR owner:2"], env)
    assert r.returncode == 0, r.stderr
    assert "OK: 저장 1건" in r.stdout, r.stdout
    saved = _active_sentences(ledger)
    assert cands[2] in saved, (cands, saved)          # 사람이 고른 2번 그대로
    assert cands[1] not in saved                      # 다른 번호 문장 혼입 0


def test_pair_without_stamp_stays_blocked(tmp_path):
    """도장 없는 에이전트 세션 pair = G4 차단(fail-closed 불변)."""
    home, ledger = _make_home(tmp_path)
    env = _env(home)
    _preview_candidates(home, env)                    # preview 만 — 도장 0
    r = _run(["--ledger", ledger, "pair", CONVO,
              "--owner-pick", "2", "--confirm", "PAIR owner:2"], env)
    assert "G4_no_auto" in r.stdout, r.stdout
    assert _active_sentences(ledger) == []


def test_pair_ai_axis_requires_own_stamp(tmp_path):
    """paired(owner+ai)는 owner 도장만으론 승격 안 됨 — all-or-nothing(단축 승격 차단)."""
    home, ledger = _make_home(tmp_path)
    env = _env(home)
    _preview_candidates(home, env)
    _stamp("세이브 2", env)                            # owner 축 도장만
    r = _run(["--ledger", ledger, "pair", CONVO, "임시 규칙 정리가 맞다고 판단했다.",
              "--owner-pick", "2", "--ai-pick", "1",
              "--confirm", "PAIR ai_accepts owner:2 ai:1"], env)
    assert "G4_no_auto" in r.stdout, r.stdout
    assert _active_sentences(ledger) == []
