# -*- coding: utf-8 -*-
"""comp3 — Merkle 앵커 (2단 무결성) (backward-compatible thin wrapper).

strangler: 순수 정본(merkle_root · _leaf_hash · _LEAF_EVENT_COLS · _canon_event · _hash · _canon ·
seal_event · record_and_seal · find_unsealed_events · verify_chain · verify_from_db · CHAIN_VER ·
_selftest)은 binggupack.pack.merkle_anchor 로 byte-identical 이관됐고, 이 파일은 공개 심볼이
동일한 thin wrapper 다. 기존 호출처(from binggu_merkle_anchor import merkle_root/_leaf_hash/
_LEAF_EVENT_COLS — binggu_hit_export 등)는 그대로 동작한다(private 심볼 재노출 포함).

봉인·검증 로직(fail-closed·TOCTOU atomic·Merkle root full64)은 1바이트도 변하지 않았다.
미이관 bare-name(_selftest 내 openbinggu_staging_write_selftest fixture · binggu_hit_stats lazy)
import 는 정본 모듈이 scripts/ 를 sys.path 에 얹어 해소한다(이 wrapper 도 동일하게 얹는다).

CLI: python scripts/binggu_merkle_anchor.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.merkle_anchor import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    CHAIN_VER,
    _LEAF_EVENT_COLS,
    _canon,
    _hash,
    _canon_event,
    _leaf_hash,
    merkle_root,
    seal_event,
    record_and_seal,
    find_unsealed_events,
    verify_chain,
    verify_from_db,
    _selftest,
)

__all__ = (
    'CHAIN_VER',
    '_LEAF_EVENT_COLS',
    '_canon',
    '_hash',
    '_canon_event',
    '_leaf_hash',
    'merkle_root',
    'seal_event',
    'record_and_seal',
    'find_unsealed_events',
    'verify_chain',
    'verify_from_db',
    '_selftest',
)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("binggu_merkle_anchor: --selftest 로 검증 실행")
