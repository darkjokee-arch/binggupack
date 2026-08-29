# -*- coding: utf-8 -*-
"""hag_l1_bridge.py — hybrid-AGI orchestrator L1 제안 → owner 승인 대기 큐(화자축 분리 배선).

목적:
  orchestrator(HybridAGIOrchestrator)가 정형화한 AI 제안 L1 명제(ai_inferred·휘발)를
  **owner candidate 와 절대 섞지 않는 별도 staging** 에 적재한다. 사람 도장(승인) 전에는
  비영구 — 운영 ledger write 0. session_close preview 에 'AI 제안 명제' 별도 섹션으로 노출,
  owner 가 승인(단계2)해야만 영구화한다.

화자축 분리(핵심 안전):
  owner=자연어 원문 그대로 저장·AI 정리 저장 금지(owner 3회+ 지적) 규율 준수. 본 staging
  (l1_proposals.sqlite)은 capture_buffer(owner candidate)와 물리 분리된 파일이며,
  extracted_by='ai_inferred' 로 화자를 명시한다. owner 후보 큐로 절대 흐르지 않는다.

영구금지 준수(전부 selftest 실측):
  - 운영 ledger(~/.binggupack/ledger.sqlite·capture_buffer.sqlite) 미접촉(읽기도 X).
  - staging 은 candidate-only·휘발(status='proposed'). 영구화(운영 등재)는 단계2(owner 도장).
  - orchestrator 는 temp blind_ledger·temp home 으로만 구동 — 운영 경로 미접촉(생성자 방어 재사용).
  - 결정론적: ts·now 는 호출자 주입(실시간 시각/난수 금지).

CLI: python hag_l1_bridge.py --selftest  ->  'GATE: GO' | 'GATE: STOP'
"""
from __future__ import annotations

from contextlib import suppress
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# 단계1(제안 staging)은 L0/L1 정형화 게이트만 필요 → hag_l1_proposition 직접 사용(파일·커넥션 0).
# 단계2(승인→영구화)에서 hag_orchestrator.blind_stamp_l1(commit-reveal) 을 별도 배선한다.
import hag_l1_proposition as l1       # noqa: E402


L1_DB_NAME = "l1_proposals.sqlite"
STATUS_PROPOSED = "proposed"
STATUS_APPROVED = "approved"     # 단계2(owner 도장) 후
STATUS_IMPORTED = "imported"     # 단계2 운영 등재 후


def _hag_home(home=None):
    """staging 루트 — <home>/hybrid_agi/ (keyring 과 동일 위치·repo 밖·candidate 저장소).

    테스트는 home 인자 또는 BINGGU_HOME 으로 운영 경로 미접촉.
    """
    if home is None:
        home = os.environ.get("BINGGU_HOME") or os.path.expanduser("~/.binggupack")
    d = os.path.join(home, "hybrid_agi")
    os.makedirs(d, exist_ok=True)
    return d


def l1_db_path(home=None):
    return os.path.join(_hag_home(home), L1_DB_NAME)


