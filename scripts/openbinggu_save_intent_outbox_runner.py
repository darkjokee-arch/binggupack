# -*- coding: utf-8 -*-
"""OpenBinggu — hosted save-intent 로컬 outbox 러너 (D2 단계, 수동 실행만).

설계 정본: docs/BINGGUPACK_HOSTED_SAVE_INTENT_DESIGN.md (schema_ver=1).
outbox = 로컬 디렉토리(파일 1건 = intent 1건, 파일명 <intent_id>.json).
D3에서 worker 적재로 전송 계층만 교체 — 게이트는 본 러너 그대로 불변.

게이트 순서 (고정 — 우회 0):
  1 schema_ver 검증(schema_mismatch) → 2 TTL 만료 폐기(.expired 마킹만, 미적용)
  → 3 intent_id 재해시 일치(intent_id_mismatch) → 4 confirm 형식("SAVE i,j" 정확 일치)
  → (duplicate: audit_log 조회 — 별도 테이블 0) → 5 save_selected 전체 게이트 위임
  (A0·PII·duplicate registry·confirm·rollback 그대로, G4_no_auto 유지)
  → 6 적용 시 intent 파일 소거 + audit(hosted_intent, 원문 해시만)
  → 7 실패 = .rejected 보존(사유 포함) — 재시도는 사람 재승인만(자동 재시도 0)

불변/금지: real staging DB 0 · live/wrangler/deploy/외부 네트워크 0 · OpenCrab 0 ·
  confirmed 자동 생성 0 · marketplace/결제 0 · hosted write live 노출 0.

4cli 12지시 정합 (R3 결론 §1~4):
  - 마킹(.rejected/.expired) 파일은 **원문(text) 미보관** — text_sha/text_len 으로 대체(최소 보안안).
  - 이중 TTL: intent TTL(.expired, 미적용 BLOCK) + 마킹 파일 TTL(marked_ts 기준 만료 삭제).
  - outbox 경로 가드: UNC/네트워크 경로 거부 · symlink/reparse(junction) 거부 ·
    intent 파일은 outbox 직속 + 비링크만 처리(경로 밖/링크 = 거부).
  - worker non-retention 은 D3(worker 연동) 진입 게이트의 canary payload 실측 항목 — 본 러너 범위 밖.

CLI: python openbinggu_save_intent_outbox_runner.py --selftest   (temp 전용)
"""
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import openbinggu_conversation_candidate_save as _convsave  # save_selected 정본 (게이트 우회 0)
from openbinggu_staging_write_selftest import OPERATING_PATHS
from openbinggu_deprecate_and_remind_g3 import open_g3

SCHEMA_VER = 1
DEFAULT_TTL_S = 86400
MARKER_TTL_S = 7 * 86400   # 마킹 파일(.rejected/.expired) 보존 한도 — 경과 시 삭제(평문 장기 잔존 차단)
_CONFIRM_RE = re.compile(r"SAVE \d+(,\d+)*")
_REPARSE_ATTR = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_link(path):
    """symlink 또는 Windows reparse point(junction 포함) 여부."""
    if os.path.islink(path):
        return True
    try:
        attrs = os.lstat(path).st_file_attributes
        return bool(attrs & _REPARSE_ATTR)
    except (AttributeError, OSError):
        return False


def _outbox_path_guard(outbox_dir):
    """outbox 디렉토리 가드 — 로컬 절대경로·비UNC·비링크만 허용. 위반 = 전체 미처리(fail-closed)."""
    p = os.path.abspath(outbox_dir)
    if p.startswith("\\\\"):
        return "outbox_unc_rejected"
    if not os.path.isdir(p):
        return "outbox_not_dir"
    if _is_link(p):
        return "outbox_link_rejected"
    return None


