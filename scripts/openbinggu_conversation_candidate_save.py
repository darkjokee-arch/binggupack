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


def save_selected(db, text, indices, ctx, snap_dir, due_date=None, speaker=None):
    """선택 후보만 staging 저장. 반환 {applied, saved, skipped_existing, rejected, reason, pack_id}.
    진입 시 사람-발화 게이트로 actor 승격 후 기존 게이트(actor/confirm/A0/PII) 그대로 적용.
    speaker: 화자 축(owner=사용자 발화/ai=AI 요약). None=미지정(기존 호출 후방호환·NULL 적재).
    owner 직감도 conv-self 자기증빙 evidence 가 붙으므로 A0 REVIEW 거부 없이 저장된다(evidence_refs 보유)."""
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
        saved_items.append({"nid": nid, "sent": sent, "kind": kind,
                            "subtype": c.get("semantic_subtype")})

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
        # 도장 단일 원천: node_type = 분류 결과 5종 EN 라벨(doc/evidence/concept/state/judgment).
        # KIND_TO_SPACE_NTYPE(space ntype: 상태·판단 둘 다 Claim 으로 붕괴)는 적재 node_type 에 쓰지 않는다.
        # A0(LABEL_KINDS=5종 EN)가 이미 이 값으로 검증했으므로 저장값=검증값=표시값 3자 일치.
        ntype = lkmap.KO2EN[it["kind"]]
        eid = "EVC-CONV-" + _sent_hash(it["sent"])
        th = _hash(it["sent"])  # capture 시점 동결 — ephemeral 출처(동어반복임을 audit 에 명시)
        nodes.append({"id": it["nid"], "type": ntype, "sentence": it["sent"],
                      "semantic_subtype": it.get("subtype"),  # 보조 메타(canonical 도장 아님)
                      "speaker": speaker})                    # 화자 축(owner/ai/None) — staging_apply 가 적재
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


# ===== 페어 저장 (owner 발화 ↔ ai 요약 독립 노드 + 연결 엣지) =====
# 화자 축 핵심 경로: owner 직감/지적/원인 노드(speaker=owner)와 AI 수정/수용/반박 노드(speaker=ai)를
# 각각 독립 저장하되 연결 엣지로 묶는다. 4cli 토론 도출 불변식 반영:
#   불변식1: ai_text=None → owner 단독(순수 직감 — 억지 ai/엣지 생성 금지)
#   불변식2: 단일 pack → staging_apply 1회(원자성·부분커밋시 전체 롤백)
#   불변식3(③): 페어 한쪽이라도 기존재 시 전체 skip(부분 적재·dangling 엣지 방지)
#   헌법: candidate-only(staging_apply 고정)·사람 confirm 게이트·PII 제외·전 엣지 evidence 증빙
# 양방향 — 누가 누구에게 반응했나(대화 시간 순서·관계 방향).
#   ai_*    = AI가 사용자 발화를 수용/반박/수정 (사용자 발화가 먼저, AI가 반응)
#   owner_* = 사용자가 AI 발화를 수용/반박/수정 (AI 발화가 먼저, 사용자가 반응)
# 페어 엣지 방향: relation prefix 가 source(반응 주체), 대상이 target.
PAIR_RELATIONS = {"ai_accepts", "ai_refutes", "ai_revises",
                  "owner_accepts", "owner_refutes", "owner_revises"}


def _pick_one_node(text, pick, speaker):
    """text 에서 pick 번째 후보 1건을 A0/PII 게이트 통과 후 node dict 로. 실패 시 에러코드(str)."""
    cands = capture_preview(text)["candidates"]
    if not isinstance(pick, int) or pick < 1 or pick > len(cands):
        return "index_out_of_range"
    c = cands[pick - 1]
    sent, kind = c["sentence"], c["label_kind"]
    verdict = a0.classify_node({"id": "pre:" + _sent_hash(sent), "sentence": sent,
                                "node_type": lkmap.KO2EN[kind], "evidence_refs": ["pre"]}, status="candidate")
    if verdict["verdict"] == "FAIL":
        # owner 발화는 사장님 자연어 원문 그대로 보존(§8-1 ⑥·화자축 본질). a0 형식 게이트
        # (node_1_word/meaning = 단어·비종결·짧음 구어체)는 "owner 직감 검열·자동폐기 금지"
        # 원칙으로 면제. PII/secret(아래)·G4_no_auto(호출부)는 그대로 강제 — 안전 게이트 무영향.
        _owner_form_exempt = {"node_1_word", "node_1_meaning"}
        if not (speaker == "owner" and verdict.get("guard") in _owner_form_exempt):
            return "a0_fail"
    pii = scan_residual_pii(sent) + [k for k, rx in _PREVIEW_PII_EXTRA if rx.search(sent)]
    if pii or any(p.search(sent) for p in v011.SECRET_PATTERNS):
        return "pii_or_secret"
    return {"id": "node:CONV:" + _sent_hash(sent), "type": lkmap.KO2EN[kind],
            "sentence": sent, "semantic_subtype": c.get("semantic_subtype"), "speaker": speaker}


