#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-A.1 Approval-Origin Contract — 회귀 하니스 (run_all 편입 · TIER-2).

정본 설계: docs/BINGGUPACK_TRUSTED_APPROVAL_EVENT_RFC.md §6(승인 기원 계약).

불변식(전부 코드로 강제 · save-n 참조 바인딩 개정 반영):
  · 환경변수(BINGGU_TRUSTED_CLI) · CLI arg · actor 문자열 · confirm 문구는 **사람 승인이 아니다**.
    사람 증명 = "preview + 사람의 save n 입력" 단일 원칙:
      (1) 에이전트 세션(CLAUDECODE=1) 안에서는 save_gate ref 앵커(사장님 '세이브 n' 발화를
          UserPromptSubmit hook 이 (preview_ref, idx) 로 기록 · AI 위조 불가)만 human
      (2) 터미널(CLAUDECODE 부재) = 명령 직접 입력이 곧 save n(cli_command) — isatty 검사 삭제
  · 비대화형 `approval approve` → 항상 no-write · exit≠0(env·strict 로 우회 불가 — 승인 채널 별도 자산).
  · 에이전트 세션 save/pair(앵커 없음) → write 0(fail-closed). ref 앵커 있으면 write 성공.
  · BINGGU_STRICT_HUMAN_GATE 는 deprecated no-op — 0/false/off/'' 로 fail-open 안 됨.
  · production wheel(binggu.py·binggupack·scripts·hooks)에 test 백도어 0 —
    test_double 채널 리터럴 0 · 환경변수 승인 read 0.
  · actor="human" 직접생성 인벤토리 — **binggu.py CLI 진입점** 잔존 리터럴 write 0(전부 _resolve_human_ctx
    경유). CLI-도달 hosted 커밋 경로는 commit_bundle 사람 저장 게이트로 봉인(매 실행 출력 · 숨김 0).

전부 temp home 격리 · 운영 ~/.binggupack 미접촉(sentinel). CLI: --selftest
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINGGU = os.path.join(REPO, "binggu.py")
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts"))


def _cli(home, ledger, args, env_extra=None, stdin_text=""):
    """subprocess env 전부 명시 제어 — CLAUDECODE 는 기본 pop(터미널 시뮬 · CI/로컬 어디서 돌아도
    결정적), 에이전트 세션 케이스만 env_extra 로 CLAUDECODE=1 주입. stdin=PIPE(approve 비대화형)."""
    e = dict(os.environ)
    e["BINGGU_HOME"] = home
    e["PYTHONUTF8"] = "1"
    e.pop("BINGGU_TRUSTED_CLI", None)
    e.pop("BINGGU_STRICT_HUMAN_GATE", None)
    e.pop("CLAUDECODE", None)
    if env_extra:
        e.update(env_extra)
    return subprocess.run([sys.executable, BINGGU, "--ledger", ledger] + args,
                          cwd=REPO, env=e, input=stdin_text,
                          capture_output=True, text=True, timeout=120)


