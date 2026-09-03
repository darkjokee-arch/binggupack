# -*- coding: utf-8 -*-
"""daily console 회귀 — `binggu` 홈 + 통합 `binggu inbox` 의 read-only·부작용 없음·호환성.

전 테스트 temp BINGGU_HOME/--ledger 격리 · 운영 ~/.binggupack 미접촉. home/inbox 실행 전후로
ledger 바이트/mtime·row count·비-사이드카 파일목록 불변을 직접 확인한다(§8). WAL 사이드카
(-wal/-shm/-journal)는 mode=ro 조회의 SQLite 조정 파일이라 논리 상태가 아니며 예외로 둔다.
"""
from contextlib import suppress
import hashlib
import importlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.cli import daily  # noqa: E402

_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_NOW = 1_900_000_000  # 결정적 타임스탬프(미래) — due_date 는 과거 문자열로 확실히 due


# ── fixtures ───────────────────────────────────────────────────────────────────────
def _capture_ddl():
    return (
        "CREATE TABLE IF NOT EXISTS capture_candidates("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,"
        "confidence TEXT, signals TEXT, state TEXT NOT NULL DEFAULT 'captured_candidate',"
        "captured_at REAL NOT NULL, cwd TEXT, session_id TEXT)")


def _seed_capture(home, texts):
    """capture_buffer.sqlite 직접 seed(게이트 우회 · 결정적 id/pinned/captured_at)."""
    p = os.path.join(home, "capture_buffer.sqlite")
    c = sqlite3.connect(p)
    c.execute(_capture_ddl())
    for text, pinned in texts:
        c.execute("INSERT INTO capture_candidates(text,pinned,captured_at) VALUES(?,?,?)",
                  (text, pinned, _NOW))
    c.commit()
    c.close()


