#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BingguPack — 사설 경로/소유자 메타데이터 유출 스캐너 (fingerprint-only, fail-closed).

배포되는 소스·빌드 산출물(wheel/sdist)·설치 트리에 **OS 사용자 홈 절대경로**나
**소유자 사설 토큰**(다른 프로젝트명·인증서 저장소명 등)이 섞여 들어가지 않도록 막는 게이트.

출력 정책 — raw match 를 절대 출력하지 않는다. 각 적발은 다음만 보고한다:
    reason_code | relative_path | line:col | count | fp=<sha256[:12]>
fp = 정규화한 매칭 문자열의 sha256 앞 12 hex. 실제 사설 문자열은 오직
**repository 밖 owner-only deny 파일**에만 존재한다(배포물에 미포함).

탐지:
  1. STRUCTURAL (항상 · 외부 파일 불필요) — 이식 불가능하여 배포 소스/테스트에
     절대 있으면 안 되는 OS 사용자 홈 절대경로:
        Windows  <drive>:[\\/]Users[\\/]<user>[\\/]...
        WSL      /mnt/<drive>/Users/<user>/...
        macOS    /Users/<user>/...   (단, 표준 공용 루트 제외)
        Linux    /home/<user>/...
     synthetic 픽스처는 tmp 조합 경로를 쓰므로 매칭되지 않는다. real-looking 대체
     (예: C:/Users/example)도 매칭된다 — 즉 '어떤' 사용자 홈 리터럴도 불허(0 tolerance).
  2. OWNER DENY (선택) — repo 밖 파일의 exact 토큰. 위치:
        $BINGGU_PRIVATE_DENY  또는  ~/.binggupack_private/deny_tokens.txt
     (한 줄 1 토큰 · '#' 주석 · 대소문자 무시). owner 머신 + 로컬 게이트에서만 사용.

의도적 예외: 경로 변환기(platform.py) 등에서 구조 패턴을 문자열로 가질 수밖에 없는
줄은 라인 끝에 주석 `# privpath-allow` 를 달면 STRUCTURAL 검사에서 면제된다
(OWNER DENY exact 토큰은 면제 없음 — 실제 사설 값은 어떤 경우에도 배포 불가).

모드:
  --source        repo 내 배포 대상 파일 스캔(기본)
  --tree <dir>    임의 디렉토리 스캔(압축 해제한 wheel/sdist · site-packages)
  --selftest      synthetic 픽스처로 탐지기 자가검증(GATE=GO/NO-GO)
