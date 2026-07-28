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


def _note(stage, exc):
    """예외 삼킴 금지 — 계약(stdout 침묵 · 항상 exit 0) 불변인 채 사유 1줄만 stderr 로 남긴다.

    ★왜(2026-07-28 CodeQL py/empty-except 수리): `except: pass` 는 배선 끊김(import 누락 등)을
    조용히 삼켜 hook 이 '동작 중'인 채 아무 일도 안 하게 만든다 — 2026-07 무증상 실사고 2건의
    공통 원인. 예외를 밖으로 던지지 않으므로 세션은 절대 죽지 않고, 사유만 디버그 채널에 남는다.
    본문은 예외 '타입'만 — 예외 메시지엔 경로·프롬프트 원문이 섞일 수 있어 미출력(유출 0).
    반환: 사유 문자열(호출부·셀프테스트 대조용). stderr 사용 불가 환경에서도 예외 없음."""
    reason = "%s: %s" % (stage, type(exc).__name__)
    try:
        line = "[binggu_capture_hook] %s\n" % reason
        buf = getattr(sys.stderr, "buffer", None)
        if buf is not None:   # cp949 콘솔에서도 UnicodeEncodeError 0(bytes 직결)
            buf.write(line.encode("utf-8", "replace"))
            buf.flush()
        else:
            sys.stderr.write(line)
    except Exception:
        return reason         # stderr 부재/닫힘 — 반환값으로만 사유 유지
    return reason


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


def _prev_assistant_text(transcript_path, cap=1500, tail=400):
    """transcript(.jsonl)에서 직전 assistant turn의 마지막 text 추출.
    classify 의 '직전이 AI 제안이면 약한 교정 보류'(무상태 1턴) 게이트에만 쓰인다 —
    owner 의 dialectic 반응(AI 말에 대한 질문·반박)을 단순질문 veto 에서 살리는 맥락.
    prev_turn 은 판정 보조일 뿐 candidate.text 로 저장되지 않음(원문 write 0 불변 유지).
    learn-outcome.js scanRecall 의 assistant text 추출과 동일 규약(교환 축 · 2026-07-13)."""
    try:
        if not transcript_path or not os.path.exists(transcript_path):
            return None
        raw = Path(transcript_path).read_text(encoding="utf-8").split("\n")
    except Exception:
        return None
    last_ai = None
    for line in [ln for ln in raw if ln.strip()][-tail:]:
        try:
            o = json.loads(line)
        except Exception:
            continue
        if (o.get("type") or o.get("role")) != "assistant":
            continue
        c = (o.get("message") or o).get("content")
        if not isinstance(c, list):
            continue
        t = "".join(x.get("text", "") for x in c
                    if isinstance(x, dict) and x.get("type") == "text").strip()
        if t:
            last_ai = t[:cap]  # 창 안 마지막 assistant text 유지(직전 turn)
    return last_ai


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
            except Exception as e:
                # 상태파일 write 실패는 수집 자체를 막지 않는다(계속) — 다만 삼키지 않고 사유를 남긴다.
                _note("Stop preview 상태파일 write 실패", e)
        else:  # UserPromptSubmit
            # ★A(2026-07-21 owner "대화가 버려진다"): 직전 AI 응답을 prev_turn 으로 넘겨
            #   classify 의 dialectic 게이트(AI 제안 → owner 질문/반박이면 단순질문 veto 해제)를
            #   깨운다. 지금까진 hook 이 prev_turn 을 안 넘겨 이 게이트가 상시 죽어 있었음
            #   (buffer/persist.feed 는 이미 prev_turn 파라미터 보유 — hook 만 미배선).
            prev = _prev_assistant_text(data.get("transcript_path"))
            buf.feed(data.get("prompt", ""), cwd,
                     prev_turn=prev, session_id=data.get("session_id"))
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
            # encoding 명시: hook 의 사유 1줄(stderr)은 utf-8 bytes 직결이라 cp949 콘솔에서도
            # 부모가 안전하게 디코드해야 한다(미명시 시 locale 디코드 → UnicodeDecodeError 위험).
            return subprocess.run(
                [sys.executable, self_path],
                input=(raw if raw is not None else json.dumps(payload)),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=base_env)

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

        # T9 prev_turn 배선: 직전 AI 제안 → owner 질문이 dialectic 으로 살아남(단순질문 veto 해제)
        tr = tmp / "tr.jsonl"
        tr.write_text(json.dumps(
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "B안을 추천합니다"}]}}) + "\n", encoding="utf-8")
        before = size()
        call({"hook_event_name": "UserPromptSubmit", "prompt": "그게 왜 나아?",
              "cwd": repo_cwd, "transcript_path": str(tr)})
        check(size() == before + 1, "T9 prev_turn(AI 제안) 배선 → owner 질문 dialectic 수집(+1)")

        # T10 transcript 없음 → 같은 질문이 단순질문 veto(기존 동작 회귀 보존)
        before2 = size()
        call({"hook_event_name": "UserPromptSubmit", "prompt": "그게 왜 나아?", "cwd": repo_cwd})
        check(size() == before2, "T10 transcript 없음 → 단순질문 veto(prev_turn 미배선 회귀 보존)")

        # T11 candidate 에 prev_turn(AI 원문) 미저장 확인 — text 는 owner 발화만
        #   위에서 from-import 한 PersistentCaptureBuffer 재사용 — 같은 모듈을 import/from-import
        #   두 형태로 이중 로드하지 않는다(CodeQL py/import-and-import-from · 2026-07-28).
        items = PersistentCaptureBuffer(home=home).render_preview().get("items", [])
        texts = " ".join(it.get("text", "") for it in items)
        check("B안을 추천합니다" not in texts, "T11 prev_turn(AI 원문) candidate 미저장(write 0 불변)")

        # T12 상태파일 write 실패 → 예외 삼킴 0(2026-07-28 CodeQL py/empty-except 수리 증명).
        #   파일 자리에 디렉터리를 두어 write_text 를 강제 실패시킨다. 계약(exit 0 · stdout 침묵)은
        #   그대로, 사유만 stderr 1줄. 종전 `except: pass` 였다면 완전 무증상이었을 자리.
        lp = home / "capture_last_preview.json"
        lp.unlink()
        lp.mkdir()
        r = call({"hook_event_name": "Stop"})
        check(r.returncode == 0 and r.stdout.strip() == ""
              and "[binggu_capture_hook]" in (r.stderr or "")
              and "write 실패" in (r.stderr or ""),
              "T12 상태파일 write 실패 → exit 0 · stdout 침묵 · stderr 사유 1줄(삼킴 0)")

        print(f"\nGATE={'GO' if ok else 'NO-GO'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main())
