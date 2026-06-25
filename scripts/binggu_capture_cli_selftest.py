# -*- coding: utf-8 -*-
"""Characterization selftest — binggu_capture_cli (v1.11.0 phase7).

이관 전 현행 동작을 고정한다(pre-move characterization). 이관 후 thin wrapper 에서도
동일 PASS 해야 한다. cli 는 leaf entrypoint(외부 호출처 0). run_batch 가 CaptureSession 을
무상태로 돌려 preview 를 반환한다(메모리만, ledger/파일/네트워크 write 0).

본 테스트는:
  - 대표/빈/synthetic 입력의 trigger·count·shape·exit code 고정
  - session → buffer → classifier 경로(run_batch 가 CaptureSession 사용)
  - cli 가 쓰는 CaptureSession 과 binggupack.capture package CaptureSession 동일 객체
  - PII/secret-like 입력: 파일/네트워크 저장·외부반영 0(메모리만) + 자동 save/confirm/actor 경로 0
  - 모든 preview item 이 captured_candidate(active/confirmed/save 전이 0)
  - idempotence
read-only(영속). write 0.
"""
import sys

import binggu_capture_cli as cli  # noqa: E402
from binggu_capture_cli import run_batch  # noqa: E402

_OUT_KEYS = {"trigger", "buffer_size", "preview", "note"}


def run():
    results = []

    def ck(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # ---- 대표: 명시 트리거 ----
    o1 = run_batch(["B안으로 결정", "이거 저장해", "빙구팩 저장해", "ㅋㅋ 농담"])
    ck("explicit_trigger", o1["trigger"] == "explicit" and o1["preview"]["count"] == 2, "빙구팩 저장해→explicit 2개")
    ck("pinned_top", o1["preview"]["items"][0]["pinned"] is True, "pinned 상단")
    ck("all_candidate", all(it["state"] == "captured_candidate" for it in o1["preview"]["items"]),
       "전부 candidate(active/confirmed/save 전이 0)")

    # ---- 세션말 경로 ----
    o2 = run_batch(["로컬로 가자", "아마 B가 더 나을 거야, 비용 때문에", "ㅋㅋ"])
    ck("session_end_trigger", o2["trigger"] == "session_end" and o2["preview"]["count"] == 2, "트리거 없음→session_end 2개")
    ck("ignored_not_buffered", o2["buffer_size"] == 2, "ignored 누적 안 됨")

    # ---- 빈 입력 ----
    o3 = run_batch([])
    ck("empty_session_end", o3["trigger"] == "session_end" and o3["preview"]["count"] == 0, "빈 입력→session_end 0개")

    # ---- synthetic ----
    o4 = run_batch(["synthetic_source#1 placeholder"])
    ck("synth_shape", set(o4.keys()) == _OUT_KEYS, "합성 입력 shape 고정")

    # ---- shape 고정 ----
    ck("out_shape_fixed", set(o1.keys()) == _OUT_KEYS and "write 0" in o1["note"], "run_batch 4키 + note write 0")

    # ---- exit code (CLI --selftest) ----
    ck("selftest_exit0", cli._selftest() is True, "내장 _selftest exit 0(GATE GO)")

    # ---- session 경로 정합: cli.CaptureSession is package CaptureSession ----
    from binggupack.capture import CaptureSession as pkgSess
    ck("session_path_identity", cli.CaptureSession is pkgSess, "cli.CaptureSession is binggupack.capture.CaptureSession")

    # ---- PII/secret-like: 파일/네트워크 저장·외부반영 0(메모리만) + 자동 save/actor/confirm 경로 0 ----
    # cli 모듈은 save_gate/actor/confirm 를 import/호출하지 않음(순수 preview).
    no_save_path = not any(hasattr(cli, n) for n in ("gate_human_for", "gate_record", "save_selected", "commit_selected"))
    ck("no_auto_save_path", no_save_path, "cli 에 save_gate/commit 경로 심볼 없음")
    op = run_batch(["내 번호 010-1234-5678 이건 저장", "api key sk-abcd1234efgh5678 이거 박제"])
    # 반환 정상 + 전부 candidate(저장/승격 0) + 파일 핸들 없음(메모리만)
    pii_ok = (set(op.keys()) == _OUT_KEYS
              and all(it["state"] == "captured_candidate" for it in op["preview"]["items"])
              and not hasattr(cli, "_path") and not hasattr(cli, "_db"))
    ck("pii_memory_only_no_save", pii_ok, "PII 입력도 candidate(저장/승격/외부반영 0)·메모리만")

    # ---- idempotence ----
    ck("idempotent", run_batch(["B안으로 결정", "빙구팩 저장해"]) == run_batch(["B안으로 결정", "빙구팩 저장해"]),
       "동일 입력 동일 결과")

    print("=" * 74)
    print("binggu_capture_cli characterization selftest (memory-only, persist write 0)")
    print("=" * 74)
    all_ok = True
    for name, ok, detail in results:
        all_ok = all_ok and ok
        print("  [%s] %-28s %s" % ("OK" if ok else "FAIL", name, "" if ok else ("<< " + detail)))
    gate = "GO" if all_ok else "NO-GO"
    print("\n  GATE:", gate)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run())
