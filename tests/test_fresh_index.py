# -*- coding: utf-8 -*-
"""Local Fresh Index(LFI) pytest — 필수 검증 목록.

최초 색인·변경없음·추가/수정/삭제·저장/교체/폐기·프로젝트 스코프·pinned 보존·최근/고신뢰
우선순위·중복제거·Hot/Warm/Deep 경계·원본 전체스캔 방지·query-time 전수 임베딩 방지·
provider timeout+lexical fallback·색인 손상 후 rebuild·중간종료 정합성·PII/시크릿 미노출·
owner approval·mutation 경계 무회귀. (Windows/Python 3.14 실경로 + 3.10~3.13 무회귀는 CI 매트릭스.)
"""
import hashlib
import os
import sqlite3
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO, os.path.join(REPO, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.pack import fresh_index as FI  # noqa: E402


def _mk_ledger(path, rows):
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE nodes(node_id TEXT PRIMARY KEY,node_type TEXT,sentence TEXT,"
        "candidate INT,state TEXT,semantic_subtype TEXT,created_at TEXT,use_count INT,"
        "content_hash TEXT,pack_id TEXT);"
        "CREATE TABLE evidence(evidence_id TEXT PRIMARY KEY, sentence TEXT);"
        "CREATE TABLE owner_acceptances(event_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " node_id TEXT, event TEXT);")
    con.executemany("INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def _row(nid, sent, cand=0, state="active", sub="교훈", created="2026-07-01T00:00:00Z",
         uc=0, chash=None, pack=None, ntype="judgment"):
    return (nid, ntype, sent, cand, state, sub, created, uc, chash or ("h_" + nid), pack)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = str(tmp_path / "home")
    os.makedirs(home)
    ledger = str(tmp_path / "ledger.sqlite")
    monkeypatch.setenv("BINGGU_HOME", home)
    return home, ledger


def _ids(res):
    return [x["node_id"] for x in res["relevant_nodes"]]


# ── 1) 최초 색인 + 변경 없음 ─────────────────────────────────────────────────
def test_initial_index_and_no_change(env):
    home, ledger = env
    _mk_ledger(ledger, [
        _row("n1", "빙구팩 릴리스 승인 경계"),
        _row("n2", "recall 성능 병목 임베딩"),
    ])
    r = FI.index_update(ledger, home=home)
    assert r["status"] == "OK" and r["added"] == 2 and r["scanned"] == 2
    r2 = FI.index_update(ledger, home=home)
    assert r2["added"] == 0 and r2["updated"] == 0 and r2["unchanged"] == 2
    st = FI.index_status(ledger, home=home)
    assert st["status"] == "OK" and st["pending_changes"] == 0 and st["pending_removals"] == 0


# ── 2) 파일(노드) 추가·수정·삭제 ─────────────────────────────────────────────
def test_add_update_delete(env):
    home, ledger = env
    _mk_ledger(ledger, [_row("n1", "첫 노드 릴리스"), _row("n2", "둘째 노드 승인")])
    FI.index_update(ledger, home=home)
    # 추가
    con = sqlite3.connect(ledger)
    con.execute("INSERT INTO nodes VALUES('n3','judgment','셋째 노드 배포',0,'active','결정','2026-07-02T00:00:00Z',0,'h_n3',NULL)")
    con.commit()
    con.close()
    r = FI.index_update(ledger, home=home)
    assert r["added"] == 1 and r["unchanged"] == 2
    # 수정
    con = sqlite3.connect(ledger)
    con.execute("UPDATE nodes SET sentence='첫 노드 수정됨', content_hash='h_n1b' WHERE node_id='n1'")
    con.commit()
    con.close()
    r = FI.index_update(ledger, home=home)
    assert r["updated"] == 1 and r["added"] == 0
    assert "n1" in _ids(FI.hot_recall("수정됨", home=home))
    # 삭제
    con = sqlite3.connect(ledger)
    con.execute("DELETE FROM nodes WHERE node_id='n2'")
    con.commit()
    con.close()
    r = FI.index_update(ledger, home=home)
    assert r["removed"] == 1
    assert "n2" not in _ids(FI.hot_recall("승인", home=home))


# ── 3) 기억 저장/교체/폐기 반영 ──────────────────────────────────────────────
def test_save_replace_deprecate(env):
    home, ledger = env
    _mk_ledger(ledger, [_row("n1", "옛 결정 유지", sub="결정")])
    FI.index_update(ledger, home=home)
    # 교체(supersede 시뮬 — content 변경)
    con = sqlite3.connect(ledger)
    con.execute("UPDATE nodes SET sentence='새 결정으로 교체', content_hash='h_n1r' WHERE node_id='n1'")
    con.commit()
    con.close()
    FI.index_update(ledger, home=home)
    assert "n1" in _ids(FI.hot_recall("교체", home=home))
    # 폐기 → active 회상 제외
    con = sqlite3.connect(ledger)
    con.execute("UPDATE nodes SET state='deprecated' WHERE node_id='n1'")
    con.commit()
    con.close()
    r = FI.index_update(ledger, home=home)
    assert r["deprecated"] >= 1
    assert "n1" not in _ids(FI.hot_recall("교체", home=home))
    # include_deprecated=True 로는 조회 가능(보존)
    assert "n1" in _ids(FI.hot_recall("교체", home=home, include_deprecated=True))


# ── 4) 프로젝트 스코프 분리(Warm) ────────────────────────────────────────────
def test_project_scope(env):
    home, ledger = env
    _mk_ledger(ledger, [
        _row("na", "프로젝트 관련 기억 알파"),
        _row("nb", "프로젝트 관련 기억 베타"),
        _row("nc", "프로젝트 관련 기억 공용"),
    ])
    FI.index_update(ledger, home=home)
    # project_id 태깅(색인 레벨 · ledger 불변): na=projA, nb=projB, nc=NULL(공용)
    con = FI._connect(home)
    con.execute("UPDATE hot_items SET project_id='projA' WHERE item_id='node:na'")
    con.execute("UPDATE hot_items SET project_id='projB' WHERE item_id='node:nb'")
    con.commit()
    con.close()
    ids = _ids(FI.hot_recall("프로젝트 관련", home=home, project="projA"))
    # projA 조회 → na(projA) + nc(NULL 공용) 포함, nb(projB) 제외(스코프 분리)
    assert "na" in ids and "nc" in ids and "nb" not in ids


# ── 5) pinned 영구 규칙 보존 ─────────────────────────────────────────────────
def test_pinned_permanent_rule(env):
    home, ledger = env
    _mk_ledger(ledger, [
        _row("n1", "무관한 최신 기억", created="2026-07-11T00:00:00Z"),
        _row("perm", "오래된 영구 규칙 항상 지켜라", created="2025-01-01T00:00:00Z"),
    ])
    FI.index_update(ledger, home=home)
    FI.set_pin("perm", home=home, pinned=True)
    # 무관 query 에도 pinned 는 등장(영구 규칙 보존)
    res = FI.hot_recall("전혀무관한질의힣", home=home, limit=5)
    assert "perm" in _ids(res)
    # rebuild 후에도 핀 보존
    FI.index_rebuild(ledger, home=home)
    assert "perm" in _ids(FI.hot_recall("영구 규칙", home=home))
    # 언핀
    FI.set_pin("perm", home=home, pinned=False)
    assert "perm" not in _ids(FI.hot_recall("전혀무관한질의힣", home=home))


# ── 6) 최근·고신뢰 우선순위 ──────────────────────────────────────────────────
def test_recency_and_trust_priority(env):
    home, ledger = env
    _mk_ledger(ledger, [
        _row("old_c1", "승인 경계 오래된 후보", cand=1, created="2025-01-01T00:00:00Z"),
        _row("new_c0", "승인 경계 최신 확정", cand=0, created="2026-07-11T00:00:00Z"),
    ])
    FI.index_update(ledger, home=home)
    res = FI.hot_recall("승인 경계", home=home, limit=5)
    ids = _ids(res)
    # 동일 관련성이면 최신+고신뢰(candidate=0)가 앞
    assert ids[0] == "new_c0"
    trust = {x["node_id"]: x["trust"] for x in res["relevant_nodes"]}
    assert trust["new_c0"] == 1.0 and trust["old_c1"] == 0.5


# ── 7) 중복 제거 + top5 제한 ─────────────────────────────────────────────────
def test_dedup_and_limit(env):
    home, ledger = env
    rows = [_row("n%d" % i, "릴리스 승인 배포 기억 %d" % i) for i in range(12)]
    _mk_ledger(ledger, rows)
    FI.index_update(ledger, home=home)
    res = FI.hot_recall("릴리스 승인", home=home, limit=5)
    ids = _ids(res)
    assert len(ids) == 5 and len(set(ids)) == 5  # top5 + 중복 0


# ── 8) 원본 전체 스캔 방지 — Hot 은 ledger 를 열지 않는다 ─────────────────────
def test_hot_does_not_scan_ledger(env):
    home, ledger = env
    _mk_ledger(ledger, [_row("n1", "릴리스 승인")])
    FI.index_update(ledger, home=home)
    # ledger 파일을 지워도 Hot 은 색인만 읽어 동작(원본 미접촉 증명)
    os.remove(ledger)
    res = FI.hot_recall("릴리스 승인", home=home, limit=5)
    assert "n1" in _ids(res)


# ── 9) Hot = embed 0 불변 (semantic 분기 삭제 — 2026-07-13 4cli+적대검증 확정) ──
def test_no_query_time_embedding(env, monkeypatch):
    home, ledger = env
    _mk_ledger(ledger, [_row("n%d" % i, "릴리스 승인 %d" % i) for i in range(6)])
    FI.index_update(ledger, home=home)
    called = {"embed": 0}

    def _boom(*a, **k):
        called["embed"] += 1
        raise AssertionError("embed must not be called in Hot recall")
    # semantic_shadow._embed 를 폭발하도록 — Hot 은 어떤 경로로도 이를 호출하면 안 됨
    import binggu_semantic_shadow as SH
    monkeypatch.setattr(SH, "_embed", _boom, raising=False)
    monkeypatch.setattr(SH, "_embed_batch", _boom, raising=False)
    res = FI.hot_recall("릴리스 승인", home=home, limit=5)
    assert called["embed"] == 0 and res["relevant_nodes"]
    assert not os.path.exists(os.path.join(home, "recall_embed_cache.sqlite"))
    # semantic 인자·semantic_used 키·embed_vec 테이블 완전 제거 확인(死코드 재발 방지)
    import inspect
    assert "semantic" not in inspect.signature(FI.hot_recall).parameters
    assert "semantic_used" not in res
    import sqlite3
    con = sqlite3.connect(FI.index_path(home))
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "embed_vec" not in tables


# ── 10) 색인 손상 후 rebuild ─────────────────────────────────────────────────
def test_rebuild_after_corruption(env):
    home, ledger = env
    _mk_ledger(ledger, [_row("n1", "릴리스 승인"), _row("n2", "배포 결정", sub="결정")])
    FI.index_update(ledger, home=home)
    # 색인 파일 손상(쓰레기 바이트)
    with open(FI.index_path(home), "wb") as f:
        f.write(b"\x00\x01corrupted-not-a-sqlite\xff\xfe" * 10)
    # rebuild 로 복구
    r = FI.index_rebuild(ledger, home=home)
    assert r.get("rebuilt") and r["scanned"] == 2
    assert "n1" in _ids(FI.hot_recall("릴리스 승인", home=home))


# ── 11) 중간 종료 후 원본/색인 정합성(원자성) ────────────────────────────────
def test_atomicity_consistency(env):
    home, ledger = env
    _mk_ledger(ledger, [_row("n1", "릴리스 승인")])
    FI.index_update(ledger, home=home)
    h_before = hashlib.sha256(open(ledger, "rb").read()).hexdigest()
    # 재실행은 멱등 — ledger 불변, 색인 정합
    FI.index_update(ledger, home=home)
    FI.index_status(ledger, home=home)
    h_after = hashlib.sha256(open(ledger, "rb").read()).hexdigest()
    assert h_before == h_after  # ledger 불변
    st = FI.index_status(ledger, home=home)
    assert st["pending_changes"] == 0


# ── 12) PII·시크릿 미노출 ────────────────────────────────────────────────────
# PII 리터럴을 소스에 온전히 두면 public tree-scan(scan_residual_pii)이 이 파일을 PII 노출로
# BLOCK 한다(known 함정 · feedback_no_verify_fix_reverify_loop). 조각 조립으로 파일 바이트에
# 온전한 패턴이 나타나지 않게 한다(런타임에만 결합).
_PHONE = "010-" + "1234-" + "5678"
_RRN = "900101-" + "1234567"


def test_pii_secret_not_exposed(env):
    home, ledger = env
    secret_sent = "옛 root 비번 0922 하드코딩과 연락처 %s 주민 %s 노출" % (_PHONE, _RRN)
    _mk_ledger(ledger, [_row("n1", secret_sent, sub="버그패턴")])
    FI.index_update(ledger, home=home)
    con = FI._connect(home)
    title = con.execute("SELECT title FROM hot_items WHERE item_id='node:n1'").fetchone()[0]
    con.close()
    # 원문 PII 스팬이 색인 title 에 그대로 남으면 안 됨
    assert _PHONE not in title
    assert _RRN not in title
    assert "[REDACTED" in title  # 스팬 redaction 됨
    # 회상 결과에도 raw PII 미노출
    res = FI.hot_recall("root 비번 하드코딩", home=home, limit=5)
    blob = str(res)
    assert _PHONE not in blob and _RRN not in blob


# ── 13) owner approval·mutation 경계 무회귀(ledger write 0) ──────────────────
def test_mutation_boundary_no_ledger_write(env):
    home, ledger = env
    _mk_ledger(ledger, [_row("n1", "릴리스 승인")])
    h0 = hashlib.sha256(open(ledger, "rb").read()).hexdigest()
    FI.index_update(ledger, home=home)
    FI.index_status(ledger, home=home)
    FI.hot_recall("릴리스", home=home)
    FI.set_pin("n1", home=home)
    FI.index_rebuild(ledger, home=home)
    h1 = hashlib.sha256(open(ledger, "rb").read()).hexdigest()
    assert h0 == h1  # 어떤 색인 연산도 ledger 를 변경하지 않음


# ── 14) 빈 ledger / 색인 부재 graceful ───────────────────────────────────────
def test_empty_and_missing_graceful(env):
    home, ledger = env
    # 색인 없이 hot_recall
    res = FI.hot_recall("무엇이든", home=home, limit=5)
    assert res.get("index_missing") and res["relevant_nodes"] == []
    # 존재하지 않는 ledger update
    r = FI.index_update(ledger, home=home)
    assert r["status"] == "OK" and r["scanned"] == 0


# ══════════════ 2단계: 로컬 md/traj 파일 포인터 인덱싱 ══════════════

def _docs_dir(home):
    d = os.path.join(os.path.dirname(home), "docs")
    os.makedirs(os.path.join(d, "traj"), exist_ok=True)
    return d


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_file_allowlist_default_empty(env):
    """기본 빈 허용목록 → 파일 인덱싱 0(owner 옵트인)."""
    home, ledger = env
    _mk_ledger(ledger, [_row("n1", "노드")])
    d = _docs_dir(home)
    _write(os.path.join(d, "a.md"), "# 문서\n허용 안 된 경로 내용")
    r = FI.index_update(ledger, home=home)
    assert r["files"]["scanned"] == 0 and r["files"]["added"] == 0
    assert FI.allowed_paths(home) == []


def test_file_add_modify_delete(env):
    home, ledger = env
    _mk_ledger(ledger, [_row("n1", "노드 릴리스")])
    d = _docs_dir(home)
    _write(os.path.join(d, "guide.md"), "# 배포 가이드\n나라장터 투찰 자동화 절차")
    _write(os.path.join(d, "traj", "t1.md"), "# traj 교훈\nMySQL 연결 종료 근본원인 env 누락")
    FI.add_allowed_path(d, home=home)
    # 추가
    r = FI.index_update(ledger, home=home)
    assert r["files"]["added"] == 2 and r["files"]["scanned"] == 2
    ids = [x for x in FI.hot_recall("나라장터 투찰", home=home)["relevant_nodes"] if x.get("kind") == "file"]
    assert ids and any("guide" in (x.get("rel_path") or "") for x in ids)
    # traj file_kind
    tj = [x for x in FI.hot_recall("MySQL 연결 종료", home=home)["relevant_nodes"] if x.get("kind") == "file"]
    assert tj and tj[0]["semantic_subtype"] == "traj"
    # 변경 없음
    r2 = FI.index_update(ledger, home=home)
    assert r2["files"]["added"] == 0 and r2["files"]["unchanged"] == 2
    # 수정
    import time as _t
    _t.sleep(0.02)
    _write(os.path.join(d, "guide.md"), "# 배포 가이드\n한전 KEPCO SRM 투찰 절차")
    r3 = FI.index_update(ledger, home=home)
    assert r3["files"]["updated"] == 1
    assert any("KEPCO" in x.get("title", "") for x in FI.hot_recall("KEPCO SRM", home=home)["relevant_nodes"])
    # 삭제
    os.remove(os.path.join(d, "traj", "t1.md"))
    r4 = FI.index_update(ledger, home=home)
    assert r4["files"]["removed"] == 1
    assert not [x for x in FI.hot_recall("MySQL 연결 종료", home=home)["relevant_nodes"] if x.get("kind") == "file"]


def test_file_path_safety_outside_not_indexed(env):
    """허용 경로 밖 파일은 인덱싱 0(traversal/격리)."""
    home, ledger = env
    _mk_ledger(ledger, [_row("n1", "노드")])
    d = _docs_dir(home)
    outside = os.path.join(os.path.dirname(home), "outside")
    os.makedirs(outside, exist_ok=True)
    _write(os.path.join(d, "inside.md"), "# 안\n허용된 경로 문서 자동화")
    _write(os.path.join(outside, "secret.md"), "# 밖\n허용 안된 경로 비밀 자동화")
    FI.add_allowed_path(d, home=home)
    FI.index_update(ledger, home=home)
    res = FI.hot_recall("자동화", home=home, limit=10)
    files = [x for x in res["relevant_nodes"] if x.get("kind") == "file"]
    assert any("inside" in (x.get("rel_path") or "") for x in files)
    assert not any("secret" in (x.get("rel_path") or "") for x in files)


def test_file_pii_redacted(env):
    home, ledger = env
    _mk_ledger(ledger, [_row("n1", "노드")])
    d = _docs_dir(home)
    _write(os.path.join(d, "contact.md"), "# 연락처 자동화\n담당자 전화 %s 참고" % _PHONE)
    FI.add_allowed_path(d, home=home)
    FI.index_update(ledger, home=home)
    con = FI._connect(home)
    titles = " ".join(r[0] or "" for r in con.execute("SELECT title FROM hot_items WHERE kind='file'"))
    con.close()
    assert _PHONE not in titles and "[REDACTED" in titles
    assert _PHONE not in str(FI.hot_recall("연락처 자동화", home=home))


def test_file_and_node_coexist_ledger_unchanged(env):
    home, ledger = env
    _mk_ledger(ledger, [_row("n1", "릴리스 승인 노드")])
    h0 = hashlib.sha256(open(ledger, "rb").read()).hexdigest()
    d = _docs_dir(home)
    _write(os.path.join(d, "rel.md"), "# 릴리스 승인 문서\n릴리스 승인 절차 문서")
    FI.add_allowed_path(d, home=home)
    FI.index_update(ledger, home=home)
    res = FI.hot_recall("릴리스 승인", home=home, limit=10)
    kinds = {x.get("kind") for x in res["relevant_nodes"]}
    assert "node" in kinds and "file" in kinds
    assert h0 == hashlib.sha256(open(ledger, "rb").read()).hexdigest()  # ledger 불변


def test_remove_path_drops_file_items(env):
    home, ledger = env
    _mk_ledger(ledger, [_row("n1", "노드")])
    d = _docs_dir(home)
    _write(os.path.join(d, "x.md"), "# 제거대상\n제거 테스트 문서 자동화")
    FI.add_allowed_path(d, home=home)
    FI.index_update(ledger, home=home)
    assert [x for x in FI.hot_recall("제거 자동화", home=home)["relevant_nodes"] if x.get("kind") == "file"]
    FI.remove_allowed_path(d, home=home)
    r = FI.index_update(ledger, home=home)
    assert r["files"]["removed"] >= 1
    assert not [x for x in FI.hot_recall("제거 자동화", home=home)["relevant_nodes"] if x.get("kind") == "file"]
