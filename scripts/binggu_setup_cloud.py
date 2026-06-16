# -*- coding: utf-8 -*-
"""binggu_setup_cloud.py — 흩어진 cloud 셋업 명령을 1개 진입점으로 묶는 오케스트레이터.

본질(중요): 이 스크립트는 owner 가 오늘 classifier hard block 때문에 `!` 로 손 빌린
KV put / deploy 를 "대신 실행" 하지 않는다. 신규 사용자는 본인 셸에서 직접 돌리므로
하네스 hard block 무관 — 따라서 핵심은 "대행" 이 아니라 "흩어진 명령을 1개 진입점으로
묶고 멱등·실패정지" 다.

설계 단계(전부 멱등 — 여러 번 돌려도 KV 중복생성·toml 중복기입·스케줄러 중복 0):
  [0] preflight (read-only): Python 3.10+ + wrangler 존재(글로벌/로컬) + detect_os.
  [0d] wrangler 로컬 설치 (멱등): hosted/workers 에 npm install — 신규 사용자의
       npx 재다운로드/스케줄러 timeout(0xC000013A)/좀비 누적(0x800710E0) 원천 차단.
       node_modules/.bin/wrangler 있으면 skip.
  [1] wrangler login 점검·안내(대행 금지): wrangler whoami 만. 미로그인 → 멈춤 + 안내.
  [2] KV namespace create (멱등): toml id 가 placeholder 면 create + id 파싱, 실 id 면 skip.
  [3] toml id 자동 기입 (멱등): [2] id 를 wrangler.real.toml 의 id 라인에 정밀 치환(.bak 백업).
  [4] packs 빌드 (멱등): realpack_build --write. 빈/최소 pack 도 정상.
  [5] KV put (멱등): wrangler kv key put. 초기 데이터 적재만.
  [6] deploy — 별도 GO (비가역, 기본 skip): --deploy 명시해야만 wrangler deploy.
  [7] 스케줄러 등록 (멱등, OS 분기): Windows=register_autopush.ps1, mac/WSL=안내만.
  [8] autopush 첫 점검 (dry-run): mock runner 1회 — 이중게이트 상태 표시(실 전송 0).

안전 불변식:
  - CF 토큰 평문 0: login 은 OAuth(브라우저)만. 스크립트가 토큰을 받거나 출력/저장 0.
    toml 에 들어가는 건 KV namespace id(비밀 아님)뿐.
  - 자동수집 OFF 유지: setup-cloud 는 capture_enabled 를 절대 건드리지 않는다(write 0).
  - autopush 이중게이트 무변: 스케줄러 등록 + 초기 KV put 만. 게이트 로직 무수정 —
    사람 SAVE 기록 없으면 이후 자동 push 0.
  - deploy 이중 게이트: --apply 와 별개로 --deploy 명시해야만 라이브 배포(비가역 분리).
  - 실 wrangler/스케줄러 호출은 owner 셸에서만. selftest 는 전부 mock + temp(실 CF/스케줄러 미접촉).

CLI:
  python binggu_setup_cloud.py             # dry-run 안내(변경 0)
  python binggu_setup_cloud.py --apply     # 실제 kv create / toml 기입 / kv put / 스케줄러
  python binggu_setup_cloud.py --apply --deploy   # 위 + wrangler deploy(비가역)
  python binggu_setup_cloud.py --selftest  # mock 으로 멱등 로직 검증(실 CF 미접촉)
"""
import argparse
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_platform as P  # noqa: E402

# ── 경로 상수 (autopush 와 동일 규칙 — config 고정) ─────────────────
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SCRIPTS_DIR)
WORKERS_DIR = os.path.join(REPO, "hosted", "workers")
WRANGLER_REAL = os.path.join(WORKERS_DIR, "wrangler.real.toml")
REGISTER_PS1 = os.path.join(REPO, "register_autopush.ps1")
PACKS_BINDING = "PACKS"
PACKS_KEY = "packs.json"
PACKS_DATA = os.path.join(WORKERS_DIR, "data", "packs.json")
SCHED_TASK_NAME = "BingguPack_AutoPush"
# placeholder 표식 — 이 값이거나 빈 id 면 namespace 미생성으로 본다.
PLACEHOLDER = "<OWNER_FILLS_KV_ID>"

# 단계 결과 상태 표기(env_check.render_report 스타일)
OK = "OK"        # 실행/이미 정상
SKIP = "SKIP"    # 이미 됨 → 멱등 skip
STOP = "STOP"    # 막힘 → 사람 행위/오류 안내 후 정지
INFO = "INFO"    # 안내만(mac/WSL 수동 단계 등)


def step(stage, status, msg, hint=None):
    return {"stage": stage, "status": status, "msg": msg, "hint": hint}


# ── [3] toml id 멱등 치환 (순수 함수 — synthetic 검증 가능) ─────────
_ID_LINE = re.compile(r'^(\s*id\s*=\s*)(["\'])(.*?)(\2)(.*)$', re.MULTILINE)


def read_kv_id(toml_text):
    """wrangler.real.toml 텍스트에서 [[kv_namespaces]] 의 id 값을 추출. 없으면 None."""
    m = _ID_LINE.search(toml_text)
    return m.group(3) if m else None


