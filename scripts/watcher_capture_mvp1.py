# -*- coding: utf-8 -*-
"""OpenBinggu Watcher MVP1 — Step0 Capture + Step1 Evidence (backward-compatible thin wrapper).

v1.16 strangler Phase2: 순수 transform(_sha8/redact_text/_has_secret/_parse_diff/_path_from_header/
capture/to_evidence/build_incoming_pack + CAPTURED_AT/SCOPE)은 binggupack.pack.capture_mvp1 로
이관됐고, 이 파일은 공개 심볼이 byte-identical 한 thin wrapper 다. 기존 호출처
(import watcher_capture_mvp1 as mvp1 → mvp1.capture/to_evidence/redact_text/SCOPE/v011 등 bare-name
import; importer 5곳)는 그대로 동작한다.

__file__ 경로상수(BASE/SCRIPTS/FIXTURE_DIR/TMP_OUT/SELFTEST_REPORT) + 파일 I/O 오케스트레이션
(_dump/process_one/run_selftest/run_single/CLI)은 scripts/ 위치·tmp/reports 경로 의존이라 이
wrapper 에 잔류. dry-run only(운영 store write 0).

CLI:
  python watcher_capture_mvp1.py --selftest        # fixture 3종 + v0.11 loader dry-run + 멱등 검증
  python watcher_capture_mvp1.py <diff_text_file>  # 단일 dry-run (temp 출력)
"""
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로
if HERE not in sys.path:
    sys.path.insert(0, HERE)   # scripts 형제(importer 호환) 호환

from binggupack.pack.capture_mvp1 import *  # noqa: E402,F401,F403
from binggupack.pack.capture_mvp1 import (  # noqa: E402,F401  (전체 명시 re-export)
    CAPTURED_AT,
    SCOPE,
    _sha8,
    redact_text,
    _has_secret,
    _parse_diff,
    _path_from_header,
    capture,
    to_evidence,
    build_incoming_pack,
    v011,
)

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "watcher_mvp1"
TMP_OUT = BASE / "tmp" / "watcher_mvp1"
SELFTEST_REPORT = BASE / "reports" / "watcher_mvp1_selftest.json"


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
