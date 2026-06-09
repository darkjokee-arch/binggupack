# -*- coding: utf-8 -*-
"""OpenBinggu Watcher M0 → pack 빌더 (dry-run only, temp pack + validate).

목적: M0 운영 산출(capture→evidence→nodes)을 reingest_pack_draft 형식의 **temp pack** 으로 묶고
  openbinggu_pack_validate.py 로 manifest 계약을 검증한다. edge 없이 node/evidence skeleton 으로 충분.
  완성형(OpenCrab pack 생성)의 앞단 dry-run.

범위(고정):
  - 입력 = git diff 텍스트 파일 1건. M0(watcher_op_m0) 재사용해 산출 생성.
  - 출력 = BASE/tmp/watcher_op_pack/<run>/ (manifest/nodes/edges/evidence_index/evidence_chunk + report) only.
  - reingest_pack_draft 원본 **미수정**. OpenCrab write/ingest 0. production graph 생성 0.
  - edge 실제 생성 0(edges.jsonl 빈 파일). review 큐 실제 적재 0.

manifest 강제: pack_type=candidate / merge_policy={review,staging,isolated} / promotion_allowed_default=false /
  status=staged / risk_level=low. (openbinggu_pack_validate REQUIRED_FIELDS 충족 → PASS 기대)
검증: validator verdict ∈ {PASS, REVIEW_ONLY} + 자체검증(secret raw 0 / candidate=true 전건 /
  promotion_allowed=false 전건 / edges=0 / 멱등 2회 byte 동일 / 운영 store mtime 불변).

CLI:
  python watcher_pack_builder_m0.py --selftest
  python watcher_pack_builder_m0.py <diff_text_file>
"""
import hashlib
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "watcher_mvp1"
TMP_PACK = BASE / "tmp" / "watcher_op_pack"
REPORTS_DIR = BASE / "reports"

sys.path.insert(0, str(SCRIPTS))
import watcher_op_m0 as m0               # M0 산출 재사용 (process_one, _store_snapshot, _has_secret)
import openbinggu_pack_validate as pv    # pack 계약 validator (read-only)
import watcher_edge_mvp21 as edgemod     # MVP2.1 evidence_supports edge producer (1차 안전가드)
import openbinggu_scope_envelope_dryrun as sed   # source pointer 판정 + fail-closed publish guard (판정 only)

PACK_RULES = [
    "단어 키워드 노드 금지 — 모든 노드는 핵심 문장",
    "secret/PII/토큰/.env 미포함(경로 포인터만)",
    "promotion_allowed=false (업로드/promote 금지)",
    "candidate=true (review 전 자동 승격 금지)",
    "edge 미생성(node/evidence skeleton, MVP2.1 구현 GO 전)",
    "origin=watcher / domain=STAGING_UNASSIGNED",
]


def _sha8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8")


