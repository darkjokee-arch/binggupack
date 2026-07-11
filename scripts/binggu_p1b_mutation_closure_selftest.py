# -*- coding: utf-8 -*-
"""binggu_p1b_mutation_closure_selftest.py — P1-B Track A 통합(cross-cutting) selftest.

목적: 개별 파일 selftest(binggu_hosted_bundle · hag_sync_adapter · hosted_inbox · save_intent
runner · binggu.py CLI)가 각자 커버하는 항목을 재실행하지 않고, **여러 mutation 표면을 관통하는
공유 불변식**만 통합 검증한다. 즉 "같은 trusted-approval 프리미티브(binding_fields→digest→request_id
→verify_event→reserve/finalize)가 P1-B 의 모든 write 표면에서 동일하게 강제되는가"를 한 자리에서 증명.

통합 관점(개별 selftest 와 중복 금지):
  A. 5-op binding digest 행렬 — P1-B Track A 5개 op(accept/unaccept/due/resolve/confirm_edges)에 대해
     (1) digest 결정성, (2) 바인딩 필드 1개만 바꿔도 request_id 변화(위조→무효), (3) op 프리픽스로
     같은 payload 라도 op 다르면 digest 다름(accept 승인을 unaccept 로 재사용 불가). — 순수 속성 테스트.
  B. 비대화형/env/문자열 우회의 **표면 간 균일성** — 동일한 env·confirm 문자열 우회가 hosted_bundle
     저장 표면과 hag import_edges 표면 **양쪽에서 동일하게** BLOCK 됨(우회가 한 곳만 막히지 않음).
  C. 표면 간 end-to-end 계약 균일성 — 두 독립 표면(bundle·import)이 공통으로
     request-only(write 0)→owner 승인→atomic 1회 write→재시도 second write 0 을 지킴.
  D. anti-forge 2종 — (import) evidence 위조 → digest 변화 → binding_mismatch·write 0(C1),
     (bundle) 승인 후 집합 팽창(membership +1) → digest 변화 → 기존 승인 무효·write 0(계약 2·3).

정직 경계: 로컬 승인 assurance=L1(SECURITY.md). 전부 temp 격리 · 운영 ~/.binggupack 미접촉.
CLI: python scripts/binggu_p1b_mutation_closure_selftest.py [--selftest]   (temp 전용)
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
_BASE = os.path.dirname(HERE)
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)
sys.path.insert(0, os.path.join(HERE, "hybrid_agi"))

from binggupack.safety import trusted_approval as ta  # noqa: E402
from binggu_hosted_bundle import commit_bundle, intent_hash, SCHEMA_VER, DEFAULT_TTL_S  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import open_g3  # noqa: E402
from hag_sync_adapter import (  # noqa: E402
    import_confirmed_edges, edge_key, open_sync_db, SyncError,
)

# 운영 store 불변 감시(개별 selftest 와 동일 sentinel).
try:
    from openbinggu_save_intent_outbox_runner import OPERATING_PATHS
except Exception:  # noqa
    OPERATING_PATHS = []


# ── A. 5-op binding digest 행렬 (순수 속성) ────────────────────────────────────────
# op → (base payload, [바꿔서 request_id 가 변해야 하는 필드 변형들])
_LID = "ledger-identity-fixed-A"


def _rid(op, payload):
    dg = ta.canonical_payload_digest(op, payload)
    return ta.compute_request_id(op, dg, _LID)


_OP_MATRIX = {
    "accept": ({"index": 2, "id8": "aabbccdd", "reason": "유지"},
               [{"index": 3}, {"id8": "eeff0011"}, {"reason": "재검토로 변경"}]),
    "unaccept": ({"index": 2, "id8": "aabbccdd", "reason": "재검토"},
                 [{"index": 5}, {"id8": "99887766"}, {"reason": "다시 검토"}]),
    "due": ({"node_id": "node:CONV:aaa", "due_date": "2099-12-31"},
            [{"node_id": "node:CONV:bbb"}, {"due_date": "2100-01-01"}]),
    "resolve": ({"node_id": "node:CONV:aaa", "outcome": "성공", "reason": "완료"},
                [{"node_id": "node:CONV:ccc"}, {"outcome": "실패"}, {"reason": "보류"}]),
    "confirm_edges": ({"edges": [{"src": "n_ev", "dst": "n_j1",
                                  "rel": "supports_judgment", "evidence": ["n_ev"]}]},
                      [  # evidence 위조 · src 변경 · 집합 팽창(edge 추가) 전부 rid 를 바꿔야 함
                       {"edges": [{"src": "n_ev", "dst": "n_j1", "rel": "supports_judgment",
                                   "evidence": ["n_ev", "n_forged"]}]},
                       {"edges": [{"src": "n_other", "dst": "n_j1", "rel": "supports_judgment",
                                   "evidence": ["n_ev"]}]},
                       {"edges": [{"src": "n_ev", "dst": "n_j1", "rel": "supports_judgment",
                                   "evidence": ["n_ev"]},
                                  {"src": "n_st", "dst": "n_j1", "rel": "supports_judgment",
                                   "evidence": ["n_st"]}]}]),
}


def _run_binding_matrix(ck):
    for op, (base, mutations) in _OP_MATRIX.items():
        base_rid = _rid(op, base)
        # (1) 결정성 — 동일 payload(사본) → 동일 request_id
        import copy as _copy
        ck("A_%s_digest_deterministic" % op, _rid(op, _copy.deepcopy(base)) == base_rid)
        # (2) 필드/집합 변형 → request_id 변화(위조 무효)
        all_flip = True
        for m in mutations:
            mutated = _copy.deepcopy(base)
            mutated.update(m)
            all_flip = all_flip and (_rid(op, mutated) != base_rid)
        ck("A_%s_field_mutation_flips_request_id" % op, all_flip)
    # (3) op 프리픽스 — 같은 payload 라도 accept≠unaccept digest(승인 재사용 불가)
    shared = {"index": 2, "id8": "aabbccdd", "reason": "유지"}
    ck("A_op_prefix_separates_accept_vs_unaccept",
       ta.canonical_payload_digest("accept", shared)
       != ta.canonical_payload_digest("unaccept", shared))


# ── 공통 헬퍼 ──────────────────────────────────────────────────────────────────────
def _enable_provider(home):
    os.makedirs(home, exist_ok=True)
    with open(ta.config_path(home), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "ttl_seconds": 900, "pending_cap": 16}, f)


def _approve(con, home, rid):
    """owner 승인 시뮬 — CLI TTY 검증은 selftest 밖. mint 기본 채널(ship-guard 리터럴 회피).
    ★ authorize/verify_event 는 실제 time.time() 으로 검증하므로 mint 도 실제 시간(-5 여유)."""
    req = ta.get_request(con, rid)
    ta.mint_approval(home, req, 900, time.time() - 5)


def _mk_intent(staging, text, idxs, now):
    confirm = "SAVE " + ",".join(str(i) for i in idxs)
    it = {"schema_ver": SCHEMA_VER, "text": text, "indices": idxs, "confirm": confirm,
          "intent_id": intent_hash(text, idxs, confirm),
          "created_ts": int(now) - 10, "ttl_s": DEFAULT_TTL_S, "source": "hosted"}
    with open(os.path.join(staging, it["intent_id"] + ".json"), "w", encoding="utf-8") as f:
        json.dump(it, f, ensure_ascii=False)
    return it["intent_id"]


def _fresh_hag(root, name, evidence=("n_ev",)):
    """운영 ledger(nodes) + sync db(confirmed edge n_ev→n_j1 · evidence 지정)."""
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    ledp = os.path.join(d, "ledger.sqlite")
    lc = sqlite3.connect(ledp)
    lc.execute("CREATE TABLE nodes (node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT,"
               " candidate INTEGER DEFAULT 1, state TEXT DEFAULT 'active', content_hash TEXT)")
    lc.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?)", [
        ("n_ev", "evidence", "로그에 오류가 있다", 0, "active", "h1"),
        ("n_j1", "judgment", "이 입찰은 보류한다", 0, "active", "h3")])
    lc.commit()
    lc.close()
    scc = open_sync_db(os.path.join(d, "sync.sqlite"))
    ek = edge_key("n_ev", "n_j1", "supports_judgment")
    scc.execute("INSERT INTO sync_edges (edge_key,src_node_id,dst_node_id,relation,src_checksum,"
                "dst_checksum,kmap_version,evidence_refs,status,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ek, "n_ev", "n_j1", "supports_judgment", None, None, "v1",
                 json.dumps(list(evidence)), "confirmed", 0, 0))
    scc.commit()
    return ledp, scc, ek


def _edge_count(ledp):
    o = sqlite3.connect(ledp)
    try:
        return o.execute("SELECT count(*) FROM edges").fetchone()[0]
    finally:
        o.close()


def _hag_request_rid(scc, ledp, home):
    """1차 import(승인 미제시)로 PENDING 생성 → rid 회수 → owner mint."""
    rid = None
    try:
        import_confirmed_edges(scc, ledp, now=int(time.time()), home=home)
    except SyncError as e:
        rid = getattr(e, "request_id", None)
    o = sqlite3.connect(ledp)
    try:
        _approve(o, home, rid)
    finally:
        o.close()
    return rid


# ── B. env/문자열 우회의 표면 간 균일성 ─────────────────────────────────────────────
def _run_uniform_bypass(ck, root):
    now = time.time()
    # env 로 truthy 를 잔뜩 세팅해도 provider 는 파일 신호로만 발견(양 표면 공통).
    keep = {k: os.environ.get(k) for k in
            ("BINGGU_TRUSTED_APPROVAL", "BINGGU_APPROVAL_TOKEN", "BINGGU_TRUSTED_CLI",
             "BINGGU_STRICT_HUMAN_GATE")}
    try:
        os.environ.update({"BINGGU_TRUSTED_APPROVAL": "1", "BINGGU_APPROVAL_TOKEN": "x",
                           "BINGGU_TRUSTED_CLI": "1", "BINGGU_STRICT_HUMAN_GATE": "1"})
        # ---- B1: provider 미구성(config 없음) → 양 표면 fail-closed(env 무효) ----
        b_home = os.path.join(root, "b_noprov_home")
        os.makedirs(b_home, exist_ok=True)
        ck("B1_env_does_not_configure_provider", ta.provider_for(b_home) is None)
        # bundle 표면
        bh = os.path.join(root, "b_bundle_noprov")
        staging = os.path.join(bh, "hosted_inbox")
        snap = os.path.join(bh, "snapshots")
        os.makedirs(staging, exist_ok=True)
        os.makedirs(snap, exist_ok=True)
        dbb = open_g3(os.path.join(bh, "ledger.sqlite"))
        i1 = _mk_intent(staging, "이 방식을 확정한다.", [1], now)
        r = commit_bundle(dbb, b_home, staging, [i1], None, snap, now)
        ck("B1_bundle_env_no_provider_write0",
           r["write"] == 0 and r["reason"] == "provider_not_configured"
           and os.path.isfile(os.path.join(staging, i1 + ".json")))
        dbb.close()
        # import 표면 — 같은 env, provider 미구성
        ledp, scc, ek = _fresh_hag(root, "b_hag_noprov")
        blk = False
        try:
            import_confirmed_edges(scc, ledp, now=int(now), home=b_home)
        except SyncError as e:
            # provider 미구성 branch 는 err.reason 미설정(메시지만) — 문자열 폴백 동반.
            blk = (getattr(e, "reason", None) == "provider_not_configured"
                   or "provider_not_configured" in str(e))
        scc.close()
        ck("B1_import_env_no_provider_write0", blk and _edge_count(ledp) == 0)

        # ---- B2: provider 활성 · 승인 미제시 → 양 표면 approval_required(env 무효) ----
        p_home = os.path.join(root, "b_prov_home")
        _enable_provider(p_home)
        bh2 = os.path.join(root, "b_bundle_prov")
        staging2 = os.path.join(bh2, "hosted_inbox")
        snap2 = os.path.join(bh2, "snapshots")
        os.makedirs(staging2, exist_ok=True)
        os.makedirs(snap2, exist_ok=True)
        dbb2 = open_g3(os.path.join(bh2, "ledger.sqlite"))
        j1 = _mk_intent(staging2, "이 캐시 전략을 유지한다.", [1], now)
        r2 = commit_bundle(dbb2, p_home, staging2, [j1], None, snap2, now)
        ck("B2_bundle_provider_noapproval_pending_write0",
           r2["write"] == 0 and r2["reason"] == "approval_required" and r2["request_id"])
        dbb2.close()
        ledp2, scc2, ek2 = _fresh_hag(root, "b_hag_prov")
        blk2, rid_seen = False, None
        try:
            import_confirmed_edges(scc2, ledp2, now=int(now), home=p_home)
        except SyncError as e:
            blk2 = getattr(e, "reason", None) == "approval_required"
            rid_seen = getattr(e, "request_id", None)
        scc2.close()
        ck("B2_import_provider_noapproval_pending_write0",
           blk2 and bool(rid_seen) and _edge_count(ledp2) == 0)

        # ---- B3: confirm 문자열을 approval_id 로 → 양 표면 binding_mismatch(문자열 승인 아님) ----
        bh3 = os.path.join(root, "b_bundle_confirm")
        staging3 = os.path.join(bh3, "hosted_inbox")
        snap3 = os.path.join(bh3, "snapshots")
        os.makedirs(staging3, exist_ok=True)
        os.makedirs(snap3, exist_ok=True)
        dbb3 = open_g3(os.path.join(bh3, "ledger.sqlite"))
        k1 = _mk_intent(staging3, "이 배포는 백업 후 진행한다.", [1], now)
        r3 = commit_bundle(dbb3, p_home, staging3, [k1], "SAVE 1", snap3, now)
        ck("B3_bundle_confirm_string_not_approval_write0",
           r3["write"] == 0 and r3["reason"] in ("binding_mismatch:request_id", "approval_required")
           and os.path.isfile(os.path.join(staging3, k1 + ".json")))
        dbb3.close()
        ledp3, scc3, ek3 = _fresh_hag(root, "b_hag_confirm")
        blk3 = False
        try:
            import_confirmed_edges(scc3, ledp3, now=int(now), home=p_home,
                                   approval_id="CONFIRM IMPORT EDGES")
        except SyncError as e:
            blk3 = getattr(e, "reason", None) == "binding_mismatch:request_id"
        scc3.close()
        ck("B3_import_confirm_string_not_approval_write0", blk3 and _edge_count(ledp3) == 0)
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── C. 표면 간 end-to-end 계약 균일성(request-only→승인→atomic→replay 0) ──────────
def _run_cross_surface_lifecycle(ck, root):
    now = time.time()
    home = os.path.join(root, "c_home")
    _enable_provider(home)

    # bundle 표면
    bh = os.path.join(root, "c_bundle")
    staging = os.path.join(bh, "hosted_inbox")
    snap = os.path.join(bh, "snapshots")
    os.makedirs(staging, exist_ok=True)
    os.makedirs(snap, exist_ok=True)
    db = open_g3(os.path.join(bh, "ledger.sqlite"))
    i1 = _mk_intent(staging, "이 입찰은 마진이 낮아 보류하기로 결정했다.", [1], now)
    r_req = commit_bundle(db, home, staging, [i1], None, snap, now)
    rid = r_req["request_id"]
    n0 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    ck("C_bundle_request_only_write0_returns_request_id",
       r_req["write"] == 0 and rid and n0 == 0
       and os.path.isfile(os.path.join(staging, i1 + ".json")))
    _approve(db.con, home, rid)
    r_commit = commit_bundle(db, home, staging, [i1], rid, snap, now)
    n1 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    ck("C_bundle_approved_atomic_exactly_one_write", r_commit["write"] == 1 and n1 == 1)
    # 재시도(같은 rid) → second write 0(계약 8)
    r_replay = commit_bundle(db, home, staging, [i1], rid, snap, now)
    n2 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    ck("C_bundle_replay_second_write_0", r_replay["write"] == 0 and n2 == n1)
    db.close()

    # import 표면 — 동일 생명주기
    ledp, scc, ek = _fresh_hag(root, "c_hag")
    rid_h = _hag_request_rid(scc, ledp, home)
    ck("C_import_request_only_write0_returns_request_id",
       bool(rid_h) and _edge_count(ledp) == 0)
    res = import_confirmed_edges(scc, ledp, now=int(now), home=home, approval_id=rid_h)
    ck("C_import_approved_exactly_one_write",
       res.get("imported") == 1 and _edge_count(ledp) == 1)
    res2 = import_confirmed_edges(scc, ledp, now=int(now), home=home, approval_id=rid_h)
    # 재시도: 첫 import 로 sync_edges.status='imported' → effective=∅ → 멱등 no_op(2차 write 0).
    # (동시성 already_consumed 경로는 hag 개별 selftest 가 커버 — 여기선 표면 관통 "second write 0"만.)
    ck("C_import_replay_second_write_0",
       res2.get("imported") == 0 and (res2.get("already_consumed") or res2.get("no_op"))
       and _edge_count(ledp) == 1)
    scc.close()


# ── D. anti-forge 2종 (evidence 위조 · membership 팽창) ─────────────────────────────
def _run_anti_forge(ck, root):
    now = time.time()
    home = os.path.join(root, "d_home")
    _enable_provider(home)

    # D1: import evidence 위조 — evidence=[n_ev] 로 승인 발행 후 sync_edges evidence 를 팽창시키면
    #     effective payload digest 변화 → 재계산 rid≠승인 rid → binding_mismatch · write 0(C1).
    ledp, scc, ek = _fresh_hag(root, "d_forge", evidence=("n_ev",))
    rid = _hag_request_rid(scc, ledp, home)   # evidence=[n_ev] 기준 승인
    scc.execute("UPDATE sync_edges SET evidence_refs=? WHERE edge_key=?",
                (json.dumps(["n_ev", "n_forged"]), ek))
    scc.commit()
    blk, reason = False, None
    try:
        import_confirmed_edges(scc, ledp, now=int(now), home=home, approval_id=rid)
    except SyncError as e:
        reason = getattr(e, "reason", None)
        blk = reason == "binding_mismatch:request_id"
    scc.close()
    ck("D_import_evidence_forge_binding_mismatch_write0", blk and _edge_count(ledp) == 0)

    # D2: bundle membership 팽창 — {i1} 로 승인 후 {i1,i2} 로 저장 시도 → digest 변화 →
    #     기존 승인 무효 · write 0(계약 2·3).
    bh = os.path.join(root, "d_bundle")
    staging = os.path.join(bh, "hosted_inbox")
    snap = os.path.join(bh, "snapshots")
    os.makedirs(staging, exist_ok=True)
    os.makedirs(snap, exist_ok=True)
    db = open_g3(os.path.join(bh, "ledger.sqlite"))
    i1 = _mk_intent(staging, "이 방식을 채택한다.", [1], now)
    i2 = _mk_intent(staging, "이 계약은 조건이 불리해 포기한다.", [1], now)
    r_req = commit_bundle(db, home, staging, [i1], None, snap, now)   # {i1} 만 승인 요청
    rid_a = r_req["request_id"]
    _approve(db.con, home, rid_a)
    n_before = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    r_expand = commit_bundle(db, home, staging, [i1, i2], rid_a, snap, now)  # 집합 팽창
    n_after = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    both_preserved = all(os.path.isfile(os.path.join(staging, x + ".json")) for x in (i1, i2))
    ck("D_bundle_membership_expansion_invalidates_approval_write0",
       r_expand["write"] == 0
       and r_expand["reason"] in ("binding_mismatch:request_id", "approval_required")
       and n_after == n_before and both_preserved)
    db.close()


def selftest():
    print("=" * 74)
    print("P1-B mutation closure — 통합(cross-cutting) selftest · temp 격리 · 운영 store 접근 0")
    print("=" * 74)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    checks = []

    def ck(name, ok):
        checks.append(bool(ok))
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))

    root = tempfile.mkdtemp(prefix="bgp_p1b_closure_")
    try:
        print("\n-- A. 5-op binding digest 행렬 (결정성·필드변형→rid변화·op분리) --")
        _run_binding_matrix(ck)
        print("\n-- B. env/문자열 우회의 표면 간 균일성 (bundle ↔ import) --")
        _run_uniform_bypass(ck, root)
        print("\n-- C. 표면 간 계약 균일성 (request-only→승인→atomic→replay 0) --")
        _run_cross_surface_lifecycle(ck, root)
        print("\n-- D. anti-forge (evidence 위조 · membership 팽창) --")
        _run_anti_forge(ck, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("Z_운영_store_불변", op_before == op_after)
    ck("Z_temp_정리", not os.path.exists(root))

    ok = all(checks)
    print("-" * 74)
    print("=== %d/%d ===" % (sum(checks), len(checks)))
    print("RESULT: %d/%d PASS" % (sum(checks), len(checks)))
    print("GATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(selftest())
    print("usage: binggu_p1b_mutation_closure_selftest.py [--selftest]  (temp 전용)")
    sys.exit(2)
