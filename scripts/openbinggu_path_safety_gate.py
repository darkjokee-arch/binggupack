#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu MCP/local path safety gate (backward-compatible thin wrapper).

v1.11.0 save-gate 라인 S2: 경로 안전 판정 로직은 binggupack.safety.path_safety 로 이관됐고,
이 파일은 공개 심볼/판정(verdict/reason_code/path_id)이 byte-identical 한 thin wrapper 다.
기존 호출처(openbinggu_mcp_path_gate_adapter 의 from ... import classify_path /
openbinggu_runtime_access_engine 의 import ... as psg)는 그대로 동작한다. 순수 판정(write 0).

CLI: python scripts/openbinggu_path_safety_gate.py --selftest
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.safety.path_safety import (  # noqa: E402,F401  (전체 명시 re-export)
    classify_path,
    main,
    _path_id,
    _selftest,
    _DENY,
    _RE_8_3,
    _RE_DRIVE,
)

__all__ = (
    'classify_path',
    'main',
    '_path_id',
    '_selftest',
    '_DENY',
    '_RE_8_3',
    '_RE_DRIVE',
)

if __name__ == "__main__":
    main()
