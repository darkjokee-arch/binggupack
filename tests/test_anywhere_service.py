# -*- coding: utf-8 -*-
"""Binggu Anywhere — service logic parity, validation, read-only (synthetic)."""
import json
import os
import tempfile

from binggupack.app import service as SVC
from binggupack.app import snapshot as S
from binggupack.app import conformance as C


def _snapshot(pid, nodes, edges, ev, extra=None):
    src = tempfile.mkdtemp()
    C._write_pack(src, pid, nodes, edges, ev, manifest_extra=extra)
    return S.make_pack_snapshot(os.path.join(src, pid), pid)


def _demo():
    return _snapshot(
        "demo_v1",
        [C._node("node:a:1", "마진이 낮으면 응찰을 보류한다.", ["EA1"], "판단"),
         C._node("node:a:2", "기초금액은 발주기관 기준 금액이다.", ["EA2"]),
         C._node("node:a:3", "이 방식을 채택하면 안 된다.", ["EA3"], "판단")],
        [C._edge("edge:a:1", "node:a:2", "node:a:1", "supports_judgment", ["EA1"]),
         C._edge("edge:a:2", "node:a:1", "node:a:3", "contradicts", ["EA3"])],
        [C._ev("EA1"), C._ev("EA2"), C._ev("EA3")])


def _materialized(tar):
    root = tempfile.mkdtemp()
    SVC.materialize([tar], root)
    return root


def test_dispatch_five_tools_and_unknown():
    tar, _ = _demo()
    root = _materialized(tar)
    for tool in SVC.READ_TOOLS:
        assert tool in ("pack_list", "pack_summary", "evidence_search", "node_edge_lookup", "handoff_context")
    assert SVC.invoke_on_root(root, "pack_upload", {}).get("error_code") == "UNKNOWN_TOOL"


def test_invoke_all_read_tools():
    tar, _ = _demo()
    root = _materialized(tar)
    assert [p["pack_id"] for p in SVC.invoke_on_root(root, "pack_list", {})["packs"]] == ["demo_v1"]
    assert SVC.invoke_on_root(root, "pack_summary", {"pack_id": "demo_v1"})["pack_id"] == "demo_v1"
    assert SVC.invoke_on_root(root, "evidence_search", {"pack_id": "demo_v1", "query": "마진 보류"})["hits"]
    assert SVC.invoke_on_root(root, "node_edge_lookup", {"pack_id": "demo_v1", "node_id": "node:a:1"})["node"]["id"] == "node:a:1"
    md = SVC.invoke_on_root(root, "handoff_context", {"pack_id": "demo_v1"})["context_markdown"]
    assert "candidate" in md


def test_validate_valid_pack():
    tar, dig = _demo()
    v = SVC.validate_and_canonicalize(tar)
    assert v["ok"] and v["pack_id"] == "demo_v1" and v["digest"] == dig
    assert v["counts"] == {"nodes": 3, "edges": 2, "evidence": 3}


def test_invalid_pack_not_published():
    # malformed jsonl -> read core refuses -> not publishable
    src = tempfile.mkdtemp()
    C._write_pack(src, "bad_v1", [C._node("n:x", "정상.", ["E1"])], [], [C._ev("E1")])
    with open(os.path.join(src, "bad_v1", "graph", "nodes.jsonl"), "a", encoding="utf-8") as f:
        f.write("{ broken json line\n")
    tar, _ = S.make_pack_snapshot(os.path.join(src, "bad_v1"), "bad_v1")
    v = SVC.validate_and_canonicalize(tar)
    assert not v["ok"] and v["reason"] == "failed_validation"


def test_unsafe_pack_not_published():
    rrn = "900101" + "-" + "1234567"
    tar, _ = _snapshot("pii_v1", [C._node("n:p", "주민번호 " + rrn + " 노출.", ["EP"])], [], [C._ev("EP")])
    v = SVC.validate_and_canonicalize(tar)
    assert not v["ok"]


def test_oversize_and_bad_bytes_rejected():
    assert SVC.validate_and_canonicalize(b"P" * (S.SNAPSHOT_MAX_BYTES + 1))["reason"] == "snapshot_too_large"
    assert SVC.validate_and_canonicalize("not-bytes")["reason"] == "invalid_bytes"


def test_materialize_skips_unsafe():
    # a corrupt/unsafe tar is skipped; a valid one still serves (no partial service crash)
    good, _ = _demo()
    root = tempfile.mkdtemp()
    ok = SVC.materialize([b"garbage-not-a-tar", good], root)
    assert ok == ["demo_v1"]
    assert [p["pack_id"] for p in SVC.invoke_on_root(root, "pack_list", {})["packs"]] == ["demo_v1"]


def test_candidate_semantics_and_no_source_path():
    tar, _ = _demo()
    root = _materialized(tar)
    ho = SVC.invoke_on_root(root, "handoff_context", {"pack_id": "demo_v1"})["context_markdown"]
    assert "candidate" in ho and "contradicts" in ho  # candidate-only + contradiction preserved
    blob = json.dumps([
        SVC.invoke_on_root(root, "pack_summary", {"pack_id": "demo_v1"}),
        SVC.invoke_on_root(root, "evidence_search", {"pack_id": "demo_v1", "query": "마진 보류"}),
    ], ensure_ascii=False)
    assert "seed/x.md" not in blob and "source_path" not in blob


def test_conformance_parity_gate_go():
    root = tempfile.mkdtemp()
    checks = C.run_conformance(root)
    assert all(ok for _, ok in checks) and len(checks) >= 20
