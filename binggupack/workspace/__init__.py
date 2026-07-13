"""workspace — ledger/home/경로 관리(storage resolver). v1.11.0 save-gate S1 이관.

현재: platform(cross-platform 경로/resolver — binggu_home/default_ledger/detect_os 등, write 0).
scripts/binggu_platform.py 는 backward-compatible thin wrapper 로 유지된다.
"""
from .platform import (  # noqa: F401
    BINGGU_DIRNAME,
    LEDGER_NAME,
    LEDGER_BUSY_TIMEOUT_MS,
    detect_os,
    default_home_dir,
    binggu_home,
    default_ledger,
    default_settings,
    python_cmd,
    invocation_prefix,
    resolve_npx,
    shared_opt_in,
    to_wsl_path,
    from_wsl_path,
    display_path,
    lock_path_for,
    lock_conflict_message,
    apply_ledger_pragmas,
    platform_summary,
)
