# -*- coding: utf-8 -*-
"""binggu_p1b_mutation_closure_selftest.py — P1-B Track A 통합(cross-cutting) selftest.

목적: 개별 파일 selftest(binggu_hosted_bundle · hag_sync_adapter · hosted_inbox · save_intent
runner · binggu.py CLI)가 각자 커버하는 항목을 재실행하지 않고, **여러 mutation 표면을 관통하는
공유 불변식**만 통합 검증한다. save-n 참조 바인딩 개정(스펙 ③) 이후 표면별 게이트:
  · hosted_bundle 저장 표면 = **사람 저장 게이트**(ctx.actor=='human' + confirm 'SAVE <idx[,idx]>'
    정확일치 — approval mint/consume 배선 없음)
  · hag import_edges(비-저장 mutation) 표면 = trusted-approval 프리미티브(binding_fields→digest→
    request_id→verify_event→reserve/finalize) **존치**

통합 관점(개별 selftest 와 중복 금지):
  A. 5-op binding digest 행렬 — 비-저장 mutation 5개 op(accept/unaccept/due/resolve/confirm_edges)에
     대해 (1) digest 결정성, (2) 바인딩 필드 1개만 바꿔도 request_id 변화(위조→무효), (3) op 프리픽스로
     같은 payload 라도 op 다르면 digest 다름(accept 승인을 unaccept 로 재사용 불가). — 순수 속성 테스트.
  B. 비대화형/env/문자열 우회의 **표면 간 균일성** — 동일한 env 우회가 bundle(사람 게이트)과
     import(approval) 양쪽에서 동일하게 BLOCK 됨(우회가 한 곳만 막히지 않음).
  C. 표면 간 end-to-end 계약 균일성 — 두 독립 표면이 공통으로 게이트 미통과 write 0 → 통과 시
     atomic 1회 write → 재시도 second write 0 을 지킴(bundle 재시도 = applied_registry 멱등 +
     archive 수렴 · import 재시도 = 소비 승인 재사용 불가).
  D. anti-forge 2종 — (import) evidence 위조 → digest 변화 → binding_mismatch·write 0(C1),
     (bundle) 선택 집합 팽창 → confirm-idx 바인딩 파괴 → confirm_phrase_mismatch·write 0(계약 2·3·4).

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

# bundle 표면(사람 저장 게이트)용 ctx — commit_bundle 은 호출자가 판정한 actor 를 받는다(계약 11).
HUMAN = {"actor": "human", "actor_source": "cli_command"}
READER = {"actor": "reader", "actor_source": "agent_session_unanchored"}


def _apr_count(dbx):
    """approval_requests 무증가 단정용(테이블 부재=0) — 저장 표면의 approval 배선 제거 증명."""
    try:
        return dbx.con.execute("SELECT count(*) FROM approval_requests").fetchone()[0]
    except Exception:
        return 0


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


def _approve(con, home, rid, approved_at):
    """owner 승인 시뮬 — CLI TTY 검증은 selftest 밖. mint 기본 채널(ship-guard 'test_double' 리터럴 회피).
    ★ mint 는 명시 논리 시각(approved_at)으로만 발행한다 — wall clock 을 내부에서 다시 읽지 않는다.
    verify_event 는 now 파라미터로만 `now < approved_at`(clock 역행/future)·`now > expires_at`(만료)를
    판정하고 내부 time.time() 을 쓰지 않으므로(production 불변), 호출측이 request_now <= approved_at <=
    verify_now < expires_at 관계를 논리 시계로 결정적으로 보장한다 — wall runtime(CI 부하) 과 무관.
    channel 은 verify_event 가 검증하지 않는 감사 메타라 기본값이 결정성에 영향 없다."""
    req = ta.get_request(con, rid)
    ta.mint_approval(home, req, 900, approved_at)


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


def _hag_request_rid(scc, ledp, home, request_now, approved_at):
    """1차 import(승인 미제시)로 PENDING 생성 → rid 회수 → owner mint(명시 approved_at).
    request_now = PENDING 생성(upsert_request) 논리 시각 · approved_at = 승인 mint 논리 시각.
    두 시각 모두 호출측이 명시 전달 — wall clock 재읽기 0(request_now <= approved_at 보장)."""
    rid = None
    try:
        import_confirmed_edges(scc, ledp, now=request_now, home=home)
    except SyncError as e:
        rid = getattr(e, "request_id", None)
    o = sqlite3.connect(ledp)
    try:
        _approve(o, home, rid, approved_at)
    finally:
        o.close()
    return rid


# ── B. env/문자열 우회의 표면 간 균일성 ─────────────────────────────────────────────
def _run_uniform_bypass(ck, root):
    # bundle 표면 = 사람 저장 게이트(ctx/confirm — env read 0) · import 표면 = approval(승인 미제시
    # 경로만 검증 → verify_event 는 approved_at 비교 전에 차단 → 단일 논리 시각으로 충분).
    now = int(time.time())
    # env 로 truthy 를 잔뜩 세팅해도 bundle 은 ctx 판정만 · import provider 는 파일 신호로만 발견.
    keep = {k: os.environ.get(k) for k in
            ("BINGGU_TRUSTED_APPROVAL", "BINGGU_APPROVAL_TOKEN", "BINGGU_TRUSTED_CLI",
             "BINGGU_STRICT_HUMAN_GATE", "CLAUDECODE")}
    try:
        os.environ.update({"BINGGU_TRUSTED_APPROVAL": "1", "BINGGU_APPROVAL_TOKEN": "x",
                           "BINGGU_TRUSTED_CLI": "1", "BINGGU_STRICT_HUMAN_GATE": "1",
                           "CLAUDECODE": "1"})
        # ---- B1: env truthy 로도 사람/승인 승격 불가 → 양 표면 fail-closed ----
        b_home = os.path.join(root, "b_noprov_home")
        os.makedirs(b_home, exist_ok=True)
        ck("B1_env_does_not_configure_provider", ta.provider_for(b_home) is None)
        # bundle 표면 — 비-human ctx(에이전트 세션 판정)는 env 무관 human_save_required
        bh = os.path.join(root, "b_bundle_noprov")
        staging = os.path.join(bh, "hosted_inbox")
        snap = os.path.join(bh, "snapshots")
        os.makedirs(staging, exist_ok=True)
        os.makedirs(snap, exist_ok=True)
        dbb = open_g3(os.path.join(bh, "ledger.sqlite"))
        i1 = _mk_intent(staging, "이 방식을 확정한다.", [1], now)
        r = commit_bundle(dbb, b_home, staging, [i1], READER, "SAVE 1", snap, now)
        ck("B1_bundle_env_cannot_grant_human_write0",
           r["write"] == 0 and r["reason"] == "human_save_required"
           and os.path.isfile(os.path.join(staging, i1 + ".json")) and _apr_count(dbb) == 0)
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

        # ---- B2: 사람 근거 미제시 → 양 표면 게이트 BLOCK(env 무효) ----
        p_home = os.path.join(root, "b_prov_home")
        _enable_provider(p_home)
        bh2 = os.path.join(root, "b_bundle_prov")
        staging2 = os.path.join(bh2, "hosted_inbox")
        snap2 = os.path.join(bh2, "snapshots")
        os.makedirs(staging2, exist_ok=True)
        os.makedirs(snap2, exist_ok=True)
        dbb2 = open_g3(os.path.join(bh2, "ledger.sqlite"))
        j1 = _mk_intent(staging2, "이 캐시 전략을 유지한다.", [1], now)
        # ctx 자체가 없으면(전달 실패/위조 dict 아님) fail-closed — request/approval mint 0
        r2 = commit_bundle(dbb2, p_home, staging2, [j1], None, "SAVE 1", snap2, now)
        ck("B2_bundle_no_human_ctx_write0_no_request",
           r2["write"] == 0 and r2["reason"] == "human_save_required"
           and not r2.get("request_id") and _apr_count(dbb2) == 0)
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

        # ---- B3: 문자열 우회 → bundle=confirm 정확일치 위반 · import=문자열은 승인 아님 ----
        bh3 = os.path.join(root, "b_bundle_confirm")
        staging3 = os.path.join(bh3, "hosted_inbox")
        snap3 = os.path.join(bh3, "snapshots")
        os.makedirs(staging3, exist_ok=True)
        os.makedirs(snap3, exist_ok=True)
        dbb3 = open_g3(os.path.join(bh3, "ledger.sqlite"))
        k1 = _mk_intent(staging3, "이 배포는 백업 후 진행한다.", [1], now)
        r3 = commit_bundle(dbb3, p_home, staging3, [k1], HUMAN, "SAVE 999", snap3, now)
        ck("B3_bundle_confirm_mismatch_write0",
           r3["write"] == 0 and r3["reason"] == "confirm_phrase_mismatch"
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


# ── C. 표면 간 end-to-end 계약 균일성(게이트 미통과 write0→통과 atomic→replay 0) ──
def _run_cross_surface_lifecycle(ck, root):
    # bundle 표면 = 사람 저장 게이트 — approval verify 가 없으므로 순수 논리 시계(고정 미래값)로
    # 충분(wall clock 재읽기 0). import 표면은 결정적 시계 패턴(base 1회 캡처·오프셋 고정 ·
    # request <= approval <= verify < replay < expiry) 그대로 존치 — PR#12 정본.
    base = int(time.time())
    approved_at = base - 5
    request_now = base - 6
    verify_now = base
    replay_now = base + 1
    home = os.path.join(root, "c_home")
    _enable_provider(home)

    # bundle 표면 — 사람 게이트 미통과 write 0 → 통과 atomic 1회 → 재시도 멱등(★재시도 semantics)
    NOWB = 2_000_000_000   # 고정 논리 시각(사람 게이트는 approval clock 없음)
    bh = os.path.join(root, "c_bundle")
    staging = os.path.join(bh, "hosted_inbox")
    snap = os.path.join(bh, "snapshots")
    os.makedirs(staging, exist_ok=True)
    os.makedirs(snap, exist_ok=True)
    db = open_g3(os.path.join(bh, "ledger.sqlite"))
    i1 = _mk_intent(staging, "이 입찰은 마진이 낮아 보류하기로 결정했다.", [1], NOWB)
    r_req = commit_bundle(db, home, staging, [i1], READER, "SAVE 1", snap, NOWB)
    n0 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    ck("C_bundle_nonhuman_gate_write0_no_request",
       r_req["write"] == 0 and r_req["reason"] == "human_save_required"
       and not r_req.get("request_id") and n0 == 0 and _apr_count(db) == 0
       and os.path.isfile(os.path.join(staging, i1 + ".json")))
    r_commit = commit_bundle(db, home, staging, [i1], HUMAN, "SAVE 1", snap, NOWB)
    n1 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    ck("C_bundle_human_confirm_atomic_exactly_one_write",
       r_commit["write"] == 1 and n1 == 1 and _apr_count(db) == 0)
    # 재시도 semantics(MUST_FIX 4): archive 완료 후 재시도 = intent_not_found quarantine(write 0 ·
    # 원문은 archive 보존) · 동일 intent 재적재 후 재시도 = applied_registry 멱등(idempotent · 재insert 0)
    r_replay = commit_bundle(db, home, staging, [i1], HUMAN, "SAVE 1", snap, NOWB + 1)
    n2 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    i1b = _mk_intent(staging, "이 입찰은 마진이 낮아 보류하기로 결정했다.", [1], NOWB)
    r_replay2 = commit_bundle(db, home, staging, [i1b], HUMAN, "SAVE 1", snap, NOWB + 2)
    n3 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    ck("C_bundle_replay_second_write_0",
       r_replay["write"] == 0 and r_replay["reason"] == "bundle_prevalidation_failed"
       and all(q["reason"] == "intent_not_found" for q in r_replay["quarantined"])
       and r_replay2["write"] == 0 and r_replay2["reason"] == "idempotent_already_applied"
       and n2 == n1 and n3 == n1)
    db.close()

    # import 표면 — approval 생명주기(존치)
    ledp, scc, ek = _fresh_hag(root, "c_hag")
    rid_h = _hag_request_rid(scc, ledp, home, request_now, approved_at)
    ck("C_import_request_only_write0_returns_request_id",
       bool(rid_h) and _edge_count(ledp) == 0)
    res = import_confirmed_edges(scc, ledp, now=verify_now, home=home, approval_id=rid_h)
    ck("C_import_approved_exactly_one_write",
       res.get("imported") == 1 and _edge_count(ledp) == 1)
    res2 = import_confirmed_edges(scc, ledp, now=replay_now, home=home, approval_id=rid_h)
    # 재시도: 첫 import 로 sync_edges.status='imported' → effective=∅ → 멱등 no_op(2차 write 0).
    # (동시성 already_consumed 경로는 hag 개별 selftest 가 커버 — 여기선 표면 관통 "second write 0"만.)
    ck("C_import_replay_second_write_0",
       res2.get("imported") == 0 and (res2.get("already_consumed") or res2.get("no_op"))
       and _edge_count(ledp) == 1)
    scc.close()


# ── D. anti-forge 2종 (evidence 위조 · 선택 집합 팽창) ─────────────────────────────
def _run_anti_forge(ck, root):
    # import 표면(D1)만 approval clock 사용 — 결정적 시계 패턴(approved_at=base-5 과거) 존치.
    base = int(time.time())
    approved_at = base - 5
    request_now = base - 6
    verify_now = base
    home = os.path.join(root, "d_home")
    _enable_provider(home)

    # D1: import evidence 위조 — evidence=[n_ev] 로 승인 발행 후 sync_edges evidence 를 팽창시키면
    #     effective payload digest 변화 → 재계산 rid≠승인 rid → binding_mismatch · write 0(C1).
    ledp, scc, ek = _fresh_hag(root, "d_forge", evidence=("n_ev",))
    rid = _hag_request_rid(scc, ledp, home, request_now, approved_at)   # evidence=[n_ev] 기준 승인
    scc.execute("UPDATE sync_edges SET evidence_refs=? WHERE edge_key=?",
                (json.dumps(["n_ev", "n_forged"]), ek))
    scc.commit()
    blk, reason = False, None
    try:
        import_confirmed_edges(scc, ledp, now=verify_now, home=home, approval_id=rid)
    except SyncError as e:
        reason = getattr(e, "reason", None)
        blk = reason == "binding_mismatch:request_id"
    scc.close()
    ck("D_import_evidence_forge_binding_mismatch_write0", blk and _edge_count(ledp) == 0)

    # D2: bundle 선택 집합 팽창 — 'SAVE 1'(선택 [i1])의 confirm 으로 {i1,i2} 저장 시도 →
    #     confirm-idx 정확 바인딩 파괴 → confirm_phrase_mismatch · write 0(계약 2·3·4).
    NOWB = 2_000_000_000
    bh = os.path.join(root, "d_bundle")
    staging = os.path.join(bh, "hosted_inbox")
    snap = os.path.join(bh, "snapshots")
    os.makedirs(staging, exist_ok=True)
    os.makedirs(snap, exist_ok=True)
    db = open_g3(os.path.join(bh, "ledger.sqlite"))
    i1 = _mk_intent(staging, "이 방식을 채택한다.", [1], NOWB)
    i2 = _mk_intent(staging, "이 계약은 조건이 불리해 포기한다.", [1], NOWB)
    n_before = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    r_expand = commit_bundle(db, home, staging, [i1, i2], HUMAN, "SAVE 1", snap, NOWB)  # 집합 팽창
    n_after = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    both_preserved = all(os.path.isfile(os.path.join(staging, x + ".json")) for x in (i1, i2))
    ck("D_bundle_selection_expansion_breaks_confirm_binding_write0",
       r_expand["write"] == 0 and r_expand["reason"] == "confirm_phrase_mismatch"
       and n_after == n_before and both_preserved and _apr_count(db) == 0)
    db.close()


# ── E. deterministic approval clock (wall-runtime 무관 · stale/expiry 거부) ────────────
def _run_deterministic_clock(ck, root):
    """승인 lifecycle 이 wall runtime 과 무관함을 논리 시계로 증명(실제 sleep 0). 고정 미래 논리값을
    쓰므로 wall clock time.time() 과 완전 무관하다(production verify_event/mint 은 now 파라미터만 사용).
    stale/expiry negative case 로 production clock 역행·만료 방어가 그대로 유지됨도 함께 증명한다."""
    home = os.path.join(root, "clk_home")
    _enable_provider(home)
    REQ = 2_000_000_000   # 고정 미래 논리 시각(wall clock 무관)

    # E1: request↔approval 사이 120초(논리)가 걸려도 verify_now>=approved_at 이면 정확 1회 import.
    #     "request 와 approval 사이 120초 경과"를 실제 sleep 없이 논리 시계로 시뮬레이션한다.
    approved_at = REQ + 120
    verify_now = approved_at + 1
    ledp, scc, ek = _fresh_hag(root, "clk_delay")
    rid = _hag_request_rid(scc, ledp, home, REQ, approved_at)
    res = import_confirmed_edges(scc, ledp, now=verify_now, home=home, approval_id=rid)
    ck("p1b_approval_clock_does_not_depend_on_wall_runtime",
       res.get("imported") == 1 and _edge_count(ledp) == 1)
    # 정확 승인 import write 1 이후 재시도(같은 rid) → second write 0
    res2 = import_confirmed_edges(scc, ledp, now=verify_now + 1, home=home, approval_id=rid)
    ck("p1b_clock_delayed_replay_second_write_0",
       res2.get("imported") == 0 and _edge_count(ledp) == 1)
    scc.close()

    # E2: verify_now < approved_at(stale/clock rollback) → approval_time_invalid · write 0.
    #     production clock 역행 방어가 그대로 유지됨을 증명(reason 완화 아님).
    ledp2, scc2, ek2 = _fresh_hag(root, "clk_stale")
    approved_at2 = REQ + 10
    stale_verify_now = REQ   # < approved_at2
    rid2 = _hag_request_rid(scc2, ledp2, home, REQ, approved_at2)
    blk, reason = False, None
    try:
        import_confirmed_edges(scc2, ledp2, now=stale_verify_now, home=home, approval_id=rid2)
    except SyncError as e:
        reason = getattr(e, "reason", None)
        blk = reason == "approval_time_invalid"
    scc2.close()
    ck("p1b_stale_verify_time_is_rejected", blk and _edge_count(ledp2) == 0)

    # E3: verify_now > expires_at(approved_at + ttl) → approval_expired · write 0(만료 방어 유지).
    ledp3, scc3, ek3 = _fresh_hag(root, "clk_expired")
    approved_at3 = REQ
    expired_verify_now = approved_at3 + 900 + 1   # mint ttl=900 → expires_at=approved_at+900
    rid3 = _hag_request_rid(scc3, ledp3, home, REQ, approved_at3)
    blk3, reason3 = False, None
    try:
        import_confirmed_edges(scc3, ledp3, now=expired_verify_now, home=home, approval_id=rid3)
    except SyncError as e:
        reason3 = getattr(e, "reason", None)
        blk3 = reason3 == "approval_expired"
    scc3.close()
    ck("p1b_expiry_blocks_stale_approval", blk3 and _edge_count(ledp3) == 0)


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
        print("\n-- E. deterministic approval clock (wall-runtime 무관 · stale/expiry 거부) --")
        _run_deterministic_clock(ck, root)
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
