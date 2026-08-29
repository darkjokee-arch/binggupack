from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path

from binggupack.cognitive.catchup import build_catchup, collect_catchup, render_catchup


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                          text=True, capture_output=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "app.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def _ledger(path: Path, rows: list[tuple[str, str, str, str, str, str | None]]) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE nodes(node_id TEXT PRIMARY KEY,node_type TEXT,sentence TEXT,"
                "state TEXT,semantic_subtype TEXT,created_at TEXT,use_count INTEGER DEFAULT 0,"
                "supersedes TEXT)")
    con.execute("CREATE TABLE edges(edge_id TEXT,relation TEXT,source TEXT,target TEXT,state TEXT)")
    for nid, ntype, sentence, state, subtype, supersedes in rows:
        con.execute("INSERT INTO nodes(node_id,node_type,sentence,state,semantic_subtype,created_at,"
                    "use_count,supersedes) VALUES(?,?,?,?,?,'2026-08-01T00:00:00Z',0,?)",
                    (nid, ntype, sentence, state, subtype, supersedes))
    con.commit()
    con.close()
    return path


def _tree_fingerprint(root: Path) -> dict[str, tuple[int, int, str]]:
    out = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        data = path.read_bytes()
        out[str(path.relative_to(root))] = (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(data).hexdigest(),
        )
    return out


def test_catchup_clean_dirty_non_git_and_repeat_are_deterministic(tmp_path):
    repo = _repo(tmp_path)
    clean = collect_catchup(repo, query="release", ledger_path=None, now="2026-08-29T00:00:00Z")
    assert clean["current_state"]["clean"] is True
    assert clean["current_state"]["branch"]
    assert clean == collect_catchup(repo, query="release", ledger_path=None,
                                    now="2026-08-29T00:00:00Z")

    (repo / "app.txt").write_text("two\n", encoding="utf-8")
    dirty = collect_catchup(repo, query="release", ledger_path=None, now="2026-08-29T00:00:00Z")
    assert dirty["current_state"]["clean"] is False
    assert any("app.txt" in item for item in dirty["what_changed"])

    outside = collect_catchup(tmp_path / "missing", query="x", ledger_path=None,
                              now="2026-08-29T00:00:00Z")
    assert outside["current_state"]["available"] is False
    assert outside["unresolved"]


def test_catchup_memory_priorities_superseded_conflict_outcome_and_context_cap(tmp_path):
    repo = _repo(tmp_path)
    (repo / "app.txt").write_text("dirty\n", encoding="utf-8")
    ledger = _ledger(tmp_path / "ledger.sqlite", [
        ("d-old", "judgment", "release decision use legacy flow", "deprecated", "결정", None),
        ("d-new", "judgment", "release decision requires approval regression", "active", "결정", "d-old"),
        ("c1", "judgment", "release must preserve G4_no_auto", "active", "제약", None),
        ("f1", "judgment", "release failed when approval regression was skipped", "active", "버그패턴", None),
        ("s1", "state", "repository state is clean", "active", "상태", None),
    ] + [
        (f"l{i}", "judgment", f"release supporting lesson {i} " + ("x" * 80), "active", "교훈", None)
        for i in range(20)
    ])
    out = collect_catchup(repo, query="release approval", ledger_path=ledger,
                          now="2026-08-29T00:00:00Z", max_chars=1800,
                          outcome_summary={"overall": {"pending_traces": 2}})
    assert out["decisions"]
    assert out["known_constraints"]
    assert out["known_failures"]
    assert any(x.get("superseded") for x in out["relevant_memory"])
    assert any("LIVE_MEMORY_CONFLICT" in x for x in out["unresolved"])
    assert any("pending" in x.lower() for x in out["unresolved"])
    assert out["budget"]["used_chars"] <= 1800
    assert out["budget"]["omitted_count"] > 0
    text = render_catchup(out)
    for section in ("CURRENT STATE", "WHAT CHANGED", "RELEVANT MEMORY", "DECISIONS",
                    "KNOWN CONSTRAINTS", "KNOWN FAILURES", "UNRESOLVED", "NEXT BEST ACTION"):
        assert section in text


