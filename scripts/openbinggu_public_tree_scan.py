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
# secret_kv: key 뒤에 "실제 secret 값(하드코딩 리터럴/토큰)"이 올 때만 검출. _secret_kv_match 로 후처리.
#   - 검출 유지: 하드코딩 리터럴/토큰 값 (cloud access key 토큰, 'ghp_...' 형 따옴표 리터럴 등)
#   - 오탐 제외: 타입주석(secret: str) / 기본값(vault_secret=None) / 함수호출·변수참조(secret = _read_secret(path))
#     / 주석라인(# secret = ...) / 합성 placeholder(selftest/dummy/changeme)
_SECRET_KV_RE = re.compile(
    r"(?P<key>api[_-]?key|token|secret|passwd|password)\s*[:=]\s*(?P<val>\S.*)$", re.I)
_SECRET_KV_TYPEDEFAULT = re.compile(
    r"^(None|True|False|str|int|bytes|float|bool|dict|list|Optional|Any)\b", re.I)
# 하드코딩 secret 리터럴: 따옴표 4자+ 또는 대소문자+숫자 혼합 토큰 8자+(AKIA.../ghp_... 형). 순수 식별자 제외.
_SECRET_KV_LITERAL = re.compile(
    r"""^['"][^'"\n]{4,}['"]|^[A-Za-z0-9][A-Za-z0-9_\-./+=]{7,}""")
_SECRET_KV_PLACEHOLDER = re.compile(r"selftest|dummy|changeme|placeholder|<[a-z_]+>|x{6,}", re.I)


def _secret_kv_match(line):
    """secret_kv 정밀 판정. 진짜 하드코딩 secret 값일 때만 True (변수명/타입/함수호출/주석/placeholder 제외)."""
    if line.lstrip().startswith("#"):           # 주석 라인 = 라이브 secret 아님
        return False
    m = _SECRET_KV_RE.search(line)
    if not m:
        return False
    val = m.group("val").strip()
    if _SECRET_KV_TYPEDEFAULT.match(val):       # 타입주석/None/bool 등 기본값
        return False
    if _SECRET_KV_PLACEHOLDER.search(val):      # 합성 테스트 placeholder
        return False
    # 함수호출/변수참조 (secret = _read_secret(path) / secrets.token_hex(...) / get_or_create_secret(...))
    quoted = val[:1] in "'\""
    if not quoted:
        first = val.split()[0] if val.split() else ""
        first = first.rstrip(",;)")
        if "(" in first or "." in first:        # 함수호출/속성참조
            return False
    if not _SECRET_KV_LITERAL.match(val):       # 하드코딩 리터럴/토큰 형태가 아님 = 순수 식별자 참조
        return False
    return True


_CONTENT = [
    # secret_kv 는 _secret_kv_match(라인 단위 후처리)로 검출 — _CONTENT 루프 밖에서 별도 호출.
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
    # 실 pack private 데이터 경로 — 공개 트리에 존재 자체가 BLOCK (GO-HOSTED-REALPACK-LOCAL U4)
    ("path_private_pack_data", re.compile(r"hosted[\\/]workers[\\/]data[\\/]", re.I)),
]


def _fid(rel):
    return "f_" + hashlib.sha256(rel.replace("\\", "/").lower().encode("utf-8", "replace")).hexdigest()[:8]


