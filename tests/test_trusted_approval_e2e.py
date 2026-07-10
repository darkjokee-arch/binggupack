# -*- coding: utf-8 -*-
"""P1-A / P1-A.1 Trusted Approval — TIER-3 e2e (실제 CLI subprocess + MCP handle_tool).

전부 temp home 격리 · 운영 ~/.binggupack 미접촉(sentinel 검증). 스타일: tests/test_demo.py.

P1-A.1: 승인 성공경로는 **환경변수 백도어(BINGGU_TRUSTED_CLI)가 아니라** owner 채널 test double
(ta.mint_approval 직접 호출) 또는 실제 대화형 PTY(Unix)로만 만든다. 비대화형/환경변수 approve 는
항상 exit≠0 · mint 0.
"""
import json
import os
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINGGU = os.path.join(REPO, "binggu.py")
for _p in (REPO, os.path.join(REPO, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _env(home):
    e = dict(os.environ)
    e["BINGGU_HOME"] = home
    e.pop("BINGGU_TRUSTED_CLI", None)
    e.pop("BINGGU_STRICT_HUMAN_GATE", None)
    return e


def _cli(home, args, stdin_data="", env_extra=None):
    e = _env(home)
    if env_extra:
        e.update(env_extra)
    ledger = os.path.join(home, "ledger.sqlite")
    return subprocess.run([sys.executable, BINGGU, "--ledger", ledger] + args,
                          cwd=REPO, env=e, input=stdin_data,
                          capture_output=True, text=True, timeout=120)


def _mint_owner(home_dir, rid):
    """owner 채널 시뮬(test double) — CLI TTY 대신 core mint_approval 직접 호출. env 백도어 미사용.
    production 에서 mint_approval 은 대화형 TTY 검증 후 CLI 만 호출한다(이 함수는 tests 전용)."""
    from binggupack.safety import trusted_approval as ta
    from binggupack.storage import open_g3
    db = open_g3(os.path.join(home_dir, "ledger.sqlite"))
    try:
        req = ta.get_request(db.con, rid)
    finally:
        db.close()
    assert req is not None
    ta.mint_approval(home_dir, req, 900, time.time(), channel="test_double")


def _approve_events(home_dir):
    from binggupack.safety import trusted_approval as ta
    return sum(1 for e in ta.read_events(home_dir) if e.get("record_type") == "approve")


@pytest.fixture()
def home(tmp_path):
    h = str(tmp_path / "binggu_home")
    os.makedirs(os.path.join(h, "snapshots"), exist_ok=True)
    from binggupack.safety import trusted_approval as ta
    with open(ta.config_path(h), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "ttl_seconds": 900, "pending_cap": 8}, f)
    from binggupack.storage import open_g3
    db = open_g3(os.path.join(h, "ledger.sqlite"))
    db.con.execute(
        "INSERT OR IGNORE INTO nodes(node_id,node_type,sentence,semantic_subtype,speaker,state,created_at,candidate)"
        " VALUES('e1','judgment','이 입찰은 마진이 낮아 보류하는 것이 낫다','교훈','owner','active','2026-06-20T00:00:00Z',1)")
    db.con.commit()
    db.close()
    return h


def _request_deprecate(home_dir):
    from binggupack.mcp.server_handlers import handle_tool
    from openbinggu_candidate_list_view import node_id8
    os.environ["BINGGU_HOME"] = home_dir
    id8 = node_id8("e1")
    tr = handle_tool("deprecate", {"index": 1, "id8": id8, "reason": "오판이라 기각",
                                   "dry_run": False, "confirm": "DEPRECATE 1 " + id8}, REPO).get("tool_result") or {}
    return id8, tr


def _node_state(home_dir, nid):
    from binggupack.storage import open_g3
    db = open_g3(os.path.join(home_dir, "ledger.sqlite"))
    try:
        r = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (nid,)).fetchone()
        return r[0] if r else None
    finally:
        db.close()


def _consume(home_dir, id8, rid):
    from binggupack.mcp.server_handlers import handle_tool
    os.environ["BINGGU_HOME"] = home_dir
    return handle_tool("deprecate", {"index": 1, "id8": id8, "reason": "오판이라 기각",
                                     "dry_run": False, "confirm": "DEPRECATE 1 " + id8,
                                     "approval_id": rid}, REPO).get("tool_result") or {}


