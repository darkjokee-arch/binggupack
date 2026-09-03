#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu MCP path gate adapter (backward-compatible thin shim).

v1.11.x 트랙 C 묶음 이관: 정본 로직은 binggupack.mcp.path_gate_adapter 로 옮겼고,
이 파일은 공개 심볼을 재노출하는 thin shim 이다(동일 객체 재노출 → identity 유지).
기존 호출처(openbinggu_mcp_server_handlers·openbinggu_runtime_access_engine 의
'import openbinggu_mcp_path_gate_adapter as mcpgate' / guarded_tool_call)는 그대로 동작한다.

CLI: python scripts/openbinggu_mcp_path_gate_adapter.py --selftest
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.mcp.path_gate_adapter import (  # noqa: E402,F401  (밑줄 + 전체 명시 re-export)
    guarded_tool_call,
    _scan,
    main,
    _selftest,
)

__all__ = (
    'guarded_tool_call',
    '_scan',
    'main',
    '_selftest',
)

if __name__ == "__main__":
    main()
