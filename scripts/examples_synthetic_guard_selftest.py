#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Examples synthetic-only guard selftest (Track C / v1.11.0 stage0).

목적: examples/ 하위 sample json 들이 합성(synthetic) 데이터만 포함하는지 fail-closed 로 검증.
       (a) 실 URL(http/https)        → FAIL
       (b) 실 PII(이메일/전화/주민번호 유사) → FAIL
       전부 synthetic 이면 PASS.

안전: read-only. examples/ 와 운영 home 어떤 것도 write 0. stdlib(re/json/pathlib)만.
설계: fail-closed — 매치가 하나라도 잡히면 GATE=NO-GO. 화이트리스트(reserved/synthetic)
      에 해당하는 placeholder 만 통과시켜 false positive 억제.
"""
import json
import re
import sys
from pathlib import Path

# examples 디렉터리 (이 스크립트 기준 ../examples)
REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"

# ---------- 탐지 패턴 ----------
RE_URL = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
RE_EMAIL = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
# 한국 전화: 010-1234-5678 / 02-123-4567 / 031-123-4567 등
RE_PHONE = re.compile(r"\b0\d{1,2}[-.\s]\d{3,4}[-.\s]\d{4}\b")
# 주민등록번호 유사: 123456-1234567
RE_RRN = re.compile(r"\b\d{6}[-]\d{7}\b")

# ---------- 화이트리스트 (예약/합성 placeholder — 실데이터 아님) ----------
# 호스트 기준으로 reserved/example 도메인은 통과. 그 외 http(s) URL 은 실데이터로 간주.
RESERVED_HOSTS = (
    "example.com", "example.org", "example.net", "example.edu",
    "localhost", "synthetic", "synthetic.local",
)


def _url_is_reserved(url: str) -> bool:
    # http(s)://host[/...] 에서 host 추출
    m = re.match(r"https?://([^/\s\"'<>)\]:]+)", url, re.IGNORECASE)
    if not m:
        return False
    host = m.group(1).lower()
    return any(host == h or host.endswith("." + h) for h in RESERVED_HOSTS)


def _walk_strings(obj):
    """json 객체 트리에서 key/value 문자열을 모두 yield."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                yield k
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_strings(item)
    elif isinstance(obj, str):
        yield obj


def scan_text(text: str):
    """단일 문자열에서 위반 목록 반환 [(kind, matched)]."""
    hits = []
    for u in RE_URL.findall(text):
        if not _url_is_reserved(u):
            hits.append(("real_url", u))
    for e in RE_EMAIL.findall(text):
        hits.append(("real_email", e))
    for p in RE_PHONE.findall(text):
        hits.append(("real_phone", p))
    for r in RE_RRN.findall(text):
        hits.append(("real_rrn", r))
    return hits


def scan_file(path: Path):
    """json 파일 1개 스캔. 반환 (parse_ok, hits). parse 실패는 fail-closed 로 위반 취급."""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return False, [("read_error", str(exc))]
    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        # json 깨짐 → fail-closed. raw 텍스트로라도 스캔.
        return False, [("json_parse_error", str(exc)[:80])] + scan_text(raw)
    hits = []
    for s in _walk_strings(data):
        hits.extend(scan_text(s))
    return True, hits


def run():
    print("=" * 78)
    print("Examples synthetic-only guard selftest (read-only, write 0)")
    print("=" * 78)

    if not EXAMPLES_DIR.is_dir():
        print(f"[X] examples dir not found: {EXAMPLES_DIR}")
        print("GATE: NO-GO")
        return 1

    json_files = sorted(EXAMPLES_DIR.rglob("*.json"))
    if not json_files:
        # 검증 대상 0 → fail-closed (스캔 대상이 사라진 것은 회귀로 간주)
        print(f"[X] no json files under {EXAMPLES_DIR}")
        print("GATE: NO-GO")
        return 1

    total_hits = 0
    clean_files = 0
    for f in json_files:
        rel = f.relative_to(REPO_ROOT)
        parse_ok, hits = scan_file(f)
        if not hits and parse_ok:
            clean_files += 1
            print(f"[OK] {rel}  (synthetic, clean)")
        else:
            total_hits += len(hits)
            print(f"[X]  {rel}  parse_ok={parse_ok} violations={len(hits)}")
            for kind, val in hits:
                # 실 PII/URL 평문 그대로 덤프하지 않도록 일부만 노출
                shown = (val[:24] + "...") if len(val) > 24 else val
                print(f"       - {kind}: {shown}")

    print("-" * 78)
    print(f"scanned={len(json_files)} files  clean={clean_files}  violations={total_hits}")
    gate = "GO" if total_hits == 0 and clean_files == len(json_files) else "NO-GO"
    print(f"GATE: {gate}")
    return 0 if gate == "GO" else 1


if __name__ == "__main__":
    sys.exit(run())
