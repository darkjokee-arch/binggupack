#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_pull_hosted.py — schtasks 주기 실행용 자동 pull.

ChatGPT/claude 채팅에서 저장한 것(클라우드 inbox 적재분)을 주기적으로 회수해
로컬 장부에 반영한다. hosted 저장 시 이미 사람이 confirm('SAVE n')을 넣었으므로
그 confirm 을 사람-증거로 신뢰해 로컬 commit 을 자동화한다(이중 confirm 생략).

안전: 후보>0(판단/지식으로 분류된) intent 만 commit. 후보 0(짧은 조각 등)은 skip.
inbox 회수(drain)는 HMAC 서명(.dev.vars.save_mcp)으로만 가능 — 인증 없으면 no-op.
"""
import subprocess
import re
import os
import sys
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 사용자 무관 기본값: env 우선 → 이 프로세스의 파이썬(sys.executable — pythonw 여도 파이프 정상)
PY = os.environ.get("BINGGU_PYTHON") or sys.executable or "python"
HOME_DIR = os.environ.get("BINGGU_HOME") or os.path.join(os.path.expanduser("~"), ".binggupack")

# Windows: 이 프로세스가 띄우는 모든 자식 콘솔 창 억제.
# 스케줄러(5분 주기)가 이 스크립트를 pythonw 로 실행해도, 자식(binggu.py 등)이
# 새 콘솔 창을 할당하면 cmd 가 깜빡인다. subprocess.Popen 을 CREATE_NO_WINDOW 로
# 전역 패치해 subprocess.run/call/check_output 전부에 적용(무인 실행 창 0).
if os.name == "nt":
    _CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    _orig_popen_init = subprocess.Popen.__init__

    def _no_window_popen_init(self, *a, **k):
        k["creationflags"] = k.get("creationflags", 0) | _CREATE_NO_WINDOW
        _orig_popen_init(self, *a, **k)

    subprocess.Popen.__init__ = _no_window_popen_init


def _log(msg):
    """pythonw 실행 시 stdout 이 안 보이므로 결과를 로그 파일에 남긴다(무인 추적)."""
    try:
        os.makedirs(HOME_DIR, exist_ok=True)
        with open(os.path.join(HOME_DIR, "auto_pull.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), msg))
    except Exception:
        pass


def _run(args):
    return subprocess.run([PY, os.path.join(BASE, "binggu.py")] + args,
                          capture_output=True, text=True, encoding="utf-8", cwd=BASE)


def main():
    # 1) inbox 회수 + 요약(번호별 후보 수)
    out = _run(["hosted", "inbox"]).stdout or ""
    print(out)
    committed = 0
    # "[N] ... 후보 M[ ⚠PII/secret]" 파싱 — M>0 만 commit.
    # 같은 라인 꼬리(후보 뒤)에 render_summary_md 의 PII/secret flag 가 있으면 auto-commit SKIP
    # (사람 직접 검토로 위임 — 원문 PII 자동 반영 방지). save_selected 재스캔 뒤 이중 백스톱.
    for m in re.finditer(r"\[(\d+)\].*?후보 (\d+)([^\n]*)", out):
        n, cand, tail = m.group(1), int(m.group(2)), m.group(3)
        if "PII/secret" in tail:
            print("skip [%s]: PII/secret flagged — 자동 commit 제외(사람 검토)" % n)
            continue
        if cand > 0:
            r = _run(["hosted", "pull", "--select", n, "--confirm", "LIVE SAVE " + n])
            print(r.stdout)
            if "applied=1" in (r.stdout or ""):
                committed += 1
    print("=== auto_pull 완료: %d 건 로컬 반영 ===" % committed)
    _log("auto_pull done committed=%d" % committed)

    # 2) owner 온톨로지 CrabAgent 스키마 동기화 — person_pack.json crab_auto_sync:true
    #    옵트인 시에만 live(없으면 DISABLED_AUTO)·변화 없으면 NO_CHANGE 로 네트워크 0.
    #    best-effort: 실패해도 auto_pull 본연(inbox 회수)에는 영향 없음.
    try:
        r = subprocess.run([PY, os.path.join(BASE, "binggu_person_crab_sync.py"), "--auto"],
                           capture_output=True, text=True, encoding="utf-8", cwd=BASE,
                           timeout=600)
        tail = (r.stdout or "").strip().splitlines()
        _log("person_crab_sync auto: %s" % (tail[-1][:200] if tail else "no-output rc=%s" % r.returncode))
    except Exception as ex:  # noqa — 동기화 실패가 pull 을 깨지 않게
        _log("person_crab_sync auto ERR: %s" % type(ex).__name__)


if __name__ == "__main__":
    main()
