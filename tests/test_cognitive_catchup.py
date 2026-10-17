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
    # 예산은 current_state 안의 **절대 경로 길이까지** 함께 센다. 종전에는 한 번의 호출에
    # max_chars=1800 을 걸고 「무엇이 담기는가」까지 봤는데 남는 여유가 2자였다 —
    # 실측(2026-09-03): repo 경로 101자면 known_failures 유지, 103자면 탈락.
    # pytest 직렬 tmp_path 는 90자, `-n auto` 는 `popen-gwN\` 가 붙어 100자라
    # **xdist 에서만** 빨갛게 났다(ci_local_preflight 15/16 FAIL 의 정체).
    # 그래서 「무엇이 담기는가」와 「상한을 지키는가」를 갈라 본다 — 둘 다 경로 길이와 무관하다.
    out = collect_catchup(repo, query="release approval", ledger_path=ledger,
                          now="2026-08-29T00:00:00Z",
                          outcome_summary={"overall": {"pending_traces": 2}})
    assert out["decisions"]
    assert out["known_constraints"]
    assert out["known_failures"]
    assert any(x.get("superseded") for x in out["relevant_memory"])
    assert any("LIVE_MEMORY_CONFLICT" in x for x in out["unresolved"])
    assert any("pending" in x.lower() for x in out["unresolved"])
    text = render_catchup(out)
    for section in ("CURRENT STATE", "WHAT CHANGED", "RELEVANT MEMORY", "DECISIONS",
                    "KNOWN CONSTRAINTS", "KNOWN FAILURES", "UNRESOLVED", "NEXT BEST ACTION"):
        assert section in text

    # 상한은 따로 건다. 교훈 20줄만 2,000자가 넘으므로 경로가 아무리 짧아도 반드시 잘린다.
    capped = collect_catchup(repo, query="release approval", ledger_path=ledger,
                             now="2026-08-29T00:00:00Z", max_chars=1800,
                             outcome_summary={"overall": {"pending_traces": 2}})
    assert capped["budget"]["used_chars"] <= 1800
    assert capped["budget"]["cap_satisfied"] is True
    assert capped["budget"]["omitted_count"] > 0
    # B-16: 압박을 받아도 과거 실수는 실제 항목으로 남고, 줄인 내역은 본문 한 줄로 보고된다.
    assert any("omitted_count" not in item for item in capped["known_failures"])
    assert "[기억 압축]" in render_catchup(capped)


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


_SECTIONS = ("what_changed", "relevant_memory", "decisions", "known_constraints", "known_failures")


def _pressure_input(max_chars: int, *, clean: bool = True) -> dict:
    nodes = (
        [{"node_id": f"n-d{i}", "claim": f"결정 {i}: " + "승인 회귀 뒤에만 릴리스한다 " * 4,
          "semantic_subtype": "결정", "evidence_refs": [f"evidence:d{i}"], "evidence_count": 1,
          "display_id": f"d{i}"} for i in range(6)]
        + [{"node_id": f"n-c{i}", "claim": f"제약 {i}: " + "G4_no_auto 를 지킨다 " * 5,
            "semantic_subtype": "제약", "display_id": f"c{i}"} for i in range(6)]
        + [{"node_id": f"n-f{i}", "claim": f"실수 {i}: " + "승인 회귀를 건너뛰어 저장 게이트가 열렸다 " * 8,
            "semantic_subtype": "버그패턴", "display_id": f"f{i}"} for i in range(5)]
        + [{"node_id": f"n-g{i}", "claim": f"참고 {i}: " + "릴리스 노트 " * 30,
            "semantic_subtype": "선호", "display_id": f"g{i}"} for i in range(20)]
    )
    return dict(
        repo_state={"available": True, "repo": "/repo", "branch": "main", "head": "a" * 40,
                    "clean": clean, "changed": [] if clean else [f" M src/module_{i}.py" for i in range(40)],
                    "last_commit": "abc base", "test_state": "green"},
        recall_result={"relevant_nodes": nodes},
        outcomes=[{"outcome_id": f"o{i}", "result": "failure", "evidence_digest": "b" * 64}
                  for i in range(3)],
        outcome_summary={"overall": {"pending_traces": 0}},
        superseded=[{"node_id": f"n-s{i}", "claim": f"옛 결정 {i}", "semantic_subtype": "결정"}
                    for i in range(3)],
        query="release", max_chars=max_chars,
    )


