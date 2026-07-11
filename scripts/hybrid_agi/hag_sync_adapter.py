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
BASE = os.path.dirname(SCRIPTS)               # repo root — binggupack 패키지 import 용(A4 approval)
sys.path.insert(0, HERE)
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, BASE)

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
def build_proposals(ledger_path=None, snapshot=None, semantic=None):
    """운영 스냅샷 → edge proposal 목록. rationale_suggest 매트릭스 PASS 만(신규 predicate 0).

    self-loop(src==dst) 제외. 전부 read-only — 운영/ sync DB write 0(기록은 record_proposals).
    semantic: suggest_rationale 로 passthrough(None=opt-in 자동 pre-filter / False=강제 OFF·전수 /
              callable=scorer 주입). 프로덕션 기본 None 불변. selftest 는 임베딩 구성 무관 결정성을
              위해 semantic=False(전수) 사용.
    """
    snap = snapshot if snapshot is not None else snapshot_operating_nodes(ledger_path)
    cmap = {n["node_id"]: n for n in snap}
    r = suggest_rationale(to_candidates(snap), semantic=semantic)
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


# ── B2: 사람 도장(confirm) + owner-only import (운영 edges write) ──
class SyncError(Exception):
    """동기화 정책 위반(BLOCK)."""


def confirm_edge(conn, edge_key, now=0, *, actor="human"):
    """사람 도장 — sync_edges proposed→confirmed(어댑터 전용 저장소 · 운영 ledger write 아님).

    ★A4: actor 는 **keyword-only**(positional 제거) — graph_confirm.apply_confirm_to_sync·
    이 모듈 CLI 는 전부 `actor=` 키워드로 호출하므로 시그니처 호환. confirm_edge 는 sync_edges
    (어댑터 전용)만 만지므로 운영 write 우회 표면이 아니다 → 기존 actor!='human' 이중 게이트 유지
    (A2 confirm_edges CLI 래퍼가 _resolve_human_ctx 로 human ctx 전달·fail-closed).
    confirm 은 사람이 후보 중 의미 있는 것을 고르는 owner 행위. AI 경로 금지. 이미 imported 면 no-op(멱등).
    """
    if actor != "human":
        raise SyncError("BLOCK actor!=human (confirm=사람 도장): %r" % actor)
    cur = conn.cursor()
    cur.execute("SELECT status FROM sync_edges WHERE edge_key = ?", (edge_key,))
    row = cur.fetchone()
    if row is None:
        raise SyncError("unknown edge_key: %s" % edge_key)
    if row[0] == "imported":
        return {"edge_key": edge_key, "status": "imported", "noop": True}
    cur.execute("UPDATE sync_edges SET status='confirmed', updated_at=? WHERE edge_key=?",
                (now, edge_key))
    conn.commit()
    return {"edge_key": edge_key, "status": "confirmed"}


def _effective_import_set(sync_conn, op_con):
    """confirmed(사람 도장·미import) 중 운영 노드 실재(non-dangling)만 = 실제 적재 대상.

    반환 (effective[list · edge_key/src/dst/rel/evidence], dangling_count, confirmed_count).
    evidence 는 sync_edges.evidence_refs(JSON) 파싱 str list. 바인딩 payload 의 정본 입력.
    """
    cur = sync_conn.cursor()
    cur.execute("SELECT edge_key, src_node_id, dst_node_id, relation, evidence_refs, imported_edge_id "
                "FROM sync_edges WHERE status = 'confirmed'")
    rows = cur.fetchall()
    opc = op_con.cursor()
    opc.execute("SELECT node_id FROM nodes")
    node_ids = {r[0] for r in opc.fetchall()}
    effective, dangling = [], 0
    for ek, sid, did, rel, ev, imp in rows:
        if imp:                                     # 방어(confirmed 인데 imported → skip)
            continue
        if sid not in node_ids or did not in node_ids:  # dangling → 적재 대상 아님(바인딩 제외·M4)
            dangling += 1
            continue
        try:
            ev_list = [str(x) for x in (json.loads(ev) if ev else [])]
        except Exception:
            ev_list = []
        effective.append({"edge_key": ek, "src": sid, "dst": did, "rel": rel, "evidence": ev_list})
    return effective, dangling, len(rows)


