# -*- coding: utf-8 -*-
"""Reader-only adapter — 승인 저장을 소비만 하는 '모델 B'(recall/explain).

Memory PR 로컬 참조구현에서 '모델 A 저장 → 모델 B 회상/설명' 을 로컬로 재현할 때, 모델 B 는
쓰기(SAVE·supersede·pair 등)를 **선언하지 않는다**. 공유 정본(ledger.sqlite)을 읽기만 한다.

설계(core 무변경 · import 재사용만):
  · BingguPackAdapter 를 상속해 `_run`/recall·explain observe 기계만 물려받고,
    capabilities() 를 RECALL/RECALL_FRESH/EXPLAIN 3개로만 좁힌다(write cap 미선언).
  · observe() 는 reader cap 이 아닌 op(save/supersede/pair/…)을 명시적으로 거부한다 —
    "미지원(UNSUPPORTED)"이 아니라 아예 실행 경로가 없음을 코드로 보장한다.
  · 공유 홈은 외부에서 `BINGGU_HOME`(=writer 가 만든 격리 홈 root)으로 주입된다. reader 는
    자기 홈을 새로 만들지(new_home) 않고, 주어진 공유 root 를 그대로 바인딩한다.
  · cleanup 은 no-op — 공유 홈은 writer(모델 A) 소유라 reader 가 rmtree 하지 않는다.
"""
from __future__ import annotations

import os

from benchmark.adapters.base import HomeHandle
from benchmark.adapters.binggupack import BingguPackAdapter
from benchmark.contracts import Cap


class ReaderOnlyAdapter(BingguPackAdapter):
    """RECALL/RECALL_FRESH/EXPLAIN 만 노출하는 읽기 전용 adapter(모델 B)."""

    name = "binggupack_reader"

    # 읽기 전용 — write/mutation cap 은 의도적으로 미선언(SAVE·SUPERSEDE·PAIR·UNAUTHORIZED_WRITE·
    # EXACT_BINDING·REPLAY_APPROVAL·CAPTURE_CANDIDATE·REMOTE_INTENT·INIT·PREVIEW·LIST_ACTIVE 전부 제외).
    _READER_CAPS = frozenset({Cap.RECALL, Cap.RECALL_FRESH, Cap.EXPLAIN})

    def capabilities(self) -> set[str]:
        return set(self._READER_CAPS)

    def bind_home(self, shared_root: str) -> HomeHandle:
        """writer 가 만든 공유 격리 홈을 읽기 바인딩. 새 홈을 만들지 않는다(single-bus 공유)."""
        return HomeHandle(root=os.path.realpath(shared_root),
                          adapter_name=self.name, meta={"shared_readonly": True})

    def new_home(self, root: str) -> HomeHandle:
        # reader 는 홈을 소유·생성하지 않는다. 공유 홈은 bind_home 으로 주입받는다.
        raise NotImplementedError("ReaderOnlyAdapter 는 공유 홈을 bind_home 으로 받는다(new_home 금지)")

    def cleanup(self, home: HomeHandle) -> None:
        # 공유 홈은 writer(모델 A) 소유 — reader 가 rmtree 하면 A↔B 사이 정본이 사라진다(금지).
        return None

    def observe(self, home: HomeHandle, op: str, **kw):
        if op not in self._READER_CAPS:
            raise ValueError(
                "ReaderOnlyAdapter 는 읽기 op(recall/explain)만 지원 — write op 거부: %s" % op)
        return super().observe(home, op, **kw)
