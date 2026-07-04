# -*- coding: utf-8 -*-
"""binggu_cloud_query_wire — OpenCrab 클라우드 read 조회 래퍼 (backward-compatible thin wrapper).

strangler: 순수 정본(run_query · build_query_payload · _extract_and_mask · _clamp_args ·
_READ_TOOLS/_WRITE_TOOLS · _selftest 등)은 binggupack.pack.cloud_query_wire 에 있고, 이 파일은
공개 심볼이 동일한 thin wrapper 다. cloud_ingest_wire shim 과 동일 패턴.

read 화이트리스트·응답 PII 마스킹·transport None=NO_TRANSPORT(네트워크 0)·raise 0 불변은
1바이트도 변하지 않는다(정본만 참조).

CLI: python scripts/binggu_cloud_query_wire.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.cloud_query_wire import *  # noqa: E402,F401,F403
from binggupack.pack.cloud_query_wire import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    _READ_TOOLS,
    _WRITE_TOOLS,
    DEFAULT_CLIENT,
    _clamp_args,
    build_query_payload,
    _extract_and_mask,
    run_query,
    load_cloud_config,
    default_http_transport,
    run_mcp_session,
    _selftest,
    main,
)


if __name__ == "__main__":
    sys.exit(main())
