"""BingguPack end-to-end lifecycle selftest (cross-platform).

실제 CLI(binggu.py)를 subprocess로 init→preview→save→list→deprecate→status
순서대로 끝까지 구동해, selftest(합성)가 아닌 실사용 라이프사이클이 현 OS에서
동작함을 확인한다.

- temp HOME 격리 (HOME/USERPROFILE 주입) — 운영 store(~/.binggupack) 미접촉.
- 실 ledger.sqlite write/read 실측 (단, temp 디렉터리 안).
- 환경 결정성: subprocess env 의 CLAUDECODE 를 케이스별 명시 제어 —
  CLAUDECODE=1(에이전트 세션 시뮬)에서는 훅 ref 앵커만 human(스펙 ①),
  CLAUDECODE 부재(터미널)에서는 명령 직접 입력이 곧 save n(스펙 ② · cli_command).
- cloud/DB/network 0.
- GATE=GO 조건 = 전 단계 PASS. (CI에서 exit code로 판정)
"""
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BINGGU = os.path.join(ROOT, "binggu.py")

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra else ""))


def run(args, home, claudecode=None):
    env = dict(os.environ)
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["PYTHONUTF8"] = "1"
    # 사람 승인이 아닌 신호는 전부 명시 제거(env 로 human 승격 불가).
    env.pop("BINGGU_TRUSTED_CLI", None)
    env.pop("BINGGU_STRICT_HUMAN_GATE", None)
    # CLAUDECODE 명시 제어(부모 세션 상속 차단 — 로컬 Claude Code/CI 어디서 돌아도 결정적):
    #   claudecode="1" = 에이전트 세션 시뮬(훅 ref 앵커만 human) · None = 터미널(cli_command).
    env.pop("CLAUDECODE", None)
    if claudecode is not None:
        env["CLAUDECODE"] = claudecode
    p = subprocess.run([sys.executable, BINGGU] + args, input="",
                       capture_output=True, text=True, env=env, encoding="utf-8")
    return p


