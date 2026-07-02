# -*- coding: utf-8 -*-
"""binggupack.pack.batch_m1 — Watcher M1 batch PII redaction 정본(canonical).

strangler: scripts/watcher_batch_m1.py 의 PII 복합 판단(batch_redact) + 독립 잔존
scanner(scan_residual_pii) + 관련 정규식/상수를 byte-identical 로 이관한 정본이다.
판정 로직·정규식은 1바이트도 변하지 않았다(한국주소/이름 shape 수정분 포함).

변경된 것은 오직 mvp1 바인딩 방식뿐:
  scripts 원본  `import watcher_capture_mvp1 as mvp1`
  이관본        `from binggupack.pack import capture_mvp1 as mvp1`
둘은 런타임 동일 객체를 가리킨다(watcher_capture_mvp1 은 binggupack.pack.capture_mvp1
re-export shim → mvp1.redact_text 는 동일 함수). 따라서 redact 동작 byte-identical.

파일 I/O 오케스트레이션(process_batch/어댑터/CLI/selftest)과 _sha8 은 scripts/
위치·tmp 경로에 의존하므로 scripts/watcher_batch_m1.py 에 잔류한다(검증도 그쪽 --selftest).

공개 API:
  - batch_redact(text, field_name="") -> (redacted:str, hits:int, review_flag:bool)
  - scan_residual_pii(text) -> list[str]
"""
import re

from binggupack.pack import capture_mvp1 as mvp1


# ---------- PII redaction (복합 판단: shape + field/context + whitelist/denylist) ----------
# 기존 secret 공용함수(mvp1.redact_text/_has_secret) 무수정. PII 판단은 이 모듈에서 독립 수행.
# 한글-숫자 인접 미매칭(\b 결함) 회피: word boundary 대신 숫자 lookaround 경계 사용.
_NL = r"(?<![0-9])"   # 좌 숫자 경계
_NR = r"(?![0-9])"    # 우 숫자 경계

# 명확한 PII shape (무하이픈/공백/점/+82 변형 포함). 형태가 명확하므로 도메인 문맥과 무관하게 마스킹.
# 이메일 TLD: ASCII(2+) | punycode(xn--..) | 한글 TLD(.한국 등). 신용카드/AKIA/vendor 토큰은 secret 계열.
_EMAIL_TLD = r"(?:[A-Za-z]{2,}|xn--[A-Za-z0-9-]+|[가-힣]{2,})"

# ---- 한국 주소/이름 PII (Fix B) ----
# 시/도 화이트리스트(고정 17 + 전체명). bare("서울")·suffixed("서울시") 둘 다. 문법어 오탐 억제 위해 화이트리스트로 앵커.
_SIDO = (r"(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주"
         r"|충청북|충청남|전라북|전라남|경상북|경상남|강원특별자치)"
         r"(?:특별자치시|특별자치도|특별시|광역시|자치시|자치도|도|시)?")
# 행정/도로 토큰: …구/군/시/읍/면/동/로/길, 뒤가 한글이 아니어야 함(우리집·처리·활동 부분매칭 차단). '리/가' 제외(우리/거리·조사 오탐).
_ADDR_TOKEN = r"(?:\s*[가-힣A-Za-z0-9]{1,12}(?:구|군|시|읍|면|동|로|길)(?![가-힣]))"
# 계층 주소: 시/도 + 토큰1+ + 지번/도로번호(필수, 오탐 억제 핵심) [+ 동/호]. 번호 없으면 주소로 보지 않음.
_KR_ADDRESS = re.compile(
    _SIDO + _ADDR_TOKEN + r"+"
    + r"\s*\d{1,5}(?:-\d{1,4})?(?:번지)?"
    + r"(?:\s*\d{1,4}동)?(?:\s*\d{1,4}호)?")
# 아파트/건물 동·호 쌍 — 시/도 없이도 거주지 식별(숫자+동+숫자+호). "101동 202호".
_KR_DONGHO = re.compile(r"(?<![0-9])\d{1,4}동\s*\d{1,4}호(?![0-9])")
# 이름: 강한 라벨(이름/성명/성함) + 구분자(콜론/조사+공백/공백) + 2~4자 한글. group(1)만 마스킹(보수적, 라벨 오탐 최소).
_KR_NAME = re.compile(r"(?:이름|성명|성함)(?:\s*[:：=]\s*|\s*(?:은|는|이|가)\s+|\s+)([가-힣]{2,4})(?![가-힣])")

