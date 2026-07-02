# -*- coding: utf-8 -*-
"""binggu_cloud_ingest_wire — topic_to_pack → opencrab-cloud ingest 래퍼 (backward-compatible thin wrapper).

strangler: 순수 정본(ingest_pack · build_ingest_payloads · build_workflow_payload · run_mcp_session ·
default_http_transport · load_cloud_config · _apply_t3_gate · _classify_* · _selftest 등)은
binggupack.pack.cloud_ingest_wire 로 byte-identical 이관됐고, 이 파일은 공개 심볼이 동일한 thin
wrapper 다. 기존 호출처(import binggu_cloud_ingest_wire — binggu_topic_to_pack · binggu_pack_edges
lazy 등)는 그대로 동작한다.

삼중 게이트·T3 하드제외·raise 0 불변은 1바이트도 변하지 않았다. 정본의 런타임 의존(bare-name
`import binggu_pack_factory` + lazy `from binggu_t3_filter`, 둘 다 MIGRATED shim)은 정본 모듈이
자기 위치에서 scripts/ 를 sys.path 에 재계산해 얹어 해소한다(이 wrapper 도 동일하게 얹는다 — 이중 안전).

CLI: python scripts/binggu_cloud_ingest_wire.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.cloud_ingest_wire import *  # noqa: E402,F401,F403
from binggupack.pack.cloud_ingest_wire import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    INGEST_TOOL,
    WORKFLOW_TOOL,
    DEFAULT_CLIENT,
    CONFIG_FILENAME,
    ENABLE_ENV,
    _T3_TEXT_FIELDS,
    _binggu_home,
    _redact_token,
    _is_confirmed,
    _classify_exception,
    _classify_response,
    load_cloud_config,
    build_ingest_payloads,
    build_workflow_payload,
    default_http_transport,
    run_mcp_session,
    _dedupe,
    _apply_t3_gate,
    ingest_pack,
    _session_reason,
    _mk_docs,
    _selftest,
    main,
)


if __name__ == "__main__":
    sys.exit(main())
