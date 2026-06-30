# -*- coding: utf-8 -*-
"""OpenBinggu Watcher 운영모드 M0 — 수동 1회 (backward-compatible thin wrapper).

v1.16 strangler Phase2: 순수 transform(_sha8/_has_secret/verify_step3_review_only/_per_run_gate)은
binggupack.pack.op_m0 로 이관됐고, 이 파일은 공개 심볼이 byte-identical 한 thin wrapper 다. 기존
호출처(import watcher_op_m0 as m0 → m0.process_one/_store_snapshot/_has_secret 등 bare-name import;
importer = watcher_pack_builder_m0/watcher_batch_m1/openbinggu_pack_consumer_smoke/
openbinggu_pack_review_e2e/openbinggu_scope_envelope_dryrun/binggupack_http_mcp_skeleton_selftest)는
그대로 동작한다.

__file__ 경로상수(BASE/SCRIPTS/FIXTURE_DIR/TMP_ROOT/REPORTS_DIR) + 운영 store 경로상수(ONTOLOGY/
OPERATING_STORES, home 기준) + 파일 I/O 오케스트레이션(_store_snapshot/_write_jsonl/process_one/
run_selftest/run_single/main/CLI)은 scripts/ 위치·tmp/reports·운영 store 경로 의존이라 이 wrapper
에 잔류. dry-run only(운영 store write 0).

설계: docs/BINGGUPACK_WATCHER_READONLY_OPERATING_MODE_DESIGN.md §2 (M0 수동 1회).
범위(M0 고정): git diff 텍스트 1건 → capture → evidence → nodes → report (edge 생성 금지),
  출력 = BASE/tmp/watcher_op/<run>/ + BASE/reports/watcher_op_<run>.json (temp/staging only).
강제(전건): candidate=true / promotion_allowed=false / origin=watcher / domain=STAGING_UNASSIGNED.
Step3(match_policy) 는 review-only 유지 검증용 read-only 로만 호출(write/merge/apply 0).

CLI:
  python watcher_op_m0.py --selftest        # fixture 3종(normal/empty/secret) + Step3 review-only + store 불변
  python watcher_op_m0.py <diff_text_file>  # 단일 운영 1회 (temp 산출)
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

from binggupack.pack.op_m0 import *  # noqa: E402,F401,F403
from binggupack.pack.op_m0 import (  # noqa: E402,F401  (전체 명시 re-export)
    _sha8,
    _has_secret,
    verify_step3_review_only,
    _per_run_gate,
    mvp1,
    mvp2,
    mp,
)

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "watcher_mvp1"   # MVP1 diff fixture 재사용
TMP_ROOT = BASE / "tmp" / "watcher_op"
REPORTS_DIR = BASE / "reports"

# 운영 store (절대 write 금지 — mtime 불변 검증 대상). MVP1 의 BASE.parent.parent 경로 버그 회피, home 기준 정확 경로.
ONTOLOGY = Path.home() / ".claude" / "memory" / "ontology"
OPERATING_STORES = [ONTOLOGY / "_graph_merge.yaml", ONTOLOGY / "user_graph.yaml"]


def _store_snapshot():
    """운영 store mtime/size 스냅샷 (write 안 함 — read-only stat)."""
    snap = {}
    for p in OPERATING_STORES:
        if p.exists():
            st = p.stat()
            snap[str(p)] = {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "exists": True}
        else:
            snap[str(p)] = {"exists": False}
    return snap


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8")


def process_one(diff_text, run):
    """단일 diff → capture → evidence → nodes → temp 산출 + Step3 review-only 검증 + report."""
    store_before = _store_snapshot()

    # capture → evidence (MVP1 재사용)
    events = mvp1.capture(diff_text, "git diff :: " + run)
    chunks, ev_stops = mvp1.to_evidence(events)
    # evidence → nodes (MVP2 재사용, 엣지 미생성)
    nodes, ev_index, node_stops = mvp2.to_nodes(chunks)

    # temp 산출 (설계 §4 경로)
    out_dir = TMP_ROOT / run
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "evidence_chunk.jsonl", chunks)
    _write_jsonl(out_dir / "incoming_nodes.jsonl", nodes)
    _write_jsonl(out_dir / "incoming_evidence_index.jsonl", ev_index)
    # incoming_edges.jsonl 은 생성하지 않음 (edge 구현 GO 후에만)

    # Step3 review-only 검증 (read-only)
    step3 = verify_step3_review_only(nodes)

    store_after = _store_snapshot()
    store_unchanged = (store_before == store_after)

    report = {
        "tool": "watcher_op_m0.py", "phase": "M0 수동 1회 운영모드", "mode": "manual single run",
        "run": run,
        "blocked_by_v09": True, "armed": False,
        "n_events": len(events), "n_chunks": len(chunks), "n_nodes": len(nodes),
        "n_stops": len(ev_stops) + len(node_stops),
        "stops": ev_stops + node_stops,
        # 안전 불변식
        "candidate_all_true": all(n["properties"]["candidate"] is True for n in nodes),
        "promotion_all_false": all(n["promotion_allowed"] is False for n in nodes),
        "origin_all_watcher": all(n["properties"]["origin"] == "watcher" for n in nodes),
        "domain_all_staging": all(n["properties"]["domain"] == "STAGING_UNASSIGNED" for n in nodes),
        "any_secret_residual": (any(_has_secret(c["text"]) for c in chunks)
                                or any(_has_secret(n["properties"]["sentence"]) for n in nodes)),
        "edges_generated": 0,
        "step3_review_only": step3,
        "store_before": store_before, "store_after": store_after,
        "operating_store_unchanged": store_unchanged,
        # write 위치는 전부 temp/reports 임을 명시
        "write_locations": [str(out_dir), str(REPORTS_DIR / ("watcher_op_" + run + ".json"))],
        "production_write": 0, "store_write": 0, "apply": 0, "merge": 0,
        "push": 0, "db_write": 0, "opencrab_call": 0, "bid_engine_touch": 0,
        "hook_daemon_registered": 0, "v09_or_armed_changed": 0,
    }
    return report, out_dir


def run_single(path):
    diff_text = Path(path).read_text(encoding="utf-8")
    run = "single_" + _sha8(diff_text)
    report, out_dir = process_one(diff_text, run)
    checks = _per_run_gate(report)
    report["per_run_checks"] = checks
    report["gate"] = "GO" if all(checks.values()) else "STOP"
    rp = REPORTS_DIR / ("watcher_op_" + run + ".json")
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"run": run, "gate": report["gate"], "checks": checks,
                      "n_nodes": report["n_nodes"], "out_dir": str(out_dir), "report": str(rp)},
                     ensure_ascii=False, indent=2))
    sys.exit(0 if report["gate"] == "GO" else 1)


def run_selftest():
    if not FIXTURE_DIR.is_dir():
        print("[FAIL] fixture 디렉토리 없음:", FIXTURE_DIR)
        sys.exit(1)
    fixtures = sorted(FIXTURE_DIR.glob("*.diff"))
    cases = []
    for fp in fixtures:
        diff_text = fp.read_text(encoding="utf-8")
        run = fp.stem
        # 멱등: 2회 처리해 incoming_nodes byte 동일 비교
        r1, out_dir = process_one(diff_text, run)
        b1 = (out_dir / "incoming_nodes.jsonl").read_bytes()
        r2, _ = process_one(diff_text, run)
        b2 = (out_dir / "incoming_nodes.jsonl").read_bytes()
        r1["idempotent"] = (b1 == b2)
        r1["per_run_checks"] = _per_run_gate(r1)
        # 단건 report 도 떨군다(운영 산출 일관)
        rp = REPORTS_DIR / ("watcher_op_" + run + ".json")
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(r1, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        cases.append(r1)

    by = {c["run"]: c for c in cases}
    # 사장님 명시 검증 항목
    checks = {
        "normal_has_nodes": "normal" in by and by["normal"]["n_nodes"] > 0,
        "empty_zero_nodes": "empty" in by and by["empty"]["n_nodes"] == 0,
        "secret_redaction_no_residual": "secret" in by and not by["secret"]["any_secret_residual"],
        "no_secret_residual_anywhere": all(not c["any_secret_residual"] for c in cases),
        "candidate_all_true": all(c["candidate_all_true"] for c in cases),
        "promotion_all_false": all(c["promotion_all_false"] for c in cases),
        "origin_all_watcher": all(c["origin_all_watcher"] for c in cases),
        "domain_all_staging": all(c["domain_all_staging"] for c in cases),
        "no_edges_generated": all(c["edges_generated"] == 0 for c in cases),
        "step3_review_only_kept": all(c["per_run_checks"]["step3_capture_auto_merge_zero"]
                                      and c["per_run_checks"]["step3_synthetic_dup_review_only"]
                                      for c in cases),
        "operating_store_unchanged": all(c["operating_store_unchanged"] for c in cases),
        "all_idempotent": all(c["idempotent"] for c in cases),
        "writes_temp_only": all(
            all(("/tmp/watcher_op/" in w.replace("\\", "/") or "/reports/" in w.replace("\\", "/"))
                for w in c["write_locations"]) for c in cases),
    }
    gate = "GO" if all(checks.values()) else "STOP"
    summary = {
        "tool": "watcher_op_m0.py", "phase": "M0 수동 1회 운영모드 selftest",
        "mode": "dry-run / selftest", "blocked_by_v09": True, "armed": False,
        "operating_store_write": 0, "production_write": 0, "apply": 0, "merge": 0,
        "push": 0, "db_write": 0, "opencrab_call": 0, "bid_engine_touch": 0,
        "edges_generated": 0, "hook_daemon_registered": 0,
        "checks": checks, "gate": gate,
        "cases": [{"run": c["run"], "n_chunks": c["n_chunks"], "n_nodes": c["n_nodes"],
                   "n_stops": c["n_stops"], "any_secret_residual": c["any_secret_residual"],
                   "idempotent": c["idempotent"],
                   "step3": c["step3_review_only"],
                   "store_unchanged": c["operating_store_unchanged"]} for c in cases],
    }
    rp = REPORTS_DIR / "watcher_op_m0_selftest.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 72)
    print("OpenBinggu Watcher 운영모드 M0 — 수동 1회 (capture→evidence→nodes→report)")
    print("=" * 72)
    for c in cases:
        s3 = c["step3_review_only"]
        print("  [%s] chunks=%d nodes=%d stops=%d secret_residual=%s idem=%s store_unchanged=%s"
              % (c["run"], c["n_chunks"], c["n_nodes"], c["n_stops"],
                 c["any_secret_residual"], c["idempotent"], c["operating_store_unchanged"]))
        print("        step3: capture_auto_merge=%s synth_dup_auto=%s synth_dup_review=%s"
              % (s3["capture_auto_merge_allowed"], s3["synthetic_dup_auto_merge"],
                 s3["synthetic_dup_review_candidate"]))
        for st in c["stops"]:
            print("        STOP:", st.get("reason"), "@", st.get("item_id", st.get("event_id")))
    print("\n  checks:")
    for k, v in checks.items():
        print("    %s %s" % ("[PASS]" if v else "[FAIL]", k))
    print("\n  temp out:", TMP_ROOT)
    print("  report  :", rp)
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    else:
        run_single(args[0])


if __name__ == "__main__":
    main()
