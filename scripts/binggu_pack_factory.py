"""binggu_pack_factory — parsed documents + evidence chunks → OpenBinggu pack.

owner 조건부 GO 반영(조건5): watcher_pack_builder_m0 의 manifest 계약/검증 흐름은 참고하되
diff 전용 구조에 묶지 않는다. 입력은 일반 'parsed documents'(harvest_one 산출물 모음),
출력은 OpenBinggu pack contract(manifest + nodes + edges + evidence_index + evidence_chunk).
완료 기준 = openbinggu_pack_validate.validate_pack 통과(STOP 아님).

불변(전 빌더 공통): promotion_allowed_default=false · candidate-only · production write 0.
이 모듈은 pack dict 를 만들고(메모리) 옵션으로 temp 5파일 write 만 — 운영 store/ledger 미접촉.
"""
import os
import re
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openbinggu_pack_validate as PV  # noqa: E402  (manifest 계약 검증 — 완료 기준)


def _slug(text):
    s = re.sub(r"[^0-9a-z가-힣]+", "-", str(text or "").lower()).strip("-")
    return s[:48] or "topic"


def _manifest(topic, pack_id, counts):
    """validate_pack 통과(PASS) 형식의 manifest. candidate/staging/isolated/promotion=false."""
    return {
        "pack_id": pack_id or ("topic/" + _slug(topic)),
        "pack_type": "candidate",                 # 후보 — production 직행 금지(rule4)
        "scope": "project:openbinggu",
        "depends_on": [],
        "evidence_policy": {"source": "harvest", "min_evidence": 0},
        "merge_policy": {"mode": "review", "target": "staging", "cross_pack": "isolated"},
        "promotion_allowed_default": False,       # rule2 — 자동승격 금지
        "status": "staged",
        "cross_pack_tags": [],
        "risk_level": "low",
        "created_from": "binggu_pack_factory",
        "counts": counts,                         # 부가(검증 대상 외 키 — validate 는 무시)
        "topic": topic,
    }


def build_pack(topic, documents, pack_id=None, out_dir=None):
    """documents: [{nodes, evidence_index, evidence_chunks, source_ref?, parse_artifacts?}] 모음.
    (harvest_one 반환을 그대로 모으면 됨 — 'evidence_chunks' 는 _content_chunks/_derived_chunks 산출.)

    반환: {status, verdict, pack, counts, validate}. validate STOP 이면 status=BLOCK.
    """
    nodes, ev_index, chunks = [], [], []
    seen_nodes, seen_ev, seen_chunk = set(), set(), set()
    parsers = set()

    for d in documents or []:
        for n in d.get("nodes", []):
            nid = n.get("id")
            if nid in seen_nodes:           # 중복 제거(요구8)
                continue
            seen_nodes.add(nid)
            nodes.append(n)
        for e in d.get("evidence_index", []):
            eid = e.get("evidence_id")
            if eid in seen_ev:
                continue
            seen_ev.add(eid)
            ev_index.append(e)
        for c in d.get("evidence_chunks", []) or d.get("chunks", []):
            cid = c.get("item_id")
            if cid in seen_chunk:
                continue
            seen_chunk.add(cid)
            chunks.append(c)
        for pa in d.get("parse_artifacts", []):
            if pa.get("parser"):
                parsers.add(pa["parser"])

    counts = {"nodes": len(nodes), "edges": 0,
              "evidence_index": len(ev_index), "evidence_chunk": len(chunks),
              "documents": len(documents or []), "parsers": sorted(parsers)}

    manifest = _manifest(topic, pack_id, counts)
    verdict = PV.validate_pack(manifest)         # 완료 기준 — STOP 이면 실패

    # 코드 불변식 명시 검증(노드 candidate/promotion) — 위반 시 BLOCK(빌드 산출 0).
    cand_ok = all(n.get("properties", {}).get("candidate") is True for n in nodes)
    promo_ok = all(n.get("promotion_allowed") is False for n in nodes)
    if nodes and not (cand_ok and promo_ok):
        return {"status": "BLOCK", "reason": "CANDIDATE_INVARIANT_VIOLATION",
                "verdict": verdict, "pack": None, "counts": counts}

    pack = {
        "format_version": "opencrab-pack-v1",
        "manifest": manifest,
        "nodes": nodes,
        "edges": [],                              # MVP: 엣지 미생성(to_nodes 와 정합). v2 동사형 엣지.
        "evidence_index": ev_index,
        "evidence_chunk": chunks,
    }

    status = "OK" if verdict["verdict"] != "STOP" else "BLOCK"
    written = None
    if out_dir and status == "OK":
        written = _write_pack(pack, out_dir)

    return {"status": status, "verdict": verdict, "pack": pack,
            "counts": counts, "written": written,
            "candidate_all_true": cand_ok if nodes else True,
            "promotion_all_false": promo_ok if nodes else True}


