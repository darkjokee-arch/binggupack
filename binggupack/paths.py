"""binggupack.paths — 경로 정본 re-export (scripts/binggu_paths.py).

패키지 호출자가 scripts/ 개별 파일을 직접 import 하지 않도록 하는 facade.
정본 로직/값은 전부 scripts/binggu_paths.py 한 곳에만 있다.
"""
import os
import sys

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from binggu_paths import home, ledger, state_path, OPERATING_PATHS, LEDGER_NAME  # noqa: E402,F401

__all__ = ["home", "ledger", "state_path", "OPERATING_PATHS", "LEDGER_NAME"]
