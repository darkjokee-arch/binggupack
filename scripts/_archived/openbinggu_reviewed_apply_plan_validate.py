# -*- coding: utf-8 -*-
"""OpenBinggu v0.16 — reviewed_apply_plan VALIDATOR (design/preview, dry-run).

목적: v0.15 reviewed_plan_preview 를 바탕으로, 향후 실제 reviewed apply 를 열기 전에
      필요한 reviewed_apply_plan 형식과 validator 를 **설계**한다.
      이것은 VALIDATOR 일 뿐이며 apply/transaction runner 가 아니다.
      v0.16 에서 모든 item executable=false / apply_allowed=false / production_write_allowed=false.

금지 (BLOCKED_BY_V09 유지, 해제 금지): reviewed apply 실행 / resolver result apply /
  transactional apply / staging apply / production write / operating store write /
  production graph 생성 / OpenCrab / GitHub / scheduler / promotion_allowed 변경 / D9 /
  coverage·pattern 승격 / BLOCKED_BY_V09 해제.
  실행 옵션(apply/write/execute/production)을 만들지 않는다. 유일한 write = reports/openbinggu_v016_*.json.

reviewed_plan_bucket → apply_plan_status:
  approved_preview + token missing        → NOT_APPROVED
  approved_preview + token + v09 유지      → BLOCKED_BY_V09
  approved_preview + token + (v09 해제가정) + apply_allowed=false → APPLY_GATE_CLOSED
  rejected_preview     → NOT_APPLICABLE_REJECTED
  held_preview         → NOT_APPLICABLE_HELD
  needs_evidence_preview → NOT_APPLICABLE_NEEDS_EVIDENCE
  blocked_or_invalid   → NOT_APPLICABLE_BLOCKED
  skipped_blocked      → NOT_APPLICABLE_SKIPPED_BLOCKED
  skipped_excluded     → NOT_APPLICABLE_SKIPPED_EXCLUDED
  (missing review_id   → STOP_MISSING_REVIEW_ID / missing refs → INCOMPLETE_*)

CLI: --selftest | <fixture.json>   (실행 옵션 없음)
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "openbinggu_v016_reviewed_apply_plan"
PLAN_REPORT = BASE / "reports" / "openbinggu_v016_apply_plan_preview.json"
SELFTEST_REPORT = BASE / "reports" / "openbinggu_v016_selftest.json"

APPROVED = "approved_preview"
NON_APPLICABLE = {
    "rejected_preview": "NOT_APPLICABLE_REJECTED",
    "held_preview": "NOT_APPLICABLE_HELD",
    "needs_evidence_preview": "NOT_APPLICABLE_NEEDS_EVIDENCE",
    "blocked_or_invalid": "NOT_APPLICABLE_BLOCKED",
    "skipped_blocked": "NOT_APPLICABLE_SKIPPED_BLOCKED",
    "skipped_excluded": "NOT_APPLICABLE_SKIPPED_EXCLUDED",
}
TOKEN_RECOGNIZED = "APPROVAL_TOKEN_RECOGNIZED_PREVIEW_ONLY"
TOKEN_REQUIRED = "APPROVAL_REQUIRED_PREVIEW"

# private path 노출 검사용 (public release safety) — 문자열 분리로 self-detect 회피
_PRIV = ["C:" + "\\", "C:" + "/", "/Users/", "/home/", "\\Users\\"]


def new_counters():
    return {"production_write": 0, "operating_store_write": 0, "opencrab_call": 0,
            "github_push": 0, "apply_calls": 0, "transaction_calls": 0,
            "promotion_allowed_changed": False, "blocked_by_v09_released": False}


def _has_private_path(item):
    blob = json.dumps(item, ensure_ascii=False)
    return any(p in blob for p in _PRIV)


def classify(item, blocked_by_v09):
    """reviewed_plan_preview item → apply_plan_status (항상 executable=false)."""
    bucket = item.get("source_reviewed_plan_bucket")
    rid = item.get("review_id")
    blockers = ["BLOCKED_BY_V09"] if blocked_by_v09 else []

    if bucket == APPROVED:
        if not rid:
            return "STOP_MISSING_REVIEW_ID", blockers + ["MISSING_REVIEW_ID"]
        if "candidate_refs" not in item:
            return "INCOMPLETE_MISSING_CANDIDATE_REFS", blockers + ["MISSING_CANDIDATE_REFS"]
        if "evidence_refs" not in item:
            return "INCOMPLETE_MISSING_EVIDENCE_REFS", blockers + ["MISSING_EVIDENCE_REFS"]
        tok_status = (item.get("approval", {}) or {}).get("token_status", TOKEN_REQUIRED)
        if tok_status != TOKEN_RECOGNIZED:
            return "NOT_APPROVED", blockers + ["APPROVAL_REQUIRED"]
        if blocked_by_v09:
            return "BLOCKED_BY_V09", ["BLOCKED_BY_V09"]
        # v09 해제 가정 시나리오에서도 apply_gate 가 닫혀 막힘
        return "APPLY_GATE_CLOSED", ["APPLY_GATE_CLOSED"]

    if bucket in NON_APPLICABLE:
        return NON_APPLICABLE[bucket], blockers + ["NOT_APPLICABLE"]

    return "STOP_MISSING_REVIEW_ID", blockers + [f"UNKNOWN_BUCKET:{bucket}"]


def build_apply_plan(report):
    """reviewed_plan_preview report → reviewed_apply_plan (전부 executable=false)."""
    counters = new_counters()
    blocked_by_v09 = report.get("blocked_by_v09", True)  # default 유지(true). 03 만 시나리오 false.
    items_out = []
    private_paths = []

    for it in report.get("items", []):
        status, blockers = classify(it, blocked_by_v09)
        if _has_private_path(it):
            private_paths.append(it.get("source_pack_id", "?"))
        items_out.append({
            "review_id": it.get("review_id"),
            "source_pack_id": it.get("source_pack_id"),
            "source_reviewed_plan_bucket": it.get("source_reviewed_plan_bucket"),
            "apply_plan_status": status,
            "executable": False,                 # hard guard — 항상 false
            "apply_allowed": False,
            "production_write_allowed": False,
            "candidate_refs": it.get("candidate_refs", {}),
            "evidence_refs": it.get("evidence_refs", []),
            "reason_codes": it.get("reason_codes", []),
            "approval": {"required": True,
                         "token_status": (it.get("approval", {}) or {}).get("token_status", TOKEN_REQUIRED)},
            "blockers": blockers,
            "dry_run": True,
        })

    candidate_count = sum(1 for i in items_out if i["source_reviewed_plan_bucket"] == APPROVED)
    blocked_count = sum(1 for i in items_out
                        if i["apply_plan_status"] in ("BLOCKED_BY_V09", "APPLY_GATE_CLOSED", "NOT_APPROVED"))
    not_applicable_count = sum(1 for i in items_out if i["apply_plan_status"].startswith("NOT_APPLICABLE"))

    plan = {
        "version": "0.16",
        "mode": "dry_run",
        "executable": False,
        "blocked_by_v09": True,                  # 전역은 항상 유지(03 의 시나리오 false 와 무관)
        "blocked_by_v09_released": False,
        "apply_allowed": False,
        "production_write_allowed": False,
        "source_reviewed_plan_preview_id": report.get("source_reviewed_plan_preview_id",
                                                      report.get("source_id", "unknown")),
        "summary": {
            "candidate_count": candidate_count,
            "executable_count": 0,
            "blocked_count": blocked_count,
            "not_applicable_count": not_applicable_count,
        },
        "items": items_out,
        "private_paths_detected": private_paths,
        "counters": counters,
    }
    return plan


def _forbidden_apply_options_absent():
    """validator 소스에 실행 옵션 리터럴이 없는지 self-check (문자열 분리로 self-detect 회피)."""
    try:
        src = Path(__file__).read_text(encoding="utf-8")
    except Exception:
        return True
    opts = ["--app" + "ly", "--wr" + "ite", "--exe" + "cute", "--pro" + "duction"]
    return not any(o in src for o in opts)


def run_selftest():
    if not FIXTURE_DIR.is_dir():
        print(f"[FAIL] fixture 디렉토리 없음: {FIXTURE_DIR}")
        sys.exit(1)
    fixtures = sorted(FIXTURE_DIR.glob("*.json"))
    cases, sample = [], None
    n_match = n_mismatch = 0
    agg = new_counters()
    opts_absent = _forbidden_apply_options_absent()

    for fp in fixtures:
        try:
            report = json.loads(fp.read_text(encoding="utf-8"))
            exp = report.get("expected", {})
            plan = build_apply_plan(report)
            if sample is None:
                sample = plan
            statuses = [i["apply_plan_status"] for i in plan["items"]]
            in_map = {i.get("review_id"): i for i in report.get("items", []) if i.get("review_id")}

            # 보존 검증
            ev_ok = cand_ok = reason_ok = src_ok = True
            for o in plan["items"]:
                orig = in_map.get(o["review_id"])
                if not orig:
                    continue
                if list(o["evidence_refs"]) != list(orig.get("evidence_refs", [])):
                    ev_ok = False
                if o["candidate_refs"] != orig.get("candidate_refs", {}):
                    cand_ok = False
                if list(o["reason_codes"]) != list(orig.get("reason_codes", [])):
                    reason_ok = False
                if o["source_pack_id"] != orig.get("source_pack_id"):
                    src_ok = False

            checks = {}
            if "statuses" in exp:
                checks["statuses"] = (statuses == exp["statuses"])
            if "status_contains" in exp:
                checks["status_contains"] = all(s in statuses for s in exp["status_contains"])
            if "evidence_preserved" in exp:
                checks["evidence_preserved"] = (ev_ok == exp["evidence_preserved"])
            if "candidate_preserved" in exp:
                checks["candidate_preserved"] = (cand_ok == exp["candidate_preserved"])
            if "reason_preserved" in exp:
                checks["reason_preserved"] = (reason_ok == exp["reason_preserved"])
            if "source_pack_preserved" in exp:
                checks["source_pack_preserved"] = (src_ok == exp["source_pack_preserved"])
            if "no_private_paths" in exp:
                checks["no_private_paths"] = ((len(plan["private_paths_detected"]) == 0) == exp["no_private_paths"])
            if "forbidden_apply_options_absent" in exp:
                checks["forbidden_apply_options_absent"] = (opts_absent == exp["forbidden_apply_options_absent"])
            # 공통 hard guard (모든 fixture)
            checks["executable_zero"] = (plan["summary"]["executable_count"] == 0 and
                                         all(i["executable"] is False for i in plan["items"]))
            checks["apply_allowed_zero"] = all(i["apply_allowed"] is False for i in plan["items"])
            checks["prod_write_zero"] = all(i["production_write_allowed"] is False for i in plan["items"])
            checks["v09_maintained"] = (plan["blocked_by_v09"] is True and plan["blocked_by_v09_released"] is False)
            checks["counters_zero"] = (plan["counters"]["apply_calls"] == 0 and
                                       plan["counters"]["transaction_calls"] == 0 and
                                       plan["counters"]["production_write"] == 0)
            checks["promotion_not_changed"] = (plan["counters"]["promotion_allowed_changed"] is False)
            ok = all(checks.values())
        except Exception as e:
            statuses, checks, ok = [], {"error": repr(e)}, False
        n_match += ok
        n_mismatch += (not ok)
        cases.append({"fixture": fp.name, "statuses": statuses, "checks": checks, "pass": ok})

    write0 = all(agg[k] == 0 for k in ("production_write", "operating_store_write", "opencrab_call",
                                       "github_push", "apply_calls", "transaction_calls"))
    all_pass = (n_mismatch == 0 and n_match > 0 and write0 and opts_absent)

    plan_report = {
        "tool": "openbinggu_reviewed_apply_plan_validate.py", "version": "v0.16",
        "mode": "reviewed_apply_plan DESIGN/VALIDATOR (dry-run, NOT a runner)",
        "blocked_by_v09": True, "blocked_by_v09_released": False, "production_graph_written": False,
        "forbidden_apply_options_absent": opts_absent,
        "apply_preflight_design": {
            "required_user_go_token": "실제 reviewed apply GO",
            "blocked_by_v09_must_be_released_by_user": True,
            "promotion_allowed_must_stay_false": True,
            "production_write_allowed_must_stay_false_until_separate_go": True,
        },
        "counters_total": agg,
        "sample_apply_plan": sample,
    }
    selftest = {
        "tool": "openbinggu_reviewed_apply_plan_validate.py", "version": "v0.16",
        "mode": "dry-run / selftest", "blocked_by_v09": True,
        "fixture_dir": str(FIXTURE_DIR), "n_cases": len(cases),
        "n_match": n_match, "n_mismatch": n_mismatch,
        "forbidden_apply_options_absent": opts_absent,
        "counters_total": agg,
        "gate": "GO" if all_pass else "STOP",
        "cases": cases,
    }
    PLAN_REPORT.parent.mkdir(parents=True, exist_ok=True)
    PLAN_REPORT.write_text(json.dumps(plan_report, ensure_ascii=False, indent=2), encoding="utf-8")
    SELFTEST_REPORT.write_text(json.dumps(selftest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 80)
    print("OpenBinggu v0.16 reviewed_apply_plan VALIDATOR  (dry-run / selftest, NOT a runner)")
    print("=" * 80)
    for c in cases:
        flag = "[PASS]" if c["pass"] else "[FAIL]"
        print(f"  {flag} {c['fixture']:54s} {c['statuses']}")
        if not c["pass"]:
            for k, v in c["checks"].items():
                if v is False:
                    print(f"         ! check failed: {k}")
    print(f"\n  cases={len(cases)} match={n_match} mismatch={n_mismatch}")
    print(f"  forbidden apply/write/execute/production options absent: {opts_absent}")
    print(f"  plan report → {PLAN_REPORT}")
    print(f"  selftest    → {SELFTEST_REPORT}")
    print(f"\n  GATE: {selftest['gate']}  (GO = 전 fixture 일치 + executable 0 + 실행옵션 부재 + v09 유지)")
    sys.exit(0 if all_pass else 1)


def run_single(path):
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    print(json.dumps(build_apply_plan(report), ensure_ascii=False, indent=2))
    sys.exit(0)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    else:
        run_single(args[0])


if __name__ == "__main__":
    main()
