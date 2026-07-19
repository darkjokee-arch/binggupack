"""세션 마무리 복붙 블록 — 한 발화 다종 저장 인식 + 히트 H버그 수정 + L1 제외.

4cli+Fable5 수렴(2026-07-19): 통합 파서(fullmatch 계약 위반) 대신 게이트 줄단위 인식을
활용한 복붙 블록. owner 가 블록을 한 메시지로 붙여넣으면 각 줄이 자기 종류로 도장된다.
"""
from binggupack.review import session_close as SC
from binggupack.safety import gate_text, gate_log


def _summary():
    return {
        "preview": {"available": True, "count": 2,
                    "items": [{"label": "저장후보A"}, {"label": "저장후보B"}]},
        "recall_hits": {"available": True, "count": 2,
                        "items": [{"idx": 1, "claim": "회상1"}, {"idx": 2, "claim": "회상2"}]},
        "l1_proposals": {"available": True, "count": 1,
                         "items": [{"idx": 1, "proposition": "AI제안X", "source": "원문Y"}]},
        "governance": {"available": False, "note": "-"},
        "save_action": {"how": "저장·도장은 사람이 직접."},
    }


def test_paste_block_one_utterance_multi_type():
    """복붙 블록을 한 발화로 붙여넣으면 SAVE·히트가 각 줄로 동시 인식(통합 파서 불요)."""
    block = SC._build_paste_block(_summary())
    assert block == ["SAVE 1,2", "히트 1,2"]
    one_utterance = "\n".join(block)
    assert gate_text.parse_save_indices(one_utterance) == [1, 2]
    assert gate_log.parse_hit_stamps(one_utterance)["hit"] == [1, 2]


def test_hit_label_no_H_prefix():
    """회상 히트 라벨·안내가 H 접두 없이 숫자 — 게이트 정규식(히트 \\d+) 정합(도장 증발 버그 수정)."""
    md = SC.render_close_md(_summary())
    assert "- H1." not in md
    assert "히트 H1" not in md
    assert "- 1. 회상1" in md
    assert "히트 1,2" in md


def test_l1_excluded_from_paste_block():
    """L1 제안(P)은 destination(단계2) 미배선이라 복붙 블록에서 제외(죽은 명령 방지)."""
    block = SC._build_paste_block(_summary())
    assert not any("P1" in b or "제안" in b for b in block)


def test_paste_block_empty_when_no_candidates():
    """저장·히트 후보 0이면 블록 빈 리스트."""
    empty = {"preview": {"available": True, "count": 0},
             "recall_hits": {"available": True, "count": 0}}
    assert SC._build_paste_block(empty) == []
