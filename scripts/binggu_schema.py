#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""정본 ledger 스키마 (canonical schema).

프로젝트 전반에 복붙돼 서로 컬럼이 갈리던 CREATE TABLE(nodes/edges/evidence/hit_events/
owner_acceptances/recall_traces/…)의 **상위집합(superset)** 을 한 곳에 모은 단일 정본이다.
Phase2 위임자는 각 파일의 인라인 CREATE TABLE 을 `apply_schema(con)` 호출로 교체한다.

설계 원칙
  - 모든 CREATE 는 `IF NOT EXISTS` → 기존 ledger 를 절대 파괴하지 않음(idempotent).
  - 컬럼 타입/DEFAULT 는 가장 관대하게(INTEGER DEFAULT 0, 전부 NULL 허용). 어느 기존
    호출부에도 결여 컬럼이 없도록 각 테이블 컬럼의 합집합을 취함.
  - 구 ledger(컬럼 결여) 대비 경량 마이그레이션 — user_version < SCHEMA_VERSION 이면
    누락 컬럼을 ALTER TABLE ADD COLUMN 으로 비파괴 보강(실패는 무해 무시).
  - CREATE INDEX IF NOT EXISTS — 실제 쿼리 패턴 기반(nodes(state/semantic_subtype/
    candidate), edges(source/target/relation), owner_acceptances(node_id,event_id),
    hit_events(node_id)).

import 경로 (양쪽 호환)
  - scripts/ sys.path 경유 bare-name:  `from binggu_schema import apply_schema, SCHEMA_VERSION`
  - 패키지 경로 shim:                   `from binggupack.storage.schema import apply_schema`

CLI: python scripts/binggu_schema.py --selftest
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import pathlib
import sqlite3
import uuid

# user_version(PRAGMA)로 기록되는 정본 스키마 버전. 마이그레이션 게이트 키.
# v2 (P1-A trusted approval event): approval_requests·approval_consumptions 테이블 + audit_meta['ledger_id'].
# v3 (다리c situation): recall_traces.situation TEXT — 회상이 '어떤 의도 상황'(lookup/decision/
#     change/ambiguous · §9 Layer1 축)에서 일어났는지. nullable ADD COLUMN 비파괴 보강.
# v4 (session_id): recall_traces.session_id TEXT — 회상이 '어느 세션'에서 일어났는지. 세션 마무리
#     preview §2 가 '이번 세션 실제 회상'(도움 판정 대상)을 누적 청소분과 분리(owner 2026-07-25).
#
# ★ evidence_locator/system_provenance(실험 축)는 이 번호를 **올리지 않는다**(재검증 NEW2.6).
#   올리면 미래의 정식 v5 마이그레이션이 `5 < 5` 거짓으로 영구 skip 되어 '버전은 5인데 컬럼은
#   없는' 무증상 결함이 생긴다. 실험 테이블은 user_version 게이트 **밖** CREATE IF NOT EXISTS
#   경로로만 만들고, 존재 여부는 ledger 실재 probe(has_table)로 판정한다.
SCHEMA_VERSION = 4

# ── evidence_locator 실험 축 게이트 (BINGGU_EVLOC_V5) ─────────────────────────
# 왜 env 플래그인가: 이 저장소는 hook/MCP 가 scripts/ 를 **직접 실행**하므로 파일 편집 = 즉시
# 운영 배포다. 플래그 OFF 가 기본이면 파일을 배포해도 운영 ledger DDL 은 0 이고, 플래그 ON 이
# 유일한 '배포 순간'이 된다(롤백 = 플래그 해제 1줄).
#
# ★ 경계(재검증 NEW2.5): env 는 **'테이블을 만들 것인가'에만** 쓴다. write 경로(locator INSERT
#   등)는 env 가 아니라 `has_table(con, "evidence_locator")` 로 **ledger 실재 상태**를 보고
#   판단해야 한다 — hook·MCP·CLI·schtasks 는 env 원천이 전부 달라 env 로 갈리면 반쪽 스키마가
#   된다(어느 프로세스가 열든 결론이 같아야 한다).
EVLOC_FLAG_ENV = "BINGGU_EVLOC_V5"
EVLOC_TABLES = ("evidence_locator", "system_provenance")


class EvlocSchemaError(RuntimeError):
    """플래그 ON 인데 evloc 스키마가 실재하지 않음(fail-open 봉인 · post-apply 검증 실패)."""


class BackupVerifyError(RuntimeError):
    """safe_backup 사본이 원본과 불일치(조용히 잘린 백업 차단)."""


def evloc_enabled() -> bool:
    """`BINGGU_EVLOC_V5=1` 여부. **호출 시점** env 를 읽는다(import 시점 스냅샷 금지).

    import 시점 상수로 굳히면 (a) 같은 프로세스 안에서 OFF/ON 양쪽 검증이 불가능하고
    (b) 모듈 캐시가 다른 두 번째 진실 원본이 된다. 프로세스 env 는 실행 중 바뀌지 않으므로
    운영 동작은 동일하다.
    """
    return os.environ.get(EVLOC_FLAG_ENV) == "1"


@contextlib.contextmanager
def evloc_env(on: bool):
    """플래그를 일시적으로 켜고/끄는 컨텍스트(테스트·dry-run 전용). 종료 시 원복 보장."""
    old = os.environ.get(EVLOC_FLAG_ENV)
    if on:
        os.environ[EVLOC_FLAG_ENV] = "1"
    else:
        os.environ.pop(EVLOC_FLAG_ENV, None)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(EVLOC_FLAG_ENV, None)
        else:
            os.environ[EVLOC_FLAG_ENV] = old

# ── 테이블 정본 정의 ──────────────────────────────────────────────────────────
# 각 테이블: 컬럼 DDL 리스트(합집합). 첫 항목은 대개 PRIMARY KEY.
# NOTE: candidate/promotion_allowed/use_count 는 INTEGER DEFAULT 0(관대),
#       state 는 TEXT DEFAULT 'active'. 나머지는 전부 NULL 허용 TEXT.

