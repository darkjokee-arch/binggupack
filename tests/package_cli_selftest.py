#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PyPI/package install CLI contract selftest.

Verifies the installed wheel exposes the public CLI entry points and that the
basic first-run flow works outside a repo checkout.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(cmd, *, env=None, cwd=None):
    return subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def assert_ok(name, proc, contains=None):
    if proc.returncode != 0:
        print("[FAIL]", name)
        print(proc.stdout)
        return False
    if contains and contains not in proc.stdout:
        print("[FAIL]", name, "missing:", contains)
        print(proc.stdout)
        return False
    print("[PASS]", name)
    return True


def main():
    tmp = Path(tempfile.mkdtemp(prefix="binggupack_pkg_cli_"))
    venv = tmp / "venv"
    home = tmp / "home"
    checks = []
    try:
        checks.append(assert_ok("venv_create", run([sys.executable, "-m", "venv", str(venv)])))
        py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
        binggu_exe = bin_dir / ("binggu.exe" if os.name == "nt" else "binggu")

        checks.append(assert_ok(
            "pip_install_repo",
            run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", str(ROOT)]),
        ))
        bundle_code = (
            "from pathlib import Path; import binggu; "
            "root=Path(binggu.__file__).parent; "
            "assert (root/'scripts/openbinggu_conversation_capture_preview.py').exists(); "
            "assert (root/'hooks/binggu_capture_hook.py').exists(); "
            "assert (root/'scripts/hybrid_agi/hag_sync_adapter.py').exists()"
        )
        checks.append(assert_ok("bundled_scripts_and_hooks", run([str(py), "-c", bundle_code])))
        console_exists = binggu_exe.exists()
        checks.append(console_exists)
        print("[PASS]" if console_exists else "[FAIL]", "console_script_exists")

        env = os.environ.copy()
        env["BINGGU_HOME"] = str(home)
        if console_exists:
            checks.append(assert_ok("binggu_help", run([str(binggu_exe), "--help"], env=env), "BingguPack"))
            checks.append(assert_ok("binggu_start_no_capture", run([str(binggu_exe), "start", "--no-capture"], env=env), "장부 생성 완료"))
            checks.append(assert_ok("binggu_remember_preview", run([str(binggu_exe), "remember", "배포 전에 live endpoint를 먼저 확인한다"], env=env), "preview_id:"))
            checks.append(assert_ok("binggu_doctor", run([str(binggu_exe), "doctor"], env=env), "audit chain INTACT"))
        else:
            checks.extend([False, False, False, False])
        checks.append(assert_ok("python_m_binggupack_help", run([str(py), "-m", "binggupack", "--help"], env=env), "BingguPack"))
        checks.append(bool((home / "ledger.sqlite").exists()))
        print("[PASS]" if checks[-1] else "[FAIL]", "ledger_created")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok = all(checks)
    print("RESULT: %d/%d PASS" % (sum(1 for c in checks if c), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