def _seed_hosted(home, intents):
    """<home>/hosted_inbox/*.json 로컬 staging seed(네트워크 fetch 없이 존재)."""
    from openbinggu_save_intent_outbox_runner import intent_hash, SCHEMA_VER
    staging = os.path.join(home, "hosted_inbox")
    os.makedirs(staging, exist_ok=True)
    for text, idxs in intents:
        confirm = "SAVE " + ",".join(str(i) for i in idxs)
        it = {"schema_ver": SCHEMA_VER, "text": text, "indices": idxs, "confirm": confirm,
              "intent_id": intent_hash(text, idxs, confirm),
              "created_ts": _NOW - 100, "ttl_s": 86400, "source": "hosted"}
        with open(os.path.join(staging, it["intent_id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(it, f, ensure_ascii=False)


def _seed_ledger(home, active=2, deprecated=1, due=True, approval=True, break_audit=False):
    from openbinggu_owner_accept_ux import open_accept
    from binggupack.safety import trusted_approval as ta
    os.makedirs(os.path.join(home, "snapshots"), exist_ok=True)
    ledger = os.path.join(home, "ledger.sqlite")
    db = open_accept(ledger)
    try:
        for i in range(active):
            db.con.execute(
                "INSERT INTO nodes(node_id,sentence,state,candidate,promotion_allowed) "
                "VALUES(?,?, 'active', 1, 0)",
                ("node:T:act%04d" % i, "이 입찰은 마진이 낮아 보류하기로 결정했다 %d." % i))
        for i in range(deprecated):
            db.con.execute(
                "INSERT INTO nodes(node_id,sentence,state,candidate,promotion_allowed) "
                "VALUES(?,?, 'deprecated', 1, 0)",
                ("node:T:dep%04d" % i, "폐기된 판단 %d." % i))
        if due:
            db.con.execute(
                "INSERT INTO judgment_reviews(node_id,due_date,status,ts) VALUES(?,?, 'pending', ?)",
                ("node:T:act0000", "2000-01-01", "2000-01-01T00:00:00Z"))
        db.con.commit()
        if approval:
            lid = "testledgerid"
            summ = ta.summary_for("hosted_bundle", {"items": [1, 2]}, lid)
            ta.upsert_request(db.con, "req_abcd1234ef", "1.0", "hosted_bundle",
                              "digestdigest", lid, summ, _NOW, 900, 32)
        if break_audit:
            db.con.execute(
                "INSERT OR REPLACE INTO audit_meta(key,value) VALUES('head_entry_hash','BOGUS_TAMPER')")
            db.con.commit()
    finally:
        db.close()
    # capture 활성 + provider 활성 플래그
    open(os.path.join(home, "capture_enabled"), "w").close()
    with open(ta.config_path(home), "w", encoding="utf-8") as f:
        json.dump({"enabled": True}, f)
    return ledger


def _full_home(tmp_path):
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = _seed_ledger(home)
    _seed_capture(home, [("캐시 전략은 이걸로 확정한다.", 0), ("백업은 항상 먼저 해 둔다.", 1)])
    _seed_hosted(home, [("이 견적은 과하다고 판단했다.", [1])])
    return home, ledger


# ── read-only snapshot helper ───────────────────────────────────────────────────────
def _is_sidecar(name):
    return any(name.endswith(s) for s in _SIDECAR_SUFFIXES)


def _snapshot(home):
    """비-사이드카 파일 → (size, sha256). WAL 사이드카는 mode=ro SQLite 조정 파일이라 제외."""
    snap = {}
    for root, _, files in os.walk(home):
        for f in files:
            if _is_sidecar(f):
                continue
            p = os.path.join(root, f)
            with suppress(OSError):
                with open(p, "rb") as fh:
                    data = fh.read()
                snap[os.path.relpath(p, home)] = (len(data), hashlib.sha256(data).hexdigest())
    return snap


def _run(args, env_extra=None, cwd=None):
    e = dict(os.environ)
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        e.update(env_extra)
    return subprocess.run([sys.executable, os.path.join(ROOT, "binggu.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=e, cwd=cwd or ROOT, timeout=120)


def _run_module(args, env_extra=None, cwd=None):
    e = dict(os.environ)
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONPATH"] = ROOT + os.pathsep + e.get("PYTHONPATH", "")
    if env_extra:
        e.update(env_extra)
    return subprocess.run([sys.executable, "-m", "binggupack", *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=e, cwd=cwd or ROOT, timeout=120)


# ════════════════════════ HOME ════════════════════════
def test_home_no_ledger_is_onboarding_and_creates_nothing(tmp_path):
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = os.path.join(home, "ledger.sqlite")
    before = _snapshot(home)
    r = _run(["--ledger", ledger, "home"])
    assert r.returncode == 0, r.stderr
    assert "환영" in r.stdout
    assert not os.path.exists(ledger), "온보딩이 ledger 를 생성하면 안 됨"
    assert _snapshot(home) == before, "온보딩 조회가 파일을 만들면 안 됨"
    # 디렉토리도 새로 안 생김(capture_buffer/hosted_inbox 등)
    assert os.listdir(home) == [], os.listdir(home)


def test_home_existing_ledger_shows_counts(tmp_path):
    home, ledger = _full_home(tmp_path)
    snap = daily.collect_home_snapshot(ledger, now=_NOW)
    assert snap["ledger"]["active"] == 2
    assert snap["ledger"]["deprecated"] == 1
    assert snap["queues"]["capture"] == 2
    assert snap["queues"]["hosted"] == 1
    assert snap["queues"]["approvals"] == 1
    assert snap["queues"]["due"] == 1
    assert snap["ledger"]["audit"] == "INTACT"
    txt = daily.render_home_text(snap, unicode_ok=True)
    assert "기억 2" in txt and "후보 2" in txt and "원격 1" in txt
    assert "승인 1" in txt and "검토 1" in txt


def test_home_audit_broken_is_prominent(tmp_path):
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = _seed_ledger(home, break_audit=True)
    snap = daily.collect_home_snapshot(ledger, now=_NOW)
    assert snap["ledger"]["audit"] == "BROKEN"
    txt = daily.render_home_text(snap, unicode_ok=True)
    # 무결성 경고가 '다음 할 일' 큐 항목보다 위(먼저)에 뜬다
    assert "무결성 점검" in txt
    assert "binggu doctor" in txt
    assert txt.index("무결성 점검") < txt.index("기억 찾기")
    # next_actions 의 첫 항목은 audit
    assert snap["next_actions"][0]["kind"] == "audit"


def test_home_is_read_only(tmp_path):
    home, ledger = _full_home(tmp_path)
    before = _snapshot(home)
    led_mt = os.path.getmtime(ledger)
    r1 = _run(["--ledger", ledger, "home"])
    r2 = _run(["--ledger", ledger, "home", "--json"])
    assert r1.returncode == 0 and r2.returncode == 0, (r1.stderr, r2.stderr)
    assert _snapshot(home) == before, "home 실행이 파일을 변경/생성했다"
    assert os.path.getmtime(ledger) == led_mt, "ledger mtime 변경됨"


def test_home_json_is_valid_and_stable(tmp_path):
    home, ledger = _full_home(tmp_path)
    r = _run(["--ledger", ledger, "home", "--json"])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)  # 순수 JSON(설명 문구 없음)
    assert j["schema_version"] == 1
    for k in ("generated_at", "ledger", "services", "queues", "next_actions"):
        assert k in j, k
    for k in ("active", "deprecated", "audit", "exists"):
        assert k in j["ledger"], k
    for k in ("capture", "hosted", "approvals", "due"):
        assert k in j["queues"], k
    # 절대 private path·홈 경로 미노출
    assert home not in r.stdout
    assert ".binggupack" not in r.stdout


# ════════════════════════ INBOX ════════════════════════
def test_inbox_empty_is_success(tmp_path):
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = _seed_ledger(home, active=1, deprecated=0, due=False, approval=False)
    r = _run(["--ledger", ledger, "inbox"])
    assert r.returncode == 0, r.stderr
    assert "검토할 항목이 없습니다" in r.stdout


def test_inbox_capture_uses_existing_indices(tmp_path):
    home, ledger = _full_home(tmp_path)
    # 정본 인덱스 = render_preview(pinned DESC, id ASC)
    from binggu_capture_persist import PersistentCaptureBuffer
    canon = PersistentCaptureBuffer(home=home).render_preview(now=_NOW)["items"]
    snap = daily.collect_inbox_snapshot(ledger, sections=["capture"], now=_NOW)
    got = snap["sections"]["capture"]["items"]
    assert len(got) == len(canon) == 2
    for g, c in zip(got, canon):
        assert g["idx"] == c["idx"]
        # 발췌는 절단될 수 있으나 정본 text 로 시작(동일 정렬·동일 항목)
        assert c["text"].startswith(g["preview"].rstrip("…")[:10])
    # 첫 항목은 pinned(pinned DESC)
    assert got[0]["pinned"] is True


def test_inbox_hosted_uses_existing_indices(tmp_path):
    home, ledger = _full_home(tmp_path)
    _seed_hosted(home, [("두 번째 원격 판단.", [1]), ("세 번째 원격 판단.", [1])])
    from binggu_hosted_inbox import summarize, staging_dir_for
    canon = summarize(staging_dir_for(home), _NOW)["items"]
    snap = daily.collect_inbox_snapshot(ledger, sections=["hosted"], now=_NOW)
    got = snap["sections"]["hosted"]["items"]
    assert [g["idx"] for g in got] == [c["idx"] for c in canon]
    assert snap["sections"]["hosted"]["count"] == len(canon) >= 3


def test_inbox_does_not_fetch_network(tmp_path, monkeypatch):
    home, ledger = _full_home(tmp_path)
    hi = importlib.import_module("binggu_hosted_inbox")

    def _boom(*a, **k):
        raise AssertionError("inbox 가 네트워크 fetch(fetch_to_staging)를 호출했다")

    monkeypatch.setattr(hi, "fetch_to_staging", _boom)
    snap = daily.collect_inbox_snapshot(ledger, sections=["hosted"], now=_NOW)  # 예외 없어야 함
    assert snap["sections"]["hosted"]["count"] == 1  # 로컬 staging 만 읽음


def test_inbox_pending_approval_is_redacted(tmp_path):
    home, ledger = _full_home(tmp_path)
    # 민감 원문/nonce 를 별도 파일에 심어두고, inbox 가 그것들을 읽지 않음을 확인
    with open(os.path.join(home, "approvals.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"nonce": "NONCE_SECRET_SENTINEL"}) + "\n")
    os.makedirs(os.path.join(home, "approval_review"), exist_ok=True)
    with open(os.path.join(home, "approval_review", "req_abcd1234ef.json"), "w", encoding="utf-8") as f:
        json.dump({"raw": "RAW_PAYLOAD_SENTINEL 민감한 판단 원문"}, f, ensure_ascii=False)
    r = _run(["--ledger", ledger, "inbox", "--approvals"])
    rj = _run(["--ledger", ledger, "inbox", "--approvals", "--json"])
    assert r.returncode == 0 and rj.returncode == 0
    for out in (r.stdout, rj.stdout):
        assert "NONCE_SECRET_SENTINEL" not in out, "nonce 노출"
        assert "RAW_PAYLOAD_SENTINEL" not in out, "raw payload 노출"
    # payload-agnostic summary 만(operation + item 수)
    j = json.loads(rj.stdout)
    items = j["sections"]["approvals"]["items"]
    assert len(items) == 1
    assert items[0]["request_id"] == "req_abcd1234ef"  # 후속 명령용 digest(비밀 아님)
    assert "intent(s)" in items[0]["summary"]


def test_inbox_due_items_render(tmp_path):
    home, ledger = _full_home(tmp_path)
    snap = daily.collect_inbox_snapshot(ledger, sections=["due"], now=_NOW)
    items = snap["sections"]["due"]["items"]
    assert len(items) == 1
    assert items[0]["due_date"] == "2000-01-01"
    assert len(items[0]["id"]) <= 8
    r = _run(["--ledger", ledger, "inbox", "--due"])
    assert r.returncode == 0
    assert "검토 예정 1개" in r.stdout
    assert "2000-01-01" in r.stdout


def test_inbox_combined_counts_are_exact(tmp_path):
    home, ledger = _full_home(tmp_path)
    snap = daily.collect_inbox_snapshot(ledger, now=_NOW)  # 전 섹션
    counts = {k: v["count"] for k, v in snap["sections"].items()}
    assert counts == {"capture": 2, "hosted": 1, "approvals": 1, "due": 1}
    # 홈 queue 와도 일치
    hsnap = daily.collect_home_snapshot(ledger, now=_NOW)
    assert hsnap["queues"] == counts


def test_inbox_json_is_valid_and_redacted(tmp_path):
    home, ledger = _full_home(tmp_path)
    r = _run(["--ledger", ledger, "inbox", "--json"])
    assert r.returncode == 0, r.stderr
    j = json.loads(r.stdout)
    assert j["schema_version"] == 1
    assert set(j["sections"]) == {"capture", "hosted", "approvals", "due"}
    assert home not in r.stdout and ".binggupack" not in r.stdout


def test_inbox_is_read_only(tmp_path):
    home, ledger = _full_home(tmp_path)
    before = _snapshot(home)
    led_mt = os.path.getmtime(ledger)
    cap_mt = os.path.getmtime(os.path.join(home, "capture_buffer.sqlite"))
    r1 = _run(["--ledger", ledger, "inbox"])
    r2 = _run(["--ledger", ledger, "inbox", "--json"])
    assert r1.returncode == 0 and r2.returncode == 0, (r1.stderr, r2.stderr)
    assert _snapshot(home) == before, "inbox 실행이 파일을 변경/생성했다"
    assert os.path.getmtime(ledger) == led_mt
    assert os.path.getmtime(os.path.join(home, "capture_buffer.sqlite")) == cap_mt


# ════════════════════════ DISPATCH / COMPAT ════════════════════════
def test_no_arg_binggu_dispatches_home(tmp_path):
    home, ledger = _full_home(tmp_path)
    r = _run([], env_extra={"BINGGU_HOME": home})   # 완전 무인자
    assert r.returncode == 0, r.stderr
    assert "BingguPack" in r.stdout and "다음 할 일" in r.stdout


def test_explicit_home_matches_no_arg(tmp_path):
    home, ledger = _full_home(tmp_path)
    r_noarg = _run([], env_extra={"BINGGU_HOME": home})
    r_home = _run(["home"], env_extra={"BINGGU_HOME": home})
    assert r_noarg.returncode == 0 and r_home.returncode == 0
    # 텍스트 홈은 타임스탬프가 없어 동일해야 함
    assert r_noarg.stdout == r_home.stdout


def test_existing_help_unchanged(tmp_path):
    r = _run(["--help"])
    assert r.returncode == 0
    for cmd in ("status", "inbox", "home", "hosted", "approval", "recall", "save", "deprecate"):
        assert cmd in r.stdout, cmd


def test_existing_hosted_inbox_unchanged(tmp_path):
    home, ledger = _full_home(tmp_path)
    # 중첩 `hosted inbox` 는 여전히 존재하고 --no-fetch/--since 플래그를 받는다(fetch 경로 보존)
    r = _run(["--ledger", ledger, "hosted", "inbox", "--no-fetch"])
    assert r.returncode == 0, r.stderr
    assert "hosted inbox" in r.stdout


def test_hosted_inbox_anchor_stays_in_ledger_home(tmp_path):
    """save-n 앵커는 ledger-home 에만 — 전역(BINGGU_HOME) home 오염 0 (2026-07-13 결함 회귀망)."""
    home, ledger = _full_home(tmp_path)
    global_home = str(tmp_path / "global_home")
    os.makedirs(global_home)
    r = _run(["--ledger", ledger, "hosted", "inbox", "--no-fetch"],
             env_extra={"BINGGU_HOME": global_home})
    assert r.returncode == 0, r.stderr
    # 앵커는 데이터와 같은 축(ledger-home)에 기록된다
    assert os.path.exists(os.path.join(home, "last_preview_candidates.json"))
    # ledger 를 격리한 실행이 전역 home 의 앵커를 만들거나 덮지 않는다
    assert not os.path.exists(os.path.join(global_home, "last_preview_candidates.json"))


def test_hosted_inbox_no_anchor_skips_preview(tmp_path):
    """--no-anchor(무인 렌더)는 사람 SAVE 앵커를 아예 기록하지 않는다(auto_pull 결함 회귀망)."""
    home, ledger = _full_home(tmp_path)
    r = _run(["--ledger", ledger, "hosted", "inbox", "--no-fetch", "--no-anchor"])
    assert r.returncode == 0, r.stderr
    assert not os.path.exists(os.path.join(home, "last_preview_candidates.json"))


def test_existing_mutation_commands_unchanged(tmp_path):
    # 기존 mutation 명령의 인자 표면(‑‑help)이 그대로다(회귀 0)
    for cmd, needle in (("save", "--preview-id"), ("deprecate", "--confirm"),
                        ("pair", "--relation"), ("resolve", "--outcome")):
        r = _run([cmd, "--help"])
        assert r.returncode == 0, (cmd, r.stderr)
        assert needle in r.stdout, (cmd, needle)


# ════════════════════════ ENTRY POINT (외부 cwd · 설치본 프록시) ════════════════════════
def test_wheel_entrypoint_no_arg_home(tmp_path):
    # `python -m binggupack`(설치본 console_script 와 동일 진입)을 저장소 밖 cwd 에서 실행
    home, ledger = _full_home(tmp_path)
    ext = str(tmp_path / "external_cwd")
    os.makedirs(ext)
    r = _run_module([], env_extra={"BINGGU_HOME": home}, cwd=ext)
    assert r.returncode == 0, r.stderr
    assert "BingguPack" in r.stdout


def test_wheel_entrypoint_inbox_external_cwd(tmp_path):
    home, ledger = _full_home(tmp_path)
    ext = str(tmp_path / "external_cwd2")
    os.makedirs(ext)
    r = _run_module(["inbox"], env_extra={"BINGGU_HOME": home}, cwd=ext)
    assert r.returncode == 0, r.stderr
    rj = _run_module(["inbox", "--json"], env_extra={"BINGGU_HOME": home}, cwd=ext)
    assert rj.returncode == 0, rj.stderr
    assert json.loads(rj.stdout)["schema_version"] == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
