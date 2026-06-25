"""schema — 빙구팩 스키마 정본 모듈. v1.11.0 strangler phase1 이관 시작.

현재: verb_edge(동사형 엣지 6종 + deprecated 검증기). scripts/openbinggu_verb_edge_schema.py
는 backward-compatible thin wrapper 로 유지된다.
"""
from .verb_edge import (  # noqa: F401
    VERB_EDGES,
    WEAK_LABELS,
    VALID_STATUS,
    validate_verb_edge,
    validate_deprecated,
    default_view_filter,
)
