# -*- coding: utf-8 -*-
"""엣지 정규화 어댑터 + 통합 검증 진입점 (MF2.5 본체). 순수 함수 · write 0.

배경 (적대검토 MF2.5):
  `validate_verb_edge` 는 G2 proposal dict shape 전용이다 — `properties.relation`,
  `properties.label_kind`(한글), `promotion_allowed`, `properties.candidate` 를 요구한다.
  그런데 실제 데이터는 shape 이 다르다:
    - ledger `edges` 행: 평면 10컬럼(edge_id/relation/source/target/candidate/state/
      evidence_refs/pack_id/content_hash/created_at). `promotion_allowed` 컬럼 자체가 없다.
    - pack dict: 평면 `{id, relation, source, target, evidence_refs}`. properties 키 없음.
    - nodes.node_type 은 **영문**(judgment/evidence/state/doc/concept) 인데 VERB_EDGES
      매트릭스는 **한글**(판단/증거/상태/개념) 이다.
  → 사본 602건을 그대로 먹이면 첫 검사에서 `relation 6종 외: None` 으로 602/602 FAIL.
  이 모듈이 그 앞단에서 3 입력형을 단일 정규형으로 바꿔 준다.

세 축 라우팅 (relation 으로 결정):
    VERB_EDGES      → 의미 매트릭스(한글 label_kind) — `validate_verb_edge` 에 위임(원형 무수정)
    SPEAKER_EDGES   → 화자 축(src.speaker≠tgt.speaker · 둘 다 {ai,owner} · self-loop 금지 · 방향)
    GROUNDING_EDGES → 증빙 축(source ∈ evidence 공간 · target ∈ node 공간)
    그 외           → relation_unregistered

★ 이번 단계는 **판정 함수 제공까지만**이다. 저장 경로(c2_check/staging_apply)에 물리지
  않는다 — BLOCK 전환·quarantine 은 2단계. 이 모듈은 어떤 파일/DB 도 열지 않는다.

CLI: python -m binggupack.schema.edge_norm --selftest
"""
from __future__ import annotations

import json
import sys

from binggupack.classifier.label_kind_map import EN2KO, KO2EN
from binggupack.schema.verb_edge import (
    GROUNDING_EDGES,
    SPEAKER_EDGES,
    SPEAKERS,
    VERB_EDGES,
    looks_like_evidence_id,
    validate_verb_edge,
)

# 입력형 3종
ORIGIN_LEDGER = "ledger"
ORIGIN_PACK = "pack"
ORIGIN_G2 = "g2_proposal"
ORIGINS = (ORIGIN_LEDGER, ORIGIN_PACK, ORIGIN_G2)

# 엣지 축 3종
AXIS_VERB = "verb"
AXIS_SPEAKER = "speaker"
AXIS_GROUNDING = "grounding"
AXIS_NONE = None


# ─────────────────────────── 내부 shape 헬퍼 ───────────────────────────

def _as_dict(obj):
    """dict / sqlite3.Row / Mapping 을 평범한 dict 로. 그 외는 TypeError(예외 삼키지 않음)."""
    if isinstance(obj, dict):
        return obj
    keys = getattr(obj, "keys", None)
    if callable(keys):                      # sqlite3.Row, Mapping
        return {k: obj[k] for k in keys()}
    raise TypeError("edge/node 는 dict 또는 mapping 이어야 한다: %r" % type(obj))


def detect_origin(edge):
    """입력형 자동 판별. 명시 origin 인자가 없을 때만 쓴다."""
    e = _as_dict(edge)
    props = e.get("properties")
    if isinstance(props, dict) and "relation" in props:
        return ORIGIN_G2
    if "edge_id" in e:
        return ORIGIN_LEDGER
    return ORIGIN_PACK


