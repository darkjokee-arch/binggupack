# -*- coding: utf-8 -*-
"""4cli 사후 강화 회귀 — 우연통과 방지·운영 sentinel 확장·MGB-08 특이성.

시나리오 판정 로직을 스텁 Observation 으로 단위 검증한다(공개 CLI 미실행·빠름).
"""
import os

from benchmark.adapters.binggupack import BingguPackAdapter, _fingerprint
from benchmark.contracts import (
    REJECTION_CONTENT_BINDING, REJECTION_OTHER, Cap, Observation, classify_rejection)
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


# ── issue #54.1 — 거부 코드 판정 결속 ──

def test_classify_rejection_maps_codes_to_classes():
    assert classify_rejection("preview_required_mismatch") == REJECTION_CONTENT_BINDING
    assert classify_rejection("content_binding_mismatch") == REJECTION_CONTENT_BINDING
    assert classify_rejection("PREVIEW_HASH_MISMATCH") == REJECTION_CONTENT_BINDING  # 키워드 폴백
    assert classify_rejection("no_candidates") == REJECTION_OTHER
    assert classify_rejection("confirm_mismatch") == REJECTION_OTHER
    assert classify_rejection("node_hash_mismatch") == REJECTION_OTHER  # 'mismatch' 만으론 결속 아님
    assert classify_rejection(None) is None
    assert classify_rejection("") is None
    assert classify_rejection("some_unregistered_code") is None  # 미등록 → 계약 강제 안 함


def test_mgb02_fails_on_nonbinding_rejection():
    # 완전한 조합이어도 거부 코드가 '엉뚱한 거부(other)'면 우연통과로 배제 → FAIL
    stub = _eb(preview_id_valid=True, baseline_exit=0, active_before=0,
               active_after_baseline=1, mutation_exit=1, active_after_mutation=1,
               mutation_digest_present=False, mutation_error_code="no_candidates")
    assert mgb_02.run(stub, _Home(), {}).verdict == Verdict.FAIL


def test_mgb02_passes_with_binding_error_code():
    stub = _eb(preview_id_valid=True, baseline_exit=0, active_before=0,
               active_after_baseline=1, mutation_exit=1, active_after_mutation=1,
               mutation_digest_present=False, mutation_error_code="preview_required_mismatch")
    assert mgb_02.run(stub, _Home(), {}).verdict == Verdict.PASS


def test_mgb02_passes_when_error_code_absent():
    # 안정 공개 거부 코드 없는 adapter(None) → 특정 코드 강제 없이 조합 판정에 폴백(이식성)
    stub = _eb(preview_id_valid=True, baseline_exit=0, active_before=0,
               active_after_baseline=1, mutation_exit=1, active_after_mutation=1,
               mutation_digest_present=False, mutation_error_code=None)
    assert mgb_02.run(stub, _Home(), {}).verdict == Verdict.PASS


# ── issue #54.2 — sentinel -journal 프로필 확장 ──

def test_sentinel_set_includes_journal():
    names = BingguPackAdapter._SENTINEL_NAMES
    assert "ledger.sqlite-journal" in names
    assert len(set(names)) == len(names)  # 중복 없음
    # WAL 4개 프로필 + rollback journal
    assert set(names) == {"ledger.sqlite", "ledger.sqlite-wal", "ledger.sqlite-shm",
                          "ledger.sqlite-journal", "approvals.jsonl"}


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
