# -*- coding: utf-8 -*-
"""Characterization selftest — binggu_save_gate.sent_hash / _norm (v1.11.0 save-gate S3-C).

이관 전 현행 동작을 고정한다(pre-move characterization). 이관 후(save_gate가
binggupack.safety.gate_text 에서 import)에도 동일 PASS 해야 한다. save_gate 경유와
autopush 경유(SGATE.sent_hash / SGATE._norm) 결과가 동일해야 한다.

sent_hash/_norm 은 순수(write 0·ledger 0·actor/confirm 0). _norm=공백정규화,
sent_hash=sha256(_norm)[:16]. 본 테스트는:
  - deterministic(동일 입력 → 동일 hash)
  - 공백/줄바꿈/앞뒤공백 normalize
  - 한글/영어/숫자/emoji/빈/긴 문자열
  - PII-like 입력도 원문 평문 미노출(hash 16-hex 만)
  - output shape(16자 lowercase hex) 고정
  - 서로 다른 대표 입력 → 서로 다른 hash(sanity, collision 유발 안 함)
  - save_gate 경유 == package 경유
read-only. write 0.
"""
import re
import sys

from binggu_save_gate import sent_hash, _norm  # noqa: E402  (save_gate 경유)

_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def run():
    results = []

    def ck(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # ---- deterministic ----
    ck("deterministic", sent_hash("hello world") == sent_hash("hello world"), "동일 입력 동일 hash")

    # ---- normalize: 공백/줄바꿈/앞뒤 ----
    ck("norm_multispace", _norm("a  b") == "a b", "다중 공백 → 1")
    ck("norm_newline", _norm("a\nb") == "a b", "줄바꿈 → 공백")
    ck("norm_tab", _norm("a\tb") == "a b", "탭 → 공백")
    ck("norm_strip", _norm("  x  ") == "x", "앞뒤 공백 제거")
    ck("hash_space_invariant", sent_hash("a  b") == sent_hash("a b"), "공백 정규화 후 hash 동일")
    ck("hash_newline_invariant", sent_hash("a\nb") == sent_hash("a b"), "줄바꿈 정규화 후 hash 동일")

    # ---- 다양한 입력 shape (16 hex) ----
    for nm, s in [("ko", "B안으로 결정한다"), ("en", "decide option B"), ("num", "비용 1200원 절감"),
                  ("emoji", "좋아요 👍 결정"), ("empty", ""), ("long", "가나다 " * 500)]:
        h = sent_hash(s)
        ck("shape_%s" % nm, bool(_HEX16.match(h)), "16자 lowercase hex (%s)" % nm)

    # ---- PII-like: 원문 평문 미노출 ----
    pii_ok = True
    pii_detail = ""
    for s, leaks in [("내 번호 010-1234-5678", ["010-1234-5678", "01012345678"]),
                     ("주민 901010-1234567", ["901010-1234567"]),
                     ("api sk-abcd1234efgh5678", ["sk-abcd1234efgh5678"])]:
        h = sent_hash(s)
        _norm(s)
        for leak in leaks:
            if leak in h:  # hash 에 원문 미노출
                pii_ok = False; pii_detail = "PII leaked in hash: %r" % leak; break
        if not pii_ok:
            break
    ck("pii_no_leak_in_hash", pii_ok, pii_detail or "sent_hash 에 PII 평문 0(hash 16-hex)")

    # ---- 서로 다른 입력 → 서로 다른 hash (sanity) ----
    distinct = len({sent_hash(x) for x in ["alpha", "beta", "gamma", "결정 A", "결정 B"]}) == 5
    ck("distinct_inputs_distinct_hash", distinct, "5개 상이 입력 → 5개 상이 hash")

    # ---- save_gate 경유 == package 경유 (이관 후에만 package 존재; 이관 전엔 skip) ----
    try:
        from binggupack.safety.gate_text import sent_hash as pkg_sent_hash
        pkg_ok = all(sent_hash(x) == pkg_sent_hash(x) for x in ["hello", "B안 결정", "a  b"])
        ck("save_gate_eq_package", pkg_ok, "save_gate 경유 == package 경유")
    except ImportError:
        ck("save_gate_eq_package", True, "이관 전 — package 미존재(skip, 이관 후 검증)")

    print("=" * 74)
    print("binggu_save_gate.sent_hash / _norm characterization (pure, write 0)")
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
