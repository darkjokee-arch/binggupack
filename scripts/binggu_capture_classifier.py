# -*- coding: utf-8 -*-
"""빙구팩 자동 캡처 판정기 (backward-compatible thin wrapper).

v1.11.0 strangler phase4: 핵심 로직은 binggupack.classifier.capture_classifier 로 이관됐고,
이 파일은 공개 심볼/동작/분류 결과가 byte-identical 한 thin wrapper 다. 기존 호출처
(binggu_capture_buffer / binggu_capture_persist / openbinggu_mcp_server_handlers 의
'from binggu_capture_classifier import classify')는 그대로 동작한다.

CLI: python scripts/binggu_capture_classifier.py   (내장 _selftest)
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.classifier.capture_classifier import *  # noqa: E402,F401,F403
from binggupack.classifier.capture_classifier import (  # noqa: E402,F401  (밑줄 + 전체 명시 re-export)
    PREVIEW_TRIGGER,
    EXPLICIT_SAVE,
    SIGNAL_PATTERNS,
    HEDGE,
    VETO_PATTERNS,
    OPS_VERBS,
    OPS_IMPERATIVE,
    OPS_REPORT,
    META_CONFIRM,
    GENERALIZE_EXEMPT,
    classify,
    _hits,
    _any,
    _selftest,
)

if __name__ == "__main__":
    sys.exit(0 if _selftest() else 1)
