# -*- coding: utf-8 -*-
"""앞막이(evidence_locator) 저장 경로 회귀 — 플래그 OFF 홈에서 저장이 절대 안 깨진다 (NEW2.5).

NEW2.5 시나리오 B 가 이 파일의 존재 이유다:
  플래그가 프로세스 env 라 hook·MCP·CLI 가 서로 다른 값을 볼 수 있는데, 테이블이 없는 ledger 에서
  `INSERT INTO evidence_locator` 가 **열린 트랜잭션 안**에서 터지면 staging_apply 가 통째로 롤백돼
  owner 발화가 사라진다. 그래서
    ① 테이블 부재 홈 → 저장은 applied True · locator 0 · **사유 반환**(table_absent) · mirror jsonl 보존
    ② write 여부 판정은 env 가 아니라 ledger 실재(has_table) — 저장 시점에 env 를 꺼도 결론 동일
    ③ locator 적재가 audit tail(verify_tail_state)을 점유하지 않는다(NEW2.7)

운영 코드가 실제 부르는 진입점만 태운다: binggupack.storage.save_paired
  → staging_apply → apply_pack_in_txn(loc_rows) → insert_locators
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import binggu_schema as bs  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import open_g3  # noqa: E402
from openbinggu_staging_write_selftest import evloc_mirror_path  # noqa: E402

from binggupack.storage import save_paired  # noqa: E402

OWNER = "이 입찰은 마진이 낮아 보류하는 편이 낫다."
OWNER2 = "다음에는 이 거래처를 우선 검토하는 것이 낫겠다."
CTX = {"actor": "human", "confirm": "PAIR owner:1"}


def _open_ledger(tmp_path, name, evloc_on):
    """ledger 를 지정 플래그로 **생성**한다. 이후 저장 시점의 env 와는 독립이어야 한다."""
    home = tmp_path / name
    snap = home / "snapshots"
    snap.mkdir(parents=True, exist_ok=True)
    with bs.evloc_env(evloc_on):
        db = open_g3(str(home / "ledger.sqlite"))
    return db, str(snap)


def _mirror_rows(db):
    path = evloc_mirror_path(db.path)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


# ── ① 테이블 부재 홈(현 운영 기본값) ────────────────────────────────────────
def test_save_succeeds_with_locator_table_absent(tmp_path):
    db, snap = _open_ledger(tmp_path, "evloc_absent_home", evloc_on=False)
    try:
        assert bs.has_table(db.con, "evidence_locator") is False

        with bs.evloc_env(False):
            r = save_paired(db, OWNER, None, dict(CTX), snap)

        assert r["applied"] is True and r["saved"] == 1, r
        # 노드/증거는 정상 적재 — locator 부재가 저장을 막지 않는다
        assert db.con.execute("SELECT count(*) FROM nodes").fetchone()[0] == 1
        assert db.con.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1
        # locator 는 0 이되 **사유가 반환된다**(best-effort ≠ 침묵)
        rep = r["locator"]
        assert rep["skipped"] is True and rep["reason"] == "table_absent"
        assert rep["inserted"] == 0 and rep["error"] is None
        # 테이블을 몰래 만들지도 않는다
        assert bs.has_table(db.con, "evidence_locator") is False
        # ★ D2/D13: 기능 자체가 꺼진 상태(table_absent)에서는 미러도 쓰지 않는다.
        #   "OFF 면 기존 동작 불변" 이 상위 규약이라, 꺼둔 기능의 산출물이 운영홈에
        #   쌓이면 안 된다(excerpt 평문·TTL/회전 없음). 이중 보관은 '테이블은 있는데
        #   INSERT 가 실패/폐기된' 경우에만 의미가 있다.
        assert _mirror_rows(db) == []
        assert rep["mirrored"] == 0 and rep.get("mirror_skipped") == "table_absent"
    finally:
        db.close()


def test_mirror_jsonl_lands_in_ledger_home_not_operating_home(tmp_path):
    """미러 경로는 `~/.binggupack` 리터럴이 아니라 **ledger 와 같은 디렉터리**(NEW2.9)."""
    db, snap = _open_ledger(tmp_path, "evloc_mirrorhome", evloc_on=False)
    try:
        with bs.evloc_env(False):
            save_paired(db, OWNER, None, dict(CTX), snap)
        mirror = evloc_mirror_path(db.path)
        assert os.path.dirname(os.path.abspath(mirror)) == os.path.dirname(os.path.abspath(db.path))
        assert str(tmp_path) in os.path.abspath(mirror)
    finally:
        db.close()


# ── ② env 독립: ledger 실재가 유일한 판정축 ────────────────────────────────
def test_locator_write_follows_ledger_not_env(tmp_path):
    """테이블이 있는 ledger 면 저장 프로세스의 env 가 꺼져 있어도 locator 가 적재된다."""
    db, snap = _open_ledger(tmp_path, "evloc_present_home", evloc_on=True)
    try:
        assert bs.has_table(db.con, "evidence_locator") is True

        with bs.evloc_env(False):                      # ★ 저장 시점 env = OFF
            assert bs.evloc_enabled() is False
            r = save_paired(db, OWNER, None, dict(CTX), snap)

        assert r["applied"] is True
        rep = r["locator"]
        assert rep["skipped"] is False and rep["reason"] is None
        assert rep["inserted"] == 1 and rep["present"] == 1
        n_loc = db.con.execute("SELECT count(*) FROM evidence_locator").fetchone()[0]
        n_ev = db.con.execute("SELECT count(*) FROM evidence").fetchone()[0]
        assert n_loc == n_ev == 1
    finally:
        db.close()


def test_locator_rows_are_idempotent_on_replay(tmp_path):
    """같은 (evidence_id, source_id, locator, excerpt_sha) 재적재 → UNIQUE 로 1행 유지."""
    db, snap = _open_ledger(tmp_path, "evloc_idem_home", evloc_on=True)
    try:
        with bs.evloc_env(True):
            r1 = save_paired(db, OWNER, None, dict(CTX), snap)
        assert r1["applied"]
        rows = list(db.con.execute("SELECT loc_id, excerpt_sha FROM evidence_locator"))
        # 운영 경로가 만든 그 행을 그대로 다시 넣어본다(정본 적재 함수 사용)
        from openbinggu_staging_write_selftest import insert_locators, loc_row
        ev_id, exc = db.con.execute(
            "SELECT evidence_id, excerpt_text FROM evidence_locator").fetchone()
        src, loc = db.con.execute(
            "SELECT source_id, locator FROM evidence_locator").fetchone()
        again = loc_row(ev_id, exc, source_id=src, locator=loc)
        rep = insert_locators(db.con, [again], db_path=db.path)
        assert rep["inserted"] == 0 and rep["present"] == 1 and rep["reason"] is None
        assert list(db.con.execute("SELECT loc_id, excerpt_sha FROM evidence_locator")) == rows
    finally:
        db.close()


# ── ③ NEW2.7: locator 앵커가 audit tail 을 점유하지 않는다 ──────────────────
def test_locator_apply_does_not_break_audit_tail(tmp_path):
    db, snap = _open_ledger(tmp_path, "evloc_audit_home", evloc_on=True)
    try:
        from openbinggu_staging_write_selftest import verify_locator_tail
        with bs.evloc_env(True):
            assert save_paired(db, OWNER, None, dict(CTX), snap)["applied"]
            assert save_paired(db, OWNER2, None, dict(CTX), snap)["applied"]
        assert db.verify_chain() is True
        assert db.verify_tail_state() is True          # locator 해시가 tail 을 점유하면 False
        assert verify_locator_tail(db.con) is True
        # 앵커는 audit_log 가 아니라 audit_meta 별도 키에 산다
        last_action = db.con.execute(
            "SELECT action FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()[0]
        assert last_action != "evidence_locator_apply"
        head = db.con.execute(
            "SELECT value FROM audit_meta WHERE key='evloc_head'").fetchone()
        assert head and head[0] == bs.locator_checksum(db.con)
    finally:
        db.close()


def test_locator_tail_detects_row_deletion(tmp_path):
    """locator 행이 지워지면 전용 축이 그것을 잡는다(MF1.3 — 별도 테이블 = 무결성 0 상쇄)."""
    db, snap = _open_ledger(tmp_path, "evloc_tamper_home", evloc_on=True)
    try:
        from openbinggu_staging_write_selftest import verify_locator_tail
        with bs.evloc_env(True):
            assert save_paired(db, OWNER, None, dict(CTX), snap)["applied"]
        assert verify_locator_tail(db.con) is True
        db.con.execute("DELETE FROM evidence_locator")
        db.con.commit()
        assert verify_locator_tail(db.con) is False
        # 기존 audit 체인은 이 변화를 전혀 못 본다 = 전용 축이 필요한 이유
        assert db.verify_chain() is True
    finally:
        db.close()
