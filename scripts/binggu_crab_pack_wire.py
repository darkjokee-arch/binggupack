# -*- coding: utf-8 -*-
"""binggu_crab_pack_wire — CrabAgent 스키마 팩 빌드+업로드 wire (thin wrapper).

정본은 binggupack.pack.crab_pack_wire (신설 2026-07-06 — 이관 아닌 정본 직행).
빌드(순수·leak fail-closed·원본 미포함) + 업로드(dry_run 기본·ENABLE+confirm 게이트·
statement timeout 세션 재발급 재시도·한글 pack_name ASCII 자동 변환) 계약은 정본 참조.

CLI: python scripts/binggu_crab_pack_wire.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.pack.crab_pack_wire import (  # noqa: E402,F401  (명시 re-export — _ 심볼 포함)
    ENABLE_ENV,
    CRAB_TOOL,
    DEFAULT_MAX_TRIES,
    ascii_pack_name,
    build_crab_pack,
    chunk_doc,
    derive_claims,
    derive_concepts,
    derive_queries,
    default_put_fn,
    default_post_fn,
    upload_crab_pack,
    _mcp_call,
    _parse_session,
    _selftest,
    main,
)

__all__ = (
    'ENABLE_ENV',
    'CRAB_TOOL',
    'DEFAULT_MAX_TRIES',
    'ascii_pack_name',
    'build_crab_pack',
    'chunk_doc',
    'derive_claims',
    'derive_concepts',
    'derive_queries',
    'default_put_fn',
    'default_post_fn',
    'upload_crab_pack',
    '_mcp_call',
    '_parse_session',
    '_selftest',
    'main',
)


if __name__ == "__main__":
    sys.exit(main())
