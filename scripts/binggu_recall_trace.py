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

from binggupack.pack.recall_trace import (  # noqa: E402,F401  (계약 심볼 명시 re-export — 문서 겸용)
    VALID_VERDICTS,
    AI_STAMP_ACTOR,
    REASON_CODES,
    NOT_APPLIED_CODE,
    VALID_SITUATIONS,
    classify_situation,
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
    latest_session_id,
    record_trace,
    trace_from_why_search,
    trace_from_preflight,
    record_outcome,
    auto_observe_adoption,
    list_miss_candidates,
    auto_observe_enabled,
    set_auto_observe_flag,
    AUTOINJECT_JUDGE_TOP_N,
    AUTOINJECT_JUDGE_REL_MIN,
    AUTOINJECT_KINDS,
    AUTOINJECT_PENDING_TTL_DAYS,
    list_pending,
    count_pending,
    pending_stats,
    save_review_snapshot,
    _load_review_snapshot,
    mark_by_index,
    aggregate,
    SNAPSHOT_SCHEMA,
    _selftest,
)

# ★ 2026-08-08 — **정본에 새로 생긴 `_` 심볼을 자동으로 따라간다.**
#   `import *` 는 밑줄 심볼을 안 가져오므로 위 목록을 손으로 관리해 왔는데, 2026-08-01 에
#   정본으로 들어온 `_autoinject_judgeable`(자동주입 회상의 판정 대상 판별 · owner B안)이
#   목록에 안 들어갔다. 그 결과 shim 을 경유하는 `server_handlers._u_trace_stamp` 가
#   AttributeError 로 죽었고, **자동주입 회상 1,373건이 도장 한 번 못 받았다**(판정 16건 = 1.2% ·
#   그마저 전부 사람 도장 · AI 도장 0건). 정본을 고치고 사본을 안 고친 전형이다.
#   → 목록은 계약 문서로 남기되, 빠진 `_` 심볼은 여기서 자동 보강한다(setdefault 라 위 명시분 불변).
from binggupack.pack import recall_trace as _rt_src  # noqa: E402

__all__ = (
    'VALID_VERDICTS',
    'AI_STAMP_ACTOR',
    'REASON_CODES',
    'NOT_APPLIED_CODE',
    'VALID_SITUATIONS',
    'classify_situation',
    '_SIGNAL_NOTE',
    '_DRIFT_N_MIN',
    '_DRIFT_RATIO',
    'trace_store_path',
    '_open_store',
    'review_snapshot_path',
    '_flag_path',
    'trace_enabled',
    'set_trace_flag',
    '_sha16',
    '_scrub_node',
    '_trace_id',
    'latest_session_id',
    'record_trace',
    'trace_from_why_search',
    'trace_from_preflight',
    'record_outcome',
    'auto_observe_adoption',
    'list_miss_candidates',
    'auto_observe_enabled',
    'set_auto_observe_flag',
    'AUTOINJECT_JUDGE_TOP_N',
    'AUTOINJECT_JUDGE_REL_MIN',
    'AUTOINJECT_KINDS',
    'AUTOINJECT_PENDING_TTL_DAYS',
    'list_pending',
    'count_pending',
    'pending_stats',
    'save_review_snapshot',
    '_load_review_snapshot',
    'mark_by_index',
    'aggregate',
    'SNAPSHOT_SCHEMA',
    '_selftest',
    '_rt_src',
)
for _name in dir(_rt_src):
    if _name.startswith("_") and not _name.startswith("__"):
        globals().setdefault(_name, getattr(_rt_src, _name))
del _name


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    if sys.argv[1] == "--aggregate":
        import json as _json
        print(_json.dumps(aggregate(), ensure_ascii=False, indent=2))
        sys.exit(0)
    print("usage: binggu_recall_trace.py [--selftest | --aggregate]")
    sys.exit(2)
