# -*- coding: utf-8 -*-
"""BingguPack — version SSOT 일치 검증 selftest (Track B).

version 의 단일 진실(SSOT)은 binggupack/__about__.py 의 __version__.
pyproject.toml [project].version 이 이와 일치하는지 검사한다.

pyproject 파싱: tomllib(py3.11+) 우선, 없으면 정규식 fallback.
__about__: 직접 import 대신 정규식으로 __version__ 추출(부수효과 0).
불일치/추출 실패 = exit 1 (fail-closed). read-only — FS write 0 · 네트워크 0.

CLI: python scripts/version_consistency_selftest.py [--selftest]
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPROJECT = os.path.join(BASE, "pyproject.toml")
ABOUT = os.path.join(BASE, "binggupack", "__about__.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _about_version():
    src = _read(ABOUT)
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', src, re.MULTILINE)
    if not m:
        raise ValueError("extract miss: __about__.__version__")
    return m.group(1)


def _pyproject_version():
    raw = _read(PYPROJECT)
    try:
        import tomllib  # py3.11+
        data = tomllib.loads(raw)
        v = data.get("project", {}).get("version")
        if not v:
            raise ValueError("extract miss: pyproject [project].version (tomllib)")
        return v, "tomllib"
    except ModuleNotFoundError:
        # 정규식 fallback (py3.10): [project] 섹션 내 version = "..."
        # 줄 끝 주석(# ...) 허용
        m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', raw, re.MULTILINE)
        if not m:
            raise ValueError("extract miss: pyproject version (regex fallback)")
        return m.group(1), "regex"


def run_selftest():
    results = []

    def rec(cid, desc, fn):
        try:
            ok, detail = fn()
        except (ValueError, OSError) as e:
            ok, detail = False, str(e)
        results.append((cid, desc, ok, detail))

    state = {}

    def grab_about():
        v = _about_version()
        state["about"] = v
        return True, "__about__.__version__=%s" % v

    def grab_pyproject():
        v, how = _pyproject_version()
        state["pyproject"] = v
        state["parser"] = how
        return True, "pyproject version=%s (parser=%s)" % (v, how)

    def match():
        a = state.get("about")
        p = state.get("pyproject")
        if a is None or p is None:
            return False, "선행 추출 실패로 비교 불가"
        return a == p, "about=%s pyproject=%s" % (a, p)

    rec(1, "__about__.__version__ 추출 (SSOT)", grab_about)
    rec(2, "pyproject [project].version 추출 (tomllib/regex)", grab_pyproject)
    rec(3, "MATCH: pyproject version == __about__ __version__", match)

    print("=" * 74)
    print("BingguPack version SSOT 일치 selftest (read-only)")
    print("=" * 74)
    npass = sum(1 for _, _, ok, _ in results if ok)
    for cid, desc, ok, detail in results:
        print("%s %2d %s  [%s]" % ("[OK]" if ok else "[X]", cid, desc, detail))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("fs_write=0  network=0  parser=%s" % state.get("parser", "n/a"))
    gate = "GO" if npass == len(results) else "NO-GO"
    if gate == "GO":
        print("MATCH: version=%s" % state.get("about"))
    print("GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        run_selftest()
    else:
        print("usage: version_consistency_selftest.py [--selftest]")
        sys.exit(2)
