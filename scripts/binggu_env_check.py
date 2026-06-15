# -*- coding: utf-8 -*-
"""binggu_env_check.py — 첫 설치/init 환경 점검 + 설치 안내 (4cli 20260615_1900 옵션1: 점검+안내).

신규 사용자가 init 하면 "무엇이 켜지고 무엇을 더 깔면 뭐가 생기는지" 한 화면에 보여준다.
- 자동 설치 안 함: 없는 의존성은 OS별 명령만 안내(사용자가 직접 복붙). 무거운 모델 동의 없이 설치 금지.
- 점검만 — 파일/장부 write 0, 네트워크는 Ollama 감지(2s) 1회뿐.
- 필수는 Python 3.10+ 하나. 나머지(Ollama=똑똑한 분류, Node=hosted)는 선택.

CLI: python binggu_env_check.py            # 점검 리포트 출력
     python binggu_env_check.py --selftest # 12/12 GATE=GO 기대
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binggu_platform as P  # noqa: E402


def _ollama_install_cmd(os_name):
    if os_name == "windows":
        return "winget install Ollama.Ollama"
    if os_name == "macos":
        return "brew install ollama"
    return "curl -fsSL https://ollama.com/install.sh | sh"   # wsl / linux


def check_env(os_name=None, ollama_probe=None, node_probe=None):
    """환경 점검 결과 dict. probe= 테스트 주입(실제는 자동 감지). write 0."""
    os_name = os_name or P.detect_os()
    py_ok = sys.version_info >= (3, 10)
    if ollama_probe is not None:
        oll = ollama_probe
    else:
        try:
            import binggu_canonical_semantic as C
            oll = C.ollama_available()
        except Exception:
            oll = False
    node = node_probe if node_probe is not None else (shutil.which("node") is not None)
    return {
        "os": os_name,
        "python": {"ok": bool(py_ok), "version": "%d.%d" % sys.version_info[:2]},
        "semantic": {"ready": bool(oll),
                     "install": _ollama_install_cmd(os_name), "pull": "ollama pull bge-m3"},
        "hosted": {"node": bool(node)},
    }


def render_report(res):
    """사람용 안내 텍스트. 있으면 [ON]/[OK], 없으면 [--]+설치 명령."""
    L = ["=" * 62, "빙구팩 환경 점검 — 무엇이 켜지나 (필수는 Python 하나)", "=" * 62]
    p = res["python"]
    L.append("%s Python %s  (필수)" % ("[OK]" if p["ok"] else "[!] 3.10+ 필요", p["version"]))
    s = res["semantic"]
    if s["ready"]:
        L.append("[ON] 똑똑한 뜻 분류 — Ollama+bge-m3 감지됨, 자동 켜짐")
    else:
        L.append("[--] 똑똑한 뜻 분류 — 지금은 정규식만. 켜려면 한 번 설치(선택):")
        L.append("       %s" % s["install"])
        L.append("       %s" % s["pull"])
        L.append("     설치 후 재설정 0 — 빙구팩이 자동 감지. (자동 설치는 안 합니다)")
    h = res["hosted"]
    if h["node"]:
        L.append("[OK] 폰/클라우드(hosted) 자가배포 가능 — Node 감지됨")
    else:
        L.append("[--] 폰/클라우드(hosted) — 쓸 때만 Node 필요: https://nodejs.org")
    L.append("-" * 62)
    L.append("거부/강제 끄기: 환경변수 BINGGU_SEMANTIC_OFF=1 (정규식 분류로 고정)")
    return "\n".join(L)


def run_selftest():
    results = []

    def rec(d, ok):
        results.append((d, bool(ok)))

    # 1. 구조
    r = check_env(os_name="windows", ollama_probe=True, node_probe=True)
    rec("1.결과 키(os/python/semantic/hosted)",
        set(r.keys()) == {"os", "python", "semantic", "hosted"})
    # 2. ollama probe True → ready
    rec("2.Ollama 감지 시 semantic.ready=True", r["semantic"]["ready"] is True)
    # 3. ollama probe False → ready False + 설치 명령 존재
    r2 = check_env(os_name="windows", ollama_probe=False, node_probe=False)
    rec("3.Ollama 없으면 ready=False + install 명령", r2["semantic"]["ready"] is False and r2["semantic"]["install"])
    # 4~6. OS별 설치 명령
    rec("4.Windows 설치 명령(winget)", "winget" in check_env(os_name="windows", ollama_probe=False)["semantic"]["install"])
    rec("5.macOS 설치 명령(brew)", "brew" in check_env(os_name="macos", ollama_probe=False)["semantic"]["install"])
    rec("6.WSL/Linux 설치 명령(curl install.sh)", "install.sh" in check_env(os_name="wsl", ollama_probe=False)["semantic"]["install"])
    # 7. node probe
    rec("7.node 없으면 hosted.node=False", check_env(ollama_probe=False, node_probe=False)["hosted"]["node"] is False)
    # 8. render 핵심 포함(켜짐)
    rep_on = render_report(check_env(os_name="windows", ollama_probe=True, node_probe=True))
    rec("8.render ON 표기", "[ON]" in rep_on and "자동 켜짐" in rep_on)
    # 9. render 설치 안내(꺼짐)
    rep_off = render_report(check_env(os_name="windows", ollama_probe=False, node_probe=False))
    rec("9.render 설치 명령 안내", "winget" in rep_off and "ollama pull bge-m3" in rep_off)
    # 10. 자동 설치 안 함 — 외부 명령 실행 코드 부재. 검사 토큰은 분리 조립(자기검출 회피).
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    bad = ["sub" + "process", "os." + "system", "Pop" + "en", "check_" + "output", "os." + "popen"]
    rec("10.외부 명령 실행 코드 부재(자동설치 X)", not any(t in src for t in bad))
    # 11. write 0 — save/write/persist 함수 부재
    rec("11.파일 write 함수 부재",
        not any(n.startswith("save") or n.startswith("write") or "persist" in n for n in dir(sys.modules[__name__])))
    # 12. python 버전 판정
    rec("12.python 버전 ok 필드", isinstance(check_env(ollama_probe=False)["python"]["ok"], bool))

    print("=" * 62)
    print("binggu_env_check — selftest (환경 점검 + 설치 안내, 자동설치 X)")
    print("=" * 62)
    npass = sum(1 for _, ok in results if ok)
    for d, ok in results:
        print("%s %s" % ("[OK]" if ok else "[X]", d))
    print("-" * 62)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    gate = "GO" if npass == len(results) else "NO-GO"
    print("GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        run_selftest()
    else:
        print(render_report(check_env()))
