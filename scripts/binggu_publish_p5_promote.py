"""BingguPack PC-mediated read 공유 — P7: candidate→active 승격 정식 모듈 (P5 임시 스크립트 정식화).

기준 커밋: caf5181 위.
owner 지시(2026-06-14 GO-P7):
- owner 명시 항목만 candidate→active 승격 가능(전체 자동 승격 금지).
- promote 전 ledger 백업/스냅샷 필수.
- checksum 변화 + audit ALLOW 기록 필수.
- evidence/node/hash 정합 깨지면 BLOCK(승격 0).
- 이미 active 항목 재승격 = **idempotent**(skip, 0 변경)로 고정.
- 실 업로드·DB insert(운영 외)·tag/release·Cloud 원본화 금지.

ledger write = candidate 플래그 해제(UPDATE)만, owner 명시 node_id 한정.
"""
from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openbinggu_deprecate_and_remind_g3 import open_g3

DEFAULT_LEDGER = os.path.join(os.path.expanduser("~"), ".binggupack", "ledger.sqlite")


# ── evidence ↔ node 1:1 정합 검증 (승격 전후 자동) ──────────────
def verify_evidence_linkage(db, node_ids=None):
    """각 대상 node ↔ evidence 1:1(evidence_supports) + sentence 일치. {ok, issues}."""
    issues = []
    if node_ids is None:
        rows = db.con.execute("SELECT node_id, sentence FROM nodes").fetchall()
    else:
        rows = []
        for nid in node_ids:
            r = db.con.execute("SELECT node_id, sentence FROM nodes WHERE node_id=?", (nid,)).fetchone()
            if r is None:
                issues.append({"node": nid, "issue": "node_not_found"})
            else:
                rows.append(r)
    # evidence_supports edge: source=evidence_id, target=node_id
    ev_sent = dict(db.con.execute("SELECT evidence_id, sentence FROM evidence").fetchall())
    for nid, nsent in rows:
        sup = db.con.execute(
            "SELECT source FROM edges WHERE relation='evidence_supports' AND target=?", (nid,)).fetchall()
        if len(sup) != 1:
            issues.append({"node": nid, "issue": "evidence_not_1to1", "linked": len(sup)})
            continue
        eid = sup[0][0]
        if eid not in ev_sent:
            issues.append({"node": nid, "issue": "evidence_missing", "evidence": eid})
        elif ev_sent[eid] != nsent:
            issues.append({"node": nid, "issue": "sentence_mismatch", "evidence": eid})
    return {"ok": not issues, "issues": issues}


# ── 승격 (owner 명시 node_id만 · 정합 BLOCK · idempotent) ────────
def promote(db, node_ids, ctx, pack_id="conv_promote"):
    """candidate→active. 정합 깨지면 BLOCK. 이미 active=idempotent skip. checksum+audit."""
    if ctx.get("actor") in ("auto", "reader"):
        return {"applied": False, "reason": "G4_no_auto", "promoted": [], "skipped_already_active": []}
    if not node_ids:
        return {"applied": False, "reason": "empty_selection", "promoted": [], "skipped_already_active": []}

    before = db.store_checksum()

    # 0) node 존재 확인 (owner 명시 node_id) — 미존재 먼저 BLOCK
    nf = [nid for nid in node_ids
          if db.con.execute("SELECT 1 FROM nodes WHERE node_id=?", (nid,)).fetchone() is None]
    if nf:
        db.audit_append(ctx.get("actor", "human"), "candidate_promote", pack_id, "BLOCK",
                        "node_not_found:%d" % len(nf), before, before)
        return {"applied": False, "reason": "node_not_found", "not_found": nf,
                "promoted": [], "skipped_already_active": []}

    # 1) 승격 전 정합 검증 (대상) — 깨지면 BLOCK(승격 0)
    pre = verify_evidence_linkage(db, node_ids)
    if not pre["ok"]:
        db.audit_append(ctx.get("actor", "human"), "candidate_promote", pack_id, "BLOCK",
                        "linkage_broken_pre:%d" % len(pre["issues"]), before, before)
        return {"applied": False, "reason": "linkage_broken_pre", "issues": pre["issues"],
                "promoted": [], "skipped_already_active": []}

    promoted, skipped = [], []
    for nid in node_ids:
        # 0단계에서 미존재 node 는 이미 BLOCK 처리됨 → 여기 row 는 항상 존재
        row = db.con.execute("SELECT candidate FROM nodes WHERE node_id=?", (nid,)).fetchone()
        if row[0] == 0:
            skipped.append(nid)              # idempotent — 이미 active
        else:
            db.con.execute("UPDATE nodes SET candidate=0 WHERE node_id=?", (nid,))
            promoted.append(nid)

    if not promoted:
        # 전부 이미 active = idempotent no-op (checksum 불변)
        db.con.rollback()
        after = db.store_checksum()
        db.audit_append(ctx.get("actor", "human"), "candidate_promote", pack_id, "ALLOW",
                        "idempotent_noop skipped=%d" % len(skipped), before, after)
        return {"applied": True, "promoted": [], "skipped_already_active": skipped,
                "checksum_before": before, "checksum_after": after,
                "checksum_changed": before != after, "audit_ok": True, "idempotent": True}

    db.con.commit()
    after = db.store_checksum()

    # 2) 승격 후 정합 재검증 — 깨지면 need_restore(백업 복원 신호)
    post = verify_evidence_linkage(db, node_ids)
    if not post["ok"]:
        db.audit_append(ctx.get("actor", "human"), "candidate_promote", pack_id, "BLOCK",
                        "linkage_broken_post:%d" % len(post["issues"]), before, after)
        return {"applied": False, "reason": "linkage_broken_post", "issues": post["issues"],
                "need_restore": True, "promoted": promoted, "checksum_before": before,
                "checksum_after": after}

    db.audit_append(ctx.get("actor", "human"), "candidate_promote", pack_id, "ALLOW",
                    "candidate->active promoted=%d skipped=%d" % (len(promoted), len(skipped)),
                    before, after)
    return {"applied": True, "promoted": promoted, "skipped_already_active": skipped,
            "checksum_before": before, "checksum_after": after,
            "checksum_changed": before != after, "audit_ok": True, "idempotent": False}


# ── run wrapper: 백업/스냅샷 필수 → promote ─────────────────────
def run_promote(ledger_path, node_ids, backup_dir, ctx=None, tag="p5"):
    """promote 전 ledger 파일 백업(필수) → promote. owner 명시 node_id만."""
    ctx = ctx or {"actor": "human"}
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, "ledger.bak_promote_%s" % tag)
    shutil.copy(ledger_path, backup)   # 백업/스냅샷 필수
    db = open_g3(ledger_path)
    try:
        r = promote(db, node_ids, ctx, pack_id=ctx.get("pack_id", "conv_promote"))
    finally:
        db.con.close()
    r["backup"] = backup
    return r


if __name__ == "__main__":
    print("P5/P7 promote module — run binggu_publish_p5_promote_selftest.py")
