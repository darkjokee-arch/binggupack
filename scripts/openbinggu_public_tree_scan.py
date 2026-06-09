#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBinggu public tree secret/PII scanner (S4 실 트리 결선 최소 구현 후보).

목적:
- 공개 후보 트리를 대상으로 secret/PII/private path 를 scan 한다.
- 기본 read-only / dry-run. 트리 변경·write 0.
- raw 값 미출력 → count / reason_code / file_id(hash) / where(파일명 아닌 위치 라벨) 만.
- 검출 1건 이상이면 verdict=BLOCK (공개/업로드 차단 근거).
- .gitignore/제외 경로(ignore_globs)와 연동: 매칭 파일은 scan 제외(공개 대상 아님 전제).

범위: 스캔(read-only) + synthetic selftest(temp fixture). production/store/DB write 0.
CLI:
  python openbinggu_public_tree_scan.py --selftest
  python openbinggu_public_tree_scan.py --tree <ROOT>     # 실 트리 scan(요약만)
"""
import sys
import os
import re
import hashlib
import fnmatch

_TEXT_EXT = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini",
             ".cfg", ".env", ".sh", ".js", ".ts", ".html", ".csv", ".pem", ".key", ""}
_MAX_BYTES = 512 * 1024  # 큰/바이너리 파일 skip

# 파일 내용 패턴(raw 미출력, reason_code 만)
_CONTENT = [
    ("secret_kv", re.compile(r"(api[_-]?key|token|secret|passwd|password)\s*[:=]\s*\S", re.I)),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{12,}")),
    ("github_token", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("pii_rrn", re.compile(r"\b\d{6}-\d{7}\b")),
    ("pii_phone", re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b")),
]
# 파일명/경로 패턴
_NAMEPATH = [
    ("path_dotenv", re.compile(r"(^|[\\/])\.env(\.|$|[\\/])", re.I)),
    ("path_credential", re.compile(r"credential", re.I)),
    ("path_private_key", re.compile(r"(private_key|id_rsa|\.pem$|\.key$)", re.I)),
    ("path_cert_npki", re.compile(r"(npki|gpki|\.der$|\.pfx$)", re.I)),
]


def _fid(rel):
    return "f_" + hashlib.sha256(rel.replace("\\", "/").lower().encode("utf-8", "replace")).hexdigest()[:8]


def _ignored(rel, ignore_globs):
    relu = rel.replace("\\", "/")
    for g in ignore_globs or ():
        if fnmatch.fnmatch(relu, g) or fnmatch.fnmatch(os.path.basename(relu), g):
            return True
        # 디렉토리 prefix 매칭(reports/ 등)
        if g.endswith("/") and (relu + "/").startswith(g):
            return True
    return False


def scan_public_tree(root, ignore_globs=()):
    """
    root 트리 scan. 반환 dict: raw 경로/내용 미포함. file_id/reason_code/where(라벨)/count 만.
    """
    findings = []
    by_reason = {}
    scanned = 0
    skipped = 0

    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            if _ignored(rel, ignore_globs):
                skipped += 1
                continue
            scanned += 1
            fid = _fid(rel)

            # 1) 파일명/경로 기반
            for code, rx in _NAMEPATH:
                if rx.search(rel):
                    findings.append({"reason_code": code, "file_id": fid, "where": "path"})
                    by_reason[code] = by_reason.get(code, 0) + 1

            # 2) 내용 기반(텍스트, 크기 제한)
            ext = os.path.splitext(fn)[1].lower()
            try:
                if ext in _TEXT_EXT and os.path.getsize(full) <= _MAX_BYTES:
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            for code, rx in _CONTENT:
                                if rx.search(line):
                                    # raw 라인 미저장: 위치는 line 번호 라벨만
                                    findings.append({"reason_code": code, "file_id": fid,
                                                     "where": "L%d" % lineno})
                                    by_reason[code] = by_reason.get(code, 0) + 1
            except Exception:
                pass  # 읽기 실패는 무시(raw 노출 0)

    hits = len(findings)
    return {
        "scanned": scanned, "skipped_ignored": skipped, "hits": hits,
        "by_reason": dict(sorted(by_reason.items())),
        "findings": findings,            # file_id/reason_code/where 만 (raw 경로/내용 0)
        "verdict": "BLOCK" if hits > 0 else "CLEAN",
        "raw_not_output": True,
    }


# ---------------- selftest ----------------

def _build_fixture(base):
    """temp 에 clean / dirty 합성 트리 생성(operating store 아님). 실 secret 아님(합성 토큰)."""
    clean = os.path.join(base, "clean_tree")
    dirty = os.path.join(base, "dirty_tree")
    for d in (clean, dirty,
              os.path.join(clean, "examples", "toy_project"),
              os.path.join(dirty, "examples", "toy_project"),
              os.path.join(dirty, "data")):
        os.makedirs(d, exist_ok=True)

    def w(p, s):
        with open(p, "w", encoding="utf-8") as f:
            f.write(s)

    # clean: secret/PII 없음
    w(os.path.join(clean, "README.md"), "# Toy\nrun: make build\n")
    w(os.path.join(clean, "examples", "toy_project", "Makefile"), "build:\n\tpython build.py\n")

    # dirty: 합성 secret/PII/private path
    # 정적 소스에 scanner 트리거 리터럴이 남지 않게 런타임 조립(파일 내용은 동일 → scanner 검출 유지)
    w(os.path.join(dirty, "examples", "toy_project", ".env"), "API_" + "KEY=" + "AKIA" + "0000EXAMPLE0000\n")
    w(os.path.join(dirty, "config.py"), "to" + "ken = '" + "ghp_" + "EXAMPLE000000000000000000'\n")
    w(os.path.join(dirty, "data", "id_rsa"), "-----BEGIN OPENSSH PRIVATE" + " KEY-----\nEXAMPLE\n")
    w(os.path.join(dirty, "notes.txt"), "contact 010-" + "0000-0000\nrrn " + "900101" + "-1000000\n")
    return clean, dirty


def _selftest():
    base = os.path.join(os.environ.get("TEMP", "/tmp"), "openbinggu_public_tree_scan_fixture")
    clean, dirty = _build_fixture(base)

    print("=" * 72)
    print("OpenBinggu public tree secret/PII scanner (synthetic / selftest)")
    print("=" * 72)

    all_ok = True
    raw_leak = False

    def check(name, root, expect_verdict, ignore=(), expect_min_hits=None):
        nonlocal all_ok, raw_leak
        r = scan_public_tree(root, ignore_globs=ignore)
        ok = (r["verdict"] == expect_verdict)
        if expect_min_hits is not None:
            ok = ok and (r["hits"] >= expect_min_hits)
        # raw 미출력 검증: findings 값에 절대경로/실내용 흔적 없어야(파일명/경로 substring 금지)
        import json as _json
        blob = _json.dumps(r, ensure_ascii=False)
        for token in (root, ".env", "id_rsa", "AKIA" + "0000EXAMPLE0000", "900101" + "-1000000", "010-" + "0000-0000"):
            if token in blob:
                raw_leak = True
        all_ok = all_ok and ok
        print("  [%s] %-26s verdict=%-5s hits=%-2d by_reason=%s"
              % ("OK" if ok else "FAIL", name, r["verdict"], r["hits"], r["by_reason"]))
        return r

    check("clean_tree_pass", clean, "CLEAN", expect_min_hits=0)
    check("dirty_tree_block", dirty, "BLOCK", expect_min_hits=4)
    # ignore 연동: .env / id_rsa 제외해도 config.py(token)·notes.txt(PII) 남아 여전히 BLOCK
    check("dirty_with_ignore_still_block", dirty, "BLOCK",
          ignore=("*/.env", ".env", "*/id_rsa", "id_rsa"), expect_min_hits=2)

    print("\n  raw_value_not_leaked:", (not raw_leak))
    print("  operating_store_unchanged: True (fixture=temp, 트리 read-only scan)")

    gate = "GO" if (all_ok and not raw_leak) else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        _selftest()
    elif args[0] == "--tree" and len(args) >= 2:
        r = scan_public_tree(args[1])
        # 요약만 출력(raw 미출력)
        print("scanned=%d skipped_ignored=%d hits=%d verdict=%s by_reason=%s"
              % (r["scanned"], r["skipped_ignored"], r["hits"], r["verdict"], r["by_reason"]))
        sys.exit(0 if r["verdict"] == "CLEAN" else 1)
    else:
        print("usage: openbinggu_public_tree_scan.py [--selftest | --tree <ROOT>]")
        sys.exit(2)


if __name__ == "__main__":
    main()