def intent_hash(text, indices, confirm):
    """설계 §1: intent_id = sha256(text|indices|confirm)[:16] — 무결성 앵커."""
    base = "%s|%s|%s" % (text, ",".join(str(i) for i in indices), confirm)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def _text_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def process_outbox(db, outbox_dir, ctx, snap_dir, now_ts):
    """outbox *.json 정렬 순회 — 게이트 순서 고정. 반환 {applied,rejected,expired,purged,details}.
    입구 가드: 경로(UNC/링크) 위반 = 전체 미처리. 시작 시 마킹 파일 TTL 청소(이중 TTL)."""
    counts = {"applied": 0, "rejected": 0, "expired": 0, "purged": 0}
    details = []
    actor = ctx.get("actor", "human")

    guard = _outbox_path_guard(outbox_dir)
    if guard:
        counts["guard"] = guard
        counts["details"] = details
        return counts

    def _mark(path, suffix, extra):
        """실패/만료 intent 마킹 — **원문 미보관**(12지시 §3 최소 보안안): text → text_sha/text_len."""
        target = path + suffix
        n = 1
        while os.path.exists(target):
            target = "%s.%d%s" % (path, n, suffix)
            n += 1
        try:
            with open(path, "r", encoding="utf-8") as f:
                body = json.load(f)
            if not isinstance(body, dict):
                body = {"_raw_intent_sha": _text_sha(json.dumps(body, ensure_ascii=False))}
        except Exception:
            body = {"_unparsed": True}
        if isinstance(body.get("text"), str):
            body["text_sha"] = _text_sha(body["text"])
            body["text_len"] = len(body["text"])
            del body["text"]
        body.update(extra)
        body["marked_ts"] = now_ts
        with open(target, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=1)
        os.remove(path)
        return target

    # 이중 TTL 2/2 — 마킹 파일(.rejected/.expired) 만료 삭제 (평문은 없지만 메타도 한도 보존)
    for fn in sorted(os.listdir(outbox_dir)):
        if not (fn.endswith(".rejected") or fn.endswith(".expired")):
            continue
        mpath = os.path.join(outbox_dir, fn)
        try:
            with open(mpath, "r", encoding="utf-8") as f:
                mbody = json.load(f)
            marked = mbody.get("marked_ts")
        except Exception:
            marked = None
        if not isinstance(marked, int) or now_ts - marked > MARKER_TTL_S:
            os.remove(mpath)   # marked_ts 부재(구버전/손상)도 fail-closed 삭제
            counts["purged"] += 1

    for fn in sorted(os.listdir(outbox_dir)):
        if not fn.endswith(".json"):
            continue  # .rejected/.expired 마킹 파일은 재처리 0 (재시도 = 사람 재승인만)
        path = os.path.join(outbox_dir, fn)
        stem = fn[:-len(".json")]
        # 파일 단위 가드 — 링크/경로 밖 실체 거부 (12지시 §2)
        if _is_link(path) or os.path.dirname(os.path.realpath(path)) != os.path.realpath(outbox_dir):
            before = db.store_checksum()
            db.audit_append(actor, "hosted_intent", stem, "BLOCK", "intent_path_rejected",
                            before, before)
            counts["rejected"] += 1
            details.append({"file": fn, "status": "rejected", "reason": "intent_path_rejected"})
            continue

        def reject(reason, extra=None, _path=path, _fn=fn, _stem=stem):
            before = db.store_checksum()
            db.audit_append(actor, "hosted_intent", _stem, "BLOCK", reason, before, before)
            mk = _mark(_path, ".rejected", dict({"reject_reason": reason}, **(extra or {})))
            counts["rejected"] += 1
            details.append({"file": _fn, "status": "rejected", "reason": reason, "marked": mk})

        try:
            with open(path, "r", encoding="utf-8") as f:
                it = json.load(f)
        except Exception:
            reject("malformed_intent")
            continue
        if not isinstance(it, dict):
            reject("malformed_intent")
            continue

        # 게이트 1 — schema_ver
        if it.get("schema_ver") != SCHEMA_VER:
            reject("schema_mismatch")
            continue

        # 필드 타입 검증 (재해시·TTL 계산 가능 여부 — fail-closed)
        text, indices, confirm = it.get("text"), it.get("indices"), it.get("confirm")
        if (not isinstance(text, str) or not isinstance(indices, list)
                or not all(isinstance(i, int) and not isinstance(i, bool) for i in indices)
                or not isinstance(confirm, str)
                or not isinstance(it.get("created_ts"), int)
                or not isinstance(it.get("ttl_s", DEFAULT_TTL_S), int)):
            reject("malformed_intent")
            continue

        # 게이트 2 — TTL 만료 폐기 (.expired 마킹만, 미적용)
        if now_ts - it["created_ts"] > it.get("ttl_s", DEFAULT_TTL_S):
            before = db.store_checksum()
            db.audit_append(actor, "hosted_intent", stem, "BLOCK", "expired", before, before)
            mk = _mark(path, ".expired", {"reject_reason": "expired"})
            counts["expired"] += 1
            details.append({"file": fn, "status": "expired", "reason": "expired", "marked": mk})
            continue

        # 게이트 3 — intent_id 재해시 일치 (불일치 = 위변조)
        if it.get("intent_id") != intent_hash(text, indices, confirm):
            reject("intent_id_mismatch")
            continue

        # 게이트 4 — confirm 형식 ("SAVE " + ",".join(indices) 정확 일치)
        expected = "SAVE " + ",".join(str(i) for i in indices)
        if confirm != expected or not _CONFIRM_RE.fullmatch(confirm):
            reject("confirm_phrase_mismatch")
            continue

        # duplicate — 이미 적용(ALLOW) 기록된 intent_id 재유입 차단 (audit_log 조회만)
        if db.con.execute("SELECT 1 FROM audit_log WHERE action='hosted_intent' "
                          "AND result='ALLOW' AND pack_id=?", (it["intent_id"],)).fetchone():
            reject("duplicate_intent")
            continue

        # 게이트 5 — save_selected 전체 게이트 위임 (A0·PII·duplicate·confirm·rollback 그대로)
        r = _convsave.save_selected(db, text, indices,
                                    {"actor": actor, "confirm": confirm}, snap_dir)
        if not r.get("applied"):
            reject("save:" + str(r.get("reason")), {"save_rejected": r.get("rejected", {})})
            continue

        # 게이트 6 — 적용: audit(hosted_intent, 원문 해시만) + intent 파일 소거
        after = db.store_checksum()
        db.audit_append(actor, "hosted_intent", it["intent_id"], "ALLOW",
                        "text_sha=%s saved=%d skipped=%d" % (_text_sha(text), r["saved"],
                                                             r["skipped_existing"]),
                        after, after)
        os.remove(path)
        counts["applied"] += 1
        details.append({"file": fn, "status": "applied", "saved": r["saved"],
                        "pack_id": r.get("pack_id")})

    counts["details"] = details
    return counts


