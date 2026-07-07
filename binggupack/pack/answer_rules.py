# -*- coding: utf-8 -*-
"""binggu_answer_rules — 회상(preflight_context) → 실행 규칙(answer_rules) 변환 · 순수 read-only.

'판단 개입'의 실체. preflight_context 가 돌려준 회상 신호(remember/avoid_patterns/preferences/
question)와 detect_conflicts 가 돌려준 충돌을, 사람이 곧바로 따를 수 있는 '그래서 이렇게 하라'
행동지시(answer_rules)로 결정적 템플릿 변환한다. LLM 0 · hallucination 0 · write 0.

각 rule:
  {rule_type(avoid/prefer/ask/remember/conflict), text(행동지시 원문), scope,
   relevance, supersedes_applied, provenance{evidence_ref(sha8)·confirmable:False·...}}.

개정 판단(supersedes):
  ledger nodes.supersedes 컬럼(NEW 노드행의 값 = 대체된 OLD 노드 id — 근거:
  openbinggu_candidate_replace_ux.py:226 `UPDATE nodes SET supersedes=OLD WHERE node_id=NEW`)을
  mode=ro 로 읽어 {old:new} 맵을 만든다. 대체된(old) 판단의 remember/avoid/prefer 규칙은 제외
  (최신 우선)하고, conflict 는 유지하되 provenance.superseded_by 로 '이후 개정됨' 표기한다.

scope:
  nodes 에는 scope 컬럼이 없다. 권위 도메인은 오직 hit_events.domain(사람이 resolve 시 actor=human
  으로 입력·hit_stats.record_resolution → _domain_norm 소문자화)만 근거로 쓴다. cwd 파생 프록시를
  하드필터로 쓰지 않으며((E) 방어), scope 미상(hit_events 증거 0) 노드는 절대 배제하지 않는다
  (허구 배제 금지). 요청 scope 가 있고 그 노드의 권위 도메인 증거가 전부 불일치일 때만 배제한다.

정렬:
  (rule_type 우선순위, -relevance, evidence_ref) 로만 결정적 정렬. rank_score/use_count 는 정렬
  key 에 절대 진입시키지 않는다((use_count) 방어 — hit_stats guard3 정신 계승).

★ 사람 노출 표면(MUST_FIX D-1):
  본 모듈이 사람에게 보여줄 유일한 표면은 render_answer_rules_md() 다. build_answer_rules() 가
  돌려주는 raw dict 를 그대로 사람에게 덤프하지 말 것(소비처 계약). provenance 에는 raw ledger
  node_id 를 담지 않는다 — evidence_ref = sha256(node_id)[:8] 만 노출하고 confirmable:False 를
  박아 confirm 위조 표면을 신설하지 않는다. supersedes/scope 조인은 build 내부에서 이미 완료되므로
  in-scope 소비처는 raw node_id 가 필요 없다. (본 모듈은 write/confirm 경로 0.)

불변(헌법):
  - 순수 read-only — ledger 는 sqlite file:...?mode=ro 로만 읽음. write 0 · self-modifying 0.
  - raw PII/secret/node_id 노출 0(evidence_ref sha8 만).
  - 빈 입력 graceful — preflight None/{} · ledger 부재 · 컬럼/테이블 부재 · 예외 전부 에러 0.
  - stdlib only(hashlib/os/sqlite3). 결정적(동일 입력 → 동일 출력).
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys

# rule_type 정렬 1차 key. conflict(주의 환기) 최상단 · remember 최하단.
_PRIORITY = {"conflict": 0, "ask": 1, "avoid": 2, "prefer": 3, "remember": 4}

# 안전/무결성 mandate 도메인 — detect_conflicts 가 이미 SKIP 하지만 방어 심층(MUST_FIX ③).
_SAFETY_DOMAINS = ("safety", "integrity")


# ---------------- 정규화 · 근거 참조(sha8) ----------------

def _norm_scope(x):
    """scope/도메인 정규화 — 소문자 trim. 빈 값은 None(미상)."""
    if x is None:
        return None
    s = str(x).strip().lower()
    return s or None


def _evidence_ref(node_id):
    """node_id → sha8(사람 노출용 · confirm 불가 · D-1). raw node_id 미노출.

    node_id 가 없으면 None(그래도 confirmable:False 라 confirm 표면 신설 0).
    """
    if not node_id:
        return None
    return hashlib.sha256(str(node_id).encode("utf-8", "replace")).hexdigest()[:8]


# ---------------- ledger 로드(read-only) ----------------

def _load_supersedes(ledger_path):
    """ledger nodes.supersedes → {old_id: new_id} (mode=ro · write 0).

    NEW 노드행의 supersedes 값 = 대체된 OLD 노드 id(replace_ux 배선). NEW 노드가 active/confirmed
    일 때만 개정으로 인정한다. ORDER BY node_id 로 결정성 보장(MUST_FIX ②) — 같은 old 를 여럿이
    대체하는 경계에서도 마지막 승자가 결정적. 파일/컬럼 부재·예외 전부 graceful {} 반환.
    """
    out = {}
    if not ledger_path or not os.path.exists(ledger_path):
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % ledger_path, uri=True)
        cur = con.cursor()
        cols = [c[1] for c in cur.execute("PRAGMA table_info(nodes)")]
        if "supersedes" not in cols:
            con.close()
            return out
        state_sel = "state" if "state" in cols else "NULL AS state"
        rows = cur.execute(
            "SELECT node_id, supersedes, " + state_sel + " FROM nodes "
            "WHERE supersedes IS NOT NULL AND supersedes != '' ORDER BY node_id"
        ).fetchall()
        con.close()
    except Exception:
        return out
    for new_id, old_id, state in rows:
        if state in (None, "active", "confirmed") and old_id:
            out[old_id] = new_id
    return out


def _load_scope_map(ledger_path):
    """ledger hit_events.domain → {node_id: frozenset(권위 도메인)} (mode=ro · write 0).

    hit_events.domain 은 hit_stats.record_resolution 이 사람 resolve(actor=human) 시에만
    _domain_norm(소문자)으로 기록 → 권위 도메인(cwd 프록시 아님 · (E) 방어).
    테이블/컬럼 부재·예외 전부 graceful {} 반환.
    """
    out = {}
    if not ledger_path or not os.path.exists(ledger_path):
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % ledger_path, uri=True)
        cur = con.cursor()
        tbls = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "hit_events" not in tbls:
            con.close()
            return out
        cols = [c[1] for c in cur.execute("PRAGMA table_info(hit_events)")]
        if "domain" not in cols or "node_id" not in cols:
            con.close()
            return out
        rows = cur.execute(
            "SELECT node_id, domain FROM hit_events "
            "WHERE domain IS NOT NULL AND domain != '' ORDER BY node_id"
        ).fetchall()
        con.close()
    except Exception:
        return out
    tmp = {}
    for nid, dom in rows:
        if nid:
            tmp.setdefault(nid, set()).add(_norm_scope(dom))
    return {k: frozenset(v) for k, v in tmp.items()}


def _scope_decision(node_scopes, requested):
    """(keep, resolved_scope) — 권위 도메인 증거만 근거. 미상은 절대 배제 0((E)).

    node_scopes 없음(증거 0) → 항상 유지(허구 배제 금지).
    요청 scope 없음         → 유지 + 대표 scope(사전순 첫째)로 표기.
    요청 scope 가 증거집합에 있음 → 유지 + 그 scope.
    요청 scope 가 증거와 전부 불일치  → 배제(권위 도메인 불일치일 때만).
    """
    req = _norm_scope(requested)
    if not node_scopes:
        return True, None
    if req is None:
        return True, sorted(node_scopes)[0]
    if req in node_scopes:
        return True, req
    return False, None


# ---------------- 공통 방출기(remember/avoid/prefer) ----------------

def _emit(items, rule_type, text_fn, source_kind, ctx, out):
    """preflight 항목 리스트 → rule 방출(supersedes 제외 · scope 배제 적용). raw node_id 미노출."""
    for it in (items or []):
        nid = it.get("node_id")
        if nid and nid in ctx["superseded_olds"]:
            continue  # ③ 대체된(old) 판단 규칙 제외 — 최신 우선
        node_scopes = ctx["scope_map"].get(nid) if (ctx["scope_map"] and nid) else None
        keep, rscope = _scope_decision(node_scopes, ctx["scope"])
        if not keep:
            continue  # ④ 권위 도메인 불일치만 제외(미상은 유지)
        claim = (it.get("claim") or "")[:ctx["claim_cap"]]
        out.append({
            "rule_type": rule_type,
            "text": text_fn(claim, it),
            "scope": rscope,
            "relevance": round(float(it.get("relevance") or 0.0), 4),
            "supersedes_applied": bool(ctx["supersede_map"]),
            "provenance": {
                "evidence_ref": _evidence_ref(nid),   # sha8 만(D-1 · raw node_id 미노출)
                "confirmable": False,
                "subtype": it.get("semantic_subtype") or it.get("subtype"),
                "source_kind": source_kind,
                "superseded_by": None,                # 방출된 규칙은 최신 생존분(피대체 아님)
            },
        })


# ---------------- 공개 API ----------------

def build_answer_rules(preflight_out, conflicts=None, scope=None, ledger_path=None,
                       supersede_map=None, scope_map=None, claim_cap=100):
    """preflight_context 출력(+detect_conflicts 출력) → answer_rules 리스트. 순수 read · write 0.

    입력 계약:
      preflight_out : binggu_recall.preflight_context(...) 반환 dict
                      (remember/avoid_patterns/preferences/needs_question/question 사용).
      conflicts     : **반드시 binggu_contrast_protocol.detect_conflicts(...) 출력**을 넘길 것.
                      detect_conflicts 는 안전/무결성 mandate(_policy_is_safety)를 이미 SKIP 한
                      상태로 반환한다. 본 함수는 그 계약을 신뢰하되, 입력이 그 출력이 아닐
                      가능성 대비 방어 심층으로 safety/integrity mandate 를 1줄 가드로 재차 건너뛴다
                      (MUST_FIX ③). 각 conflict 는 {binggu_item, mandate, quote_status, relevance,
                      conflict_id} 형식.
      scope         : 요청 도메인(선택). 권위 hit_events 증거가 없으면 배제 0(항상 안전).
      ledger_path   : supersede_map/scope_map 미주입 시 여기서 mode=ro 로 읽음.
      supersede_map / scope_map : 테스트/재사용 주입용(주입 시 ledger 재조회 skip).

    반환: list[rule]. 사람 노출은 render_answer_rules_md() 로만(raw dict 덤프 금지 · D-1).
    """
    preflight_out = preflight_out or {}
    if supersede_map is None:
        supersede_map = _load_supersedes(ledger_path) if ledger_path else {}
    if scope_map is None:
        scope_map = _load_scope_map(ledger_path) if ledger_path else {}
    ctx = {
        "supersede_map": supersede_map,
        "superseded_olds": set(supersede_map),
        "scope_map": scope_map,
        "scope": scope,
        "claim_cap": claim_cap,
    }
    out = []

    _emit(preflight_out.get("avoid_patterns"), "avoid",
          lambda c, it: "하지 마라: %s (과거 버그패턴)" % c, "preflight_avoid", ctx, out)
    _emit(preflight_out.get("preferences"), "prefer",
          lambda c, it: "이렇게 하라: %s (사용자 선호)" % c, "preflight_pref", ctx, out)
    _emit(preflight_out.get("remember"), "remember",
          lambda c, it: "기억하라: %s" % c, "preflight_remember", ctx, out)

    # 반문(위험도 높음) — preflight 가 needs_question 을 세운 경우만.
    q = preflight_out.get("question")
    if preflight_out.get("needs_question") and q:
        out.append({
            "rule_type": "ask",
            "text": "먼저 확인: %s" % str(q)[:300],
            "scope": _norm_scope(scope),
            "relevance": 1.0,
            "supersedes_applied": bool(supersede_map),
            "provenance": {
                "evidence_ref": _evidence_ref(str(q)),
                "confirmable": False,
                "subtype": None,
                "source_kind": "risk_question",
                "superseded_by": None,
            },
        })

    # 충돌(detect_conflicts 출력) — 개정 표기 유지.
    for c in (conflicts or []):
        bi = c.get("binggu_item") or {}
        md = c.get("mandate") or {}
        # ③ 방어 심층: 안전/무결성 mandate 는 대비 대상 아님(detect_conflicts 가 이미 SKIP).
        if md.get("domain") in _SAFETY_DOMAINS or md.get("is_safety") or c.get("safety"):
            continue
        nid = bi.get("node_id")
        node_scopes = scope_map.get(nid) if (scope_map and nid) else None
        keep, rscope = _scope_decision(node_scopes, scope)
        if not keep:
            continue
        superseded_by = None
        extra = ""
        if nid and nid in supersede_map:
            superseded_by = _evidence_ref(supersede_map[nid])   # 신 노드의 sha8
            extra = " (과거 판단은 이후 개정됨 — 최신 반영)"
        bclaim = (bi.get("claim") or "")[:claim_cap]
        mclaim = (md.get("clause_text") or "")[:claim_cap]
        out.append({
            "rule_type": "conflict",
            "text": ("충돌: 과거 '%s' vs 현재 강제조항 '%s' — 최신 개정 반영, 사장님이 선택"
                     "(빙구팩 결정 0)%s." % (bclaim, mclaim, extra)),
            "scope": rscope,
            "relevance": round(float(c.get("relevance") or 0.0), 4),
            "supersedes_applied": bool(supersede_map),
            "provenance": {
                "evidence_ref": _evidence_ref(nid),   # sha8 만(D-1)
                "confirmable": False,
                "subtype": bi.get("subtype"),
                "source_kind": "contrast_conflict",
                "conflict_id": c.get("conflict_id"),   # 이미 sha 기반 식별자
                "mandate_ref": md.get("ref"),          # 박제/CLAUDE.md 조항 참조(ledger id 아님)
                "quote_status": c.get("quote_status"),
                "superseded_by": superseded_by,
            },
        })

    # 정렬: rule_type 우선순위 → -relevance → evidence_ref(결정적). rank_score/use_count 진입 0.
    out.sort(key=lambda r: (_PRIORITY.get(r["rule_type"], 9),
                            -r["relevance"],
                            r["provenance"].get("evidence_ref") or ""))
    return out


def render_answer_rules_md(rules):
    """answer_rules → 사람이 읽는 마크다운(참고·강제 아님). ★ 사람 노출은 이 함수 단독(D-1).

    raw node_id 미노출 — evidence_ref(sha8)만 표기. build_answer_rules raw dict 를 사람에게
    직접 덤프하지 말고 이 렌더를 쓸 것. 빈 rules → None.
    """
    if not rules:
        return None
    lines = ["# 빙구팩 실행 규칙 (참고·강제 아님·빙구팩 결정 0)"]
    for r in rules:
        ev = r["provenance"].get("evidence_ref") or "-"
        sc = r.get("scope") or "전역"
        lines.append("- [%s·%s·ev:%s] %s" % (r["rule_type"], sc, ev, r["text"]))
    return "\n".join(lines)


# ---------------- selftest (temp ledger · 운영 write 0) ----------------

def _mk_ledger(path):
    """temp ledger 생성 — nodes(supersedes/state/semantic_subtype) + hit_events(domain).

    개정: NEW n2.supersedes='n1'(active) · OLD n1(deprecated). 이중대체 n3/n4 → same old 'ns'.
    도메인: nb→bid · nc→cook(권위 hit_events). 나머지(n1/rmX/rmY/rmZ/av1)는 scope 미상.
    """
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE nodes(node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT, "
        "state TEXT DEFAULT 'active', supersedes TEXT, semantic_subtype TEXT, use_count INTEGER DEFAULT 0)")
    con.execute(
        "CREATE TABLE hit_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT, "
        "speaker TEXT, kind TEXT, outcome TEXT, subtype TEXT, ts TEXT, domain TEXT)")
    nodes = [
        ("n1", "judgment", "과거 판단 A", "deprecated", None, "교훈"),
        ("n2", "judgment", "개정 판단 A2", "active", "n1", "교훈"),
        ("nb", "judgment", "결론부터 짧게", "active", None, "선호"),
        ("nc", "judgment", "요리 판단", "active", None, "교훈"),
        ("rmX", "judgment", "scope 미상 판단", "active", None, "교훈"),
        ("rmY", "judgment", "판단 Y", "active", None, "교훈"),
        ("rmZ", "judgment", "판단 Z", "active", None, "교훈"),
        ("av1", "judgment", "백그라운드 프로세스 안 죽임", "active", None, "버그패턴"),
        # 이중대체(ORDER BY node_id 결정성 검증용): n3·n4 둘 다 old 'ns' 대체
        ("ns", "judgment", "구 판단 S", "deprecated", None, "교훈"),
        ("n3", "judgment", "개정 S3", "active", "ns", "교훈"),
        ("n4", "judgment", "개정 S4", "active", "ns", "교훈"),
    ]
    con.executemany(
        "INSERT INTO nodes(node_id,node_type,sentence,state,supersedes,semantic_subtype) "
        "VALUES(?,?,?,?,?,?)", nodes)
    con.executemany(
        "INSERT INTO hit_events(node_id,speaker,kind,outcome,subtype,ts,domain) VALUES(?,?,?,?,?,?,?)",
        [("nb", "owner", "직감", "hit", "선호", "2026-06-20T00:00:00Z", "bid"),
         ("nc", "owner", "직감", "hit", "교훈", "2026-06-20T00:00:00Z", "cook")])
    con.commit()
    con.close()


def _selftest():
    import json
    import string
    import tempfile
    import shutil

    tmp = tempfile.mkdtemp(prefix="binggu_answer_rules_")
    dbp = os.path.join(tmp, "ledger.sqlite")
    _mk_ledger(dbp)
    mtime_before = os.path.getmtime(dbp)

    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if bool(ok) else "FAIL"))

    def _is_sha8(x):
        return isinstance(x, str) and len(x) == 8 and all(ch in string.hexdigits for ch in x)

    # ---- 맵 로드 ----
    sup = _load_supersedes(dbp)
    scope_map = _load_scope_map(dbp)

    rec(1, "_load_supersedes {old:new} — n1→n2", sup.get("n1") == "n2")
    rec(2, "_load_scope_map 권위 도메인 — nb={'bid'}·nc={'cook'}",
        scope_map.get("nb") == frozenset({"bid"}) and scope_map.get("nc") == frozenset({"cook"}))
    # MUST_FIX ②: ORDER BY node_id 결정성 — 이중대체 old 'ns' 는 마지막 승자(n4) 결정적
    sup2 = _load_supersedes(dbp)
    rec(3, "MUST_FIX② ORDER BY node_id 결정성 — 이중대체 안정(ns→n4·재호출 동일)",
        sup.get("ns") == "n4" and sup2 == sup)

    # ---- preflight 픽스처 ----
    preflight = {
        "avoid_patterns": [
            {"node_id": "av1", "claim": "백그라운드 프로세스 안 죽임", "semantic_subtype": "버그패턴",
             "relevance": 0.9},
        ],
        "preferences": [
            {"node_id": "nb", "claim": "결론부터 짧게", "relevance": 0.8},
        ],
        "remember": [
            {"node_id": "n1", "claim": "과거 판단 A", "semantic_subtype": "교훈", "relevance": 0.7},
            {"node_id": "nc", "claim": "요리 판단", "semantic_subtype": "교훈", "relevance": 0.5},
            {"node_id": "rmX", "claim": "scope 미상 판단", "semantic_subtype": "교훈", "relevance": 0.6},
            {"node_id": "rmY", "claim": "판단 Y", "relevance": 0.6},
            {"node_id": "rmZ", "claim": "판단 Z", "relevance": 0.6},
        ],
        "needs_question": True,
        "question": "요구조건 먼저 확인했나요?",
    }
    conflicts = [
        {"conflict_id": "contrast:abc123", "quote_status": "verified", "relevance": 0.85,
         "binggu_item": {"node_id": "n1", "claim": "과거 방식 X", "subtype": "선호"},
         "mandate": {"clause_text": "현재는 Y 로 해라", "ref": "CLAUDE.md §5", "stance": "forbid"}},
        # ③ 안전 mandate — 반드시 SKIP
        {"conflict_id": "contrast:safety1", "quote_status": "verified", "relevance": 0.99,
         "binggu_item": {"node_id": "av1", "claim": "안전 관련 과거", "subtype": "버그패턴"},
         "mandate": {"clause_text": "DB 파괴 금지", "ref": "CLAUDE.md §3", "stance": "forbid",
                     "domain": "safety"}},
    ]

    rules = build_answer_rules(preflight, conflicts=conflicts, scope="bid",
                               supersede_map=sup, scope_map=scope_map)

    by_type = {}
    for r in rules:
        by_type.setdefault(r["rule_type"], []).append(r)

    # ---- 행동지시 변환 ----
    avoid = by_type.get("avoid", [])
    rec(4, "avoid 규칙 생성 + '하지 마라'로 시작(행동지시 변환)",
        len(avoid) == 1 and avoid[0]["text"].startswith("하지 마라"))

    prefer = by_type.get("prefer", [])
    rec(5, "prefer(nb·domain bid) scope='bid' 유지 + rule.scope=='bid'",
        len(prefer) == 1 and prefer[0]["scope"] == "bid" and prefer[0]["text"].startswith("이렇게 하라"))

    # ---- ③ supersedes 제외 ----
    rem = by_type.get("remember", [])
    rem_refs = {r["provenance"]["evidence_ref"] for r in rem}
    rec(6, "③ 대체된 n1 remember 규칙 제외(생존분에 n1 evidence_ref 없음)",
        _evidence_ref("n1") not in rem_refs and _evidence_ref("rmX") in rem_refs)

    # ---- ④ scope 배제(미상 유지) ----
    rec(7, "④ nc(cook)는 scope='bid'서 제외 · rmX(미상)는 유지((E) 허구배제 0)",
        _evidence_ref("nc") not in rem_refs and _evidence_ref("rmX") in rem_refs)

    # ---- 반문 ----
    ask = by_type.get("ask", [])
    rec(8, "반문: needs_question True → rule_type 'ask' text '먼저 확인'",
        len(ask) == 1 and ask[0]["text"].startswith("먼저 확인"))

    # ---- ⑤ conflict + superseded_by + quote_status ----
    conf = by_type.get("conflict", [])
    rec(9, "⑤ conflict 생성 · '충돌:'로 시작 · superseded_by==ev('n2') · quote_status 보존",
        len(conf) == 1 and conf[0]["text"].startswith("충돌:")
        and conf[0]["provenance"]["superseded_by"] == _evidence_ref("n2")
        and conf[0]["provenance"]["quote_status"] == "verified")

    # ---- ③(MUST_FIX) 안전 mandate SKIP ----
    conf_ids = {r["provenance"].get("conflict_id") for r in conf}
    rec(10, "MUST_FIX③ 안전 mandate(domain=safety) 대비 SKIP(conflict rule 미생성)",
        "contrast:safety1" not in conf_ids and "contrast:abc123" in conf_ids)

    # ---- D-1(MUST_FIX①): raw node_id 0 · evidence_ref sha8 · confirmable False · provenance에 node_id 키 없음 ----
    dump = json.dumps(rules, ensure_ascii=False)
    raw_ids = ["n1", "n2", "nb", "nc", "rmX", "rmY", "rmZ", "av1", "ns", "n3", "n4"]
    # 토큰 경계로 검사(claim 텍스트 오탐 방지 — 따옴표/구분자로 감싸 raw id 자체 노출만 잡음)
    leaked = [nid for nid in raw_ids
              if ('"%s"' % nid) in dump or (":%s:" % nid) in dump or ("ref:%s" % nid) in dump]
    all_ev_sha8 = all(_is_sha8(r["provenance"]["evidence_ref"]) for r in rules
                      if r["provenance"].get("evidence_ref") is not None)
    no_nid_key = all("node_id" not in r["provenance"] for r in rules)
    all_unconfirmable = all(r["provenance"]["confirmable"] is False for r in rules)
    rec(11, "MUST_FIX① D-1: 출력 raw node_id 0 · provenance node_id 키 없음 · confirmable False",
        not leaked and no_nid_key and all_unconfirmable)
    rec(12, "MUST_FIX① D-1: evidence_ref 는 전부 sha8(hex 8자)",
        all_ev_sha8 and rem and all(_is_sha8(r["provenance"]["evidence_ref"]) for r in rem))

    # ---- 정렬 단조 ----
    prio_seq = [_PRIORITY[r["rule_type"]] for r in rules]
    rec(13, "정렬 단조: _PRIORITY 수열 == sorted(conflict<ask<avoid<prefer<remember)",
        prio_seq == sorted(prio_seq))

    # ---- (use_count) 방어: 동일 relevance remember 는 evidence_ref 로만 정렬(rank_score 무개입) ----
    rmy_ev, rmz_ev = _evidence_ref("rmY"), _evidence_ref("rmZ")
    order = [r["provenance"]["evidence_ref"] for r in rem
             if r["provenance"]["evidence_ref"] in (rmy_ev, rmz_ev)]
    rec(14, "(use_count) 방어: 동일 relevance → evidence_ref 오름차순 정렬(rank_score 무시)",
        order == sorted([rmy_ev, rmz_ev]))

    # ---- 결정성 ----
    rules2 = build_answer_rules(preflight, conflicts=conflicts, scope="bid",
                               supersede_map=sup, scope_map=scope_map)
    rec(15, "결정적: 동일 인자 재호출 결과 완전 동일",
        json.dumps(rules, ensure_ascii=False, sort_keys=True)
        == json.dumps(rules2, ensure_ascii=False, sort_keys=True))

    # ---- 빈 입력 graceful ----
    rec(16, "빈 입력 graceful: build_answer_rules({})==[] · (None)==[]",
        build_answer_rules({}) == [] and build_answer_rules(None) == [])

    # ---- render: raw node_id 0 ----
    md = render_answer_rules_md(rules)
    md_leaked = [nid for nid in raw_ids if ('"%s"' % nid) in (md or "")]
    rec(17, "render_answer_rules_md 출력 raw node_id 0 · None(빈 rules)",
        md and not md_leaked and "ev:" in md and render_answer_rules_md([]) is None)

    # ---- ledger_path 경로(주입 없이 mode=ro 로 직접 읽기) 동작 ----
    rules_ro = build_answer_rules(preflight, conflicts=conflicts, scope="bid", ledger_path=dbp)
    rec(18, "ledger_path 경로: mode=ro 자동 로드로 동일 결과",
        json.dumps(rules_ro, ensure_ascii=False, sort_keys=True)
        == json.dumps(rules, ensure_ascii=False, sort_keys=True))

    # ---- read-only: ledger mtime 불변 ----
    mtime_after = os.path.getmtime(dbp)
    rec(19, "read-only: 전 호출 후 ledger mtime 불변(mode=ro · write 0)",
        mtime_before == mtime_after)

    shutil.rmtree(tmp, ignore_errors=True)

    npass = sum(1 for _, _, v in results if v == "PASS")
    total = len(results)
    print("=" * 74)
    print("binggu_answer_rules — preflight→실행규칙 변환 selftest (temp ledger·운영 write 0)")
    print("=" * 74)
    for cid, desc, v in results:
        print("%s %2d %s" % ("[OK]" if v == "PASS" else "[X]", cid, desc))
    print("-" * 74)
    print("=== %d/%d ===" % (npass, total))
    print("ledger_mtime_unchanged=%s  raw_node_id_in_output=0  write=0"
          % (mtime_before == mtime_after))
    gate = "GO" if (npass == total and mtime_before == mtime_after) else "NO-GO"
    print("GATE=%s" % gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("binggu_answer_rules: --selftest 로 검증 실행")
