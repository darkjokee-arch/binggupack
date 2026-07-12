# -*- coding: utf-8 -*-
"""Studio Memory Explorer read model — ledger 조회(mode=ro) · lexical recall · exact-ID detail.

전부 read-only: mode=ro URI 조회만 · makedirs 0 · apply_schema 0 · migration 0 · audit append 0 ·
use_count 증가 0 · snapshot 생성 0 · cache 생성 0. schema 를 적용할 수 있는 open_g3/open_accept 는
이 경로에서 쓰지 않는다(daily 의 _ro_connect 재사용). 표/컬럼 이름은 정적 상수, 값은 전부 parameter binding.

recall 은 LEXICAL_ONLY_SCORER(항상 None) 주입 — why_search 가 scorer is None 분기를 타지 않아
_semantic_scorer() 초기화·recall_embed_cache open·Ollama/embed/network 를 전혀 하지 않고 term-frequency
ranking 만 쓴다. use_count/recall_trace 기록도 하지 않는다(호출측 record 미선택).
"""
import hashlib
import unicodedata

from binggupack.cli import daily              # _ro_connect · _table_exists · safe_excerpt · _ensure_scripts_path

SCHEMA_VERSION = 1

LIST_LIMIT_MAX = 100
LIST_LIMIT_DEFAULT = 30
RECALL_LIMIT_MAX = 20
RECALL_LIMIT_DEFAULT = 10
NODE_ID_MAX = 300
QUERY_MAX = 500

_CLAIM_CAP = 160
_DETAIL_CLAIM_CAP = 400
_EXCERPT_CAP = 160

STATES = ("active", "deprecated", "all")
_ACTIVE_STATES = (None, "active", "confirmed")


class ValidationError(Exception):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


def node_id8(node_id):
    """표시용 안전 식별자(sha256[:8]) — 원문 node_id/EVC- id 대신 노출."""
    return hashlib.sha256((node_id or "").encode("utf-8")).hexdigest()[:8]


def _rank_score(created_at, use_count):
    """P1 rank_score(신선도+유용성). p1_ranking 은 scripts(binggu_p1_config)에 의존하므로 지연 import —
    studio 패키지 import 만으로 scripts sys.path 를 요구하지 않게 한다(binggu.py 실행 시 주입)."""
    try:
        daily._ensure_scripts_path()
        from binggupack.pack import p1_ranking as RANK
        return round(RANK.node_rank_score(created_at, use_count), 6)
    except Exception:
        return 0.0


def normalize_text(s):
    """control/format(bidi) 제거 + 공백 정규화(절단 없음 — exact id/query 검증용)."""
    if s is None:
        return ""
    out = []
    for ch in unicodedata.normalize("NFC", str(s)):
        if ch in ("\t", "\n", "\r"):
            out.append(" ")
            continue
        if unicodedata.category(ch) in ("Cc", "Cf"):
            continue
        out.append(ch)
    return " ".join("".join(out).split())


# ── 입력 검증(순수 · API 계층에서 400 판정에 사용) ────────────────────────────────
def parse_int(raw, field):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValidationError(field, "%s must be an integer" % field)


def validate_list_params(state, limit, offset):
    if state not in STATES:
        raise ValidationError("state", "state must be one of active|deprecated|all")
    if not (1 <= limit <= LIST_LIMIT_MAX):
        raise ValidationError("limit", "limit must be 1..%d" % LIST_LIMIT_MAX)
    if offset < 0:
        raise ValidationError("offset", "offset must be >= 0")


def validate_node_id(raw):
    nid = normalize_text(raw)
    if "\x00" in (raw or ""):
        raise ValidationError("node_id", "node_id must not contain NUL")
    if not nid:
        raise ValidationError("node_id", "node_id is required")
    if len(nid) > NODE_ID_MAX:
        raise ValidationError("node_id", "node_id too long")
    return nid


def validate_query(raw):
    if raw is None:
        raise ValidationError("q", "q is required")
    if "\x00" in raw:
        raise ValidationError("q", "q must not contain NUL")
    q = normalize_text(raw)
    if not q:
        raise ValidationError("q", "q must not be empty")
    if len(q) > QUERY_MAX:
        raise ValidationError("q", "q too long (max %d)" % QUERY_MAX)
    return q


# ── ledger 컬럼 정규화(구 ledger 방어 · recall._load_graph 규약) ─────────────────
def _ncols(con):
    try:
        return {c[1] for c in con.execute("PRAGMA table_info(nodes)")}
    except Exception:
        return set()


def _node_cols(cols):
    return ["node_id", "node_type", "sentence", "state",
            "semantic_subtype" if "semantic_subtype" in cols else "NULL AS semantic_subtype",
            "created_at" if "created_at" in cols else "NULL AS created_at",
            "use_count" if "use_count" in cols else "0 AS use_count"]