def _import_payload(effective):
    """binding_fields('import_edges', …) 입력 payload — 실제 적재될 post-filter subset(M4).
    src|dst|rel|evidence 만(binding_fields 가 evidence NFC+정렬·edge 정렬). edge_key/checksum 제외."""
    return {"edges": [{"src": e["src"], "dst": e["dst"], "rel": e["rel"],
                       "evidence": list(e["evidence"])} for e in effective]}


def import_confirmed_edges(sync_conn, ledger_path, now=0, *, home=None, approval_id=None, actor=None):
    """운영 ledger edges INSERT — **exact-bound trusted approval 전용**(A4 · owner-only).

    ★ `--actor` 는 승인 권한 없음(감사 metadata 로만). 운영 write 유일 근거 = operation='import_edges'
    exact-bound approval event(binding: 실제 적재 edge 의 src|dst|rel|evidence 정렬 · §2 C1). env/confirm/
    actor 로 승격 0. provider 미구성/미승인 → SyncError(fail-closed) · 직접 import 우회 0.

    - ledger_id·PENDING request·reserve/finalize 는 **운영 ledger con**(M3 · sync_edges db 아님).
      운영 con 에 approval_requests/approval_consumptions 를 apply_schema 로 idempotent 보장(비파괴).
    - 빈 confirmed(또는 effective=∅ · 전부 dangling) = 승인 불요 no-op(A-5 · T13 멱등 정합 · write 0).
    - 승인 미제시 → PENDING request + owner 검토 레코드 기록 후 SyncError(reason=approval_required·request_id).
    - 승인 제시 → payload 재계산 rid 대조 + verify_event(operation/payload/ledger/protocol/TTL) + one-time
      reserve → 정확히 1회 INSERT → finalize. replay/동시 → already_consumed(2차 write 0).
    - 적재 중 실패 → rollback + release(승인 소각 0). selftest 는 temp 운영 ledger 로만 검증.
    """
    from binggupack.safety import trusted_approval as ta
    from binggu_schema import apply_schema, ledger_id as _lid

    op = sqlite3.connect(ledger_path)
    try:
        apply_schema(op)  # approval_requests/consumptions + ledger_id 보장(idempotent·비파괴)
        effective, dangling, confirmed_n = _effective_import_set(sync_conn, op)

        # A-5: 실제 적재 대상 0(빈 confirmed 또는 전부 dangling) → 승인 불요 no-op(운영 write 0).
        if not effective:
            return {"imported": 0, "skipped": 0, "dangling": dangling,
                    "confirmed": confirmed_n, "no_op": True}

        provider = ta.provider_for(home)
        if provider is None:
            raise SyncError("BLOCK provider_not_configured (import=exact-bound approval only · fail-closed)")

        payload = _import_payload(effective)
        try:
            digest = ta.canonical_payload_digest("import_edges", payload)
        except ta.ControlCharReject as e:
            err = SyncError("BLOCK binding_reject:control_char: %s" % e)
            err.reason = "binding_reject:control_char"
            raise err
        lid = _lid(op)
        rid = ta.compute_request_id("import_edges", digest, lid)

        if not approval_id:
            # 승인 미제시 → owner 가 승인할 수 있게 PENDING 요청 + 검토 레코드(원문 문장 발췌) 기록. fail-closed.
            up = ta.upsert_request(op, rid, ta.PROTOCOL_VERSION, "import_edges", digest, lid,
                                   ta.summary_for("import_edges", payload, lid), now,
                                   provider.ttl_seconds, provider.pending_cap)
            if up.get("ok") and home:
                try:
                    ta.write_review(home, rid, "import_edges", payload, digest)
                except Exception:
                    pass
            err = SyncError("approval_required — owner 로컬 승인 필요: "
                            "binggu approval show %s → binggu approval approve %s" % (rid, rid))
            err.reason = "approval_required"
            err.request_id = rid
            raise err

        if approval_id != rid:
            # 모델/호출부가 제 payload 와 다른 승인 id 제시 = payload(evidence 포함·C1) 바인딩 불일치.
            err = SyncError("BLOCK binding_mismatch:request_id (payload≠승인 · evidence/edge 변조 의심)")
            err.reason = "binding_mismatch:request_id"
            err.request_id = rid
            raise err

        v = ta.verify_event(home, rid, "import_edges", digest, lid, now)
        if not v.get("ok"):
            err = SyncError("BLOCK %s" % v.get("reason"))
            err.reason = v.get("reason")
            err.request_id = rid
            raise err

        nonce = v["nonce"]
        res = ta.reserve(op, nonce, now)
        st = res.get("status")
        if st == "already_consumed":
            # replay — 승인 1회 소비 완료 → 2차 import 0(운영 write 0 · 멱등).
            return {"imported": 0, "skipped": len(effective), "dangling": dangling,
                    "already_consumed": True, "request_id": rid}
        if st == "in_progress":
            err = SyncError("BLOCK approval_in_progress")
            err.reason = "approval_in_progress"
            err.request_id = rid
            raise err

        # reserved(승자/takeover). mutate 직전 tombstone 재확인(verify 후 landing 한 revoke 차단).
        tomb, treason = ta.is_tombstoned(home, rid)
        if tomb:
            ta.release(op, nonce)
            err = SyncError("BLOCK %s" % treason)
            err.reason = treason
            err.request_id = rid
            raise err

        # 운영 edges INSERT — op.commit + sync_conn.commit 순차. ★R3-7(Fable5 사후): cross-DB(운영 ledger
        # + sync_edges db)라 단일 sqlite tx 불가·비원자. op.commit 후 sync.commit 실패 창은 edge_id=
        # EDG-sync-<ek16> 결정적 + INSERT OR IGNORE 멱등으로 재시도 수렴(이중저장 0). 실패 시 승인 소각 0
        # (release). ★R3-1: consume 감사 = approval_consumptions.receipt(imported_edge_ids·actor·request_id),
        # 운영 edges 무결성 = content_hash — hag 는 B1 설계상 audit_log 체인을 쓰지 않는다(직접 sqlite).
        imported_ids = []
        try:
            opc = op.cursor()
            scur = sync_conn.cursor()
            for e in effective:
                ek, sid, did, rel = e["edge_key"], e["src"], e["dst"], e["rel"]
                edge_id = "EDG-sync-%s" % ek[:16]
                content = hashlib.sha256(("%s|%s|%s|%s" % (rel, sid, did, ek)).encode()).hexdigest()
                opc.execute(
                    "INSERT OR IGNORE INTO edges (edge_id, relation, source, target, candidate,"
                    " state, evidence_refs, content_hash, created_at) VALUES (?,?,?,?,0,'active',?,?,?)",
                    (edge_id, rel, sid, did, json.dumps(e["evidence"]), content, now))
                scur.execute("UPDATE sync_edges SET status='imported', imported_edge_id=?, updated_at=? "
                             "WHERE edge_key=?", (edge_id, now, ek))
                imported_ids.append(edge_id)
            op.commit()
            sync_conn.commit()
        except Exception as ex:
            try:
                op.rollback()
            except Exception:
                pass
            try:
                sync_conn.rollback()
            except Exception:
                pass
            ta.release(op, nonce)          # hag_failed_import_does_not_consume — 승인 소각 0
            err = SyncError("import_write_failed: %s" % ex)
            err.reason = "import_write_failed"
            err.request_id = rid
            raise err

        # actor 는 감사 metadata 로만 — 승인/게이트에 관여 0(receipt 에 기록·nonce 미포함 · TAE-6).
        receipt = {"request_id": rid, "operation": "import_edges",
                   "imported_edge_ids": imported_ids, "actor": actor}
        ta.finalize_consumed(op, nonce, rid, receipt, now)
        try:
            op.execute("UPDATE approval_requests SET state='consumed' WHERE request_id=?", (rid,))
            op.commit()
        except Exception:
            pass
        if home:
            ta.purge_review(home, rid)
        return {"imported": len(imported_ids), "skipped": 0, "dangling": dangling,
                "request_id": rid, "receipt_actor": actor}
    finally:
        op.close()


