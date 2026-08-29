# -*- coding: utf-8 -*-
"""Binggu App Path read core 회귀 — 5 tool contract·안전 게이트·candidate/evidence·read-only.
synthetic pack 만 사용 · 운영 ~/.binggupack 미접촉 · 네트워크 0."""
from contextlib import suppress
import hashlib
import json
import importlib
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.app.read_core import PackRepository, PackService       # noqa: E402
from binggupack.app import conformance as C                           # noqa: E402


def _svc(root):
    return PackService(PackRepository(root))


def _snap(root):
    out = {}
    for r, _, fs in os.walk(root):
        for fn in fs:
            p = os.path.join(r, fn)
            with suppress(OSError):
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


@pytest.fixture
def repo(tmp_path):
    root = str(tmp_path / "packs")
    os.makedirs(root)
    C.build_fixtures(root)
    return root


# ── pack_list ──
def test_pack_list_deterministic_and_paginated(repo):
    s = _svc(repo)
    a = s.list_packs()
    b = s.list_packs()
    assert [p["pack_id"] for p in a["packs"]] == [p["pack_id"] for p in b["packs"]]
    assert a["packs"][0]["pack_id"] == "fx_multi_v1"   # updated_at DESC
    p1 = s.list_packs(limit=1)
    p2 = s.list_packs(cursor=p1["next_cursor"], limit=1)
    assert p1["packs"][0]["pack_id"] != p2["packs"][0]["pack_id"]
    assert p1["next_cursor"] and "/" not in p1["next_cursor"] and repo not in p1["next_cursor"]


def test_pack_list_excludes_invalid_pack(repo):
    ids = {p["pack_id"] for p in _svc(repo).list_packs()["packs"]}
    for bad in ("fx_malformed_v1", "fx_pii_v1", "fx_oversize_v1", "fx_promo_v1"):
        assert bad not in ids
    assert _svc(repo).list_packs()["invalid_pack_count"] >= 3


# ── pack_summary ──
def test_pack_summary_exact_pack_id(repo):
    s = _svc(repo).get_pack_summary("fx_multi_v1")
    assert s["pack_id"] == "fx_multi_v1" and s["counts"]["nodes"] == 2
    assert "자동 병합" in s["candidate_note"]


def test_pack_summary_missing_safe_error(repo):
    s = _svc(repo)
    assert s.get_pack_summary("nope").get("error_code") == "PACK_NOT_FOUND"
    assert s.get_pack_summary("fx_multi").get("error_code") == "PACK_NOT_FOUND"  # prefix
    err = s.get_pack_summary("nope")
    assert repo not in json.dumps(err) and "Traceback" not in json.dumps(err)


# ── evidence_search ──
def test_evidence_search_lexical_deterministic(repo):
    s = _svc(repo)
    a = s.search_evidence("fx_multi_v1", "마진 보류")
    b = s.search_evidence("fx_multi_v1", "마진 보류")
    assert a == b and a["hits"] and a["hits"][0]["evidence_id"] == "EU1"
    assert a["hits"][0]["score"] > 0.0 and a["hits"][0]["candidate"] is True


