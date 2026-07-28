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

# 혼합 도장 줄(owner 실측 2026-07-24): 한 줄에 '저장1,2 히트2,4 미스1,3' 처럼 SAVE+히트/미스를
# 함께 침 — SAVE 세그먼트만 추출하되, 줄 전체가 도장 세그먼트(SAVE/히트/미스/승격)들로만 구성될
# 때만 인정(그 외 임의 텍스트 붙으면 오도장 차단). 히트/미스가 이미 혼합 줄 지원(gate_log 7/22)한
# 것과 대칭 — SAVE 앵커만 줄 fullmatch 요구하던 비대칭 해소.
_STAMP_SEG = (r"(?:SAVE|저장|세이브|HIT|히트|MISS|미스|PROMOTE|승격)\s*\d+(?:\s*[-~]\s*\d+)?"
              r"(?:\s*,\s*\d+(?:\s*[-~]\s*\d+)?)*(?:\s*(?:무관|이미앎|약함|낡음|맥락|최신|틀림))?")
_STAMP_LINE_RE = re.compile(r"\s*(?:%s\s*)+" % _STAMP_SEG, re.IGNORECASE)   # 줄 = 도장 세그 1+
_SAVE_SEG_RE = re.compile(
    r"(?:SAVE|저장|세이브)\s*(\d+(?:\s*[-~]\s*\d+)?(?:\s*,\s*\d+(?:\s*[-~]\s*\d+)?)*)",
    re.IGNORECASE)  # 혼합 줄에서 SAVE 숫자부만 캡처

_RANGE_CAP = 50  # 범위 확장 상한(오타 '1-99999' 폭주 방지 — 초과 시 그 도장 무효)

# fenced 코드블록 구분선(``` 또는 ~~~ 로 시작하는 줄) — 붙여넣은 로그/AI응답/문서의 '명령 아닌 영역' 표시.
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")


def _strip_embedded_regions(text):
    """줄 스캔 전 '명령 아닌 영역' 제거 — fenced 코드블록(``` 또는 ~~~ 구분선 사이 본문+구분선)과
    blockquote('>' 시작) 줄을 걸러낸다. owner 가 로그·AI응답·문서 예시를 붙여넣을 때 그 안에 박힌
    독립줄 '세이브 n'(및 히트/승격 도장)이 실제 승인으로 오인되는 것을 차단(임베디드 트리거 무효화).
    정상 다중명령 묶음('메모 정리\\n세이브 1\\n끝')은 펜스/인용이 아니라 그대로 보존. 순수·write 0."""
    out, in_fence = [], False
    for line in str(text).splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue  # 구분선 자체도 제거
        if in_fence:
            continue  # 펜스 본문 제거
        if line.lstrip().startswith(">"):
            continue  # blockquote 인용 제거
        out.append(line)
    return "\n".join(out)


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


# ── L-lane 토큰 (2단계 절단 · docs/BINGGUPACK_STAGE2_TRUNCATION_DESIGN.md) ────────────
# 기존 SAVE_TRIGGER_RE/_expand_indices 는 **손대지 않는다** — allow_long=False(기본)면
# 파싱 경로가 종전과 완전히 동일해 회귀 0. L 은 전용 정규식·전용 확장 함수로만 처리한다.
# 범위 'L1-L3' 는 미지원(범위 오지정 위험 · 설계 S2-2). 혼합 도장 줄('히트 1 SAVE L1')도
# 미지원 — 순수 'SAVE L1' / 'SAVE 1,L2' 형태만 인정한다(계약 최소 확장).
_L_TOKEN = r"(?:\d+(?:\s*[-~]\s*\d+)?|[Ll]\d+)"
SAVE_TRIGGER_L_RE = re.compile(
    r"\s*(?:SAVE|저장|세이브)\s*" + _L_TOKEN + r"(\s*,\s*" + _L_TOKEN + r")*\s*",
    re.IGNORECASE)


def _expand_indices_long(text):
    """숫자 → int, L 토큰 → 'L<n>' 문자열(대문자 정규화). 정수축과 문자열축이라 충돌 불가.
    범위 폭주(_RANGE_CAP 초과) → None 은 기존과 동일."""
    out, seen = [], set()
    for part in re.findall(r"\d+(?:\s*[-~]\s*\d+)?|[Ll]\d+", str(text)):
        if part[:1] in ("L", "l"):
            key = "L" + part[1:]
            if key not in seen:
                seen.add(key)
                out.append(key)
            continue
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


def parse_save_indices(prompt, allow_long=False):
    """도장 인식 — 인덱스 리스트 또는 None.

    allow_long: L 토큰('SAVE L1') 인정 여부. **기본 False = 종전 동작 그대로**(회귀 0).
      True 는 L-lane 을 아는 호출자만 명시로 켠다. 반환 리스트에 int 와 'L1' 문자열이 섞일 수
      있으므로, 켜는 쪽은 두 축을 구분해 소비할 책임이 있다.

    ① 발화 전체가 정확형('<트리거> n' 등) → 인정(기존 동작).
    ② ★줄 단위 정확형(2026-07-13 owner GO): 여러 지시를 한 메시지에 묶는 사용자 스타일 대응 —
       어떤 **한 줄 전체**가 정확형이면 그 줄(들)을 도장으로 인정. 문장 속 언급("도장은 세이브1
       왜 실패")은 줄 일부라 여전히 무시(오도장 차단 계약 유지).
    ③ ★fenced 코드블록/blockquote 안 독립줄은 도장 아님(2026-07-18 P0 수정): owner 가 로그·AI응답·
       문서 예시를 붙여넣을 때 그 안 '세이브 n' 줄이 실제 승인으로 오인되던 회귀 차단
       (_strip_embedded_regions 로 줄 스캔 전 제거). 평문 라인모드(②)는 그대로 보존.
    트리거 = SAVE/저장/세이브(대소문자 무시). 범위 '1-5' 지원(_RANGE_CAP 상한)."""
    p = str(prompt or "")
    trigger = SAVE_TRIGGER_L_RE if allow_long else SAVE_TRIGGER_RE
    expand = _expand_indices_long if allow_long else _expand_indices
    if trigger.fullmatch(p):
        return expand(p)
    out, seen = [], set()
    for line in _strip_embedded_regions(p).splitlines():
        ls = line.strip()
        if not ls:
            continue
        if trigger.fullmatch(ls):
            segs = [ls]                       # 순수 SAVE 줄(기존 동작)
        elif _STAMP_LINE_RE.fullmatch(ls):
            segs = _SAVE_SEG_RE.findall(ls)   # ④ 혼합 도장 줄 → SAVE 세그먼트만(히트/미스 동반 무시)
        else:
            segs = []                         # 문장 속 언급·임의 텍스트 → 오도장 차단(계약 유지)
        for seg in segs:
            for i in expand(seg) or []:
                if i not in seen:
                    seen.add(i)
                    out.append(i)
    return out or None
