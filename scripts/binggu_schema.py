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

import sqlite3
import uuid

# user_version(PRAGMA)로 기록되는 정본 스키마 버전. 마이그레이션 게이트 키.
# v2 (P1-A trusted approval event): approval_requests·approval_consumptions 테이블 + audit_meta['ledger_id'].
SCHEMA_VERSION = 2

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

# ALTER TABLE ADD COLUMN 으로 안전하게 보강 가능한 컬럼만(제약/AUTOINCREMENT/PK 제외).
_ADDABLE_DEFAULT_NONCONST = ("PRIMARY KEY", "AUTOINCREMENT", "UNIQUE", "NOT NULL")


def _create_table_sql(table: str, staging: bool = False) -> str:
    cols = list(_TABLE_COLUMNS[table])
    # staging(미확정 스테이징 ledger): nodes.candidate 기본값을 1(=미확정)로.
    # 정본(production)은 DEFAULT 0. 그 외 컬럼/제약은 완전히 동일(상위집합 유지).
    if staging and table == "nodes":
        cols = ["candidate INTEGER DEFAULT 1" if c.startswith("candidate ") else c
                for c in cols]
    cols += _TABLE_CONSTRAINTS.get(table, [])
    return "CREATE TABLE IF NOT EXISTS %s(%s)" % (table, ", ".join(cols))


def _addable_columns(table: str):
    """마이그레이션으로 ALTER ADD 가능한 (col_name, ddl) 목록.

    PK/AUTOINCREMENT/UNIQUE/NOT NULL 컬럼은 ALTER ADD 불가 → 제외(신규 테이블에서만 존재).
    """
    out = []
    for ddl in _TABLE_COLUMNS[table]:
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


def _migrate(con):
    """구 ledger(컬럼 결여) 비파괴 보강. 실패는 무해 무시."""
    for table in _TABLE_COLUMNS:
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


def apply_schema(con, staging=False):
    """정본 스키마를 con 에 idempotent 하게 적용.

    - 전 테이블 CREATE IF NOT EXISTS + 인덱스 CREATE IF NOT EXISTS.
    - user_version < SCHEMA_VERSION 이면 누락 컬럼 경량 마이그레이션.
    - PRAGMA user_version 을 SCHEMA_VERSION 으로 설정.
    - staging=True: nodes.candidate DEFAULT 1(미확정 스테이징 의미). 그 외 전부 동일.
      기존 apply_schema(con) 호출(default False)은 정본(DEFAULT 0)으로 불변.
      DEFAULT 는 CREATE 시점 신규 테이블에만 적용(IF NOT EXISTS·기존 테이블 불변).
    반환: 적용 후 user_version(int).
    """
    for table in _TABLE_COLUMNS:
        con.execute(_create_table_sql(table, staging=staging))
    # 인덱스가 참조하는 컬럼이 구 ledger 에 없을 수 있으므로 마이그레이션을 먼저 수행.
    if _user_version(con) < SCHEMA_VERSION:
        _migrate(con)
        con.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
    for name, table, cols in _INDEXES:
        con.execute("CREATE INDEX IF NOT EXISTS %s ON %s(%s)" % (name, table, cols))
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


# ── selftest ──────────────────────────────────────────────────────────────────
def _selftest() -> int:
    import os
    import tempfile

    fails = []

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
    ck("전 정본 테이블 생성", set(_TABLE_COLUMNS).issubset(have_tables))

    # 3) 각 테이블 정본 컬럼 전부 존재
    all_cols_ok = True
    for table, ddls in _TABLE_COLUMNS.items():
        want = {d.split()[0] for d in ddls}
        have = _existing_columns(con, table)
        if not want.issubset(have):
            all_cols_ok = False
            print("   missing in %s: %s" % (table, want - have))
    ck("각 테이블 정본 컬럼 전부 존재", all_cols_ok)

    # 4) 인덱스 존재
    have_idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    want_idx = {n for n, _, _ in _INDEXES}
    ck("정본 인덱스 전부 생성", want_idx.issubset(have_idx))

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

    print("GATE=%s (%d fail)" % ("GO" if not fails else "STOP", len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("binggu_schema: SCHEMA_VERSION=%d, tables=%d, indexes=%d"
          % (SCHEMA_VERSION, len(_TABLE_COLUMNS), len(_INDEXES)))
