# -*- coding: utf-8 -*-
"""Characterization selftest — binggu_platform resolver (v1.11.0 save-gate S1).

이관 전 현행 resolver 동작을 고정한다(pre-move characterization). 이관 후 thin wrapper
에서도 동일 PASS 해야 한다. 호출처와 동일 import 형태(import binggu_platform as P).

binggu_platform 은 순수 함수(write 0, env/os_name 주입형). 본 테스트는:
  - resolver 우선순위: BINGGU_HOME(explicit) > OS별 홈/.binggupack
  - OS별 default(windows/linux/macos/wsl) synthetic 검증
  - default_ledger = <home>/ledger.sqlite
  - deterministic: same input → same path
  - **no auto-create**: 경로 계산만, FS 생성 0(존재 안 하는 경로 주입 후 미생성 확인)
  - **현행 미지원 고정(정직)**: BINGGUPACK_LEDGER 미반영 · project .binggupack 자동탐색 없음
    (= 자동 추측 금지 안전 동작 — 이관 시 이 미지원 동작을 그대로 보존, 새 기능 추가 0)
  - split-brain 정합: binggu_home 은 단일 resolver(같은 env→같은 path), gate/ledger 동일 source
read-only. write 0.
"""
import os
import sys

import binggu_platform as P  # noqa: E402  (호출처 17개와 동일 형태)


def run():
    results = []

    def ck(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # ---- 우선순위 1: BINGGU_HOME explicit 우선 ----
    ck("explicit_BINGGU_HOME_win",
       P.binggu_home(env={"BINGGU_HOME": "C:\\custom\\home"}, os_name="windows") == "C:\\custom\\home",
       "BINGGU_HOME 명시 → 그대로")
    ck("explicit_BINGGU_HOME_posix",
       P.binggu_home(env={"BINGGU_HOME": "/srv/binggu"}, os_name="linux") == "/srv/binggu",
       "BINGGU_HOME posix")

    # ---- 우선순위 2: 미설정 → OS별 홈/.binggupack ----
    ck("default_linux", P.binggu_home(env={"HOME": "/home/fixture-user"}, os_name="linux") == "/home/fixture-user/.binggupack",
       "linux 글로벌 fallback")
    ck("default_macos", P.binggu_home(env={"HOME": "/Users/fixture-user"}, os_name="macos") == "/Users/fixture-user/.binggupack",
       "macos 글로벌 fallback")
    ck("default_windows",
       P.binggu_home(env={"USERPROFILE": "C:\\Users\\fixture-user"}, os_name="windows") == "C:\\Users\\fixture-user\\.binggupack",
       "windows %USERPROFILE%")
    ck("default_wsl", P.binggu_home(env={"HOME": "/home/fixture-user"}, os_name="wsl") == "/home/fixture-user/.binggupack",
       "wsl 글로벌 fallback")

    # ---- default_ledger = home/ledger.sqlite ----
    led = P.default_ledger(env={"BINGGU_HOME": "/srv/binggu"}, os_name="linux")
    ck("default_ledger", led == "/srv/binggu/ledger.sqlite", "ledger = home/ledger.sqlite")

    # ---- deterministic: 같은 입력 2회 동일 ----
    e = {"BINGGU_HOME": "/srv/x"}
    ck("deterministic", P.binggu_home(env=e, os_name="linux") == P.binggu_home(env=e, os_name="linux"),
       "same input → same path")

    # ---- no auto-create: 존재 안 하는 경로 주입 → 반환만, FS 미생성 ----
    ghost = os.path.join(os.environ.get("TEMP", "/tmp"), "bgp_ghost_home_does_not_exist_zzz")
    h = P.binggu_home(env={"BINGGU_HOME": ghost}, os_name=P.detect_os())
    ck("no_auto_create", h == ghost and not os.path.exists(ghost), "경로 계산만(FS 생성 0)")
    led2 = P.default_ledger(env={"BINGGU_HOME": ghost}, os_name=P.detect_os())
    ck("no_ledger_create", not os.path.exists(led2), "ledger 경로 계산만(생성 0)")

    # ---- 현행 미지원 고정 (정직): BINGGUPACK_LEDGER 미반영 ----
    # env에 BINGGUPACK_LEDGER 넣어도 binggu_home은 BINGGU_HOME/글로벌만 본다(현행).
    hb = P.binggu_home(env={"BINGGUPACK_LEDGER": "/other/led.sqlite", "HOME": "/home/fixture-user"}, os_name="linux")
    ck("BINGGUPACK_LEDGER_not_supported", hb == "/home/fixture-user/.binggupack",
       "BINGGUPACK_LEDGER 미반영(현행 — 글로벌 fallback)")

    # ---- 현행 미지원 고정: project .binggupack 자동탐색 없음 (CWD 무관) ----
    # binggu_home은 CWD를 보지 않는다(자동 추측 금지 = 안전). env만으로 결정.
    h_cwd1 = P.binggu_home(env={"HOME": "/home/fixture-user"}, os_name="linux")
    ck("no_project_autodiscover", h_cwd1 == "/home/fixture-user/.binggupack",
       "project .binggupack 자동탐색 없음(CWD 무관·자동추측 금지)")

    # ---- shared_opt_in: BINGGU_HOME 유무 ----
    ck("shared_opt_in_true", P.shared_opt_in(env={"BINGGU_HOME": "/x"}) is True, "BINGGU_HOME 있으면 공유 opt-in")
    ck("shared_opt_in_false", P.shared_opt_in(env={"HOME": "/h"}) is False, "BINGGU_HOME 없으면 False(자동추측 0)")

    # ---- lock_path_for ----
    ck("lock_path", P.lock_path_for("/srv/x/ledger.sqlite") == "/srv/x/ledger.sqlite.lock", "lock = ledger+.lock")

    # ---- split-brain 정합: 단일 resolver(같은 env → gate/ledger 동일 source) ----
    e2 = {"BINGGU_HOME": "/srv/single"}
    home_v = P.binggu_home(env=e2, os_name="linux")
    led_v = P.default_ledger(env=e2, os_name="linux")
    ck("single_resolver_scope", led_v.startswith(home_v + "/"),
       "ledger가 binggu_home 하위(gate/ledger 동일 home source — split-brain 차단 기반)")

    # ---- 운영 home write 0 (전 케이스 후 ~/.binggupack 미생성/미변경은 호출측에서 mtime 측정) ----
    ck("pure_no_write", not hasattr(P, "_write") and not hasattr(P, "save"), "platform에 write/save 심볼 없음(순수)")

    print("=" * 74)
    print("binggu_platform resolver characterization selftest (pure, write 0)")
    print("=" * 74)
    all_ok = True
    for name, ok, detail in results:
        all_ok = all_ok and ok
        print("  [%s] %-32s %s" % ("OK" if ok else "FAIL", name, "" if ok else ("<< " + detail)))
    gate = "GO" if all_ok else "NO-GO"
    print("\n  GATE:", gate)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run())
