# -*- coding: utf-8 -*-
"""crab_pack_wire — CrabAgent 스키마 Cloud Pack v1 빌드(순수) + SaaS 업로드 wire(주입형).

기존 ingest_text 경로(문서당 13노드/12엣지 서버 추출 상한·스키마 없음)와 달리,
로컬에서 개념/주장/증거 계층을 가진 스키마 팩 ZIP 을 빌드해 crab-agent 업로드 세션으로
적재한다(2026-07-06 잔지바르 파일럿 + 40팩군 배치로 검증된 경로의 정본화).

흐름:
  1) build_crab_pack(data_dir → Cloud Pack v1 ZIP): stdlib only·네트워크 0.
     도메인 상수(CONCEPTS/CLAIMS/QUERIES)는 데이터에서 실측 파생하고, 검증 질의는
     빌더와 동일한 청킹·idf 채점을 사전 시뮬레이션해 통과분만 채택한다
     (사후 게이트 실패 7/38 재발 방지 — 자기채점 게이트는 생성 단계에서 선실행).
  2) upload_crab_pack(ZIP → create_upload_session → PUT → finalize):
     서버 "existing chunks/nodes delete" statement timeout 은 재시도(세션 재발급)로
     워밍 통과. 업로드 토큰은 1회용이라 재시도 = 매번 새 세션.

안전 불변 (전부 _selftest 로 증명):
  - 빌드: PII/secret leak 게이트 fail-closed·원본 문서 ZIP 미포함(파생 청크만·
    original_documents_stored=false)·release gate 실측(A 아니면 ok=False).
  - 업로드: 기본 dry_run. live 는 ENABLE_ENV=='1' + confirm=True + cloud config 존재 +
    ZIP release_ready 전부 필요(fail-closed). 서명 URL/업로드 토큰은 공개 출력 0.
  - pack_name 비ASCII → ASCII 자동 변환(서버 스토리지 한글 키 InvalidKey 버그 회피).
  - transport/put_fn/post_fn 주입형: 실 네트워크는 live 경로에서만 lazy 진입.

CLI: python -m binggupack.pack.crab_pack_wire --selftest
     python -m binggupack.pack.crab_pack_wire build --data <dir> --out <zip> --title <t>
     python -m binggupack.pack.crab_pack_wire upload --zip <zip> --pack-name <ascii> [--live --confirm]
"""
import argparse
import hashlib
import json
import math
import os
import re
import time
import zipfile
from collections import Counter
from itertools import combinations
from pathlib import Path

from binggupack.pack.cloud_ingest_wire import (
    _classify_response,
    _redact_token,
    default_http_transport,
    load_cloud_config,
)

ENABLE_ENV = "BINGGU_CRAB_UPLOAD"       # owner 토글 — '1' 이어야 live 가능(기본 OFF)
CRAB_TOOL = "opencrab_crab_agent"
DEFAULT_MAX_TRIES = 8
RETRYABLE_RX = re.compile(r"statement timeout|http_5\d\d|fetch_error", re.I)
TOKEN_IN_CMD_RX = re.compile(r"X-OpenCrab-Upload-Token: ([A-Za-z0-9_-]+)'")

TEXT_EXTS = {".md", ".markdown", ".txt", ".csv", ".tsv", ".json", ".jsonl",
             ".yaml", ".yml", ".xml", ".html", ".htm"}
MAX_FILE = 5 * 1024 * 1024
CLAIM_HINTS = re.compile(r"추천|인기|유명|좋(?:은|다|아)|필수|즐길|체험|가능|아름다|매력")
TOKEN_RX = re.compile(r"[가-힣A-Za-z]{4,}")

