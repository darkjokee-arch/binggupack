# -*- coding: utf-8 -*-
"""binggu_save_ref_binding_selftest.py — save-n 참조 바인딩(스펙 ①~④) 정본 selftest.

사람 증명 = "preview + 사람의 save n 입력" 단일 원칙 검증:
  · pref(preview_ref) 결정성 + 훅 이중기록(ref 1행 + 레거시 sh 병기 — 구 소비자 무수정 호환)
  · save/pair/hosted 승격 = save_gate_ref (Claude Code 세션 CLAUDECODE=1 안에서도 훅 앵커만 human)
  · CLAUDECODE=1 + 앵커 없음 → reader BLOCK(①의 위조불가 성질) · CLAUDECODE 부재 → cli_command
    human(② 터미널 = 명령 직접 입력) — 단 write 성사는 core confirm 정확일치를 여전히 통과해야
  · 타 preview 동일 문장 불통(구 내용-hash 약점 봉인 · 스펙 ④) · stale/미래ts/부분 idx 거부
  · hosted pull 저장 경로 approval_requests 무증가(스펙 ③ — 저장 경로 approval 배선 제거)
  · explicit 모드 패리티(MUST_FIX 2): preview(explicit=False)→pair · preview --explicit→save --explicit

결정성: 격리 BINGGU_HOME(tempfile) · ts/now 명시 주입(wall-clock 경과 가정 0 — 논리시계 케이스는
now 인자, CLI 통합 케이스는 seed ts=time.time() 로 age>=0 만 가정) · CLAUDECODE/BINGGU_TRUSTED_CLI/
BINGGU_STRICT_HUMAN_GATE 명시 set/pop. 운영 ~/.binggupack 미접촉(OPERATING_PATHS sentinel).
CLI: python scripts/binggu_save_ref_binding_selftest.py [--selftest]
"""
from pathlib import Path
import json
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("BINGGU_SEMANTIC_OFF", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 논리시계 기준(과거 고정값) — 대조는 now 명시 주입으로만 판정(wall clock 경과 무관).
BASE_TS = 1_700_000_000

# probe 확인된 explicit=False 유효 판단 문장(기존 selftest 재사용 — 후보 산출 결정적)
S1 = "이 입찰은 마진이 낮아 보류하기로 결정했다."
S2 = "백업은 항상 작업 전에 먼저 해 둔다."
S3 = "이 변경은 회귀 위험이 커서 조심해야 한다."
T3 = S1 + " " + S2 + " " + S3
S_SHARED = "이 계약은 조건이 유리하여 진행하기로 결정했다."
S_OTHER = "신규 거래처는 항상 신용조사를 먼저 한다."
S_SPEC = "이 방침은 다음 분기에 재검토하기로 결정했다."
S_H1 = "캐시 전략은 이걸로 확정한다."


class _A:
    pass


def _args(**kw):
    a = _A()
    a.no_capture = True
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def _set_env(key, val):
    if val is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = val


def run():
    import binggu as BG
    import binggu_save_gate as sg
    import binggu_hosted_inbox as HI
    import binggu_publish_autopush as AP
    from openbinggu_conversation_capture_preview import capture_preview
    from openbinggu_conversation_candidate_save import save_selected
    from openbinggu_deprecate_and_remind_g3 import open_g3
    from openbinggu_save_intent_outbox_runner import (OPERATING_PATHS, SCHEMA_VER,
                                                      DEFAULT_TTL_S, intent_hash)

    checks = []

    def ck(name, ok):
        checks.append(bool(ok))
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))

    def _nodes(ledger):
        d = open_g3(ledger)
        try:
            return d.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        finally:
            d.close()

    def _apr(db):
        """approval_requests 무증가 단정용(테이블 부재=0) — 저장 경로 approval 배선 제거 증명."""
        try:
            return db.con.execute("SELECT count(*) FROM approval_requests").fetchone()[0]
        except Exception:
            return 0

    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    keep = {k: os.environ.get(k) for k in
            ("CLAUDECODE", "BINGGU_HOME", "BINGGU_TRUSTED_CLI", "BINGGU_STRICT_HUMAN_GATE")}
    root = tempfile.mkdtemp(prefix="bgp_saveref_")
    try:
        # 전 케이스 공통: env 명시 제어(로컬/CI 어디서 돌아도 동일) + home 격리
        os.environ.pop("BINGGU_TRUSTED_CLI", None)
        os.environ.pop("BINGGU_STRICT_HUMAN_GATE", None)
        _set_env("CLAUDECODE", None)
        _set_env("BINGGU_HOME", root)

        # ── R1. pref 결정성 — 후보 집합+순서에서 결정론적 파생 ─────────────────────
        c1 = capture_preview(T3)["candidates"]
        c2 = capture_preview(T3)["candidates"]
        pref = sg.preview_ref_for_candidates(c1)
        ck("R1 pref 결정성(동일 후보→동일 · 순서/부분집합 변경→상이)",
           len(c1) >= 3 and pref == sg.preview_ref_for_candidates(c2)
           and pref != sg.preview_ref_for_candidates(list(reversed(c1)))
           and pref != sg.preview_ref_for_candidates(c1[:1]))

        # ── R2. 훅 이중기록 — ref 레코드 1행 + 레거시 sh 행 병기(구 소비자 호환) ────
        d_r2 = os.path.join(root, "r2")
        os.makedirs(d_r2, exist_ok=True)
        lp = os.path.join(d_r2, "last_preview_candidates.json")
        gp = os.path.join(d_r2, "save_gate_log.jsonl")
        n_prev = sg.write_last_preview(c1, path=lp)
        pv = json.loads(Path(lp).read_text(encoding='utf-8'))
        n_rec = sg.gate_record_from_prompt("세이브 1,3", preview_path=lp, gate_path=gp, ts=BASE_TS)
        rows = [json.loads(x) for x in Path(gp).read_text(encoding='utf-8').splitlines(keepends=True) if x.strip()]
        refs = [r for r in rows if r.get("pref")]
        shs = [r for r in rows if r.get("sh")]
        ck("R2 훅 이중기록(ref 1행+sh 2행 병기 · pref/idxs/explicit 일치)",
           n_prev == len(c1) and n_rec == 2
           and pv.get("pref") == pref and pv.get("explicit") is False
           and len(refs) == 1 and refs[0]["pref"] == pref and refs[0]["idxs"] == [1, 3]
           and len(shs) == 2
           and sg._load_refs(path=gp) == {(pref, 1): BASE_TS, (pref, 3): BASE_TS}
           and set(sg._load(gp)) == {sg.sent_hash(c1[0]["sentence"]),
                                     sg.sent_hash(c1[2]["sentence"])})

        # ── R4. 타 preview 동일 문장 불통 — ④ ref 바인딩이 구 hash 약점을 봉인 ──────
        ca = capture_preview(S_SHARED + " " + S_OTHER)["candidates"]
        cb = capture_preview(S_SHARED)["candidates"]
        pref_a = sg.preview_ref_for_candidates(ca)
        pref_b = sg.preview_ref_for_candidates(cb)
        gp4 = os.path.join(root, "r4_gate.jsonl")
        sg.gate_record_ref(pref_a, [1], ts=BASE_TS, path=gp4)
        sg.gate_record([cb[0]["sentence"]], ts=BASE_TS, path=gp4)   # 구 sh 앵커(대조군)
        ck("R4 타 preview 동일 문장: ref 불통(세대 구분) · 구 hash 는 통과(약점 봉인 증명)",
           len(ca) >= 2 and len(cb) == 1 and ca[0]["sentence"] == cb[0]["sentence"]
           and pref_a != pref_b
           and not sg.gate_human_for_ref(pref_b, [1], path=gp4, now=BASE_TS + 10)
           and sg.gate_human_for_ref(pref_a, [1], path=gp4, now=BASE_TS + 10)
           and sg.gate_human_for([cb[0]["sentence"]], path=gp4, now=BASE_TS + 10))

        # ── R5. idx subset/superset — 기록된 idx 만 통과(all-or-nothing) ────────────
        ck("R5 idx subset 통과 · 미기록 idx 포함(superset) 차단",
           sg.gate_human_for_ref(pref, [1], path=gp, now=BASE_TS + 10)
           and sg.gate_human_for_ref(pref, [3], path=gp, now=BASE_TS + 10)
           and sg.gate_human_for_ref(pref, [1, 3], path=gp, now=BASE_TS + 10)
           and not sg.gate_human_for_ref(pref, [1, 2], path=gp, now=BASE_TS + 10)
           and not sg.gate_human_for_ref(pref, [2], path=gp, now=BASE_TS + 10))

        # ── R6. stale 창 + 미래-ts 무효 — 논리시계 주입(wall-clock 경과 가정 0) ─────
        W = sg.GATE_WINDOW_SEC
        ck("R6 신선도 창 이내 통과 · 창 초과 stale 거부 · 미래-ts(age<0) 거부",
           sg.gate_human_for_ref(pref, [1], path=gp, now=BASE_TS + W)
           and not sg.gate_human_for_ref(pref, [1], path=gp, now=BASE_TS + W + 1)
           and not sg.gate_human_for_ref(pref, [1], path=gp, now=BASE_TS - 1))

        # ── R7. CLAUDECODE=1(에이전트 세션): 훅 앵커만 human — deny 전용 가드 ───────
        d_r7 = os.path.join(root, "r7")
        os.makedirs(d_r7, exist_ok=True)
        led7 = os.path.join(d_r7, "ledger.sqlite")
        gp7 = os.path.join(d_r7, "save_gate_log.jsonl")
        sg.gate_record_ref(pref, [1, 3], ts=time.time(), path=gp7)   # 훅 앵커(age>=0 만 가정)
        _set_env("CLAUDECODE", "1")
        ctx_a = BG._resolve_human_ctx(led7, None, "SAVE 1")
        ctx_b = BG._resolve_human_ctx(led7, [("f" * 16, [1])], "SAVE 1")
        ctx_c = BG._resolve_human_ctx(led7, [(pref, [1, 3])], "SAVE 1,3")
        ck("R7 CLAUDECODE=1: 무앵커/타 pref → reader(agent_session_unanchored) · ref 앵커 → human(save_gate_ref)",
           ctx_a["actor"] == "reader" and ctx_a["actor_source"] == "agent_session_unanchored"
           and ctx_b["actor"] == "reader" and ctx_b["actor_source"] == "agent_session_unanchored"
           and ctx_c["actor"] == "human" and ctx_c["actor_source"] == "save_gate_ref")

        # ── R8. CLAUDECODE 부재(터미널) = cli_command human · core confirm 은 불변 ───
        _set_env("CLAUDECODE", None)
        ctx_t = BG._resolve_human_ctx(led7, None, "SAVE 1")
        d_r8 = os.path.join(root, "r8")
        snap8 = os.path.join(d_r8, "snapshots")
        os.makedirs(snap8, exist_ok=True)
        led8 = os.path.join(d_r8, "ledger.sqlite")
        db8 = open_g3(led8)
        r8 = save_selected(db8, T3, [1], {"actor": "human", "confirm": "SAVE 999"}, snap8)
        db8.close()
        ck("R8 터미널(cli_command)=human · confirm 불일치는 core 가 write 0",
           ctx_t["actor"] == "human" and ctx_t["actor_source"] == "cli_command"
           and (not r8.get("applied")) and r8.get("reason") == "confirm_phrase_mismatch"
           and _nodes(led8) == 0)

        # ── R3. ① 실흐름 통합: preview → '세이브 1' 훅 → save (CLAUDECODE=1) ────────
        d_r3 = os.path.join(root, "r3home")
        os.makedirs(d_r3, exist_ok=True)
        _set_env("BINGGU_HOME", d_r3)
        _set_env("CLAUDECODE", "1")
        led3 = os.path.join(d_r3, "ledger.sqlite")
        BG.cmd_init(_args(ledger=led3))
        rc_p = BG.cmd_preview(_args(ledger=led3, text=S_SHARED))
        sg.gate_record_from_prompt("세이브 1", ts=time.time())
        rc_s = BG.cmd_save(_args(ledger=led3, text=S_SHARED, preview_id=BG._preview_id(S_SHARED),
                                 pick="1", confirm="SAVE 1", due=None))
        ck("R3 save ref 승격: preview→훅 '세이브 1'→save 저장 성공(에이전트 세션 내 훅 앵커)",
           rc_p == 0 and rc_s == 0 and _nodes(led3) == 1)

        # ── R9. pair 2-ref + explicit 패리티 A(preview False → pair 동일 모드 재계산) ─
        d_r9 = os.path.join(root, "r9home")
        os.makedirs(d_r9, exist_ok=True)
        _set_env("BINGGU_HOME", d_r9)
        led9 = os.path.join(d_r9, "ledger.sqlite")
        BG.cmd_init(_args(ledger=led9))
        BG.cmd_preview(_args(ledger=led9, text=S_SHARED))
        sg.gate_record_from_prompt("세이브 1", ts=time.time())
        BG.cmd_preview(_args(ledger=led9, text=S_OTHER))
        sg.gate_record_from_prompt("세이브 1", ts=time.time())
        rc_pair = BG.cmd_pair(_args(ledger=led9, owner_text=S_SHARED, ai_text=S_OTHER,
                                    by="ai", relation="refutes", owner_pick=1, ai_pick=1,
                                    confirm="PAIR ai_refutes owner:1 ai:1", due=None))
        ck("R9 pair 2-ref(owner/ai 각 훅 앵커) 승격 + explicit 패리티(MUST_FIX 2 회귀 봉인)",
           rc_pair == 0 and _nodes(led9) == 2)

        # ── R13. explicit 패리티 B: preview --explicit → save --explicit ────────────
        d_r13 = os.path.join(root, "r13home")
        os.makedirs(d_r13, exist_ok=True)
        _set_env("BINGGU_HOME", d_r13)
        led13 = os.path.join(d_r13, "ledger.sqlite")
        BG.cmd_init(_args(ledger=led13))
        BG.cmd_preview(_args(ledger=led13, text=S_SPEC), explicit=True)
        sg.gate_record_from_prompt("세이브 1", ts=time.time())
        rc13 = BG.cmd_save(_args(ledger=led13, text=S_SPEC, preview_id=BG._preview_id(S_SPEC),
                                 pick="1", confirm="SAVE 1", due=None, explicit=True))
        ck("R13 explicit 모드 패리티: last_preview 의 explicit 기록으로 동일 재계산 저장",
           rc13 == 0 and _nodes(led13) == 1)

        # ── R10. hosted pull — 성공/human_save_required/confirm 불일치/approval 무증가 ─
        h_home = os.path.join(root, "hosted_home")
        staging = HI.staging_dir_for(h_home)
        snap_h = os.path.join(h_home, "snapshots")
        os.makedirs(staging, exist_ok=True)
        os.makedirs(snap_h, exist_ok=True)
        led_h = os.path.join(h_home, "ledger.sqlite")
        NOW_H = int(time.time())

        def _mk_intent(text):
            confirm = "SAVE 1"
            it = {"schema_ver": SCHEMA_VER, "text": text, "indices": [1], "confirm": confirm,
                  "intent_id": intent_hash(text, [1], confirm),
                  "created_ts": NOW_H - 10, "ttl_s": DEFAULT_TTL_S, "source": "hosted"}
            with open(os.path.join(staging, it["intent_id"] + ".json"), "w", encoding="utf-8") as f:
                json.dump(it, f, ensure_ascii=False)
            return it["intent_id"]

        _mk_intent(S1)
        _mk_intent(S_H1)
        _set_env("BINGGU_HOME", h_home)
        _set_env("CLAUDECODE", "1")
        db_h = open_g3(led_h)
        pref_h = HI.write_inbox_preview(staging, NOW_H)   # inbox 렌더 = 1 intent = 1 row
        # a) 앵커 없음(에이전트 세션) → human_save_required · write 0 · 원문 보존
        ctx0 = BG._resolve_human_ctx(led_h, [(pref_h, [1])], "SAVE 1")
        r0 = HI.commit_selected(db_h, h_home, staging, [1], ctx0, "SAVE 1", snap_h, NOW_H)
        apr0 = _apr(db_h)
        n_stg0 = len([f for f in os.listdir(staging) if f.endswith(".json")])
        # b) 사람 '세이브 1' 발화(훅) → save_gate_ref 승격 → atomic 저장 · archive
        sg.gate_record_from_prompt("세이브 1", ts=time.time())
        ctx1 = BG._resolve_human_ctx(led_h, [(pref_h, [1])], "SAVE 1")
        r1 = HI.commit_selected(db_h, h_home, staging, [1], ctx1, "SAVE 1", snap_h, NOW_H)
        apr1 = _apr(db_h)
        n_stg1 = len([f for f in os.listdir(staging) if f.endswith(".json")])
        # c) 터미널 human 이어도 confirm 불일치(남은 intent idx=1 · 'SAVE 2') → write 0
        _set_env("CLAUDECODE", None)
        ctx2 = BG._resolve_human_ctx(led_h, None, "SAVE 2")
        r2 = HI.commit_selected(db_h, h_home, staging, [1], ctx2, "SAVE 2", snap_h, NOW_H)
        apr2 = _apr(db_h)
        n_act = db_h.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
        db_h.close()
        ck("R10a hosted: 에이전트 세션 무앵커 → human_save_required · write 0 · 원문 보존",
           r0.get("write") == 0 and r0.get("reason") == "human_save_required"
           and r0.get("guidance") and n_stg0 == 2)
        ck("R10b hosted: 훅 '세이브 1' → save_gate_ref 승격 · atomic 저장 1건 · 원문 archive",
           ctx1["actor_source"] == "save_gate_ref" and r1.get("write") == 1
           and r1.get("applied") == 1 and n_stg1 == 1 and n_act == 1)
        ck("R10c hosted: confirm 불일치 → confirm_phrase_mismatch · write 0",
           r2.get("write") == 0 and r2.get("reason") == "confirm_phrase_mismatch")
        ck("R10d hosted: 전 구간 approval_requests 무증가(스펙 ③ 저장 경로 approval 배선 제거)",
           apr0 == 0 and apr1 == 0 and apr2 == 0)

        # ── R11. 신구 gate log 혼재 파싱 — 구 소비자(_load)/신 소비자(_load_refs) 격리 ─
        gp11 = os.path.join(root, "r11_gate.jsonl")
        with open(gp11, "w", encoding="utf-8") as f:
            f.write(json.dumps({"sh": "a" * 16, "ts": BASE_TS, "source": "user_prompt"}) + "\n")
            f.write("깨진 라인 (json 아님)\n")
            f.write("\n")
            f.write(json.dumps({"pref": "b" * 16, "idxs": [2], "ts": BASE_TS + 1,
                                "source": "user_prompt"}) + "\n")
        ck("R11 신구 혼재 로그: _load=sh 만 · _load_refs=ref 만 · 깨진 라인 skip",
           sg._load(gp11) == {"a" * 16: BASE_TS}
           and sg._load_refs(path=gp11) == {("b" * 16, 2): BASE_TS + 1})

        # ── R12. sh 병기 → autopush has_human_save_record 무수정 호환 유지 ──────────
        ck("R12 autopush has_human_save_record: 훅 이중기록의 sh 행으로 True 유지",
           AP.has_human_save_record([("n1", "judgment", c1[0]["sentence"])], gate_path=gp) is True
           and AP.has_human_save_record([("n2", "judgment", "전혀 기록되지 않은 문장이다.")],
                                        gate_path=gp) is False)
    finally:
        for k, v in keep.items():
            _set_env(k, v)
        shutil.rmtree(root, ignore_errors=True)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    checks.append(op_before == op_after)
    print("  [%s] Z 운영 store 불변" % ("OK" if op_before == op_after else "FAIL"))
    checks.append(not os.path.exists(root))
    print("  [%s] Z temp 정리" % ("OK" if not os.path.exists(root) else "FAIL"))

    ok = all(checks)
    print("-" * 74)
    print("=== %d/%d ===" % (sum(checks), len(checks)))
    print("GATE=%s" % ("GO" if ok else "NO-GO"))
    return 0 if ok else 1


if __name__ == "__main__":
    print("=" * 74)
    print("save-n 참조 바인딩 selftest — 스펙 ①~④ · temp 격리 · 운영 store 접근 0")
    print("=" * 74)
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        sys.exit(run())
    print("usage: binggu_save_ref_binding_selftest.py [--selftest]")
    sys.exit(2)
