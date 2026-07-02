# -*- coding: utf-8 -*-
"""binggu_contrast_protocol.py — 대비 규약(Contrast Protocol, 1단) · read-only
(backward-compatible thin wrapper).

v1.16 strangler Phase2: 순수 transform impl(detect_conflicts/build_contrast_table/
render_contrast_md/record_contrast/verify_snapshot + 봉인/판정 helper·상수)은
binggupack.safety.contrast_protocol 로 이관됐고, 이 파일은 공개 심볼이 동일한 thin
wrapper 다. 기존 호출처(import binggu_contrast_protocol)는 그대로 동작한다.

형제 의존(binggu_p1_config·binggu_recall)은 정본 모듈에서 패키지 import 로 재배선됐고
(p1_config→binggupack.safety · recall→binggupack.pack), comp2(binggu_policy 선택적 lazy)는
scripts/ sys.path 경유 bare-name 으로 유지된다. read-only 불변(규칙 자산 write 0 · audit +
contrast_snapshot append 만)은 1바이트도 변하지 않았다.

temp staging selftest(openbinggu_staging_write_selftest scripts sibling · OPERATING_PATHS
의존)는 scripts/ sys.path 의존이라 이 wrapper 에 잔류한다(recall 선례 동일).

CLI: python scripts/binggu_contrast_protocol.py [--selftest]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.safety.contrast_protocol import *  # noqa: E402,F401,F403
from binggupack.safety.contrast_protocol import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    _SAFETY_DOMAINS,
    _BINGGU_FORCED_CONS,
    _BINGGU_TRUST,
    _BINGGU_FORBID_SUBTYPE,
    _BINGGU_REQUIRE_SUBTYPE,
    _snap_sha,
    _policy_is_safety,
    _mandate_stance,
    _quote_verify,
    _binggu_side_items,
    detect_conflicts,
    build_contrast_table,
    _fmt_quote,
    render_contrast_md,
    _SNAPSHOT_SCHEMA,
    _ensure_snapshot_table,
    record_contrast,
    verify_snapshot,
)


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
