# -*- coding: utf-8 -*-
"""hag_sync_adapter.py — 운영 ledger ↔ blind ledger 단방향 동기화 어댑터 (B1: proposal 계층).

4cli 토론(debate/20260617_0500_sync_adapter, both_reject→근거분해 수렴) 합의 + C 기술갭 반영.

방향(단방향):
  운영 ledger(sealed 노드) → read-only 스냅샷 → 의미 후보(edge proposal) 생성
  → sync_edges 에 'proposed' 기록. 사람 도장 후 운영 import(=B2, owner-only)만 영구.
  노드는 절대 blind 가 만들지 않는다(운영=노드 원천). edge proposal 만 어댑터가 생성.

C 기술갭 반영:
  C1 blind_ledger.append 는 answer_hash 만 받음(노드 스냅샷 칼럼 없음) → 멱등키·스냅샷은
     별도 sync_edges 테이블에 저장. blind_ledger 무수정(commit-reveal 봉인은 B2 연동).
  C4 edge_key 멱등키에 evidence/snapshot/timestamp/actor 제외 → 재스냅샷(checksum 변경)
     에도 같은 논리 edge. snapshot_checksum 은 별도 칼럼(변경 추적용), edge_key 와 분리.
  C5 운영 import(운영 edges write)는 owner-only(=B2). 본 B1 은 read-only + sync_edges
     'proposed' 기록까지(영구 아님). actor=human 봉인/도장은 B2.

KMAP:
  openbinggu_label_kind_map(EN2KO/KO2EN) 단일 정본 재사용 — 신규 매핑 정의 0(정본 규약 준수).

영구금지 준수:
  - 운영 ledger 는 read-only(uri mode=ro) — write 0. 운영 edges 등재는 B2(owner-only).
  - sync_edges 는 어댑터 전용 저장소(운영 ledger 와 별도 파일). selftest 는 temp 만.

CLI: python hag_sync_adapter.py --selftest
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)

from openbinggu_label_kind_map import EN2KO  # noqa: E402  (KMAP 단일 정본 재사용)
from binggu_rationale_suggest import suggest_rationale  # noqa: E402

KMAP_VERSION = "v1"                       # edge_key 에 포함 — KMAP 개정 시 키 네임스페이스 분리
SUPPORTS_SRC_KO = {"증거", "상태", "개념"}  # supports_judgment src (verb_edge_schema 정합)


# ── 운영 ledger read-only 스냅샷 (write 0) ─────────────────────────
def snapshot_operating_nodes(ledger_path, state="active"):
    """운영 ledger 의 sealed(active·non-candidate) 노드 read-only 스냅샷.

    uri mode=ro 로 열어 write 물리 차단. 운영 노드는 어댑터가 절대 수정하지 않는다.
    반환: [{node_id, node_type(영문), sentence, content_hash}].
    """
    if not os.path.exists(ledger_path):
        return []
    conn = sqlite3.connect("file:%s?mode=ro" % ledger_path, uri=True)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT node_id, node_type, sentence, content_hash FROM nodes "
            "WHERE state = ? AND candidate = 0", (state,))
        rows = cur.fetchall()
    finally:
        conn.close()
    return [{"node_id": r[0], "node_type": r[1], "sentence": r[2], "content_hash": r[3]}
            for r in rows]


def snapshot_checksum(node):
    """노드 스냅샷 체크섬 — 재스냅샷 시 내용 변경 추적용(edge_key 와는 분리·C4)."""
    payload = json.dumps({"node_type": node["node_type"], "sentence": node["sentence"]},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── 후보 변환 (KMAP EN2KO) ─────────────────────────────────────────
def to_candidates(snapshot):
    """운영 스냅샷 → rationale_suggest 입력. node_type(영문)→label_kind(한글) 변환.

    증거/상태/개념(supports src)은 자기증빙(conv-self) evidence_refs=[node_id] 부여 —
    노드 자체가 자기 근거(reflect traj '노드=증거=자기증빙'). 판단(tgt)은 evidence 없음.
    """
    cands = []
    for n in snapshot:
        kind = EN2KO.get(n["node_type"], n["node_type"])  # 비매핑은 그대로 → 매트릭스가 폐기
        c = {"id": n["node_id"], "text": n["sentence"], "label_kind": kind}
        if kind in SUPPORTS_SRC_KO:
            c["evidence_refs"] = [n["node_id"]]
        cands.append(c)
    return cands


# ── 멱등 edge_key (C4: evidence/snapshot/ts/actor 제외) ────────────
def edge_key(src_id, dst_id, relation, kmap_version=KMAP_VERSION):
    """안정 멱등키 — src·dst·relation·kmap_version 만. 재스냅샷/evidence 변경에 불변.

    evidence_refs·snapshot_checksum·timestamp·actor 는 절대 키에 넣지 않는다(C4 충돌 방지).
    """
    raw = "sync_edge:%s|%s|%s|%s" % (kmap_version, src_id, dst_id, relation)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── proposal 생성 (read-only) ──────────────────────────────────────
def build_proposals(ledger_path=None, snapshot=None):
    """운영 스냅샷 → edge proposal 목록. rationale_suggest 매트릭스 PASS 만(신규 predicate 0).

    self-loop(src==dst) 제외. 전부 read-only — 운영/ sync DB write 0(기록은 record_proposals).
    """
    snap = snapshot if snapshot is not None else snapshot_operating_nodes(ledger_path)
    cmap = {n["node_id"]: n for n in snap}
    r = suggest_rationale(to_candidates(snap))
    proposals = []
    for e in r["suggested_edges"]:
        sid, did = e["source_id"], e["target_id"]
        if sid == did:
            continue
        proposals.append({
            "edge_key": edge_key(sid, did, e["relation"]),
            "src_node_id": sid, "dst_node_id": did, "relation": e["relation"],
            "src_checksum": snapshot_checksum(cmap[sid]) if sid in cmap else None,
            "dst_checksum": snapshot_checksum(cmap[did]) if did in cmap else None,
            "kmap_version": KMAP_VERSION,
            "evidence_refs": list(e.get("evidence_refs", [])),
            "status": "proposed",
        })
    return proposals


# ── sync_edges 어댑터 전용 저장소 ──────────────────────────────────
def open_sync_db(path):
    """sync_edges 테이블 열기(없으면 생성). 운영 ledger 와 별도 파일."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sync_edges ("
        " edge_key      TEXT PRIMARY KEY,"   # 멱등키(C4)
        " src_node_id   TEXT NOT NULL,"
        " dst_node_id   TEXT NOT NULL,"
        " relation      TEXT NOT NULL,"
        " src_checksum  TEXT,"              # 스냅샷 변경 추적
        " dst_checksum  TEXT,"
        " kmap_version  TEXT NOT NULL,"
        " evidence_refs TEXT,"             # JSON
        " status        TEXT NOT NULL DEFAULT 'proposed',"  # proposed→confirmed→imported(B2)
        " imported_edge_id TEXT,"          # 운영 edge_id (B2 import 후)
        " created_at    INTEGER,"
        " updated_at    INTEGER"
        ")")
    conn.commit()
    return conn


