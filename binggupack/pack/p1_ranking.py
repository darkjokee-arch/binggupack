# -*- coding: utf-8 -*-
"""binggu_p1_ranking — P1 ② pack 우선순위 랭킹 (3축 가중합 pre-compute).

랭킹 점수 = w_fresh·freshness + w_rel·relevance + w_util·utility.
  - freshness(신선도): created_at 기준 — 최근일수록 1.0 에 가깝게(반감기 감쇠).
  - relevance(관련성): 빌드 단계엔 쿼리가 없으므로 중립(0.0) — 실제 관련성은 worker
    evidence_search term-frequency 가 회상 시점에 계산(기존 것 재사용). pre-compute 점수는
    신선도+유용성만 반영하고, worker 가 query 가 있으면 relevance 를 더해 재정렬한다.
  - utility(유용성): use_count(로컬 회상 빈도) — log 스케일 정규화(빈도 폭증 방어).

가중치 = ⚙️ binggu_p1_config.ranking_weights (사용자별 설정값, 코드 고정 금지).
빌더가 점수를 pre-compute 해 properties.rank_score 에 박고, worker 는 sort 만 한다
(worker 는 read-only — 점수 계산/저장 불가, 결정9).

불변/안전:
- 순수 함수(부수효과 0) — DB write 는 record_use() 한 곳만(로컬 ledger use_count++).
- record_use 는 PC CLI 회상 시점 호출용. 폰/웹 회상 집계는 deferred(worker write 필요).
- created_at 파싱 실패/부재 → freshness 중립(0.5)·예외 0(방어적).
"""
from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:  # 진입점(scripts/ sys.path) 경유 bare import — wheel 단독 import 는 scripts 패키지 폴백
    import binggu_p1_config as cfg
except ImportError:  # pragma: no cover — wheel 설치본: scripts 는 top-level 패키지
    from scripts import binggu_p1_config as cfg

# 신선도 반감기(일) — 이 일수가 지나면 freshness 0.5. 설정 아닌 알고리즘 상수(축 형태).
FRESHNESS_HALFLIFE_DAYS = 90.0
# 유용성 정규화 기준 — use_count 가 이 값이면 utility ≈ 1.0(log 포화). 빈도 폭증 방어.
UTILITY_SATURATION = 20.0


def _parse_iso(s):
    """'YYYY-MM-DDTHH:MM:SSZ' → aware datetime(UTC). 실패 시 None(예외 0)."""
    if not s or not isinstance(s, str):
        return None
    try:
        t = s.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def freshness(created_at, now=None):
    """created_at → [0,1] 신선도. 반감기 감쇠(최근=1, 오래될수록 0.5→0). 부재/파싱불가=0.5(중립)."""
    dt = _parse_iso(created_at)
    if dt is None:
        return 0.5  # 알 수 없음 = 중립(자동폐기·검열 금지 헌법 정합)
    now = now or datetime.now(timezone.utc)
    age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
    # 2^(-age/halflife): age=0 → 1.0, age=halflife → 0.5
    return float(2.0 ** (-age_days / FRESHNESS_HALFLIFE_DAYS))


def utility(use_count):
    """use_count → [0,1] 유용성. log 포화(빈도 폭증 방어). 0회=0.0, SATURATION회≈1.0."""
    try:
        c = max(0, int(use_count or 0))
    except (TypeError, ValueError):
        return 0.0
    if c <= 0:
        return 0.0
    return float(min(1.0, math.log1p(c) / math.log1p(UTILITY_SATURATION)))


def compute_score(fresh, rel, util, weights=None, home=None):
    """3축 가중합. weights 미지정 시 설정값(binggu_p1_config.ranking_weights) 사용.

    반환 = w_f·fresh + w_r·rel + w_u·util (정규화 안 함 — 상대 정렬용 raw 점수).
    소비처(worker sort)는 절대값이 아니라 노드 간 상대 순위만 쓴다.
    """
    w = weights or cfg.ranking_weights(home)
    return (float(w.get("freshness", 1.0)) * float(fresh)
            + float(w.get("relevance", 1.0)) * float(rel)
            + float(w.get("utility", 1.0)) * float(util))


