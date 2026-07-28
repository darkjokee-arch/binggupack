# -*- coding: utf-8 -*-
"""무손실 증명축 회귀 (MF1.2 · NEW2.8) — `store_checksum before==after` 를 증명으로 쓰지 마라.

MF1.2: store_checksum 의 projection 에 `nodes.use_count` 가 들어 있는데, 그 컬럼은
`binggupack/pack/p1_ranking.py` 의 `record_use()` 가 **audit 밖에서** UPDATE 한다.
그래서 운영 ledger 의 `verify_tail_state()` 는 이번 변경 착수 전부터 이미 False 다.
살아있는 장부에서 회상 도장 1건만 찍혀도 체크섬이 흔들리므로, 그걸 '무손실 게이트'로 쓰면
오경보 → "이 체크섬은 원래 안 맞아, 무시" → 진짜 손실이 안 보이는 상태로 굳는다.

NEW2.8: 그렇다고 `state` 까지 휘발로 빼면 tombstone/상태 파괴가 무검출이 된다(검출력 축소).
→ probe 는 2층이어야 한다: core_sha(use_count 만 제외 · state 포함) / mutable_sha(둘 다 제외).

스티뮬러스는 전부 **운영 코드**를 태운다: record_use() · tombstone() · save_paired().
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import binggu_schema as bs  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import open_g3  # noqa: E402
from openbinggu_staging_write_selftest import tombstone  # noqa: E402

from binggupack.pack.p1_ranking import record_use  # noqa: E402
from binggupack.storage import save_paired  # noqa: E402

OWNER = "이 입찰은 마진이 낮아 보류하는 편이 낫다."
OWNER2 = "다음에는 이 거래처를 우선 검토하는 것이 낫겠다."
CTX = {"actor": "human", "confirm": "PAIR owner:1"}


def _ledger(tmp_path, name):
    home = tmp_path / name
    snap = home / "snapshots"
    snap.mkdir(parents=True, exist_ok=True)
    with bs.evloc_env(False):
        db = open_g3(str(home / "ledger.sqlite"))
    return db, str(snap)


def _seed(db, snap, texts=(OWNER, OWNER2)):
    with bs.evloc_env(False):
        for t in texts:
            r = save_paired(db, t, None, dict(CTX), snap)
            assert r["applied"], r
    return [r[0] for r in db.con.execute("SELECT node_id FROM nodes")]


def test_probe_is_stable_under_audit_free_use_count_update(tmp_path):
    """MF1.2 본체 — record_use(audit 밖)는 무결성 위반이 아니다. store_checksum 은 흔들린다."""
    db, snap = _ledger(tmp_path, "probe_usecount_home")
    try:
        node_ids = _seed(db, snap)
        before = bs.integrity_probe(db.con)
        ck_before = db.store_checksum()

        assert record_use(db, node_ids[0]) == 1        # 운영 회상 도장 경로

        assert bs.integrity_probe(db.con) == before, "use_count 변화가 무결성 위반으로 오탐됐다"
        assert db.store_checksum() != ck_before, (
            "store_checksum 이 use_count 에 흔들리지 않는다면 MF1.2 전제가 바뀐 것")
        # 그래서 tail 대조는 원리적으로 False 가 된다(이번 변경 탓이 아님을 못박는다)
        assert db.verify_chain() is True
        assert db.verify_tail_state() is False
    finally:
        db.close()


def test_probe_detects_row_deletion(tmp_path):
    db, snap = _ledger(tmp_path, "probe_delete_home")
    try:
        node_ids = _seed(db, snap)
        before = bs.integrity_probe(db.con)
        db.con.execute("DELETE FROM nodes WHERE node_id=?", (node_ids[0],))
        db.con.commit()
        after = bs.integrity_probe(db.con)
        assert after != before
        assert after["counts"]["nodes"] == before["counts"]["nodes"] - 1
        assert after["pk_sha"]["nodes"] != before["pk_sha"]["nodes"]
        assert after["core_sha"]["nodes"] != before["core_sha"]["nodes"]
    finally:
        db.close()


def test_state_change_is_caught_by_core_sha_only(tmp_path):
    """NEW2.8 — tombstone(state 전이)은 core_sha 로 검출되고, mutable_sha 에서만 제외된다."""
    db, snap = _ledger(tmp_path, "probe_state_home")
    try:
        node_ids = _seed(db, snap)
        before = bs.integrity_probe(db.con)
        r = tombstone(db, node_ids[0], {"actor": "human"}, snap)
        assert r["state"] == "tombstoned" and r["physical_present"] is True

        after = bs.integrity_probe(db.con)
        assert after["core_sha"]["nodes"] != before["core_sha"]["nodes"], (
            "state 파괴가 무검출이면 회상·preview 에서 노드가 사라져도 게이트가 '무손실'이라 보고한다")
        assert after["mutable_sha"]["nodes"] == before["mutable_sha"]["nodes"]
        # 물리 보존(tombstone 은 삭제가 아니다) — audit_log 는 이 전이를 설명하는 행이 늘어난다
        assert after["counts"]["nodes"] == before["counts"]["nodes"]
        assert after["pk_sha"]["nodes"] == before["pk_sha"]["nodes"]
        assert after["counts"]["audit_log"] == before["counts"]["audit_log"] + 1
    finally:
        db.close()


def test_probe_default_target_excludes_evloc_tables(tmp_path):
    """probe 기본 대상은 v4 정본 테이블 — evloc 축은 플래그를 가로질러 값이 흔들리면 안 된다."""
    db_off, snap_off = _ledger(tmp_path, "probe_axis_off_home")
    home_on = tmp_path / "probe_axis_on_home"
    (home_on / "snapshots").mkdir(parents=True, exist_ok=True)
    with bs.evloc_env(True):
        db_on = open_g3(str(home_on / "ledger.sqlite"))
    try:
        p_off = bs.integrity_probe(db_off.con)
        p_on = bs.integrity_probe(db_on.con)
        assert set(bs.EVLOC_TABLES) & set(p_off["tables"]) == set()
        assert set(bs.EVLOC_TABLES) & set(p_on["tables"]) == set()
        assert p_off["tables"] == p_on["tables"]

        # evloc 행을 넣어도 probe 는 불변 — 그 축은 locator_checksum 이 담당한다
        base = bs.integrity_probe(db_on.con)
        lc_base = bs.locator_checksum(db_on.con)
        db_on.con.execute("INSERT INTO evidence_locator(loc_id,evidence_id,excerpt_text)"
                          " VALUES('L9','EVC-9','발췌')")
        db_on.con.commit()
        assert bs.integrity_probe(db_on.con) == base
        assert bs.locator_checksum(db_on.con) != lc_base
    finally:
        db_off.close()
        db_on.close()


def test_probe_is_insensitive_to_column_order_and_vacuum(tmp_path):
    """행 순서·물리 재배치(VACUUM)에 불변 — 게이트가 '노이즈로 흔들리는' 상태를 만들지 않는다."""
    db, snap = _ledger(tmp_path, "probe_vacuum_home")
    try:
        _seed(db, snap)
        before = bs.integrity_probe(db.con)
        db.con.commit()
        db.con.execute("VACUUM")
        assert bs.integrity_probe(db.con) == before
    finally:
        db.close()
