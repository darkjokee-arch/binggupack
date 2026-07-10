# -*- coding: utf-8 -*-
"""P1-A Trusted Approval Event — TIER-3 e2e (실제 CLI subprocess + MCP handle_tool).

전부 temp home 격리 · 운영 ~/.binggupack 미접촉(sentinel 검증). 스타일: tests/test_demo.py.
"""
import json
import os
import subprocess
import sys

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
    return e


def _cli(home, args, stdin_data=None, trusted=False):
    e = _env(home)
    if trusted:
        e["BINGGU_TRUSTED_CLI"] = "1"
    ledger = os.path.join(home, "ledger.sqlite")
    return subprocess.run([sys.executable, BINGGU, "--ledger", ledger] + args,
                          cwd=REPO, env=e, input=(stdin_data or ""),
                          capture_output=True, text=True, timeout=120)


@pytest.fixture()
def home(tmp_path):
    h = str(tmp_path / "binggu_home")
    os.makedirs(os.path.join(h, "snapshots"), exist_ok=True)
    # provider 활성화 config.
    from binggupack.safety import trusted_approval as ta
    with open(ta.config_path(h), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "ttl_seconds": 900, "pending_cap": 8}, f)
    # 회상/기각 대상 노드 seed.
    from binggupack.storage import open_g3
    db = open_g3(os.path.join(h, "ledger.sqlite"))
    db.con.execute(
        "INSERT OR IGNORE INTO nodes(node_id,node_type,sentence,semantic_subtype,speaker,state,created_at,candidate)"
        " VALUES('e1','judgment','이 입찰은 마진이 낮아 보류하는 것이 낫다','교훈','owner','active','2026-06-20T00:00:00Z',1)")
    db.con.commit()
    db.close()
    return h


def _request_deprecate(home_dir):
    """MCP 모델 경로: dry_run=False, 승인 없음 → approval_required + request_id."""
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


def test_full_owner_approval_flow(home):
    id8, tr = _request_deprecate(home)
    rid = tr.get("request_id")
    assert tr.get("reason") == "approval_required"
    assert tr.get("executed_write") is False
    assert rid

    # CLI: approvals 목록에 rid 노출
    r = _cli(home, ["approvals"])
    assert r.returncode == 0
    assert rid in r.stdout

    # CLI: approval show — 실내용(문장) 노출
    r = _cli(home, ["approval", "show", rid])
    assert r.returncode == 0
    assert "보류" in r.stdout  # 실제 저장/기각 대상 문장 렌더

    # CLI: approve 비대화형(pipe stdin) → 하드 거부 exit 2 (TAE-5)
    r = _cli(home, ["approval", "approve", rid], stdin_data="")
    assert r.returncode == 2
    assert _node_state(home, "e1") == "active"  # 승인 안 됨 → 여전히 active

    # CLI: approve owner 자동화(BINGGU_TRUSTED_CLI=1) → 승인 발행
    r = _cli(home, ["approval", "approve", rid], trusted=True)
    assert r.returncode == 0

    # MCP 모델: approval_id 제시 → 정확히 1회 consume → 노드 deprecated
    from binggupack.mcp.server_handlers import handle_tool
    os.environ["BINGGU_HOME"] = home
    tr = handle_tool("deprecate", {"index": 1, "id8": id8, "reason": "오판이라 기각",
                                   "dry_run": False, "confirm": "DEPRECATE 1 " + id8,
                                   "approval_id": rid}, REPO).get("tool_result") or {}
    assert tr.get("executed_write") is True
    assert _node_state(home, "e1") == "deprecated"

    # replay: 같은 approval → 2차 write 0
    tr2 = handle_tool("deprecate", {"index": 1, "id8": id8, "reason": "오판이라 기각",
                                    "dry_run": False, "confirm": "DEPRECATE 1 " + id8,
                                    "approval_id": rid}, REPO).get("tool_result") or {}
    assert tr2.get("executed_write") is False
    assert tr2.get("reason") == "approval_already_consumed"


def test_reject_blocks_consume(home):
    id8, tr = _request_deprecate(home)
    rid = tr.get("request_id")
    # CLI: reject
    r = _cli(home, ["approval", "reject", rid])
    assert r.returncode == 0
    # owner 가 실수로 approve 도 했다고 가정 → 그래도 reject tombstone 우선(consume 차단)
    r = _cli(home, ["approval", "approve", rid], trusted=True)
    from binggupack.mcp.server_handlers import handle_tool
    os.environ["BINGGU_HOME"] = home
    tr = handle_tool("deprecate", {"index": 1, "id8": id8, "reason": "오판이라 기각",
                                   "dry_run": False, "confirm": "DEPRECATE 1 " + id8,
                                   "approval_id": rid}, REPO).get("tool_result") or {}
    assert tr.get("executed_write") is False
    assert tr.get("reason") in ("approval_rejected", "approval_revoked")
    assert _node_state(home, "e1") == "active"


def test_operating_ledger_untouched(home):
    """실 ~/.binggupack ledger/approvals mtime 불변(모든 e2e 는 temp home)."""
    real = os.path.join(os.path.expanduser("~"), ".binggupack")
    before = {fn: (os.path.getmtime(os.path.join(real, fn)) if os.path.exists(os.path.join(real, fn)) else None)
              for fn in ("ledger.sqlite", "approvals.jsonl")}
    id8, tr = _request_deprecate(home)
    rid = tr.get("request_id")
    _cli(home, ["approval", "approve", rid], trusted=True)
    from binggupack.mcp.server_handlers import handle_tool
    os.environ["BINGGU_HOME"] = home
    handle_tool("deprecate", {"index": 1, "id8": id8, "reason": "오판이라 기각",
                              "dry_run": False, "confirm": "DEPRECATE 1 " + id8,
                              "approval_id": rid}, REPO)
    after = {fn: (os.path.getmtime(os.path.join(real, fn)) if os.path.exists(os.path.join(real, fn)) else None)
             for fn in ("ledger.sqlite", "approvals.jsonl")}
    assert before == after
