# -*- coding: utf-8 -*-
"""binggu_setup_save.py — 저장 채널(save_mcp worker) 온보딩 오케스트레이터.

setup-cloud(읽기 worker)와 짝. 이 모듈은 "ChatGPT/claude 채팅 저장 → 클라우드 inbox
→ PC pull → 로컬 장부" 채널을 신규 사용자 본인 CF 계정에 셋업한다.
`binggu onboard` 가 setup-cloud → setup-save 를 순차 호출(원클릭 진입점).

단계(전부 멱등 · 기본 dry-run):
  [s0] preflight: wrangler.save_mcp.prod.toml 존재 + wrangler(글로벌/로컬).
  [s1] wrangler login 점검(대행 금지 — setup_cloud [1] 재사용).
  [s2] 키 생성 (멱등): <repo>/../workers_port/.dev.vars.save_mcp 에
       SAVE_PATH_TOKEN·SAVE_SIGN_SECRET 생성(secrets.token_hex(24)=48hex).
       이미 있으면 SKIP(값 불변 — 재발급은 파일 삭제 후 재실행).
       파일은 repo 밖(workers_port) — git 커밋 유출 원천 차단.
  [s3] deploy — 별도 GO(비가역, --deploy 명시): wrangler deploy --config save_mcp.prod.
       시크릿 미주입 worker 는 503 전면 거부(save_intent_mcp.ts pathKey 가드) — 노출 창 0.
  [s4] secret put (--deploy 와 묶음 — worker 존재 보장 시점): 값은 stdin 주입
       (셸 히스토리/프로세스 목록/argv 노출 0).
  [s5] WORKER_URL 기입 (멱등): deploy 출력의 workers.dev URL 을 .dev.vars.save_mcp upsert.
  [s6] 커넥터 안내: 기본 마스킹(…/mcp2/앞8자…) — 전체 URL 은 --show-url 옵트인.
  [s7] auto-pull 스케줄러 (멱등·OS 분기): Windows=register_autopull.ps1(경로 자동탐지),
       mac/linux=cron 라인 안내만(자동 등록 X).

안전 불변식:
  - 시크릿 평문 출력 0: report 는 앞8자+길이 마스킹만. --show-url 은 본인 옵트인.
  - 시크릿 파일 repo 밖 + secret put 은 stdin — 커밋/히스토리 유출 0.
  - login·deploy 는 본인 행위(대행 0) — setup_cloud 와 동일 정책.
  - 자동수집/자동저장 게이트 무변: 이 모듈은 전송로만 셋업. 저장은 여전히
    hosted 쪽 confirm('SAVE n') 사람-증거 + auto-pull 의 후보>0/PII flag skip 게이트.
  - selftest 전부 mock + temp — 실 CF/스케줄러/실 키파일 미접촉.

CLI:
  python binggu_setup_save.py                    # dry-run 점검(변경 0)
  python binggu_setup_save.py --apply            # 키 생성 + 스케줄러(배포 제외)
  python binggu_setup_save.py --apply --deploy   # 위 + deploy + secret put(비가역)
  python binggu_setup_save.py --selftest
"""
import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_platform as P  # noqa: E402
import binggu_setup_cloud as SC  # noqa: E402 — step/상수/runner/login 재사용(정책 단일화)

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SCRIPTS_DIR)
WORKERS_DIR = os.path.join(REPO, "hosted", "workers")
WRANGLER_SAVE = os.path.join(WORKERS_DIR, "wrangler.save_mcp.prod.toml")
REGISTER_PS1 = os.path.join(SCRIPTS_DIR, "register_autopull.ps1")
AUTOPULL_PY = os.path.join(SCRIPTS_DIR, "auto_pull_hosted.py")


def _sched_name(base):
    """스케줄 작업명에 env BINGGU_TASK_SUFFIX 를 반영(같은 PC 다중 사용자 충돌 회피).
    미설정(기본) → 현행 이름 그대로(owner·기존 사용자 회귀 0). register_*.ps1 도 같은
    env 를 독립 해석하므로 subprocess env 상속으로 등록/조회 이름이 자동 일치한다."""
    sfx = "".join(c for c in (os.environ.get("BINGGU_TASK_SUFFIX") or "")
                  if c == "_" or (c.isascii() and c.isalnum()))  # ASCII 한정 = ps1 -replace 와 동일
    return "%s_%s" % (base, sfx) if sfx else base


SCHED_TASK_NAME = _sched_name("BingguPack_AutoPull")
REGISTER_WEBMCP_PS1 = os.path.join(SCRIPTS_DIR, "register_webmcp.ps1")
START_WEB_PY = os.path.join(SCRIPTS_DIR, "start_binggu_web.py")
WEBMCP_TASK_NAME = _sched_name("BingguPack_WebMCP")
VARS_NAME = ".dev.vars.save_mcp"
KEY_FIELDS = ("SAVE_PATH_TOKEN", "SAVE_SIGN_SECRET")

step, OK, SKIP, STOP, INFO = SC.step, SC.OK, SC.SKIP, SC.STOP, SC.INFO


def default_wp_dir():
    """pull 러너(_load_save_env)와 동일 규약 — env 우선, 기본 <repo>/../workers_port."""
    return os.environ.get("BINGGU_WORKERS_PORT") or \
        os.path.abspath(os.path.join(REPO, "..", "workers_port"))


def mask(v):
    """시크릿 표시용 — 앞8자+길이만(평문 0). 8자 이하면 길이만."""
    v = v or ""
    if len(v) <= 8:
        return "…(%d자)" % len(v)
    return v[:8] + "…(%d자)" % len(v)


def read_dev_vars(path):
    """KEY=VALUE 파일 파싱 — openbinggu_save_intent_live_runner._load_save_env 와 동일 규칙."""
    d = {}
    if not os.path.exists(path):
        return d
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            if "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
    return d


def _read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _read_json(path):
    return json.loads(_read_text(path))


