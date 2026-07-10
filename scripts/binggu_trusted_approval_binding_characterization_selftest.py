#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-A Trusted Approval Event — canonical binding characterization (TIER-4, no DB).

정본 설계: docs/BINGGUPACK_TRUSTED_APPROVAL_EVENT_RFC.md §9·§24.
순수함수(canonical_payload_digest / compute_request_id / binding_fields) 만 검증 — DB·네트워크 0.

CLI: python scripts/binggu_trusted_approval_binding_characterization_selftest.py --selftest
"""
import os
import sys
import unicodedata

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)


def run():
    from binggupack.safety import trusted_approval as ta

    fails, ran = [], []

    def ck(name, cond):
        ran.append(name)
        print("  [%s] %s" % ("OK" if cond else "X", name))
        if not cond:
            fails.append(name)

    d = ta.canonical_payload_digest

    TXT = "이 입찰은 마진이 낮아 보류하는 것이 낫다"

    # 1) 결정성 — dict key order / index 정렬 무관
    ck("digest 결정성(key/index order 무관)",
       d("save_candidate", {"text": TXT, "indices": [1, 3]})
       == d("save_candidate", {"indices": [3, 1], "text": TXT}))

    # 2) explicit flip → 상이(TAE-P2-04)
    ck("explicit flip → digest 상이",
       d("save_candidate", {"text": TXT, "indices": [1]})
       != d("save_candidate", {"text": TXT, "indices": [1], "explicit": True}))

    # 3) speaker / due_date 바인딩(save)
    ck("save speaker 바인딩",
       d("save_candidate", {"text": TXT, "indices": [1]})
       != d("save_candidate", {"text": TXT, "indices": [1], "speaker": "owner"}))
    ck("save due_date 바인딩",
       d("save_candidate", {"text": TXT, "indices": [1]})
       != d("save_candidate", {"text": TXT, "indices": [1], "due_date": "2099-12-31"}))

    # 4) ★pair due_date 바인딩(TA-ATK-1 characterization) — owner 미검토 리마인더 주입 차단
    ck("pair due_date 바인딩(TA-ATK-1)",
       d("pair", {"owner_text": TXT, "owner_pick": 1})
       != d("pair", {"owner_text": TXT, "owner_pick": 1, "due_date": "2099-12-31"}))

    # 5) NFC — NFD 변형은 같은 digest, 다른 codepoint 는 상이
    nfc = "café"
    nfd = unicodedata.normalize("NFD", nfc)
    ck("NFC 정규화(NFD 변형 == NFC)",
       d("deprecate", {"index": 1, "id8": "x", "reason": nfc})
       == d("deprecate", {"index": 1, "id8": "x", "reason": nfd}))
    ck("다른 codepoint → 상이",
       d("deprecate", {"index": 1, "id8": "x", "reason": "cafe"})
       != d("deprecate", {"index": 1, "id8": "x", "reason": nfc}))

    # 6) bidi/control codepoint 거부
    try:
        d("deprecate", {"index": 1, "id8": "x", "reason": "a‮b"})  # RLO
        ck("bidi(RLO) 거부", False)
    except ta.ControlCharReject:
        ck("bidi(RLO) 거부", True)
    try:
        d("deprecate", {"index": 1, "id8": "x", "reason": "a​b"})  # ZWSP (Cf)
        ck("zero-width(Cf) 거부", False)
    except ta.ControlCharReject:
        ck("zero-width(Cf) 거부", True)

    # 7) field-reorder / concat 충돌 불가 — 값 경계 모호성 없음
    ck("concat 충돌 불가({'a':'x','b':'yz'} != {'a':'xy','b':'z'})",
       d("harvest_add", {"kind": "x", "url": "yz"})
       != d("harvest_add", {"kind": "xy", "url": "z"}))

    # 8) int vs str 구분
    ck("int vs str 구분(index 1 != '1')",
       d("deprecate", {"index": 1, "id8": "x", "reason": "r"})
       != d("deprecate", {"index": "1", "id8": "x", "reason": "r"}))

    # 9) null vs 값 구분(omitted optional = explicit null → 일관)
    ck("null vs 값 구분(keyword)",
       d("harvest_add", {"kind": "url", "url": "u"})
       != d("harvest_add", {"kind": "url", "url": "u", "keyword": "k"}))
    ck("omitted == explicit null(keyword)",
       d("harvest_add", {"kind": "url", "url": "u"})
       == d("harvest_add", {"kind": "url", "url": "u", "keyword": None}))

    # 10) mark recall_nonce 바인딩(TAE-P2-05)
    ck("mark recall_nonce 바인딩",
       d("mark_hit", {"recall_query": "q", "index": 1})
       != d("mark_hit", {"recall_query": "q", "index": 1, "recall_nonce": "n1"}))
    ck("mark_hit != mark_miss(operation prefix)",
       d("mark_hit", {"recall_query": "q", "index": 1})
       != d("mark_miss", {"recall_query": "q", "index": 1}))

    # 11) request_id — 같은 \x1f 구분자·operation/proto 경계 무충돌(TAE-P2-09)
    dig = d("deprecate", {"index": 1, "id8": "x", "reason": "r"})
    ck("request_id 결정성 + 길이 24",
       ta.compute_request_id("deprecate", dig, "L") == ta.compute_request_id("deprecate", dig, "L")
       and len(ta.compute_request_id("deprecate", dig, "L")) == 24)
    ck("request_id operation 경계 무충돌",
       ta.compute_request_id("deprecate", dig, "L") != ta.compute_request_id("replace", dig, "L"))
    ck("request_id ledger 경계 무충돌",
       ta.compute_request_id("deprecate", dig, "L1") != ta.compute_request_id("deprecate", dig, "L2"))

    # 12) protocol_version 바인딩(canonicalization 버전 변경 시 구 승인 무효)
    ck("protocol_version 바인딩",
       d("deprecate", {"index": 1, "id8": "x", "reason": "r"}, protocol_version="tae-1")
       != d("deprecate", {"index": 1, "id8": "x", "reason": "r"}, protocol_version="tae-2"))

    print("-" * 66)
    print("RESULT: %d checks, %d fail" % (len(ran), len(fails)))
    print("GATE=%s" % ("GO" if not fails else "NO-GO"))
    return 0 if not fails else 1


if __name__ == "__main__":
    print("=" * 66)
    print("P1-A Trusted Approval — canonical binding characterization (TIER-4)")
    print("=" * 66)
    if not sys.argv[1:] or "--selftest" in sys.argv:
        raise SystemExit(run())
    print("usage: --selftest")
    sys.exit(2)
