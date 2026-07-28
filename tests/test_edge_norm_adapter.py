# -*- coding: utf-8 -*-
"""엣지 정규화 어댑터 회귀 (MF2.5) — 3 입력형 → 단일 정규형 · EN↔KO · 부재 필드 축 제외.

MF2.5: `validate_verb_edge` 는 G2 proposal shape 전용(`properties.relation`,
한글 `label_kind`, `promotion_allowed`, `properties.candidate`)인데 실제 데이터는
  ledger row : 평면 10컬럼 · `promotion_allowed` 컬럼 **부재**
  pack dict  : 평면 `{id, relation, source, target, evidence_refs}`
  nodes.node_type : **영문**(judgment/…) ↔ 매트릭스는 **한글**(판단/…)
이라 사본 602건을 그대로 먹이면 첫 검사에서 602/602 FAIL 이 난다.
어댑터가 그 간극을 메우되, **G2 경로 판정은 1건도 안 바뀌어야** 한다(회귀 0).

여기서 못박는 것
  ① 같은 엣지를 3 입력형으로 줘도 정규형의 의미 필드가 동일하고 판정도 동일
  ② EN2KO 는 정본(binggupack.classifier.label_kind_map) 재사용 — 두 번째 어휘집 금지
  ③ `promotion_allowed`/`candidate` 검사는 origin=='g2_proposal' 에서만 활성
  ④ 축 라우팅(verb/speaker/grounding) + 미등록 relation 은 거부가 아니라 보류(disposition=hold)
  ⑤ `VERB_EDGES` 원형 6종 불변(소비자 `rel not in VERB_EDGES` 가드 보호)
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.classifier.label_kind_map import EN2KO, KO2EN  # noqa: E402
from binggupack.schema import edge_norm as en  # noqa: E402
from binggupack.schema.verb_edge import (  # noqa: E402
    GROUNDING_EDGES,
    SPEAKER_EDGES,
    SPEAKERS,
    VERB_EDGES,
    validate_verb_edge,
)

_MEANING_KEYS = ("relation", "src_id", "tgt_id", "src_kind", "tgt_kind",
                 "src_space", "tgt_space", "src_speaker", "tgt_speaker", "evidence_refs", "axis")


def _meaning(n):
    return {k: n[k] for k in _MEANING_KEYS}


# 같은 엣지 1건(증거 → 판단)을 3 입력형으로 표현 ------------------------------
LEDGER_NODES = {"EV1": {"node_id": "EV1", "node_type": "evidence"},
                "JD1": {"node_id": "JD1", "node_type": "judgment"}}
G2_NODES = {"EV1": {"id": "EV1", "properties": {"label_kind": "증거"}},
            "JD1": {"id": "JD1", "properties": {"label_kind": "판단"}}}

LEDGER_EDGE = {"edge_id": "E1", "relation": "supports_judgment", "source": "EV1",
               "target": "JD1", "candidate": 0, "state": "active",
               "evidence_refs": '["EVC-1"]', "pack_id": "p", "content_hash": "h",
               "created_at": "2026-07-27T00:00:00Z"}
PACK_EDGE = {"id": "E1", "relation": "supports_judgment", "source": "EV1",
             "target": "JD1", "evidence_refs": ["EVC-1"]}
G2_EDGE = {"id": "E1", "promotion_allowed": False, "evidence_refs": ["EVC-1"],
           "source": "EV1", "target": "JD1",
           "properties": {"relation": "supports_judgment", "candidate": True}}


def test_three_input_shapes_produce_the_same_meaning():
    """① 3 입력형 → 동일 정규형(의미 축) + 동일 PASS 판정."""
    n_led = en.normalize_edge(LEDGER_EDGE, nodes_by_id=LEDGER_NODES)
    n_pack = en.normalize_edge(PACK_EDGE, nodes_by_id=LEDGER_NODES)
    n_g2 = en.normalize_edge(G2_EDGE, nodes_by_id=G2_NODES)

    assert (n_led["origin"], n_pack["origin"], n_g2["origin"]) == ("ledger", "pack", "g2_proposal")
    assert _meaning(n_led) == _meaning(n_pack) == _meaning(n_g2)
    assert n_led["src_kind"] == "증거" and n_led["tgt_kind"] == "판단"
    assert n_led["edge_id"] == n_pack["edge_id"] == n_g2["edge_id"] == "E1"
    assert n_led["evidence_refs"] == ["EVC-1"]          # ledger 의 JSON 문자열도 list 로

    for edge, nodes in ((LEDGER_EDGE, LEDGER_NODES), (PACK_EDGE, LEDGER_NODES), (G2_EDGE, G2_NODES)):
        v = en.validate_edge(edge, nodes_by_id=nodes)
        assert v["verdict"] == "PASS", (edge.get("id"), v)
        assert v["axis"] == "verb"


def test_en_ko_mapping_reuses_the_canonical_dict():
    """② 사본 어휘집 금지 — EN2KO 는 정본의 역매핑이어야 한다."""
    assert EN2KO == {v: k for k, v in KO2EN.items()}
    assert en.node_kind_ko({"node_type": "judgment"}) == "판단"
    assert en.node_kind_ko({"properties": {"label_kind": "판단"}}) == "판단"
    assert en.node_kind_ko({"node_type": "알수없는타입"}) is None
    assert en.node_kind_ko(None) is None
    for en_name, ko in EN2KO.items():
        assert en.node_kind_ko({"node_type": en_name}) == ko


def test_absent_fields_are_excluded_from_the_axis():
    """③ ledger/pack 에는 promotion_allowed/candidate 축을 적용하지 않는다."""
    n_led = en.normalize_edge(LEDGER_EDGE, nodes_by_id=LEDGER_NODES)
    n_pack = en.normalize_edge(PACK_EDGE, nodes_by_id=LEDGER_NODES)
    for n in (n_led, n_pack):
        assert n["has_promotion_field"] is False and n["has_candidate_field"] is False
        assert "promotion_allowed" not in n and "candidate" not in n

    # ledger 의 candidate=0(confirmed) 은 G2 의 properties.candidate 와 다른 필드다 —
    # 같은 축으로 묶으면 실측 supports_judgment 49건이 전부 FAIL 난다.
    confirmed = dict(LEDGER_EDGE, candidate=0)
    assert en.validate_edge(confirmed, nodes_by_id=LEDGER_NODES)["verdict"] == "PASS"

    n_g2 = en.normalize_edge(G2_EDGE, nodes_by_id=G2_NODES)
    assert n_g2["has_promotion_field"] is True and n_g2["has_candidate_field"] is True


def test_g2_contract_verdicts_are_unchanged():
    """③' G2 경로 판정 회귀 0 — 어댑터를 거쳐도 원형과 같은 결론."""
    bad_promo = {**G2_EDGE, "promotion_allowed": True}
    bad_cand = {**G2_EDGE, "properties": {**G2_EDGE["properties"], "candidate": False}}
    for edge in (bad_promo, bad_cand):
        assert validate_verb_edge(edge, G2_NODES)["verdict"] == "FAIL"       # 원형
        assert en.validate_edge(edge, nodes_by_id=G2_NODES)["verdict"] == "FAIL"   # 어댑터 경유
    assert validate_verb_edge(G2_EDGE, G2_NODES)["verdict"] == "PASS"
    assert en.validate_edge(G2_EDGE, nodes_by_id=G2_NODES)["verdict"] == "PASS"


