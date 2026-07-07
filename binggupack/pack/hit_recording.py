# -*- coding: utf-8 -*-
"""binggu_hit_recording — 회수(recall/why) 조언의 적중/빗나감(hit/miss)을 안전하게 기록.

작업A(2차): "회수→판단개입→충돌검증→hit/miss" 루프의 마지막 고리. 사람이 회상한 조언이
실제로 맞았는지(hit)/틀렸는지(miss)를 hit_events 에 적재한다. record_resolution(hit_stats)의
얇은 상위 API 로, 두 개의 red-team 결함(Fable5 D-1/D-2)을 구조로 봉쇄한다.

★ Fable5 방어 (이 모듈이 물리로 보장):
  (D-1) node_id confirm 위조 차단 — mark 는 node_id 를 직접 받지 않는다. (recall_query, index)
        로 받아 why_search 를 **재실행**해 그 index 의 node_id 를 서버가 스스로 확보한다. 회상에
        없는 임의 node_id 를 hit 로 위조할 표면이 없다(회상 결과에 실재하는 노드만 기록 가능).
  (D-2) 이중계상 차단 — decision_id 를 now() 로 두지 않고 _decision_id(node_id, nonce) 로
        **안정 생성**한다. 같은 회상(nonce)+같은 노드의 반복 mark 는 record_resolution 의
        (decision_id,node_id,speaker) dup 가드에 걸려 dup_decision(INSERT 0). 시각 흔들림으로
        같은 판단이 여러 건 계상되지 않는다.

nonce(회상 봉인):
  recall_nonce = sha256(query | sorted(node_ids))[:16]. why_search 출력은 node_id 를 노출하지
  않으므로(_u_why index 만) 사용자는 정직하게 조회해야 nonce 를 얻는다. mark 시 재실행 결과로
  재계산해 대조 — 회상 이후 ledger 가 바뀌어 결과가 달라지면 stale_recall 로 거부(스냅샷 고정).

안전 불변:
  - actor=human 게이트(불변식6 — AI 자동 기록 0). record_resolution 그대로 위임.
  - write 는 record_resolution 의 hit_events INSERT 한 곳만(nodes/edges/도장 불변).
  - 규칙/박제 write 0 · self-modifying 0 · 신규 엣지 타입 0.
"""
from __future__ import annotations

import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))      # <repo>/binggupack/pack
ROOT = os.path.dirname(os.path.dirname(HERE))          # <repo>
_SCRIPTS = os.path.join(ROOT, "scripts")               # 미이관 sibling(schema·staging fixture)
for _p in (ROOT, _SCRIPTS):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.pack import recall as RECALL       # why_search(read-only)               # noqa: E402
from binggupack.pack import hit_stats as HIT        # record_resolution·_decision_id      # noqa: E402

_UNIT_SEP = "\x1f"


def recall_nonce(query, node_ids):
    """회상 봉인 해시 — sha256(query | sorted(node_ids))[:16]. 결정적(LLM 0).

    node_id 는 why_search 출력에 노출되지 않으므로(index 만), 이 nonce 는 '실제로 조회를 거쳤다'는
    증거로 기능한다. ledger 가 바뀌어 결과 집합이 달라지면 nonce 도 달라진다(stale 감지).
    """
    q = (query or "").strip()
    ids = _UNIT_SEP.join(sorted(str(x) for x in (node_ids or [])))
    raw = (q + _UNIT_SEP + ids).encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:16]


