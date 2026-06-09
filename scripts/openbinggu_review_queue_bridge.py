# -*- coding: utf-8 -*-
"""OpenBinggu v0.12 — review queue bridge (dry-run only).

목적: v0.11 staging_plan 의 REVIEW_REQUIRED / REVIEW_ONLY 항목을
      v0.8 review workflow(localbinggu_review_resolver) 가 받을 수 있는
      review queue draft/preview 로 변환한다.

규칙:
  - SAFE_STAGING : review queue 에 넣지 않음(excluded count 만).
  - REVIEW_REQUIRED : review queue 에 넣음.
  - REVIEW_ONLY : review queue 에 넣음.
  - STOP : review queue 가 아니라 blocked section 에 넣음.
  - 각 항목의 reason_codes / source_pack_id / candidate_refs / evidence_refs 보존.
  - v0.8 resolver.resolve() 의 items 스키마 {review_id, kind, node} 와 호환되는 preview 생성.

금지 (BLOCKED_BY_V09 유지):
  staging apply / production write / operating store write(_graph_merge·user_graph·localcrab_index) /
  localbinggu_production_graph.yaml 생성 / OpenCrab 호출 / GitHub push·repo 생성 /
  promotion_allowed 변경 / D9 상태 변경 / coverage·pattern 승격 / resolver·apply 호출.
  유일한 write = reports/openbinggu_v012_*.json (dry-run preview).

CLI:
  python openbinggu_review_queue_bridge.py --selftest        # fixtures 전수 + report
  python openbinggu_review_queue_bridge.py <staging_plan.json>
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
FIXTURE_DIR = BASE / "tests" / "fixtures" / "openbinggu_v012_review_queue"
QUEUE_REPORT = BASE / "reports" / "openbinggu_v012_review_queue.json"
SELFTEST_REPORT = BASE / "reports" / "openbinggu_v012_selftest.json"

IN_QUEUE = {"REVIEW_REQUIRED", "REVIEW_ONLY"}
ALLOWED_DECISIONS = ["approve_safe_merge", "reject", "keep_review_only", "request_more_evidence"]

# verdict → v0.8 resolver kind 매핑 (resolver 미호출, 포맷 호환 preview 용)
#   REVIEW_REQUIRED/REVIEW_ONLY 모두 resolver 의 'review' kind(approve/reject 가능)로 매핑.
RESOLVER_KIND = "review"


def _candidate_refs(item):
    cr = item.get("candidate_refs") or {}
    return {"nodes": list(cr.get("nodes", [])), "edges": list(cr.get("edges", []))}


def _summary(item):
    if item.get("human_summary"):
        return item["human_summary"]
    rc = ", ".join(item.get("reason_codes", [])) or item.get("verdict", "")
    return f"{item.get('source_pack_id', '?')}: {rc}"


def bridge(plan):
    """staging_plan dict → {review_queue, blocked, excluded_safe_staging, v08_preview, counters}."""
    counters = {"production_write": 0, "operating_store_write": 0, "opencrab_call": 0,
                "github_push": 0, "resolver_calls": 0, "apply_calls": 0}
    plan_id = plan.get("source_staging_plan_id", "unknown_plan")
    items = plan.get("items", []) if isinstance(plan, dict) else []

    review_queue, blocked, resolver_preview = [], [], []
    n_excluded = 0
    rev_idx = 1

    for item in items:
        verdict = item.get("verdict")
        src_pack = item.get("source_pack_id", "?")

        if verdict == "SAFE_STAGING":
            n_excluded += 1
            continue

        if verdict == "STOP":
            blocked.append({
                "source_pack_id": src_pack,
                "source_staging_plan_id": plan_id,
                "classification": "STOP",
                "reason_codes": list(item.get("reason_codes", [])),
                "human_summary": _summary(item),
                "candidate_refs": _candidate_refs(item),
                "evidence_refs": list(item.get("evidence_refs", [])),
                "blocked_from_review_queue": True,
                "dry_run": True,
            })
            continue

        if verdict in IN_QUEUE:
            review_id = f"rev_{plan_id}_{rev_idx:03d}"
            rev_idx += 1
            ri = {
                "review_id": review_id,
                "source_pack_id": src_pack,
                "source_staging_plan_id": plan_id,
                "classification": verdict,
                "reason_codes": list(item.get("reason_codes", [])),
                "human_summary": _summary(item),
                "candidate_refs": _candidate_refs(item),
                "evidence_refs": list(item.get("evidence_refs", [])),
                "risk_level": item.get("risk_level", "unknown"),
                "cross_pack_tags": list(item.get("cross_pack_tags", [])),
                "recommended_action": "review_required",
                "allowed_decisions": list(ALLOWED_DECISIONS),
                "dry_run": True,
            }
            review_queue.append(ri)
            # v0.8 resolver items 호환 preview (resolver 미호출)
            node = (ri["candidate_refs"]["nodes"] or [src_pack])[0]
            resolver_preview.append({"review_id": review_id, "kind": RESOLVER_KIND, "node": node})
        # 그 외 알 수 없는 verdict 는 무시(보수적: queue/blocked 어디에도 안 넣음)

    v08_preview = {
        "resolver_compatible": True,
        "resolver_target": "localbinggu_review_resolver.resolve()",
        "note": "items 스키마 {review_id, kind, node} 와 호환. resolver/apply 미호출(dry-run).",
        "items": resolver_preview,
    }
    return {
        "source_staging_plan_id": plan_id,
        "review_queue": review_queue,
        "blocked": blocked,
        "excluded_safe_staging": n_excluded,
        "v08_review_workflow_preview": v08_preview,
        "counters": counters,
    }


def _zero_counters(c):
    return all(c.get(k, 0) == 0 for k in
               ["production_write", "operating_store_write", "opencrab_call", "github_push",
                "resolver_calls", "apply_calls"])


def _evidence_preserved(result, plan):
    """입력 REVIEW 항목의 evidence_refs 가 review_queue 에 그대로 보존됐는지."""
    in_ev = {it["source_pack_id"]: it.get("evidence_refs", [])
             for it in plan.get("items", []) if it.get("verdict") in IN_QUEUE}
    for ri in result["review_queue"]:
        exp = in_ev.get(ri["source_pack_id"], [])
        if list(ri["evidence_refs"]) != list(exp):
            return False
    return True


def run_selftest():
    if not FIXTURE_DIR.is_dir():
        print(f"[FAIL] fixture 디렉토리 없음: {FIXTURE_DIR}")
        sys.exit(1)
    fixtures = sorted(FIXTURE_DIR.glob("*.json"))
    cases, all_queue = [], []
    n_match = n_mismatch = 0

    for fp in fixtures:
        try:
            plan = json.loads(fp.read_text(encoding="utf-8"))
            exp = plan.get("expected", {})
            res = bridge(plan)
            actual = {
                "queue": len(res["review_queue"]),
                "blocked": len(res["blocked"]),
                "excluded": res["excluded_safe_staging"],
            }
            ev_ok = _evidence_preserved(res, plan)
            checks = {
                "queue": (actual["queue"] == exp.get("queue")) if "queue" in exp else True,
                "blocked": (actual["blocked"] == exp.get("blocked")) if "blocked" in exp else True,
                "excluded": (actual["excluded"] == exp.get("excluded")) if "excluded" in exp else True,
                "evidence_preserved": (ev_ok == exp.get("evidence_preserved")) if "evidence_preserved" in exp else True,
                "zero_counters": _zero_counters(res["counters"]),
            }
            ok = all(checks.values())
        except Exception as e:
            actual, checks, ok = {"error": repr(e)}, {}, False
            res = {"review_queue": []}
        n_match += ok
        n_mismatch += (not ok)
        cases.append({"fixture": fp.name, "expected": plan.get("expected", {}) if isinstance(plan, dict) else {},
                      "actual": actual, "checks": checks, "pass": ok})
        all_queue.extend(res.get("review_queue", []))

    all_pass = (n_mismatch == 0 and n_match > 0)

    queue_report = {
        "bridge": "openbinggu_review_queue_bridge.py", "version": "v0.12",
        "mode": "review queue bridge dry-run (NO apply / NO graph write)",
        "blocked_by_v09": True, "production_graph_written": False,
        "n_review_items_total": len(all_queue),
        "review_queue_sample": all_queue[:5],
        "counters": {"production_write": 0, "operating_store_write": 0, "opencrab_call": 0,
                     "github_push": 0, "resolver_calls": 0, "apply_calls": 0},
    }
    selftest = {
        "bridge": "openbinggu_review_queue_bridge.py", "version": "v0.12",
        "mode": "dry-run / selftest", "blocked_by_v09": True,
        "fixture_dir": str(FIXTURE_DIR), "n_cases": len(cases),
        "n_match": n_match, "n_mismatch": n_mismatch,
        "gate": "GO" if all_pass else "STOP",
        "cases": cases,
    }
    SELFTEST_REPORT.parent.mkdir(parents=True, exist_ok=True)
    SELFTEST_REPORT.write_text(json.dumps(selftest, ensure_ascii=False, indent=2), encoding="utf-8")
    QUEUE_REPORT.write_text(json.dumps(queue_report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 72)
    print("OpenBinggu v0.12 review queue bridge  (dry-run / selftest)")
    print("=" * 72)
    for c in cases:
        flag = "[PASS]" if c["pass"] else "[FAIL]"
        print(f"  {flag} {c['fixture']:48s} actual={c['actual']}")
        if not c["pass"]:
            for k, v in c["checks"].items():
                if not v:
                    print(f"         ! check failed: {k}")
    print(f"\n  cases={len(cases)} match={n_match} mismatch={n_mismatch}")
    print(f"  review items total: {len(all_queue)}")
    print(f"  queue report → {QUEUE_REPORT}")
    print(f"  selftest     → {SELFTEST_REPORT}")
    print(f"\n  GATE: {selftest['gate']}  (GO = 전 fixture 기대 일치 + write/resolver counter 전부 0)")
    sys.exit(0 if all_pass else 1)


def run_single(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    res = bridge(plan)
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
