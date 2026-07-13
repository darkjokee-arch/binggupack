#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""binggu CLI 임베드 selftest 분리본 (God-file #4 분리 → wheel 자족성 이동).

기존 binggu.py 안에 있던 임베드 selftest 를 옮긴 것 — binggu.py --selftest 는 이 모듈의
selftest() 를 호출한다. pytest 는 tests/test_binggu_cli_selftest.py(thin shim)로 이 게이트를
수집한다(GATE=GO → exit 0).

wheel 자족성(2026-07-13): 구현부가 tests/ 에 있으면 wheel 에 tests/ 미포함이라 설치본
`binggu --selftest` 가 ModuleNotFoundError — binggupack.cli 로 이동해 wheel 에 동봉한다.
scripts/ 형제 bare import 는 try bare / except → `from scripts import ...` 폴백(wheel 에서
scripts 는 top-level 패키지).

save-n 참조 바인딩 개정(스펙 ①~④) 반영: 사람 증명 = "preview + 사람의 save n 입력" 단일 원칙.
  · 터미널 시뮬 = CLAUDECODE env pop(스펙 ② — 명령 직접 입력이 곧 save n · isatty 시뮬 폐기)
  · 에이전트 세션 시뮬 = CLAUDECODE=1 주입(훅 ref 앵커만 human · FE 블록)
  · 결정성: CLAUDECODE/BINGGU_TRUSTED_CLI/BINGGU_STRICT_HUMAN_GATE 명시 set/pop + BINGGU_HOME temp 격리
