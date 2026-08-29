# -*- coding: utf-8 -*-
"""OpenBinggu minimum pack contract validator v0.10 — dry-run only (thin wrapper).

v1.16 strangler Phase2: pack 계약 validator 의 enum 상수 + validate_pack/_expected_from_name
정본 transform 로직은 binggupack.pack.contract_validate 로 이관됐고, 이 파일은 공개 심볼이
byte-identical 한 backward-compatible thin wrapper 다. 기존 호출처(import openbinggu_pack_validate
as PV/pv/v010 등 bare-name import → PV.validate_pack)는 그대로 동작한다. 순수 함수(production
write 0·graph 생성 0·LLM 0·멱등).

__file__ 경로상수(BASE/FIXTURE_DIR/REPORT_PATH)·run_selftest(report write)·run_single·CLI·
__main__ 은 scripts/ 진입점·report 디렉토리 의존이므로 이 wrapper 에 잔류한다.

전제/금지 (BLOCKED_BY_V09 유지):
  - production write X / 운영 store(_graph_merge.yaml·user_graph.yaml·localcrab_index.sqlite) write X
  - localbinggu_production_graph.yaml 생성 X
  - OpenCrab 호출 X / GitHub push X
  - reingest_pack_draft 원본 수정 X / scheduler 수정 X
  - promotion_allowed 변경 X / D9 상태 변경 X / coverage·pattern 승격 X
  본 validator 는 pack dict 를 읽어 verdict 만 판정한다(graph 생성 안 함).
  유일한 write = selftest report JSON (reports/) — production graph 아님.

verdict: PASS / REVIEW_ONLY / STOP
  - STOP : 안전 위반(하나라도). 자동 merge·승격 금지.
  - REVIEW_ONLY : 형식은 통과하나 cross-pack fuzzy 등 사람이 봐야 하는 경우.
  - PASS : 최소 계약 충족(그래도 production write 는 v0.9 정책으로 별도 차단).

CLI:
  python openbinggu_pack_validate.py --selftest          # fixtures 전수 검증 + report 생성
  python openbinggu_pack_validate.py <pack.json>         # 단일 pack dry-run
"""
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.pack.contract_validate import (  # noqa: E402,F401  (전체 명시 re-export)
    REQUIRED_FIELDS,
    PACK_TYPE_ALLOWED,
    STATUS_ALLOWED,
    RISK_ALLOWED,
    MERGE_MODE_ALLOWED,
    MERGE_TARGET_ALLOWED,
    CROSS_PACK_ALLOWED,
    NON_PRODUCTION_PACK_TYPES,
    HARD_FALSE_FLAGS,
    validate_pack,
    _expected_from_name,
)

__all__ = (
    'REQUIRED_FIELDS',
    'PACK_TYPE_ALLOWED',
    'STATUS_ALLOWED',
    'RISK_ALLOWED',
    'MERGE_MODE_ALLOWED',
    'MERGE_TARGET_ALLOWED',
    'CROSS_PACK_ALLOWED',
    'NON_PRODUCTION_PACK_TYPES',
    'HARD_FALSE_FLAGS',
    'validate_pack',
    '_expected_from_name',
)

BASE = Path(__file__).resolve().parent.parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "openbinggu_pack_contract"
REPORT_PATH = BASE / "reports" / "openbinggu_pack_contract_selftest.json"


def run_selftest():
    if not FIXTURE_DIR.is_dir():
        print(f"[FAIL] fixture 디렉토리 없음: {FIXTURE_DIR}")
        sys.exit(1)
    fixtures = sorted(FIXTURE_DIR.glob("*.json"))
    cases, n_pass, n_fail = [], 0, 0
    for fp in fixtures:
        expected = _expected_from_name(fp.name)
        try:
            pack = json.loads(fp.read_text(encoding="utf-8"))
            res = validate_pack(pack)
        except Exception as e:
            res = {"verdict": "ERROR", "stops": [repr(e)], "reviews": [], "notes": []}
        ok = (res["verdict"] == expected)
        n_pass += ok
        n_fail += (not ok)
        cases.append({
            "fixture": fp.name,
            "expected": expected,
            "actual": res["verdict"],
            "match": ok,
            "stops": res["stops"],
            "reviews": res["reviews"],
            "notes": res["notes"],
        })

    all_match = (n_fail == 0 and n_pass > 0)
    report = {
        "validator": "openbinggu_pack_validate.py",
        "version": "v0.10",
        "mode": "dry-run / selftest",
        "blocked_by_v09": True,
        "production_write": 0,
        "fixture_dir": str(FIXTURE_DIR),
        "n_cases": len(cases),
        "n_match": n_pass,
        "n_mismatch": n_fail,
        "gate": "GO" if all_match else "STOP",
        "cases": cases,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 66)
    print("OpenBinggu PACK CONTRACT validator v0.10  (dry-run / selftest)")
    print("=" * 66)
    for c in cases:
        flag = "[PASS]" if c["match"] else "[FAIL]"
        print(f"  {flag} {c['fixture']:42s} expected={c['expected']:11s} actual={c['actual']}")
        if not c["match"]:
            for s in c["stops"] + c["reviews"]:
                print("         !", s)
    print(f"\n  cases={len(cases)} match={n_pass} mismatch={n_fail}")
    print(f"  report → {REPORT_PATH}")
    print(f"\n  GATE: {report['gate']}  (v0.10 GO = 전 fixture 기대 verdict 일치)")
    sys.exit(0 if all_match else 1)


def run_single(path):
    pack = json.loads(Path(path).read_text(encoding="utf-8"))
    res = validate_pack(pack)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res["verdict"] in {"PASS", "REVIEW_ONLY"} else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    else:
        run_single(args[0])


if __name__ == "__main__":
    main()
