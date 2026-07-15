# -*- coding: utf-8 -*-
"""MGB v0.1 시나리오 — 각 모듈은 ID·TITLE·REQUIRES·run(adapter, home, ctx) 을 제공한다.

run 은 adapter 의 관찰값(exit code·구조화 state)만으로 verdict 를 독립 계산한다.
adapter 가 반환한 'PASS' 같은 자기신고를 신뢰하지 않는다. REQUIRES 미충족(공개 인터페이스 부족)은
runner 가 UNSUPPORTED/UNSUPPORTED 로 처리한다.
"""

ORDER = [
    "mgb_01", "mgb_02", "mgb_03", "mgb_04", "mgb_05", "mgb_06",
    "mgb_07", "mgb_08", "mgb_09", "mgb_10", "mgb_11", "mgb_12",
]
