"""save-batch 세션 경계 단일화 회귀 — 마무리 preview·앵커·저장 목록 통일(이원화 오저장 방지).

버그(2026-07-20): 마무리 hook 은 render_preview(session_id)(세션 필터)를 보여주지만
cmd_save_batch 는 render_preview()(전체 누적)로 저장 → idx 축이 어긋나 owner 가 preview
번호로 'SAVE n' 쳐도 다른 원문 저장(또는 pref 불일치로 저장 실패). session_id 를 앵커에
심어 3자(마무리 preview · 앵커 · 저장)를 같은 세션 목록으로 통일한다.
"""
from pathlib import Path
import json
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.safety import gate_log  # noqa: E402
from binggu_save_batch import stage_batch_anchor, _anchor_candidates  # noqa: E402

CWD = "C:/Users/fixture-user/binggupack"


def _make_home(td):
    home = os.path.join(td, ".binggupack")
    os.makedirs(home)
    with open(os.path.join(home, "capture_enabled"), "w", encoding="utf-8") as f:
        f.write("1")
    with open(os.path.join(home, "capture_scope.json"), "w", encoding="utf-8") as f:
        json.dump({"allowed_cwd_prefixes": [CWD], "denied_cwd_substrings": ["example-project"]},
                  f, ensure_ascii=False)
    return home


def _seed_two_sessions(home):
    """과거 세션(S1 누적) + 이번 세션(S2) 발화를 섞어 capture — 판단/교훈 문장(분류기 통과)."""
    from binggu_capture_persist import PersistentCaptureBuffer
    b = PersistentCaptureBuffer(home=home)
    b.feed("과거 세션에는 A안으로 결정한다", CWD, session_id="S1")
    b.feed("과거 세션 이 패턴은 항상 버그를 유발한다는 교훈", CWD, session_id="S1")
    b.feed("이번 세션엔 B안으로 결정한다", CWD, session_id="S2")
    b.feed("이번 세션 저 방식이 더 빠르다는 교훈", CWD, session_id="S2")
    return b


def test_session_filter_reduces_and_shifts_idx():
    """전체 목록 > 세션 목록, 같은 idx=1 이라도 원문이 다름(이원화가 오저장이 되는 근거)."""
    with tempfile.TemporaryDirectory() as td:
        home = _make_home(td)
        _seed_two_sessions(home)
        from binggu_capture_persist import PersistentCaptureBuffer
        buf = PersistentCaptureBuffer(home=home)
        full = buf.render_preview().get("items", [])
        s2 = buf.render_preview(session_id="S2").get("items", [])
        assert len(s2) >= 1
        assert len(full) > len(s2)                 # 누적에 S1 포함
        assert full[0]["text"] != s2[0]["text"]    # 같은 idx1 다른 원문 = 이원화 오저장 조건


def test_anchor_carries_session_id_and_pref_parity():
    """마무리 hook 이 S2 목록+session_id 로 앵커 stage → round-trip + 저장 재현 pref 가 앵커 pref 와 일치."""
    with tempfile.TemporaryDirectory() as td:
        home = _make_home(td)
        _seed_two_sessions(home)
        from binggu_capture_persist import PersistentCaptureBuffer
        buf = PersistentCaptureBuffer(home=home)
        s2 = buf.render_preview(session_id="S2").get("items", [])
        anchor = os.path.join(home, "last_preview_candidates.json")
        stage_batch_anchor(s2, path=anchor, session_id="S2")

        data = json.loads(Path(anchor).read_text(encoding='utf-8'))
        assert data.get("session_id") == "S2"

        import binggu
        assert binggu._anchor_session_id(anchor) == "S2"

        # 저장 경로가 앵커 session_id 로 재현한 목록의 pref == 앵커 pref (SAVE n 대조 통과 근거)
        replay = buf.render_preview(session_id="S2").get("items", [])
        pref_replay = gate_log.preview_ref_for_candidates(_anchor_candidates(replay))
        assert pref_replay == data["pref"]

        # 전체 목록 pref 는 앵커 pref 와 다름 — 수정 전엔 이 값으로 대조돼 오저장/실패했다
        full = buf.render_preview().get("items", [])
        pref_full = gate_log.preview_ref_for_candidates(_anchor_candidates(full))
        assert pref_full != data["pref"]


def test_save_gate_matches_session_list_not_full():
    """owner 'SAVE 1' → 앵커(S2) pref 로 gate 기록 → 세션 목록 pref 통과 / 전체 목록 pref 차단."""
    with tempfile.TemporaryDirectory() as td:
        home = _make_home(td)
        _seed_two_sessions(home)
        from binggu_capture_persist import PersistentCaptureBuffer
        buf = PersistentCaptureBuffer(home=home)
        s2 = buf.render_preview(session_id="S2").get("items", [])
        anchor = os.path.join(home, "last_preview_candidates.json")
        gate = os.path.join(home, "save_gate_log.jsonl")
        stage_batch_anchor(s2, path=anchor, session_id="S2")

        gate_log.gate_record_from_prompt("SAVE 1", preview_path=anchor, gate_path=gate)

        pref_s2 = gate_log.preview_ref_for_candidates(_anchor_candidates(s2))
        pref_full = gate_log.preview_ref_for_candidates(
            _anchor_candidates(buf.render_preview().get("items", [])))
        assert gate_log.gate_human_for_ref(pref_s2, [1], path=gate) is True
        assert gate_log.gate_human_for_ref(pref_full, [1], path=gate) is False


def test_no_session_id_anchor_falls_back_full():
    """구형 앵커(session_id 없음) → _anchor_session_id None → 전체 목록(하위호환)."""
    with tempfile.TemporaryDirectory() as td:
        home = _make_home(td)
        _seed_two_sessions(home)
        from binggu_capture_persist import PersistentCaptureBuffer
        buf = PersistentCaptureBuffer(home=home)
        full = buf.render_preview().get("items", [])
        anchor = os.path.join(home, "last_preview_candidates.json")
        stage_batch_anchor(full, path=anchor)  # session_id 미전달 = 구형

        data = json.loads(Path(anchor).read_text(encoding='utf-8'))
        assert "session_id" not in data            # 필드 미기록(구 앵커와 byte 호환)
        import binggu
        assert binggu._anchor_session_id(anchor) is None
