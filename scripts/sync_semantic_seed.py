# -*- coding: utf-8 -*-
"""semantic seed SSOT 도구 (issue #52) — data → fixture 단방향 mirror + canonical + hosted 정합.

SSOT 관계:
    binggupack/data/semantic/*.jsonl   (배포 package data · 정본)
            ↓ 단방향 mirror (역동기화·mtime 자동선택 금지)
    tests/fixtures/semantic/*.jsonl     (byte-identical 파생 mirror)

canonical byte 규칙(각 정본 파일): UTF-8 · BOM 없음 · CR 바이트 없음 · LF · 마지막에 정확히 하나의
LF · 각 비어있지 않은 줄이 유효한 JSON object. + fixture == source byte-identical.

hosted 정합: sha256(binggupack/data/semantic/seed_canonical_5.jsonl)[:16] ==
    hosted/workers/src/centroids_canonical_5.json 의 seed_hash. (계약: 단일 파일·raw bytes·[:16]
    lowercase — binggu_hosted_centroid_gen.py::_seed_sha 재현). 불일치면 centroid 재산출 필요로 실패.

모드:
    --check        : source canonical + fixture byte-identical 검증. 수정 0. 실패 시 exit 1.
    --check-hosted : hosted seed_hash 정합 검증. 불일치 시 실패(자동 덮어쓰기 0).
    --write        : source(정본·canonical) → fixture 단방향 write_bytes(newline 변환 없음). fixture
                     외 파일 수정 0. 실행 후 --check 가 PASS 해야 함.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, "binggupack", "data", "semantic")
FIXTURE_DIR = os.path.join(ROOT, "tests", "fixtures", "semantic")
CENTROIDS = os.path.join(ROOT, "hosted", "workers", "src", "centroids_canonical_5.json")

SEEDS = ("seed_canonical_5.jsonl", "seed_candidates.jsonl")
HOSTED_SEED = "seed_canonical_5.jsonl"  # hosted seed_hash 입력(단일·canonical_5 · candidates 제외)

_BOM = b"\xef\xbb\xbf"


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def canonical_violations(b: bytes) -> list[str]:
    """canonical byte 규칙 위반 목록. 빈 리스트 = canonical."""
    v: list[str] = []
    if b.startswith(_BOM):
        v.append("utf8-bom")
    if b"\r" in b:
        v.append("cr-byte")
    try:
        b.decode("utf-8")
    except UnicodeDecodeError:
        v.append("non-utf8")
    if b and not b.endswith(b"\n"):
        v.append("no-final-lf")
    if b.endswith(b"\n\n"):
        v.append("multiple-final-lf")
    for i, line in enumerate(b.split(b"\n"), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line.decode("utf-8", errors="replace"))
        except (ValueError, json.JSONDecodeError):
            v.append("invalid-json:line%d" % i)
            continue
        if not isinstance(obj, dict):
            v.append("not-json-object:line%d" % i)
    return v


def check(verbose: bool = True) -> bool:
    ok = True
    for name in SEEDS:
        dp = os.path.join(DATA_DIR, name)
        fp = os.path.join(FIXTURE_DIR, name)
        if not os.path.exists(dp):
            ok = False
            verbose and print("FAIL: source 누락 %s" % name)
            continue
        if not os.path.exists(fp):
            ok = False
            verbose and print("FAIL: fixture 누락 %s" % name)
            continue
        db, fb = _read(dp), _read(fp)
        dv = canonical_violations(db)
        if dv:
            ok = False
            verbose and print("FAIL: source canonical 위반 %s: %s" % (name, dv))
        # fixture 도 canonical 이어야(mirror). 단순 byte identity 만이 아니라 source canonicality 별도 검사.
        if db != fb:
            ok = False
            verbose and print("FAIL: fixture drift %s (data %s != fixture %s)"
                              % (name, _sha(db)[:16], _sha(fb)[:16]))
    return ok


def check_hosted(verbose: bool = True) -> bool:
    dp = os.path.join(DATA_DIR, HOSTED_SEED)
    if not os.path.exists(dp) or not os.path.exists(CENTROIDS):
        verbose and print("FAIL: hosted 정합 검사 대상 누락")
        return False
    recomputed = _sha(_read(dp))[:16]
    stored = json.loads(_read(CENTROIDS).decode("utf-8")).get("seed_hash")
    if recomputed != stored:
        verbose and print("FAIL: hosted seed_hash 불일치 — recomputed %s != centroids %s "
                          "→ centroid 재산출 필요(자동 덮어쓰기 안 함)" % (recomputed, stored))
        return False
    verbose and print("OK: hosted seed_hash 정합 %s" % recomputed)
    return True


def write(verbose: bool = True) -> bool:
    # 정본(source)이 canonical 이어야 fixture 로 복제한다(정본 오염 방지).
    for name in SEEDS:
        dv = canonical_violations(_read(os.path.join(DATA_DIR, name)))
        if dv:
            verbose and print("FAIL: source canonical 위반 — --write 중단 %s: %s" % (name, dv))
            return False
    for name in SEEDS:
        dp = os.path.join(DATA_DIR, name)
        fp = os.path.join(FIXTURE_DIR, name)
        with open(fp, "wb") as f:      # write_bytes: OS newline 변환 없음(Windows 안전)
            f.write(_read(dp))
        verbose and print("wrote: %s -> %s" % (name, os.path.relpath(fp, ROOT)))
    return check(verbose)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="semantic seed SSOT mirror/check (issue #52)")
    ap.add_argument("--check", action="store_true", help="canonical + byte-identity 검증(수정 0)")
    ap.add_argument("--check-hosted", action="store_true", help="hosted seed_hash 정합 검증")
    ap.add_argument("--write", action="store_true", help="source → fixture 단방향 복제")
    a = ap.parse_args(argv)

    if a.write:
        ok = write()
        print("GATE:", "GO" if ok else "FAIL")
        return 0 if ok else 1
    if a.check_hosted and not a.check:
        return 0 if check_hosted() else 1
    ok = check()
    if a.check_hosted:
        ok = check_hosted() and ok
    print("GATE:", "GO" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
