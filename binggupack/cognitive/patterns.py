"""Deterministic Paperthin-derived work habits with no memory authority."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable

_CONSTRAINT_WORDS = (
    "must", "never", "do not", "without", "preserve", "required",
    "금지", "절대", "반드시", "유지", "보존", "조건", "우회", "없어야",
)
_DELIVERABLE_WORDS = (
    "deliver", "implement", "test", "document", "report", "provide", "create",
    "구현", "테스트", "검증", "문서", "보고", "산출물", "작성", "제공",
)
_SECTION_CONSTRAINTS = {"constraint", "constraints", "rules", "금지", "조건", "규칙", "절대 조건"}
_SECTION_DELIVERABLES = {"deliverable", "deliverables", "outputs", "산출물", "결과", "완료 기준"}
_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "this", "that", "must", "never",
    "하고", "한다", "위해", "대한", "있는", "없는", "모든", "현재", "기존",
}
_CANDIDATE_KINDS = {"decision", "lesson", "state", "procedure", "constraint", "preference"}
_EXTERNAL_TYPES = {
    "external_api", "library", "version", "external_spec", "law", "regulation",
    "standard", "paper", "external_repository", "product", "model", "public_service",
}
_HIGH_RISK_CHANGES = {
    "architecture", "memory_semantics", "approval", "recall_behavior",
    "outcome_attribution", "public_interface", "release_candidate",
}


def _clean_line(line: str) -> str:
    return re.sub(r"^\s*(?:[-*+] |\d+[.)]\s*)", "", line).strip()


def _section_name(line: str) -> str | None:
    raw = line.strip().lstrip("#").strip().rstrip(":").lower()
    if not raw or len(raw) > 40:
        return None
    if line.lstrip().startswith("#") or line.rstrip().endswith(":"):
        return raw
    return raw if raw in _SECTION_CONSTRAINTS | _SECTION_DELIVERABLES | {"goal", "목표"} else None


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = " ".join(str(item).split()).casefold()
        if key and key not in seen:
            seen.add(key)
            out.append(" ".join(str(item).split()))
    return out


def reconstruct_intent(
    request: str,
    *,
    available_facts: dict[str, Any] | None = None,
    ambiguity_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reconstruct intent and a bounded recall query without inventing questions."""
    del available_facts  # reserved for callers that pre-resolve ambiguity upstream
    lines = [line for line in str(request or "").splitlines() if line.strip()]
    section: str | None = None
    intent_lines: list[str] = []
    constraints: list[str] = []
    deliverables: list[str] = []
    for line in lines:
        maybe_section = _section_name(line)
        if maybe_section is not None:
            section = maybe_section
            continue
        clean = _clean_line(line)
        low = clean.casefold()
        if not clean:
            continue
        if section in {"goal", "목표"} and not intent_lines:
            intent_lines.append(clean)
        elif section in _SECTION_CONSTRAINTS or any(word in low for word in _CONSTRAINT_WORDS):
            constraints.append(clean)
        elif section in _SECTION_DELIVERABLES or any(word in low for word in _DELIVERABLE_WORDS):
            deliverables.append(clean)
        elif not intent_lines:
            intent_lines.append(clean)

    if not intent_lines:
        intent_lines = [_clean_line(lines[0])] if lines else [""]
    intent = " ".join(intent_lines).strip()
    constraints = _unique(constraints)
    deliverables = _unique(deliverables)

    resolved: list[dict[str, str]] = []
    unresolved: list[dict[str, Any]] = []
    positive = [line for line in constraints if not any(
        word in line.casefold() for word in ("never", "must not", "do not", "금지", "하지 마")
    )]
    negative = [line for line in constraints if line not in positive]
    for yes in positive:
        yes_tokens = {t.casefold() for t in re.findall(r"[A-Za-z0-9_+-]+|[가-힣]{2,}", yes)}
        for no in negative:
            no_tokens = {t.casefold() for t in re.findall(r"[A-Za-z0-9_+-]+|[가-힣]{2,}", no)}
            overlap = (yes_tokens & no_tokens) - _STOPWORDS - {"must", "preserve", "never"}
            if overlap:
                unresolved.append({"text": "conflicting constraints: %s <> %s" % (yes, no),
                                   "material": True, "source": "request"})
                break
    for ambiguity in ambiguity_candidates or []:
        text = str(ambiguity.get("text") or "").strip()
        if not text:
            continue
        resolution = ambiguity.get("resolved_by") or ambiguity.get("safe_default")
        if resolution:
            resolved.append({"text": text, "resolution": str(resolution)})
        elif ambiguity.get("material"):
            unresolved.append(dict(ambiguity))

    query_source = " ".join([intent, *constraints[:3], *deliverables[:3]])
    tokens = re.findall(r"[A-Za-z0-9_:.+-]+|[가-힣]{2,}", query_source)
    query_tokens: list[str] = []
    seen_tokens: set[str] = set()
    for token in tokens:
        key = token.casefold()
        if key in _STOPWORDS or key in seen_tokens:
            continue
        seen_tokens.add(key)
        query_tokens.append(token)
        if len(query_tokens) >= 24:
            break
    recall_query = " ".join(query_tokens)[:320]
    question = str(unresolved[0]["text"]) if unresolved else None
    return {
        "intent": intent,
        "constraints": constraints,
        "deliverables": deliverables,
        "ambiguities": unresolved,
        "resolved_ambiguities": resolved,
        "needs_user_question": bool(unresolved),
        "question": question,
        "recall_query": recall_query,
        "source": "user_request",
    }


