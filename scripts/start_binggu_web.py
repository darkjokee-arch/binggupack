# -*- coding: utf-8 -*-
"""start_binggu_web.py — 빙구팩 로컬 MCP를 웹/앱 커넥터에 노출(사용자 무관 일반화판).

HTTP 서버(24도구, 경로키 인증) + cloudflared quick tunnel 을 창 없이 상시 가동하고,
발급된 공개 주소(<주소>/mcp/<경로키>)를 <home>/mcp_web_url.txt 에 기록한다.
quick tunnel 은 재부팅 시 주소가 바뀐다(그때 파일 재확인·커넥터 갱신). 도메인을
Cloudflare 에 붙이면 named tunnel 로 고정 가능(수동).

사용자 무관 규약:
  - 파이썬 = 이 프로세스의 sys.executable (BINGGU_PYTHON env 우선)
  - repo = 이 파일의 상위 디렉토리 (scripts/ 의 부모)
  - home = BINGGU_HOME env > ~/.binggupack
  - cloudflared = <home>/bin/cloudflared(.exe) > PATH (없으면 안내 후 종료 — 자동 다운로드 0)
  - 경로 토큰 = <home>/mcp_http_token (없으면 최초 1회 자동 생성 · 화면 출력 0)
  - 포트 = BINGGU_WEB_PORT env > 8790

스케줄러 등록은 scripts/register_webmcp.ps1 (본인 직접 실행 — 공개 터널 노출은 사람 결정).
"""
import os
import re
import shutil
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.environ.get("BINGGU_PYTHON") or sys.executable or "python"
HOME_DIR = os.environ.get("BINGGU_HOME") or os.path.join(os.path.expanduser("~"), ".binggupack")
TOKFILE = os.path.join(HOME_DIR, "mcp_http_token")
URLFILE = os.path.join(HOME_DIR, "mcp_web_url.txt")
LOGFILE = os.path.join(HOME_DIR, "binggu_web.log")
PORT = os.environ.get("BINGGU_WEB_PORT") or "8790"
# Windows 자식 프로세스 콘솔창 숨김(무인 가동 창 0) — posix 는 0(무해)
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def log(msg):
    os.makedirs(HOME_DIR, exist_ok=True)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def find_cloudflared():
    """<home>/bin 우선(설치 안내 위치) → PATH. 없으면 None — 자동 다운로드 안 함."""
    exe = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    local = os.path.join(HOME_DIR, "bin", exe)
    if os.path.exists(local):
        return local
    return shutil.which("cloudflared")


def named_config():
    """named tunnel 설정(<home>/web_tunnel.json) 감지 — 있으면 (tunnel_name, hostname),
    없으면 None(quick 폴백). named = 고정 주소(재부팅에도 불변), quick = 가변 주소."""
    p = os.path.join(HOME_DIR, "web_tunnel.json")
    if not os.path.exists(p):
        return None
    try:
        import json
        d = json.loads(open(p, encoding="utf-8").read())
        if d.get("tunnel_name") and d.get("hostname"):
            return d["tunnel_name"], d["hostname"]
    except Exception:
        pass
    return None


def ensure_token():
    """경로 토큰 — 없으면 최초 1회 생성(48hex). 값은 파일에만(화면/로그 출력 0)."""
    if os.path.exists(TOKFILE):
        return open(TOKFILE, encoding="ascii").read().strip()
    import secrets
    tok = secrets.token_hex(24)
    os.makedirs(HOME_DIR, exist_ok=True)
    with open(TOKFILE, "w", encoding="ascii") as f:
        f.write(tok)
    log("[token] mcp_http_token 최초 생성")
    return tok


def main():
    cf = find_cloudflared()
    if not cf:
        log("[error] cloudflared 없음 — %s/bin 또는 PATH 에 설치하세요 "
            "(https://developers.cloudflare.com/cloudflared/)" % HOME_DIR)
        sys.exit(1)
    tok = ensure_token()
    env = dict(os.environ, BINGGU_MCP_PATH_TOKEN=tok)

    # 1) HTTP 서버 (127.0.0.1:PORT, 창 없이) — 외부 노출은 터널만
    http = subprocess.Popen(
        [PY, os.path.join(REPO, "scripts", "openbinggu_mcp_server.py"), "--http", PORT, REPO],
        env=env, creationflags=CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2)

    # 2) 터널 — named(고정 주소·web_tunnel.json) 우선, 없으면 quick(가변 주소)
    named = named_config()
    if named:
        tname, host = named
        # named tunnel run — ~/.cloudflared/config.yml(tunnel+ingress) 사용. 주소 고정(mcp.<도메인>).
        proc = subprocess.Popen(
            [cf, "tunnel", "run", tname],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW, text=True, encoding="utf-8", errors="replace",
        )
        url = "https://" + host   # 고정 — stdout 파싱 불필요
        with open(URLFILE, "w", encoding="ascii") as f:
            f.write(url + "/mcp/" + tok)
        log("[start-named] http pid=%s cf pid=%s host=%s (고정 주소·URLFILE 기록·재부팅 불변)"
            % (http.pid, proc.pid, host))
    else:
        # quick tunnel (창 없이) — 출력에서 가변 공개 URL 파싱
        proc = subprocess.Popen(
            [cf, "tunnel", "--url", "http://127.0.0.1:" + PORT],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW, text=True, encoding="utf-8", errors="replace",
        )
        log("[start] http pid=%s cf pid=%s port=%s" % (http.pid, proc.pid, PORT))

        url = None
        for _ in range(120):
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.5)
                continue
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                url = m.group(0)
                with open(URLFILE, "w", encoding="ascii") as f:
                    f.write(url + "/mcp/" + tok)
                log("[url] 기록됨 → %s (전체 주소는 파일 참조 — 로그에 토큰 0)" % URLFILE)
                break

        if not url:
            log("[error] tunnel url not found")
            sys.exit(1)

    # 3) 상주 — cloudflared 가 죽으면 종료(스케줄러가 재기동)
    proc.wait()
    log("[exit] cloudflared ended")


if __name__ == "__main__":
    main()
