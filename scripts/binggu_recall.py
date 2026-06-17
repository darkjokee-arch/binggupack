# -*- coding: utf-8 -*-
"""binggu_recall.py — 회상 API + 반문 엔진 (L4 회상 · L5 preflight · L6 반문 · read-only).

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
"""
from __future__ import annotations

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import binggu_p1_ranking as RANK                # node_rank_score · record_use  # noqa: E402
import binggu_p1_config as CFG                  # recall_config(설정값)         # noqa: E402
from binggu_rationale_suggest import _SUBTYPE_WHY  # 결정적 반문 문구 토대       # noqa: E402

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

def _semantic_scorer():
    """Ollama bge-m3 cos 보강 — 사용 가능하면 (embed된 query, node embed) 유사도 함수, 아니면 None.

    엔진 부재/임베드 실패는 graceful(None 반환) — 회상은 term-frequency 로만 동작.
    capture 가 쓰는 동일 엔진(binggu_semantic_shadow) 재사용 — 신규 모델 0.
    """
    try:
        import binggu_semantic_shadow as SH
        if not SH.DEFAULT_ENABLED:
            # 기본 비활성 — 자동 활성 0(헌법). owner 가 켰을 때만 보강.
            return None
        probe = SH._embed("점검", timeout=3)
        if probe is None:
            return None

        def score(query, sentence):
            qe = SH._embed(query, timeout=3)
            se = SH._embed(sentence, timeout=3)
            if qe is None or se is None:
                return None
            return SH._dot(qe, se)
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
            if cs is not None:
                rel = max(rel, max(0.0, cs))  # cos 보강(의미 매칭) — 둘 중 강한 신호
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
            if cs is not None:
                rel = max(rel, max(0.0, cs))
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


# ---------------- selftest (temp ledger · 운영 미접촉 · write 0) ----------------

