"""BingguPack PC-mediated read 공유 — P4: 실 ledger build 라벨 정정 (data_class 인자화·candidate/active 구분).

기준 커밋: 3a14ff6 (P3) 위.
owner 지시(2026-06-14 GO-P4, 업로드 GO 아님):
- data_class 인자화 + candidate/active 구분.
- 저장된 후보가 candidate면 real_candidate로만 빌드. active/confirmed/real_release 표시 금지.
- synthetic 하드코딩 제거 — fixture=synthetic_fixture / ledger candidate=real_candidate / active 확정분=real_active 분리.
- cloud upload·DB insert·tag/release·배포 실행 0. report-only/dry-run.
- 업로드는 검증 ZIP/hash/plan 보고 후 "이 ZIP 업로드 실행" 문구 전까지 HOLD.

ledger read-only(mode=ro). 실 ledger write 0.
설계: docs/BINGGUPACK_CROSSDEVICE_PUBLISH_PIPELINE_DESIGN.md §9
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_cloud_pack_export as EXP
import binggu_publish_p2_pipeline as P2
import binggu_publish_p3_real_ledger as P3

DEFAULT_LEDGER = P3.DEFAULT_LEDGER

# ledger node_type(영문 canonical) → build label_kind(한글 canonical 5종) 매핑
EN2KO = {"Claim": "판단", "Evidence": "증거", "Concept": "개념", "State": "상태", "Document": "문서"}


def _rows_to_build(rows):
    """ledger row → build_cloud_pack 입력. node_type(영문)→label_kind(한글) 매핑. evidence 1:1."""
    nodes, evidence = [], []
    for i, r in enumerate(rows):
        node_id, node_type, sentence = r[0], r[1], r[2]
        label_kind = EN2KO.get(node_type, node_type)  # 비매핑은 그대로 → G23이 잡음(fail-closed)
        ev_id = "EVC-real-%d" % i
        nodes.append({"id": node_id,
                      "properties": {"label_kind": label_kind, "sentence": sentence,
                                     "semantic_subtype": None},
                      "evidence_refs": [ev_id]})
        evidence.append({"id": ev_id, "text": sentence, "source": "real_ledger"})
    return nodes, evidence


def extract_by_state(ledger_path):
    """ledger read-only. candidate / active(confirmed) 분리 추출. (P3는 active만 — P4는 둘 다 구분)"""
    if not os.path.exists(ledger_path):
        return {"total": 0, "candidate_rows": [], "active_rows": []}
    conn = sqlite3.connect("file:%s?mode=ro" % ledger_path, uri=True)
    cur = conn.cursor()
    cur.execute("SELECT node_id,node_type,sentence,candidate,state,content_hash FROM nodes")
    nrows = cur.fetchall()
    conn.close()
    candidate_rows = [r for r in nrows if r[3]]
    active_rows = [r for r in nrows if not r[3] and (r[4] in (None, "active", "confirmed"))]
    return {"total": len(nrows), "candidate_rows": candidate_rows, "active_rows": active_rows}


def build_real_pack(ledger_path=DEFAULT_LEDGER, out_dir=None, db_path=None, state="candidate"):
    """ledger 상태별 build 라벨 정정 (dry-run).
    state='candidate' → data_class=real_candidate(release 자격 없음) / 'active' → real_active.
    데이터 0이면 NO_REAL_LEDGER_DATA BLOCK. cloud/DB/배포 0. report-only.
    """
    base = {"cloud_upload": False, "db_insert": False, "upload_executed": False,
            "report_only": True, "ledger": ledger_path, "requested_state": state}

    if not os.path.exists(ledger_path):
        return dict(base, status="BLOCK", reason="NO_REAL_LEDGER_DATA", detail="ledger 파일 부재")

    ext = extract_by_state(ledger_path)
    base["ledger_stats"] = {"total": ext["total"],
                            "candidate": len(ext["candidate_rows"]),
                            "active": len(ext["active_rows"])}

    if state == "candidate":
        rows, data_class = ext["candidate_rows"], "real_candidate"
    elif state == "active":
        rows, data_class = ext["active_rows"], "real_active"
    else:
        return dict(base, status="BLOCK", reason="BAD_STATE", detail="state must be candidate|active")

    if not rows:
        return dict(base, status="BLOCK", reason="NO_REAL_LEDGER_DATA",
                    detail="%s node 0 — 실데이터 꾸미지 않음(owner). SAVE/확정 후 재시도" % state)

    if out_dir is None or db_path is None:
        return dict(base, status="BLOCK", reason="NO_OUT_DIR",
                    detail="build하려면 out_dir/db_path 필요")
    P2.Q._assert_temp_path(db_path)

    nodes, evidence = _rows_to_build(rows)  # node_type(영문)→label_kind(한글) 매핑 포함
    g = EXP.build_graph_preview(nodes, evidence_items=evidence)
    conf = EXP.build_graph_confirm(g, approve=list(range(1, len(g["edges"]) + 1)))
    build = EXP.build_cloud_pack(out_dir, nodes, evidence, g, conf, data_class=data_class)

    # 검증 (P2 게이트 재사용)
    try:
        P2.validate_gate(build)
        P2.check_permanent_guards(out_dir, build)
    except P2.BlockError as e:
        return dict(base, status="BLOCK", reason="BUILD_VALIDATION_FAILED", detail=str(e),
                    data_class=data_class)

    man = build["manifest"]
    # owner 규약 검증: candidate는 release_ready 금지 (active/real_release 표시 금지)
    if data_class == "real_candidate" and man.get("release_ready") is True:
        return dict(base, status="BLOCK", reason="CANDIDATE_RELEASE_FORBIDDEN",
                    detail="real_candidate가 release_ready=true (active/real_release 표시 금지)")

    # ZIP + bundle hash (검증 ZIP/hash/plan 보고용 — 업로드 0)
    zip_path = os.path.join(os.path.dirname(out_dir), "p4_%s_candidate.zip" % data_class)
    EXP.make_zip(out_dir, zip_path)
    bundle_hash = P2._sha256_file(zip_path)
    plans = P2.build_deploy_plan(man, bundle_hash)

    return dict(base, status="DRYRUN_OK", data_class=data_class,
                release_ready=man.get("release_ready"), release_status=man.get("release_status"),
                degraded_reasons=build["quality"].get("degraded_reasons"),
                counts=build.get("counts"),
                bundle_hash=bundle_hash, zip_path=zip_path, deploy=plans,
                note="라벨 정정 dry-run. data_class=%s. ZIP/hash/plan 보고만 — "
                     "실 업로드는 ' 이 ZIP 업로드 실행' 문구 전까지 HOLD." % data_class)


if __name__ == "__main__":
    import json
    print(json.dumps(build_real_pack(), ensure_ascii=False, indent=2, default=str))
