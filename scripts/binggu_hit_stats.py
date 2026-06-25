# -*- coding: utf-8 -*-
"""양방향 신뢰도 — owner 직감 / ai 반박 적중률 (별도 분모·시간감쇠·표본게이트).

4cli 토론 도출 불변식:
  5(이중계상 분리): owner/ai 적중률을 speaker 별도 분모로 산정(같은 표본 이중계상 금지).
  6(라벨 오염 차단): record 는 사람 resolve(actor=human) 시에만 — AI 자동 기록 0.
  7(시간감쇠): 조회 시 반감기 가중(낡은 적중률이 새 판단을 오염시키지 않게).
  8(표본게이트): N<N_MIN 이면 rate=None·weight=0(과소표본 편향 차단).

정체성(헌법): 신뢰도는 '참고 가중치'이지 '맹종 스위치'가 아니다. AI 가 owner 직감을
무조건 신뢰·선실행하는 근거로 쓰지 않는다 — 양쪽(owner/ai) 적중률을 모두 표시해 사람이 판단.
hit_events 는 append-only 이벤트 로그(조회 시 산정) → 시간감쇠·표본게이트가 자연 반영.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

N_MIN = 5             # 표본 미달 시 가중 0(불변식8)
HALFLIFE_DAYS = 30.0  # 시간감쇠 반감기(불변식7)
PAIR_RELS = ("ai_accepts", "ai_refutes", "ai_revises")


def _parse_ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _now_iso(ts=None):
    return ts if ts else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ai_outcome(owner_success, relation):
    """페어 relation 으로 ai 입장의 적중 도출. 수용=같은편 / 반박=반대 / 수정=중립(None=기록 skip)."""
    if relation == "ai_accepts":
        return owner_success
    if relation == "ai_refutes":
        return not owner_success
    return None  # ai_revises = 중립


def record_resolution(db, owner_node_id, owner_success, ctx, ts=None):
    """owner 직감 노드의 resolve 결과를 기록하고, 페어 ai 노드 입장을 도출해 함께 기록.
    헌법: actor=human 만(불변식6). owner_success=True(직감 적중)/False(빗나감)."""
    if ctx.get("actor", "").strip().lower() != "human":
        return {"recorded": False, "reason": "G4_no_auto"}
    now = _now_iso(ts)
    row = db.con.execute("SELECT speaker, semantic_subtype FROM nodes WHERE node_id=?",
                         (owner_node_id,)).fetchone()
    if not row:
        return {"recorded": False, "reason": "node_not_found"}
    speaker, subtype = row
    rec = 0
    db.con.execute("INSERT INTO hit_events(node_id,speaker,kind,outcome,subtype,ts) VALUES(?,?,?,?,?,?)",
                   (owner_node_id, speaker or "owner", "직감",
                    "hit" if owner_success else "miss", subtype, now))
    rec += 1
    # 페어 ai 노드 (ai --(relation)--> owner)
    pairs = db.con.execute(
        "SELECT source, relation FROM edges WHERE target=? AND relation IN (?,?,?)",
        (owner_node_id, *PAIR_RELS)).fetchall()
    for ai_id, rel in pairs:
        ao = _ai_outcome(owner_success, rel)
        if ao is None:
            continue
        arow = db.con.execute("SELECT semantic_subtype FROM nodes WHERE node_id=?", (ai_id,)).fetchone()
        db.con.execute("INSERT INTO hit_events(node_id,speaker,kind,outcome,subtype,ts) VALUES(?,?,?,?,?,?)",
                       (ai_id, "ai", "반박" if rel == "ai_refutes" else "수용",
                        "hit" if ao else "miss", arow[0] if arow else None, now))
        rec += 1
    db.con.commit()
    return {"recorded": True, "events": rec}


def get_hit_rate(db, speaker, subtype=None, now_ts=None, n_min=N_MIN, halflife=HALFLIFE_DAYS):
    """speaker(+subtype) 적중률(시간감쇠 가중·표본게이트). 반환 {rate, n, enough, weight}.
    enough=False(n<n_min) 면 rate=None·weight=0 → 호출자는 신뢰도 미반영(불변식8)."""
    now = (_parse_ts(now_ts) if now_ts else None) or datetime.now(timezone.utc)
    q = "SELECT outcome, ts FROM hit_events WHERE speaker=?"
    args = [speaker]
    if subtype is not None:
        q += " AND subtype=?"
        args.append(subtype)
    rows = db.con.execute(q, args).fetchall()
    n = len(rows)
    if n < n_min:
        return {"rate": None, "n": n, "enough": False, "weight": 0.0}
    wh = wt = 0.0
    for outcome, ts in rows:
        t = _parse_ts(ts)
        days = max(0.0, (now - t).total_seconds() / 86400.0) if t else 0.0
        w = 0.5 ** (days / halflife)
        wt += w
        if outcome == "hit":
            wh += w
    return {"rate": (wh / wt if wt > 0 else None), "n": n, "enough": True, "weight": 1.0}


def both_sides(db, subtype=None, now_ts=None):
    """양쪽(owner 직감 / ai 반박·수용) 적중률을 함께 반환 — 한쪽 편들지 않는 균형 표시(헌법)."""
    return {"owner": get_hit_rate(db, "owner", subtype, now_ts),
            "ai": get_hit_rate(db, "ai", subtype, now_ts)}
