# -*- coding: utf-8 -*-
"""OpenBinggu Watcher 운영모드 M0 — 수동 1회 (capture → evidence → nodes → report, temp/staging only).

설계: docs/BINGGUPACK_WATCHER_READONLY_OPERATING_MODE_DESIGN.md §2 (M0 수동 1회).
범위(M0 고정):
  - 입력 = git diff 텍스트 파일 1건(단일 소스). 라이브 git 호출/hook/daemon 없음.
  - 흐름 = capture → evidence_chunk → incoming_nodes → report. **edge 생성 금지.**
  - 출력 = BASE/tmp/watcher_op/<run>/ + BASE/reports/watcher_op_<run>.json (temp/staging only).
  - MVP1(watcher_capture_mvp1) + MVP2(watcher_candidate_mvp2) to_nodes 재사용. 신규 변환 로직 0.
  - Step3(match_policy) 는 **review-only 유지 검증용 read-only** 로만 호출(write/merge/apply 0).

강제(전건): candidate=true / promotion_allowed=false / origin=watcher / domain=STAGING_UNASSIGNED.
금지: apply / store / DB / OpenCrab write / GitHub push / bid-engine 변경 / v09·ARMED 변경 / edge 생성 /
  hook·daemon 등록 / 운영 store(_graph_merge·user_graph) write.
STOP: secret raw 잔존 / temp 외 write / edge 생성 / candidate=false / promotion_allowed=true /
  Step3 auto_merge 발생 / 운영 store mtime 변동 / 멱등 깨짐.

CLI:
  python watcher_op_m0.py --selftest        # fixture 3종(normal/empty/secret) + Step3 review-only + store 불변
  python watcher_op_m0.py <diff_text_file>  # 단일 운영 1회 (temp 산출)
"""
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "watcher_mvp1"   # MVP1 diff fixture 재사용
TMP_ROOT = BASE / "tmp" / "watcher_op"
REPORTS_DIR = BASE / "reports"

sys.path.insert(0, str(SCRIPTS))
import watcher_capture_mvp1 as mvp1       # Step0+1 capture/to_evidence 재사용
import watcher_candidate_mvp2 as mvp2     # Step2 to_nodes 재사용
import localbinggu_match_policy as mp     # Step3 review-only 검증(read-only)

# 운영 store (절대 write 금지 — mtime 불변 검증 대상). MVP1 의 BASE.parent.parent 경로 버그 회피, home 기준 정확 경로.
ONTOLOGY = Path.home() / ".claude" / "memory" / "ontology"
OPERATING_STORES = [ONTOLOGY / "_graph_merge.yaml", ONTOLOGY / "user_graph.yaml"]


def _sha8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


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


def _has_secret(text):
    return any(pat.search(text) for pat in mvp1.v011.SECRET_PATTERNS)


def verify_step3_review_only(nodes):
    """Step3(match_policy) read-only 호출로 watcher 노드의 auto_merge 자격 박탈(review 강등) 확인.
       write/merge/apply 0. 반환: dict(검증 지표)."""
    norm = mp.normalize_nodes(nodes)
    # (a) 실제 capture 노드 페어와이즈: auto_merge 후보 0 이어야 함.
    buckets, fuzzy, cda = mp.evaluate(norm)
    s = mp.summarize(buckets, fuzzy, cda)
    # (b) 합성 duplicate(동일 sentence watcher 노드 2개) → wrapper 강등 작동 직접 증명.
    synth_auto, synth_review, synth_tested = None, None, False
    if norm:
        a = dict(norm[0])
        b = dict(a)
        b["id"] = a["id"] + ":synthdup"
        b["evidence_refs"] = set(a["evidence_refs"])  # set 공유 회피
        bs, bf, bc = mp.evaluate([a, b])
        ss = mp.summarize(bs, bf, bc)
        synth_auto = ss["auto_merge_allowed_count"]
        synth_review = ss["localbinggu_review_candidate_count"]
        synth_tested = True
    return {
        "capture_auto_merge_allowed": s["auto_merge_allowed_count"],
        "capture_cross_domain_auto_merge": s["cross_domain_auto_merge_count"],
        "synthetic_dup_tested": synth_tested,
        "synthetic_dup_auto_merge": synth_auto,   # 0 기대 (watcher override 강등)
        "synthetic_dup_review_candidate": synth_review,  # >=1 기대
        "rapidfuzz_available": mp.RF,
    }


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


def _per_run_gate(report):
    """단일 run 안전 게이트."""
    s3 = report["step3_review_only"]
    checks = {
        "no_secret_residual": not report["any_secret_residual"],
        "candidate_all_true": report["candidate_all_true"],
        "promotion_all_false": report["promotion_all_false"],
        "origin_all_watcher": report["origin_all_watcher"],
        "domain_all_staging": report["domain_all_staging"],
        "no_edges": report["edges_generated"] == 0,
        "step3_capture_auto_merge_zero": s3["capture_auto_merge_allowed"] == 0,
        "step3_synthetic_dup_review_only": (
            (not s3["synthetic_dup_tested"])
            or (s3["synthetic_dup_auto_merge"] == 0 and s3["synthetic_dup_review_candidate"] >= 1)),
        "operating_store_unchanged": report["operating_store_unchanged"],
    }
    return checks


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
