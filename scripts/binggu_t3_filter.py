# -*- coding: utf-8 -*-
"""T3 하드제외 필터 — 클라우드 반출 절대금지(PII · 민감 과거사) 판정.

기록 트랙(자동 캡처 → 클라우드 자동 업로드)의 유일한 강제 안전벨트.
owner 결정(2026-07-02·[[feedback-binggupack-identity-personal-ontology-agi]] §부분개정):
사용자 온톨로지는 헌법 §3(반출 명시승인) 제약을 제외하고 완전자동 업로드하되,
**T3(PII·민감 과거사)만은 코드로 하드 차단**(owner 명시·양보 불가).

- PII: 기존 정본(watcher_batch_m1.batch_redact + scan_residual_pii 이중방어) 재사용.
- 과거사: 민감 개인사 키워드 — 과잉차단=안전(owner 명시: 과소차단이 위험).
- fail-closed: 판정 중 예외/불확실 → 차단(True). 반출 안전을 열어두지 않는다.
- read-only: ledger/운영 store write 0. 순수 판정 함수.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 민감 과거사(T3) — 보수적으로 넓게(과잉차단=안전). 하나라도 매치 시 반출 제외.
# owner 개인사 반출 시 해가 되는 카테고리. 단독 오탐이 심한 초단문(예: "암")은 제외하고
# 병력은 질병/진단/수술/입원/정신과류로 커버.
T3_PAST_TERMS = [
    "빚", "채무", "대출", "파산", "개인회생", "신용불량", "연체", "압류",
    "이혼", "별거", "위자료", "양육권", "불륜",
    "병력", "질병", "진단", "우울증", "공황장애", "정신과", "수술", "입원", "장애등급",
    "전과", "범죄", "고소", "고발", "소송", "합의금", "구속",
    "사망", "부고", "장례", "유산", "상속",
    "중독", "도박",
]
# 긴 키워드 우선 매칭(부분 겹침 안정) — findall 결과 중복은 set 으로 제거.
_T3_PAST_RE = re.compile(
    "|".join(re.escape(t) for t in sorted(T3_PAST_TERMS, key=len, reverse=True)))


def past_hits(text):
    """민감 과거사 매치 키워드(read-only · 중복 제거)."""
    if not text or not isinstance(text, str):
        return []
    return sorted(set(_T3_PAST_RE.findall(text)))


def _pii_present(text):
    """PII 존재 여부(이중방어): 원문 직접 scan + 마스킹 후 잔존 scan.
    어느 쪽이든 걸리면 True(원문에 PII 가 있었다는 신호)."""
    import watcher_batch_m1 as M1
    if M1.scan_residual_pii(text):
        return True
    red = M1.batch_redact(text)[0]   # (redacted, hits, review)
    return bool(M1.scan_residual_pii(red))


def is_t3_blocked(text):
    """T3(반출 절대금지) 판정 — True=클라우드 반출 제외.
    PII(이중방어) OR 민감 과거사 매치 시 차단. fail-closed(예외 시 True)."""
    try:
        if not text or not isinstance(text, str):
            return False
        if _pii_present(text):
            return True
        if past_hits(text):
            return True
        return False
    except Exception:
        return True  # fail-closed: 불확실하면 반출 차단


def t3_report(text):
    """차단 판정 상세(read-only) — {blocked, pii, past}."""
    try:
        pii = _pii_present(text or "")
        past = past_hits(text or "")
        return {"blocked": bool(pii or past), "pii": pii, "past": past}
    except Exception:
        return {"blocked": True, "pii": None, "past": [], "error": True}


def filter_uploadable(items, text_key="sentence"):
    """업로드 후보에서 T3 통과분만 선별(read-only). T3 차단분은 사유와 함께 분리.
    반환 {ok: [...통과...], blocked: [{item, report}...]}."""
    ok, blocked = [], []
    for it in items or []:
        text = it.get(text_key, "") if isinstance(it, dict) else str(it)
        rep = t3_report(text)
        if rep["blocked"]:
            blocked.append({"item": it, "report": rep})
        else:
            ok.append(it)
    return {"ok": ok, "blocked": blocked}


# ---------------- selftest ----------------
def _selftest():
    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    # T1 일반 판단/선호 통과(비차단)
    check(not is_t3_blocked("검증 없이 배포하면 실패한다"), "T1 일반 판단 통과(비차단)")
    check(not is_t3_blocked("결론부터 짧게 3줄 이내로 답한다"), "T1b owner 원칙 통과(비차단)")
    # T2 과거사 차단
    check(is_t3_blocked("작년에 빚 때문에 대출을 받았다"), "T2 과거사(빚/대출) 차단")
    check(is_t3_blocked("이혼 후 양육권 소송 중이다"), "T2b 과거사(이혼/소송) 차단")
    check(is_t3_blocked("우울증 진단으로 정신과 다닌다"), "T2c 과거사(병력) 차단")
    # T3 PII 차단(이중방어)
    check(is_t3_blocked("연락처는 010-1234-5678 이다"), "T3 PII(휴대폰) 차단")
    check(is_t3_blocked("메일 주소는 test@example.com 이다"), "T3b PII(이메일) 차단")
    # T4 빈/None graceful
    check(not is_t3_blocked(""), "T4 빈 문자열 비차단")
    check(not is_t3_blocked(None), "T4b None 비차단")
    # T5 report 상세(pii+past 동시)
    r = t3_report("빚 때문에 010-1234-5678 로 연락했다")
    check(r["blocked"] and r["pii"] and "빚" in r["past"], "T5 report(pii+past 동시 검출)")
    # T6 filter_uploadable(통과/차단 분리)
    items = [{"sentence": "짧게 결론부터 말한다"},
             {"sentence": "파산 신청했다"},
             {"sentence": "이메일 a@b.com"}]
    fr = filter_uploadable(items)
    check(len(fr["ok"]) == 1 and len(fr["blocked"]) == 2,
          "T6 filter_uploadable(1 통과 · 2 차단)")
    # T7 과거사 카테고리 표본 전부 차단(과잉차단=안전)
    check(all(is_t3_blocked("%s 관련 이야기" % t)
              for t in ["대출", "우울증", "전과", "사망", "도박", "상속"]),
          "T7 과거사 카테고리 표본 전부 차단")
    # T8 멱등/read-only: 판정이 텍스트를 변형하지 않음(순수 함수)
    t = "빚 010-1234-5678"
    check(is_t3_blocked(t) == is_t3_blocked(t), "T8 판정 멱등(순수 함수)")

    print(f"\nGATE={'GO' if ok else 'NO-GO'}")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("binggu_t3_filter: --selftest 로 검증 실행")
