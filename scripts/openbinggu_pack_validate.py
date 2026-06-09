# -*- coding: utf-8 -*-
"""OpenBinggu minimum pack contract validator v0.10 — dry-run only.

목적: pack 경계(ontology 칸)를 확정하는 것이 아니라, "나쁜 pack 이 들어오는 것만 막는
      최소 안전 gate". session/evidence/candidate pack 이 나중에 안전하게 쌓이도록
      최소 계약과 validator 만 만든다.

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
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "openbinggu_pack_contract"
REPORT_PATH = BASE / "reports" / "openbinggu_pack_contract_selftest.json"

REQUIRED_FIELDS = [
    "pack_id", "pack_type", "scope", "depends_on", "evidence_policy",
    "merge_policy", "promotion_allowed_default", "status",
    "cross_pack_tags", "risk_level", "created_from",
]
PACK_TYPE_ALLOWED = {
    "seed", "session", "evidence", "candidate", "review", "audit", "runtime", "synthetic_fixture",
}
STATUS_ALLOWED = {"draft", "staged", "validated", "review_required", "archived", "rejected"}
RISK_ALLOWED = {"low", "medium", "high", "unknown"}
MERGE_MODE_ALLOWED = {"manual", "auto", "review"}
MERGE_TARGET_ALLOWED = {"staging", "candidate", "review", "production"}
CROSS_PACK_ALLOWED = {"isolated", "review_only", "fuzzy"}

# pack_type 중 production target 으로 직접 가면 안 되는 타입 (rule 4)
NON_PRODUCTION_PACK_TYPES = {"session", "candidate", "evidence"}
# hard-default 플래그 — 존재하면 반드시 false (rule: hard_defaults)
HARD_FALSE_FLAGS = ["production_write_allowed", "opencrab_ingest_allowed", "github_publish_allowed"]


def validate_pack(pack):
    """단일 pack dict → {verdict, stops:[...], reviews:[...], notes:[...]} (read-only)."""
    stops, reviews, notes = [], [], []

    if not isinstance(pack, dict):
        return {"verdict": "STOP", "stops": ["pack 이 object(dict) 가 아님"], "reviews": [], "notes": []}

    # rule 1: required field 누락
    missing = [f for f in REQUIRED_FIELDS if f not in pack]
    if missing:
        stops.append(f"required field 누락: {missing}")

    # enum 검증 (rule 6 — 허용값 밖이면 STOP)
    pt = pack.get("pack_type")
    if pt is not None and pt not in PACK_TYPE_ALLOWED:
        stops.append(f"pack_type 비허용값: {pt!r}")
    st = pack.get("status")
    if st is not None and st not in STATUS_ALLOWED:
        stops.append(f"status 비허용값: {st!r}")
    rl = pack.get("risk_level")
    if rl is not None and rl not in RISK_ALLOWED:
        stops.append(f"risk_level 비허용값: {rl!r}")

    mp = pack.get("merge_policy")
    mode = target = cross = None
    if not isinstance(mp, dict):
        if "merge_policy" not in missing:
            stops.append("merge_policy 가 object 아님")
    else:
        mode, target, cross = mp.get("mode"), mp.get("target"), mp.get("cross_pack")
        if mode not in MERGE_MODE_ALLOWED:
            stops.append(f"merge_policy.mode 비허용값: {mode!r}")
        if target not in MERGE_TARGET_ALLOWED:
            stops.append(f"merge_policy.target 비허용값: {target!r}")
        if cross not in CROSS_PACK_ALLOWED:
            stops.append(f"merge_policy.cross_pack 비허용값: {cross!r}")

    # rule 2: promotion_allowed_default 는 반드시 false
    pad = pack.get("promotion_allowed_default")
    if "promotion_allowed_default" not in missing and pad is not False:
        stops.append(f"promotion_allowed_default 는 false 강제(현재 {pad!r})")

    # hard_defaults: production_write/opencrab_ingest/github_publish 는 true 면 STOP
    for flag in HARD_FALSE_FLAGS:
        if pack.get(flag) is True:
            stops.append(f"hard-default 위반: {flag}=true (BLOCKED_BY_V09)")

    # rule 7: 새 주제를 9도메인에 강제배정하는 forced_domain 금지
    if "forced_domain" in pack:
        stops.append("forced_domain 금지(9도메인 강제배정) — scope/cross_pack_tags 로만 표현")

    # rule 3: risk_level high|unknown 이면 자동 merge 금지
    if rl in {"high", "unknown"} and mode == "auto":
        stops.append(f"risk_level={rl} + merge_policy.mode=auto → 자동 merge 금지")

    # rule 4: session|candidate|evidence pack 은 production target 금지
    if pt in NON_PRODUCTION_PACK_TYPES and target == "production":
        stops.append(f"pack_type={pt} 는 production target 금지(merge_policy.target=production)")

    # rule 5: depends_on 은 형식(존재)만 확인, 의미론적 merge 안 함
    dep = pack.get("depends_on")
    if "depends_on" not in missing:
        if not isinstance(dep, list) or any(not isinstance(d, str) for d in dep):
            stops.append("depends_on 은 string 배열이어야 함")
        elif dep:
            notes.append(f"depends_on={dep} — 존재 형식만 확인(의미론적 merge 미실행)")

    # rule 6 / cross-pack: cross_pack_tags 가 있고 fuzzy merge 면 review-only
    cpt = pack.get("cross_pack_tags")
    if isinstance(cpt, list) and cpt and cross == "fuzzy":
        reviews.append(f"cross_pack_tags={cpt} + cross_pack=fuzzy → cross-pack fuzzy merge 는 REVIEW_ONLY")

    if stops:
        verdict = "STOP"
    elif reviews:
        verdict = "REVIEW_ONLY"
    else:
        verdict = "PASS"
    return {"verdict": verdict, "stops": stops, "reviews": reviews, "notes": notes}


def _expected_from_name(name):
    """fixture 파일명 prefix → 기대 verdict."""
    low = name.lower()
    if low.startswith("pass_"):
        return "PASS"
    if low.startswith("stop_"):
        return "STOP"
    if low.startswith("review_"):
        return "REVIEW_ONLY"
    return None


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
