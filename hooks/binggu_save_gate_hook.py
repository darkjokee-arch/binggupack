#!/usr/bin/env python
"""BingguPack 사람-발화 저장 게이트 hook — UserPromptSubmit (sync).

사장님이 'SAVE n'(정확형)을 키보드로 입력하면, 직전 preview 후보의 hash 를 게이트 기록장
(~/.binggupack/save_gate_log.jsonl)에 남긴다. 이 기록이 있어야 save_selected 가 actor 를
human 으로 승격한다(0-A 해법, 4cli debate 20260616_0938 REFINE 합의).

설계 불변:
  - sync 등록 의무: 토큰/기록이 저장 호출보다 먼저 완료돼야 함(async 레이스 회피, B 지적).
  - capture_enabled(자동수집)와 무관 — 저장 게이트는 별개 축(B 지적2). 자동수집 OFF 여도 작동.
  - AI(claude)는 UserPromptSubmit 이벤트를 못 거침 → 위조 불가. 평소(SAVE 발화 0) 기록 0 = 자동적재 차단.
  - 원문 미접근: 직전 preview 의 hash 만 기록(binggu_save_gate.gate_record_from_prompt).
  - stdout 침묵 · 항상 exit 0 · 예외 전부 흡수(세션 무방해).
"""
import json
import os
import sys
from pathlib import Path


def _scripts_dir():
    env = os.environ.get("BINGGU_SCRIPTS")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "scripts"


def _run(data):
    try:
        if (data.get("hook_event_name") or "") != "UserPromptSubmit":
            return
        prompt = data.get("prompt", "")
        if "SAVE" not in prompt.upper():
            return  # 빠른 차단 — SAVE 토큰 없으면 모듈 로드도 안 함
        sd = str(_scripts_dir())
        if sd not in sys.path:
            sys.path.insert(0, sd)
        import binggu_save_gate as sgate
        sgate.gate_record_from_prompt(prompt)
    except Exception:
        return


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0
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

    tmp = Path(tempfile.mkdtemp(prefix="bgp_save_gate_hook_"))
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

        sys.path.insert(0, scripts)
        import binggu_save_gate as sgate
        # 직전 preview 후보 영속(원문 미저장)
        SA, SB = "선택될 후보 문장 가나다라", "선택 안 될 후보 마바사"
        sgate.write_last_preview([{"sentence": SA}, {"sentence": SB}],
                                 path=str(home / "last_preview_candidates.json"))

        gate_log = home / "save_gate_log.jsonl"

        # T1 비SAVE 발화 → 기록 0 · stdout 침묵
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": "그냥 잡담이야", "cwd": "x"})
        check(r.returncode == 0 and r.stdout.strip() == "" and not gate_log.exists(),
              "T1 비SAVE 발화 → 기록 0 · stdout 침묵")

        # T2 'SAVE 1' 발화 → idx1(SA) 기록
        r = call({"hook_event_name": "UserPromptSubmit", "prompt": "SAVE 1", "cwd": "x"})
        rec_ok = gate_log.exists() and (sgate.sent_hash(SA) in gate_log.read_text(encoding="utf-8"))
        check(r.returncode == 0 and r.stdout.strip() == "" and rec_ok,
              "T2 'SAVE 1' → SA hash 기록 · stdout 침묵")

        # T3 gate_human_for(SA) True / SB False
        check(sgate.gate_human_for([SA], path=str(gate_log)) is True
              and sgate.gate_human_for([SB], path=str(gate_log)) is False,
              "T3 SA→통과 / SB→차단")

        # T4 Stop 이벤트는 무시(발급 안 함)
        before = gate_log.read_text(encoding="utf-8")
        call({"hook_event_name": "Stop"})
        check(gate_log.read_text(encoding="utf-8") == before, "T4 Stop 이벤트 무시(기록 불변)")

        # T5 깨진/빈 stdin 방어
        check(call(None, raw="{ broken").returncode == 0, "T5 깨진 stdin → exit 0")
        check(call(None, raw="").returncode == 0, "T6 빈 stdin → exit 0")

        # T7 원문 미저장(preview 파일에 원문 없음)
        lp = (home / "last_preview_candidates.json").read_text(encoding="utf-8")
        check((SA not in lp) and (SB not in lp), "T7 preview 영속 원문 미포함(hash만)")

        print(f"\nGATE={'GO' if ok else 'NO-GO'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main())