def test_full_owner_approval_flow(home):
    id8, tr = _request_deprecate(home)
    rid = tr.get("request_id")
    assert tr.get("reason") == "approval_required"
    assert tr.get("executed_write") is False and rid

    r = _cli(home, ["approvals"])
    assert r.returncode == 0 and rid in r.stdout
    r = _cli(home, ["approval", "show", rid])
    assert r.returncode == 0 and "보류" in r.stdout

    # 비대화형(pipe) approve → 하드 거부 exit 2 · mint 0
    r = _cli(home, ["approval", "approve", rid], stdin_data="")
    assert r.returncode == 2
    assert _approve_events(home) == 0 and _node_state(home, "e1") == "active"

    # owner 승인(test double) → approval_id consume 정확히 1회
    _mint_owner(home, rid)
    tr = _consume(home, id8, rid)
    assert tr.get("executed_write") is True
    assert _node_state(home, "e1") == "deprecated"

    # replay → 2차 write 0
    tr2 = _consume(home, id8, rid)
    assert tr2.get("executed_write") is False
    assert tr2.get("reason") == "approval_already_consumed"


def test_reject_blocks_consume(home):
    id8, tr = _request_deprecate(home)
    rid = tr.get("request_id")
    r = _cli(home, ["approval", "reject", rid])
    assert r.returncode == 0
    # reject 후 owner 가 approve(test double) 해도 tombstone 우선 → consume 차단
    _mint_owner(home, rid)
    tr = _consume(home, id8, rid)
    assert tr.get("executed_write") is False
    assert tr.get("reason") in ("approval_rejected", "approval_revoked")
    assert _node_state(home, "e1") == "active"


def test_env_var_cannot_approve(home):
    """환경변수(BINGGU_TRUSTED_CLI 여러 값)로는 approval event 발행 불가 — 비대화형 항상 exit≠0·mint0."""
    id8, tr = _request_deprecate(home)
    rid = tr.get("request_id")
    for v in ("1", "true", "TRUE", "yes", "on", "randomstring"):
        r = _cli(home, ["approval", "approve", rid], stdin_data="junk\n",
                 env_extra={"BINGGU_TRUSTED_CLI": v})
        assert r.returncode != 0, "env %s must not approve" % v
    assert _approve_events(home) == 0
    assert _node_state(home, "e1") == "active"


def test_strict_flag_false_cannot_fail_open(home):
    """BINGGU_STRICT_HUMAN_GATE=0/false/off/'' 로도 approve 는 fail-open 되지 않는다(비대화형 거부)."""
    id8, tr = _request_deprecate(home)
    rid = tr.get("request_id")
    for sv in ("0", "false", "off", ""):
        r = _cli(home, ["approval", "approve", rid], stdin_data="",
                 env_extra={"BINGGU_STRICT_HUMAN_GATE": sv, "BINGGU_TRUSTED_CLI": "1"})
        assert r.returncode != 0
    assert _approve_events(home) == 0 and _node_state(home, "e1") == "active"


@pytest.mark.skipif(sys.platform == "win32", reason="PTY 미지원(Windows) — 대화형 성공경로는 Unix 에서만")
def test_interactive_approve_pty(home):
    """실제 대화형 TTY(pty)에서 'APPROVE <rid8>' 입력 → event 1개 발행 · 이후 MCP consume 정확히 1회."""
    import pty
    id8, tr = _request_deprecate(home)
    rid = tr.get("request_id")
    mfd, sfd = pty.openpty()
    ledger = os.path.join(home, "ledger.sqlite")
    p = subprocess.Popen([sys.executable, BINGGU, "--ledger", ledger, "approval", "approve", rid],
                         cwd=REPO, env=_env(home), stdin=sfd, stdout=sfd, stderr=sfd, close_fds=True)
    os.close(sfd)
    time.sleep(0.8)
    os.write(mfd, ("APPROVE %s\n" % rid[:8]).encode())
    try:
        p.wait(timeout=60)
    finally:
        os.close(mfd)
    assert p.returncode == 0
    assert _approve_events(home) == 1
    tr = _consume(home, id8, rid)
    assert tr.get("executed_write") is True and _node_state(home, "e1") == "deprecated"


def test_operating_ledger_untouched(home):
    """실 ~/.binggupack ledger/approvals mtime 불변(모든 e2e 는 temp home)."""
    real = os.path.join(os.path.expanduser("~"), ".binggupack")
    before = {fn: (os.path.getmtime(os.path.join(real, fn)) if os.path.exists(os.path.join(real, fn)) else None)
              for fn in ("ledger.sqlite", "approvals.jsonl")}
    id8, tr = _request_deprecate(home)
    rid = tr.get("request_id")
    _mint_owner(home, rid)
    _consume(home, id8, rid)
    after = {fn: (os.path.getmtime(os.path.join(real, fn)) if os.path.exists(os.path.join(real, fn)) else None)
             for fn in ("ledger.sqlite", "approvals.jsonl")}
    assert before == after
