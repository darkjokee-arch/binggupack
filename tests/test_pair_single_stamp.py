"""pair 결합 번호축(도장 1회) + learn-consume 도장 소비 — 2026-07-13 owner "같이 프리뷰 주면 해결".

기존 흐름은 축(owner/ai)마다 preview 스테이징+'세이브 n' 도장이 따로 필요해 pair 1건에
사람 도장 2회 · 에이전트 세션 learn-consume 은 아예 불가(별도 터미널행)였다. 개선:
  - pair --confirm 생략 = owner+ai 후보를 한 preview(연속 번호: owner 1..N · ai N+1..)로
    스테이징 → 사람 도장 1회('세이브 o,a')로 양축 ref 가 함께 기록 → 승격.
  - learn-consume dry-run 이 큐 발화 원문을 preview 로 스테이징 → 도장 1회('세이브 qi+1')로
    에이전트 세션에서도 소비. 도장=사람 키보드만 원칙·fail-closed 는 그대로(완화 0).
CLAUDECODE=1 주입으로 에이전트 세션(deny 전용)을 시뮬 — 도장만이 사람 증명.
"""
from pathlib import Path
import json
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OWNER_TEXT = "빙구팩은 개인 지식을 팩으로 만드는 도구라고 본다"
AI_TEXT = "그 정의를 빙구팩 정체성 기준으로 수용한다"


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


def _active_sentences(ledger):
    con = sqlite3.connect("file:%s?mode=ro" % ledger.replace("\\", "/"), uri=True)
    rows = [r[0] for r in con.execute("SELECT sentence FROM nodes WHERE state='active'")]
    con.close()
    return rows


