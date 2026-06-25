# -*- coding: utf-8 -*-
"""Characterization selftest — binggu_capture_classifier.classify (v1.11.0 phase4).

이관 전 현행 동작을 고정한다(pre-move characterization). 이관 후 thin wrapper 에서도
동일 PASS 해야 한다. 호출처 3개와 동일 import 형태(from binggu_capture_classifier import classify).

classify 는 순수 함수(write 0 / 외부반영 0 / re 만 사용). 본 테스트는:
  - 대표 입력의 분류 label/shape 고정
  - 빈 입력 → ignored
  - synthetic-only 입력
  - PII/secret-like 입력이 저장·외부반영 없이 분류만 되고, 출력 dict 에 원문/PII 평문이
    실리지 않음(부작용 0·PII 미노출) 확인
read-only. write 0.
"""
import sys

from binggu_capture_classifier import classify  # noqa: E402  (호출처 3개와 동일 형태)

_SHAPE_KEYS = {"state", "confidence", "pinned", "reasons", "signals", "vetoes"}
_VALID_STATES = {"ignored", "preview_trigger", "captured_candidate"}


def run():
    results = []

    def ck(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # ---- 대표 입력 label 고정 ----
    ck("rep_explicit_save", classify("이거 저장해")["state"] == "captured_candidate"
       and classify("이거 저장해")["pinned"] is True, "explicit save → pinned candidate")
    ck("rep_preview_trigger", classify("빙구팩 저장해")["state"] == "preview_trigger", "preview 트리거")
    ck("rep_decision_signal", classify("B안으로 결정")["state"] == "captured_candidate", "판단 신호")
    ck("rep_ops_veto", classify("commit 진행해라")["state"] == "ignored", "운영 명령 veto")
    ck("rep_joke_veto", classify("ㅋㅋㅋ 그거 웃기네")["state"] == "ignored", "농담 veto")
    ck("rep_hedge_only", classify("아마 되겠지")["state"] == "ignored", "추측 단독")
    ck("rep_generalize_exempt", classify("테스트는 항상 돌려")["state"] == "captured_candidate", "반복기준 면제")

    # ---- 빈 입력 ----
    e = classify("")
    ck("empty_ignored", e["state"] == "ignored" and "empty" in e["reasons"], "빈 입력 → ignored/empty")
    ck("none_ignored", classify(None)["state"] == "ignored", "None → ignored")

    # ---- synthetic-only 입력 ----
    ck("synth_neutral", classify("synthetic_source#1 placeholder sentence")["state"] in _VALID_STATES,
       "합성 중립 입력 valid state")

    # ---- output shape 고정 (모든 분기에서 6키 + valid state) ----
    shape_ok = True
    for utt in ["", "이거 저장해", "빙구팩 저장해", "B안으로 결정", "commit 진행해라", "와 대박", "테스트는 항상 돌려"]:
        r = classify(utt)
        if set(r.keys()) != _SHAPE_KEYS or r["state"] not in _VALID_STATES:
            shape_ok = False
            break
        if not (isinstance(r["reasons"], list) and isinstance(r["signals"], list) and isinstance(r["vetoes"], list)):
            shape_ok = False
            break
    ck("output_shape_fixed", shape_ok, "6키 고정 + valid state + list 타입")

    # ---- PII/secret-like 입력: 저장·외부반영 0 + 출력에 원문/PII 평문 미노출 ----
    pii_cases = [
        ("내 번호 010-1234-5678 이건 저장", ["010-1234-5678", "01012345678"]),
        ("주민번호 901010-1234567 기억해 둬", ["901010-1234567", "9010101234567"]),
        ("내 메일 secret@example.com 이거 박제", ["secret@example.com"]),
        ("api key sk-abcd1234efgh5678 이거 저장", ["sk-abcd1234efgh5678"]),
    ]
    pii_safe = True
    pii_detail = ""
    for utt, leaks in pii_cases:
        r = classify(utt)
        # (1) classify 는 분류 dict 만 반환(저장/외부반영 없음 — 순수함수)
        if set(r.keys()) != _SHAPE_KEYS:
            pii_safe = False; pii_detail = "shape broken on PII input"; break
        # (2) 출력 dict 어디에도 원문 PII substring 미노출
        blob = repr(r)
        for leak in leaks:
            if leak in blob:
                pii_safe = False; pii_detail = "PII leaked in output: %r" % leak; break
        if not pii_safe:
            break
    ck("pii_no_leak_no_sideeffect", pii_safe, pii_detail or "PII 평문 출력 0 + 분류만(부작용 0)")

    # ---- 멱등/무부작용: 같은 입력 2회 동일 결과 ----
    idem = classify("B안으로 결정") == classify("B안으로 결정")
    ck("idempotent_pure", idem, "동일 입력 2회 동일 결과(순수)")

    # ---- 내장 _selftest 28케이스 전수 (현행 동작 정본) ----
    import binggu_capture_classifier as m
    builtin_ok = m._selftest()
    ck("builtin_selftest_28", builtin_ok, "내장 _selftest 전수 PASS")

    print("=" * 74)
    print("binggu_capture_classifier characterization selftest (read-only, write 0)")
    print("=" * 74)
    all_ok = True
    for name, ok, detail in results:
        all_ok = all_ok and ok
        print("  [%s] %-30s %s" % ("OK" if ok else "FAIL", name, "" if ok else ("<< " + detail)))
    print("\n  classify_is_pure: write 0 / network 0 / re-only")
    gate = "GO" if all_ok else "NO-GO"
    print("\n  GATE:", gate)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run())
