# -*- coding: utf-8 -*-
"""binggu_cloud_pack_export.py — 6층 OpenCrab Cloud Pack v1 ZIP export (dry-run · fixture).

출구: fixture-only (synthetic fixture 로 빌더 계약 검증만 — 실데이터 serve/local-ingest 아님).

정본 파이프라인: 1층 node → 2층 edge → 3층 graph → 4층 validation → 5층 confirm → **6층 pack/export**.
본 모듈 = 6층. 5층 approved edge 만 OpenCrab Cloud Pack v1 ZIP 으로 변환 + 계약 검증.
**Cloud 업로드 0 · DB insert 0 · 운영 ledger/candidate 미접촉**(owner 명시: ZIP 생성+검증까지만).

fixture 기준(owner 명시): 실 approved 데이터 0 → synthetic fixture(test/candidate/unverified 표시)로 빌더 검증.
텍스트 전용: visual_processing.status=not_applicable · OCR/CLIP 신규 의존성 설치 0.
release gate: fixture라 억지 true 금지 — 미달이면 release_ready=false / release_status=degraded 정직 기록.
"""
import hashlib
import json
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from binggu_graph_preview import build_graph_preview        # 3·4층
from binggu_graph_confirm import build_graph_confirm        # 5층
from binggu_rationale_suggest import SUPPORTS               # supports_judgment
from openbinggu_verb_edge_schema import validate_verb_edge, VERB_EDGES

PACK_FORMAT = "opencrab-cloud-pack-v1"
PACK_TITLE = "BingguPack Layered Graph Dryrun Pack"
PACK_PURPOSE = ("BingguPack 1~5층 파이프라인(node → rationale/edge → graph → validation → human confirm)을 "
                "OpenCrab Cloud Pack v1 형식으로 변환·검증할 수 있는지 확인하는 dry-run 팩 (synthetic fixture)")

# secret/PII leak 스캔(보수) — fixture 는 안전 텍스트지만 게이트로 강제
_LEAK = [re.compile(r"sk-live-[A-Za-z0-9]"), re.compile(r"\b\d{3}-\d{4}-\d{4}\b"),
         re.compile(r"password\s*[:=]", re.I), re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")]

ZIP_DENY_DIRS = ("__MACOSX/", ".git/", ".ipynb_checkpoints/")
ZIP_DENY_NAMES = (".DS_Store",)
ZIP_ALLOWED_EXT = {".json", ".jsonl", ".md", ".txt"}
ZIP_MAX_ENTRIES = 500
ZIP_MAX_FILE_BYTES = 5 * 1024 * 1024


def _sha(s):
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:12]


def _leak_count(text):
    return sum(1 for rx in _LEAK if rx.search(text or ""))


# ---------------- fixture: 1~5층 거쳐 approved 생성 ----------------
def synthetic_approved():
    """synthetic fixture(test) — 1층 node + evidence → 3층 graph → 5층 confirm(approve). 운영 데이터 아님."""
    nodes = [
        {"id": "node:ev1", "properties": {"label_kind": "증거", "sentence": "[SYNTHETIC] 로그에 오타가 3번 찍혔다",
                                          "semantic_subtype": "버그패턴"}, "evidence_refs": ["EVC-s1"]},
        {"id": "node:st1", "properties": {"label_kind": "상태", "sentence": "[SYNTHETIC] 빌드가 깨져 있다",
                                          "semantic_subtype": "사실"}, "evidence_refs": ["EVC-s2"]},
        {"id": "node:j1", "properties": {"label_kind": "판단", "sentence": "[SYNTHETIC] 배포 전 한 번 더 확인하자",
                                         "semantic_subtype": "교훈"}, "evidence_refs": ["EVC-s3"]},
    ]
    evidence = [
        {"id": "EVC-s1", "text": "[SYNTHETIC TEST] commit a1b2 diff: return 2 -> return 3", "source": "synthetic"},
        {"id": "EVC-s2", "text": "[SYNTHETIC TEST] CI run #12 failed at build step", "source": "synthetic"},
        {"id": "EVC-s3", "text": "[SYNTHETIC TEST] retro note: verify before deploy", "source": "synthetic"},
    ]
    g = build_graph_preview(nodes, evidence_items=evidence)
    approve_idx = list(range(1, len(g["edges"]) + 1))   # fixture: 모든 valid edge 사람 승인 가정
    conf = build_graph_confirm(g, approve=approve_idx)
    return nodes, evidence, g, conf


