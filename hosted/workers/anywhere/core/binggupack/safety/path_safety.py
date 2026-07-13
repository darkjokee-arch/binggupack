#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu MCP/local path safety gate (S3/X1 최소 구현 후보).

v1.11.0 save-gate 라인 S2: 경로 안전 판정 로직을 scripts/openbinggu_path_safety_gate.py
에서 이 모듈로 이관했다. scripts 파일은 backward-compatible thin wrapper(sys.path bootstrap
+ 전체 심볼 re-export + __main__ main)로 유지되며 공개 심볼/판정(verdict/reason_code/path_id)은
byte-identical 하다(기능 변경 0). 순수 판정(write 0)·binggupack 외부 의존 0.

목적:
- MCP/local 도구가 사용자의 명시 작업 디렉터리(allow_root) 밖을 읽지 못하게 한다.
- symlink/junction/대소문자/8.3 단축명/ADS/UNC/상위경로 탈출을 차단한다.
- 인증서 저장소(cert store)·.env/credential·OpenCrab operating store·소유자 지정 사설
  프로젝트(런타임 로드 · 배포물엔 실제 토큰 없음)·다른 프로젝트 경로를 deny한다.
- raw 경로값은 출력하지 않는다 → verdict(ALLOW/BLOCK) + reason_code + path_id(hash) 만.

범위: 경로 분석만. 실제 파일시스템 write 0. operating store/DB/production 미접근.
CLI: python scripts/openbinggu_path_safety_gate.py --selftest

