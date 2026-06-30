# -*- coding: utf-8 -*-
"""binggu_recall — 회상 API + 반문 엔진 (L4 회상 · L5 preflight · L6 반문 · read-only).

설계: BINGGUPACK_USER_AGI_FULL_DESIGN.md §10(읽기도구) · Phase4(why_search/judgment_trace) ·
      Phase5(preflight) · Phase6(반문 엔진 위험도 3단). 헌법 §6 안전벨트(회상=경고/랭킹 안전).

세 가지 read-only 회상 함수(저장 0 · ledger write 0 · use_count record 만 호출측 선택):
  - why_search(query)       : query 관련 노드 + why-edge(supports_judgment 등). P1 rank_score 정렬.
  - judgment_trace(node_id) : 판단 노드 1개에서 연결 엣지를 따라 근거 사슬(다홉) + 검증 이력.
  - preflight_context(...)  : cwd/prompt/domain → 관련 판단 3~7 + 위험패턴 매칭 + 반문 필요여부.

반문 엔진(Phase6): 현재 작업 텍스트가 과거 위험패턴(node_type=judgment +
  semantic_subtype∈{버그패턴,교훈})과 닮으면 위험도 산출:
    낮음 → 조용히 참고 / 중간 → 짧게 경고 / 높음 → 반문(needs_question=True).
  위험도 = subtype 가중(버그패턴 1.0 > 교훈 0.7) × 관련성(term-frequency 정규화).
  임계는 binggu_p1_config.recall_config (사용자 조정 · 과잉반문 방지).

불변:
  - 순수 read-only — ledger 는 P3.extract_real_ledger(mode=ro) 로만 읽음. write 0.
  - 빈 그래프 graceful — 노드 0(신규 사용자)이면 빈 결과 · 반문 없음 · 에러 0.
  - owner 31노드 하드코딩 0 — 어떤 사용자 ledger 든 동일 동작.
  - 신규 predicate 0 · rationale 문구는 결정적 템플릿(LLM 0 · hallucination 0).
  - cos 임베딩(Ollama)은 선택적 보강 — 없으면 term-frequency 로 graceful fallback.

v1.16 strangler Phase2: 순수 transform impl(토큰화/관련성·_load_graph(ro)·why_search/
judgment_trace/match_risk_patterns/preflight_context·embed 캐시 helper)이 이 모듈로 이관됐다.
형제 의존은 패키지 import 로 재배선됐고(p1_ranking→binggupack.pack · p1_config→binggupack.safety),
미이관 모듈(binggu_rationale_suggest 상단 import · semantic 함수내부 lazy import)은 scripts/ sys.path
경유 bare-name 으로 유지된다(semantic 은 byte-identical 불가라 미접촉). 진입점 scripts/binggu_recall.py
는 공개 심볼 동일한 thin wrapper(부트스트랩 + temp-home selftest + --precompute CLI 잔류).
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))      # <repo>/binggupack/pack
ROOT = os.path.dirname(os.path.dirname(HERE))          # <repo>
_SCRIPTS = os.path.join(ROOT, "scripts")               # 미이관 sibling(rationale_suggest·semantic lazy)
for _p in (ROOT, _SCRIPTS):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from binggu_rationale_suggest import _SUBTYPE_WHY  # 결정적 반문 문구 토대(미이관)  # noqa: E402

from binggupack.pack import p1_ranking as RANK  # node_rank_score · record_use  # noqa: E402
from binggupack.safety import p1_config as CFG  # recall_config(설정값)         # noqa: E402

# 위험 신호 subtype 가중 — 버그패턴(반복 결함)이 교훈보다 강한 위험 신호.
# 그 외 subtype 은 위험 매칭 후보 아님(0.0 = 매칭 제외).
RISK_SUBTYPE_WEIGHT = {"버그패턴": 1.0, "교훈": 0.7}
RISK_SUBTYPES = tuple(RISK_SUBTYPE_WEIGHT)

# 판단 노드(EN/KO 양쪽 허용 — 저장 node_type 은 EN 'judgment', 일부 fixture 는 '판단').
JUDGMENT_KINDS = ("judgment", "판단")


# ---------------- 토큰화 · 관련성(term-frequency) ----------------

def _tokens(text):
    """공백 분리 소문자 토큰(2자 이상). 한국어는 공백 단위 — cos 보강 시 의미 매칭 대체."""
    if not text:
        return []
    return [t for t in str(text).lower().replace("\n", " ").split() if len(t) >= 2]


def _relevance(query_tokens, sentence):
    """query 토큰이 문장에 등장하는 비율 [0,1]. 부분 문자열 포함도 1회로 계산(결정적)."""
    if not query_tokens:
        return 0.0
    s = (sentence or "").lower()
    hit = sum(1 for t in set(query_tokens) if t in s)
    return hit / float(len(set(query_tokens)))


# ---------------- ledger 로드(read-only) ----------------

def _load_graph(ledger_path):
    """ledger → {nodes, edges, evidence}. 파일 부재/노드 0 → 빈 그래프(graceful, 에러 0).

    로컬 회상 = owner 가 자기 ledger 를 읽는 것 → state='active' 노드 전부(candidate 무관).
    (cloud publish 게이트의 candidate=0 필터와 다름 — 로컬은 SAVE 확정분 모두가 회상 대상.
    owner 운영 ledger 는 sealed candidate=0, CLI 저장분은 candidate=1·active — 둘 다 회상.)
    read-only(mode=ro) — write 0. P3 스타일 방어적 컬럼 정규화(구 ledger 패딩).

    nodes 각 항목: id/node_type/sentence/semantic_subtype/created_at/use_count/rank_score.
    edges 각 항목: id/relation/source/target.
    """
    empty = {"nodes": [], "edges": [], "ev_sent": {}, "by_id": {}}
    if not ledger_path or not os.path.exists(ledger_path):
        return empty
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % ledger_path, uri=True)
        cur = conn.cursor()
        ncols = [c[1] for c in cur.execute("PRAGMA table_info(nodes)")]
        sel = ["node_id", "node_type", "sentence", "state",
               "semantic_subtype" if "semantic_subtype" in ncols else "NULL AS semantic_subtype",
               "created_at" if "created_at" in ncols else "NULL AS created_at",
               "use_count" if "use_count" in ncols else "0 AS use_count"]
        cur.execute("SELECT " + ",".join(sel) + " FROM nodes")
        nrows = cur.fetchall()
        cur.execute("SELECT evidence_id,sentence FROM evidence")
        erows = cur.fetchall()
        try:
            cur.execute("SELECT edge_id,relation,source,target,state FROM edges")
            edge_rows = cur.fetchall()
        except sqlite3.OperationalError:
            edge_rows = []
        conn.close()
    except Exception:
        return empty
    ev_sent = {r[0]: r[1] for r in erows}
    nodes = []
    for r in nrows:
        # r: node_id, node_type, sentence, state, semantic_subtype, created_at, use_count
        if r[3] not in (None, "active", "confirmed"):
            continue  # deprecated 등 제외(보존하되 회상 view 에서 제외 — 헌법 default_view)
        created_at, use_count = r[5], r[6]
        rank = RANK.node_rank_score(created_at, use_count)
        nodes.append({
            "id": r[0], "node_type": r[1], "sentence": r[2] or "",
            "semantic_subtype": r[4],
            "created_at": created_at, "use_count": int(use_count or 0),
            "rank_score": round(rank, 6),
        })
    # rank_score 내림차순(동점 id 사전순 — 결정적). 회상 우선순위 = P1-② 활용.
    nodes.sort(key=lambda n: (-n["rank_score"], n["id"]))
    by_id = {n["id"]: n for n in nodes}
    edges = []
    for er in edge_rows:
        if er[4] not in (None, "active", "confirmed"):
            continue
        edges.append({"id": er[0], "relation": er[1], "source": er[2], "target": er[3]})
    return {"nodes": nodes, "edges": edges, "ev_sent": ev_sent, "by_id": by_id}


# ---------------- 선택적 cos 보강(없으면 term-frequency) ----------------

# bge-m3 cos 의미 하한: 무관 한국어 문장쌍도 통상 0.3~0.5 cos → floor 미만은 어휘 신호만 쓴다
# (semantic ON 시 rel<=0 게이트가 무력화돼 거의 모든 노드가 통과하고, risk 매칭에서 무관 작업에
#  false-positive 반문이 나는 것을 방지). 관련 의미 문장쌍은 통상 0.55+.
_SEMANTIC_FLOOR = 0.55


# ---- 노드 임베딩 영속 캐시 (회상 N+1 embed → query 1회) ----
# 별도 sqlite(recall_embed_cache) — 운영 ledger 불변(순수 파생 데이터). 키 = sentence sha256 + model_digest
# → 문장 변경/모델 교체 시 자동 miss·재계산(stale 0). leak_guard 통과 텍스트만 캐시(PII embed 0).
def _embed_cache_path(home=None):
    base = home or os.path.join(os.path.expanduser("~"), ".binggupack")
    return os.path.join(base, "recall_embed_cache.sqlite")


def _open_embed_cache(home=None):
    p = _embed_cache_path(home)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE IF NOT EXISTS embed_cache("
                "sent_sha TEXT, model TEXT, dim INTEGER, vec BLOB, "
                "PRIMARY KEY(sent_sha, model))")
    return con


def _sent_sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _pack_vec(v):
    return struct.pack("<%df" % len(v), *v)


def _unpack_vec(blob, dim):
    return list(struct.unpack("<%df" % dim, blob))


def precompute_embeddings(ledger_path, home=None, embed_fn=None):
    """active 노드 sentence 임베딩 미리 캐시(멱등) — 회상 N+1 embed → query 1회로.
    semantic OFF(opt-in 미통과)면 SKIP. ledger read-only · 캐시 sqlite 만 write(운영 데이터 불변)."""
    try:
        if embed_fn is None:
            import binggu_canonical_semantic as CS
            if not CS.enabled():
                return {"status": "SKIP", "reason": "semantic OFF (opt-in 미통과)"}
        import binggu_semantic_shadow as SH
        embed = embed_fn or SH._embed
        model = SH.model_digest()
        g = _load_graph(ledger_path)
        cache = _open_embed_cache(home)
        computed = skipped = failed = 0
        for n in g["nodes"]:
            text = n["sentence"]
            sha = _sent_sha(text)
            if cache.execute("SELECT 1 FROM embed_cache WHERE sent_sha=? AND model=?",
                             (sha, model)).fetchone():
                skipped += 1
                continue
            ok, _ = SH.leak_guard(text)
            if not ok:
                failed += 1
                continue
            v = embed(text)
            if v is None:
                failed += 1
                continue
            cache.execute("INSERT OR REPLACE INTO embed_cache VALUES(?,?,?,?)",
                          (sha, model, len(v), _pack_vec(v)))
            computed += 1
        cache.commit()
        cache.close()
        return {"status": "OK", "total": len(g["nodes"]), "computed": computed,
                "skipped": skipped, "failed": failed, "model": model}
    except Exception as e:
        return {"status": "ERROR", "reason": str(e)}


def _semantic_scorer(home=None, embed_fn=None):
    """bge-m3 cos 보강 — 사용 가능하면 (query, sentence) → cos 유사도 함수, 아니면 None.

    P2 semantic 회상. 활성화는 두 게이트의 AND(어느 하나라도 OFF면 None → term-frequency 만):
      1) binggu_canonical_semantic.enabled() — opt-in(명시 플래그 OR Ollama bge-m3 자동감지,
         BINGGU_SEMANTIC_OFF=1 이면 강제 OFF). 도장 제안과 동일한 단일 opt-in 원천 재사용.
      2) recall_config["semantic_recall_enabled"](기본 False) — 회상에 한정한 owner 추가 스위치.
    엔진은 capture·도장이 쓰는 동일 bge-m3(binggu_semantic_shadow._embed) 재사용 — 신규 모델 0.

    query 임베딩은 1회만 계산해 클로저에 캐시(노드마다 재임베드 방지 — N+1 → 1+N).
    엔진 부재/임베드 실패/예외는 전부 graceful(None) — 회상은 term-frequency 로만 동작.
    read-only — embedding 결과는 메모리에서만 cos 계산에 쓰이고 어디에도 저장하지 않는다.
    embed_fn= 테스트 주입(결정적 fake embed) — selftest 가 Ollama 없이 의미 회상 로직 검증.
    """
    try:
        if embed_fn is None:
            import binggu_canonical_semantic as CS
            if not CS.enabled():
                return None  # opt-in OFF — 자동 활성 0(헌법). 사람이 켰을 때만 보강.
            rc = CFG.recall_config(home)
            if not rc.get("semantic_recall_enabled", False):
                return None  # 회상 한정 추가 스위치 OFF
            import binggu_semantic_shadow as SH
            embed = SH._embed
            dot = SH._dot
            if embed("점검", timeout=3) is None:
                return None  # 엔진 미응답 → term-frequency fallback
        else:
            import binggu_semantic_shadow as SH
            embed, dot = embed_fn, SH._dot

        _qcache = {}
        # 노드 임베딩 영속 캐시 — 실 Ollama 경로(embed_fn None)만. fake embed 주입(테스트)은 캐시 우회.
        _cache = _open_embed_cache(home) if embed_fn is None else None
        _model = SH.model_digest()

        def _emb(text):
            if _cache is None:
                return embed(text)
            sha = _sent_sha(text)
            row = _cache.execute("SELECT dim, vec FROM embed_cache WHERE sent_sha=? AND model=?",
                                 (sha, _model)).fetchone()
            if row:
                return _unpack_vec(row[1], row[0])  # 캐시 hit — embed 0
            v = embed(text)
            if v is not None:
                _cache.execute("INSERT OR REPLACE INTO embed_cache VALUES(?,?,?,?)",
                               (sha, _model, len(v), _pack_vec(v)))
                _cache.commit()
            return v

        def score(query, sentence):
            # PII/secret 선차단(leak_guard) — shadow/canonical 임베드 경로와 동일 패리티(회상 경로 누락 수정).
            if query not in _qcache:
                ok_q, _ = SH.leak_guard(query)
                _qcache[query] = _emb(query) if ok_q else None  # query 1회만 임베드(캐시)
            qe = _qcache[query]
            if qe is None:
                return None
            ok_s, _ = SH.leak_guard(sentence)
            if not ok_s:
                return None  # 잔존 PII/secret → semantic 미개입(어휘 fallback)
            se = _emb(sentence)  # 노드 임베딩 영속 캐시(hit 시 embed 0)
            if se is None:
                return None
            return dot(qe, se)
        return score
    except Exception:
        return None


# ---------------- Phase4: why_search ----------------

def why_search(ledger_path, query, limit=None, home=None, scorer=None):
    """query 관련 노드(판단·근거) + 연결 why-edge 회상. P1 rank_score + 관련성 정렬.

    반환(설계 §10 응답형식): {relevant_nodes, relevant_edges, evidence, summary,
                              recommended_question, confidence}. read-only · write 0.
    빈 그래프/무관 query → relevant_nodes [] · confidence 0.0(에러 0).
    """
    rc = CFG.recall_config(home)
    limit = limit or rc["recall_limit"]
    if scorer is None:
        scorer = _semantic_scorer(home=home)  # opt-in 통과 시만 활성, 아니면 None(어휘만)
    g = _load_graph(ledger_path)
    qtok = _tokens(query)
    if not g["nodes"]:
        return {"relevant_nodes": [], "relevant_edges": [], "evidence": [],
                "summary": "그래프가 비어 있습니다(관련 기억 없음).",
                "recommended_question": None, "confidence": 0.0}

    scored = []
    for n in g["nodes"]:
        rel = _relevance(qtok, n["sentence"])
        if scorer is not None and qtok:
            cs = scorer(query, n["sentence"])
            if cs is not None and cs >= _SEMANTIC_FLOOR:
                rel = max(rel, cs)  # cos 보강(의미 매칭, floor 이상만 — 무관 cos 잡음 차단)
        if rel <= 0.0:
            continue
        scored.append((rel, n))
    # 관련성 1차, rank_score(신선도+유용성) 2차, id 사전순 3차 — 결정적.
    scored.sort(key=lambda x: (-x[0], -x[1]["rank_score"], x[1]["id"]))
    top = scored[:limit]

    rel_nodes = [{
        "node_id": n["id"], "claim": n["sentence"][:120],
        "node_type": n["node_type"], "semantic_subtype": n["semantic_subtype"],
        "rank_score": n["rank_score"], "relevance": round(rel, 4),
        "candidate": True, "trust": "candidate_unverified",
    } for rel, n in top]

    top_ids = {n["id"] for _, n in top}
    rel_edges = [{
        "edge_id": e["id"], "relation": e["relation"],
        "source": e["source"], "target": e["target"], "candidate": True,
    } for e in g["edges"] if e["source"] in top_ids or e["target"] in top_ids]

    evidence = [{"node_id": n["id"], "evidence_excerpt": n["sentence"][:120]}
                for _, n in top]
    confidence = round(top[0][0], 4) if top else 0.0
    summary = ("관련 기억 %d건(랭킹순). candidate — 사람 확정 전 참고용." % len(top)
               if top else "query 와 관련된 판단/근거 노드를 찾지 못했습니다.")
    return {"relevant_nodes": rel_nodes, "relevant_edges": rel_edges,
            "evidence": evidence, "summary": summary,
            "recommended_question": None, "confidence": confidence}


# ---------------- Phase4: judgment_trace ----------------

def judgment_trace(ledger_path, node_id, max_hops=3, home=None):
    """판단 노드에서 연결 엣지를 따라 근거 사슬(다홉) + 검증 이력. read-only · write 0.

    반환: {root, chain(노드별 연결 엣지+peer), summary, confidence, found}.
    node_id 부재(dangling) → found=False · 빈 chain(에러 0).
    """
    g = _load_graph(ledger_path)
    if node_id not in g["by_id"]:
        return {"root": node_id, "chain": [], "found": False,
                "summary": "노드를 찾을 수 없습니다(dangling 또는 미저장).",
                "confidence": 0.0}

    # BFS 다홉 — root 에서 연결된 엣지를 따라가며 사슬 구성. 사이클/중복 노드 방어.
    visited = {node_id}
    frontier = [node_id]
    chain = []
    for _hop in range(max_hops):
        next_frontier = []
        for cur in frontier:
            for e in g["edges"]:
                peer = None
                direction = None
                if e["source"] == cur:
                    peer, direction = e["target"], "out"
                elif e["target"] == cur:
                    peer, direction = e["source"], "in"
                if peer is None:
                    continue
                pnode = g["by_id"].get(peer)
                chain.append({
                    "edge_id": e["id"], "relation": e["relation"],
                    "from": cur, "to": peer, "direction": direction,
                    "peer_claim": (pnode["sentence"][:100] if pnode else None),
                    "peer_present": pnode is not None,  # dangling peer graceful
                })
                if pnode is not None and peer not in visited:
                    visited.add(peer)
                    next_frontier.append(peer)
        if not next_frontier:
            break
        frontier = next_frontier

    root = g["by_id"][node_id]
    summary = ("판단 '%s' 의 근거 사슬 %d개 연결(다홉). candidate edge — 사람 확정 전 참고."
               % (root["sentence"][:40], len(chain)) if chain
               else "이 판단에 연결된 근거 엣지가 없습니다(고립 노드).")
    # 사슬 길이 + 근거 노드 존재 비율로 신뢰도(근거 많을수록 ↑).
    present = sum(1 for c in chain if c["peer_present"])
    confidence = round(min(1.0, present / 3.0), 4) if chain else 0.0
    return {"root": {"node_id": node_id, "claim": root["sentence"][:120],
                     "node_type": root["node_type"],
                     "semantic_subtype": root["semantic_subtype"],
                     "rank_score": root["rank_score"]},
            "chain": chain, "found": True, "summary": summary, "confidence": confidence}


# ---------------- Phase6: 반문 엔진(위험도 산출) ----------------

def _risk_question(node):
    """위험패턴 노드 → 반문 문구(결정적 · _SUBTYPE_WHY 토대 재사용). LLM 0 · hallucination 0."""
    sub = node.get("semantic_subtype")
    why = _SUBTYPE_WHY.get(sub, "과거 판단")
    return ("이 작업은 과거 패턴과 닮았습니다: \"%s\" (%s). "
            "같은 실수를 반복하지 않도록, 먼저 점검/확인하고 진행할까요?"
            % (node["sentence"][:60], why))


def match_risk_patterns(g, work_text, qtok, home=None, scorer=None):
    """현재 작업 텍스트 vs 과거 위험패턴(judgment + 버그패턴/교훈) 매칭 → 위험도 산출.

    반환: {risk_level(낮음/중간/높음), needs_question, top_score, matches[], question}.
    빈 그래프/무관 작업 → risk_level=낮음 · needs_question=False · matches [](에러 0).
    """
    rc = CFG.recall_config(home)
    matches = []
    for n in g["nodes"]:
        sub = n["semantic_subtype"]
        if n["node_type"] not in JUDGMENT_KINDS or sub not in RISK_SUBTYPES:
            continue  # 위험 신호 후보 아님(1차 subtype 필터 — 가장 싸고 결정적)
        rel = _relevance(qtok, n["sentence"])
        if scorer is not None and qtok:
            cs = scorer(work_text, n["sentence"])
            if cs is not None and cs >= _SEMANTIC_FLOOR:
                rel = max(rel, cs)  # floor 이상만 — 무관 cos로 인한 false-positive 반문 차단
        if rel <= 0.0:
            continue
        # 위험도 = subtype 가중 × 관련성 [0,1].
        score = RISK_SUBTYPE_WEIGHT[sub] * rel
        matches.append({"node_id": n["id"], "claim": n["sentence"][:100],
                        "semantic_subtype": sub, "risk_score": round(score, 4),
                        "relevance": round(rel, 4)})
    matches.sort(key=lambda m: (-m["risk_score"], m["node_id"]))
    top = matches[0]["risk_score"] if matches else 0.0
    if top >= rc["risk_high_score"]:
        level, needs = "높음", True
    elif top >= rc["risk_mid_score"]:
        level, needs = "중간", False
    else:
        level, needs = "낮음", False
    question = None
    if matches and needs:
        # 높은 위험만 반문 — 원천 노드 찾아 결정적 문구 생성.
        src = next((n for n in g["nodes"] if n["id"] == matches[0]["node_id"]), None)
        if src:
            question = _risk_question(src)
    return {"risk_level": level, "needs_question": needs, "top_score": round(top, 4),
            "matches": matches[:5], "question": question}


# ---------------- Phase5: preflight_context ----------------

def _domain_from_cwd(cwd, domain=None):
    """domain 명시 우선, 없으면 cwd 마지막 경로 조각을 거친 도메인 힌트로(키워드 prefilter용)."""
    if domain:
        return str(domain).lower()
    if not cwd:
        return None
    base = os.path.basename(os.path.normpath(str(cwd)))
    return base.lower() if base and base not in (".", os.sep) else None


def preflight_context(ledger_path, prompt=None, cwd=None, domain=None,
                      files_changed=None, home=None, scorer=None):
    """작업 시작 전 자동 회상 + 반문(L5+L6). 입력=prompt/cwd/domain/files. read-only · write 0.

    반환(설계 Phase5 출력): {remember, ask(반문), avoid_patterns, preferences,
                            risk_level, needs_question, question, confidence}.
    빈 그래프 → 전부 빈 리스트 · risk_level=낮음 · needs_question=False(에러 0).
    """
    rc = CFG.recall_config(home)
    if scorer is None:
        scorer = _semantic_scorer(home=home)  # opt-in 통과 시 의미 회상·반문 보강
    g = _load_graph(ledger_path)
    # 작업 텍스트 = prompt + cwd 도메인 힌트 + 변경 파일명(거친 1차 신호 — 키워드 prefilter).
    dom = _domain_from_cwd(cwd, domain)
    parts = [p for p in [prompt, dom] if p]
    if files_changed:
        parts.extend(os.path.basename(str(f)) for f in files_changed)
    work_text = " ".join(parts)
    qtok = _tokens(work_text)

    if not g["nodes"]:
        return {"remember": [], "ask": [], "avoid_patterns": [], "preferences": [],
                "risk_level": "낮음", "needs_question": False, "question": None,
                "confidence": 0.0,
                "summary": "그래프가 비어 있습니다(신규 사용자 — 회상할 기억 없음)."}

    # remember = 관련 판단/근거 상위 preflight_max개(why_search 재사용).
    ws = why_search(ledger_path, work_text, limit=rc["preflight_max"], home=home, scorer=scorer)
    remember = ws["relevant_nodes"]

    # 반문 = 위험패턴 매칭(Phase6).
    risk = match_risk_patterns(g, work_text, qtok, home=home, scorer=scorer)

    # 하면 안 되는 과거 패턴 = 버그패턴 subtype 중 관련된 것.
    avoid = [m for m in risk["matches"] if m["semantic_subtype"] == "버그패턴"]
    # 사용자 선호 = subtype=선호 노드 중 관련된 것(위험 아님 — 참고).
    preferences = []
    for n in g["nodes"]:
        if n["semantic_subtype"] != "선호":
            continue
        rel = _relevance(qtok, n["sentence"])
        if rel > 0.0:
            preferences.append({"node_id": n["id"], "claim": n["sentence"][:100],
                                "relevance": round(rel, 4)})
    preferences.sort(key=lambda p: -p["relevance"])

    ask = [risk["question"]] if risk["question"] else []
    return {"remember": remember, "ask": ask, "avoid_patterns": avoid,
            "preferences": preferences[:rc["preflight_max"]],
            "risk_level": risk["risk_level"], "needs_question": risk["needs_question"],
            "question": risk["question"], "confidence": ws["confidence"],
            "summary": ("관련 기억 %d · 위험패턴 %d · 위험도 %s%s"
                        % (len(remember), len(risk["matches"]), risk["risk_level"],
                           " (반문 필요)" if risk["needs_question"] else ""))}
