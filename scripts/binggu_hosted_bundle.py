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

from openbinggu_save_intent_outbox_runner import (SCHEMA_VER, DEFAULT_TTL_S,  # noqa: E402
                                                  intent_hash, _CONFIRM_RE, OPERATING_PATHS)
import openbinggu_conversation_candidate_save as _convsave  # noqa: E402
from openbinggu_p3_self_improve import rollback_to_snapshot  # noqa: E402
from binggupack.mcp import approval_gate  # noqa: E402
from binggupack.safety import trusted_approval as ta  # noqa: E402


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
    """선택 intent 로드 + pre-validate. 유효=items, 모호/실패=quarantined(원문 보존·계약 12).
    membership = selected 만(계약 1). 중복 intent_id 는 1회로 접음."""
    items, quarantined, seen = [], [], set()
    for iid in selected_intent_ids:
        if iid in seen:
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
    return {"items": items, "quarantined": quarantined}


def _bind(items, approval_id=None):
    """authorize 에 넘길 payload — trusted_approval.binding_fields('hosted_bundle') 가 각 intent 의
    save_candidate digest + bundle digest 로 캐논화(raw 는 digest 재료일 뿐 store 미저장·계약 13)."""
    b = {"items": [{"intent_id": it["intent_id"], "text": it["text"],
                    "indices": it["indices"], "speaker": it.get("speaker")} for it in items]}
    if approval_id is not None:
        b["approval_id"] = approval_id
    return b


def _archive(staging_dir, intent_id, meta, archive_dir):
    """성공 저장된 intent 원문을 archive(processed) — staging→archive 이동. 원문 삭제 아님(계약 15):
    archive 파일에 원문 body + provenance 보존. 실제 삭제는 별도 owner purge 만(계약 16)."""
    os.makedirs(archive_dir, exist_ok=True)
    src = os.path.join(staging_dir, intent_id + ".json")
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
    dst = os.path.join(archive_dir, intent_id + ".processed.json")
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=1)
    os.replace(tmp, dst)
    if os.path.isfile(src):
        os.remove(src)   # archive(dst)로 이전 완료 후에만 staging 원본 제거 = 이동(원문 보존)
    return dst


def _existing_node_ids(it):
    """idempotent(이미 저장) intent 의 node_id 재구성 — provenance 열화 방지(Fable5 사후 R3-5)."""
    try:
        return ta._derive_save_node_ids({"text": it.get("text", ""),
                                         "indices": it.get("indices", []), "explicit": False})
    except Exception:
        return []


def _fail_audit(db, rid, reason):
    """실패한 bundle write 를 tamper-evident audit_log 에 1행 기록(Fable5 사후 R3-1 · 사일런트 실패 0).
    rollback_to_snapshot 후 호출(재오픈된 con 에 append)."""
    try:
        ck = db.store_checksum()
        db.audit_append("reader", "hosted_bundle_fail", str(rid or ""), "BLOCK",
                        str(reason)[:80], ck, ck)
    except Exception:
        pass