def is_placeholder_id(value):
    """id 가 미생성(placeholder/빈값)인지 — 실 namespace id 면 False."""
    return value is None or value.strip() == "" or value.strip() == PLACEHOLDER


def replace_kv_id(toml_text, new_id):
    """id 라인만 정밀 치환(다른 줄 무변). 이미 동일 id 면 동일 텍스트 반환(no-op).

    반환 (new_text, changed: bool). id 라인이 없으면 changed=False(치환 대상 부재).
    """
    m = _ID_LINE.search(toml_text)
    if not m:
        return toml_text, False
    if m.group(3) == new_id:
        return toml_text, False  # 이미 동일 → no-op(멱등)
    pre, q, _old, q2, tail = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
    repl = "%s%s%s%s%s" % (pre, q, new_id, q2, tail)
    new_text = toml_text[:m.start()] + repl + toml_text[m.end():]
    return new_text, True


def parse_kv_create_output(stdout):
    """`wrangler kv namespace create` stdout 에서 32-hex namespace id 파싱.

    wrangler 출력 형식이 버전마다 달라 'id = "..."' / 'id: "..."' / 헐벗은 32hex 모두 시도.
    못 찾으면 None(호출자가 STOP 처리).
    """
    if not stdout:
        return None
    # 1) id = "abc..." / id: "abc..."
    m = re.search(r'id\s*[:=]\s*["\']?([0-9a-fA-F]{32})["\']?', stdout)
    if m:
        return m.group(1).lower()
    # 2) 헐벗은 32-hex 토큰(마지막 등장)
    cands = re.findall(r'\b([0-9a-fA-F]{32})\b', stdout)
    return cands[-1].lower() if cands else None


# ── runner 추상화 (실 wrangler 는 owner 셸 / selftest 는 mock) ──────
def _real_runner(args, cwd=None):
    """실 wrangler 호출 — owner 셸에서만. import 시점 부수효과 0(호출 때만).

    npx 경유(P.resolve_npx) — Windows 의 wrangler 는 npm-global 설치 시 .cmd 라,
    shell=False subprocess 로 "wrangler" 이름만 호출하면 WinError 2(파일 못 찾음)가 난다.
    autopush(_real_wrangler_runner)와 동일 정책으로 통일(신규 Windows 사용자 회귀 방지).
    """
    import subprocess  # noqa: 지역 import — 모듈 로드 시 외부명령 의존 0
    npx = P.resolve_npx()
    full = [npx, "wrangler"] + list(args)
    proc = subprocess.run(full, cwd=cwd, capture_output=True, text=True)
    return {"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr,
            "args": full, "cwd": cwd}


# ── wrangler 로컬 설치 ([0d]) — 신규 사용자 npx 재다운로드/timeout/좀비 차단 ──
def _wrangler_local_path(workers_dir=None):
    """hosted/workers/node_modules 에 wrangler 가 설치돼 있으면 경로, 없으면 None.

    npx 는 cwd 의 node_modules/.bin 을 먼저 쓴다 — 로컬 설치돼 있으면 재다운로드 0.
    """
    wd = workers_dir or WORKERS_DIR
    for name in ("wrangler.cmd", "wrangler"):
        p = os.path.join(wd, "node_modules", ".bin", name)
        if os.path.exists(p):
            return p
    return None


