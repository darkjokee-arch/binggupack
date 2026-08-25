"""회상 도장이 **한 화면에서만 보이던 것** 봉합 (2026-08-25 owner "이어").

▶ 무슨 일이 있었나
  같은 저장소를 보고도 세 화면의 그림이 달랐다.
    CLI  `trace review`  →  748건      (AI 가 하나도 안 찍은 것처럼 보인다)
    세션 preview          →  33건 중 24건 도장(73%)
    `status`              →  도장 얘기가 아예 없다
  실측: 전체 pending 2,305 중 AI 도장 1,557(used 577 · ignored 978 · corrected 2) ·
  미도장 748 · 이행률 67.6%. CLI 만 보고 "잘 하고 있지 않다" 고 잘못 보고했다.

▶ 무엇을 잠그나
  ① `trace review` 요약에 도장률이 **언제나** 나온다(미도장만 볼 때도)
  ② `--all` 로 AI 도장분을 볼 수 있고 그 항목엔 `AI:verdict` 가 붙는다
  ③ `status` 에도 같은 숫자가 나온다 — 한 곳에서만 보이면 다시 갈린다
"""
import io
import os
import re

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "binggu.py")
TEXT = io.open(SRC, encoding="utf-8").read()


def _block(start, end):
    a = TEXT.index(start)
    b = TEXT.index(end, a)
    return TEXT[a:b]


def test_review_always_counts_ai_stamps():
    """미도장만 보여줄 때도 도장률을 세야 한다 — 세지 않으면 화면이 다시 갈린다."""
    body = _block("def _trace_review(", "def cmd_trace(")
    assert "include_ai_stamped=True" in body, "review 가 AI 도장분을 아예 안 센다"
    assert "AI 도장 %d건" in body, "요약 줄에 도장 수가 없다"
    assert "미도장 %d건" in body, "요약 줄에 미도장 수가 없다"


def test_review_shows_verdict_tag_on_stamped_items():
    """--all 로 볼 때 그 항목이 어떤 판정인지 붙어야 owner 가 덮어쓸지 정한다."""
    body = _block("def _trace_review(", "def cmd_trace(")
    assert re.search(r'AI:%s', body), "항목에 AI 판정 표기가 없다"
    assert "show_ai" in body, "--all 분기가 없다"


def test_all_flag_registered():
    """CLI 에 --all 이 실제로 등록돼 있어야 한다(문서만 있고 인자가 없으면 무용)."""
    assert '"--all", dest="show_all"' in TEXT, "--all 인자가 파서에 없다"
    assert "show_ai=bool(getattr(a, 'show_all', False))" in TEXT, "--all 이 review 로 안 전달된다"


def test_status_reports_same_number():
    """status 도 같은 숫자를 말해야 한다 — 한 곳에서만 보이면 갈린다."""
    assert "회상 도장(AI use-time" in TEXT, "status 에 도장률이 없다"
    assert "미도장 %d건" in TEXT


def test_status_stamp_line_never_kills_status():
    """표시 전용이므로 실패해도 status 전체를 죽이면 안 된다(MF5 정합)."""
    at = TEXT.index("회상 도장(AI use-time")
    tail = TEXT[at:at + 600]
    assert "except Exception" in tail, "도장 줄이 예외를 안 삼킨다 — status 가 통째로 죽을 수 있다"
