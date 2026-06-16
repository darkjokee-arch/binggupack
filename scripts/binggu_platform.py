# -*- coding: utf-8 -*-
"""BingguPack cross-platform 경로/플랫폼 helper (v1.5.0 — 정책 단일 원천).

지원 정책(README/INSTALL/docs/BINGGUPACK_CROSS_PLATFORM_SUPPORT.md 와 동일):
  1. 기본 = OS별 로컬 홈:
       Windows  %USERPROFILE%\\.binggupack
       WSL      ~/.binggupack
       macOS    ~/.binggupack
  2. 같은 장부 공유는 자동 추측 금지 — BINGGU_HOME 명시 opt-in 만.
  3. BINGGU_HOME 이 있으면 모든 ledger/capture/publish 경로는 그 아래를 쓴다.
  4. OS 간 동일 ledger 공유는 가능하나 동시 실행 금지 — lock 충돌은 fail-closed.
  5. Windows/WSL/macOS 경로 변환은 표시/안내용만 — 자동 마이그레이션 0.
  6. python 런처: Windows=py, WSL/macOS/Linux=python3 (안내용).

설계 원칙:
  - 순수 함수 — os_name/home/env 를 주입할 수 있어 WSL·macOS 미보유 머신에서도
    synthetic(가짜 입력)으로 정책을 검증할 수 있다.
  - 부수효과 0 — 파일 write 0. detect_os() 의 /proc 읽기만 best-effort(없으면 무시).
  - 기존 Windows 동작 보존 — BINGGU_HOME 미설정 + 현재 OS 입력이면
    기존 `os.path.expanduser("~")/.binggupack` 와 동일 경로를 돌려준다.
"""
import ntpath
import os
import posixpath
import sys

BINGGU_DIRNAME = ".binggupack"
LEDGER_NAME = "ledger.sqlite"
# StagingDB 와 동일 — OS 간 공유 장부 lock 경합 시 이 시간(ms) 만큼만 대기 후 fail-closed.
LEDGER_BUSY_TIMEOUT_MS = 5000


def detect_os(platform_name=None, wsl_distro=None, osrelease=None):
    """'windows' | 'wsl' | 'macos' | 'linux'.

    인자를 주면 그 값으로 판정(synthetic 검증). 미지정 시 실 환경에서 추론.
    """
    plat = (platform_name if platform_name is not None else sys.platform).lower()
    if plat.startswith("win") or plat == "cygwin":
        return "windows"
    if plat == "darwin":
        return "macos"
    # linux 계열 — WSL 판정(WSL_DISTRO_NAME 또는 /proc/sys/kernel/osrelease 의 microsoft 표식)
    distro = wsl_distro if wsl_distro is not None else os.environ.get("WSL_DISTRO_NAME", "")
    rel = osrelease
    if rel is None:
        try:
            with open("/proc/sys/kernel/osrelease", "r", encoding="utf-8", errors="ignore") as f:
                rel = f.read()
        except OSError:
            rel = ""
    rel = (rel or "").lower()
    if distro or "microsoft" in rel or "wsl" in rel:
        return "wsl"
    return "linux"


def _joiner(os_name):
    """OS별 경로 조립기 — synthetic 경로(WSL/macOS)를 Windows 머신에서 조립해도 정확."""
    return ntpath if os_name == "windows" else posixpath


def default_home_dir(os_name=None, env=None):
    """BINGGU_HOME 을 무시한 'OS별 기본 홈 디렉터리'(~/ 또는 %USERPROFILE%).

    표시·기본값 계산 전용. synthetic 검증 시 env 로 USERPROFILE/HOME 을 주입한다.
    """
    env = env if env is not None else os.environ
    name = os_name or detect_os()
    if name == "windows":
        return env.get("USERPROFILE") or env.get("HOME") or os.path.expanduser("~")
    return env.get("HOME") or os.path.expanduser("~")


def binggu_home(env=None, os_name=None):
    """장부 루트. BINGGU_HOME 우선(opt-in), 없으면 OS별 홈/.binggupack.

    자동 마이그레이션 0 — 경로 계산만 한다(파일 이동/생성 없음).
    """
    env = env if env is not None else os.environ
    explicit = env.get("BINGGU_HOME")
    if explicit:
        return explicit
    name = os_name or detect_os()
    return _joiner(name).join(default_home_dir(os_name=name, env=env), BINGGU_DIRNAME)


def default_ledger(env=None, os_name=None):
    """기본 장부 sqlite 경로 = <binggu_home>/ledger.sqlite (BINGGU_HOME 우선)."""
    name = os_name or detect_os()
    return _joiner(name).join(binggu_home(env=env, os_name=name), LEDGER_NAME)


def default_settings(env=None, os_name=None):
    """Claude hook 등록 대상 settings.json = <홈>/.claude/settings.json (OS별 세션 기준).

    BINGGU_HOME 과 무관 — hook/세션은 각 OS 의 실제 홈을 따른다(공유 장부여도 세션은 로컬).
    """
    name = os_name or detect_os()
    j = _joiner(name)
    return j.join(default_home_dir(os_name=name, env=env), ".claude", "settings.json")


