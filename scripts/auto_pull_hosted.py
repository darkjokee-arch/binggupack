#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_pull_hosted.py — schtasks 주기 실행용 자동 회수(staging-only).

ChatGPT/claude 채팅에서 저장한 것(클라우드 inbox 적재분)을 주기적으로 회수해
로컬 staging 에 적재만 한다. 실제 로컬 장부(ledger) 확정은 PC 에서 사람이 'SAVE n'
을 쳐야 이뤄진다 — auto_pull 은 회수만, 무인 commit 은 하지 않는다.

(2026-07-18 staging-only 전환: 과거엔 원격 confirm 을 사람-증거로 신뢰해 무인 commit
 했으나, 안전 약속["hosted 확정은 항상 PC 에서 사람 SAVE n"]을 문서로 낮추지 말고
 코드가 지키게 하는 방향[owner 원칙]으로 되돌림. hosted CLI 설계["collect broad,
 commit narrow · no autopull no autosave"]와도 정합.)

안전: 회수(drain)는 HMAC 서명(.dev.vars.save_mcp)으로만 가능 — 인증 없으면 no-op.
PII/secret flag 후보도 staging 에만 남고 사람 검토로 위임(무인 반영 0).
"""
from contextlib import suppress
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
    with suppress(Exception):
        os.makedirs(HOME_DIR, exist_ok=True)
        with open(os.path.join(HOME_DIR, "auto_pull.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), msg))


def _run(args):
    return subprocess.run([PY, os.path.join(BASE, "binggu.py")] + args,
                          capture_output=True, text=True, encoding="utf-8", cwd=BASE)


def main():
    # 1) inbox 회수 → staging 적재만(무인 commit 없음). 실제 ledger 확정은 PC 에서 사람 'SAVE n'.
    #    --no-anchor: 무인 렌더는 사람 SAVE 앵커(last_preview)를 덮지 않는다 — owner 가 CLI preview
    #    를 보고 '세이브 n' 을 치기 전에 5분 틱이 앵커를 갈아치우던 결함 봉합(2026-07-13 실측).
    #    (2026-07-18 staging-only: 과거엔 여기서 hosted pull --confirm 로 무인 commit 했으나,
    #     안전 약속["hosted 확정은 항상 PC SAVE n"]을 코드가 지키도록 회수만 하고 확정은 사람에 위임.
    #     사람은 PC 에서 `binggu hosted inbox` 확인 후 `hosted pull --select n --confirm "SAVE n"`
    #     또는 세션 '세이브 n' 발화로 ledger 에 확정한다.)
    out = _run(["hosted", "inbox", "--no-anchor"]).stdout or ""
    print(out)
    staged = 0
    # "[N] ... 후보 M[ ⚠PII/secret]" 파싱 — 회수돼 staging 대기 중인 후보 수 집계만. ledger commit 0.
    for m in re.finditer(r"\[(\d+)\].*?후보 (\d+)([^\n]*)", out):
        cand, tail = int(m.group(2)), m.group(3)
        if cand > 0 and "PII/secret" not in tail:
            staged += 1
    print("=== auto_pull 완료: %d 건 staging 회수(로컬 확정 대기 — PC 에서 'SAVE n') ===" % staged)
    _log("auto_pull done staged=%d (staging-only · 사람 SAVE n 확정 대기)" % staged)

    # 2) owner 온톨로지 CrabAgent 스키마 동기화 — person_pack.json crab_auto_sync:true
    #    옵트인 시에만 live(없으면 DISABLED_AUTO)·변화 없으면 NO_CHANGE 로 네트워크 0.
    #    best-effort: 실패해도 auto_pull 본연(inbox 회수)에는 영향 없음.
    try:
        r = subprocess.run([PY, os.path.join(BASE, "scripts", "binggu_person_crab_sync.py"), "--auto"],
                           capture_output=True, text=True, encoding="utf-8", cwd=BASE,
                           timeout=600)
        tail = (r.stdout or "").strip().splitlines()
        if tail:
            _log("person_crab_sync auto: %s" % tail[-1][:200])
        else:  # stdout 부재 시 stderr 꼬리도 남긴다 — rc 만 남는 원인 은폐 방지
            err = (r.stderr or "").strip().splitlines()
            _log("person_crab_sync auto rc=%s stderr: %s" % (r.returncode, err[-1][:200] if err else "-"))
    except Exception as ex:  # noqa — 동기화 실패가 pull 을 깨지 않게
        _log("person_crab_sync auto ERR: %s" % type(ex).__name__)


if __name__ == "__main__":
    main()