def _selftest():
    import sqlite3
    import tempfile
    import shutil
    from datetime import datetime, timezone

    sys.path.insert(0, HERE)
    from openbinggu_staging_write_selftest import OPERATING_PATHS

    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_recall_")
    try:
        # ── 빈 그래프(신규 사용자) graceful — 파일조차 없음 ──
        empty_ledger = os.path.join(tmp, "nonexistent.sqlite")
        ws0 = why_search(empty_ledger, "배포 검증")
        ck(ws0["relevant_nodes"] == [] and ws0["confidence"] == 0.0,
           "빈 그래프(파일 부재) why_search → 빈 결과·confidence 0(에러 0)")
        jt0 = judgment_trace(empty_ledger, "node:CONV:deadbeef")
        ck(jt0["found"] is False and jt0["chain"] == [],
           "빈 그래프 judgment_trace → found False·빈 사슬(에러 0)")
        pf0 = preflight_context(empty_ledger, prompt="바로 배포한다", cwd="/x/bid-engine")
        ck(pf0["remember"] == [] and pf0["needs_question"] is False
           and pf0["risk_level"] == "낮음",
           "빈 그래프 preflight → 빈 기억·반문 없음(신규 사용자 graceful)")

        # ── 실제 그래프 구성(temp ledger) ──
        ledger = os.path.join(tmp, "ledger.sqlite")
        con = sqlite3.connect(ledger)
        con.executescript(
            "CREATE TABLE nodes(node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT,"
            " candidate INT, state TEXT, content_hash TEXT, created_at TEXT,"
            " semantic_subtype TEXT, use_count INTEGER DEFAULT 0);"
            "CREATE TABLE evidence(evidence_id TEXT, sentence TEXT, source_pointer_id TEXT, source_hash TEXT);"
            "CREATE TABLE edges(edge_id TEXT, relation TEXT, source TEXT, target TEXT,"
            " candidate INT, state TEXT, evidence_refs TEXT);")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def add_node(nid, ntype, sent, sub, used=0, cand=0, state="active"):
            con.execute(
                "INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
                "created_at,semantic_subtype,use_count) VALUES(?,?,?,?,?,?,?,?,?)",
                (nid, ntype, sent, cand, state, "h", now, sub, used))
            con.execute("INSERT INTO evidence VALUES(?,?,?,?)",
                        ("EVC-CONV-" + nid.split(":")[-1], sent, "ptr", "sh"))

        # 위험패턴(버그패턴): "검증 없이 바로 배포해서 실패" — 배포 작업과 닮음.
        add_node("node:CONV:aa01", "judgment",
                 "검증 없이 바로 배포하면 실패한다 selftest live endpoint 확인 누락", "버그패턴", used=5)
        # 교훈(약한 위험 신호)
        add_node("node:CONV:bb02", "judgment",
                 "배포 전에 로컬 selftest 와 live endpoint 를 확인한다", "교훈", used=2)
        # 무관한 판단(요리)
        add_node("node:CONV:cc03", "judgment", "토마토 수프는 마지막에 간을 맞춘다", "결정")
        # 근거(증거) — 사슬용
        add_node("node:CONV:dd04", "evidence",
                 "지난주 배포에서 endpoint 응답 500 로그가 찍혔다", "사실")
        # 선호
        add_node("node:CONV:ee05", "judgment", "배포 작업은 항상 백업 먼저 한다", "선호")
        # supports_judgment edge: 증거(dd04) → 판단(aa01)
        con.execute("INSERT INTO edges VALUES(?,?,?,?,?,?,?)",
                    ("edge:1", "supports_judgment", "node:CONV:dd04", "node:CONV:aa01",
                     0, "active", "[]"))
        con.commit()
        con.close()

        # ── why_search: 관련 노드 rank_score순 ──
        ws = why_search(ledger, "배포 검증 endpoint")
        nids = [n["node_id"] for n in ws["relevant_nodes"]]
        ck(len(nids) > 0 and "node:CONV:aa01" in nids and "node:CONV:cc03" not in nids,
           "why_search → 관련 노드 회수(배포 관련 O · 무관 요리 X)")
        ck(ws["confidence"] > 0.0 and ws["relevant_nodes"][0]["candidate"] is True,
           "why_search → confidence>0 · candidate 표시")
        # rank_score 정렬 보존(내림차순)
        rs = [n["rank_score"] for n in ws["relevant_nodes"]]
        ck(rs == sorted(rs, reverse=True) or len(rs) <= 1,
           "why_search 동점 시 rank_score 내림차순 정렬")

        # ── judgment_trace: edge 따라 사슬 ──
        jt = judgment_trace(ledger, "node:CONV:aa01")
        ck(jt["found"] and len(jt["chain"]) >= 1
           and any(c["from"] == "node:CONV:aa01" and c["peer_present"] for c in jt["chain"]),
           "judgment_trace → 연결 엣지 사슬(증거→판단)")
        jt_iso = judgment_trace(ledger, "node:CONV:ee05")
        ck(jt_iso["found"] and jt_iso["chain"] == [],
           "judgment_trace 고립 노드 → 빈 사슬(에러 0)")
        jt_dangling = judgment_trace(ledger, "node:CONV:zzzz")
        ck(jt_dangling["found"] is False, "judgment_trace dangling node → found False graceful")

        # ── 반문: 위험패턴 닮으면 needs_question ──
        pf = preflight_context(ledger, prompt="검증 없이 바로 배포하려고 한다 endpoint",
                               cwd="/work/bid-engine")
        ck(pf["risk_level"] in ("중간", "높음") and len(pf["avoid_patterns"]) >= 1,
           "preflight 위험작업 → 위험도 중간↑ · avoid_patterns(버그패턴) 매칭")
        ck(pf["needs_question"] and pf["question"] and "배포" in pf["question"],
           "preflight 높은 위험 → needs_question True · 반문 문구 생성")
        ck(any("node:CONV:aa01" == m["node_id"] for m in pf["avoid_patterns"]),
           "avoid_patterns = 버그패턴 노드(검증없이 배포)")

        # ── 무관 작업 → 반문 0 ──
        pf_safe = preflight_context(ledger, prompt="토마토 수프 레시피를 정리한다",
                                    cwd="/work/cooking")
        ck(pf_safe["needs_question"] is False and len(pf_safe["avoid_patterns"]) == 0,
           "preflight 무관 작업(요리) → 반문 없음 · avoid 0")

        # ── 임계 override: risk_high 를 낮추면 같은 작업이 반문 ──
        home = os.path.join(tmp, ".binggupack")
        os.makedirs(home, exist_ok=True)
        CFG.save_user_config({"recall_config": {"risk_mid_score": 0.01, "risk_high_score": 0.02,
                                                "preflight_max": 5, "recall_limit": 5}}, home=home)
        pf_low = preflight_context(ledger, prompt="배포", cwd="/x", home=home)
        ck(pf_low["needs_question"] is True,
           "임계 override(risk_high 낮춤) → 약한 매칭도 반문(사용자 조정 반영)")
        # 반대로 임계를 올리면(0.9) 부분 매칭은 반문 안 함(과잉반문 방지).
        # "배포한다" 만 = aa01 노드와 부분 매칭(score < 0.9) → 임계 미달.
        CFG.save_user_config({"recall_config": {"risk_mid_score": 0.5, "risk_high_score": 0.9,
                                                "preflight_max": 5, "recall_limit": 5}}, home=home)
        pf_high = preflight_context(ledger, prompt="배포한다", cwd="/x", home=home)
        ck(pf_high["needs_question"] is False,
           "임계 override(risk_high 0.9) → 부분 매칭 반문 안 함(과잉반문 방지)")

        # ── preferences 회수(subtype=선호) ──
        pf_pref = preflight_context(ledger, prompt="배포 작업 백업")
        ck(any(p["node_id"] == "node:CONV:ee05" for p in pf_pref["preferences"]),
           "preflight → 사용자 선호(subtype=선호) 회수")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck(op_before == op_after, "운영 store 불변(ledger write 0 · read-only)")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    print("usage: binggu_recall.py [--selftest]")
    sys.exit(2)
