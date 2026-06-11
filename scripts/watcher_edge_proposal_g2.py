# -*- coding: utf-8 -*-
"""OpenBinggu G2 — node→node 약한 후보 엣지 proposal 생산기 (dry-run only).

4cli R3 지시 2·4·8 준수:
  - 6종 강한 동사 라벨(supports_judgment 등) **자동 생산 0** — 스키마 검증기(openbinggu_verb_edge_schema)로
    검증만 하고, 생산은 명시 단서/승인 주입 경로의 몫.
  - 자동 생산은 약한 후보 2종만: nearby_candidate(구조 신호: co_evidence/same_file) ·
    stance_candidate(판단쌍 상반 어조 — 확정 아님, 후보 표시만).
  - 산출물 = edge_proposals.jsonl (incoming_edges.jsonl 아님) → v0.7 loader가 구조적으로 읽지 않음.
    본 그래프 진입은 C-2 승인 후만 (이 스크립트는 temp write만).

cap: 쌍당 1 + run당 32. 멱등(sha8 id·sort). 운영 store write 0.
CLI: python watcher_edge_proposal_g2.py --selftest
"""
import hashlib
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
TMP_OUT = BASE / "tmp" / "watcher_edge_proposal_g2"
SELFTEST_REPORT = BASE / "reports" / "watcher_edge_proposal_g2_selftest.json"

sys.path.insert(0, str(SCRIPTS))
import openbinggu_verb_edge_schema as schema   # 약한 라벨 정의 + 강한 라벨 검증기(거부 증명용)
import localbinggu_incoming_loader as v07loader  # loader 미입력 구조 증명용 (read-only)

RUN_CAP = 32
NEG_RX = re.compile(r"(보류|중단|기각|반대|하지 않|않는다|위험)")
POS_RX = re.compile(r"(진행|채택|승인|참여|가능|안전|좋다|낫다)")

GENERATED_BY = {"extractor": "watcher_edge_proposal_g2", "rule_version": "g2.1"}


def _sha8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _pair_key(a, b):
    return tuple(sorted([a, b]))


def build_proposals(nodes, ev_index):
    """nodes(G0 5종 분류본) + evidence_index → (proposals, stats). 약한 후보 2종만."""
    ev_src = {e["evidence_id"]: e.get("source_path", "") for e in ev_index}
    by_ev = {}       # evidence_id -> [node]
    by_file = {}     # source_path -> [node]
    for n in nodes:
        for r in n.get("evidence_refs", []):
            by_ev.setdefault(r, []).append(n)
            sp = ev_src.get(r, "")
            if sp:
                by_file.setdefault(sp, []).append(n)

    proposals = []
    seen_pairs = set()
    capped = 0

    def emit(a, b, label, signal, shared_refs):
        nonlocal capped
        pk = _pair_key(a["id"], b["id"])
        if pk in seen_pairs:
            return
        seen_pairs.add(pk)
        if len(proposals) >= RUN_CAP:
            capped += 1
            return
        pid = "prop:STAGING:g2:" + _sha8(pk[0] + "→" + pk[1] + "::" + label)
        proposals.append({
            "id": pid, "kind": "edge_proposal", "label": label, "signal": signal,
            "source": pk[0], "target": pk[1],
            "evidence_refs": sorted(set(shared_refs)),
            "promotion_allowed": False,
            "properties": {"candidate": True, "origin": "watcher",
                           "generated_by": dict(GENERATED_BY)},
        })

    # 신호 1 — co_evidence: 같은 evidence 를 공유하는 노드쌍
    for ev_id, ns in sorted(by_ev.items()):
        ns_sorted = sorted(ns, key=lambda n: n["id"])
        for i in range(len(ns_sorted)):
            for j in range(i + 1, len(ns_sorted)):
                a, b = ns_sorted[i], ns_sorted[j]
                ka = a["properties"].get("label_kind")
                kb = b["properties"].get("label_kind")
                # 신호 1b — stance: 판단쌍 + 상반 어조 → stance_candidate (확정 아님)
                if ka == "판단" and kb == "판단":
                    sa, sb = a["properties"].get("sentence", ""), b["properties"].get("sentence", "")
                    opposed = (NEG_RX.search(sa) and POS_RX.search(sb)) or \
                              (POS_RX.search(sa) and NEG_RX.search(sb))
                    if opposed:
                        emit(a, b, "stance_candidate", "co_evidence_opposed_stance", [ev_id])
                        continue
                emit(a, b, "nearby_candidate", "co_evidence", [ev_id])

    # 신호 2 — same_file: 같은 source_path 에서 나온 노드쌍
    for sp, ns in sorted(by_file.items()):
        ns_sorted = sorted(ns, key=lambda n: n["id"])
        for i in range(len(ns_sorted)):
            for j in range(i + 1, len(ns_sorted)):
                a, b = ns_sorted[i], ns_sorted[j]
                shared = set(a.get("evidence_refs", [])) & set(b.get("evidence_refs", []))
                refs = shared or (set(a.get("evidence_refs", [])[:1]) | set(b.get("evidence_refs", [])[:1]))
                emit(a, b, "nearby_candidate", "same_file", refs)

    proposals.sort(key=lambda p: p["id"])
    stats = {"n_proposals": len(proposals), "capped_skipped": capped,
             "labels": {lb: sum(1 for p in proposals if p["label"] == lb)
                        for lb in sorted({p["label"] for p in proposals})}}
    return proposals, stats


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8")


