# -*- coding: utf-8 -*-
"""binggupack.pack.scope_envelope — source pointer 공개 차단 판정 + fail-closed publish guard 정본.

strangler: scripts/openbinggu_scope_envelope_dryrun.py 의 순수 판정부(publish_decision·
regression_guard·classify_source_pointer·classify_source_pointers·_host_is_internal·_ip_is_internal
+ 정규식/상수)를 byte-identical 로 이관한 정본이다. 판정 로직·정규식은 1바이트도 변하지 않았다
(IP integer/hex/octal/short-form/IPv6 SSRF 우회 차단 포함).

파일 I/O 오케스트레이션(BASE/reports/tmp 경로·make_pack·read_validate(m1.scan_residual_pii 의존)·
run_selftest·reader·CLI)은 __file__ 경로·synthetic fixture·m0/m1 sibling 에 의존하므로
scripts/openbinggu_scope_envelope_dryrun.py 에 잔류한다(검증도 그쪽 --selftest).

공개 API(pack_builder 등 소비):
  - classify_source_pointer(value) -> 'clean' | 'dirty' | 'unknown'   (raw 미반환 · 판정만)
  - classify_source_pointers(pointers) -> {labels, counts}
  - publish_decision(items, publish_approved, regression_state=None) -> {...}
  - PUBLISH_REGRESSION_STATE / regression_guard(state)
"""
import ipaddress
import re
from urllib.parse import urlsplit


# ---------- 트랙1 GitHub 공개 fail-closed guard (설계: TRACK1_FAILCLOSED_PUBLISH_GUARD_DESIGN) ----------
# 회귀방지 R1~R3 기본 상태(현 트랙 고정). 하나라도 깨지면 공개 파이프라인 FAIL.
PUBLISH_REGRESSION_STATE = {
    "marketplace_enabled": False,
    "enum_status": "HOLD",
    "team_billing_code_exists": False,
}


def regression_guard(state):
    """R1~R3: marketplace_enabled==False AND enum_status=='HOLD' AND team_billing_code_exists==False.
    하나라도 깨지면 False(FAIL)."""
    return (state.get("marketplace_enabled") is False
            and state.get("enum_status") == "HOLD"
            and state.get("team_billing_code_exists") is False)


def publish_decision(items, publish_approved, regression_state=None):
    """트랙1 GitHub 공개 fail-closed 결정.
    게이트1(마스킹): 모든 item.mask_result == 'clean' 이어야 통과. dirty/unknown(=clean 아님) 1건↑ = BLOCK.
    게이트2(수동승인): publish_approved is True 아니면 BLOCK.
    회귀방지(R1~R3): 깨지면 FAIL(파이프라인 차단).
    fail-closed: 의심·불명(unknown)·미검증·미승인은 전부 차단. raw 값은 미기재(reason_code만)."""
    state = regression_state if regression_state is not None else PUBLISH_REGRESSION_STATE
    reason = []
    reg_ok = regression_guard(state)
    if not reg_ok:
        reason.append("REGRESSION_FAIL")
    # 게이트1: clean 외 전부 BLOCK (fail-closed). unknown 도 dirty 도 통과 못 함.
    results = [str(it.get("mask_result", "unknown")).lower() for it in items]
    non_clean = [r for r in results if r != "clean"]
    gate1_ok = len(non_clean) == 0
    if not gate1_ok:
        if any(r == "dirty" for r in non_clean):
            reason.append("RESIDUAL_DIRTY")
        if any(r != "dirty" for r in non_clean):   # unknown 또는 알 수 없는 값 → fail-closed
            reason.append("MASK_UNKNOWN")
    # 게이트2: 수동승인
    gate2_ok = publish_approved is True
    if not gate2_ok:
        reason.append("NOT_APPROVED")
    publish_allowed = bool(reg_ok and gate1_ok and gate2_ok)
    verdict = "ALLOW" if publish_allowed else "BLOCK"
    if not reg_ok:
        verdict = "FAIL"   # 회귀는 단순 BLOCK 이 아니라 파이프라인 FAIL
    return {"publish_allowed": publish_allowed, "verdict": verdict,
            "reason_codes": sorted(set(reason)), "regression_pass": reg_ok}


# ---------- source pointer 공개 차단 판정 (판정 only · 치환/sanitizer 없음) ----------
# 비공개/내부 형태 = dirty, 판정불가 = unknown, 그 외 안전 형태 = clean.
# raw 경로값은 반환/출력하지 않음 (라벨·count 만).

_WIN_ABSPATH = re.compile(r"^[A-Za-z]:[\\/]")                       # C:\... or C:/...
_FILE_URI = re.compile(r"^file://", re.I)
_UNC = re.compile(r"^\\\\[^\\]+\\")                                 # \\server\share
_UNIX_PRIVATE = re.compile(r"^(/home/|/Users/|/root/|/var/|/etc/|/mnt/|/opt/|/private/)")
# 호스트명 형태(IP 아님) 내부 표식 — 도메인 suffix·localhost 등.
_INTERNAL_NAME = re.compile(
    r"(^|\.)(localhost)$|(\.local|\.internal|\.lan|\.intra|\.corp|\.home|\.localdomain)$", re.I)