# ── 표시용 목록 (운영 노드 sentence 조인 · read-only) ──────────────
def list_view(sync_conn, ledger_path, status=None):
    """sync_edges + 운영 노드 sentence 조인 — 사람이 도장 결정할 때 보는 목록. read-only."""
    snap = {}
    if ledger_path and os.path.exists(ledger_path):
        snap = {n["node_id"]: n["sentence"] for n in snapshot_operating_nodes(ledger_path)}
    cur = sync_conn.cursor()
    base = "SELECT edge_key, src_node_id, dst_node_id, relation, status FROM sync_edges"
    rows = (cur.execute(base + " WHERE status = ?", (status,)).fetchall()
            if status else cur.execute(base).fetchall())
    return [{"edge_key": ek, "status": st, "relation": rel,
             "src": (snap.get(sid) or sid)[:40], "dst": (snap.get(did) or did)[:40]}
            for ek, sid, did, rel, st in rows]


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
    # semantic=False: 임베딩 구성 유무 무관 결정성(cos pre-filter OFF·전수) — 카운트 테스트 안정화.
    props = build_proposals(snapshot=snap, semantic=False)
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
    props2 = build_proposals(snapshot=snap2, semantic=False)
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

    # ── B2: confirm_edge(사람 도장 · sync_edges 전용 · 운영 write 아님) ──
    sdb2 = os.path.join(tmp, "sync2.sqlite")
    sc2 = open_sync_db(sdb2)
    props_b2 = build_proposals(snapshot=snap, semantic=False)
    record_proposals(sc2, props_b2, now=100)
    target_key = props_b2[0]["edge_key"]

    # T9 confirm actor!=human BLOCK(이중 게이트 유지 · actor keyword-only)
    blk = False
    try:
        confirm_edge(sc2, target_key, actor="ai", now=110)
    except SyncError:
        blk = True
    ck("T9 confirm actor!=human BLOCK", blk)
    # T10 confirm human → confirmed
    ck("T10 confirm human → confirmed", confirm_edge(sc2, target_key, actor="human", now=120)["status"] == "confirmed")
    sc2.close()

    # ══════════════════════════════════════════════════════════════════════════════
    # A4: import_confirmed_edges 운영 ledger write = exact-bound trusted approval 전용
    #     --actor 승인 권한 제거(감사 metadata 만) · provider 미구성/미승인 fail-closed · 우회 0.
    # ══════════════════════════════════════════════════════════════════════════════
    import inspect
    import json as _json
    import time as _time
    from binggupack.safety import trusted_approval as ta
    from binggu_schema import apply_schema as _apply, ledger_id as _lid

    hag_home = os.path.join(tmp, "hag_home")
    os.makedirs(hag_home, exist_ok=True)

    def _enable():
        with open(ta.config_path(hag_home), "w", encoding="utf-8") as f:
            _json.dump({"enabled": True, "ttl_seconds": 900, "pending_cap": 16}, f)

    def _disable():
        p = ta.config_path(hag_home)
        if os.path.exists(p):
            os.remove(p)

    def _fresh(name, with_nodes=True):
        """운영 ledger(nodes n_ev·n_j1) + sync db(confirmed n_ev→n_j1 supports·evidence=[n_ev])."""
        d = os.path.join(tmp, name)
        os.makedirs(d, exist_ok=True)
        ledp = os.path.join(d, "ledger.sqlite")
        lc = sqlite3.connect(ledp)
        lc.execute("CREATE TABLE nodes (node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT,"
                   " candidate INTEGER DEFAULT 1, state TEXT DEFAULT 'active', content_hash TEXT)")
        if with_nodes:
            lc.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?)", [
                ("n_ev", "evidence", "로그에 오류가 있다", 0, "active", "h1"),
                ("n_j1", "judgment", "이 입찰은 보류한다", 0, "active", "h3")])
        lc.commit()
        lc.close()
        sdb = os.path.join(d, "sync.sqlite")
        scc = open_sync_db(sdb)
        ek = edge_key("n_ev", "n_j1", "supports_judgment")
        scc.execute("INSERT INTO sync_edges (edge_key,src_node_id,dst_node_id,relation,src_checksum,"
                    "dst_checksum,kmap_version,evidence_refs,status,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (ek, "n_ev", "n_j1", "supports_judgment", None, None, "v1",
                     _json.dumps(["n_ev"]), "confirmed", 0, 0))
        scc.commit()
        return ledp, sdb, scc, ek

    def _ecount(ledp):
        o = sqlite3.connect(ledp)
        try:
            return o.execute("SELECT count(*) FROM edges").fetchone()[0]
        finally:
            o.close()

    def _consumed(ledp):
        o = sqlite3.connect(ledp)
        try:
            cons = o.execute("SELECT count(*) FROM approval_consumptions WHERE state='consumed'").fetchone()[0]
            total = o.execute("SELECT count(*) FROM approval_consumptions").fetchone()[0]
            return cons, total
        finally:
            o.close()

    def _rid_for(ledp):
        """운영 ledger 기준 import_edges rid/digest/lid(현재 effective payload 기준)."""
        o = sqlite3.connect(ledp)
        _apply(o)
        lid = _lid(o)
        o.close()
        payload = {"edges": [{"src": "n_ev", "dst": "n_j1", "rel": "supports_judgment",
                              "evidence": ["n_ev"]}]}
        digest = ta.canonical_payload_digest("import_edges", payload)
        return ta.compute_request_id("import_edges", digest, lid), digest, lid

    def _request_mint(scc, ledp):
        """1차 import(승인 미제시)로 PENDING request 생성 → rid 회수 → owner mint(실시간)."""
        rid = None
        try:
            import_confirmed_edges(scc, ledp, now=int(_time.time()), home=hag_home)
        except SyncError as e:
            rid = getattr(e, "request_id", None)
        o = sqlite3.connect(ledp)
        req = ta.get_request(o, rid)
        o.close()
        # 5초 과거로 mint — 이후 int(now) truncation 이 approved_at 보다 작아지는 clock 오탐 회피(TTL 내).
        ta.mint_approval(hag_home, req, 900, _time.time() - 5)
        return rid

    # hag_actor_is_audit_metadata_only 전제: import 시그니처에 authz 용 actor positional 없음.
    _sig = inspect.signature(import_confirmed_edges)
    _actor_p = _sig.parameters.get("actor")
    ck("hag_actor_is_audit_metadata_only(actor keyword-only·기본 None)",
       _actor_p is not None and _actor_p.kind == inspect.Parameter.KEYWORD_ONLY and _actor_p.default is None)

    # ── hag_provider_absent_fail_closed ──
    _disable()
    ledp, sdb, scc, ek = _fresh("hag_prov_absent")
    blk = False
    try:
        import_confirmed_edges(scc, ledp, now=100, home=hag_home)
    except SyncError as e:
        blk = getattr(e, "reason", None) == "provider_not_configured" or "provider" in str(e)
    scc.close()
    ck("hag_provider_absent_fail_closed(운영 edges INSERT 0)", blk and _ecount(ledp) == 0)

    # ── hag_actor_human_cannot_approve — actor='human' 이라도 승인 없으면 write 0 ──
    _enable()
    ledp, sdb, scc, ek = _fresh("hag_actor_human")
    blk = False
    rid_seen = None
    try:
        import_confirmed_edges(scc, ledp, now=int(_time.time()), home=hag_home, actor="human")
    except SyncError as e:
        blk = getattr(e, "reason", None) == "approval_required"
        rid_seen = getattr(e, "request_id", None)
    scc.close()
    ck("hag_actor_human_cannot_approve(승인 없으면 human 도 write 0)",
       blk and bool(rid_seen) and _ecount(ledp) == 0)

    # ── hag_confirm_phrase_cannot_approve — 매직 confirm 문자열은 승인 아님(binding mismatch) ──
    ledp, sdb, scc, ek = _fresh("hag_confirm_phrase")
    blk = False
    try:
        import_confirmed_edges(scc, ledp, now=int(_time.time()), home=hag_home,
                               approval_id="CONFIRM IMPORT EDGES")
    except SyncError as e:
        blk = getattr(e, "reason", None) == "binding_mismatch:request_id"
    scc.close()
    ck("hag_confirm_phrase_cannot_approve(문자열 승인 우회 0)", blk and _ecount(ledp) == 0)

    # ── hag_environment_cannot_approve — truthy env 로 provider 활성화 안 됨(파일 신호만) ──
    _disable()
    os.environ["BINGGU_TRUSTED_APPROVAL"] = "1"
    os.environ["BINGGU_APPROVAL_TOKEN"] = "x"
    ledp, sdb, scc, ek = _fresh("hag_env")
    blk = False
    try:
        import_confirmed_edges(scc, ledp, now=int(_time.time()), home=hag_home)
    except SyncError:
        blk = ta.provider_for(hag_home) is None
    scc.close()
    os.environ.pop("BINGGU_TRUSTED_APPROVAL", None)
    os.environ.pop("BINGGU_APPROVAL_TOKEN", None)
    ck("hag_environment_cannot_approve(env 승격 0)", blk and _ecount(ledp) == 0)
    _enable()

    # ── hag_valid_exact_approval_imports_once — 정확 승인 → 정확히 1회 ──
    ledp, sdb, scc, ek = _fresh("hag_valid")
    rid = _request_mint(scc, ledp)
    r = import_confirmed_edges(scc, ledp, now=int(_time.time()), home=hag_home, approval_id=rid)
    erow = None
    o = sqlite3.connect(ledp)
    erow = o.execute("SELECT relation, candidate, state FROM edges").fetchone()
    o.close()
    cons, _ = _consumed(ledp)
    ck("hag_valid_exact_approval_imports_once(import 1·consume 1·supports·active·non-candidate)",
       r["imported"] == 1 and _ecount(ledp) == 1 and cons == 1
       and erow == ("supports_judgment", 0, "active"))

    # ── hag_approval_replay_no_second_import — sync 를 confirmed 로 되돌려 reserve 가드 검증 ──
    scc.execute("UPDATE sync_edges SET status='confirmed', imported_edge_id=NULL WHERE edge_key=?", (ek,))
    scc.commit()
    r2 = import_confirmed_edges(scc, ledp, now=int(_time.time()), home=hag_home, approval_id=rid)
    ck("hag_approval_replay_no_second_import(already_consumed·운영 edges 여전히 1)",
       r2.get("imported") == 0 and r2.get("already_consumed") is True and _ecount(ledp) == 1)
    scc.close()

    # ── hag_approval_operation_mismatch_blocked — operation 위조 event ──
    ledp, sdb, scc, ek = _fresh("hag_opmis")
    rid, digest, lid = _rid_for(ledp)
    tn = _time.time()
    ta.append_event(hag_home, {"request_id": rid, "protocol_version": ta.PROTOCOL_VERSION,
                               "operation": "confirm_edges", "payload_digest": digest, "ledger_id": lid,
                               "approval_nonce": "opmis_nonce_" + "a" * 20,
                               "approved_at": tn, "expires_at": tn + 900, "record_type": "approve"})
    blk = False
    try:
        import_confirmed_edges(scc, ledp, now=int(tn), home=hag_home, approval_id=rid)
    except SyncError as e:
        blk = getattr(e, "reason", None) == "binding_mismatch:operation"
    scc.close()
    ck("hag_approval_operation_mismatch_blocked(운영 edges INSERT 0)", blk and _ecount(ledp) == 0)

    # ── hag_approval_payload_mismatch_blocked — evidence 위조 → digest/rid 변화 → 승인 불일치 ──
    ledp, sdb, scc, ek = _fresh("hag_payloadmis")
    rid = _request_mint(scc, ledp)                       # evidence=[n_ev] 로 승인
    scc.execute("UPDATE sync_edges SET evidence_refs=? WHERE edge_key=?",
                (_json.dumps(["n_ev", "FORGED_EVIDENCE"]), ek))
    scc.commit()
    blk = False
    try:
        import_confirmed_edges(scc, ledp, now=int(_time.time()), home=hag_home, approval_id=rid)
    except SyncError as e:
        blk = getattr(e, "reason", None) == "binding_mismatch:request_id"
    scc.close()
    cons, _ = _consumed(ledp)
    ck("hag_approval_payload_mismatch_blocked(evidence 바인딩·C1·edges 0·consume 0)",
       blk and _ecount(ledp) == 0 and cons == 0)

    # ── hag_approval_ledger_mismatch_blocked — 다른 ledger_id event ──
    ledp, sdb, scc, ek = _fresh("hag_ledgermis")
    rid, digest, lid = _rid_for(ledp)
    tn = _time.time()
    ta.append_event(hag_home, {"request_id": rid, "protocol_version": ta.PROTOCOL_VERSION,
                               "operation": "import_edges", "payload_digest": digest,
                               "ledger_id": "OTHER_LEDGER_ID", "approval_nonce": "ledgermis_" + "a" * 20,
                               "approved_at": tn, "expires_at": tn + 900, "record_type": "approve"})
    blk = False
    try:
        import_confirmed_edges(scc, ledp, now=int(tn), home=hag_home, approval_id=rid)
    except SyncError as e:
        blk = getattr(e, "reason", None) == "binding_mismatch:ledger"
    scc.close()
    ck("hag_approval_ledger_mismatch_blocked(ledger 바인딩·edges 0)", blk and _ecount(ledp) == 0)

    # ── hag_approval_expired_blocked — 만료된 approve event ──
    ledp, sdb, scc, ek = _fresh("hag_expired")
    rid, digest, lid = _rid_for(ledp)
    past = _time.time() - 10000
    ta.append_event(hag_home, {"request_id": rid, "protocol_version": ta.PROTOCOL_VERSION,
                               "operation": "import_edges", "payload_digest": digest, "ledger_id": lid,
                               "approval_nonce": "expired_" + "a" * 20,
                               "approved_at": past, "expires_at": past + 60, "record_type": "approve"})
    blk = False
    try:
        import_confirmed_edges(scc, ledp, now=int(_time.time()), home=hag_home, approval_id=rid)
    except SyncError as e:
        blk = getattr(e, "reason", None) == "approval_expired"
    scc.close()
    ck("hag_approval_expired_blocked(만료 TTL·edges 0)", blk and _ecount(ledp) == 0)

    # ── hag_failed_import_does_not_consume — write 단계 실패 → rollback + release(승인 소각 0) ──
    ledp, sdb, scc, ek = _fresh("hag_failed")
    rid = _request_mint(scc, ledp)
    scc.close()
    sc_ro = sqlite3.connect("file:%s?mode=ro" % sdb, uri=True)   # sync UPDATE 실패 유도(read-only)
    blk = False
    try:
        import_confirmed_edges(sc_ro, ledp, now=int(_time.time()), home=hag_home, approval_id=rid)
    except SyncError as e:
        blk = getattr(e, "reason", None) == "import_write_failed"
    sc_ro.close()
    cons, total = _consumed(ledp)
    ck("hag_failed_import_does_not_consume(edges 0·consume 0·nonce 재사용 가능)",
       blk and _ecount(ledp) == 0 and cons == 0 and total == 0)

    # ── hag_actor_is_audit_metadata_only(실증) — 정상 승인 + actor='reader'(정상 차단 actor)라도
    #    import 됨(actor 는 authz 미관여) · receipt 에 actor 기록만 ──
    ledp, sdb, scc, ek = _fresh("hag_actor_meta")
    rid = _request_mint(scc, ledp)
    r = import_confirmed_edges(scc, ledp, now=int(_time.time()), home=hag_home,
                               approval_id=rid, actor="reader")
    o = sqlite3.connect(ledp)
    rcpt = o.execute("SELECT receipt FROM approval_consumptions WHERE state='consumed'").fetchone()[0]
    o.close()
    scc.close()
    ck("hag_actor_is_audit_metadata_only_effect(reader 도 승인 있으면 import·receipt 에 actor 기록)",
       r["imported"] == 1 and r.get("receipt_actor") == "reader" and '"actor": "reader"' in rcpt)

    # ── hag_empty_confirmed_no_op — 빈 confirmed 집합 = 승인 불요 no-op(A-5·T13 정합) ──
    ledp_e, sdb_e, scc_e, _ = _fresh("hag_empty", with_nodes=True)
    scc_e.execute("DELETE FROM sync_edges")           # confirmed 0
    scc_e.commit()
    _disable()                                         # provider 없어도 no-op(write 0·에러 0)
    re_ = import_confirmed_edges(scc_e, ledp_e, now=1, home=hag_home)
    scc_e.close()
    ck("hag_empty_confirmed_no_op(승인 불요·edges 0)",
       re_.get("no_op") is True and re_["imported"] == 0 and _ecount(ledp_e) == 0)

    # ── hag_dangling_only_no_op — confirmed 존재하나 전부 dangling → effective ∅ → no-op ──
    ledp_d, sdb_d, scc_d, _ = _fresh("hag_dangling", with_nodes=False)   # 운영 노드 0 → 전부 dangling
    rd = import_confirmed_edges(scc_d, ledp_d, now=1, home=hag_home)
    scc_d.close()
    ck("hag_dangling_only_no_op(전부 dangling → 승인 불요 no-op·edges 0)",
       rd.get("no_op") is True and rd["imported"] == 0 and rd["dangling"] >= 1 and _ecount(ledp_d) == 0)
    _enable()

    # T17 list_view 운영 sentence 조인
    sc3 = open_sync_db(os.path.join(tmp, "sync3.sqlite"))
    record_proposals(sc3, build_proposals(snapshot=snap, semantic=False), now=0)
    lv = list_view(sc3, led)
    ck("T17 list_view sentence 조인", len(lv) == 4 and all("src" in x and "edge_key" in x for x in lv))
    sc3.close()

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    gate = "GO" if passed == total else "NO-GO"
    print("\nselftest: %d/%d\nGATE: %s" % (passed, total, gate))
    return passed == total


