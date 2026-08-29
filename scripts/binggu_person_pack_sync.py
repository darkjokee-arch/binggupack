# -*- coding: utf-8 -*-
"""binggu_person_pack_sync — 사람축(owner) 온톨로지 팩 자동 동기화 (backward-compatible thin wrapper).

strangler: 순수 정본(build_pack_text · sync · confirm · sync_delta · confirm_delta ·
_owner_sentences · load_state/save_state · _hash/_sent_hash · main · _selftest 등)은
binggupack.pack.person_pack_sync 로 byte-identical 이관됐고, 이 파일은 공개 심볼이 동일한
thin wrapper 다. 기존 CLI 호출처(python scripts/binggu_person_pack_sync.py [--delta|--baseline|
--confirm-* |--selftest])는 그대로 동작한다.

read-only(ledger mode=ro)·상태파일만 write 불변은 1바이트도 변하지 않았다. 정본의 dep
(binggu_paths → binggupack.paths facade · binggu_t3_filter → binggupack.safety.t3_filter ·
binggu_schema → binggupack.storage.schema, 전부 MIGRATED)은 정본이 패키지 경로로 직접 참조한다.
이 wrapper 는 binggupack 패키지·scripts/ 형제 import 경로를 sys.path 에 얹어 정본을 로드한다.

CLI: python scripts/binggu_person_pack_sync.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.person_pack_sync import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    PACK_ID,
    STATE_FILE,
    PACK_CONFIG_FILE,
    DEFAULT_OWNER_LABEL,
    PACK_TITLE,
    pack_create_required,
    record_pack_id,
    _PACK_HEADER,
    _SENT_HASH_LEN,
    _home,
    _ledger,
    _state_path,
    _owner_sentences,
    _render_pack,
    build_pack_text,
    load_state,
    save_state,
    _hash,
    _sent_hash,
    sync,
    confirm,
    _render_delta,
    sync_delta,
    confirm_delta,
    _selftest,
    main,
)

__all__ = (
    'PACK_ID',
    'STATE_FILE',
    'PACK_CONFIG_FILE',
    'DEFAULT_OWNER_LABEL',
    'PACK_TITLE',
    'pack_create_required',
    'record_pack_id',
    '_PACK_HEADER',
    '_SENT_HASH_LEN',
    '_home',
    '_ledger',
    '_state_path',
    '_owner_sentences',
    '_render_pack',
    'build_pack_text',
    'load_state',
    'save_state',
    '_hash',
    '_sent_hash',
    'sync',
    'confirm',
    '_render_delta',
    'sync_delta',
    'confirm_delta',
    '_selftest',
    'main',
)


if __name__ == "__main__":
    sys.exit(main())
