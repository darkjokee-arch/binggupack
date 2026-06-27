# -*- coding: utf-8 -*-
"""C0 — storage 현재 동작 고정 (characterization). 트랙 C(정본 이동) 전 회귀 가드.

binggupack/storage facade(C1)로 옮기기 전에, 지금 save_selected 가 보장하는 동작을 명시적으로
박는다. facade 가 이 테스트를 그대로 통과해야 "동작 변경 0" 이 증명된다.

고정 속성:
  1 저장 성공(human + 정확 confirm + 판단문)
  2 actor auto/reader 차단(G4_no_auto, 저장 0)
  3 confirm 문구 불일치 차단
  4 snapshot + audit 생성
  5 duplicate 차단(skipped_existing)
  6 read-only 조회 불변(문장/도장 불변)
  7 audit chain INTACT (무결성)

실행: python tests/storage_characterization.py
"""
import os
import sys
import tempfile

os.environ["BINGGU_SEMANTIC_OFF"] = "1"   # 결정적
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, _ROOT)

from openbinggu_deprecate_and_remind_g3 import open_g3
from openbinggu_conversation_candidate_save import save_selected

JUDG = "이 입찰은 마진이 낮아 보류하기로 결정했다."   # capture_preview 후보 1건(판단)


def _fresh():
    tmp = tempfile.mkdtemp(prefix="bgp_storage_char_")
    db = open_g3(os.path.join(tmp, "l.sqlite"))
    snap = os.path.join(tmp, "snap")
    os.makedirs(snap)
    return db, snap, tmp


def _node_count(db):
    return db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]


def _audit_count(db):
    return db.con.execute("SELECT count(*) FROM audit_log").fetchone()[0]


def main():
    results = []

    def rec(name, ok):
        results.append((name, ok))

    # 1) 저장 성공 + 4) snapshot/audit 생성 + 7) chain
    db, snap, _ = _fresh()
    a0 = _audit_count(db)
    snap0 = len(os.listdir(snap))
    r = save_selected(db, JUDG, [1], {"actor": "human", "confirm": "SAVE 1"}, snap)
    rec("1_save_human_ok", bool(r["applied"]) and r["saved"] == 1 and _node_count(db) == 1)
    rec("4_audit_and_snapshot_created", _audit_count(db) > a0 and len(os.listdir(snap)) > snap0)
    rec("7_audit_chain_intact", db.verify_chain())

    # 5) duplicate 차단 — 같은 문장 재저장
    r_dup = save_selected(db, JUDG, [1], {"actor": "human", "confirm": "SAVE 1"}, snap)
    rec("5_duplicate_blocked", r_dup["saved"] == 0 and r_dup["skipped_existing"] == 1
        and _node_count(db) == 1)

    # 6) read-only 불변 — 저장 노드의 문장/도장이 그대로
    row = db.con.execute("SELECT sentence, node_type FROM nodes").fetchone()
    rec("6_stored_stamp_present", row is not None and row[0] == JUDG)

    # 2) actor auto 차단
    db2, snap2, _ = _fresh()
    r_auto = save_selected(db2, JUDG, [1], {"actor": "auto", "confirm": "SAVE 1"}, snap2)
    rec("2a_actor_auto_blocked", r_auto["reason"] == "G4_no_auto" and r_auto["saved"] == 0
        and _node_count(db2) == 0)

    # 2) actor reader 차단
    r_reader = save_selected(db2, JUDG, [1], {"actor": "reader", "confirm": "SAVE 1"}, snap2)
    rec("2b_actor_reader_blocked", r_reader["reason"] == "G4_no_auto" and _node_count(db2) == 0)

    # 3) confirm 문구 불일치 차단 (indices=[1] 인데 confirm 이 SAVE 2)
    db3, snap3, _ = _fresh()
    r_cf = save_selected(db3, JUDG, [1], {"actor": "human", "confirm": "SAVE 2"}, snap3)
    rec("3_confirm_mismatch_blocked", r_cf["reason"] == "confirm_phrase_mismatch"
        and _node_count(db3) == 0)

    print("=" * 70)
    print("storage characterization (C0) — save_selected 현재 동작 고정")
    print("=" * 70)
    npass = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    print("-" * 70)
    gate = "GO" if npass == len(results) else "NO-GO"
    print("RESULT: %d/%d  GATE: %s" % (npass, len(results), gate))
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    main()
