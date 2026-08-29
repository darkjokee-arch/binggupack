"""BingguPack PC-mediated read 공유 — P1 (backward-compatible thin wrapper).

strangler: 순수 정본(open_queue · enqueue · transition · approve · mark_block · validation_passes ·
parse_approve · content_hash · verify_hash_triple · is_full_sha256 · acquire_lock · release_lock ·
QueueError · IllegalTransition · ALLOWED_TRANSITIONS · TERMINAL)은 binggupack.pack.publish_queue_p1
로 byte-identical 이관됐고, 이 파일은 공개 심볼이 동일한 thin wrapper 다. 기존 호출처
(import binggu_publish_queue_p1 — binggu_publish_p2_pipeline · binggu_publish_queue_p1_selftest 등)는
그대로 동작한다.

상태머신·hash 3중·fail-closed 판정은 1바이트도 변하지 않았다. top-level `import binggu_platform`
(MIGRATED shim · 미이관 bare-name) lazy import 는 정본 모듈이 scripts/ 를 sys.path 에 얹어 해소한다
(이 wrapper 도 동일하게 얹는다 — 이중 안전).

설계: docs/BINGGUPACK_CROSSDEVICE_PUBLISH_PIPELINE_DESIGN.md
CLI: python scripts/binggu_publish_queue_p1_selftest.py  (실검증은 별도 selftest 모듈)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.publish_queue_p1 import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    ALLOWED_TRANSITIONS,
    TERMINAL,
    QueueError,
    IllegalTransition,
    open_queue,
    enqueue,
    transition,
    approve,
    mark_block,
    validation_passes,
    parse_approve,
    content_hash,
    verify_hash_triple,
    is_full_sha256,
    acquire_lock,
    release_lock,
    _assert_temp_path,
    _status,
    _plat,
    _OPERATIONAL_LEDGER,
    _APPROVE_RE,
)

__all__ = (
    'ALLOWED_TRANSITIONS',
    'TERMINAL',
    'QueueError',
    'IllegalTransition',
    'open_queue',
    'enqueue',
    'transition',
    'approve',
    'mark_block',
    'validation_passes',
    'parse_approve',
    'content_hash',
    'verify_hash_triple',
    'is_full_sha256',
    'acquire_lock',
    'release_lock',
    '_assert_temp_path',
    '_status',
    '_plat',
    '_OPERATIONAL_LEDGER',
    '_APPROVE_RE',
)


if __name__ == "__main__":
    print("P1 module — run binggu_publish_queue_p1_selftest.py")
