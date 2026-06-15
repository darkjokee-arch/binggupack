#!/usr/bin/env python3
"""hag_blind_ledger.py — hash-chain append-only blind ledger.

목적
  AGI 가설/답안을 "봉인(seal)"한 뒤 나중에 "공개(reveal)"하는 commit-reveal 패턴을
  hash-chain append-only 원장으로 기록한다. 한번 쓴 행은 update/delete 불가(append만).

영구금지 준수
  - 운영 ledger(~/.binggupack/ledger.sqlite·capture_buffer.sqlite) 미접촉. 본 모듈은
    호출자가 넘긴 temp_path(SQLite)만 연다. 기본 경로/홈 경로 추정 없음.
  - actor != 'human' 은 append 단계에서 BLOCK (allowlist default-deny).
  - update/delete SQL 없음. INSERT(append) + SELECT(verify)만 사용.

스키마(컬럼)
  seq         INTEGER  단조 증가 시퀀스(0부터). PK.
  prev_hash   TEXT     직전 행 entry_hash. 최초 행은 GENESIS_HASH.
  entry_hash  TEXT     sha256(prev_hash + 핵심필드) hex.
  seal        TEXT     봉인된 값(예: 가설/답안의 봉인 토큰). 공개 전 blind 식별자.
  seal_ts     INTEGER  봉인 시각(epoch).
  answer_hash TEXT     답안의 sha256 hex (공개 전엔 답 자체 비노출).
  commit_ts   INTEGER  커밋 시각(epoch). seal_ts < commit_ts 강제.
  reveal_ts   INTEGER  공개 시각(epoch). commit_ts < reveal_ts 강제.
  actor       TEXT     행위자. 'human'만 허용.
  result      TEXT     공개 결과/판정 라벨.

불변식
  - seal_ts < commit_ts < reveal_ts (위반 시 BLOCK / append 거부).
  - append only. update/delete 경로 없음.
  - entry_hash 는 prev_hash + 핵심필드 결정론적 직렬화의 sha256.

selftest
  python hag_blind_ledger.py --selftest  ->  'GATE: GO' (전부 통과) 또는 'GATE: STOP'.
  실시간 시각/난수 미사용. 모든 ts는 주입값. 임시 DB(tempfile.mkdtemp)만 사용.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile

GENESIS_HASH = "0" * 64
ALLOWED_ACTORS = frozenset({"human"})  # allowlist default-deny


class LedgerError(Exception):
    """append/verify 정책 위반(BLOCK)."""


# ---- 핵심 해시 ---------------------------------------------------------------

def _core_payload(prev_hash, seq, seal, seal_ts, answer_hash,
                  commit_ts, reveal_ts, actor, result):
    """entry_hash 입력이 되는 결정론적 직렬화 문자열.

    구분자 \x1f(unit separator)로 필드 경계를 명확히 해 인접 필드 합치기 공격을 막는다.
    """
    parts = [
        prev_hash,
        str(seq),
        seal,
        str(seal_ts),
        answer_hash,
        str(commit_ts),
        str(reveal_ts),
        actor,
        result,
    ]
    return "\x1f".join(parts)


def compute_entry_hash(prev_hash, seq, seal, seal_ts, answer_hash,
                       commit_ts, reveal_ts, actor, result):
    payload = _core_payload(prev_hash, seq, seal, seal_ts, answer_hash,
                            commit_ts, reveal_ts, actor, result)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---- 원장 ------------------------------------------------------------------

class BlindLedger:
    """temp_path SQLite 위의 append-only hash-chain 원장."""

    def __init__(self, conn):
        self._conn = conn

    # update/delete 메서드는 의도적으로 정의하지 않음(append-only).

    def append(self, row):
        """한 행을 체인에 append.

        row: dict {seal, seal_ts, answer_hash, commit_ts, reveal_ts, actor, result}
        반환: 기록된 전체 행 dict (seq·prev_hash·entry_hash 포함).
        위반 시 LedgerError(BLOCK).
        """
        actor = row.get("actor")
        if actor not in ALLOWED_ACTORS:
            raise LedgerError(
                f"BLOCK actor not allowed: {actor!r} (allowlist={sorted(ALLOWED_ACTORS)})"
            )

        try:
            seal = str(row["seal"])
            seal_ts = int(row["seal_ts"])
            answer_hash = str(row["answer_hash"])
            commit_ts = int(row["commit_ts"])
            reveal_ts = int(row["reveal_ts"])
            result = str(row["result"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerError(f"BLOCK invalid row fields: {exc}")

        # 불변식: seal_ts < commit_ts < reveal_ts
        if not (seal_ts < commit_ts < reveal_ts):
            raise LedgerError(
                f"BLOCK temporal invariant: require seal_ts<commit_ts<reveal_ts, "
                f"got {seal_ts}<{commit_ts}<{reveal_ts}"
            )

        cur = self._conn.cursor()
        cur.execute("SELECT seq, entry_hash FROM ledger ORDER BY seq DESC LIMIT 1")
        last = cur.fetchone()
        if last is None:
            seq = 0
            prev_hash = GENESIS_HASH
        else:
            seq = int(last[0]) + 1
            prev_hash = str(last[1])

        entry_hash = compute_entry_hash(
            prev_hash, seq, seal, seal_ts, answer_hash,
            commit_ts, reveal_ts, actor, result,
        )

        cur.execute(
            "INSERT INTO ledger "
            "(seq, prev_hash, entry_hash, seal, seal_ts, answer_hash, "
            " commit_ts, reveal_ts, actor, result) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (seq, prev_hash, entry_hash, seal, seal_ts, answer_hash,
             commit_ts, reveal_ts, actor, result),
        )
        self._conn.commit()

        return {
            "seq": seq,
            "prev_hash": prev_hash,
            "entry_hash": entry_hash,
            "seal": seal,
            "seal_ts": seal_ts,
            "answer_hash": answer_hash,
            "commit_ts": commit_ts,
            "reveal_ts": reveal_ts,
            "actor": actor,
            "result": result,
        }

    def rows(self):
        cur = self._conn.cursor()
        cur.execute(
            "SELECT seq, prev_hash, entry_hash, seal, seal_ts, answer_hash, "
            "commit_ts, reveal_ts, actor, result FROM ledger ORDER BY seq ASC"
        )
        cols = ["seq", "prev_hash", "entry_hash", "seal", "seal_ts",
                "answer_hash", "commit_ts", "reveal_ts", "actor", "result"]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def verify_chain(self):
        """체인 무결성 검사. 정상 True / 변조·끊김·불변식위반 False."""
        rows = self.rows()
        prev_hash = GENESIS_HASH
        expected_seq = 0
        for r in rows:
            if r["seq"] != expected_seq:
                return False
            if r["prev_hash"] != prev_hash:
                return False
            # 불변식 재검(저장 후 직접 변조 탐지)
            if not (r["seal_ts"] < r["commit_ts"] < r["reveal_ts"]):
                return False
            if r["actor"] not in ALLOWED_ACTORS:
                return False
            recomputed = compute_entry_hash(
                r["prev_hash"], r["seq"], r["seal"], r["seal_ts"],
                r["answer_hash"], r["commit_ts"], r["reveal_ts"],
                r["actor"], r["result"],
            )
            if recomputed != r["entry_hash"]:
                return False
            prev_hash = r["entry_hash"]
            expected_seq += 1
        return True

    def close(self):
        self._conn.close()


def open_ledger(temp_path):
    """temp_path SQLite 파일을 열고(없으면 생성) BlindLedger 반환.

    영구금지: 운영 ledger 경로 사용 금지. 호출자가 tempfile 경로를 넘긴다.
    """
    conn = sqlite3.connect(temp_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ledger ("
        " seq         INTEGER PRIMARY KEY,"
        " prev_hash   TEXT NOT NULL,"
        " entry_hash  TEXT NOT NULL,"
        " seal        TEXT NOT NULL,"
        " seal_ts     INTEGER NOT NULL,"
        " answer_hash TEXT NOT NULL,"
        " commit_ts   INTEGER NOT NULL,"
        " reveal_ts   INTEGER NOT NULL,"
        " actor       TEXT NOT NULL,"
        " result      TEXT NOT NULL"
        ")"
    )
    conn.commit()
    return BlindLedger(conn)


# ---- selftest --------------------------------------------------------------

def _selftest():
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))

    tmpdir = tempfile.mkdtemp(prefix="hag_blind_ledger_")
    db_path = os.path.join(tmpdir, "blind.sqlite")
    led = open_ledger(db_path)

    # T1: 정상 append 체인 (seal_ts<commit_ts<reveal_ts)
    r0 = led.append({
        "seal": "seal-A", "seal_ts": 100, "answer_hash": "a" * 64,
        "commit_ts": 200, "reveal_ts": 300, "actor": "human", "result": "correct",
    })
    r1 = led.append({
        "seal": "seal-B", "seal_ts": 400, "answer_hash": "b" * 64,
        "commit_ts": 500, "reveal_ts": 600, "actor": "human", "result": "wrong",
    })
    check("T1 seq monotonic 0,1", r0["seq"] == 0 and r1["seq"] == 1)
    check("T1 genesis prev_hash", r0["prev_hash"] == GENESIS_HASH)
    check("T1 chain link", r1["prev_hash"] == r0["entry_hash"])

    # T2: verify_chain True
    check("T2 verify_chain True", led.verify_chain() is True)

    # T3: entry_hash 결정론
    r0_recomp = compute_entry_hash(
        GENESIS_HASH, 0, "seal-A", 100, "a" * 64, 200, 300, "human", "correct")
    check("T3 entry_hash deterministic", r0_recomp == r0["entry_hash"])

    # T4: 변조 탐지 (result 직접 UPDATE -> verify False)
    led._conn.execute("UPDATE ledger SET result='TAMPERED' WHERE seq=0")
    led._conn.commit()
    check("T4 tamper detected (verify False)", led.verify_chain() is False)
    led.close()

    # T5: 순서 위반 BLOCK (seal_ts >= commit_ts)
    db2 = os.path.join(tmpdir, "order.sqlite")
    led2 = open_ledger(db2)
    blocked = False
    try:
        led2.append({
            "seal": "s", "seal_ts": 500, "answer_hash": "c" * 64,
            "commit_ts": 200, "reveal_ts": 900, "actor": "human", "result": "x",
        })
    except LedgerError:
        blocked = True
    check("T5 order violation BLOCK", blocked)

    # T5b: commit_ts >= reveal_ts BLOCK
    blocked2 = False
    try:
        led2.append({
            "seal": "s", "seal_ts": 100, "answer_hash": "c" * 64,
            "commit_ts": 800, "reveal_ts": 800, "actor": "human", "result": "x",
        })
    except LedgerError:
        blocked2 = True
    check("T5b commit>=reveal BLOCK", blocked2)

    # T6: actor != human BLOCK (allowlist default-deny)
    for bad in ("ai", "system", "auto", "AUTO", "agent", "", None):
        blk = False
        try:
            led2.append({
                "seal": "s", "seal_ts": 1, "answer_hash": "d" * 64,
                "commit_ts": 2, "reveal_ts": 3, "actor": bad, "result": "x",
            })
        except LedgerError:
            blk = True
        check(f"T6 actor BLOCK {bad!r}", blk)

    # T7: BLOCK된 행은 실제로 안 써짐(append-only 무결성 유지)
    check("T7 nothing written after blocks", led2.rows() == [])

    # T8: 정상 actor=human 회귀 (BLOCK 사이에서도 동작)
    ok = led2.append({
        "seal": "ok", "seal_ts": 10, "answer_hash": "e" * 64,
        "commit_ts": 20, "reveal_ts": 30, "actor": "human", "result": "ok",
    })
    check("T8 human append works", ok["seq"] == 0 and led2.verify_chain() is True)

    # T9: update/delete 메서드 부재(append-only 표면)
    check("T9 no update method", not hasattr(led2, "update"))
    check("T9 no delete method", not hasattr(led2, "delete"))
    led2.close()

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"selftest: {passed}/{total}")
    gate = "GO" if passed == total else "STOP"
    print(f"GATE: {gate}")
    return passed, total, gate


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        p, t, g = _selftest()
        sys.exit(0 if g == "GO" else 1)
    print("usage: python hag_blind_ledger.py --selftest")
