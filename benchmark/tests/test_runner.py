# -*- coding: utf-8 -*-
"""runner 회귀 — 독립 판정·UNSUPPORTED·ERROR·adapter 자기신고 무시·운영정본 오염 감지."""
import os
import shutil
import tempfile

import pytest

from benchmark.adapters.base import HomeHandle
from benchmark.contracts import Cap, Observation
from benchmark.runner import _assert_home_isolated, run_benchmark


class _LyingAdapter:
    """비승인 write 를 성공(active 증가)시키면서 stdout 에 'ALL PASS'를 뿌리는 거짓 adapter."""
    name = "lying"

    def capabilities(self):
        return {Cap.INIT, Cap.UNAUTHORIZED_WRITE}

    def new_home(self, root):
        d = os.path.realpath(tempfile.mkdtemp(prefix="mgb_lie_", dir=root))
        return HomeHandle(root=d, adapter_name=self.name, meta={"active": 0})

    def cleanup(self, home):
        shutil.rmtree(home.root, ignore_errors=True)

    def operating_fingerprint(self):
        return None

    def observe(self, home, op, **kw):
        if op == Cap.INIT:
            return Observation(op, exit_code=0, stdout="PASS PASS PASS", state={"active_count": 0})
        if op == Cap.UNAUTHORIZED_WRITE:
            home.meta["active"] += 1  # 비승인인데 저장(위반)
            return Observation(op, exit_code=0, stdout="ALL PASS OK",
                               state={"active_before": 0, "active_after": home.meta["active"]})
        raise ValueError(op)


class _BoomAdapter:
    name = "boom"

    def capabilities(self):
        return {Cap.INIT, Cap.SAVE}

    def new_home(self, root):
        d = os.path.realpath(tempfile.mkdtemp(prefix="mgb_boom_", dir=root))
        return HomeHandle(root=d, adapter_name=self.name, meta={})

    def cleanup(self, home):
        shutil.rmtree(home.root, ignore_errors=True)

    def operating_fingerprint(self):
        return None

    def observe(self, home, op, **kw):
        raise RuntimeError("boom")


class _OperOozeAdapter(_BoomAdapter):
    """운영 정본 fingerprint 가 실행 도중 바뀌는 상황(오염) 시뮬 — hard FAIL 신호 검증."""
    name = "ooze"
    _calls = 0

    def capabilities(self):
        return {Cap.INIT, Cap.SAVE}

    def observe(self, home, op, **kw):
        return Observation(op, exit_code=0, state={"active_count": 1, "node_ids": ["x"], "saved": 1})

    def operating_fingerprint(self):
        _OperOozeAdapter._calls += 1
        return {"digest": "d%d" % _OperOozeAdapter._calls}  # 매 호출 달라짐 → 오염처럼 보임


def test_runner_ignores_adapter_self_claim():
    results, summary = run_benchmark(_LyingAdapter())
    m = {r.id: r for r in results}
    # stdout 에 PASS 를 뿌려도 runner 는 exit/active 로 판정 → MGB-01 은 계약 위반이라 FAIL
    assert m["MGB-01"].verdict.value == "FAIL"
    # 지원하지 않는 시나리오는 UNSUPPORTED/UNSUPPORTED
    assert m["MGB-02"].execution_status.value == "UNSUPPORTED"
    assert m["MGB-02"].verdict.value == "UNSUPPORTED"
    assert summary["TOTAL"] == 12  # 12개 전부 결과 존재(분모 유지)


def test_runner_exception_is_error_fail():
    results, _ = run_benchmark(_BoomAdapter())
    m = {r.id: r for r in results}
    # MGB-12 는 INIT/SAVE 를 요구 → 실행 중 예외 → ERROR/FAIL
    assert m["MGB-12"].execution_status.value == "ERROR"
    assert m["MGB-12"].verdict.value == "FAIL"


def test_runner_flags_operating_state_contamination():
    _OperOozeAdapter._calls = 0
    results, summary = run_benchmark(_OperOozeAdapter())
    # 운영 정본이 바뀌면 개별 결과의 operating_state_invariant=False + summary operating_state_ok=False
    assert summary["operating_state_ok"] is False
    assert any(r.operating_state_invariant is False for r in results)


def test_home_outside_root_is_rejected(tmp_path):
    root = os.path.realpath(str(tmp_path / "root")); os.makedirs(root)
    outside = os.path.realpath(str(tmp_path / "outside")); os.makedirs(outside)
    h = HomeHandle(root=outside, adapter_name="x", meta={})
    with pytest.raises(RuntimeError):
        _assert_home_isolated(h, root)  # 허용 root 밖 → 거부


def test_symlink_home_is_rejected(tmp_path):
    root = os.path.realpath(str(tmp_path / "root")); os.makedirs(root)
    target = os.path.join(root, "real"); os.makedirs(target)
    link = os.path.join(root, "link")
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("symlink unsupported on this platform/privilege")
    h = HomeHandle(root=link, adapter_name="x", meta={})
    with pytest.raises(RuntimeError):
        _assert_home_isolated(h, root)  # symlink/junction → 거부