def write_dev_vars(path, d):
    """KEY=VALUE 재작성(순서 고정 — diff 안정). 디렉토리 없으면 생성."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    order = ["WORKER_URL"] + list(KEY_FIELDS)
    keys = [k for k in order if k in d] + [k for k in d if k not in order]
    with open(path, "w", encoding="utf-8") as fp:
        for k in keys:
            fp.write("%s=%s\n" % (k, d[k]))


# ── [s2] 키 생성 (멱등) ─────────────────────────────────────────────
def ensure_save_keys(wp_dir, apply=False, gen=None):
    """SAVE_PATH_TOKEN/SAVE_SIGN_SECRET 없으면 생성(48hex). 있으면 SKIP(값 불변).

    반환 (step, keys_dict_or_None). keys 는 호출자(secret put)용 — report 로는 안 나감.
    """
    if gen is None:
        import secrets as _sec
        gen = lambda: _sec.token_hex(24)  # noqa: E731 — 48 hex (owner 키와 동일 길이)
    path = os.path.join(wp_dir, VARS_NAME)
    d = read_dev_vars(path)
    if all(d.get(k) for k in KEY_FIELDS):
        return step("s2", SKIP, "키 이미 있음: %s (%s)" % (
            path, " ".join("%s=%s" % (k, mask(d[k])) for k in KEY_FIELDS))), d
    if not apply:
        return step("s2", INFO, "키 미생성 — --apply 시 %s 에 48hex 2종 생성" % path), None
    for k in KEY_FIELDS:
        if not d.get(k):
            d[k] = gen()
    write_dev_vars(path, d)
    return step("s2", OK, "키 생성 완료: %s (%s) — 이 파일은 repo 밖·비밀" % (
        path, " ".join("%s=%s" % (k, mask(d[k])) for k in KEY_FIELDS))), d


# ── [s3] deploy (별도 GO) ───────────────────────────────────────────
_URL_RE = re.compile(r"https://[A-Za-z0-9.\-]+\.workers\.dev")


def parse_deploy_url(stdout):
    """wrangler deploy stdout 에서 workers.dev URL 추출. 없으면 None."""
    m = _URL_RE.search(stdout or "")
    return m.group(0) if m else None


def deploy_save_step(runner, apply=False, deploy=False):
    """save_mcp worker 배포 — --deploy 명시 시에만(비가역). 반환 (step, url_or_None)."""
    if not deploy:
        return step("s3", SKIP, "save worker 배포 생략(비가역) — 배포하려면 --deploy 추가\n"
                    "  미배포/시크릿 미주입 worker 는 503 전면 거부라 노출 창 0"), None
    if not apply:
        return step("s3", INFO, "deploy 는 --apply --deploy 동시 명시 필요"), None
    r = runner(["deploy", "--config", os.path.basename(WRANGLER_SAVE)], WORKERS_DIR)
    if r.get("rc") != 0:
        return step("s3", STOP, "save worker deploy 실패",
                    "원문: %s" % (r.get("stderr") or r.get("stdout") or "(no output)")), None
    url = parse_deploy_url(r.get("stdout", "") + "\n" + r.get("stderr", ""))
    if not url:
        return step("s3", OK, "deploy 완료 — URL 파싱 실패(출력에서 workers.dev 주소 직접 확인)"), None
    return step("s3", OK, "save worker deploy 완료: %s" % url), url


# ── [s4] secret put (stdin 주입) ────────────────────────────────────
def _real_secret_runner(args, cwd=None, input_text=None):
    """wrangler secret put — 값은 stdin 으로만(argv/히스토리 노출 0). 호출 때만 subprocess."""
    import subprocess  # 지역 import — 모듈 로드 시 외부명령 의존 0
    npx = P.resolve_npx()
    proc = subprocess.run([npx, "wrangler"] + list(args), cwd=cwd,
                          capture_output=True, text=True, input=input_text)
    return {"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def secrets_put_step(keys, secret_runner=None, apply=False, deploy=False):
    """SAVE_PATH_TOKEN/SAVE_SIGN_SECRET 를 worker secret 으로 주입.

    --deploy 와 묶음(그 시점 worker 존재 보장). 같은 값 재주입은 무해(멱등).
    """
    if not (apply and deploy):
        return step("s4", INFO, "secret put 은 --apply --deploy 와 묶음(worker 존재 보장 시점)")
    if not keys or not all(keys.get(k) for k in KEY_FIELDS):
        return step("s4", STOP, "주입할 키 없음 — [s2] 키 생성이 선행돼야 함")
    runner = secret_runner or _real_secret_runner
    for k in KEY_FIELDS:
        r = runner(["secret", "put", k, "--config", os.path.basename(WRANGLER_SAVE)],
                   WORKERS_DIR, keys[k])
        if r.get("rc") != 0:
            return step("s4", STOP, "secret put 실패: %s" % k,
                        "외부 명령 출력은 입력 secret echo 가능성 때문에 표시하지 않음")
    return step("s4", OK, "worker secret 주입 완료(%s) — 값은 stdin 경유(노출 0)"
                % ", ".join(KEY_FIELDS))


# ── [s5] WORKER_URL 기입 (멱등) ─────────────────────────────────────
def upsert_worker_url(wp_dir, url, apply=False):
    """deploy URL 을 .dev.vars.save_mcp 의 WORKER_URL 로 upsert. 동일 값이면 no-op."""
    if not url:
        return step("s5", SKIP, "기입할 URL 없음(deploy 생략/파싱 실패)")
    path = os.path.join(wp_dir, VARS_NAME)
    d = read_dev_vars(path)
    if d.get("WORKER_URL") == url:
        return step("s5", SKIP, "WORKER_URL 이미 동일 — 무변경")
    if not apply:
        return step("s5", INFO, "WORKER_URL 기입 예정(--apply): %s" % url)
    d["WORKER_URL"] = url
    write_dev_vars(path, d)
    return step("s5", OK, "WORKER_URL 기입 완료: %s" % url)


# ── [s6] 커넥터 안내 (기본 마스킹) ──────────────────────────────────
def connector_step(wp_dir, show_url=False):
    """ChatGPT 커넥터 URL 안내. 경로 secret 은 명시 옵션에도 출력하지 않는다."""
    d = read_dev_vars(os.path.join(wp_dir, VARS_NAME))
    base, tok = d.get("WORKER_URL"), d.get("SAVE_PATH_TOKEN")
    if not (base and tok):
        return step("s6", INFO, "커넥터 URL 형식: <WORKER_URL>/mcp2/<SAVE_PATH_TOKEN>\n"
                    "  키 생성([s2])·deploy([s3]) 후 다시 실행하면 실제 주소 안내")
    shown = base.rstrip("/") + "/mcp2/" + mask(tok)
    hint = ("ChatGPT: 설정 → 커넥터 → 새 커넥터 → MCP 서버 URL 에 위 주소 붙여넣기.\n"
            "이 URL 은 비밀입니다(경로키 포함) — 공유/스크린샷 금지."
            + ("\n--show-url 은 보안상 폐기되어 마스킹 출력을 유지합니다." if show_url else ""))
    return step("s6", INFO, "커넥터 URL: %s" % shown, hint)


# ── [s7] auto-pull 스케줄러 (멱등·OS 분기) ──────────────────────────
def _real_task_exists(name=SCHED_TASK_NAME):
    """Windows 스케줄 태스크 존재 여부 — schtasks /Query rc==0."""
    import subprocess
    r = subprocess.run(["schtasks", "/Query", "/TN", name],
                       capture_output=True, text=True)
    return r.returncode == 0


def _real_ps1_runner(ps1):
    import subprocess
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", ps1], capture_output=True, text=True)
    return {"rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr}


def autopull_scheduler_step(os_name, runner=None, task_exists=None, apply=False):
    """5분 주기 auto-pull 등록. Windows=register_autopull.ps1(자동탐지판). 그외=cron 안내."""
    if os_name != "windows":
        py_abs = sys.executable or P.python_cmd(os_name)
        hint = ("cron 라인 추가(중복 점검 후): crontab -l | grep auto_pull || "
                "(crontab -l 2>/dev/null; echo '*/5 * * * * %s %s') | crontab -"
                % (py_abs, AUTOPULL_PY))
        return step("s7", INFO, "스케줄러 자동 등록은 Windows 만 — %s 는 아래 복붙" % os_name, hint)
    exists = task_exists if task_exists is not None else _real_task_exists()
    if exists:
        return step("s7", SKIP, "스케줄러 '%s' 이미 등록됨" % SCHED_TASK_NAME)
    if not apply:
        return step("s7", INFO, "스케줄러 미등록 — --apply 시 register_autopull.ps1 실행")
    r = (runner or _real_ps1_runner)(REGISTER_PS1)
    if isinstance(r, dict) and r.get("rc") not in (0, None):
        return step("s7", STOP, "스케줄러 등록 실패",
                    "직접 실행: powershell -ExecutionPolicy Bypass -File \"%s\"" % REGISTER_PS1)
    return step("s7", OK, "스케줄러 '%s' 등록 완료(5분 주기 · 후보>0/PII-flag skip 게이트 그대로)"
                % SCHED_TASK_NAME)


# ── [s8] 웹 MCP(선택) — 기본 안내(변경 0) · --webmcp 명시 옵트인 시 등록 실행 ──
def web_mcp_step(os_name=None, webmcp=False, apply=False, runner=None,
                 task_exists=None, cloudflared=None):
    """로컬 24도구를 웹/앱 커넥터로 여는 선택 단계. 공개 터널 노출은 사람 결정 정책 —
    자동 등록 0 유지, 단 본인이 --webmcp 를 명시 타이핑한 옵트인은 사람 결정으로 간주해
    (--apply 와 함께) register_webmcp.ps1 실행까지 대행한다. 미지정=기존 안내(INFO)."""
    if not webmcp:
        return step("s8", INFO, "(선택) 웹 MCP — 로컬 24도구를 claude/ChatGPT 커넥터로",
                    "--webmcp --apply 로 로그온 자동가동 등록(공개 터널=본인 결정 옵트인) 또는 본인 셸에서:\n"
                    "  powershell -ExecutionPolicy Bypass -File \"%s\"\n"
                    "  권장=named tunnel(고정 도메인, 예: mcp.binggu.uk) — 재부팅에도 URL 불변이라 커넥터 재등록 0.\n"
                    "  quick tunnel(<home>/mcp_web_url.txt)은 임시 폴백 — 재부팅마다 주소 갱신(그때만 사용)."
                    % REGISTER_WEBMCP_PS1)
    os_name = os_name or P.detect_os()
    if os_name != "windows":
        return step("s8", INFO, "웹 MCP 자동 등록은 Windows 만 — %s 는 수동 실행" % os_name,
                    "본인 셸에서: python \"%s\" (HTTP+cloudflared quick tunnel)" % START_WEB_PY)
    exists = task_exists if task_exists is not None else _real_task_exists(WEBMCP_TASK_NAME)
    if exists:
        return step("s8", SKIP, "웹 MCP 스케줄러 '%s' 이미 등록됨" % WEBMCP_TASK_NAME)
    cf = cloudflared if cloudflared is not None else (shutil.which("cloudflared") is not None)
    if not cf:
        return step("s8", STOP, "cloudflared 미설치 — 웹 MCP 터널 불가",
                    "설치: https://developers.cloudflare.com/cloudflared/ 후 --webmcp --apply 재실행")
    if not apply:
        return step("s8", INFO, "웹 MCP 미등록 — --webmcp --apply 시 register_webmcp.ps1 실행")
    r = (runner or _real_ps1_runner)(REGISTER_WEBMCP_PS1)
    if isinstance(r, dict) and r.get("rc") not in (0, None):
        return step("s8", STOP, "웹 MCP 등록 실패",
                    "직접 실행: powershell -ExecutionPolicy Bypass -File \"%s\"" % REGISTER_WEBMCP_PS1)
    return step("s8", OK, "웹 MCP 스케줄러 '%s' 등록 완료(로그온 자동가동 · 권장 named tunnel 고정도메인"
                " → 재부팅에도 URL 불변 · quick tunnel 폴백 주소 <home>/mcp_web_url.txt)"
                % WEBMCP_TASK_NAME)


# ── [s9] 개인 팩 config — 신규 사용자 auto_create 마커(owner 회귀 0) ──
def person_pack_config_step(apply=False, home=None, username=None):
    """PACK_ID 사용자별 자동생성 진입점. env/기존 config 있으면 SKIP(owner·기존 사용자 회귀 0).
    --apply 시 <home>/person_pack.json 에 auto_create 마커 생성 → 첫 sync 가
    PACK_CREATE_REQUIRED 반환 → MCP Agent 가 opencrab_ingest_text 로 본인 클라우드에
    팩 생성 → person_pack_sync --record-pack-id <uuid> 로 완결(이후 델타만 업로드).
    빙구팩 자체는 클라우드 자격증명 0 원칙 유지(생성은 사용자 MCP 채널 몫)."""
    if os.environ.get("BINGGU_PACK_ID"):
        return step("s9", SKIP, "개인 팩 env(BINGGU_PACK_ID) 사용 중 — config 불필요")
    h = home or os.environ.get("BINGGU_HOME") or os.path.join(os.path.expanduser("~"), ".binggupack")
    cfg = os.path.join(h, "person_pack.json")
    if os.path.exists(cfg):
        return step("s9", SKIP, "개인 팩 config 이미 존재: person_pack.json")
    if os.path.exists(os.path.join(h, "person_pack_last.json")):
        # 이미 sync 운영 이력 = 기존 사용자(owner 포함) — auto_create 마커를 만들면
        # 다음 sync 가 PACK_CREATE_REQUIRED 로 바뀌어 기존 팩 업로드가 멎는다. 보호 SKIP.
        return step("s9", SKIP, "기존 sync 이력(person_pack_last.json) — 기존 팩 설정 유지")
    if not apply:
        return step("s9", INFO, "개인 팩 config 미생성 — --apply 시 auto_create 마커 생성",
                    "생성 후 첫 sync = PACK_CREATE_REQUIRED → Agent 팩 생성 → --record-pack-id 로 완결")
    os.makedirs(h, exist_ok=True)
    user = username or os.environ.get("USERNAME") or os.environ.get("USER") or "사용자"
    with open(cfg, "w", encoding="utf-8") as f:
        json.dump({"pack_id": "", "title": "%s 개인 온톨로지" % user, "auto_create": True},
                  f, ensure_ascii=False, indent=2)
    return step("s9", OK, "개인 팩 config 생성(auto_create) — 첫 sync 시 팩 생성 흐름으로 연결",
                "기존 사용자가 실수로 생성했다면 삭제로 복귀: %s" % cfg)


# ── [s10] OpenCrab Expert MCP 연결 (팩 자동생성 채널) ─────────────────
OPENCRAB_MCP_NAME = "opencrab-cloud"
# Expert 가입 시 발행되는 전용 URL 만 허용: https://[sub.]opencrab.<tld>/api/mcp/<token>
_OPENCRAB_URL_RE = re.compile(
    r"^https://([A-Za-z0-9\-]+\.)*opencrab\.[A-Za-z]{2,}/api/mcp/[A-Za-z0-9_\-]+$")


def default_claude_json():
    """Claude MCP 설정 경로 — env CLAUDE_CONFIG_PATH 우선, 기본 ~/.claude.json."""
    return os.environ.get("CLAUDE_CONFIG_PATH") or \
        os.path.join(os.path.expanduser("~"), ".claude.json")


def _utc_stamp():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def mask_opencrab_url(u):
    """URL 토큰 마스킹 — .../api/mcp/앞6자…(길이). 평문 토큰 노출 0."""
    m = re.match(r"^(https://[^/]+/api/mcp/)([A-Za-z0-9_\-]+)$", u or "")
    if not m:
        return "(형식오류)"
    tok = m.group(2)
    return m.group(1) + tok[:6] + "…(%d자)" % len(tok)


def register_opencrab_mcp(url, apply=False, claude_json_path=None, now_fn=None):
    """OpenCrab Expert 전용 URL 을 Claude MCP 설정(mcpServers.opencrab-cloud)에 등록(멱등).

    이 연결이 있어야 "X 팩 만들어줘" → crab_agent 로 스키마 팩 자동생성이 작동한다(Expert 전용).
    url 없음=INFO(미연결)·형식 오류=STOP·동일 등록=SKIP·미등록/변경=등록(백업 후 원자 교체).
    apply 아니면 PLAN(파일 변경 0)·토큰은 화면 마스킹(평문 0)·기존 mcpServers/타 키 보존.
    """
    path = claude_json_path or default_claude_json()
    if not url:
        return step("s10", INFO, "OpenCrab Expert URL 미제공 — 팩 자동생성 채널 미연결",
                    "Expert 가입:  https://opencrab.sh  (Expert 티어 → 내 전용 MCP URL 발급)\n"
                    "  발급 후:  %s onboard --opencrab-url <내 전용 URL>" % P.invocation_prefix())
    if not _OPENCRAB_URL_RE.match(url):
        return step("s10", STOP, "OpenCrab URL 형식 오류 — https://…opencrab.…/api/mcp/<token> 만 허용",
                    "입력값(마스킹): %s" % mask_opencrab_url(url))
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as ex:
            return step("s10", STOP, "%s 파싱 실패(%s) — 손상 우려로 미변경" % (path, type(ex).__name__),
                        "파일 확인/백업 후 재실행")
    if not isinstance(data, dict):
        return step("s10", STOP, "%s 최상위가 객체가 아님 — 미변경(안전)" % path)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    cur = servers.get(OPENCRAB_MCP_NAME)
    if isinstance(cur, dict) and cur.get("url") == url and cur.get("type") == "http":
        return step("s10", SKIP, "OpenCrab MCP 이미 등록됨: %s (%s)"
                    % (OPENCRAB_MCP_NAME, mask_opencrab_url(url)))
    if not apply:
        verb = "업데이트" if cur is not None else "등록"
        return step("s10", INFO, "OpenCrab MCP %s 대기 — --apply 시 %s 에 기입" % (verb, path),
                    "대상: mcpServers.%s = {type:http, url:%s}"
                    % (OPENCRAB_MCP_NAME, mask_opencrab_url(url)))
    bak = None
    if os.path.exists(path):
        bak = "%s.binggu-bak.%s" % (path, (now_fn or _utc_stamp)())
        shutil.copy2(path, bak)
    servers[OPENCRAB_MCP_NAME] = {"type": "http", "url": url}
    data["mcpServers"] = servers
    tmp = path + ".binggu-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 원자 교체(부분쓰기 노출 0)
    hint = "Claude Code/데스크톱 재시작 시 반영 — 이후 'X 팩 만들어줘' 로 스키마 팩 생성"
    if bak:
        hint += "\n  백업: %s" % bak
    return step("s10", OK, "OpenCrab MCP 등록 완료: %s (%s)"
                % (OPENCRAB_MCP_NAME, mask_opencrab_url(url)), hint)


def enforce_hooks_step(settings_path, apply=False, register_fn=None):
    """[s11] 강제 회상/학습/가드 hook 4개 등록 — 신규 사용자 AGI 구동 배선.

    register_hook(binggu_capture_profile) 재사용 · marker idempotent(owner·기존 사용자 재실행 skip) ·
    전부 SYNC(is_async=False — Stop exit 2 차단력·UserPromptSubmit 감지 확정). 다른 키
    (statusLine/permissions) 보존은 register_hook 이 json.loads→setdefault→write 로 보장(.bak 백업)."""
    hooks_dir = os.path.join(REPO, "hooks")
    specs = [
        ("user-prompt-enforce-recall", "user-prompt-enforce-recall.js", ("UserPromptSubmit",)),
        ("stop-enforce-recall", "stop-enforce-recall.js", ("Stop",)),
        # ★ 2026-08-08 신설 — 회상을 인출하고 **도장을 안 찍은 채** 턴을 끝내는 것을 막는다.
        #   도장은 사람이 아니라 **쓰는 순간의 AI** 가 찍는 것이 정본인데(CLAUDE.md §C-11-1 ·
        #   2026-07-27 owner 지시: 세션 끝 목록만 보는 사람보다 쓰는 순간의 AI 판정이 정확하다),
        #   실측 결과 그 예외를 받은 쪽이 이행하지 않았다 — 회상 1,454회 중 판정 109회(**7%**).
        #   도장이 랭킹(use_count)으로 이어지므로 안 찍으면 좋은 회상이 위로 못 올라온다.
        ("stop-enforce-recall-stamp", "stop-enforce-recall-stamp.js", ("Stop",)),
        ("user-prompt-learn-outcome", "user-prompt-learn-outcome.js", ("UserPromptSubmit",)),
        ("pre-enforce-guard", "pre-enforce-guard.js", ("PreToolUse",)),
    ]
    if not apply:
        return step("s11", INFO,
                    "강제 회상/학습/가드 hook(4) 등록 대기 — --apply 시 SYNC 등록: %s" % settings_path,
                    "UserPromptSubmit(enforce·learn) · Stop(enforce=recall 미이행 시 재답변 강제) · PreToolUse(guard)")
    try:
        from binggu_capture_profile import register_hook
        rf = register_fn or register_hook
        added = []
        for marker, fname, events in specs:
            cmd = 'node "%s"' % os.path.join(hooks_dir, fname)
            for ev in rf(settings_path, cmd, events=events, marker=marker, is_async=False):
                added.append("%s→%s" % (marker.rsplit("-", 1)[-1], ev))
    except Exception as e:
        return step("s11", STOP, "enforce hook 등록 실패: %s" % e, "settings.json .bak 확인 후 수동 등록")
    return step("s11", OK if added else SKIP,
                "강제 회상/학습/가드 hook: %s" % (", ".join(added) or "이미 등록됨(marker skip)"),
                "재시작 시 반영 — 결정/검토 답변 전 recall 미이행이면 Stop 이 재답변 강제 "
                "(끄기: ~/.claude/state/recall_enforce_disabled)")


def session_close_hook_step(settings_path, apply=False, register_fn=None, home=None):
    """[s12] 세션 마무리 저장 트리거 hook + close_phrases 기본 표현 등록(신규 사용자).

    "빙구팩 저장해" 등 마무리 발화 → 저장 preview 표(candidate·번호) 자동 주입 → owner 가 SAVE n
    직접 선택(저장 0·G4). register_hook 재사용·SYNC(표 주입 현 turn)·marker idempotent(재실행 skip)."""
    hook_cmd = 'py "%s"' % os.path.join(REPO, "hooks", "binggu_session_close_hook.py")
    if not apply:
        return step("s12", INFO,
                    "세션 마무리 저장 트리거 hook 등록 대기 — --apply 시 SYNC 등록: %s" % settings_path,
                    "UserPromptSubmit(session_close) + close_phrases.json 기본 마무리 표현")
    try:
        from binggu_capture_profile import register_hook
        from binggupack.review.session_close import register_close_phrase, register_close_suffix
        rf = register_fn or register_hook
        added = rf(settings_path, hook_cmd, events=("UserPromptSubmit",),
                   marker="binggu_session_close_hook", is_async=False)
        bh = home or os.environ.get("BINGGU_HOME") or os.path.join(os.path.expanduser("~"), ".binggupack")
        for ph in ("빙구팩 저장해", "운영 세션 마무리", "세션 마무리", "오늘 여기까지", "마무리하자"):
            register_close_phrase(ph, home=bh)
        # N3 접미(단일 종결어만) — 등록 표현 × 접미 유한폐포로 "…하자/해줘/요" 변형 흡수.
        #   ★부정계(안/못/말/마/않)는 seed 영구 금지 — "세션 마무리 안해" 의미반전 오발동(등록 정책·정규화 아님).
        for sfx in ("하자", "해", "해요", "해줘", "요", "입니다", "합시다"):
            register_close_suffix(sfx, home=bh)
    except Exception as e:
        return step("s12", STOP, "세션 마무리 hook 등록 실패: %s" % e, "settings.json .bak 확인 후 수동 등록")
    return step("s12", OK if added else SKIP,
                "세션 마무리 저장 트리거: %s" % (", ".join(added) or "이미 등록됨(marker skip)"),
                "재시작 후 '빙구팩 저장해' → 저장 preview 표(candidate·번호) 자동 · owner 가 SAVE n 직접 선택")


# ── 오케스트레이터 ─────────────────────────────────────────────────
def run_save_setup(apply=False, deploy=False, show_url=False, os_name=None, wp_dir=None,
                   login_runner=None, deploy_runner=None, secret_runner=None,
                   sched_runner=None, task_exists=None, keygen=None, toml_path=None,
                   webmcp=False, webmcp_runner=None, webmcp_task_exists=None,
                   cloudflared=None, pp_home=None, opencrab_url=None, claude_json_path=None,
                   settings_path=None, enforce_register_fn=None):
    """저장 채널 셋업 1회. 기본 dry-run(변경 0). 모든 runner 는 selftest 주입용."""
    os_name = os_name or P.detect_os()
    wp_dir = wp_dir or default_wp_dir()
    toml_path = toml_path or WRANGLER_SAVE
    steps = []

    # [s0] preflight — toml + wrangler 존재(read-only)
    if not os.path.exists(toml_path):
        steps.append(step("s0", STOP, "wrangler.save_mcp.prod.toml 없음: %s" % toml_path,
                          "wheel 설치본엔 hosted/ 미포함 — sdist(`pip download --no-binary :all: binggupack`) 또는 `git clone` 으로 받으세요(hosted/workers 소스 필요)"))
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "s0"}
    steps.append(step("s0", OK, "save_mcp toml 확인: %s" % os.path.basename(toml_path)))

    # [s1] login (read-only whoami — setup_cloud 정책 재사용)
    login_ok, s1 = SC.check_login(login_runner or SC._real_runner)
    steps.append(s1)
    if not login_ok:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "s1"}

    # [s2] 키 생성 (멱등 · repo 밖)
    s2, keys = ensure_save_keys(wp_dir, apply=apply, gen=keygen)
    steps.append(s2)

    # [s3] deploy (별도 GO)
    s3, url = deploy_save_step(deploy_runner or SC._real_runner, apply=apply, deploy=deploy)
    steps.append(s3)
    if s3["status"] == STOP:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "s3"}

    # [s4] secret put (--deploy 묶음)
    s4 = secrets_put_step(keys, secret_runner=secret_runner, apply=apply, deploy=deploy)
    steps.append(s4)
    if s4["status"] == STOP:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "s4"}

    # [s5] WORKER_URL upsert
    s5 = upsert_worker_url(wp_dir, url, apply=apply)
    steps.append(s5)

    # [s6] 커넥터 안내 (기본 마스킹)
    steps.append(connector_step(wp_dir, show_url=show_url))

    # [s7] auto-pull 스케줄러
    steps.append(autopull_scheduler_step(os_name, runner=sched_runner,
                                         task_exists=task_exists, apply=apply))

    # [s8] 웹 MCP — 기본 안내 · --webmcp 옵트인 시 등록
    steps.append(web_mcp_step(os_name=os_name, webmcp=webmcp, apply=apply,
                              runner=webmcp_runner, task_exists=webmcp_task_exists,
                              cloudflared=cloudflared))

    # [s9] 개인 팩 config(auto_create) — PACK_ID 사용자별 생성 진입점
    steps.append(person_pack_config_step(apply=apply, home=pp_home))

    # [s10] OpenCrab Expert MCP 연결(팩 자동생성 채널) — --opencrab-url 제공 시에만 등록
    s10 = register_opencrab_mcp(opencrab_url, apply=apply, claude_json_path=claude_json_path)
    steps.append(s10)
    if s10["status"] == STOP:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "s10"}

    # [s11] 강제 회상/학습/가드 hook 등록 — 신규 사용자 AGI 구동(안 따를 수 없는 recall 강제)
    _sp = settings_path or (P.default_settings() if hasattr(P, "default_settings")
                            else os.path.join(os.path.expanduser("~"), ".claude", "settings.json"))
    s11 = enforce_hooks_step(_sp, apply=apply, register_fn=enforce_register_fn)
    steps.append(s11)
    if s11["status"] == STOP:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "s11"}

    # [s12] 세션 마무리 저장 트리거 hook("빙구팩 저장해" → preview 표 자동 · owner SAVE n)
    s12 = session_close_hook_step(_sp, apply=apply, register_fn=enforce_register_fn)
    steps.append(s12)
    if s12["status"] == STOP:
        return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": "s12"}

    return {"apply": apply, "deploy": deploy, "steps": steps, "halted_at": None}


def render_report(result):
    mode = "APPLY" if result["apply"] else "DRY-RUN(점검만 · 변경 0)"
    if result["deploy"]:
        mode += " +DEPLOY"
    L = ["=" * 64, "빙구팩 저장 채널(save_mcp) 셋업 — %s" % mode, "=" * 64]
    for s in result["steps"]:
        L.append("%s [%s] %s" % (SC._TAG.get(s["status"], "[?]"), s["stage"], s["msg"]))
        if s.get("hint"):
            for line in s["hint"].splitlines():
                L.append("       %s" % line)
    L.append("-" * 64)
    if result["halted_at"] is not None:
        L.append("⛔ [%s] 단계에서 멈춤 — 위 안내대로 본인이 처리 후 다시 실행하세요." % result["halted_at"])
    elif not result["apply"]:
        L.append("다음: 실제 적용은  %s onboard --apply" % P.invocation_prefix())
        L.append("      (worker 배포까지: --apply --deploy / login·deploy 는 본인 행위)")
    else:
        L.append("완료 — ChatGPT 에서 저장하면 5분 내 auto-pull 이 로컬 장부에 반영합니다.")
    L.append("주체: wrangler login(브라우저 OAuth) · --deploy 결정은 본인 손. 시크릿 평문 출력 0.")
    return "\n".join(L)


# ── selftest (실 CF/스케줄러/실 키파일 미접촉 — mock + temp 만) ─────
def _selftest():
    import tempfile
    ok = 0
    tot = 0

    def chk(name, cond):
        nonlocal ok, tot
        tot += 1
        ok += 1 if cond else 0
        print(("  PASS " if cond else "  FAIL ") + name)

    FAKE = "aaaabbbbccccddddeeeeffff000011112222333344445555"  # 48자 모의 키(실키 아님)

    # 1~3. mask — 평문 미노출
    chk("1.mask 앞8+길이", mask(FAKE) == "aaaabbbb…(48자)")
    chk("2.mask 짧은값 길이만", mask("abc") == "…(3자)")
    chk("3.mask 에 전문 미포함", FAKE not in mask(FAKE))

    wp = tempfile.mkdtemp(prefix="setup_save_st_")

    # 4~5. dev_vars 왕복
    write_dev_vars(os.path.join(wp, VARS_NAME),
                   {"WORKER_URL": "https://x.workers.dev", "SAVE_PATH_TOKEN": FAKE})
    d = read_dev_vars(os.path.join(wp, VARS_NAME))
    chk("4.dev_vars 왕복", d["WORKER_URL"] == "https://x.workers.dev" and d["SAVE_PATH_TOKEN"] == FAKE)
    chk("5.부재 파일 → 빈 dict", read_dev_vars(os.path.join(wp, "no_file")) == {})

    # 6~9. ensure_save_keys — 생성/멱등/마스킹/dry-run
    wp2 = tempfile.mkdtemp(prefix="setup_save_k_")
    calls = [0]

    def fake_gen():
        calls[0] += 1
        return FAKE
    s, keys = ensure_save_keys(wp2, apply=True, gen=fake_gen)
    chk("6.키 생성 OK(2종 48자)", s["status"] == OK and calls[0] == 2
        and all(len(keys[k]) == 48 for k in KEY_FIELDS))
    s2, keys2 = ensure_save_keys(wp2, apply=True, gen=fake_gen)
    chk("7.재실행 SKIP + 값 불변(멱등)", s2["status"] == SKIP and calls[0] == 2
        and keys2["SAVE_PATH_TOKEN"] == FAKE)
    chk("8.step msg 마스킹(전문 0)", FAKE not in s["msg"] and FAKE not in s2["msg"])
    s3, k3 = ensure_save_keys(tempfile.mkdtemp(prefix="setup_save_d_"), apply=False)
    chk("9.dry-run → INFO + 파일 미생성", s3["status"] == INFO and k3 is None)

    # 10~11. parse_deploy_url
    OUT = "Uploaded x (1 sec)\n  https://binggupack-save-intent-mcp.example.workers.dev\nVersion: 1"
    chk("10.deploy URL 파싱", parse_deploy_url(OUT)
        == "https://binggupack-save-intent-mcp.example.workers.dev")
    chk("11.URL 없음 → None", parse_deploy_url("no url here") is None)

    # 12~14. deploy 게이트
    dep_calls = []

    def mock_deploy(args, cwd=None):
        dep_calls.append(args)
        return {"rc": 0, "stdout": OUT, "stderr": ""}
    s, url = deploy_save_step(mock_deploy, apply=True, deploy=False)
    chk("12.deploy 기본 SKIP + 미호출", s["status"] == SKIP and not dep_calls)
    s, url = deploy_save_step(mock_deploy, apply=True, deploy=True)
    chk("13.--deploy → OK + URL", s["status"] == OK and url and "workers.dev" in url)
    def mock_deploy_fail(args, cwd=None):
        return {"rc": 1, "stdout": "", "stderr": "boom"}
    chk("14.deploy 실패 → STOP", deploy_save_step(mock_deploy_fail, apply=True, deploy=True)[0]["status"] == STOP)

    # 15~18. secrets put — stdin 전달 + 게이트
    put_calls = []

    def mock_secret(args, cwd=None, input_text=None):
        put_calls.append((args[2], input_text))
        return {"rc": 0, "stdout": "ok", "stderr": ""}
    keys_d = {k: FAKE for k in KEY_FIELDS}
    s = secrets_put_step(keys_d, secret_runner=mock_secret, apply=True, deploy=True)
    chk("15.secret put 2종 + stdin 값 전달", s["status"] == OK and len(put_calls) == 2
        and all(v == FAKE for _n, v in put_calls))
    chk("16.put 대상 이름 정확", [n for n, _v in put_calls] == list(KEY_FIELDS))
    put_calls.clear()
    s = secrets_put_step(keys_d, secret_runner=mock_secret, apply=True, deploy=False)
    chk("17.--deploy 없음 → INFO + 미호출", s["status"] == INFO and not put_calls)
    chk("18.키 없음 → STOP", secrets_put_step(None, secret_runner=mock_secret,
                                              apply=True, deploy=True)["status"] == STOP)

    # 19~21. upsert_worker_url 멱등
    s = upsert_worker_url(wp2, "https://a.workers.dev", apply=True)
    chk("19.URL 기입 OK", s["status"] == OK
        and read_dev_vars(os.path.join(wp2, VARS_NAME))["WORKER_URL"] == "https://a.workers.dev")
    chk("20.동일 값 재기입 → SKIP", upsert_worker_url(wp2, "https://a.workers.dev", apply=True)["status"] == SKIP)
    chk("21.URL 없음 → SKIP", upsert_worker_url(wp2, None, apply=True)["status"] == SKIP)

    # 22~24. connector — 모든 경로에서 마스킹 / 미완성 안내
    s = connector_step(wp2, show_url=False)
    chk("22.커넥터 기본 마스킹(전문 0)", FAKE not in s["msg"] and "/mcp2/" in s["msg"])
    s = connector_step(wp2, show_url=True)
    chk("23.--show-url 도 전체 URL 출력 금지", FAKE not in s["msg"] and "폐기" in s["hint"])
    chk("24.키/URL 없으면 형식 안내", "형식" in connector_step(tempfile.mkdtemp(prefix="setup_save_e_"))["msg"])

    # 25~28. 스케줄러 분기
    chk("25.win 존재 → SKIP", autopull_scheduler_step("windows", task_exists=True, apply=True)["status"] == SKIP)
    sched = []

    def mock_ps1(p):
        sched.append(p)
        return {"rc": 0}
    chk("26.win 미존재+apply → 등록", autopull_scheduler_step(
        "windows", runner=mock_ps1, task_exists=False, apply=True)["status"] == OK and sched)
    chk("27.win dry-run → INFO", autopull_scheduler_step(
        "windows", task_exists=False, apply=False)["status"] == INFO)
    chk("28.mac → cron 안내", "crontab" in (autopull_scheduler_step("macos", apply=True)["hint"] or ""))

    # 29~31. 오케스트레이터 — dry-run 변경 0 / 미로그인 halt / report 시크릿 0
    def mock_login_ok(args, cwd=None):
        return {"rc": 0, "stdout": "logged in", "stderr": ""}

    def mock_login_no(args, cwd=None):
        return {"rc": 1, "stdout": "", "stderr": "not logged in"}
    wp3 = tempfile.mkdtemp(prefix="setup_save_o_")
    res = run_save_setup(apply=False, os_name="windows", wp_dir=wp3,
                         login_runner=mock_login_ok, deploy_runner=mock_deploy,
                         secret_runner=mock_secret, task_exists=False, keygen=fake_gen)
    chk("29.dry-run 변경 0(키파일 미생성)", res["halted_at"] is None
        and not os.path.exists(os.path.join(wp3, VARS_NAME)))
    res2 = run_save_setup(apply=True, os_name="windows", wp_dir=wp3,
                          login_runner=mock_login_no)
    chk("30.미로그인 → halt s1", res2["halted_at"] == "s1")
    res3 = run_save_setup(apply=True, deploy=True, os_name="windows",
                          wp_dir=tempfile.mkdtemp(prefix="setup_save_f_"),
                          login_runner=mock_login_ok, deploy_runner=mock_deploy,
                          secret_runner=mock_secret, sched_runner=mock_ps1,
                          task_exists=True, keygen=fake_gen,
                          pp_home=tempfile.mkdtemp(prefix="setup_save_pp_"))
    rep = render_report(res3)
    chk("31.full apply report 에 시크릿 전문 0(마스킹만)", FAKE not in rep and res3["halted_at"] is None)

    # 32. default_wp_dir — repo 밖(부모 디렉토리) 규약
    chk("32.기본 wp = repo 밖 workers_port",
        os.environ.get("BINGGU_WORKERS_PORT") is not None
        or os.path.basename(default_wp_dir()) == "workers_port"
        and os.path.dirname(default_wp_dir()) == os.path.dirname(REPO))

    # 33. capture flag write 0 (setup_cloud 케이스33 패턴 — 자기검출 회피 분리 조립)
    src = _read_text(os.path.abspath(__file__))
    bad = ["init_" + "profile(", "capture_" + "enabled =", "set_" + "capture"]
    chk("33.capture flag write 코드 0", not any(t in src for t in bad))

    # 34. 실 toml 존재(repo 정합 — 읽기만)
    chk("34.실 wrangler.save_mcp.prod.toml 존재", os.path.exists(WRANGLER_SAVE))

    # 35. [s8] 웹 MCP 기본 = INFO(변경 0) + 등록 스크립트 경로 포함
    s35 = web_mcp_step(webmcp=False)
    chk("35.웹 MCP 기본 = INFO + register_webmcp 경로",
        s35["status"] == INFO and "register_webmcp.ps1" in (s35["hint"] or ""))
    chk("36.run_save_setup 에 s8 포함", any(s["stage"] == "s8" for s in res3["steps"]))

    # 37~41. [s8] --webmcp 옵트인 분기(전부 mock — 실 스케줄러/ps1 0)
    chk("37.webmcp 기등록 → SKIP",
        web_mcp_step(os_name="windows", webmcp=True, task_exists=True)["status"] == SKIP)
    chk("38.webmcp cloudflared 없음 → STOP",
        web_mcp_step(os_name="windows", webmcp=True, task_exists=False,
                     cloudflared=False)["status"] == STOP)
    chk("39.webmcp dry-run → INFO(등록 대기)",
        web_mcp_step(os_name="windows", webmcp=True, apply=False, task_exists=False,
                     cloudflared=True)["status"] == INFO)
    chk("40.webmcp apply+rc0 → OK(등록 완료)",
        web_mcp_step(os_name="windows", webmcp=True, apply=True, task_exists=False,
                     cloudflared=True, runner=lambda p_: {"rc": 0})["status"] == OK)
    chk("41.webmcp apply+rc1 → STOP(실패 안내)",
        web_mcp_step(os_name="windows", webmcp=True, apply=True, task_exists=False,
                     cloudflared=True, runner=lambda p_: {"rc": 1})["status"] == STOP)
    chk("42.webmcp 비Windows → INFO(수동 안내)",
        web_mcp_step(os_name="mac", webmcp=True, apply=True)["status"] == INFO)

    # 43~47. [s9] 개인 팩 config(auto_create) — temp home 격리
    import tempfile as _tf
    _pph = _tf.mkdtemp(prefix="pp_home_")
    try:
        chk("43.s9 dry-run → INFO(생성 대기)",
            person_pack_config_step(apply=False, home=_pph)["status"] == INFO)
        s44 = person_pack_config_step(apply=True, home=_pph, username="테스터")
        cfgp = os.path.join(_pph, "person_pack.json")
        cfgj = _read_json(cfgp)
        chk("44.s9 apply → OK + auto_create config 생성",
            s44["status"] == OK and cfgj["auto_create"] is True and cfgj["pack_id"] == ""
            and "테스터" in cfgj["title"])
        chk("45.s9 재실행 → SKIP(멱등)",
            person_pack_config_step(apply=True, home=_pph)["status"] == SKIP)
        os.environ["BINGGU_PACK_ID"] = "x" * 36
        chk("46.s9 env 사용 중 → SKIP(owner/기존 사용자 회귀 0)",
            person_pack_config_step(apply=True, home=_pph)["status"] == SKIP)
        os.environ.pop("BINGGU_PACK_ID", None)
        chk("47.run_save_setup 에 s9 포함", any(s["stage"] == "s9" for s in res3["steps"]))
        _pph2 = _tf.mkdtemp(prefix="pp_home2_")
        try:
            with open(os.path.join(_pph2, "person_pack_last.json"), "w", encoding="utf-8") as f:
                f.write('{"pack_id":"기존"}')
            chk("48.s9 기존 sync 이력 → SKIP(운영 중 사용자 보호·apply 여도 마커 미생성)",
                person_pack_config_step(apply=True, home=_pph2)["status"] == SKIP
                and not os.path.exists(os.path.join(_pph2, "person_pack.json")))
        finally:
            _sh2 = __import__("shutil")
            _sh2.rmtree(_pph2, ignore_errors=True)

        # 49~55. [s10] OpenCrab MCP 등록 — temp claude.json fixture
        _cjd = tempfile.mkdtemp(prefix="claude_json_")
        _cj = os.path.join(_cjd, ".claude.json")
        with open(_cj, "w", encoding="utf-8") as _f:
            json.dump({"mcpServers": {"other": {"type": "stdio"}}, "keep": 1}, _f)
        _URL = "https://opencrab.sh/api/mcp/ocm_" + "a" * 24
        _URL2 = "https://opencrab.sh/api/mcp/ocm_" + "b" * 24
        try:
            chk("49.s10 url 없음 → INFO(미연결)",
                register_opencrab_mcp(None, apply=True, claude_json_path=_cj)["status"] == INFO)
            chk("50.s10 형식 오류 → STOP + 미변경",
                register_opencrab_mcp("https://evil.example/x", apply=True, claude_json_path=_cj)["status"] == STOP
                and "opencrab-cloud" not in _read_json(_cj)["mcpServers"])
            chk("51.s10 dry-run → INFO + 파일 변경 0",
                register_opencrab_mcp(_URL, apply=False, claude_json_path=_cj)["status"] == INFO
                and "opencrab-cloud" not in _read_json(_cj)["mcpServers"])
            r49 = register_opencrab_mcp(_URL, apply=True, claude_json_path=_cj)
            _cjj = _read_json(_cj)
            chk("52.s10 apply → OK + 등록 + 기존 서버/키 보존",
                r49["status"] == OK
                and _cjj["mcpServers"]["opencrab-cloud"] == {"type": "http", "url": _URL}
                and _cjj["mcpServers"].get("other") == {"type": "stdio"} and _cjj.get("keep") == 1)
            chk("53.s10 멱등 재등록 → SKIP",
                register_opencrab_mcp(_URL, apply=True, claude_json_path=_cj)["status"] == SKIP)
            r50 = register_opencrab_mcp(_URL2, apply=True, claude_json_path=_cj)
            chk("54.s10 URL 변경 → OK(업데이트) + 백업 생성",
                r50["status"] == OK
                and _read_json(_cj)["mcpServers"]["opencrab-cloud"]["url"] == _URL2
                and any(f.startswith(".claude.json.binggu-bak.") for f in os.listdir(_cjd)))
            chk("55.s10 토큰 평문 0(마스킹)",
                ("ocm_" + "a" * 24) not in register_opencrab_mcp(_URL, apply=False, claude_json_path=_cj)["msg"])
        finally:
            _sh3 = __import__("shutil")
            _sh3.rmtree(_cjd, ignore_errors=True)
    finally:
        os.environ.pop("BINGGU_PACK_ID", None)
        import shutil as _sh
        _sh.rmtree(_pph, ignore_errors=True)

    # s11. enforce hooks 등록 실경로(temp settings — 멀티키 소실 0·SYNC·idempotent · 회상 반영: 실수 방지)
    st_enf = os.path.join(wp, "settings_enforce.json")
    with open(st_enf, "w", encoding="utf-8") as _ef:
        json.dump({"statusLine": {"x": 1}, "permissions": {"allow": ["a"]}, "hooks": {}},
                  _ef, ensure_ascii=False)
    _er = enforce_hooks_step(st_enf, apply=True)
    _ed = _read_json(st_enf)
    chk("s11a enforce_hooks 등록 OK", _er["status"] == OK)
    chk("s11b 멀티키 소실 0(statusLine·permissions 보존)",
        _ed.get("statusLine") == {"x": 1} and _ed.get("permissions") == {"allow": ["a"]})

    def _ehas(groups, name):
        return any(name in (h.get("command") or "") for g in groups for h in g.get("hooks", []))

    def _esync(groups, name):
        for g in groups:
            for h in g.get("hooks", []):
                if name in (h.get("command") or ""):
                    return "async" not in h
        return False
    _eups = _ed["hooks"].get("UserPromptSubmit", [])
    _estops = _ed["hooks"].get("Stop", [])
    _epts = _ed["hooks"].get("PreToolUse", [])
    chk("s11c enforce-recall+learn(UP)+guard(PreToolUse) 등록",
        _ehas(_eups, "user-prompt-enforce-recall") and _ehas(_eups, "user-prompt-learn-outcome")
        and _ehas(_epts, "pre-enforce-guard"))
    chk("s11d stop-enforce SYNC(exit2 차단력·async 키 없음)",
        _ehas(_estops, "stop-enforce-recall") and _esync(_estops, "stop-enforce-recall"))
    # 도장 강제도 **반드시 SYNC** — async 면 exit 2 가 무시돼 차단력이 0 이다(회상 강제와 같은 함정).
    chk("s11d2 stop-enforce-recall-stamp SYNC(회상 인출 후 도장 누락 차단)",
        _ehas(_estops, "stop-enforce-recall-stamp") and _esync(_estops, "stop-enforce-recall-stamp"))
    _enb = len(_eups)
    _er2 = enforce_hooks_step(st_enf, apply=True)
    _ed2 = _read_json(st_enf)
    chk("s11e idempotent 재실행 SKIP+중복 0",
        _er2["status"] == SKIP and len(_ed2["hooks"].get("UserPromptSubmit", [])) == _enb)
    chk("s11f dry-run(apply=False) 무변경 INFO", enforce_hooks_step(st_enf, apply=False)["status"] == INFO)

    # s12. 세션 마무리 트리거 hook 등록 실경로(register + close_phrases · 멀티키 소실 0)
    st_sc = os.path.join(wp, "settings_sc.json")
    with open(st_sc, "w", encoding="utf-8") as _sf:
        json.dump({"statusLine": {"x": 1}, "hooks": {}}, _sf, ensure_ascii=False)
    hc = os.path.join(wp, "sc_home")
    os.makedirs(hc, exist_ok=True)
    _scr = session_close_hook_step(st_sc, apply=True, home=hc)
    _scd = _read_json(st_sc)
    chk("s12a session_close hook 등록 OK", _scr["status"] == OK and any(
        "binggu_session_close_hook" in (h.get("command") or "")
        for g in _scd["hooks"].get("UserPromptSubmit", []) for h in g["hooks"]))
    _cp = _read_json(os.path.join(hc, "close_phrases.json"))
    chk("s12b close_phrases 기본 표현('빙구팩 저장해') 등록", "빙구팩 저장해" in _cp.get("phrases", []))
    chk("s12b2 close 접미(suffixes '하자') seed · 부정계 없음", "하자" in _cp.get("suffixes", [])
        and not any(neg in _cp.get("suffixes", []) for neg in ("안", "못", "말", "마", "않")))
    chk("s12c 멀티키 소실 0(statusLine 보존)", _scd.get("statusLine") == {"x": 1})
    chk("s12d SYNC(async 키 없음 · 표 주입 현 turn)", all(
        "async" not in h for g in _scd["hooks"].get("UserPromptSubmit", []) for h in g["hooks"]
        if "binggu_session_close_hook" in (h.get("command") or "")))
    chk("s12e dry-run(apply=False) 무변경 INFO", session_close_hook_step(st_sc, apply=False)["status"] == INFO)

    print("\n" + "=" * 62)
    print("binggu_setup_save — selftest (mock + temp · 실 CF/스케줄러 미접촉)")
    print("=" * 62)
    print("RESULT: %d/%d %s" % (ok, tot, "PASS" if ok == tot else "FAIL"))
    print("GATE: %s" % ("GO" if ok == tot else "NO-GO"))
    return ok == tot


def main(argv=None):
    p = argparse.ArgumentParser(prog="binggu_setup_save",
                                description="저장 채널(save_mcp) 셋업 — 멱등·실패정지·dry-run 기본")
    p.add_argument("--apply", action="store_true", help="실제 변경(키 생성/스케줄러)")
    p.add_argument("--deploy", action="store_true", help="(--apply 와) deploy+secret put — 비가역")
    p.add_argument("--show-url", action="store_true", help="호환용(보안상 전체 URL은 출력하지 않음)")
    p.add_argument("--webmcp", action="store_true",
                   help="웹 MCP 로그온 자동가동 등록 옵트인(공개 터널=본인 결정 · --apply 와 함께)")
    p.add_argument("--opencrab-url", default=None,
                   help="OpenCrab Expert 전용 MCP URL 을 Claude 설정에 등록(팩 자동생성 채널)")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args(argv)
    if a.selftest:
        return 0 if _selftest() else 1
    res = run_save_setup(apply=a.apply, deploy=a.deploy, show_url=a.show_url, webmcp=a.webmcp,
                         opencrab_url=a.opencrab_url)
    print(render_report(res))
    return 0 if res["halted_at"] is None else 2


if __name__ == "__main__":
    sys.exit(main())
