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

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.environ.get("BINGGU_PYTHON", r"C:/Users/PC/AppData/Local/Programs/Python/Python314/python")


def _run(args):
    return subprocess.run([PY, os.path.join(BASE, "binggu.py")] + args,
                          capture_output=True, text=True, encoding="utf-8", cwd=BASE)


def main():
    # 1) inbox 회수 + 요약(번호별 후보 수)
    out = _run(["hosted", "inbox"]).stdout or ""
    print(out)
    committed = 0
    # "[N] ... 후보 M" 파싱 — M>0 만 commit
    for m in re.finditer(r"\[(\d+)\].*?후보 (\d+)", out):
        n, cand = m.group(1), int(m.group(2))
        if cand > 0:
            r = _run(["hosted", "pull", "--select", n, "--confirm", "LIVE SAVE " + n])
            print(r.stdout)
            if "applied=1" in (r.stdout or ""):
                committed += 1
    print("=== auto_pull 완료: %d 건 로컬 반영 ===" % committed)


if __name__ == "__main__":
    main()