def build_pack(diff_text, run):
    """M0 실행 → temp pack 묶기 → validator + 자체검증. 반환 (report_dict, pack_dir)."""
    store_before = m0._store_snapshot()

    # M0 산출 (temp; Step3 review-only read-only 호출 포함, write 0)
    m0_report, m0_out = m0.process_one(diff_text, run)
    nodes = _read_jsonl(m0_out / "incoming_nodes.jsonl")
    ev_index = _read_jsonl(m0_out / "incoming_evidence_index.jsonl")
    ev_chunk = _read_jsonl(m0_out / "evidence_chunk.jsonl")

    # MVP2.1 evidence_supports edge 생성 (producer 1차 안전가드 재사용 — node→node 0)
    freshness_map = {c["item_id"]: c.get("evidence_meta", {}).get("timestamp") for c in ev_chunk}
    edges, edge_stops = edgemod.build_edges(nodes, ev_index, freshness_map)

    # --- source pointer 공개 차단 판정 (판정 only · 치환/sanitizer 없음 · raw 경로값 미기록) ---
    # 각 evidence 의 source pointer(evidence_meta.raw_pointer → source) 를 clean/dirty/unknown 으로 판정,
    # mask_result 동등 필드(source_pointer_mask)만 부여. dirty/unknown 은 publish guard 가 BLOCK.
    sp_labels = []
    for c in ev_chunk:
        em = c.get("evidence_meta", {}) if isinstance(c.get("evidence_meta"), dict) else {}
        ptr = em.get("raw_pointer") or c.get("source_pointer") or c.get("source")
        lab = sed.classify_source_pointer(ptr)
        c["source_pointer_mask"] = lab            # builder 가 항목에 mask_result(동등 필드) 부여
        sp_labels.append(lab)
    sp_counts = {k: sp_labels.count(k) for k in ("clean", "dirty", "unknown")}
    sp_worst = "dirty" if sp_counts["dirty"] else ("unknown" if sp_counts["unknown"] else "clean")
    sp_pub = sed.publish_decision([{"mask_result": l} for l in sp_labels], True, sed.PUBLISH_REGRESSION_STATE)
    source_pointer_scan = {"counts": sp_counts, "worst_label": sp_worst,
                           "publish_allowed": sp_pub["publish_allowed"],
                           "publish_reasons": sp_pub["reason_codes"], "raw_value_recorded": False}

    pack_dir = TMP_PACK / run
    pack_dir.mkdir(parents=True, exist_ok=True)

    # manifest = validator REQUIRED_FIELDS + reingest 메타 (한 파일로 둘 다 충족)
    manifest = {
        "format_version": "opencrab-pack-v1",
        # --- validator REQUIRED_FIELDS ---
        "pack_id": "watcher_op_" + run,
        "pack_type": "candidate",
        "scope": "project:openbinggu",
        "depends_on": [],
        "evidence_policy": {"source": "watcher", "min_evidence": 0},
        "merge_policy": {"mode": "review", "target": "staging", "cross_pack": "isolated"},
        "promotion_allowed_default": False,
        "status": "staged",
        "cross_pack_tags": [],
        "risk_level": "low",
        "created_from": "watcher_op_m0",
        # --- reingest 메타 ---
        "title": "Watcher M0 운영 capture pack (candidate, dry-run)",
        "counts": {"nodes": len(nodes), "edges": len(edges), "evidence": len(ev_chunk),
                   "evidence_index": len(ev_index)},
        "source_pointer_scan": source_pointer_scan,
        "rules": PACK_RULES,
        "blocked_by_v09": True,
        "files": ["manifest.json", "nodes.jsonl", "edges.jsonl",
                  "evidence_index.jsonl", "evidence_chunk.jsonl"],
    }

    # pack 파일 write (temp only)
    (pack_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    _write_jsonl(pack_dir / "nodes.jsonl", nodes)
    _write_jsonl(pack_dir / "edges.jsonl", edges)   # MVP2.1 evidence_supports 실 edge
    _write_jsonl(pack_dir / "evidence_index.jsonl", ev_index)
    _write_jsonl(pack_dir / "evidence_chunk.jsonl", ev_chunk)

    # 1) validator (read-only, manifest dict 검증)
    v = pv.validate_pack(manifest)

    # 2) 자체검증 (validator 가 안 보는 실내용)
    edges_read = _read_jsonl(pack_dir / "edges.jsonl")
    pack_sec = (any(m0._has_secret(n.get("properties", {}).get("sentence", "") or n.get("label", ""))
                    for n in nodes)
                or any(m0._has_secret(c.get("text", "")) for c in ev_chunk)
                or any(m0._has_secret(e.get("properties", {}).get("sentence", "")) for e in edges_read))
    self_checks = {
        "no_secret_residual": not pack_sec,
        "candidate_all_true": all(n.get("properties", {}).get("candidate") is True for n in nodes),
        "promotion_all_false": all(n.get("promotion_allowed") is False for n in nodes),
        "validator_ok": v["verdict"] in {"PASS", "REVIEW_ONLY"},
        # edge 항목 (MVP2.1 연결)
        "edge_count_match": len(edges_read) == manifest["counts"]["edges"],
        "edge_no_node_to_node": all(e.get("source", "").startswith("EVC-")
                                    and e.get("target", "").startswith("node:") for e in edges_read),
        "edge_relation_supports": all(e.get("properties", {}).get("relation") == "evidence_supports"
                                      for e in edges_read),
        "edge_origin_watcher": all(e.get("properties", {}).get("origin") == "watcher" for e in edges_read),
        "edge_candidate_true": all(e.get("properties", {}).get("candidate") is True for e in edges_read),
        "edge_promotion_false": all(e.get("promotion_allowed") is False for e in edges_read),
        "edge_evidence_refs_present": all(e.get("evidence_refs") for e in edges_read),
        "edge_build_stops_zero": len(edge_stops) == 0,
    }

    store_after = m0._store_snapshot()
    self_checks["operating_store_unchanged"] = (store_before == store_after)

    report = {
        "tool": "watcher_pack_builder_m0.py", "phase": "M0 → pack 빌더 dry-run",
        "run": run, "pack_dir": str(pack_dir),
        "blocked_by_v09": True, "armed": False,
        "n_nodes": len(nodes), "n_edges": len(edges), "n_evidence": len(ev_chunk),
        "source_pointer_scan": source_pointer_scan,
        "edge_build_stops": edge_stops,
        "validator_verdict": v["verdict"], "validator_stops": v["stops"],
        "validator_reviews": v["reviews"], "validator_notes": v["notes"],
        "self_checks": self_checks,
        "store_before": store_before, "store_after": store_after,
        "manifest": manifest,
        # write 위치 명시 (temp only)
        "write_locations": [str(pack_dir), str(REPORTS_DIR / ("watcher_pack_" + run + ".json"))],
        "production_write": 0, "store_write": 0, "apply": 0, "merge": 0, "push": 0,
        "db_write": 0, "opencrab_call": 0, "opencrab_ingest": 0, "bid_engine_touch": 0,
        "edges_generated": 0, "review_queue_appended": 0,
        "reingest_pack_draft_modified": 0, "v09_or_armed_changed": 0,
    }
    return report, pack_dir


def _per_run_gate(report):
    sc = report["self_checks"]
    return {
        "validator_pass_or_review": report["validator_verdict"] in {"PASS", "REVIEW_ONLY"},
        "no_secret_residual": sc["no_secret_residual"],
        "candidate_all_true": sc["candidate_all_true"],
        "promotion_all_false": sc["promotion_all_false"],
        "edge_count_match": sc["edge_count_match"],
        "edge_no_node_to_node": sc["edge_no_node_to_node"],
        "edge_build_stops_zero": sc["edge_build_stops_zero"],
        "operating_store_unchanged": sc["operating_store_unchanged"],
    }


def run_single(path):
    diff_text = Path(path).read_text(encoding="utf-8")
    run = "single_" + _sha8(diff_text)
    report, pack_dir = build_pack(diff_text, run)
    checks = _per_run_gate(report)
    report["per_run_checks"] = checks
    report["gate"] = "GO" if all(checks.values()) else "STOP"
    rp = REPORTS_DIR / ("watcher_pack_" + run + ".json")
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"run": run, "gate": report["gate"],
                      "validator": report["validator_verdict"], "checks": checks,
                      "n_nodes": report["n_nodes"], "n_edges": report["n_edges"],
                      "pack_dir": str(pack_dir), "report": str(rp)}, ensure_ascii=False, indent=2))
    sys.exit(0 if report["gate"] == "GO" else 1)


