# -*- coding: utf-8 -*-
"""Studio Memory Explorer 회귀 — mode=ro 목록/detail/lexical recall · exact-ID · provenance redaction ·
semantic cache/network 0 · read-only 불변. 전 테스트 임시 ledger 격리 · 운영 ~/.binggupack 미접촉.
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.studio import server, read_model      # noqa: E402
from binggupack.pack import recall as RC               # noqa: E402
from openbinggu_owner_accept_ux import open_accept     # noqa: E402

_STATIC_DIR = os.path.join(ROOT, "binggupack", "studio", "static")
_SIDECAR = ("-wal", "-shm", "-journal")


def _id8(nid):
    return hashlib.sha256(nid.encode("utf-8")).hexdigest()[:8]


def _seed(ledger):
    db = open_accept(ledger)
    con = db.con

    def node(nid, nt, sent, st, sub, created, uc):
        con.execute("INSERT INTO nodes(node_id,node_type,sentence,state,semantic_subtype,"
                    "created_at,use_count,candidate,promotion_allowed) VALUES(?,?,?,?,?,?,?,1,0)",
                    (nid, nt, sent, st, sub, created, uc))
    node("node:CONV:aaaa1111", "judgment", "이 입찰은 마진이 낮아 보류하기로 결정했다.", "active", "교훈", "2026-07-03T00:00:00Z", 2)
    node("node:CONV:bbbb2222", "state", "백업은 항상 먼저 해 둔다.", "active", "결정", "2026-07-02T00:00:00Z", 0)
    node("node:CONV:cccc3333", "judgment", "폐기된 옛 판단이다.", "deprecated", "교훈", "2026-07-01T00:00:00Z", 1)
    con.execute("INSERT INTO evidence(evidence_id,sentence,source_pointer_id,source_hash) VALUES(?,?,?,?)",
                ("EVC-CONV-aaaa1111", "이 결정의 근거가 된 문장이다.", "PTR-PRIVATE-SENTINEL", "HASHSENTINELDEAD"))
    con.execute("INSERT INTO edges(edge_id,relation,source,target,state) VALUES(?,?,?,?,'active')",
                ("edge:CONV:e1", "evidence_supports", "EVC-CONV-aaaa1111", "node:CONV:aaaa1111"))
    con.execute("INSERT INTO edges(edge_id,relation,source,target,state) VALUES(?,?,?,?,'active')",
                ("edge:CONV:e2", "ai_accepts", "node:CONV:aaaa1111", "node:CONV:bbbb2222"))
    con.execute("INSERT INTO judgment_reviews(node_id,due_date,status,ts) VALUES(?,?,?,?)",
                ("node:CONV:aaaa1111", "2026-08-01", "pending", "2026-07-03T00:00:00Z"))
    con.execute("INSERT INTO owner_acceptances(node_id,event,reason,ts) VALUES(?,?,?,?)",
                ("node:CONV:aaaa1111", "accept", "사장님이 직접 확정", "2026-07-03T01:00:00Z"))
    db.con.commit()
    db.close()


def _snapshot(home):
    snap = {}
    for root, _, files in os.walk(home):
        for f in files:
            if f.endswith(_SIDECAR):
                continue
            p = os.path.join(root, f)
            try:
                with open(p, "rb") as fh:
                    snap[os.path.relpath(p, home)] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                pass
    return snap


class _Studio:
    def __init__(self, ledger):
        self.httpd, self.session = server.build_server(ledger, port=0)
        self.port = self.httpd.server_address[1]
        self.base = "http://127.0.0.1:%d/s/%s/" % (self.port, self.session)
        self._t = threading.Thread(target=self.httpd.serve_forever)
        self._t.daemon = True
        self._t.start()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, None

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def seeded(tmp_path):
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = os.path.join(home, "ledger.sqlite")
    _seed(ledger)
    s = _Studio(ledger)
    try:
        yield s, home, ledger
    finally:
        s.close()


def _read_static(fn):
    with open(os.path.join(_STATIC_DIR, fn), encoding="utf-8") as f:
        return f.read()


# ════════════════════════ memories list ════════════════════════
def test_memories_api_lists_active_deterministically(seeded):
    s, _, _ = seeded
    st, j = s.get("api/memories?state=active")
    assert st == 200
    assert j["total"] == 2
    assert j["schema_version"] == 1
    # active 우선 + created_at DESC → aaaa1111(07-03) 먼저, bbbb2222(07-02)
    ids = [it["node_id"] for it in j["items"]]
    assert ids == ["node:CONV:aaaa1111", "node:CONV:bbbb2222"]
    # 결정적: 두 번 호출 동일
    st2, j2 = s.get("api/memories?state=active")
    assert [it["node_id"] for it in j2["items"]] == ids
    assert j["items"][0]["display_id"] == _id8("node:CONV:aaaa1111")


def test_memories_api_filters_deprecated(seeded):
    s, _, _ = seeded
    st, j = s.get("api/memories?state=deprecated")
    assert st == 200 and j["total"] == 1
    assert j["items"][0]["state"] == "deprecated"
    st, ja = s.get("api/memories?state=all")
    assert ja["total"] == 3


def test_memories_api_filters_type_and_subtype(seeded):
    s, _, _ = seeded
    st, j = s.get("api/memories?state=active&type=judgment")
    assert j["total"] == 1 and j["items"][0]["node_type"] == "judgment"
    st, j = s.get("api/memories?state=all&subtype=" + urllib.parse.quote("교훈"))
    assert j["total"] == 2
    st, j = s.get("api/memories?state=all&type=state&subtype=" + urllib.parse.quote("결정"))
    assert j["total"] == 1 and j["items"][0]["node_id"] == "node:CONV:bbbb2222"


def test_memories_api_paginates_and_caps_limit(seeded):
    s, _, _ = seeded
    st, j = s.get("api/memories?state=active&limit=1&offset=0")
    assert len(j["items"]) == 1 and j["total"] == 2 and j["limit"] == 1
    st, j2 = s.get("api/memories?state=active&limit=1&offset=1")
    assert len(j2["items"]) == 1
    assert j["items"][0]["node_id"] != j2["items"][0]["node_id"]
    # limit cap / bad params → 400
    assert s.get("api/memories?limit=101")[0] == 400
    assert s.get("api/memories?limit=0")[0] == 400
    assert s.get("api/memories?limit=abc")[0] == 400
    assert s.get("api/memories?offset=-1")[0] == 400
    assert s.get("api/memories?state=bogus")[0] == 400


def test_memories_api_query_is_parameterized(seeded):
    s, _, _ = seeded
    # 정상 부분일치
    st, j = s.get("api/memories?state=all&q=" + urllib.parse.quote("백업"))
    assert j["total"] == 1 and "백업" in j["items"][0]["claim"]
    # SQL injection 시도는 리터럴로 취급 → 매치 0 (파괴 0)
    for inj in ("'; DROP TABLE nodes;--", "%", "' OR '1'='1"):
        st, j = s.get("api/memories?state=all&q=" + urllib.parse.quote(inj))
        assert st == 200 and j["total"] == 0, inj
    # 테이블 파손 없음(정상 조회 여전히 3)
    assert s.get("api/memories?state=all")[1]["total"] == 3


# ════════════════════════ memory detail ════════════════════════
def _detail(s, node_id):
    return s.get("api/memory/" + urllib.parse.quote(node_id, safe=""))


def test_memory_detail_exact_full_id(seeded):
    s, _, _ = seeded
    st, j = _detail(s, "node:CONV:aaaa1111")
    assert st == 200
    assert j["node_id"] == "node:CONV:aaaa1111"
    assert j["evidence_count"] == 1 and j["evidence"][0]["excerpt"]
    assert j["relation_count"] == 1 and j["relations"][0]["peer_display_id"] == _id8("node:CONV:bbbb2222")
    assert j["acceptance"]["event"] == "accept"
    assert j["review_status"] == "pending"
    assert j["explain_summary"]


def test_memory_detail_does_not_accept_id8(seeded):
    s, _, _ = seeded
    # display_id(id8) 로 조회 → exact match 아님 → 404 (fuzzy 금지)
    st, j = _detail(s, _id8("node:CONV:aaaa1111"))
    assert st == 404
    # suffix 도 거부
    assert _detail(s, "aaaa1111")[0] == 404
    assert _detail(s, "CONV:aaaa1111")[0] == 404


def test_memory_detail_deprecated_supported(seeded):
    s, _, _ = seeded
    st, j = _detail(s, "node:CONV:cccc3333")
    assert st == 200 and j["state"] == "deprecated"
    assert j["node_id"] == "node:CONV:cccc3333"


def test_memory_detail_missing_404(seeded):
    s, _, _ = seeded
    assert _detail(s, "node:CONV:doesnotexist")[0] == 404
    # NUL / oversized → 400
    assert s.get("api/memory/" + urllib.parse.quote("node:\x00evil", safe=""))[0] == 400
    assert s.get("api/memory/" + urllib.parse.quote("x" * 400, safe=""))[0] == 400


def test_memory_detail_redacts_private_provenance(seeded):
    s, _, _ = seeded
    st, j = _detail(s, "node:CONV:aaaa1111")
    blob = json.dumps(j, ensure_ascii=False)
    assert "PTR-PRIVATE-SENTINEL" not in blob      # source_pointer_id
    assert "HASHSENTINELDEAD" not in blob          # source_hash
    assert "EVC-CONV-aaaa1111" not in blob         # raw evidence id (display_id 로 대체)
    # evidence 는 안전 발췌만
    assert j["evidence"][0]["display_id"] == _id8("EVC-CONV-aaaa1111")


# ════════════════════════ recall (lexical-only) ════════════════════════
def test_recall_api_lexical_results_match_existing_logic(seeded):
    s, _, ledger = seeded
    st, j = s.get("api/recall?q=" + urllib.parse.quote("입찰 보류"))
    assert st == 200 and j["mode"] == "lexical"
    # 정본 why_search(lexical scorer) 와 node 집합/순서 일치
    ref = RC.why_search(ledger, "입찰 보류", limit=10, scorer=read_model._lexical_only_scorer)
    assert [i["node_id"] for i in j["items"]] == [n["node_id"] for n in ref["relevant_nodes"]]
    assert j["count"] >= 1


def test_recall_api_does_not_create_embed_cache(tmp_path, monkeypatch):
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = os.path.join(home, "ledger.sqlite")
    _seed(ledger)
    monkeypatch.setenv("BINGGU_HOME", home)
    cache = os.path.join(home, "recall_embed_cache.sqlite")
    assert not os.path.exists(cache)
    read_model.collect_recall_snapshot(ledger, "입찰", limit=10)
    assert not os.path.exists(cache), "lexical recall 이 embed cache 를 생성했다"


def test_recall_api_does_not_call_semantic_or_network(tmp_path, monkeypatch):
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = os.path.join(home, "ledger.sqlite")
    _seed(ledger)

    def boom_scorer(*a, **k):
        raise AssertionError("recall 이 _semantic_scorer 를 초기화했다")

    def boom_cache(*a, **k):
        raise AssertionError("recall 이 recall_embed_cache 를 열었다")

    monkeypatch.setattr(RC, "_semantic_scorer", boom_scorer)
    monkeypatch.setattr(RC, "_open_embed_cache", boom_cache)
    # LEXICAL_ONLY_SCORER 주입이라 두 함수 모두 미호출 → 예외 없이 정상 반환
    snap = read_model.collect_recall_snapshot(ledger, "백업 결정", limit=10)
    assert snap["mode"] == "lexical"


def test_recall_api_does_not_record_use_or_trace(seeded):
    s, home, ledger = seeded
    before = _snapshot(home)
    for _ in range(3):
        s.get("api/recall?q=" + urllib.parse.quote("입찰"))
        _detail(s, "node:CONV:aaaa1111")
        s.get("api/memories?state=all")
    assert _snapshot(home) == before, "조회가 ledger/파일을 변경했다(use_count/recall_traces 등)"


def test_recall_empty_or_oversized_query_blocked(seeded):
    s, _, _ = seeded
    assert s.get("api/recall?q=")[0] == 400
    assert s.get("api/recall")[0] == 400
    assert s.get("api/recall?q=" + urllib.parse.quote("x" * 501))[0] == 400
    assert s.get("api/recall?q=" + urllib.parse.quote("입찰") + "&limit=99")[0] == 400
    assert s.get("api/recall?q=" + urllib.parse.quote("입찰") + "&limit=0")[0] == 400


# ════════════════════════ UI 정적 계약 ════════════════════════
def test_studio_memory_ui_uses_text_content():
    js = _read_static("app.js")
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert banned not in js, banned
    assert "textContent" in js
    # 동적 검색 파라미터는 URLSearchParams / encodeURIComponent 로 인코딩
    assert "URLSearchParams" in js and "encodeURIComponent" in js


def test_studio_memory_ui_has_no_mutation_action():
    js = _read_static("app.js").lower()
    html = _read_static("index.html").lower()
    # 삭제/폐기/교체/승인/편집 버튼·동사가 UI 에 없다(mutation handoff 는 v1.20-E)
    for banned in ("forget", "deprecate", "replace", "delete", "accept("):
        assert banned not in js, banned
    for banned in ('data-view="forget"', "id=\"deprecate", "id=\"delete"):
        assert banned not in html, banned
    # 복사 명령은 read-only(explain/recall)만
    assert "binggu explain" in _read_static("app.js")
    assert "binggu recall" in _read_static("app.js")


def test_memory_explorer_refresh_is_read_only(seeded):
    s, home, ledger = seeded
    before = _snapshot(home)
    led_mt = os.path.getmtime(ledger)
    for _ in range(5):
        for p in ("api/memories?state=all", "api/memories?state=active&limit=1",
                  "api/recall?q=" + urllib.parse.quote("보류")):
            s.get(p)
        _detail(s, "node:CONV:aaaa1111")
    assert _snapshot(home) == before
    assert os.path.getmtime(ledger) == led_mt


def test_memory_explorer_no_ledger_creates_nothing(tmp_path):
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = os.path.join(home, "ledger.sqlite")
    s = _Studio(ledger)
    try:
        st, j = s.get("api/memories?state=all")
        assert st == 200 and j["total"] == 0 and j["items"] == []
        assert _detail(s, "node:CONV:whatever")[0] == 404
        st, j = s.get("api/recall?q=" + urllib.parse.quote("아무거나"))
        assert st == 200 and j["count"] == 0
    finally:
        s.close()
    assert not os.path.exists(ledger)
    assert sorted(os.listdir(home)) == []


# ════════════════════════ packaging / 설치본 ════════════════════════
def test_wheel_memory_explorer_assets_present():
    from importlib.resources import files
    base = files("binggupack.studio") / "static"
    for fn in ("index.html", "app.js", "style.css"):
        txt = (base / fn).read_bytes().decode("utf-8")
        assert txt
    # read_model 모듈도 패키지에 포함
    import binggupack.studio.read_model as rm
    assert rm.SCHEMA_VERSION == 1
    # Memories nav 가 index.html 에 존재
    assert 'data-view="memories"' in (base / "index.html").read_bytes().decode("utf-8")


def test_external_cwd_memory_api_smoke(tmp_path):
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = os.path.join(home, "ledger.sqlite")
    _seed(ledger)
    ext = str(tmp_path / "external_cwd")
    os.makedirs(ext)
    e = dict(os.environ)
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    e["BINGGU_HOME"] = home
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "binggu.py"), "--ledger", ledger,
         "studio", "--no-open", "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", env=e, cwd=ext)
    try:
        url = None
        deadline = time.time() + 20
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            if line.startswith("URL:"):
                url = line.split("URL:", 1)[1].strip()
                break
        assert url and url.startswith("http://127.0.0.1:"), repr(url)
        with urllib.request.urlopen(url + "api/memories?state=all", timeout=5) as r:
            assert r.status == 200
            assert json.loads(r.read())["total"] == 3
        with urllib.request.urlopen(url + "api/memory/" +
                                    urllib.parse.quote("node:CONV:aaaa1111", safe=""), timeout=5) as r:
            assert r.status == 200
            assert json.loads(r.read())["node_id"] == "node:CONV:aaaa1111"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    assert proc.poll() is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