def _real_npm_runner(args, cwd=None):
    """실 npm 호출 — owner 셸에서만. import 시점 부수효과 0(호출 때만)."""
    import subprocess  # noqa: 지역 import — 모듈 로드 시 외부명령 의존 0
    npm = "npm.cmd" if os.name == "nt" else "npm"
    npm = shutil.which(npm) or npm
    proc = subprocess.run([npm] + list(args), cwd=cwd, capture_output=True, text=True)
    return {"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def ensure_wrangler_local(apply=False, npm_runner=None, local_path=None, workers_dir=None):
    """[0d] hosted/workers 에 wrangler 로컬 설치(npm install) 멱등.

    신규 사용자 회귀 차단: 로컬 미설치면 autopush 가 npx 로 매번 wrangler 를 새로 받아
    5분 ExecutionTimeLimit 을 넘겨 스케줄러가 작업을 죽이고(0xC000013A) 좀비가 누적돼
    이후 실행이 거부된다(0x800710E0). 로컬 핀 설치 1회로 npx 가 즉시 로컬 바이너리를
    써 재다운로드 0. 멱등 — node_modules/.bin/wrangler 가 이미 있으면 SKIP.
    """
    wd = workers_dir or WORKERS_DIR
    found = local_path if local_path is not None else _wrangler_local_path(wd)
    if found:
        return step("0d", SKIP, "wrangler 로컬 설치 이미 있음 (npx 재다운로드 0)")
    if not apply:
        return step("0d", INFO, "wrangler 로컬 미설치 — --apply 시 npm install (hosted/workers)")
    runner = npm_runner or _real_npm_runner
    r = runner(["install"], wd)
    if r.get("rc") != 0:
        return step("0d", STOP, "npm install 실패 (wrangler 로컬 설치)",
                    "직접: cd hosted/workers && npm install\n원문: %s"
                    % (r.get("stderr") or r.get("stdout") or "(no output)"))
    return step("0d", OK, "wrangler 로컬 설치 완료 (npx 재다운로드/timeout/좀비 차단)")


# ── 단계 구현 (전부 read-only 또는 멱등) ───────────────────────────
def preflight(os_name=None, py_version=None, which_wrangler=None, wrangler_local=None, apply=False):
    """[0] read-only — Python 3.10+ + wrangler 존재(글로벌/로컬) + OS 분기. 변경 0.

    wrangler 가 글로벌·로컬 모두 없을 때: --apply 면 [0d]에서 로컬 설치하므로 STOP 아닌
    INFO, dry-run 이면 STOP(설치 안내).
    """
    os_name = os_name or P.detect_os()
    ver = py_version if py_version is not None else sys.version_info[:2]
    py_ok = tuple(ver) >= (3, 10)
    wpath = which_wrangler if which_wrangler is not None else shutil.which("wrangler")
    wlocal = wrangler_local if wrangler_local is not None else _wrangler_local_path()
    steps = []
    steps.append(step("0a", OK if py_ok else STOP,
                      "Python %d.%d" % (ver[0], ver[1]),
                      None if py_ok else "Python 3.10+ 필요 — https://python.org"))
    if wpath or wlocal:
        steps.append(step("0b", OK, "wrangler 발견: %s" % (wpath or wlocal)))
    elif apply:
        steps.append(step("0b", INFO, "wrangler 미설치 — [0d]에서 로컬 자동 설치 예정(--apply)"))
    else:
        steps.append(step("0b", STOP, "wrangler 미설치",
                          "직접 설치: npm i -g wrangler  /  또는 --apply 시 로컬 자동 설치"))
    steps.append(step("0c", INFO, "OS: %s (python launcher=%s)"
                      % (os_name, P.python_cmd(os_name))))
    ok = all(s["status"] != STOP for s in steps)
    return ok, steps


def check_login(runner):
    """[1] wrangler whoami 만 — 대행 금지. 미로그인 → STOP + 안내. 멱등(로그인이면 skip)."""
    r = runner(["whoami"], WORKERS_DIR)
    if r.get("rc") == 0:
        return True, step("1", SKIP, "이미 wrangler 로그인됨", None)
    return False, step("1", STOP, "wrangler 미로그인",
                       "직접 실행(브라우저 OAuth — 본인 행위): wrangler login")


def ensure_kv_namespace(toml_text, runner, apply=False):
    """[2] KV namespace create 멱등.

    이미 실 id → ('SKIP', id). placeholder + apply → create + 파싱. dry-run → INFO.
    반환 (step, kv_id_or_None).
    """
    cur = read_kv_id(toml_text)
    if not is_placeholder_id(cur):
        return step("2", SKIP, "KV namespace 이미 연결됨 (id 채워짐)"), cur
    if not apply:
        return step("2", INFO, "KV namespace 미생성 — --apply 시 wrangler kv namespace create PACKS"), None
    r = runner(["kv", "namespace", "create", PACKS_BINDING], WORKERS_DIR)
    if r.get("rc") != 0:
        return step("2", STOP, "KV namespace 생성 실패",
                    "원문: %s\n다음: 계정 권한/중복 이름 확인 후 재시도"
                    % (r.get("stderr") or r.get("stdout") or "(no output)")), None
    new_id = parse_kv_create_output(r.get("stdout", ""))
    if not new_id:
        return step("2", STOP, "namespace 생성됐으나 id 파싱 실패",
                    "wrangler 출력에서 32-hex id 를 직접 확인해 toml 에 기입:\n%s"
                    % r.get("stdout", "")), None
    return step("2", OK, "KV namespace 생성됨 (id 파싱 성공)"), new_id


def apply_toml_id(toml_path, new_id, apply=False, backup=True):
    """[3] toml id 자동 기입 멱등(.bak 백업). new_id None 이면 no-op.

    반환 (step, wrote: bool). KV id 는 비밀 아님 — 평문 토큰 0(불변식 유지).
    """
    if not new_id:
        return step("3", SKIP, "toml 기입 대상 없음(생성 단계 skip)"), False
    with open(toml_path, encoding="utf-8") as f:
        text = f.read()
    new_text, changed = replace_kv_id(text, new_id)
    if not changed:
        return step("3", SKIP, "toml id 이미 동일 — 무변경"), False
    if not apply:
        return step("3", INFO, "toml id 갱신 예정(--apply 시 기입, .bak 백업)"), False
    if backup:
        shutil.copy2(toml_path, toml_path + ".bak")
    with open(toml_path, "w", encoding="utf-8") as f:
        f.write(new_text)
    return step("3", OK, "wrangler.real.toml id 기입 완료 (.bak 백업)"), True


def build_packs_step(apply=False, build_fn=None, write_fn=None, out_path=None):
    """[4] realpack build --write 멱등. 빈/최소 pack 도 정상(빌더 자가검증 통과분만)."""
    if not apply:
        return step("4", INFO, "packs 빌드 예정(--apply 시 realpack_build --write)"), None
    if build_fn is None or write_fn is None:
        import binggu_realpack_build as RP
        build_fn = build_fn or RP.build_packs
        write_fn = write_fn or RP.write_packs
        out_path = out_path or RP.DATA_PATH
    res = build_fn()
    if res.get("status") != "OK":
        # 신규 = SAVE 0 일 수 있음. NO_REAL_LEDGER_DATA 등은 '아직 적재할 게 없음'(STOP 아님, 안내).
        return step("4", INFO, "빌드할 확정 노드 없음(신규는 정상) — reason=%s"
                    % res.get("reason")), None
    w = write_fn(res, out_path)
    if "written" not in w:
        return step("4", STOP, "packs validate 위반 — 적재 중단",
                    "violations=%s" % w.get("violations")), None
    return step("4", OK, "data/packs.json 생성: %s" % w["written"]), w["written"]


def kv_put_step(packs_path, runner, apply=False):
    """[5] KV put 멱등(초기 데이터 적재). config=wrangler.real.toml 고정(외부 주입 0)."""
    if not apply:
        return step("5", INFO, "KV put 예정(--apply 시 초기 데이터 적재)")
    if not packs_path or not os.path.exists(packs_path):
        return step("5", SKIP, "적재할 packs.json 없음 — KV put 생략(다음 SAVE 후 자동)")
    args = ["kv", "key", "put", PACKS_KEY, "--path", os.path.abspath(packs_path),
            "--binding", PACKS_BINDING, "--config", "wrangler.real.toml"]
    r = runner(args, WORKERS_DIR)
    if r.get("rc") != 0:
        return step("5", STOP, "KV put 실패",
                    "원문: %s" % (r.get("stderr") or r.get("stdout") or "(no output)"))
    return step("5", OK, "초기 packs.json → KV 적재 완료")


def deploy_step(runner, apply=False, deploy=False):
    """[6] deploy — 별도 GO(비가역). --deploy 명시 시에만. 데이터는 [5]에서 이미 KV에."""
    if not deploy:
        return step("6", SKIP, "코드 배포 생략(라이브 비가역) — 배포하려면 --deploy 추가\n"
                    "  데이터는 [5]에서 이미 KV에 — 미배포여도 데이터는 준비됨")
    if not apply:
        return step("6", INFO, "deploy 는 --apply --deploy 동시 명시 필요")
    r = runner(["deploy", "--config", "wrangler.real.toml"], WORKERS_DIR)
    if r.get("rc") != 0:
        return step("6", STOP, "wrangler deploy 실패",
                    "원문: %s" % (r.get("stderr") or r.get("stdout") or "(no output)"))
    return step("6", OK, "wrangler deploy 완료 (rollback 으로만 복귀 가능)")


def scheduler_step(os_name, runner=None, task_exists=None, apply=False):
    """[7] 스케줄러 등록 멱등(OS 분기). Windows=register_autopush.ps1(이미 -Force).
    mac/WSL/linux=launchd/cron 라인 안내만(P1 자동화 X).
    """
    if os_name != "windows":
        # cron/launchd 는 PATH 가 빈약 — "python3" 이름은 not found 위험. 현재 실행 중인
        # 파이썬 절대경로(sys.executable)로 안내해 PATH 무관하게 동작(autopush 회귀 교훈).
        py_abs = sys.executable or P.python_cmd(os_name)
        if os_name == "macos":
            hint = ("launchd plist 안내(자동생성은 P2): ~/Library/LaunchAgents 에 10분 주기 plist.\n"
                    "  또는 cron: */10 * * * * %s %s"
                    % (py_abs, os.path.join(SCRIPTS_DIR, "binggu_publish_autopush.py")))
        else:  # wsl / linux
            hint = ("cron 라인 추가(중복 점검 후): crontab -l | grep binggu || "
                    "(crontab -l 2>/dev/null; echo '*/10 * * * * %s %s') | crontab -"
                    % (py_abs, os.path.join(SCRIPTS_DIR, "binggu_publish_autopush.py")))
        return step("7", INFO, "스케줄러 자동 등록은 Windows 만 — %s 는 아래 명령 복붙" % os_name, hint)
    # Windows — 존재 확인 후 없으면 register_autopush.ps1
    if task_exists:
        return step("7", SKIP, "스케줄러 '%s' 이미 등록됨" % SCHED_TASK_NAME)
    if not apply:
        return step("7", INFO, "스케줄러 미등록 — --apply 시 register_autopush.ps1 실행")
    if runner is None:
        return step("7", INFO, "스케줄러 등록은 register_autopush.ps1 (owner 셸):\n  & \"%s\"" % REGISTER_PS1)
    r = runner(REGISTER_PS1)
    if isinstance(r, dict) and r.get("rc") not in (0, None):
        return step("7", STOP, "스케줄러 등록 실패",
                    "직접 실행: & \"%s\"" % REGISTER_PS1)
    return step("7", OK, "스케줄러 '%s' 등록 완료(이중게이트라 등록만으로 전송 0)" % SCHED_TASK_NAME)


def autopush_dryrun(runner=None, run_fn=None):
    """[8] autopush 첫 점검(dry-run) — mock runner 1회. 실 전송 0, 이중게이트 상태만 표시."""
    try:
        if run_fn is None:
            import binggu_publish_autopush as AP
            run_fn = AP.run_autopush

        def _mock(args, cwd=None):   # 실 wrangler 호출 차단 — dry-run 점검 전용
            return {"rc": 0, "stdout": "(dry-run mock — 실 전송 0)", "stderr": "", "mock": True}
        res = run_fn(runner=runner or _mock)
        status = res.get("status")
        reason = res.get("reason")
        human = res.get("save_gate_match")
        msg = "autopush 상태=%s reason=%s · 사람 SAVE 기록=%s" % (
            status, reason, {True: "있음", False: "없음", None: "미확인"}.get(human, human))
        return step("8", INFO, msg,
                    "긴급 OFF: %s 에 autopush_disabled 파일 생성" % P.binggu_home())
    except Exception as e:  # 점검 실패는 셋업 흐름에 영향 0(안내만)
        return step("8", INFO, "autopush dry-run 점검 생략(%s)" % type(e).__name__)


# ── 오케스트레이터 ─────────────────────────────────────────────────
def run_setup(apply=False, deploy=False, os_name=None,
              login_runner=None, kv_runner=None, sched_runner=None, npm_runner=None,
              task_exists=None, toml_path=None, build_fn=None, write_fn=None,
              packs_out=None, autopush_run_fn=None, preflight_kwargs=None):
    """전체 흐름 1회. 기본 dry-run(변경 0). apply=True 면 실 변경(멱등).

    모든 runner/경로는 selftest 주입용(기본은 실 wrangler/실 경로). selftest 는 전부 mock + temp.
    """
    os_name = os_name or P.detect_os()
    toml_path = toml_path or WRANGLER_REAL
    login_runner = login_runner or _real_runner
    kv_runner = kv_runner or _real_runner
    steps = []

    pf_ok, pf_steps = preflight(os_name=os_name, apply=apply, **(preflight_kwargs or {}))
    steps.extend(pf_steps)
    if not pf_ok:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "0"}

    # [0d] wrangler 로컬 설치 (신규 사용자 npx 재다운로드/timeout/좀비 차단)
    s0d = ensure_wrangler_local(apply=apply, npm_runner=npm_runner)
    steps.append(s0d)
    if s0d["status"] == STOP:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "0d"}

    # [1] login (read-only whoami)
    login_ok, s1 = check_login(login_runner)
    steps.append(s1)
    if not login_ok:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "1"}

    # [2] KV namespace
    with open(toml_path, encoding="utf-8") as f:
        toml_text = f.read()
    s2, kv_id = ensure_kv_namespace(toml_text, kv_runner, apply=apply)
    steps.append(s2)
    if s2["status"] == STOP:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "2"}

    # [3] toml id 기입
    s3, _wrote = apply_toml_id(toml_path, kv_id, apply=apply)
    steps.append(s3)
    if s3["status"] == STOP:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "3"}

    # [4] packs build
    s4, packs_path = build_packs_step(apply=apply, build_fn=build_fn, write_fn=write_fn,
                                      out_path=packs_out)
    steps.append(s4)
    if s4["status"] == STOP:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "4"}

    # [5] KV put
    s5 = kv_put_step(packs_path, kv_runner, apply=apply)
    steps.append(s5)
    if s5["status"] == STOP:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "5"}

    # [6] deploy (별도 GO)
    s6 = deploy_step(kv_runner, apply=apply, deploy=deploy)
    steps.append(s6)
    if s6["status"] == STOP:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "6"}

    # [7] scheduler
    s7 = scheduler_step(os_name, runner=sched_runner, task_exists=task_exists, apply=apply)
    steps.append(s7)

    # [8] autopush dry-run 점검
    s8 = autopush_dryrun(run_fn=autopush_run_fn)
    steps.append(s8)

    return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": None}


