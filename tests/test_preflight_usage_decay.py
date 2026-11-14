"""사용률 퇴화 — 도장이 쌓였는데 거의 안 쓰인 기억은 자동 주입에서 쉰다. 직접 인출은 그대로(2026-11-14)."""
import sqlite3

from binggupack.pack import recall as R
from binggupack.pack import recall_trace as RT

A = "공고찾기는 검색이 아니라 가입 때 면허로 자동으로 맞춰 나오는 시스템이다"
B = "공고찾기 검색 화면은 가입 면허 기준으로 한 줄씩 보여 주기로 정했다"
Q = "공고찾기 검색 가입 면허"


def _ledger(path):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE nodes (node_id TEXT, node_type TEXT, sentence TEXT, state TEXT,"
                " semantic_subtype TEXT, created_at TEXT, use_count INTEGER, speaker TEXT)")
    for nid, sent in (("nA", A), ("nB", B)):
        con.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?)",
                    (nid, "judgment", sent, "active", "교훈", "2026-11-01T00:00:00Z", 0, "owner"))
    con.commit()
    con.close()
    return str(path)


def _stamp(home, led, node_id, verdicts, reason=None):
    for i, v in enumerate(verdicts):
        ts = "2026-11-%02dT00:00:%02dZ" % (2 + i, len(node_id))
        t = RT.record_trace("q%d %s" % (i, node_id), "preflight", [{"node_id": node_id}], ts, home=home)
        r = RT.record_outcome(t["trace_id"], node_id, v, {"actor": "human"}, ts,
                              reason_code=reason if v == "ignored" else None, home=home, ledger_path=led)
        assert r["recorded"], r


def _remembered(led, home):
    return {n["node_id"] for n in R.preflight_context(led, prompt=Q, home=home)["remember"]}


def test_rarely_used_memory_rests_from_auto_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("BINGGU_RECALL_TRACE", "1")
    home, led = str(tmp_path), _ledger(tmp_path / "ledger.sqlite")
    assert _remembered(led, home) == {"nA", "nB"}
    _stamp(home, led, "nA", ["ignored"] * 5, "not_relevant")
    assert _remembered(led, home) == {"nB"}
    direct = {n["node_id"] for n in R.why_search(led, Q, home=home)["relevant_nodes"]}
    assert direct == {"nA", "nB"}                      # 직접 인출은 퇴화와 무관


def test_below_sample_floor_or_used_enough_stays(tmp_path, monkeypatch):
    monkeypatch.setenv("BINGGU_RECALL_TRACE", "1")
    home, led = str(tmp_path), _ledger(tmp_path / "ledger.sqlite")
    _stamp(home, led, "nA", ["ignored"] * 4, "not_relevant")            # 표본 4 — 아직 판정 안 함
    _stamp(home, led, "nB", ["used"] + ["ignored"] * 4, "not_relevant")  # 1/5 = 20% — 기준(10%) 위
    assert _remembered(led, home) == {"nA", "nB"}


def test_not_applied_is_not_counted_against_the_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("BINGGU_RECALL_TRACE", "1")
    home, led = str(tmp_path), _ledger(tmp_path / "ledger.sqlite")
    _stamp(home, led, "nA", ["ignored"] * 8, RT.NOT_APPLIED_CODE)        # 회상은 맞았고 AI 가 안 쓴 것
    assert _remembered(led, home) == {"nA", "nB"}


def test_use_after_resting_brings_it_back(tmp_path, monkeypatch):
    monkeypatch.setenv("BINGGU_RECALL_TRACE", "1")
    home, led = str(tmp_path), _ledger(tmp_path / "ledger.sqlite")
    _stamp(home, led, "nA", ["ignored"] * 5, "not_relevant")
    assert "nA" not in _remembered(led, home)
    t = RT.record_trace("direct", "why_search", [{"node_id": "nA"}], "2026-11-20T00:00:00Z", home=home)
    RT.record_outcome(t["trace_id"], "nA", "used", {"actor": "human"}, "2026-11-20T00:00:00Z", home=home, ledger_path=led)
    assert "nA" in _remembered(led, home)              # 1/6 = 17% — 다시 실린다


def test_no_stamp_store_means_no_decay(tmp_path):
    led = _ledger(tmp_path / "ledger.sqlite")
    assert R._decayed_node_ids(str(tmp_path)) == frozenset()
    assert _remembered(led, str(tmp_path)) == {"nA", "nB"}
