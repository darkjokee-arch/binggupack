#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BingguPack Phase 4 — reviewer/confirmed flow synthetic selftest (DESIGN→검증, sandbox only).

기준: docs/BINGGUPACK_PHASE4_REVIEWER_CONFIRMED_FLOW_DESIGN.md
흐름: candidate → review_pending → [token 검증 + reviewer] → CONFIRM_ALLOWED(preview)
      → (owner approval 후보) → confirmed plan. **confirmed 실제 생성/apply/promote 0.**

재사용(무수정 import):
  - reviewer 토큰 계층 — issue_token / verify_token / token_to_access / ISSUER_OWNER (20/20)
  - review_resolver_sandbox.ReviewResolver (resolve=preview, try_apply=HOLD)
  - confirmed_governance.evaluate_confirm (간접: ReviewResolver 내부)
  - staging_write_selftest: StagingDB / staging_apply / base_pack / OPERATING_PATHS (C8 rollback·store 불변)

불변: confirmed_created=0 · applied=0 · promoted=0 · upload=0 · push=0 · neo4j=0 ·
      운영 store write 0 · raw token/PII/private path 출력 0(id·reason_code·hash만).

CLI: python openbinggu_phase4_reviewer_confirmed_selftest.py [--selftest]
"""
import os
import sys
import json
import shutil
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_reviewer_auth_session_selftest import (  # noqa: E402
    issue_token, verify_token, token_to_access, ISSUER_OWNER,
)
from openbinggu_review_resolver_sandbox import ReviewResolver  # noqa: E402
from openbinggu_staging_write_selftest import (  # noqa: E402
    StagingDB, staging_apply, base_pack, OPERATING_PATHS, _hash,
)

REVIEW_SCOPE = "review_decision:preview"
NOW = 1000


def _wal_checkpoint(con):
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.commit()


def _drop_wal_shm(p):
    for ext in ("-wal", "-shm"):
        f = p + ext
        if os.path.exists(f):
            os.remove(f)


def review_preview(token, queue_item, decision, resolver, evidence_store,
                   revocation, seen_nonces, approval_grant=False):
    """token 검증 → access(scope) → ReviewResolver.resolve(preview). confirmed/apply 0."""
    ok, rc, _tid = verify_token(token, NOW, revocation, seen_nonces)
    if not ok:
        return {"verdict": "BLOCK", "reason_code": rc, "stage": "token", "confirmed_created": 0}
    access, arc = token_to_access(token, "review_decision", None, approval_grant)
    if access is None:
        return {"verdict": "BLOCK", "reason_code": arc, "stage": "scope", "confirmed_created": 0}
    reviewer = {"user_root": access["user_root"], "actor": access["actor_kind"],
                "owner_approved": access["approval"]}
    r = resolve = resolver.resolve(queue_item, decision, reviewer, evidence_store)
    r["stage"] = "resolve"
    return r


def _qitem(iid="i1", ur="user_a", refs=None, layer="objective", status="review_pending"):
    return {"item_id": iid, "user_root": ur, "status": status,
            "evidence_refs": refs or ["EV_ok"], "layer": layer}


def _tok(role="reader", scope=(REVIEW_SCOPE,), issuer=ISSUER_OWNER, issued=900,
         expires=2000, nonce=None, subject="reader:codex", ur="user_a"):
    nonce = nonce if nonce is not None else "n_" + _hash(str(role) + str(scope) + str(issued))
    return issue_token(subject, ur, role, scope, ("private",), issued, expires, nonce, issuer=issuer)


def _selftest():
    print("=" * 80)
    print("PREVIEW ONLY: no changes will be applied "
          "(confirmed_created=0 / applied=0 / promoted=0 / upload=0)")
    print("BingguPack Phase 4 — reviewer/confirmed flow selftest (synthetic, confirmed_created=0)")
    print("=" * 80)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ev = {"EV_ok": {"user_root": "user_a", "layer": "objective"}}
    R = ReviewResolver()
    rev, seen = set(), set()
    results = []
    leak_blobs = []

    def rec(cid, name, ok):
        results.append((cid, name, "PASS" if ok else "FAIL"))

    # C1 no token → deny
    r = review_preview(None, _qitem(), "confirm", R, ev, rev, seen)
    leak_blobs.append(r)
    rec("C1", "no token → preview deny", r["verdict"] == "BLOCK" and r["reason_code"] == "no_token")

    # C2 invalid issuer → deny
    r = review_preview(_tok(nonce="n_c2", issuer="reader:self"), _qitem(), "confirm", R, ev, rev, seen)
    leak_blobs.append(r)
    rec("C2", "invalid issuer → deny", r["verdict"] == "BLOCK" and r["reason_code"] == "token_issuer_invalid")

    # C3 expired / revoked → deny
    r_exp = review_preview(_tok(nonce="n_c3a", expires=500), _qitem(), "confirm", R, ev, rev, seen)
    t_rev = _tok(nonce="n_c3b")
    rev.add(t_rev["claim"]["token_id"])
    r_rev = review_preview(t_rev, _qitem(), "confirm", R, ev, rev, seen)
    leak_blobs += [r_exp, r_rev]
    rec("C3", "expired/revoked → deny",
        r_exp["reason_code"] == "token_expired" and r_rev["reason_code"] == "token_revoked")

    # C4 reviewer scope mismatch → deny (scope에 review_decision:preview 없음)
    r = review_preview(_tok(scope=("read",), nonce="n_c4"), _qitem(), "confirm", R, ev, rev, seen)
    leak_blobs.append(r)
    rec("C4", "reviewer scope mismatch → deny", r["verdict"] == "BLOCK" and r["reason_code"] == "scope_deny")

    # C5 candidate → review_pending preview 가능
    #   reader 는 confirm 불가(G4: 자동/reader confirmed 금지, 사람 필수). review_pending 항목의
    #   reject/defer preview 는 가능(실 apply 0). confirm preview(CONFIRM_ALLOWED)는 owner = C6.
    r_reject = review_preview(_tok(role="reader", nonce="n_c5a", ur="user_a"),
                              _qitem(ur="user_a"), "reject", R, ev, rev, seen)
    r_reader_confirm = review_preview(_tok(role="reader", nonce="n_c5b", ur="user_a"),
                                      _qitem(ur="user_a"), "confirm", R, ev, rev, seen)
    leak_blobs += [r_reject, r_reader_confirm]
    rec("C5", "review_pending preview 가능(reader reject preview) + reader confirm 차단",
        r_reject["verdict"] == "PREVIEW" and r_reject.get("confirmed_created", 0) == 0
        and r_reader_confirm["verdict"] == "FAIL"
        and "G4_no_auto_confirm" in str(r_reader_confirm.get("reason_code", "")))

    # C6 owner approval 있어도 confirmed_created=0 (owner 토큰 + approval, preview까지만)
    r = review_preview(_tok(role="owner", subject="cli:local", nonce="n_c6", ur="user_a"),
                       _qitem(ur="user_a"), "confirm", R, ev, rev, seen, approval_grant=True)
    leak_blobs.append(r)
    rec("C6", "owner approval에도 confirmed_created=0",
        r["verdict"] == "CONFIRM_ALLOWED" and r.get("confirmed_created", 0) == 0)

    # C7 apply / promote 시도 → HOLD/BLOCK
    ta = R.try_apply("i1", {"user_root": "user_a", "actor": "human"})
    leak_blobs.append(ta)
    rec("C7", "apply/promote 시도 → HOLD",
        ta["verdict"] == "BLOCK" and ta["reason_code"] == "apply_to_graph_store_HOLD" and ta["applied"] == 0)

    # C8 rollback / checksum 원복 (confirmed apply 미래 실행 시 rollback 메커니즘, temp staging 모의)
    tmp = tempfile.mkdtemp(prefix="phase4_rb_")
    path = os.path.join(tmp, "rb.sqlite")
    snap_dir = os.path.join(tmp, "snap"); os.makedirs(snap_dir, exist_ok=True)
    db = StagingDB(path); _wal_checkpoint(db.con)
    before_ck = db.store_checksum()
    snap = os.path.join(snap_dir, "before.sqlite"); shutil.copy2(path, snap)
    staging_apply(db, base_pack(pack_id="p4_rb"), {"actor": "human"}, snap_dir); _wal_checkpoint(db.con)
    after_ck = db.store_checksum(); db.close()
    shutil.copy2(snap, path); _drop_wal_shm(path)
    db = StagingDB(path); _wal_checkpoint(db.con)
    roll_ck = db.store_checksum(); db.close()
    rec("C8", "rollback/checksum 원복", after_ck != before_ck and roll_ck == before_ck)

    # C9 operating_store_unchanged + raw_leak=0
    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    store_unchanged = (op_before == op_after)
    blob = json.dumps([results, leak_blobs, R.audit], ensure_ascii=False, default=str)
    needles = [os.path.expanduser("~"), BASE, "C:\\Users", "/Users/", "/home/", tmp]
    leak = sum(1 for nd in needles if nd and nd in blob)
    rec("C9", "operating_store_unchanged + raw_leak=0", store_unchanged and leak == 0)

    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, name, v in sorted(results):
        print(f"  [{'OK' if v == 'PASS' else 'X'}] {cid:>3} {name}")
    print("-" * 80)
    print(f"  confirmed_created={R.confirmed_created}  applied={R.applied}  promoted=0  upload=0 push=0 neo4j=0")
    print(f"  operating_store_unchanged={store_unchanged}  resolver_confirmed_created={R.confirmed_created}")
    gate = "GO" if (npass == len(results) and store_unchanged and R.confirmed_created == 0 and R.applied == 0) else "NO-GO"
    print(f"  RESULT: {npass}/{len(results)} PASS   GATE: {gate}")
    return 0 if gate == "GO" else 1


def main():
    if len(sys.argv) == 1 or "--selftest" in sys.argv:
        return _selftest()
    print("usage: python openbinggu_phase4_reviewer_confirmed_selftest.py --selftest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