def test_evidence_search_no_network_or_cache(repo, tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    before = _snap(repo)
    r = _svc(repo).search_evidence("fx_multi_v1", "마진")
    assert "hits" in r
    assert _snap(repo) == before   # cache/index write 0


def test_evidence_search_redacts_source(repo):
    r = _svc(repo).search_evidence("fx_multi_v1", "마진 보류")
    blob = json.dumps(r, ensure_ascii=False)
    assert "seed/x.md" not in blob and "source_path" not in blob
    assert _svc(repo).search_evidence("fx_multi_v1", "x").get("error_code") == "QUERY_TOO_SHORT"


# ── node_edge_lookup ──
def test_node_lookup_exact_id(repo):
    nl = _svc(repo).lookup_node_edges("fx_multi_v1", node_id="node:mu:b")
    assert nl["node"]["id"] == "node:mu:b" and nl["node"]["candidate"] is True


def test_node_lookup_does_not_accept_prefix(repo):
    s = _svc(repo)
    for bad in ("node:mu", "mu:b", "node:mu:"):
        assert s.lookup_node_edges("fx_multi_v1", node_id=bad).get("error_code") == "NODE_NOT_FOUND"


def test_keyword_lookup_ambiguous(repo):
    r = _svc(repo).lookup_node_edges("fx_conflict_v1", keyword="이 방식을 채택")
    assert r.get("error_code") == "AMBIGUOUS_KEYWORD" and len(r["candidate_ids"]) >= 2


def test_edges_preserve_evidence_refs(repo):
    nl = _svc(repo).lookup_node_edges("fx_multi_v1", node_id="node:mu:b")
    assert nl["edges"] and nl["edges"][0]["evidence_refs"] == ["EU1"]
    assert nl["edges"][0]["evidence_backed"] is True


# ── handoff ──
def test_handoff_matches_phase3_template(repo):
    md = _svc(repo).build_handoff_context("fx_multi_v1")["context_markdown"]
    assert "Answer ONLY from evidence_refs" in md
    assert "do not confirm, merge, or promote" in md
    assert "contradicts" in md   # 규칙 4번


def test_handoff_candidate_only(repo):
    md = _svc(repo).build_handoff_context("fx_multi_v1")["context_markdown"]
    assert "candidate" in md and "do not merge or promote" in md.lower()


def test_handoff_preserves_contradictions(repo):
    md = _svc(repo).build_handoff_context("fx_conflict_v1")["context_markdown"]
    assert "contradicts" in md


def test_handoff_missing_evidence_does_not_invent(repo):
    # missing evidence_ref → edge.evidence_refs 빈 목록 + no-evidence 표시(없는 ref 생성 0)
    mr = _svc(repo).lookup_node_edges("fx_missingref_v1", node_id="node:mr:x")
    assert mr["edges"][0]["evidence_refs"] == [] and mr["edges"][0]["evidence_backed"] is False
    md = _svc(repo).build_handoff_context("fx_missingref_v1")["context_markdown"]
    assert "ENOPE" not in md   # 없는 evidence id 를 지어내지 않음


def test_handoff_output_cap(tmp_path):
    root = str(tmp_path / "big")
    os.makedirs(root)
    nodes = [C._node("node:big:%04d" % i, "긴 문장 반복 " * 40 + str(i), ["EB%d" % i]) for i in range(200)]
    # 모든 edge 를 selected 후보 node 에 연결 → handoff 출력이 40KB cap 초과하도록
    edges = [C._edge("edge:big:%04d" % i, "node:big:0000", "node:big:%04d" % (i % 200), "refines", [])
             for i in range(400)]
    evs = [C._ev("EB%d" % i) for i in range(200)]
    C._write_pack(root, "fx_big_v1", nodes, edges, evs)
    h = _svc(root).build_handoff_context("fx_big_v1", max_nodes=50)
    assert h["truncated"] is True
    assert len(h["context_markdown"].encode("utf-8")) <= 40 * 1024 + 200


# ── 안전 게이트 ──
def test_pack_path_traversal_blocked(repo):
    s = _svc(repo)
    for bad in ("../outside", "..\\outside", "a/b", "packs/../x"):
        assert s.get_pack_summary(bad).get("error_code") == "PACK_NOT_FOUND"


def test_pack_symlink_blocked(tmp_path):
    root = str(tmp_path / "packs")
    os.makedirs(root)
    real = C._write_pack(str(tmp_path / "hidden"), "fx_t_v1",
                         [C._node("node:t:x", "문장.", ["ET1"])], [], [C._ev("ET1")])
    link = os.path.join(root, "fx_link_v1")
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 생성 불가(권한)")
    assert _svc(root).get_pack_summary("fx_link_v1").get("error_code") == "PACK_NOT_FOUND"


def test_unsafe_pack_pii_or_secret_blocked(repo):
    # fx_pii_v1(주민번호 형태) 는 list/summary 에서 서비스되지 않는다
    assert _svc(repo).get_pack_summary("fx_pii_v1").get("error_code") == "PACK_NOT_FOUND"
    assert "fx_pii_v1" not in {p["pack_id"] for p in _svc(repo).list_packs()["packs"]}


def test_malformed_pack_fail_closed(repo):
    # malformed jsonl 은 부분 서비스 0 — 전체 unavailable
    assert _svc(repo).get_pack_summary("fx_malformed_v1").get("error_code") == "PACK_NOT_FOUND"


# ── read-only ──
def test_all_tools_read_only(repo):
    before = _snap(repo)
    s = _svc(repo)
    s.list_packs()
    s.get_pack_summary("fx_multi_v1")
    s.search_evidence("fx_multi_v1", "마진")
    s.lookup_node_edges("fx_multi_v1", node_id="node:mu:b")
    s.build_handoff_context("fx_multi_v1")
    s.get_pack_summary("bad")
    s.search_evidence("fx_multi_v1", "x")
    assert _snap(repo) == before


def test_repeated_calls_byte_identical(repo):
    s = _svc(repo)
    a = json.dumps(s.build_handoff_context("fx_multi_v1"), ensure_ascii=False, sort_keys=True)
    b = json.dumps(s.build_handoff_context("fx_multi_v1"), ensure_ascii=False, sort_keys=True)
    assert a == b


# ── packaging ──
def test_wheel_contains_app_read_core():
    rc = importlib.import_module("binggupack.app.read_core")
    cf = importlib.import_module("binggupack.app.conformance")
    assert hasattr(rc, "PackService") and hasattr(cf, "selftest")
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        pj = f.read()
    assert 'include = ["binggupack*"' in pj or "binggupack*" in pj   # 하위패키지 자동 포함


def test_external_cwd_conformance_smoke(tmp_path):
    ext = str(tmp_path / "ext")
    os.makedirs(ext)
    e = dict(os.environ)
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONPATH"] = ROOT + os.pathsep + os.path.join(ROOT, "scripts")
    r = subprocess.run([sys.executable, "-m", "binggupack.app.conformance", "--selftest"],
                       cwd=ext, env=e, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    assert r.returncode == 0, r.stdout[-400:] + r.stderr[-400:]
    assert "GATE=GO" in r.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
