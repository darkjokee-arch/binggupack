#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu — real staging DB **남기는** 1-pack 적용 (owner GO: rollback 검증은 apply_once에서 선행 실증).

apply_once(검증용: apply→rollback 원복)와의 차이 = 마지막에 원복하지 않고 DB·스냅샷을 보존.
선행 의무: 같은 세션에서 openbinggu_real_staging_apply_once GATE=GO (rollback 원복 실증) 후에만 사용.

안전 불변: staging 한정(StagingDB 운영경로 거부) · candidate=1 · promotion=0 · confirmed 0 ·
          운영 store write 0 · 수동 복구 = snapshots/snap_before_*.sqlite 를 DB 위에 copy 1줄.

CLI: python openbinggu_real_staging_persist_apply.py --go ENABLE-PERSONAL-APPLY-STAGING --pack-dir <dir>
"""
import os
import sys
import json
import hashlib
import shutil

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_staging_write_selftest import StagingDB, staging_apply, OPERATING_PATHS  # noqa: E402
try:
    from openbinggu_real_staging_apply_once import (  # noqa: E402  (무수정 재사용)
        load_m1_batch_pack, gate_recheck, _wal_checkpoint,
        REAL_STAGING_DIR, REAL_STAGING_DB, SNAP_DIR, GO_PHRASE, FLAG_ON, EMERGENCY, STATE_DIR,
    )
except ImportError:
    print("이 스크립트는 비공개 운영 모듈이 필요합니다(openbinggu_real_staging_apply_once — 공개 repo 범위 밖).")
    print("공개 대안: python scripts/openbinggu_staging_write_selftest.py (temp SQLite staging write selftest)")
    sys.exit(2)
from watcher_batch_m1 import scan_residual_pii  # noqa: E402


def main():
    go_arg = pack_dir = None
    for i, a in enumerate(sys.argv):
        if a == "--go" and i + 1 < len(sys.argv):
            go_arg = sys.argv[i + 1]
        if a == "--pack-dir" and i + 1 < len(sys.argv):
            pack_dir = sys.argv[i + 1]
    if go_arg != GO_PHRASE or not pack_dir:
        print("usage/GO 불일치 — --go ENABLE-PERSONAL-APPLY-STAGING --pack-dir <dir> 필수. 중단.")
        return 4
    if os.path.exists(EMERGENCY):
        print("emergency 플래그 존재 → 중단.")
        return 3

    print("=" * 78)
    print("OpenBinggu — real staging persist apply (남기는 1-pack 적용, staging 한정)")
    print("=" * 78)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}

    print("\n[1] 선행 게이트 4종 실측 재확인")
    gates_ok, _ = gate_recheck()
    if not gates_ok:
        print("  ✗ 게이트 미충족 → 중단.")
        return 2

    os.makedirs(STATE_DIR, exist_ok=True)
    go_hash = hashlib.sha256(go_arg.encode()).hexdigest()[:12]
    with open(FLAG_ON, "w", encoding="utf-8") as f:
        json.dump({"actor": "human", "owner": True, "scope": "staging_only",
                   "go_phrase_hash": go_hash, "limit": "persist_1pack"}, f)

    ok = False
    try:
        print("\n[2] real staging DB 준비 (기존 있으면 유지·없으면 생성)")
        os.makedirs(SNAP_DIR, exist_ok=True)
        db = StagingDB(REAL_STAGING_DB)  # 운영 경로면 PermissionError

        print("\n[3] apply 전 backup + before_checksum")
        _wal_checkpoint(db.con)
        before = db.store_checksum()
        snap_before = os.path.join(SNAP_DIR, "snap_before_persist_" + before + ".sqlite")
        shutil.copy2(REAL_STAGING_DB, snap_before)
        print("  before_checksum=%s  snapshot=%s" % (before, os.path.basename(snap_before)))

        print("\n[4] pack 적용 + read-back: %s" % pack_dir)
        pack = load_m1_batch_pack(pack_dir)
        pii = sorted({k for s in ([n["sentence"] for n in pack["nodes"]]
                                  + [ev["sentence"] for ev in pack["evidence"]])
                      for k in scan_residual_pii(s)})
        print("  pre-apply PII rescan kinds=%s" % (pii if pii else "없음(0건)"))
        if pii:
            print("  ✗ PII 잔존 → 중단.")
            return 5
        r = staging_apply(db, pack, {"actor": "human"}, SNAP_DIR)
        if not r.get("applied"):
            print("  ✗ apply 거부 reason=%s" % r.get("reason"))
            return 6
        _wal_checkpoint(db.con)
        n_n = db.con.execute("SELECT count(*) FROM nodes WHERE pack_id=?", (pack["pack_id"],)).fetchone()[0]
        n_e = db.con.execute("SELECT count(*) FROM edges WHERE pack_id=?", (pack["pack_id"],)).fetchone()[0]
        n_v = db.con.execute("SELECT count(*) FROM evidence WHERE pack_id=?", (pack["pack_id"],)).fetchone()[0]
        bad_promo = db.con.execute("SELECT count(*) FROM nodes WHERE promotion_allowed=1").fetchone()[0]
        non_cand = db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1").fetchone()[0]
        after = db.store_checksum()
        chain = db.verify_chain()
        readback = (n_n == len(pack["nodes"]) and n_e == len(pack["edges"]) and n_v == len(pack["evidence"])
                    and bad_promo == 0 and non_cand == 0 and after != before and chain)
        print("  applied=True nodes=%d edges=%d evidence=%d promo위반=%d 비candidate=%d" %
              (n_n, n_e, n_v, bad_promo, non_cand))
        print("  after_checksum=%s (≠ before)  audit_chain=%s  read-back PASS=%s" % (after, chain, readback))

        print("\n[5] DB·스냅샷 보존 (rollback 안 함 — 수동 복구 = snapshot copy 1줄)")
        db.close()
        persisted = os.path.exists(REAL_STAGING_DB) and os.path.exists(snap_before)
        print("  db=%s  snapshot 보존=%s" % (os.path.relpath(REAL_STAGING_DB, BASE), persisted))
        ok = readback and persisted
    finally:
        if os.path.exists(FLAG_ON):
            os.remove(FLAG_ON)
        print("\n[6] personal_apply_allowed OFF 복귀 = %s" % (not os.path.exists(FLAG_ON)))

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    unchanged = op_before == op_after
    print("\nRESULT: persist_apply=%s  operating_store_unchanged=%s  confirmed=0 promotion=0 deploy=0" %
          (ok, unchanged))
    gate = "GO" if (ok and unchanged) else "NO-GO"
    print("GATE:", gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