def mark_outcome(db, ledger_path, recall_query, index, outcome, ctx,
                 nonce=None, domain=None, home=None):
    """회상 조언(recall_query 의 index 번째 노드)의 적중/빗나감을 기록. read 재실행 + 안전 write.

    절차: actor=human 게이트 → why_search 재실행(read-only) → nonce 대조(stale 차단) →
          index→node_id 확보(D-1 위조 차단) → 안정 decision_id(D-2 이중계상 차단) →
          record_resolution(hit_events INSERT).

    인자:
      db          : write 커넥션(record_resolution 대상). 호출자가 연다(_open/StagingDB).
      ledger_path : why_search 재실행용 경로(read-only).
      recall_query: 회상에 쓴 query(재실행으로 index 노드 확정).
      index       : 1-based 회상 순위(why_search relevant_nodes 순서).
      outcome     : 'hit'(직감 적중) | 'miss'(빗나감). 그 외 → invalid_outcome.
      ctx         : {"actor": "human"} 필수(AI 자동 기록 0).
      nonce       : 회상 시 발급된 recall_nonce. 지정 시 재계산과 대조(stale_recall 차단).
      domain      : 분모 분리 키(hit_stats._domain_norm 정규화).
    반환: {recorded, reason?, outcome?, node_claim?, decision_id?, nonce?, events?, domain?}.
    """
    if (ctx or {}).get("actor", "").strip().lower() != "human":
        return {"recorded": False, "reason": "G4_no_auto"}
    if outcome not in ("hit", "miss"):
        return {"recorded": False, "reason": "invalid_outcome"}
    if not isinstance(index, int) or isinstance(index, bool) or index < 1:
        return {"recorded": False, "reason": "index_out_of_range"}
    if not ledger_path or not os.path.exists(ledger_path):
        return {"recorded": False, "reason": "no_ledger"}

    # D-1: node_id 는 사용자 입력이 아니라 why_search 재실행 결과에서 서버가 확보(위조 표면 0).
    res = RECALL.why_search(ledger_path, recall_query, home=home or os.path.dirname(ledger_path))
    nodes = res.get("relevant_nodes") or []
    if not nodes:
        return {"recorded": False, "reason": "no_recall"}
    node_ids = [n.get("node_id") for n in nodes]

    # nonce 대조(선택) — 회상 이후 결과 집합이 바뀌었으면 거부(스냅샷 고정).
    recomputed = recall_nonce(recall_query, node_ids)
    if nonce is not None and str(nonce) != recomputed:
        return {"recorded": False, "reason": "stale_recall",
                "expected_nonce": recomputed}

    if index > len(nodes):
        return {"recorded": False, "reason": "index_out_of_range",
                "recall_count": len(nodes)}
    target = nodes[index - 1]
    node_id = target.get("node_id")

    # D-2: decision_id 는 now() 가 아니라 (node_id, nonce) 안정 해시 — 같은 회상의 반복 mark 는 dup.
    did = HIT._decision_id(node_id, recomputed)
    r = HIT.record_resolution(db, node_id, outcome == "hit", ctx,
                              domain=domain, decision_id=did)
    r = dict(r)
    r["outcome"] = outcome
    r["node_claim"] = target.get("claim")
    r["nonce"] = recomputed
    if "decision_id" not in r:
        r["decision_id"] = did
    return r


def mark_hit(db, ledger_path, recall_query, index, ctx, nonce=None, domain=None, home=None):
    """회상 조언이 맞았다(직감 적중) — outcome='hit'. mark_outcome 얇은 래퍼."""
    return mark_outcome(db, ledger_path, recall_query, index, "hit", ctx,
                        nonce=nonce, domain=domain, home=home)


def mark_miss(db, ledger_path, recall_query, index, ctx, nonce=None, domain=None, home=None):
    """회상 조언이 틀렸다(직감 빗나감) — outcome='miss'. mark_outcome 얇은 래퍼."""
    return mark_outcome(db, ledger_path, recall_query, index, "miss", ctx,
                        nonce=nonce, domain=domain, home=home)


# ---------------- selftest (temp DB · 운영 write 0) ----------------