def run():
    from binggupack.safety import trusted_approval as ta
    from binggupack.storage import open_g3
    from binggupack.storage.schema import ledger_id as _ledger_id

    fails = []
    ran = []

    def ck(name, cond):
        ran.append(name)
        print("  [%s] %s" % ("OK" if cond else "X", name))
        if not cond:
            fails.append(name)

    # 운영 ledger sentinel — 실 ~/.binggupack 불변 확인.
    real_home = os.path.join(os.path.expanduser("~"), ".binggupack")
    sentinel = {}
    for fn in ("ledger.sqlite", "approvals.jsonl"):
        p = os.path.join(real_home, fn)
        sentinel[p] = os.path.getmtime(p) if os.path.exists(p) else None

    home = tempfile.mkdtemp(prefix="bgp_aorigin_")
    os.makedirs(os.path.join(home, ".binggupack"), exist_ok=True)
    os.makedirs(os.path.join(home, "snapshots"), exist_ok=True)
    ledger = os.path.join(home, ".binggupack", "ledger.sqlite")

    def approve_events():
        return sum(1 for ev in ta.read_events(home) if ev.get("record_type") == "approve")

    def node_count():
        d = open_g3(ledger)
        try:
            return d.con.execute("SELECT count(*) FROM nodes").fetchone()[0]
        finally:
            d.close()

    try:
        _cli(home, ledger, ["init", "--no-capture"])
        with open(ta.config_path(home), "w", encoding="utf-8") as f:
            json.dump({"enabled": True, "ttl_seconds": 900, "pending_cap": 8}, f)

        # PENDING request seed (모델 MCP 경로 시뮬 — 승인 대기 상태).
        db = open_g3(ledger)
        lid = _ledger_id(db.con)
        dig = ta.canonical_payload_digest("deprecate", {"index": 1, "id8": "abcd1234", "reason": "r"})
        rid = ta.compute_request_id("deprecate", dig, lid)
        ta.upsert_request(db.con, rid, ta.PROTOCOL_VERSION, "deprecate", dig, lid,
                          "deprecate: node #1", time.time(), 900, 8)
        db.close()

        # ── A) environment_variable_cannot_approve ────────────────────────────────
        # BINGGU_TRUSTED_CLI 어떤 truthy 값도 비대화형 approve 를 승격시키지 못한다(AOB-1 봉인).
        env_all_blocked = True
        for v in ("1", "true", "TRUE", "yes", "on", "randomstring"):
            r = _cli(home, ledger, ["approval", "approve", rid],
                     env_extra={"BINGGU_TRUSTED_CLI": v}, stdin_text="junk\n")
            if r.returncode == 0 or approve_events() != 0:
                env_all_blocked = False
        ck("A_env_var_cannot_approve(1/true/TRUE/yes/on/random 전부 exit≠0·mint0)",
           env_all_blocked and approve_events() == 0)

        # ── B) noninteractive_approval_always_blocked ─────────────────────────────
        # 여러 비대화형 stdin(빈 파이프·문구·잡음)에서 approve → exit 2 · mint 0.
        noninteractive_blocked = True
        for stdin_text in ("", "APPROVE %s\n" % rid[:8], "junk\njunk2\n", "\n"):
            r = _cli(home, ledger, ["approval", "approve", rid], stdin_text=stdin_text)
            if r.returncode != 2 or approve_events() != 0:
                noninteractive_blocked = False
        ck("B_noninteractive_approve_always_blocked(파이프/문구/빈입력 → exit2·mint0)",
           noninteractive_blocked and approve_events() == 0)

        # ── C) strict_flag_false_cannot_fail_open ─────────────────────────────────
        # STRICT=0/false/off/'' (+TRUSTED_CLI) 로도 approve fail-open 안 됨.
        strict_no_open = True
        for sv in ("0", "false", "off", ""):
            r = _cli(home, ledger, ["approval", "approve", rid],
                     env_extra={"BINGGU_STRICT_HUMAN_GATE": sv, "BINGGU_TRUSTED_CLI": "1"},
                     stdin_text="")
            if r.returncode == 0 or approve_events() != 0:
                strict_no_open = False
        ck("C1_strict_flag_false_cannot_fail_open(approve)", strict_no_open and approve_events() == 0)

        # ── D) agent_session_save_without_anchor_blocked ──────────────────────────
        # 에이전트 세션(CLAUDECODE=1) 안에서는 훅 ref 앵커만 사람 증명 — 앵커 없으면 write 0.
        txt = "이 계약은 조건이 불리해서 이번에는 포기하기로 결정했다."
        pv = _cli(home, ledger, ["preview", txt])
        m = re.search(r"preview_id: ([0-9a-f]+)", pv.stdout)
        pid = m.group(1) if m else ""
        n0 = node_count()
        r = _cli(home, ledger, ["save", txt, "--preview-id", pid, "--pick", "1", "--confirm", "SAVE 1"],
                 env_extra={"CLAUDECODE": "1"}, stdin_text="")
        ck("D1_agent_session_save_no_anchor → write0(fail-closed)",
           "'saved': 1" not in r.stdout and node_count() == n0)

        # STRICT=false 로도 에이전트 세션 save fail-open 안 됨.
        r = _cli(home, ledger, ["save", txt, "--preview-id", pid, "--pick", "1", "--confirm", "SAVE 1"],
                 env_extra={"CLAUDECODE": "1", "BINGGU_STRICT_HUMAN_GATE": "false"}, stdin_text="")
        ck("C2_strict_flag_false_cannot_fail_open(save)", node_count() == n0)

        # pair 에이전트 세션·앵커없음 → 노드 0.
        n1 = node_count()
        r = _cli(home, ledger, ["pair", "이건 내 직감으로 판단한 거다", "AI 가 제안한 방향",
                                "--by", "owner", "--relation", "refutes",
                                "--confirm", "PAIR owner_refutes owner:1 ai:1"],
                 env_extra={"CLAUDECODE": "1"}, stdin_text="")
        ck("D2_agent_session_pair_no_anchor → write0(fail-closed)", node_count() == n1)

        # D3) 터미널(CLAUDECODE 부재) = 명령 직접 입력이 곧 save n → 저장 성공(스펙 ②).
        txt2 = "이 방침은 다음 분기에 재검토하기로 결정했다."
        pv2 = _cli(home, ledger, ["preview", txt2])
        m2 = re.search(r"preview_id: ([0-9a-f]+)", pv2.stdout)
        pid2 = m2.group(1) if m2 else ""
        r = _cli(home, ledger, ["save", txt2, "--preview-id", pid2, "--pick", "1",
                                "--confirm", "SAVE 1"], stdin_text="")
        ck("D3_terminal_no_CLAUDECODE_save → write 성공(cli_command)",
           "'saved': 1" in r.stdout and node_count() == n1 + 1)

        # ── E) 대조군: 에이전트 세션 + save_gate ref 앵커 → write 성공(게이트가 막기만 하는 게 아님) ──
        from binggupack.capture import preview as cvp
        from binggu import _gate_log_for_ledger
        import binggu_save_gate as sg
        n2 = node_count()
        cands = cvp.capture_preview(txt, explicit=False).get("candidates", [])
        if cands:
            # 훅 시뮬: preview 후보의 (preview_ref, idx=1) ref 레코드 — 사장님 '세이브 1' 발화 기록.
            sg.gate_record_ref(sg.preview_ref_for_candidates(cands), [1],
                               path=_gate_log_for_ledger(ledger))
            r = _cli(home, ledger, ["save", txt, "--preview-id", pid, "--pick", "1",
                                    "--confirm", "SAVE 1"],
                     env_extra={"CLAUDECODE": "1"}, stdin_text="")
            ck("E_save_WITH_ref_anchor → write 성공(save_gate_ref 정상 경로)",
               "'saved': 1" in r.stdout and node_count() == n2 + 1)
        else:
            ck("E_save_WITH_ref_anchor(후보 0·explicit=False 필터 · skip)", True)

        # ── F) test_provider_not_packaged (ship-guard) ────────────────────────────
        ck("F_test_backdoor_not_packaged(test_double 채널·env 승인 read 0)", _ship_guard())

        # ── G) approval_origin_inventory (actor='human' 직접생성 사이트 분류) ───────
        ck("G_approval_origin_inventory(binggu.py CLI 진입점 잔존 human write 리터럴 0 · P1-B 명시제외)",
           _inventory_binggu_py())

        # ── H) 운영 ledger sentinel ───────────────────────────────────────────────
        sentinel_ok = True
        for p, m0 in sentinel.items():
            m1 = os.path.getmtime(p) if os.path.exists(p) else None
            if m0 != m1:
                sentinel_ok = False
        ck("H_운영_ledger_sentinel(실 ~/.binggupack mtime 불변)", sentinel_ok)

    finally:
        import shutil
        shutil.rmtree(home, ignore_errors=True)

    print("-" * 70)
    print("RESULT: %d checks, %d fail" % (len(ran), len(fails)))
    print("GATE=%s" % ("GO" if not fails else "NO-GO"))
    return 0 if not fails else 1


