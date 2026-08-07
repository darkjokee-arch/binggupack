# -*- coding: utf-8 -*-
"""B-02 자동주입 판정 시효(TTL) — 접힘·무시효·가시화 불변식 (2026-08-07).

배경: B안 컷(top2·rel 0.6)이 유입은 줄였지만 배수구가 없어 누적 미판정이
8/1 280 → 8/7 실측 1,005 로 다시 자랐다. 자동주입(preflight)은 세션이 지나가면
"AI 가 읽었는지"를 알 수 없어 판정 자체가 불가 — TTL(기본 7일)을 넘긴 미판정은
판정 대기에서 접는다. 불변식:
  - 시효 경과 preflight 는 list_pending 에서 접힘(기준 시각 = 최신 trace ts · 벽시계 미사용).
  - 직접 인출(mcp_recall 등 비 AUTOINJECT kind)은 무시효(v7 원칙).
  - 접힘은 읽기 측 필터 — include_expired=True 로 전량 복원(store 원본 불변).
  - pending_stats 가 접힌 수를 따로 센다(silent drop 금지).
  - TTL=0 이면 종전 그대로(무시효) · 파싱 불가 ts 는 오접힘보다 잔류.
"""
from binggupack.pack import recall_trace as RT

NOW = "2026-08-07T12:00:00Z"
OLD = "2026-07-28T12:00:00Z"    # NOW 기준 10일 전 — 기본 TTL(7일) 초과
FRESH = "2026-08-06T12:00:00Z"  # 1일 전 — 시효 안


def _mk(home, kind, ts, prefix, query):
    RT.set_trace_flag(True, home=home)
    recalled = [{"node_id": "node:CONV:%s%d" % (prefix, i), "semantic_subtype": "교훈",
                 "rank_score": 0.9, "relevance": 0.8} for i in (1, 2)]
    r = RT.record_trace(query, kind, recalled, ts, home=home)
    assert r["recorded"]
    return r["trace_id"]


def test_expired_autoinject_folds_but_direct_and_fresh_stay(tmp_path):
    home = str(tmp_path)
    _mk(home, "preflight", OLD, "aa", "q-old-preflight")
    _mk(home, "preflight", FRESH, "bb", "q-fresh-preflight")
    _mk(home, "mcp_recall", OLD, "cc", "q-old-direct")

    pend = RT.list_pending(home=home, now_ts=NOW)
    nodes = {(p["kind"], p["node_id"]) for p in pend}
    assert not any(n.startswith("node:CONV:aa") for _, n in nodes), "시효 경과 preflight 는 접혀야"
    assert {n for k, n in nodes if k == "preflight"} == {"node:CONV:bb1", "node:CONV:bb2"}
    assert {n for k, n in nodes if k == "mcp_recall"} == {"node:CONV:cc1", "node:CONV:cc2"}, \
        "직접 인출은 무시효(v7 원칙)"
    assert len(pend) == 4


def test_base_defaults_to_newest_trace_ts_not_wall_clock(tmp_path):
    """now_ts 미지정 → 기준 = store 최신 trace ts. 고정 fixture 라도 결정적으로 판정."""
    home = str(tmp_path)
    _mk(home, "preflight", OLD, "aa", "q-old-preflight")
    _mk(home, "preflight", FRESH, "bb", "q-fresh-preflight")
    pend = RT.list_pending(home=home)  # 기준 = FRESH → OLD 는 9일 차로 접힘
    assert {p["node_id"] for p in pend} == {"node:CONV:bb1", "node:CONV:bb2"}
    # 같은 날 fixture 만 있으면 아무것도 안 접힌다(기존 고정 ts 시험 무영향의 근거).
    home2 = str(tmp_path / "same_day")
    _mk(home2, "preflight", OLD, "dd", "q-old-only")
    assert len(RT.list_pending(home=home2)) == 2


def test_include_expired_restores_everything(tmp_path):
    home = str(tmp_path)
    _mk(home, "preflight", OLD, "aa", "q-old-preflight")
    _mk(home, "preflight", FRESH, "bb", "q-fresh-preflight")
    assert len(RT.list_pending(home=home, now_ts=NOW)) == 2
    assert len(RT.list_pending(home=home, now_ts=NOW, include_expired=True)) == 4, \
        "접힘은 읽기 측 필터 — 원본은 전량 남는다"


def test_pending_stats_counts_folded(tmp_path):
    home = str(tmp_path)
    _mk(home, "preflight", OLD, "aa", "q-old-preflight")
    _mk(home, "preflight", FRESH, "bb", "q-fresh-preflight")
    _mk(home, "mcp_recall", OLD, "cc", "q-old-direct")
    st = RT.pending_stats(home=home, now_ts=NOW)
    assert st == {"pending": 4, "expired_autoinject": 2,
                  "ttl_days": RT.AUTOINJECT_PENDING_TTL_DAYS}


def test_count_pending_matches_list_pending_with_ttl(tmp_path):
    """count_pending 위임 불변식(정본 1곳) — TTL 접힘 후에도 둘이 같은 수."""
    home = str(tmp_path)
    _mk(home, "preflight", OLD, "aa", "q-old-preflight")
    _mk(home, "preflight", FRESH, "bb", "q-fresh-preflight")
    assert RT.count_pending(home=home) == len(RT.list_pending(home=home, ledger_path=None))


def test_ttl_zero_keeps_legacy_behavior(tmp_path, monkeypatch):
    home = str(tmp_path)
    _mk(home, "preflight", OLD, "aa", "q-old-preflight")
    _mk(home, "preflight", FRESH, "bb", "q-fresh-preflight")
    monkeypatch.setattr(RT, "AUTOINJECT_PENDING_TTL_DAYS", 0)
    assert len(RT.list_pending(home=home, now_ts=NOW)) == 4
    assert RT.pending_stats(home=home, now_ts=NOW)["expired_autoinject"] == 0


def test_unparseable_ts_is_not_folded(tmp_path):
    home = str(tmp_path)
    _mk(home, "preflight", "not-a-timestamp", "aa", "q-weird-ts")
    _mk(home, "preflight", FRESH, "bb", "q-fresh-preflight")
    pend = RT.list_pending(home=home, now_ts=NOW)
    assert {p["node_id"] for p in pend} >= {"node:CONV:aa1", "node:CONV:aa2"}, \
        "파싱 불가 ts 는 오접힘보다 잔류가 안전"
