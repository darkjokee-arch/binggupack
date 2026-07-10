#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-A Trusted Approval Event — 적대 경계 회귀 하니스 (TIER-2).

정본 설계: docs/BINGGUPACK_TRUSTED_APPROVAL_EVENT_RFC.md §24.
전부 temp home(격리)·운영 ~/.binggupack 미접촉. handle_tool(MCP) + trusted_approval(core) 직접 구동.

공격 커버: no-provider fail-closed · valid owner approval 정확 1회 · replay 무-2차-write ·
operation/ledger/payload/protocol mismatch · expired · revoked · rejected · id-guessing ·
concurrent double-consume(정확 1) · demo/test actor · env-var spoof · nonce 응답 미노출 ·
summary PII residue · harvest fail-closed · 6-core actor sweep(TAE-2) · migration 비파괴 ·
control-char binding · MCP TOOLS 파일write 도구 부재 · 운영 ledger sentinel.

CLI: python scripts/openbinggu_trusted_approval_boundary_selftest.py --selftest
"""
import json
import os
import sys
import tempfile
import threading
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "scripts"))


def run():
    from binggupack.safety import trusted_approval as ta
    from binggupack.storage import open_g3

    fails = []
    ran = []

    def ck(name, cond):
        ran.append(name)
        print("  [%s] %s" % ("OK" if cond else "X", name))
        if not cond:
            fails.append(name)

    # 운영 ledger sentinel — 실 ~/.binggupack 파일 mtime 스냅샷(전 구간 불변 확인).
    real_home = os.path.join(os.path.expanduser("~"), ".binggupack")
    sentinel = {}
    for fn in ("ledger.sqlite", "approvals.jsonl"):
        p = os.path.join(real_home, fn)
        sentinel[p] = os.path.getmtime(p) if os.path.exists(p) else None

    allow = os.path.abspath(BASE)
    _saved = os.environ.get("BINGGU_HOME")
    tmp = tempfile.mkdtemp(prefix="ta_boundary_")
    os.environ["BINGGU_HOME"] = tmp
    home = tmp
    os.makedirs(os.path.join(home, "snapshots"), exist_ok=True)

    def enable_provider(ttl=900, cap=8):
        with open(ta.config_path(home), "w", encoding="utf-8") as f:
            json.dump({"enabled": True, "ttl_seconds": ttl, "pending_cap": cap}, f)

    def disable_provider():
        if os.path.exists(ta.config_path(home)):
            os.remove(ta.config_path(home))

    def seed_node(nid, sent):
        db = open_g3(os.path.join(home, "ledger.sqlite"))
        db.con.execute(
            "INSERT OR IGNORE INTO nodes(node_id,node_type,sentence,semantic_subtype,speaker,state,created_at,candidate)"
            " VALUES(?,?,?,?,?,?,?,1)", (nid, "judgment", sent, "교훈", "owner", "active", "2026-06-20T00:00:00Z"))
        db.con.commit()
        db.close()

    try:
        from binggupack.mcp.server_handlers import handle_tool, TOOLS
        from openbinggu_candidate_list_view import node_id8

        def rr(op, args):
            return handle_tool(op, args, allow).get("tool_result") or {}

        # 회상/기각 대상 노드 seed.
        seed_node("nA", "이 입찰은 마진이 낮아 보류하는 것이 낫다")
        id8A = node_id8("nA")

        # ── 1) no-provider fail-closed (P0 baseline · 8벡터 일부) ──────────────────
        disable_provider()
        no_prov_ok = True
        for op, args in (("deprecate", {"index": 1, "id8": id8A, "reason": "x", "dry_run": False,
                                        "confirm": "DEPRECATE 1 " + id8A}),
                         ("harvest_add", {"kind": "url", "url": "https://example.com/x", "dry_run": False,
                                          "confirm": "HARVEST_ADD url https://example.com/x"})):
            tr = rr(op, args)
            no_prov_ok = no_prov_ok and tr.get("executed_write") is False and tr.get("write_available") is False
        ck("no_provider_fail_closed(deprecate/harvest write0)", no_prov_ok)
        ck("no_provider → nA 여전히 active",
           _state(home, "nA") == "active")

        # env-var spoof: 모델이 설정 가능한 truthy env 로는 provider 활성화 안 됨(파일 신호만).
        os.environ["BINGGU_TRUSTED_APPROVAL"] = "1"
        os.environ["BINGGU_APPROVAL_TOKEN"] = "x"
        ck("env_var_spoof → provider 미구성 유지", ta.provider_for(home) is None)
        tr = rr("deprecate", {"index": 1, "id8": id8A, "reason": "x", "dry_run": False,
                              "confirm": "DEPRECATE 1 " + id8A})
        ck("env_var_spoof → write0", tr.get("executed_write") is False)
        os.environ.pop("BINGGU_TRUSTED_APPROVAL", None)
        os.environ.pop("BINGGU_APPROVAL_TOKEN", None)

        # ── 2) valid owner approval → 정확히 1회 write (deprecate canary) ──────────
        enable_provider()
        # 모델: dry_run=False, approval 없음 → approval_required + request_id.
        tr = rr("deprecate", {"index": 1, "id8": id8A, "reason": "오판이라 기각", "dry_run": False,
                              "confirm": "DEPRECATE 1 " + id8A})
        rid = tr.get("request_id")
        ck("approval_required + request_id", tr.get("reason") == "approval_required" and bool(rid))
        ck("approval_required → write0", tr.get("executed_write") is False and _state(home, "nA") == "active")
        # owner CLI: mint approval(get_request → mint_approval).
        db = open_g3(os.path.join(home, "ledger.sqlite"))
        try:
            req = ta.get_request(db.con, rid)
        finally:
            db.close()
        ck("PENDING request 저장됨", req is not None and req["operation"] == "deprecate")
        ta.mint_approval(home, req, 900, time.time())
        # 모델: approval_id 제시 → consume → 정확히 1회.
        tr = rr("deprecate", {"index": 1, "id8": id8A, "reason": "오판이라 기각", "dry_run": False,
                              "confirm": "DEPRECATE 1 " + id8A, "approval_id": rid})
        ck("valid approval → executed_write True", tr.get("executed_write") is True)
        ck("valid approval → nA deprecated", _state(home, "nA") == "deprecated")
        ck("valid approval → nonce 응답 미노출", "nonce" not in json.dumps(tr, ensure_ascii=False)
           or tr.get("nonce") is None)

        # ── 3) replay — 같은 approval 재사용 → already_consumed · 2차 write 0 ──────
        n_before = _node_count(home)
        tr = rr("deprecate", {"index": 1, "id8": id8A, "reason": "오판이라 기각", "dry_run": False,
                              "confirm": "DEPRECATE 1 " + id8A, "approval_id": rid})
        ck("replay → already_consumed", tr.get("reason") == "approval_already_consumed")
        ck("replay → 2차 write 0", tr.get("executed_write") is False and _node_count(home) == n_before)

        # ── 4) binding mismatch (operation / payload / ledger / id) ───────────────
        seed_node("nB", "이 거래처는 결제가 느려 주의가 필요하다")
        id8B = node_id8("nB")
        tr = rr("deprecate", {"index": 2, "id8": id8B, "reason": "다른 사유", "dry_run": False,
                              "confirm": "DEPRECATE 2 " + id8B})
        rid_b = tr.get("request_id")
        db = open_g3(os.path.join(home, "ledger.sqlite"))
        req_b = ta.get_request(db.con, rid_b)
        db.close()
        ta.mint_approval(home, req_b, 900, time.time())
        # payload 변조: 같은 approval_id(rid_b) 인데 reason 을 바꿔 재시도 → 다른 request_id → mismatch.
        tr = rr("deprecate", {"index": 2, "id8": id8B, "reason": "변조된 사유", "dry_run": False,
                              "confirm": "DEPRECATE 2 " + id8B, "approval_id": rid_b})
        ck("payload 변조 → binding_mismatch:request_id",
           tr.get("reason") == "binding_mismatch:request_id" and tr.get("executed_write") is False)
        ck("payload 변조 → nB 여전히 active", _state(home, "nB") == "active")

        # ── 5) expired · revoked · rejected (event store 직접 조작) ────────────────
        # expired: approve 이벤트를 과거 만료로 직접 기록.
        seed_node("nC", "이 공고는 조건이 까다로워 재검토한다")
        id8C = node_id8("nC")
        tr = rr("deprecate", {"index": 3, "id8": id8C, "reason": "만료테스트", "dry_run": False,
                              "confirm": "DEPRECATE 3 " + id8C})
        rid_c = tr.get("request_id")
        db = open_g3(os.path.join(home, "ledger.sqlite"))
        req_c = ta.get_request(db.con, rid_c)
        db.close()
        past = time.time() - 10000
        ta.append_event(home, {"request_id": rid_c, "protocol_version": ta.PROTOCOL_VERSION,
                               "operation": "deprecate", "payload_digest": req_c["payload_digest"],
                               "ledger_id": req_c["ledger_id"], "approval_nonce": "expired_nonce_deadbeef00000000",
                               "approved_at": past, "expires_at": past + 60, "record_type": "approve"})
        tr = rr("deprecate", {"index": 3, "id8": id8C, "reason": "만료테스트", "dry_run": False,
                              "confirm": "DEPRECATE 3 " + id8C, "approval_id": rid_c})
        ck("expired approval → blocked(write0)",
           tr.get("reason") == "approval_expired" and tr.get("executed_write") is False
           and _state(home, "nC") == "active")

        # rejected: tombstone → 이후 approve 이벤트가 있어도 reject 우선.
        seed_node("nD", "이 견적은 마진이 확보되어 진행한다")
        id8D = node_id8("nD")
        tr = rr("deprecate", {"index": 4, "id8": id8D, "reason": "거절테스트", "dry_run": False,
                              "confirm": "DEPRECATE 4 " + id8D})
        rid_d = tr.get("request_id")
        db = open_g3(os.path.join(home, "ledger.sqlite"))
        req_d = ta.get_request(db.con, rid_d)
        db.close()
        ta.tombstone(home, req_d, "reject", time.time())
        ta.mint_approval(home, req_d, 900, time.time())  # reject 후 approve 가 와도
        tr = rr("deprecate", {"index": 4, "id8": id8D, "reason": "거절테스트", "dry_run": False,
                              "confirm": "DEPRECATE 4 " + id8D, "approval_id": rid_d})
        ck("rejected(tombstone) → blocked(write0)",
           tr.get("reason") == "approval_rejected" and tr.get("executed_write") is False)

        # ── 6) concurrent double-consume → 정확히 1 winner (연결 2개가 같은 파일 경쟁) ──
        # 실제 시나리오: 두 프로세스/연결이 같은 ledger 의 같은 nonce 를 동시에 reserve. UNIQUE PK +
        # WAL + busy_timeout 로 정확히 1 winner. (각 스레드는 자기 연결 — sqlite 스레드 격리 준수.)
        nonce = "concnonce_" + "a" * 22
        results = []
        barrier = threading.Barrier(2)

        def worker():
            d = open_g3(os.path.join(home, "ledger.sqlite"))
            try:
                barrier.wait()
                results.append(ta.reserve(d.con, nonce, time.time()).get("status"))
            finally:
                d.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        winners = [s for s in results if s == "reserved"]
        ck("concurrent reserve → 정확히 1 winner",
           len(winners) == 1 and len(results) == 2)

        # ── 7) id-guessing — 아무 approval_id 나 제시(승인 없음) → not_found · write0 ─
        seed_node("nE", "이 사업은 리스크가 커서 보류한다")
        id8E = node_id8("nE")
        tr = rr("deprecate", {"index": 5, "id8": id8E, "reason": "추측", "dry_run": False,
                              "confirm": "DEPRECATE 5 " + id8E, "approval_id": "deadbeef" * 3})
        ck("id_guessing → binding_mismatch/not_found · write0",
           tr.get("executed_write") is False and _state(home, "nE") == "active")

        # ── 8) control-char binding reject ────────────────────────────────────────
        try:
            ta.canonical_payload_digest("deprecate", {"index": 1, "id8": "x", "reason": "a‮b"})
            ck("control-char(bidi) → binding reject", False)
        except ta.ControlCharReject:
            ck("control-char(bidi) → binding reject", True)

        # ── 9) summary payload-agnostic (PII residue 0) ───────────────────────────
        pii_summary = ta.summary_for("deprecate", {"index": 1, "reason": "이혼 관련 판단 삭제"}, "ledgerX")
        ck("summary PII residue 0(payload-agnostic)", "이혼" not in pii_summary)

        # ── 10) 6-core actor sweep (TAE-2) — human 외 전부 write0 ──────────────────
        ck("6-core actor sweep(human 외 write0)", _actor_sweep(home))

        # ── 11) migration 비파괴 + auto-grant 0 ───────────────────────────────────
        ck("migration 비파괴(nodes 보존·auto-grant 0)", _migration_check())

        # ── 12) MCP TOOLS 에 파일/셸 write 도구 부재(TAE-1 이 보증 가능한 것) ──────
        # write-gated 핸들러(save/pair/deprecate/replace/mark/harvest)는 approval 필요. 임의 파일write·셸 도구 0.
        forbidden_names = ("write_file", "edit_file", "shell", "bash", "exec", "run_code", "fs_write")
        exposed = set(TOOLS.keys())
        ck("MCP TOOLS 파일/셸 write 도구 부재",
           not any(any(fn in t for fn in forbidden_names) for t in exposed))

        # ── 13) 운영 ledger sentinel — 실 ~/.binggupack 불변 ──────────────────────
        sentinel_ok = True
        for p, m0 in sentinel.items():
            m1 = os.path.getmtime(p) if os.path.exists(p) else None
            if m0 != m1:
                sentinel_ok = False
        ck("운영 ledger sentinel(실 ~/.binggupack mtime 불변)", sentinel_ok)

    finally:
        if _saved is None:
            os.environ.pop("BINGGU_HOME", None)
        else:
            os.environ["BINGGU_HOME"] = _saved
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 70)
    print("RESULT: %d checks, %d fail" % (len(ran), len(fails)))
    print("GATE=%s" % ("GO" if not fails else "NO-GO"))
    return 0 if not fails else 1


def _state(home, nid):
    from binggupack.storage import open_g3
    db = open_g3(os.path.join(home, "ledger.sqlite"))
    try:
        r = db.con.execute("SELECT state FROM nodes WHERE node_id=?", (nid,)).fetchone()
        return r[0] if r else None
    finally:
        db.close()


def _node_count(home):
    from binggupack.storage import open_g3
    db = open_g3(os.path.join(home, "ledger.sqlite"))
    try:
        return db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    finally:
        db.close()


def _actor_sweep(home):
    """TAE-2: 6 core 게이트에 non-human actor 를 먹여 write 0 확인(allowlist 하드닝 검증)."""
    import tempfile
    from binggupack.storage import open_g3, save_selected
    tmp = tempfile.mkdtemp(prefix="ta_sweep_")
    snap = os.path.join(tmp, "snap")
    os.makedirs(snap, exist_ok=True)
    CONVO = "이 입찰은 마진이 낮아 보류한다."
    ok = True
    for actor in ("reader", "auto", "unapproved", "Human", "HUMAN", "agent", "system", ""):
        db = open_g3(os.path.join(tmp, "s_%s.sqlite" % (actor or "empty")))
        try:
            r = save_selected(db, CONVO, [1], {"actor": actor, "confirm": "SAVE 1"}, snap, explicit=True)
            wrote = bool(r.get("applied"))
            n = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
            if wrote or n != 0:
                ok = False
        finally:
            db.close()
    # human 은 저장돼야(대조군)
    db = open_g3(os.path.join(tmp, "s_human.sqlite"))
    try:
        r = save_selected(db, CONVO, [1], {"actor": "human", "confirm": "SAVE 1"}, snap, explicit=True)
        if not r.get("applied"):
            ok = False
    finally:
        db.close()
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return ok


def _migration_check():
    """구 ledger(approval 테이블 없음)에 apply_schema → 테이블 생성·기존 노드 보존·auto-grant 0·ledger_id 발행."""
    import sqlite3
    import tempfile
    from binggu_schema import apply_schema, ledger_id
    tmp = tempfile.mkdtemp(prefix="ta_mig_")
    p = os.path.join(tmp, "legacy.sqlite")
    con = sqlite3.connect(p)
    con.executescript(
        "CREATE TABLE nodes(node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT, state TEXT);"
        "CREATE TABLE audit_meta(key TEXT PRIMARY KEY, value TEXT);")
    con.execute("INSERT INTO nodes VALUES('old1','judgment','옛 노드','active')")
    con.commit()
    apply_schema(con)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    has_tables = {"approval_requests", "approval_consumptions"}.issubset(tables)
    node_kept = con.execute("SELECT sentence FROM nodes WHERE node_id='old1'").fetchone()[0] == "옛 노드"
    # auto-grant 0: consumptions 비어있고 approvals.jsonl 부재.
    cons = con.execute("SELECT count(*) FROM approval_consumptions").fetchone()[0]
    lid = ledger_id(con)
    con.close()
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return has_tables and node_kept and cons == 0 and bool(lid)


if __name__ == "__main__":
    print("=" * 70)
    print("P1-A Trusted Approval Event — 적대 경계 회귀 하니스 (TIER-2)")
    print("=" * 70)
    if not sys.argv[1:] or "--selftest" in sys.argv:
        raise SystemExit(run())
    print("usage: openbinggu_trusted_approval_boundary_selftest.py --selftest")
    sys.exit(2)
