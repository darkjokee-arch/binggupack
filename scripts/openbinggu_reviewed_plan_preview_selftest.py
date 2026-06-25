# -*- coding: utf-8 -*-
"""Characterization selftest — openbinggu_reviewed_plan_preview (v1.11.0 phase3 저위험 모듈).

목적: 이관(scripts → binggupack.review) 전후 공개 동작 byte-identical 보장.
결정론적·외부 비의존(fixture 디렉토리/파일 write 미사용·in-memory report dict 만 사용).
wrapper import(PYTHONPATH=scripts) 와 package import(binggupack.review) 양형태 모두 검증.

  python scripts/openbinggu_reviewed_plan_preview_selftest.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>


def _load_both_forms():
    """wrapper(sibling) 과 package import 양형태를 모두 로드해 동일 객체 계열인지 확인."""
    # package import
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from binggupack.review import reviewed_plan_preview as pkg  # noqa: E402

    # wrapper import (sibling, scripts on path)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import openbinggu_reviewed_plan_preview as wrap  # noqa: E402
    return pkg, wrap


def _cases():
    """(label, report, expected_partial) 결정론 케이스. fixture 파일 비의존."""
    return [
        ("approved_no_token", {
            "items": [{
                "source_decision_bucket": "APPROVED_PREVIEW", "review_id": "rv-1",
                "source_pack_id": "pk-1", "candidate_refs": {"c": 1},
                "evidence_refs": ["e1"], "reason_codes": ["R_OK"], "human_summary": "ok",
            }],
        }, {
            "counts": {"approved_preview": 1},
            "approval_gate_status": "APPROVAL_REQUIRED_PREVIEW",
        }),
        ("approved_with_token", {
            "approval_token": "GO",
            "items": [{
                "source_decision_bucket": "APPROVED_PREVIEW", "review_id": "rv-2",
                "source_pack_id": "pk-2", "candidate_refs": {}, "evidence_refs": [],
                "reason_codes": [], "human_summary": "",
            }],
        }, {
            "counts": {"approved_preview": 1},
            "approval_gate_status": "APPROVAL_TOKEN_RECOGNIZED_PREVIEW_ONLY",
        }),
        ("rejected", {
            "items": [{"source_decision_bucket": "REJECTED_PREVIEW", "review_id": "rv-3",
                       "source_pack_id": "pk-3"}],
        }, {"counts": {"rejected_preview": 1}}),
        ("held", {
            "items": [{"source_decision_bucket": "HELD_REVIEW_ONLY", "review_id": "rv-4",
                       "source_pack_id": "pk-4"}],
        }, {"counts": {"held_preview": 1}}),
        ("needs_evidence", {
            "items": [{"source_decision_bucket": "NEEDS_MORE_EVIDENCE", "review_id": "rv-5",
                       "source_pack_id": "pk-5"}],
        }, {"counts": {"needs_evidence_preview": 1}}),
        ("stop_to_blocked", {
            "items": [{"source_decision_bucket": "STOP", "review_id": "rv-6",
                       "source_pack_id": "pk-6"}],
        }, {"counts": {"blocked_or_invalid": 1}}),
        ("skipped_blocked", {
            "items": [{"source_decision_bucket": "SKIPPED_BLOCKED", "review_id": "rv-7",
                       "source_pack_id": "pk-7"}],
        }, {"counts": {"skipped_blocked": 1}}),
        ("skipped_excluded", {
            "items": [{"source_decision_bucket": "SKIPPED_EXCLUDED", "review_id": "rv-8",
                       "source_pack_id": "pk-8"}],
        }, {"counts": {"skipped_excluded": 1}}),
        ("action_missing_review_id_to_blocked", {
            "items": [{"source_decision_bucket": "APPROVED_PREVIEW", "review_id": None,
                       "source_pack_id": "pk-9"}],
        }, {"counts": {"approved_preview": 0, "blocked_or_invalid": 1}}),
        ("unknown_bucket_to_blocked", {
            "items": [{"source_decision_bucket": "WHATEVER", "review_id": "rv-10",
                       "source_pack_id": "pk-10"}],
        }, {"counts": {"blocked_or_invalid": 1}}),
    ]


def _check(mod):
    n_ok = n_fail = 0
    fails = []
    for label, report, exp in _cases():
        result = mod.assess(report)
        counts = mod._counts(result)
        ok = True
        for k, v in exp.get("counts", {}).items():
            if counts.get(k, 0) != v:
                ok = False
        if "approval_gate_status" in exp:
            if result["approval_gate_status"] != exp["approval_gate_status"]:
                ok = False
        # hard guards (불변): apply/production/promotion 0/False
        c = result["counters"]
        if not (c["apply_calls"] == 0 and c["transaction_calls"] == 0
                and c["production_write"] == 0 and c["operating_store_write"] == 0
                and c["promotion_allowed_changed"] is False):
            ok = False
        # action item gate 는 항상 apply_allowed/production_write_allowed False
        rp = result["reviewed_plan_preview"]
        for it in (rp["approved_preview"] + rp["rejected_preview"]
                   + rp["held_preview"] + rp["needs_evidence_preview"]):
            g = it["approval_gate"]
            if g["apply_allowed"] is not False or g["production_write_allowed"] is not False:
                ok = False
        # gate design 불변
        gate = mod.human_approval_gate_design()
        if not (gate["apply_allowed"] is False and gate["blocked_by_v09_required"] is True):
            ok = False
        n_ok += ok
        n_fail += (not ok)
        if not ok:
            fails.append((label, counts, result["approval_gate_status"]))
    return n_ok, n_fail, fails


def main():
    pkg, wrap = _load_both_forms()
    print("=" * 70)
    print("characterization selftest: openbinggu_reviewed_plan_preview")
    print("=" * 70)
    total_fail = 0
    for name, mod in (("package(binggupack.review)", pkg), ("wrapper(scripts sibling)", wrap)):
        n_ok, n_fail, fails = _check(mod)
        total_fail += n_fail
        flag = "PASS" if n_fail == 0 else "FAIL"
        print(f"  [{flag}] {name:32s} ok={n_ok} fail={n_fail}")
        for label, counts, gate in fails:
            print(f"         ! {label}: counts={counts} gate={gate}")
    # 두 import 형태 동일 함수 객체 (re-export 동일성)
    same = (pkg.assess is wrap.assess)
    print(f"  [{'PASS' if same else 'FAIL'}] wrapper.assess is package.assess (re-export identity)")
    if not same:
        total_fail += 1
    print(f"\n  GATE: {'GO' if total_fail == 0 else 'STOP'}")
    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
