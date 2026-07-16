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
                 nonce=None, domain=None, home=None, expected_node_id=None):
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
      expected_node_id: 도장 staging(--from-recall) 경로의 정본 노드. Hot(어휘) 회상과
        why_search(semantic 활성 시 집합/순위 상이 가능)의 위치 불일치를 구조로 차단 —
        지정 시 index 위치가 아니라 재확보 결과 집합에 이 node_id 가 **실재**하는지로
        대상을 집고, 없으면 stale_recall BLOCK. 미지정(None)=기존 위치 재확보(하위호환).
        위조 표면 아님: 호출측(cmd_mark --from-recall)이 staging 파일에서만 읽고,
        재확보 집합 실재 확인 + 사람 도장(gate_human_for_recall) 이중 게이트를 거친다.
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

    if expected_node_id is not None:
        # MF1/MF4: staging idx→node_id 가 정본 — 위치(index)가 아니라 실재로 집는다.
        target = next((n for n in nodes if n.get("node_id") == expected_node_id), None)
        if target is None:
            return {"recorded": False, "reason": "stale_recall",
                    "expected_nonce": recomputed}
        node_id = expected_node_id
    else:
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


def mark_outcome_uttered(db, feedback, ts, outcome, ctx, domain=None):
    """[SUPERSEDED 2026-07-13 → mark_exchange_uttered] 축 뒤집힘 결함 — 발화 극성(outcome)을
    speaker=owner 로 직결해 owner 의 옳은 지적("아니지…")이 owner miss 로 기록됐다(정반대).
    신규 소비 경로는 mark_exchange_uttered(교환 축) 사용. 이 함수는 호환용으로만 유지.

    ★A 재설계(2026-07-10): recall 무관 owner 지적/정정을 **발화 앵커**로 hit/miss 기록.

    회상 조언(mark_outcome)이 아닌 owner 실시간 지적("산으로 간다"·"틀렸어" 등)의 적중을 센다.
    owner 가 측정하려는 건 "내 직감/지적이 맞았나"인데, 그 지적 대부분은 recall 결과가 아니라
    AI 작업/진단에 대한 정정이라 회상 재실행(why_search)으로 node_id 를 확보할 대상이 없다.

    ★안전(위조 차단은 다른 방식으로 이미 충족):
      - 큐는 UserPromptSubmit hook(사람만 발생·AI 위조 불가)만 append → 발화 자체가 앵커.
      - 소비는 owner 승인 경로(ctx.actor=human) — AI 자동 기록 표면 0(learn-consume 게이트).
      - hit_events.node_id 는 nodes FK 가 아닌 자유 TEXT → 발화 앵커 ID("utter:<sha16>")로
        노드 생성 없이 직접 INSERT. 적중률(both_sides)은 speaker 별 outcome 개수만 세므로
        노드에 묶지 않아도 owner 적중률에 그대로 반영된다.
      - dup 가드: 같은 발화 앵커의 반복 소비 = 같은 decision_id → skip(이중계상 0).
    """
    if (ctx or {}).get("actor", "").strip().lower() != "human":
        return {"recorded": False, "reason": "G4_no_auto"}
    if outcome not in ("hit", "miss"):
        return {"recorded": False, "reason": "invalid_outcome"}
    fb = (feedback or "").strip()
    if not fb:
        return {"recorded": False, "reason": "empty_feedback"}
    # 발화 앵커 node_id — 발화+ts 로 안정 해시(같은 발화·같은 시각 = 같은 앵커 → dup 방지).
    anchor_raw = (fb + _UNIT_SEP + str(ts or "")).encode("utf-8", "replace")
    node_id = "utter:" + hashlib.sha256(anchor_raw).hexdigest()[:16]
    fb_hash = hashlib.sha256(fb.encode("utf-8", "replace")).hexdigest()[:16]
    did = HIT._decision_id(node_id, fb_hash)
    dom = HIT._domain_norm(domain)
    speaker = "owner"
    if HIT._dup_exists(db, did, node_id, speaker):
        return {"recorded": False, "reason": "dup_decision"}
    now = HIT._now_iso(ts)
    db.con.execute(
        "INSERT INTO hit_events(node_id,speaker,kind,outcome,subtype,ts,domain,context_hash,decision_id) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (node_id, speaker, "지적", outcome, None, now, dom, None, did))
    db.con.commit()
    return {"recorded": True, "outcome": outcome, "node_id": node_id,
            "decision_id": did, "domain": dom, "anchor": "utterance"}


def mark_exchange_uttered(db, feedback, ts, stance, verdict, ctx, domain=None, ai_answer=None):
    """★교환 축(2026-07-13 owner "사용자 대화 - ai답변 - 맞는지 틀리는지 확인").

    발화 극성은 결과가 아니라 **입장(stance)**이다: refutes=사용자가 AI 답변을 반박,
    accepts=사용자가 AI 답변을 인정. 누가 맞았는지는 사람이 소비 시점에 **확인(verdict)**한다:
    upheld=발화 판단이 결과적으로 옳았음(기본) · overturned=나중에 뒤집힘.

    귀속 표(hit_events INSERT):
      refutes + upheld     → owner(지적, hit)  + ai(답변, miss)   ← 옳은 지적 = owner 적중
      refutes + overturned → owner(지적, miss) + ai(답변, hit)
      accepts + upheld     → ai(답변, hit)                        ← 인정 발화는 owner 직감 표본 아님
      accepts + overturned → ai(답변, miss)

    안전(mark_outcome_uttered 와 동일): 발화 앵커(UserPromptSubmit hook 만 append·AI 위조 불가) ·
    actor=human 게이트 · 안정 decision_id 로 speaker 별 dup 차단(부분 삽입 없이 all-or-nothing) ·
    ai_answer 는 반환 표시용으로만 쓰고 DB 미저장(PII 최소).
    """
    if (ctx or {}).get("actor", "").strip().lower() != "human":
        return {"recorded": False, "reason": "G4_no_auto"}
    if stance not in ("refutes", "accepts"):
        return {"recorded": False, "reason": "invalid_stance"}
    if verdict not in ("upheld", "overturned"):
        return {"recorded": False, "reason": "invalid_verdict"}
    fb = (feedback or "").strip()
    if not fb:
        return {"recorded": False, "reason": "empty_feedback"}
    anchor_raw = (fb + _UNIT_SEP + str(ts or "")).encode("utf-8", "replace")
    node_id = "utter:" + hashlib.sha256(anchor_raw).hexdigest()[:16]
    fb_hash = hashlib.sha256(fb.encode("utf-8", "replace")).hexdigest()[:16]
    did = HIT._decision_id(node_id, fb_hash)
    dom = HIT._domain_norm(domain)

    stated = verdict == "upheld"
    rows = []
    if stance == "refutes":
        rows.append(("owner", "지적", "hit" if stated else "miss"))
        rows.append(("ai", "답변", "miss" if stated else "hit"))
    else:  # accepts — 판정 대상은 AI 답변뿐(owner 표본 없음)
        rows.append(("ai", "답변", "hit" if stated else "miss"))

    # dup 는 all-or-nothing — 한 speaker 라도 이미 기록됐으면 전체 skip(부분 삽입 금지).
    for speaker, _kind, _oc in rows:
        if HIT._dup_exists(db, did, node_id, speaker):
            return {"recorded": False, "reason": "dup_decision"}
    now = HIT._now_iso(ts)
    for speaker, kind, oc in rows:
        db.con.execute(
            "INSERT INTO hit_events(node_id,speaker,kind,outcome,subtype,ts,domain,context_hash,decision_id) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (node_id, speaker, kind, oc, None, now, dom, None, did))
    db.con.commit()
    return {"recorded": True, "stance": stance, "verdict": verdict,
            "rows": [{"speaker": s, "kind": k, "outcome": oc} for s, k, oc in rows],
            "node_id": node_id, "decision_id": did, "domain": dom,
            "anchor": "utterance", "ai_answer": (ai_answer or "")[:70]}


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

        # T9~T11 ★A 재설계: 발화 앵커 hit/miss(recall 무관 owner 지적 — nodes 없이 직접 기록).
        before_u = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        ru1 = mark_outcome_uttered(db, "산으로 간다", "2026-07-10T00:00:00Z", "miss", {"actor": "human"})
        after_u = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        owner_utt = db.con.execute(
            "SELECT speaker,kind,outcome FROM hit_events WHERE node_id LIKE 'utter:%'").fetchone()
        rec(9, "발화 앵커 기록(recorded True·+1·node_id utter:·speaker owner·kind 지적·outcome miss)",
            ru1.get("recorded") and after_u == before_u + 1
            and str(ru1.get("node_id")).startswith("utter:") and owner_utt == ("owner", "지적", "miss"))

        # T10 발화 앵커 이중계상 차단 — 같은 발화+ts 재소비 → dup_decision·INSERT 0.
        ru2 = mark_outcome_uttered(db, "산으로 간다", "2026-07-10T00:00:00Z", "miss", {"actor": "human"})
        after_u2 = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        rec(10, "발화 앵커 이중계상 차단(같은 발화+ts → dup_decision·INSERT 0)",
            (not ru2.get("recorded")) and ru2.get("reason") == "dup_decision" and after_u2 == after_u)

        # T11 발화 앵커 안전 — actor!=human → G4_no_auto / 빈 발화 → empty_feedback (둘 다 이벤트 0).
        before_u3 = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        ru3 = mark_outcome_uttered(db, "다른 지적", "2026-07-10T00:01:00Z", "hit", {"actor": "ai"})
        ru4 = mark_outcome_uttered(db, "   ", "2026-07-10T00:02:00Z", "hit", {"actor": "human"})
        after_u3 = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        rec(11, "발화 앵커 안전(actor!=human→G4_no_auto·빈발화→empty_feedback·이벤트 0)",
            ru3.get("reason") == "G4_no_auto" and ru4.get("reason") == "empty_feedback"
            and before_u3 == after_u3)

        # T12~T15 ★교환 축(2026-07-13 owner): 사용자 발화 → AI 답변 → 확인(verdict).
        # T12 refutes+upheld(옳은 지적) → owner hit + ai miss 2행(축 교정의 핵심).
        b12 = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        re1 = mark_exchange_uttered(db, "아니지 그게 아니라 preview 를 먼저 줘야지",
                                    "2026-07-13T00:00:00Z", "refutes", "upheld",
                                    {"actor": "human"}, ai_answer="번호는 AI 가 정합니다")
        a12 = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        pair12 = db.con.execute(
            "SELECT speaker,kind,outcome FROM hit_events WHERE decision_id=? ORDER BY speaker",
            (re1.get("decision_id"),)).fetchall()
        rec(12, "교환 refutes+upheld → owner(지적,hit)+ai(답변,miss) 2행(옳은 지적=owner 적중)",
            re1.get("recorded") and a12 == b12 + 2
            and pair12 == [("ai", "답변", "miss"), ("owner", "지적", "hit")])

        # T13 교환 dup — 같은 발화+ts 재소비 → dup_decision·INSERT 0(부분 삽입 없음).
        re2 = mark_exchange_uttered(db, "아니지 그게 아니라 preview 를 먼저 줘야지",
                                    "2026-07-13T00:00:00Z", "refutes", "upheld", {"actor": "human"})
        a13 = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        rec(13, "교환 이중계상 차단(재소비 → dup_decision·INSERT 0)",
            (not re2.get("recorded")) and re2.get("reason") == "dup_decision" and a13 == a12)

        # T14 accepts+upheld → ai hit 1행만(인정 발화는 owner 직감 표본 아님) /
        #     refutes+overturned → owner miss + ai hit(뒤집힌 지적).
        re3 = mark_exchange_uttered(db, "클로드맞다.", "2026-07-13T01:00:00Z",
                                    "accepts", "upheld", {"actor": "human"})
        rows3 = re3.get("rows") or []
        re4 = mark_exchange_uttered(db, "아니야 그건 다르지", "2026-07-13T02:00:00Z",
                                    "refutes", "overturned", {"actor": "human"})
        rows4 = {(r["speaker"], r["outcome"]) for r in (re4.get("rows") or [])}
        rec(14, "accepts+upheld→ai hit 1행 · refutes+overturned→owner miss+ai hit",
            re3.get("recorded") and rows3 == [{"speaker": "ai", "kind": "답변", "outcome": "hit"}]
            and re4.get("recorded") and rows4 == {("owner", "miss"), ("ai", "hit")})

        # T15 교환 게이트 — actor!=human / invalid stance / invalid verdict 전부 기록 0.
        b15 = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        re5 = mark_exchange_uttered(db, "지적", "t", "refutes", "upheld", {"actor": "ai"})
        re6 = mark_exchange_uttered(db, "지적", "t", "confirms", "upheld", {"actor": "human"})
        re7 = mark_exchange_uttered(db, "지적", "t", "refutes", "maybe", {"actor": "human"})
        a15 = db.con.execute("SELECT count(*) FROM hit_events").fetchone()[0]
        rec(15, "교환 게이트(G4_no_auto·invalid_stance·invalid_verdict → 기록 0)",
            re5.get("reason") == "G4_no_auto" and re6.get("reason") == "invalid_stance"
            and re7.get("reason") == "invalid_verdict" and b15 == a15)

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
