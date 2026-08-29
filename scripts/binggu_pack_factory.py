"""binggu_pack_factory — parsed documents → OpenBinggu pack (backward-compatible thin wrapper).

strangler: 순수 정본(build_pack · export_cloud_text · _manifest · _slug · _write_pack ·
_iter_pack_chunks · _guess_title · _selftest)은 binggupack.pack.pack_factory 로 byte-identical
이관됐고, 이 파일은 공개 심볼이 동일한 thin wrapper 다. 기존 호출처(import binggu_pack_factory —
binggu_local_collect · binggu_topic_to_pack 등)는 그대로 동작한다.

top-level `import openbinggu_pack_validate`(MIGRATED shim·bare-name)는 정본 모듈이 scripts/ 를
sys.path 에 얹어 해소한다(이 wrapper 도 동일하게 얹는다 — 이중 안전). 파일쓰기(_write_pack)는
out_dir 인자기반이라 __file__ 위치 무관. 런타임 이관부(build_pack 등)는 cross-dep 0.

CLI: python scripts/binggu_pack_factory.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.pack_factory import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    build_pack,
    export_cloud_text,
    _manifest,
    _slug,
    _write_pack,
    _iter_pack_chunks,
    _guess_title,
    _selftest,
)

__all__ = (
    'build_pack',
    'export_cloud_text',
    '_manifest',
    '_slug',
    '_write_pack',
    '_iter_pack_chunks',
    '_guess_title',
    '_selftest',
)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_pack_factory — use --selftest, or import build_pack()")
