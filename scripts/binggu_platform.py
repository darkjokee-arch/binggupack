# -*- coding: utf-8 -*-
"""BingguPack cross-platform 경로/플랫폼 helper (backward-compatible thin wrapper).

v1.11.0 save-gate 라인 S1: storage resolver 로직은 binggupack.workspace.platform 으로
이관됐고, 이 파일은 공개 심볼/동작/경로 계산이 byte-identical 한 thin wrapper 다. 기존
호출처(import binggu_platform as _plat/P/plat → _plat.binggu_home() 등)는 그대로 동작한다.
순수 함수(write 0). stage0 split-brain 차단(save_gate 가 보는 home == 이 resolver) 유지.

CLI: python scripts/binggu_platform.py   (platform_summary)
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.workspace.platform import *  # noqa: E402,F401,F403
from binggupack.workspace.platform import (  # noqa: E402,F401  (전체 명시 re-export)
    BINGGU_DIRNAME,
    LEDGER_NAME,
    LEDGER_BUSY_TIMEOUT_MS,
    detect_os,
    default_home_dir,
    binggu_home,
    default_ledger,
    default_settings,
    python_cmd,
    resolve_npx,
    shared_opt_in,
    to_wsl_path,
    from_wsl_path,
    display_path,
    lock_path_for,
    lock_conflict_message,
    apply_ledger_pragmas,
    platform_summary,
    _joiner,
)

# invocation_prefix 는 위 star-import 로도 들어오지만, 구버전 platform.py(심볼 부재)에서도
# 안전하도록 try/except 로 명시 재export 후 실패 시 동일 계약의 자체 폴백을 정의한다.
try:  # noqa: SIM105
    from binggupack.workspace.platform import invocation_prefix  # noqa: E402,F401
except ImportError:
    def invocation_prefix(argv0=None):  # type: ignore[misc]
        """설치본="binggu" / 소스=".py"→"python binggu.py" (자체 폴백·raise 없음)."""
        try:
            raw = argv0 if argv0 is not None else (sys.argv[0] if sys.argv else "")
            base = os.path.basename(raw or "")
            if base and not base.lower().endswith(".py"):
                return "binggu"
        except Exception:
            pass
        return "python binggu.py"

if __name__ == "__main__":
    import json
    print(json.dumps(platform_summary(), ensure_ascii=False, indent=2))
