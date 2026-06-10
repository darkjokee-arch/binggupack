# -*- coding: utf-8 -*-
"""OpenBinggu v0.15 — decision preview → reviewed-plan PREVIEW (dry-run).

목적: v0.14 decision preview bucket 을 reviewed-plan PREVIEW 형식으로 정규화하고
      human approval gate 를 **설계만** 한다(열지 않음).
      이것은 PREVIEW 단계이며 reviewed_apply_plan(실제 apply) 단계가 아니다.

허용: decision bucket → reviewed_plan bucket 정규화 / approval gate 설계 / approval token 인식(preview only).
금지 (BLOCKED_BY_V09 유지): reviewed plan apply / resolver result apply / transactional apply /
  staging apply / production write / operating store write / production graph 생성 / OpenCrab / GitHub /
  scheduler / promotion_allowed 변경 / D9 / coverage·pattern 승격.
  유일한 write = reports/openbinggu_v015_*.json.

decision bucket → reviewed_plan bucket:
  APPROVED_PREVIEW    → approved_preview        (≠ reviewed apply / ≠ production write / ≠ promotion=true)
  REJECTED_PREVIEW    → rejected_preview
  HELD_REVIEW_ONLY    → held_preview
  NEEDS_MORE_EVIDENCE → needs_evidence_preview
  STOP                → blocked_or_invalid
  SKIPPED_BLOCKED     → skipped_blocked
  SKIPPED_EXCLUDED    → skipped_excluded

approval gate (설계만):
  approval token present → APPROVAL_TOKEN_RECOGNIZED_PREVIEW_ONLY (apply 없음)
  approval token missing → APPROVAL_REQUIRED_PREVIEW

CLI:
  python openbinggu_reviewed_plan_preview.py --selftest
  python openbinggu_reviewed_plan_preview.py <fixture.json>
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "openbinggu_v015_reviewed_plan"
PLAN_REPORT = BASE / "reports" / "openbinggu_v015_reviewed_plan_preview.json"
SELFTEST_REPORT = BASE / "reports" / "openbinggu_v015_selftest.json"

ACTION_MAP = {
    "APPROVED_PREVIEW": "approved_preview",
    "REJECTED_PREVIEW": "rejected_preview",
    "HELD_REVIEW_ONLY": "held_preview",
    "NEEDS_MORE_EVIDENCE": "needs_evidence_preview",
}
NON_ACTION_MAP = {
    "STOP": "blocked_or_invalid",
    "SKIPPED_BLOCKED": "skipped_blocked",
    "SKIPPED_EXCLUDED": "skipped_excluded",
}


def new_counters():
    return {"production_write": 0, "operating_store_write": 0, "opencrab_call": 0,
            "github_push": 0, "apply_calls": 0, "transaction_calls": 0,
            "promotion_allowed_changed": False}


def human_approval_gate_design():
    """설계만. 열지 않음(preview_only)."""
    return {
        "required": True,
        "gate_mode": "preview_only",
        "apply_allowed": False,
        "production_write_allowed": False,
        "required_user_go_token": "실제 reviewed apply GO",
        "blocked_by_v09_required": True,
        "approval_scope": ["review_id", "source_pack_id", "candidate_refs", "evidence_refs"],
        "approval_must_not_change": ["promotion_allowed_default", "BLOCKED_BY_V09", "production_write_allowed"],
    }


def assess(report):
    """fixture(items + approval_token?) → reviewed_plan_preview + non_action + gate + counters."""
    counters = new_counters()
    token_present = bool(report.get("approval_token"))
    gate_status = "APPROVAL_TOKEN_RECOGNIZED_PREVIEW_ONLY" if token_present else "APPROVAL_REQUIRED_PREVIEW"

    reviewed = {"approved_preview": [], "rejected_preview": [], "held_preview": [], "needs_evidence_preview": []}
    non_action = {"blocked_or_invalid": [], "skipped_blocked": [], "skipped_excluded": []}

    for item in report.get("items", []):
        bucket = item.get("source_decision_bucket")
        rid = item.get("review_id")
        src = item.get("source_pack_id")

        if bucket in ACTION_MAP:
            # action item: missing review_id → blocked_or_invalid (STOP)
            if not rid:
                non_action["blocked_or_invalid"].append({
                    "review_id": None, "source_pack_id": src,
                    "source_decision_bucket": "STOP",
                    "reviewed_plan_bucket": "blocked_or_invalid",
                    "reason_codes": item.get("reason_codes", []),
                    "human_summary": item.get("human_summary", "missing review_id"),
                    "apply_allowed": False, "production_write_allowed": False, "dry_run": True,
                })
                continue
            rp_bucket = ACTION_MAP[bucket]
            reviewed[rp_bucket].append({
                "review_id": rid,
                "source_pack_id": src,
                "source_decision_bucket": bucket,
                "reviewed_plan_bucket": rp_bucket,
                "candidate_refs": item.get("candidate_refs", {}),
                "evidence_refs": item.get("evidence_refs", []),
                "reason_codes": item.get("reason_codes", []),
                "human_summary": item.get("human_summary", ""),
                "approval_gate": {
                    "required": True,
                    "status": gate_status,           # token 유무 반영, 단 apply 없음
                    "apply_allowed": False,           # hard guard
                    "production_write_allowed": False,
                },
                "dry_run": True,
            })
        elif bucket in NON_ACTION_MAP:
            rp_bucket = NON_ACTION_MAP[bucket]
            non_action[rp_bucket].append({
                "review_id": rid,
                "source_pack_id": src,
                "source_decision_bucket": bucket,
                "reviewed_plan_bucket": rp_bucket,
                "reason_codes": item.get("reason_codes", []),
                "human_summary": item.get("human_summary", ""),
                "apply_allowed": False, "production_write_allowed": False, "dry_run": True,
            })
        else:
            # 알 수 없는 bucket → blocked_or_invalid
            non_action["blocked_or_invalid"].append({
                "review_id": rid, "source_pack_id": src,
                "source_decision_bucket": "STOP",
                "reviewed_plan_bucket": "blocked_or_invalid",
                "reason_codes": item.get("reason_codes", []),
                "human_summary": f"unknown source_decision_bucket: {bucket!r}",
                "apply_allowed": False, "production_write_allowed": False, "dry_run": True,
            })

    return {
        "reviewed_plan_preview": reviewed,
        "non_action": non_action,
        "human_approval_gate": human_approval_gate_design(),
        "approval_token_present": token_present,
        "approval_gate_status": gate_status,
        "counters": counters,
    }


def _counts(result):
    rp = result["reviewed_plan_preview"]
    na = result["non_action"]
    return {
        "approved_preview": len(rp["approved_preview"]),
        "rejected_preview": len(rp["rejected_preview"]),
        "held_preview": len(rp["held_preview"]),
        "needs_evidence_preview": len(rp["needs_evidence_preview"]),
        "blocked_or_invalid": len(na["blocked_or_invalid"]),
        "skipped_blocked": len(na["skipped_blocked"]),
        "skipped_excluded": len(na["skipped_excluded"]),
    }


def _all_action_items(result):
    rp = result["reviewed_plan_preview"]
    return rp["approved_preview"] + rp["rejected_preview"] + rp["held_preview"] + rp["needs_evidence_preview"]


def _refs_preserved(result, report):
    in_map = {it.get("review_id"): it for it in report.get("items", []) if it.get("review_id")}
    ev_ok = cand_ok = reason_ok = src_ok = True
    for it in _all_action_items(result):
        orig = in_map.get(it["review_id"])
        if not orig:
            continue
        if list(it["evidence_refs"]) != list(orig.get("evidence_refs", [])):
            ev_ok = False
        if it["candidate_refs"] != orig.get("candidate_refs", {}):
            cand_ok = False
        if list(it["reason_codes"]) != list(orig.get("reason_codes", [])):
            reason_ok = False
        if it["source_pack_id"] != orig.get("source_pack_id"):
            src_ok = False
    return ev_ok, cand_ok, reason_ok, src_ok


def run_selftest():
    if not FIXTURE_DIR.is_dir():
        print(f"[FAIL] fixture 디렉토리 없음: {FIXTURE_DIR}")
        sys.exit(1)
    fixtures = sorted(FIXTURE_DIR.glob("*.json"))
    cases, sample_plan = [], None
    n_match = n_mismatch = 0
    agg = new_counters()

    for fp in fixtures:
        try:
            report = json.loads(fp.read_text(encoding="utf-8"))
            exp = report.get("expected", {})
            result = assess(report)
            if sample_plan is None:
                sample_plan = result
            for k in ("production_write", "operating_store_write", "opencrab_call",
                      "github_push", "apply_calls", "transaction_calls"):
                agg[k] += result["counters"][k]
            counts = _counts(result)
            ev_ok, cand_ok, reason_ok, src_ok = _refs_preserved(result, report)
            gate_ok = all(it["approval_gate"]["apply_allowed"] is False and
                          it["approval_gate"]["production_write_allowed"] is False
                          for it in _all_action_items(result))

            checks = {}
            if "counts" in exp:
                checks["counts"] = all(counts.get(k, 0) == v for k, v in exp["counts"].items())
            if "approval_gate_status" in exp:
                checks["approval_gate_status"] = (result["approval_gate_status"] == exp["approval_gate_status"])
            if "evidence_preserved" in exp:
                checks["evidence_preserved"] = (ev_ok == exp["evidence_preserved"])
            if "candidate_preserved" in exp:
                checks["candidate_preserved"] = (cand_ok == exp["candidate_preserved"])
            if "reason_preserved" in exp:
                checks["reason_preserved"] = (reason_ok == exp["reason_preserved"])
            if "source_pack_preserved" in exp:
                checks["source_pack_preserved"] = (src_ok == exp["source_pack_preserved"])
            if "blocked_by_v09_required" in exp:
                checks["blocked_by_v09_required"] = (
                    result["human_approval_gate"]["blocked_by_v09_required"] == exp["blocked_by_v09_required"])
            # 공통 hard guard
            checks["gate_no_apply"] = gate_ok
            checks["no_apply_counters"] = (result["counters"]["apply_calls"] == 0 and
                                           result["counters"]["transaction_calls"] == 0)
            checks["promotion_not_changed"] = (result["counters"]["promotion_allowed_changed"] is False)
            ok = all(checks.values())
        except Exception as e:
            counts, checks, ok = {}, {"error": repr(e)}, False
        n_match += ok
        n_mismatch += (not ok)
        cases.append({"fixture": fp.name, "counts": counts, "checks": checks, "pass": ok})

    write0 = all(agg[k] == 0 for k in ("production_write", "operating_store_write", "opencrab_call",
                                       "github_push", "apply_calls", "transaction_calls"))
    all_pass = (n_mismatch == 0 and n_match > 0 and write0)

    plan_report = {
        "tool": "openbinggu_reviewed_plan_preview.py", "version": "v0.15",
        "mode": "reviewed-plan PREVIEW (dry-run, NOT reviewed_apply_plan)",
        "blocked_by_v09": True, "production_graph_written": False,
        "human_approval_gate": human_approval_gate_design(),
        "decision_to_reviewed_plan_map": {**ACTION_MAP, **NON_ACTION_MAP},
        "counters_total": agg,
        "apply_calls": agg["apply_calls"], "transaction_calls": agg["transaction_calls"],
        "promotion_allowed_changed": False,
        "sample_reviewed_plan_preview": _counts(sample_plan) if sample_plan else {},
    }
    selftest = {
        "tool": "openbinggu_reviewed_plan_preview.py", "version": "v0.15",
        "mode": "dry-run / selftest", "blocked_by_v09": True,
        "fixture_dir": str(FIXTURE_DIR), "n_cases": len(cases),
        "n_match": n_match, "n_mismatch": n_mismatch,
        "counters_total": agg,
        "gate": "GO" if all_pass else "STOP",
        "cases": cases,
    }
    PLAN_REPORT.parent.mkdir(parents=True, exist_ok=True)
    PLAN_REPORT.write_text(json.dumps(plan_report, ensure_ascii=False, indent=2), encoding="utf-8")
    SELFTEST_REPORT.write_text(json.dumps(selftest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 78)
    print("PREVIEW ONLY: no changes will be applied (apply/tx/write = 0)")
    print("OpenBinggu v0.15 reviewed-plan PREVIEW  (dry-run / selftest)")
    print("=" * 78)
    for c in cases:
        flag = "[PASS]" if c["pass"] else "[FAIL]"
        nz = {k: v for k, v in c["counts"].items() if v}
        print(f"  {flag} {c['fixture']:54s} {nz}")
        if not c["pass"]:
            for k, v in c["checks"].items():
                if v is False:
                    print(f"         ! check failed: {k}")
    print(f"\n  cases={len(cases)} match={n_match} mismatch={n_mismatch}")
    print(f"  counters: apply_calls={agg['apply_calls']} transaction_calls={agg['transaction_calls']} "
          f"(apply/tx 0 의무)")
    print(f"  plan report → {PLAN_REPORT}")
    print(f"  selftest    → {SELFTEST_REPORT}")
    print(f"\n  GATE: {selftest['gate']}  (GO = 전 fixture 일치 + apply/tx/write 0 + gate preview_only)")
    sys.exit(0 if all_pass else 1)


def run_single(path):
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    result = assess(report)
    result["counts"] = _counts(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        run_selftest()
    else:
        run_single(args[0])


if __name__ == "__main__":
    main()
