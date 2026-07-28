# -*- coding: utf-8 -*-
"""G7 게이트 정본 — evidence_locator 충족률(coverage) 산출 (D9).

왜 이 파일이 필요한가
---------------------
설계 v2 §1-1 이 Unit A 산출물로 지정한 `evidence_locator_coverage()` 가 **출하 코드에 0건**이었고,
배제 규약은 `tests/test_evidence_locator_axis.py` 안의 사설 helper 로만 존재했다. 그 상태에서
누구든 `SELECT count(*) FROM evidence_locator` 로 G7 을 보고하면 백필 실측 기준 **486/500 =
97.2%** 가 나오는데, 스펙 §1 이 말하는 **1차 출처**(원문 대화 턴)는 **83건 = 16.6%** 다.
T2(문서 2차 요약본·재언급)·T3(자기 렌더·self_reference)가 같은 테이블에 섞여 분자를 오염시킨다.

그래서 이 모듈이 못박는 3가지
  ① **분자는 두 개다** — `primary_ratio`(1차 출처 = `evidence_grade.is_primary_source`) 와
     `any_ratio`(등급 무관 좌표 1건 이상)를 **분리 반환**한다. 둘을 더하거나 뭉개지 않는다.
     G7 판정은 언제나 `primary_ratio` 다. `any_ratio` 는 "좌표가 하나라도 붙었나" 진척도일 뿐이다.
  ② **분모는 evidence 다** — `evidence` 테이블만 훑는다. `system_provenance`(파서·파일경로·
     frontmatter 등 시스템 유래)는 스펙 §1:20-21 이 "증거로 인정하지 않음"으로 못박았으므로
     **이 파일의 SQL 어디에도 등장하지 않는다**(문자열조차 쿼리에 없다).
  ③ **증빙 엣지 없는 노드는 '충족'이 아니라 별도 버킷** — `no_evidence_nodes` 로 따로 보고한다.
     충족률에 섞이면 "증거가 없어서 좌표도 없다"가 분모에서 사라져 지표가 좋아 보인다.

계약 대조: `tests/test_evidence_locator_axis.py:55 _locator_coverage` 가 기대 계약이다.
`evidence_total` · `with_locator` · `ratio`(= any_ratio) · `no_evidence_nodes` 4키는 그 helper 와
**같은 값**을 낸다(selftest 28 이 실제로 두 산식을 같은 ledger 위에서 대조한다).
"""
from __future__ import annotations

import sqlite3

# ── 1차 출처 판정 = 등급 정본에 **위임** (D10 · §12-3) ────────────────────────
# 여기에 등급표를 다시 적지 않는다. 사후 회수(백필)는 `match_method` 로, 앞막이(live_capture)는
# `confidence` 로 1차 여부가 갈리므로 두 축을 한 함수(`is_primary_source`)가 판정해야
# "같은 테이블을 두 방식으로 세면 답이 갈리는" 상태(D10)가 재발하지 않는다.
# 정본: `binggupack/schema/evidence_grade.py` — 백필(`binggu_backfill_evidence_locator.py:63`)도
# 같은 모듈을 import 한다(소비자 2곳, 표 1개).
from binggupack.schema.evidence_grade import (  # noqa: F401  (재노출: 보고에 축을 실어야 함)
    PRIMARY_METHODS,
    is_primary_source,
)

EVLOC_TABLE = "evidence_locator"
GROUNDING_RELATION = "evidence_supports"

__all__ = [
    "PRIMARY_METHODS",
    "is_primary_source",
    "evidence_locator_coverage",
    "evidence_ids_for_node",
    "coverage_line",
]


def _has_table(con, name) -> bool:
    """테이블 실재 probe. env 플래그가 아니라 **ledger 실재 상태**로 판단(NEW2.5)."""
    try:
        return bool(list(con.execute('PRAGMA table_info("%s")' % str(name).replace('"', '""'))))
    except sqlite3.Error:
        return False


def _scalar(con, sql, args=()):
    return con.execute(sql, args).fetchone()[0]


def evidence_ids_for_node(con, node_id):
    """node → evidence 라우팅(설계 §1-1) — 자기증빙 엣지 경유. locator 는 노드가 아니라 증거에 붙는다."""
    return [r[0] for r in con.execute(
        "SELECT source FROM edges WHERE target=? AND relation=? AND state!='tombstoned'",
        (node_id, GROUNDING_RELATION))]