def _write_pack(pack, out_dir):
    """temp 5파일(jsonl) + manifest.json. 운영 store 미접촉(out_dir 는 호출자가 temp 로)."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(pack["manifest"], f, ensure_ascii=False, indent=2)
    for fname, key in [("nodes.jsonl", "nodes"), ("edges.jsonl", "edges"),
                       ("evidence_index.jsonl", "evidence_index"),
                       ("evidence_chunk.jsonl", "evidence_chunk")]:
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            for row in pack[key]:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return out_dir


# ── selftest (메모리 mock documents · 실 네트워크/store 0) ─────────────
def _selftest():
    import tempfile
    ok = []

    def chk(name, cond):
        ok.append(cond)
        print(("  PASS " if cond else "  FAIL ") + name)

    def mk_doc(prefix, n):
        nodes, evidx, chunks = [], [], []
        for i in range(n):
            iid = "EVC-%s-%d" % (prefix, i)
            nid = "node:STAGING:wch:%s%d" % (prefix, i)
            chunks.append({"item_id": iid, "text": "%s 문장 %d 입니다 본문." % (prefix, i),
                           "source": "harvest :: url :: src", "evidence_meta": {"raw_pointer": "x"}})
            evidx.append({"evidence_id": iid, "kind": "file_pointer", "domain": "STAGING_UNASSIGNED",
                          "promotion_allowed": False, "note": "p"})
            nodes.append({"id": nid, "promotion_allowed": False,
                          "properties": {"candidate": True, "sentence": "%s 문장 %d" % (prefix, i)},
                          "evidence_refs": [iid]})
        return {"nodes": nodes, "evidence_index": evidx, "evidence_chunks": chunks,
                "parse_artifacts": [{"parser": "markitdown"}]}

    docs = [mk_doc("A", 3), mk_doc("B", 2)]
    r = build_pack("입찰 가격 예측", docs, out_dir=None)
    chk("F1 pack 조립 OK", r["status"] == "OK")
    chk("F2 validate_pack STOP 아님(완료기준)", r["verdict"]["verdict"] in ("PASS", "REVIEW_ONLY"))
    chk("F2b verdict=PASS", r["verdict"]["verdict"] == "PASS")
    chk("F3 promotion_allowed_default=false", r["pack"]["manifest"]["promotion_allowed_default"] is False)
    chk("F4 노드 5건 수집", r["counts"]["nodes"] == 5)
    chk("F4b evidence_chunk 5건", r["counts"]["evidence_chunk"] == 5)
    chk("F5 parser 출처 기록", "markitdown" in r["counts"]["parsers"])

    # dedup — 같은 doc 두 번
    r2 = build_pack("t", [mk_doc("A", 3), mk_doc("A", 3)])
    chk("F6 중복 노드 제거(3건)", r2["counts"]["nodes"] == 3)

    # 빈 입력도 valid pack
    r3 = build_pack("빈주제", [])
    chk("F7 빈 documents → valid 빈 pack", r3["status"] == "OK" and r3["counts"]["nodes"] == 0)

    # candidate 불변식 위반 노드 → BLOCK
    bad = {"nodes": [{"id": "n1", "promotion_allowed": True, "properties": {"candidate": True}}],
           "evidence_index": [], "evidence_chunks": []}
    r4 = build_pack("t", [bad])
    chk("F8 promotion=true 노드 → BLOCK", r4["status"] == "BLOCK")

    # write roundtrip
    out = tempfile.mkdtemp(prefix="packfac_")
    r5 = build_pack("쓰기", docs, out_dir=out)
    chk("F9 5파일 write", os.path.exists(os.path.join(out, "manifest.json"))
        and os.path.exists(os.path.join(out, "nodes.jsonl")))
    mani = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
    chk("F9b 기록 manifest 재검증 PASS", PV.validate_pack(mani)["verdict"] in ("PASS", "REVIEW_ONLY"))

    total, passed = len(ok), sum(ok)
    print("\nRESULT: %d/%d PASS" % (passed, total))
    print("GATE: " + ("GO" if passed == total else "NO-GO"))
    return passed == total


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_pack_factory — use --selftest, or import build_pack()")