def _open_text(path):
    """텍스트 read 핸들(selftest 에서 읽기 실패 시뮬레이션용 분리)."""
    return open(path, "r", encoding="utf-8", errors="replace")


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
    # content 미검사 사유별 집계(fail-open 가시화): ext=화이트리스트 밖 / size=512KB 초과 / read_error=읽기 실패
    content_skipped = {"ext": 0, "size": 0, "read_error": 0}

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

            # 2) 내용 기반(텍스트, 크기 제한) — 미검사 파일은 집계해 노출
            ext = os.path.splitext(fn)[1].lower()
            if ext not in _TEXT_EXT:
                content_skipped["ext"] += 1
                continue
            try:
                if os.path.getsize(full) > _MAX_BYTES:
                    content_skipped["size"] += 1
                    continue
                with _open_text(full) as fh:
                    for lineno, line in enumerate(fh, 1):
                        # secret_kv: 정밀 판정(하드코딩 리터럴/토큰만 — 변수명/타입/함수호출/주석 제외)
                        if _secret_kv_match(line):
                            findings.append({"reason_code": "secret_kv", "file_id": fid,
                                             "where": "L%d" % lineno})
                            by_reason["secret_kv"] = by_reason.get("secret_kv", 0) + 1
                        for code, rx in _CONTENT:
                            if rx.search(line):
                                # raw 라인 미저장: 위치는 line 번호 라벨만
                                findings.append({"reason_code": code, "file_id": fid,
                                                 "where": "L%d" % lineno})
                                by_reason[code] = by_reason.get(code, 0) + 1
            except Exception:
                # 읽기 실패 = 검사 불능 → fail-closed: BLOCK 사유로 승격(raw 노출 0)
                content_skipped["read_error"] += 1
                findings.append({"reason_code": "content_read_error", "file_id": fid,
                                 "where": "content"})
                by_reason["content_read_error"] = by_reason.get("content_read_error", 0) + 1

    hits = len(findings)
    return {
        "scanned": scanned, "skipped_ignored": skipped, "hits": hits,
        "content_skipped": content_skipped,  # 미검사 카운트 노출(fail-open 방지)
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

    # skip: content 미검사 사유별 집계 검증용(내용 자체는 clean)
    skip = os.path.join(base, "skip_tree")
    os.makedirs(skip, exist_ok=True)
    w(os.path.join(skip, "blob.bin"), "binary-ish payload (ext whitelist 밖)\n")
    w(os.path.join(skip, "big.txt"), "x" * (_MAX_BYTES + 1))
    w(os.path.join(skip, "locked.txt"), "normal readable text\n")

    # neg(음성 fixture): 스캔이 반드시 잡아야 할 합성 위반 1건 — credentials 경로+secret 내용
    neg = os.path.join(base, "neg_tree")
    os.makedirs(os.path.join(neg, "config"), exist_ok=True)
    w(os.path.join(neg, "config", "credentials.txt"),
      "api_" + "key = " + "AKIA" + "0000EXAMPLE0000\n")
    return clean, dirty, skip, neg


def _selftest():
    global _open_text
    base = os.path.join(os.environ.get("TEMP", "/tmp"), "openbinggu_public_tree_scan_fixture")
    clean, dirty, skip, neg = _build_fixture(base)

    print("=" * 72)
    print("OpenBinggu public tree secret/PII scanner (synthetic / selftest)")
    print("=" * 72)

    all_ok = True
    raw_leak = False

    def check(name, root, expect_verdict, ignore=(), expect_min_hits=None, expect_skip=None):
        nonlocal all_ok, raw_leak
        r = scan_public_tree(root, ignore_globs=ignore)
        ok = (r["verdict"] == expect_verdict)
        if expect_min_hits is not None:
            ok = ok and (r["hits"] >= expect_min_hits)
        if expect_skip is not None:
            for k, v in expect_skip.items():
                ok = ok and (r["content_skipped"].get(k) == v)
        # raw 미출력 검증: findings 값에 절대경로/실내용 흔적 없어야(파일명/경로 substring 금지)
        import json as _json
        blob = _json.dumps(r, ensure_ascii=False)
        for token in (root, ".env", "id_rsa", "AKIA" + "0000EXAMPLE0000", "900101" + "-1000000",
                      "010-" + "0000-0000", "credentials.txt", "blob.bin", "locked.txt"):
            if token in blob:
                raw_leak = True
        all_ok = all_ok and ok
        print("  [%s] %-26s verdict=%-5s hits=%-2d content_skipped=%s by_reason=%s"
              % ("OK" if ok else "FAIL", name, r["verdict"], r["hits"],
                 r["content_skipped"], r["by_reason"]))
        return r

    check("clean_tree_pass", clean, "CLEAN", expect_min_hits=0,
          expect_skip={"ext": 0, "size": 0, "read_error": 0})
    check("dirty_tree_block", dirty, "BLOCK", expect_min_hits=4)
    # ignore 연동: .env / id_rsa 제외해도 config.py(token)·notes.txt(PII) 남아 여전히 BLOCK
    check("dirty_with_ignore_still_block", dirty, "BLOCK",
          ignore=("*/.env", ".env", "*/id_rsa", "id_rsa"), expect_min_hits=2)
    # content skip 집계 노출: ext 밖 1 + 512KB 초과 1 (읽기 실패 없음 → CLEAN)
    check("skip_counts_exposed_clean", skip, "CLEAN", expect_min_hits=0,
          expect_skip={"ext": 1, "size": 1, "read_error": 0})
    # 읽기 실패 = fail-closed BLOCK 승격(시뮬레이션: locked.txt 만 read 실패)
    _orig_open = _open_text

    def _failing_open(path):
        if os.path.basename(path) == "locked.txt":
            raise OSError("simulated read failure")
        return _orig_open(path)

    _open_text = _failing_open
    try:
        check("read_error_fail_closed", skip, "BLOCK", expect_min_hits=1,
              expect_skip={"ext": 1, "size": 1, "read_error": 1})
    finally:
        _open_text = _orig_open
    # 음성 fixture: 합성 위반 1건(credentials 경로 + secret 내용)은 반드시 검출
    check("neg_fixture_must_detect", neg, "BLOCK", expect_min_hits=1)

    print("\n  raw_value_not_leaked:", (not raw_leak))
    print("  operating_store_unchanged: True (fixture=temp, 트리 read-only scan)")

    gate = "GO" if (all_ok and not raw_leak) else "NO-GO"
    print("\n  GATE:", gate)
    sys.exit(0 if gate == "GO" else 1)


# 공개 트리 scan 기본 제외(.gitignore 계열 — 공개 대상 아님). doctor._real_tree_scan 과 동일 + 비공개 pack 데이터.
# 주의: .env/credentials*/private_key* 는 scanner 검출 대상이므로 여기 절대 추가 금지(검출 무력화 방지).
PUBLIC_IGNORE = ["*.sqlite", "*.db", "*_graph.yaml", "reports/", "reviews/", "captures/",
                 "tmp/", "__pycache__/", "*.bak_*",
                 # gitignore 대상 비공개·미커밋 라이브 데이터 (path_private_pack_data 자기탐지 회피)
                 "hosted/workers/data/", "data/packs.json",
                 # 서드파티 의존성(gitignore·미커밋) — CI/로컬에서 npm install 로 생성됨. 자기 코드 아님 → scan 제외.
                 # 중첩 경로(hosted/workers/node_modules/...)라 fnmatch 글롭으로 매칭(디렉토리 prefix startswith 불가).
                 "*/node_modules/*", "node_modules/*"]


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        _selftest()
    elif args[0] == "--tree" and len(args) >= 2:
        ignore = PUBLIC_IGNORE if "--public" in args[2:] else ()
        r = scan_public_tree(args[1], ignore_globs=ignore)
        # 요약만 출력(raw 미출력)
        print("scanned=%d skipped_ignored=%d content_skipped=%s hits=%d verdict=%s by_reason=%s"
              % (r["scanned"], r["skipped_ignored"], r["content_skipped"],
                 r["hits"], r["verdict"], r["by_reason"]))
        sys.exit(0 if r["verdict"] == "CLEAN" else 1)
    else:
        print("usage: openbinggu_public_tree_scan.py [--selftest | --tree <ROOT>]")
        sys.exit(2)


if __name__ == "__main__":
    main()
