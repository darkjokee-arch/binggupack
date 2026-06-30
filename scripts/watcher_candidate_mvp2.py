# -*- coding: utf-8 -*-
"""OpenBinggu Watcher MVP2 — Step2 Candidate (backward-compatible thin wrapper).

v1.16 strangler Phase2: 순수 transform(_sha8/_has_secret/_meaningful/to_nodes +
DOMAIN/REDACT_RE/GENERATED_BY/NODE_KEYS/PROP_KEYS/EVIDX_KEYS)은
binggupack.pack.candidate_mvp2 로 이관됐고, 이 파일은 공개 심볼이 byte-identical 한 thin
wrapper 다. 기존 호출처(import watcher_candidate_mvp2 as mvp2 → mvp2.to_nodes/_meaningful 등
bare-name import; importer 5곳)는 그대로 동작한다.

범위(MVP2 고정): evidence_chunk → incoming_nodes.jsonl + incoming_evidence_index.jsonl 만.
  - incoming_edges 생성 금지(MVP2.1 분리). Step3 merge preview(match_policy) 호출 금지.
  - 출력 = temp dir(BASE/tmp/watcher_mvp2/) only. 운영 store write 0.

__file__ 경로상수(BASE/SCRIPTS/FIXTURE_DIR/TMP_OUT/SELFTEST_REPORT) + 파일 I/O 오케스트레이션
(_write_jsonl/process_one/run_selftest/run_single/CLI)은 scripts/ 위치·tmp/reports 경로 의존이라
이 wrapper 에 잔류. dry-run only(운영 store write 0).

CLI:
  python watcher_candidate_mvp2.py --selftest
  python watcher_candidate_mvp2.py <diff_text_file>
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

from binggupack.pack.candidate_mvp2 import *  # noqa: E402,F401,F403
from binggupack.pack.candidate_mvp2 import (  # noqa: E402,F401  (전체 명시 re-export)
    DOMAIN,
    REDACT_RE,
    GENERATED_BY,
    NODE_KEYS,
    PROP_KEYS,
    EVIDX_KEYS,
    _sha8,
    _has_secret,
    _meaningful,
    to_nodes,
    mvp1,
    v011,
    v07loader,
    lkmap,
    a0,
)

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "watcher_mvp1"   # MVP1 diff fixture 재사용
TMP_OUT = BASE / "tmp" / "watcher_mvp2"
SELFTEST_REPORT = BASE / "reports" / "watcher_mvp2_selftest.json"


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8")


def process_one(diff_text, name):
    events = mvp1.capture(diff_text, "git diff :: " + name)
    chunks, _ = mvp1.to_evidence(events)
    nodes, ev_index, stops = to_nodes(chunks)
    out_dir = TMP_OUT / name
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "incoming_nodes.jsonl", nodes)
    _write_jsonl(out_dir / "incoming_evidence_index.jsonl", ev_index)
    # incoming_edges.jsonl 은 생성하지 않음(MVP2.1)
    # v0.7 loader 7불변식 검증 (Step3 아님, read-only)
    loader_res = v07loader.load_incoming(str(out_dir), known_evidence_ids=None)
    refs_all_matched = all(
        all(r in {e["evidence_id"] for e in ev_index} for r in n["evidence_refs"]) for n in nodes)
    return {
        "name": name, "n_chunks": len(chunks), "n_nodes": len(nodes), "n_stops": len(stops),
        "stops": stops,
        "candidate_all_true": all(n["properties"]["candidate"] is True for n in nodes),
        "promotion_all_false": all(n["promotion_allowed"] is False for n in nodes),
        "origin_all_watcher": all(n["properties"]["origin"] == "watcher" for n in nodes),
        "domain_all_staging": all(n["properties"]["domain"] == DOMAIN for n in nodes),
        "no_d_domain": all(not re.fullmatch(r"D[1-9]", n["properties"]["domain"]) for n in nodes),
        "evidence_refs_matched": refs_all_matched,
        "any_secret_residual": any(_has_secret(n["properties"]["sentence"]) for n in nodes),
        "loader_schema_valid": loader_res["schema_valid"],
        "loader_violations": loader_res["violations"],
        "loader_nodes_accepted": loader_res["counts"]["nodes_accepted"],
        "loader_edges_in": loader_res["counts"]["edges_in"],
        "out_dir": str(out_dir),
    }


def run_selftest():
    fixtures = sorted(FIXTURE_DIR.glob("*.diff"))
    cases = []
    for fp in fixtures:
        diff_text = fp.read_text(encoding="utf-8")
        r1 = process_one(diff_text, fp.stem)
        b1 = (TMP_OUT / fp.stem / "incoming_nodes.jsonl").read_bytes()
        r2 = process_one(diff_text, fp.stem)
        b2 = (TMP_OUT / fp.stem / "incoming_nodes.jsonl").read_bytes()
        r1["idempotent"] = (b1 == b2)
        cases.append(r1)

    checks = {
        "normal_has_nodes": any(c["name"] == "normal" and c["n_nodes"] > 0 for c in cases),
        "empty_zero_nodes": any(c["name"] == "empty" and c["n_nodes"] == 0 for c in cases),
        "candidate_all_true": all(c["candidate_all_true"] for c in cases),
        "promotion_all_false": all(c["promotion_all_false"] for c in cases),
        "origin_all_watcher": all(c["origin_all_watcher"] for c in cases),
        "domain_all_staging_no_d": all(c["domain_all_staging"] and c["no_d_domain"] for c in cases),
        "evidence_refs_matched": all(c["evidence_refs_matched"] for c in cases),
        "loader_7invariant_pass": all(c["loader_schema_valid"] for c in cases),
        "loader_edges_zero": all(c["loader_edges_in"] == 0 for c in cases),
        "no_secret_residual": all(not c["any_secret_residual"] for c in cases),
        "all_idempotent": all(c["idempotent"] for c in cases),
    }
    gate = "GO" if all(checks.values()) else "STOP"
    report = {
        "tool": "watcher_candidate_mvp2.py", "phase": "MVP2 Step2 candidate (nodes only)",
        "mode": "dry-run / selftest", "blocked_by_v09": True,
        "incoming_edges": "NOT_GENERATED (MVP2.1)", "step3_match_policy_called": False,
        "write_locations": [str(TMP_OUT), str(SELFTEST_REPORT)],
        "operating_store_write": 0, "production_write": 0, "merge_call": 0, "apply_call": 0,
        "opencrab_call": 0, "github_push": 0, "db_write": 0,
        "checks": checks, "gate": gate, "cases": cases,
    }
    SELFTEST_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SELFTEST_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 70)
    print("OpenBinggu Watcher MVP2 — Step2 Candidate (nodes only, dry-run)")
    print("=" * 70)
    for c in cases:
        print("  [%s] chunks=%d nodes=%d stops=%d loader_valid=%s edges_in=%d idem=%s sec=%s"
              % (c["name"], c["n_chunks"], c["n_nodes"], c["n_stops"], c["loader_schema_valid"],
                 c["loader_edges_in"], c["idempotent"], c["any_secret_residual"]))
        for s in c["stops"]:
            print("        STOP:", s["reason"], "@", s["item_id"])
    print("\n  checks:")
    for k, v in checks.items():
        print("    %s %s" % ("[PASS]" if v else "[FAIL]", k))
    print("\n  temp out:", TMP_OUT, "\n  report  :", SELFTEST_REPORT)
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def run_single(path):
    diff_text = Path(path).read_text(encoding="utf-8")
    print(json.dumps(process_one(diff_text, Path(path).stem), ensure_ascii=False, indent=2))
    sys.exit(0)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    else:
        run_single(args[0])


if __name__ == "__main__":
    main()