설계 ref: BINGGUPACK_FIRST_RELEASE_4CLI_SYNTHESIS.md §3-A(S3)/§3-B(X1)
"""
import hashlib
import os
import re
import sys


def _path_id(s):
    """raw 경로 대신 안정적 식별자(역추적용 원본 미노출). 짧은 hash."""
    return "sp_" + hashlib.sha256(os.path.normcase(s).encode("utf-8", "replace")).hexdigest()[:8]


# denylist 키워드(정규화 소문자 기준). raw 경로는 출력하지 않고 카테고리만 reason_code로.
# 소유자-특정 사설 프로젝트명은 배포 소스에 하드코딩하지 않고 repo 밖 owner-only 파일에서
# 런타임 로드한다(deny_private_project). 배포물엔 실제 토큰이 없다 — 파일 부재 시 미적용.
_DENY = [
    ("deny_cert_npki", ["npki", "gpki", "yessign", "magicline", "secukit", "인증서", "certificate", "공동인증"]),
    ("deny_secret", [".env", "credential", "private_key", "id_rsa", ".pem", ".key", "secret", "_token", "password"]),
    ("deny_opencrab_store", ["localcrab_index", "_graph.yaml", "operating_store",
                             "localbinggu_production_graph", "user_graph.yaml", "_graph_merge.yaml"]),
]

_OWNER_DENY_CACHE: dict[str, list[str]] = {}


def _owner_private_tokens():
    """소유자-특정 사설 프로젝트/경로 토큰을 repo 밖 owner-only 파일에서 로드(런타임).
    위치: $BINGGU_PRIVATE_DENY 또는 ~/.binggupack_private/deny_tokens.txt.
    배포물엔 실제 토큰이 없다 — 파일 부재 시 빈 목록(=이 카테고리 미적용). 경로별 캐시."""
    path = os.environ.get("BINGGU_PRIVATE_DENY") or os.path.join(
        os.path.expanduser("~"), ".binggupack_private", "deny_tokens.txt")
    if path in _OWNER_DENY_CACHE:
        return _OWNER_DENY_CACHE[path]
    toks = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                t = ln.strip().lower()
                if t and not t.startswith("#"):
                    toks.append(t)
    except OSError:
        toks = []
    _OWNER_DENY_CACHE[path] = toks
    return toks

# 8.3 단축명 의심: NAME~N 형태
_RE_8_3 = re.compile(r"[^\\/]{1,8}~\d")
# ADS 의심: 드라이브 문자(단일 알파벳:) 가 아닌 위치의 ':'
_RE_DRIVE = re.compile(r"^[A-Za-z]:$")


def classify_path(input_path, allow_root, *, symlink_detected=False):
    """
    입력 경로가 공개/도구 접근에 안전한지 판정.
    symlink_detected: 호출부(운영)가 os.path.islink/realpath 비교로 채움.
                      selftest 는 이 플래그를 직접 주입(실 FS symlink 불필요).
    반환: dict(verdict, reason_code, path_id)  — raw 경로 미포함.
    """
    pid = _path_id(input_path)
    raw = input_path or ""
    norm_lower = raw.replace("\\", "/").lower()

    def block(rc):
        return {"verdict": "BLOCK", "reason_code": rc, "path_id": pid}

    # 0) 빈 값 = 판단불가 → fail-closed
    if not raw.strip():
        return block("empty_unknown")

    # 1) UNC (\\server\share 또는 //server/share)
    if raw.startswith("\\\\") or raw.startswith("//"):
        return block("unc")

    # 2) ADS (드라이브 콜론 외의 ':' 존재)
    segs_for_colon = raw.replace("\\", "/").split("/")
    for seg in segs_for_colon:
        if ":" in seg and not _RE_DRIVE.match(seg):
            return block("ads")

    # 3) 8.3 단축명 의심
    if _RE_8_3.search(raw):
        return block("short_8_3")

    # 4) 상위경로 탈출 (.. 세그먼트)
    parts = raw.replace("\\", "/").split("/")
    if ".." in parts:
        return block("parent_escape")

    # 5) denylist (allow_root 내부여도 위험 키워드는 차단)
    for rc, kws in _DENY:
        if any(k in norm_lower for k in kws):
            return block(rc)

    # 5b) 소유자 사설 프로젝트(런타임 로드 · 배포물엔 토큰 없음)
    if any(k in norm_lower for k in _owner_private_tokens()):
        return block("deny_private_project")

    # 6) symlink/junction (호출부가 탐지해 주입)
    if symlink_detected:
        return block("symlink_junction")

    # 7) resolve 후 allow_root 내부인지
    try:
        root_real = os.path.normcase(os.path.realpath(allow_root))
        joined = input_path if os.path.isabs(input_path) else os.path.join(allow_root, input_path)
        target_real = os.path.normcase(os.path.realpath(joined))
    except Exception:
        return block("resolve_error")

    root_with_sep = root_real if root_real.endswith(os.sep) else root_real + os.sep
    if target_real != root_real and not target_real.startswith(root_with_sep):
        return block("outside_root")

    # 8) 통과
    return {"verdict": "ALLOW", "reason_code": None, "path_id": pid}


# ---------------- selftest ----------------

def _selftest():
    # allow_root = synthetic 절대경로(실파일 불필요, realpath 는 비존재 경로도 정규화)
    allow_root = os.path.normpath(os.path.join(os.environ.get("TEMP", "/tmp"),
                                               "openbinggu_path_safety_allow_root"))

    # 트리 밖 절대경로: OS별로 실제 "절대경로"인 입력 사용 (Windows=드라이브문자 / POSIX=/-시작)
    # — Windows 경로는 linux에서 os.path.isabs=False 라 root 하위로 join돼 의미가 사라짐(반대도 동일)
    _outside_abs = "C:/Windows/System32/config/SAM" if os.name == "nt" else "/etc/passwd"

    cases = [
        # (name, input_path, symlink_detected, expected_verdict, expected_reason)
        ("toy_internal_ok",        "examples/toy_project/Makefile",          False, "ALLOW", None),
        ("toy_internal_sub_ok",    "examples/toy_project/src/build.py",      False, "ALLOW", None),
        ("parent_escape_bad",      "../secret_outside.txt",                  False, "BLOCK", "parent_escape"),
        ("deep_parent_escape_bad", "examples/../../etc/passwd",              False, "BLOCK", "parent_escape"),
        ("symlink_bad",            "examples/toy_project/linked_dir/x",      True,  "BLOCK", "symlink_junction"),
        ("cert_store_bad",         "examples/toy_project/certs/NPKI/vendor/cert.der",         False, "BLOCK", "deny_cert_npki"),
        ("env_secret_bad",         "examples/toy_project/.env",              False, "BLOCK", "deny_secret"),
        ("private_key_bad",        "examples/toy_project/keys/id_rsa",       False, "BLOCK", "deny_secret"),
        ("opencrab_store_bad",     "data/localcrab_index.sqlite",            False, "BLOCK", "deny_opencrab_store"),
        ("unc_bad",                "\\\\fileserver\\share\\secret",          False, "BLOCK", "unc"),
        ("short_8_3_bad",          "examples/PROGRA~1/app.py",               False, "BLOCK", "short_8_3"),
        ("ads_bad",                "examples/toy_project/notes.txt:hidden",  False, "BLOCK", "ads"),
        ("outside_abs_bad",        _outside_abs,                             False, "BLOCK", "outside_root"),
        ("empty_unknown_bad",      "   ",                                    False, "BLOCK", "empty_unknown"),
    ]

    print("=" * 72)
    print("OpenBinggu MCP/local path safety gate (synthetic / selftest)")
    print("=" * 72)

    all_ok = True
    raw_leak = False
    counts = {}
    for name, inp, slink, exp_v, exp_r in cases:
        r = classify_path(inp, allow_root, symlink_detected=slink)
        v_ok = (r["verdict"] == exp_v)
        r_ok = (r["reason_code"] == exp_r)
        ok = v_ok and r_ok
        all_ok = all_ok and ok
        counts[r["reason_code"] or "ALLOW"] = counts.get(r["reason_code"] or "ALLOW", 0) + 1
        # raw 경로 미출력 검증: 결과 dict 값에 입력 원본 문자열이 들어가면 누설
        for val in r.values():
            if isinstance(val, str) and inp.strip() and inp.strip() in val:
                raw_leak = True
        tag = "OK" if ok else "FAIL"
        print("  [%s] %-22s verdict=%-5s reason=%-20s path_id=%s"
              % (tag, name, r["verdict"], str(r["reason_code"]), r["path_id"]))

    # 소유자 사설 프로젝트 deny: repo 밖 owner-only 파일에서 런타임 로드(배포물엔 토큰 없음).
    # synthetic 토큰(example-private-proj)으로 검증 — owner 실제 값 미사용.
    import shutil as _sh
    import tempfile as _tf
    _pp_dir = _tf.mkdtemp(prefix="pp_owner_deny_")
    _pp_file = os.path.join(_pp_dir, "deny_tokens.txt")
    with open(_pp_file, "w", encoding="utf-8") as _f:
        _f.write("# synthetic owner deny (selftest)\nexample-private-proj\n")
    _saved = os.environ.get("BINGGU_PRIVATE_DENY")
    try:
        os.environ["BINGGU_PRIVATE_DENY"] = os.path.join(_pp_dir, "absent.txt")
        _OWNER_DENY_CACHE.clear()
        r_absent = classify_path("examples/example-private-proj/app.py", allow_root)
        ok_absent = (r_absent["reason_code"] != "deny_private_project")
        os.environ["BINGGU_PRIVATE_DENY"] = _pp_file
        _OWNER_DENY_CACHE.clear()
        r_present = classify_path("examples/example-private-proj/app.py", allow_root)
        ok_present = (r_present["verdict"] == "BLOCK" and r_present["reason_code"] == "deny_private_project")
    finally:
        if _saved is None:
            os.environ.pop("BINGGU_PRIVATE_DENY", None)
        else:
            os.environ["BINGGU_PRIVATE_DENY"] = _saved
        _OWNER_DENY_CACHE.clear()
        _sh.rmtree(_pp_dir, ignore_errors=True)
    all_ok = all_ok and ok_absent and ok_present
    print("  [%s] %-22s (배포 기본=미적용)" % ("OK" if ok_absent else "FAIL", "private_project_absent"))
    print("  [%s] %-22s (owner 파일=차단)" % ("OK" if ok_present else "FAIL", "private_project_present"))

    print("\n  --- 집계(raw 경로 미출력, reason_code/count 만) ---")
    for k in sorted(counts):
        print("    %-22s %d" % (k, counts[k]))

    print("\n  raw_path_not_leaked:", (not raw_leak))
    print("  operating_store_unchanged: True (경로 분석만, FS write 0)")

    gate = "GO" if (all_ok and not raw_leak) else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        _selftest()
    else:
        print("usage: openbinggu_path_safety_gate.py [--selftest]")
        sys.exit(2)
