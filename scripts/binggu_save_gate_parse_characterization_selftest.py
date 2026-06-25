# -*- coding: utf-8 -*-
"""Characterization selftest — binggu_save_gate.parse_save_indices (v1.11.0 save-gate S3-B).

이관 전 현행 동작을 고정한다(pre-move characterization). 이관 후 thin wrapper(save_gate가
binggupack.safety.gate_text 에서 import)에서도 동일 PASS 해야 한다. 호출처와 동일 import
형태(from binggu_save_gate import parse_save_indices).

parse_save_indices 는 순수 텍스트 파싱(write 0·ledger 0·actor/confirm 0). 발화 전체가 정확히
'<트리거> n' / '<트리거> 1,3' 형태(SAVE/저장/세이브·대소문자 무시·fullmatch)일 때만 인덱스 리스트,
아니면 None. 본 테스트는:
  - 대표 파싱(영문/한글 트리거·복수 인덱스)
  - 대소문자/공백 변형
  - 부정문/문장 내 SAVE-like/인용 → None(fullmatch 아님)
  - 빈/None → None
  - PII-like 입력에도 원문 평문 미노출(int 리스트만 반환)
  - deterministic·output shape(list[int] | None) 고정
read-only. write 0.
"""
import sys

from binggu_save_gate import parse_save_indices  # noqa: E402  (호출처와 동일 형태)


def run():
    results = []

    def ck(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # ---- 대표 파싱 ----
    ck("save_1", parse_save_indices("SAVE 1") == [1], "영문 단일")
    ck("save_multi", parse_save_indices("SAVE 1,2,3") == [1, 2, 3], "영문 복수")
    ck("ko_저장_1", parse_save_indices("저장 1") == [1], "한글 저장")
    ck("ko_저장_multi", parse_save_indices("저장 1,3") == [1, 3], "한글 복수")
    ck("ko_세이브_2", parse_save_indices("세이브 2") == [2], "한글 세이브")

    # ---- 대소문자/공백 변형 ----
    ck("lower_save", parse_save_indices("save 1") == [1], "소문자")
    ck("mixed_case", parse_save_indices("Save 1") == [1], "혼합 대소문자")
    ck("no_space", parse_save_indices("저장1") == [1], "공백 없음")
    ck("extra_space", parse_save_indices("SAVE  1 , 3") == [1, 3], "공백 변형")

    # ---- 중복/큰 숫자(현행 동작 고정) ----
    ck("dup_indices", parse_save_indices("SAVE 1,1") == [1, 1], "중복 숫자 현행(그대로 반환)")
    ck("large_number", parse_save_indices("SAVE 99") == [99], "큰 숫자(범위 검증은 save_selected 책임)")

    # ---- None: 부정/문장내/인용/숫자없음 ----
    ck("negation_ko", parse_save_indices("저장 하지마") is None, "한글 부정문")
    ck("negation_en", parse_save_indices("SAVE 안 해") is None, "영문+부정")
    ck("in_sentence_en", parse_save_indices("아 그거 save 7 말이야") is None, "문장 내 save-like")
    ck("in_sentence_ko", parse_save_indices("그거 저장 7 어쩌고") is None, "문장 내 저장-like")
    ck("no_number", parse_save_indices("SAVE") is None, "숫자 없음")
    ck("trigger_only_ko", parse_save_indices("저장해줘") is None, "트리거만(숫자 없음)")

    # ---- 빈/None ----
    ck("empty", parse_save_indices("") is None, "빈 입력")
    ck("none_input", parse_save_indices(None) is None, "None 입력")

    # ---- PII-like: 원문 평문 미노출(int 리스트만) ----
    # "저장 01012345678" 류는 fullmatch 시 int 리스트 반환 — 원문 문자열은 결과에 없음(int 변환).
    r_pii = parse_save_indices("저장 01012345678")
    pii_ok = (r_pii is None) or (isinstance(r_pii, list) and all(isinstance(x, int) for x in r_pii)
                                 and "01012345678" not in str(r_pii))
    ck("pii_no_raw_leak", pii_ok, "PII-like 입력도 int 리스트만(원문 평문 0) 또는 None")
    # 인덱스 자리에 비숫자 PII가 섞이면 fullmatch 실패 → None
    ck("pii_email_none", parse_save_indices("저장 user@example.com") is None, "이메일 섞이면 None")

    # ---- deterministic ----
    ck("deterministic", parse_save_indices("SAVE 1,3") == parse_save_indices("SAVE 1,3"), "2회 동일")

    # ---- output shape 고정 (list[int] | None) ----
    shape_ok = True
    for inp in ["SAVE 1", "저장 1,2", "SAVE 안 해", "", None, "저장해줘"]:
        r = parse_save_indices(inp)
        if not (r is None or (isinstance(r, list) and all(isinstance(x, int) for x in r))):
            shape_ok = False; break
    ck("output_shape", shape_ok, "list[int] | None 고정")

    print("=" * 74)
    print("binggu_save_gate.parse_save_indices characterization (pure, write 0)")
    print("=" * 74)
    all_ok = True
    for name, ok, detail in results:
        all_ok = all_ok and ok
        print("  [%s] %-22s %s" % ("OK" if ok else "FAIL", name, "" if ok else ("<< " + detail)))
    gate = "GO" if all_ok else "NO-GO"
    print("\n  GATE:", gate)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run())
