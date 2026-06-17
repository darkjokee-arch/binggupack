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


# ---------------- 셀프테스트 (순수 함수 + temp ledger — 운영 미접촉) ----------------
def _selftest():
    import sqlite3
    import tempfile
    import shutil
    from datetime import timedelta

    ok = 0
    tot = 0

    def chk(name, cond):
        nonlocal ok, tot
        tot += 1
        ok += 1 if cond else 0
        print(("  PASS " if cond else "  FAIL ") + name)

    now = datetime(2026, 6, 17, tzinfo=timezone.utc)

    # ── freshness ──
    fresh_now = freshness((now).strftime("%Y-%m-%dT%H:%M:%SZ"), now=now)
    chk("T1 created_at=now → freshness≈1.0", abs(fresh_now - 1.0) < 1e-6)
    half = (now - timedelta(days=FRESHNESS_HALFLIFE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    chk("T2 created_at=반감기 전 → freshness≈0.5", abs(freshness(half, now=now) - 0.5) < 1e-3)
    old = (now - timedelta(days=360)).strftime("%Y-%m-%dT%H:%M:%SZ")
    chk("T3 오래된 노드 freshness < 0.1", freshness(old, now=now) < 0.1)
    chk("T4 created_at None → 중립 0.5", freshness(None, now=now) == 0.5)
    chk("T4b created_at 깨진 문자열 → 중립 0.5(예외 0)", freshness("not-a-date", now=now) == 0.5)

    # ── utility ──
    chk("T5 use_count 0 → utility 0.0", utility(0) == 0.0)
    chk("T5b use_count None → utility 0.0(예외 0)", utility(None) == 0.0)
    chk("T6 use_count 1 < use_count 5 (단조 증가)", utility(1) < utility(5))
    chk("T7 use_count 포화점 → utility≈1.0", abs(utility(UTILITY_SATURATION) - 1.0) < 1e-6)
    chk("T7b use_count 폭증(1000) → 1.0 캡(폭증 방어)", utility(1000) == 1.0)

    # ── compute_score 가중합 ──
    s_eq = compute_score(0.8, 0.0, 0.5, weights={"freshness": 1.0, "relevance": 1.0, "utility": 1.0})
    chk("T8 가중합 정확(1·0.8 + 1·0 + 1·0.5 = 1.3)", abs(s_eq - 1.3) < 1e-9)
    s_w = compute_score(0.8, 0.0, 0.5, weights={"freshness": 2.0, "relevance": 1.0, "utility": 0.5})
    chk("T9 가중치 override 반영(2·0.8 + 0 + 0.5·0.5 = 1.85)", abs(s_w - 1.85) < 1e-9)
    # freshness 가중치 0 이면 신선도 무시
    s_no_fresh = compute_score(1.0, 0.0, 0.5, weights={"freshness": 0.0, "relevance": 1.0, "utility": 1.0})
    chk("T10 freshness 가중치 0 → 신선도 무시(=0.5)", abs(s_no_fresh - 0.5) < 1e-9)

    # ── 설정값 연동(temp home) ──
    tmp = tempfile.mkdtemp(prefix="bgp_rank_st_")
    try:
        home = os.path.join(tmp, ".binggupack")
        os.makedirs(home)
        # 기본 가중치(설정파일 없음) = 전부 1.0. created_at=실제 now → freshness≈1.0, use_count0 → util0.
        real_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        s_default = node_rank_score(real_now, 0, home=home)
        chk("T11 설정 없음 → 기본 가중치(fresh1+util0≈1.0)", abs(s_default - 1.0) < 1e-3)
        # 설정파일로 utility 가중치 강조 → use_count 많은 노드 점수 상승
        cfg.save_user_config({"ranking_weights": {"freshness": 0.0, "relevance": 0.0, "utility": 3.0}}, home=home)
        s_util = node_rank_score(old, 10, home=home)  # 오래됐지만 자주 씀
        s_util_zero = node_rank_score(now.strftime("%Y-%m-%dT%H:%M:%SZ"), 0, home=home)  # 새것이지만 안 씀
        chk("T12 설정 override(utility 강조) → 자주 쓴 노드 우선", s_util > s_util_zero)

        # ── 음수 가중치 방어: 부호 반전으로 오래된 노드가 상위로 가지 않아야 ──
        import warnings as _warnings
        fresh_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        old_iso = old  # now-360d
        # 음수 freshness 가중치를 설정에 꽂아도 _coerce_ranking 이 0 으로 클램프 →
        # freshness 축이 무력화될 뿐, 부호 반전(오래된 게 상위)은 일어나지 않는다.
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore")
            cfg.save_user_config({"ranking_weights": {"freshness": -5.0, "relevance": 1.0, "utility": 1.0}}, home=home)
            s_fresh_neg = node_rank_score(fresh_iso, 0, home=home)
            s_old_neg = node_rank_score(old_iso, 0, home=home)
        chk("T12b 음수 freshness 가중치 → 오래된 노드가 새 노드 위로 안 감(부호 반전 차단)",
            s_old_neg <= s_fresh_neg)
        # 전부 0/음수 설정 → 기본값 폴백(평탄 정렬/0 나눗셈 방지) → 새 노드가 freshness 로 우선
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore")
            cfg.save_user_config({"ranking_weights": {"freshness": 0.0, "relevance": -1.0, "utility": 0.0}}, home=home)
            s_fresh_z = node_rank_score(fresh_iso, 0, home=home)
            s_old_z = node_rank_score(old_iso, 0, home=home)
        chk("T12c 전부 0/음수 → 기본 가중치 폴백 → 새 노드 우선(평탄 정렬 방지)", s_fresh_z > s_old_z)

        # ── record_use: 로컬 ledger use_count++ ──
        lp = os.path.join(tmp, "ledger.sqlite")
        con = sqlite3.connect(lp)
        con.executescript("CREATE TABLE nodes(node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT,"
                          " candidate INT, state TEXT, content_hash TEXT, created_at TEXT,"
                          " semantic_subtype TEXT, use_count INTEGER DEFAULT 0);")
        con.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,use_count)"
                    " VALUES('n1','judgment','문장',0,'active',0)")
        con.commit()

        class _DB:
            def __init__(self, c):
                self.con = c
        db = _DB(con)
        c1 = record_use(db, "n1")
        c2 = record_use(db, "n1")
        chk("T13 record_use use_count++ (0→1→2)", c1 == 1 and c2 == 2)
        stored = con.execute("SELECT use_count, sentence, node_type FROM nodes WHERE node_id='n1'").fetchone()
        chk("T14 use_count 영속 + 문장/도장 불변", stored == (2, "문장", "judgment"))
        chk("T15 부재 노드 record_use → None(예외 0)", record_use(db, "nope") is None)
        con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nRESULT: %d/%d %s" % (ok, tot, "PASS" if ok == tot else "FAIL"))
    print("GATE: %s" % ("GO" if ok == tot else "BLOCK"))
    return ok == tot


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("usage: binggu_p1_ranking.py --selftest")
    sys.exit(2)
