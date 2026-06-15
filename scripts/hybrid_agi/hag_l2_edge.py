# -*- coding: utf-8 -*-
"""Hybrid-AGI L2 — 추론 엣지 (MVP: depends_on, contradicts).

설계 원칙 (영구금지 준수):
  - L0(사람 원문 노드) 불변. L2 엣지는 L1 노드(src_l1/tgt_l1) 사이 추론 관계.
  - AI 는 영구 엣지를 **직접 못 쓴다**. AI 발(origin=ai) 엣지는 'candidate' 휘발 제안만.
  - 영구(confirmed) 승격 = AI 제안 + 사람 도장(stamped_by=human) 둘 다 있어야만.
    → blind(사람 도장) 없는 AI발 승격 시도 = FAIL (자동승격 0).
  - 매트릭스 검증은 openbinggu_verb_edge_schema.validate_verb_edge 에 위임(import).
    import 실패 시 동등 6종 매트릭스를 내장(fallback)해 동일 판정.
  - evidence_refs 비어있으면 hard fail (헌법: 근거 없는 엣지 확정 금지).
  - 순수 함수 / write 0 / 운영 ledger 미접촉. selftest 결정론(주입 ts·고정 시드).

CLI: python hag_l2_edge.py --selftest   →  'GATE: GO' | 'GATE: STOP'
"""
import os
import sys

# ---------------- 매트릭스: 스키마 모듈 위임(import) + 내장 fallback ----------------
# 본 파일 위치: scripts/hybrid_agi/  →  스키마는 scripts/openbinggu_verb_edge_schema.py
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# 내장 fallback 6종 매트릭스 (스키마 정본과 동등 — import 실패 대비).
_FALLBACK_VERB_EDGES = {
    "supports_judgment": {"verb": "근거가_된다", "src": {"증거", "상태", "개념"}, "tgt": {"판단"}},
    "contradicts":       {"verb": "반박한다",   "src": {"판단", "증거"},          "tgt": {"판단"}},
    "depends_on":        {"verb": "선행조건이다", "src": {"판단"},                "tgt": {"상태", "개념", "판단"}},
    "blocks":            {"verb": "막는다",     "src": {"상태", "판단"},          "tgt": {"판단"}},
    "enables":           {"verb": "가능하게_한다", "src": {"상태", "판단"},        "tgt": {"판단"}},
    "refines":           {"verb": "정밀화한다",  "src": {"개념", "판단"},          "tgt": {"판단", "개념"}},
}

try:
    from openbinggu_verb_edge_schema import validate_verb_edge as _schema_validate  # type: ignore
    from openbinggu_verb_edge_schema import VERB_EDGES as _SCHEMA_VERB_EDGES  # type: ignore
    _MATRIX_SOURCE = "schema_import"
    _VERB_EDGES = _SCHEMA_VERB_EDGES
except Exception:  # pragma: no cover - 환경 의존
    _schema_validate = None
    _MATRIX_SOURCE = "fallback_embedded"
    _VERB_EDGES = _FALLBACK_VERB_EDGES


# L2 MVP 가 다루는 추론 relation (6종의 부분집합).
L2_MVP_RELATIONS = ("depends_on", "contradicts")

VALID_ORIGINS = {"ai", "human"}
# 영구 노드/엣지를 직접 쓸 수 있는 도장 주체 = 사람만 (actor allowlist default-deny).
STAMP_ALLOWLIST = {"human"}


def _matrix_validate(edge, nodes_by_id):
    """매트릭스 검증. 스키마 import 가능하면 위임, 아니면 내장 fallback 으로 동등 판정."""
    if _schema_validate is not None:
        return _schema_validate(edge, nodes_by_id)
    # ----- fallback: 스키마 validate_verb_edge 와 동일 규칙 -----
    def fail(reason):
        return {"verdict": "FAIL", "reason": reason, "edge_id": edge.get("id")}
    rel = (edge.get("properties") or {}).get("relation")
    if rel not in _VERB_EDGES:
        return fail("relation 6종 외: %s" % rel)
    spec = _VERB_EDGES[rel]
    src = nodes_by_id.get(edge.get("source"))
    tgt = nodes_by_id.get(edge.get("target"))
    if src is None or tgt is None:
        return fail("source/target 노드 미존재 (dangling)")
    sk = (src.get("properties") or {}).get("label_kind")
    tk = (tgt.get("properties") or {}).get("label_kind")
    if sk not in spec["src"]:
        return fail("source label_kind 매트릭스 위반: %s -%s->" % (sk, rel))
    if tk not in spec["tgt"]:
        return fail("target label_kind 매트릭스 위반: -%s-> %s" % (rel, tk))
    if edge.get("source") == edge.get("target"):
        return fail("self-loop")
    if not edge.get("evidence_refs"):
        return fail("evidence_refs 비어있음 (헌법: 근거 없는 엣지 확정 금지)")
    if edge.get("promotion_allowed") is not False:
        return fail("promotion_allowed != false")
    if (edge.get("properties") or {}).get("candidate") is not True:
        return fail("candidate != true")
    return {"verdict": "PASS", "reason": "6종 매트릭스 적합", "edge_id": edge.get("id")}


