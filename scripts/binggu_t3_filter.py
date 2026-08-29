# -*- coding: utf-8 -*-
"""T3 하드제외 필터 (backward-compatible thin wrapper).

strangler: 순수 정본(past_hits · _pii_present · is_t3_blocked · t3_report · filter_uploadable ·
T3_PAST_TERMS* 사전 · _selftest)은 binggupack.safety.t3_filter 로 byte-identical 이관됐고, 이
파일은 공개 심볼이 동일한 thin wrapper 다. 기존 호출처(from binggu_t3_filter import ... —
binggu_cloud_ingest_wire · binggu_person_pack_sync 등)는 그대로 동작한다.

판정 로직·fail-closed 불변은 1바이트도 변하지 않았다. _pii_present 런타임의 watcher_batch_m1
(미이관·bare-name) lazy import 는 정본 모듈이 scripts/ 를 sys.path 에 얹어 해소한다(이 wrapper
도 동일하게 얹는다 — 이중 안전).

CLI: python scripts/binggu_t3_filter.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.safety.t3_filter import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    T3_PAST_TERMS,
    T3_PAST_TERMS_HANJA,
    T3_PAST_TERMS_EN,
    past_hits,
    _pii_present,
    is_t3_blocked,
    t3_report,
    filter_uploadable,
    _selftest,
)

__all__ = (
    'T3_PAST_TERMS',
    'T3_PAST_TERMS_HANJA',
    'T3_PAST_TERMS_EN',
    'past_hits',
    '_pii_present',
    'is_t3_blocked',
    't3_report',
    'filter_uploadable',
    '_selftest',
)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("binggu_t3_filter: --selftest 로 검증 실행")
