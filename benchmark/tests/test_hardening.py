# -*- coding: utf-8 -*-
"""4cli 사후 강화 회귀 — 우연통과 방지·운영 sentinel 확장·MGB-08 특이성.

시나리오 판정 로직을 스텁 Observation 으로 단위 검증한다(공개 CLI 미실행·빠름).
"""
import os

from benchmark.adapters.binggupack import _fingerprint
from benchmark.contracts import Cap, Observation
from benchmark.result import Verdict
from benchmark.scenarios import mgb_02, mgb_08


class _Home:
    root = "stub"
    adapter_name = "stub"
    meta: dict = {}


class _Stub:
    def __init__(self, responses, caps):
        self._r = responses
        self._caps = caps

    def capabilities(self):
        return self._caps

    def observe(self, home, op, **kw):
        return self._r.get(op, Observation(op, exit_code=0, state={}))

    def operating_fingerprint(self):
        return None


_EB = Cap.EXACT_BINDING
_EB_CAPS = {Cap.INIT, _EB}


def _eb(**state):
    return _Stub({_EB: Observation(_EB, exit_code=state.get("mutation_exit", 1), state=state)},
                 _EB_CAPS)


def test_mgb02_rejects_empty_preview_baseline():
    # 빈 preview → preview 무효·baseline 실패 → 우연통과 금지
    stub = _eb(preview_id_valid=False, baseline_exit=1, active_before=0,
               active_after_baseline=0, mutation_exit=1, active_after_mutation=0,
               mutation_digest_present=False)
    assert mgb_02.run(stub, _Home(), {}).verdict == Verdict.FAIL


def test_mgb02_fails_when_mutation_digest_created():
    # baseline 성공이어도 변조 내용이 장부에 생성되면 FAIL
    stub = _eb(preview_id_valid=True, baseline_exit=0, active_before=0,
               active_after_baseline=1, mutation_exit=1, active_after_mutation=1,
               mutation_digest_present=True)
    assert mgb_02.run(stub, _Home(), {}).verdict == Verdict.FAIL


def test_mgb02_fails_when_mutation_increases_active():
    stub = _eb(preview_id_valid=True, baseline_exit=0, active_before=0,
               active_after_baseline=1, mutation_exit=0, active_after_mutation=2,
               mutation_digest_present=False)
    assert mgb_02.run(stub, _Home(), {}).verdict == Verdict.FAIL


def test_mgb02_passes_only_with_full_contract():
    stub = _eb(preview_id_valid=True, baseline_exit=0, active_before=0,
               active_after_baseline=1, mutation_exit=1, active_after_mutation=1,
               mutation_digest_present=False)
    assert mgb_02.run(stub, _Home(), {}).verdict == Verdict.PASS


class _RecallStub:
    def __init__(self, out):
        self._out = out

    def capabilities(self):
        return {Cap.INIT, Cap.SAVE, Cap.RECALL_FRESH}

    def observe(self, home, op, **kw):
        if op in (Cap.RECALL, Cap.RECALL_FRESH):
            return Observation(op, exit_code=0, stdout=self._out, state={})
        return Observation(op, exit_code=0, state={})

    def operating_fingerprint(self):
        return None


def test_mgb08_fails_on_distractor_only():
    assert mgb_08.run(_RecallStub(mgb_08._DIST1), _Home(), {}).verdict == Verdict.FAIL


def test_mgb08_fails_on_full_dump():
    dump = "\n".join([mgb_08._TARGET, mgb_08._DIST1, mgb_08._DIST2, mgb_08._HARD])
    assert mgb_08.run(_RecallStub(dump), _Home(), {}).verdict == Verdict.FAIL


def test_mgb08_passes_target_only():
    assert mgb_08.run(_RecallStub(mgb_08._TARGET), _Home(), {}).verdict == Verdict.PASS


def test_sentinel_detects_new_and_deleted_file(tmp_path):
    p = str(tmp_path / "ledger.sqlite-wal")
    before = _fingerprint(p)
    with open(p, "wb") as f:
        f.write(b"wal-data")
    after = _fingerprint(p)
    assert before["exists"] is False and after["exists"] is True and before != after
    os.remove(p)
    deleted = _fingerprint(p)
    assert deleted["exists"] is False and deleted != after


def test_sentinel_detects_content_change(tmp_path):
    p = str(tmp_path / "approvals.jsonl")
    with open(p, "wb") as f:
        f.write(b"a")
    fp0 = _fingerprint(p)
    with open(p, "wb") as f:
        f.write(b"ab")
    fp1 = _fingerprint(p)
    assert fp0 != fp1  # size/digest 변경 감지