def evidence_locator_coverage(con, since=None):
    """G7 충족률. **raise 하지 않는다** — 테이블 부재도 정상 결과(0 충족)로 보고한다.

    Args:
      con:   ledger sqlite3 커넥션(읽기만 한다).
      since: ISO8601 문자열. 지정 시 `created_at >= since` 인 evidence/nodes 만 집계
             (설계 §1-1 판정: "S4 이후 생성 evidence 는 primary_ratio 판정 대상").
             created_at 이 NULL 인 행은 since 지정 시 **분모에서 빠진다**(시점 미상).

    Returns dict:
      evidence_total     분모 = evidence 행수(system_provenance 불참)
      with_primary       1차 출처 좌표를 가진 evidence 수(is_primary_source)  ← ★G7 분자
      with_locator       등급 무관 좌표 1건 이상인 evidence 수(진척도)
      primary_ratio      with_primary / evidence_total     ← ★G7 판정값
      any_ratio          with_locator / evidence_total
      ratio              = any_ratio (테스트 helper 계약 호환 별칭 · **G7 보고에 쓰지 말 것**)
      no_evidence_nodes  증빙 엣지가 없어 §1 증거를 가질 수 없는 노드 수(보류 버킷)
      rows_by_method     locator **행** 기준 match_method 분포(진단용 · evidence 기준 아님)
      rows_by_confidence locator **행** 기준 T1~T4 분포(진단용)
      locator_table      evidence_locator 테이블 실재 여부
      primary_methods    사후 회수 축의 1차 출처 정본(앞막이는 confidence 축 — 보고 재현성용)
      since              에코백
    """
    out = {
        "evidence_total": 0, "with_primary": 0, "with_locator": 0,
        "primary_ratio": 0.0, "any_ratio": 0.0, "ratio": 0.0,
        "no_evidence_nodes": 0, "rows_by_method": {}, "rows_by_confidence": {},
        "locator_table": _has_table(con, EVLOC_TABLE),
        "primary_methods": tuple(PRIMARY_METHODS), "since": since,
    }
    if not _has_table(con, "evidence"):
        return out

    ev_where, ev_args = "", []
    if since is not None:
        ev_where, ev_args = " WHERE e.created_at >= ?", [since]

    ev_ids = [r[0] for r in con.execute(
        "SELECT e.evidence_id FROM evidence e" + ev_where, ev_args)]
    out["evidence_total"] = len(ev_ids)

    cols = set(_table_columns(con, EVLOC_TABLE))
    # 동명이지만 스키마가 다른 선재 테이블/뷰가 있어도 죽지 않는다(부착축 컬럼 부재 = 집계 0).
    if out["locator_table"] and ev_ids and "evidence_id" in cols:
        sel_method = "match_method" if "match_method" in cols else "NULL"
        sel_conf = "confidence" if "confidence" in cols else "NULL"
        any_ids, primary_ids = set(), set()
        by_method, by_conf = {}, {}
        # 1차 판정은 SQL 술어로 흉내내지 않고 **정본 함수를 그대로 태운다**(드리프트 0).
        # 등급 정본에 축이 하나 더 생겨도 이 파일은 안 고쳐도 맞는다.
        for eid, method, conf in con.execute(
                "SELECT evidence_id, %s, %s FROM evidence_locator" % (sel_method, sel_conf)):
            any_ids.add(eid)
            if is_primary_source(method, conf):
                primary_ids.add(eid)
            by_method[method or ""] = by_method.get(method or "", 0) + 1
            by_conf[conf or ""] = by_conf.get(conf or "", 0) + 1
        out["with_locator"] = sum(1 for i in ev_ids if i in any_ids)
        out["with_primary"] = sum(1 for i in ev_ids if i in primary_ids)
        # 진단용 분포 — evidence 기준이 아니라 **locator 행** 기준임을 키 이름으로 못박는다.
        if "match_method" in cols:
            out["rows_by_method"] = by_method
        if "confidence" in cols:
            out["rows_by_confidence"] = by_conf

    total = out["evidence_total"]
    if total:
        out["primary_ratio"] = out["with_primary"] / total
        out["any_ratio"] = out["with_locator"] / total
        out["ratio"] = out["any_ratio"]

    if _has_table(con, "nodes") and _has_table(con, "edges"):
        n_where, n_args = "", []
        if since is not None:
            n_where, n_args = " AND n.created_at >= ?", [since]
        out["no_evidence_nodes"] = _scalar(
            con,
            "SELECT count(*) FROM nodes n WHERE NOT EXISTS("
            "  SELECT 1 FROM edges e WHERE e.target=n.node_id AND e.relation=?)" + n_where,
            [GROUNDING_RELATION] + n_args)
    return out


def _table_columns(con, name):
    try:
        return [r[1] for r in con.execute(
            'PRAGMA table_info("%s")' % str(name).replace('"', '""'))]
    except sqlite3.Error:
        return []


def coverage_line(cov) -> str:
    """보고 1줄. 두 비율을 **항상 같이** 찍어 하나만 인용되는 오보(D9)를 구조적으로 막는다."""
    total = cov.get("evidence_total", 0)
    return ("G7 primary %d/%d=%.1f%% (1차 출처 %s) · any %d/%d=%.1f%% · 증거없는노드 %d"
            % (cov.get("with_primary", 0), total, 100.0 * cov.get("primary_ratio", 0.0),
               "/".join(cov.get("primary_methods", PRIMARY_METHODS)),
               cov.get("with_locator", 0), total, 100.0 * cov.get("any_ratio", 0.0),
               cov.get("no_evidence_nodes", 0)))
