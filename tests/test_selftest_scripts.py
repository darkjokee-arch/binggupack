"""Parametrized pytest wrapper over the repo's core selftest scripts.

목적(정본신설 #4): tests/ 의 실검증은 96개 *_selftest.py / --selftest CLI 의
custom check()→GATE→exit code 관례에 의존하는데, pytest 관점에서는 `def test_` 가
test_gates.py 1개뿐이라 커버리지가 0에 가깝게 오판된다. 이 파일은 그 관례를 pytest 로
가시화하는 얇은 래퍼로, 주요 게이트 스크립트들을 subprocess 로 돌려
exit 0 + GATE 토큰(GATE=GO / GATE: GO / REGRESSION=GO)을 확인한다.

원칙(test_gates.py 스타일 계승):
- 기존 selftest 러너/harness 를 import·수정하지 않는다(부작용·중복 수집 방지).
- 각 스크립트는 자기 프로세스에서 격리 실행(sys.executable, cwd=REPO_ROOT).
- 파라미터화로 스크립트 목록을 1파일에서 순회 — CI 확장은 SPECS 에 한 줄 추가.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# (id, 스크립트 상대경로, 추가 인자, 성공 토큰)
#   토큰 형식이 스크립트마다 다르다(GATE=GO / GATE: GO / REGRESSION=GO) — 각 항목에 명시.
#   openbinggu_public_tree_scan 은 synthetic --selftest(temp fixture, 트리 스캔 아님)로
#   환경 비의존·결정적 게이트만 확인한다.
SPECS = [
    ("t3_filter", "scripts/binggu_t3_filter.py", ["--selftest"], "GATE=GO"),
    ("cloud_ingest_wire", "scripts/binggu_cloud_ingest_wire.py", ["--selftest"], "GATE=GO"),
    ("crab_pack_wire", "scripts/binggu_crab_pack_wire.py", ["--selftest"], "GATE=GO"),
    ("person_crab_sync", "scripts/binggu_person_crab_sync.py", ["--selftest"], "GATE=GO"),
    ("person_pack_sync", "scripts/binggu_person_pack_sync.py", ["--selftest"], "GATE=GO"),
    ("public_tree_scan", "scripts/openbinggu_public_tree_scan.py", ["--selftest"], "GATE: GO"),
    ("recall", "scripts/binggu_recall.py", ["--selftest"], "GATE=GO"),
    # 회귀 묶음(run_all: P1~P7 + cloud_pack + tree scan)은 여기서 제외한다 — ci.yml selftest job 의
    # "Publish pipeline regression" 스텝과 scripts/ci_local_preflight.py 의 "run_all 회귀" 스텝이
    # 각 환경에서 이미 1회 실행하므로, pytest 경로에서 또 돌리면 run_all 이 2회 실행된다(중복 제거).
    # t3_filter·person_pack_sync 등 위 항목은 run_all GATES 에 없는 유일 커버리지라 유지한다.
]


@pytest.mark.parametrize("script_rel,args,ok_token", [s[1:] for s in SPECS], ids=[s[0] for s in SPECS])
def test_selftest_gate_passes(script_rel: str, args: list[str], ok_token: str) -> None:
    """대상 selftest 스크립트가 exit 0 + 성공 토큰으로 끝나는지 확인."""
    script = REPO_ROOT / script_rel
    assert script.is_file(), f"script not found: {script}"
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=900,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, (
        f"{script_rel} exit={proc.returncode} (expected 0).\n"
        f"--- stdout tail ---\n{proc.stdout[-2000:]}\n"
        f"--- stderr tail ---\n{proc.stderr[-2000:]}"
    )
    assert ok_token in out, (
        f"{script_rel}: success token {ok_token!r} not found in output.\n"
        f"--- output tail ---\n{out[-2000:]}"
    )