def commit_bundle(db, home, staging_dir, selected_intent_ids, approval_id, snap_dir, now_ts,
                  archive_dir=None):
    """묶음 exact-bound 저장. approval_id 없으면 PENDING(원문 보존). 있으면 verify→snapshot→
    전체 save→finalize/rollback(atomic all-or-nothing). owner 16계약.

    반환 {applied(저장건수), write(0/1), reason, request_id, bundle_id, receipt?, archived?, quarantined}.
    """
    if archive_dir is None:
        archive_dir = os.path.join(staging_dir, "_archive")
    bl = build_bundle(staging_dir, selected_intent_ids, now_ts)
    items, quarantined = bl["items"], bl["quarantined"]
    bid = bundle_id_of([it["intent_id"] for it in items]) if items else None
    if not items:
        # 전부 quarantine — write 0 · 원문 보존(계약 12)
        return {"applied": 0, "write": 0, "reason": "no_valid_intent",
                "bundle_id": bid, "quarantined": quarantined}

    bind = _bind(items, approval_id)
    with approval_gate.authorize("hosted_bundle", bind, home, db) as auth:
        rid = auth.request_id
        if auth.actor != "human":
            # PENDING(approval_required) · provider 미구성 · 바인딩 불일치 · already_consumed(재시도).
            # 어느 경우든 write 0 · 원문 보존(계약 11,14). already_consumed 면 receipt 반환(계약 8·재write 0).
            auth.settle({"applied": False, "reason": auth.reason})
            extra = auth.response_extra()
            return {"applied": 0, "write": 0, "reason": extra.get("reason"),
                    "request_id": rid, "bundle_id": bid, "quarantined": quarantined,
                    "receipt": extra.get("receipt"), "guidance": extra.get("guidance")}
        # reserved(human) — snapshot(reserve 행 포함) 후 atomic 저장
        snap = db.snapshot(snap_dir, "bundle_%s" % (rid or now_ts))
        saved, node_map, fail = 0, {}, None
        try:
            for it in items:
                confirm = "SAVE " + ",".join(str(i) for i in it["indices"])
                r = _convsave.save_selected(db, it["text"], it["indices"],
                                            {"actor": "human", "confirm": confirm}, snap_dir,
                                            speaker=it.get("speaker"), explicit=False)
                if r.get("applied"):
                    saved += 1
                    node_map[it["intent_id"]] = r.get("node_ids", [])
                elif r.get("reason") == "nothing_to_save" and r.get("skipped_existing", 0) > 0:
                    # 이미 저장됨(idempotent·계약 8) — provenance 열화 방지 위해 node_id 재구성(R3-5).
                    node_map[it["intent_id"]] = _existing_node_ids(it)
                else:
                    fail = {"intent_id": it["intent_id"], "reason": r.get("reason")}
                    break
        except Exception as _e:
            # ★R3-2(Fable5 사후): in-process 예외(디스크풀/sqlite 오류)도 rollback → 전체 write 0(계약 5).
            # validation-fail 뿐 아니라 예외 경로도 부분 bundle 을 남기지 않는다(계약 5 완전 강제).
            rollback_to_snapshot(db, snap)
            auth.settle({"applied": False, "reason": "bundle_exception"})
            _fail_audit(db, rid, "bundle_exception:%s" % type(_e).__name__)
            return {"applied": 0, "write": 0, "reason": "bundle_exception",
                    "request_id": rid, "bundle_id": bid, "quarantined": quarantined}
        if fail is not None:
            # 하나라도 hard-fail → rollback(이전 저장분 포함 전체 write 0·계약 5) + release(consume 0·계약 6)
            rollback_to_snapshot(db, snap)       # db.con 재오픈(nodes 원복·reserve 행 복구)
            auth.settle({"applied": False, "reason": "bundle_partial_fail:" + str(fail["reason"])})
            _fail_audit(db, rid, "bundle_partial_fail:" + str(fail["reason"]))   # ★R3-1 실패 write audit
            return {"applied": 0, "write": 0, "reason": "bundle_partial_fail",
                    "fail": fail, "request_id": rid, "bundle_id": bid, "quarantined": quarantined}
        # 전부 applied/idempotent → finalize(consume·계약 7)
        node_ids = [nid for nids in node_map.values() for nid in nids]
        auth.settle({"applied": True, "saved": saved, "node_ids": node_ids, "reason": None})
        extra = auth.response_extra()
    receipt = extra.get("receipt") or {}
    receipt_id = receipt.get("request_id") or rid
    # 원문 archive(processed·삭제 0·계약 15) + provenance(계약 10). 미선택은 staging PENDING(계약 9).
    archived = []
    for it in items:
        meta = {"source_intent_id": it["intent_id"], "bundle_id": bid,
                "approval_id": approval_id, "receipt_id": receipt_id,
                "node_ids": node_map.get(it["intent_id"], []), "processed_ts": now_ts}
        archived.append(_archive(staging_dir, it["intent_id"], meta, archive_dir))
    return {"applied": saved, "write": 1, "reason": None, "receipt": receipt,
            "request_id": rid, "bundle_id": bid, "archived": archived,
            "quarantined": quarantined}


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

    # 4) 재시도(같은 approval_id) → already_consumed · 재write 0(계약 8)
    #    (원문은 이미 archive 이동 → build_bundle intent_not_found · 그래도 재저장 0)
    r4 = commit_bundle(db, home, staging, [i1, i2], rid, snap, NOW)
    n_active2 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    ck(r4["write"] == 0 and n_active2 == n_active, "4 재시도 → 재write 0(계약 8)")

    # 5) all-or-nothing — 2건 중 1건 PII → 전체 write 0 · rollback · consume 0
    i5a, i5b = mk(staging, S[2], [1]), mk(staging, PII, [1])
    r5pending = commit_bundle(db, home, staging, [i5a, i5b], None, snap, NOW)
    rid5 = r5pending["request_id"]
    approve(db, home, rid5)
    n_before5 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    r5 = commit_bundle(db, home, staging, [i5a, i5b], rid5, snap, NOW)
    n_after5 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    both_preserved = all(os.path.isfile(os.path.join(staging, x + ".json")) for x in (i5a, i5b))
    fail_audit5 = db.con.execute(
        "SELECT count(*) FROM audit_log WHERE action='hosted_bundle_fail' AND result='BLOCK'").fetchone()[0]
    ck(r5["write"] == 0 and r5["reason"] == "bundle_partial_fail" and n_after5 == n_before5
       and both_preserved and fail_audit5 >= 1,
       "5 all-or-nothing: 1건 PII → 전체 write 0 · rollback · 원문 보존 · 실패 audit(R3-1)")

    # 6) rollback 후 approval 재사용 가능(consume 0) — PII intent 제외하고 재선택 → 저장
    r6 = commit_bundle(db, home, staging, [i5a], rid5, snap, NOW)
    ck(r6["write"] == 0 and r6["reason"] in ("binding_mismatch:request_id", "approval_required"),
       "6 membership 변경(부분) → 기존 승인 무효(계약 2·3)")

    # 7) quarantine — 변조 intent(재해시 불일치) → quarantine · write 0
    i7 = mk(staging, S[0], [1])
    p7 = os.path.join(staging, i7 + ".json")
    b7 = json.load(open(p7, encoding="utf-8")); b7["text"] = b7["text"] + " 변조."
    json.dump(b7, open(p7, "w", encoding="utf-8"), ensure_ascii=False)
    r7 = commit_bundle(db, home, staging, [i7], None, snap, NOW)
    ck(r7["write"] == 0 and any(q["reason"] == "intent_id_mismatch" for q in r7["quarantined"])
       and os.path.isfile(p7), "7 변조 intent → quarantine · write 0 · 원문 보존")

    # 7b) R3-2(Fable5 사후) — in-process 예외(save_selected raise)도 rollback → 전체 write 0 · 원문 보존 · bundle_exception audit
    i8a = mk(staging, "이 방침은 다음 분기에 재검토하기로 결정했다.", [1])
    i8b = mk(staging, "예산은 보수적으로 확정한다.", [1])
    r8p = commit_bundle(db, home, staging, [i8a, i8b], None, snap, NOW)
    rid8 = r8p["request_id"]
    approve(db, home, rid8)
    n_b8 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    _orig_ss, _calls = _convsave.save_selected, {"n": 0}

    def _boom(*a, **k):
        _calls["n"] += 1
        if _calls["n"] >= 2:
            raise RuntimeError("injected disk/sqlite error")
        return _orig_ss(*a, **k)
    try:
        _convsave.save_selected = _boom
        r8 = commit_bundle(db, home, staging, [i8a, i8b], rid8, snap, NOW)
    finally:
        _convsave.save_selected = _orig_ss
    n_a8 = db.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    exc_audit = db.con.execute(
        "SELECT count(*) FROM audit_log WHERE reason_code LIKE 'bundle_exception%'").fetchone()[0]
    both8 = all(os.path.isfile(os.path.join(staging, x + ".json")) for x in (i8a, i8b))
    ck(r8["write"] == 0 and r8["reason"] == "bundle_exception" and n_a8 == n_b8 and both8 and exc_audit >= 1,
       "7b R3-2 in-process 예외 → rollback · 전체 write 0 · 원문 보존 · bundle_exception audit")

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