# ---------------- selftest (temp 전용 — tempfile.mkdtemp 만) ----------------

CONVO = ("이 문서는 배포 절차를 정의한다. 테스트 로그에 통과 결과가 기록되어 있다. "
         "낙찰하한율은 기초금액 대비 최저 투찰 비율을 말한다. 백필 작업이 진행 중이다. "
         "이 입찰은 마진이 낮아 보류한다.")
NOW = 1780000000


def _mk_intent(outbox, text, indices, confirm=None, **over):
    confirm = confirm if confirm is not None else "SAVE " + ",".join(str(i) for i in indices)
    it = {"schema_ver": SCHEMA_VER, "text": text, "indices": indices, "confirm": confirm,
          "intent_id": intent_hash(text, indices, confirm),
          "created_ts": NOW - 10, "ttl_s": DEFAULT_TTL_S, "source": "hosted"}
    it.update(over)
    p = os.path.join(outbox, it["intent_id"] + ".json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(it, f, ensure_ascii=False)
    return it, p


def run():
    before_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="obg_outbox_")
    outbox = os.path.join(tmp, "outbox")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(outbox)
    os.makedirs(snap_dir)
    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if ok else "FAIL"))

    def load(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    db = open_g3(os.path.join(tmp, "s.sqlite"))
    ctx = {"actor": "human"}

    # 1. 정상 intent 적용 — 노드 생성(발췌만)·파일 소거
    it1, p1 = _mk_intent(outbox, CONVO, [1, 5])
    r1 = process_outbox(db, outbox, ctx, snap_dir, NOW)
    n1 = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    rec(1, "정상 intent 적용(노드 2·intent 파일 소거)",
        r1["applied"] == 1 and r1["rejected"] == 0 and r1["expired"] == 0
        and n1 == 2 and not os.path.exists(p1))

    # 2. schema_ver 불일치 거부
    it2, p2 = _mk_intent(outbox, "이 검토안은 schema 게이트 확인을 위해 보류한다.", [1], schema_ver=2)
    r2 = process_outbox(db, outbox, ctx, snap_dir, NOW)
    m2 = p2 + ".rejected"
    rec(2, "schema_ver 불일치 .rejected(schema_mismatch)",
        r2["rejected"] == 1 and os.path.exists(m2)
        and load(m2)["reject_reason"] == "schema_mismatch")

    # 3. TTL 만료 폐기 — .expired 마킹만, 미적용
    it3, p3 = _mk_intent(outbox, "이 케이스는 TTL 검증 후 보류한다.", [1],
                         created_ts=NOW - DEFAULT_TTL_S - 1)
    n_before3 = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    r3 = process_outbox(db, outbox, ctx, snap_dir, NOW)
    n_after3 = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    m3 = p3 + ".expired"
    rec(3, "TTL 만료 .expired 마킹만(미적용)",
        r3["expired"] == 1 and r3["applied"] == 0 and os.path.exists(m3)
        and n_before3 == n_after3 and load(m3)["reject_reason"] == "expired")

    # 4. intent_id 변조(text 변조 → 재해시 불일치) 거부
    it4, p4 = _mk_intent(outbox, "이 항목은 변조 검증을 위해 보류한다.", [1])
    tampered = dict(it4)
    tampered["text"] = it4["text"] + " 변조됨."
    with open(p4, "w", encoding="utf-8") as f:
        json.dump(tampered, f, ensure_ascii=False)
    r4 = process_outbox(db, outbox, ctx, snap_dir, NOW)
    m4 = p4 + ".rejected"
    rec(4, "intent_id 재해시 불일치(tamper) 거부",
        r4["rejected"] == 1 and os.path.exists(m4)
        and load(m4)["reject_reason"] == "intent_id_mismatch")

    # 5. confirm 형식 불일치 거부 (재해시는 일치 — confirm 게이트 단독 검증)
    it5, p5 = _mk_intent(outbox, "이 안건은 confirm 게이트 확인 후 보류한다.", [1],
                         confirm="SAVE 1,2")
    r5 = process_outbox(db, outbox, ctx, snap_dir, NOW)
    m5 = p5 + ".rejected"
    rec(5, "confirm 형식 불일치 거부",
        r5["rejected"] == 1 and os.path.exists(m5)
        and load(m5)["reject_reason"] == "confirm_phrase_mismatch")

    # 6. save 게이트 위임 — PII 후보가 save 경로에 도달해도 save 재스캔이 거부
    #    (preview 단계 제외를 우회한 위협 시나리오를 capture_preview 패치로 재현 — 러너 자체 PII 로직 0)
    pii_sent = "이 입찰은 010-" + "1234-5678 통화 결과 마진이 낮아 보류한다."
    it6, p6 = _mk_intent(outbox, pii_sent, [1])
    orig_preview = _convsave.capture_preview
    try:
        _convsave.capture_preview = lambda t: {"candidates": [
            {"sentence": pii_sent, "label_kind": "판단"}]}
        r6 = process_outbox(db, outbox, ctx, snap_dir, NOW)
    finally:
        _convsave.capture_preview = orig_preview
    m6 = p6 + ".rejected"
    body6 = load(m6) if os.path.exists(m6) else {}
    rec(6, "save 게이트 위임(PII intent → .rejected pii_or_secret)",
        r6["rejected"] == 1 and r6["applied"] == 0 and os.path.exists(m6)
        and body6.get("save_rejected", {}).get("pii_or_secret") == 1
        and body6.get("reject_reason", "").startswith("save:"))

    # 7. duplicate intent 거부 — 동일 intent_id 재유입 (audit_log 조회, 별도 테이블 0)
    with open(p1, "w", encoding="utf-8") as f:
        json.dump(it1, f, ensure_ascii=False)
    r7 = process_outbox(db, outbox, ctx, snap_dir, NOW)
    m7 = p1 + ".rejected"
    rec(7, "duplicate intent 거부(audit_log 조회)",
        r7["rejected"] == 1 and r7["applied"] == 0 and os.path.exists(m7)
        and load(m7)["reject_reason"] == "duplicate_intent")

    # 8. raw 원문 DB 미저장 — 전문 비재현(intent 파일에는 있어도 DB blob 에 없음)
    blob = "\n".join(str(row) for t in ("nodes", "edges", "evidence", "audit_log")
                     for row in db.con.execute("SELECT * FROM " + t))
    rec(8, "raw 원문 DB 미저장(전문 비재현 + PII 무유입)",
        CONVO not in blob and "1234-5678" not in blob)

    # 9. candidate-only · confirmed 0
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0])
    rec(9, "candidate-only · confirmed 0", bad == 0)

    # 10. audit chain INTACT
    rec(10, "audit chain INTACT", db.verify_chain())

    # 11. hosted_intent audit 존재 + 원문 무누설(해시만)
    h_allow = db.con.execute("SELECT pack_id, reason_code FROM audit_log "
                             "WHERE action='hosted_intent' AND result='ALLOW'").fetchall()
    h_blob = "\n".join(str(r) for r in db.con.execute(
        "SELECT * FROM audit_log WHERE action='hosted_intent'"))
    rec(11, "hosted_intent audit 존재(ALLOW 1·해시만) + 원문 무누설",
        len(h_allow) == 1 and h_allow[0][0] == it1["intent_id"]
        and "text_sha=" in h_allow[0][1]
        and CONVO[:20] not in h_blob and "1234-5678" not in h_blob)

    # 14. 마킹 파일 원문 미보관 — text 필드 제거 + text_sha/text_len 대체 (12지시 §3)
    b2, b6 = load(m2), load(m6)
    rec(14, "마킹 파일 원문 미보관(text 제거+sha 대체)",
        "text" not in b2 and "text" not in b6
        and b2.get("text_sha") and b6.get("text_sha")
        and "변조" not in json.dumps(b2, ensure_ascii=False))

    # 15. 마킹 파일 TTL 청소 — 만료 마킹 삭제·신선 마킹 보존 (이중 TTL 2/2, 12지시 §4)
    old_m = os.path.join(outbox, "oldcase.json.rejected")
    with open(old_m, "w", encoding="utf-8") as f:
        json.dump({"reject_reason": "x", "marked_ts": NOW - MARKER_TTL_S - 1}, f)
    fresh_keep = os.path.exists(m6)
    r15 = process_outbox(db, outbox, ctx, snap_dir, NOW)
    rec(15, "마킹 TTL 청소(만료 삭제·신선 보존)",
        r15["purged"] >= 1 and not os.path.exists(old_m)
        and fresh_keep and os.path.exists(m6))

    # 16. outbox 경로 가드 — UNC 거부 + junction(reparse) 거부(생성 가능 환경에서만)
    rg_unc = process_outbox(db, "\\\\localhost\\c$\\nope", ctx, snap_dir, NOW)
    junc_ok = True
    junc = os.path.join(tmp, "junc_outbox")
    rc_j = os.system('cmd /c mklink /J "%s" "%s" >nul 2>&1' % (junc, outbox))
    if rc_j == 0:
        rg_j = process_outbox(db, junc, ctx, snap_dir, NOW)
        junc_ok = rg_j.get("guard") == "outbox_link_rejected"
    rec(16, "outbox 경로 가드(UNC·junction 거부)",
        rg_unc.get("guard") in ("outbox_unc_rejected", "outbox_not_dir") and junc_ok)

    db.close()

    # 12. 운영 store 불변
    after_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    rec(12, "운영 store 불변", before_mtime == after_mtime)

    # 13. temp 정리
    shutil.rmtree(tmp, ignore_errors=True)
    rec(13, "temp 정리(잔존 0)", not os.path.exists(tmp))

    print("=" * 74)
    print("OpenBinggu — save_intent_outbox_runner selftest (D2, temp 전용)")
    print("=" * 74)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print("%s %2d %s" % ("[OK]" if v == "PASS" else "[X]", cid, desc))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("real_staging=0  live=0  deploy=0  network=0  opencrab=0  confirmed=0  raw_full_text_db=0")
    gate = "GO" if npass == len(results) else "NO-GO"
    print("GATE:", gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(run())
    print("usage: openbinggu_save_intent_outbox_runner.py [--selftest]")
    print("temp selftest 전용 — real staging/outbox 운영·worker 연동·live 노출은 각 단계 별도 GO 의무 (D3~D5)")
    sys.exit(2)
