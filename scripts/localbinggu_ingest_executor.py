"""localbinggu_ingest_executor — BingguPack ZIP -> 로컬 OpenCrab 역인제스트 (thin wrapper).

strangler: 순수 정본(REQUIRED_ENTRIES · INGEST_EXTENSIONS · find_opencrab_exe · extract_zip ·
validate_extracted · build_ingest_command · ingest_zip · _selftest · main)은
binggupack.pack.ingest_executor 로 byte-identical 이관됐고, 이 파일은 공개 심볼이 동일한
thin wrapper 다. 기존 호출처(publish_run_all_selftests 의 파일명 실행,
cloud_pack_export 의 note 참조)는 그대로 동작한다. sibling import 0, __file__ 미의존.

CLI: python scripts/localbinggu_ingest_executor.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.ingest_executor import (  # noqa: E402,F401  (전체 명시 re-export)
    INGEST_EXTENSIONS,
    REQUIRED_ENTRIES,
    _selftest,
    build_ingest_command,
    extract_zip,
    find_opencrab_exe,
    ingest_zip,
    main,
    validate_extracted,
)

__all__ = (
    'INGEST_EXTENSIONS',
    'REQUIRED_ENTRIES',
    '_selftest',
    'build_ingest_command',
    'extract_zip',
    'find_opencrab_exe',
    'ingest_zip',
    'main',
    'validate_extracted',
)


if __name__ == "__main__":
    sys.exit(main())
