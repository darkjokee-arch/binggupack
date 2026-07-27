"""one-shot — ai_stamp used 도장 중 use_count 미반영분 백필 (2026-07-27).

배경: owner 결정 "AI 도장도 바로 반영"(PR #115) 직후 실사용 1차에서 `_ai_stamp_use_count` 의
RANK 지연 import 누락(NameError)을 except 가 삼켜 **12건 도장이 전부 무증상 미반영**됐다.
버그 수정 후, 이미 장부에 있는 ai_stamp used 도장의 랭킹 몫을 채운다.

- 대상: recall_outcomes(actor='ai_stamp' AND verdict='used') 의 node_id 중
  ledger.use_events 에 고정 키(use-aistamp) 행이 없는 것.
- 멱등: 고정 키 UNIQUE 라 재실행해도 두 번 오르지 않는다.
- 되돌림: `p1_ranking.revoke_use(db, node_id, "use-aistamp")` — AI 몫만 회수(사람 몫 불변).
- ledger 에 없는 node_id 는 건너뛴다(dangling · 카운트 보고).

사용: python scripts/binggu_backfill_aistamp_usecount.py [--apply]   (기본 dry-run)
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

AI_KEY = "use-aistamp"


def _home():
    return os.environ.get("BINGGU_HOME") or os.path.join(os.path.expanduser("~"), ".binggupack")


def main():
    apply = "--apply" in sys.argv
    home = _home()
    ledger = os.path.join(home, "ledger.sqlite")
    trace = os.path.join(home, "recall_trace.sqlite")
    for p in (ledger, trace):
        if not os.path.exists(p):
            print(f"없음: {p}")
            return 2

    t = sqlite3.connect(f"file:{trace}?mode=ro", uri=True)
    targets = [r[0] for r in t.execute(
        "SELECT DISTINCT node_id FROM recall_outcomes"
        " WHERE actor='ai_stamp' AND verdict='used'")]
    t.close()

    lro = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    todo, already, dangling = [], [], []
    for nid in targets:
        if not lro.execute("SELECT 1 FROM nodes WHERE node_id=?", (nid,)).fetchone():
            dangling.append(nid)
            continue
        hit = lro.execute("SELECT 1 FROM use_events WHERE node_id=? AND use_key=?",
                          (nid, AI_KEY)).fetchone()
        (already if hit else todo).append(nid)
    lro.close()

    print(f"ai_stamp used 대상 {len(targets)} · 백필 필요 {len(todo)}"
          f" · 이미 반영 {len(already)} · ledger 부재 {len(dangling)}")
    for nid in todo:
        print(f"  + {nid}")
    if dangling:
        print(f"  (건너뜀 · ledger 에 없는 노드 {len(dangling)})")

    if not apply:
        print("\nDRY-RUN — 반영하려면 --apply")
        return 0
    if not todo:
        print("\n반영할 것 없음(멱등).")
        return 0

    import binggu_p1_ranking as RANK
    from openbinggu_owner_accept_ux import open_accept
    db = open_accept(ledger)
    done = 0
    try:
        for nid in todo:
            n = RANK.record_use(db, nid, use_key=AI_KEY)
            if n is not None:
                done += 1
                print(f"  ✓ {nid} → use_count={n}")
    finally:
        db.close()
    print(f"\n백필 {done}/{len(todo)} 건 · 되돌림: revoke_use(node_id, '{AI_KEY}')")
    return 0


if __name__ == "__main__":
    sys.exit(main())
