#!/usr/bin/env python3
"""binggupack_sign_util.py — save-intent HMAC 서명 단일 출처 (backward-compatible thin wrapper).

strangler: 순수 정본(signed_headers/signed_headers_legacy/_pathname·서명 재료 계약)은
binggupack.safety.sign_util 로 byte-identical 이관됐고, 이 파일은 공개 심볼이 동일한 thin
wrapper 다. 기존 호출처(from binggupack_sign_util import signed_headers — save_intent live
runner · v21/v2a selftest 등)는 그대로 동작한다.

서명 재료(hosted/workers/src/save_common.ts 와 바이트 동일 의무)는 1바이트도 변하지 않았다 —
로직은 정본 모듈에 그대로 있고 여기서 재-export 만 한다.

CLI: python scripts/binggupack_sign_util.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.safety.sign_util import *  # noqa: E402,F401,F403
from binggupack.safety.sign_util import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    signed_headers,
    signed_headers_legacy,
    _pathname,
    _selftest,
)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    print("usage: binggupack_sign_util.py --selftest")
    sys.exit(1)
