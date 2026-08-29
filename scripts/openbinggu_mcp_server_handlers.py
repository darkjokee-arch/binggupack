#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu MCP 서버 도구 핸들러 (backward-compatible thin shim).

v1.11.x 트랙 C 묶음 이관: 정본 로직은 binggupack.mcp.server_handlers 로 옮겼고,
이 파일은 공개 심볼을 재노출하는 thin shim 이다(동일 객체 재노출 → identity 유지).
기존 호출처(binggupack.mcp facade·openbinggu_mcp_server·binggupack.pack.smoke 의
'from openbinggu_mcp_server_handlers import handle_tool')는 그대로 동작한다.

CLI: python scripts/openbinggu_mcp_server_handlers.py --selftest
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.mcp.server_handlers import (  # noqa: E402,F401  (밑줄 + 전체 명시 re-export)
    handle_tool,
    reason_code_hint,
    TOOLS,
    _FORBIDDEN,
    main,
    _selftest,
    _SAVE_CONVO,
    _u_pack_build,
    _u_pack_validate,
    _u_consumer_smoke,
    _u_publish_guard_dryrun,
    _u_selftest,
    _u_capture_classify,
    _u_capture_preview,
    _u_save_candidate,
)

__all__ = (
    'handle_tool',
    'reason_code_hint',
    'TOOLS',
    '_FORBIDDEN',
    'main',
    '_selftest',
    '_SAVE_CONVO',
    '_u_pack_build',
    '_u_pack_validate',
    '_u_consumer_smoke',
    '_u_publish_guard_dryrun',
    '_u_selftest',
    '_u_capture_classify',
    '_u_capture_preview',
    '_u_save_candidate',
)

if __name__ == "__main__":
    main()
