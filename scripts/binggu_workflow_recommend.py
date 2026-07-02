"""binggu_workflow_recommend — pack 기반 실행 가능 workflow spec 추천 (backward-compatible thin wrapper).

strangler: 순수 정본(WORKFLOW_TEMPLATES · _tokens · _pack_tokens · recommend · _selftest)은
binggupack.pack.workflow_recommend 로 byte-identical 이관됐고, 이 파일은 공개 심볼이 동일한 thin
wrapper 다. 기존 호출처(import binggu_workflow_recommend — binggu_topic_to_pack 등)는 그대로 동작한다.

selftest W10 의 watcher_batch_m1 의존은 정본 _selftest 내부 lazy import 지만, 이 wrapper 가
scripts/ 를 sys.path 에 얹으므로 진입점 실행 시 실제 scanner 로 검증된다(패키지 단독 실행 시엔
graceful skip). 런타임 이관부(recommend 등)는 cross-dep 0.

CLI: python scripts/binggu_workflow_recommend.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.workflow_recommend import *  # noqa: E402,F401,F403
from binggupack.pack.workflow_recommend import (  # noqa: E402,F401  (전체 명시 re-export)
    WORKFLOW_TEMPLATES,
    _tokens,
    _pack_tokens,
    recommend,
    _selftest,
)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
    print("binggu_workflow_recommend — use --selftest, or import recommend(pack)")