def record_proposals(conn, proposals, now=0):
    """sync_edges 멱등 기록. edge_key PK → 중복 0.

    이미 있고 status='proposed' 이며 checksum 변했으면 추적 갱신(같은 키). confirmed/imported
    (사람 도장·B2) 행은 절대 덮어쓰지 않는다(영구 보존). 반환 카운트.
    """
    inserted = updated = preserved = 0
    cur = conn.cursor()
    for p in proposals:
        cur.execute("SELECT src_checksum, dst_checksum, status FROM sync_edges WHERE edge_key = ?",
                    (p["edge_key"],))
        ex = cur.fetchone()
        if ex is None:
            cur.execute(
                "INSERT INTO sync_edges (edge_key, src_node_id, dst_node_id, relation,"
                " src_checksum, dst_checksum, kmap_version, evidence_refs, status,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (p["edge_key"], p["src_node_id"], p["dst_node_id"], p["relation"],
                 p["src_checksum"], p["dst_checksum"], p["kmap_version"],
                 json.dumps(p["evidence_refs"]), p["status"], now, now))
            inserted += 1
        elif ex[2] != "proposed":
            preserved += 1   # confirmed/imported = 사람 도장분 보존(덮어쓰기 0)
        elif ex[0] != p["src_checksum"] or ex[1] != p["dst_checksum"]:
            cur.execute("UPDATE sync_edges SET src_checksum=?, dst_checksum=?, updated_at=? "
                        "WHERE edge_key=?",
                        (p["src_checksum"], p["dst_checksum"], now, p["edge_key"]))
            updated += 1
    conn.commit()
    return {"inserted": inserted, "updated": updated, "preserved": preserved,
            "total_proposed": len(proposals)}


