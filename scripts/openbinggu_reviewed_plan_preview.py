# -*- coding: utf-8 -*-
"""OpenBinggu v0.15 — reviewed-plan PREVIEW (backward-compatible thin wrapper).

v1.11.0 strangler phase3: 핵심 로직은 binggupack.review.reviewed_plan_preview 로 이관됐고,
이 파일은 공개 심볼/동작이 byte-identical 한 thin wrapper 다. 기존 호출처
(python scripts/openbinggu_reviewed_plan_preview.py --selftest / <fixture.json>,
 import openbinggu_reviewed_plan_preview as m → m.assess 등)는 그대로 동작한다.

CLI:
  python scripts/openbinggu_reviewed_plan_preview.py --selftest
  python scripts/openbinggu_reviewed_plan_preview.py <fixture.json>
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.review.reviewed_plan_preview import (  # noqa: E402,F401  (밑줄 내부 심볼 + 전체 명시 re-export)
    BASE,
    FIXTURE_DIR,
    PLAN_REPORT,
    SELFTEST_REPORT,
    ACTION_MAP,
    NON_ACTION_MAP,
    new_counters,
    human_approval_gate_design,
    assess,
    _counts,
    _all_action_items,
    _refs_preserved,
    run_selftest,
    run_single,
    main,
)

__all__ = (
    'BASE',
    'FIXTURE_DIR',
    'PLAN_REPORT',
    'SELFTEST_REPORT',
    'ACTION_MAP',
    'NON_ACTION_MAP',
    'new_counters',
    'human_approval_gate_design',
    'assess',
    '_counts',
    '_all_action_items',
    '_refs_preserved',
    'run_selftest',
    'run_single',
    'main',
)

if __name__ == "__main__":
    main()