def _l1_key(l0_id, proposition):
    """멱등키 — 같은 (L0, 명제) 재제안 시 중복 0."""
    raw = "l1_prop:%s|%s" % (l0_id, proposition)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def open_l1_db(path):
    """l1_proposals staging 열기(없으면 생성). 운영 ledger 와 별도 파일."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS l1_proposals ("
        " l1_key       TEXT PRIMARY KEY,"       # 멱등키
        " l0_id        TEXT NOT NULL,"
        " l0_raw       TEXT NOT NULL,"          # 원문 스냅샷(출처 추적)
        " proposition  TEXT NOT NULL,"          # AI 제안 명제(ai_inferred)
        " source_start INTEGER NOT NULL,"
        " source_end   INTEGER NOT NULL,"
        " extracted_by TEXT NOT NULL,"          # 항상 'ai_inferred'(화자축 명시)
        " origin       TEXT,"
        " status       TEXT NOT NULL DEFAULT 'proposed',"  # proposed→approved→imported(단계2)
        " session_id   TEXT,"
        " created_at   INTEGER,"
        " updated_at   INTEGER"
        ")")
    conn.commit()
    return conn


def stage_proposals(conn, l0_id, l0_raw, items, ts, now, session_id=None):
    """AI 제안 L1 명제를 staging 에 적재(orchestrator 정형화 경유·화자축 분리).

    items = [{"proposition": str, "source_span": (start, end) | None}].
      source_span 미지정 시 (0, len(l0_raw)) 전체 구간으로 폴백.
    orchestrator.save_l0 + propose_l1 로 타입분리·source_span 범위·ai_inferred 휘발 규율을
    강제 검증한 뒤 통과분만 INSERT. 멱등(l1_key PK) — 재적재 중복 0. 운영 ledger write 0.

    ts  = 명제 created_at(orchestrator L1 객체용·문자열 허용).
    now = staging 감사 타임스탬프(int epoch·sqlite).
    반환: {"staged": n, "skipped_dup": m, "invalid": k, "l1_keys": [...]}.
    """
    if not items:
        return {"staged": 0, "skipped_dup": 0, "invalid": 0, "l1_keys": []}
    l0 = l1.L0Raw(l0_id=l0_id, raw=l0_raw, created_at=ts)   # 원문(L0) — 순수 객체·파일 0·불변
    staged, dup, invalid, keys = 0, 0, 0, []
    for i, it in enumerate(items):
        prop = it.get("proposition")
        span = it.get("source_span") or (0, len(l0_raw))
        # L0/L1 정형화 게이트 — 타입분리·span 범위·ai_inferred 휘발 검증.
        # 실패분은 silent drop 하지 않고 invalid 로 카운트해 반환(호출자 가시).
        try:
            l1obj = l1.propose_l1_ai(l0, "%s#L1-%d" % (l0_id, i), prop, span, ts)
        except (ValueError, TypeError):
            invalid += 1
            continue
        key = _l1_key(l0_id, l1obj.proposition)
        cur = conn.execute(
            "INSERT OR IGNORE INTO l1_proposals "
            "(l1_key, l0_id, l0_raw, proposition, source_start, source_end,"
            " extracted_by, origin, status, session_id, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, l0_id, l0_raw, l1obj.proposition,
             l1obj.source_span[0], l1obj.source_span[1],
             l1obj.extracted_by, l1obj.origin, STATUS_PROPOSED, session_id, now, now))
        if cur.rowcount:
            staged += 1
            keys.append(key)
        else:
            dup += 1
    conn.commit()
    return {"staged": staged, "skipped_dup": dup, "invalid": invalid, "l1_keys": keys}


def list_pending(conn, status=STATUS_PROPOSED):
    """승인 대기(proposed) L1 제안 목록 — preview·승인 CLI 입력용."""
    cur = conn.execute(
        "SELECT l1_key, l0_id, l0_raw, proposition, source_start, source_end,"
        " session_id, created_at FROM l1_proposals WHERE status = ?"
        " ORDER BY created_at, l1_key", (status,))
    out = []
    for r in cur.fetchall():
        out.append({"l1_key": r[0], "l0_id": r[1], "l0_raw": r[2], "proposition": r[3],
                    "source_span": (r[4], r[5]), "session_id": r[6], "created_at": r[7]})
    return out


def _short(s, cap=60):
    s = " ".join(str(s).split())
    return s if len(s) <= cap else s[:cap - 1] + "…"


def render_l1_preview(pending):
    """session_close 통합용 markdown — 'AI 제안 명제(승인 대기)' 별도 섹션.

    화자축 분리 명시: 이 섹션은 AI 제안(ai_inferred)이며 owner candidate 와 다르다.
    owner 가 승인(단계2)해야만 영구화된다. 대기 0건이면 빈 문자열(섹션 미표시).
    """
    if not pending:
        return ""
    lines = ["### \U0001f9e9 AI 제안 명제 (승인 대기 · %d건)" % len(pending),
             "> ai_inferred · owner 후보와 분리 · 사장님 승인(도장) 전 비영구",
             ""]
    for i, p in enumerate(pending, 1):
        lines.append("- **H%d** %s" % (i, p["proposition"]))
        lines.append("    ↳ 출처 원문: %s" % _short(p["l0_raw"]))
    return "\n".join(lines)


# ---------------- selftest (결정론 · 운영홈 미접촉 실측) ----------------
def _dir_sig(d):
    """디렉터리 파일 (상대경로, size, mtime) 시그니처 — 운영홈 불변 실측용."""
    if not os.path.isdir(d):
        return None
    sig = []
    for root, _dirs, files in os.walk(d):
        for f in sorted(files):
            p = os.path.join(root, f)
            with suppress(OSError):
                st = os.stat(p)
                sig.append((os.path.relpath(p, d), st.st_size, int(st.st_mtime)))
    return tuple(sorted(sig))


def _selftest():
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    TS = "2026-06-15T00:00:00Z"          # 주입 ts(결정론)
    NOW = 1750000000                     # 주입 epoch(결정론)
    raw = "빨리 시작하고 같이 수정한다. 완벽 사전검증보다 점진 구현이 낫다."

    real_home = os.path.expanduser("~/.binggupack")
    real_before = _dir_sig(real_home)    # 운영홈 baseline

    with tempfile.TemporaryDirectory() as home:
        db = l1_db_path(home)            # 격리 홈 — 운영홈 미접촉
        ck(os.path.abspath(db).startswith(os.path.abspath(home)), "staging 격리 홈에 생성(운영홈 밖)")
        conn = open_l1_db(db)

        items = [
            {"proposition": "사장님은 완벽 사전검증보다 빠른 착수+수정을 선호한다",
             "source_span": (0, 16)},
            {"proposition": "점진 구현이 1인 환경 기본 전략이다",
             "source_span": (17, len(raw))},
        ]
        r = stage_proposals(conn, "L0-t1", raw, items, TS, NOW, session_id="s-test")
        ck(r["staged"] == 2 and r["skipped_dup"] == 0 and r["invalid"] == 0, "AI 제안 2건 staging")

        pend = list_pending(conn)
        ck(len(pend) == 2, "list_pending 2건")
        ck(all(p["proposition"] for p in pend), "명제 텍스트 보존")

        # 멱등 — 같은 제안 재적재 중복 0
        r2 = stage_proposals(conn, "L0-t1", raw, items, TS, NOW)
        ck(r2["staged"] == 0 and r2["skipped_dup"] == 2, "멱등 재적재 중복 0")
        ck(len(list_pending(conn)) == 2, "재적재 후에도 2건")

        # 화자축 분리 — 전부 ai_inferred(owner candidate 아님)
        cur = conn.execute("SELECT DISTINCT extracted_by FROM l1_proposals")
        kinds = sorted(x[0] for x in cur.fetchall())
        ck(kinds == [l1.EXTRACT_AI], "화자축 분리 — 전부 ai_inferred(owner candidate 아님)")

        # preview 렌더
        md = render_l1_preview(pend)
        ck("승인 대기" in md and "H1" in md, "preview 렌더(승인 대기 섹션)")
        ck(render_l1_preview([]) == "", "대기 0건 → 빈 섹션(미표시)")

        # source_span L0 초과 제안은 orchestrator 가 거부 → invalid(예외 대신 카운트)
        bad = [{"proposition": "범위초과", "source_span": (0, len(raw) + 99)}]
        rb = stage_proposals(conn, "L0-t1", raw, bad, TS, NOW)
        ck(rb["staged"] == 0 and rb["invalid"] == 1, "source_span L0 초과 → orchestrator 거부(invalid)")

        conn.close()

    # 운영홈 불변 실측(격리 홈 + temp orchestrator 만 씀)
    ck(_dir_sig(real_home) == real_before, "운영홈(~/.binggupack) 불변 — write 0")

    print("\nGATE: %s" % ("GO" if ok else "STOP"))
    return 0 if ok else 1


def _cli_stage(home_arg):
    """--stage: stdin JSON → staging. 대화 중 Claude 가 명제 후보를 넘기는 진입점.

    payload = {"home":?, "l0_id":str, "l0_raw":str, "ts":str, "now":int,
               "session_id":?, "items":[{"proposition":str, "source_span":[s,e]}]}.
    출력 = stage_proposals 결과 JSON.
    """
    payload = json.load(sys.stdin)
    home = payload.get("home") or home_arg
    conn = open_l1_db(l1_db_path(home))
    try:
        r = stage_proposals(
            conn, payload["l0_id"], payload["l0_raw"], payload.get("items", []),
            payload.get("ts", "-"), int(payload.get("now", 0)),
            session_id=payload.get("session_id"))
    finally:
        conn.close()
    print(json.dumps(r, ensure_ascii=False))
    return 0


def _cli_pending(home_arg):
    """--pending: 승인 대기(proposed) 제안 목록 JSON."""
    conn = open_l1_db(l1_db_path(home_arg))
    try:
        pend = list_pending(conn)
    finally:
        conn.close()
    print(json.dumps(pend, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hag_l1_bridge.py",
                                 description="hybrid-AGI L1 제안 → owner 승인 대기 큐(화자축 분리)")
    ap.add_argument("--selftest", action="store_true", help="결정론 selftest(운영홈 미접촉)")
    ap.add_argument("--stage", action="store_true", help="stdin JSON 제안 적재")
    ap.add_argument("--pending", action="store_true", help="승인 대기 제안 목록")
    ap.add_argument("--home", default=None, help="BINGGU_HOME(미지정=운영홈)")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.stage:
        return _cli_stage(a.home)
    if a.pending:
        return _cli_pending(a.home)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
