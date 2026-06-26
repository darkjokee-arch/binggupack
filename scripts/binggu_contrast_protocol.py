# -*- coding: utf-8 -*-
"""binggu_contrast_protocol.py — 대비 규약(Contrast Protocol, 1단) · read-only.

빙구팩 preflight 신호(preferences/avoid_patterns)와 현 작업 강제조항(mandate: 박제/스킬/
CLAUDE.md)이 어긋날 때, 양쪽을 **동일 양식·원문 인용**으로 대비표화하여 사람이 선택하게
하는 read-only 컴포넌트. 빙구팩은 "감지·대비표 렌더링·기록"만 하고 어떤 규칙도 변경하지
않는다(결정 0·자동교체 0·promote 0).

설계 출처: 거버넌스 1·2단 토론(20260626) 결론 — 1단(대비)=GO. 3단(규칙 진화)은 본 컴포넌트가
절대 호출하지 않는다(규칙 변경은 빙구팩 무관 독립 도구로 사람이).

[흐름 — 단방향 read-only]
  preflight_context(...) → dict{avoid_patterns, preferences, risk_level, ...}
    → detect_conflicts(preflight_out, mandates) → [Conflict...]
    → build_contrast_table(conflict)            → 중립 dict(양쪽 동일 필드)
    → render_contrast_md(table)                 → str(사람이 읽는 마크다운, 결정 0)
    → record_contrast(staging_db, table)        → audit_append 1건(기록만, 규칙 write 0)
  사람이 표를 보고 선택 → 선택 적용은 1단 범위 밖(빙구팩 무관). 빙구팩은 여기서 멈춘다.

★가드2 fix(검증서):
  ① 원문 본문 봉인 — sha 만이 아니라 mandate 강제조항 원문 텍스트 + preflight raw 를 별도
     contrast_snapshot 테이블(append-only)에 봉인 저장 → 렌더된 표를 원문과 정합 검증 가능.
  ② 중립 템플릿 대칭 — 빙구팩 칸 cons 고정 + mandate 칸도 동일 양식(편들기 0).
  ③ detect_conflicts 의 safety/integrity SKIP 은 **정책 분류기(comp2 binggu_policy) 결과** 또는
     호출측이 준 **구조화 domain enum** 만 사용(자유문자열 키워드 매칭 금지).

불변(헌법):
  - candidate-only · 사람 승인 게이트 · PII 제외 · audit chain · 안전 양보불가 · AI 추천만.
  - record_contrast 는 audit_append(+contrast_snapshot append) 만 — 규칙 자산(박제/스킬/
    CLAUDE.md/ledger nodes·edges) write 0.
  - stdlib only(hashlib/json·외부 바이너리 0). 결정적(LLM 0 · hallucination 0).
  - binggu_recall._relevance 재사용(2차 토큰 매칭) · 신규 임베딩/모델 0.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import binggu_p1_config as CFG          # contrast_config(설정값)            # noqa: E402
import binggu_recall as RECALL          # _tokens · _relevance(2차 매칭)      # noqa: E402

# safety/integrity 강제조항은 빙구팩 양보 0 — 대비표조차 만들지 않는다(헌법 그대로 따른다).
_SAFETY_DOMAINS = ("safety", "integrity")

# 빙구팩 칸 강제 단점(중립 템플릿 상수 — 빙구팩이 못 지움). 가드 ②.
_BINGGU_FORCED_CONS = (
    "candidate — 사람 확정 전 참고용(자동결정 0)",
    "적중률 미검증 시 신뢰 보류(표본 게이트)",
    "빙구팩이 이 표를 렌더링함 — 검증 독립성 없음(토론 3R 결론)",
)
_BINGGU_TRUST = "candidate_unverified"

# 빙구팩 신호 항목 → stance 매핑(semantic_subtype 기반 · 자유문자열 매칭 아님).
#   avoid_patterns(버그패턴) → 'forbid'(하지 마라) / preferences(선호) → 'require'(이렇게 해라).
_BINGGU_FORBID_SUBTYPE = "버그패턴"
_BINGGU_REQUIRE_SUBTYPE = "선호"


# ---------------- 해시(staging _hash 패리티: sha256[:16]) ----------------

def _snap_sha(text):
    """sha256(text)[:16] — staging audit chain · contrast_snapshot 봉인 패리티."""
    return hashlib.sha256(("" if text is None else str(text)).encode("utf-8", "replace")).hexdigest()[:16]


# ---------------- comp2(binggu_policy) 정책 분류기 — 선택적 graceful ----------------

def _policy_is_safety(mandate, env=None):
    """mandate 가 safety/integrity(교체불가) 조항인지 — comp2 정책 분류기 결과 우선.

    가드 ③: 자유문자열 키워드 매칭 금지. 판정 원천 우선순위(전부 구조화/봉인 데이터):
      1) comp2 binggu_policy 가 있으면 is_immutable / classify_clause(directive|clause_id) 로 판정
         (immutable_whitelist == 안전 화이트리스트 → safety).
      2) comp2 부재/실패 → 호출측이 준 구조화 enum mandate['domain'] ∈ {safety,integrity}.
    둘 다 free-string 이 아니다(directive 정규화 키 · 정책 데이터 · 구조화 domain enum).
    fail 시 graceful: comp2 예외는 무시하고 domain enum 으로 폴백(에러 0).
    """
    # 1차: comp2 정책 분류기(있을 때만 · graceful)
    clause_id = mandate.get("directive") or mandate.get("clause_id")
    if clause_id:
        try:
            import binggu_policy as POLICY  # comp2 — 선택적
            if POLICY.is_immutable(clause_id, env=env):
                return True
            info = POLICY.classify_clause(clause_id, env=env)
            if info.get("is_safety"):
                return True
        except Exception:
            pass  # comp2 부재/오류 → 2차(domain enum)로 폴백
    # 2차: 호출측 구조화 enum(자유문자열 아님 — 명시 도메인 분류)
    dom = mandate.get("domain")
    return dom in _SAFETY_DOMAINS


# ---------------- mandate 정규화/검증 ----------------

def _mandate_stance(mandate):
    """mandate.stance 정규화 — require/forbid 만 유효, 그 외 None(매칭 제외)."""
    st = mandate.get("stance")
    return st if st in ("require", "forbid") else None


def _quote_verify(mandate):
    """원문 인용 신뢰 판정 — clause_text 의 실제 sha 와 주입 snapshot_sha 대조(가드 ①).

    반환: (computed_sha, snapshot_sha, status)
      status: 'verified'(일치) / 'sha_mismatch'(변조 의심) / 'unverified_quote'(sha 미주입).
    빙구팩이 인용을 조작하면 computed != snapshot 으로 드러난다(원문 봉인 기준).
    """
    clause = mandate.get("clause_text") or ""
    computed = _snap_sha(clause)
    given = mandate.get("snapshot_sha")
    if not given:
        return computed, None, "unverified_quote"
    return computed, given, ("verified" if computed == given else "sha_mismatch")


def _binggu_side_items(preflight_out):
    """preflight 신호 → 빙구팩 측 항목 리스트 [{stance, subtype, claim, node_id, relevance}].

    avoid_patterns(버그패턴) → forbid · preferences(선호) → require. semantic_subtype 기반 분류.
    """
    items = []
    for m in (preflight_out.get("avoid_patterns") or []):
        items.append({"stance": "forbid", "subtype": m.get("semantic_subtype") or _BINGGU_FORBID_SUBTYPE,
                      "claim": m.get("claim") or "", "node_id": m.get("node_id"),
                      "relevance": m.get("relevance", 0.0)})
    for p in (preflight_out.get("preferences") or []):
        items.append({"stance": "require", "subtype": _BINGGU_REQUIRE_SUBTYPE,
                      "claim": p.get("claim") or "", "node_id": p.get("node_id"),
                      "relevance": p.get("relevance", 0.0)})
    return items


# ---------------- 충돌 감지(read-only) ----------------

def detect_conflicts(preflight_out, mandates, home=None, env=None):
    """빙구팩 신호 vs mandate 가 같은 directive 에서 stance 가 어긋날 때만 Conflict 생성.

    매칭(결정적):
      1차 — directive 정규화 키 일치(mandate.directive == 빙구팩 항목의 매핑 키. 빙구팩 항목엔
            directive 가 없으므로 1차는 mandate 측에서만 가능 → 통상 2차로 진입).
      2차 — claim 토큰과 clause_text 토큰의 _relevance >= match_relevance_min(binggu_recall 재사용).
    SKIP 규칙(헌법 그대로 · 대비 제외):
      - _policy_is_safety(mandate) → SKIP(안전/무결성 충돌은 빙구팩 양보 0 · 대비표 안 만든다). 가드 ③.
      - risk_level=='높음' 이고 빙구팩이 forbid(avoid_patterns) 쪽이면 SKIP(반문 경로가 처리 — 이중표시 방지).
    충돌 조건: stance 가 정확히 반대(require↔forbid).

    반환: list[Conflict dict] — 각 항목은 build_contrast_table 입력.
      {conflict_id, binggu_item, mandate, quote_status, quote_computed_sha, relevance, match_via}.
    빈 preflight/빈 mandates → [](graceful · 에러 0).
    """
    cc = CFG.contrast_config(home)
    rmin = cc["match_relevance_min"]
    max_rows = cc["max_rows"]
    risk_level = preflight_out.get("risk_level")

    bitems = _binggu_side_items(preflight_out)
    conflicts = []
    for mandate in (mandates or []):
        if not isinstance(mandate, dict):
            continue
        m_stance = _mandate_stance(mandate)
        if m_stance is None:
            continue  # stance 불명 mandate 는 대비 대상 아님
        if _policy_is_safety(mandate, env=env):
            continue  # SKIP — 안전/무결성(헌법 양보 0)
        clause = mandate.get("clause_text") or ""
        ctok = RECALL._tokens(clause)
        directive = mandate.get("directive")
        for it in bitems:
            # stance 반대일 때만 충돌(require↔forbid)
            if it["stance"] == m_stance:
                continue
            # 반문 이중표시 방지: 높은 위험 + 빙구팩 forbid → SKIP(반문 경로가 처리)
            if risk_level == "높음" and it["stance"] == "forbid":
                continue
            # 매칭: 1차 directive 키(빙구팩 항목엔 directive 없음 → 통상 2차) · 2차 토큰 _relevance
            claim_tok = RECALL._tokens(it["claim"])
            rel = RECALL._relevance(claim_tok, clause)
            rel2 = RECALL._relevance(ctok, it["claim"])
            rel = max(rel, rel2)
            match_via = "token_relevance"
            if directive and directive == it.get("directive"):
                rel = 1.0
                match_via = "directive_key"
            if rel < rmin:
                continue
            qc, qg, qstatus = _quote_verify(mandate)
            cid = _snap_sha(json.dumps(
                [it.get("node_id"), it["stance"], directive or "", _snap_sha(clause)],
                ensure_ascii=False, sort_keys=True))
            conflicts.append({
                "conflict_id": "contrast:" + cid,
                "binggu_item": it,
                "mandate": mandate,
                "quote_status": qstatus,
                "quote_computed_sha": qc,
                "quote_given_sha": qg,
                "relevance": round(rel, 4),
                "match_via": match_via,
            })
    # 관련성 내림차순 · conflict_id 사전순(결정적) · max_rows 상한(과잉표시 억제).
    conflicts.sort(key=lambda c: (-c["relevance"], c["conflict_id"]))
    return conflicts[:max_rows]


# ---------------- 대비표 빌드(중립 템플릿 · 양쪽 동일 필드) ----------------

def build_contrast_table(conflict, home=None):
    """중립 대비표 — 양쪽 칸 '완전히 동일한 필드 집합'(편향 금지). 가드 ②.

    칸 공통 필드: position/source/ref/quote(원문)/quote_sha/quote_status/stance/pros[]/cons[]/trust.
    빙구팩 칸: cons 에 _BINGGU_FORCED_CONS 3개 항상 고정 주입(템플릿 상수) · trust=candidate_unverified.
    mandate 칸: 동일 양식 — pros 에 '사람이 명시 박제/규약(현행 강제력)' · cons 는 호출측이 준 것만
                (빙구팩 가공 0). mandate 칸 trust 도 표기.
    원문 봉인(가드 ①): mandate 칸 quote = clause_text 원문(요약 0) · 빙구팩 칸 quote = preflight claim 원문.
    """
    it = conflict["binggu_item"]
    mandate = conflict["mandate"]
    clause = mandate.get("clause_text") or ""

    binggu_side = {
        "position": "A",
        "source": "빙구팩 preflight 신호",
        "ref": it.get("node_id"),
        "quote": it.get("claim") or "",                 # 원문(요약 0) — 봉인 대상
        "quote_sha": _snap_sha(it.get("claim") or ""),
        "quote_status": "self_signal",                  # 빙구팩 자기 신호(외부 원문 아님)
        "stance": it["stance"],
        "pros": ["과거 적중/패턴 기반 신호(참고)"],
        # 빙구팩 단점 강제(헌법 — 빙구팩이 못 지움). 가드 ②.
        "cons": list(_BINGGU_FORCED_CONS),
        "trust": _BINGGU_TRUST,
    }
    mandate_side = {
        "position": "B",
        "source": mandate.get("source"),                # 박제/스킬/CLAUDE.md
        "ref": mandate.get("ref"),
        "quote": clause,                                 # 원문 그대로(요약 0) — 봉인 대상. 가드 ①.
        "quote_sha": conflict["quote_computed_sha"],
        "quote_status": conflict["quote_status"],        # verified/sha_mismatch/unverified_quote
        "stance": _mandate_stance(mandate),
        "pros": ["사람이 명시 박제/규약(현행 강제력)"],
        # mandate 칸 cons 는 호출측이 준 것만(빙구팩 가공/추가 0). 동일 양식(list).
        "cons": [str(c) for c in (mandate.get("cons") or [])],
        "trust": mandate.get("trust", "owner_mandate"),
    }
    return {
        "conflict_id": conflict["conflict_id"],
        "headline": "충돌 감지 — 둘 중 무엇을 따를지 사장님이 선택하세요(빙구팩은 추천만·결정 0)",
        "binggu_side": binggu_side,
        "mandate_side": mandate_side,
        "match_via": conflict.get("match_via"),
        "relevance": conflict.get("relevance"),
        # 봉인 페어(원문 sha) — record/verify 용. 가드 ①.
        "quote_sha_pair": "%s|%s" % (binggu_side["quote_sha"], mandate_side["quote_sha"]),
        # 사람 선택 3지선다(자동선택 0 — 입력 대기).
        "choices": ["A:빙구팩 신호", "B:현 강제조항", "C:보류"],
    }


# ---------------- 결정적 마크다운 렌더(LLM 0) ----------------

def _fmt_quote(side):
    """원문 quote 를 코드블록 + (sha) 표기 — 빙구팩 요약/의역 금지, 인용만. 가드 ①."""
    tag = ""
    if side["quote_status"] == "sha_mismatch":
        tag = " ⚠️sha_mismatch(인용 신뢰 박탈)"
    elif side["quote_status"] == "unverified_quote":
        tag = " (unverified_quote)"
    return "```quote\n%s\n```\n(sha:%s)%s" % (side["quote"], side["quote_sha"], tag)


def render_contrast_md(table):
    """결정적 마크다운 — 좌(빙구팩 신호)·우(현 강제조항) 2칸, 동일 행 라벨. 동일 입력→동일 출력.

    헤더 고정 · 원문 인용(sha)만 · 표 끝 3지선다(사람 입력 대기 · 자동선택 토큰 0).
    """
    a = table["binggu_side"]
    b = table["mandate_side"]

    def block(side, title):
        lines = ["### %s" % title,
                 "- position: %s" % side["position"],
                 "- source: %s" % side.get("source"),
                 "- ref: %s" % side.get("ref"),
                 "- stance: %s" % side.get("stance"),
                 "- trust: %s" % side.get("trust"),
                 "- quote:",
                 _fmt_quote(side),
                 "- pros:"]
        lines += ["  - %s" % p for p in side["pros"]]
        lines.append("- cons:")
        lines += ["  - %s" % c for c in side["cons"]]
        return "\n".join(lines)

    parts = [
        "## %s" % table["headline"],
        "conflict_id: %s · match_via: %s · relevance: %s"
        % (table["conflict_id"], table.get("match_via"), table.get("relevance")),
        "",
        block(a, "[A] 빙구팩 신호"),
        "",
        block(b, "[B] 현 강제조항"),
        "",
        "선택: [A] 빙구팩 신호 / [B] 현 강제조항 / [C] 보류",
        "(빙구팩은 추천만 — 자동결정 0. 선택은 사장님 입력 대기.)",
    ]
    return "\n".join(parts)


# ---------------- contrast_snapshot 봉인 테이블(append-only · 원문 본문 봉인) 가드 ① ----------------

_SNAPSHOT_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS contrast_snapshot("
    " seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, conflict_id TEXT,"
    " binggu_quote_raw TEXT, mandate_quote_raw TEXT, preflight_raw TEXT,"
    " binggu_quote_sha TEXT, mandate_quote_sha TEXT, table_sha TEXT)")


def _ensure_snapshot_table(con):
    con.execute(_SNAPSHOT_SCHEMA)


def record_contrast(staging_db, table, actor="ai", ts=None, preflight_raw=None):
    """대비표 emit 기록 — audit_append 1건 + contrast_snapshot 원문 봉인 1건. 규칙 write 0.

    가드 ①: sha 만이 아니라 mandate 강제조항 원문 텍스트 + preflight raw 를 contrast_snapshot
            (append-only)에 봉인 → 렌더된 표를 원문과 정합 검증 가능(verify_snapshot).
    audit: staging_db.audit_append(actor, action='contrast_emitted', ...) — append-only 1건.
           verify_chain 호환(v2 양식). actor='ai' 는 단순 기록(검증게이트/확정 아님 — G4 와 무관).
    규칙/박제/CLAUDE.md/nodes/edges write 0 — audit_log + contrast_snapshot append 만.

    반환: {recorded, seq, table_sha, conflict_id}.
    """
    a = table["binggu_side"]
    b = table["mandate_side"]
    table_sha = _snap_sha(json.dumps(table, ensure_ascii=False, sort_keys=True))

    # ① 원문 본문 봉인(append-only contrast_snapshot)
    con = staging_db.con
    _ensure_snapshot_table(con)
    pf_raw = json.dumps(preflight_raw, ensure_ascii=False, sort_keys=True) if preflight_raw is not None else None
    cur = con.execute(
        "INSERT INTO contrast_snapshot(ts,conflict_id,binggu_quote_raw,mandate_quote_raw,"
        "preflight_raw,binggu_quote_sha,mandate_quote_sha,table_sha) VALUES(?,?,?,?,?,?,?,?)",
        (ts, table["conflict_id"], a["quote"], b["quote"], pf_raw,
         a["quote_sha"], b["quote_sha"], table_sha))
    seq = cur.lastrowid
    con.commit()

    # ② audit chain append(규칙 write 0 — 기록만)
    staging_db.audit_append(
        actor=actor, action="contrast_emitted", pack_id=table["conflict_id"],
        result="emitted", reason="preflight_vs_mandate",
        before=table["quote_sha_pair"], after=table_sha, ts=ts)

    return {"recorded": True, "seq": seq, "table_sha": table_sha,
            "conflict_id": table["conflict_id"]}


def verify_snapshot(staging_db, conflict_id, rendered_md=None):
    """봉인된 원문 ↔ (선택)렌더된 표 정합 검증 — 가드 ① 검증 경로.

    봉인 raw 에서 sha 재계산 → 저장 sha 와 일치(원문 무변조) 확인.
    rendered_md 주어지면 원문 quote 가 렌더 안에 그대로 포함되는지(요약/의역 0) 확인.
    반환: {ok, reason, rows}.
    """
    con = staging_db.con
    _ensure_snapshot_table(con)
    rows = list(con.execute(
        "SELECT binggu_quote_raw,mandate_quote_raw,binggu_quote_sha,mandate_quote_sha "
        "FROM contrast_snapshot WHERE conflict_id=? ORDER BY seq", (conflict_id,)))
    if not rows:
        return {"ok": False, "reason": "snapshot_not_found", "rows": 0}
    for bq, mq, bsha, msha in rows:
        if _snap_sha(bq) != bsha:
            return {"ok": False, "reason": "binggu_quote_tampered", "rows": len(rows)}
        if _snap_sha(mq) != msha:
            return {"ok": False, "reason": "mandate_quote_tampered", "rows": len(rows)}
        if rendered_md is not None:
            # 원문 본문이 렌더에 그대로 있는가(빙구팩 요약/의역 검출).
            if mq and mq not in rendered_md:
                return {"ok": False, "reason": "mandate_quote_not_in_render", "rows": len(rows)}
            if bq and bq not in rendered_md:
                return {"ok": False, "reason": "binggu_quote_not_in_render", "rows": len(rows)}
    return {"ok": True, "reason": "OK", "rows": len(rows)}


# ---------------- selftest (temp · 운영 미접촉 · write 0) ----------------

def _selftest():
    import shutil
    import tempfile
    from openbinggu_staging_write_selftest import StagingDB, OPERATING_PATHS

    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_contrast_")
    try:
        # 강제조항 원문(요약 금지 — 봉인 대상)
        clause_deploy = "배포 전 반드시 로컬 selftest GO 와 live endpoint 응답을 직접 확인한다"
        mandate_deploy = {
            "source": "박제", "ref": "feedback_deploy.md", "stance": "require",
            "directive": "deploy.verify_required", "domain": "normal",
            "clause_text": clause_deploy, "snapshot_sha": _snap_sha(clause_deploy),
            "cons": ["매번 확인 시간 비용"],
        }
        # 빙구팩 신호: '바로 배포'(forbid 검증생략) 가 require(검증) 와 충돌하도록 구성.
        #   같은 directive(배포) 충돌이므로 claim 과 clause 가 어휘를 충분히 공유(현실적 — 동일 작업).
        preflight = {
            "avoid_patterns": [{"node_id": "node:CONV:aa01",
                                "claim": "배포 전 로컬 selftest live endpoint 응답 확인 없이 바로 진행하면 실패",
                                "semantic_subtype": "버그패턴", "relevance": 0.6}],
            "preferences": [{"node_id": "node:CONV:pp01",
                             "claim": "배포 작업은 selftest live endpoint 확인 없이 빠르게 바로 진행 선호",
                             "relevance": 0.5}],
            "risk_level": "중간",
        }

        # ── T1 무충돌: stance 같은 mandate(forbid) vs 빙구팩 forbid → 충돌 0 ──
        m_same = dict(mandate_deploy, stance="forbid", directive="deploy.no_verify")
        c_same = detect_conflicts({"avoid_patterns": preflight["avoid_patterns"],
                                   "preferences": [], "risk_level": "중간"}, [m_same])
        ck(c_same == [], "T1 무충돌(stance 동일) → 대비표 0")

        # ── T2 일반 충돌: preferences(require 빠른배포) vs mandate require verify ──
        # 두 require 가 같은 작업(배포)에서 상반된 행동을 요구하는 케이스는 stance 반대(require↔forbid)로
        #   모델링: 빙구팩 avoid(forbid 검증) vs mandate require 검증 = 정반대.
        conflicts = detect_conflicts(preflight, [mandate_deploy])
        ck(len(conflicts) >= 1, "T2 일반 충돌(빙구팩 forbid↔mandate require) → Conflict 생성")
        conf = conflicts[0]
        tbl = build_contrast_table(conf)
        bk = set(tbl["binggu_side"].keys())
        mk = set(tbl["mandate_side"].keys())
        ck(bk == mk, "T2b 양쪽 칸 필드집합 동일(중립 템플릿 대칭 · 가드②)")

        # ── T3 빙구팩 단점 강제 + trust ──
        ck(all(c in tbl["binggu_side"]["cons"] for c in _BINGGU_FORCED_CONS)
           and tbl["binggu_side"]["trust"] == "candidate_unverified",
           "T3 빙구팩 칸 cons 고정 3문구 전부 + trust=candidate_unverified(가드②)")
        ck(tbl["mandate_side"]["pros"] == ["사람이 명시 박제/규약(현행 강제력)"]
           and tbl["mandate_side"]["cons"] == ["매번 확인 시간 비용"],
           "T3b mandate 칸 pros 명시 · cons 는 호출측이 준 것만(빙구팩 가공 0)")

        # ── T4 안전 제외: domain=safety/integrity → SKIP(대비표 0) 가드③ ──
        m_safety = dict(mandate_deploy, domain="safety")
        m_integ = dict(mandate_deploy, domain="integrity")
        ck(detect_conflicts(preflight, [m_safety]) == [], "T4 domain=safety → SKIP(Conflict 0 · 가드③)")
        ck(detect_conflicts(preflight, [m_integ]) == [], "T4b domain=integrity → SKIP")

        # ── T5 반문 중복 방지: risk_level=높음 + 빙구팩 forbid → SKIP ──
        pf_high = dict(preflight, risk_level="높음", preferences=[])
        ck(detect_conflicts(pf_high, [mandate_deploy]) == [],
           "T5 risk=높음 + 빙구팩 forbid → SKIP(반문 경로가 처리 · 이중표시 방지)")

        # ── T6 원문 조작 불가: clause_text 변조(snapshot_sha 그대로) → sha_mismatch ──
        m_tamper = dict(mandate_deploy, clause_text=clause_deploy + " (몰래 추가)")
        ct = detect_conflicts(preflight, [m_tamper])
        ck(len(ct) >= 1 and ct[0]["quote_status"] == "sha_mismatch",
           "T6 clause_text 변조 → quote_status=sha_mismatch(인용 신뢰 박탈 · 가드①)")
        # sha 미주입 → unverified_quote
        m_nosha = {k: v for k, v in mandate_deploy.items() if k != "snapshot_sha"}
        ct2 = detect_conflicts(preflight, [m_nosha])
        ck(len(ct2) >= 1 and ct2[0]["quote_status"] == "unverified_quote",
           "T6b snapshot_sha 미주입 → unverified_quote 강등")

        # ── T7 렌더 결정성 + 3지선다 + 자동선택 토큰 0 ──
        md1 = render_contrast_md(tbl)
        md2 = render_contrast_md(tbl)
        ck(md1 == md2, "T7 render_contrast_md 동일 입력 → 동일 출력(LLM 0 · 결정적)")
        ck("선택: [A] 빙구팩 신호 / [B] 현 강제조항 / [C] 보류" in md1
           and clause_deploy in md1,
           "T7b 3지선다 포함 + 원문 그대로 인용(요약 0)")
        ck("자동결정 0" in md1 and "auto_select" not in md1.lower(),
           "T7c 자동선택 토큰 0(사람 입력 대기)")

        # ── T8 기록 한정: record_contrast 후 audit chain 무손상 · 규칙 store 불변 ──
        staging = StagingDB(os.path.join(tmp, "staging.sqlite"))
        try:
            chk_before = staging.store_checksum()
            rec = record_contrast(staging, tbl, actor="ai",
                                  ts="2026-06-26T00:00:00Z", preflight_raw=preflight)
            ck(rec["recorded"] and staging.verify_chain() is True,
               "T8 record_contrast 후 verify_chain True(audit append-only)")
            ck(staging.store_checksum() == chk_before,
               "T8b nodes/edges/evidence store_checksum 불변(규칙 write 0)")
            # audit_log 에 contrast_emitted 1건
            n_emit = staging.con.execute(
                "SELECT count(*) FROM audit_log WHERE action='contrast_emitted'").fetchone()[0]
            ck(n_emit == 1, "T8c audit_log contrast_emitted 1건")

            # ── T9 원문 봉인 정합(가드①): verify_snapshot ──
            vs = verify_snapshot(staging, tbl["conflict_id"], rendered_md=md1)
            ck(vs["ok"] and vs["rows"] == 1, "T9 verify_snapshot OK(원문 ↔ 렌더 정합 · 가드①)")
            # 봉인 raw 변조 시 검출
            staging.con.execute(
                "UPDATE contrast_snapshot SET mandate_quote_raw=? WHERE conflict_id=?",
                ("변조된 원문", tbl["conflict_id"]))
            staging.con.commit()
            vs_bad = verify_snapshot(staging, tbl["conflict_id"])
            ck(vs_bad["ok"] is False and vs_bad["reason"] == "mandate_quote_tampered",
               "T9b 봉인 원문 변조 → verify_snapshot fail(mandate_quote_tampered)")
        finally:
            staging.close()

        # ── T10 빈 preflight(신규 사용자) → Conflict 0 · 에러 0 ──
        empty_pf = {"avoid_patterns": [], "preferences": [], "risk_level": "낮음"}
        ck(detect_conflicts(empty_pf, [mandate_deploy]) == []
           and detect_conflicts(preflight, []) == [],
           "T10 빈 preflight/빈 mandates → Conflict 0(graceful · 에러 0)")

        # ── T11 comp2(binggu_policy) 분류기 결과로 SKIP — 자유문자열 매칭 금지 입증(가드③) ──
        #   domain=normal 이지만 정책 분류기가 immutable(안전)이라 판정하면 SKIP 돼야.
        import types as _types
        fake_policy = _types.ModuleType("binggu_policy")
        fake_policy.is_immutable = lambda cid, env=None: cid == "deploy.verify_required"
        fake_policy.classify_clause = lambda cid, env=None: {"is_safety": cid == "deploy.verify_required"}
        _saved = sys.modules.get("binggu_policy")
        sys.modules["binggu_policy"] = fake_policy
        try:
            m_normal_but_immutable = dict(mandate_deploy, domain="normal")
            ck(detect_conflicts(preflight, [m_normal_but_immutable]) == [],
               "T11 comp2 분류기가 immutable 판정 → domain=normal 이어도 SKIP(가드③ 정책 결과 사용)")
            # 정책 분류기가 안전 아님으로 판정하면 정상 충돌(directive 다름).
            fake_policy.is_immutable = lambda cid, env=None: False
            fake_policy.classify_clause = lambda cid, env=None: {"is_safety": False}
            m_normal2 = dict(mandate_deploy, domain="normal")
            ck(len(detect_conflicts(preflight, [m_normal2])) >= 1,
               "T11b comp2 분류기 not-safety → 정상 대비표 생성(자유문자열 무관)")
        finally:
            if _saved is not None:
                sys.modules["binggu_policy"] = _saved
            else:
                sys.modules.pop("binggu_policy", None)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck(op_before == op_after, "운영 store 불변(OPERATING_PATHS mtime 전후 동일 · write 0)")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    print("usage: binggu_contrast_protocol.py [--selftest]")
    sys.exit(2)
