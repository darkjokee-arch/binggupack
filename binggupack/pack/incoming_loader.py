# -*- coding: utf-8 -*-
"""LocalBinggu incoming graph loader v0.7 (정본 impl · read-only).

v1.16 strangler Phase2: scripts/localbinggu_incoming_loader.py 에서 이관.
scripts/localbinggu_incoming_loader.py 는 이 모듈을 re-export 하는 backward-compatible
thin wrapper(CLI/__main__ 잔류)다.

(위치 주의: 의미상 storage 영역이나, binggupack.storage.__init__ facade 가 import 시 무거운
scripts 모듈을 로드해 순환을 일으키므로 storage/ 대신 pack/ 에 둔다 — 순수 transform 이라 패키지 위치 무관.)

incoming_{nodes,edges,evidence_index}.jsonl 을 읽어 schema/evidence/promotion/D9/coverage 불변식을 검증.
structured delta only. 위반 시 violations 에 기록(거부). 운영 store write 없음.
"""
import json
from pathlib import Path

VALID_SPACE = {"resource", "evidence", "concept", "claim"}
VALID_NTYPE = {"Document", "Evidence", "Concept", "Claim"}
VALID_KIND = {"문서", "증거", "개념", "상태", "판단"}
VALID_REL = {"contains", "describes", "supports"}


def _jl(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_incoming(incoming_dir, known_evidence_ids=None):
    """known_evidence_ids: seed 등 기존 graph 의 evidence_id 집합.
    incoming 노드가 기존 evidence 를 참조하는 것은 정상(merge/protect 의 evidence 교집합)."""
    d = Path(incoming_dir)
    nodes = _jl(d / "incoming_nodes.jsonl")
    edges = _jl(d / "incoming_edges.jsonl")
    evidence = _jl(d / "incoming_evidence_index.jsonl")
    ev_ids = {e.get("evidence_id") for e in evidence} | set(known_evidence_ids or [])

    violations = []
    accepted_nodes, accepted_edges = [], []

    for n in nodes:
        nid = n.get("id", "?")
        p = n.get("properties", {})
        # coverage/pattern 거부
        if "pattern_id" in n or "source_type" in n:
            violations.append({"id": nid, "rule": "coverage/pattern node 금지"}); continue
        # promotion_allowed=false 강제
        if n.get("promotion_allowed") is not False:
            violations.append({"id": nid, "rule": "promotion_allowed!=false"}); continue
        # enum
        if n.get("space") not in VALID_SPACE:
            violations.append({"id": nid, "rule": f"space invalid: {n.get('space')}"}); continue
        if n.get("node_type") not in VALID_NTYPE:
            violations.append({"id": nid, "rule": f"node_type invalid: {n.get('node_type')}"}); continue
        if p.get("label_kind") not in VALID_KIND:
            violations.append({"id": nid, "rule": f"label_kind invalid: {p.get('label_kind')}"}); continue
        # evidence_refs 필수 + index 존재
        refs = n.get("evidence_refs", [])
        if not refs:
            violations.append({"id": nid, "rule": "evidence_refs 비어있음"}); continue
        miss = [r for r in refs if r not in ev_ids]
        if miss:
            violations.append({"id": nid, "rule": f"evidence_refs index 누락: {miss}"}); continue
        # 단어 키워드 금지(핵심 문장 휴리스틱: 공백 포함 + 최소 길이)
        s = p.get("sentence", "")
        if len(s) < 6 or (" " not in s and len(s) < 12):
            violations.append({"id": nid, "rule": "단어 키워드 의심(핵심 문장 아님)"}); continue
        accepted_nodes.append(n)

    for e in edges:
        eid = e.get("id", "?")
        if e.get("promotion_allowed") is not False:
            violations.append({"id": eid, "rule": "edge promotion_allowed!=false"}); continue
        if e.get("relation") not in VALID_REL:
            violations.append({"id": eid, "rule": f"relation invalid: {e.get('relation')}"}); continue
        if not e.get("evidence_refs"):
            violations.append({"id": eid, "rule": "edge evidence_refs 비어있음"}); continue
        accepted_edges.append(e)

    d9_partial = [n["id"] for n in accepted_nodes
                  if n.get("properties", {}).get("domain") == "D9"
                  and (n["properties"].get("candidate") or n["properties"].get("evidence_status") != "confirmed")]

    return {
        "incoming_dir": str(d),
        "counts": {"nodes_in": len(nodes), "edges_in": len(edges), "evidence_in": len(evidence),
                   "nodes_accepted": len(accepted_nodes), "edges_accepted": len(accepted_edges)},
        "violations": violations,
        "d9_partial_protected_ids": d9_partial,
        "schema_valid": len(violations) == 0,
        "accepted_nodes": accepted_nodes,
        "accepted_edges": accepted_edges,
        "evidence": evidence,
    }