def _write_queue(home, entries):
    state = os.path.join(home, "state")
    os.makedirs(state, exist_ok=True)
    qp = os.path.join(state, "learn_outcome_queue.jsonl")
    with open(qp, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return qp


def test_pair_combined_preview_single_stamp(tmp_path):
    """--confirm 생략 preview = 결합 번호축 스테이징 → '세이브 1,2' 도장 1회 → pair 저장 2건."""
    home, ledger = _make_home(tmp_path)
    env = _env(home)
    r = _run(["--ledger", ledger, "pair", OWNER_TEXT, AI_TEXT], env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "결합 번호축" in r.stdout
    assert "| 1 | owner |" in r.stdout and "| 2 | ai |" in r.stdout, r.stdout
    lp = json.loads(Path(os.path.join(home, 'last_preview_candidates.json')).read_text(encoding='utf-8'))
    assert lp["explicit"] is True and len(lp["items"]) == 2
    assert OWNER_TEXT not in json.dumps(lp, ensure_ascii=False)   # 원문 미저장(hash 만)
    assert _active_sentences(ledger) == []                        # preview 는 ledger 미접촉
    _stamp("세이브 1,2", env)                                      # 사람 도장 1회
    r2 = _run(["--ledger", ledger, "pair", OWNER_TEXT, AI_TEXT,
               "--confirm", "PAIR ai_accepts owner:1 ai:1"], env)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "OK: 저장 2건" in r2.stdout, r2.stdout
    saved = _active_sentences(ledger)
    assert OWNER_TEXT in saved and AI_TEXT in saved


def test_pair_combined_preview_without_stamp_blocked(tmp_path):
    """결합 preview 만(도장 0) = 에이전트 세션 G4 차단 불변(fail-closed)."""
    home, ledger = _make_home(tmp_path)
    env = _env(home)
    _run(["--ledger", ledger, "pair", OWNER_TEXT, AI_TEXT], env)   # preview 만
    r = _run(["--ledger", ledger, "pair", OWNER_TEXT, AI_TEXT,
              "--confirm", "PAIR ai_accepts owner:1 ai:1"], env)
    assert "G4_no_auto" in r.stdout, r.stdout
    assert _active_sentences(ledger) == []


def test_pair_partial_stamp_blocked(tmp_path):
    """결합 preview 에 owner 번호만 도장('세이브 1') = all-or-nothing 차단(ai 축 미도장)."""
    home, ledger = _make_home(tmp_path)
    env = _env(home)
    _run(["--ledger", ledger, "pair", OWNER_TEXT, AI_TEXT], env)
    _stamp("세이브 1", env)                                        # ai 축(2번) 미도장
    r = _run(["--ledger", ledger, "pair", OWNER_TEXT, AI_TEXT,
              "--confirm", "PAIR ai_accepts owner:1 ai:1"], env)
    assert "G4_no_auto" in r.stdout, r.stdout
    assert _active_sentences(ledger) == []


def test_learn_consume_stamp_promotes(tmp_path):
    """dry-run 스테이징 → '세이브 1' 도장 → 에이전트 세션에서 CONSUME 0 소비(교환 축).

    ★교환 축(2026-07-13): 구큐 outcome=miss → stance=refutes 유도 · upheld 기본 →
    owner(지적,hit)+ai(답변,miss) 2행 — 옳은 지적이 owner 적중으로 계상(축 뒤집힘 교정)."""
    home, ledger = _make_home(tmp_path)
    env = _env(home)
    _write_queue(home, [{"ts": "2026-07-13T04:00:00Z", "outcome": "miss", "queries": [],
                         "recall_linked": False,
                         "evidence": {"feedback": "산으로 간다 다시 봐라"}, "consumed": False}])
    r = _run(["--ledger", ledger, "learn-consume"], env)           # dry-run = 스테이징
    assert r.returncode == 0 and "세이브 N+1" in r.stdout, r.stdout + r.stderr
    lp = json.loads(Path(os.path.join(home, 'last_preview_candidates.json')).read_text(encoding='utf-8'))
    assert len(lp["items"]) == 1
    _stamp("세이브 1", env)                                        # qi=0 → 도장 번호 1
    r2 = _run(["--ledger", ledger, "learn-consume", "--confirm", "CONSUME 0"], env)
    assert r2.returncode == 0 and "OK: 교환 소비" in r2.stdout, r2.stdout + r2.stderr
    con = sqlite3.connect("file:%s?mode=ro" % ledger.replace("\\", "/"), uri=True)
    rows = con.execute("SELECT speaker, outcome FROM hit_events WHERE node_id LIKE 'utter:%'"
                       " ORDER BY speaker").fetchall()
    con.close()
    assert rows == [("ai", "miss"), ("owner", "hit")]


def test_learn_consume_line_stamp_batch(tmp_path):
    """★줄 도장 + 일괄 소비(2026-07-13 owner GO) — 여러 지시가 섞인 메시지의 '세이브 1-3' 줄
    한 번으로 3건 소비(번호 재편 재도장 불필요). 실제 훅 경유 E2E."""
    home, ledger = _make_home(tmp_path)
    env = _env(home)
    _write_queue(home, [
        {"ts": "2026-07-13T21:00:00Z", "stance": "refutes", "queries": [],
         "evidence": {"feedback": "일괄 지적 하나"}, "consumed": False},
        {"ts": "2026-07-13T21:01:00Z", "stance": "accepts", "queries": [],
         "evidence": {"feedback": "일괄 인정 둘"}, "consumed": False},
        {"ts": "2026-07-13T21:02:00Z", "stance": "refutes", "queries": [],
         "evidence": {"feedback": "일괄 지적 셋"}, "consumed": False},
    ])
    r = _run(["--ledger", ledger, "learn-consume"], env)           # dry-run = 스테이징
    assert r.returncode == 0, r.stdout + r.stderr
    # 사장님 스타일 그대로: 다른 지시들 사이에 도장이 한 줄
    _stamp("머지해. 그리고 조사도 해줘\n세이브 1-3\n다음 작업 고", env)
    r2 = _run(["--ledger", ledger, "learn-consume", "--confirm", "CONSUME 0,1,2"], env)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert r2.stdout.count("OK: 교환 소비") == 3, r2.stdout
    con = sqlite3.connect("file:%s?mode=ro" % ledger.replace("\\", "/"), uri=True)
    n = con.execute("SELECT count(*) FROM hit_events WHERE node_id LIKE 'utter:%'").fetchone()[0]
    con.close()
    assert n == 5   # refutes 2건×2행 + accepts 1건×1행


def test_learn_consume_inline_mention_still_blocked(tmp_path):
    """문장 속 '세이브1' 언급(줄 일부)은 여전히 도장 아님 — 오도장 차단 계약 유지."""
    home, ledger = _make_home(tmp_path)
    env = _env(home)
    _write_queue(home, [{"ts": "2026-07-13T22:00:00Z", "stance": "refutes", "queries": [],
                         "evidence": {"feedback": "지적"}, "consumed": False}])
    _run(["--ledger", ledger, "learn-consume"], env)
    _stamp("도장은 세이브1 - 이건 왜 자꾸 실패하는거지?", env)     # 줄 일부 → 무시돼야
    r = _run(["--ledger", ledger, "learn-consume", "--confirm", "CONSUME 0"], env)
    assert "BLOCK: G4_no_auto" in r.stdout, r.stdout


def test_learn_consume_without_stamp_blocked(tmp_path):
    """도장 없는 에이전트 세션 learn-consume = G4 차단 불변(fail-closed)."""
    home, ledger = _make_home(tmp_path)
    env = _env(home)
    _write_queue(home, [{"ts": "2026-07-13T04:00:00Z", "outcome": "hit", "queries": [],
                         "recall_linked": False,
                         "evidence": {"feedback": "클로드맞다"}, "consumed": False}])
    _run(["--ledger", ledger, "learn-consume"], env)               # 스테이징만 · 도장 0
    r = _run(["--ledger", ledger, "learn-consume", "--confirm", "CONSUME 0"], env)
    assert "BLOCK: G4_no_auto" in r.stdout, r.stdout