def make_l2_edge(edge_id, relation, src_l1, tgt_l1, confidence,
                 evidence_refs, origin, counterevidence=None, stamped_by=None):
    """L2 추론엣지 1건 조립 (순수 함수, write 0).

    필드: edge_id·relation·src_l1·tgt_l1·confidence·counterevidence·evidence_refs·origin·stamped_by.
    매트릭스 위임을 위해 verb_edge 스키마 형태(source/target/properties/...)로도 투영한다.
    candidate(휘발) 기본: AI 발은 promotion_allowed=False, candidate=True 강제.
    """
    refs = list(evidence_refs or [])
    counter = list(counterevidence or [])
    return {
        "edge_id": edge_id,
        "relation": relation,
        "src_l1": src_l1,
        "tgt_l1": tgt_l1,
        "confidence": confidence,
        "counterevidence": counter,
        "evidence_refs": refs,
        "origin": origin,
        "stamped_by": stamped_by,  # None 이면 미도장(휘발 candidate)
        # ----- verb_edge 스키마 위임용 투영 -----
        "id": edge_id,
        "source": src_l1,
        "target": tgt_l1,
        "properties": {"relation": relation, "candidate": True},
        "promotion_allowed": False,
        "status": "candidate",
    }


def validate_l2_edge(edge, nodes_by_id, attestation_verifier=None):
    """L2 추론엣지 검증. 반환 {"verdict": PASS|FAIL, "permanent": bool, "reason": ...}.

    attestation_verifier = hag_commit_reveal.CommitRevealVault.verify_attestation 콜백.
    AI발(origin=ai) 영구 승격 시 edge['attestation'] 을 이 콜백으로만 검증(H2-1: dict 값 신뢰 금지).

    규칙:
      1) relation 은 L2 MVP(depends_on/contradicts) 내.
      2) origin 은 ai|human.
      3) evidence_refs 비어있으면 hard fail (근거 없는 엣지 금지).
      4) 매트릭스 검증 위임 (label_kind·방향·self-loop·dangling).
      5) 영구(permanent) = origin=ai 제안 + stamped_by=human 도장 둘 다.
         - blind(사람 도장) 없는 AI발 승격(promotion_allowed=True 또는 status=confirmed) = FAIL.
         - stamped_by 가 human 외(ai/system/누락) = 영구 불가 (allowlist default-deny).
    write 0.
    """
    def fail(reason):
        return {"verdict": "FAIL", "permanent": False, "reason": reason,
                "edge_id": edge.get("edge_id") or edge.get("id")}

    rel = edge.get("relation") or (edge.get("properties") or {}).get("relation")
    if rel not in L2_MVP_RELATIONS:
        return fail("L2 MVP relation 외(depends_on/contradicts 만): %s" % rel)

    origin = edge.get("origin")
    if origin not in VALID_ORIGINS:
        return fail("origin enum 외(ai|human): %s" % origin)

    if not edge.get("evidence_refs"):
        return fail("evidence_refs 비어있음 (hard fail: 근거 없는 추론엣지 금지)")

    # 매트릭스 위임 (또는 fallback) — **candidate 형상**으로만 구조검증한다.
    # (스키마 validate_verb_edge 는 promotion_allowed=False·candidate=True 강제.
    #  승격 의도는 L2 게이트가 별도로 판정하므로 매트릭스엔 candidate 형상만 넘긴다.)
    proj = {
        "id": edge.get("edge_id") or edge.get("id"),
        "source": edge.get("src_l1") or edge.get("source"),
        "target": edge.get("tgt_l1") or edge.get("target"),
        "properties": {"relation": rel, "candidate": True},
        "evidence_refs": list(edge.get("evidence_refs") or []),
        "promotion_allowed": False,
    }
    m = _matrix_validate(proj, nodes_by_id)
    if m["verdict"] != "PASS":
        return fail("매트릭스 위반: %s" % m["reason"])

    # ----- 영구 승격 게이트: AI 제안 + 사람 도장 -----
    stamped_by = edge.get("stamped_by")
    wants_permanent = (edge.get("promotion_allowed") is True
                       or edge.get("status") == "confirmed")

    if wants_permanent:
        # blind(사람 도장) 없는 AI발 승격 = FAIL
        if stamped_by not in STAMP_ALLOWLIST:
            return fail("영구 승격은 사람 도장만(stamped_by=human): %s" % stamped_by)
        # H2-1 — AI발 엣지 영구 승격은 attestation 의 dict 값을 믿지 않고 **vault verifier 콜백**으로만 검증.
        #   verifier 없음/위조 dict/blind 미통과/copy 의심 = 차단(베껴 도장 자기기만 + dict 위조 차단).
        if origin == "ai":
            if not callable(attestation_verifier):
                return fail("AI발 영구 승격엔 attestation verifier(vault.verify_attestation) 필수")
            if not attestation_verifier(edge.get("attestation")):
                return fail("attestation 검증 실패(위조/blind 미통과/copy 의심) — 승격 차단")
        return {"verdict": "PASS", "permanent": True,
                "reason": "AI 제안 + 사람 도장 + blind attestation → 영구 승격 가능",
                "edge_id": edge.get("edge_id")}

    # 승격 의도 없음 = 휘발 candidate 제안 (정상)
    return {"verdict": "PASS", "permanent": False,
            "reason": "candidate(휘발) 제안 — 영구 아님",
            "edge_id": edge.get("edge_id")}