def test_catchup_performs_zero_repository_and_database_writes(tmp_path):
    repo = _repo(tmp_path)
    ledger = _ledger(tmp_path / "ledger.sqlite", [
        ("d1", "judgment", "release requires regression", "active", "결정", None),
    ])
    before_tree = _tree_fingerprint(tmp_path)
    before_db = (ledger.stat().st_size, ledger.stat().st_mtime_ns, hashlib.sha256(ledger.read_bytes()).hexdigest())
    out = collect_catchup(repo, query="release", ledger_path=ledger,
                          now="2026-08-29T00:00:00Z")
    after_tree = _tree_fingerprint(tmp_path)
    after_db = (ledger.stat().st_size, ledger.stat().st_mtime_ns, hashlib.sha256(ledger.read_bytes()).hexdigest())
    assert out["safety"]["writes"] == 0
    assert before_tree == after_tree
    assert before_db == after_db
    assert not (tmp_path / "recall_trace.sqlite").exists()


def test_catchup_fails_closed_on_wal_without_shm(tmp_path):
    repo = _repo(tmp_path)
    ledger = tmp_path / "ledger.sqlite"
    ledger.write_bytes(b"not opened when unsafe WAL residue exists")
    (tmp_path / "ledger.sqlite-wal").write_bytes(b"residue")
    before = _tree_fingerprint(tmp_path)
    out = collect_catchup(repo, query="release", ledger_path=ledger)
    assert out["relevant_memory"] == []
    assert _tree_fingerprint(tmp_path) == before
    assert not (tmp_path / "ledger.sqlite-shm").exists()


def test_catchup_disables_repository_fsmonitor_hook(tmp_path):
    repo = _repo(tmp_path)
    marker = repo / "fsmonitor-ran.txt"
    hook = repo / "fsmonitor-hook"
    hook.write_text("#!/bin/sh\necho ran > fsmonitor-ran.txt\necho\n", encoding="utf-8")
    hook.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", str(hook))
    collect_catchup(repo, query="release", ledger_path=None)
    assert not marker.exists()


def test_catchup_hard_caps_large_dirty_state():
    out = build_catchup(
        repo_state={"available": True, "repo": "x" * 300, "branch": "main", "head": "a" * 40,
                    "clean": False, "changed": [f"M file-{i}-{'x' * 80}" for i in range(100)],
                    "last_commit": "a base", "test_state": "not run"},
        recall_result={"relevant_nodes": []}, outcomes=[],
        outcome_summary={"overall": {"pending_traces": 0}}, superseded=[],
        query="large", max_chars=500,
    )
    context = {key: out[key] for key in (
        "current_state", "what_changed", "relevant_memory", "decisions", "known_constraints",
        "known_failures", "unresolved", "next_best_action",
    )}
    assert len(json.dumps(context, ensure_ascii=False, sort_keys=True)) <= 500
    assert out["budget"]["cap_satisfied"] is True
    assert out["budget"]["omitted_count"] > 0
    assert "changed" not in out["current_state"]
    assert len(render_catchup(out)) <= 500


def test_build_catchup_handles_no_memory_and_explicit_test_state():
    out = build_catchup(
        repo_state={"available": True, "branch": "main", "head": "abc", "clean": True,
                    "changed": [], "last_commit": "abc initial", "test_state": "17 passed"},
        recall_result={"relevant_nodes": []},
        outcomes=[], outcome_summary={"overall": {"pending_traces": 0}},
        superseded=[], query="new work", max_chars=2000,
    )
    assert out["relevant_memory"] == []
    assert out["current_state"]["test_state"] == "17 passed"


def test_catchup_does_not_resurrect_human_overturned_failure():
    out = build_catchup(
        repo_state={"available": True, "branch": "main", "head": "abc", "clean": True,
                    "changed": [], "last_commit": "abc initial", "test_state": "green"},
        recall_result={"relevant_nodes": []},
        outcomes=[{"outcome_id": "o1", "result": "failure", "overturned": True,
                   "evidence_digest": "a" * 64, "signal_only": True}],
        outcome_summary={"overall": {"pending_traces": 0}}, superseded=[],
        query="corrected", max_chars=2000,
    )
    assert out["known_failures"] == []
    assert "known failure" not in out["next_best_action"]["next_best_action"].lower()
