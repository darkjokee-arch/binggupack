#!/usr/bin/env python3
"""binggupack_sign_util.py — save-intent HMAC 서명 단일 출처 (L-8: 복붙 6곳 통합).

서명 재료 (L-4 method+path 바인딩 — hosted/workers/src/save_common.ts 와 바이트 동일 의무):
  신형 v2: HMAC-SHA256(sign_material, ts + "." + METHOD + "." + path + "." + sha256_hex(body))
  구형:    HMAC-SHA256(sign_material, ts + "." + sha256_hex(body))
서버는 신형 우선 검증, SAVE_SIG_V2_ONLY 미설정(기본 false) 동안만 구형 수용.
헤더: X-BGP-TS(epoch초) + X-BGP-SIG(hex). 서명 재료는 요청에 실리지 않음.
CLI: python binggupack_sign_util.py --selftest   (외부 네트워크 0 · write 0)
"""
import hashlib
import hmac as hmac_mod
import sys
import time
from urllib.parse import urlsplit


def _pathname(url_or_path):
    # 전체 URL 이 오면 pathname 만 추출 — 서버측 new URL(request.url).pathname 과 동일 기준
    if "://" in url_or_path:
        return urlsplit(url_or_path).path
    return url_or_path


def signed_headers(sign_material, body_bytes, url_or_path, method="POST", ts=None):
    """신형 v2 서명 — method+path 바인딩 (서버 우선 검증 대상)."""
    ts = str(ts if ts is not None else int(time.time()))
    bh = hashlib.sha256(body_bytes).hexdigest()
    msg = ts + "." + method.upper() + "." + _pathname(url_or_path) + "." + bh
    mac = hmac_mod.new(sign_material.encode("utf-8"), msg.encode("utf-8"),
                       hashlib.sha256).hexdigest()
    return {"X-BGP-TS": ts, "X-BGP-SIG": mac}


def signed_headers_legacy(sign_material, body_bytes, ts=None):
    """구형 서명 — SAVE_SIG_V2_ONLY 전환 전 하위호환 전용. 신규 사용 금지."""
    ts = str(ts if ts is not None else int(time.time()))
    bh = hashlib.sha256(body_bytes).hexdigest()
    mac = hmac_mod.new(sign_material.encode("utf-8"), (ts + "." + bh).encode("utf-8"),
                       hashlib.sha256).hexdigest()
    return {"X-BGP-TS": ts, "X-BGP-SIG": mac}


def _selftest():
    sm = "a" * 64
    body = b'{"k":1}'
    bh = hashlib.sha256(body).hexdigest()
    results = []

    def rec(cid, desc, ok):
        results.append(ok)
        print("[%s] %s %s" % ("OK" if ok else "NG", cid, desc))

    h_url = signed_headers(sm, body, "https://x.example/save2/k/intent", ts=1700000000)
    h_path = signed_headers(sm, body, "/save2/k/intent", ts=1700000000)
    rec("T1", "전체 URL / path 동일 서명", h_url == h_path)

    expect_v2 = hmac_mod.new(sm.encode("utf-8"),
                             ("1700000000.POST./save2/k/intent." + bh).encode("utf-8"),
                             hashlib.sha256).hexdigest()
    rec("T2", "v2 재료 = ts.METHOD.path.bodyhash", h_url["X-BGP-SIG"] == expect_v2)

    h_leg = signed_headers_legacy(sm, body, ts=1700000000)
    expect_leg = hmac_mod.new(sm.encode("utf-8"), ("1700000000." + bh).encode("utf-8"),
                              hashlib.sha256).hexdigest()
    rec("T3", "legacy 재료 = ts.bodyhash", h_leg["X-BGP-SIG"] == expect_leg)
    rec("T4", "v2 ≠ legacy (재료 분리)", h_leg["X-BGP-SIG"] != h_url["X-BGP-SIG"])

    h_get = signed_headers(sm, body, "/save2/k/intent", method="get", ts=1700000000)
    rec("T5", "method 대문자 정규화 + 바인딩 반영", h_get != h_url)

    h_other = signed_headers(sm, body, "/save2/k/pull", ts=1700000000)
    rec("T6", "path 바인딩 반영 (다른 path = 다른 서명)", h_other != h_url)

    h_now = signed_headers(sm, body, "/p")
    rec("T7", "ts 미지정 = 현재 epoch", abs(int(h_now["X-BGP-TS"]) - int(time.time())) <= 2)

    n_ok = sum(1 for ok in results if ok)
    gate = "GO" if n_ok == len(results) else "BLOCK"
    print("---")
    print("SIGN-UTIL GATE=%s (%d/%d)" % (gate, n_ok, len(results)))
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    print("usage: binggupack_sign_util.py --selftest")
    sys.exit(1)