def main(argv=None):
    import argparse
    import time
    try:
        import binggu_platform as P
        home = P.binggu_home()
    except Exception:
        home = os.path.expanduser("~/.binggupack")
    def_ledger = os.path.join(home, "ledger.sqlite")
    def_sync = os.path.join(home, "sync_edges.sqlite")
    p = argparse.ArgumentParser(prog="hag_sync_adapter",
                                description="운영 ledger ↔ blind 동기화 어댑터(단방향·사람 도장)")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--build", action="store_true", help="운영 스냅샷→edge proposal 생성·기록(read-only 생성)")
    p.add_argument("--list", action="store_true", help="후보 목록(sentence 조인)")
    p.add_argument("--confirm", metavar="EDGE_KEY", help="사람 도장 proposed→confirmed (owner)")
    p.add_argument("--import-edges", action="store_true",
                   help="confirmed→운영 edges 등재(owner-only·운영 write·exact-bound approval 필수)")
    p.add_argument("--approval-id", default=None,
                   help="--import-edges 용 trusted approval id(owner 가 binggu approval approve 로 발행). "
                        "미제시 시 PENDING 요청만 생성하고 승인 방법 안내 후 종료(fail-closed).")
    p.add_argument("--status", help="--list 필터(proposed/confirmed/imported)")
    p.add_argument("--ledger", default=def_ledger)
    p.add_argument("--sync-db", default=def_sync)
    p.add_argument("--actor", default="human",
                   help="감사 metadata 표기용(승인 권한 없음 · --confirm 사람 도장 게이트에만 human 필요).")
    a = p.parse_args(argv)

    if a.selftest:
        return 0 if _selftest() else 1
    now = int(time.time())
    if a.build:
        props = build_proposals(a.ledger)
        sc = open_sync_db(a.sync_db)
        r = record_proposals(sc, props, now=now)
        sc.close()
        print("build: snapshot→%d proposals · %s" % (len(props), r))
        return 0
    if a.list:
        sc = open_sync_db(a.sync_db)
        rows = list_view(sc, a.ledger, status=a.status)
        sc.close()
        for x in rows:
            print("[%-9s] %s\n    %s --(%s)--> %s" % (x["status"], x["edge_key"][:16],
                                                      x["src"], x["relation"], x["dst"]))
        print("총 %d건" % len(rows))
        return 0
    if a.confirm:
        sc = open_sync_db(a.sync_db)
        try:
            r = confirm_edge(sc, a.confirm, actor=a.actor, now=now)
            print("confirm:", r)
        except SyncError as e:
            print("BLOCK:", e)
            return 2
        finally:
            sc.close()
        return 0
    if a.import_edges:
        sc = open_sync_db(a.sync_db)
        try:
            # A4: 운영 write = exact-bound approval 전용. actor 는 감사 metadata(승인 권한 0).
            r = import_confirmed_edges(sc, a.ledger, now, home=home,
                                       approval_id=a.approval_id, actor=a.actor)
            print("import(owner-only·approval-bound):", r)
        except SyncError as e:
            reason = getattr(e, "reason", None)
            if reason == "approval_required":
                rid = getattr(e, "request_id", None)
                print("승인 필요(운영 write 는 exact-bound approval 전용):")
                print("  1) 검토:  binggu approval show %s" % rid)
                print("  2) 승인:  binggu approval approve %s" % rid)
                print("  3) 재실행: --import-edges --approval-id %s" % rid)
                return 2
            print("BLOCK:", e)
            return 2
        finally:
            sc.close()
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    if not sys.argv[1:]:
        sys.exit(0 if _selftest() else 1)
    sys.exit(main())
