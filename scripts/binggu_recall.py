# -*- coding: utf-8 -*-
"""binggu_recall.py — 회상 API + 반문 엔진 (backward-compatible thin wrapper).

v1.16 strangler Phase2: 순수 transform impl(토큰화/관련성 _tokens/_relevance · _load_graph(ro) ·
why_search/judgment_trace/match_risk_patterns/preflight_context · embed 캐시 helper ·
_semantic_scorer/precompute_embeddings)은 binggupack.pack.recall 로 byte-identical 이관됐고,
이 파일은 공개 심볼이 동일한 thin wrapper 다. 기존 호출처(import binggu_recall — binggu.py ·
binggu_contrast_protocol(_tokens/_relevance) · binggu_rationale_suggest(_semantic_scorer lazy) ·
hooks/binggu_preflight_hook · tests/recall_consistency_harness)는 그대로 동작한다.

read-only 불변(ledger write 0)은 1바이트도 변하지 않았다 — _load_graph 는 mode=ro 로만 읽고,
캐시/임베드도 운영 ledger 와 분리된 별도 sqlite 다. 형제 의존은 정본 모듈에서 패키지 import 로
재배선됐다(p1_ranking→binggupack.pack · p1_config→binggupack.safety). 미이관 모듈
(binggu_rationale_suggest 상단 import · semantic 함수내부 lazy import)은 정본 모듈이 scripts/
sys.path 경유 bare-name 으로 해소한다(semantic 은 byte-identical 불가라 미접촉).

temp ledger selftest(OPERATING_PATHS 의존)와 __main__ 의 --precompute CLI 는 scripts/ sys.path
의존이라 이 wrapper 에 잔류한다.

CLI: python scripts/binggu_recall.py [--selftest] | --precompute
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.recall import *  # noqa: E402,F401,F403
from binggupack.pack.recall import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    RANK,
    CFG,
    _SUBTYPE_WHY,
    RISK_SUBTYPE_WEIGHT,
    RISK_SUBTYPES,
    JUDGMENT_KINDS,
    _tokens,
    _relevance,
    _load_graph,
    _SEMANTIC_FLOOR,
    _embed_cache_path,
    _open_embed_cache,
    _sent_sha,
    _pack_vec,
    _unpack_vec,
    precompute_embeddings,
    _semantic_scorer,
    why_search,
    judgment_trace,
    _risk_question,
    match_risk_patterns,
    _domain_from_cwd,
    preflight_context,
)


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

        # ── P2 의미(semantic) 회상: 어휘 미매칭 query 가 의미로 회상되는가 ──
        # 별도 temp ledger — 어휘가 전혀 겹치지 않는 동의 개념 쌍을 심는다.
        sem_ledger = os.path.join(tmp, "sem_ledger.sqlite")
        scon = sqlite3.connect(sem_ledger)
        scon.executescript(
            "CREATE TABLE nodes(node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT,"
            " candidate INT, state TEXT, content_hash TEXT, created_at TEXT,"
            " semantic_subtype TEXT, use_count INTEGER DEFAULT 0);"
            "CREATE TABLE evidence(evidence_id TEXT, sentence TEXT, source_pointer_id TEXT, source_hash TEXT);"
            "CREATE TABLE edges(edge_id TEXT, relation TEXT, source TEXT, target TEXT,"
            " candidate INT, state TEXT, evidence_refs TEXT);")
        # 버그패턴: query("프로세스 종료") 와 토큰이 전혀 겹치지 않지만 같은 개념.
        scon.execute(
            "INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
            "created_at,semantic_subtype,use_count) VALUES(?,?,?,?,?,?,?,?,?)",
            ("node:CONV:ff10", "judgment",
             "PID 만 죽이면 자식 워커가 좀비로 남아 충돌한다", 0, "active", "h", now, "버그패턴", 3))
        # 무관 노드(요리) — 의미상으로도 query 와 안 닮음.
        scon.execute(
            "INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
            "created_at,semantic_subtype,use_count) VALUES(?,?,?,?,?,?,?,?,?)",
            ("node:CONV:gg11", "judgment",
             "양파는 약불에 갈색이 날 때까지 볶는다", 0, "active", "h", now, "결정", 0))
        scon.commit()
        scon.close()

        # 결정적 fake embed(Ollama 비의존) — 개념 축으로 직교 벡터(토큰 무관·의미 매칭 모사).
        # "프로세스/종료/PID/죽이/좀비/워커/충돌" → 같은 축(프로세스-관리 개념).
        def _fake_embed(text, timeout=10):
            v = [0.0, 0.0, 0.0]
            proc_kw = ["프로세스", "종료", "pid", "죽이", "좀비", "워커", "충돌", "재시작", "kill"]
            cook_kw = ["양파", "볶", "약불", "갈색", "수프", "간을", "레시피", "요리"]
            t = text.lower()
            if any(w in t for w in proc_kw):
                v[0] = 1.0
            if any(w in t for w in cook_kw):
                v[1] = 1.0
            if sum(v) == 0:
                v[2] = 1.0  # 미매칭 개념은 직교 축(cos≈0)
            import math as _m
            n = _m.sqrt(sum(x * x for x in v)) or 1.0
            return [x / n for x in v]

        fake_scorer = _semantic_scorer(embed_fn=_fake_embed)
        ck(fake_scorer is not None, "semantic scorer(주입 embed) 생성")

        # query "프로세스 종료" — 노드 문장과 토큰 0 겹침. 어휘만으로는 회상 실패해야.
        ws_lex = why_search(sem_ledger, "프로세스 종료 방법")  # scorer 미주입(어휘만)
        lex_ids = [n["node_id"] for n in ws_lex["relevant_nodes"]]
        ck("node:CONV:ff10" not in lex_ids,
           "어휘 회상: '프로세스 종료' → '좀비 워커' 노드 미회상(토큰 0 겹침 — 기존 한계 확인)")

        # 같은 query 에 semantic scorer 주입 → 의미로 회상돼야(설계 §5 L4 목표).
        ws_sem = why_search(sem_ledger, "프로세스 종료 방법", scorer=fake_scorer)
        sem_ids = [n["node_id"] for n in ws_sem["relevant_nodes"]]
        ck("node:CONV:ff10" in sem_ids and "node:CONV:gg11" not in sem_ids,
           "의미 회상: '프로세스 종료' → '좀비 워커' 노드 회상 O · 무관 요리 X")
        ck(ws_sem["confidence"] > 0.0 and ws_sem["relevant_nodes"][0]["candidate"] is True,
           "의미 회상 결과도 candidate 표시(사람 확정 전 참고용 — 헌법)")

        # 반문도 의미 매칭으로 발동(어휘 0 겹침이라 scorer 없으면 위험 미감지).
        pf_lex = preflight_context(sem_ledger, prompt="서버 죽이고 다시 띄운다")
        pf_sem = preflight_context(sem_ledger, prompt="서버 죽이고 다시 띄운다", scorer=fake_scorer)
        # "서버 죽이고 다시 띄운다" → proc_kw('죽이','재시작') 축 → ff10(버그패턴)와 의미 매칭.
        ck(len(pf_lex["avoid_patterns"]) == 0 and len(pf_sem["avoid_patterns"]) >= 1,
           "의미 반문: 어휘 미매칭 작업도 semantic scorer 면 버그패턴 매칭(avoid_patterns)")

        # scorer 부재(opt-in OFF 기본) → why_search 가 어휘로 graceful 동작(에러 0).
        ws_off = why_search(sem_ledger, "양파 볶기")  # _semantic_scorer() 기본 OFF → None
        ck(isinstance(ws_off["relevant_nodes"], list),
           "opt-in OFF(기본) → scorer None · 어휘 회상으로 graceful(에러 0)")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck(op_before == op_after, "운영 store 불변(ledger write 0 · read-only)")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] and sys.argv[1] == "--precompute":
        import json as _json
        import binggu_platform as _plat
        _r = precompute_embeddings(_plat.default_ledger())
        print(_json.dumps(_r, ensure_ascii=False, indent=2))
        sys.exit(0 if _r.get("status") in ("OK", "SKIP") else 1)
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    print("usage: binggu_recall.py [--selftest]")
    sys.exit(2)
