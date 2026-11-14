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


def test_dropped_slots_are_not_backfilled(tmp_path):
    # 상위 N 을 뽑은 뒤에 거른다 — 빠진 자리를 다음 순위(무관할 가능성이 큰 것)로 채우지 않는다
    rows = [("검수 끝 확인 %d." % i, "결정") for i in range(8)] + [("검수 관련해서 길게 적어 둔 다른 이야기 문장이다", "교훈")]
    pf = R.preflight_context(_ledger(tmp_path / "l.sqlite", rows), prompt="검수 끝 확인", home=str(tmp_path))
    assert pf["remember"] == []      # 상위 5개가 전부 조각이었고, 6위 이하로 채우지 않는다


def test_risk_question_axis_unchanged(tmp_path):
    led = _ledger(tmp_path / "l.sqlite", [("배포 전 확인 누락", "버그패턴")])   # 10자 위험패턴
    pf = R.preflight_context(led, prompt="배포 전 확인 누락", home=str(tmp_path))
    assert pf["remember"] == [] and pf["avoid_patterns"] and pf["risk_level"] in ("중간", "높음")


def _ledger_spk(path, rows):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE nodes (node_id TEXT, node_type TEXT, sentence TEXT, state TEXT,"
                " semantic_subtype TEXT, created_at TEXT, use_count INTEGER, speaker TEXT)")
    for i, (sent, spk) in enumerate(rows):
        con.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?)",
                    ("n%d" % i, "judgment", sent, "active", "교훈", "2026-11-01T00:00:00Z", 0, spk))
    con.commit()
    con.close()
    return str(path)


OWNER = "공고찾기는 검색이 아니라 가입 때 면허로 자동으로 맞춰 나오는 시스템이다"
AI = "공고찾기 검색 화면을 가입 면허 기준으로 고쳤습니다 확인 부탁드립니다"
NOSPK = "공고찾기 검색 관련 옛 메모 — 화자 칸이 생기기 전에 저장된 문장이다"


def test_preflight_injects_owner_utterances_only(tmp_path):
    led = _ledger_spk(tmp_path / "l.sqlite", [(OWNER, "owner"), (AI, "ai"), (NOSPK, None)])
    pf = R.preflight_context(led, prompt="공고찾기 검색 가입 면허", home=str(tmp_path))
    assert [n["claim"] for n in pf["remember"]] == [OWNER]
    direct = [n["claim"] for n in R.why_search(led, "공고찾기 검색 가입 면허", home=str(tmp_path))["relevant_nodes"]]
    assert {OWNER, AI, NOSPK} <= set(direct)        # 직접 인출은 화자와 무관하게 전부


def test_no_speaker_data_means_no_speaker_filter(tmp_path):
    # 화자 칸이 비어 있기만 한 장부(owner 표시 0건)와 화자 칸이 없는 구 장부 — 주입이 통째로 사라지면 안 된다
    led = _ledger_spk(tmp_path / "a.sqlite", [(AI, None), (NOSPK, None)])
    assert len(R.preflight_context(led, prompt="공고찾기 검색 가입 면허", home=str(tmp_path))["remember"]) == 2
    old = _ledger(tmp_path / "b.sqlite", [(FULL, "교훈")])
    assert [n["claim"] for n in R.preflight_context(old, prompt="검수 끝났습니다 확인", home=str(tmp_path))["remember"]] == [FULL]


def test_threshold_boundary():
    assert R.PREFLIGHT_MIN_CHARS == 16
    assert len("가" * 16) >= R.PREFLIGHT_MIN_CHARS > len("가" * 15)