def _evidence_refs(raw):
    """evidence_refs 정규화 — ledger 는 JSON 문자열, pack/G2 는 list. 파싱 실패는 [] 아닌 표식."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            v = json.loads(s)
        except ValueError:
            return [s]                       # 단일 id 문자열도 수용(절단·삼킴 없음)
        return list(v) if isinstance(v, (list, tuple)) else [v]
    return [raw]


def node_kind_ko(node):
    """노드 dict → 한글 label_kind. EN2KO 정본(binggupack.classifier.label_kind_map) 재사용.

    수용 shape: G2 proposal(`properties.label_kind`, 한글) · ledger/pack(`node_type`, 영문).
    미지 값은 None (호출측 fail-closed — 매트릭스 검사에서 위반으로 잡힌다)."""
    if node is None:
        return None
    n = _as_dict(node)
    props = n.get("properties")
    raw = None
    if isinstance(props, dict):
        raw = props.get("label_kind")
    if raw is None:
        raw = n.get("label_kind") or n.get("node_type")
    if raw is None:
        return None
    if raw in KO2EN:                          # 이미 한글
        return raw
    return EN2KO.get(raw)                     # 영문 → 한글, 미지면 None


def node_speaker(node):
    """노드 dict → speaker('ai'|'owner'|기타|None). ledger 는 컬럼, G2 는 properties 도 수용."""
    if node is None:
        return None
    n = _as_dict(node)
    props = n.get("properties")
    if isinstance(props, dict) and props.get("speaker") is not None:
        return props.get("speaker")
    return n.get("speaker")


def axis_for(relation):
    """relation → 축. 미등록이면 None."""
    if relation in VERB_EDGES:
        return AXIS_VERB
    if relation in SPEAKER_EDGES:
        return AXIS_SPEAKER
    if relation in GROUNDING_EDGES:
        return AXIS_GROUNDING
    return AXIS_NONE


# ─────────────────────────── 정규화 ───────────────────────────

def normalize_edge(edge, origin=None, nodes_by_id=None, evidence_by_id=None):
    """3 입력형(ledger row | pack dict | G2 proposal dict) → 단일 정규형 dict.

    반환 키
      relation, src_id, tgt_id, src_kind, tgt_kind (한글 label_kind|None),
      src_space, tgt_space ('node'|'evidence'|'missing'), src_speaker, tgt_speaker,
      evidence_refs(list), edge_id, origin, axis,
      promotion_allowed / candidate  — **부재 입력형에서는 키 자체가 없다**,
      has_promotion_field / has_candidate_field (bool), raw(원본 dict)

    부재 필드 축 제외 규칙 (설계 §2-2):
      `promotion_allowed is False` · `candidate is True` 두 검사는 origin=='g2_proposal'
      에서만 활성이다. ledger `edges.candidate` 컬럼은 존재하지만 **다른 필드**다
      (0=confirmed / 1=candidate 플래그 · 실측 supports_judgment 49건 전부 0) —
      G2 proposal 계약의 `properties.candidate` 와 의미가 달라 같은 축으로 묶지 않는다.
    """
    e = _as_dict(edge)
    if origin is None:
        origin = detect_origin(e)
    if origin not in ORIGINS:
        raise ValueError("알 수 없는 origin: %r (허용: %s)" % (origin, ", ".join(ORIGINS)))
    nodes_by_id = nodes_by_id or {}
    evidence_by_id = evidence_by_id or {}

    props = e.get("properties") if isinstance(e.get("properties"), dict) else {}
    relation = props.get("relation") if origin == ORIGIN_G2 else e.get("relation")
    if relation is None:                      # 평면 shape 인데 origin 을 G2 로 준 경우 등
        relation = e.get("relation")

    src_id = e.get("source")
    tgt_id = e.get("target")
    src_node = nodes_by_id.get(src_id)
    tgt_node = nodes_by_id.get(tgt_id)

    def _space(item_id, node):
        if node is not None:
            return "node"
        if item_id in evidence_by_id:
            return "evidence"
        if not evidence_by_id and looks_like_evidence_id(item_id):
            return "evidence"                 # evidence 테이블 미제공 시 prefix 폴백
        return "missing"

    n = {
        "edge_id": e.get("edge_id") if origin == ORIGIN_LEDGER else e.get("id", e.get("edge_id")),
        "origin": origin,
        "relation": relation,
        "axis": axis_for(relation),
        "src_id": src_id,
        "tgt_id": tgt_id,
        "src_kind": node_kind_ko(src_node),
        "tgt_kind": node_kind_ko(tgt_node),
        "src_space": _space(src_id, src_node),
        "tgt_space": _space(tgt_id, tgt_node),
        "src_speaker": node_speaker(src_node),
        "tgt_speaker": node_speaker(tgt_node),
        "evidence_refs": _evidence_refs(e.get("evidence_refs")),
        "has_promotion_field": False,
        "has_candidate_field": False,
        "raw": e,
    }
    if origin == ORIGIN_G2:
        # G2 proposal 계약에서만 두 축이 실재한다 → 그때만 정규형에 싣는다.
        n["has_promotion_field"] = "promotion_allowed" in e
        n["has_candidate_field"] = "candidate" in props
        if n["has_promotion_field"]:
            n["promotion_allowed"] = e.get("promotion_allowed")
        if n["has_candidate_field"]:
            n["candidate"] = props.get("candidate")
    return n


# ─────────────────────────── 축별 판정 ───────────────────────────

def _fail(nedge, code, detail=None):
    return {"verdict": "FAIL", "reason_code": code,
            "reason": detail or code, "edge_id": nedge.get("edge_id"),
            "axis": nedge.get("axis"), "origin": nedge.get("origin"),
            # FAIL 은 '거부'가 아니다 — 스펙 §0:14(지식은 안 버린다) 대로 처리는 보류다.
            # 2단계에서 quarantine 이 이 값을 소비한다(이번 단계는 판정만).
            "disposition": "hold"}


def _pass(nedge, detail):
    return {"verdict": "PASS", "reason_code": None, "reason": detail,
            "edge_id": nedge.get("edge_id"), "axis": nedge.get("axis"),
            "origin": nedge.get("origin"), "disposition": "accept"}


def validate_speaker_edge(nedge):
    """화자축 판정. **label_kind 매트릭스 미적용** — 실측 53건이 5종×5종 전면 분산이라
    의미 매트릭스가 원리적으로 안 맞는다. 축은 speaker 다."""
    spec = SPEAKER_EDGES[nedge["relation"]]
    if nedge["src_space"] != "node" or nedge["tgt_space"] != "node":
        return _fail(nedge, "dangling_ref", "source/target 노드 미존재 (dangling)")
    if nedge["src_id"] == nedge["tgt_id"]:
        return _fail(nedge, "self_loop", "self-loop (자기 자신에게 반응 불가)")
    ss, ts = nedge["src_speaker"], nedge["tgt_speaker"]
    if ss not in SPEAKERS or ts not in SPEAKERS:
        return _fail(nedge, "speaker_unknown",
                     "화자 미상 (src=%r tgt=%r · 허용 %s)" % (ss, ts, sorted(SPEAKERS)))
    if ss == ts:
        return _fail(nedge, "speaker_same", "동일 화자 (src=tgt=%r) — 반응 관계 성립 불가" % ss)
    if ss != spec["src_speaker"]:
        return _fail(nedge, "speaker_direction",
                     "방향 규약 위반: %s 는 source 가 %s 여야 하는데 %r"
                     % (nedge["relation"], spec["src_speaker"], ss))
    if not nedge["evidence_refs"]:
        return _fail(nedge, "evidence_refs_missing",
                     "evidence_refs 비어있음 (헌법: 근거 없는 엣지 확정 금지)")
    return _pass(nedge, "화자축 적합 (%s→%s)" % (ss, ts))


def validate_grounding_edge(nedge):
    """증빙축 판정. source 는 evidence 공간(EVC-*), target 은 node 공간."""
    if nedge["src_space"] != "evidence":
        return _fail(nedge, "grounding_src_not_evidence",
                     "source 가 evidence 가 아님 (space=%s id=%r)"
                     % (nedge["src_space"], nedge["src_id"]))
    if nedge["tgt_space"] != "node":
        return _fail(nedge, "dangling_ref",
                     "target 노드 미존재 (space=%s id=%r)" % (nedge["tgt_space"], nedge["tgt_id"]))
    if nedge["src_id"] == nedge["tgt_id"]:
        return _fail(nedge, "self_loop", "self-loop")
    if not nedge["evidence_refs"]:
        return _fail(nedge, "evidence_refs_missing", "evidence_refs 비어있음")
    return _pass(nedge, "증빙축 적합 (evidence→node)")


def validate_verb_axis(nedge):
    """의미축 판정 — `validate_verb_edge` **원형 무수정** 위임.

    ledger/pack 입력은 EN→KO 변환한 G2 shape 투영을 만들어 넘긴다. 이때 그 입력형에
    부재한 두 축(promotion_allowed·properties.candidate)은 검사에서 빠져야 하므로
    투영에 통과값(False / True)을 채운다 = '축 제외'의 구현.
    G2 proposal 입력은 **원본 객체를 그대로** 넘겨 기존 판정을 1바이트도 바꾸지 않는다."""
    raw = nedge["raw"]
    if nedge["origin"] == ORIGIN_G2:
        edge_obj = raw
        nodes = {
            nedge["src_id"]: {"id": nedge["src_id"],
                              "properties": {"label_kind": nedge["src_kind"]}},
            nedge["tgt_id"]: {"id": nedge["tgt_id"],
                              "properties": {"label_kind": nedge["tgt_kind"]}},
        }
        # dangling 은 정규형이 먼저 안다 — 투영으로 없는 노드를 만들어 주지 않는다.
        if nedge["src_space"] != "node":
            nodes.pop(nedge["src_id"], None)
        if nedge["tgt_space"] != "node":
            nodes.pop(nedge["tgt_id"], None)
    else:
        edge_obj = {
            "id": nedge["edge_id"],
            "source": nedge["src_id"],
            "target": nedge["tgt_id"],
            "properties": {"relation": nedge["relation"], "candidate": True},
            "evidence_refs": nedge["evidence_refs"],
            "promotion_allowed": False,
        }
        nodes = {}
        if nedge["src_space"] == "node":
            nodes[nedge["src_id"]] = {"id": nedge["src_id"],
                                      "properties": {"label_kind": nedge["src_kind"]}}
        if nedge["tgt_space"] == "node":
            nodes[nedge["tgt_id"]] = {"id": nedge["tgt_id"],
                                      "properties": {"label_kind": nedge["tgt_kind"]}}
    r = validate_verb_edge(edge_obj, nodes)
    if r["verdict"] == "PASS":
        return _pass(nedge, r["reason"])
    return _fail(nedge, _verb_reason_code(r["reason"]), r["reason"])


_VERB_REASON_CODES = (
    ("dangling", "dangling_ref"),
    ("self-loop", "self_loop"),
    ("evidence_refs", "evidence_refs_missing"),
    ("promotion_allowed", "promotion_not_false"),
    ("candidate", "candidate_not_true"),
    ("source label_kind", "src_kind_matrix"),
    ("target label_kind", "tgt_kind_matrix"),
    ("6종 외", "relation_unregistered"),
)


def _verb_reason_code(reason):
    """`validate_verb_edge` 의 한국어 사유 문자열 → 기계 판독 reason_code (집계용)."""
    for needle, code in _VERB_REASON_CODES:
        if needle in reason:
            return code
    return "verb_matrix_other"


# ─────────────────────────── 통합 진입점 ───────────────────────────

def validate_edge(edge, nodes_by_id=None, evidence_by_id=None, origin=None):
    """통합 진입점 — 3 입력형 어느 것이든 받아 축 라우팅 후 판정.

    반환: {"verdict": "PASS"|"FAIL", "reason_code", "reason", "edge_id", "axis",
           "origin", "disposition"}
      - `verdict` 는 호출 계약이고, `disposition` 은 스펙 §0:14 처리 지침이다
        (FAIL 이어도 disposition='hold' — 버리지 않고 보류. 차단은 PII/시크릿만).
      - 미등록 relation → {"verdict":"FAIL","reason_code":"relation_unregistered"}.

    ★ 저장 경로에 물리지 않는다(이번 단계는 판정 함수 제공까지). warn-only 삽입·
      pack split·quarantine 은 2단계 Unit C 소관이다."""
    nedge = normalize_edge(edge, origin=origin, nodes_by_id=nodes_by_id,
                           evidence_by_id=evidence_by_id)
    return validate_norm_edge(nedge)


def validate_norm_edge(nedge):
    """정규형 dict 판정(설계 §2-2 시그니처). `validate_edge` 가 내부적으로 호출한다."""
    axis = nedge.get("axis")
    if axis == AXIS_VERB:
        return validate_verb_axis(nedge)
    if axis == AXIS_SPEAKER:
        return validate_speaker_edge(nedge)
    if axis == AXIS_GROUNDING:
        return validate_grounding_edge(nedge)
    return _fail(nedge, "relation_unregistered",
                 "미등록 relation: %r (VERB/SPEAKER/GROUNDING 어디에도 없음)"
                 % (nedge.get("relation"),))


def registry_of(relation):
    """relation → (축, 스펙 dict). 미등록이면 (None, None). 소비자 편의용 순수 함수."""
    if relation in VERB_EDGES:
        return AXIS_VERB, VERB_EDGES[relation]
    if relation in SPEAKER_EDGES:
        return AXIS_SPEAKER, SPEAKER_EDGES[relation]
    if relation in GROUNDING_EDGES:
        return AXIS_GROUNDING, GROUNDING_EDGES[relation]
    return AXIS_NONE, None


# ─────────────────────────── selftest ───────────────────────────

def _ledger_row(eid, rel, src, tgt, refs='["EVC-1"]', cand=1):
    """ledger `edges` 10컬럼 shape (평면 · promotion_allowed 컬럼 없음)."""
    return {"edge_id": eid, "relation": rel, "source": src, "target": tgt,
            "candidate": cand, "state": "active", "evidence_refs": refs,
            "pack_id": "pk", "content_hash": "ch", "created_at": "2026-07-27T00:00:00Z"}


def _pack_edge(eid, rel, src, tgt, refs=("EVC-1",)):
    """pack dict shape (평면 · properties/promotion_allowed/candidate 키 없음)."""
    return {"id": eid, "relation": rel, "source": src, "target": tgt,
            "evidence_refs": list(refs)}


def _g2_edge(eid, rel, src, tgt, refs=("EVC-1",), promo=False, cand=True):
    """G2 proposal dict shape (중첩 properties)."""
    return {"id": eid, "source": src, "target": tgt,
            "properties": {"relation": rel, "candidate": cand},
            "evidence_refs": list(refs), "promotion_allowed": promo}


def _ln(nid, ntype, speaker=None):
    """ledger nodes 행 shape (node_type = 영문)."""
    return {"node_id": nid, "node_type": ntype, "speaker": speaker,
            "sentence": "s", "state": "active"}


def _gn(nid, kind_ko, speaker=None):
    """G2 proposal 노드 shape (label_kind = 한글)."""
    return {"id": nid, "properties": {"label_kind": kind_ko, "candidate": True,
                                      "speaker": speaker}}


def _selftest():
    cases = []
    ok_all = True

    L_NODES = {
        "n_ev": _ln("n_ev", "evidence"), "n_st": _ln("n_st", "state"),
        "n_co": _ln("n_co", "concept"), "n_doc": _ln("n_doc", "doc"),
        "n_j1": _ln("n_j1", "judgment", "owner"), "n_j2": _ln("n_j2", "judgment", "ai"),
        "n_j3": _ln("n_j3", "judgment", "owner"), "n_x": _ln("n_x", "judgment", None),
    }
    EV = {"EVC-CONV-1": {"evidence_id": "EVC-CONV-1"}}

    def ck(name, got, exp_verdict, exp_code=None):
        nonlocal ok_all
        good = got["verdict"] == exp_verdict and (exp_code is None or got["reason_code"] == exp_code)
        ok_all = ok_all and good
        cases.append((good, name, got["verdict"], got.get("reason_code"), got.get("reason")))

    # ---- EN↔KO 매핑 (정본 재사용 확인) ----
    map_ok = (node_kind_ko(_ln("x", "judgment")) == "판단"
              and node_kind_ko(_gn("x", "판단")) == "판단"
              and node_kind_ko(_ln("x", "evidence")) == "증거"
              and node_kind_ko(_ln("x", "unknown_type")) is None
              and node_kind_ko(None) is None)
    ok_all = ok_all and map_ok
    cases.append((map_ok, "EN2KO_매핑_정본재사용", "-", None, ""))

    # ---- origin 자동판별 ----
    org_ok = (detect_origin(_ledger_row("e", "contradicts", "n_j2", "n_j1")) == ORIGIN_LEDGER
              and detect_origin(_pack_edge("e", "contradicts", "n_j2", "n_j1")) == ORIGIN_PACK
              and detect_origin(_g2_edge("e", "contradicts", "n_j2", "n_j1")) == ORIGIN_G2)
    ok_all = ok_all and org_ok
    cases.append((org_ok, "detect_origin_3형", "-", None, ""))

    # ---- MF2.5 본체: 같은 의미 엣지가 3 입력형 모두 PASS ----
    for org, mk in ((ORIGIN_LEDGER, _ledger_row), (ORIGIN_PACK, _pack_edge)):
        ck("의미축_%s_증거→판단" % org,
           validate_edge(mk("e1", "supports_judgment", "n_ev", "n_j1"), L_NODES, EV), "PASS")
    ck("의미축_g2_증거→판단",
       validate_edge(_g2_edge("e1", "supports_judgment", "n_ev", "n_j1"),
                     {"n_ev": _gn("n_ev", "증거"), "n_j1": _gn("n_j1", "판단")}, EV), "PASS")

    # ledger 의 candidate=0 (confirmed) 이 축 제외로 PASS 를 막지 않는다 (실측 49건이 전부 0)
    ck("축제외_ledger_candidate0",
       validate_edge(_ledger_row("e2", "supports_judgment", "n_ev", "n_j1", cand=0), L_NODES, EV),
       "PASS")
    # pack 에 promotion_allowed 키가 없어도 PASS
    ck("축제외_pack_promotion부재",
       validate_edge(_pack_edge("e3", "contradicts", "n_j2", "n_j1"), L_NODES, EV), "PASS")
    # G2 에서는 두 축이 살아있다 (기존 판정 불변)
    ck("g2_promotion_true는FAIL",
       validate_edge(_g2_edge("e4", "contradicts", "n_j2", "n_j1", promo=True),
                     {"n_j1": _gn("n_j1", "판단"), "n_j2": _gn("n_j2", "판단")}), "FAIL",
       "promotion_not_false")
    ck("g2_candidate_false는FAIL",
       validate_edge(_g2_edge("e5", "contradicts", "n_j2", "n_j1", cand=False),
                     {"n_j1": _gn("n_j1", "판단"), "n_j2": _gn("n_j2", "판단")}), "FAIL",
       "candidate_not_true")
    # 매트릭스 위반은 여전히 잡힌다 (느슨해지지 않았다는 증명)
    ck("의미축_매트릭스위반_문서→판단",
       validate_edge(_ledger_row("e6", "supports_judgment", "n_doc", "n_j1"), L_NODES, EV),
       "FAIL", "src_kind_matrix")
    ck("의미축_dangling",
       validate_edge(_ledger_row("e7", "supports_judgment", "n_ev", "n_missing"), L_NODES, EV),
       "FAIL", "dangling_ref")

    # ---- 화자축 ----
    ck("화자축_ai_accepts_ai→owner", validate_edge(
        _ledger_row("s1", "ai_accepts", "n_j2", "n_j1"), L_NODES, EV), "PASS")
    ck("화자축_owner_revises", validate_edge(_ledger_row("s2", "owner_revises", "n_j1", "n_j2"),
                                          L_NODES, EV), "PASS")
    ck("화자축_방향위반", validate_edge(_ledger_row("s3", "ai_accepts", "n_j1", "n_j2"),
                                  L_NODES, EV), "FAIL", "speaker_direction")
    ck("화자축_동일화자", validate_edge(_ledger_row("s4", "owner_accepts", "n_j1", "n_j3"),
                                  L_NODES, EV), "FAIL", "speaker_same")
    ck("화자축_화자미상", validate_edge(_ledger_row("s5", "ai_accepts", "n_x", "n_j1"),
                                  L_NODES, EV), "FAIL", "speaker_unknown")
    ck("화자축_selfloop", validate_edge(_ledger_row("s6", "ai_accepts", "n_j2", "n_j2"),
                                     L_NODES, EV), "FAIL", "self_loop")
    ck("화자축_dangling", validate_edge(_ledger_row("s7", "ai_accepts", "n_j2", "n_gone"),
                                     L_NODES, EV), "FAIL", "dangling_ref")
    # ★ 화자축에 의미 매트릭스가 걸리지 않는다 — judgment→doc 처럼 VERB 매트릭스로는
    #   불가능한 쌍이 실측 53건에 실재한다(judgment→doc 3건).
    ck("화자축_매트릭스미적용_judgment→doc",
       validate_edge(_ledger_row("s8", "owner_revises", "n_j1",
                                 "n_doc2"), dict(L_NODES, n_doc2=_ln("n_doc2", "doc", "ai")), EV),
       "PASS")

    # ---- 증빙축 ----
    ck("증빙축_EVC→node", validate_edge(
        _ledger_row("g1", "evidence_supports", "EVC-CONV-1", "n_j1",
                    refs='["EVC-CONV-1"]'), L_NODES, EV), "PASS")
    # evidence 테이블 미제공 → id prefix 폴백
    ck("증빙축_prefix폴백", validate_edge(
        _ledger_row("g2", "evidence_supports", "EVC-CONV-9", "n_j1",
                    refs='["EVC-CONV-9"]'), L_NODES), "PASS")
    ck("증빙축_source가node면FAIL", validate_edge(
        _ledger_row("g3", "evidence_supports", "n_ev", "n_j1"), L_NODES, EV),
        "FAIL", "grounding_src_not_evidence")
    ck("증빙축_target없음", validate_edge(
        _ledger_row("g4", "evidence_supports", "EVC-CONV-1", "n_gone"), L_NODES, EV),
        "FAIL", "dangling_ref")

    # ---- 미등록 relation ----
    ck("미등록_relation", validate_edge(_ledger_row("u1", "owner_questions", "n_j1", "n_j2"),
                                     L_NODES, EV), "FAIL", "relation_unregistered")
    ck("미등록_relation_None", validate_edge(_pack_edge("u2", None, "n_j1", "n_j2"),
                                          L_NODES, EV), "FAIL", "relation_unregistered")

    # ---- evidence_refs 파싱 (ledger JSON 문자열 ↔ pack list) ----
    ref_ok = (_evidence_refs('["a","b"]') == ["a", "b"] and _evidence_refs(["a"]) == ["a"]
              and _evidence_refs(None) == [] and _evidence_refs("") == []
              and _evidence_refs("EVC-1") == ["EVC-1"])
    ok_all = ok_all and ref_ok
    cases.append((ref_ok, "evidence_refs_정규화", "-", None, ""))
    ck("evidence_refs_빈값", validate_edge(
        _ledger_row("r1", "supports_judgment", "n_ev", "n_j1", refs="[]"), L_NODES, EV),
        "FAIL", "evidence_refs_missing")

    # ---- FAIL 은 거부가 아니라 보류(스펙 §0:14) ----
    held = validate_edge(_ledger_row("h1", "owner_questions", "n_j1", "n_j2"), L_NODES, EV)
    hold_ok = held["disposition"] == "hold"
    ok_all = ok_all and hold_ok
    cases.append((hold_ok, "FAIL은_hold_거부아님", "-", None, ""))

    # ---- 축 라우팅 무중복 ----
    reg_ok = (registry_of("supports_judgment")[0] == AXIS_VERB
              and registry_of("ai_accepts")[0] == AXIS_SPEAKER
              and registry_of("evidence_supports")[0] == AXIS_GROUNDING
              and registry_of("owner_questions") == (None, None))
    ok_all = ok_all and reg_ok
    cases.append((reg_ok, "registry_of_라우팅", "-", None, ""))

    print("=" * 78)
    print("edge_norm — 3 입력형 정규화 어댑터 + 축 라우팅 검증기 selftest (dry-run)")
    print("=" * 78)
    for good, name, verdict, code, reason in cases:
        print("  [%s] %-34s verdict=%-4s %s" % ("OK" if good else "FAIL", name, verdict,
                                                "" if good else "(%s) %s" % (code, reason)))
    print("\n  operating_store_unchanged: True (순수 함수 · FS/DB write 0)")
    print("  wired_into_save_path: False (판정 함수 제공까지 — BLOCK 전환은 2단계)")
    gate = "GO" if ok_all else "NO-GO"
    print("\n  GATE:", gate)
    return 0 if ok_all else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    print("usage: python -m binggupack.schema.edge_norm [--selftest]")
    sys.exit(2)
