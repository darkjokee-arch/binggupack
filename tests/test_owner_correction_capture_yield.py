"""owner 교정 캡처 수율 회귀 — pair 전문 저장 · 구조 신호 relation · 깔때기 계수기.

배경(2026-08-04 적대검증 실측): 사장님 교정 엣지가 전체 53건뿐. 깔때기 실측(최근 7일)
버퍼 114건(교정계열 62) → 노드 매칭 38건(교정계열 17 · 73% 유실) → 교정 엣지 29건.
코드 원인 3:
  ① `_SENT_SPLIT` 문자클래스 lookbehind(`[.!?다음임함됨까요]`)가 '다/음/까…'로 끝나는
     아무 단어 뒤에서 오분리("사전 자체를 **다** | 쪼개야지" — 부사 '다'를 어미로 오판)
  ② batch 대화쌍 경로가 owner_pick=1 고정 → 첫 조각만 owner 노드가 되고 교정 원문의
     나머지가 무음 유실(운영 실측: "사전 자체를 다"·"쿼리를 꼭 페이지 열릴때마다" 류
     6~15자 절단 owner 노드 — 버퍼엔 전문 잔존)
  ③ 버퍼 상태 전이·TTL 소멸 계수기 부재 → 저장/미저장 구분 불가, 유실이 완전 무음.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

CWD = "C:/Users/fixture-user/binggupack"

# 운영 실사고 재현 발화 — '다'(부사)·'~마다' 뒤 공백에서 _SENT_SPLIT 이 오분리하는 형태.
OWNER_CORRECTION = ("사전 자체를 다 쪼개야지 지금 같이 온실까지 있어야 처리하는게 아니라 "
                    "금속구조물면허 보유업체도 같이 잡아야 하지 않나?")
PREV_AI = "사전 하나로 온실까지 묶어 처리하는 게 낫다고 판단합니다"


def _make_home(tmp_path):
    home = str(tmp_path)
    with open(os.path.join(home, "capture_enabled"), "w", encoding="utf-8") as f:
        f.write("1")
    with open(os.path.join(home, "capture_scope.json"), "w", encoding="utf-8") as f:
        json.dump({"allowed_cwd_prefixes": [CWD], "denied_cwd_substrings": ["example-project"]},
                  f, ensure_ascii=False)
    return home


def test_batch_pair_saves_whole_owner_text(tmp_path, monkeypatch):
    """대화쌍 batch 저장 → owner 노드 = 발화 **전문**(조각 아님) + owner_refutes 엣지."""
    home = _make_home(tmp_path)
    monkeypatch.setenv("BINGGU_HOME", home)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    subprocess.run([sys.executable, os.path.join(_ROOT, "binggu.py"), "start"],
                   env=dict(os.environ), capture_output=True)
    from binggu_capture_persist import PersistentCaptureBuffer
    from binggu_save_batch import save_candidates_batch, stage_batch_anchor

    import binggu
    from binggupack.safety.gate_log import gate_record_from_prompt

    buf = PersistentCaptureBuffer(home=home)
    r = buf.feed(OWNER_CORRECTION, CWD, prev_turn=PREV_AI, session_id="S-YIELD")
    assert r["action"] == "captured" and r["stored"]

    items = buf.render_preview(session_id="S-YIELD").get("items", [])
    assert len(items) == 1 and items[0]["ai_context"]
    assert items[0].get("buffer_id")                      # mark_saved 배선 키
    assert items[0].get("pair_relation") == "owner_refutes"   # 반문(않나?) → refutes 제안

    anchor = os.path.join(home, "last_preview_candidates.json")
    stage_batch_anchor(items, path=anchor, session_id="S-YIELD")
    assert gate_record_from_prompt("SAVE 1") >= 1        # owner SAVE 발화 앵커(사람 도장)

    ledger = os.path.join(home, "ledger.sqlite")
    db, snap = binggu._open(ledger)
    try:
        rb = save_candidates_batch(db, snap, items, [1])
        assert rb["applied"] and rb["saved"] == 1
        assert rb["results"][0].get("owner_whole") is True
        assert rb["results"][0].get("owner_whole_fallback") is None

        want = re.sub(r"\s+", " ", OWNER_CORRECTION).strip()
        rows = db.con.execute(
            "SELECT sentence FROM nodes WHERE speaker='owner'").fetchall()
        assert [s for (s,) in rows] == [want]            # 전문 저장 — 첫 조각("사전 자체를 다") 아님
        edges = db.con.execute(
            "SELECT e.relation FROM edges e JOIN nodes s ON e.source=s.node_id"
            " JOIN nodes t ON e.target=t.node_id"
            " WHERE s.speaker='owner' AND t.speaker='ai'").fetchall()
        assert [rel for (rel,) in edges] == ["owner_refutes"]   # 교정 엣지 형성
    finally:
        db.close()


def test_relation_from_signals_structural_negation():
    """신호명 무매치라도 owner 부정/정정 표현(구조 신호)이면 refutes — 기존 매핑은 회귀 0."""
    from binggu_capture_persist import relation_from_signals

    # 기존 신호 매핑 보존(회귀 앵커 — 모듈 selftest T37과 동일 계약)
    assert relation_from_signals(["AI교정"]) == "owner_refutes"
    assert relation_from_signals(["반문"]) == "owner_refutes"
    assert relation_from_signals(["약한교정(맥락1턴)"]) == "owner_revises"
    assert relation_from_signals([]) == "owner_revises"
    # 구조 신호: 신호명은 없는데(선긋기금지 등) 부정/정정 표현 → refutes
    assert relation_from_signals(["선긋기금지"], text="그렇게 하면 안 돼") == "owner_refutes"
    assert relation_from_signals([], text="왜 또 제목으로 하는거지") == "owner_refutes"
    assert relation_from_signals([], text="틀렸잖아 다시 봐") == "owner_refutes"
    # 부정 표현 없는 중립 발화 → 종전 기본값(revises)
    assert relation_from_signals([], text="이 방식으로 정리해두자") == "owner_revises"
    # 신호명이 있으면 텍스트보다 우선(약한교정 + 부정 표현 → revises 유지)
    assert relation_from_signals(["약한교정(맥락1턴)"], text="아니야") == "owner_revises"


def test_mark_saved_transitions_and_filters_preview(tmp_path):
    """mark_saved → 상태 전이 + preview 재등장 차단(중복 SAVE 유도 제거)."""
    home = _make_home(tmp_path)
    from binggu_capture_persist import PersistentCaptureBuffer

    buf = PersistentCaptureBuffer(home=home)
    buf.feed("A안으로 결정한다", CWD, session_id="S-M")
    buf.feed("B안으로 결정한다", CWD, session_id="S-M")
    items = buf.render_preview(session_id="S-M")["items"]
    assert len(items) == 2 and all(it.get("buffer_id") for it in items)

    assert buf.mark_saved([items[0]["buffer_id"]]) == 1
    left = buf.render_preview(session_id="S-M")["items"]
    assert [it["text"] for it in left] == [items[1]["text"]]   # 저장분은 목록에서 제외

    con = sqlite3.connect(str(buf.db_path))
    states = dict(con.execute("SELECT id, state FROM capture_candidates").fetchall())
    con.close()
    assert states[items[0]["buffer_id"]] == "saved_to_ledger"
    assert states[items[1]["buffer_id"]] == "captured_candidate"
    # 멱등: 이미 전이된 행 재호출 → 0
    assert buf.mark_saved([items[0]["buffer_id"]]) == 0


def test_ttl_purge_counts_unsaved_loss(tmp_path):
    """TTL 소멸이 더 이상 무음이 아니다 — 미저장/교정계열 계수 + preview 경고 노출."""
    home = _make_home(tmp_path)
    from binggu_capture_persist import DEFAULT_TTL_DAYS, PersistentCaptureBuffer

    buf = PersistentCaptureBuffer(home=home)
    t0 = time.time()
    # 교정계열(반문 신호) 1건 + 일반 판단 1건 — 둘 다 저장 없이 TTL 소멸 예정
    r1 = buf.feed("여긴 왜 이렇게 진행하지", CWD, prev_turn="C안으로 하자는 제안", now=t0,
                  session_id="S-T")
    r2 = buf.feed("B안으로 결정한다", CWD, now=t0, session_id="S-T")
    assert r1["stored"] and r2["stored"]
    assert "반문" in r1["verdict"]["signals"]

    future = t0 + (DEFAULT_TTL_DAYS + 1) * 86400
    pv = buf.render_preview(now=future)
    assert pv["count"] == 0                                   # 소멸 자체는 종전과 동일
    assert pv["ttl_lost"] == {"unsaved": 2, "unsaved_correction": 1}
    assert "TTL 소멸(미저장) 2건(교정계열 1)" in pv["note"]   # 무음 유실 금지

    con = sqlite3.connect(str(buf.db_path))
    ev = con.execute("SELECT event, n, n_correction FROM funnel_events").fetchall()
    con.close()
    assert ("ttl_purged_captured_candidate", 2, 1) in ev