def test_matrix_violation_and_dangling_still_fail():
    """의미축은 여전히 엄격하다 — 100% PASS 가 '헐거워서'가 아님을 음성 대조로 못박는다."""
    reversed_dir = dict(LEDGER_EDGE, source="JD1", target="EV1")
    v = en.validate_edge(reversed_dir, nodes_by_id=LEDGER_NODES)
    assert v["verdict"] == "FAIL" and "kind" in v["reason_code"]

    dangling = dict(LEDGER_EDGE, target="NOPE")
    assert en.validate_edge(dangling, nodes_by_id=LEDGER_NODES)["reason_code"] == "dangling_ref"

    no_ev = dict(LEDGER_EDGE, evidence_refs="[]")
    assert en.validate_edge(no_ev, nodes_by_id=LEDGER_NODES)["reason_code"] == "evidence_refs_missing"


# ── ④ 축 라우팅 ─────────────────────────────────────────────────────────────
SPK_NODES = {"O1": {"node_id": "O1", "node_type": "judgment", "speaker": "owner"},
             "A1": {"node_id": "A1", "node_type": "doc", "speaker": "ai"}}


def _spk_edge(rel, src, tgt):
    return {"edge_id": "S1", "relation": rel, "source": src, "target": tgt,
            "evidence_refs": '["EVC-1"]'}


