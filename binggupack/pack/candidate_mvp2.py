# -*- coding: utf-8 -*-
"""OpenBinggu Watcher MVP2 — Step2 Candidate transform (정본 impl · dry-run only).

v1.16 strangler Phase2: 순수 transform(_sha8/_has_secret/_meaningful/to_nodes +
DOMAIN/REDACT_RE/GENERATED_BY/NODE_KEYS/PROP_KEYS/EVIDX_KEYS 상수)을
scripts/watcher_candidate_mvp2.py 에서 byte-identical 이관. scripts/watcher_candidate_mvp2.py
는 이 모듈을 re-export 하는 backward-compatible thin wrapper(__file__ 경로상수 + _write_jsonl/
process_one/run_selftest/run_single/CLI 오케스트레이션 잔류)다.

evidence_chunk[] → incoming_nodes (노드만, 엣지 미생성). 강제: candidate=true /
promotion_allowed=false / origin=watcher / domain=STAGING_UNASSIGNED / 출력 키 whitelist.
sibling 정본 패키지 재사용(capture_mvp1/incoming_to_staging/incoming_loader/label_kind_map/a0_node).
transform 본문은 파일 I/O 무관(실제 graph/store write 없음).
"""
import hashlib
import re

from binggupack.classifier import label_kind_map as lkmap  # G0 — 5종 분류 + 한영 매핑 단일 정본
from binggupack.pack import capture_mvp1 as mvp1  # Step0+1 (capture/to_evidence) 재사용
from binggupack.pack import incoming_loader as v07loader  # v0.7 7불변식 검증 (Step3 아님)
from binggupack.pack import incoming_to_staging as v011  # secret 패턴 재사용 (_has_secret)
from binggupack.safety import a0_node as a0  # G0 — 노드 헌법 shadow 판정 (기록만, stop은 기존 가드)

# 주의: localbinggu_match_policy(Step3) 는 import 하지 않는다.

DOMAIN = "STAGING_UNASSIGNED"
REDACT_RE = re.compile(r"\[REDACTED:\d+\]")

# G0 — 생성 주체 attribution (PROV). 멱등 유지를 위해 timestamp 미포함(deterministic 값만).
GENERATED_BY = {"extractor": "watcher_candidate_mvp2", "rule_version": "g0.1"}

# 출력 키 whitelist (이 외 키는 절대 생성 안 함)
NODE_KEYS = {"id", "space", "node_type", "label", "properties", "evidence_refs", "promotion_allowed"}
PROP_KEYS = {"label_kind", "sentence", "domain", "candidate", "evidence_status", "origin",
             "rule_id", "generated_by", "a0_verdict"}
EVIDX_KEYS = {"evidence_id", "kind", "source_path", "domain", "promotion_allowed", "note"}


def _sha8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _has_secret(text):
    return any(pat.search(text) for pat in v011.SECRET_PATTERNS)


def _meaningful(sentence):
    """redacted-only / 6자미만 / 공백없는 짧은 문장 거부 (loader 휴리스틱 + redacted 제거)."""
    stripped = REDACT_RE.sub("", sentence).strip()
    if len(stripped) < 6:
        return False
    if " " not in stripped and len(stripped) < 12:
        return False
    return True


def to_nodes(chunks):
    """evidence_chunk[] → (nodes, evidence_index, stops). 노드만(엣지 미생성)."""
    nodes, ev_index, stops = [], [], []
    for c in chunks:
        item_id = c["item_id"]
        sent = c["text"]
        if not _meaningful(sent):
            stops.append({"item_id": item_id, "reason": "short/redacted-only sentence"})
            continue
        if _has_secret(sent):  # 3차 재검사
            stops.append({"item_id": item_id, "reason": "secret residual (3rd scan)"})
            continue
        # G0 — deterministic 5종 분류 (매칭 실패 = 판단 fallback, 현행 동일값)
        kind, rule_id = lkmap.classify_label_kind(sent)
        # node_type = OpenCrab space 노드타입(Document/Evidence/Concept/Claim) — v0.7 loader VALID_NTYPE 계약.
        #   conv 경로의 KO2EN 5종 도장(state/judgment)과 다른 스키마 층: 여기 node_type 은 OpenCrab 적재용이므로
        #   상태·판단→Claim 붕괴가 정상(loader 가 TitleCase 4종만 허용). 5종 도장은 A0 validator(아래 KO2EN) 전용.
        space, ntype = lkmap.KIND_TO_SPACE_NTYPE[kind]
        # G0 — A0 노드 헌법 shadow 판정 (기록만. 캡처 문장 품질 개선 전까지 stop 미적용)
        a0_res = a0.classify_node(
            {"id": "node:STAGING:wch:" + _sha8(item_id), "sentence": sent,
             "node_type": lkmap.KO2EN[kind], "evidence_refs": [item_id]},
            status="candidate")
        node = {
            "id": "node:STAGING:wch:" + _sha8(item_id),
            "space": space,
            "node_type": ntype,
            "label": sent,
            "properties": {
                "label_kind": kind,
                "sentence": sent,
                "domain": DOMAIN,
                "candidate": True,
                "evidence_status": "partial",
                "origin": "watcher",
                "rule_id": rule_id,
                "generated_by": dict(GENERATED_BY),
                "a0_verdict": a0_res["verdict"],
            },
            "evidence_refs": [item_id],
            "promotion_allowed": False,
        }
        ev = {
            "evidence_id": item_id,
            "kind": "file_pointer",
            "source_path": c.get("evidence_meta", {}).get("raw_pointer", ""),
            "domain": DOMAIN,
            "promotion_allowed": False,
            "note": "watcher capture pointer (원문 미복사)",
        }
        # whitelist 강제(이 외 키 차단)
        assert set(node) <= NODE_KEYS and set(node["properties"]) <= PROP_KEYS, "node key whitelist 위반"
        assert set(ev) <= EVIDX_KEYS, "evidence_index key whitelist 위반"
        nodes.append(node)
        ev_index.append(ev)
    return nodes, ev_index, stops
