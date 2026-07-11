# -*- coding: utf-8 -*-
"""OpenBinggu pack → review queue e2e (dry-run only).

목적: M0 pack builder 산출(node+edge candidate pack)을 review queue 입력으로 변환 →
  bridge(v0.12) 적재 → 합성 decision(approve/reject/defer) → resolver(v0.8) dry-run.
  production 반영 없이 review-only e2e 확인.

연결:
  watcher_pack_builder_m0.build_pack(diff) → pack_dir(nodes+edges+evidence)
    → pack_to_staging_plan(어댑터, node·edge 모두 review item) → bridge.bridge() → review_queue
    → resolver.resolve(preview_items, 합성 decisions) → audit/buckets (production_write=False)

무수정 재사용: openbinggu_review_queue_bridge / localbinggu_review_resolver / watcher_pack_builder_m0.
금지(BLOCKED_BY_V09): apply/store/production write / OpenCrab / DB / github push / v09·ARMED / example-project /
  resolver.write_reports(파일 reports write) 미호출 — resolve() 순수함수만.

CLI:
  python openbinggu_pack_review_e2e.py --selftest
  python openbinggu_pack_review_e2e.py <diff_text_file>
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "watcher_mvp1"
TMP_OUT = BASE / "tmp" / "watcher_pack_review_e2e"
SELFTEST_REPORT = BASE / "reports" / "openbinggu_pack_review_e2e_selftest.json"

sys.path.insert(0, str(SCRIPTS))
import watcher_pack_builder_m0 as packer        # build_pack
import watcher_op_m0 as m0                       # _store_snapshot, _has_secret
import openbinggu_review_queue_bridge as bridgemod   # bridge (무수정)
import localbinggu_review_resolver as resolvermod    # resolve (무수정)


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def pack_to_staging_plan(pack_dir, pack_id):
    """pack(nodes+edges) → bridge 입력 staging_plan. node·edge 모두 REVIEW_REQUIRED item.
       candidate_refs.nodes / candidate_refs.edges / evidence_refs 보존."""
    nodes = _read_jsonl(pack_dir / "nodes.jsonl")
    edges = _read_jsonl(pack_dir / "edges.jsonl")
    items = []
    for n in nodes:
        items.append({
            "verdict": "REVIEW_REQUIRED",
            "source_pack_id": pack_id + "::node::" + n["id"],
            "reason_codes": ["watcher_candidate_node"],
            "human_summary": "watcher candidate node — 사람 검토 필요",
            "candidate_refs": {"nodes": [n["id"]], "edges": []},
            "evidence_refs": list(n.get("evidence_refs", [])),
            "risk_level": "low",
            "cross_pack_tags": [],
        })
    for e in edges:
        items.append({
            "verdict": "REVIEW_REQUIRED",
            "source_pack_id": pack_id + "::edge::" + e["id"],
            "reason_codes": ["watcher_candidate_edge", "relation:" + e.get("properties", {}).get("relation", "")],
            "human_summary": "watcher candidate evidence_supports edge — 사람 검토 필요",
            "candidate_refs": {"nodes": [], "edges": [e["id"]]},
            "evidence_refs": list(e.get("evidence_refs", [])),
            "risk_level": "low",
            "cross_pack_tags": [],
        })
    return {"source_staging_plan_id": pack_id, "items": items}, nodes, edges


def _synthetic_decisions(preview_items):
    """3분기 합성: 1st approve_safe_merge / 2nd reject / 나머지 defer(또는 keep_review_only).
       review_id 결정적 순서. 반환 {review_id: decision}."""
    decisions = {}
    for i, it in enumerate(preview_items):
        rid = it["review_id"]
        if i == 0:
            decisions[rid] = "approve_safe_merge"
        elif i == 1:
            decisions[rid] = "reject"
        elif i == 2:
            decisions[rid] = "keep_review_only"
        else:
            decisions[rid] = "defer"
    return decisions


def run_e2e(diff_text, run):
    store_before = m0._store_snapshot()

    # 1) pack 생성 (M0 builder, edge 포함)
    pack_report, pack_dir = packer.build_pack(diff_text, "e2e_" + run)

    # 2) 어댑터: pack → staging_plan (node·edge 모두 review item)
    plan, nodes, edges = pack_to_staging_plan(pack_dir, "e2e_" + run)

    # 3) bridge 적재 (무수정)
    bres = bridgemod.bridge(plan)
    review_queue = bres["review_queue"]
    preview_items = bres["v08_review_workflow_preview"]["items"]

    # node/edge review item 수
    n_node_items = sum(1 for ri in review_queue if ri["candidate_refs"]["nodes"])
    n_edge_items = sum(1 for ri in review_queue if ri["candidate_refs"]["edges"])

    # edge 누락/evidence_refs 보존 점검
    edge_ids_in_pack = {e["id"] for e in edges}
    edge_ids_in_queue = set()
    for ri in review_queue:
        for eid in ri["candidate_refs"]["edges"]:
            edge_ids_in_queue.add(eid)
    edge_not_missing = (edge_ids_in_pack == edge_ids_in_queue)
    # evidence_refs 보존: queue item evidence_refs 가 비지 않은 것(node/edge 모두 ref 보유)
    ev_refs_preserved = all(ri["evidence_refs"] for ri in review_queue) if review_queue else True

    # 4) resolver 3분기 (resolve() 순수함수만, write_reports 미호출)
    decisions = _synthetic_decisions(preview_items)
    audit, buckets, decision, why = resolvermod.resolve(preview_items, decisions)

    store_after = m0._store_snapshot()

    # secret raw (pack 내용 전수)
    ev_chunk = _read_jsonl(pack_dir / "evidence_chunk.jsonl")
    secret_raw = (any(m0._has_secret(n.get("properties", {}).get("sentence", "")) for n in nodes)
                  or any(m0._has_secret(e.get("properties", {}).get("sentence", "")) for e in edges)
                  or any(m0._has_secret(c.get("text", "")) for c in ev_chunk))

    return {
        "run": run, "pack_dir": str(pack_dir),
        "n_nodes": len(nodes), "n_edges": len(edges),
        "review_queue_total": len(review_queue),
        "n_node_review_items": n_node_items, "n_edge_review_items": n_edge_items,
        "edge_not_missing": edge_not_missing,
        "evidence_refs_preserved": ev_refs_preserved,
        "bridge_counters": bres["counters"],
        "bridge_blocked": len(bres["blocked"]),
        "decisions": decisions,
        "resolver_buckets": buckets,
        "resolver_decision": decision,
        "resolver_audit": audit,
        "production_write_false": all(a.get("promotion_allowed") is False for a in audit),
        "any_secret_residual": secret_raw,
        "store_before": store_before, "store_after": store_after,
        "operating_store_unchanged": (store_before == store_after),
    }


def _per_run_gate(r):
    buckets = r["resolver_buckets"]
    return {
        "review_queue_matches_pack": r["review_queue_total"] == (r["n_nodes"] + r["n_edges"]),
        "node_items_match": r["n_node_review_items"] == r["n_nodes"],
        "edge_items_match": r["n_edge_review_items"] == r["n_edges"],
        "edge_not_missing": r["edge_not_missing"],
        "evidence_refs_preserved": r["evidence_refs_preserved"],
        "approve_applied": (len(buckets["applied"]) >= 1) if r["review_queue_total"] >= 1 else True,
        "reject_excluded": (len(buckets["excluded"]) >= 1) if r["review_queue_total"] >= 2 else True,
        "defer_held": (len(buckets["held"]) >= 1) if r["review_queue_total"] >= 3 else True,
        "promotion_all_false": r["production_write_false"],
        "no_secret_residual": not r["any_secret_residual"],
        "bridge_counters_zero": all(v == 0 for v in r["bridge_counters"].values()),
        "operating_store_unchanged": r["operating_store_unchanged"],
    }


def run_single(path):
    diff_text = Path(path).read_text(encoding="utf-8")
    import hashlib
    run = "single_" + hashlib.sha256(diff_text.encode("utf-8")).hexdigest()[:8]
    r = run_e2e(diff_text, run)
    r["per_run_checks"] = _per_run_gate(r)
    r["gate"] = "GO" if all(r["per_run_checks"].values()) else "STOP"
    print(json.dumps({"run": run, "gate": r["gate"],
                      "review_queue": {"total": r["review_queue_total"], "node": r["n_node_review_items"],
                                       "edge": r["n_edge_review_items"]},
                      "resolver_buckets": r["resolver_buckets"],
                      "resolver_decision": r["resolver_decision"],
                      "checks": r["per_run_checks"]}, ensure_ascii=False, indent=2))
    sys.exit(0 if r["gate"] == "GO" else 1)


def run_selftest():
    fixtures = sorted(FIXTURE_DIR.glob("*.diff"))
    cases = []
    for fp in fixtures:
        diff_text = fp.read_text(encoding="utf-8")
        run = fp.stem
        r1 = run_e2e(diff_text, run)
        # 멱등: review_queue 직렬화 2회 동일
        b1 = json.dumps(r1["resolver_audit"], sort_keys=True, ensure_ascii=False)
        r2 = run_e2e(diff_text, run)
        b2 = json.dumps(r2["resolver_audit"], sort_keys=True, ensure_ascii=False)
        r1["idempotent"] = (b1 == b2)
        r1["per_run_checks"] = _per_run_gate(r1)
        cases.append(r1)

    by = {c["run"]: c for c in cases}
    checks = {
        "normal_has_node_and_edge_items": "normal" in by and by["normal"]["n_node_review_items"] > 0
                                          and by["normal"]["n_edge_review_items"] > 0,
        "all_per_run_pass": all(all(c["per_run_checks"].values()) for c in cases),
        "edge_never_missing": all(c["edge_not_missing"] for c in cases),
        "evidence_refs_preserved": all(c["evidence_refs_preserved"] for c in cases),
        "promotion_all_false": all(c["production_write_false"] for c in cases),
        "no_secret_residual": all(not c["any_secret_residual"] for c in cases),
        "bridge_counters_zero": all(all(v == 0 for v in c["bridge_counters"].values()) for c in cases),
        "operating_store_unchanged": all(c["operating_store_unchanged"] for c in cases),
        "all_idempotent": all(c["idempotent"] for c in cases),
        # 3분기 실증 (normal: node2+edge2=4 items)
        "three_way_demonstrated": "normal" in by
            and len(by["normal"]["resolver_buckets"]["applied"]) >= 1
            and len(by["normal"]["resolver_buckets"]["excluded"]) >= 1
            and len(by["normal"]["resolver_buckets"]["held"]) >= 1,
    }
    gate = "GO" if all(checks.values()) else "STOP"
    report = {
        "tool": "openbinggu_pack_review_e2e.py", "phase": "pack→review queue→resolver e2e",
        "mode": "dry-run / selftest", "blocked_by_v09": True,
        "production_write": 0, "store_write": 0, "apply": 0, "opencrab_call": 0,
        "db_write": 0, "github_push": 0, "example_project_touch": 0,
        "checks": checks, "gate": gate,
        "cases": [{"run": c["run"], "n_nodes": c["n_nodes"], "n_edges": c["n_edges"],
                   "review_queue_total": c["review_queue_total"],
                   "node_items": c["n_node_review_items"], "edge_items": c["n_edge_review_items"],
                   "resolver_buckets": c["resolver_buckets"], "resolver_decision": c["resolver_decision"],
                   "idempotent": c["idempotent"], "store_unchanged": c["operating_store_unchanged"]}
                  for c in cases],
    }
    SELFTEST_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SELFTEST_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 74)
    print("OpenBinggu pack → review queue → resolver e2e (dry-run / selftest)")
    print("=" * 74)
    for c in cases:
        rb = c["resolver_buckets"]
        print("  [%s] nodes=%d edges=%d queue=%d(node=%d/edge=%d) buckets(app=%d/exc=%d/held=%d) dec=%s idem=%s store=%s"
              % (c["run"], c["n_nodes"], c["n_edges"], c["review_queue_total"],
                 c["n_node_review_items"], c["n_edge_review_items"],
                 len(rb["applied"]), len(rb["excluded"]), len(rb["held"]),
                 c["resolver_decision"], c["idempotent"], c["operating_store_unchanged"]))
    print("\n  checks:")
    for k, v in checks.items():
        print("    %s %s" % ("[PASS]" if v else "[FAIL]", k))
    print("\n  temp:", TMP_OUT, "\n  report:", SELFTEST_REPORT)
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
