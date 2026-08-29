# -*- coding: utf-8 -*-
"""binggu_rationale_suggest.py — 2층 근거 사슬 추천 (backward-compatible thin wrapper).

strangler: 순수 정본(suggest_rationale·_rationale_text·SUPPORTS·상수·_selftest)은
binggupack.pack.rationale_suggest 로 byte-identical 이관됐고, 이 파일은 공개 심볼 동일한
thin wrapper 다. 기존 호출처(from binggu_rationale_suggest import suggest_rationale, SUPPORTS /
import binggu_rationale_suggest as r2)는 그대로 동작한다.

CLI: python binggu_rationale_suggest.py   (selftest)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.pack.rationale_suggest import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    SUPPORTS,
    VERB_EDGES,
    SUPPORTS_SRC,
    CAVEAT_NODE,
    CAVEAT_EDGE,
    suggest_rationale,
    _rationale_text,
    _selftest,
    _SUBTYPE_WHY,
    _RATIONALE_FLOOR,
    _DUP_CEIL,
)

__all__ = (
    'SUPPORTS',
    'VERB_EDGES',
    'SUPPORTS_SRC',
    'CAVEAT_NODE',
    'CAVEAT_EDGE',
    'suggest_rationale',
    '_rationale_text',
    '_selftest',
    '_SUBTYPE_WHY',
    '_RATIONALE_FLOOR',
    '_DUP_CEIL',
)


if __name__ == "__main__":
    sys.exit(_selftest())
