# -*- coding: utf-8 -*-
"""binggu_outcome_attribution.py — Recall→Outcome Attribution (thin wrapper).

순수 정본은 binggupack.pack.outcome_attribution 이고, 이 파일은 공개 심볼이 동일한 thin
wrapper 다(run_all selftest 러너·bare-name 호출처 호환). store 는 recall_trace 와 공유하는
별도 <home>/recall_trace.sqlite(ledger.sqlite sibling · 미접촉)이며, 인과 단정 0·evidence-gated
자동 관찰·append-only·overturn 은 사람만 — 정본 모듈 docstring 참조.

CLI: python scripts/binggu_outcome_attribution.py [--selftest | --aggregate]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.pack.outcome_attribution import *  # noqa: E402,F401,F403
from binggupack.pack.outcome_attribution import (  # noqa: E402,F401 (전체 명시 re-export)
    VALID_APPLICATION,
    VALID_RESULT,
    VALID_EVIDENCE_KIND,
    TRUST_AUTO,
    TRUST_OVERTURN,
    _SIGNAL_NOTE,
    _outcome_id,
    record_run_outcome,
    list_run_outcomes,
    overturn_run_outcome,
    aggregate_run_outcomes,
    last_trace_path,
    stage_last_trace,
    last_staged_trace,
    _selftest,
)


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    if sys.argv[1] == "--aggregate":
        import json as _json
        print(_json.dumps(aggregate_run_outcomes(), ensure_ascii=False, indent=2))
        sys.exit(0)
    print("usage: binggu_outcome_attribution.py [--selftest | --aggregate]")
    sys.exit(2)