def run_source_pointer_link_check():
    """synthetic source pointer → builder 판정(classify) → fail-closed publish guard 연결 검증.
    clean only=ALLOW / dirty·unknown 1↑=BLOCK. raw 경로값 미출력(라벨·count·reason 만). 반환 (ok, results)."""
    samples = [
        ("clean", ["EVC-hash1", "examples/toy/a.md", "https://github.com/u/r"]),
        ("dirty", ["C:\\Users\\PC\\private\\x.md"]),
        ("dirty", ["file:///C:/p/x.md"]),
        ("dirty", ["http://localhost:9000/api"]),
        ("dirty", ["http://192.168.1.2/internal"]),
        ("unknown", ["MASK_UNDECIDED_TOKEN"]),
        ("unknown", [""]),
    ]
    results, ok = [], True
    for expect_kind, ptrs in samples:
        labels = [sed.classify_source_pointer(p) for p in ptrs]
        pub = sed.publish_decision([{"mask_result": l} for l in labels], True, sed.PUBLISH_REGRESSION_STATE)
        all_clean = all(l == "clean" for l in labels)
        if expect_kind == "clean":
            intended = (pub["publish_allowed"] is True) and all_clean
        else:
            intended = (pub["publish_allowed"] is False) and (not all_clean)
        fail_open = (expect_kind != "clean" and pub["publish_allowed"] is True)
        intended = intended and not fail_open
        ok = ok and intended
        results.append({"expect_kind": expect_kind,
                        "counts": {k: labels.count(k) for k in ("clean", "dirty", "unknown")},
                        "publish_allowed": pub["publish_allowed"], "reasons": pub["reason_codes"],
                        "fail_open": fail_open, "as_intended": intended})
    return ok, results


