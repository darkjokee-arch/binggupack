# -*- coding: utf-8 -*-
"""excerpt 원문은 pack dict 에 **타입상 들어갈 자리가 없다** (MF2.7).

MF2.7: pack 안에 회수 원문을 실으면 유출 차단이 '구조'가 아니라 '규율'이 된다 —
pack 객체는 pack_id 산정·audit·스냅샷·export 경로가 전부 공유하므로, 나중에 누군가
`for k, v in pack.items()` 로 순회하는 순간 조용히 새 나간다.
→ locator 행은 `apply_pack_in_txn(db, pack, now, loc_rows=None)` 의 **별도 인자**로 간다.

여기서 못박는 것
  ① 준비 단계(prepare_selected)가 loc_rows 를 pack 밖으로 반환한다
  ② pack dict 키는 화이트리스트 {pack_id, content, nodes, edges, evidence} 외 0건
     (fixture 문자열 매칭이 아니라 **키 집합**으로 — 새 키가 섞이면 바로 걸린다)
  ③ pack 을 통째로 JSON 직렬화해도 excerpt 전용 키가 안 나온다
  ④ `_collect_source_pointers` 가 스캔하는 키(source_path/source_ref/path)를 locator 가 쓰지 않는다
  ⑤ 구 3인자 호출(`apply_pack_in_txn(db, pack, now)`)은 무영향(기본 None)
  ⑥ 저장 후 nodes/edges/evidence 테이블에 locator 컬럼이 생기지 않는다
"""
import inspect
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import binggu_schema as bs  # noqa: E402
import openbinggu_conversation_candidate_save as cs  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import open_g3  # noqa: E402
from openbinggu_staging_write_selftest import apply_pack_in_txn  # noqa: E402

from binggupack.storage import save_paired  # noqa: E402

PACK_KEYS = {"pack_id", "content", "nodes", "edges", "evidence"}
SOURCE_POINTER_KEYS = {"source_path", "source_ref", "path"}
OWNER = "이 입찰은 마진이 낮아 보류하는 편이 낫다."
CTX = {"actor": "human", "confirm": "PAIR owner:1"}


def _ledger(tmp_path, name, evloc_on=True):
    home = tmp_path / name
    snap = home / "snapshots"
    snap.mkdir(parents=True, exist_ok=True)
    with bs.evloc_env(evloc_on):
        db = open_g3(str(home / "ledger.sqlite"))
    return db, str(snap)


def test_prepare_selected_returns_loc_rows_outside_the_pack(tmp_path):
    db, _snap = _ledger(tmp_path, "pack_prepare_home")
    try:
        pr = cs.prepare_selected(db, OWNER, [1], speaker="owner", explicit=True,
                                 origin={"session_id": "S-PACK", "turn_uuid": "turn-1"})
        assert pr["ok"] is True
        pack = pr["pack"]

        # ① loc_rows 는 반환 dict 의 형제 키(= pack 밖)
        assert pr["loc_rows"] and len(pr["loc_rows"]) == len(pack["evidence"])
        assert pr["loc_rows"][0]["excerpt_text"] == OWNER

        # ② pack 키 화이트리스트
        assert set(pack) == PACK_KEYS

        # ③ 직렬화해도 excerpt 전용 키가 없다
        blob = json.dumps(pack, ensure_ascii=False)
        for k in ("excerpt_text", "excerpt_sha", "loc_id", "locator", "container_sha",
                  "source_id", "loc_rows"):
            assert '"%s"' % k not in blob, "pack 에 %s 가 실렸다" % k

        # ④ publish 스캐너가 보는 키를 locator 가 쓰지 않는다
        assert set(pr["loc_rows"][0]) & SOURCE_POINTER_KEYS == set()
        # 좌표는 origin 대로 실렸다(빈칸 아님)
        assert pr["loc_rows"][0]["source_id"] == "session:S-PACK"
        assert pr["loc_rows"][0]["locator"] == "uuid:turn-1"
    finally:
        db.close()


def test_apply_pack_in_txn_signature_is_backward_compatible():
    """⑤ loc_rows/loc_report 는 기본 None — 기존 3인자 호출이 그대로 산다."""
    sig = inspect.signature(apply_pack_in_txn)
    params = list(sig.parameters)
    assert params[:3] == ["db", "pack", "now_iso"]
    assert sig.parameters["loc_rows"].default is None
    assert sig.parameters["loc_report"].default is None


def test_legacy_three_arg_call_still_applies(tmp_path):
    """구 호출자(binggu_hosted_bundle 등) 경로 — locator 없이도 pack 이 그대로 적재된다."""
    db, _snap = _ledger(tmp_path, "pack_legacy_home")
    try:
        pr = cs.prepare_selected(db, OWNER, [1], speaker="owner", explicit=True)
        pack = pr["pack"]
        db.con.execute("BEGIN")
        ch = apply_pack_in_txn(db, pack, "2026-07-27T00:00:00Z")     # ★ 3인자
        db.con.execute("COMMIT")
        assert ch
        assert db.con.execute("SELECT count(*) FROM nodes").fetchone()[0] == len(pack["nodes"])
        assert db.con.execute("SELECT count(*) FROM evidence_locator").fetchone()[0] == 0
    finally:
        db.close()


def test_saved_tables_have_no_locator_columns(tmp_path):
    """⑥ 저장 후에도 nodes/edges/evidence 스키마는 v4 정본 그대로 — excerpt 는 별도 테이블에만."""
    db, snap = _ledger(tmp_path, "pack_columns_home")
    try:
        with bs.evloc_env(True):
            assert save_paired(db, OWNER, None, dict(CTX), snap)["applied"]
        for table in ("nodes", "edges", "evidence"):
            cols = set(bs.table_columns(db.con, table))
            assert cols == {d.split()[0] for d in bs._TABLE_COLUMNS[table]}
            assert cols & {"excerpt_text", "excerpt_sha", "locator", "container_sha"} == set()
        # 원문 발췌는 evidence_locator 에만 있다
        assert db.con.execute(
            "SELECT count(*) FROM evidence_locator WHERE excerpt_text=?", (OWNER,)
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_save_paired_pack_shape_is_unchanged_with_locator_on(tmp_path):
    """앞막이 ON/OFF 어느 쪽이든 pack 모양(키 집합·pack_id 산정 축)이 같다."""
    ids = {}
    for name, on in (("pack_shape_off_home", False), ("pack_shape_on_home", True)):
        db, snap = _ledger(tmp_path, name, evloc_on=on)
        try:
            captured = {}
            orig = cs.staging_apply

            def _spy(db_, pack, ctx, snap_dir, ts=None, loc_rows=None, _o=orig, _c=captured):
                _c["keys"] = set(pack)
                _c["pack_id"] = pack["pack_id"]
                _c["content"] = pack["content"]
                _c["loc_rows"] = loc_rows
                return _o(db_, pack, ctx, snap_dir, ts=ts, loc_rows=loc_rows)

            cs.staging_apply = _spy
            try:
                with bs.evloc_env(on):
                    r = save_paired(db, OWNER, None, dict(CTX), snap)
            finally:
                cs.staging_apply = orig
            assert r["applied"]
            assert captured["keys"] == PACK_KEYS
            assert captured["loc_rows"] and captured["loc_rows"][0]["excerpt_text"] == OWNER
            ids[name] = (captured["pack_id"], captured["content"])
        finally:
            db.close()
    # pack_id/content 는 노드 문장으로만 산정 — locator 유무가 dedup 축을 흔들지 않는다
    assert ids["pack_shape_off_home"] == ids["pack_shape_on_home"]