def emit_run(nodes, ev_index, run):
    proposals, stats = build_proposals(nodes, ev_index)
    out_dir = TMP_OUT / run
    _write_jsonl(out_dir / "edge_proposals.jsonl", proposals)
    # loader 미입력 구조 증명: 같은 디렉토리를 loader 로 읽어도 edges_in=0 (파일명이 다르므로)
    loader_res = v07loader.load_incoming(str(out_dir), known_evidence_ids=None)
    return {"run": run, "out_dir": str(out_dir), "stats": stats,
            "proposals": proposals,
            "loader_edges_in": loader_res["counts"]["edges_in"],
            "strong_labels_generated": sum(1 for p in proposals if p["label"] in schema.VERB_EDGES),
            "weak_only": all(p["label"] in schema.WEAK_LABELS for p in proposals),
            "promotion_all_false": all(p["promotion_allowed"] is False for p in proposals),
            "evidence_refs_present": all(p["evidence_refs"] for p in proposals)}


# ---------------- selftest (합성 fixture) ----------------

def _n(nid, kind, sentence, refs):
    return {"id": "node:STAGING:wch:" + nid, "evidence_refs": list(refs),
            "properties": {"label_kind": kind, "sentence": sentence, "candidate": True}}


def _ev(eid, sp="syn/a.md"):
    return {"evidence_id": eid, "source_path": sp}


