"""binggu_pack_edges — pack-간 workflow edges 추론 (backward-compatible thin wrapper).

strangler: 순수 정본(infer_edges · to_workflow_nodes_edges · build_workflow_payload_from_edges ·
sync_edges_to_workflow · RELATIONS · WORKFLOW_TOOL · SYNC_ENABLE_ENV · DEFAULT_CLIENT · 내부 헬퍼 ·
_selftest)은 binggupack.pack.pack_edges 로 byte-identical 이관됐고, 이 파일은 공개 심볼이 동일한 thin
wrapper 다. 기존 호출처(import binggu_pack_edges — binggu_topic_to_pack 등)는 그대로 동작한다.

live 분기 lazy import(binggu_cloud_ingest_wire · watcher_batch_m1 — 둘 다 미이관·bare-name)는 정본
모듈이 scripts/ 를 sys.path 에 얹어 해소한다(이 wrapper 도 동일하게 얹는다 — 이중 안전). 순수 추론
경로(infer_edges 등)는 cross-dep 0 · 네트워크 0.

CLI: python scripts/binggu_pack_edges.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.pack_edges import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    RELATIONS,
    WORKFLOW_TOOL,
    SYNC_ENABLE_ENV,
    DEFAULT_CLIENT,
    infer_edges,
    to_workflow_nodes_edges,
    build_workflow_payload_from_edges,
    sync_edges_to_workflow,
    _selftest,
)

__all__ = (
    'RELATIONS',
    'WORKFLOW_TOOL',
    'SYNC_ENABLE_ENV',
    'DEFAULT_CLIENT',
    'infer_edges',
    'to_workflow_nodes_edges',
    'build_workflow_payload_from_edges',
    'sync_edges_to_workflow',
    '_selftest',
)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_pack_edges — use --selftest, or import infer_edges(pack_metas)")