# ---------------- selftest (결정론) ----------------

def _n(nid, kind):
    return {"id": nid, "properties": {"label_kind": kind, "candidate": True}}


def _selftest():
    nodes = {n["id"]: n for n in [
        _n("L1_j1", "판단"), _n("L1_j2", "판단"),
        _n("L1_st", "상태"), _n("L1_co", "개념"),
        _n("L1_ev", "증거"), _n("L1_doc", "문서"),
    ]}

    ATT_OK = {"qid": "q", "seal": "s", "blind_passed": True, "copy_suspected": False}
    # GOOD_V = vault.verify_attestation 모사(실제 HMAC 검증은 hag_commit_reveal selftest 커버).
    GOOD_V = lambda att: (isinstance(att, dict) and att.get("blind_passed") is True
                          and att.get("copy_suspected") is not True)

    def E(eid, rel, s, t, origin="ai", refs=("EVC-1",), stamped=None,
          promo=None, status="candidate", counter=None, att=None):
        e = make_l2_edge(eid, rel, s, t, 0.7, refs, origin,
                         counterevidence=counter, stamped_by=stamped)
        if promo is not None:
            e["promotion_allowed"] = promo
        e["status"] = status
        if att is not None:
            e["attestation"] = att
        return e

    cases = [
        # (이름, edge, 기대verdict, 기대permanent)
        ("depends_on_판단→상태",   E("e1", "depends_on", "L1_j1", "L1_st"), "PASS", False),
        ("depends_on_판단→판단",   E("e2", "depends_on", "L1_j1", "L1_j2"), "PASS", False),
        ("contradicts_판단→판단",  E("e3", "contradicts", "L1_j2", "L1_j1"), "PASS", False),
        ("contradicts_증거→판단",  E("e4", "contradicts", "L1_ev", "L1_j1"), "PASS", False),
        # 영구 승격: AI제안 + 사람도장 + blind attestation → 영구 가능
        ("AI제안+사람도장+attest_영구", E("e5", "depends_on", "L1_j1", "L1_st",
                                  origin="ai", stamped="human", promo=True, status="confirmed",
                                  att=ATT_OK),
         "PASS", True),
        ("사람발+사람도장_영구",   E("e6", "contradicts", "L1_j2", "L1_j1",
                                  origin="human", stamped="human", promo=True, status="confirmed"),
         "PASS", True),
        # ---- FAIL 군 ----
        # H2: AI발 영구 승격에 attestation 없음
        ("AI발_attest없이_승격_BLOCK", E("fa", "depends_on", "L1_j1", "L1_st",
                                    origin="ai", stamped="human", promo=True, status="confirmed"),
         "FAIL", False),
        # H2: AI발 영구 + copy 의심
        ("AI발_copy의심_승격_BLOCK", E("fb", "depends_on", "L1_j1", "L1_st",
                                  origin="ai", stamped="human", promo=True, status="confirmed",
                                  att={"blind_passed": True, "copy_suspected": True}),
         "FAIL", False),
        # 자동승격 0: blind(사람 도장) 없는 AI발 승격
        ("AI발_도장없이_승격_BLOCK", E("f1", "depends_on", "L1_j1", "L1_st",
                                    origin="ai", stamped=None, promo=True, status="confirmed",
                                    att=ATT_OK),
         "FAIL", False),
        ("AI발_AI도장_승격_BLOCK",  E("f2", "depends_on", "L1_j1", "L1_st",
                                    origin="ai", stamped="ai", promo=True, status="confirmed"),
         "FAIL", False),
        ("AI발_system도장_BLOCK",  E("f3", "depends_on", "L1_j1", "L1_st",
                                    origin="ai", stamped="system", promo=True, status="confirmed"),
         "FAIL", False),
        # evidence 없으면 hard fail
        ("evidence_없음_hardfail",  E("f4", "depends_on", "L1_j1", "L1_st", refs=()),
         "FAIL", False),
        # 매트릭스 위반
        ("매트릭스_상태→판단_depends", E("f5", "depends_on", "L1_st", "L1_j1"), "FAIL", False),
        ("매트릭스_문서→판단_contra",  E("f6", "contradicts", "L1_doc", "L1_j1"), "FAIL", False),
        ("self_loop_BLOCK",         E("f7", "contradicts", "L1_j1", "L1_j1"), "FAIL", False),
        ("dangling_target",         E("f8", "depends_on", "L1_j1", "L1_missing"), "FAIL", False),
        # L2 MVP 외 relation
        ("L2외_relation_supports",  E("f9", "supports_judgment", "L1_ev", "L1_j1"), "FAIL", False),
        # origin enum 외
        ("origin_enum외",           E("f10", "depends_on", "L1_j1", "L1_st", origin="robot"),
         "FAIL", False),
    ]

    print("=" * 74)
    print("Hybrid-AGI L2 — 추론엣지(depends_on/contradicts) selftest (dry-run)")
    print("  matrix_source:", _MATRIX_SOURCE)
    print("=" * 74)
    all_ok = True
    for name, edge, exp_v, exp_p in cases:
        r = validate_l2_edge(edge, nodes, attestation_verifier=GOOD_V)
        ok = (r["verdict"] == exp_v) and (r["permanent"] == exp_p)
        all_ok = all_ok and ok
        print("  [%s] %-30s verdict=%-4s perm=%-5s %s" % (
            "OK" if ok else "XX", name, r["verdict"], r["permanent"],
            "" if ok else "(기대 %s/%s) %s" % (exp_v, exp_p, r["reason"])))

    # 불변식: 어떤 케이스도 자동(사람도장 없이) 영구가 되어선 안 됨.
    auto_perm = 0
    for _, edge, _, _ in cases:
        r = validate_l2_edge(edge, nodes, attestation_verifier=GOOD_V)
        if r["permanent"] and edge.get("stamped_by") not in STAMP_ALLOWLIST:
            auto_perm += 1
    inv_ok = (auto_perm == 0)
    all_ok = all_ok and inv_ok
    print("  [%s] %-30s (사람도장 없는 영구 = %d 건)" % (
        "OK" if inv_ok else "XX", "invariant_no_auto_promote", auto_perm))

    # H2-1: verifier 자체가 없으면 AI발 영구 승격 차단 (dict 값만으론 승격 불가)
    e_perm = E("v1", "depends_on", "L1_j1", "L1_st", origin="ai", stamped="human",
               promo=True, status="confirmed", att=ATT_OK)
    rno = validate_l2_edge(e_perm, nodes, attestation_verifier=None)
    rforge = validate_l2_edge(e_perm, nodes, attestation_verifier=lambda a: False)
    noverif_ok = (rno["verdict"] == "FAIL" and rforge["verdict"] == "FAIL")
    all_ok = all_ok and noverif_ok
    print("  [%s] %-30s (verifier 없음·위조 reject → AI발 영구 승격 차단)" % (
        "OK" if noverif_ok else "XX", "no_verifier_or_forged_block"))

    print("\n  operating_store_unchanged: True (순수 판정, FS write 0)")
    gate = "GO" if all_ok else "STOP"
    print("\nGATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        _selftest()
    else:
        print("usage: hag_l2_edge.py [--selftest]")
        sys.exit(2)
