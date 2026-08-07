"""B-08 회귀 — save-batch 기저장 재등장·조용한 실패 표면화.

결함(2026-08-04 심야 SAVE 집행 중 발견 · 장부 B-08):
  ① 이미 원장에 저장된 발화가 preview 후보로 재등장(세션 귀속 필터가 못 거르는 경로 —
     구형 앵커=전체 목록·이전 세션 잔존·배치 밖 경로 저장). all-fail 배치가 조기 return 으로
     버퍼 상태 전이를 건너뛰어 같은 후보가 세션마다 되살아났다.
  ② 저장 전건 실패 시 최상위 reason 이 상시 None — CLI 가 'BLOCK: None' 만 출력하고
     results[].reason(개별 사유)이 증발했다(조용한 실패).

수리(2026-08-07): stale_ledger_ids(원장 대조 사전 제외 · 저장 경로와 같은 node id 계산) +
transition_targets(성공/기존재 공통 전이) + all_failed(...) reason 집계.
"""
import os
import sqlite3
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggu_save_batch import (  # noqa: E402
    save_candidates_batch, stage_batch_anchor, stale_ledger_ids, transition_targets,
)
from binggupack.safety import gate_log  # noqa: E402

PAIR_TEXT = "그 판단은 틀렸고 원문 그대로 저장하는 게 맞다"
AI_CTX = "요약해서 저장하는 편이 효율적입니다"
SOLO_TEXT = "빠른 결정이 느린 완벽보다 낫다"


class _StubDB:
    """nodes 테이블만 있는 read-only 대역 — stale 판정은 node_id 존재 여부만 본다."""

    def __init__(self, node_ids=()):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("CREATE TABLE nodes(node_id TEXT PRIMARY KEY)")
        self.con.executemany("INSERT INTO nodes(node_id) VALUES(?)",
                             [(n,) for n in node_ids])


def _conv_id(sentence):
    from openbinggu_conversation_candidate_save import _sent_hash
    return "node:CONV:" + _sent_hash(sentence)


def test_transition_targets_saved_and_dup_only():
    """저장 성공 + 기존재(pair_partial_exists)만 전이 — 그 외 실패(pii 등)를 전이하면
    미저장 발화가 preview 에서 사라진다(유실)."""
    tt = transition_targets([
        {"cand": 1, "applied": True, "reason": None},
        {"cand": 2, "applied": False, "reason": "pair_partial_exists"},
        {"cand": 3, "applied": False, "reason": "pii_or_secret"},
        {"cand": 4, "applied": False, "reason": "candidate_missing"},
    ])
    assert tt == {1, 2}
    assert transition_targets(None) == set()


def test_stale_pair_item_owner_whole_node():
    """pair 후보: owner 전문 노드가 원장에 있으면 stale(save_paired 가 pair_partial_exists 로
    전건 차단 → 새로 저장될 게 없다) / 없으면 유지."""
    it = {"buffer_id": 7, "idx": 2, "text": PAIR_TEXT, "ai_context": AI_CTX}
    assert stale_ledger_ids(_StubDB([_conv_id(PAIR_TEXT)]), [it]) == [7]
    assert stale_ledger_ids(_StubDB(), [it]) == []


def test_stale_pair_item_ai_node_also_blocks():
    """pair 후보: ai 노드 기존재도 pair 전체를 막으므로 stale."""
    it = {"buffer_id": 9, "idx": 1, "text": PAIR_TEXT, "ai_context": AI_CTX}
    assert stale_ledger_ids(_StubDB([_conv_id(AI_CTX)]), [it]) == [9]


def test_stale_solo_requires_all_candidates_present():
    """단독 후보: 주 목록 전 pick 이 기존재일 때만 stale — 일부만 있으면 유지(나머지 저장 기회)."""
    from openbinggu_conversation_capture_preview import capture_preview
    it = {"buffer_id": 3, "idx": 1, "text": SOLO_TEXT}
    sents = [c["sentence"] for c in capture_preview(SOLO_TEXT, explicit=True)["candidates"]]
    assert sents, "전제: 이 문장은 capture_preview 후보를 만든다"
    assert stale_ledger_ids(_StubDB([_conv_id(s) for s in sents]), [it]) == [3]
    assert stale_ledger_ids(_StubDB(), [it]) == []


def test_stale_check_is_conservative_on_bad_items():
    """판정 불가(빈 텍스트·buffer_id 없음)는 유지 — 잘못 제외가 재등장 소음보다 큰 손실."""
    db = _StubDB([_conv_id(PAIR_TEXT)])
    assert stale_ledger_ids(db, [{"idx": 1, "text": PAIR_TEXT},          # buffer_id 없음
                                 {"buffer_id": 5, "idx": 2, "text": ""},  # 빈 텍스트
                                 None]) == []                             # 형식 불량


def test_all_fail_reason_aggregates_and_transitions(monkeypatch):
    """전건 실패 배치: 최상위 reason 이 개별 사유 집계('BLOCK: None' 금지) + 기존재 후보는
    전이 대상으로 표기된다(재등장 루프 차단의 재료)."""
    import binggupack.storage as storage
    monkeypatch.setattr(storage, "save_paired",
                        lambda *a, **k: {"applied": False, "reason": "pair_partial_exists",
                                         "pack_id": None})
    items = [{"idx": 1, "text": PAIR_TEXT, "ai_context": AI_CTX, "buffer_id": 1}]
    with tempfile.TemporaryDirectory() as td:
        anchor = os.path.join(td, "last_preview_candidates.json")
        gate = os.path.join(td, "save_gate_log.jsonl")
        stage_batch_anchor(items, path=anchor)
        gate_log.gate_record_from_prompt("SAVE 1", preview_path=anchor, gate_path=gate)
        r = save_candidates_batch(None, None, items, [1], gate_log_path=gate)
    assert r["applied"] is False and r["saved"] == 0
    assert r["reason"] == "all_failed(pair_partial_exists×1)"
    assert transition_targets(r["results"]) == {1}


def test_saved_batch_keeps_reason_none(monkeypatch):
    """1건이라도 저장되면 최상위 reason 은 종전대로 None(성공 경로 계약 불변)."""
    import binggupack.storage as storage
    monkeypatch.setattr(storage, "save_paired",
                        lambda *a, **k: {"applied": True, "reason": None, "pack_id": "p1"})
    items = [{"idx": 1, "text": PAIR_TEXT, "ai_context": AI_CTX, "buffer_id": 1}]
    with tempfile.TemporaryDirectory() as td:
        anchor = os.path.join(td, "last_preview_candidates.json")
        gate = os.path.join(td, "save_gate_log.jsonl")
        stage_batch_anchor(items, path=anchor)
        gate_log.gate_record_from_prompt("SAVE 1", preview_path=anchor, gate_path=gate)
        r = save_candidates_batch(None, None, items, [1], gate_log_path=gate)
    assert r["applied"] is True and r["saved"] == 1 and r["reason"] is None
