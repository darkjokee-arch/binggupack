"""BingguPack end-to-end lifecycle selftest (cross-platform).

실제 CLI(binggu.py)를 subprocess로 init→preview→save→list→deprecate→status
순서대로 끝까지 구동해, selftest(합성)가 아닌 실사용 라이프사이클이 현 OS에서
동작함을 확인한다.

- temp HOME 격리 (HOME/USERPROFILE 주입) — 운영 store(~/.binggupack) 미접촉.
- 실 ledger.sqlite write/read 실측 (단, temp 디렉터리 안).
- cloud/DB/network 0.
- GATE=GO 조건 = 전 단계 PASS. (CI에서 exit code로 판정)
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BINGGU = os.path.join(ROOT, "binggu.py")

results = []


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra else ""))


def run(args, home):
    env = dict(os.environ)
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["PYTHONUTF8"] = "1"
    # P1-A.1: 비대화형 subprocess 는 fail-closed(env 로 human 승격 불가). 정당 사람 근거는 save_gate 앵커.
    env.pop("BINGGU_TRUSTED_CLI", None)
    env.pop("BINGGU_STRICT_HUMAN_GATE", None)
    # P1-A.1: stdin=PIPE 강제(input="") → isatty() False 결정적. stdin 미지정 시 부모 콘솔을 상속해
    #   대화형 터미널에서 isatty True(Windows inherited console)가 되어 step8 deprecate 가 human 으로
    #   통과(비결정)한다. 파이프는 전 OS 에서 non-tty 이므로 비대화형 fail-closed 를 결정적으로 검증한다.
    p = subprocess.run([sys.executable, BINGGU] + args, input="",
                       capture_output=True, text=True, env=env, encoding="utf-8")
    return p


def _seed_anchor(ledger, txt):
    """사장님 키보드 SAVE 시뮬 — save_gate 앵커 seed(정당 사람 근거 · env 백도어 아님). subprocess 의
    gate_human_for 가 _gate_log_for_ledger(ledger) 를 읽으므로 동일 경로에 in-process 로 seed 한다."""
    for _p in (ROOT, os.path.join(ROOT, "scripts")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    from binggupack.capture import preview as cvp
    import binggu_save_gate as sg
    from binggu import _gate_log_for_ledger
    cands = cvp.capture_preview(txt, explicit=False).get("candidates", [])
    if cands:
        sg.gate_record([cands[0]["sentence"]], path=_gate_log_for_ledger(ledger))
    return bool(cands)


def main():
    home = tempfile.mkdtemp(prefix="bgp_e2e_")
    ledger = os.path.join(home, ".binggupack", "ledger.sqlite")
    txt = "엔드투엔드 라이프사이클은 항상 preview 후 SAVE 확인을 먼저 거친다."

    # 1) init — 실장부 생성
    r = run(["init", "--no-capture"], home)
    check("1.init 실장부 생성", r.returncode == 0 and "장부 생성 완료" in r.stdout)
    check("2.ledger.sqlite 파일 실재", os.path.exists(ledger))

    # 3) status 초기 — active 0 · audit INTACT
    r = run(["status"], home)
    check("3.초기 status active 0 · INTACT",
          "active 후보 0" in r.stdout and "INTACT" in r.stdout)

    # 4) preview — preview_id 발급
    r = run(["preview", txt], home)
    m = re.search(r"preview_id: ([0-9a-f]+)", r.stdout)
    has_candidate_1 = bool(re.search(r"\|\s*1\s*\|", r.stdout))
    check("4.preview 후보 1건 + preview_id 발급", bool(m) and has_candidate_1,
          extra=(m.group(0) if m else r.stdout[:80] or r.stderr[:80]))
    pid = m.group(1) if m else ""

    # P1-A.1: save 전에 save_gate 앵커 seed(사장님 키보드 SAVE 시뮬). 이게 없으면 비대화형 save 는
    #         fail-closed(reader)로 차단된다 — 환경변수/isatty 로는 human 승격 불가.
    _seed_anchor(ledger, txt)

    # 5) save — 실장부 write (saved:1) · save_gate 앵커(사람 근거)로 human 승격
    r = run(["save", txt, "--preview-id", pid, "--pick", "1", "--confirm", "SAVE 1"], home)
    check("5.save 실장부 write(saved:1 · save_gate 앵커)",
          r.returncode == 0 and "'saved': 1" in r.stdout, extra=r.stdout.strip()[:80])

    # 6) list — 저장 노드 1건 + id8 파싱
    r = run(["list"], home)
    m2 = re.search(r"\|\s*1\s*\|\s*([0-9a-f]{8})\s*\|", r.stdout)
    check("6.list 저장 노드 1건 + id8", bool(m2))
    id8 = m2.group(1) if m2 else ""

    # 7) status — 저장 후 active 1
    r = run(["status"], home)
    check("7.저장 후 status active 1", "active 후보 1" in r.stdout)

    # 8) deprecate — P1-A.1: index-op 는 save_gate 앵커가 없고 비대화형(subprocess)이므로 fail-closed.
    #    (owner 는 대화형 TTY 에서 실행 · 자동화/파이프는 사람 승인이 아님 · RFC §6. 비대화형 owner
    #     승인 경로[approval-event schema]는 accept/unaccept/due/resolve 와 함께 P1-B.)
    r = run(["deprecate", "1", id8, "--reason", "e2e_lifecycle_test",
             "--confirm", f"DEPRECATE 1 {id8}"], home)
    check("8.deprecate 비대화형 → fail-closed(returncode≠0)", r.returncode != 0,
          extra=r.stdout.strip()[:80] or r.stderr.strip()[:80])

    # 9) status 최종 — deprecate 차단되어 active 1 유지 · audit INTACT
    r = run(["status"], home)
    check("9.deprecate 차단 → active 1 유지 · audit INTACT",
          "active 후보 1" in r.stdout and "INTACT" in r.stdout)

    # 10) deprecated 목록 비어있음(fail-closed 로 기각 미반영)
    r = run(["list", "--status", "deprecated"], home)
    check("10.deprecated 목록에 노드 없음(fail-closed)", id8 not in r.stdout)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} ===")
    gate = "GO" if passed == total else "BLOCK"
    print(f"E2E_LIFECYCLE: {gate}  (platform check via binggu_platform_selftest)")
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
