# -*- coding: utf-8 -*-
"""binggu_hosted_bundle.py — hosted 묶음 승인(bundle) exact-bound 저장 (owner 16계약 · P1-B A3).

owner 결정(2026-07-11): 휴대폰→PC 저장의 영구 write 는 PC 의 exact-bound 로컬 승인 이벤트 이후에만
정확히 한 번. 명시 선택한 intent 를 immutable bundle(전체 digest + 각 intent digest 바인딩)로 만들어
한 번 승인 → atomic all-or-nothing 저장. 부분저장 금지 · 원문 자동삭제 0.

16계약:
  1 선택 intent 만 포함 · 2 요청 후 membership 수정 금지(digest 고정) · 3 수정 = revoke/supersede + 새 요청
  4 op + stable ledger_id + protocol + request_id + 각 intent digest + 전체 bundle digest + expiry 바인딩
  5 하나라도 mismatch/validation 실패 → 전체 write 0 · 6 실패 시 approval consume 0
  7 성공 시 bundle 전체 write + consume 을 동일(논리) 트랜잭션에서 확정
  8 재시도는 original receipt 만 반환 · second write 0 · 9 미선택 intent = 원문과 함께 PENDING 유지
  10 provenance(source_intent_id, bundle_id, approval_id, receipt_id) · 11 legacy(confirm/SAVE/actor=human)
     = PENDING approval request 만 생성 · 직접 write 0 · 12 payload 모호(exact binding 불가) = quarantine + write 0 + 원문 보존
  13 approval request 에 raw conversation 복제 0 · source reference + digest 만 · 14 어떤 상태서도 원문 자동삭제 0
  15 성공 후 원문 = processed/archived 전환(삭제 아님) · 16 삭제 = 별도 명시 owner purge 만

정직 경계: 로컬 TTY 승인 assurance = L1. shell/PTY 가능 agent 상대 hard security 아님(SECURITY.md · Track B RFC).
CLI: python binggu_hosted_bundle.py --selftest   (temp 전용)
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
_BASE = os.path.dirname(HERE)
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

import time as _t  # noqa: E402
from openbinggu_save_intent_outbox_runner import (SCHEMA_VER, DEFAULT_TTL_S,  # noqa: E402
                                                  intent_hash, _CONFIRM_RE, OPERATING_PATHS)
import openbinggu_conversation_candidate_save as _convsave  # noqa: E402
from openbinggu_staging_write_selftest import (apply_pack_in_txn,  # noqa: E402
                                               _now_iso as _sw_now_iso, _hash as _sw_hash)
from binggupack.mcp import approval_gate  # noqa: E402
from binggupack.safety import trusted_approval as ta  # noqa: E402
from binggupack.storage.schema import ledger_id as _ledger_id  # noqa: E402


# ── 테스트 전용 crash failpoint (운영 기본 미설정 = no-op) ─────────────────────────────
def _failpoint(name):
    """BINGGU_BUNDLE_FAILPOINT 환경변수와 일치하면 os._exit(91) = hard crash 주입.
    단일 COMMIT 경계의 crash-atomicity 를 subprocess 로 증명(handled exception 아님)."""
    if os.environ.get("BINGGU_BUNDLE_FAILPOINT") == name:
        os._exit(91)


def bundle_id_of(intent_ids):
    """정렬 intent_id 집합의 안정 bundle_id(표시·provenance 용). request_id 와 별개(계약 10)."""
    raw = "|".join(sorted(intent_ids))
    return "bundle:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _load_intent(staging_dir, intent_id):
    p = os.path.join(staging_dir, intent_id + ".json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            it = json.load(f)
    except Exception:
        return None
    return it if isinstance(it, dict) else None


def _prevalidate(it, now_ts):
    """intent pre-validate — schema/필드/TTL/intent_id 재해시/confirm 형식. 반환 (ok, reason).
    exact binding 불가(모호)면 quarantine(계약 12). PII 최종 차단은 저장 게이트(save_selected)."""
    if it.get("schema_ver") != SCHEMA_VER:
        return False, "schema_mismatch"
    text, indices, confirm = it.get("text"), it.get("indices"), it.get("confirm")
    if (not isinstance(text, str) or not isinstance(indices, list)
            or not indices
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in indices)
            or not isinstance(confirm, str)
            or not isinstance(it.get("created_ts"), int)
            or not isinstance(it.get("ttl_s", DEFAULT_TTL_S), int)):
        return False, "malformed_intent"
    if now_ts - it["created_ts"] > it.get("ttl_s", DEFAULT_TTL_S):
        return False, "expired"
    if it.get("intent_id") != intent_hash(text, indices, confirm):
        return False, "intent_id_mismatch"
    expected = "SAVE " + ",".join(str(i) for i in indices)
    if confirm != expected or not _CONFIRM_RE.fullmatch(confirm):
        return False, "confirm_phrase_mismatch"
    return True, None


def build_bundle(staging_dir, selected_intent_ids, now_ts):
    """선택 intent 로드 + pre-validate. 유효=items, 실패=quarantined(원문 보존·계약 12).
    membership = selected 만(계약 1). ★P1-B.1: 암묵적 dedupe 폐기 — 중복 intent_id 는
    quarantine('duplicate_selection')로 표면화(명시 계약). exact user selection = 봉인."""
    items, quarantined, seen = [], [], set()
    for iid in selected_intent_ids:
        if iid in seen:
            quarantined.append({"intent_id": iid, "reason": "duplicate_selection"})
            continue
        seen.add(iid)
        it = _load_intent(staging_dir, iid)
        if it is None:
            quarantined.append({"intent_id": iid, "reason": "intent_not_found"})
            continue
        ok, reason = _prevalidate(it, now_ts)
        if not ok:
            quarantined.append({"intent_id": iid, "reason": reason})
            continue
        items.append({"intent_id": iid, "text": it["text"], "indices": it["indices"],
                      "speaker": it.get("speaker")})
    return {"items": items, "quarantined": quarantined,
            "selected_count": len(selected_intent_ids)}


def _bind(items, approval_id=None):
    """authorize 에 넘길 payload — trusted_approval.binding_fields('hosted_bundle') 가 각 intent 의
    save_candidate digest + bundle digest 로 캐논화(raw 는 digest 재료일 뿐 store 미저장·계약 13)."""
    b = {"items": [{"intent_id": it["intent_id"], "text": it["text"],
                    "indices": it["indices"], "speaker": it.get("speaker")} for it in items]}
    if approval_id is not None:
        b["approval_id"] = approval_id
    return b


def _archive_member(staging_dir, intent_id, meta, archive_dir):
    """저장된 intent 원문을 archive(processed) — staging→archive 이동. 원문 삭제 아님(계약 15).
    ★P1-B.1: idempotent post-commit reconciliation. dst 이미 존재 + src 부재 = 이미 archive 완료
    → 재기록 0(원 archive 보존). archive 실패는 예외를 올려 호출자가 archive_pending 처리(§4)."""
    os.makedirs(archive_dir, exist_ok=True)
    src = os.path.join(staging_dir, intent_id + ".json")
    dst = os.path.join(archive_dir, intent_id + ".processed.json")
    if os.path.isfile(dst) and not os.path.isfile(src):
        return dst   # 이미 archive 됨(idempotent) — 재기록/재이동 0
    try:
        if os.path.isfile(src):
            with open(src, "r", encoding="utf-8") as f:
                body = json.load(f)
        else:
            body = {}
    except Exception:
        body = {}
    body["_provenance"] = meta
    body["_status"] = "processed"
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)
    if os.path.isfile(src):
        os.remove(src)   # archive(dst)로 이전 완료 후에만 staging 원본 제거 = 이동(원문 보존)
    return dst


def _trim_receipt(receipt):
    """응답용 receipt(nonce/내부필드 제외). request_id/node_ids/decision_id 만(TAE-6)."""
    receipt = receipt or {}
    return {"request_id": receipt.get("request_id"),
            "node_ids": receipt.get("node_ids"),
            "decision_id": receipt.get("decision_id")}


def _reconcile_archive(staging_dir, members, base_meta, archive_dir):
    """receipt.members 를 idempotent archive(§4·§5). 반환 (archived, archive_pending)."""
    archived, pending = [], []
    for m in members or []:
        iid = m.get("intent_id")
        if not iid:
            continue
        meta = dict(base_meta, source_intent_id=iid, node_ids=m.get("node_ids", []))
        try:
            archived.append(_archive_member(staging_dir, iid, meta, archive_dir))
        except Exception as _ae:
            pending.append({"intent_id": iid, "error": type(_ae).__name__})
    return archived, pending


def _fail_audit(db, rid, reason):
    """실패한 bundle write 를 tamper-evident audit_log 에 1행 기록(Fable5 사후 R3-1 · 사일런트 실패 0).
    txn ROLLBACK 후 호출(자체 commit)."""
    try:
        ck = db.store_checksum()
        db.audit_append("reader", "hosted_bundle_fail", str(rid or ""), "BLOCK",
                        str(reason)[:80], ck, ck)
    except Exception:
        pass


def commit_bundle(db, home, staging_dir, selected_intent_ids, approval_id, snap_dir, now_ts,
                  archive_dir=None):
    """묶음 exact-bound 저장(owner 16계약 · P1-B.1 crash-atomic).

    흐름: ①Contract-8(approval_id 이미 consumed → original receipt·reconcile only)
    ②build_bundle(source load) ③H1 exact membership(하나라도 invalid/dup → 전체 BLOCK·authorize 전)
    ④approval_id 없음 → PENDING 요청 ⑤Phase1 prepare(DB write 0·하나라도 hard-fail → BLOCK·reserve 전)
    ⑥Phase2 authorize/reserve ⑦Phase3 단일 BEGIN IMMEDIATE(전 intent insert + finalize_consumed +
    approval_requests consumed + 성공 audit → COMMIT 정확히 1회) ⑧post-commit idempotent archive.

    crash 규칙: COMMIT 이전 kill → ledger write 0·approval consume 0(예약은 stale → lease 회복).
    COMMIT 이후 kill → ledger 전체 write + consumed receipt. 부분 bundle 은 어떤 재오픈 시점에도 없음.
    archive(filesystem)는 DB 트랜잭션과 분리 = post-commit reconciliation(실패해도 ledger 성공 불변).

    반환 {applied, write(0/1), reason, request_id, bundle_id, receipt?, archived?, archive_pending?,
          quarantined, selected_count, validated_count, executed_write}.
    """
    if archive_dir is None:
        archive_dir = os.path.join(staging_dir, "_archive")

    # ── ① Contract-8: source load 전 consumption receipt 우선 조회(재시도 멱등·§5) ──────────────
    if approval_id:
        prior = ta.get_consumption(db.con, approval_id)
        if prior is not None:
            receipt = prior["receipt"] or {}
            base_meta = {"bundle_id": receipt.get("bundle_id"), "approval_id": approval_id,
                         "receipt_id": receipt.get("request_id"), "processed_ts": now_ts,
                         "reconciled": True}
            archived, pending = _reconcile_archive(staging_dir, receipt.get("members"),
                                                   base_meta, archive_dir)
            return {"applied": 0, "write": 0, "reason": "already_consumed",
                    "request_id": approval_id, "bundle_id": receipt.get("bundle_id"),
                    "receipt": _trim_receipt(receipt), "archived": archived,
                    "archive_pending": pending, "quarantined": [], "executed_write": False}

    # ── ② source load + pre-validate ───────────────────────────────────────────────────────
    bl = build_bundle(staging_dir, selected_intent_ids, now_ts)
    items, quarantined = bl["items"], bl["quarantined"]
    bid = bundle_id_of([it["intent_id"] for it in items]) if items else None

    # ── ③ H1 exact membership: 선택 중 하나라도 invalid/dup → 전체 BLOCK(authorize/reserve/write 0) ─
    if quarantined:
        return {"applied": 0, "write": 0, "reason": "bundle_prevalidation_failed",
                "selected_count": bl["selected_count"], "validated_count": len(items),
                "quarantined": quarantined, "bundle_id": bid, "executed_write": False}
    if not items:
        return {"applied": 0, "write": 0, "reason": "no_valid_intent",
                "selected_count": bl["selected_count"], "validated_count": 0,
                "quarantined": quarantined, "bundle_id": bid, "executed_write": False}

    bind = _bind(items, approval_id)

    # ── ④ approval_id 없음 → PENDING 승인 요청만(직접 write 0·원문 보존·계약 11). prevalidation 통과 intent 는
    #   요청 생성 · 저장 게이트(PII/a0/index) 실패는 commit(승인 후) 단계에서 전체 차단(§2·SECURITY §8 2계층).
    if not approval_id:
        with approval_gate.authorize("hosted_bundle", bind, home, db) as auth:
            rid = auth.request_id
            auth.settle({"applied": False, "reason": auth.reason})
            extra = auth.response_extra()
        return {"applied": 0, "write": 0, "reason": extra.get("reason"),
                "request_id": rid, "bundle_id": bid, "quarantined": quarantined,
                "selected_count": bl["selected_count"], "validated_count": len(items),
                "guidance": extra.get("guidance"), "executed_write": False}

    # ── binding pre-check: 제시된 approval_id 가 현 membership 의 request_id 와 다르면 조기 차단 ──
    #   (membership 팽창/축소·잘못된 approval_id). read-only·prepare/reserve 전. binding_fields 는 approval_id 제외.
    try:
        _digest = ta.canonical_payload_digest("hosted_bundle", bind)
        _expected_rid = ta.compute_request_id("hosted_bundle", _digest, _ledger_id(db.con))
    except ta.ControlCharReject:
        return {"applied": 0, "write": 0, "reason": "binding_reject:control_char",
                "bundle_id": bid, "quarantined": quarantined, "executed_write": False}
    except Exception:
        return {"applied": 0, "write": 0, "reason": "binding_error",
                "bundle_id": bid, "quarantined": quarantined, "executed_write": False}
    if approval_id != _expected_rid:
        return {"applied": 0, "write": 0, "reason": "binding_mismatch:request_id",
                "request_id": _expected_rid, "bundle_id": bid, "quarantined": quarantined,
                "selected_count": bl["selected_count"], "validated_count": len(items),
                "executed_write": False}

    # ── ⑤ Phase 1: prepare all (DB persistent write 0). 하나라도 hard-fail → BLOCK(reserve 전) ──
    #   ★exact membership(intra-intent): 선택 index 중 하나라도 거부(index/a0/pii)면 부분 저장(silent subset
    #   shrink)이 되므로 — ok 이어도 rejected 가 있으면 hard-fail 로 취급해 전체 BLOCK(§2·계약 5).
    prepared, prep_fail = [], None
    for it in items:
        pr = _convsave.prepare_selected(db, it["text"], it["indices"],
                                        speaker=it.get("speaker"), explicit=False)
        if pr["ok"] and not pr["rejected"]:
            ch = _sw_hash(pr["pack"]["content"])
            dup = db.con.execute("SELECT 1 FROM applied_registry WHERE pack_id=? AND content_hash=?",
                                 (pr["pack"]["pack_id"], ch)).fetchone()
            pr["_new"] = not dup   # 이미 적재된 동일 pack = idempotent(재삽입 0)
            prepared.append((it, pr))
        elif (not pr["ok"] and pr["reason"] == "nothing_to_save"
              and pr["skipped_existing"] > 0 and not pr["rejected"]):
            pr["_new"] = False     # 전부 기존재(idempotent·계약 8)
            prepared.append((it, pr))
        else:
            # 하나라도 거부(부분 포함)/전부 거부 = hard-fail → 전체 BLOCK(계약 5·consume 0·원문 보존)
            _rr = next(iter(pr.get("rejected") or {}), None) or pr.get("reason") or "prepare_failed"
            prep_fail = {"intent_id": it["intent_id"], "reason": _rr, "rejected": pr.get("rejected")}
            break
    if prep_fail:
        return {"applied": 0, "write": 0, "reason": "bundle_prepare_failed", "fail": prep_fail,
                "selected_count": bl["selected_count"], "validated_count": len(items),
                "quarantined": quarantined, "bundle_id": bid, "executed_write": False}

    # ── ★within-bundle node_id cross-dedup: 서로 다른 두 intent 가 같은 문장(=같은 node_id)을 선택하면
    #   단일 트랜잭션에서 PK 충돌 → 형제가 이미 신규 저장 예정인 node 는 이 pack 에서 제거(멱등). membership
    #   (각 intent node_ids)은 유지해 receipt/archive 에 반영. hosted 반복 판단 문장 저장불가 회귀 봉인.
    seen_new = set()
    for _it, pr in prepared:
        if not pr["_new"]:
            continue
        pack = pr["pack"]
        remaining = [n for n in pack["nodes"] if n["id"] not in seen_new]
        if len(remaining) != len(pack["nodes"]):
            if not remaining:
                pr["_new"] = False   # 전부 형제 중복 → idempotent(형제 intent 가 저장)
            else:
                _keep = {n["id"] for n in remaining}
                _edges = [e for e in pack["edges"] if e["target"] in _keep]
                _eids = {e["source"] for e in _edges}
                _ev = [x for x in pack["evidence"] if x["id"] in _eids]
                _content = "\n".join(sorted(n["sentence"] for n in remaining))
                pr["pack"] = {"pack_id": "conv_" + _sw_hash(_content)[:8], "content": _content,
                              "nodes": remaining, "edges": _edges, "evidence": _ev}
        if pr["_new"]:
            seen_new |= {n["id"] for n in pr["pack"]["nodes"]}

    # membership/receipt (선택 유효 node_id 전체·중복 제거·계약 10)
    all_node_ids, _seen = [], set()
    for _it, pr in prepared:
        for nid in pr["node_ids"]:
            if nid not in _seen:
                _seen.add(nid); all_node_ids.append(nid)
    members = [{"intent_id": it["intent_id"], "node_ids": pr["node_ids"]} for it, pr in prepared]
    receipt = {"request_id": None, "operation": "hosted_bundle", "bundle_id": bid,
               "node_ids": all_node_ids, "members": members, "decision_id": None}

    # ── ⑥ Phase 2: authorize/reserve ───────────────────────────────────────────────────────
    with approval_gate.authorize("hosted_bundle", bind, home, db) as auth:
        rid = auth.request_id
        receipt["request_id"] = rid
        if auth.actor != "human":
            # binding 불일치 · verify 실패 · provider 미구성 · already_consumed(race). write 0 · 원문 보존.
            auth.settle({"applied": False, "reason": auth.reason})
            extra = auth.response_extra()
            return {"applied": 0, "write": 0, "reason": extra.get("reason"),
                    "request_id": rid, "bundle_id": bid, "quarantined": quarantined,
                    "receipt": extra.get("receipt"), "guidance": extra.get("guidance"),
                    "selected_count": bl["selected_count"], "validated_count": len(items),
                    "executed_write": False}
        nonce = auth._nonce
        con = db.con

        # ── ⑦ Phase 3: 단일 BEGIN IMMEDIATE — 전 intent insert + finalize + audit → COMMIT 1회 ──
        try:
            con.execute("BEGIN IMMEDIATE")
            ts_iso = _sw_now_iso(now_ts)
            saved = 0
            for k, (it, pr) in enumerate(prepared):
                if pr["_new"]:
                    b = db.store_checksum()
                    apply_pack_in_txn(db, pr["pack"], ts_iso)
                    a = db.store_checksum()
                    db.audit_append("human", "conv_save", pr["pack"]["pack_id"], "ALLOW",
                                    "hosted_bundle saved=%d" % len(pr["pack"]["nodes"]),
                                    b, a, commit=False)
                    saved += 1
                if k == 0:
                    _failpoint("mid_apply")   # 첫 insert 후 hard crash → txn 미커밋 → 부분 write 0
            # 승인 소비를 같은 트랜잭션 안에서 확정(§3 금지: 별도 consume COMMIT)
            ta.finalize_consumed(con, nonce, rid, receipt, _t.time(), commit=False)
            con.execute("UPDATE approval_requests SET state='consumed' WHERE request_id=?", (rid,))
            ck = db.store_checksum()
            db.audit_append("human", "approval_consume:hosted_bundle", rid, "ALLOW",
                            "receipt nodes=%d bundle=%s" % (len(all_node_ids), bid),
                            ck, ck, commit=False)
            _failpoint("before_commit")       # 모든 write 준비 후 COMMIT 직전 crash → ledger write 0
            con.execute("COMMIT")             # ★ 정확히 1회 — 여기 통과 = 전체 확정
            _failpoint("after_commit")        # COMMIT 후 archive 전 crash → 재시도 시 receipt 재사용
        except Exception as _e:
            try:
                con.execute("ROLLBACK")       # 단일 txn 원복 — 부분 bundle 0
            except Exception:
                pass
            _fail_audit(db, rid, "bundle_txn_exception:%s" % type(_e).__name__)
            # auth._nonce 유지 → with __exit__ 이 release(예약 해제·승인 재사용 가능·소각 0)
            return {"applied": 0, "write": 0, "reason": "bundle_exception",
                    "request_id": rid, "bundle_id": bid, "quarantined": quarantined,
                    "selected_count": bl["selected_count"], "validated_count": len(items),
                    "executed_write": False}
        # 성공 — __exit__ 의 release 억제(이미 consumed) + PENDING 검토 레코드 정리
        auth._nonce = None
    try:
        ta.purge_review(home, rid)   # post-commit 정리 — 실패해도 커밋된 저장을 뒤집지 않음(§4)
    except Exception:
        pass

    # ── ⑧ post-commit: idempotent archive(원문 삭제 0·계약 15·§4 DB 트랜잭션과 분리) ──────────────
    base_meta = {"bundle_id": bid, "approval_id": approval_id, "receipt_id": rid,
                 "processed_ts": now_ts}
    archived, pending = _reconcile_archive(staging_dir, members, base_meta, archive_dir)
    return {"applied": saved, "write": 1 if saved > 0 else 0,
            "reason": None if saved > 0 else "idempotent_already_applied",
            "receipt": _trim_receipt(receipt), "request_id": rid, "bundle_id": bid,
            "archived": archived, "archive_pending": pending, "quarantined": quarantined,
            "selected_count": bl["selected_count"], "validated_count": len(items),
            "executed_write": saved > 0}


# ---------------- selftest (temp 전용 · 운영 홈 미접촉) ----------------
def _selftest():
    from openbinggu_deprecate_and_remind_g3 import open_g3
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    NOW = 1_900_000_000
    S = ["이 입찰은 마진이 낮아 보류하기로 결정했다.",
         "백업은 항상 작업 전에 먼저 해 둔다.",
         "캐시 전략은 이걸로 확정한다."]
    PII = "담당자 연락처는 010-" + "1234-5678 이고 마진이 낮아 보류한다."

    def mk(staging, text, idxs, created=NOW - 10):
        confirm = "SAVE " + ",".join(str(i) for i in idxs)
        it = {"schema_ver": SCHEMA_VER, "text": text, "indices": idxs, "confirm": confirm,
              "intent_id": intent_hash(text, idxs, confirm),
              "created_ts": created, "ttl_s": DEFAULT_TTL_S, "source": "hosted"}
        with open(os.path.join(staging, it["intent_id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(it, f, ensure_ascii=False)
        return it["intent_id"]

    def enable_provider(home):
        os.makedirs(home, exist_ok=True)
        with open(ta.config_path(home), "w", encoding="utf-8") as f:
            json.dump({"enabled": True}, f)

    def approve(db, home, rid):
        """owner 승인 시뮬 — CLI TTY 검증은 selftest 밖. mint_approval 기본 채널(ship-guard 회피).
        ★ authorize 는 verify 에 실제 time.time() 을 쓰므로 mint 도 실제 시간으로(NOW=미래면 approval_time_invalid)."""
        import time as _t
        req = ta.get_request(db.con, rid)
        ta.mint_approval(home, req, 900, _t.time())   # 기본 channel="unverified_direct"(test_double 리터럴 금지)

    tmp = tempfile.mkdtemp(prefix="bgp_bundle_")
    home = os.path.join(tmp, ".binggupack")
    staging = os.path.join(home, "hosted_inbox")
    snap = os.path.join(home, "snapshots")
    os.makedirs(staging, exist_ok=True)
    os.makedirs(snap, exist_ok=True)
    ledger = os.path.join(home, "ledger.sqlite")
    db = open_g3(ledger)

    # 1) provider 미구성 → PENDING 아니고 fail-closed(approval 없음) · write 0 · 원문 보존
    i1, i2 = mk(staging, S[0], [1]), mk(staging, S[1], [1])
    r1 = commit_bundle(db, home, staging, [i1, i2], None, snap, NOW)
    ck(r1["write"] == 0 and r1["reason"] == "provider_not_configured"
       and os.path.isfile(os.path.join(staging, i1 + ".json")), "1 provider 미구성 → write 0 · 원문 보존")

    # 2) provider 활성 · approval_id 없음 → PENDING 생성 · write 0 · 원문 보존(계약 11)
    enable_provider(home)
    r2 = commit_bundle(db, home, staging, [i1, i2], None, snap, NOW)
    rid = r2["request_id"]
    ck(r2["write"] == 0 and r2["reason"] == "approval_required" and rid
       and os.path.isfile(os.path.join(staging, i1 + ".json")), "2 approval_id 없음 → PENDING · write 0 · 원문 보존")

    # 3) 승인 후 approval_id 제시 → atomic 저장(2건) · 원문 archive(삭제 아님) · provenance
    approve(db, home, rid)
    r3 = commit_bundle(db, home, staging, [i1, i2], rid, snap, NOW)
    n_active = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    arch_ok = all(not os.path.isfile(os.path.join(staging, x + ".json"))
                  and os.path.isfile(os.path.join(staging, "_archive", x + ".processed.json"))
                  for x in (i1, i2))
    prov = json.load(open(os.path.join(staging, "_archive", i1 + ".processed.json"), encoding="utf-8"))
    ck(r3["write"] == 1 and r3["applied"] == 2 and n_active >= 2 and arch_ok
       and prov["_provenance"]["bundle_id"] == r3["bundle_id"]
       and prov["_provenance"]["approval_id"] == rid, "3 승인 후 atomic 저장 2건 · archive · provenance")

    # 4) 재시도(같은 approval_id) → Contract-8 already_consumed · original receipt · 재write 0(계약 8)
    #    (원문은 이미 archive 이동 → build_bundle 이전에 get_consumption 이 receipt 반환·M1 봉인)
    r4 = commit_bundle(db, home, staging, [i1, i2], rid, snap, NOW)
    n_active2 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    ck(r4["write"] == 0 and r4["reason"] == "already_consumed" and r4.get("receipt")
       and r4["receipt"].get("request_id") == rid and n_active2 == n_active,
       "4 재시도 → already_consumed · original receipt · 재write 0(계약 8·M1)")

    # 5) exact membership — 2건 중 1건 PII → Phase1 prepare hard-fail → 전체 write 0 · reserve/consume 0 · 원문 보존
    i5a, i5b = mk(staging, S[2], [1]), mk(staging, PII, [1])
    r5pending = commit_bundle(db, home, staging, [i5a, i5b], None, snap, NOW)
    rid5 = r5pending["request_id"]
    approve(db, home, rid5)
    n_before5 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    r5 = commit_bundle(db, home, staging, [i5a, i5b], rid5, snap, NOW)
    n_after5 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    both_preserved = all(os.path.isfile(os.path.join(staging, x + ".json")) for x in (i5a, i5b))
    consumed5 = db.con.execute("SELECT count(*) FROM approval_consumptions WHERE state='consumed'"
                               " AND request_id=?", (rid5,)).fetchone()[0]
    ck(r5["write"] == 0 and r5["reason"] == "bundle_prepare_failed" and n_after5 == n_before5
       and both_preserved and consumed5 == 0,
       "5 exact membership: 1건 PII → 전체 write 0 · reserve/consume 0 · 원문 보존")

    # 6) membership 변경(부분 재선택) → 기존 승인 request_id 불일치 → write 0(계약 2·3)
    r6 = commit_bundle(db, home, staging, [i5a], rid5, snap, NOW)
    ck(r6["write"] == 0 and r6["reason"] in ("binding_mismatch:request_id", "approval_required"),
       "6 membership 변경(부분) → 기존 승인 무효(계약 2·3)")

    # 7) quarantine — 변조 intent(재해시 불일치) → bundle_prevalidation_failed · write 0
    i7 = mk(staging, S[0], [1])
    p7 = os.path.join(staging, i7 + ".json")
    b7 = json.load(open(p7, encoding="utf-8")); b7["text"] = b7["text"] + " 변조."
    json.dump(b7, open(p7, "w", encoding="utf-8"), ensure_ascii=False)
    r7 = commit_bundle(db, home, staging, [i7], None, snap, NOW)
    ck(r7["write"] == 0 and r7["reason"] == "bundle_prevalidation_failed"
       and any(q["reason"] == "intent_id_mismatch" for q in r7["quarantined"])
       and os.path.isfile(p7), "7 변조 intent → bundle_prevalidation_failed · write 0 · 원문 보존")

    # 7b) 단일 txn 내 in-process 예외(apply_pack_in_txn raise) → ROLLBACK · 전체 write 0 · 원문 보존 · consume 0
    global apply_pack_in_txn
    i8a = mk(staging, "이 계약은 조건이 유리하여 진행하기로 결정했다.", [1])
    i8b = mk(staging, "신규 거래처는 항상 신용조사를 먼저 한다.", [1])
    r8p = commit_bundle(db, home, staging, [i8a, i8b], None, snap, NOW)
    rid8 = r8p["request_id"]
    approve(db, home, rid8)
    n_b8 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    _orig_apply, _calls = apply_pack_in_txn, {"n": 0}

    def _boom(dbx, pack, ts):
        _calls["n"] += 1
        if _calls["n"] >= 2:
            raise RuntimeError("injected disk/sqlite error")
        return _orig_apply(dbx, pack, ts)
    try:
        apply_pack_in_txn = _boom
        r8 = commit_bundle(db, home, staging, [i8a, i8b], rid8, snap, NOW)
    finally:
        apply_pack_in_txn = _orig_apply
    n_a8 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    exc_audit = db.con.execute(
        "SELECT count(*) FROM audit_log WHERE reason_code LIKE 'bundle_txn_exception%'").fetchone()[0]
    both8 = all(os.path.isfile(os.path.join(staging, x + ".json")) for x in (i8a, i8b))
    consumed8 = db.con.execute("SELECT count(*) FROM approval_consumptions WHERE state='consumed'"
                               " AND request_id=?", (rid8,)).fetchone()[0]
    ck(r8["write"] == 0 and r8["reason"] == "bundle_exception" and n_a8 == n_b8 and both8
       and exc_audit >= 1 and consumed8 == 0,
       "7b 단일 txn 예외 → ROLLBACK · 전체 write 0 · 원문 보존 · consume 0 · txn_exception audit")

    ck(db.verify_chain(), "8 audit chain INTACT")
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0])
    ck(bad == 0, "9 candidate-only · confirmed 0")
    db.close()

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck(op_before == op_after, "10 운영 store 불변")
    shutil.rmtree(tmp, ignore_errors=True)
    ck(not os.path.exists(tmp), "11 temp 정리")

    print("\nGATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(_selftest())
    print("usage: binggu_hosted_bundle.py [--selftest]  (temp 전용)")
    sys.exit(2)