# ── 사람용 리포트 (env_check.render_report 스타일) ─────────────────
_TAG = {OK: "[OK]", SKIP: "[SKIP 이미됨]", STOP: "[STOP]", INFO: "[--]"}


def render_report(result):
    mode = "APPLY" if result["apply"] else "DRY-RUN(점검만 · 변경 0)"
    if result["deploy"]:
        mode += " +DEPLOY"
    L = ["=" * 64, "빙구팩 cloud 셋업 — %s" % mode, "=" * 64]
    for s in result["steps"]:
        L.append("%s [%s] %s" % (_TAG.get(s["status"], "[?]"), s["stage"], s["msg"]))
        if s.get("hint"):
            for line in s["hint"].splitlines():
                L.append("       %s" % line)
    L.append("-" * 64)
    if result["halted_at"] is not None:
        L.append("⛔ [%s] 단계에서 멈춤 — 위 안내대로 본인이 처리 후 다시 실행하세요." % result["halted_at"])
    elif not result["apply"]:
        L.append("다음: 실제 적용은  python binggu.py setup-cloud --apply")
        L.append("      (코드 배포까지: --apply --deploy / login·deploy 는 본인 행위)")
    else:
        L.append("완료 — 첫 SAVE n 을 하면 다음 스케줄러 주기에 자동 KV 갱신(이중게이트).")
    L.append("주체: wrangler login(브라우저 OAuth) · --deploy 결정 은 본인 손. 토큰 평문 0.")
    return "\n".join(L)