def node_rank_score(created_at, use_count, relevance=0.0, weights=None, home=None):
    """노드 1개의 pre-compute 랭킹 점수. relevance 는 빌드 시 중립(0.0) —
    worker 가 query 가 있을 때 evidence_search 점수를 더해 재정렬."""
    return compute_score(freshness(created_at), relevance, utility(use_count),
                         weights=weights, home=home)


# ── 유용성 카운터 (로컬 ledger use_count++) — PC CLI 회상 시점 호출 ──────────
def adoption_key(query, domain=None):
    """채택 멱등 키 — 같은 회상(query+domain)은 하루 1회만 use_count 에 기여(정렬 오염 차단).

    use_count 는 utility→compute_score→why_search 정렬에 '인과적으로' 진입한다(적중률 신호와 달리
    합법 입력). 그래서 같은 회상을 짧은 시간 반복하면 정렬이 부풀려질 수 있다(Fable5 D — guard3 우회).
    날짜 버킷으로 같은 날 반복은 멱등, 다음 날은 재채택 허용(장기 유용성 반영·단기 반복만 억제).
    """
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw = "%s|%s|%s" % ((query or "").strip().lower(), (domain or ""), day)
    return "use-" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def record_use(db, node_id, use_key=None):
    """노드 회상 1회 기록 — use_count++. 로컬 ledger write(staging_apply 와 동일 DB).

    폰/웹 회상은 worker(read-only)라 집계 불가 → deferred. 이 함수는 PC CLI 회상 전용.
    node_type/sentence/도장 일체 미변경 — use_count 컬럼만 UPDATE. audit 는 호출자 선택.

    use_key(작업B·채택 멱등): 지정하면 같은 (node_id, use_key) 는 use_events UNIQUE 로 dedup 돼
      use_count 를 다시 올리지 않는다(현재값 반환). 미지정(None)은 기존 동작(무조건 ++·하위호환).
      use_events 테이블이 없는 구 ledger 는 graceful fallback(무조건 ++) — apply_schema 후엔 생성됨.
    반환 = 갱신 후 use_count(멱등 skip 시 현재값·노드 부재 시 None).
    """
    cur = db.con.execute("SELECT use_count FROM nodes WHERE node_id=?", (node_id,))
    row = cur.fetchone()
    if row is None:
        return None
    if use_key is not None:
        try:
            ins = db.con.execute(
                "INSERT OR IGNORE INTO use_events(node_id, use_key, ts) VALUES(?,?,?)",
                (node_id, use_key, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
            if ins.rowcount == 0:
                # 이미 채택됨 — use_count 불변(정렬 오염 차단). ++ 하지 않고 현재값 반환.
                db.con.commit()
                return int(row[0] or 0)
        except sqlite3.OperationalError:
            pass  # use_events 부재(구 ledger·미마이그) → fallback 무조건 ++
    new_count = int(row[0] or 0) + 1
    db.con.execute("UPDATE nodes SET use_count=? WHERE node_id=?", (new_count, node_id))
    db.con.commit()
    return new_count


def revoke_use(db, node_id, use_key):
    """record_use 취소 — use_events row 삭제 + use_count--. 되돌림 대칭성(2026-07-27).

    배경: owner 지시로 AI 자기신고 도장(ai_stamp)도 use_count 를 올리게 됐다. 그러면 AI 가
    잘못 찍은 도장이 랭킹에 **영구 잔류**할 수 있으므로, owner 가 같은 항목을 다르게 판정하면
    올렸던 몫을 되돌려야 대칭이 맞는다. use_key 를 actor 별로 나눠 기록하기 때문에
    (`__trace_mark_ai__` vs `__trace_mark__`) **AI 가 올린 몫만** 회수되고 사람 몫은 안 건드린다.
    같은 구조라서 owner 가 나중에 "AI 반영 취소"로 마음을 바꿔도 AI 분만 일괄 회수할 수 있다.

    use_key 필수 — 무엇을 되돌리는지 특정하지 않은 감산은 금지. 그 출처로 올린 적이 없으면 불변.
    use_events 부재 구 ledger 는 되돌릴 근거가 없으므로 no-op(임의 감산 안 함 · 안전).
    반환 = 갱신 후 use_count(노드 부재 None).
    """
    cur = db.con.execute("SELECT use_count FROM nodes WHERE node_id=?", (node_id,))
    row = cur.fetchone()
    if row is None:
        return None
    try:
        d = db.con.execute("DELETE FROM use_events WHERE node_id=? AND use_key=?",
                           (node_id, use_key))
    except sqlite3.OperationalError:
        return int(row[0] or 0)   # use_events 부재 — 근거 없는 감산 금지(no-op)
    if d.rowcount == 0:
        db.con.commit()
        return int(row[0] or 0)   # 그 출처로 올린 적 없음 — 불변
    new_count = max(0, int(row[0] or 0) - 1)
    db.con.execute("UPDATE nodes SET use_count=? WHERE node_id=?", (new_count, node_id))
    db.con.commit()
    return new_count


# ---- AI 자기신고 도장 → use_count 반영 (정본 · 2026-07-30 binggu.py 에서 승격) ----
# 승격 이유: MCP use-time 도장(trace_stamp)도 같은 반영/회수 대칭이 필요한데, 종전 위치
# (binggu.py 최상위)는 패키지에서 import 불가라 CLI 경로만 랭킹으로 이어졌다.

# AI 자기신고 도장이 올린 use_count 를 되돌릴 수 있어야 하므로 **고정 키**(day-bucket 금지).
# adoption_key 는 날짜가 섞여 있어, AI 가 어제 찍고 owner 가 오늘 뒤집으면 어제 몫을 못 찾는다.
# 고정 키라 (node_id, use_key) UNIQUE 로 노드당 1회만 계상된다 = 자기강화 루프도 함께 억제.
AI_STAMP_USE_KEY = "use-aistamp"


class _ConAdapter(object):
    """record_use/revoke_use 는 db.con 인터페이스만 쓴다 — sqlite3 커넥션 최소 어댑터."""

    def __init__(self, con):
        self.con = con


def ai_stamp_use_count(ledger_path, res, verdict, use_ai=True):
    """AI 자기신고 도장의 랭킹(use_count) 반영 + owner 덮어쓰기 시 회수. best-effort.

    2026-07-27 owner 결정 "AI 도장도 바로 반영":
      · AI 가 used 로 찍음                          → record_use(+1 · 고정 키라 노드당 1회)
      · owner 가 그 항목을 used 아닌 것으로 덮어씀 → revoke_use(AI 몫만 −1)
    owner 가 used 로 확인해준 경우는 결론이 같으므로 카운트를 흔들지 않는다.
    사람 몫은 use_key 가 달라(adoption_key 계열) 이 경로에서 절대 건드려지지 않는다.

    반환 (use_count, action) — action ∈ {"record","revoke","error(...)",None}.
    실패를 조용히 넘기지 않는다(§13 B10) — 예외는 "error(타입)" 로 반환해 호출자가 표시한다."""
    node_id = (res or {}).get("node_id")
    if not node_id:
        return None, None
    try:
        # AI_STAMP_ACTOR 정본은 recall_trace — 지연 import(모듈 층위 순환 회피).
        from binggupack.pack.recall_trace import AI_STAMP_ACTOR
        if use_ai and verdict == "used":
            action = "record"
        elif (res.get("overwrote") == AI_STAMP_ACTOR
                and res.get("prev_verdict") == "used" and verdict != "used"):
            action = "revoke"
        else:
            return None, None
        con = sqlite3.connect(ledger_path)
        try:
            db = _ConAdapter(con)
            if action == "record":
                n = record_use(db, node_id, use_key=AI_STAMP_USE_KEY)
            else:
                n = revoke_use(db, node_id, AI_STAMP_USE_KEY)
        finally:
            con.close()
        return n, action
    except Exception as e:
        return None, "error(%s)" % type(e).__name__
