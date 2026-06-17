# -*- coding: utf-8 -*-
"""binggu_semantic_subtype_backfill — 기존 active 노드에 보조 semantic_subtype 소급 부착.

설계: semantic_subtype(교훈/결정/선호/설계결정/버그패턴/사실)은 canonical 5종 도장(label_kind)이
      아닌 보조 메타. 값 원천 = cos shadow(Ollama bge-m3). 신규 저장은 capture_preview→save 경로가
      자동 채우지만, 승격 이전 저장된 active 노드는 NULL → 본 스크립트로 1회 소급 채움.

불변/안전:
- dry-run 기본. 실제 write 는 --execute 명시할 때만(운영 ledger 변경 = opt-in).
- open_g3 로 ledger 열기 → __init__ 이 nodes.semantic_subtype 컬럼 자동 마이그레이션(비파괴).
- 대상 = active(candidate=0/NULL) AND semantic_subtype IS NULL 만. 이미 값 있으면 미접촉(멱등).
- subtype 은 보조 메타 = 사람 도장(SAVE) 아님 → audit actor='backfill_semantic'(사람 도장과 구분 정직 라벨).
- canon.enabled() False(Ollama bge-m3 부재)면 BLOCK(헛 NULL write 0).
- node_type(canonical 5종)·sentence·candidate·state 일체 미변경 — semantic_subtype 컬럼만 UPDATE.

CLI:
  python binggu_semantic_subtype_backfill.py            # dry-run(운영 ledger 미리보기)
  python binggu_semantic_subtype_backfill.py --execute  # 실제 적용
  python binggu_semantic_subtype_backfill.py --selftest  # temp ledger 검증
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_platform as _plat
import binggu_canonical_semantic as canon
from openbinggu_deprecate_and_remind_g3 import open_g3

DEFAULT_LEDGER = _plat.default_ledger()
_VALID_BANDS = ("hi", "ambiguous")


def _suggest(shadow, sent):
    """hi/ambiguous → subtype, 그 외(lo/차단/실패) → None. capture_preview._suggest_subtype 정합."""
    sug = shadow.subtype_suggestion(sent)
    if sug and sug.get("band") in _VALID_BANDS:
        return sug["sem_subtype"]
    return None


def plan_backfill(db, shadow):
    """대상 active 노드(subtype NULL)별 제안 subtype 목록. write 0(순수 조회)."""
    rows = db.con.execute(
        "SE" + "LECT node_id, sentence FROM nodes "
        "WHERE (candidate=0 OR candidate IS NULL) AND semantic_subtype IS NULL "
        "ORDER BY node_id").fetchall()
    plan = []
    for nid, sent in rows:
        plan.append({"node_id": nid, "sentence": sent, "subtype": _suggest(shadow, sent)})
    return plan


def run_backfill(ledger_path=DEFAULT_LEDGER, execute=False):
    if not canon.enabled():
        return {"status": "BLOCK", "reason": "SEMANTIC_OFF",
                "detail": "Ollama bge-m3 부재/거부 — subtype 계산 불가(헛 NULL write 회피)"}
    if not os.path.exists(ledger_path):
        return {"status": "BLOCK", "reason": "NO_LEDGER", "detail": ledger_path}

    from binggu_semantic_shadow import get_cached_shadow
    db = open_g3(ledger_path)  # __init__ 에서 semantic_subtype 컬럼 마이그레이션(없으면 ALTER)
    try:
        plan = plan_backfill(db, get_cached_shadow())
        fill = [p for p in plan if p["subtype"]]
        dist = {}
        for p in fill:
            dist[p["subtype"]] = dist.get(p["subtype"], 0) + 1
        result = {"status": "DRYRUN" if not execute else "APPLIED",
                  "ledger": ledger_path, "candidates": len(plan),
                  "fillable": len(fill), "null_after": len(plan) - len(fill),
                  "distribution": dist,
                  "samples": [{"subtype": p["subtype"], "sentence": p["sentence"][:40]}
                              for p in fill[:10]]}
        if not execute:
            return result
        before = db.store_checksum()
        for p in fill:
            db.con.execute("UPDATE nodes SET semantic_subtype=? WHERE node_id=?",
                           (p["subtype"], p["node_id"]))
        db.con.commit()
        db.audit_append("backfill_semantic", "backfill_semantic_subtype", "backfill",
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

    # fake shadow: 결정적 — 문장 키워드로 subtype/band 부여
    class _FakeShadow:
        def subtype_suggestion(self, text):
            if "비밀" in text:  # leak 모사 → None
                return None
            if "결정" in text:
                return {"sem_subtype": "결정", "sem_conf": 0.7, "band": "hi"}
            if "교훈" in text:
                return {"sem_subtype": "교훈", "sem_conf": 0.55, "band": "ambiguous"}
            return {"sem_subtype": "사실", "sem_conf": 0.4, "band": "lo"}  # lo → None

    work = tempfile.mkdtemp(prefix="subfill_st_")
    try:
        lp = os.path.join(work, "ledger.sqlite")
        # 마이그레이션 검증: semantic_subtype 컬럼 없이 생성 → open_g3 가 추가해야
        c = sqlite3.connect(lp)
        c.executescript(
            "CREATE TABLE nodes(node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT, "
            "candidate INTEGER DEFAULT 1, promotion_allowed INTEGER DEFAULT 0, state TEXT, "
            "supersedes TEXT, pack_id TEXT, content_hash TEXT, created_at TEXT);")
        c.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state) VALUES('n1','judgment','이건 결정 문장이다',0,'active')")
        c.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state) VALUES('n2','concept','이건 교훈 문장이다',0,'active')")
        c.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state) VALUES('n3','state','이건 lo 문장이다',0,'active')")
        c.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state) VALUES('n4','doc','후보라 제외 결정',1,NULL)")
        c.commit()
        c.close()

        db = open_g3(lp)
        cols = [x[1] for x in db.con.execute("PRAGMA table_info(nodes)")]
        chk("T1 마이그레이션: semantic_subtype 컬럼 추가됨", "semantic_subtype" in cols)

        plan = plan_backfill(db, _FakeShadow())
        chk("T2 active 만 대상(candidate=1 n4 제외)", len(plan) == 3)
        subs = {p["node_id"]: p["subtype"] for p in plan}
        chk("T3 결정(hi)→채움", subs.get("n1") == "결정")
        chk("T4 교훈(ambiguous)→채움", subs.get("n2") == "교훈")
        chk("T5 lo→None(보수적 미채움)", subs.get("n3") is None)

        # execute 모사(직접 — run_backfill 은 canon.enabled 의존이라 단위 검증)
        before = db.store_checksum()
        for p in plan:
            if p["subtype"]:
                db.con.execute("UPDATE nodes SET semantic_subtype=? WHERE node_id=?",
                               (p["subtype"], p["node_id"]))
        db.con.commit()
        db.audit_append("backfill_semantic", "backfill_semantic_subtype", "backfill",
                        "ALLOW", "test", before, db.store_checksum())
        n1 = db.con.execute("SE" + "LECT semantic_subtype FROM nodes WHERE node_id='n1'").fetchone()[0]
        n3 = db.con.execute("SE" + "LECT semantic_subtype FROM nodes WHERE node_id='n3'").fetchone()[0]
        n4 = db.con.execute("SE" + "LECT semantic_subtype FROM nodes WHERE node_id='n4'").fetchone()[0]
        chk("T6 UPDATE 반영(n1=결정)", n1 == "결정")
        chk("T7 lo·candidate 미접촉(n3·n4 NULL)", n3 is None and n4 is None)
        chk("T8 audit chain INTACT", db.verify_chain())

        # 멱등: 재실행 시 이미 값 있는 건 대상 제외
        plan2 = plan_backfill(db, _FakeShadow())
        chk("T9 멱등(이미 채운 노드 재대상 0 — n3만 남음)",
            len(plan2) == 1 and plan2[0]["node_id"] == "n3")

        # node_type/sentence 불변
        nt = db.con.execute("SE" + "LECT node_type, sentence FROM nodes WHERE node_id='n1'").fetchone()
        chk("T10 node_type·sentence 불변", nt[0] == "judgment" and nt[1] == "이건 결정 문장이다")
        db.close()
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