def _selftest():
    all_ok = True
    checks = {}

    # case 1 — co_evidence: 증거+판단이 같은 evidence 공유 → nearby_candidate 1건
    n1 = [_n("a1", "증거", "로그에 결과가 기록되어 있다.", ["EVC-x"]),
          _n("a2", "판단", "이 입찰은 보류한다.", ["EVC-x"])]
    r1 = emit_run(n1, [_ev("EVC-x")], "co_ev")
    checks["co_evidence_nearby_1"] = (r1["stats"]["labels"].get("nearby_candidate") == 1
                                      and r1["stats"]["n_proposals"] == 1)

    # case 2 — stance: 판단쌍 상반 어조 → stance_candidate
    n2 = [_n("b1", "판단", "마진이 낮아 보류한다.", ["EVC-y"]),
          _n("b2", "판단", "조건이 좋아 진행한다.", ["EVC-y"])]
    r2 = emit_run(n2, [_ev("EVC-y")], "stance")
    checks["stance_candidate_1"] = (r2["stats"]["labels"].get("stance_candidate") == 1)

    # case 3 — same_file: evidence 다르지만 같은 source_path → nearby_candidate
    n3 = [_n("c1", "상태", "백필 작업이 진행 중이다.", ["EVC-p"]),
          _n("c2", "판단", "백필 완료 후 검증해야 한다.", ["EVC-q"])]
    r3 = emit_run(n3, [_ev("EVC-p", "syn/work.md"), _ev("EVC-q", "syn/work.md")], "same_file")
    checks["same_file_nearby_1"] = (r3["stats"]["labels"].get("nearby_candidate") == 1)

    # case 4 — cap: 노드 12개 같은 evidence 공유 (66쌍) → RUN_CAP 32 에서 절단 + capped 기록
    n4 = [_n("d%02d" % i, "판단", "케이스 %d 는 보류한다." % i, ["EVC-z"]) for i in range(12)]
    r4 = emit_run(n4, [_ev("EVC-z")], "cap")
    checks["run_cap_enforced"] = (r4["stats"]["n_proposals"] <= 32 and r4["stats"]["capped_skipped"] > 0)

    # case 5 — 중복 쌍 1회 (co_evidence + same_file 이중 신호 → 쌍당 1)
    n5 = [_n("e1", "증거", "결과가 기록되어 있다.", ["EVC-w"]),
          _n("e2", "판단", "이 방식을 채택한다.", ["EVC-w"])]
    r5 = emit_run(n5, [_ev("EVC-w", "syn/dup.md")], "dup_pair")
    checks["pair_dedup_1"] = (r5["stats"]["n_proposals"] == 1)

    # 공통 불변식 (전 run)
    runs = [r1, r2, r3, r4, r5]
    checks["strong_labels_zero"] = all(r["strong_labels_generated"] == 0 for r in runs)
    checks["weak_only_all"] = all(r["weak_only"] for r in runs)
    checks["loader_edges_in_zero"] = all(r["loader_edges_in"] == 0 for r in runs)
    checks["promotion_all_false"] = all(r["promotion_all_false"] for r in runs)
    checks["evidence_refs_present"] = all(r["evidence_refs_present"] for r in runs)

    # 멱등 (case 1 재실행 byte 동일)
    b1 = (TMP_OUT / "co_ev" / "edge_proposals.jsonl").read_bytes()
    emit_run(n1, [_ev("EVC-x")], "co_ev")
    b2 = (TMP_OUT / "co_ev" / "edge_proposals.jsonl").read_bytes()
    checks["idempotent"] = (b1 == b2)

    # 강한 라벨 검증기 연동: proposal 을 본 그래프 엣지로 위장 투입 시 schema 검증기가 거부
    fake_nodes = {n["id"]: n for n in n1}
    fake_edge = {"id": "prop_as_edge", "source": n1[0]["id"], "target": n1[1]["id"],
                 "properties": {"relation": "nearby_candidate", "candidate": True},
                 "evidence_refs": ["EVC-x"], "promotion_allowed": False}
    checks["schema_rejects_weak_as_edge"] = (
        schema.validate_verb_edge(fake_edge, fake_nodes)["verdict"] == "FAIL")

    all_ok = all(checks.values())
    gate = "GO" if all_ok else "STOP"
    report = {"tool": "watcher_edge_proposal_g2.py", "mode": "dry-run / selftest",
              "operating_store_write": 0, "production_write": 0, "merge": 0, "apply": 0,
              "confirmed_created": 0, "deploy": 0,
              "checks": checks, "gate": gate,
              "runs": [{k: v for k, v in r.items() if k != "proposals"} for r in runs]}
    SELFTEST_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SELFTEST_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
                               encoding="utf-8")

    print("=" * 74)
    print("OpenBinggu G2 — edge proposal 생산기 (약한 후보 2종, dry-run)")
    print("=" * 74)
    for k, v in checks.items():
        print("    %s %s" % ("[PASS]" if v else "[FAIL]", k))
    print("\n  temp out:", TMP_OUT, "\n  report  :", SELFTEST_REPORT)
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        _selftest()
    else:
        print("usage: watcher_edge_proposal_g2.py [--selftest]")
        sys.exit(2)
