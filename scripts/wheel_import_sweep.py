#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wheel import sweep — 설치본 자족성(self-containment) 게이트.

wheel 로 설치된 env 에서 binggu(top-level)·binggupack.*·scripts.* 전 모듈을
**모듈별 fresh subprocess** 로 단독 import 해, repo checkout 없이도 전부 import 되는지
검증한다(2026-07-13 결함: tests/·scripts sys.path 의존이 wheel 에서 ModuleNotFoundError).

핵심 격리(우연 통과 차단):
  - subprocess cwd = 저장소 밖 temp 디렉토리 → sys.path[0] 에 repo 가 실리지 않음
  - PYTHONPATH 제거 → 호출 셸의 경로 오염 차단
  - BINGGU_HOME = temp → 운영 홈(~/.binggupack) 미접촉

import 계약 2단(모듈 소비 방식 그대로 검증):
  - binggu·binggupack.* : dotted 단독 import 필수(strict)
  - scripts.*           : dotted 단독 우선 · 실패 시 역사적 소비 계약(binggu.py 가
    <install-root>/scripts 를 sys.path 에 삽입 → bare name import)으로 재시도.
    양쪽 다 실패해야 FAIL — 자체 sys.path shim 없는 선존 scripts 모듈(2026-07-13 실측 11개,
    전부 bare 계약으론 정상)이 게이트를 영구 red 로 만들지 않게 하되, 파일 누락·문법
    오류·binggupack 의존 붕괴는 여전히 잡는다. legacy-only 통과 건수는 drift 관찰용으로 출력.

사용:
  python wheel_import_sweep.py                # 현재 인터프리터의 설치본 검사
  python wheel_import_sweep.py --site <dir>   # 명시한 site-packages 검사(같은 인터프리터로 import)

실패 목록을 출력하고 실패 > 0 이면 exit 1 (CI 게이트). 이 스크립트 자체는 repo 밖 cwd 에서
실행해야 한다(테스트 대상 env 의 python 으로).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile


def _module_names(pkg_dir: str, pkg_name: str) -> list[str]:
    """패키지 디렉토리에서 import 가능한 전 모듈명을 열거.

    __init__.py 없는 하위 디렉토리(예: scripts/hybrid_agi = package-data)는 제외.
    비식별자 파일명·__pycache__ 제외. 패키지 자신(pkg_name)도 포함.
    """
    names = [pkg_name]
    for root, dirs, files in os.walk(pkg_dir):
        rel = os.path.relpath(root, pkg_dir)
        if rel == ".":
            parts: list[str] = []
        else:
            parts = rel.replace("\\", "/").split("/")
            # 중간 경로 전부가 패키지(__init__.py 보유)여야 dotted import 가능
            probe = pkg_dir
            is_pkg_chain = True
            for p in parts:
                probe = os.path.join(probe, p)
                if not os.path.isfile(os.path.join(probe, "__init__.py")):
                    is_pkg_chain = False
                    break
            if not is_pkg_chain or not all(p.isidentifier() for p in parts):
                dirs[:] = []
                continue
            names.append(".".join([pkg_name] + parts))
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py") or f == "__init__.py":
                continue
            stem = f[:-3]
            if not stem.isidentifier():
                continue
            names.append(".".join([pkg_name] + parts + [stem]))
    return names


def _discover(site: str | None) -> tuple[str | None, str | None, bool]:
    """(binggupack_dir, scripts_dir, has_binggu) — --site 명시 또는 현재 env 에서 발견."""
    if site:
        bp = os.path.join(site, "binggupack")
        sc = os.path.join(site, "scripts")
        return (bp if os.path.isdir(bp) else None,
                sc if os.path.isdir(sc) else None,
                os.path.isfile(os.path.join(site, "binggu.py")))
    import importlib.util
    bp_dir = sc_dir = None
    spec = importlib.util.find_spec("binggupack")
    if spec and spec.submodule_search_locations:
        bp_dir = list(spec.submodule_search_locations)[0]
    spec = importlib.util.find_spec("scripts")
    if spec and spec.submodule_search_locations:
        sc_dir = list(spec.submodule_search_locations)[0]
    has_binggu = importlib.util.find_spec("binggu") is not None
    return bp_dir, sc_dir, has_binggu


def sweep(site: str | None = None, timeout: int = 120) -> int:
    bp_dir, sc_dir, has_binggu = _discover(site)
    modules: list[str] = []
    if has_binggu:
        modules.append("binggu")
    if bp_dir:
        modules += _module_names(bp_dir, "binggupack")
    if sc_dir:
        modules += _module_names(sc_dir, "scripts")
    if not modules:
        print("wheel_import_sweep: 대상 모듈 0 — binggupack/scripts/binggu 를 찾지 못함")
        return 1

    work = tempfile.mkdtemp(prefix="bgp_sweep_cwd_")     # 저장소 밖 cwd (sys.path 오염 차단)
    home = tempfile.mkdtemp(prefix="bgp_sweep_home_")    # 운영 홈 미접촉
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["BINGGU_HOME"] = home
    env.setdefault("PYTHONUTF8", "1")

    def _try(cmd_args: list[str]) -> tuple[bool, str]:
        try:
            r = subprocess.run(cmd_args, cwd=work, env=env,
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "TIMEOUT %ss" % timeout
        if r.returncode == 0:
            return True, ""
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        return False, (tail[-1] if tail else "exit %s" % r.returncode)

    failures: list[tuple[str, str]] = []
    legacy_only: list[str] = []
    for m in sorted(modules):
        ok, why = _try([sys.executable, "-c",
                        "import importlib,sys; importlib.import_module(sys.argv[1])", m])
        if ok:
            continue
        if m.startswith("scripts.") and sc_dir:
            # 역사적 소비 계약: scripts/ 를 sys.path 에 얹고 bare name 으로 import(binggu.py 와 동일)
            ok2, why2 = _try([sys.executable, "-c",
                              "import sys; sys.path.insert(0, sys.argv[2]); "
                              "import importlib; importlib.import_module(sys.argv[1])",
                              m.split(".", 1)[1], sc_dir])
            if ok2:
                legacy_only.append(m)
                continue
            why = "dotted: %s | bare: %s" % (why, why2)
        failures.append((m, why))

    print("wheel_import_sweep: %d modules · %d failed · %d legacy-bare-only"
          % (len(modules), len(failures), len(legacy_only)))
    for m in legacy_only:
        print("  [LEGACY] %s — dotted 단독 불가·bare(scripts sys.path) 계약으론 정상" % m)
    for m, why in failures:
        print("  [FAIL] %s — %s" % (m, why))
    print("GATE:", "GO" if not failures else "NO-GO")
    return 0 if not failures else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="wheel 설치본 전 모듈 fresh-subprocess import 게이트")
    ap.add_argument("--site", default=None, help="검사할 site-packages 경로(기본: 현재 env 에서 발견)")
    ap.add_argument("--timeout", type=int, default=120, help="모듈당 import 타임아웃 초(기본 120)")
    a = ap.parse_args()
    return sweep(a.site, a.timeout)


if __name__ == "__main__":
    sys.exit(main())
