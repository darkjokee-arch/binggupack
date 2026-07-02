#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""빙구팩 경로 정본 (single source of truth).

목적: 여러 scripts 파일에 중복 정의된 _home()/_ledger()/_state_path() 와,
셀프테스트 파일(openbinggu_staging_write_selftest)에 있던 OPERATING_PATHS 를
비-셀프테스트 위치로 정본화한다(결합 위반 해소).

공개 API (기존 로직과 동치):
  home()          == binggu_platform.binggu_home()  (BINGGU_HOME 우선 · 없으면 OS별 홈/.binggupack)
  ledger()        == BINGGU_LEDGER 우선 · 없으면 <home>/ledger.sqlite
  state_path(name)== <home>/<name>
  OPERATING_PATHS == 운영 store 거부 목록 (env OPENBINGGU_* 우선 · 없으면 temp dummy)

주의: 반환형은 str(os.path.join 기반). Path 가 필요한 호출자는 Path(...) 로 감싼다.
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import binggu_platform as _plat  # noqa: E402  OS-aware home 정본(binggupack.workspace.platform re-export)

LEDGER_NAME = "ledger.sqlite"


def home():
    """장부 루트. BINGGU_HOME 우선(opt-in) · 없으면 OS별 홈/.binggupack.

    기존 각 파일 _home()(str) 동치. OS-aware(_plat.binggu_home) 이라
    같은 OS 에서는 os.environ.get("BINGGU_HOME") or ~/.binggupack 와 동일 경로.
    """
    return _plat.binggu_home()


def ledger():
    """기본 장부 sqlite 경로. BINGGU_LEDGER 우선 · 없으면 <home>/ledger.sqlite.

    기존 _ledger()(person_pack_sync) 동치.
    """
    return os.environ.get("BINGGU_LEDGER") or os.path.join(home(), LEDGER_NAME)


def state_path(name):
    """<home>/<name> 상태 파일 경로. 기존 _state_path() (os.path.join(_home(), FILE)) 동치."""
    return os.path.join(home(), name)


# ── 운영 store 거부 목록 (openbinggu_staging_write_selftest 에서 이관 · 셀프테스트 결합 해소) ──
# 운영 store 경로(거부 대상) = OpenCrab user_graph/graph_merge/운영 sqlite.
# env 미설정 시 temp 의 dummy 경로(존재하지 않아도 됨) — "거부 대상 표식" 의미만 유지.
_TMP = tempfile.gettempdir()
OPERATING_PATHS = [
    os.environ.get("OPENBINGGU_USER_GRAPH",  os.path.join(_TMP, "openbinggu_user_graph_dummy.yaml")),
    os.environ.get("OPENBINGGU_GRAPH_MERGE", os.path.join(_TMP, "openbinggu_graph_merge_dummy.yaml")),
    os.environ.get("OPENBINGGU_OPERATING_DB", os.path.join(_TMP, "openbinggu_operating_dummy.sqlite")),
]


def _selftest():
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print(("PASS" if c else "FAIL"), m)

    def _norm(p):
        return os.path.normcase(os.path.abspath(str(p)))

    # 격리: env 백업 후 조작, 마지막 복원
    _saved = {k: os.environ.get(k) for k in ("BINGGU_HOME", "BINGGU_LEDGER")}
    try:
        os.environ.pop("BINGGU_HOME", None)
        os.environ.pop("BINGGU_LEDGER", None)
        # 1) home() == 레거시 simple form(_plat 미경유 재현)
        legacy_home = os.environ.get("BINGGU_HOME") or os.path.join(os.path.expanduser("~"), ".binggupack")
        ck(_norm(home()) == _norm(legacy_home), "home() == legacy _home()")
        # 2) ledger() == <home>/ledger.sqlite
        ck(_norm(ledger()) == _norm(os.path.join(home(), LEDGER_NAME)), "ledger() == <home>/ledger.sqlite")
        # 3) state_path
        ck(state_path("x.json") == os.path.join(home(), "x.json"), "state_path(name) == <home>/name")
        # 4) BINGGU_HOME override
        h = os.path.join(_TMP, "bp_selftest_home")
        os.environ["BINGGU_HOME"] = h
        ck(home() == h, "BINGGU_HOME override -> home()")
        ck(ledger() == os.path.join(h, LEDGER_NAME), "ledger() under BINGGU_HOME")
        # 5) BINGGU_LEDGER override
        lp = os.path.join(_TMP, "bp_selftest.sqlite")
        os.environ["BINGGU_LEDGER"] = lp
        ck(ledger() == lp, "BINGGU_LEDGER override -> ledger()")
        # 6) OPERATING_PATHS shape
        ck(isinstance(OPERATING_PATHS, list) and len(OPERATING_PATHS) == 3, "OPERATING_PATHS is list[3]")
    finally:
        for k, v in _saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("GATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("home   =", home())
    print("ledger =", ledger())
    print("OPERATING_PATHS =", OPERATING_PATHS)
