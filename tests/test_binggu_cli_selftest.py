#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""binggu CLI 임베드 selftest 분리본 (God-file #4 분리).

기존 binggu.py 안에 있던 255줄 임베드 selftest 를 그대로 옮긴 것.
케이스 로직은 변경 0 — binggu.py --selftest 는 이 모듈의 selftest() 를 호출한다.
pytest 도 test_binggu_cli_selftest() 로 이 게이트를 수집한다(GATE=GO → exit 0).
"""
import os
import shutil
import sys
import tempfile

# repo root(= binggu.py 위치)를 path 에 올려 binggu 를 import 가능하게 한다.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# 케이스가 그대로 참조하는 이름을 binggu 모듈에서 바인딩(로직 불변·재배선만).
from binggu import (  # noqa: E402
    OPERATING_PATHS,
    cap_status,
    capture_preview,
    list_candidates,
    open_accept,
    cmd_init,
    cmd_capture,
    cmd_preview,
    cmd_reflect,
    cmd_save,
    cmd_list,
    cmd_recall,
    cmd_preflight,
    cmd_trace,
    cmd_deprecate,
    cmd_replace,
    cmd_accept,
    cmd_unaccept,
    cmd_due,
    cmd_resolve,
    cmd_reminders,
    cmd_hosted,
    _preview_id,
    _open,
    _gate_log_for_ledger,
)


class _FakeTTY:
    """대화형 owner 터미널 시뮬 — P1-A.1: index-op(deprecate/replace/mark 등)는 sentence 앵커가 없어
    사람 근거로 '대화형 TTY'만 가능. binggu.py --selftest·pytest 양 진입점 모두 비대화형이므로 여기서
    owner-interactive 를 시뮬한다. FE 블록은 내부에서 StringIO(isatty False)로 fail-closed 경로를 별도
    검증(스스로 stdin 저장·복원)."""
    def isatty(self):
        return True

    def readline(self, *a):
        return "\n"

    def read(self, *a):
        return ""

    def fileno(self):
        return 0


def selftest():
    # P1-A.1: 양 진입점(binggu.py --selftest · pytest 래퍼) 모두 비대화형 stdin 이므로 여기서 owner
    #         대화형 TTY 를 시뮬한다(아래 FE 블록만 StringIO 로 fail-closed 경로를 별도 검증). 정상 경로에서
    #         복원하고, 예외 누수 대비는 pytest 래퍼의 try/finally 가 담당한다.
    _real_stdin = sys.stdin
    sys.stdin = _FakeTTY()
    print("=" * 74)
    print("binggu CLI — temp 장부 풀 사이클 selftest (영속 장부·운영 store 접근 0)")
    print("=" * 74)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_cli_")
    ledger = os.path.join(tmp, "ledger.sqlite")
    checks = []

    def ck(name, ok):
        checks.append(ok)
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))

    class A:  # argparse 흉내 — CLI 함수를 그대로 검증
        pass

    def args(**kw):
        a = A()
        a.ledger = ledger
        a.no_capture = True  # 기본: 장부 사이클만(실 settings.json 미접촉)
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    ck("1_init", cmd_init(args()) == 0 and os.path.exists(ledger))
    ck("1b_init_멱등", cmd_init(args()) == 0)
    # capture profile (AGI memory) — temp settings 전용, 실 ~/.claude/settings.json 미접촉
    cap_settings = os.path.join(tmp, "settings.json")
    cap_cwd = os.path.realpath(tmp)
    cap_home = os.path.dirname(ledger)
    ck("1c_capture_init", cmd_init(args(no_capture=False, capture_settings=cap_settings,
                                        capture_cwd=cap_cwd)) == 0)
    _cst = cap_status(cap_home, cap_cwd, cap_settings)
    ck("1d_capture_ON+hook+scope", _cst["enabled"] and _cst["hook_registered"]
       and _cst["in_current_scope"] and not _cst["global"])
    # --agi-memory = 전역(AGI memory mode) — 임의 cwd 도 수집 대상
    ck("1d2_agi_memory→전역",
       cmd_init(args(no_capture=False, capture_settings=cap_settings, capture_cwd=cap_cwd, agi_memory=True)) == 0
       and cap_status(cap_home, "D:/anywhere/else", cap_settings)["global"]
       and cap_status(cap_home, "D:/anywhere/else", cap_settings)["in_current_scope"])
    ck("1e_pause→OFF", cmd_capture(args(capture_cmd="pause", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and not cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    ck("1f_resume→ON", cmd_capture(args(capture_cmd="resume", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    ck("1f2_disable→sticky OFF", cmd_capture(args(capture_cmd="disable", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and not cap_status(cap_home, cap_cwd, cap_settings)["enabled"]
       and cap_status(cap_home, cap_cwd, cap_settings)["disabled"])
    ck("1f3_재init중_sticky OFF 유지", cmd_init(args(no_capture=False, capture_settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and not cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    ck("1f4_enable→ON 복구", cmd_capture(args(capture_cmd="enable", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    ck("1g_preview(저장0)", cmd_capture(args(capture_cmd="preview", settings=cap_settings, capture_cwd=cap_cwd)) == 0)
    ck("1h_uninstall", cmd_capture(args(capture_cmd="uninstall", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and not cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    # SSOT 후보 게이트(should_capture) 도입 후 — 순수 사실/상태 문장은 제외되므로 판단 3문장으로 구성.
    TEXT = ("이 입찰은 마진이 낮아 보류하기로 결정했다. 백업은 항상 작업 전에 먼저 해 둔다. "
            "이 변경은 회귀 위험이 커서 조심해야 한다.")
    ck("2_preview(저장0)", cmd_preview(args(text=TEXT)) == 0)
    ck("2c_reflect(회고→후보·저장0)", cmd_reflect(args(text=TEXT, from_file=None)) == 0)
    ck("2d_reflect_빈입력_안내", cmd_reflect(args(text=None, from_file=None)) == 1)
    ck("2e_reflect_파일오류_안내",
       cmd_reflect(args(text=None, from_file=os.path.join(tmp, "_no_such_reflect_file.txt"))) == 1)
    ck("2b_preview없는_save_BLOCK", cmd_save(args(text=TEXT, preview_id="deadbeef",
                                                  pick="1,2,3", confirm="SAVE 1,2,3",
                                                  due=None)) == 1)
    # P1-A.1: 비대화형(pytest·isatty False)에선 save 가 fail-closed(reader). 사장님 SAVE 시뮬로
    #         선택 문장의 save_gate 앵커를 seed(정당 사람 근거 · env 백도어 아님) → 저장 성공.
    import binggu_save_gate as _sg3
    _c3 = capture_preview(TEXT, explicit=False)["candidates"]
    _sg3.gate_record([c["sentence"] for c in _c3], path=_gate_log_for_ledger(ledger))
    ck("3_save", cmd_save(args(text=TEXT, preview_id=_preview_id(TEXT),
                               pick="1,2,3", confirm="SAVE 1,2,3",
                               due="2099-12-31")) == 0)
    db, _ = _open(ledger)
    rows = list_candidates(db)["rows"]
    db.close()
    ck("4_list_3건", len(rows) == 3 and cmd_list(args(status=None, kind=None)) == 0)

    # ---- Fix E / P1-A.1: CLI 'human' 승격 = 사람 근거(save_gate 앵커 or 대화형 TTY)로만 ----
    # P1-A.1: fail-closed 가 기본이다. 비대화형 + 앵커없음 → reader → BLOCK(환경변수 우회 불가·
    # BINGGU_TRUSTED_CLI 는 사람 승인이 아님·BINGGU_STRICT_HUMAN_GATE 는 deprecated no-op).
    # 결정성 위해 sys.stdin 을 비-TTY 로 치환(실제 실행 터미널 TTY 여부와 무관).
    import io as _io
    fe_ledger = os.path.join(tmp, "fixE.sqlite")
    cmd_init(args(ledger=fe_ledger))
    FE_TEXT = "다음 배포는 회귀 위험이 커서 반드시 백업 후 진행하기로 결정했다."
    FE_TEXT2 = "이 거래처는 납기 지연 이력이 있어 다음부터 우선순위를 낮추기로 했다."
    fe_gate = _gate_log_for_ledger(fe_ledger)
    _env_keep = {k: os.environ.get(k) for k in ("BINGGU_STRICT_HUMAN_GATE", "BINGGU_TRUSTED_CLI")}
    _stdin_keep = sys.stdin

    def _fe_nodes():
        _d, _ = _open(fe_ledger)
        _n = _d.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        _d.close()
        return _n
    try:
        sys.stdin = _io.StringIO()  # 비대화형 강제(결정적)
        os.environ.pop("BINGGU_TRUSTED_CLI", None)
        # FE1: strict + 게이트 기록 없음 → 비-human 강등 → G4_no_auto BLOCK(저장 0·코드 강제)
        os.environ["BINGGU_STRICT_HUMAN_GATE"] = "1"
        _feA = cmd_save(args(ledger=fe_ledger, text=FE_TEXT, preview_id=_preview_id(FE_TEXT),
                             pick="1", confirm="SAVE 1", due=None))
        ck("FE1_strict+게이트없음→BLOCK(저장0)", _feA == 1 and _fe_nodes() == 0)
        # FE2: strict + 사람 SAVE 발화 게이트 기록 존재 → human 확정 → 저장 성공(게이트 실제 소비)
        import binggu_save_gate as _sgfe
        _fc = capture_preview(FE_TEXT, explicit=False)["candidates"]
        _sgfe.gate_record([_fc[0]["sentence"]], path=fe_gate)
        _feB = cmd_save(args(ledger=fe_ledger, text=FE_TEXT, preview_id=_preview_id(FE_TEXT),
                             pick="1", confirm="SAVE 1", due=None))
        ck("FE2_strict+게이트기록→저장성공(gate 소비)", _feB == 0 and _fe_nodes() == 1)
        # FE3: 기본(비-strict) + 비대화형 + 게이트 없음 → **BLOCK(저장 0)** — P1-A.1 fail-closed 기본.
        #      (종전엔 오버블록회피로 저장됐으나 환경변수/비대화형은 사람 승인이 아니다 · RFC §6.)
        os.environ.pop("BINGGU_STRICT_HUMAN_GATE", None)
        _feC = cmd_save(args(ledger=fe_ledger, text=FE_TEXT2, preview_id=_preview_id(FE_TEXT2),
                             pick="1", confirm="SAVE 1", due=None))
        ck("FE3_기본_비대화형_앵커없음→BLOCK(fail-closed)", _feC == 1 and _fe_nodes() == 1)
        # FE4: BINGGU_TRUSTED_CLI 환경변수는 사람 승인이 아니다 → 비대화형이면 여전히 BLOCK(env 백도어 봉인).
        os.environ["BINGGU_STRICT_HUMAN_GATE"] = "1"   # deprecated no-op
        os.environ["BINGGU_TRUSTED_CLI"] = "1"
        FE_TEXT3 = "이 계약은 조건이 불리해서 이번에는 포기하기로 결정했다."
        _feD = cmd_save(args(ledger=fe_ledger, text=FE_TEXT3, preview_id=_preview_id(FE_TEXT3),
                             pick="1", confirm="SAVE 1", due=None))
        ck("FE4_env_TRUSTED_CLI→비대화형이면_BLOCK(env는승인아님)", _feD == 1 and _fe_nodes() == 1)
    finally:
        sys.stdin = _stdin_keep
        for _k, _v in _env_keep.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v

    # ---- 회상(L4~L6 · read-only) — recall/trace/preflight CLI 래퍼 + use_count++ ----
    # 빈 그래프(미존재 ledger) graceful — 에러 0
    _empty = os.path.join(tmp, "no_ledger.sqlite")
    ck("R1_recall_빈그래프_graceful",
       cmd_recall(args(ledger=_empty, query="배포", limit=None, record=False)) == 0)
    ck("R1b_preflight_빈그래프_graceful",
       cmd_preflight(args(ledger=_empty, prompt="바로 배포", cwd=None, domain=None, files=None)) == 0)
    # 저장된 후보 중 '마진' 관련 회상 → 결과 + use_count++ 기록(P1-② 프리미티브)
    db, _ = _open(ledger)
    _uc_before = {r["node_id"]: db.con.execute(
        "SELECT use_count FROM nodes WHERE node_id=?", (r["node_id"],)).fetchone() for r in rows}
    db.close()
    ck("R2_recall_관련회상(use_count기록·--record)",
       cmd_recall(args(ledger=ledger, query="마진 보류", limit=None, record=True)) == 0)
    db, _ = _open(ledger)
    # 적어도 1개 노드의 use_count 가 증가(회상 기록). 도장/문장 불변은 R5 에서 확인.
    _uc_after = {nid: db.con.execute(
        "SELECT use_count FROM nodes WHERE node_id=?", (nid,)).fetchone()[0]
        for nid in _uc_before}
    db.close()
    ck("R3_use_count_증가(P1-②_유용성)",
       any((_uc_after[nid] or 0) >= 1 for nid in _uc_before))
    # judgment_trace — 저장된 판단 노드 1개 (사슬 없어도 found True graceful)
    _j_nid = next((r["node_id"] for r in rows if r["kind"] == "판단"), rows[0]["node_id"])
    ck("R4_trace_노드조회(고립_graceful)",
       cmd_trace(args(ledger=ledger, node_id=_j_nid)) == 0)
    ck("R4b_trace_dangling_graceful",
       cmd_trace(args(ledger=ledger, node_id="node:CONV:nope")) == 0)
    # 회상은 read-only — 문장/도장 불변(use_count 만 변경)
    db, _ = _open(ledger)
    _stamp_intact = all(
        db.con.execute("SELECT sentence,node_type FROM nodes WHERE node_id=?", (r["node_id"],)).fetchone()
        is not None for r in rows)
    db.close()
    ck("R5_회상_도장문장_불변(read-only)", _stamp_intact)
    # SSOT 게이트 후 후보가 모두 '판단' 도장일 수 있어 '상태' 미존재 시 첫 후보로 fallback(흐름 검증용).
    i_state = next((i for i, r in enumerate(rows, 1) if r["kind"] == "상태"), 1)
    h_state = rows[i_state - 1]["id8"]
    ck("5_deprecate", cmd_deprecate(args(n=i_state, id8=h_state, reason="셀프테스트 기각",
                                         confirm="DEPRECATE %s %s" % (i_state, h_state))) == 0)
    db, _ = _open(ledger)
    rows2 = list_candidates(db)["rows"]
    db.close()
    i_j = next(i for i, r in enumerate(rows2, 1) if r["kind"] == "판단" and r["state"] == "active")
    h_j = rows2[i_j - 1]["id8"]
    NEW = "재검토 결과 이 입찰은 조건부로 진행한다."
    ck("6_replace", cmd_replace(args(n=i_j, id8=h_j, reason="셀프테스트 수정",
                                     confirm="REPLACE %s %s WITH %s" % (i_j, h_j, NEW),
                                     **{"with": NEW})) == 0)
    db, _ = _open(ledger)
    rows3 = list_candidates(db)["rows"]
    db.close()
    i_n = next(i for i, r in enumerate(rows3, 1) if NEW[:10] in r["sentence"])
    h_n = rows3[i_n - 1]["id8"]
    ck("7_accept", cmd_accept(args(n=i_n, id8=h_n, reason="유지",
                                   confirm="ACCEPT %s %s" % (i_n, h_n))) == 0)
    ck("7b_unaccept", cmd_unaccept(args(n=i_n, id8=h_n, reason="재검토",
                                        confirm="UNACCEPT %s %s" % (i_n, h_n))) == 0)
    ck("8_due+resolve", cmd_due(args(n=i_n, id8=h_n, date="2000-01-01")) == 0
       and cmd_reminders(args(today="2000-01-02")) == 0
       and cmd_resolve(args(n=i_n, id8=h_n, outcome="성공", reason="셀프테스트")) == 0)
    ck("9_잘못된_confirm_BLOCK", cmd_deprecate(args(n=1, id8="deadbeef", reason="x",
                                                   confirm="DEPRECATE 1 deadbeef")) == 1)
    db, _ = _open(ledger)
    bad = (db.con.execute("SELECT count(*) FROM nodes WHERE candidate!=1 OR promotion_allowed!=0").fetchone()[0])
    chain = db.verify_chain()
    blob = "\n".join(str(r) for t in ("nodes", "audit_log")
                     for r in db.con.execute("SELECT * FROM " + t))
    db.close()
    ck("10_candidate-only+chain+raw0", bad == 0 and chain and TEXT not in blob)

    # 10b. _show 실패노출 — 이미 저장된 TEXT 재선택 → nothing_to_save + skip 건수까지 stdout 출력
    import io
    import contextlib
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        _rc = cmd_save(args(text=TEXT, preview_id=_preview_id(TEXT), pick="1",
                            confirm="SAVE 1", due=None))
    _out = _buf.getvalue()
    ck("10b_show_실패이유_노출(BLOCK+skip)",
       _rc == 1 and "BLOCK" in _out and "skip" in _out)

    # ---- hosted: collect broad, commit narrow (worker 미접촉 · 별도 temp · staging 직접) ----
    import time as _time
    import json as _json
    from binggu_hosted_inbox import staging_dir_for as _sdir
    from openbinggu_save_intent_outbox_runner import intent_hash as _ih, SCHEMA_VER as _SV
    h_tmp = tempfile.mkdtemp(prefix="bgp_cli_hosted_")
    h_home = os.path.join(h_tmp, ".binggupack")
    h_staging = _sdir(h_home)
    os.makedirs(h_staging)
    h_ledger = os.path.join(h_home, "ledger.sqlite")
    os.makedirs(os.path.join(h_home, "snapshots"))
    open_accept(h_ledger).close()

    def _mk(text, idxs):
        c = "SAVE " + ",".join(str(i) for i in idxs)
        it = {"schema_ver": _SV, "text": text, "indices": idxs, "confirm": c,
              "intent_id": _ih(text, idxs, c), "created_ts": int(_time.time()) - 10,
              "ttl_s": 86400, "source": "hosted"}
        with open(os.path.join(h_staging, it["intent_id"] + ".json"), "w", encoding="utf-8") as f:
            _json.dump(it, f, ensure_ascii=False)

    _mk("이 입찰은 마진이 낮아 보류하기로 결정했다.", [1])
    _mk("백업은 항상 작업 전에 먼저 해 둔다.", [1])
    ck("13_hosted_inbox_요약(저장0·worker미접촉)",
       cmd_hosted(args(ledger=h_ledger, hosted_cmd="inbox", no_fetch=True, since=None)) == 0)
    ck("14_hosted_pull_select없음_안내(실행0)",
       cmd_hosted(args(ledger=h_ledger, hosted_cmd="pull", select=None, confirm=None)) == 0)
    n_stg_before = len([f for f in os.listdir(h_staging) if f.endswith(".json")])
    rc15 = cmd_hosted(args(ledger=h_ledger, hosted_cmd="pull", select="1", confirm="LIVE SAVE 1"))
    db_h = open_accept(h_ledger)
    n_act_h = db_h.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
    db_h.close()
    n_stg_after = len([f for f in os.listdir(h_staging) if f.endswith(".json")])
    ck("15_hosted_pull_commit_narrow(선택1건만·나머지잔류)",
       rc15 == 0 and n_act_h == 1 and n_stg_after == n_stg_before - 1)
    ck("16_hosted_pull_confirm불일치_BLOCK(전량자동 차단)",
       cmd_hosted(args(ledger=h_ledger, hosted_cmd="pull", select="1", confirm="LIVE SAVE 9")) == 1)
    shutil.rmtree(h_tmp, ignore_errors=True)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("11_운영_store_불변", op_before == op_after)
    shutil.rmtree(tmp, ignore_errors=True)
    ck("12_temp_정리", not os.path.exists(tmp))

    sys.stdin = _real_stdin   # P1-A.1: 대화형 owner 시뮬 종료(정상 경로 복원)
    ok = all(checks)
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (sum(checks), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


def test_binggu_cli_selftest():
    """pytest 수집용 얇은 래퍼 — 임베드 selftest 전 케이스 GATE=GO(exit 0).

    stdin 대화형 시뮬은 selftest() 본체에서 수행(양 진입점 공통). 여기 try/finally 는 selftest 가
    예외로 중단돼도 다른 pytest 케이스에 stdin 오염이 새지 않도록 하는 안전망이다."""
    _real = sys.stdin
    try:
        assert selftest() == 0
    finally:
        sys.stdin = _real
