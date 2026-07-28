# -*- coding: utf-8 -*-
"""출처 칸의 **부착축**은 evidence 다 (MF2.6) — system_provenance 는 증거로 계상되지 않는다.

스펙 §1:20-21 은 provenance(파서·파일경로·frontmatter 등 시스템 유래)를 "증거로 인정하지 않음"
으로 못박았다. 그래서 §1 증거 3요소(source_id · 위치 · excerpt_sha)를 `provenance` 라는 이름의
테이블에 넣으면, G7('증거 전건 위치 보유')을 검사하는 쪽이 evidence 실체를 훑을 때 여전히 0건이
나온다 — 문서상으로만 충족되는 무증상 결함이다.

여기서 못박는 것
  ① locator 행은 `evidence_id` 로 붙는다(노드가 아니라 증거)
  ② node → evidence 라우팅은 `edges.relation='evidence_supports'` 로 해결된다
  ③ 증거 축 coverage 는 evidence ⟕ evidence_locator 로만 산출되고,
     `system_provenance` 행을 아무리 넣어도 **분자·분모 어디에도 안 들어간다**
  ④ `system_provenance.evidence_eligible` 기본값은 0(증거 불인정)

⚠ 1단계 현재 저장소에는 `evidence_locator_coverage()` 함수가 아직 없다(설계상 Unit A 의
   `binggupack/evidence/locator.py` — 미구현). 그래서 이 파일은 그 함수가 지켜야 할 **집계 축**을
   실제 ledger 위에서 못박는다: 구현이 생기면 이 단언과 같은 값을 내야 한다.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import binggu_schema as bs  # noqa: E402
from openbinggu_deprecate_and_remind_g3 import open_g3  # noqa: E402

from binggupack.storage import save_paired  # noqa: E402

OWNER = "이 입찰은 마진이 낮아 보류하는 편이 낫다."
AI = "말씀대로 이번 건은 보류가 안전하다고 판단한다."
CTX_SOLO = {"actor": "human", "confirm": "PAIR owner:1"}
CTX_PAIR = {"actor": "human", "confirm": "PAIR ai_accepts owner:1 ai:1"}


def _ledger(tmp_path, name):
    home = tmp_path / name
    snap = home / "snapshots"
    snap.mkdir(parents=True, exist_ok=True)
    with bs.evloc_env(True):
        db = open_g3(str(home / "ledger.sqlite"))
    return db, str(snap)


def _evidence_ids_for_node(con, node_id):
    """설계 §1-1 의 node → evidence 라우팅(증빙 엣지 경유)."""
    return [r[0] for r in con.execute(
        "SELECT source FROM edges WHERE target=? AND relation='evidence_supports'"
        " AND state!='tombstoned'", (node_id,))]


def _locator_coverage(con):
    """증거 축 coverage — evidence 테이블만 훑는다(system_provenance 미참여)."""
    total = con.execute("SELECT count(*) FROM evidence").fetchone()[0]
    with_loc = con.execute(
        "SELECT count(*) FROM evidence e WHERE EXISTS("
        "  SELECT 1 FROM evidence_locator l WHERE l.evidence_id = e.evidence_id)").fetchone()[0]
    orphan_nodes = con.execute(
        "SELECT count(*) FROM nodes n WHERE NOT EXISTS("
        "  SELECT 1 FROM edges e WHERE e.target=n.node_id AND e.relation='evidence_supports')"
    ).fetchone()[0]
    return {"evidence_total": total, "with_locator": with_loc,
            "ratio": (with_loc / total) if total else 0.0,
            "no_evidence_nodes": orphan_nodes}


def test_locator_attaches_to_evidence_id_and_routes_from_node(tmp_path):
    db, snap = _ledger(tmp_path, "evloc_axis_home")
    try:
        with bs.evloc_env(True):
            r = save_paired(db, OWNER, AI, dict(CTX_PAIR), snap, relation_kind="ai_accepts")
        assert r["applied"] and r["saved"] == 2

        # ① 부착축 = evidence_id (nodes.node_id 가 아니다)
        loc = list(db.con.execute("SELECT evidence_id, excerpt_text FROM evidence_locator"))
        ev_ids = {r[0] for r in db.con.execute("SELECT evidence_id FROM evidence")}
        node_ids = {r[0] for r in db.con.execute("SELECT node_id FROM nodes")}
        assert len(loc) == len(ev_ids) == 2
        assert {e for e, _ in loc} == ev_ids
        assert {e for e, _ in loc} & node_ids == set()

        # ② node → evidence 라우팅이 증빙 엣지로 실제 해결된다
        for nid in node_ids:
            evs = _evidence_ids_for_node(db.con, nid)
            assert len(evs) == 1 and evs[0] in ev_ids
            got = db.con.execute(
                "SELECT excerpt_text FROM evidence_locator WHERE evidence_id=?", (evs[0],)
            ).fetchone()
            sent = db.con.execute("SELECT sentence FROM nodes WHERE node_id=?", (nid,)).fetchone()[0]
            assert got and got[0] == sent          # 그 노드의 좌표가 증거를 경유해 회수된다

        cov = _locator_coverage(db.con)
        assert cov["evidence_total"] == 2 and cov["with_locator"] == 2
        assert cov["ratio"] == 1.0                  # 앞막이 이후 신규 증거는 전건 보유
        assert cov["no_evidence_nodes"] == 0
    finally:
        db.close()


def test_system_provenance_is_never_counted_as_evidence(tmp_path):
    """③④ — 시스템 유래 정보를 아무리 넣어도 증거 축 수치가 흔들리지 않는다."""
    db, snap = _ledger(tmp_path, "evloc_sysprov_home")
    try:
        with bs.evloc_env(True):
            assert save_paired(db, OWNER, None, dict(CTX_SOLO), snap)["applied"]
        base = _locator_coverage(db.con)
        assert base["evidence_total"] == 1 and base["ratio"] == 1.0

        ev_id = db.con.execute("SELECT evidence_id FROM evidence").fetchone()[0]
        node_id = db.con.execute("SELECT node_id FROM nodes").fetchone()[0]
        # 같은 subject 를 가리키는 시스템 유래 행 3건(파서·파일경로·frontmatter)
        db.con.executemany(
            "INSERT INTO system_provenance(prov_id,subject_kind,subject_id,parser,file_path)"
            " VALUES(?,?,?,?,?)",
            [("P1", "evidence", ev_id, "md_parser", "seed/x.md"),
             ("P2", "node", node_id, "md_parser", "seed/x.md"),
             ("P3", "evidence", "EVC-NOT-REAL", "md_parser", "_archive/y.md")])
        db.con.commit()

        assert _locator_coverage(db.con) == base     # 분자·분모 어디에도 안 들어간다
        # ④ 기본값 = 증거 불인정
        elig = {r[0] for r in db.con.execute("SELECT evidence_eligible FROM system_provenance")}
        assert elig == {0}
        # 두 테이블은 구조적으로 분리돼 있다(같은 칸에 섞이지 않음)
        assert set(bs.table_columns(db.con, "evidence_locator")) & \
            set(bs.table_columns(db.con, "system_provenance")) == {"batch_id", "created_at"}
        # 다만 전용 무결성 축은 두 테이블을 모두 덮는다(행이 지워지면 잡힌다)
        before = bs.locator_checksum(db.con)
        db.con.execute("DELETE FROM system_provenance WHERE prov_id='P3'")
        db.con.commit()
        assert bs.locator_checksum(db.con) != before
    finally:
        db.close()


def test_node_without_grounding_edge_is_not_counted_as_satisfied(tmp_path):
    """증빙 엣지가 없는 노드는 '충족'이 아니라 **보류 버킷**으로 따로 보고돼야 한다."""
    db, snap = _ledger(tmp_path, "evloc_orphan_home")
    try:
        with bs.evloc_env(True):
            assert save_paired(db, OWNER, None, dict(CTX_SOLO), snap)["applied"]
        db.con.execute("INSERT INTO nodes(node_id,node_type,sentence,state)"
                       " VALUES('node:ORPHAN','judgment','증빙 엣지가 없는 노드','active')")
        db.con.commit()
        cov = _locator_coverage(db.con)
        assert cov["no_evidence_nodes"] == 1
        assert cov["evidence_total"] == 1 and cov["ratio"] == 1.0   # 충족률에 섞이지 않는다
    finally:
        db.close()


def test_locator_excerpt_is_frozen_full_text(tmp_path):
    """excerpt 는 동결 복사 — 절단 0 · sha 는 64hex 전체(설계 §1-1 산식)."""
    import hashlib
    db, snap = _ledger(tmp_path, "evloc_excerpt_home")
    try:
        with bs.evloc_env(True):
            assert save_paired(db, OWNER, None, dict(CTX_SOLO), snap)["applied"]
        row = db.con.execute(
            "SELECT excerpt_text, excerpt_sha, source_id, locator, container_sha,"
            " match_method, confidence, verified_by, batch_id FROM evidence_locator").fetchone()
        text, sha, src, loc, cont, method, conf, by, batch = row
        assert text == OWNER
        assert sha == hashlib.sha256(OWNER.encode("utf-8")).hexdigest() and len(sha) == 64
        assert src and loc and cont                 # 빈칸을 남기지 않는다(origin 미지정도 좌표 기록)
        # ★ D10: 등급은 하드코딩이 아니라 공용 GRADE 표로 산출한다.
        #   origin 미지정 = 독립 컨테이너가 없다(container_sha == excerpt_sha) → T2.
        #   앞막이가 세션 좌표를 실어 보낸 경우에만 T1.
        assert method == "live_capture" and by == "auto"
        assert conf == "T2" and cont == sha        # 자기참조 컨테이너 = 2차 등급
        assert batch and batch.startswith("save:")  # 롤백 단위가 붙어 있다
    finally:
        db.close()
