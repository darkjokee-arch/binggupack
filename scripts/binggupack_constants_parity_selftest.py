# -*- coding: utf-8 -*-
"""BingguPack — py↔ts 매직넘버 동기 검증 selftest (L-10).

언어 횡단 상수(후보 상한 10, INPUT_CAP 20000 등)를 양쪽 소스에서 regex로 추출해
일치 검사. 추출 실패(상수 이동/개명 포함)·불일치 = exit 1 (fail-closed).
read-only — FS write 0 · 네트워크 0.

비교 제외(의도적 분기): MAX_RESPONSE_CHARS — py skeleton 36000(PoC frozen) vs
TS index.ts 20000(S6 축소)는 설계상 다름. 현재 의도값 고정 검사만 수행.
참고: VIEW_CAP 은 양쪽 트리에 미존재 — 검사 대상 아님.

CLI: python binggupack_constants_parity_selftest.py [--selftest]
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = {
    "py_preview": "scripts/openbinggu_conversation_capture_preview.py",
    "ts_preview": "hosted/workers/src/capture_preview.ts",
    "ts_index": "hosted/workers/src/index.ts",
    "py_skeleton": "scripts/binggupack_http_mcp_skeleton.py",
    "ts_save_mcp": "hosted/workers/src/save_intent_mcp.ts",
}


def _load(key):
    path = os.path.join(BASE, FILES[key])
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _grab(src, pattern, label):
    m = re.search(pattern, src, re.MULTILINE)
    if not m:
        raise ValueError("extract miss: " + label)
    return m.group(1)


def run_selftest():
    srcs = {k: _load(k) for k in FILES}
    results = []

    def rec(cid, desc, fn):
        try:
            ok, detail = fn()
        except ValueError as e:
            ok, detail = False, str(e)
        results.append((cid, desc, ok, detail))

    def num_pair(py_key, py_name, ts_key, ts_name):
        a = _grab(srcs[py_key], r"^%s = (\d+)" % py_name, py_key + "." + py_name)
        b = _grab(srcs[ts_key], r"const %s = (\d+);" % ts_name, ts_key + "." + ts_name)
        return a == b, "py=%s ts=%s" % (a, b)

    rec(1, "INPUT_CAP py==ts (입력 캡)",
        lambda: num_pair("py_preview", "INPUT_CAP", "ts_preview", "INPUT_CAP"))
    rec(2, "DEFAULT_MAX py==ts (후보 상한 기본)",
        lambda: num_pair("py_preview", "DEFAULT_MAX", "ts_preview", "DEFAULT_MAX"))
    rec(3, "HARD_MAX py==ts (후보 상한 최대)",
        lambda: num_pair("py_preview", "HARD_MAX", "ts_preview", "HARD_MAX"))
    rec(4, "MAX_NODE_SENTENCE py==ts (단일 문장 정당 상한 — 발췌 cut 폐기)",
        lambda: num_pair("py_preview", "MAX_NODE_SENTENCE", "ts_preview", "MAX_NODE_SENTENCE"))

    def bizno_pair(name):
        a = _grab(srcs["py_preview"],
                  r'\("%s", re\.compile\(r"([^"]+)"\)\)' % name, "py." + name)
        b = _grab(srcs["ts_preview"],
                  r'\["%s", /(.+?)/\]' % name, "ts." + name)
        return a == b, "py=%s ts=%s" % (a, b)

    rec(5, "scan_bizno_fmt regex py==ts (사업자번호 형식)",
        lambda: bizno_pair("scan_bizno_fmt"))
    rec(6, "scan_bizno_bare regex py==ts (사업자번호 bare)",
        lambda: bizno_pair("scan_bizno_bare"))

    def candidate_max():
        a = _grab(srcs["py_preview"], r"^DEFAULT_MAX = (\d+)", "py.DEFAULT_MAX")
        b = _grab(srcs["ts_save_mcp"], r"const CANDIDATE_MAX = (\d+);", "ts.CANDIDATE_MAX")
        return a == b, "py DEFAULT_MAX=%s ts CANDIDATE_MAX=%s" % (a, b)

    rec(7, "save_intent_mcp CANDIDATE_MAX == DEFAULT_MAX (번호 체계 동일성)", candidate_max)

    def desc_numbers():
        d, h = _grab(srcs["py_preview"], r"^DEFAULT_MAX = (\d+)", "py.DEFAULT_MAX"), \
               _grab(srcs["py_preview"], r"^HARD_MAX = (\d+)", "py.HARD_MAX")
        m = re.search(r'max_candidates: \{ type: "integer", description: "기본 (\d+), 최대 (\d+)"',
                      srcs["ts_index"])
        if not m:
            raise ValueError("extract miss: ts_index.max_candidates description")
        return (m.group(1), m.group(2)) == (d, h), \
            "desc=(%s,%s) const=(%s,%s)" % (m.group(1), m.group(2), d, h)

    rec(8, "index.ts max_candidates 설명문구 숫자 == DEFAULT_MAX/HARD_MAX", desc_numbers)

    def excerpt_max():
        a = _grab(srcs["py_skeleton"], r"^EXCERPT_MAX = (\d+)", "py_skeleton.EXCERPT_MAX")
        b = _grab(srcs["ts_index"], r"const EXCERPT_MAX = (\d+);", "ts_index.EXCERPT_MAX")
        return a == b, "py=%s ts=%s" % (a, b)

    rec(9, "EXCERPT_MAX py skeleton==ts index (검색 발췌 캡)", excerpt_max)

    def response_caps():
        a = _grab(srcs["py_skeleton"], r"^MAX_RESPONSE_CHARS = (\d+)", "py_skeleton.cap")
        b = _grab(srcs["ts_index"], r"const MAX_RESPONSE_CHARS = (\d+);", "ts_index.cap")
        return (a, b) == ("36000", "20000"), "py=%s(기대36000) ts=%s(기대20000)" % (a, b)

    rec(10, "MAX_RESPONSE_CHARS 의도값 고정(py 36000 / ts 20000 — S6 분기)", response_caps)

    print("=" * 74)
    print("BingguPack constants parity — py<->ts 매직넘버 동기 selftest (read-only)")
    print("=" * 74)
    npass = sum(1 for _, _, ok, _ in results if ok)
    for cid, desc, ok, detail in results:
        print("%s %2d %s  [%s]" % ("[OK]" if ok else "[X]", cid, desc, detail))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    print("fs_write=0  network=0  excluded=MAX_RESPONSE_CHARS(의도 분기)  view_cap=미존재")
    gate = "GO" if npass == len(results) else "NO-GO"
    print("GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        run_selftest()
    else:
        print("usage: binggupack_constants_parity_selftest.py [--selftest]")
        sys.exit(2)