_TABLE_COLUMNS = {
    "nodes": [
        "node_id TEXT PRIMARY KEY",
        "node_type TEXT",
        "sentence TEXT",
        "candidate INTEGER DEFAULT 0",
        "promotion_allowed INTEGER DEFAULT 0",
        "state TEXT DEFAULT 'active'",
        "supersedes TEXT",
        "pack_id TEXT",
        "content_hash TEXT",
        "created_at TEXT",
        "semantic_subtype TEXT",
        "use_count INTEGER DEFAULT 0",
        "speaker TEXT",
    ],
    "edges": [
        "edge_id TEXT PRIMARY KEY",
        "relation TEXT",
        "source TEXT",
        "target TEXT",
        "candidate INTEGER DEFAULT 0",
        "state TEXT DEFAULT 'active'",
        "evidence_refs TEXT",
        "pack_id TEXT",
        "content_hash TEXT",
        "created_at TEXT",
    ],
    "evidence": [
        "evidence_id TEXT PRIMARY KEY",
        "sentence TEXT",
        "source_pointer_id TEXT",
        "source_hash TEXT",
        "redaction_policy TEXT",
        "pack_id TEXT",
        "created_at TEXT",
    ],
    "owner_acceptances": [
        # 관대판: production CHECK(event IN(...)) 은 신규 ledger 에서 제외(호환·관대성 우선).
        # 기존 ledger 는 IF NOT EXISTS 로 자신의 CHECK 유지(비파괴).
        "event_id INTEGER PRIMARY KEY AUTOINCREMENT",
        "node_id TEXT",
        "event TEXT",
        "reason TEXT",
        "ts TEXT",
    ],
    "hit_events": [
        "event_id INTEGER PRIMARY KEY AUTOINCREMENT",
        "node_id TEXT",
        "speaker TEXT",
        "kind TEXT",
        "outcome TEXT",
        "subtype TEXT",
        "ts TEXT",
        "domain TEXT",
        "context_hash TEXT",
        "decision_id TEXT",
    ],
    # 채택 멱등 로그(작업B) — 같은 (node_id, use_key) 재채택은 use_count 기여 0(정렬 오염 차단).
    # use_key = 회상 스냅샷 키(query+domain+날짜버킷). UNIQUE 로 dedup, ts 는 선택(감사용).
    "use_events": [
        "node_id TEXT",
        "use_key TEXT",
        "ts TEXT",
    ],
    "recall_traces": [
        "trace_id TEXT PRIMARY KEY",
        "kind TEXT",
        "query_sha TEXT",
        "domain TEXT",
        # situation(v3): 회상 시점의 의도 상황 — lookup/decision/change/ambiguous(§9 Layer1 축).
        #   domain(어느 프로젝트)과 직교하는 '무슨 상황에서 회상했나'. reason_code(왜 무시/교정)와도 직교.
        #   PII 0 — 자유 원문 아닌 enum 라벨만(VALID_SITUATIONS). nullable → 구 store ALTER 보강.
        "situation TEXT",
        # session_id(v4): 회상이 일어난 세션 — 마무리 preview §2 '이번 세션 회상' 필터. nullable ADD COLUMN.
        "session_id TEXT",
        "recalled_json TEXT",
        "top1_node_id TEXT",
        "risk_level TEXT",
        "needs_question INTEGER",
        "ts TEXT",
    ],
    "recall_outcomes": [
        "outcome_id TEXT PRIMARY KEY",
        "trace_id TEXT",
        "node_id TEXT",
        "verdict TEXT",
        "reason_code TEXT",
        "actor TEXT",
        "ts TEXT",
    ],
    # ── recall_run_outcomes (Recall→Outcome Attribution v0.1) ──────────────────
    # 회상된 기억이 실제 작업에 '적용됐고'(application) 그 작업 '결과가 어땠는지'(result)의
    # 관찰 telemetry. recall_outcomes(회상 자체의 효용 used/ignored/corrected)와 **다른 축** —
    # 오염 금지 별도 테이블(스펙 §2). 인과 단정 컬럼(memory_improved_result 류)은 **스키마에서
    # 원천 배제**(스펙 §3) — application·result 두 관찰 사실만 저장. 원문 evidence 미저장, digest만.
    # trust_tier: 'ai_observation'(evidence-gated 자동 관찰) / 'owner_overturn'(사람 정정 reversal).
    # supersedes: overturn reversal 이 가리키는 원본 outcome_id. 원본 행은 ''(빈 문자열·NULL 아님 →
    #   UNIQUE 가 원본 중복을 막도록). append-only — 삭제/UPDATE 0, 정정도 reversal 행 append.
    "recall_run_outcomes": [
        "outcome_id TEXT PRIMARY KEY",
        "trace_id TEXT",
        "applied_node_ids_json TEXT",
        "application TEXT",
        "result TEXT",
        "evidence_digest TEXT",
        "evidence_kind TEXT",
        "trust_tier TEXT",
        "supersedes TEXT",
        "ts TEXT",
    ],
    "applied_registry": [
        "pack_id TEXT",
        "content_hash TEXT",
        "applied_at TEXT",
    ],
    "audit_log": [
        "seq INTEGER PRIMARY KEY AUTOINCREMENT",
        "ts TEXT",
        "actor TEXT",
        "action TEXT",
        "pack_id TEXT",
        "result TEXT",
        "reason_code TEXT",
        "before_hash TEXT",
        "after_hash TEXT",
        "prev_audit_hash TEXT",
        "entry_hash TEXT",
        "chain_ver TEXT",
    ],
    "audit_meta": [
        "key TEXT PRIMARY KEY",
        "value TEXT",
    ],
    "hit_event_chain": [
        "sequence_no INTEGER PRIMARY KEY",
        "event_id INTEGER",
        "raw_json TEXT",
        "snapshot_hash TEXT",
        "external_ts TEXT",
        "prev_hash TEXT",
        "entry_hash TEXT",
        "chain_ver TEXT DEFAULT 'm1'",
    ],
    "hit_event_anchor": [
        "key TEXT PRIMARY KEY",
        "value TEXT",
    ],
    # ── P1-A trusted approval event ────────────────────────────────────────────
    # approval_requests: 모델이 MCP 로 만들 수 있는 PENDING 요청(승인 아님). raw payload 저장 0 —
    #   payload_digest(§9 canonical) + payload-agnostic summary 만. owner 실내용 검토는 별도
    #   approval_review 파일(cap/TTL/PII 게이트·결정 시 purge). state 는 정보용(신뢰=EVENT store).
    "approval_requests": [
        "request_id TEXT PRIMARY KEY",
        "protocol_version TEXT",
        "operation TEXT",
        "payload_digest TEXT",
        "ledger_id TEXT",
        "summary TEXT",
        "state TEXT DEFAULT 'pending'",
        "created_at TEXT",
        "expires_at TEXT",
    ],
    # approval_consumptions: one-time consume dedup ledger. approval_nonce UNIQUE PK = single-winner.
    #   reserved_at = reserve 시각(lease 판정). receipt = node_id/decision_id(nonce 절대 미포함).
    #   MCP-writable 이나 승인을 부여하지 않음(사용 사실만 기록).
    "approval_consumptions": [
        "approval_nonce TEXT PRIMARY KEY",
        "request_id TEXT",
        "state TEXT",
        "reserved_at TEXT",
        "receipt TEXT",
        "consumed_at TEXT",
    ],
}

# 복합 PK / UNIQUE 등 컬럼 리스트로 표현 못하는 테이블 제약을 별도 부여.
_TABLE_CONSTRAINTS = {
    "recall_outcomes": ["UNIQUE(trace_id, node_id)"],
    "applied_registry": ["PRIMARY KEY(pack_id, content_hash)"],
    "use_events": ["UNIQUE(node_id, use_key)"],  # 채택 멱등 dedup 키(작업B)
    # 같은 (trace, 증거) 원본은 1건만(합격기준5) — supersedes=''(원본) 이라 NULL-distinct 회피.
    # reversal 행은 supersedes=원본oid 라 (trace,digest,oid) 별개 → append 가능(정정 이력 보존).
    "recall_run_outcomes": ["UNIQUE(trace_id, evidence_digest, supersedes)"],
}

