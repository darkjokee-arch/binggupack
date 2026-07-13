# -*- coding: utf-8 -*-
"""P1-B.1 — exact membership + crash-atomic single COMMIT + 멱등 재시도/archive 회귀 게이트.

외부 owner review 의 H1(silent shrink)·H2(partial write) 봉인 + save-n 개정(스펙 ③) 재시도
semantics(MUST_FIX 4)를 검증한다. 저장 게이트 = 사람 저장 게이트(ctx.actor=='human' +
confirm='SAVE <idx[,idx]>' 정확 일치) — 구 approval mint/consume/receipt 재사용 배선은 제거됐다.
  membership 7 : 선택 중 하나라도 invalid/dup → 전체 BLOCK(write 0 · sources 보존 · approval 0).
  crash 4      : subprocess + os._exit(91) hard crash 로 단일 COMMIT 경계의 원자성 증명(handled 예외 아님).
                 ★COMMIT 후 crash 재시도 = applied_registry 멱등(idempotent_already_applied ·
                 부분 write 0 · 중복 insert 0) + post-commit archive 수렴.
  retry/arch 6 : 재시도 계약 — archive 완료 후 재시도 = intent_not_found quarantine(원문 archive 보존) ·
                 archive 실패 시 재시도 = reconcile only(second write 0) · reconcile 멱등.

CLI:  python binggu_p1b1_bundle_atomicity_selftest.py [--selftest]
내부: --crash-child <home> <staging> <snap> <ledger> <csv_intents>  (subprocess 전용 · 직접 호출 금지)
권장: BINGGU_SEMANTIC_OFF=1 (worktree Ollama 격리 · CI 재현).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

os.environ.setdefault("BINGGU_SEMANTIC_OFF", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
_BASE = os.path.dirname(HERE)
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

import binggu_hosted_bundle as H  # noqa: E402
from openbinggu_save_intent_outbox_runner import (SCHEMA_VER, DEFAULT_TTL_S,  # noqa: E402
                                                  intent_hash, OPERATING_PATHS)
from openbinggu_deprecate_and_remind_g3 import open_g3  # noqa: E402

NOW = 1_900_000_000
# 사람 저장 게이트 ctx — commit_bundle 은 호출자(_resolve_human_ctx)가 판정한 actor 를 받는다(계약 11).
HUMAN = {"actor": "human", "actor_source": "cli_command"}
# explicit=False 자동캡처 후보 1건씩(probe 로 확인된 유효 판단 문장)
VALID = [
    "이 계약은 조건이 유리하여 진행하기로 결정했다.",
    "신규 거래처는 항상 신용조사를 먼저 한다.",
    "이 방침은 다음 분기에 재검토하기로 결정했다.",
    "이 입찰은 마진이 낮아 보류하기로 결정했다.",
]


def _confirm_for(n):
    """선택 n건(indices 기본 1..n)의 정확 confirm 문구."""
    return "SAVE " + ",".join(str(i) for i in range(1, n + 1))


def _mk(staging, text, idxs=(1,), created=NOW - 10, ttl=DEFAULT_TTL_S, schema_ver=SCHEMA_VER,
        confirm=None, tamper_text=False, malformed=False):
    """staging 에 hosted intent json 1건 작성. 각 인자로 invalid 종류 주입."""
    idxs = list(idxs)
    if confirm is None:
        confirm = "SAVE " + ",".join(str(i) for i in idxs)
    iid = intent_hash(text, idxs, confirm)   # intent_id 는 (원본 text, idxs, confirm) 기준
    p = os.path.join(staging, iid + ".json")
    if malformed:
        it = {"schema_ver": schema_ver, "text": text, "indices": "notalist",  # 필드 타입 위반
              "confirm": confirm, "intent_id": iid, "created_ts": created, "ttl_s": ttl,
              "source": "hosted"}
    else:
        it = {"schema_ver": schema_ver,
              "text": (text + " 변조됨") if tamper_text else text,   # tamper → 재해시 불일치
              "indices": idxs, "confirm": confirm, "intent_id": iid,
              "created_ts": created, "ttl_s": ttl, "source": "hosted"}
    with open(p, "w", encoding="utf-8") as f:
        json.dump(it, f, ensure_ascii=False)
    return iid


def _setup(root, name):
    home = os.path.join(root, name)
    staging = os.path.join(home, "hosted_inbox")
    snap = os.path.join(home, "snapshots")
    os.makedirs(staging, exist_ok=True)
    os.makedirs(snap, exist_ok=True)
    return home, staging, snap, os.path.join(home, "ledger.sqlite")


def _counts(db):
    q = db.con.execute

    def _safe(sql):
        """approval 테이블은 저장 경로에서 미생성일 수 있음(배선 제거) — 부재=0."""
        try:
            return q(sql).fetchone()[0]
        except Exception:
            return 0
    return {
        "nodes": q("SELECT count(*) FROM nodes").fetchone()[0],
        "edges": q("SELECT count(*) FROM edges").fetchone()[0],
        "requests": _safe("SELECT count(*) FROM approval_requests"),
        "consumptions": _safe("SELECT count(*) FROM approval_consumptions"),
    }


def _src(staging, iid):
    return os.path.isfile(os.path.join(staging, iid + ".json"))


def _archive_files(staging):
    d = os.path.join(staging, "_archive")
    return [f for f in os.listdir(d) if f.endswith(".processed.json")] if os.path.isdir(d) else []


# ═══════════════════════ subprocess crash child (직접 호출 금지) ═══════════════════════
def _crash_child(home, staging, snap, ledger, csv):
    iids = [x for x in csv.split(",") if x]
    db = open_g3(ledger)
    H.commit_bundle(db, home, staging, iids, HUMAN, _confirm_for(len(iids)),
                    snap, NOW)  # BINGGU_BUNDLE_FAILPOINT 에서 os._exit(91)
    db.close()
    sys.exit(0)   # failpoint 미발동(비정상) — 부모의 returncode==91 단언이 잡는다


def _run_crash(root, name, failpoint, n=2):
    home, staging, snap, ledger = _setup(root, name)
    db = open_g3(ledger)
    iids = [_mk(staging, VALID[i]) for i in range(n)]
    db.close()   # child 가 재오픈
    env = dict(os.environ, BINGGU_SEMANTIC_OFF="1", BINGGU_BUNDLE_FAILPOINT=failpoint)
    cp = subprocess.run([sys.executable, os.path.abspath(__file__), "--crash-child",
                         home, staging, snap, ledger, ",".join(iids)],
                        env=env, capture_output=True, text=True)
    db = open_g3(ledger)   # crash 후 재오픈(WAL 복구)
    return db, home, staging, snap, ledger, iids, cp, _counts(db)


# ═══════════════════════ membership 7 (exact binding · H1) ═══════════════════════
def _membership(ck, root):
    cases = [
        ("missing",             lambda st: "0" * 64,                          "intent_not_found"),
        ("expired",             lambda st: _mk(st, VALID[1], created=NOW - 10_000, ttl=1), "expired"),
        ("malformed",           lambda st: _mk(st, VALID[1], malformed=True), "malformed_intent"),
        ("schema_mismatch",     lambda st: _mk(st, VALID[1], schema_ver="v0-wrong"), "schema_mismatch"),
        ("intent_id_mismatch",  lambda st: _mk(st, VALID[1], tamper_text=True), "intent_id_mismatch"),
        ("confirm_mismatch",    lambda st: _mk(st, VALID[1], confirm="SAVE 2"), "confirm_phrase_mismatch"),
    ]
    for tag, mkbad, exp_reason in cases:
        home, staging, snap, ledger = _setup(root, "m_" + tag)
        db = open_g3(ledger)
        good = _mk(staging, VALID[0])
        bad = mkbad(staging)
        r = H.commit_bundle(db, home, staging, [good, bad], HUMAN, "SAVE 1,2", snap, NOW)
        c = _counts(db)
        ck("hosted_bundle_mixed_valid_and_%s_blocks_all" % (
               "missing" if tag == "missing" else tag),
           r["write"] == 0 and r["reason"] == "bundle_prevalidation_failed"
           and not r.get("request_id") and r.get("executed_write") is False
           and c["nodes"] == 0 and c["requests"] == 0 and c["consumptions"] == 0
           and _src(staging, good)                                   # 정상 sibling 보존(원문 잔류)
           and any(q["reason"] == exp_reason for q in r["quarantined"])
           and r["validated_count"] == 1 and r["selected_count"] == 2)
        db.close()

    # duplicate selection — 암묵적 dedupe 금지(명시 계약)
    home, staging, snap, ledger = _setup(root, "m_dup")
    db = open_g3(ledger)
    good = _mk(staging, VALID[0])
    r = H.commit_bundle(db, home, staging, [good, good], HUMAN, "SAVE 1,2", snap, NOW)
    c = _counts(db)
    ck("hosted_bundle_duplicate_selection_policy_explicit",
       r["write"] == 0 and r["reason"] == "bundle_prevalidation_failed"
       and any(q["reason"] == "duplicate_selection" for q in r["quarantined"])
       and c["nodes"] == 0 and c["requests"] == 0 and c["consumptions"] == 0 and _src(staging, good))
    db.close()


# ═══════════════════════ Fable5 재검증 회귀 2 (intra-intent shrink · sibling dedup) ═══════════════════════
def _fable5_regressions(ck, root):
    # intra-intent partial index rejection → 전체 BLOCK(silent subset shrink 봉인·Fable5 R1)
    home, staging, snap, ledger = _setup(root, "intra")
    db = open_g3(ledger)
    two = VALID[0] + " " + VALID[1]          # 후보 2개짜리 text
    iid = _mk(staging, two, idxs=(1, 2, 3))  # index 3 = 범위 밖(부분 거부)
    r = H.commit_bundle(db, home, staging, [iid], HUMAN, "SAVE 1", snap, NOW)
    c = _counts(db)
    ck("hosted_bundle_intra_intent_partial_reject_blocks_all",
       r["write"] == 0 and r["reason"] == "bundle_prepare_failed" and c["nodes"] == 0
       and c["consumptions"] == 0 and _src(staging, iid))
    db.close()

    # 서로 다른 두 intent 가 같은 판단 문장 선택 → 단일 txn node_id 충돌 없이 멱등 1회 저장(Fable5 R2)
    home, staging, snap, ledger = _setup(root, "sib")
    db = open_g3(ledger)
    ta_txt = "이번 분기 매출이 크게 늘었다. 신규 투자를 대폭 늘리기로 결정했다."
    tb_txt = "경쟁사가 가격을 내려서 위협이 커졌다. 신규 투자를 대폭 늘리기로 결정했다."
    ia, ib = _mk(staging, ta_txt), _mk(staging, tb_txt)
    r = H.commit_bundle(db, home, staging, [ia, ib], HUMAN, "SAVE 1,2", snap, NOW)
    c = _counts(db)
    arch = _archive_files(staging)
    # 재시도(같은 선택 · archive 완료 후) → intent_not_found quarantine · 재write 0(★재시도 semantics)
    r2 = H.commit_bundle(db, home, staging, [ia, ib], HUMAN, "SAVE 1,2", snap, NOW)
    ck("hosted_bundle_sibling_same_sentence_dedup_saves_once",
       r["write"] == 1 and r["reason"] is None and c["nodes"] == 1
       and len(arch) == 2 and r2["write"] == 0
       and r2["reason"] == "bundle_prevalidation_failed"
       and all(q["reason"] == "intent_not_found" for q in r2["quarantined"]))
    db.close()


# ═══════════════════════ crash atomicity 4 (single COMMIT · H2) ═══════════════════════
def _crash(ck, root):
    # before_commit → ledger write 0 · sources 보존 · archive 0
    db, home, staging, snap, ledger, iids, cp, c = _run_crash(root, "cr_before", "before_commit")
    ck("hosted_bundle_process_kill_before_commit_zero_write",
       cp.returncode == 91 and c["nodes"] == 0 and c["requests"] == 0
       and all(_src(staging, i) for i in iids))
    ck("hosted_bundle_crash_before_commit_no_archive", _archive_files(staging) == [])
    db.close()

    # mid_apply → 첫 insert 후 crash · 단일 txn 미커밋 → 부분 write 0(one row 도 남지 않음)
    db, home, staging, snap, ledger, iids, cp, c = _run_crash(root, "cr_mid", "mid_apply")
    ck("hosted_bundle_process_kill_mid_apply_zero_partial_write",
       cp.returncode == 91 and c["nodes"] == 0
       and all(_src(staging, i) for i in iids))
    db.close()

    # after_commit → COMMIT 후 archive 전 crash → ledger 전체 write · sources 아직 staging
    db, home, staging, snap, ledger, iids, cp, c = _run_crash(root, "cr_after", "after_commit")
    ck("hosted_bundle_process_kill_after_commit_full_write",
       cp.returncode == 91 and c["nodes"] == len(iids)
       and all(_src(staging, i) for i in iids) and _archive_files(staging) == [])
    # ★재시도 계약(MUST_FIX 4): COMMIT 후 crash 재시도 = applied_registry 멱등
    #   (idempotent_already_applied · 부분 write 0 · 중복 insert 0) + ⑥ archive 수렴.
    r_retry = H.commit_bundle(db, home, staging, iids, HUMAN, _confirm_for(len(iids)), snap, NOW)
    c2 = _counts(db)
    ck("hosted_bundle_after_commit_retry_idempotent_and_archive_converges",
       r_retry["write"] == 0 and r_retry["reason"] == "idempotent_already_applied"
       and c2["nodes"] == len(iids)                       # 중복 insert 0
       and all(not _src(staging, i) for i in iids)        # staging → archive 이동 수렴
       and len(_archive_files(staging)) == len(iids))
    db.close()


# ═══════════════════════ 재시도 / archive 6 (멱등 · §4·§5 reconciliation) ═══════════════════════
def _retry_archive(ck, root):
    # 정상 성공 → archive 후 재시도 = intent_not_found(원문 archive 보존) → 재적재 재시도 = idempotent
    home, staging, snap, ledger = _setup(root, "ra_basic")
    db = open_g3(ledger)
    iids = [_mk(staging, VALID[0]), _mk(staging, VALID[1])]
    r1 = H.commit_bundle(db, home, staging, iids, HUMAN, "SAVE 1,2", snap, NOW)
    r2 = H.commit_bundle(db, home, staging, iids, HUMAN, "SAVE 1,2", snap, NOW)   # archive 후 재시도
    ck("hosted_bundle_retry_after_archive_quarantines_not_rewrites",
       r1["write"] == 1 and r1["receipt"]["node_ids"]
       and r2["write"] == 0 and r2["reason"] == "bundle_prevalidation_failed"
       and all(q["reason"] == "intent_not_found" for q in r2["quarantined"])
       and len(_archive_files(staging)) == len(iids))
    # 동일 intent 재적재(내용 동일 → 같은 intent_id) 후 재시도 → applied_registry 멱등 · receipt 반환
    iids_re = [_mk(staging, VALID[0]), _mk(staging, VALID[1])]
    r3 = H.commit_bundle(db, home, staging, iids_re, HUMAN, "SAVE 1,2", snap, NOW)
    c3 = _counts(db)
    ck("hosted_bundle_restaged_retry_idempotent_no_second_write",
       iids_re == iids and r3["write"] == 0 and r3["reason"] == "idempotent_already_applied"
       and r3["receipt"]["node_ids"] == r1["receipt"]["node_ids"]
       and c3["nodes"] == 2)
    db.close()

    # archive 실패 주입 → ledger 성공 불변 · source 보존 → 재시도 reconcile only · second write 0 · idempotent
    home, staging, snap, ledger = _setup(root, "ra_recon")
    db = open_g3(ledger)
    iids = [_mk(staging, VALID[0]), _mk(staging, VALID[1])]
    _orig = H._archive_member

    def _boom_arch(*a, **k):
        raise OSError("injected archive disk-full")
    try:
        H._archive_member = _boom_arch
        r1 = H.commit_bundle(db, home, staging, iids, HUMAN, "SAVE 1,2", snap, NOW)   # write 1 · archive 실패
    finally:
        H._archive_member = _orig
    c1 = _counts(db)
    ck("hosted_bundle_archive_failure_preserves_source",
       r1["write"] == 1 and r1.get("archive_pending") and len(r1["archive_pending"]) == len(iids)
       and all(_src(staging, i) for i in iids) and c1["nodes"] == 2)

    r2 = H.commit_bundle(db, home, staging, iids, HUMAN, "SAVE 1,2", snap, NOW)   # 재시도 = reconcile
    c2 = _counts(db)
    ck("hosted_bundle_archive_failure_no_second_write",
       r2["reason"] == "idempotent_already_applied" and r2["write"] == 0
       and c2["nodes"] == c1["nodes"])
    ck("hosted_bundle_retry_before_archive_reconciles_only",
       all(not _src(staging, i) for i in iids)
       and len(_archive_files(staging)) == len(iids))

    r3 = H.commit_bundle(db, home, staging, iids, HUMAN, "SAVE 1,2", snap, NOW)   # 또 재시도(archive 후)
    ck("hosted_bundle_archive_reconciliation_idempotent",
       r3["write"] == 0 and r3["reason"] == "bundle_prevalidation_failed"
       and all(q["reason"] == "intent_not_found" for q in r3["quarantined"])
       and len(_archive_files(staging)) == len(iids) and _counts(db)["nodes"] == 2)
    db.close()


def selftest():
    print("=" * 74)
    print("P1-B.1 — exact membership + crash-atomic + 멱등 재시도/archive selftest (temp 격리)")
    print("=" * 74)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    checks = []

    def ck(name, ok):
        checks.append(bool(ok))
        print("  [%s] %s" % ("OK" if ok else "X", name))

    root = tempfile.mkdtemp(prefix="bgp_p1b1_")
    try:
        print("\n-- membership 7 (exact binding · H1 silent shrink 봉인) --")
        _membership(ck, root)
        print("\n-- Fable5 재검증 회귀 2 (intra-intent shrink · sibling node dedup) --")
        _fable5_regressions(ck, root)
        print("\n-- crash atomicity 4 (single COMMIT · H2 partial write 봉인 · os._exit) --")
        _crash(ck, root)
        print("\n-- 재시도 / archive 6 (applied_registry 멱등 · post-commit reconciliation) --")
        _retry_archive(ck, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("operating_store_unchanged", op_before == op_after)
    ck("temp_cleaned", not os.path.exists(root))

    npass = sum(checks)
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(checks)))
    gate = "GO" if npass == len(checks) else "NO-GO"
    print("GATE=%s" % gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--crash-child"]:
        _crash_child(*sys.argv[2:7])
        sys.exit(0)
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(selftest())
    print("usage: binggu_p1b1_bundle_atomicity_selftest.py [--selftest]")
    sys.exit(2)
