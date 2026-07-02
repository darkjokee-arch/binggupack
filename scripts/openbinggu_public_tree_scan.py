#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu public tree secret/PII scanner (backward-compatible thin shim).

strangler: 순수 정본(scan_public_tree · PUBLIC_IGNORE · _secret_kv_match · _ignored ·
_open_text · _selftest · main 및 내부 상수/헬퍼)은 binggupack.safety.public_tree_scan 로
byte-identical 이관됐고, 이 파일은 공개 심볼이 동일한 thin shim 이다. 기존 호출처
(from openbinggu_public_tree_scan import scan_public_tree — openbinggu_doctor ·
binggu_publish_run_all_selftests(subprocess --tree/--public) 등)는 그대로 동작한다.

판정 로직·fail-closed(size/read_error 미검사 텍스트 = BLOCK)·raw 미출력은 1바이트도 변하지
않았다. sibling bare-name import 는 0(fixture=tempfile, __file__ 미사용)이나, subprocess 진입점
호환을 위해 scripts/ 와 repo root 를 sys.path 에 얹어 패키지 import 를 보장한다(선례 t3_filter).

CLI:
  python scripts/openbinggu_public_tree_scan.py --selftest
  python scripts/openbinggu_public_tree_scan.py --tree <ROOT> [--public]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.safety.public_tree_scan import *  # noqa: E402,F401,F403
from binggupack.safety.public_tree_scan import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    scan_public_tree,
    PUBLIC_IGNORE,
    _secret_kv_match,
    _ignored,
    _open_text,
    _selftest,
    main,
)


if __name__ == "__main__":
    main()
