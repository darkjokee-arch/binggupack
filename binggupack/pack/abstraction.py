# -*- coding: utf-8 -*-
"""binggu_abstraction — 반복 판단/hit_events 에서 candidate rule 을 '제안만' 하는 read-only 함수.

목적(작업4): owner ledger 의 반복된 개별 판단(nodes) + 성공/실패 패턴(hit_events)에서
  추상화 후보(candidate rule)를 텍스트 리스트로 '제안'한다. 규칙화(active 승격)는 절대 하지
  않는다 — 제안은 반환/표시뿐이고, 규칙 자산 write 는 사람 SAVE(기존 candidate confirm 경로)에서만.

헌법 4불변(이 모듈이 물리로 보장):
  1) 대화중 0개입 — 자동 실행/자동 확정 0. propose 는 read-only 조회 후 dict 리스트만 반환.
  2) 자동확정 저장 금지 — DB write 0. INSERT/UPDATE/audit_append 0. active 승격 0.
  3) self-modifying 0 — 신규 엣지 타입(generates_rule 등)·스키마 마이그레이션 0. 규칙/박제 write 0.
  4) 로컬 정본 — ledger 는 recall._load_graph(mode=ro) + 별도 mode=ro hit_events 로더로만 접근.

출력 형식(제안 1건): {
  proposal_id            : 'abstraction:'+sha256(sorted(evidence_refs)+subtype)[:16]  # content hash(node_id 아님)
  proposed_principle_text: 결정적 템플릿 문구(LLM 0 · hallucination 0)
  evidence_refs          : 뒷받침 node_id 리스트(정렬·중복 제거) — 비면 제안 자체가 금지
  supporting_count       : len(evidence_refs)  # 순수 근거 노드 개수(구조적)
  semantic_subtype       : 클러스터 subtype(버그패턴/교훈/선호)
  domain                 : _domain_norm(domain)  # 표시용 파티션 키
  evidence_summary       : {distinct_decisions,hits,misses,dominant_outcome}  # 표시 전용(정렬 key 진입 금지)
  source_kind            : 'repeated_judgment'
  trust                  : 'candidate_unverified'
  promote                : False           # 자동 승격 0
  requires_human_save    : True            # 규칙화는 사람 SAVE 승인 이후에만
  save_note              : 안내 문구
}

비인과 봉쇄(guard3 정합): 적중률 신호(get_hit_rate/proposal_priority_signal)는 정렬 key·게이트에
  절대 진입하지 않는다. 정렬은 supporting_count(구조적 카운트) + proposal_id(content hash)만 사용.
  evidence_summary(표시용 순수 int dict)는 hit_stats.assert_not_ranking_input 를 통과시켜
  '신호가 아님'을 코드로 증명한다(_SIGNAL_MARK 없는 값 → 그대로 통과).

Fable5 방어:
  (E) domain 은 hit_events 에 대한 WHERE 파티션 필터로만(가산 아님). 정규화는 hit_stats._domain_norm.
  (D-1) proposal_id 는 node_id 가 아닌 content hash — confirm 위조용 node_id 노출 표면 없음.
  (D-2) _hit_support 는 (decision_id,node_id,speaker) distinct dedupe 후 집계(이중계상 방지).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))      # <repo>/binggupack/pack
ROOT = os.path.dirname(os.path.dirname(HERE))          # <repo>
_SCRIPTS = os.path.join(ROOT, "scripts")               # 미이관 sibling(schema·staging fixture)
for _p in (ROOT, _SCRIPTS):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.pack import recall as RECALL      # _tokens·_relevance·_load_graph·JUDGMENT_KINDS  # noqa: E402
from binggupack.pack import hit_stats as HIT      # assert_not_ranking_input·_domain_norm(신호만)   # noqa: E402

# 추상화 임계 — CLAUDE.md '3회+ 반복' 스킬화 임계와 정합(근거 노드 최소 개수).
MIN_SUPPORT = 3
# 반복 판단 클러스터링 어휘 관련성 임계(term-frequency).
CLUSTER_REL_MIN = 0.5
# 추상화 후보 subtype 만(그 외 subtype 은 제외 — 규칙화 부적합).
ABSTRACTABLE_SUBTYPES = ("버그패턴", "교훈", "선호")
# 판단 노드 종류(EN/KO) — recall 정본 재사용.
JUDGMENT_KINDS = RECALL.JUDGMENT_KINDS


# ---------------- read-only 로더 ----------------

def _load_nodes_ro(ledger_path):
    """active/confirmed 판단 노드 중 추상화 후보 subtype 만 read-only 로 로드.

    recall._load_graph(mode=ro) 를 그대로 호출 → ledger write 0 은 그 커넥션이 보장.
    반환 노드 각 항목: id/node_type/sentence/semantic_subtype/created_at/use_count/rank_score.
    """
    g = RECALL._load_graph(ledger_path)
    out = []
    for n in g["nodes"]:
        if n.get("node_type") not in JUDGMENT_KINDS:
            continue
        if n.get("semantic_subtype") not in ABSTRACTABLE_SUBTYPES:
            continue
        out.append(n)
    return out


def _load_hit_events_ro(ledger_path):
    """hit_events 를 read-only 로 로드(성공/실패 패턴 근거). 파일/테이블 부재 → [] graceful.

    recall._load_graph 는 hit_events 를 로드하지 않으므로 별도 mode=ro 로더가 필요하다.
    write 0 — mode=ro 커넥션으로만 SELECT 하고 즉시 close.
    """
    if not ledger_path or not os.path.exists(ledger_path):
        return []
    events = []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % ledger_path, uri=True)
        try:
            rows = con.execute(
                "SELECT node_id,speaker,outcome,domain,subtype,decision_id,ts FROM hit_events"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []  # hit_events 테이블 부재 → 근거 없음(graceful)
        con.close()
    except Exception:
        return []
    for r in rows:
        events.append({
            "node_id": r[0], "speaker": r[1], "outcome": r[2], "domain": r[3],
            "subtype": r[4], "decision_id": r[5], "ts": r[6],
        })
    return events


# ---------------- 반복 판단 클러스터링(결정적) ----------------

def _cluster_repeated(nodes):
    """결정적 그리디 클러스터링 — 같은 subtype + 어휘 관련성 >= CLUSTER_REL_MIN 이면 같은 클러스터.

    id 사전순 정렬 후, 각 노드를 (같은 subtype AND 대표와 양방향 max 관련성 >= 임계)인 첫 클러스터에
    편입, 없으면 새 클러스터(대표=자기). LLM 0 · 동일 입력 동일 출력. '반복된 개별 판단' 검출.
    반환: [{'subtype':.., 'rep':node, 'members':[node,..]}, ..]
    """
    ordered = sorted(nodes, key=lambda n: n["id"])
    clusters = []
    for n in ordered:
        sub = n.get("semantic_subtype")
        sent = n.get("sentence") or ""
        placed = False
        for cl in clusters:
            if cl["subtype"] != sub:
                continue
            rep_sent = cl["rep"].get("sentence") or ""
            rel = max(
                RECALL._relevance(RECALL._tokens(sent), rep_sent),
                RECALL._relevance(RECALL._tokens(rep_sent), sent),
            )
            if rel >= CLUSTER_REL_MIN:
                cl["members"].append(n)
                placed = True
                break
        if not placed:
            clusters.append({"subtype": sub, "rep": n, "members": [n]})
    return clusters


# ---------------- hit_events 근거 집계(표시 전용) ----------------

def _hit_support(hit_events, node_ids, domain=None):
    """클러스터 node_ids 에 해당하는 owner hit_events 를 (decision_id,node_id,speaker) distinct
    dedupe 후 집계. domain 지정 시 domain==_domain_norm(domain) 이벤트만(WHERE 필터·가산 아님).

    반환 {distinct_decisions,hits,misses,dominant_outcome} — 표시용 evidence_summary 로만 쓰이고
    정렬 key 진입 금지(D-2 이중계상 방어 · Fable5 E 방어).
    """
    node_set = set(node_ids)
    dom = HIT._domain_norm(domain) if domain is not None else None
    seen = set()
    decisions = set()
    hits = misses = 0
    for ev in hit_events:
        if ev.get("node_id") not in node_set:
            continue
        if ev.get("speaker") != "owner":
            continue
        if dom is not None and ev.get("domain") != dom:
            continue
        key = (ev.get("decision_id"), ev.get("node_id"), ev.get("speaker"))
        if key in seen:
            continue  # D-2: 같은 (decision_id,node,speaker) 이중계상 방지
        seen.add(key)
        did = ev.get("decision_id")
        if did:
            decisions.add(did)
        if ev.get("outcome") == "hit":
            hits += 1
        elif ev.get("outcome") == "miss":
            misses += 1
    dominant = None
    if hits or misses:
        dominant = "hit" if hits >= misses else "miss"
    return {"distinct_decisions": len(decisions), "hits": hits, "misses": misses,
            "dominant_outcome": dominant}


# ---------------- 제안 문구(결정적 템플릿) ----------------

def _principle_text(cluster, support):
    """결정적 템플릿 문구(LLM 0). 대표 노드 = sentence 토큰 최다·동점 id 최소."""
    members = cluster["members"]
    rep = sorted(members, key=lambda n: (-len(RECALL._tokens(n.get("sentence") or "")), n["id"]))[0]
    sub = cluster["subtype"]
    cnt = len(members)
    excerpt = (rep.get("sentence") or "")[:60]
    if sub == "버그패턴":
        base = ('반복된 버그패턴 %d건 감지: "%s" 부류 — 이 상황에서 착수 전 점검을 기본 규칙 후보로.'
                % (cnt, excerpt))
    elif sub == "교훈":
        base = '반복된 교훈 %d건: "%s" 부류 — 원칙 규칙 후보.' % (cnt, excerpt)
    else:  # 선호
        base = '반복된 선호 %d건: "%s" 부류 — 기본값 규칙 후보.' % (cnt, excerpt)
    if support and (support.get("hits") or support.get("misses")):
        base += (" (owner 직감 근거 %d적중/%d빗나감, distinct 결정 %d건 — candidate·미검증)"
                 % (support.get("hits", 0), support.get("misses", 0),
                    support.get("distinct_decisions", 0)))
    return base


# ---------------- 메인: propose_abstractions ----------------

def propose_abstractions(ledger_path, domain=None, min_support=MIN_SUPPORT, home=None):
    """반복 판단 + hit_events 에서 candidate rule 을 '제안만' 한다(read-only · DB write 0).

    절차: nodes(ro) → clusters → evidence_refs(정렬·중복제거) → 근거 게이트(비면/미달 skip) →
          domain 파티션 필터 → content-hash proposal_id → dict 구성 → 구조적 카운트로 정렬.
    active 승격 0 · 신규 엣지 0 · 근거 없는 제안 0. 규칙화는 사람 SAVE 경로에서만(본 함수 밖).
    빈 그래프(노드 0) → [] graceful.
    """
    nodes = _load_nodes_ro(ledger_path)
    if not nodes:
        return []
    hit_events = _load_hit_events_ro(ledger_path)
    clusters = _cluster_repeated(nodes)

    proposals = []
    for cl in clusters:
        evidence_refs = sorted({n["id"] for n in cl["members"]})
        # 요구③ 근거 없는 제안 절대 금지.
        if len(evidence_refs) == 0:
            continue
        # 근거 미달(반복 3회 미만)은 추상화 후보 아님.
        if len(evidence_refs) < min_support:
            continue
        support = _hit_support(hit_events, evidence_refs, domain)
        # domain 지정 시 파티션 필터: 그 domain hit_events 에 하나도 안 걸리면 skip(domain=필터·가산 아님).
        if domain is not None and support["distinct_decisions"] == 0:
            continue
        sub = cl["subtype"]
        # D-1: proposal_id 는 node_id 가 아닌 sorted(evidence_refs)+subtype 의 content hash.
        pid = "abstraction:" + hashlib.sha256(
            json.dumps([evidence_refs, sub], ensure_ascii=False, sort_keys=True).encode("utf-8", "replace")
        ).hexdigest()[:16]
        proposals.append({
            "proposal_id": pid,
            "proposed_principle_text": _principle_text(cl, support),
            "evidence_refs": evidence_refs,
            "supporting_count": len(evidence_refs),
            "semantic_subtype": sub,
            "domain": HIT._domain_norm(domain),
            "evidence_summary": support,
            "source_kind": "repeated_judgment",
            "trust": "candidate_unverified",
            "promote": False,
            "requires_human_save": True,
            "save_note": ("규칙화는 사람 SAVE(기존 candidate confirm 경로)에서만 — "
                          "본 제안은 텍스트 표시/반환뿐(자동확정 0)"),
        })

    # 비인과 봉쇄: evidence_summary 가 '신호가 아님'을 코드로 증명(순수 int dict → 통과).
    # 만약 신호 dict(_SIGNAL_MARK) 가 섞였다면 여기서 TypeError → 정렬 진입 자체가 차단됨.
    for p in proposals:
        HIT.assert_not_ranking_input(p.get("evidence_summary"), where="abstraction_sort")
    # 정렬: 구조적 카운트(내림차순) + content-id(사전순)만. 적중률 신호 배제.
    proposals.sort(key=lambda p: (-p["supporting_count"], p["proposal_id"]))
    return proposals


# ---------------- 표시(결정적 마크다운) ----------------

def render_proposals_md(proposals):
    """제안 리스트 → 결정적 마크다운. '제안만'임을 문구로 명시(자동확정 0)."""
    lines = ["## 추상화 제안(candidate rule 후보 — 빙구팩은 제안만·자동확정 0)", ""]
    if not proposals:
        lines.append("_제안 없음(근거 %d건 이상 반복 판단 미검출)._" % MIN_SUPPORT)
        lines.append("")
    for p in proposals:
        lines.append(p["proposed_principle_text"])
        lines.append("- 근거(evidence_refs): " + ", ".join(p["evidence_refs"]))
        lines.append("- supporting_count: %d" % p["supporting_count"])
        lines.append("- trust: candidate_unverified")
        lines.append("")
    lines.append("규칙화하려면 사람이 SAVE 로 명시 승인해야 합니다"
                 "(빙구팩 규칙 자산 write 0·self-modifying 0).")
    return "\n".join(lines)


# ---------------- selftest (recall._selftest 패턴 · raw sqlite3 · mtime 가드) ----------------

def _selftest():
    import sqlite3 as _sq
    import tempfile
    import shutil
    from datetime import datetime, timezone

    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)
    from openbinggu_staging_write_selftest import OPERATING_PATHS
    from binggu_schema import apply_schema  # 정본 스키마(temp fixture 도 정본 상위집합)

    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if bool(ok) else "FAIL"))

    # ★ 운영 store mtime 가드(read-only 불변 증명) — 전/후 대조.
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_abstraction_")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # raw sqlite3 픽스처(기본 rollback journal — WAL 아님) → commit + close 후 mode=ro 조회 가시성 보장.
    def build_ledger(name, node_rows, hit_rows=None):
        path = os.path.join(tmp, name)
        con = _sq.connect(path)
        apply_schema(con)
        for nid, ntype, sent, sub in node_rows:
            con.execute(
                "INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
                "created_at,semantic_subtype,use_count) VALUES(?,?,?,?,?,?,?,?,?)",
                (nid, ntype, sent, 0, "active", "h", now, sub, 0))
        for hr in (hit_rows or []):
            con.execute(
                "INSERT INTO hit_events(node_id,speaker,kind,outcome,subtype,ts,domain,context_hash,decision_id) "
                "VALUES(?,?,?,?,?,?,?,?,?)", hr)
        con.commit()
        con.close()
        return path

    def count_rows(path):
        c = _sq.connect("file:%s?mode=ro" % path, uri=True)
        out = {}
        for t in ("nodes", "edges", "hit_events"):
            try:
                out[t] = c.execute("SELECT count(*) FROM %s" % t).fetchone()[0]
            except _sq.OperationalError:
                out[t] = 0
        c.close()
        return out

    try:
        # ── T1 반복 버그패턴 3건 → 제안 1·supporting_count 3·evidence_refs 정렬 ──
        led1 = build_ledger("t1.sqlite", [
            ("n:a1", "judgment", "검증 없이 바로 배포하면 실패한다 endpoint 확인 누락", "버그패턴"),
            ("n:a2", "judgment", "검증 없이 바로 배포하면 endpoint 응답 확인 누락으로 실패", "버그패턴"),
            ("n:a3", "judgment", "검증 없이 배포 endpoint 확인 누락 실패 반복", "버그패턴"),
        ])
        pr1 = propose_abstractions(led1)
        rec(1, "반복 버그패턴 3건 → 제안 1·supporting_count 3·evidence_refs 정렬",
            len(pr1) == 1 and pr1[0]["supporting_count"] == 3
            and pr1[0]["evidence_refs"] == ["n:a1", "n:a2", "n:a3"])

        # ── T2 근거 미달(2건 < min_support 3) → 제안 0 ──
        led2 = build_ledger("t2.sqlite", [
            ("n:b1", "judgment", "검증 없이 바로 배포하면 실패한다 endpoint 확인 누락", "버그패턴"),
            ("n:b2", "judgment", "검증 없이 바로 배포하면 endpoint 확인 누락 실패", "버그패턴"),
        ])
        pr2 = propose_abstractions(led2)
        rec(2, "근거 미달(2<3) → 제안 0", pr2 == [])

        # ── T3 근거 필수(요구③): 모든 제안 len(evidence_refs)>0 ──
        led3 = build_ledger("t3.sqlite", [
            ("n:c1", "judgment", "배포 전 로컬 selftest 와 live endpoint 를 확인한다", "교훈"),
            ("n:c2", "judgment", "배포 전 로컬 selftest 확인하고 live endpoint 를 본다", "교훈"),
            ("n:c3", "judgment", "배포 전 selftest 로컬 확인 live endpoint 점검", "교훈"),
            ("n:c4", "judgment", "배포 전 selftest 확인 live endpoint 반드시 점검한다", "교훈"),
        ])
        pr3 = propose_abstractions(led3)
        rec(3, "요구③ 근거 없는 제안 절대 0(모든 제안 evidence_refs>0)",
            len(pr3) >= 1 and all(len(p["evidence_refs"]) > 0 for p in pr3))

        # ── T4 read-only 불변(요구④): OPERATING_PATHS mtime + temp ledger row count 전후 동일 ──
        led4 = build_ledger("t4.sqlite", [
            ("n:d1", "judgment", "검증 없이 바로 배포하면 실패한다 endpoint 확인 누락", "버그패턴"),
            ("n:d2", "judgment", "검증 없이 바로 배포하면 endpoint 확인 누락 실패", "버그패턴"),
            ("n:d3", "judgment", "검증 없이 배포 endpoint 확인 누락 실패 반복", "버그패턴"),
        ], hit_rows=[
            ("n:d1", "owner", "직감", "hit", "버그패턴", now, "bid", None, "dec-d1"),
        ])
        cnt_before = count_rows(led4)
        _ = propose_abstractions(led4)
        _ = propose_abstractions(led4, domain="bid")
        cnt_after = count_rows(led4)
        rec(4, "read-only 불변: temp ledger nodes/edges/hit_events count 전후 동일(INSERT/UPDATE 0)",
            cnt_before == cnt_after)

        # ── T5 D-2 이중계상 방어: 같은 decision_id 2건 → distinct_decisions==1 ──
        led5 = build_ledger("t5.sqlite", [
            ("n:e1", "judgment", "검증 없이 바로 배포하면 실패한다 endpoint 확인 누락", "버그패턴"),
            ("n:e2", "judgment", "검증 없이 바로 배포하면 endpoint 확인 누락 실패", "버그패턴"),
            ("n:e3", "judgment", "검증 없이 배포 endpoint 확인 누락 실패 반복", "버그패턴"),
        ], hit_rows=[
            ("n:e1", "owner", "직감", "hit", "버그패턴", now, None, None, "dec-x"),
            ("n:e1", "owner", "직감", "hit", "버그패턴", now, None, None, "dec-x"),
        ])
        pr5 = propose_abstractions(led5)
        rec(5, "D-2 이중계상 방어: 같은 decision_id 2건 → distinct_decisions==1·hits==1",
            len(pr5) == 1 and pr5[0]["evidence_summary"]["distinct_decisions"] == 1
            and pr5[0]["evidence_summary"]["hits"] == 1)

        # ── T6 Fable5 E domain 파티션: bid/cook 분리 — domain 은 WHERE 필터(가산 아님) ──
        led6 = build_ledger("t6.sqlite", [
            ("n:f1", "judgment", "검증 없이 바로 배포하면 실패한다 endpoint 확인 누락", "버그패턴"),
            ("n:f2", "judgment", "검증 없이 바로 배포하면 endpoint 확인 누락 실패", "버그패턴"),
            ("n:f3", "judgment", "검증 없이 배포 endpoint 확인 누락 실패 반복", "버그패턴"),
        ], hit_rows=[
            ("n:f1", "owner", "직감", "hit", "버그패턴", now, "bid", None, "dec-b1"),
            ("n:f2", "owner", "직감", "hit", "버그패턴", now, "cook", None, "dec-c1"),
        ])
        pr6_bid = propose_abstractions(led6, domain="bid")
        pr6_cook = propose_abstractions(led6, domain="cook")
        pr6_all = propose_abstractions(led6)  # domain=None → 둘 다 집계
        rec(6, "Fable5 E domain 파티션: bid=1·cook=1 분리·전역=2(가산 아님·필터)",
            len(pr6_bid) == 1 and pr6_bid[0]["evidence_summary"]["distinct_decisions"] == 1
            and len(pr6_cook) == 1 and pr6_cook[0]["evidence_summary"]["distinct_decisions"] == 1
            and pr6_all[0]["evidence_summary"]["distinct_decisions"] == 2)

        # ── T7 신규 엣지/노드 0(요구⑥): edges count 불변 + generates_rule 등 write 흔적 0 ──
        blob7 = json.dumps(pr1, ensure_ascii=False)
        rec(7, "신규 엣지/노드 0: edges count 불변 + generates_rule/relation/edge write 흔적 0",
            cnt_before["edges"] == cnt_after["edges"] == 0
            and "generates_rule" not in blob7 and '"relation"' not in blob7
            and '"edge_id"' not in blob7)

        # ── T8 D-1 방어: proposal_id 는 'abstraction:' content hash 이고 어떤 node_id 와도 불일치 ──
        node_ids8 = {"n:a1", "n:a2", "n:a3"}
        rec(8, "D-1 방어: proposal_id='abstraction:' content hash·node_id 노출 아님",
            all(p["proposal_id"].startswith("abstraction:") for p in pr1)
            and all(p["proposal_id"] not in node_ids8 for p in pr1))

        # ── T9 비인과 봉쇄: evidence_summary 통과(신호 아님) + 신호 dict 은 차단 + 정렬=supporting_count ──
        led9 = build_ledger("t9.sqlite", [
            # 버그패턴 4건(클러스터 1)
            ("n:g1", "judgment", "검증 없이 바로 배포하면 실패한다 endpoint 확인 누락", "버그패턴"),
            ("n:g2", "judgment", "검증 없이 바로 배포하면 endpoint 확인 누락 실패", "버그패턴"),
            ("n:g3", "judgment", "검증 없이 배포 endpoint 확인 누락 실패 반복", "버그패턴"),
            ("n:g4", "judgment", "검증 없이 배포하면 endpoint 확인 누락으로 실패한다", "버그패턴"),
            # 교훈 3건(클러스터 2)
            ("n:h1", "judgment", "백업 먼저 하고 파괴작업 승인 받는다 대량 삭제", "교훈"),
            ("n:h2", "judgment", "백업 먼저 하고 파괴작업 대량 삭제 승인 받는다", "교훈"),
            ("n:h3", "judgment", "백업 먼저 파괴작업 대량 삭제 승인 필수", "교훈"),
        ])
        pr9 = propose_abstractions(led9)
        # evidence_summary 는 신호가 아니므로 통과해야 함(예외 0)
        summary_passes = True
        try:
            for p in pr9:
                HIT.assert_not_ranking_input(p["evidence_summary"], where="abstraction_sort")
        except TypeError:
            summary_passes = False
        # 실제 적중률 신호 dict 은 차단돼야 함
        signal_blocked = False
        try:
            HIT.assert_not_ranking_input(HIT._mark_signal({"rate": 0.9}), where="abstraction_sort")
        except TypeError:
            signal_blocked = True
        sorted_by_count = (len(pr9) == 2 and pr9[0]["supporting_count"] == 4
                           and pr9[1]["supporting_count"] == 3)
        rec(9, "비인과 봉쇄: evidence_summary 통과·신호 dict 차단·정렬=supporting_count",
            summary_passes and signal_blocked and sorted_by_count)

        # ── T10 promote/자동확정 0(요구⑤) + 빈 그래프 graceful ──
        promote_zero = all(
            (p["promote"] is False and p["requires_human_save"] is True and "active" not in p)
            for pr in (pr1, pr3, pr5, pr6_all, pr9) for p in pr)
        empty_led = os.path.join(tmp, "nonexistent.sqlite")
        pr_empty = propose_abstractions(empty_led)
        rec(10, "promote/자동확정 0(promote False·requires_human_save True·active 부재)+빈그래프 []",
            promote_zero and pr_empty == [])

        # ── T11 render 결정적 + 빈 리스트 graceful ──
        md = render_proposals_md(pr1)
        md_empty = render_proposals_md([])
        rec(11, "render_proposals_md 결정적(헤더·SAVE 안내)·빈 리스트 graceful",
            "추상화 제안" in md and "규칙화하려면 사람이 SAVE" in md
            and "제안 없음" in md_empty)

    finally:
        # 운영 store mtime 재측정(read-only 불변 증명).
        op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
        shutil.rmtree(tmp, ignore_errors=True)

    store_unchanged = op_before == op_after

    print("=" * 74)
    print("binggu_abstraction — 작업4 추상화 제안(read-only·제안만) selftest (temp DB·운영 write 0)")
    print("=" * 74)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print("%s %2d %s" % ("[OK]" if v == "PASS" else "[X]", cid, desc))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("operating_store_unchanged=%s  db_write=0  promote=0  evidence_required=1"
          % store_unchanged)
    gate = "GO" if (npass == len(results) and store_unchanged) else "NO-GO"
    print("GATE=%s" % gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("binggu_abstraction: --selftest 로 검증 실행")