# ── evidence_locator 축 (BINGGU_EVLOC_V5=1 일 때만 활성) ──────────────────────
# 스펙 §1: 증거 3요소(source_id · 위치 · excerpt_sha)는 **evidence_id 에 부착**한다.
# 'provenance'(파서/파일경로/frontmatter 등 시스템 유래)는 스펙 §1:20-21 이 "증거로 인정하지
# 않음"으로 못박았으므로 **다른 테이블**(system_provenance · evidence_eligible=0)로 분리한다.
# 두 테이블 모두 store_checksum() projection 밖 → 전용 무결성 축은 locator_checksum() 이 담당.
_EVLOC_TABLE_COLUMNS = {
    "evidence_locator": [
        # loc_id = sha256(evidence_id|source_id|locator|excerpt_sha)[:24] — 산출은 write 측(Unit A/C).
        "loc_id TEXT PRIMARY KEY",
        # ★부착축 = 증거(evidence.evidence_id). node 는 edges.relation='evidence_supports' 경유.
        # UNIQUE 참여 4컬럼은 DEFAULT '' — sqlite UNIQUE 는 NULL 을 서로 distinct 로 보므로
        # NULL 이 섞이면 중복 차단이 무력해진다. writer 는 None 대신 '' 를 넣을 것.
        "evidence_id TEXT DEFAULT ''",
        "source_id TEXT DEFAULT ''",       # 원본 파일 경로 또는 sessionId
        "locator TEXT DEFAULT ''",         # 'uuid:<turn-uuid>' | 'line:<n>:off:<n>'
        "excerpt_sha TEXT DEFAULT ''",     # sha256(excerpt_text.utf-8) 64hex 전체
        "excerpt_text TEXT",               # 회수 원문 발췌(동결 복사·절단 0)
        "container_sha TEXT",              # 원본 라인/턴 전체 sha(문맥 무결성)
        "match_method TEXT",               # live_capture|md_exact|session_exact|owner_confirmed
        "confidence TEXT",                 # T1|T2|T3|T4
        "verified_by TEXT",                # auto|owner
        "batch_id TEXT",                   # 롤백 단위(DELETE WHERE batch_id=? · DROP 금지)
        "created_at TEXT",
    ],
    "system_provenance": [
        "prov_id TEXT PRIMARY KEY",
        "subject_kind TEXT",               # 'node'|'evidence'|'edge'
        "subject_id TEXT",
        "parser TEXT",
        "file_path TEXT",
        "frontmatter_json TEXT",
        # 항상 0. CHECK 미사용 — sqlite 는 CHECK 동반 ADD COLUMN 을 거부하므로 ALTER 보강 경로가
        # 막힌다. 대신 코드/테스트로 강제하고 coverage 집계에서 이 테이블을 원천 제외한다.
        "evidence_eligible INTEGER DEFAULT 0",
        "batch_id TEXT",
        "created_at TEXT",
    ],
}

_EVLOC_TABLE_CONSTRAINTS = {
    # 같은 증거·같은 원본·같은 위치·같은 발췌는 1건(재삽입 멱등 · INSERT OR IGNORE 로 dedup).
    "evidence_locator": ["UNIQUE(evidence_id, source_id, locator, excerpt_sha)"],
}

# ── 인덱스 정본 (실제 WHERE/JOIN 쿼리 패턴 기반) ──────────────────────────────
_INDEXES = [
    ("idx_nodes_state", "nodes", "state"),
    ("idx_nodes_semantic_subtype", "nodes", "semantic_subtype"),
    ("idx_nodes_candidate", "nodes", "candidate"),
    ("idx_edges_source", "edges", "source"),
    ("idx_edges_target", "edges", "target"),
    ("idx_edges_relation", "edges", "relation"),
    ("idx_owner_acceptances_node", "owner_acceptances", "node_id, event_id"),
    ("idx_hit_events_node", "hit_events", "node_id"),
]

# ★ 인덱스도 반드시 같은 플래그로 감싼다(재검증 NEW1.4). apply_schema 의 인덱스 루프는 try 가
#   없어서, 테이블 없이 인덱스만 등록되면 `no such table` 이 **매 ledger open 마다** raise 되고
#   capture hook·MCP·CLI 가 전부 죽는다. → _active_indexes() 로만 접근한다.
_EVLOC_INDEXES = [
    ("idx_evloc_evidence", "evidence_locator", "evidence_id"),
    ("idx_evloc_batch", "evidence_locator", "batch_id"),
    ("idx_sysprov_subject", "system_provenance", "subject_kind, subject_id"),
]

# ALTER TABLE ADD COLUMN 으로 안전하게 보강 가능한 컬럼만(제약/AUTOINCREMENT/PK 제외).
_ADDABLE_DEFAULT_NONCONST = ("PRIMARY KEY", "AUTOINCREMENT", "UNIQUE", "NOT NULL")


def _active_table_columns():
    """이 프로세스가 생성/보강할 테이블 정의. 플래그 OFF 면 v4 정본 dict **그 자체**를 반환."""
    if not evloc_enabled():
        return _TABLE_COLUMNS
    return {**_TABLE_COLUMNS, **_EVLOC_TABLE_COLUMNS}


def _active_indexes():
    """생성할 인덱스 목록. 플래그 OFF 면 기존 리스트 **그 자체**(신규 인덱스 0)."""
    if not evloc_enabled():
        return _INDEXES
    return _INDEXES + _EVLOC_INDEXES


def _columns_for(table: str):
    if table in _TABLE_COLUMNS:
        return _TABLE_COLUMNS[table]
    return _EVLOC_TABLE_COLUMNS[table]   # 미등록 테이블명은 KeyError 로 즉시 드러냄


def _constraints_for(table: str):
    return _TABLE_CONSTRAINTS.get(table) or _EVLOC_TABLE_CONSTRAINTS.get(table) or []


def _create_table_sql(table: str, staging: bool = False) -> str:
    cols = list(_columns_for(table))
    # staging(미확정 스테이징 ledger): nodes.candidate 기본값을 1(=미확정)로.
    # 정본(production)은 DEFAULT 0. 그 외 컬럼/제약은 완전히 동일(상위집합 유지).
    if staging and table == "nodes":
        cols = ["candidate INTEGER DEFAULT 1" if c.startswith("candidate ") else c
                for c in cols]
    cols += list(_constraints_for(table))
    return "CREATE TABLE IF NOT EXISTS %s(%s)" % (table, ", ".join(cols))


def _addable_columns(table: str):
    """마이그레이션으로 ALTER ADD 가능한 (col_name, ddl) 목록.

    PK/AUTOINCREMENT/UNIQUE/NOT NULL 컬럼은 ALTER ADD 불가 → 제외(신규 테이블에서만 존재).
    """
    out = []
    for ddl in _columns_for(table):
        name = ddl.split()[0]
        upper = ddl.upper()
        if any(tok in upper for tok in _ADDABLE_DEFAULT_NONCONST):
            continue
        out.append((name, ddl))
    return out


def _existing_columns(con, table):
    try:
        return {row[1] for row in con.execute("PRAGMA table_info(%s)" % table)}
    except sqlite3.Error:
        return set()


