# -*- coding: utf-8 -*-
"""wheel package resource semantic seed 검증 (issue #52 · owner 6).

built wheel 안 seed bytes == repo package data == fresh-installed importlib.resources.
비교 기준은 fixture 가 아니라 **배포 정본 package resource**(binggupack.data). 설치본 resolver 가
fixture fallback 이나 repo 상대경로로 우연히 통과하지 않도록 repo 밖 cwd 의 fresh 인터프리터로 읽는다.

사용: python scripts/wheel_semantic_seed_check.py <wheel.whl> <installed-python>
"""
import hashlib
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEDS = ("seed_canonical_5.jsonl", "seed_candidates.jsonl")
_BOM = b"\xef\xbb\xbf"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canonical_ok(b: bytes) -> bool:
    # LF · BOM 없음 · CR 없음 · 마지막 정확히 하나의 LF
    return (not b.startswith(_BOM)) and (b"\r" not in b) and b.endswith(b"\n") and not b.endswith(b"\n\n")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 1:
        print("usage: wheel_semantic_seed_check.py <wheel.whl> [installed-python]")
        return 2
    wheel = argv[0]
    venv_py = argv[1] if len(argv) > 1 else sys.executable
    fail: list[str] = []

    for name in SEEDS:
        data = _read(os.path.join(ROOT, "binggupack", "data", "semantic", name))

        # 1) wheel ZIP 내부 bytes == repo package data (canonical)
        arc = "binggupack/data/semantic/" + name
        with zipfile.ZipFile(wheel) as z:
            if arc not in z.namelist():
                fail.append("wheel 내부 %s 부재" % arc)
                continue
            wb = z.read(arc)
        if _sha(wb) != _sha(data):
            fail.append("wheel != data %s (%s vs %s)" % (name, _sha(wb)[:16], _sha(data)[:16]))
        if not _canonical_ok(wb):
            fail.append("wheel seed non-canonical %s" % name)

        # 2) fresh-installed importlib.resources == data (repo 밖 cwd · package resource)
        code = ("import importlib.resources as r, sys;"
                "sys.stdout.buffer.write(r.files('binggupack.data')"
                ".joinpath('semantic/%s').read_bytes())" % name)
        out = subprocess.run([venv_py, "-c", code], capture_output=True,
                             cwd=os.path.dirname(os.path.abspath(wheel)))
        ib = out.stdout
        if out.returncode != 0 or not ib:
            fail.append("installed resource 읽기 실패 %s: %s" % (name, (out.stderr or b"")[:120]))
            continue
        if _sha(ib) != _sha(data):
            fail.append("installed resource != data %s" % name)
        if not _canonical_ok(ib):
            fail.append("installed seed non-canonical %s" % name)

    if fail:
        print("GATE: FAIL")
        for f in fail:
            print("  -", f)
        return 1
    print("GATE: GO — wheel archive == installed resource == package data (canonical LF)")
    return 0


def _read(p: str) -> bytes:
    with open(p, "rb") as f:
        return f.read()


if __name__ == "__main__":
    raise SystemExit(main())
