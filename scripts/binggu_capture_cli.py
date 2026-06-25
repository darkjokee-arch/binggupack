# -*- coding: utf-8 -*-
"""빙구팩 캡처 수동 호출 경로 (backward-compatible thin wrapper).

v1.11.0 strangler phase7: 핵심 로직은 binggupack.capture.cli 로 이관됐고, 이 파일은
공개 심볼/동작/출력/exit code 가 byte-identical 한 thin wrapper 다. cli 는 leaf entrypoint
(외부 import 호출처 0). CaptureSession 은 binggupack 정본(binggupack.capture.session) 경유.

사용(수동):
  echo 발화들 | python scripts/binggu_capture_cli.py        # stdin 줄단위
  python scripts/binggu_capture_cli.py --feed "B안으로 결정" --feed "이거 저장해"
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.capture.cli import *  # noqa: E402,F401,F403
from binggupack.capture.cli import (  # noqa: E402,F401  (전체 명시 re-export)
    run_batch,
    run_cli,
    CaptureSession,
    _read_args,
    _selftest,
)

if __name__ == "__main__":
    sys.exit(run_cli())
