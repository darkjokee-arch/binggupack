# -*- coding: utf-8 -*-
"""scripts/binggu_recall_trace.py(thin shim)가 정본의 `_` 심볼을 하나도 안 흘리는지 못박는다.

★ 왜 (2026-08-08 사장님 지적 "회상 히트/미스는 왜 안나오냐")
  shim 은 `from ... import *` 로 공개 심볼을 받고, **밑줄 심볼은 손으로 나열**해 왔다.
  파이썬의 `import *` 가 `_` 로 시작하는 이름을 안 가져오기 때문이다. 그런데 2026-08-01 에
  정본으로 들어온 `_autoinject_judgeable`(자동주입 회상의 판정 대상 판별 · owner B안)이
  그 목록에 안 들어갔다. shim 을 경유하는 `server_handlers._u_trace_stamp` 가 AttributeError 로
  죽었고, 그래서 **자동주입 회상 1,373건이 도장을 한 번도 못 받았다**(판정 16건 = 1.2% ·
  그마저 전부 사람 도장 · AI 도장 0건). 정본을 고치고 사본을 안 고친 전형이다.

  shim 에 자동 보강 루프를 넣어 고쳤지만, 그 루프가 나중에 지워지면 같은 사고가 재발한다.
  이 테스트가 그 자리를 지킨다 — **정본에 심볼이 늘면 shim 도 자동으로 따라가야 한다.**

read-only: import 만 한다(store write 0 · 운영홈 미접촉).
"""
import importlib
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")


@pytest.fixture(scope="module")
def shim_and_source():
    for p in (REPO, SCRIPTS):
        if p not in sys.path:
            sys.path.insert(0, p)
    shim = importlib.import_module("binggu_recall_trace")
    src = importlib.import_module("binggupack.pack.recall_trace")
    return shim, src


def test_shim_reexports_every_underscore_symbol(shim_and_source):
    """정본의 모든 `_` 심볼(던더 제외)이 shim 에서 그대로 보인다."""
    shim, src = shim_and_source
    under = [n for n in dir(src) if n.startswith("_") and not n.startswith("__")]
    assert under, "정본에 `_` 심볼이 하나도 없다면 이 테스트의 전제가 깨진 것이다"
    missing = [n for n in under if not hasattr(shim, n)]
    assert not missing, (
        "shim 이 정본의 `_` 심볼을 흘렸다: %s — `import *` 는 밑줄을 안 가져온다. "
        "shim 하단 자동 보강 루프가 살아 있는지 확인할 것(2026-08-08 회상 도장 사고)" % missing
    )


def test_shim_symbols_are_the_same_objects(shim_and_source):
    """재노출이 '이름만 같은 다른 것'이 되면 안 된다 — 같은 객체여야 상태가 갈리지 않는다."""
    shim, src = shim_and_source
    for name in ("_autoinject_judgeable", "_open_store_ro", "_ts_iso", "record_outcome",
                 "list_pending", "AUTOINJECT_KINDS"):
        assert hasattr(shim, name), f"{name} 가 shim 에 없다"
        assert getattr(shim, name) is getattr(src, name), f"{name} 가 정본과 다른 객체다"


def test_autoinject_stamp_path_is_reachable_through_shim(shim_and_source):
    """실제로 터졌던 경로 — 자동주입 도장이 shim 경유로 살아 있는지 직접 확인한다."""
    shim, _src = shim_and_source
    judgeable = shim._autoinject_judgeable(
        [{"node_id": "node:CONV:aa", "rank": 1.0, "relevance": 0.9},
         {"node_id": "node:CONV:bb", "rank": 0.5, "relevance": 0.1}]
    )
    ids = {n.get("node_id") for n in judgeable}
    assert "node:CONV:aa" in ids, "관련도 높은 노드는 판정 대상이어야 한다"
    assert "node:CONV:bb" not in ids, "관련도 하한 미달은 판정 대상에서 빠져야 한다(owner B안)"
    assert "preflight" in shim.AUTOINJECT_KINDS, "자동주입 kind 가 도장 대상 목록에 있어야 한다"
