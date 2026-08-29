# -*- coding: utf-8 -*-
"""binggu_person_crab_sync — owner 온톨로지 CrabAgent 스키마 동기화 (thin wrapper).

정본은 binggupack.pack.person_crab_sync (신설 2026-07-06 — 정본 직행).
문장당 1문서 수출 → crab_pack_wire 스키마 빌드 → 같은 pack_name 제자리 교체.
기본 dry_run·auto 는 person_pack.json crab_auto_sync:true 옵트인 필수 — 계약은 정본 참조.

CLI: python scripts/binggu_person_crab_sync.py --selftest | [--live --confirm] | --auto
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.pack.person_crab_sync import (  # noqa: E402,F401  (명시 re-export)
    DEFAULT_PACK_NAME,
    DEFAULT_PACK_TITLE,
    DEFAULT_OWNER_LABEL,
    DEFAULT_PACK_PURPOSE,
    ENABLE_ENV,
    EXTRA_SOURCES_DIR,
    PACK_CONFIG_FILE,
    PACK_TITLE,
    PACK_PURPOSE,
    STATE_FILE,
    export_docs,
    extra_signature,
    merge_extra_sources,
    load_state,
    save_state,
    sync,
    sync_auto,
    _selftest,
    main,
)

__all__ = (
    'DEFAULT_PACK_NAME',
    'DEFAULT_PACK_TITLE',
    'DEFAULT_OWNER_LABEL',
    'DEFAULT_PACK_PURPOSE',
    'ENABLE_ENV',
    'EXTRA_SOURCES_DIR',
    'PACK_CONFIG_FILE',
    'PACK_TITLE',
    'PACK_PURPOSE',
    'STATE_FILE',
    'export_docs',
    'extra_signature',
    'merge_extra_sources',
    'load_state',
    'save_state',
    'sync',
    'sync_auto',
    '_selftest',
    'main',
)


if __name__ == "__main__":
    sys.exit(main())
