# -*- coding: utf-8 -*-
"""LocalBinggu 전용 read-only match policy (backward-compatible thin wrapper).

v1.11.0 strangler phase2: 핵심 로직은 binggupack.policy.match 로 이관됐고, 이 파일은
공개 심볼/동작이 byte-identical 한 thin wrapper 다. 기존 호출처
(import localbinggu_match_policy as mp → mp.classify_edge_pair 등)는 그대로 동작한다.

CLI(demo): python scripts/localbinggu_match_policy.py
  (reingest_pack_draft/localcrab_import_package.json 로드 — fixture 경로는 repo 기준 유지)
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.policy.match import (  # noqa: E402,F401  (밑줄 내부 심볼 + 전체 명시 re-export)
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
    _norm,
    _shash,
    _sim,
    _role,
    _sim_id,
    _is_d9_protect,
    _classify_pair_core,
    _edge_role,
)

__all__ = (
    'RF',
    'FUZZY_THRESHOLD',
    'SIM_T2',
    'SIM_T3',
    'EDGE_RELATION_ALLOWED',
    'normalize_nodes',
    'classify_pair',
    'classify_edge_pair',
    'evaluate',
    'evaluate_edges',
    'summarize',
    'summarize_edges',
    '_norm',
    '_shash',
    '_sim',
    '_role',
    '_sim_id',
    '_is_d9_protect',
    '_classify_pair_core',
    '_edge_role',
)

if __name__ == "__main__":
    import json
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent
    pkg = json.load((base / "reingest_pack_draft" / "localcrab_import_package.json").open(encoding="utf-8"))
    nodes = normalize_nodes(pkg["graph"]["nodes"])
    buckets, fuzzy, cda = evaluate(nodes)
    print(json.dumps(summarize(buckets, fuzzy, cda), ensure_ascii=False, indent=2))
