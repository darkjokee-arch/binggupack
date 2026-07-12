# -*- coding: utf-8 -*-
"""Binggu App Path read core — transport 없는 5-tool contract conformance harness.

synthetic pack 만 사용(운영 ledger/네트워크 0). temp 격리에서 pack fixture 10종을 만들고 5 tool 의
계약(안전 게이트·exact lookup·candidate 보존·evidence 미생성·read-only)을 검증한다.

    python -m binggupack.app.conformance --selftest
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

from binggupack.app.read_core import PackRepository, PackService


def _manifest(pack_id, extra=None):
    m = {"format_version": "opencrab-pack-v1", "pack_id": pack_id, "pack_type": "candidate",
         "status": "validated", "scope": "domain:bid", "promotion_allowed_default": False,
         "depends_on": [], "cross_pack_tags": ["bid"],
         "merge_policy": {"mode": "review", "target": "staging", "cross_pack": "isolated"},
         "evidence_policy": {"min_evidence": 1, "source": "synthetic_fixture"},
         "risk_level": "low", "created_from": "conformance", "updated_at": "2026-07-10T00:00:00Z"}
    if extra:
        m.update(extra)
    return m


def _node(nid, sentence, refs, kind="개념"):
    return {"id": nid, "node_type": "Concept", "label": sentence,
            "properties": {"label_kind": kind, "sentence": sentence, "candidate": True,
                           "evidence_status": "partial"}, "evidence_refs": refs, "promotion_allowed": False}


def _edge(eid, src, tgt, rel, refs):
    return {"id": eid, "edge_type": "SupportsJudgment", "source": src, "target": tgt,
            "properties": {"relation": rel, "candidate": True}, "evidence_refs": refs, "promotion_allowed": False}


def _ev(evid, path="seed/x.md"):
    return {"evidence_id": evid, "kind": "file_pointer", "source_path": path, "note": "synthetic"}


def _write_pack(root, pack_id, nodes, edges, evidence, nested=True, manifest_extra=None):
    d = os.path.join(root, pack_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(_manifest(pack_id, manifest_extra), f, ensure_ascii=False)
    gd = os.path.join(d, "graph") if nested else d
    ed = os.path.join(d, "evidence") if nested else d
    os.makedirs(gd, exist_ok=True)
    os.makedirs(ed, exist_ok=True)
    with open(os.path.join(gd, "nodes.jsonl"), "w", encoding="utf-8") as f:
        for n in nodes:
            f.write(json.dumps(n, ensure_ascii=False) + "\n")
    with open(os.path.join(gd, "edges.jsonl"), "w", encoding="utf-8") as f:
        for e in edges:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    with open(os.path.join(ed, "index.jsonl" if nested else "evidence_index.jsonl"), "w", encoding="utf-8") as f:
        for v in evidence:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    return d


def build_fixtures(root):
    """§12 synthetic pack 10종. 반환: {fixture_key: pack_id 또는 특수 marker}."""
    info = {}
    # 1 valid minimal
    _write_pack(root, "fx_minimal_v1", [_node("node:m:1", "최소 유효 문장.", ["EM1"])], [], [_ev("EM1")])
    info["valid_minimal"] = "fx_minimal_v1"
    # 2 valid multi
    _write_pack(root, "fx_multi_v1",
                [_node("node:mu:a", "마진이 낮으면 응찰을 보류한다.", ["EU1"], "판단"),
                 _node("node:mu:b", "기초금액은 발주기관 기준 금액이다.", ["EU2"], "개념")],
                [_edge("edge:mu:1", "node:mu:b", "node:mu:a", "supports_judgment", ["EU1"])],
                [_ev("EU1"), _ev("EU2")], manifest_extra={"updated_at": "2026-07-12T00:00:00Z"})
    info["valid_multi"] = "fx_multi_v1"
    # 3 contradicting
    _write_pack(root, "fx_conflict_v1",
                [_node("node:cf:x", "이 방식을 채택한다.", ["EC1"], "판단"),
                 _node("node:cf:y", "이 방식을 채택하면 안 된다.", ["EC2"], "판단")],
                [_edge("edge:cf:1", "node:cf:x", "node:cf:y", "contradicts", ["EC1"])],
                [_ev("EC1"), _ev("EC2")])
    info["contradicting"] = "fx_conflict_v1"
    # 4 missing evidence ref (edge refs 존재하지 않는 evidence 를 가리킴)
    _write_pack(root, "fx_missingref_v1",
                [_node("node:mr:x", "근거 없는 문장.", ["ENOPE"])],
                [_edge("edge:mr:1", "node:mr:x", "node:mr:x", "refines", ["ENOPE2"])],
                [_ev("EMR1")])   # EMR1 만 존재 · ENOPE/ENOPE2 부재
    info["missing_ref"] = "fx_missingref_v1"
    # 5 malformed jsonl
    d = _write_pack(root, "fx_malformed_v1", [_node("node:ml:x", "정상.", ["EML1"])], [], [_ev("EML1")])
    with open(os.path.join(d, "graph", "nodes.jsonl"), "a", encoding="utf-8") as f:
        f.write("{ broken json line\n")
    info["malformed"] = "fx_malformed_v1"
    # 6 path traversal pack id (서비스 호출 시 거부 — 실제 디렉터리 안 만듦)
    info["traversal_id"] = "../outside_repo"
    # 7 symlink pack (권한 없으면 skip)
    info["symlink_target"] = None
    try:
        real = _write_pack(os.path.join(root, "_hidden"), "fx_symtarget_v1",
                           [_node("node:sy:x", "symlink target.", ["ESY1"])], [], [_ev("ESY1")])
        link = os.path.join(root, "fx_symlink_v1")
        os.symlink(real, link)
        info["symlink_target"] = "fx_symlink_v1"
    except (OSError, NotImplementedError):
        info["symlink_target"] = "SKIP"
    # 8 oversized (manifest > cap)
    _write_pack(root, "fx_oversize_v1", [_node("node:ov:x", "문장.", ["EOV1"])], [], [_ev("EOV1")],
                manifest_extra={"pad": "x" * (300 * 1024)})
    info["oversized"] = "fx_oversize_v1"
    # 9 ambiguous keyword(=contradicting 재사용 · 동일 접두 2노드)
    info["ambiguous"] = "fx_conflict_v1"
    # 10 PII/secret unsafe (주민번호 형태) — 리터럴을 분리 조립해 소스 tree-scan pii_rrn 자가오탐 회피.
    rrn = "900101" + "-" + "1234567"
    _write_pack(root, "fx_pii_v1", [_node("node:pi:x", "고객 주민번호 " + rrn + " 노출.", ["EPI1"])],
                [], [_ev("EPI1")])
    info["pii"] = "fx_pii_v1"
    return info


def _snap(root):
    out = {}
    for r, _, fs in os.walk(root):
        for fn in fs:
            pth = os.path.join(r, fn)
            try:
                with open(pth, "rb") as fh:
                    out[os.path.relpath(pth, root)] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                pass
    return out


def run_conformance(root):
    info = build_fixtures(root)
    svc = PackService(PackRepository(root))
    before = _snap(root)
    checks = []

    def ck(name, ok):
        checks.append((name, bool(ok)))

    lst = svc.list_packs()
    ids = {pk["pack_id"] for pk in lst["packs"]}
    ck("list_excludes_malformed", "fx_malformed_v1" not in ids)
    ck("list_excludes_pii", "fx_pii_v1" not in ids)
    ck("list_excludes_oversized", "fx_oversize_v1" not in ids)
    ck("list_includes_valid", {"fx_minimal_v1", "fx_multi_v1", "fx_conflict_v1"} <= ids)
    ck("list_deterministic_updated_desc", [p["pack_id"] for p in lst["packs"]][0] == "fx_multi_v1")

    ck("summary_exact", svc.get_pack_summary("fx_multi_v1").get("pack_id") == "fx_multi_v1")
    ck("summary_missing_safe", svc.get_pack_summary("fx_zzz").get("error_code") == "PACK_NOT_FOUND")

    es = svc.search_evidence("fx_multi_v1", "마진 보류")
    ck("evidence_lexical_hit", bool(es.get("hits")))
    ck("evidence_no_source_path", "seed/x.md" not in json.dumps(es, ensure_ascii=False))
    ck("evidence_short_query", svc.search_evidence("fx_multi_v1", "x").get("error_code") == "QUERY_TOO_SHORT")

    nl = svc.lookup_node_edges("fx_multi_v1", node_id="node:mu:b")
    ck("node_exact", nl.get("node", {}).get("id") == "node:mu:b")
    ck("node_prefix_rejected", svc.lookup_node_edges("fx_multi_v1", node_id="node:mu").get("error_code") == "NODE_NOT_FOUND")
    ck("keyword_ambiguous", svc.lookup_node_edges("fx_conflict_v1", keyword="이 방식을 채택").get("error_code") == "AMBIGUOUS_KEYWORD")

    mr = svc.lookup_node_edges("fx_missingref_v1", node_id="node:mr:x")
    # 없는 evidence_ref 를 새로 만들지 않음 — edge.evidence_refs 는 빈 목록 + evidence_backed=false
    ck("missing_ref_no_invention", mr.get("edges") and mr["edges"][0]["evidence_refs"] == []
       and mr["edges"][0]["evidence_backed"] is False)

    h = svc.build_handoff_context("fx_conflict_v1")
    ck("handoff_candidate_only", "candidate" in h.get("context_markdown", ""))
    ck("handoff_preserves_contradicts", "contradicts" in h.get("context_markdown", ""))
    ck("handoff_no_source_path", "seed/x.md" not in h.get("context_markdown", ""))

    ck("traversal_id_blocked", svc.get_pack_summary(info["traversal_id"]).get("error_code") == "PACK_NOT_FOUND")
    if info["symlink_target"] not in (None, "SKIP"):
        ck("symlink_blocked", svc.get_pack_summary(info["symlink_target"]).get("error_code") == "PACK_NOT_FOUND")

    ck("read_only_byte_identical", _snap(root) == before)
    # repeated calls
    a = json.dumps(svc.get_pack_summary("fx_multi_v1"), ensure_ascii=False, sort_keys=True)
    b = json.dumps(svc.get_pack_summary("fx_multi_v1"), ensure_ascii=False, sort_keys=True)
    ck("repeated_calls_identical", a == b)
    return checks


def selftest():
    print("=" * 60)
    print("Binggu App Path read core — conformance (synthetic · read-only)")
    print("=" * 60)
    tmp = tempfile.mkdtemp(prefix="bgp_app_conf_")
    try:
        checks = run_conformance(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))
    total = len(checks)
    print("-" * 60)
    print("=== %d/%d ===" % (passed, total))
    print("GATE=%s" % ("GO" if passed == total else "STOP"))
    return 0 if passed == total else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print("usage: python -m binggupack.app.conformance --selftest")
    sys.exit(2)
