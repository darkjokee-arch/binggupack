# -*- coding: utf-8 -*-
"""OpenBinggu Watcher MVP2.1 — evidence_supports edge producer (backward-compatible thin wrapper).

v1.16 strangler Phase2: 순수 transform(build_edges/_sha8/_has_secret/_freshness_from_chunks +
EVIDENCE_FANOUT_CAP/NODE_INDEGREE_CAP/EDGE_KEYS/EDGE_PROP_KEYS/REDACT_RE 상수)은
binggupack.pack.edge_mvp21 로 이관됐고, 이 파일은 공개 심볼이 byte-identical 한 thin wrapper 다.
기존 호출처(import watcher_edge_mvp21 as ... → build_edges 등 bare-name; pack_builder_m0)는 그대로
동작한다.

__file__ 경로상수(BASE/SCRIPTS/FIXTURE_DIR/TMP_OUT/SELFTEST_REPORT) + 파일 I/O 오케스트레이션
(_write_jsonl/process_from_diff/_emit/selftest fixture/run_selftest/run_single/CLI)은 scripts/ 위치·
tmp/reports 경로 의존이라 이 wrapper 에 잔류. dry-run only(운영 store write 0).

설계: docs/BINGGUPACK_MVP21_EDGE_SAFETY_FILTER_DESIGN.md (R2).
범위(MVP2.1 고정): evidence → node `evidence_supports` edge 1종만 생성. node→node 의미추론 전면 금지.
  - 출력 = BASE/tmp/watcher_edge_mvp21/<run>/incoming_edges.jsonl only (temp/staging).
2차 차단(소비처): localbinggu_match_policy(policy.match).classify_edge_pair (origin=watcher edge
  auto_merge 자격 박탈 → review).

CLI:
  python watcher_edge_mvp21.py --selftest
  python watcher_edge_mvp21.py <diff_text_file>   # M0 흐름으로 nodes 생성 후 edge
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

from binggupack.pack.edge_mvp21 import *  # noqa: E402,F401,F403
from binggupack.pack.edge_mvp21 import (  # noqa: E402,F401  (밑줄 내부 심볼 + 전체 명시 re-export)
    EVIDENCE_FANOUT_CAP,
    NODE_INDEGREE_CAP,
    EDGE_KEYS,
    EDGE_PROP_KEYS,
    REDACT_RE,
    _sha8,
    _has_secret,
    _freshness_from_chunks,
    build_edges,
    v011,
)

import binggupack.pack.capture_mvp1 as mvp1       # capture / to_evidence
import binggupack.pack.candidate_mvp2 as mvp2     # to_nodes
import binggupack.policy.match as mp              # 2차 소비처 필터 (classify_edge_pair)

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "watcher_mvp1"
TMP_OUT = BASE / "tmp" / "watcher_edge_mvp21"
SELFTEST_REPORT = BASE / "reports" / "watcher_edge_mvp21_selftest.json"


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8")


def process_from_diff(diff_text, run):
    """M0 흐름 재사용: diff → nodes/evidence → edge."""
    events = mvp1.capture(diff_text, "git diff :: " + run)
    chunks, _ = mvp1.to_evidence(events)
    nodes, ev_index, _ = mvp2.to_nodes(chunks)
    fresh = _freshness_from_chunks(chunks)
    return _emit(nodes, ev_index, fresh, run)


def _emit(nodes, ev_index, freshness_map, run):
    edges, stops = build_edges(nodes, ev_index, freshness_map)
    out_dir = TMP_OUT / run
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "incoming_edges.jsonl", edges)
    # 2차 소비처 필터 (read-only): watcher edge auto_merge 자격 박탈 확인
    buckets = mp.evaluate_edges([{**e, "id": e["id"]} for e in edges])
    edge_summary = mp.summarize_edges(buckets)
    # 합성 dup edge로 강등 실증
    synth = {"tested": False, "auto": None, "review": None}
    if edges:
        a = json.loads(json.dumps(edges[0]))
        b = json.loads(json.dumps(edges[0]))
        b["id"] = a["id"] + ":synthdup"   # 같은 role(src/tgt/relation), origin=watcher
        bk = mp.evaluate_edges([a, b])
        bs = mp.summarize_edges(bk)
        synth = {"tested": True, "auto": bs["edge_auto_merge_allowed_count"],
                 "review": bs["edge_review_candidate_count"]}
    return {
        "run": run, "n_nodes": len(nodes), "n_edges": len(edges), "n_stops": len(stops),
        "stops": stops, "out_dir": str(out_dir),
        "relation_all_supports": all(e["properties"]["relation"] == "evidence_supports" for e in edges),
        "origin_all_watcher": all(e["properties"]["origin"] == "watcher" for e in edges),
        "candidate_all_true": all(e["properties"]["candidate"] is True for e in edges),
        "promotion_all_false": all(e["promotion_allowed"] is False for e in edges),
        "evidence_refs_present": all(e["evidence_refs"] for e in edges),
        "no_node_to_node": all(e["source"].startswith("EVC-") and e["target"].startswith("node:") for e in edges),
        "any_secret_residual": any(_has_secret(e["properties"]["sentence"]) for e in edges),
        "edge_summary": edge_summary,
        "synthetic_dup": synth,
    }


# ---------- selftest (합성 fixture) ----------
def _mk_node(sha, sentence, refs, domain="STAGING_UNASSIGNED"):
    return {"id": "node:STAGING:wch:" + sha, "space": "claim", "node_type": "Claim",
            "label": sentence, "properties": {"label_kind": "판단", "sentence": sentence,
            "domain": domain, "candidate": True, "evidence_status": "partial", "origin": "watcher"},
            "evidence_refs": refs, "promotion_allowed": False}


def _mk_ev(eid):
    return {"evidence_id": eid, "kind": "file_pointer", "source_path": "x/y.py",
            "domain": "STAGING_UNASSIGNED", "promotion_allowed": False, "note": "ptr"}


def _fixtures():
    """(name, nodes, ev_index, freshness_map, expect) — expect: 'pass' edge>0 stop0 / 'stop' stop>0."""
    fx = []
    # 1. normal: 2 evidence → 2 node, 정상
    n1 = _mk_node("aaa1", "노드1 문장", ["EVC-e1"])
    n2 = _mk_node("bbb2", "노드2 문장", ["EVC-e2"])
    fx.append(("normal", [n1, n2], [_mk_ev("EVC-e1"), _mk_ev("EVC-e2")],
               {"EVC-e1": "t", "EVC-e2": "t"}, "pass"))
    # 2. duplicate: 같은 ref 2번 (동일 src/tgt/relation)
    nd = _mk_node("dup1", "중복 노드", ["EVC-d1", "EVC-d1"])
    fx.append(("duplicate", [nd], [_mk_ev("EVC-d1")], {"EVC-d1": "t"}, "stop"))
    # 3. dangling evidence: ref 가 ev_index 에 없음
    nde = _mk_node("dng1", "댕글 ev", ["EVC-missing"])
    fx.append(("dangling_evidence", [nde], [_mk_ev("EVC-other")], {"EVC-missing": "t"}, "stop"))
    # 4. dangling node: ev 는 있으나 target node 가 set 밖 (고아 node 참조)
    #    build_edges 는 nodes 순회라 target 은 항상 자기 node → dangling node 재현 위해 node_ids 에서 제외 케이스를
    #    별도 구성: ref 가 가리키는 node 가 없는 상황 = 여기선 freshness 정상, ev 정상이나 node set 강제 축소 불가.
    #    대신 'node 미존재' 는 ev 정상 + target prefix 깨짐으로 direction 가드가 잡음 → 별도 'broken target' fixture.
    nbt = _mk_node("brk1", "broken target", ["EVC-b1"])
    nbt["id"] = "BROKEN_no_prefix"   # target prefix 깨짐 → direction/node 가드
    fx.append(("dangling_node", [nbt], [_mk_ev("EVC-b1")], {"EVC-b1": "t"}, "stop"))
    # 5. fan-out 초과: 1 evidence → 9 node (CAP 8)
    fan_nodes = [_mk_node("f%02d" % i, "fanout %d" % i, ["EVC-fan"]) for i in range(9)]
    fx.append(("fanout_exceed", fan_nodes, [_mk_ev("EVC-fan")],
               {"EVC-fan": "t"}, "stop"))
    # 6. freshness 누락: timestamp 없음
    nf = _mk_node("frs1", "freshness 누락", ["EVC-f1"])
    fx.append(("freshness_missing", [nf], [_mk_ev("EVC-f1")], {"EVC-f1": None}, "stop"))
    # 7. secret 포함: node sentence 에 secret raw
    ns = _mk_node("sec1", "AKIA" + "IOSFODNN7EXAMPLE 키 노출", ["EVC-s1"])
    fx.append(("secret_included", [ns], [_mk_ev("EVC-s1")], {"EVC-s1": "t"}, "stop"))
    return fx


def run_selftest():
    cases = []
    for name, nodes, ev_index, fresh, expect in _fixtures():
        r1 = _emit(nodes, ev_index, fresh, name)
        b1 = (TMP_OUT / name / "incoming_edges.jsonl").read_bytes()
        _emit(nodes, ev_index, fresh, name)
        b2 = (TMP_OUT / name / "incoming_edges.jsonl").read_bytes()
        r1["idempotent"] = (b1 == b2)
        r1["expect"] = expect
        if expect == "pass":
            r1["case_ok"] = (r1["n_edges"] > 0 and r1["n_stops"] == 0 and not r1["any_secret_residual"])
        else:
            r1["case_ok"] = (r1["n_stops"] > 0)
        cases.append(r1)

    by = {c["run"]: c for c in cases}
    # node 회귀: match_policy 기존 node evaluate 동작 무변(간이 — normalize+evaluate 결과 구조 확인)
    node_regression_ok = True
    try:
        sample = [_mk_node("r1", "회귀 노드", ["EVC-r1"]), _mk_node("r1", "회귀 노드", ["EVC-r1"])]
        nn = mp.normalize_nodes(sample)
        nb, _, ncda = mp.evaluate(nn)
        ns2 = mp.summarize(nb, [], ncda)
        node_regression_ok = (ns2["auto_merge_allowed_count"] == 0)  # watcher 동일노드 → 강등(0)
    except Exception:
        node_regression_ok = False

    checks = {
        "normal_edges_pass": "normal" in by and by["normal"]["case_ok"],
        "risk_fixtures_stop": all(by[k]["case_ok"] for k in
            ["duplicate", "dangling_evidence", "dangling_node", "fanout_exceed", "freshness_missing", "secret_included"]),
        "relation_all_supports": all(c["relation_all_supports"] for c in cases),
        "origin_all_watcher": all(c["origin_all_watcher"] for c in cases),
        "candidate_all_true": all(c["candidate_all_true"] for c in cases),
        "promotion_all_false": all(c["promotion_all_false"] for c in cases),
        "no_node_to_node": all(c["no_node_to_node"] for c in cases),
        "evidence_refs_present": all(c["evidence_refs_present"] for c in cases),
        "no_secret_residual": all(not c["any_secret_residual"] for c in cases),
        "watcher_edge_auto_merge_zero": all(
            c["edge_summary"]["edge_auto_merge_allowed_count"] == 0 for c in cases),
        "synthetic_dup_review_only": all(
            (not c["synthetic_dup"]["tested"]) or
            (c["synthetic_dup"]["auto"] == 0 and c["synthetic_dup"]["review"] >= 1) for c in cases),
        "all_idempotent": all(c["idempotent"] for c in cases),
        "node_regression_zero": node_regression_ok,
    }
    gate = "GO" if all(checks.values()) else "STOP"
    report = {
        "tool": "watcher_edge_mvp21.py", "phase": "MVP2.1 evidence_supports edge producer",
        "mode": "dry-run / selftest", "blocked_by_v09": True,
        "EVIDENCE_FANOUT_CAP": EVIDENCE_FANOUT_CAP, "NODE_INDEGREE_CAP": NODE_INDEGREE_CAP,
        "operating_store_write": 0, "production_write": 0, "merge": 0, "apply": 0,
        "opencrab_call": 0, "db_write": 0, "github_push": 0, "node_to_node_edges": 0,
        "checks": checks, "gate": gate,
        "cases": [{"run": c["run"], "expect": c["expect"], "case_ok": c["case_ok"],
                   "n_edges": c["n_edges"], "n_stops": c["n_stops"],
                   "edge_auto_merge": c["edge_summary"]["edge_auto_merge_allowed_count"],
                   "synth_dup": c["synthetic_dup"], "idempotent": c["idempotent"],
                   "stops": c["stops"]} for c in cases],
    }
    SELFTEST_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SELFTEST_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 74)
    print("OpenBinggu Watcher MVP2.1 — evidence_supports edge producer (dry-run)")
    print("=" * 74)
    for c in cases:
        print("  [%s] expect=%s ok=%s edges=%d stops=%d edge_auto=%d synth(auto=%s/review=%s) idem=%s"
              % (c["run"], c["expect"], c["case_ok"], c["n_edges"], c["n_stops"],
                 c["edge_summary"]["edge_auto_merge_allowed_count"],
                 c["synthetic_dup"]["auto"], c["synthetic_dup"]["review"], c["idempotent"]))
    print("\n  checks:")
    for k, v in checks.items():
        print("    %s %s" % ("[PASS]" if v else "[FAIL]", k))
    print("\n  temp out:", TMP_OUT, "\n  report  :", SELFTEST_REPORT)
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def run_single(path):
    diff_text = Path(path).read_text(encoding="utf-8")
    res = process_from_diff(diff_text, "single_" + _sha8(diff_text))
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
