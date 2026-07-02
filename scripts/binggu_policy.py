# -*- coding: utf-8 -*-
"""binggu_policy — 자기진화 거버넌스 2단 선언형 정책 read-only 평가기
(backward-compatible thin wrapper).

v1.16 strangler Phase2: 순수 정본(load_policy/evaluate/classify_clause/is_immutable/
compute_digest/policy_path/pin_path + 가드 5종 형상검증 + _selftest)은
binggupack.safety.policy 로 이관됐고(정책/pin read-only·write 0 불변),
이 파일은 공개 심볼이 동일한 thin wrapper 다. 기존 호출처(contrast_protocol 의
lazy `import binggu_policy as POLICY` comp2 등)는 그대로 동작한다.

정책 앵커(_repo_root)는 정본 모듈에서 이 파일 이동(scripts/ → binggupack/safety/)에
맞춰 dirname 깊이를 보정했다 — policies/binggu_policy.json{,.sha256} 기본 경로는 동일
repo root 로 해소된다(패키지 기본 봉인 T12b 정합 유지).

CLI: python scripts/binggu_policy.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.safety.policy import *  # noqa: E402,F401,F403
from binggupack.safety.policy import (  # noqa: E402,F401  (전체 명시 re-export)
    SCHEMA_VERSION,
    CATEGORIES,
    SAFETY_CATEGORY,
    REQUIRED_IMMUTABLE,
    policy_path,
    pin_path,
    compute_digest,
    load_policy,
    classify_clause,
    is_immutable,
    evaluate,
    _selftest,
)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    import json
    res = load_policy()
    print(json.dumps({
        "ok": res["ok"], "reason": res["reason"],
        "digest": res["digest"], "fail_closed": res["fail_closed"],
    }, ensure_ascii=False, indent=2))