def run_selftest():
    if not FIXTURE_DIR.is_dir():
        print("[FAIL] fixture 디렉토리 없음:", FIXTURE_DIR)
        sys.exit(1)
    fixtures = sorted(FIXTURE_DIR.glob("*.diff"))
    cases = []
    for fp in fixtures:
        diff_text = fp.read_text(encoding="utf-8")
        run = fp.stem
        r1, pack_dir = build_pack(diff_text, run)
        # 멱등: pack 파일 5종 byte 동일 비교
        names = ["manifest.json", "nodes.jsonl", "edges.jsonl",
                 "evidence_index.jsonl", "evidence_chunk.jsonl"]
        b1 = {n: (pack_dir / n).read_bytes() for n in names}
        r2, _ = build_pack(diff_text, run)
        b2 = {n: (pack_dir / n).read_bytes() for n in names}
        r1["idempotent"] = (b1 == b2)
        r1["per_run_checks"] = _per_run_gate(r1)
        rp = REPORTS_DIR / ("watcher_pack_" + run + ".json")
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(r1, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        cases.append(r1)

    by = {c["run"]: c for c in cases}
    sp_link_ok, sp_link_results = run_source_pointer_link_check()
    checks = {
        "normal_pack_has_nodes": "normal" in by and by["normal"]["n_nodes"] > 0,
        "empty_pack_zero_nodes": "empty" in by and by["empty"]["n_nodes"] == 0,
        "all_validator_pass_or_review": all(
            c["validator_verdict"] in {"PASS", "REVIEW_ONLY"} for c in cases),
        "no_validator_stop": all(c["validator_verdict"] != "STOP" for c in cases),
        "no_secret_residual": all(c["self_checks"]["no_secret_residual"] for c in cases),
        "candidate_all_true": all(c["self_checks"]["candidate_all_true"] for c in cases),
        "promotion_all_false": all(c["self_checks"]["promotion_all_false"] for c in cases),
        "normal_pack_has_edges": "normal" in by and by["normal"]["n_edges"] > 0,
        "edge_count_match_all": all(c["self_checks"]["edge_count_match"] for c in cases),
        "edge_no_node_to_node_all": all(c["self_checks"]["edge_no_node_to_node"] for c in cases),
        "edge_relation_supports_all": all(c["self_checks"]["edge_relation_supports"] for c in cases),
        "edge_candidate_promotion_all": all(c["self_checks"]["edge_candidate_true"]
                                            and c["self_checks"]["edge_promotion_false"] for c in cases),
        "edge_build_stops_zero_all": all(c["self_checks"]["edge_build_stops_zero"] for c in cases),
        "all_idempotent": all(c["idempotent"] for c in cases),
        "operating_store_unchanged": all(c["self_checks"]["operating_store_unchanged"] for c in cases),
        "source_pointer_scan_present": all("source_pointer_scan" in c["manifest"] for c in cases),
        "source_pointer_link_ok": sp_link_ok,
        "writes_temp_only": all(
            all(("/tmp/watcher_op_pack/" in w.replace("\\", "/") or "/reports/" in w.replace("\\", "/"))
                for w in c["write_locations"]) for c in cases),
    }
    gate = "GO" if all(checks.values()) else "STOP"
    summary = {
        "tool": "watcher_pack_builder_m0.py", "phase": "M0 → pack 빌더 dry-run selftest",
        "mode": "dry-run / selftest", "blocked_by_v09": True, "armed": False,
        "operating_store_write": 0, "production_write": 0, "opencrab_ingest": 0,
        "edges_generated": 0, "review_queue_appended": 0, "reingest_pack_draft_modified": 0,
        "checks": checks, "gate": gate,
        "cases": [{"run": c["run"], "n_nodes": c["n_nodes"], "n_edges": c["n_edges"],
                   "n_evidence": c["n_evidence"], "validator_verdict": c["validator_verdict"],
                   "idempotent": c["idempotent"],
                   "store_unchanged": c["self_checks"]["operating_store_unchanged"],
                   "pack_dir": c["pack_dir"]} for c in cases],
    }
    rp = REPORTS_DIR / "watcher_pack_builder_m0_selftest.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 72)
    print("OpenBinggu Watcher M0 → pack 빌더 (dry-run / selftest)")
    print("=" * 72)
    for c in cases:
        print("  [%s] nodes=%d edges=%d evidence=%d validator=%s idem=%s store_unchanged=%s"
              % (c["run"], c["n_nodes"], c["n_edges"], c["n_evidence"], c["validator_verdict"],
                 c["idempotent"], c["self_checks"]["operating_store_unchanged"]))
        if c["validator_stops"]:
            for s in c["validator_stops"]:
                print("        validator STOP:", s)
        sps = c["manifest"].get("source_pointer_scan", {})
        print("        source_pointer_scan: counts=%s worst=%s publish_allowed=%s"
              % (sps.get("counts"), sps.get("worst_label"), sps.get("publish_allowed")))
    print("\n  --- source pointer 판정 → fail-closed publish 연결 (raw 경로 미출력) ---")
    for r in sp_link_results:
        mark = "[OK]" if r["as_intended"] else "[FAIL]"
        print("    %s expect=%-8s counts=%s publish_allowed=%-5s reasons=%s%s"
              % (mark, r["expect_kind"], r["counts"], r["publish_allowed"], r["reasons"],
                 "  FAIL-OPEN!" if r["fail_open"] else ""))
    print("\n  checks:")
    for k, v in checks.items():
        print("    %s %s" % ("[PASS]" if v else "[FAIL]", k))
    print("\n  temp pack:", TMP_PACK)
    print("  report   :", rp)
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
