#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""binggu CLI 임베드 selftest — thin shim (wheel 자족성 이동).

구현부는 binggupack/cli/selftest_embed.py 로 이동 — wheel 에 tests/ 가 미포함이라 설치본
`binggu --selftest` 가 ModuleNotFoundError 나던 결함 수정(구현을 패키지에 동봉).
pytest 는 종전대로 이 파일에서 test_binggu_cli_selftest 를 수집한다(star import 노출).
"""
from binggupack.cli.selftest_embed import (  # noqa: F401  (수집·호환 명시 재노출)
    selftest,
    test_binggu_cli_selftest,
)

__all__ = (
    'selftest',
    'test_binggu_cli_selftest',
)
