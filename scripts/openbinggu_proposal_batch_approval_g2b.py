# -*- coding: utf-8 -*-
"""OpenBinggu G2-B — 연필 후보(edge proposal) 묶음 승인(batch approval) (staging 한정 write).

4cli R3 지시 7·8 + C-2 단일통제 정합:
  - 승인 UX = 건별이 아니라 **묶음(batch) 1클릭**: 요약(라벨 카운트 + 항목별 양끝 문장 발췌 +
    신호=추천 사유 + 공유 evidence id)을 보고 전체 승인/전체 거부.
  - 자동검사 4종 실패 시 버튼 비활성(승인 함수가 거부) — C-2 패턴.
  - write 대상 = **temp/staging SQLite 의 edge_proposals 전용 테이블만** (본 그래프 edges 테이블
    insert 0 — proposal graph 한정, R3 지시 8). 승인되어도 candidate 유지(confirmed 생성 0).
  - 운영 store(localcrab_index.sqlite/user_graph/_graph_merge) write 0 (StagingDB 운영경로 거부 재사용).
  - 거부 시 insert 0 + audit REJECT 기록.

CLI: python openbinggu_proposal_batch_approval_g2b.py --selftest
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openbinggu_staging_write_selftest import StagingDB, OPERATING_PATHS, _hash  # 재사용 (무수정)
import openbinggu_verb_edge_schema as schema

WEAK_LABELS = schema.WEAK_LABELS  # nearby_candidate / stance_candidate
EXCERPT = 60

PROPOSAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS edge_proposals(
    proposal_id TEXT PRIMARY KEY, label TEXT, signal TEXT, source TEXT, target TEXT,
    evidence_refs TEXT, candidate INTEGER DEFAULT 1, promotion_allowed INTEGER DEFAULT 0,
    state TEXT DEFAULT 'accepted_candidate', batch_id TEXT, created_at TEXT);
"""


def open_staging(path):
    """StagingDB 재사용(운영경로 거부 포함) + proposal 전용 테이블 추가."""
    db = StagingDB(path)
    db.con.executescript(PROPOSAL_SCHEMA)
    db.con.commit()
    return db


# ---------------- 묶음 요약 (R3 지시 7: 후보 묶음 + 근거 + 추천 사유) ----------------

def build_batch(proposals, nodes_by_id):
    """proposals → batch dict + 사람용 마크다운 요약. raw 경로/secret 미출력(문장 발췌·id만)."""
    items = []
    for p in sorted(proposals, key=lambda x: x["id"]):
        sa = (nodes_by_id.get(p["source"], {}).get("properties", {}) or {}).get("sentence", "(노드 미제출)")
        sb = (nodes_by_id.get(p["target"], {}).get("properties", {}) or {}).get("sentence", "(노드 미제출)")
        items.append({"proposal_id": p["id"], "label": p["label"], "signal": p["signal"],
                      "source": p["source"], "target": p["target"],
                      "source_excerpt": sa[:EXCERPT], "target_excerpt": sb[:EXCERPT],
                      "evidence_refs": list(p.get("evidence_refs", []))})
    content = json.dumps([i["proposal_id"] for i in items], ensure_ascii=False)
    batch_id = "batch:g2b:" + _hash(content)
    label_counts = {}
    for i in items:
        label_counts[i["label"]] = label_counts.get(i["label"], 0) + 1

    lines = ["# 연필 후보 묶음 승인 요청 — %s" % batch_id,
             "후보 %d건 | 라벨: %s" % (len(items), json.dumps(label_counts, ensure_ascii=False)),
             "", "| # | 라벨 | 신호(사유) | A 문장 | B 문장 | 증거 |", "|---|---|---|---|---|---|"]
    for k, i in enumerate(items, 1):
        lines.append("| %d | %s | %s | %s | %s | %s |" % (
            k, i["label"], i["signal"], i["source_excerpt"], i["target_excerpt"],
            ", ".join(i["evidence_refs"])))
    lines += ["", "→ [전체 승인] 또는 [전체 거부] (1클릭, 승인해도 candidate 유지·confirmed 아님)"]
    return {"batch_id": batch_id, "items": items, "label_counts": label_counts,
            "content_hash": _hash(content), "summary_md": "\n".join(lines)}