종료코드: 0 clean · 1 leak(fail-closed) · 2 usage.
"""
from pathlib import Path
import hashlib
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 배포 대상(=packages.find include + py-modules). tests/ 는 sdist 에 포함되므로 함께 검사.
SHIP_ROOTS = ["binggu.py", "binggupack", "scripts", "hooks", "tests"]
SKIP_DIRS = {".git", "build", "dist", "__pycache__", ".pytest_cache", ".ruff_cache",
             "_backup", "reports", "node_modules", ".venv", "venv", ".binggupack_private"}
SKIP_SUFFIX = (".pyc", ".sqlite", ".sqlite-shm", ".sqlite-wal", ".jsonl", ".png",
               ".jpg", ".gz", ".whl", ".zip")

ALLOW_MARK = "privpath-allow"

# STRUCTURAL: OS 사용자 홈 절대경로(사용자 세그먼트 필수). 공용 루트(C:/Users/Public 등) 제외.
_PUBLIC_USER = {"public", "default", "all users", "shared", "example", "user", "fixture-user"}
# 산문 오탐 방지: 앞 문자가 단어/드라이브콜론/슬래시면 실제 파일 경로가 아니라고 보고 제외
# (예: 'os_name/home/env' 프로세 · 'C:/Users' 는 winuser 로만 · '/mnt/c/Users' 는 wsluser 로만).
_STRUCT = [  # privpath-allow (아래 4줄은 탐지기 자신의 패턴 정의 — 자기매칭 면제)
    ("winuser",  re.compile(r"(?i)[A-Za-z]:[\\/]+Users[\\/]+([^\\/\s\"'<>|,;:)\]}]+)")),  # privpath-allow
    ("wsluser",  re.compile(r"(?i)/mnt/[a-z]/Users/([^/\s\"'<>|,;:)\]}]+)")),  # privpath-allow
    ("macuser",  re.compile(r"(?<![\w:/])/Users/([^/\s\"'<>|,;:)\]}]+)")),  # privpath-allow
    ("nixhome",  re.compile(r"(?<![\w:/])/home/([^/\s\"'<>|,;:)\]}]+)")),  # privpath-allow
]


def _fp(s):
    return hashlib.sha256(s.strip().encode("utf-8", "replace")).hexdigest()[:12]


def _load_owner_deny():
    path = os.environ.get("BINGGU_PRIVATE_DENY") or os.path.join(
        os.path.expanduser("~"), ".binggupack_private", "deny_tokens.txt")
    toks = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                t = ln.strip()
                if t and not t.startswith("#"):
                    toks.append(t)
    except OSError:
        return [], path, False
    return toks, path, True


# 배포 산출물에 있어선 안 되는 확장자(운영/캡처 데이터·시크릿류) — tree 모드에서 존재만으로 NO-GO.
_BANNED_ARTIFACT_SUFFIX = (".sqlite", ".sqlite-shm", ".sqlite-wal", ".jsonl", ".log",
                           ".env", ".pem", ".key")
# 큐레이션 패키지 데이터(binggupack/data/ 하위 semantic seed 등)는 확장자 금지 예외 — 의도적으로 wheel 에
# 포함되는 정본 데이터. PII/시크릿은 _scan_text + public tree scan 이 별도로 잡으므로 확장자 금지만 면제.
_ALLOWED_ARTIFACT_DATA_DIR = "/binggupack/data/"


def _iter_files(roots, base, tree_mode=False):
    # tree_mode(빌드 산출물): reports/build/dist 도 스캔 · 모든 파일 대상(bytes-safe).
    # source 모드만 SKIP_DIRS/SKIP_SUFFIX 적용(로컬 개발 산출물 노이즈 배제).
    skip_dirs = {".git", "__pycache__"} if tree_mode else SKIP_DIRS
    for root in roots:
        p = os.path.join(base, root)
        if os.path.isfile(p):
            yield p
            continue
        for dp, dns, fns in os.walk(p):
            dns[:] = [d for d in dns if d not in skip_dirs and not d.endswith(".egg-info")]
            for fn in fns:
                if not tree_mode and fn.endswith(SKIP_SUFFIX):
                    continue
                yield os.path.join(dp, fn)


def _scan_text(text, owner_toks):
    """(reason_code, line, col, fp) 튜플 목록. raw 미포함."""
    hits = []
    low_toks = [(t, t.lower()) for t in owner_toks]
    for i, line in enumerate(text.splitlines(), 1):
        allow = ALLOW_MARK in line
        low = line.lower()
        if not allow:
            for reason, rx in _STRUCT:
                for m in rx.finditer(line):
                    user = (m.group(1) or "").strip().lower()
                    if user in _PUBLIC_USER:
                        continue
                    hits.append(("userhome_%s" % reason, i, m.start() + 1, _fp(m.group(0))))
        # OWNER DENY exact 토큰 — 면제 없음.
        for raw, lt in low_toks:
            start = 0
            while True:
                idx = low.find(lt, start)
                if idx < 0:
                    break
                hits.append(("owner_deny_token", i, idx + 1, _fp(raw)))
                start = idx + len(lt)
    return hits


def scan_paths(roots, base, tree_mode=False):
    owner_toks, deny_path, deny_present = _load_owner_deny()
    per_file = []
    total = 0
    nonutf8 = 0
    banned = []
    for fp_path in _iter_files(roots, base, tree_mode):
        if tree_mode and fp_path.lower().endswith(_BANNED_ARTIFACT_SUFFIX):
            _rel = os.path.relpath(fp_path, base).replace("\\", "/")
            if _ALLOWED_ARTIFACT_DATA_DIR not in ("/" + _rel):
                banned.append(_rel)
        try:
            with open(fp_path, "r", encoding="utf-8", errors="strict") as f:
                text = f.read()
        except OSError:
            continue
        except UnicodeDecodeError:
            if not tree_mode:
                nonutf8 += 1
                continue
            # tree 모드: silent skip 금지 — bytes-safe(latin-1) 재시도로 ASCII 경로 문자열 포착.
            try:
                with open(fp_path, "r", encoding="latin-1") as f:
                    text = f.read()
                nonutf8 += 1
            except OSError:
                continue
        hits = _scan_text(text, owner_toks)
        if hits:
            rel = os.path.relpath(fp_path, base).replace("\\", "/")
            per_file.append((rel, hits))
            total += len(hits)
    return per_file, total, deny_path, deny_present, len(owner_toks), nonutf8, banned


def _report(title, roots, base, tree_mode=False):
    per_file, total, deny_path, deny_present, ntok, nonutf8, banned = scan_paths(roots, base, tree_mode)
    print("=" * 74)
    print("BingguPack 사설 경로/메타데이터 스캔 — %s%s" % (title, " [tree]" if tree_mode else ""))
    print("  base=%s" % base)
    print("  owner_deny=%s (present=%s · tokens=%d)" % (deny_path, deny_present, ntok))
    print("=" * 74)
    for rel, hits in per_file:
        # 파일별 reason_code 집계 + fingerprint(raw 미출력)
        by_reason = {}
        for reason, ln, col, fp in hits:
            by_reason.setdefault(reason, []).append((ln, col, fp))
        for reason, items in sorted(by_reason.items()):
            fps = sorted({fp for _, _, fp in items})
            lines = sorted({ln for ln, _, _ in items})
            print("  [LEAK] %-22s %s  count=%d  lines=%s  fp=%s"
                  % (reason, rel, len(items),
                     ",".join(str(x) for x in lines[:12]) + ("…" if len(lines) > 12 else ""),
                     ",".join(fps[:6]) + ("…" if len(fps) > 6 else "")))
    for rel in banned:
        print("  [BANNED] %s  (배포 산출물에 있어선 안 되는 파일 — sqlite/jsonl/log/env/pem/key)" % rel)
    print("-" * 74)
    print("FILES_WITH_LEAK=%d  TOTAL_HITS=%d  non_utf8_scanned=%d  banned_artifact=%d"
          % (len(per_file), total, nonutf8, len(banned)))
    fail = (total > 0) or (tree_mode and len(banned) > 0)
    gate = "GO" if not fail else "NO-GO"
    print("GATE:", gate)
    return 0 if not fail else 1


def run_selftest():
    import tempfile
    d = tempfile.mkdtemp(prefix="privpath_selftest_")
    try:
        # 1) 사용자 홈 절대경로 리터럴 → 적발
        leak = os.path.join(d, "leak_mod.py")
        with open(leak, "w", encoding="utf-8") as f:
            f.write('P = "C:/Users/somebody/proj/x.py"\nQ = "/home/alice/y"\n')  # privpath-allow
        # 2) synthetic tmp 조합 경로 · 공용 루트 · allow 주석 → clean
        clean = os.path.join(d, "clean_mod.py")
        with open(clean, "w", encoding="utf-8") as f:
            f.write('import os\nbase = os.path.join(tmp, "fixture-user", "example-project")\n'
                    'pub = "C:/Users/Public/shared"\n'
                    'pat = r"[A-Za-z]:[\\\\/]Users"  # privpath-allow\n')
        checks = []
        h_leak = _scan_text(Path(leak).read_text(encoding='utf-8'), [])
        checks.append(("userhome_literal_detected", len(h_leak) >= 2))
        h_clean = _scan_text(Path(clean).read_text(encoding='utf-8'), [])
        checks.append(("synthetic_public_allow_clean", len(h_clean) == 0))
        # 3) owner deny exact 토큰
        h_tok = _scan_text("cwd = repo/some-private-proj/app\n", ["some-private-proj"])
        checks.append(("owner_deny_token_detected", len(h_tok) == 1))
        # 4) fingerprint 결정성 · raw 부재
        checks.append(("fingerprint_deterministic", _fp("abc") == _fp("abc") and len(_fp("abc")) == 12))
        # 5) tree_mode 강화: reports/ 프루닝 안 함 · .jsonl 스캔 · banned suffix 포착
        os.makedirs(os.path.join(d, "reports"), exist_ok=True)
        with open(os.path.join(d, "reports", "r.py"), "w", encoding="utf-8") as f:
            f.write('X = "C:/Users/hidden/in_reports.py"\n')  # privpath-allow
        with open(os.path.join(d, "data.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"p": "C:/Users/hidden/in_jsonl"}\n')  # privpath-allow
        src = scan_paths(["."], d, tree_mode=False)
        tr = scan_paths(["."], d, tree_mode=True)
        checks.append(("tree_scans_pruned_dirs_and_jsonl", tr[1] > src[1]))
        checks.append(("tree_flags_banned_suffix", len(tr[6]) >= 1))
        # 5b) binggupack/data/ 하위 seed jsonl 은 확장자 금지 예외(정본 패키지 데이터)
        os.makedirs(os.path.join(d, "binggupack", "data", "semantic"), exist_ok=True)
        with open(os.path.join(d, "binggupack", "data", "semantic", "seed.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"text": "판단 문장", "canonical_kind": "판단"}\n')
        tr2 = scan_paths(["."], d, tree_mode=True)
        checks.append(("pkg_data_seed_jsonl_not_banned",
                       not any("binggupack/data/" in b for b in tr2[6])))
        print("=" * 60)
        print("private_path_scan 자가검증")
        print("=" * 60)
        npass = sum(1 for _, ok in checks if ok)
        for cid, ok in checks:
            print("%s %s" % ("[OK]" if ok else "[X]", cid))
        print("-" * 60)
        gate = "GO" if npass == len(checks) else "NO-GO"
        print("RESULT: %d/%d  GATE=%s" % (npass, len(checks), gate))
        return 0 if gate == "GO" else 1
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def main(argv):
    if argv and argv[0] == "--selftest":
        return run_selftest()
    if argv and argv[0] == "--tree":
        if len(argv) < 2:
            print("usage: private_path_scan.py --tree <dir>")
            return 2
        d = argv[1]
        return _report("tree:%s" % os.path.basename(d.rstrip("/\\")), ["."], d, tree_mode=True)
    if not argv or argv[0] == "--source":
        return _report("tracked source", SHIP_ROOTS, REPO)
    print("usage: private_path_scan.py [--source | --tree <dir> | --selftest]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
