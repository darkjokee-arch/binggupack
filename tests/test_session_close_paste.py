"""세션 마무리 복붙 블록 — 한 발화 다종 저장 인식 + 히트 H버그 수정 + L1 제외.

4cli+Fable5 수렴(2026-07-19): 통합 파서(fullmatch 계약 위반) 대신 게이트 줄단위 인식을
활용한 복붙 블록. owner 가 블록을 한 메시지로 붙여넣으면 각 줄이 자기 종류로 도장된다.
"""
from binggupack.review import session_close as SC
from binggupack.safety import gate_text


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


def test_paste_block_saves_only_no_auto_hit():
    """복붙 블록엔 SAVE 만 — 회상 판정(히트/미스)은 usefulness 100% 편향 방지 위해 자동 미포함.
    owner 가 §2 를 보고 도움=`히트 N`·헛다리=`미스 N` 을 골라 직접 도장(전체 자동 히트 제거)."""
    block = SC._build_paste_block(_summary())
    assert block == ["SAVE 1,2"]
    assert not any(b.startswith("히트") or b.startswith("미스") for b in block)
    one_utterance = "\n".join(block)
    assert gate_text.parse_save_indices(one_utterance) == [1, 2]


def test_hit_label_no_H_prefix():
    """회상 히트 라벨·안내가 H 접두 없이 숫자 — 게이트 정규식(히트 \\d+) 정합(도장 증발 버그 수정)."""
    md = SC.render_close_md(_summary())
    assert "- H1." not in md
    assert "히트 H1" not in md
    assert "- 1. 회상1" in md
    assert "히트 1,2" in md


def test_empty_recall_shows_why_not_bare_zero():
    """회상이 0으로 보일 때 **왜 0인지**를 화면이 말해야 한다.

    2026-08-01 owner 지적: 실제로는 이번 세션 회상이 17건 기록돼 있는데 마무리 화면엔 늘
    "회상 0"으로만 떠서 판정할 게 없는 줄 알았다. 코드는 note 로 이유를 내려보내는데
    렌더가 그걸 버리고 고정 문구를 찍고 있었다.
    """
    held = SC.render_close_md({"recall_hits": {
        "available": True, "count": 0, "scope": "session", "total_pending": 17,
        "auto_excluded": 17, "items": [],
        "note": "이번 세션 회상 17건 — 관련도 기준(0.60) 아래라 판정 목록에 안 올림"}})
    assert "관련도 기준(0.60)" in held, "코드가 준 사유가 화면에 그대로 나와야 한다"
    assert "17건 기록됨" in held, "장부에 남은 건수를 알려 '0건'으로 오해하지 않게 한다"
    assert "trace OFF 거나 회상 0" not in held, "옛 고정 문구가 남아 있으면 안 된다"

    # 진짜 0건과 장부를 못 읽은 경우는 서로 다른 문장이어야 한다(구분이 안 되면 진단이 안 된다).
    none = SC.render_close_md({"recall_hits": {
        "available": True, "count": 0, "scope": "session", "total_pending": 0,
        "items": [], "note": "이번 세션 회상 0건 — 판정 대상 없음."}})
    assert "판정 대상 없음" in none
    assert "기록됨" not in none, "0건인데 건수 줄을 덧붙이면 안 된다"

    off = SC.render_close_md({"recall_hits": {"available": False}})
    assert "읽지 못했다" in off


def test_l1_excluded_from_paste_block():
    """L1 제안(P)은 destination(단계2) 미배선이라 복붙 블록에서 제외(죽은 명령 방지)."""
    block = SC._build_paste_block(_summary())
    assert not any("P1" in b or "제안" in b for b in block)


def test_paste_block_empty_when_no_candidates():
    """저장·히트 후보 0이면 블록 빈 리스트."""
    empty = {"preview": {"available": True, "count": 0},
             "recall_hits": {"available": True, "count": 0}}
    assert SC._build_paste_block(empty) == []