def _seed_ref_anchor(home, txt):
    """사장님 '세이브 1' 발화 시뮬 — 훅 실흐름과 동일하게 write_last_preview + gate_record_from_prompt
    로 (preview_ref, idx) ref 레코드를 남긴다(정당 사람 근거 · env 백도어 아님). subprocess 의
    _resolve_human_ctx 가 <home>/.binggupack/save_gate_log.jsonl 을 읽으므로 동일 경로에 seed."""
    for _p in (ROOT, os.path.join(ROOT, "scripts")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    from binggupack.capture import preview as cvp
    import binggu_save_gate as sg
    bp_home = os.path.join(home, ".binggupack")
    os.makedirs(bp_home, exist_ok=True)
    lp = os.path.join(bp_home, "last_preview_candidates.json")
    gp = os.path.join(bp_home, "save_gate_log.jsonl")
    cands = cvp.capture_preview(txt, explicit=False).get("candidates", [])
    if cands:
        sg.write_last_preview(cands, path=lp)
        sg.gate_record_from_prompt("세이브 1", preview_path=lp, gate_path=gp, ts=time.time())
    return bool(cands)


def main():
    home = tempfile.mkdtemp(prefix="bgp_e2e_")
    ledger = os.path.join(home, ".binggupack", "ledger.sqlite")
    txt = "엔드투엔드 라이프사이클은 항상 preview 후 SAVE 확인을 먼저 거친다."

    # 1) init — 실장부 생성 (에이전트 세션 시뮬로 진행 — read-only/init 은 게이트 무관)
    r = run(["init", "--no-capture"], home, claudecode="1")
    check("1.init 실장부 생성", r.returncode == 0 and "장부 생성 완료" in r.stdout)
    check("2.ledger.sqlite 파일 실재", os.path.exists(ledger))

    # 3) status 초기 — active 0 · audit INTACT
    r = run(["status"], home, claudecode="1")
    check("3.초기 status active 0 · INTACT",
          "active 후보 0" in r.stdout and "INTACT" in r.stdout)

    # 4) preview — preview_id 발급
    r = run(["preview", txt], home, claudecode="1")
    m = re.search(r"preview_id: ([0-9a-f]+)", r.stdout)
    has_candidate_1 = bool(re.search(r"\|\s*1\s*\|", r.stdout))
    check("4.preview 후보 1건 + preview_id 발급", bool(m) and has_candidate_1,
          extra=(m.group(0) if m else r.stdout[:80] or r.stderr[:80]))
    pid = m.group(1) if m else ""

    # save-n 참조 바인딩: 에이전트 세션(CLAUDECODE=1)에서는 훅 ref 앵커가 없으면 save 가
    # fail-closed(reader)로 차단된다 — 사장님 '세이브 1' 발화 seed 후에만 저장된다.
    _seed_ref_anchor(home, txt)

    # 5) save — 실장부 write (saved:1) · save_gate ref 앵커(사람 근거)로 human 승격(스펙 ①)
    r = run(["save", txt, "--preview-id", pid, "--pick", "1", "--confirm", "SAVE 1"],
            home, claudecode="1")
    check("5.save 실장부 write(saved:1 · save_gate ref 앵커)",
          r.returncode == 0 and "'saved': 1" in r.stdout, extra=r.stdout.strip()[:80])

    # 6) list — 저장 노드 1건 + id8 파싱
    r = run(["list"], home, claudecode="1")
    m2 = re.search(r"\|\s*1\s*\|\s*([0-9a-f]{8})\s*\|", r.stdout)
    check("6.list 저장 노드 1건 + id8", bool(m2))
    id8 = m2.group(1) if m2 else ""

    # 7) status — 저장 후 active 1
    r = run(["status"], home, claudecode="1")
    check("7.저장 후 status active 1", "active 후보 1" in r.stdout)

    # 8) deprecate — 에이전트 세션(CLAUDECODE=1): index-op 는 ref 앵커가 없으므로 fail-closed.
    #    (에이전트 세션 안에서 명령 실행 주체는 AI 일 수 있음 — 훅 앵커만 사람 증명 · deny 전용 가드.)
    r = run(["deprecate", "1", id8, "--reason", "e2e_lifecycle_test",
             "--confirm", f"DEPRECATE 1 {id8}"], home, claudecode="1")
    check("8.deprecate 에이전트 세션 → fail-closed(returncode≠0)", r.returncode != 0,
          extra=r.stdout.strip()[:80] or r.stderr.strip()[:80])

    # 9) status — deprecate 차단되어 active 1 유지 · audit INTACT
    r = run(["status"], home, claudecode="1")
    check("9.deprecate 차단 → active 1 유지 · audit INTACT",
          "active 후보 1" in r.stdout and "INTACT" in r.stdout)

    # 10) deprecated 목록 비어있음(fail-closed 로 기각 미반영)
    r = run(["list", "--status", "deprecated"], home, claudecode="1")
    check("10.deprecated 목록에 노드 없음(fail-closed)", id8 not in r.stdout)

    # 11) 터미널(CLAUDECODE 부재) = 명령 직접 입력이 곧 save n → deprecate 성공(스펙 ② · cli_command)
    r = run(["deprecate", "1", id8, "--reason", "e2e_lifecycle_terminal",
             "--confirm", f"DEPRECATE 1 {id8}"], home, claudecode=None)
    check("11.deprecate 터미널(CLAUDECODE 부재) → 성공(cli_command)", r.returncode == 0,
          extra=r.stdout.strip()[:80] or r.stderr.strip()[:80])

    # 12) status 최종 — 터미널 기각 반영 active 0 · audit INTACT
    r = run(["status"], home, claudecode=None)
    check("12.터미널 기각 반영 → active 0 · audit INTACT",
          "active 후보 0" in r.stdout and "INTACT" in r.stdout)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} ===")
    gate = "GO" if passed == total else "BLOCK"
    print(f"E2E_LIFECYCLE: {gate}  (platform check via binggu_platform_selftest)")
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
