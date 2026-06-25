# -*- coding: utf-8 -*-
"""Characterization selftest — binggu_capture_buffer.CaptureBuffer (v1.11.0 phase5).

이관 전 현행 동작을 고정한다(pre-move characterization). 이관 후 thin wrapper 에서도
동일 PASS 해야 한다. 호출처 3개와 동일 import 형태(from binggu_capture_buffer import CaptureBuffer).

CaptureBuffer 는 메모리 내 candidate 누적만(ledger/파일/네트워크 write 0, 영속화 0).
classify 결과를 받아 captured/preview/ignored 로 분기한다.
본 테스트는:
  - 대표/빈/synthetic 입력의 action·label·shape 고정
  - classify 결과가 buffer 에 반영되는 형태(captured 누적·preview 렌더)
  - buffer 가 쓰는 classify 와 binggupack.classifier package classify 가 동일 판정
  - PII/secret-like 입력: 파일/네트워크 저장·외부반영 0(메모리만), classify verdict 에 PII 평문 미노출
  - idempotence(동일 입력 동일 verdict)
read-only(영속). write 0.
"""
import sys

from binggu_capture_buffer import CaptureBuffer  # noqa: E402  (호출처 3개와 동일 형태)

_FEED_KEYS = {"action", "verdict", "preview"}
_VALID_ACTIONS = {"captured", "preview", "ignored"}


def run():
    results = []

    def ck(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # ---- 대표 입력 action 고정 ----
    b = CaptureBuffer()
    r1 = b.feed("이거 저장해")
    ck("rep_explicit_captured", r1["action"] == "captured" and r1["verdict"]["pinned"] is True, "명시저장→captured+pinned")
    r2 = b.feed("B안으로 결정")
    ck("rep_decision_captured", r2["action"] == "captured" and r2["verdict"]["confidence"] == "normal", "결정→captured normal")
    b.feed("ㅋㅋ 웃기네")
    ck("rep_ignored_not_buffered", b.size == 2, "ignored는 누적 안 됨 (size=2)")
    rp = b.feed("빙구팩 저장해")
    ck("rep_preview_action", rp["action"] == "preview" and rp["preview"]["count"] == 2, "preview 액션+렌더")
    ck("rep_pinned_top", rp["preview"]["items"][0]["pinned"] is True, "pinned 상단")
    ck("rep_buffer_kept", b.size == 2, "render 후 버퍼 유지")

    # ---- 빈 입력 ----
    b2 = CaptureBuffer()
    ck("empty_ignored", b2.feed("")["action"] == "ignored" and b2.size == 0, "빈 입력 → ignored, 누적 0")

    # ---- synthetic-only ----
    b3 = CaptureBuffer()
    ck("synth_valid_action", b3.feed("synthetic_source#1 placeholder")["action"] in _VALID_ACTIONS, "합성 valid action")

    # ---- feed/render shape 고정 ----
    shape_ok = True
    bb = CaptureBuffer()
    for utt in ["이거 저장해", "B안으로 결정", "ㅋㅋ", "빙구팩 저장해"]:
        r = bb.feed(utt)
        if set(r.keys()) != _FEED_KEYS or r["action"] not in _VALID_ACTIONS:
            shape_ok = False; break
    pv = bb.render_preview()
    if set(pv.keys()) != {"count", "items", "note"}:
        shape_ok = False
    ck("feed_render_shape_fixed", shape_ok, "feed 3키 + render 3키 + valid action")

    # ---- buffer 의 classify 와 package classify 동일 판정 (import 경로 정합) ----
    import binggu_capture_buffer as bbmod
    from binggupack.classifier import classify as pkg_classify
    same = True
    for utt in ["이거 저장해", "B안으로 결정", "commit 진행해라", "빙구팩 저장해", "와 대박"]:
        if bbmod.classify(utt) != pkg_classify(utt):
            same = False; break
    ck("classify_path_parity", same, "buffer.classify == binggupack.classifier.classify (동일 판정)")

    # ---- PII/secret-like: 파일/네트워크 저장·외부반영 0(메모리만) + classify verdict PII 미노출 ----
    bp = CaptureBuffer()
    pii_safe = True
    pii_detail = ""
    for utt, leaks in [
        ("내 번호 010-1234-5678 이건 저장", ["010-1234-5678"]),
        ("주민번호 901010-1234567 기억해 둬", ["901010-1234567"]),
        ("api key sk-abcd1234efgh5678 이거 저장", ["sk-abcd1234efgh5678"]),
    ]:
        r = bp.feed(utt)
        # verdict(classify 산출)에 PII 평문 미노출
        vblob = repr(r["verdict"])
        for leak in leaks:
            if leak in vblob:
                pii_safe = False; pii_detail = "PII leaked in verdict: %r" % leak; break
        if not pii_safe:
            break
    ck("pii_no_leak_in_verdict", pii_safe, pii_detail or "classify verdict PII 평문 0")
    # 부작용 0: CaptureBuffer 는 메모리 리스트만(파일/네트워크 write 0) — 구조적으로 영속화 경로 없음
    ck("buffer_memory_only", not hasattr(bp, "_path") and not hasattr(bp, "_db"), "buffer 영속화 핸들 없음(메모리만)")

    # ---- idempotence (동일 입력 동일 verdict) ----
    b4, b5 = CaptureBuffer(), CaptureBuffer()
    ck("idempotent_verdict", b4.feed("B안으로 결정")["verdict"] == b5.feed("B안으로 결정")["verdict"], "동일 입력 동일 verdict")

    # ---- 내장 _selftest 전수 ----
    builtin_ok = bbmod._selftest()
    ck("builtin_selftest", builtin_ok, "내장 _selftest 전수 PASS")

    print("=" * 74)
    print("binggu_capture_buffer characterization selftest (memory-only, persist write 0)")
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