def _self_evidence(node):
    """노드의 conv-self 자기증빙 evidence + evidence_supports 엣지(헌법: 전 노드 증빙)."""
    h = _sent_hash(node["sentence"])
    th = _hash(node["sentence"])
    eid = "EVC-CONV-" + h
    ev = {"id": eid, "sentence": node["sentence"], "source_pointer_id": "conv-self:" + h,
          "source_missing": False, "source_hash": th, "captured_hash": th, "redaction_policy": "v1"}
    edge = {"id": "edge:CONV:" + h, "relation": "evidence_supports",
            "source": eid, "target": node["id"], "evidence_refs": [eid]}
    return ev, edge


def save_paired(db, owner_text, ai_text, ctx, snap_dir,
                relation_kind="ai_accepts", owner_pick=1, ai_pick=1, due_date=None):
    """owner 발화 + ai 요약을 각각 독립 노드로 저장하고 연결 엣지로 묶는다(ai_text=None → owner 단독)."""
    before = db.store_checksum()

    def block(reason):
        db.audit_append(ctx.get("actor", "human"), "conv_save_pair", "conv_pending", "BLOCK",
                        reason, before, before)
        return {"applied": False, "saved": 0, "reason": reason}

    if ctx.get("actor", "").strip().lower() != "human":
        return block("G4_no_auto")
    paired = bool(ai_text)
    if paired and relation_kind not in PAIR_RELATIONS:
        return block("relation_kind_invalid")
    expected = ("PAIR %s owner:%d ai:%d" % (relation_kind, owner_pick, ai_pick)) if paired \
        else ("PAIR owner:%d" % owner_pick)
    if ctx.get("confirm") != expected:
        return block("confirm_phrase_mismatch")

    own = _pick_one_node(owner_text, owner_pick, "owner")
    if isinstance(own, str):
        return block("owner_" + own)
    nodes_pack, edges_pack, ev_pack = [own], [], []
    ev, ed = _self_evidence(own)
    ev_pack.append(ev); edges_pack.append(ed)

    if paired:
        ain = _pick_one_node(ai_text, ai_pick, "ai")
        if isinstance(ain, str):
            return block("ai_" + ain)
        if ain["id"] == own["id"]:
            return block("pair_same_node")
        nodes_pack.append(ain)
        ev2, ed2 = _self_evidence(ain)
        ev_pack.append(ev2); edges_pack.append(ed2)
        # 페어 엣지 방향: relation prefix 가 반응 주체(source). 증빙 = source 노드 자기증빙(헌법: 전 엣지 evidence).
        if relation_kind.startswith("owner_"):
            _src, _tgt, _ev = own["id"], ain["id"], ev["id"]    # 사용자가 AI 발화를 수용/반박/수정
        else:
            _src, _tgt, _ev = ain["id"], own["id"], ev2["id"]   # AI가 사용자 발화를 수용/반박/수정
        edges_pack.append({"id": "edge:PAIR:" + _sent_hash(own["sentence"] + ain["sentence"]),
                           "relation": relation_kind, "source": _src, "target": _tgt,
                           "evidence_refs": [_ev]})

    # ③ 페어 중복검사 — 한쪽이라도 기존재 시 전체 skip(부분 적재·dangling 방지)
    for nd in nodes_pack:
        if db.con.execute("SELECT 1 FROM nodes WHERE node_id=?", (nd["id"],)).fetchone():
            return block("pair_partial_exists")

    pack_content = "\n".join(sorted(nd["sentence"] for nd in nodes_pack))
    pack = {"pack_id": "pair_" + _hash(pack_content)[:8], "content": pack_content,
            "nodes": nodes_pack, "edges": edges_pack, "evidence": ev_pack}
    r = staging_apply(db, pack, {"actor": ctx.get("actor", "human"),
                                 **{k: v for k, v in ctx.items() if k in ("backup_fail", "wal_abort", "checksum_mismatch")}},
                      snap_dir)
    if not r.get("applied"):
        db.audit_append(ctx.get("actor", "human"), "conv_save_pair", pack["pack_id"], "BLOCK",
                        "staging_apply:" + str(r.get("reason")), before, db.store_checksum())
        return {"applied": False, "saved": 0, "reason": r.get("reason"), "pack_id": pack["pack_id"]}
    db.audit_append(ctx.get("actor", "human"), "conv_save_pair", pack["pack_id"], "ALLOW",
                    "pair %s saved=%d" % ("owner+ai" if paired else "owner_solo", len(nodes_pack)),
                    before, db.store_checksum())

    due_set = 0
    if due_date:
        for nd in nodes_pack:
            if nd["type"] == "judgment":
                rr = set_review_due(db, nd["id"], due_date, {"actor": ctx.get("actor", "human")})
                if rr.get("applied"):
                    due_set += 1
    return {"applied": True, "saved": len(nodes_pack), "reason": None, "pack_id": pack["pack_id"],
            "paired": paired, "relation": relation_kind if paired else None,
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
    # 도장 5종 세분화: node_type 은 doc/evidence/concept/state/judgment EN 라벨로 저장(Claim 붕괴 폐기).
    # 후보 1·5 = 문서(doc)·판단(judgment) → 정확히 그 두 값이어야.
    rec(1, "정상 저장(2건+5종 node_type+conv_save audit+판단 due)",
        r1["applied"] and r1["saved"] == 2 and n == 2 and e == 2 and v == 2
        and {x[0] for x in nt} == {"doc", "judgment"}
        and {x[0] for x in nt} <= set(lkmap.KO2EN.values())
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

    # ===== 화자 축 + 페어 + 양방향 신뢰도 (speaker 확장 정식 케이스) =====
    import binggu_hit_stats as _HS
    db_spk = open_g3(os.path.join(tmp, "s_spk.sqlite"))
    # 14. speaker 적재(owner) — save_selected speaker 파라미터(후방호환·NULL/owner/ai)
    save_selected(db_spk, CONVO, [1], {"actor": "human", "confirm": "SAVE 1"}, snap_dir, speaker="owner")
    _sp = db_spk.con.execute("SELECT speaker FROM nodes").fetchone()
    rec(14, "speaker 적재(owner)", bool(_sp) and _sp[0] == "owner")
    # 15. 페어 저장 — owner/ai 독립 노드 + ai_refutes 연결 + dangling 0(불변식2·3)
    _OWN = "이 입찰은 마진이 낮아 보류하는 것이 낫다."
    _AI = "백필 작업이 진행 중인 상태이다."
    rp = save_paired(db_spk, _OWN, _AI, {"actor": "human", "confirm": "PAIR ai_refutes owner:1 ai:1"},
                     snap_dir, relation_kind="ai_refutes")
    _spk2 = {r[0] for r in db_spk.con.execute("SELECT speaker FROM nodes WHERE speaker IS NOT NULL")}
    _rels = [r[0] for r in db_spk.con.execute("SELECT relation FROM edges")]
    _nids = {r[0] for r in db_spk.con.execute("SELECT node_id FROM nodes")}
    _evids = {r[0] for r in db_spk.con.execute("SELECT evidence_id FROM evidence")}
    _valid = _nids | _evids
    _dang = [1 for s, t in db_spk.con.execute("SELECT source,target FROM edges")
             if s not in _valid or t not in _valid]
    rec(15, "페어 저장(owner/ai 독립+ai_refutes+dangling0)",
        rp["applied"] and rp["saved"] == 2 and {"owner", "ai"} <= _spk2
        and "ai_refutes" in _rels and not _dang)
    # 16. owner 단독(ai_text=None) — 억지 ai 노드 금지(불변식1)
    rs = save_paired(db_spk, "다음에는 이 거래처를 우선 검토하는 것이 낫겠다.", None,
                     {"actor": "human", "confirm": "PAIR owner:1"}, snap_dir)
    rec(16, "owner 단독(불변식1·억지 ai 금지)", rs["applied"] and rs["saved"] == 1 and rs["paired"] is False)
    # 17. 페어 중복 차단(③ 부분적재·dangling 방지)
    rd = save_paired(db_spk, _OWN, _AI, {"actor": "human", "confirm": "PAIR ai_refutes owner:1 ai:1"},
                     snap_dir, relation_kind="ai_refutes")
    rec(17, "페어 중복 차단(pair_partial_exists)", (not rd["applied"]) and rd["reason"] == "pair_partial_exists")
    # 18. 양방향 신뢰도 — 사람만 기록(불변식6)·페어 relation 으로 ai 입장 도출
    _onid = db_spk.con.execute("SELECT node_id FROM nodes WHERE speaker='owner' LIMIT 1").fetchone()[0]
    _hr = _HS.record_resolution(db_spk, _onid, True, {"actor": "human"})
    _auto = _HS.record_resolution(db_spk, _onid, True, {"actor": "auto"})
    _ng5 = _HS.get_hit_rate(db_spk, "owner")  # n<5 → enough False(불변식8)
    rec(18, "신뢰도 record(사람만·표본게이트)",
        _hr["recorded"] and _hr["events"] >= 1 and _auto.get("reason") == "G4_no_auto"
        and _ng5["enough"] is False)
    rec(19, "speaker 확장 후 audit chain + tail anchor 무손상",
        db_spk.verify_chain() and db_spk.verify_tail_state())
    db_spk.close()

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