def test_catchup_pressure_shrinks_every_section_instead_of_dropping():
    full = build_catchup(**_pressure_input(10**9, clean=False))
    out = build_catchup(**_pressure_input(3000, clean=False))
    rows = out["budget"]["compression"]["sections"]
    for name in _SECTIONS:
        assert out[name], name
        assert rows[name]["original"] == len(full[name])
        assert rows[name]["kept"] + rows[name]["folded"] == rows[name]["original"]
    assert out["budget"]["omitted_count"] == sum(row["folded"] for row in rows.values()) > 0
    assert out["budget"]["cap_satisfied"] is True
    assert out["budget"]["used_chars"] <= 3000
    flat = json.dumps({name: out[name] for name in _SECTIONS}, ensure_ascii=False)
    assert "…외 " in flat and "자 줄임)" in flat
    line = out["budget"]["compression"]["line"]
    assert line.startswith("[기억 압축] 예산 3000자 중 갈래별 원래→남김:")
    assert "과거실수 %d→" % len(full["known_failures"]) in line
    text = render_catchup(out)
    assert text.splitlines()[1] == line
    assert len(text) <= 3000


def test_catchup_known_failures_never_drop_to_zero_under_pressure():
    full = build_catchup(**_pressure_input(10**9))
    top_ref = full["known_failures"][0]["source_ref"]
    for max_chars in range(1200, 8001, 400):
        out = build_catchup(**_pressure_input(max_chars))
        assert out["budget"]["cap_satisfied"] is True, max_chars
        real = [item for item in out["known_failures"] if "omitted_count" not in item]
        assert real and real[0]["source_ref"] == top_ref, max_chars
        # 다음 행동이 근거로 드는 과거 실수가 본문에서 사라지지 않는다.
        assert out["next_best_action"]["evidence"] == [top_ref]
        for name in ("relevant_memory", "decisions", "known_constraints"):
            assert out[name], (max_chars, name)


def _golden_input(max_chars: int) -> dict:
    nodes = [
        {"node_id": "n-d1", "claim": "릴리스는 승인 회귀 시험 뒤에만 한다", "semantic_subtype": "결정",
         "evidence_refs": ["evidence:e1"], "evidence_count": 1, "display_id": "d1"},
        {"node_id": "n-c1", "claim": "G4_no_auto 를 지킨다", "semantic_subtype": "제약", "display_id": "c1"},
        {"node_id": "n-f1", "claim": "승인 회귀를 건너뛰어 저장 게이트가 열렸다", "semantic_subtype": "버그패턴",
         "display_id": "f1"},
        {"node_id": "n-g1", "claim": "릴리스 노트는 한국어로 쓴다", "semantic_subtype": "선호", "display_id": "g1"},
    ]
    return dict(
        repo_state={"available": True, "repo": "/repo", "branch": "main", "head": "a" * 40, "clean": False,
                    "changed": [" M app.txt", "?? new.txt"], "last_commit": "abc base", "test_state": "green"},
        recall_result={"relevant_nodes": nodes},
        outcomes=[{"outcome_id": "o1", "result": "failure", "evidence_digest": "b" * 64}],
        outcome_summary={"overall": {"pending_traces": 1}},
        superseded=[{"node_id": "n-d0", "claim": "옛 흐름으로 릴리스", "semantic_subtype": "결정"}],
        query="release", max_chars=max_chars,
    )


def test_catchup_within_budget_output_is_byte_identical_to_before_b16():
    # 해시는 B-16 이전 코드(main 738af46)로 같은 입력을 돌려 얻은 값이다(JSON 키 순서 + 렌더 본문).
    # 예산 안(1808 = 딱 맞는 경계 포함)에서는 한 바이트도 달라지면 안 된다.
    for max_chars, digest in (
        (8000, "1542cd29aa671176bd2521e6cd72a04726d5dec74812fec432249310d281080d"),
        (1808, "508244eacc7d03ac8a60bce92322e2fdfecdcc5dd6247427d2c602265fb77053"),
    ):
        out = build_catchup(**_golden_input(max_chars))
        blob = json.dumps(out, ensure_ascii=False) + "\n" + render_catchup(out)
        assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == digest, max_chars
        assert "compression" not in out["budget"]
    # 한 글자만 모자라도 갈래를 통째로 버리지 않는다(종전엔 변경파일 2건을 다 버렸다) — 줄이고 보고한다.
    tight = build_catchup(**_golden_input(1807))
    assert tight["what_changed"] == [" M app.txt", "…외 1건"]
    assert tight["known_failures"] == build_catchup(**_golden_input(8000))["known_failures"]
    assert render_catchup(tight).splitlines()[1].startswith("[기억 압축] 예산 1807자")
