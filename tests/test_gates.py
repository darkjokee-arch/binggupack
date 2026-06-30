"""Thin pytest wrapper over the existing fast selftest gate.

목적(Finding #2): pyproject.toml 의 pytest python_files 패턴(*_test.py / test_*.py)이
tests/ 의 실제 harness 파일명과 불일치 → `python -m pytest` 가 0개 수집(거짓 green).
이 파일은 패턴에 맞는 단 하나의 얇은 테스트로, 기존 핵심 게이트(binggu.py --selftest)를
subprocess 로 호출하고 exit code 0 만 확인한다.

원칙:
- 기존 selftest 러너/harness 를 import·수정하지 않는다(부작용·중복 수집 방지).
- 빠른 게이트 1개만 래핑 — CI 를 무겁게 만들지 않는다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BINGGU = REPO_ROOT / "binggu.py"


def test_binggu_selftest_gate_passes() -> None:
    """binggu.py --selftest 가 exit 0(GATE: GO)로 끝나는지 확인."""
    assert BINGGU.is_file(), f"binggu.py not found at {BINGGU}"
    proc = subprocess.run(
        [sys.executable, str(BINGGU), "--selftest"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        "binggu.py --selftest failed "
        f"(exit={proc.returncode}).\n--- stdout tail ---\n"
        f"{proc.stdout[-2000:]}\n--- stderr tail ---\n{proc.stderr[-2000:]}"
    )
