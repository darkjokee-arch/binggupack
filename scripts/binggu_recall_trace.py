# -*- coding: utf-8 -*-
"""binggu_recall_trace.py — 회상 효용 trace (backward-compatible thin wrapper).

strangler Phase2: 순수 정본(record_trace/trace_from_*/record_outcome/list_pending/
mark_by_index/aggregate + opt-in 판정 + PII scrub + REASON_CODES/VALID_VERDICTS + _selftest)은
binggupack.pack.recall_trace 로 byte-identical 이관됐고, 이 파일은 공개 심볼이 동일한 thin
wrapper 다. 기존 호출처(import binggu_recall_trace — binggu.py · hooks/binggu_preflight_hook ·
doctor recall_trace 체크)는 그대로 동작한다.

PII 0/opt-in/actor=human 게이트/운영 ledger 불변은 1바이트도 변하지 않았다 — trace store 는
별도 <home>/recall_trace.sqlite(ledger.sqlite sibling · 미접촉)이고, query=sha16·노드=메타만
저장한다(selftest 가 바이트 검증). 코어 dep(binggu_p1_config/binggu_schema/binggu_recall)은
정본 모듈에서 scripts/ sys.path 경유 bare-name 으로 해소되며 각각 패키지 shim 으로 재배선된다.

CLI: python scripts/binggu_recall_trace.py [--selftest | --aggregate]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.recall_trace import *  # noqa: E402,F401,F403
from binggupack.pack.recall_trace import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    VALID_VERDICTS,
    REASON_CODES,
    _SIGNAL_NOTE,
    _DRIFT_N_MIN,
    _DRIFT_RATIO,
    trace_store_path,
    _open_store,
    review_snapshot_path,
    _flag_path,
    trace_enabled,
    set_trace_flag,
    _sha16,
    _scrub_node,
    _trace_id,
    record_trace,
    trace_from_why_search,
    trace_from_preflight,
    record_outcome,
    list_pending,
    save_review_snapshot,
    _load_review_snapshot,
    mark_by_index,
    aggregate,
    _selftest,
)


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    if sys.argv[1] == "--aggregate":
        import json as _json
        print(_json.dumps(aggregate(), ensure_ascii=False, indent=2))
        sys.exit(0)
    print("usage: binggu_recall_trace.py [--selftest | --aggregate]")
    sys.exit(2)
