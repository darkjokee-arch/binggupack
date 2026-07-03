# -*- coding: utf-8 -*-
"""LocalBinggu review resolver v0.8 (read-only, production write 금지 · backward-compatible wrapper).

review decision 을 apply_plan 에 반영해 reviewed plan + audit 을 생성.
모드: 실제(--apply-plan --decisions) / 테스트(--fixture-dir) / 순수검증(--selftest).

strangler: 순수 라우팅(resolve·items_from_plan + APPROVE/HOLD_DECISIONS)은
binggupack.review.resolver 로 byte-identical 이관됐고, 이 파일은 그를 re-export 하며
파일 I/O(load_decisions·write_reports·run_fixtures·CLI·BASE/reports 경로)를 잔류시킨
wrapper 다. 이관 시 순수 라우팅 _selftest 를 신설해 --selftest 로 검증수단을 부여했다.
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.review.resolver import *  # noqa: E402,F401,F403
from binggupack.review.resolver import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    APPROVE,
    HOLD_DECISIONS,
    resolve,
    items_from_plan,
    _selftest,
)

BASE = Path(__file__).resolve().parent.parent


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
    md = [f"# {name}\n", f"- decision: **{decision}** — {why}", "- production_write: False\n", "## audit"]
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
    ap.add_argument("--selftest", action="store_true")   # 순수 라우팅 인메모리 검증(이관 신설)
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest())

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
        print("reports: localbinggu_apply_plan.reviewed.json/.md + localbinggu_review_audit.json/.md")
        return

    print("usage: --apply-plan <json> --decisions <jsonl>  또는  --fixture-dir <dir>  또는  --selftest")


if __name__ == "__main__":
    main()
