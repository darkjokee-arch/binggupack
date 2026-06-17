"""BingguPack PC-mediated read 공유 — P3: 실 ledger 데이터 fixture로 build 단계 재검증 (report-only/dry-run).

기준 커밋: 2410272 (P2) 위.
owner 지시(2026-06-14 GO-P3, 업로드 GO 아님):
- 범위: 실 ledger 데이터 fixture로 build 단계 재검증.
- ledger 0개면 실데이터를 꾸미지 말고 NO_REAL_LEDGER_DATA로 BLOCK 보고.
- owner가 아직 SAVE n 안 한 후보(candidate)는 실 ledger 데이터로 취급 금지.
- cloud upload / DB insert / tag·release / 배포 실행 0.
- report-only / dry-run.

ledger read-only(mode=ro)로만 접근. 실 ledger write 0.
설계: docs/BINGGUPACK_CROSSDEVICE_PUBLISH_PIPELINE_DESIGN.md §9
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_cloud_pack_export as EXP
import binggu_publish_p2_pipeline as P2
import binggu_platform as _plat

# cross-platform: BINGGU_HOME 우선 · 없으면 OS별 홈/.binggupack (Windows 동작 보존).
DEFAULT_LEDGER = _plat.default_ledger()


def extract_real_ledger(ledger_path):
    """ledger read-only. active(non-candidate) node = SAVE 확정분만. candidate(미SAVE)는 실데이터 취급 금지."""
    conn = sqlite3.connect("file:%s?mode=ro" % ledger_path, uri=True)
    cur = conn.cursor()
    cur.execute("SELECT node_id,node_type,sentence,candidate,state,content_hash FROM nodes")
    nrows = cur.fetchall()
    cur.execute("SELECT evidence_id,sentence,source_pointer_id,source_hash FROM evidence")
    erows = cur.fetchall()
    # edges (구버전 ledger 에 테이블 부재 가능 — 방어)
    try:
        cur.execute("SELECT edge_id,relation,source,target,candidate,state,evidence_refs FROM edges")
        edge_rows = cur.fetchall()
    except sqlite3.OperationalError:
        edge_rows = []
    conn.close()
    # candidate=falsy(0/None) && state confirmed/active 만 = SAVE 확정분 (미SAVE 후보 제외)
    active = [r for r in nrows if not r[3] and (r[4] in (None, "active", "confirmed"))]
    active_edges = [r for r in edge_rows if not r[4] and (r[5] in (None, "active", "confirmed"))]
    return {
        "total_nodes": len(nrows),
        "candidate_nodes": sum(1 for r in nrows if r[3]),
        "active_nodes": len(active),
        "evidence_count": len(erows),
        "active_rows": active,
        "evidence_rows": erows,
        "active_edge_rows": active_edges,
        "active_edges": len(active_edges),
    }


def _to_build_inputs(ext):
    """ledger active row → build_cloud_pack 입력 형식. best-effort 1:1 evidence 매핑.
    ⚠ 실 ledger 0개라 미검증 — 실 node-evidence 연결 매핑 확정은 P4(데이터 형태 확인 후)."""
    nodes, evidence = [], []
    for i, r in enumerate(ext["active_rows"]):
        node_id, node_type, sentence = r[0], r[1], r[2]
        ev_id = "EVC-real-%d" % i
        nodes.append({"id": node_id,
                      "properties": {"label_kind": node_type, "sentence": sentence,
                                     "semantic_subtype": None},
                      "evidence_refs": [ev_id]})
        evidence.append({"id": ev_id, "text": sentence, "source": "real_ledger"})
    return nodes, evidence


def run_p3(ledger_path=DEFAULT_LEDGER, out_dir=None, db_path=None):
    """실 ledger → build 단계 재검증 (dry-run). 0개면 NO_REAL_LEDGER_DATA BLOCK. cloud/DB 0."""
    base = {"cloud_upload": False, "db_insert": False, "upload_executed": False,
            "report_only": True, "ledger": ledger_path}

    if not os.path.exists(ledger_path):
        return dict(base, status="BLOCK", reason="NO_REAL_LEDGER_DATA",
                    detail="ledger 파일 부재")

    ext = extract_real_ledger(ledger_path)
    base["ledger_stats"] = {k: ext[k] for k in
                            ("total_nodes", "candidate_nodes", "active_nodes", "evidence_count")}

    if ext["active_nodes"] == 0:
        return dict(base, status="BLOCK", reason="NO_REAL_LEDGER_DATA",
                    detail="active(SAVE 확정) node 0 — 실데이터 꾸미지 않음(owner). "
                           "candidate=%d 는 실데이터 취급 금지. SAVE n 후 재시도"
                           % ext["candidate_nodes"])

    # ── 실 데이터 존재: build 단계 재검증 (dry-run) ──
    if out_dir is None or db_path is None:
        return dict(base, status="BLOCK", reason="NO_OUT_DIR",
                    detail="실데이터 있음 — build 재검증하려면 out_dir/db_path 필요")
    P2.Q._assert_temp_path(db_path)
    nodes, evidence = _to_build_inputs(ext)
    g = EXP.build_graph_preview(nodes, evidence_items=evidence)
    conf = EXP.build_graph_confirm(g, approve=list(range(1, len(g["edges"]) + 1)))
    build = EXP.build_cloud_pack(out_dir, nodes, evidence, g, conf)
    try:
        P2.validate_gate(build)
        P2.check_permanent_guards(out_dir, build)
    except P2.BlockError as e:
        return dict(base, status="BLOCK", reason="BUILD_VALIDATION_FAILED", detail=str(e),
                    source="real_ledger")
    return dict(base, status="DRYRUN_OK", source="real_ledger",
                counts=build.get("counts"),
                release_status=build["manifest"].get("release_status"),
                note="실 ledger build 재검증 통과(dry-run). ZIP/배포/업로드 0. "
                     "⚠ build_cloud_pack은 data_class=synthetic_fixture 하드코딩 — "
                     "실데이터 정직 라벨링은 P4 과제")


if __name__ == "__main__":
    import json
    print(json.dumps(run_p3(), ensure_ascii=False, indent=2))
