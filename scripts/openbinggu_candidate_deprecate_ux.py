# -*- coding: utf-8 -*-
"""OpenBinggu v1.0 — 기각 UX 1단계 (목록 인덱스+id8 → deprecate, node 한정).

4cli 토론 결론(BINGGUPACK_V1_BOUNDARY_DEBATE_CONCLUSION.md) 반영판:
  - confirm 문구 = "DEPRECATE <n> <id8>" — 인덱스 단독 금지(결론 4). id8 은 목록 뷰 id 칼럼.
  - **confirm 은 transaction 밖에서** 받고(사람 대기 중 잠금 0), 실행 직전 **목록 재실행 +
    id8 재검증**으로 stale 목록 오지정을 BLOCK(결론 2 — 단일 connection 직렬 실행이라
    재검증~deprecate_item(자체 state 재확인 후 BEGIN~COMMIT) 사이 외부 writer 없음).
  - deprecated wins(결론 1)·event sourcing 도입 금지(state UPDATE 모델 유지)·자동 강등 0.

불변: 삭제 아님 — 물리 보존 + 기본조회(active 경로) 제외는 deprecate_item(G3)이 보장.
      edge 기각은 범위 밖(node 한정) · confirmed 0 · promotion 0 · 운영 store write 0.
CLI: python openbinggu_candidate_deprecate_ux.py --selftest
     (real staging 적용은 별도 GO 필요 — 본 단계는 temp selftest만)
"""
import os
import shutil
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

from openbinggu_staging_write_selftest import OPERATING_PATHS, _hash  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import open_g3, deprecate_item, active_view  # noqa: E402
from openbinggu_candidate_list_view import list_candidates, node_id8, T1  # noqa: E402
from openbinggu_conversation_candidate_save import save_selected  # noqa: E402

EXCERPT_CAP = 40


def deprecate_from_list(db, index, node_hash8, reason, ctx, snap_dir, status="all", kind=None):
    """목록 인덱스 1건 기각(node 한정). confirm="DEPRECATE <index> <node_hash8>" 정확 일치 의무.
    반환 {applied, reason|node_id, sentence_excerpt, snapshot}."""
    before = db.store_checksum()

    def block(rc):
        db.audit_append(ctx.get("actor", "human"), "deprecate_ux", "idx:%s" % index,
                        "BLOCK", rc, before, before)
        return {"applied": False, "reason": rc}

    # 1) 사람만 — deprecate_item 위임 전 입구에서도 차단
    if ctx.get("actor") in ("auto", "reader"):
        return block("G4_no_auto")
    # 2) confirm 문구 정확 일치 — 인덱스+id8 이중 바인딩 (transaction 밖, 잠금 0)
    if ctx.get("confirm") != "DEPRECATE %s %s" % (index, node_hash8):
        return block("confirm_phrase_mismatch")
    # 3) 사유 필수
    if not (reason or "").strip():
        return block("deprecated_reason_required")
    # 4) 목록 재실행 (호출자 목록 객체 불신 — 결정론 재매핑)
    rows = list_candidates(db, status, kind)["rows"]
    if not isinstance(index, int) or index < 1 or index > len(rows):
        return block("index_out_of_range")
    row = rows[index - 1]
    # 5) id8 재검증 — 사용자가 본 목록과 현재 목록이 다르면(BLOCK) 오지정 차단 (실행 직전·결론 2)
    if node_id8(row["node_id"]) != node_hash8:
        return block("node_hash_mismatch")

    # 6) node 한정 deprecate — deprecate_item 이 row state 재확인 후 BEGIN~COMMIT (자동 강등 경로 없음)
    r = deprecate_item(db, "node", row["node_id"], reason, ctx, snap_dir)
    if not r.get("applied"):
        return {"applied": False, "reason": r.get("reason")}
    return {"applied": True, "node_id": row["node_id"],
            "sentence_excerpt": row["sentence"][:EXCERPT_CAP], "snapshot": r.get("snapshot")}


# ---------------- selftest (temp) ----------------

def _insert_shift_node(db):
    """정렬상 맨 앞에 오는 노드 삽입 — stale 목록 인덱스 시프트 재현용."""
    db.con.execute(
        "INSERT INTO nodes(node_id,node_type,sentence,candidate,promotion_allowed,state,pack_id,content_hash,created_at) "
        "VALUES('node:AAA:shift','judgment','[검증픽스처] 목록 시프트 유발용 판단이다.',1,0,'active','depux_fix',?, '2026-06-11')",
        (_hash("node:AAA:shift"),))
    db.con.commit()