def python_cmd(os_name=None):
    """문서/안내용 권장 파이썬 런처. Windows=py, WSL/macOS/Linux=python3."""
    return "py" if (os_name or detect_os()) == "windows" else "python3"


def resolve_npx(os_name=None):
    """npx 실행파일을 PATH 에서 실제로 해결한다(외부 명령 호출 정책 단일원천).

    Windows 의 npx 는 `npx.cmd` 라, shell=False 인 subprocess 는 PATHEXT 를
    적용하지 못해 "npx" 이름만으론 WinError 2(파일 못 찾음)가 난다.
    shutil.which 로 실제 실행파일(npx.cmd/npx)을 찾고, 못 찾으면 OS별 이름으로 폴백.
    (autopush 스케줄러 회귀에서 드러난 함정 — wrangler 류 외부 호출은 전부 이걸 경유.)
    """
    import shutil
    name = os_name or detect_os()
    return shutil.which("npx") or ("npx.cmd" if name == "windows" else "npx")


def shared_opt_in(env=None):
    """OS 간 같은 장부 공유(BINGGU_HOME 명시)가 켜져 있는가 — 자동 추측 아님."""
    env = env if env is not None else os.environ
    return bool(env.get("BINGGU_HOME"))


# ---------------- 경로 표시/안내 변환 (자동 마이그레이션 절대 금지 — 문자열만) ----------------

def to_wsl_path(win_path):
    r"""C:\\Users\\PC\\.binggupack → /mnt/c/Users/PC/.binggupack (표시/안내용만)."""
    p = ntpath.splitdrive(win_path)
    drive, rest = p[0], p[1]
    if drive and drive.endswith(":"):
        letter = drive[0].lower()
        rest = rest.replace("\\", "/").lstrip("/")
        return "/mnt/%s/%s" % (letter, rest)
    return win_path.replace("\\", "/")


def from_wsl_path(wsl_path):
    r"""/mnt/c/Users/PC/.binggupack → C:\\Users\\PC\\.binggupack (표시/안내용만)."""
    parts = [p for p in wsl_path.split("/") if p != ""]
    if len(parts) >= 2 and parts[0] == "mnt" and len(parts[1]) == 1:
        drive = parts[1].upper() + ":"
        tail = parts[2:]
        return ntpath.join(drive + "\\", *tail) if tail else drive + "\\"
    return wsl_path


def display_path(path, target_os=None):
    """경로를 target_os 표기로 '표시'만 변환(파일은 건드리지 않음).

    target_os 미지정이면 현재 OS 표기 그대로. Windows↔WSL 만 변환 의미가 있다
    (macOS/Linux 는 동일 POSIX 표기).
    """
    name = target_os or detect_os()
    looks_windows = bool(ntpath.splitdrive(path)[0]) or "\\" in path
    if name == "wsl" and looks_windows:
        return to_wsl_path(path)
    if name == "windows" and path.startswith("/mnt/"):
        return from_wsl_path(path)
    return path


# ---------------- SQLite lock / 동시 접근 fail-closed (cross-platform) ----------------

def lock_path_for(ledger_path):
    """StagingDB.write_lock 과 동일 규칙의 O_EXCL lock 파일 경로."""
    return ledger_path + ".lock"


def lock_conflict_message(ledger_path):
    """OS 간 동시 실행으로 lock 경합 시 사용자에게 보여줄 안내(fail-closed)."""
    return (
        "장부가 다른 실행에서 사용 중입니다(lock): %s\n"
        "OS 간 같은 장부(BINGGU_HOME 공유)는 동시 실행이 금지됩니다 — "
        "다른 쪽(다른 OS/터미널)을 끝낸 뒤 다시 시도하세요." % lock_path_for(ledger_path)
    )


def apply_ledger_pragmas(con, busy_timeout_ms=LEDGER_BUSY_TIMEOUT_MS):
    """ledger sqlite 연결에 WAL + busy_timeout 적용(동시 접근 fail-closed 일관).

    busy_timeout 초과 시 sqlite3 가 OperationalError('database is locked') 를 던진다 —
    자동 재시도/우회 없음(fail-closed). 호출자는 그대로 표면화해야 한다.
    """
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=%d" % int(busy_timeout_ms))
    return con


def platform_summary(env=None, os_name=None):
    """doctor/안내 출력용 1-회 요약(read-only)."""
    name = os_name or detect_os()
    return {
        "os": name,
        "python_cmd": python_cmd(name),
        "binggu_home": binggu_home(env=env, os_name=name),
        "ledger": default_ledger(env=env, os_name=name),
        "settings": default_settings(env=env, os_name=name),
        "shared_opt_in": shared_opt_in(env),
        "busy_timeout_ms": LEDGER_BUSY_TIMEOUT_MS,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(platform_summary(), ensure_ascii=False, indent=2))
