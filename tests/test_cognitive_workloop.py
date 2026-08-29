from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from binggupack.cognitive.catchup import collect_catchup
from binggupack.cognitive.patterns import select_next_best_action
from binggupack.cognitive.workloop import run_cognitive_workloop
from binggupack.pack import outcome_attribution as OA
from binggupack.pack import recall_trace as RT


def test_seven_pattern_workloop_is_ephemeral_and_connected(tmp_path: Path):
    sentinel = tmp_path / "ledger.sqlite"
    sentinel.write_bytes(b"not-a-real-ledger-but-must-not-change")
    before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    spec = {
        "request": "Implement safe API integration. Preserve approval. Deliver tests and docs.",
        "repo_state": {"available": True, "branch": "main", "head": "abc", "clean": False,
                       "changed": ["M api.py"], "last_commit": "abc base", "test_state": "not run"},
        "recall": [{"node_id": "m1", "claim": "API v2 previously broke approval",
                    "semantic_subtype": "버그패턴", "effect": "avoid", "applies_to": "ship",
                    "weight": 1.0}],
        "outcomes": [{"applied_node_ids": ["m1"], "application": "applied", "result": "failure",
                      "evidence_digest": "d" * 64}],
        "objections": [{"text": "approval can be bypassed", "impact": 1.0, "likelihood": 0.8,
                         "falsification_test": "run forged approval regression", "test_cost": 0.1}],
        "falsification_result": "pass",
        "verification": {"passed": True, "evidence_refs": ["pytest:approval"]},
        "candidate_items": [{"kind": "lesson", "text": "Keep forged approval regression.",
                             "source_refs": ["pytest:approval"], "external_claims": [{
                                 "claim_id": "api-v2", "claim": "API v2 supports JSON",
                                 "claim_type": "external_api"}]}],
        "fact_evidence": [{"claim_id": "api-v2", "stance": "supports",
                           "source_uri": "https://example.test/api-v2",
                           "source_digest": "e" * 64, "checked_at": "2026-08-29T00:00:00Z"}],
        "actions": [
            {"id": "ship", "action": "Ship", "value": 0.9, "urgency": 0.8,
             "effort": 0.1, "risk": 0.2, "memory_ids": ["m1"]},
            {"id": "test", "action": "Run approval regression", "value": 0.6,
             "urgency": 0.5, "effort": 0.1, "risk": 0.0, "resolves_blocker": True,
             "memory_ids": ["m1"]},
        ],
        "now": "2026-08-29T00:00:00Z",
    }
    out = run_cognitive_workloop(spec)
    assert out["readchk"]["recall_query"]
    assert out["hate"]["status"] == "FALSIFIED"
    assert out["factchk"][0]["status"] == "VERIFIED"
    assert out["sip"]["candidates"]
    assert out["sip"]["commit_allowed"] is False
    assert out["sip"]["candidates"][0]["evidence_binding"] == "EPHEMERAL_ONLY"
    assert out["sip"]["candidates"][0]["save_ready"] is False
    assert out["nba"]["action_id"] == "test"
    assert out["nba"]["recall_changed_decision"] is True
    assert out["safety"] == {
        "writes": 0, "auto_save": False, "approval_bypass": False,
        "external_mutation": False,
    }
    assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == before


def test_workloop_refuses_to_claim_verified_when_external_evidence_is_missing():
    out = run_cognitive_workloop({
        "request": "Document external API behavior",
        "candidate_items": [{"kind": "lesson", "text": "API behavior", "external_claims": [{
            "claim_id": "api", "claim": "API supports X", "claim_type": "external_api"}]}],
        "fact_evidence": [], "actions": [{"id": "verify", "action": "Verify source", "value": 1.0}],
        "now": "2026-08-29T00:00:00Z",
    })
    assert out["factchk"][0]["status"] == "UNVERIFIED"
    assert out["sip"]["candidates"][0]["fact_status"] == "UNVERIFIED"


def test_recall_decision_action_outcome_next_decision_and_catchup_closed_loop(
    tmp_path: Path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    import os
    import subprocess

    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    for args in (("init",), ("config", "user.email", "test@example.invalid"),
                 ("config", "user.name", "Test")):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
    (repo / "work.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "work.txt"], cwd=repo, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, env=env, check=True, capture_output=True)

    home = tmp_path / "home"
    home.mkdir()
    ledger = home / "ledger.sqlite"
    con = sqlite3.connect(ledger)
    con.execute("CREATE TABLE nodes(node_id TEXT PRIMARY KEY,node_type TEXT,sentence TEXT,state TEXT,"
                "semantic_subtype TEXT,created_at TEXT,use_count INTEGER DEFAULT 0,supersedes TEXT)")
    con.execute("CREATE TABLE edges(edge_id TEXT,relation TEXT,source TEXT,target TEXT,state TEXT)")
    con.execute("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)", (
        "m1", "judgment", "deployment retry previously failed", "active", "버그패턴",
        "2026-08-01T00:00:00Z", 0, None,
    ))
    con.commit()
    con.close()

    monkeypatch.setenv("BINGGU_RECALL_TRACE", "1")
    recall = [{"node_id": "m1", "claim": "deployment retry previously failed",
               "semantic_subtype": "버그패턴", "effect": "avoid", "applies_to": "retry",
               "weight": 1.0}]
    initial = select_next_best_action([
        {"id": "retry", "action": "Retry deployment", "value": 0.9, "memory_ids": ["m1"]},
        {"id": "inspect", "action": "Inspect failure", "value": 0.6, "memory_ids": []},
    ], {"recall": recall, "outcomes": []})
    assert initial["action_id"] == "inspect"
    assert initial["recall_changed_decision"] is True

    trace = RT.record_trace("deployment", "preflight", recall, "2026-08-29T00:00:00Z", home=str(home))
    assert trace["recorded"] is True
    outcome = OA.record_run_outcome(
        trace["trace_id"], ["m1"], "applied", "failure", "pytest", "f" * 64,
        "2026-08-29T00:01:00Z", home=str(home),
    )
    assert outcome["recorded"] is True

    recent = [{"outcome_id": outcome["outcome_id"], "applied_node_ids": ["m1"],
               "application": "applied", "result": "failure", "evidence_digest": "f" * 64}]
    without_outcome = select_next_best_action([
        {"id": "retry", "action": "Retry deployment", "value": 0.9, "memory_ids": ["m1"]},
        {"id": "inspect", "action": "Inspect failure", "value": 0.6, "memory_ids": []},
    ], {})
    after_outcome = select_next_best_action([
        {"id": "retry", "action": "Retry deployment", "value": 0.9, "memory_ids": ["m1"]},
        {"id": "inspect", "action": "Inspect failure", "value": 0.6, "memory_ids": []},
    ], {"outcomes": recent})
    assert without_outcome["action_id"] == "retry"
    assert after_outcome["action_id"] == "inspect"

    resumed = collect_catchup(repo, query="deployment", ledger_path=ledger,
                              now="2026-08-29T00:02:00Z")
    assert any(item.get("source_ref", "").startswith("outcome:")
               for item in resumed["known_failures"])
    assert resumed["safety"]["writes"] == 0