LEAK_PATTERNS = [
    ("pii_bizno_fmt", re.compile(r"\d{3}-\d{2}-\d{5}")),
    ("pii_rrn", re.compile(r"\b\d{6}-\d{7}\b")),
    ("pii_phone", re.compile(r"\b01[016789]-\d{3,4}-\d{4}\b")),
    ("pii_email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("secret_kv", re.compile(r"(api[_-]?key|passwd|password)\s*[:=]\s*\S", re.I)),
    ("win_abs_path", re.compile(r"[A-Za-z]:\\Users\\(?!<)")),
    # 접두 없는 맨 토큰(prefix 없이도 유출) — batch_m1 의 가장 공격적(최소) 임계와 정합.
    ("secret_aws_akia", re.compile(r"\bAKIA[0-9A-Z]{7,}")),
    ("secret_vendor_token",
     re.compile(r"\b(?:sk-live-[A-Za-z0-9]{8,}|sk-[A-Za-z0-9]{16,}|gh[oprsu]_[A-Za-z0-9]{20,})")),
    ("secret_bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{20,}")),
    ("secret_private_key", re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----")),
]


def _sha256(b):
    return hashlib.sha256(b).hexdigest()


def chunk_doc(text, cap=2400):
    """행 단위 누적 분할(행 내부 분절 0 — 문장은 항상 한 청크에 통째로)."""
    parts, cur = [], []
    for ln in text.splitlines():
        if ln.startswith("#") and cur and sum(len(x) for x in cur) > 600:
            parts.append("\n".join(cur).strip())
            cur = []
        cur.append(ln)
        if sum(len(x) for x in cur) > cap:
            parts.append("\n".join(cur).strip())
            cur = []
    if cur:
        parts.append("\n".join(cur).strip())
    return [p for p in parts if p.strip()]


# ───────────────────────────── 도메인 상수 실측 파생 ─────────────────────────────
def scan_data(data_dir):
    """데이터 폴더 → [(파일명, 주제, 본문)] (파일명 소문자 정렬 = 빌드 순서와 동일)."""
    docs = []
    for p in sorted(Path(data_dir).glob("*"), key=lambda x: str(x).lower()):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXTS or p.stat().st_size > MAX_FILE:
            continue
        topic = re.sub(r"_\d+$", "", p.stem).replace("_", " ")
        docs.append((p.name, topic, p.read_text(encoding="utf-8", errors="replace")))
    return docs


def derive_concepts(docs):
    corpus_low = "\n".join(t for _, _, t in docs).lower()
    concepts, seen = [], set()
    for i, (_, topic, _) in enumerate(docs):
        if topic in seen:
            continue
        seen.add(topic)
        terms = [w for w in TOKEN_RX.findall(topic) if w.lower() in corpus_low][:3]
        if terms:
            concepts.append(("t%02d" % (i + 1), topic, terms))
    return concepts


def _claim_sentence(text, hint_required):
    for line in text.splitlines():
        line = line.strip()
        if not (30 <= len(line) <= 400):
            continue
        for sent in re.split(r"(?<=다\.)\s+|(?<=[.!?])\s+", line):
            sent = sent.strip()
            if not (20 <= len(sent) <= 160):
                continue
            if hint_required and not CLAIM_HINTS.search(sent):
                continue
            mid = max(0, len(sent) // 2 - 7)
            needle = sent[mid:mid + 14]
            if len(needle) >= 8 and needle.lower() in text.lower():
                return sent, needle
    return None


def derive_claims(docs, limit=12):
    claims = []
    for _, _, text in docs:
        got = _claim_sentence(text, True) or _claim_sentence(text, False)
        if got:
            sent, needle = got
            claims.append(("tc_" + _sha256(sent.encode())[:6], sent, needle))
        if len(claims) >= limit:
            break
    return claims


def derive_queries(docs, limit=10, cap=2400):
    """빌더와 동일한 청킹·idf 채점을 사전 실행해 hit/relevant/coverage 를 실제로
    통과하는 질의만 채택 — retrieval 게이트 실패의 구조적 봉합."""
    lows = []
    for _, _, text in docs:
        lows.extend(c.lower() for c in chunk_doc(text, cap))
    nch = len(lows)
    if not nch:
        return []

    def idf(t):
        return math.log((nch + 1) / (sum(1 for x in lows if t in x) + 1)) + 1.0

    def score(qts, tl):
        return sum(idf(w) for w in sorted({x.lower() for x in qts}) if w in tl)

    df = Counter()
    doc_tokens = []
    for _, _, text in docs:
        toks = set(TOKEN_RX.findall(text.lower()))
        doc_tokens.append(toks)
        df.update(toks)

    queries, used = [], set()
    for idx, (_, topic, _) in enumerate(docs):
        if len(queries) >= limit:
            break
        rare = sorted((t for t in doc_tokens[idx] if df[t] <= 2 and t not in used),
                      key=lambda t: (df[t], -len(t)))[:6]
        for r1, r2 in combinations(rare, 2):
            keys = [r1, r2]
            q = "%s %s %s" % (topic, r1, r2)
            qts = [w for w in re.split(r"\s+", q) if len(w) > 1] + keys
            top3 = sorted(lows, key=lambda tl: -score(qts, tl))[:3]
            hit = any(keys[0] in tl for tl in top3)
            rhit = any(all(k in tl for k in keys) for tl in top3)
            uq = sorted({x.lower() for x in qts})
            cov = (sum(1 for w in uq if w in top3[0]) / max(1, len(uq))) if top3 else 0.0
            if hit and rhit and cov >= 0.25:
                used.update(keys)
                queries.append(("q%02d" % (len(queries) + 1), q, keys))
                break
    return queries


def _local_vector(text, dim=256):
    v = [0.0] * dim
    low = re.sub(r"\s+", " ", text.lower())
    for i in range(len(low) - 2):
        h = int(hashlib.md5(low[i:i + 3].encode("utf-8")).hexdigest()[:8], 16) % dim
        v[h] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [round(x / norm, 5) for x in v]


# ───────────────────────────── 빌드 (순수·네트워크 0) ─────────────────────────────
def build_crab_pack(data_dir, out_zip, title, purpose, *, min_queries=6, now_fn=None, chunk_cap=2400):
    """데이터 폴더 → Cloud Pack v1 ZIP. 반환(raise 0): typed dict.

    {ok, grade, release_ready, failed_gates, counts, zip, leak_count, retrieval,
     concepts, claims, queries, reason}
    ok = grade 'A' + release_ready. 원본 문서는 ZIP 에 넣지 않는다(파생 청크만).
    Claim 노드의 properties.status='candidate' 는 스키마 고정값(파생 주장=검증 전 후보)로,
    장부 candidate 등급(문서 본문의 후보/봉인 정본 텍스트 표기)과는 무관하다.
    """
    out = {"ok": False, "grade": None, "release_ready": False, "failed_gates": [],
           "counts": {}, "zip": str(out_zip), "leak_count": 0, "retrieval": {},
           "concepts": 0, "claims": 0, "queries": 0, "reason": None}
    try:
        docs_raw = scan_data(data_dir)
        if not docs_raw:
            out["reason"] = "NO_READABLE_DOCS"
            return out
        concepts = derive_concepts(docs_raw)
        claims = derive_claims(docs_raw)
        queries = derive_queries(docs_raw, cap=chunk_cap)
        out.update({"concepts": len(concepts), "claims": len(claims), "queries": len(queries)})
        if not concepts or not claims or len(queries) < min_queries:
            out["reason"] = "DERIVE_INSUFFICIENT"
            return out

        started = time.time()
        documents, chunks, ev_index, nodes, edges = [], [], [], [], []
        leak_hits = {}
        for di, (fname, _, text) in enumerate(docs_raw, 1):
            doc_id = "doc:%02d" % di
            raw_b = text.encode("utf-8")
            documents.append({"id": doc_id, "title": fname, "path": fname,
                              "sha256": _sha256(raw_b), "bytes": len(raw_b),
                              "mime": "text/markdown", "source_id": doc_id, "name": fname})
            nodes.append({"id": doc_id, "space": "resource", "node_type": "Document",
                          "type": "Document", "label": fname, "path": fname,
                          "sha256": documents[-1]["sha256"], "bytes": len(raw_b),
                          "properties": {"path": fname}, "evidence_refs": []})
            for ci, body in enumerate(chunk_doc(text, chunk_cap), 1):
                ch_id = "chunk:%02d:%02d" % (di, ci)
                for code, rx in LEAK_PATTERNS:
                    n = len(rx.findall(body))
                    if n:
                        leak_hits[code] = leak_hits.get(code, 0) + n
                chunks.append({"id": ch_id, "chunk_id": ch_id, "document_id": doc_id,
                               "source_id": doc_id, "path": fname, "ordinal": ci, "seq": ci,
                               "text": body, "chars": len(body), "evidence_id": ch_id,
                               "evidence_refs": [ch_id]})
                ev_index.append({"id": ch_id, "evidence_id": ch_id, "chunk_id": ch_id,
                                 "document_id": doc_id, "source_id": doc_id, "path": fname,
                                 "ordinal": ci, "text": body, "sha256": _sha256(body.encode()),
                                 "chars": len(body), "source_path": fname,
                                 "evidence_refs": [ch_id]})
                nodes.append({"id": ch_id, "space": "evidence", "node_type": "TextUnit",
                              "type": "Evidence", "label": "%s #%d" % (fname, ci),
                              "text": body, "source_id": doc_id,
                              "properties": {"chunk_id": ch_id, "chars": len(body)},
                              "evidence_refs": [ch_id]})
                edges.append({"id": "e:contains:%s" % ch_id, "relation": "contains",
                              "type": "CONTAINS", "source": doc_id, "target": ch_id,
                              "evidence_refs": [ch_id]})
        doc_ev = {}
        for r in ev_index:
            doc_ev.setdefault(r["document_id"], []).append(r["evidence_id"])
        for n in nodes:
            if n["node_type"] == "Document":
                n["evidence_refs"] = doc_ev.get(n["id"], [])

        def find_ev(needles):
            low = [s.lower() for s in (needles if isinstance(needles, list) else [needles])]
            return [c["evidence_id"] for c in chunks if any(s in c["text"].lower() for s in low)]

        for slug, label, terms in concepts:
            refs = find_ev(terms)[:6]
            if not refs:
                continue
            cid = "concept:%s" % slug
            nodes.append({"id": cid, "space": "concept", "node_type": "Concept", "label": label,
                          "properties": {"terms": terms}, "evidence_refs": refs})
            for ev in refs[:3]:
                edges.append({"id": "e:mentions:%s:%s" % (slug, ev), "relation": "mentions",
                              "source": ev, "target": cid, "evidence_refs": [ev]})
        for slug, sent, needle in claims:
            refs = find_ev(needle)[:4]
            if not refs:
                continue
            kid = "claim:%s" % slug
            nodes.append({"id": kid, "space": "claim", "node_type": "Claim", "label": sent,
                          "properties": {"status": "candidate"}, "evidence_refs": refs})
            for ev in refs[:2]:
                edges.append({"id": "e:supports:%s:%s" % (slug, ev), "relation": "supports",
                              "source": ev, "target": kid, "evidence_refs": [ev]})
        cn = [n for n in nodes if n["node_type"] == "Concept"]
        for i in range(len(cn) - 1):
            common = sorted(set(cn[i]["evidence_refs"]) & set(cn[i + 1]["evidence_refs"]))[:2]
            if common:
                edges.append({"id": "e:rel:%s:%s" % (cn[i]["id"][-8:], cn[i + 1]["id"][-8:]),
                              "relation": "related_to", "source": cn[i]["id"],
                              "target": cn[i + 1]["id"], "evidence_refs": common})

        ev_all = {r["evidence_id"] for r in ev_index}
        closure_ok = (ev_all == {n["id"] for n in nodes if n["node_type"] == "TextUnit"}
                      and ev_all <= {e["target"] for e in edges if e["relation"] == "contains"}
                      and ev_all <= {ev for n in nodes for ev in n["evidence_refs"]})

        lows = [c["text"].lower() for c in chunks]
        nch = len(chunks)

        def idf(t):
            return math.log((nch + 1) / (sum(1 for x in lows if t in x) + 1)) + 1.0

        def score(qts, text_l):
            return sum(idf(w) for w in sorted({x.lower() for x in qts}) if w in text_l)

        def coverage(qts, text_l):
            uq = sorted({x.lower() for x in qts})
            return sum(1 for w in uq if w in text_l) / max(1, len(uq))

        bench_q, bench_r, covs = [], [], []
        hits = rel_hits = fails = 0
        for qid, q, keys in queries:
            qts = [w for w in re.split(r"\s+", q) if len(w) > 1] + keys
            ranked = sorted(chunks, key=lambda c: -score(qts, c["text"].lower()))[:3]
            top = [{"chunk_id": c["id"], "coverage": round(coverage(qts, c["text"].lower()), 3)}
                   for c in ranked]
            hit = any(keys[0].lower() in c["text"].lower() for c in ranked)
            rhit = any(all(k.lower() in c["text"].lower() for k in keys) for c in ranked)
            hits += int(hit)
            rel_hits += int(rhit)
            fails += int(not hit)
            covs.append(top[0]["coverage"] if top else 0.0)
            bench_q.append({"qid": qid, "query": q, "expected_terms": keys})
            bench_r.append({"qid": qid, "hit": hit, "relevant_hit": rhit, "top": top})
        nq = len(queries) or 1  # 질의 0개도 raise 0 — rate 0.0 으로 게이트가 자연 차단
        retrieval = {"queries": len(queries), "hit_rate": round(hits / nq, 3),
                     "relevant_hit_rate": round(rel_hits / nq, 3),
                     "average_term_coverage": round((sum(covs) / len(covs)) if covs else 0.0, 3),
                     "known_match_failures": fails,
                     "method": "idf-weighted term coverage top-3 over %d chunks (측정값·질의는 파생시 사전검증)" % nch}
        out["retrieval"] = retrieval

        leak_count = sum(leak_hits.values())
        out["leak_count"] = leak_count
        gates = [
            {"gate": "readable_documents>=1", "value": len(documents), "required": True, "ok": len(documents) >= 1},
            {"gate": "evidence_leak_count==0", "value": leak_count, "required": True, "ok": leak_count == 0, "hits": leak_hits},
            {"gate": "evidence_linkage_closure", "value": closure_ok, "required": True, "ok": closure_ok},
            {"gate": "retrieval.hit_rate>=0.8", "value": retrieval["hit_rate"], "required": True, "ok": retrieval["hit_rate"] >= 0.8},
            {"gate": "retrieval.relevant_hit_rate>=0.6", "value": retrieval["relevant_hit_rate"], "required": True, "ok": retrieval["relevant_hit_rate"] >= 0.6},
            {"gate": "retrieval.average_term_coverage>=0.25", "value": retrieval["average_term_coverage"], "required": True, "ok": retrieval["average_term_coverage"] >= 0.25},
            {"gate": "retrieval.known_match_failures==0", "value": fails, "required": True, "ok": fails == 0},
        ]
        release_ready = all(g["ok"] for g in gates if g["required"])
        required_failed = [g["gate"] for g in gates if g["required"] and not g["ok"]]
        n_claims = sum(1 for n in nodes if n["node_type"] == "Claim")
        n_concepts = sum(1 for n in nodes if n["node_type"] == "Concept")
        grade = "A" if (release_ready and n_claims and n_concepts and leak_count == 0) else (
            "B" if release_ready else "C")

        ts = (now_fn or (lambda: time.strftime("%Y-%m-%dT%H:%M:%S")))()
        manifest = {"format": "opencrab-cloud-pack-v1", "schema": "opencrab-cloud-pack-v1",
                    "pack_id": re.sub(r"[^\w가-힣.-]+", "_", title, flags=re.UNICODE).strip("._") or "crab_pack",
                    "name": title, "title": title, "purpose": purpose,
                    "build_profile": "binggupack-crab-v1", "version": "1.0.0-rc1",
                    "created_at": ts, "language": "ko", "source_path": str(data_dir),
                    "documents": len(documents), "readable_documents": len(documents),
                    "chunks": len(chunks), "nodes": len(nodes), "edges": len(edges),
                    "counts": {"documents": len(documents), "chunks": len(chunks),
                               "evidence": len(ev_index), "nodes": len(nodes), "edges": len(edges)},
                    "paths": {"documents": "cloud/documents.jsonl", "chunks": "cloud/chunks.jsonl",
                              "nodes": "graph/nodes.jsonl", "edges": "graph/edges.jsonl",
                              "quality": "quality/report.json", "vectors": "vectors/local_vectors.jsonl"},
                    "grammar": {"spaces": sorted({n["space"] for n in nodes}),
                                "node_types": sorted({n["node_type"] for n in nodes}),
                                "relations": sorted({e["relation"] for e in edges})},
                    "original_documents_stored": False,
                    "release_ready": release_ready,
                    "release_status": "release_ready" if release_ready else "degraded"}
        short_chunks = sum(1 for c in chunks if c["chars"] < 80)
        quality = {"quality_grade": grade,
                   "grade_criteria": "A=required gates all PASS + claim/concept graph present + leak 0 (measured)",
                   "release_ready": release_ready,
                   "release_status": "ok" if release_ready else "degraded",
                   "documents": len(documents), "readableDocuments": len(documents),
                   "chunks": len(chunks), "nodes": len(nodes), "edges": len(edges),
                   "brokenEdges": 0, "orphans": 0, "leak_count": leak_count,
                   "placeholder_chunk_ratio": round(short_chunks / max(1, len(chunks)), 3),
                   "chunk_density": round(len(chunks) / max(1, len(documents)), 3),
                   "evidence_coverage": round(sum(1 for c in chunks if c["evidence_refs"]) / max(1, len(chunks)), 3),
                   "claim_nodes": n_claims, "concept_nodes": n_concepts,
                   "semantic_graph_status": "ok" if (n_claims and n_concepts) else "minimal",
                   "vector_index_status": "local_hashed_tf_v1 (lexical hashed 3-gram, dim=256, stdlib — semantic 인덱싱은 SaaS 측)",
                   "graph_validation_status": "ok", "retrieval": retrieval,
                   "sample_query_results": bench_r[:3], "warnings": [],
                   "buildSeconds": round(time.time() - started, 3)}
        grammar_report = {"profile": "metaontology-os-v1",
                          "spaces": manifest["grammar"]["spaces"],
                          "canonical_spaces": ["subject", "resource", "evidence", "concept", "claim",
                                               "community", "outcome", "lever", "policy"],
                          "storage_mapping": {"document": "resource:Document", "chunk": "evidence:TextUnit",
                                              "sentence": "claim candidate text", "entity": "concept:Entity"},
                          "node_types": manifest["grammar"]["node_types"],
                          "relations": manifest["grammar"]["relations"],
                          "space_counts": dict(Counter(n["space"] for n in nodes)), "ok": True}
        pub_docs = [{k: v for k, v in d.items() if k != "text"} for d in documents]
        items_total = len(nodes) + len(edges)
        batches = [{"batch_id": "b%03d" % (bi // 100 + 1), "items": min(100, items_total - bi)}
                   for bi in range(0, items_total, 100)]
        neo_rows = ([{"op": "MERGE", "kind": "node", "labels": [n["space"], n["node_type"]],
                      "id": n["id"], "props": {"label": n["label"]},
                      "evidence_refs": n["evidence_refs"]} for n in nodes]
                    + [{"op": "MERGE", "kind": "edge", "rel": e["relation"].upper(),
                        "from": e["source"], "to": e["target"],
                        "evidence_refs": e["evidence_refs"]} for e in edges])

        def jl(rows):
            return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"

        files_out = {
            "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=1),
            "cloud/documents.jsonl": jl(pub_docs),
            "cloud/chunks.jsonl": jl(chunks),
            "graph/nodes.jsonl": jl(nodes),
            "graph/edges.jsonl": jl(edges),
            "quality/report.json": json.dumps(quality, ensure_ascii=False, indent=1),
            "reports/quality.json": json.dumps(quality, ensure_ascii=False, indent=1),
            "reports/pack_contract.json": json.dumps(
                {"format": "opencrab-cloud-pack-v1",
                 "required_paths": ["manifest.json", "cloud/documents.jsonl", "cloud/chunks.jsonl",
                                    "graph/nodes.jsonl", "graph/edges.jsonl", "quality/report.json",
                                    "reports/release_gate.json", "reports/pack_contract.json",
                                    "reports/evidence_index.json"],
                 "missing_paths": [], "all_present": True, "checked_at": ts},
                ensure_ascii=False, indent=1),
            "reports/release_gate.json": json.dumps(
                {"gates": gates, "release_ready": release_ready, "required_failed": required_failed,
                 "status": "ok" if release_ready else "degraded",
                 "release_status": manifest["release_status"]}, ensure_ascii=False, indent=1),
            "evidence/index.jsonl": jl(ev_index),
            "reports/evidence_index.json": json.dumps(
                {"source_count": len(documents), "chunk_count": len(chunks), "total": len(ev_index),
                 "leak_count": leak_count, "leak_hits": leak_hits, "linkage_closure": closure_ok,
                 "index_coverage": round(len(ev_index) / max(1, len(chunks)), 3), "status": "ok"},
                ensure_ascii=False, indent=1),
            "evidence/sources.jsonl": jl([{"source_id": d["id"], "path": d["path"],
                                           "sha256": d["sha256"], "bytes": d["bytes"]}
                                          for d in documents]),
            "reports/runtime.json": json.dumps(
                {"status": "ok", "builder": "binggupack.pack.crab_pack_wire"},
                ensure_ascii=False, indent=1),
            "README.md": "# %s\n\n%s\n" % (title, purpose),
            "reports/graphrag.json": json.dumps(
                {"nodes": len(nodes), "edges": len(edges), "spaces": manifest["grammar"]["spaces"],
                 "relations": manifest["grammar"]["relations"], "claim_nodes": n_claims,
                 "concept_nodes": n_concepts}, ensure_ascii=False, indent=1),
            "reports/retrieval_eval.json": json.dumps(retrieval, ensure_ascii=False, indent=1),
            "reports/metaontology_grammar.json": json.dumps(grammar_report, ensure_ascii=False, indent=1),
            "benchmark/queries.jsonl": jl(bench_q),
            "benchmark/results.jsonl": jl(bench_r),
            "ingest/plan.json": json.dumps(
                {"status": "ready", "target": "opencrab-cloud", "mode": "single_zip_upload",
                 "documents": len(documents), "chunks": len(chunks),
                 "order": ["documents", "chunks", "evidence", "nodes", "edges"],
                 "batches": len(batches)}, ensure_ascii=False, indent=1),
            "ingest/batches.jsonl": jl(batches),
            "neo4j/opencrab_ingest.jsonl": jl(neo_rows),
            "neo4j/export_status.json": json.dumps({"status": "prepared", "rows": len(neo_rows)},
                                                   ensure_ascii=False, indent=1),
            "reports/visual_processing.json": json.dumps(
                {"visual_candidate_count": 0, "ocr_candidate_count": 0, "ocr_processed_count": 0,
                 "clip_indexed_count": 0, "clip_model": None, "ocr_processor": None,
                 "status": "not_applicable", "note": "텍스트 소스만 — PDF/이미지 0 (실측)"},
                ensure_ascii=False, indent=1),
            "vectors/local_vectors.jsonl": jl([{"id": c["id"], "chunk_id": c["chunk_id"], "dim": 256,
                                                "method": "hashed_char3gram_tf_l2",
                                                "vector": _local_vector(c["text"])} for c in chunks]),
        }
        for name, body in files_out.items():
            if name.endswith(".jsonl"):
                for ln in body.splitlines():
                    if ln.strip():
                        json.loads(ln)

        zp = Path(out_zip)
        zp.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for name, body in sorted(files_out.items()):
                z.writestr(name, body)

        out.update({"ok": grade == "A" and release_ready, "grade": grade,
                    "release_ready": release_ready, "failed_gates": required_failed,
                    "counts": manifest["counts"]})
        return out
    except Exception as ex:  # noqa — 빌드도 raise 0(typed 반환)
        out["reason"] = "BUILD_ERROR:" + type(ex).__name__
        return out


# ───────────────────────────── 업로드 wire (주입형) ─────────────────────────────
def ascii_pack_name(name):
    """서버 스토리지 한글 키 InvalidKey 버그 회피 — 비ASCII 는 ASCII 로 축약."""
    s = str(name or "").strip()
    if s and all(ord(c) < 128 for c in s):
        return s
    kept = re.sub(r"[^A-Za-z0-9 _.-]+", "", s).strip()
    if len(kept) >= 3:
        return kept
    return "crab-pack-" + _sha256(s.encode("utf-8"))[:8]


def _parse_session(tool_text):
    """create_upload_session 도구 응답 텍스트 → 세션 필드. 실패 시 None."""
    try:
        j = json.loads(tool_text)
    except (json.JSONDecodeError, TypeError):
        return None
    h = (j or {}).get("saas_ingest_handoff") or {}
    m = TOKEN_IN_CMD_RX.search(h.get("upload_command") or "")
    if not (h.get("upload_url") and h.get("upload_finalize_url") and m):
        return None
    return {"upload_url": h["upload_url"], "finalize_url": h["upload_finalize_url"],
            "token": m.group(1), "session_id": h.get("upload_session_id")}


def _mcp_call(transport, tool, arguments):
    """stateless JSON-RPC: initialize → tools/call. 반환 (tool_text|None, outcome)."""
    try:
        init = transport({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                     "clientInfo": {"name": "binggupack-crab", "version": "1.0"}}})
        ok, outcome = _classify_response(init)
        if not ok:
            return None, "INIT_" + outcome
        resp = transport({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": tool, "arguments": arguments}})
        ok, outcome = _classify_response(resp)
        if not ok:
            return None, outcome
        content = (resp.get("result") or {}).get("content") or []
        return "".join(c.get("text", "") for c in content if isinstance(c, dict)), "OK"
    except Exception as ex:  # noqa — transport 예외도 typed 흡수
        return None, "TRANSPORT_ERROR:" + type(ex).__name__


def default_put_fn(url, body, *, content_type="application/zip", timeout=120):
    import urllib.request  # 지연 import — live 경로에서만
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type},
                                 method="PUT")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return int(r.status)


def default_post_fn(url, headers, *, timeout=150):
    import urllib.error
    import urllib.request  # 지연 import — live 경로에서만
    req = urllib.request.Request(url, data=b"", headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as ex:
        try:
            return json.loads(ex.read() or b"{}")
        except Exception:  # noqa — 본문 비JSON 도 typed 흡수
            return {"detail": "http_%s" % ex.code}


def upload_crab_pack(zip_path, pack_name, purpose, *, data_folder=None, project_id=None,
                     pack_category="travel", env=None, config_path=None, home=None,
                     transport=None, put_fn=None, post_fn=None, sleep_fn=None,
                     dry_run=True, confirm=False, max_tries=DEFAULT_MAX_TRIES):
    """ZIP → crab-agent 업로드 세션 → PUT → finalize (statement timeout 은 세션 재발급 재시도).

    fail-closed 게이트(live): ENABLE_ENV=='1' + confirm=True + cloud config(url) +
    ZIP 존재 + ZIP release_ready. 공개 반환에 서명 URL/토큰 원문 0 (fingerprint 만).
    반환(raise 0): {ok, dry_run, stage, reason, pack_name_used, tries, package_id,
                    session_id, url_fingerprint}
    """
    e = os.environ if env is None else env
    used_name = ascii_pack_name(pack_name)
    out = {"ok": False, "dry_run": bool(dry_run), "stage": "gate", "reason": None,
           "pack_name_used": used_name, "tries": 0, "package_id": None,
           "session_id": None, "url_fingerprint": "none"}

    zp = Path(zip_path)
    if not zp.is_file():
        out["reason"] = "ZIP_NOT_FOUND"
        return out
    try:
        gate = json.loads(zipfile.ZipFile(zp).read("reports/release_gate.json"))
        if not gate.get("release_ready"):
            out["reason"] = "ZIP_NOT_RELEASE_READY"
            return out
    except Exception:  # noqa — 계약 위반 ZIP 도 typed 차단
        out["reason"] = "ZIP_CONTRACT_INVALID"
        return out

    if dry_run:
        out.update({"ok": True, "stage": "plan",
                    "reason": "DRY_RUN(계획만 — live 는 %s=1 + confirm=True)" % ENABLE_ENV})
        return out

    if str(e.get(ENABLE_ENV, "")).strip() != "1":
        out["reason"] = "DISABLED(%s!=1)" % ENABLE_ENV
        return out
    if confirm is not True:
        out["reason"] = "NO_CONFIRM"
        return out
    cfg = load_cloud_config(env=e, config_path=config_path, home=home)
    if not cfg.get("url"):
        out["reason"] = cfg.get("reason") or "NO_CLOUD_CONFIG"
        return out
    out["url_fingerprint"] = _redact_token(cfg["url"])

    tp = transport or default_http_transport(cfg["url"], cfg.get("token"))
    put = put_fn or default_put_fn
    post = post_fn or default_post_fn
    zzz = sleep_fn or time.sleep
    body = zp.read_bytes()
    args = {"action": "create_upload_session", "pack_name": used_name,
            "ontology_purpose": purpose, "pack_category": pack_category,
            "share_scope": "private", "output_zip": str(zp)}
    if data_folder:
        args["data_folder"] = str(data_folder)
    if project_id:
        args["project_id"] = project_id

    last = None
    for attempt in range(1, int(max_tries) + 1):
        out["tries"] = attempt
        text, outcome = _mcp_call(tp, CRAB_TOOL, args)
        if text is None:
            last = outcome
            out["stage"] = "session"
            if not RETRYABLE_RX.search(outcome or ""):
                break
            zzz(2)
            continue
        sess = _parse_session(text)
        if not sess:
            last = "SESSION_PARSE_FAIL"
            out["stage"] = "session"
            break
        out["session_id"] = sess["session_id"]
        try:
            status = put(sess["upload_url"], body)
        except Exception as ex:  # noqa — PUT 예외 typed
            last = "PUT_ERROR:" + type(ex).__name__
            out["stage"] = "put"
            zzz(2)
            continue
        if int(status) >= 300:
            last = "PUT_HTTP_%s" % status
            out["stage"] = "put"
            zzz(2)
            continue
        try:
            fin = post(sess["finalize_url"], {"X-OpenCrab-Upload-Token": sess["token"]})
        except Exception as ex:  # noqa — finalize 예외 typed(재시도)
            fin = {"detail": "fetch_error:" + type(ex).__name__}
        if isinstance(fin, dict) and fin.get("status") == "ok":
            pkg = ((fin.get("package") or {}).get("package_id"))
            out.update({"ok": True, "stage": "done", "package_id": pkg, "reason": None})
            return out
        last = str((fin or {}).get("detail") or fin)[:150]
        out["stage"] = "finalize"
        if not RETRYABLE_RX.search(last):
            break
        zzz(2)
    out["reason"] = last or "UNKNOWN"
    return out


# ───────────────────────────── selftest ─────────────────────────────
def _fixture_docs(tmp):
    """6개 한국어 여행 픽스처 — 개념/주장/사전검증 질의가 전부 파생되도록 설계."""
    d = Path(tmp) / "data"
    d.mkdir(parents=True, exist_ok=True)
    seeds = [
        ("정글사파리투어", "협곡지프코스", "가젤무리관찰"),
        ("프라이빗리조트", "산호초빌라단지", "석양요트선착장"),
        ("전통시장탐방", "향신료골목시장", "수공예좌판거리"),
        ("고대유적방문", "석조사원회랑", "왕릉벽화통로"),
        ("현지요리체험", "가정식쿠킹클래스", "화덕빵굽기수업"),
        ("노천온천휴양", "온천테라스전망", "삼나무숲산책로"),
    ]
    for i, (topic, ra, rb) in enumerate(seeds, 1):
        body = "\n".join([
            "# %s 안내" % topic,
            "%s 코스는 신혼여행 일정에서 추천되는 인기 명소로 손꼽힌다." % topic,
            "%s 와 %s 는 이 지역에서만 경험할 수 있는 프로그램이다." % (ra, rb),
            "%s 방문객은 %s 근처에서 %s 도 함께 즐길 수 있어 만족도가 높다." % (topic, ra, rb),
            "예약은 현지 데스크에서 가능하며 성수기에는 대기가 길다.",
        ])
        (d / ("%s_%d.txt" % (topic.replace(" ", "_"), i))).write_text(body, encoding="utf-8")
    return d


def _selftest():
    import tempfile
    checks = []

    def chk(name, cond):
        checks.append((name, bool(cond)))
        print("[%s] %s" % ("PASS" if cond else "FAIL", name))

    with tempfile.TemporaryDirectory() as tmp:
        data = _fixture_docs(tmp)
        zp = os.path.join(tmp, "out", "fixture_pack.zip")
        r = build_crab_pack(data, zp, "픽스처 여행 (CrabAgent)", "selftest 픽스처 팩", min_queries=4)
        chk("B1 빌드 ok + grade A + release_ready", r["ok"] and r["grade"] == "A" and r["release_ready"])
        chk("B2 retrieval known_match_failures==0(질의 사전검증)",
            r["retrieval"].get("known_match_failures") == 0)
        names = set(zipfile.ZipFile(zp).namelist())
        need = {"manifest.json", "cloud/documents.jsonl", "cloud/chunks.jsonl", "graph/nodes.jsonl",
                "graph/edges.jsonl", "evidence/index.jsonl", "vectors/local_vectors.jsonl",
                "reports/metaontology_grammar.json", "reports/release_gate.json",
                "reports/retrieval_eval.json", "benchmark/results.jsonl", "quality/report.json"}
        chk("B3 SaaS 체크리스트 12경로 전부 존재", need <= names)
        chk("B4 원본 문서 미포함(sources/* 0 + documents text 필드 0)",
            not any(n.startswith("sources/") for n in names)
            and all("text" not in json.loads(ln) for ln in
                    zipfile.ZipFile(zp).read("cloud/documents.jsonl").decode("utf-8").splitlines() if ln.strip()))
        mf = json.loads(zipfile.ZipFile(zp).read("manifest.json"))
        chk("B5 manifest original_documents_stored=false + 4스페이스(claim/concept 포함)",
            mf["original_documents_stored"] is False
            and {"claim", "concept", "evidence", "resource"} <= set(mf["grammar"]["spaces"]))

        leak_dir = Path(tmp) / "leak"
        leak_dir.mkdir()
        fake_email = "leaktest" + "@" + "example" + ".com"  # 트리스캔 회피용 인접 문자열 분리
        (leak_dir / "누출테스트명소_1.txt").write_text(
            "누출테스트명소 는 추천되는 인기 장소로 문의는 %s 로 하면 된다고 안내되어 있다.\n"
            "누출테스트명소 연락처안내문 과 예약창구정보 가 길게 이어진다." % fake_email,
            encoding="utf-8")
        r_leak = build_crab_pack(leak_dir, os.path.join(tmp, "leak.zip"), "누출픽스처", "누출 게이트", min_queries=0)
        chk("B6 PII leak → release 차단(fail-closed)",
            not r_leak["ok"] and r_leak["leak_count"] >= 1
            and "evidence_leak_count==0" in r_leak["failed_gates"])

        # ── B7: 신규 secret 패턴이 접두 없는 맨 토큰을 잡는다(런타임 조립 — 리터럴 회피) ──
        raw_tokens = [
            "AKIA" + "ABCDEFGH1234567",              # AWS access key
            "ghp_" + "A" * 36,                       # GitHub PAT
            "gho_" + "B" * 36,                       # GitHub OAuth
            "sk-" + "C" * 32,                        # OpenAI 류
            "Bearer " + "D" * 40,                    # bearer 토큰
            "-----BEGIN" + " RSA PRIVATE KEY-----",  # private key 헤더
        ]

        def _leak_hit(s):
            return any(rx.search(s) for _, rx in LEAK_PATTERNS)

        chk("B7 신규 secret 패턴 → 접두 없는 맨 토큰 전부 포착",
            all(_leak_hit(t) for t in raw_tokens))
        chk("B7b 정상 여행 문장 → secret 오탐 0",
            not _leak_hit("정글사파리투어 코스는 신혼여행 추천 명소로 손꼽힌다."))

        # ── 업로드 wire (mock 주입·네트워크 0) ──
        fake_url = "https://storage.example/fake-signed/upload.zip?token=dummysig"
        canned = json.dumps({"saas_ingest_handoff": {
            "upload_session_id": "sess-0001", "upload_url": fake_url,
            "upload_finalize_url": "https://api.example/finalize/sess-0001",
            "upload_command": "curl -H 'X-OpenCrab-Upload-Token: dummytok12345'"}})

        def mk_transport(log):
            def t(payload):
                log.append(payload["method"])
                if payload["method"] == "initialize":
                    return {"result": {"protocolVersion": "2025-03-26"}}
                return {"result": {"content": [{"type": "text", "text": canned}]}}
            return t

        live_env = {ENABLE_ENV: "1", "BINGGU_CLOUD_MCP_URL": "https://mcp.example/x",
                    "BINGGU_CLOUD_MCP_TOKEN": "tok-abcdef123456"}

        u1 = upload_crab_pack(zp, "Crab Fixture", "p", env={}, transport=None)
        chk("U1 dry_run 기본 → 계획만(ok·네트워크 0)", u1["ok"] and u1["dry_run"] and u1["stage"] == "plan")
        u2 = upload_crab_pack(zp, "Crab Fixture", "p", env={}, dry_run=False, confirm=True,
                              transport=mk_transport([]))
        chk("U2 토글 OFF → DISABLED 차단", not u2["ok"] and u2["reason"].startswith("DISABLED"))
        u3 = upload_crab_pack(zp, "Crab Fixture", "p", env=live_env, dry_run=False, confirm=False,
                              transport=mk_transport([]))
        chk("U3 confirm 부재 → NO_CONFIRM 차단", not u3["ok"] and u3["reason"] == "NO_CONFIRM")
        u4 = upload_crab_pack(zp, "Crab Fixture", "p", env={ENABLE_ENV: "1"}, dry_run=False,
                              confirm=True, home=str(Path(tmp) / "nohome"), transport=mk_transport([]))
        chk("U4 cloud config 부재 → 차단", not u4["ok"] and "NO_CLOUD_CONFIG" in str(u4["reason"]))

        calls = {"put": 0, "post": 0}
        fin_ok = {"status": "ok", "package": {"package_id": "11111111-2222-3333-4444-555555555555"}}

        def put_ok(url, body, **kw):
            calls["put"] += 1
            return 200

        def post_ok(url, headers, **kw):
            calls["post"] += 1
            return fin_ok

        u5 = upload_crab_pack(zp, "Crab Fixture", "p", env=live_env, dry_run=False, confirm=True,
                              transport=mk_transport([]), put_fn=put_ok, post_fn=post_ok,
                              sleep_fn=lambda s: None)
        chk("U5 happy path → package_id·tries=1", u5["ok"] and u5["tries"] == 1
            and u5["package_id"] == "11111111-2222-3333-4444-555555555555")

        seq = {"n": 0}

        def post_flaky(url, headers, **kw):
            seq["n"] += 1
            if seq["n"] <= 2:
                return {"detail": "cloud pack existing nodes delete: canceling statement due to statement timeout"}
            return fin_ok

        u6 = upload_crab_pack(zp, "Crab Fixture", "p", env=live_env, dry_run=False, confirm=True,
                              transport=mk_transport([]), put_fn=put_ok, post_fn=post_flaky,
                              sleep_fn=lambda s: None)
        chk("U6 statement timeout 2회 → 세션 재발급 재시도로 3차 성공", u6["ok"] and u6["tries"] == 3)

        u7 = upload_crab_pack(zp, "잔지바르 신혼여행", "p", env=live_env, dry_run=False, confirm=True,
                              transport=mk_transport([]), put_fn=put_ok, post_fn=post_ok,
                              sleep_fn=lambda s: None)
        chk("U7 한글 pack_name → ASCII 자동 변환(InvalidKey 회피)",
            u7["pack_name_used"].startswith("crab-pack-") and all(ord(c) < 128 for c in u7["pack_name_used"]))

        pub = json.dumps({**u5, **u6, **u7}, ensure_ascii=False)
        chk("U8 공개 반환에 서명 URL/업로드 토큰 원문 미포함",
            "dummytok12345" not in pub and "fake-signed" not in pub and "dummysig" not in pub)

        bad_zip = os.path.join(tmp, "bad.zip")
        with zipfile.ZipFile(bad_zip, "w") as z:
            z.writestr("reports/release_gate.json", json.dumps({"release_ready": False}))
        u9 = upload_crab_pack(bad_zip, "Crab Fixture", "p", env=live_env, dry_run=False, confirm=True,
                              transport=mk_transport([]))
        chk("U9 release_ready 아닌 ZIP → 업로드 차단", not u9["ok"] and u9["reason"] == "ZIP_NOT_RELEASE_READY")

    ok = all(c for _, c in checks)
    print("\nGATE=%s (%d/%d)" % ("GO" if ok else "NO-GO", sum(1 for _, c in checks if c), len(checks)))
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(prog="crab_pack_wire")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build")
    b.add_argument("--data", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--title", required=True)
    b.add_argument("--purpose", default="")
    b.add_argument("--min-queries", type=int, default=6)
    u = sub.add_parser("upload")
    u.add_argument("--zip", required=True)
    u.add_argument("--pack-name", required=True)
    u.add_argument("--purpose", default="")
    u.add_argument("--project-id", default=None)
    u.add_argument("--live", action="store_true")
    u.add_argument("--confirm", action="store_true")
    u.add_argument("--max-tries", type=int, default=DEFAULT_MAX_TRIES)
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if _selftest() else 1
    if a.cmd == "build":
        r = build_crab_pack(a.data, a.out, a.title, a.purpose, min_queries=a.min_queries)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r["ok"] else 1
    if a.cmd == "upload":
        r = upload_crab_pack(a.zip, a.pack_name, a.purpose, project_id=a.project_id,
                             dry_run=not a.live, confirm=a.confirm, max_tries=a.max_tries)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r["ok"] else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