def select_load_bearing_objection(
    objections: list[dict[str, Any]], *, test_result: str | None = None,
    change_kinds: list[str] | None = None,
) -> dict[str, Any]:
    """Return one high-impact objection and its cheapest falsification test."""
    if change_kinds is not None and not _HIGH_RISK_CHANGES.intersection(change_kinds):
        return {"status": "SKIP", "objection": None, "falsification_test": None,
                "considered": [], "reason": "no high-risk change kind"}
    if not objections:
        return {"status": "NO_BLOCKER", "objection": None, "falsification_test": None,
                "considered": []}
    ranked = []
    for index, raw in enumerate(objections):
        item = dict(raw)
        impact = max(0.0, min(1.0, float(item.get("impact", 0.0))))
        likelihood = max(0.0, min(1.0, float(item.get("likelihood", 0.0))))
        tests = list(item.get("falsification_tests") or [])
        if item.get("falsification_test"):
            tests.append({"test": item["falsification_test"], "cost": item.get("test_cost", 0.5)})
        tests = [t for t in tests if str(t.get("test") or "").strip()]
        tests.sort(key=lambda t: (float(t.get("cost", 1.0)), str(t.get("test"))))
        ranked.append((-(impact * likelihood), -impact, index, item, tests))
    ranked.sort(key=lambda row: row[:3])
    _score, _impact, _index, chosen, tests = ranked[0]
    observed = (test_result or "").strip().lower()
    status = {"pass": "FALSIFIED", "fail": "BLOCKER_CONFIRMED"}.get(observed, "TEST_REQUIRED")
    return {
        "status": status,
        "objection": str(chosen.get("text") or "").strip() or None,
        "falsification_test": str(tests[0]["test"]) if tests else None,
        "test_cost": float(tests[0].get("cost", 1.0)) if tests else None,
        "considered": [str(item.get("text") or "") for item in objections],
    }


def _candidate_key(text: str) -> str:
    normalized = " ".join(str(text or "").split()).casefold().rstrip(".。")
    return hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()


