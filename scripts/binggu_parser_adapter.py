# -*- coding: utf-8 -*-
"""binggu_parser_adapter — 전방위 파싱 어댑터 (backward-compatible thin wrapper).

v1.16 strangler Phase2: 파싱 어댑터 정본 로직(parse_document/detect_format · ParserBackend/
PlainTextBackend/MarkItDownBackend/KorDocBackend · ERR_* typed error · _which/_run_cli/
_default_backends/_looks_corrupt)은 binggupack.pack.parser_adapter 로 byte-identical 이관됐고,
이 파일은 공개 심볼이 동일한 thin wrapper 다. 기존 호출처(import binggu_parser_adapter —
binggu_harvest 의 lazy import PA.detect_format/PA.parse_document)는 그대로 동작한다.

stdlib(subprocess/shutil/hashlib)만 쓰고 binggu 의존 0·__file__ 상대 seed 0 이라 정본 이동이
byte-identical 하며, backend 는 subprocess CLI 라 import seed 도 없다. selftest 는 backend mock
(실 파서/네트워크 0)으로 자기완결적이라 정본 모듈에 함께 이관됐고 이 wrapper 는 위임한다.

CLI: python scripts/binggu_parser_adapter.py [--selftest]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.pack.parser_adapter import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    ERR_UNSUPPORTED,
    ERR_NOT_WIRED,
    ERR_CALL_FAILED,
    ERR_MISSING,
    ERR_FAILED,
    ERR_CORRUPT,
    ERR_EMPTY,
    ParserBackend,
    PlainTextBackend,
    MarkItDownBackend,
    KorDocBackend,
    detect_format,
    parse_document,
    _which,
    _run_cli,
    _default_backends,
    _looks_corrupt,
    _selftest,
)

__all__ = (
    'ERR_UNSUPPORTED',
    'ERR_NOT_WIRED',
    'ERR_CALL_FAILED',
    'ERR_MISSING',
    'ERR_FAILED',
    'ERR_CORRUPT',
    'ERR_EMPTY',
    'ParserBackend',
    'PlainTextBackend',
    'MarkItDownBackend',
    'KorDocBackend',
    'detect_format',
    'parse_document',
    '_which',
    '_run_cli',
    '_default_backends',
    '_looks_corrupt',
    '_selftest',
)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_parser_adapter — use --selftest, or import parse_document()")
