# -*- coding: utf-8 -*-
"""binggu_recall_trace.py — 회상 효용 trace (Phase 2: real handoff trace usefulness).

목적: 실제 세션에서 recall/preflight 가 회상한 노드가 실제로 쓰였는지(used) ·
      무시됐는지(ignored) · 틀려서 교정됐는지(corrected) 를 기록·집계해
      offline golden set(recall_golden.json) 보정 신호로 쓴다.

Phase 1(binggu_recall.py + recall_consistency_harness.py)은 합성 corpus 의 정적 정확도를
잰다. Phase 2 는 그 정확도가 "현실에서 쓸만한가"를 사후 라벨로 검증한다(둘은 보완 관계).

설계 제약(사장님 명시 · 헌법 정합):
  - 원문/PII 저장 금지 — query 는 sha256[:16] 만, 회상 노드는 node_id/category/rank/relevance 만.
    claim·sentence 원문은 store 에 한 바이트도 들어가지 않는다(selftest 가 바이트 검증).
  - opt-in 기록 — recall_config["trace_enabled"](기본 False) 또는 env BINGGU_RECALL_TRACE=1
    이 켜졌을 때만 record_trace 가 write. 안 켜면 no-op(status='disabled', write 0).
  - 사람 판정 게이트 — record_outcome(used/ignored/corrected)은 actor=human 만(헌법 §1 안전벨트).
  - 운영 ledger 불변 — trace 는 별도 store(<home>/recall_trace.sqlite). ledger.sqlite 미접촉.
  - 자동결정 0 — 집계(golden_drift_candidates)는 signal_only. golden set 을 자동수정하지 않고
    "사람이 재검토할 후보"만 표시한다(Phase 1 교훈: 사람 승인 SSOT).

차원 구분(중복 아님):
  - binggu_hit_stats : owner 직감 / ai 반박 '판단 적중률'(사람의 판단이 맞았나).
  - 이 모듈          : recall 시스템의 '회상 효용'(회상된 노드가 쓸모 있었나). 다른 축.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys

# 형제(binggu_p1_config·binggu_schema shim · list_pending 의 binggu_recall lazy ·
# openbinggu_staging_write_selftest fixture) bare-name 해소 — 원본이 자기 위치(scripts/)를
# 얹던 것을 패키지 위치에서 scripts/ 로 재계산해 동일 효과. HERE=scripts/ 유지 →
# _selftest 의 sys.path.insert(0, HERE) 도 동일 동작(정본 dep 는 shim 이 패키지로 재배선).
HERE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts")
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import binggu_p1_config as CFG  # recall_config["trace_enabled"] opt-in  # noqa: E402
from binggu_schema import apply_schema  # 정본 스키마(recall_traces/recall_outcomes 포함)  # noqa: E402
import binggu_platform as _plat  # binggu_home(BINGGU_HOME 존중 · 격리 폴백)  # noqa: E402

VALID_VERDICTS = ("used", "ignored", "corrected")

# reason_code 화이트리스트 — note 는 자유 원문 금지(PII 차단), verdict 별 enum 만 허용.
# golden_drift 분석에 그대로 쓰이는 구조화 신호(왜 무시/교정됐나 → fixture 보정 방향).
#   ignored : not_relevant(무관) · already_known(이미 앎) · low_signal(약한 신호)
#   corrected: stale(낡음/경로 바뀜) · wrong_context(맥락 어긋남) · superseded(더 최신 판단) ·
#              false_match(무관한데 회상됨 — golden 에서 제외 후보)
#   used     : note 불필요(빈 값만). 명시하면 거부(used 사유는 효용 측정에 불요).
REASON_CODES = {
    "used": (),
    "ignored": ("not_relevant", "already_known", "low_signal"),
    "corrected": ("stale", "wrong_context", "superseded", "false_match"),
}

# ── situation(v3, 다리c) — 회상 시점의 '의도 상황' 축(§9 Layer1) ──────────────────
# domain(cwd 프로젝트)과 직교하는 "무슨 상황에서 회상했나". reason_code(왜 무시/교정)와도 직교.
# PII 0 — 자유 원문 아닌 enum 라벨만 저장(recall_traces 원문 0 규칙 유지).
VALID_SITUATIONS = ("lookup", "decision", "change", "ambiguous")

# 키워드 신호 근사(자연어 이해 아님 · 1인 점진구현 §13 B9 — 실로그로 보정).
# 우선순위 = §9 혼합규칙: decision > change > lookup(결정+변경→결정 우선·조회+변경→변경 우선).
_SIT_DECISION = ("할까", "할까요", "될까", "되는건가", "되나", "맞나", "맞을까", "맞아?",
                 "검토", "방향", "의견", "어때", "어떨까", "좋을까", "괜찮을까", "응찰", "판단해")
_SIT_CHANGE = ("정리", "수정", "삭제", "등록", "발송", "발행", "적용", "추가", "고쳐", "고침",
               "만들", "생성", "바꿔", "변경", "지워", "제거", "올려", "배포", "커밋", "머지",
               "실행", "돌려", "설치", "착수", "해결", "해줘", "해라", "하자")
_SIT_LOOKUP = ("보여", "확인", "상태", "뭐야", "뭔가", "어디", "알려", "조회", "찾아", "검색",
               "보자", "궁금", "무엇")


def classify_situation(prompt):
    """회상 시점 prompt → 의도 상황 라벨(∈ VALID_SITUATIONS). 키워드 신호 근사.
    우선순위(§9 혼합규칙): decision > change > lookup > ambiguous. 미매치는 ambiguous."""
    p = str(prompt or "")
    if any(k in p for k in _SIT_DECISION):
        return "decision"
    if any(k in p for k in _SIT_CHANGE):
        return "change"
    if any(k in p for k in _SIT_LOOKUP):
        return "lookup"
    return "ambiguous"

# signal_only 라벨 — 집계 반환이 golden set 을 자동수정하는 입력으로 쓰이지 못하게 명시(헌법).
_SIGNAL_NOTE = ("이 수치는 표시 신호일 뿐 — golden set/fixture 자동수정 근거 아님. "
                "사람이 후보를 보고 recall_golden.json 을 직접 보정한다(자동결정 0).")

# golden_drift 후보 임계: 표본 N≥min 이고 (ignored+corrected)/total ≥ ratio 인 노드만 후보.
_DRIFT_N_MIN = 3
_DRIFT_RATIO = 0.5


# ---------------- store 경로 (운영 ledger 와 분리된 sibling) ----------------

def trace_store_path(home=None):
    """trace store 경로 = <binggu_home>/recall_trace.sqlite (ledger.sqlite sibling · 운영 불변)."""
    base = home or _plat.binggu_home()
    return os.path.join(base, "recall_trace.sqlite")


def _open_store(home=None):
    p = trace_store_path(home)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(p)
    # 정본 스키마 위임(recall_traces/recall_outcomes 는 상위집합에 동일 컬럼 + UNIQUE 포함).
    # idempotent · IF NOT EXISTS 이므로 기존 trace store 비파괴. 추가 테이블은 빈 채로 무해.
    apply_schema(con)
    return con


def _open_store_ro(home=None):
    """집계용 read-only 커넥션 — mode=ro URI 로만 열어 apply_schema/makedirs 미경유(write 0).

    _open_store 와 달리 폴더 생성·스키마 적용을 하지 않는다 → store(recall_trace.sqlite +
    -wal/-shm/-journal 사이드카)를 1바이트도 안 건드리는 순수 SELECT 경로. 호출측이 store 존재를
    os.path.exists 로 먼저 가드해야 한다(부재 시 OperationalError). store 는 항상 _open_store 가
    apply_schema 로 만든 것이라 recall_traces/recall_outcomes 테이블이 이미 있다(정본 상위집합)."""
    uri = "file:%s?mode=ro" % os.path.abspath(trace_store_path(home)).replace("\\", "/")
    return sqlite3.connect(uri, uri=True)


def review_snapshot_path(home=None):
    """review 번호→(trace_id,node_id) 매핑 스냅샷 경로. 메타만(원문 0). mark 의 N shift 방지."""
    base = home or _plat.binggu_home()
    return os.path.join(base, "recall_trace_review.json")


# ---------------- opt-in 판정 ----------------

def _flag_path(home=None):
    """opt-in 파일플래그 경로 — preflight_enabled 와 동일 패턴(파일 존재=ON). UX 통일."""
    base = home or _plat.binggu_home()
    return os.path.join(base, "recall_trace_enabled")


def trace_enabled(home=None):
    """기록 opt-in 여부 — 3원천 OR(어느 하나라도 ON). 기본 False.
      1) 파일플래그 <home>/recall_trace_enabled (binggu trace enable, preflight 패턴 통일)
      2) env BINGGU_RECALL_TRACE=1 (세션 한정 토글)
      3) config recall_config.trace_enabled (binggu_config.json · 영구)."""
    if os.path.exists(_flag_path(home)):
        return True
    if os.environ.get("BINGGU_RECALL_TRACE") == "1":
        return True
    try:
        return bool(CFG.recall_config(home).get("trace_enabled", False))
    except Exception:
        return False  # config 손상도 graceful — 기본 미기록(보수)


def set_trace_flag(enable, home=None):
    """파일플래그 ON/OFF(binggu trace enable/disable). 반환 {enabled, flag_path}."""
    p = _flag_path(home)
    if enable:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("1")
    elif os.path.exists(p):
        os.remove(p)
    return {"enabled": enable, "flag_path": p}


# ---------------- PII 차단 정규화 ----------------

def _sha16(text):
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:16]


def _scrub_node(n):
    """회상 노드 dict → 저장 가능한 메타만(원문 0). node_id/category(subtype)/rank/relevance.

    claim·sentence·evidence_excerpt 등 원문 키는 의도적으로 버린다(PII 차단). node_id 는
    빙구팩 정본 스키마(node:CONV:<h8>) — 식별자일 뿐 원문 아님."""
    return {
        "node_id": n.get("node_id"),
        "category": n.get("semantic_subtype"),
        "rank": n.get("rank_score"),
        "relevance": n.get("relevance"),
    }


def _trace_id(kind, query_sha, node_ids, ts):
    raw = "%s|%s|%s|%s" % (kind, query_sha, ",".join(node_ids), ts)
    return "rtr-" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


# ---------------- 기록: recall/preflight 결과 → trace (opt-in) ----------------

def record_trace(query, kind, recalled_nodes, ts, *, domain=None, situation=None,
                 risk_level=None, needs_question=None, session_id=None, home=None):
    """회상 결과 1건을 trace store 에 적재(opt-in). PII 0 — query=sha16, 노드=메타만.

    kind        : 'why_search' | 'preflight'.
    recalled_nodes: why_search.relevant_nodes / preflight.remember 형식 리스트.
    ts          : 호출자 제공 ISO 타임스탬프(결정성 · Date.now 미사용 정책).
    반환 {status, trace_id?, recorded, n_nodes} — status='disabled' 면 write 0(no-op)."""
    if not trace_enabled(home):
        return {"status": "disabled", "recorded": False, "trace_id": None,
                "reason": "trace opt-in OFF(recall_config.trace_enabled / BINGGU_RECALL_TRACE)"}
    qsha = _sha16(query)
    scrubbed = [_scrub_node(n) for n in (recalled_nodes or [])]
    node_ids = [s["node_id"] for s in scrubbed if s["node_id"]]
    tid = _trace_id(kind, qsha, node_ids, ts)
    con = _open_store(home)
    try:
        con.execute(
            "INSERT OR IGNORE INTO recall_traces"
            "(trace_id,kind,query_sha,domain,situation,session_id,recalled_json,top1_node_id,risk_level,needs_question,ts)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (tid, kind, qsha, domain,
             situation if situation in VALID_SITUATIONS else None,
             session_id,
             json.dumps(scrubbed, ensure_ascii=False, sort_keys=True),
             node_ids[0] if node_ids else None, risk_level,
             None if needs_question is None else int(bool(needs_question)), ts))
        con.commit()
    finally:
        con.close()
    return {"status": "ok", "recorded": True, "trace_id": tid, "n_nodes": len(node_ids),
            "node_ids": node_ids}  # node_ids: outcome staging(preflight hook)이 재사용(비파괴 확장)


def trace_from_why_search(query, result, ts, *, domain=None, situation=None, session_id=None, home=None):
    """why_search 반환 dict 를 그대로 받아 trace 기록(호출측 편의 · recall.py 비침습)."""
    return record_trace(query, "why_search", result.get("relevant_nodes", []), ts,
                        domain=domain, situation=situation, session_id=session_id, home=home)


def trace_from_preflight(query, result, ts, *, domain=None, situation=None, session_id=None, home=None):
    """preflight_context 반환 dict 를 받아 trace 기록(remember + 위험도/반문 메타)."""
    return record_trace(query, "preflight", result.get("remember", []), ts,
                        domain=domain, situation=situation, session_id=session_id,
                        risk_level=result.get("risk_level"),
                        needs_question=result.get("needs_question"), home=home)


# ---------------- 기록: 사후 효용 판정 (actor=human 게이트) ----------------

def record_outcome(trace_id, node_id, verdict, ctx, ts, *, reason_code=None, home=None):
    """회상 노드 1개에 대한 사후 효용 판정. verdict∈used/ignored/corrected.

    actor 게이트(헌법 v2 자율성 티어 · 2026-07-20 owner GO):
      - human          : used/ignored/corrected 전부 허용(기존 경로 · 강제력 불변).
      - ai_observation : T0 자동 관측 — verdict='used' 만 허용(auto_fact_observation 게이트).
        owner 신호('노드·엣지·증거 연결 시작 = 채택')의 자동 도장. ignored/corrected(부정·교정)는
        여전히 human 만 — 오판·인기편향 방지(negative-only 는 사람). actor 원문 보존(사람 위장 0).

    reason_code: note 대용 — 자유 원문 금지(PII 차단), REASON_CODES[verdict] 화이트리스트만.
      None 은 항상 허용. used 에 코드 명시는 거부(used 사유 불요).
    같은 (trace_id,node_id)는 UNIQUE — 재판정은 무시(이중계상 차단 · 첫 판정 보존)."""
    actor = (ctx or {}).get("actor", "").strip().lower()
    if actor == "human":
        pass  # 기존 경로(전 verdict)
    elif actor == "ai_observation" and verdict == "used" and CFG.auto_fact_observation_allowed():
        pass  # T0 자동 관측(used only · 헌법 v2 · auto_fact_observation)
    else:
        return {"recorded": False, "reason": "G4_no_auto"}
    if verdict not in VALID_VERDICTS:
        return {"recorded": False, "reason": "invalid_verdict"}
    if reason_code is not None and reason_code not in REASON_CODES.get(verdict, ()):
        return {"recorded": False, "reason": "invalid_reason_code"}  # 원문/오타 차단
    oid = "rto-" + hashlib.sha256(
        ("%s|%s|%s" % (trace_id, node_id, ts)).encode("utf-8", "replace")).hexdigest()[:16]
    con = _open_store(home)
    try:
        # trace 존재 확인(dangling outcome 방지 — 없는 trace 판정은 분모 오염)
        if not con.execute("SELECT 1 FROM recall_traces WHERE trace_id=?", (trace_id,)).fetchone():
            return {"recorded": False, "reason": "trace_not_found"}
        if con.execute("SELECT 1 FROM recall_outcomes WHERE trace_id=? AND node_id=?",
                       (trace_id, node_id)).fetchone():
            return {"recorded": False, "reason": "dup_outcome"}
        con.execute(
            "INSERT INTO recall_outcomes(outcome_id,trace_id,node_id,verdict,reason_code,actor,ts)"
            " VALUES(?,?,?,?,?,?,?)", (oid, trace_id, node_id, verdict, reason_code, actor, ts))
        con.commit()
    finally:
        con.close()
    return {"recorded": True, "outcome_id": oid, "reason_code": reason_code, "actor": actor}


# ---------------- T0 자동 관측: 그래프 편입 = 채택 (헌법 v2 · owner 신호) ----------------

def auto_observe_adoption(ts, *, home=None, ledger_path=None, dry_run=False):
    """T0 자동 효용 관측 — 미판정 회상 노드가 '회상 이후 생성된 새 엣지'의 source/target 로
    편입됐으면 used 자동 도장(actor=ai_observation). owner 신호: 노드·엣지·증거 연결 시작=채택.

    - auto_fact_observation OFF(헌법상 항상 True·방어) / ledger 부재 / trace store 부재 → no-op.
    - 자기증빙(evidence_supports) 엣지는 제외 — 노드 저장시 자동 생성되는 자기 엣지라 '채택' 아님.
    - used 만 자동. ignored/corrected(부정·교정)는 손대지 않음(사람 negative-only).
    - ledger 는 read-only(mode=ro) 조회만 — 운영 장부 write 0. recall_outcomes(sibling)만 append.
    - signal_only — recall_outcomes 집계는 golden/use_count/랭킹 자동수정에 진입 안 함(기존 라벨 유지).
    반환 {observed, stamped:[{trace_id,node_id}], reason?}."""
    if not CFG.auto_fact_observation_allowed():
        return {"observed": 0, "stamped": [], "reason": "auto_fact_observation_off"}
    if not ledger_path or not os.path.exists(ledger_path):
        return {"observed": 0, "stamped": [], "reason": "no_ledger"}
    if not os.path.exists(trace_store_path(home)):
        return {"observed": 0, "stamped": [], "reason": "no_trace_store"}
    con = _open_store(home)
    try:
        judged = set(con.execute("SELECT trace_id, node_id FROM recall_outcomes").fetchall())
        rows = con.execute("SELECT trace_id, recalled_json, ts FROM recall_traces").fetchall()
    finally:
        con.close()
    lcon = sqlite3.connect(
        "file:%s?mode=ro" % os.path.abspath(ledger_path).replace("\\", "/"), uri=True)
    stamped = []
    try:
        for trace_id, rj, trace_ts in rows:
            if not trace_ts:
                continue  # 회상 시점 모르면 이후 편입 판정 불가(보수)
            try:
                nodes = json.loads(rj) if rj else []
            except Exception:
                nodes = []
            for n in nodes:
                nid = n.get("node_id")
                if not nid or (trace_id, nid) in judged:
                    continue
                # 회상 이후 생성된 '관계' 엣지(자기증빙 제외)에 이 노드가 편입됐나 — read-only
                row = lcon.execute(
                    "SELECT 1 FROM edges WHERE (source=? OR target=?)"
                    " AND relation != 'evidence_supports'"
                    " AND created_at IS NOT NULL AND created_at > ? LIMIT 1",
                    (nid, nid, trace_ts)).fetchone()
                if not row:
                    continue
                if dry_run:  # 미리보기 — 실제 도장 0(owner 확인용)
                    stamped.append({"trace_id": trace_id, "node_id": nid})
                    judged.add((trace_id, nid))
                    continue
                res = record_outcome(trace_id, nid, "used", {"actor": "ai_observation"}, ts, home=home)
                if res.get("recorded"):
                    stamped.append({"trace_id": trace_id, "node_id": nid})
                    judged.add((trace_id, nid))
    finally:
        lcon.close()
    return {"observed": len(stamped), "stamped": stamped, "dry_run": dry_run}


# ---------------- 미스 후보 자동선별 (auto_observe 의 부정 거울 · read-only · 값 확정 0) ----------------

def _parse_iso_utc(ts):
    """ISO 'YYYY-MM-DDTHH:MM:SSZ' → aware datetime(UTC). 파싱 실패 → None(graceful)."""
    if not ts:
        return None
    import datetime as _dt
    try:
        return _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def list_miss_candidates(now_ts, *, home=None, ledger_path=None,
                         min_age_hours=24, top_n=10):
    """미스(헛다리) 후보 자동선별 — auto_observe_adoption 의 부정 거울(순수 SELECT · 값 확정 0).

    회상됐으나 (a) now_ts 기준 min_age_hours 이상 경과 (b) 회상 이후 '관계 엣지' 미편입
    = 오래도록 그래프에 안 쓰인 노드 → owner 가 '미스' 도장할 후보를 AI 가 정렬·상위 top_n 제시.

    헌법 정합(owner 논증 · 2026-07-21): 후보 제시(조회+신호) ≠ 값 확정(판정). ignored 도장은
    owner actor=human 만(record_outcome) — 본 함수는 recall_outcomes 를 한 바이트도 write 하지
    않는다. auto_observe_adoption 이 '편입=used' 를 자동 도장하므로 편입 노드는 여기서 제외
    (긍정/부정 거울 대칭 · used 와 겹치지 않음). ledger 는 read-only(mode=ro) 조회만.

    now_ts : 호출자 제공 ISO 타임스탬프(결정성 · Date.now 미사용 정책).
    정렬: age desc(오래된 순) → rank asc(낮은 관련도 우선) → trace_id → node_id(결정적).
    반환 [{trace_id, node_id, category, rank, age_hours, claim}] (top_n 컷 · idx 미부여).
    store/now_ts 파싱 실패 → [](graceful)."""
    if not os.path.exists(trace_store_path(home)):
        return []
    now_dt = _parse_iso_utc(now_ts)
    if now_dt is None:
        return []
    con = _open_store_ro(home)
    try:
        judged = set(con.execute("SELECT trace_id, node_id FROM recall_outcomes").fetchall())
        rows = con.execute("SELECT trace_id, recalled_json, ts FROM recall_traces").fetchall()
    finally:
        con.close()

    lcon = None
    by_id = {}
    if ledger_path and os.path.exists(ledger_path):
        lcon = sqlite3.connect(
            "file:%s?mode=ro" % os.path.abspath(ledger_path).replace("\\", "/"), uri=True)
        try:
            import binggu_recall as RC
            by_id = RC._load_graph(ledger_path).get("by_id", {})
        except Exception:
            by_id = {}

    cands = []
    try:
        for trace_id, rj, trace_ts in rows:
            tdt = _parse_iso_utc(trace_ts)
            if tdt is None:
                continue  # 회상 시점 모르면 경과·편입 판정 불가(보수 제외)
            age_h = (now_dt - tdt).total_seconds() / 3600.0
            if age_h < min_age_hours:
                continue  # 아직 신선 — 막 회상된 건 미스라 단정 못 함(판정 유예)
            try:
                nodes = json.loads(rj) if rj else []
            except Exception:
                nodes = []
            for n in nodes:
                nid = n.get("node_id")
                if not nid or (trace_id, nid) in judged:
                    continue
                # 회상 이후 관계 엣지 편입? 편입됐으면 auto_observe 가 used 로 잡음 → 미스 후보 제외
                if lcon is not None:
                    row = lcon.execute(
                        "SELECT 1 FROM edges WHERE (source=? OR target=?)"
                        " AND relation != 'evidence_supports'"
                        " AND created_at IS NOT NULL AND created_at > ? LIMIT 1",
                        (nid, nid, trace_ts)).fetchone()
                    if row:
                        continue  # 편입됨(used 신호) → 부정 거울에서 제외
                node = by_id.get(nid)
                claim = (node["sentence"][:100] if node else None)
                cands.append({"trace_id": trace_id, "node_id": nid,
                              "category": n.get("category"), "rank": n.get("rank"),
                              "age_hours": round(age_h, 1), "claim": claim})
    finally:
        if lcon is not None:
            lcon.close()

    cands.sort(key=lambda c: (-c["age_hours"],
                              c["rank"] if c["rank"] is not None else 1.0,
                              c["trace_id"], c["node_id"]))
    return cands[:top_n]


# ---------------- T0 자동관측 opt-in 플래그 (기본 OFF · 첫 실행 안전) ----------------

def _auto_observe_flag_path(home=None):
    base = home or _plat.binggu_home()
    return os.path.join(base, "auto_observe_enabled")


def auto_observe_enabled(home=None):
    """T0 자동관측 hook 활성 여부 — 파일플래그(기본 OFF). dry-run 미리보기 후 owner 가 켠다.
    헌법 T0(auto_fact_observation)는 허용이나, 첫 대량 도장 방지를 위한 운영 opt-in(안전 기본값)."""
    return os.path.exists(_auto_observe_flag_path(home))


def set_auto_observe_flag(enable, home=None):
    """T0 자동관측 hook 플래그 ON/OFF(binggu observe --enable/--disable). 반환 {enabled, flag_path}."""
    p = _auto_observe_flag_path(home)
    if enable:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("1")
    elif os.path.exists(p):
        os.remove(p)
    return {"enabled": enable, "flag_path": p}


# ---------------- review / mark (수동 outcome 명령 — binggu trace) ----------------

def list_pending(home=None, ledger_path=None, session_id=None):
    """미판정 (trace,node) 펼침 목록 + ledger claim join(표시용 · store 원문 0 유지).

    claim 은 ledger(read-only)에서 node_id 로 조회한 표시 텍스트일 뿐 — trace store 엔
    여전히 미저장(PII 0). ledger 부재/노드 부재면 claim=None(graceful).
    정렬: ts asc → trace_id → node_id (결정적 · 새 trace 는 뒤에 붙어 앞 순번 불변).
    session_id(v4): 지정 시 그 세션 회상만(마무리 preview §2 '이번 세션 회상' 필터) · None=전체 누적.
    반환 [{idx, trace_id, node_id, category, rank, kind, claim}] (idx=1부터)."""
    if not os.path.exists(trace_store_path(home)):
        return []
    con = _open_store(home)
    try:
        judged = set(con.execute("SELECT trace_id, node_id FROM recall_outcomes").fetchall())
        if session_id:
            rows = con.execute(
                "SELECT trace_id, kind, recalled_json, ts FROM recall_traces"
                " WHERE session_id=? ORDER BY ts, trace_id", (session_id,)).fetchall()
        else:
            rows = con.execute(
                "SELECT trace_id, kind, recalled_json, ts FROM recall_traces ORDER BY ts, trace_id").fetchall()
    finally:
        con.close()

    # ledger claim 조회(read-only · 표시용) — binggu_recall._load_graph 재사용.
    by_id = {}
    if ledger_path and os.path.exists(ledger_path):
        try:
            import binggu_recall as RC
            by_id = RC._load_graph(ledger_path).get("by_id", {})
        except Exception:
            by_id = {}

    pending = []
    for trace_id, kind, rj, _ts in rows:
        try:
            nodes = json.loads(rj)
        except Exception:
            nodes = []
        for n in nodes:
            nid = n.get("node_id")
            if not nid or (trace_id, nid) in judged:
                continue
            node = by_id.get(nid)
            claim = (node["sentence"][:100] if node else None)
            pending.append({"trace_id": trace_id, "node_id": nid,
                            "category": n.get("category"), "rank": n.get("rank"),
                            "kind": kind, "claim": claim})
    # 결정적 정렬 후 idx 부여
    for i, p in enumerate(pending, 1):
        p["idx"] = i
    return pending


def save_review_snapshot(pending, home=None):
    """review 번호→(trace_id,node_id) 매핑만 저장(원문 0). mark 가 N 을 안전 역참조."""
    snap = [{"idx": p["idx"], "trace_id": p["trace_id"], "node_id": p["node_id"]}
            for p in pending]
    p = review_snapshot_path(home)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    return p


def _load_review_snapshot(home=None):
    p = review_snapshot_path(home)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def mark_by_index(n, verdict, ctx, ts, *, reason_code=None, home=None):
    """review 스냅샷의 N 번 항목을 판정(binggu trace mark N verdict). N shift 방지.

    스냅샷 부재 → need_review(먼저 binggu trace review). N 범위 밖 → bad_index."""
    snap = _load_review_snapshot(home)
    if not snap:
        return {"recorded": False, "reason": "need_review"}
    hit = next((s for s in snap if s.get("idx") == n), None)
    if not hit:
        return {"recorded": False, "reason": "bad_index"}
    return record_outcome(hit["trace_id"], hit["node_id"], verdict, ctx, ts,
                          reason_code=reason_code, home=home)


# ---------------- 집계 (signal_only — golden set 자동수정 0) ----------------

def aggregate(home=None):
    """node별/전체 회상 효용 집계 + golden_drift 후보. 전부 signal_only(자동수정 0).

    반환 {overall, per_node, golden_drift_candidates, signal_only, note}.
      overall: 전체 trace/outcome 수, used/ignored/corrected 합, usefulness_rate(used/total).
      per_node[node_id]: {used,ignored,corrected,total,usefulness_rate}.
      golden_drift_candidates: 표본 N≥3 · (ignored+corrected)/total≥0.5 인 node — 재검토 후보.
    빈 store → 전부 0(에러 0)."""
    if not os.path.exists(trace_store_path(home)):
        return {"overall": {"traces": 0, "outcomes": 0, "used": 0, "ignored": 0,
                            "corrected": 0, "usefulness_rate": None},
                "per_node": {}, "golden_drift_candidates": [],
                "signal_only": True, "note": _SIGNAL_NOTE}
    con = _open_store_ro(home)  # read-only 집계 — apply_schema/makedirs 미경유(store write 0)
    try:
        n_traces = con.execute("SELECT COUNT(*) FROM recall_traces").fetchone()[0]
        rows = con.execute("SELECT node_id, verdict, reason_code FROM recall_outcomes").fetchall()
    finally:
        con.close()

    per = {}
    tot = {"used": 0, "ignored": 0, "corrected": 0}
    for node_id, verdict, reason in rows:
        if verdict not in VALID_VERDICTS:
            continue
        tot[verdict] += 1
        d = per.setdefault(node_id, {"used": 0, "ignored": 0, "corrected": 0, "reasons": {}})
        d[verdict] += 1
        if reason:
            d["reasons"][reason] = d["reasons"].get(reason, 0) + 1

    per_node = {}
    drift = []
    for node_id, d in per.items():
        total = d["used"] + d["ignored"] + d["corrected"]
        rate = round(d["used"] / total, 4) if total else None
        per_node[node_id] = {"used": d["used"], "ignored": d["ignored"],
                             "corrected": d["corrected"], "reasons": d["reasons"],
                             "total": total, "usefulness_rate": rate}
        bad = d["ignored"] + d["corrected"]
        if total >= _DRIFT_N_MIN and total and (bad / total) >= _DRIFT_RATIO:
            drift.append({"node_id": node_id, "total": total,
                          "ignored": d["ignored"], "corrected": d["corrected"],
                          "reasons": d["reasons"],  # 보정 방향(false_match→제외·superseded→갱신)
                          "bad_ratio": round(bad / total, 4)})
    drift.sort(key=lambda x: (-x["bad_ratio"], -x["total"], x["node_id"]))

    n_out = tot["used"] + tot["ignored"] + tot["corrected"]
    overall = {"traces": n_traces, "outcomes": n_out, **tot,
               "usefulness_rate": round(tot["used"] / n_out, 4) if n_out else None}
    return {"overall": overall, "per_node": per_node,
            "golden_drift_candidates": drift, "signal_only": True, "note": _SIGNAL_NOTE}


# ---------------- selftest (temp home · 운영 미접촉 · write 0) ----------------

def _selftest():
    import tempfile
    import shutil

    sys.path.insert(0, HERE)
    from openbinggu_staging_write_selftest import OPERATING_PATHS

    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_rtrace_")
    TS = "2026-06-27T00:00:00Z"
    try:
        home = os.path.join(tmp, ".binggupack")
        os.makedirs(home, exist_ok=True)
        # 운영 ledger 미접촉 sentinel
        ledger = os.path.join(home, "ledger.sqlite")
        with open(ledger, "wb") as f:
            f.write(b"LEDGER-SENTINEL")
        ledger_mt0 = os.path.getmtime(ledger)

        # 회상 결과(원문 claim 포함 — scrub 으로 떨궈져야)
        SECRET_CLAIM = "검증없이배포비밀원문문장SHOULDNOTPERSIST"
        recalled = [
            {"node_id": "node:CONV:aa01", "claim": SECRET_CLAIM,
             "semantic_subtype": "버그패턴", "rank_score": 0.9, "relevance": 0.8},
            {"node_id": "node:CONV:bb02", "claim": "또다른원문",
             "semantic_subtype": "교훈", "rank_score": 0.7, "relevance": 0.6},
        ]

        # ── opt-in OFF(기본) → no-op, store 파일조차 안 생김 ──
        CFG.save_user_config({"recall_config": {"trace_enabled": False}}, home=home)
        os.environ.pop("BINGGU_RECALL_TRACE", None)
        r_off = record_trace("배포 " + SECRET_CLAIM, "preflight", recalled, TS, home=home)
        ck(r_off["status"] == "disabled" and r_off["recorded"] is False,
           "opt-in OFF → record_trace no-op(status=disabled)")
        ck(not os.path.exists(trace_store_path(home)),
           "opt-in OFF → store 파일 미생성(write 0)")

        # ── opt-in ON → 기록 ──
        CFG.save_user_config({"recall_config": {"trace_enabled": True}}, home=home)
        QUERY = "검증없이 바로 배포 " + SECRET_CLAIM
        res_pf = {"remember": recalled, "risk_level": "높음", "needs_question": True}
        r_on = trace_from_preflight(QUERY, res_pf, TS, domain="example-project", home=home)
        ck(r_on["status"] == "ok" and r_on["recorded"] and r_on["n_nodes"] == 2,
           "opt-in ON → trace 기록(n_nodes=2)")
        tid = r_on["trace_id"]

        # ── PII 0: store 바이트에 query/claim 원문이 한 글자도 없어야 ──
        with open(trace_store_path(home), "rb") as f:
            blob = f.read()
        ck(SECRET_CLAIM.encode("utf-8") not in blob,
           "PII 0: 회상 노드 claim 원문이 store 에 미저장(scrub)")
        ck("배포".encode("utf-8") not in blob,
           "PII 0: query 원문이 store 에 미저장(sha16 만)")
        # 저장된 건 node_id/category/rank/relevance 만
        con = _open_store(home)
        rj = con.execute("SELECT recalled_json FROM recall_traces WHERE trace_id=?", (tid,)).fetchone()[0]
        con.close()
        parsed = json.loads(rj)
        ck(all(set(p.keys()) == {"node_id", "category", "rank", "relevance"} for p in parsed)
           and parsed[0]["node_id"] == "node:CONV:aa01" and parsed[0]["category"] == "버그패턴",
           "저장 메타 = node_id/category/rank/relevance 만(원문 키 0)")

        # ── 멱등: 같은 입력 재기록 → trace_id 동일·중복 INSERT 0 ──
        r_dup = trace_from_preflight(QUERY, res_pf, TS, domain="example-project", home=home)
        con = _open_store(home)
        n_tr = con.execute("SELECT COUNT(*) FROM recall_traces").fetchone()[0]
        con.close()
        ck(r_dup["trace_id"] == tid and n_tr == 1, "멱등: 동일 입력 → trace 1건(INSERT OR IGNORE)")

        # ── outcome actor=ai → G4_no_auto ──
        o_ai = record_outcome(tid, "node:CONV:aa01", "used", {"actor": "ai"}, TS, home=home)
        ck(not o_ai["recorded"] and o_ai["reason"] == "G4_no_auto",
           "outcome actor=ai → G4_no_auto(사람 판정 게이트)")

        # ── invalid verdict ──
        o_bad = record_outcome(tid, "node:CONV:aa01", "maybe", {"actor": "human"}, TS, home=home)
        ck(not o_bad["recorded"] and o_bad["reason"] == "invalid_verdict",
           "invalid verdict → 거부")

        # ── dangling trace_id → 거부(분모 오염 방지) ──
        o_dang = record_outcome("rtr-deadbeefdeadbeef", "node:CONV:aa01", "used",
                                {"actor": "human"}, TS, home=home)
        ck(not o_dang["recorded"] and o_dang["reason"] == "trace_not_found",
           "dangling trace_id → trace_not_found")

        # ── 정상 outcome: aa01 used, bb02 ignored ──
        ck(record_outcome(tid, "node:CONV:aa01", "used", {"actor": "human"}, TS, home=home)["recorded"],
           "outcome used 기록(actor=human)")
        ck(record_outcome(tid, "node:CONV:bb02", "ignored", {"actor": "human"}, TS, home=home)["recorded"],
           "outcome ignored 기록")
        # 이중계상: 같은 (trace,node) 재판정 → dup
        o_d2 = record_outcome(tid, "node:CONV:aa01", "corrected", {"actor": "human"}, TS, home=home)
        ck(not o_d2["recorded"] and o_d2["reason"] == "dup_outcome",
           "이중계상 가드: 같은 (trace,node) 재판정 → dup_outcome(첫 판정 보존)")

        # ── 집계: used 1 / ignored 1 → usefulness 0.5 ──
        agg = aggregate(home=home)
        ck(agg["overall"]["used"] == 1 and agg["overall"]["ignored"] == 1
           and agg["overall"]["usefulness_rate"] == 0.5,
           "집계: used 1·ignored 1 → usefulness_rate 0.5")
        ck(agg["per_node"]["node:CONV:aa01"]["usefulness_rate"] == 1.0
           and agg["per_node"]["node:CONV:bb02"]["usefulness_rate"] == 0.0,
           "per_node usefulness(aa01=1.0 · bb02=0.0)")
        ck(agg["signal_only"] is True, "집계 signal_only=True(golden 자동수정 0)")

        # ── golden_drift 후보: bb02 를 ignored/corrected 3회 채워 후보화 ──
        #   (서로 다른 trace 가 필요 — 같은 trace 는 node UNIQUE). 추가 trace 2건 생성.
        for i, verdict in enumerate(["ignored", "corrected"]):
            q = "다른 작업 %d" % i
            rr = record_trace(q, "why_search",
                              [{"node_id": "node:CONV:bb02", "semantic_subtype": "교훈",
                                "rank_score": 0.5, "relevance": 0.4}], TS, home=home)
            record_outcome(rr["trace_id"], "node:CONV:bb02", verdict, {"actor": "human"}, TS, home=home)
        agg2 = aggregate(home=home)
        drift_ids = [d["node_id"] for d in agg2["golden_drift_candidates"]]
        bb = agg2["per_node"]["node:CONV:bb02"]
        ck(bb["total"] == 3 and bb["ignored"] == 2 and bb["corrected"] == 1
           and "node:CONV:bb02" in drift_ids,
           "golden_drift: bb02(ignored2+corrected1/3=1.0≥0.5,N≥3) → 재검토 후보")
        ck("node:CONV:aa01" not in drift_ids,
           "golden_drift: aa01(used 1·N<3) → 후보 아님(표본게이트)")

        # ── readonly 집계: store mtime 불변(mode=ro · apply_schema/makedirs 미경유 · write 0) ──
        sp_ro = trace_store_path(home)
        mt_ro = os.path.getmtime(sp_ro)
        aggregate(home=home)
        ck(os.path.getmtime(sp_ro) == mt_ro,
           "readonly 집계: aggregate 후 store mtime 불변(mode=ro · write 0)")

        # ── env opt-in: config OFF 라도 BINGGU_RECALL_TRACE=1 이면 기록 ──
        home2 = os.path.join(tmp, ".binggupack2")
        os.makedirs(home2, exist_ok=True)
        CFG.save_user_config({"recall_config": {"trace_enabled": False}}, home=home2)
        os.environ["BINGGU_RECALL_TRACE"] = "1"
        r_env = record_trace("env 작업", "why_search", recalled, TS, home=home2)
        os.environ.pop("BINGGU_RECALL_TRACE", None)
        ck(r_env["recorded"], "env BINGGU_RECALL_TRACE=1 → config OFF 라도 세션 기록")

        # ── 빈 store graceful ──
        home3 = os.path.join(tmp, ".binggupack3")
        agg_empty = aggregate(home=home3)
        ck(agg_empty["overall"]["traces"] == 0 and agg_empty["golden_drift_candidates"] == []
           and agg_empty["overall"]["usefulness_rate"] is None,
           "빈 store → 집계 0·후보 0(에러 0)")

        # ── 파일플래그 opt-in(preflight 패턴 통일) ──
        home4 = os.path.join(tmp, ".binggupack4")
        os.makedirs(home4, exist_ok=True)
        CFG.save_user_config({"recall_config": {"trace_enabled": False}}, home=home4)
        ck(trace_enabled(home4) is False, "파일플래그/env/config 전부 OFF → trace_enabled False")
        set_trace_flag(True, home=home4)
        ck(trace_enabled(home4) is True and os.path.exists(_flag_path(home4)),
           "set_trace_flag(True) → 파일 생성·trace_enabled True(config OFF여도)")
        set_trace_flag(False, home=home4)
        ck(trace_enabled(home4) is False and not os.path.exists(_flag_path(home4)),
           "set_trace_flag(False) → 파일 삭제·OFF 복귀")

        # ── situation(v3, 다리c): 분류기 4분기 + 기록/화이트리스트 ──
        ck(classify_situation("이거 삭제해줘") == "change", "classify_situation → change(행동동사)")
        ck(classify_situation("이렇게 하는 게 맞나?") == "decision", "classify_situation → decision(결정)")
        ck(classify_situation("현재 상태 보여줘") == "lookup", "classify_situation → lookup(조회)")
        ck(classify_situation("음 그렇군") == "ambiguous", "classify_situation → ambiguous(미매치)")
        ck(classify_situation("이거 삭제하는 게 맞나?") == "decision",
           "classify_situation 결정+변경 혼합 → decision 우선(§9)")
        home_sit = os.path.join(tmp, ".binggupack_sit")  # 고유 home(기존 home5 재사용 금지 — 격리)
        os.makedirs(home_sit, exist_ok=True)
        set_trace_flag(True, home=home_sit)
        r_sit = trace_from_preflight("배포 결정 질의", res_pf, TS,
                                     domain="proj", situation="decision", home=home_sit)
        con = _open_store(home_sit)
        s_row = con.execute("SELECT situation, domain FROM recall_traces WHERE trace_id=?",
                            (r_sit["trace_id"],)).fetchone()
        con.close()
        ck(s_row == ("decision", "proj"), "trace_from_preflight → situation+domain 함께 기록(다리c)")
        # ── session_id(v4): 이번 세션 회상 필터(마무리 preview §2 도움 판정 대상) ──
        home_sid = os.path.join(tmp, ".binggupack_sid")  # 고유 home(격리 · home_sit 재사용 금지)
        os.makedirs(home_sid, exist_ok=True)
        set_trace_flag(True, home=home_sid)
        record_trace("세션A질의1", "preflight", [{"node_id": "node:SA1"}], "2026-07-25T01:00:00Z",
                     session_id="SID_A", home=home_sid)
        record_trace("세션A질의2", "preflight", [{"node_id": "node:SA2"}], "2026-07-25T02:00:00Z",
                     session_id="SID_A", home=home_sid)
        record_trace("세션B질의", "preflight", [{"node_id": "node:SB1"}], "2026-07-25T03:00:00Z",
                     session_id="SID_B", home=home_sid)
        pend_a = list_pending(home=home_sid, session_id="SID_A")
        pend_all = list_pending(home=home_sid)
        ck(len(pend_a) == 2 and len(pend_all) == 3,
           "session_id(v4) → list_pending 이번 세션 필터(SID_A 2 / 전체 누적 3)")
        r_bad_sit = trace_from_preflight("다른 질의 xyz", res_pf, "2026-06-27T09:00:00Z",
                                         situation="INVALID_SIT", home=home_sit)
        con = _open_store(home_sit)
        sb = con.execute("SELECT situation FROM recall_traces WHERE trace_id=?",
                        (r_bad_sit["trace_id"],)).fetchone()[0]
        con.close()
        ck(sb is None, "situation 화이트리스트 밖 → NULL(오타/원문 차단)")

        # ── reason_code 화이트리스트(PII 차단) ──
        set_trace_flag(True, home=home4)
        rc4 = record_trace("리뷰 작업", "why_search", recalled, TS, home=home4)
        tid4 = rc4["trace_id"]
        ck(record_outcome(tid4, "node:CONV:aa01", "ignored", {"actor": "human"}, TS,
                          reason_code="not_relevant", home=home4)["recorded"],
           "reason_code 화이트리스트(ignored:not_relevant) → 기록")
        ck(record_outcome(tid4, "node:CONV:bb02", "corrected", {"actor": "human"}, TS,
                          reason_code="zzz_freeform", home=home4)["reason"] == "invalid_reason_code",
           "reason_code 화이트리스트 외(원문/오타) → invalid_reason_code(PII 차단)")
        ck(record_outcome(tid4, "node:CONV:bb02", "used", {"actor": "human"}, TS,
                          reason_code="not_relevant", home=home4)["reason"] == "invalid_reason_code",
           "used 에 reason_code 명시 → 거부(used 사유 불요)")

        # ── list_pending + ledger claim join(표시용·store 원문 0) ──
        # 별 home5 + ledger(node sentence) — review/mark/N-shift 안전 검증.
        home5 = os.path.join(tmp, ".binggupack5")
        os.makedirs(home5, exist_ok=True)
        set_trace_flag(True, home=home5)
        led5 = os.path.join(home5, "ledger.sqlite")
        lcon = sqlite3.connect(led5)
        apply_schema(lcon)  # 정본 스키마(claim-join 용 ledger fixture · 아래 INSERT 컬럼 명시)
        lcon.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
                     "created_at,semantic_subtype,use_count) VALUES"
                     "('node:CONV:p1','judgment','배포 전 live endpoint 확인',0,'active','h',?, '교훈',2)", (TS,))
        lcon.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
                     "created_at,semantic_subtype,use_count) VALUES"
                     "('node:CONV:p2','judgment','오래된 인증서 경로 확인',0,'active','h',?, '버그패턴',3)", (TS,))
        lcon.commit()
        lcon.close()
        rev_recalled = [
            {"node_id": "node:CONV:p1", "semantic_subtype": "교훈", "rank_score": 0.82, "relevance": 0.7},
            {"node_id": "node:CONV:p2", "semantic_subtype": "버그패턴", "rank_score": 0.74, "relevance": 0.6},
        ]
        record_trace("배포 점검", "preflight", rev_recalled, TS, home=home5)
        pend = list_pending(home=home5, ledger_path=led5)
        ck(len(pend) == 2 and pend[0]["idx"] == 1 and pend[0]["claim"] == "배포 전 live endpoint 확인"
           and pend[0]["category"] == "교훈",
           "list_pending → 미판정 2건·ledger claim join(표시용)·idx 부여")
        # claim 은 ledger 표시용일 뿐 — trace store 바이트엔 원문 0
        with open(trace_store_path(home5), "rb") as f:
            blob5 = f.read()
        ck("배포 전 live endpoint 확인".encode("utf-8") not in blob5,
           "claim 은 ledger join 표시용 — trace store 엔 미저장(PII 0 유지)")

        # ── review 스냅샷 + mark_by_index N-shift 안전 ──
        ck(mark_by_index(1, "used", {"actor": "human"}, TS, home=home5)["reason"] == "need_review",
           "스냅샷 없이 mark → need_review")
        save_review_snapshot(pend, home=home5)
        ck(mark_by_index(1, "used", {"actor": "human"}, TS, home=home5)["recorded"],
           "review 후 mark 1 used → 기록")
        # mark 1 로 p1 판정됨 → 미판정 재계산하면 p2 가 1번이 되지만, 스냅샷 기준 mark 2 는 여전히 p2
        m2 = mark_by_index(2, "corrected", {"actor": "human"}, TS, reason_code="stale", home=home5)
        ck(m2["recorded"] and m2["reason_code"] == "stale",
           "mark 2 corrected(--note stale) → p2 판정(N-shift 안전: 스냅샷 역참조)")
        ck(mark_by_index(9, "used", {"actor": "human"}, TS, home=home5)["reason"] == "bad_index",
           "범위 밖 mark → bad_index")
        # 집계 reasons 분포
        agg5 = aggregate(home=home5)
        ck(agg5["per_node"]["node:CONV:p2"]["reasons"].get("stale") == 1,
           "aggregate per_node reasons 분포(p2: stale 1) — golden 보정 방향")

        # ── T0 자동 관측: 그래프 편입 = 채택(헌법 v2 · owner 신호) ──
        home6 = os.path.join(tmp, ".binggupack6")
        os.makedirs(home6, exist_ok=True)
        set_trace_flag(True, home=home6)
        led6 = os.path.join(home6, "ledger.sqlite")
        lcon6 = sqlite3.connect(led6)
        apply_schema(lcon6)
        for nid in ("node:CONV:g1", "node:CONV:g2"):
            lcon6.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
                          "created_at,semantic_subtype,use_count) VALUES"
                          "(?,'judgment','문장',0,'active','h',?, '교훈',0)", (nid, TS))
        lcon6.commit()
        lcon6.close()
        adopt_recalled = [
            {"node_id": "node:CONV:g1", "semantic_subtype": "교훈", "rank_score": 0.8, "relevance": 0.7},
            {"node_id": "node:CONV:g2", "semantic_subtype": "교훈", "rank_score": 0.7, "relevance": 0.6},
        ]
        rt6 = record_trace("그래프 편입 점검", "preflight", adopt_recalled, TS, home=home6)
        tid6 = rt6["trace_id"]

        # ai_observation 은 used 만 자동 — ignored/corrected 거부(부정 판정은 사람 negative-only)
        ck(record_outcome(tid6, "node:CONV:g1", "ignored", {"actor": "ai_observation"}, TS,
                          home=home6)["reason"] == "G4_no_auto",
           "ai_observation ignored → 거부(부정 판정은 사람만)")
        ck(record_outcome(tid6, "node:CONV:g1", "corrected", {"actor": "ai_observation"}, TS,
                          home=home6)["reason"] == "G4_no_auto", "ai_observation corrected → 거부")

        # 편입 전: 관계 엣지 없음 → 자동 도장 0
        TS_LATER = "2026-06-27T02:00:00Z"
        ck(auto_observe_adoption(TS_LATER, home=home6, ledger_path=led6)["observed"] == 0,
           "편입 전(관계 엣지 없음) → 자동 도장 0")

        lcon6 = sqlite3.connect(led6)
        # g1: 회상 이후 생성된 관계 엣지(supports_judgment)의 target 로 편입 → 채택 신호
        lcon6.execute("INSERT INTO edges(edge_id,relation,source,target,candidate,state,created_at)"
                      " VALUES('e6a','supports_judgment','node:CONV:new1','node:CONV:g1',0,'active',?)",
                      (TS_LATER,))
        # g2: 회상 이전 엣지에만(created_at < trace_ts) → 신호 아님
        lcon6.execute("INSERT INTO edges(edge_id,relation,source,target,candidate,state,created_at)"
                      " VALUES('e6b','supports_judgment','node:CONV:old','node:CONV:g2',0,'active',"
                      "'2026-06-26T00:00:00Z')")
        # g2: 자기증빙(evidence_supports)은 회상 이후라도 신호 아님(자기 엣지 제외)
        lcon6.execute("INSERT INTO edges(edge_id,relation,source,target,candidate,state,created_at)"
                      " VALUES('e6c','evidence_supports','EVC-CONV-g2','node:CONV:g2',0,'active',?)",
                      (TS_LATER,))
        lcon6.commit()
        led6_mt = os.path.getmtime(led6)
        lcon6.close()

        adopt = auto_observe_adoption(TS_LATER, home=home6, ledger_path=led6)
        ck(adopt["observed"] == 1 and adopt["stamped"][0]["node_id"] == "node:CONV:g1",
           "자동 관측: g1(회상 후 관계 엣지 편입) → used 1건 자동 도장")
        agg6 = aggregate(home=home6)
        ck(agg6["per_node"].get("node:CONV:g1", {}).get("used") == 1
           and "node:CONV:g2" not in agg6["per_node"],
           "g1 만 used(회상 이전 엣지·자기증빙은 신호 아님)")
        con6 = _open_store(home6)
        act6 = con6.execute("SELECT actor FROM recall_outcomes WHERE trace_id=? AND node_id=?",
                            (tid6, "node:CONV:g1")).fetchone()[0]
        con6.close()
        ck(act6 == "ai_observation", "자동 도장 actor=ai_observation(사람 위장 0)")
        ck(os.path.getmtime(led6) == led6_mt,
           "auto_observe_adoption: ledger read-only(mode=ro · mtime 불변)")
        ck(auto_observe_adoption(TS_LATER, home=home6, ledger_path=led6)["observed"] == 0,
           "자동 관측 멱등: 이미 도장된 것 재도장 0(dup 차단)")

        # ── dry_run 미리보기(도장 0) + opt-in 플래그(기본 OFF · 첫 실행 안전) ──
        home7 = os.path.join(tmp, ".binggupack7")
        os.makedirs(home7, exist_ok=True)
        set_trace_flag(True, home=home7)
        ck(auto_observe_enabled(home7) is False, "auto_observe 플래그 기본 OFF(첫 실행 안전)")
        set_auto_observe_flag(True, home=home7)
        ck(auto_observe_enabled(home7) is True and os.path.exists(_auto_observe_flag_path(home7)),
           "set_auto_observe_flag(True) → 플래그 ON")
        set_auto_observe_flag(False, home=home7)
        ck(auto_observe_enabled(home7) is False, "set_auto_observe_flag(False) → OFF 복귀")
        led7 = os.path.join(home7, "ledger.sqlite")
        lcon7 = sqlite3.connect(led7)
        apply_schema(lcon7)
        lcon7.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
                      "created_at,semantic_subtype,use_count) VALUES"
                      "('node:CONV:h1','judgment','문장',0,'active','h',?, '교훈',0)", (TS,))
        lcon7.execute("INSERT INTO edges(edge_id,relation,source,target,candidate,state,created_at)"
                      " VALUES('e7','supports_judgment','node:CONV:x','node:CONV:h1',0,'active',?)",
                      (TS_LATER,))
        lcon7.commit()
        lcon7.close()
        record_trace("dry 점검", "preflight",
                     [{"node_id": "node:CONV:h1", "semantic_subtype": "교훈",
                       "rank_score": 0.8, "relevance": 0.7}], TS, home=home7)
        dry = auto_observe_adoption(TS_LATER, home=home7, ledger_path=led7, dry_run=True)
        ck(dry["observed"] == 1 and dry["dry_run"] is True,
           "dry_run: 편입 1건 감지(미리보기)")
        ck(aggregate(home=home7)["overall"]["outcomes"] == 0,
           "dry_run: 실제 도장 0(미리보기만 · store 에 outcome 없음)")
        real7 = auto_observe_adoption(TS_LATER, home=home7, ledger_path=led7)
        ck(real7["observed"] == 1 and aggregate(home=home7)["overall"]["used"] == 1,
           "dry_run 후 실제 실행 → used 1 도장")

        # ── 미스 후보 자동선별(list_miss_candidates · auto_observe 부정 거울 · owner 논증 2026-07-21) ──
        #    회상됐으나 오래도록 그래프 미편입 = owner 가 '미스' 도장할 후보. AI 선별=조회(값 확정 0).
        home8 = os.path.join(tmp, ".binggupack8")
        os.makedirs(home8, exist_ok=True)
        set_trace_flag(True, home=home8)
        led8 = os.path.join(home8, "ledger.sqlite")
        lcon8 = sqlite3.connect(led8)
        apply_schema(lcon8)
        for nid in ("node:CONV:m1", "node:CONV:m2"):
            lcon8.execute("INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
                          "created_at,semantic_subtype,use_count) VALUES"
                          "(?,'judgment','오래 안 쓰인 회상',0,'active','h',?, '교훈',0)", (nid, TS))
        lcon8.commit()
        lcon8.close()
        record_trace("오래된 회상", "preflight",
                     [{"node_id": "node:CONV:m1", "semantic_subtype": "교훈",
                       "rank_score": 0.5, "relevance": 0.4},
                      {"node_id": "node:CONV:m2", "semantic_subtype": "버그패턴",
                       "rank_score": 0.9, "relevance": 0.8}], TS, home=home8)
        NOW8 = "2026-06-29T00:00:00Z"  # TS(06-27) + 48h
        led8_mt = os.path.getmtime(led8)
        miss8 = list_miss_candidates(NOW8, home=home8, ledger_path=led8, min_age_hours=24)
        ck(len(miss8) == 2 and {m["node_id"] for m in miss8} == {"node:CONV:m1", "node:CONV:m2"}
           and all(m["age_hours"] >= 24 for m in miss8),
           "list_miss_candidates: 오래됨·미편입 → 미스 후보(owner 도장 대상 · AI 값 확정 0)")
        ck(miss8[0]["node_id"] == "node:CONV:m1",
           "미스 후보 정렬: 같은 나이면 낮은 관련도(rank) 우선")
        ck(aggregate(home=home8)["overall"]["outcomes"] == 0,
           "list_miss_candidates 는 recall_outcomes write 0(선별=조회 ≠ 판정 · 헌법 정합)")
        ck(os.path.getmtime(led8) == led8_mt,
           "list_miss_candidates: ledger read-only(mode=ro · mtime 불변)")
        ck(len(list_miss_candidates("2026-06-27T06:00:00Z", home=home8,
                                    ledger_path=led8, min_age_hours=24)) == 0,
           "신선 회상(<24h) → 미스 후보 제외(판정 유예)")
        lcon8 = sqlite3.connect(led8)
        lcon8.execute("INSERT INTO edges(edge_id,relation,source,target,candidate,state,created_at)"
                      " VALUES('e8','supports_judgment','node:CONV:z','node:CONV:m1',0,'active',?)",
                      (TS_LATER,))
        lcon8.commit()
        lcon8.close()
        ck({m["node_id"] for m in list_miss_candidates(NOW8, home=home8, ledger_path=led8,
                                                       min_age_hours=24)} == {"node:CONV:m2"},
           "회상 후 관계엣지 편입 → 미스 후보 제외(긍정/부정 거울 대칭)")
        ck(len(list_miss_candidates(NOW8, home=home8, ledger_path=led8,
                                    min_age_hours=24, top_n=1)) == 1,
           "top_n 컷(AI 선별 소수만 owner 에 제시 · 425 통짜 방지)")

        # ── 운영 ledger sentinel 미접촉 ──
        ck(os.path.exists(ledger) and os.path.getmtime(ledger) == ledger_mt0,
           "운영 ledger.sqlite sentinel 미접촉(별도 store · write 0)")
    finally:
        CFG_home = None  # noqa: F841
        shutil.rmtree(tmp, ignore_errors=True)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck(op_before == op_after, "운영 store 불변(OPERATING_PATHS mtime 전후 동일)")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    if sys.argv[1] == "--aggregate":
        import json as _json
        print(_json.dumps(aggregate(), ensure_ascii=False, indent=2))
        sys.exit(0)
    if sys.argv[1] == "--observe-dry-run":
        # 운영 홈 T0 자동관측 미리보기(도장 0 · owner 확인용) — 실시간 ts(실행 기록 목적)
        from datetime import datetime, timezone
        _h = _plat.binggu_home()
        _res = auto_observe_adoption(datetime.now(timezone.utc).isoformat(),
                                     home=_h, ledger_path=os.path.join(_h, "ledger.sqlite"),
                                     dry_run=True)
        print(json.dumps(_res, ensure_ascii=False, indent=2))
        sys.exit(0)
    print("usage: binggu_recall_trace.py [--selftest | --aggregate | --observe-dry-run]")
    sys.exit(2)
