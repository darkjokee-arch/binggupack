# -*- coding: utf-8 -*-
"""빙구팩 캡처 세션 entrypoint (backward-compatible thin wrapper).

v1.11.0 strangler phase6: 핵심 로직은 binggupack.capture.session 으로 이관됐고, 이 파일은
공개 심볼/동작이 byte-identical 한 thin wrapper 다. 기존 호출처
(binggu_capture_cli 의 'from binggu_capture_session import CaptureSession')는 그대로 동작한다.

CaptureBuffer 는 binggupack 정본(binggupack.capture.buffer) 경유로 노출(역참조 0).
CLI: python scripts/binggu_capture_session.py   (내장 _selftest)
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.capture.session import (  # noqa: E402,F401  (전체 명시 re-export)
    CaptureSession,
    CaptureBuffer,
    _selftest,
)

__all__ = (
    'CaptureSession',
    'CaptureBuffer',
    '_selftest',
)

if __name__ == "__main__":
    sys.exit(0 if _selftest() else 1)
