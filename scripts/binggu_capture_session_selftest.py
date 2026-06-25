# -*- coding: utf-8 -*-
"""Characterization selftest — binggu_capture_session.CaptureSession (v1.11.0 phase6).

이관 전 현행 동작을 고정한다(pre-move characterization). 이관 후 thin wrapper 에서도
동일 PASS 해야 한다. 호출처(binggu_capture_cli)와 동일 import 형태
(from binggu_capture_session import CaptureSession).

CaptureSession 은 CaptureBuffer 를 감싸 on_user_prompt/on_session_end entrypoint 를 제공한다
(메모리만, 영속화 0). session → buffer → classifier 판정 경로.
본 테스트는:
  - 대표/빈/synthetic 입력의 action·shape 고정
  - session 이 buffer 를 생성·사용하는 방식(누적·preview 렌더)
  - session→buffer→classifier 판정 경로(verdict 전달)
  - session 이 쓰는 CaptureBuffer 와 binggupack.capture package CaptureBuffer 가 동일 객체
  - PII/secret-like 입력: verdict 평문 미노출 + 영속화 부작용 0(메모리만)
  - idempotence
read-only(영속). write 0.
"""
import sys

from binggu_capture_session import CaptureSession  # noqa: E402  (호출처와 동일 형태)

_PROMPT_KEYS = {"action", "verdict", "preview"}
_END_KEYS = {"action", "trigger", "preview"}
_VALID_ACTIONS = {"captured", "preview", "ignored"}


def run():
    results = []

    def ck(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # ---- 대표 입력 ----
    s = CaptureSession()
    r1 = s.on_user_prompt("이거 저장해")
    ck("rep_explicit_captured", r1["action"] == "captured" and r1["verdict"]["pinned"] is True, "명시저장→captured+pinned")
    s.on_user_prompt("B안으로 결정")
    s.on_user_prompt("ㅋㅋ 웃기네")
    ck("rep_ignored_not_buffered", s.size == 2, "ignored 누적 안 됨 (size=2)")
    rp = s.on_user_prompt("빙구팩 저장해")
    ck("rep_preview", rp["action"] == "preview" and rp["preview"]["count"] == 2, "preview 액션+렌더")
    ck("rep_pinned_top", rp["preview"]["items"][0]["pinned"] is True, "pinned 상단")
    e = s.on_session_end()
    ck("rep_session_end", e["action"] == "preview" and e["trigger"] == "session_end" and e["preview"]["count"] == 2,
       "세션말 → preview")
    ck("rep_buffer_kept", s.size == 2, "preview 후 버퍼 유지")

    # ---- 빈 입력 ----
    s2 = CaptureSession()
    ck("empty_ignored", s2.on_user_prompt("")["action"] == "ignored" and s2.size == 0, "빈 입력 → ignored, 누적 0")

    # ---- synthetic ----
    s3 = CaptureSession()
    ck("synth_valid", s3.on_user_prompt("synthetic_source#1 placeholder")["action"] in _VALID_ACTIONS, "합성 valid action")

    # ---- shape 고정 ----
    sh = CaptureSession()
    shape_ok = set(sh.on_user_prompt("B안으로 결정").keys()) == _PROMPT_KEYS \
        and set(sh.on_session_end().keys()) == _END_KEYS
    ck("shape_fixed", shape_ok, "on_user_prompt 3키 + on_session_end 3키")

    # ---- session → buffer → classifier 경로: verdict 전달 ----
    sp = CaptureSession()
    v = sp.on_user_prompt("B안으로 결정")["verdict"]
    ck("verdict_path", v.get("state") == "captured_candidate" and "signals" in v, "session이 classify verdict 전달")

    # ---- session 이 쓰는 CaptureBuffer == binggupack.capture package CaptureBuffer (동일 객체) ----
    import binggu_capture_session as ssmod
    from binggupack.capture import CaptureBuffer as pkgBuf
    same_buf = ssmod.CaptureBuffer is pkgBuf and isinstance(CaptureSession().buf, pkgBuf)
    ck("buffer_path_identity", same_buf, "session.CaptureBuffer is binggupack.capture.CaptureBuffer")

    # ---- PII/secret-like: verdict 평문 미노출 + 영속화 부작용 0 ----
    sp2 = CaptureSession()
    pii_safe = True
    pii_detail = ""
    for utt, leaks in [
        ("내 번호 010-1234-5678 이건 저장", ["010-1234-5678"]),
        ("주민번호 901010-1234567 기억해 둬", ["901010-1234567"]),
        ("api key sk-abcd1234efgh5678 이거 저장", ["sk-abcd1234efgh5678"]),
    ]:
        r = sp2.on_user_prompt(utt)
        vblob = repr(r["verdict"])
        for leak in leaks:
            if leak in vblob:
                pii_safe = False; pii_detail = "PII leaked in verdict: %r" % leak; break
        if not pii_safe:
            break
    ck("pii_no_leak_in_verdict", pii_safe, pii_detail or "classify verdict PII 평문 0")
    ck("session_memory_only", not hasattr(sp2, "_path") and not hasattr(sp2, "_db"), "session 영속화 핸들 없음(메모리만)")

    # ---- idempotence ----
    a, b = CaptureSession(), CaptureSession()
    ck("idempotent_verdict", a.on_user_prompt("B안으로 결정")["verdict"] == b.on_user_prompt("B안으로 결정")["verdict"],
       "동일 입력 동일 verdict")

    # ---- 내장 _selftest 전수 ----
    ck("builtin_selftest", ssmod._selftest(), "내장 _selftest 전수 PASS")

    print("=" * 74)
    print("binggu_capture_session characterization selftest (memory-only, persist write 0)")
    print("=" * 74)
    all_ok = True
    for name, ok, detail in results:
        all_ok = all_ok and ok
        print("  [%s] %-30s %s" % ("OK" if ok else "FAIL", name, "" if ok else ("<< " + detail)))
    gate = "GO" if all_ok else "NO-GO"
    print("\n  GATE:", gate)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run())
