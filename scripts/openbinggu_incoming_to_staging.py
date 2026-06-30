# -*- coding: utf-8 -*-
"""OpenBinggu v0.11 — incoming → staging loader (backward-compatible thin wrapper).

v1.16 strangler Phase2: 정본 transform(SECRET_PATTERNS/scan_secrets/assess_incoming/fixture)은
binggupack.pack.incoming_to_staging 로 이관됐고, 이 파일은 공개 심볼이 byte-identical 한 thin
wrapper 다. 기존 호출처(import openbinggu_incoming_to_staging as v011 → v011.assess_incoming/
SECRET_PATTERNS 등 bare-name import; importer 8곳)는 그대로 동작한다.

__file__ 경로상수(BASE/SCRIPTS/REPORT) + selftest/CLI 오케스트레이션(run_selftest/run_single)은
scripts/ 위치·reports 경로 의존이라 이 wrapper 에 잔류. dry-run only(production write 0).

CLI:
  python scripts/openbinggu_incoming_to_staging.py --selftest      # synthetic fixtures 전수
  python scripts/openbinggu_incoming_to_staging.py <incoming.json> # 단일 dry-run
"""
import json
import os
import shutil
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로
if HERE not in sys.path:
    sys.path.insert(0, HERE)   # scripts 형제(v010 wrapper 등) 호환

from binggupack.pack.incoming_to_staging import *  # noqa: E402,F401,F403
from binggupack.pack.incoming_to_staging import (  # noqa: E402,F401  (전체 명시 re-export)
    SECRET_PATTERNS,
    scan_secrets,
    assess_incoming,
    _expected_from_name,
    _base_pack,
    _content,
    synthesize_fixtures,
    v010,
)

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
SELFTEST_REPORT = BASE / "reports" / "openbinggu_v011_selftest.json"
STAGING_PLAN_REPORT = BASE / "reports" / "openbinggu_v011_staging_plan.json"


def run_selftest():
    fixture_dir = synthesize_fixtures()
    fixtures = sorted(fixture_dir.glob("*.json"))
    cases, staging_plan = [], []
    n_match = n_mismatch = 0
    agg = {"production_write": 0, "operating_store_write": 0, "opencrab_call": 0, "github_push": 0}

    for fp in fixtures:
        expected = _expected_from_name(fp.name)
        try:
            incoming = json.loads(fp.read_text(encoding="utf-8"))
            res = assess_incoming(incoming)
        except Exception as e:
            res = {"verdict": "ERROR", "contract_verdict": "N/A", "reasons": [repr(e)],
                   "counters": agg}
        ok = (res["verdict"] == expected)
        n_match += ok
        n_mismatch += (not ok)
        for k in agg:
            agg[k] += res.get("counters", {}).get(k, 0)
        cases.append({
            "fixture": fp.name, "expected": expected, "actual": res["verdict"],
            "match": ok, "contract_verdict": res.get("contract_verdict"),
            "reasons": res["reasons"],
        })
        if res["verdict"] == "SAFE_STAGING" and "normalized_pack" in res:
            staging_plan.append(res["normalized_pack"])

    shutil.rmtree(fixture_dir, ignore_errors=True)  # non-retention: temp fixture 즉시 정리

    all_match = (n_mismatch == 0 and n_match > 0)
    selftest = {
        "loader": "openbinggu_incoming_to_staging.py", "version": "v0.11",
        "mode": "dry-run / selftest", "blocked_by_v09": True,
        "fixture_mode": "synthetic_temp", "n_cases": len(cases),
        "n_match": n_match, "n_mismatch": n_mismatch,
        "counters_total": agg,
        "gate": "GO" if (all_match and agg == {"production_write": 0, "operating_store_write": 0,
                                               "opencrab_call": 0, "github_push": 0}) else "STOP",
        "cases": cases,
    }
    plan = {
        "loader": "openbinggu_incoming_to_staging.py", "version": "v0.11",
        "mode": "staging plan dry-run (NO graph write)", "blocked_by_v09": True,
        "production_graph_written": False,
        "n_safe_staging": len(staging_plan),
        "staging_candidates": staging_plan,
        "counters": agg,
    }
    SELFTEST_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SELFTEST_REPORT.write_text(json.dumps(selftest, ensure_ascii=False, indent=2), encoding="utf-8")
    STAGING_PLAN_REPORT.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 70)
    print("OpenBinggu v0.11 incoming→staging loader  (dry-run / selftest)")
    print("=" * 70)
    for c in cases:
        flag = "[PASS]" if c["match"] else "[FAIL]"
        print(f"  {flag} {c['fixture']:40s} expected={c['expected']:15s} actual={c['actual']}")
        if not c["match"]:
            for r in c["reasons"]:
                print("         !", r)
    print(f"\n  cases={len(cases)} match={n_match} mismatch={n_mismatch}")
    print(f"  counters(전부 0 의무): {agg}")
    print(f"  staging candidates(SAFE_STAGING): {len(staging_plan)}")
    print(f"  selftest → {SELFTEST_REPORT}")
    print(f"  plan     → {STAGING_PLAN_REPORT}")
    print(f"\n  GATE: {selftest['gate']}  (GO = 전 fixture 일치 + write counter 전부 0)")
    sys.exit(0 if selftest["gate"] == "GO" else 1)


def run_single(path):
    incoming = json.loads(Path(path).read_text(encoding="utf-8"))
    res = assess_incoming(incoming)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res["verdict"] in {"SAFE_STAGING", "REVIEW_REQUIRED", "REVIEW_ONLY"} else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    else:
        run_single(args[0])


if __name__ == "__main__":
    main()