PII_SHAPES = [
    ("rrn",            re.compile(_NL + r"\d{6}[-\s.]?\d{7}" + _NR)),                                  # 주민(13)
    ("phone_mobile",   re.compile(_NL + r"(?:\+?82[-\s.]?)?0?1[016789][-\s.]?\d{3,4}[-\s.]?\d{4}" + _NR)),
    ("phone_landline", re.compile(_NL + r"(?:\+?82[-\s.]?)?0\d{1,2}[-\s.]?\d{3,4}[-\s.]?\d{4}" + _NR)),
    # 신용카드: 4-4-4-4 (구분자 -/공백/점 또는 무구분 16자리). Luhn 불요 형태매칭. 사업자번호(3-2-5)와 자릿수 상이.
    ("credit_card",    re.compile(_NL + r"\d{4}[-\s.]?\d{4}[-\s.]?\d{4}[-\s.]?\d{4}" + _NR)),
    ("email",          re.compile(r"[A-Za-z0-9._%+\-가-힣]+@[A-Za-z0-9.\-가-힣]+\." + _EMAIL_TLD)),
    # AWS access key: AKIA + 7자 이상(짧은 변형까지 공격적으로).
    ("aws_akia",       re.compile(r"\bAKIA[0-9A-Z]{7,}")),
    # vendor 토큰: sk-live-/sk-/ghp_/gho_/ghs_ 등 prefix + 충분 길이.
    ("vendor_token",   re.compile(r"\b(?:sk-live-[A-Za-z0-9]{8,}|sk-[A-Za-z0-9]{16,}|gh[oprsu]_[A-Za-z0-9]{20,})")),
    # bearer 토큰: 'bearer ' + 긴 토큰.
    ("bearer_token",   re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    # 긴 base64-ish secret (>=32, 영숫자+/=_-). 카드/주민 등 숫자열은 먼저 매칭되어 제외됨.
    ("b64_secret",     re.compile(r"\b[A-Za-z0-9+/=_-]{32,}\b")),
    # 한국 주소(계층/동호). full-span 마스킹 — 기존 겹침정리 로직이 서브스팬 흡수.
    ("kr_address",     _KR_ADDRESS),
    ("kr_dongho",      _KR_DONGHO),
]

# 도메인 식별자 형태 — 문맥이 명확히 일치할 때만 보존(아니면 마스킹+review_flag).
BIZNO_SHAPE = re.compile(_NL + r"\d{3}-\d{2}-\d{5}" + _NR)   # 사업자등록번호 10자리

# 보존(whitelist) 문맥 — 필드명 또는 주변 문맥에 존재해야 보존.
WHITELIST_CONTEXT = ("사업자등록번호", "사업자번호", "등록번호", "공고번호", "입찰번호", "계약번호",
                     "공고", "입찰공고", "계약", "biz", "bizno", "notice_no", "bid_no", "contract_no")
# denylist — 존재하면 whitelist 무효화(무조건 마스킹). 인증/토큰/키 계열.
DENYLIST_CONTEXT = ("인증", "토큰", "비밀", "비번", "패스워드", "credential", "cert", "token",
                    "api", "apikey", "api_key", "key", "session", "cookie", "fingerprint",
                    "secret", "password", "passwd", "private")

# substring 오매칭 차단 — ASCII 토큰은 경계(\b 또는 _ 구분자), 한글 토큰은 인접 한글 미존재로 경계 판정.
# 'key' in turkey/monkey, '공고' in 공고문 등 부분일치 제거. 정규식 캐시(모듈 1회 컴파일).
def _compile_kw(kw):
    if re.fullmatch(r"[A-Za-z0-9_]+", kw):
        # ASCII 식별자: 영숫자/언더스코어 경계. notice_no, api_key 등 _ 포함 토큰도 단일 토큰 취급.
        return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(kw) + r"(?![A-Za-z0-9_])", re.IGNORECASE)
    # 한글(또는 혼합) 키워드: 좌우 한글 인접 시 부분일치로 간주, 경계 요구.
    return re.compile(r"(?<![가-힣])" + re.escape(kw) + r"(?![가-힣])")

_WHITELIST_RX = [_compile_kw(k) for k in WHITELIST_CONTEXT]
_DENYLIST_RX = [_compile_kw(k) for k in DENYLIST_CONTEXT]


def _ctx_window(text, start, end, w=20):
    return text[max(0, start - w):min(len(text), end + w)]


def _domain_preserve(ctx, field):
    """문맥/필드명이 도메인 식별자로 명확하고 denylist 아니면 True(보존). 토큰 경계 매칭."""
    joined = (field or "") + " " + ctx
    if any(rx.search(joined) for rx in _DENYLIST_RX):
        return False
    return any(rx.search(joined) for rx in _WHITELIST_RX)


