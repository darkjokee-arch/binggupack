# -*- coding: utf-8 -*-
"""save_gate 텍스트 파싱 helper 정본 (v1.11.0 save-gate S3-B 이관).

scripts/binggu_save_gate.py 의 순수 helper(parse_save_indices + SAVE_TRIGGER_RE)를 이 모듈로
이관했다. save_gate 는 이 정본을 import 한다(import 실패 시 동일 정의 폴백 — byte-identical).
순수 텍스트 파싱(write 0 · ledger 0 · actor/confirm/token 흐름 0 · resolver 무의존).

게이트 정책/actor/confirm/G4/write 로직과 무관 — '<트리거> n' 형태 인식 + 문장 정규화/hash만 담당.
"""
import hashlib
import re


def _norm(s):
    """문장 정규화 — 공백류(공백/탭/줄바꿈) 단일 공백 + 앞뒤 strip. 순수."""
    return re.sub(r"\s+", " ", str(s)).strip()


def sent_hash(s):
    """정규화 문장의 sha256 앞 16자(hex). 원문 평문 미노출 식별자. 순수·write 0."""
    return hashlib.sha256(_norm(s).encode("utf-8")).hexdigest()[:16]

# 사람-발화 저장 트리거: 영문 'SAVE' 외 한글 '저장'/'세이브' 동등. '<트리거> n' / '<트리거> 1,3' /
# '<트리거> 1-5'(범위 · 2026-07-13 owner GO) 형태. 발화 전체 또는 **한 줄 전체**가 정확형일 때만
# (부분문자열·인용문·부정문 무시 — fullmatch. 줄 단위 인정은 아래 parse_save_indices).
SAVE_TRIGGER_RE = re.compile(
    r"\s*(?:SAVE|저장|세이브)\s*\d+(\s*[-~]\s*\d+)?(\s*,\s*\d+(\s*[-~]\s*\d+)?)*\s*",
    re.IGNORECASE)  # 트리거↔숫자 공백 선택적(저장1·저장 1 모두)

_RANGE_CAP = 50  # 범위 확장 상한(오타 '1-99999' 폭주 방지 — 초과 시 그 도장 무효)


def _expand_indices(text):
    """'1,3' / '1-5' / '1,3-5' → 인덱스 리스트(중복 제거·순서 보존). 범위 폭주 → None."""
    out, seen = [], set()
    for part in re.findall(r"\d+(?:\s*[-~]\s*\d+)?", str(text)):
        nums = [int(x) for x in re.findall(r"\d+", part)]
        if len(nums) == 2:
            lo, hi = min(nums), max(nums)
            if hi - lo + 1 > _RANGE_CAP:
                return None
            span = range(lo, hi + 1)
        else:
            span = nums
        for i in span:
            if i not in seen:
                seen.add(i)
                out.append(i)
    return out or None


def parse_save_indices(prompt):
    """도장 인식 — 인덱스 리스트 또는 None.

    ① 발화 전체가 정확형('<트리거> n' 등) → 인정(기존 동작).
    ② ★줄 단위 정확형(2026-07-13 owner GO): 여러 지시를 한 메시지에 묶는 사용자 스타일 대응 —
       어떤 **한 줄 전체**가 정확형이면 그 줄(들)을 도장으로 인정. 문장 속 언급("도장은 세이브1
       왜 실패")은 줄 일부라 여전히 무시(오도장 차단 계약 유지).
    트리거 = SAVE/저장/세이브(대소문자 무시). 범위 '1-5' 지원(_RANGE_CAP 상한)."""
    p = str(prompt or "")
    if SAVE_TRIGGER_RE.fullmatch(p):
        return _expand_indices(p)
    out, seen = [], set()
    for line in p.splitlines():
        if line.strip() and SAVE_TRIGGER_RE.fullmatch(line):
            idx = _expand_indices(line)
            for i in idx or []:
                if i not in seen:
                    seen.add(i)
                    out.append(i)
    return out or None
