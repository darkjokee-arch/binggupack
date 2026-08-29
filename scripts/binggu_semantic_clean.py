# -*- coding: utf-8 -*-
"""binggu_semantic_clean.py — 의미정제 (backward-compatible thin wrapper).

v1.16 strangler Phase2: 본문 정본은 binggupack.pack.semantic_clean 로 byte-identical 이관됐고,
이 파일은 공개 심볼이 동일한 thin wrapper 다. 기존 호출처(import binggu_semantic_clean as CLEAN —
binggu_topic_to_pack 의 CLEAN.clean_chunks / CLEAN.default_ollama_transport)는 그대로 동작한다.

정본 _structural_keep 폴백은 binggu_harvest(bare-name·아직 scripts/ 내부)를 scripts/ sys.path
anchor 로 lazy 호출한다(try/except graceful). 이 wrapper 는 ROOT/HERE 를 sys.path 에 부트스트랩해
정본 패키지 import 를 보장한다.

CLI: python scripts/binggu_semantic_clean.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.semantic_clean import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    clean_chunks,
    build_clean_prompt,
    default_ollama_transport,
    _structural_keep,
    _parse_verdicts,
    _extract_json,
    _strip_fences,
    _coerce_verdict,
    _norm_hints,
    _chunk_text,
    _selftest,
)

__all__ = (
    'clean_chunks',
    'build_clean_prompt',
    'default_ollama_transport',
    '_structural_keep',
    '_parse_verdicts',
    '_extract_json',
    '_strip_fences',
    '_coerce_verdict',
    '_norm_hints',
    '_chunk_text',
    '_selftest',
)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_semantic_clean — use --selftest, or import clean_chunks() / default_ollama_transport()")
