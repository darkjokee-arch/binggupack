"""policy — 빙구팩 read-only 정책 모듈. v1.11.0 strangler phase2 이관.

현재: match(LocalBinggu read-only match policy — node/edge Tier 분류, D9 보호,
watcher override). scripts/localbinggu_match_policy.py 는 backward-compatible thin
wrapper 로 유지된다.
"""
from .match import (  # noqa: F401
    RF,
    FUZZY_THRESHOLD,
    SIM_T2,
    SIM_T3,
    EDGE_RELATION_ALLOWED,
    normalize_nodes,
    classify_pair,
    classify_edge_pair,
    evaluate,
    evaluate_edges,
    summarize,
    summarize_edges,
)
