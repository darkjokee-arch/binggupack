# -*- coding: utf-8 -*-
"""마무리 화면이 세션마다 인라인으로 캐내던 3종을 status/trace_review 가 직접 준다(2026-08-01).

배경 실측: 세션 마무리 구간 셸 호출 134회 중 `python -c` 인라인 조회가 35회였다. 판정 대기
"수" 하나를 알려고 trace_review 를 부르면 수백 건 전문(실측 305건)이 통째로 딸려와, AI 소비자
에겐 그대로 토큰이고 사람에겐 볼 필요 없는 덤프였다. 저장 전후 대조용 기준값(nodes 총수·ledger
mtime)도 status 에 없어 매번 sqlite 를 직접 열었다.

이 파일이 못 박는 계약:
  - `count_pending()` == `len(list_pending())` — 선별 로직은 정본 1곳(list_pending)에 위임하므로
    판정 기준이 바뀌어도 두 값이 발산할 수 없다. 복제였다면 여기가 조용히 틀어진다.
  - `trace_review(count_only=true)` → 목록 없이 count. **기본 호출은 종전대로 목록 포함**(하위호환).
  - `status` → nodes_total · recall_pending · ledger_mtime.

운영홈 불변: conftest 의 홈 격리 + 각 검증이 tmp home 을 명시 인자/env 로 넘긴다(write 는 tmp 안에서만).
"""
import os
import subprocess
import sys

from binggupack.mcp import server_handlers as SH
from binggupack.pack import recall_trace as RT

TS = "2026-08-01T00:00:00Z"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINGGU = os.path.join(REPO, "binggu.py")


def _mk_trace(home, node_ids=("node:CONV:aa01", "node:CONV:bb02", "node:CONV:cc03")):
    RT.set_trace_flag(True, home=home)
    recalled = [{"node_id": n, "semantic_subtype": "교훈", "rank_score": 0.9, "relevance": 0.8}
                for n in node_ids]
    return RT.record_trace("작업", "why_search", recalled, TS, home=home)["trace_id"]


def _init_home(home):
    """운영홈을 건드리지 않도록 tmp 에 장부를 만든다(CLI init · test_status_rollup 과 동일 방식)."""
    ledger = os.path.join(home, "ledger.sqlite")
    env = dict(os.environ, BINGGU_HOME=home)
    r = subprocess.run([sys.executable, BINGGU, "--ledger", ledger, "init"],
                       env=env, capture_output=True, text=True, cwd=REPO, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return ledger


def test_count_pending_matches_list_pending(tmp_path):
    """수만 세는 경로가 목록 경로와 같은 수를 낸다 — 위임이라 발산할 수 없음을 고정."""
    home = str(tmp_path)
    _mk_trace(home)
    full = RT.list_pending(home=home, ledger_path=None)
    assert RT.count_pending(home=home) == len(full)
    assert len(full) == 3, "직접 인출(why_search)은 무컷이라 3건 전부 판정 대상"


def test_count_pending_follows_judgement(tmp_path):
    """판정이 찍히면 양쪽 다 줄어든다(같은 선별 규칙을 쓴다는 증거)."""
    home = str(tmp_path)
    tid = _mk_trace(home)
    before = RT.count_pending(home=home)
    r = RT.record_outcome(tid, "node:CONV:aa01", "used", {"actor": "human"}, TS, home=home)
    assert r.get("recorded") is True, r
    after = RT.count_pending(home=home)
    assert after == before - 1
    assert after == len(RT.list_pending(home=home, ledger_path=None))


def test_trace_review_count_only_omits_list(tmp_path, monkeypatch):
    """count_only=true → 수만. 수백 건 전문이 딸려오지 않는다(이 도구를 비싸게 만들던 원인)."""
    home = str(tmp_path)
    _init_home(home)
    monkeypatch.setenv("BINGGU_HOME", home)
    _mk_trace(home)
    out = SH._u_trace_review({"count_only": True})
    assert out["count"] == 3
    assert out.get("count_only") is True
    assert "pending" not in out, "목록을 빼는 것이 이 옵션의 전부 — 남아 있으면 절약이 0"


def test_trace_review_default_still_returns_list(tmp_path, monkeypatch):
    """기본 호출은 종전 그대로(하위호환) — 옵션이 기존 소비자를 깨뜨리지 않는다."""
    home = str(tmp_path)
    _init_home(home)
    monkeypatch.setenv("BINGGU_HOME", home)
    _mk_trace(home)
    out = SH._u_trace_review()
    assert out["count"] == 3
    assert len(out["pending"]) == 3
    assert out["pending"][0]["idx"] == 1


def test_status_exposes_close_fields(tmp_path, monkeypatch):
    """status 가 마무리 3종을 준다 — 저장 전후 대조(앵커≠저장 방지)의 기준값 포함."""
    home = str(tmp_path)
    _init_home(home)
    monkeypatch.setenv("BINGGU_HOME", home)
    _mk_trace(home)
    st = SH._u_status()
    assert st["ledger_exists"] is True
    assert st["nodes_total"] == st["active"] + st["deprecated"]
    assert st["recall_pending"] == 3
    assert st["ledger_mtime"] and st["ledger_mtime"].endswith("+00:00")


def test_status_survives_trace_store_absent(tmp_path, monkeypatch):
    """trace store 가 없거나 opt-in off 여도 status 는 죽지 않는다(표시 실패로 상태 조회를 막지 않음)."""
    home = str(tmp_path)
    _init_home(home)
    monkeypatch.setenv("BINGGU_HOME", home)
    st = SH._u_status()
    assert st["ledger_exists"] is True
    assert st["recall_pending"] in (0, None), "store 부재는 0 또는 None — 예외로 터지면 안 된다"
