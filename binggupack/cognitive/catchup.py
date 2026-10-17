"""Read-only repository + canonical recall + outcome re-entry briefing."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

_DECISION_SUBTYPES = {"결정", "판단", "decision"}
_CONSTRAINT_SUBTYPES = {"제약", "원칙", "constraint", "policy"}
_FAILURE_SUBTYPES = {"버그패턴", "교훈", "failure", "lesson"}


def _sidecar_safe(path: str | os.PathLike[str] | None) -> bool:
    """Fail closed when SQLite would have to create a missing WAL shared-memory file."""
    if not path:
        return False
    value = os.path.abspath(str(path))
    return not (os.path.exists(value + "-wal") and not os.path.exists(value + "-shm"))


def _git(repo: Path, *args: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
             "-c", "core.untrackedCache=false", *args], cwd=str(repo), env=env,
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


def _item_size(item: Any) -> int:
    return len(json.dumps(item, ensure_ascii=False, sort_keys=True))


# 예산 압박 때 갈래를 통째로 버리지 않고 줄인다 (B-16 · 2026-10-17 사장님 결정:
# «과거실수기록부터 버리는게 아니라 기억을 모두 정리해서 보고해야지 정상 아니냐?»).
# 앞일수록 끝까지 지킨다 — 최소 몫조차 모자랄 때 뒤에서부터 «건수만» 으로 줄인다.
_KEEP_PRIORITY = ("known_failures", "decisions", "known_constraints", "relevant_memory", "what_changed")
_SECTION_LABELS = {
    "known_failures": "과거실수",
    "decisions": "결정",
    "known_constraints": "제약",
    "relevant_memory": "관련기억",
    "what_changed": "변경파일",
}
_CLAIM_FLOOR = 80  # 여러 건을 담을 때 한 건에 남기는 최소 앞부분 글자 수
_CLAIM_LAST = 20   # 한 건만 겨우 담을 때의 최소 앞부분 글자 수


def _shorten(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    out = "%s…(%d자 줄임)" % (text[:limit], len(text) - limit)
    if len(out) >= len(text):
        return text, False
    return out, True


def _compact(entry: Any, limit: int) -> tuple[Any, bool]:
    if not isinstance(entry, dict):
        return _shorten(str(entry), limit)
    claim, cut = _shorten(str(entry.get("claim") or ""), limit)
    out: dict[str, Any] = {"claim": claim, "source_ref": entry.get("source_ref") or "memory:unknown"}
    if entry.get("superseded"):
        out["superseded"] = True
    return out, cut


def _stub(count: int, *, shown: bool, as_dict: bool) -> Any:
    text = ("…외 %d건" % count) if shown else ("…%d건(본문 생략)" % count)
    return {"claim": text, "omitted_count": count} if as_dict else text


def _build(items: list[Any], keep: int, limit: int, as_dict: bool) -> tuple[list[Any], int]:
    out: list[Any] = []
    cut = 0
    for entry in items[:keep]:
        small, was_cut = _compact(entry, limit)
        out.append(small)
        cut += int(was_cut)
    if keep < len(items):
        out.append(_stub(len(items) - keep, shown=True, as_dict=as_dict))
    return out, cut


def _max_keep(items: list[Any], limit: int, cap: int, as_dict: bool, most: int) -> int:
    """Largest top-K whose compact list (plus «외 M건») fits ``cap`` JSON chars."""
    best = 0
    used = 2
    for k in range(1, min(len(items), most) + 1):
        used += _item_size(_compact(items[k - 1], limit)[0]) + (2 if k > 1 else 0)
        if used > cap:
            break
        total = used
        if k < len(items):
            total += 2 + _item_size(_stub(len(items) - k, shown=True, as_dict=as_dict))
        if total <= cap:
            best = k
    return best


def _max_limit(items: list[Any], keep: int, floor: int, cap: int, as_dict: bool) -> int:
    def text_len(entry: Any) -> int:
        return len(str(entry.get("claim") or "")) if isinstance(entry, dict) else len(str(entry))

    lo, hi, best = floor, max(text_len(entry) for entry in items[:keep]), floor
    while lo <= hi:
        mid = (lo + hi) // 2
        if _item_size(_build(items, keep, mid, as_dict)[0]) <= cap:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def _fit_section(items: list[Any], cap: int, *, as_dict: bool) -> tuple[list[Any], dict[str, Any]]:
    """Shrink one section into ``cap`` JSON chars without dropping it wholesale."""
    n = len(items)
    info: dict[str, Any] = {"original": n, "kept": n, "folded": 0, "truncated": 0, "compacted": False}
    if not n or _item_size(items) <= cap:
        return list(items), info
    for floor, most in ((_CLAIM_FLOOR, n), (_CLAIM_LAST, 1)):
        keep = _max_keep(items, floor, cap, as_dict, most)
        if keep:
            out, cut = _build(items, keep, _max_limit(items, keep, floor, cap, as_dict), as_dict)
            info.update(kept=keep, folded=n - keep, truncated=cut, compacted=True)
            return out, info
    info.update(kept=0, folded=n, compacted=True)
    tiny = [_stub(n, shown=False, as_dict=as_dict)]
    if _item_size(tiny) <= cap:
        return tiny, info
    info["dropped"] = True
    return [], info


def _min_share(items: list[Any], as_dict: bool, limit: int = _CLAIM_FLOOR) -> int:
    if not items:
        return 0
    return min(_item_size(items), _item_size(_build(items, 1, limit, as_dict)[0])) - 2


def _tiny_share(items: list[Any], as_dict: bool) -> int:
    return _item_size([_stub(len(items), shown=False, as_dict=as_dict)]) - 2 if items else 0


def _fit_sections(
    sections: dict[str, list[Any]], avail: int, max_chars: int
) -> tuple[dict[str, list[Any]], dict[str, dict[str, Any]]]:
    """Minimum share per section, then the rest split by need, then item-level shrink."""
    as_dict = {name: name != "what_changed" for name in sections}
    floor = {name: _min_share(items, as_dict[name]) for name, items in sections.items()}
    top = {name: (_item_size(items) - 2 if items else 0) for name, items in sections.items()}
    if sections["what_changed"]:  # 변경 파일 목록은 종전처럼 예산의 1/4 까지만
        top["what_changed"] = max(floor["what_changed"], min(top["what_changed"], max_chars // 4))
    if sum(floor.values()) <= avail:
        rest = avail - sum(floor.values())
        need = {name: top[name] - floor[name] for name in sections}
        total_need = sum(need.values())
        if total_need <= rest:
            share = dict(top)
        else:
            share = {name: floor[name] + rest * need[name] // total_need for name in sections}
    else:
        # 모자라면 뒤 갈래부터: 먼저 모두 «한 건 앞부분+외 M건» 으로, 그래도 모자라면 «건수만» 으로.
        share = dict(floor)
        deficit = sum(floor.values()) - avail
        for shrink in (lambda items, flag: _min_share(items, flag, _CLAIM_LAST), _tiny_share):
            for name in reversed(_KEEP_PRIORITY):
                if deficit <= 0:
                    break
                if sections[name]:
                    smaller = min(share[name], shrink(sections[name], as_dict[name]))
                    deficit -= share[name] - smaller
                    share[name] = smaller
    fitted: dict[str, list[Any]] = {}
    info: dict[str, dict[str, Any]] = {}
    for name in _KEEP_PRIORITY:
        fitted[name], info[name] = _fit_section(sections[name], share[name] + 2, as_dict=as_dict[name])
    slack = avail - sum(_item_size(value) - 2 for value in fitted.values())
    for name in _KEEP_PRIORITY:  # 반올림·여유분은 앞 갈래부터 다시 나눠 준다
        if slack <= 0:
            break
        if not info[name]["compacted"]:
            continue
        before = _item_size(fitted[name]) - 2
        room = before + slack
        if name == "what_changed":
            room = min(room, top[name])
        again, again_info = _fit_section(sections[name], room + 2, as_dict=as_dict[name])
        after = _item_size(again) - 2
        if before < after <= room:
            fitted[name], info[name] = again, again_info
            slack -= after - before
    return fitted, info


def _compression_line(max_chars: int, info: dict[str, dict[str, Any]], frame_notes: list[str]) -> str:
    parts = []
    for name in _KEEP_PRIORITY:
        row = info[name]
        if not row["original"]:
            continue
        text = "%s %d→%d건" % (_SECTION_LABELS[name], row["original"], row["kept"])
        extra = []
        if row["folded"]:
            extra.append("외 %d건 묶음" % row["folded"])
        if row["truncated"]:
            extra.append("%d건 앞부분만" % row["truncated"])
        if row.get("dropped"):
            extra.append("상한 초과로 비움")
        elif row["compacted"] and not extra:
            extra.append("세부칸 뺌")
        if extra:
            text += "(" + "·".join(extra) + ")"
        parts.append(text)
    parts.extend(frame_notes)
    return "[기억 압축] 예산 %d자 중 갈래별 원래→남김: %s" % (max_chars, " · ".join(parts) or "없음")


def _source_item(raw: dict[str, Any], *, superseded: bool = False) -> dict[str, Any]:
    node_id = raw.get("node_id")
    return {
        "node_id": node_id,
        "claim": str(raw.get("claim") or raw.get("sentence") or "")[:240],
        "semantic_subtype": raw.get("semantic_subtype") or raw.get("subtype"),
        "relevance": raw.get("relevance"),
        "superseded": bool(raw.get("superseded", superseded)),
        "superseded_by": raw.get("superseded_by"),
        "evidence_refs": list(raw.get("evidence_refs") or []),
        "evidence_count": int(raw.get("evidence_count") or 0),
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
    """Merge already-read sources under a deterministic context budget.

    Within budget the output is the full merge (byte-identical to the 2026-08-29
    behaviour).  Under pressure no section is dropped wholesale: every non-empty
    section keeps a minimum share, the rest is split by need, and overflowing
    sections shrink item by item (head + «…(N자 줄임)», top K + «…외 M건»).
    What shrank is reported in ``budget.compression`` and as one line at the top
    of ``render_catchup``.
    """
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
    failures = failures[:5]
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
        if outcome.get("result") == "failure" and not outcome.get("overturned"):
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
                   "confidence": "medium",
                   "evidence": [str(failures[0].get("source_ref") or "memory:unknown")]}
        else:
            nba = {"next_best_action": "Execute the smallest verifiable step toward the current goal",
                   "confidence": "low", "evidence": []}
    else:
        nba = dict(next_action)

    current_state = {key: value for key, value in repo_state.items() if key != "changed"}
    raw_changed = list(repo_state.get("changed") or [])
    sections: dict[str, list[Any]] = {
        "what_changed": [str(item) for item in raw_changed],
        "relevant_memory": history + general,
        "decisions": decisions,
        "known_constraints": constraints,
        "known_failures": failures,
    }
    result: dict[str, Any] = {
        "query": query,
        "current_state": current_state,
        "what_changed": list(sections["what_changed"]),
        "relevant_memory": list(sections["relevant_memory"]),
        "decisions": list(decisions),
        "known_constraints": list(constraints),
        "known_failures": list(failures),
        "unresolved": unresolved,
        "next_best_action": nba,
        "budget": {},
        "safety": {"writes": 0, "ledger_mutation": False, "candidate_write": False,
                   "approval_write": False, "outcome_write": False, "git_write": False},
    }
    context_keys = (
        "current_state", "what_changed", "relevant_memory", "decisions",
        "known_constraints", "known_failures", "unresolved", "next_best_action",
    )

    def context_size() -> int:
        return _item_size({key: result[key] for key in context_keys})

    changed_fits = sum(_item_size(item) for item in raw_changed) <= max(0, max_chars // 4)
    if changed_fits and context_size() <= max_chars:
        result["budget"] = {"max_chars": max_chars, "used_chars": context_size(), "omitted_count": 0,
                            "hard_cap_applied": False, "cap_satisfied": True}
        return result

    as_dict = {name: name != "what_changed" for name in sections}
    frame_notes: list[str] = []

    def avail() -> int:
        frame = {key: ([] if key in sections else result[key]) for key in context_keys}
        return max_chars - _item_size(frame)

    if sum(_min_share(items, as_dict[name]) for name, items in sections.items()) > avail():
        result["current_state"] = {
            "available": current_state.get("available"),
            "repo": str(current_state.get("repo") or "")[-100:],
            "branch": current_state.get("branch"),
            "head": str(current_state.get("head") or "")[:12],
            "clean": current_state.get("clean"),
            "test_state": str(current_state.get("test_state") or "")[:80],
        }
        frame_notes.append("상태칸 축약")
    if sum(_tiny_share(items, as_dict[name]) for name, items in sections.items()) > avail():
        result["unresolved"] = [str(item)[:100] for item in unresolved[:1]]
        result["next_best_action"] = {
            "next_best_action": str(nba.get("next_best_action") or "")[:100],
            "confidence": nba.get("confidence", "low"),
        }
        frame_notes.append("미해결 %d→%d건·다음행동 축약" % (len(unresolved), len(result["unresolved"])))
    fitted, info = _fit_sections(sections, avail(), max_chars)
    result.update(fitted)
    for name in reversed(_KEEP_PRIORITY):  # 건수 표시조차 못 담는 극단 상한에서만 — 그래도 상한은 지킨다
        if context_size() <= max_chars:
            break
        if result[name]:
            result[name] = []
            info[name].update(kept=0, folded=info[name]["original"], truncated=0, dropped=True)
    actual = context_size()
    line = _compression_line(max_chars, info, frame_notes)
    result["budget"] = {
        "max_chars": max_chars,
        "used_chars": actual,
        "omitted_count": sum(row["folded"] for row in info.values()),
        "hard_cap_applied": True,
        "cap_satisfied": actual <= max_chars,
        "compression": {
            "sections": {name: info[name] for name in _KEEP_PRIORITY},
            "frame": frame_notes,
            "line": line,
        },
    }
    return result


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
    superseded: list[dict[str, Any]] = []
    ledger_readable = bool(
        ledger_path and os.path.exists(ledger_path) and _sidecar_safe(ledger_path)
    )
    if ledger_readable:
        from binggupack.studio.read_model import (
            collect_memory_detail_snapshot,
            collect_memory_list_snapshot,
            collect_recall_snapshot,
            collect_superseded_decision_snapshot,
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
        superseded = collect_superseded_decision_snapshot(str(ledger_path), query, limit=5)
    home = os.path.dirname(os.path.abspath(str(ledger_path))) if ledger_path else None
    trace_path = os.path.join(home, "recall_trace.sqlite") if home else None
    if trace_path and os.path.exists(trace_path) and _sidecar_safe(trace_path):
        from binggupack.pack.outcome_attribution import aggregate_run_outcomes, list_run_outcomes_ro

        outcomes = list_run_outcomes_ro(home=home, limit=10)
        read_summary = aggregate_run_outcomes(home=home)
    else:
        outcomes = []
        read_summary = {
            "overall": {"traces": 0, "outcomes": 0, "pending_traces": 0},
            "signal_only": True,
        }
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
    budget = result.get("budget") or {}
    note = str((budget.get("compression") or {}).get("line") or "")
    lines = ["# BingguPack catchup (read-only)"] + ([note] if note else [])
    for heading, content in sections:
        lines.extend(["", "## " + heading, *content])
    lines.extend(["", "context: %s/%s chars; omitted=%s" % (
        budget.get("used_chars", 0), budget.get("max_chars", 0), budget.get("omitted_count", 0)
    )])
    rendered = "\n".join(lines) + "\n"
    max_chars = int(budget.get("max_chars") or 0)
    if max_chars and len(rendered) > max_chars:
        note = note[:max_chars // 4]
        head = ["# BingguPack catchup (read-only)"] + ([note] if note else [])
        fixed = list(head)
        first_lines = []
        for heading, content in sections:
            fixed.extend(["", "## " + heading])
            first_lines.append(content[0] if content else "- none")
        fixed.extend(["", "context: capped; omitted=%s" % budget.get("omitted_count", 0)])
        fixed_size = len("\n".join(fixed)) + len(first_lines) + 1
        per_section = max(4, (max_chars - fixed_size) // max(1, len(first_lines)))
        compact = list(head)
        for (heading, _content), first in zip(sections, first_lines):
            compact.extend(["", "## " + heading, first[:per_section]])
        compact.extend(["", "context: capped; omitted=%s" % budget.get("omitted_count", 0)])
        rendered = "\n".join(compact) + "\n"
    return rendered
