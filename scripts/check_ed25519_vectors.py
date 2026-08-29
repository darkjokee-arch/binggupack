# -*- coding: utf-8 -*-
"""Ed25519 KAT drift/fail-open 검증 — docs/ed25519_kat_vectors.json 재계산 대조.

signing_provider(PW-Core1) 의 순수함수(ed25519_publickey/ed25519_sign/ed25519_verify)로 벡터를
재계산해:
  · positive 벡터 → verify accept(+ derive=true 는 publickey/sign 결정성까지 대조),
  · negative 벡터 → 전부 reject(verify 가 조용히 True 를 내는 fail-open 이 하나라도 있으면 FAIL)
를 확인한다. 하나라도 어긋나면 exit 1(CI BLOCK), 전부 통과면 exit 0.

정직 라벨(vectors json honest_label 계승): 이 KAT 는 서명 산식의 정확성과 fail-open 부재를 실증할
뿐, "로컬에서 위조 차단/보안 강화"를 주장하지 않는다. L2 실질 값은 hosted/locked 배포에서만.
(같은 머신 셸이 keychain sk 를 로드하면 유효 서명 가능 = 보안 연극 · config kind 는 모델-writable 평문.)

사용: python scripts/check_ed25519_vectors.py    ->  exit 0(GO) | exit 1(BLOCK)
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))                 # scripts/
ROOT = os.path.dirname(BASE)                                      # repo root
VECTORS = os.path.join(ROOT, "docs", "ed25519_kat_vectors.json")
for _p in (ROOT, BASE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _hex_to_bytes(h):
    """hex → bytes. 홀수 길이/비-hex 는 그대로 예외(길이/형식 오류도 검증 대상)."""
    return bytes.fromhex(h)


def main():
    from binggupack.safety import signing_provider as sp

    data = json.loads(Path(VECTORS).read_text(encoding='utf-8'))
    fails = []
    n_pos = n_neg = 0

    # ── positive: verify accept (+ derive: publickey/sign 결정성) ──────────────────
    for v in data.get("positive", []):
        n_pos += 1
        vid = v["id"]
        try:
            pk = _hex_to_bytes(v["pk"])
            msg = _hex_to_bytes(v.get("msg_hex", ""))
            sig = _hex_to_bytes(v["sig"])
            if sp.ed25519_verify(pk, msg, sig) is not True:
                fails.append("%s: positive verify 가 accept 하지 않음(예상 accept)" % vid)
                continue
            if v.get("derive"):
                sk = _hex_to_bytes(v["sk"])
                got_pk = sp.ed25519_publickey(sk)
                if got_pk != pk:
                    fails.append("%s: ed25519_publickey(sk) drift (got=%s)" % (vid, got_pk.hex()))
                got_sig = sp.ed25519_sign(sk, msg)
                if got_sig != sig:
                    fails.append("%s: ed25519_sign(sk,msg) drift (got=%s)" % (vid, got_sig.hex()))
                # 결정성(RFC 8032 deterministic) 재확인
                if sp.ed25519_sign(sk, msg) != got_sig:
                    fails.append("%s: ed25519_sign 비결정(재계산 불일치)" % vid)
        except Exception as e:  # noqa: BLE001 — 계산 실패도 drift 로 보고
            fails.append("%s: positive 계산 실패 (%s)" % (vid, e))

    # ── negative: 전부 reject (fail-open 차단) ─────────────────────────────────────
    for v in data.get("negative", []):
        n_neg += 1
        vid = v["id"]
        try:
            pk = _hex_to_bytes(v["pk"])
            msg = _hex_to_bytes(v.get("msg_hex", ""))
            sig = _hex_to_bytes(v["sig"])
        except Exception as e:  # noqa: BLE001 — 길이/형식 오류 벡터는 verify 인자로 넘겨 False 를 봐야 함
            # hex 파싱 자체가 실패하면(홀수 길이 등) verify 에 못 넘김 → 벡터 정의 오류로 취급.
            fails.append("%s: negative 벡터 hex 파싱 실패 (%s)" % (vid, e))
            continue
        got = sp.ed25519_verify(pk, msg, sig)
        if got is not False:
            # ★ fail-open: negative 를 accept 했다 → 최악. 즉시 BLOCK.
            fails.append("%s: FAIL-OPEN — negative 를 verify 가 accept(got=%r · 예상 reject)" % (vid, got))

    total = n_pos + n_neg
    if fails:
        print("ED25519-KAT: BLOCK (%d/%d 어긋남)" % (len(fails), total))
        for f in fails:
            print("  -", f)
        return 1
    print("ED25519-KAT: GO — positive %d accept · negative %d 전부 reject (fail-open 0 · drift 0)."
          % (n_pos, n_neg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
