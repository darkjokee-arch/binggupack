"""자동 주입(preflight)은 16자 미만 조각을 싣지 않는다 — 직접 인출·위험반문은 그대로(2026-11-14)."""
import sqlite3

from binggupack.pack import recall as R

FRAG = "검수 끝났습니다."                                   # 9자 — 질문 낱말과 그대로 겹친다
FULL = "검수 끝났습니다 라고 말하기 전에 산출물을 실제로 열어 확인한다"


def _ledger(path, rows):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE nodes (node_id TEXT, node_type TEXT, sentence TEXT, state TEXT,"
                " semantic_subtype TEXT, created_at TEXT, use_count INTEGER)")
    for i, (sent, sub) in enumerate(rows):
        con.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?)",
                    ("n%d" % i, "judgment", sent, "active", sub, "2026-11-01T00:00:00Z", 0))
    con.commit()
    con.close()
    return str(path)


def test_preflight_drops_fragments_but_direct_recall_keeps_them(tmp_path):
    led = _ledger(tmp_path / "l.sqlite", [(FRAG, "결정"), (FULL, "교훈")])
    pf = R.preflight_context(led, prompt="검수 끝났습니다. 확인", home=str(tmp_path))
    got = [n["claim"] for n in pf["remember"]]
    assert FULL in got and FRAG not in got
    direct = [n["claim"] for n in R.why_search(led, "검수 끝났습니다. 확인", home=str(tmp_path))["relevant_nodes"]]
    assert FRAG in direct and FULL in direct


def test_fragment_does_not_take_a_slot(tmp_path):
    # 조각이 상위를 다 차지해도 자리를 먹지 않는다 — 걸러낸 뒤에 상위 N 을 자른다
    rows = [("검수 끝 %d." % i, "결정") for i in range(8)] + [(FULL, "교훈")]
    pf = R.preflight_context(_ledger(tmp_path / "l.sqlite", rows), prompt="검수 끝 확인", home=str(tmp_path))
    assert [n["claim"] for n in pf["remember"]] == [FULL]


def test_risk_question_axis_unchanged(tmp_path):
    led = _ledger(tmp_path / "l.sqlite", [("배포 전 확인 누락", "버그패턴")])   # 10자 위험패턴
    pf = R.preflight_context(led, prompt="배포 전 확인 누락", home=str(tmp_path))
    assert pf["remember"] == [] and pf["avoid_patterns"] and pf["risk_level"] in ("중간", "높음")


def test_threshold_boundary():
    assert R.PREFLIGHT_MIN_CHARS == 16
    assert len("가" * 16) >= R.PREFLIGHT_MIN_CHARS > len("가" * 15)
