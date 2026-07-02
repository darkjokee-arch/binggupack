# -*- coding: utf-8 -*-
"""회상 유용성 측정 — Phase 1 offline golden recall score (결정적 · 네트워크 0 · write 0).

목적: 릴리스 후 "회상이 실제로 쓸만한가"를 숫자로 고정한다. 저장된 합성 그래프(corpus)에
      쿼리 fixture 를 돌려 기대 node/category hit · top-k precision · preflight 위험 트리거
      정확도를 측정하고, 회귀를 GATE 로 막는다. (실사용 trace 효용 = Phase 2 후속.)

결정성: BINGGU_SEMANTIC_OFF=1 강제 → 회상=term-frequency 어휘 매칭만(자매 selftest 동일 패턴).
        corpus 문장은 토큰 겹침이 명백히 갈리도록 설계 → 기대치가 구현 스냅샷이 아니라
        사람이 어휘로 산정한 승인 기준.

측정값:
  - why_search   : hit_rate(기대 hit 회수율) · top1 정확 · exclusion(무관 노드 미회수) · top_k_precision
  - preflight    : risk_level 정확 · needs_question 정확 · avoid_patterns(버그패턴) 포함
  - judgment_trace: found · 사슬 최소 길이

실행:
  python tests/recall_consistency_harness.py                  # 측정 리포트 + GATE
  python tests/recall_consistency_harness.py --json           # 기계 판독 JSON
  python tests/recall_consistency_harness.py --selftest       # doctor 회귀망용(GATE 라인만 강조)
  python tests/recall_consistency_harness.py --validate-fixture  # fixture 무결성만 점검
"""
import json
import os
import sqlite3
import sys
import tempfile
import shutil
from datetime import datetime, timezone

# 결정적 기준: 어휘 회상만(semantic OFF). import 전에 고정(자매 selftest 패턴 — watcher_pack_builder_m0 등).
os.environ["BINGGU_SEMANTIC_OFF"] = "1"

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_FIXTURE = os.path.join(_HERE, "fixtures", "recall_consistency", "recall_golden.json")

# 정본 스키마 (Phase1) — scripts sys.path 확보 후 import.
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
from binggu_schema import apply_schema

_VALID_RISK = ("낮음", "중간", "높음")
_VALID_SUBTYPE = ("버그패턴", "교훈", "선호", "결정", "사실")


def _load_recall():
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    import binggu_recall as R
    return R


