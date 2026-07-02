# -*- coding: utf-8 -*-
"""양방향 신뢰도 — owner 직감 / ai 반박 적중률 (backward-compatible thin wrapper).

strangler: 순수 정본(record_resolution · record_stage1_selection · get_hit_rate · both_sides ·
proposal_priority_signal · snapshot_context · classify_outcome · assert_not_ranking_input ·
N_MIN · HALFLIFE_DAYS · PAIR_RELS · _selftest)은 binggupack.pack.hit_stats 로 byte-identical
이관됐고, 이 파일은 공개 심볼이 동일한 thin wrapper 다. 기존 호출처(import binggu_hit_stats —
binggu_hit_export · binggu_merkle_anchor · binggu_session_close · openbinggu_conversation_candidate_save
등)는 그대로 동작한다.

비인과 봉쇄(guard3 H1/H2)·시간감쇠·표본게이트·이중계상 가드는 1바이트도 변하지 않았다.
미이관 bare-name(binggu_recall._domain_from_cwd lazy · selftest 내 openbinggu_staging_write_selftest
fixture) lazy import 는 정본 모듈이 scripts/ 를 sys.path 에 얹어 해소한다(이 wrapper 도 동일하게 얹는다).

CLI: python scripts/binggu_hit_stats.py --selftest
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # <repo>/scripts
ROOT = os.path.dirname(HERE)                         # <repo>
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)   # binggupack 패키지 + scripts/ 형제 import 경로

from binggupack.pack.hit_stats import *  # noqa: E402,F401,F403
from binggupack.pack.hit_stats import (  # noqa: E402,F401  (전체 명시 re-export — _ 심볼 포함)
    N_MIN,
    HALFLIFE_DAYS,
    PAIR_RELS,
    record_resolution,
    record_stage1_selection,
    get_hit_rate,
    both_sides,
    proposal_priority_signal,
    snapshot_context,
    classify_outcome,
    assert_not_ranking_input,
    _selftest,
)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("binggu_hit_stats: --selftest 로 검증 실행")
