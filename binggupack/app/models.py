# -*- coding: utf-8 -*-
"""Binggu App Path read core — 상수·에러 코드·공통 출력 계약.

모든 도구는 JSON 직렬화 가능한 dict 를 반환한다. 오류는 {error_code, message}(내부 경로/stack/secret 미노출).
"""
SCHEMA_VERSION = 1

# 도구별 한도
LIST_LIMIT_DEFAULT = 20
LIST_LIMIT_MAX = 100
SEARCH_LIMIT_DEFAULT = 5
SEARCH_LIMIT_MAX = 20
QUERY_MIN = 2
QUERY_MAX = 200
HANDOFF_MAX_NODES_DEFAULT = 15
HANDOFF_MAX_NODES_MAX = 50
HANDOFF_CAP_BYTES = 40 * 1024
KEYWORD_CANDIDATES_MAX = 5
EXCERPT_CAP = 200
TOPICS_MAX = 10

# pack 안전 한도(malformed/oversized fail-closed)
MANIFEST_MAX_BYTES = 256 * 1024
JSONL_MAX_BYTES = 8 * 1024 * 1024
JSONL_MAX_ROWS = 50_000

# 에러 코드
ERR_PACK_NOT_FOUND = "PACK_NOT_FOUND"
ERR_QUERY_TOO_SHORT = "QUERY_TOO_SHORT"
ERR_NODE_NOT_FOUND = "NODE_NOT_FOUND"
ERR_AMBIGUOUS_KEYWORD = "AMBIGUOUS_KEYWORD"
ERR_INVALID_INPUT = "INVALID_INPUT"
ERR_NODE_OR_KEYWORD_REQUIRED = "NODE_OR_KEYWORD_REQUIRED"


def error(code, message):
    """오류 응답(내부 경로/stack/secret 미노출 — message 는 호출측이 안전 문구만 전달)."""
    return {"error_code": code, "message": message}
