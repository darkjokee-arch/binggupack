#!/usr/bin/env python
"""BingguPack 자동 후보 수집 hook (opt-in) — UserPromptSubmit / Stop.

배포물입니다. 설치하지 않으면 아무 동작도 하지 않습니다.
설치/활성화 가이드: docs/BINGGUPACK_CAPTURE_HOOK_SETUP.md

이 hook이 하는 일은 "자동 후보 수집"뿐 — **저장이 아닙니다.**
저장은 사용자가 preview를 보고 `SAVE n`(정확한 confirm)을 타이핑했을 때만 기존
게이트(`save_selected`)로 진행합니다. hook은 candidate 버퍼에 발췌만 쌓습니다.

안전 불변 (전부 --selftest 로 증명):
  - 기본 OFF: ~/.binggupack/capture_enabled 플래그가 없으면 즉시 종료(타 세션 무부담, import 전 차단)
  - scope 게이트: capture_scope.json 화이트리스트(fail-closed) + deny 우선 → example-project 등 타 repo 제외
  - candidate-only: ledger / active / confirmed write 0, 원문 전문 미저장(발췌 cap)
  - stdout 침묵 + 모든 예외 흡수(항상 exit 0) → 어떤 경우에도 세션 방해 / 원문 출력 0
"""
import json
import os
import sys
from pathlib import Path


def _scripts_dir():
    """binggu_capture_persist 가 있는 scripts/ 경로.
    1) BINGGU_SCRIPTS env 우선  2) 이 파일이 <repo>/hooks 에 있을 때 ../scripts."""
    env = os.environ.get("BINGGU_SCRIPTS")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "scripts"


def _home():
    env = os.environ.get("BINGGU_HOME")
    return Path(env) if env else (Path.home() / ".binggupack")


def _run(data):
    # 1) 기본 OFF 빠른 차단 (import 전 — 플래그 없으면 타 세션에 부담 0)
    try:
        if not (_home() / "capture_enabled").exists():
            return
    except Exception:
        return
    # 2) 플래그 ON 일 때만 영속 모듈 로드
    try:
        sd = str(_scripts_dir())
        if sd not in sys.path:
            sys.path.insert(0, sd)
        from binggu_capture_persist import PersistentCaptureBuffer
    except Exception:
        return
    # 3) 이벤트 처리 (scope 게이트는 PersistentCaptureBuffer.feed 내부에서 강제)
    try:
        cwd = data.get("cwd") or os.getcwd()
        event = data.get("hook_event_name") or ""
        buf = PersistentCaptureBuffer()
        if event == "Stop":
            pv = buf.render_preview()
            # 세션말: 건수 + 대화 덩어리 veto 건수만 상태 파일에 기록(원문/발췌 stdout 출력 0).
            #   bulk_vetoed = 긴 발화 제외 건수(무음 폐기 방지 — owner 가 명시저장으로 회수 인지).
            try:
                (_home() / "capture_last_preview.json").write_text(
                    json.dumps({"count": pv["count"], "bulk_vetoed": pv.get("bulk_vetoed", 0)},
                               ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
        else:  # UserPromptSubmit
            buf.feed(data.get("prompt", ""), cwd, session_id=data.get("session_id"))
    except Exception:
        return


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0  # stdin 파싱 실패 = 조용히 통과
    _run(data)
    return 0  # 항상 0 · stdout 침묵


# ---------------- 셀프테스트 (subprocess end-to-end, temp home 전용) ----------------
def _selftest():
    import shutil
    import subprocess
    import tempfile

    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    tmp = Path(tempfile.mkdtemp(prefix="bgp_capture_hook_"))
    try:
        home = tmp / ".binggupack"
        home.mkdir(parents=True)
        scripts = str(_scripts_dir())
        self_path = str(Path(__file__).resolve())
        base_env = {**os.environ, "BINGGU_HOME": str(home),
                    "BINGGU_SCRIPTS": scripts, "PYTHONUTF8": "1"}

        def call(payload, raw=None):
            return subprocess.run(
                [sys.executable, self_path],
                input=(raw if raw is not None else json.dumps(payload)),
                capture_output=True, text=True, env=base_env)

        repo_cwd = "C:/Users/fixture-user/binggupack"
        other_cwd = "C:/Users/fixture-user/example-org/example-project"

        sys.path.insert(0, scripts)
        from binggu_capture_persist import PersistentCaptureBuffer
        size = lambda: PersistentCaptureBuffer(home=home).size

        # T1 기본 OFF → 수집 0 · stdout 침묵
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": "이거 저장해", "cwd": repo_cwd})
        check(r.returncode == 0 and r.stdout.strip() == "" and size() == 0,
              "T1 기본 OFF(플래그 없음) → 수집 0 · stdout 침묵")

        # 활성화: 플래그 + scope
        (home / "capture_enabled").write_text("1", encoding="utf-8")
        (home / "capture_scope.json").write_text(json.dumps({
            "allowed_cwd_prefixes": ["C:/Users/fixture-user/binggupack"],
            "denied_cwd_substrings": ["example-project", "example-org"],
        }, ensure_ascii=False), encoding="utf-8")

        # T2 발화 단위 누적
        call({"hook_event_name": "UserPromptSubmit", "prompt": "이거 저장해", "cwd": repo_cwd})
        call({"hook_event_name": "UserPromptSubmit", "prompt": "B안으로 결정한다", "cwd": repo_cwd})
        check(size() == 2, "T2 발화 단위 누적(size=2)")

        # T3 example-project 세션 → 제외
        call({"hook_event_name": "UserPromptSubmit", "prompt": "결정했다", "cwd": other_cwd})
        check(size() == 2, "T3 example-project 세션 발화 제외(size 불변=2)")

        # T4 ignored 발화 → 수집 0
        call({"hook_event_name": "UserPromptSubmit", "prompt": "ㅋㅋ 웃기네", "cwd": repo_cwd})
        check(size() == 2, "T4 ignored 발화 수집 0")

        # T5 Stop preview → 건수 상태파일 · stdout 침묵
        r = call({"hook_event_name": "Stop"})
        prev = json.loads((home / "capture_last_preview.json").read_text(encoding="utf-8"))
        check(r.stdout.strip() == "" and prev["count"] == 2,
              "T5 Stop preview(count=2) · stdout 침묵")

        # T6/T7 stdin 깨짐·빈 입력 방어
        check(call(None, raw="{ not json").returncode == 0, "T6 깨진 stdin → exit 0")
        check(call(None, raw="").returncode == 0, "T7 빈 stdin → exit 0")

        # T8 candidate-only: ledger 미생성(write 0)
        check(not (home / "ledger.sqlite").exists(), "T8 ledger.sqlite 미생성(write 0)")

        print(f"\nGATE={'GO' if ok else 'NO-GO'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main())
