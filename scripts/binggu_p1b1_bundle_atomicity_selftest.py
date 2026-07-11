# -*- coding: utf-8 -*-
"""P1-B.1 — exact membership + crash-atomic single COMMIT + receipt/archive 회귀 게이트.

외부 owner review 의 H1(silent shrink)·H2(partial write)·M1(consumed retry receipt) 봉인을 검증한다.
  membership 7 : 선택 중 하나라도 invalid/dup → 전체 BLOCK(request/write/consume 0·sources 보존).
  crash 5      : subprocess + os._exit(91) hard crash 로 단일 COMMIT 경계의 원자성 증명(handled 예외 아님).
  receipt/arch 6: Contract-8 original receipt · archive=idempotent post-commit reconciliation.

CLI:  python binggu_p1b1_bundle_atomicity_selftest.py [--selftest]
내부: --crash-child <home> <staging> <snap> <ledger> <rid> <csv_intents>  (subprocess 전용 · 직접 호출 금지)
권장: BINGGU_SEMANTIC_OFF=1 (worktree Ollama 격리 · CI 재현).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

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
from binggupack.safety import trusted_approval as ta  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import open_g3  # noqa: E402

NOW = 1_900_000_000
# explicit=False 자동캡처 후보 1건씩(probe 로 확인된 유효 판단 문장)
VALID = [
    "이 계약은 조건이 유리하여 진행하기로 결정했다.",
    "신규 거래처는 항상 신용조사를 먼저 한다.",
    "이 방침은 다음 분기에 재검토하기로 결정했다.",
    "이 입찰은 마진이 낮아 보류하기로 결정했다.",
]


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


def _enable(home):
    os.makedirs(home, exist_ok=True)
    with open(ta.config_path(home), "w", encoding="utf-8") as f:
        json.dump({"enabled": True}, f)


def _approve(db, home, rid):
    req = ta.get_request(db.con, rid)
    ta.mint_approval(home, req, 900, time.time())


def _setup(root, name):
    home = os.path.join(root, name)
    staging = os.path.join(home, "hosted_inbox")
    snap = os.path.join(home, "snapshots")
    os.makedirs(staging, exist_ok=True)
    os.makedirs(snap, exist_ok=True)
    _enable(home)
    return home, staging, snap, os.path.join(home, "ledger.sqlite")


def _counts(db):
    q = db.con.execute
    return {
        "nodes": q("SELECT count(*) FROM nodes").fetchone()[0],
        "edges": q("SELECT count(*) FROM edges").fetchone()[0],
        "requests": q("SELECT count(*) FROM approval_requests").fetchone()[0],
        "consumptions": q("SELECT count(*) FROM approval_consumptions").fetchone()[0],
        "consumed": q("SELECT count(*) FROM approval_consumptions WHERE state='consumed'").fetchone()[0],
        "consuming": q("SELECT count(*) FROM approval_consumptions WHERE state='consuming'").fetchone()[0],
    }


def _src(staging, iid):
    return os.path.isfile(os.path.join(staging, iid + ".json"))


def _archive_files(staging):
    d = os.path.join(staging, "_archive")
    return [f for f in os.listdir(d) if f.endswith(".processed.json")] if os.path.isdir(d) else []


# ═══════════════════════ subprocess crash child (직접 호출 금지) ═══════════════════════
def _crash_child(home, staging, snap, ledger, rid, csv):
    iids = [x for x in csv.split(",") if x]
    db = open_g3(ledger)
    H.commit_bundle(db, home, staging, iids, rid, snap, NOW)  # BINGGU_BUNDLE_FAILPOINT 에서 os._exit(91)
    db.close()
    sys.exit(0)   # failpoint 미발동(비정상) — 부모의 returncode==91 단언이 잡는다


def _run_crash(root, name, failpoint, n=2):
    home, staging, snap, ledger = _setup(root, name)
    db = open_g3(ledger)
    iids = [_mk(staging, VALID[i]) for i in range(n)]
    rid = H.commit_bundle(db, home, staging, iids, None, snap, NOW)["request_id"]
    _approve(db, home, rid)
    db.close()   # child 가 재오픈
    env = dict(os.environ, BINGGU_SEMANTIC_OFF="1", BINGGU_BUNDLE_FAILPOINT=failpoint)
    cp = subprocess.run([sys.executable, os.path.abspath(__file__), "--crash-child",
                         home, staging, snap, ledger, rid, ",".join(iids)],
                        env=env, capture_output=True, text=True)
    db = open_g3(ledger)   # crash 후 재오픈(WAL 복구)
    return db, home, staging, snap, ledger, rid, iids, cp, _counts(db)


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
        r = H.commit_bundle(db, home, staging, [good, bad], None, snap, NOW)
        c = _counts(db)
        ck("hosted_bundle_mixed_valid_and_%s_blocks_all" % (
               "missing" if tag == "missing" else tag),
           r["write"] == 0 and r["reason"] == "bundle_prevalidation_failed"
           and not r.get("request_id") and r.get("executed_write") is False
           and c["nodes"] == 0 and c["requests"] == 0 and c["consumptions"] == 0
           and _src(staging, good)                                   # 정상 sibling 보존(PENDING)
           and any(q["reason"] == exp_reason for q in r["quarantined"])
           and r["validated_count"] == 1 and r["selected_count"] == 2)
        db.close()

    # duplicate selection — 암묵적 dedupe 금지(명시 계약)
    home, staging, snap, ledger = _setup(root, "m_dup")
    db = open_g3(ledger)
    good = _mk(staging, VALID[0])
    r = H.commit_bundle(db, home, staging, [good, good], None, snap, NOW)
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
    rid = H.commit_bundle(db, home, staging, [iid], None, snap, NOW)["request_id"]
    _approve(db, home, rid)
    r = H.commit_bundle(db, home, staging, [iid], rid, snap, NOW)
    c = _counts(db)
    ck("hosted_bundle_intra_intent_partial_reject_blocks_all",
       r["write"] == 0 and r["reason"] == "bundle_prepare_failed" and c["nodes"] == 0
       and c["consumed"] == 0 and _src(staging, iid))
    db.close()

    # 서로 다른 두 intent 가 같은 판단 문장 선택 → 단일 txn node_id 충돌 없이 멱등 1회 저장(Fable5 R2)
    home, staging, snap, ledger = _setup(root, "sib")
    db = open_g3(ledger)
    ta_txt = "이번 분기 매출이 크게 늘었다. 신규 투자를 대폭 늘리기로 결정했다."
    tb_txt = "경쟁사가 가격을 내려서 위협이 커졌다. 신규 투자를 대폭 늘리기로 결정했다."
    ia, ib = _mk(staging, ta_txt), _mk(staging, tb_txt)
    rid = H.commit_bundle(db, home, staging, [ia, ib], None, snap, NOW)["request_id"]
    _approve(db, home, rid)
    r = H.commit_bundle(db, home, staging, [ia, ib], rid, snap, NOW)
    c = _counts(db)
    arch = _archive_files(staging)
    r2 = H.commit_bundle(db, home, staging, [ia, ib], rid, snap, NOW)  # 재시도 → already_consumed
    ck("hosted_bundle_sibling_same_sentence_dedup_saves_once",
       r["write"] == 1 and r["reason"] is None and c["nodes"] == 1 and c["consumed"] == 1
       and len(arch) == 2 and r2["reason"] == "already_consumed" and r2["write"] == 0)
    db.close()


# ═══════════════════════ crash atomicity 5 (single COMMIT · H2) ═══════════════════════
def _crash(ck, root):
    # before_commit → ledger write 0 · consume 0 · 예약 stale(consuming) · sources 보존
    db, home, staging, snap, ledger, rid, iids, cp, c = _run_crash(root, "cr_before", "before_commit")
    ck("hosted_bundle_process_kill_before_commit_zero_write",
       cp.returncode == 91 and c["nodes"] == 0 and c["consumed"] == 0
       and c["consuming"] == 1 and all(_src(staging, i) for i in iids))
    ck("hosted_bundle_crash_does_not_false_consume",
       c["consumed"] == 0 and _archive_files(staging) == [])
    db.close()

    # mid_apply → 첫 insert 후 crash · 단일 txn 미커밋 → 부분 write 0(one row 도 남지 않음)
    db, home, staging, snap, ledger, rid, iids, cp, c = _run_crash(root, "cr_mid", "mid_apply")
    ck("hosted_bundle_process_kill_mid_apply_zero_partial_write",
       cp.returncode == 91 and c["nodes"] == 0 and c["consumed"] == 0
       and all(_src(staging, i) for i in iids))
    db.close()

    # after_commit → COMMIT 후 archive 전 crash → ledger 전체 write + consumed receipt · sources 아직 staging
    db, home, staging, snap, ledger, rid, iids, cp, c = _run_crash(root, "cr_after", "after_commit")
    ck("hosted_bundle_process_kill_after_commit_full_write",
       cp.returncode == 91 and c["nodes"] == len(iids) and c["consumed"] == 1
       and all(_src(staging, i) for i in iids) and _archive_files(staging) == [])
    db.close()

    # stale reservation recoverable — before_commit 예약을 lease 만료로 aging → takeover 재실행 성공
    db, home, staging, snap, ledger, rid, iids, cp, c = _run_crash(root, "cr_stale", "before_commit")
    r_live = H.commit_bundle(db, home, staging, iids, rid, snap, NOW)   # 즉시 재시도 = lease 유효
    live_locked = r_live["write"] == 0 and r_live["reason"] == "approval_in_progress"
    db.con.execute("UPDATE approval_consumptions SET reserved_at=? WHERE state='consuming'",
                   (str(time.time() - 130),))   # LEASE_SECONDS(120) 초과로 aging
    db.con.commit()
    r_rec = H.commit_bundle(db, home, staging, iids, rid, snap, NOW)
    c2 = _counts(db)
    ck("hosted_bundle_stale_reservation_recoverable",
       live_locked and r_rec["write"] == 1 and c2["nodes"] == len(iids) and c2["consumed"] == 1)
    db.close()


# ═══════════════════════ receipt / archive 6 (Contract-8 · §4·§5) ═══════════════════════
def _receipt_archive(ck, root):
    # 정상 성공 → 재시도 original receipt(archive 후)
    home, staging, snap, ledger = _setup(root, "ra_basic")
    db = open_g3(ledger)
    iids = [_mk(staging, VALID[0]), _mk(staging, VALID[1])]
    rid = H.commit_bundle(db, home, staging, iids, None, snap, NOW)["request_id"]
    _approve(db, home, rid)
    r1 = H.commit_bundle(db, home, staging, iids, rid, snap, NOW)
    r2 = H.commit_bundle(db, home, staging, iids, rid, snap, NOW)   # archive 후 재시도
    ck("hosted_bundle_retry_returns_original_receipt",
       r1["write"] == 1 and r2["write"] == 0 and r2["reason"] == "already_consumed"
       and r2["receipt"]["request_id"] == r1["receipt"]["request_id"] == rid)
    ck("hosted_bundle_retry_after_archive_returns_receipt",
       all(not _src(staging, i) for i in iids)
       and r2["receipt"]["node_ids"] == r1["receipt"]["node_ids"] and r2["receipt"]["node_ids"])
    db.close()

    # archive 실패 주입 → ledger 성공 불변 · source 보존 → 재시도 reconcile only · second write 0 · idempotent
    home, staging, snap, ledger = _setup(root, "ra_recon")
    db = open_g3(ledger)
    iids = [_mk(staging, VALID[0]), _mk(staging, VALID[1])]
    rid = H.commit_bundle(db, home, staging, iids, None, snap, NOW)["request_id"]
    _approve(db, home, rid)
    _orig = H._archive_member

    def _boom_arch(*a, **k):
        raise OSError("injected archive disk-full")
    try:
        H._archive_member = _boom_arch
        r1 = H.commit_bundle(db, home, staging, iids, rid, snap, NOW)   # write 1 · archive 실패
    finally:
        H._archive_member = _orig
    c1 = _counts(db)
    ck("hosted_bundle_archive_failure_preserves_source",
       r1["write"] == 1 and r1.get("archive_pending") and len(r1["archive_pending"]) == len(iids)
       and all(_src(staging, i) for i in iids) and c1["consumed"] == 1)

    r2 = H.commit_bundle(db, home, staging, iids, rid, snap, NOW)   # 재시도 = reconcile
    c2 = _counts(db)
    ck("hosted_bundle_archive_failure_no_second_write",
       r2["reason"] == "already_consumed" and r2["write"] == 0 and c2["nodes"] == c1["nodes"])
    ck("hosted_bundle_retry_before_archive_reconciles_only",
       all(not _src(staging, i) for i in iids) and r2["receipt"]["request_id"] == rid)

    r3 = H.commit_bundle(db, home, staging, iids, rid, snap, NOW)   # 또 재시도(멱등)
    ck("hosted_bundle_archive_reconciliation_idempotent",
       r3["reason"] == "already_consumed" and r3["write"] == 0
       and len(_archive_files(staging)) == len(iids))
    db.close()


def selftest():
    print("=" * 74)
    print("P1-B.1 — exact membership + crash-atomic + receipt/archive selftest (temp 격리)")
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
        print("\n-- crash atomicity 5 (single COMMIT · H2 partial write 봉인 · os._exit) --")
        _crash(ck, root)
        print("\n-- receipt / archive 6 (Contract-8 · M1 · post-commit reconciliation) --")
        _receipt_archive(ck, root)
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
        _crash_child(*sys.argv[2:8])
        sys.exit(0)
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(selftest())
    print("usage: binggu_p1b1_bundle_atomicity_selftest.py [--selftest]")
    sys.exit(2)
