# -*- coding: utf-8 -*-
"""S4 GAP characterization (tests-only) — 현재 동작 pin. 구현 변경 0.

기준: docs/SAVE_GATE_S4_CHARACTERIZATION_DESIGN.md §2 GAP 표.
성격: gate-critical write core 의 미커버 분기를 '현재 동작 그대로' 고정하는 characterization.
      production 코드(scripts/ gate-critical, binggupack/) 미접촉 — import 후 호출만.
안전: 전 시나리오 temp DB · BINGGU_HOME temp 격리 · 운영 store(OPERATING_PATHS) mtime 불변.

대상 GAP:
  - A2/E1b : c2_check reader 명시차단 + save_selected actor allowlist(정규화/변형 전수) → G4 약화 0
  - B5     : staging_apply INSERT 예외 주입 → ROLLBACK(partial write 0)
  - F1~F4  : _maybe_promote_actor_by_gate fail-closed 분기(save-n 참조 바인딩: human 유지/미기록
             승격0/sh-only 승격0/ref 기록 시 actor_promoted_by=='save_gate_ref'/예외 fail-closed)
  - D11    : sqlite integrity_check=ok 공통 사후단언(save/차단 이후)
  - B-low  : H4/H5/J3/K4(deprecate_g3 가드) + L2/N2/O3/O4(save_gate read/validation) early-return
  - C-high : A4/A6/A10(c2_check) + B9/B10(staging_apply) + C2/C3(tombstone) + D3/D9/D10(StagingDB)
             + E3/E6/E7(save_selected) — 본체 무수정·외부 호출+단언/monkeypatch만

CLI: python openbinggu_s4_gap_characterization_selftest.py --selftest
"""
import os
import sys
import types
import sqlite3
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from openbinggu_staging_write_selftest import (
    StagingDB, c2_check, staging_apply, base_pack, OPERATING_PATHS, tombstone, _hash)
from openbinggu_conversation_candidate_save import (
    save_selected, _maybe_promote_actor_by_gate, CONVO)
import openbinggu_conversation_candidate_save as cs_mod  # E7 monkeypatch 대상(scan_residual_pii)
import openbinggu_a0_node_dryrun as a0mod  # E6 monkeypatch 대상(classify_node)
from openbinggu_conversation_capture_preview import capture_preview
from openbinggu_deprecate_and_remind_g3 import (
    open_g3, deprecate_item, resolve_review, classify_harvest_item)
from binggu_save_gate import gate_record, write_last_preview, gate_record_from_prompt


# SSOT should_capture 게이트 이후 CONVO(사실/상태문 위주)는 explicit=False 후보 0 — 후보 실재가
# 필요한 분기(F/D11/E6/E7)는 판단 문장 텍스트로 검증한다(현재 동작 pin 원칙 유지 · 구현 변경 0).
GTEXT = "이 입찰은 마진이 낮아 보류하기로 결정했다. 백업은 항상 작업 전에 먼저 해 둔다."


