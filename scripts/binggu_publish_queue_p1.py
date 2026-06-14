"""BingguPack PC-mediated read 공유 — P1: publish_queue + 멱등 잠금 + 상태머신.

P1 범위(owner 명시 2026-06-14): 대기열 + 중복(멱등) 잠금 + selftest 까지만.
금지: 실 ledger write 0 / cloud upload 0 / 운영 DB insert 0 / capture_enabled 재활성 0.
빌드/검증/배포(②③⑤)는 P1 미구현 — 스텁/인터페이스만.

설계: docs/BINGGUPACK_CROSSDEVICE_PUBLISH_PIPELINE_DESIGN.md
4cli: session 20260614_1330_publish_pipeline (REFINE)
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_platform as _plat  # noqa: E402

# ── 상태머신 (불법 전이 ABORT) ────────────────────────────────
ALLOWED_TRANSITIONS = {
    "queued": {"building", "failed"},
    "building": {"candidate_ready", "failed", "aborted"},
    "candidate_ready": {"approved", "failed", "aborted"},
    "approved": {"deploying", "aborted"},
    "deploying": {"deployed", "aborted"},
    "deployed": set(),
    "failed": set(),
    "aborted": set(),
}
TERMINAL = {"deployed", "failed", "aborted"}

# 운영 장부 경로 — P1은 절대 접촉 금지(temp 전용).
# cross-platform: BINGGU_HOME 공유 장부도 거부 대상에 포함(helper 경유).
_OPERATIONAL_LEDGER = os.path.normcase(os.path.abspath(_plat.default_ledger()))


class QueueError(Exception):
    pass


class IllegalTransition(QueueError):
    pass


def _assert_temp_path(db_path: str) -> None:
    """운영 ledger 경로 거부 (P1 fail-closed)."""
    norm = os.path.normcase(os.path.abspath(db_path))
    if norm == _OPERATIONAL_LEDGER:
        raise QueueError("운영 ledger 경로 접근 거부 — P1은 temp DB 전용")


def open_queue(db_path: str) -> sqlite3.Connection:
    _assert_temp_path(db_path)
    conn = sqlite3.connect(db_path)
    _plat.apply_ledger_pragmas(conn)  # WAL + busy_timeout (동시 접근 fail-closed 일관)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS publish_queue (
            queue_id      TEXT PRIMARY KEY,
            node_id       TEXT NOT NULL,
            status        TEXT NOT NULL,
            node_hash     TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            bundle_hash   TEXT,
            lock_owner    TEXT,
            approved_by   TEXT,
            enqueued_at   TEXT,
            built_at      TEXT,
            approved_at   TEXT
        )
        """
    )
    conn.commit()
    return conn


# ── hash 3중 (node / evidence / bundle) ──────────────────────
def content_hash(data: bytes) -> str:
    """full sha256 (64 hex). hash8은 표시용일 뿐 pin/비교는 항상 full."""
    return hashlib.sha256(data).hexdigest()


def is_full_sha256(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value or ""))


def verify_hash_triple(row_node_hash, row_evidence_hash, row_bundle_hash,
                       expect_node, expect_evidence, expect_bundle):
    """enqueue → build 재읽기 → deploy 직전 3중 비교. 하나라도 불일치 ABORT."""
    if not (is_full_sha256(row_node_hash) and is_full_sha256(expect_node)):
        raise QueueError("node_hash full sha256 아님")
    if row_node_hash != expect_node:
        raise IllegalTransition("node_hash 불일치 — ledger 변조 의심")
    if row_evidence_hash != expect_evidence:
        raise IllegalTransition("evidence_hash 불일치")
    if expect_bundle is not None:
        if not is_full_sha256(expect_bundle):
            raise QueueError("bundle_hash full sha256 아님")
        if row_bundle_hash != expect_bundle:
            raise IllegalTransition("bundle_hash 불일치 — 후보 변조 의심")
    return True


# ── enqueue (포인터만 · evidence 없으면 BLOCK) ───────────────
def enqueue(conn, queue_id, node_id, node_hash, evidence_hash, ts="t0"):
    """SAVE 확정분 적재. evidence_hash 없으면 BLOCK(영구금지 27). 멱등."""
    if not evidence_hash:
        raise QueueError("evidence 미연결 — BLOCK (영구금지 27)")
    if not is_full_sha256(node_hash):
        raise QueueError("node_hash full sha256 아님")
    if not is_full_sha256(evidence_hash):
        raise QueueError("evidence_hash full sha256 아님")
    cur = conn.execute("SELECT 1 FROM publish_queue WHERE queue_id=?", (queue_id,))
    if cur.fetchone():
        raise QueueError(f"중복 enqueue 차단(멱등) — {queue_id}")
    conn.execute(
        "INSERT INTO publish_queue(queue_id,node_id,status,node_hash,evidence_hash,enqueued_at) "
        "VALUES(?,?,?,?,?,?)",
        (queue_id, node_id, "queued", node_hash, evidence_hash, ts),
    )
    conn.commit()
    return queue_id