def _migrate(con, tables=None):
    """구 ledger(컬럼 결여) 비파괴 보강. 실패는 무해 무시.

    tables: 보강 대상 테이블 정의 dict(기본 = 이 프로세스의 활성 정의).
    """
    for table in (tables if tables is not None else _active_table_columns()):
        have = _existing_columns(con, table)
        if not have:
            continue  # 테이블 자체가 없으면(방금 CREATE 됐어야 함) skip
        for name, ddl in _addable_columns(table):
            if name in have:
                continue
            try:
                con.execute("ALTER TABLE %s ADD COLUMN %s" % (table, ddl))
            except sqlite3.Error:
                pass  # 무해 무시(관대성)


def _user_version(con) -> int:
    return int(con.execute("PRAGMA user_version").fetchone()[0])


def _q(ident: str) -> str:
    """sqlite 식별자 인용(테이블/컬럼명은 바인딩 불가라 인용으로 처리)."""
    return '"%s"' % str(ident).replace('"', '""')


def has_table(con, name) -> bool:
    """`name` 테이블이 이 ledger 에 **실재**하는가.

    ★ write 경로(locator INSERT 등)는 env 플래그가 아니라 이 함수로 판단한다(NEW2.5).
    hook·MCP·CLI·schtasks 는 env 원천이 달라, env 로 갈리면 같은 ledger 에 대해 프로세스마다
    결론이 달라진다(반쪽 스키마). 테이블 실재는 어느 프로세스가 열든 동일한 사실이다.
    """
    try:
        return bool(list(con.execute("PRAGMA table_info(%s)" % _q(name))))
    except sqlite3.Error:
        return False


def table_columns(con, name):
    """실재 컬럼명 리스트(PRAGMA 순서). 테이블 부재/에러 시 []. 동적 INSERT/SELECT 구성용."""
    try:
        return [row[1] for row in con.execute("PRAGMA table_info(%s)" % _q(name))]
    except sqlite3.Error:
        return []


def _evloc_post_apply(con, check_indexes: bool = True) -> None:
    """플래그 ON 한정 post-apply 검증 — 미충족이면 **raise**(fail-open 봉인).

    `_migrate` 가 `except sqlite3.Error: pass` 로 실패를 삼키므로, '만들었다고 믿는데 실제로는
    없는' 상태가 조용히 성립할 수 있다(7/27 RANK 지연 import 누락을 except 가 삼켜 도장 12건이
    전량 미반영된 사례와 동일 계열). 여기서 실재를 직접 확인하고 사유를 담아 raise 한다.
    동명이지만 스키마가 다른 선재 테이블(CREATE IF NOT EXISTS 는 no-op)도 여기서 잡힌다.
    """
    missing = []
    for table, ddls in _EVLOC_TABLE_COLUMNS.items():
        have = _existing_columns(con, table)
        if not have:
            missing.append("table:%s" % table)
            continue
        gap = {d.split()[0] for d in ddls} - have
        if gap:
            missing.append("%s.columns:%s" % (table, ",".join(sorted(gap))))
    if check_indexes and not missing:
        have_idx = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        missing += ["index:%s" % n for n, _t, _c in _EVLOC_INDEXES if n not in have_idx]
    if missing:
        raise EvlocSchemaError(
            "%s=1 인데 evloc 스키마 미충족: %s (동명 선재 테이블의 스키마 불일치일 수 있음 — "
            "CREATE TABLE IF NOT EXISTS 는 기존 테이블을 고치지 않는다)"
            % (EVLOC_FLAG_ENV, ", ".join(missing)))


def apply_schema(con, staging=False):
    """정본 스키마를 con 에 idempotent 하게 적용.

    - 전 테이블 CREATE IF NOT EXISTS + 인덱스 CREATE IF NOT EXISTS.
    - user_version < SCHEMA_VERSION 이면 누락 컬럼 경량 마이그레이션.
    - PRAGMA user_version 을 SCHEMA_VERSION 으로 설정.
    - staging=True: nodes.candidate DEFAULT 1(미확정 스테이징 의미). 그 외 전부 동일.
      기존 apply_schema(con) 호출(default False)은 정본(DEFAULT 0)으로 불변.
      DEFAULT 는 CREATE 시점 신규 테이블에만 적용(IF NOT EXISTS·기존 테이블 불변).
    - BINGGU_EVLOC_V5=1 일 때만 evidence_locator/system_provenance + 전용 인덱스를 추가로
      생성하고, 생성 결과를 post-apply 검증한다(미충족 시 EvlocSchemaError raise).
      **SCHEMA_VERSION 은 올리지 않는다**(NEW2.6) — 실험 축은 user_version 게이트 밖.
    반환: 적용 후 user_version(int).
    """
    tables = _active_table_columns()
    for table in tables:
        con.execute(_create_table_sql(table, staging=staging))
    # 인덱스가 참조하는 컬럼이 구 ledger 에 없을 수 있으므로 마이그레이션을 먼저 수행.
    if _user_version(con) < SCHEMA_VERSION:
        _migrate(con, tables)
        con.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
    if evloc_enabled():
        # 실험 테이블 컬럼 보강은 user_version 게이트 **밖**에서(이미 v4 로 찍힌 ledger 에도
        # 도달해야 하고, 선형 버전축을 점유하지 않기 위해). 인덱스 생성 전에 실재를 확인해
        # 구조 불일치(다른 스키마의 동명 테이블)를 명확한 예외로 드러낸다.
        _migrate(con, _EVLOC_TABLE_COLUMNS)
        _evloc_post_apply(con, check_indexes=False)
    for name, table, cols in _active_indexes():
        con.execute("CREATE INDEX IF NOT EXISTS %s ON %s(%s)" % (name, table, cols))
    if evloc_enabled():
        _evloc_post_apply(con, check_indexes=True)   # fail-open 봉인(_migrate 는 예외를 삼킨다)
    # P1-A: stable ledger identity — 최초 open 시 무조건 발행(user_version 게이트 밖 · INSERT OR
    # IGNORE 라 재open 멱등·기존 값 보존). approval 은 이 ledger_id 에 바인딩되어 ledger 간 replay 차단.
    # (downgrade/upgrade churn 으로 ledger_id 누락 시 binding 실패를 막기 위해 user_version 무관 무조건.)
    con.execute("INSERT OR IGNORE INTO audit_meta(key,value) VALUES('ledger_id',?)",
                (uuid.uuid4().hex,))
    con.commit()
    return _user_version(con)


def ledger_id(con) -> str:
    """이 ledger 의 안정 식별자(audit_meta['ledger_id']). apply_schema 미실행 시 발행 후 반환."""
    row = con.execute("SELECT value FROM audit_meta WHERE key='ledger_id'").fetchone()
    if row and row[0]:
        return row[0]
    lid = uuid.uuid4().hex
    con.execute("INSERT OR IGNORE INTO audit_meta(key,value) VALUES('ledger_id',?)", (lid,))
    con.commit()
    return con.execute("SELECT value FROM audit_meta WHERE key='ledger_id'").fetchone()[0]


def schema_version(con) -> int:
    """con 의 현재 user_version 조회."""
    return _user_version(con)