def propose_sip_candidates(
    items: list[dict[str, Any]], *, existing_candidates: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Produce ephemeral typed proposals; never call preview, SAVE, approval, or commit."""
    existing = {_candidate_key(item.get("text", "")) for item in existing_candidates or []}
    seen = set(existing)
    candidates: list[dict[str, Any]] = []
    duplicates = 0
    rejected = 0
    excluded: list[dict[str, Any]] = []
    for raw in items or []:
        kind = str(raw.get("kind") or "").strip().lower()
        text = " ".join(str(raw.get("text") or "").split())
        if kind not in _CANDIDATE_KINDS or not text:
            rejected += 1
            continue
        # Existing pure preview is the canonical PII/secret/shape gate.  Do not use
        # MCP capture_preview: that surface stages a preview file.
        from binggupack.capture.preview import capture_preview

        preview = capture_preview(text, explicit=True)
        preview_candidates = list(preview.get("candidates") or [])
        if not preview_candidates:
            rejected += 1
            excluded.append({"text_sha": _candidate_key(text)[:16],
                             "excluded_counts": dict(preview.get("excluded_counts") or {})})
            continue
        key = _candidate_key(text)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        claims = [dict(c) for c in raw.get("external_claims") or []]
        candidates.append({
            "proposal_id": "sip-" + key[:16],
            "kind": kind,
            "text": text,
            "source_refs": _unique(raw.get("source_refs") or []),
            "external_claims": claims,
            "needs_factchk": any(str(c.get("claim_type") or "") in _EXTERNAL_TYPES for c in claims),
            "canonical_preview": preview_candidates,
            "status": "PROPOSED",
            "promotion_allowed": False,
            "save_ready": not claims,
        })
    return {
        "candidates": candidates,
        "duplicates": duplicates,
        "rejected": rejected,
        "excluded": excluded,
        "ephemeral": True,
        "writes": 0,
        "commit_allowed": False,
        "requires_human_approval": True,
        "next_gate": "existing_binggupack_preview_dedupe_conflict_evidence_then_human_SAVE",
    }


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def fact_check_candidate(
    candidate: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    now: datetime | str | None = None,
    max_age_days: int = 30,
) -> dict[str, Any]:
    """Evaluate supplied evidence for external claims; performs no network access."""
    claims = [dict(c) for c in candidate.get("external_claims") or []
              if str(c.get("claim_type") or "") in _EXTERNAL_TYPES]
    if not claims:
        return {"status": "NOT_APPLICABLE", "claims": [], "provenance_preserved": True}
    now_dt = _parse_time(now) or datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []
    for claim in claims:
        cid = str(claim.get("claim_id") or "")
        refs = [dict(ref) for ref in evidence or [] if str(ref.get("claim_id") or "") == cid]
        fresh: list[dict[str, Any]] = []
        stale: list[dict[str, Any]] = []
        for ref in refs:
            checked = _parse_time(ref.get("checked_at"))
            if checked is None or (now_dt - checked).total_seconds() > max_age_days * 86400:
                stale.append(ref)
            else:
                fresh.append(ref)
        stances = {str(ref.get("stance") or "").lower() for ref in fresh}
        if "supports" in stances and "refutes" not in stances:
            status = "VERIFIED"
        elif "refutes" in stances and "supports" not in stances:
            status = "CONTRADICTED"
        elif fresh:
            status = "UNVERIFIED"
        elif stale:
            status = "STALE"
        else:
            status = "UNVERIFIED"
        results.append({**claim, "status": status, "evidence": fresh + stale})
    statuses = {item["status"] for item in results}
    if "CONTRADICTED" in statuses:
        overall = "CONTRADICTED"
    elif "UNVERIFIED" in statuses:
        overall = "UNVERIFIED"
    elif "STALE" in statuses:
        overall = "STALE"
    else:
        overall = "VERIFIED"
    return {"status": overall, "claims": results, "provenance_preserved": True,
            "network_calls": 0, "writes": 0}


def _base_action_score(action: dict[str, Any], blocker: bool) -> float:
    value = float(action.get("value", 0.0))
    urgency = float(action.get("urgency", 0.0))
    effort = float(action.get("effort", 0.0))
    risk = float(action.get("risk", 0.0))
    unblock = 0.5 if blocker and action.get("resolves_blocker") else 0.0
    return (2.0 * value) + urgency + unblock - effort - risk


def _rank_actions(actions: list[dict[str, Any]], context: dict[str, Any], *, use_memory: bool) -> list[dict[str, Any]]:
    blocker = bool(context.get("blocker"))
    recalls = list(context.get("recall") or []) if use_memory else []
    outcomes = list(context.get("outcomes") or []) if use_memory else []
    ranked: list[dict[str, Any]] = []
    for action in actions:
        aid = str(action.get("id") or "")
        score = _base_action_score(action, blocker)
        influence: list[dict[str, Any]] = []
        for memory in recalls:
            if str(memory.get("applies_to") or "") != aid:
                continue
            weight = max(0.0, min(1.0, float(memory.get("weight", 0.5))))
            effect = str(memory.get("effect") or "").lower()
            delta = weight if effect in {"recommend", "prefer", "support"} else -2.0 * weight
            score += delta
            influence.append({"type": "recall", "node_id": memory.get("node_id"), "delta": delta})
        memory_ids = set(action.get("memory_ids") or [])
        for outcome in outcomes:
            overlap = memory_ids.intersection(outcome.get("applied_node_ids") or [])
            if not overlap or outcome.get("application") != "applied":
                continue
            result = outcome.get("result")
            delta = {"success": 0.5, "failure": -0.75, "mixed": -0.15}.get(result, 0.0)
            score += delta
            influence.append({"type": "outcome", "node_ids": sorted(overlap), "result": result,
                              "delta": delta, "evidence_digest": outcome.get("evidence_digest")})
        ranked.append({"action": action, "score": round(score, 6), "influence": influence})
    ranked.sort(key=lambda row: (-row["score"], str(row["action"].get("id") or "")))
    return ranked


def select_next_best_action(actions: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    """Select one action and expose whether recall/outcome changed the decision."""
    if not actions:
        return {"action_id": None, "next_best_action": None, "why": "no actionable input",
                "evidence": [], "confidence": "low", "blocker": context.get("blocker"),
                "counterfactual_without_recall": None, "recall_changed_decision": False}
    ranked = _rank_actions(actions, context, use_memory=True)
    baseline = _rank_actions(actions, context, use_memory=False)
    chosen = ranked[0]
    margin = chosen["score"] - (ranked[1]["score"] if len(ranked) > 1 else chosen["score"] - 1.0)
    confidence = "high" if margin >= 0.75 else "medium" if margin >= 0.25 else "low"
    evidence = chosen["influence"]
    action = chosen["action"]
    return {
        "action_id": action.get("id"),
        "next_best_action": action.get("action"),
        "why": "highest bounded value after blocker, risk, recall, and outcome signals",
        "evidence": evidence,
        "confidence": confidence,
        "blocker": context.get("blocker"),
        "counterfactual_without_recall": baseline[0]["action"].get("id"),
        "recall_changed_decision": baseline[0]["action"].get("id") != action.get("id"),
        "ranked": [{"action_id": row["action"].get("id"), "score": row["score"]} for row in ranked],
    }