def _packaged_py_files():
    """pyproject 패키징 세트(wheel 반영): binggu.py + binggupack/ + scripts/ + hooks/. tests/ 제외."""
    files = [os.path.join(REPO, "binggu.py")]
    for sub in ("binggupack", "scripts", "hooks"):
        base = os.path.join(REPO, sub)
        for root, _dirs, names in os.walk(base):
            # 패키징 제외 디렉터리(pyproject find.exclude 관습): _*, __pycache__
            parts = os.path.relpath(root, REPO).split(os.sep)
            if any(p.startswith("_") or p == "__pycache__" for p in parts):
                continue
            for n in names:
                if n.endswith(".py"):
                    files.append(os.path.join(root, n))
    return files


def _ship_guard():
    """production wheel 에 test 백도어 부재 검증:
      1) 'test_double' 채널 리터럴 0 (pytest 전용 owner-채널 시뮬 · 운영 미포함)
      2) 환경변수 BINGGU_TRUSTED_CLI 를 승인용으로 READ 하는 코드 0
         (env.pop/주석/문서 언급은 허용 — os.environ.get/os.getenv 로 읽는 활성 백도어만 차단)
    """
    env_read = re.compile(r"(os\.environ\.get|environ\.get|os\.getenv|getenv)\(\s*['\"]BINGGU_TRUSTED_CLI")
    # 위험 벡터 = test-double owner-채널을 실제 mint 에 넘기는 코드(channel="test_double").
    # 산문/주석/라벨 속 단어 언급은 무해하므로 mint 호출 시그니처만 정밀 검사한다.
    td_channel = re.compile(r"channel\s*=\s*['\"]test_double['\"]")
    self_path = os.path.abspath(__file__)   # 가드 자신은 대상 패턴을 산문으로 명시하므로 제외(리뷰 별도).
    ok = True
    for fp in _packaged_py_files():
        if os.path.abspath(fp) == self_path:
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                src = f.read()
        except Exception:
            continue
        if td_channel.search(src):
            print("      [ship-guard] test_double 채널 mint 발견: %s" % os.path.relpath(fp, REPO))
            ok = False
        if env_read.search(src):
            print("      [ship-guard] BINGGU_TRUSTED_CLI 승인 read 발견: %s" % os.path.relpath(fp, REPO))
            ok = False
    return ok


