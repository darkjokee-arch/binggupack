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
    p = subprocess.run([sys.executable, BINGGU] + args,
                       capture_output=True, text=True, env=env, encoding="utf-8")
    return p


def main():
    home = tempfile.mkdtemp(prefix="bgp_e2e_")
    ledger = os.path.join(home, ".binggupack", "ledger.sqlite")
    txt = "엔드투엔드 라이프사이클 검증 문장 — 빙구팩 실사용 경로가 이 OS에서 작동한다"

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
    check("4.preview preview_id 발급", bool(m), extra=(m.group(0) if m else r.stderr[:80]))
    pid = m.group(1) if m else ""

    # 5) save — 실장부 write (saved:1)
    r = run(["save", txt, "--preview-id", pid, "--pick", "1", "--confirm", "SAVE 1"], home)
    check("5.save 실장부 write(saved:1)",
          r.returncode == 0 and "'saved': 1" in r.stdout, extra=r.stdout.strip()[:80])

    # 6) list — 저장 노드 1건 + id8 파싱
    r = run(["list"], home)
    m2 = re.search(r"\|\s*1\s*\|\s*([0-9a-f]{8})\s*\|", r.stdout)
    check("6.list 저장 노드 1건 + id8", bool(m2))
    id8 = m2.group(1) if m2 else ""

    # 7) status — 저장 후 active 1
    r = run(["status"], home)
    check("7.저장 후 status active 1", "active 후보 1" in r.stdout)

    # 8) deprecate — 라이프사이클 상태 전이 (confirm = "DEPRECATE <#> <id8>")
    r = run(["deprecate", "1", id8, "--reason", "e2e_lifecycle_test",
             "--confirm", f"DEPRECATE 1 {id8}"], home)
    check("8.deprecate 처리(returncode 0)", r.returncode == 0, extra=r.stdout.strip()[:80] or r.stderr.strip()[:80])

    # 9) status 최종 — 기각 1 · audit INTACT (체인 무결)
    r = run(["status"], home)
    check("9.최종 status 기각 1 · audit INTACT",
          "기각 1" in r.stdout and "INTACT" in r.stdout)

    # 10) deprecated 목록 조회
    r = run(["list", "--status", "deprecated"], home)
    check("10.deprecated 목록에 노드 존재", id8 in r.stdout)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} ===")
    gate = "GO" if passed == total else "BLOCK"
    print(f"E2E_LIFECYCLE: {gate}  (platform check via binggu_platform_selftest)")
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
