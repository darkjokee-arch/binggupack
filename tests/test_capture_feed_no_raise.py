# -*- coding: utf-8 -*-
"""capture feed() 는 어떤 예외에도 raise 하지 않는다 (MF1.5 · NEW2.11).

MF1.5 시나리오: 사장님 발화 → hook 이 `_conn()` → 다른 프로세스(MCP recall)가 buffer 를 잠금
→ ALTER 실패 → 직후 INSERT 가 `no such column` 으로 raise → **그 발화가 버퍼에도 안 남고 사라진다**.
캡처 유실은 절단보다 큰 손실이라, feed 는
  ① 어떤 실패에도 raise 하지 않고
  ② 원문을 fallback jsonl 에 남기며
  ③ **침묵하지 않는다** — 사유(store_note)를 반환하고 대기 건수를 preview 에 상시 노출한다(NEW2.11).

운영 진입점 `PersistentCaptureBuffer.feed()` / `render_preview()` 만 태운다.
"""
import json
import os
import sqlite3
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import binggu_capture_persist as cap  # noqa: E402

CWD = "C:/Users/fixture-user/binggupack"
# 분류기(classify)가 captured_candidate 로 받는 owner 판단 문장 fixture 3종.
OWNER = "이 방식은 항상 버그를 유발한다는 교훈을 남긴다."
OWNER_A = "이 입찰은 마진이 낮아 보류하는 편이 낫다."
OWNER_B = "다음에는 이 거래처를 우선 검토하는 것이 낫겠다."


def _home(tmp_path, name):
    """capture ON + global scope 인 격리 홈(운영 홈 이름과 겹치지 않는 이름만 쓴다)."""
    home = tmp_path / name
    home.mkdir(parents=True, exist_ok=True)
    sc = cap.CaptureScope(home=str(home))
    sc.flag.write_text("1", encoding="utf-8")
    sc.scope_file.write_text(json.dumps({"global": True}), encoding="utf-8")
    return str(home)


