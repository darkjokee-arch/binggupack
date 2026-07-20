"""pair accept 기본 ON 회귀 — 실제 hook 경로(gate_ref 승격)에서 '세이브' 한 번이
저장 + owner_accepted 를 함께 남기는지 검증.

배경(2026-07-20): 저장축 도장 통합 Stage2-a 로 cmd_pair 를 accept 기본 ON 으로 전환.
4cli 검토에서 'hook 경로 무음 수용실패' 우려가 제기됐으나, 실측(수정 전/후 동일 PASS)으로
반증됨 — _resolve_human_ctx(분기1 save_gate_ref)와 save_paired 의 _gate_ref_ok 가 같은
gate_human_for_ref 를 보므로 갈리지 않는다. 이 테스트는 그 실측 경로를 회귀로 고정한다
(ctx 직접 주입이 아니라 owner '세이브' 발화 → hook 도장 → 저장 의 진짜 경로).
"""
import os
import sys
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO, os.path.join(REPO, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _mk(ledger, confirm, ai_text=None):
    from types import SimpleNamespace
    return SimpleNamespace(ledger=ledger, owner_text="회귀 검증용 직감 문장 하나다",
                           ai_text=ai_text, relation="accepts", by="ai",
                           owner_pick=1, ai_pick=1, confirm=confirm, due=None, tentative=False)


def _owner_accept_count(binggu, ledger):
    db, _ = binggu._open(ledger)
    try:
        return db.con.execute("SELECT COUNT(*) FROM owner_acceptances").fetchone()[0]
    finally:
        db.close()


def test_pair_accept_on_hookpath(tmp_path, monkeypatch):
    """owner '세이브 1' 한 번 → 저장 + 수용(owner_accepted) 함께."""
    home = str(tmp_path)
    monkeypatch.setenv("BINGGU_HOME", home)
    monkeypatch.delenv("CLAUDECODE", raising=False)   # 터미널 사용자 경로
    subprocess.run([sys.executable, os.path.join(REPO, "binggu.py"), "start"],
                   env=dict(os.environ), capture_output=True)
    import binggu
    from binggupack.safety.gate_log import gate_record_from_prompt
    ledger = os.path.join(home, "ledger.sqlite")

    binggu.cmd_pair(_mk(ledger, None))                       # 1) 미리보기 → 앵커
    assert gate_record_from_prompt("세이브 1") >= 1          # 2) hook 도장(owner 발화)
    rc = binggu.cmd_pair(_mk(ledger, "PAIR owner:1"))        # 3) 저장(ctx 강제주입 없음)
    assert rc == 0
    assert _owner_accept_count(binggu, ledger) == 1          # 세이브 한 번 = 저장+수용


def test_pair_tentative_defers_accept(tmp_path, monkeypatch):
    """--tentative 면 저장만 되고 수용은 보류."""
    home = str(tmp_path)
    monkeypatch.setenv("BINGGU_HOME", home)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    subprocess.run([sys.executable, os.path.join(REPO, "binggu.py"), "start"],
                   env=dict(os.environ), capture_output=True)
    import binggu
    from binggupack.safety.gate_log import gate_record_from_prompt
    from types import SimpleNamespace
    ledger = os.path.join(home, "ledger.sqlite")

    binggu.cmd_pair(_mk(ledger, None))
    assert gate_record_from_prompt("세이브 1") >= 1
    a = SimpleNamespace(ledger=ledger, owner_text="회귀 검증용 직감 문장 하나다",
                        ai_text=None, relation="accepts", by="ai",
                        owner_pick=1, ai_pick=1, confirm="PAIR owner:1", due=None, tentative=True)
    binggu.cmd_pair(a)
    assert _owner_accept_count(binggu, ledger) == 0          # 보류 = 수용 0
