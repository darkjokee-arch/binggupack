# -*- coding: utf-8 -*-
"""binggupack.evidence — 증거 축(스펙 §1) 공개 경로.

`locator` 모듈이 evidence_locator(출처 좌표) 집계 정본을 제공한다. 적재(write) 정본은
`scripts/openbinggu_staging_write_selftest.py`(insert_locators / loc_row / mirror)이며,
여기서는 **읽기·집계**만 다룬다(순환 import 0 · 무거운 import 0).
"""
from binggupack.evidence.locator import (  # noqa: F401
    PRIMARY_METHODS,
    coverage_line,
    evidence_ids_for_node,
    evidence_locator_coverage,
    is_primary_source,
)

__all__ = [
    "PRIMARY_METHODS",
    "is_primary_source",
    "evidence_locator_coverage",
    "evidence_ids_for_node",
    "coverage_line",
]
