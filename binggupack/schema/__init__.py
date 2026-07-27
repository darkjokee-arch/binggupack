"""schema — 빙구팩 스키마 정본 모듈. v1.11.0 strangler phase1 이관 시작.

현재: verb_edge(동사형 엣지 6종 + 화자축/증빙축 + deprecated 검증기) · edge_norm(3 입력형
정규화 + 통합 판정 진입점 `validate_edge`) · evidence_grade(evidence_locator 등급 정본).
scripts/openbinggu_verb_edge_schema.py 는 backward-compatible thin wrapper 로 유지된다.

★ 신규 심볼은 **여기서도 노출**한다(결함 D11) — 패키지 경로에서 안 잡히면 소비자가
  scripts/ 를 sys.path 에 얹는 bare-name import 로 우회하게 되고, 그 순간 정본 경로가 갈린다.
"""
from .verb_edge import (  # noqa: F401
    VERB_EDGES,
    SPEAKER_EDGES,
    SPEAKERS,
    GROUNDING_EDGES,
    EVIDENCE_ID_PREFIXES,
    WEAK_LABELS,
    VALID_STATUS,
    looks_like_evidence_id,
    validate_verb_edge,
    validate_deprecated,
    default_view_filter,
)
from .edge_norm import (  # noqa: F401
    AXIS_VERB,
    AXIS_SPEAKER,
    AXIS_GROUNDING,
    normalize_edge,
    validate_edge,
    validate_norm_edge,
    registry_of,
)
from .evidence_grade import (  # noqa: F401
    GRADE,
    METHOD_RANK,
    PRIMARY_METHODS,
    LIVE_METHODS,
    PRIMARY_CONFIDENCE,
    grade_of,
    is_primary_source,
    live_capture_confidence,
)
