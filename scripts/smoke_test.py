#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BingguPack MCP — offline smoke test (clone 직후 실행 가능. MCP 등록 불필요).

v1.11.0: 핵심 로직은 binggupack.pack.smoke 로 이관됐고, 이 파일은
backward-compatible thin wrapper 다. 명령/출력/exit code 는 v1.10.0 과 동일.

usage:
  python scripts/smoke_test.py --home ./_binggu_test_home
  python scripts/smoke_test.py                 # home 미지정 시 temp 자동
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.pack.smoke import run_smoke_cli

__all__ = (
    'run_smoke_cli',
)

if __name__ == "__main__":
    run_smoke_cli()
