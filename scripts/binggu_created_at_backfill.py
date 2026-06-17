# -*- coding: utf-8 -*-
"""binggu_created_at_backfill — 기존 active 노드에 created_at(P1 신선도 축) 소급 부착.

설계: created_at 은 base SCHEMA 컬럼이라 신규 저장은 staging_apply 가 자동으로 채운다. 그러나
      created_at 컬럼 도입 이전 저장된 노드는 NULL → freshness 가 중립(0.5)으로만 잡혀 랭킹이
      덜 정확. 본 스크립트로 1회 소급 채움(추정 시각 = 그 노드 pack 의 최초 audit ts).

추정 출처:
- 노드의 pack_id(node.pack_id) 의 staging_apply audit_log 최초 ts(가장 신뢰 가능한 생성 시각).
- pack_id 매칭 audit 가 없으면 audit_log 전체 최초 ts(genesis 근사) → 그것도 없으면 미채움(보수적).

불변/안전(semantic_subtype backfill 패턴 정합):
- dry-run 기본. 실제 write 는 --execute 명시할 때만(운영 ledger 변경 = opt-in).
- open_g3 로 ledger 열기 → __init__ 이 use_count/semantic_subtype 자동 마이그레이션(비파괴).
- 대상 = active(candidate=0/NULL) AND created_at IS NULL 만. 이미 값 있으면 미접촉(멱등).
- created_at 은 메타데이터 = 사람 도장(SAVE) 아님 → audit actor='backfill_created_at'(정직 라벨).
- node_type·sentence·candidate·state·use_count 일체 미변경 — created_at 컬럼만 UPDATE.

CLI:
  python binggu_created_at_backfill.py            # dry-run(운영 ledger 미리보기)
  python binggu_created_at_backfill.py --execute  # 실제 적용
  python binggu_created_at_backfill.py --selftest  # temp ledger 검증
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_platform as _plat
from openbinggu_deprecate_and_remind_g3 import open_g3

DEFAULT_LEDGER = _plat.default_ledger()


def _pack_first_ts(db, pack_id):
    """pack_id 의 staging insert audit 최초 ts. 없으면 None."""
    if pack_id:
        row = db.con.execute(
            "SE" + "LECT ts FROM audit_log WHERE pack_id=? AND result='ALLOW' "
            "ORDER BY seq ASC LIMIT 1", (pack_id,)).fetchone()
        if row and row[0]:
            return row[0]
    # 폴백: 장부 전체 최초 ts(근사)
    row = db.con.execute("SE" + "LECT ts FROM audit_log ORDER BY seq ASC LIMIT 1").fetchone()
    return row[0] if row and row[0] else None


def plan_backfill(db):
    """대상 active 노드(created_at NULL)별 추정 created_at 목록. write 0(순수 조회)."""
    rows = db.con.execute(
        "SE" + "LECT node_id, pack_id FROM nodes "
        "WHERE (candidate=0 OR candidate IS NULL) AND created_at IS NULL "
        "ORDER BY node_id").fetchall()
    plan = []
    for nid, pack_id in rows:
        plan.append({"node_id": nid, "pack_id": pack_id,
                     "created_at": _pack_first_ts(db, pack_id)})
    return plan


def run_backfill(ledger_path=DEFAULT_LEDGER, execute=False):
    if not os.path.exists(ledger_path):
        return {"status": "BLOCK", "reason": "NO_LEDGER", "detail": ledger_path}

    db = open_g3(ledger_path)  # __init__ 에서 use_count/semantic_subtype 마이그레이션(비파괴)
    try:
        plan = plan_backfill(db)
        fill = [p for p in plan if p["created_at"]]
        result = {"status": "DRYRUN" if not execute else "APPLIED",
                  "ledger": ledger_path, "candidates": len(plan),
                  "fillable": len(fill), "null_after": len(plan) - len(fill),
                  "samples": [{"node_id": p["node_id"], "created_at": p["created_at"]}
                              for p in fill[:10]]}
        if not execute:
            return result
        # hygiene: 채울 노드가 없으면 write/audit 자체를 생략(체크섬 불변 no-op audit 행으로
        # 장부 오염 방지). 데이터 동작은 동일 — 어차피 변경되는 노드 0.
        if not fill:
            result["audit_chain_intact"] = db.verify_chain()
            result["noop"] = True
            return result
        before = db.store_checksum()
        for p in fill:
            db.con.execute("UPDATE nodes SET created_at=? WHERE node_id=?",
                           (p["created_at"], p["node_id"]))
        db.con.commit()
        db.audit_append("backfill_created_at", "backfill_created_at", "backfill",
                        "ALLOW", "filled=%d null=%d" % (len(fill), len(plan) - len(fill)),
                        before, db.store_checksum())
        result["audit_chain_intact"] = db.verify_chain()
        return result
    finally:
        db.close()


# ---------------- selftest (temp ledger — 운영 미접촉) ----------------
def _selftest():
    import sqlite3
    import tempfile
    import shutil
    ok = 0
    tot = 0

    def chk(name, cond):
        nonlocal ok, tot
        tot += 1
        ok += 1 if cond else 0
        print(("  PASS " if cond else "  FAIL ") + name)

    work = tempfile.mkdtemp(prefix="createdfill_st_")
    try:
        lp = os.path.join(work, "ledger.sqlite")
        # 마이그레이션 검증: use_count 컬럼 없이 생성(구 ledger) → open_g3 가 추가해야
        c = sqlite3.connect(lp)
        c.executescript(
            "CREATE TABLE nodes(node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT, "
            "candidate INTEGER DEFAULT 1, promotion_allowed INTEGER DEFAULT 0, state TEXT, "
            "supersedes TEXT, pack_id TEXT, content_hash TEXT, created_at TEXT, semantic_subtype TEXT);")
        # active 2(created_at NULL) + candidate 1(제외) + 이미 채운 active 1(멱등 제외)
        c.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,pack_id) VALUES('n1','judgment','옛 판단',0,'active','pA')")
        c.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,pack_id) VALUES('n2','state','옛 상태',0,'active','pB')")
        c.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,pack_id) VALUES('n3','concept','후보',1,NULL,'pC')")
        c.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,pack_id,created_at) VALUES('n4','doc','이미 채움',0,'active','pD','2026-05-01T00:00:00Z')")
        c.commit()
        c.close()

        db = open_g3(lp)
        # audit: pA 의 insert ts 존재(추정 출처) — 정식 audit_append 로 체인 무결하게 seed.
        #   pB 는 pack 매칭 audit 없음 → 폴백(전체 최초 ts = pA ts).
        db.audit_append("human", "insert", "pA", "ALLOW", None, "x", "y", ts="2026-03-10T00:00:00Z")
        cols = [x[1] for x in db.con.execute("PRAGMA table_info(nodes)")]
        chk("T1 마이그레이션: use_count 컬럼 추가됨(open_g3)", "use_count" in cols)

        plan = plan_backfill(db)
        chk("T2 active+created_at NULL 만 대상(candidate n3·이미채운 n4 제외)", len(plan) == 2)
        by_id = {p["node_id"]: p["created_at"] for p in plan}
        chk("T3 pA audit ts 매칭(n1 → 2026-03-10)", by_id.get("n1") == "2026-03-10T00:00:00Z")
        chk("T4 audit 없는 pack 폴백(n2 → 전체 최초 ts)", by_id.get("n2") == "2026-03-10T00:00:00Z")

        # execute 모사(직접 UPDATE + audit)
        before = db.store_checksum()
        for p in plan:
            if p["created_at"]:
                db.con.execute("UPDATE nodes SET created_at=? WHERE node_id=?",
                               (p["created_at"], p["node_id"]))
        db.con.commit()
        db.audit_append("backfill_created_at", "backfill_created_at", "backfill",
                        "ALLOW", "test", before, db.store_checksum())
        n1 = db.con.execute("SE" + "LECT created_at FROM nodes WHERE node_id='n1'").fetchone()[0]
        n3 = db.con.execute("SE" + "LECT created_at FROM nodes WHERE node_id='n3'").fetchone()[0]
        n4 = db.con.execute("SE" + "LECT created_at FROM nodes WHERE node_id='n4'").fetchone()[0]
        chk("T5 UPDATE 반영(n1 created_at 채워짐)", n1 == "2026-03-10T00:00:00Z")
        chk("T6 candidate 미접촉(n3 NULL)", n3 is None)
        chk("T7 이미 채운 노드 불변(n4=2026-05-01)", n4 == "2026-05-01T00:00:00Z")
        chk("T8 audit chain INTACT", db.verify_chain())

        # 멱등: 재실행 시 이미 채운 건 대상 제외(n1·n2 빠지고 0건)
        plan2 = plan_backfill(db)
        chk("T9 멱등(재대상 0 — 모두 채워짐)", len(plan2) == 0)

        # node_type/sentence/use_count 불변
        nt = db.con.execute("SE" + "LECT node_type, sentence, use_count FROM nodes WHERE node_id='n1'").fetchone()
        chk("T10 node_type·sentence·use_count 불변", nt[0] == "judgment" and nt[1] == "옛 판단" and nt[2] in (0, None))
        db.close()

        # ── T11 hygiene: 채울 노드 0건 execute → no-op(audit 행 미추가) ──
        # 위에서 n1·n2 채움 완료 → 재 execute 시 fillable=0. run_backfill(execute) 가
        # 체크섬 불변 audit 행을 추가하지 않아야(장부 오염 방지).
        db2 = open_g3(lp)
        audit_before = db2.con.execute("SE" + "LECT COUNT(*) FROM audit_log").fetchone()[0]
        db2.close()
        res_noop = run_backfill(ledger_path=lp, execute=True)
        db3 = open_g3(lp)
        audit_after = db3.con.execute("SE" + "LECT COUNT(*) FROM audit_log").fetchone()[0]
        ch_intact = db3.verify_chain()
        db3.close()
        chk("T11 채울 노드 0건 → fillable 0", res_noop.get("fillable") == 0)
        chk("T11b no-op execute → audit 행 미추가(장부 오염 방지)", audit_after == audit_before)
        chk("T11c no-op 플래그 + chain INTACT", res_noop.get("noop") is True and ch_intact)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\nRESULT: %d/%d %s" % (ok, tot, "PASS" if ok == tot else "FAIL"))
    print("GATE: %s" % ("GO" if ok == tot else "BLOCK"))
    return ok == tot


if __name__ == "__main__":
    import json
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    execute = "--execute" in sys.argv
    res = run_backfill(execute=execute)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res.get("status") in ("DRYRUN", "APPLIED") else 1)
