# -*- coding: utf-8 -*-
"""OpenBinggu G2 — 동사형 엣지 6종 스키마 검증기 (backward-compatible thin wrapper).

v1.11.0 strangler phase1: 핵심 로직은 binggupack.schema.verb_edge 로 이관됐고, 이 파일은
공개 심볼/동작/exit code 가 byte-identical 한 thin wrapper 다. 기존 호출처
(from openbinggu_verb_edge_schema import validate_verb_edge, VERB_EDGES / import ... as schema)
는 그대로 동작한다.

CLI: python openbinggu_verb_edge_schema.py --selftest
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.schema.verb_edge import (  # noqa: E402,F401
    VERB_EDGES,
    WEAK_LABELS,
    VALID_STATUS,
    validate_verb_edge,
    validate_deprecated,
    default_view_filter,
    _selftest,
    _n,
    _e,
)

__all__ = (
    'VERB_EDGES',
    'WEAK_LABELS',
    'VALID_STATUS',
    'validate_verb_edge',
    'validate_deprecated',
    'default_view_filter',
    '_selftest',
    '_n',
    '_e',
)

if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        _selftest()
    else:
        print("usage: openbinggu_verb_edge_schema.py [--selftest]")
        sys.exit(2)