"""
import os
import shutil
import sys
import tempfile

# repo root(= binggu.py 위치)를 path 에 올려 binggu 를 import 가능하게 한다.
# repo: <root>/binggupack/cli/ 에서 3단계 상위 = <root>. wheel: 3단계 상위 = site-packages(이미 path).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

_ENV_KEYS = ("CLAUDECODE", "BINGGU_HOME", "BINGGU_TRUSTED_CLI", "BINGGU_STRICT_HUMAN_GATE")


def _restore_env(keep):
    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def selftest():
    # 결정성: 전 케이스가 env 를 명시 제어(Claude Code 세션의 CLAUDECODE=1 상속 · CI 부재 이중 결과 차단).
    # 기본 = 터미널 시뮬(CLAUDECODE pop → 스펙 ② cli_command human). FE/hosted 블록이 국소적으로
    # CLAUDECODE=1(에이전트 세션)을 주입·복원한다.
    keep = {k: os.environ.get(k) for k in _ENV_KEYS}
    os.environ.pop("CLAUDECODE", None)
    os.environ.pop("BINGGU_TRUSTED_CLI", None)
    os.environ.pop("BINGGU_STRICT_HUMAN_GATE", None)
    try:
        return _selftest_body()
    finally:
        _restore_env(keep)


def _selftest_body():
    print("=" * 74)
    print("binggu CLI — temp 장부 풀 사이클 selftest (영속 장부·운영 store 접근 0)")
    print("=" * 74)
    op_before = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    tmp = tempfile.mkdtemp(prefix="bgp_cli_")
    # last_preview/save_gate_log 격리 — preview/hosted inbox 가 gate_home() 에 쓰므로 운영 홈 미접촉.
    os.environ["BINGGU_HOME"] = tmp
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
    ck("1c_capture_init", cmd_init(args(with_capture=True, capture_settings=cap_settings,
                                        capture_cwd=cap_cwd)) == 0)
    _cst = cap_status(cap_home, cap_cwd, cap_settings)
    ck("1d_capture_ON+hook+scope", _cst["enabled"] and _cst["hook_registered"]
       and _cst["in_current_scope"] and not _cst["global"])
    # --agi-memory = 전역(AGI memory mode) — 임의 cwd 도 수집 대상
    ck("1d2_agi_memory→전역",
       cmd_init(args(with_capture=True, capture_settings=cap_settings, capture_cwd=cap_cwd, agi_memory=True)) == 0
       and cap_status(cap_home, "D:/anywhere/else", cap_settings)["global"]
       and cap_status(cap_home, "D:/anywhere/else", cap_settings)["in_current_scope"])
    ck("1e_pause→OFF", cmd_capture(args(capture_cmd="pause", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and not cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    ck("1f_resume→ON", cmd_capture(args(capture_cmd="resume", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    ck("1f2_disable→sticky OFF", cmd_capture(args(capture_cmd="disable", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and not cap_status(cap_home, cap_cwd, cap_settings)["enabled"]
       and cap_status(cap_home, cap_cwd, cap_settings)["disabled"])
    ck("1f3_재init중_sticky OFF 유지", cmd_init(args(with_capture=True, capture_settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and not cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    ck("1f4_enable→ON 복구", cmd_capture(args(capture_cmd="enable", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    ck("1g_preview(저장0)", cmd_capture(args(capture_cmd="preview", settings=cap_settings, capture_cwd=cap_cwd)) == 0)
    ck("1h_uninstall", cmd_capture(args(capture_cmd="uninstall", settings=cap_settings, capture_cwd=cap_cwd)) == 0
       and not cap_status(cap_home, cap_cwd, cap_settings)["enabled"])
    # ── T3(start 부작용 분리): 기본 start = 장부만(settings.json hook 미접촉) · capture install = 명시 옵트인 등록 ──
    si_home = os.path.join(tmp, "startinit")
    si_ledger = os.path.join(si_home, "ledger.sqlite")
    si_settings = os.path.join(si_home, "settings.json")
    si_cwd = os.path.realpath(si_home)
    os.makedirs(si_home, exist_ok=True)
    # 기본 start(with_capture 미지정) → 장부 생성만 · settings.json 자체가 안 생김(hook 미등록)
    _rc_si = cmd_init(args(ledger=si_ledger, capture_settings=si_settings, capture_cwd=si_cwd))
    _hook_after_start = cap_status(si_home, si_cwd, si_settings)["hook_registered"]
    ck("T3a_start기본_장부만_hook미등록",
       _rc_si == 0 and os.path.exists(si_ledger)
       and not os.path.exists(si_settings) and not _hook_after_start)
    # capture install → hook 등록 + scope 생성(owner sticky OFF 아니므로 ON)
    _rc_ci = cmd_capture(args(ledger=si_ledger, capture_cmd="install",
                              settings=si_settings, capture_cwd=si_cwd))
    _cst_ci = cap_status(si_home, si_cwd, si_settings)
    ck("T3b_capture_install_hook등록+ON",
       _rc_ci == 0 and _cst_ci["enabled"] and _cst_ci["hook_registered"])
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
    # 스펙 ②: CLAUDECODE 부재(터미널 시뮬) → 명령 직접 입력이 곧 save n(cli_command human) →
    #         confirm 정확일치로 저장 성공(별도 앵커 seed 불요 — 훅 앵커 경로는 FE2 가 검증).
    ck("3_save", cmd_save(args(text=TEXT, preview_id=_preview_id(TEXT),
                               pick="1,2,3", confirm="SAVE 1,2,3",
                               due="2099-12-31")) == 0)
    db, _ = _open(ledger)
    rows = list_candidates(db)["rows"]
    db.close()
    ck("4_list_3건", len(rows) == 3 and cmd_list(args(status=None, kind=None)) == 0)

    # ---- Fix E / save-n 참조 바인딩: 'human' 승격 = 훅 ref 앵커 or 터미널(cli_command) 만 ----
    # 에이전트 세션(CLAUDECODE=1) 내부에서는 훅이 기록한 (preview_ref, idx) 만 사람 증명 —
    # confirm 문구 복제·환경변수는 승격 불가(deny 전용 가드 · fail-closed).
    fe_ledger = os.path.join(tmp, "fixE.sqlite")
    cmd_init(args(ledger=fe_ledger))
    FE_TEXT = "다음 배포는 회귀 위험이 커서 반드시 백업 후 진행하기로 결정했다."
    FE_TEXT2 = "이 거래처는 납기 지연 이력이 있어 다음부터 우선순위를 낮추기로 했다."
    _env_keep = {k: os.environ.get(k) for k in ("CLAUDECODE", "BINGGU_TRUSTED_CLI",
                                                "BINGGU_STRICT_HUMAN_GATE")}

    def _fe_nodes():
        _d, _ = _open(fe_ledger)
        _n = _d.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        _d.close()
        return _n
    try:
        os.environ.pop("BINGGU_TRUSTED_CLI", None)
        os.environ.pop("BINGGU_STRICT_HUMAN_GATE", None)
        os.environ["CLAUDECODE"] = "1"   # 에이전트 세션 시뮬(결정적)
        # FE1: 에이전트 세션 + 훅 앵커 없음 → reader 강등 → G4_no_auto BLOCK(저장 0·코드 강제)
        _feA = cmd_save(args(ledger=fe_ledger, text=FE_TEXT, preview_id=_preview_id(FE_TEXT),
                             pick="1", confirm="SAVE 1", due=None))
        ck("FE1_CLAUDECODE=1+앵커없음→BLOCK(저장0)", _feA == 1 and _fe_nodes() == 0)
        # FE2: 에이전트 세션 + 사람 '세이브 1' 훅 ref 앵커 → save_gate_ref 승격 → 저장 성공
        try:
            import binggu_save_gate as _sgfe
        except ImportError:  # wheel 설치본 — scripts 는 top-level 패키지
            from scripts import binggu_save_gate as _sgfe
        _fc = capture_preview(FE_TEXT, explicit=False)["candidates"]
        _sgfe.write_last_preview(_fc)                       # BINGGU_HOME=tmp 격리 preview
        _sgfe.gate_record_from_prompt("세이브 1")           # 훅 기록 시뮬(ref+sh 이중기록)
        _feB = cmd_save(args(ledger=fe_ledger, text=FE_TEXT, preview_id=_preview_id(FE_TEXT),
                             pick="1", confirm="SAVE 1", due=None))
        ck("FE2_CLAUDECODE=1+ref앵커→저장성공(save_gate_ref)", _feB == 0 and _fe_nodes() == 1)
        # FE3: 에이전트 세션 + 새 텍스트(앵커 없음) → 여전히 BLOCK — fail-closed 기본.
        _feC = cmd_save(args(ledger=fe_ledger, text=FE_TEXT2, preview_id=_preview_id(FE_TEXT2),
                             pick="1", confirm="SAVE 1", due=None))
        ck("FE3_CLAUDECODE=1_앵커없음→BLOCK(fail-closed)", _feC == 1 and _fe_nodes() == 1)
        # FE4: BINGGU_TRUSTED_CLI/STRICT env 는 사람 승인이 아니다 → 에이전트 세션이면 여전히 BLOCK.
        os.environ["BINGGU_STRICT_HUMAN_GATE"] = "1"   # deprecated no-op
        os.environ["BINGGU_TRUSTED_CLI"] = "1"
        FE_TEXT3 = "이 계약은 조건이 불리해서 이번에는 포기하기로 결정했다."
        _feD = cmd_save(args(ledger=fe_ledger, text=FE_TEXT3, preview_id=_preview_id(FE_TEXT3),
                             pick="1", confirm="SAVE 1", due=None))
        ck("FE4_env_TRUSTED_CLI→에이전트세션이면_BLOCK(env는승인아님)", _feD == 1 and _fe_nodes() == 1)
        # FE5: CLAUDECODE 부재(터미널) → 명령 직접 입력 = save n(cli_command) → 저장 성공(스펙 ②).
        os.environ.pop("CLAUDECODE", None)
        os.environ.pop("BINGGU_TRUSTED_CLI", None)
        os.environ.pop("BINGGU_STRICT_HUMAN_GATE", None)
        _feE = cmd_save(args(ledger=fe_ledger, text=FE_TEXT2, preview_id=_preview_id(FE_TEXT2),
                             pick="1", confirm="SAVE 1", due=None))
        ck("FE5_CLAUDECODE부재→cli_command_human_저장성공", _feE == 0 and _fe_nodes() == 2)
    finally:
        _restore_env(_env_keep)
        os.environ.pop("CLAUDECODE", None)   # 본체 기본 = 터미널 시뮬 유지

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
    # index-op(deprecate/replace/accept/…)는 터미널 시뮬(CLAUDECODE 부재) = cli_command human(스펙 ②).
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

    # ---- hosted: collect broad, commit narrow — ★save-n 참조 바인딩(preview + save n · approval 배선 0) ----
    import time as _time
    import json as _json
    try:
        from binggu_hosted_inbox import staging_dir_for as _sdir, inbox_preview_candidates as _ipc
        from openbinggu_save_intent_outbox_runner import intent_hash as _ih, SCHEMA_VER as _SV
        import binggu_save_gate as _sgh
    except ImportError:  # wheel 설치본 — scripts 는 top-level 패키지
        from scripts.binggu_hosted_inbox import staging_dir_for as _sdir, inbox_preview_candidates as _ipc
        from scripts.openbinggu_save_intent_outbox_runner import intent_hash as _ih, SCHEMA_VER as _SV
        from scripts import binggu_save_gate as _sgh
    h_tmp = tempfile.mkdtemp(prefix="bgp_cli_hosted_")
    h_home = os.path.join(h_tmp, ".binggupack")
    h_staging = _sdir(h_home)
    os.makedirs(h_staging)
    h_ledger = os.path.join(h_home, "ledger.sqlite")
    os.makedirs(os.path.join(h_home, "snapshots"))
    open_accept(h_ledger).close()

    def _h_apr():
        """approval_requests 무증가(테이블 부재=0) — 저장 경로 approval 배선 제거 증명."""
        _d = open_accept(h_ledger)
        try:
            return _d.con.execute("SELECT count(*) FROM approval_requests").fetchone()[0]
        except Exception:
            return 0
        finally:
            _d.close()

    def _mk(text, idxs):
        c = "SAVE " + ",".join(str(i) for i in idxs)
        it = {"schema_ver": _SV, "text": text, "indices": idxs, "confirm": c,
              "intent_id": _ih(text, idxs, c), "created_ts": int(_time.time()) - 10,
              "ttl_s": 86400, "source": "hosted"}
        with open(os.path.join(h_staging, it["intent_id"] + ".json"), "w", encoding="utf-8") as f:
            _json.dump(it, f, ensure_ascii=False)

    def _h_active():
        _d = open_accept(h_ledger)
        try:
            return _d.con.execute("SELECT count(*) FROM nodes WHERE state='active'").fetchone()[0]
        finally:
            _d.close()

    _mk("이 입찰은 마진이 낮아 보류하기로 결정했다.", [1])
    _mk("백업은 항상 작업 전에 먼저 해 둔다.", [1])
    ck("13_hosted_inbox_요약(저장0·worker미접촉·preview 기록)",
       cmd_hosted(args(ledger=h_ledger, hosted_cmd="inbox", no_fetch=True, since=None)) == 0)
    ck("14_hosted_pull_select없음_안내(실행0)",
       cmd_hosted(args(ledger=h_ledger, hosted_cmd="pull", select=None, confirm=None)) == 0)
    _h_env = {k: os.environ.get(k) for k in ("CLAUDECODE",)}
    try:
        # ① 에이전트 세션(CLAUDECODE=1) + 훅 앵커 없음 → human_save_required · write 0 · 원문 보존
        os.environ["CLAUDECODE"] = "1"
        n_stg_before = len([f for f in os.listdir(h_staging) if f.endswith(".json")])
        rc15 = cmd_hosted(args(ledger=h_ledger, hosted_cmd="pull", select="1", confirm="SAVE 1"))
        n_stg_15 = len([f for f in os.listdir(h_staging) if f.endswith(".json")])
        ck("15_hosted_pull_에이전트세션_무앵커→BLOCK(write 0·원문 보존·approval 0)",
           rc15 == 1 and _h_active() == 0 and n_stg_15 == n_stg_before and _h_apr() == 0)
        # ② 사람 '세이브 1' 발화(훅 시뮬 — inbox preview 의 ref 로 기록) → save_gate_ref 승격 저장
        _h_pref = _sgh.preview_ref_for_candidates(_ipc(h_staging, int(_time.time())))
        _sgh.gate_record_ref(_h_pref, [1],
                             path=_gate_log_for_ledger(h_ledger))
        rc15b = cmd_hosted(args(ledger=h_ledger, hosted_cmd="pull", select="1", confirm="SAVE 1"))
        n_stg_after = len([f for f in os.listdir(h_staging) if f.endswith(".json")])
        ck("15b_hosted_pull_훅앵커→atomic저장(선택1건·commit narrow·나머지잔류)",
           rc15b == 0 and _h_active() == 1 and n_stg_after == n_stg_before - 1 and _h_apr() == 0)
        # ③ 에이전트 세션 + 새 선택(앵커 없음 — staged 집합 변경으로 pref 도 변경) → 여전히 BLOCK
        n_act_pre16 = _h_active()
        rc16 = cmd_hosted(args(ledger=h_ledger, hosted_cmd="pull", select="1", confirm="SAVE 1"))
        ck("16_hosted_pull_에이전트세션_새선택_무앵커_BLOCK(write 0)",
           rc16 == 1 and _h_active() == n_act_pre16)
        # ④ 터미널(CLAUDECODE 부재) = 명령 직접 입력이 곧 save n → 저장 성공(스펙 ②)
        os.environ.pop("CLAUDECODE", None)
        rc16b = cmd_hosted(args(ledger=h_ledger, hosted_cmd="pull", select="1", confirm="SAVE 1"))
        ck("16b_hosted_pull_터미널_cli_command→저장성공",
           rc16b == 0 and _h_active() == n_act_pre16 + 1 and _h_apr() == 0)
    finally:
        _restore_env(_h_env)
        os.environ.pop("CLAUDECODE", None)
    shutil.rmtree(h_tmp, ignore_errors=True)

    op_after = {p: (os.path.getmtime(p) if os.path.exists(p) else None) for p in OPERATING_PATHS}
    ck("11_운영_store_불변", op_before == op_after)
    shutil.rmtree(tmp, ignore_errors=True)
    ck("12_temp_정리", not os.path.exists(tmp))

    ok = all(checks)
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (sum(checks), len(checks)))
    print("GATE:", "GO" if ok else "NO-GO")
    return 0 if ok else 1


def test_binggu_cli_selftest():
    """pytest 수집용 얇은 래퍼 — 임베드 selftest 전 케이스 GATE=GO(exit 0).

    tests/test_binggu_cli_selftest.py(thin shim)가 star import 로 이 함수를 노출해 수집한다.
    env 명시 제어는 selftest() 본체에서 수행(양 진입점 공통). 여기 try/finally 는 selftest 가
    예외로 중단돼도 다른 pytest 케이스에 env 오염이 새지 않도록 하는 안전망이다."""
    _keep = {k: os.environ.get(k) for k in _ENV_KEYS}
    try:
        assert selftest() == 0
    finally:
        _restore_env(_keep)
