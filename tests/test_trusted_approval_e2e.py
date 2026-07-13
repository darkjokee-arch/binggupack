# -*- coding: utf-8 -*-
"""Trusted Approval — TIER-3 e2e (실제 CLI subprocess + MCP handle_tool).

2026-07-13 MCP save approval 제거 반영(저장 게이트 "preview + 사람의 save n 입력" 단일 원칙 후속):
  - MCP 표면(보안 회귀): approval_id 를 줘도 write 승격 불가(fail-closed) · PENDING 요청도 미발행 ·
    owner 가 진짜 mint 한 approval 조차 MCP 로는 소비/승격 0.
  - owner CLI 표면(보존 자산): `binggu approval` 채널(비대화형 approve 하드 거부 · 대화형 PTY 성공) +
    비-저장 mutation 의 exact-bound `--approval-id` 경로(_mutation_via_approval)는 여전히 동작 —
    요청 발행 → owner mint → 정확 1회 consume → replay 차단.

전부 temp home 격리 · 운영 ~/.binggupack 미접촉(sentinel 검증). 스타일: tests/test_demo.py.
승인 성공경로는 **환경변수 백도어가 아니라** owner 채널 test double(ta.mint_approval 직접 호출)
또는 실제 대화형 PTY(Unix)로만 만든다. 비대화형/환경변수 approve 는 항상 exit≠0 · mint 0.
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


def _seed_request(home_dir, operation, payload):
    """구 P1-A MCP 배선이 하던 PENDING 요청 등록을 owner 측 픽스처로 재현(정본 binding 함수 재사용).
    MCP 는 더 이상 요청을 만들지 않으므로, 'owner mint approval 도 MCP 승격 불가' 검증과
    CLI --approval-id 흐름 검증에 이 픽스처를 쓴다. 반환 = request_id."""
    from binggupack.safety import trusted_approval as ta
    from binggupack.storage import open_g3
    from binggupack.storage.schema import ledger_id
    digest = ta.canonical_payload_digest(operation, payload)
    db = open_g3(os.path.join(home_dir, "ledger.sqlite"))
    try:
        lid = ledger_id(db.con)
        rid = ta.compute_request_id(operation, digest, lid)
        up = ta.upsert_request(db.con, rid, ta.PROTOCOL_VERSION, operation, digest, lid,
                               ta.summary_for(operation, payload, lid), time.time(), 900, 8)
        assert up.get("ok")
    finally:
        db.close()
    return rid


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


def _mcp_deprecate(home_dir, approval_id=None):
    """MCP write 시도(정확 confirm) — 2026-07-13 이후 항상 fail-closed 여야 한다."""
    from binggupack.mcp.server_handlers import handle_tool
    from openbinggu_candidate_list_view import node_id8
    os.environ["BINGGU_HOME"] = home_dir
    id8 = node_id8("e1")
    args = {"index": 1, "id8": id8, "reason": "오판이라 기각",
            "dry_run": False, "confirm": "DEPRECATE 1 " + id8}
    if approval_id is not None:
        args["approval_id"] = approval_id
    return id8, (handle_tool("deprecate", args, REPO).get("tool_result") or {})


def _node_state(home_dir, nid):
    from binggupack.storage import open_g3
    db = open_g3(os.path.join(home_dir, "ledger.sqlite"))
    try:
        r = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (nid,)).fetchone()
        return r[0] if r else None
    finally:
        db.close()


def _table_count(home_dir, table):
    from binggupack.storage import open_g3
    db = open_g3(os.path.join(home_dir, "ledger.sqlite"))
    try:
        return db.con.execute("SELECT count(*) FROM %s" % table).fetchone()[0]
    finally:
        db.close()


# ── MCP 표면: approval 로 write 승격 불가(보안 회귀) ────────────────────────────────


def test_mcp_write_fail_closed_and_no_request(home):
    """MCP write 시도는 G4_no_auto fail-closed — PENDING 요청도 만들지 않는다(2026-07-13 제거)."""
    _id8, tr = _mcp_deprecate(home)
    assert tr.get("executed_write") is False
    assert tr.get("reason") == "G4_no_auto"
    assert not tr.get("request_id")
    assert tr.get("write_available") is False
    assert _table_count(home, "approval_requests") == 0
    assert _node_state(home, "e1") == "active"


def test_mcp_cannot_consume_owner_approval(home):
    """owner 가 진짜 mint 한 approval 이라도 MCP 는 소비/승격 불가 — approval_id 는 무시된다."""
    rid = _seed_request(home, "deprecate", {"index": 1, "id8": _e1_id8(), "reason": "오판이라 기각"})
    _mint_owner(home, rid)
    assert _approve_events(home) == 1

    _id8, tr = _mcp_deprecate(home, approval_id=rid)
    assert tr.get("executed_write") is False
    assert tr.get("reason") == "G4_no_auto"
    assert tr.get("approval_id_ignored") is True
    assert _node_state(home, "e1") == "active"
    assert _table_count(home, "approval_consumptions") == 0   # 소비 0(승인 소각도 없음)

    # 재시도(구 replay 벡터)도 동일 fail-closed — 2차 write 0.
    _id8, tr2 = _mcp_deprecate(home, approval_id=rid)
    assert tr2.get("executed_write") is False
    assert _node_state(home, "e1") == "active"


def _e1_id8():
    from openbinggu_candidate_list_view import node_id8
    return node_id8("e1")


# ── owner CLI 표면(보존 자산): approval 발행 채널 + exact-bound --approval-id 경로 ──


def test_cli_approval_channel_lists_and_rejects_noninteractive(home):
    """`binggu approvals/approval show` 조회 + 비대화형(pipe) approve 하드 거부(exit 2 · mint 0)."""
    rid = _seed_request(home, "deprecate", {"index": 1, "id8": _e1_id8(), "reason": "오판이라 기각"})

    r = _cli(home, ["approvals"])
    assert r.returncode == 0 and rid in r.stdout
    r = _cli(home, ["approval", "show", rid])
    assert r.returncode == 0

    r = _cli(home, ["approval", "approve", rid], stdin_data="")
    assert r.returncode == 2
    assert _approve_events(home) == 0 and _node_state(home, "e1") == "active"


def test_cli_mutation_via_approval_still_works(home):
    """CLI `_mutation_via_approval`(비-저장 mutation·exact-bound approval) 경로 생존 검증 —
    accept: 요청 발행(--approval-id \"\") → owner mint → --approval-id <rid> 정확 1회 적용 → replay 차단."""
    id8 = _e1_id8()
    confirm = "ACCEPT 1 " + id8

    # ① 승인 요청 발행(approval 미제시와 동등한 빈 approval_id) — write 0 · PENDING 1행.
    r = _cli(home, ["accept", "1", id8, "--reason", "확정", "--confirm", confirm,
                    "--approval-id", ""])
    assert "approval_required" in (r.stdout + r.stderr)
    assert _table_count(home, "approval_requests") == 1
    assert _table_count(home, "owner_acceptances") == 0

    from binggupack.storage import open_g3
    db = open_g3(os.path.join(home, "ledger.sqlite"))
    try:
        rid = db.con.execute("SELECT request_id FROM approval_requests").fetchone()[0]
    finally:
        db.close()

    # ② owner 승인(test double) → ③ --approval-id <rid> 재호출 → 정확 1회 적용.
    _mint_owner(home, rid)
    r = _cli(home, ["accept", "1", id8, "--reason", "확정", "--confirm", confirm,
                    "--approval-id", rid])
    assert r.returncode == 0, r.stdout + r.stderr
    assert _table_count(home, "owner_acceptances") == 1
    assert _table_count(home, "approval_consumptions") >= 1   # one-time consume 기록

    # ④ replay — 같은 approval 재사용 → 2차 적용 0.
    r = _cli(home, ["accept", "1", id8, "--reason", "확정", "--confirm", confirm,
                    "--approval-id", rid])
    assert "approval_already_consumed" in (r.stdout + r.stderr)
    assert _table_count(home, "owner_acceptances") == 1


def test_env_var_cannot_approve(home):
    """환경변수(BINGGU_TRUSTED_CLI 여러 값)로는 approval event 발행 불가 — 비대화형 항상 exit≠0·mint0."""
    rid = _seed_request(home, "deprecate", {"index": 1, "id8": _e1_id8(), "reason": "오판이라 기각"})
    for v in ("1", "true", "TRUE", "yes", "on", "randomstring"):
        r = _cli(home, ["approval", "approve", rid], stdin_data="junk\n",
                 env_extra={"BINGGU_TRUSTED_CLI": v})
        assert r.returncode != 0, "env %s must not approve" % v
    assert _approve_events(home) == 0
    assert _node_state(home, "e1") == "active"


def test_strict_flag_false_cannot_fail_open(home):
    """BINGGU_STRICT_HUMAN_GATE=0/false/off/'' 로도 approve 는 fail-open 되지 않는다(비대화형 거부)."""
    rid = _seed_request(home, "deprecate", {"index": 1, "id8": _e1_id8(), "reason": "오판이라 기각"})
    for sv in ("0", "false", "off", ""):
        r = _cli(home, ["approval", "approve", rid], stdin_data="",
                 env_extra={"BINGGU_STRICT_HUMAN_GATE": sv, "BINGGU_TRUSTED_CLI": "1"})
        assert r.returncode != 0
    assert _approve_events(home) == 0 and _node_state(home, "e1") == "active"


@pytest.mark.skipif(sys.platform == "win32", reason="PTY 미지원(Windows) — 대화형 성공경로는 Unix 에서만")
def test_interactive_approve_pty(home):
    """실제 대화형 TTY(pty)에서 'APPROVE <rid8>' 입력 → event 1개 발행(owner 채널 생존).
    발행된 approval 도 MCP 표면으로는 소비/승격 불가(2026-07-13 제거 회귀)."""
    import pty
    rid = _seed_request(home, "deprecate", {"index": 1, "id8": _e1_id8(), "reason": "오판이라 기각"})
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
    _id8, tr = _mcp_deprecate(home, approval_id=rid)
    assert tr.get("executed_write") is False and _node_state(home, "e1") == "active"


def test_operating_ledger_untouched(home):
    """실 ~/.binggupack ledger/approvals mtime 불변(모든 e2e 는 temp home)."""
    real = os.path.join(os.path.expanduser("~"), ".binggupack")
    before = {fn: (os.path.getmtime(os.path.join(real, fn)) if os.path.exists(os.path.join(real, fn)) else None)
              for fn in ("ledger.sqlite", "approvals.jsonl")}
    rid = _seed_request(home, "deprecate", {"index": 1, "id8": _e1_id8(), "reason": "오판이라 기각"})
    _mint_owner(home, rid)
    _mcp_deprecate(home, approval_id=rid)
    after = {fn: (os.path.getmtime(os.path.join(real, fn)) if os.path.exists(os.path.join(real, fn)) else None)
             for fn in ("ledger.sqlite", "approvals.jsonl")}
    assert before == after
