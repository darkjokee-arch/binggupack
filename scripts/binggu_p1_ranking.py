# -*- coding: utf-8 -*-
"""binggu_p1_ranking — P1 ② pack 우선순위 랭킹 (backward-compatible thin wrapper).

v1.16 strangler Phase2: 랭킹 정본 로직(freshness/utility/compute_score/node_rank_score/
record_use + 상수 FRESHNESS_HALFLIFE_DAYS/UTILITY_SATURATION)은 binggupack.pack.p1_ranking
으로 이관됐고, 이 파일은 공개 심볼이 byte-identical 한 thin wrapper 다. 기존 호출처
(import binggu_p1_ranking as RANK — binggu.py·realpack_build·recall)는 그대로 동작한다.
순수 함수(write 0·LLM 0·멱등). record_use 만 로컬 ledger use_count++.

binggu_p1_config(가중치 설정) 의존은 scripts/ sys.path 로 해소된다 — 이 wrapper 가 scripts/
스크립트로 실행되거나(러너 GATE) bare-name import 될 때 scripts/ 가 항상 sys.path 에 있고,
이관된 정본 모듈의 `import binggu_p1_config` 도 그 경로로 해소된다.

selftest 의 동적 검증(temp ledger record_use · temp home 설정 연동)은 scripts/ sys.path
의존이므로 이 wrapper 에 잔류한다.

CLI: python scripts/binggu_p1_ranking.py --selftest
"""
import os
import sys
from datetime import datetime, timezone  # selftest 본문이 사용

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.storage.schema import apply_schema  # noqa: E402  스키마 정본(selftest ledger)
from binggupack.pack.p1_ranking import *  # noqa: E402,F401,F403
from binggupack.pack.p1_ranking import (  # noqa: E402,F401  (전체 명시 re-export)
    FRESHNESS_HALFLIFE_DAYS,
    UTILITY_SATURATION,
    _parse_iso,
    freshness,
    utility,
    compute_score,
    node_rank_score,
    record_use,
    revoke_use,
    adoption_key,
    cfg,
)


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
        apply_schema(con)   # 정본 스키마(nodes 상위집합 — use_count/semantic_subtype 포함) 적용
        con.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,use_count)"
                    " VALUES('n1','judgment','문장',0,'active',0)")
        con.commit()

        class _DB:
            def __init__(self, c):
                self.con = c
        db = _DB(con)
        c1 = record_use(db, "n1")
        c2 = record_use(db, "n1")
        chk("T13 record_use use_count++ (0→1→2·use_key 없음=하위호환)", c1 == 1 and c2 == 2)
        stored = con.execute("SELECT use_count, sentence, node_type FROM nodes WHERE node_id='n1'").fetchone()
        chk("T14 use_count 영속 + 문장/도장 불변", stored == (2, "문장", "judgment"))
        chk("T15 부재 노드 record_use → None(예외 0)", record_use(db, "nope") is None)

        # ── 작업B 채택 멱등: 같은 use_key 반복 → use_count 불변(정렬 오염 차단) ──
        con.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,use_count)"
                    " VALUES('n2','judgment','문장2',0,'active',0)")
        con.commit()
        k = adoption_key("배포 절차 확인", domain="bid")
        b1 = record_use(db, "n2", use_key=k)   # 신규 채택 → 0→1
        b2 = record_use(db, "n2", use_key=k)   # 같은 회상 재채택 → 멱등(1 유지)
        b3 = record_use(db, "n2", use_key=k)   # 재재채택 → 여전히 1
        chk("T16 채택 멱등: 같은 use_key 반복 → use_count 불변(1·1·1)",
            b1 == 1 and b2 == 1 and b3 == 1)
        stored2 = con.execute("SELECT use_count FROM nodes WHERE node_id='n2'").fetchone()
        ev_cnt = con.execute("SELECT count(*) FROM use_events WHERE node_id='n2'").fetchone()[0]
        chk("T16b use_events UNIQUE dedup(행 1건·use_count 영속 1)",
            stored2 == (1,) and ev_cnt == 1)
        # 다른 use_key(다른 회상) → 재카운트 허용(장기 유용성 반영)
        k2 = adoption_key("전혀 다른 회상 주제", domain="cook")
        b4 = record_use(db, "n2", use_key=k2)
        chk("T17 다른 use_key(다른 회상) → 재카운트 허용(1→2)", b4 == 2)
        # adoption_key 결정성(같은 인자 같은 날 → 동일 키)
        chk("T18 adoption_key 결정적(같은 query/domain → 동일)",
            adoption_key("배포 절차 확인", domain="bid") == k)

        # ── T19 revoke_use (2026-07-27) — AI 도장 랭킹 반영의 되돌림 대칭성 ──
        AIK = "use-aistamp"
        r1 = record_use(db, "n2", use_key=AIK)          # 2 → 3 (AI 몫 추가)
        chk("T19 AI 몫 record_use → +1(2→3)", r1 == 3)
        r2 = revoke_use(db, "n2", AIK)                  # 3 → 2 (AI 몫만 회수)
        chk("T19b revoke_use → AI 몫 −1(3→2)", r2 == 2)
        ev_ai = con.execute("SELECT count(*) FROM use_events"
                            " WHERE node_id='n2' AND use_key=?", (AIK,)).fetchone()[0]
        chk("T19c 회수 시 use_events 행도 삭제(재기록 가능)", ev_ai == 0)
        # 사람 몫(k·k2)은 그대로 — 출처가 다르면 안 건드린다
        ev_human = con.execute("SELECT count(*) FROM use_events"
                               " WHERE node_id='n2' AND use_key IN (?,?)", (k, k2)).fetchone()[0]
        chk("T19d 사람 몫 use_events 불변(2건 · 출처 분리)", ev_human == 2)
        r3 = revoke_use(db, "n2", AIK)                  # 이미 회수됨 → 불변
        chk("T19e 없는 출처 회수 → use_count 불변(2)", r3 == 2)
        r4 = revoke_use(db, "없는노드", AIK)
        chk("T19f 노드 부재 → None", r4 is None)
        # 하한 클램프 — use_count 0 에서 회수해도 음수로 안 감
        con.execute("INSERT INTO nodes(node_id,use_count) VALUES('n3',0)")
        con.execute("INSERT INTO use_events(node_id,use_key,ts) VALUES('n3',?,'t')", (AIK,))
        con.commit()
        chk("T19g use_count 0 에서 회수 → 0 유지(음수 금지)",
            revoke_use(db, "n3", AIK) == 0)
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