def _load_fixture():
    with open(_FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _build_ledger(corpus, ledger_path):
    """fixture corpus → temp ledger(운영 selftest 와 동일 스키마). write 는 temp 한정."""
    con = sqlite3.connect(ledger_path)
    apply_schema(con)  # 정본 스키마 (상위집합) — 아래 INSERT 는 명시 컬럼 지정.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for n in corpus["nodes"]:
        con.execute(
            "INSERT INTO nodes(node_id,node_type,sentence,candidate,state,content_hash,"
            "created_at,semantic_subtype,use_count) VALUES(?,?,?,?,?,?,?,?,?)",
            (n["node_id"], n["node_type"], n["sentence"], 1, "active", "h", now,
             n["semantic_subtype"], n.get("use_count", 0)))
        con.execute(
            "INSERT INTO evidence(evidence_id,sentence,source_pointer_id,source_hash)"
            " VALUES(?,?,?,?)",
            ("EVC-" + n["node_id"].split(":")[-1], n["sentence"], "ptr", "sh"))
    for e in corpus.get("edges", []):
        con.execute(
            "INSERT INTO edges(edge_id,relation,source,target,candidate,state,evidence_refs)"
            " VALUES(?,?,?,?,?,?,?)",
            (e["edge_id"], e["relation"], e["source"], e["target"], 0, "active", "[]"))
    con.commit()
    con.close()


# ---------------- fixture 무결성(스냅샷 아님 — 정적 규칙 점검) ----------------

def validate_fixture(fx):
    errs = []
    ids = {n["node_id"] for n in fx["corpus"]["nodes"]}
    for n in fx["corpus"]["nodes"]:
        if n["semantic_subtype"] not in _VALID_SUBTYPE:
            errs.append("노드 %s subtype 무효: %s" % (n["node_id"], n["semantic_subtype"]))
    for e in fx["corpus"].get("edges", []):
        for end in ("source", "target"):
            if e[end] not in ids:
                errs.append("엣지 %s %s 가 corpus 에 없음: %s" % (e["edge_id"], end, e[end]))
    for c in fx["cases"]:
        k = c["kind"]
        if k == "why_search":
            for fld in ("expected_hits", "expected_excluded"):
                for nid in c[fld]:
                    if nid not in ids:
                        errs.append("case %d %s 참조 무효: %s" % (c["id"], fld, nid))
            if c["expected_top1"] not in ids:
                errs.append("case %d expected_top1 무효: %s" % (c["id"], c["expected_top1"]))
            if set(c["expected_hits"]) & set(c["expected_excluded"]):
                errs.append("case %d hits/excluded 교집합 있음" % c["id"])
        elif k == "preflight":
            if c["expected_risk_level"] not in _VALID_RISK:
                errs.append("case %d risk_level 무효: %s" % (c["id"], c["expected_risk_level"]))
            for nid in c.get("expected_avoid_contains", []):
                if nid not in ids:
                    errs.append("case %d avoid 참조 무효: %s" % (c["id"], nid))
        elif k == "judgment_trace":
            pass  # dangling(미존재) 케이스 의도적 — 참조 검증 제외
        else:
            errs.append("case %d 알 수 없는 kind: %s" % (c["id"], k))
    return errs


# ---------------- 케이스 평가 ----------------

def _eval_why(R, ledger, c):
    res = R.why_search(ledger, c["query"])
    recalled = [n["node_id"] for n in res["relevant_nodes"]]
    rset = set(recalled)
    hits = set(c["expected_hits"])
    excl = set(c["expected_excluded"])
    recalled_hits = rset & hits
    excl_violation = sorted(rset & excl)
    top1_ok = bool(recalled) and recalled[0] == c["expected_top1"]
    hit_rate = len(recalled_hits) / len(hits) if hits else 1.0
    precision = len(recalled_hits) / len(recalled) if recalled else 0.0
    ok = (hit_rate == 1.0) and (not excl_violation) and top1_ok
    return {
        "id": c["id"], "kind": "why_search", "query": c["query"], "ok": ok,
        "hit_rate": round(hit_rate, 3), "top_k_precision": round(precision, 3),
        "top1_ok": top1_ok, "top1_expected": c["expected_top1"],
        "top1_actual": recalled[0] if recalled else None,
        "exclusion_violation": excl_violation, "recalled": recalled,
    }


def _eval_preflight(R, ledger, c):
    res = R.preflight_context(ledger, prompt=c["prompt"])
    avoid_ids = {m["node_id"] for m in res["avoid_patterns"]}
    risk_ok = res["risk_level"] == c["expected_risk_level"]
    needs_ok = res["needs_question"] == c["expected_needs_question"]
    avoid_need = set(c.get("expected_avoid_contains", []))
    avoid_ok = avoid_need <= avoid_ids
    ok = risk_ok and needs_ok and avoid_ok
    return {
        "id": c["id"], "kind": "preflight", "prompt": c["prompt"], "ok": ok,
        "risk_ok": risk_ok, "risk_expected": c["expected_risk_level"], "risk_actual": res["risk_level"],
        "needs_ok": needs_ok, "needs_expected": c["expected_needs_question"],
        "needs_actual": res["needs_question"],
        "avoid_ok": avoid_ok, "avoid_missing": sorted(avoid_need - avoid_ids),
        "avoid_actual": sorted(avoid_ids),
    }


def _eval_trace(R, ledger, c):
    res = R.judgment_trace(ledger, c["node_id"])
    found_ok = res["found"] == c["expected_found"]
    chain_ok = len(res["chain"]) >= c["expected_min_chain"]
    ok = found_ok and chain_ok
    return {
        "id": c["id"], "kind": "judgment_trace", "node_id": c["node_id"], "ok": ok,
        "found_ok": found_ok, "found_expected": c["expected_found"], "found_actual": res["found"],
        "chain_len": len(res["chain"]), "chain_min": c["expected_min_chain"],
    }


def evaluate():
    R = _load_recall()
    fx = _load_fixture()
    tmp = tempfile.mkdtemp(prefix="bgp_recall_golden_")
    try:
        ledger = os.path.join(tmp, "ledger.sqlite")
        _build_ledger(fx["corpus"], ledger)
        rows = []
        for c in fx["cases"]:
            if c["kind"] == "why_search":
                rows.append(_eval_why(R, ledger, c))
            elif c["kind"] == "preflight":
                rows.append(_eval_preflight(R, ledger, c))
            elif c["kind"] == "judgment_trace":
                rows.append(_eval_trace(R, ledger, c))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    why = [r for r in rows if r["kind"] == "why_search"]
    pf = [r for r in rows if r["kind"] == "preflight"]
    tr = [r for r in rows if r["kind"] == "judgment_trace"]
    summary = {
        "n_cases": len(rows),
        "passed": sum(1 for r in rows if r["ok"]),
        "failed": sum(1 for r in rows if not r["ok"]),
        "why_mean_hit_rate": round(sum(r["hit_rate"] for r in why) / len(why), 3) if why else None,
        "why_mean_top_k_precision": round(sum(r["top_k_precision"] for r in why) / len(why), 3) if why else None,
        "why_top1_accuracy": round(sum(1 for r in why if r["top1_ok"]) / len(why), 3) if why else None,
        "preflight_risk_accuracy": round(sum(1 for r in pf if r["risk_ok"]) / len(pf), 3) if pf else None,
        "preflight_needs_accuracy": round(sum(1 for r in pf if r["needs_ok"]) / len(pf), 3) if pf else None,
        "trace_accuracy": round(sum(1 for r in tr if r["ok"]) / len(tr), 3) if tr else None,
    }
    gate = "GO" if summary["failed"] == 0 else "NO-GO"
    return {"summary": summary, "gate": gate, "rows": rows}


# ---------------- 출력 ----------------

def _print_report(res):
    s = res["summary"]
    print("=" * 78)
    print("회상 골든셋 — Phase 1 offline recall score (semantic OFF 결정적, n=%d)" % s["n_cases"])
    print("=" * 78)
    print("  why_search   hit_rate(평균)        : %.1f%%" % (s["why_mean_hit_rate"] * 100))
    print("  why_search   top_k_precision(평균) : %.1f%%" % (s["why_mean_top_k_precision"] * 100))
    print("  why_search   top1 정확도           : %.1f%%" % (s["why_top1_accuracy"] * 100))
    print("  preflight    risk_level 정확도     : %.1f%%" % (s["preflight_risk_accuracy"] * 100))
    print("  preflight    needs_question 정확도 : %.1f%%" % (s["preflight_needs_accuracy"] * 100))
    print("  judgment_trace 정확도              : %.1f%%" % (s["trace_accuracy"] * 100))
    print("-" * 78)
    for r in res["rows"]:
        flag = "PASS" if r["ok"] else "FAIL"
        if r["kind"] == "why_search":
            detail = ("hit=%.0f%% prec=%.0f%% top1=%s" %
                      (r["hit_rate"] * 100, r["top_k_precision"] * 100,
                       "OK" if r["top1_ok"] else ("기대 %s 실제 %s" % (r["top1_expected"], r["top1_actual"]))))
            if r["exclusion_violation"]:
                detail += " 무관회수=%s" % r["exclusion_violation"]
        elif r["kind"] == "preflight":
            detail = ("risk=%s/%s needs=%s/%s" %
                      (r["risk_actual"], r["risk_expected"], r["needs_actual"], r["needs_expected"]))
            if r["avoid_missing"]:
                detail += " avoid누락=%s" % r["avoid_missing"]
        else:
            detail = "found=%s/%s chain=%d>=%d" % (r["found_actual"], r["found_expected"],
                                                   r["chain_len"], r["chain_min"])
        print("  [%s] #%-2d %-15s %s" % (flag, r["id"], r["kind"], detail))
    print("-" * 78)
    print("RESULT: %d/%d  GATE=%s" % (s["passed"], s["n_cases"], res["gate"]))


def main():
    args = sys.argv[1:]

    if "--validate-fixture" in args:
        errs = validate_fixture(_load_fixture())
        if errs:
            print("FIXTURE 무결성 NO-GO:")
            for e in errs:
                print("  - " + e)
            print("GATE=NO-GO")
            sys.exit(1)
        print("fixture 무결성 OK (corpus 참조·subtype·hits/excluded 정합)")
        print("GATE=GO")
        sys.exit(0)

    res = evaluate()

    if "--json" in args:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        _print_report(res)

    sys.exit(0 if res["gate"] == "GO" else 1)


if __name__ == "__main__":
    main()
