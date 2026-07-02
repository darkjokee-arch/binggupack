"""binggu_knowledge_graph.py — explore 그래프 → workflow 페이로드 + 수집 훅 (thin wrapper).

v1.16 strangler Phase2: 순수 transform impl(graph_to_workflow_nodes_edges·build_workflow_payload·
build_workflow_sync_plan·select_nodes·collect_nodes·graph_stats·serialize_graph · _node_id/
_child_ids/_category 등)은 binggupack.pack.knowledge_graph 로 이관됐고, 이 파일은 공개 심볼이
동일한 thin wrapper 다. 기존 호출처(binggu_publish_run_all_selftests.py 서브프로세스
`--selftest`·`--explore` CLI)는 그대로 동작한다.

미이관 형제 의존(binggu_local_collect line44 lazy import · binggu_branch_explorer __main__ lazy)은
정본 모듈이 scripts/ 를 sys.path 에 부트스트랩해 bare-name 으로 해소한다(recall 선례). 순수
transform·네트워크 0. selftest/CLI 도 정본 모듈에서 re-export(mock 그래프·네트워크 0).

CLI: python scripts/binggu_knowledge_graph.py [--selftest] | --explore '<주제>'
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.knowledge_graph import *  # noqa: E402,F401,F403
from binggupack.pack.knowledge_graph import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    LOCAL_COLLECT,
    WORKFLOW_TOOL,
    BRANCH_RELATION,
    DEFAULT_WORKFLOW_STATUS,
    graph_to_workflow_nodes_edges,
    build_workflow_payload,
    build_workflow_sync_plan,
    select_nodes,
    collect_nodes,
    graph_stats,
    serialize_graph,
    _node_id,
    _child_ids,
    _category,
    _mock_graph,
    _selftest,
    _main,
)


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
