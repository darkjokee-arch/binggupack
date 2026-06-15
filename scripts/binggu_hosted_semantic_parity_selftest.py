# -*- coding: utf-8 -*-
"""binggu_hosted_semantic_parity_selftest.py — hosted semantic 도장 분류 py↔ts parity (4cli B'7).

node 런타임 부재(npm 미구성) → Python 이 .ts 소스를 regex 로 추출해 로직·상수 parity 검증.
검증 대상:
  - 모델 핀 일치: centroid_gen.py MODEL == capture_preview_semantic.ts EXPECT_MODEL == "@cf/baai/bge-m3"
  - band 임계 일관성: centroid_gen.py BAND_HI/LO == binggu_semantic_shadow BAND_HI/LO (로컬 정합)
  - band 분기 공식 py↔ts 동치 (hi/lo/ambiguous 경계)
  - B'7 ③ fallback no_suggestion 사슬 존재(.ts) — fallback_judgment → label_kind=null
  - B'7 ⑤ cos 격리: .ts 에 저장/캡처 결정 함수 부재(분류 제안 전용)
read-only · network 0 · fs write 0.

CLI: python binggu_hosted_semantic_parity_selftest.py [--selftest]
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F = {
    "py_gen": "scripts/binggu_hosted_centroid_gen.py",
    "py_shadow": "scripts/binggu_semantic_shadow.py",
    "ts_sem": "hosted/workers/src/capture_preview_semantic.ts",
}


def _load(k):
    with open(os.path.join(BASE, F[k]), encoding="utf-8") as f:
        return f.read()


def _strip_comments_ts(src):
    """JS/TS 주석(//, /* */) 제거 — 주석 내 설명 토큰이 코드 연결로 오탐되는 것 방지."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def _grab(src, pat, label):
    m = re.search(pat, src, re.MULTILINE)
    if not m:
        raise ValueError("extract miss: " + label)
    return m.group(1)


def run_selftest():
    s = {k: _load(k) for k in F}
    results = []

    def rec(cid, desc, fn):
        try:
            ok, detail = fn()
        except ValueError as e:
            ok, detail = False, str(e)
        results.append((cid, desc, ok, detail))

    def model_pin():
        a = _grab(s["py_gen"], r'^MODEL = "([^"]+)"', "py_gen.MODEL")
        b = _grab(s["ts_sem"], r'EXPECT_MODEL = "([^"]+)"', "ts.EXPECT_MODEL")
        return a == b == "@cf/baai/bge-m3", "py=%s ts=%s" % (a, b)

    rec(1, "모델 핀 py==ts (@cf/baai/bge-m3)", model_pin)

    def band_consistency():
        # centroid_gen.py BAND_HI/LO == 로컬 shadow BAND_HI/LO (centroid JSON 으로 .ts 에 전달됨)
        gh = _grab(s["py_gen"], r"^BAND_HI, BAND_LO = ([\d.]+), [\d.]+", "py_gen.BAND_HI")
        gl = _grab(s["py_gen"], r"^BAND_HI, BAND_LO = [\d.]+, ([\d.]+)", "py_gen.BAND_LO")
        sh = _grab(s["py_shadow"], r"^BAND_HI, BAND_LO = ([\d.]+), [\d.]+", "shadow.BAND_HI")
        sl = _grab(s["py_shadow"], r"^BAND_HI, BAND_LO = [\d.]+, ([\d.]+)", "shadow.BAND_LO")
        return (gh, gl) == (sh, sl), "gen=(%s,%s) shadow=(%s,%s)" % (gh, gl, sh, sl)

    rec(2, "band 임계 일관성 centroid_gen==shadow (로컬 정합)", band_consistency)

    def band_formula_ts():
        # .ts band 분기: bs >= cent.band_hi ? "hi" : (bs < cent.band_lo ? "lo" : "ambiguous")
        ok = bool(re.search(
            r'bs >= cent\.band_hi \? "hi" : \(bs < cent\.band_lo \? "lo" : "ambiguous"\)', s["ts_sem"]))
        return ok, "ts band 분기 공식 존재"

    rec(3, "band 분기 공식 .ts(hi/lo/ambiguous 경계)", band_formula_ts)

    def band_formula_py():
        # .py classify band 분기: "hi" if bs >= BAND_HI else ("lo" if bs < BAND_LO else "ambiguous")
        ok = bool(re.search(
            r'"hi" if bs >= BAND_HI else \("lo" if bs < BAND_LO else "ambiguous"\)', s["py_gen"]))
        return ok, "py band 분기 공식 존재(.ts 와 동치)"

    rec(4, "band 분기 공식 .py(.ts 와 동일 경계)", band_formula_py)

    def fallback_no_suggestion():
        # B'7 ③: RULES fallback_judgment → label_kind_suggestion: null (판단 강제 금지)
        has_branch = 'ruleId === "fallback_judgment"' in s["ts_sem"]
        has_null = "label_kind_suggestion: null" in s["ts_sem"]
        has_source = 'source: "no_suggestion"' in s["ts_sem"]
        return has_branch and has_null and has_source, \
            "fallback_judgment분기=%s null=%s no_suggestion=%s" % (has_branch, has_null, has_source)

    rec(5, "B'7③ fallback_judgment → no_suggestion 사슬(.ts)", fallback_no_suggestion)

    def optin_off_default():
        # 기본 OFF: SEMANTIC_LABEL_ENABLED === "1" 일 때만 활성
        ok = 'SEMANTIC_LABEL_ENABLED === "1"' in s["ts_sem"]
        return ok, "opt-in OFF 기본(=='1' 일때만 ON)"

    rec(6, "opt-in 기본 OFF(.ts)", optin_off_default)

    def cos_isolation():
        # B'7 ⑤: 분류 제안 전용 — 저장/캡처 결정 함수 부재(주석 제외, 실제 코드만 스캔)
        code = _strip_comments_ts(s["ts_sem"])
        bad = [t for t in ("should_capture", "shouldCapture", "saveCandidate", "writeLedger",
                            "persist", "ctx.storage", "INSERT INTO") if t in code]
        return not bad, ("격리 OK(주석 제외)" if not bad else "누수: " + ",".join(bad))

    rec(7, "B'7⑤ cos 격리 — 저장/캡처 결정 함수 부재(.ts)", cos_isolation)

    def drift_guard():
        # B'7 ①: model 불일치 시 semantic 비활성(fallback)
        ok = "cent.model !== EXPECT_MODEL" in s["ts_sem"]
        return ok, "버전 불일치 → fallback 가드"

    rec(8, "B'7① centroid 버전 핀 가드(.ts)", drift_guard)

    print("=" * 74)
    print("hosted semantic 도장 분류 — py<->ts parity selftest (read-only · network 0)")
    print("=" * 74)
    npass = sum(1 for _, _, ok, _ in results if ok)
    for cid, desc, ok, detail in results:
        print("%s %2d %s  [%s]" % ("[OK]" if ok else "[X]", cid, desc, detail))
    print("-" * 74)
    print("RESULT: %d/%d PASS" % (npass, len(results)))
    gate = "GO" if npass == len(results) else "NO-GO"
    print("GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    if not sys.argv[1:] or sys.argv[1] == "--selftest":
        run_selftest()
    else:
        print("usage: binggu_hosted_semantic_parity_selftest.py [--selftest]")
        sys.exit(2)
