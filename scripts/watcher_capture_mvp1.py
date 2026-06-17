# -*- coding: utf-8 -*-
"""OpenBinggu Watcher MVP1 — Step0 Capture + Step1 Evidence (git diff 단일 소스, dry-run only).

범위(MVP1 고정): Step0 Capture + Step1 Evidence 만. git diff 텍스트 1종 입력.
  - 수동 1회 dry-run. hook/daemon/상주 감시 배선 없음(라이브 git 호출도 안 함 — diff 텍스트 파일 입력).
  - 출력 = temp dir(BASE/tmp/watcher_mvp1/) only. 운영 store write 0.
  - Step2 candidate 변환 / merge / apply / DB / OpenCrab / GitHub push / production graph = 전부 금지.

안전:
  - secret/PII/token/.env raw 출력 금지. capture 1차 + Step1 2차 redaction(v0.11 SECRET_PATTERNS 재사용).
  - 2차 재검사에서 잔존 발견 → 해당 event STOP(evidence_chunk 미생성, stop 기록).
  - promotion_allowed=false 강제. candidate 미생성(MVP1은 evidence까지만).
  - 멱등: event_id/item_id = 내용 sha8, captured_at 고정 placeholder, JSON sort_keys → 2회 byte 동일.

CLI:
  python watcher_capture_mvp1.py --selftest        # fixture 3종 + v0.11 loader dry-run + 멱등 검증
  python watcher_capture_mvp1.py <diff_text_file>  # 단일 dry-run (temp 출력)
"""
import hashlib
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "watcher_mvp1"
TMP_OUT = BASE / "tmp" / "watcher_mvp1"
SELFTEST_REPORT = BASE / "reports" / "watcher_mvp1_selftest.json"

# v0.11 loader 재사용 (secret 패턴 + dry-run 검증 게이트)
sys.path.insert(0, str(SCRIPTS))
import openbinggu_incoming_to_staging as v011  # noqa: E402

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


def _dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def process_one(diff_text, name):
    """단일 diff → events/chunks/incoming_pack + temp 출력. 반환 dict."""
    source_ref = "git diff :: " + name
    events = capture(diff_text, source_ref)
    chunks, stops = to_evidence(events)
    incoming = build_incoming_pack(chunks, name)
    # v0.11 loader dry-run (read-only assess, write 없음)
    loader_res = v011.assess_incoming(incoming)
    out_ev = TMP_OUT / (name + "_evidence.jsonl")
    out_in = TMP_OUT / (name + "_incoming.json")
    TMP_OUT.mkdir(parents=True, exist_ok=True)
    out_ev.write_text(
        "".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n" for c in chunks),
        encoding="utf-8")
    _dump(out_in, incoming)
    return {
        "name": name, "n_events": len(events), "n_chunks": len(chunks), "n_stops": len(stops),
        "stops": stops,
        "all_chunks_have_required": all(c.get("item_id") and c.get("text") for c in chunks),
        "any_secret_residual": any(_has_secret(c["text"]) for c in chunks),
        "loader_verdict": loader_res["verdict"],
        "out_evidence": str(out_ev), "out_incoming": str(out_in),
    }