# ---------------- Cloud Pack v1 산출물 빌드 ----------------
ALLOWED_DATA_CLASS = {"synthetic_fixture", "real_candidate", "real_active"}


def build_cloud_pack(out_dir, nodes, evidence, graph_preview, graph_confirm,
                     data_class="synthetic_fixture"):
    if data_class not in ALLOWED_DATA_CLASS:
        raise ValueError("data_class 비허용값: %r (allowed=%s)" % (data_class, sorted(ALLOWED_DATA_CLASS)))
    os.makedirs(out_dir, exist_ok=True)
    for sub in ("cloud", "graph", "evidence", "quality", "reports", "benchmark", "ingest", "neo4j"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)

    nodes_by_id = {n["id"]: n for n in nodes}
    ev_by_id = {e["id"]: e for e in evidence}

    # ---- documents / chunks (Evidence chunk = chunk) ----
    documents = [{"doc_id": "DOC-synthetic-1", "title": "[SYNTHETIC] BingguPack fixture document",
                  "source": "synthetic", "readable": True, "candidate": True, "unverified": True,
                  "text": "synthetic fixture aggregating evidence chunks"}]
    chunks, ev_index, graph_nodes, graph_edges = [], [], [], []
    leak_count = 0

    # evidence → chunk + evidence/index + Evidence node + document/chunk edge
    for e in evidence:
        txt = e["text"]
        leak_count += _leak_count(txt)
        cid = "CHUNK-" + e["id"]
        chunks.append({"chunk_id": cid, "doc_id": "DOC-synthetic-1", "evidence_id": e["id"],
                       "text": txt, "candidate": True})
        ev_index.append({"evidence_id": e["id"], "chunk_id": cid, "doc_id": "DOC-synthetic-1",
                         "source": e.get("source", "synthetic"), "text_sha": _sha(txt)})
        # Evidence node
        graph_nodes.append({"id": e["id"], "node_type": "Evidence", "label_kind": "증거",
                            "text": txt[:80], "evidence_refs": [e["id"]], "candidate": True, "unverified": True})
        # document/chunk edge (Evidence node -> document)  *grounding, not a verb-edge*
        graph_edges.append({"from": e["id"], "to": "DOC-synthetic-1", "relation": "evidence_of_document",
                            "edge_kind": "grounding", "evidence_refs": [e["id"]], "candidate": True})

    # canonical content nodes
    for n in nodes:
        p = n["properties"]
        graph_nodes.append({"id": n["id"], "node_type": "Claim", "label_kind": p.get("label_kind"),
                            "text": p.get("sentence", "")[:80],
                            "semantic_subtype": p.get("semantic_subtype"),   # 보조 메타(canonical 승격 0)
                            "evidence_refs": n.get("evidence_refs") or [], "candidate": True, "unverified": True})
        leak_count += _leak_count(p.get("sentence", ""))

    # ---- approved supports_judgment edges 만 (validate_verb_edge 통과) ----
    nbi = {n["id"]: {"id": n["id"], "properties": {"label_kind": n["properties"].get("label_kind"),
                                                   "candidate": True}} for n in nodes}
    edge_skipped = []
    for a in graph_confirm.get("approved", []):
        rel = a.get("relation")
        sid, tid = a.get("source_id"), a.get("target_id")
        if rel != SUPPORTS or rel not in VERB_EDGES:
            edge_skipped.append({"edge": "%s->%s" % (sid, tid), "reason": "relation_not_supports_judgment"})
            continue
        edge_obj = {"id": "e_%s_%s" % (sid, tid), "source": sid, "target": tid,
                    "properties": {"relation": rel, "candidate": True},
                    "evidence_refs": a.get("evidence_refs") or [], "promotion_allowed": False}
        v = validate_verb_edge(edge_obj, nbi)
        if v["verdict"] != "PASS":
            edge_skipped.append({"edge": "%s->%s" % (sid, tid), "reason": v["reason"]})
            continue
        if sid not in nodes_by_id or tid not in nodes_by_id:
            edge_skipped.append({"edge": "%s->%s" % (sid, tid), "reason": "missing_endpoint"})
            continue
        if not (a.get("evidence_refs")):
            edge_skipped.append({"edge": "%s->%s" % (sid, tid), "reason": "no_evidence_refs"})
            continue
        graph_edges.append({"source": sid, "target": tid, "from": sid, "to": tid, "relation": rel,
                            "verb": "근거가_된다", "evidence_refs": a.get("evidence_refs"),
                            "edge_kind": "verb", "status": "candidate", "promotion_allowed": False,
                            "candidate": True, "unverified": True})

    # ---- 품질/게이트 ----
    # graph 검증: node id 필수 · edge from/to(or source/target) 필수
    nodes_with_id = all(n.get("id") for n in graph_nodes)
    edges_have_endpoints = all((e.get("source") and e.get("target")) or (e.get("from") and e.get("to"))
                               for e in graph_edges)
    jsonl_ok = True   # 아래 write 시 json.dumps 라 valid 보장(이중 확인은 _validate_jsonl)
    readable_docs = sum(1 for d in documents if d.get("readable") and (d.get("text") or "").strip())
    nonempty = all((c.get("text") or "").strip() for c in chunks)

    # retrieval eval (fixture · 실제 term coverage 계산 · 억지 true 금지)
    bench_q, bench_r, reval = _retrieval_eval(chunks)

    required_failures = []
    if leak_count != 0:
        required_failures.append("evidence_leak_count=%d" % leak_count)
    if not nodes_with_id:
        required_failures.append("graph_node_missing_id")
    if not edges_have_endpoints:
        required_failures.append("graph_edge_missing_endpoint")
    if readable_docs < 1:
        required_failures.append("no_readable_document")
    if reval["known_match_failures"] != 0:
        required_failures.append("known_match_failures=%d" % reval["known_match_failures"])

    gate_pass = (reval["hit_rate"] >= 0.8 and reval["relevant_hit_rate"] >= 0.6
                 and reval["average_term_coverage"] >= 0.25 and reval["known_match_failures"] == 0)
    # release 자격 = real_active 만(사람 확정분). synthetic/real_candidate 는 게이트 통과해도 release 자격 없음.
    # 억지 true 금지(owner). 측정값은 정직 기록. candidate/active/synthetic 명확 분리.
    is_synthetic = (data_class == "synthetic_fixture")
    is_release_eligible = (data_class == "real_active")
    release_ready = (len(required_failures) == 0) and gate_pass and is_release_eligible
    release_status = "ready" if release_ready else "degraded"
    degraded_reasons = list(required_failures)
    if is_synthetic:
        degraded_reasons.append("synthetic_fixture: 실데이터 아님 — release 자격 없음(빌더 검증용 dry-run)")
    elif data_class == "real_candidate":
        degraded_reasons.append("real_candidate: 사람 확정(active) 전 후보 — release 자격 없음")
    if not gate_pass:
        if reval["hit_rate"] < 0.8:
            degraded_reasons.append("hit_rate %.3f < 0.8" % reval["hit_rate"])
        if reval["relevant_hit_rate"] < 0.6:
            degraded_reasons.append("relevant_hit_rate %.3f < 0.6" % reval["relevant_hit_rate"])
        if reval["average_term_coverage"] < 0.25:
            degraded_reasons.append("average_term_coverage %.3f < 0.25" % reval["average_term_coverage"])

    visual = {"status": "not_applicable", "visual_candidate_count": 0, "ocr_candidate_count": 0,
              "ocr_processed_count": 0, "clip_indexed_count": 0, "clip_model": None, "ocr_processor": None,
              "note": "텍스트 전용 fixture — 시각 자산 0 · OCR/CLIP 의존성 설치 0"}

    counts = {"documents": len(documents), "chunks": len(chunks), "evidence": len(ev_index),
              "nodes": len(graph_nodes), "edges": len(graph_edges), "edges_skipped": len(edge_skipped)}

    manifest = {"format_version": PACK_FORMAT, "pack_title": PACK_TITLE, "purpose": PACK_PURPOSE,
                "pack_type": "candidate", "data_class": data_class, "unverified": True,
                "promotion_allowed_default": False, "cloud_upload": False, "db_insert": False,
                "counts": counts, "release_ready": release_ready, "release_status": release_status,
                "files": ["manifest.json", "cloud/documents.jsonl", "cloud/chunks.jsonl",
                          "graph/nodes.jsonl", "graph/edges.jsonl", "evidence/index.jsonl",
                          "quality/report.json", "reports/quality.json", "reports/pack_contract.json",
                          "reports/release_gate.json", "reports/evidence_index.json", "reports/graphrag.json",
                          "reports/retrieval_eval.json", "benchmark/queries.jsonl", "benchmark/results.jsonl",
                          "ingest/plan.json", "ingest/batches.jsonl", "neo4j/opencrab_ingest.jsonl",
                          "neo4j/export_status.json", "reports/visual_processing.json"]}

    quality = {"leak_count": leak_count, "nodes_with_id": nodes_with_id,
               "edges_have_endpoints": edges_have_endpoints, "readable_documents": readable_docs,
               "nonempty_chunks": nonempty, "edges_skipped": edge_skipped,
               "required_failures": required_failures, "release_ready": release_ready,
               "release_status": release_status, "degraded_reasons": degraded_reasons}
    release_gate = {"thresholds": {"hit_rate": 0.8, "relevant_hit_rate": 0.6,
                                   "average_term_coverage": 0.25, "known_match_failures": 0},
                    "measured": reval, "gate_pass": gate_pass, "release_ready": release_ready,
                    "release_status": release_status, "degraded_reasons": degraded_reasons}
    pack_contract = {"format": PACK_FORMAT, "required_files_present": True, "synthetic": is_synthetic,
                     "data_class": data_class,
                     "cloud_upload": False, "db_insert": False, "operating_db_touched": False}
    graphrag = {"nodes": counts["nodes"], "edges": counts["edges"], "verb_edges": SUPPORTS,
                "new_predicates": 0, "node_to_node_verb_edges": sum(1 for e in graph_edges if e.get("edge_kind") == "verb")}
    ingest_plan = {"target": "opencrab-local-ingest (NOT executed)", "executed": False, "cloud_upload": False,
                   "db_insert": False, "batches": 1,
                   "note": "로컬 역인제스트 대기 — localbinggu_ingest_executor가 ZIP 풀어 opencrab ingest. execute 명시 전 실행 0(비가역 write 방어)"}
    ingest_batches = [{"batch": 1, "nodes": counts["nodes"], "edges": counts["edges"], "executed": False}]
    neo4j_ingest = [{"op": "MERGE_NODE", "id": n["id"], "executed": False} for n in graph_nodes] + \
                   [{"op": "MERGE_EDGE", "from": e.get("from") or e.get("source"),
                     "to": e.get("to") or e.get("target"), "executed": False} for e in graph_edges]
    neo4j_status = {"exported": True, "ingested": False, "cloud_upload": False, "db_insert": False,
                    "ingest_target": "local", "ingest_method": "offline-unzip-and-ingest",
                    "note": "ingest jsonl 생성만 — ZIP 해제 후 로컬 opencrab ingest 진입 대기(실행 0)"}

    # ---- write 산출물 ----
    def wjson(rel, obj):
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)

    def wjsonl(rel, rows):
        with open(os.path.join(out_dir, rel), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    wjson("manifest.json", manifest)
    wjsonl("cloud/documents.jsonl", documents)
    wjsonl("cloud/chunks.jsonl", chunks)
    wjsonl("graph/nodes.jsonl", graph_nodes)
    wjsonl("graph/edges.jsonl", graph_edges)
    wjsonl("evidence/index.jsonl", ev_index)
    wjson("quality/report.json", quality)
    wjson("reports/quality.json", quality)
    wjson("reports/pack_contract.json", pack_contract)
    wjson("reports/release_gate.json", release_gate)
    wjson("reports/evidence_index.json", {"count": len(ev_index), "rows": ev_index})
    wjson("reports/graphrag.json", graphrag)
    wjson("reports/retrieval_eval.json", reval)
    wjsonl("benchmark/queries.jsonl", bench_q)
    wjsonl("benchmark/results.jsonl", bench_r)
    wjson("ingest/plan.json", ingest_plan)
    wjsonl("ingest/batches.jsonl", ingest_batches)
    wjsonl("neo4j/opencrab_ingest.jsonl", neo4j_ingest)
    wjson("neo4j/export_status.json", neo4j_status)
    wjson("reports/visual_processing.json", visual)

    return {"manifest": manifest, "quality": quality, "release_gate": release_gate, "counts": counts,
            "visual": visual, "edge_skipped": edge_skipped}


def _retrieval_eval(chunks):
    """fixture 텍스트로 term coverage 실측(억지 true 금지). synthetic query = 각 chunk 핵심어."""
    def terms(t):
        return set(re.findall(r"[A-Za-z가-힣0-9]+", (t or "").lower())) - {"synthetic", "test"}
    chunk_terms = [(c["chunk_id"], terms(c["text"])) for c in chunks]
    queries, results = [], []
    hits, rel_hits, covs, known_fail = 0, 0, [], 0
    for c in chunks:
        ct = terms(c["text"])
        q_terms = set(list(ct)[:3])   # synthetic query = chunk 핵심어 3개
        queries.append({"query_id": "Q-" + c["chunk_id"], "terms": sorted(q_terms),
                        "expected_chunk": c["chunk_id"], "synthetic": True})
        # 검색: q_terms 와 overlap 최대 chunk
        best, best_ov = None, 0.0
        for cid, t in chunk_terms:
            ov = (len(q_terms & t) / len(q_terms)) if q_terms else 0.0
            if ov > best_ov:
                best_ov, best = ov, cid
        hit = best == c["chunk_id"]
        hits += 1 if hit else 0
        rel_hits += 1 if (hit and best_ov >= 0.5) else 0
        covs.append(best_ov)
        if not hit:
            known_fail += 1
        results.append({"query_id": "Q-" + c["chunk_id"], "retrieved": best, "term_coverage": round(best_ov, 3),
                        "hit": hit})
    n = max(1, len(chunks))
    reval = {"queries": len(queries), "hit_rate": round(hits / n, 3),
             "relevant_hit_rate": round(rel_hits / n, 3),
             "average_term_coverage": round(sum(covs) / n, 3), "known_match_failures": known_fail,
             "synthetic": True}
    return queries, results, reval


# ---------------- ZIP (규칙 적용) ----------------
def make_zip(out_dir, zip_path):
    entries, skipped = [], []
    for root, dirs, files in os.walk(out_dir):
        dirs[:] = [d for d in dirs if not any((d + "/").startswith(x.rstrip("/") + "/") for x in ZIP_DENY_DIRS)]
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, out_dir).replace("\\", "/")
            if any(part + "/" in (rel + "/") for part in (x.rstrip("/") for x in ZIP_DENY_DIRS)):
                skipped.append((rel, "deny_dir")); continue
            if fn in ZIP_DENY_NAMES:
                skipped.append((rel, "deny_name")); continue
            if os.path.splitext(fn)[1].lower() not in ZIP_ALLOWED_EXT:
                skipped.append((rel, "ext_not_allowed")); continue
            if os.path.getsize(full) > ZIP_MAX_FILE_BYTES:
                skipped.append((rel, "too_large")); continue
            entries.append((full, rel))
    if len(entries) > ZIP_MAX_ENTRIES:
        raise ValueError("ZIP 엔트리 %d > %d" % (len(entries), ZIP_MAX_ENTRIES))
    os.makedirs(os.path.dirname(zip_path), exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in sorted(entries, key=lambda x: x[1]):
            z.write(full, rel)
    return [r for _, r in entries], skipped


# ---------------- selftest ----------------
def _validate_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                json.loads(ln)   # 한 줄당 valid JSON (실패 시 raise)
    return True


def _selftest():
    import tempfile
    import shutil
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    tmp = tempfile.mkdtemp(prefix="bgp_cloud_pack_")
    try:
        out = os.path.join(tmp, "pack")
        nodes, evidence, g, conf = synthetic_approved()
        ck(conf["summary"]["approved"] >= 1 and conf["summary"]["auto_approved"] == 0,
           "fixture 1~5층 통과 → approved edge 존재 · 자동 approve 0")
        rep = build_cloud_pack(out, nodes, evidence, g, conf)

        # 필수 산출물 20종 전부 존재
        missing = [f for f in rep["manifest"]["files"] if not os.path.exists(os.path.join(out, f))]
        ck(not missing, "필수 산출물 전부 존재 (%d종)" % len(rep["manifest"]["files"]))

        # manifest format
        ck(rep["manifest"]["format_version"] == PACK_FORMAT, "manifest format=opencrab-cloud-pack-v1")
        ck(rep["manifest"]["data_class"] == "synthetic_fixture" and rep["manifest"]["unverified"] is True,
           "synthetic/unverified 표시")

        # JSONL valid (한 줄당 valid JSON)
        jsonl_files = [f for f in rep["manifest"]["files"] if f.endswith(".jsonl")]
        ck(all(_validate_jsonl(os.path.join(out, f)) for f in jsonl_files), "모든 JSONL 한 줄당 valid JSON")

        # graph node id 필수 · edge endpoint 필수
        ck(rep["quality"]["nodes_with_id"] and rep["quality"]["edges_have_endpoints"],
           "graph node id 필수 · edge source/target(from/to) 필수")

        # evidence leak_count=0
        ck(rep["quality"]["leak_count"] == 0, "evidence leak_count=0")

        # evidence 4중 연결: index row · Evidence node · doc edge · evidence_refs
        nodes_j = [json.loads(l) for l in open(os.path.join(out, "graph/nodes.jsonl"), encoding="utf-8")]
        ev_node_ids = {n["id"] for n in nodes_j if n.get("node_type") == "Evidence"}
        idx = [json.loads(l) for l in open(os.path.join(out, "evidence/index.jsonl"), encoding="utf-8")]
        edges_j = [json.loads(l) for l in open(os.path.join(out, "graph/edges.jsonl"), encoding="utf-8")]
        doc_edge_src = {e.get("from") for e in edges_j if e.get("edge_kind") == "grounding"}
        ck(all(r["evidence_id"] in ev_node_ids for r in idx) and ev_node_ids <= doc_edge_src,
           "Evidence chunk 4중 연결(index·Evidence node·doc edge·refs)")

        # 신규 predicate 0 — verb edge 는 supports_judgment 만
        verb_rels = {e.get("relation") for e in edges_j if e.get("edge_kind") == "verb"}
        ck(verb_rels <= {SUPPORTS}, "verb edge = supports_judgment만(신규 predicate 0)")

        # release gate 정직(fixture) — 억지 true 금지
        rg = json.load(open(os.path.join(out, "reports/release_gate.json"), encoding="utf-8"))
        ck(rg["release_ready"] in (True, False) and
           (rg["release_ready"] or rg["release_status"] == "degraded"),
           "release_gate 정직(미달이면 degraded · 사유 기록): ready=%s status=%s" % (rg["release_ready"], rg["release_status"]))

        # visual not_applicable
        vp = json.load(open(os.path.join(out, "reports/visual_processing.json"), encoding="utf-8"))
        ck(vp["status"] == "not_applicable" and vp["clip_model"] is None and vp["ocr_processor"] is None
           and vp["visual_candidate_count"] == 0, "visual_processing not_applicable · 전부 0 · 모델 none")

        # cloud upload 0 · db insert 0
        ck(rep["manifest"]["cloud_upload"] is False and rep["manifest"]["db_insert"] is False,
           "manifest cloud_upload=false · db_insert=false")
        ns = json.load(open(os.path.join(out, "neo4j/export_status.json"), encoding="utf-8"))
        ck(ns["ingested"] is False and ns["cloud_upload"] is False, "neo4j export_status: ingest/upload 0")

        # ZIP 생성 + 규칙
        zp = os.path.join(tmp, "pack.zip")
        ents, skipped = make_zip(out, zp)
        ck(os.path.exists(zp) and len(ents) == len(rep["manifest"]["files"]) and len(ents) <= ZIP_MAX_ENTRIES,
           "단일 ZIP 생성 · 엔트리 %d (<=500)" % len(ents))
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
        ck(all(os.path.splitext(n)[1].lower() in ZIP_ALLOWED_EXT for n in names) and
           not any(d.rstrip("/") in n for n in names for d in ZIP_DENY_DIRS),
           "ZIP 허용 확장자만 · deny dir 제외")

        print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        sys.exit(_selftest())
    if args[0] == "--build":
        # 실 빌드(temp 산출 + ZIP). Cloud 업로드 0 · DB insert 0.
        import tempfile
        out = os.path.join(tempfile.gettempdir(), "bgp_cloud_pack_v1", "pack")
        nodes, evidence, g, conf = synthetic_approved()
        rep = build_cloud_pack(out, nodes, evidence, g, conf)
        zp = os.path.join(tempfile.gettempdir(), "bgp_cloud_pack_v1", "BingguPack_Layered_Graph_Dryrun_Pack.zip")
        ents, skipped = make_zip(out, zp)
        print(json.dumps({"zip": zp, "entries": len(ents), "counts": rep["counts"],
                          "release_ready": rep["manifest"]["release_ready"],
                          "release_status": rep["manifest"]["release_status"],
                          "degraded_reasons": rep["quality"]["degraded_reasons"],
                          "leak_count": rep["quality"]["leak_count"],
                          "cloud_upload": rep["manifest"]["cloud_upload"],
                          "db_insert": rep["manifest"]["db_insert"]}, ensure_ascii=False, indent=2))
        sys.exit(0)
    print("usage: binggu_cloud_pack_export.py [--selftest | --build]")
    sys.exit(2)


if __name__ == "__main__":
    main()