# ── 무결성 축 ────────────────────────────────────────────────────────────────
# store_checksum()(openbinggu_staging_write_selftest.py)은 nodes/edges/evidence 전컬럼
# projection 이라 **use_count 를 포함**하고, p1_ranking 이 audit 밖에서 그 값을 UPDATE 한다
# (= verify_tail_state 가 착수 전부터 이미 False). 그래서 'checksum before == after' 는 무손실
# 증명이 될 수 없다(MF1.2). 아래 두 함수가 대체 증명 축이다.
#   integrity_probe() — 기존 v4 테이블의 행 단위 대조(휘발 컬럼 2층 분리)
#   locator_checksum() — evloc 테이블 전용 해시(store_checksum projection 밖 = 무결성 0 상쇄)

# core_sha: audit 밖 UPDATE 가 실증된 컬럼만 제외(nodes.use_count · p1_ranking.py:136,167).
_VOLATILE_CORE = {"nodes": ("use_count",)}
# mutable_sha: 위 + 상태 전이(state)까지 제외. state 를 무조건 빼면 tombstone 파괴가 무검출이
# 되므로(NEW2.8) 두 층을 **함께** 반환하고, state 변화는 core_sha 차이로 반드시 표면화한다.
_VOLATILE_MUTABLE = {"nodes": ("use_count", "state")}


def _canon_value(v):
    if isinstance(v, bytes):
        return {"__b__": v.hex()}
    if isinstance(v, float):
        return {"__f__": repr(v)}
    return v


def _row_digest(cols, row) -> str:
    payload = json.dumps([[c, _canon_value(v)] for c, v in zip(cols, row)],
                         ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _set_sha(digests) -> str:
    """행 순서 무관(정렬 후 결합) 집합 해시."""
    h = hashlib.sha256()
    for d in sorted(digests):
        h.update(d.encode("ascii"))
    return h.hexdigest()


def integrity_probe(con, tables=None):
    """무손실 대조용 probe — {counts, pk_sha, core_sha, mutable_sha}.

    - 대상 기본값: **v4 정본 테이블 중 실재하는 것**(evloc 실험 테이블 제외 → 플래그 ON/OFF 를
      가로질러 값이 안정. evloc 축은 locator_checksum 이 담당).
    - 컬럼은 이름순 정렬해서 읽는다(ALTER 보강 순서가 ledger 마다 달라도 동일 값).
    - 행 순서 무관(행별 sha 를 정렬해 결합) → VACUUM·rowid 재배치에 불변.
    - core_sha: nodes.use_count 만 제외(state 포함 → tombstone/상태 파괴 검출).
      mutable_sha: use_count+state 제외(상태 전이를 '설명 필요 변화'로 분리하고 싶을 때).
    판정: `probe_before == probe_after` (dict 동등 비교).
    """
    names = list(tables) if tables is not None else list(_TABLE_COLUMNS)
    out = {"tables": [], "counts": {}, "pk_sha": {}, "core_sha": {}, "mutable_sha": {}}
    for table in names:
        info = list(con.execute("PRAGMA table_info(%s)" % _q(table)))
        if not info:
            continue   # 부재 테이블은 스킵(대상 목록에만 등장)
        out["tables"].append(table)
        cols = sorted(r[1] for r in info)
        pk_cols = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
        sel = ", ".join(_q(c) for c in cols)
        rows = list(con.execute("SELECT %s FROM %s" % (sel, _q(table))))
        idx = {c: i for i, c in enumerate(cols)}
        vol_core = set(_VOLATILE_CORE.get(table, ()))
        vol_mut = set(_VOLATILE_MUTABLE.get(table, ()))
        core_cols = [c for c in cols if c not in vol_core]
        mut_cols = [c for c in cols if c not in vol_mut]
        out["counts"][table] = len(rows)
        out["core_sha"][table] = _set_sha(
            _row_digest(core_cols, [r[idx[c]] for c in core_cols]) for r in rows)
        out["mutable_sha"][table] = _set_sha(
            _row_digest(mut_cols, [r[idx[c]] for c in mut_cols]) for r in rows)
        out["pk_sha"][table] = (_set_sha(
            _row_digest(pk_cols, [r[idx[c]] for c in pk_cols]) for r in rows)
            if pk_cols else None)
    return out


def locator_checksum(con) -> str:
    """evloc 전용 무결성 해시(MF1.3) — evidence_locator + system_provenance 내용 sha16.

    두 테이블은 store_checksum() projection 밖이라 기존 audit 체인이 변경을 전혀 덮지 않는다
    (행이 지워져도 verify_chain 은 INTACT). 배치 적용마다 이 값을 before/after 로 기록해
    전용 축을 만든다. 테이블 부재는 'absent' 로 해시에 반영 → 플래그 OFF ledger 도 안정값.
    """
    h = hashlib.sha256()
    for table in EVLOC_TABLES:
        h.update(("\x1ftable:%s\x1f" % table).encode("utf-8"))
        cols = sorted(table_columns(con, table))
        if not cols:
            h.update(b"absent")
            continue
        sel = ", ".join(_q(c) for c in cols)
        rows = con.execute("SELECT %s FROM %s" % (sel, _q(table)))
        h.update(_set_sha(_row_digest(cols, r) for r in rows).encode("ascii"))
    return h.hexdigest()[:16]


# ── 백업 ─────────────────────────────────────────────────────────────────────
def _connect_source_readonly(path):
    """백업 소스 연결 — write 0 보장. (con, mode) 반환."""
    try:
        uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()   # 실제 열림 확인
        return con, "mode=ro"
    except Exception:   # noqa: BLE001  (sqlite3.Error·URI 변환 실패 모두 폴백 대상)
        try:
            con.close()
        except Exception:
            pass
        con = sqlite3.connect(path)
        con.execute("PRAGMA query_only=ON")
        return con, "query_only"


def _backup_verify(src_con, dst_path):
    """사본이 원본과 같은지 검증. 불일치면 BackupVerifyError raise. 통과 시 요약 dict 반환.

    ★ 절대 숫자(500/602/500 …)를 판정식에 넣지 않는다(NEW1.9) — 사장님이 한 번만 저장해도
    오경보가 나고, 그러면 게이트는 '원래 안 맞는 것'이 되어 진짜 손실을 못 잡는다. 전부 원본
    대비 **상대 대조**다.
    """
    def _tables(con):
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}

    def _counts(con, names):
        return {t: con.execute("SELECT count(*) FROM %s" % _q(t)).fetchone()[0] for t in names}

    def _meta(con):
        if not has_table(con, "audit_meta"):
            return {}
        return {k: v for k, v in con.execute("SELECT key, value FROM audit_meta")}

    dst = sqlite3.connect(dst_path)
    try:
        qc = dst.execute("PRAGMA quick_check").fetchone()[0]
        if qc != "ok":
            raise BackupVerifyError("사본 quick_check 실패: %s" % qc)
        st, dt = _tables(src_con), _tables(dst)
        if st != dt:
            raise BackupVerifyError("테이블 집합 불일치: 원본-사본=%s / 사본-원본=%s"
                                    % (sorted(st - dt), sorted(dt - st)))
        sc, dc = _counts(src_con, sorted(st)), _counts(dst, sorted(dt))
        diff = {t: (sc[t], dc[t]) for t in sc if sc[t] != dc[t]}
        if diff:
            raise BackupVerifyError("행수 불일치(원본,사본): %s" % diff)
        sm, dm = _meta(src_con), _meta(dst)
        if sm != dm:
            keys = sorted(set(sm) | set(dm))
            raise BackupVerifyError("audit_meta 불일치: %s"
                                    % {k: (sm.get(k), dm.get(k)) for k in keys
                                       if sm.get(k) != dm.get(k)})
        sv = int(src_con.execute("PRAGMA user_version").fetchone()[0])
        dv = int(dst.execute("PRAGMA user_version").fetchone()[0])
        if sv != dv:
            raise BackupVerifyError("user_version 불일치: %d != %d" % (sv, dv))
        return {"tables": len(st), "counts": dc, "user_version": dv, "quick_check": qc,
                "audit_meta_keys": len(dm)}
    finally:
        dst.close()


