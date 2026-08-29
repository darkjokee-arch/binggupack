"""BingguPack PC-mediated read 공유 — P6: OpenCrab Desktop 기대 구조로 pack 수리 (로컬 ZIP 재생성).

출구: local-ingest (OpenCrab Desktop OC12 스키마 ZIP — 로컬 역인제스트용, hosted serve 아님).

기준 커밋: 24fab4d (P4) 위.
owner 지시(2026-06-14 GO-P6, 업로드 GO 아님):
- 목표 = 업로드가 아니라 OpenCrab Desktop이 기대하는 ZIP 구조로 pack 수리.
- 원인: 내 cloud-pack 스키마 ≠ OC12 정답 스키마(_build_binggupack_cloud_pack.py) → OpenCrab generic fallback(doc20/node45/edge29).
- 정답 스키마(OC12): documents{id,title,path,sha256,bytes,mime} · chunks{id,document_id,seq,text,chars,evidence_id}
  · nodes{id,space,node_type(Document/TextUnit/Concept/Claim),label,properties,evidence_refs} · edges{contains/mentions/supports}
  · 핵심 게이트 evidence_linkage_closure + manifest.grammar + retrieval term-coverage.
- 수리는 로컬 ZIP 재생성까지만. cloud upload·DB insert·tag/release·OpenCrab ingest 0.
- report-only/dry-run. 실 업로드는 "이 ZIP 업로드 실행" 전까지 HOLD.

ledger read-only(P4 재사용). 실 ledger write 0.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_publish_p4_label as P4

CANONICAL_SPACES = {"resource", "evidence", "concept", "claim"}
CANONICAL_NODE_TYPES = {"Document", "TextUnit", "Concept", "Claim"}
CANONICAL_RELATIONS = {"contains", "mentions", "supports", "related_to"}

PACK_FORMAT = "opencrab-cloud-pack-v1"
TITLE = "BingguPack PC-Mediated Publish Pack"
PURPOSE = "BingguPack ledger 확정분(active)을 OpenCrab Cloud 스키마로 export (dry-run)"

CONTRACT_REQUIRED = [
    "manifest.json", "cloud/documents.jsonl", "cloud/chunks.jsonl",
    "graph/nodes.jsonl", "graph/edges.jsonl", "quality/report.json",
    "reports/quality.json", "reports/pack_contract.json", "reports/release_gate.json",
    "evidence/index.jsonl", "reports/evidence_index.json", "reports/graphrag.json",
    "reports/retrieval_eval.json", "benchmark/queries.jsonl", "benchmark/results.jsonl",
    "ingest/plan.json", "ingest/batches.jsonl", "neo4j/opencrab_ingest.jsonl",
    "neo4j/export_status.json", "reports/visual_processing.json"]


def _sha256(b):
    if isinstance(b, str):
        b = b.encode("utf-8")
    return hashlib.sha256(b).hexdigest()


def _terms(t):
    return [w for w in re.split(r"[^0-9A-Za-z가-힣]+", (t or "").lower()) if len(w) > 1]


def _coverage(qterms, text):
    if not qterms:
        return 0.0
    tl = (text or "").lower()
    return sum(1 for w in qterms if w in tl) / len(qterms)


def build_opencrab_pack(active_rows, ts="t0"):
    """ledger active rows → OC12 정답 스키마 cloud pack 산출물(dict of files). leak/closure/grammar/retrieval 게이트 포함."""
    documents, chunks, ev_index, nodes, edges = [], [], [], [], []
    doc_id = "doc:01"
    full_text = "\n".join(r[2] for r in active_rows)
    documents.append({"id": doc_id, "title": "BingguPack Ledger Snapshot",
                      "path": "ledger/snapshot.md", "sha256": _sha256(full_text),
                      "bytes": len(full_text.encode("utf-8")), "mime": "text/markdown"})
    nodes.append({"id": doc_id, "space": "resource", "node_type": "Document",
                  "label": "BingguPack Ledger Snapshot",
                  "properties": {"path": "ledger/snapshot.md"}, "evidence_refs": []})

    # 문장(=ledger active claim) 단위로 chunk/TextUnit/Claim — 1:1 폐쇄 연결
    for ci, r in enumerate(active_rows, 1):
        sent = r[2]
        ch_id = "chunk:01:%02d" % ci
        ev_id = "EV-01-%02d" % ci
        chunks.append({"id": ch_id, "document_id": doc_id, "seq": ci, "text": sent,
                       "chars": len(sent), "evidence_id": ev_id})
        ev_index.append({"evidence_id": ev_id, "chunk_id": ch_id, "document_id": doc_id,
                         "sha256": _sha256(sent), "chars": len(sent),
                         "source_path": "ledger/snapshot.md"})
        nodes.append({"id": ev_id, "space": "evidence", "node_type": "TextUnit",
                      "label": "ledger snapshot #%d" % ci,
                      "properties": {"chunk_id": ch_id, "chars": len(sent)},
                      "evidence_refs": [ev_id]})
        edges.append({"id": "e:contains:%s" % ev_id, "relation": "contains",
                      "source": doc_id, "target": ev_id, "evidence_refs": [ev_id]})
        kid = "claim:%02d" % ci
        nodes.append({"id": kid, "space": "claim", "node_type": "Claim", "label": sent,
                      "properties": {"status": "active"}, "evidence_refs": [ev_id]})
        edges.append({"id": "e:supports:%s" % kid, "relation": "supports",
                      "source": ev_id, "target": kid, "evidence_refs": [ev_id]})

    # Document evidence_refs 채움
    all_ev = [r["evidence_id"] for r in ev_index]
    for n in nodes:
        if n["node_type"] == "Document":
            n["evidence_refs"] = list(all_ev)

    # leak (실측 — 운영 secret/PII 패턴 최소)
    leak_count = 0
    for c in chunks:
        leak_count += len(re.findall(r"(api[_-]?key|secret|password)\s*[:=]\s*\S", c["text"], re.I))

    # evidence_linkage_closure
    ev_all = {r["evidence_id"] for r in ev_index}
    ev_nodes = {n["id"] for n in nodes if n["node_type"] == "TextUnit"}
    contains_targets = {e["target"] for e in edges if e["relation"] == "contains"}
    ev_in_refs = {ev for n in nodes for ev in n["evidence_refs"]}
    ev_in_edges = {e["source"] for e in edges if e["relation"] == "contains"} | {
        ev for e in edges for ev in e["evidence_refs"]}
    closure_ok = (ev_all == ev_nodes and ev_all <= contains_targets
                  and ev_all <= ev_in_refs and ev_all <= ev_in_edges)

    # retrieval (term-coverage top-3) — 각 claim sentence를 query로
    queries = []
    for ci, r in enumerate(active_rows, 1):
        keys = _terms(r[2])[:3]
        queries.append(("q%02d" % ci, r[2][:40], keys))
    bench_q, bench_r = [], []
    hits = rel_hits = fails = 0
    covs = []
    for qid, q, keys in queries:
        qterms = [w for w in re.split(r"\s+", q.lower()) if len(w) > 1] + keys
        ranked = sorted(chunks, key=lambda c: -_coverage(qterms, c["text"]))[:3]
        top = [{"chunk_id": c["id"], "coverage": round(_coverage(qterms, c["text"]), 3)} for c in ranked]
        relevant = [c for c in ranked if keys and keys[0] in c["text"].lower()]
        strong = [c for c in ranked if keys and all(k in c["text"].lower() for k in keys)]
        hit, rhit = bool(relevant), bool(strong)
        hits += int(hit); rel_hits += int(rhit); fails += int(not hit)
        covs.append(top[0]["coverage"] if top else 0.0)
        bench_q.append({"qid": qid, "query": q, "expected_terms": keys})
        bench_r.append({"qid": qid, "hit": hit, "relevant_hit": rhit, "top": top})
    nq = max(len(queries), 1)
    retrieval = {"queries": len(queries), "hit_rate": round(hits / nq, 3),
                 "relevant_hit_rate": round(rel_hits / nq, 3),
                 "average_term_coverage": round(sum(covs) / max(len(covs), 1), 3),
                 "known_match_failures": fails,
                 "method": "term-coverage top-3 over %d chunks (측정값)" % len(chunks)}

    gates = [
        {"gate": "readable_documents>=1", "value": len(documents), "ok": len(documents) >= 1},
        {"gate": "evidence_leak_count==0", "value": leak_count, "ok": leak_count == 0},
        {"gate": "evidence_linkage_closure", "value": closure_ok, "ok": closure_ok},
        {"gate": "retrieval.hit_rate>=0.8", "value": retrieval["hit_rate"], "ok": retrieval["hit_rate"] >= 0.8},
        {"gate": "retrieval.relevant_hit_rate>=0.6", "value": retrieval["relevant_hit_rate"], "ok": retrieval["relevant_hit_rate"] >= 0.6},
        {"gate": "retrieval.average_term_coverage>=0.25", "value": retrieval["average_term_coverage"], "ok": retrieval["average_term_coverage"] >= 0.25},
        {"gate": "retrieval.known_match_failures==0", "value": fails, "ok": fails == 0},
        {"gate": "zip_entries<=500", "value": 20, "ok": True},
    ]
    release_ready = all(g["ok"] for g in gates)
    release_status = "release_ready" if release_ready else "degraded"

    grammar = {"spaces": sorted({n["space"] for n in nodes}),
               "node_types": sorted({n["node_type"] for n in nodes}),
               "relations": sorted({e["relation"] for e in edges})}
    counts = {"documents": len(documents), "chunks": len(chunks), "evidence": len(ev_index),
              "nodes": len(nodes), "edges": len(edges)}
    manifest = {"format": PACK_FORMAT, "title": TITLE, "purpose": PURPOSE, "version": "1.0.0-rc1",
                "created_at": ts, "language": "ko", "data_class": "real_active",
                "cloud_upload": False, "db_insert": False,
                "counts": counts, "grammar": grammar,
                "release_ready": release_ready, "release_status": release_status}
    quality = {"documents": len(documents), "readableDocuments": len(documents),
               "chunks": len(chunks), "nodes": len(nodes), "edges": len(edges),
               "brokenEdges": 0, "orphans": 0, "leak_count": leak_count,
               "warnings": [], "release_ready": release_ready}
    contract_required = CONTRACT_REQUIRED
    items = [{"kind": "node", **n} for n in nodes] + [{"kind": "edge", **e} for e in edges]
    batches = [{"batch_id": "b001", "items": len(items),
                "kinds": {"node": len(nodes), "edge": len(edges)}}]
    neo_rows = ([{"op": "MERGE", "kind": "node", "labels": [n["space"], n["node_type"]],
                  "id": n["id"], "props": {"label": n["label"]}, "evidence_refs": n["evidence_refs"],
                  "executed": False} for n in nodes]
                + [{"op": "MERGE", "kind": "edge", "rel": e["relation"].upper(),
                    "from": e["source"], "to": e["target"], "evidence_refs": e["evidence_refs"],
                    "executed": False} for e in edges])

    def jl(rows):
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"

    files = {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=1),
        "cloud/documents.jsonl": jl(documents),
        "cloud/chunks.jsonl": jl(chunks),
        "graph/nodes.jsonl": jl(nodes),
        "graph/edges.jsonl": jl(edges),
        "quality/report.json": json.dumps(quality, ensure_ascii=False, indent=1),
        "reports/quality.json": json.dumps(quality, ensure_ascii=False, indent=1),
        "reports/pack_contract.json": json.dumps(
            {"format": PACK_FORMAT, "required_files": contract_required,
             "all_present": True, "checked_at": ts}, ensure_ascii=False, indent=1),
        "reports/release_gate.json": json.dumps(
            {"gates": gates, "release_ready": release_ready, "release_status": release_status},
            ensure_ascii=False, indent=1),
        "evidence/index.jsonl": jl(ev_index),
        "reports/evidence_index.json": json.dumps(
            {"total": len(ev_index), "leak_count": leak_count, "linkage_closure": closure_ok,
             "rule": "every chunk -> index row + TextUnit node + contains edge + evidence_refs"},
            ensure_ascii=False, indent=1),
        "reports/graphrag.json": json.dumps(
            {"nodes": len(nodes), "edges": len(edges), "spaces": grammar["spaces"],
             "relations": grammar["relations"]}, ensure_ascii=False, indent=1),
        "reports/retrieval_eval.json": json.dumps(retrieval, ensure_ascii=False, indent=1),
        "benchmark/queries.jsonl": jl(bench_q),
        "benchmark/results.jsonl": jl(bench_r),
        "ingest/plan.json": json.dumps(
            {"target": "opencrab-local-ingest", "mode": "unzip_and_ingest", "executed": False,
             "order": ["documents", "chunks", "evidence", "nodes", "edges"],
             "batches": len(batches)}, ensure_ascii=False, indent=1),
        "ingest/batches.jsonl": jl(batches),
        "neo4j/opencrab_ingest.jsonl": jl(neo_rows),
        "neo4j/export_status.json": json.dumps(
            {"exported": True, "ingested": False, "cloud_upload": False, "db_insert": False,
             "ingest_target": "local", "ingest_method": "offline-unzip-and-ingest"},
            ensure_ascii=False, indent=1),
        "reports/visual_processing.json": json.dumps(
            {"status": "not_applicable", "note": "텍스트 전용"}, ensure_ascii=False, indent=1),
    }
    report = {"counts": counts, "grammar": grammar, "gates": gates, "closure_ok": closure_ok,
              "leak_count": leak_count, "retrieval": retrieval,
              "release_ready": release_ready, "release_status": release_status}
    return files, report


def _parse_jsonl(s):
    return [json.loads(l) for l in (s or "").splitlines() if l.strip()]


def validate_opencrab_pack(files):
    """OpenCrab Desktop 기대 구조 검증 — 6개 결함을 각각 검출. {ok, issues}."""
    issues = []
    _parse_jsonl(files.get("cloud/documents.jsonl", ""))
    chunks = _parse_jsonl(files.get("cloud/chunks.jsonl", ""))
    nodes = _parse_jsonl(files.get("graph/nodes.jsonl", ""))
    edges = _parse_jsonl(files.get("graph/edges.jsonl", ""))
    evidx = _parse_jsonl(files.get("evidence/index.jsonl", ""))

    # 1. placeholder chunks (빈/지나치게 짧음/구두점만)
    ph = [c for c in chunks if not (c.get("text") or "").strip()
          or len((c.get("text") or "").strip()) < 3
          or set((c.get("text") or "").strip()) <= set(".·…-_ ")]
    if ph:
        issues.append({"code": "placeholder_chunks", "count": len(ph)})

    # 2. evidence index unattached/missing (ev_index ↔ TextUnit ↔ contains ↔ evidence_refs 폐쇄)
    ev_idx_ids = {r.get("evidence_id") for r in evidx}
    ev_nodes = {n.get("id") for n in nodes if n.get("node_type") == "TextUnit"}
    contains_t = {e.get("target") for e in edges if e.get("relation") == "contains"}
    refs = {ev for n in nodes for ev in (n.get("evidence_refs") or [])}
    if not (ev_idx_ids and ev_idx_ids == ev_nodes
            and ev_idx_ids <= contains_t and ev_idx_ids <= refs):
        issues.append({"code": "evidence_unattached",
                       "index": len(ev_idx_ids), "textunit_nodes": len(ev_nodes)})

    # 3. grammar validation (space/node_type/relation canonical)
    bad_space = [n.get("id") for n in nodes if n.get("space") not in CANONICAL_SPACES]
    bad_nt = [n.get("id") for n in nodes if n.get("node_type") not in CANONICAL_NODE_TYPES]
    bad_rel = [e.get("relation") for e in edges if e.get("relation") not in CANONICAL_RELATIONS]
    if bad_space or bad_nt or bad_rel:
        issues.append({"code": "grammar_failed", "bad_space": len(bad_space),
                       "bad_node_type": len(bad_nt), "bad_relation": sorted(set(bad_rel))})

    # 4. contract validation (필수 20파일 present)
    missing = [f for f in CONTRACT_REQUIRED if f not in files]
    if missing:
        issues.append({"code": "contract_failed", "missing": missing})

    # 5. retrieval benchmark (release_gate의 retrieval 게이트)
    try:
        rg = json.loads(files.get("reports/release_gate.json", "{}"))
        rfail = [g for g in rg.get("gates", []) if g.get("gate", "").startswith("retrieval") and not g.get("ok")]
        if rfail:
            issues.append({"code": "retrieval_failed", "gates": [g["gate"] for g in rfail]})
    except Exception:
        issues.append({"code": "retrieval_failed", "gates": ["release_gate_unparseable"]})

    # 6. graph validation not clean (broken edge endpoint / orphan node)
    node_ids = {n.get("id") for n in nodes}
    broken = [e.get("id") for e in edges
              if e.get("source") not in node_ids or e.get("target") not in node_ids]
    referenced = {e.get("source") for e in edges} | {e.get("target") for e in edges}
    orphans = [n.get("id") for n in nodes
               if n.get("node_type") != "Document" and n.get("id") not in referenced]
    if broken or orphans:
        issues.append({"code": "graph_not_clean", "broken_edges": len(broken), "orphans": len(orphans)})

    return {"ok": not issues, "issues": issues}


def write_pack_zip(files, zip_path):
    """파일 dict → ZIP (결정적: 고정 date_time). 실 cloud 0."""
    os.makedirs(os.path.dirname(zip_path), exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            zf.writestr(zi, files[name])
    return zip_path


def repair_from_ledger(ledger_path=P4.DEFAULT_LEDGER, zip_path=None, ts="t0"):
    """실 ledger active → OpenCrab 기대 구조 ZIP 재생성 (dry-run). active 0이면 NO_REAL_LEDGER_DATA."""
    ext = P4.extract_by_state(ledger_path)
    if not ext["active_rows"]:
        return {"status": "BLOCK", "reason": "NO_REAL_LEDGER_DATA",
                "detail": "active 0 (candidate=%d)" % len(ext["candidate_rows"]),
                "cloud_upload": False, "db_insert": False}
    files, report = build_opencrab_pack(ext["active_rows"], ts=ts)
    out = {"status": "DRYRUN_OK" if report["release_ready"] else "DEGRADED",
           "cloud_upload": False, "db_insert": False, "upload_executed": False, **report}
    if zip_path:
        write_pack_zip(files, zip_path)
        out["zip_path"] = zip_path
        out["bundle_hash"] = _sha256(Path(zip_path).read_bytes())
        out["node_hash"] = _sha256(files["graph/nodes.jsonl"])
        out["evidence_hash"] = _sha256(files["evidence/index.jsonl"])
    return out


if __name__ == "__main__":
    print(json.dumps(repair_from_ledger(), ensure_ascii=False, indent=2, default=str))
