#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trusted Approval Event — 적대 경계 회귀 하니스 (TIER-2).

정본 설계: docs/BINGGUPACK_TRUSTED_APPROVAL_EVENT_RFC.md §24 (+ 2026-07-13 MCP save approval 제거).
전부 temp home(격리)·운영 ~/.binggupack 미접촉. handle_tool(MCP) + trusted_approval(core) 직접 구동.

2026-07-13 개정: MCP mutation 표면의 approval 요청/소비 배선은 제거됐다(owner 결정 · 저장 게이트
"preview + 사람의 save n 입력" 단일 원칙 후속). 따라서 이 하니스의 MCP 벡터는 "approval 로 소비된다"가
아니라 "approval 이 있어도 절대 승격되지 않는다(fail-closed)"를 회귀로 봉인한다. approval core 의
소비 semantics(정확 1회·replay·binding·expiry·tombstone)는 CLI/HAG 경로 하니스가 담당
(binggu_p1b_mutation_closure_selftest · binggu_trusted_approval_binding_characterization_selftest ·
tests/test_trusted_approval_e2e.py 의 CLI --approval-id 흐름).

공격 커버: no-provider fail-closed · MCP 는 PENDING 요청 미발행 · owner mint approval 도 MCP 승격 0
(소비 0·approval_id_ignored) · dry-run fail-closed 안내 정합 · concurrent double-consume(core 정확 1) ·
env-var spoof · nonce 응답 미노출 · summary PII residue · harvest fail-closed ·
6-core actor sweep(TAE-2) · migration 비파괴 · control-char binding ·
MCP TOOLS 파일write 도구 부재 · 운영 ledger sentinel.

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

        # ── 2) provider 구성돼도 MCP 는 approval 요청/소비 경로가 없다(2026-07-13 제거) ──
        enable_provider()
        tr = rr("deprecate", {"index": 1, "id8": id8A, "reason": "오판이라 기각", "dry_run": False,
                              "confirm": "DEPRECATE 1 " + id8A})
        ck("MCP write 시도 → G4_no_auto(write0·request_id 미발행)",
           tr.get("executed_write") is False and tr.get("reason") == "G4_no_auto"
           and not tr.get("request_id"))
        db = open_g3(os.path.join(home, "ledger.sqlite"))
        try:
            n_req = db.con.execute("SELECT count(*) FROM approval_requests").fetchone()[0]
        finally:
            db.close()
        ck("MCP 는 PENDING approval 요청도 만들지 않는다(0행)", n_req == 0)
        ck("MCP 차단 → nA 여전히 active", _state(home, "nA") == "active")

        # ── 3) owner 가 진짜 mint 한 approval 이라도 MCP 표면은 소비/승격 불가 ────────
        #    (구 P1-A 경로 재현: 요청을 owner 측에서 직접 등록 + mint → MCP 에 approval_id 제시)
        payload = {"index": 1, "id8": id8A, "reason": "오판이라 기각"}
        digest = ta.canonical_payload_digest("deprecate", payload)
        db = open_g3(os.path.join(home, "ledger.sqlite"))
        try:
            from binggupack.storage.schema import ledger_id as _lid
            lid = _lid(db.con)
            rid = ta.compute_request_id("deprecate", digest, lid)
            ta.upsert_request(db.con, rid, ta.PROTOCOL_VERSION, "deprecate", digest, lid,
                              ta.summary_for("deprecate", payload, lid), time.time(), 900, 8)
            req = ta.get_request(db.con, rid)
        finally:
            db.close()
        ck("owner 측 요청 등록됨(픽스처)", req is not None and req["operation"] == "deprecate")
        ta.mint_approval(home, req, 900, time.time())
        tr = rr("deprecate", {"index": 1, "id8": id8A, "reason": "오판이라 기각", "dry_run": False,
                              "confirm": "DEPRECATE 1 " + id8A, "approval_id": rid})
        ck("owner mint approval + approval_id 제시 → 승격 0(G4_no_auto·write0)",
           tr.get("executed_write") is False and tr.get("reason") == "G4_no_auto"
           and tr.get("approval_id_ignored") is True)
        ck("approval 미소비(consumptions 0행)", _consumption_count(home) == 0)
        ck("approval 제시에도 nA 여전히 active", _state(home, "nA") == "active")
        ck("nonce 응답 미노출", "nonce" not in json.dumps(tr, ensure_ascii=False)
           or tr.get("nonce") is None)

        # ── 4) dry-run fail-closed 안내 정합 — write_available=False·human_save_required·CLI 안내 ──
        tr = rr("deprecate", {"index": 1, "id8": id8A, "dry_run": True})
        ck("dry-run fail-closed 안내(human_save_required·use_local_cli)",
           tr.get("write_available") is False and tr.get("reason") == "human_save_required"
           and tr.get("owner_action") == "use_local_cli")

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

        # ── 7) id-guessing — 아무 approval_id 나 제시(승인 없음) → 무시 + write0 ──────
        seed_node("nE", "이 사업은 리스크가 커서 보류한다")
        id8E = node_id8("nE")
        tr = rr("deprecate", {"index": 2, "id8": id8E, "reason": "추측", "dry_run": False,
                              "confirm": "DEPRECATE 2 " + id8E, "approval_id": "deadbeef" * 3})
        ck("id_guessing → approval_id 무시(G4_no_auto) · write0",
           tr.get("executed_write") is False and tr.get("reason") == "G4_no_auto"
           and tr.get("approval_id_ignored") is True and _state(home, "nE") == "active")

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
        # write-gated 핸들러(save/pair/deprecate/replace/mark/harvest)는 사람 앵커 없이는
        # fail-closed(approval_id 승격 경로 없음). 임의 파일write·셸 도구 0.
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


def _consumption_count(home):
    """approval_consumptions 행수 — MCP 가 approval 을 소비하지 않음을 단정하는 용도."""
    from binggupack.storage import open_g3
    db = open_g3(os.path.join(home, "ledger.sqlite"))
    try:
        return db.con.execute("SELECT count(*) FROM approval_consumptions").fetchone()[0]
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
    print("Trusted Approval Event — 적대 경계 회귀 하니스 (TIER-2 · MCP 승격 배선 제거 반영)")
    print("=" * 70)
    if not sys.argv[1:] or "--selftest" in sys.argv:
        raise SystemExit(run())
    print("usage: openbinggu_trusted_approval_boundary_selftest.py --selftest")
    sys.exit(2)
