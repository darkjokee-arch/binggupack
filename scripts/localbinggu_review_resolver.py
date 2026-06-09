# -*- coding: utf-8 -*-
"""LocalBinggu review resolver v0.8 (read-only, production write 금지).

review decision 을 apply_plan 에 반영해 reviewed plan + audit 을 생성.
모드: 실제(--apply-plan --decisions) / 테스트(--fixture-dir).
"""
import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
APPROVE = {"approve_safe_merge", "approve_insert"}
HOLD_DECISIONS = {"defer", "edit_required", ""}


def resolve(items, decisions):
    """items: [{review_id, kind, node}] (kind: review|d9_protected|cross_domain|insert)
    decisions: {review_id: decision}
    반환: audit(list), buckets, decision"""
    audit = []
    applied, excluded, held, stopped = [], [], [], []

    for it in items:
        rid = it["review_id"]
        kind = it.get("kind", "review")
        dec = decisions.get(rid, "")
        result, reason = None, None

        if dec in APPROVE:
            if kind == "d9_protected":
                result, reason = "STOPPED", "D9 protected 는 approve 불가 (보호 상태 변경 금지)"
                stopped.append(rid)
            elif kind == "cross_domain":
                result, reason = "STOPPED", "cross-domain rejected 는 approve 불가"
                stopped.append(rid)
            else:
                # safety: promotion_allowed=false 유지, evidence_refs 유지 가정(노드 자체 미변경)
                result, reason = "APPLIED", f"{dec} 승인 → apply candidate"
                applied.append(rid)
        elif dec == "reject":
            result, reason = "EXCLUDED", "reject → 적용 제외"
            excluded.append(rid)
        elif dec in HOLD_DECISIONS:
            result, reason = "HELD", ("미결정 → HOLD 유지" if dec == "" else f"{dec} → HOLD 유지")
            held.append(rid)
        else:
            result, reason = "HELD", f"알 수 없는 decision '{dec}' → HOLD 유지"
            held.append(rid)

        audit.append({"review_id": rid, "kind": kind, "node": it.get("node"),
                      "decision": dec, "result": result, "reason": reason,
                      "promotion_allowed": False})

    if stopped:
        decision, why = "STOP", f"approve 불가 항목 approve 시도: {stopped}"
    elif held:
        decision, why = "HOLD", f"미해결(defer/edit_required/미결정) 항목 잔존: {held}"
    else:
        decision, why = "GO", "review-only 전부 approve/reject 처리, 잔여 HOLD 없음, STOP 신호 없음"

    return audit, {"applied": applied, "excluded": excluded, "held": held, "stopped": stopped}, decision, why


def items_from_plan(plan):
    rw = plan.get("review_workflow", {})
    items = []
    for it in rw.get("review_items", []):
        items.append({"review_id": None, "kind": "review", "node": it.get("a"), "raw": it})
    for it in rw.get("d9_protected_items", []):
        items.append({"review_id": None, "kind": "d9_protected", "node": it.get("a"), "raw": it})
    for it in rw.get("cross_domain_items", []):
        items.append({"review_id": None, "kind": "cross_domain", "node": it.get("a"), "raw": it})
    # review_id 부여 (review-only 만 REV-, 나머지는 참고용 id)
    rev = 1
    for it in items:
        if it["kind"] == "review":
            it["review_id"] = f"REV-{rev:03d}"; rev += 1
        else:
            it["review_id"] = f"{it['kind'].upper()}-{it['node']}"
    return items


def load_decisions(path):
    d = {}
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                o = json.loads(line)
                d[o["review_id"]] = o.get("decision", "")
    return d


def write_reports(name, audit, buckets, decision, why, extra=None):
    reports = BASE / "reports"
    reports.mkdir(exist_ok=True)
    reviewed = {"stage": "reviewed_apply_plan", "production_write": False,
                "decision": decision, "reason": why, "buckets": buckets, "audit": audit}
    if extra:
        reviewed.update(extra)
    (reports / f"{name}.json").write_text(json.dumps(reviewed, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# {name}\n", f"- decision: **{decision}** — {why}", f"- production_write: False\n", "## audit"]
    for a in audit:
        md.append(f"- [{a['result']}] {a['review_id']} ({a['kind']}) decision={a['decision'] or '없음'}: {a['reason']}")
    (reports / f"{name}.md").write_text("\n".join(md), encoding="utf-8")
    return reviewed


def run_fixtures(fixture_dir):
    results = []
    for case in sorted(p for p in Path(fixture_dir).iterdir() if p.is_dir()):
        cfg = json.loads((case / "config.json").read_text(encoding="utf-8"))
        items = cfg["items"]
        decisions = {d["review_id"]: d.get("decision", "") for d in cfg.get("decisions", [])}
        audit, buckets, decision, why = resolve(items, decisions)
        exp = cfg.get("expected_decision")
        results.append({"case": case.name, "decision": decision, "expected": exp,
                        "pass": decision == exp, "buckets": buckets})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply-plan", default=None)
    ap.add_argument("--decisions", default=None)
    ap.add_argument("--fixture-dir", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.fixture_dir:
        fdir = (BASE / args.fixture_dir) if not Path(args.fixture_dir).is_absolute() else Path(args.fixture_dir)
        results = run_fixtures(fdir)
        print("=" * 60)
        print("LocalBinggu review resolver v0.8 — fixtures (dry-run, write 0)")
        print("=" * 60)
        allp = True
        for r in results:
            allp = allp and r["pass"]
            print(f"  [{'PASS' if r['pass'] else 'FAIL'}] {r['case']}: {r['decision']} (expected {r['expected']})")
        # 리포트 저장
        (BASE / "reports").mkdir(exist_ok=True)
        (BASE / "reports" / "localbinggu_review_fixture_result.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nfixtures: {'ALL PASS' if allp else 'FAIL 있음'}")
        import sys
        sys.exit(0 if allp else 1)

    if args.apply_plan and args.decisions:
        plan = json.loads(((BASE / args.apply_plan) if not Path(args.apply_plan).is_absolute()
                           else Path(args.apply_plan)).read_text(encoding="utf-8"))
        items = items_from_plan(plan)
        dec_path = (BASE / args.decisions) if not Path(args.decisions).is_absolute() else Path(args.decisions)
        decisions = load_decisions(dec_path)
        audit, buckets, decision, why = resolve(items, decisions)
        write_reports("localbinggu_apply_plan.reviewed", audit, buckets, decision, why,
                      extra={"original_decision": plan.get("decision")})
        write_reports("localbinggu_review_audit", audit, buckets, decision, why)
        print("=" * 60)
        print(f"LocalBinggu review resolver v0.8 (dry-run={args.dry_run}, production_write=False)")
        print("=" * 60)
        print(f"review items: {len(items)} (review-only {sum(1 for i in items if i['kind']=='review')})")
        for a in audit:
            print(f"  [{a['result']}] {a['review_id']} decision={a['decision'] or '없음'}")
        print(f"\nbuckets: {buckets}")
        print(f"Decision: {decision} — {why}")
        print(f"reports: localbinggu_apply_plan.reviewed.json/.md + localbinggu_review_audit.json/.md")
        return

    print("usage: --apply-plan <json> --decisions <jsonl>  또는  --fixture-dir <dir>")


if __name__ == "__main__":
    main()