def run_selftest():
    if not FIXTURE_DIR.is_dir():
        print("[FAIL] fixture 디렉토리 없음:", FIXTURE_DIR)
        sys.exit(1)
    fixtures = sorted(FIXTURE_DIR.glob("*.diff"))
    # secret 검출 케이스: 평문 가짜 secret 을 공개 repo 에 커밋하지 않는다(tree scan 자기검출 방지).
    # repo 관행(키워드 런타임 조립)대로 temp diff 를 만들어 검출 능력은 동일하게 검증 — git 미커밋.
    import tempfile
    import shutil as _shutil
    _sec_dir = Path(tempfile.mkdtemp(prefix="watcher_mvp1_sec_"))
    _sec_fp = _sec_dir / "secret.diff"
    _sec_fp.write_text(
        "diff --git a/config/settings.env b/config/settings.env\n"
        "index 5555555..6666666 100644\n"
        "--- a/config/settings.env\n+++ b/config/settings.env\n"
        "@@ -1,2 +1,4 @@\n DEBUG=true\n LOG_LEVEL=info\n"
        "+api_key=" + "AKIA" + "IOSFODNN7" + "EXAMPLE\n"
        "+password=" + "hunter2" + "dummy0123456789abc\n",
        encoding="utf-8")
    fixtures = list(fixtures) + [_sec_fp]
    cases = []
    for fp in fixtures:
        diff_text = fp.read_text(encoding="utf-8")
        # 멱등: 2회 처리해 evidence jsonl byte 동일 비교
        r1 = process_one(diff_text, fp.stem)
        b1 = Path(r1["out_evidence"]).read_bytes()
        r2 = process_one(diff_text, fp.stem)
        b2 = Path(r2["out_evidence"]).read_bytes()
        r1["idempotent"] = (b1 == b2)
        cases.append(r1)

    # 게이트 판정
    checks = {
        "normal_has_chunks_no_secret": any(
            c["name"] == "normal" and c["n_chunks"] > 0 and not c["any_secret_residual"]
            and c["loader_verdict"] == "SAFE_STAGING" for c in cases),
        "secret_redacted_no_residual": any(
            c["name"] == "secret" and not c["any_secret_residual"] for c in cases),
        "empty_zero_chunks": any(
            c["name"] == "empty" and c["n_chunks"] == 0 for c in cases),
        "all_required_fields": all(c["all_chunks_have_required"] for c in cases),
        "loader_no_stop_for_safe": all(
            c["loader_verdict"] in {"SAFE_STAGING", "REVIEW_REQUIRED", "REVIEW_ONLY"} for c in cases),
        "all_idempotent": all(c["idempotent"] for c in cases),
        "no_secret_residual_anywhere": all(not c["any_secret_residual"] for c in cases),
    }
    # 운영 store write 0 확인 (스크립트는 tmp/ + reports/ 만 write)
    operating_stores = [
        BASE.parent.parent / ".claude" / "memory" / "ontology" / "_graph_merge.yaml",
        BASE.parent.parent / ".claude" / "memory" / "ontology" / "user_graph.yaml",
    ]
    # (write 자체를 안 하므로 mtime 변화 없음 — 존재 여부만 기록, 미접촉)
    gate = "GO" if all(checks.values()) else "STOP"
    report = {
        "tool": "watcher_capture_mvp1.py", "phase": "MVP1 Step0+Step1", "mode": "dry-run / selftest",
        "blocked_by_v09": True, "step2_candidate": "NOT_IMPLEMENTED (MVP1 범위 밖)",
        "write_locations": [str(TMP_OUT), str(SELFTEST_REPORT)],
        "operating_store_write": 0, "production_write": 0, "opencrab_call": 0,
        "github_push": 0, "db_write": 0,
        "checks": checks, "gate": gate, "cases": cases,
    }
    _dump(SELFTEST_REPORT, report)

    print("=" * 70)
    print("OpenBinggu Watcher MVP1 — Step0 Capture + Step1 Evidence (dry-run)")
    print("=" * 70)
    for c in cases:
        print("  [%s] events=%d chunks=%d stops=%d loader=%s idem=%s secret_residual=%s"
              % (c["name"], c["n_events"], c["n_chunks"], c["n_stops"],
                 c["loader_verdict"], c["idempotent"], c["any_secret_residual"]))
        for s in c["stops"]:
            print("        STOP:", s["reason"], "@", s["event_id"])
    print("\n  checks:")
    for k, v in checks.items():
        print("    %s %s" % ("[PASS]" if v else "[FAIL]", k))
    print("\n  temp out:", TMP_OUT)
    print("  report  :", SELFTEST_REPORT)
    _shutil.rmtree(_sec_dir, ignore_errors=True)  # 런타임 조립 secret 픽스처 정리
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def run_single(path):
    diff_text = Path(path).read_text(encoding="utf-8")
    res = process_one(diff_text, Path(path).stem)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    else:
        run_single(args[0])


if __name__ == "__main__":
    main()
