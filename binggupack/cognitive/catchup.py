"""Read-only repository + canonical recall + outcome re-entry briefing."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

_DECISION_SUBTYPES = {"결정", "판단", "decision"}
_CONSTRAINT_SUBTYPES = {"제약", "원칙", "constraint", "policy"}
_FAILURE_SUBTYPES = {"버그패턴", "교훈", "failure", "lesson"}


def _git(repo: Path, *args: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", *args], cwd=str(repo), env=env,
            text=True, encoding="utf-8", errors="replace", capture_output=True,
            timeout=10, check=False,
        )
        return proc.returncode, proc.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def snapshot_repository(repo_path: str | os.PathLike[str], *, test_state: str | None = None) -> dict[str, Any]:
    """Read live Git state with optional locks disabled and no fallback writes."""
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        return {"available": False, "repo": str(repo), "error": "repository path unavailable",
                "branch": None, "head": None, "clean": None, "changed": [],
                "last_commit": None, "test_state": test_state or "not provided"}
    rc, root = _git(repo, "rev-parse", "--show-toplevel")
    if rc != 0:
        return {"available": False, "repo": str(repo), "error": "not a git repository",
                "branch": None, "head": None, "clean": None, "changed": [],
                "last_commit": None, "test_state": test_state or "not provided"}
    root_path = Path(root).resolve()
    _rc, branch = _git(root_path, "branch", "--show-current")
    _rc, head = _git(root_path, "rev-parse", "HEAD")
    _rc, last_commit = _git(root_path, "log", "-1", "--pretty=%h %s")
    _rc, status = _git(root_path, "status", "--porcelain=v1", "--untracked-files=normal")
    changed = [line for line in status.splitlines() if line.strip()]
    return {
        "available": True,
        "repo": str(root_path),
        "branch": branch or "DETACHED",
        "head": head,
        "clean": not changed,
        "changed": changed,
        "last_commit": last_commit,
        "test_state": test_state or "not provided",
    }


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in str(text or "").replace("\n", " ").split() if len(token) >= 2}


def _read_superseded_ro(ledger_path: str | os.PathLike[str] | None, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Read only relevant historical decisions; never applies schema or creates files."""
    if not ledger_path or not os.path.exists(ledger_path):
        return []
    con = None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(str(ledger_path)), uri=True)
        cols = {row[1] for row in con.execute("PRAGMA table_info(nodes)")}
        if not {"node_id", "sentence", "state"} <= cols:
            return []
        subtype = "semantic_subtype" if "semantic_subtype" in cols else "NULL"
        supersedes = "supersedes" if "supersedes" in cols else "NULL"
        rows = con.execute(
            "SELECT node_id,sentence,state,%s,%s FROM nodes ORDER BY node_id" % (subtype, supersedes)
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        if con is not None:
            con.close()
    replaced_by = {row[4]: row[0] for row in rows if row[4]}
    qtokens = _tokens(query)
    found = []
    for node_id, sentence, state, semantic_subtype, _supersedes in rows:
        if state in (None, "active", "confirmed") or semantic_subtype not in _DECISION_SUBTYPES:
            continue
        stokens = _tokens(sentence)
        if qtokens and not qtokens.intersection(stokens):
            continue
        found.append({
            "node_id": node_id,
            "claim": str(sentence or "")[:240],
            "semantic_subtype": semantic_subtype,
            "superseded": True,
            "superseded_by": replaced_by.get(node_id),
            "source_ref": "memory:%s" % str(node_id)[-8:],
        })
    return found[:limit]


def _read_outcomes_ro(home: str | os.PathLike[str] | None, limit: int = 10) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read outcome rows and aggregate without calling schema-applying helpers."""
    if not home:
        return [], {"overall": {"traces": 0, "outcomes": 0, "pending_traces": 0}, "signal_only": True}
    path = os.path.join(str(home), "recall_trace.sqlite")
    if not os.path.exists(path):
        return [], {"overall": {"traces": 0, "outcomes": 0, "pending_traces": 0}, "signal_only": True}
    con = None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(path), uri=True)
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        trace_count = (con.execute("SELECT COUNT(*) FROM recall_traces").fetchone()[0]
                       if "recall_traces" in tables else 0)
        if "recall_run_outcomes" not in tables:
            return [], {"overall": {"traces": trace_count, "outcomes": 0,
                                      "pending_traces": trace_count}, "signal_only": True}
        rows = con.execute(
            "SELECT outcome_id,trace_id,applied_node_ids_json,application,result,"
            "evidence_digest,evidence_kind,trust_tier,supersedes,ts "
            "FROM recall_run_outcomes ORDER BY ts,outcome_id"
        ).fetchall()
    except sqlite3.Error:
        return [], {"overall": {"traces": 0, "outcomes": 0, "pending_traces": 0}, "signal_only": True}
    finally:
        if con is not None:
            con.close()
    superseded_ids = {row[8] for row in rows if row[8]}
    results = []
    linked = set()
    for row in rows:
        if row[8] or row[0] in superseded_ids:
            continue
        try:
            nodes = json.loads(row[2]) if row[2] else []
        except (TypeError, json.JSONDecodeError):
            nodes = []
        linked.add(row[1])
        results.append({"outcome_id": row[0], "trace_id": row[1], "applied_node_ids": nodes,
                        "application": row[3], "result": row[4], "evidence_digest": row[5],
                        "evidence_kind": row[6], "trust_tier": row[7], "ts": row[9]})
    summary = {"overall": {"traces": trace_count, "outcomes": len(results),
                            "pending_traces": max(0, trace_count - len(linked))},
               "signal_only": True}
    return results[-limit:] if limit else results, summary


def _item_size(item: Any) -> int:
    return len(json.dumps(item, ensure_ascii=False, sort_keys=True))


def _source_item(raw: dict[str, Any], *, superseded: bool = False) -> dict[str, Any]:
    node_id = raw.get("node_id")
    return {
        "node_id": node_id,
        "claim": str(raw.get("claim") or raw.get("sentence") or "")[:240],
        "semantic_subtype": raw.get("semantic_subtype") or raw.get("subtype"),
        "relevance": raw.get("relevance"),
        "superseded": bool(raw.get("superseded", superseded)),
        "superseded_by": raw.get("superseded_by"),
        "source_ref": raw.get("source_ref") or (
            "memory:%s" % (raw.get("display_id") or str(node_id)[-8:]) if node_id else "memory:unknown"
        ),
    }


def build_catchup(
    *,
    repo_state: dict[str, Any],
    recall_result: dict[str, Any],
    outcomes: list[dict[str, Any]],
    outcome_summary: dict[str, Any],
    superseded: list[dict[str, Any]],
    query: str,
    max_chars: int = 8000,
    next_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge already-read sources under a deterministic context budget."""
    nodes = recall_result.get("relevant_nodes") or recall_result.get("remember") or []
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in nodes:
        item = _source_item(dict(raw))
        key = str(item.get("node_id") or item.get("claim")).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    history = []
    for raw in superseded:
        item = _source_item(dict(raw), superseded=True)
        key = "history:" + str(item.get("node_id") or item.get("claim")).casefold()
        if key not in seen:
            seen.add(key)
            history.append(item)

    decisions = [item for item in unique if item.get("semantic_subtype") in _DECISION_SUBTYPES]
    constraints = [item for item in unique if item.get("semantic_subtype") in _CONSTRAINT_SUBTYPES]
    failures = [item for item in unique if item.get("semantic_subtype") in _FAILURE_SUBTYPES]
    classified = {id(item) for item in decisions + constraints + failures}
    general = [item for item in unique if id(item) not in classified]

    unresolved: list[str] = []
    if not repo_state.get("available"):
        unresolved.append("REPO_STATE_UNAVAILABLE: %s" % repo_state.get("error", "unknown"))
    pending = int((outcome_summary.get("overall") or {}).get("pending_traces") or 0)
    if pending:
        unresolved.append("OUTCOME_PENDING: %d recall trace(s) have no linked outcome" % pending)
    if repo_state.get("clean") is False:
        for item in unique:
            claim = item.get("claim", "").casefold()
            if "repository state is clean" in claim or "repo is clean" in claim or "저장소가 깨끗" in claim:
                unresolved.append("LIVE_MEMORY_CONFLICT: live repository is dirty; memory says clean")
                break
    for outcome in outcomes:
        if outcome.get("result") == "failure":
            failures.append({"claim": "linked outcome failure", "semantic_subtype": "failure",
                             "source_ref": "outcome:%s" % outcome.get("outcome_id", outcome.get("trace_id")),
                             "evidence_digest": outcome.get("evidence_digest"), "superseded": False})

    if next_action is None:
        if repo_state.get("clean") is False:
            nba = {"next_best_action": "Verify the current repository changes before continuing",
                   "confidence": "high", "evidence": ["repo:dirty"]}
        elif pending:
            nba = {"next_best_action": "Link the oldest unresolved recall trace to verified outcome evidence",
                   "confidence": "high", "evidence": ["outcome:pending"]}
        elif failures:
            nba = {"next_best_action": "Run the cheapest regression for the most relevant known failure",
                   "confidence": "medium", "evidence": [failures[0].get("source_ref")]}
        else:
            nba = {"next_best_action": "Execute the smallest verifiable step toward the current goal",
                   "confidence": "low", "evidence": []}
    else:
        nba = dict(next_action)

    current_state = dict(repo_state)
    what_changed = list(repo_state.get("changed") or [])
    base_used = _item_size(current_state) + _item_size(what_changed) + _item_size(unresolved) + _item_size(nba)
    used = base_used
    omitted = 0
    kept: dict[str, list[Any]] = {
        "decisions": [], "constraints": [], "history": [], "failures": [], "general": []
    }
    prioritized = [
        ("decisions", item) for item in decisions
    ] + [
        ("constraints", item) for item in constraints
    ] + [
        ("history", item) for item in history
    ] + [
        ("failures", item) for item in failures
    ] + [
        ("general", item) for item in general
    ]
    for section, item in prioritized:
        size = _item_size(item)
        if used + size <= max_chars:
            kept[section].append(item)
            used += size
        else:
            omitted += 1
    return {
        "query": query,
        "current_state": current_state,
        "what_changed": what_changed,
        "relevant_memory": kept["history"] + kept["general"],
        "decisions": kept["decisions"],
        "known_constraints": kept["constraints"],
        "known_failures": kept["failures"],
        "unresolved": unresolved,
        "next_best_action": nba,
        "budget": {"max_chars": max_chars, "used_chars": min(used, max_chars),
                   "omitted_count": omitted},
        "safety": {"writes": 0, "ledger_mutation": False, "candidate_write": False,
                   "approval_write": False, "outcome_write": False, "git_write": False},
    }


def collect_catchup(
    repo_path: str | os.PathLike[str],
    *,
    query: str,
    ledger_path: str | os.PathLike[str] | None,
    now: str | None = None,
    max_chars: int = 8000,
    test_state: str | None = None,
    outcome_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect all catchup inputs through strict read-only paths."""
    del now  # deterministic caller clock reserved for future display; no implicit current time
    repo_state = snapshot_repository(repo_path, test_state=test_state)
    recall_result: dict[str, Any] = {"relevant_nodes": []}
    if ledger_path and os.path.exists(ledger_path):
        from binggupack.studio.read_model import (
            collect_memory_detail_snapshot,
            collect_memory_list_snapshot,
            collect_recall_snapshot,
        )

        snapshot = collect_recall_snapshot(str(ledger_path), query, limit=20)
        enriched = []
        for item in snapshot.get("items") or []:
            current = dict(item)
            detail = collect_memory_detail_snapshot(str(ledger_path), item["node_id"])
            if detail:
                current["evidence_refs"] = ["evidence:%s" % ev.get("display_id")
                                            for ev in detail.get("evidence") or []]
                current["evidence_count"] = detail.get("evidence_count", 0)
            enriched.append(current)
        state_snapshot = collect_memory_list_snapshot(
            str(ledger_path), state="active", subtype="상태", limit=20
        )
        for item in state_snapshot.get("items") or []:
            claim = str(item.get("claim") or "").casefold()
            if any(word in claim for word in ("repository", "repo ", "저장소", "branch", "브랜치")):
                enriched.append(dict(item))
        recall_result = {"relevant_nodes": enriched, "confidence": snapshot.get("confidence", 0.0)}
    superseded = _read_superseded_ro(ledger_path, query)
    home = os.path.dirname(os.path.abspath(str(ledger_path))) if ledger_path else None
    outcomes, read_summary = _read_outcomes_ro(home)
    return build_catchup(
        repo_state=repo_state,
        recall_result=recall_result,
        outcomes=outcomes,
        outcome_summary=outcome_summary or read_summary,
        superseded=superseded,
        query=query,
        max_chars=max_chars,
    )


def _render_items(items: list[Any]) -> list[str]:
    if not items:
        return ["- none"]
    lines = []
    for item in items:
        if isinstance(item, dict):
            text = item.get("claim") or item.get("next_best_action") or json.dumps(item, ensure_ascii=False)
            source = item.get("source_ref")
            suffix = " [%s]" % source if source else ""
            if item.get("superseded"):
                suffix += " [superseded]"
            lines.append("- %s%s" % (text, suffix))
        else:
            lines.append("- %s" % item)
    return lines


def render_catchup(result: dict[str, Any]) -> str:
    """Render the stable human-facing catchup sections."""
    state = result.get("current_state") or {}
    state_lines = [
        "- repo: %s" % state.get("repo"),
        "- branch: %s" % state.get("branch"),
        "- head: %s" % state.get("head"),
        "- clean: %s" % state.get("clean"),
        "- last commit: %s" % state.get("last_commit"),
        "- tests: %s" % state.get("test_state"),
    ]
    sections = [
        ("CURRENT STATE", state_lines),
        ("WHAT CHANGED", _render_items(result.get("what_changed") or [])),
        ("RELEVANT MEMORY", _render_items(result.get("relevant_memory") or [])),
        ("DECISIONS", _render_items(result.get("decisions") or [])),
        ("KNOWN CONSTRAINTS", _render_items(result.get("known_constraints") or [])),
        ("KNOWN FAILURES", _render_items(result.get("known_failures") or [])),
        ("UNRESOLVED", _render_items(result.get("unresolved") or [])),
        ("NEXT BEST ACTION", _render_items([result.get("next_best_action") or {}])),
    ]
    lines = ["# BingguPack catchup (read-only)"]
    for heading, content in sections:
        lines.extend(["", "## " + heading, *content])
    budget = result.get("budget") or {}
    lines.extend(["", "context: %s/%s chars; omitted=%s" % (
        budget.get("used_chars", 0), budget.get("max_chars", 0), budget.get("omitted_count", 0)
    )])
    return "\n".join(lines) + "\n"