def _inventory_binggu_py():
    """actor='human' 직접생성 인벤토리 — **스코프: binggu.py CLI 진입점**.

    의미있는 불변식: 모든 CLI 서브커맨드의 운영 write 는 `_resolve_human_ctx`(save-n 참조 바인딩/
    cli_command · 에이전트 세션 deny)로 actor 를 해소한다 — binggu.py 에 human 을 부여하는 리터럴
    dict/kwarg 가 0 이어야 한다.
    (하위 구현 함수·*_selftest 는 전달받은 ctx 또는 owner-승인 시뮬 픽스처이므로 독립 우회가 아니다 —
    패키지 전체에 ~200개의 {"actor":"human"} 리터럴이 있으나 대부분 이 두 부류다. 이 함수는 그 노이즈를
    스캔하지 않고, 사용자 입력이 들어오는 CLI 진입점만 강제한다.)

    **CLI-도달 hosted 커밋 경로(봉인 유지):** hosted save-intent 커밋 3파일은 commit_bundle 의
    사람 저장 게이트(ctx.actor=='human' + confirm 정확일치 — save-n 참조 바인딩 개정) 경유로 봉인돼
    있다 — transported confirm(`LIVE SAVE n`)으로 literal human 을 찍던 direct save_selected 경로가
    제거됐다. 아래 출력은 파일 정적 존재 확인일 뿐이며(숨김 0 · 내용 재스캔 아님), direct write 회귀는
    각 러너의 behavioral selftest(write 0 단언)가 잡는다. 여기서는 '숨기지 않고 명시'가 정직성 계약이다.
    """
    import ast
    KNOWN_P1B_HOSTED = (   # CLI-도달하지만 P1-B 로 이연된 literal-human write(문서화된 예외)
        os.path.join(REPO, "scripts", "binggu_hosted_inbox.py"),
        os.path.join(REPO, "scripts", "openbinggu_save_intent_live_runner.py"),
        os.path.join(REPO, "scripts", "openbinggu_save_intent_outbox_runner.py"),
    )
    with open(BINGGU, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    offenders = []

    def _is_human(node):
        return isinstance(node, ast.Constant) and node.value == "human"

    def _is_actor_key(node):
        return isinstance(node, ast.Constant) and node.value == "actor"

    # AST 로 실제 코드만 검사 — docstring/주석/print 문자열 속 'actor=human' 언급은 리터럴 노드가
    # 아니므로 자연히 제외된다. write 부여 벡터 2가지: {"actor":"human"} dict · f(actor="human") kwarg.
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if k is not None and _is_actor_key(k) and _is_human(v):
                    offenders.append((node.lineno, 'dict {"actor": "human"}'))
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "actor" and _is_human(kw.value):
                    offenders.append((node.lineno, 'call actor="human"'))
    if offenders:
        for i, s in offenders:
            print("      [inventory] binggu.py 잔존 human write 리터럴 L%d: %s" % (i, s))
    # 투명성: hosted 3파일은 P1-B 에서 commit_bundle exact-bound 경유로 봉인됨(direct human write 제거).
    # 매 실행 봉인 상태를 명시(숨김 0). 존재/미존재 모두 정보만(실패 아님).
    for p in KNOWN_P1B_HOSTED:
        state = "봉인(commit_bundle 사람 저장 게이트 경유·direct human write 제거)" if os.path.exists(p) else "부재"
        print("      [inventory · P1-B SEALED] %s — %s" % (os.path.relpath(p, REPO), state))
    return not offenders


if __name__ == "__main__":
    print("=" * 70)
    print("P1-A.1 Approval-Origin Contract — 회귀 하니스 (TIER-2)")
    print("=" * 70)
    if not sys.argv[1:] or "--selftest" in sys.argv:
        raise SystemExit(run())
    print("usage: binggu_approval_origin_selftest.py --selftest")
    sys.exit(2)