def safe_backup(src_path, dst_path, *, overwrite=True, verify=True):
    """sqlite **Online Backup API** 로 안전 백업(MF1.1). 검증 실패 시 raise.

    `shutil.copy2` + `PRAGMA wal_checkpoint(TRUNCATE)` 경로는 쓰지 않는다 — 리더가 읽기
    트랜잭션을 잡고 있으면 checkpoint 가 busy 로 실패하고(반환값을 버리면 무음) main 파일만
    복사돼 **조용히 잘린 백업**이 만들어진다(실증: 501행 중 1행). Online Backup API 는 WAL
    잔존분을 포함하고 잠금 안전하다.
    반환: {"src","dst","mode","verified", ...행수 요약}
    """
    src_path, dst_path = os.path.abspath(str(src_path)), os.path.abspath(str(dst_path))
    if not os.path.exists(src_path):
        raise FileNotFoundError("백업 소스 없음: %s" % src_path)
    if os.path.abspath(src_path) == os.path.abspath(dst_path):
        raise ValueError("src 와 dst 가 같은 경로")
    if os.path.exists(dst_path):
        if not overwrite:
            raise FileExistsError("백업 대상이 이미 존재: %s" % dst_path)
        # 잔존 -wal/-shm 을 함께 제거하지 않으면 새 main 파일과 옛 WAL 이 섞여 사본이 오염된다.
        for suffix in ("", "-wal", "-shm"):
            p = dst_path + suffix
            if os.path.exists(p):
                os.remove(p)
    src, mode = _connect_source_readonly(src_path)
    try:
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
        summary = _backup_verify(src, dst_path) if verify else {}
    finally:
        src.close()
    summary.update({"src": src_path, "dst": dst_path, "mode": mode, "verified": bool(verify)})
    return summary


