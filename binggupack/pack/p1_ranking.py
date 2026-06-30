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

import math
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_p1_config as cfg

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
def record_use(db, node_id):
    """노드 회상 1회 기록 — use_count++. 로컬 ledger write(staging_apply 와 동일 DB).

    폰/웹 회상은 worker(read-only)라 집계 불가 → deferred. 이 함수는 PC CLI 회상 전용.
    node_type/sentence/도장 일체 미변경 — use_count 컬럼만 UPDATE. audit 는 호출자 선택.
    반환 = 갱신 후 use_count(노드 부재 시 None).
    """
    cur = db.con.execute("SELECT use_count FROM nodes WHERE node_id=?", (node_id,))
    row = cur.fetchone()
    if row is None:
        return None
    new_count = int(row[0] or 0) + 1
    db.con.execute("UPDATE nodes SET use_count=? WHERE node_id=?", (new_count, node_id))
    db.con.commit()
    return new_count
