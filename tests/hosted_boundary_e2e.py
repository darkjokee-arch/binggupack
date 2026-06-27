# -*- coding: utf-8 -*-
"""hosted 신뢰 경계 로컬 E2E — 한 명령으로 클라우드 inbox 경계 속성을 검증.

hosted save-intent 는 대화 텍스트를 잠깐 클라우드 inbox 에 둔다(pull 전까지). 그 경계
(TTL/삭제/원문 보관 범위/pull 후 purge)를 코드가 실제로 지키는지 로컬 selftest 로 모아 검증한다.
경계 명세: docs/BINGGUPACK_HOSTED_BOUNDARY.md

이 묶음은 doctor 게이트가 안 보던 hosted 경로의 회귀 가드다(SSOT 게이트가 hosted 를 깨뜨린
회귀를 doctor 가 못 잡은 사건 이후 추가 — 2026-06-27).

실행: python tests/hosted_boundary_e2e.py        # 전부 GO 면 exit 0
"""
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# (제목, 스크립트, 이 selftest 가 보증하는 경계 속성)
SUITES = [
    ("outbox runner", "scripts/openbinggu_save_intent_outbox_runner.py",
     "TTL 만료 intent 저장 BLOCK · tamper/confirm 거부 · 마킹(.expired/.rejected) 원문 미보관(sha만) · 마킹 TTL purge"),
    ("hosted inbox", "scripts/binggu_hosted_inbox.py",
     "pull = 선택분만 commit(narrow) · 적용분 staging 제거 · 만료 표시 · PII/secret 후보 제외 · 장부 write 0"),
]


def run_suite(script):
    p = subprocess.run([sys.executable, os.path.join(_ROOT, script), "--selftest"],
                       capture_output=True, text=True, cwd=_ROOT)
    out = p.stdout + p.stderr
    go = ("GATE: GO" in out) or ("GATE=GO" in out)
    # 결과 요약 줄만 추림
    tail = [ln for ln in out.splitlines() if "RESULT" in ln or "GATE" in ln]
    return go, tail


def main():
    print("=" * 78)
    print("hosted 신뢰 경계 로컬 E2E — 클라우드 inbox 경계 검증")
    print("=" * 78)
    all_go = True
    for title, script, props in SUITES:
        go, tail = run_suite(script)
        all_go = all_go and go
        print("\n[%s]  %s" % ("GO" if go else "NO-GO", title))
        print("  보증: %s" % props)
        for ln in tail:
            print("    " + ln.strip())
    print("\n" + "-" * 78)
    print("경계 요약(명세=docs/BINGGUPACK_HOSTED_BOUNDARY.md):")
    print("  TTL 24h 기본·7일 상한 · 만료=삭제(클라우드 DO storage.delete, 로컬 .expired BLOCK)")
    print("  pull=drain(클라우드 atomic read+delete) / 적용분 staging 제거(로컬) — pull 후 잔존 0")
    print("  원문 보관: 클라우드 payload 로깅 0 · 로컬 마킹 파일 원문 미보관(sha만)")
    print("  ⚠ 저장 암호화 없음 — 전송 TLS + HMAC 무결성. 보완: 민감정보 preview 단계 제외 · TTL 짧음")
    print("\nE2E GATE:", "GO" if all_go else "NO-GO")
    sys.exit(0 if all_go else 1)


if __name__ == "__main__":
    main()
