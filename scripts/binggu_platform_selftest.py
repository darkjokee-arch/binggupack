# -*- coding: utf-8 -*-
"""binggu_platform helper selftest — Windows/WSL/macOS cross-platform 정책 검증.

검증 방식:
  - WSL/macOS 기본 홈 경로는 **synthetic**(env/os_name 주입)으로 검증한다.
    이 머신이 Windows 라도 정책이 옳은지 확인할 수 있다.
    => macOS path policy covered by synthetic tests.
  - lock 충돌 fail-closed 는 temp 파일에 실제 O_EXCL lock 을 만들어 StagingDB.write_lock
    이 다른 프로세스 흔적을 만나면 RuntimeError 로 막는지 **실측**한다(운영 store 미접촉).

불변: 운영 store write 0 · 실 ~/.binggupack write 0(temp only) · 네트워크 0.
CLI: py scripts/binggu_platform_selftest.py   (WSL/macOS: python3 ...)
"""
import os
import sqlite3
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import binggu_platform as P  # noqa: E402
from openbinggu_staging_write_selftest import StagingDB, OPERATING_PATHS  # noqa: E402


def main():
    print("=" * 70)
    print("binggu_platform — cross-platform 정책 selftest (synthetic + 실 lock)")
    print("=" * 70)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    checks = []

    def ck(name, ok):
        checks.append(bool(ok))
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))

    # ---- 1. OS 감지 (주입식) ----
    ck("1_detect_windows", P.detect_os(platform_name="win32") == "windows")
    ck("1b_detect_cygwin→windows", P.detect_os(platform_name="cygwin") == "windows")
    ck("2_detect_macos", P.detect_os(platform_name="darwin") == "macos")
    ck("3_detect_wsl_by_distro",
       P.detect_os(platform_name="linux", wsl_distro="Ubuntu", osrelease="x") == "wsl")
    ck("3b_detect_wsl_by_osrelease",
       P.detect_os(platform_name="linux", wsl_distro="", osrelease="5.15.0-microsoft-standard-WSL2") == "wsl")
    ck("4_detect_plain_linux",
       P.detect_os(platform_name="linux", wsl_distro="", osrelease="5.15.0-generic") == "linux")

    # ---- 2. OS별 기본 홈 (synthetic env 주입) ----
    win_env = {"USERPROFILE": r"C:\Users\fixture-user"}
    wsl_env = {"HOME": "/home/fixture-user"}
    mac_env = {"HOME": "/Users/fixture-user"}
    ck("5_windows_home", P.binggu_home(env=win_env, os_name="windows") == r"C:\Users\fixture-user\.binggupack")
    ck("6_wsl_home", P.binggu_home(env=wsl_env, os_name="wsl") == "/home/fixture-user/.binggupack")
    ck("7_macos_home", P.binggu_home(env=mac_env, os_name="macos") == "/Users/fixture-user/.binggupack")
    ck("8_windows_ledger",
       P.default_ledger(env=win_env, os_name="windows") == r"C:\Users\fixture-user\.binggupack\ledger.sqlite")
    ck("8b_wsl_ledger",
       P.default_ledger(env=wsl_env, os_name="wsl") == "/home/fixture-user/.binggupack/ledger.sqlite")
    ck("8c_macos_ledger",
       P.default_ledger(env=mac_env, os_name="macos") == "/Users/fixture-user/.binggupack/ledger.sqlite")
    ck("9_windows_settings",
       P.default_settings(env=win_env, os_name="windows") == r"C:\Users\fixture-user\.claude\settings.json")
    ck("9b_wsl_settings",
       P.default_settings(env=wsl_env, os_name="wsl") == "/home/fixture-user/.claude/settings.json")

    # ---- 3. BINGGU_HOME override (opt-in, OS 무관 그대로) ----
    sh = {"BINGGU_HOME": "/mnt/d/shared/.binggupack", "HOME": "/home/fixture-user"}
    ck("10_override_home_wsl", P.binggu_home(env=sh, os_name="wsl") == "/mnt/d/shared/.binggupack")
    ck("10b_override_ledger_wsl",
       P.default_ledger(env=sh, os_name="wsl") == "/mnt/d/shared/.binggupack/ledger.sqlite")
    shw = {"BINGGU_HOME": r"D:\shared\.binggupack", "USERPROFILE": r"C:\Users\fixture-user"}
    ck("10c_override_home_windows", P.binggu_home(env=shw, os_name="windows") == r"D:\shared\.binggupack")
    ck("11_shared_opt_in_true", P.shared_opt_in(env=sh) is True)
    ck("11b_shared_opt_in_false", P.shared_opt_in(env=win_env) is False)

    # ---- 4. python 런처 안내 ----
    ck("12_python_windows", P.python_cmd("windows") == "py")
    ck("12b_python_wsl", P.python_cmd("wsl") == "python3")
    ck("12c_python_macos", P.python_cmd("macos") == "python3")
    ck("12d_python_linux", P.python_cmd("linux") == "python3")

    # ---- 4b. npx 실행파일 해결 (외부 호출 PATH 폴백 — autopush 회귀 교훈, mock 이 못 잡던 실로직) ----
    import shutil as _sh
    _orig_which = _sh.which
    try:
        _sh.which = lambda n: None      # PATH 미발견 시뮬 → OS별 폴백 분기 검증
        ck("12e_resolve_npx_win_fallback", P.resolve_npx("windows") == "npx.cmd")
        ck("12f_resolve_npx_linux_fallback", P.resolve_npx("linux") == "npx")
        ck("12g_resolve_npx_macos_fallback", P.resolve_npx("macos") == "npx")
        _sh.which = lambda n: "/usr/local/bin/npx"   # 발견 시 실제 경로(.cmd 포함) 우선
        ck("12h_resolve_npx_prefers_which", P.resolve_npx("windows") == "/usr/local/bin/npx")
    finally:
        _sh.which = _orig_which

    # ---- 5. 경로 표시 변환 (표시용만 — 파일 미접촉) ----
    ck("13_to_wsl", P.to_wsl_path(r"C:\Users\fixture-user\.binggupack") == "/mnt/c/Users/fixture-user/.binggupack")
    ck("13b_from_wsl", P.from_wsl_path("/mnt/c/Users/fixture-user/.binggupack") == r"C:\Users\fixture-user\.binggupack")
    ck("13c_roundtrip", P.from_wsl_path(P.to_wsl_path(r"D:\a\b")) == r"D:\a\b")
    ck("14_display_to_wsl",
       P.display_path(r"C:\Users\fixture-user\.binggupack", target_os="wsl") == "/mnt/c/Users/fixture-user/.binggupack")
    ck("14b_display_to_windows",
       P.display_path("/mnt/c/Users/fixture-user/.binggupack", target_os="windows") == r"C:\Users\fixture-user\.binggupack")
    ck("14c_display_noop_posix",
       P.display_path("/home/fixture-user/.binggupack", target_os="wsl") == "/home/fixture-user/.binggupack")

    # ---- 6. 기존 Windows 동작 보존 (BINGGU_HOME 미설정·현재 OS = 기존 expanduser 와 동일) ----
    real_win_env = {"USERPROFILE": os.path.expanduser("~")}
    legacy = os.path.join(os.path.expanduser("~"), ".binggupack", "ledger.sqlite")
    # Windows 머신에서만 의미 있는 비교(분리자 일치). 다른 OS면 자동 PASS 처리.
    if P.detect_os() == "windows":
        ck("15_windows_legacy_preserved",
           P.default_ledger(env=real_win_env, os_name="windows") == legacy)
    else:
        ck("15_windows_legacy_preserved(skip:non-windows)", True)

    # ---- 7. SQLite busy_timeout 적용 (동시 접근 fail-closed 일관) ----
    tmp = tempfile.mkdtemp(prefix="bgp_plat_")
    try:
        dbf = os.path.join(tmp, "t.sqlite")
        con = sqlite3.connect(dbf)
        P.apply_ledger_pragmas(con)
        bt = con.execute("PRAGMA busy_timeout").fetchone()[0]
        jm = con.execute("PRAGMA journal_mode").fetchone()[0]
        con.close()
        ck("16_busy_timeout=5000", bt == P.LEDGER_BUSY_TIMEOUT_MS)
        ck("16b_journal_wal", str(jm).lower() == "wal")

        # ---- 8. lock 충돌 fail-closed (다른 프로세스 흔적 = 동시 실행 차단) ----
        led = os.path.join(tmp, "ledger.sqlite")
        db = StagingDB(led)               # temp — 운영 경로 아님
        lock = P.lock_path_for(led)       # helper 가 계산한 lock 경로
        # 다른 프로세스가 잡고 있는 상황: 살아있는 자식 pid 로 lock 선점
        # (죽은 pid lock 은 stale 로 자동 정리되므로 차단 계약은 live pid 로만 성립)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(child.pid).encode())   # 자기 pid 아님 · 생존 중
            os.close(fd)
            blocked = False
            try:
                with db.write_lock():
                    pass
            except RuntimeError as e:
                blocked = "locked" in str(e)
            ck("17_lock_conflict_fail_closed", blocked)
            ck("17b_lock_path_matches", lock == led + ".lock")
        finally:
            child.kill()
            child.wait()
            if os.path.exists(lock):
                os.remove(lock)
        # 선점 해제 후엔 정상 진입(같은 프로세스)
        ok_after = False
        with db.write_lock():
            ok_after = True
        ck("17c_lock_released_ok", ok_after and not os.path.exists(lock))
        db.close()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 운영 store 불변 ----
    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("18_operating_store_unchanged", op_before == op_after)

    ok = all(checks)
    print("-" * 70)
    print("=== %d/%d ===" % (sum(checks), len(checks)))
    print("RESULT: %d/%d PASS" % (sum(checks), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
