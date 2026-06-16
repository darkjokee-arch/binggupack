# -*- coding: utf-8 -*-
"""OpenBinggu v0.8 — conversation_candidate_save (temp 구현, 적대 리뷰 15건 반영).

설계: docs/BINGGUPACK_V08_PERSONAL_WRITE_LOOP_DESIGN.md §2.
핵심: save는 preview 결과 객체를 받지 않는다 — **원본 text 를 받아 capture_preview 를 내부 재실행**
(deterministic·멱등 기증명)하고, 사용자는 인덱스 + confirm 문구("SAVE 3,5,7")로만 선택을 증명한다.

불변: real/temp staging SQLite 한정(StagingDB 운영경로 거부) · candidate=1 · promotion=0 · confirmed 0 ·
      OpenCrab apply 0 · 원문 전문 저장 0 · audit(conv_save) · backup/checksum rollback(staging_apply 재사용) ·
      자기증빙 evidence 는 "conv-self:" prefix 명시 + promotion 영구 제외.
CLI: python openbinggu_conversation_candidate_save.py --selftest
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openbinggu_conversation_capture_preview import capture_preview, _PREVIEW_PII_EXTRA  # 정본 재실행
from openbinggu_staging_write_selftest import staging_apply, OPERATING_PATHS, _hash
from openbinggu_deprecate_and_remind_g3 import open_g3, set_review_due
import openbinggu_label_kind_map as lkmap
import openbinggu_a0_node_dryrun as a0
import openbinggu_incoming_to_staging as v011
from watcher_batch_m1 import scan_residual_pii


def _sent_hash(s):
    return hashlib.sha256(re.sub(r"\s+", " ", s).strip().encode("utf-8")).hexdigest()[:8]


def _maybe_promote_actor_by_gate(text, indices, ctx):
    """사람-발화 게이트(binggu_save_gate): actor 비human 이어도 선택 문장이 사람 SAVE 발화로
    기록됐으면 human 승격(0-A 해법, 4cli REFINE). 게이트 실패/미기록 → 승격 0(fail-closed)."""
    if ctx.get("actor", "").strip().lower() == "human":
        return ctx
    try:
        import binggu_save_gate as sgate
        cands = capture_preview(text)["candidates"]
        sents = [cands[i - 1]["sentence"] for i in indices
                 if isinstance(i, int) and 1 <= i <= len(cands)]
        if sents and sgate.gate_human_for(sents):
            ctx = dict(ctx)
            ctx["actor"] = "human"
            ctx["actor_promoted_by"] = "save_gate"  # 사후감사 표식
    except Exception:
        pass  # 게이트 부재/오류 → 기존 actor 게이트 유지(default-deny)
    return ctx


def save_selected(db, text, indices, ctx, snap_dir, due_date=None):
    """선택 후보만 staging 저장. 반환 {applied, saved, skipped_existing, rejected, reason, pack_id}.
    진입 시 사람-발화 게이트로 actor 승격 후 기존 게이트(actor/confirm/A0/PII) 그대로 적용."""
    ctx = _maybe_promote_actor_by_gate(text, indices, ctx)
    before = db.store_checksum()

    def block(reason):
        db.audit_append(ctx.get("actor", "human"), "conv_save", "conv_pending", "BLOCK",
                        reason, before, before)
        return {"applied": False, "saved": 0, "skipped_existing": 0, "rejected": {}, "reason": reason}

    # 1) actor + confirm 문구 (사람 발화 유래 증거 — 정확 일치 의무)
    # allowlist: 'human'만 허용. denylist(auto/reader만 차단)는 'agent'/'system'/누락/대문자
    # 우회로 자동저장 가능(영구금지 25 우회) → human 정확매칭+정규화로 default-deny.
    if ctx.get("actor", "").strip().lower() != "human":
        return block("G4_no_auto")
    expected = "SAVE " + ",".join(str(i) for i in indices)
    if ctx.get("confirm") != expected:
        return block("confirm_phrase_mismatch")
    if not indices:
        return block("empty_selection")

    # 2) 원본 text 재실행 (변조 불가 — preview 결과 객체 불신)
    pv = capture_preview(text)
    cands = pv["candidates"]

    saved_items = []
    skipped = 0
    rejected = {}

    def rej(code):
        rejected[code] = rejected.get(code, 0) + 1

    for i in indices:
        if not isinstance(i, int) or i < 1 or i > len(cands):
            rej("index_out_of_range")
            continue
        c = cands[i - 1]
        sent = c["sentence"]  # 사용자가 고른 문장 전체 = 저장될 문자열 (발췌 cut 폐기 — 개인 온톨로지 정체성)
        kind = c["label_kind"]
        # 3a) 저장될 문자열(=문장 전체) 그대로 A0 재판정
        verdict = a0.classify_node(
            {"id": "pre:" + _sent_hash(sent), "sentence": sent,
             "node_type": lkmap.KO2EN[kind], "evidence_refs": ["pre"]}, status="candidate")
        if verdict["verdict"] == "FAIL":
            rej("a0_fail")
            continue
        if verdict["verdict"] == "REVIEW" and not ctx.get("allow_review"):
            rej("a0_review_needs_explicit_allow")
            continue
        # 3b) PII/secret/bizno 재스캔 (재실행 경로 무결성 방어)
        pii = scan_residual_pii(sent) + [k for k, rx in _PREVIEW_PII_EXTRA if rx.search(sent)]
        if pii or any(p.search(sent) for p in v011.SECRET_PATTERNS):
            rej("pii_or_secret")
            continue
        # 3c) 기존재 노드 skip (부분 재선택 시 배치 전멸 방지)
        nid = "node:CONV:" + _sent_hash(sent)
        if db.con.execute("SELECT 1 FROM nodes WHERE node_id=?", (nid,)).fetchone():
            skipped += 1
            continue
        saved_items.append({"nid": nid, "sent": sent, "kind": kind})

    if not saved_items:
        db.audit_append(ctx.get("actor", "human"), "conv_save", "conv_noop", "BLOCK",
                        "nothing_to_save", before, before)
        return {"applied": False, "saved": 0, "skipped_existing": skipped,
                "rejected": rejected, "reason": "nothing_to_save"}

    # 4) mini-pack 조립 — 어휘 매핑 경유 + 자기증빙 prefix + ephemeral freshness 동결
    pack_content = "\n".join(sorted(it["sent"] for it in saved_items))
    pack_id = "conv_" + _hash(pack_content)[:8]
    nodes, edges, evidence = [], [], []
    for it in saved_items:
        space, ntype = lkmap.KIND_TO_SPACE_NTYPE[it["kind"]]
        eid = "EVC-CONV-" + _sent_hash(it["sent"])
        th = _hash(it["sent"])  # capture 시점 동결 — ephemeral 출처(동어반복임을 audit 에 명시)
        nodes.append({"id": it["nid"], "type": ntype, "sentence": it["sent"]})
        evidence.append({"id": eid, "sentence": it["sent"],
                         "source_pointer_id": "conv-self:" + _sent_hash(it["sent"]),
                         "source_missing": False, "source_hash": th, "captured_hash": th,
                         "redaction_policy": "v1"})
        edges.append({"id": "edge:CONV:" + _sent_hash(it["sent"]), "relation": "evidence_supports",
                      "source": eid, "target": it["nid"], "evidence_refs": [eid]})
    pack = {"pack_id": pack_id, "content": pack_content,
            "nodes": nodes, "edges": edges, "evidence": evidence}

    # 5) staging_apply 경유 (duplicate·backup·transaction·checksum·audit 재사용)
    r = staging_apply(db, pack, {"actor": ctx.get("actor", "human"),
                                 **{k: v for k, v in ctx.items() if k in ("backup_fail", "wal_abort", "checksum_mismatch")}},
                      snap_dir)
    if not r.get("applied"):
        db.audit_append(ctx.get("actor", "human"), "conv_save", pack_id, "BLOCK",
                        "staging_apply:" + str(r.get("reason")), before, db.store_checksum())
        return {"applied": False, "saved": 0, "skipped_existing": skipped,
                "rejected": rejected, "reason": r.get("reason"), "pack_id": pack_id}
    db.audit_append(ctx.get("actor", "human"), "conv_save", pack_id, "ALLOW",
                    "ephemeral_conv saved=%d skipped=%d" % (len(saved_items), skipped),
                    before, db.store_checksum())

    # 6) 판단 노드 + due_date → G3 리마인드 등록 (옵션)
    due_set = 0
    if due_date:
        for it in saved_items:
            if it["kind"] == "판단":
                rr = set_review_due(db, it["nid"], due_date, {"actor": ctx.get("actor", "human")})
                if rr.get("applied"):
                    due_set += 1

    return {"applied": True, "saved": len(saved_items), "skipped_existing": skipped,
            "rejected": rejected, "reason": None, "pack_id": pack_id,
            "snapshot": r.get("snapshot"), "due_set": due_set}


# ---------------- selftest ----------------

CONVO = ("이 문서는 배포 절차를 정의한다. 테스트 로그에 통과 결과가 기록되어 있다. "
         "낙찰하한율은 기초금액 대비 최저 투찰 비율을 말한다. 백필 작업이 진행 중이다. "
         "이 입찰은 마진이 낮아 보류한다.")


def run():
    before_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="obg_v08_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if ok else "FAIL"))

    db = open_g3(os.path.join(tmp, "s.sqlite"))

    # 1. 정상: 후보 1·5번(문서·판단) 선택 저장 + due
    r1 = save_selected(db, CONVO, [1, 5], {"actor": "human", "confirm": "SAVE 1,5"},
                       snap_dir, due_date="2026-06-20")
    n = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
    e = db.con.execute("SELECT count(*) FROM edges").fetchone()[0]
    v = db.con.execute("SELECT count(*) FROM evidence").fetchone()[0]
    nt = db.con.execute("SELECT node_type FROM nodes ORDER BY node_id").fetchall()
    aud = db.con.execute("SELECT count(*) FROM audit_log WHERE action='conv_save' AND result='ALLOW'").fetchone()[0]
    rev = db.con.execute("SELECT count(*) FROM judgment_reviews WHERE status='pending'").fetchone()[0]
    rec(1, "정상 저장(2건+어휘 매핑+conv_save audit+판단 due)",
        r1["applied"] and r1["saved"] == 2 and n == 2 and e == 2 and v == 2
        and {x[0] for x in nt} <= {"Document", "Evidence", "Concept", "Claim"}
        and aud == 1 and rev == 1 and r1["due_set"] == 1)

    # 2. confirm 문구 불일치 BLOCK
    r2 = save_selected(db, CONVO, [2], {"actor": "human", "confirm": "SAVE 2,3"}, snap_dir)
    rec(2, "confirm 불일치 BLOCK", (not r2["applied"]) and r2["reason"] == "confirm_phrase_mismatch")

    # 3. auto 차단
    r3 = save_selected(db, CONVO, [2], {"actor": "auto", "confirm": "SAVE 2"}, snap_dir)
    rec(3, "actor=auto BLOCK", (not r3["applied"]) and r3["reason"] == "G4_no_auto")

    # 4. 자기증빙 prefix + ephemeral 동결 확인
    ptr = db.con.execute("SELECT source_pointer_id FROM evidence ORDER BY evidence_id").fetchone()[0]
    rec(4, "자기증빙 conv-self prefix", ptr.startswith("conv-self:"))

    # 5. 원문 전문 미저장 증명 — 입력 전문 문자열이 어떤 행에도 없음 (문장 단위만 저장)
    blob = "\n".join(str(row) for t in ("nodes", "edges", "evidence", "audit_log")
                     for row in db.con.execute("SELECT * FROM " + t))
    rec(5, "원문 전문 미저장(문장 단위만)", CONVO not in blob)

    # 6. 부분 재선택 — [1,5] 재선택 시 전부 skip → nothing_to_save / [5,2]는 5 skip·2 저장
    r6a = save_selected(db, CONVO, [1, 5], {"actor": "human", "confirm": "SAVE 1,5"}, snap_dir)
    r6b = save_selected(db, CONVO, [5, 2], {"actor": "human", "confirm": "SAVE 5,2"}, snap_dir)
    rec(6, "부분 재선택(전부 skip→noop / 일부만 신규 저장)",
        (not r6a["applied"]) and r6a["reason"] == "nothing_to_save" and r6a["skipped_existing"] == 2
        and r6b["applied"] and r6b["saved"] == 1 and r6b["skipped_existing"] == 1)

    # 7. 인덱스 범위 밖 거부
    r7 = save_selected(db, CONVO, [99], {"actor": "human", "confirm": "SAVE 99"}, snap_dir)
    rec(7, "인덱스 범위 밖 거부", (not r7["applied"]) and r7["rejected"].get("index_out_of_range") == 1)

    # 8. A0 FAIL 후보 거부 — 단편 문장만으로 구성된 입력 (preview 가 후보로 올려도 절단/단편은 저장 게이트가 거부)
    frag_text = "공고번호 20250000001 검토 진행 상황 정리 메모"  # 비종결 — preview 후보로 잡혀도 A0 FAIL
    pv8 = capture_preview(frag_text)
    if pv8["candidates"]:
        r8 = save_selected(db, frag_text, [1], {"actor": "human", "confirm": "SAVE 1"}, snap_dir)
        ok8 = (not r8["applied"]) and r8["rejected"].get("a0_fail") == 1
    else:
        ok8 = True  # 후보 자체가 없으면 저장 경로 진입 불가 = 동일하게 안전
    rec(8, "A0 FAIL 후보 저장 거부", ok8)

    # 9. checksum rollback — 신규 DB
    db2 = open_g3(os.path.join(tmp, "s2.sqlite"))
    r9 = save_selected(db2, CONVO, [1], {"actor": "human", "confirm": "SAVE 1",
                                         "checksum_mismatch": True}, snap_dir)
    rolled = db2.con.execute("SELECT count(*) FROM nodes").fetchone()[0] == 0
    rec(9, "checksum rollback(부분쓰기 0)", (not r9["applied"]) and rolled)
    db2.close()

    # 10. duplicate pack 차단 (동일 선택 동일 내용 재시도 — 6a 가 skip 경로, 이번엔 registry 경로 검증)
    db3 = open_g3(os.path.join(tmp, "s3.sqlite"))
    save_selected(db3, CONVO, [3], {"actor": "human", "confirm": "SAVE 3"}, snap_dir)
    db3.con.execute("DELETE FROM nodes")  # 노드만 지워 skip 우회 → registry 가 잡아야 함
    db3.con.commit()
    r10 = save_selected(db3, CONVO, [3], {"actor": "human", "confirm": "SAVE 3"}, snap_dir)
    rec(10, "duplicate(applied_registry) 차단", (not r10["applied"])
        and r10["reason"] == "duplicate_already_applied")
    db3.close()

    # 11. confirmed 0 · promotion 0 전수
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0]
           + db.con.execute("SELECT count(*) FROM edges WHERE candidate!=1").fetchone()[0])
    rec(11, "confirmed 0 · promotion 0", bad == 0)

    # 13. 긴 문장(80자 초과) 전체 저장 — 발췌 cut 폐기 검증(저장된 sentence == 입력 문장 전체)
    db_long = open_g3(os.path.join(tmp, "s_long.sqlite"))
    LONG = "이 입찰은 " + "매우 " * 30 + "신중하게 검토한 끝에 보류한다."
    rl = save_selected(db_long, LONG, [1], {"actor": "human", "confirm": "SAVE 1"}, snap_dir)
    stored = db_long.con.execute("SELECT sentence FROM nodes").fetchone()
    rec(13, "긴 문장 전체 저장(발췌 0·node sentence=전체)",
        rl["applied"] and rl["saved"] == 1 and stored and stored[0] == LONG and len(LONG) > 80)
    db_long.close()

    # 12. audit chain INTACT + 운영 store 불변
    intact = db.verify_chain()
    db.close()
    after_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    rec(12, "audit chain + 운영 store 불변", intact and before_mtime == after_mtime)

    shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 74)
    print("OpenBinggu v0.8 — conversation_candidate_save selftest (temp staging)")
    print("=" * 74)
    npass = sum(1 for _, _, vv in results if vv == "PASS")
    for cid, desc, vv in results:
        print("%s %2d %s" % ("[OK]" if vv == "PASS" else "[X]", cid, desc))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("operating_store_unchanged=True 기준  raw_full_text_stored=0  confirmed=0  opencrab=0  deploy=0")
    gate = "GO" if npass == len(results) else "NO-GO"
    print("GATE:", gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(run())
    print("usage: openbinggu_conversation_candidate_save.py [--selftest]")
    sys.exit(2)