# ── selftest ──────────────────────────────────────────────────────────────────
def _selftest() -> int:
    import tempfile

    fails = []
    _env_at_start = os.environ.get(EVLOC_FLAG_ENV)   # 플래그 오염 방지 확인용(케이스 19)

    def ck(name, cond):
        print("[%s] %s" % ("PASS" if cond else "FAIL", name))
        if not cond:
            fails.append(name)

    tmp = tempfile.mkdtemp(prefix="binggu_schema_st_")
    dbp = os.path.join(tmp, "ledger.sqlite")
    con = sqlite3.connect(dbp)

    # 1) apply 2회 idempotent (에러 0)
    v1 = apply_schema(con)
    v2 = apply_schema(con)
    ck("apply_schema idempotent 2회 (에러 0)", v1 == SCHEMA_VERSION and v2 == SCHEMA_VERSION)

    # 2) 전 테이블 존재
    have_tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ck("전 정본 테이블 생성", set(_active_table_columns()).issubset(have_tables))

    # 3) 각 테이블 정본 컬럼 전부 존재
    all_cols_ok = True
    for table, ddls in _active_table_columns().items():
        want = {d.split()[0] for d in ddls}
        have = _existing_columns(con, table)
        if not want.issubset(have):
            all_cols_ok = False
            print("   missing in %s: %s" % (table, want - have))
    ck("각 테이블 정본 컬럼 전부 존재", all_cols_ok)

    # 4) 인덱스 존재 (플래그별 기대 집합 — NEW1.4)
    have_idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    want_idx = {n for n, _, _ in _active_indexes()}
    ck("정본 인덱스 전부 생성(플래그=%s)" % ("ON" if evloc_enabled() else "OFF"),
       want_idx.issubset(have_idx))

    # 5) user_version 기록
    ck("user_version == SCHEMA_VERSION", schema_version(con) == SCHEMA_VERSION)
    con.close()

    # 6) 구 ledger(컬럼 결여) 비파괴 마이그레이션 — 기존 행 보존 + 누락 컬럼 보강
    legacy = os.path.join(tmp, "legacy.sqlite")
    lc = sqlite3.connect(legacy)
    lc.executescript(
        "CREATE TABLE nodes(node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT,"
        " candidate INT, state TEXT, content_hash TEXT);"
        "CREATE TABLE edges(edge_id TEXT, relation TEXT, source TEXT, target TEXT);"
    )
    lc.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state)"
               " VALUES('old1','judgment','옛 노드',0,'active')")
    lc.commit()
    apply_schema(lc)
    ncols = _existing_columns(lc, "nodes")
    row = lc.execute("SELECT sentence, semantic_subtype, use_count, speaker FROM nodes"
                     " WHERE node_id='old1'").fetchone()
    ck("legacy nodes 누락컬럼 ALTER 보강(비파괴)",
       {"semantic_subtype", "use_count", "speaker", "created_at", "pack_id"}.issubset(ncols)
       and row[0] == "옛 노드" and row[1] is None and row[2] in (0, None))
    ecols = _existing_columns(lc, "edges")
    ck("legacy edges 누락컬럼 ALTER 보강",
       {"candidate", "state", "evidence_refs", "pack_id", "content_hash", "created_at"}.issubset(ecols))
    ck("legacy user_version 승격", schema_version(lc) == SCHEMA_VERSION)
    lc.close()

    # 7) 관대 INSERT — 최소 컬럼만 지정해도 삽입 성공(DEFAULT 채움)
    con2 = sqlite3.connect(os.path.join(tmp, "insert.sqlite"))
    apply_schema(con2)
    try:
        con2.execute("INSERT INTO nodes(node_id) VALUES('n1')")
        con2.commit()
        r = con2.execute("SELECT candidate, promotion_allowed, use_count, state FROM nodes"
                         " WHERE node_id='n1'").fetchone()
        ins_ok = r == (0, 0, 0, "active")
    except sqlite3.Error as e:
        ins_ok = False
        print("   insert err:", e)
    ck("관대 DEFAULT(candidate=0/state='active') INSERT", ins_ok)
    con2.close()

    # 8) staging=True — nodes.candidate DEFAULT 1(미확정), 그 외 정본과 동일
    con3 = sqlite3.connect(os.path.join(tmp, "staging.sqlite"))
    apply_schema(con3, staging=True)
    try:
        con3.execute("INSERT INTO nodes(node_id) VALUES('s1')")
        con3.commit()
        r = con3.execute("SELECT candidate, promotion_allowed, use_count, state FROM nodes"
                         " WHERE node_id='s1'").fetchone()
        # candidate DEFAULT 1(staging), 나머지 DEFAULT 는 정본과 동일
        stg_ok = r == (1, 0, 0, "active")
        # edges.candidate 는 staging 에서도 DEFAULT 0(정본 유지) — nodes 만 변경 확인
        con3.execute("INSERT INTO edges(edge_id) VALUES('se1')")
        con3.commit()
        ec = con3.execute("SELECT candidate FROM edges WHERE edge_id='se1'").fetchone()
        stg_edge_ok = ec == (0,)
    except sqlite3.Error as e:
        stg_ok = stg_edge_ok = False
        print("   staging insert err:", e)
    ck("staging=True → nodes.candidate DEFAULT 1", stg_ok)
    ck("staging=True → edges.candidate 는 DEFAULT 0(정본 유지·nodes만 변경)", stg_edge_ok)
    con3.close()

    # 9) default(staging=False) 는 정본 DEFAULT 0 불변 재확인(회귀 봉인)
    con4 = sqlite3.connect(os.path.join(tmp, "prod.sqlite"))
    apply_schema(con4)  # 기존 시그니처 호출 = staging 미지정
    con4.execute("INSERT INTO nodes(node_id) VALUES('p1')")
    con4.commit()
    prod_cand = con4.execute("SELECT candidate FROM nodes WHERE node_id='p1'").fetchone()
    ck("default(staging 생략) → nodes.candidate DEFAULT 0(정본 불변)", prod_cand == (0,))
    con4.close()

    # ── evloc 축 (BINGGU_EVLOC_V5) ───────────────────────────────────────────
    def _sysobjs(c):
        t = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                     " AND name NOT LIKE 'sqlite_%'")}
        i = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'"
                                     " AND name NOT LIKE 'sqlite_%'")}
        return t, i

    # 10) 플래그 OFF(기본) — 신규 테이블/인덱스 0, user_version 불변. 기존 동작 완전 동일.
    with evloc_env(False):
        off = sqlite3.connect(os.path.join(tmp, "evloc_off.sqlite"))
        apply_schema(off)
        t_off, i_off = _sysobjs(off)
        ck("OFF: 테이블 집합 == v4 정본 그대로(신규 0)", t_off == set(_TABLE_COLUMNS))
        ck("OFF: 인덱스 집합 == 기존 _INDEXES 그대로(신규 0)",
           i_off == {n for n, _, _ in _INDEXES})
        ck("OFF: evidence_locator/system_provenance 부재",
           not has_table(off, "evidence_locator") and not has_table(off, "system_provenance"))
        ck("OFF: user_version == 4(SCHEMA_VERSION 불변)",
           schema_version(off) == 4 == SCHEMA_VERSION)
        ck("OFF: apply_schema 재실행 예외 0(멱등)", apply_schema(off) == SCHEMA_VERSION)
        off_probe = integrity_probe(off)
        off_loc = locator_checksum(off)
        off.close()

    # 11) 플래그 ON — 테이블·컬럼·인덱스 생성 + ★user_version 은 그대로 4 (NEW2.6)
    onp = os.path.join(tmp, "evloc_on.sqlite")
    with evloc_env(True):
        on = sqlite3.connect(onp)
        apply_schema(on)
        t_on, i_on = _sysobjs(on)
        ck("ON: evloc 테이블 2종 생성", set(EVLOC_TABLES).issubset(t_on))
        cols_ok = True
        for table, ddls in _EVLOC_TABLE_COLUMNS.items():
            want = {d.split()[0] for d in ddls}
            if not want.issubset(set(table_columns(on, table))):
                cols_ok = False
                print("   missing in %s: %s" % (table, want - set(table_columns(on, table))))
        ck("ON: evloc 정본 컬럼 전부 존재", cols_ok)
        ck("ON: evloc 인덱스 생성", {n for n, _, _ in _EVLOC_INDEXES}.issubset(i_on))
        ck("ON: user_version 은 여전히 4(정식 v5 마이그레이션 스쿼팅 금지·NEW2.6)",
           schema_version(on) == 4)
        ck("ON: v4 정본 테이블 집합 무변경(추가만)", set(_TABLE_COLUMNS).issubset(t_on))

        # 12) UNIQUE(evidence_id, source_id, locator, excerpt_sha) 멱등
        for _ in range(2):
            on.execute("INSERT OR IGNORE INTO evidence_locator"
                       "(loc_id,evidence_id,source_id,locator,excerpt_sha,excerpt_text,created_at)"
                       " VALUES(?,?,?,?,?,?,?)",
                       ("L1", "EVC-1", "s.md", "line:3:off:0", "a" * 64, "원문 발췌", "T0"))
        on.execute("INSERT OR IGNORE INTO evidence_locator"
                   "(loc_id,evidence_id,source_id,locator,excerpt_sha)"
                   " VALUES('L2','EVC-1','s.md','line:3:off:0',?)", ("a" * 64,))
        on.execute("INSERT INTO system_provenance(prov_id,subject_kind,subject_id,parser)"
                   " VALUES('P1','node','N1','md')")
        on.commit()
        n_loc = on.execute("SELECT count(*) FROM evidence_locator").fetchone()[0]
        ck("ON: UNIQUE 4튜플 중복 INSERT OR IGNORE → 1행", n_loc == 1)
        ck("ON: system_provenance.evidence_eligible DEFAULT 0(증거 불인정)",
           on.execute("SELECT evidence_eligible FROM system_provenance"
                      " WHERE prov_id='P1'").fetchone()[0] == 0)
        on.execute("INSERT INTO nodes(node_id,sentence,use_count) VALUES('n_evloc','문장',0)")
        on.commit()
        on_probe, on_loc = integrity_probe(on), locator_checksum(on)
        ck("locator_checksum: 행 존재 시 OFF(빈) 값과 다름", on_loc != off_loc)
        on.close()

    # 13) 다시 OFF 로 재open — 예외 0 · 테이블 무접촉 · 데이터 불변(다운그레이드 안전)
    with evloc_env(False):
        try:
            back = sqlite3.connect(onp)
            v_back = apply_schema(back)
            re_ok = (v_back == SCHEMA_VERSION and has_table(back, "evidence_locator")
                     and back.execute("SELECT count(*) FROM evidence_locator").fetchone()[0] == 1
                     and integrity_probe(back) == on_probe
                     and locator_checksum(back) == on_loc)
            back.close()
        except Exception as e:      # noqa: BLE001
            re_ok = False
            print("   downgrade reopen err:", e)
        ck("ON→OFF 재open: 예외 0 · evloc 테이블/행 무접촉 · probe 불변", re_ok)

    # 14) post-apply 봉인 — 동명이지만 스키마가 다른 선재 테이블이면 raise(fail-open 금지)
    with evloc_env(True):
        badp = os.path.join(tmp, "evloc_bad.sqlite")
        bad = sqlite3.connect(badp)
        bad.execute("CREATE TABLE evidence_locator(other_pk TEXT PRIMARY KEY, evidence_id TEXT)")
        bad.commit()
        try:
            apply_schema(bad)
            raised = False
        except EvlocSchemaError as e:
            raised = "loc_id" in str(e)
        except Exception as e:      # noqa: BLE001
            raised = False
            print("   unexpected err:", type(e).__name__, e)
        bad_idx = {r[0] for r in bad.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        bad.close()
        ck("ON + 스키마 불일치 선재 테이블 → EvlocSchemaError raise(사유 포함)", raised)
        ck("ON 실패 시 잘못된 테이블에 인덱스 생성 0", "idx_evloc_evidence" not in bad_idx)

    # 15) has_table / table_columns — write 경로가 env 아닌 ledger 실재로 판단(NEW2.5)
    probe_con = sqlite3.connect(onp)
    with evloc_env(False):      # env 는 OFF 인데도 실재 테이블은 True 로 보여야 한다
        ck("has_table: env OFF 여도 실재 테이블은 True", has_table(probe_con, "evidence_locator"))
    ck("has_table: 없는 테이블은 False", not has_table(probe_con, "nope_table"))
    ck("table_columns: 실재 컬럼 반환", "excerpt_sha" in table_columns(probe_con, "evidence_locator"))
    ck("table_columns: 부재 테이블은 []", table_columns(probe_con, "nope_table") == [])

    # 16) locator_checksum — 행 변화 검출 + 삭제 시 원복(전용 무결성 축·MF1.3)
    lc0 = locator_checksum(probe_con)
    probe_con.execute("UPDATE evidence_locator SET excerpt_text='변조' WHERE loc_id='L1'")
    probe_con.commit()
    lc1 = locator_checksum(probe_con)
    probe_con.execute("DELETE FROM evidence_locator WHERE loc_id='L1'")
    probe_con.commit()
    lc2 = locator_checksum(probe_con)
    ck("locator_checksum: excerpt_text 변조 검출", lc0 != lc1)
    ck("locator_checksum: 행 삭제 검출", lc2 not in (lc0, lc1))
    probe_con.close()
    off2 = sqlite3.connect(os.path.join(tmp, "evloc_off.sqlite"))
    # 테이블 부재('absent')와 '빈 테이블'은 서로 다른 값이다 — DROP 도 변화로 잡히도록 의도한 구분.
    ck("locator_checksum: 테이블 부재 ledger 안정값(재계산 동일)", locator_checksum(off2) == off_loc)
    off2.close()

    # 17) integrity_probe — 휘발 컬럼 2층 (MF1.2 · NEW2.8)
    ip = sqlite3.connect(os.path.join(tmp, "probe.sqlite"))
    apply_schema(ip)
    for i in range(3):
        ip.execute("INSERT INTO nodes(node_id,sentence,use_count,state)"
                   " VALUES(?,?,0,'active')", ("p%d" % i, "문장%d" % i))
    ip.commit()
    p0 = integrity_probe(ip)
    ip.execute("UPDATE nodes SET use_count=use_count+1")
    ip.commit()
    p1 = integrity_probe(ip)
    ck("probe: audit 밖 use_count UPDATE 는 무결성 위반 아님(before==after)", p0 == p1)
    ip.execute("UPDATE nodes SET state='tombstoned' WHERE node_id='p0'")
    ip.commit()
    p2 = integrity_probe(ip)
    ck("probe: state 파괴는 core_sha 로 검출(과잉 제외 금지·NEW2.8)",
       p2["core_sha"]["nodes"] != p1["core_sha"]["nodes"])
    ck("probe: state 는 mutable_sha 에서만 제외(2층 분리 동작)",
       p2["mutable_sha"]["nodes"] == p1["mutable_sha"]["nodes"])
    ip.execute("DELETE FROM nodes WHERE node_id='p1'")
    ip.commit()
    p3 = integrity_probe(ip)
    ck("probe: 행 삭제 검출(counts·pk_sha)",
       p3["counts"]["nodes"] == 2 and p3["pk_sha"]["nodes"] != p2["pk_sha"]["nodes"])
    ck("probe: 기본 대상은 v4 정본 테이블(evloc 제외 → 플래그 가로질러 안정)",
       "evidence_locator" not in p3["tables"] and off_probe["tables"] == p0["tables"])
    ip.close()

    # 18) safe_backup — WAL + 살아있는 리더(읽기 트랜잭션) 상태에서도 전건 백업 (MF1.1)
    live = os.path.join(tmp, "live.sqlite")
    w = sqlite3.connect(live)
    w.execute("PRAGMA journal_mode=WAL")
    w.execute("CREATE TABLE t(i INTEGER PRIMARY KEY, v TEXT)")
    w.executemany("INSERT INTO t(i,v) VALUES(?,?)", [(i, "v%d" % i) for i in range(500)])
    w.commit()
    reader = sqlite3.connect(live)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM t").fetchone()          # 읽기 트랜잭션 점유 유지
    snap = os.path.join(tmp, "live_snap.sqlite")
    try:
        summ = safe_backup(live, snap)
        sc = sqlite3.connect(snap)
        n_snap = sc.execute("SELECT count(*) FROM t").fetchone()[0]
        sc.close()
        bk_ok = (n_snap == 500 and summ["verified"] and summ["counts"]["t"] == 500)
    except Exception as e:      # noqa: BLE001
        bk_ok = False
        n_snap = -1
        print("   safe_backup err:", type(e).__name__, e)
    ck("safe_backup: 리더 점유(WAL) 중에도 사본 행수 == 원본(500) [실측 %d]" % n_snap, bk_ok)

    # 잘린 사본은 반드시 raise — 검증이 실제로 작동함을 못박음
    trunc = os.path.join(tmp, "trunc.sqlite")
    safe_backup(live, trunc)
    tc = sqlite3.connect(trunc)
    tc.execute("DELETE FROM t WHERE i > 0")
    tc.commit()
    tc.close()
    src_con, _mode = _connect_source_readonly(live)
    try:
        _backup_verify(src_con, trunc)
        vf_ok = False
    except BackupVerifyError:
        vf_ok = True
    finally:
        src_con.close()
    ck("safe_backup 검증: 행수 불일치 사본 → BackupVerifyError raise", vf_ok)
    ck("safe_backup: 소스 없음 → FileNotFoundError",
       _raises(FileNotFoundError, safe_backup, os.path.join(tmp, "nope.sqlite"), snap))
    reader.rollback()
    reader.close()
    w.close()

    # 19) 플래그 원복 확인 — selftest 가 프로세스 env 를 오염시키지 않는다
    ck("selftest 종료 시 %s 원복" % EVLOC_FLAG_ENV,
       (os.environ.get(EVLOC_FLAG_ENV) == _env_at_start))

    print("GATE=%s (%d fail)" % ("GO" if not fails else "STOP", len(fails)))
    return 0 if not fails else 1


def _raises(exc, fn, *a, **kw) -> bool:
    try:
        fn(*a, **kw)
        return False
    except exc:
        return True


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("binggu_schema: SCHEMA_VERSION=%d, tables=%d, indexes=%d (%s=%s)"
          % (SCHEMA_VERSION, len(_active_table_columns()), len(_active_indexes()),
             EVLOC_FLAG_ENV, "1" if evloc_enabled() else "unset"))
