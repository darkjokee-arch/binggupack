# -*- coding: utf-8 -*-
"""OpenBinggu Watcher MVP1 — Step0 Capture + Step1 Evidence transform (정본 impl · dry-run only).

v1.16 strangler Phase2: 순수 transform(_sha8/redact_text/_has_secret/_parse_diff/_path_from_header/
capture/to_evidence/build_incoming_pack + CAPTURED_AT/SCOPE 상수)을 scripts/watcher_capture_mvp1.py
에서 이관. scripts/watcher_capture_mvp1.py 는 이 모듈을 re-export 하는 backward-compatible thin
wrapper(__file__ 경로상수 + _dump/process_one/run_selftest/run_single/CLI 오케스트레이션 잔류)다.

git diff 텍스트 1종 입력 → watcher_event[] → evidence_chunk[](2중 redaction) → v0.11 incoming pack.
secret 패턴은 binggupack.pack.incoming_to_staging(v0.11) 정본을 재사용한다(SECRET_PATTERNS).
실제 graph/store write 없음(transform 본문은 파일 I/O 무관).
"""
import hashlib
import re

# v0.11 loader 재사용 (secret 패턴 + dry-run 검증 게이트) — 정본 패키지 모듈.
from binggupack.pack import incoming_to_staging as v011

CAPTURED_AT = "(deterministic-mvp1)"  # 멱등 위해 시간 미사용
SCOPE = "project:openbinggu"


def _sha8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def redact_text(text):
    """v0.11 SECRET_PATTERNS 로 매치를 [REDACTED:len] 치환. (redacted_text, hit_count) 반환."""
    hits = 0
    out = text
    for pat in v011.SECRET_PATTERNS:
        def _sub(m):
            nonlocal hits
            hits += 1
            return "[REDACTED:%d]" % len(m.group(0))
        out = pat.sub(_sub, out)
    return out, hits


def _has_secret(text):
    return any(pat.search(text) for pat in v011.SECRET_PATTERNS)


# ---------- Step0 Capture ----------
def _parse_diff(diff_text):
    files, cur = [], None
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            if cur:
                files.append(cur)
            cur = {"header": line, "added": [], "removed": []}
        elif cur is not None:
            if line.startswith("+") and not line.startswith("+++"):
                cur["added"].append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                cur["removed"].append(line[1:])
    if cur:
        files.append(cur)
    return files


def _path_from_header(header):
    m = re.search(r"b/(\S+)$", header)
    return m.group(1) if m else "?"


def capture(diff_text, source_ref):
    """Step0: git diff 텍스트 → watcher_event[] (raw 미복사, 1차 redaction)."""
    events = []
    for f in _parse_diff(diff_text):
        path = _path_from_header(f["header"])
        n_add, n_rm = len(f["added"]), len(f["removed"])
        # 핵심 요약: 변경 라인 일부만(raw 전체 미복사). 1차 redaction.
        preview = " | ".join(f["added"][:3])
        raw_summary = "변경 %s (+%d/-%d): %s" % (path, n_add, n_rm, preview)
        summary, hits1 = redact_text(raw_summary)
        events.append({
            "event_id": "WEV-" + _sha8(source_ref + "::" + path + "::" + str(n_add) + str(n_rm)),
            "event_type": "file_change",
            "captured_at": CAPTURED_AT,
            "source": {"kind": "git", "ref": source_ref + " :: " + path},
            "summary": summary,
            "raw_pointer": path,            # 위치 포인터만, 원문 복사 X
            "scope": SCOPE,
            "redaction": {"applied": True, "hits": hits1},
            "confidence": 0.5,
        })
    return events


# ---------- Step1 Evidence ----------
def to_evidence(events):
    """Step1: watcher_event[] → evidence_chunk[] (2차 재검사, 잔존 시 STOP).
       반환: (chunks, stops). v0.11 content.items[] 호환(item_id,text required)."""
    chunks, stops = [], []
    for ev in events:
        text2, hits2 = redact_text(ev["summary"])
        if _has_secret(text2):  # 2차 재검사 잔존 → STOP
            stops.append({"event_id": ev["event_id"], "reason": "secret residual after redaction"})
            continue
        chunks.append({
            "item_id": "EVC-" + _sha8(ev["event_id"]),
            "text": text2,
            "source": ev["event_id"],
            "evidence_meta": {
                "confidence": ev["confidence"],
                "source_kind": ev["source"]["kind"],
                "timestamp": ev["captured_at"],
                "scope": ev["scope"],
                "raw_pointer": ev["raw_pointer"],
                "redaction_applied": True,
                "redaction_hits": ev["redaction"]["hits"] + hits2,
            },
        })
    return chunks, stops


def build_incoming_pack(chunks, incoming_id):
    """evidence_chunk[] → v0.11 incoming pack(loader dry-run 검증용). low-risk valid pack."""
    pack = {
        "pack_id": "watcher_mvp1_" + incoming_id,
        "pack_type": "evidence",
        "scope": SCOPE,
        "depends_on": [],
        "evidence_policy": {"source": "watcher", "min_evidence": 0},
        "merge_policy": {"mode": "review", "target": "staging", "cross_pack": "isolated"},
        "promotion_allowed_default": False,
        "status": "staged",
        "cross_pack_tags": [],
        "risk_level": "low",
        "created_from": "watcher_mvp1_git_diff",
    }
    return {"incoming_id": incoming_id, "pack": pack, "content": {"items": chunks}}