def _fallback_records(buf):
    if not buf.fallback_path.exists():
        return []
    with open(buf.fallback_path, "r", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_feed_survives_connect_failure_and_preserves_text(tmp_path, monkeypatch):
    """sqlite 연결 자체가 죽어도 발화는 잃지 않는다(가장 넓은 실패면 주입)."""
    home = _home(tmp_path, "capfeed_connectfail_home")
    buf = cap.PersistentCaptureBuffer(home=home)

    def _boom(*a, **kw):
        raise sqlite3.OperationalError("주입: unable to open database file")

    monkeypatch.setattr(cap.sqlite3, "connect", _boom)
    r = buf.feed(OWNER, CWD, session_id="S-CAPFAIL")     # raise 하면 여기서 테스트가 죽는다
    monkeypatch.undo()

    assert r["action"] == "captured"
    assert r["stored"] is False
    assert "fallback_jsonl" in r["store_note"]           # 침묵 금지 — 사유 반환
    recs = _fallback_records(buf)
    assert len(recs) == 1 and recs[0]["text"] == OWNER   # 원문 보존(절단 0)
    assert recs[0]["src_sha"] == __import__("hashlib").sha256(OWNER.encode("utf-8")).hexdigest()

    # NEW2.11 — 조용히 비는 SAVE 목록 금지: 대기 건수가 preview 상단에 뜬다
    pv = buf.render_preview()
    assert pv["fallback_pending"] == 1
    assert "대기" in pv["note"]


def test_feed_survives_exclusive_lock_from_another_connection(tmp_path, monkeypatch):
    """MF1.5 원 시나리오 — 타 프로세스가 buffer 를 EXCLUSIVE 로 쥔 상태.

    busy_timeout 은 300ms 로 낮춰 테스트를 빠르게 한다(대기 시간만 바뀌고 판정 경로는 동일).
    """
    monkeypatch.setattr(cap, "CAPTURE_BUSY_TIMEOUT_MS", 300)
    home = _home(tmp_path, "capfeed_locked_home")
    buf = cap.PersistentCaptureBuffer(home=home)
    assert buf.feed(OWNER_A, CWD, session_id="S-LOCK")["stored"] is True

    blocker = sqlite3.connect(str(buf.db_path))
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        r = buf.feed(OWNER, CWD, session_id="S-LOCK")    # raise 0 이어야 한다
        assert r["stored"] is False and r["store_note"]
        assert _fallback_records(buf)[-1]["text"] == OWNER
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    # 잠금이 풀리면 정상 저장으로 복귀한다(영구 고장 아님)
    r2 = buf.feed(OWNER_B, CWD, session_id="S-LOCK")
    assert r2["stored"] is True
    assert buf.render_preview(session_id="S-LOCK")["fallback_pending"] == 1


def test_feed_degrades_when_new_columns_cannot_be_added(tmp_path, monkeypatch):
    """레거시 buffer + ALTER 불가 → 하드코딩 INSERT 대신 **동적 강등**으로 저장은 성공한다."""
    home = _home(tmp_path, "capfeed_degraded_home")
    buf = cap.PersistentCaptureBuffer(home=home)
    # 신규 컬럼이 없는 구 스키마를 먼저 만든다
    con = sqlite3.connect(str(buf.db_path))
    con.execute("""CREATE TABLE capture_candidates(
        id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
        pinned INTEGER NOT NULL DEFAULT 0, confidence TEXT, signals TEXT,
        state TEXT NOT NULL DEFAULT 'captured_candidate', captured_at REAL NOT NULL, cwd TEXT)""")
    con.commit()
    con.close()
    monkeypatch.setattr(cap, "_CAPTURE_ADDABLE", ())     # ALTER 전멸 시뮬

    r = buf.feed(OWNER, CWD, session_id="S-DEGRADE")
    assert r["stored"] is True                            # 저장은 성공
    assert r["store_note"].startswith("degraded_columns:")
    assert "src_id" in r["store_note"] and "src_sha" in r["store_note"]
    pv = buf.render_preview()                             # SELECT 도 동적 구성
    assert pv["count"] == 1 and pv["items"][0]["text"] == OWNER
    assert pv["items"][0]["src_id"] is None               # 컬럼 부재 = 조용한 에러가 아니라 None
    assert "fallback_pending" not in pv                   # 유실 아님


def test_per_column_alter_failures_are_recorded_not_swallowed(tmp_path, monkeypatch):
    """ALTER 는 컬럼별 개별 try — 한 컬럼 실패가 뒤 컬럼을 통째로 건너뛰지 않는다.

    evloc ON 에서 검증한다 — 앞막이 좌표 컬럼(src_id/src_sha)이 플래그 뒤에 있으므로
    OFF 면 시도 대상이 2종뿐이라 '뒤 컬럼 3개' 시나리오를 만들 수 없다.
    """
    monkeypatch.setenv("BINGGU_EVLOC_V5", "1")
    home = _home(tmp_path, "capfeed_altererr_home")
    buf = cap.PersistentCaptureBuffer(home=home)
    con = sqlite3.connect(str(buf.db_path))
    con.execute("""CREATE TABLE capture_candidates(
        id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
        pinned INTEGER NOT NULL DEFAULT 0, confidence TEXT, signals TEXT,
        state TEXT NOT NULL DEFAULT 'captured_candidate', captured_at REAL NOT NULL, cwd TEXT)""")
    con.commit()
    con.close()
    # 첫 컬럼만 실패하도록 DDL 을 망가뜨린다 — 나머지 3개는 그대로 붙어야 한다
    monkeypatch.setattr(cap, "_CAPTURE_ADDABLE",
                        (("session_id", "session_id BAD-TYPE!!"),) + cap._CAPTURE_ADDABLE[1:])
    r = buf.feed(OWNER, CWD, session_id="S-ALTER")
    assert r["stored"] is True
    assert "session_id" in buf._alter_errors               # 실패 사유가 남는다(삼킴 0)
    assert set(buf._live_cols) >= {"ai_context", "src_id", "src_sha"}   # 뒤 컬럼은 붙었다


def test_evloc_columns_are_flag_gated(tmp_path, monkeypatch):
    """앞막이 좌표 컬럼은 evloc 플래그 뒤 — OFF 면 운영 버퍼를 ALTER 하지 않는다.

    이 파일이 저장되는 순간 hook·MCP 가 _conn() 을 호출하므로, 게이트가 없으면
    플래그 OFF 인데도 다음 프롬프트에 운영 capture_buffer 스키마가 바뀐다.
    컬럼이 없어도 저장은 degraded_columns 로 정상 동작해야 한다.
    """
    monkeypatch.delenv("BINGGU_EVLOC_V5", raising=False)
    home = _home(tmp_path, "capfeed_evlocgate_home")
    buf = cap.PersistentCaptureBuffer(home=home)
    con = sqlite3.connect(str(buf.db_path))          # 운영과 동일 스키마(좌표 컬럼 없음)
    con.execute("""CREATE TABLE capture_candidates(
        id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
        pinned INTEGER NOT NULL DEFAULT 0, confidence TEXT, signals TEXT,
        state TEXT NOT NULL DEFAULT 'captured_candidate', captured_at REAL NOT NULL,
        cwd TEXT, session_id TEXT, ai_context TEXT)""")
    con.commit()
    con.close()

    r = buf.feed(OWNER, CWD, session_id="S-GATE-OFF")
    assert r["stored"] is True                                    # 저장은 정상
    assert "src_id" not in set(buf._live_cols)                    # ★ ALTER 0
    assert "src_sha" not in set(buf._live_cols)
    assert "degraded_columns:" in (r.get("store_note") or "")     # 강등 사유 표면화

    monkeypatch.setenv("BINGGU_EVLOC_V5", "1")                    # ON 이면 붙는다
    buf2 = cap.PersistentCaptureBuffer(home=home)
    r2 = buf2.feed(OWNER + " 둘째.", CWD, session_id="S-GATE-ON")
    assert r2["stored"] is True
    assert {"src_id", "src_sha"} <= set(buf2._live_cols)


def test_feed_never_raises_across_injected_failures(tmp_path, monkeypatch):
    """방어 3단(동적 INSERT → 최소 INSERT → jsonl)이 전부 막혀도 raise 0."""
    home = _home(tmp_path, "capfeed_total_home")
    buf = cap.PersistentCaptureBuffer(home=home)

    class _Boom(Exception):
        pass

    def _explode(*a, **kw):
        raise _Boom("주입: 저장 전멸")

    monkeypatch.setattr(cap.sqlite3, "connect", _explode)
    monkeypatch.setattr(cap.PersistentCaptureBuffer, "_fallback_append",
                        lambda self, payload, note: False)
    try:
        r = buf.feed(OWNER, CWD, session_id="S-TOTAL")
    except Exception as ex:                                # pragma: no cover - 실패 시 진단용
        pytest.fail("feed 가 raise 했다: %r" % (ex,))
    assert r["stored"] is False
    assert "FALLBACK-WRITE-FAILED" in r["store_note"]       # 최악의 경우도 사유는 남는다