# ---------------- 자동검사 4종 (실패 = 버튼 비활성) ----------------

def batch_check(db, batch, nodes_by_id, ctx):
    """통과 None / 실패 reason_code. 전부 deterministic."""
    if ctx.get("actor") in ("auto", "reader"):
        return "G4_no_auto"
    if not batch["items"]:
        return "empty_batch"
    # 이미 결정된 묶음(승인/거부 불문)이 최우선 — 쌍 중복보다 먼저 판정
    if db.con.execute("SELECT 1 FROM applied_registry WHERE pack_id=? AND content_hash=?",
                      (batch["batch_id"], batch["content_hash"])).fetchone():
        return "duplicate_batch_already_decided"
    for i in batch["items"]:
        if i["label"] not in WEAK_LABELS:
            return "strong_or_unknown_label_forbidden"
        if not i["evidence_refs"]:
            return "evidence_refs_missing"
        if i["source"] == i["target"]:
            return "self_loop"
        if i["source"] not in nodes_by_id or i["target"] not in nodes_by_id:
            return "dangling_node"
        if db.con.execute("SELECT 1 FROM edge_proposals WHERE source=? AND target=?",
                          (i["source"], i["target"])).fetchone():
            return "duplicate_pair_in_staging"
    return None


# ---------------- 1클릭 결정 ----------------

def decide_batch(db, batch, nodes_by_id, decision, ctx, snap_dir):
    """decision: 'approve' | 'reject'. approve 시에만 edge_proposals insert (edges 테이블 0).
    실패/거부 시 insert 0. backup→transaction→checksum→audit (staging_apply 패턴)."""
    before = db.store_checksum()
    reason = batch_check(db, batch, nodes_by_id, ctx)
    if reason:
        db.audit_append(ctx.get("actor", "human"), "proposal_batch", batch["batch_id"],
                        "BLOCK", reason, before, before)
        return {"applied": False, "decision": None, "reason": reason, "button": "disabled"}

    if decision == "reject":
        db.con.execute("INSERT INTO applied_registry VALUES(?,?,?)",
                       (batch["batch_id"], batch["content_hash"], "2026-06-11"))
        db.con.commit()
        db.audit_append(ctx.get("actor", "human"), "proposal_batch", batch["batch_id"],
                        "REJECT", "owner_rejected", before, db.store_checksum())
        return {"applied": False, "decision": "reject", "reason": "owner_rejected", "button": "enabled"}

    snap = os.path.join(snap_dir, "snap_g2b_" + _hash(before))
    shutil.copy2(db.path, snap)
    try:
        db.con.execute("BEGIN")
        for i in batch["items"]:
            db.con.execute(
                "INSERT INTO edge_proposals(proposal_id,label,signal,source,target,evidence_refs,"
                "candidate,promotion_allowed,state,batch_id,created_at) VALUES(?,?,?,?,?,?,1,0,?,?,?)",
                (i["proposal_id"], i["label"], i["signal"], i["source"], i["target"],
                 json.dumps(i["evidence_refs"], ensure_ascii=False),
                 "accepted_candidate", batch["batch_id"], "2026-06-11"))
        if ctx.get("checksum_mismatch"):
            db.con.execute("ROLLBACK")
            db.audit_append(ctx.get("actor", "human"), "proposal_batch", batch["batch_id"],
                            "ROLLBACK", "sqlite_checksum_mismatch", before, db.store_checksum())
            return {"applied": False, "decision": None, "reason": "sqlite_checksum_mismatch",
                    "button": "disabled"}
        db.con.execute("INSERT INTO applied_registry VALUES(?,?,?)",
                       (batch["batch_id"], batch["content_hash"], "2026-06-11"))
        db.con.execute("COMMIT")
    except Exception as ex:
        db.con.execute("ROLLBACK")
        return {"applied": False, "decision": None, "reason": "exception:" + type(ex).__name__,
                "button": "disabled"}
    after = db.store_checksum()
    db.audit_append(ctx.get("actor", "human"), "proposal_batch", batch["batch_id"],
                    "ALLOW", None, before, after)
    return {"applied": True, "decision": "approve", "reason": None, "button": "enabled",
            "snapshot": snap}