# ── 멱등 단일 잠금 (watcher 중복 빌드·이중 deploy 차단) ──────
def acquire_lock(conn, queue_id, lock_owner):
    """lock_owner 없을 때만 set. 다른 owner 점유 시 실패. 같은 owner 재요청은 멱등 OK."""
    cur = conn.execute("SELECT lock_owner FROM publish_queue WHERE queue_id=?", (queue_id,))
    row = cur.fetchone()
    if row is None:
        raise QueueError(f"미존재 queue_id — {queue_id}")
    current = row[0]
    if current is None:
        conn.execute("UPDATE publish_queue SET lock_owner=? WHERE queue_id=?",
                     (lock_owner, queue_id))
        conn.commit()
        return True
    if current == lock_owner:
        return True  # 멱등
    raise QueueError(f"이미 잠김({current}) — 중복 차단")


def release_lock(conn, queue_id, lock_owner):
    cur = conn.execute("SELECT lock_owner FROM publish_queue WHERE queue_id=?", (queue_id,))
    row = cur.fetchone()
    if row and row[0] == lock_owner:
        conn.execute("UPDATE publish_queue SET lock_owner=NULL WHERE queue_id=?", (queue_id,))
        conn.commit()
        return True
    return False


# ── 상태 전이 (불법 ABORT) ────────────────────────────────────
def _status(conn, queue_id):
    cur = conn.execute("SELECT status FROM publish_queue WHERE queue_id=?", (queue_id,))
    row = cur.fetchone()
    if row is None:
        raise QueueError(f"미존재 queue_id — {queue_id}")
    return row[0]


def transition(conn, queue_id, to_status):
    cur_status = _status(conn, queue_id)
    if to_status not in ALLOWED_TRANSITIONS.get(cur_status, set()):
        raise IllegalTransition(f"불법 전이 {cur_status} -> {to_status} — ABORT")
    conn.execute("UPDATE publish_queue SET status=? WHERE queue_id=?", (to_status, queue_id))
    conn.commit()
    return to_status


def mark_block(conn, queue_id, reason):
    """검증 실패·미실행·증거 없음 = 전부 BLOCK → failed (fail-closed)."""
    cur_status = _status(conn, queue_id)
    if "failed" not in ALLOWED_TRANSITIONS.get(cur_status, set()):
        # 이미 terminal이면 그대로(이중 BLOCK 방지)
        if cur_status in TERMINAL:
            return cur_status
        raise IllegalTransition(f"{cur_status}에서 failed 불가")
    conn.execute("UPDATE publish_queue SET status='failed' WHERE queue_id=?", (queue_id,))
    conn.commit()
    return "failed"


# ── 검증기 결과 fail-closed 판정 ─────────────────────────────
def validation_passes(validate_result, evidence_file_exists):
    """검증기 미실행(None)·에러·빈입력·증거파일 없음 = 전부 False (BLOCK)."""
    if not evidence_file_exists:
        return False
    if validate_result is None:           # 미실행
        return False
    if not isinstance(validate_result, dict):
        return False
    if validate_result.get("error"):       # 에러/타임아웃
        return False
    if validate_result.get("count", 0) <= 0:  # 빈 입력
        return False
    return validate_result.get("ok") is True


# ── APPROVE 파싱 (안전토큰 금지 · full hash 묶음) ─────────────
_APPROVE_RE = re.compile(r"^APPROVE\s+(\S+)\s+([0-9a-f]{64})\s*$")


def parse_approve(text):
    """APPROVE <queue_id> <bundle_full_hash> 만 허용.
    안전토큰 경로 없음 — 등호로 묶은 토큰/플래그 형태는 reject."""
    if text is None:
        raise QueueError("빈 승인 입력")
    if "TOKEN" in text.upper() or "--token" in text.lower():
        raise QueueError("안전토큰 승인 금지 — APPROVE <queue_id> <bundle_full_hash>만 허용")
    m = _APPROVE_RE.match(text.strip())
    if not m:
        raise QueueError("형식 오류 — APPROVE <queue_id> <bundle_full_hash>(64 hex) 필요")
    return {"queue_id": m.group(1), "bundle_full_hash": m.group(2)}


def approve(conn, text, expected_bundle_hash, ts="t_appr"):
    """owner 명시 승인. candidate_ready 상태 + bundle_hash 일치 시에만 approved."""
    parsed = parse_approve(text)
    qid = parsed["queue_id"]
    if _status(conn, qid) != "candidate_ready":
        raise IllegalTransition("candidate_ready 아님 — 승인 거부")
    if parsed["bundle_full_hash"] != expected_bundle_hash:
        raise IllegalTransition("bundle_hash 불일치 — 승인 거부(변조 의심)")
    transition(conn, qid, "approved")
    conn.execute(
        "UPDATE publish_queue SET approved_by='owner_explicit', bundle_hash=?, approved_at=? "
        "WHERE queue_id=?", (expected_bundle_hash, ts, qid))
    conn.commit()
    return qid


if __name__ == "__main__":
    print("P1 module — run binggu_publish_queue_p1_selftest.py")
