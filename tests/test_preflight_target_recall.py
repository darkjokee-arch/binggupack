"""대상 표지 회상 — 표지 붙은 기억은 대상 이름이 질문에 나올 때만 자동 주입된다(2026-11-14)."""
import sqlite3

from binggupack.pack import recall as R

CALC = "견적계산기 상의 빈칸은 있을 수 있다 지붕이 없는 곳은 당연히 비어 있는 거다"
FIND = "공고찾기는 검색이 아니라 가입 때 면허로 자동으로 맞춰 나오는 시스템이다"
RULE = "키워드로 분류하지 말고 공고문 전문에서 공사 범위를 보고 판단하면 된다"
CHAT = "그럼 그렇게 하고 다시 확인해 보고 결과를 나한테 알려 주면 된다"
NEW = "견적 화면 관련해서 새로 저장됐고 아직 아무도 읽지 않은 문장이다"


def _ledger(path):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE nodes (node_id TEXT, node_type TEXT, sentence TEXT, state TEXT,"
                " semantic_subtype TEXT, created_at TEXT, use_count INTEGER, speaker TEXT)")
    for nid, sent in (("calc", CALC), ("find", FIND), ("rule", RULE), ("chat", CHAT), ("new", NEW)):
        con.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?)",
                    (nid, "judgment", sent, "active", "교훈", "2026-11-01T00:00:00Z", 0, "owner"))
    con.commit()
    con.close()
    return str(path)


LABELS = [
    {"id": "calc", "lane": "target", "targets": ["견적계산기"], "aliases": {"견적계산기": ["견적 계산기"]}},
    {"id": "find", "lane": "target", "targets": ["공고찾기"], "aliases": {"공고찾기": ["맞춤공고"]}},
    {"id": "rule", "lane": "general", "targets": [], "aliases": {}},
    {"id": "chat", "lane": "none", "targets": [], "aliases": {}},
]


def _ids(led, home, prompt):
    return [n["node_id"] for n in R.preflight_context(led, prompt=prompt, home=home)["remember"]]


def test_no_label_file_keeps_previous_behaviour(tmp_path):
    led = _ledger(tmp_path / "ledger.sqlite")
    assert "find" in _ids(led, str(tmp_path), "공고찾기 검색 가입 면허")
    assert R._load_targets(str(tmp_path)) == {}


def test_target_memory_comes_when_its_target_is_named(tmp_path):
    led, home = _ledger(tmp_path / "ledger.sqlite"), str(tmp_path)
    assert R.save_targets(LABELS, home) == 4
    got = _ids(led, home, "견적계산기안에 빈칸은 지붕이 없는 곳")        # 조사 붙은 꼴
    assert "calc" in got and "find" not in got
    assert "find" in _ids(led, home, "맞춤공고 검색이 아니라 면허로 자동으로")   # 별칭
    assert "calc" in _ids(led, home, "견적 계산기 빈칸은 지붕이")               # 띄어쓰기


def test_target_name_alone_does_not_pull_memory(tmp_path):
    # 대상 이름만 겹치고 내용은 다른 일 — 새로 뽑지 않는다(이름만으로 뽑으면 쓸모 5.8% 였다)
    led, home = _ledger(tmp_path / "ledger.sqlite"), str(tmp_path)
    R.save_targets(LABELS, home)
    assert "calc" not in _ids(led, home, "견적계산기 버튼 색을 파란색으로 바꿔줘")


def test_labelled_memory_stays_quiet_without_its_target(tmp_path):
    led, home = _ledger(tmp_path / "ledger.sqlite"), str(tmp_path)
    R.save_targets(LABELS, home)
    got = _ids(led, home, "검색 가입 면허 자동으로 분류 판단 확인")       # 문장 낱말은 겹치지만 대상 이름이 없다
    assert not {"calc", "find", "rule", "chat"} & set(got)
    direct = {n["node_id"] for n in R.why_search(led, "검색 가입 면허 자동으로", home=home)["relevant_nodes"]}
    assert "find" in direct                                               # 직접 인출은 표지와 무관


def test_unlabelled_memory_uses_similarity_as_before(tmp_path):
    led, home = _ledger(tmp_path / "ledger.sqlite"), str(tmp_path)
    R.save_targets(LABELS, home)
    assert "new" in _ids(led, home, "견적 화면 관련해서 새로 저장됐고")


def test_save_merges_and_ignores_bad_rows(tmp_path):
    home = str(tmp_path)
    R.save_targets(LABELS[:2], home)
    n = R.save_targets([{"id": "x", "lane": "wrong"}, {"lane": "target"},
                        {"id": "calc", "lane": "both", "targets": ["견적계산기", "가"], "aliases": {}}], home)
    t = R._load_targets(home)
    assert n == 2 and t["calc"]["lane"] == "both" and t["calc"]["names"] == ["견적계산기"]
    (tmp_path / R.TARGET_STORE_NAME).write_text("{깨진", encoding="utf-8")
    assert R._load_targets(home) == {}
