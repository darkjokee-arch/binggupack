# -*- coding: utf-8 -*-
"""binggu_workspace_organize — 항목 W: 클라우드 workspace 정리(비파괴) 분석 (thin wrapper).

v1.11.0 save-gate 라인: detector/리포트 본문 정본은 binggupack.workspace.organize 로 이관됐고,
이 파일은 공개 심볼/동작/detector 로직이 byte-identical 한 backward-compatible thin wrapper 다.
기존 호출처(import binggu_workspace_organize / --selftest CLI / publish_run_all_selftests)는
그대로 동작한다. 읽기 전용·raise 0·비파괴 불변은 정본 모듈에서 보장·검증한다.

CLI: python scripts/binggu_workspace_organize.py --selftest
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)   # binggupack 패키지 import 경로

from binggupack.workspace.organize import *  # noqa: E402,F401,F403
from binggupack.workspace.organize import (  # noqa: E402,F401  (전체 명시 re-export — 밑줄 심볼 포함)
    RESOURCES,
    _ID_KEYS,
    _TYPE_KEYS,
    _TITLE_KEYS,
    _TOPIC_KEYS,
    _SENTENCE_KEYS,
    _NODE_IDS_KEYS,
    _EDGE_SRC_KEYS,
    _EDGE_DST_KEYS,
    _first,
    _as_list,
    _norm_id,
    _norm_type,
    _norm_title,
    _norm_topic,
    _norm_node_ids,
    _norm_sentence,
    _tokens,
    _sha8,
    fetch_workspace,
    detect_duplicate_packs,
    detect_orphan_nodes,
    detect_structure_candidates,
    _build_recommendations,
    build_report,
    analyze,
    _mk_workspace,
    _mk_transport,
    _selftest,
    main,
)

if __name__ == "__main__":
    sys.exit(main())