def _selftest():
    import tempfile
    import shutil
    from openbinggu_staging_write_selftest import StagingDB, OPERATING_PATHS

    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="binggu_hitrec_")
    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if bool(ok) else "FAIL"))

    def mk(db, nid, sent, subtype="교훈", speaker="owner"):
        db.con.execute(
            "INSERT INTO nodes(node_id,node_type,sentence,semantic_subtype,speaker,state,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (nid, "judgment", sent, subtype, speaker, "active", "2026-06-20T00:00:00Z"))
        db.con.commit()

    path = os.path.join(tmp, "led.sqlite")
    db = StagingDB(path)
    # 회상되도록 같은 어휘를 공유하는 판단 노드 3건.
    mk(db, "n1", "배포 전 로컬 selftest 와 live endpoint 를 확인한다")
    mk(db, "n2", "배포 전 로컬 selftest 확인하고 endpoint 응답을 본다")
    mk(db, "n3", "무관한 요리 레시피 메모")

    q = "배포 전 endpoint 확인"
    res = RECALL.why_search(path, q, home=tmp)
    nodes = res.get("relevant_nodes") or []
    n_ids = [n.get("node_id") for n in nodes]
    nonce = recall_nonce(q, n_ids)

    try:
        # T1 정상 hit 기록 — index 1, actor=human, nonce 일치 → recorded True·hit_events 1건.
        r1 = mark_hit(db, path, q, 1, {"actor": "human"}, nonce=nonce)
        n_ev = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        rec(1, "정상 hit 기록(recorded True·outcome hit·hit_events 1건)",
            r1.get("recorded") and r1.get("outcome") == "hit" and n_ev == 1)

        # T2 D-2 이중계상 차단 — 같은 회상+같은 index 재mark → dup_decision·INSERT 0.
        r2 = mark_hit(db, path, q, 1, {"actor": "human"}, nonce=nonce)
        n_ev2 = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        rec(2, "D-2 이중계상 차단(안정 decision_id → dup_decision·INSERT 0)",
            (not r2.get("recorded")) and r2.get("reason") == "dup_decision" and n_ev2 == 1)

        # T3 actor!=human → G4_no_auto·이벤트 0(불변식6).
        before3 = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        r3 = mark_hit(db, path, q, 2, {"actor": "ai"}, nonce=nonce)
        after3 = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        rec(3, "actor!=human → G4_no_auto·이벤트 0",
            (not r3.get("recorded")) and r3.get("reason") == "G4_no_auto" and before3 == after3)

        # T4 D-1 위조 차단 — 회상 건수 초과 index → index_out_of_range(node_id 위조 표면 없음).
        r4 = mark_hit(db, path, q, 999, {"actor": "human"}, nonce=nonce)
        rec(4, "D-1: 회상 초과 index → index_out_of_range(임의 노드 위조 불가)",
            (not r4.get("recorded")) and r4.get("reason") == "index_out_of_range")

        # T5 stale nonce → stale_recall(회상 스냅샷 불일치 거부).
        r5 = mark_hit(db, path, q, 2, {"actor": "human"}, nonce="deadbeefdeadbeef")
        rec(5, "stale nonce → stale_recall(스냅샷 고정)",
            (not r5.get("recorded")) and r5.get("reason") == "stale_recall")

        # T6 miss 기록 — 다른 index(2)는 다른 node_id → 다른 decision_id → 정상 기록.
        r6 = mark_miss(db, path, q, 2, {"actor": "human"}, nonce=nonce)
        miss_ev = db.con.execute("SELECT outcome FROM hit_events WHERE outcome='miss'").fetchone()
        rec(6, "miss 기록(다른 index → 다른 decision_id → recorded True·outcome miss)",
            r6.get("recorded") and r6.get("outcome") == "miss" and miss_ev == ("miss",))

        # T7 invalid outcome / no_recall graceful.
        r7a = mark_outcome(db, path, q, 1, "maybe", {"actor": "human"}, nonce=nonce)
        r7b = mark_hit(db, path, "존재하지않는쿼리zzxxqq", 1, {"actor": "human"})
        rec(7, "invalid_outcome + no_recall graceful(recorded False·에러 0)",
            r7a.get("reason") == "invalid_outcome" and r7b.get("reason") == "no_recall")

        # T8 nonce 미지정 허용 — nonce=None 이면 재실행 결과로 node 확보(stale 검사만 skip).
        db8 = StagingDB(os.path.join(tmp, "led8.sqlite"))
        mk(db8, "m1", "백업 먼저 하고 파괴작업 승인 받는다")
        mk(db8, "m2", "백업 먼저 파괴작업 대량 삭제 승인 받는다")
        r8 = mark_hit(db8, os.path.join(tmp, "led8.sqlite"), "백업 파괴작업 승인", 1, {"actor": "human"})
        rec(8, "nonce 미지정 허용(재실행으로 node 확보·recorded True)", r8.get("recorded"))
        db8.close()

    finally:
        db.close()
        op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
        shutil.rmtree(tmp, ignore_errors=True)

    store_unchanged = op_before == op_after
    print("=" * 74)
    print("binggu_hit_recording — 작업A hit/miss 안전 기록(nonce·안정 decision_id) selftest")
    print("=" * 74)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print("%s %2d %s" % ("[OK]" if v == "PASS" else "[X]", cid, desc))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("operating_store_unchanged=%s  d1_id_forge=blocked  d2_double_count=blocked"
          % store_unchanged)
    gate = "GO" if (npass == len(results) and store_unchanged) else "NO-GO"
    print("GATE=%s" % gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("binggu_hit_recording: --selftest 로 검증 실행")
