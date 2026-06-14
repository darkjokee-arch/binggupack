"""BingguPack PC-mediated read 공유 — P2: 실 빌더·검증기 연결 + 영구금지 hard fail + 배포 plan(실행 0).

기준 커밋: 7d1b875 (P1) 위.
owner 지시(2026-06-14 GO-P2, 업로드 GO 아님):
- 실제 빌더(binggu_cloud_pack_export.build_cloud_pack)·검증기 연결.
- 영구금지 22~27 builder hard fail.
- 배포 전 rollback/live 확인 절차 구현 — 단 dry-run / deploy plan / rollback plan / live-check command 준비까지만.
- 금지: cloud upload 0 / DB insert 0 / tag·release 0 / capture_enabled 재활성 0.
- BLOCK: 검증기 실패·미실행·증거 누락·hash 불일치·synthetic release_ready=true.
- 실제 업로드는 별도 문구 "이 ZIP 업로드 실행" 전까지 HOLD.

설계: docs/BINGGUPACK_CROSSDEVICE_PUBLISH_PIPELINE_DESIGN.md §8/§9
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_publish_queue_p1 as Q
import binggu_cloud_pack_export as EXP

# canonical 존재론 5종 (label_kind) — subtype은 이 집합으로 승격 불가(영구금지 23)
CANONICAL_LABEL_KINDS = {"문서", "증거", "개념", "상태", "판단"}
CANONICAL_NODE_TYPES = {"Claim", "Evidence", "Concept", "State", "Document"}
SUPPORTS = "supports_judgment"


class BlockError(Exception):
    """검증 BLOCK — fail-closed."""


def _read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 영구금지 22~27 hard fail (builder 산출물 대상) ────────────
def check_permanent_guards(out_dir, build_result):
    """영구금지 22~27을 builder 산출물에서 hard fail 검사. 위반 1건이라도 BLOCK."""
    if build_result is None or not isinstance(build_result, dict):
        raise BlockError("검증기 미실행 — build_result 없음")

    manifest = build_result.get("manifest")
    quality = build_result.get("quality")
    if manifest is None or quality is None:
        raise BlockError("검증기 미실행 — manifest/quality 누락")

    nodes = _read_jsonl(os.path.join(out_dir, "graph", "nodes.jsonl"))
    edges = _read_jsonl(os.path.join(out_dir, "graph", "edges.jsonl"))
    graphrag = _read_json(os.path.join(out_dir, "reports", "graphrag.json"))

    violations = []

    # 22: node-to-node verb = supports_judgment만 + 신규 predicate 0
    if graphrag.get("new_predicates", 0) != 0:
        violations.append("G22: new_predicates != 0")
    for e in edges:
        if e.get("edge_kind") == "verb":
            if e.get("relation") != SUPPORTS:
                violations.append("G22: verb edge relation != supports_judgment (%s)" % e.get("relation"))
            # 27: evidence 실연결 — verb edge에 evidence_refs 필수
            if not e.get("evidence_refs"):
                violations.append("G27: verb edge evidence 미연결 %s->%s"
                                  % (e.get("from"), e.get("to")))

    # 23: subtype canonical 승격 금지 — label_kind는 canonical 5종, subtype은 보조필드만
    for n in nodes:
        lk = n.get("label_kind")
        nt = n.get("node_type")
        sub = n.get("semantic_subtype")
        if lk is not None and lk not in CANONICAL_LABEL_KINDS:
            violations.append("G23: label_kind 비-canonical (%s)" % lk)
        if nt is not None and nt not in CANONICAL_NODE_TYPES:
            violations.append("G23: node_type 비-canonical (%s)" % nt)
        if sub is not None and (sub == lk or sub == nt):
            violations.append("G23: subtype이 canonical(label_kind/node_type)로 승격 (%s)" % sub)

    # 24: synthetic 실팩표시 금지 — synthetic_fixture인데 release_ready=true면 BLOCK
    if manifest.get("data_class") == "synthetic_fixture" and manifest.get("release_ready") is True:
        violations.append("G24: synthetic_fixture가 release_ready=true (실팩 표시 금지)")

    # 26: cos를 capture/저장/승인 결정에 사용 금지 — 산출물에 cos 결정필드 부재 확인(구조적 0)
    for e in edges:
        if "cosine" in e or "cos_score" in e:
            violations.append("G26: edge에 cos 결정필드 노출")

    # 27(보강): content node evidence_refs 누락 — Claim 노드는 evidence_refs 필수
    for n in nodes:
        if n.get("node_type") == "Claim" and not n.get("evidence_refs"):
            violations.append("G27: Claim node evidence 미연결 (%s)" % n.get("id"))

    if violations:
        raise BlockError("영구금지 위반: " + " | ".join(violations))
    return {"guards": "PASS", "checked": ["G22", "G23", "G24", "G26", "G27"]}


# ── 검증 게이트 (leak·required_failures·release 정직) ────────
def validate_gate(build_result):
    """검증기 실패·미실행·증거누락 → BLOCK (fail-closed)."""
    if build_result is None:
        raise BlockError("검증기 미실행")
    quality = build_result.get("quality")
    if not isinstance(quality, dict):
        raise BlockError("검증기 결과 없음")
    if quality.get("leak_count", 1) != 0:
        raise BlockError("leak_count != 0 (시크릿/PII)")
    rf = quality.get("required_failures")
    if rf is None:
        raise BlockError("required_failures 미산출 — 검증기 미실행")
    if len(rf) != 0:
        raise BlockError("required_failures: " + ", ".join(rf))
    if not quality.get("edges_have_endpoints", False):
        raise BlockError("graph edge endpoint 누락")
    if not quality.get("nodes_with_id", False):
        raise BlockError("graph node id 누락")
    return {"gate": "PASS"}


# ── 배포 plan / rollback plan / live-check (실행 0) ──────────
def build_deploy_plan(manifest, bundle_full_hash):
    """배포 절차 준비만 — executed=False 강제. 실 cloud 0."""
    release_ready = manifest.get("release_ready") is True
    blocked = None if release_ready else (
        "release_ready=false — 실배포 자격 없음(data_class=%s)" % manifest.get("data_class"))
    return {
        "deploy_plan": {
            "tool": "wrangler",
            "action": "deploy",
            "config": "<wrangler.real.toml>",   # 실 경로/토큰 미노출
            "precondition": [
                "bundle full sha256 배포 직전 재계산 == %s" % bundle_full_hash,
                "manifest.release_ready == true (synthetic이면 실배포 자격 없음)",
                "owner 명시 '이 ZIP 업로드 실행' 문구",
            ],
            "executed": False,
            "cloud_upload": False,
            "db_insert": False,
            "blocked_reason": blocked,
        },
        "rollback_plan": {
            "action": "wrangler rollback <prev_version_id>",
            "prev_pack_preserved": True,
            "restore_verify_required": True,
            "note": "배포 직전 직전 pack/worker 버전 보존 → 실패 시 즉시 복원·복원 후 검증",
            "executed": False,
        },
        "live_check": {
            "command": "curl -fsS --user-agent <custom-UA> <WORKER_URL>/health",
            "followup": "read-only smoke (pack_list/handoff_context) 기대 GO",
            "rationale": "로컬 GATE=GO != live GO — 배포 후 실 endpoint 재확인 의무",
            "ua_note": "python 기본 UA는 Cloudflare 1010 차단 → custom UA 고정",
            "executed": False,
        },
    }


# ── 파이프라인 (synthetic fixture dry-run, P1 큐 구동) ───────
def run_pipeline(out_dir, db_path, queue_id="p2q1",
                 force_release_ready=False, tamper_bundle=False):
    """fixture → build → 검증(hard fail) → zip → P1 큐 상태전이 → 배포 plan. 실 cloud 0.

    force_release_ready / tamper_bundle = selftest BLOCK 케이스 주입용.
    반환: report dict.
    """
    Q._assert_temp_path(db_path)  # 운영 ledger 거부
    nodes, evidence, g, conf = EXP.synthetic_approved()
    build = EXP.build_cloud_pack(out_dir, nodes, evidence, g, conf)

    # (selftest 주입) synthetic release_ready=true 강제 → G24 BLOCK 유도
    if force_release_ready:
        build["manifest"]["release_ready"] = True

    conn = Q.open_queue(db_path)

    # node/evidence/bundle hash
    node_hash = _sha256_file(os.path.join(out_dir, "graph", "nodes.jsonl"))
    evidence_hash = _sha256_file(os.path.join(out_dir, "evidence", "index.jsonl"))

    Q.enqueue(conn, queue_id, "NODE:fixture", node_hash, evidence_hash)
    Q.acquire_lock(conn, queue_id, "watcher_p2")
    Q.transition(conn, queue_id, "building")

    report = {"queue_id": queue_id, "status": None, "blocked_reason": None,
              "cloud_upload": False, "db_insert": False,
              "counts": build.get("counts"), "release_status": build["manifest"].get("release_status")}

    # ── 검증 (fail-closed) ──
    try:
        validate_gate(build)
        check_permanent_guards(out_dir, build)
    except BlockError as e:
        Q.mark_block(conn, queue_id, str(e))
        report["status"] = Q._status(conn, queue_id)   # failed
        report["blocked_reason"] = str(e)
        conn.close()
        return report

    # ── zip + bundle hash ──
    zip_path = os.path.join(os.path.dirname(out_dir), "p2_candidate.zip")
    EXP.make_zip(out_dir, zip_path)
    bundle_hash = _sha256_file(zip_path)

    # (selftest 주입) 배포 직전 재계산 불일치 → hash ABORT 유도
    recomputed = "0" * 64 if tamper_bundle else _sha256_file(zip_path)
    try:
        Q.verify_hash_triple(node_hash, evidence_hash, bundle_hash,
                             node_hash, evidence_hash, recomputed)
    except (Q.IllegalTransition, Q.QueueError) as e:
        Q.mark_block(conn, queue_id, "hash 불일치: %s" % e)
        report["status"] = Q._status(conn, queue_id)
        report["blocked_reason"] = "hash 불일치: %s" % e
        conn.close()
        return report

    # 검증 통과 → candidate_ready
    Q.transition(conn, queue_id, "candidate_ready")

    # owner 승인(APPROVE) — synthetic dry-run: 형태 검증만, 실배포 0
    Q.approve(conn, "APPROVE %s %s" % (queue_id, bundle_hash), bundle_hash)

    # 배포 plan/rollback/live-check 준비 (executed=False)
    plans = build_deploy_plan(build["manifest"], bundle_hash)
    report["status"] = Q._status(conn, queue_id)   # approved
    report["bundle_hash"] = bundle_hash
    report["deploy"] = plans
    report["guards"] = "PASS"
    report["upload_executed"] = False
    conn.close()
    return report


if __name__ == "__main__":
    print("P2 pipeline — run binggu_publish_p2_pipeline_selftest.py")
