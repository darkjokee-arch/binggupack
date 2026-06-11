#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu — real staging 위 G3 실연 1회 (deprecated 도장 1건 + 검증 리마인드 1건, owner GO).

조건: 스냅샷 선확보 · deprecated=보존+기본조회 제외+사유 필수 · 리마인드=사람 검토 유도까지(자동 승격 0) ·
      기각 proposal 재확정 차단 유지 확인 · read-back으로 state/audit/candidate·confirmed 위반 0 재확인.
"""
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_staging_write_selftest import OPERATING_PATHS  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import (  # noqa: E402
    open_g3, deprecate_item, active_view, set_review_due, list_due_reminders)
from openbinggu_proposal_to_verb_edge_g2c import finalize_proposal  # noqa: E402
from openbinggu_real_staging_apply_once import REAL_STAGING_DB, SNAP_DIR, _wal_checkpoint  # noqa: E402

PII_NODE = "node:STAGING:wch:4cf0c23e"   # "주민[REDACTED] 정보가 본문에 포함되어 있다" — 사례 기술인데 판단 분류 = 오분류 기각 실연
DUE_NODE = "node:STAGING:wch:df4613cd"   # "도메인 식별자로 보존되어야 한다" — 판단성 문장, 검증예정일 실연
PII_A = "node:STAGING:wch:4cf0c23e"
PII_B = "node:STAGING:wch:911c2324"


def main():
    print("=" * 78)
    print("OpenBinggu — real staging G3 실연 (deprecated 1건 + 리마인드 1건)")
    print("=" * 78)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-44s %s" % ("OK" if ok else "FAIL", name, detail))

    db = open_g3(REAL_STAGING_DB)

    # ① 스냅샷 선확보
    _wal_checkpoint(db.con)
    before = db.store_checksum()
    snap = os.path.join(SNAP_DIR, "snap_g3_before_" + before + ".sqlite")
    shutil.copy2(REAL_STAGING_DB, snap)
    ck("1_snapshot_확보", os.path.exists(snap), "checksum=%s" % before)

    # ② deprecated 도장 1건 — 오분류 노드 기각 (보존+기본조회 제외+사유)
    r2 = deprecate_item(db, "node", PII_NODE, "PII 사례 기술 문장 — '판단' 오분류 (label_kind 재검토 대상)",
                        {"actor": "human"}, SNAP_DIR, counter_evidence_ref=None)
    phys = db.con.execute("SELECT count(*) FROM nodes WHERE node_id=?", (PII_NODE,)).fetchone()[0]
    view = active_view(db)
    ck("2_deprecate_보존+기본조회_제외", r2["applied"] and phys == 1 and PII_NODE not in view["nodes"],
       "active nodes=%d (9→8)" % len(view["nodes"]))

    # ③ 사유 없는 deprecate 차단 (real 위 재검증)
    r3 = deprecate_item(db, "node", DUE_NODE, "", {"actor": "human"}, SNAP_DIR)
    ck("3_사유없는_deprecate_차단", (not r3["applied"]) and r3["reason"] == "deprecated_reason_required")

    # ④ 기각 proposal 재확정 차단 유지 (사이클에서 기각한 PII 쌍)
    row = db.con.execute("SELECT proposal_id FROM edge_proposals WHERE state='rejected'").fetchone()
    r4 = finalize_proposal(db, row[0], "refines", PII_A, PII_B, {"actor": "human"}, SNAP_DIR) if row else {"applied": True}
    ck("4_기각_proposal_재확정_차단_유지", row is not None and (not r4["applied"])
       and r4["reason"] == "proposal_rejected")

    # ⑤ 검증예정일 등록 (판단성 문장 1건)
    r5 = set_review_due(db, DUE_NODE, "2026-06-10", {"actor": "human"})
    ck("5_검증예정일_등록", r5["applied"], "due=2026-06-10")

    # ⑥ 리마인드 목록 — 사람 검토 유도까지만 (상태 무변 증명)
    cs_before = db.store_checksum()
    rem = list_due_reminders(db, "2026-06-11")
    cs_after = db.store_checksum()
    ck("6_리마인드_검토유도만(상태_무변)", rem["count"] == 1 and DUE_NODE in rem["items"]
       and cs_before == cs_after)
    print("\n--- 리마인드 출력(사람용) ---")
    print(rem["markdown"])
    print("---")

    # ⑦ read-back — 상태·audit·candidate/confirmed 위반 0 재확인
    _wal_checkpoint(db.con)
    n_active = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    n_dep = db.con.execute("SELECT count(*) FROM nodes WHERE state='deprecated'").fetchone()[0]
    n_edges = db.con.execute("SELECT count(*) FROM edges WHERE state='active'").fetchone()[0]
    n_rev = db.con.execute("SELECT count(*) FROM judgment_reviews WHERE status='pending'").fetchone()[0]
    n_aud = db.con.execute("SELECT count(*) FROM audit_log").fetchone()[0]
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edge_proposals WHERE promotion_allowed!=0").fetchone()[0])
    chain = db.verify_chain()
    ck("7_readback", n_active == 8 and n_dep == 1 and n_edges == 10 and n_rev == 1 and bad == 0 and chain,
       "active=%d deprecated=%d edges=%d pending_review=%d audit=%d 위반=%d chain=%s" %
       (n_active, n_dep, n_edges, n_rev, n_aud, bad, chain))
    db.close()

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("8_운영_store_불변", op_before == op_after)

    ok_all = all(o for _, o in checks)
    print("-" * 78)
    print("RESULT: %d/%d PASS  자동승격=0 confirmed=0 deploy=0  복구=%s copy 1줄" %
          (sum(1 for _, o in checks if o), len(checks), os.path.basename(snap)))
    print("GATE:", "GO" if ok_all else "NO-GO")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