def batch_redact(text, field_name=""):
    """secret(mvp1, 무수정) + PII 복합 판단. 정규식 단독 X — shape+문맥+whitelist/denylist.
    returns (redacted, hits, review_flag). 애매한 도메인 숫자열은 raw 보존 금지 → 마스킹+review."""
    red, hits = mvp1.redact_text(text)   # secret 동작 그대로(회귀 0)
    field = field_name or ""
    review = False

    # 1단계: 보존할 도메인 식별자 span 확정 (사업자번호 형태 + 문맥 일치 + not denylist)
    preserve = []
    for m in BIZNO_SHAPE.finditer(red):
        ctx = _ctx_window(red, m.start(), m.end())
        if _domain_preserve(ctx, field):
            preserve.append((m.start(), m.end()))
        else:
            review = True   # 사업자번호 형태인데 문맥 불충분 = 애매 → 마스킹 대상 + review

    def _in_preserve(s, e):
        return any(not (e <= ps or s >= pe) for ps, pe in preserve)

    # 2단계: PII shape 후보 수집 (보존 span 제외)
    cands = []
    for kind, pat in PII_SHAPES:
        for m in pat.finditer(red):
            if not _in_preserve(m.start(), m.end()):
                cands.append((m.start(), m.end(), kind))
    # 애매 사업자번호(보존 실패)도 마스킹 후보로 편입
    for m in BIZNO_SHAPE.finditer(red):
        if not _in_preserve(m.start(), m.end()):
            cands.append((m.start(), m.end(), "bizno_ambiguous"))
    # 한국 이름(라벨 문맥): 라벨 제외 이름 group(1)만 마스킹
    for m in _KR_NAME.finditer(red):
        if not _in_preserve(m.start(1), m.end(1)):
            cands.append((m.start(1), m.end(1), "kr_name"))

    # 3단계: 겹침 정리(좌→우, 긴 것 우선) 후 우→좌 치환
    cands.sort(key=lambda c: (c[0], -(c[1] - c[0])))
    merged = []
    for s, e, k in cands:
        if merged and s < merged[-1][1]:
            continue
        merged.append((s, e, k))
    out = red
    for s, e, k in sorted(merged, key=lambda c: -c[0]):
        out = out[:s] + ("[REDACTED:%d]" % (e - s)) + out[e:]
        hits += 1
    return out, hits, review


# ---------- 독립 잔존 scanner (redactor 로직 import 안 함 — 별도 shape, 더 공격적) ----------
# redactor가 놓친 PII/secret 형태를 다른 로직으로 잡는다(검증자≠피검증자).
# boundary 없이 광범위 탐지. 도메인 식별자(사업자번호 10자리)는 패턴에 안 걸려 오탐 0.
_SCAN_SHAPES = [
    ("scan_rrn",      re.compile(r"\d{6}[-\s.]\d{7}")),
    ("scan_rrn_nohp", re.compile(r"(?<![0-9])\d{13}(?![0-9])")),
    ("scan_mobile",   re.compile(r"(?<![0-9])0?1[016789][-\s.]?\d{3,4}[-\s.]?\d{4}(?![0-9])")),
    ("scan_landline", re.compile(r"(?<![0-9])0\d{1,2}[-\s.]\d{3,4}[-\s.]\d{4}(?![0-9])")),
    # 신용카드 4-4-4-4 (구분자 또는 무구분 16자리). 사업자번호 10자리(3-2-5)는 미매칭.
    ("scan_credit_card", re.compile(r"(?<![0-9])\d{4}[-\s.]?\d{4}[-\s.]?\d{4}[-\s.]?\d{4}(?![0-9])")),
    # 이메일: ASCII | punycode | 한글 TLD.
    ("scan_email",    re.compile(r"[A-Za-z0-9._%+\-가-힣]+@[A-Za-z0-9.\-가-힣]+\.(?:[A-Za-z]{2,}|xn--[A-Za-z0-9-]+|[가-힣]{2,})")),
    # AKIA: 7자 이상(짧은 변형 포함, redactor와 동일 임계).
    ("scan_aws",      re.compile(r"\bAKIA[0-9A-Z]{7,}")),
    # vendor 토큰: sk-live/sk-/ghp_ 등.
    ("scan_vendor",   re.compile(r"\b(?:sk-live-[A-Za-z0-9]{8,}|sk-[A-Za-z0-9]{16,}|gh[oprsu]_[A-Za-z0-9]{20,})")),
    # bearer 토큰.
    ("scan_bearer",   re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    # 값이 영숫자/특수 토큰일 때만 secret (한글 서술 "token: 서명방식…"=단어≠값 오탐 제외). 박제 feedback scan_kv 오탐.
    ("scan_kv",       re.compile(r"(?i)(?:api[_-]?key|token|secret|password|passwd)\s*[:=]\s*['\"]?[A-Za-z0-9_\-+/=.]{4,}")),
    # 긴 base64-ish secret.
    ("scan_b64",      re.compile(r"\b[A-Za-z0-9+/=_-]{32,}\b")),
    # 한국 주소/이름 잔존 검증(redactor와 동일 shape 공유 — 신규 PII 타입은 형태 자체가 판정근거).
    ("scan_kr_address", _KR_ADDRESS),
    ("scan_kr_dongho",  _KR_DONGHO),
    ("scan_kr_name",    _KR_NAME),
]


def scan_residual_pii(text):
    """산출물 텍스트에서 PII/secret 형태 잔존 탐지. returns kind list (raw 값 미반환)."""
    if not isinstance(text, str):
        return []
    found = []
    for kind, pat in _SCAN_SHAPES:
        if pat.search(text):
            found.append(kind)
    return found


__all__ = [
    "batch_redact",
    "scan_residual_pii",
    "PII_SHAPES",
    "BIZNO_SHAPE",
    "WHITELIST_CONTEXT",
    "DENYLIST_CONTEXT",
    "_SCAN_SHAPES",
]
