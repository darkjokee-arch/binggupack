from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

from binggupack.cognitive.catchup import collect_catchup
from binggupack.cognitive.patterns import (
    attach_factcheck,
    fact_check_candidate,
    propose_sip_candidates,
    reconstruct_intent,
    select_load_bearing_objection,
    select_next_best_action,
)
from binggupack.eval.paperthin_behavioral import run_reference_behavioral_eval
from binggupack.pack import outcome_attribution as OA
from binggupack.pack import recall_trace as RT
from binggupack.studio.read_model import collect_recall_snapshot


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_canonical_recall_action_outcome_catchup_next_decision_closed_loop(
    tmp_path: Path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "work.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-m", "base")

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

    readchk = reconstruct_intent(
        "Inspect deployment safely. Preserve approval. Deliver verified evidence."
    )
    canonical = collect_recall_snapshot(str(ledger), readchk["recall_query"], limit=5)
    assert canonical["items"][0]["node_id"] == "m1"
    interpreted = [{**canonical["items"][0], "effect": "avoid", "applies_to": "retry", "weight": 1.0}]
    actions = [
        {"id": "retry", "action": "Retry deployment", "value": 0.9},
        {"id": "inspect", "action": "Inspect failure", "value": 0.7, "resolves_blocker": True},
    ]
    decision = select_next_best_action(actions, {"recall": interpreted})
    assert decision["action_id"] == "inspect"
    assert decision["recall_changed_decision"] is True

    artifact = tmp_path / "inspection.txt"
    subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; Path(r'%s').write_text('failure reproduced')" % artifact],
        check=True,
    )
    evidence_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    hate = select_load_bearing_objection(
        [{"text": "retry repeats the failure", "impact": 1.0, "likelihood": 0.9,
          "falsification_test": "inspect failure artifact", "test_cost": 0.1}],
        test_result="fail",
        test_evidence={"test": "inspect failure artifact", "digest": evidence_digest},
        change_kinds=["recall_behavior"],
    )
    assert hate["status"] == "BLOCKER_CONFIRMED"

    fact_text = "External API supports JSON"
    sip = propose_sip_candidates([{"kind": "lesson", "text": fact_text, "external_claims": [{
        "claim_id": "api-json", "claim": fact_text, "claim_type": "external_api",
    }]}])
    before_sip = sorted(path.relative_to(home) for path in home.rglob("*") if path.is_file())
    checked = fact_check_candidate(sip["candidates"][0], [{
        "claim_id": "api-json", "stance": "supports", "source_uri": "https://example.test/spec",
        "source_digest": "a" * 64,
        "claim_digest": hashlib.sha256(fact_text.encode()).hexdigest(),
        "checked_at": "2026-08-28T00:00:00Z",
    }], now="2026-08-29T00:00:00Z")
    proposal = attach_factcheck(sip["candidates"][0], checked)
    after_sip = sorted(path.relative_to(home) for path in home.rglob("*") if path.is_file())
    assert checked["status"] == "VERIFIED"
    assert proposal["canonical_gate_eligible"] is False
    assert proposal["promotion_allowed"] is False
    assert before_sip == after_sip

    monkeypatch.setenv("BINGGU_RECALL_TRACE", "1")
    trace = RT.record_trace(
        readchk["recall_query"], "preflight", canonical["items"],
        "2026-08-29T00:00:00Z", home=str(home),
    )
    outcome = OA.record_run_outcome(
        trace["trace_id"], ["m1"], "applied", "failure", "file", evidence_digest,
        "2026-08-29T00:01:00Z", home=str(home),
    )
    assert outcome["recorded"] is True
    observed = OA.list_run_outcomes_ro(home=str(home), limit=10)
    assert observed[0]["evidence_digest"] == evidence_digest

    resumed = collect_catchup(repo, query="deployment", ledger_path=ledger)
    assert any(item.get("source_ref", "").startswith("outcome:")
               for item in resumed["known_failures"])
    before_outcome_judgment = select_next_best_action(actions, {"outcomes": observed})
    after_outcome_judgment = select_next_best_action(actions, {
        "outcomes": observed,
        "blocker": "verified failure outcome requires inspection before retry",
    })
    assert before_outcome_judgment["action_id"] == "retry"
    assert after_outcome_judgment["action_id"] == "inspect"
    assert after_outcome_judgment["outcome_used_for_ranking"] is False
    assert after_outcome_judgment["outcome_signals"][0]["signal_only"] is True

    evaluation = run_reference_behavioral_eval()
    assert evaluation["mandela"]["verdict"] == "BLOCK"
    assert evaluation["evaluation"]["verdict"] == "INSUFFICIENT EVIDENCE"
