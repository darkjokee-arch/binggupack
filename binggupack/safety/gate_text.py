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

# 사람-발화 저장 트리거: 영문 'SAVE' 외 한글 '저장'/'세이브' 동등. 발화 전체가 정확히
# '<트리거> n' / '<트리거> 1,3' 형태일 때만(부분문자열·인용문·부정문 무시 — fullmatch).
SAVE_TRIGGER_RE = re.compile(
    r"\s*(?:SAVE|저장|세이브)\s*\d+(\s*,\s*\d+)*\s*", re.IGNORECASE)  # 트리거↔숫자 공백 선택적(저장1·저장 1 모두)


def parse_save_indices(prompt):
    """발화 전체가 정확히 '<트리거> n' / '<트리거> 1,3' 형태일 때만 인덱스 반환.
    트리거 = SAVE/저장/세이브(대소문자 무시). 부분문자열·인용문·부정문 무시. 아니면 None."""
    if not SAVE_TRIGGER_RE.fullmatch(str(prompt or "")):
        return None
    return [int(x) for x in re.findall(r"\d+", prompt)]
