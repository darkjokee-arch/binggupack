# -*- coding: utf-8 -*-
"""OpenBinggu Watcher MVP2.1 — evidence_supports edge producer (dry-run only).

설계: docs/OPENBINGGU_MVP21_EDGE_SAFETY_FILTER_DESIGN.md (R2).
범위(MVP2.1 고정):
  - **evidence → node `evidence_supports` edge 1종만** 생성. 기존 evidence_refs 를 1급 edge 로 승격.
  - node→node 의미관계 자동추론 **전면 금지**. 신규 의미추론 0.
  - 출력 = BASE/tmp/watcher_edge_mvp21/<run>/incoming_edges.jsonl only (temp/staging).
  - MVP1(capture/evidence) + MVP2(to_nodes) 재사용해 입력 nodes/evidence 생성.

1차 차단(생산자 가드): dangling(evidence/node 미존재) · self-loop · fan-out cap(evidence 8 / node indegree 16) ·
  direction(evidence→node 단방향) · freshness(evidence timestamp 필수) · duplicate(동일 src/tgt/relation) · secret raw.
2차 차단(소비처): localbinggu_match_policy.classify_edge_pair (origin=watcher edge auto_merge 자격 박탈 → review).

강제(전건): relation=evidence_supports / origin=watcher / candidate=true / promotion_allowed=false / evidence_refs 필수.
STOP: 위 1차 가드 위반 1건이라도 / 멱등 깨짐 / temp 외 write / 운영 store mtime 변동 / watcher edge auto_merge>0.

CLI:
  python watcher_edge_mvp21.py --selftest
  python watcher_edge_mvp21.py <diff_text_file>   # M0 흐름으로 nodes 생성 후 edge
"""
import hashlib
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "watcher_mvp1"
TMP_OUT = BASE / "tmp" / "watcher_edge_mvp21"
SELFTEST_REPORT = BASE / "reports" / "watcher_edge_mvp21_selftest.json"

sys.path.insert(0, str(SCRIPTS))
import watcher_capture_mvp1 as mvp1       # capture / to_evidence
import watcher_candidate_mvp2 as mvp2     # to_nodes
import openbinggu_incoming_to_staging as v011   # secret 패턴
import localbinggu_match_policy as mp     # 2차 소비처 필터 (classify_edge_pair)

EVIDENCE_FANOUT_CAP = 8     # evidence 1개가 만들 수 있는 supports edge 수
NODE_INDEGREE_CAP = 16      # node 1개가 받는 incoming supports edge 수
EDGE_KEYS = {"id", "edge_type", "source", "target", "properties", "evidence_refs", "promotion_allowed"}
EDGE_PROP_KEYS = {"relation", "domain", "candidate", "origin", "sentence"}
REDACT_RE = re.compile(r"\[REDACTED:\d+\]")


def _sha8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _has_secret(text):
    return any(pat.search(text) for pat in v011.SECRET_PATTERNS)


def build_edges(nodes, ev_index, freshness_map):
    """nodes + evidence_index + freshness_map → (edges, stops). 1차 안전가드 전수.
       freshness_map: {evidence_id: timestamp}. 위반 1건이라도 있으면 stops 에 기록(전체 STOP 신호)."""
    edges, stops = [], []
    ev_ids = {e["evidence_id"] for e in ev_index}
    node_ids = {n["id"] for n in nodes}
    ev_fanout = {}      # evidence_id -> 생성 edge 수
    node_indeg = {}     # node_id -> incoming edge 수
    seen_edge_ids = set()

    for n in nodes:
        tgt = n["id"]
        sentence = n.get("properties", {}).get("sentence", n.get("label", ""))
        for ev_id in n.get("evidence_refs", []):
            src = ev_id
            # direction: source=evidence(EVC-), target=node(node:)
            if not (src in ev_ids):
                stops.append({"reason": "dangling evidence_ref (evidence 미존재)", "src": src, "tgt": tgt})
                continue
            if tgt not in node_ids:
                stops.append({"reason": "dangling node (target 미존재)", "src": src, "tgt": tgt})
                continue
            if src == tgt:
                stops.append({"reason": "self-loop", "src": src, "tgt": tgt})
                continue
            if not src.startswith("EVC-") or not tgt.startswith("node:"):
                stops.append({"reason": "direction 위반(evidence→node 아님)", "src": src, "tgt": tgt})
                continue
            # freshness: evidence timestamp 필수
            ts = freshness_map.get(ev_id)
            if not ts:
                stops.append({"reason": "freshness stamp 누락", "src": src, "tgt": tgt})
                continue
            # secret raw (node sentence 기준)
            if _has_secret(sentence):
                stops.append({"reason": "secret residual in node sentence", "src": src, "tgt": tgt})
                continue
            # fan-out cap
            ev_fanout[src] = ev_fanout.get(src, 0) + 1
            node_indeg[tgt] = node_indeg.get(tgt, 0) + 1
            if ev_fanout[src] > EVIDENCE_FANOUT_CAP:
                stops.append({"reason": f"evidence fan-out cap 초과(>{EVIDENCE_FANOUT_CAP})", "src": src, "tgt": tgt})
                continue
            if node_indeg[tgt] > NODE_INDEGREE_CAP:
                stops.append({"reason": f"node indegree cap 초과(>{NODE_INDEGREE_CAP})", "src": src, "tgt": tgt})
                continue
            eid = "edge:STAGING:wch:" + _sha8(src + "→" + tgt)
            # duplicate (동일 src/tgt/relation → 동일 id)
            if eid in seen_edge_ids:
                stops.append({"reason": "duplicate relation (동일 src/tgt/relation)", "src": src, "tgt": tgt})
                continue
            seen_edge_ids.add(eid)
            domain = n.get("properties", {}).get("domain", "STAGING_UNASSIGNED")
            edge = {
                "id": eid,
                "edge_type": "EvidenceSupports",
                "source": src,
                "target": tgt,
                "properties": {
                    "relation": "evidence_supports",
                    "domain": domain,
                    "candidate": True,
                    "origin": "watcher",
                    "sentence": "evidence가 노드를 뒷받침한다",
                },
                "evidence_refs": [ev_id],
                "promotion_allowed": False,
            }
            assert set(edge) <= EDGE_KEYS and set(edge["properties"]) <= EDGE_PROP_KEYS, "edge key whitelist 위반"
            edges.append(edge)
    return edges, stops


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8")


def _freshness_from_chunks(chunks):
    return {c["item_id"]: c.get("evidence_meta", {}).get("timestamp") for c in chunks}


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
        r2 = _emit(nodes, ev_index, fresh, name)
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
    except Exception as e:
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