@pytest.mark.parametrize("rel,spec", sorted(SPEAKER_EDGES.items()))
def test_speaker_axis_accepts_every_registered_relation(rel, spec):
    src, tgt = ("A1", "O1") if spec["src_speaker"] == "ai" else ("O1", "A1")
    v = en.validate_edge(_spk_edge(rel, src, tgt), nodes_by_id=SPK_NODES)
    assert v["verdict"] == "PASS" and v["axis"] == "speaker", v
    # 화자축엔 의미 매트릭스를 걸지 않는다 — judgment→doc 쌍(실측 존재)도 통과해야 한다
    assert set(spec) == {"verb", "src_speaker", "tgt_speaker"}


def test_speaker_axis_negative_controls():
    assert en.validate_edge(_spk_edge("ai_accepts", "O1", "A1"),
                            nodes_by_id=SPK_NODES)["reason_code"] == "speaker_direction"
    assert en.validate_edge(_spk_edge("ai_accepts", "A1", "A1"),
                            nodes_by_id=SPK_NODES)["reason_code"] == "self_loop"
    no_spk = {"O1": {"node_id": "O1", "node_type": "judgment"},
              "A1": {"node_id": "A1", "node_type": "judgment", "speaker": "ai"}}
    assert en.validate_edge(_spk_edge("ai_accepts", "A1", "O1"),
                            nodes_by_id=no_spk)["reason_code"] == "speaker_unknown"


def test_grounding_axis_requires_evidence_source():
    ev_edge = {"edge_id": "G1", "relation": "evidence_supports", "source": "EVC-CONV-abc",
               "target": "JD1", "evidence_refs": '["EVC-CONV-abc"]'}
    v = en.validate_edge(ev_edge, nodes_by_id=LEDGER_NODES,
                         evidence_by_id={"EVC-CONV-abc": {"evidence_id": "EVC-CONV-abc"}})
    assert v["verdict"] == "PASS" and v["axis"] == "grounding"
    # source 가 node 면 증빙축이 아니다
    bad = dict(ev_edge, source="EV1")
    assert en.validate_edge(bad, nodes_by_id=LEDGER_NODES,
                            evidence_by_id={})["reason_code"] == "grounding_src_not_evidence"


def test_unregistered_relation_is_hold_not_rejection():
    """④' 미등록 술어는 스펙 §0:14 대로 '버리지 않는다' — FAIL 이라도 처리는 보류다."""
    unknown = dict(LEDGER_EDGE, relation="owner_questions")
    v = en.validate_edge(unknown, nodes_by_id=LEDGER_NODES)
    assert v["reason_code"] == "relation_unregistered"
    assert v["disposition"] == "hold"
    assert en.axis_for("owner_questions") is None


def test_registries_do_not_overlap_and_verb_edges_frozen():
    """⑤ VERB_EDGES 원형 6종 불변 + 세 레지스트리 교집합 0."""
    assert set(VERB_EDGES) == {"supports_judgment", "contradicts", "depends_on",
                               "blocks", "enables", "refines"}
    assert not (set(VERB_EDGES) & set(SPEAKER_EDGES))
    assert not (set(VERB_EDGES) & set(GROUNDING_EDGES))
    assert not (set(SPEAKER_EDGES) & set(GROUNDING_EDGES))
    assert SPEAKERS == {"ai", "owner"}
    for rel in VERB_EDGES:
        assert en.registry_of(rel) == (en.AXIS_VERB, VERB_EDGES[rel])
    for rel in SPEAKER_EDGES:
        assert en.registry_of(rel) == (en.AXIS_SPEAKER, SPEAKER_EDGES[rel])
    for rel in GROUNDING_EDGES:
        assert en.registry_of(rel) == (en.AXIS_GROUNDING, GROUNDING_EDGES[rel])
    assert en.registry_of("owner_questions") == (None, None)


def test_normalize_accepts_sqlite_rows(tmp_path):
    """ledger 를 실제로 읽는 형태(sqlite3.Row)도 그대로 정규화된다."""
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE edges(edge_id TEXT, relation TEXT, source TEXT, target TEXT,"
                " candidate INTEGER, evidence_refs TEXT)")
    con.execute("INSERT INTO edges VALUES('E1','supports_judgment','EV1','JD1',0,'[\"EVC-1\"]')")
    row = con.execute("SELECT * FROM edges").fetchone()
    v = en.validate_edge(row, nodes_by_id=LEDGER_NODES)
    assert v["verdict"] == "PASS" and v["origin"] == "ledger"
    con.close()