# ---------------- selftest ----------------

def _n(nid, kind, sent):
    return {"id": nid, "properties": {"label_kind": kind, "sentence": sent, "candidate": True}}


def _p(pid, src, tgt, label="nearby_candidate", signal="co_evidence", refs=("EVC-1",)):
    return {"id": pid, "label": label, "signal": signal, "source": src, "target": tgt,
            "evidence_refs": list(refs)}


def run():
    before_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="obg_g2b_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if ok else "FAIL"))

    nodes = {n["id"]: n for n in [
        _n("node:a", "증거", "로그에 결과가 기록되어 있다."),
        _n("node:b", "판단", "이 입찰은 마진이 낮아 보류한다."),
        _n("node:c", "판단", "조건이 좋아 진행한다."),
    ]}
    props = [_p("prop:1", "node:a", "node:b"),
             _p("prop:2", "node:b", "node:c", label="stance_candidate", signal="co_evidence_opposed_stance")]

    # 1. 정상 묶음 승인 → edge_proposals 2건 · 본 그래프 edges 0건 · candidate 유지 · confirmed 0
    db = open_staging(os.path.join(tmp, "s1.sqlite"))
    batch = build_batch(props, nodes)
    r = decide_batch(db, batch, nodes, "approve", {"actor": "human"}, snap_dir)
    n_prop = db.con.execute("SELECT count(*) FROM edge_proposals").fetchone()[0]
    n_edges = db.con.execute("SELECT count(*) FROM edges").fetchone()[0]
    cand = db.con.execute("SELECT min(candidate), max(promotion_allowed) FROM edge_proposals").fetchone()
    rec(1, "정상 묶음 승인 (proposals=2, edges=0, candidate 유지)",
        r["applied"] and n_prop == 2 and n_edges == 0 and cand == (1, 0))

    # 2. 동일 batch 재결정 차단
    r2 = decide_batch(db, batch, nodes, "approve", {"actor": "human"}, snap_dir)
    rec(2, "duplicate batch 차단", (not r2["applied"]) and r2["reason"] == "duplicate_batch_already_decided")

    # 3. 동일 쌍 staging 중복 차단 (새 batch 로 같은 쌍 제출)
    b3 = build_batch([_p("prop:3", "node:a", "node:b", refs=("EVC-9",))], nodes)
    r3 = decide_batch(db, b3, nodes, "approve", {"actor": "human"}, snap_dir)
    rec(3, "동일 쌍 staging 중복 차단", (not r3["applied"]) and r3["reason"] == "duplicate_pair_in_staging")
    db.close()

    # 4. 거부 → insert 0 + audit REJECT
    db = open_staging(os.path.join(tmp, "s4.sqlite"))
    b4 = build_batch(props, nodes)
    r4 = decide_batch(db, b4, nodes, "reject", {"actor": "human"}, snap_dir)
    n4 = db.con.execute("SELECT count(*) FROM edge_proposals").fetchone()[0]
    aud = db.con.execute("SELECT result FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()[0]
    rec(4, "전체 거부 (insert 0 + audit REJECT)", (not r4["applied"]) and n4 == 0 and aud == "REJECT")
    # 4b. 거부 후 동일 batch 재제출도 차단(결정 번복 방지)
    r4b = decide_batch(db, b4, nodes, "approve", {"actor": "human"}, snap_dir)
    rec(5, "거부된 batch 재승인 차단", (not r4b["applied"]) and r4b["reason"] == "duplicate_batch_already_decided")
    db.close()

    # 6. 강한 라벨 끼워넣기 → 비활성
    db = open_staging(os.path.join(tmp, "s6.sqlite"))
    b6 = build_batch([_p("prop:s", "node:a", "node:b", label="supports_judgment")], nodes)
    r6 = decide_batch(db, b6, nodes, "approve", {"actor": "human"}, snap_dir)
    rec(6, "강한 라벨 혼입 차단", (not r6["applied"]) and r6["reason"] == "strong_or_unknown_label_forbidden")

    # 7. dangling 노드 → 비활성
    b7 = build_batch([_p("prop:d", "node:a", "node:missing")], nodes)
    r7 = decide_batch(db, b7, nodes, "approve", {"actor": "human"}, snap_dir)
    rec(7, "dangling 노드 차단", (not r7["applied"]) and r7["reason"] == "dangling_node")

    # 8. evidence_refs 빈 값 → 비활성
    b8 = build_batch([_p("prop:e", "node:a", "node:b", refs=())], nodes)
    r8 = decide_batch(db, b8, nodes, "approve", {"actor": "human"}, snap_dir)
    rec(8, "evidence_refs 빈 값 차단", (not r8["applied"]) and r8["reason"] == "evidence_refs_missing")

    # 9. actor=auto → 차단 (G4)
    b9 = build_batch([_p("prop:a", "node:a", "node:b", refs=("EVC-2",))], nodes)
    r9 = decide_batch(db, b9, nodes, "approve", {"actor": "auto"}, snap_dir)
    rec(9, "actor=auto 차단 (G4_no_auto)", (not r9["applied"]) and r9["reason"] == "G4_no_auto")
    db.close()

    # 10. checksum mismatch → rollback (부분쓰기 미반영)
    db = open_staging(os.path.join(tmp, "s10.sqlite"))
    b10 = build_batch(props, nodes)
    r10 = decide_batch(db, b10, nodes, "approve", {"actor": "human", "checksum_mismatch": True}, snap_dir)
    rolled = db.con.execute("SELECT count(*) FROM edge_proposals").fetchone()[0] == 0
    rec(10, "checksum mismatch rollback", (not r10["applied"]) and rolled)

    # 11. audit chain intact → 변조 BROKEN
    intact = db.verify_chain()
    db.con.execute("UPDATE audit_log SET action='TAMPER' WHERE seq=(SELECT min(seq) FROM audit_log)")
    db.con.commit()
    rec(11, "audit chain intact→변조 BROKEN", intact and (not db.verify_chain()))
    db.close()

    # 12. 요약 마크다운 안전 (절대경로/백업트리 흔적 0) + batch_id 멱등
    md = build_batch(props, nodes)["summary_md"]
    leak = re.search(r"[A-Za-z]:\\\\?|_backup|cloud_reset_\d+", md)
    idem = build_batch(props, nodes)["batch_id"] == build_batch(props, nodes)["batch_id"]
    rec(12, "요약 raw 미출력 + batch_id 멱등", (leak is None) and idem)

    after_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    store_unchanged = before_mtime == after_mtime
    shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 74)
    print("OpenBinggu G2-B — proposal 묶음 승인 selftest (temp staging, 운영 write 0)")
    print("=" * 74)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print("%s %2d %s" % ("[OK]" if v == "PASS" else "[X]", cid, desc))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("operating_store_unchanged=%s  confirmed_created=0  main_graph_edges_insert=0  deploy=0"
          % store_unchanged)
    gate = "GO" if (npass == len(results) and store_unchanged) else "NO-GO"
    print("GATE:", gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(run())