def _review_status(con, nid):
    if not daily._table_exists(con, "judgment_reviews"):
        return None
    try:
        r = con.execute("SELECT status, outcome FROM judgment_reviews WHERE node_id=? "
                        "ORDER BY review_id DESC LIMIT 1", (nid,)).fetchone()
    except Exception:
        return None
    if not r:
        return None
    return r[0] if r[0] == "pending" else ("resolved:%s" % (r[1] or "?"))


def _evidence_count(con, nid):
    try:
        return int(con.execute(
            "SELECT COUNT(*) FROM edges WHERE relation='evidence_supports' AND target=?",
            (nid,)).fetchone()[0])
    except Exception:
        return 0


def _build_where(state, node_type, subtype, q, cols):
    """정적 컬럼명 + 값 parameter binding. state 는 화이트리스트 리터럴만."""
    clauses, params = [], []
    if state == "active":
        clauses.append("state='active'")
    elif state == "deprecated":
        clauses.append("state='deprecated'")
    # all → state 조건 없음
    if node_type:
        clauses.append("node_type=?")
        params.append(node_type)
    if subtype and "semantic_subtype" in cols:
        clauses.append("semantic_subtype=?")
        params.append(subtype)
    if q:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("sentence LIKE ? ESCAPE '\\'")
        params.append("%" + esc + "%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# ── snapshots ────────────────────────────────────────────────────────────────────
def collect_memory_list_snapshot(ledger, state="active", node_type=None, subtype=None,
                                 q=None, limit=LIST_LIMIT_DEFAULT, offset=0):
    """페이지네이션된 ledger 목록(read-only). 필터: state/type/subtype/q(LIKE). 정렬: active 우선 →
    created_at DESC → node_id(결정적). 무제한 전량 반환 없음(limit cap)."""
    out = {"schema_version": SCHEMA_VERSION, "total": 0, "offset": offset, "limit": limit,
           "items": [], "filters": {"state": state, "type": node_type, "subtype": subtype, "q": q}}
    con = daily._ro_connect(ledger)
    if con is None:
        return out
    try:
        if not daily._table_exists(con, "nodes"):
            return out
        cols = _ncols(con)
        where, params = _build_where(state, node_type, subtype, q, cols)
        try:
            out["total"] = int(con.execute("SELECT COUNT(*) FROM nodes " + where, params).fetchone()[0])
        except Exception:
            return out
        order = "ORDER BY (state='active') DESC, "
        order += "created_at DESC, " if "created_at" in cols else ""
        order += "node_id"
        sel = _node_cols(cols)
        try:
            rows = con.execute("SELECT " + ",".join(sel) + " FROM nodes " + where + " " + order +
                               " LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
        except Exception:
            return out
        for nid, ntype, sent, st, subt, created, uc in rows:
            out["items"].append({
                "node_id": nid, "display_id": node_id8(nid), "state": st,
                "node_type": ntype, "semantic_subtype": subt,
                "claim": daily.safe_excerpt(sent, _CLAIM_CAP),
                "created_at": created, "use_count": int(uc or 0),
                "rank_score": _rank_score(created, uc),
                "review_status": _review_status(con, nid),
                "evidence_count": _evidence_count(con, nid),
            })
    finally:
        con.close()
    return out


def _acceptance(con, nid):
    if not daily._table_exists(con, "owner_acceptances"):
        return None
    try:
        r = con.execute("SELECT event, reason, ts FROM owner_acceptances WHERE node_id=? "
                        "ORDER BY event_id DESC LIMIT 1", (nid,)).fetchone()
    except Exception:
        return None
    if not r:
        return None
    return {"event": r[0], "reason": daily.safe_excerpt(r[1], 120), "ts": r[2]}


def _evidence_items(con, nid):
    """자기증빙 evidence(evidence_supports → evidence 테이블 sentence). source_pointer_id/source_hash 미노출."""
    try:
        srcs = [r[0] for r in con.execute(
            "SELECT source FROM edges WHERE relation='evidence_supports' AND target=?", (nid,))]
    except Exception:
        return []
    has_ev = daily._table_exists(con, "evidence")
    items = []
    for s in srcs:
        excerpt = None
        if has_ev:
            try:
                er = con.execute("SELECT sentence FROM evidence WHERE evidence_id=?", (s,)).fetchone()
                if er:
                    excerpt = daily.safe_excerpt(er[0], _EXCERPT_CAP)
            except Exception:
                excerpt = None
        items.append({"display_id": node_id8(s), "excerpt": excerpt})
    return items


def _relations(con, nid, cols):
    """node 간 direct relation(evidence_supports 제외 · active/confirmed edge). peer=상대 node."""
    try:
        rows = con.execute(
            "SELECT relation, source, target, state FROM edges "
            "WHERE (source=? OR target=?) AND relation!='evidence_supports'", (nid, nid)).fetchall()
    except Exception:
        return []
    subt_col = "semantic_subtype" if "semantic_subtype" in cols else "NULL"
    out = []
    for rel, src, tgt, est in rows:
        if est not in _ACTIVE_STATES:
            continue
        peer = tgt if src == nid else src
        direction = "out" if src == nid else "in"
        try:
            pr = con.execute("SELECT node_type, sentence, " + subt_col +
                             " FROM nodes WHERE node_id=?", (peer,)).fetchone()
        except Exception:
            pr = None
        if pr:
            out.append({"relation": rel, "direction": direction, "peer_display_id": node_id8(peer),
                        "peer_type": pr[0], "peer_subtype": pr[2],
                        "peer_excerpt": daily.safe_excerpt(pr[1], _EXCERPT_CAP), "dangling": False})
        else:
            out.append({"relation": rel, "direction": direction, "peer_display_id": node_id8(peer),
                        "peer_type": None, "peer_subtype": None, "peer_excerpt": None, "dangling": True})
    return out


def _explain_summary(ledger, nid, state):
    """active node 는 judgment_trace(mode=ro · write 0) 요약/confidence 재사용. deprecated 는 None."""
    if state not in _ACTIVE_STATES:
        return None, 0.0
    try:
        from binggupack.pack import recall as RC
        jt = RC.judgment_trace(ledger, nid)
        if jt.get("found"):
            return jt.get("summary"), float(jt.get("confidence", 0.0))
    except Exception:
        pass
    return None, 0.0


def collect_memory_detail_snapshot(ledger, node_id):
    """full node_id exact match(정적 컬럼 · parameter binding). 없으면 None(→404). deprecated 도 지원.
    id8/suffix fuzzy 없음 — WHERE node_id=? 정확 일치만. raw source/nonce/path/credential 미노출."""
    con = daily._ro_connect(ledger)
    if con is None:
        return None
    try:
        if not daily._table_exists(con, "nodes"):
            return None
        cols = _ncols(con)
        sel = _node_cols(cols)
        row = con.execute("SELECT " + ",".join(sel) + " FROM nodes WHERE node_id=?", (node_id,)).fetchone()
        if row is None:
            return None
        nid, ntype, sent, st, subt, created, uc = row
        evidence = _evidence_items(con, nid)
        relations = _relations(con, nid, cols)
        detail = {
            "schema_version": SCHEMA_VERSION,
            "node_id": nid, "display_id": node_id8(nid), "state": st,
            "node_type": ntype, "semantic_subtype": subt,
            "claim": daily.safe_excerpt(sent, _DETAIL_CLAIM_CAP),
            "created_at": created, "use_count": int(uc or 0),
            "rank_score": _rank_score(created, uc),
            "review_status": _review_status(con, nid),
            "acceptance": _acceptance(con, nid),
            "evidence": evidence,
            "evidence_count": len(evidence),
            "relations": relations,
            "relation_count": len(relations),
        }
    finally:
        con.close()
    detail["explain_summary"], detail["confidence"] = _explain_summary(ledger, node_id, st)
    return detail


def _lexical_only_scorer(query, sentence):
    """항상 None — why_search 가 scorer is None 분기(_semantic_scorer 초기화 · recall_embed_cache open ·
    Ollama/embed/network)를 타지 않게 해 term-frequency ranking 만 쓰게 한다."""
    return None


def collect_recall_snapshot(ledger, query, limit=RECALL_LIMIT_DEFAULT):
    """lexical-only 회상(read-only). semantic scorer/cache/network 0 · use_count/trace 기록 0."""
    from binggupack.pack import recall as RC
    res = RC.why_search(ledger, query, limit=limit, scorer=_lexical_only_scorer)
    items = []
    for n in res.get("relevant_nodes", []):
        items.append({
            "node_id": n["node_id"], "display_id": node_id8(n["node_id"]),
            "claim": daily.safe_excerpt(n.get("claim"), _CLAIM_CAP),
            "node_type": n.get("node_type"), "semantic_subtype": n.get("semantic_subtype"),
            "relevance": n.get("relevance"), "rank_score": n.get("rank_score"),
            "trust": n.get("trust"),
        })
    relations = [{"relation": e["relation"],
                  "source_display_id": node_id8(e["source"]),
                  "target_display_id": node_id8(e["target"])}
                 for e in res.get("relevant_edges", [])]
    return {"schema_version": SCHEMA_VERSION, "query": query, "count": len(items),
            "items": items, "relations": relations, "confidence": res.get("confidence", 0.0),
            "mode": "lexical", "note": "Studio 검색은 읽기 전용 lexical recall 입니다. "
            "의미 검색 설정이나 캐시는 변경하지 않습니다."}