# ── selftest (실 wrangler/스케줄러/CF 미접촉 — mock + temp 만) ──────
def _selftest():
    import tempfile
    ok = 0
    tot = 0

    def chk(name, cond):
        nonlocal ok, tot
        tot += 1
        ok += 1 if cond else 0
        print(("  PASS " if cond else "  FAIL ") + name)

    SAMPLE = ('[[kv_namespaces]]\nbinding = "PACKS"\n'
              'id = "%s"  # comment\n[vars]\nPACKS_KEY = "packs.json"\n')

    # 1. read_kv_id
    chk("1.read_kv_id placeholder", read_kv_id(SAMPLE % PLACEHOLDER) == PLACEHOLDER)
    chk("2.read_kv_id real", read_kv_id(SAMPLE % "78badf369eac47498a8e60038bba0f2b")
        == "78badf369eac47498a8e60038bba0f2b")
    # 3. is_placeholder
    chk("3.placeholder 판정 True", is_placeholder_id(PLACEHOLDER) and is_placeholder_id("") and is_placeholder_id(None))
    chk("4.실 id 는 placeholder 아님", not is_placeholder_id("78badf369eac47498a8e60038bba0f2b"))
    # 5. replace_kv_id 멱등 — 동일 id 면 changed False
    t = SAMPLE % "aaaa1111bbbb2222cccc3333dddd4444"
    _nt, ch = replace_kv_id(t, "aaaa1111bbbb2222cccc3333dddd4444")
    chk("5.동일 id 치환 = no-op(멱등)", ch is False)
    # 6. replace_kv_id placeholder → 실 id 치환, 다른 줄 무변
    nt, ch2 = replace_kv_id(SAMPLE % PLACEHOLDER, "ffff0000ffff0000ffff0000ffff0000")
    chk("6.placeholder→실 id 치환", ch2 is True and "ffff0000ffff0000ffff0000ffff0000" in nt)
    chk("7.치환 후 다른 줄 보존", 'binding = "PACKS"' in nt and 'PACKS_KEY = "packs.json"' in nt)
    chk("8.id 라인 1개만 존재(중복 0)", nt.count("id =") == 1)
    # 9. parse_kv_create_output 여러 형식
    chk("9.parse id = \"...\"", parse_kv_create_output('id = "0123456789abcdef0123456789abcdef"')
        == "0123456789abcdef0123456789abcdef")
    chk("10.parse 헐벗은 32hex", parse_kv_create_output("created. ABCDEF0123456789ABCDEF0123456789 done")
        == "abcdef0123456789abcdef0123456789")
    chk("11.parse 실패 None", parse_kv_create_output("no id here") is None)

    # mock runners
    def mock_login_ok(args, cwd=None):
        return {"rc": 0, "stdout": "logged in", "stderr": ""}

    def mock_login_no(args, cwd=None):
        return {"rc": 1, "stdout": "", "stderr": "not logged in"}

    def mock_kv(args, cwd=None):
        if args[:3] == ["kv", "namespace", "create"]:
            return {"rc": 0, "stdout": 'id = "1234567890abcdef1234567890abcdef"', "stderr": ""}
        return {"rc": 0, "stdout": "kv put ok", "stderr": ""}

    # 12. preflight read-only — Python 3.9 → STOP
    pf_ok, pf = preflight(os_name="windows", py_version=(3, 9), which_wrangler="C:/w")
    chk("12.preflight py<3.10 → STOP", pf_ok is False and any(s["status"] == STOP for s in pf))
    # 13. preflight wrangler 부재(글로벌·로컬 모두) + dry-run → STOP
    pf_ok2, _ = preflight(os_name="windows", py_version=(3, 11), which_wrangler="", wrangler_local="")
    chk("13.preflight wrangler 부재 + dry-run → STOP", pf_ok2 is False)
    # 14. preflight 정상 → ok
    pf_ok3, _ = preflight(os_name="windows", py_version=(3, 11), which_wrangler="C:/w")
    chk("14.preflight 정상 → ok", pf_ok3 is True)
    # 15. login 미로그인 → STOP
    lo, s = check_login(mock_login_no)
    chk("15.미로그인 → STOP(대행 0)", lo is False and s["status"] == STOP and "wrangler login" in s["hint"])
    # 16. login 됨 → SKIP
    lo2, s2 = check_login(mock_login_ok)
    chk("16.로그인됨 → SKIP", lo2 is True and s2["status"] == SKIP)

    work = tempfile.mkdtemp(prefix="setup_cloud_st_")
    tp = os.path.join(work, "wrangler.real.toml")

    # 17. ensure_kv: 실 id 이미 있음 → SKIP, create 호출 0
    calls = []

    def mock_kv_track(args, cwd=None):
        calls.append(args)
        return mock_kv(args, cwd)
    s, kid = ensure_kv_namespace(SAMPLE % "aaaabbbbccccddddeeeeffffaaaabbbb", mock_kv_track, apply=True)
    chk("17.실 id → SKIP + create 미호출", s["status"] == SKIP and not calls)
    # 18. ensure_kv: placeholder + apply → create + 파싱
    s, kid = ensure_kv_namespace(SAMPLE % PLACEHOLDER, mock_kv_track, apply=True)
    chk("18.placeholder+apply → 생성+id 파싱", s["status"] == OK and kid == "1234567890abcdef1234567890abcdef")
    # 19. ensure_kv: dry-run → INFO, create 미호출
    calls.clear()
    s, kid = ensure_kv_namespace(SAMPLE % PLACEHOLDER, mock_kv_track, apply=False)
    chk("19.dry-run → INFO + create 미호출", s["status"] == INFO and not calls)

    # 20. apply_toml_id 멱등: placeholder 파일에 기입 → .bak 생성 + 재기입 no-op
    with open(tp, "w", encoding="utf-8") as f:
        f.write(SAMPLE % PLACEHOLDER)
    s, wrote = apply_toml_id(tp, "9999888877776666555544443333222", apply=True)
    chk("20.toml 기입 OK + .bak 백업", wrote is True and os.path.exists(tp + ".bak"))
    s2, wrote2 = apply_toml_id(tp, "9999888877776666555544443333222", apply=True)
    chk("21.재기입 = no-op(멱등 SKIP)", wrote2 is False and s2["status"] == SKIP)
    with open(tp, encoding="utf-8") as f:
        chk("22.기입 후 id 라인 1개만(중복 0)", f.read().count("id =") == 1)

    # 23. kv_put dry-run → INFO
    chk("23.kv_put dry-run → INFO", kv_put_step("x", mock_kv, apply=False)["status"] == INFO)
    # 24. kv_put 파일 부재 → SKIP
    chk("24.kv_put 파일 부재 → SKIP", kv_put_step(os.path.join(work, "no.json"), mock_kv, apply=True)["status"] == SKIP)
    # 25. kv_put 정상
    pj = os.path.join(work, "packs.json")
    open(pj, "w").write("{}")
    chk("25.kv_put 적재 OK", kv_put_step(pj, mock_kv, apply=True)["status"] == OK)

    # 26. deploy 기본 skip(--deploy 없음)
    chk("26.deploy 기본 SKIP(비가역 분리)", deploy_step(mock_kv, apply=True, deploy=False)["status"] == SKIP)
    # 27. deploy --deploy → 실행
    chk("27.--deploy → OK", deploy_step(mock_kv, apply=True, deploy=True)["status"] == OK)

    # 28. scheduler Windows 이미 존재 → SKIP
    chk("28.win 스케줄러 존재 → SKIP", scheduler_step("windows", task_exists=True, apply=True)["status"] == SKIP)
    # 29. scheduler Windows 미존재 + apply + mock runner → OK
    sched_calls = []

    def mock_sched(ps1):
        sched_calls.append(ps1)
        return {"rc": 0}
    chk("29.win 미존재+apply → 등록 OK", scheduler_step("windows", runner=mock_sched, task_exists=False, apply=True)["status"] == OK and sched_calls)
    # 30. scheduler mac/WSL → INFO(안내만, 자동 0)
    chk("30.mac → INFO(자동 등록 X)", scheduler_step("macos", apply=True)["status"] == INFO)
    chk("31.wsl → INFO(cron 안내)", "cron" in (scheduler_step("wsl", apply=True)["hint"] or ""))

    # 32. autopush dry-run mock — 실 전송 0
    def mock_autopush(runner=None):
        # runner 가 mock 인지 확인(실 wrangler 호출 0 보장)
        return {"status": "NOOP", "reason": "NO_HUMAN_SAVE_RECORD", "save_gate_match": False}
    chk("32.autopush dry-run = 점검만(INFO)", autopush_dryrun(run_fn=mock_autopush)["status"] == INFO)

    # 33. capture flag write 0 — capture profile/flag 를 쓰는 호출/대입 코드 부재.
    #     (검사 토큰은 분리 조립 — 이 줄 자체가 자기검출에 걸리지 않게.)
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    bad_writes = [
        "init_" + "profile(",        # capture profile 설치 호출
        "capture_" + "enabled =",    # flag 직접 대입
        "set_" + "capture",          # flag setter (조각 — 자기검출 회피)
    ]
    chk("33.capture flag write 코드 0", not any(t in src for t in bad_writes))

    # 34. 전체 오케스트레이터 dry-run — 변경 0(toml 무수정), 모든 단계 STOP 아님
    before = open(tp, encoding="utf-8").read()
    res = run_setup(apply=False, os_name="windows",
                    login_runner=mock_login_ok, kv_runner=mock_kv_track,
                    task_exists=False, toml_path=tp,
                    preflight_kwargs={"py_version": (3, 11), "which_wrangler": "C:/w"},
                    autopush_run_fn=mock_autopush)
    after = open(tp, encoding="utf-8").read()
    chk("34.dry-run 변경 0(toml 불변)", before == after and res["halted_at"] is None)
    # 35. 미로그인 → halted_at=1, 이후 단계 미실행
    res2 = run_setup(apply=True, os_name="windows", login_runner=mock_login_no, kv_runner=mock_kv,
                     toml_path=tp, preflight_kwargs={"py_version": (3, 11), "which_wrangler": "C:/w"})
    chk("35.미로그인 → halt at [1]", res2["halted_at"] == "1")
    # 36. render_report STOP 표기
    chk("36.render STOP 표기", "STOP" in render_report(res2) and "wrangler login" in render_report(res2))
    # 37. 실 toml 존재 점검(repo 정합) — 읽기만
    chk("37.실 wrangler.real.toml 존재", os.path.exists(WRANGLER_REAL))
    # 38. 실 toml 은 이미 실 id(placeholder 아님) → setup 멱등 skip 확인
    real_id = read_kv_id(open(WRANGLER_REAL, encoding="utf-8").read())
    chk("38.실 toml id 이미 채워짐(멱등 skip)", not is_placeholder_id(real_id))

    # ── [0d] wrangler 로컬 설치 (신규 사용자 회귀 차단) ──
    # 39. _wrangler_local_path: node_modules/.bin/wrangler 있으면 탐지
    wdir = tempfile.mkdtemp(prefix="wlocal_st_")
    binp = os.path.join(wdir, "node_modules", ".bin")
    os.makedirs(binp)
    open(os.path.join(binp, "wrangler.cmd"), "w").write("")
    chk("39.로컬 wrangler 탐지", _wrangler_local_path(wdir) is not None)
    # 40. _wrangler_local_path: 없으면 None
    chk("40.로컬 미설치 → None", _wrangler_local_path(tempfile.mkdtemp(prefix="wempty_st_")) is None)
    # 41. ensure: 이미 있음 → SKIP (npm 미호출)
    npm_calls = []

    def mock_npm(args, cwd=None):
        npm_calls.append(args)
        return {"rc": 0, "stdout": "added 34 packages", "stderr": ""}
    chk("41.로컬 이미있음 → SKIP",
        ensure_wrangler_local(apply=True, npm_runner=mock_npm, local_path="X")["status"] == SKIP and not npm_calls)
    # 42. ensure: dry-run → INFO (npm 미호출)
    chk("42.dry-run → INFO + npm 미호출",
        ensure_wrangler_local(apply=False, npm_runner=mock_npm, local_path=None, workers_dir=wdir + "_none")["status"] == INFO and not npm_calls)
    # 43. ensure: apply + 미설치 → npm install OK
    s43 = ensure_wrangler_local(apply=True, npm_runner=mock_npm, local_path=None, workers_dir=os.path.join(wdir, "nomod"))
    chk("43.apply+미설치 → npm install OK", s43["status"] == OK and npm_calls == [["install"]])
    # 44. ensure: npm install 실패 → STOP
    def mock_npm_fail(args, cwd=None):
        return {"rc": 1, "stdout": "", "stderr": "ENOENT"}
    chk("44.npm install 실패 → STOP",
        ensure_wrangler_local(apply=True, npm_runner=mock_npm_fail, local_path=None, workers_dir=os.path.join(wdir, "nomod2"))["status"] == STOP)
    # 45. preflight: apply + wrangler 둘다 없음 → 0b INFO(STOP 아님), pf_ok True
    pf_ok4, pf4 = preflight(os_name="windows", py_version=(3, 11), which_wrangler="", wrangler_local="", apply=True)
    chk("45.apply+wrangler부재 → STOP 아님([0d]에서 설치)",
        pf_ok4 is True and any(s["stage"] == "0b" and s["status"] == INFO for s in pf4))
    # 46. run_setup dry-run: [0d] 단계 존재 + npm 미호출
    npm_calls.clear()
    res46 = run_setup(apply=False, os_name="windows",
                      login_runner=mock_login_ok, kv_runner=mock_kv_track, npm_runner=mock_npm,
                      task_exists=False, toml_path=tp,
                      preflight_kwargs={"py_version": (3, 11), "which_wrangler": "C:/w"},
                      autopush_run_fn=mock_autopush)
    chk("46.run_setup [0d] 단계 존재 + dry-run npm 미호출",
        any(s["stage"] == "0d" for s in res46["steps"]) and not npm_calls)

    print("\n" + "=" * 62)
    print("binggu_setup_cloud — selftest (mock + temp · 실 CF/스케줄러 미접촉)")
    print("=" * 62)
    print("RESULT: %d/%d %s" % (ok, tot, "PASS" if ok == tot else "FAIL"))
    gate = "GO" if ok == tot else "NO-GO"
    print("GATE: %s" % gate)
    return ok == tot


def main(argv=None):
    p = argparse.ArgumentParser(prog="binggu_setup_cloud",
                                description="흩어진 cloud 셋업 명령을 1개 진입점으로(멱등·실패정지)")
    p.add_argument("--apply", action="store_true", help="실제 변경(kv create/toml/kv put/스케줄러)")
    p.add_argument("--deploy", action="store_true", help="(--apply 와 함께) wrangler deploy 까지 — 비가역")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)
    if a.selftest:
        return 0 if _selftest() else 1
    res = run_setup(apply=a.apply, deploy=a.deploy)
    print(render_report(res))
    return 0 if res["halted_at"] is None else 2


if __name__ == "__main__":
    sys.exit(main())
