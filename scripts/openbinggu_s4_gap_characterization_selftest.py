# -*- coding: utf-8 -*-
"""S4 GAP characterization (tests-only) — 현재 동작 pin. 구현 변경 0.

기준: docs/SAVE_GATE_S4_CHARACTERIZATION_DESIGN.md §2 GAP 표.
성격: gate-critical write core 의 미커버 분기를 '현재 동작 그대로' 고정하는 characterization.
      production 코드(scripts/ gate-critical, binggupack/) 미접촉 — import 후 호출만.
안전: 전 시나리오 temp DB · BINGGU_HOME temp 격리 · 운영 store(OPERATING_PATHS) mtime 불변.

대상 GAP:
  - A2/E1b : c2_check reader 명시차단 + save_selected actor allowlist(정규화/변형 전수) → G4 약화 0
  - B5     : staging_apply INSERT 예외 주입 → ROLLBACK(partial write 0)
  - F2~F4  : _maybe_promote_actor_by_gate fail-closed 4분기(승격/미승격/예외)
  - D11    : sqlite integrity_check=ok 공통 사후단언(save/차단 이후)

CLI: python openbinggu_s4_gap_characterization_selftest.py --selftest
"""
import os
import sys
import types
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from openbinggu_staging_write_selftest import (
    StagingDB, c2_check, staging_apply, base_pack, OPERATING_PATHS)
from openbinggu_conversation_candidate_save import (
    save_selected, _maybe_promote_actor_by_gate, CONVO)
from openbinggu_conversation_capture_preview import capture_preview
from openbinggu_deprecate_and_remind_g3 import open_g3, deprecate_item


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
        out_h = _maybe_promote_actor_by_gate(CONVO, [1], ctx_h)
        rec("F1", "actor=human → ctx 그대로(promoted_by 없음)",
            out_h.get("actor") == "human" and "actor_promoted_by" not in out_h)

        # F3: 비human + 게이트 미기록 → 승격 0(원 actor 유지)
        out_f3 = _maybe_promote_actor_by_gate(CONVO, [1], {"actor": "auto"})
        rec("F3", "비human + 게이트 미기록 → 승격 0(actor=auto 유지)",
            out_f3.get("actor") == "auto" and "actor_promoted_by" not in out_f3)

        # F2: 비human + 해당 문장이 사람 SAVE 발화로 기록됨 → human 승격 + 표식
        cands = capture_preview(CONVO)["candidates"]
        sent1 = cands[0]["sentence"]
        sgate.gate_record([sent1], path=sgate.gate_path())  # 사람 발화 기록(temp home)
        out_f2 = _maybe_promote_actor_by_gate(CONVO, [1], {"actor": "auto"})
        rec("F2", "비human + 사람 SAVE 기록 존재 → human 승격(actor_promoted_by=save_gate)",
            out_f2.get("actor") == "human" and out_f2.get("actor_promoted_by") == "save_gate")

        # F4: sgate import 시 예외 → except pass → 원 ctx(default-deny). sys.modules 치환.
        saved_mod = sys.modules.get("binggu_save_gate")
        broken = types.ModuleType("binggu_save_gate")

        def _raise(*a, **k):
            raise RuntimeError("sgate boom")
        broken.gate_human_for = _raise
        sys.modules["binggu_save_gate"] = broken
        try:
            out_f4 = _maybe_promote_actor_by_gate(CONVO, [1], {"actor": "auto"})
        finally:
            if saved_mod is not None:
                sys.modules["binggu_save_gate"] = saved_mod
            else:
                sys.modules.pop("binggu_save_gate", None)
        rec("F4", "sgate 오류/부재 → except pass → 승격 0(actor=auto 유지·fail-closed)",
            out_f4.get("actor") == "auto" and "actor_promoted_by" not in out_f4)

        # ============ D11 — integrity_check=ok 공통 사후단언 ============
        # 대상별: save 이후 + 차단 이후 모두 sqlite 무결.
        d_db = open_g3(os.path.join(tmp, "d11.sqlite"))
        # save 이후
        save_selected(d_db, CONVO, [1, 5], {"actor": "human", "confirm": "SAVE 1,5"},
                      snap_dir, due_date="2026-06-20")
        rec("D11-1", "candidate save 이후 integrity_check=ok", _integrity_ok(d_db))
        # 차단 이후(confirm 불일치)
        save_selected(d_db, CONVO, [2], {"actor": "human", "confirm": "SAVE 9"}, snap_dir)
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