def main_selftest():
    print("=" * 78)
    print("기각 UX 1단계 — temp selftest (id8 confirm·node 한정·운영/real 접근 0)")
    print("=" * 78)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_depux_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, ok))
        print("  [%s] %-48s %s" % ("OK" if ok else "FAIL", name, detail))

    db = open_g3(os.path.join(tmp, "s.sqlite"))
    r0 = save_selected(db, T1, [1, 2, 3], {"actor": "human", "confirm": "SAVE 1,2,3"}, snap_dir)
    ck("0_시나리오_구성(후보3건)", r0["applied"] and r0["saved"] == 3)
    n_before = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    rows0 = list_candidates(db)["rows"]
    ck("0b_목록_id8_표시", all(len(r["id8"]) == 8 for r in rows0)
       and rows0[0]["id8"] in list_candidates(db)["markdown"])

    # 1. stale 목록 오지정 BLOCK — 사용자가 index2=B(hB)를 봤는데, 새 노드 삽입으로 목록이 시프트됨
    stale_h = rows0[1]["id8"]  # 사용자가 본 index 2 의 id8
    _insert_shift_node(db)     # 'node:AAA:shift' 가 맨 앞으로 → 전 항목 인덱스 +1
    r1 = deprecate_from_list(db, 2, stale_h, "사유",
                             {"actor": "human", "confirm": "DEPRECATE 2 %s" % stale_h}, snap_dir)
    ck("1_stale_목록_오지정_BLOCK(hash_mismatch)", (not r1["applied"]) and r1["reason"] == "node_hash_mismatch")

    # 2. hash 단독 불일치 BLOCK — index 는 맞고 id8 이 다른 노드 것
    rows1 = list_candidates(db)["rows"]
    wrong_h = rows1[3]["id8"]
    r2 = deprecate_from_list(db, 1, wrong_h, "사유",
                             {"actor": "human", "confirm": "DEPRECATE 1 %s" % wrong_h}, snap_dir)
    ck("2_hash_불일치_BLOCK", (not r2["applied"]) and r2["reason"] == "node_hash_mismatch")

    # 3. 정상 기각 — 현재 목록 index 3 + 올바른 id8
    target = rows1[2]["node_id"]
    th = rows1[2]["id8"]
    r3 = deprecate_from_list(db, 3, th, "기각 UX 검증 — 반증 확인",
                             {"actor": "human", "confirm": "DEPRECATE 3 %s" % th}, snap_dir)
    n_after = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    all_v = list_candidates(db)
    dep_v = list_candidates(db, status="deprecated")
    act = active_view(db)
    ck("3_정상_기각(applied+물리보존+상태반영+active제외)",
       r3["applied"] and r3["node_id"] == target and len(r3["sentence_excerpt"]) <= EXCERPT_CAP
       and n_after == n_before + 1
       and any(r["node_id"] == target and r["state"] == "deprecated" for r in all_v["rows"])
       and len(dep_v["rows"]) == 1 and dep_v["rows"][0]["node_id"] == target
       and target not in act["nodes"])

    # 4. confirm 불일치 BLOCK (인덱스 갈림)
    h1 = list_candidates(db)["rows"][0]["id8"]
    r4 = deprecate_from_list(db, 1, h1, "사유",
                             {"actor": "human", "confirm": "DEPRECATE 2 %s" % h1}, snap_dir)
    ck("4_confirm_불일치_BLOCK", (not r4["applied"]) and r4["reason"] == "confirm_phrase_mismatch")

    # 5. 사유 없음 BLOCK
    r5 = deprecate_from_list(db, 1, h1, "  ",
                             {"actor": "human", "confirm": "DEPRECATE 1 %s" % h1}, snap_dir)
    ck("5_사유없음_BLOCK", (not r5["applied"]) and r5["reason"] == "deprecated_reason_required")

    # 6. actor=auto BLOCK (입구 차단)
    r6 = deprecate_from_list(db, 1, h1, "사유",
                             {"actor": "auto", "confirm": "DEPRECATE 1 %s" % h1}, snap_dir)
    ck("6_actor_auto_BLOCK", (not r6["applied"]) and r6["reason"] == "G4_no_auto")

    # 7. 인덱스 범위 밖 BLOCK
    r7 = deprecate_from_list(db, 99, h1, "사유",
                             {"actor": "human", "confirm": "DEPRECATE 99 %s" % h1}, snap_dir)
    ck("7_인덱스_범위밖_BLOCK", (not r7["applied"]) and r7["reason"] == "index_out_of_range")

    # 8. 이중 기각 BLOCK — status=all 목록의 동일 인덱스+id8 → underlying reason 전달
    r8 = deprecate_from_list(db, 3, th, "또 기각",
                             {"actor": "human", "confirm": "DEPRECATE 3 %s" % th}, snap_dir)
    ck("8_이중_기각_BLOCK(already_deprecated)", (not r8["applied"]) and r8["reason"] == "already_deprecated")

    # 9. deprecations 행에 사유 기록
    dep = db.con.execute("SELECT reason FROM deprecations WHERE item_id=? AND kind='node'", (target,)).fetchone()
    ck("9_사유_기록(deprecations)", dep is not None and "반증" in dep[0])

    # 10. audit chain INTACT
    ck("10_audit_chain_INTACT", db.verify_chain())

    # 11. confirmed 0 · promotion 0 전수
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0])
    ck("11_confirmed0_promotion0_전수", bad == 0)
    db.close()

    # 12. 운영 store 불변
    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("12_운영_store_불변", op_before == op_after)

    # 13. temp 정리
    shutil.rmtree(tmp, ignore_errors=True)
    ck("13_temp_정리", not os.path.exists(tmp))

    ok = all(o for _, o in checks)
    print("-" * 78)
    print("RESULT: %d/%d PASS  edge기각=범위밖 confirmed=0 opencrab=0 deploy=0" %
          (sum(1 for _, o in checks if o), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(main_selftest())
    print("usage: openbinggu_candidate_deprecate_ux.py [--selftest]")
    print("real staging 적용은 별도 GO 필요 — 본 단계는 temp selftest만")
    sys.exit(2)
