# -*- coding: utf-8 -*-
"""OpenBinggu Watcher MVP2.1 — evidence_supports edge transform (정본 impl · dry-run only).

v1.16 strangler Phase2: 순수 transform(build_edges/_sha8/_has_secret/_freshness_from_chunks +
EVIDENCE_FANOUT_CAP/NODE_INDEGREE_CAP/EDGE_KEYS/EDGE_PROP_KEYS/REDACT_RE 상수)을
scripts/watcher_edge_mvp21.py 에서 byte-identical 이관. scripts/watcher_edge_mvp21.py 는 이
모듈을 re-export 하는 backward-compatible thin wrapper(__file__ 경로상수 + _write_jsonl/
process_from_diff/_emit/selftest fixture/CLI 오케스트레이션 잔류)다.

설계: docs/BINGGUPACK_MVP21_EDGE_SAFETY_FILTER_DESIGN.md (R2).
범위(MVP2.1 고정): evidence → node `evidence_supports` edge 1종만. node→node 의미추론 전면 금지.
1차 차단(생산자 가드): dangling · self-loop · fan-out cap(evidence 8 / node indegree 16) ·
  direction(evidence→node 단방향) · freshness(evidence timestamp 필수) · duplicate · secret raw.
transform 본문은 파일 I/O 무관(실제 graph/store write 없음).
"""
import hashlib
import re

from binggupack.pack import incoming_to_staging as v011  # secret 패턴

EVIDENCE_FANOUT_CAP = 8     # evidence 1개가 만들 수 있는 supports edge 수
NODE_INDEGREE_CAP = 16      # node 1개가 받는 incoming supports edge 수
EDGE_KEYS = {"id", "edge_type", "source", "target", "properties", "evidence_refs", "promotion_allowed"}
EDGE_PROP_KEYS = {"relation", "domain", "candidate", "origin", "sentence"}
REDACT_RE = re.compile(r"\[REDACTED:\d+\]")


def _sha8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _has_secret(text):
    return any(pat.search(text) for pat in v011.SECRET_PATTERNS)


def build_edges(nodes, ev_index, freshness_map):
    """nodes + evidence_index + freshness_map → (edges, stops). 1차 안전가드 전수.
       freshness_map: {evidence_id: timestamp}. 위반 1건이라도 있으면 stops 에 기록(전체 STOP 신호)."""
    edges, stops = [], []
    ev_ids = {e["evidence_id"] for e in ev_index}
    node_ids = {n["id"] for n in nodes}
    ev_fanout = {}      # evidence_id -> 생성 edge 수
    node_indeg = {}     # node_id -> incoming edge 수
    seen_edge_ids = set()

    for n in nodes:
        tgt = n["id"]
        sentence = n.get("properties", {}).get("sentence", n.get("label", ""))
        for ev_id in n.get("evidence_refs", []):
            src = ev_id
            # direction: source=evidence(EVC-), target=node(node:)
            if not (src in ev_ids):
                stops.append({"reason": "dangling evidence_ref (evidence 미존재)", "src": src, "tgt": tgt})
                continue
            if tgt not in node_ids:
                stops.append({"reason": "dangling node (target 미존재)", "src": src, "tgt": tgt})
                continue
            if src == tgt:
                stops.append({"reason": "self-loop", "src": src, "tgt": tgt})
                continue
            if not src.startswith("EVC-") or not tgt.startswith("node:"):
                stops.append({"reason": "direction 위반(evidence→node 아님)", "src": src, "tgt": tgt})
                continue
            # freshness: evidence timestamp 필수
            ts = freshness_map.get(ev_id)
            if not ts:
                stops.append({"reason": "freshness stamp 누락", "src": src, "tgt": tgt})
                continue
            # secret raw (node sentence 기준)
            if _has_secret(sentence):
                stops.append({"reason": "secret residual in node sentence", "src": src, "tgt": tgt})
                continue
            # fan-out cap
            ev_fanout[src] = ev_fanout.get(src, 0) + 1
            node_indeg[tgt] = node_indeg.get(tgt, 0) + 1
            if ev_fanout[src] > EVIDENCE_FANOUT_CAP:
                stops.append({"reason": f"evidence fan-out cap 초과(>{EVIDENCE_FANOUT_CAP})", "src": src, "tgt": tgt})
                continue
            if node_indeg[tgt] > NODE_INDEGREE_CAP:
                stops.append({"reason": f"node indegree cap 초과(>{NODE_INDEGREE_CAP})", "src": src, "tgt": tgt})
                continue
            eid = "edge:STAGING:wch:" + _sha8(src + "→" + tgt)
            # duplicate (동일 src/tgt/relation → 동일 id)
            if eid in seen_edge_ids:
                stops.append({"reason": "duplicate relation (동일 src/tgt/relation)", "src": src, "tgt": tgt})
                continue
            seen_edge_ids.add(eid)
            domain = n.get("properties", {}).get("domain", "STAGING_UNASSIGNED")
            edge = {
                "id": eid,
                "edge_type": "EvidenceSupports",
                "source": src,
                "target": tgt,
                "properties": {
                    "relation": "evidence_supports",
                    "domain": domain,
                    "candidate": True,
                    "origin": "watcher",
                    "sentence": "evidence가 노드를 뒷받침한다",
                },
                "evidence_refs": [ev_id],
                "promotion_allowed": False,
            }
            assert set(edge) <= EDGE_KEYS and set(edge["properties"]) <= EDGE_PROP_KEYS, "edge key whitelist 위반"
            edges.append(edge)
    return edges, stops


def _freshness_from_chunks(chunks):
    return {c["item_id"]: c.get("evidence_meta", {}).get("timestamp") for c in chunks}
