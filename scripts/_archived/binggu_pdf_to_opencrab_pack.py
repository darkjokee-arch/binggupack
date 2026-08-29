# -*- coding: utf-8 -*-
"""Local PDF -> OpenCrab Cloud Pack v1 ZIP (dry-run only).

This is a source-document pack builder: it preserves every PDF page as an
Evidence chunk, then adds chapter/section Concept nodes and source-backed
Claim/State nodes. It never uploads, ingests, or writes the BingguPack ledger.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

PACK_FORMAT = "opencrab-cloud-pack-v1"
CONTRACT_REQUIRED = [
    "manifest.json", "cloud/documents.jsonl", "cloud/chunks.jsonl",
    "graph/nodes.jsonl", "graph/edges.jsonl", "quality/report.json",
    "reports/quality.json", "reports/pack_contract.json",
    "reports/release_gate.json", "evidence/index.jsonl",
    "reports/evidence_index.json", "reports/graphrag.json",
    "reports/retrieval_eval.json", "benchmark/queries.jsonl",
    "benchmark/results.jsonl", "ingest/plan.json", "ingest/batches.jsonl",
    "neo4j/opencrab_ingest.jsonl", "neo4j/export_status.json",
    "reports/visual_processing.json",
]


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def norm(text: str) -> str:
    text = (text or "").replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def dump_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
        encoding="utf-8",
    )


def terms(text: str) -> list[str]:
    return [w for w in re.split(r"[^0-9A-Za-z가-힣]+", text.lower()) if len(w) > 1]


def coverage(qterms: list[str], text: str) -> float:
    low = text.lower()
    return sum(1 for w in qterms if w in low) / max(1, len(qterms))


def clean_heading(text: str) -> str:
    text = compact(text)
    text = re.sub(r"·{2,}.*$", "", text).strip()
    text = re.sub(r"\s+\d+$", "", text).strip()
    return text[:90]


def sentence_candidates(text: str) -> list[str]:
    flat = compact(text)
    parts = re.split(r"(?<=[.。])\s+|(?<=다\.)\s+|(?<=한다\.)\s+", flat)
    return [p.strip() for p in parts if 35 <= len(p.strip()) <= 240 and not p.strip().startswith("목 차")]


def extract_pdf_pages(pdf_path: Path) -> list[dict]:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - environment guard
        raise SystemExit("pypdf is required. Run with: uv run --with pypdf -- python ...") from exc

    reader = PdfReader(str(pdf_path))
    pages = []
    for idx, page in enumerate(reader.pages, 1):
        text = norm(page.extract_text() or "")
        if not text:
            text = "[EMPTY_PAGE]"
        pages.append({"page": idx, "text": text, "sha256": sha_text(text)})
    return pages


def build_pack(pdf_path: Path, out_root: Path, title: str | None = None) -> dict:
    pdf_path = pdf_path.resolve()
    out_root = out_root.resolve()
    pack_dir = out_root / "pack"
    zip_path = out_root / "localgov_award_criteria_opencrab_pack_v1.zip"
    if out_root.exists():
        shutil.rmtree(out_root)
    for sub in ("cloud", "graph", "evidence", "quality", "reports", "benchmark", "ingest", "neo4j"):
        (pack_dir / sub).mkdir(parents=True, exist_ok=True)

    pdf_bytes = pdf_path.read_bytes()
    pdf_sha = sha_bytes(pdf_bytes)
    pages = extract_pdf_pages(pdf_path)
    title = title or pdf_path.stem
    doc_id = "doc:localgov-award-criteria-20251201"
    full_text = "\n\n".join("--- page %03d ---\n%s" % (p["page"], p["text"]) for p in pages)
    (out_root / "source_extracted_text.md").write_text(
        "# %s\n\nsource_pdf: `%s`\nsha256: `%s`\npages: %d\n\n%s\n"
        % (title, pdf_path, pdf_sha, len(pages), full_text),
        encoding="utf-8",
    )

    chapter_pat = re.compile(r"(제\d+장의?\d*\s*[^\n·]{0,70})")
    section_pat = re.compile(r"(제\d+절\s*[^\n·]{0,50})")
    concepts, seen = [], set()
    for page in pages:
        one = compact(page["text"])
        for pat, kind in ((chapter_pat, "chapter"), (section_pat, "section")):
            for match in pat.finditer(one):
                label = clean_heading(match.group(1))
                if len(label) < 4 or len(label) > 80:
                    continue
                if kind == "section" and len(label) > 35:
                    continue
                key = (kind, label)
                if key not in seen:
                    seen.add(key)
                    concepts.append({
                        "kind": kind,
                        "label": label,
                        "page": page["page"],
                        "evidence_id": "EV-P%03d" % page["page"],
                    })
    concepts = concepts[:120]

    claim_keywords = ["낙찰자", "적격심사", "사전심사", "종합평가", "협상", "평가", "심사", "기준", "결정", "입찰"]
    claims = []
    for page in pages:
        picked = None
        for sent in sentence_candidates(page["text"]):
            if any(k in sent for k in claim_keywords):
                picked = sent
                break
        if picked:
            claims.append({"label": picked[:220], "page": page["page"], "evidence_id": "EV-P%03d" % page["page"]})
        if len(claims) >= 150:
            break

    first = compact(pages[0]["text"]) if pages else ""
    states = []
    for match in re.finditer(r"\[[^\]]{4,60}\]", first):
        label = match.group(0)
        if "시행" in label or "예규" in label or "개정" in label:
            states.append({"label": label, "page": 1, "evidence_id": "EV-P001"})
    states = states[:4]

    documents = [{
        "id": doc_id,
        "title": title,
        "path": str(pdf_path),
        "sha256": pdf_sha,
        "bytes": len(pdf_bytes),
        "mime": "application/pdf",
        "pages": len(pages),
        "source_type": "local_pdf",
    }]
    chunks, evidence, nodes, edges = [], [], [], []
    all_ev = ["EV-P%03d" % p["page"] for p in pages]
    nodes.append({
        "id": doc_id,
        "space": "resource",
        "node_type": "Document",
        "label": title,
        "properties": {"path": str(pdf_path), "sha256": pdf_sha, "pages": len(pages), "label_kind": "문서"},
        "evidence_refs": all_ev,
    })
    for page in pages:
        ev_id = "EV-P%03d" % page["page"]
        chunk_id = "chunk:p%03d" % page["page"]
        chunks.append({
            "id": chunk_id,
            "document_id": doc_id,
            "seq": page["page"],
            "page": page["page"],
            "text": page["text"],
            "chars": len(page["text"]),
            "evidence_id": ev_id,
        })
        evidence.append({
            "evidence_id": ev_id,
            "chunk_id": chunk_id,
            "document_id": doc_id,
            "page": page["page"],
            "sha256": page["sha256"],
            "chars": len(page["text"]),
            "source_path": str(pdf_path),
        })
        nodes.append({
            "id": ev_id,
            "space": "evidence",
            "node_type": "TextUnit",
            "label": "page %03d evidence" % page["page"],
            "properties": {
                "page": page["page"],
                "chunk_id": chunk_id,
                "label_kind": "증거",
                "source_sha256": page["sha256"],
            },
            "evidence_refs": [ev_id],
        })
        edges.append({
            "id": "edge:contains:%03d" % page["page"],
            "relation": "contains",
            "source": doc_id,
            "target": ev_id,
            "evidence_refs": [ev_id],
            "properties": {"verb": "contains"},
        })

    for idx, concept in enumerate(concepts, 1):
        cid = "concept:%03d" % idx
        nodes.append({
            "id": cid,
            "space": "concept",
            "node_type": "Concept",
            "label": concept["label"],
            "properties": {
                "concept_kind": concept["kind"],
                "page": concept["page"],
                "label_kind": "개념",
                "semantic_subtype": "사실",
            },
            "evidence_refs": [concept["evidence_id"]],
        })
        edges.append({
            "id": "edge:mentions:%03d" % idx,
            "relation": "mentions",
            "source": concept["evidence_id"],
            "target": cid,
            "evidence_refs": [concept["evidence_id"]],
            "properties": {"verb": "mentions"},
        })

    for idx, state in enumerate(states, 1):
        sid = "state:%03d" % idx
        nodes.append({
            "id": sid,
            "space": "claim",
            "node_type": "Claim",
            "label": state["label"],
            "properties": {
                "page": state["page"],
                "label_kind": "상태",
                "canonical_kind": "State",
                "semantic_subtype": "사실",
            },
            "evidence_refs": [state["evidence_id"]],
        })
        edges.append({
            "id": "edge:supports_state:%03d" % idx,
            "relation": "supports",
            "source": state["evidence_id"],
            "target": sid,
            "evidence_refs": [state["evidence_id"]],
            "properties": {"verb": "supports_judgment", "binggu_relation": "supports_judgment"},
        })

    for idx, claim in enumerate(claims, 1):
        cid = "claim:%03d" % idx
        nodes.append({
            "id": cid,
            "space": "claim",
            "node_type": "Claim",
            "label": claim["label"],
            "properties": {"page": claim["page"], "label_kind": "판단", "semantic_subtype": "사실"},
            "evidence_refs": [claim["evidence_id"]],
        })
        edges.append({
            "id": "edge:supports_claim:%03d" % idx,
            "relation": "supports",
            "source": claim["evidence_id"],
            "target": cid,
            "evidence_refs": [claim["evidence_id"]],
            "properties": {"verb": "supports_judgment", "binggu_relation": "supports_judgment"},
        })

    node_ids = {n["id"] for n in nodes}
    ev_ids = {e["evidence_id"] for e in evidence}
    edge_ok = all(e["source"] in node_ids and e["target"] in node_ids and e.get("evidence_refs") for e in edges)
    refs = {ev for n in nodes for ev in n.get("evidence_refs", [])} | {
        ev for e in edges for ev in e.get("evidence_refs", [])
    }
    closure_ok = ev_ids <= refs and all(c["evidence_id"] in ev_ids for c in chunks)
    leak_patterns = [
        re.compile(r"(api[_-]?key|secret|password)\s*[:=]\s*\S+", re.I),
        re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    ]
    leak_count = sum(1 for page in pages for rx in leak_patterns if rx.search(page["text"]))

    queries = [
        ("q01", "입찰참가자격 사전심사 대상공사"),
        ("q02", "적격심사 기준 낙찰자 결정"),
        ("q03", "종합평가 낙찰자 결정기준"),
        ("q04", "협상에 의한 계약 낙찰자 결정기준"),
        ("q05", "품질 등에 의한 낙찰자 결정기준"),
    ]
    bench_q, bench_r, hits, covs = [], [], 0, []
    for qid, query in queries:
        qterms = terms(query)
        ranked = sorted(chunks, key=lambda c: -coverage(qterms, c["text"]))[:3]
        top = [
            {"chunk_id": c["id"], "page": c["page"], "coverage": round(coverage(qterms, c["text"]), 3)}
            for c in ranked
        ]
        hit = bool(top and top[0]["coverage"] >= 0.4)
        hits += int(hit)
        covs.append(top[0]["coverage"] if top else 0)
        bench_q.append({"qid": qid, "query": query, "expected_terms": qterms})
        bench_r.append({"qid": qid, "hit": hit, "top": top})
    retrieval = {
        "queries": len(queries),
        "hit_rate": round(hits / max(1, len(queries)), 3),
        "average_term_coverage": round(sum(covs) / max(1, len(covs)), 3),
        "known_match_failures": len(queries) - hits,
    }
    release_ready = (
        len(pages) == 242
        and edge_ok
        and closure_ok
        and leak_count == 0
        and retrieval["hit_rate"] >= 0.8
    )
    counts = {
        "documents": len(documents),
        "chunks": len(chunks),
        "evidence": len(evidence),
        "nodes": len(nodes),
        "edges": len(edges),
        "concepts": len(concepts),
        "states": len(states),
        "claims": len(claims),
    }
    manifest = {
        "format": PACK_FORMAT,
        "title": title,
        "purpose": (
            "지방자치단체 입찰시 낙찰자 결정기준 원문 PDF를 증거 기반 그래프 pack으로 변환한다. "
            "낙찰자 결정 기준, 적격심사, 사전심사, 종합평가, 협상/품질 기준 질문에 원문 page evidence로 답하기 위한 pack."
        ),
        "source_pdf": str(pdf_path),
        "source_sha256": pdf_sha,
        "language": "ko",
        "data_class": "real_source_document",
        "cloud_upload": False,
        "db_insert": False,
        "ingested": False,
        "counts": counts,
        "grammar": {
            "canonical_label_kinds": ["문서", "증거", "개념", "상태", "판단"],
            "semantic_subtype": "보조층 only; canonical 승격 금지",
            "relations": sorted({e["relation"] for e in edges}),
            "binggu_relation": "supports_judgment for evidence->claim/state edges",
        },
        "release_ready": release_ready,
        "release_status": "ready" if release_ready else "degraded",
    }
    quality = {
        "release_ready": release_ready,
        "pages_extracted": len(pages),
        "edge_endpoints_ok": edge_ok,
        "evidence_closure_ok": closure_ok,
        "leak_count": leak_count,
        "retrieval": retrieval,
        "warnings": [] if release_ready else ["degraded: quality gate failed"],
    }
    gate_rows = [
        {"gate": "pages_extracted==242", "value": len(pages), "ok": len(pages) == 242},
        {"gate": "edge_endpoints_ok", "value": edge_ok, "ok": edge_ok},
        {"gate": "evidence_closure_ok", "value": closure_ok, "ok": closure_ok},
        {"gate": "leak_count==0", "value": leak_count, "ok": leak_count == 0},
        {
            "gate": "retrieval.hit_rate>=0.8",
            "value": retrieval["hit_rate"],
            "ok": retrieval["hit_rate"] >= 0.8,
        },
        {
            "gate": "retrieval.known_match_failures==0",
            "value": retrieval["known_match_failures"],
            "ok": retrieval["known_match_failures"] == 0,
        },
    ]

    dump_json(pack_dir / "manifest.json", manifest)
    dump_jsonl(pack_dir / "cloud/documents.jsonl", documents)
    dump_jsonl(pack_dir / "cloud/chunks.jsonl", chunks)
    dump_jsonl(pack_dir / "graph/nodes.jsonl", nodes)
    dump_jsonl(pack_dir / "graph/edges.jsonl", edges)
    dump_jsonl(pack_dir / "evidence/index.jsonl", evidence)
    dump_json(pack_dir / "quality/report.json", quality)
    dump_json(pack_dir / "reports/quality.json", quality)
    dump_json(pack_dir / "reports/pack_contract.json", {
        "format": PACK_FORMAT,
        "required_files": CONTRACT_REQUIRED,
        "all_present": True,
    })
    dump_json(pack_dir / "reports/release_gate.json", {
        "release_ready": release_ready,
        "release_status": manifest["release_status"],
        "gates": gate_rows,
        "quality": quality,
    })
    dump_json(pack_dir / "reports/evidence_index.json", {
        "total": len(evidence),
        "closure_ok": closure_ok,
        "source_pdf_sha256": pdf_sha,
    })
    dump_json(pack_dir / "reports/graphrag.json", {
        "nodes": len(nodes),
        "edges": len(edges),
        "label_kinds": manifest["grammar"]["canonical_label_kinds"],
        "relations": manifest["grammar"]["relations"],
    })
    dump_json(pack_dir / "reports/retrieval_eval.json", retrieval)
    dump_jsonl(pack_dir / "benchmark/queries.jsonl", bench_q)
    dump_jsonl(pack_dir / "benchmark/results.jsonl", bench_r)
    dump_json(pack_dir / "ingest/plan.json", {
        "target": "opencrab-cloud",
        "mode": "single_zip_upload",
        "executed": False,
        "cloud_upload": False,
        "db_insert": False,
    })
    dump_jsonl(pack_dir / "ingest/batches.jsonl", [{
        "batch_id": "b001",
        "nodes": len(nodes),
        "edges": len(edges),
        "executed": False,
    }])
    dump_jsonl(pack_dir / "neo4j/opencrab_ingest.jsonl", [
        {"op": "MERGE_NODE", "id": n["id"], "executed": False} for n in nodes
    ] + [
        {"op": "MERGE_EDGE", "id": e["id"], "executed": False} for e in edges
    ])
    dump_json(pack_dir / "neo4j/export_status.json", {
        "exported": True,
        "ingested": False,
        "cloud_upload": False,
        "db_insert": False,
    })
    dump_json(pack_dir / "reports/visual_processing.json", {
        "status": "not_applicable",
        "note": "텍스트 PDF 추출 기반; OCR/CLIP 사용 0",
        "visual_candidate_count": 0,
        "ocr_processed_count": 0,
        "clip_indexed_count": 0,
    })

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(pack_dir.rglob("*")):
            if item.is_file():
                zf.write(item, item.relative_to(pack_dir).as_posix())

    summary = {
        "status": "PACK_READY" if release_ready else "PACK_DEGRADED",
        "source_pdf": str(pdf_path),
        "source_sha256": pdf_sha,
        "output_dir": str(out_root),
        "pack_dir": str(pack_dir),
        "zip_path": str(zip_path),
        "bundle_hash": sha_bytes(zip_path.read_bytes()),
        "node_hash": sha_bytes((pack_dir / "graph/nodes.jsonl").read_bytes()),
        "evidence_hash": sha_bytes((pack_dir / "evidence/index.jsonl").read_bytes()),
        "counts": counts,
        "quality": quality,
    }
    dump_json(out_root / "build_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--title")
    args = parser.parse_args()
    summary = build_pack(Path(args.pdf), Path(args.out), title=args.title)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "PACK_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
