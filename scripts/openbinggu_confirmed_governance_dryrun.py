#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenBinggu — confirmed governance (G4/G6) validator (backward-compatible thin wrapper).

v1.16 strangler Phase2: 정본 판정 로직은 binggupack.safety.confirmed_governance 로
byte-identical 이관됐고, 이 파일은 공개 심볼(evaluate_confirm/ALLOWED_STATUS/TRANSITIONS/
main/_selftest)이 동일한 thin wrapper 다. 기존 호출처(runtime_access_engine 의
gov.evaluate_confirm 등 import openbinggu_confirmed_governance_dryrun)는 그대로 동작.

순수 stdlib(외부·bare-name 의존 0) — 정본 모듈도 자기완결. synthetic selftest(operating
store write 0)는 정본 모듈에 있고, 이 wrapper 가 re-export 후 CLI 로 실행한다.

CLI: python scripts/openbinggu_confirmed_governance_dryrun.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로
if HERE not in sys.path:
    sys.path.insert(0, HERE)   # scripts/ 형제 import 경로(미이관 bare-name 대비)

from binggupack.safety.confirmed_governance import (  # noqa: E402,F401  (전체 명시 re-export)
    ALLOWED_STATUS,
    TRANSITIONS,
    evaluate_confirm,
    _selftest,
    main,
)

__all__ = (
    'ALLOWED_STATUS',
    'TRANSITIONS',
    'evaluate_confirm',
    '_selftest',
    'main',
)


if __name__ == "__main__":
    main()
