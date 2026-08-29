#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu — review queue resolver 결선 (backward-compatible thin wrapper).

strangler phase3: 순수 정본(ReviewResolver · _selftest · main)은
binggupack.review.resolver_sandbox 로 byte-identical 이관됐고, 이 파일은 공개 심볼
동일한 thin wrapper 다. sandbox·apply HOLD·raw 0 불변은 1바이트도 변하지 않았다.
기존 호출처(runtime_access_engine 등 import openbinggu_review_resolver_sandbox·scripts/
직접 실행)는 그대로 동작한다.

dep(evaluate_confirm)은 정본 모듈이 정식 패키지 import(binggupack.safety.confirmed_governance
— migrated)로 해소한다. 미이관 bare-name 대비로 scripts/ 도 sys.path 에 얹는다.

CLI: python openbinggu_review_resolver_sandbox.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.review.resolver_sandbox import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    ReviewResolver,
    _selftest,
    main,
)

__all__ = (
    'ReviewResolver',
    '_selftest',
    'main',
)


if __name__ == "__main__":
    main()