def _integrity_ok(db):
    return db.con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def run():
    before_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="obg_s4gap_")
    snap_dir = os.path.join(tmp, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    # BINGGU_HOME 격리 — _maybe_promote_actor_by_gate 가 path 인자 없이 gate_path() 를 읽으므로
    #   운영 ~/.binggupack 미접촉 보장(빈 기록장 → 승격 0 = fail-closed 기본).
    home0 = os.environ.get("BINGGU_HOME")
    os.environ["BINGGU_HOME"] = os.path.join(tmp, "home_iso")
    os.makedirs(os.environ["BINGGU_HOME"], exist_ok=True)

    results = []

    def rec(cid, desc, ok):
        results.append((cid, desc, "PASS" if ok else "FAIL"))

    try:
        # ============ A2 — c2_check reader/auto 명시 차단(방어 ②) ============
        db = StagingDB(os.path.join(tmp, "a2.sqlite"))
        rec("A2-1", "c2_check actor=reader → G4_no_auto",
            c2_check(db, base_pack(), {"actor": "reader"}) == "G4_no_auto")
        rec("A2-2", "c2_check actor=auto → G4_no_auto",
            c2_check(db, base_pack(), {"actor": "auto"}) == "G4_no_auto")
        rec("A2-3", "c2_check actor=human(정상 pack) → None(통과)",
            c2_check(db, base_pack(), {"actor": "human"}) is None)
        db.close()

        # ============ E1b — save_selected actor allowlist 정규화/변형 전수 ============
        # 통과군: 정규화 후 'human' 인 변형 → actor 게이트 통과(applied) ⇒ 주방어 ①은 정규화 allowlist.
        # 차단군: human 외 전부(대소문자/공백/누락/agent/system/reader/auto) → G4_no_auto BLOCK.
        # ※ 현재 동작 그대로 pin — 새 동작 요구 아님. _maybe_promote 빈 기록장이라 비human 승격 0.
        db = open_g3(os.path.join(tmp, "e1b_pass.sqlite"))
        pass_actors = ["human", "Human", "HUMAN", " human ", "  Human  "]
        pass_ok = True
        for idx, a in enumerate(pass_actors, start=1):
            # 각 변형마다 다른 후보(중복 skip 회피): 1=문서, 5=판단 번갈아 + 신규 DB 분리 불필요
            r = save_selected(db, CONVO, [1, 5], {"actor": a, "confirm": "SAVE 1,5"}, snap_dir)
            # 첫 호출만 실제 저장, 이후는 skipped(동일 내용) — 핵심은 'G4_no_auto 로 막히지 않음'
            blocked_by_g4 = (not r.get("applied")) and r.get("reason") == "G4_no_auto"
            pass_ok = pass_ok and (not blocked_by_g4)
        rec("E1b-1", "human 정규화 변형(Human/HUMAN/공백) → actor 게이트 통과(G4 미차단)", pass_ok)

        db2 = open_g3(os.path.join(tmp, "e1b_block.sqlite"))
        block_actors = ["auto", "AUTO", "Auto", "reader", "READER", "agent", "system",
                        "ai", "claude", "", "  "]
        block_ok = all(
            (lambda r: (not r.get("applied")) and r.get("reason") == "G4_no_auto")(
                save_selected(db2, CONVO, [1], {"actor": a, "confirm": "SAVE 1"}, snap_dir))
            for a in block_actors)
        # actor 키 자체 누락도 G4_no_auto (위장 없이 차단)
        r_nokey = save_selected(db2, CONVO, [1], {"confirm": "SAVE 1"}, snap_dir)
        nokey_ok = (not r_nokey.get("applied")) and r_nokey.get("reason") == "G4_no_auto"
        rec("E1b-2", "human 외 전수(auto/AUTO/reader/agent/누락) → G4_no_auto BLOCK",
            block_ok and nokey_ok)
        # 차단군 동안 실제 write 0 (노드 0)
        n_block = db2.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        rec("E1b-3", "G4 차단군 동안 actual write 0(노드=0)", n_block == 0)
        db.close()
        db2.close()

        # ============ B5 — staging_apply INSERT 예외 주입 → ROLLBACK ============
        # c2_check 통과(edges/evidence 정상) 후 nodes 에서 KeyError 유발('type' 키 제거).
        #   → except 분기 ROLLBACK · reason "exception:KeyError" · partial write 0.
        db = StagingDB(os.path.join(tmp, "b5.sqlite"))
        bad = base_pack(pack_id="pB5", content="b5 예외 주입")
        del bad["nodes"][0]["type"]  # INSERT 시 n["type"] → KeyError
        before_chk = db.store_checksum()
        r = staging_apply(db, bad, {"actor": "human"}, snap_dir)
        n_nodes = db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        n_edges = db.con.execute("SELECT count(*) FROM edges").fetchone()[0]
        n_reg = db.con.execute("SELECT count(*) FROM applied_registry").fetchone()[0]
        after_chk = db.store_checksum()
        rec("B5-1", "INSERT 예외 → applied=False · reason exception:* ",
            (not r.get("applied")) and str(r.get("reason", "")).startswith("exception:"))
        rec("B5-2", "예외 ROLLBACK → partial write 0(nodes/edges/registry 0)",
            n_nodes == 0 and n_edges == 0 and n_reg == 0)
        rec("B5-3", "예외 후 store checksum 불변(body 무결)", before_chk == after_chk)
        rec("B5-4", "예외 후 sqlite integrity_check=ok", _integrity_ok(db))
        # 동일 DB 에 정상 pack 재투입 → 정상 동작(예외가 DB 손상 안 시킴)
        r2 = staging_apply(db, base_pack(pack_id="pB5ok", content="b5 정상 재투입"), {"actor": "human"}, snap_dir)
        rec("B5-5", "예외 후 정상 pack 재투입 성공(DB 미손상)",
            r2.get("applied") and db.con.execute("SELECT count(*) FROM nodes").fetchone()[0] == 1)
        db.close()

        # ============ F2~F4 — _maybe_promote_actor_by_gate fail-closed 4분기 ============
        import binggu_save_gate as sgate
        # 격리 gate 기록장(BINGGU_HOME temp 이미 설정 — gate_path() 가 그 아래)
        # F1: actor 이미 human → ctx 그대로(승격 무관·키 미부여)
        ctx_h = {"actor": "human", "x": 1}
        out_h = _maybe_promote_actor_by_gate(GTEXT, [1], ctx_h)
        rec("F1", "actor=human → ctx 그대로(promoted_by 없음)",
            out_h.get("actor") == "human" and "actor_promoted_by" not in out_h)

        # F3: 비human + 게이트 미기록 → 승격 0(원 actor 유지)
        out_f3 = _maybe_promote_actor_by_gate(GTEXT, [1], {"actor": "auto"})
        rec("F3", "비human + 게이트 미기록 → 승격 0(actor=auto 유지)",
            out_f3.get("actor") == "auto" and "actor_promoted_by" not in out_f3)

        # F3b: 레거시 sh 행만 있으면 승격 0 — 승격 정본은 ref 대조(구 내용-hash 약점 봉인 · 스펙 ④)
        cands = capture_preview(GTEXT)["candidates"]
        sent1 = cands[0]["sentence"]
        sgate.gate_record([sent1], path=sgate.gate_path())  # 구 sh 앵커만(temp home)
        out_f3b = _maybe_promote_actor_by_gate(GTEXT, [1], {"actor": "auto"})
        rec("F3b", "비human + sh-only 기록 → 승격 0(ref 대조가 정본)",
            len(cands) >= 2
            and out_f3b.get("actor") == "auto" and "actor_promoted_by" not in out_f3b)

        # F2: 비human + 해당 (preview_ref, idx) 가 사람 save-n 발화로 기록됨 → human 승격 + 표식
        pref_f2 = sgate.preview_ref_for_candidates(cands)
        sgate.gate_record_ref(pref_f2, [1], path=sgate.gate_path())  # 사람 발화 ref 기록(temp home)
        out_f2 = _maybe_promote_actor_by_gate(GTEXT, [1], {"actor": "auto"})
        rec("F2", "비human + 사람 save-n ref 기록 존재 → human 승격(actor_promoted_by=save_gate_ref)",
            out_f2.get("actor") == "human" and out_f2.get("actor_promoted_by") == "save_gate_ref")

        # F4: sgate import 시 예외 → except pass → 원 ctx(default-deny). sys.modules 치환.
        saved_mod = sys.modules.get("binggu_save_gate")
        broken = types.ModuleType("binggu_save_gate")

        def _raise(*a, **k):
            raise RuntimeError("sgate boom")
        broken.gate_human_for = _raise
        broken.gate_human_for_ref = _raise
        broken.preview_ref_for_candidates = _raise
        broken.last_preview_path = _raise
        sys.modules["binggu_save_gate"] = broken
        try:
            out_f4 = _maybe_promote_actor_by_gate(GTEXT, [1], {"actor": "auto"})
        finally:
            if saved_mod is not None:
                sys.modules["binggu_save_gate"] = saved_mod
            else:
                sys.modules.pop("binggu_save_gate", None)
        rec("F4", "sgate 오류/부재 → except pass → 승격 0(actor=auto 유지·fail-closed)",
            out_f4.get("actor") == "auto" and "actor_promoted_by" not in out_f4)

        # ============ D11 — integrity_check=ok 공통 사후단언 ============
        # 대상별: save 이후 + 차단 이후 모두 sqlite 무결. (후보 실재 필요 → GTEXT)
        d_db = open_g3(os.path.join(tmp, "d11.sqlite"))
        # save 이후
        save_selected(d_db, GTEXT, [1, 2], {"actor": "human", "confirm": "SAVE 1,2"},
                      snap_dir, due_date="2026-06-20")
        rec("D11-1", "candidate save 이후 integrity_check=ok", _integrity_ok(d_db))
        # 차단 이후(confirm 불일치)
        save_selected(d_db, GTEXT, [2], {"actor": "human", "confirm": "SAVE 9"}, snap_dir)
        rec("D11-2", "save 차단(confirm 불일치) 이후 integrity_check=ok", _integrity_ok(d_db))
        # deprecate 이후
        nid = d_db.con.execute("SELECT node_id FROM nodes LIMIT 1").fetchone()[0]
        deprecate_item(d_db, "node", nid, "테스트 기각 사유", {"actor": "human"}, snap_dir)
        rec("D11-3", "deprecate 이후 integrity_check=ok", _integrity_ok(d_db))
        d_db.close()
        # staging 직접 write 이후
        s_db = StagingDB(os.path.join(tmp, "d11s.sqlite"))
        staging_apply(s_db, base_pack(pack_id="pD11", content="d11 staging"), {"actor": "human"}, snap_dir)
        rec("D11-4", "staging_apply 이후 integrity_check=ok", _integrity_ok(s_db))
        s_db.close()
        # save_gate 는 jsonl(비 sqlite) — 무결=모든 라인 valid json
        import json as _json
        gpath = sgate.gate_path()
        jsonl_ok = True
        if os.path.exists(gpath):
            with open(gpath, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        _json.loads(line)  # 깨지면 예외 → FAIL
        rec("D11-5", "save_gate jsonl 기록장 무결(전 라인 valid json)", jsonl_ok)

        # ============ B-low — 저위험 GAP(deprecate_g3 가드 + save_gate read/validation) ============
        # H4·H5·J3·K4: deprecate_g3 추가 가드 분기. L2·N2·O3·O4: save_gate read/validation early-return.
        # 전부 actual write core 미경유 — 현재 동작 그대로 pin.
        bl_db = open_g3(os.path.join(tmp, "blow.sqlite"))
        bl_db.con.execute(
            "INSERT INTO nodes(node_id,node_type,sentence,candidate,promotion_allowed,state,pack_id,content_hash,created_at) "
            "VALUES('bn1','judgment','보류 판단 문장이다',1,0,'active','bltest','h','2026-06-25T00:00:00Z')")
        bl_db.con.commit()
        # H4: tombstoned 노드 deprecate → tombstoned_item
        tombstone(bl_db, "bn1", {"actor": "human"}, snap_dir)
        rH4 = deprecate_item(bl_db, "node", "bn1", "기각 사유", {"actor": "human"}, snap_dir)
        rec("H4", "tombstoned 노드 deprecate → tombstoned_item",
            (not rH4.get("applied")) and rH4.get("reason") == "tombstoned_item")
        # H5: kind∉{node,edge} → kind_invalid (row 조회 이전 반환)
        rH5 = deprecate_item(bl_db, "concept", "whatever", "사유", {"actor": "human"}, snap_dir)
        rec("H5", "kind=concept → kind_invalid",
            (not rH5.get("applied")) and rH5.get("reason") == "kind_invalid")
        # J3: resolve_review reason 공백 → resolve_reason_required (outcome 유효·pending 무관, reason 체크가 먼저)
        rJ3 = resolve_review(bl_db, "bn1", "성공", "  ", {"actor": "human"})
        rec("J3", "resolve reason 공백 → resolve_reason_required",
            (not rJ3.get("applied")) and rJ3.get("reason") == "resolve_reason_required")
        # K4: classify_harvest_item item_id 공백 → item_id_required
        rK4 = classify_harvest_item(bl_db, "  ", "keep", "근거", {"actor": "human"})
        rec("K4", "classify item_id 공백 → item_id_required",
            (not rK4.get("applied")) and rK4.get("reason") == "item_id_required")
        bl_db.close()

        # save_gate 저위험(temp 기록장 — BINGGU_HOME 격리 하, 운영 미접촉)
        gp_bl = os.path.join(tmp, "blow_gate.jsonl")
        lp_bl = os.path.join(tmp, "blow_preview.json")
        # L2: gate_record 빈/공백 문장 skip → 유효 1건만 기록
        nL2 = gate_record(["  ", "", "유효한 사람 발화 문장"], path=gp_bl)
        rec("L2", "gate_record 빈/공백 skip → 유효 1건만 기록", nL2 == 1)
        # N2: write_last_preview 빈 sentence 후보 skip → rows 제외 (+pref/explicit 필드 영속 확인)
        nN2 = write_last_preview([{"sentence": ""}, {"sentence": "후보 문장 가나다"}], path=lp_bl)
        with open(lp_bl, encoding="utf-8") as f:
            pv_n2 = _json.load(f)
        rec("N2", "write_last_preview 빈 sentence skip → rows 1 (+pref 16자·explicit 기록)",
            nN2 == 1 and len(pv_n2.get("pref") or "") == 16 and pv_n2.get("explicit") is False)
        # O3: gate_record_from_prompt preview 파일 부재 → 0 / 파싱 실패 → 0
        o3a = gate_record_from_prompt("SAVE 1", preview_path=os.path.join(tmp, "no_such.json"), gate_path=gp_bl)
        broken_pv = os.path.join(tmp, "broken_preview.json")
        with open(broken_pv, "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        o3b = gate_record_from_prompt("SAVE 1", preview_path=broken_pv, gate_path=gp_bl)
        rec("O3", "preview 파일 부재/파싱실패 → 0", o3a == 0 and o3b == 0)
        # O4: idx 매칭 0 → 0 (preview엔 idx1만, 'SAVE 9' 요청)
        write_last_preview([{"sentence": "단일 후보 라마바"}], path=lp_bl)
        o4 = gate_record_from_prompt("SAVE 9", preview_path=lp_bl, gate_path=gp_bl)
        rec("O4", "idx 매칭 0 → 0", o4 == 0)

        # ============ C-high — 고위험 GAP(actual write core 본체 무수정·외부 호출+단언) ============
        # 관측 설계: docs/SAVE_GATE_S4_HIGH_RISK_GAP_CHARACTERIZATION_PLAN.md §2.
        # 본체(staging_apply/save_selected/tombstone/StagingDB/c2_check) 무수정 — import-호출-단언만.

        # --- A4: c2_check evidence.source_missing → freshness_source_missing ---
        ch_db = StagingDB(os.path.join(tmp, "ch_a.sqlite"))
        pA4 = base_pack(); pA4["evidence"][0]["source_missing"] = True
        rec("A4", "c2_check source_missing → freshness_source_missing",
            c2_check(ch_db, pA4, {"actor": "human"}) == "freshness_source_missing")
        # --- A6: redaction_policy≠v1 → freshness_redaction_policy_changed (앞 freshness 분기 통과) ---
        pA6 = base_pack(); pA6["evidence"][0]["redaction_policy"] = "v2"
        rec("A6", "c2_check redaction≠v1 → freshness_redaction_policy_changed",
            c2_check(ch_db, pA6, {"actor": "human"}) == "freshness_redaction_policy_changed")
        # --- A10: 판정 순서 actor→evidence_refs→freshness→duplicate→backup ---
        # 1) actor=auto + evidence_refs 빈 + freshness 위반 → G4_no_auto(actor 1순위)
        p1 = base_pack(); p1["edges"][0]["evidence_refs"] = []; p1["evidence"][0]["source_missing"] = True
        o1 = c2_check(ch_db, p1, {"actor": "auto"}) == "G4_no_auto"
        # 2) human + evidence_refs 빈 + freshness 위반 → evidence_refs_missing(2순위)
        p2 = base_pack(); p2["edges"][0]["evidence_refs"] = []; p2["evidence"][0]["source_missing"] = True
        o2 = c2_check(ch_db, p2, {"actor": "human"}) == "evidence_refs_missing"
        # 3) human + evidence_refs 정상 + source_missing + duplicate 조건 → freshness_source_missing(freshness>duplicate)
        p3 = base_pack(pack_id="pA10", content="a10 순서"); p3["evidence"][0]["source_missing"] = True
        ch_db.con.execute("INSERT OR IGNORE INTO applied_registry VALUES(?,?,?)",
                          ("pA10", _hash("a10 순서"), "2026-06-25T00:00:00Z")); ch_db.con.commit()
        o3 = c2_check(ch_db, p3, {"actor": "human"}) == "freshness_source_missing"
        # 4) human + freshness 정상 + duplicate 선삽입 + backup_fail → duplicate_already_applied(duplicate>backup)
        p4 = base_pack(pack_id="pA10b", content="a10 dup")
        ch_db.con.execute("INSERT OR IGNORE INTO applied_registry VALUES(?,?,?)",
                          ("pA10b", _hash("a10 dup"), "2026-06-25T00:00:00Z")); ch_db.con.commit()
        o4d = c2_check(ch_db, p4, {"actor": "human", "backup_fail": True}) == "duplicate_already_applied"
        rec("A10", "c2_check 판정 순서 actor→evidence_refs→freshness→duplicate→backup",
            o1 and o2 and o3 and o4d)
        ch_db.close()

        # --- B9: staging_apply 정상 → applied=True + snapshot 파일 존재(snap_dir 하위) ---
        b9_db = StagingDB(os.path.join(tmp, "ch_b9.sqlite"))
        rB9 = staging_apply(b9_db, base_pack(), {"actor": "human"}, snap_dir)
        rec("B9", "staging_apply 정상 → snapshot 파일 존재(snap_dir 하위)",
            rB9.get("applied") and rB9.get("snapshot")
            and os.path.exists(rB9["snapshot"]) and os.path.dirname(rB9["snapshot"]) == snap_dir)
        b9_db.close()

        # --- B10: BLOCK(backup_fail) 시 before==after checksum 불변 + audit before_hash==after_hash ---
        b10_db = StagingDB(os.path.join(tmp, "ch_b10.sqlite"))
        before10 = b10_db.store_checksum()
        rB10 = staging_apply(b10_db, base_pack(pack_id="pB10"), {"actor": "human", "backup_fail": True}, snap_dir)
        after10 = b10_db.store_checksum()
        arow = b10_db.con.execute("SELECT before_hash, after_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        rec("B10", "staging_apply BLOCK → checksum 불변 + audit before==after(write 0)",
            (not rB10.get("applied")) and before10 == after10 and arow and arow[0] == arow[1])
        b10_db.close()

        # --- C2: tombstone 미존재 node_id → {state:None, physical_present:False} ---
        c2_db = StagingDB(os.path.join(tmp, "ch_c2.sqlite"))
        tC2 = tombstone(c2_db, "nonexistent_node", {"actor": "human"}, snap_dir)
        rec("C2", "tombstone 미존재 node → {state:None, physical_present:False}",
            tC2 == {"state": None, "physical_present": False})
        c2_db.close()

        # --- C3: tombstone 정상 → state=tombstoned + snapshot 파일 + audit ALLOW + lock 해제 ---
        c3_db = StagingDB(os.path.join(tmp, "ch_c3.sqlite"))
        staging_apply(c3_db, base_pack(), {"actor": "human"}, snap_dir)
        before_snaps = set(os.listdir(snap_dir))
        tC3 = tombstone(c3_db, "n1", {"actor": "human"}, snap_dir)
        new_snaps = set(os.listdir(snap_dir)) - before_snaps
        snap_made = any(n.startswith("snap_t_") for n in new_snaps)
        aud_allow = c3_db.con.execute(
            "SELECT count(*) FROM audit_log WHERE action='tombstone' AND result='ALLOW' AND reason_code IS NULL").fetchone()[0]
        lock_released = not os.path.exists(c3_db.path + ".lock")
        rec("C3", "tombstone 정상 → tombstoned+snapshot+audit ALLOW+lock 해제",
            tC3.get("state") == "tombstoned" and tC3.get("physical_present")
            and snap_made and aud_allow == 1 and lock_released)
        c3_db.close()

        # --- D3: write_lock 같은 pid 재진입 허용 / 타 pid → RuntimeError ---
        d3_db = StagingDB(os.path.join(tmp, "ch_d3.sqlite"))
        with open(d3_db.path + ".lock", "w") as f:
            f.write(str(os.getpid()))
        entered = False
        with d3_db.write_lock():
            entered = True
        if os.path.exists(d3_db.path + ".lock"):
            os.remove(d3_db.path + ".lock")
        # 대조군: 타 pid lock → RuntimeError
        with open(d3_db.path + ".lock", "w") as f:
            f.write("999999")
        other_raised = False
        try:
            with d3_db.write_lock():
                pass
        except RuntimeError:
            other_raised = True
        os.remove(d3_db.path + ".lock")
        rec("D3", "write_lock 같은 pid 재진입 허용 / 타 pid RuntimeError", entered and other_raised)
        d3_db.close()

        # --- D9: snapshot wal_checkpoint 후 copy → 파일 생성 + 복사본 nodes count 일치 ---
        d9_db = StagingDB(os.path.join(tmp, "ch_d9.sqlite"))
        staging_apply(d9_db, base_pack(), {"actor": "human"}, snap_dir)
        snap_d9 = d9_db.snapshot(snap_dir, "snap_d9")
        orig_cnt = d9_db.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        sc = sqlite3.connect(snap_d9)
        snap_cnt = sc.execute("SELECT count(*) FROM nodes").fetchone()[0]
        sc.close()
        rec("D9", "snapshot 파일 생성 + 복사본 nodes count 원본 일치",
            snap_d9 == os.path.join(snap_dir, "snap_d9") and os.path.exists(snap_d9)
            and snap_cnt == orig_cnt and orig_cnt == 1)
        d9_db.close()

        # --- D10: store_checksum 결정성(동일 상태 2회 동일·상태 민감·복원) ---
        d10_db = StagingDB(os.path.join(tmp, "ch_d10.sqlite"))
        staging_apply(d10_db, base_pack(), {"actor": "human"}, snap_dir)
        k1 = d10_db.store_checksum(); k2 = d10_db.store_checksum()
        d10_db.con.execute("INSERT INTO nodes(node_id,node_type,sentence) VALUES('extra10','judgment','추가 노드')")
        d10_db.con.commit()
        k3 = d10_db.store_checksum()
        d10_db.con.execute("DELETE FROM nodes WHERE node_id='extra10'"); d10_db.con.commit()
        k4 = d10_db.store_checksum()
        rec("D10", "store_checksum 결정성(2회 동일·변경 감지·삭제 후 복원)",
            k1 == k2 and k3 != k1 and k4 == k1)
        d10_db.close()

        # --- E3: save_selected indices=[] → empty_selection (confirm="SAVE " 정합) ---
        e3_db = open_g3(os.path.join(tmp, "ch_e3.sqlite"))
        rE3 = save_selected(e3_db, CONVO, [], {"actor": "human", "confirm": "SAVE "}, snap_dir)
        rec("E3", "save_selected indices=[] → empty_selection",
            (not rE3.get("applied")) and rE3.get("reason") == "empty_selection")
        e3_db.close()

        # --- E6: A0 REVIEW & not allow_review → a0_review_needs_explicit_allow (a0.classify_node monkeypatch) ---
        e6_db = open_g3(os.path.join(tmp, "ch_e6.sqlite"))
        _orig_classify = a0mod.classify_node
        a0mod.classify_node = lambda node, status=None: {"verdict": "REVIEW"}
        try:
            rE6 = save_selected(e6_db, GTEXT, [1], {"actor": "human", "confirm": "SAVE 1"}, snap_dir)
        finally:
            a0mod.classify_node = _orig_classify
        rec("E6", "A0 REVIEW & not allow_review → a0_review_needs_explicit_allow",
            (not rE6.get("applied")) and rE6.get("rejected", {}).get("a0_review_needs_explicit_allow") == 1)
        e6_db.close()

        # --- E7: PII/secret 재스캔 hit → pii_or_secret (scan_residual_pii monkeypatch) ---
        e7_db = open_g3(os.path.join(tmp, "ch_e7.sqlite"))
        _orig_scan = cs_mod.scan_residual_pii
        cs_mod.scan_residual_pii = lambda s: ["FAKE_PII_HIT"]
        try:
            rE7 = save_selected(e7_db, GTEXT, [1], {"actor": "human", "confirm": "SAVE 1"}, snap_dir)
        finally:
            cs_mod.scan_residual_pii = _orig_scan
        rec("E7", "PII/secret 재스캔 hit → pii_or_secret",
            (not rE7.get("applied")) and rE7.get("rejected", {}).get("pii_or_secret") == 1)
        e7_db.close()

    finally:
        if home0 is None:
            os.environ.pop("BINGGU_HOME", None)
        else:
            os.environ["BINGGU_HOME"] = home0

    after_mtime = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    store_unchanged = before_mtime == after_mtime
    shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 74)
    print("OpenBinggu S4 GAP characterization (tests-only · 현재 동작 pin · 구현 변경 0)")
    print("=" * 74)
    npass = sum(1 for _, _, v in results if v == "PASS")
    for cid, desc, v in results:
        print("%s %-6s %s" % ("[OK]" if v == "PASS" else "[X]", cid, desc))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("operating_store_unchanged=%s  production_code_touched=0" % store_unchanged)
    gate = "GO" if (npass == len(results) and store_unchanged) else "NO-GO"
    print("GATE:", gate)
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(run())
    print("usage: openbinggu_s4_gap_characterization_selftest.py [--selftest]")
    sys.exit(2)