# ── selftest (temp 만 · 운영 ledger/blind/CF 미접촉) ───────────────
def _selftest():
    import tempfile
    results = []

    def ck(name, cond):
        results.append((name, bool(cond)))

    tmp = tempfile.mkdtemp(prefix="hag_sync_")
    led = os.path.join(tmp, "ledger.sqlite")
    conn = sqlite3.connect(led)
    conn.execute("CREATE TABLE nodes (node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT,"
                 " candidate INTEGER DEFAULT 1, state TEXT DEFAULT 'active', content_hash TEXT)")
    sample = [
        ("n_ev", "evidence", "로그에 오류가 기록되어 있다", 0, "active", "h1"),
        ("n_st", "state", "백필이 진행 중이다", 0, "active", "h2"),
        ("n_j1", "judgment", "이 입찰은 보류한다", 0, "active", "h3"),
        ("n_j2", "judgment", "이 방식을 채택한다", 0, "active", "h4"),
        ("n_doc", "doc", "이 문서는 절차를 규정한다", 0, "active", "h5"),
        ("n_cand", "judgment", "후보 노드(미확정)", 1, "candidate", "h6"),  # candidate=제외
    ]
    conn.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?)", sample)
    conn.commit()
    conn.close()

    # T1 snapshot read-only — active·non-candidate 5건(n_cand 제외)
    snap = snapshot_operating_nodes(led)
    ck("T1 snapshot active 5건(candidate 제외)", len(snap) == 5)
    # T1b 운영 ledger 미변경(read-only) — mode=ro 라 write 시 예외
    ro = sqlite3.connect("file:%s?mode=ro" % led, uri=True)
    blocked = False
    try:
        ro.execute("INSERT INTO nodes VALUES ('x','judgment','x',0,'active','h')")
        ro.commit()
    except sqlite3.OperationalError:
        blocked = True
    ro.close()
    ck("T1b 운영 read-only(write 차단)", blocked)

    # T2 EN2KO 변환 + 자기증빙
    cands = to_candidates(snap)
    cmap = {c["id"]: c for c in cands}
    ck("T2 evidence→증거 변환", cmap["n_ev"]["label_kind"] == "증거")
    ck("T2b judgment→판단 변환", cmap["n_j1"]["label_kind"] == "판단")
    ck("T2c 증거 자기증빙 evidence_refs", cmap["n_ev"]["evidence_refs"] == ["n_ev"])
    ck("T2d 판단(tgt)은 evidence_refs 없음", "evidence_refs" not in cmap["n_j1"])

    # T3 edge_key 멱등 + 안정성(C4)
    ck("T3 edge_key 멱등", edge_key("a", "b", "supports_judgment") == edge_key("a", "b", "supports_judgment"))
    ck("T3b relation 다르면 키 다름", edge_key("a", "b", "r1") != edge_key("a", "b", "r2"))
    ck("T3c src/dst 다르면 키 다름", edge_key("a", "b", "r") != edge_key("b", "a", "r"))

    # T4 build_proposals — 증거+상태(src) × 판단2(tgt) = 4, 문서 src 제외, self-loop 0
    props = build_proposals(snapshot=snap)
    ck("T4 proposal = src(증거1+상태1)×판단2 = 4", len(props) == 4)
    ck("T4b 문서 src 제외(매트릭스)", all(p["src_node_id"] != "n_doc" for p in props))
    ck("T4c self-loop 0", all(p["src_node_id"] != p["dst_node_id"] for p in props))
    ck("T4d 전부 supports_judgment(신규 predicate 0)", all(p["relation"] == "supports_judgment" for p in props))
    ck("T4e proposal 전부 status=proposed(영구 아님)", all(p["status"] == "proposed" for p in props))

    # T5 record 멱등 — 2회 → 중복 0
    sdb = os.path.join(tmp, "sync.sqlite")
    sc = open_sync_db(sdb)
    r1 = record_proposals(sc, props, now=100)
    r2 = record_proposals(sc, props, now=200)
    cnt = sc.execute("SELECT count(*) FROM sync_edges").fetchone()[0]
    ck("T5 2회 기록 → 중복 0(멱등)", r1["inserted"] == 4 and r2["inserted"] == 0 and cnt == 4)

    # T6 checksum 변경(sentence 수정) → 같은 edge_key, checksum 추적 갱신(중복 0)
    snap2 = [dict(n, sentence=n["sentence"] + " 수정" if n["node_id"] == "n_ev" else n["sentence"])
             for n in snap]
    props2 = build_proposals(snapshot=snap2)
    r3 = record_proposals(sc, props2, now=300)
    cnt2 = sc.execute("SELECT count(*) FROM sync_edges").fetchone()[0]
    ck("T6 checksum 변경에도 edge_key 동일(중복 0·C4)", cnt2 == 4 and r3["inserted"] == 0)
    ck("T6b checksum 추적 갱신됨", r3["updated"] >= 1)

    # T7 imported(사람 도장·B2) 행은 재기록에 보존(덮어쓰기 0)
    one_key = props[0]["edge_key"]
    sc.execute("UPDATE sync_edges SET status='imported', imported_edge_id='E-real-1' WHERE edge_key=?",
               (one_key,))
    sc.commit()
    r4 = record_proposals(sc, props, now=400)
    st = sc.execute("SELECT status, imported_edge_id FROM sync_edges WHERE edge_key=?", (one_key,)).fetchone()
    ck("T7 imported 행 보존(재proposal 덮어쓰기 0)", st[0] == "imported" and st[1] == "E-real-1" and r4["preserved"] >= 1)
    sc.close()

    # T8 KMAP 단일 정본 재사용(신규 매핑 0)
    from openbinggu_label_kind_map import EN2KO as SRC
    ck("T8 KMAP 단일 정본 재사용", EN2KO is SRC)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    gate = "GO" if passed == total else "NO-GO"
    print("\nselftest: %d/%d\nGATE: %s" % (passed, total, gate))
    return passed == total


if __name__ == "__main__":
    if "--selftest" in sys.argv or not sys.argv[1:]:
        sys.exit(0 if _selftest() else 1)
    print("usage: python hag_sync_adapter.py --selftest")
    sys.exit(2)
