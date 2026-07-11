# -*- coding: utf-8 -*-
"""Characterization selftest — openbinggu_path_safety_gate.classify_path (v1.11.0 save-gate S2).

이관 전 현행 판정을 고정한다(pre-move characterization). 이관 후 thin wrapper 에서도
동일 PASS 해야 한다. 호출처 2개와 동일 import 형태
(from openbinggu_path_safety_gate import classify_path / import ... as psg).

classify_path 는 순수 판정(write 0, raw 경로 미노출 — verdict/reason_code/path_id 만).
본 테스트는:
  - safe relative / project-local → ALLOW
  - parent traversal(..) / absolute outside / UNC / ADS / 8.3 / denylist / symlink / empty → BLOCK(각 reason)
  - Windows-style path
  - allow/block label·shape 고정({verdict, reason_code, path_id})
  - deterministic(같은 입력 2회 동일)
  - raw 경로 미노출(path_id 에 원본 substring 없음)
  - write 0
read-only. write 0.
"""
import os
import sys

import openbinggu_path_safety_gate as psg  # noqa: E402  (호출처와 동일 형태)
from openbinggu_path_safety_gate import classify_path  # noqa: E402

_KEYS = {"verdict", "reason_code", "path_id"}
ROOT = os.path.normpath(os.path.join(os.environ.get("TEMP", "/tmp"), "bgp_s2_char_allow_root"))


def run():
    results = []

    def ck(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    def verdict(inp, slink=False):
        return classify_path(inp, ROOT, symlink_detected=slink)

    # ---- ALLOW: safe relative / project-local ----
    ck("safe_relative_allow", verdict("examples/toy_project/Makefile")["verdict"] == "ALLOW", "프로젝트 내부 상대경로")
    ck("safe_sub_allow", verdict("examples/toy_project/src/build.py")["verdict"] == "ALLOW", "하위 경로")

    # ---- BLOCK: 각 reason_code 고정 ----
    def block_is(inp, rc, slink=False):
        r = verdict(inp, slink)
        return r["verdict"] == "BLOCK" and r["reason_code"] == rc
    ck("parent_escape", block_is("../secret.txt", "parent_escape"), "상위 탈출")
    ck("deep_parent_escape", block_is("examples/../../etc/passwd", "parent_escape"), "깊은 상위 탈출")
    ck("unc", block_is("\\\\fileserver\\share\\secret", "unc"), "UNC")
    ck("ads", block_is("examples/notes.txt:hidden", "ads"), "ADS")
    ck("short_8_3", block_is("examples/PROGRA~1/app.py", "short_8_3"), "8.3 단축명")
    ck("deny_secret_env", block_is("examples/toy_project/.env", "deny_secret"), ".env")
    ck("deny_secret_key", block_is("examples/keys/id_rsa", "deny_secret"), "private key")
    # 소유자 사설 프로젝트 deny(deny_private_project): repo 밖 owner-only 파일에서 런타임 로드
    #   (배포물엔 실제 토큰 없음). synthetic 토큰(example-project)으로 판정 고정 — owner 실제 값 미사용.
    #   고유 temp 경로 env 라 gate 내부 경로별 캐시와 무관(fresh load). 검증 후 원복.
    import shutil as _sh
    import tempfile as _tf
    _pp_dir = _tf.mkdtemp(prefix="s2char_pp_")
    _pp_file = os.path.join(_pp_dir, "deny_tokens.txt")
    with open(_pp_file, "w", encoding="utf-8") as _f:
        _f.write("# synthetic owner deny (characterization)\nexample-project\n")
    _saved_pd = os.environ.get("BINGGU_PRIVATE_DENY")
    try:
        os.environ["BINGGU_PRIVATE_DENY"] = _pp_file
        try:                                          # gate 내부 캐시 무효화(있으면·없어도 무관)
            from binggupack.safety.path_safety import _OWNER_DENY_CACHE as _odc
            _odc.clear()
        except Exception:
            pass
        ck("deny_private_project",
           block_is("examples/example-project/x.py", "deny_private_project"),
           "소유자 사설 프로젝트(런타임 deny 토큰)")
    finally:
        if _saved_pd is None:
            os.environ.pop("BINGGU_PRIVATE_DENY", None)
        else:
            os.environ["BINGGU_PRIVATE_DENY"] = _saved_pd
        try:
            from binggupack.safety.path_safety import _OWNER_DENY_CACHE as _odc2
            _odc2.clear()
        except Exception:
            pass
        _sh.rmtree(_pp_dir, ignore_errors=True)
    ck("deny_cert_npki", block_is("C:/Users/fixture-user/NPKI/yessign/cert.der", "deny_cert_npki"), "NPKI 인증서")
    ck("deny_opencrab", block_is("data/localcrab_index.sqlite", "deny_opencrab_store"), "opencrab store")
    ck("symlink_junction", block_is("examples/toy_project/linked/x", "symlink_junction", slink=True), "symlink 주입")
    ck("empty_unknown", block_is("   ", "empty_unknown"), "빈 입력 fail-closed")

    # ---- absolute outside ----
    outside = "C:/Windows/System32/config/SAM" if os.name == "nt" else "/etc/passwd"
    ck("outside_root_abs", block_is(outside, "outside_root"), "트리 밖 절대경로")

    # ---- Windows-style path (드라이브) ----
    win = verdict("C:/SomeOther/proj/file.txt")
    ck("windows_style_handled", win["verdict"] in ("ALLOW", "BLOCK") and set(win.keys()) == _KEYS,
       "윈도우 경로 정상 판정")

    # ---- shape 고정 ----
    shape_ok = True
    for inp in ["examples/toy_project/Makefile", "../x", ".env", "   ", outside]:
        if set(verdict(inp).keys()) != _KEYS:
            shape_ok = False; break
    ck("verdict_shape_fixed", shape_ok, "3키 고정(verdict/reason_code/path_id)")

    # ---- deterministic ----
    ck("deterministic", verdict("examples/toy_project/Makefile") == verdict("examples/toy_project/Makefile"),
       "같은 입력 2회 동일")

    # ---- raw 경로 미노출 (path_id 에 원본 substring 없음) ----
    leak = False
    for inp in ["examples/toy_project/.env", "C:/Users/fixture-user/example-org/example-project/x.py", "../secret.txt"]:
        r = verdict(inp)
        if any(isinstance(v, str) and inp.strip() in v for v in r.values()):
            leak = True; break
    ck("raw_path_not_leaked", not leak, "verdict 결과에 raw 경로 평문 0(path_id=hash)")

    # ---- write 0 (모듈에 write/save 심볼 없음) ----
    ck("pure_no_write", not hasattr(psg, "_write") and not hasattr(psg, "save"), "path_safety에 write/save 심볼 없음")

    print("=" * 74)
    print("openbinggu_path_safety_gate characterization selftest (pure, write 0)")
    print("=" * 74)
    all_ok = True
    for name, ok, detail in results:
        all_ok = all_ok and ok
        print("  [%s] %-28s %s" % ("OK" if ok else "FAIL", name, "" if ok else ("<< " + detail)))
    gate = "GO" if all_ok else "NO-GO"
    print("\n  GATE:", gate)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run())