# raw 문자열 어디에든 내부 IP 옥텟이 보이면(스킴/포트 무관) 보수적 dirty — fail-closed 보조.
_INTERNAL_OCTET = re.compile(
    r"(127\.0\.0\.1|0\.0\.0\.0|192\.168\.|10\.\d+\.|169\.254\.|"
    r"172\.(1[6-9]|2\d|3[01])\.)")
_UNDECIDED_TOKENS = {"mask_undecided_token", "unknown", "n/a", "na", "?", "tbd"}


def _ip_is_internal(ip):
    """ipaddress 객체가 비공개/루프백/링크로컬/예약/멀티캐스트/미지정이면 True(공개 아님)."""
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or
            (ip.version == 4 and ip.is_global is False))


def _host_is_internal(host):
    """host 문자열(IP 표기 정규화 포함) 이 내부/비공개면 True. SSRF 우회(integer/hex/octal/short) 차단.

    파싱 불가/판정 애매(호스트명) 면 False 반환(이름 표식은 _INTERNAL_NAME 이 별도 판정).
    """
    if not host:
        return False
    h = host.strip().strip("[]").lower()  # IPv6 대괄호 제거
    if not h:
        return False
    # 1) 표준/축약 IPv4·IPv6 직접 파싱
    try:
        return _ip_is_internal(ipaddress.ip_address(h))
    except ValueError:
        pass
    # 2) integer / hex / octal / dotted 부분표기(127.1, 0x7f.1, 010.0.0.1, 0177.0.0.1 …) → 32bit int.
    def _octet(p):
        pl = p.lower()
        if pl.startswith("0x"):
            return int(pl, 16)
        if pl.startswith("0o"):
            return int(pl, 8)
        if pl.startswith("0") and len(pl) > 1:   # 선행 0 = 8진(0177 등) — Python3 int(.,0) 거부 우회
            return int(pl, 8)
        return int(pl, 10)
    try:
        parts = h.split(".")
        if 1 <= len(parts) <= 4 and all(p != "" for p in parts):
            vals = [_octet(p) for p in parts]  # 0x.. /0o.. /0NN(8진) /10진 자동 인식
            # dotted short form 규칙: 마지막 파트가 나머지 비트를 차지 (예: 127.1 → 127.0.0.1)
            n = len(vals)
            if n == 1:
                num = vals[0]
            elif n == 2:
                num = (vals[0] << 24) | vals[1]
            elif n == 3:
                num = (vals[0] << 24) | (vals[1] << 16) | vals[2]
            else:
                num = (vals[0] << 24) | (vals[1] << 16) | (vals[2] << 8) | vals[3]
            if 0 <= num <= 0xFFFFFFFF:
                return _ip_is_internal(ipaddress.ip_address(num))
    except (ValueError, TypeError):
        # Malformed alternate IPv4 encodings are conservatively treated as non-matches.
        pass
    return False


def classify_source_pointer(value):
    """source pointer 1건 → 'clean' | 'dirty' | 'unknown'. raw 값 미반환(판정만, 치환 없음).
    dirty = Windows 절대경로 / file:// / UNC / 비공개 unix path / localhost / 내부 URL·IP.
            (IP 는 integer/hex/octal/short-form/IPv6 까지 정규화해 내부대역 차단 — SSRF 우회 봉쇄.)
    unknown = None·빈값·판정불가 토큰.
    clean = 그 외(synthetic 식별자·hash_reference·상대경로·외부 공개 URL)."""
    if value is None:
        return "unknown"
    s = str(value).strip()
    if not s or s.lower() in _UNDECIDED_TOKENS:
        return "unknown"
    if (_WIN_ABSPATH.match(s) or _FILE_URI.match(s) or _UNC.match(s) or _UNIX_PRIVATE.match(s)):
        return "dirty"
    # URL 형태면 host 만 추려 IP 정규화 판정(스킴 무관). 파싱 실패 시 원문 보조검사로 폴백.
    host = None
    try:
        sp = urlsplit(s if "://" in s else "//" + s, scheme="")
        host = sp.hostname  # 포트/인증정보 제거된 순수 호스트(IPv6 대괄호도 제거됨)
    except ValueError:
        host = None
    if host is not None:
        if _host_is_internal(host) or _INTERNAL_NAME.search(host):
            return "dirty"
    # 보조 fail-closed — URL 파싱이 host 를 못 뽑았어도 raw 에 내부 옥텟/이름 표식이 보이면 dirty.
    if _INTERNAL_OCTET.search(s) or _INTERNAL_NAME.search(s):
        return "dirty"
    return "clean"


def classify_source_pointers(pointers):
    """여러 source pointer → 라벨 list + {clean,dirty,unknown} count. raw 경로값 미포함."""
    labels = [classify_source_pointer(p) for p in pointers]
    return {"labels": labels,
            "counts": {k: labels.count(k) for k in ("clean", "dirty", "unknown")}}
