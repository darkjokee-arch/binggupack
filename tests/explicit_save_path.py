# -*- coding: utf-8 -*-
"""명시 저장 경로 면제 — A안 버그픽스 회귀 가드.

제품 계약: 자동 캡처/일반 preview 는 SSOT 판단-veto 를 적용하지만, pair/remember 처럼 사용자가
직접 "이걸 기억해"라고 친 명시 입력은 판단-veto 면제(explicit=True). 단 PII/secret/A0/중복/confirm/
actor 안전 게이트는 절대 완화하지 않는다.

고정 속성:
  1 START_HERE/README pair 예시(판단성 약한 직감 포함)가 명시 경로에서 통과
  2 명시 입력이어도 PII/secret 은 저장 0 으로 차단
  3 자동 preview(explicit=False)는 노이즈 0 유지
  4 명시 preview(explicit=True)는 단순조회도 후보(reason=명시저장) — 단 PII 제외

실행: python tests/explicit_save_path.py
"""
import os
import sys

os.environ["BINGGU_SEMANTIC_OFF"] = "1"
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, _ROOT)

from openbinggu_conversation_candidate_save import _pick_one_node
import openbinggu_conversation_capture_preview as prev

# README/START_HERE 의 pair/remember 예시(자동 SSOT 에서는 후보가 안 되던 직감 문장 포함)
DOC_EXAMPLES = [
    "다음엔 이 거래처 먼저 검토",
    "이 건은 보류한다",
    "그래도 이 건은 응찰한다",
    "데이터가 부족해 보수적 접근이 맞다",
    "다음엔 이 거래처 우선 검토",
    "백업은 항상 먼저 한다",
]


def main():
    results = []

    def rec(name, ok):
        results.append((name, ok))

    # 1) 명시 경로에서 문서 예시 6개 통과
    ok_all = all(isinstance(_pick_one_node(t, 1, "owner"), dict) for t in DOC_EXAMPLES)
    rec("1_doc_examples_pass_explicit(6)", ok_all)

    # 2) 명시 입력이어도 PII/secret 차단(저장 0)
    pii = _pick_one_node("담당자 연락처는 010-" + "1234-5678 이다", 1, "owner")
    sec = _pick_one_node("토큰은 gh" + "p_" + "EXAMPLE000000000000000000 이다", 1, "ai")
    rec("2_pii_blocked_even_explicit", pii == "pii_or_secret")
    rec("2b_secret_blocked_even_explicit", sec == "pii_or_secret")

    # 3) 자동 preview 노이즈 0 (explicit=False)
    auto = prev.capture_preview("상태 보여줘. 커밋 완료했다. 낙찰하한율은 비율이다.")
    rec("3_auto_preview_noise_zero", len(auto["candidates"]) == 0)

    # 4) 명시 preview 는 단순조회도 후보(reason=명시저장), PII 는 제외
    ex = prev.capture_preview("상태 보여줘", explicit=True)
    rec("4_explicit_preview_keeps_query",
        len(ex["candidates"]) == 1 and ex["candidates"][0]["capture_reason"] == "명시저장")
    ex_pii = prev.capture_preview("연락처 010-" + "1234-5678", explicit=True)
    rec("4b_explicit_preview_still_excludes_pii",
        len(ex_pii["candidates"]) == 0
        and any(k.startswith("pii_") for k in ex_pii["excluded_counts"]))

    print("=" * 70)
    print("explicit save path — 명시 입력 판단-veto 면제 + 안전 게이트 유지")
    print("=" * 70)
    npass = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    print("-" * 70)
    gate = "GO" if npass == len(results) else "NO-GO"
    print("RESULT: %d/%d  GATE: %s" % (npass, len(results), gate))
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    main()
