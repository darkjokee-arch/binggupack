#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu — 물리 store 격리 validator (backward-compatible thin wrapper).

strangler: 핵심 로직은 binggupack.safety.physical_store_isolation 로 이관됐고, 이 파일은
공개 심볼/동작이 byte-identical 한 thin wrapper 다. 기존 호출처
(openbinggu_runtime_access_engine 의 'import openbinggu_physical_store_isolation_dryrun as phys')는
그대로 동작한다. 순수 stdlib(re/sys/hashlib/json)·__file__/open 0·seed 0.

CLI: python scripts/openbinggu_physical_store_isolation_dryrun.py --selftest
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.safety.physical_store_isolation import (  # noqa: E402,F401  (밑줄 + 전체 명시 re-export)
    OPERATING_STORE,
    USERS_RE,
    SHARED_RE,
    _path_id,
    check_store_access,
    build_allow_root,
    _selftest,
    main,
)

__all__ = (
    'OPERATING_STORE',
    'USERS_RE',
    'SHARED_RE',
    '_path_id',
    'check_store_access',
    'build_allow_root',
    '_selftest',
    'main',
)

if __name__ == "__main__":
    main()
