# -*- coding: utf-8 -*-
"""binggu_branch_explorer — 1주제 재귀 분기 지식그래프 (backward-compatible thin wrapper).

v1.16 strangler Phase2: 정본 로직(explore/expand_node/score_relevance/build_expand_prompt/
build_relevance_prompt/path_consistency/default_ollama_transport + 파서/필터 헬퍼)은
binggupack.pack.branch_explorer 로 byte-identical 이관됐고, 이 파일은 공개 심볼을 그대로
re-export 하는 thin wrapper 다. 기존 호출처(bare-name `import binggu_branch_explorer as BE`
— knowledge_graph __main__ lazy)는 그대로 동작한다.

완전 stdlib(collections/json/re/urllib)·__file__ 전무·피임포트 0 인 가장 안전한 leaf 라
정본 모듈이 독립 실행 가능하며, 이 wrapper 는 scripts/ 를 sys.path 에 부트스트랩해
패키지를 import 한 뒤 심볼을 재노출하고 CLI 를 위임한다(selftest 는 정본 _selftest 재사용).

CLI: python scripts/binggu_branch_explorer.py --selftest
     python scripts/binggu_branch_explorer.py --explore '<주제>' [--depth N] [--max-nodes N]
                                              [--breadth N] [--rel-min F] [--llm-relevance]
"""
import os
import sys
import json  # noqa: F401  CLI 본문(explore 결과 직렬화)이 사용

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.pack.branch_explorer import *  # noqa: E402,F401,F403
from binggupack.pack.branch_explorer import (  # noqa: E402,F401  (전체 명시 re-export)
    _normalize_label,
    _tokens,
    _coerce_labels,
    _coerce_score,
    _is_broken_label,
    _context_chain,
    build_expand_prompt,
    build_relevance_prompt,
    path_consistency,
    expand_node,
    score_relevance,
    explore,
    default_ollama_transport,
    _tree_transport,
    _rel_transport,
    _selftest,
)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    # CLI: --explore '<주제>' [--depth N] [--max-nodes N] [--breadth N] [--rel-min F] [--llm-relevance]
    root = None
    if "--explore" in sys.argv:
        i = sys.argv.index("--explore")
        if i + 1 < len(sys.argv):
            root = sys.argv[i + 1]

    def _arg(flag, default, cast):
        if flag in sys.argv:
            j = sys.argv.index(flag)
            if j + 1 < len(sys.argv):
                try:
                    return cast(sys.argv[j + 1])
                except ValueError:
                    return default
        return default

    if root:
        tr = default_ollama_transport()
        rel_tr = tr if "--llm-relevance" in sys.argv else None
        res = explore(root, tr,
                      max_depth=_arg("--depth", 3, int),
                      max_nodes=_arg("--max-nodes", 300, int),
                      breadth=_arg("--breadth", 8, int),
                      relevance_min=_arg("--rel-min", 0.0, float),
                      relevance_transport=rel_tr)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("binggu_branch_explorer — use --selftest, or "
              "--explore '<주제>' [--depth N] [--max-nodes N] [--breadth N] [--rel-min F] [--llm-relevance]")
        print("import: explore(root, transport) -> {nodes, edges, pruned, stats}")
